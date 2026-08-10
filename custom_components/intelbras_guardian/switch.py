"""Switch platform for Intelbras Guardian eletrificadores."""
import asyncio
import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, ELETRIFICADOR_MODELS
from .coordinator import GuardianCoordinator

_LOGGER = logging.getLogger(__name__)

# hass.data[DOMAIN] key holding, per alarm device, the zone bypass switches and
# the lock that serializes the commands they send. See GuardianZoneBypassSwitch.
ZONE_BYPASS_KEY = "zone_bypass"


def _bypass_registry(hass: HomeAssistant, device_id: int) -> dict:
    """Return the shared bypass state for one alarm device."""
    registry = hass.data.setdefault(DOMAIN, {}).setdefault(ZONE_BYPASS_KEY, {})
    return registry.setdefault(
        device_id, {"lock": asyncio.Lock(), "switches": {}}
    )


def is_eletrificador(device: dict) -> bool:
    """Check if device is an electric fence (eletrificador)."""
    model = device.get("model", "")
    if not model:
        return False
    model_upper = model.upper()
    return any(elc in model_upper for elc in ELETRIFICADOR_MODELS)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up switch entities for eletrificadores and zone bypass."""
    coordinator: GuardianCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = []
    created_bypass: set[str] = set()

    if coordinator.data:
        for device_id, device in coordinator.data.get("devices", {}).items():
            if is_eletrificador(device):
                # Shock control switch
                entities.append(
                    GuardianEletrificadorShockSwitch(
                        coordinator,
                        device_id,
                        device.get("mac", ""),
                    )
                )
                # Alarm control switch
                entities.append(
                    GuardianEletrificadorAlarmSwitch(
                        coordinator,
                        device_id,
                        device.get("mac", ""),
                    )
                )

        # One "Anular" (bypass) switch per zone, so a zone can be bypassed
        # from a script/dashboard even while it is CLOSED (pre-bypass, so it
        # will not trip if it opens after arming). The panel does not report
        # the bypass bitmap back, so these are assumed_state switches.
        for zone in coordinator.data.get("zones", []):
            zone_index = zone.get("index", zone.get("id", 0))
            uid = f"{zone.get('device_mac', '')}_zone_{zone_index}_bypass"
            if uid in created_bypass:
                continue
            created_bypass.add(uid)
            entities.append(
                GuardianZoneBypassSwitch(
                    coordinator,
                    zone["device_id"],
                    zone_index,
                    zone.get("device_mac", ""),
                )
            )

    async_add_entities(entities)

    # Zones may only show up after the first successful poll - add later ones too
    def _check_new_zones() -> None:
        if not coordinator.data:
            return
        new_entities = []
        for zone in coordinator.data.get("zones", []):
            zone_index = zone.get("index", zone.get("id", 0))
            uid = f"{zone.get('device_mac', '')}_zone_{zone_index}_bypass"
            if uid not in created_bypass:
                created_bypass.add(uid)
                new_entities.append(
                    GuardianZoneBypassSwitch(
                        coordinator,
                        zone["device_id"],
                        zone_index,
                        zone.get("device_mac", ""),
                    )
                )
        if new_entities:
            _LOGGER.info("Adding %d new zone bypass switches", len(new_entities))
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.async_add_listener(_check_new_zones))


class GuardianZoneBypassSwitch(CoordinatorEntity, SwitchEntity, RestoreEntity):
    """Bypass (anular) switch for a single alarm zone.

    ON  = zone is bypassed (anulada): it will NOT trip the siren, even if opened.
    OFF = zone is active.

    The ISECNet status frame parsed by the middleware never exposes the bypass
    bitmap (`is_bypassed` is always False), so the state is optimistic: it
    reflects the last command sent from Home Assistant and is restored across
    restarts. The panel clears bypasses on its own arm/disarm cycle, so scripts
    should re-assert the bypass right before arming and clear it on disarm.

    Every command carries the whole set of zones this device wants bypassed,
    not just the zone that changed - see `_async_set_bypass` for why.
    """

    _attr_has_entity_name = True
    _attr_device_class = SwitchDeviceClass.SWITCH
    _attr_entity_category = EntityCategory.CONFIG
    _attr_assumed_state = True

    def __init__(
        self,
        coordinator: GuardianCoordinator,
        device_id: int,
        zone_index: int,
        device_mac: str,
    ):
        """Initialize the bypass switch."""
        super().__init__(coordinator)
        self._device_id = device_id
        self._zone_index = zone_index
        self._device_mac = device_mac
        self._is_bypassed = False

        self._attr_unique_id = f"{device_mac}_zone_{zone_index}_bypass"

    async def async_added_to_hass(self) -> None:
        """Restore the last known bypass state and join the device's set."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state == "on":
            self._is_bypassed = True
        registry = _bypass_registry(self.hass, self._device_id)
        registry["switches"][self._zone_index] = self

    async def async_will_remove_from_hass(self) -> None:
        """Leave the device's bypass set so a stale entity is not counted."""
        switches = _bypass_registry(self.hass, self._device_id)["switches"]
        if switches.get(self._zone_index) is self:
            del switches[self._zone_index]
        await super().async_will_remove_from_hass()

    @property
    def name(self) -> str:
        """Return the name of the switch."""
        zone = self.coordinator.get_zone(self._device_id, self._zone_index)
        base = f"Zona {self._zone_index + 1:02d}"
        if zone:
            base = zone.get("friendly_name") or zone.get("name") or base
        return f"{base} Anular"

    @property
    def icon(self) -> str:
        """Return the icon."""
        return "mdi:shield-off" if self.is_on else "mdi:shield-check"

    @property
    def available(self) -> bool:
        """Return True if entity is available (connection to panel is working)."""
        if not self.coordinator.last_update_success:
            return False
        device = self.coordinator.get_device(self._device_id)
        if device and device.get("connection_unavailable", False):
            return False
        return True

    @property
    def device_info(self):
        """Return device info."""
        device = self.coordinator.get_device(self._device_id)
        if device:
            return {
                "identifiers": {(DOMAIN, self._device_mac)},
                "name": device.get("description", f"Intelbras Alarm {self._device_id}"),
                "manufacturer": "Intelbras",
                "model": device.get("model", "Guardian Alarm"),
            }
        return None

    @property
    def is_on(self) -> bool:
        """Return true if the zone is bypassed."""
        zone = self.coordinator.get_zone(self._device_id, self._zone_index)
        # Trust the panel only when it actively says "bypassed" - it never
        # reports True today, so a False there must not clear our own state.
        if zone and zone.get("is_bypassed"):
            return True
        return self._is_bypassed

    @property
    def extra_state_attributes(self):
        """Return extra state attributes."""
        zone = self.coordinator.get_zone(self._device_id, self._zone_index) or {}
        return {
            "device_id": self._device_id,
            "zone_index": self._zone_index,
            "zone_number": self._zone_index + 1,
            "is_open": zone.get("is_open", False),
            "panel_reports_bypassed": zone.get("is_bypassed", False),
        }

    def _desired_zones(self, bypass: bool) -> set[int]:
        """Return every zone of this device that should end up bypassed."""
        desired = {
            index
            for index, switch in _bypass_registry(
                self.hass, self._device_id
            )["switches"].items()
            if switch is not self and switch.is_on
        }
        if bypass:
            desired.add(self._zone_index)
        else:
            desired.discard(self._zone_index)
        return desired

    async def _async_send(self, zone_indices: list[int], bypass: bool) -> None:
        """Send one bypass command, raising if the panel does not take it."""
        result = await self.coordinator.client.bypass_zones(
            self._device_id, zone_indices, bypass=bypass
        )
        if result and result.get("success"):
            return
        error = (result or {}).get("error", "sem resposta")
        _LOGGER.error(
            "Failed to %s zone %d on device %s (sent zones %s): %s",
            "bypass" if bypass else "unbypass",
            self._zone_index + 1,
            self._device_id,
            [index + 1 for index in zone_indices],
            error,
        )
        raise HomeAssistantError(
            f"Falha ao {'anular' if bypass else 'reativar'} a zona "
            f"{self._zone_index + 1:02d}: {error}"
        )

    async def _async_set_bypass(self, bypass: bool) -> None:
        """Send the bypass command to the panel.

        The command has to carry every zone that should stay bypassed, because
        on ISECNet V1 panels (AMT 2018 E SMART and friends) bypass is a
        FULL-STATE bitmask of all 48 zones: the zones left out of the request
        are actively un-bypassed. One command per zone therefore made each
        command wipe the one before it, and since Home Assistant runs a
        multi-entity `switch.turn_on` concurrently, only whichever command
        reached the panel last survived. A script bypassing four zones before
        arming silently ended up with a single one bypassed - and the panel
        refused to arm if one of the others was open.

        The commands are serialized per device and the set is recomputed
        inside the lock, so concurrent calls converge on the full set instead
        of racing. Turning a zone off also sends an explicit un-bypass for
        that zone first: on V2 panels the command is per-zone and omitting a
        zone means "leave it alone", so the full-set command alone would never
        clear it. On V1 that first command is a harmless all-zeros bitmask
        which the second one immediately corrects.
        """
        registry = _bypass_registry(self.hass, self._device_id)
        async with registry["lock"]:
            desired = self._desired_zones(bypass)
            if not bypass:
                await self._async_send([self._zone_index], False)
            if desired:
                await self._async_send(sorted(desired), True)
            self._is_bypassed = bypass

        self.async_write_ha_state()
        await self.coordinator.async_refresh_device(self._device_id)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Bypass (anular) the zone."""
        _LOGGER.info(f"Bypassing zone {self._zone_index + 1} on device {self._device_id}")
        await self._async_set_bypass(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Un-bypass (reativar) the zone."""
        _LOGGER.info(f"Un-bypassing zone {self._zone_index + 1} on device {self._device_id}")
        await self._async_set_bypass(False)


