"""Overview click-to-select (anchor-no-click-maximize, 2026-11-03): a click with the circle cursor (the free pointer's
dot) on a desktop selects it and stays in the overview, anchored (view.ViewControl.select_desktop, from
free_pointer.OverviewPointer.on_op). It never maximizes: only Ctrl+Alt+Up (zoom_in) does. The real cursor comes back
onto the clicked desktop under the dot (desktop mode), and that warp is what gives its monitor Hyprland's focus: the
click asks for no monitor focus of its own, which would warp the cursor away from the dot. Clicks on anything else
(pills, corners, the help card, empty space) and clicks that only wake the idle dot select nothing.

Until this change a click zoomed into the desktop as Ctrl+Alt+Up does (click-zoom-fix, 2026-11-02); Cody asked for the
click to stop maximizing.

Offline only: fake clock, the free-pointer test rig, focus requests recorded in a list. No compositor, renderer binary
or glasses.
"""
import unittest

from tools.native_spike import free_pointer as fp, view
from tools.native_spike.geometry import DEFAULT_PLACEMENT
from tools.native_spike.hyprland import HyprlandClient

from test_free_pointer import G, T1, X1, X2, PointerBase, frames, last


class ClickSelectTests(PointerBase):
    def free_on(self, x):
        """Push the cursor against the top edge of the monitor at layout x: free mode, the dot on that desktop's top row."""
        self.at(x + 960, 540); self.at(x + 960, 0)
        self.assertEqual(self.p.mode, 'free')

    def focused_after(self, *ops):
        out = [f for op in ops for f in self.op(op)]
        self.control.join_focus(2)
        return out

    def test_a_click_on_a_desktop_selects_it_and_stays_in_the_overview(self):
        self.free_on(X2)
        self.assertEqual(self.p.hover[:2], ('desktop', 1))
        self.control.state.selected = 0  # e.g. Ctrl+Alt+Left since the dot came up here
        self.focused_after('resize_press')  # the press alone: maybe a window drag (on hold), nothing selected yet
        self.assertEqual(self.control.state.selected, 0)
        out = self.focused_after('resize_release')
        self.assertEqual(self.control.state.selected, 1)
        self.assertFalse(self.control.state.zoomed)  # never maximized by a click
        self.assertEqual(self.control.state.mode, 'anchor')
        self.assertEqual([(f['payload']['selected'], f['payload']['zoomed']) for f in out if f['op'] == 'view'], [(1, False)])
        self.assertEqual(self.focused, [])  # no focus request: the cursor's own warp focuses the monitor (below)
        self.assertEqual(self.p.focus_clicks, 1)
        self.assertTrue(self.p.overview())  # the free pointer, pills and corners stay up

    def test_the_cursor_comes_back_under_the_dot_on_the_clicked_desktop(self):
        self.free_on(X2)
        self.control.state.selected = 0
        self.focused_after('resize_press', 'resize_release')
        self.assertEqual((self.p.mode, self.p.desktop), ('desktop', 1))
        self.assertEqual(self.p.want[1:], ('desktop', (X2 + 960, fp.INSET_PX)))  # at the click, on XR-2
        out = self.at(X2 + 960, fp.INSET_PX)  # the sampler warped it there: it stays the pointer on XR-2
        self.assertEqual(self.p.mode, 'desktop')
        self.assertFalse(any(f['op'] == 'view' for f in out))
        self.assertEqual(self.focused_after('resize_press', 'resize_release'), [])  # the next click is the window's
        self.assertEqual(self.p.focus_clicks, 1)
        self.assertFalse(self.control.state.zoomed)

    def test_a_click_on_the_selected_desktop_hands_the_cursor_back_and_changes_no_view(self):
        self.free_on(X2)
        self.assertEqual(self.control.state.selected, 1)  # moving into XR-2 selected it already
        out = self.focused_after('resize_press', 'resize_release')
        self.assertFalse(any(f['op'] == 'view' for f in out))  # nothing changed in the view
        self.assertFalse(self.control.state.zoomed)
        self.assertEqual(self.focused, [])
        self.assertEqual(self.p.want[1:], ('desktop', (X2 + 960, fp.INSET_PX)))

    def test_a_click_on_another_desktop_than_the_one_the_dot_left_selects_the_clicked_one(self):
        # The cursor runs off XR-1's right edge: free, the hidden cursor parked on XR-1. The dot then crosses just onto
        # XR-2 (less than ENTER_PX in, so still free) and clicks there: XR-2 is selected and the cursor shows on XR-2,
        # not on the parking monitor.
        self.at(X1 + 1900, 540); self.at(X2 + 6, 540)
        self.assertEqual((self.p.mode, self.p.desktop), ('free', 0))
        self.move_canvas(fp.project(G, T1, DEFAULT_PLACEMENT, T1[0] + 1, T1[1] + T1[3] / 2)[0] - self.p.pointer[0], 0)
        self.assertEqual((self.p.mode, self.p.hover[:2]), ('free', ('desktop', 1)))
        self.focused_after('resize_press', 'resize_release')
        self.assertEqual(self.control.state.selected, 1)
        self.assertFalse(self.control.state.zoomed)
        self.assertEqual((self.p.mode, self.p.desktop), ('desktop', 1))
        self.assertEqual(self.p.want[2][0] // 1920, X2 // 1920)  # the cursor goes to XR-2, not the parking monitor

    def test_each_click_selects_the_desktop_it_lands_on(self):
        self.free_on(X1)
        self.control.state.selected = 2
        self.focused_after('resize_press', 'resize_release')
        self.assertEqual(self.control.state.selected, 0)
        self.free_on(X2)
        self.control.state.selected = 0
        self.focused_after('resize_press', 'resize_release')
        self.assertEqual(self.control.state.selected, 1)
        self.assertFalse(self.control.state.zoomed)
        self.assertEqual(self.focused, [])

    def test_ctrl_alt_up_after_a_click_maximizes_the_clicked_desktop(self):
        self.free_on(X2)
        self.control.state.selected = 0
        self.focused_after('resize_press', 'resize_release')
        self.assertFalse(self.control.state.zoomed)
        out = self.focused_after('zoom_in')  # Ctrl+Alt+Up: the one way to maximize
        self.assertTrue(self.control.state.zoomed)
        self.assertEqual(self.control.state.selected, 1)
        self.assertEqual(self.control.state.mode, 'follow')
        self.assertEqual((last(out, 'view')['selected'], last(out, 'view')['zoomed']), (1, True))
        self.assertEqual(self.focused, ['XR-2'])  # the zoom-in's own focus
        self.control.poll()
        self.assertFalse(self.p.overview())  # out of the overview: the free pointer is off

    def test_a_click_outside_an_open_panel_closes_it_and_still_selects(self):
        self.control.pill_click(0)
        self.assertTrue(self.control.state.menu_open)
        self.free_on(X2)
        self.control.state.selected = 0
        self.focused_after('resize_press')
        self.assertFalse(self.control.state.menu_open)  # the press collapses it
        self.focused_after('resize_release')
        self.assertEqual(self.control.state.selected, 1)
        self.assertFalse(self.control.state.zoomed)

    def test_a_click_in_empty_space_selects_nothing(self):
        self.free_on(X1)
        self.control.state.selected = 2
        self.move(0, -300)
        self.assertIsNone(self.p.hover)
        out = self.focused_after('resize_press', 'resize_release')
        self.assertEqual(self.focused, [])
        self.assertEqual(self.control.state.selected, 2)
        self.assertFalse(any(f['op'] == 'view' for f in out))
        self.assertEqual(self.p.mode, 'free')

    def test_a_click_on_a_pill_opens_it_and_selects_nothing(self):
        self.on_pill(0)
        self.control.state.selected = 2
        self.focused_after('resize_press', 'resize_release')
        self.assertTrue(self.control.state.menu_open)
        self.assertEqual(self.focused, [])
        self.assertEqual(self.control.state.selected, 2)
        self.assertFalse(self.control.state.zoomed)

    def test_a_press_on_a_corner_resizes_and_selects_nothing(self):
        self.free_at_top(lx=1919)  # XR-1's top-right corner, its zone reaching outside the desktop
        self.move_canvas(4, -4)
        self.assertEqual(self.p.hover[0], 'corner')
        self.control.state.selected = 2
        self.focused_after('resize_press', 'resize_release')
        self.assertEqual(self.focused, [])
        self.assertEqual(self.control.state.selected, 2)

    def test_a_click_on_the_help_card_over_a_desktop_selects_nothing(self):
        self.op('help')
        self.control.set_help_geometry({'card': (560, 300, 800, 600)})  # over the middle desktop
        self.at(X2 + 960, 540)  # under the card: the dot, not the cursor
        self.assertEqual(self.p.hover, ('help', 'card'))
        self.control.state.selected = 0
        self.focused_after('resize_press', 'resize_release')
        self.assertEqual(self.focused, [])
        self.assertEqual(self.control.state.selected, 0)

    def test_a_click_that_only_wakes_the_idle_dot_selects_nothing(self):
        self.free_on(X2)
        self.control.state.selected = 0
        self.clock.now += view.OVERLAY_IDLE_CLOSE_S + 1
        self.control.poll()
        self.focused_after('resize_press', 'resize_release')
        self.assertEqual(self.focused, [])
        self.assertEqual(self.control.state.selected, 0)
        self.assertEqual(self.p.mode, 'free')

    def test_the_click_never_asks_the_compositor_for_monitor_focus(self):
        # A monitor focus would warp the cursor to that monitor's last window, away from the dot, and it would block or
        # fail off the loop anyway. The click needs none: the cursor's warp under the dot focuses the monitor.
        calls = []
        def refuse(name):
            calls.append(name)
            raise OSError('compositor gone')
        self.control.focus = refuse
        self.free_on(X2)
        self.control.state.selected = 0
        out = self.focused_after('resize_press', 'resize_release')
        self.assertEqual(last(out, 'view')['selected'], 1)
        self.assertFalse(last(out, 'view')['zoomed'])
        self.assertEqual(calls, [])


class SelectDesktopTests(PointerBase):
    def test_selects_without_maximizing_or_focusing(self):
        out = frames(self.control.select_desktop(2))
        self.control.join_focus(2)
        self.assertEqual(self.focused, [])
        self.assertEqual([f['op'] for f in out], ['view'])  # one view frame: the highlight moves
        self.assertEqual(last(out, 'view'), {**self.control.state.view(), 'selected': 2, 'zoomed': False})
        self.assertFalse(self.control.state.zoomed)
        self.assertEqual(self.control.state.selected, 2)
        self.assertEqual(self.control.state.mode, 'anchor')

    def test_the_same_frame_as_the_overview_arrow_keys(self):
        keyed = frames(self.control.on_message({'op': 'next', 'payload': {}}), ('view',))  # Ctrl+Alt+Right: XR-2
        self.control.state.selected = 0
        clicked = frames(self.control.select_desktop(1))
        self.assertEqual([f['payload'] for f in clicked], [f['payload'] for f in keyed])

    def test_the_selected_desktop_again_sends_nothing(self):
        self.assertEqual(self.control.select_desktop(0), b'')
        self.assertEqual(self.control.state.selected, 0)

    def test_only_ctrl_alt_up_maximizes(self):
        self.control.select_desktop(2)
        self.assertFalse(self.control.state.zoomed)
        out = frames(self.control.on_message({'op': 'zoom_in', 'payload': {}}))
        self.control.join_focus(2)
        self.assertTrue(self.control.state.zoomed)
        self.assertEqual((last(out, 'view')['selected'], last(out, 'view')['zoomed']), (2, True))
        self.assertEqual(self.focused, ['XR-3'])  # the zoom-in's own focus, as before

    def test_bad_desktops_do_nothing(self):
        for bad in (-1, 3, '1', None, True, 1.0):
            with self.subTest(bad=bad): self.assertEqual(self.control.select_desktop(bad), b'')
        self.control.join_focus(2)
        self.assertEqual(self.focused, [])
        self.assertEqual(self.control.state.selected, 0)

    def test_a_stale_click_while_zoomed_typing_or_playing_does_nothing(self):
        for gate in ('zoomed', 'typing', 'video'):
            with self.subTest(gate=gate):
                if gate == 'video': self.control.video_active = True
                else: setattr(self.control.state, gate, True)
                self.assertEqual(self.control.select_desktop(2), b'')
                self.control.video_active = self.control.state.zoomed = self.control.state.typing = False
        self.control.join_focus(2)
        self.assertEqual(self.focused, [])
        self.assertEqual(self.control.state.selected, 0)

    def test_there_is_no_click_to_maximize_path_left(self):
        self.assertFalse(hasattr(self.control, 'focus_desktop'))  # the old select-and-zoom click path

    def test_the_focus_command_is_one_the_compositor_client_accepts(self):
        for name in self.control.state.names:  # managed.view_controls: focus = client.mutate(view.focus_command(name))
            with self.subTest(name=name): HyprlandClient._check_mutation(view.focus_command(name))


if __name__ == '__main__':
    unittest.main()
