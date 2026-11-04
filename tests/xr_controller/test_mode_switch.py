import unittest
from tests.xr_controller.test_incident_detect import GOOD, MODE, healthy, run, kinds

class ModeSwitchTests(unittest.TestCase):
    def test_pending_reconnect_is_not_video_loss_in_either_direction(self):
        for width, rate in ((3840, 60), (1920, 120)):
            w = healthy()
            if width == 1920:
                w.mode = w.seen_mode = (3840, 1200, 60)
            pending = dict(GOOD, immersive={'switching': True})
            signals = run(w, 2, start=20, status=pending, monitor=None, drm='disconnected')
            signals += run(w, 8, start=22, status=GOOD,
                           monitor=dict(MODE, width=width, refresh=rate))
            self.assertNotIn('video-lost', kinds(signals))

    def test_unrequested_disconnect_still_reports(self):
        self.assertIn('video-lost', kinds(run(healthy(), 2, start=20, monitor=None, drm='disconnected')))

    def test_pending_real_loss_still_reports(self):
        self.assertIn('video-lost', kinds(run(healthy(), 15, start=20,
            status=dict(GOOD, immersive={'switching': True}), monitor=None, drm='disconnected')))

    def test_r1_can_be_held_late_in_the_pending_window(self):
        w = healthy()
        pending = dict(GOOD, immersive={'switching': True})
        run(w, 25, start=20, status=pending)
        signals = run(w, 2, start=45, status=pending, monitor=None, drm='disconnected')
        signals += run(w, 8, start=47, status=GOOD, monitor=dict(MODE, width=3840, refresh=60))
        self.assertNotIn('video-lost', kinds(signals))

    def test_unsupported_new_mode_is_still_a_fault(self):
        w = healthy()
        signals = run(w, 15, start=20, status=dict(GOOD, immersive={'switching': True}),
                      monitor=dict(MODE, width=2560))
        self.assertIn('video-lost', kinds(signals))

class LiveModeSwitchCases(unittest.TestCase):
    """Curated from Nov 4's 10:12 enter and 10:27 probable exit reports.

    Reconnect sampling below is synthetic: the reports establish mode and order,
    not exact time to first new picture. USB stays present in both cases.
    """
    def test_enter_then_probable_exit_in_the_same_session(self):
        from tools.xr_controller.incident_detect import RunWatch
        from tests.xr_controller.test_incident_detect import CONSENT, RUN, obs
        w = RunWatch(RUN, CONSENT, 0)
        signals = run(w, 20)
        for start, width, hz in ((20, 3840, 60), (60, 1920, 120)):
            with self.subTest(case='10:12 enter' if width == 3840 else '10:27 probable exit'):
                before = len(signals)
                # Renderer dispatch closes first; DP-2 is reported gone one tick later.
                old_mode = dict(MODE, width=w.mode[0], refresh=w.mode[2])
                signals += w.observe(obs(start, monitor=old_mode, immersive={'switching': True}))
                for offset in (1, 2, 3):
                    signals += w.observe(obs(start + offset, monitor=None, drm='disconnected',
                        immersive={'switching': True}, status=dict(GOOD, tracking=False),
                        usb_present=True, driver_alive=True))
                mode = dict(MODE, width=width, refresh=hz)
                for offset in range(4, 12):
                    signals += w.observe(obs(start + offset, monitor=mode, immersive={'switching': False}))
                self.assertNotIn('video-lost', kinds(signals[before:]))
                self.assertNotIn('usb-drop', kinds(signals[before:]))
                self.assertEqual(w.mode, (width, 1200, hz))
                signals += run(w, 28, start=start + 12, monitor=mode)
        self.assertEqual(getattr(w, 'mode_switches', 0), 2)
