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

    def test_defaults_when_missing(self):
        for m in profiles.MODELS:
            self.assertEqual(profiles.load_profile(self.path, m), profiles.DEFAULT_PROFILE)
        self.assertEqual(profiles.DEFAULT_PROFILE['desktops'], [DEFAULT_DESKTOP] * 3)

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
        store.save({'spacing': 60, 'mode': 'follow', 'cursor_follow': True, 'prediction': False})
        self.assertEqual(store.load(), {'spacing': 60, 'mode': 'follow', 'cursor_follow': True, 'prediction': False})
        after = profiles.load_profile(self.path, 'beast')
        self.assertEqual({k: after[k] for k in ('desktops', 'tracking', 'mode_switch', 'fallback')},
                         {k: full[k] for k in ('desktops', 'tracking', 'mode_switch', 'fallback')})
        with self.assertRaises(ValueError): store.save({'spacing': 1})

    def test_layout_store(self):
        s = profiles.LayoutStore(self.dir / 'layout.json')
        self.assertEqual(s.load(), view.DEFAULT_SETTINGS)
        s.save({**view.DEFAULT_SETTINGS, 'spacing': 10})
        self.assertEqual(s.load()['spacing'], 10)

    def test_view_controls_default_store_unchanged(self):
        from tools.native_spike import managed
        import inspect
        self.assertIsNone(inspect.signature(managed.view_controls).parameters['store'].default)


if __name__ == '__main__':
    unittest.main()
