"""Directional desktop switching (neighbors.py, unified_cursor.EdgePush): zoomed in, pushing the cursor past an edge of
the selected desktop switches to the desktop placed that way round you in 3D (yaw / pitch / roll as drawn), not just the
next one in the row, with the Ctrl+Alt+Left / Right switch (the renderer zooms out, turns, zooms in) and every desktop
left where it is placed. Nothing that way: the cursor stays on home, clamped at the edge.

Regression: dot-cursor monitor switching was removed on 2026-11-02 (the cursor never left home); it comes back here,
chosen by placement. Overview click-to-zoom and the Ctrl+Alt arrows are unchanged (test_unified_cursor.py).

Offline only: the fixture's overview layout, fake clocks and a fake compositor client.
"""
import math
import time
import unittest

from tools.native_spike import free_pointer as fp, neighbors as nb, orbit, unified_cursor as uc, view
from tools.native_spike.geometry import DEFAULT_PLACEMENT

from test_free_pointer import BIND_ARGS, G, MONITORS, NAMES, OUTPUTS, X1, X2, X3, frames, last
from test_unified_cursor import FakeUnifiedClient, UnifiedBase

T = G['tiles']
DEG = 10  # wire units (tenths of a degree) per degree
A = [math.degrees(orbit.slot_azimuth(G, t)) for t in T]  # each slot's own azimuth, degrees


def flat(n=3):
    return [dict(DEFAULT_PLACEMENT) for _ in range(n)]


def at_direction(i, az, el, **extra):
    """Desktop i's placement that puts its centre at azimuth `az` / elevation `el` degrees (straight ahead is 0, 0)."""
    return {**DEFAULT_PLACEMENT, 'x': round((az - A[i]) * DEG), 'y': round(el * DEG), **extra}


def pick(placements, home, edge):
    return nb.pick(nb.frames(G, placements), home, edge)


