"""Display handovers preserve pose continuity only for one exact Pro 2 device."""
import tempfile
import unittest
from pathlib import Path

from tools.native_spike.survive import usb_identity
from tests.native_spike.test_survive import bus


class Pro2IdentityTests(unittest.TestCase):
    def test_pro2_identity_tracks_bus_and_enumeration_not_the_luma_device(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = bus(tmp, ('1-9', '35ca', '1301', 1, 30))
            (root / '1-9' / 'product').write_text('VITURE PRO 2 XR Glasses\n')
            self.assertEqual(usb_identity(root, model='pro2'), (1, 30))
            (root / '1-9' / 'devnum').write_text('31\n')
            self.assertEqual(usb_identity(root, model='pro2'), (1, 31))
            self.assertIsNone(usb_identity(root))

    def test_product_name_and_id_must_both_agree(self):
        for product_id, product in (('1301', 'VITURE Pro XR Glasses'),
                                    ('1104', 'VITURE PRO 2 XR Glasses'),
                                    ('1301', None)):
            with self.subTest(product_id=product_id, product=product), tempfile.TemporaryDirectory() as tmp:
                root = bus(tmp, ('1-9', '35ca', product_id, 1, 30))
                if product is not None:
                    (root / '1-9' / 'product').write_text(product)
                self.assertIsNone(usb_identity(root, model='pro2'))

    def test_multiple_viture_devices_fail_closed(self):
        for other in ('1301', '1104', 'ffff'):
            with self.subTest(other=other), tempfile.TemporaryDirectory() as tmp:
                root = bus(tmp, ('1-9', '35ca', '1301', 1, 30), ('1-10', '35ca', other, 1, 31))
                for name in ('1-9', '1-10'):
                    (root / name / 'product').write_text('VITURE PRO 2 XR Glasses')
                self.assertIsNone(usb_identity(root, model='pro2'))

    def test_unknown_model_does_not_fall_back_to_luma(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = bus(tmp, ('1-9', '35ca', '1104', 1, 30))
            self.assertIsNone(usb_identity(root, model='unknown'))
            self.assertIsNone(usb_identity(root, model=None))
        self.assertIsNone(usb_identity(Path('/nonexistent'), model='pro2'))
