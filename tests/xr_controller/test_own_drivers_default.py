"""Our own drivers are the default: the installer's Pro 2 bundle, the hidden way back, the Pro 2's settings and swapping glasses."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock

from tools.native_spike import tracking
from tools.native_spike.display_policy import PRO2_MOUNT_BOUNDS, PRO2_POSE_RATE
from tools.pro2_diagnostic.candidate import SHA256
from tools.pro2_runtime import launch
from tools.pro2_runtime.scope import SMOOTH_MS, TestScope
from tools.xr_controller import __main__ as cli, install as inst, pro2_route as route
from tools.xr_controller.incident_detect import RunWatch


def fake_builder(tag=b'inert '):
    """What tools.pro2_runtime.package.build leaves, without a compiler."""
    def build(build_dir, destination):
        destination = Path(destination); (destination / 'bin').mkdir(parents=True)
        files = {}
        for name in ('pro2-sdk-bridge', 'xr-native-spike', 'libxr-ownership.so'):
            path = destination / 'bin' / name; path.write_bytes(tag + name.encode()); files['bin/' + name] = hashlib.sha256(path.read_bytes()).hexdigest()
        (destination / 'manifest.json').write_text(json.dumps(dict(schema=1, sdk_sha256=SHA256, files=files)))
    return build


class Dirs(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory(); self.addCleanup(t.cleanup)
        self.root = Path(t.name).resolve(); self.data = self.root / 'data'; self.config = self.root / 'config'; self.config.mkdir()


class InstalledBundle(Dirs):
    def test_install_pins_a_bundle_named_by_its_manifest_with_no_other_step(self):
        got = inst.install_pro2_bundle('/build', self.data, builder=fake_builder())
        self.assertEqual(Path(got['bundle']).parent, self.data / 'viture-workspace' / 'pro2')
        self.assertEqual(Path(got['bundle']).name, got['manifest_sha256'][:12])
        self.assertEqual(route.probe(self.data), (route.load_route(self.data), None))
        self.assertEqual(stat.S_IMODE(os.stat(self.data / 'viture-workspace' / route.PIN_FILE).st_mode), 0o600)
        self.assertEqual(got['renderer']['path'], got['bundle'] + '/bin/xr-native-spike')

    def test_the_same_build_again_keeps_the_folder(self):
        first = inst.install_pro2_bundle('/build', self.data, builder=fake_builder())
        second = inst.install_pro2_bundle('/build', self.data, builder=fake_builder())
        self.assertEqual(first['bundle'], second['bundle'])
        self.assertEqual([p.name for p in (self.data / 'viture-workspace/pro2').iterdir()], [Path(first['bundle']).name])

    def test_a_new_build_keeps_the_old_folder_for_a_running_session_and_a_rollback(self):
        first = inst.install_pro2_bundle('/build', self.data, builder=fake_builder())
        second = inst.install_pro2_bundle('/build', self.data, builder=fake_builder(b'newer '))
        self.assertNotEqual(first['bundle'], second['bundle'])
        self.assertTrue(Path(first['bundle']).is_dir())
        self.assertEqual(route.load_route(self.data)['bundle'], second['bundle'])

    def test_a_build_that_fails_leaves_the_pin_and_no_debris(self):
        first = inst.install_pro2_bundle('/build', self.data, builder=fake_builder())
        def broken(build_dir, destination):
            Path(destination).mkdir(); raise ValueError('compiler said no')
        with self.assertRaisesRegex(ValueError, 'compiler'): inst.install_pro2_bundle('/build', self.data, builder=broken)
        self.assertEqual(route.load_route(self.data)['bundle'], first['bundle'])
        self.assertEqual([p.name for p in (self.data / 'viture-workspace/pro2').iterdir()], [Path(first['bundle']).name])

    def test_the_real_builder_lands_on_a_path_the_bundle_grammar_accepts(self):
        with mock.patch('tools.pro2_runtime.package.build', fake_builder()):
            got = inst.install_pro2_bundle('/build', self.data)
        self.assertEqual(launch.validate_bundle_location(got['bundle']), Path(got['bundle']))

    def test_the_cli_install_refuses_a_build_without_our_driver_and_changes_nothing(self):
        build = self.root / 'build'; build.mkdir()
        (build / 'xr-native-spike').write_bytes(b'r'); (build / 'libxr-ownership.so').write_bytes(b'p')
        err = io.StringIO()
        with mock.patch.dict(os.environ, XDG_DATA_HOME=str(self.data), XDG_CONFIG_HOME=str(self.config)), contextlib.redirect_stderr(err), \
                mock.patch.object(cli, 'install_pro2_bundle') as bundle, mock.patch.object(cli, 'install_unit') as unit:
            self.assertEqual(cli.main(['install', '--build', str(build)]), 1)
        self.assertIn('xr-luma-driver', err.getvalue()); bundle.assert_not_called(); unit.assert_not_called()
        self.assertFalse((self.data / 'viture-workspace/installed.json').exists())

    def test_the_cli_install_does_the_bundle_first_so_a_failure_leaves_the_install_alone(self):
        build = self.root / 'build'; (build / 'driver').mkdir(parents=True)
        for path in ('xr-native-spike', 'libxr-ownership.so', 'driver/xr-luma-driver'): (build / path).write_bytes(path.encode())
        with mock.patch.dict(os.environ, XDG_DATA_HOME=str(self.data), XDG_CONFIG_HOME=str(self.config)), contextlib.redirect_stderr(io.StringIO()), \
                mock.patch.object(cli, 'install_pro2_bundle', side_effect=ValueError('no')), mock.patch.object(cli, 'install_unit') as unit:
            self.assertEqual(cli.main(['install', '--build', str(build)]), 1)
        unit.assert_not_called(); self.assertFalse((self.data / 'viture-workspace/installed.json').exists())


class Override(Dirs):
    def path(self): return self.config / 'tracking-override.json'

    def test_nothing_saved_is_no_override(self):
        self.assertIsNone(tracking.load_override(self.path()))

    def test_both_values_round_trip_privately(self):
        for driver in tracking.OVERRIDES:
            tracking.save_override(driver, self.path()); self.assertEqual(tracking.load_override(self.path()), driver)
        self.assertEqual(stat.S_IMODE(self.path().stat().st_mode), 0o600)
        tracking.clear_override(self.path()); self.assertIsNone(tracking.load_override(self.path()))

    def test_our_own_driver_and_anything_else_are_refused(self):
        for bad in ('luma', '/usr/bin/xrDriver', '', None):
            with self.subTest(bad=bad), self.assertRaises(ValueError): tracking.save_override(bad, self.path())

    def test_a_broken_file_is_no_override(self):
        for text in ('{', '[]', '{"schema":1,"driver":"luma"}', '{"schema":2,"driver":"xrdriver"}', '{"schema":1,"driver":"xrdriver","x":1}'):
            with self.subTest(text=text):
                self.path().write_text(text); self.path().chmod(0o600)
                self.assertIsNone(tracking.load_override(self.path()))

    def test_open_permissions_are_no_override(self):
        tracking.save_override('xrdriver', self.path()); self.path().chmod(0o644)
        self.assertIsNone(tracking.load_override(self.path()))

    def test_the_cli_sets_shows_and_clears_it_and_is_not_in_the_help(self):
        out = io.StringIO()
        with mock.patch.object(tracking, 'OVERRIDE_FILE', self.path()), contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(['tracking-override', 'set', 'xrdriver']), 0)
            self.assertEqual(cli.main(['tracking-override', 'status']), 0)
            self.assertEqual(cli.main(['tracking-override', 'clear']), 0)
        self.assertEqual([json.loads(l)['override'] for l in out.getvalue().splitlines()], ['xrdriver', 'xrdriver', None])
        helped = io.StringIO()
        with contextlib.redirect_stdout(helped), self.assertRaises(SystemExit): cli.main(['--help'])
        self.assertNotIn('recovery', helped.getvalue().lower()); self.assertNotIn('XRLinuxDriver', helped.getvalue())

    def test_the_cli_says_when_set_has_no_driver(self):
        err = io.StringIO()
        with mock.patch.object(tracking, 'OVERRIDE_FILE', self.path()), contextlib.redirect_stderr(err):
            self.assertEqual(cli.main(['tracking-override', 'set']), 1)
        self.assertIn('xrdriver', err.getvalue())


class Settings(Dirs):
    def write(self, text):
        path = self.config / route.SETTINGS_FILE; path.write_text(text); path.chmod(0o600)

    def test_defaults(self):
        self.assertEqual(route.load_settings(self.config), dict(projection=44.5, mount_pitch_deg=-8.5, pose_rate_hz=60))
        self.assertEqual(PRO2_POSE_RATE, 60)

    def test_the_alternative_is_one_setting_away_and_nothing_else_moves(self):
        route.save_settings(dict(projection=46.5), self.config)
        route.save_settings(dict(mount_pitch_deg=-11), self.config)
        self.assertEqual(route.load_settings(self.config), dict(projection=46.5, mount_pitch_deg=-11.0, pose_rate_hz=60))
        route.save_settings(dict(pose_rate_hz=120), self.config)
        self.assertEqual(route.load_settings(self.config), dict(projection=46.5, mount_pitch_deg=-11.0, pose_rate_hz=120))

    def test_a_file_from_before_these_settings_keeps_its_projection(self):
        self.write('{"schema":1,"projection":47.5}')
        self.assertEqual(route.load_settings(self.config), dict(projection=47.5, mount_pitch_deg=-8.5, pose_rate_hz=60))

    def test_each_value_stands_alone(self):
        self.write('{"schema":1,"projection":52,"mount_pitch_deg":-11,"pose_rate_hz":1000}')
        self.assertEqual(route.load_settings(self.config), dict(projection=44.5, mount_pitch_deg=-11.0, pose_rate_hz=60))
        self.write('{"mount_pitch_deg":true,"pose_rate_hz":120.0}')
        self.assertEqual(route.load_settings(self.config), dict(projection=44.5, mount_pitch_deg=-8.5, pose_rate_hz=60))
        for text in ('[]', '{', '', '{"mount_pitch_deg":1' + '0' * 400 + '}'):
            with self.subTest(text=text[:20]):
                self.write(text); self.assertEqual(route.load_settings(self.config)['mount_pitch_deg'], -8.5)

    def test_refused_values_change_nothing(self):
        route.save_settings(dict(mount_pitch_deg=-11), self.config)
        low, high = PRO2_MOUNT_BOUNDS
        for change in (dict(mount_pitch_deg=low - 1), dict(mount_pitch_deg=high + 1), dict(mount_pitch_deg=float('nan')), dict(mount_pitch_deg=True),
                       dict(pose_rate_hz=30), dict(pose_rate_hz=120.0), dict(projection=52), dict(other=1), {}):
            with self.subTest(change=change), self.assertRaises(ValueError): route.save_settings(change, self.config)
        self.assertEqual(route.load_settings(self.config)['mount_pitch_deg'], -11.0)

    def test_the_cli(self):
        out = io.StringIO()
        with mock.patch.object(route, 'profiles_path', return_value=self.config / 'profiles.json'), contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(['pro2-settings']), 0)
            self.assertEqual(cli.main(['pro2-settings', '--mount-pitch', '-11', '--pose-rate', '120']), 0)
        lines = [json.loads(l) for l in out.getvalue().splitlines()]
        self.assertEqual((lines[0]['mount_pitch_deg'], lines[1]['mount_pitch_deg'], lines[1]['pose_rate_hz']), (-8.5, -11.0, 120))
        err = io.StringIO()
        with mock.patch.object(route, 'profiles_path', return_value=self.config / 'profiles.json'), contextlib.redirect_stderr(err):
            self.assertEqual(cli.main(['pro2-settings', '--pose-rate', '30']), 1)
        self.assertIn('pose rate', err.getvalue())

    def test_route_status_cannot_enable_anything(self):
        out = io.StringIO()
        with mock.patch.dict(os.environ, XDG_DATA_HOME=str(self.data)), contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(['pro2-route']), 0)
        self.assertIs(json.loads(out.getvalue())['installed'], False)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit): cli.main(['pro2-route', 'enable'])

    def test_smoothing_is_on_for_a_route_session(self):
        self.assertEqual(SMOOTH_MS, 35.0)
        self.assertEqual(TestScope('/p', 44.5, '/h', 'n', fixed_desktops=False).smooth_ms, 35.0)


class SwappingGlasses(Dirs):
    def test_our_stale_driver_is_stopped_before_the_bridge_looks_for_competitors(self):
        with mock.patch.object(tracking, 'stop') as stop: route.release_tracking_driver('/run')
        stop.assert_called_once_with('/run')

    def test_a_driver_we_did_not_start_is_left_alone(self):
        runtime = self.root / 'run'; runtime.mkdir(mode=0o700)
        with mock.patch.object(tracking.os, 'kill') as kill: route.release_tracking_driver(runtime)
        kill.assert_not_called()


class IncidentScope(unittest.TestCase):
    def test_a_route_session_watches_head_tracking_but_no_driver_process(self):
        watch = RunWatch('r', dict(tracking='pro2-sdk'), 0.0)
        self.assertIs(watch.tracking, True); self.assertIs(watch.driver_process, False)

    def test_the_drivers_of_tracking_py_are_watched_as_before(self):
        for scope in tracking.SCOPES:
            watch = RunWatch('r', dict(tracking=scope), 0.0)
            self.assertIs(watch.tracking, True); self.assertIs(watch.driver_process, True)
        self.assertIs(RunWatch('r', dict(tracking=False), 0.0).tracking, False)


if __name__ == '__main__': unittest.main()
