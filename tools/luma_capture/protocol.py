"""Names for what is on the wire.

Everything standard (control requests, hub port status, URB status codes) is decoded for certain: it comes
from the USB spec and the kernel's include/uapi/linux/usb/ch11.h.

The glasses' two command envelopes are decoded by decode_frame, from spike/driver/docs/protocol.md on branch
drv-protocol-research, which takes them from go-viture/luma (BSD-3-Clause): the V1 frame on 0x06/0x87 and the
FA 55 frame on 0x04/0x85. The V1 CRC lets a capture prove that layout on the wire. If it does not fit,
infer_framing() looks for a MsgID from behaviour instead, and a layout can be given with Framing.parse().

The IMU stream's layout is not public at all: see streams.py.
"""
from collections import Counter
from dataclasses import dataclass
import re
import struct

from .capfile import F_POLICY_DROPPED, F_SETUP, XFER_CTRL, XFER_INT, XFER_ISO

GET_DISPLAY_MODE = 0x0007    # named by the SDK's own error line in driver.log

V1_REQUEST, V1_REPLY, MCU_MAGIC = b'\xff\xfe', b'\xff\xfd', b'\xfa\x55'
V1_HEADER = 18               # sync 2, CRC 2, pklen 2, counter 8, MsgID 2, reserved 2
MESSAGES = {0x0001: 'firmware version', 0x0007: 'get_display_mode', 0x000b: 'DP version', 0x0010: 'board serial',
            0x0011: 'package serial', 0x0063: 'OSD version', 0x100b: 'enable status reports'}

STATUS_NAMES = {0: 'ok', -2: 'cancelled(ENOENT)', -19: 'ENODEV', -32: 'stall(EPIPE)', -62: 'ETIME', -71: 'EPROTO',
                -75: 'EOVERFLOW', -84: 'EILSEQ', -104: 'cancelled(ECONNRESET)', -108: 'ESHUTDOWN', -110: 'ETIMEDOUT',
                -115: 'pending'}
GONE = frozenset({-19, -108})           # the device is no longer there
CANCELLED = frozenset({-2, -104})       # what a libusb timeout looks like: the host gave up on the transfer

PORT_STATUS = {0x0001: 'connected', 0x0002: 'enabled', 0x0004: 'suspended', 0x0008: 'over-current', 0x0010: 'reset',
               0x0100: 'power', 0x0400: 'high-speed'}
PORT_CHANGE = {0x0001: 'connection', 0x0002: 'enable', 0x0004: 'suspend', 0x0008: 'over-current', 0x0010: 'reset'}
PORT_FEATURES = {1: 'ENABLE', 2: 'SUSPEND', 4: 'RESET', 8: 'POWER', 16: 'C_CONNECTION', 17: 'C_ENABLE',
                 18: 'C_SUSPEND', 19: 'C_OVER_CURRENT', 20: 'C_RESET'}
DESCRIPTORS = {1: 'device', 2: 'config', 3: 'string', 6: 'qualifier', 15: 'bos', 0x29: 'hub'}
STANDARD = {0: 'GET_STATUS', 1: 'CLEAR_FEATURE', 3: 'SET_FEATURE', 5: 'SET_ADDRESS', 6: 'GET_DESCRIPTOR',
            9: 'SET_CONFIGURATION', 11: 'SET_INTERFACE'}

STREAM_MIN_HZ = 50          # an IN endpoint must be this busy to be a stream
STREAM_OVER_COMMANDS = 3    # and must outnumber every command sent by this much, or it may just be replies
MAX_MSGIDS = 64             # a real MsgID field repeats a small set; timestamps and checksums do not
SCAN_BYTES = 32
ECHO_WINDOW_NS = 200_000_000
CLEAR_ECHO = 0.5            # below this share of replies repeating the id, an inferred field is only a guess
DISCONNECT = re.compile(r'usb (\d+-[\d.]+): USB disconnect')


def status_name(status):
    return STATUS_NAMES.get(status, f'errno {status}')


def bit_names(value, table):
    return [name for bit, name in table.items() if value & bit]


def parse_setup(setup):
    """(bmRequestType, bRequest, wValue, wIndex, wLength)."""
    return struct.unpack('<BBHHH', setup)


