"""Token refresh behaviour of the middleware's AuthService.

Root cause of the 2026-08-05 outage: the refresh was only ever attempted
inside TOKEN_REFRESH_BUFFER (300s before the access token expired). The
Intelbras access token outlives its refresh token by a wide margin, so that
first attempt already got "invalid_grant / Persisted access token data not
found" — and every failure was logged at DEBUG while the container ran at
INFO, so nothing surfaced until the session was gone.
"""
import asyncio
import logging
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from app.core.exceptions import TokenExpiredError, TokenRefreshError
from app.services.auth_service import AuthService
from app.services.state_manager import state_manager


def _token(*, expires_in_seconds, refresh_token="refresh-abc"):
    return {
        "access_token": "access-abc",
        "refresh_token": refresh_token,
        "expires_at": (
            datetime.utcnow() + timedelta(seconds=expires_in_seconds)
        ).isoformat(),
        "expires_in": expires_in_seconds,
        "username": "user",
    }


@pytest.fixture
def service():
    svc = AuthService()
    svc.refresh_buffer = 300
    return svc


@pytest.fixture(autouse=True)
def clean_state():
    state_manager._tokens.clear()
    yield
    state_manager._tokens.clear()


def test_refresh_failure_is_logged_not_swallowed(service, caplog):
    """A failing refresh on a still-valid token must leave a trace."""
    asyncio.run(state_manager.set_token("sess", _token(expires_in_seconds=60)))

    with patch.object(
        service, "_refresh_token", AsyncMock(side_effect=TokenRefreshError("boom"))
    ), caplog.at_level(logging.WARNING):
        token = asyncio.run(service.get_valid_token("sess"))

    # Token is still usable, so the call succeeds...
    assert token == "access-abc"
    # ...but the operator can see the renewal is broken.
    assert any(
        "refresh failed" in record.message.lower() for record in caplog.records
    )


def test_expired_token_with_failed_refresh_raises(service):
    """Once the access token is gone a failed refresh is fatal for the session."""
    asyncio.run(state_manager.set_token("sess", _token(expires_in_seconds=-10)))

    with patch.object(
        service, "_refresh_token", AsyncMock(side_effect=TokenRefreshError("boom"))
    ):
        with pytest.raises(TokenExpiredError):
            asyncio.run(service.get_valid_token("sess"))


def test_proactive_refresh_runs_long_before_expiry(service):
    """
    The whole point of the fix: sessions are refreshed on a cadence, while the
    access token is still nowhere near expiring, so the rotating refresh token
    never gets a chance to go stale.
    """
    asyncio.run(state_manager.set_token("sess", _token(expires_in_seconds=36 * 3600)))

    refresh = AsyncMock(return_value=_token(expires_in_seconds=36 * 3600))
    with patch.object(service, "_refresh_token", refresh):
        results = asyncio.run(service.refresh_all_sessions())

    assert results == {"sess": True}
    refresh.assert_awaited_once()


def test_proactive_refresh_reports_each_failing_session(service, caplog):
    """One dead session must not stop the others from being refreshed."""
    asyncio.run(state_manager.set_token("good", _token(expires_in_seconds=36 * 3600)))
    asyncio.run(state_manager.set_token("bad", _token(expires_in_seconds=36 * 3600)))

    async def _refresh(session_id, token_data):
        if session_id == "bad":
            raise TokenRefreshError("invalid_grant")
        return token_data

    with patch.object(service, "_refresh_token", _refresh), caplog.at_level(logging.ERROR):
        results = asyncio.run(service.refresh_all_sessions())

    assert results == {"good": True, "bad": False}
    assert any("Proactive refresh failed" in r.message for r in caplog.records)


def test_refresh_without_refresh_token_fails_fast(service):
    """No refresh token stored means no amount of retrying will help."""
    token = _token(expires_in_seconds=3600, refresh_token=None)

    with pytest.raises(TokenRefreshError):
        asyncio.run(service._refresh_token("sess", token))
