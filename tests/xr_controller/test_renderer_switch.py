"""The renderer switch (renderer_switch.py): which renderer a Start launches, the one setting that picks it, and the Rust
renderer's own pin beside the C++ one (install.py RUST_RENDERER_PIN).

Real files in temp folders, inert binaries, the real install, CLI, Host, Controller and runner over fakes: nothing is executed,
no device is opened and no compositor is touched. A "Start" is begin_start and confirm_start on a Controller over a probe of a
Luma Ultra on DP-2; the runner is runner.execute with a fake Hyprland client and a run_managed that only records.
"""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import socket
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from tools.native_spike import sbs, supervisor, tracking
from tools.native_spike.panel_consent import build_consent
from tools.native_spike.profiles import DEFAULT_PROFILE
from tools.xr_controller import __main__ as cli, install as inst, renderer_switch as rs, runner
from tools.xr_controller.state import Controller

REPO = Path(__file__).resolve().parents[2]
INSTANCE = 'a' * 40 + '_123_456'
DESKTOPS = [{'width': 1920, 'height': 1080, 'scale': 1.0}]


class Store:
    def __init__(self): self.profile = copy.deepcopy(DEFAULT_PROFILE)
    def load(self, model): return copy.deepcopy(self.profile)
    def save(self, model, p): self.profile = copy.deepcopy(p)


class Units:
    def alive(self, run_id): return False
    def alive_many(self, runs): return {r: False for r in runs}
    def control(self, run_id, s, op): return True


class FakeClient:
    def __init__(self, log): self.log, self.loaded = log, []
    def plugins_loaded(self): return list(self.loaded)
    def plugin_load(self, path, *, data_home=None): self.log.append(('plugin_load', path)); self.loaded.append('xr-ownership')
    def plugin_unload(self, path, *, data_home=None, recovery_pin=None): self.log.append(('plugin_unload', path)); self.loaded.clear()
    def park_presentation(self, output): self.log.append(('park_presentation', output))
    def apply_session_mode(self, output): self.log.append(('apply_session_mode', output)); return {}


def _private(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data) if not isinstance(data, str) else data); os.chmod(path, 0o600)


def _engine(target, *a, **k):
    """A stand-in depth engine beside the C++ renderer, so a C++ Luma start offers 3D (immersive.find_engine)."""
    (Path(target) / inst.ENGINE).write_bytes(b'#!/bin/sh\n'); os.chmod(Path(target) / inst.ENGINE, 0o500)


class Base(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory(dir='/tmp'); self.addCleanup(t.cleanup)
        self.root = Path(t.name).resolve()
        self.data, self.config, self.state, self.runtime = (self.root / n for n in ('data', 'config', 'state', 'runtime'))
        for d in (self.config, self.state, self.runtime): d.mkdir(mode=0o700)
        self.build = self.root / 'build'; (self.build / 'driver').mkdir(parents=True)
        for name in (inst.RENDERER, inst.PLUGIN, 'driver/' + inst.LUMA_DRIVER): (self.build / name).write_bytes(b'c++ ' + name.encode())
        self.rust = self.root / 'cargo' / 'xr-renderer'; self.rust.parent.mkdir(); self.rust.write_bytes(b'rust xr-renderer A')
        env = mock.patch.dict(os.environ, XDG_DATA_HOME=str(self.data), XDG_CONFIG_HOME=str(self.config), XDG_RUNTIME_DIR=str(self.runtime))
        for patch in (env, mock.patch.object(tracking, 'OVERRIDE_FILE', self.config / 'viture-workspace/tracking-override.json'),
                      mock.patch.object(inst, '_write_engine', _engine)):
            patch.start(); self.addCleanup(patch.stop)
        self.setting = rs.setting_path()
        self.pin = self.data / 'viture-workspace' / inst.RUST_RENDERER_PIN

    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                mock.patch.object(cli, 'install_pro2_bundle', return_value=dict(bundle='/b')), mock.patch.object(cli, 'install_unit'):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def install(self, rust=True):
        code, out, err = self.cli('install', '--build', str(self.build), *(('--rust-renderer', str(self.rust)) if rust else ()))
        self.assertEqual(code, 0, err)
        return json.loads(out)


class SettingTests(Base):
    def test_no_file_is_cpp(self):
        self.assertIsNone(rs.read_setting())
        self.assertFalse(self.setting.exists())

    def test_save_read_clear(self):
        rs.save_setting('rust')
        self.assertEqual(self.setting, self.config / 'viture-workspace/renderer.json')
        self.assertEqual(oct(self.setting.stat().st_mode & 0o777), '0o600')
        self.assertEqual(json.loads(self.setting.read_text()), dict(schema=1, renderer='rust'))
        self.assertEqual(rs.read_setting(), 'rust')
        rs.save_setting('cpp'); self.assertEqual(rs.read_setting(), 'cpp')
        rs.clear_setting(); self.assertIsNone(rs.read_setting()); rs.clear_setting()

    def test_save_refuses_unknown(self):
        for bad in ('python', 'Rust', 'c++', None):
            with self.subTest(bad=bad), self.assertRaises(ValueError): rs.save_setting(bad)
        self.assertFalse(self.setting.exists())

    def test_read_is_strict(self):
        for bad in ({'schema': 2, 'renderer': 'rust'}, {'schema': 1, 'renderer': 'Rust'}, {'schema': 1}, {'schema': True, 'renderer': 'rust'},
                    {'schema': 1, 'renderer': 'rust', 'extra': 1}, {'schema': 1, 'supervisor': 'rust'}, ['rust'], 'rust', '', '{'):
            _private(self.setting, bad if not isinstance(bad, str) or bad in ('', '{') else json.dumps(bad))
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, 'renderer.json is invalid; .*renderer set cpp'): rs.read_setting()
        _private(self.setting, {'schema': 1, 'renderer': 'rust'}); os.chmod(self.setting, 0o644)
        with self.assertRaises(PermissionError): rs.read_setting()


