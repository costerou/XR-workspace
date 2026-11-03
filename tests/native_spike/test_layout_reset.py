"""Ctrl+Alt+R (view.RESET_OP, remember-layout-reset-bind, 2026-11-03): every desktop back to the default three flat
1920x1080 desktops at 100%, live: an open panel collapses, a maximized desktop returns to the overview (its follow /
locked choice stays), the saved setup is replaced by the default at once, every desktop eases flat and level over
view.RESET_ANIM_S, then one desktops.RESET plan reshapes, removes or adds desktops, and a toast confirms. The bind is a
row of the central bind table (view.BINDS), like every other run bind.

Offline: test_desktops' fake broker and real journal, a supervisor stand-in, the real overview pointer (its `roll`
frames), temporary profiles. Nothing here talks to a compositor."""
import json
from pathlib import Path
import re
import tempfile
import unittest

from tools.native_spike import bind_check, desktops as dk, free_pointer as fp, geometry as g, profiles, send, view, xr_control
from tools.native_spike.ipc import decode_control
from test_desktops import Run, SupervisorStandIn, frames, ops

ROOT = Path(__file__).resolve().parents[2]
D169 = dict(g.DEFAULT_DESKTOP)
WIDE = {'width': 2560, 'height': 1080, 'scale': 1.0}
SHARP = {'width': 2560, 'height': 1440, 'scale': 1.25}
FLAT = dict(g.DEFAULT_PLACEMENT)
PLACED = [{'x': -450, 'y': 30, 'distance': 80, 'curvature': 40}, {'x': 0, 'y': -120, 'distance': 140, 'curvature': 0},
          {'x': 1800, 'y': 0, 'distance': 100, 'curvature': 120}, {'x': 600, 'y': 900, 'distance': 10, 'curvature': 7}]
ROLLS = [250, -1200, 0, 9000]
MODEL = 'beast'


class BindTests(unittest.TestCase):
    def test_it_is_a_row_of_the_central_bind_table(self):
        self.assertEqual(view.RESET_OP, 'layout_reset')
        self.assertIn(('CTRL + ALT + R', view.RESET_OP), view.BINDS)  # what the bind panel lists and rebinds
        self.assertIn(view.RESET_OP, view.HELP_ACTIONS)
        self.assertIn(('run', 'CTRL + ALT + R', view.RESET_OP), bind_check.run_chords())
        lua = view.install_binds_lua('/usr/bin/python3', '/opt/x/xr_control.py', '/run/user/1000/c.sock', 'run', 'session')
        self.assertIn('hl.bind("CTRL + ALT + R", hl.dsp.exec_cmd(c .. "layout_reset"))', lua)
        self.assertNotIn('layout_reset', view.install_binds_lua('/usr/bin/python3', '/opt/x/xr_control.py',
                                                                 '/run/user/1000/c.sock', 'run', 'session', typing=True))
        self.assertIn(view.RESET_OP, xr_control.OPS)  # the bind helper sends it
        frame = (json.dumps({'schema': 1, 'request_id': 3, 'run_id': 'run', 'session': 's', 'op': view.RESET_OP,
                             'payload': {}}) + '\n').encode()
        self.assertEqual(decode_control(frame)['op'], view.RESET_OP)  # and the run takes it
        self.assertNotIn(view.RESET_OP, view.USER_OPS)  # not a panel capability (no panel button)

    def test_the_chord_is_free(self):
        mine = bind_check.normalize('CTRL + ALT + R')
        others = [c for _, c, op in bind_check.run_chords() if op != view.RESET_OP]
        self.assertNotIn(mine, [bind_check.normalize(c) for c in others])  # the panel's bare R is another chord
        self.assertIn(bind_check.normalize('R'), [bind_check.normalize(c) for c, _ in view.MENU_BINDS])
        # Omarchy's own Ctrl+Alt binds (default/hypr/bindings), where installed; bind_check.py checks the live config.
        omarchy = Path('/usr/share/omarchy/default/hypr/bindings')
        if omarchy.is_dir():
            chords = [m.group(1) for f in omarchy.glob('*.lua') for m in re.finditer(r'bind\w*\("([^"]+)"', f.read_text())]
            chords = [c for c in chords if not c.rstrip().endswith('+')]  # "SUPER + " .. key: built at load time
            self.assertTrue(chords)
            self.assertNotIn(mine, [bind_check.normalize(c) for c in chords])
            self.assertIn(bind_check.normalize('CTRL + ALT + TAB'), [bind_check.normalize(c) for c in chords])  # parsed

    def test_the_help_card_names_it_on_the_recenter_row(self):
        rows = dict(view.help_rows())
        self.assertEqual(rows['Ctrl+Alt+Space / R'], 'Recenter / reset to 3 flat desktops')
        self.assertEqual(len(view.help_rows()), 14)  # the card keeps its row count (help_card_test: roomiest text)
        src = (ROOT / 'spike/tests/help_card_test.cpp').read_text()
        self.assertIn('{"Ctrl+Alt+Space / R", "Recenter / reset to 3 flat desktops"}', src)

    def test_the_docs_list_it(self):
        readme = (ROOT / 'README.md').read_text()
        self.assertIn('| Ctrl+Alt+R |', readme.split('### Key cheat sheet', 1)[1].split('\n## ', 1)[0])
        technical = (ROOT / 'docs/technical.md').read_text()
        self.assertIn('| Ctrl+Alt+R | run |', technical)


