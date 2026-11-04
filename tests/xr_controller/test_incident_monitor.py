"""The incident monitor end to end, offline: whole sessions as the controller reports them, against fake logs, DRM and USB.

Each scenario replays what the run, kernel and driver did; the 2026-11-03 ones are the real sequences. HostSources has its own
tests at the end, against a fake sysfs, /proc and command runner. Nothing here reads the host or runs journalctl.
"""
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import time
import unittest
from unittest import mock

from tools.xr_controller.incident_monitor import CORE_WAIT_S, HostSources, IncidentMonitor, relevant_kernel, stack_frames
from tools.xr_controller.incident_report import Reports, Scrubber

RUN = '2298c7a18df736b9b6c4bde5b5877031'
OTHER = '549bcebd15b354c2c168fabd58cc1e0c'
PIN = '/home/testuser/.local/share/viture-workspace/bin/f0de154899b9/'
CONSENT = dict(run_id=RUN, instance='sig', output='DP-2', recovery_output='DP-1', tracking='xrlinuxdriver', model='luma-ultra',
               renderer=dict(path=PIN + 'xr-native-spike', sha256='a' * 64), plugin=dict(path=PIN + 'libxr-ownership.so', sha256='b' * 64))
DP2 = dict(name='DP-2', width=1920, height=1200, refresh=120.0, dpms=True)
DP1 = dict(name='DP-1', width=5120, height=1440, refresh=240.0, dpms=True)
GOOD = dict(presented_hz=120.0, missed=0, capture_path='dmabuf', tracking=True, capture_failure_run=0)
CLEAN = dict(run_id=RUN, phase='done', complete=True, renderer_returncode=0, remaining=[], error=None)
T0 = 1793750000.0


def stamp(at):
    return time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(at)) + time.strftime('%z', time.localtime(at))[:3] + ':00'


class Sources:
    """The host, as a script: a test changes these fields as the story goes on."""
    def __init__(self):
        self.boot, self.consents, self.results = 'boot-1', {RUN: CONSENT, OTHER: dict(CONSENT, run_id=OTHER)}, {}
        self.recoveries = {}
        self.kernel, self.driver = [], ''
        self.reads = 0
        self.drm_state, self.ports, self.hypr_pid, self.hypr_up, self.driver_up = 'connected', {'5-2.1', '5-2.3'}, 1690, True, True
        self.cores, self.journals = [], {}
        self.broken = set()

    def _maybe(self, name):
        if name in self.broken: raise OSError(f'{name} broke')

    def boot_id(self): return self.boot
    def consent(self, run_id): self._maybe('consent'); return self.consents.get(run_id)
    def result(self, run_id): self._maybe('result'); return self.results.get(run_id)
    def recovery(self, run_id): self._maybe('recovery'); return self.recoveries.get(run_id)
    def kernel_start(self): self._maybe('kernel'); return len(self.kernel)

    def kernel_read(self, cursor):
        self._maybe('kernel')
        self.reads += 1
        return self.kernel[cursor:], len(self.kernel)

    def kernel_around(self, first, last): self._maybe('kernel'); return list(self.kernel)
    def journal(self, unit, lines): self._maybe('journal'); return self.journals.get(unit, [])[-lines:]
    def driver_start(self): self._maybe('driver'); return len(self.driver)
    def driver_read(self, offset): self._maybe('driver'); return self.driver[offset:], len(self.driver)
    def driver_tail(self, lines): self._maybe('driver'); return self.driver.splitlines()[-lines:]
    def drm(self, output): self._maybe('drm'); return self.drm_state
    def usb_ports(self): self._maybe('usb'); return set(self.ports)
    def hyprland_pid(self, instance): return self.hypr_pid
    def alive(self, pid): return self.hypr_up if pid == self.hypr_pid else None
    def driver_alive(self): return self.driver_up
    def source(self): return dict(sha='13c8d5e' + '0' * 33, dirty=False)

    def core_dumps(self, since, exes, pids):
        self._maybe('core')
        return [c for c in self.cores if c['exe_path'] in exes or c['pid'] in pids]

    def drop(self, at, n=42):
        self.kernel += [f'{stamp(at)} h kernel: usb 5-2: USB disconnect, device number {n}', f'{stamp(at)} h kernel: usb 5-2.1: USB disconnect, device number {n + 1}']


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        os.chmod(self.root, 0o700)
        self.dir = self.root / 'incidents'
        self.host = Sources()
        self.toasts, self.handed = [], []
        self.now = T0
        quiet = mock.patch('tools.xr_controller.incident_monitor.log')  # its journal lines; the assertions read the reports
        self.said = quiet.start()
        self.addCleanup(quiet.stop)
        self.fresh()

    def fresh(self):
        """A controller (re)start: the same disk, nothing in memory."""
        self.monitor = IncidentMonitor(self.host, Reports(self.dir), notify=self.toasts.append, hand_on=self.handed.append,
                                       scrub=Scrubber(home='/home/testuser', user='testuser', host='testhost'), clock=lambda: self.now)

    def tick(self, state='running', run_id=RUN, seconds=1, status=GOOD, **over):
        """The controller's tick, once a second."""
        for _ in range(seconds):
            o = dict(dict(state=state, run_id=run_id, stop_requested=None, monitors=[DP2, DP1], plugins=1, status=status, instance='sig'), **over)
            self.monitor.observe(o)
            self.monitor.step(self.now)
            self.now += 1

    def idle(self, seconds=1, **over): self.tick('idle', None, seconds, status=None, **over)

    def session(self, seconds=20, run_id=RUN):
        self.tick('starting', run_id, 3, status=None)
        self.tick('running', run_id, seconds)

    def reports(self): return sorted(self.dir.glob('*.json')) if self.dir.exists() else []

    def only(self):
        reports = self.reports()
        self.assertEqual(len(reports), 1, [p.name for p in reports])
        return json.loads(reports[0].read_text())

    def assertNoIncident(self):
        self.assertEqual(self.reports(), [])
        self.assertEqual(self.toasts, [])
        self.assertEqual(self.handed, [])
        self.assertIsNone(self.monitor.latest)