class InstallTests(Base):
    """The Rust renderer is pinned in its own file, not in installed.json, so every reader of installed.json, the older
    controllers in installed bundles and other worktrees included, reads it unchanged (the Rust Luma driver's way)."""
    def installed_json(self): return json.loads((self.data / 'viture-workspace/installed.json').read_text())

    def test_a_plain_install_writes_what_it_always_wrote(self):
        entry = self.install(rust=False)
        self.assertNotIn('rust_renderer', entry)
        self.assertFalse(self.pin.exists())
        self.assertEqual(list(self.installed_json()), ['renderer', 'plugin', 'luma_driver'])
        self.assertIsNone(inst.load_rust_renderer(self.data))

    def test_install_pins_the_rust_renderer_beside_the_cpp_one(self):
        plain = self.install(rust=False)
        entry = self.install()
        rust = entry['rust_renderer']
        folder = Path(entry['renderer']['path']).parent
        self.assertEqual(Path(rust['path']), folder / inst.RUST_DIR / inst.RUST_RENDERER)
        self.assertEqual(Path(rust['path']).read_bytes(), b'rust xr-renderer A')
        self.assertEqual(oct(Path(rust['path']).stat().st_mode & 0o777), '0o500')
        # Its hash joins the folder's: a new Rust renderer alone gets its own folder, and a running session's pins stay true.
        self.assertNotEqual(folder, Path(plain['renderer']['path']).parent)
        self.assertTrue(Path(plain['renderer']['path']).is_file())
        self.assertEqual(self.pin.read_text(), json.dumps(rust, indent=2))
        self.assertEqual(oct(self.pin.stat().st_mode & 0o777), '0o600')
        self.assertEqual(inst.load_rust_renderer(self.data), rust)
        # installed.json: the same keys as ever, the C++ renderer, read by every reader as it was.
        on_disk = self.installed_json()
        self.assertEqual(list(on_disk), ['renderer', 'plugin', 'luma_driver'])
        self.assertEqual(Path(on_disk['renderer']['path']).read_bytes(), b'c++ xr-native-spike')
        self.assertEqual(inst.load_installed(self.data), on_disk)
        self.assertEqual({k: v for k, v in entry.items() if k != 'rust_renderer'}, on_disk)

    def test_every_reader_of_installed_json_reads_it(self):
        for rust in (False, True):
            with self.subTest(rust=rust):
                self.install(rust=rust)
                on_disk = self.installed_json()
                self.assertEqual(inst.load_installed(self.data), on_disk)  # Host.probe, runner, isolated, hyprland, spike CLI
                drivers = {d['key']: d for d in inst.tracking_drivers(self.data, on_disk)}
                self.assertEqual(drivers['luma']['path'], on_disk['luma_driver']['path'])
                from tools.native_spike.__main__ import _pinned_luma
                with mock.patch.dict(os.environ, XDG_DATA_HOME=str(self.data)):
                    self.assertEqual(_pinned_luma(), on_disk['luma_driver']['path'])
                observed = Host(self).probe()
                self.assertEqual(observed['installed'], on_disk)

    def test_a_plain_install_drops_the_rust_pin(self):
        first = self.install()
        self.install(rust=False)
        self.assertFalse(self.pin.exists())
        self.assertIsNone(inst.load_rust_renderer(self.data))
        self.assertTrue(Path(first['rust_renderer']['path']).is_file())  # the old folder stays for a running session

    def test_both_rust_pins_together(self):
        driver = self.root / 'cargo' / 'xr-luma-driver'; driver.write_bytes(b'rust xr-luma-driver')
        code, out, err = self.cli('install', '--build', str(self.build), '--rust-driver', str(driver), '--rust-renderer', str(self.rust))
        self.assertEqual(code, 0, err)
        entry = json.loads(out)
        self.assertEqual(Path(entry['rust_luma_driver']['path']).parent, Path(entry['rust_renderer']['path']).parent)
        self.assertEqual(inst.load_installed(self.data)['luma_driver'], entry['rust_luma_driver'])
        self.assertEqual(inst.load_rust_renderer(self.data), entry['rust_renderer'])

    def test_a_broken_pin_is_refused(self):
        self.install()
        for text in ('{', '[]', json.dumps(dict(path='/usr/bin/sh', sha256='a' * 64)), json.dumps(dict(path=str(self.rust), sha256='a' * 64, x=1))):
            with self.subTest(text=text):
                self.pin.write_text(text)
                with self.assertRaises(ValueError): inst.load_rust_renderer(self.data)
                self.assertEqual(list(inst.load_installed(self.data)), ['renderer', 'plugin', 'luma_driver'])  # never blocks a C++ start

    def test_restoring_the_previous_pins_rolls_back(self):
        files = (self.data / 'viture-workspace/installed.json', self.pin)
        def save(): return {p: (p.read_bytes() if p.exists() else None) for p in files}
        def restore(saved):
            for p, b in saved.items():
                if b is None: p.unlink(missing_ok=True)
                else: p.write_bytes(b)
        self.install(rust=False); before = save()
        first = self.install(); a = save()
        self.rust.write_bytes(b'rust xr-renderer B')
        second = self.install()
        self.assertNotEqual(second['rust_renderer'], first['rust_renderer'])
        restore(a)  # back to renderer A: its folder was kept, its pin verifies
        self.assertEqual(rs.rust_renderer(self.data), first['rust_renderer'])
        restore(before)  # back to before there was a Rust renderer
        with self.assertRaisesRegex(ValueError, 'not installed.*renderer set cpp'): rs.rust_renderer(self.data)
        self.assertEqual(list(inst.load_installed(self.data)), ['renderer', 'plugin', 'luma_driver'])

    def test_an_unreadable_rust_renderer_refuses_before_anything_is_written(self):
        code, _, err = self.cli('install', '--build', str(self.build), '--rust-renderer', str(self.root / 'missing'))
        self.assertEqual(code, 1); self.assertIn('unreadable', err)
        self.assertFalse((self.data / 'viture-workspace/installed.json').exists())


