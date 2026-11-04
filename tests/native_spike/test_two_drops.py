"""survive-usb-drop and usb-drop-fix together: the real Supervisor inside the real keep_renderer, and the drops as they come.

Drops come in pairs: the first takes the tracking, the second, 2 to 28 s later, usually takes the video too. The first half
is the supervisor's (hold, and give up after the limit), the second is keep_renderer's. Each has tests of its own in
test_usb_drop.py and test_survive.py; here one run goes through both, on a fake clock. The real renderer's half is ctest
capture_e2e_feed_drop and capture_e2e_feed_gone_video_loss.
"""
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

from tools.native_spike import managed, survive, tracking
from tools.native_spike.supervisor import Supervisor

GLASSES, RECONNECTED, AGAIN = (5, 4), (5, 9), (5, 12)
LIMIT = 60.0


class Clock:
    def __init__(self): self.now = 0.0
    def __call__(self): return self.now


class Child:
    """A renderer that lives until it is told otherwise."""
    def __init__(self): self.rc = None
    def poll(self): return self.rc
    def terminate(self): self.rc = -15
    def kill(self): self.rc = -9


class Run:
    """One run: every entry of `script` is one renderer, as steps the world takes while it runs.

    A step is (seconds, tracking), or a dict changing the world: {'usb': ...}, {'video': False}, {'exit': code}, {'stop': True}. A renderer
    whose script ends is left running until something ends it; the last one exits clean, as Stop would.
    """
    def __init__(self, *script, back=True, after_hold=None):
        self.script, self.back, self.after_hold = list(script), back, after_hold or {}
        self.clock, self.usb, self.video = Clock(), GLASSES, True
        self.report, self.renderers, self.told, self.restores, self.recovered = {}, [], [], 0, 0
        self.holds = survive.Holds(self.clock); self.holds.revive = lambda: True

    def query(self, kind):
        return [{'name': 'DP-1', 'disabled': False}] + ([{'name': 'DP-2', 'disabled': False}] if self.video else [])

    def launch(self):
        self.report.clear()
        steps = self.script.pop(0)
        child = Child()
        watch = Supervisor(child, self.recover, now=self.clock.now, tracking_timeout=LIMIT)
        record = {'tracking': [], 'watch': watch}; self.renderers.append(record)
        for step in steps:
            if watch.finished: break
            if isinstance(step, dict):
                if 'usb' in step: self.usb = step['usb']
                if 'video' in step: self.video = step['video']
                if 'exit' in step: child.rc = step['exit']; watch.tick(self.clock.now)
                if 'stop' in step: watch.disconnect(self.clock.now, 'interrupted')
                continue
            seconds, tracked = step
            for _ in range(int(seconds)):
                self.clock.now += 1
                watch.heartbeat(self.clock.now)
                watch.status_bytes((json.dumps({'presented_hz': 120.0, 'tracking': tracked}) + '\n').encode(), now=self.clock.now)
                watch.tick(self.clock.now)
                if watch.finished: break
        if not watch.finished and watch.stop_reason is None: child.rc = 0; watch.tick(self.clock.now)
        while not watch.finished:
            self.clock.now += 0.1; watch.tick(self.clock.now)
        self.report['stop_reason'] = watch.stop_reason
        return child.rc

    def recover(self): self.recovered += 1; return 'recovered'
    def restore(self): self.restores += 1

    def go(self):
        def serve(control, seconds):
            if self.back: self.usb, self.video = self.after_hold.get('usb', RECONNECTED), True
            self.clock.now += seconds
        with patch.object(managed, 'serve_without_renderer', serve), patch.object(survive, 'usb_identity', lambda *_: self.usb), \
             patch.object(survive, 'monitors_of', lambda client: client.query('monitors')), \
             patch.object(survive, 'time', types.SimpleNamespace(monotonic=self.clock)):
            return managed.keep_renderer(self.launch, self, 'DP-2', self.report, None, self.restore, self.holds, tell=self.told.append)


PAIR = [(10, True), {'usb': None}, (5, False), {'usb': RECONNECTED}, (3, False), {'usb': None, 'video': False}, {'exit': 1}]


