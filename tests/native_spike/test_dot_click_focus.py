"""Dot-cursor click focus under the input lock (the run's pointer, input_lock.LockedPointer) and with desktops placed
anywhere round you (3DoF orbit, always facing you): a click with the dot wherever it is shown on a desktop focuses that
desktop's monitor, and the focus stays there.

Regressions (dot-click-focus, 2026-11-02):
  * The click asked Hyprland to focus the clicked monitor but left the hidden, held cursor on the monitor the dot came
    from, so focus-follows-mouse gave that monitor the focus straight back on the next hand motion. The held cursor now
    moves to the clicked monitor, under the dot.
  * Past the view in anchor mode a desktop visibly under the dot lost the click to another desktop's pill found only
    along the stretched drag direction. What is under the dot now wins; the stretched pill is the fallback.
  * A click (no resize) on the inner half of a desktop's corner zone, which is on the desktop, focused nothing.

Offline only: fake clock, the free-pointer test rig, focus requests recorded in a list. No compositor or glasses.
"""
import math
import unittest

from tools.native_spike import free_pointer as fp, input_lock as il, orbit
from tools.native_spike.geometry import DEFAULT_PLACEMENT

from test_free_pointer import G, OUTPUTS, T0, T1, X1, X2, X3, last
from test_input_lock import LockBase

T2 = G['tiles'][2]


def centre(tile):
    return (tile[0] + tile[2] / 2, tile[1] + tile[3] / 2)


