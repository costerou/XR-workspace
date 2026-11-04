"""Private restart memory; the loopback compositor never contacts the desktop."""
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from tools.native_spike import managed, survive
from tests.native_spike.test_survive import Loop

ROOT = Path(__file__).resolve().parents[2]

class ContinuityTests(unittest.TestCase):
    def test_only_expected_mode_switch_retains_private_memory(self):
        for expected in (False, True):
            with self.subTest(expected=expected):
                fd = os.memfd_create('test-view-continuity')
                self.addCleanup(os.close, fd)
                loop = Loop([(1, None, False), (0, None, True)])
                seen = []
                def launch():
                    seen.append(os.pread(fd, 20, 0))
                    os.pwrite(fd, b'pose snapshot', 0)
                    return loop.launch()
                with patch.object(survive, 'usb_identity', return_value=(1, 2)), \
                     patch.object(survive, 'output_ok', return_value=False), \
                     patch.object(survive, 'wait_for_glasses', return_value=True):
                    managed.keep_renderer(launch, loop, 'DP-2', loop.report, None, loop.restore,
                        loop.holds, switching=lambda: expected, continuity_fd=fd, tell=lambda _: None)
                if expected: self.assertEqual(seen, [b'', b'pose snapshot'])
                else: self.assertEqual(seen, [b'', b''])

    def test_usb_reenumeration_during_a_pending_switch_discards_pose(self):
        for identities in ([(1, 2), (1, 3), (1, 3), (1, 3), (1, 3)],
                           [(1, 2), (1, 2), (1, 3), (1, 3), (1, 3)]):
            fd = os.memfd_create('test-usb-continuity')
            self.addCleanup(os.close, fd)
            loop = Loop([(1, None, False), (0, None, True)])
            seen = []
            def launch():
                seen.append(os.pread(fd, 20, 0)); os.pwrite(fd, b'pose snapshot', 0)
                return loop.launch()
            with patch.object(survive, 'usb_identity', side_effect=identities), \
                 patch.object(survive, 'output_ok', return_value=False), \
                 patch.object(survive, 'wait_for_glasses', return_value=True):
                managed.keep_renderer(launch, loop, 'DP-2', loop.report, None, loop.restore,
                    loop.holds, switching=lambda: True, continuity_fd=fd, tell=lambda _: None)
            self.assertEqual(seen, [b'', b''])

    def test_real_renderer_writes_then_restores_on_private_loopback(self):
        binary = ROOT / 'build/release/xr-native-spike'
        bench = ROOT / 'build/release/capture_e2e_bench'
        if not binary.is_file() or not bench.is_file(): self.skipTest('build renderer and loopback first')
        original = os.memfd_create('test-renderer-continuity')
        fd = fcntl.fcntl(original, fcntl.F_DUPFD_CLOEXEC, 50)
        os.close(original); self.addCleanup(os.close, fd)
        with tempfile.TemporaryDirectory() as tmp:
            wrapper = Path(tmp) / 'renderer'
            wrapper.write_text(f'#!{sys.executable}\nimport os, sys\nos.execv({str(binary)!r}, [{str(binary)!r}, *sys.argv[1:], "--continuity-fd", "{fd}"])\n')
            wrapper.chmod(0o700)
            results = []
            for _ in range(3):
                p = subprocess.run([str(bench), '--renderer', str(wrapper), '--mode', 'cpu',
                                    '--seconds', '0.15', '--warmup', '0.1'],
                                   pass_fds=(fd,), capture_output=True, text=True, timeout=20)
                self.assertEqual(p.returncode, 0, p.stderr)
                results.append(json.loads(p.stdout))
                self.assertEqual(os.pread(fd, 8, 0), b'XRCONT01')
            self.assertNotIn('continuity: restored', results[0].get('renderer_stderr', []))
            for result in results[1:]:
                self.assertIn('continuity: restored', result.get('renderer_stderr', []))
