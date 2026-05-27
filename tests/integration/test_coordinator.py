"""Integration tests for GuardianCoordinator (HA imported, hass + client mocked).

Validates the data-processing logic that runs against a real panel — without
a panel, without a running HA — by driving _async_update_data with canned
api_client responses and a lightweight MagicMock hass. Tests are synchronous
and drive the coroutine with asyncio.run, so no event-loop fixture is needed.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

from custom_components.intelbras_guardian.coordinator import GuardianCoordinator

DEVICE_ID = 550793


def _device():
    return {
        "id": DEVICE_ID,
        "model": "AMT_2018_E_SMART",
        "mac": "AA:BB:CC:DD:EE:FF",
        "description": "Casa",
        "has_saved_password": True,
        "partitions": [
            {"id": 1775173, "name": "Particao A"},
            {"id": 1775174, "name": "Particao B"},
        ],
        "zones": [],
    }


def _status(*, is_triggered, zones, partition_state="armed_away"):
    return {
        "arm_mode": "armed_away" if is_triggered else "disarmed",
        "is_armed": is_triggered,
        "is_triggered": is_triggered,
        "connection_unavailable": False,
        "partitions_enabled": True,
        "partitions": [
            {"index": 0, "state": partition_state},
            {"index": 1, "state": partition_state},
        ],
        "zones": zones,
        "last_updated": "2026-05-27T00:00:00Z",
    }


def _coordinator(status):
    client = MagicMock()
    client.session_id = "sess"
    client.get_devices = AsyncMock(return_value=[_device()])
    client.get_events = AsyncMock(return_value=[])
    client.get_alarm_status_auto = AsyncMock(return_value=status)
    client.listen_sse_events = AsyncMock()
    coord = GuardianCoordinator(MagicMock(), client, MagicMock())
    coord._sse_task = MagicMock()  # skip starting the real SSE listener
    return coord


def test_captures_triggering_zone_from_is_in_alarm():
    """Bug 2: the zone in alarm is captured synchronously with is_triggered."""
    zones = [
        {"index": 0, "name": "Zona 01", "is_open": False, "is_bypassed": False, "is_in_alarm": False},
        {"index": 3, "name": "CAM 4 PISCINA", "is_open": True, "is_bypassed": False, "is_in_alarm": True},
    ]
    data = asyncio.run(_coordinator(_status(is_triggered=True, zones=zones))._async_update_data())

    assert data["devices"][DEVICE_ID]["is_triggered"] is True
    trigger = data["_last_trigger"][DEVICE_ID]
    assert trigger["zone_name"] == "CAM 4 PISCINA"
    assert trigger["zone_index"] == 3


def test_falls_back_to_open_zone_when_no_is_in_alarm():
    """If no zone reports is_in_alarm, use an open (non-bypassed) zone."""
    zones = [
        {"index": 5, "name": "Zona 06", "is_open": True, "is_bypassed": False, "is_in_alarm": False},
        {"index": 6, "name": "Zona 07", "is_open": True, "is_bypassed": True, "is_in_alarm": False},
    ]
    data = asyncio.run(_coordinator(_status(is_triggered=True, zones=zones))._async_update_data())

    trigger = data["_last_trigger"][DEVICE_ID]
    assert trigger["zone_index"] == 5  # bypassed zone 6 is ignored


def test_no_trigger_when_not_triggered():
    """A routine poll without a trigger must not populate _last_trigger."""
    zones = [{"index": 0, "name": "Zona 01", "is_open": False, "is_bypassed": False, "is_in_alarm": False}]
    data = asyncio.run(
        _coordinator(_status(is_triggered=False, zones=zones, partition_state="disarmed"))._async_update_data()
    )

    assert data["devices"][DEVICE_ID]["is_triggered"] is False
    assert DEVICE_ID not in data["_last_trigger"]
