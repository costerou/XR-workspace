import ctypes
import math
import os
import random
import struct
import tempfile
import unittest
from pathlib import Path

from tools.luma_probe.feed import Row, analyse, angle_deg, attitude, attitude_lines, compare, decode, load, read_segment, record


def yaw(degrees):
    half = math.radians(degrees) / 2
    return (0.0, 0.0, math.sin(half), math.cos(half))


def still_rows(seconds, rate=1000, drift_deg_per_min=0.0, start_s=0.0, q=None):
    rows = []
    for i in range(int(seconds * rate)):
        t = start_s + i / rate
        rows.append(Row(int(t * 1e9), t * 1000, *(q or yaw(drift_deg_per_min * t / 60))))
    return rows


class AttitudeTests(unittest.TestCase):
    def row(self, w, x, y, z):
        return Row(0, 0.0, x, y, z, w)

    def half(self, degrees):
        return math.cos(math.radians(degrees) / 2), math.sin(math.radians(degrees) / 2)

    def test_signs_are_the_ones_the_plan_reads_off(self):
        c, s = self.half(30)
        self.assertAlmostEqual(attitude(self.row(c, 0, 0, s))[0], 30)   # about up: turned left
        self.assertAlmostEqual(attitude(self.row(c, 0, s, 0))[1], 30)   # about left: nose down
        self.assertAlmostEqual(attitude(self.row(c, s, 0, 0))[2], 30)   # about forward: right ear down

    def test_one_line_per_second(self):
        rows = [Row(i * 100_000_000, float(i), 0.0, 0.0, 0.0, 1.0) for i in range(35)]
        self.assertEqual(len(attitude_lines(rows)), 4)


class DecodeTests(unittest.TestCase):
    def raw(self, q=(0, 0, 0, 1), ms=123.0):
        return struct.pack('<16f', *q, *q, *q, ms, ms - 16, ms - 32, 0)

    def test_reads_newest_quaternion_and_timestamp(self):
        self.assertEqual(decode(self.raw(), 5), Row(5, 123.0, 0.0, 0.0, 0.0, 1.0))

    def test_rejects_a_torn_quaternion(self):
        self.assertIsNone(decode(self.raw(q=(0.9, 0.9, 0, 0.9)), 5))

    def test_rejects_nan(self):
        self.assertIsNone(decode(self.raw(ms=float('nan')), 5))
        self.assertIsNone(decode(self.raw(q=(float('nan'), 0, 0, 1)), 5))

    def test_rejects_short_input(self):
        self.assertIsNone(decode(b'\0' * 12, 5))


class AnalyseTests(unittest.TestCase):
    def test_rate_at_one_kilohertz(self):
        self.assertAlmostEqual(analyse(still_rows(5))['rate_hz'], 1000, delta=1)

    def test_counts_a_gap(self):
        rows = still_rows(1) + still_rows(1, start_s=1.5)
        result = analyse(rows)
        self.assertEqual(result['gaps_over_100ms'], 1)
        self.assertAlmostEqual(result['max_gap_ms'], 501, delta=2)

    def test_still_drift_in_degrees_per_minute(self):
        result = analyse(still_rows(60, rate=100, drift_deg_per_min=2.0))
        self.assertAlmostEqual(result['still_drift_deg_per_min'], 2.0, delta=0.1)

    def test_return_error_after_a_move_away_and_back(self):
        rows = still_rows(20, rate=100, q=yaw(0)) + still_rows(20, rate=100, start_s=20, q=yaw(45)) \
            + still_rows(20, rate=100, start_s=40, q=yaw(1.5))
        self.assertAlmostEqual(analyse(rows)['return_error_deg'], 1.5, delta=0.05)

    def test_noise_does_not_become_drift(self):
        rng = random.Random(1)
        rows = [Row(i * 10_000_000, i * 10.0, *yaw(rng.uniform(-0.1, 0.1))) for i in range(6000)]
        self.assertLess(analyse(rows)['still_drift_deg_per_min'], 0.05)

    def test_arrival_spread_is_zero_for_steady_arrivals_whatever_the_clock_offset(self):
        rows = [r._replace(device_ms=r.device_ms + 5000) for r in still_rows(2)]
        self.assertAlmostEqual(analyse(rows)['arrival_spread_ms'], 0, delta=0.01)

    def test_arrival_spread_sees_late_samples(self):
        rows = still_rows(2)
        late = [r._replace(host_ns=r.host_ns + 3_000_000) if i % 10 == 0 else r for i, r in enumerate(rows)]
        self.assertAlmostEqual(analyse(late)['arrival_spread_ms'], 3, delta=0.01)

    def test_too_few_rows_is_an_error_not_a_number(self):
        with self.assertRaises(ValueError):
            analyse(still_rows(0.001))

    def test_angle_handles_the_double_cover(self):
        q = yaw(10)
        self.assertAlmostEqual(angle_deg(q, tuple(-v for v in q)), 0, delta=1e-3)


