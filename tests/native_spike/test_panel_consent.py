"""P2-panel consent gate: strict, capture-granting, never accepted by the CLI gate."""
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest

from tools.native_spike.__main__ import load_consent
from tools.native_spike.panel_consent import PANEL_GATE, PANEL_KEYS, build_consent, load_panel_consent, write_consent

INSTANCE = 'a' * 40 + '_123_456'
SHA = 'ab' * 32
LUMA_MODE = {'width': 1920, 'height': 1200, 'refresh_hz': 120}


def scope(**changes):
    base = {'run_id': 'run1', 'instance': INSTANCE, 'output': 'DP-2', 'recovery_output': 'DP-1', 'model': 'luma-ultra',
            'tracking': 'xrlinuxdriver', 'mode_switch': dict(LUMA_MODE),
            'plugin': {'path': '/opt/x/plugin.so', 'sha256': SHA},
            'renderer': {'path': '/opt/x/renderer', 'sha256': SHA},
            'desktops': [{'width': 1920, 'height': 1080, 'scale': 1.0}, {'width': 2560, 'height': 1440, 'scale': 1.25},
                         {'width': 3840, 'height': 1080, 'scale': 2.0}]}
    base.update(changes)
    return base


class PanelConsentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.fd = os.open(self.tmp.name, os.O_RDONLY | os.O_DIRECTORY); self.addCleanup(os.close, self.fd)

    def write_raw(self, data, name='raw.json'):
        path = Path(self.tmp.name) / name
        path.write_text(json.dumps(data)); path.chmod(0o600)
        return path

    def written(self, **changes):
        consent = build_consent(scope(**changes))
        return self.write_raw(consent), consent

    def test_roundtrip_exact(self):
        consent = build_consent(scope())
        name = write_consent(self.fd, consent)
        self.assertEqual(name, 'run1.json')
        self.assertEqual(load_panel_consent(Path(self.tmp.name) / name, INSTANCE, 'DP-2'), consent)
        self.assertEqual(set(consent), PANEL_KEYS); self.assertEqual(consent['gate'], PANEL_GATE)
        self.assertIs(consent['input'], False); self.assertIs(consent['sdk'], False)

    def test_beast_roundtrip_no_switch_no_tracking(self):
        consent = build_consent(scope(model='beast', mode_switch=False, tracking=False))
        path = self.write_raw(consent)
        self.assertEqual(load_panel_consent(path, INSTANCE, 'DP-2'), consent)

    def test_cli_gate_refuses_panel_record(self):
        path, _ = self.written()
        with self.assertRaises(ValueError): load_consent(path, INSTANCE, 'DP-2')

    def test_panel_gate_refuses_cli_record(self):
        path = self.write_raw({'schema': 1, 'gate': 'P2', 'run_id': 'run', 'instance': INSTANCE, 'output': 'DP-2',
                               'recovery_output': 'DP-1', 'allow': ['headless-outputs', 'test-pattern'], 'capture': False,
                               'input': False, 'sdk': False, 'mode_switch': False})
        with self.assertRaises(ValueError): load_panel_consent(path, INSTANCE, 'DP-2')

    def test_rejects_extra_or_missing_keys(self):
        _, consent = self.written()
        with self.assertRaises(ValueError): load_panel_consent(self.write_raw({**consent, 'extra': 1}), INSTANCE, 'DP-2')
        short = dict(consent); del short['tracking']
        with self.assertRaises(ValueError): load_panel_consent(self.write_raw(short), INSTANCE, 'DP-2')
        with self.assertRaises(ValueError): build_consent({**scope(), 'input': False})
        s = scope(); del s['model']
        with self.assertRaises(ValueError): build_consent(s)

    def test_rejects_mode_switch_other_mode(self):
        with self.assertRaises(ValueError): build_consent(scope(mode_switch={'width': 1920, 'height': 1080, 'refresh_hz': 120}))
        _, consent = self.written()
        bad = {**consent, 'mode_switch': {'width': 1920, 'height': 1080, 'refresh_hz': 120}}
        with self.assertRaises(ValueError): load_panel_consent(self.write_raw(bad), INSTANCE, 'DP-2')
        with self.assertRaises(ValueError): load_panel_consent(self.write_raw({**consent, 'mode_switch': True}), INSTANCE, 'DP-2')

    def test_mode_switch_only_on_luma(self):
        for model in ('beast', 'unknown'):
            with self.assertRaises(ValueError): build_consent(scope(model=model))
        _, consent = self.written()
        with self.assertRaises(ValueError): load_panel_consent(self.write_raw({**consent, 'model': 'beast'}), INSTANCE, 'DP-2')
        with self.assertRaises(ValueError): build_consent(scope(model='viture'))

    def test_rejects_input_true(self):
        _, consent = self.written()
        for key in ('input', 'sdk'):
            with self.assertRaises(ValueError): load_panel_consent(self.write_raw({**consent, key: True}), INSTANCE, 'DP-2')
            with self.assertRaises(ValueError): load_panel_consent(self.write_raw({**consent, key: 0}), INSTANCE, 'DP-2')

    def test_rejects_bad_sha(self):
        for sha in ('AB' * 32, 'ab' * 31, 'zz' * 32, 5):
            with self.assertRaises(ValueError): build_consent(scope(plugin={'path': '/p', 'sha256': sha}))
        _, consent = self.written()
        bad = {**consent, 'renderer': {'path': '/r', 'sha256': 'AB' * 32}}
        with self.assertRaises(ValueError): load_panel_consent(self.write_raw(bad), INSTANCE, 'DP-2')

    def test_rejects_relative_path_and_extra_artifact_keys(self):
        with self.assertRaises(ValueError): build_consent(scope(renderer={'path': 'renderer', 'sha256': SHA}))
        with self.assertRaises(ValueError): build_consent(scope(plugin={'path': '/p', 'sha256': SHA, 'x': 1}))

    def test_rejects_scope_mismatch_and_tokens(self):
        path, _ = self.written()
        with self.assertRaises(ValueError): load_panel_consent(path, INSTANCE, 'DP-3')
        with self.assertRaises(ValueError): load_panel_consent(path, 'b' * 40 + '_1_2', 'DP-2')
        with self.assertRaises(ValueError): build_consent(scope(run_id='x' * 81))
        with self.assertRaises(ValueError): build_consent(scope(run_id='../x'))
        with self.assertRaises(ValueError): build_consent(scope(recovery_output='DP-2'))

    def test_load_requires_scope_arguments(self):
        path, _ = self.written()
        for args in ((None, None), (INSTANCE, None), (None, 'DP-2')):
            with self.assertRaises(ValueError): load_panel_consent(path, *args)

    def test_rejects_bad_desktops_tracking_schema(self):
        with self.assertRaises(ValueError): build_consent(scope(desktops=scope()['desktops'][:2]))
        with self.assertRaises(ValueError):
            build_consent(scope(desktops=[{'width': 3840, 'height': 1080, 'scale': 1.75}] * 3))
        with self.assertRaises(ValueError): build_consent(scope(tracking=True))
        with self.assertRaises(ValueError): build_consent(scope(tracking='other'))
        _, consent = self.written()
        for key, val in (('schema', True), ('schema', 2), ('capture', False), ('allow', ['headless-outputs'])):
            with self.assertRaises(ValueError): load_panel_consent(self.write_raw({**consent, key: val}), INSTANCE, 'DP-2')

    def test_load_requires_private_file(self):
        path, _ = self.written(); path.chmod(0o644)
        with self.assertRaises(PermissionError): load_panel_consent(path, INSTANCE, 'DP-2')

    def test_write_refuses_existing_file(self):
        consent = build_consent(scope())
        write_consent(self.fd, consent)
        with self.assertRaises(FileExistsError): write_consent(self.fd, consent)

    def test_write_refuses_invalid_consent(self):
        consent = build_consent(scope()); consent['input'] = True
        with self.assertRaises(ValueError): write_consent(self.fd, consent)
        self.assertEqual(os.listdir(self.tmp.name), [])

    def test_write_mode_0600_single_link(self):
        name = write_consent(self.fd, build_consent(scope()))
        st = os.stat(Path(self.tmp.name) / name)
        self.assertEqual(stat.S_IMODE(st.st_mode), 0o600); self.assertEqual(st.st_nlink, 1)

    def test_write_refuses_symlink(self):
        os.symlink('/nonexistent-target', Path(self.tmp.name) / 'run1.json')
        with self.assertRaises(OSError): write_consent(self.fd, build_consent(scope()))


if __name__ == '__main__':
    unittest.main()
