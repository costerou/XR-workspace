"""Ctrl+Alt+R (view.RESET_OP, remember-layout-reset-bind, 2026-11-03): every desktop back to the default three flat
1920x1080 desktops at 100% with the default spacing, live. The first press only asks; a second within view.RESET_ARM_S
resets: an open panel collapses, a maximized desktop returns to the overview (its follow / locked choice stays), the saved
setup is replaced by the default with the old layout kept as a one-step backup, every desktop eases flat and level over
view.RESET_ANIM_S while a fourth drops out of the view, then one desktops.RESET plan reshapes, removes or adds desktops,
the tiles easing along the same curve (the `desktops` frames' `ease`, view.hpp TileEase), a removed desktop's windows go to
the last one that stays, and a toast confirms. The bind is a row of the central bind table (view.BINDS), like every other
run bind.

Offline: test_desktops' fake broker and real journal, a supervisor stand-in, the real overview pointer (its `roll`
frames), temporary profiles. Nothing here talks to a compositor."""
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest import mock

from tools.native_spike import bind_check, desktops as dk, free_pointer as fp, geometry as g, managed, profiles, view, xr_control
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
        self.assertIn('| Ctrl+Alt+R, twice |', readme.split('### Key cheat sheet', 1)[1].split('\n## ', 1)[0])
        self.assertIn(view.RESET_TOASTS['confirm'], readme)
        technical = (ROOT / 'docs/technical.md').read_text()
        self.assertIn('| Ctrl+Alt+R, twice within 3 s | run |', technical)
        self.assertIn(view.RESET_TOASTS['confirm'], technical)


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
    """The live reset end to end: the confirm press, the backup, the ease, the broker steps, the frames, the save, the toast."""
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / 'profiles.json'
        self.store = profiles.PlacementStore(self.path, MODEL)

    def start(self, desktops, placements, rolls, spacing=view.DEFAULT_SPACING):
        self.run = Run(self, desktops)
        self.store.save_desktops(self.run.manager.desktops(), placements, rolls)
        self.settings = profiles.ProfileStore(self.path, MODEL)
        self.settings.save({**self.settings.load(), 'spacing': spacing})
        self.now = 100.0
        self.told = []
        self.moved = []
        state = view.ViewState(self.run.manager.names(), spacing=spacing, placements=placements)
        self.c = view.ViewControl(state, 'run', 'session', lambda n: None, self.settings, None, clock=lambda: self.now,
                                  placements=self.store)
        self.c.desktops = self.run.manager
        self.run.manager.store = self.store
        self.run.manager.move_windows = lambda source, target: (self.moved.append((source, target)),
                                                                self.run.transport.events.append('move_windows'))
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

    def reset(self):
        """Both presses: the frames of the second."""
        self.assertIsNone(self.press())
        self.assertEqual(self.told, [view.RESET_TOASTS['confirm']])
        self.told.clear()
        return self.press()

    def tick(self, dt):
        self.now += dt
        out = self.c.poll(self.now)
        self.c.join_focus()
        return frames(out)

    def placements_sent(self, fs):
        return [f['payload']['desktops'] for f in fs if f['op'] == 'placement']

    def rolls_sent(self, fs):
        return [f['payload']['roll'] for f in fs if f['op'] == 'roll']

    def saved(self):
        return json.loads(self.path.read_text())['profiles'][MODEL]

    def test_the_first_press_only_asks_and_a_late_second_asks_again(self):
        self.start([WIDE, D169], PLACED[:2], [10, 20], spacing=60)
        before = self.path.read_bytes()
        self.assertIsNone(self.press())
        self.assertEqual(self.told, ['Press Ctrl+Alt+R again to reset all monitors'])
        self.assertIsNone(self.c.reset)  # nothing happened: no frames, no save, nothing moved
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.c.state.placements, PLACED[:2])
        self.assertEqual(self.tick(view.RESET_ARM_S + 1), [])  # and left alone, nothing happens either
        self.assertIsNone(self.c.reset)
        self.assertIsNone(self.press())  # too late: this one only asks again
        self.assertEqual(self.told, ['Press Ctrl+Alt+R again to reset all monitors'] * 2)
        self.assertIsNone(self.c.reset)
        self.now += view.RESET_ARM_S - 0.01
        self.press()  # within 3 s of that one: the reset
        self.assertIsNotNone(self.c.reset)

    def test_the_arm_window_is_three_seconds(self):
        self.assertEqual(view.RESET_ARM_S, 3.0)
        self.start([WIDE, D169], PLACED[:2], [0, 0])
        self.press()
        self.now += 3.0  # exactly 3 s: too late, like Remove's
        self.press()
        self.assertIsNone(self.c.reset)

    def test_four_custom_desktops_ease_then_become_the_default_three(self):
        self.start([WIDE, SHARP, D169, WIDE], PLACED, ROLLS, spacing=120)
        self.c.state.open_menu(1)
        first = frames(self.reset())
        self.assertEqual([f['op'] for f in first], ['placement_menu', 'view'])
        self.assertFalse(first[0]['payload']['open'])  # the open panel collapsed first
        self.assertEqual(first[1]['payload']['spacing'], view.DEFAULT_SPACING)  # the renderer eases the spacing
        self.assertEqual(self.store.load_setup(), (profiles.default_setup(), None))  # the save is replaced at once
        self.assertEqual(self.saved()['spacing'], view.DEFAULT_SPACING)
        self.assertEqual(self.saved()['previous_setup'], {'desktops': [WIDE, SHARP, D169, WIDE], 'placements': PLACED,
                                                          'rolls': ROLLS, 'spacing': 120})  # the backup, spacing included
        self.assertTrue(self.c.state.desktops_busy)
        # Halfway: three desktops part of the way to flat and level, the fourth on its way down out of the view.
        half = self.tick(view.RESET_ANIM_S / 2)
        (mid,) = self.placements_sent(half)
        for a, m in zip(PLACED[:3], mid):
            for key in g.PLACEMENT_KEYS:
                lo, hi = sorted((a[key], FLAT[key]))
                if key == 'x' and a[key] == 1800: lo, hi = 0, 1800  # behind you: comes round the short way
                self.assertTrue(lo <= m[key] <= hi, (key, a[key], m[key]))
                if a[key] != FLAT[key]: self.assertNotIn(m[key], (a[key], FLAT[key]), key)
        self.assertEqual(mid[3], {'x': 300, 'y': 0, 'distance': 105, 'curvature': 4})  # from +90 up to -90 down: level at half
        (roll,) = self.rolls_sent(half)
        for a, r in zip(ROLLS, roll):
            self.assertTrue(min(a, 0) <= r <= max(a, 0))
            if a: self.assertNotIn(r, (a, 0))
        self.assertEqual(self.store.load_setup()[0], profiles.default_setup())  # never an in-between placement saved
        self.assertEqual(self.sup.calls, [])  # the desktops wait for the ease
        # The ease ends, XR-4 out of the view: it lets go of its capture before the broker removes it, its windows go to
        # XR-3 first, and (the worker runs inline in tests) the same tick collects the result. Both frames ask for the
        # tiles' ease.
        end = self.tick(view.RESET_ANIM_S)
        self.assertEqual(self.placements_sent(end)[0], [FLAT] * 3 + [view.RESET_LEAVING_PLACEMENT])
        self.assertEqual([f['payload'] for f in end if f['op'] == 'desktops'],
                         [{'outputs': ['XR-1', 'XR-2', 'XR-3'], 'ease': True},
                          {'outputs': ['XR-1', 'XR-2', 'XR-3'], 'renew': ['XR-1', 'XR-2'], 'ease': True}])
        self.assertEqual(self.sup.calls[0], 'pause')
        self.assertEqual(self.moved, [('XR-4', 'XR-3')])
        events = self.run.transport.events
        self.assertLess(events.index('move_windows'), events.index('evacuate_remove'))
        self.assertEqual(self.run.manager.names(), ['XR-1', 'XR-2', 'XR-3'])
        self.assertEqual(self.run.manager.desktops(), [D169] * 3)
        self.assertEqual((self.c.state.names, self.c.state.placements), (['XR-1', 'XR-2', 'XR-3'], [FLAT] * 3))
        self.assertEqual(self.c.state.sizes, [D169] * 3)
        self.assertEqual(self.pointer.rolls, [0] * 4)
        self.assertFalse(self.c.state.desktops_busy)
        self.assertIsNone(self.c.reset)
        self.assertEqual(self.sup.calls[-1][0], 'replace')
        self.assertEqual(self.told, ["All monitors reset to three flat 1920x1080 desktops at 100%. XR-4's windows are now on XR-3."])
        self.assertEqual(self.store.load_setup(), (profiles.default_setup(), None))
        self.assertEqual(self.saved()['desktops'], [D169] * 3)
        self.assertEqual(self.saved()['previous_setup']['spacing'], 120)  # the saves after it keep the backup
        self.c.state.placements = [PLACED[0]] * 3
        self.c.save_setup()
        self.assertEqual(self.saved()['previous_setup']['desktops'], [WIDE, SHARP, D169, WIDE])

    def test_a_desktop_frame_outside_a_reset_does_not_ease(self):
        self.start(None, PLACED[:3], [0] * 3)
        self.c.state.open_menu(0)
        self.c.desktop_action(0, 'ultrawide')
        fs = self.tick(0.01)
        self.assertEqual([f['payload'] for f in fs if f['op'] == 'desktops'], [{'outputs': ['XR-1', 'XR-2', 'XR-3'], 'renew': ['XR-1']}])
        self.assertEqual(self.told, [])

    def test_with_the_real_worker_thread_the_desktops_stay_busy_until_it_lands(self):
        self.start([WIDE, SHARP, D169, WIDE], PLACED, ROLLS)
        self.run.manager.threads = True
        gate = __import__('threading').Event()
        original = self.run.broker.reshape
        self.run.broker.reshape = lambda *a: (gate.wait(5), original(*a))[1]
        self.reset()
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
        self.assertEqual(len(self.told), 1)
        self.assertEqual(self.store.load_setup(), (profiles.default_setup(), None))
        self.run.manager.close()

    def test_already_three_default_desktops_only_ease(self):
        self.start(None, PLACED[:3], ROLLS[:3])
        events = len(self.run.transport.events)
        self.reset()
        self.tick(view.RESET_ANIM_S * 0.3)
        end = self.tick(view.RESET_ANIM_S)
        self.assertEqual(self.c.state.placements, [FLAT] * 3)
        self.assertEqual(self.run.transport.events[events:], [])  # no broker step at all
        self.assertNotIn('desktops', [f['op'] for f in end])
        self.assertEqual((self.told, self.c.reset, self.c.state.desktops_busy), ([view.RESET_TOASTS['done']], None, False))
        self.assertEqual(self.moved, [])
        self.assertEqual(self.pointer.rolls[:3], [0] * 3)
        self.assertEqual(self.store.load_setup(), (profiles.default_setup(), None))

    def test_a_missing_desktop_is_added_with_the_tiles_eased(self):
        self.start([WIDE, D169], PLACED[:2], [0, 0])
        self.reset()
        end = self.tick(view.RESET_ANIM_S * 1.1)
        self.assertEqual([f['payload'] for f in end if f['op'] == 'desktops'],
                         [{'outputs': ['XR-1', 'XR-2', 'XR-3'], 'renew': ['XR-1'], 'ease': True}])
        self.assertEqual(self.run.manager.desktops(), [D169] * 3)
        self.assertEqual(self.moved, [])

    def test_resetting_the_default_keeps_the_backup_of_the_last_real_layout(self):
        self.start([WIDE, D169], PLACED[:2], [10, 20], spacing=60)
        self.reset()
        self.tick(view.RESET_ANIM_S * 1.1)
        first = self.saved()['previous_setup']
        self.assertEqual((first['desktops'], first['rolls'], first['spacing']), ([WIDE, D169], [10, 20], 60))
        self.now += 10
        self.told.clear()
        self.reset()  # nothing left to reset: the backup must not become the default
        self.tick(view.RESET_ANIM_S * 1.1)
        self.assertEqual(self.saved()['previous_setup'], first)
        self.c.state.placements = [PLACED[0]] + [FLAT] * 2  # a new layout, then a reset: this is the one step kept
        self.now += 10
        self.told.clear()
        self.reset()
        self.assertEqual(self.saved()['previous_setup'], {'desktops': [D169] * 3, 'placements': [PLACED[0]] + [FLAT] * 2,
                                                          'rolls': [0] * 3, 'spacing': view.DEFAULT_SPACING})

    def test_maximized_it_goes_back_to_the_overview_and_keeps_the_follow_or_locked_choice(self):
        self.start(None, PLACED[:3], [0] * 3)
        s = self.c.state
        s.apply('zoom_in'); s.apply('mode_next')
        self.assertEqual((s.zoomed, s.mode), (True, 'locked'))
        views = [f['payload'] for f in frames(self.reset()) if f['op'] == 'view']
        self.assertEqual([(v['zoomed'], v['mode']) for v in views], [(False, 'anchor')])
        self.assertEqual(s.maximized_mode, 'locked')  # the next maximize is still locked
        self.tick(view.RESET_ANIM_S * 2)
        s.apply('zoom_in')
        self.assertEqual(s.mode, 'locked')

    def test_maximized_the_backup_keeps_the_rolls_of_the_overview_not_the_level_one(self):
        self.start(None, PLACED[:3], [1200, -300, 50])  # drawn level while maximized; these are still its rolls
        self.c.state.apply('zoom_in')
        self.reset()
        self.assertEqual(self.saved()['previous_setup']['rolls'], [1200, -300, 50])
        self.tick(view.RESET_ANIM_S * 2)
        self.assertEqual(self.pointer.rolls[:3], [0, 0, 0])

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
        self.assertIsNone(self.c.reset_armed_at)  # neither counted as the first press
        self.assertEqual(self.store.load_setup()[0]['desktops'], [WIDE, D169])  # the save untouched

    def test_typing_mode_and_a_second_press_change_nothing(self):
        self.start([WIDE, D169], PLACED[:2], [10, 20])
        self.c.state.typing = True
        self.assertIsNone(self.press())  # a stale bind: its key is released in typing mode
        self.assertEqual((self.c.reset, self.told), (None, []))
        self.c.state.typing = False
        self.reset()
        at = self.c.reset['at']
        self.tick(0.05)
        self.assertIsNone(self.press())  # one at a time
        self.assertEqual((self.c.reset['at'], self.c.resets), (at, 1))

    def test_a_failed_step_says_so_and_saves_what_is_there(self):
        self.start([WIDE, D169, D169], PLACED[:3], [0, 0, 0])
        self.run.transport.refuse_reshape = True
        self.reset()
        self.tick(view.RESET_ANIM_S * 1.1)
        self.tick(0.01)
        self.assertEqual(len(self.told), 1)
        self.assertTrue(self.told[0].startswith('Desktop reset stopped: '), self.told)
        self.assertIsNone(self.c.reset)
        self.assertEqual(self.store.load_setup()[0]['placements'], [FLAT] * 3)

    def test_a_refused_removal_brings_the_dropped_desktop_back_flat(self):
        self.start([D169] * 4, PLACED, [0] * 4)
        self.run.transport.refuse_remove = True
        self.reset()
        self.tick(view.RESET_ANIM_S * 1.1)
        self.tick(0.01)
        self.assertEqual(self.c.state.names, ['XR-1', 'XR-2', 'XR-3', 'XR-4'])
        self.assertEqual(self.c.state.placements, [FLAT] * 4)  # not left underfoot
        self.assertIn('could not be removed', self.told[0])
        self.assertIsNone(self.c.reset)

    def test_a_run_ending_mid_reset_saves_the_default(self):
        self.start([WIDE, SHARP], PLACED[:2], [500, -500])
        self.reset()
        self.tick(view.RESET_ANIM_S / 3)
        self.c.save_due = self.now; self.c.flush()  # a pending save from before: no in-between placements written
        self.assertEqual(self.store.load_setup()[0], profiles.default_setup())
        self.c.save_setup()  # managed.view_controls at the run's end
        self.assertEqual(self.store.load_setup(), (profiles.default_setup(), None))
        self.assertEqual(self.saved()['previous_setup']['rolls'], [500, -500])  # the backup survives it


