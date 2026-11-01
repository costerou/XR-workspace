"""Head-tracking plumbing: consent scope, renderer argv, recenter control, driver lifecycle."""
import json
import os
from pathlib import Path
import socket
import stat
import sys
import tempfile
import textwrap
import time
import unittest
from unittest.mock import patch

from tools.native_spike.profiles import LayoutStore
from tools.native_spike import tracking, view
from tools.native_spike.__main__ import load_consent
from tools.native_spike.supervisor import _launch

INSTANCE = 'a' * 40 + '_123_456'


class TrackingConsentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)

    def write(self, **changes):
        data = {'schema': 1, 'gate': 'P2', 'run_id': 'run', 'instance': INSTANCE, 'output': 'DP-2', 'recovery_output': 'DP-1',
                'allow': ['headless-outputs', 'capture-managed-outputs'], 'capture': 'managed-outputs', 'input': False,
                'sdk': False, 'mode_switch': False, **changes}
        path = Path(self.tmp.name) / f'c{len(os.listdir(self.tmp.name))}.json'
        path.write_text(json.dumps(data)); path.chmod(0o600)
        return path

    def test_tracking_scope_only_with_capture(self):
        self.assertEqual(load_consent(self.write(tracking='xrlinuxdriver'), INSTANCE, 'DP-2')['tracking'], 'xrlinuxdriver')
        self.assertNotIn('tracking', load_consent(self.write(), INSTANCE, 'DP-2'))
        for changes in ({'tracking': 'sdk'}, {'tracking': True},
                        {'tracking': 'xrlinuxdriver', 'capture': False, 'allow': ['headless-outputs', 'test-pattern']}):
            with self.subTest(changes), self.assertRaises(ValueError):
                load_consent(self.write(**changes), INSTANCE, 'DP-2')

    def test_launch_pose_feed_requires_capture(self):
        _, wayland = socket.socketpair()
        class Client: _peer = (*__import__('struct').unpack('3i', wayland.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)), 'x')
        seen = []
        class Child:
            def poll(self): return 0
        def spawn(argv, **kwargs):
            seen.append(argv); return Child()
        _launch(Client, 'run', 'session', 'DP-2', wayland, None, popen=spawn, capture=['XR-1'], pose_feed=True)
        self.assertEqual(seen[0][-4:], ['--capture', 'XR-1', '--pose-feed', 'xrld'])
        _, wayland2 = socket.socketpair()
        with self.assertRaises(ValueError):
            _launch(Client, 'run', 'session', 'DP-2', wayland2, None, popen=spawn, pose_feed=True)


class RecenterTests(unittest.TestCase):
    def test_recenter_op_sends_recenter_frame_without_view_change(self):
        state = view.ViewState(['XR-1', 'XR-2'])
        control = view.ViewControl(state, 'run', 'session', lambda name: None, LayoutStore(Path(tempfile.mkdtemp()) / 'l.json'), None)
        frame = control.on_message({'op': 'recenter'})
        self.assertEqual(json.loads(frame)['op'], 'recenter')
        self.assertEqual(state.view(), {'zoomed': False, 'selected': 0, 'spacing': view.DEFAULT_SPACING, 'mode': 'anchor', 'cursor_follow': False, 'prediction': True})
        self.assertIn(('CTRL + ALT + space', 'recenter'), view.BINDS)


