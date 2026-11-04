"""The Luma driver in tracking.py. A fake xr-luma-driver stands in for the real one; the network namespace is real."""
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from unittest.mock import patch

from tools.native_spike import tracking


def fake_driver(path, body):
    path.write_text(f'#!{sys.executable}\n' + textwrap.dedent(body))
    path.chmod(0o700)


CALIBRATES = '''\
    import signal, sys, time
    signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))
    print('luma: starting', file=sys.stderr, flush=True)
    time.sleep(0.2)
    print('luma: calibrated', file=sys.stderr, flush=True)
    while True: time.sleep(0.1)
    '''
NEVER_CALIBRATES = '''\
    import signal, sys, time
    signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))
    print('luma: calibrating', file=sys.stderr, flush=True)
    while True: time.sleep(0.1)
    '''
EXITS = '''\
    import sys
    print('luma: no glasses', file=sys.stderr, flush=True)
    sys.exit(3)
    '''
RECONNECTS = '''\
    import signal, sys, time
    signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))
    def say(line): print(line, file=sys.stderr, flush=True)
    say('luma: calibrated restored=0 at=1.000')
    time.sleep(0.4)
    say('luma: disconnected reason=gone at=2.000')
    time.sleep(0.8)
    say('luma: calibrated restored=1 at=3.000')
    while True: time.sleep(0.1)
    '''
CRASHES_ONCE = '''\
    import os, signal, sys, time
    signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))
    print('luma: calibrated restored=0', file=sys.stderr, flush=True)
    time.sleep(0.2)
    mark = {mark!r}
    if not os.path.exists(mark):
        open(mark, 'w').close()
        os.kill(os.getpid(), signal.SIGKILL)
    while True: time.sleep(0.1)
    '''
ALWAYS_CRASHES = '''\
    import os, signal, sys, time
    print('luma: calibrated restored=0', file=sys.stderr, flush=True)
    time.sleep(0.1)
    os.kill(os.getpid(), signal.SIGKILL)
    '''
XRDRIVER = '''\
    import signal, sys, time
    signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))
    open({log!r}, 'a').write('2026 Device calibration complete\\n')
    while True: time.sleep(0.1)
    '''


