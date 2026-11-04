"""The expected bytes below are not computed by this code. The requests are the frames
VITURE's own web updater emits and the replies are what a real Luma Ultra answered, both
as recorded in go-viture/luma info_test.go (cc37e96) and frame.go. See ../sources.md."""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import luma_wire as w  # noqa: E402

VENDOR_REQUESTS = {
    0x0001: "fffe7a600c0000000000000000000100",
    0x0010: "fffe690d0c0000000000000000001000",
    0x0011: "fffedd7b0c0000000000000000001100",
    0x000b: "fffed1080c0000000000000000000b00",
    0x0063: "fffec0d40c0000000000000000006300",
}
REPLY_FIRMWARE = "fffdaf0f210000000000763e42000100000000" + "31322e302e30312e3130315f3230323630363035"
REPLY_BOARD = "fffd097a210000000000773e42001000000000" + "503653504e483534373031313332000000000000"


@pytest.mark.parametrize("msg,expected", VENDOR_REQUESTS.items())
def test_request_matches_the_vendors_own_bytes(msg, expected):
    frame = w.v1_request(msg)
    assert frame[:16].hex() == expected
    assert len(frame) == 512
    assert frame[16:] == bytes(496)


@pytest.mark.parametrize("raw,msg,text", [
    (REPLY_FIRMWARE, 0x0001, b"12.0.01.101_20260605"),
    (REPLY_BOARD, 0x0010, b"P6SPNH54701132"),
])
def test_reply_from_a_real_headset(raw, msg, text):
    frame = w.parse_v1(bytes.fromhex(raw))
    assert frame.crc_ok is True  # the crc is the device's own, so this judges our range and polynomial
    assert frame.msg == msg
    assert frame.status == 0
    assert frame.payload.rstrip(b"\0") == text


def test_corrupt_reply_fails_the_crc():
    raw = bytearray.fromhex(REPLY_FIRMWARE)
    raw[25] ^= 1
    assert w.parse_v1(bytes(raw)).crc_ok is False


def test_truncated_capture_reads_but_cannot_check():
    frame = w.parse_v1(bytes.fromhex(REPLY_FIRMWARE)[:32])
    assert frame.msg == 1 and frame.crc_ok is None


@pytest.mark.parametrize("raw", ["", "fffe", "00" * 64, "fffc" + "00" * 62])
def test_not_a_v1_frame(raw):
    assert w.parse_v1(bytes.fromhex(raw)) is None


def test_get_display_mode_frame_has_the_logged_shape():
    # The driver log says: MsgID 0x0007, PayloadLen 0, DataLen 12, PacketLen 512.
    frame = w.parse_v1(w.v1_request(0x0007))
    assert (frame.msg, frame.pklen, frame.crc_ok, frame.payload) == (7, 12, True, b"")


def test_one_byte_payload_has_the_logged_shape():
    # The driver log says: MsgID 0x100B, PayloadLen 1, DataLen 13, PacketLen 512.
    frame = w.parse_v1(w.v1_request(0x100b, b"\x01"))
    assert (frame.pklen, frame.payload, frame.crc_ok) == (13, b"\x01", True)


def test_request_limits():
    with pytest.raises(ValueError):
        w.v1_request(0x10000)
    with pytest.raises(ValueError):
        w.v1_request(1, bytes(495))
    assert len(w.v1_request(1, bytes(494))) == 512


def test_mcu_frames_from_a_real_headset():
    assert w.mcu_request(0x80).hex() == "fa55800000"
    assert w.mcu_request(0xc3).hex() == "fa55c30000"
    assert w.mcu_request(0x01, b"\xaa\xbb").hex() == "fa5501" + "0002" + "aabb"  # big-endian on the way out
    reply = w.parse_mcu(bytes.fromhex("fa55800000020109"))
    assert (reply.command, reply.field, reply.rest) == (0x80, b"\0\0", b"\x02\x01\x09")
    assert w.parse_mcu(bytes.fromhex("fa55c3")) is None


CAPTURE = """\
ffff0001 1000000 S Bo:5:007:6 -115 512 = fffe7a60 0c000000 00000000 00000100 00000000 00000000 00000000 00000000
ffff0001 1000100 C Bo:5:007:6 0 512 >
ffff0002 1000200 S Bi:5:007:7 -115 512 <
ffff0002 1000900 C Bi:5:007:7 0 39 = fffdaf0f 21000000 0000763e 42000100 00000031 322e302e 30312e31 30315f32
ffff0003 1001000 S Ii:5:007:3 -115:8 512 <
ffff0003 1002000 C Ii:5:007:3 0:8 64 = aabbccdd 00000000
ffff0004 1003000 C Ii:5:007:3 0:8 64 = aabbccdd 00000001
ffff0005 1004000 C Ii:5:007:3 0:8 64 = aabbccdd 00000002
ffff0006 1000000 C Ii:5:002:1 0:1 8 = 00000400 00000000
ffff0007 1005000 S Ci:5:007:0 s 80 06 0100 0000 0012 18 <
ffff0008 1006000 C Zi:5:009:1 -18:8:0 1 -18:0:0 3 = 00
ffff0009 1007000 C Bo:5:007:4 -108 0 >
garbage line
"""


def test_usbmon_text_parser():
    events = list(w.parse_usbmon_text(CAPTURE.splitlines()))
    assert len(events) == 11  # the isochronous and garbage lines are gone
    first = events[0]
    assert (first.t_us, first.kind, first.transfer, first.bus, first.dev, first.ep) == (1000000, "S", "Bo", 5, 7, 6)
    assert first.address == 0x06 and first.length == 512 and first.data[:4] == b"\xff\xfe\x7a\x60"
    assert events[3].address == 0x87 and events[3].length == 39
    assert events[4].status == "-115:8"
    assert events[9].transfer == "Ci" and events[9].length == 18 and events[9].status == "s"


def test_summary_keeps_to_the_device_and_names_commands():
    text = w.summarize(w.parse_usbmon_text(CAPTURE.splitlines()), dev=7)
    assert "V1 request msg=0x0001 pklen=12 crc ok" in text
    assert "0x83" in text and "1000.0" in text  # three reports a millisecond apart
    assert "first ESHUTDOWN (-108) at t=1007000 us" in text
    assert "00000400" not in text  # another device's reports must never reach the output
    assert "0x81" not in text


def test_cli(capsys, tmp_path):
    assert w.main(["frame", "0x0001"]) == 0
    assert capsys.readouterr().out.strip() == "fffe7a600c0000000000000000000" + "1"
    assert w.main(["decode", REPLY_FIRMWARE]) == 0
    assert "V1 reply msg=0x0001 status=0" in capsys.readouterr().out
    capture = tmp_path / "c.txt"
    capture.write_text(CAPTURE)
    assert w.main(["summarize", str(capture), "--dev", "7"]) == 0
    assert "host to device" in capsys.readouterr().out