class LockedClickFocusTests(LockBase):
    def click(self):
        out = self.op('resize_press') + self.op('resize_release')
        self.control.join_focus(2)
        if self.p.want[1] == 'free': self.cur = self.p.want[2]  # the sampler parks the hidden cursor there
        return out

    def dot_to(self, point):
        return self.move_canvas(point[0] - self.p.pointer[0], point[1] - self.p.pointer[1])

    def test_the_held_cursor_moves_to_the_clicked_monitor_under_the_dot(self):
        self.at(X1 + 960, 540)  # captured on XR-1: the hidden cursor is held there
        self.assertEqual(self.p.want[1:], ('free', (X1 + 960, 540)))
        q = (T1[0] + T1[2] * 0.25, T1[1] + T1[3] * 0.75)
        self.dot_to(q)  # the dot onto XR-2 (XR-2 is at its slot, so its tile pixel is the canvas point)
        self.assertEqual(self.p.hover[:2], ('desktop', 1))
        pointer = self.p.pointer
        self.click()
        self.assertEqual(self.focused, ['XR-2'])
        self.assertEqual(self.control.state.selected, 1)
        self.assertEqual(self.p.mode, 'free')  # still the lock: no hand-back to the window
        _, kind, park = self.p.want
        self.assertEqual(kind, 'free')
        self.assertEqual(self.p._at(park)[0], 1)  # held on XR-2 now, not XR-1: focus-follows-mouse agrees
        self.assertLessEqual(abs(park[0] - (X2 + 0.25 * 1920)), 2); self.assertLessEqual(abs(park[1] - 0.75 * 1080), 2)
        self.assertEqual(self.p._sync, self.p.want[0])  # old samples (on XR-1) never count as a warp
        self.assertEqual(self.p.pointer, pointer)  # the dot stays where it clicked
        self.assertEqual((self.p.desktop, self.p.gain), (1, self.p._gain(1)))

    def test_hand_motion_after_the_click_keeps_the_focus_on_the_clicked_monitor(self):
        self.at(X1 + 960, 540)
        self.dot_to(centre(T1))
        self.click()
        park = self.p.want[2]
        serial = self.p.want[0]
        self.at(park[0] + il.LEASH_PX - 1, park[1])  # the hidden cursor strays within the leash, on XR-2
        self.move(5, 0)
        self.assertEqual(self.p.want[0], serial)
        self.assertEqual(self.p._at(self.cur)[0], 1)
        self.assertEqual(self.p.follows, 0)
        self.assertEqual(self.control.state.selected, 1)

    def test_hyprlands_focus_warp_after_the_click_stays_on_the_clicked_monitor(self):
        self.at(X1 + 960, 540)
        self.dot_to((T1[0] + 30, T1[1] + 30))
        self.click()
        pointer = self.p.pointer
        self.at(X2 + 960, 540)  # the focus dispatch warped the hidden cursor to XR-2's middle
        self.assertEqual(self.p._at(self.p.want[2])[0], 1)
        self.assertEqual(self.p.pointer, pointer)
        self.assertEqual(self.focused, ['XR-2'])

    def test_each_click_focuses_the_monitor_it_lands_on(self):
        self.at(X2 + 960, 540)
        for tile, name, index in ((T0, 'XR-1', 0), (T2, 'XR-3', 2), (T1, 'XR-2', 1)):
            self.dot_to(centre(tile))
            self.click()
            with self.subTest(name=name):
                self.assertEqual(self.focused[-1], name)
                self.assertEqual(self.p._at(self.p.want[2])[0], index)
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
        q = (T1[0] + T1[2] * 0.7, T1[1] + T1[3] * 0.4)
        self.dot_to(fp.project(G, T1, placed, *q))  # on XR-2 as drawn, billboarded toward you
        self.assertEqual(self.p.hover[:2], ('desktop', 1))
        self.assertAlmostEqual(self.p.hover[2][0], q[0], delta=0.05); self.assertAlmostEqual(self.p.hover[2][1], q[1], delta=0.05)
        self.click()
        self.assertEqual(self.focused, ['XR-2'])
        park = self.p.want[2]
        self.assertEqual(self.p._at(park)[0], 1)
        self.assertLessEqual(abs(park[0] - (X2 + 0.7 * 1920)), 2); self.assertLessEqual(abs(park[1] - 0.4 * 1080), 2)

    def test_past_the_view_the_desktop_under_the_dot_wins_over_a_stretched_pill(self):
        # Anchor mode. XR-3 turned 15 degrees right: its middle is past the view's edge but the dot reaches it. XR-1 sits
        # nearer, about 76 degrees round, its pill exactly where that dot's stretched drag direction points.
        placements = [{**DEFAULT_PLACEMENT, 'x': 760, 'y': 45, 'distance': 80}, dict(DEFAULT_PLACEMENT),
                      {**DEFAULT_PLACEMENT, 'x': 150}]
        self.control.state.placements = placements
        self.assertEqual(self.control.state.mode, 'anchor')
        target = fp.project(G, T2, placements[2], *centre(T2))
        self.assertTrue(orbit.beyond_view(G, target))
        seen = orbit.stretched_pointer(G, T0, placements[0], target)
        q0 = fp.unproject(G, T0, {**placements[0], 'x': 0, 'y': 0}, seen)
        px, py, pw, ph = fp.pill_rect(T0)
        self.assertTrue(px <= q0[0] <= px + pw and T0[1] + T0[3] <= q0[1] <= py + ph + fp.PILL_PAD_Y)  # the stretched pill
        self.assertEqual(fp.front_order(G, placements)[0], 0)  # and it is tested first
        hit = fp.hit(G, placements, target, around=True)
        self.assertEqual(hit[:2], ('desktop', 2))
        self.at(X3 + 960, 540)
        self.dot_to(target)
        self.assertEqual(self.p.hover[:2], ('desktop', 2))
        self.click()
        self.assertEqual(self.focused, ['XR-3'])
        self.assertEqual(self.control.state.placements, placements)  # no pill drag
        self.assertFalse(self.control.state.menu_open)  # nor a pill click
        # With nothing under the dot, the stretched pill is still found (a pill anywhere round you can be grabbed).
        alone = [placements[0], dict(DEFAULT_PLACEMENT), {**DEFAULT_PLACEMENT, 'x': -150}]
        self.assertEqual(fp.hit(G, alone, target, around=True), ('pill', 0))

    def test_a_click_on_the_inner_half_of_a_corner_focuses_and_a_drag_there_still_resizes(self):
        self.at(X2 + 960, 540)
        inner = (T0[0] + T0[2] - 4, T0[1] + 4)  # XR-1's top-right corner zone, on the desktop
        self.dot_to(inner)
        self.assertEqual(self.p.hover, ('corner', 0, 'tr'))
        self.click()
        self.assertEqual(self.focused, ['XR-1'])
        self.assertEqual(self.control.state.placements[0], DEFAULT_PLACEMENT)
        self.assertEqual(self.p._at(self.p.want[2])[0], 0)
        self.assertIsNone(self.p.drag)
        self.op('resize_press'); self.move_canvas(30, -30); self.op('resize_release')  # a resize is not a click
        self.control.join_focus(2)
        self.assertLess(self.control.state.placements[0]['distance'], 100)
        self.assertEqual(self.focused, ['XR-1'])
        corner = fp.project(G, T0, self.control.state.placements[0], T0[0] + T0[2], T0[1])  # the resized desktop's
        self.dot_to((corner[0] + 4, corner[1] - 4))  # the outer half, off the desktop: a click there is nothing
        self.assertEqual(self.p.hover[:2], ('corner', 0))
        self.click()
        self.assertEqual(self.focused, ['XR-1'])


if __name__ == '__main__':
    unittest.main()
