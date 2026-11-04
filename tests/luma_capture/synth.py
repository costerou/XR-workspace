"""Made-up captures with a known answer. The framing here is invented for the tests: the code must find
it from behaviour alone, so tests vary it, and nothing in tools/ may share these numbers."""
import random
import struct

from tools.luma_capture import capfile
from tools.luma_capture.capfile import F_GLASSES, F_SETUP, Record, XFER_BULK, XFER_CTRL, XFER_INT, note_record

T0 = 1_762_000_000_000_000_000
MS = 1_000_000
S = 1_000_000_000
GLASSES_DEV = 7
ROOT_HUB = 1


class Builder:
    def __init__(self, offset=14, endian='little', echo=True, seed=1):
        self.offset, self.endian, self.echo = offset, endian, echo
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
        urb_id = self.usb(t_ns, 'S', 0x04, self.payload(msgid), status=-115)
        if complete:
            self.usb(t_ns + 200_000, 'C', 0x04, urb_id=urb_id, length=64)
            reply = self.payload(msgid if self.echo else self.rng.randrange(0x4000, 0x8000), 128)
            read = self.usb(t_ns, 'S', 0x82, status=-115, length=512)
            self.usb(t_ns + 2 * MS, 'C', 0x82, reply, urb_id=read)
        return urb_id

    def stream(self, start, stop, *, ep=0x81, period=MS, xfer=XFER_INT):
        t_ns = start
        while t_ns < stop:
            read = self.usb(t_ns - period // 2, 'S', ep, status=-115, length=1024, xfer=xfer)
            self.usb(t_ns, 'C', ep, self.rng.randbytes(60), urb_id=read, xfer=xfer)
            t_ns += period

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