class GuardianEletrificadorShockSwitch(CoordinatorEntity, SwitchEntity):
    """Representation of an Intelbras electric fence SHOCK switch."""

    _attr_has_entity_name = True
    _attr_device_class = SwitchDeviceClass.SWITCH

    def __init__(
        self,
        coordinator: GuardianCoordinator,
        device_id: int,
        device_mac: str,
    ):
        """Initialize the switch."""
        super().__init__(coordinator)
        self._device_id = device_id
        self._device_mac = device_mac

        # Entity attributes
        self._attr_unique_id = f"{device_mac}_shock"
        self._attr_name = "Choque"
        self._attr_icon = "mdi:flash"

    @property
    def available(self) -> bool:
        """Return True if entity is available (connection to panel is working)."""
        if not self.coordinator.last_update_success:
            return False
        device = self.coordinator.get_device(self._device_id)
        if device and device.get("connection_unavailable", False):
            return False
        return True

    @property
    def device_info(self):
        """Return device info."""
        device = self.coordinator.get_device(self._device_id)
        if device:
            return {
                "identifiers": {(DOMAIN, self._device_mac)},
                "name": device.get("description", f"Eletrificador {self._device_id}"),
                "manufacturer": "Intelbras",
                "model": device.get("model", "Eletrificador"),
            }
        return None

    @property
    def is_on(self) -> bool:
        """Return true if shock is enabled."""
        device = self.coordinator.get_device(self._device_id)
        if not device:
            return False
        return device.get("shock_enabled", False)

    @property
    def extra_state_attributes(self):
        """Return extra state attributes."""
        device = self.coordinator.get_device(self._device_id)
        if device:
            return {
                "device_id": self._device_id,
                "shock_triggered": device.get("shock_triggered", False),
            }
        return {}

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on shock."""
        _LOGGER.info(f"Turning on shock for eletrificador {self._device_id}")
        success = await self.coordinator.client.eletrificador_shock_on(self._device_id)
        if success:
            await self.coordinator.async_refresh_device(self._device_id)
        else:
            _LOGGER.error("Failed to turn on shock")

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off shock."""
        _LOGGER.info(f"Turning off shock for eletrificador {self._device_id}")
        success = await self.coordinator.client.eletrificador_shock_off(self._device_id)
        if success:
            await self.coordinator.async_refresh_device(self._device_id)
        else:
            _LOGGER.error("Failed to turn off shock")


