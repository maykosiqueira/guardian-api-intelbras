"""Bypass commands must carry the whole set of zones, not one zone each.

Regression for 2026-08-10 17:51 UTC: the "piscina" script bypasses zones
36/37/40/43 with a single multi-entity `switch.turn_on`, Home Assistant ran the
four entities concurrently, and each one sent its own command. On ISECNet V1
the bypass command is a FULL-STATE bitmask of all 48 zones, so every command
un-bypassed the zones the previous one had just set:

    V1 bypass cmd: zones=[36] bitmask=[00,00,00,00,10,00]
    V1 bypass cmd: zones=[42] bitmask=[00,00,00,00,00,04]
    V1 bypass cmd: zones=[35] bitmask=[00,00,00,00,08,00]
    V1 bypass cmd: zones=[39] bitmask=[00,00,00,00,80,00]   <- only this survived

Zones 36 and 37 (indices 35/36) were open, were not actually bypassed, and the
arm was refused with `OpenZonesError: Zona 37`.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

try:
    from custom_components.intelbras_guardian.switch import (
        GuardianZoneBypassSwitch,
        ZONE_BYPASS_KEY,
    )
except ImportError as e:  # pragma: no cover - depends on the installed HA
    pytest.skip(f"needs Home Assistant importable: {e}", allow_module_level=True)

from custom_components.intelbras_guardian.const import DOMAIN

DEVICE_ID = 550793
# Zone numbers 36, 37, 40 and 43 -> 0-based indices, the "piscina" set.
PISCINA = [35, 36, 39, 42]


def _switches(indices, coordinator=None):
    """Build bypass switches for one device sharing a mocked hass."""
    coordinator = coordinator or _coordinator()
    hass = MagicMock()
    hass.data = {DOMAIN: {}}

    built = []
    for index in indices:
        switch = GuardianZoneBypassSwitch.__new__(GuardianZoneBypassSwitch)
        switch.coordinator = coordinator
        switch._device_id = DEVICE_ID
        switch._zone_index = index
        switch._device_mac = "546CAC17874A"
        switch._is_bypassed = False
        switch.hass = hass
        switch.async_write_ha_state = MagicMock()
        # Join the device's set, the way async_added_to_hass does.
        hass.data[DOMAIN].setdefault(ZONE_BYPASS_KEY, {}).setdefault(
            DEVICE_ID, {"lock": asyncio.Lock(), "switches": {}}
        )["switches"][index] = switch
        built.append(switch)
    return built, coordinator


def _coordinator():
    coordinator = MagicMock()
    coordinator.client.bypass_zones = AsyncMock(return_value={"success": True})
    coordinator.async_refresh_device = AsyncMock()
    coordinator.get_zone.return_value = {"is_bypassed": False, "is_open": False}
    return coordinator


def _sent(coordinator):
    """Return the (zones, bypass) pairs sent to the panel, in order."""
    return [
        (list(call.args[1]), call.kwargs.get("bypass", True))
        for call in coordinator.client.bypass_zones.await_args_list
    ]


def test_concurrent_turn_on_converges_on_the_full_set():
    """Four switches flipped at once must end with all four zones bypassed."""
    switches, coordinator = _switches(PISCINA)

    async def _flip_all_at_once():
        await asyncio.wait_for(
            asyncio.gather(*(s.async_turn_on() for s in switches)), timeout=5
        )

    asyncio.run(_flip_all_at_once())

    sent = _sent(coordinator)
    assert all(bypass for _, bypass in sent), sent
    # Whatever order the four calls resolved in, the panel's last word has to
    # be the whole set - that is what the old code got wrong.
    assert sent[-1][0] == PISCINA, sent
    assert all(s.is_on for s in switches)


def test_turn_on_sends_the_zones_already_bypassed_too():
    """A single switch must not drop the zones its siblings hold."""
    switches, coordinator = _switches(PISCINA)
    for switch in switches[:-1]:
        switch._is_bypassed = True

    asyncio.run(switches[-1].async_turn_on())

    assert _sent(coordinator) == [(PISCINA, True)]


def test_turn_off_clears_its_zone_and_keeps_the_others():
    """Un-bypass is explicit (V2) and followed by the surviving set (V1)."""
    switches, coordinator = _switches(PISCINA)
    for switch in switches:
        switch._is_bypassed = True

    asyncio.run(switches[0].async_turn_off())

    assert _sent(coordinator) == [([35], False), ([36, 39, 42], True)]
    assert not switches[0].is_on


def test_turn_off_last_zone_only_clears():
    """With nothing left to hold, a bare un-bypass is the whole command."""
    switches, coordinator = _switches([39])
    switches[0]._is_bypassed = True

    asyncio.run(switches[0].async_turn_off())

    assert _sent(coordinator) == [([39], False)]


def test_failed_command_keeps_the_switch_off():
    """A refused bypass must raise and must not claim the zone is anulada."""
    from homeassistant.exceptions import HomeAssistantError

    switches, coordinator = _switches([39])
    coordinator.client.bypass_zones = AsyncMock(
        return_value={"success": False, "error": "Bypass negado: central esta armada"}
    )

    with pytest.raises(HomeAssistantError, match="central esta armada"):
        asyncio.run(switches[0].async_turn_on())

    assert not switches[0].is_on