class PlanTests(unittest.TestCase):
    def test_four_desktops_lose_the_highest_and_the_rest_go_default_and_flat(self):
        specs = Run(self, [WIDE, SHARP, D169, WIDE]).specs
        p = dk.plan(specs, PLACED, dk.RESET, 0)
        self.assertEqual(p.remove['name'], 'XR-4')
        self.assertEqual([(s['name'], s['width'], s['height'], s['scale'], s['x']) for s in p.specs],
                         [('XR-1', 1920, 1080, 1.0, 0), ('XR-2', 1920, 1080, 1.0, 1920), ('XR-3', 1920, 1080, 1.0, 3840)])
        self.assertEqual([s['name'] for s in p.reshape], ['XR-1', 'XR-2', 'XR-3'])  # all shrink or move left: left to right
        self.assertEqual(p.create, [])
        self.assertEqual(p.placements, [FLAT] * 3)
        self.assertEqual(sorted(p.resized), ['XR-1', 'XR-2'])  # XR-3 only slides left: its capture keeps going
        self.assertIsNone(p.focus)  # the panel collapses
        self.assertEqual([k for k, _ in p.steps()], ['remove', 'reshape', 'reshape', 'reshape'])

    def test_one_desktop_gets_two_more_with_the_lowest_free_numbers(self):
        run = Run(self, [WIDE, D169])
        specs = [s for s in run.specs if s['name'] == 'XR-2']  # XR-1 was removed during the run
        p = dk.plan(specs, [PLACED[0]], dk.RESET, 0)
        self.assertIsNone(p.remove)
        self.assertEqual([(s['name'], s['x']) for s in p.specs], [('XR-1', 2560), ('XR-2', 4480), ('XR-3', 6400)])
        self.assertEqual([s['name'] for s in p.create], ['XR-1', 'XR-3'])
        self.assertEqual(p.placements, [FLAT] * 3)
        held = dk.plan(specs, [PLACED[0]], dk.RESET, 0, held={'XR-1'})  # a failed create still holds XR-1
        self.assertEqual([s['name'] for s in held.specs], ['XR-2', 'XR-3', 'XR-4'])

    def test_the_default_three_need_no_step(self):
        p = dk.plan(Run(self).specs, PLACED[:3], dk.RESET, 0)
        self.assertEqual(p.steps(), [])
        self.assertEqual(p.placements, [FLAT] * 3)


