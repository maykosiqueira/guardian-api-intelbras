"""Pure state-classification logic for the unified alarm panel.

This module is intentionally free of Home Assistant imports so the
classification rules can be unit-tested without a Home Assistant install
(see ``tests/test_state_logic.py``). It returns the string values of
``homeassistant.components.alarm_control_panel.AlarmControlPanelState``
(a ``StrEnum``), which the entity converts back into the enum.

The single source of truth for "what state is the unified panel in?" lives
here. ``GuardianUnifiedAlarmControlPanel._compute_state`` is a thin wrapper
that gathers the inputs from the coordinator and calls
:func:`compute_unified_state`.
"""
from __future__ import annotations

from typing import Iterable, Mapping, Optional

# String values mirror AlarmControlPanelState (a StrEnum). Keeping them as
# plain constants avoids importing Home Assistant here.
STATE_DISARMED = "disarmed"
STATE_ARMING = "arming"
STATE_ARMED_HOME = "armed_home"
STATE_ARMED_AWAY = "armed_away"
STATE_TRIGGERED = "triggered"

_ARMED_AWAY = "armed_away"
_ARMED_HOME = ("armed_stay", "armed_home")
_ARMED_GENERIC = "armed"


def compute_unified_state(
    *,
    is_triggered: bool,
    partition_states: Mapping[int, Optional[str]],
    away_partitions: Iterable[int],
    home_partitions: Iterable[int],
    last_arm_intent: Optional[str],
    bypass_arm_type: Optional[str],
    partition_arm_modes: Optional[Mapping[str, str]] = None,
) -> str:
    """Classify the unified alarm state. Pure function — no HA dependencies.

    Priority order:
      1. ``is_triggered`` → TRIGGERED.
      2. A bypass is pending (open zones detected on arm) and the target set
         of that arm is not fully armed yet → ARMING. Keyed off
         ``bypass_arm_type`` (not ``last_arm_intent``, which may have been
         cleared once the central reported every partition disarmed in the
         atomic-hold case).
      3. No partitions armed → DISARMED.
      4. User intent (``last_arm_intent``), but ONLY when EVERY partition of
         the target set is armed (``target ⊆ armed``). A single armed
         partition no longer declares the whole set armed.
      5. Fallback (keypad arm / after HA restart): recover the mode from the
         configured home/away topology, then from raw mode counts.

    Args:
        is_triggered: device-level triggered flag.
        partition_states: index → status string (e.g. ``"armed_away"``,
            ``"disarmed"``, ``"armed_stay"``, ``"armed"``).
        away_partitions / home_partitions: configured partition index sets.
        last_arm_intent: ``"home"``, ``"away"`` or ``None``.
        bypass_arm_type: arm_type of a fresh pending bypass (``"home"`` /
            ``"away"``), or ``None`` when there is no pending bypass.
        partition_arm_modes: index(str) → ``"home"``/``"away"`` used to
            resolve a generic ``"armed"`` status (default ``"away"``).

    Returns:
        One of the ``STATE_*`` string constants.
    """
    if is_triggered:
        return STATE_TRIGGERED

    modes = partition_arm_modes or {}
    away_set = set(away_partitions)
    home_set = set(home_partitions)

    mode_counts = {"away": 0, "home": 0}
    armed_indices: set[int] = set()
    for idx, status in partition_states.items():
        s = str(status).lower() if status else ""
        if s == _ARMED_AWAY:
            mode_counts["away"] += 1
            armed_indices.add(idx)
        elif s in _ARMED_HOME:
            mode_counts["home"] += 1
            armed_indices.add(idx)
        elif s == _ARMED_GENERIC:
            # Generic "armed" without mode suffix — use the configured
            # intent for this partition (default: away).
            intent = modes.get(str(idx), "away")
            mode_counts[intent] = mode_counts.get(intent, 0) + 1
            armed_indices.add(idx)

    # An arm "completes" only when EVERY partition of the target set is armed.
    away_complete = bool(away_set) and away_set.issubset(armed_indices)
    home_complete = bool(home_set) and home_set.issubset(armed_indices)

    # Bypass pending + target set incomplete → still arming.
    if bypass_arm_type == "away" and not away_complete:
        return STATE_ARMING
    if bypass_arm_type == "home" and not home_complete:
        return STATE_ARMING

    if not armed_indices:
        return STATE_DISARMED

    # User-issued intent wins, but only when its full target set is armed.
    if last_arm_intent == "home" and home_complete:
        return STATE_ARMED_HOME
    if last_arm_intent == "away" and away_complete:
        return STATE_ARMED_AWAY

    # No usable intent: recover the mode from the configured topology.
    if away_set and armed_indices == away_set:
        return STATE_ARMED_AWAY
    if home_set and armed_indices == home_set and home_set != away_set:
        return STATE_ARMED_HOME

    # Mixed / partial pattern matching neither set exactly: prefer ARMED_AWAY
    # when any partition reports armed_away mode, otherwise ARMED_HOME.
    if mode_counts["away"] > 0:
        return STATE_ARMED_AWAY
    return STATE_ARMED_HOME


