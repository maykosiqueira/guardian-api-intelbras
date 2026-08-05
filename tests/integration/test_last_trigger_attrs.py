"""The triggering zone must travel WITH the panel's state change.

Regression for the 2026-07-29 13:37 trigger: the automation fired on
`alarm_control_panel -> triggered` and read `sensor.*_ultimo_disparo`, which
the sensor platform only wrote ~24ms later, so the alert went out as
"Ultimo evento: Sem disparos". The panel now carries the zone in its own
attributes, built from the same coordinator data that produced the
`triggered` state.

These tests drive the coordinator (mocked client/hass) and feed its output
through the same pure builder the entity uses.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

from custom_components.intelbras_guardian.state_logic import build_last_trigger_attrs

from test_coordinator import DEVICE_ID, _coordinator, _status


def _attrs(coord):
    """Build the panel attributes exactly as the entity does."""
    data = coord.data or {}
    device = (data.get("devices") or {}).get(DEVICE_ID) or {}
    return build_last_trigger_attrs(
        (data.get("_last_trigger") or {}).get(DEVICE_ID),
        is_triggered=bool(device.get("is_triggered")),
        started_at=coord.trigger_started_at(DEVICE_ID),
    )


def _poll(coord):
    coord.data = asyncio.run(coord._async_update_data())
    return coord.data


def _set_status(coord, status):
    coord.client.get_alarm_status_auto = AsyncMock(return_value=status)


ZONE_IN_ALARM = [
    {"index": 0, "name": "Zona 01", "is_open": False, "is_bypassed": False, "is_in_alarm": False},
    {"index": 42, "name": "CAM 4 PISCINA", "is_open": True, "is_bypassed": False, "is_in_alarm": True},
]
ZONE_CLEAR = [
    {"index": 42, "name": "CAM 4 PISCINA", "is_open": False, "is_bypassed": False, "is_in_alarm": False},
]


def test_zone_is_available_in_the_same_update_that_sets_triggered():
    coord = _coordinator(_status(is_triggered=True, zones=ZONE_IN_ALARM))
    data = _poll(coord)

    assert data["devices"][DEVICE_ID]["is_triggered"] is True
    attrs = _attrs(coord)
    assert attrs["last_trigger_zone"] == "CAM 4 PISCINA"
    assert attrs["last_trigger_zones"] == ["CAM 4 PISCINA"]
    assert attrs["last_trigger_is_current"] is True


def test_no_trigger_yet_returns_empty_attrs():
    coord = _coordinator(
        _status(is_triggered=False, zones=ZONE_CLEAR, partition_state="disarmed")
    )
    _poll(coord)

    attrs = _attrs(coord)
    assert attrs["last_trigger_zone"] is None
    assert attrs["last_trigger_is_current"] is False


def test_cleared_alarm_keeps_the_record_but_flags_it_not_current():
    coord = _coordinator(_status(is_triggered=True, zones=ZONE_IN_ALARM))
    _poll(coord)

    _set_status(coord, _status(is_triggered=False, zones=ZONE_CLEAR, partition_state="disarmed"))
    _poll(coord)

    attrs = _attrs(coord)
    assert attrs["last_trigger_zone"] == "CAM 4 PISCINA"  # still the LAST trigger
    assert attrs["last_trigger_is_current"] is False  # but not the current one


def test_sse_trigger_without_zone_does_not_claim_the_previous_zone():
    """An SSE trigger carrying no zone must not present the previous alarm's
    zone as the current one — the poll path fills it in a moment later."""
    coord = _coordinator(_status(is_triggered=True, zones=ZONE_IN_ALARM))
    _poll(coord)
    _set_status(coord, _status(is_triggered=False, zones=ZONE_CLEAR, partition_state="disarmed"))
    _poll(coord)

    coord.hass = MagicMock()
    coord.async_set_updated_data = MagicMock()
    coord._apply_alarm_trigger(
        {"device_id": DEVICE_ID, "event_name": "Disparo de Setor", "is_alarm": True}
    )

    attrs = _attrs(coord)
    assert attrs["last_trigger_zone"] == "CAM 4 PISCINA"
    assert attrs["last_trigger_is_current"] is False


def test_sse_trigger_with_zone_is_current():
    coord = _coordinator(
        _status(is_triggered=False, zones=ZONE_CLEAR, partition_state="disarmed")
    )
    _poll(coord)

    coord.hass = MagicMock()
    coord.async_set_updated_data = MagicMock()
    coord._apply_alarm_trigger({
        "device_id": DEVICE_ID,
        "event_name": "Disparo de Setor",
        "is_alarm": True,
        "zone": {"index": 42, "name": "CAM 4 PISCINA"},
    })

    attrs = _attrs(coord)
    assert attrs["last_trigger_zone"] == "CAM 4 PISCINA"
    assert attrs["last_trigger_is_current"] is True
