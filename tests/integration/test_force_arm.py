"""Force-arm after bypass must not be refused by the server pre-check.

Regression for 2026-08-05 20:58: the "fechar casa" script arms away, gets
OpenZonesError for zone 30, fires IG_BYPASS_ARM, and the handler bypasses the
zone and re-arms. The retry was refused with the same error because
/arm-multi re-reads the ISECNet status frame — which never carries the bypass
bitmap — so a zone bypassed one second earlier still counts as open. The flow
could never succeed, no matter how many times it retried.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

try:
    from custom_components.intelbras_guardian.alarm_control_panel import (
        GuardianUnifiedAlarmControlPanel,
    )
except ImportError as e:  # pragma: no cover - depends on the installed HA
    # AlarmControlPanelState landed in HA 2024.11; the pinned test HA on
    # Python 3.11 is older. The `integration-test-current-ha` CI job runs
    # these against the HA version actually deployed.
    pytest.skip(f"needs a newer Home Assistant: {e}", allow_module_level=True)

DEVICE_ID = 550793


def _panel():
    coordinator = MagicMock()
    coordinator.client.arm_partitions_multi = AsyncMock(
        return_value={"success": True}
    )
    coordinator.data = {"devices": {DEVICE_ID: {}}}

    panel = GuardianUnifiedAlarmControlPanel.__new__(GuardianUnifiedAlarmControlPanel)
    panel.coordinator = coordinator
    panel._device_id = DEVICE_ID
    panel._partitions = [{"id": 1775173}, {"id": 1775174}]
    panel._away_partitions = [0, 1]
    panel._home_partitions = [0]
    panel._partition_arm_modes = {"0": "away", "1": "away"}
    panel._skip_open_zone_check = False
    panel._optimistic_state = None
    panel._last_arm_intent = None
    panel.hass = MagicMock()
    panel.async_write_ha_state = MagicMock()
    panel._schedule_optimistic_clear = MagicMock()
    panel._store_bypass_and_notify = MagicMock()
    return panel


def _arm_away(panel):
    asyncio.run(panel.async_alarm_arm_away())


def test_normal_arm_keeps_the_server_precheck():
    panel = _panel()
    _arm_away(panel)

    kwargs = panel.coordinator.client.arm_partitions_multi.await_args.kwargs
    assert kwargs["ignore_open_zones"] is False


def test_arm_after_bypass_tells_the_server_to_skip_the_precheck():
    panel = _panel()
    panel._skip_open_zone_check = True  # set by _execute_bypass_and_rearm

    _arm_away(panel)

    kwargs = panel.coordinator.client.arm_partitions_multi.await_args.kwargs
    assert kwargs["ignore_open_zones"] is True


def test_the_skip_is_consumed_once():
    """A forced arm must not silently disable the pre-check forever."""
    panel = _panel()
    panel._skip_open_zone_check = True

    _arm_away(panel)
    _arm_away(panel)

    first, second = panel.coordinator.client.arm_partitions_multi.await_args_list
    assert first.kwargs["ignore_open_zones"] is True
    assert second.kwargs["ignore_open_zones"] is False


def test_arm_home_also_consumes_the_skip_flag():
    """Home arms per partition, but must not leave the flag set for later."""
    panel = _panel()
    panel._skip_open_zone_check = True
    panel._get_partition_states = MagicMock(return_value={})
    panel.coordinator.client.arm_partition = AsyncMock(return_value={"success": True})
    panel.coordinator.client.disarm_partition = AsyncMock(return_value={"success": True})
    panel.coordinator.async_request_refresh = AsyncMock()

    asyncio.run(panel.async_alarm_arm_home())

    assert panel._skip_open_zone_check is False
