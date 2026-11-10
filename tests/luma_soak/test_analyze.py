from datetime import datetime
from pathlib import Path
import unittest

from tools.luma_soak.analyze import EXIT_DROP, EXIT_INCONCLUSIVE, EXIT_PASS, Policy, analyze, records_from_logs

FIXTURES = Path(__file__).parent / 'fixtures'
T0 = 1793700000.0
HUB = {'id': '1a86:8091', 'product': 'USB HUB', 'devnum': 3}
GLASSES = {'id': '35ca:1104', 'product': 'VITURE Luma Ultra XR GLASSES', 'devnum': 4}
CAMERA = {'id': '0c45:636b', 'product': 'USB 2.0 Camera', 'devnum': 5}


def kernel(t, message):
    return {'src': 'kernel', 't': t, 'line': datetime.fromtimestamp(t).astimezone().isoformat() + f' host kernel: {message}'}


def driver(t, line):
    return {'src': 'driver', 't': t, 'line': line}


def start(**extra):
    return {'src': 'meta', 'kind': 'start', 't': T0, 'tree': {'5-2': HUB, '5-2.1': GLASSES, '5-2.2': CAMERA}, 'glasses_at_start': True, **extra}


def stop(duration=1800.0, **extra):
    return {'src': 'meta', 'kind': 'stop', 't': T0 + duration, 'reason': 'duration', 'duration_s': duration, 'audit': 'ok', 'glasses_at_end': True, 'suspended_s': 0, **extra}


def alive(duration=1800, feed=True, drive=True):
    out = []
    for second in range(1, duration):
        out.append({'src': 'cpu', 't': T0 + second, 'driver_pids': [7] if drive else [], 'system_pct': 10.0, 'core_max_pct': 30.0, 'driver_pct': 3.0, 'run_delay_ms': 1.0})
        out.append({'src': 'feed', 't': T0 + second, 'kind': 'stats', 'advances': 60 if feed else 0, 'window_s': 1.0})
    return out


def drop_at(t):
    return [kernel(t, 'usb 5-2: USB disconnect, device number 3'), kernel(t + 0.01, 'usb 5-2.1: USB disconnect, device number 4')]


class VerdictTests(unittest.TestCase):
    def verdict(self, *records, **policy):
        return analyze([*records], Policy(**policy))['verdict']

    def test_a_clean_proven_soak_passes(self):
        result = self.verdict(start(), *alive(), stop())
        self.assertEqual((result['result'], result['exit']), ('PASS', EXIT_PASS))

    def test_one_drop_fails(self):
        result = self.verdict(start(), *alive(), *drop_at(T0 + 100), stop())
        self.assertEqual((result['result'], result['exit']), ('FAIL', EXIT_DROP))

    def test_budget_is_honoured(self):
        self.assertEqual(self.verdict(start(), *alive(), *drop_at(T0 + 100), stop(), budget=1)['exit'], EXIT_PASS)

    def test_a_drop_outranks_a_soak_that_was_cut_short(self):
        self.assertEqual(self.verdict(start(), *drop_at(T0 + 5), stop(duration=10))['exit'], EXIT_DROP)

    def assert_unproven(self, result, word):
        self.assertEqual(result['exit'], EXIT_INCONCLUSIVE, result)
        self.assertTrue(any(word in reason for reason in result['reasons']), result)

    def test_short_soak_proves_nothing(self):
        self.assert_unproven(self.verdict(start(), *alive(600), stop(600)), 'required')

    def test_glasses_absent_at_start_proves_nothing(self):
        self.assert_unproven(self.verdict(start(glasses_at_start=False), *alive(), stop()), 'started')

    def test_glasses_gone_at_end_proves_nothing(self):
        self.assert_unproven(self.verdict(start(), *alive(), stop(glasses_at_end=False)), 'ended')

    def test_no_driver_proves_nothing_because_a_plain_display_never_drops(self):
        self.assert_unproven(self.verdict(start(), *alive(drive=False), stop()), 'driver')

    def test_frozen_feed_proves_nothing(self):
        self.assert_unproven(self.verdict(start(), *alive(feed=False), stop()), 'feed')

    def test_no_data_at_all_proves_nothing(self):
        self.assert_unproven(self.verdict(start(), stop()), 'driver')

    def test_requirements_can_be_lifted_on_purpose(self):
        self.assertEqual(self.verdict(start(), *alive(drive=False, feed=False), stop(), require_driver=False, require_feed=False)['exit'], EXIT_PASS)

    def test_failed_audit_proves_nothing(self):
        self.assert_unproven(self.verdict(start(), *alive(), stop(audit='failed: journalctl status 1')), 'audit')

    def test_a_killed_monitor_with_no_stop_record_proves_nothing(self):
        self.assert_unproven(self.verdict(start(), *alive()), 'audit')

    def test_suspend_proves_nothing(self):
        self.assert_unproven(self.verdict(start(), *alive(), stop(suspended_s=40)), 'suspended')

    def test_unidentified_disconnect_proves_nothing(self):
        self.assert_unproven(self.verdict(start(), *alive(), kernel(T0 + 9, 'usb 3-1: USB disconnect, device number 2'), stop()), 'identified')

    def test_replay_can_fail_but_never_pass(self):
        lines = (FIXTURES / 'kernel_boot_2026-11-01_03.log').read_text().splitlines()
        self.assertEqual(analyze(records_from_logs(lines))['verdict']['exit'], EXIT_DROP)
        self.assert_unproven(analyze(records_from_logs(lines[:50]))['verdict'], 'replayed')

    def test_camera_drop_warns_and_only_strict_fails(self):
        camera = kernel(T0 + 9, 'usb 5-2.2: USB disconnect, device number 5')
        self.assertEqual(self.verdict(start(), *alive(), camera, stop())['exit'], EXIT_PASS)
        self.assertEqual(self.verdict(start(), *alive(), camera, stop(), strict=True)['exit'], EXIT_DROP)

    def test_kernel_warning_only_strict_fails(self):
        reset = kernel(T0 + 9, 'usb 5-2.1: reset high-speed USB device number 4 using xhci_hcd')
        self.assertEqual(self.verdict(start(), *alive(), reset, stop())['exit'], EXIT_PASS)
        self.assertEqual(self.verdict(start(), *alive(), reset, stop(), strict=True)['exit'], EXIT_DROP)

    def test_a_restarted_driver_is_counted_and_only_strict_fails(self):
        restarted = {'src': 'cpu', 't': T0 + 9, 'driver_pids': [8]}
        report = analyze([start(), *alive(), restarted, stop()], Policy())
        self.assertEqual((report['coverage']['driver_processes'], report['verdict']['exit']), (2, EXIT_PASS))
        self.assertEqual(self.verdict(start(), *alive(), restarted, stop(), strict=True)['exit'], EXIT_DROP)

    def test_feed_gap_limit(self):
        gap = {'src': 'feed', 't': T0 + 9, 'kind': 'gap', 'start': T0 + 8.9, 'ms': 90.0}
        self.assertEqual(self.verdict(start(), *alive(), gap, stop())['exit'], EXIT_PASS)
        self.assertEqual(self.verdict(start(), *alive(), gap, stop(), max_feed_gap_ms=50.0)['exit'], EXIT_DROP)


