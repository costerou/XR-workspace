"""The detectors, offline: what counts as an incident and, as much, what does not.

The kernel, driver and unit lines are the real ones of 2026-11-03 (the 19:18 drop, a Stop during a start, the runs that
ended without one), with a fixed run id. Nothing here reads the host.
"""
import json
import unittest

from tools.xr_controller import incident_detect as d
from tools.xr_controller.incident_detect import Correlator, DriverLog, KernelLog, RunWatch, Signal, classify_end

RUN = '2298c7a18df736b9b6c4bde5b5877031'
CONSENT = dict(output='DP-2', recovery_output='DP-1', tracking='xrlinuxdriver', model='luma-ultra')
UNTRACKED = dict(CONSENT, tracking=False)
GOOD = dict(presented_hz=120.0, missed=0, capture_path='dmabuf', tracking=True, capture_failure_run=0)
MODE = dict(name='DP-2', width=1920, height=1200, refresh=120.0, dpms=True)


def obs(at, state='running', status=GOOD, monitor=MODE, **over):
    return dict(dict(at=at, state=state, run_id=RUN, status=status, monitor=monitor, monitors_known=True, plugins=1,
                     drm='connected', usb_present=True, hyprland_alive=True, driver_alive=True), **over)


def run(watch, seconds, start=0, **over):
    """One observation a second; the signals raised."""
    out = []
    for at in range(start, start + seconds): out += watch.observe(obs(float(at), **over))
    return out


def healthy(consent=CONSENT, seconds=20):
    watch = RunWatch(RUN, consent, 0.0)
    run(watch, seconds)
    return watch


def kinds(signals): return [s.kind for s in signals]


DROP = """\
2026-11-03T19:18:33-05:00 costerou kernel: usb 5-2: USB disconnect, device number 42
2026-11-03T19:18:33-05:00 costerou kernel: usb 5-2.1: USB disconnect, device number 45
2026-11-03T19:18:33-05:00 costerou kernel: usb 5-2.2: USB disconnect, device number 43
2026-11-03T19:18:33-05:00 costerou kernel: usb 5-2.3: USB disconnect, device number 44
2026-11-03T19:18:33-05:00 costerou kernel: usb 5-2: new high-speed USB device number 46 using xhci_hcd
2026-11-03T19:18:34-05:00 costerou kernel: usb 5-2.1: New USB device found, idVendor=35ca, idProduct=1104, bcdDevice= 0.01
2026-11-03T19:18:40-05:00 costerou kernel: usb 5-2: USB disconnect, device number 46
2026-11-03T19:18:40-05:00 costerou kernel: usb 5-2.1: USB disconnect, device number 47
""".splitlines()


