"""The glasses' 3D mode (tools/native_spike/sbs.py), and what the rest of a run does around it: the button is a person's, so the
tests are about what happens whatever that person does, or does not do. Nothing here touches a real output or the glasses."""
import json
from pathlib import Path
import threading
import time
import unittest
from unittest import mock

from tools.native_spike import hyprland, immersive, managed, panel_consent, sbs, sbs_probe, survive, view
from tools.native_spike.immersive import ImmersiveRun, NoDeviceSbs, SbsError

ROOT = Path(__file__).resolve().parents[2]
TWO_D = ['1920x1080@60.00Hz', '1920x1200@120.00Hz', '1920x1200@90.00Hz', '1920x1200@60.00Hz', '1920x1080@90.00Hz']
WIDE = ['3840x1200@90.00Hz', '3840x1200@60.00Hz', '3840x1080@60.00Hz']


def monitor(width=1920, height=1200, hz=120.0, modes=TWO_D, name='DP-2'):
    return {'name': name, 'id': 1 if name == 'DP-2' else 2, 'width': width, 'height': height, 'x': -3000, 'y': 9000, 'scale': 1.25, 'refreshRate': hz, 'transform': 0,
            'mirrorOf': 'none', 'disabled': False, 'dpmsStatus': True, 'availableModes': modes}


class Glasses:
    """A compositor's view of the glasses. `script`: what query answers, in turn, the last for ever; None: the output is away."""
    def __init__(self, *script):
        self.script, self.applied, self.session = list(script) or [monitor()], [], 0

    def query(self, kind, timeout=None):
        now = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        return [m for m in [now, monitor(name='DP-1')] if m is not None]

    def apply_wide_mode(self, output, rate):
        self.applied.append((output, rate))

    def apply_session_mode(self, output):
        self.session += 1


class Clock:
    def __init__(self): self.t = 0.0
    def __call__(self): return self.t
    def sleep(self, s): self.t += s


def button(client, **kw):
    clock, told = Clock(), []
    out = sbs.ButtonSbs(client, 'DP-2', told.append, clock=clock, sleep=clock.sleep, **kw)
    out.told, out.test_clock = told, clock
    return out


class ModesTests(unittest.TestCase):
    def test_the_2d_edid_offers_no_wide_mode(self):
        self.assertIsNone(sbs.best_wide(monitor()))

    def test_90_is_preferred_and_60_will_do(self):
        self.assertEqual(sbs.best_wide(monitor(modes=WIDE)), 90.0)
        self.assertEqual(sbs.best_wide(monitor(modes=['3840x1200@60.00Hz'])), 60.0)

    def test_rates_viture_does_not_list_are_not_offered(self):
        self.assertIsNone(sbs.best_wide(monitor(modes=['3840x1200@120.00Hz', '3840x1200@30.00Hz'])))

    def test_3840x1080_is_not_this_mode(self):
        self.assertIsNone(sbs.best_wide(monitor(modes=['3840x1080@60.00Hz', '3840x1080@90.00Hz'])))

    def test_nonsense_is_not_a_mode(self):
        self.assertEqual(sbs.offered(monitor(modes=['garbage', 3, None, '3840x1200@90.00Hz'])), [(3840, 1200, 90.0)])
        self.assertEqual(sbs.offered(None), [])
        self.assertEqual(sbs.offered({'availableModes': None}), [])

    def test_the_rule_has_a_fixed_shape(self):
        self.assertEqual(sbs.wide_mode_lua('DP-2', -3072, 9000, 1.25, 90.0), 'hl.monitor({ output = "DP-2", mode = "3840x1200@90", position = "-3072x9000", scale = 1.25 })')

    def test_the_rule_refuses_what_it_was_not_built_for(self):
        for bad in (('XR-1', 0, 0, 1, 90.0), ('DP-2"; os.exit() --', 0, 0, 1, 90.0), ('DP-2', '0', 0, 1, 90.0), ('DP-2', 0, 0, 9, 90.0), ('DP-2', 0, 0, 1, 120.0)):
            self.assertRaises(ValueError, sbs.wide_mode_lua, *bad)

    def test_nothing_here_can_reach_usb(self):
        # The way in is a person's thumb: no module this one uses opens a device or the driver's command channel.
        source = (ROOT / 'tools/native_spike/sbs.py').read_text()
        code = source.split('"""', 2)[2]
        for word in ('usb', 'hidraw', '/dev/', 'luma', 'tracking', 'subprocess', 'ioctl'):
            self.assertNotIn(word, code.lower())