class NeighbourTests(unittest.TestCase):
    def test_the_row_as_laid_out(self):
        p = flat()
        self.assertEqual([pick(p, 0, e) for e in ('left', 'right', 'up', 'down')], [None, 1, None, None])
        self.assertEqual([pick(p, 1, e) for e in ('left', 'right', 'up', 'down')], [0, 2, None, None])
        self.assertEqual([pick(p, 2, e) for e in ('left', 'right', 'up', 'down')], [1, None, None, None])

    def test_a_desktop_above_is_up_not_right(self):
        # XR-2 lifted straight over XR-1: up from XR-1 goes there, right skips it for XR-3, and down from XR-2 comes back.
        p = [at_direction(0, 0, 0), at_direction(1, 0, 25), at_direction(2, 25, 0)]
        self.assertEqual(pick(p, 0, 'up'), 1)
        self.assertEqual(pick(p, 0, 'right'), 2)
        self.assertIsNone(pick(p, 0, 'down'))
        self.assertEqual(pick(p, 1, 'down'), 0)
        self.assertIsNone(pick(p, 1, 'up'))
        self.assertEqual(pick(p, 2, 'left'), 0)  # XR-1 is straight left of XR-3; XR-2 is up-left, further off-axis

    def test_below_and_behind(self):
        p = [at_direction(0, 0, 0), at_direction(1, 0, -30), at_direction(2, 180, 0)]
        self.assertEqual(pick(p, 0, 'down'), 1)
        self.assertEqual(pick(p, 1, 'up'), 0)
        for e in ('left', 'right'): self.assertIsNone(pick(p, 0, e))  # directly behind has no bearing: never picked
        p[2] = at_direction(2, 170, 0)  # 10 degrees short of behind, to the right: round that way
        self.assertEqual(pick(p, 0, 'right'), 2)

    def test_a_diagonal_neighbour_is_reachable_from_both_of_its_sides(self):
        p = [at_direction(0, 0, 0), at_direction(1, 20, 20), at_direction(2, -60, -40)]
        self.assertEqual(pick(p, 0, 'right'), 1)
        self.assertEqual(pick(p, 0, 'up'), 1)
        self.assertEqual(pick(p, 1, 'left'), 0)
        self.assertEqual(pick(p, 1, 'down'), 0)
        self.assertEqual(pick(p, 0, 'left'), 2)  # down-left at ~34 degrees off left, inside the cone
        self.assertEqual(pick(p, 0, 'down'), 2)  # and ~56 degrees off down: still inside it

    def test_outside_the_cone_is_never_picked(self):
        p = [at_direction(0, 0, 0), at_direction(1, 8, 30), at_direction(2, -60, -60)]  # ~75 degrees off right
        self.assertIsNone(pick(p, 0, 'right'))
        self.assertEqual(pick(p, 0, 'up'), 1)

    def test_the_nearest_in_the_cone_wins_and_off_axis_counts_as_further(self):
        p = [at_direction(0, 0, 0), at_direction(1, 20, 0), at_direction(2, 40, 0)]
        self.assertEqual(pick(p, 0, 'right'), 1)
        self.assertEqual(pick(p, 2, 'left'), 1)
        # Straight right at 25 degrees beats up-right at 20 / 15 (nearer, but ~37 degrees off-axis).
        p = [at_direction(0, 0, 0), at_direction(1, 20, 15), at_direction(2, 25, 0)]
        self.assertEqual(pick(p, 0, 'right'), 2)
        self.assertEqual(pick(p, 0, 'up'), 1)

    def test_offset_rows_pick_by_placement_not_by_index(self):
        # XR-3 placed left of XR-1, XR-2 right: the row order would say otherwise.
        p = [at_direction(0, 0, 0), at_direction(1, 20, 0), at_direction(2, -20, 0)]
        self.assertEqual(pick(p, 0, 'left'), 2)
        self.assertEqual(pick(p, 0, 'right'), 1)
        self.assertIsNone(pick(p, 2, 'left'))
        self.assertEqual(pick(p, 2, 'right'), 0)

    def test_a_rolled_home_turns_the_directions_with_it(self):
        # Home rolled 90 degrees counter-clockwise: its right edge faces up, so the desktop above is past its right edge.
        p = [at_direction(0, 0, 0, roll=9000), at_direction(1, 0, 25), at_direction(2, 25, 0)]
        self.assertEqual(pick(p, 0, 'right'), 1)
        self.assertEqual(pick(p, 0, 'down'), 2)
        self.assertIsNone(pick(p, 0, 'up'))

    def test_frames_are_unit_and_orthogonal(self):
        for f in nb.frames(G, [at_direction(0, 10, 50), at_direction(1, -120, -20), at_direction(2, 30, 5, roll=1500)]):
            d, r, u = f
            for v in f: self.assertAlmostEqual(orbit._dot(v, v), 1.0, places=9)
            for a, b in ((d, r), (d, u), (r, u)): self.assertAlmostEqual(orbit._dot(a, b), 0.0, places=9)

    def test_without_a_layout_only_the_row_order(self):
        self.assertEqual([nb.row_fallback(3, 1, e) for e in ('left', 'right', 'up', 'down')], [0, 2, None, None])
        self.assertIsNone(nb.row_fallback(3, 2, 'right'))
        self.assertIsNone(nb.row_fallback(3, 0, 'left'))
        with self.assertRaises(ValueError): nb.row_fallback(3, 0, 'sideways')
        with self.assertRaises(ValueError): nb.pick(nb.frames(G, flat()), 0, 'sideways')
        self.assertIsNone(nb.pick([None, None], 0, 'right'))


def neighbours(**table):
    return lambda edge: table.get(edge)


