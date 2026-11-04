import json
import os
from pathlib import Path
import tempfile
import unittest
from tools.native_spike import profiles, view
from tools.native_spike.geometry import DEFAULT_DESKTOP


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name) / 'cfg'
        self.path = self.dir / 'profiles.json'

    def test_tracking_driver_defaults_to_xrlinuxdriver_and_keeps_a_valid_choice(self):
        self.assertEqual(profiles.DEFAULT_PROFILE['tracking_driver'], 'xrdriver')
        self.assertEqual(profiles.load_profile(self.path, 'beast')['tracking_driver'], 'xrdriver')  # nothing saved
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({'schema': profiles.SCHEMA, 'profiles': {'beast': {'tracking': True}}}))
        self.assertEqual(profiles.load_profile(self.path, 'beast')['tracking_driver'], 'xrdriver')  # saved before the setting
        for bad in ('other', 1, None, ['luma']):
            self.path.write_text(json.dumps({'schema': profiles.SCHEMA, 'profiles': {'beast': {'tracking_driver': bad}}}))
            self.assertEqual(profiles.load_profile(self.path, 'beast')['tracking_driver'], 'xrdriver')
        profiles.save_profile(self.path, 'beast', {**profiles.DEFAULT_PROFILE, 'tracking_driver': 'luma'})
        self.assertEqual(profiles.load_profile(self.path, 'beast')['tracking_driver'], 'luma')
        self.assertEqual(profiles.load_profile(self.path, 'luma-ultra')['tracking_driver'], 'xrdriver')  # per glasses

    def test_defaults_when_missing(self):
        for m in profiles.MODELS:
            self.assertEqual(profiles.load_profile(self.path, m), profiles.DEFAULT_PROFILE)
        self.assertEqual(profiles.DEFAULT_PROFILE['desktops'], [DEFAULT_DESKTOP] * 3)

    def test_tracking_driver_defaults_to_the_standard_one(self):
        self.assertEqual(profiles.DEFAULT_PROFILE['tracking_driver'], 'xrdriver')

    def test_every_tracking_driver_survives_a_save_and_other_saves(self):
        for driver in ('xrdriver', '3dof', 'luma'):
            with self.subTest(driver):
                profile = profiles.load_profile(self.path, 'luma-ultra')
                profiles.save_profile(self.path, 'luma-ultra', {**profile, 'tracking_driver': driver})
                self.assertEqual(profiles.load_profile(self.path, 'luma-ultra')['tracking_driver'], driver)
                store = profiles.ProfileStore(self.path, 'luma-ultra')
                store.save(store.load())
                profiles.PlacementStore(self.path, 'luma-ultra').reset()
                self.assertEqual(profiles.load_profile(self.path, 'luma-ultra')['tracking_driver'], driver)

    def test_bad_tracking_driver_in_a_file_cleans_to_the_standard_one(self):
        self.dir.mkdir()
        for bad in ('/usr/bin/sh', 'xrDriver', '/usr/bin/xrDriver', 5, None, ['luma'], 'luma\0'):
            with self.subTest(bad):
                self.path.write_text(json.dumps({'schema': profiles.SCHEMA, 'profiles': {'luma-ultra': {'tracking_driver': bad}}}))
                self.assertEqual(profiles.load_profile(self.path, 'luma-ultra')['tracking_driver'], 'xrdriver')

    def test_the_binary_path_of_an_unreleased_build_is_dropped(self):
        self.dir.mkdir()  # xrdriver-3dof-ab saved a path; only ever on a test install
        self.path.write_text(json.dumps({'schema': profiles.SCHEMA, 'profiles': {'luma-ultra': {'tracking_driver_binary': '/x/xrDriver'}}}))
        profile = profiles.load_profile(self.path, 'luma-ultra')
        self.assertNotIn('tracking_driver_binary', profile)
        self.assertEqual(profile['tracking_driver'], 'xrdriver')

    def test_save_refuses_a_bad_tracking_driver(self):
        profile = profiles.load_profile(self.path, 'luma-ultra')
        for bad in ('/usr/bin/sh', 'xrDriver', 5, None):
            with self.subTest(bad), self.assertRaises(ValueError):
                profiles.save_profile(self.path, 'luma-ultra', {**profile, 'tracking_driver': bad})

    def test_seeds_from_layout_v1(self):
        self.dir.mkdir()
        (self.dir / 'layout.json').write_text('{"spacing": 80}')
        for m in profiles.MODELS:
            self.assertEqual(profiles.load_profile(self.path, m)['spacing'], 80)

    def test_invalid_desktop_falls_back_alone(self):
        good = {'width': 2560, 'height': 1440, 'scale': 1.0}
        bad = {'width': 3840, 'height': 1080, 'scale': 1.75}
        self.dir.mkdir()
        p = {**profiles.DEFAULT_PROFILE, 'desktops': [good, bad, good]}
        self.path.write_text(json.dumps({'schema': 2, 'profiles': {'beast': p, 'mystery': p}}))
        self.assertEqual(profiles.load_profile(self.path, 'beast')['desktops'], [good, DEFAULT_DESKTOP, good])
        profiles.save_profile(self.path, 'luma-ultra', profiles.DEFAULT_PROFILE)
        self.assertNotIn('mystery', json.loads(self.path.read_text())['profiles'])  # a rewrite drops unknown models

    def test_invalid_fields_fall_back(self):
        self.dir.mkdir()
        self.path.write_text(json.dumps({'schema': 2, 'profiles': {'beast': {
            'spacing': 99999, 'tracking': 1, 'mode_switch': 'x', 'fallback': 'bad name!', 'desktops': 'no'}}}))
        self.assertEqual(profiles.load_profile(self.path, 'beast'), profiles.DEFAULT_PROFILE)

    def test_unknown_model_raises(self):
        with self.assertRaises(ValueError): profiles.load_profile(self.path, 'nope')
        with self.assertRaises(ValueError): profiles.save_profile(self.path, 'nope', profiles.DEFAULT_PROFILE)

    def test_models_independent(self):
        before = profiles.load_profile(self.path, 'luma-ultra')
        profiles.save_profile(self.path, 'luma-ultra', {**before, 'spacing': 20})
        profiles.save_profile(self.path, 'beast', {**profiles.DEFAULT_PROFILE, 'spacing': 40, 'fallback': 'DP-1'})
        self.assertEqual(profiles.load_profile(self.path, 'luma-ultra')['spacing'], 20)
        self.assertEqual(profiles.load_profile(self.path, 'beast')['fallback'], 'DP-1')
        self.assertEqual(profiles.load_profile(self.path, 'unknown'), profiles.DEFAULT_PROFILE)

    def test_save_rejects_invalid(self):
        bad = {**profiles.DEFAULT_PROFILE, 'desktops': [DEFAULT_DESKTOP, DEFAULT_DESKTOP, {'width': 3840, 'height': 1080, 'scale': 1.75}]}
        with self.assertRaises(ValueError): profiles.save_profile(self.path, 'beast', bad)
        with self.assertRaises(ValueError): profiles.save_profile(self.path, 'beast', {**profiles.DEFAULT_PROFILE, 'tracking': 1})
        with self.assertRaises(ValueError): profiles.save_profile(self.path, 'beast', {**profiles.DEFAULT_PROFILE, 'tracking_driver': 'other'})
        self.assertFalse(self.path.exists())

    def test_atomic_mode_0600(self):
        profiles.save_profile(self.path, 'beast', profiles.DEFAULT_PROFILE)
        self.assertEqual(oct(self.path.stat().st_mode & 0o777), '0o600')
        self.assertEqual(sorted(os.listdir(self.dir)), ['profiles.json'])
        self.assertEqual(json.loads(self.path.read_text())['schema'], 2)

    def test_profile_store_roundtrip_view_keys(self):
        full = {**profiles.DEFAULT_PROFILE, 'tracking': False, 'fallback': 'DP-2',
                'desktops': [{'width': 2560, 'height': 1440, 'scale': 1.0}] * 3}
        profiles.save_profile(self.path, 'beast', full)
        store = profiles.ProfileStore(self.path, 'beast')
        self.assertEqual(store.load(), view.DEFAULT_SETTINGS)
        sharp = {'filter': 'nearest', 'deband': True}
        grid = {'background': 'grid', 'last': 'grid', 'brightness': 60}
        store.save({'spacing': 60, 'mode': 'follow', 'prediction': False, 'stabilization': 25, 'video_quality': sharp, 'environment': grid,
                    'immersive': {**view.DEFAULT_SETTINGS['immersive'], 'desktops': {'XR-2': True}}})
        self.assertEqual(store.load(), {'spacing': 60, 'mode': 'follow', 'prediction': False, 'stabilization': 25, 'video_quality': sharp, 'environment': grid,
                                        'immersive': {**view.DEFAULT_SETTINGS['immersive'], 'desktops': {'XR-2': True}}})
        with self.assertRaises(ValueError): store.save({'spacing': 60, 'mode': 'follow', 'cursor_follow': True, 'prediction': False,
                                                        'video_quality': sharp, 'environment': grid})
        with self.assertRaises(ValueError): store.save({'spacing': 60, 'mode': 'follow', 'prediction': False,  # Ctrl+Alt+A: removed
                                                        'cursor_switch_animate': True, 'video_quality': sharp, 'environment': grid})
        with self.assertRaises(ValueError): store.save({'spacing': 60, 'mode': 'follow', 'prediction': False})  # every key
        with self.assertRaises(ValueError): store.save({**view.DEFAULT_SETTINGS, 'video_quality': {'filter': 'cubic', 'deband': False}})
        with self.assertRaises(ValueError): store.save({**view.DEFAULT_SETTINGS, 'environment': {'background': 'red', 'last': 'grid', 'brightness': 100}})
        after = profiles.load_profile(self.path, 'beast')
        self.assertEqual({k: after[k] for k in ('desktops', 'tracking', 'mode_switch', 'fallback')},
                         {k: full[k] for k in ('desktops', 'tracking', 'mode_switch', 'fallback')})
        with self.assertRaises(ValueError): store.save({'spacing': 1})

    def test_layout_store(self):
        s = profiles.LayoutStore(self.dir / 'layout.json')
        self.assertEqual(s.load(), view.DEFAULT_SETTINGS)
        s.save({**view.DEFAULT_SETTINGS, 'spacing': 10})
        self.assertEqual(s.load()['spacing'], 10)

    def test_stale_tmp_does_not_affect_mode(self):
        self.dir.mkdir()
        stale = self.dir / 'profiles.json.tmp'
        stale.write_text('stale'); stale.chmod(0o644)
        profiles.save_profile(self.path, 'beast', profiles.DEFAULT_PROFILE)
        self.assertEqual(oct(self.path.stat().st_mode & 0o777), '0o600')
        self.assertEqual(sorted(os.listdir(self.dir)), ['profiles.json', 'profiles.json.tmp'])

    def test_failed_save_removes_temp(self):
        from unittest import mock
        with mock.patch('os.replace', side_effect=OSError('boom')), self.assertRaises(OSError):
            profiles.save_profile(self.path, 'beast', profiles.DEFAULT_PROFILE)
        self.assertEqual(os.listdir(self.dir), [])

    def test_view_controls_default_store_unchanged(self):
        from unittest import mock
        from tools.native_spike import managed
        runtime = Path(self.tmp.name) / 'run'; runtime.mkdir(mode=0o700)
        client = mock.Mock(runtime_dir=runtime, event_stream=None)
        client.query.return_value = []
        with mock.patch.dict(os.environ, {'XDG_CONFIG_HOME': self.tmp.name}):
            (Path(self.tmp.name) / 'viture-workspace').mkdir()
            (Path(self.tmp.name) / 'viture-workspace' / 'layout.json').write_text('{"spacing": 70}')
            with managed.view_controls(client, 'run', 'session', ['XR-1', 'XR-2', 'XR-3']) as control:
                self.assertEqual(control.state.spacing, 70)
                control.settings.save({**view.DEFAULT_SETTINGS, 'spacing': 30})
        self.assertEqual(json.loads((Path(self.tmp.name) / 'viture-workspace' / 'layout.json').read_text())['spacing'], 30)


if __name__ == '__main__':
    unittest.main()