class NotAnIncidentTests(Case):
    def test_a_session_from_start_to_stop(self):
        self.session()
        self.tick('stopping', stop_requested=RUN, seconds=2, status=None)
        self.host.results[RUN] = CLEAN
        self.idle(60)
        self.assertNoIncident()

    def test_a_stop_faster_than_a_tick(self):
        # The controller may not show 'stopping' for a whole tick; its stop flag still says so.
        self.session()
        self.tick(stop_requested=RUN)
        self.host.results[RUN] = dict(CLEAN, renderer_returncode=-15)
        self.idle(60)
        self.assertNoIncident()

    def test_stop_then_unplugging_the_glasses(self):
        # 2026-11-03 20:27:18 Stop, 20:27:25 the hub gone: a person putting the glasses away.
        self.session()
        self.tick('stopping', stop_requested=RUN, seconds=2, status=None)
        self.host.results[RUN] = CLEAN
        self.idle(5)
        self.host.drop(self.now)
        self.host.ports = set()
        self.host.drm_state = 'disconnected'
        self.idle(60)
        self.assertNoIncident()

    def test_a_key_stop_then_an_unplug_before_the_next_tick(self):
        self.session()
        self.host.results[RUN] = CLEAN
        self.host.drop(self.now)
        self.idle(60)
        self.assertNoIncident()

    def test_stop_during_a_start(self):
        # The real run 0df845d2, stopped while tracking calibrated.
        self.tick('starting', seconds=3, status=None)
        self.tick('stopping', stop_requested=RUN, seconds=2, status=None)
        self.host.results[RUN] = dict(complete=False, error='KeyboardInterrupt', phase='tracking', remaining=None, renderer_returncode=None, run_id=RUN)
        self.idle(60)
        self.assertNoIncident()

    def test_a_panel_restart_in_a_live_session(self):
        self.session()
        self.fresh()
        self.tick(seconds=20)
        self.assertNoIncident()

    def test_a_controller_restart_finding_a_clean_end(self):
        self.session()
        self.fresh()
        self.host.results[RUN] = CLEAN
        self.idle(60)
        self.assertNoIncident()

    def test_a_reboot_is_not_an_unrecovered_run(self):
        self.session()
        self.fresh()
        self.host.boot = 'boot-2'
        self.idle(60)
        self.assertNoIncident()

    def test_glasses_unplugged_with_no_session(self):
        self.idle(5)
        self.host.drop(self.now)
        self.host.ports = set()
        self.idle(60)
        self.assertNoIncident()

    def test_usb_trouble_during_a_start_is_not_read_until_the_session_runs(self):
        self.tick('starting', seconds=3, status=None)
        self.host.drop(self.now)
        self.tick('starting', seconds=3, status=None)
        self.tick(seconds=60)
        self.assertNoIncident()


class UsbDropTests(Case):
    def test_the_19_18_drop_is_one_report_of_a_session_that_ended(self):
        # 19:18:33 the hub dropped; 19:18:40 again; rc 1, status 2, no core dump.
        self.session()
        self.host.driver += time.strftime('%Y-%m-%d %H:%M:%S.100 Retrying driver connection in 1 second\n', time.localtime(self.now))
        self.host.drop(self.now)
        self.tick(seconds=7)
        self.host.drop(self.now, 46)
        self.host.drm_state = 'disconnected'
        self.tick(seconds=1, status=None)
        self.host.results[RUN] = dict(CLEAN, renderer_returncode=1)
        self.host.journals[f'viture-xr-{RUN}.service'] = ['2026-11-03T19:18:40-05:00 h python3[1]: dispatch: closed, removed or disconnected',
            "2026-11-03T19:18:41-05:00 h systemd[1]: x.service: Main process exited, code=exited, status=2/INVALIDARGUMENT",
            "2026-11-03T19:18:41-05:00 h systemd[1]: x.service: Failed with result 'exit-code'."]
        self.idle(60)
        report = self.only()
        self.assertEqual(report['kind'], 'usb-drop')
        self.assertEqual(report['session'], 'ended')
        self.assertEqual(report['summary'], "the glasses' USB connection dropped and the session ended")
        self.assertEqual(report['renderer']['returncode'], 1)
        self.assertEqual(report['unit'], dict(code='exited', status='2/INVALIDARGUMENT', result='exit-code'))
        self.assertEqual(report['cause']['confidence'], 'high')
        self.assertEqual(report['run_id'], RUN)
        kinds = [t['kind'] for t in report['timeline']]
        self.assertEqual(kinds.count('usb-drop'), 3)  # kernel twice, driver once
        self.assertIn('video-lost', kinds)
        self.assertIn('renderer-exit', kinds)
        self.assertEqual(self.toasts, ["XR Workspace hit a problem: the glasses' USB connection dropped and the session ended. A report was saved."])
        self.assertEqual(self.handed, self.reports())

    def test_a_drop_the_session_survives(self):
        self.session()
        self.host.drop(self.now)
        self.tick(seconds=40)
        report = self.only()
        self.assertEqual(report['session'], 'kept-running')
        self.assertIn('kept running', report['summary'])

    def test_a_drop_then_stop_says_it_was_stopped(self):
        # 2026-11-03 20:06:12 the hub, 20:06:23 Stop.
        self.session()
        self.host.drop(self.now)
        self.tick(seconds=11)
        self.tick('stopping', stop_requested=RUN, seconds=1, status=None)
        self.host.results[RUN] = CLEAN
        self.idle(30)
        self.assertEqual(self.only()['session'], 'stopped')

    def test_a_drop_right_before_the_end_is_read_in_time(self):
        # The last two seconds of logs have not been polled when the run ends.
        self.session()
        self.host.drop(self.now)
        self.host.results[RUN] = dict(CLEAN, renderer_returncode=1)
        self.idle(30)
        self.assertEqual(self.only()['kind'], 'usb-drop')

    def test_a_repeat_is_counted_and_said_in_the_next_report(self):
        self.session()
        self.host.drop(self.now)
        self.tick(seconds=30)
        self.host.drop(self.now, 50)
        self.tick(seconds=30)
        self.assertEqual(len(self.reports()), 1)
        self.assertEqual(len(self.toasts), 1)
        self.now += 700
        self.host.drop(self.now, 60)
        self.tick(seconds=30)
        reports = self.reports()
        self.assertEqual(len(reports), 2)
        self.assertEqual(json.loads(reports[-1].read_text())['suppressed_since_last'], {'usb-drop': 1})

    def test_the_session_ending_right_after_a_reported_drop_is_still_reported(self):
        self.session()
        self.host.drop(self.now)
        self.tick(seconds=30)
        self.host.results[RUN] = dict(CLEAN, renderer_returncode=1)
        self.host.drop(self.now, 50)
        self.tick(seconds=3)
        self.idle(30)
        self.assertEqual(len(self.reports()), 2)

    def test_the_port_is_found_at_run_time(self):
        self.host.ports = {'1-4'}
        self.session()
        self.host.kernel += [f'{stamp(self.now)} h kernel: usb 1-4: USB disconnect, device number 3']
        self.tick(seconds=40)
        self.assertEqual(self.only()['kind'], 'usb-drop')


