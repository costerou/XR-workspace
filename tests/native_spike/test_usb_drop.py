"""A USB drop of the glasses: the session outlives it, gives up only after a limit, and video loss still ends it.

The renderer's half (the held view, the hint, the resume) is in spike/tests/pose_test.cpp and the capture_e2e_feed_drop tests,
which run the real renderer against a simulated feed. Here: the supervisor and the driver's health check, with the feed's
timeline as the renderer reports it, in real seconds of a fake clock.
"""
import json
from pathlib import Path
import socket
import struct
import tempfile
import unittest
from unittest.mock import patch

from tools.native_spike import tracking
from tools.native_spike import supervisor
from tools.native_spike.supervisor import Supervisor, _launch


class Child:
    """A renderer that lives until it is told otherwise."""
    def __init__(self): self.rc, self.terminated, self.killed = None, 0, 0
    def poll(self): return self.rc
    def terminate(self): self.terminated += 1; self.rc = -15
    def kill(self): self.killed += 1; self.rc = -9


def status(tracking_now): return (json.dumps({'presented_hz': 120.0, 'tracking': tracking_now}) + '\n').encode()


class Session:
    """The supervisor, its heartbeats and the renderer's once-a-second status, on a fake clock."""
    def __init__(self, timeout=60.0):
        self.child, self.recovered = Child(), 0
        self.watch = Supervisor(self.child, self.recover, now=0.0, tracking_timeout=timeout)
        self.now = 0.0

    def recover(self): self.recovered += 1; return 'recovered'

    def run(self, seconds, tracking_now):
        for _ in range(int(seconds)):
            self.now += 1
            self.watch.heartbeat(self.now)
            self.watch.status_bytes(status(tracking_now), now=self.now)
            self.watch.tick(self.now)


class TrackingDropTests(unittest.TestCase):
    def test_twenty_seconds_and_two_disconnects_leave_the_session_running(self):
        s = Session()
        s.run(600, True)
        for gone, back in ((6, 0.4), (14, 0)):  # the feed stops, a moment of life, stops again
            s.run(gone, False)
            if back: s.run(1, False)
        s.run(30, True)
        self.assertFalse(s.watch.finished)
        self.assertEqual(s.child.terminated, 0)
        self.assertIsNone(s.watch.stop_reason)

    def test_drops_in_a_row_each_get_the_whole_limit(self):
        # 18 of 53 drops came within 30 s of the one before: the limit is per outage, not per session.
        s = Session()
        s.run(10, True)
        for _ in range(5):
            s.run(50, False)
            s.run(5, True)
        self.assertFalse(s.watch.finished)

    def test_a_feed_gone_past_the_limit_ends_the_session_cleanly(self):
        s = Session(timeout=60)
        s.run(10, True)
        s.run(59, False)
        self.assertFalse(s.watch.finished)
        s.run(2, False)
        self.assertEqual(s.child.terminated, 1)
        self.assertEqual(s.watch.stop_reason, 'tracking-timeout')
        self.assertTrue(s.watch.finished)
        self.assertEqual(s.recovered, 1)  # windows back on the monitor, as any end
        self.assertEqual(s.child.killed, 0)

    def test_the_limit_is_configurable(self):
        s = Session(timeout=5)
        s.run(3, True)
        s.run(7, False)
        self.assertEqual(s.watch.stop_reason, 'tracking-timeout')

    def test_video_loss_during_a_drop_still_ends_the_session(self):
        s = Session()
        s.run(10, True)
        s.run(5, False)
        s.child.rc = 1  # the renderer's output went away: "dispatch: closed, removed or disconnected"
        s.watch.tick(s.now + 1)
        self.assertTrue(s.watch.finished)
        self.assertEqual(s.recovered, 1)
        self.assertIsNone(s.watch.stop_reason)  # its own exit code tells that story, as today

    def test_tracking_that_never_started_is_not_a_drop(self):
        s = Session(timeout=5)  # a run whose feed never came up keeps the desktops it had: nothing was lost
        s.run(60, False)
        self.assertFalse(s.watch.finished)

    def test_without_head_tracking_nothing_is_watched(self):
        child, watch = Child(), None
        watch = Supervisor(child, lambda: None, now=0.0)
        watch.status_bytes(status(True), now=1.0)
        watch.status_bytes(status(False), now=2.0)
        watch.heartbeat(1000.0)
        watch.tick(1000.0)
        self.assertFalse(watch.finished)

    def test_a_person_stopping_during_a_drop_keeps_their_reason(self):
        s = Session()
        s.run(10, True)
        s.run(5, False)
        s.watch.disconnect(s.now, 'interrupted')
        s.run(100, False)
        s.watch.tick(s.now)
        self.assertEqual(s.watch.stop_reason, 'interrupted')


