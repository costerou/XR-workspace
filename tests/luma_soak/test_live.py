import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from tests.luma_soak.fakes import Feed, Machine, journal_line
from tools.luma_soak import cli
from tools.luma_soak.monitor import Tail, load_session

REPO = Path(__file__).resolve().parents[2]
DROP = 'usb 5-2: USB disconnect, device number 3'


class TailTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / 'log'

    def test_starts_at_the_end(self):
        self.path.write_text('old\n')
        tail = Tail(self.path)
        with self.path.open('a') as f:
            f.write('new\n')
        self.assertEqual(tail.poll(), ['new'])

    def test_holds_a_half_written_line(self):
        self.path.write_text('')
        tail = Tail(self.path)
        with self.path.open('a') as f:
            f.write('ha')
            f.flush()
            self.assertEqual(tail.poll(), [])
            f.write('lf\n')
        self.assertEqual(tail.poll(), ['half'])

    def test_a_truncated_file_is_read_from_its_start(self):
        self.path.write_text('a long first line\n')
        tail = Tail(self.path)
        self.path.write_text('x\n')
        self.assertEqual(tail.poll(), ['x'])

    def test_a_replaced_file_is_read_from_its_start(self):
        self.path.write_text('old\n')
        tail = Tail(self.path)
        moved = self.path.with_suffix('.1')
        self.path.rename(moved)
        self.path.write_text('fresh one\n')
        self.assertEqual(tail.poll(), ['fresh one'])

    def test_a_file_that_appears_later_is_read_whole(self):
        tail = Tail(self.path)
        self.assertEqual(tail.poll(), [])
        self.path.write_text('first\n')
        self.assertEqual(tail.poll(), ['first'])

    def test_heap_bytes_do_not_break_it(self):
        self.path.write_text('')
        tail = Tail(self.path)
        self.path.write_bytes(b'Error opening file (\xbf\xff): 2\n')
        self.assertEqual(len(tail.poll()), 1)


class SessionFileTests(unittest.TestCase):
    def test_a_line_cut_short_by_a_kill_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 's.jsonl'
            path.write_text('{"src": "cpu", "t": 1}\n{"src": "ker')
            self.assertEqual(load_session(path), [{'src': 'cpu', 't': 1}])