class KernelLogTests(unittest.TestCase):
    def test_a_hub_drop_and_its_children_are_one_signal_each_time_it_drops(self):
        out = KernelLog({'5-2.1', '5-2.3'}).feed(DROP)
        self.assertEqual(kinds(out), ['usb-drop', 'usb-drop'])  # 19:18:33 and 19:18:40: the pair seen all day on 2026-11-03
        self.assertEqual(out[1].at - out[0].at, 7)

    def test_the_port_comes_from_the_vendor_not_a_fixed_name(self):
        # A disconnect line names no vendor: only the vendor id in the kernel's own lines says which port is the glasses'.
        log = KernelLog()
        self.assertEqual(log.feed(['2026-11-03T00:00:10-05:00 h kernel: usb 1-4: USB disconnect, device number 3']), [])
        log.feed(['2026-11-03T00:00:11-05:00 h kernel: usb 1-4: New USB device found, idVendor=35ca, idProduct=1104, bcdDevice= 0.01'])
        self.assertEqual(kinds(log.feed(['2026-11-03T00:00:14-05:00 h kernel: usb 1-4: USB disconnect, device number 4'])), ['usb-drop'])

    def test_a_game_controller_dropping_as_a_run_ends_is_not_the_glasses(self):
        # The real lines of 2026-11-03 00:00:14: run af60daa6 ended in that very second, for a reason of its own.
        self.assertEqual(KernelLog({'5-2.1', '5-2.3'}).feed([
            '2026-11-03T00:00:14-05:00 costerou kernel: usb 1-4: USB disconnect, device number 11',
            '2026-11-03T00:00:15-05:00 costerou kernel: usb 1-4: New USB device found, idVendor=057e, idProduct=2009, bcdDevice= 2.10',
            '2026-11-03T00:00:15-05:00 costerou kernel: usb 1-4: Product: Pro Controller']), [])

    def test_a_parent_hub_counts_for_the_glasses_behind_it(self):
        self.assertEqual(kinds(KernelLog({'5-2.1'}).feed(['2026-11-03T19:18:33-05:00 h kernel: usb 5-2: USB disconnect, device number 42'])),
                         ['usb-drop'])

    def test_another_device_unplugged_is_no_incident(self):
        log = KernelLog({'5-2.1'})
        self.assertEqual(log.feed(['2026-11-03T12:36:52-05:00 h kernel: usb 1-1: USB disconnect, device number 9',
                                   '2026-11-03T12:36:52-05:00 h kernel: usb 5-20: USB disconnect, device number 9']), [])

    def test_noise_and_new_devices_alone_are_no_incident(self):
        log = KernelLog({'5-2.1'})
        self.assertEqual(log.feed(['2026-11-03T19:17:03-05:00 h kernel: x86/split lock detection: #DB: CHTTPClientThre/1 took a bus_lock trap',
                                   '2026-11-03T19:18:34-05:00 h kernel: usb 5-2.1: New USB device found, idVendor=35ca, idProduct=1104, bcdDevice= 0.01',
                                   'not a journal line at all']), [])

    def test_a_suspend_and_the_half_minute_after_it_are_no_incident(self):
        log = KernelLog({'5-2.1'})
        self.assertEqual(log.feed(['2026-11-03T22:00:00-05:00 h kernel: PM: suspend entry (deep)',
                                   '2026-11-03T22:00:01-05:00 h kernel: usb 5-2: USB disconnect, device number 1',
                                   '2026-11-03T23:00:00-05:00 h kernel: PM: suspend exit',
                                   '2026-11-03T23:00:05-05:00 h kernel: usb 5-2: USB disconnect, device number 2']), [])
        self.assertEqual(kinds(log.feed(['2026-11-03T23:01:00-05:00 h kernel: usb 5-2: USB disconnect, device number 3'])), ['usb-drop'])


class DriverLogTests(unittest.TestCase):
    def feed(self, text, at=100.0, log=None): return (log or DriverLog()).feed(text, at)

    def test_retrying_the_connection_is_a_drop(self):
        out = self.feed('2026-11-03 20:06:13.925 Retrying driver connection in 1 second\n')
        self.assertEqual(kinds(out), ['usb-drop'])
        self.assertEqual(out[0].source, 'driver-log')

    def test_one_failed_usb_command_is_not_a_drop(self):
        # 42 of the log's 438 were not followed by a retry within 30 s.
        self.assertEqual(self.feed('[E][libglasses] Failed to send USB command - MsgID: 0x0007, error code: -1\n'
                                   '[E][libglasses] get_display_mode execute_usb_command_with_response returned -7\n'), [])

    def test_two_failed_usb_commands_close_together_are_a_drop(self):
        log = DriverLog()
        line = '[E][libglasses] Failed to send USB command - MsgID: 0x0007, error code: -1\n'
        self.assertEqual(log.feed(line, 100.0), [])
        self.assertEqual(kinds(log.feed(line, 110.0)), ['usb-drop'])

    def test_two_failed_usb_commands_far_apart_are_not(self):
        log = DriverLog()
        line = '[E][libglasses] Failed to send USB command - MsgID: 0x0007, error code: -1\n'
        self.assertEqual(log.feed(line, 100.0) + log.feed(line, 200.0), [])

    def test_an_imu_failure_counts_as_a_failed_command(self):
        log = DriverLog()
        log.feed('[E][libglasses] Failed to send USB command - MsgID: 0x0007, error code: -1\n', 100.0)
        self.assertEqual(kinds(log.feed('2026-11-03 20:06:13.000 [ERROR] VITURE open_imu failed at 1000Hz (-3 USB execution error)\n', 101.0)),
                         ['usb-drop'])

    def test_the_licence_noise_of_every_healthy_run_is_nothing(self):
        self.assertEqual(self.feed('2026-11-03 20:06:28.700 [ERROR] Failed to get hardwareId, retrying in 5 seconds\n'
                                   '2026-11-03 20:06:58.701 [ERROR] No hardwareId found, not retrieving license\n'
                                   '[E][libglasses] m_StateCallback is not initialized\n'
                                   '2026-11-03 20:07:12.000 Device calibration complete\n'
                                   '2026-11-03 20:07:12.001 Centering screen\n'), [])

    def test_recalibrating_or_restarting_mid_run_loses_tracking(self):
        self.assertEqual(kinds(self.feed('2026-11-03 20:06:16.929 Waiting on device calibration\n')), ['tracking-lost'])
        self.assertEqual(kinds(self.feed('2026-11-03 20:06:58.701 Starting up XR driver\n')), ['tracking-lost'])

    def test_a_line_split_across_two_reads_counts_once(self):
        log = DriverLog()
        self.assertEqual(log.feed('2026-11-03 20:06:13.925 Retrying driver', 100.0), [])
        self.assertEqual(kinds(log.feed(' connection in 1 second\n', 101.0)), ['usb-drop'])


