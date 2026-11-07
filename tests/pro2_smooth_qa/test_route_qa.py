"""The panel route on its failure paths. Fakes for the compositor and the bridge; the real run_session, keep_renderer, consent and route code."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from unittest.mock import Mock, patch
from tests.xr_controller.test_pro2_route import Base, MONITORS, Store
from tools.native_spike import managed, panel_consent as pc, survive
from tools.native_spike.hyprland import OWNERSHIP_PLUGIN_NAME
from tools.pro2_runtime import launch
from tools.pro2_runtime.scope import TestScope
from tools.xr_controller import pro2_route as route, pro2_test
from tools.xr_controller.detect import identify
from tools.xr_controller.state import Controller

ROOT = Path(__file__).resolve().parents[2]
UNIT = dict(width=1920, height=1080, scale=1)


class Provider:
    def __init__(self, root, directory, projection, seconds, approved, pose_rate_hz=60, panel_route=False, mount_pitch_deg=0.0):
        directory.mkdir(); self.prefix = str(directory / 'pose_'); self.projection = projection; self.nonce = 'n'
        self.health_path = directory / 'health.json'; self.done = dict(samples=1, seconds=1.0, pose_rate_hz=60, gap_ms_counts=[0] * 6); self.closed = 0
        self.health(True)
    def health(self, ready):
        self.health_path.write_text(json.dumps(dict(nonce='n', ready=ready, monotonic=time.monotonic()))); self.health_path.chmod(0o600)
    def start(self): Provider.last = self; return self
    def close(self): self.closed += 1


class Projection(Base):
    def write(self, text):
        path = self.config / route.SETTINGS_FILE; path.write_text(text); path.chmod(0o600)

    def test_odd_files_are_the_default(self):
        for text in ('[]', '{"projection":"46.5"}', '{"projection":true}', '{"projection":NaN}', '{"projection":1e999}', '{', ''):
            self.write(text)
            with self.subTest(text=text): self.assertEqual(route.load_projection(self.config), 44.5)

    def test_a_number_too_big_for_a_float_is_the_default_not_a_crash(self):
        self.write('{"projection":1' + '0' * 400 + '}')
        self.assertEqual(route.load_projection(self.config), 44.5)


class ConsentTable(Base):
    def scope(self, **over):
        r = self.enable()
        s = dict(run_id='r1', instance='i', output='DP-2', recovery_output='DP-1', model='pro2', tracking='pro2-sdk', mode_switch=False,
                 desktops=[UNIT], plugin=r['plugin'], renderer=r['renderer'], pro2_route=route.consent_block(r, 44.5))
        return {**s, **over}

    def test_refused(self):
        block = self.scope()['pro2_route']
        for block2 in ({**block, 'extra': 1}, {**block, 'projection': 46}, {**block, 'projection': True}, {**block, 'projection': float('nan')}, {**block, 'pose_rate_hz': True},
                       {**block, 'pose_rate_hz': 60.0}, {**block, 'pose_rate_hz': 500}, {**block, 'bundle': 'relative'}, {**block, 'bundle': '/a\0b'}, {**block, 'manifest_sha256': 'A' * 64},
                       {k: v for k, v in block.items() if k != 'pose_rate_hz'}, [], None):
            with self.subTest(block=block2), self.assertRaises((ValueError, TypeError, KeyError)): pc.build_consent(self.scope(pro2_route=block2))

    def test_a_3d_or_driver_key_cannot_ride_along(self):
        for extra in (dict(tracking_driver='3dof'), {'sbs_mode': {}}):
            with self.subTest(extra=extra), self.assertRaises(ValueError): pc.build_consent(self.scope(**extra))

    def consent(self): return pc.build_consent(self.scope())

    def test_require_refuses_a_consent_pin_that_is_not_the_bundle(self):
        consent = self.consent()
        for key in ('plugin', 'renderer'):
            bad = {**consent, key: {**consent[key], 'sha256': 'a' * 64}}
            with self.subTest(key=key), self.assertRaises(ValueError): route.require(bad, self.data)

    def test_require_catches_a_binary_changed_after_install(self):
        consent = self.consent()
        (self.bundle / 'bin/xr-native-spike').write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError, 'hash mismatch'): route.require(consent, self.data)

    def test_a_pin_file_with_open_permissions_is_off(self):
        self.enable(); (self.install / route.PIN_FILE).chmod(0o644)
        got, why = route.probe(self.data)
        self.assertIsNone(got); self.assertTrue(why)

    def test_a_symlinked_bundle_is_refused(self):
        link = self.dir / 'link'; link.symlink_to(self.bundle)
        with self.assertRaises(ValueError): route.write_pin(link, self.data)

    def test_a_pro2_consent_needs_the_route_block(self):
        s = self.scope(); s.pop('pro2_route')
        with self.assertRaises(ValueError): pc.build_consent({**s, 'tracking': False})


class OtherModels(Base):
    def test_identify_is_unchanged_with_the_route_on(self):
        for usb in ([dict(vendor_id='35ca', product_id='1104', product='VITURE Luma Ultra XR GLASSES')], [dict(vendor_id='35ca', product_id='1201', product='VITURE Beast XR Glasses')], []):
            self.assertEqual(identify(usb, [], MONITORS, ('pro2',)), identify(usb, [], MONITORS))

    def test_a_luma_consent_never_reaches_the_route(self):
        from tools.xr_controller import runner
        path = self.dir / 'luma.json'; path.write_text(json.dumps(dict(model='luma-ultra', run_id='r1'))); path.chmod(0o600)
        with patch('tools.xr_controller.pro2_route.execute') as go:
            result = runner.execute(path, self.dir)
        go.assert_not_called(); self.assertTrue(result['error'])

    def test_a_luma_snapshot_has_no_pro2_key_with_the_route_on(self):
        info = self.enable()
        observed = dict(model='luma-ultra', output='DP-2', instance='i', monitors=MONITORS, installed={}, plugins=[], journals=[], tracking_drivers=[], pro2_route=info)
        fd = os.open(self.dir, os.O_RDONLY); self.addCleanup(os.close, fd)
        units = Mock(); units.alive.return_value = False
        c = Controller(lambda: 0.0, lambda: observed, Mock(), units, Store(), fd, self.dir); c.route_config = self.config; c.route_data = self.data
        self.assertNotIn('pro2', c.snapshot())
        self.assertFalse(c.request(dict(op='set_pro2_projection', projection=46.5))['ok'])


class Session(Base):
    def setUp(self):
        super().setUp()
        self.runtime = self.dir / 'run'; self.runtime.mkdir(); self.state = self.dir / 'state'; self.state.mkdir()
        self.client = Mock(); self.client.plugins_loaded.side_effect = lambda: list(self.loaded)
        self.loaded = []
        self.client.plugin_load.side_effect = lambda p: self.loaded.append(OWNERSHIP_PLUGIN_NAME)
        self.client.plugin_unload.side_effect = lambda p: self.loaded.clear()
        self.consent = dict(run_id='r1', output='DP-2')

    def session(self, run, **over):
        with patch('tools.xr_controller.pro2_test.monitor_rules.refuse_start'):
            return pro2_test.run_session(self.client, self.consent, self.bundle, self.runtime, self.state, [UNIT], projection=44.5, seconds=100, panel_route=True,
                                         store=None, session_factory=over.get('factory', Provider), run=run)

    def journal(self, operations=()):
        path = self.runtime / 'xr-workspace-spike/r1/journal.json'; path.parent.mkdir(parents=True)
        path.write_text(json.dumps(dict(run_id='r1', operations=list(operations)))); path.chmod(0o600)

    def test_stop_while_the_bridge_starts_leaves_nothing(self):
        class Slow(Provider):
            def start(self): Provider.last = self; raise KeyboardInterrupt()
        out = self.session(Mock(side_effect=AssertionError('no run')), factory=Slow)
        self.client.plugin_load.assert_not_called(); self.assertEqual(Provider.last.closed, 1); self.assertTrue(out['errors'])

    def test_stop_during_the_run_closes_the_bridge_and_unloads(self):
        out = self.session(Mock(side_effect=KeyboardInterrupt()))
        self.client.plugin_unload.assert_called_once(); self.assertEqual(Provider.last.closed, 1); self.assertEqual(out['errors'], ['KeyboardInterrupt'])

    def test_an_unfinished_run_keeps_plugin_and_says_so(self):
        out = self.session(Mock(return_value=dict(complete=False, renderer_returncode=1)))
        self.client.plugin_unload.assert_not_called(); self.assertEqual(Provider.last.closed, 1); self.assertIn('ownership retained for explicit recovery', out['errors'])

    def test_a_failing_bridge_close_is_reported(self):
        class Bad(Provider):
            def close(self): raise RuntimeError('runtime failed')
        out = self.session(Mock(return_value=dict(complete=True, renderer_returncode=0)), factory=Bad)
        self.assertIn('runtime failed', out['errors'])

    def test_a_load_that_raised_is_still_unloaded(self):
        # runner.execute sets `attempted` before the load, for exactly this: an unconfirmed load must not leave a broker behind with
        # no journal, where nothing offers Recover and every Start says "already loaded".
        def half(path): self.loaded.append(OWNERSHIP_PLUGIN_NAME); raise ValueError('reply lost')
        self.client.plugin_load.side_effect = half
        self.session(Mock(side_effect=AssertionError('no run')))
        self.client.plugin_unload.assert_called_once()

    def test_an_unload_that_did_not_take_is_not_a_clean_end(self):
        # runner.execute reads the plugins back after unload; run_session does not.
        self.client.plugin_unload.side_effect = None
        out = self.session(Mock(return_value=dict(complete=True, renderer_returncode=0)))
        self.assertEqual(self.loaded, [OWNERSHIP_PLUGIN_NAME])
        self.assertTrue(out['errors'], 'a plugin still loaded after unload was reported as a clean end')

    def test_a_glasses_drop_ending_the_session_leaves_no_broker_behind(self):
        """The drop the docs call the end of a session: the bridge dies with the display, the hold waits, the glasses return.

        keep_renderer starts a new renderer; _launch's pro2_scope.healthy() raises; _run_owned's finally recovers everything; the exception
        goes on up. run_session then sees result None and a (finished) journal file, and keeps the plugin. Nothing offers Recover: the
        controller lists only journals with unfinished operations, and every later Start refuses with "ownership plugin already loaded"."""
        def run(client, consent, runtime, *, test_scope, desktops, store, renderer):
            self.journal()   # opened by the managed run; every operation done once recovery ran
            shown = iter([[], [dict(name='DP-2', disabled=False)]])
            client.query.side_effect = lambda *a, **k: next(shown, [dict(name='DP-2', disabled=False)])
            def launch_renderer():
                test_scope.healthy()   # _launch, before anything
                Provider.last.health(False)   # the bridge went with the USB device while this renderer ran
                return 2
            holds = survive.Holds(); holds.revive = lambda: False
            with patch.object(survive, 'usb_identity', return_value=None), patch.object(survive, 'SETTLE_S', 0), patch.object(managed, 'serve_without_renderer', lambda c, s: None):
                try: return managed.keep_renderer(launch_renderer, client, 'DP-2', {}, None, lambda: None, holds, tell=lambda text: None)
                finally: pass   # _run_owned's recovery has completed here
        out = self.session(run)
        self.client.plugin_unload.assert_called_once()
        self.assertEqual(self.loaded, [])


class Recover(Base):
    def consent_file(self):
        r = self.enable()
        c = pc.build_consent(dict(run_id='r1', instance='i', output='DP-2', recovery_output='DP-1', model='pro2', tracking='pro2-sdk', mode_switch=False,
                                  desktops=[UNIT], plugin=r['plugin'], renderer=r['renderer'], pro2_route=route.consent_block(r, 47.5)))
        p = self.dir / 'r1.json'; p.write_text(json.dumps(c)); p.chmod(0o600); return p

    def recover(self, path):
        client = Mock(); client.plugins_loaded.side_effect = [[OWNERSHIP_PLUGIN_NAME], []]
        with patch.dict(os.environ, XDG_RUNTIME_DIR=str(self.dir)), patch('tools.native_spike.managed.recover_pro2_test_managed', return_value=dict(complete=True)):
            return route.recover(path, self.dir / 'journal.json', (), client_factory=lambda *a: client)

    def test_recovery_runs_with_the_pin_gone(self):
        path = self.consent_file(); self.unpin()
        self.assertTrue(self.recover(path)['complete'])

    def test_recover_pinned_sends_a_pro2_run_to_the_route(self):
        from tools.xr_controller import __main__ as main
        path = self.consent_file()
        with patch.object(main.pro2_route, 'recover', return_value=dict(complete=True)) as go:
            main.recover_pinned(path, 'j', ('XR-1',))
        go.assert_called_once_with(path, 'j', ('XR-1',))

    def test_recovery_needs_only_the_plugin_it_unloads(self):
        # recover_pinned needs the run's plugin pin. This also needs every other file of the bundle: a renderer it will never start.
        path = self.consent_file()
        (self.bundle / 'bin/xr-native-spike').write_bytes(b'rebuilt')
        self.assertTrue(self.recover(path)['complete'])


class Competing(unittest.TestCase):
    def test_a_mapped_file_with_a_non_utf8_name_does_not_end_the_session(self):
        """competing() runs every 20 ms for the whole session (RuntimeSession.monitor via healthy). Only OSError is caught around a strict decode."""
        with tempfile.TemporaryDirectory() as d:
            name = os.fsencode(d) + b'/caf\xe9.bin'
            with open(name, 'wb') as f: f.write(b'x' * 4096)
            code = 'import mmap,sys,time\nf=open(sys.argv[1].encode("utf-8","surrogateescape"),"rb");m=mmap.mmap(f.fileno(),4096,prot=mmap.PROT_READ);print("up",flush=True);time.sleep(30)'
            child = subprocess.Popen([sys.executable, '-c', code, os.fsdecode(name)], stdout=subprocess.PIPE, text=True)
            try:
                child.stdout.readline()
                only = Mock(); only.iterdir.return_value = [Path(f'/proc/{child.pid}')]
                with patch.object(launch, 'Path', return_value=only):
                    self.assertFalse(launch.competing())
            finally:
                child.kill(); child.wait()


SIGINT_CHILD = '''
import json, os, sys, time
from pathlib import Path
from unittest.mock import Mock, patch
consent, marks = sys.argv[1], Path(sys.argv[2])
sys.argv = ['runner', '--consent', consent, '--state-dir', sys.argv[3]]
from tools.xr_controller import pro2_route as route, runner
from tools.native_spike.hyprland import OWNERSHIP_PLUGIN_NAME
class Session:
    def __init__(self, root, directory, projection, seconds, approved, **k):
        directory.mkdir(); self.prefix = str(directory / 'pose_'); self.projection = projection; self.nonce = 'n'; self.health_path = directory / 'h'; self.done = None
        self.health_path.write_text(json.dumps(dict(nonce='n', ready=True, monotonic=time.monotonic()))); self.health_path.chmod(0o600)
    def start(self): return self
    def close(self): (marks / 'closed').touch()
loaded = []
client = Mock(); client.plugins_loaded.side_effect = lambda: list(loaded)
client.plugin_load.side_effect = lambda p: loaded.append(OWNERSHIP_PLUGIN_NAME)
client.plugin_unload.side_effect = lambda p: ((marks / 'unloaded').touch(), loaded.clear())
def run(*a, **k):
    print('up', flush=True); time.sleep(30)
with patch('tools.pro2_runtime.scope.BundleClient', lambda *a: client), patch('tools.pro2_runtime.launch.RuntimeSession', Session), patch('tools.native_spike.managed.run_pro2_test_managed', run), \\
     patch('tools.xr_controller.pro2_test.monitor_rules.refuse_start'), patch.object(route, 'profiles_path', return_value=Path(os.environ['QA_CONFIG']) / 'profiles.json'):
    sys.exit(runner.main())
'''


class RealSigint(Base):
    def test_sigint_to_the_runner_cleans_up_and_writes_a_result(self):
        """What Stop sends (systemctl kill --kill-whom=main --signal=SIGINT), as a real signal to a real runner.main."""
        r = self.enable()
        c = pc.build_consent(dict(run_id='r1', instance='i', output='DP-2', recovery_output='DP-1', model='pro2', tracking='pro2-sdk', mode_switch=False,
                                  desktops=[UNIT], plugin=r['plugin'], renderer=r['renderer'], pro2_route=route.consent_block(r, 44.5)))
        path = self.dir / 'r1.json'; path.write_text(json.dumps(c)); path.chmod(0o600)
        self.dir.chmod(0o700)
        marks = self.dir / 'marks'; marks.mkdir()
        env = dict(os.environ, PYTHONPATH=str(ROOT), QA_CONFIG=str(self.config), XDG_RUNTIME_DIR=str(self.dir), XDG_CONFIG_HOME=str(self.dir), XDG_DATA_HOME=str(self.data))
        child = subprocess.Popen([sys.executable, '-c', SIGINT_CHILD, str(path), str(marks), str(self.dir)], cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            line = child.stdout.readline()
            self.assertEqual(line.strip(), 'up', child.stderr.read() if child.poll() is not None else '')
            child.send_signal(signal.SIGINT)
            child.wait(timeout=10)
        finally:
            if child.poll() is None: child.kill(); child.wait()
        self.assertTrue((marks / 'closed').exists()); self.assertTrue((marks / 'unloaded').exists())
        self.assertEqual(json.loads((self.dir / 'r1.result.json').read_text())['run_id'], 'r1')
