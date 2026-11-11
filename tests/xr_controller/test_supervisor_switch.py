"""The supervisor switch (supervisor_switch.py): which supervisor a Start's run unit runs, and the one setting that picks it."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tools.xr_controller import supervisor_switch as sw
from tools.xr_controller.__main__ import Host, main


def _private(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data) if not isinstance(data, str) else data); os.chmod(path, 0o600)


def install_bundle(data_home, binary=b'#!/bin/sh\nexit 0\n'):
    """What `xr-workspace install` leaves: workspace/<sha12>/ with bin/xr-workspace and manifest.json, and workspace.pin.json."""
    root = Path(data_home) / 'viture-workspace'
    files = {sw.BINARY: hashlib.sha256(binary).hexdigest()}
    text = json.dumps(dict(schema=1, files=files), indent=2) + '\n'
    sha = hashlib.sha256(text.encode()).hexdigest()
    bundle = root / sw.WORKSPACE_DIR / sha[:12]
    (bundle / 'bin').mkdir(parents=True)
    (bundle / sw.BINARY).write_bytes(binary); os.chmod(bundle / sw.BINARY, 0o500)
    (bundle / 'manifest.json').write_text(text); os.chmod(bundle / 'manifest.json', 0o400)
    _private(root / sw.WORKSPACE_PIN, dict(schema=1, bundle=str(bundle), manifest_sha256=sha))
    return bundle


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(dir='/tmp'); self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.data, self.config = self.tmp / 'data', self.tmp / 'config'
        env = patch.dict(os.environ, XDG_CONFIG_HOME=str(self.config), XDG_DATA_HOME=str(self.data)); env.start(); self.addCleanup(env.stop)
        self.setting = sw.setting_path()


class SettingTests(Base):
    def test_no_file_is_python(self):
        self.assertIsNone(sw.read_setting())
        self.assertIsNone(sw.run_argv({'run_id': 'r'}, '/s/r.json', '/s', self.data))

    def test_save_read_clear(self):
        sw.save_setting('rust')
        self.assertEqual(self.setting, self.config / 'viture-workspace/supervisor.json')
        self.assertEqual(oct(self.setting.stat().st_mode & 0o777), '0o600')
        self.assertEqual(json.loads(self.setting.read_text()), dict(schema=1, supervisor='rust'))
        self.assertEqual(sw.read_setting(), 'rust')
        sw.save_setting('python'); self.assertEqual(sw.read_setting(), 'python')
        sw.clear_setting(); self.assertIsNone(sw.read_setting()); sw.clear_setting()

    def test_save_refuses_unknown(self):
        with self.assertRaises(ValueError): sw.save_setting('go')
        self.assertFalse(self.setting.exists())

    def test_read_is_strict(self):
        for bad in ({'schema': 2, 'supervisor': 'rust'}, {'schema': 1, 'supervisor': 'Rust'}, {'schema': 1},
                    {'schema': 1, 'supervisor': 'rust', 'extra': 1}, ['rust'], 'rust'):
            _private(self.setting, bad)
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, 'supervisor set python'): sw.read_setting()
        _private(self.setting, {'schema': 1, 'supervisor': 'rust'}); os.chmod(self.setting, 0o644)
        with self.assertRaises(PermissionError): sw.read_setting()


class RustBinaryTests(Base):
    def setUp(self):
        super().setUp(); sw.save_setting('rust')

    def test_rust_runs_the_pinned_binary(self):
        bundle = install_bundle(self.data)
        argv = sw.run_argv({'run_id': 'r', 'model': 'beast'}, '/s/consents/r.json', '/s', self.data)
        self.assertEqual(argv, [str(bundle / 'bin/xr-workspace'), 'supervisor', '--consent', '/s/consents/r.json', '--state-dir', '/s'])

    def test_pro2_stays_on_python(self):
        install_bundle(self.data)
        self.assertIsNone(sw.run_argv({'run_id': 'r', 'model': 'pro2'}, '/s/r.json', '/s', self.data))

    def test_python_setting_ignores_a_broken_pin(self):
        sw.save_setting('python')
        _private(self.data / 'viture-workspace' / sw.WORKSPACE_PIN, 'garbage')
        self.assertIsNone(sw.run_argv({'run_id': 'r'}, '/s/r.json', '/s', self.data))

    def refuses(self, pattern='supervisor set python'):
        with self.assertRaisesRegex(ValueError, pattern): sw.run_argv({'run_id': 'r'}, '/s/r.json', '/s', self.data)

    def test_no_pin_refuses_never_falls_back(self): self.refuses('not installed')

    def test_tampered_binary_refuses(self):
        bundle = install_bundle(self.data)
        os.chmod(bundle / sw.BINARY, 0o700); (bundle / sw.BINARY).write_bytes(b'evil')
        self.refuses('changed since it was installed')

    def test_tampered_manifest_refuses(self):
        bundle = install_bundle(self.data)
        os.chmod(bundle / 'manifest.json', 0o600); (bundle / 'manifest.json').write_text('{"schema": 1, "files": {}}\n')
        self.refuses('bundle changed')

    def test_symlinked_binary_refuses(self):
        bundle = install_bundle(self.data)
        real = self.tmp / 'real'; real.write_bytes((bundle / sw.BINARY).read_bytes())
        os.chmod(bundle / 'bin', 0o700); (bundle / sw.BINARY).unlink(); (bundle / sw.BINARY).symlink_to(real)
        self.refuses()

    def test_bundle_outside_install_root_refuses(self):
        bundle = install_bundle(self.data)
        pin = self.data / 'viture-workspace' / sw.WORKSPACE_PIN
        other = self.tmp / 'elsewhere' / bundle.name; other.parent.mkdir(); bundle.rename(other)
        data = json.loads(pin.read_text()); data['bundle'] = str(other); _private(pin, data)
        self.refuses('not one the installer manages')

    def test_bad_pin_refuses(self):
        install_bundle(self.data)
        pin = self.data / 'viture-workspace' / sw.WORKSPACE_PIN
        data = json.loads(pin.read_text()); data['manifest_sha256'] = 'X' * 64; _private(pin, data)
        self.refuses('invalid')

    def test_broken_setting_refuses_the_start(self):
        install_bundle(self.data); _private(self.setting, '{"schema": 1, "supervisor": "rusty"}')
        self.refuses()


class SpawnTests(Base):
    def spawn(self, action='start', raw=None):
        with patch('tools.xr_controller.__main__.read_private_json', return_value=raw or {'run_id': 'run', 'model': 'beast'}), \
                patch('tools.xr_controller.__main__.subprocess.run') as run:
            Host('/runtime', '/state', self.data).spawn(action, '/state/run.json', '/state/run.journal')
        argv = run.call_args.args[0]
        self.assertEqual(argv[:4], ['systemd-run', '--user', '--unit=viture-xr-run', '--collect'])
        return argv

    def test_default_spawns_python_runner(self):
        argv = self.spawn()
        self.assertIn('tools.xr_controller.runner', ' '.join(argv)); self.assertNotIn('supervisor', argv)

    def test_rust_setting_spawns_rust_per_run_and_back(self):
        bundle = install_bundle(self.data); sw.save_setting('rust')
        argv = self.spawn()
        self.assertEqual(argv[-6:], [str(bundle / sw.BINARY), 'supervisor', '--consent', '/state/run.json', '--state-dir', '/state'])
        self.assertIn('--setenv=XDG_CONFIG_HOME=' + str(self.config), argv)
        sw.save_setting('python')  # the way back: one setting, read at the next Start
        self.assertIn('tools.xr_controller.runner', ' '.join(self.spawn()))

    def test_recover_is_python_even_on_rust(self):
        install_bundle(self.data); sw.save_setting('rust')
        argv = self.spawn('recover')
        self.assertIn('recover-run', argv); self.assertNotIn('supervisor', argv)

    def test_recover_works_with_a_broken_setting(self):
        _private(self.setting, 'garbage')
        self.assertIn('recover-run', self.spawn('recover'))

    def test_rust_without_pin_fails_the_spawn(self):
        sw.save_setting('rust')
        with patch('tools.xr_controller.__main__.read_private_json', return_value={'run_id': 'run'}), \
                patch('tools.xr_controller.__main__.subprocess.run') as run, self.assertRaisesRegex(ValueError, 'supervisor set python'):
            Host('/runtime', '/state', self.data).spawn('start', '/state/run.json', None)
        run.assert_not_called()


FAKE_ENV = 'XR_WORKSPACE_SUPERVISOR_FAKE'  # runner/fake.rs ENV
DEBUG_BINARY = Path(__file__).resolve().parents[2] / 'rust/target/debug/xr-workspace'


@unittest.skipUnless(DEBUG_BINARY.is_file(), 'needs `cargo build -p xr-workspace` (debug: only it has the loopback backend)')
class LoopbackTests(Base):
    """The real Rust binary, pinned as the installer pins it, started with exactly the command and environment Host.spawn gives
    systemd-run, on its loopback backend (runner/fake.rs): it writes the result a Python controller reads."""
    def test_rust_start_runs_end_to_end_and_switches_back(self):
        from tools.native_spike.ipc import read_private_json
        bundle = install_bundle(self.data, DEBUG_BINARY.read_bytes()); sw.save_setting('rust')
        bin_dir = self.data / 'viture-workspace/bin'; bin_dir.mkdir()
        pins = {}
        for key, name in (('plugin', 'libxr-ownership.so'), ('renderer', 'xr-native-spike')):
            body = f'{key} build'.encode(); (bin_dir / name).write_bytes(body)
            pins[key] = dict(path=str(bin_dir / name), sha256=hashlib.sha256(body).hexdigest())
        (self.data / 'viture-workspace/installed.json').write_text(json.dumps(pins))
        runtime, state = self.tmp / 'run', self.tmp / 'state'
        for d in (runtime, state, self.tmp / 'consents'): d.mkdir(mode=0o700)
        consent = self.tmp / 'consents/r1.json'
        raw = dict(schema=1, gate='P2-panel', run_id='r1', instance='inst', output='DP-1', recovery_output='eDP-1', model='beast',
                   allow=['headless-outputs', 'capture-managed-outputs'], capture='managed-outputs', tracking=False, mode_switch=False,
                   plugin=pins['plugin'], renderer=pins['renderer'], desktops=[dict(width=1920, height=1080, scale=1.0)],
                   input=False, sdk=False)
        _private(consent, raw)
        script = self.tmp / 'script.json'; script.write_text(json.dumps(dict(log=str(self.tmp / 'steps.log'))))

        def spawned():
            with patch('tools.xr_controller.__main__.subprocess.run') as run:
                Host(runtime, state, self.data).spawn('start', str(consent), None)
            return run.call_args.args[0]
        argv = spawned()
        command = argv[argv.index(str(bundle / sw.BINARY)):]
        env = dict(kv.removeprefix('--setenv=').split('=', 1) for kv in argv if kv.startswith('--setenv='))
        done = subprocess.run(command, env={**env, 'HOME': str(self.tmp), FAKE_ENV: str(script)},
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        result = read_private_json(state / 'r1.result.json', 1 << 20)
        self.assertEqual((result['run_id'], result['complete'], result['error'], result['phase']), ('r1', True, None, 'done'))
        sw.save_setting('python')
        self.assertIn('tools.xr_controller.runner', ' '.join(spawned()))


class CliTests(Base):
    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err): code = main(['supervisor', *argv])
        return code, out.getvalue(), err.getvalue()

    def test_set_status_clear(self):
        code, out, _ = self.cli('status')
        self.assertEqual(code, 0); self.assertEqual(json.loads(out)['supervisor'], 'python'); self.assertIsNone(json.loads(out)['setting'])
        bundle = install_bundle(self.data)
        code, out, _ = self.cli('set', 'rust')
        self.assertEqual(code, 0); self.assertEqual(json.loads(out), dict(supervisor='rust', setting='rust', rust_binary=str(bundle / sw.BINARY)))
        code, out, _ = self.cli('set', 'python')
        self.assertEqual((code, json.loads(out)['supervisor']), (0, 'python'))
        code, out, _ = self.cli('clear')
        self.assertEqual((code, json.loads(out)['setting']), (0, None)); self.assertFalse(self.setting.exists())

    def test_status_reports_why_rust_cannot_start(self):
        code, out, _ = self.cli('set', 'rust')
        self.assertEqual(code, 0); self.assertIsNone(json.loads(out)['rust_binary']); self.assertIn('not installed', json.loads(out)['rust_error'])

    def test_set_needs_a_choice(self):
        code, _, err = self.cli('set'); self.assertEqual(code, 1); self.assertIn('rust or python', err)

    def test_status_on_a_broken_setting_fails(self):
        _private(self.setting, 'garbage')
        code, _, err = self.cli('status'); self.assertEqual(code, 1); self.assertIn('supervisor.json is invalid', err)
        self.assertEqual(self.cli('clear')[0], 0)  # clearing is the way out of a broken file


if __name__ == '__main__':
    unittest.main()
