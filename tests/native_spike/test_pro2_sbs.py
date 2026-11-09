"""Pro 2's observed L1 mode transition, with no device or compositor access."""
import unittest
from unittest.mock import Mock, patch

from tools.native_spike import hyprland, sbs
from tests.native_spike.test_sbs import Clock, monitor

NARROW = ['1920x1080@120.00Hz', '1920x1080@90.00Hz', '1920x1080@60.00Hz']
WIDE = ['3840x1080@60.00Hz']


def pro2(width=1920, hz=120, modes=NARROW):
    return monitor(width=width, height=1080, hz=hz, modes=modes)


class Display:
    def __init__(self, *script):
        self.script = list(script)
        self.writes = []

    def query(self, kind, timeout=None):
        value = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        return [value] if value is not None else []

    def apply_wide_mode(self, output, rate, *, model):
        self.writes.append(('wide', output, rate, model))

    def apply_pro2_mode(self, output, rate):
        self.writes.append(('narrow', output, rate))

    def apply_session_mode(self, output):
        raise AssertionError('Pro 2 must never enter the Luma display path')


class Pro2Sbs(unittest.TestCase):
    def button(self, *script, **kw):
        client = Display(*script)
        clock, messages = Clock(), []
        button = sbs.ButtonSbs(client, 'DP-2', messages.append, model='pro2',
                               clock=clock, sleep=clock.sleep, **kw)
        return button, client, messages

    def test_only_observed_pro2_wide_timing_is_eligible(self):
        self.assertEqual(sbs.best_wide(pro2(modes=WIDE), model='pro2'), 60)
        for modes in (NARROW, ['3840x1200@60.00Hz'], ['3840x1080@90.00Hz']):
            self.assertIsNone(sbs.best_wide(pro2(modes=modes), model='pro2'))
        self.assertIsNone(sbs.best_wide(pro2(modes=WIDE)))
        self.assertTrue(sbs.is_wide(pro2(width=3840, hz=60, modes=WIDE), model='pro2'))

    def test_scope_does_not_cross_models_or_accept_numeric_bools(self):
        expected = {'width': 3840, 'height': 1080, 'refresh_hz': [60]}
        self.assertTrue(sbs.scope_ok(expected, model='pro2'))
        self.assertFalse(sbs.scope_ok(expected))
        self.assertFalse(sbs.scope_ok(sbs.SCOPE, model='pro2'))
        self.assertFalse(sbs.scope_ok(expected, model='unknown'))
        self.assertFalse(sbs.scope_ok({**expected, 'width': 3840.0}, model='pro2'))

    def test_enter_prompts_l1_and_follows_observed_1080p60(self):
        button, client, messages = self.button(pro2(), None, pro2(hz=60, modes=WIDE))
        self.assertTrue(button.enter())
        self.assertEqual(client.writes, [('wide', 'DP-2', 60, 'pro2')])
        self.assertEqual(messages, ['Hold L1 on the glasses to switch them to 3D.'])

    def test_already_wide_needs_no_modeset_or_button(self):
        button, client, messages = self.button(pro2(width=3840, hz=60, modes=WIDE))
        self.assertTrue(button.enter())
        self.assertEqual(client.writes, [])
        self.assertEqual(messages, [])

    def test_timeout_mentions_l1_and_pro2_resolution(self):
        button, client, messages = self.button(pro2(), enter_wait=0.5)
        with self.assertRaisesRegex(sbs.SbsError, '3840x1080.*L1'):
            button.enter()
        self.assertEqual(client.writes, [])

    def test_leave_restores_observed_refresh_only_after_l1_returns_narrow(self):
        button, client, messages = self.button(pro2(), pro2(width=3840, hz=60, modes=WIDE))
        self.assertTrue(button.enter())
        button.leave()
        self.assertIn('L1', messages[-1])
        self.assertFalse(button.settle())
        client.script = [pro2(hz=60)]
        self.assertTrue(button.settle())
        self.assertEqual(client.writes, [('narrow', 'DP-2', 120)])

    def test_already_wide_session_preserves_mode_after_l1_return(self):
        button, client, _ = self.button(pro2(width=3840, hz=60, modes=WIDE))
        self.assertTrue(button.enter())
        button.leave()
        client.script = [pro2(hz=60)]
        self.assertTrue(button.settle())
        self.assertEqual(client.writes, [])

    def test_absent_original_rate_is_not_requested(self):
        button, client, _ = self.button(pro2(), pro2(width=3840, hz=60, modes=WIDE))
        self.assertTrue(button.enter())
        button.leave()
        client.script = [pro2(hz=60, modes=['1920x1080@60.00Hz'])]
        self.assertTrue(button.settle())
        self.assertEqual(client.writes, [])

    def test_manual_switch_remembers_the_narrow_rate_for_return(self):
        button, client, _ = self.button(pro2(hz=90))
        button.follow_by_hand()
        client.script = [pro2(width=3840, hz=60, modes=WIDE)]
        button.follow_by_hand()
        self.assertTrue(button.enter())
        button.leave()
        client.script = [pro2(hz=60)]
        self.assertTrue(button.settle())
        self.assertEqual(client.writes, [('narrow', 'DP-2', 90)])

    def test_direct_l1_exit_after_enter_window_is_still_a_mode_switch(self):
        button, client, _ = self.button(pro2(), pro2(width=3840, hz=60, modes=WIDE))
        self.assertTrue(button.enter())
        button.clock.sleep(sbs.EXPECT_S + 1)
        self.assertFalse(button.switching())
        client.script = [pro2(hz=60)]
        self.assertTrue(button.left_by_hand())
        self.assertTrue(button.switching())
        self.assertTrue(button.parked())
        self.assertTrue(button.settle())
        self.assertEqual(client.writes, [('narrow', 'DP-2', 120)])

    def test_mode_probe_marks_late_physical_transitions_without_extending_stable_state(self):
        button, client, _ = self.button(pro2(width=3840, hz=60, modes=WIDE))
        self.assertTrue(button.enter())
        button.clock.sleep(sbs.EXPECT_S + 1)
        client.script = [pro2(hz=60)]
        button.follow_by_hand()
        self.assertTrue(button.switching())
        button.clock.sleep(sbs.EXPECT_S + 1)
        button.follow_by_hand()
        self.assertFalse(button.switching())
        client.script = [pro2(width=3840, hz=60, modes=WIDE)]
        button.follow_by_hand()
        self.assertTrue(button.switching())


