"""Offline integration gates. Synthetic identities are not captured hardware evidence.

Run explicitly: python -m unittest discover -s tests/pro2 -v
Red tests are integration blockers, never evidence of live acceptance.
"""
import tempfile
import unittest
from pathlib import Path
from tools.xr_controller.detect import identify
from tools.native_spike.profiles import load_profile, save_profile

PRODUCT = 'VITURE PRO 2 XR Glasses'


def usb(product=PRODUCT, pid='1301', vid='35ca'):
    return dict(vendor_id=vid, product_id=pid, product=product)


class IdentityAcceptance(unittest.TestCase):
    def test_exact_identity_has_independent_key(self):
        self.assertEqual(identify([usb()], [], [])['model'], 'pro2')

    def test_product_pid_and_vendor_must_all_match(self):
        for row in [usb(product='VITURE Pro XR Glasses'), usb(product='VITURE Luma Ultra XR GLASSES'),
                    usb(product='VITURE Beast XR Glasses'), usb(product=''), usb(pid='1104'),
                    usb(pid='1201'), usb(pid='ffff'), usb(vid='ffff')]:
            with self.subTest(row=row):
                self.assertNotIn(identify([row], [], [])['model'], ('pro2', 'luma-ultra', 'beast'))

    def test_duplicate_identity_cannot_select_one_device(self):
        self.assertEqual(identify([usb(), usb()], [], [])['model'], 'unknown')

    def test_connector_and_edid_cannot_prove_identity(self):
        for rows in ([], [usb(product='unverified')]):
            result = identify(rows, [dict(name='card1-DP-2', status='connected')],
                              [dict(name='DP-2', model='VITURE', width=1920, height=1080)])
            self.assertNotEqual(result['model'], 'pro2')

    def test_legacy_exact_models_preserved(self):
        for product, pid, model in [('VITURE Beast XR Glasses', '1201', 'beast'),
                                    ('VITURE Luma Ultra XR GLASSES', '1104', 'luma-ultra')]:
            self.assertEqual(identify([usb(product, pid)], [], [])['model'], model)


class ProfileAcceptance(unittest.TestCase):
    def test_pro2_save_does_not_modify_other_models(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'profiles.json'
            before = {}
            for model in ('beast', 'luma-ultra'):
                profile = load_profile(path, model)
                profile['tracking'] = False
                save_profile(path, model, profile)
                before[model] = load_profile(path, model)
            pro2 = load_profile(path, 'pro2')
            self.assertFalse(pro2['tracking'])
            self.assertFalse(pro2['mode_switch'])
            # No numerical FOV is asserted without exact-model optical evidence.
            save_profile(path, 'pro2', pro2)
            self.assertEqual(load_profile(path, 'pro2'), pro2)
            for model, profile in before.items():
                self.assertEqual(load_profile(path, model), profile)


class ProviderBoundaryAcceptance(unittest.TestCase):
    def test_unknown_pro2_provider_cannot_launch_even_with_saved_legacy_choice(self):
        import copy
        import os
        from unittest.mock import Mock
        from tests.xr_controller.test_state import Store, Units
        from tools.xr_controller.state import Controller
        from tools.native_spike.tracking import DRIVERS
        from tools.native_spike.panel_consent import LUMA_PIN
        for driver in DRIVERS:
            with self.subTest(driver=driver), tempfile.TemporaryDirectory() as tmp:
                fd = os.open(tmp, os.O_DIRECTORY)
                try:
                    store = Store()
                    store.profile.update(tracking=True, tracking_driver=driver, mode_switch=False)
                    observation = dict(model='pro2', output='DP-2', reason=None,
                                       instance='offline', monitors=[dict(name='DP-2'), dict(name='DP-1')],
                                       journals=[], plugins=[], tracking_drivers=[],
                                       installed={k: dict(path='/offline/' + k, sha256='a' * 64)
                                                  for k in ('plugin', 'renderer', LUMA_PIN)})
                    spawn = Mock(side_effect=AssertionError('unsupported provider launched'))
                    controller = Controller(lambda: 0, lambda: copy.deepcopy(observation),
                                            spawn, Units(), store, fd, tmp)
                    # This fixture has no bridge installed, regardless of the
                    # real user's pinned bundle or saved Pro 2 settings.
                    controller.route_data = Path(tmp) / 'data'
                    controller.route_config = Path(tmp) / 'config'
                    reply = controller.request(dict(op='begin_start'))
                    self.assertFalse(reply['ok'], reply)
                    self.assertIsNone(controller.pending)
                    spawn.assert_not_called()
                finally:
                    os.close(fd)
