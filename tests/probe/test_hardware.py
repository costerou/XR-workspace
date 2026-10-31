"""Synthetic sysfs fixtures only; never real hardware evidence."""
from pathlib import Path
import tempfile
import unittest
from tools.xr_probe import hardware


class HardwareTests(unittest.TestCase):
    def test_drm_modes_and_unknown_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            node = root / 'card0-DP-1'
            node.mkdir()
            (node / 'status').write_text('connected')
            (node / 'modes').write_text('1920x1080\n3840x1080\n')
            (node / 'device').symlink_to(root / 'gpu')
            result = hardware.probe_drm(root)
            self.assertEqual(result.status, 'ok')
            self.assertEqual(result.data[0]['modes']['value'], ['1920x1080', '3840x1080'])
            self.assertNotIn('120', repr(result))
            (node / 'modes').write_text('')
            self.assertEqual(hardware.probe_drm(root).data[0]['modes']['status'], 'unknown')

    def test_permission_errors_are_field_evidence(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '1-1').mkdir()
            with patch.object(Path, 'open', side_effect=PermissionError):
                result = hardware.probe_usb(root)
            self.assertEqual(result.status, 'ok')
            self.assertEqual(result.data[0]['vendor_id']['status'], 'permission_denied')
            self.assertIsNone(result.data[0]['vendor_id']['value'])
            self.assertIn('idVendor', result.data[0]['vendor_id']['source'])
            self.assertEqual(hardware.probe_usb(root / 'absent').status, 'missing')

    def test_usb_exact_vendor_and_physical_dedup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(hardware.probe_usb(root).data, [])
            for name, vendor in [('1-1', '35ca'), ('1-2', '035ca'), ('1-3', 'abcd')]:
                node = root / name
                node.mkdir()
                (node / 'idVendor').write_text(vendor)
                (node / 'idProduct').write_text('1234')
                (node / 'product').write_text('Unverified model')
                (node / 'authorized').write_text('1')
                (node / 'serial').write_text('NEVER_READ')
            (root / '1-1:1.0').symlink_to(root / '1-1', target_is_directory=True)
            result = hardware.probe_usb(root)
            self.assertEqual(result.status, 'ok')
            self.assertEqual(len(result.data), 1)
            self.assertEqual(result.data[0]['product']['value'], 'Unverified model')
            self.assertEqual(result.data[0]['vendor_id']['value'], '35ca')
            self.assertNotIn('serial', repr(result))
            self.assertNotIn('Beast', repr(result))


if __name__ == '__main__':
    unittest.main()
