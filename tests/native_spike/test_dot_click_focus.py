"""Dot-cursor click under the input lock (input_lock.LockedPointer; the run now wires the unified cursor, whose free mode
grabs like this one: test_unified_cursor.py) and with desktops placed anywhere round you (3DoF orbit, always facing
you): a click with the dot wherever a desktop is shown zooms into that desktop, leaving the overview, exactly as
Ctrl+Alt+Up does on the selected desktop.

Regressions:
  * click-zoom-fix (2026-11-02, installed master 80fab06): a dot click on a monitor in the overview did not zoom into it.
    The click (resize_press from the lock's left-button bind -> OverviewPointer.on_op) only reached
    ViewControl.focus_desktop, which selected the desktop and asked Hyprland to focus its monitor but kept the view in
    the overview; the input lock then re-parked the hidden, held cursor on the clicked monitor and kept the capture.
    Nothing on the click path ever sent the view to zoom_in, which only Ctrl+Alt+Up did. Now the click zooms in through
    that same path, the lock lets go, and the cursor shows under the dot on the clicked monitor.
  * dot-click-focus (2026-11-02): past the view in anchor mode a desktop visibly under the dot lost the click to another
    desktop's pill found only along the stretched drag direction. What is under the dot wins; the stretched pill is the
    fallback. A click (no resize) on the inner half of a desktop's corner zone, which is on the desktop, counts as a
    click on that desktop.

Offline only: fake clock, the free-pointer test rig, focus requests recorded in a list; the sampler tests run the real
LockedSampler thread against a fake compositor client. No compositor or glasses.
"""
import math
import time
import unittest

from tools.native_spike import free_pointer as fp, input_lock as il, orbit, view
from tools.native_spike.geometry import DEFAULT_PLACEMENT

from test_free_pointer import G, NAMES, OUTPUTS, T0, T1, X1, X2, X3, frames, last
from test_input_lock import BIND_ARGS, FakeLockClient, LockBase

T2 = G['tiles'][2]


def centre(tile):
    return (tile[0] + tile[2] / 2, tile[1] + tile[3] / 2)