class EnterTests(unittest.TestCase):
    def test_no_press_is_a_clear_failure_with_nothing_changed(self):
        g = Glasses()
        b = button(g, enter_wait=2)
        with self.assertRaisesRegex(SbsError, 'R1'): b.enter()
        self.assertEqual(g.applied, [])
        self.assertEqual(b.told, [sbs.PROMPT_ON])  # asked once, however long it waits

    def test_the_mode_follows_the_press(self):
        g = Glasses(monitor(), monitor(), monitor(modes=WIDE))
        b = button(g)
        self.assertTrue(b.enter())
        self.assertEqual(g.applied, [('DP-2', 90.0)])
        self.assertEqual(b.told, [sbs.PROMPT_ON])

    def test_the_press_may_take_the_output_away_for_a_while(self):
        g = Glasses(monitor(), None, None, None, monitor(width=3840, height=1200, hz=60.0, modes=WIDE))
        self.assertTrue(button(g).enter())
        self.assertEqual(g.applied, [('DP-2', 90.0)])

    def test_a_broken_compositor_reply_is_waiting_not_a_crash(self):
        class Broken(Glasses):
            def query(self, kind, timeout=None): raise OSError('gone')
        with self.assertRaisesRegex(SbsError, 'R1'): button(Broken(), enter_wait=1).enter()

    def test_glasses_already_side_by_side_need_no_press(self):
        g = Glasses(monitor(modes=WIDE))
        b = button(g)
        self.assertTrue(b.enter())
        self.assertEqual(b.told, [])
        self.assertEqual(g.applied, [('DP-2', 90.0)])

    def test_already_driven_wide_changes_nothing(self):
        g = Glasses(monitor(width=3840, hz=90.0, modes=WIDE))
        self.assertTrue(button(g).enter())
        self.assertEqual(g.applied, [])

    def test_calling_it_off_while_waiting(self):
        g = Glasses()
        b = button(g)
        calls = []
        self.assertFalse(b.enter(lambda: calls.append(1) or len(calls) > 3))
        self.assertEqual(g.applied, [])

    def test_only_the_wrong_mode_says_which(self):
        g = Glasses(monitor(), monitor(modes=['3840x1080@60.00Hz']))
        with self.assertRaisesRegex(SbsError, '3840x1080'): button(g, enter_wait=2).enter()
        self.assertEqual(g.applied, [])

    def test_a_mode_that_will_not_take_is_an_sbs_error(self):
        class Refuses(Glasses):
            def apply_wide_mode(self, output, rate): raise ValueError('not observed')
        with self.assertRaisesRegex(SbsError, 'not observed'): button(Refuses(monitor(modes=WIDE))).enter()

    def test_it_counts_as_expected_while_waiting_and_for_a_while_after(self):
        g = Glasses(monitor(), monitor(modes=WIDE))
        b = button(g)
        self.assertFalse(b.switching())
        b.enter()
        self.assertTrue(b.switching())
        b.test_clock.t += sbs.EXPECT_S + 1
        self.assertFalse(b.switching())


class ParkTests(unittest.TestCase):
    """The output's place depends on its width, and the wide mode doubles that."""
    class Client(Glasses):
        def __init__(self, *script):
            super().__init__(*script)
            self.order = []
        def apply_wide_mode(self, output, rate): self.order.append('wide')
        def apply_session_mode(self, output): self.order.append('session')
        def park_presentation(self, output): self.order.append('park')

    def test_after_going_wide(self):
        c = self.Client(monitor(modes=WIDE))
        button(c).enter()
        self.assertEqual(c.order, ['wide', 'park'])

    def test_after_coming_back(self):
        c = self.Client(monitor(width=3840, hz=90.0, modes=WIDE), monitor())
        b = button(c)
        b.leave()
        self.assertTrue(b.settle())
        self.assertEqual(c.order, ['session', 'park'])


