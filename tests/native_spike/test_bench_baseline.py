"""The phase 0 baseline harness (tools/native_spike/bench_baseline.py) measures what it says: pidstat-style CPU and
VmRSS read from /proc, child processes found, and the overview bench's key press -> renderer frame timing.

Offline only: throwaway child processes and bench_control's fake compositor and renderer.
"""
import os
import subprocess
import sys
import time
import unittest

from tools.native_spike import bench_baseline as bb, bench_control as bc


class SamplerTests(unittest.TestCase):
    def spawn(self, code):
        p = subprocess.Popen([sys.executable, '-I', '-c', code], stdin=subprocess.DEVNULL)
        self.addCleanup(lambda: (p.kill(), p.wait()))
        return p

    def test_busy_process_reads_as_busy_and_has_memory(self):
        p = self.spawn('while True: pass')
        s = bb.Sampler(p.pid)
        for _ in range(3):
            time.sleep(0.3); s.sample()
        r = s.finish()
        self.assertFalse(r['exited'])
        self.assertGreater(r['cpu_pct'], 30)
        self.assertGreater(r['usr_pct'], r['system_pct'])
        self.assertEqual(r['cpu_pct_per_second']['n'], 3)
        self.assertGreater(r['vmrss_kib'], 1000)
        self.assertGreaterEqual(r['vmhwm_kib'], r['vmrss_kib'])
        self.assertEqual(r['vmrss_plus_swap_kib'], r['vmrss_kib'] + r['vmswap_kib'])
        self.assertEqual(r['vmrss_kib'], r['rssanon_kib'] + r['rssfile_kib'] + r['rssshmem_kib'])

    def test_idle_process_reads_as_idle(self):
        p = self.spawn('import time; time.sleep(60)')
        time.sleep(0.2)
        s = bb.Sampler(p.pid)
        time.sleep(0.5); s.sample()
        self.assertLess(s.finish()['cpu_pct'], 5)

    def test_exited_process_is_reported_not_raised(self):
        p = self.spawn('pass'); p.wait()
        s = bb.Sampler(os.getpid())
        s.pid = p.pid
        s.sample()
        r = s.finish()
        self.assertTrue(r['exited'])
        self.assertIsNone(r['vmrss_kib'])
        self.assertIsNone(r['vmrss_plus_swap_kib'])

    def test_descendants_include_grandchildren(self):
        p = self.spawn('import subprocess, sys, time; subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"]);'
                       ' time.sleep(30)')
        deadline = time.monotonic() + 5
        while len(bb.descendants(p.pid)) < 1 and time.monotonic() < deadline: time.sleep(0.02)
        kids = bb.descendants(p.pid)
        self.assertEqual(len(kids), 1)
        self.addCleanup(lambda: [os.kill(k, 9) for k in kids])
        self.assertNotIn(p.pid, kids)

    def test_live_lookup_skips_excluded_and_matches_roles(self):
        found = bb.live_processes({os.getpid()})
        self.assertTrue(set(found) <= set(bb.LIVE))
        self.assertTrue(bb.LIVE['renderer']('xr-native-spike', ''))
        self.assertTrue(bb.LIVE['supervisor']('python3', "runpy.run_module('tools.xr_controller.runner')"))
        self.assertTrue(bb.LIVE['driver']('4', '/proc/self/fd/4 --supervised-pro2-test --sdk-fd 3'))
        self.assertFalse(bb.LIVE['supervisor']('python3', 'tools.xr_controller serve'))


class OverviewKeyPressTests(unittest.TestCase):
    def test_key_presses_reach_the_renderer_while_the_overview_is_open(self):
        r = bc.bench_overview(True, window=2.5, press_every=0.5)
        self.assertEqual(r['key_presses_unanswered'], 0)
        self.assertGreaterEqual(r['key_press_to_frame_ms']['n'], 3)
        self.assertGreater(r['key_press_to_frame_ms']['p50'], 0)
        self.assertEqual(r['renderer_frames']['recenter'], r['key_press_to_frame_ms']['n'])
        self.assertGreater(r['press_spawn_cpu_pct'], 0)
        self.assertGreater(r['samples_per_s'], 0)

    def test_without_presses_the_result_is_unchanged(self):
        r = bc.bench_overview(True, window=1.0)
        self.assertNotIn('key_press_to_frame_ms', r)
        self.assertNotIn('recenter', r['renderer_frames'])


if __name__ == '__main__':
    unittest.main()
