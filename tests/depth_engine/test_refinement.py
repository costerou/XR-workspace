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
