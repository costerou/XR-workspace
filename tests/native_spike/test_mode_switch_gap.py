import unittest
from tools.mode_switch_gap import measure

class GapTests(unittest.TestCase):
    def test_new_mode_pending_switch_skips_usb_settle(self):
        self.assertEqual(measure(0.0), 1.0)

    def test_no_pending_switch_keeps_usb_settle(self):
        self.assertEqual(measure(3.0), 4.0)

    def test_r1_without_click_offers_depth_once(self):
        from tools.native_spike.sbs import ButtonSbs
        from tests.native_spike.test_sbs import monitor
        class Client:
            mode = monitor()
            def query(self, _): return [self.mode]
        c, told = Client(), []
        s = ButtonSbs(c, 'DP-2', told.append)
        s.follow_by_hand()
        c.mode = dict(c.mode, width=3840)
        s.follow_by_hand(); s.follow_by_hand()
        self.assertEqual(told, ['The glasses are in 3D. Click 3D to enable desktop depth.'])