class DriverLifecycleTests(unittest.TestCase):
    """Fake driver script stands in for /usr/bin/xrDriver; unshare network isolation is real."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.runtime = root / 'rt'; self.runtime.mkdir(mode=0o700)
        self.log = root / 'driver.log'; self.log.write_text('old line\n')
        self.config = root / 'config.ini'
        self.config.write_text('disabled=false\noutput_mode=external_only\nmetrics_disabled=true\n')
        self.driver = root / 'xrDriver'
        self.driver.write_text(textwrap.dedent(f'''\
            #!{sys.executable}
            import time, signal, sys
            signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))
            time.sleep(0.3)
            open({str(self.log)!r}, 'a').write('2026 Device calibration complete\\n')
            while True: time.sleep(0.1)
            '''))
        self.driver.chmod(0o700)
        self.patches = [patch.object(tracking, 'DRIVER', str(self.driver)), patch.object(tracking, 'DRIVER_LOG', self.log),
                        patch.object(tracking, 'DRIVER_CONFIG', self.config)]
        for p in self.patches: p.start(); self.addCleanup(p.stop)

    def test_disconnect_invalidates_previous_calibration(self):
        data = {'pid': os.getpid(), 'start_tick': tracking._start_tick(os.getpid()), 'log_offset': 0}
        tracking._state(self.runtime).write_text(json.dumps(data))
        self.log.write_text('Device calibration complete\nRetrying driver connection in 1 second\n')
        self.assertFalse(tracking.status(self.runtime)['calibrated'])
        with self.log.open('a') as log:
            log.write('Device connected, redirecting input to external_only...\nWaiting on device calibration\n')
        self.assertFalse(tracking.status(self.runtime)['calibrated'])
        with self.log.open('a') as log: log.write('Device calibration complete\n')
        self.assertTrue(tracking.status(self.runtime)['calibrated'])
        with self.log.open('a') as log: log.write('Failed to send USB command\n')
        self.assertFalse(tracking.status(self.runtime)['calibrated'])

    def test_ensure_ready_reuses_only_calibrated_isolated_driver(self):
        healthy = dict(running=True, network_isolated=True, calibrated=True)
        for state in (healthy, dict(healthy, calibrated=False), dict(healthy, network_isolated=False), dict(running=False)):
            with self.subTest(state=state), patch.object(tracking, 'status', return_value=state), \
                    patch.object(tracking, 'start', return_value=healthy) as start, patch.object(tracking, 'stop') as stop:
                self.assertEqual(tracking.ensure_ready(self.runtime), healthy)
                if state == healthy:
                    start.assert_not_called(); stop.assert_not_called()
                else:
                    start.assert_called_once_with(self.runtime, timeout=120.0)
                    self.assertEqual(stop.call_count, int(state['running']))

    def test_start_status_stop_network_blocked(self):
        status = tracking.start(self.runtime, timeout=10)
        self.addCleanup(lambda: tracking.stop(self.runtime))
        self.assertTrue(status['running'] and status['calibrated'] and status['network_isolated'], status)
        self.assertEqual(tracking.status(self.runtime)['pid'], status['pid'])
        with self.assertRaises(ValueError): tracking.start(self.runtime, timeout=10)  # already running
        self.assertTrue(tracking.stop(self.runtime)['stopped'])
        self.assertFalse(tracking.status(self.runtime)['running'])
        self.assertFalse(Path(f"/proc/{status['pid']}").exists() and Path(f"/proc/{status['pid']}/exe").exists()
                         and os.readlink(f"/proc/{status['pid']}/exe") == sys.executable)

    def test_refuses_config_that_could_drive_the_mouse(self):
        self.config.write_text('disabled=false\noutput_mode=mouse\n')
        with self.assertRaises(ValueError): tracking.start(self.runtime, timeout=5)
        self.config.write_text('output_mode=external_only\n')  # driver defaults to disabled=true
        with self.assertRaises(ValueError): tracking.start(self.runtime, timeout=5)

    def test_stop_never_signals_a_reused_pid(self):
        state = self.runtime / 'viture-workspace'; state.mkdir(mode=0o700)
        (state / 'tracking.json').write_text(json.dumps({'pid': os.getpid(), 'start_tick': '0'}))
        (state / 'tracking.json').chmod(0o600)
        self.assertFalse(tracking.stop(self.runtime)['stopped'])  # our own pid with a wrong tick: untouched


if __name__ == '__main__':
    unittest.main()


class IsolatedTrackingConsentTests(unittest.TestCase):
    def test_isolated_tracking_scope(self):
        from tools.native_spike import isolated
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        base = {'schema': 1, 'gate': 'VM-isolated', 'mode': 'isolated-vm', 'run_id': 'run', 'instance': 'b' * 40 + '_1_2',
                'boot_id': '0f0e0d0c-0b0a-4908-8706-050403020100', 'session': 's', 'runtime': '/run/user/1000',
                'output': 'Virtual-1', 'recovery_output': 'Virtual-2', 'allow': ['headless-outputs', 'capture-managed-outputs'],
                'capture': 'managed-outputs', 'input': False, 'sdk': False, 'mode_switch': False}
        for i, (changes, ok) in enumerate([({'tracking': 'xrlinuxdriver'}, True), ({'tracking': 'x'}, False),
                                            ({'tracking': 'xrlinuxdriver', 'capture': False, 'allow': ['headless-outputs', 'test-pattern']}, False)]):
            path = Path(tmp.name) / f'{i}.json'; path.write_text(json.dumps({**base, **changes})); path.chmod(0o600)
            if ok: self.assertEqual(isolated.load_isolated_consent(path, base['instance'], 'Virtual-1')['tracking'], 'xrlinuxdriver')
            else:
                with self.assertRaises(ValueError): isolated.load_isolated_consent(path, base['instance'], 'Virtual-1')

    def test_cli_tracking_status_needs_no_compositor(self):
        from tools.native_spike.__main__ import main
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        rt = Path(tmp.name) / 'rt'; rt.mkdir(mode=0o700)
        with patch.dict(os.environ, XDG_RUNTIME_DIR=str(rt)), patch('tools.native_spike.__main__.HyprlandClient') as client:
            self.assertEqual(main(['tracking', 'status']), 0)
        client.assert_not_called()