class OtherDetectorTests(Case):
    def report_after(self, change, seconds=45, **tick):
        self.session()
        change()
        self.tick(seconds=seconds, **tick)
        return self.only()

    def test_lost_video(self):
        report = self.report_after(lambda: setattr(self.host, 'drm_state', 'disconnected'))
        self.assertEqual(report['kind'], 'video-lost')
        self.assertEqual(report['display']['drm'], 'disconnected')
        self.assertEqual(report['display']['mode'], '1920x1200@120')

    def test_a_mode_change(self):
        report = self.report_after(lambda: None, monitors=[dict(DP2, height=1080, refresh=60.0), DP1])
        self.assertEqual(report['kind'], 'video-lost')
        self.assertEqual(report['display'], dict(mode='1920x1080@60', expected_mode='1920x1200@120', drm='connected'))

    def test_a_stall(self):
        report = self.report_after(lambda: None, status=dict(GOOD, presented_hz=None))
        self.assertEqual(report['kind'], 'present-stall')
        self.assertEqual(report['metrics']['last']['missed'], 0)

    def test_capture_falling_back(self):
        self.assertEqual(self.report_after(lambda: None, status=dict(GOOD, capture_path='shm'))['kind'], 'capture-failed')

    def test_tracking_lost(self):
        self.assertEqual(self.report_after(lambda: None, status=dict(GOOD, tracking=False))['kind'], 'tracking-lost')

    def test_the_tracking_driver_gone(self):
        self.assertEqual(self.report_after(lambda: setattr(self.host, 'driver_up', False))['kind'], 'tracking-lost')

    def test_the_tracking_driver_recalibrating(self):
        self.assertEqual(self.report_after(lambda: setattr(self.host, 'driver', 'x\n2026-11-03 20:06:16.929 Waiting on device calibration\n'))['kind'], 'tracking-lost')

    def test_the_plugin_gone(self):
        self.assertEqual(self.report_after(lambda: None, plugins=0)['kind'], 'plugin-failed')

    def test_a_run_gone_silent(self):
        self.assertEqual(self.report_after(lambda: None, state='failed', status=None)['kind'], 'watchdog')

    def test_the_glasses_gone_from_usb_with_no_kernel_log(self):
        self.host.broken.add('kernel')
        self.assertEqual(self.report_after(lambda: setattr(self.host, 'ports', set()))['kind'], 'usb-drop')