def Host(test):
    """A freshly started controller's Host over a Luma Ultra on DP-2, its probe faked below the Hyprland and USB layer."""
    host = cli.Host(test.runtime, test.state, test.data)
    real = host.probe
    def probe():
        with mock.patch.object(cli, 'HyprlandClient') as client, mock.patch.object(cli, 'probe_usb'), mock.patch.object(cli, 'probe_drm'), \
                mock.patch.object(cli, 'identify', return_value=dict(model='luma-ultra', output='DP-2', reason=None)), \
                mock.patch.object(cli.monitor_rules, 'leftovers', return_value=[]), \
                mock.patch.object(cli, 'Units', lambda runtime: Units()):  # a run left in the state dir: no real systemctl
            client.return_value.query.return_value = [dict(name='DP-2', model='VITURE'), dict(name='DP-1', model='Dell')]
            client.return_value.plugins_loaded.return_value = []
            return real()
    host.probe = probe
    return host


class Starts(Base):
    """Start and run helpers; the tests are in StartTests and BuiltRendererTests."""
    def setUp(self):
        super().setUp()
        self.spawned, self.log, self.launched = [], [], []

    def controller(self):
        host = Host(self)
        observed = host.probe(); self.assertIsNone(observed['reason'])
        fd = os.open(self.state, os.O_DIRECTORY); self.addCleanup(os.close, fd)
        def spawn(*args):
            with mock.patch.object(cli.subprocess, 'run') as run: host.spawn(*args)
            self.spawned.append(run.call_args.args[0])
        c = Controller(lambda: 0, lambda: copy.deepcopy(observed), spawn, Units(), Store(), fd, self.state)
        c.scope_transform = host.scope  # as serve wires it (ServeTests)
        c.tick()
        return c

    def begin(self, c=None):
        return (c or self.controller()).request(dict(op='begin_start'))

    def start(self):
        """Start and confirm (head tracking off, so the runner below needs no driver); the consent written."""
        c = self.controller()
        reply = self.begin(c); self.assertTrue(reply['ok'], reply)
        confirmed = c.request(dict(op='confirm_start', scope_id=reply['scope_id'], optional=dict(tracking=False)))
        self.assertTrue(confirmed['ok'], confirmed)
        path = self.state / (reply['scope']['run_id'] + '.json')
        return reply['scope'], json.loads(path.read_text()), path

    def execute(self, consent_path):
        """runner.execute over fakes: the renderer it would launch is the one the real launcher's argv[0] names."""
        def run_managed(client, consent, runtime, *, renderer=None, **_):
            self.log.append('run')
            def popen(argv, **kwargs):
                self.launched.append(argv); raise OSError('stop before running')
            parent, wayland = socket.socketpair()
            with parent, wayland, contextlib.suppress(OSError):
                supervisor._launch(SimpleNamespace(_peer=(os.getpid(), os.getuid(), os.getgid())), consent['run_id'], 'session',
                                   consent['output'], wayland, None, popen=popen, renderer=renderer)
            return dict(journal='j', renderer_returncode=0, complete=True, remaining=[])
        with mock.patch.object(runner, 'HyprlandClient', lambda *a: FakeClient(self.log)), mock.patch.object(runner, 'run_managed', run_managed):
            return runner.execute(consent_path, self.data)

    def write_consent(self, renderer):
        scope = dict(run_id='run1', instance=INSTANCE, output='DP-2', recovery_output='DP-1', model='luma-ultra', tracking=False,
                     mode_switch=False, plugin=inst.load_installed(self.data)['plugin'], renderer=renderer, desktops=DESKTOPS)
        path = self.state / 'run1.json'; _private(path, build_consent(scope))
        return path

    def refused(self, result):
        self.assertIn('renderer set cpp', result['error'])
        self.assertEqual((self.log, self.launched), ([], []))  # no plugin, no run, no renderer


