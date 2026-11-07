"""Offline provider rejection; all identities below are synthetic sysfs fixtures."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.native_spike import tracking


class Pro2ProviderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        p = patch.object(tracking, 'SYS_USB', self.root)
        p.start(); self.addCleanup(p.stop)

    def device(self, pid='1301', product='VITURE PRO 2 XR Glasses', vid='35ca', name='1-1'):
        d = self.root / name; d.mkdir()
        for key, value in [('idVendor', vid), ('idProduct', pid), ('product', product)]:
            (d / key).write_text(value)

    def test_all_providers_reject_before_launch_or_reuse(self):
        self.device()
        for method in ('start', 'ensure_ready'):
            for kwargs in ({}, {'binary': '/test/3dof'}, {'luma': '/test/luma'}):
                with self.subTest(method=method, kwargs=kwargs), \
                     patch.object(tracking, '_check_config') as config, \
                     patch.object(tracking, '_open_luma_log') as log, \
                     patch.object(tracking, 'status') as status, \
                     patch.object(tracking, 'stop') as stop, \
                     patch.object(tracking.subprocess, 'Popen') as spawn:
                    with self.assertRaisesRegex(ValueError, '35ca:1301.*not for these glasses'):
                        getattr(tracking, method)(self.root, **kwargs)
                    config.assert_not_called(); log.assert_not_called()
                    status.assert_not_called(); stop.assert_not_called(); spawn.assert_not_called()

    def test_no_sdk_fallback_for_unsupported_model(self):
        self.device()
        with patch.object(tracking, 'ensure_ready') as ready:
            with self.assertRaisesRegex(ValueError, 'not for these glasses'):
                tracking.ensure_ready_luma(self.root, '/test/luma')
            ready.assert_not_called()

    def test_conflicting_or_missing_product_still_rejects(self):
        for pid, product in [('1301', 'Unknown'), ('1104', 'VITURE PRO 2 XR Glasses')]:
            with self.subTest(pid=pid):
                self.device(pid, product, name=pid)
                with self.assertRaisesRegex(ValueError, 'not for these glasses'):
                    tracking._check_provider_compatibility()
                for f in (self.root / pid).iterdir(): f.unlink()
                (self.root / pid).rmdir()

    def test_missing_product_and_mixed_models_cannot_hide_pro2(self):
        self.device()
        (self.root / '1-1/product').unlink()
        self.device('1104', 'VITURE Luma Ultra XR GLASSES', name='luma')
        with self.assertRaisesRegex(ValueError, 'not for these glasses'):
            tracking._check_provider_compatibility()

    def test_existing_and_unrelated_devices_are_unchanged(self):
        for pid, product in [('1104', 'VITURE Luma Ultra XR GLASSES'), ('1201', 'VITURE Beast XR Glasses')]:
            self.device(pid, product, name=pid)
        self.device('1301', 'Other vendor', vid='ffff', name='other')
        tracking._check_provider_compatibility()

    def test_rejection_during_luma_start_cannot_be_swallowed(self):
        with patch.object(tracking, 'ensure_ready', side_effect=tracking.UnsupportedTrackingProvider('not approved')) as ready:
            with self.assertRaises(tracking.UnsupportedTrackingProvider):
                tracking.ensure_ready_luma(self.root, '/test/luma')
            self.assertEqual(ready.call_count, 1)

    def test_incomplete_or_conflicting_identity_rejects_before_provider_work(self):
        cases = [('idProduct', False), ('idProduct', True), ('product', False),
                 ('product', True), ('conflicting-pid', False), ('conflicting-product', False)]
        for field, unreadable in cases:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); device = root / '1-1'; device.mkdir()
                for name, value in [('idVendor', '35ca'), ('idProduct', '1301'),
                                    ('product', 'VITURE PRO 2 XR Glasses')]:
                    (device / name).write_text(value)
                if field.startswith('conflicting'):
                    (device / ('idProduct' if field == 'conflicting-pid' else 'product')).write_text('1104' if field == 'conflicting-pid' else 'Other model')
                else:
                    (device / field).unlink()
                    if unreadable: (device / field).mkdir()  # read_text raises OSError even as root
                for method in ('start', 'ensure_ready', 'ensure_ready_luma'):
                    with self.subTest(field=field, unreadable=unreadable, method=method), \
                         patch.object(tracking, 'SYS_USB', root), \
                         patch.object(tracking, 'status') as status, \
                         patch.object(tracking, '_check_config') as config, \
                         patch.object(tracking, '_open_luma_log') as log, \
                         patch.object(tracking, 'stop') as stop, \
                         patch.object(tracking.subprocess, 'Popen') as spawn:
                        with self.assertRaises(tracking.UnsupportedTrackingProvider):
                            if method == 'ensure_ready_luma': tracking.ensure_ready_luma(root, '/test/luma')
                            else: getattr(tracking, method)(root)
                        status.assert_not_called(); config.assert_not_called(); log.assert_not_called()
                        stop.assert_not_called(); spawn.assert_not_called()
