#!/usr/bin/env python3
"""Offline helpers for the VITURE Luma Ultra wire format. See ../protocol.md.

Nothing here opens a USB device. It builds and reads bytes, and it summarises a
usbmon text capture that somebody else took. The one frame layout it knows is the
"V1" envelope on endpoints 0x06/0x87 and the "FA 55" envelope on 0x04/0x85, both as
published by go-viture/luma (BSD-3-Clause); the code here is our own.

    luma_wire.py frame 0x0007                 # print a V1 request as hex
    luma_wire.py decode fffd...               # read a frame from hex
    luma_wire.py summarize capture.txt --dev 7 # per-endpoint table and every command
"""
from __future__ import annotations

import argparse
import collections
import dataclasses
import struct
import sys
from typing import Iterable, Iterator, Optional

V1_PACKET = 512  # both directions, padded with zeros
V1_HEADER = 12  # pklen of an empty request
V1_REQUEST = b"\xff\xfe"
V1_REPLY = b"\xff\xfd"
MCU_MAGIC = b"\xfa\x55"


def crc16_xmodem(data: bytes) -> int:
    """CRC-16/XMODEM: poly 0x1021, init 0, no reflection, no final xor."""
    crc = 0
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def v1_request(msg: int, payload: bytes = b"") -> bytes:
    """A host-to-device frame for endpoint 0x06. Building one sends nothing."""
    if not 0 <= msg <= 0xFFFF:
        raise ValueError("message id does not fit 16 bits")
    if 18 + len(payload) > V1_PACKET:
        raise ValueError("payload does not fit one packet")
    frame = bytearray(V1_PACKET)
    frame[:2] = V1_REQUEST
    pklen = V1_HEADER + len(payload)
    frame[4:6] = struct.pack("<H", pklen)
    frame[14:16] = struct.pack("<H", msg)
    frame[18 : 18 + len(payload)] = payload
    frame[2:4] = struct.pack("<H", crc16_xmodem(bytes(frame[4 : 6 + pklen])))
    return bytes(frame)


@dataclasses.dataclass(frozen=True)
class V1Frame:
    sync: bytes
    crc_ok: Optional[bool]  # None: the capture stopped before the end of the checked range
    pklen: int
    counter: bytes  # bytes 6..13: zero on a request, device-filled on a reply
    msg: int
    status: Optional[int]  # replies only
    payload: bytes


def parse_v1(data: bytes) -> Optional[V1Frame]:
    """Read a frame, or None if it is not one. A truncated capture is still read."""
    if len(data) < 16 or data[:2] not in (V1_REQUEST, V1_REPLY):
        return None
    crc = struct.unpack_from("<H", data, 2)[0]
    pklen = struct.unpack_from("<H", data, 4)[0]
    end = 6 + pklen
    crc_ok = crc16_xmodem(data[4:end]) == crc if end <= len(data) else None
    msg = struct.unpack_from("<H", data, 14)[0]
    reply = data[:2] == V1_REPLY
    start = 19 if reply else 18
    return V1Frame(
        sync=data[:2],
        crc_ok=crc_ok,
        pklen=pklen,
        counter=data[6:14],
        msg=msg,
        status=(data[18] if reply and len(data) > 18 else None),
        payload=data[start:end],
    )


def mcu_request(command: int, payload: bytes = b"") -> bytes:
    """FA 55 frame for endpoint 0x04. That channel also carries firmware flashing:
    see "Never send" in ../protocol.md. Building one sends nothing."""
    return MCU_MAGIC + bytes([command]) + struct.pack(">H", len(payload)) + payload


@dataclasses.dataclass(frozen=True)
class McuFrame:
    command: int
    field: bytes  # bytes 3..4; the meaning in a reply is unsettled
    rest: bytes


def parse_mcu(data: bytes) -> Optional[McuFrame]:
    if len(data) < 5 or data[:2] != MCU_MAGIC:
        return None
    return McuFrame(command=data[2], field=data[3:5], rest=data[5:])


@dataclasses.dataclass(frozen=True)
class UsbEvent:
    t_us: int
    kind: str  # S submit, C complete, E error
    transfer: str  # Bi Bo Ii Io Ci Co
    bus: int
    dev: int
    ep: int
    status: str
    length: int
    data: bytes

    @property
    def address(self) -> int:
        """The USB endpoint address: 0x87 for ep 7 in."""
        return self.ep | (0x80 if self.transfer[1] == "i" else 0)


