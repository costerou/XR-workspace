import unittest
import numpy as np
from tools.depth_engine.refinement import refine

class RefinementTests(unittest.TestCase):
    def test_noise_and_colour_boundary(self):
        rng = np.random.default_rng(7)
        truth = np.zeros((80, 100), np.float32)
        truth[:, 50:] = 1
        noisy = truth + rng.normal(0, .04, truth.shape)
        result = refine(noisy, truth)
        self.assertLess(np.mean((result-truth)**2), np.mean((noisy-truth)**2))
        self.assertGreater(np.mean(result[:,50] - result[:,49]), .9)
        self.assertTrue(np.isfinite(refine(noisy * np.nan, truth)).all())

    def test_constant_is_unchanged(self):
        d = np.full((32, 48), 7., np.float32)
        np.testing.assert_allclose(refine(d, d / 10), d, atol=1e-5)

    def test_blurred_depth_edge_snaps_to_colour(self):
        truth = np.zeros((48, 96), np.float32); truth[:, 48:] = 1
        depth = np.broadcast_to(np.clip((np.arange(96) - 43) / 16, 0, 1), truth.shape).copy()
        result = refine(depth, truth)
        # An edge four pixels out of register should follow the visible boundary.
        self.assertGreater(float(np.mean(result[:, 48] - result[:, 47])), .15)

    def test_disparity_step_ceiling_preserves_constant_planes(self):
        from tools.depth_engine.refinement import limit_steps
        step = np.zeros((16, 32), np.float32); step[:, 16:] = 1
        out = limit_steps(step)
        self.assertLessEqual(float(np.abs(np.diff(out, axis=1)).max()), .30001)
        self.assertLessEqual(float(np.abs(np.diff(out, axis=0)).max()), .30001)
        self.assertEqual(float(out[8, 0]), 0.)
        self.assertEqual(float(out[8, -1]), 1.)
        np.testing.assert_array_equal(limit_steps(step * 0 + .7), step * 0 + .7)