class EdgePushTests(unittest.TestCase):
    def setUp(self):
        self.push, self.now, self.prev = uc.EdgePush(), 10.0, None

    def step(self, cursor, n, dt=0.01):
        self.now += dt
        warp, switch = self.push.step(OUTPUTS, 0, cursor, self.prev, self.now, n)
        self.prev = warp or cursor
        return warp, switch

    def test_pushing_onto_the_monitor_beside_it_switches_after_push_px(self):
        n = neighbours(right=1)
        steps = math.ceil(uc.PUSH_PX / 10)
        for _ in range(steps - 1):
            warp, switch = self.step((X2 + 9, 300), n)  # 10 px past XR-1's right edge each sample
            self.assertEqual(warp, (X1 + 1919 - fp.INSET_PX, 300)); self.assertIsNone(switch)
        warp, switch = self.step((X2 + 9, 300), n)
        self.assertEqual(switch, (1, 'right', 300 / 1079))

    def test_no_neighbour_that_way_is_plain_confinement(self):
        n = neighbours()
        for _ in range(20):
            warp, switch = self.step((X2 + 30, 300), n)
            self.assertEqual(warp, uc.confine_target(OUTPUTS, 0, (X2 + 30, 300))); self.assertIsNone(switch)
        self.assertEqual(self.step((X1 + 960, 0), n), (None, None))  # a clamped edge: no nudge, it just stays there

    def test_a_clamped_edge_with_a_neighbour_nudges_and_counts_each_touch(self):
        n = neighbours(up=2)
        touches = math.ceil(uc.PUSH_PX / uc.NUDGE_PX)
        for _ in range(touches - 1):
            warp, switch = self.step((X1 + 480, 0), n)
            self.assertEqual(warp, (X1 + 480, uc.NUDGE_PX)); self.assertIsNone(switch)
        warp, switch = self.step((X1 + 480, 0), n)
        self.assertEqual(switch, (2, 'up', 480 / 1919))

    def test_a_pause_starts_the_push_over(self):
        n = neighbours(right=1)
        for _ in range(4): self.step((X2 + 9, 300), n)
        _, switch = self.step((X2 + 9, 300), n, dt=uc.PUSH_WINDOW_S + 0.05)
        self.assertIsNone(switch)
        self.assertEqual(self.push.amount, 10)

    def test_another_edge_starts_the_push_over(self):
        n = neighbours(right=1, up=2)
        for _ in range(4): self.step((X2 + 9, 300), n)
        self.step((X1 + 900, 0), n)
        self.assertEqual((self.push.edge, self.push.amount), ('up', uc.NUDGE_PX))

    def test_a_warp_is_never_a_push(self):
        n = neighbours(right=1)
        for _ in range(10):
            warp, switch = self.step((X3 + 500, 300), n)  # far onto XR-3: a focus change or warp
            self.assertIsNotNone(warp); self.assertIsNone(switch)
        self.assertEqual(self.push.amount, 0)

    def test_after_a_switch_the_push_rests_for_the_animation(self):
        n = neighbours(right=1)
        while self.step((X2 + 60, 300), n)[1] is None: pass
        for _ in range(10):
            self.assertIsNone(self.step((X2 + 60, 300), n)[1])
        self.now += uc.SWITCH_COOLDOWN_S
        self.assertIsNotNone(self.step((X2 + 60, 300), n)[1])

    def test_a_corner_takes_the_edge_that_has_a_neighbour(self):
        n = neighbours(up=1)
        warp, _ = self.step((X1 + 1919, 0), n)
        self.assertEqual(self.push.edge, 'up'); self.assertEqual(warp, (X1 + 1919 - fp.INSET_PX, uc.NUDGE_PX))

    def test_the_physical_monitor_is_still_left_alone_after_a_warp(self):
        n = neighbours(left=1)
        self.prev = (500, 500)  # already on the physical monitor
        self.assertEqual(self.step((X1 - 3, 300), n), (None, None))

    def test_entry_points(self):
        o = OUTPUTS[1]
        self.assertEqual(uc.entry_point(o, 'up', 0.5), (X2 + 960, 1079 - uc.ENTRY_PX))
        self.assertEqual(uc.entry_point(o, 'down', 0.0), (X2 + fp.INSET_PX, uc.ENTRY_PX))
        self.assertEqual(uc.entry_point(o, 'right', 1.0), (X2 + uc.ENTRY_PX, 1079 - fp.INSET_PX))
        self.assertEqual(uc.entry_point(o, 'left', 0.25), (X2 + 1919 - uc.ENTRY_PX, round(0.25 * 1079)))


