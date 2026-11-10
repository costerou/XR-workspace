"""Is the offline evidence what docs/pro2/smoothness.md says it is? Aggregates and synthetic heads only."""
import json
import math
from pathlib import Path
import subprocess
import tempfile
import unittest
from tools.pro2_runtime import smooth_acceptance as A

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = json.loads((ROOT / 'docs/pro2/smooth-evidence/replay-aggregates.json').read_text())


def quat(deg): return (0.0, 0.0, math.sin(math.radians(deg) / 2), math.cos(math.radians(deg) / 2))


class Metrics(unittest.TestCase):
    def rows(self, dps=30.0):
        out, t, i = [], 0.0, 0
        while t < 4000:
            out.append((t + .5, t, quat(dps * t / 1000))); t += (15.0, 15.0, 20.0)[i % 3]; i += 1
        return out

    def score(self, shown):
        rows = self.rows()
        frames = [dict(present=40 + 8.3333 * k, device=0.0, q=shown(40 + 8.3333 * k), horizon=0.0, predicted=0.0) for k in range(400)]
        return A.summary(A.pool([A.measure(rows, (0, 0, 0, 1), frames)]))

    def test_lag_reads_a_known_delay(self):
        for delay in (0.0, 5.0, 12.0, 30.0):
            s = self.score(lambda t: quat(30 * (t - .5 - delay) / 1000))
            with self.subTest(delay=delay): self.assertLess(abs(s['lag_ms'] - delay), .3, s)

    def test_a_view_that_leads_reads_negative(self):
        self.assertLess(self.score(lambda t: quat(30 * (t + 9.5) / 1000))['lag_ms'], -9)

    def test_every_other_frame_held_is_near_fifty_percent_stuck(self):
        def held(t): return quat(30 * (40 + 16.6666 * ((t - 40) // 16.6666)) / 1000)
        self.assertGreater(self.score(held)['stuck_pct'], 40)

    def test_a_periodic_judder_shows_in_jerk(self):
        calm = self.score(lambda t: quat(30 * t / 1000))['jerk_p95']
        rough = self.score(lambda t: quat(30 * t / 1000 + .1 * math.sin(t / 8.3333 * math.pi)))['jerk_p95']
        self.assertGreater(rough, calm + .1)


class Order(unittest.TestCase):
    def test_the_replay_keeps_the_renderers_order_of_operations(self):
        wayland = (ROOT / 'spike/src/wayland.cpp').read_text()
        draw = wayland[wayland.index('Status Presentation::draw_test_pattern()'):]
        replay = (ROOT / 'spike/tests/pro2_smooth_replay.cpp').read_text()
        def order(text, names):
            at = [text.index(n) for n in names]
            self.assertEqual(at, sorted(at), names)
        order(draw, ['s.stabilization.update(', 's.pose.update(', 's.pose.relative()', 's.pose_age.observe(', 'const auto horizon = prediction_horizon(', 's.smoother.update(', 's.modes.update(relative'])
        order(replay, ['stabilizer.update(', 'pose.update(', 'pose.relative()', 'age.observe(', 'const auto horizon = prediction_horizon(', 'smoother.update('])

    def test_both_give_the_smoother_the_same_clock_and_the_raw_stamp(self):
        # Both also pass the bridge's wire velocity (D12), empty when the wire is absent.
        wayland = (ROOT / 'spike/src/wayland.cpp').read_text()
        self.assertIn('s.smoother.update(relative, sample->device_ms, *horizon, double(read_ns) / 1e6, measured)', wayland)
        self.assertIn('smoother.update(relative, f.device_ms, *horizon, read, measured)', (ROOT / 'spike/tests/pro2_smooth_replay.cpp').read_text())

    def test_the_capture_axes_are_the_bridges(self):
        # bridge: Quatf{w,x,y,z} = {v3,-v6,-v4,v5}. load_capture: (x,y,z,w) = (-v6,-v4,v5,v3).
        self.assertIn('{{float(s.v[3]/norm),float(-s.v[6]/norm),float(-s.v[4]/norm),float(s.v[5]/norm)}', (ROOT / 'tools/pro2_runtime/bridge.cpp').read_text())
        self.assertRegex((ROOT / 'spike/driver/include/luma/types.hpp').read_text(), r'float w = 1, x = 0, y = 0, z = 0')
        self.assertIn('(-v[6], -v[4], v[5], v[3])', (ROOT / 'tools/pro2_runtime/smooth_acceptance.py').read_text())


class Replay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.binary = Path(cls.tmp.name) / 'replay'
        subprocess.run(['c++', '-std=c++20', '-O1', '-pthread', '-Ispike/include', 'spike/tests/pro2_smooth_replay.cpp', 'spike/src/motion_smooth.cpp', 'spike/src/predict.cpp', 'spike/src/pose.cpp', '-o', str(cls.binary)], cwd=ROOT, check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls): cls.tmp.cleanup()

    def test_synthetic_gate_passes_and_means_something(self):
        rows = A.synthetic(10)
        before = A.evaluate(self.binary, rows, mode='hold')
        after = A.evaluate(self.binary, rows, mode='smooth')
        self.assertGreater(before['stuck_pct'], 30); self.assertLess(after['stuck_pct'], before['stuck_pct'] / 5)
        self.assertLess(after['lag_ms'], before['lag_ms'])

    def test_a_badly_tuned_smoother_fails_the_limits(self):
        rows = A.synthetic(10)
        self.assertGreater(A.evaluate(self.binary, rows, mode='smooth', tau=80.0)['lag_ms'], A.evaluate(self.binary, rows, mode='smooth')['lag_ms'])


class Evidence(unittest.TestCase):
    def test_committed_parameters_are_the_shipped_ones(self):
        self.assertEqual({k: EVIDENCE['parameters'][k] for k in A.SHIPPED}, A.SHIPPED)

    def test_no_failed_limit_is_hidden(self):
        self.assertEqual(EVIDENCE['failed'], [])

    def test_the_doc_table_is_the_json(self):
        doc = (ROOT / 'docs/pro2/smoothness.md').read_text()
        r = EVIDENCE['results']['directional']
        self.assertIn(f"| Frames that did not move | {r['hold']['stuck_pct']:.1f}% | {r['legacy_prediction']['stuck_pct']:.1f}% | {r['smoothed']['stuck_pct']}% | 2% |", doc)

    @unittest.expectedFailure  # a real limit of the evidence, stated in docs/pro2/smoothness.md: only more captures can close it
    def test_worst_overshoot_rests_on_enough_distinct_stops(self):
        # `stops` is pooled over four grid phases of one capture, so 16 is four physical stops.
        physical = {name: EVIDENCE['results'][name]['smoothed']['stops'] / len(A.PHASES_MS) for name in ('directional', 'roll')}
        self.assertGreaterEqual(sum(physical.values()), 20, physical)