class WindowMoveTests(unittest.TestCase):
    """The removed desktop's windows go to XR-3 as whole workspaces, the way adoption moves them (managed._window_mover)."""
    def client(self, workspaces, clients):
        client = mock.Mock()
        client.query.side_effect = lambda what, *a, **k: {'workspaces': workspaces, 'clients': clients}[what]
        return client

    def window(self, address, workspace, **extra):
        return {'address': address, 'workspace': {'id': workspace}, 'pinned': False, 'at': [0, 0], 'class': 'kitty', 'title': 't', **extra}

    def test_each_workspace_moves_whole(self):
        client = self.client([{'id': 91, 'name': '91', 'monitor': 'XR-4'}, {'id': 95, 'name': '95', 'monitor': 'XR-4'},
                              {'id': 81, 'name': '81', 'monitor': 'XR-3'}],
                             [self.window('0xa', 91), self.window('0xb', 95), self.window('0xc', 81)])
        managed._window_mover(client)('XR-4', 'XR-3')
        client.adopt_windows.assert_called_once_with({'workspaces': [(91, 'XR-3'), (95, 'XR-3')], 'windows': []})

    def test_an_empty_desktop_moves_nothing(self):
        client = self.client([{'id': 91, 'name': '91', 'monitor': 'XR-4'}], [])
        managed._window_mover(client)('XR-4', 'XR-3')
        client.adopt_windows.assert_not_called()

    def test_a_pinned_window_stays_and_the_others_still_move(self):
        client = self.client([{'id': 91, 'name': '91', 'monitor': 'XR-4'}],
                             [self.window('0xa', 91), self.window('0xb', 91, pinned=True)])
        managed._window_mover(client)('XR-4', 'XR-3')
        client.adopt_windows.assert_called_once_with({'workspaces': [], 'windows': [('0xa', 'XR-3')]})

    def test_a_compositor_error_says_so_and_never_stops_the_reset(self):
        client = mock.Mock()
        client.query.side_effect = OSError('no socket')
        with mock.patch('tools.native_spike.send.notify') as told:
            managed._window_mover(client)('XR-4', 'XR-3')
        told.assert_called_once_with("Could not move XR-4's windows to XR-3: they go to your normal monitor.")

    def test_the_run_wires_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp) / 'run'; runtime.mkdir(mode=0o700)
            client = mock.Mock(runtime_dir=runtime, event_stream=None)
            client.query.return_value = []
            for name in ('lock_pointer', 'enter_desktop'): delattr(client, name)
            run = Run(self)
            store = profiles.ProfileStore(Path(tmp) / 'profiles.json', MODEL)
            with managed.view_controls(client, 'run', 'session', run.manager.names(), store=store, desktops=run.manager):
                self.assertIsNotNone(run.manager.move_windows)