class RunEndTests(Case):
    def core(self, pid=4242, exe=PIN + 'xr-native-spike'):
        return dict(exe_path=exe, exe=Path(exe).name, pid=pid, signal='SIGSEGV', frames=['#0  0x00007fa8 _ZN2xr12Presentation4drawEv (xr-native-spike + 0x10)'])

    def end(self, **result):
        self.session()
        self.host.results[RUN] = dict(CLEAN, **result)

    def test_a_crash_waits_for_its_core_dump(self):
        self.end(renderer_returncode=-11)
        self.idle(10)
        self.assertEqual(self.reports(), [])  # systemd-coredump is still writing it
        self.host.cores = [self.core()]
        self.idle(8)
        report = self.only()
        self.assertEqual(report['kind'], 'renderer-crash')
        self.assertEqual(report['renderer']['signal'], 'SIGSEGV')
        self.assertEqual(report['core_dumps'][0]['signal'], 'SIGSEGV')
        self.assertIn('_ZN2xr12Presentation4drawEv', report['cause']['text'])

    def test_waiting_for_a_core_dump_does_not_hammer_coredumpctl(self):
        self.end(renderer_returncode=-11)
        calls = []
        real = self.host.core_dumps
        self.host.core_dumps = lambda *a: calls.append(a) or real(*a)
        self.monitor.observe(dict(state='idle', run_id=None))
        for i in range(40): self.monitor.step(self.now + i * .25)  # 4 passes a second, for 10 seconds
        self.assertLessEqual(len(calls), 6)

    def test_a_crash_with_no_core_dump_is_not_held_for_ever(self):
        self.end(renderer_returncode=-11)
        self.idle(int(CORE_WAIT_S) + 8)
        self.assertEqual(self.only()['core_dumps'], [])

    def test_an_abnormal_exit(self):
        self.end(renderer_returncode=3)
        self.idle(30)
        report = self.only()
        self.assertEqual(report['kind'], 'renderer-exit')
        self.assertEqual(report['summary'], 'the XR renderer stopped unexpectedly and the session ended')

    def test_the_heartbeat_watchdog(self):
        self.end(renderer_returncode=-9, stop_reason='heartbeat-timeout')
        self.idle(30)
        report = self.only()
        self.assertEqual(report['kind'], 'watchdog')
        self.assertEqual(report['renderer']['stop_reason'], 'heartbeat-timeout')

    def test_a_killed_unit_leaves_one_report_through_recovery(self):
        self.session()
        self.tick('recovery-needed', status=None, seconds=30)
        self.assertEqual(self.only()['kind'], 'run-killed')
        self.tick('confirming', status=None, seconds=2)
        self.tick('starting', status=None, seconds=3)
        self.idle(30)
        self.assertEqual(len(self.reports()), 1)

    def test_a_recover_that_fails_is_its_own_report(self):
        # 2026-11-03: "recovery plugin pin mismatch", twice.
        self.session()
        self.tick('recovery-needed', status=None, seconds=30)
        self.tick('confirming', status=None, seconds=2)
        self.tick('starting', status=None, seconds=3)
        self.tick('recovery-needed', status=None, seconds=30)
        self.assertEqual([json.loads(p.read_text())['kind'] for p in self.reports()], ['run-killed', 'recovery-failed'])

    def test_a_recover_that_fails_says_why_in_its_report(self):
        # 20261104T022700Z: three Recovers exited 2, and the report could only say "No clear cause".
        why = 'XR-1: the ownership plugin refused to remove it (state blocked)'
        self.session()
        self.tick('recovery-needed', status=None, seconds=30)
        self.tick('confirming', status=None, seconds=2)
        self.tick('starting', status=None, seconds=3)
        self.host.recoveries[RUN] = dict(run_id=RUN, complete=False, reasons=[why])
        self.tick('recovery-needed', status=None, seconds=30)
        report = json.loads(self.reports()[-1].read_text())
        self.assertEqual(report['kind'], 'recovery-failed')
        self.assertEqual(report['recovery']['reasons'], [why])
        self.assertIn(why, report['cause']['text'])
        self.assertIn(why, report['timeline'][-1]['detail'])

    def test_a_failed_start(self):
        self.tick('starting', seconds=3, status=None)
        self.host.results[RUN] = dict(complete=False, error='tracking driver did not calibrate in time', phase='tracking', remaining=None, renderer_returncode=None, run_id=RUN)
        self.idle(30)
        self.assertEqual(self.only()['kind'], 'start-failed')

    def test_a_renderer_crash_outranks_the_drop_that_set_it_off(self):
        self.session()
        self.host.drop(self.now)
        self.tick(seconds=2)
        self.host.cores = [self.core()]
        self.host.results[RUN] = dict(CLEAN, renderer_returncode=-11)
        self.idle(30)
        report = self.only()
        self.assertEqual(report['kind'], 'renderer-crash')
        self.assertIn('USB', report['cause']['text'])

    def test_the_next_session_is_watched_afresh(self):
        self.session()
        self.host.results[RUN] = dict(CLEAN, renderer_returncode=1)
        self.tick('starting', OTHER, 3, status=None)
        self.tick('running', OTHER, 40)
        self.assertEqual(self.only()['run_id'], RUN)
        self.host.drm_state = 'disconnected'
        self.tick('running', OTHER, 40)
        self.assertEqual(len(self.reports()), 2)


class HyprlandTests(Case):
    def crash(self):
        self.session()
        self.host.hypr_up = False
        self.tick('failed', status=None, seconds=2, monitors=[], plugins=0)
        self.host.results[RUN] = dict(CLEAN, renderer_returncode=1, complete=False, error='ownership plugin retained for explicit recovery')

    def test_a_crash_of_the_live_compositor(self):
        self.crash()
        self.host.cores = [dict(exe_path='/usr/bin/Hyprland', exe='Hyprland', pid=1690, signal='SIGABRT', frames=[])]
        self.idle(30)
        report = self.only()
        self.assertEqual(report['kind'], 'hyprland-crash')
        self.assertEqual(report['core_dumps'][0]['exe'], 'Hyprland')

    def test_some_other_hyprland_aborting_is_not_the_live_compositors_crash(self):
        # 2026-11-03 18:08: a throwaway headless Hyprland aborted four times while the live one ran on.
        self.session()
        self.host.cores = [dict(exe_path='/usr/bin/Hyprland', exe='Hyprland', pid=4166041, signal='SIGABRT', frames=[])]
        self.tick(seconds=60)
        self.assertNoIncident()

    def test_the_compositor_gone_with_no_crash_record(self):
        self.crash()
        self.idle(int(CORE_WAIT_S) + 8)
        report = self.only()
        self.assertEqual(report['kind'], 'hyprland-crash')
        self.assertIn('logout', report['cause']['text'])

    def test_gone_while_the_controller_was_down_too(self):
        self.session()
        self.host.hypr_up = False
        self.fresh()
        self.host.results[RUN] = dict(CLEAN, renderer_returncode=1, complete=False, error='hyprctl failed')
        self.idle(int(CORE_WAIT_S) + 8)
        self.assertEqual(self.only()['kind'], 'hyprland-crash')