class RunWatchTests(unittest.TestCase):
    def test_a_healthy_session_raises_nothing(self):
        self.assertEqual(run(RunWatch(RUN, CONSENT, 0.0), 600), [])

    # --- the video link ---------------------------------------------------------------------------------------------
    def test_the_connector_disconnecting_is_lost_video_once(self):
        out = run(healthy(), 10, start=20, drm='disconnected')
        self.assertEqual(kinds(out), ['video-lost'])

    def test_a_config_reloads_short_mode_change_is_nothing(self):
        # managed.after_reload puts the session mode back: a theme switch can show 1080p for a moment.
        watch = healthy()
        self.assertEqual(run(watch, 4, start=20, monitor=dict(MODE, height=1080, refresh=60.0)) + run(watch, 20, start=24), [])

    def test_a_mode_that_stays_changed_is_lost_video_once(self):
        out = run(healthy(), 30, start=20, monitor=dict(MODE, height=1080, refresh=60.0))
        self.assertEqual(kinds(out), ['video-lost'])
        self.assertIn('1920x1080@60', out[0].detail)

    def test_the_output_leaving_hyprland_is_lost_video(self):
        self.assertEqual(kinds(run(healthy(), 10, start=20, monitor=None)), ['video-lost'])

    def test_a_run_whose_output_is_unknown_has_no_output_to_miss(self):
        self.assertEqual(run(RunWatch(RUN, {}, 0.0), 30, monitor=None), [])

    def test_an_unreadable_monitor_list_says_nothing_about_the_output(self):
        self.assertEqual(run(healthy(), 30, start=20, monitor=None, monitors_known=False), [])

    # --- presentation -----------------------------------------------------------------------------------------------
    def test_no_frames_for_five_seconds_is_a_stall_once(self):
        out = run(healthy(), 20, start=20, status=dict(GOOD, presented_hz=None))
        self.assertEqual(kinds(out), ['present-stall'])

    def test_the_renderer_leaving_out_the_rate_is_a_stall(self):
        # metrics.cpp: under two presents in the last second, presented_hz is simply absent.
        status = {k: v for k, v in GOOD.items() if k != 'presented_hz'}
        self.assertEqual(kinds(run(healthy(), 20, start=20, status=status)), ['present-stall'])

    def test_a_short_gap_in_frames_is_nothing(self):
        watch = healthy()
        self.assertEqual(run(watch, 4, start=20, status=dict(GOOD, presented_hz=None)) + run(watch, 20, start=24), [])

    def test_an_untracked_static_desktop_presents_nothing_and_that_is_fine(self):
        # wayland.cpp frame_done: without head tracking the renderer draws only when something changes.
        self.assertEqual(run(RunWatch(RUN, UNTRACKED, 0.0), 120, status=dict(GOOD, presented_hz=None, tracking=None)), [])

    def test_the_screen_asleep_presents_nothing_and_that_is_fine(self):
        self.assertEqual(run(healthy(), 120, start=20, status=dict(GOOD, presented_hz=None), monitor=dict(MODE, dpms=False)), [])

    def test_no_status_reply_says_nothing_about_frames(self):
        self.assertEqual(run(healthy(), 120, start=20, status=None), [])

    def test_a_collapse_in_the_rate_is_a_stall_once(self):
        out = run(healthy(), 40, start=20, status=dict(GOOD, presented_hz=30.0))
        self.assertEqual(kinds(out), ['present-stall'])
        self.assertIn('30', out[0].detail)

    def test_a_short_dip_is_nothing(self):
        watch = healthy()
        self.assertEqual(run(watch, 8, start=20, status=dict(GOOD, presented_hz=30.0)) + run(watch, 20, start=28), [])

    def test_halving_to_sixty_under_load_is_not_a_collapse(self):
        self.assertEqual(run(healthy(), 60, start=20, status=dict(GOOD, presented_hz=60.0)), [])

    def test_a_steady_sixty_hertz_session_is_healthy(self):
        self.assertEqual(run(RunWatch(RUN, CONSENT, 0.0), 120, status=dict(GOOD, presented_hz=60.0)), [])

    def test_a_low_rate_at_the_very_start_is_not_a_collapse(self):
        watch = RunWatch(RUN, CONSENT, 0.0)
        self.assertEqual(run(watch, 3, status=dict(GOOD, presented_hz=20.0)) + run(watch, 60, start=3), [])

    # --- capture ----------------------------------------------------------------------------------------------------
    def test_dmabuf_falling_back_to_shm_is_a_capture_failure(self):
        self.assertEqual(kinds(run(healthy(), 5, start=20, status=dict(GOOD, capture_path='shm'))), ['capture-failed'])

    def test_a_shm_session_from_the_start_is_healthy(self):
        self.assertEqual(run(RunWatch(RUN, CONSENT, 0.0), 30, status=dict(GOOD, capture_path='shm')), [])

    def test_a_resize_failing_a_capture_or_two_is_nothing(self):
        # Expected, and ended by the next frame: 21 failures over 39 desktop resizes in capture_e2e_churn_resize.
        self.assertEqual(run(healthy(), 60, start=20, status=dict(GOOD, capture_failure_run=2)), [])

    def test_a_capture_stuck_failing_is_reported_once(self):
        self.assertEqual(kinds(run(healthy(), 10, start=20, status=dict(GOOD, capture_failure_run=d.CAPTURE_STUCK))), ['capture-failed'])

    def test_a_capture_that_recovers_and_sticks_again_is_news(self):
        watch = healthy()
        stuck = dict(GOOD, capture_failure_run=d.CAPTURE_STUCK + 5)
        self.assertEqual(kinds(run(watch, 3, start=20, status=stuck) + run(watch, 3, start=23) + run(watch, 3, start=26, status=stuck)), ['capture-failed'] * 2)

    def test_an_older_renderer_without_the_count_is_fine(self):
        status = {k: v for k, v in GOOD.items() if k != 'capture_failure_run'}
        self.assertEqual(run(healthy(), 30, start=20, status=status), [])

    # --- tracking ---------------------------------------------------------------------------------------------------
    def test_tracking_lost_for_three_seconds_once(self):
        self.assertEqual(kinds(run(healthy(), 20, start=20, status=dict(GOOD, tracking=False))), ['tracking-lost'])

    def test_a_tracking_blip_is_nothing(self):
        # The renderer flips at 100 ms without a sample (pose.hpp): a blip is not lost tracking.
        watch = healthy()
        self.assertEqual(run(watch, 2, start=20, status=dict(GOOD, tracking=False)) + run(watch, 20, start=22), [])

    def test_tracking_that_never_starts_is_reported_after_half_a_minute(self):
        out = run(RunWatch(RUN, CONSENT, 0.0), 40, status=dict(GOOD, tracking=False))
        self.assertEqual(kinds(out), ['tracking-lost'])
        self.assertGreaterEqual(out[0].at, 30)

    def test_a_session_without_tracking_is_never_short_of_it(self):
        self.assertEqual(run(RunWatch(RUN, UNTRACKED, 0.0), 60, status=dict(GOOD, tracking=None), driver_alive=False), [])

    def test_the_tracking_driver_process_going_away(self):
        self.assertEqual(kinds(run(healthy(), 10, start=20, driver_alive=False)), ['tracking-lost'])

    # --- USB, compositor, plugin, the run itself ------------------------------------------------------------------------
    def test_the_glasses_leaving_the_usb_bus(self):
        self.assertEqual(kinds(run(healthy(), 10, start=20, usb_present=False)), ['usb-drop'])

    def test_a_session_never_seen_on_usb_has_no_usb_to_lose(self):
        self.assertEqual(run(RunWatch(RUN, CONSENT, 0.0), 30, usb_present=False), [])

    def test_a_driver_never_seen_alive_is_not_a_driver_lost(self):
        self.assertEqual(run(RunWatch(RUN, CONSENT, 0.0), 30, driver_alive=False), [])

    def test_hyprland_going_away(self):
        self.assertEqual(kinds(run(healthy(), 5, start=20, hyprland_alive=False)), ['hyprland-crash'])

    def test_the_plugin_leaving_hyprland(self):
        self.assertEqual(kinds(run(healthy(), 10, start=20, plugins=0)), ['plugin-failed'])

    def test_an_unreadable_plugin_list_says_nothing(self):
        self.assertEqual(run(healthy(), 30, start=20, plugins=0, monitors_known=False, monitor=None), [])

    def test_a_run_gone_silent_while_its_unit_lives(self):
        self.assertEqual(kinds(run(healthy(), 30, start=20, state='failed', status=None)), ['watchdog'])

    def test_a_key_stops_teardown_is_not_a_silent_run(self):
        # The control socket goes first, then recovery, a few seconds at most: state 'failed' with the unit still up.
        self.assertEqual(run(healthy(), 5, start=20, state='failed', status=None), [])

    def test_nothing_is_read_into_a_stop_in_progress(self):
        self.assertEqual(run(healthy(), 60, start=20, state='stopping', status=None, monitor=dict(MODE, height=1080, refresh=60.0), plugins=0,
                             drm='disconnected', usb_present=False, driver_alive=False, hyprland_alive=False), [])

    def test_nothing_is_read_into_a_start_in_progress(self):
        # Before 'running' the session mode, plugin and tracking are still being set up.
        self.assertEqual(run(RunWatch(RUN, CONSENT, 0.0), 60, state='starting', status=None, monitor=dict(MODE, height=1080, refresh=60.0),
                             plugins=0, driver_alive=False), [])

    def test_the_mode_a_session_compares_with_is_its_running_mode(self):
        watch = RunWatch(RUN, CONSENT, 0.0)
        run(watch, 10, state='starting', status=None, monitor=dict(MODE, height=1080, refresh=60.0))
        self.assertEqual(run(watch, 30, start=10), [])

    def test_a_suspend_is_no_stall(self):
        # A gap between observations (the machine slept, or the controller did) restarts every timer.
        watch = healthy()
        run(watch, 4, start=20, status=dict(GOOD, presented_hz=None))
        self.assertEqual(run(watch, 4, start=500, status=dict(GOOD, presented_hz=None)), [])

    def test_a_resume_gives_the_display_and_usb_time_to_come_back(self):
        watch = healthy()
        self.assertEqual(run(watch, 10, start=500, drm='disconnected', usb_present=False, monitor=None), [])
        self.assertEqual(run(watch, 20, start=510), [])

    def test_a_display_still_gone_after_the_resume_is_reported(self):
        watch = healthy()
        run(watch, 5, start=500)
        self.assertEqual(kinds(run(watch, 40, start=505, drm='disconnected')), ['video-lost'])

    def test_it_remembers_what_it_last_saw(self):
        watch = healthy()
        run(watch, 1, start=20, status=dict(GOOD, presented_hz=118.5))
        self.assertEqual(watch.last_status['presented_hz'], 118.5)
        self.assertEqual(watch.mode_text(), '1920x1200@120')


