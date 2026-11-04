import unittest
import numpy as np
from tools.stereo_eval.metrics import edge_artifact, temporal_flicker

class EdgeMetrics(unittest.TestCase):
    def test_clean_shift_beats_doubled_edge(self):
        source = np.zeros((64, 128, 3), np.uint8)
        source[:, 50:80] = 240
        clean = np.roll(source, 3, axis=1)
        bad = clean.copy(); bad[:, 44:48] = 120
        self.assertLess(edge_artifact(source, clean)['mean_residual'], edge_artifact(source, bad)['mean_residual'])

    def test_flicker_ignores_source_motion_but_catches_static_swimming(self):
        rng = np.random.default_rng(5)
        source = rng.integers(0, 255, (64, 128, 3), dtype=np.uint8)
        still = [source] * 3
        swimming = [source, np.roll(source, 1, axis=1), source]
        self.assertEqual(temporal_flicker(still, still)['mean_rgb_change'], 0)
        self.assertGreater(temporal_flicker(still, swimming)['mean_rgb_change'], .01)
        self.assertEqual(temporal_flicker(swimming, swimming)['mean_rgb_change'], 0)