class LockedClickZoomTests(LockBase):
    def click(self):
        out = self.op('resize_press') + self.op('resize_release')  # the lock's left-button bind, press then release
        self.control.join_focus(2)
        return out

    def dot_to(self, point):
        return self.move_canvas(point[0] - self.p.pointer[0], point[1] - self.p.pointer[1])

    def back_to_overview(self, cursor):
        """A tick while zoomed in (the pointer goes off), Ctrl+Alt+Down, then the next sample: captured again there."""
        self.at(*cursor)
        self.assertEqual(self.p.mode, 'off')
        self.op('zoom_out')
        self.at(*cursor)
        self.assertEqual(self.p.mode, 'free')

    def test_a_dot_click_on_a_monitor_zooms_into_it(self):
        # As Cody sees it: the cursor comes onto XR-1 in the overview and is captured (hidden, held there); the dot goes
        # onto XR-2 and clicks. Before the fix the view stayed in the overview (zoomed False) and the lock kept the mouse.
        self.at(X1 + 960, 540)
        self.assertEqual(self.p.want[1:], ('free', (X1 + 960, 540)))
        q = (T1[0] + T1[2] * 0.25, T1[1] + T1[3] * 0.75)
        self.dot_to(q)  # XR-2 is at its slot, so its tile pixel is the canvas point
        self.assertEqual(self.p.hover[:2], ('desktop', 1))
        out = self.click()
        self.assertTrue(self.control.state.zoomed)  # zoomed in: out of the overview
        self.assertEqual(self.control.state.selected, 1)  # into the clicked desktop, not the one the dot came from
        views = [f['payload'] for f in out if f['op'] == 'view']
        self.assertEqual(len(views), 1)  # one view frame, as Ctrl+Alt+Up sends
        self.assertTrue(views[0]['zoomed']); self.assertEqual(views[0]['selected'], 1)
        self.assertEqual(self.focused, ['XR-2'])  # its monitor gets Hyprland's focus, once
        self.assertEqual(self.p.focus_clicks, 1)
        at = self.p.release_at  # where the cursor shows once the lock lets go: on XR-2, under the dot
        self.assertEqual(self.p._at(at)[0], 1)
        self.assertLessEqual(abs(at[0] - (X2 + 0.25 * 1920)), 2); self.assertLessEqual(abs(at[1] - 0.75 * 1080), 2)
        self.at(*self.cur)  # the next supervisor tick: the overview is gone, so is the lock
        self.assertEqual(self.p.mode, 'off')
        self.assertEqual(self.p.want[1], 'off')
        self.assertFalse(self.p.overview())

    def test_the_click_zooms_in_like_ctrl_alt_up_on_the_clicked_desktop(self):
        self.at(X1 + 960, 540)
        self.dot_to(centre(T2))
        clicked = [f for f in self.click() if f['op'] == 'view']
        clicked_focus = list(self.focused)
        state = self.control.state.view()
        self.back_to_overview((X1 + 960, 540))
        self.focused.clear()
        self.control.state.selected = 2
        keyed = self.op('zoom_in', ops=('view',))
        self.control.join_focus(2)
        self.assertEqual(self.control.state.view(), state)
        self.assertEqual([f['payload'] for f in clicked], [f['payload'] for f in keyed])
        self.assertEqual(clicked_focus, self.focused)  # the same Hyprland focus, XR-3

    def test_a_click_with_a_panel_open_collapses_it_then_zooms_in(self):
        self.on_pill(0)
        self.op('resize_press'); self.op('resize_release')  # a pill click: XR-1's panel opens
        self.assertTrue(self.control.state.menu_open)
        self.dot_to(centre(T1))
        self.click()
        self.assertFalse(self.control.state.menu_open)  # collapsed (saved) first, as Ctrl+Alt+Up does
        self.assertTrue(self.control.state.zoomed)
        self.assertEqual(self.control.state.selected, 1)

    def test_each_click_zooms_into_the_monitor_it_lands_on(self):
        for tile, name, index in ((T0, 'XR-1', 0), (T2, 'XR-3', 2), (T1, 'XR-2', 1)):
            self.back_to_overview((X2 + 960, 540)) if self.control.state.zoomed else self.at(X2 + 960, 540)
            self.dot_to(centre(tile))
            self.click()
            with self.subTest(name=name):
                self.assertTrue(self.control.state.zoomed)
                self.assertEqual(self.control.state.selected, index)
                self.assertEqual(self.focused[-1], name)
                self.assertEqual(self.p._at(self.p.release_at)[0], index)
        self.assertEqual(self.focused, ['XR-1', 'XR-3', 'XR-2'])

    def test_a_desktop_placed_round_you_is_clicked_where_it_is_drawn(self):
        # 3DoF: XR-2 lifted 15 degrees and turned 5 to the right, facing you. Its slot is empty now.
        placed = {**DEFAULT_PLACEMENT, 'x': 50, 'y': 150}
        self.control.state.placements = [dict(DEFAULT_PLACEMENT), placed, dict(DEFAULT_PLACEMENT)]
        self.at(X1 + 960, 540)
        self.dot_to(centre(T1))  # where XR-2 used to be: nothing there
        self.assertIsNone(self.p.hover)
        self.click()
        self.assertEqual(self.focused, [])
        self.assertFalse(self.control.state.zoomed)  # empty space: nothing
        q = (T1[0] + T1[2] * 0.7, T1[1] + T1[3] * 0.4)
        self.dot_to(fp.project(G, T1, placed, *q, around=True))  # on XR-2 as drawn, billboarded toward you
        self.assertEqual(self.p.hover[:2], ('desktop', 1))
        self.assertAlmostEqual(self.p.hover[2][0], q[0], delta=0.05); self.assertAlmostEqual(self.p.hover[2][1], q[1], delta=0.05)
        self.click()
        self.assertEqual(self.focused, ['XR-2'])
        self.assertTrue(self.control.state.zoomed); self.assertEqual(self.control.state.selected, 1)
        at = self.p.release_at
        self.assertEqual(self.p._at(at)[0], 1)
        self.assertLessEqual(abs(at[0] - (X2 + 0.7 * 1920)), 2); self.assertLessEqual(abs(at[1] - 0.4 * 1080), 2)

    def test_past_the_view_what_is_hit_is_what_the_dot_is_drawn_on(self):
        # Anchor mode. Past the view the dot is drawn in its stretched direction round you (orbit.pointer_direction, the
        # frame's az / el), and the hit test reads that same direction. XR-1 sits about 76 degrees round, XR-3 15
        # degrees: the dot drawn on XR-3 clicks XR-3, and the dot drawn on XR-1's pill is on that pill.
        placements = [{**DEFAULT_PLACEMENT, 'x': 760, 'y': 45, 'distance': 80}, dict(DEFAULT_PLACEMENT),
                      {**DEFAULT_PLACEMENT, 'x': 150}]
        self.control.state.placements = placements
        self.assertEqual(self.control.state.mode, 'anchor')
        target = fp.project(G, T2, placements[2], *centre(T2), around=True)
        self.assertTrue(orbit.beyond_view(G, target))
        self.assertEqual(fp.hit(G, placements, target, around=True)[:2], ('desktop', 2))
        pill = fp.pill_rect(T0)
        on_pill = fp.project(G, T0, placements[0], pill[0] + pill[2] / 2, pill[1] + pill[3] / 2, around=True)
        self.assertTrue(orbit.beyond_view(G, on_pill))
        self.assertEqual(fp.hit(G, placements, on_pill, around=True), ('pill', 0))
        self.at(X3 + 960, 540)
        self.dot_to(target)
        self.assertEqual(self.p.hover[:2], ('desktop', 2))
        dot = self.p.sent  # the dot as last sent to the renderer
        az, el = orbit.pointer_direction(G, self.p.pointer, True)
        self.assertEqual((dot['az'], dot['el']), (round(math.degrees(az) * 100), round(math.degrees(el) * 100)))
        seen = orbit.place_point(G, T2, placements[2], *centre(T2))  # the dot is drawn on XR-3's middle
        self.assertAlmostEqual(math.degrees(math.atan2(seen[0], -seen[2])), dot['az'] / 100, delta=0.02)
        self.click()
        self.assertEqual(self.focused, ['XR-3'])
        self.assertTrue(self.control.state.zoomed); self.assertEqual(self.control.state.selected, 2)
        self.assertEqual(self.control.state.placements, placements)  # no pill drag
        self.assertFalse(self.control.state.menu_open)  # nor a pill click

    def test_a_click_on_the_inner_half_of_a_corner_zooms_in_and_a_drag_there_still_resizes(self):
        self.at(X2 + 960, 540)
        inner = (T0[0] + T0[2] - 4, T0[1] + 4)  # XR-1's top-right corner zone, on the desktop
        self.dot_to(inner)
        self.assertEqual(self.p.hover, ('corner', 0, 'tr'))
        self.op('resize_press'); self.op('resize_release'); self.op('resize_press')
        self.op('resize_release')  # the first click zoomed in; the rest are stale (no free pointer while zoomed)
        self.control.join_focus(2)
        self.assertEqual(self.focused, ['XR-1'])
        self.assertTrue(self.control.state.zoomed); self.assertEqual(self.control.state.selected, 0)
        self.assertEqual(self.control.state.placements[0], DEFAULT_PLACEMENT)
        self.assertEqual(self.p._at(self.p.release_at)[0], 0)
        self.assertIsNone(self.p.drag)
        self.back_to_overview((X2 + 960, 540))
        self.dot_to(inner)
        self.op('resize_press'); self.move_canvas(30, -30); self.op('resize_release')  # a resize is not a click
        self.control.join_focus(2)
        self.assertLess(self.control.state.placements[0]['distance'], 100)
        self.assertFalse(self.control.state.zoomed)
        self.assertEqual(self.focused, ['XR-1'])
        corner = fp.project(G, T0, self.control.state.placements[0], T0[0] + T0[2], T0[1])  # the resized desktop's
        self.dot_to((corner[0] + 4, corner[1] - 4))  # the outer half, off the desktop: a click there is nothing
        self.assertEqual(self.p.hover[:2], ('corner', 0))
        self.click()
        self.assertEqual(self.focused, ['XR-1'])
        self.assertFalse(self.control.state.zoomed)

    def test_a_new_capture_forgets_an_old_click(self):
        self.at(X1 + 960, 540)
        self.dot_to(centre(T1))
        self.click()
        self.assertIsNotNone(self.p.release_at)
        self.back_to_overview((X1 + 960, 540))
        self.assertIsNone(self.p.release_at)  # a later zoom-in by key shows the cursor where it is