class EndOfRunTests(unittest.TestCase):
    def result(self, **over):
        return dict(dict(run_id=RUN, phase='done', complete=True, renderer_returncode=0, remaining=[], error=None), **over)

    def end(self, result, **over): return kinds(classify_end(100.0, result, **dict(dict(started=True), **over)))

    # --- what is not an incident ------------------------------------------------------------------------------------
    def test_a_clean_end_is_nothing(self):
        self.assertEqual(self.end(self.result()), [])

    def test_stop_is_nothing(self):
        self.assertEqual(self.end(self.result(), stopped=True), [])

    def test_stops_own_sigterm_or_its_sigkill_after_the_grace_is_nothing(self):
        for rc in (-15, -9): self.assertEqual(self.end(self.result(renderer_returncode=rc), stopped=True), [])

    def test_stop_during_a_start_is_nothing(self):
        # The real result of run 0df845d2, stopped at 17:19:27 while tracking calibrated.
        self.assertEqual(self.end(dict(complete=False, error='KeyboardInterrupt', phase='tracking', remaining=None, renderer_returncode=None,
                                       run_id=RUN), started=False), [])

    def test_stop_with_the_unit_already_gone_and_no_result_is_nothing(self):
        self.assertEqual(self.end(None, stopped=True), [])

    def test_a_supervisor_that_saw_the_interrupt_is_a_stop_too(self):
        self.assertEqual(self.end(self.result(renderer_returncode=-15, stop_reason='interrupted')), [])

    # --- the renderer -----------------------------------------------------------------------------------------------
    def test_the_usb_drops_rc_1_is_an_abnormal_exit(self):
        # The real result of run 2298c7a1, 19:18:40: the result itself looks tidy.
        self.assertEqual(self.end(self.result(renderer_returncode=1)), ['renderer-exit'])

    def test_a_crash_signal(self):
        out = classify_end(100.0, self.result(renderer_returncode=-11), started=True)
        self.assertEqual(kinds(out), ['renderer-crash'])
        self.assertIn('SIGSEGV', out[0].detail)

    def test_a_crash_while_stopping_is_still_a_crash(self):
        self.assertEqual(self.end(self.result(renderer_returncode=-6), stopped=True), ['renderer-crash'])

    def test_something_else_killing_the_renderer(self):
        self.assertEqual(self.end(self.result(renderer_returncode=-9)), ['renderer-crash'])

    def test_the_heartbeat_watchdog(self):
        out = classify_end(100.0, self.result(renderer_returncode=-9, stop_reason='heartbeat-timeout'), started=True)
        self.assertEqual(kinds(out), ['watchdog'])
        self.assertIn('2 seconds', out[0].detail)

    def test_the_guard_ending_a_run_is_a_watchdog_even_on_a_clean_exit(self):
        # The guard SIGTERMs the renderer, which exits 0.
        self.assertEqual(self.end(self.result(stop_reason='guard-failed')), ['watchdog'])

    def test_a_send_that_failed_on_a_renderer_stopping_by_itself_is_the_renderers_story(self):
        self.assertEqual(self.end(self.result(renderer_returncode=1, stop_reason='send-failed')), ['renderer-exit'])
        self.assertEqual(self.end(self.result(renderer_returncode=-11, stop_reason='send-failed')), ['renderer-crash'])

    def test_a_send_that_failed_on_a_live_renderer_is_the_supervisors(self):
        self.assertEqual(self.end(self.result(stop_reason='send-failed')), ['watchdog'])

    def test_the_renderer_closing_its_socket_says_nothing_new(self):
        self.assertEqual(self.end(self.result(stop_reason='renderer-closed')), [])

    # --- the run ----------------------------------------------------------------------------------------------------
    def test_a_unit_dead_without_a_result(self):
        out = classify_end(100.0, None, started=True, unit=dict(code='killed', status='9/KILL', result='signal'))
        self.assertEqual(kinds(out), ['run-killed'])
        self.assertIn('9/KILL', out[0].detail)

    def test_out_of_memory(self):
        out = classify_end(100.0, None, started=True, unit=dict(code='killed', status='9/KILL', result='oom-kill'))
        self.assertIn('out of memory', out[0].detail)

    def test_a_stop_that_left_the_run_stranded_is_not_normal(self):
        self.assertEqual(self.end(None, stopped=True, stranded=True), ['run-killed'])

    def test_a_unit_dead_before_the_session_was_up_is_a_failed_start(self):
        self.assertEqual(self.end(None, started=False), ['start-failed'])

    def test_an_error_before_the_run(self):
        self.assertEqual(self.end(dict(complete=False, error='tracking driver did not calibrate in time', phase='tracking', remaining=None,
                                       renderer_returncode=None, run_id=RUN), started=False), ['start-failed'])

    def test_the_plugin_failing_to_load(self):
        self.assertEqual(self.end(dict(complete=False, error='plugin load not confirmed by readback', phase='plugin', remaining=None,
                                       renderer_returncode=None, run_id=RUN), started=False), ['plugin-failed'])

    def test_the_plugin_failing_to_unload(self):
        self.assertEqual(self.end(self.result(error='unload: plugin still loaded after unload', phase='teardown')), ['plugin-failed'])

    def test_a_run_retained_for_recovery(self):
        # The real result of run a16e723d: renderer rc 0, yet recovery never finished.
        self.assertEqual(self.end(dict(complete=False, error='ownership plugin retained for explicit recovery', phase='run',
                                       remaining=['b3e3'], renderer_returncode=0, run_id=RUN)), ['run-failed'])

    def test_an_incomplete_recovery_without_an_error(self):
        self.assertEqual(self.end(self.result(complete=False)), ['run-failed'])