class ControllerRestartTests(Case):
    def test_an_unrecovered_run_found_at_start(self):
        self.session()
        self.fresh()
        self.tick('recovery-needed', status=None, seconds=30)
        report = self.only()
        self.assertEqual(report['kind'], 'controller-restart')
        self.assertEqual(report['summary'], 'the XR controller restarted and found an unfinished session')
        self.assertEqual(self.monitor.latest['kind'], 'controller-restart')

    def test_it_is_reported_once_however_many_restarts_follow(self):
        self.session()
        for _ in range(3):
            self.fresh()
            self.tick('recovery-needed', status=None, seconds=30)
        self.assertEqual(len(self.reports()), 1)

    def test_one_the_old_controller_never_watched(self):
        # An unrecovered run from before this code was installed, or a lost state file.
        self.tick('recovery-needed', status=None, seconds=30)
        self.assertEqual(self.only()['kind'], 'controller-restart')

    def test_a_run_that_needs_the_cli(self):
        self.tick('cli-recovery', status=None, seconds=30)
        self.assertEqual(self.only()['kind'], 'controller-restart')

    def test_a_crash_while_the_controller_was_down_keeps_its_name(self):
        self.session()
        self.fresh()
        self.host.results[RUN] = dict(CLEAN, renderer_returncode=-6)
        self.idle(int(CORE_WAIT_S) + 8)
        report = self.only()
        self.assertEqual(report['kind'], 'renderer-crash')
        self.assertIn('controller-restart', [t['kind'] for t in report['timeline']])

    def test_a_stop_before_the_restart_still_counts(self):
        self.session()
        self.tick('stopping', stop_requested=RUN, seconds=2, status=None)
        self.fresh()
        self.host.results[RUN] = dict(CLEAN, renderer_returncode=-9)
        self.idle(30)
        self.assertNoIncident()

    def test_the_latest_report_survives_a_restart(self):
        self.session()
        self.host.drop(self.now)
        self.tick(seconds=40)
        latest = self.monitor.latest
        self.fresh()
        self.assertEqual(self.monitor.latest, latest)

    def test_the_rate_limit_survives_a_restart(self):
        self.session()
        self.host.drop(self.now)
        self.tick(seconds=40)
        self.fresh()
        self.host.drop(self.now, 50)
        self.tick(seconds=40)
        self.assertEqual(len(self.reports()), 1)


class ReliabilityTests(Case):
    def test_what_the_panel_shows(self):
        self.session()
        self.host.drop(self.now)
        self.tick(seconds=40)
        latest = self.monitor.latest
        self.assertEqual(sorted(latest), ['at', 'id', 'kind', 'path', 'summary'])
        self.assertEqual(latest['kind'], 'usb-drop')
        self.assertEqual(latest['path'], str(self.reports()[0]))
        self.assertTrue(latest['at'].endswith('Z'))

    def test_a_private_folder_and_private_files(self):
        self.session()
        self.host.drop(self.now)
        self.tick(seconds=40)
        self.assertEqual(stat.S_IMODE(self.dir.stat().st_mode), 0o700)
        for path in self.dir.iterdir(): self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600, path.name)

    def test_no_source_can_stop_a_report(self):
        for name in ('result', 'journal', 'driver', 'drm', 'usb', 'core'):
            self.host.broken.add(name)
        self.session()
        self.tick(status=dict(GOOD, presented_hz=None), seconds=40)
        self.assertEqual(self.only()['kind'], 'present-stall')

    def test_a_broken_kernel_log_does_not_stop_the_rest(self):
        self.host.broken.add('kernel')
        self.session()
        self.host.drm_state = 'disconnected'
        self.tick(seconds=40)
        self.assertEqual(self.only()['kind'], 'video-lost')

    def test_a_journal_hiccup_at_the_start_never_replays_old_drops(self):
        # With no cursor, journalctl would hand back its last lines: the day's old USB drops, as if they were new.
        self.host.drop(self.now - 3600)
        self.host.driver = '2026-11-03 18:00:00.000 Retrying driver connection in 1 second\n'
        self.host.broken |= {'kernel', 'driver'}
        self.session()
        self.host.broken -= {'kernel', 'driver'}
        real = self.host.kernel_read
        self.host.kernel_read = lambda cursor: real(cursor if cursor is not None else 0)  # what a read with no cursor gives
        self.tick(seconds=40)
        self.assertNoIncident()

    def test_it_recovers_the_log_position_after_a_hiccup(self):
        self.host.broken |= {'kernel', 'driver'}
        self.session()
        self.host.broken -= {'kernel', 'driver'}
        self.tick(seconds=5)
        self.host.drop(self.now)
        self.tick(seconds=40)
        self.assertEqual(self.only()['kind'], 'usb-drop')

    def test_a_malformed_consent_still_reports(self):
        self.host.consents[RUN] = dict(CONSENT, renderer='not a pin', plugin=7)
        self.session()
        self.host.drop(self.now)
        self.tick(seconds=40)
        self.assertEqual(self.only()['versions']['bin'], None)

    def test_one_report_failing_does_not_lose_the_next(self):
        real = self.monitor._evidence
        calls = []
        def flaky(episode, suppressed):
            calls.append(episode.run_id)
            if len(calls) == 1: raise RuntimeError('bad evidence')
            return real(episode, suppressed)
        self.monitor._evidence = flaky
        self.session()
        self.host.results[RUN] = dict(CLEAN, renderer_returncode=1)
        self.tick('starting', OTHER, 3, status=None)
        self.host.results[OTHER] = dict(CLEAN, run_id=OTHER, renderer_returncode=-9)
        self.idle(30)
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.only()['run_id'], OTHER)

    def test_an_unreadable_consent_does_not_stop_a_report(self):
        self.host.consents.clear()
        self.session()
        self.tick(status=dict(GOOD, presented_hz=None), seconds=40)
        self.assertEqual(len(self.reports()), 0)  # without a consent the run's tracking choice is unknown, so a stall means nothing
        self.host.drop(self.now)
        self.tick(seconds=40)
        self.assertEqual(self.only()['kind'], 'usb-drop')

    def test_no_notification_says_saved_when_nothing_was_saved(self):
        (self.root / 'incidents').mkdir(mode=0o755)
        os.chmod(self.dir, 0o755)
        self.session()
        self.host.drop(self.now)
        self.tick(seconds=40)
        self.assertEqual(self.toasts, [])
        self.assertEqual(self.handed, [])
        self.assertIsNone(self.monitor.latest)

    def test_a_broken_notifier_does_not_stop_the_hook(self):
        def boom(_): raise OSError('no bus')
        self.monitor.notify = boom
        self.session()
        self.host.drop(self.now)
        self.tick(seconds=40)
        self.assertEqual(self.handed, self.reports())

    def test_the_real_hook_runs_with_the_report(self):
        hook = self.root / 'on-incident'
        hook.write_text(f'#!/bin/sh\nprintf "%s" "$1" > "{self.root}/got"\n')
        os.chmod(hook, 0o755)
        self.monitor = IncidentMonitor(self.host, Reports(self.dir), notify=self.toasts.append, hook=hook, spawn=lambda work: work(),
                                       scrub=Scrubber(home='/home/testuser', user='testuser', host='testhost'), clock=lambda: self.now)
        self.session()
        self.host.drop(self.now)
        self.tick(seconds=40)
        self.assertEqual((self.root / 'got').read_text(), str(self.reports()[0]))
        self.assertIn('rc=0', (self.dir / 'hook.log').read_text())

    def test_no_hook_installed_is_fine(self):
        self.monitor = IncidentMonitor(self.host, Reports(self.dir), notify=self.toasts.append, hook=self.root / 'nothing', spawn=lambda work: work(),
                                       scrub=Scrubber(home='/home/testuser', user='testuser', host='testhost'), clock=lambda: self.now)
        self.session()
        self.host.drop(self.now)
        self.tick(seconds=40)
        self.assertEqual(len(self.reports()), 1)
        self.assertFalse((self.dir / 'hook.log').exists())

    def test_the_logs_are_read_every_two_seconds_at_most(self):
        self.session(seconds=20)
        self.assertLessEqual(self.host.reads, 11)
        self.assertGreaterEqual(self.host.reads, 9)

    def test_an_idle_controller_reads_no_logs(self):
        self.idle(60)
        self.assertEqual(self.host.reads, 0)

    def test_a_flood_of_observations_is_bounded(self):
        for _ in range(100000): self.monitor.observe(dict(state='idle', run_id=None))
        self.assertLessEqual(len(self.monitor.queue), 256)

    def test_every_kind_is_saved_under_its_own_name(self):
        self.tick('recovery-needed', status=None, seconds=30)  # a long kind: controller-restart
        self.assertRegex(self.reports()[0].name, r'^\d{8}T\d{6}Z-controller-restart\.json$')

    def test_a_repeating_fault_is_said_once(self):
        self.host.broken.add('drm')
        self.session()
        said = [c.args[0] for c in self.said.call_args_list if 'drm failed' in c.args[0]]
        self.assertEqual(len(said), 1)

    def test_the_worker_thread_runs_steps_and_outlives_a_bad_one(self):
        calls = []
        def step(now):
            calls.append(now)
            if len(calls) == 1: raise RuntimeError('bad')
        self.monitor.step = step
        self.monitor.interval = 0.01
        self.monitor.start()
        self.addCleanup(self.monitor.stop)
        deadline = time.monotonic() + 3
        while len(calls) < 3 and time.monotonic() < deadline: time.sleep(.01)
        self.assertGreaterEqual(len(calls), 3)