class GuardianEletrificadorAlarmSwitch(CoordinatorEntity, SwitchEntity):
    """Representation of an Intelbras electric fence ALARM switch."""

    _attr_has_entity_name = True
    _attr_device_class = SwitchDeviceClass.SWITCH

    def __init__(
        self,
        coordinator: GuardianCoordinator,
        device_id: int,
        device_mac: str,
    ):
        """Initialize the switch."""
        super().__init__(coordinator)
        self._device_id = device_id
        self._device_mac = device_mac

        # Entity attributes
        self._attr_unique_id = f"{device_mac}_alarm"
        self._attr_name = "Alarme"
        self._attr_icon = "mdi:shield"

    @property
    def available(self) -> bool:
        """Return True if entity is available (connection to panel is working)."""
        if not self.coordinator.last_update_success:
            return False
        device = self.coordinator.get_device(self._device_id)
        if device and device.get("connection_unavailable", False):
            return False
        return True

    @property
    def device_info(self):
        """Return device info."""
        device = self.coordinator.get_device(self._device_id)
        if device:
            return {
                "identifiers": {(DOMAIN, self._device_mac)},
                "name": device.get("description", f"Eletrificador {self._device_id}"),
                "manufacturer": "Intelbras",
                "model": device.get("model", "Eletrificador"),
            }
        return None

    @property
    def is_on(self) -> bool:
        """Return true if alarm is armed."""
        device = self.coordinator.get_device(self._device_id)
        if not device:
            return False
        return device.get("alarm_enabled", False)

    @property
    def extra_state_attributes(self):
        """Return extra state attributes."""
        device = self.coordinator.get_device(self._device_id)
        if device:
            return {
                "device_id": self._device_id,
                "alarm_triggered": device.get("alarm_triggered", False),
            }
        return {}

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Arm the alarm."""
        _LOGGER.info(f"Arming alarm for eletrificador {self._device_id}")
        success = await self.coordinator.client.eletrificador_alarm_activate(self._device_id)
        if success:
            await self.coordinator.async_refresh_device(self._device_id)
        else:
            _LOGGER.error("Failed to arm alarm")

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disarm the alarm."""
        _LOGGER.info(f"Disarming alarm for eletrificador {self._device_id}")
        success = await self.coordinator.client.eletrificador_alarm_deactivate(self._device_id)
        if success:
            await self.coordinator.async_refresh_device(self._device_id)
        else:
            _LOGGER.error("Failed to disarm alarm")