class StartTests(Starts):
    """A Start resolves the renderer from the setting, at every Start; the runner checks the consent against the same."""
    def test_default_is_the_cpp_renderer(self):
        self.install()
        scope, consent, path = self.start()
        cpp = inst.load_installed(self.data)['renderer']
        self.assertEqual((scope['renderer'], consent['renderer']), (cpp, cpp))
        self.assertIn(sbs.SCOPE_KEY, scope)  # 3D is offered: the engine sits beside the C++ renderer
        result = self.execute(path)
        self.assertIsNone(result['error'], result)
        self.assertEqual(self.launched[0][0], cpp['path'])

    def test_rust_launches_the_pinned_rust_renderer_and_the_way_back_is_one_command(self):
        entry = self.install()
        self.assertEqual(self.cli('renderer', 'set', 'rust')[0], 0)
        scope, consent, path = self.start()
        rust = entry['rust_renderer']
        self.assertEqual((scope['renderer'], consent['renderer']), (rust, rust))  # the confirm box names the one that runs
        self.assertNotIn(sbs.SCOPE_KEY, scope)  # no depth engine beside the Rust renderer: no 3D offered
        result = self.execute(path)  # its strict load_panel_consent: the consent format is the one it always was
        self.assertIsNone(result['error'], result)
        self.assertEqual(result['phase'], 'done')
        self.assertEqual(self.launched[0][0], rust['path'])
        self.assertIn('tools.xr_controller.runner', ' '.join(self.spawned[0]))  # the unit: unchanged, the consent names the renderer
        self.assertEqual(self.cli('renderer', 'set', 'cpp')[0], 0)
        self.launched.clear()
        scope, _, path = self.start()
        self.assertEqual(scope['renderer'], inst.load_installed(self.data)['renderer'])
        self.assertIsNone(self.execute(path)['error'])
        self.assertEqual(self.launched[0][0], inst.load_installed(self.data)['renderer']['path'])

    def test_rust_without_a_rust_renderer_fails_the_start(self):
        self.install(rust=False); rs.save_setting('rust')
        reply = self.begin()
        self.assertFalse(reply['ok']); self.assertIn('renderer set cpp', reply['error']); self.assertIn('not installed', reply['error'])
        self.assertEqual(self.spawned, [])
        self.refused(self.execute(self.write_consent(inst.load_installed(self.data)['renderer'])))  # never the C++ one instead

    def test_a_changed_rust_renderer_fails_the_start(self):
        entry = self.install(); rs.save_setting('rust')
        path = Path(entry['rust_renderer']['path']); os.chmod(path, 0o700); path.write_bytes(b'evil'); os.chmod(path, 0o500)
        reply = self.begin()
        self.assertFalse(reply['ok']); self.assertIn('renderer set cpp', reply['error']); self.assertIn('changed', reply['error'])
        self.refused(self.execute(self.write_consent(entry['rust_renderer'])))

    def test_a_consent_for_the_other_renderer_is_refused(self):
        entry = self.install()
        cpp = inst.load_installed(self.data)['renderer']
        rs.save_setting('rust')  # set after a C++ Start was confirmed: the runner refuses rather than run C++
        self.refused(self.execute(self.write_consent(cpp)))
        rs.save_setting('cpp')  # and the other way: a Rust consent under a C++ setting
        result = self.execute(self.write_consent(entry['rust_renderer']))
        self.assertIn('consent renderer is not the pinned install', result['error']); self.assertEqual(self.launched, [])

    def test_a_broken_setting_fails_the_start(self):
        self.install(); _private(self.setting, '{"schema": 1, "renderer": "rusty"}')
        reply = self.begin()
        self.assertFalse(reply['ok']); self.assertIn('renderer set cpp', reply['error'])
        self.refused(self.execute(self.write_consent(inst.load_installed(self.data)['renderer'])))
        self.assertEqual(self.cli('renderer', 'set', 'cpp')[0], 0)  # which also replaces the broken file
        self.assertTrue(self.begin()['ok'])

    def test_a_cpp_setting_never_reads_the_rust_pin(self):
        self.install(); rs.save_setting('cpp'); self.pin.write_text('garbage')
        scope, _, path = self.start()
        self.assertEqual(scope['renderer'], inst.load_installed(self.data)['renderer'])
        self.assertIsNone(self.execute(path)['error'])