class LeaveTests(unittest.TestCase):
    def test_it_asks_once_and_never_waits(self):
        g = Glasses(monitor(width=3840, hz=90.0, modes=WIDE))
        b = button(g)
        start = time.monotonic()
        b.leave(); b.leave()
        self.assertLess(time.monotonic() - start, 0.1)
        self.assertEqual(b.told, [sbs.PROMPT_OFF])
        self.assertTrue(b.parked() and b.switching())

    def test_glasses_in_2d_have_nothing_to_leave(self):
        b = button(Glasses())
        b.leave()
        self.assertEqual(b.told, [])
        self.assertFalse(b.parked())

    def test_120_comes_back_when_the_button_is_held(self):
        g = Glasses(monitor(width=3840, hz=90.0, modes=WIDE), monitor(width=3840, hz=90.0, modes=WIDE), monitor())
        b = button(g)
        b.leave()
        self.assertFalse(b.settle())
        self.assertEqual(g.session, 0)
        self.assertTrue(b.settle())
        self.assertEqual(g.session, 1)
        self.assertFalse(b.parked())

    def test_the_output_being_away_is_not_going_back(self):
        g = Glasses(monitor(width=3840, hz=90.0, modes=WIDE), None)
        b = button(g)
        b.leave()
        self.assertFalse(b.settle())
        self.assertTrue(b.parked())

    def test_turning_3d_on_again_before_the_press_needs_no_press(self):
        g = Glasses(monitor(width=3840, hz=90.0, modes=WIDE))
        b = button(g)
        b.leave()
        self.assertTrue(b.enter())
        self.assertFalse(b.parked())
        self.assertEqual(b.told, [sbs.PROMPT_OFF])

    def test_held_while_3d_is_up(self):
        self.assertTrue(button(Glasses(monitor())).left_by_hand())
        self.assertFalse(button(Glasses(monitor(width=3840, hz=90.0, modes=WIDE))).left_by_hand())
        self.assertFalse(button(Glasses(None)).left_by_hand())


class Engine:
    def __init__(self): self.up, self.dead, self.binary = False, False, 'x'
    def start(self, desktops, cancelled=None): self.up = True; return True
    def set_desktops(self, desktops): pass
    def alive(self): return self.up and not self.dead
    def stop(self): self.up = False


def wait_for(check, what='it', seconds=3):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if check(): return
        time.sleep(0.005)
    raise AssertionError(f'{what} did not happen')