KERNEL = """\
2026-11-03T19:17:03-05:00 costerou kernel: x86/split lock detection: #DB: CHTTPClientThre/2015542 took a bus_lock trap at address: 0xee4386c4
2026-11-03T19:17:14-05:00 costerou kernel: [UFW BLOCK] IN=wlp16s0u3 OUT= MAC=28:94:01:b8:9b:e3 SRC=10.0.0.15 DST=10.0.0.165
2026-11-03T19:18:33-05:00 costerou kernel: usb 5-2: USB disconnect, device number 42
2026-11-03T19:18:33-05:00 costerou kernel: usb 5-2.1: USB disconnect, device number 45
2026-11-03T19:18:33-05:00 costerou kernel: hub 5-2:1.0: 4 ports detected
2026-11-03T19:18:34-05:00 costerou kernel: uvcvideo 5-2.2:1.0: Found UVC 1.00 device USB 2.0 Camera (0c45:636b)
2026-11-03T19:18:34-05:00 costerou kernel: hid-generic 0003:35CA:1102.0091: unbalanced collection at end of report description
2026-11-03T19:18:34-05:00 costerou kernel: usb 1-1: Product: Somebody's Phone
2026-11-03T14:58:36-05:00 costerou kernel: amdgpu 0000:03:00.0: [drm] REG_WAIT timeout 1us * 100 tries - dcn32_program_compbuf_size line:149
2026-11-03T14:58:37-05:00 costerou kernel: xhci_hcd 0000:12:00.4: xHCI host controller not responding, assume dead
2026-11-03T14:58:38-05:00 costerou kernel: PM: suspend entry (deep)
2026-11-03T14:58:39-05:00 costerou kernel: audit: type=1400 audit(1): something else entirely
""".splitlines()


class RelevantKernelTests(unittest.TestCase):
    def test_it_keeps_usb_for_the_glasses_and_the_gpu_and_nothing_else(self):
        kept = relevant_kernel(KERNEL, {'5-2.1'})
        text = '\n'.join(kept)
        for wanted in ('usb 5-2: USB disconnect', 'usb 5-2.1: USB disconnect', 'hub 5-2:1.0', 'uvcvideo 5-2.2', '35CA', 'REG_WAIT', 'xhci_hcd', 'PM: suspend'):
            self.assertIn(wanted, text)
        for unwanted in ('split lock', 'UFW', 'Phone', 'audit'):
            self.assertNotIn(unwanted, text)