class IgnoredTests(Base):
    """A Pro 2 run and Recover stay on their own renderer and path whatever the setting says, even a broken one."""
    def setUp(self):
        super().setUp()
        self.install(); _private(self.setting, 'garbage')
        never = mock.patch.object(rs, 'read_setting', side_effect=AssertionError('the renderer setting was read'))
        never.start(); self.addCleanup(never.stop)

    def test_a_pro2_scope_is_left_as_it_is(self):
        scope = dict(model='pro2', renderer=dict(path='/pro2/bin/xr-native-spike', sha256='b' * 64), **{sbs.SCOPE_KEY: 'x'})
        self.assertEqual(cli.Host(self.runtime, self.state, self.data).scope(copy.deepcopy(scope)), scope)

    def test_a_pro2_run_goes_its_own_route(self):
        path = self.state / 'pro2.json'; _private(path, dict(model='pro2', run_id='p'))
        with mock.patch.object(runner, 'execute_pro2', return_value=dict(run_id='p', phase='done', error=None)) as pro2:
            self.assertEqual(runner.execute(path, self.data)['phase'], 'done')
        pro2.assert_called_once()

    def test_recover_spawns_recover_run(self):
        with mock.patch.object(cli, 'read_private_json', return_value={'run_id': 'run', 'model': 'luma-ultra'}), \
                mock.patch.object(cli.subprocess, 'run') as run:
            cli.Host(self.runtime, self.state, self.data).spawn('recover', '/state/run.json', '/state/run.journal')
        self.assertIn('recover-run', run.call_args.args[0])