UNIT = """\
2026-11-03T19:18:40-05:00 costerou python3[4160047]: dispatch: closed, removed or disconnected
2026-11-03T19:18:41-05:00 costerou systemd[1268]: viture-xr-2298c7a18df736b9b6c4bde5b5877031.service: Main process exited, code=exited, status=2/INVALIDARGUMENT
2026-11-03T19:18:41-05:00 costerou systemd[1268]: viture-xr-2298c7a18df736b9b6c4bde5b5877031.service: Failed with result 'exit-code'.
2026-11-03T19:18:41-05:00 costerou systemd[1268]: viture-xr-2298c7a18df736b9b6c4bde5b5877031.service: Consumed 27min 225ms CPU time over 1h 11min 36.238s wall clock time.
""".splitlines()


class UnitExitTests(unittest.TestCase):
    def test_the_units_own_last_words(self):
        self.assertEqual(d.parse_unit_exit(UNIT), dict(code='exited', status='2/INVALIDARGUMENT', result='exit-code'))

    def test_the_latest_exit_wins_over_an_earlier_one(self):
        # A failed Recover reuses the run's unit name, so the journal can hold two ends.
        lines = UNIT + ["2026-11-03T19:30:00-05:00 h systemd[1]: x.service: Main process exited, code=killed, status=9/KILL",
                        "2026-11-03T19:30:00-05:00 h systemd[1]: x.service: Failed with result 'signal'."]
        self.assertEqual(d.parse_unit_exit(lines), dict(code='killed', status='9/KILL', result='signal'))

    def test_no_exit_lines(self):
        self.assertEqual(d.parse_unit_exit(['2026-11-03T20:06:06-05:00 h python3[1]: xr-workspace: run: hello']), {})