class LaunchTests(unittest.TestCase):
    """A real run gets the limit only with head tracking on, and from the config."""
    def launched(self, **kwargs):
        ours, wayland = socket.socketpair()
        self.addCleanup(ours.close)
        class Client: _peer = (*struct.unpack('3i', wayland.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)), 'x')
        class Finished:
            def poll(self): return 0
        seen = {}
        class Watch(Supervisor):
            def __init__(self, *args, **kw): seen.update(kw); super().__init__(*args, **kw)
        with patch.object(supervisor, 'Supervisor', Watch), patch.object(supervisor, 'lost_timeout', return_value=45.0):
            _launch(Client, 'run', 'session', 'DP-2', wayland, None, popen=lambda argv, **_: Finished(), capture=['XR-1'], **kwargs)
        return seen['tracking_timeout']

    def test_a_tracked_run_has_the_configured_limit(self):
        self.assertEqual(self.launched(pose_feed=True), 45.0)

    def test_an_untracked_run_has_none(self):
        self.assertIsNone(self.launched(pose_feed=False))


class LostTimeoutConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'tracking.json'
        p = patch.object(tracking, 'RECOVERY_CONFIG', self.path); p.start(); self.addCleanup(p.stop)

    def test_sixty_seconds_unless_told_otherwise(self):
        self.assertEqual(tracking.lost_timeout(), 60.0)

    def test_the_file_sets_it(self):
        self.path.write_text('{"lost_timeout_s": 90}')
        self.assertEqual(tracking.lost_timeout(), 90.0)

    def test_a_bad_value_never_costs_a_session(self):
        for text in ('not json', '[]', '{"lost_timeout_s": "soon"}', '{"lost_timeout_s": true}', '{"lost_timeout_s": 0}',
                     '{"lost_timeout_s": -5}', '{"lost_timeout_s": 1e999}', '{"lost_timeout_s": 90, "other": 1}'):
            with self.subTest(text=text):
                self.path.write_text(text)
                self.assertEqual(tracking.lost_timeout(), 60.0)


class DriverReconnectsItselfTests(unittest.TestCase):
    """A start just after a drop finds the driver reconnecting. It must be left to finish: 18 of 53 drops came within 30 s of
    the one before, so a stop and start here lands on glasses that are about to drop again."""
    healthy = dict(running=True, network_isolated=True, calibrated=True, driver=tracking.XRDRIVER, binary=tracking.DRIVER)
    reconnecting = dict(healthy, calibrated=False)

    def ready(self, states):
        states = iter(states)
        times = iter(range(0, 10000))
        with patch.object(tracking, 'status', side_effect=lambda runtime: next(states)), \
                patch.object(tracking, 'start', return_value=self.healthy) as start, patch.object(tracking, 'stop') as stop, \
                patch.object(tracking.time, 'monotonic', side_effect=lambda: float(next(times))), patch.object(tracking.time, 'sleep'):
            result = tracking.ensure_ready('rt')
        return result, start, stop

    def test_a_driver_that_recalibrates_by_itself_is_left_alone(self):
        result, start, stop = self.ready([self.reconnecting] * 5 + [self.healthy])
        self.assertTrue(result['calibrated'])
        start.assert_not_called(); stop.assert_not_called()

    def test_a_driver_that_never_comes_back_is_restarted_once(self):
        _, start, stop = self.ready([self.reconnecting] * 1000)
        self.assertEqual((start.call_count, stop.call_count), (1, 1))

    def test_the_wrong_driver_is_never_waited_for(self):
        # After a run on the other choice: reconnecting or not, it is not the one asked for.
        for other in (dict(self.reconnecting, binary='/3dof/xrDriver'), dict(self.reconnecting, driver=tracking.LUMA, binary='/pin/xr-luma-driver')):
            with self.subTest(other['binary']):
                result, start, stop = self.ready([other])
                self.assertEqual((start.call_count, stop.call_count), (1, 1))

    def test_a_driver_that_died_while_waiting_is_started(self):
        _, start, stop = self.ready([self.reconnecting, dict(running=False)])
        self.assertEqual(start.call_count, 1)
        stop.assert_not_called()

    def test_a_leaky_driver_is_never_waited_for(self):
        _, start, stop = self.ready([dict(self.reconnecting, network_isolated=False)])
        self.assertEqual((start.call_count, stop.call_count), (1, 1))


if __name__ == '__main__':
    unittest.main()
