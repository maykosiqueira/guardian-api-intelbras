"""A panel with no partitions must still report that it is armed.

`_get_max_partitions_for_model` mirrors the APK's getPartitionMaxCount(), which
answers 0 for the ANM 24 Net, the ANM 24 Net G2 and the AMT 1000 Smart - those
panels have no partitions to count. The parser looped over that number, so for
them the loop never ran, `status.partitions` stayed empty and `arm_mode` kept
its "disarmed" default no matter how the panel was set.

Home Assistant showed the arm for the 15 s of its optimistic window and then
fell back to Desarmado while the Guardian app kept showing Armado - and, worse,
its disarm button short-circuits on "already disarmed", so the panel could not
be disarmed from Home Assistant at all.

The two frames at the bottom were captured from a real ANM 24 Net G2.
"""
from app.services.isecnet_protocol import ISECNetProtocol

ANM_24_NET_G2 = 37
AMT_2018_E_SMART = 52


def _partial_status_frame(*, model=ANM_24_NET_G2, partitions_enabled=0,
                          armed_byte=0x00, open_zone_indices=()):
    """46-byte V1 partial status: [size=44][data 44][checksum]."""
    data = bytearray(44)
    data[0] = 0xE9  # command echo
    for idx in open_zone_indices:  # zone bitmap lives in data[1:7]
        data[1 + idx // 8] |= 1 << (idx % 8)
    data[19] = model
    data[20] = 0x10  # firmware
    data[21] = partitions_enabled
    data[22] = armed_byte
    return _framed(data)


def _framed(data):
    body = [len(data)] + list(data)
    chk = 0
    for b in body:
        chk ^= b
    return bytes(body + [chk ^ 0xFF])


def _parse(frame):
    return ISECNetProtocol()._parse_isecv1_status_response(frame)


def test_panel_without_partitions_reports_armed():
    status = _parse(_partial_status_frame(armed_byte=0x01))
    assert status.is_armed is True
    assert status.arm_mode == "armed_away"


def test_panel_without_partitions_reports_disarmed():
    status = _parse(_partial_status_frame(armed_byte=0x00))
    assert status.is_armed is False
    assert status.arm_mode == "disarmed"


def test_partitioned_model_still_reads_every_partition():
    status = _parse(_partial_status_frame(
        model=AMT_2018_E_SMART, partitions_enabled=1, armed_byte=0x02
    ))
    assert [p["armed"] for p in status.partitions] == [False, True]
    assert status.arm_mode == "armed_away"


# Captured 2026-08-29 from an ANM 24 Net G2 over the Cloud V1 connection. The
# panel was armed at 16:34:44 (the cloud logged "Ativação do Usuário" and the
# arm command was acked with 0xFE); data[22] went 0x00 -> 0x03 1.7 s later and
# stayed there, while the middleware answered "disarmed" 35 polls in a row.
_REAL_DISARMED = (
    "e900000000000000000000000000000000000025000100001335290826"
    "00000000000000000000000000000001000302ff1f00000000"
)
_REAL_ARMED = (
    "e900000000000000000000000000000000000025000103001335290826"
    "00000000000000000000000000000001000302ff1f00000000"
)


def test_real_anm_24_net_g2_frames():
    disarmed = _parse(_framed(bytes.fromhex(_REAL_DISARMED)))
    armed = _parse(_framed(bytes.fromhex(_REAL_ARMED)))
    assert disarmed.model == armed.model == "ANM_24_NET_G2"
    assert (disarmed.is_armed, disarmed.arm_mode) == (False, "disarmed")
    assert (armed.is_armed, armed.arm_mode) == (True, "armed_away")
