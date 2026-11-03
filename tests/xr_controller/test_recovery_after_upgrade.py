"""A run interrupted before an install upgrades the plugin must still recover with the plugin it pinned
(the one still loaded), not fail on 'recovery plugin pin mismatch'. Offline fakes only."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from tools.native_spike.hyprland import OWNERSHIP_PLUGIN_NAME
from tools.xr_controller import __main__ as controller_main
from tools.xr_controller.install import verify_recovery_pin


class RecoveryPin(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)
        bin_dir = self.data / 'viture-workspace/bin'
        for ver, body in (('old', b'old-plugin'), ('new', b'new-plugin')):
            d = bin_dir / ver; d.mkdir(parents=True)
            (d / 'libxr-ownership.so').write_bytes(body)
        self.old = dict(path=str(bin_dir / 'old/libxr-ownership.so'), sha256=hashlib.sha256(b'old-plugin').hexdigest())
        self.new = dict(path=str(bin_dir / 'new/libxr-ownership.so'), sha256=hashlib.sha256(b'new-plugin').hexdigest())

    def test_old_pin_inside_bin_verifies(self):
        verify_recovery_pin(self.old, self.data)

    def test_changed_binary_rejected(self):
        with self.assertRaisesRegex(ValueError, 'pinned binary changed'):
            verify_recovery_pin(dict(self.old, sha256='b' * 64), self.data)

    def test_path_outside_bin_rejected(self):
        outside = self.data / 'libxr-ownership.so'; outside.write_bytes(b'old-plugin')
        with self.assertRaisesRegex(ValueError, 'outside install directory'):
            verify_recovery_pin(dict(self.old, path=str(outside)), self.data)

    def test_recover_run_uses_consent_pin_after_upgrade(self):
        root = self.data
        consent_path, journal = root / 'consent.json', root / 'journal.json'
        consent_path.write_text('{}')
        client = Mock(); client.plugins_loaded.side_effect = [[OWNERSHIP_PLUGIN_NAME], []]
        with patch.dict(os.environ, XDG_RUNTIME_DIR=str(root), XDG_DATA_HOME=str(root)), \
             patch.object(controller_main, 'read_private_json', return_value=dict(instance='i', output='DP-2')), \
             patch('tools.native_spike.panel_consent.load_panel_consent', return_value=dict(instance='i', plugin=self.old)), \
             patch.object(controller_main, 'load_installed', return_value=dict(plugin=self.new)), \
             patch.object(controller_main, 'HyprlandClient', return_value=client), \
             patch('tools.native_spike.managed.recover_managed', return_value=dict(complete=True)) as rec:
            self.assertEqual(controller_main.recover_run(consent_path, journal), 0)
        rec.assert_called_once()
        client.plugin_unload.assert_called_once_with(self.old['path'], data_home=root, recovery_pin=self.old)


if __name__ == '__main__': unittest.main()