class WitnessTests(unittest.TestCase):
    def test_sysfs_alone_is_a_drop(self):
        change = {'src': 'sysfs', 't': T0 + 50, 'kind': 'renumbered', 'path': '5-2.1', 'id': '35ca:1104', 'devnum': 9, 'was': 4}
        report = analyze([start(), *alive(), change, stop()], Policy())
        self.assertEqual((report['drops'], report['sysfs_only_drops'], report['verdict']['exit']), (1, 1, EXIT_DROP))

    def test_sysfs_seeing_a_kernel_drop_does_not_count_it_twice(self):
        change = {'src': 'sysfs', 't': T0 + 50.7, 'kind': 'gone', 'path': '5-2.1', 'id': '35ca:1104', 'devnum': 4}
        self.assertEqual(analyze([start(), *alive(), *drop_at(T0 + 50), change, stop()], Policy())['drops'], 1)

    def test_hub_and_glasses_leaving_one_poll_are_one_drop(self):
        changes = [{'src': 'sysfs', 't': T0 + 50, 'kind': 'gone', 'path': p, 'id': i, 'devnum': 1} for p, i in (('5-2', '1a86:8091'), ('5-2.1', '35ca:1104'))]
        self.assertEqual(analyze([start(), *alive(), *changes, stop()], Policy())['drops'], 1)

    def test_audit_lines_repeat_without_doubling(self):
        lines = drop_at(T0 + 50)
        self.assertEqual(analyze([start(), *alive(), *lines, *[dict(r, audit=True) for r in lines], stop()], Policy())['drops'], 1)

    def test_audit_alone_finds_a_drop_the_stream_missed(self):
        records = [start(), *alive(), {'src': 'meta', 'kind': 'blind', 't': T0 + 2, 'why': 'kernel log stream ended, status 1'}, *[dict(r, audit=True) for r in drop_at(T0 + 50)], stop()]
        report = analyze(records, Policy())
        self.assertEqual((report['drops'], report['verdict']['exit']), (1, EXIT_DROP))
        self.assertEqual(report['watch']['blind'], ['kernel log stream ended, status 1'])

    def test_lines_outside_the_soak_do_not_count(self):
        self.assertEqual(analyze([start(), *alive(), *drop_at(T0 - 60), *drop_at(T0 + 3600), stop()], Policy())['drops'], 0)