class RunTests(unittest.TestCase):
    """The worker and a real ButtonSbs, on real threads with short waits: on, off, and back out of everything that can go wrong."""
    def make(self, *script, **kw):
        g = Glasses(*script)
        told = []
        s = sbs.ButtonSbs(g, 'DP-2', told.append, **kw)
        run, engine = ImmersiveRun(Engine(), s), None
        self.addCleanup(run.close, 0.2)
        return run, g, s, told

    def events(self, run, n):
        got = []
        def check():
            got.extend(run.take_events())
            return len(got) >= n
        wait_for(check, 'events')
        return got

    def test_enter_then_exit_then_120_again(self):
        run, g, s, told = self.make(monitor(), monitor(modes=WIDE), monitor(width=3840, hz=90.0, modes=WIDE))
        with mock.patch.object(sbs, 'POLL_S', 0.01):
            run.request(['XR-1'])
            self.assertEqual(self.events(run, 1), [('active', ['XR-1'])])
        self.assertEqual(run.phase, 'on')
        self.assertEqual(g.applied, [('DP-2', 90.0)])
        run.request([])
        self.assertEqual(self.events(run, 1), [('off',)])
        self.assertFalse(run.engine.up)
        self.assertEqual(told, [sbs.PROMPT_ON, sbs.PROMPT_OFF])
        g.script = [monitor()]
        wait_for(lambda: g.session == 1, 'the session mode')
        self.assertFalse(s.parked())

    def test_no_press_rolls_back_to_2d_with_the_engine_stopped(self):
        run, g, s, told = self.make(monitor(), enter_wait=0.05)
        with mock.patch.object(sbs, 'POLL_S', 0.005):
            run.request(['XR-1'])
            events = self.events(run, 1)
        self.assertEqual(events[0][:2], ('failed', 'sbs'))
        self.assertIn('R1', events[0][2])
        self.assertFalse(run.engine.up)
        self.assertEqual(g.applied, [])
        self.assertEqual(run.phase, 'off')

    def test_a_mode_that_will_not_take_rolls_back_and_asks_for_the_button(self):
        class Refuses(Glasses):
            def apply_wide_mode(self, output, rate): raise ValueError('not observed')
        g = Refuses(monitor(modes=WIDE))
        told = []
        run = ImmersiveRun(Engine(), sbs.ButtonSbs(g, 'DP-2', told.append))
        self.addCleanup(run.close, 0.2)
        run.request(['XR-1'])
        self.assertEqual(self.events(run, 1)[0][:2], ('failed', 'sbs'))
        self.assertFalse(run.engine.up)
        self.assertEqual(told, [sbs.PROMPT_OFF])  # the glasses are wide and nothing can draw on them: the way back

    def test_clicking_off_during_the_wait_stops_the_engine_quietly(self):
        run, g, s, told = self.make(monitor())
        with mock.patch.object(sbs, 'POLL_S', 0.005):
            run.request(['XR-1'])
            wait_for(lambda: run.phase == 'glasses', 'the wait')
            run.request([])
            wait_for(lambda: not run.engine.up, 'the engine stopping')
        self.assertEqual(run.phase, 'off')
        self.assertEqual(g.applied, [])
        self.assertEqual([e for e in run.take_events() if e[0] == 'failed'], [])

    def test_r1_held_while_3d_is_up_falls_back_and_says_why(self):
        run, g, s, told = self.make(monitor(width=3840, hz=90.0, modes=WIDE))
        run.request(['XR-1'])
        self.assertEqual(self.events(run, 1), [('active', ['XR-1'])])
        g.script = [monitor()]
        events = self.events(run, 1)
        self.assertEqual(events[0][:2], ('failed', 'sbs'))
        self.assertIn('R1', events[0][2])
        self.assertFalse(run.engine.up)

    def test_the_engine_dying_during_the_wait_is_a_failure_not_a_blank_3d(self):
        run, g, s, told = self.make(monitor(), monitor(), monitor(modes=WIDE))
        run.engine.start = lambda desktops, cancelled=None: setattr(run.engine, 'up', True) or setattr(run.engine, 'dead', True) or True
        with mock.patch.object(sbs, 'POLL_S', 0.005):
            run.request(['XR-1'])
            self.assertEqual(self.events(run, 1)[0][:2], ('failed', 'died'))

    def test_a_run_without_the_scope_never_asks_for_the_button(self):
        run = ImmersiveRun(Engine(), immersive.make_sbs(Glasses(), 'DP-2', {}))
        self.addCleanup(run.close, 0.2)
        self.assertIsInstance(run.sbs, NoDeviceSbs)
        run.request(['XR-1'])
        self.assertEqual(self.events(run, 1)[0][:2], ('failed', 'sbs'))
        self.assertFalse(run.switching())


class ScopeTests(unittest.TestCase):
    def consent(self, **kw):
        return {'schema': 1, 'gate': panel_consent.PANEL_GATE, 'run_id': 'a' * 32, 'instance': 'i', 'output': 'DP-2', 'recovery_output': 'DP-1',
                'model': 'luma-ultra', 'allow': panel_consent.CAPTURE_ALLOW, 'capture': panel_consent.CAPTURE_SCOPE, 'tracking': False,
                'mode_switch': dict(panel_consent.LUMA_MODE), 'input': False, 'sdk': False, 'desktops': [dict(width=1920, height=1080, scale=1)],
                'plugin': {'path': '/p', 'sha256': 'a' * 64}, 'renderer': {'path': '/r', 'sha256': 'b' * 64}, **kw}

    def test_a_run_without_it_reads_as_it_always_did(self):
        self.assertNotIn(sbs.SCOPE_KEY, panel_consent._validate(self.consent()) or self.consent())

    def test_the_scope_is_exact(self):
        panel_consent._validate(self.consent(sbs_mode=dict(sbs.SCOPE)))
        for bad in ({**sbs.SCOPE, 'height': 1080}, {**sbs.SCOPE, 'refresh_hz': [120]}, True, {}):
            self.assertRaises(ValueError, panel_consent._validate, self.consent(sbs_mode=bad))

    def test_it_needs_the_glasses_and_the_mode_switch(self):
        self.assertRaises(ValueError, panel_consent._validate, self.consent(sbs_mode=dict(sbs.SCOPE), mode_switch=False))
        self.assertRaises(ValueError, panel_consent._validate, self.consent(sbs_mode=dict(sbs.SCOPE), model='beast', mode_switch=False))

    def test_make_sbs_takes_only_the_exact_scope(self):
        self.assertIsInstance(immersive.make_sbs(), NoDeviceSbs)
        self.assertIsInstance(immersive.make_sbs(Glasses(), 'DP-2', {'sbs_mode': {**sbs.SCOPE, 'width': 1920}}), NoDeviceSbs)
        self.assertIsInstance(immersive.make_sbs(Glasses(), 'DP-2', {'sbs_mode': dict(sbs.SCOPE)}), sbs.ButtonSbs)