class ControlTests(UnifiedBase):
    def test_switch_desktop_is_the_key_switch_zoomed_in(self):
        self.op('zoom_in')
        out = frames(self.control.switch_desktop(2))
        self.assertEqual(last(out, 'view'), {**self.control.state.view(), 'selected': 2, 'zoomed': True})
        self.assertEqual(self.control.state.placements, flat())  # nothing is moved
        for stale in (2, 5, -1, 'x'): self.assertEqual(self.control.switch_desktop(stale), b'')
        self.op('zoom_out')
        self.assertEqual(self.control.switch_desktop(0), b'')  # the overview: a click or the keys switch there
        self.op('zoom_in'); self.control.state.typing = True
        self.assertEqual(self.control.switch_desktop(0), b'')
        self.control.state.typing = False; self.control.video_active = True
        self.assertEqual(self.control.switch_desktop(0), b'')

    def test_the_pointer_reads_the_placed_layout(self):
        self.assertEqual([self.p.neighbour(e) for e in ('left', 'right', 'up')], [None, 1, None])
        self.control.state.set_placements([at_direction(0, 0, 0), at_direction(1, 0, 25), at_direction(2, 25, 0)])
        self.assertEqual([self.p.neighbour(e) for e in ('right', 'up')], [2, 1])
        self.p.geometry = None  # no layout yet: the row
        self.assertEqual([self.p.neighbour(e) for e in ('right', 'up')], [1, None])

    def test_a_requested_switch_is_made_on_the_loop_unless_stale(self):
        self.op('zoom_in')
        self.p.request_switch(0, 1)
        self.assertEqual(last(frames(self.control.poll()), 'view')['selected'], 1)
        self.assertEqual(self.p.edge_switches, 1)
        self.p.request_switch(0, 2)  # asked from the old home: Ctrl+Alt+Right got there first
        self.assertIsNone(self.control.poll())
        self.assertEqual(self.control.state.selected, 1)
        self.op('zoom_out')
        self.p.request_switch(1, 2)
        self.control.poll()
        self.assertEqual(self.control.state.selected, 1)

    def test_ctrl_alt_arrows_and_the_overview_click_still_switch(self):
        self.op('zoom_in')
        self.assertEqual(last(self.op('next'), 'view')['selected'], 1)
        self.assertEqual(last(self.op('prev'), 'view')['selected'], 0)
        self.op('zoom_out')
        self.assertEqual(last(frames(self.control.focus_desktop(2)), 'view'), {**self.control.state.view(), 'selected': 2})