class CompareTests(unittest.TestCase):
    OLD = {'rate_hz': 1000, 'max_gap_ms': 5, 'gaps_over_100ms': 0, 'still_drift_deg_per_min': 0.3, 'return_error_deg': 2.0}

    def verdict(self, **changes):
        return compare(self.OLD, {**self.OLD, **changes})[0]

    def test_equal_runs_pass(self):
        passed, lines = compare(self.OLD, self.OLD)
        self.assertTrue(passed)
        self.assertEqual(len(lines), 5)

    def test_any_gap_fails(self):
        self.assertFalse(self.verdict(gaps_over_100ms=1))

    def test_rate_within_recorder_noise_passes(self):
        self.assertTrue(self.verdict(rate_hz=985))

    def test_rate_below_the_old_driver_fails(self):
        self.assertFalse(self.verdict(rate_hz=975))

    def test_a_hitch_over_twenty_milliseconds_fails(self):
        self.assertFalse(self.verdict(max_gap_ms=21))

    def test_drift_within_noise_passes(self):
        self.assertTrue(self.verdict(still_drift_deg_per_min=0.38))

    def test_worse_drift_fails(self):
        self.assertFalse(self.verdict(still_drift_deg_per_min=0.45))

    def test_drift_over_the_absolute_limit_fails_even_if_the_old_driver_is_as_bad(self):
        old = {**self.OLD, 'still_drift_deg_per_min': 0.7}
        self.assertFalse(compare(old, old)[0])

    def test_return_error_within_placement_noise_passes(self):
        self.assertTrue(self.verdict(return_error_deg=2.4))

    def test_worse_return_error_fails(self):
        self.assertFalse(self.verdict(return_error_deg=2.6))


class SegmentTests(unittest.TestCase):
    """A real SysV segment, created the way the driver creates it."""
    IPC_CREAT, IPC_RMID = 0o1000, 0

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'feed_pose_orientation'
        self.path.write_text('')
        self.libc = ctypes.CDLL(None, use_errno=True)
        self.libc.shmat.restype = ctypes.c_void_p
        self.libc.shmat.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
        self.libc.shmdt.argtypes = [ctypes.c_void_p]
        self.libc.shmctl.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
        self.libc.ftok.argtypes = [ctypes.c_char_p, ctypes.c_int]
        key = self.libc.ftok(os.fsencode(self.path), 0)
        self.id = self.libc.shmget(key, 64, self.IPC_CREAT | 0o600)
        self.assertGreaterEqual(self.id, 0)
        self.addCleanup(lambda: self.libc.shmctl(self.id, self.IPC_RMID, None))
        self.addr = self.libc.shmat(self.id, None, 0)
        self.addCleanup(lambda: self.libc.shmdt(self.addr))

    def put(self, ms):
        ctypes.memmove(self.addr, struct.pack('<16f', 0, 0, 0, 1, *([0] * 8), ms, 0, 0, 0), 64)

    def test_reads_what_the_driver_wrote(self):
        self.put(42.0)
        with read_segment(str(self.path)) as read:
            self.assertEqual(decode(read(), 1).device_ms, 42.0)

    def test_attachment_is_read_only(self):
        self.put(1.0)
        with read_segment(str(self.path)):
            maps = Path('/proc/self/maps').read_text().splitlines()
        shared = [line.split()[1] for line in maps if 'SYSV' in line]
        self.assertIn('r--s', shared)  # the writable one is this test's own

    def test_records_only_new_samples(self):
        self.put(1.0)
        out = Path(self.tmp.name) / 'run.csv'
        values = iter([1.0, 1.0, 2.0, 2.0, 3.0])
        ticks = iter(range(0, 10_000))

        def clock():
            value = next(values, None)
            if value is not None:
                self.put(value)
            return next(ticks) * 1_000_000

        count = record(str(self.path), out, seconds=0.004, poll_s=0, clock=clock)
        self.assertEqual(count, 3)
        self.assertEqual([r.device_ms for r in load(out)], [1.0, 2.0, 3.0])

    def test_absent_feed_says_so(self):
        with self.assertRaisesRegex(ValueError, 'feed'):
            with read_segment(self.tmp.name + '/missing'):
                pass

    def test_refuses_to_overwrite_a_recording(self):
        self.put(1.0)
        out = Path(self.tmp.name) / 'run.csv'
        out.write_text('keep me')
        with self.assertRaises(FileExistsError):
            record(str(self.path), out, seconds=0.001, poll_s=0)
        self.assertEqual(out.read_text(), 'keep me')