class DropPairTests(unittest.TestCase):
    def test_the_second_drop_of_a_pair_takes_the_video_and_the_run_lives(self):
        run = Run(PAIR, [(30, True)])
        self.assertEqual(run.go(), 0)
        self.assertEqual(len(run.renderers), 2)
        self.assertEqual((run.holds.survived, run.restores), (1, 1))
        self.assertIsNone(run.renderers[0]['watch'].stop_reason)  # the output took it: the supervisor stopped nothing
        self.assertEqual(run.told[0], 'The glasses dropped off USB. Your desktops are safe; XR comes back by itself.')
        self.assertEqual(run.told[-1], 'The glasses are back. XR picks up where it was.')

    def test_a_tracking_only_drop_never_starts_a_second_renderer(self):
        run = Run([(10, True), {'usb': None}, (20, False), {'usb': RECONNECTED}, (10, True)])
        self.assertEqual(run.go(), 0)
        self.assertEqual((len(run.renderers), run.holds.record, run.told), (1, [], []))

    def test_time_held_before_the_video_went_does_not_count_against_the_new_renderer(self):
        # 50 s lost, the video goes, then a new outage of 50 s. Added up that is past the limit; each one alone is not.
        before = [(10, True), {'usb': None}, (50, False), {'usb': None, 'video': False}, {'exit': 1}]
        after = [(10, True), {'usb': None}, (50, False), {'usb': AGAIN}, (10, True)]
        run = Run(before, after)
        self.assertEqual(run.go(), 0)
        self.assertEqual(len(run.renderers), 2)
        self.assertEqual([r['watch'].stop_reason for r in run.renderers], [None, None])

    def test_the_limit_still_ends_a_run_with_the_video_up(self):
        # USB gone or renumbered, the output fine: the run must end as the limit promises, not wait out a hold.
        for usb, name in ((None, 'glasses gone'), (RECONNECTED, 'glasses back, no calibration')):
            with self.subTest(name):
                run = Run([(10, True), {'usb': usb}, (LIMIT + 5, False)], [(1, True)])
                self.assertNotEqual(run.go(), 0)
                self.assertEqual(run.renderers[0]['watch'].stop_reason, 'tracking-timeout')
                self.assertEqual((len(run.renderers), run.holds.record, run.told), (1, [], []))

    def test_the_limit_applies_again_after_a_pair(self):
        run = Run(PAIR, [(10, True), {'usb': None}, (LIMIT + 5, False)])
        self.assertNotEqual(run.go(), 0)
        self.assertEqual(run.renderers[1]['watch'].stop_reason, 'tracking-timeout')
        self.assertEqual(run.holds.survived, 1)

    def test_a_new_renderer_with_no_tracking_at_all_keeps_the_session(self):
        # The driver never came back: a still picture, not the end. Stop still works.
        run = Run(PAIR, [(LIMIT + 60, False)])
        self.assertEqual(run.go(), 0)
        self.assertIsNone(run.renderers[1]['watch'].stop_reason)

    def test_two_pairs_in_a_row(self):
        run = Run(PAIR, PAIR, [(30, True)])
        self.assertEqual(run.go(), 0)
        self.assertEqual((len(run.renderers), run.holds.survived), (3, 2))

    def test_a_pair_where_the_glasses_never_return_ends_the_run(self):
        run = Run(PAIR, back=False)
        self.assertEqual(run.go(), 1)
        self.assertEqual(len(run.renderers), 1)
        self.assertEqual(run.holds.record[0]['resumed'], False)
        self.assertIn('did not come back', run.told[-1])
        self.assertEqual(run.restores, 0)

    def test_stop_during_the_tracking_hold_is_a_stop(self):
        # With the USB number changed: a person's Stop still ends the run.
        run = Run([(10, True), {'usb': RECONNECTED}, (5, False), {'stop': True}], [(1, True)])
        self.assertNotEqual(run.go(), 0)
        self.assertEqual((len(run.renderers), run.report['stop_reason'], run.told), (1, 'interrupted', []))


class SameDriverAfterAVideoDropTests(unittest.TestCase):
    """The driver died with the video: the one the run had comes back, by the real revive_tracking and restart_same."""
    def setUp(self):
        usb = patch.object(tracking, 'SYS_USB', Path('/nonexistent/offline-usb'))
        usb.start(); self.addCleanup(usb.stop)
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.runtime = Path(tmp.name); self.state = self.runtime / 'state.json'
        for patcher in (patch.object(tracking, '_state', lambda rt: self.state), patch.object(tracking, 'status', lambda rt: {'running': False})):
            patcher.start(); self.addCleanup(patcher.stop)

    def revived(self, **record):
        self.state.write_text(json.dumps({'pid': 1, 'start_tick': '7', **record}))
        with patch.object(tracking, 'start', return_value={'running': True}) as start:
            self.assertIs(survive.revive_tracking(self.runtime, tracking), True)
        return start.call_args.kwargs

    def test_luma(self):
        self.assertEqual(self.revived(driver=tracking.LUMA, binary='/pin/xr-luma-driver')['luma'], '/pin/xr-luma-driver')

    def test_3dof(self):
        how = self.revived(driver=tracking.XRDRIVER, binary='/3dof/xrDriver')
        self.assertIsNone(how['luma']); self.assertEqual(how['binary'], '/3dof/xrDriver')

    def test_a_fallback_stays_on_the_driver_it_fell_back_to(self):
        how = self.revived(driver=tracking.XRDRIVER, binary=tracking.DRIVER)
        self.assertIsNone(how['luma']); self.assertEqual(how['binary'], tracking.DRIVER)

    def test_a_driver_someone_stopped_stays_stopped_and_the_run_goes_on(self):
        with patch.object(tracking, 'start') as start:
            self.assertIs(survive.revive_tracking(self.runtime, tracking), False)
        start.assert_not_called()


if __name__ == '__main__':
    unittest.main()
