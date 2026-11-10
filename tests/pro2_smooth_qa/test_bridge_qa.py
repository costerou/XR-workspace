"""The real bridge against the simulated SDK fixture: flags fail closed. No vendor library, no USB."""
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
import unittest
from tests.pro2_bridge import build_bridge

ROOT = Path(__file__).resolve().parents[2]


@unittest.skipUnless(os.environ.get('PRO2_QA_NAMESPACE') == '1', 'run via tests/pro2_smooth_qa/run.py')
class Bridge(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(); root = cls.root = Path(cls.tmp.name)
        cls.usb = root / 'sysfs'; (cls.usb / '1-1').mkdir(parents=True)
        for name, value in dict(idVendor='35ca', idProduct='1301', product='VITURE PRO 2 XR Glasses', busnum='1', devnum='13').items(): (cls.usb / '1-1' / name).write_text(value + '\n')
        cls.sdk = root / 'fixture.so'; cls.binary = root / 'bridge'
        subprocess.run(['cc', '-shared', '-fPIC', '-pthread', 'tests/pro2_runtime_qa/sdk_fixture.c', '-o', str(cls.sdk)], cwd=ROOT, check=True, capture_output=True)
        build_bridge(cls.binary)
        cls.digest = hashlib.sha256(cls.sdk.read_bytes()).hexdigest(); cls.n = 0

    @classmethod
    def tearDownClass(cls): cls.tmp.cleanup()

    def start(self, *extra, rate=None):
        type(self).n += 1
        status = self.root / f'status{self.n}'
        sdk_fd = os.open(self.sdk, os.O_RDONLY); status_fd = os.open(status, os.O_CREAT | os.O_WRONLY, 0o600)
        env = dict(os.environ, **({'PRO2_QA_RATE': rate} if rate else {}))
        child = subprocess.Popen([str(self.binary), '--supervised-pro2-test', '--sdk-fd', str(sdk_fd), '--status-fd', str(status_fd), '--prefix', str(self.root / f'pose{self.n}_'), '--projection', '44.5',
                                  '--fixture-root', str(self.usb), '--fixture-hash', self.digest, *extra], pass_fds=(sdk_fd, status_fd), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        os.close(sdk_fd); os.close(status_fd)
        return child, status

    def run_it(self, *extra, rate=None):
        child, status = self.start(*extra, rate=rate)
        try: code = child.wait(timeout=8)
        finally:
            if child.poll() is None: child.kill(); child.wait()
        return code, [json.loads(l) for l in status.read_text().splitlines()]

    def test_long_deadline_only_with_the_panel_flag(self):
        self.assertEqual(self.run_it('--seconds', '301')[0], 2)
        self.assertEqual(self.run_it('--seconds', '43201', '--panel-route')[0], 2)
        self.assertEqual(self.run_it('--seconds', '.3', '--panel-route')[0], 0)

    def test_every_listed_rate_needs_the_sdk_yes(self):
        for hz, code in (('90', '1'), ('120', '2'), ('240', '3')):
            with self.subTest(hz=hz):
                self.assertNotEqual(self.run_it('--seconds', '.3', '--pose-rate', hz)[0], 0)
                done, events = self.run_it('--seconds', '.3', '--pose-rate', hz, rate=code)
                self.assertEqual((done, events[-1]['pose_rate_hz']), (0, int(hz)))

    def test_sigterm_mid_run_is_a_clean_done(self):
        child, status = self.start('--seconds', '30', '--panel-route')
        deadline = time.monotonic() + 5
        while b'ready' not in status.read_bytes() and time.monotonic() < deadline: time.sleep(.01)
        child.send_signal(signal.SIGTERM)
        self.assertEqual(child.wait(timeout=5), 0)
        done = json.loads(status.read_text().splitlines()[-1])
        self.assertEqual((done['event'], done['cleanup_errors'], done['error']), ('done', 0, ''))

    def test_a_rate_with_trailing_junk_is_refused(self):
        # std::stoi stops at the first non-digit. launch.py never sends this; the bridge's own check should still say no.
        self.assertEqual(self.run_it('--seconds', '.3', '--pose-rate', '120x', rate='2')[0], 2)
