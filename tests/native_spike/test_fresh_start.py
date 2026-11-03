"""Every XR start is fresh: three desktops, all flat; saved placements and a saved desktop count are not restored, while
the other saved settings (spacing, mode, prediction, cursor_switch_animate) still are."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from tools.native_spike import managed, profiles, view
from tools.native_spike.geometry import DEFAULT_DESKTOP, DEFAULT_PLACEMENT

NAMES = ['XR-1', 'XR-2', 'XR-3']
CURVED = {'x': 30, 'y': -10, 'distance': 160, 'curvature': 40}
ULTRAWIDE = {'width': 2560, 'height': 1080, 'scale': 1.0}
SMALL = {'width': 1280, 'height': 720, 'scale': 1.25}


class StartupDesktopsTests(unittest.TestCase):
    def test_always_three(self):
        self.assertEqual(profiles.startup_desktops(None), [DEFAULT_DESKTOP] * 3)
        self.assertEqual(profiles.startup_desktops([SMALL]), [SMALL, DEFAULT_DESKTOP, DEFAULT_DESKTOP])
        self.assertEqual(profiles.startup_desktops([SMALL, ULTRAWIDE, SMALL, ULTRAWIDE]), [SMALL, ULTRAWIDE, SMALL])


class FreshPlacementTests(unittest.TestCase):
    def test_saved_placements_are_not_restored_but_other_settings_are(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp) / 'run'; runtime.mkdir(mode=0o700)
            client = mock.Mock(runtime_dir=runtime, event_stream=None)
            client.query.return_value = []
            path = Path(tmp) / 'profiles.json'
            profiles.save_profile(path, 'luma-ultra', {**profiles.DEFAULT_PROFILE, 'spacing': 60, 'mode': 'follow',
                                                       'prediction': False, 'cursor_switch_animate': False,
                                                       'placements': [CURVED] * 3})
            store = profiles.ProfileStore(path, 'luma-ultra')
            with managed.view_controls(client, 'run', 'session', NAMES, store=store) as control:
                s = control.state
                self.assertEqual(s.placements, [DEFAULT_PLACEMENT] * 3)  # all flat
                self.assertEqual((s.spacing, s.mode, s.prediction, s.cursor_switch_animate), (60, 'follow', False, False))
            self.assertEqual(profiles.load_profile(path, 'luma-ultra')['cursor_switch_animate'], False)


if __name__ == '__main__':
    unittest.main()