class CorrelationTests(unittest.TestCase):
    def setUp(self):
        self.t = T0 + 100
        self.records = [start(), *alive(), *drop_at(self.t)]

    def drop(self, *extra, **policy):
        return analyze([*self.records, *extra, stop()], Policy(**policy))['drop_details'][0]

    def test_commands_in_the_window(self):
        drop = self.drop(driver(self.t - 1, 'event=cmd_sent cmd=0x07'), driver(self.t - 2, 'event=cmd_sent cmd=0x07'),
                         driver(self.t - 1, 'event=cmd_failed cmd=0x07 rc=-7'), driver(self.t - 1, 'event=cmd_sent cmd=0x15'))
        self.assertEqual(drop['driver']['commands_sent'], {'0x07': 2, '0x15': 1})
        self.assertEqual(drop['driver']['commands_failed'], {'0x07': 1})
        self.assertEqual(drop['driver']['usb_return_codes'], {'-7': 1})
        self.assertTrue(any('command failure' in hint for hint in drop['hints']))

    def test_what_came_before_or_after_the_window_is_left_out(self):
        drop = self.drop(driver(self.t - 5.1, 'event=cmd_sent cmd=0x07'), driver(self.t + 0.1, 'event=cmd_sent cmd=0x07'))
        self.assertEqual(drop['driver']['commands_sent'], {})

    def test_window_size_is_a_setting(self):
        self.assertEqual(self.drop(driver(self.t - 8, 'event=cmd_sent cmd=0x07'), window_s=10.0)['driver']['commands_sent'], {'0x07': 1})

    def test_failure_after_the_drop_is_a_result_not_a_cause(self):
        self.assertEqual(self.drop(driver(self.t + 2, 'event=cmd_failed cmd=0x07 rc=-7'))['driver']['commands_failed'], {})

    def test_undated_line_that_may_be_after_the_drop_is_flagged_not_counted(self):
        drop = self.drop({'src': 'driver', 't': self.t - 3, 't_hi': self.t + 4, 'line': '[E][libglasses] Failed to send USB command - MsgID: 0x0007, error code: -1'})
        self.assertEqual((drop['driver']['commands_failed'], drop['driver']['uncertain_lines']), ({}, 1))

    def test_sample_gaps(self):
        self.assertEqual(self.drop(driver(self.t - 1, 'event=sample_gap ms=38.2'))['driver']['sample_gaps_ms'], [38.2])

    def test_feed_stall_running_into_the_drop(self):
        drop = self.drop({'src': 'feed', 't': self.t + 3, 'kind': 'gap', 'start': self.t - 0.25, 'ms': 3250.0})
        self.assertEqual(drop['feed']['stalled_before_drop_ms'], 250.0)

    def test_feed_gap_that_ended_before_the_drop(self):
        drop = self.drop({'src': 'feed', 't': self.t - 1, 'kind': 'gap', 'start': self.t - 2, 'ms': 80.0})
        self.assertEqual((drop['feed']['gaps'], drop['feed']['stalled_before_drop_ms']), (1, None))

    def test_cpu_load_and_run_delay(self):
        drop = self.drop({'src': 'cpu', 't': self.t - 0.5, 'driver_pids': [7], 'system_pct': 95.0, 'core_max_pct': 100.0, 'driver_pct': 40.0, 'run_delay_ms': 75.0})
        self.assertEqual((drop['cpu']['system_pct_max'], drop['cpu']['run_delay_ms_max']), (95.0, 75.0))
        self.assertTrue(any('CPU busy' in hint for hint in drop['hints']))
        self.assertTrue(any('waited' in hint for hint in drop['hints']))

    def test_kernel_warning_before_the_drop(self):
        self.assertEqual(len(self.drop(kernel(self.t - 1, 'usb 5-2.1: reset high-speed USB device number 4 using xhci_hcd'))['kernel_trouble']), 1)

    def test_recovery_time(self):
        back = [kernel(self.t + 2, 'usb 5-2.1: new high-speed USB device number 9 using xhci_hcd'),
                kernel(self.t + 2.1, 'usb 5-2.1: New USB device found, idVendor=35ca, idProduct=1104, bcdDevice= 1.00')]
        self.assertEqual(self.drop(*back)['recovered_s'], 2.1)

    def test_no_return_before_the_next_drop_is_no_recovery(self):
        report = analyze([*self.records, *drop_at(self.t + 3), stop()], Policy())
        self.assertIsNone(report['drop_details'][0]['recovered_s'])
        self.assertEqual(report['never_recovered'], 2)

    def test_bursts(self):
        report = analyze([*self.records, *drop_at(self.t + 10), *drop_at(self.t + 500), stop()], Policy())
        self.assertEqual((report['drops'], report['bursts']), (3, 2))