def classify_arm_mode(
    *,
    armed_indices: Iterable[int],
    away_partitions: Iterable[int],
    home_partitions: Iterable[int],
    last_arm_intent: Optional[str],
) -> Optional[str]:
    """Classify a set of armed partitions as ``"home"`` / ``"away"`` / ``None``.

    Recovers the home/away mode from a snapshot of *which* partitions are (or
    were) armed — e.g. the pre-trigger snapshot, since the AMT zeroes the
    partition byte during an active alarm. This answers "which mode?" rather
    than "which HA state?" (so it never returns disarmed/triggered), and is
    shared by the unified entity's ``_compute_pre_trigger_arm_mode``.

    Returns ``None`` when no partition is armed.
    """
    armed = set(armed_indices)
    if not armed:
        return None
    away_set = set(away_partitions)
    home_set = set(home_partitions)

    if last_arm_intent == "home" and home_set and armed.issubset(home_set):
        return "home"
    if last_arm_intent == "away" and away_set and armed.issubset(away_set):
        return "away"
    if away_set and armed == away_set:
        return "away"
    if home_set and armed == home_set and home_set != away_set:
        return "home"
    # Mixed: any partition exclusive to the away set tips it to away.
    return "away" if any(i in away_set and i not in home_set for i in armed) else "home"


def build_last_trigger_attrs(
    trigger: Optional[Mapping[str, object]],
    *,
    is_triggered: bool,
    started_at: Optional[float],
) -> dict:
    """Describe the last alarm trigger for the panel's state attributes.

    The panel carries the triggering zone in its OWN attributes so it changes
    atomically with the `triggered` state. Automations firing on `triggered`
    used to read `sensor.*_ultimo_disparo`, which is written by a different
    platform ~24ms later — long enough for the alert to go out naming the
    previous trigger (2026-07-29 13:37: "Ultimo evento: Sem disparos").

    ``last_trigger_is_current`` distinguishes the trigger in progress from a
    leftover record of an earlier alarm, so templates can fall back instead
    of naming a zone that has nothing to do with the current alarm.
    """
    if not trigger:
        return {
            "last_trigger_zone": None,
            "last_trigger_zones": [],
            "last_trigger_time": None,
            "last_trigger_is_current": False,
        }

    # Both writers stamp `captured_at` after `started_at` within the same
    # transition, so a record captured before the current trigger began
    # necessarily belongs to an earlier alarm.
    captured_at = trigger.get("captured_at")
    is_current = bool(
        is_triggered
        and started_at is not None
        and isinstance(captured_at, (int, float))
        and captured_at >= started_at
    )

    return {
        "last_trigger_zone": trigger.get("zone_name"),
        "last_trigger_zones": trigger.get("zones") or [],
        "last_trigger_time": trigger.get("timestamp"),
        "last_trigger_is_current": is_current,
    }