def describe_control(setup):
    request_type, request, value, index, length = parse_setup(setup)
    category = request_type >> 5 & 3
    if category == 1 and request_type & 0x1f == 3:     # class request to a hub port
        if request == 0:
            return f'hub GET_PORT_STATUS port {index}'
        if request in (1, 3):
            verb = 'CLEAR' if request == 1 else 'SET'
            return f'hub {verb} {PORT_FEATURES.get(value, f"feature {value}")} port {index}'
    if category == 0:
        name = STANDARD.get(request, f'request {request}')
        if request == 6:
            return f'{name} {DESCRIPTORS.get(value >> 8, f"type {value >> 8}")}'
        if request == 1 and value == 0 and request_type & 0x1f == 2:
            return f'CLEAR_HALT ep {index:#04x}'     # the host clearing a stalled endpoint
        return f'{name} value={value}' if request in (5, 9, 11) else name
    prefix = {1: 'class', 2: 'vendor'}.get(category, 'reserved')
    return f'{prefix} request {request:#04x} value={value:#06x} index={index:#06x} len={length}'


@dataclass(frozen=True, slots=True)
class Framing:
    """Where the MsgID sits in a command. magic is what every command starts with, possibly nothing."""
    offset: int
    endian: str = 'little'
    magic: bytes = b''
    source: str = 'given'

    def msgid(self, data):
        if not data.startswith(self.magic) or len(data) < self.offset + 2:
            return None
        return int.from_bytes(data[self.offset:self.offset + 2], self.endian)

    def spec(self):
        return f'offset={self.offset},endian={self.endian},magic={self.magic.hex()}'

    @classmethod
    def parse(cls, text):
        """From 'offset=14,endian=little,magic=fffe'; only offset is required."""
        fields = dict(part.split('=', 1) for part in text.split(','))
        unknown = set(fields) - {'offset', 'endian', 'magic'}
        if unknown or 'offset' not in fields:
            raise ValueError(f'bad framing {text!r}: want offset=N[,endian=little|big][,magic=hex]')
        endian = fields.get('endian', 'little')
        if endian not in ('little', 'big'):
            raise ValueError(f'bad endian {endian!r}')
        return cls(int(fields['offset'], 0), endian, bytes.fromhex(fields.get('magic', '')))


@dataclass(frozen=True, slots=True)
class Candidate:
    framing: Framing
    packets: int        # commands holding MsgID 0x0007 at this position
    distinct: int       # how many different values the field takes
    echo: float         # share of replies carrying the same value at the same place


@dataclass(frozen=True, slots=True)
class Inference:
    framing: Framing | None    # None when the capture does not settle it
    candidates: tuple
    commands: int

    @property
    def verdict(self):
        if self.framing is None:
            return 'undetermined' if self.candidates else 'not seen'
        return self.framing.source


def _common_prefix(payloads, limit=4):
    first = payloads[0]
    size = min(limit, min(map(len, payloads)))
    for index in range(size):
        if any(data[index] != first[index] for data in payloads):
            return first[:index]
    return first[:size]


def _value(data, offset, endian):
    return int.from_bytes(data[offset:offset + 2], endian) if len(data) >= offset + 2 else None


