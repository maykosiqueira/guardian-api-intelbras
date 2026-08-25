"""A failed or empty status poll must not be rendered as "every zone closed".

2026-08-25: zones 27/36/37/39 (every open wireless zone) flipped to closed
and every zone signal to unknown for 1-15 s, several times a day. Each
episode matched a `Request error` (30 s timeout) in api_client while the
middleware was stalled, or a 200 whose zone list was empty. The coordinator
then fell through to the cloud zone list, which has no `is_open` or
`signal_strength`. These tests drive the same sequence through
_async_update_data and check the previous cycle's state survives.
"""
import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

from custom_components.intelbras_guardian.coordinator import GuardianCoordinator

DEVICE_ID = 550793


def _device():
    # Fresh dict every call: the cloud refresh replaces the cached device
    # objects, so nothing may rely on state lingering inside them.
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
        # What the cloud knows about the zones: names only, no live state.
        "zones": [{"id": 21135370 + i, "name": f"Zona {i + 1:02d}"} for i in range(48)],
    }


def _zone(index, is_open, signal=None):
    return {
        "index": index,
        "name": f"Zona {index + 1:02d}",
        "is_open": is_open,
        "is_bypassed": False,
        "is_wireless": signal is not None,
        "battery_low": False,
        "signal_strength": signal,
        "tamper": False,
        "is_in_alarm": False,
    }


def _status(zones, *, arm_mode="armed_home", partitions=("armed_home", "disarmed")):
    return {
        "arm_mode": arm_mode,
        "is_armed": arm_mode != "disarmed",
        "is_triggered": False,
        "connection_unavailable": False,
        "partitions_enabled": True,
        "partitions": [{"index": i, "state": s} for i, s in enumerate(partitions)],
        "zones": zones,
        "last_updated": "2026-08-25T19:32:05Z",
    }


GOOD = _status([_zone(26, True, 6), _zone(38, True, 7), _zone(39, False, 5), _zone(0, False)])


def _coordinator(statuses):
    client = MagicMock()
    client.session_id = "sess"
    client.get_devices = AsyncMock(side_effect=lambda: [_device()])
    client.get_events = AsyncMock(return_value=[])
    client.get_alarm_status_auto = AsyncMock(side_effect=list(statuses))
    client.listen_sse_events = AsyncMock()
    coord = GuardianCoordinator(MagicMock(), client, MagicMock())
    coord._sse_task = MagicMock()  # skip starting the real SSE listener
    return coord


def _run_cycles(coord, n, *, refresh_cloud_each_cycle=False):
    """Drive n update cycles the way DataUpdateCoordinator would."""
    results = []

    async def go():
        for _ in range(n):
            if refresh_cloud_each_cycle:
                coord._cached_devices = None  # force get_devices() -> fresh dicts
            data = await coord._async_update_data()
            coord.data = data
            results.append(data)

    asyncio.run(go())
    return results


def _zones_by_index(data):
    return {z["index"]: z for z in data["zones"] if z["device_id"] == DEVICE_ID}


def _partition_statuses(data):
    return [p.get("status") for p in data["partitions"] if p["device_id"] == DEVICE_ID]


def test_timeout_keeps_previous_zones_arm_state_and_partitions(caplog):
    """status None (api_client timed out) -> previous cycle's state survives."""
    coord = _coordinator([GOOD, None])
    with caplog.at_level(logging.WARNING):
        first, second = _run_cycles(coord, 2, refresh_cloud_each_cycle=True)

    good = _zones_by_index(first)
    assert good[26]["is_open"] is True and good[26]["signal_strength"] == 6

    kept = _zones_by_index(second)
    assert kept[26]["is_open"] is True, "open zone must not flip to closed"
    assert kept[38]["is_open"] is True
    assert kept[26]["signal_strength"] == 6, "signal must not go unknown"
    assert kept[39]["is_open"] is False
    assert second["devices"][DEVICE_ID]["arm_mode"] == "armed_home"
    assert second["devices"][DEVICE_ID]["is_armed"] is True
    assert _partition_statuses(second) == ["armed_home", "disarmed"], "partition B must not go unknown"
    assert "keeping the last known zone/partition state" in caplog.text


def test_empty_zone_list_keeps_previous_zones():
    """A 200 with zones=[] (middleware parsed a truncated frame) is not 'all closed'."""
    coord = _coordinator([GOOD, _status([], arm_mode="disarmed", partitions=())])
    _, second = _run_cycles(coord, 2)

    kept = _zones_by_index(second)
    assert kept[26]["is_open"] is True
    assert kept[38]["is_open"] is True
    assert kept[26]["signal_strength"] == 6


def test_warns_once_per_episode_and_recovers():
    caplog_msgs = []

    class _Handler(logging.Handler):
        def emit(self, record):
            caplog_msgs.append(record.getMessage())

    logger = logging.getLogger("custom_components.intelbras_guardian.coordinator")
    handler = _Handler()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        after = _status([_zone(26, False, 6), _zone(38, True, 7), _zone(39, False, 5), _zone(0, False)])
        coord = _coordinator([GOOD, None, None, None, after])
        results = _run_cycles(coord, 5)
    finally:
        logger.removeHandler(handler)

    warnings = [m for m in caplog_msgs if "keeping the last known" in m]
    assert len(warnings) == 1, "one warning per stall episode, not one per second"
    assert any("back after 3 stale poll" in m for m in caplog_msgs)

    # Real data flows again: zone 27 really closed now.
    final = _zones_by_index(results[-1])
    assert final[26]["is_open"] is False
    assert final[38]["is_open"] is True
    assert coord._stale_status_polls == {}


def test_first_cycle_without_status_still_uses_cloud_zone_names():
    """No previous state to keep on the very first poll: cloud fallback as before."""
    coord = _coordinator([None])
    (only,) = _run_cycles(coord, 1)
    zones = _zones_by_index(only)
    assert len(zones) == 48
    assert zones[26]["name"] == "Zona 27"