class Pro2ModeWrites(unittest.TestCase):
    def client(self, before, after=None):
        client = hyprland.HyprlandClient.__new__(hyprland.HyprlandClient)
        client.query = Mock(side_effect=[[before], [after or before]])
        client._eval = Mock()
        return client

    def test_wide_rule_and_readback_use_pro2_shape(self):
        client = self.client(pro2(hz=60, modes=WIDE), pro2(width=3840, hz=60, modes=WIDE))
        result = client.apply_wide_mode('DP-2', 60, model='pro2')
        self.assertEqual((result['width'], result['height']), (3840, 1080))
        self.assertIn('mode = "3840x1080@60"', client._eval.call_args.args[0])

    def test_unoffered_or_wrong_model_wide_mode_never_writes(self):
        for mode, rate in ((pro2(), 60), (pro2(modes=['3840x1200@60.00Hz']), 60),
                           (pro2(modes=['3840x1080@90.00Hz']), 90)):
            client = self.client(mode)
            with self.assertRaises(ValueError):
                client.apply_wide_mode('DP-2', rate, model='pro2')
            client._eval.assert_not_called()

    def test_narrow_restore_uses_offered_original_rate(self):
        client = self.client(pro2(hz=60), pro2(hz=119.999))
        result = client.apply_pro2_mode('DP-2', 120)
        self.assertEqual(result['height'], 1080)
        self.assertIn('mode = "1920x1080@120"', client._eval.call_args.args[0])

    def test_narrow_restore_refuses_wide_unoffered_and_invalid_rates(self):
        for before, rate in ((pro2(width=3840, hz=60, modes=WIDE), 120),
                             (pro2(hz=60, modes=['1920x1080@60.00Hz']), 120),
                             (pro2(), float('nan')), (pro2(), True), (pro2(), 144)):
            client = self.client(before)
            with self.assertRaises(ValueError):
                client.apply_pro2_mode('DP-2', rate)
            client._eval.assert_not_called()

    def test_wrong_height_readback_cannot_claim_success(self):
        client = self.client(pro2(modes=WIDE))
        client.query = Mock(return_value=[pro2(hz=60, modes=WIDE)])
        with patch.object(hyprland, 'DEADLINE', 0.001), self.assertRaisesRegex(ValueError, '3840x1080'):
            client.apply_wide_mode('DP-2', 60, model='pro2')


if __name__ == '__main__':
    unittest.main()
