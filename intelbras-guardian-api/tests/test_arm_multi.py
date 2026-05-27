"""Unit tests for the atomic multi-partition arm endpoint.

These tests mock the ISECNet client and supporting services so they run WITHOUT
a physical alarm panel. They are intentionally kept out of
tests/integration/test_real_api.py (which requires real credentials/hardware).

Run with pytest:
    python -m pytest tests/test_arm_multi.py -v

Or standalone (no pytest required):
    python tests/test_arm_multi.py
"""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi.testclient import TestClient

from app.main import app
from app.services.isecnet_protocol import AlarmStatus

DEVICE_ID = 12345
SESSION_ID = "test-session"
HEADERS = {"X-Session-ID": SESSION_ID}
ARM_MULTI_URL = f"/api/v1/alarm/{DEVICE_ID}/arm-multi"

client = TestClient(app)


def _make_status(partitions_enabled=True, open_zone_indices=None):
    """Build a fake AlarmStatus with the given open zones."""
    open_zone_indices = set(open_zone_indices or [])
    zones = []
    for i in range(8):
        zones.append({
            "index": i,
            "open": i in open_zone_indices,
            "triggered": False,
            "state": "open" if i in open_zone_indices else "closed",
        })
    return AlarmStatus(
        model="AMT_8000",
        is_armed=False,
        arm_mode="disarmed",
        partitions_enabled=partitions_enabled,
        partitions=[{"index": 0, "state": "disarmed"}, {"index": 1, "state": "disarmed"}],
        zones=zones,
    )


def _patches(get_status_return, arm_return=(True, "Armed (away)"), conn_info=True,
             partitions_enabled_cache=True):
    """Build the standard set of patches for the alarm module singletons."""
    from app.api.v1 import alarm as alarm_mod

    # auth_service.get_valid_token -> token
    auth = patch.object(alarm_mod.auth_service, "get_valid_token",
                        new=AsyncMock(return_value="token-abc"))

    # _get_device_connection_info -> a DeviceConnectionInfo (or None)
    conn = alarm_mod.DeviceConnectionInfo(mac="AABBCCDDEEFF", use_ip_receiver=False) if conn_info else None
    conn_patch = patch.object(alarm_mod, "_get_device_connection_info",
                              new=AsyncMock(return_value=conn))

    # state_manager
    sm_pw = patch.object(alarm_mod.state_manager, "get_device_password",
                         new=AsyncMock(return_value="1234"))
    sm_set_pw = patch.object(alarm_mod.state_manager, "set_device_password",
                             new=AsyncMock(return_value=None))
    sm_pe = patch.object(alarm_mod.state_manager, "get_device_partitions_enabled",
                         new=AsyncMock(return_value=partitions_enabled_cache))
    sm_set_pe = patch.object(alarm_mod.state_manager, "set_device_partitions_enabled",
                             new=AsyncMock(return_value=None))
    sm_del = patch.object(alarm_mod.state_manager, "delete_device_state",
                          new=AsyncMock(return_value=None))
    sm_fn = patch.object(alarm_mod.state_manager, "get_all_zone_friendly_names",
                         new=AsyncMock(return_value={0: "Porta da frente"}))

    # event_stream
    es = patch.object(alarm_mod.event_stream, "broadcast_event",
                      new=AsyncMock(return_value=None))

    # isecnet_client
    ic_status = patch.object(alarm_mod.isecnet_client, "get_status",
                             new=AsyncMock(return_value=get_status_return))
    arm_mock = AsyncMock(return_value=arm_return)
    ic_arm = patch.object(alarm_mod.isecnet_client, "arm", new=arm_mock)
    ic_disc = patch.object(alarm_mod.isecnet_client, "disconnect",
                           new=AsyncMock(return_value=(True, "Disconnected")))

    return [auth, conn_patch, sm_pw, sm_set_pw, sm_pe, sm_set_pe, sm_del, sm_fn, es,
            ic_status, ic_arm, ic_disc], arm_mock


def _run(patches, fn):
    started = [p.start() for p in patches]
    try:
        return fn()
    finally:
        for p in patches:
            p.stop()


