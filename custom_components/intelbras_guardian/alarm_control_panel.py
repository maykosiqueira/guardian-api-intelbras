"""Alarm control panel for Intelbras Guardian."""
import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity,
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_AWAY_PARTITIONS,
    CONF_HOME_PARTITIONS,
    CONF_PARTITION_ARM_MODES,
    CONF_UNIFIED_ALARM,
    DOMAIN,
    STATE_MAPPING,
)
from .coordinator import GuardianCoordinator
from .state_logic import (
    build_last_trigger_attrs,
    classify_arm_mode,
    compute_unified_state,
)

_LOGGER = logging.getLogger(__name__)


def _notify(hass: HomeAssistant, message: str, title: str, notification_id: str) -> None:
    """Create a persistent notification using the modern HA service call API."""
    hass.async_create_task(
        hass.services.async_call(
            "persistent_notification",
            "create",
            {
                "message": message,
                "title": title,
                "notification_id": notification_id,
            },
        )
    )

# Per-device command locks to prevent race conditions from rapid clicks
_device_command_locks: Dict[int, asyncio.Lock] = {}


def _get_device_command_lock(device_id: int) -> asyncio.Lock:
    """Get or create a command lock for a specific device."""
    if device_id not in _device_command_locks:
        _device_command_locks[device_id] = asyncio.Lock()
    return _device_command_locks[device_id]


def _pre_trigger_arm_mode_attr(coordinator: GuardianCoordinator, device_id: int) -> Optional[str]:
    """Translate the coordinator's pre-trigger memory into a friendly value.

    Used by template switches that want to know which armed mode the panel
    was in immediately before the trigger fired (the central zeroes the
    partition byte during an active alarm, which would otherwise lose this
    information). Returns "away", "home", or None.
    """
    pre_mode = coordinator._pre_trigger_arm_mode.get(device_id)
    if pre_mode == "armed_away":
        return "away"
    if pre_mode in ("armed_stay", "armed_home"):
        return "home"
    return None


_ESTADO_TEXTO = {
    "disarmed": "Desarmado",
    "armed_away": "Armado",
    "armed_home": "Armado parcial",
    "armed_night": "Armado noturno",
    "armed_vacation": "Armado viagem",
    "arming": "Armando",
    "pending": "Entrando",
    "triggered": "DISPARADO",
}


def _estado_texto(state) -> str:
    """Name the panel's state in the language the rest of this integration speaks.

    The card in front of the user reads "Armado ausente", because that is how
    Home Assistant names `armed_away` for every alarm brand there is - correct
    and unhelpful on a panel whose owner only ever arms it whole. Core state
    names have no per-entity override, so carry the wording as an attribute the
    tile card can show through `state_content`.
    """
    if state is None:
        return "Desconhecido"
    return _ESTADO_TEXTO.get(str(state), str(state))


