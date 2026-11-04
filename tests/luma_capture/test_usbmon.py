import ctypes
import errno
import struct
import tempfile
from pathlib import Path
import unittest

from tools.luma_capture import usbmon
from tools.luma_capture.usbmon import MON_HEADER, UsbmonSource, parse_event


def header(*, urb=7, kind='C', xfer=3, ep=0x81, dev=4, bus=5, flag_setup=ord('-'), flag_data=0, sec=100, usec=250,
           status=0, len_urb=3, len_cap=3, setup=bytes(8)):
    return MON_HEADER.pack(urb, ord(kind), xfer, ep, dev, bus, flag_setup, flag_data, sec, usec, status, len_urb,
                           len_cap, setup, 0, 0, 0, 0)


class Kernel:
    """Stands in for ioctl(2): fills the caller's buffers through the pointers it was given, as the kernel does."""

    def __init__(self, events=(), ring_limit=1 << 40):
        self.events = list(events)
        self.ring_limit = ring_limit
        self.rings = []
        self.lost = 0

    def __call__(self, fd, request, argument, mutate=False):
        if request == usbmon.MON_IOCT_RING_SIZE:
            self.rings.append(argument)
            if argument > self.ring_limit:
                raise OSError(errno.EINVAL, 'too big')
            return 0
        if request == usbmon.MON_IOCG_STATS:
            argument[:] = struct.pack('<II', len(self.events), self.lost)
            return 0
        assert request == usbmon.MON_IOCX_GETX
        if not self.events:
            raise BlockingIOError(errno.EAGAIN, 'empty')
        head, data = self.events.pop(0)
        hdr, buffer, alloc = struct.unpack('<QQQ', argument)
        ctypes.memmove(hdr, head, len(head))
        ctypes.memmove(buffer, data, min(len(data), alloc))
        return 0


class Constants(unittest.TestCase):
    def test_kernel_struct_size(self):
        self.assertEqual(MON_HEADER.size, 64)     # PKT_SIZE in mon_bin.c

    def test_ioctl_numbers_equal_the_kernel_macros(self):
        self.assertEqual(usbmon.MON_IOCX_GETX, 0x4018920a)     # _IOW(0x92, 10, 24 bytes)
        self.assertEqual(usbmon.MON_IOCG_STATS, 0x80089203)    # _IOR(0x92, 3, 8 bytes)
        self.assertEqual(usbmon.MON_IOCT_RING_SIZE, 0x9204)    # _IO(0x92, 4)


class Parse(unittest.TestCase):
    def test_fields(self):
        event = parse_event(header(status=-71, len_urb=512, len_cap=64), b'xyz')
        self.assertEqual((event.id, event.type, event.xfer_type, event.epnum, event.devnum, event.busnum),
                         (7, 'C', 3, 0x81, 4, 5))
        self.assertEqual((event.ts_ns, event.status, event.len_urb, event.len_cap, event.data),
                         (100_000_250_000, -71, 512, 64, b'xyz'))

    def test_the_flags_are_zero_when_something_is_present(self):
        event = parse_event(header(flag_setup=0, flag_data=ord('<')))
        self.assertTrue(event.has_setup)
        self.assertFalse(event.has_data)


class Source(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / 'usbmon5'
        self.path.touch()     # a regular file polls ready at once, which stands in for "events waiting"

    def source(self, kernel):
        source = UsbmonSource(5, path=self.path, ioctl=kernel)
        self.addCleanup(source.close)
        return source

    def test_each_event_arrives_with_its_own_data(self):
        kernel = Kernel([(header(urb=1, len_cap=3), b'abc'), (header(urb=2, len_cap=1), b'z')])
        events = self.source(kernel).read_batch(0)
        self.assertEqual([(e.id, e.data) for e in events], [(1, b'abc'), (2, b'z')])

    def test_an_empty_ring_ends_the_batch_quietly(self):
        self.assertEqual(self.source(Kernel()).read_batch(0), [])

    def test_a_batch_has_a_ceiling_so_periodic_work_still_runs(self):
        kernel = Kernel([(header(), b'abc')] * (usbmon.BATCH + 5))
        self.assertEqual(len(self.source(kernel).read_batch(0)), usbmon.BATCH)
        self.assertEqual(len(kernel.events), 5)

    def test_asks_for_a_big_ring_and_settles_for_what_the_kernel_allows(self):
        kernel = Kernel(ring_limit=4 << 20)
        self.assertEqual(self.source(kernel).ring_bytes, 4 << 20)
        self.assertEqual(kernel.rings, [16 << 20, 8 << 20, 4 << 20])

    def test_an_old_kernel_leaves_the_default_ring(self):
        self.assertEqual(self.source(Kernel(ring_limit=1000)).ring_bytes, 300 << 10)

    def test_stats_reports_what_the_kernel_lost(self):
        kernel = Kernel([(header(), b'abc')])
        kernel.lost = 9
        self.assertEqual(self.source(kernel).stats(), (1, 9))

    def test_only_the_fetch_size_is_copied(self):
        kernel = Kernel([(header(len_cap=100, len_urb=100), bytes(100))])
        source = UsbmonSource(5, path=self.path, ioctl=kernel, fetch_bytes=10)
        self.addCleanup(source.close)
        event = source.read_batch(0)[0]
        self.assertEqual((len(event.data), event.len_cap), (10, 100))

    def test_opened_read_only(self):
        self.path.chmod(0o400)
        self.source(Kernel())


class Sysfs(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)

    def device(self, name, **attributes):
        path = self.root / name
        path.mkdir()
        for key, value in attributes.items():
            (path / key).write_text(f'{value}\n')

    def test_lists_devices_and_skips_interfaces_and_half_gone_ones(self):
        self.device('5-2.1', busnum=5, devnum=101, idVendor='35ca', idProduct='1104', bDeviceClass='00',
                    product='VITURE Luma Ultra XR GLASSES')
        self.device('5-2', busnum=5, devnum=98, idVendor='1a86', idProduct='8091', bDeviceClass='09')
        self.device('5-2.1:1.0', bInterfaceClass='00')
        self.device('5-2.3', busnum=5)
        devices = usbmon.scan_devices(self.root)
        self.assertEqual([d['path'] for d in devices], ['5-2', '5-2.1'])
        self.assertTrue(devices[0]['hub'])
        glasses = usbmon.find_glasses(self.root)
        self.assertEqual((glasses['bus'], glasses['dev'], glasses['path']), (5, 101, '5-2.1'))

    def test_the_companion_is_recognised(self):
        self.assertTrue(usbmon.is_companion({'vid': 0x35ca, 'pid': 0x1102}))
        self.assertFalse(usbmon.is_companion({'vid': 0x35ca, 'pid': 0x1104}))

    def test_no_glasses(self):
        self.device('usb5', busnum=5, devnum=1, idVendor='1d6b', idProduct='0002', bDeviceClass='09')
        self.assertIsNone(usbmon.find_glasses(self.root))


class Grant(unittest.TestCase):
    def test_commands_name_the_bus_and_the_user(self):
        self.assertEqual(usbmon.grant_commands(5, 'cody'), ['sudo modprobe usbmon', 'sudo udevadm settle',
                                                            'sudo setfacl -m u:cody:r /dev/usbmon5'])
        self.assertEqual(usbmon.revoke_commands(5, 'cody'), ['sudo setfacl -x u:cody /dev/usbmon5',
                                                             'sudo modprobe -r usbmon'])


if __name__ == '__main__':
    unittest.main()