class SamplerTests(unittest.TestCase):
    def setUp(self):
        self.focused = []
        self.control = view.ViewControl(view.ViewState(NAMES), 'run', 'session', self.focused.append, None, None)
        self.addCleanup(self.control.join_focus, 2)
        self.addCleanup(self.control.stop_menu_binds)
        self.p = self.control.overview_pointer = uc.UnifiedPointer(self.control); self.addCleanup(self.p.close)
        self.p.set_geometry(G)
        self.control.state.zoomed = True

    def start(self, client):
        sampler = uc.UnifiedSampler(client, NAMES, self.p, BIND_ARGS).start()
        self.addCleanup(sampler.stop)
        return sampler

    def push(self, client, cursor, until, timeout=2.0):
        """The hand keeps pushing: the cursor is put at `cursor` every few ms until `until()` holds."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            client.cursor = cursor
            self.control.poll()
            if until(): return True
            time.sleep(0.005)
        return until()

    def test_pushing_right_switches_and_the_cursor_comes_in_over_the_left_edge(self):
        client = FakeUnifiedClient(cursor=(X1 + 960, 300))
        sampler = self.start(client)
        self.assertTrue(self.push(client, (X2 + 8, 300), lambda: self.control.state.selected == 1))
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not any(a[2] == 'XR-2' for a in client.args('confine')): time.sleep(0.005)
        entry = next(a for a in client.args('confine') if a[2] == 'XR-2')
        self.assertEqual(entry, (X2 + uc.ENTRY_PX, 300, 'XR-2'))
        self.assertEqual(self.focused, [])  # no focus thread: the sampler's confine focuses XR-2 at the entry point
        self.assertEqual(self.p.edge_switches, 1)
        self.assertGreater(sampler.confines, 1)

    def test_pushing_up_reaches_the_desktop_placed_above(self):
        self.control.state.set_placements([at_direction(0, 0, 0), at_direction(1, 0, 25), at_direction(2, 25, 0)])
        client = FakeUnifiedClient(cursor=(X1 + 960, 500))
        self.start(client)
        self.assertTrue(self.push(client, (X1 + 960, 0), lambda: self.control.state.selected == 1))
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not any(a[2] == 'XR-2' for a in client.args('confine')): time.sleep(0.005)
        self.assertIn((X2 + 960, 1079 - uc.ENTRY_PX, 'XR-2'), client.args('confine'))
        self.assertEqual(self.control.state.placements[1], at_direction(1, 0, 25))  # it stays where it was placed

    def hand(self, client, delta, seconds):
        """The hand keeps moving by `delta` logical px every 5 ms (relative, as a mouse does, from wherever the cursor was
        put), Hyprland keeping it on some monitor. Returns every view frame's `selected`, in order."""
        def onto_a_monitor(c):
            on = [m for m in MONITORS if m['x'] <= c[0] < m['x'] + m['width'] and m['y'] <= c[1] < m['y'] + m['height']]
            if on: return c
            return min(((min(max(c[0], m['x']), m['x'] + m['width'] - 1), min(max(c[1], m['y']), m['y'] + m['height'] - 1))
                        for m in MONITORS), key=lambda q: abs(q[0] - c[0]) + abs(q[1] - c[1]))
        selected, deadline = [], time.monotonic() + seconds
        while time.monotonic() < deadline:
            client.cursor = onto_a_monitor((client.cursor[0] + delta[0], client.cursor[1] + delta[1]))
            selected += [f['payload']['selected'] for f in frames(self.control.poll()) if f['op'] == 'view']
            time.sleep(0.005)
        return selected

    def test_a_cursor_switch_goes_straight_to_the_destination_and_stays(self):
        # cursor-switch-no-anim (2026-11-03): "the view goes to the other monitor and then goes back". The cursor path
        # asks the renderer for exactly one switch, to the desktop the cursor ends up on, and nothing after it: the hand
        # still pushing through the switch animation (and past the cooldown) never selects another desktop or the old
        # one. Here the 3D order differs from Hyprland's row (XR-2 above, XR-3 to the right). The turn back seen in anchor
        # mode is the renderer's switch animation, the same for Ctrl+Alt+Left / Right (fixed there, anchor-switch-stay).
        placed = [at_direction(0, 0, 0), at_direction(1, 0, 25), at_direction(2, 25, 0)]
        for start, delta, destination in (((X1 + 1700, 300), (4, 0), 2),   # right: across XR-2 in the row, to XR-3
                                          ((X1 + 960, 200), (0, -4), 1)):  # up: the clamped top edge, to XR-2 above
            with self.subTest(destination=destination):
                self.control.state.selected = 0
                self.control.state.set_placements(placed)
                client = FakeUnifiedClient(cursor=start)
                sampler = self.start(client)
                self.assertEqual(self.hand(client, delta, uc.SWITCH_COOLDOWN_S + 0.6), [destination])
                self.assertEqual(self.control.state.selected, destination)
                self.assertEqual(client.args('confine')[-1][2], NAMES[destination])  # the cursor and focus are there
                self.assertEqual(self.p.edge_switches, 1)
                sampler.stop(); self.p.edge_switches = 0

    def test_a_cursor_switch_frame_is_the_key_switch_frame(self):
        # The renderer animates (and settles) a cursor switch exactly as Ctrl+Alt+Left / Right: the same view frame.
        self.control.state.selected = 0
        cursor = last(frames(self.control.switch_desktop(1)), 'view')
        self.control.state.selected = 0
        key = last(frames(self.control.on_message({'op': 'next', 'payload': {}})), 'view')
        self.assertEqual(cursor, key)

    def test_the_push_rests_until_the_switch_animation_has_landed(self):
        # spike ViewAnimator defaults: zoom out 220 ms, turn 300 ms, zoom in 220 ms. No second push lands mid-animation.
        self.assertGreater(uc.SWITCH_COOLDOWN_S, (220 + 300 + 220) / 1000)

    def test_nothing_that_way_stays_put(self):
        client = FakeUnifiedClient(cursor=(X1 + 960, 500))
        self.start(client)
        self.assertFalse(self.push(client, (X1 + 960, 0), lambda: self.control.state.selected != 0, timeout=0.3))
        self.assertEqual(client.count('confine'), 0)  # no neighbour above: no nudge either
        self.assertFalse(self.push(client, (X1 - 4, 300), lambda: self.control.state.selected != 0, timeout=0.3))
        self.assertEqual(client.args('confine')[-1], (X1 + fp.INSET_PX, 300, 'XR-1'))  # off onto the physical one: back

    def test_typing_mode_never_switches(self):
        self.control.state.typing = True
        client = FakeUnifiedClient(cursor=(X1 + 960, 300))
        self.start(client)
        self.assertFalse(self.push(client, (X2 + 8, 300), lambda: self.control.state.selected != 0, timeout=0.4))
        self.assertEqual(client.args('confine')[-1], (X1 + 1919 - fp.INSET_PX, 300, 'XR-1'))


if __name__ == '__main__':
    unittest.main()