CORE_INFO = """\
           PID: 33597 (focus_center_te)
           UID: 1000 (testuser)
        Signal: 6 (ABRT) si_code: SI_TKILL
  Command Line: build/native-spike/focus_center_test --secret-arg
    Executable: /home/testuser/x/focus_center_test
       Storage: /var/lib/systemd/coredump/core.focus_center_te.zst (present)
       Message: Process 33597 (focus_center_te) of user 1000 dumped core.

                Stack trace of thread 33597:
                #0  0x00007fa89ea9a11c n/a (libc.so.6 + 0x9a11c)
                #1  0x00007fa89ea3e5d0 raise (libc.so.6 + 0x3e5d0)
                #2  0x00007fa89ea25685 abort (libc.so.6 + 0x25685)

                Stack trace of thread 33600:
                #0  0x00007fa89ea9a11c poll (libc.so.6 + 0x9a11c)
                ELF object binary architecture: AMD x86-64
"""


class HostSourcesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for name in ('runtime', 'state', 'data', 'sys/class/drm', 'sys/bus/usb/devices', 'proc/sys/kernel/random'): (self.root / name).mkdir(parents=True)
        os.chmod(self.root / 'state', 0o700)
        self.calls = []
        self.replies = {}
        self.driver_log = self.root / 'driver.log'
        self.host = HostSources(self.root / 'runtime', self.root / 'state', repo=self.root, sys_root=self.root / 'sys',
                                proc_root=self.root / 'proc', driver_log=self.driver_log, run=self.fake_run)

    def fake_run(self, argv, **kw):
        self.calls.append((argv, kw))
        for needle, reply in self.replies.items():
            if needle in argv:
                if isinstance(reply, Exception): raise reply
                out, rc = reply
                return subprocess.CompletedProcess(argv, rc, stdout=out, stderr='')
        return subprocess.CompletedProcess(argv, 1, stdout='', stderr='')

    def test_every_command_is_bounded_and_has_no_shell(self):
        self.replies['-k'] = ('', 0)
        self.host.kernel_start()
        self.host.journal('x.service', 200)
        self.host.core_dumps(0, (), ())
        self.host.source()
        for argv, kw in self.calls:
            self.assertIsInstance(argv, list)
            self.assertNotIn('shell', kw)
            self.assertGreater(kw['timeout'], 0)
            self.assertEqual(kw['stdin'], subprocess.DEVNULL)

    def test_the_kernel_log_resumes_at_its_cursor(self):
        self.replies['-k'] = ('-- cursor: s=1;i=2\n', 0)
        self.assertEqual(self.host.kernel_start(), 's=1;i=2')
        self.assertIn('-n', self.calls[0][0])
        self.replies['-k'] = ('line one\nline two\n-- cursor: s=1;i=4\n', 0)
        lines, cursor = self.host.kernel_read('s=1;i=2')
        self.assertEqual(lines, ['line one', 'line two'])
        self.assertEqual(cursor, 's=1;i=4')
        self.assertIn('--after-cursor=s=1;i=2', self.calls[-1][0])

    def test_no_new_kernel_lines_keeps_the_cursor(self):
        self.replies['-k'] = ('-- No entries --\n', 0)
        self.assertEqual(self.host.kernel_read('s=1;i=2'), ([], 's=1;i=2'))

    def test_a_journal_that_cannot_be_read_says_so_quietly(self):
        self.replies['-k'] = (subprocess.TimeoutExpired('journalctl', 5))
        self.assertEqual(self.host.kernel_read('c'), ([], 'c'))
        self.replies['-k'] = FileNotFoundError('journalctl')
        self.assertEqual(self.host.kernel_around(0, 1), [])

    def test_the_kernel_around_a_time(self):
        self.replies['-k'] = ('a\n', 0)
        self.assertEqual(self.host.kernel_around(1000.5, 2000.5), ['a'])
        self.assertIn('--since=@1000', self.calls[-1][0])
        self.assertIn('--until=@2001', self.calls[-1][0])

    def test_a_unit_journal(self):
        self.replies['--user'] = ('one\ntwo\n', 0)
        self.assertEqual(self.host.journal('viture-xr-1.service', 200), ['one', 'two'])
        argv = self.calls[-1][0]
        self.assertEqual(argv[argv.index('-u') + 1], 'viture-xr-1.service')
        self.assertEqual(argv[argv.index('-n') + 1], '200')

    def test_the_driver_log_is_read_as_it_grows(self):
        self.driver_log.write_text('old\n')
        offset = self.host.driver_start()
        self.assertEqual(self.host.driver_read(offset), ('', 4))
        with self.driver_log.open('a') as stream: stream.write('new\n')
        self.assertEqual(self.host.driver_read(offset), ('new\n', 8))

    def test_a_driver_log_that_shrank_starts_over(self):
        self.driver_log.write_text('short\n')
        self.assertEqual(self.host.driver_read(1000), ('short\n', 6))

    def test_a_missing_driver_log_is_empty(self):
        self.assertEqual(self.host.driver_start(), 0)
        self.assertEqual(self.host.driver_read(0), ('', 0))
        self.assertEqual(self.host.driver_tail(10), [])

    def test_the_driver_log_is_not_followed_through_a_symlink(self):
        secret = self.root / 'secret'
        secret.write_text('private\n')
        self.driver_log.symlink_to(secret)
        self.assertEqual(self.host.driver_read(0), ('', 0))
        self.assertEqual(self.host.driver_tail(10), [])

    def test_the_tail_of_the_driver_log(self):
        self.driver_log.write_text(''.join(f'{i}\n' for i in range(500)))
        self.assertEqual(self.host.driver_tail(2), ['498', '499'])

    def test_the_connector(self):
        (self.root / 'sys/class/drm/card1-DP-2').mkdir()
        (self.root / 'sys/class/drm/card1-DP-2/status').write_text('connected\n')
        (self.root / 'sys/class/drm/card0-DP-22').mkdir()
        (self.root / 'sys/class/drm/card0-DP-22/status').write_text('disconnected\n')
        self.assertEqual(self.host.drm('DP-2'), 'connected')
        (self.root / 'sys/class/drm/card1-DP-2/status').write_text('disconnected\n')
        self.assertEqual(self.host.drm('DP-2'), 'disconnected')
        self.assertIsNone(self.host.drm('DP-9'))

    def test_the_glasses_ports_come_from_the_vendor_id(self):
        base = self.root / 'sys/bus/usb/devices'
        for name, vendor in (('5-2', '1a86'), ('5-2.1', '35ca'), ('5-2.3', '35CA'), ('3-1', '046d'), ('5-2.1:1.0', '35ca')):
            (base / name).mkdir()
            (base / name / 'idVendor').write_text(vendor + '\n')
        (base / 'usb5').mkdir()  # a root hub has its own entry; one with no idVendor must not break the walk
        self.assertEqual(self.host.usb_ports(), {'5-2.1', '5-2.3'})

    def test_an_unreadable_usb_tree_is_unknown_not_empty(self):
        (self.root / 'sys/bus/usb/devices').rmdir()
        self.assertIsNone(self.host.usb_ports())

    def test_the_live_compositors_pid_comes_from_its_lock_file(self):
        lock = self.root / 'runtime/hypr/sig'
        lock.mkdir(parents=True)
        (lock / 'hyprland.lock').write_text('1690\nwayland-1\n')
        self.assertEqual(self.host.hyprland_pid('sig'), 1690)
        self.assertIsNone(self.host.hyprland_pid('other'))
        self.assertIsNone(self.host.hyprland_pid(None))

    def test_a_pid_is_alive_only_as_hyprland(self):
        (self.root / 'proc/1690').mkdir()
        (self.root / 'proc/1690/comm').write_text('Hyprland\n')
        (self.root / 'proc/1691').mkdir()
        (self.root / 'proc/1691/comm').write_text('bash\n')  # the pid went to something else
        self.assertTrue(self.host.alive(1690))
        self.assertFalse(self.host.alive(1691))
        self.assertFalse(self.host.alive(1692))
        self.assertIsNone(self.host.alive(None))

    def test_core_dumps_are_matched_and_summarised(self):
        pick = dict(time=1793748201567984, pid=33597, sig=6, corefile='present', exe=PIN + 'xr-native-spike', size=1)
        skip = dict(time=1793748201567984, pid=7, sig=6, corefile='present', exe='/usr/bin/other', size=1)
        self.replies['list'] = (json.dumps([skip, pick]), 0)
        self.replies['info'] = (CORE_INFO, 0)
        core = self.host.core_dumps(1000.0, (PIN + 'xr-native-spike',), ())
        self.assertEqual(len(core), 1)
        self.assertEqual(core[0]['exe'], 'xr-native-spike')
        self.assertEqual(core[0]['signal'], 'SIGABRT')
        self.assertEqual(len(core[0]['frames']), 3)  # the crashing thread only
        self.assertNotIn('secret-arg', json.dumps(core))
        self.assertIn('--since=@1000', self.calls[0][0])

    def test_a_core_dump_is_matched_by_pid_too(self):
        self.replies['list'] = (json.dumps([dict(time=1, pid=1690, sig=6, exe='/usr/bin/Hyprland')]), 0)
        self.assertEqual(self.host.core_dumps(0, (), (1690,))[0]['exe'], 'Hyprland')

    def test_no_core_dumps(self):
        self.assertEqual(self.host.core_dumps(0, ('x',), ()), [])  # coredumpctl exits 1 for none
        self.replies['list'] = ('not json', 0)
        self.assertEqual(self.host.core_dumps(0, ('x',), ()), [])

    def test_stack_frames_are_the_crashing_threads_only(self):
        self.assertEqual(stack_frames(CORE_INFO), ['#0  0x00007fa89ea9a11c n/a (libc.so.6 + 0x9a11c)', '#1  0x00007fa89ea3e5d0 raise (libc.so.6 + 0x3e5d0)',
                                                   '#2  0x00007fa89ea25685 abort (libc.so.6 + 0x25685)'])

    def test_the_source_version(self):
        self.replies['rev-parse'] = ('13c8d5e\n', 0)
        self.replies['--porcelain'] = (' M a.py\n', 0)
        self.assertEqual(self.host.source(), dict(sha='13c8d5e', dirty=True))

    def test_the_run_files(self):
        state = self.root / 'state'
        (state / f'{RUN}.json').write_text(json.dumps(CONSENT))
        (state / f'{RUN}.result.json').write_text(json.dumps(CLEAN))
        for path in state.iterdir(): os.chmod(path, 0o600)
        self.assertEqual(self.host.consent(RUN)['output'], 'DP-2')
        self.assertEqual(self.host.result(RUN)['phase'], 'done')
        self.assertIsNone(self.host.result(OTHER))

    def test_a_result_for_another_run_is_not_this_runs_result(self):
        state = self.root / 'state'
        (state / f'{OTHER}.result.json').write_text(json.dumps(CLEAN))
        os.chmod(state / f'{OTHER}.result.json', 0o600)
        self.assertIsNone(self.host.result(OTHER))

    def test_the_boot_id(self):
        (self.root / 'proc/sys/kernel/random/boot_id').write_text('abc\n')
        self.assertEqual(self.host.boot_id(), 'abc')


if __name__ == '__main__': unittest.main()