class ClickZoomSamplerTests(unittest.TestCase):
    """The installed path end to end: ViewControl, LockedPointer and the real LockedSampler thread (as managed.py wires
    them) against a fake compositor client."""
    def setUp(self):
        self.focused = []
        self.control = view.ViewControl(view.ViewState(NAMES), 'run', 'session', self.focused.append, None, None)
        self.addCleanup(self.control.join_focus, 2)
        self.addCleanup(self.control.stop_menu_binds)
        self.p = self.control.overview_pointer = il.LockedPointer(self.control); self.addCleanup(self.p.close)
        self.p.set_geometry(G)

    def wait(self, predicate, timeout=2.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.control.poll()
            if predicate(): return True
            time.sleep(0.003)
        return predicate()

    def test_a_dot_click_zooms_in_and_the_cursor_shows_on_the_clicked_monitor_under_the_dot(self):
        client = FakeLockClient(cursor=(X1 + 960, 540))
        sampler = il.LockedSampler(client, NAMES, self.p, BIND_ARGS).start(); self.addCleanup(sampler.stop)
        self.assertTrue(self.wait(lambda: sampler.grabbed and self.p.mode == 'free'))  # captured, held on XR-1
        q = (T1[0] + T1[2] * 0.25, T1[1] + T1[3] * 0.75)
        self.p.pointer = q  # the dot onto XR-2 (hand motion; driving it there sample by sample adds nothing here)
        out = frames(self.control.on_message({'op': 'resize_press', 'payload': {}}))
        self.control.on_message({'op': 'resize_release', 'payload': {}})
        self.assertTrue(last(out, 'view')['zoomed'])
        self.assertTrue(self.wait(lambda: client.count('release') == 1))
        self.control.join_focus(2)
        self.assertEqual(self.focused, ['XR-2'])
        (at,) = client.args('release')
        self.assertEqual(self.p._at(at)[0], 1)  # shown on XR-2, not held on XR-1 where focus-follows-mouse would win
        self.assertLessEqual(abs(at[0] - (X2 + 0.25 * 1920)), 2); self.assertLessEqual(abs(at[1] - 0.75 * 1080), 2)
        self.assertFalse(sampler.grabbed)  # the lock let go: binds removed, cursor visible
        self.assertIsNone(self.p.release_at)
        time.sleep(0.05)
        self.assertEqual(client.count('lock'), 1)  # never grabbed again while zoomed in
        self.assertEqual(client.count('release'), 1)


if __name__ == '__main__':
    unittest.main()
