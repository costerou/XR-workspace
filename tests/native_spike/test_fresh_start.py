"""Every XR start is fresh: three desktops, all flat, in the overview, anchored, a maximize following until Ctrl+Alt+V picks
locked; saved placements, a saved desktop count and a saved viewing mode are not restored (maximize-follow: the overview
is always anchor, the follow / locked choice lasts one session), while the other saved settings (spacing, prediction)
still are. A `cursor_switch_animate` left by the removed Ctrl+Alt+A is ignored. Placements last one session: a run that
moved, resized and bumped its desktops writes none of it (monitor-collision-bump)."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from tools.native_spike import free_pointer as fp, managed, profiles, view
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
    def test_saved_placements_and_mode_are_not_restored_but_other_settings_are(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp) / 'run'; runtime.mkdir(mode=0o700)
            client = mock.Mock(runtime_dir=runtime, event_stream=None)
            client.query.return_value = []
            path = Path(tmp) / 'profiles.json'
            profiles.save_profile(path, 'luma-ultra', {**profiles.DEFAULT_PROFILE, 'spacing': 60, 'mode': 'locked',
                                                       'prediction': False, 'placements': [CURVED] * 3})
            # A profile written while Ctrl+Alt+A existed still carries its setting: tolerated, ignored, no error.
            raw = json.loads(path.read_text()); raw['profiles']['luma-ultra']['cursor_switch_animate'] = False
            path.write_text(json.dumps(raw))
            store = profiles.ProfileStore(path, 'luma-ultra')
            self.assertNotIn('cursor_switch_animate', store.load())
            for run, spacing in (('run', 60), ('run2', 80)):  # run 2 gets the spacing run 1 saved
                with managed.view_controls(client, run, 'session', NAMES, store=store) as control:
                    s = control.state
                    self.assertEqual(s.placements, [DEFAULT_PLACEMENT] * 3)  # all flat
                    self.assertEqual((s.zoomed, s.mode), (False, 'anchor'))  # the overview, anchored: the saved mode is not read
                    self.assertEqual(json.loads(control.initial_frame().split(b'\n')[0])['payload']['mode'], 'anchor')
                    self.assertEqual((s.spacing, s.prediction), (spacing, False))
                    self.assertFalse(hasattr(s, 'cursor_switch_animate'))
                    self.assertEqual(s.maximized_mode, 'follow')  # a fresh start maximizes in follow, even after locked
                    s.apply('zoom_in'); s.apply('mode_next'); s.apply('zoom_out'); s.apply('zoom_in')
                    self.assertEqual(s.mode, 'locked')  # this session keeps locked...
                    s.apply('spacing_up')  # + view.SPACING_STEP (20)
                    control.settings.save(s.settings())
            saved = profiles.load_profile(path, 'luma-ultra')  # ...but it is never saved
            self.assertEqual(saved['mode'], 'anchor')
            self.assertNotIn('cursor_switch_animate', saved)
            self.assertNotIn('cursor_switch_animate', json.loads(path.read_text())['profiles']['luma-ultra'])


class SessionOnlyPlacementTests(unittest.TestCase):
    def test_a_run_that_moved_and_bumped_its_desktops_saves_none_of_it(self):
        root = Path(__file__).resolve().parents[2]
        fixture = json.loads((root / 'tests/native_spike/fixtures/overview_pointer_payloads.json').read_text())
        geometry = fp.parse_overview_geometry(json.loads(fixture['geometry']['line'])['overview_geometry'])
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp) / 'run'; runtime.mkdir(mode=0o700)
            client = mock.Mock(runtime_dir=runtime, event_stream=None)
            client.query.return_value = []
            path = Path(tmp) / 'profiles.json'
            profiles.save_profile(path, 'luma-ultra', {**profiles.DEFAULT_PROFILE, 'spacing': 60})
            written = path.read_text()
            store = profiles.ProfileStore(path, 'luma-ultra')
            for run in ('run', 'run2'):
                with managed.view_controls(client, run, 'session', NAMES, store=store) as control:
                    self.assertEqual(control.state.placements, [DEFAULT_PLACEMENT] * 3)  # fresh, also after run 1
                    control.set_overview_geometry(geometry)  # the renderer's layout: bumping is on
                    control.on_message({'op': 'menu_toggle'}); control.on_message({'op': 'menu_inc_big'})  # XR-1 into XR-2
                    control.on_message({'op': 'menu_field_next'}); control.on_message({'op': 'menu_field_next'})
                    control.on_message({'op': 'menu_dec_big'})  # bigger (Distance down)
                    control.on_message({'op': 'placement', 'payload': {'desktops': [dict(p) for p in control.state.placements[:2]]
                                                                         + [{**control.state.placements[2], 'y': 120}]}})
                    moved = [dict(p) for p in control.state.placements]
                    self.assertNotEqual(moved[1], DEFAULT_PLACEMENT)  # XR-2 was pushed
                    self.assertGreater(control.bumps, 0)
                    self.assertIsNone(control.placements)  # the run has no placement store
                    control.on_message({'op': 'menu_toggle'})  # collapsing used to save at once
                    control.flush()
                self.assertEqual(path.read_text(), written)  # not one byte: nothing about placements, nothing else
                self.assertEqual(profiles.load_profile(path, 'luma-ultra')['placements'], [DEFAULT_PLACEMENT] * 3)


if __name__ == '__main__':
    unittest.main()
