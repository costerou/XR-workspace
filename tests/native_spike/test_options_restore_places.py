"""The Restore mockups' stated ways to reach them, run through the real free_pointer.py ray and hit test at Cody's saved sizes.
Needs tools/options_restyle_mockups built (its README); skipped without it, because the meshes come from the real tile_mesh."""
import unittest

from tools.native_spike.unified_cursor import NUDGE_PX
from tools.options_restyle_mockups import checks as C, harness as h, maximize_options as X


@unittest.skipUnless(h.BINARY.exists(), 'restyle_preview not built')
class Reach(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reach = C.restore_reach('bottom-right')

    def test_cody_has_no_band_outside_a_maximized_picture(self):
        bands = C.letterbox([('XR-1', 2560, 1600), ('XR-2', 1920, 1200)] + h.OTHER_SIZES)
        self.assertEqual((bands['XR-1']['above'], bands['XR-2']['above']), (0, 0))
        self.assertEqual(bands['beast']['above'], 60)

    def test_a_flush_corner_target_is_reached_and_an_inset_one_is_not(self):
        for name, r in self.reach.items():
            self.assertTrue(r['pill_hit'] and r['chip_hit'], name)
            self.assertFalse(r['inset_rect_hit'], name)

    def test_the_hot_corner_survives_the_edge_nudge(self):
        self.assertGreater(X.ZONE_LOGICAL, NUDGE_PX)
        for name, r in self.reach.items():
            self.assertTrue(r['zone_at_corner'] and r['zone_after_nudge'], name)

    def test_nothing_outside_the_picture_is_reachable_today(self):
        for name, r in self.reach.items():
            self.assertEqual(r['band_hits_any_output_pixel'], 0, name)
        self.assertEqual([p.slug for p in X.PLACES if p.new_handoff], ['step-back-band'])

    def test_every_place_says_how_it_is_reached(self):
        self.assertTrue(3 <= len(X.PLACES) <= 4)
        for p in X.PLACES:
            self.assertTrue(p.reach and p.covers and p.build and p.test, p.name)


if __name__ == '__main__':
    unittest.main()