class LumaLifecycle(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.runtime = root / 'rt'; self.runtime.mkdir(mode=0o700)
        self.log = root / 'state/viture-workspace/luma-driver.log'
        self.xr_log = root / 'driver.log'; self.xr_log.write_text('')
        self.config = root / 'config.ini'; self.config.write_text('disabled=false\noutput_mode=external_only\n')
        self.luma = root / 'xr-luma-driver'
        self.xrdriver = root / 'xrDriver'
        self.flags = root / 'luma-driver-flags'
        fake_driver(self.luma, CALIBRATES)
        fake_driver(self.xrdriver, XRDRIVER.format(log=str(self.xr_log)))
        for p in (patch.object(tracking, 'LUMA_LOG', self.log), patch.object(tracking, 'DRIVER', str(self.xrdriver)),
                  patch.object(tracking, 'DRIVER_LOG', self.xr_log), patch.object(tracking, 'DRIVER_CONFIG', self.config),
                  patch.object(tracking, 'LUMA_FLAGS', self.flags), patch.object(tracking, '_other_drivers', return_value=[]),
                  patch.object(tracking, '_check_usb_access'), patch.object(tracking, 'SYS_USB', root / 'fake-sysfs')):
            p.start(); self.addCleanup(p.stop)
        self.addCleanup(self.nothing_left_running, root)
        self.addCleanup(lambda: tracking.stop(self.runtime))

    def nothing_left_running(self, root):
        """A driver that outlives its test is a lifecycle bug: name the test, and leave nothing behind."""
        left = []
        for entry in Path('/proc').glob('[0-9]*'):
            try: argv = (entry / 'cmdline').read_bytes().split(b'\0')
            except OSError: continue
            if any(arg.startswith(str(root).encode()) for arg in argv): left.append(int(entry.name))
        for pid in left: os.kill(pid, signal.SIGKILL)
        self.assertEqual(left, [])

    def state(self): return json.loads(tracking._state(self.runtime).read_text())

    def pretend(self, **fields):
        """A state file naming a throwaway process: the cleanup's stop() signals whatever it names."""
        child = subprocess.Popen(['sleep', '30'])
        self.addCleanup(child.wait); self.addCleanup(child.kill)
        tick = None
        while tick is None: tick = tracking._start_tick(child.pid)
        tracking._state(self.runtime).write_text(json.dumps({'pid': child.pid, 'start_tick': tick, **fields}))

    def test_start_status_stop_network_blocked(self):
        status = tracking.start(self.runtime, timeout=10, luma=str(self.luma))
        self.assertTrue(status['running'] and status['calibrated'] and status['network_isolated'], status)
        self.assertEqual((status['driver'], status['binary']), ('luma', str(self.luma)))
        self.assertEqual(tracking._interfaces(status['pid']), ['lo'])
        self.assertEqual(self.state()['driver'], 'luma')
        self.assertTrue(tracking.driver_alive(self.runtime))  # the incident monitor's check
        self.assertIn('luma: calibrated', self.log.read_text())  # its stderr, kept
        self.assertEqual(stat.S_IMODE(self.log.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.log.parent.stat().st_mode), 0o700)
        self.assertTrue(tracking.stop(self.runtime)['stopped'])
        self.assertFalse(tracking.status(self.runtime)['running'])

    def test_it_runs_as_us_with_no_capabilities(self):
        status = tracking.start(self.runtime, timeout=10, luma=str(self.luma))
        fields = dict(line.split(':', 1) for line in Path(f"/proc/{status['pid']}/status").read_text().splitlines())
        self.assertEqual(int(fields['Uid'].split()[0]), os.getuid())
        self.assertEqual(int(fields['CapEff'], 16), 0)
        self.assertEqual(int(fields['CapPrm'], 16), 0)

    def test_needs_no_xrdriver_config(self):
        self.config.unlink()
        self.assertTrue(tracking.start(self.runtime, timeout=10, luma=str(self.luma))['calibrated'])

    def test_a_lost_connection_invalidates_calibration(self):
        self.log.parent.mkdir(parents=True)
        self.log.write_text('luma: calibrated\n')  # an earlier run's: before the offset, so not counted
        self.pretend(log_offset=self.log.stat().st_size, driver='luma', binary='x')
        self.assertFalse(tracking.status(self.runtime)['calibrated'])
        def add(line):
            with self.log.open('a') as log: log.write(line + '\n')
        add('luma: calibrated')
        self.assertTrue(tracking.status(self.runtime)['calibrated'])
        for lost in tracking.LUMA_LOST:
            add(lost)
            self.assertFalse(tracking.status(self.runtime)['calibrated'], lost)
            add('luma: calibrated')
            self.assertTrue(tracking.status(self.runtime)['calibrated'])

    def test_the_daemons_real_lines(self):
        # spike/driver/include/luma/daemon.hpp: a USB drop that keeps its calibration, then one that loses it.
        self.log.parent.mkdir(parents=True); self.log.write_text('')
        self.pretend(log_offset=0, driver='luma', binary='x')
        for line, ready in (('luma: connected at=1.000', False), ('luma: calibrating at=1.001', False),
                            ('luma: calibrated restored=0 at=2.500', True), ('luma: stats samples=60000 commands=1 at=62.0', True),
                            ('luma: stale ms=1000 at=70.0', True), ('luma: fresh gap_ms=1200 at=70.2', True),
                            ('luma: disconnected reason=gone at=80.0', False), ('luma: reconnected gap_ms=900 calibration=kept at=80.9', False),
                            ('luma: calibrated restored=1 at=80.9', True),
                            ('luma: disconnected reason=silent at=90.0', False), ('luma: reconnected gap_ms=900 calibration=lost at=90.9', False),
                            ('luma: calibrating at=90.9', False), ('luma: calibrated restored=0 at=92.0', True)):
            with self.log.open('a') as log: log.write(line + '\n')
            self.assertEqual(tracking.status(self.runtime)['calibrated'], ready, line)

    def test_each_driver_is_read_from_its_own_log(self):
        self.xr_log.write_text('Device calibration complete\n')
        self.log.parent.mkdir(parents=True); self.log.write_text('')
        self.pretend(log_offset=0, driver='luma', binary='x')
        self.assertFalse(tracking.status(self.runtime)['calibrated'])  # XRLinuxDriver's words mean nothing here

    def test_a_big_log_is_moved_aside_at_start(self):
        self.log.parent.mkdir(parents=True)
        self.log.write_bytes(b'x' * (tracking.LUMA_LOG_MAX + 1))
        tracking.start(self.runtime, timeout=10, luma=str(self.luma))
        self.assertEqual(self.log.with_suffix('.log.1').stat().st_size, tracking.LUMA_LOG_MAX + 1)
        self.assertLess(self.log.stat().st_size, 1000)

    def test_ensure_ready_reuses_the_same_install(self):
        first = tracking.ensure_ready(self.runtime, timeout=10, luma=str(self.luma))
        self.assertEqual(tracking.ensure_ready(self.runtime, timeout=10, luma=str(self.luma))['pid'], first['pid'])

    def test_ensure_ready_restarts_for_a_newer_install(self):
        first = tracking.ensure_ready(self.runtime, timeout=10, luma=str(self.luma))
        newer = self.luma.with_name('newer'); newer.mkdir(); newer = newer / 'xr-luma-driver'
        fake_driver(newer, CALIBRATES)
        second = tracking.ensure_ready(self.runtime, timeout=10, luma=str(newer))
        self.assertNotEqual(second['pid'], first['pid'])
        self.assertEqual(second['binary'], str(newer))

    def test_ensure_ready_swaps_our_driver_for_the_one_asked_for(self):
        luma = tracking.ensure_ready(self.runtime, timeout=10, luma=str(self.luma))
        xr = tracking.ensure_ready(self.runtime, timeout=10)
        self.assertEqual((xr['driver'], xr['binary']), ('xrdriver', tracking.DRIVER))
        self.assertFalse(Path(f"/proc/{luma['pid']}/stat").exists() and tracking._start_tick(luma['pid']))
        back = tracking.ensure_ready(self.runtime, timeout=10, luma=str(self.luma))
        self.assertEqual(back['driver'], 'luma')

    def wait_for(self, check, seconds=5.0):
        deadline = time.monotonic() + seconds
        while not check() and time.monotonic() < deadline: time.sleep(0.02)
        self.assertTrue(check())

    def test_a_reconnecting_driver_is_left_alone(self):
        # Cody's bar: a reconnect keeps calibration and adds no USB traffic; a restart would lose both.
        fake_driver(self.luma, RECONNECTS)
        first = tracking.start(self.runtime, timeout=10, luma=str(self.luma))
        self.wait_for(lambda: 'luma: disconnected' in self.log.read_text())
        self.assertFalse(tracking.status(self.runtime)['calibrated'])
        with patch.object(tracking, 'start') as start:
            ready = tracking.ensure_ready(self.runtime, timeout=10, luma=str(self.luma))
        start.assert_not_called()
        self.assertEqual(ready['pid'], first['pid'])
        self.assertTrue(ready['calibrated'])

    def test_a_driver_that_does_not_come_back_is_replaced(self):
        fake_driver(self.luma, NEVER_CALIBRATES)
        self.pretend(log_offset=0, driver='luma', binary=str(self.luma))
        old = self.state()['pid']
        fake_driver(self.luma, CALIBRATES)
        with patch.object(tracking, 'LUMA_SETTLE', 0.2), patch.object(tracking, '_network_isolated', return_value=True):
            ready = tracking.ensure_ready(self.runtime, timeout=10, luma=str(self.luma))
        self.assertNotEqual(ready['pid'], old)

    def test_a_crash_reads_as_gone_though_the_zombie_remains(self):
        # The runner is the driver's parent and does not reap it mid-session.
        status = tracking.start(self.runtime, timeout=10, luma=str(self.luma))
        self.assertEqual(tracking.active_driver(self.runtime), 'luma')
        os.kill(status['pid'], signal.SIGKILL)
        self.wait_for(lambda: tracking._zombie(status['pid']))
        self.assertEqual(tracking._start_tick(status['pid']), self.state()['start_tick'])  # the trap: the tick survives
        self.assertFalse(tracking.driver_alive(self.runtime))
        self.assertIsNone(tracking.active_driver(self.runtime))
        self.assertFalse(tracking.status(self.runtime)['running'])

    def test_log_path_follows_the_driver(self):
        self.assertEqual(tracking.log_path(self.runtime), self.xr_log)  # nothing started yet
        tracking.start(self.runtime, timeout=10, luma=str(self.luma))
        self.assertEqual(tracking.log_path(self.runtime), self.log)
        tracking.stop(self.runtime)
        tracking.start(self.runtime, timeout=10)
        self.assertEqual(tracking.log_path(self.runtime), self.xr_log)

    def watch(self, **changes):
        notes = []
        watch = tracking.LumaWatch(self.runtime, str(self.luma), notes.append, poll=0.05)
        watch.start(); self.addCleanup(watch.finish)
        return watch, notes

    def test_the_watch_brings_a_crashed_driver_back(self):
        fake_driver(self.luma, CRASHES_ONCE.format(mark=str(self.luma) + '.crashed'))
        first = tracking.start(self.runtime, timeout=10, luma=str(self.luma))
        watch, notes = self.watch()
        self.wait_for(lambda: tracking.driver_alive(self.runtime) and self.state()['pid'] != first['pid']
                      and tracking.status(self.runtime)['calibrated'], 10)
        self.assertTrue(tracking.status(self.runtime)['calibrated'])
        self.assertEqual(len(watch.restarts), 1)
        self.assertEqual(notes, [])

    def test_the_watch_gives_up_and_says_so(self):
        fake_driver(self.luma, ALWAYS_CRASHES)
        with patch.object(tracking, 'start', wraps=tracking.start) as start:
            self.pretend(log_offset=0, driver='luma', binary=str(self.luma))
            os.kill(self.state()['pid'], signal.SIGKILL)
            watch, notes = self.watch()
            self.wait_for(lambda: notes, 20)
            watch.join(2)
        self.assertFalse(watch.is_alive())
        self.assertLessEqual(start.call_count, tracking.RESTARTS)  # never more than the budget
        self.assertIn('keeps crashing', notes[0])

    def test_a_stop_is_not_a_crash(self):
        tracking.start(self.runtime, timeout=10, luma=str(self.luma))
        watch, notes = self.watch()
        tracking.stop(self.runtime)
        watch.join(3)
        self.assertFalse(watch.is_alive())
        self.assertFalse(tracking.status(self.runtime)['running'])
        self.assertEqual((watch.restarts, notes), ([], []))

    def test_a_swap_ends_the_watch(self):
        tracking.start(self.runtime, timeout=10, luma=str(self.luma))
        watch, notes = self.watch()
        tracking.ensure_ready(self.runtime, timeout=10)
        watch.join(3)
        self.assertFalse(watch.is_alive())
        self.assertEqual(tracking.active_driver(self.runtime), 'xrdriver')

    def test_a_driver_that_exits_stops_the_start(self):
        fake_driver(self.luma, EXITS)
        with self.assertRaisesRegex(ValueError, 'exited|did not calibrate'):
            tracking.start(self.runtime, timeout=5, luma=str(self.luma))
        self.assertIn('luma: no glasses', self.log.read_text())

    def test_a_missing_binary_stops_the_start(self):
        with self.assertRaises(ValueError): tracking.start(self.runtime, timeout=5, luma=str(self.luma) + '-gone')

    def test_a_driver_that_never_calibrates_is_stopped(self):
        fake_driver(self.luma, NEVER_CALIBRATES)
        with self.assertRaisesRegex(ValueError, 'did not calibrate'):
            tracking.start(self.runtime, timeout=0.6, luma=str(self.luma))
        self.assertFalse(tracking.status(self.runtime)['running'])

    def test_fallback_hands_over_to_xrlinuxdriver(self):
        fake_driver(self.luma, NEVER_CALIBRATES)
        with patch.object(tracking, 'LUMA_TIMEOUT', 0.6), patch.object(tracking, 'FALLBACK_TIMEOUT', 10):
            ready = tracking.ensure_ready_luma(self.runtime, str(self.luma))
        self.assertEqual(ready['driver'], 'xrdriver')
        self.assertIn('did not calibrate', ready['fallback'])
        self.assertTrue(ready['calibrated'] and ready['network_isolated'])
        self.assertEqual(self.state()['driver'], 'xrdriver')

    def test_saved_flags_reach_the_driver(self):
        fake_driver(self.luma, """\
            import signal, sys, time
            signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))
            print('luma: args', *sys.argv[1:], file=sys.stderr, flush=True)
            print('luma: calibrated', file=sys.stderr, flush=True)
            while True: time.sleep(0.1)
            """)
        self.flags.write_text('--start prepared\n--read-events\n')
        tracking.start(self.runtime, timeout=10, luma=str(self.luma))
        self.assertIn('luma: args --start prepared --read-events', self.log.read_text())

    def test_no_flags_file_runs_the_default(self):
        self.assertEqual(tracking._luma_argv('/x')[-1], '/x')

    def test_a_bad_flags_file_falls_back_and_says_why(self):
        self.flags.write_text('--replay /tmp/x\n')
        ready = tracking.ensure_ready_luma(self.runtime, str(self.luma))
        self.assertEqual(ready['driver'], 'xrdriver')
        self.assertIn('unknown flag: --replay', ready['fallback'])

    def test_a_new_driver_that_never_calibrates_hands_over_to_the_standard_binary(self):
        fake_driver(self.luma, NEVER_CALIBRATES)
        with patch.object(tracking, 'LUMA_TIMEOUT', 0.5):
            ready = tracking.ensure_ready_luma(self.runtime, str(self.luma))
        self.assertEqual((ready['driver'], ready['binary']), ('xrdriver', str(self.xrdriver)))
        self.assertIn('did not calibrate', ready['fallback'])

    def test_the_3dof_build_runs_as_asked_and_is_replaced_when_not_wanted(self):
        build = self.tmp_build()
        first = tracking.ensure_ready(self.runtime, timeout=10, binary=build)
        self.assertEqual((first['driver'], first['binary']), ('xrdriver', build))
        self.assertEqual(tracking.ensure_ready(self.runtime, timeout=10, binary=build)['pid'], first['pid'])  # the same one: reused
        standard = tracking.ensure_ready(self.runtime, timeout=10)
        self.assertEqual(standard['binary'], str(self.xrdriver))
        self.assertNotEqual(standard['pid'], first['pid'])  # a test arm must not run on the other's driver
        back = tracking.ensure_ready(self.runtime, timeout=10, binary=build)
        self.assertEqual(back['binary'], build)

    def tmp_build(self):
        build = Path(self.tmp.name) / 'build3dof' / 'xrDriver'
        build.parent.mkdir()
        fake_driver(build, XRDRIVER.format(log=str(self.xr_log)))
        return str(build)

    def test_a_failed_3dof_start_is_never_handed_to_the_standard_driver(self):
        build = Path(self.tmp.name) / 'build3dof' / 'xrDriver'
        build.parent.mkdir()
        fake_driver(build, EXITS)
        with self.assertRaises(ValueError):
            tracking.ensure_ready(self.runtime, timeout=10, binary=str(build))
        self.assertFalse(tracking.status(self.runtime)['running'])

    def test_no_usb_permission_refuses_both_and_says_what_to_do(self):
        tracking._check_usb_access.side_effect = ValueError('no permission to open the glasses: unplug them')
        with self.assertRaisesRegex(ValueError, 'XR Luma driver failed .*no permission.* so did XRLinuxDriver'):
            tracking.ensure_ready_luma(self.runtime, str(self.luma))
        self.assertFalse(self.log.exists() and 'calibrated' in self.log.read_text())

    def test_no_fallback_when_luma_calibrates(self):
        ready = tracking.ensure_ready_luma(self.runtime, str(self.luma))
        self.assertEqual(ready['driver'], 'luma')
        self.assertNotIn('fallback', ready)

    def test_when_both_fail_the_error_names_both(self):
        fake_driver(self.luma, EXITS)
        self.config.write_text('disabled=false\noutput_mode=mouse\n')
        with self.assertRaisesRegex(ValueError, r'XR Luma driver failed \(.*\) and so did XRLinuxDriver: .*external_only'):
            tracking.ensure_ready_luma(self.runtime, str(self.luma))

    def test_a_cancel_is_not_a_failure(self):
        # The panel's Stop during calibration: nothing may start after it.
        with patch.object(tracking, 'ensure_ready', side_effect=KeyboardInterrupt) as ready, self.assertRaises(KeyboardInterrupt):
            tracking.ensure_ready_luma(self.runtime, str(self.luma))
        ready.assert_called_once()

    def test_state_from_before_luma_reads_as_xrlinuxdriver(self):
        self.pretend(log_offset=0)
        self.assertEqual(tracking.status(self.runtime)['driver'], 'xrdriver')
        self.assertEqual(tracking.status(self.runtime)['binary'], tracking.DRIVER)


class LumaFlags(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'flags'
        patcher = patch.object(tracking, 'LUMA_FLAGS', self.path); patcher.start(); self.addCleanup(patcher.stop)

    def test_none(self):
        self.assertEqual(tracking._luma_flags(), [])

    def test_every_allowed_form(self):
        self.path.write_text('--start full --read-events\n  --drain-camera\n')
        self.assertEqual(tracking._luma_flags(), ['--start', 'full', '--read-events', '--drain-camera'])

    def test_empty_file(self):
        self.path.write_text('\n')
        self.assertEqual(tracking._luma_flags(), [])

    def test_anything_else_is_refused(self):
        for text in ('--start', '--start guess', '--start listen', '--replay x', '--ipc-prefix /tmp/x', '--state-dir /x', 'x'):
            with self.subTest(text):
                self.path.write_text(text)
                with self.assertRaises(ValueError): tracking._luma_flags()


class OtherDrivers(unittest.TestCase):
    """A real process with each name in argv[0], as /proc shows it."""
    def named(self, argv0):
        process = subprocess.Popen(['bash', '-c', f'exec -a {argv0} sleep 30'])
        self.addCleanup(process.wait); self.addCleanup(process.kill)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not Path(f'/proc/{process.pid}/cmdline').read_bytes().startswith(argv0.encode()):
            time.sleep(0.01)
        return process.pid

    def test_finds_both_drivers_by_any_path(self):
        for name in ('/usr/bin/xrDriver', 'xrDriver', '/home/x/xrdriver-3dof/v/xrDriver', '/home/x/bin/abc/xr-luma-driver', 'xr-luma-driver'):
            with self.subTest(name): self.assertIn(self.named(name), tracking._other_drivers())

    def test_ignores_a_name_that_only_looks_alike(self):
        self.assertNotIn(self.named('/usr/bin/xr-luma-driver-test'), tracking._other_drivers())

    def test_start_refuses_to_compete(self):
        self.named('/opt/xr-luma-driver')
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp) / 'rt'; runtime.mkdir(mode=0o700)
            with patch.object(tracking, 'SYS_USB', Path(tmp) / 'fake-sysfs'), patch.object(tracking, 'LUMA_LOG', Path(tmp) / 'l.log'), patch.object(tracking.subprocess, 'Popen') as popen:
                with self.assertRaisesRegex(ValueError, 'another head tracking driver'):
                    tracking.start(runtime, luma='/x/xr-luma-driver')
                popen.assert_not_called()


class TrackingCli(unittest.TestCase):
    def test_luma_start_needs_an_install(self):
        from tools.native_spike.__main__ import _pinned_luma
        with tempfile.TemporaryDirectory() as data, patch.dict(os.environ, {'XDG_DATA_HOME': data}):
            root = Path(data) / 'viture-workspace'; root.mkdir()
            entry = {'path': str(root / 'bin/x/f'), 'sha256': 'a' * 64}
            (root / 'installed.json').write_text(json.dumps({'plugin': entry, 'renderer': entry}))
            with self.assertRaisesRegex(ValueError, 'not installed'): _pinned_luma()
            (root / 'installed.json').write_text(json.dumps({'plugin': entry, 'renderer': entry, 'luma_driver': entry}))
            with self.assertRaisesRegex(ValueError, 'pinned binary changed'): _pinned_luma()


if __name__ == '__main__': unittest.main()


class RestartFailureNotice(unittest.TestCase):
    def test_failed_start_cleanup_is_not_mistaken_for_an_intentional_stop(self):
        notes = []
        with patch.object(tracking, '_read_state', side_effect=[{'driver':'luma'}, None]), \
                patch.object(tracking, '_ours', return_value=False), \
                patch.object(tracking, 'ensure_ready', side_effect=ValueError('startup crash')):
            watch = tracking.LumaWatch(Path('/unused-test-runtime'), '/fake-driver', notes.append, poll=.001)
            watch.start(); watch.join(1)
        self.assertFalse(watch.is_alive())
        self.assertEqual(len(notes), 1)
        self.assertIn('head tracking has stopped', notes[0])
