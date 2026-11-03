"""A maximized desktop sits level (Cody, 2026-11-03), supervisor side. The renderer draws it level (spike view.hpp
zoomed_placements; ctest roll_level, focus_center), so the levelling roll the supervisor owns stays the overview's the
whole time: nothing here may change it, send a levelled one or save one. These tests hold the rest of the deal: a maximize,
a switch, Ctrl+Alt+V and the end of a run all keep the original, and no way of rotating works while maximized.

Offline only: a fake clock, the renderer's overview geometry fixture. No compositor, renderer binary or glasses.
"""
from pathlib import Path
import tempfile
from unittest import mock

from tools.native_spike import profiles

from test_desktops import Run, SupervisorStandIn
from test_glass_pills import Base, OUTPUTS, T0, frames
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
