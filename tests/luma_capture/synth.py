"""Made-up captures with a known answer.

By default the framing is invented, so the inference must find it from behaviour alone: tests vary it, and
nothing in tools/ may share those numbers. With v1=True the commands are real documented frames, which is what the
glasses are expected to send. The IMU stream layout is invented either way, because nobody knows the real one.
"""
import math
import random
import struct

from tools.luma_capture import capfile
from tools.luma_capture.capfile import F_GLASSES, F_SETUP, Record, XFER_BULK, XFER_CTRL, XFER_INT, note_record
from tools.luma_capture.protocol import crc16_xmodem

T0 = 1_762_000_000_000_000_000
MS = 1_000_000
S = 1_000_000_000
GLASSES_DEV = 7
ROOT_HUB = 1


def v1_frame(msgid, *, reply=False, payload=b'', bad_crc=False):
    body = bytearray(512)
    length = 12 + len(payload)
    body[4:6] = struct.pack('<H', length)
    body[14:16] = struct.pack('<H', msgid)
    body[18:18 + len(payload)] = payload
    crc = crc16_xmodem(bytes(body[4:6 + length])) ^ (0xffff if bad_crc else 0)
    body[:2] = b'\xff\xfd' if reply else b'\xff\xfe'
    body[2:4] = struct.pack('<H', crc)
    return bytes(body)


def imu_packet(index, *, g=1.0, period_us=1000, rng=random, samples=1, axis=1):
    """An invented IMU frame: magic, a microsecond clock, gyro floats, then a gravity vector that wobbles a little."""
    out = b''
    for sample in range(samples):
        when = (index * samples + sample) * period_us
        tilt = 0.05 + rng.random() * 0.002
        major, minor, rest = g * math.cos(tilt) + rng.random() * 1e-4, g * math.sin(tilt), rng.random() * 1e-4
        accel = {0: (major, minor, rest), 1: (minor, major, rest), 2: (rest, minor, major)}[axis]
        gyro = tuple(rng.random() * 1e-3 for _ in range(3))
        out += b'\xc5\x3a' + struct.pack('<I', when & 0xffffffff) + struct.pack('<6f', *gyro, *accel)
    return out


class Builder:
    def __init__(self, offset=14, endian='little', echo=True, seed=1, v1=False):
        self.offset, self.endian, self.echo, self.v1 = offset, endian, echo, v1
        self.rng = random.Random(seed)
        self.records = []
        self.urb = 0x1000

    def _id(self):
        self.urb += 1
        return self.urb

    def note(self, t_ns, **fields):
        self.records.append(note_record(t_ns, **fields))

    def devices(self, t_ns, port='5-2'):
        self.note(t_ns, type='device_map', port=port, devices=[])

    def payload(self, msgid, size=64):
        data = bytearray(self.rng.randbytes(size))
        data[:2] = b'\xab\xcd'
        data[self.offset:self.offset + 2] = msgid.to_bytes(2, self.endian)
        return bytes(data)

    def usb(self, t_ns, event, ep, data=b'', *, status=0, urb_id=None, xfer=XFER_BULK, dev=GLASSES_DEV, length=None,
            setup=None, flags=None):
        urb_id = self._id() if urb_id is None else urb_id
        if flags is None:
            flags = F_GLASSES if dev == GLASSES_DEV else 0
        flags |= F_SETUP if setup else 0
        self.records.append(Record(t_ns, capfile.KIND_USB, event, xfer, ep, urb_id, 5, dev, flags, status,
                                   len(data) if length is None else length, setup or bytes(8), data))
        return urb_id

    def command(self, t_ns, msgid, *, complete=True):
        out_ep, in_ep = (0x06, 0x87) if self.v1 else (0x04, 0x82)
        data = v1_frame(msgid) if self.v1 else self.payload(msgid)
        urb_id = self.usb(t_ns, 'S', out_ep, data, status=-115)
        if complete:
            self.usb(t_ns + 200_000, 'C', out_ep, urb_id=urb_id, length=len(data))
            if self.v1:
                reply = v1_frame(msgid, reply=True, payload=b'\0ok')
            else:
                reply = self.payload(msgid if self.echo else self.rng.randrange(0x4000, 0x8000), 128)
            read = self.usb(t_ns, 'S', in_ep, status=-115, length=512)
            self.usb(t_ns + 2 * MS, 'C', in_ep, reply, urb_id=read)
        return urb_id

    def stream(self, start, stop, *, ep=0x81, period=MS, xfer=XFER_INT, make=None, jitter=None, queued=False,
               dev=GLASSES_DEV):
        """Interrupt transfers. With queued, a second read is always waiting, as the zero-drop rules ask."""
        t_ns = start
        index = 0
        mine = dict(xfer=xfer, dev=dev, flags=F_GLASSES)
        spare = self.usb(t_ns - period, 'S', ep, status=-115, length=1024, **mine) if queued else None
        while t_ns < stop:
            read = self.usb(t_ns - period // 2, 'S', ep, status=-115, length=1024, **mine)
            data = make(index) if make else self.rng.randbytes(60)
            self.usb(t_ns, 'C', ep, data, urb_id=spare if queued else read, **mine)
            spare = read if queued else None
            t_ns += period + (jitter(index) if jitter else 0)
            index += 1

    def port_status(self, t_ns, status, change, port=2):
        setup = struct.pack('<BBHHH', 0xa3, 0, 0, port, 4)
        urb_id = self.usb(t_ns, 'S', 0x80, xfer=XFER_CTRL, dev=ROOT_HUB, status=-115, length=4, setup=setup)
        self.usb(t_ns + 50_000, 'C', 0x80, struct.pack('<HH', status, change), urb_id=urb_id, xfer=XFER_CTRL,
                 dev=ROOT_HUB)

    def write(self, path, bus=5):
        ordered = sorted(self.records, key=lambda record: record.t_ns)
        with capfile.Writer(path, start_ns=ordered[0].t_ns, bus=bus) as writer:
            for record in ordered:
                writer.write(record)
        return path


STARTUP = (0x0001, 0x0002, 0x0015, 0x0100, 0x0007)


def session(seconds=10, *, drop_at=None, **options):
    """A driver session: start-up commands, a 1 kHz stream and one 0x0007 a second, then optionally a drop.

    The drop: a 0x0007 goes out 500 ms before and is never answered, the stream stops 50 ms before, then
    the port loses its connection, the kernel says so, and everything outstanding fails with ESHUTDOWN.
    """
    build = Builder(**options)
    build.devices(T0)
    for index, msgid in enumerate(STARTUP):
        build.command(T0 + 10 * MS * index, msgid)
    end = T0 + seconds * S if drop_at is None else T0 + drop_at * S
    stream_end = end if drop_at is None else end - 50 * MS
    build.stream(T0 + 100 * MS, stream_end)
    tick = T0 + 500 * MS
    polled = 1
    while tick < end - (0 if drop_at is None else 500 * MS):
        build.command(tick, 0x0007)
        polled += 1
        tick += S
    if drop_at is not None:
        stuck = build.command(end - 500 * MS, 0x0007, complete=False)
        polled += 1
        build.port_status(end, 0x0100, 0x0001)
        build.note(end + MS, type='kernel', text='usb 5-2: USB disconnect, device number 3')
        build.note(end + 2 * MS, type='driver_log',
                   text='[E][libglasses] Failed to send USB command - MsgID: 0x0007, error code: -1')
        build.usb(end + 3 * MS, 'C', 0x04, urb_id=stuck, status=-108)
    build.polled = polled
    return build
