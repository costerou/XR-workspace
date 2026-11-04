"""Compiles the C++ reader the daemon will use and checks it agrees with Python on the same bytes."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from tools.luma_capture import capfile
from tools.luma_capture.capfile import F_GLASSES, Record, Writer, XFER_BULK, note_record

import synth

ROOT = Path(__file__).resolve().parents[2]
COMPILER = shutil.which(os.environ.get('CXX', 'c++'))


def expected(path):
    reader = capfile.Reader(path)
    lines = [f'file {reader.start_ns} {reader.bus}']
    for record in reader:
        checksum = 0
        for byte in record.data:
            checksum = (checksum * 31 + byte) & 0xffffffffffffffff
        host_write = (record.kind == capfile.KIND_USB and record.event == 'S' and not record.is_in and record.glasses
                      and record.xfer != capfile.XFER_CTRL)
        event = record.event if record.event != '\0' else '-'
        lines.append(f'{record.t_ns} {record.kind} {event} {record.dev} {record.ep:02x} {record.status} '
                     f'{record.urb_len} {len(record.data)} {int(record.replayable)} {int(host_write)} {checksum}')
    lines.append(f'truncated {int(reader.truncated)}')
    return lines


@unittest.skipUnless(COMPILER, 'no C++ compiler')
class CppReader(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.folder.cleanup)
        cls.dir = Path(cls.folder.name)
        cls.binary = cls.dir / 'check'
        subprocess.run([COMPILER, '-std=c++20', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined',
                        '-I', ROOT / 'spike/driver/include', ROOT / 'tests/luma_capture/capture_file_check.cpp',
                        '-o', cls.binary], check=True)

    def read(self, path):
        run = subprocess.run([self.binary, path], capture_output=True, text=True)
        return run.returncode, run.stdout.splitlines()

    def test_agrees_with_python_on_a_whole_session(self):
        path = synth.session(3, drop_at=2).write(self.dir / 'session.lcap')
        code, lines = self.read(path)
        self.assertEqual(code, 0)
        self.assertEqual(lines, expected(path))
        self.assertGreater(sum(line.split()[-3:-2] == ['1'] for line in lines), 1000)   # replayable stream

    def test_a_cut_off_file_stops_at_the_last_whole_record(self):
        path = synth.session(1).write(self.dir / 'cut.lcap')
        path.write_bytes(path.read_bytes()[:-7])
        code, lines = self.read(path)
        self.assertEqual(code, 0)
        self.assertEqual(lines[-1], 'truncated 1')
        self.assertEqual(lines, expected(path))

    def test_an_unknown_kind_is_stepped_over(self):
        path = self.dir / 'kinds.lcap'
        with Writer(path, start_ns=1, bus=5) as writer:
            writer.write(Record(1, event='C', ep=0x81, flags=F_GLASSES, xfer=XFER_BULK, data=b'one'))
            writer.write(Record(2, kind=9, data=b'future'))
            writer.write(note_record(3, type='x'))
        code, lines = self.read(path)
        self.assertEqual((code, len(lines)), (0, 4))
        self.assertEqual(lines, expected(path))

    def test_garbage_and_missing_files_are_refused(self):
        bad = self.dir / 'bad.lcap'
        bad.write_bytes(b'not a capture at all, sorry about that friend')
        code, lines = self.read(bad)
        self.assertEqual(code, 1)
        self.assertIn('not a capture', lines[0])
        self.assertEqual(self.read(self.dir / 'absent.lcap')[0], 1)

    def test_a_newer_version_is_refused(self):
        path = synth.session(1).write(self.dir / 'v2.lcap')
        raw = bytearray(path.read_bytes())
        raw[8] = 2
        path.write_bytes(raw)
        self.assertEqual(self.read(path)[0], 1)


if __name__ == '__main__':
    unittest.main()
