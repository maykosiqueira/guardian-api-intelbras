"""Typed contracts for the FastAPI middleware payloads.

These ``TypedDict``s mirror the server's pydantic models
(``intelbras-guardian-api/app/models`` and ``app/api/v1/alarm.py``) and
document the exact shapes ``GuardianApiClient`` returns. They are
``TypedDict``s, so they are plain ``dict``s at runtime — zero behaviour
change — but let ``mypy`` catch misspelled/wrong keys statically (the class
of bug where ``result.get("succes")`` silently returns ``None``).

Adopt incrementally: annotate api_client return types now; the coordinator
can migrate field-by-field without any runtime change.
"""
from __future__ import annotations

from typing import List, Optional, TypedDict


class OpenZone(TypedDict, total=False):
    """A zone reported open when an arm is refused. See alarm.py OpenZoneInfo."""

    index: int
    name: str
    friendly_name: Optional[str]


class PartitionArmResult(TypedDict, total=False):
    """Per-partition result inside an arm-multi response."""

    partition_index: Optional[int]
    success: bool
    message: str


class ArmResult(TypedDict, total=False):
    """Result of arm / disarm / arm-multi / bypass operations.

    ``success`` is always present; the rest depend on the path. On an
    open-zones refusal, ``success`` is False and ``open_zones`` is populated.
    """

    success: bool
    error: str
    open_zones: List[OpenZone]
    new_status: str
    message: str
    device_id: int
    partition_id: Optional[int]
    partitions: List[int]
    results: List[PartitionArmResult]


class ZoneStatus(TypedDict, total=False):
    """A zone in an alarm status payload. See alarm.py ZoneStatusInfo."""

    index: int
    name: str
    is_open: bool
    is_bypassed: bool
    is_in_alarm: bool
    is_wireless: bool
    battery_low: bool
    signal_strength: Optional[int]
    tamper: bool
    friendly_name: Optional[str]


class PartitionStatus(TypedDict, total=False):
    """A partition in an alarm status payload (ISECNet index + state)."""

    index: int
    state: str


class AlarmStatus(TypedDict, total=False):
    """Real-time alarm status. See alarm.py AlarmStatusResponse."""

    arm_mode: Optional[str]
    is_armed: Optional[bool]
    is_triggered: Optional[bool]
    connection_unavailable: bool
    last_updated: Optional[str]
    partitions_enabled: Optional[bool]
    partitions: List[PartitionStatus]
    zones: List[ZoneStatus]
    # Eletrificador-only fields
    shock_enabled: Optional[bool]
    alarm_enabled: Optional[bool]
    shock_triggered: Optional[bool]
    alarm_triggered: Optional[bool]
    message: Optional[str]
