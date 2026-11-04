import json
import struct
import tempfile
from pathlib import Path
import unittest

from tools.luma_capture import capfile
from tools.luma_capture.capfile import (CaptureError, F_CAPPED, F_GLASSES, F_KERNEL_TRUNCATED, F_POLICY_DROPPED, Reader,
                                        Record, Writer, XFER_BULK, XFER_CTRL, XFER_INT, note_record)


def usb(t_ns=5, **fields):
    return Record(t_ns, **fields)


class Base(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.dir = Path(folder.name)

    def write(self, name, *records, start_ns=1):
        with Writer(self.dir / name, start_ns=start_ns, bus=5) as writer:
            for record in records:
                writer.write(record)
        return self.dir / name


class Layout(Base):
    def test_header_sizes_are_what_the_spec_says(self):
        self.assertEqual(capfile.FILE_HEADER.size, 32)
        self.assertEqual(capfile.RECORD_HEADER.size, 48)

    def test_bytes_on_disk_match_the_spec_field_by_field(self):
        record = Record(0x1122334455667788, capfile.KIND_USB, 'C', XFER_BULK, 0x81, 0xaabb, 5, 9, F_GLASSES, -71, 512,
                        b'12345678', b'\xfe\xed')
        raw = self.write('a.lcap', record, start_ns=77).read_bytes()
        self.assertEqual(raw[:8], b'LUMACAP1')
        self.assertEqual(struct.unpack_from('<HHH', raw, 8), (1, 32, 48))
        self.assertEqual(struct.unpack_from('<QI', raw, 16), (77, 5))
        body = raw[32:]
        self.assertEqual(struct.unpack_from('<I', body, 0)[0], 50)
        self.assertEqual((body[4], chr(body[5]), body[6], body[7]), (1, 'C', 3, 0x81))
        self.assertEqual(struct.unpack_from('<QQ', body, 8), (0x1122334455667788, 0xaabb))
        self.assertEqual((struct.unpack_from('<H', body, 24)[0], body[26], body[27]), (5, 9, F_GLASSES))
        self.assertEqual(struct.unpack_from('<iII', body, 28), (-71, 512, 2))
        self.assertEqual(body[40:48], b'12345678')
        self.assertEqual(body[48:], b'\xfe\xed')


class RoundTrip(Base):
    def test_usb_and_note_records_come_back_equal(self):
        records = [Record(10, capfile.KIND_USB, 'S', XFER_CTRL, 0x80, 3, 5, 1, 1, -115, 4, b'\xa3\0\0\0\x02\0\x04\0'),
                   Record(11, capfile.KIND_USB, 'C', XFER_INT, 0x81, 4, 5, 7, 2, 0, 3, bytes(8), b'abc'),
                   note_record(12, type='kernel', text='usb 5-2: héllo')]
        reader = Reader(self.write('a.lcap', *records))
        back = list(reader)
        self.assertEqual(back, records)
        self.assertEqual(back[2].note, {'type': 'kernel', 'text': 'usb 5-2: héllo'})
        self.assertFalse(reader.truncated)
        self.assertEqual((reader.start_ns, reader.bus), (1, 5))

    def test_an_empty_capture_has_no_records(self):
        self.assertEqual(list(Reader(self.write('a.lcap'))), [])


class Damage(Base):
    def test_a_file_cut_inside_a_record_keeps_everything_before_it(self):
        path = self.write('a.lcap', usb(1, data=b'1234'), usb(2, data=b'5678'))
        for cut in (2, 6, 49):     # into the data, and into the header
            path.write_bytes(path.read_bytes()[:-cut])
            reader = Reader(path)
            self.assertEqual([r.t_ns for r in reader], [1], cut)
            self.assertTrue(reader.truncated)
            path = self.write('a.lcap', usb(1, data=b'1234'), usb(2, data=b'5678'))

    def test_lengths_that_disagree_stop_the_read(self):
        path = self.write('a.lcap', usb(1), usb(2))
        raw = bytearray(path.read_bytes())
        struct.pack_into('<I', raw, 32 + 48, 49)
        path.write_bytes(raw)
        reader = Reader(path)
        self.assertEqual(len(list(reader)), 1)
        self.assertTrue(reader.truncated)

    def test_not_a_capture(self):
        path = self.dir / 'x.lcap'
        path.write_bytes(b'hello' * 20)
        with self.assertRaises(CaptureError):
            Reader(path)
        path.write_bytes(b'LU')
        with self.assertRaises(CaptureError):
            Reader(path)

    def test_a_later_version_is_refused_not_misread(self):
        path = self.write('a.lcap')
        raw = bytearray(path.read_bytes())
        raw[8] = 2
        path.write_bytes(raw)
        with self.assertRaisesRegex(CaptureError, 'version 2'):
            Reader(path)


class Forward(Base):
    def test_an_unknown_record_kind_is_stepped_over(self):
        path = self.write('a.lcap', usb(1), Record(2, kind=9, data=b'future'), usb(3))
        self.assertEqual([r.t_ns for r in Reader(path)], [1, 3])

    def test_a_longer_record_header_is_honoured(self):
        path = self.write('a.lcap')
        raw = bytearray(path.read_bytes())
        struct.pack_into('<H', raw, 12, 56)
        body = capfile.RECORD_HEADER.pack(56 + 2, 1, ord('C'), 3, 0x81, 9, 1, 5, 7, 2, 0, 2, 2, bytes(8))
        path.write_bytes(bytes(raw) + body + b'XXXXXXXX' + b'hi')
        self.assertEqual(next(iter(Reader(path))).data, b'hi')


class Replayable(unittest.TestCase):
    good = dict(event='C', ep=0x81, xfer=XFER_BULK, flags=F_GLASSES, status=0)

    def test_a_complete_read_from_the_glasses(self):
        self.assertTrue(usb(**self.good).replayable)

    def test_interrupt_counts_too(self):
        self.assertTrue(usb(**{**self.good, 'xfer': XFER_INT}).replayable)

    def test_everything_else_is_not(self):
        for change in ({'event': 'S'}, {'ep': 0x04}, {'flags': 0}, {'status': -71}, {'xfer': XFER_CTRL},
                       {'flags': F_GLASSES | F_CAPPED}, {'flags': F_GLASSES | F_KERNEL_TRUNCATED},
                       {'flags': F_GLASSES | F_POLICY_DROPPED}, {'kind': capfile.KIND_NOTE}):
            self.assertFalse(usb(**{**self.good, **change}).replayable, change)


class Sets(Base):
    def test_files_and_folders_read_in_time_order(self):
        (self.dir / 'sub').mkdir()
        self.write('sub/late.lcap', usb(30), start_ns=30)
        early = self.write('sub/early.lcap', usb(10), start_ns=10)
        middle = self.write('middle.lcap', usb(20), start_ns=20)
        self.assertEqual([r.t_ns for r in capfile.records([self.dir / 'sub', middle])], [10, 20, 30])
        self.assertEqual([r.t_ns for r in capfile.records([early, early])], [10])

    def test_nothing_to_read_says_so(self):
        with self.assertRaisesRegex(CaptureError, 'no .lcap'):
            list(capfile.records([self.dir]))

    def test_merge_makes_one_file_a_replay_can_take(self):
        self.write('b.lcap', usb(30), start_ns=30)
        self.write('a.lcap', usb(10), note_record(11, type='x'), start_ns=10)
        output = self.dir / 'out' / 'all.bin'
        output.parent.mkdir()
        self.assertEqual(capfile.merge([self.dir], output), 3)
        self.assertEqual([r.t_ns for r in Reader(output)], [10, 11, 30])
        self.assertEqual(Reader(output).start_ns, 10)


class Notes(unittest.TestCase):
    def test_notes_are_compact_json(self):
        self.assertEqual(json.loads(note_record(1, type='x', n=1).data), {'type': 'x', 'n': 1})


if __name__ == '__main__':
    unittest.main()
