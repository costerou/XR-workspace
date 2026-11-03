"""Overview click-to-select (anchor-no-click-maximize, 2026-11-03): moving the circle cursor (the free pointer's dot)
onto a desktop selects it and hands it the cursor; where the dot is still free over a desktop's content (next to the help
card) a click selects it and stays in the overview, anchored (view.ViewControl.select_desktop, from
free_pointer.OverviewPointer.on_op). A desktop's edge is its resize edge: a click there selects nothing. Nothing
maximizes but Ctrl+Alt+Up (zoom_in). The real cursor comes back
onto the clicked desktop under the dot (desktop mode), and that warp is what gives its monitor Hyprland's focus: the
click asks for no monitor focus of its own, which would warp the cursor away from the dot. Clicks on anything else
(pills, corners, the help card, empty space) and clicks that only wake the idle dot select nothing.

Until this change a click zoomed into the desktop as Ctrl+Alt+Up does (click-zoom-fix, 2026-11-02); Cody asked for the
click to stop maximizing.

Offline only: fake clock, the free-pointer test rig, focus requests recorded in a list. No compositor, renderer binary
or glasses.
"""
import unittest

from tools.native_spike import free_pointer as fp, orbit, view
from tools.native_spike.geometry import DEFAULT_PLACEMENT
from tools.native_spike.hyprland import HyprlandClient

from test_free_pointer import G, OUTPUTS, T1, X1, X2, PointerBase, frames, last


class ClickSelectTests(PointerBase):
    def free_on(self, x):
        """Push the cursor against the top edge of the monitor at layout x: free mode, the dot just above that desktop's
        top edge, on its resize edge."""
        self.at(x + 960, 540); self.at(x + 960, 0)
        self.assertEqual(self.p.mode, 'free')

    def free_by_card(self, i, q):
        """The free dot over desktop i's content at tile pixel q, kept free by the open help card 1 px to its right (the
        dot enters a desktop only HELP_CLEAR_PX clear of the card): the one place a click lands on content in free mode."""
        tile = G['tiles'][i]
        self.op('help')
        dot = fp.project(G, tile, DEFAULT_PLACEMENT, *q)
        plane = orbit.plane_at(G, orbit.pointer_ray(G, dot))
        self.control.set_help_geometry({'card': (plane[0] + 1, plane[1] - 60, 300, 120)})
        self.free_on(X1)
        self.move_canvas(dot[0] - self.p.pointer[0], dot[1] - self.p.pointer[1])
        self.assertEqual((self.p.mode, self.p.hover[:2]), ('free', ('desktop', i)))

    def focused_after(self, *ops):
        out = [f for op in ops for f in self.op(op)]
        self.control.join_focus(2)
        return out

    def test_at_a_desktops_edge_the_dot_is_on_its_resize_edge_and_a_click_there_selects_nothing(self):
        self.free_on(X2)
        self.assertEqual(self.p.hover, ('edge', 1, 't'))
        self.control.state.selected = 0
        out = self.focused_after('resize_press', 'resize_release')
        self.assertEqual(self.control.state.selected, 0)
        self.assertFalse(self.control.state.zoomed)
        self.assertFalse(any(f['op'] in ('view', 'placement') for f in out))
        self.assertEqual((self.focused, self.p.focus_clicks), ([], 0))
        self.assertEqual(self.control.state.placements[1], DEFAULT_PLACEMENT)  # no move: a click resizes nothing

    def test_moving_onto_a_desktop_selects_it_and_hands_it_the_cursor_without_a_click(self):
        self.free_on(X2)
        self.control.state.selected = 0
        out = self.move_canvas(0, 40)  # down into XR-2
        self.assertEqual((self.p.mode, self.p.desktop, self.control.state.selected), ('desktop', 1, 1))
        self.assertEqual((last(out, 'view')['selected'], last(out, 'view')['zoomed']), (1, False))
        self.assertEqual(self.p.want[1], 'desktop')
        self.assertEqual(self.p._at(self.p.want[2])[0], 1)  # the cursor goes onto XR-2, under the dot
        self.assertEqual(self.focused, [])
        self.assertTrue(self.p.overview())

    def test_ctrl_alt_up_after_moving_onto_a_desktop_maximizes_it(self):
        self.free_on(X2)
        self.control.state.selected = 0
        self.move_canvas(0, 40)
        out = self.focused_after('zoom_in')  # Ctrl+Alt+Up: the one way to maximize
        self.assertTrue(self.control.state.zoomed)
        self.assertEqual(self.control.state.mode, 'follow')
        self.assertEqual((last(out, 'view')['selected'], last(out, 'view')['zoomed']), (1, True))
        self.assertEqual(self.focused, ['XR-2'])  # the zoom-in's own focus
        self.control.poll()
        self.assertFalse(self.p.overview())

    def test_a_click_on_content_the_dot_is_free_over_selects_it_and_stays_in_the_overview(self):
        q = (T1[0] + T1[2] * 0.3, T1[1] + T1[3] * 0.4)
        self.free_by_card(1, q)
        self.control.state.selected = 0
        self.focused_after('resize_press')  # the press alone: nothing yet
        self.assertEqual(self.control.state.selected, 0)
        out = self.focused_after('resize_release')
        self.assertEqual(self.control.state.selected, 1)
        self.assertFalse(self.control.state.zoomed)  # never maximized by a click
        self.assertEqual([(f['payload']['selected'], f['payload']['zoomed']) for f in out if f['op'] == 'view'], [(1, False)])
        self.assertEqual(self.focused, [])
        self.assertEqual(self.p.focus_clicks, 1)
        self.assertEqual((self.p.mode, self.p.desktop), ('desktop', 1))
        self.assertEqual(self.p.want[1:], ('desktop', fp.surface_to_layout(T1, OUTPUTS[1], q)))  # the cursor under the dot

    def test_a_click_on_another_desktop_selects_it_and_the_panel_goes_there(self):
        self.control.pill_click(0)
        q = (T1[0] + T1[2] * 0.3, T1[1] + T1[3] * 0.4)
        self.free_by_card(1, q)
        self.assertTrue(self.control.state.menu_open)
        self.control.state.selected = 0
        self.focused_after('resize_press')
        self.assertEqual((self.control.state.menu_open, self.control.state.menu_desktop, self.p.outside_closes), (True, 0, 0))  # not a click outside
        self.focused_after('resize_release')
        self.assertEqual(self.control.state.selected, 1)
        self.assertEqual((self.control.state.menu_open, self.control.state.menu_desktop), (True, 1))
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
        q = (T1[0] + T1[2] * 0.3, T1[1] + T1[3] * 0.4)
        self.free_by_card(1, q)
        self.control.state.selected = 0
        self.clock.now += view.OVERLAY_IDLE_CLOSE_S + 1
        self.control.poll()
        self.focused_after('resize_press', 'resize_release')
        self.assertEqual(self.focused, [])
        self.assertEqual(self.control.state.selected, 0)
        self.assertEqual(self.p.mode, 'free')

    def test_the_click_never_asks_the_compositor_for_monitor_focus(self):
        # A monitor focus would warp the cursor to that monitor's last window, away from the dot, and it would block or
        # fail off the loop anyway. The click needs none: the cursor's move under the dot does it (unified_cursor).
        calls = []
        def refuse(name):
            calls.append(name)
            raise OSError('compositor gone')
        self.control.focus = refuse
        self.free_by_card(1, (T1[0] + T1[2] * 0.3, T1[1] + T1[3] * 0.4))
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
