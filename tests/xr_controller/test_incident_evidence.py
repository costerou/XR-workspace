"""Offline timelines: factual USB evidence, ordering, and later run outcomes."""
import json
import unittest

from tests.xr_controller.test_incident_monitor import Case, CLEAN, RUN, OTHER
from tests.xr_controller.test_incident_report import episode, evidence, scrubber
from tools.xr_controller.incident_report import build_bundle


class EvidenceTests(unittest.TestCase):
    def test_usb_disconnect_is_not_a_hardware_root_cause(self):
        b = build_bundle(episode('usb-drop'), evidence(), 1010, scrubber())
        self.assertEqual(b['cause']['confidence'], 'low')
        self.assertIn('undetermined', b['cause']['text'])
        self.assertEqual(b['observations']['usb_disconnect'], 'confirmed')

    def test_driver_silence_does_not_confirm_bus_disconnect(self):
        e = episode('usb-drop')
        from tools.xr_controller.incident_detect import Signal
        e.signals = [Signal(1000, 'usb-drop', 'driver-log', 'driver lost samples')]
        b = build_bundle(e, evidence(), 1010, scrubber())
        self.assertIn('observations', b)
        self.assertEqual(b['observations']['usb_disconnect'], 'unconfirmed')
        self.assertEqual(b['cause']['confidence'], 'low')

    def test_usb_and_video_order_is_timestamp_order_not_kind_priority(self):
        for kinds, order in [(('video-lost', 'usb-drop'), 'video-first'),
                             (('usb-drop', 'video-lost'), 'usb-first')]:
            with self.subTest(order=order):
                e = episode(*kinds)
                e.signals.reverse()  # arrival order may differ from event time
                b = build_bundle(e, evidence(), 1010, scrubber())
                self.assertEqual([t['kind'] for t in b['timeline']], list(kinds))
                self.assertIn('observations', b)
                self.assertEqual(b['observations']['link_order'], order)
                self.assertEqual(b['cause']['confidence'], 'low')


class LifecycleTests(Case):
    def test_nov4_clusters_then_timeout_amend_existing_report(self):
        self.session()
        first = self.now
        for offset in (0, 6, 15, 25, 30):
            self.tick(seconds=int(first + offset - self.now))
            self.host.drop(self.now)
            self.tick()
        self.tick(seconds=int(first + 61 - self.now))
        path = self.reports()[0]
        self.host.results[RUN] = dict(CLEAN, stop_reason='tracking-timeout')
        self.idle(30)
        b = self.only()
        self.assertEqual(self.reports(), [path])
        self.assertEqual(b['session'], 'ended')
        self.assertFalse(b['session_kept_alive'])
        self.assertEqual(b['renderer']['stop_reason'], 'tracking-timeout')
        self.assertNotIn('kept running', path.with_suffix('.md').read_text())
        self.assertEqual(len(self.handed), 1)
        self.assertEqual(len(self.toasts), 1)

    def test_suppressed_cluster_keeps_timestamp_and_updates_on_stop_after_restart(self):
        self.session()
        self.host.drop(self.now)
        self.tick(seconds=30)
        first = self.only()['first_signal_at']
        self.host.drop(self.now, 70)
        self.tick(seconds=30)
        b = self.only()
        self.assertEqual(len([t for t in b['timeline'] if t['source'] == 'kernel']), 2)
        self.fresh()
        self.tick('stopping', stop_requested=RUN)
        self.host.results[RUN] = dict(CLEAN, stop_reason='interrupted')
        self.idle(30)
        b = self.only()
        self.assertEqual(b['session'], 'stopped')
        self.assertEqual(b['first_signal_at'], first)
        self.assertEqual(len(self.handed), 1)

    def test_all_reports_in_run_get_terminal_outcome(self):
        self.session()
        self.host.drop(self.now)
        self.tick(seconds=30)
        self.now += 700
        self.host.drop(self.now)
        self.tick(seconds=30)
        self.assertEqual(len(self.reports()), 2)
        self.host.results[RUN] = dict(CLEAN, renderer_returncode=1)
        self.idle(30)
        self.assertEqual(len(self.reports()), 2)
        for path in self.reports():
            self.assertEqual(json.loads(path.read_text())['session'], 'ended')

    def test_clean_end_without_stop_evidence_is_not_called_user_stop(self):
        self.session()
        self.host.drop(self.now)
        self.tick(seconds=30)
        self.host.results[RUN] = CLEAN
        self.idle(30)
        self.assertEqual(self.only()['session'], 'ended')

    def test_new_run_end_cannot_amend_previous_run_report(self):
        self.session()
        self.host.drop(self.now)
        self.tick(seconds=30)
        self.host.results[RUN] = CLEAN
        self.idle(10)
        old = self.reports()[0]
        before = old.read_bytes()
        self.session(run_id=OTHER)
        self.host.results[OTHER] = dict(CLEAN, run_id=OTHER, renderer_returncode=1)
        self.idle(30)
        self.assertEqual(old.read_bytes(), before)
        self.assertEqual(len(self.reports()), 2)

    def test_restart_after_run_ended_updates_saved_association(self):
        self.session()
        self.host.drop(self.now)
        self.tick(seconds=30)
        self.fresh()
        self.host.results[RUN] = dict(CLEAN, stop_reason='tracking-timeout')
        self.idle(30)
        self.assertEqual(self.only()['session'], 'ended')
        self.assertEqual(self.only()['renderer']['stop_reason'], 'tracking-timeout')

    def test_recorded_timeout_is_not_relabelled_by_later_stop_request(self):
        self.session()
        self.host.drop(self.now)
        self.tick(seconds=30)
        self.tick('stopping', stop_requested=RUN)
        self.host.results[RUN] = dict(CLEAN, stop_reason='tracking-timeout')
        self.idle(30)
        self.assertEqual(self.only()['session'], 'ended')
        self.assertEqual(self.only()['renderer']['stop_reason'], 'tracking-timeout')
