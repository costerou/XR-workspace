"""Head-tracking plumbing: consent scope, renderer argv, recenter control, driver lifecycle."""
import json
import os
from pathlib import Path
import socket
import stat
import subprocess
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
# As they appear in the real driver.log of 2026-11-04.
CRASH_LINES = ('[E][libglasses] Failed to send USB command - MsgID: 0x100B, error code: -1',
               '[CARINA][TIME]:3044930524\t[PID:474055][TID:474076]\tCannot open device, may not connect',
               '[CARINA][TIME]:3044930538\t[PID:474055][TID:474076]\tjcx start failed[35ca,1104]',
               '2026-11-04 00:22:00.932 [ERROR] Segmentation fault occurred')
CRASH_MESSAGE = ("The glasses' head tracking couldn't connect and the tracking driver crashed. "
                 'Unplug the glasses\' USB, wait 10 seconds, plug it back in and try again.')


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
        self.assertEqual(state.view(), {'zoomed': False, 'selected': 0, 'spacing': view.DEFAULT_SPACING, 'mode': 'anchor', 'cursor_follow': False, 'prediction': True, 'typing': False})
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

    def test_cancel_during_calibration_stops_owned_driver(self):
        with (
            patch.object(tracking, '_ours', return_value=False),
            patch.object(tracking, '_other_drivers', return_value=[]),
            patch.object(tracking, '_start_tick', return_value='1'),
            patch.object(tracking.subprocess, 'Popen', return_value=type('Child', (), {'pid':123})()),
            patch.object(tracking, 'status', side_effect=KeyboardInterrupt),
            patch.object(tracking, 'stop') as stop,
        ):
            with self.assertRaises(KeyboardInterrupt): tracking.start(self.runtime)
            stop.assert_called_once_with(self.runtime)

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

    def test_log_offset_is_in_bytes_whatever_the_earlier_log_holds(self):
        # The driver prints freed memory into a licence error line, so the log is not plain text.
        earlier = b'\xc3\xa9' * 100 + b'[ERROR] Error opening file (\xa7\xac^v\xdcU): 2\n'
        self.log.write_bytes(earlier + b'Waiting on device calibration\nDevice calibration complete\n')
        data = {'pid': os.getpid(), 'start_tick': tracking._start_tick(os.getpid()), 'log_offset': len(earlier)}
        tracking._state(self.runtime).write_text(json.dumps(data))
        self.assertTrue(tracking.status(self.runtime)['calibrated'])

    def test_another_process_can_see_and_stop_the_nested_driver(self):
        # The controller and the CLI are not the driver's parent, and it sits two user namespaces down.
        status = tracking.start(self.runtime, timeout=10)
        self.addCleanup(lambda: tracking.stop(self.runtime))
        script = textwrap.dedent(f'''
            import json, sys
            from pathlib import Path
            from tools.native_spike import tracking
            tracking.DRIVER_LOG = Path({str(self.log)!r})
            print(json.dumps([tracking.status({str(self.runtime)!r}), tracking.stop({str(self.runtime)!r}, timeout=1)]))
            ''')
        out = subprocess.run([sys.executable, '-c', script], check=True, capture_output=True, text=True, timeout=20).stdout
        seen, stopped = json.loads(out)
        self.assertEqual(seen, status)
        self.assertEqual(stopped, {'stopped': True})
        self.assertFalse(tracking.status(self.runtime)['running'])

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
        self.assertEqual(tracking.ensure_ready(self.runtime), status)  # warm path: the same driver, untouched
        with self.assertRaises(ValueError): tracking.start(self.runtime, timeout=10)  # already running
        self.assertTrue(tracking.stop(self.runtime)['stopped'])
        self.assertFalse(tracking.status(self.runtime)['running'])
        self.assertFalse(Path(f"/proc/{status['pid']}").exists() and Path(f"/proc/{status['pid']}/exe").exists()
                         and os.readlink(f"/proc/{status['pid']}/exe") == sys.executable)

    def fake_driver(self, *lines, then='sys.exit(1)'):
        """A driver that writes these log lines, then does `then`."""
        text = ''.join(line + '\n' for line in lines)
        self.driver.write_text(textwrap.dedent(f'''\
            #!{sys.executable}
            import signal, sys, time
            signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))
            open({str(self.log)!r}, 'a').write({text!r})
            {then}
            '''))

    def failure(self):
        with self.assertRaises(ValueError) as caught: tracking.start(self.runtime, timeout=10)
        return str(caught.exception)

    def test_driver_that_crashes_at_start_gets_the_plain_message(self):
        self.fake_driver(*CRASH_LINES)
        started = time.monotonic()
        self.assertEqual(self.failure(), CRASH_MESSAGE)
        self.assertLess(time.monotonic() - started, 4)  # at once, not after waiting for a process already gone
        self.assertFalse(tracking._state(self.runtime).exists())

    def test_any_one_crash_line_is_enough(self):
        for line in CRASH_LINES:
            with self.subTest(line):
                self.fake_driver(line)
                self.assertEqual(self.failure(), CRASH_MESSAGE)

    def test_a_crash_logged_before_this_start_is_not_this_start(self):
        self.log.write_text(''.join(line + '\n' for line in CRASH_LINES))
        self.fake_driver('Starting up XR driver')
        self.assertEqual(self.failure(), 'tracking driver exited during startup')

    def test_exit_with_no_crash_in_the_log_blames_no_usb(self):
        self.fake_driver('Starting up XR driver')
        self.assertEqual(self.failure(), 'tracking driver exited during startup')

    def test_live_driver_outside_the_namespace_is_still_refused(self):
        # The crash lines must not excuse it: the process is alive, so isolation is the failure.
        self.fake_driver(*CRASH_LINES, then='while True: time.sleep(0.1)')
        with patch.object(tracking, '_network_isolated', return_value=False):
            self.assertEqual(self.failure(), 'tracking driver is not network-isolated; stopped')
        self.assertFalse(tracking.status(self.runtime)['running'])

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

    def test_ensure_ready_reuses_a_driver_started_by_the_previous_version(self):
        # A run started before this update is plain `--map-current-user --net`, with no made-up MAC.
        child = subprocess.Popen(['unshare', '--user', '--map-current-user', '--net', '--', str(self.driver)],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(child.wait); self.addCleanup(child.kill)
        deadline = time.monotonic() + 5
        while 'Device calibration complete' not in self.log.read_text() and time.monotonic() < deadline: time.sleep(0.02)
        state = {'pid': child.pid, 'start_tick': tracking._start_tick(child.pid), 'log_offset': 0}
        tracking._state(self.runtime).write_text(json.dumps(state))
        with patch.object(tracking, 'start') as start:
            ready = tracking.ensure_ready(self.runtime)
        start.assert_not_called()
        self.assertEqual(ready, {'running': True, 'pid': child.pid, 'network_isolated': True, 'calibrated': True})


class NetworkIsolationCheckTests(unittest.TestCase):
    NAMESPACES = {f'/proc/1234/ns/net': 'net:[2]', '/proc/self/ns/net': 'net:[1]'}

    def isolated(self, interfaces, namespaces=NAMESPACES):
        with patch.object(tracking.os, 'readlink', side_effect=namespaces.__getitem__), \
                patch.object(tracking, '_interfaces', return_value=interfaces):
            return tracking._network_isolated(1234)

    def test_own_namespace_holding_only_lo_is_isolated(self):
        self.assertTrue(self.isolated(['lo']))

    def test_any_other_interface_is_a_way_off_the_machine(self):
        for interfaces in (['lo', 'eth0'], ['eth0'], ['lo', 'tailscale0'], ['lo', 'wlan0']):
            with self.subTest(interfaces=interfaces): self.assertFalse(self.isolated(interfaces))

    def test_sharing_our_namespace_is_not_isolated(self):
        self.assertFalse(self.isolated(['lo'], {'/proc/1234/ns/net': 'net:[1]', '/proc/self/ns/net': 'net:[1]'}))
        self.assertFalse(tracking._network_isolated(os.getpid()))  # the real check, on this very process

    def test_process_that_cannot_be_read_is_not_isolated(self):
        with patch.object(tracking.os, 'readlink', side_effect=OSError):
            self.assertFalse(tracking._network_isolated(1234))
        with patch.object(tracking.os, 'readlink', side_effect=self.NAMESPACES.__getitem__), \
                patch.object(tracking, '_interfaces', side_effect=OSError):
            self.assertFalse(tracking._network_isolated(1234))

    def test_interfaces_are_read_from_the_process_namespace(self):
        child = subprocess.Popen(['unshare', '--user', '--map-current-user', '--net', '--', 'sleep', '5'])
        self.addCleanup(child.wait); self.addCleanup(child.kill)
        deadline = time.monotonic() + 2
        while os.readlink(f'/proc/{child.pid}/ns/net') == os.readlink('/proc/self/ns/net') and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(tracking._interfaces(child.pid), ['lo'])
        self.assertTrue(tracking._network_isolated(child.pid))


PROBE = '''#!{python}
import errno, fcntl, json, os, socket, struct, time
SIOCGIFFLAGS, SIOCSIFFLAGS, SIOCGIFHWADDR, SIOCGIFADDR, IFF_UP = 0x8913, 0x8914, 0x8927, 0x8915, 1

def lo(op, flags=0):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        return fcntl.ioctl(s, op, struct.pack('16sH38x', b'lo', flags))

def reach(address):
    with socket.socket() as s:
        s.settimeout(3); started = time.monotonic()
        try: s.connect((address, 443)); result = 'connected'
        except OSError as e: result = errno.errorcode.get(e.errno, str(e))
        return result, time.monotonic() - started

def lookup():
    started = time.monotonic()
    try: socket.getaddrinfo('example.invalid', 443); result = 'resolved'
    except socket.gaierror: result = 'failed'
    return result, time.monotonic() - started

def has_ipv4():
    try: lo(SIOCGIFADDR); return True
    except OSError: return False

def bring_up_lo():
    try: lo(SIOCSIFFLAGS, IFF_UP); return 'allowed'
    except OSError as e: return errno.errorcode.get(e.errno, str(e))

status = dict(line.split(':', 1) for line in open('/proc/self/status') if ':' in line)
json.dump({{
    'pid': os.getpid(), 'uid': os.getuid(), 'gid': os.getgid(),
    'caps': [int(status[k], 16) for k in ('CapEff', 'CapPrm', 'CapInh', 'CapAmb')],
    'interfaces': [name for _, name in socket.if_nameindex()],
    'lo_up': bool(struct.unpack('16sH', lo(SIOCGIFFLAGS)[:18])[1] & IFF_UP),
    'mac': lo(SIOCGIFHWADDR)[18:24].hex(':'), 'ipv4': has_ipv4(),
    'routes': [l for f in ('route', 'ipv6_route') for l in open('/proc/net/' + f).read().splitlines()[f == 'route':]
               if ' 00200200 ' not in l],  # IPv6's built-in reject entries are not routes
    'off_machine': reach('198.51.100.1'), 'loopback': reach('127.0.0.1'), 'lookup': lookup(), 'bring_up_lo': bring_up_lo(),
    'resolver': sorted(os.listdir({resolver!r})) if os.path.isdir({resolver!r}) else None,
}}, open({out!r}, 'w'))
'''


class NetworkBlockTests(unittest.TestCase):
    """The real namespace setup, with a probe where the driver goes. Nothing here touches a driver or the glasses."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.out = self.root / 'probe.json'

    def probe(self, resolver):
        driver = self.root / 'xrDriver'
        driver.write_text(PROBE.format(python=sys.executable, resolver=str(resolver), out=str(self.out)))
        driver.chmod(0o700)
        with patch.object(tracking, 'DRIVER', str(driver)), patch.object(tracking, 'RESOLVED_DIR', str(resolver)):
            argv = tracking._driver_argv()
        child = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(child.kill)
        self.assertEqual(child.wait(timeout=30), 0)
        return json.loads(self.out.read_text()), child.pid

    def resolver(self):
        """A stand-in for systemd-resolved: a filesystem socket, which a network namespace does not cover."""
        directory = self.root / 'resolve'; directory.mkdir()
        server = socket.socket(socket.AF_UNIX); self.addCleanup(server.close)
        server.bind(str(directory / 'io.systemd.Resolve')); server.listen()
        return directory

    def test_driver_gets_a_made_up_mac_and_still_no_way_out(self):
        directory = self.resolver()
        seen, _ = self.probe(directory)
        self.assertEqual(seen['mac'], tracking.FAKE_MAC)  # what ends the driver's 30 s wait...
        self.assertTrue(seen['ipv4'])                     # ...which only looks at interfaces with an IPv4 address
        self.assertEqual(seen['interfaces'], ['lo'])
        self.assertFalse(seen['lo_up'])
        self.assertEqual(seen['routes'], [])
        for name in ('off_machine', 'loopback'):
            self.assertEqual(seen[name][0], 'ENETUNREACH', name)
            self.assertLess(seen[name][1], 0.5, name)  # fails at once: nothing for a licence attempt to wait on

    @unittest.skipUnless(os.path.isdir(tracking.RESOLVED_DIR), 'needs systemd-resolved')
    def test_a_name_lookup_fails_at_once_with_the_real_resolver_hidden(self):
        # Three attempts per start go through this, each allowed 15 s by the driver if it were to hang.
        seen, _ = self.probe(tracking.RESOLVED_DIR)
        self.assertEqual(seen['resolver'], [])
        self.assertEqual(seen['lookup'][0], 'failed')
        self.assertLess(seen['lookup'][1], 0.5)

    def test_the_host_resolver_cannot_be_reached_from_inside(self):
        directory = self.resolver()
        seen, _ = self.probe(directory)
        self.assertEqual(seen['resolver'], [])
        self.assertEqual(os.listdir(directory), ['io.systemd.Resolve'])  # hidden in the namespace only

    def test_driver_runs_as_us_and_can_change_nothing_about_the_namespace(self):
        seen, pid = self.probe(self.resolver())
        self.assertEqual((seen['uid'], seen['gid']), (os.getuid(), os.getgid()))
        self.assertEqual(seen['pid'], pid)  # the recorded pid is the driver itself: stop() signals exactly that
        self.assertEqual(seen['caps'], [0, 0, 0, 0])
        self.assertEqual(seen['bring_up_lo'], 'EPERM')

    def test_no_mac_unless_the_resolver_was_hidden(self):
        # A hardware id is what makes the driver try the licence server, so it must not get one here.
        seen, _ = self.probe(self.root / 'resolver-that-cannot-be-covered')
        self.assertEqual(seen['mac'], '00:00:00:00:00:00')
        self.assertFalse(seen['ipv4'])
        self.assertEqual(seen['interfaces'], ['lo'])
        self.assertEqual(seen['caps'], [0, 0, 0, 0])


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
