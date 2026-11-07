"""Offline eligibility only; no compositor or device access."""
import unittest
from tools.native_spike import view

class SessionEligibilityTests(unittest.TestCase):
    def test_nonfinite_refresh_is_not_eligible_for_restoration(self):
        for rate in (float('nan'), float('inf'), -float('inf'), True):
            m = dict(name='DP-2', width=1920, height=1200, refreshRate=rate, x=0, y=0, scale=1)
            self.assertIsNone(view.session_mode(m), rate)

class ModelEligibilityTests(unittest.TestCase):
    def test_pro2_initial_eligibility_requires_1080p60(self):
        from tools.native_spike.display_policy import initial_picture_mode
        for w, h, rate, want in ((1920,1080,60,True), (1920,1080,59.94,True),
                                (1920,1080,90,False), (1920,1080,120,False),
                                (1920,1200,60,False), (3840,1080,60,False)):
            with self.subTest(w=w,h=h,rate=rate):
                self.assertEqual(initial_picture_mode('pro2', w,h,rate), want)

    def test_legacy_modes_and_unknown_fail_closed(self):
        from tools.native_spike.display_policy import initial_picture_mode
        for model in ('luma-ultra', 'beast'):
            self.assertTrue(initial_picture_mode(model,1920,1200,119.999))
            self.assertFalse(initial_picture_mode(model,1920,1080,60))
        for model in ('unknown','pro',None):
            self.assertFalse(initial_picture_mode(model,1920,1080,60))
        for rate in (float('nan'),float('inf'),True,'60',None):
            self.assertFalse(initial_picture_mode('pro2',1920,1080,rate))
        for w,h in ((1920.0,1080),(1920,1080.0),(True,1080)):
            self.assertFalse(initial_picture_mode('pro2',w,h,60))