def infer_framing(samples, target=GET_DISPLAY_MODE):
    """Finds the 16 bit field that behaves like a MsgID in (t_ns, is_in, payload) samples, in time order.

    A candidate must hold target somewhere and take few values, which rules out clocks and checksums, and
    the deciding evidence is that replies repeat it: the SDK's call is execute_usb_command_with_response.
    Without that evidence the best field is reported as a candidate only, so a report never prints a count
    for a field that was merely plausible.
    """
    commands = [data for _, is_in, data in samples if not is_in and data]
    if not commands:
        return Inference(None, (), 0)
    replies = []    # (command, reply) pairs: the first IN payload after a command, if it came soon enough
    waiting = None
    for t_ns, is_in, data in samples:
        if not is_in:
            waiting = (t_ns, data)
        elif waiting and data:
            if t_ns - waiting[0] <= ECHO_WINDOW_NS:
                replies.append((waiting[1], data))
            waiting = None
    magic = _common_prefix(commands)
    found = []
    for offset in range(len(magic), SCAN_BYTES - 1):
        for endian in ('little', 'big'):
            values = Counter(_value(data, offset, endian) for data in commands)
            values.pop(None, None)
            if not values[target] or len(values) > MAX_MSGIDS:
                continue
            paired = [(a, b) for a, b in replies if _value(a, offset, endian) is not None]
            echo = sum(_value(a, offset, endian) == _value(b, offset, endian) for a, b in paired) / len(paired) \
                if paired else 0.0
            found.append(Candidate(Framing(offset, endian, magic, 'inferred'), values[target], len(values), echo))
    # A field that never changes across varied commands is a version or flag byte, so those rank last. Little
    # endian wins a tie only because the tie must break somewhere.
    found.sort(key=lambda c: (c.distinct > 1, c.echo, c.packets, c.framing.endian == 'little'), reverse=True)
    best = found[0] if found else None
    if best and best.distinct > 1 and best.echo >= CLEAR_ECHO:
        rivals = [c for c in found[1:] if c.distinct > 1 and c.echo >= best.echo and c.packets == best.packets]
        return Inference(None if rivals else best.framing, tuple(found[:5]), len(commands))
    return Inference(None, tuple(found[:5]), len(commands))


def crc16_xmodem(data):
    """Poly 0x1021, init 0, no reflection, no final xor."""
    crc = 0
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = (crc << 1 ^ 0x1021) & 0xffff if crc & 0x8000 else crc << 1 & 0xffff
    return crc


@dataclass(frozen=True, slots=True)
class Frame:
    """One command-channel frame. crc_ok is None for FA 55 frames, which carry no checksum."""
    key: str              # what to count it as: 'MsgID 0x0007', 'MCU 0x80'
    name: str             # for people: 'MsgID 0x0007 (get_display_mode)'
    msgid: int | None
    is_reply: bool
    crc_ok: bool | None
    status: int | None = None


def decode_frame(data):
    """The frame in a transfer's payload, or None if it is not a documented envelope."""
    if data[:2] in (V1_REQUEST, V1_REPLY) and len(data) >= 16:
        crc, length = struct.unpack_from('<HH', data, 2)
        msgid = struct.unpack_from('<H', data, 14)[0]
        end = 6 + length
        ok = end <= len(data) and crc16_xmodem(data[4:end]) == crc
        reply = data[:2] == V1_REPLY
        known = f' ({MESSAGES[msgid]})' if msgid in MESSAGES else ''
        status = data[18] if reply and len(data) > 18 else None
        return Frame(f'MsgID {msgid:#06x}', f'MsgID {msgid:#06x}{known}', msgid, reply, ok, status)
    if data[:2] == MCU_MAGIC and len(data) >= 5:
        return Frame(f'MCU {data[2]:#04x}', f'MCU {data[2]:#04x}', None, False, None)
    return None


@dataclass(frozen=True, slots=True)
class Label:
    kind: str     # hub, control, command, sent, reply, stream, read, device, companion
    text: str
    msgid: int | None = None


