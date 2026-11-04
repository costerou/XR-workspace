import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.depth_engine.temporal import TemporalFilter, luma_of  # noqa: E402

H, W = 64, 96
DT = 1 / 60


def scene():
    d = np.tile(np.linspace(0, 1, W, dtype=np.float32), (H, 1))
    return d, 0.2 + d * 0.5


class Flicker(unittest.TestCase):
    def test_noise_on_a_still_picture_is_reduced_fivefold(self):
        rng = np.random.default_rng(1)
        d, luma = scene()
        f = TemporalFilter()
        raw, out = [], []
        for _ in range(240):
            noisy = d + rng.normal(0, 0.012, d.shape).astype(np.float32)
            smooth, _, _ = f.update(noisy, luma, DT)
            raw.append(noisy)
            out.append(smooth.copy())
        before = np.std(np.stack(raw[60:]), axis=0).mean()
        after = np.std(np.stack(out[60:]), axis=0).mean()
        self.assertLess(after, before / 5)  # Stronger static EMA; motion and scene cuts still release it.

    def test_the_whole_map_jumping_in_scale_does_not_flicker(self):
        # What the model really does: same scene, different overall scale and offset from frame to frame.
        rng = np.random.default_rng(2)
        d, luma = scene()
        f = TemporalFilter()
        out = []
        for _ in range(240):
            s, _, _ = f.update(d * rng.uniform(0.9, 1.1) + rng.uniform(-0.1, 0.1), luma, DT)
            out.append(s.copy())
        self.assertLess(np.std(np.stack(out[60:]), axis=0).mean(), 0.02)

    def test_an_identical_map_stays_identical(self):
        d, luma = scene()
        f = TemporalFilter()
        first = f.update(d, luma, DT)[0].copy()
        for _ in range(30):
            last = f.update(d, luma, DT)[0]
        self.assertLess(np.abs(last - first).max(), 1e-3)


class Motion(unittest.TestCase):
    def test_a_moving_window_leaves_no_ghost(self):
        f = TemporalFilter()
        ghost = 0.0
        for x in range(10, 70):
            d = np.zeros((H, W), np.float32) + 0.1
            luma = np.zeros((H, W), np.float32) + 0.1
            d[20:44, x:x + 16] = 0.9
            luma[20:44, x:x + 16] = 0.8
            s, _, _ = f.update(d, luma, DT)
            if x > 20:
                ghost = max(ghost, float(s[32, x - 6]))  # Just behind the window's trailing edge.
                self.assertGreater(float(s[32, x + 8]), 0.8)  # The window itself is at depth straight away.
        self.assertLess(ghost, 0.15)

    def test_depth_change_with_the_same_picture_settles_quickly(self):
        d, luma = scene()
        f = TemporalFilter()
        f.update(d, luma, DT)
        moved = d[:, ::-1]  # Same picture, opposite depth.
        for _ in range(12):  # 200 ms at 60 Hz.
            s, _, _ = f.update(moved, luma, DT)
        self.assertLess(np.abs(s - moved).mean(), 0.05)


class Cuts(unittest.TestCase):
    def test_a_scene_cut_snaps_and_says_so(self):
        d, luma = scene()
        f = TemporalFilter()
        for _ in range(10):
            f.update(d, luma, DT)
        s, cut, _ = f.update(d[:, ::-1], 1 - luma * 1.6, DT)
        self.assertTrue(cut)
        self.assertLess(np.abs(s - d[:, ::-1]).mean(), 0.05)

    def test_the_first_frame_is_not_called_a_cut(self):
        d, luma = scene()
        self.assertFalse(TemporalFilter().update(d, luma, DT)[1])

    def test_a_new_size_starts_again(self):
        d, luma = scene()
        f = TemporalFilter()
        f.update(d, luma, DT)
        s, _, _ = f.update(d[::2, ::2], luma[::2, ::2], DT)
        self.assertEqual(s.shape, (H // 2, W // 2))


class Edges(unittest.TestCase):
    def test_a_blank_frame_is_flat_and_in_range(self):
        for value in (0.0, 1.0):
            s, _, flat = TemporalFilter().update(np.full((H, W), value, np.float32), np.full((H, W), value, np.float32), DT)
            self.assertTrue(flat)
            self.assertTrue((s == 0.5).all())

    def test_nan_from_the_model_never_reaches_the_renderer(self):
        d, luma = scene()
        d = d.copy()
        d[3, 3] = np.nan
        d[4, 4] = np.inf
        s, _, _ = TemporalFilter().update(d, luma, DT)
        self.assertTrue(np.isfinite(s).all())
        self.assertGreaterEqual(float(s.min()), 0)
        self.assertLessEqual(float(s.max()), 1)

    def test_output_stays_in_range(self):
        rng = np.random.default_rng(4)
        f = TemporalFilter()
        for _ in range(50):
            s, _, _ = f.update(rng.normal(0, 3, (H, W)).astype(np.float32), rng.random((H, W)).astype(np.float32), DT)
            self.assertGreaterEqual(float(s.min()), 0)
            self.assertLessEqual(float(s.max()), 1)

    def test_a_busy_picture_with_nothing_still_keeps_its_depth(self):
        # Every pixel changes a little, not enough for a cut, so nothing can be aligned on, and the model's raw units are
        # far from 0..1. The map must keep its shape, not clip to black and white.
        d, luma = scene()
        f = TemporalFilter()
        f.update(d * 40 + 90, luma, DT)
        for i in range(1, 10):
            s, cut, _ = f.update(d * 40 + 90, luma + (i % 2) * 0.05, DT)
            self.assertFalse(cut)
        self.assertLess(np.abs(s - d).mean(), 0.05)

    def test_a_blank_frame_does_not_stick(self):
        d, luma = scene()
        f = TemporalFilter()
        f.update(np.zeros((H, W), np.float32), luma, DT)  # Flat first, with the picture already the same brightness.
        for _ in range(30):
            s, _, flat = f.update(d, luma, DT)
        self.assertFalse(flat)
        self.assertLess(np.abs(s - d).mean(), 0.05)

    def test_luma_of_rgba(self):
        px = np.zeros((2, 2, 4), np.uint8)
        px[..., 1] = 255
        self.assertAlmostEqual(float(luma_of(px)[0, 0]), 0.5, places=5)


if __name__ == "__main__":
    unittest.main()
