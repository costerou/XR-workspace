import contextlib
import io
import struct
import tempfile
from pathlib import Path
import signal
import threading
import unittest
from unittest.mock import patch

from tools.luma_capture import __main__ as cli, capfile
from tools.luma_capture.report import analyze
from tools.luma_capture.usbmon import Event

import synth


def usb_events(build):
    """A synthetic session as the kernel would hand it to the recorder."""
    events = []
    for record in sorted(build.records, key=lambda r: r.t_ns):
        if record.is_note:
            continue
        has_setup = bool(record.flags & capfile.F_SETUP)
        events.append(Event(record.urb_id, record.event, record.xfer, record.ep, record.dev, record.bus, has_setup,
                            bool(record.data), record.t_ns, record.status, record.urb_len, len(record.data),
                            record.setup, record.data))
    return events


def run(*argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = cli.main(list(map(str, argv)))
    return code, out.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.dir = Path(folder.name)
        self.path = synth.session(10, drop_at=8).write(self.dir / 'a.lcap')


class Access(Base):
    def test_a_missing_node_says_the_module_is_not_loaded(self):
        self.assertIn('module is not loaded', cli.access_problem(5, dev_root=self.dir))

    def test_a_readable_node_is_fine(self):
        (self.dir / 'usbmon5').touch()
        self.assertIsNone(cli.access_problem(5, dev_root=self.dir))

    def test_check_prints_the_exact_steps_when_blocked(self):
        with patch.object(cli, 'find_glasses', return_value={'bus': 5, 'dev': 4, 'path': '5-2.1'}), \
                patch.object(cli, 'access_problem', return_value='nope'):
            code, out = run('check')
        self.assertEqual(code, 1)
        self.assertIn('sudo modprobe usbmon', out)
        self.assertIn('setfacl -m', out)

    def test_check_passes_when_ready(self):
        with patch.object(cli, 'find_glasses', return_value={'bus': 5, 'dev': 4, 'path': '5-2.1'}), \
                patch.object(cli, 'access_problem', return_value=None):
            code, out = run('check')
        self.assertEqual(code, 0)
        self.assertIn('ready', out)

    def test_record_refuses_without_access_and_names_the_way_forward(self):
        with patch.object(cli, 'find_glasses', return_value={'bus': 5, 'dev': 4, 'path': '5-2.1'}), \
                patch.object(cli, 'access_problem', return_value='nope'), self.assertRaisesRegex(SystemExit, 'check'):
            run('record', '--out', self.dir / 'o')

    def test_record_refuses_with_no_glasses_and_no_bus(self):
        with patch.object(cli, 'find_glasses', return_value=None), self.assertRaisesRegex(SystemExit, 'not plugged'):
            run('record', '--out', self.dir / 'o')


class EndToEnd(Base):
    def test_record_then_report_from_end_to_end(self):
        events = usb_events(synth.session(3, drop_at=2))
        batches = [events[index:index + 500] for index in range(0, len(events), 500)]

        class Source:
            ring_bytes = 16 << 20
            closed = False

            def __init__(self, bus, fetch_bytes):
                self.bus = bus

            def read_batch(self, _):
                return batches.pop(0) if batches else []

            def stats(self):
                return (0, 0)

            def close(self):
                Source.closed = True

        glasses = {'bus': 5, 'dev': synth.GLASSES_DEV, 'path': '5-2.1', 'vid': 0x35ca, 'pid': 0x1104, 'hub': False,
                   'product': 'x'}
        out = self.dir / 'capture'
        handlers = [signal.getsignal(number) for number in (signal.SIGINT, signal.SIGTERM)]
        self.addCleanup(signal.signal, signal.SIGINT, handlers[0])
        self.addCleanup(signal.signal, signal.SIGTERM, handlers[1])
        stop = threading.Event()
        with patch.object(cli, 'find_glasses', return_value=glasses), \
                patch.object(cli, 'access_problem', return_value=None), \
                patch.object(cli, 'UsbmonSource', Source), \
                patch.object(cli, 'scan_devices', side_effect=lambda: [glasses] if batches else []), \
                patch.object(cli, 'start_followers', return_value=stop):
            # Longer than one sysfs poll, so the recorder sees the glasses go.
            code, printed = run('record', '--out', out, '--duration', 0.6, '--post-roll', 0)
        self.assertEqual(code, 0)
        self.assertIn('Recording bus 5', printed)
        self.assertTrue(stop.is_set())
        self.assertTrue(Source.closed)
        result = analyze([out])
        # The synthetic transfers carry 2025 times and the recorder's note today's, so the failed transfer forms a
        # second, unconfirmed cluster here. Only the note may count.
        self.assertEqual(sum(drop['confirmed'] for drop in result['drops']), 1)
        self.assertGreater(result['endpoints']['ep81']['transfers'], 1000)
        self.assertTrue(list((out / 'drops').rglob('*.lcap')))


class Marks(Base):
    def test_mark_leaves_a_line_for_the_recorder(self):
        code, _ = run('mark', 'pressed', 'brightness', 'up', '--out', self.dir)
        self.assertEqual(code, 0)
        self.assertEqual((self.dir / 'marks.txt').read_text(), 'pressed brightness up\n')


class Compare(Base):
    def test_prints_both_and_the_audit(self):
        quiet = synth.Builder(v1=True)
        quiet.devices(synth.T0)
        quiet.command(synth.T0, 0x100b)
        quiet.stream(synth.T0 + synth.MS, synth.T0 + 12 * synth.S)
        after = quiet.write(self.dir / 'after.lcap')
        code, out = run('compare', self.path, after)
        self.assertEqual(code, 0)
        self.assertIn('Commands per second once running', out)
        self.assertIn('Wire rules', out)
        self.assertIn('Sends nothing the baseline did not', out)


class Report(Base):
    def test_text(self):
        code, out = run('report', self.path)
        self.assertEqual(code, 0)
        self.assertIn('Disconnects: 1', out)
        self.assertIn('What stopped first', out)

    def test_json_and_framing(self):
        code, out = run('report', self.path, '--json', '--framing', 'offset=14,magic=abcd')
        self.assertEqual(code, 0)
        self.assertIn('"source": "given"', out)

    def test_audit_exit_status_follows_the_rules(self):
        noisy = synth.session(14).write(self.dir / 'noisy.lcap')
        self.assertEqual(run('report', noisy)[0], 0)
        self.assertEqual(run('report', noisy, '--audit')[0], 1)

    def test_a_bad_file_is_a_message_not_a_traceback(self):
        bad = self.dir / 'bad.lcap'
        bad.write_bytes(b'nope')
        with self.assertRaisesRegex(SystemExit, 'not a capture'):
            run('report', bad)


class Tools(Base):
    def test_show(self):
        code, out = run('show', self.path, '--limit', '3')
        self.assertEqual(code, 0)
        self.assertEqual(len(out.splitlines()), 3)

    def test_merge(self):
        output = self.dir / 'merged.bin'
        code, out = run('merge', self.path, '-o', output)
        self.assertEqual(code, 0)
        self.assertEqual(len(list(capfile.Reader(output))), len(list(capfile.Reader(self.path))))

    def test_pcap_is_what_wireshark_expects(self):
        output = self.dir / 'x.pcap'
        run('pcap', self.path, '-o', output)
        raw = output.read_bytes()
        magic, major, minor, _, _, _, link = struct.unpack_from('<IHHiIII', raw)
        self.assertEqual((magic, major, minor, link), (0xa1b2c3d4, 2, 4, 220))
        records = [r for r in capfile.Reader(self.path) if not r.is_note]
        offset, packets = 24, 0
        while offset < len(raw):
            seconds, micros, included, original = struct.unpack_from('<IIII', raw, offset)
            self.assertEqual(included, original)
            urb, kind, xfer, ep, dev, bus = struct.unpack_from('<QBBBBH', raw, offset + 16)
            record = records[packets]
            self.assertEqual((urb, chr(kind), xfer, ep, dev, bus),
                             (record.urb_id, record.event, record.xfer, record.ep, record.dev, record.bus))
            self.assertEqual(raw[offset + 16 + 64:offset + 16 + included], record.data)
            self.assertEqual(seconds * 10**6 + micros, record.t_ns // 1000)
            offset += 16 + included
            packets += 1
        self.assertEqual(packets, len(records))


if __name__ == '__main__':
    unittest.main()
