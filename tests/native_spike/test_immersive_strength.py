import unittest
from tools.native_spike import immersive

class DesktopStrengthTests(unittest.TestCase):
    def test_roundtrip(self):
        setting = {**immersive.DEFAULTS, 'strengths': {'XR-2': 63}}
        self.assertTrue(immersive.is_valid(setting))
        self.assertEqual(immersive.clean(setting), setting)
        self.assertEqual(immersive.wire_params(setting)['strengths'], [100, 63, 100, 100])
        self.assertEqual(immersive.toggled_desktop(setting, 'XR-1')['strengths'], {'XR-2': 63})

    def test_bad_strength_is_rejected(self):
        for value in (-1, 201, True, '60'):
            self.assertFalse(immersive.is_valid({**immersive.DEFAULTS, 'strengths': {'XR-2': value}}))
