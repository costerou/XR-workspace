"""tools/luma_soak/cpp_baseline.sh and soak.sh end to end, against the pretend machine: the 3-hour soaks of D13, cut to seconds."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

from tests.luma_soak.fakes import Feed, Machine, journal_line

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / 'tools/luma_soak/cpp_baseline.sh'
SOAK = REPO / 'tools/luma_soak/soak.sh'
PIN = '/data/viture-workspace/bin/0123456789ab'
DROP = 'usb 5-2: USB disconnect, device number 3'


class SoakCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.machine = Machine(tmp.name)
        self.machine.glasses()
        self.machine.driver(name='xr-luma-driver')
        self.state = self.machine.root / 'state'
        self.out = self.machine.root / 'out'
        self.log = self.state / 'viture-workspace/luma-driver.log'
        self.log.parent.mkdir(parents=True)
        self.log.write_text('')
        self.feed = Feed(self.machine.root / 'feed')
        self.addCleanup(self.feed.close)
        self.feed.run()
        self.env = {**os.environ, 'XDG_STATE_HOME': str(self.state), 'LUMA_SOAK_OUT': str(self.out), 'LUMA_SOAK_SECONDS': '1.0',
                    'PYTHON': sys.executable}
        m = self.machine
        self.fakes = ['--journalctl', str(m.journalctl), '--sysfs', str(m.sysfs), '--proc', str(m.proc), '--settle', '0', '--tick', '0.05',
                      '--feed', str(self.feed.path)]

    def run_script(self, *args):
        done = subprocess.run([str(SCRIPT), *args], cwd=REPO, env=self.env, capture_output=True, text=True, timeout=60)
        return done.returncode, done.stdout, done.stderr

    def stored(self):
        reports = sorted(self.out.glob('*.report.json'))
        self.assertEqual(len(reports), 1, list(self.out.iterdir()) if self.out.exists() else None)
        self.assertTrue(reports[0].with_suffix('').with_suffix('.report.txt').exists())
        return json.loads(reports[0].read_text())

    def run_driver(self, exe):
        shutil.rmtree(self.machine.proc / '4242')
        self.machine.driver(name='xr-luma-driver', exe=exe)


class CppBaselineTests(SoakCase):
    def test_drops_are_counted_and_the_measurement_is_still_valid(self):
        self.env['LUMA_SOAK_SECONDS'] = '1.5'
        child = subprocess.Popen([str(SCRIPT), *self.fakes], cwd=REPO, env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        time.sleep(0.6)   # the driver log is followed from its end, as a live session's is
        now = time.time()
        with self.log.open('a') as f:
            f.write(f'luma: stale ms=300 at={now:.3f}\nluma: disconnected reason=gone at={now + 0.2:.3f}\n')
            f.write(f'luma: reconnected gap_ms=4200 calibration=kept at={now + 0.4:.3f}\n')
        self.machine.audit.write_text(journal_line(now + 0.1, DROP) + '\n')
        out, err = child.communicate(timeout=60)
        code = child.returncode
        self.assertEqual(code, 0, (out, err))
        self.assertIn('cpp baseline: VALID: 1 drop(s)', out)
        report = self.stored()
        self.assertEqual((report['drops'], report['coverage']['driver_processes']), (1, 1))
        self.assertGreater(report['drops_per_hour'], 1000)
        self.assertEqual(report['driver_events']['reconnect_gap_ms']['max'], 4200.0)

    def test_a_soak_that_proved_nothing_is_not_valid(self):
        shutil.rmtree(self.machine.sysfs / '5-2.1')
        code, out, _ = self.run_script(*self.fakes)
        self.assertEqual(code, 2)
        self.assertIn('NOT VALID', out)
        self.assertIn('glasses were not on USB', out)
        self.assertEqual(self.stored()['verdict']['exit'], 2)

    def test_only_the_cpp_driver_counts(self):
        shutil.rmtree(self.machine.proc / '4242')
        self.machine.driver(name='xrDriver')
        code, out, _ = self.run_script(*self.fakes)
        self.assertEqual(code, 2, out)
        self.assertIn('driver running for 0', out)

    def test_a_baseline_of_the_rust_driver_is_not_valid(self):
        self.run_driver(f'{PIN}/rust/xr-luma-driver')
        code, out, _ = self.run_script(*self.fakes)
        self.assertEqual(code, 2, out)
        self.assertIn('the rust driver ran, not only the cpp one', out)


    def test_a_recorded_session_is_judged_again_at_the_full_length(self):
        code, _, _ = self.run_script(*self.fakes)
        self.assertEqual(code, 0)
        session = next((self.state / 'luma_soak').glob('cpp-baseline-*.jsonl'))
        shutil.rmtree(self.out)
        self.env['LUMA_SOAK_SECONDS'] = '10800'
        code, out, _ = self.run_script('--session', str(session))
        self.assertEqual(code, 2)
        self.assertIn('of the required 10800 s', out)


class RustSoakTests(SoakCase):
    """soak.sh rust: the after soak, judged against the stored C++ baseline (D13)."""
    def soak(self, build, *args):
        done = subprocess.run([str(SOAK), build, *args, *self.fakes], cwd=REPO, env=self.env, capture_output=True, text=True, timeout=60)
        return done.returncode, done.stdout + done.stderr

    def baseline(self, drops=0, seconds=1.0):
        path = self.machine.root / 'baseline.report.json'
        path.write_text(json.dumps({'drops': drops, 'duration_s': seconds, 'coverage': {'driver_exes': [f'{PIN}/xr-luma-driver']},
                                    'verdict': {'result': 'PASS', 'exit': 0, 'reasons': []}}))
        self.env['LUMA_SOAK_BASELINE'] = str(path)

    def test_after_the_baseline_the_rust_soak_passes(self):
        self.run_driver(f'{PIN}/xr-luma-driver')
        code, out = self.soak('cpp')
        self.assertEqual(code, 0, out)
        self.assertIn('cpp baseline: VALID: 0 drop(s)', out)
        self.run_driver(f'{PIN}/rust/xr-luma-driver')
        self.env['LUMA_SOAK_SECONDS'] = '1.5'   # no shorter than the baseline: equal hours
        code, out = self.soak('rust')
        self.assertEqual(code, 0, out)
        self.assertIn('rust soak: VALID: 0 drop(s)', out)
        self.assertIn('drop gate: PASS', out)
        rust = json.loads(next(self.out.glob('rust-soak-*.report.json')).read_text())
        self.assertEqual(rust['coverage']['driver_exes'], [f'{PIN}/rust/xr-luma-driver'])

    def test_with_no_baseline_the_gate_proves_nothing(self):
        self.run_driver(f'{PIN}/rust/xr-luma-driver')
        code, out = self.soak('rust')
        self.assertEqual(code, 2, out)
        self.assertIn('rust soak: VALID', out)
        self.assertIn('drop gate: INCONCLUSIVE', out)

    def test_a_drop_more_than_the_baseline_fails(self):
        self.baseline(drops=0)
        self.run_driver(f'{PIN}/rust/xr-luma-driver')
        self.machine.audit.write_text(journal_line(time.time() + 0.5, DROP) + '\n')
        code, out = self.soak('rust')
        self.assertEqual(code, 1, out)
        self.assertIn('rust soak: VALID: 1 drop(s)', out)
        self.assertIn('drop gate: FAIL', out)

    def test_the_cpp_driver_is_not_a_rust_soak(self):
        self.baseline()
        self.run_driver(f'{PIN}/xr-luma-driver')
        code, out = self.soak('rust')
        self.assertEqual(code, 2, out)
        self.assertIn('rust soak: NOT VALID', out)
        self.assertIn('drop gate: INCONCLUSIVE', out)


if __name__ == '__main__':
    unittest.main()
