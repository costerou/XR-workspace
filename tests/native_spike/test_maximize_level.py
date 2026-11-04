"""A maximized desktop sits level (Cody, 2026-11-03), supervisor side. The renderer draws it level (spike view.hpp
zoomed_placements; ctest roll_level, focus_center), so the levelling roll the supervisor owns stays the overview's the
whole time: nothing here may change it, send a levelled one or save one. These tests hold the rest of the deal: a maximize,
a switch, Ctrl+Alt+V and the end of a run all keep the original, and no way of rotating works while maximized.

Offline only: a fake clock, the renderer's overview geometry fixture. No compositor, renderer binary or glasses.
"""
from pathlib import Path
import tempfile
from unittest import mock

from tools.native_spike import profiles, view
from tools.native_spike.geometry import DEFAULT_PLACEMENT

from test_desktops import Run, SupervisorStandIn
from test_glass_pills import Base, OUTPUTS, T0, frames, last
from test_roll_level import grip

ROLLS = [1700, -900, 400, 0]
MODEL = 'luma-ultra'


class RollsStayTests(Base):
    def setUp(self):
        super().setUp()
        self.p.rolls = list(ROLLS)
        self.free()
        self.control.poll()

    def tick(self):
        self.clock.now += 0.05
        return frames(self.control.poll())

    def test_maximizing_and_coming_back_changes_no_roll(self):
        out = self.key('zoom_in') + self.tick()
        self.assertTrue(self.state.zoomed)
        self.assertEqual(self.p.rolls, ROLLS)
        for f in out:
            if f['op'] == 'roll': self.assertEqual(f['payload']['roll'], ROLLS)  # the renderer levels it, not a frame
        self.assertEqual(self.p.roll_sent['roll'], ROLLS)
        out = self.key('zoom_out') + self.tick()
        self.assertFalse(self.state.zoomed)
        self.assertEqual(self.p.rolls, ROLLS)
        self.assertEqual(self.p.roll_sent['roll'], ROLLS)

    def test_a_switch_and_ctrl_alt_v_while_maximized_change_no_roll(self):
        self.key('zoom_in')
        for op in ('next', 'mode_next', 'next', 'prev', 'mode_next'):
            self.key(op)
            self.tick()
            self.assertEqual(self.p.rolls, ROLLS)
        self.assertEqual(self.p.roll_sent['roll'], ROLLS)

    def test_a_maximize_never_counts_as_a_change_to_save(self):
        self.tick()  # the roll watch has seen them
        self.assertIsNone(self.control.save_due)
        self.key('zoom_in'); self.tick()
        self.key('zoom_out'); self.tick()
        self.assertIsNone(self.control.save_due)

    def test_the_brackets_hide_and_take_no_press_while_maximized(self):
        self.key('zoom_in'); self.tick()
        self.assertEqual(self.p.roll_sent['icons'], ['none'] * 4)
        self.press(); self.release()
        self.assertIsNone(self.p.drag)
        self.assertEqual((self.p.rolls, self.p.roll_clicks), (ROLLS, 0))

    def test_r_levels_nothing_while_maximized(self):
        # R belongs to an open panel, and maximizing collapses that panel first: the late key finds none.
        self.key('menu_toggle')
        self.assertTrue(self.state.menu_open)
        self.key('zoom_in')
        self.assertFalse(self.state.menu_open)
        self.key('menu_reset')
        self.assertEqual((self.p.rolls, self.p.roll_levels), (ROLLS, 0))

    def test_a_desktop_change_finishing_after_a_maximize_opens_no_panel(self):
        # U in the open panel, Ctrl+Alt+Up before the change lands. Its focus used to open the panel again, unseen (the
        # renderer draws panels in the overview only) with its keys live, so a bare R in a window levelled the desktop
        # behind the level one, for good.
        run = Run(self)
        self.control.desktops, run.manager.store = run.manager, mock.Mock()
        self.control.attach_supervisor(SupervisorStandIn())
        self.key('menu_toggle')
        self.key('menu_ultrawide')
        self.assertTrue(self.state.desktops_busy)
        self.key('zoom_in')
        self.tick()  # the change lands
        self.join()
        self.assertFalse(self.state.desktops_busy)
        self.assertEqual((self.state.zoomed, self.state.menu_open), (True, False))
        self.key('menu_reset')
        self.assertEqual((self.p.rolls, self.p.roll_levels), (ROLLS, 0))

    def test_a_bracket_click_cut_off_by_a_maximize_turns_nothing(self):
        # Pressed in the overview, released once maximized, before the supervisor's next tick ended the drag: that click
        # would change the desktop behind the level one, unseen.
        self.to(*grip(T0, 'cw', roll=ROLLS[0]))
        self.assertEqual(self.p.hover, ('roll', 0, 'cw'))
        self.press()
        self.assertEqual(self.p.drag['kind'], 'roll')
        self.key('zoom_in')
        self.release()
        self.assertIsNone(self.p.drag)
        self.assertEqual((self.p.rolls, self.p.roll_clicks), (ROLLS, 0))

    def test_a_bracket_drag_ends_where_it_was_as_the_desktop_maximizes(self):
        # Turned in the overview, so it is that overview orientation that comes back.
        self.to(*grip(T0, 'cw', roll=ROLLS[0]))
        self.press()
        self.by(0, 60)
        turned = self.p.rolls[0]
        self.assertNotEqual(turned, ROLLS[0])
        self.key('zoom_in')
        self.clock.now += 0.05
        self.p.feed(self.cur, (0.0, 40.0), OUTPUTS, self.p.want[0])  # the monitors stay: only the maximize ends it
        self.control.poll()
        self.assertIsNone(self.p.drag)
        self.assertEqual(self.p.rolls[0], turned)
        self.release()
        self.key('zoom_out')
        self.assertEqual(self.p.rolls[0], turned)