class GateTests(unittest.TestCase):
    """The real gate, end to end, in a child process, against a pretend machine."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.machine = Machine(tmp.name)
        self.machine.glasses()
        self.machine.driver()

    def spawn(self, *extra, duration='0.6'):
        command = [sys.executable, '-m', 'tools.luma_soak', 'gate', '--duration', duration, '--json', *self.machine.argv(*extra)]
        return subprocess.Popen(command, cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def gate(self, *extra, duration='0.6'):
        child = self.spawn(*extra, duration=duration)
        out, err = child.communicate(timeout=30)
        return child.returncode, json.loads(out) if out.strip() else None, err

    def test_clean_soak_with_a_driver_and_a_moving_feed_passes(self):
        feed = Feed(self.machine.root / 'feed')
        self.addCleanup(feed.close)
        feed.run()
        code, report, err = self.gate('--feed', str(feed.path))
        self.assertEqual(code, 0, (report and report['verdict'], err))
        self.assertGreater(report['coverage']['feed_samples_per_s'], 20)
        self.assertEqual(report['watch']['audit'], 'ok')

    def test_session_file_is_private_and_judges_the_same_later(self):
        code, report, _ = self.gate('--no-require-feed')
        path = self.machine.root / 'session.jsonl'
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(code, 0)
        self.assertEqual(cli.main(['gate', '--session', str(path), '--min-duration', '0.5', '--no-require-feed', '--json']), 0)

    def test_frozen_feed_is_inconclusive(self):
        feed = Feed(self.machine.root / 'feed')
        self.addCleanup(feed.close)
        code, report, _ = self.gate('--feed', str(feed.path))
        self.assertEqual(code, 2, report['verdict'])
        self.assertEqual(report['coverage']['feed'], 0)

    def test_drop_in_the_stream_fails(self):
        self.machine.stream.write_text(journal_line(time.time() + 0.2, DROP) + '\n')
        code, report, _ = self.gate('--no-require-feed')
        self.assertEqual((code, report['drops'], report['hub_drops']), (1, 1, 1))

    def test_drop_only_the_end_audit_sees_still_fails(self):
        self.machine.audit.write_text(journal_line(time.time() + 0.2, DROP) + '\n')
        code, report, _ = self.gate('--no-require-feed')
        self.assertEqual((code, report['drops']), (1, 1))

    def test_a_dead_kernel_stream_with_a_good_audit_is_noted(self):
        self.machine.journalctl.write_text(f'#!/bin/sh\ncase " $* " in *" -f "*) exit 1;; *) cat "{self.machine.audit}";; esac\n')
        code, report, _ = self.gate('--no-require-feed')
        self.assertEqual(code, 0)
        self.assertIn('kernel log stream ended, status 1', report['watch']['blind'])

    def test_journalctl_failing_outright_is_inconclusive(self):
        self.machine.journalctl.write_text('#!/bin/sh\nexit 1\n')
        code, report, _ = self.gate('--no-require-feed')
        self.assertEqual(code, 2)
        self.assertTrue(any('audit' in reason for reason in report['verdict']['reasons']))

    def test_journalctl_missing_is_inconclusive(self):
        self.machine.journalctl.unlink()
        code, report, _ = self.gate('--no-require-feed')
        self.assertEqual(code, 2)

    def test_glasses_absent_is_inconclusive(self):
        import shutil
        shutil.rmtree(self.machine.sysfs / '5-2.1')
        code, report, _ = self.gate('--no-require-feed')
        self.assertEqual(code, 2)

    def test_no_driver_is_inconclusive(self):
        import shutil
        shutil.rmtree(self.machine.proc / '4242')
        code, report, _ = self.gate('--no-require-feed')
        self.assertEqual(code, 2)

    def test_driver_name_excludes_a_process_that_merely_shares_the_other_name(self):
        code, report, _ = self.gate('--no-require-feed', '--driver-name', 'xr-luma-driver')
        self.assertEqual((code, report['coverage']['driver']), (2, 0))

    def test_a_re_enumeration_the_log_never_mentions_fails(self):
        child = self.spawn('--no-require-feed', duration='1.0')
        time.sleep(0.5)
        self.machine.plug('5-2.1', '35ca:1104', 99)
        out, _ = child.communicate(timeout=30)
        report = json.loads(out)
        self.assertEqual((child.returncode, report['sysfs_only_drops']), (1, 1))

    def test_fail_fast_stops_at_the_first_drop(self):
        self.machine.stream.write_text(journal_line(time.time() + 0.2, DROP) + '\n')
        started = time.monotonic()
        code, report, err = self.gate('--no-require-feed', '--fail-fast', duration='30')
        self.assertEqual((code, report['stop_reason']), (1, 'fail-fast'))
        self.assertLess(time.monotonic() - started, 15)
        self.assertIn('DROP 1', err)

    def test_stopped_early_is_inconclusive_never_a_pass(self):
        child = self.spawn('--no-require-feed', duration='30')
        time.sleep(1.0)
        child.send_signal(signal.SIGTERM)
        out, _ = child.communicate(timeout=30)
        report = json.loads(out)
        self.assertEqual((child.returncode, report['stop_reason']), (2, 'interrupted'))

    def test_driver_log_lines_reach_the_correlation(self):
        log = self.machine.root / 'driver.log'
        log.write_text('')
        child = self.spawn('--no-require-feed', duration='1.2')
        time.sleep(0.5)
        with log.open('a') as f:
            f.write('[E][libglasses] Failed to send USB command - MsgID: 0x0007, error code: -1\n')
        time.sleep(0.2)
        self.machine.audit.write_text(journal_line(time.time(), DROP) + '\n')
        out, _ = child.communicate(timeout=30)
        report = json.loads(out)
        self.assertEqual(child.returncode, 1)
        self.assertEqual(report['drop_details'][0]['driver']['commands_failed'], {'0x0007': 1})

    def test_a_broken_tool_reads_as_inconclusive(self):
        self.assertEqual(cli.main(['gate', '--session', str(self.machine.root / 'never-written.jsonl')]), 2)


class ReportCommandTests(unittest.TestCase):
    FIXTURES = Path(__file__).parent / 'fixtures'

    def run_cli(self, *argv):
        return subprocess.run([sys.executable, '-m', 'tools.luma_soak', *argv], cwd=REPO, capture_output=True, text=True, timeout=60)

    def test_report_of_real_logs(self):
        done = self.run_cli('report', '--kernel-log', str(self.FIXTURES / 'kernel_boot_2026-11-01_03.log'), '--driver-log', str(self.FIXTURES / 'driver_2026-11-03.log'))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn('Drops: 54 (54 hub', done.stdout)
        self.assertIn('Luma soak: FAIL', done.stdout)

    def test_gate_on_a_saved_log_with_drops_is_nonzero(self):
        self.assertEqual(self.run_cli('gate', '--kernel-log', str(self.FIXTURES / 'kernel_boot_2026-11-01_03.log')).returncode, 1)

    def test_report_needs_a_source(self):
        self.assertNotEqual(self.run_cli('report').returncode, 0)

    def test_sanitize(self):
        raw = '2026-11-03T01:22:43.252154-05:00 costerou kernel: usb 5-2.2: SerialNumber: 1234333333\n2026-11-03T01:22:43.2-05:00 costerou kernel: audit: nope\n'
        done = subprocess.run([sys.executable, '-m', 'tools.luma_soak', 'sanitize', '--kernel-usb'], cwd=REPO, input=raw, capture_output=True, text=True)
        self.assertEqual(done.stdout, '2026-11-03T01:22:43.252154-05:00 host kernel: usb 5-2.2: SerialNumber: REDACTED\n')

    def test_sanitize_survives_heap_bytes(self):
        done = subprocess.run([sys.executable, '-m', 'tools.luma_soak', 'sanitize'], cwd=REPO, input=b'Using hardware id abc\nError (\xbf\x01): 2\n', capture_output=True)
        self.assertEqual(done.stdout, b'Using hardware id REDACTED\nError (??): 2\n')

    def test_committed_fixtures_are_clean(self):
        for path in self.FIXTURES.iterdir():
            text = path.read_text()
            self.assertEqual(path.read_bytes(), text.encode('ascii'), path)
            for secret in ('costerou', '1234333333', 'SN0001', 'c27c02f'):
                self.assertNotIn(secret, text)
