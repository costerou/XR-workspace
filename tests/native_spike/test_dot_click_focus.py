"""Dot-cursor click under the input lock (input_lock.LockedPointer; the run now wires the unified cursor, whose free mode
grabs like this one: test_unified_cursor.py) and with desktops placed anywhere round you (3DoF orbit, always facing
you): a click with the dot wherever a desktop is shown selects that desktop and stays in the overview, anchored. The
held cursor is parked again on the clicked desktop's monitor, under the dot, so focus-follows-mouse gives that monitor
Hyprland's focus. Only Ctrl+Alt+Up maximizes.

History:
  * click-zoom-fix (2026-11-02, installed master 80fab06): a dot click selected the desktop and asked Hyprland to focus
    its monitor but kept the view in the overview, with the held cursor left where it was; the fix made the click zoom
    in, as Ctrl+Alt+Up does.
  * anchor-no-click-maximize (2026-11-03, Cody: "Remove click to maximize in anchor mode"): the click selects again and
    never maximizes, and this time the held cursor goes to the clicked monitor under the dot (LockedPointer._click_desktop)
    instead of staying on the one it was parked on.
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


class LockedClickSelectTests(LockBase):
    def click(self):
        out = self.op('resize_press') + self.op('resize_release')  # the lock's left-button bind, press then release
        self.control.join_focus(2)
        if self.p.want[1] == 'free': self.cur = self.p.want[2]  # the sampler parks the held cursor where it was asked
        return out

    def dot_to(self, point):
        return self.move_canvas(point[0] - self.p.pointer[0], point[1] - self.p.pointer[1])

    def assert_parked_on(self, index, near=None):
        """Captured (free), the held cursor parked on desktop `index`'s monitor (near layout point `near`, if given)."""
        self.assertEqual(self.p.mode, 'free')
        self.assertEqual(self.p.want[1], 'free')
        park = self.p.want[2]
        self.assertEqual(self.p._at(park)[0], index)
        if near is not None: self.assertEqual(park, il.park_point(OUTPUTS[index], near))

    def test_a_dot_click_on_a_monitor_selects_it_and_stays_in_the_overview(self):
        # As Cody sees it: the cursor comes onto XR-1 in the overview and is captured (hidden, held there); the dot goes
        # onto XR-2 and clicks. XR-2 is selected; the view stays in the overview (it zoomed in from 2026-11-02 until
        # anchor-no-click-maximize).
        self.at(X1 + 960, 540)
        self.assertEqual(self.p.want[1:], ('free', (X1 + 960, 540)))
        q = (T1[0] + T1[2] * 0.25, T1[1] + T1[3] * 0.75)
        self.dot_to(q)  # XR-2 is at its slot, so its tile pixel is the canvas point
        self.assertEqual(self.p.hover[:2], ('desktop', 1))
        self.assertEqual(self.control.state.selected, 1)  # under the lock the dot moving onto it selected it already
        self.control.state.selected = 0  # e.g. Ctrl+Alt+Left since
        out = self.click()
        self.assertFalse(self.control.state.zoomed)  # still the overview
        self.assertEqual(self.control.state.mode, 'anchor')
        self.assertEqual(self.control.state.selected, 1)  # the clicked desktop, not the one the dot came from
        views = [f['payload'] for f in out if f['op'] == 'view']
        self.assertEqual(len(views), 1)  # one view frame: the highlight moves
        self.assertFalse(views[0]['zoomed']); self.assertEqual(views[0]['selected'], 1)
        self.assertEqual(self.focused, [])  # no focus call: the held cursor's move focuses the monitor (below)
        self.assertEqual(self.p.focus_clicks, 1)
        self.assert_parked_on(1, (X2 + round(0.25 * 1920), round(0.75 * 1080)))  # under the dot on XR-2
        self.at(*self.cur)  # the next supervisor tick, the cursor held there: still captured, nothing changes
        self.assertEqual(self.control.state.selected, 1)
        self.assertTrue(self.p.overview())
        self.assertEqual(self.p.captures, 1)  # the same capture, never re-captured

    def test_ctrl_alt_up_after_the_click_maximizes_the_clicked_desktop(self):
        self.at(X1 + 960, 540)
        self.dot_to(centre(T2))
        self.click()
        self.assertFalse(self.control.state.zoomed)
        self.assertEqual(self.control.state.selected, 2)
        keyed = self.op('zoom_in', ops=('view',))  # Ctrl+Alt+Up: the one way to maximize
        self.control.join_focus(2)
        self.assertTrue(self.control.state.zoomed)
        self.assertEqual([(f['payload']['selected'], f['payload']['zoomed']) for f in keyed], [(2, True)])
        self.assertEqual(self.focused, ['XR-3'])  # the zoom-in's own focus
        self.at(*self.cur)
        self.assertEqual(self.p.mode, 'off')  # the lock lets go: out of the overview

    def test_a_click_with_a_panel_open_collapses_it_then_selects(self):
        self.on_pill(0)
        self.op('resize_press'); self.op('resize_release')  # a pill click: XR-1's panel opens
        self.assertTrue(self.control.state.menu_open)
        self.dot_to(centre(T1))
        self.click()
        self.assertFalse(self.control.state.menu_open)  # collapsed (saved) by the press
        self.assertFalse(self.control.state.zoomed)
        self.assertEqual(self.control.state.selected, 1)

    def test_each_click_selects_the_monitor_it_lands_on(self):
        self.at(X2 + 960, 540)
        for tile, index in ((T0, 0), (T2, 2), (T1, 1)):
            self.dot_to(centre(tile))
            self.click()
            with self.subTest(index=index):
                self.assertFalse(self.control.state.zoomed)
                self.assertEqual(self.control.state.selected, index)
                self.assert_parked_on(index)
                self.at(*self.cur)
                self.assertEqual(self.control.state.selected, index)
        self.assertEqual(self.focused, [])

    def test_a_desktop_placed_round_you_is_clicked_where_it_is_drawn(self):
        # 3DoF: XR-2 lifted 15 degrees and turned 5 to the right, facing you. Its slot is empty now.
        placed = {**DEFAULT_PLACEMENT, 'x': 50, 'y': 150}
        self.control.state.placements = [dict(DEFAULT_PLACEMENT), placed, dict(DEFAULT_PLACEMENT)]
        self.at(X1 + 960, 540)
        self.dot_to(centre(T1))  # where XR-2 used to be: nothing there
        self.assertIsNone(self.p.hover)
        self.click()
        self.assertEqual(self.control.state.selected, 0)  # empty space: nothing
        self.assert_parked_on(0)
        q = (T1[0] + T1[2] * 0.7, T1[1] + T1[3] * 0.4)
        self.dot_to(fp.project(G, T1, placed, *q))  # on XR-2 as drawn, billboarded toward you
        self.assertEqual(self.p.hover[:2], ('desktop', 1))
        self.assertAlmostEqual(self.p.hover[2][0], q[0], delta=0.05); self.assertAlmostEqual(self.p.hover[2][1], q[1], delta=0.05)
        self.click()
        self.assertFalse(self.control.state.zoomed); self.assertEqual(self.control.state.selected, 1)
        park = self.p.want[2]
        self.assertEqual(self.p._at(park)[0], 1)
        self.assertLessEqual(abs(park[0] - (X2 + 0.7 * 1920)), 2); self.assertLessEqual(abs(park[1] - 0.4 * 1080), 2)

    def test_past_the_view_what_is_hit_is_what_the_dot_is_drawn_on(self):
        # Anchor mode. Past the view the dot is drawn in its stretched direction round you (orbit.pointer_direction, the
        # frame's az / el), and the hit test reads that same direction. XR-1 sits about 76 degrees round, XR-3 15
        # degrees: the dot drawn on XR-3 clicks XR-3, and the dot drawn on XR-1's pill is on that pill.
        placements = [{**DEFAULT_PLACEMENT, 'x': 760, 'y': 45, 'distance': 80}, dict(DEFAULT_PLACEMENT),
                      {**DEFAULT_PLACEMENT, 'x': 150}]
        self.control.state.placements = placements
        self.assertEqual(self.control.state.mode, 'anchor')
        target = fp.project(G, T2, placements[2], *centre(T2))
        self.assertGreater(abs(target[0] - G['w'] / 2), G['w'] / 2)  # past the view
        self.assertEqual(fp.hit(G, placements, target)[:2], ('desktop', 2))
        pill = fp.pill_rect(T0)
        on_pill = fp.project(G, T0, placements[0], pill[0] + pill[2] / 2, pill[1] + pill[3] / 2)
        self.assertGreater(abs(on_pill[0] - G['w'] / 2), G['w'] / 2)
        self.assertEqual(fp.hit(G, placements, on_pill), ('pill', 0))
        self.at(X3 + 960, 540)
        self.control.state.selected = 1
        self.dot_to(target)
        self.assertEqual(self.p.hover[:2], ('desktop', 2))
        dot = self.p.sent  # the dot as last sent to the renderer
        az, el = orbit.pointer_direction(G, (dot['x'], dot['y']))  # the whole pixel the frame carries
        self.assertEqual((dot['az'], dot['el']), (round(math.degrees(az) * 100), round(math.degrees(el) * 100)))
        seen = orbit.place_point(G, T2, placements[2], *centre(T2))  # the dot is drawn on XR-3's middle
        self.assertAlmostEqual(math.degrees(math.atan2(seen[0], -seen[2])), dot['az'] / 100, delta=0.02)
        self.click()
        self.assertFalse(self.control.state.zoomed); self.assertEqual(self.control.state.selected, 2)
        self.assertEqual(self.control.state.placements, placements)  # no pill drag
        self.assertFalse(self.control.state.menu_open)  # nor a pill click

    def test_inside_a_corner_is_content_and_only_outside_it_resizes(self):
        # anchor-no-click-maximize: a resize handle is the corner square's part outside the desktop; inside it is content.
        self.at(X2 + 960, 540)
        inner = fp.project(G, T0, self.control.state.placements[0], T0[0] + T0[2] - 4, T0[1] + 4)  # inside XR-1's top-right corner
        self.dot_to(inner)
        self.assertEqual(self.p.hover[:2], ('desktop', 0))
        self.control.state.selected = 2
        self.click()
        self.assertFalse(self.control.state.zoomed); self.assertEqual(self.control.state.selected, 0)
        self.assertEqual(self.control.state.placements[0], DEFAULT_PLACEMENT)
        self.assert_parked_on(0)
        self.assertIsNone(self.p.drag)
        outer = fp.project(G, T0, self.control.state.placements[0], T0[0] + T0[2] + 4, T0[1] - 4)  # just outside it
        self.dot_to(outer)
        self.assertEqual(self.p.hover, ('corner', 0, 'tr'))
        self.control.state.selected = 1
        self.click()  # a click on the handle: nothing (no resize, no selection)
        self.assertEqual((self.control.state.selected, self.control.state.placements[0]), (1, DEFAULT_PLACEMENT))
        self.op('resize_press'); self.move_canvas(30, -30); self.op('resize_release')  # a drag there resizes
        self.assertLess(self.control.state.placements[0]['distance'], 100)
        self.assertFalse(self.control.state.zoomed)
        self.assertEqual(self.control.state.selected, 1)
        self.assertEqual(self.focused, [])


