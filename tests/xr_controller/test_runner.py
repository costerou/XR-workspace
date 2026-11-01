"""Runner lifecycle over a fake client: temp dirs, fake binaries, never a real Hyprland socket."""
import contextlib
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock

from tools.native_spike.panel_consent import build_consent
from tools.native_spike.profiles import ProfileStore
from tools.xr_controller import runner
from tools.xr_controller.install import install

INSTANCE = 'a' * 40 + '_123_456'
DESKTOPS = [{'width': 1920, 'height': 1080, 'scale': 1.0}, {'width': 2560, 'height': 1440, 'scale': 1.25},
            {'width': 3840, 'height': 1080, 'scale': 2.0}]
RESULT_KEYS = {'run_id', 'phase', 'complete', 'renderer_returncode', 'remaining', 'error'}


class FakeClient:
    def __init__(self, log, loaded=(), unload_sticks=False):
        self.log, self.loaded, self.unload_sticks = log, list(loaded), unload_sticks
    def plugins_loaded(self):
        self.log.append('plugins_loaded'); return list(self.loaded)
    def plugin_load(self, path, *, data_home=None):
        self.log.append(('plugin_load', path)); self.loaded.append('xr-ownership')
    def plugin_unload(self, path, *, data_home=None):
        self.log.append(('plugin_unload', path))
        if 'xr-ownership' not in self.loaded: raise ValueError('plugin not loaded')
        if not self.unload_sticks: self.loaded.remove('xr-ownership')
    def apply_session_mode(self, output):
        self.log.append(('apply_session_mode', output)); return {'name': output, 'x': 0, 'y': 0, 'scale': 1.0}


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / 'build').mkdir()
        (root / 'build' / 'xr-native-spike').write_bytes(b'renderer')
        (root / 'build' / 'libxr-ownership.so').write_bytes(b'plugin')
        self.data, self.runtime, self.config = root / 'data', root / 'runtime', root / 'config'
        for d in (self.data, self.runtime, self.config): d.mkdir(mode=0o700)
        self.installed = install(root / 'build', self.data)
        self.state = root / 'state'
        self.consent_path = root / 'consent.json'
        env = {'XDG_DATA_HOME': str(self.data), 'XDG_RUNTIME_DIR': str(self.runtime), 'XDG_CONFIG_HOME': str(self.config)}
        patcher = mock.patch.dict(os.environ, env); patcher.start(); self.addCleanup(patcher.stop)
        self.log, self.clients = [], []
        self.client = FakeClient(self.log)
        self.run_result = {'journal': 'j', 'renderer_returncode': 0, 'complete': True, 'remaining': []}
        self.run_error = None

    def consent(self, **changes):
        scope = {'run_id': 'run1', 'instance': INSTANCE, 'output': 'DP-2', 'recovery_output': 'DP-1', 'model': 'luma-ultra',
                 'tracking': False, 'mode_switch': {'width': 1920, 'height': 1200, 'refresh_hz': 120},
                 'plugin': self.installed['plugin'], 'renderer': self.installed['renderer'], 'desktops': DESKTOPS}
        scope.update(changes)
        self.write(build_consent(scope))

    def write(self, data):
        self.consent_path.write_text(json.dumps(data)); self.consent_path.chmod(0o600)

    def fake_run(self, client, consent, runtime, *, desktops=None, store=None, renderer=None):
        self.log.append('run')
        self.run_args = dict(client=client, consent=consent, runtime=runtime, desktops=desktops, store=store, renderer=renderer)
        if self.run_error: raise self.run_error
        return self.run_result

    def main(self):
        def factory(instance, runtime):
            self.clients.append((instance, runtime)); return self.client
        with mock.patch.object(runner, 'HyprlandClient', factory), mock.patch.object(runner, 'run_managed', self.fake_run), \
                contextlib.redirect_stderr(io.StringIO()):
            return runner.main(['--consent', str(self.consent_path), '--state-dir', str(self.state)])

    def result(self, name='run1'):
        path = self.state / f'{name}.result.json'
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        data = json.loads(path.read_text())
        self.assertEqual(set(data), RESULT_KEYS)
        return data

    def test_order_load_mode_run_unload(self):
        self.consent()
        self.assertEqual(self.main(), 0)
        plugin = self.installed['plugin']['path']
        self.assertEqual(self.log, ['plugins_loaded', ('plugin_load', plugin), 'plugins_loaded',
                                    ('apply_session_mode', 'DP-2'), 'run', ('plugin_unload', plugin), 'plugins_loaded'])
        self.assertEqual(self.clients, [(INSTANCE, self.runtime)])
        self.assertEqual(self.run_args['desktops'], DESKTOPS)
        self.assertEqual(self.run_args['renderer'], self.installed['renderer']['path'])
        self.assertEqual(self.run_args['runtime'], self.runtime)
        self.assertIsInstance(self.run_args['store'], ProfileStore)
        self.assertEqual(self.run_args['store'].model, 'luma-ultra')
        self.assertEqual(self.run_args['store'].path, self.config / 'viture-workspace' / 'profiles.json')
        self.assertEqual(self.result(), {'run_id': 'run1', 'phase': 'done', 'complete': True, 'renderer_returncode': 0,
                                         'remaining': [], 'error': None})

    def test_refuses_when_plugin_already_loaded(self):
        self.consent(); self.client.loaded = ['xr-ownership']
        self.assertEqual(self.main(), 2)
        self.assertEqual(self.log, ['plugins_loaded'])
        result = self.result()
        self.assertEqual((result['phase'], result['error']), ('plugin', 'ownership plugin already loaded'))

    def test_unloads_after_run_failure(self):
        self.consent(); self.run_error = ValueError('renderer spawn failed')
        self.assertEqual(self.main(), 2)
        self.assertEqual(self.log[-3:], ['run', ('plugin_unload', self.installed['plugin']['path']), 'plugins_loaded'])
        result = self.result()
        self.assertEqual(result['phase'], 'run'); self.assertIn('renderer spawn failed', result['error'])
        self.assertFalse(result['complete'])

    def test_does_not_unload_foreign_plugin(self):
        self.consent(); self.client.loaded = ['someone-else']
        self.assertEqual(self.main(), 2)
        self.assertFalse(any(isinstance(e, tuple) for e in self.log))
        self.assertEqual(self.client.loaded, ['someone-else'])
        self.assertEqual(self.result()['error'], 'ownership plugin already loaded')

    def test_unload_readback_failure_is_reported(self):
        self.consent(); self.client.unload_sticks = True
        self.assertEqual(self.main(), 2)
        result = self.result()
        self.assertEqual(result['phase'], 'teardown'); self.assertIn('plugin still loaded', result['error'])
        self.assertTrue(result['complete'])

    def test_load_without_readback_blocks_run_and_unloads(self):
        self.consent()
        self.client.plugin_load = lambda path, data_home=None: self.log.append(('plugin_load', path))
        self.assertEqual(self.main(), 2)
        self.assertNotIn('run', self.log)
        self.assertIn(('plugin_unload', self.installed['plugin']['path']), self.log)
        result = self.result()
        self.assertEqual(result['phase'], 'plugin'); self.assertIn('load not confirmed', result['error'])

    def test_mode_failure_still_unloads(self):
        self.consent()
        def bad(output): self.log.append('mode'); raise ValueError('session mode not observed')
        self.client.apply_session_mode = bad
        self.assertEqual(self.main(), 2)
        self.assertNotIn('run', self.log)
        self.assertEqual(self.log[-2:], [('plugin_unload', self.installed['plugin']['path']), 'plugins_loaded'])
        self.assertEqual(self.result()['phase'], 'mode')

    def test_pin_mismatch_blocks_before_any_client_call(self):
        other = dict(self.installed['plugin'], sha256='0' * 64)
        cases = [('consent sha differs', dict(plugin=other)),
                 ('consent path differs', dict(renderer={'path': '/opt/x/xr-native-spike', 'sha256': self.installed['renderer']['sha256']})),
                 ('installed bytes changed', {})]
        for name, changes in cases:
            with self.subTest(name):
                self.log.clear(); self.clients.clear(); self.consent(**changes)
                if name == 'installed bytes changed':
                    p = Path(self.installed['plugin']['path']); p.chmod(0o700); p.write_bytes(b'tampered')
                self.assertEqual(self.main(), 2)
                self.assertEqual((self.log, self.clients), ([], []))
                result = self.result()
                self.assertEqual(result['phase'], 'verify'); self.assertTrue(result['error'])

    def test_no_mode_switch_when_false(self):
        self.consent(model='beast', mode_switch=False)
        self.assertEqual(self.main(), 0)
        self.assertFalse(any(isinstance(e, tuple) and e[0] == 'apply_session_mode' for e in self.log))
        self.assertIn('run', self.log)

    def test_nonzero_renderer_exit_is_failure(self):
        self.consent(); self.run_result = dict(self.run_result, renderer_returncode=3)
        self.assertEqual(self.main(), 2)
        self.assertEqual(self.result()['renderer_returncode'], 3)
        self.run_result = dict(self.run_result, renderer_returncode=0, complete=False, remaining=['XR-1'])
        self.assertEqual(self.main(), 2)
        self.assertEqual(self.result()['remaining'], ['XR-1'])

    def test_result_written_on_every_exit(self):
        # Unreadable consent: no run id is known.
        self.assertEqual(self.main(), 2)
        invalid = self.result('invalid')
        self.assertEqual((invalid['run_id'], invalid['phase'], invalid['complete']), (None, 'verify', False))
        # Readable but rejected consent: result under its own run id.
        self.consent(); data = json.loads(self.consent_path.read_text()); data['input'] = True; self.write(data)
        self.assertEqual(self.main(), 2)
        self.assertEqual(self.result()['phase'], 'verify')
        # Unsafe run id never becomes a file name.
        data['run_id'] = '../escape'; self.write(data)
        self.assertEqual(self.main(), 2)
        self.assertIsNone(self.result('invalid')['run_id'])
        self.assertFalse((Path(self.tmp.name) / 'escape.result.json').exists())
        # Missing runtime directory after a valid consent.
        self.consent()
        with mock.patch.dict(os.environ):
            del os.environ['XDG_RUNTIME_DIR']
            self.assertEqual(self.main(), 2)
        self.assertEqual(self.result()['phase'], 'plugin')
        self.assertEqual(stat.S_IMODE(self.state.stat().st_mode), 0o700)
        self.assertEqual(sorted(p.name for p in self.state.iterdir()), ['invalid.result.json', 'run1.result.json'])


if __name__ == '__main__':
    unittest.main()