class SignalTests(unittest.TestCase):
    def test_a_signal_that_is_always_there_shows_in_the_baseline(self):
        t = T0 + 100
        failures = [driver(T0 + s, 'event=cmd_failed cmd=0x07 rc=-7') for s in range(1, 1800, 5)]
        signals = analyze([start(), *alive(), *drop_at(t), *failures, stop()], Policy())['signals']
        row = signals['command_failed']
        self.assertEqual(row['before_drops'], 1)
        self.assertGreater(row['quiet_windows'] / row['quiet_total'], 0.9)

    def test_windows_after_a_drop_are_not_quiet(self):
        t = T0 + 100
        signals = analyze([start(), *alive(), *drop_at(t), driver(t + 3, 'event=cmd_failed cmd=0x07 rc=-7'), stop()], Policy())['signals']
        self.assertEqual(signals['command_failed']['quiet_windows'], 0)

    def test_what_nobody_watched_is_not_reported(self):
        lines = (FIXTURES / 'kernel_boot_2026-11-01_03.log').read_text().splitlines()
        self.assertEqual(set(analyze(records_from_logs(lines))['signals']), {'command_failed', 'kernel_trouble'})

    def test_undated_failures_count_on_both_sides_alike(self):
        """The baseline holds only windows where the driver was logging, and takes undated lines by the same test as the drop windows."""
        kernel_lines = (FIXTURES / 'kernel_boot_2026-11-01_03.log').read_text().splitlines()
        driver_lines = (FIXTURES / 'driver_2026-11-03.log').read_text().splitlines()
        row = analyze(records_from_logs(kernel_lines, driver_lines))['signals']['command_failed']
        self.assertGreater(row['quiet_total'], 0)
        self.assertLess(row['quiet_total'], 5000)   # hours with no driver running are left out


class ReplayTests(unittest.TestCase):
    def test_undated_lines_are_bracketed_by_their_neighbours(self):
        records = records_from_logs([], ['2026-11-03 23:00:01.000 Waiting for glasses', '[E][libglasses] x', '2026-11-03 23:00:09.000 Waiting for glasses', '[E][libglasses] y'])
        self.assertEqual(records[1]['t_hi'], records[2]['t'])
        self.assertEqual(records[3]['t_hi'], float('inf'))

    def test_undated_lines_before_any_date_are_dropped(self):
        self.assertEqual(records_from_logs([], ['[E][libglasses] x']), [])

    def test_real_day(self):
        kernel_lines = (FIXTURES / 'kernel_boot_2026-11-01_03.log').read_text().splitlines()
        report = analyze(records_from_logs(kernel_lines))
        self.assertEqual((report['drops'], report['hub_drops'], report['glasses_only_drops']), (54, 54, 0))
        self.assertEqual(len(report['unattributed']), 0)
        self.assertGreaterEqual(len(report['kernel_trouble']), 3)


class CppDriverTests(unittest.TestCase):
    """The shipped C++ xr-luma-driver's own lines, as the 3-hour baseline soak will see them."""

    def setUp(self):
        self.t = T0 + 100
        self.records = [start(), *alive(), *drop_at(self.t)]

    def test_a_stall_before_the_drop_is_a_sample_gap_and_a_disconnect_has_no_return_code(self):
        report = analyze([*self.records, driver(self.t - 1, 'luma: stale ms=250 at=0'), driver(self.t - 0.5, 'luma: disconnected reason=silent at=0'), stop()])
        window = report['drop_details'][0]['driver']
        self.assertEqual((window['sample_gaps_ms'], window['usb_return_codes']), ([250.0], {}))

    def test_driver_reconnects_are_summed_over_the_soak(self):
        lines = [driver(self.t + 0.1, 'luma: disconnected reason=gone at=0'), driver(self.t + 1, 'luma: retry attempt=1 reason=gone at=0'),
                 driver(self.t + 4.3, 'luma: reconnected gap_ms=4200 calibration=kept at=0'), driver(T0 + 900, 'luma: stale ms=300 at=0'),
                 driver(T0 + 901, 'luma: reconnected gap_ms=1000 calibration=kept at=0')]
        events = analyze([*self.records, *lines, stop()])['driver_events']
        self.assertEqual(events, {'disconnects': 1, 'retries': 1, 'stale': 1, 'reconnected': 2,
                                  'reconnect_gap_ms': {'count': 2, 'avg': 2600.0, 'max': 4200.0}})

    def test_no_driver_lines_means_no_driver_events(self):
        self.assertIsNone(analyze([*self.records, stop()])['driver_events'])

    def test_a_saved_cpp_log_is_dated_by_its_own_clock(self):
        records = records_from_logs([], ['luma: connected at=1793700000.500', 'luma: stale ms=250 at=1793700010.000'])
        self.assertEqual([r['t'] for r in records], [1793700000.5, 1793700010.0])
        self.assertNotIn('t_hi', records[0])