class RestoreTests(unittest.TestCase):
    """A press looks like the glasses unplugged, so a hold's restore has to put back the mode they came back in."""
    class Client(Glasses):
        def __init__(self, *script):
            super().__init__(*script)
            self.parked = 0
        def park_presentation(self, output): self.parked += 1

    def restore(self, client, **consent):
        return managed._restore_output(client, {'mode_switch': dict(panel_consent.LUMA_MODE), **consent}, 'DP-2')

    def test_back_in_2d_gets_the_session_mode(self):
        c = self.Client(monitor())
        self.restore(c, sbs_mode=dict(sbs.SCOPE))()
        self.assertEqual((c.session, c.applied, c.parked), (1, [], 1))

    def test_back_side_by_side_gets_the_wide_mode(self):
        c = self.Client(monitor(modes=WIDE))
        self.restore(c, sbs_mode=dict(sbs.SCOPE))()
        self.assertEqual((c.session, c.applied, c.parked), (0, [('DP-2', 90.0)], 1))

    def test_a_run_without_the_scope_never_asks_for_a_wide_mode(self):
        c = self.Client(monitor(modes=WIDE))
        self.restore(c)()
        self.assertEqual((c.session, c.applied), (1, []))


class HoldTests(unittest.TestCase):
    def test_the_output_back_already_still_holds_if_it_was_expected(self):
        hold = dict(output_ok=True, usb_before=(1, 2), usb_now=(1, 2))
        self.assertFalse(survive.should_hold(2, None, **hold))  # a crash with the glasses fine is still a crash...
        self.assertTrue(survive.should_hold(2, None, **hold, expected=True))  # ...unless the mode was changing

    def test_being_expected_does_not_turn_a_stop_into_a_hold(self):
        self.assertFalse(survive.should_hold(0, 'interrupted', output_ok=False, usb_before=None, usb_now=None, expected=True))
        self.assertFalse(survive.should_hold(0, None, output_ok=True, usb_before=None, usb_now=None, expected=True))

    def test_keep_renderer_holds_quietly_through_a_press(self):
        told, codes = [], [2, 0]
        restored = []
        holds = survive.Holds(); holds.revive = lambda: True
        report = {}
        class Client:
            def query(self, kind, timeout=None): return [monitor(), monitor(name='DP-1')]
        with mock.patch.object(survive, 'wait_for_glasses', lambda ready, restore, serve: restored.append(1) or True), \
                mock.patch.object(survive, 'usb_identity', lambda: (1, 2)):
            code = managed.keep_renderer(lambda: codes.pop(0), Client(), 'DP-2', report, None, lambda: None, holds, tell=told.append, switching=lambda: True)
        self.assertEqual(code, 0)
        self.assertEqual(restored, [1])
        self.assertEqual(told, [])  # no "dropped off USB" for something Cody just did

    def test_a_real_drop_still_says_so(self):
        told, codes = [], [2, 0]
        holds = survive.Holds(); holds.revive = lambda: True
        class Client:
            def query(self, kind, timeout=None): return [monitor(name='DP-1')]
        with mock.patch.object(survive, 'wait_for_glasses', lambda ready, restore, serve: True), mock.patch.object(survive, 'usb_identity', lambda: (1, 2)):
            managed.keep_renderer(lambda: codes.pop(0), Client(), 'DP-2', {}, None, lambda: None, holds, tell=told.append)
        self.assertIn('dropped off USB', told[0])


