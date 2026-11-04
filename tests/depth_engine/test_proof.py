"""tools/immersive_proof: the parts of the offline proof that are logic, not GPU. The run itself is in docs/immersive-3d.md."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools.immersive_proof import __main__ as proof  # noqa: E402


class Pictures(unittest.TestCase):
    def test_a_clip_turns_round_instead_of_jumping_back(self):
        frames = [np.full((2, 3, 3), i, np.uint8) for i in range(4)]
        with tempfile.TemporaryDirectory() as d:
            listing = proof.write_pictures(frames, Path(d) / 'in')
            order = [Path(line.split()[2]).stem for line in listing.read_text().splitlines()]
            self.assertEqual(order, ['0000', '0001', '0002', '0003', '0002', '0001'])
            self.assertEqual(len(Path(d, 'in', '0001.rgba').read_bytes()), 2 * 3 * 4)
            self.assertEqual(Path(d, 'in', '0001.rgba').read_bytes()[:4], bytes([1, 1, 1, 255]))

    def test_one_picture_stays_one_picture(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(len(proof.write_pictures([np.zeros((2, 3, 3), np.uint8)], Path(d) / 'in').read_text().splitlines()), 1)

    def test_the_presets_it_draws_with_are_the_run_s(self):
        self.assertEqual(proof.look_options('video'), {'strength': 100, 'convergence': 70, 'near': 14, 'far': 36, 'text': 100, 'range_ms': 1000})

    def test_red_from_the_left_eye_and_cyan_from_the_right(self):
        sbs = np.zeros((1, 2, 3), np.uint8)
        sbs[0, 0] = (200, 1, 2); sbs[0, 1] = (3, 100, 50)
        self.assertEqual(proof.anaglyph(sbs).tolist(), [[[200, 100, 50]]])


class Refusal(unittest.TestCase):
    def test_it_will_not_load_the_gpu_under_a_session(self):
        with mock.patch.object(proof, 'xr_is_up', lambda: True), self.assertRaisesRegex(SystemExit, 'XR session'):
            proof.main(['--out', '/nonexistent'])

    def test_a_running_unit_counts(self):
        done = subprocess.CompletedProcess([], 0, stdout='viture-xr-abc.service loaded active running x\n')
        with mock.patch.object(subprocess, 'run', lambda *a, **k: done):
            self.assertTrue(proof.xr_is_up())
        with mock.patch.object(subprocess, 'run', lambda *a, **k: subprocess.CompletedProcess([], 0, stdout='')):
            self.assertFalse(proof.xr_is_up())


if __name__ == '__main__':
    unittest.main()
