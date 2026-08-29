"""An unreachable middleware must not read as an invalid session.

`check_session` answered a plain bool and turned every exception into False,
and `async_setup_entry` took that False as "the session is dead" and discarded
the stored credential. On a host reboot Home Assistant Core is already serving
while the add-on is still starting - measured on a live install: Core's port
answered ~40 s before the middleware's did - so the first check of every boot
ran against a closed port and the user was asked to log in again, with a
perfectly good session sitting in /data/sessions.json.
"""
import asyncio
from unittest.mock import MagicMock

from custom_components.intelbras_guardian.api_client import GuardianApiClient


class _Response:
    def __init__(self, status):
        self.status = status


class _Session:
    """Minimal aiohttp stand-in: answers with a status, or raises."""

    def __init__(self, *, status=None, error=None):
        self._status = status
        self._error = error

    async def get(self, *args, **kwargs):
        if self._error is not None:
            raise self._error
        return _Response(self._status)


def _client(session):
    client = GuardianApiClient(host="127.0.0.1", port=8000, session=session)
    client.set_session_id("sess-1")
    return client


def _check(session):
    return asyncio.run(_client(session).check_session())


def test_accepted_session_is_true():
    assert _check(_Session(status=200)) is True


def test_rejected_session_is_false():
    assert _check(_Session(status=401)) is False


def test_unreachable_middleware_is_unknown():
    assert _check(_Session(error=OSError("Connect call failed"))) is None


def test_timeout_is_unknown():
    assert _check(_Session(error=asyncio.TimeoutError())) is None


def test_server_error_is_unknown():
    # A 502 from a middleware still warming up says nothing about the session.
    assert _check(_Session(status=502)) is None


def test_no_session_id_is_false():
    client = GuardianApiClient(host="127.0.0.1", port=8000, session=MagicMock())
    client.set_session_id(None)
    assert asyncio.run(client.check_session()) is False