class WideModeRequestTests(unittest.TestCase):
    def client(self, *readbacks):
        c = hyprland.HyprlandClient.__new__(hyprland.HyprlandClient)
        c.evals, script = [], list(readbacks)
        c._eval = c.evals.append
        c.query = lambda kind, timeout=None: [script.pop(0) if len(script) > 1 else script[0], monitor(name='DP-1')]
        return c

    def test_it_asks_for_the_mode_where_the_output_is_and_reads_it_back(self):
        c = self.client(monitor(modes=WIDE), monitor(width=3840, hz=90.0, modes=WIDE))
        found = c.apply_wide_mode('DP-2', 90.0)
        self.assertEqual(c.evals, [sbs.wide_mode_lua('DP-2', -3000, 9000, 1.25, 90.0)])
        self.assertEqual(found['width'], 3840)

    def test_it_raises_unless_it_reads_back(self):
        c = self.client(monitor(modes=WIDE))
        with mock.patch.object(hyprland, 'DEADLINE', 0.05), self.assertRaises(ValueError): c.apply_wide_mode('DP-2', 90.0)

    def test_it_is_the_only_new_way_to_touch_a_mode(self):
        # Lua hl.monitor through _eval, as apply_session_mode: no plugin call, no function hook, no new keyword.
        src = (ROOT / 'tools/native_spike/sbs.py').read_text()
        self.assertNotIn('createFunctionHook', src)
        self.assertNotIn('dispatch', src)


class ProbeTests(unittest.TestCase):
    """tools/native_spike/sbs_probe.py: the first thing to run on real glasses, and read-only."""
    def test_what_each_outcome_means(self):
        two_d_kernel, two_d = ('1920x1080', '1920x1200'), tuple(TWO_D)
        self.assertEqual(sbs_probe.verdict([two_d_kernel, ('3840x1200',)], [two_d, tuple(WIDE)]), ('ready', {'mode': '3840x1200@90'}))
        self.assertEqual(sbs_probe.verdict([two_d_kernel], [two_d, ('3840x1200@60.00Hz',)]), ('ready', {'mode': '3840x1200@60'}))
        self.assertEqual(sbs_probe.verdict([two_d_kernel], [two_d, ('3840x1200@120.00Hz',)])[0], 'wrong_rate')
        self.assertEqual(sbs_probe.verdict([two_d_kernel, ('3840x1200',)], [two_d])[0], 'hyprland_blind')
        self.assertEqual(sbs_probe.verdict([two_d_kernel, ('3840x1080',)], [two_d]), ('narrow', {'modes': '3840x1080'}))
        self.assertEqual(sbs_probe.verdict([two_d_kernel], [two_d])[0], 'silent')
        self.assertEqual(sbs_probe.verdict([()], [None, ()])[0], 'silent')

    def test_every_finding_can_be_said(self):
        for finding, fill in (('ready', {'mode': 'x'}), ('wrong_rate', {'rates': '1'}), ('hyprland_blind', {}), ('narrow', {'modes': 'x'}), ('silent', {})):
            self.assertTrue(sbs_probe.FINDINGS[finding].format(**fill))

    def test_the_kernel_side_reads_files_and_nothing_else(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / 'card1-DP-2'
            root.mkdir()
            (root / 'status').write_text('connected\n'); (root / 'modes').write_text('3840x1200\n1920x1200\n'); (root / 'edid').write_bytes(b'\0\xff')
            self.assertEqual(sbs_probe.connector('DP-2', Path(d)), root)
            self.assertEqual(sbs_probe.kernel_state(root), ('connected', ('3840x1200', '1920x1200'), b'\0\xff'))
            self.assertEqual(sbs_probe.kernel_state(root / 'gone'), ('gone', (), b''))
            self.assertIsNone(sbs_probe.connector('DP-9', Path(d)))

    def test_hyprland_is_asked_one_read_only_question(self):
        asked = []
        class Done:
            stdout = json.dumps([{'name': 'DP-2', 'availableModes': TWO_D}, {'name': 'DP-1'}])
        def run(argv, **kw):
            asked.append(argv); return Done()
        self.assertEqual(sbs_probe.hyprland_modes('DP-2', run), tuple(TWO_D))
        self.assertEqual(asked, [['hyprctl', 'monitors', 'all', '-j']])
        self.assertIsNone(sbs_probe.hyprland_modes('DP-9', run))
        class Bad:
            stdout = 'not json'
        self.assertEqual(sbs_probe.hyprland_modes('DP-2', lambda *a, **k: Bad()), ())

    def test_nothing_in_it_writes_to_a_device(self):
        code = (ROOT / 'tools/native_spike/sbs_probe.py').read_text().split('"""', 2)[2]
        for word in ('keyword', 'dispatch', 'hl.monitor', '/dev/', 'usb', 'ioctl'):
            self.assertNotIn(word, code)
        self.assertEqual(code.count('write_bytes'), 1)  # the EDID copy in the state folder, and that is all


if __name__ == '__main__':
    unittest.main()
