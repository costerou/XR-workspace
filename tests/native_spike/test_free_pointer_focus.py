"""Overview click-to-zoom: a click with the circle cursor (the free pointer's dot) on a desktop selects it, gives its
XR-n output Hyprland's monitor focus and zooms into it, as Ctrl+Alt+Up does (view.ViewControl.focus_desktop, from
free_pointer.OverviewPointer.on_op). Clicks on anything else (pills, corners, the help card, empty space) and clicks
that only wake the idle dot focus nothing and stay in the overview.

Offline only: fake clock, the free-pointer test rig, focus requests recorded in a list. No compositor, renderer binary
or glasses.
"""
import threading
import unittest

from tools.native_spike import free_pointer as fp, view
from tools.native_spike.geometry import DEFAULT_PLACEMENT
from tools.native_spike.hyprland import HyprlandClient

from test_free_pointer import G, T1, X1, X2, PointerBase, frames, last


class ClickFocusTests(PointerBase):
    def free_on(self, x):
        """Push the cursor against the top edge of the monitor at layout x: free mode, the dot on that desktop's top row."""
        self.at(x + 960, 540); self.at(x + 960, 0)
        self.assertEqual(self.p.mode, 'free')

    def focused_after(self, *ops):
        out = [f for op in ops for f in self.op(op)]
        self.control.join_focus(2)
        return out

    def test_a_click_on_a_desktop_focuses_its_monitor_and_selects_it(self):
        self.free_on(X2)
        self.assertEqual(self.p.hover[:2], ('desktop', 1))
        self.control.state.selected = 0  # e.g. Ctrl+Alt+Left since the dot came up here
        self.focused_after('resize_press')  # the press alone: maybe a window drag (on hold), nothing focused yet
        self.assertEqual(self.focused, [])
        self.assertFalse(self.control.state.zoomed)
        out = self.focused_after('resize_release')
        self.assertEqual(self.focused, ['XR-2'])
        self.assertEqual(self.control.state.selected, 1)
        self.assertEqual([(f['payload']['selected'], f['payload']['zoomed']) for f in out if f['op'] == 'view'], [(1, True)])
        self.assertTrue(self.control.state.zoomed)  # zoomed into it, as Ctrl+Alt+Up does
        self.assertEqual(self.p.release_at, (X2 + 960, fp.INSET_PX))  # where the cursor shows: at the click
        self.assertEqual(self.p.focus_clicks, 1)
        self.assertEqual(self.focused_after('resize_press', 'resize_release'), [])  # zoomed in: no overview click now
        self.assertEqual(self.focused, ['XR-2'])

    def test_the_selected_desktop_still_gets_hyprland_focus(self):
        # The selection is the run's; Hyprland's focus can be elsewhere (the hidden cursor was parked on another monitor).
        self.free_on(X2)
        self.assertEqual(self.control.state.selected, 1)  # moving into XR-2 selected it already
        out = self.focused_after('resize_press', 'resize_release')
        self.assertEqual(self.focused, ['XR-2'])
        self.assertEqual(last(out, 'view')['selected'], 1)  # the same desktop, zoomed into
        self.assertTrue(last(out, 'view')['zoomed'])

    def test_a_click_on_another_desktop_than_the_one_the_dot_left_focuses_the_clicked_one(self):
        # The cursor runs off XR-1's right edge: free, the hidden cursor parked on XR-1. The dot then crosses just onto
        # XR-2 (less than ENTER_PX in, so still free) and clicks there: XR-2 gets the focus, not the parking monitor.
        self.at(X1 + 1900, 540); self.at(X2 + 6, 540)
        self.assertEqual((self.p.mode, self.p.desktop), ('free', 0))
        self.move_canvas(fp.project(G, T1, DEFAULT_PLACEMENT, T1[0] + 1, T1[1] + T1[3] / 2)[0] - self.p.pointer[0], 0)
        self.assertEqual((self.p.mode, self.p.hover[:2]), ('free', ('desktop', 1)))
        self.focused_after('resize_press', 'resize_release')
        self.assertEqual(self.focused, ['XR-2'])
        self.assertEqual(self.control.state.selected, 1)
        self.assertTrue(self.control.state.zoomed)
        self.assertEqual(self.p.release_at[0] // 1920, X2 // 1920)  # the cursor shows on XR-2, not the parking monitor

    def test_each_click_focuses_the_desktop_it_lands_on(self):
        self.free_on(X1)
        self.focused_after('resize_press', 'resize_release')
        self.assertTrue(self.control.state.zoomed)
        self.control.poll(); self.op('zoom_out')
        self.free_on(X2)
        self.focused_after('resize_press', 'resize_release')
        self.assertEqual(self.focused, ['XR-1', 'XR-2'])
        self.assertEqual(self.control.state.selected, 1)

    def test_a_click_outside_an_open_panel_closes_it_and_still_focuses(self):
        self.control.pill_click(0)
        self.assertTrue(self.control.state.menu_open)
        self.free_on(X2)
        self.focused_after('resize_press')
        self.assertFalse(self.control.state.menu_open)  # the press collapses it
        self.focused_after('resize_release')
        self.assertEqual(self.focused, ['XR-2'])

    def test_a_click_in_empty_space_focuses_nothing(self):
        self.free_on(X1)
        self.move(0, -300)
        self.assertIsNone(self.p.hover)
        self.focused_after('resize_press', 'resize_release')
        self.assertEqual(self.focused, [])

    def test_a_click_on_a_pill_opens_it_and_focuses_nothing(self):
        self.on_pill(0)
        self.focused_after('resize_press', 'resize_release')
        self.assertTrue(self.control.state.menu_open)
        self.assertEqual(self.focused, [])

    def test_a_press_on_a_corner_resizes_and_focuses_nothing(self):
        self.free_at_top(lx=1919)  # XR-1's top-right corner, its zone reaching outside the desktop
        self.move_canvas(4, -4)
        self.assertEqual(self.p.hover[0], 'corner')
        self.focused_after('resize_press', 'resize_release')
        self.assertEqual(self.focused, [])

    def test_a_click_on_the_help_card_over_a_desktop_focuses_nothing(self):
        self.op('help')
        self.control.set_help_geometry({'card': (560, 300, 800, 600)})  # over the middle desktop
        self.at(X2 + 960, 540)  # under the card: the dot, not the cursor
        self.assertEqual(self.p.hover, ('help', 'card'))
        self.focused_after('resize_press', 'resize_release')
        self.assertEqual(self.focused, [])

    def test_a_click_that_only_wakes_the_idle_dot_focuses_nothing(self):
        self.free_on(X2)
        self.clock.now += view.OVERLAY_IDLE_CLOSE_S + 1
        self.control.poll()
        self.focused_after('resize_press', 'resize_release')
        self.assertEqual(self.focused, [])
        self.assertEqual(self.p.mode, 'free')

    def test_the_focus_never_blocks_the_supervisor_loop(self):
        gate, calls = threading.Event(), []
        self.control.focus = lambda name: (gate.wait(5), calls.append(name))
        self.addCleanup(gate.set)
        self.free_on(X2)
        self.control.state.selected = 0
        out = self.op('resize_press') + self.op('resize_release')  # returns while the compositor call is still waiting
        self.assertEqual(last(out, 'view')['selected'], 1)
        self.assertEqual(calls, [])
        gate.set()
        self.control.join_focus(2)
        self.assertEqual(calls, ['XR-2'])

    def test_a_refused_focus_still_selects(self):
        def refuse(name): raise OSError('compositor gone')
        self.control.focus = refuse
        self.free_on(X2)
        self.control.state.selected = 0
        out = self.focused_after('resize_press', 'resize_release')
        self.assertEqual(last(out, 'view')['selected'], 1)
        self.assertTrue(self.control.state.zoomed)  # the view still zooms in


class FocusDesktopTests(PointerBase):
    def test_selects_focuses_off_the_loop_and_zooms_in(self):
        out = frames(self.control.focus_desktop(2))
        self.control.join_focus(2)
        self.assertEqual(self.focused, ['XR-3'])  # once: the zoom-in's own focus, as Ctrl+Alt+Up
        self.assertEqual(last(out, 'view')['selected'], 2)
        self.assertTrue(last(out, 'view')['zoomed']); self.assertTrue(self.control.state.zoomed)

    def test_bad_desktops_do_nothing(self):
        for bad in (-1, 3, '1', None, True, 1.0):
            with self.subTest(bad=bad): self.assertEqual(self.control.focus_desktop(bad), b'')
        self.control.join_focus(2)
        self.assertEqual(self.focused, [])
        self.assertEqual(self.control.state.selected, 0)

    def test_a_stale_click_while_zoomed_typing_or_playing_does_nothing(self):
        for gate in ('zoomed', 'typing', 'video'):
            with self.subTest(gate=gate):
                if gate == 'video': self.control.video_active = True
                else: setattr(self.control.state, gate, True)
                self.assertEqual(self.control.focus_desktop(2), b'')
                self.control.video_active = self.control.state.zoomed = self.control.state.typing = False
        self.control.join_focus(2)
        self.assertEqual(self.focused, [])
        self.assertEqual(self.control.state.selected, 0)

    def test_the_focus_command_is_one_the_compositor_client_accepts(self):
        for name in self.control.state.names:  # managed.view_controls: focus = client.mutate(view.focus_command(name))
            with self.subTest(name=name): HyprlandClient._check_mutation(view.focus_command(name))


if __name__ == '__main__':
    unittest.main()