class EaseTests(unittest.TestCase):
    def test_smootherstep_and_the_short_way_round(self):
        self.assertEqual((view.smootherstep(-1), view.smootherstep(0), view.smootherstep(0.5), view.smootherstep(2)),
                         (0.0, 0.0, 0.5, 1.0))
        p = {'x': 1700, 'y': -900, 'distance': 10, 'curvature': 120}
        self.assertEqual(view.eased_to(p, FLAT, 0), p)
        self.assertEqual(view.eased_to(p, FLAT, 1), FLAT)
        self.assertEqual(view.eased_to({**FLAT, 'x': -1700}, FLAT, 0.5)['x'], -850)
        self.assertEqual(view.eased_to({**FLAT, 'x': 1800}, FLAT, 0.5)['x'], 900)  # behind you: round, never a jump
        for e in (0.1, 0.5, 0.9): g.validate_placement(view.eased_to(p, FLAT, e))

    def test_the_drop_away_is_a_valid_placement_out_of_the_view(self):
        self.assertEqual(g.validate_placement(view.RESET_LEAVING_PLACEMENT), view.RESET_LEAVING_PLACEMENT)
        self.assertEqual(view.RESET_LEAVING_PLACEMENT['y'], g.PLACEMENT_BOUNDS['y'][0])  # straight down
        self.assertEqual(set(view.RESET_LEAVING_PLACEMENT), set(g.PLACEMENT_KEYS))

    def test_the_renderers_tile_ease_uses_the_same_time(self):
        hpp = (ROOT / 'spike/include/xr/view.hpp').read_text()
        self.assertEqual(float(re.search(r'kTileEaseMs = ([0-9.]+)f', hpp).group(1)), view.RESET_ANIM_S * 1000)
        self.assertIn('bool ease = false;', (ROOT / 'spike/include/xr/ipc.hpp').read_text())

    def test_the_desktops_frame_carries_ease_only_when_asked(self):
        plain = json.loads(view.desktops_frame('run', 's', 1, ['XR-1'], ['XR-1']))['payload']
        self.assertEqual(plain, {'outputs': ['XR-1'], 'renew': ['XR-1']})
        eased = json.loads(view.desktops_frame('run', 's', 1, ['XR-1'], ease=True))['payload']
        self.assertEqual(eased, {'outputs': ['XR-1'], 'ease': True})


if __name__ == '__main__':
    unittest.main()
