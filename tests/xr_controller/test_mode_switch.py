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
