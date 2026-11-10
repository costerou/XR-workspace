"""tools/luma_soak/compare.py: the D13 drop gate, a Rust soak report against the C++ baseline report."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tools.luma_soak.compare import compare

REPO = Path(__file__).resolve().parents[2]
RUST = '/data/bin/0123456789ab/rust/xr-luma-driver'
CPP = '/data/bin/0123456789ab/xr-luma-driver'


def report(drops, hours=3.0, exes=(CPP,), valid=True):
    return {'drops': drops, 'duration_s': hours * 3600, 'drops_per_hour': round(drops / hours, 2),
            'coverage': {'driver_exes': list(exes)},
            'verdict': {'result': 'PASS', 'exit': 0, 'reasons': []} if valid else
                       {'result': 'INCONCLUSIVE', 'exit': 2, 'reasons': ['ran 600.0 s of the required 10800 s']}}


def rust(drops, hours=3.0, **extra):
    return report(drops, hours, exes=(RUST,), **extra)


class CompareTests(unittest.TestCase):
    def test_equal_drops_pass(self):
        result = compare(report(2), rust(2))
        self.assertEqual((result['result'], result['exit']), ('PASS', 0))
        self.assertEqual(result['allowed'], 2.0)

    def test_zero_against_zero_passes(self):
        self.assertEqual(compare(report(0), rust(0))['exit'], 0)

    def test_one_more_drop_fails(self):
        result = compare(report(1), rust(2))
        self.assertEqual((result['result'], result['exit']), ('FAIL', 1))
        self.assertIn('Rust 2 drop(s) in 3.00 h, C++ 1 in 3.00 h', result['reasons'][0])

    def test_any_drop_fails_against_a_clean_baseline(self):
        self.assertEqual(compare(report(0), rust(1))['exit'], 1)

    def test_drops_scale_to_equal_hours(self):
        self.assertEqual(compare(report(2), rust(4, hours=6.0))['exit'], 0)   # the same rate over twice the hours
        self.assertEqual(compare(report(2), rust(5, hours=6.0))['exit'], 1)

    def test_fewer_hours_than_the_baseline_prove_nothing(self):
        result = compare(report(0, hours=3.0), rust(0, hours=2.5))
        self.assertEqual(result['exit'], 2)
        self.assertIn('not equal hours', result['reasons'][0])

    def test_an_invalid_soak_proves_nothing(self):
        for baseline, candidate in ((report(0, valid=False), rust(0)), (report(0), rust(0, valid=False))):
            result = compare(baseline, candidate)
            self.assertEqual(result['exit'], 2)
            self.assertIn('not a valid measurement: ran 600.0 s', result['reasons'][0])

    def test_the_builds_must_be_the_right_way_round(self):
        result = compare(rust(0), report(0))
        self.assertEqual(result['exit'], 2)
        self.assertEqual(len(result['reasons']), 2, result['reasons'])

    def test_a_rust_soak_needs_its_binary_on_record(self):
        result = compare(report(0, exes=()), rust(0) | {'coverage': {'driver_exes': []}})
        self.assertEqual(result['exit'], 2)
        self.assertIn('Rust soak: no record of which driver binary ran', result['reasons'][0])

    def test_the_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            base, cand = Path(tmp, 'b.json'), Path(tmp, 'c.json')
            base.write_text(json.dumps(report(1)))
            for drops, code, word in ((1, 0, 'PASS'), (2, 1, 'FAIL')):
                cand.write_text(json.dumps(rust(drops)))
                done = subprocess.run([sys.executable, '-m', 'tools.luma_soak', 'compare', '--baseline', str(base), '--candidate', str(cand)],
                                      cwd=REPO, capture_output=True, text=True, timeout=60)
                self.assertEqual(done.returncode, code, done)
                self.assertTrue(done.stdout.startswith(f'drop gate: {word}\n'), done.stdout)


if __name__ == '__main__':
    unittest.main()
