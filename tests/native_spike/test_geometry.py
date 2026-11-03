import unittest

from tools.native_spike import geometry as g


class GeometryTests(unittest.TestCase):
    def test_exact_scale_table(self):
        self.assertTrue(g.exact_scale(1920, 1080, 1.25))
        self.assertTrue(g.exact_scale(3440, 1440, 1.25))
        self.assertFalse(g.exact_scale(3840, 1080, 1.75))
        self.assertFalse(g.exact_scale(1920, 1080, 1.75))
        self.assertTrue(g.exact_scale(1920, 1080, 2.5) and g.exact_scale(1920, 1080, 3.0))
        self.assertFalse(g.exact_scale(5120, 1440, 3.0))
        self.assertEqual(g.valid_scales(5120, 1440), [1.0, 1.25, 2.0, 2.5])
        self.assertEqual(g.valid_scales(3840, 1080), [1.0, 1.25, 1.5, 2.0, 2.5, 3.0])

    def test_validate_desktop_rejects(self):
        ok = {'width': 1920, 'height': 1080, 'scale': 1.0}
        for bad in (dict(ok, width=1921), dict(ok, width=7682), dict(ok, scale=3.5), dict(ok, scale=2.25), dict(ok, scale=4.0),
                    dict(ok, scale=1.75), dict(ok, width=True), dict(ok, scale=True),
                    dict(ok, extra=1), {'width': 1920, 'height': 1080}, [], None):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                g.validate_desktop(bad)

    def test_validate_desktop_normalizes(self):
        d = g.validate_desktop({'width': 3440, 'height': 1440, 'scale': 1})
        self.assertEqual(d, {'width': 3440, 'height': 1440, 'scale': 1.0})
        self.assertIs(type(d['scale']), float)
        self.assertEqual(g.logical_width(g.validate_desktop({'width': 3440, 'height': 1440, 'scale': 1.25})), 2752)

    def test_presets_exact(self):
        self.assertEqual(g.PRESETS, {'16:9': ((1920, 1080), (2560, 1440)),
                                     '21:9': ((2560, 1080), (3440, 1440)),
                                     '32:9': ((3840, 1080), (5120, 1440))})
