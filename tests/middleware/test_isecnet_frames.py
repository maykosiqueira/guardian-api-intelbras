"""A V1 status frame split across TCP segments must not become an empty status.

2026-08-25 15:22:09: `_send_and_receive` returned a single byte (the frame's
size byte), `_parse_isecv1_status_response` logged "invalid or too short"
and returned an empty AlarmStatus(), and `get_status` reported it as a
SUCCESS. The endpoint then answered HTTP 200 with zero zones and Home
Assistant showed every open zone as closed and partition B as unknown.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

from app.services.isecnet_protocol import ISECNetProtocol


def _partial_status_frame(open_zone_indices=(26, 38)):
    """46-byte V1 partial status: [size=44][data 44][checksum]."""
    data = bytearray(44)
    data[0] = 0xE9  # command echo
    for idx in open_zone_indices:  # zone bitmap lives in data[1:7]
        data[1 + idx // 8] |= 1 << (idx % 8)
    data[19] = 0x34  # AMT_2018_E_SMART
    data[20] = 0x10  # firmware
    data[21] = 0x01  # partitions enabled
    data[22] = 0x00  # nothing armed
    body = [44] + list(data)
    x = 0
    for b in body:
        x ^= b
    return bytes(body + [x ^ 0xFF])


def _protocol(chunks, gap=0.02):
    proto = ISECNetProtocol()
    proto._is_v1 = True
    proto.is_connected = True
    proto.is_authenticated = True
    proto._password = "1234"
    proto._model_code = 52
    proto.reader = asyncio.StreamReader()
    proto.writer = MagicMock()
    proto.writer.drain = AsyncMock()

    async def feed():
        for chunk in chunks:
            await asyncio.sleep(gap)
            proto.reader.feed_data(chunk)

    return proto, feed


def _get_status(chunks):
    async def run():
        proto, feed = _protocol(chunks)
        feeder = asyncio.create_task(feed())
        try:
            return await proto.get_status()
        finally:
            await feeder

    return asyncio.run(run())


def _open_indices(status):
    return sorted(z["index"] for z in status.zones if z.get("open"))


def test_whole_frame_parses():
    ok, status = _get_status([_partial_status_frame()])
    assert ok is True and status.is_valid is True
    assert _open_indices(status) == [26, 38]


def test_frame_split_in_three_segments_is_reassembled():
    frame = _partial_status_frame()
    ok, status = _get_status([frame[:1], frame[1:30], frame[30:]])
    assert ok is True and status.is_valid is True
    assert _open_indices(status) == [26, 38]


def test_lonely_size_byte_is_a_failure_not_an_empty_status():
    async def run():
        proto, feed = _protocol([bytes([44])])
        feeder = asyncio.create_task(feed())
        # Don't wait the full 2 s completion window in the test suite.
        orig = proto._complete_v1_frame
        proto._complete_v1_frame = lambda response, timeout=0.1: orig(response, timeout=timeout)
        try:
            return await proto.get_status()
        finally:
            await feeder

    ok, status = asyncio.run(run())
    assert ok is False, "a truncated reply must be reported as a failure"
    assert status.is_valid is False
    assert status.zones == []


def test_complete_frame_leaves_a_following_frame_in_the_buffer():
    """Reading exactly size+2 bytes must not swallow the next reply."""
    frame = _partial_status_frame()
    nxt = _partial_status_frame(open_zone_indices=(0,))

    async def run():
        proto, feed = _protocol([frame[:5], frame[5:] + nxt])
        feeder = asyncio.create_task(feed())
        ok, status = await proto.get_status()
        await feeder
        leftover = await asyncio.wait_for(proto.reader.read(1024), timeout=0.2)
        return ok, status, leftover

    ok, status, leftover = asyncio.run(run())
    assert ok is True and _open_indices(status) == [26, 38]
    assert leftover == nxt


def test_v2_mode_is_untouched():
    proto = ISECNetProtocol()
    proto._is_v1 = False
    proto._is_ip_receiver = False
    assert asyncio.run(proto._complete_v1_frame(b"\x2c")) == b"\x2c"
