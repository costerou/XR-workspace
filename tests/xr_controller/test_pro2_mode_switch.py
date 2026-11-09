"""Only a consented, observed Pro 2 SBS transition changes the expected mode."""
import unittest

from tests.xr_controller.test_incident_detect import GOOD, MODE, RUN, kinds, run
from tools.xr_controller.incident_detect import RunWatch

SCOPE = {'width': 3840, 'height': 1080, 'refresh_hz': [60]}
NARROW = dict(MODE, height=1080)


class Pro2ModeSwitchTests(unittest.TestCase):
    def watch(self, **over):
        consent = dict(output='DP-2', model='pro2', tracking='pro2-sdk', sbs_mode=SCOPE, **over)
        watch = RunWatch(RUN, consent, 0)
        run(watch, 20, monitor=NARROW)
        return watch

    def test_enter_and_each_supported_return_rate_survive_the_display_gap(self):
        for rate in (60, 90, 120):
            with self.subTest(rate=rate):
                watch = self.watch()
                pending = dict(GOOD, immersive={'switching': True})
                signals = run(watch, 2, start=20, status=pending, monitor=None, drm='disconnected')
                wide = dict(NARROW, width=3840, refresh=60)
                signals += run(watch, 16, start=22, monitor=wide)
                self.assertNotIn('video-lost', kinds(signals))
                self.assertEqual(watch.mode, (3840, 1080, 60))
                signals = run(watch, 2, start=38, status=pending, monitor=None, drm='disconnected')
                signals += run(watch, 16, start=40, monitor=dict(NARROW, refresh=rate))
                self.assertNotIn('video-lost', kinds(signals))
                self.assertEqual(watch.mode, (1920, 1080, rate))
                self.assertEqual(watch.mode_switches, 2)

    def test_unsupported_wide_or_luma_modes_are_faults(self):
        for width, height, rate in ((3840, 1080, 90), (3840, 1200, 60), (1920, 1200, 120), (1920, 1080, 240)):
            with self.subTest(mode=(width, height, rate)):
                watch = self.watch()
                signals = run(watch, 20, start=20, status=dict(GOOD, immersive={'switching': True}),
                              monitor=dict(NARROW, width=width, height=height, refresh=rate))
                self.assertIn('video-lost', kinds(signals))
                self.assertEqual(watch.mode_switches, 0)

    def test_a_pending_flag_cannot_expand_the_consented_scope(self):
        for scope in (None, {'width': 3840, 'height': 1200, 'refresh_hz': [90, 60]},
                      {'width': 3840, 'height': 1080, 'refresh_hz': [90, 60]}):
            with self.subTest(scope=scope):
                watch = self.watch()
                watch.consent['sbs_mode'] = scope
                signals = run(watch, 20, start=20, status=dict(GOOD, immersive={'switching': True}),
                              monitor=dict(NARROW, width=3840, refresh=60))
                self.assertIn('video-lost', kinds(signals))
                self.assertEqual(watch.mode_switches, 0)

    def test_an_unrequested_change_is_still_a_fault(self):
        watch = self.watch()
        signals = run(watch, 20, start=20, monitor=dict(NARROW, width=3840, refresh=60))
        self.assertIn('video-lost', kinds(signals))
        self.assertEqual(watch.mode_switches, 0)