def _last_trigger_attrs(
    coordinator: GuardianCoordinator, device_id: int
) -> Dict[str, Any]:
    """Expose the triggering zone as attributes of the panel itself.

    Automations fire on the panel's `triggered` state and used to read
    `sensor.<device>_ultimo_disparo` to name the zone. Both entities are fed
    by the SAME coordinator update, but the alarm_control_panel platform is
    set up first, so its listener writes the new state (and runs the
    automation) ~24ms BEFORE the sensor writes the zone — the automation read
    the *previous* value ("Sem disparos" right after a restart). Carrying the
    zone in the panel's own attributes makes it atomic with the state change:
    `trigger.to_state.attributes.last_trigger_zone` is always the zone of the
    trigger that just fired.

    `last_trigger_is_current` tells whether the record belongs to the trigger
    in progress (vs. a leftover from a previous alarm), so templates can fall
    back gracefully when the central reports a trigger without an
    identifiable zone.
    """
    data = coordinator.data or {}
    device = (data.get("devices") or {}).get(device_id) or {}
    return build_last_trigger_attrs(
        (data.get("_last_trigger") or {}).get(device_id),
        is_triggered=bool(device.get("is_triggered")),
        started_at=coordinator.trigger_started_at(device_id),
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up alarm control panel entities."""
    coordinator: GuardianCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = []
    if coordinator.data:
        # Get unified alarm config from options
        unified_config = entry.options.get(CONF_UNIFIED_ALARM, {})
        _LOGGER.debug(f"Entry options: {entry.options}")
        _LOGGER.debug(f"Unified config: {unified_config}")

        # Track which devices have unified alarm enabled
        unified_devices = set()

        # Create unified alarm entities for configured devices
        for device_id_str, device_config in unified_config.items():
            _LOGGER.debug(f"Processing unified config for device {device_id_str}: {device_config}")
            if device_config.get("enabled", True):
                device_id = int(device_id_str)
                device = coordinator.get_device(device_id)
                _LOGGER.debug(f"Device {device_id} found: {device is not None}")
                if device:
                    unified_devices.add(device_id)
                    # Get partitions for this device
                    device_partitions = [
                        p for p in coordinator.data.get("partitions", [])
                        if p.get("device_id") == device_id
                    ]
                    _LOGGER.debug(f"Device {device_id} has {len(device_partitions)} partitions")
                    if len(device_partitions) > 1:
                        entities.append(
                            GuardianUnifiedAlarmControlPanel(
                                coordinator,
                                device_id,
                                device_config.get("mac", device.get("mac", "")),
                                device_config.get(CONF_HOME_PARTITIONS, [0]),
                                device_config.get(CONF_AWAY_PARTITIONS, list(range(len(device_partitions)))),
                                device_partitions,
                                device_config.get(CONF_PARTITION_ARM_MODES, {}),
                            )
                        )
                        _LOGGER.info(
                            f"Created unified alarm for device {device_id} "
                            f"(home={device_config.get(CONF_HOME_PARTITIONS)}, "
                            f"away={device_config.get(CONF_AWAY_PARTITIONS)}, "
                            f"arm_modes={device_config.get(CONF_PARTITION_ARM_MODES)})"
                        )
                    else:
                        _LOGGER.warning(f"Device {device_id} has only {len(device_partitions)} partitions, skipping unified alarm")

        # Create individual partition entities
        for partition in coordinator.data.get("partitions", []):
            entities.append(
                GuardianAlarmControlPanel(
                    coordinator,
                    partition["device_id"],
                    partition["id"],
                    partition.get("device_mac", ""),
                )
            )

    # Register entities for bypass action handler lookup
    hass.data[DOMAIN].setdefault("alarm_entities", {})
    for entity in entities:
        if isinstance(entity, GuardianUnifiedAlarmControlPanel):
            key = (entity._device_id, "unified")
        else:
            key = (entity._device_id, "individual", entity._partition_id)
        hass.data[DOMAIN]["alarm_entities"][key] = entity

    async_add_entities(entities)


class GuardianAlarmControlPanel(CoordinatorEntity, AlarmControlPanelEntity):
    """Representation of an Intelbras Guardian alarm partition."""

    _attr_has_entity_name = True
    _attr_code_arm_required = False
    _attr_code_format = None
    # Individual partitions only support ARM_AWAY (simple arm/disarm)
    # Use the unified alarm entity for HOME/AWAY modes
    _attr_supported_features = AlarmControlPanelEntityFeature.ARM_AWAY

    def __init__(
        self,
        coordinator: GuardianCoordinator,
        device_id: int,
        partition_id: int,
        device_mac: str,
    ):
        """Initialize the alarm control panel."""
        super().__init__(coordinator)
        self._device_id = device_id
        self._partition_id = partition_id
        self._device_mac = device_mac

        # Optimistic state management
        self._optimistic_state: Optional[AlarmControlPanelState] = None

        # Entity attributes
        self._attr_unique_id = f"{device_mac}_partition_{partition_id}"

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
    def name(self) -> str:
        """Return the name of the alarm."""
        partition = self.coordinator.get_partition(self._device_id, self._partition_id)
        if partition:
            return partition.get("name", f"Partition {self._partition_id}")
        return f"Partition {self._partition_id}"

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
    def state(self) -> str:
        """Return the state of the alarm."""
        # Use optimistic state if set (for immediate UI feedback)
        if self._optimistic_state is not None:
            return self._optimistic_state

        partition = self.coordinator.get_partition(self._device_id, self._partition_id)
        device = self.coordinator.get_device(self._device_id)

        if not partition:
            return None

        # Check if triggered first (from device real-time status)
        if device and device.get("is_triggered"):
            return AlarmControlPanelState.TRIGGERED

        # Check partition-level alarm state
        if partition.get("is_in_alarm", False):
            return AlarmControlPanelState.TRIGGERED

        # Get status from partition or device arm_mode
        status = partition.get("status")
        if not status and device:
            # Use device-level arm_mode from real-time status
            status = device.get("arm_mode")

        if not status:
            return AlarmControlPanelState.DISARMED

        # Map status to Home Assistant state
        ha_state = STATE_MAPPING.get(status)
        if ha_state:
            # Convert string to AlarmControlPanelState enum
            state_map = {
                "armed_away": AlarmControlPanelState.ARMED_AWAY,
                "armed_home": AlarmControlPanelState.ARMED_HOME,
                "disarmed": AlarmControlPanelState.DISARMED,
                "triggered": AlarmControlPanelState.TRIGGERED,
            }
            return state_map.get(ha_state, AlarmControlPanelState.DISARMED)

        # Fallback mapping
        status_upper = str(status).upper()
        if "AWAY" in status_upper or "ARMED" in status_upper:
            return AlarmControlPanelState.ARMED_AWAY
        if "STAY" in status_upper or "HOME" in status_upper:
            return AlarmControlPanelState.ARMED_HOME

        return AlarmControlPanelState.DISARMED

    def _clear_optimistic_state(self) -> None:
        """Clear optimistic state after sync."""
        self._optimistic_state = None
        self.async_write_ha_state()

    def _schedule_optimistic_clear(self, expected_state, timeout=15):
        """Schedule clearing optimistic state after timeout."""
        async def _clear():
            await asyncio.sleep(timeout)
            if self._optimistic_state is not None:
                self._optimistic_state = None
                self.async_write_ha_state()
                self._verify_state(expected_state)
        self.hass.async_create_task(_clear())

    def _verify_state(self, expected_state):
        """Verify state after optimistic clear and notify if mismatch."""
        actual = self.state
        if actual != expected_state:
            if expected_state in (AlarmControlPanelState.ARMED_AWAY, AlarmControlPanelState.ARMED_HOME):
                _notify(
                    self.hass,
                    "O alarme pode nao ter armado corretamente.\n\n"
                    "Verifique se existem zonas abertas ou "
                    "se o painel respondeu ao comando.",
                    title="Aviso: Alarme nao confirmado",
                    notification_id=f"alarm_verify_{self._device_id}"
                )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from coordinator."""
        if self._optimistic_state is not None:
            real_state = self._get_real_state()
            if real_state == self._optimistic_state:
                self._optimistic_state = None
        super()._handle_coordinator_update()

    def _get_real_state(self):
        """Get the real state from coordinator data (ignoring optimistic)."""
        partition = self.coordinator.get_partition(self._device_id, self._partition_id)
        device = self.coordinator.get_device(self._device_id)

        if not partition:
            return None

        if device and device.get("is_triggered"):
            return AlarmControlPanelState.TRIGGERED

        status = partition.get("status")
        if not status and device:
            status = device.get("arm_mode")

        if not status:
            return AlarmControlPanelState.DISARMED

        ha_state = STATE_MAPPING.get(status)
        if ha_state:
            state_map = {
                "armed_away": AlarmControlPanelState.ARMED_AWAY,
                "armed_home": AlarmControlPanelState.ARMED_HOME,
                "disarmed": AlarmControlPanelState.DISARMED,
                "triggered": AlarmControlPanelState.TRIGGERED,
            }
            return state_map.get(ha_state, AlarmControlPanelState.DISARMED)

        status_upper = str(status).upper()
        if "AWAY" in status_upper or "ARMED" in status_upper:
            return AlarmControlPanelState.ARMED_AWAY
        if "STAY" in status_upper or "HOME" in status_upper:
            return AlarmControlPanelState.ARMED_HOME

        return AlarmControlPanelState.DISARMED

    @property
    def extra_state_attributes(self):
        """Return extra state attributes."""
        partition = self.coordinator.get_partition(self._device_id, self._partition_id)
        device = self.coordinator.get_device(self._device_id)

        attrs = {
            "device_id": self._device_id,
            "partition_id": self._partition_id,
        }

        if partition:
            attrs["partition_name"] = partition.get("name")
            attrs["is_in_alarm"] = partition.get("is_in_alarm", False)
            attrs["raw_status"] = partition.get("status")

        if device:
            attrs["arm_mode"] = device.get("arm_mode")
            attrs["is_triggered"] = device.get("is_triggered", False)
            attrs["has_saved_password"] = device.get("has_saved_password", False)
            attrs["partitions_enabled"] = device.get("partitions_enabled")
            # Connection status (for detecting AMT legacy app blocking).
            # `connection_unavailable` is the debounced value used for
            # `available`; `connection_unavailable_raw` reflects every poll
            # so users can see brief APK takeovers without the entity
            # actually flipping to unavailable.
            attrs["connection_unavailable"] = device.get("connection_unavailable", False)
            attrs["connection_unavailable_raw"] = device.get("connection_unavailable_raw", False)
            # The middleware's `last_updated` stamp is deliberately NOT an
            # attribute. Home Assistant records a new state row whenever any
            # attribute differs, and a stamp that moves on every poll turns a
            # panel that never changes into one row per poll: measured at
            # 891,560 rows for this single entity against 2,898 for the next
            # busiest one, and an 849 MB database. The entity already carries
            # HA's own `last_updated`/`last_changed`, which say the same thing.

        attrs["estado_texto"] = _estado_texto(self.state)
        attrs["pre_trigger_arm_mode"] = _pre_trigger_arm_mode_attr(self.coordinator, self._device_id)
        attrs.update(_last_trigger_attrs(self.coordinator, self._device_id))
        return attrs

    def _get_current_partition_state(self) -> Optional[str]:
        """Get current partition state from coordinator data."""
        partition = self.coordinator.get_partition(self._device_id, self._partition_id)
        if partition:
            return str(partition.get("status", "")).lower()
        return None

    async def async_alarm_disarm(self, code: str = None) -> None:
        """Send disarm command with optimistic update."""
        _LOGGER.info(f"Disarming partition {self._partition_id}")

        # Check if already disarmed to avoid unnecessary commands
        current_state = self._get_current_partition_state()
        if current_state == "disarmed":
            _LOGGER.debug(f"Partition {self._partition_id} already disarmed, skipping command")
            return

        # Optimistic update - UI responds immediately
        self._optimistic_state = AlarmControlPanelState.DISARMED
        self.async_write_ha_state()

        # Execute command in background with device lock to prevent race conditions
        async def _execute_disarm():
            device_lock = _get_device_command_lock(self._device_id)
            async with device_lock:
                try:
                    result = await self.coordinator.client.disarm_partition(
                        self._device_id,
                        self._partition_id
                    )

                    if not result.get("success"):
                        error_msg = self._format_disarm_error(result)
                        # Don't revert for "No response" - command likely worked
                        if "No response" not in str(result.get("error", "")):
                            _LOGGER.error(f"Failed to disarm partition: {error_msg}")
                            # Revert optimistic state on error
                            self._optimistic_state = None
                            self.async_write_ha_state()
                            # Show error to user via persistent notification
                            _notify(
                                self.hass,
                                error_msg,
                                title="Erro ao Desarmar Alarme",
                                notification_id=f"alarm_error_{self._device_id}_{self._partition_id}"
                            )
                        else:
                            _LOGGER.warning(f"Disarm command sent but no response received")
                except Exception as e:
                    _LOGGER.error(f"Error disarming partition: {e}")
                    # Revert optimistic state on exception
                    self._optimistic_state = None
                    self.async_write_ha_state()
                    # Show error notification
                    _notify(
                        self.hass,
                        f"Erro ao desarmar: {str(e)}",
                        title="Erro ao Desarmar Alarme",
                        notification_id=f"alarm_error_{self._device_id}_{self._partition_id}"
                    )
                    return
                # Don't clear optimistic state here - let SSE event update
                # coordinator data, which will make optimistic state redundant.
                self._schedule_optimistic_clear(AlarmControlPanelState.DISARMED)

        # Run in background
        self.hass.async_create_task(_execute_disarm())

    async def async_alarm_arm_home(self, code: str = None) -> None:
        """Send arm home (stay/partial) command with optimistic update."""
        _LOGGER.info(f"Arming partition {self._partition_id} in home mode")

        # Check if already armed in home mode to avoid unnecessary commands
        current_state = self._get_current_partition_state()
        if current_state in ("armed_home", "armed_stay"):
            _LOGGER.debug(f"Partition {self._partition_id} already armed (home), skipping command")
            return

        # Optimistic update - UI responds immediately
        self._optimistic_state = AlarmControlPanelState.ARMING
        self.async_write_ha_state()

        # Execute command in background with device lock to prevent race conditions
        async def _execute_arm_home():
            device_lock = _get_device_command_lock(self._device_id)
            async with device_lock:
                try:
                    result = await self.coordinator.client.arm_partition(
                        self._device_id,
                        self._partition_id,
                        mode="home"
                    )

                    if result.get("success"):
                        # Update to armed state
                        self._optimistic_state = AlarmControlPanelState.ARMED_HOME
                        self.async_write_ha_state()
                    else:
                        error_msg = self._format_arm_error(result)
                        # Don't revert for "No response" - command likely worked
                        if "No response" not in str(result.get("error", "")):
                            _LOGGER.error(f"Failed to arm partition in home mode: {error_msg}")
                            # Revert optimistic state on error
                            self._optimistic_state = None
                            self.async_write_ha_state()
                            # Show error to user via persistent notification
                            _notify(
                                self.hass,
                                error_msg,
                                title="Erro ao Armar Alarme",
                                notification_id=f"alarm_error_{self._device_id}_{self._partition_id}"
                            )
                            # Send actionable mobile notification for open zones
                            open_zones = result.get("open_zones", [])
                            if open_zones:
                                self._store_bypass_and_notify("home", open_zones)
                        else:
                            _LOGGER.warning(f"Arm home command sent but no response received")
                            self._optimistic_state = AlarmControlPanelState.ARMED_HOME
                            self.async_write_ha_state()
                except Exception as e:
                    _LOGGER.error(f"Error arming partition in home mode: {e}")
                    # Revert optimistic state on exception
                    self._optimistic_state = None
                    self.async_write_ha_state()
                    # Show error notification
                    _notify(
                        self.hass,
                        f"Erro ao armar (home): {str(e)}",
                        title="Erro ao Armar Alarme",
                        notification_id=f"alarm_error_{self._device_id}_{self._partition_id}"
                    )
                    return
                # Don't clear optimistic state here - let SSE event update
                self._schedule_optimistic_clear(AlarmControlPanelState.ARMED_HOME)

        # Run in background
        self.hass.async_create_task(_execute_arm_home())

    async def async_alarm_arm_away(self, code: str = None) -> None:
        """Send arm away (total) command with optimistic update."""
        _LOGGER.info(f"Arming partition {self._partition_id} in away mode")

        # Check if already armed in away mode to avoid unnecessary commands
        current_state = self._get_current_partition_state()
        if current_state == "armed_away":
            _LOGGER.debug(f"Partition {self._partition_id} already armed (away), skipping command")
            return

        # Optimistic update - UI responds immediately
        self._optimistic_state = AlarmControlPanelState.ARMING
        self.async_write_ha_state()

        # Execute command in background with device lock to prevent race conditions
        async def _execute_arm_away():
            device_lock = _get_device_command_lock(self._device_id)
            async with device_lock:
                try:
                    result = await self.coordinator.client.arm_partition(
                        self._device_id,
                        self._partition_id,
                        mode="away"
                    )

                    if result.get("success"):
                        # Update to armed state
                        self._optimistic_state = AlarmControlPanelState.ARMED_AWAY
                        self.async_write_ha_state()
                    else:
                        error_msg = self._format_arm_error(result)
                        # Don't revert for "No response" - command likely worked
                        if "No response" not in str(result.get("error", "")):
                            _LOGGER.error(f"Failed to arm partition in away mode: {error_msg}")
                            # Revert optimistic state on error
                            self._optimistic_state = None
                            self.async_write_ha_state()
                            # Show error to user via persistent notification
                            _notify(
                                self.hass,
                                error_msg,
                                title="Erro ao Armar Alarme",
                                notification_id=f"alarm_error_{self._device_id}_{self._partition_id}"
                            )
                            # Send actionable mobile notification for open zones
                            open_zones = result.get("open_zones", [])
                            if open_zones:
                                self._store_bypass_and_notify("away", open_zones)
                        else:
                            _LOGGER.warning(f"Arm away command sent but no response received")
                            self._optimistic_state = AlarmControlPanelState.ARMED_AWAY
                            self.async_write_ha_state()
                except Exception as e:
                    _LOGGER.error(f"Error arming partition in away mode: {e}")
                    # Revert optimistic state on exception
                    self._optimistic_state = None
                    self.async_write_ha_state()
                    # Show error notification
                    _notify(
                        self.hass,
                        f"Erro ao armar (away): {str(e)}",
                        title="Erro ao Armar Alarme",
                        notification_id=f"alarm_error_{self._device_id}_{self._partition_id}"
                    )
                    return
                # Don't clear optimistic state here - let SSE event update
                self._schedule_optimistic_clear(AlarmControlPanelState.ARMED_AWAY)

        # Run in background
        self.hass.async_create_task(_execute_arm_away())

    def _format_arm_error(self, result: dict) -> str:
        """Format error message for arming failure."""
        error = result.get("error", "Falha ao armar")
        open_zones = result.get("open_zones", [])

        # Check for connection unavailable error
        if "ConnectionUnavailable" in error or "indisponivel" in error.lower():
            return (
                "Conexao com a central indisponivel.\n\n"
                "Verifique se o aplicativo AMT nao esta aberto.\n"
                "A central so permite uma conexao por vez."
            )

        if open_zones:
            zone_names = []
            for zone in open_zones:
                if isinstance(zone, dict):
                    name = zone.get("friendly_name") or zone.get("name") or f"Zona {zone.get('index', '?') + 1}"
                else:
                    name = str(zone)
                zone_names.append(name)
            zones_list = "\n".join(f"  - {z}" for z in zone_names)
            return f"Nao foi possivel armar: zonas abertas\n\n{zones_list}"

        return f"Falha ao armar: {error}"

    def _format_disarm_error(self, result: dict) -> str:
        """Format error message for disarming failure."""
        error = result.get("error", "Falha ao desarmar")

        # Check for connection unavailable error
        if "ConnectionUnavailable" in error or "indisponivel" in error.lower():
            return (
                "Conexao com a central indisponivel.\n\n"
                "Verifique se o aplicativo AMT nao esta aberto.\n"
                "A central so permite uma conexao por vez."
            )

        return f"Falha ao desarmar: {error}"

    def _store_bypass_and_notify(self, arm_type: str, open_zones: list) -> None:
        """Store bypass context and send actionable mobile notification."""
        from . import _send_bypass_notification

        self.hass.data[DOMAIN].setdefault("pending_bypass", {})
        self.hass.data[DOMAIN]["pending_bypass"][self._device_id] = {
            "arm_type": arm_type,
            "entity_type": "individual",
            "partition_id": self._partition_id,
            "zone_indices": [z["index"] for z in open_zones if isinstance(z, dict) and "index" in z],
            "timestamp": time.monotonic(),
        }
        self.hass.async_create_task(
            _send_bypass_notification(self.hass, self._device_id, arm_type, open_zones)
        )


class GuardianUnifiedAlarmControlPanel(CoordinatorEntity, RestoreEntity, AlarmControlPanelEntity):
    """Unified alarm control panel that controls multiple partitions."""

    _attr_has_entity_name = True
    _attr_code_arm_required = False
    _attr_code_format = None
    _attr_supported_features = (
        AlarmControlPanelEntityFeature.ARM_HOME |
        AlarmControlPanelEntityFeature.ARM_AWAY
    )

    def __init__(
        self,
        coordinator: GuardianCoordinator,
        device_id: int,
        device_mac: str,
        home_partitions: list,
        away_partitions: list,
        partitions: list[dict],
        partition_arm_modes: dict = None,
    ):
        """Initialize the unified alarm control panel."""
        super().__init__(coordinator)
        self._device_id = device_id
        self._device_mac = device_mac
        # Ensure partition indices are integers (may come as strings from config)
        self._home_partitions = [int(p) for p in home_partitions]
        self._away_partitions = [int(p) for p in away_partitions]
        self._partitions = partitions  # List of partition dicts
        # Arm mode per partition: {"0": "away", "1": "home", ...}
        # Default to "away" (total) for all partitions if not configured
        self._partition_arm_modes = partition_arm_modes or {}

        _LOGGER.info(
            f"Unified alarm initialized: home_partitions={self._home_partitions}, "
            f"away_partitions={self._away_partitions}, arm_modes={self._partition_arm_modes}"
        )

        # Optimistic state management
        self._optimistic_state: Optional[AlarmControlPanelState] = None

        # Last arming intent ("home" or "away") set by arm_home/arm_away
        # service calls. The unified state computation prefers this over
        # mode-based heuristics so that a user pressing "Em Casa" stays as
        # ARMED_HOME even when their `partition_arm_modes` config arms each
        # partition in `armed_away` mode (which would otherwise be classified
        # as ARMED_AWAY by the fallback). Cleared once all partitions are
        # disarmed.
        self._last_arm_intent: Optional[str] = None

        # When True, the next arm_away/arm_home skips the open-zone
        # pre-check. Set by the bypass+rearm flow (zones were just
        # anulled, so re-checking would re-detect them before the next
        # poll refreshes the cached status and bounce into a loop).
        self._skip_open_zone_check: bool = False

        # Entity attributes
        self._attr_unique_id = f"{device_mac}_unified_alarm"

    async def async_added_to_hass(self) -> None:
        """Restore the last arming intent across HA restarts.

        `_last_arm_intent` is otherwise runtime-only — losing it on
        restart drops the unified entity back to mode-based heuristics
        and can flip ARMED_HOME → ARMED_AWAY when `partition_arm_modes`
        arms partition 0 in away mode (which is the default).

        Only restores from the explicit `last_arm_intent` attribute we
        write out. NOT inferred from the state name itself: if the
        previous state was already wrong (e.g., this version is being
        rolled out while the entity is in a misclassified state), the
        state-name shortcut would lock the wrong intent in. With
        attribute-only restoration, an entity coming up without the
        attribute (first restart after upgrade) stays at intent=None
        and the set-based fallback in _compute_state recovers the
        correct mode from the configured home/away topology.
        """
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if not last_state:
            return
        stored_intent = last_state.attributes.get("last_arm_intent")
        if stored_intent in ("home", "away"):
            self._last_arm_intent = stored_intent
            _LOGGER.debug(f"Unified alarm restored last_arm_intent={stored_intent} from state attributes")

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
    def name(self) -> str:
        """Return the name of the alarm."""
        device = self.coordinator.get_device(self._device_id)
        if device:
            return device.get("description", "Alarme")
        return "Alarme"

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

    def _get_partition_states(self) -> dict[int, str]:
        """Get current state of each partition by index."""
        states = {}
        partitions = self.coordinator.data.get("partitions", []) if self.coordinator.data else []

        device_partitions = [p for p in partitions if p.get("device_id") == self._device_id]
        for idx, partition in enumerate(device_partitions):
            status = partition.get("status", "disarmed")
            states[idx] = status
            _LOGGER.debug(f"Partition {idx} status: {status}")

        return states

    def _active_bypass(self) -> Optional[dict]:
        """Return the pending open-zone bypass for this device, if still fresh.

        Set by `_store_bypass_and_notify` when arming detects open zones and
        the actionable "Ignorar e Armar" notification is sent. Goes stale
        after `_BYPASS_STALE_TIMEOUT` (same window the bypass+rearm handler
        uses), so a notification the user never answers eventually releases
        the ARMING hold. Returns None when absent or stale.
        """
        from . import _BYPASS_STALE_TIMEOUT

        pending = self.hass.data.get(DOMAIN, {}).get("pending_bypass", {}).get(self._device_id)
        if not pending:
            return None
        if (time.monotonic() - pending.get("timestamp", 0)) >= _BYPASS_STALE_TIMEOUT:
            return None
        return pending

    async def _get_live_open_zones(self) -> list[dict]:
        """Fetch a fresh status and return zones currently open (not bypassed).

        Used for the atomic open-zone pre-check before arming: we never want
        to arm part of the away set and leave the rest pending, nor trigger
        the siren on a partition before the user decides about the open
        zone(s). Returns an empty list on any failure so arming falls back to
        the per-partition path instead of being blocked.
        """
        try:
            status = await self.coordinator.client.get_alarm_status_auto(self._device_id)
        except Exception as e:  # noqa: BLE001 - never block arming on a check error
            _LOGGER.debug(f"Open-zone pre-check failed for device {self._device_id}: {e}")
            return []
        if not status:
            return []
        open_zones: list[dict] = []
        for z in status.get("zones", []):
            if z.get("is_open") and not z.get("is_bypassed"):
                idx = z.get("index", 0)
                open_zones.append({
                    "index": idx,
                    "name": z.get("name", f"Zona {idx + 1:02d}"),
                    "friendly_name": z.get("friendly_name"),
                })
        return open_zones

    def _compute_state(self) -> AlarmControlPanelState:
        """Compute the unified alarm state from coordinator data.

        Single source of truth shared by `state` (with optimistic override)
        and `_get_real_state` (used to clear optimistic).

        Priority order:
        1. Triggered → TRIGGERED.
        2. Arm waiting on an open-zone decision (pending bypass) and the
           target set not yet fully armed → ARMING. Keeps the panel from
           prematurely claiming ARMED_* while the user still has to confirm
           "Ignorar e Armar" (and nothing — or only part of the set — armed).
        3. No partitions armed → DISARMED.
        4. User-issued intent (`_last_arm_intent`), but ONLY when EVERY
           partition of the target set is armed. A single armed partition no
           longer declares the whole set armed (that was the root cause of
           the premature "Ausente" with one partition still disarmed).
        5. Fallback for arming via keypad / after HA restart: recover the
           mode from the configured home/away topology, then from raw mode
           counts.
        """
        device = self.coordinator.get_device(self._device_id)
        pending = self._active_bypass()
        state_str = compute_unified_state(
            is_triggered=bool(device and device.get("is_triggered")),
            partition_states=self._get_partition_states(),
            away_partitions=self._away_partitions,
            home_partitions=self._home_partitions,
            last_arm_intent=self._last_arm_intent,
            bypass_arm_type=pending.get("arm_type") if pending else None,
            partition_arm_modes=self._partition_arm_modes,
        )
        return AlarmControlPanelState(state_str)

    @property
    def state(self) -> str:
        """Return the state of the unified alarm."""
        if self._optimistic_state is not None:
            _LOGGER.debug(f"Unified alarm using optimistic state: {self._optimistic_state}")
            return self._optimistic_state
        computed = self._compute_state()
        _LOGGER.debug(f"Unified alarm computed state: {computed}")
        return computed

    def _compute_pre_trigger_arm_mode(self) -> Optional[str]:
        """Compute the pre-trigger home/away mode from the partition snapshot.

        The shared `_pre_trigger_arm_mode_attr` reads `coordinator._pre_trigger_arm_mode`,
        which on AMT_2018_E_SMART (V1 partial status) is always `"armed_away"` —
        the partial response cannot distinguish armed_stay from armed_away
        (`isecnet_protocol.py` defaults armed bits to `armed_away`). For the
        unified entity, home vs away is defined by *which* partitions are armed
        (per `home_partitions`/`away_partitions` config), not by the per-partition
        mode byte. Apply the same set-based recovery as `_compute_state` to the
        partition snapshot captured at trigger time.
        """
        snapshot = self.coordinator._pre_trigger_partition_status.get(self._device_id, {})
        return classify_arm_mode(
            armed_indices=snapshot.keys(),
            away_partitions=self._away_partitions,
            home_partitions=self._home_partitions,
            last_arm_intent=self._last_arm_intent,
        )

    @property
    def extra_state_attributes(self):
        """Return extra state attributes."""
        device = self.coordinator.get_device(self._device_id)
        partition_states = self._get_partition_states()

        # Build partition status list
        partition_status = []
        for idx, state in partition_states.items():
            partition = next(
                (p for p in self._partitions if self._partitions.index(p) == idx),
                None
            )
            name = partition.get("name", f"Particao {idx + 1}") if partition else f"Particao {idx + 1}"
            partition_status.append({
                "index": idx,
                "name": name,
                "status": state,
                "in_home_mode": idx in self._home_partitions,
                "in_away_mode": idx in self._away_partitions,
            })

        attrs = {
            "device_id": self._device_id,
            "unified_mode": True,
            "home_partitions": self._home_partitions,
            "away_partitions": self._away_partitions,
            "partition_arm_modes": self._partition_arm_modes,
            "partition_status": partition_status,
            "pre_trigger_arm_mode": self._compute_pre_trigger_arm_mode(),
            # Persisted across HA restarts via RestoreEntity so the
            # unified entity does not drop to mode-based heuristics
            # after a restart while the central is partially armed.
            "last_arm_intent": self._last_arm_intent,
            "estado_texto": _estado_texto(self.state),
        }

        if device:
            attrs["connection_unavailable"] = device.get("connection_unavailable", False)
            attrs["connection_unavailable_raw"] = device.get("connection_unavailable_raw", False)
            # No `last_updated` attribute here either — see
            # GuardianAlarmControlPanel.extra_state_attributes for why.

        attrs.update(_last_trigger_attrs(self.coordinator, self._device_id))
        return attrs

    def _schedule_optimistic_clear(self, expected_state, timeout=15):
        """Schedule clearing optimistic state after timeout."""
        async def _clear():
            await asyncio.sleep(timeout)
            if self._optimistic_state is not None:
                self._optimistic_state = None
                self.async_write_ha_state()
                self._verify_state(expected_state)
        self.hass.async_create_task(_clear())

    def _verify_state(self, expected_state):
        """Verify state after optimistic clear and notify if mismatch."""
        actual = self.state
        if actual != expected_state:
            if expected_state in (AlarmControlPanelState.ARMED_AWAY, AlarmControlPanelState.ARMED_HOME):
                _notify(
                    self.hass,
                    "O alarme pode nao ter armado corretamente.\n\n"
                    "Verifique se existem zonas abertas ou "
                    "se o painel respondeu ao comando.",
                    title="Aviso: Alarme nao confirmado",
                    notification_id=f"alarm_verify_{self._device_id}"
                )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from coordinator."""
        if self._optimistic_state is not None:
            real_state = self._get_real_state()
            if real_state == self._optimistic_state:
                self._optimistic_state = None

        # Clear stale arming intent once the API confirms the device is
        # really disarmed (e.g., user disarmed via the keypad). The check
        # MUST come from the real-time status payload — earlier versions
        # inferred "all disarmed" from `partition_states`, which proved
        # unsafe in two ways:
        #   1. Optimistic ARMING/ARMED_* races during a HA-issued command
        #      (~1s window before ISECNet picks up the new partition).
        #   2. Partial cloud refreshes where one partition simply
        #      disappears from the cached payload for a single cycle —
        #      observed on 2026-05-04 00:24, 53 min after Em Casa, when
        #      `partition_states` momentarily collapsed to `{0: disarmed}`
        #      and the cleanup fired before the next poll restored the
        #      missing partition. The unified entity then fell back to
        #      mode-based heuristics and flipped to Ausente.
        # Trusting the parser's `is_armed`/`is_triggered` flags only — and
        # gating on `optimistic is None` — covers both regressions without
        # false-clearing during transients.
        if (
            self._last_arm_intent is not None
            and self._optimistic_state is None
            and not self._active_bypass()
        ):
            device = self.coordinator.get_device(self._device_id)
            if device:
                rt = device.get("real_time_status") or {}
                # Only clear when the API explicitly says the device is
                # idle (not armed and not triggered) — and we actually
                # have a fresh real-time status (a missing/empty payload
                # means we don't know yet, so leave the intent alone).
                if rt and rt.get("is_armed") is False and not rt.get("is_triggered"):
                    self._last_arm_intent = None

        super()._handle_coordinator_update()

    def _get_real_state(self):
        """Get the real state from coordinator data (ignoring optimistic)."""
        return self._compute_state()

    def _store_bypass_and_notify(self, arm_type: str, open_zones: list) -> None:
        """Store bypass context and send actionable mobile notification."""
        from . import _send_bypass_notification

        self.hass.data[DOMAIN].setdefault("pending_bypass", {})
        self.hass.data[DOMAIN]["pending_bypass"][self._device_id] = {
            "arm_type": arm_type,
            "entity_type": "unified",
            "partition_id": None,
            "zone_indices": [z["index"] for z in open_zones if isinstance(z, dict) and "index" in z],
            "timestamp": time.monotonic(),
        }
        self.hass.async_create_task(
            _send_bypass_notification(self.hass, self._device_id, arm_type, open_zones)
        )

    async def async_alarm_disarm(self, code: str = None) -> None:
        """Disarm all partitions that are currently armed."""
        _LOGGER.info(f"Unified alarm: Disarming armed partitions for device {self._device_id}")

        self._last_arm_intent = None
        self._skip_open_zone_check = False
        # Cancel any arm that was held waiting for an open-zone decision so
        # the panel does not stay stuck in ARMING after the user disarms.
        self.hass.data.get(DOMAIN, {}).get("pending_bypass", {}).pop(self._device_id, None)
        self._optimistic_state = AlarmControlPanelState.DISARMED
        self.async_write_ha_state()

        async def _execute_disarm():
            device_lock = _get_device_command_lock(self._device_id)
            async with device_lock:
                all_success = True
                errors = []

                # Get current partition states to only disarm armed partitions
                # This avoids sending DISARM to already-disarmed partitions which can
                # cause unexpected behavior on some panels (e.g., AMT_2018_E_SMART)
                partition_states = self._get_partition_states()
                armed_states = {"armed_away", "armed_stay", "armed_home", "armed"}

                # Only disarm partitions that are actually armed
                all_partitions = set(self._away_partitions) | set(self._home_partitions)
                partitions_to_disarm = []
                for idx in all_partitions:
                    status = partition_states.get(idx, "")
                    status_lower = str(status).lower() if status else ""
                    if status_lower in armed_states:
                        partitions_to_disarm.append(idx)
                    else:
                        _LOGGER.debug(f"Partition {idx} already disarmed (status={status}), skipping")

                _LOGGER.info(f"Partitions to disarm: {partitions_to_disarm} (armed from {all_partitions})")

                for idx in partitions_to_disarm:
                    if idx < len(self._partitions):
                        partition_id = self._partitions[idx].get("id")
                        try:
                            result = await self.coordinator.client.disarm_partition(
                                self._device_id,
                                partition_id
                            )
                            if not result.get("success"):
                                if "No response" not in str(result.get("error", "")):
                                    all_success = False
                                    errors.append(f"Particao {idx + 1}: {result.get('error')}")
                        except Exception as e:
                            all_success = False
                            errors.append(f"Particao {idx + 1}: {str(e)}")

                if not all_success:
                    self._optimistic_state = None
                    self.async_write_ha_state()
                    error_msg = "Falha ao desarmar:\n" + "\n".join(errors)
                    _notify(
                        self.hass,
                        error_msg,
                        title="Erro ao Desarmar Alarme",
                        notification_id=f"alarm_error_{self._device_id}_unified"
                    )
                    return

                # Don't clear optimistic state here - let SSE event update
                self._schedule_optimistic_clear(AlarmControlPanelState.DISARMED)

        self.hass.async_create_task(_execute_disarm())

    async def async_alarm_arm_home(self, code: str = None) -> None:
        """Arm home partitions only."""
        _LOGGER.info(
            f"Unified alarm: Arming HOME partitions {self._home_partitions} for device {self._device_id}"
        )

        self._last_arm_intent = "home"
        self._optimistic_state = AlarmControlPanelState.ARMING
        self.async_write_ha_state()

        async def _execute_arm_home():
            device_lock = _get_device_command_lock(self._device_id)
            async with device_lock:
                # Consume the flag here too. This path arms partition by
                # partition (the panel itself decides on open zones), but
                # leaving it set would silently skip the pre-check on the next
                # away arm.
                self._skip_open_zone_check = False

                all_success = True
                errors = []
                open_zones_all = []

                # First disarm partitions not in home mode (if they're armed)
                partition_states = self._get_partition_states()
                armed_states = {"armed_away", "armed_stay", "armed_home", "armed"}
                for idx, status in partition_states.items():
                    status_lower = str(status).lower() if status else ""
                    # Only disarm if partition is not in home mode AND is actually armed
                    # Note: Can't use "armed" in status because "disarmed" contains "armed"!
                    if idx not in self._home_partitions and status_lower in armed_states:
                        if idx < len(self._partitions):
                            partition_id = self._partitions[idx].get("id")
                            _LOGGER.info(f"Disarming partition {idx} (not in home mode, status={status})")
                            try:
                                await self.coordinator.client.disarm_partition(
                                    self._device_id, partition_id
                                )
                            except Exception as e:
                                _LOGGER.warning(f"Error disarming partition {idx}: {e}")

                # Arm home partitions
                for idx in self._home_partitions:
                    if idx < len(self._partitions):
                        partition_id = self._partitions[idx].get("id")
                        # Use configured arm mode for this partition, default to "away"
                        arm_mode = self._partition_arm_modes.get(str(idx), "away")
                        _LOGGER.debug(f"Arming partition {idx} with mode={arm_mode}")
                        try:
                            result = await self.coordinator.client.arm_partition(
                                self._device_id,
                                partition_id,
                                mode=arm_mode
                            )
                            if not result.get("success"):
                                if "No response" not in str(result.get("error", "")):
                                    all_success = False
                                    errors.append(f"Particao {idx + 1}: {result.get('error')}")
                                    if result.get("open_zones"):
                                        open_zones_all.extend(result.get("open_zones"))
                        except Exception as e:
                            all_success = False
                            errors.append(f"Particao {idx + 1}: {str(e)}")

                if all_success:
                    self._optimistic_state = AlarmControlPanelState.ARMED_HOME
                    self.async_write_ha_state()
                else:
                    self._optimistic_state = None
                    self.async_write_ha_state()

                    if open_zones_all:
                        zone_names = []
                        for zone in open_zones_all:
                            if isinstance(zone, dict):
                                name = zone.get("friendly_name") or zone.get("name") or f"Zona {zone.get('index', '?') + 1}"
                            else:
                                name = str(zone)
                            if name not in zone_names:
                                zone_names.append(name)
                        zones_list = "\n".join(f"  - {z}" for z in zone_names)
                        error_msg = f"Nao foi possivel armar: zonas abertas\n\n{zones_list}"
                    else:
                        error_msg = "Falha ao armar (Em Casa):\n" + "\n".join(errors)

                    _notify(
                        self.hass,
                        error_msg,
                        title="Erro ao Armar Alarme",
                        notification_id=f"alarm_error_{self._device_id}_unified"
                    )
                    # Send actionable mobile notification for open zones
                    if open_zones_all:
                        self._store_bypass_and_notify("home", open_zones_all)
                    return

                # Don't clear optimistic state here - let SSE event update
                self._schedule_optimistic_clear(AlarmControlPanelState.ARMED_HOME)

        self.hass.async_create_task(_execute_arm_home())

    async def async_alarm_arm_away(self, code: str = None) -> None:
        """Arm all away partitions."""
        _LOGGER.info(
            f"Unified alarm: Arming AWAY partitions {self._away_partitions} for device {self._device_id}"
        )

        self._last_arm_intent = "away"
        self._optimistic_state = AlarmControlPanelState.ARMING
        self.async_write_ha_state()

        async def _execute_arm_away():
            device_lock = _get_device_command_lock(self._device_id)
            async with device_lock:
                skip_check = self._skip_open_zone_check
                self._skip_open_zone_check = False

                idxs = [i for i in self._away_partitions if i < len(self._partitions)]
                target_modes = {
                    self._partition_arm_modes.get(str(i), "away") for i in idxs
                }

                # Preferred path: atomic server-side multi-arm. A single
                # ISECNet session pre-checks open zones and arms all-or-nothing
                # — no window where one partition arms (siren) before the
                # open-zone decision. Used when the target partitions share one
                # mode (the common case). Mixed per-partition modes fall back to
                # the client-side pre-check + per-partition loop below.
                if idxs and len(target_modes) == 1:
                    mode = next(iter(target_modes))
                    try:
                        # skip_check also has to reach the server: its pre-check
                        # reads the same status frame, which carries no bypass
                        # bitmap, so a zone bypassed a moment ago still counts
                        # as open and the "Ignorar Zonas e Armar" retry would
                        # be refused exactly like the first attempt.
                        result = await self.coordinator.client.arm_partitions_multi(
                            self._device_id, idxs, mode=mode,
                            ignore_open_zones=skip_check,
                        )
                    except Exception as e:  # noqa: BLE001
                        result = {"success": False, "error": str(e)}

                    if result.get("success"):
                        self._optimistic_state = AlarmControlPanelState.ARMED_AWAY
                        self.async_write_ha_state()
                        self._schedule_optimistic_clear(AlarmControlPanelState.ARMED_AWAY)
                        return

                    open_zones = result.get("open_zones") or []
                    if open_zones:
                        _LOGGER.info(
                            f"Arm AWAY held for device {self._device_id}: "
                            f"{len(open_zones)} open zone(s); nothing armed, "
                            f"awaiting 'Ignorar e Armar' confirmation"
                        )
                        self._store_bypass_and_notify("away", open_zones)
                        self._optimistic_state = None
                        self.async_write_ha_state()
                        return

                    # Non-open-zone failure (connection/busy/etc.)
                    self._optimistic_state = None
                    self.async_write_ha_state()
                    _notify(
                        self.hass,
                        "Falha ao armar (Ausente): "
                        + str(result.get("error", "erro desconhecido")),
                        title="Erro ao Armar Alarme",
                        notification_id=f"alarm_error_{self._device_id}_unified",
                    )
                    return

                # Fallback (mixed per-partition modes): client-side open-zone
                # pre-check, then per-partition arm.
                if not skip_check:
                    open_zones = await self._get_live_open_zones()
                    if open_zones:
                        _LOGGER.info(
                            f"Arm AWAY held for device {self._device_id}: "
                            f"{len(open_zones)} open zone(s); nothing armed, "
                            f"awaiting 'Ignorar e Armar' confirmation"
                        )
                        self._store_bypass_and_notify("away", open_zones)
                        self._optimistic_state = None
                        self.async_write_ha_state()
                        return

                all_success = True
                errors = []
                open_zones_all = []

                # Arm away partitions
                for idx in self._away_partitions:
                    if idx < len(self._partitions):
                        partition_id = self._partitions[idx].get("id")
                        # Use configured arm mode for this partition, default to "away"
                        arm_mode = self._partition_arm_modes.get(str(idx), "away")
                        _LOGGER.debug(f"Arming partition {idx} with mode={arm_mode}")
                        try:
                            result = await self.coordinator.client.arm_partition(
                                self._device_id,
                                partition_id,
                                mode=arm_mode
                            )
                            if not result.get("success"):
                                if "No response" not in str(result.get("error", "")):
                                    all_success = False
                                    errors.append(f"Particao {idx + 1}: {result.get('error')}")
                                    if result.get("open_zones"):
                                        open_zones_all.extend(result.get("open_zones"))
                        except Exception as e:
                            all_success = False
                            errors.append(f"Particao {idx + 1}: {str(e)}")

                if all_success:
                    self._optimistic_state = AlarmControlPanelState.ARMED_AWAY
                    self.async_write_ha_state()
                else:
                    self._optimistic_state = None
                    self.async_write_ha_state()

                    if open_zones_all:
                        zone_names = []
                        for zone in open_zones_all:
                            if isinstance(zone, dict):
                                name = zone.get("friendly_name") or zone.get("name") or f"Zona {zone.get('index', '?') + 1}"
                            else:
                                name = str(zone)
                            if name not in zone_names:
                                zone_names.append(name)
                        zones_list = "\n".join(f"  - {z}" for z in zone_names)
                        error_msg = f"Nao foi possivel armar: zonas abertas\n\n{zones_list}"
                    else:
                        error_msg = "Falha ao armar (Ausente):\n" + "\n".join(errors)

                    _notify(
                        self.hass,
                        error_msg,
                        title="Erro ao Armar Alarme",
                        notification_id=f"alarm_error_{self._device_id}_unified"
                    )
                    # Send actionable mobile notification for open zones
                    if open_zones_all:
                        self._store_bypass_and_notify("away", open_zones_all)
                    return

                # Don't clear optimistic state here - let SSE event update
                self._schedule_optimistic_clear(AlarmControlPanelState.ARMED_AWAY)

        self.hass.async_create_task(_execute_arm_away())
