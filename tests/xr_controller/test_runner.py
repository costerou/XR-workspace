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
from tools.native_spike.hyprland import OWNERSHIP_PLUGIN_NAME
HYPRBARS = 'hyprbars'  # Omarchy titlebars workspace mode

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
    def plugin_unload(self, path, *, data_home=None, recovery_pin=None):
        self.log.append(('plugin_unload', path))
        if 'xr-ownership' not in self.loaded: raise ValueError('plugin not loaded')
        if not self.unload_sticks: self.loaded.remove('xr-ownership')
    def park_presentation(self, output):
        self.log.append(('park_presentation', output))
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
        self.assertEqual(set(data) - {'stop_reason', 'recovery'}, RESULT_KEYS)  # only when the supervisor ended the renderer, or recovery fell short
        return data

    def test_tracking_is_ready_before_plugin_or_renderer(self):
        self.consent(tracking='xrlinuxdriver')
        with mock.patch('tools.native_spike.tracking.ensure_ready') as ready:
            ready.side_effect = lambda runtime: self.assertEqual(self.log, [])
            self.assertEqual(self.main(), 0)
            ready.assert_called_once_with(self.runtime)

    def test_progress_is_published_before_tracking_wait(self):
        self.consent(tracking='xrlinuxdriver')
        def waiting(runtime):
            progress=json.loads((self.state/'run1.progress.json').read_text())
            self.assertEqual(progress, dict(run_id='run1',phase='tracking'))
        with mock.patch('tools.native_spike.tracking.ensure_ready',side_effect=waiting):
            self.assertEqual(self.main(),0)

    def test_tracking_failure_blocks_before_plugin_load(self):
        self.consent(tracking='xrlinuxdriver')
        with mock.patch('tools.native_spike.tracking.ensure_ready', side_effect=ValueError('tracking unavailable')):
            self.assertEqual(self.main(), 2)
        self.assertEqual(self.log, [])
        self.assertEqual(self.result()['phase'], 'tracking')

    def test_order_load_mode_run_unload(self):
        self.consent()
        self.assertEqual(self.main(), 0)
        plugin = self.installed['plugin']['path']
        self.assertEqual(self.log, ['plugins_loaded', ('plugin_load', plugin), 'plugins_loaded',
                                    ('apply_session_mode', 'DP-2'), ('park_presentation', 'DP-2'), 'run', ('plugin_unload', plugin), 'plugins_loaded'])
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
        # A foreign plugin no longer blocks the run, and teardown only ever unloads the pinned broker path.
        self.consent(); self.client.loaded = ['someone-else']
        self.assertEqual(self.main(), 0)
        plugin = self.installed['plugin']['path']
        self.assertEqual([e for e in self.log if isinstance(e, tuple) and e[0].startswith('plugin_')],
                         [('plugin_load', plugin), ('plugin_unload', plugin)])
        self.assertEqual(self.client.loaded, ['someone-else'])

    def test_incomplete_recovery_preserves_plugin(self):
        self.consent()
        self.run_result = dict(self.run_result, complete=False, remaining=['XR-1'])
        self.assertEqual(self.main(), 2)
        self.assertEqual(self.client.loaded, ['xr-ownership'])
        self.assertFalse(any(isinstance(e,tuple) and e[0]=='plugin_unload' for e in self.log))
        self.assertIn('recovery',self.result()['error'])

    def test_an_install_during_the_session_still_unloads_the_sessions_plugin(self):
        """The run pinned its plugin at start and installed.json moved on: a clean Stop must not leave that plugin behind.
        It would, with a clean journal: no Recover on offer, and every start refused with "plugin already loaded"."""
        from tools.native_spike.hyprland import HyprlandClient
        class Client(FakeClient):
            plugin_unload, _plugin_command = HyprlandClient.plugin_unload, HyprlandClient._plugin_command
            def _request(client, wire, timeout=None):
                client.log.append(wire); client.loaded.remove('xr-ownership'); return b'ok'
        self.client = Client(self.log)
        self.consent()
        session = dict(self.installed['plugin'])
        next_build = Path(self.tmp.name) / 'next'; next_build.mkdir()
        (next_build / 'xr-native-spike').write_bytes(b'renderer two'); (next_build / 'libxr-ownership.so').write_bytes(b'plugin two')
        real = self.fake_run
        def run_through_an_install(*a, **k):
            install(next_build, self.data)
            return real(*a, **k)
        with mock.patch.object(self, 'fake_run', run_through_an_install):
            self.assertEqual(self.main(), 0)
        self.assertEqual(self.client.loaded, [])
        self.assertIn(f"/plugin unload {session['path']}", self.log)
        result = self.result()
        self.assertEqual((result['phase'], result['error']), ('done', None))

    def test_a_session_plugin_that_changed_on_disk_is_not_unloaded_on_trust(self):
        from tools.native_spike.hyprland import HyprlandClient
        class Client(FakeClient):
            plugin_unload, _plugin_command = HyprlandClient.plugin_unload, HyprlandClient._plugin_command
            def _request(client, wire, timeout=None): client.log.append(wire); return b'ok'
        self.client = Client(self.log)
        self.consent()
        plugin = Path(self.installed['plugin']['path'])
        real = self.fake_run
        def tampered(*a, **k):
            plugin.chmod(0o700); plugin.write_bytes(b'not what was pinned')
            return real(*a, **k)
        with mock.patch.object(self, 'fake_run', tampered):
            self.assertEqual(self.main(), 2)
        self.assertFalse(any(isinstance(e, str) and e.startswith('/plugin unload') for e in self.log))
        self.assertIn('unload:', self.result()['error'])

    def test_incomplete_recovery_says_why(self):
        self.consent()
        why = 'XR-1: the ownership plugin refused to remove it (state blocked)'
        self.run_result = dict(self.run_result, complete=False, remaining=['XR-1'], reasons=[why])
        self.assertEqual(self.main(), 2)
        self.assertEqual(self.result()['recovery'], [why])
        self.assertIn(why, self.result()['error'])

    def test_a_config_rule_naming_a_desktop_blocks_a_start_before_the_plugin_with_its_line(self):
        config = self.config / 'monitors.lua'
        config.write_text('hl.monitor({ output = "", mode = "preferred" })\nhl.monitor({ output = "XR-1", mode = "preferred" })\n')
        self.client.monitors_config = config
        self.consent(tracking='xrlinuxdriver')
        with mock.patch('tools.native_spike.tracking.ensure_ready') as tracking:
            self.assertEqual(self.main(), 2)
        tracking.assert_not_called()  # up to 120 seconds of calibration, to then hear this
        self.assertEqual(self.log, [])
        result = self.result()
        self.assertEqual(result['phase'], 'plugin')
        for word in ('monitors.lua', 'line 2', 'XR-1'): self.assertIn(word, result['error'])

    def test_a_config_that_cannot_be_read_stops_nothing(self):
        config = self.config / 'monitors.lua'
        config.write_bytes(b'\xff\xfe not text')
        self.client.monitors_config = config
        self.consent()
        self.assertEqual(self.main(), 0)

    def test_run_exception_with_journal_preserves_plugin(self):
        self.consent()
        directory=self.runtime/'xr-workspace-spike'/'run1'
        directory.mkdir(parents=True)
        (directory/'journal.json').write_text('{}')
        self.run_error=ValueError('recovery blocked')
        self.assertEqual(self.main(),2)
        self.assertEqual(self.client.loaded,['xr-ownership'])
        self.assertIn('recovery blocked',self.result()['error'])

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

    def test_layout_failure_blocks_launch_and_unloads(self):
        self.consent()
        def bad(output): raise ValueError('presentation placement was not observed')
        self.client.park_presentation = bad
        self.assertEqual(self.main(), 2)
        self.assertNotIn('run', self.log)
        self.assertEqual(self.log[-2:], [('plugin_unload', self.installed['plugin']['path']), 'plugins_loaded'])
        self.assertEqual(self.result()['phase'], 'layout')

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
        self.assertIn(('park_presentation', 'DP-2'), self.log)
        self.assertIn('run', self.log)

    def test_nonzero_renderer_exit_is_failure(self):
        self.consent(); self.run_result = dict(self.run_result, renderer_returncode=3)
        self.assertEqual(self.main(), 2)
        self.assertEqual(self.result()['renderer_returncode'], 3)
        self.run_result = dict(self.run_result, renderer_returncode=0, complete=False, remaining=['XR-1'])
        self.assertEqual(self.main(), 2)
        self.assertEqual(self.result()['remaining'], ['XR-1'])

    def test_the_supervisors_reason_reaches_the_result(self):
        self.consent(); self.run_result = dict(self.run_result, renderer_returncode=-9, stop_reason='heartbeat-timeout')
        self.main()
        self.assertEqual(self.result()['stop_reason'], 'heartbeat-timeout')

    def test_no_reason_leaves_the_result_as_it_was(self):
        self.consent()
        self.main()
        self.assertNotIn('stop_reason', self.result())

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


    # Unrelated plugins (Omarchy's hyprbars stays loaded all session) must not block; only 'xr-ownership' counts.
    def test_runs_and_leaves_hyprbars_alone(self):
        self.consent(); self.client.loaded = [HYPRBARS]
        self.assertEqual(self.main(), 0, self.result())
        plugin = self.installed['plugin']['path']
        self.assertIn(('plugin_load', plugin), self.log); self.assertIn(('plugin_unload', plugin), self.log)
        self.assertEqual(self.client.loaded, [HYPRBARS])
        self.assertEqual(self.result()['error'], None)

    def test_refuses_when_our_broker_is_loaded_beside_hyprbars(self):
        self.consent(); self.client.loaded = [HYPRBARS, OWNERSHIP_PLUGIN_NAME]
        self.assertEqual(self.main(), 2)
        self.assertEqual(self.log, ['plugins_loaded'])
        self.assertEqual(self.result()['error'], 'ownership plugin already loaded')
        self.assertEqual(self.client.loaded, [HYPRBARS, OWNERSHIP_PLUGIN_NAME])

    def test_readback_without_our_broker_is_not_confirmed_by_hyprbars(self):
        self.consent(); self.client.loaded = [HYPRBARS]
        self.client.plugin_load = lambda path, data_home=None: self.log.append(('plugin_load', path))  # load is a no-op
        self.assertEqual(self.main(), 2)
        self.assertIn('plugin load not confirmed by readback', self.result()['error'])
        self.assertNotIn('run', self.log)


if __name__ == '__main__':
    unittest.main()