class ExitReportTests(unittest.TestCase):
    TIMING = json.dumps({'kind': 'generated_pattern_presentation_timing', 'presented': 510042, 'discarded': 1, 'abandoned': 1,
                         'submitted': 510044, 'interval_count': 510041, 'interval_mean_ns': 8421000.5, 'interval_max_ns': 91000000,
                         'observation_duration_ns': 4295000000000, 'interval_histogram': [0, 1], 'clock_id': 1})
    CAPTURE = json.dumps({'kind': 'managed_output_capture', 'theme': {'accent': [1, 2, 3]}, 'view': {'selected': 1},
                          'outputs': [{'name': 'XR-1', 'frames': 209893, 'failures': 0, 'reallocations': 1, 'path': 'dmabuf', 'width': 1920,
                                       'height': 1200, 'something_new': 'text'}],
                          'tracking': {'requested': True, 'feed': True, 'tracking': False, 'samples': 900, 'lost_events': 1, 'fov_source': 'driver'},
                          'video': {'path': '/secret.mp4'}})

    def lines(self): return [f'2026-11-03T19:18:40-05:00 h python3[1]: {self.TIMING}', f'2026-11-03T19:18:40-05:00 h python3[1]: {self.CAPTURE}']

    def test_only_the_numbers_that_say_how_it_ran_come_through(self):
        report = d.exit_metrics(self.lines())
        self.assertEqual(report['presentation']['presented'], 510042)
        self.assertEqual(report['capture'], [{'name': 'XR-1', 'frames': 209893, 'failures': 0, 'reallocations': 1, 'path': 'dmabuf'}])
        self.assertEqual(report['tracking'], {'feed': True, 'tracking': False, 'samples': 900, 'lost_events': 1})
        self.assertNotIn('secret', json.dumps(report))
        self.assertNotIn('theme', report)

    def test_the_latest_report_wins(self):
        later = json.dumps({'kind': 'generated_pattern_presentation_timing', 'presented': 7})
        self.assertEqual(d.exit_metrics(self.lines() + [f'2026-11-03T19:30:00-05:00 h python3[1]: {later}'])['presentation']['presented'], 7)

    def test_nothing_to_read(self):
        self.assertEqual(d.exit_metrics(['2026-11-03T19:18:40-05:00 h python3[1]: {not json', 'x']), {})

    def test_it_knows_a_report_line_when_it_sees_one(self):
        self.assertTrue(d.is_exit_report(self.lines()[1]))
        self.assertFalse(d.is_exit_report('2026-11-03T19:18:40-05:00 h python3[1]: dispatch: closed, removed or disconnected'))


