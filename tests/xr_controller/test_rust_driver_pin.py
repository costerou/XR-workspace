"""The Rust Luma driver pinned beside the C++ one (docs/rust-rebuild-plan.md phase 2), and the one-command way back.

Real files in temp folders, inert binaries, the real install, override CLI, Host.probe and Controller. A "controller restart"
is a new Host and Controller reading the same folders: nothing survives it except what is on disk. Nothing is executed.
"""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from tools.native_spike import tracking
from tools.native_spike.panel_consent import LUMA_PIN
from tools.native_spike.profiles import DEFAULT_PROFILE
from tools.xr_controller import __main__ as cli, install as inst
from tools.xr_controller.state import Controller


class Store:
    def __init__(self): self.profile = copy.deepcopy(DEFAULT_PROFILE)
    def load(self, model): return copy.deepcopy(self.profile)
    def save(self, model, p): self.profile = copy.deepcopy(p)


class Units:
    def alive(self, run_id): return False
    def alive_many(self, runs): return {r: False for r in runs}
    def control(self, run_id, s, op): return True


class RustPin(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory(); self.addCleanup(t.cleanup)
        self.root = Path(t.name).resolve()
        self.data, self.config, self.state, self.runtime = (self.root / n for n in ('data', 'config', 'state', 'runtime'))
        for d in (self.config, self.state, self.runtime): d.mkdir(mode=0o700)
        self.build = self.root / 'build'; (self.build / 'driver').mkdir(parents=True)
        for name in ('xr-native-spike', 'libxr-ownership.so', 'driver/xr-luma-driver'): (self.build / name).write_bytes(b'c++ ' + name.encode())
        self.rust = self.root / 'cargo' / 'xr-luma-driver'; self.rust.parent.mkdir(); self.rust.write_bytes(b'rust xr-luma-driver')
        for patch in (mock.patch.object(tracking, 'OVERRIDE_FILE', self.config / 'viture-workspace/tracking-override.json'),
                      mock.patch.object(inst, '_write_engine', lambda target, *a, **k: None)):
            patch.start(); self.addCleanup(patch.stop)

    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, XDG_DATA_HOME=str(self.data), XDG_CONFIG_HOME=str(self.config)), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                mock.patch.object(cli, 'install_pro2_bundle', return_value=dict(bundle='/b')), mock.patch.object(cli, 'install_unit'):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def install(self, rust=True):
        code, out, err = self.cli('install', '--build', str(self.build), *(('--rust-driver', str(self.rust)) if rust else ()))
        self.assertEqual(code, 0, err)
        return json.loads(out)

    def controller(self):
        """A freshly started controller: a new Host and Controller, with a Luma Ultra on DP-2."""
        host = cli.Host(self.runtime, self.state, self.data)
        fd = os.open(self.state, os.O_DIRECTORY); self.addCleanup(os.close, fd)
        with mock.patch.object(cli, 'HyprlandClient') as client, mock.patch.object(cli, 'probe_usb'), mock.patch.object(cli, 'probe_drm'), \
                mock.patch.object(cli, 'identify', return_value=dict(model='luma-ultra', output='DP-2', reason=None)), \
                mock.patch.object(cli.monitor_rules, 'leftovers', return_value=[]):
            client.return_value.query.return_value = [dict(name='DP-2', model='VITURE'), dict(name='DP-1', model='Dell')]
            client.return_value.plugins_loaded.return_value = []
            observed = host.probe()
        self.assertIsNone(observed['reason'])
        c = Controller(lambda: 0, lambda: copy.deepcopy(observed), lambda *a: None, Units(), Store(), fd, self.state)
        c.tick()
        return c

    def start(self, c):
        """What a Start writes, checked the way the runner checks it before it runs the driver."""
        reply = c.request(dict(op='begin_start')); self.assertTrue(reply['ok'], reply)
        self.assertTrue(c.request(dict(op='confirm_start', scope_id=reply['scope_id']))['ok'])
        consent = json.loads((self.state / (reply['scope']['run_id'] + '.json')).read_text())
        self.assertEqual(consent['tracking'], tracking.LUMA)
        installed = inst.load_installed(self.data)  # runner.run: the consent's pin must be the pinned install, bytes unchanged
        self.assertEqual(consent[LUMA_PIN], installed[LUMA_PIN]); inst.verify_pinned(installed[LUMA_PIN])
        return Path(consent[LUMA_PIN]['path']).read_bytes(), c.snapshot()['tracking_in_use']

    def test_install_pins_both_and_a_luma_start_runs_the_rust_one(self):
        entry = self.install()
        self.assertEqual(Path(entry['rust_luma_driver']['path']).name, tracking.LUMA_BINARY)  # tracking._other_drivers finds it
        on_disk = json.loads((self.data / 'viture-workspace/installed.json').read_text())
        self.assertEqual(Path(on_disk[LUMA_PIN]['path']).read_bytes(), b'c++ driver/xr-luma-driver')  # an older controller's C++
        driver, in_use = self.start(self.controller())
        self.assertEqual(driver, b'rust xr-luma-driver')
        self.assertEqual((in_use['key'], in_use['available'], in_use['note']), (tracking.LUMA, True, None))

    def test_the_way_back_is_one_command_and_survives_a_controller_restart(self):
        self.install()
        self.assertEqual(self.start(self.controller())[0], b'rust xr-luma-driver')
        code, out, _ = self.cli('tracking-override', 'set', 'luma-cpp')
        self.assertEqual((code, json.loads(out)), (0, dict(override='luma-cpp')))
        for restart in range(2):
            with self.subTest(restart=restart):
                driver, in_use = self.start(self.controller())
                self.assertEqual(driver, b'c++ driver/xr-luma-driver')
                self.assertEqual((in_use['key'], in_use['label'], in_use['available']), ('luma-cpp', 'XR Luma driver (C++)', True))
                self.assertIn('Recovery setting', in_use['note'])  # the panel's amber line, so it cannot be forgotten
        self.assertEqual(self.cli('tracking-override', 'clear')[0], 0)
        self.assertEqual(self.start(self.controller())[0], b'rust xr-luma-driver')

    def test_a_plain_install_drops_the_rust_pin(self):
        first = self.install()
        self.install(rust=False)
        self.assertFalse((self.data / 'viture-workspace' / inst.RUST_LUMA_PIN).exists())
        self.assertEqual(Path(inst.load_installed(self.data)[LUMA_PIN]['path']).read_bytes(), b'c++ driver/xr-luma-driver')
        self.assertTrue(Path(first['rust_luma_driver']['path']).is_file())  # the old folder stays for a running session

    def test_without_a_rust_build_nothing_changes(self):
        entry = self.install(rust=False)
        self.assertNotIn('rust_luma_driver', entry)
        self.assertEqual(inst.load_installed(self.data), json.loads((self.data / 'viture-workspace/installed.json').read_text()))

    def test_a_broken_rust_pin_refuses_but_never_blocks_the_way_back(self):
        self.install()
        pin = self.data / 'viture-workspace' / inst.RUST_LUMA_PIN
        for text in ('{', json.dumps(dict(path='/usr/bin/sh', sha256='a' * 64))):
            with self.subTest(text=text):
                pin.write_text(text)
                with self.assertRaises(ValueError): inst.load_installed(self.data)
                tracking.save_override(tracking.LUMA_CPP)
                self.assertEqual(Path(inst.load_installed(self.data)[LUMA_PIN]['path']).read_bytes(), b'c++ driver/xr-luma-driver')
                tracking.clear_override()

    def test_luma_cpp_never_runs_xrlinuxdriver(self):
        self.install()
        tracking.save_override(tracking.LUMA_CPP)
        reply = self.controller().request(dict(op='begin_start'))
        self.assertEqual(reply['scope']['tracking'], tracking.LUMA); self.assertNotIn('tracking_driver', reply['scope'])


if __name__ == '__main__':
    unittest.main()