def parse_usbmon_text(lines: Iterable[str]) -> Iterator[UsbEvent]:
    """Read the text ("u") format of Documentation/usb/usbmon.rst. Other lines are
    skipped, and so are isochronous events, which are the camera and the microphone.
    The text format holds at most the first 32 bytes of each transfer."""
    for line in lines:
        words = line.split()
        if len(words) < 6 or words[2] not in ("S", "C", "E") or ":" not in words[3]:
            continue
        address = words[3].split(":")
        if len(address) != 4 or address[0][0] == "Z":
            continue
        try:
            if words[4] == "s":  # control setup: s bRT bR wValue wIndex wLength len tag
                status, length, rest = "s", int(words[10]), words[11:]
            else:
                status, length, rest = words[4], int(words[5]), words[6:]
            data = bytes.fromhex("".join(rest[1:])) if rest[:1] == ["="] else b""
            yield UsbEvent(int(words[1]), words[2], address[0], int(address[1]), int(address[2]),
                           int(address[3]), status, length, data)
        except (ValueError, IndexError):
            continue


def describe(data: bytes) -> str:
    frame = parse_v1(data)
    if frame:
        side = "reply" if frame.sync == V1_REPLY else "request"
        crc = {True: "crc ok", False: "CRC BAD", None: "crc not checkable"}[frame.crc_ok]
        status = "" if frame.status is None else f" status={frame.status}"
        return f"V1 {side} msg=0x{frame.msg:04x}{status} pklen={frame.pklen} {crc} payload={frame.payload.hex()}"
    mcu = parse_mcu(data)
    if mcu:
        return f"MCU cmd=0x{mcu.command:02x} field={mcu.field.hex()} rest={mcu.rest.hex()}"
    return data[:16].hex()


def summarize(events: Iterable[UsbEvent], dev: int) -> str:
    """One table per endpoint, then every host-to-device transfer in order. These are
    what is needed to find the stream's start command and the stream's endpoint."""
    table = collections.defaultdict(lambda: {"n": 0, "bytes": 0, "first": None, "last": None,
                                             "sizes": collections.Counter(),
                                             "heads": collections.Counter(),
                                             "status": collections.Counter()})
    commands = []
    disconnected = None
    for e in events:
        if e.dev != dev:
            continue
        outbound = e.transfer[1] == "o"
        if (outbound and e.kind == "S") or (not outbound and e.kind == "C"):
            row = table[(e.address, e.transfer)]
            row["n"] += 1
            row["bytes"] += e.length
            row["first"] = e.t_us if row["first"] is None else row["first"]
            row["last"] = e.t_us
            row["sizes"][e.length] += 1
            row["heads"][e.data[:4].hex()] += 1
            if outbound and e.ep != 0:
                commands.append(e)
        if e.kind == "C" and e.status.lstrip("-").split(":")[0] == "108":
            disconnected = disconnected or e.t_us
    out = ["endpoint  type  count  rate/s  sizes                 first bytes"]
    for (address, transfer), row in sorted(table.items()):
        span = (row["last"] - row["first"]) / 1e6
        rate = (row["n"] - 1) / span if span > 0 else 0.0
        sizes = ",".join(f"{s}x{n}" for s, n in row["sizes"].most_common(3))
        heads = ",".join(h or "-" for h, _ in row["heads"].most_common(3))
        out.append(f"0x{address:02x}      {transfer}    {row['n']:<6} {rate:<7.1f} {sizes:<21} {heads}")
    if disconnected is not None:
        out.append(f"first ESHUTDOWN (-108) at t={disconnected} us")
    out.append("")
    out.append("host to device, in order:")
    for e in commands:
        out.append(f"{e.t_us} 0x{e.address:02x} len={e.length} {describe(e.data)}")
    return "\n".join(out)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="what", required=True)
    frame = sub.add_parser("frame", help="print a V1 request as hex; nothing is sent")
    frame.add_argument("msg", type=lambda s: int(s, 0))
    frame.add_argument("payload", nargs="?", default="")
    decode = sub.add_parser("decode", help="read a frame from hex")
    decode.add_argument("hex")
    summary = sub.add_parser("summarize", help="summarise a usbmon text capture")
    summary.add_argument("capture")
    summary.add_argument("--dev", type=int, required=True, help="USB device number of the glasses")
    args = parser.parse_args(argv)
    if args.what == "frame":
        print(v1_request(args.msg, bytes.fromhex(args.payload)).rstrip(b"\0").hex())
    elif args.what == "decode":
        print(describe(bytes.fromhex(args.hex)))
    else:
        with open(args.capture, encoding="ascii", errors="replace") as capture:
            print(summarize(parse_usbmon_text(capture), args.dev))
    return 0


if __name__ == "__main__":
    sys.exit(main())
