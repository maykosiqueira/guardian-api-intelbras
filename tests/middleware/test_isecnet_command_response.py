"""A status dump answering arm/disarm must not be read as an error code.

Both shapes of reply open with the 0xE9 echo, so only the length separates
them. A refusal or an ack is 4 bytes and carries its code in response[2] -
captured from an ANM 24 Net G2: `02 e9 e4 f0` for open zones and
`02 e9 fe ea` for success. A status dump instead answers with the panel's
whole state, and there response[2] is the first zone-open bitmap byte.

Matching that byte against the response-code table invents failures: the zone
pattern 0xE7 (zones 1,2,3,6,7,8 open) reads as DEACTIVATION_DENIED, so arm and
disarm both report "Deactivation denied" while the panel obeys the command.
The dump was only recognised at exactly 46 bytes, but the ANM 24 Net G2 sends
56 - on that panel every reply took the wrong path.
"""
from app.services.isecnet_protocol import ISECNetProtocol


def _partial_status_frame(open_zone_indices=()):
    """46-byte V1 partial status: [size=44][data 44][checksum]."""
    data = bytearray(44)
    data[0] = 0xE9  # command echo
    for idx in open_zone_indices:  # zone bitmap lives in data[1:7]
        data[1 + idx // 8] |= 1 << (idx % 8)
    data[19] = 37  # ANM_24_NET_G2
    body = [44] + list(data)
    chk = 0
    for b in body:
        chk ^= b
    return bytes(body + [chk ^ 0xFF])


def _parse_command(frame):
    return ISECNetProtocol()._parse_isecv1_command_response(frame)


def test_open_zones_do_not_fake_a_command_error():
    # zones 0,1,2,5,6,7 open -> response[2] == 0xE7 == DEACTIVATION_DENIED
    frame = _partial_status_frame(open_zone_indices=(0, 1, 2, 5, 6, 7))
    assert len(frame) == 46 and frame[2] == 0xE7
    assert _parse_command(frame) == (True, "OK")


def test_longer_status_echo_is_not_read_as_an_error_code():
    frame = _partial_status_frame(open_zone_indices=(0, 1, 2, 5, 6, 7)) + bytes(10)
    assert len(frame) == 56 and frame[2] == 0xE7
    assert _parse_command(frame) == (True, "OK")


def test_a_refusal_from_the_panel_still_fails():
    # Real frame from an ANM 24 Net G2 refusing to arm with a zone open.
    assert _parse_command(bytes.fromhex("02e9e4f0")) == (False, "Open zones")


def test_an_ack_from_the_panel_still_succeeds():
    # Real frame from the same panel accepting the arm command.
    assert _parse_command(bytes.fromhex("02e9feea")) == (True, "OK")
