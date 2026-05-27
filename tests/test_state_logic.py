"""Unit tests for the unified alarm panel state classification.

Regression contract for `compute_unified_state` — the logic behind
`GuardianUnifiedAlarmControlPanel._compute_state`. These cases pin the
behaviour the long chain of `unified`/`coordinator` fixes converged on,
plus the three bugs fixed in May/2026 (atomic away-arm + correct state).

Topology mirrors the user's real config: Em Casa = {0}, Ausente = {0,1},
both partitions in Total (away) mode.
"""
import pytest

from state_logic import (
    STATE_ARMED_AWAY,
    STATE_ARMED_HOME,
    STATE_ARMING,
    STATE_DISARMED,
    STATE_TRIGGERED,
    classify_arm_mode,
    compute_unified_state,
)

AWAY = [0, 1]
HOME = [0]
MODES = {"0": "away", "1": "away"}  # both Total

# id, intent, partition_states, bypass_arm_type, is_triggered, expected
CASES = [
    ("triggered_priority",            "away", {0: "armed_away", 1: "armed_away"}, None,   True,  STATE_TRIGGERED),
    ("away_complete",                 "away", {0: "armed_away", 1: "armed_away"}, None,   False, STATE_ARMED_AWAY),
    # Bug 1: only one away partition armed while a bypass is pending → must NOT be ARMED_AWAY.
    ("bug1_away_partial_with_bypass", "away", {0: "armed_away", 1: "disarmed"},   "away", False, STATE_ARMING),
    # Bug 3 (atomic): open zone detected, nothing armed yet, bypass pending → ARMING (not DISARMED).
    ("bug3_atomic_hold_bypass",       "away", {0: "disarmed", 1: "disarmed"},     "away", False, STATE_ARMING),
    ("nothing_armed_no_bypass",       "away", {0: "disarmed", 1: "disarmed"},     None,   False, STATE_DISARMED),
    ("home_complete",                 "home", {0: "armed_stay", 1: "disarmed"},   None,   False, STATE_ARMED_HOME),
    # Intent wins over the partition's reported mode (commit 853290c).
    ("home_intent_over_away_mode",    "home", {0: "armed_away", 1: "disarmed"},   None,   False, STATE_ARMED_HOME),
    ("restart_no_intent_away",        None,   {0: "armed_away", 1: "armed_away"}, None,   False, STATE_ARMED_AWAY),
    ("restart_no_intent_home",        None,   {0: "armed_stay", 1: "disarmed"},   None,   False, STATE_ARMED_HOME),
    ("home_open_zone_bypass",         "home", {0: "disarmed", 1: "disarmed"},     "home", False, STATE_ARMING),
    # Generic "armed" resolves via partition_arm_modes (both away) → away set complete.
    ("generic_armed_uses_modes",      None,   {0: "armed", 1: "armed"},           None,   False, STATE_ARMED_AWAY),
    ("no_intent_partial_a_only",      None,   {0: "armed_away", 1: "disarmed"},   None,   False, STATE_ARMED_HOME),
    # Bypass present but target already complete → not held in ARMING.
    ("complete_overrides_bypass",     "away", {0: "armed_away", 1: "armed_away"}, "away", False, STATE_ARMED_AWAY),
    ("partial_b_only_no_intent",      None,   {0: "disarmed", 1: "armed_away"},   None,   False, STATE_ARMED_AWAY),
    ("empty_partitions",              "away", {},                                 None,   False, STATE_DISARMED),
]


@pytest.mark.parametrize(
    "intent,parts,bypass,trig,expected",
    [(c[1], c[2], c[3], c[4], c[5]) for c in CASES],
    ids=[c[0] for c in CASES],
)
def test_compute_unified_state(intent, parts, bypass, trig, expected):
    assert (
        compute_unified_state(
            is_triggered=trig,
            partition_states=parts,
            away_partitions=AWAY,
            home_partitions=HOME,
            last_arm_intent=intent,
            bypass_arm_type=bypass,
            partition_arm_modes=MODES,
        )
        == expected
    )


# classify_arm_mode: recover home/away from the set of armed partitions
# (e.g. the pre-trigger snapshot). away_set={0,1}, home_set={0}.
MODE_CASES = [
    ("empty_none",            set(),  None,   None),
    ("both_armed_away",       {0, 1}, None,   "away"),
    ("only_a_home",           {0},    None,   "home"),
    ("only_b_away",           {1},    None,   "away"),
    ("intent_home_a",         {0},    "home", "home"),
    ("intent_away_both",      {0, 1}, "away", "away"),
]


@pytest.mark.parametrize(
    "armed,intent,expected",
    [(c[1], c[2], c[3]) for c in MODE_CASES],
    ids=[c[0] for c in MODE_CASES],
)
def test_classify_arm_mode(armed, intent, expected):
    assert (
        classify_arm_mode(
            armed_indices=armed,
            away_partitions=AWAY,
            home_partitions=HOME,
            last_arm_intent=intent,
        )
        == expected
    )


def test_state_constants_match_ha_values():
    """The string constants must equal AlarmControlPanelState's StrEnum values."""
    assert STATE_DISARMED == "disarmed"
    assert STATE_ARMING == "arming"
    assert STATE_ARMED_HOME == "armed_home"
    assert STATE_ARMED_AWAY == "armed_away"
    assert STATE_TRIGGERED == "triggered"