class SavedRollsTests(Base):
    """XR stopping or crashing while maximized: what is saved, and so what the next start restores, is the original."""
    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / 'profiles.json'
        self.control.placements = self.store = profiles.PlacementStore(self.path, MODEL)
        self.store.save_layout(self.state.placements, [1, 2, 3])  # an older save: only a new one can bring ROLLS
        self.p.rolls = list(ROLLS)
        self.control.poll()

    def test_a_run_ending_maximized_saves_the_overview_rolls(self):
        self.key('zoom_in')
        self.control.save_setup()
        setup, why = self.store.load_setup()
        self.assertIsNone(why)
        self.assertEqual(setup['rolls'], ROLLS[:3])

    def test_a_debounced_save_landing_while_maximized_saves_the_overview_rolls(self):
        self.p.rolls[1] = -1200  # turned in the overview just before the maximize: its save is still due
        self.clock.now += 0.1; self.control.poll()
        self.p.rolls[1] = -1250
        self.clock.now += 0.1; self.control.poll()
        self.assertIsNotNone(self.control.save_due)
        self.key('zoom_in')
        self.clock.now += 5; self.control.poll()
        self.assertEqual(self.store.load_setup()[0]['rolls'], [1700, -1250, 400])


def moved(**fields):
    return {**DEFAULT_PLACEMENT, **fields}