def test_success_arms_all_partitions():
    """No open zones -> all target partitions armed, single broadcast, arm called per partition."""
    status = _make_status(partitions_enabled=True, open_zone_indices=[])
    patches, arm_mock = _patches((True, status, "OK"))

    def call():
        resp = client.post(ARM_MULTI_URL, headers=HEADERS,
                           json={"partitions": [0, 1], "mode": "away", "password": "1234"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["success"] is True
        assert body["partitions"] == [0, 1]
        assert body["new_status"] == "armed_away"
        assert len(body["results"]) == 2
        assert all(r["success"] for r in body["results"])
        # arm called once per target partition
        assert arm_mock.await_count == 2
        called_indices = sorted(c.kwargs["partition_index"] for c in arm_mock.await_args_list)
        assert called_indices == [0, 1]

    _run(patches, call)
    print("PASS test_success_arms_all_partitions")


def test_open_zones_blocks_and_arms_nothing():
    """Open zone -> 400 OpenZonesError, arm NEVER called (atomic, all-or-nothing)."""
    status = _make_status(partitions_enabled=True, open_zone_indices=[0, 3])
    patches, arm_mock = _patches((True, status, "OK"))

    def call():
        resp = client.post(ARM_MULTI_URL, headers=HEADERS,
                           json={"partitions": [0, 1], "mode": "away", "password": "1234"})
        assert resp.status_code == 400, resp.text
        detail = resp.json()["detail"]
        assert detail["error"] == "OpenZonesError"
        idxs = sorted(z["index"] for z in detail["open_zones"])
        assert idxs == [0, 3]
        names = {z["index"]: z["friendly_name"] for z in detail["open_zones"]}
        assert names[0] == "Porta da frente"  # friendly name resolved
        # CRITICAL: nothing was armed
        assert arm_mock.await_count == 0

    _run(patches, call)
    print("PASS test_open_zones_blocks_and_arms_nothing")


def test_precheck_status_failure_returns_503():
    """Cannot read status (connection unavailable) -> 503 ConnectionUnavailable, nothing armed."""
    patches, arm_mock = _patches((False, AlarmStatus(), "central busy"))

    def call():
        resp = client.post(ARM_MULTI_URL, headers=HEADERS,
                           json={"partitions": [0, 1], "mode": "away", "password": "1234"})
        assert resp.status_code == 503, resp.text
        assert resp.json()["detail"]["error"] == "ConnectionUnavailable"
        assert arm_mock.await_count == 0

    _run(patches, call)
    print("PASS test_precheck_status_failure_returns_503")


def test_partitions_disabled_collapses_to_single_arm():
    """Device without partitions -> one arm call with partition_index=None."""
    status = _make_status(partitions_enabled=False, open_zone_indices=[])
    patches, arm_mock = _patches((True, status, "OK"), partitions_enabled_cache=False)

    def call():
        resp = client.post(ARM_MULTI_URL, headers=HEADERS,
                           json={"partitions": [0, 1], "mode": "away", "password": "1234"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["success"] is True
        assert arm_mock.await_count == 1
        assert arm_mock.await_args_list[0].kwargs["partition_index"] is None

    _run(patches, call)
    print("PASS test_partitions_disabled_collapses_to_single_arm")


def test_arm_connection_error_returns_503():
    """Zones clear but arm command hits a connection error -> 503."""
    status = _make_status(partitions_enabled=True, open_zone_indices=[])
    patches, arm_mock = _patches((True, status, "OK"), arm_return=(False, "central busy"))

    def call():
        resp = client.post(ARM_MULTI_URL, headers=HEADERS,
                           json={"partitions": [0], "mode": "away", "password": "1234"})
        assert resp.status_code == 503, resp.text
        assert resp.json()["detail"]["error"] == "ConnectionUnavailable"

    _run(patches, call)
    print("PASS test_arm_connection_error_returns_503")


def test_missing_password_returns_400():
    """No password provided and none saved -> 400."""
    from app.api.v1 import alarm as alarm_mod
    status = _make_status(open_zone_indices=[])
    patches, arm_mock = _patches((True, status, "OK"))
    # Override saved password lookup to return None
    pw_patch = patch.object(alarm_mod.state_manager, "get_device_password",
                            new=AsyncMock(return_value=None))

    def call():
        resp = client.post(ARM_MULTI_URL, headers=HEADERS,
                           json={"partitions": [0], "mode": "away"})
        assert resp.status_code == 400, resp.text
        assert arm_mock.await_count == 0

    _run(patches + [pw_patch], call)
    print("PASS test_missing_password_returns_400")


def test_empty_partitions_rejected_by_validation():
    """Empty partitions list -> 422 (pydantic min_length validation)."""
    status = _make_status(open_zone_indices=[])
    patches, arm_mock = _patches((True, status, "OK"))

    def call():
        resp = client.post(ARM_MULTI_URL, headers=HEADERS,
                           json={"partitions": [], "mode": "away", "password": "1234"})
        assert resp.status_code == 422, resp.text

    _run(patches, call)
    print("PASS test_empty_partitions_rejected_by_validation")


if __name__ == "__main__":
    test_success_arms_all_partitions()
    test_open_zones_blocks_and_arms_nothing()
    test_precheck_status_failure_returns_503()
    test_partitions_disabled_collapses_to_single_arm()
    test_arm_connection_error_returns_503()
    test_missing_password_returns_400()
    test_empty_partitions_rejected_by_validation()
    print("\nAll arm-multi unit tests passed.")