class Labeler:
    """Names records in stream order. It holds control setups so a completion can use its request."""

    def __init__(self, framing=None, stream_eps=()):
        self.framing = framing
        self.stream_eps = frozenset(stream_eps)
        self._setups = {}

    def command_key(self, record):
        """What this host-to-glasses transfer is, for counting: a MsgID when known, else its shape."""
        if record.xfer == XFER_CTRL:
            return f'control request {record.setup[1]:#04x}', None
        msgid = self.framing.msgid(record.data) if self.framing else None
        if msgid is not None:
            return f'MsgID {msgid:#06x}', msgid
        frame = decode_frame(record.data)
        if frame:
            return frame.key, frame.msgid
        return f'ep{record.ep:02x} len={record.urb_len} {record.data[:2].hex() or "-"}', None

    def label(self, record):
        if record.xfer == XFER_CTRL:
            return self._control(record)
        if record.companion:
            return Label('companion', f'companion ep{record.ep:02x} len={record.urb_len}')
        if not record.glasses:
            return self._device(record)
        if record.is_in:
            if record.event == 'S':
                return Label('read', f'read posted ep{record.ep:02x} ({record.urb_len} bytes)')
            if record.ep in self.stream_eps:
                return Label('stream', f'stream ep{record.ep:02x} len={record.urb_len}')
            msgid = self.framing.msgid(record.data) if self.framing else None
            if msgid is not None:
                return Label('reply', f'reply MsgID {msgid:#06x} ep{record.ep:02x} len={record.urb_len}', msgid)
            frame = decode_frame(record.data)
            if frame:
                text = f'{"reply" if frame.is_reply else "frame"} {frame.name} ep{record.ep:02x}'
                text += '' if frame.status in (None, 0) else f' status={frame.status}'
                return Label('reply', text + ('' if frame.crc_ok is not False else ' BAD CRC'), frame.msgid)
            return Label('reply', f'data ep{record.ep:02x} len={record.urb_len}')
        if record.event != 'S':
            return Label('sent', f'sent ep{record.ep:02x}')
        key, msgid = self.command_key(record)
        frame = decode_frame(record.data) if record.xfer != XFER_CTRL else None
        if frame:
            return Label('command', 'command ' + frame.name + ('' if frame.crc_ok is not False else ' BAD CRC'), msgid)
        return Label('command', ('command ' if msgid is not None else 'OUT ') + key, msgid)

    def incoming_key(self, record):
        """What this non-stream data from the glasses is, for counting."""
        frame = decode_frame(record.data)
        return frame.key if frame else f'ep{record.ep:02x} len={record.urb_len} {record.data[:2].hex() or "-"}'

    def _device(self, record):
        kind = {XFER_ISO: 'iso', XFER_INT: 'interrupt'}.get(record.xfer, 'bulk')
        note = ' (payload not kept)' if record.flags & F_POLICY_DROPPED else ''
        if record.xfer == XFER_INT and record.is_in and record.event == 'C' and record.data:
            return Label('hub', f'dev{record.dev} status change bitmap {record.data.hex()}')
        return Label('device', f'dev{record.dev} {kind} ep{record.ep:02x}{note}')

    def _control(self, record):
        if record.flags & F_SETUP:
            self._setups[record.urb_id] = record.setup
            setup = record.setup
        else:
            setup = self._setups.pop(record.urb_id, None)
        if setup is None:
            return Label('control', f'dev{record.dev} control completed')
        text = describe_control(setup)
        kind = 'hub' if text.startswith('hub ') else 'control'
        if record.event == 'C' and text.startswith('hub GET_PORT_STATUS') and len(record.data) == 4:
            status, change = struct.unpack('<HH', record.data)
            flags = ','.join(bit_names(status, PORT_STATUS)) or 'nothing'
            changed = ','.join(bit_names(change, PORT_CHANGE)) or 'none'
            text += f' -> {flags}; changed: {changed}'
        return Label(kind, f'dev{record.dev} {text}')


def port_of(path):
    """The port the whole glasses assembly hangs off: 5-2.1 is the glasses behind hub 5-2."""
    return path.split('.', 1)[0] if path else None


def kernel_disconnect(text, port=None):
    """True when a kernel line says this assembly (or, with no port known, anything) left the bus."""
    match = DISCONNECT.search(text)
    if not match:
        return False
    return port is None or match[1] == port or match[1].startswith(port + '.')


def confirms_drop(signal):
    """A failed transfer alone proves nothing: the kernel flushes endpoints with ESHUTDOWN on a mere
    SET_CONFIGURATION or SET_INTERFACE. Only the kernel log or sysfs can say the device really left."""
    return not signal.startswith('transfer failed')


def drop_signal(record, port=None):
    """Why this record suggests the glasses left the bus, or None. See confirms_drop."""
    if record.is_note:
        note = record.note
        if note.get('type') == 'glasses_gone':
            return 'gone from sysfs' + (f": {note['why']}" if note.get('why') else '')
        if note.get('type') == 'kernel' and kernel_disconnect(note.get('text', ''), port):
            return 'kernel: ' + note['text']
        return None
    if record.glasses and record.event == 'C' and record.status in GONE:
        return f'transfer failed {status_name(record.status)} on ep{record.ep:02x}'
    return None