class HeldPlaceTests(Base):
    """Cody, 2026-11-03: a change in the settings panel's Place tab while a desktop is maximized waits and applies on the
    way back to the overview. It can't be seen from a maximized desktop, the view being aimed at it. Never refused: the
    panel keeps showing it, and a run ending first saves it, since the next start begins in the overview."""
    FLAT = [dict(DEFAULT_PLACEMENT) for _ in range(3)]

    def setUp(self):
        super().setUp()
        self.told = []
        self.control.notify = self.told.append
        self.free()
        self.saves = self.store.saves

    def place(self, *placements):
        """The panel's Apply: every desktop's placement, as Model.withPlacement sends them."""
        return frames(self.control.on_message({'op': 'placement', 'payload': {'desktops': list(placements)}}) or b'')

    def shown(self):
        return self.control.status_reply({})['placements']

    def test_it_waits_while_maximized_and_lands_in_the_overview(self):
        self.key('zoom_in')
        self.assertEqual(self.place(moved(x=300, y=100), self.FLAT[1], self.FLAT[2]), [])  # nothing reaches the renderer
        self.assertEqual(self.state.placements, self.FLAT)
        self.assertEqual(self.store.saves, self.saves)  # nor the profile, yet
        out = self.key('zoom_out')
        self.assertEqual(self.state.placements, [moved(x=300, y=100), *self.FLAT[1:]])
        self.assertEqual(self.store.saved, self.state.placements)
        placement = last(out, 'placement')
        self.assertEqual(placement['desktops'], self.state.placements)
        self.assertEqual(placement['glide'], view.GLIDE_MS)  # it glides there as the desktop shrinks, with no jump
        self.assertLess([f['op'] for f in out].index('view'), [f['op'] for f in out].index('placement'))
        self.key('zoom_in')
        self.assertNotIn('placement', [f['op'] for f in self.key('zoom_out')])  # applied once, nothing left over

    def test_every_way_back_to_the_overview_applies_it(self):
        ways = {'Ctrl+Alt+Down': lambda: self.key('zoom_out'), 'Ctrl+Alt+L': lambda: self.key('menu_toggle'),
                'a pill': lambda: frames(self.control.pill_click(1))}
        for name, back in ways.items():
            with self.subTest(way=name):
                self.key('zoom_in')
                self.place(moved(x=len(name)), self.FLAT[1], self.FLAT[2])
                back()
                self.assertFalse(self.state.zoomed)
                self.assertEqual(self.state.placements[0], moved(x=len(name)))
                if self.state.menu_open: self.key('menu_back')

    def test_the_panel_keeps_showing_it_and_a_second_edit_adds_to_it(self):
        self.key('zoom_in')
        self.place(moved(x=300), self.FLAT[1], self.FLAT[2])
        self.assertEqual(self.shown(), [moved(x=300), *self.FLAT[1:]])  # the slider does not snap back
        self.place(*self.shown()[:1], moved(y=-50), self.FLAT[2])       # the panel builds on what it was shown
        self.assertEqual(self.shown(), [moved(x=300), moved(y=-50), self.FLAT[2]])
        self.key('zoom_out')
        self.assertEqual(self.state.placements, [moved(x=300), moved(y=-50), self.FLAT[2]])

    def test_a_switch_and_ctrl_alt_v_leave_it_waiting(self):
        self.key('zoom_in')
        self.place(moved(x=300), self.FLAT[1], self.FLAT[2])
        for op in ('next', 'mode_next', 'next', 'mode_next'):
            self.assertNotIn('placement', [f['op'] for f in self.key(op)])
        self.assertEqual(self.state.placements, self.FLAT)
        self.key('zoom_out')
        self.assertEqual(self.state.placements[0], moved(x=300))

    def test_changing_it_back_leaves_nothing_to_apply(self):
        self.key('zoom_in')
        self.place(moved(x=300), self.FLAT[1], self.FLAT[2])
        self.place(*self.FLAT)
        self.assertEqual(self.shown(), self.FLAT)
        self.assertNotIn('placement', [f['op'] for f in self.key('zoom_out')])
        self.assertEqual(self.store.saves, self.saves)

    def test_a_bad_change_is_ignored_while_maximized_too(self):
        self.key('zoom_in')
        self.assertEqual(self.place(moved(x=300)), [])  # one placement for three desktops
        self.assertEqual(self.shown(), self.FLAT)

    def test_it_says_so_once_per_change_waiting(self):
        self.key('zoom_in')
        self.place(moved(x=300), self.FLAT[1], self.FLAT[2])
        self.place(moved(x=320), self.FLAT[1], self.FLAT[2])
        self.control.join_focus()
        self.assertEqual(self.told, [view.PLACE_HELD_TOAST])
        self.key('zoom_out')
        self.key('zoom_in')
        self.place(moved(x=340), self.FLAT[1], self.FLAT[2])
        self.control.join_focus()
        self.assertEqual(self.told, [view.PLACE_HELD_TOAST] * 2)

    def test_the_overview_still_applies_at_once(self):
        out = self.place(moved(x=300), self.FLAT[1], self.FLAT[2])
        self.assertEqual(self.state.placements[0], moved(x=300))
        self.assertNotIn('glide', last(out, 'placement'))
        self.assertEqual(self.told, [])

    def test_it_follows_its_desktop_through_a_desktop_change(self):
        self.key('zoom_in')
        self.place(self.FLAT[0], self.FLAT[1], moved(x=300))
        self.state.set_desktops(['XR-1', 'XR-3'], self.FLAT[:2], [False, False])
        self.assertEqual(self.shown(), [self.FLAT[0], moved(x=300)])
        self.key('zoom_out')
        self.assertEqual(self.state.placements, [self.FLAT[0], moved(x=300)])


class HeldPlaceSavedTests(Base):
    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.control.placements = self.store = profiles.PlacementStore(Path(tmp.name) / 'profiles.json', MODEL)
        self.p.rolls = list(ROLLS)
        self.control.poll()

    def test_a_run_ending_while_it_waits_saves_it_with_the_overview_rolls(self):
        self.key('zoom_in')
        change = [moved(x=300), dict(DEFAULT_PLACEMENT), dict(DEFAULT_PLACEMENT)]
        self.control.on_message({'op': 'placement', 'payload': {'desktops': change}})
        self.assertEqual(self.store.load_setup()[0]['placements'][0], DEFAULT_PLACEMENT)  # not while it waits
        self.control.flush()
        self.control.save_setup()
        setup, why = self.store.load_setup()
        self.assertIsNone(why)
        self.assertEqual((setup['placements'], setup['rolls']), (change, ROLLS[:3]))