class ResetTests(unittest.TestCase):
    """The live reset end to end: the ease, the broker steps, the frames, the save and the toast."""
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / 'profiles.json'
        self.store = profiles.PlacementStore(self.path, MODEL)

    def start(self, desktops, placements, rolls):
        self.run = Run(self, desktops)
        self.store.save_desktops(self.run.manager.desktops(), placements, rolls)
        self.now = 100.0
        self.told = []
        state = view.ViewState(self.run.manager.names(), placements=placements)
        self.c = view.ViewControl(state, 'run', 'session', lambda n: None, None, None, clock=lambda: self.now,
                                  placements=self.store)
        self.c.desktops = self.run.manager
        self.run.manager.store = self.store
        state.wide, state.sizes = self.run.manager.wide(), self.run.manager.desktops()
        self.c.notify = self.told.append
        self.sup = SupervisorStandIn()
        self.c.attach_supervisor(self.sup)
        self.pointer = self.c.overview_pointer = fp.OverviewPointer(self.c, clock=lambda: self.now)
        self.addCleanup(self.pointer.close)
        self.pointer.rolls[:len(rolls)] = rolls
        self.c.poll(self.now)  # the restored rolls go out; the roll watch takes them in

    def press(self):
        out = self.c.on_message({'op': view.RESET_OP})
        self.c.join_focus()
        return out

    def tick(self, dt):
        self.now += dt
        out = self.c.poll(self.now)
        self.c.join_focus()
        return frames(out)

    def placements_sent(self, fs):
        return [f['payload']['desktops'] for f in fs if f['op'] == 'placement']

    def rolls_sent(self, fs):
        return [f['payload']['roll'] for f in fs if f['op'] == 'roll']

    def test_four_custom_desktops_ease_flat_then_become_the_default_three(self):
        self.start([WIDE, SHARP, D169, WIDE], PLACED, ROLLS)
        self.c.state.open_menu(1)
        first = frames(self.press())
        self.assertEqual(ops(b''.join((json.dumps(f) + '\n').encode() for f in first)), ['placement_menu'])
        self.assertFalse(first[0]['payload']['open'])  # the open panel collapsed first
        self.assertEqual(self.store.load_setup(), (profiles.default_setup(), None))  # the save is cleared at once
        self.assertTrue(self.c.state.desktops_busy)
        # Halfway: every desktop part of the way to flat and level, none there yet, none past it.
        half = self.tick(view.RESET_ANIM_S / 2)
        (mid,) = self.placements_sent(half)
        for a, m in zip(PLACED, mid):
            for key in g.PLACEMENT_KEYS:
                lo, hi = sorted((a[key], FLAT[key]))
                if key == 'x' and a[key] == 1800: lo, hi = 0, 1800  # behind you: comes round the short way
                self.assertTrue(lo <= m[key] <= hi, (key, a[key], m[key]))
                if a[key] != FLAT[key]: self.assertNotIn(m[key], (a[key], FLAT[key]), key)
        (roll,) = self.rolls_sent(half)
        for a, r in zip(ROLLS, roll):
            self.assertTrue(min(a, 0) <= r <= max(a, 0))
            if a: self.assertNotIn(r, (a, 0))
        self.assertEqual(self.store.load_setup()[0], profiles.default_setup())  # never an in-between placement saved
        self.assertEqual(self.sup.calls, [])  # the desktops wait for the ease
        # The ease ends: flat and level, then the desktops: XR-4 lets go of its capture before the broker removes it,
        # and (the worker runs inline in tests) the same tick collects the result.
        end = self.tick(view.RESET_ANIM_S)
        self.assertEqual(self.placements_sent(end)[0], [FLAT] * 4)
        self.assertEqual([f['payload'] for f in end if f['op'] == 'desktops'],
                         [{'outputs': ['XR-1', 'XR-2', 'XR-3']}, {'outputs': ['XR-1', 'XR-2', 'XR-3'], 'renew': ['XR-1', 'XR-2']}])
        self.assertEqual(self.sup.calls[0], 'pause')
        self.assertEqual(self.run.manager.names(), ['XR-1', 'XR-2', 'XR-3'])
        self.assertEqual(self.run.manager.desktops(), [D169] * 3)
        self.assertEqual((self.c.state.names, self.c.state.placements), (['XR-1', 'XR-2', 'XR-3'], [FLAT] * 3))
        self.assertEqual(self.c.state.sizes, [D169] * 3)
        self.assertEqual(self.pointer.rolls, [0] * 4)
        self.assertFalse(self.c.state.desktops_busy)
        self.assertIsNone(self.c.reset)
        self.assertEqual(self.sup.calls[-1][0], 'replace')
        self.assertEqual(self.told, [view.RESET_TOASTS['done']])
        self.assertEqual(self.store.load_setup(), (profiles.default_setup(), None))
        self.assertEqual(json.loads(self.path.read_text())['profiles'][MODEL]['desktops'], [D169] * 3)

    def test_with_the_real_worker_thread_the_desktops_stay_busy_until_it_lands(self):
        self.start([WIDE, SHARP, D169, WIDE], PLACED, ROLLS)
        self.run.manager.threads = True
        gate = __import__('threading').Event()
        original = self.run.broker.reshape
        self.run.broker.reshape = lambda *a: (gate.wait(5), original(*a))[1]
        self.press()
        self.tick(view.RESET_ANIM_S * 1.1)
        self.assertEqual(self.sup.calls, ['pause'])
        self.assertTrue(self.c.state.desktops_busy)
        self.assertIsNone(self.press())  # the running reset finishes first
        self.c.state.open_menu(0)
        self.assertEqual(self.c.desktop_action(0, 'add'), b'')  # the panel's buttons wait too
        self.assertEqual(self.told, [])
        gate.set(); dk.wait_idle(self.run.manager)
        self.tick(0.01)
        self.assertEqual((self.run.manager.names(), self.c.state.names), (['XR-1', 'XR-2', 'XR-3'], ['XR-1', 'XR-2', 'XR-3']))
        self.assertEqual(self.told, [view.RESET_TOASTS['done']])
        self.assertEqual(self.store.load_setup(), (profiles.default_setup(), None))
        self.run.manager.close()

    def test_already_three_default_desktops_only_ease(self):
        self.start(None, PLACED[:3], ROLLS[:3])
        events = len(self.run.transport.events)
        self.press()
        self.tick(view.RESET_ANIM_S * 0.3)
        end = self.tick(view.RESET_ANIM_S)
        self.assertEqual(self.c.state.placements, [FLAT] * 3)
        self.assertEqual(self.run.transport.events[events:], [])  # no broker step at all
        self.assertNotIn('desktops', [f['op'] for f in end])
        self.assertEqual((self.told, self.c.reset, self.c.state.desktops_busy), ([view.RESET_TOASTS['done']], None, False))
        self.assertEqual(self.pointer.rolls[:3], [0] * 3)
        self.assertEqual(self.store.load_setup(), (profiles.default_setup(), None))

    def test_maximized_it_goes_back_to_the_overview_and_keeps_the_follow_or_locked_choice(self):
        self.start(None, PLACED[:3], [0] * 3)
        s = self.c.state
        s.apply('zoom_in'); s.apply('mode_next')
        self.assertEqual((s.zoomed, s.mode), (True, 'locked'))
        views = [f['payload'] for f in frames(self.press()) if f['op'] == 'view']
        self.assertEqual([(v['zoomed'], v['mode']) for v in views], [(False, 'anchor')])
        self.assertEqual(s.maximized_mode, 'locked')  # the next maximize is still locked
        self.tick(view.RESET_ANIM_S * 2)
        s.apply('zoom_in')
        self.assertEqual(s.mode, 'locked')

    def test_a_video_or_a_change_in_progress_turns_it_away_with_a_toast(self):
        self.start([WIDE, D169], PLACED[:2], [10, 20])
        self.c.video_active = True
        self.assertIsNone(self.press())
        self.assertEqual(self.told, [view.RESET_TOASTS['video']])
        self.c.video_active = False
        self.c.state.desktops_busy = True
        self.assertIsNone(self.press())
        self.assertEqual(self.told[-1], view.RESET_TOASTS['busy'])
        self.assertIsNone(self.c.reset)
        self.assertEqual(self.store.load_setup()[0]['desktops'], [WIDE, D169])  # the save untouched

    def test_typing_mode_and_a_second_press_change_nothing(self):
        self.start([WIDE, D169], PLACED[:2], [10, 20])
        self.c.state.typing = True
        self.assertIsNone(self.press())  # a stale bind: its key is released in typing mode
        self.assertIsNone(self.c.reset)
        self.c.state.typing = False
        self.press()
        at = self.c.reset['at']
        self.tick(0.05)
        self.assertIsNone(self.press())  # one at a time
        self.assertEqual((self.c.reset['at'], self.c.resets), (at, 1))

    def test_a_failed_step_says_so_and_saves_what_is_there(self):
        self.start([WIDE, D169, D169], PLACED[:3], [0, 0, 0])
        self.run.transport.refuse_reshape = True
        self.press()
        self.tick(view.RESET_ANIM_S * 1.1)
        self.tick(0.01)
        self.assertEqual(len(self.told), 1)
        self.assertTrue(self.told[0].startswith('Desktop reset stopped: '), self.told)
        self.assertIsNone(self.c.reset)
        self.assertEqual(self.store.load_setup()[0]['placements'], [FLAT] * 3)

    def test_a_run_ending_mid_reset_saves_the_default(self):
        self.start([WIDE, SHARP], PLACED[:2], [500, -500])
        self.press()
        self.tick(view.RESET_ANIM_S / 3)
        self.c.save_due = self.now; self.c.flush()  # a pending save from before: no in-between placements written
        self.assertEqual(self.store.load_setup()[0], profiles.default_setup())
        self.c.save_setup()  # managed.view_controls at the run's end
        self.assertEqual(self.store.load_setup(), (profiles.default_setup(), None))


class EaseTests(unittest.TestCase):
    def test_smootherstep_and_the_short_way_round(self):
        self.assertEqual((view.smootherstep(-1), view.smootherstep(0), view.smootherstep(0.5), view.smootherstep(2)),
                         (0.0, 0.0, 0.5, 1.0))
        p = {'x': 1700, 'y': -900, 'distance': 10, 'curvature': 120}
        self.assertEqual(view.eased_flat(p, 0), p)
        self.assertEqual(view.eased_flat(p, 1), FLAT)
        self.assertEqual(view.eased_flat({**FLAT, 'x': -1700}, 0.5)['x'], -850)
        self.assertEqual(view.eased_flat({**FLAT, 'x': 1800}, 0.5)['x'], 900)  # behind you: round, never a jump
        for e in (0.1, 0.5, 0.9): g.validate_placement(view.eased_flat(p, e))


if __name__ == '__main__':
    unittest.main()
