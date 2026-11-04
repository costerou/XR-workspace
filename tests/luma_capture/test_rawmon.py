"""A raw `cat /dev/usbmonN` dump to a sanitised capture. What matters most: nothing identifying survives."""
from pathlib import Path
import struct
import tempfile
import unittest

from tools.luma_capture import capfile, rawmon

HEADER = struct.Struct('<QBBBBHbbqiiII8s')
SERIAL = b'P6TESTSERIAL0001'
T0 = 1_793_769_764


def event(kind, xfer, ep, dev, data=b'', *, urb=1, usec=0, status=0, setup=None, len_urb=None, bus=5):
    return HEADER.pack(urb, ord(kind), xfer, ep, dev, bus, 0 if setup else 45, 0 if data else 62, T0, usec, status,
                       len(data) if len_urb is None else len_urb, len(data), setup or bytes(8)) + data


def device_descriptor(vid, pid):
    return bytes([18, 1, 0x10, 2, 0, 0, 0, 64]) + struct.pack('<HH', vid, pid) + bytes([1, 0, 0x10, 0x20, 0, 1])


def imu(seq=1):
    return b'\xaa\x8f\x00\x88' + struct.pack('<HHI', seq, 16, seq * 10) + struct.pack('<6hI', 1, 2, 3, 4, 5, 8000, 2000)


class Base(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.dir = Path(folder.name)

    def imported(self, *events):
        source = self.dir / 'dump.bin'
        source.write_bytes(b''.join(events))
        output = self.dir / 'out.lcap'
        self.count = rawmon.import_raw(source, output)
        return [r for r in capfile.Reader(output) if not r.is_note], output


class Sanitising(Base):
    def glasses(self):
        return event('C', 2, 0x80, 13, device_descriptor(0x35ca, 0x1104))

    def test_imu_and_commands_stay_whole(self):
        start = b'\xfa\x55\x95\x00\x00'
        records, _ = self.imported(self.glasses(), event('S', 3, 0x04, 13, start), event('C', 1, 0x81, 13, imu()),
                                   event('C', 3, 0x85, 13, b'\xfa\x55\x95\x00\x00\x00'))
        kept = [r for r in records if r.ep != 0x80]
        self.assertEqual([r.data for r in kept], [start, imu(), b'\xfa\x55\x95\x00\x00\x00'])
        self.assertTrue(kept[1].replayable)

    def test_calibration_and_serial_on_the_mcu_channel_are_dropped(self):
        records, output = self.imported(self.glasses(), event('C', 3, 0x85, 13, b'\xfa\x55\xed\x00\x00\x40' + SERIAL + bytes(48)))
        reply = records[-1]
        self.assertEqual(reply.data, b'')
        self.assertTrue(reply.flags & capfile.F_POLICY_DROPPED)
        self.assertNotIn(SERIAL, output.read_bytes())

    def test_string_descriptors_are_dropped_for_every_device(self):
        text = b'\x16\x03' + SERIAL.decode().encode('utf-16-le')
        records, output = self.imported(self.glasses(), event('C', 2, 0x80, 13, text), event('C', 2, 0x80, 2, text))
        self.assertNotIn(SERIAL.decode().encode('utf-16-le'), output.read_bytes())

    def test_camera_keeps_its_header_and_never_its_pixels(self):
        tile = b'\xaa\x8f\x00\x12\x01\x00\xf4\x3f\xd2\x20\x01\x00' + b'\x7f' * 16372
        records, output = self.imported(self.glasses(), event('C', 3, 0x82, 13, tile))
        self.assertEqual(records[-1].data, tile[:12])
        self.assertEqual(records[-1].urb_len, len(tile))
        self.assertTrue(records[-1].flags & capfile.F_CAPPED)
        self.assertFalse(records[-1].replayable)
        self.assertNotIn(b'\x7f' * 32, output.read_bytes())

    def test_other_devices_keep_no_data(self):
        records, output = self.imported(event('C', 3, 0x81, 5, b'keyboard typing'))
        self.assertEqual(records[0].data, b'')
        self.assertTrue(records[0].flags & capfile.F_POLICY_DROPPED)
        self.assertNotIn(b'keyboard', output.read_bytes())

    def test_companion_interrupts_keep_no_data(self):
        records, _ = self.imported(event('C', 2, 0x80, 12, device_descriptor(0x35ca, 0x1102)), event('C', 1, 0x83, 12, b'button'))
        self.assertTrue(records[-1].companion)
        self.assertEqual(records[-1].data, b'')

    def test_isochronous_events_are_left_out(self):
        records, _ = self.imported(self.glasses(), event('C', 0, 0x81, 2, bytes(112)))
        self.assertEqual(len(records), 1)

    def test_a_setup_packet_survives_as_a_setup(self):
        setup = bytes.fromhex('02 01 00 00 82 00 00 00')
        records, _ = self.imported(self.glasses(), event('S', 2, 0x00, 13, setup=setup))
        self.assertEqual(records[-1].setup, setup)
        self.assertTrue(records[-1].flags & capfile.F_SETUP)


class Damage(Base):
    def test_a_dump_cut_off_mid_event_keeps_what_came_before(self):
        whole = event('C', 2, 0x80, 13, device_descriptor(0x35ca, 0x1104))
        records, _ = self.imported(whole, event('C', 1, 0x81, 13, imu())[:-5])
        self.assertEqual(len(records), 1)

    def test_something_else_is_refused(self):
        with self.assertRaises(rawmon.RawError):
            self.imported(b'this is not a usbmon dump at all, not even slightly so, no no no no no no no')

    def test_an_empty_dump_is_refused(self):
        with self.assertRaises(rawmon.RawError):
            self.imported(b'')


class Fixture(Base):
    def capture(self, *packets):
        _, output = self.imported(event('C', 2, 0x80, 13, device_descriptor(0x35ca, 0x1104)), *packets)
        return output

    def test_cuts_the_stretch_asked_for(self):
        output = self.capture(*(event('C', 1, 0x81, 13, imu(i), usec=i * 1000) for i in range(10)))
        fixture = self.dir / 'fixture.bin'
        self.assertEqual(rawmon.imu_fixture(output, fixture, start_s=0.002, seconds=0.003), 3)
        self.assertEqual(fixture.read_bytes(), imu(2) + imu(3) + imu(4))

    def test_refuses_anything_that_is_not_an_imu_packet(self):
        output = self.capture(event('C', 1, 0x81, 13, b'\x00' * 28))
        with self.assertRaises(rawmon.RawError):
            rawmon.imu_fixture(output, self.dir / 'fixture.bin', start_s=0, seconds=1)

    def test_refuses_an_empty_stretch(self):
        output = self.capture(event('C', 1, 0x81, 13, imu()))
        with self.assertRaises(rawmon.RawError):
            rawmon.imu_fixture(output, self.dir / 'fixture.bin', start_s=9, seconds=1)


class RealFixture(unittest.TestCase):
    def test_the_committed_fixture_is_imu_packets_only(self):
        path = Path(__file__).parents[2] / 'spike/driver/tests/fixtures/luma_imu_real.bin'
        data = path.read_bytes()
        self.assertEqual(len(data) % rawmon.IMU_PACKET, 0)
        for at in range(0, len(data), rawmon.IMU_PACKET):
            self.assertEqual(data[at:at + 4], rawmon.IMU_HEAD)


if __name__ == '__main__':
    unittest.main()
