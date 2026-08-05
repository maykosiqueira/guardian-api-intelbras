"""How the coordinator reacts when the middleware session dies.

Regression cover for the 2026-08-05 outage: the middleware's OAuth token could
not be refreshed any more, the session was dropped, and the coordinator kept
returning empty-but-successful data. The alarm panel therefore went on showing
its last known state ("disarmed") for seven hours while nothing was being read,
and the only symptom was one log line per second.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.intelbras_guardian.coordinator import GuardianCoordinator


def _coordinator(*, session_id, devices=None):
    client = MagicMock()
    client.session_id = session_id
    client.get_devices = AsyncMock(return_value=devices if devices is not None else [])
    client.get_events = AsyncMock(return_value=[])
    client.listen_sse_events = AsyncMock()

    hass = MagicMock()
    hass.services.async_call = AsyncMock()

    coord = GuardianCoordinator(hass, client, MagicMock())
    coord._sse_task = None  # nothing to stop; these paths never start it
    return coord


def test_lost_session_raises_config_entry_auth_failed():
    """No session -> HA is told to re-authenticate, not fed empty data."""
    coord = _coordinator(session_id=None)

    with pytest.raises(ConfigEntryAuthFailed):
        asyncio.run(coord._async_update_data())


def test_lost_session_notifies_the_user_once():
    """The persistent notification is created once, not once per poll."""
    coord = _coordinator(session_id=None)

    for _ in range(5):
        with pytest.raises(ConfigEntryAuthFailed):
            asyncio.run(coord._async_update_data())

    creates = [
        call for call in coord.hass.services.async_call.call_args_list
        if call.args[:2] == ("persistent_notification", "create")
    ]
    assert len(creates) == 1
    assert creates[0].args[2]["notification_id"] == "intelbras_guardian_session_lost"


def test_lost_session_backs_off_the_poll_interval():
    """Polling every second while unauthenticated only floods the log."""
    coord = _coordinator(session_id=None)
    assert coord.update_interval.total_seconds() == 1

    with pytest.raises(ConfigEntryAuthFailed):
        asyncio.run(coord._async_update_data())

    assert coord.update_interval.total_seconds() == 60


def test_empty_device_list_fails_the_update_instead_of_blanking_state():
    """No devices means "unknown", not "everything is fine and empty"."""
    coord = _coordinator(session_id="sess", devices=[])

    with pytest.raises(UpdateFailed):
        asyncio.run(coord._async_update_data())


def test_session_restored_clears_notification_and_interval():
    """Coming back from a lost session undoes the back-off and the warning."""
    coord = _coordinator(session_id=None)
    with pytest.raises(ConfigEntryAuthFailed):
        asyncio.run(coord._async_update_data())

    # Session is back but the cloud call still returns nothing: the restore
    # path must run before that failure, so the next successful poll is normal.
    coord.client.session_id = "new-session"
    with pytest.raises(UpdateFailed):
        asyncio.run(coord._async_update_data())

    assert coord.update_interval.total_seconds() == 1
    dismissals = [
        call for call in coord.hass.services.async_call.call_args_list
        if call.args[:2] == ("persistent_notification", "dismiss")
    ]
    assert len(dismissals) == 1