class ServeTests(Base):
    def test_serve_binds_the_switch_to_the_controller(self):
        state = self.root / 'xdg-state'
        with mock.patch.dict(os.environ, XDG_STATE_HOME=str(state)), mock.patch.object(cli, 'serve') as serve, \
                mock.patch.object(cli, 'incident_monitor', return_value=None):
            self.assertEqual(cli.main(['serve']), 0)
        controller = serve.call_args.args[0]
        self.install(); rs.save_setting('rust')
        scope = dict(model='luma-ultra', renderer=inst.load_installed(self.data)['renderer'])
        self.assertEqual(controller.scope_transform(scope)['renderer'], inst.load_rust_renderer(self.data))


class CliTests(Base):
    def renderer(self, *argv):
        code, out, err = self.cli('renderer', *argv)
        return code, (json.loads(out) if out else None), err

    def test_set_status_clear(self):
        code, out, _ = self.renderer('status')
        self.assertEqual(code, 0); self.assertEqual((out['renderer'], out['setting'], out['rust_renderer']), ('cpp', None, None))
        entry = self.install()
        code, out, _ = self.renderer('set', 'rust')
        self.assertEqual((code, out), (0, dict(renderer='rust', setting='rust', rust_renderer=entry['rust_renderer']['path'])))
        code, out, _ = self.renderer('set', 'cpp')
        self.assertEqual((code, out['renderer'], out['setting']), (0, 'cpp', 'cpp'))
        code, out, _ = self.renderer('clear')
        self.assertEqual((code, out['renderer'], out['setting']), (0, 'cpp', None)); self.assertFalse(self.setting.exists())

    def test_status_says_why_a_rust_start_would_fail(self):
        code, out, _ = self.renderer('set', 'rust')
        self.assertEqual(code, 0); self.assertIsNone(out['rust_renderer']); self.assertIn('not installed', out['rust_error'])
        entry = self.install()
        path = Path(entry['rust_renderer']['path']); os.chmod(path, 0o700); path.write_bytes(b'evil')
        code, out, _ = self.renderer('status')
        self.assertEqual(code, 0); self.assertIn('changed', out['rust_error'])

    def test_set_needs_a_choice(self):
        code, _, err = self.renderer('set'); self.assertEqual(code, 1); self.assertIn('rust or cpp', err)

    def test_status_on_a_broken_setting_fails(self):
        _private(self.setting, 'garbage')
        code, _, err = self.renderer('status'); self.assertEqual(code, 1); self.assertIn('renderer.json is invalid', err)
        self.assertEqual(self.renderer('clear')[0], 0)  # clearing is the way out of a broken file


BUILT = next((p for p in (REPO / 'rust/target/release/xr-renderer', REPO / 'rust/target/debug/xr-renderer') if p.is_file()), None)


@unittest.skipUnless(BUILT, 'needs a built xr-renderer (`cargo build -p xr-renderer` in rust/)')
class BuiltRendererTests(Starts):
    """The real Rust renderer binary, pinned as the installer pins it: a Start's launch command names its pinned copy."""
    def test_the_launch_command_carries_the_pinned_rust_renderer(self):
        self.rust = BUILT
        entry = self.install(); rs.save_setting('rust')
        _, _, path = self.start()
        result = self.execute(path)
        self.assertIsNone(result['error'], result)
        argv = self.launched[0]
        self.assertEqual(argv[0], entry['rust_renderer']['path'])
        self.assertEqual(Path(argv[0]).read_bytes(), BUILT.read_bytes())
        self.assertEqual(argv[1], '--supervised-fd')


if __name__ == '__main__':
    unittest.main()
