"""Unrelated Hyprland plugins (Omarchy keeps hyprbars loaded all session for its titlebars workspace mode) must not
block a start or a recovery. Only our own broker, named 'xr-ownership' by PLUGIN_INIT, counts. Offline fakes only."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from tools.native_spike.hyprland import OWNERSHIP_PLUGIN_NAME, ownership_plugin_count
from tools.xr_controller import __main__ as controller_main

HYPRBARS = 'hyprbars'  # name in `hyprctl plugin list -j` for /usr/lib/omarchy-hyprland-titlebars/titlebars.so


class CountTests(unittest.TestCase):
    def test_name_matches_the_plugin_init_name(self):
        src = (Path(__file__).resolve().parents[2] / 'spike/ownership/plugin.cpp').read_text()
        self.assertIn(f'return {{"{OWNERSHIP_PLUGIN_NAME}",', src)

    def test_only_our_broker_counts(self):
        for names, want in (([], 0), ([HYPRBARS], 0), ([HYPRBARS, 'someone-else'], 0),
                            ([HYPRBARS, OWNERSHIP_PLUGIN_NAME], 1), ([OWNERSHIP_PLUGIN_NAME, OWNERSHIP_PLUGIN_NAME], 2),
                            (['xr-ownership-old', 'XR-OWNERSHIP'], 0)):
            with self.subTest(names=names): self.assertEqual(ownership_plugin_count(names), want)


class RecoverWithForeignPlugin(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.consent_path, self.journal = root / 'consent.json', root / 'journal.json'
        self.consent_path.write_text(json.dumps(dict(instance='i', output='DP-2')))
        self.pin = dict(path='/pinned/libxr-ownership.so', sha256='a' * 64)
        self.client = Mock()

    def recover(self, *lists, complete=True):
        self.client.plugins_loaded.side_effect = [list(x) for x in lists]
        with patch.dict(os.environ, XDG_RUNTIME_DIR=self.tmp.name, XDG_DATA_HOME=self.tmp.name), \
             patch.object(controller_main, 'read_private_json', return_value=dict(instance='i', output='DP-2')), \
             patch('tools.native_spike.panel_consent.load_panel_consent', return_value=dict(instance='i', plugin=self.pin)), \
             patch('tools.xr_controller.install.verify_recovery_pin'), \
             patch.object(controller_main, 'HyprlandClient', return_value=self.client), \
             patch('tools.native_spike.managed.recover_managed', return_value=dict(complete=complete)) as rec:
            return controller_main.recover_run(self.consent_path, self.journal), rec

    def test_recovers_with_hyprbars_loaded(self):
        rc, rec = self.recover([HYPRBARS, OWNERSHIP_PLUGIN_NAME], [HYPRBARS])
        self.assertEqual(rc, 0); rec.assert_called_once()
        self.client.plugin_unload.assert_called_once_with(self.pin['path'], data_home=Path(self.tmp.name), recovery_pin=self.pin)

    def test_hyprbars_alone_is_not_our_broker(self):
        with self.assertRaisesRegex(ValueError, 'consented ownership plugin unavailable'):
            self.recover([HYPRBARS])
        self.client.plugin_unload.assert_not_called()

    def test_unload_confirmation_ignores_hyprbars(self):
        with self.assertRaisesRegex(ValueError, 'plugin unload not confirmed'):
            self.recover([HYPRBARS, OWNERSHIP_PLUGIN_NAME], [HYPRBARS, OWNERSHIP_PLUGIN_NAME])


if __name__ == '__main__': unittest.main()
