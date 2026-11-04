"""Offline identity/isolation/access tests; synthetic inventory is not hardware proof."""
import json
import tempfile
import unittest
from pathlib import Path
from tools.xr_controller.detect import identify
from tools.native_spike import profiles, view
from tools.luma_probe.udev import covers, RULE_TEXT


class Pro2PlatformTests(unittest.TestCase):
    def test_exact_identity_does_not_assert_display_support(self):
        usb = [dict(vendor_id='35ca', product_id='1301', product='VITURE PRO 2 XR Glasses')]
        result = identify(usb, [], [dict(name='DP-2', model='VITURE')])
        self.assertEqual(result['model'], 'pro2')
        self.assertIsNone(result['output'])
        self.assertIn('driver', result['reason'])
        self.assertIn('display', result['reason'])
        for key, value in [('product', 'VITURE Pro'), ('product_id', '1104'), ('vendor_id', 'ffff')]:
            changed = [{**usb[0], key: value}]
            self.assertNotEqual(identify(changed, [], [])['model'], 'pro2')

    def test_profiles_do_not_seed_pro2_from_legacy_or_overwrite_luma(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'profiles.json'
            view.save_settings(path.with_name('layout.json'), {**view.DEFAULT_SETTINGS, 'stabilization': 87})
            luma = {**profiles.DEFAULT_PROFILE, 'tracking_driver': 'luma', 'mode_switch': True}
            profiles.save_profile(path, 'luma-ultra', luma)
            original = json.loads(path.read_text())['profiles']['luma-ultra']
            pro2 = profiles.load_profile(path, 'pro2')
            self.assertEqual(pro2['stabilization'], view.DEFAULT_SETTINGS['stabilization'])
            self.assertFalse(pro2['tracking'])
            self.assertFalse(pro2['mode_switch'])
            profiles.save_profile(path, 'pro2', {**pro2, 'stabilization': 43})
            self.assertEqual(profiles.load_profile(path, 'pro2')['stabilization'], 43)
            self.assertEqual(json.loads(path.read_text())['profiles']['luma-ultra'], original)
            self.assertEqual(profiles.load_profile(path, 'luma-ultra')['tracking_driver'], 'luma')
            self.assertEqual(profiles.load_setup(path, 'pro2')[1], None)

    def test_saved_luma_startup_choices_are_disabled_only_for_pro2(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'profiles.json'
            saved = {**profiles.DEFAULT_PROFILE, 'tracking_driver': 'luma', 'tracking': True, 'mode_switch': True}
            profiles.save_profile(path, 'pro2', saved)
            loaded = profiles.load_profile(path, 'pro2')
            self.assertFalse(loaded['tracking'])
            self.assertFalse(loaded['mode_switch'])
            self.assertNotEqual(loaded['tracking_driver'], 'luma')
            self.assertEqual(json.loads(path.read_text())['profiles']['pro2'], saved)

    def test_vendor_rule_already_covers_pro2_and_narrow_luma_rule_does_not(self):
        self.assertEqual(covers(RULE_TEXT.splitlines(), product_id='1301'), dict(usb=True, hidraw=True))
        narrowed = RULE_TEXT.replace('MODE=', 'ATTRS{idProduct}=="1104", MODE=')
        self.assertEqual(covers(narrowed.splitlines(), product_id='1301'), dict(usb=False, hidraw=False))