class ClickSelectSamplerTests(unittest.TestCase):
    """The lock end to end: ViewControl, LockedPointer and the real LockedSampler thread (as managed.py wired them before
    the unified cursor) against a fake compositor client."""
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

    def test_a_dot_click_parks_the_held_cursor_on_the_clicked_monitor_and_ctrl_alt_up_still_maximizes(self):
        client = FakeLockClient(cursor=(X1 + 960, 540))
        sampler = il.LockedSampler(client, NAMES, self.p, BIND_ARGS).start(); self.addCleanup(sampler.stop)
        self.assertTrue(self.wait(lambda: sampler.grabbed and self.p.mode == 'free'))  # captured, held on XR-1
        q = (T1[0] + T1[2] * 0.25, T1[1] + T1[3] * 0.75)
        self.p.pointer = q  # the dot onto XR-2 (hand motion; driving it there sample by sample adds nothing here)
        out = frames(self.control.on_message({'op': 'resize_press', 'payload': {}}))
        out += frames(self.control.on_message({'op': 'resize_release', 'payload': {}}))  # a click selects on its release
        self.assertEqual((last(out, 'view')['selected'], last(out, 'view')['zoomed']), (1, False))
        self.assertTrue(self.wait(lambda: client.count('lock') == 2))  # grabbed again, parked under the dot on XR-2
        at = client.args('lock')[-1][5:]
        self.assertEqual(self.p._at(at)[0], 1)  # held on XR-2 now, where focus-follows-mouse puts the focus
        self.assertLessEqual(abs(at[0] - (X2 + 0.25 * 1920)), 2); self.assertLessEqual(abs(at[1] - 0.75 * 1080), 2)
        time.sleep(0.05); self.control.poll()
        self.assertTrue(sampler.grabbed)  # still the overview: the lock holds on
        self.assertEqual(client.count('release'), 0)
        self.assertEqual(self.control.state.selected, 1)
        self.control.join_focus(2)
        self.assertEqual(self.focused, [])
        self.control.on_message({'op': 'zoom_in', 'payload': {}})  # Ctrl+Alt+Up
        self.assertTrue(self.wait(lambda: client.count('release') == 1))
        self.assertEqual(client.args('release'), [()])  # shown where it is held: on XR-2, under the dot
        self.assertFalse(sampler.grabbed)
        self.control.join_focus(2)
        self.assertEqual(self.focused, ['XR-2'])
        self.assertTrue(self.control.state.zoomed)


if __name__ == '__main__':
    unittest.main()