def sig(at, kind='usb-drop', source='kernel', detail='x'): return Signal(at, kind, source, detail)


class CorrelatorTests(unittest.TestCase):
    def test_the_hubs_two_drops_are_one_incident(self):
        # The second drop came 2 to 23 s after the first on 2026-11-03; 19:18:33 and :40 is the pair that ended a run.
        c = Correlator()
        c.add(sig(100.0), RUN)
        self.assertEqual(c.due(110.0), [])
        c.add(sig(123.0), RUN)
        self.assertEqual(c.due(140.0), [])
        done = c.due(148.0)
        self.assertEqual(len(done), 1)
        self.assertEqual([s.at for s in done[0].signals], [100.0, 123.0])

    def test_a_quiet_incident_closes(self):
        c = Correlator()
        c.add(sig(100.0), RUN)
        self.assertEqual(c.due(124.0), [])
        self.assertEqual(len(c.due(125.0)), 1)
        self.assertEqual(c.due(126.0), [])

    def test_a_noisy_incident_still_closes(self):
        c = Correlator()
        for at in range(100, 170, 5):
            c.add(sig(float(at)), RUN)
            done = c.due(float(at))
            if done: break
        self.assertEqual(at, 160)
        self.assertEqual(len(done), 1)

    def test_the_run_ending_closes_it_soon_after(self):
        c = Correlator()
        c.add(sig(100.0), RUN)
        c.run_ended(RUN, 107.0, 'ended')
        self.assertEqual(c.due(110.0), [])
        done = c.due(112.0)
        self.assertEqual(done[0].ended, 'ended')

    def test_a_run_that_ends_with_nothing_wrong_makes_no_incident(self):
        c = Correlator()
        c.run_ended(RUN, 107.0, 'stopped')
        self.assertEqual(c.due(1000.0), [])

    def test_a_drop_then_a_stop_is_still_reported_as_stopped(self):
        # 2026-11-03 20:06:12 and 20:06:23: the hub dropped, Cody pressed Stop.
        c = Correlator()
        c.add(sig(100.0), RUN)
        c.run_ended(RUN, 111.0, 'stopped')
        self.assertEqual(c.due(116.0)[0].ended, 'stopped')

    def test_the_next_runs_trouble_is_its_own_incident(self):
        c = Correlator()
        c.add(sig(100.0), RUN)
        c.add(sig(101.0, 'video-lost'), 'other-run')
        done = c.due(101.0)
        self.assertEqual([e.run_id for e in done], [RUN])
        self.assertEqual([e.run_id for e in c.due(200.0)], ['other-run'])

    def test_the_most_telling_signal_names_the_incident(self):
        c = Correlator()
        for kind in ('renderer-exit', 'tracking-lost', 'usb-drop', 'video-lost'): c.add(sig(100.0, kind), RUN)
        self.assertEqual(c.due(1000.0)[0].kind, 'usb-drop')

    def test_a_crash_outranks_the_usb_drop_that_set_it_off(self):
        c = Correlator()
        for kind in ('usb-drop', 'renderer-crash'): c.add(sig(100.0, kind), RUN)
        self.assertEqual(c.due(1000.0)[0].kind, 'renderer-crash')

    def test_a_wrong_clock_in_a_log_cannot_hold_an_incident_open(self):
        c = Correlator()
        c.add(sig(5000.0), RUN, now=100.0)  # a log line stamped far from the time it was read
        self.assertEqual(c.due(124.0), [])
        self.assertEqual(len(c.due(125.0)), 1)

    def test_every_kind_has_a_rank(self):
        self.assertEqual(len(set(d.KINDS)), len(d.KINDS))
        self.assertTrue({'hyprland-crash', 'renderer-crash', 'usb-drop', 'video-lost', 'present-stall', 'capture-failed', 'tracking-lost',
                         'plugin-failed', 'watchdog', 'run-killed', 'run-failed', 'renderer-exit', 'start-failed', 'recovery-failed',
                         'controller-restart'} == set(d.KINDS))

    def test_repeats_do_not_grow_without_bound(self):
        c = Correlator()
        for at in range(10000): c.add(sig(100.0 + at / 1000, detail=f'd{at}'), RUN)
        self.assertLessEqual(len(c.due(1000.0)[0].signals), d.MAX_SIGNALS)

    def test_identical_signals_are_counted_not_listed(self):
        c = Correlator()
        for _ in range(5): c.add(sig(100.0), RUN)
        done = c.due(1000.0)[0]
        self.assertEqual(len(done.signals), 1)
        self.assertEqual(done.repeats[0], 4)


if __name__ == '__main__': unittest.main()
