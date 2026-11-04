"""The push past a maximized desktop's edge, drawn by the hand (edge_pan.py, neighbors.pick_at, unified_cursor.EdgePush): only a
desktop really that way, measured from the edge point on the desktops as placed; travel 1:1 with the view's turn; the entry
point at the height the cursor left; pulling back undoes it; every other switch stays a cut.

The renderer's half: ctest view (ViewAnimator's pan, edge_pan_path) and focus_center (aim_along, follow and locked landing);
the two compared: ctest placement_contract. Offline only: the fixture's overview layout, fake clocks, a fake compositor.
"""
import json
import math
import time
import unittest

from tools.native_spike import edge_pan as ep, free_pointer as fp, neighbors as nb, orbit, unified_cursor as uc, view
from tools.native_spike.geometry import DEFAULT_PLACEMENT

from test_directional_switch import A, DEG, at_direction, flat
from test_free_pointer import BIND_ARGS, G, MONITORS, NAMES, OUTPUTS, X1, X2, X3, frames, last
from test_unified_cursor import FakeUnifiedClient

SIZE = (1920, 1080)


def plan(placements, home=0, target=1, side='right', along=0.5):
    return ep.plan(G, placements, home, target, side, along, SIZE)


def bearing_placement(az, el, target=1):
    """The other desktops out of the way behind you, `target` at (az, el) degrees."""
    p = [at_direction(0, 0, 0), at_direction(1, -150, 0), at_direction(2, 150, -40)]
    p[target] = at_direction(target, az, el)
    return p


class ConeTests(unittest.TestCase):
    def test_the_row_is_in_reach_along_the_whole_edge(self):
        p = flat()
        for t in (0, 0.3, 1):
            self.assertEqual(nb.pick_at(G, p, 1, 'right', t), 2)
            self.assertEqual(nb.pick_at(G, p, 1, 'left', t), 0)
            for side in ('up', 'down'): self.assertIsNone(nb.pick_at(G, p, 1, side, t))

    def test_measured_from_the_edge_point_with_a_45_degree_cone(self):
        # Straight along the bearing from the exit point: in at 40 degrees off the push, out at 50, each way round.
        p0 = at_direction(0, 0, 0)
        e = nb.edge_frame(G, G['tiles'][0], p0, 'right', 0.5)
        for off, want in ((0, 1), (40, 1), (-40, 1), (50, None), (-50, None)):
            d = [e[0][i] + math.tan(math.radians(30)) * (math.cos(math.radians(off)) * e[1][i] + math.sin(math.radians(off)) * e[2][i]) for i in range(3)]
            n = math.sqrt(sum(v * v for v in d))
            az, el = math.degrees(math.atan2(d[0] / n, -d[2] / n)), math.degrees(math.asin(d[1] / n))
            p = bearing_placement(az, el)
            self.assertEqual(nb.pick_at(G, p, 0, 'right', 0.5), want, off)

    def test_nothing_that_way_is_no_switch_at_all(self):
        # Directly above: pushing right finds nothing, though the old 60-degree cone and the row both had an answer nearby.
        p = bearing_placement(14, 26)
        self.assertEqual(nb.pick_at(G, p, 0, 'up', 0.5), 1)
        self.assertIsNone(nb.pick_at(G, p, 0, 'right', 0.5))
        self.assertIsNone(nb.pick_at(G, p, 0, 'left', 0.5))

    def test_the_same_desktop_can_be_in_reach_from_one_end_of_an_edge_only(self):
        p = None
        for el in range(5, 60):  # the edge's top end sees it flatter than its bottom end does
            q = bearing_placement(20, el)
            if nb.pick_at(G, q, 0, 'right', 0.0) == 1 and nb.pick_at(G, q, 0, 'right', 1.0) is None: p = q; break
        self.assertIsNotNone(p)

    def test_behind_you_goes_the_way_the_push_implies_never_the_long_way(self):
        p = bearing_placement(150, 0)
        self.assertEqual(nb.pick_at(G, p, 0, 'right', 0.5), 1)
        self.assertIsNone(nb.pick_at(G, p, 0, 'left', 0.5))
        p = bearing_placement(-150, 0)
        self.assertEqual(nb.pick_at(G, p, 0, 'left', 0.5), 1)
        self.assertIsNone(nb.pick_at(G, p, 0, 'right', 0.5))

    def test_a_desktop_dead_opposite_has_no_way_round(self):
        tile, p0 = G['tiles'][0], at_direction(0, 0, 0)
        e = nb.edge_frame(G, tile, p0, 'right', 0.5)[0]
        az, el = math.degrees(math.atan2(-e[0], e[2])), math.degrees(math.asin(-e[1]))
        p = bearing_placement(az, el)
        self.assertIsNone(nb.pick_at(G, p, 0, 'right', 0.5))

    def test_directions_up_down_and_diagonal(self):
        self.assertEqual(nb.pick_at(G, bearing_placement(0, 30), 0, 'up', 0.5), 1)
        self.assertEqual(nb.pick_at(G, bearing_placement(0, -30), 0, 'down', 0.5), 1)
        self.assertEqual(nb.pick_at(G, bearing_placement(30, 20), 0, 'right', 0.5), 1)  # up and to the right


class EntryTests(unittest.TestCase):
    def fraction(self, p, along, side='right', home=0, target=1):
        return ep.entry_fraction(G, G['tiles'][home], p[home], G['tiles'][target], p[target], side, along)

    def test_a_level_neighbour_at_the_height_the_cursor_left(self):
        for along in (0.1, 0.3, 0.7):
            self.assertAlmostEqual(self.fraction(flat(), along), along, delta=0.03)
            self.assertNotAlmostEqual(self.fraction(flat(), along), 0.5, delta=0.1)  # not its centre

    def test_the_height_is_measured_in_real_positions(self):
        # Lifted, the same height is lower down the target: further along its edge, and the point is at the height left.
        p = flat()
        p[1] = {**p[1], 'y': 3 * DEG}
        along = 0.3
        f = self.fraction(p, along)
        self.assertGreater(f, along + 0.05)
        e = ep.point(G, G['tiles'][0], p[0], 'right', along)
        q = ep.point(G, G['tiles'][1], p[1], 'left', f)
        self.assertAlmostEqual(math.asin(e[1]), math.asin(q[1]), places=3)

    def test_clamped_to_the_target(self):
        p = flat()
        p[1] = {**p[1], 'y': 15 * DEG}  # all of it above the height left
        self.assertEqual(self.fraction(p, 0.5), 1.0)
        p[1] = {**p[1], 'y': -15 * DEG}
        self.assertEqual(self.fraction(p, 0.5), 0.0)

    def test_up_and_down_keep_the_position_along_the_edge(self):
        p = [at_direction(0, 0, 0), at_direction(1, 0, 25), at_direction(2, 150, 0)]
        self.assertAlmostEqual(self.fraction(p, 0.3, 'up'), 0.3, delta=0.05)
        self.assertAlmostEqual(self.fraction(p, 0.8, 'up'), 0.8, delta=0.05)

    def test_the_cursor_comes_in_there(self):
        o = OUTPUTS[1]
        self.assertEqual(uc.entry_point(o, 'right', 0.25), (X2 + uc.ENTRY_PX, round(0.25 * 1079)))
        self.assertEqual(uc.entry_point(o, 'left', 0.25), (X2 + 1919 - uc.ENTRY_PX, round(0.25 * 1079)))


class MappingTests(unittest.TestCase):
    def test_the_rate_is_the_maximized_desktops_own(self):
        # Its whole angle over its whole width: a cursor crossing it turns exactly that angle.
        hw, _ = G['half']
        fit = ep.fit_tile(G, G['tiles'][0])
        self.assertAlmostEqual(ep.rate(G, G['tiles'][0], 'right', SIZE) * 1920, 2 * math.atan(fit[2] / G['w'] * hw))

    def test_crossing_is_1_to_1_with_the_hand(self):
        far = bearing_placement(60, 0)
        pl = plan(far)
        r = ep.rate(G, G['tiles'][0], 'right', SIZE)
        t, q = ep.drawn_home(G, G['tiles'][0], far[0], pl.zoom)
        angle = ep._angle(ep.point(G, t, q, 'right', pl.exit), ep.point(G, G['tiles'][1], far[1], 'left', pl.entry))
        self.assertAlmostEqual(pl.gap_px, angle / r, places=3)
        for px in (10, 100, 500):  # each px of hand past the peek is `r` radians along the path, at any point of it
            f = pl.frame(pl.peek_px + px)
            self.assertAlmostEqual(f['cross'] / 10000 * angle, px * r, places=3)

    def test_the_view_turns_1_to_1_every_px_of_the_way(self):
        for p in (flat(), bearing_placement(60, 0)):
            pl = plan(p)
            for px in (5, 40, pl.total_px / 2, pl.total_px):
                self.assertAlmostEqual(pl.frame(px)['turn'] / 10000 * math.pi, px * pl.rate * pl.spin, places=3)

    def test_the_view_has_one_way_from_end_to_end(self):
        # The whole turn, on the desktops as placed: the rotation from the view on one to the view on the other.
        for p, target in ((flat(), 1), (bearing_placement(60, 0), 1), (bearing_placement(30, 20), 1), (bearing_placement(150, 0), 1)):
            pl = plan(p, target=target)
            a, b = nb.frame(G, G['tiles'][0], p[0])[0], nb.frame(G, G['tiles'][target], p[target])[0]
            self.assertGreaterEqual(pl.way, ep._angle(a, b) - 1e-9)
            self.assertLess(pl.way, ep._angle(a, b) * 1.1)
            self.assertLessEqual(pl.total_px * pl.rate * pl.spin, pl.way + 1e-6)  # the hand never asks for more than the way
            self.assertEqual(pl.frame(0)['way'], round(pl.way / (2 * math.pi) * 10000))

    def test_the_hand_moves_the_views_centre_1_to_1_on_a_long_diagonal(self):
        # A turn between two upright views with different heights also rolls the view, and that part moves its centre less.
        p = bearing_placement(100, 35)
        pl = plan(p)
        self.assertGreater(pl.spin, 1.0)
        turn, spin = ep.way(G, G['tiles'][0], p[0], G['tiles'][1], p[1], 'right')
        seen = ep._angle(nb.frame(G, G['tiles'][0], p[0])[0], nb.frame(G, G['tiles'][1], p[1])[0])
        self.assertAlmostEqual(math.sin(turn / 2) / spin, math.sin(seen / 2), places=9)

    def test_far_round_the_way_the_push_goes_is_the_long_way(self):
        # Just over half a turn to the right of home's centre, so the short way from there is left, but from the point of the
        # right edge, which the push picked it from, the short way to it is right.
        p = bearing_placement(-177, 0)
        self.assertEqual(nb.pick_at(G, p, 0, 'right', 0.5), 1)
        turn, _ = ep.way(G, G['tiles'][0], p[0], G['tiles'][1], p[1], 'right')
        self.assertGreater(turn, math.pi)
        self.assertLess(ep.way(G, G['tiles'][0], p[0], G['tiles'][1], p[1], 'left')[0], math.pi)

    def test_neighbours_in_a_row_are_carried_by_the_hand_too(self):
        # Under a degree apart as placed, and a maximized desktop covers them: the hand still carries the view to where
        # the dot meets the target, half way between the two.
        p = flat()
        pl = plan(p, side='right')
        centres = ep._angle(nb.frame(G, G['tiles'][0], p[0])[0], nb.frame(G, G['tiles'][1], p[1])[0])
        self.assertLess(pl.gap_px * pl.rate, math.radians(1))
        self.assertAlmostEqual(pl.total_px * pl.rate, centres * ep.MEET_SHARE, places=4)
        self.assertGreater(pl.total_px, 300)

    def test_the_hand_decides_not_the_clock(self):
        pl = plan(bearing_placement(60, 0))
        a = pl.frame(100.0)
        self.assertEqual(a, pl.frame(100.0))
        self.assertLess(pl.frame(90.0)['cross'], a['cross'])  # and it runs backwards

    def test_a_peek_comes_first_and_is_the_least_that_switches(self):
        pl = plan(flat())
        self.assertEqual(pl.frame(0)['peek'], 0)
        self.assertEqual(pl.frame(pl.peek_px / 2)['cross'], 0)
        self.assertGreaterEqual(pl.peek_px, ep.PEEK_PX)
        self.assertGreater(pl.total_px, pl.peek_px)

    def test_the_zoom_falls_over_the_hands_whole_way_however_far(self):
        # So a desktop far off shows the same shrink as a neighbour, over a longer push: not all of it in the first 48 px.
        for p in (flat(), bearing_placement(60, 0)):
            pl = plan(p)
            self.assertEqual(pl.frame(pl.total_px / 2)['peek'], 5000)
            self.assertEqual(pl.frame(pl.total_px)['peek'], 10000)

    def test_touching_desktops_have_only_the_peek(self):
        p = flat()
        tiles = G['tiles']
        gap_az = math.degrees(orbit.slot_azimuth(G, tiles[1]) - orbit.slot_azimuth(G, tiles[0]))
        p[1] = at_direction(1, A[0] + gap_az * 0.9, 0)  # overlapping home's right edge
        pl = plan([at_direction(0, A[0], 0), p[1], at_direction(2, 150, 0)])
        self.assertEqual(pl.gap_px, 0.0)
        self.assertEqual(pl.total_px, pl.peek_px)
        self.assertEqual(pl.frame(pl.peek_px)['cross'], 0)

    def test_the_zoom_is_only_what_the_pair_needs(self):
        near, far = plan(flat()), plan(bearing_placement(90, 0))
        self.assertEqual(far.zoom, ep.MAX_ZOOM)  # nothing helps: both can never be in view, and it covers nothing
        self.assertLess(near.zoom, 0.3)          # a maximized desktop covers its row neighbour: it has to come down
        # A wider gap needs less of it: home's edge may stay further out, half way across.
        q = flat()
        q[0] = at_direction(0, A[0] - 5, 0)
        self.assertGreater(plan(q).zoom, near.zoom)
        p = flat()
        c = nb.frame(G, G['tiles'][0], p[0])[0]
        reach = ep._angle(c, ep.point(G, G['tiles'][1], p[1], 'left', near.entry))
        t, r = ep.drawn_home(G, G['tiles'][0], p[0], near.zoom)
        self.assertLess(ep._angle(c, ep.point(G, t, r, 'right', near.exit)), reach)  # there is still a way across

    def test_a_desktop_placed_bigger_than_the_view_stays_maximized_by_hand(self):
        # Coming down the zoom makes it bigger: it only does that on its way out, behind the target.
        p = bearing_placement(100, 0)
        p[0] = {**p[0], 'distance': 20}
        self.assertLess(ep.grown(G, G['tiles'][0], p[0]), 1)
        self.assertEqual(plan(p).zoom, 1.0)

    def test_the_zoom_clears_the_target_as_it_is_drawn_rolled_and_all(self):
        # Close enough that only the target's roll brings a corner over home: home has to come down further for that.
        p = flat()
        g = G
        level = ep.plan(g, p, 0, 1, 'right', 0.5, SIZE)
        rolled = ep.plan(g, p, 0, 1, 'right', 0.5, SIZE, [0, 800, 0])
        self.assertLess(rolled.zoom, level.zoom)
        t, q = ep.drawn_home(G, G['tiles'][0], p[0], rolled.zoom)
        target = orbit.footprint(G, G['tiles'][1], {**p[1], 'roll': 800})
        self.assertTrue(ep._apart(orbit.footprint(G, t, q), target))
        t, q = ep.drawn_home(G, G['tiles'][0], p[0], level.zoom)
        self.assertFalse(ep._apart(orbit.footprint(G, t, q), target))

    def test_up_uses_the_view_height(self):
        p = bearing_placement(0, 40)
        self.assertAlmostEqual(ep.rate(G, G['tiles'][0], 'up', SIZE), 2 * math.atan(G['half'][1] * ep.fit_tile(G, G['tiles'][0])[3] / G['h']) / 1080)
        self.assertEqual(plan(p, side='up').side, 'up')

    def test_degenerate(self):
        p = flat()
        self.assertIsNone(plan(p, 0, 0))
        self.assertIsNone(plan(p, 0, 9))
        self.assertIsNone(ep.plan(G, p, 0, 1, 'right', 0.5, (0, 1080)))

    def test_the_frame_is_what_the_renderer_decodes(self):
        out = json.loads(ep.frame_bytes('run', 'session', 7, plan(flat()).frame(60)))
        self.assertEqual(out['op'], 'edge_pan')
        self.assertEqual(sorted(out['payload']), ['active', 'cross', 'entry', 'exit', 'from', 'peek', 'side', 'to', 'turn', 'way', 'zoom'])
        self.assertTrue(all(type(v) is int and 0 <= v <= 10000 for k, v in out['payload'].items() if k not in ('active', 'side')))
        self.assertEqual(json.loads(ep.frame_bytes('run', 'session', 8, None))['payload'], {'active': False})


class Hand:
    """EdgePush driven as the sampler drives it: relative hand motion onto OUTPUTS, Hyprland clamping it to the layout."""
    def __init__(self, placements, home=0, cursor=None, neighbour=None):
        self.p, self.home, self.now, self.warps, self.switches = placements, home, 100.0, [], []
        self.push = uc.EdgePush()
        o = OUTPUTS[home]
        self.cursor = cursor or (o['x'] + o['width'] / 2, 300)
        self.previous = self.cursor

    def neighbour(self, edge):
        o = OUTPUTS[self.home]
        return nb.pick_at(G, self.p, self.home, edge, uc.along(o, self.cursor, edge))

    def planner(self, target, edge, along, output):
        return ep.plan(G, self.p, self.home, target, edge, along, (output['width'], output['height']))

    def layout(self, c):
        on = [m for m in MONITORS if m['x'] <= c[0] < m['x'] + m['width'] and m['y'] <= c[1] < m['y'] + m['height']]
        if on: return c
        return min(((min(max(c[0], m['x']), m['x'] + m['width'] - 1), min(max(c[1], m['y']), m['y'] + m['height'] - 1)) for m in MONITORS),
                   key=lambda q: abs(q[0] - c[0]) + abs(q[1] - c[1]))

    def step(self, dx=0, dy=0, dt=0.004, columns=None):
        self.now += dt
        self.cursor = self.layout((self.cursor[0] + dx, self.cursor[1] + dy))
        warp, switch = self.push.step(OUTPUTS, self.home, self.cursor, self.previous, self.now, self.neighbour, columns, self.planner)
        if warp is not None: self.cursor = warp; self.warps.append(warp)
        self.previous = self.cursor
        if switch is not None: self.switches.append(switch)
        return switch

    def move(self, dx, dy=0, steps=1, **kw):
        for _ in range(steps): self.step(dx, dy, **kw)

    def to_right_edge(self):
        o = OUTPUTS[self.home]
        self.cursor = (o['x'] + o['width'] - 2, self.cursor[1])
        self.previous = self.cursor


class PushTests(unittest.TestCase):
    def test_a_brush_of_the_edge_shows_nothing(self):
        h = Hand(flat()); h.to_right_edge()
        h.move(10)
        self.assertIsNone(h.push.frame)

    def test_the_push_starts_a_pan_and_holds_the_cursor_inside(self):
        h = Hand(flat()); h.to_right_edge()
        h.move(10, steps=3)
        self.assertIsNotNone(h.push.frame)
        self.assertEqual(h.push.frame['to'], 1)
        self.assertEqual(h.cursor[0], X1 + 1919 - uc.PAN_HOLD_PX)
        self.assertEqual(h.switches, [])

    def test_nothing_in_the_cone_the_cursor_just_stops(self):
        h = Hand(bearing_placement(14, 26)); h.to_right_edge()
        h.move(20, steps=40)
        self.assertIsNone(h.push.frame)
        self.assertEqual(h.switches, [])
        self.assertLess(h.cursor[0], X2)  # back on home, as the plain confinement puts it

    def test_reaching_the_target_switches_with_the_entry_point(self):
        h = Hand(flat()); h.to_right_edge()
        h.cursor = (h.cursor[0], 324); h.previous = h.cursor
        pl = ep.plan(G, h.p, 0, 1, 'right', uc.along(OUTPUTS[0], h.cursor, 'right'), SIZE)
        while not h.switches: h.step(5)
        target, side, entry = h.switches[0]
        self.assertEqual((target, side), (1, 'right'))
        self.assertAlmostEqual(entry, pl.entry)
        self.assertTrue(h.push.panned or h.push.frame is not None)
        self.assertEqual(h.push.frame['cross'], 10000)

    def test_travel_is_the_hands_however_it_is_cut_up(self):
        pl = None
        ends = []
        for step in (1, 4, 16):
            h = Hand(bearing_placement(60, 0)); h.to_right_edge()
            h.move(5, steps=8)
            start = h.push.track['travel']
            for _ in range(80 // step): h.step(step)
            ends.append(h.push.track['travel'] - start)
        self.assertEqual(ends[0], 80)
        self.assertLessEqual(max(ends) - min(ends), 16)

    def test_a_clamped_edge_reads_the_hand_too(self):
        # Up: nothing past it in the layout. The hold leaves room, so every step of a hand still counts.
        p = [at_direction(0, 0, 0), at_direction(1, 0, 40), at_direction(2, 150, 0)]
        h = Hand(p)
        h.cursor = (h.cursor[0], 1); h.previous = h.cursor
        for _ in range(6): h.step(0, -20, dt=0.004)
        self.assertIsNotNone(h.push.track)
        t0 = h.push.track['travel']
        for _ in range(10): h.step(0, -10)
        self.assertEqual(h.push.track['travel'] - t0, 100)

    def test_pulling_back_undoes_it_and_stays_home(self):
        h = Hand(bearing_placement(60, 0)); h.to_right_edge()
        h.move(10, steps=20)
        at = h.push.frame
        self.assertGreater(at['cross'], 0)
        h.move(-10, steps=5)
        self.assertLess(h.push.frame['cross'], at['cross'])
        h.move(-10, steps=40)
        self.assertIsNone(h.push.frame)
        self.assertEqual((h.switches, h.push.pulled_back), ([], 1))
        self.assertGreaterEqual(h.cursor[0], X1 + 1919 - uc.PAN_PULL_PX - 10 * 40)  # the hand has the cursor again
        self.assertLess(h.cursor[0], X2)

    def test_a_pan_that_rests_goes_back(self):
        h = Hand(bearing_placement(60, 0)); h.to_right_edge()
        h.move(10, steps=20)
        h.step(0, dt=ep.REST_S - 0.05)
        self.assertIsNotNone(h.push.frame)  # a pause is not the end
        h.step(0, dt=0.1)
        self.assertIsNone(h.push.frame)
        self.assertEqual(h.warps[-1], (X1 + 1919 - uc.PAN_PULL_PX, 300))
        self.assertEqual(h.switches, [])

    def test_no_stacking_after_a_switch(self):
        h = Hand(flat()); h.to_right_edge()
        while not h.switches: h.step(5)
        h.push.reset()
        h.home = 1
        h.cursor = (X2 + 1917, 300); h.previous = h.cursor
        h.move(20, steps=100, dt=0.004)  # 0.4 s: still landing
        self.assertEqual((len(h.switches), h.push.frame), (1, None))
        h.now += uc.SWITCH_COOLDOWN_S
        h.move(10, steps=4)
        self.assertEqual(h.push.frame['from'], 1)  # a new push, from the new desktop
        self.assertGreaterEqual(uc.SWITCH_COOLDOWN_S, 0.74)  # the renderer's longest landing

    def test_a_warp_is_never_the_hand(self):
        h = Hand(flat()); h.to_right_edge()
        h.move(10, steps=4)
        t = h.push.track['travel']
        h.step(fp.JUMP_PX + 5)
        self.assertEqual(h.push.track['travel'], t)

    def test_a_key_during_a_pan_ends_it(self):
        h = Hand(flat()); h.to_right_edge()
        h.move(10, steps=4)
        h.home = 2
        h.step(1)
        self.assertIsNone(h.push.frame)

    def test_columns_left_hold_the_push_and_a_late_one_stops_it(self):
        h = Hand(flat()); h.to_right_edge()
        h.move(10, steps=20, columns=lambda edge, now, confirm=False: True)
        self.assertIsNone(h.push.frame)
        self.assertEqual(h.switches, [])
        h = Hand(flat()); h.to_right_edge()
        h.move(5, steps=200, columns=lambda edge, now, confirm=False: confirm)
        self.assertEqual(h.switches, [])
        self.assertGreaterEqual(h.push.pulled_back, 1)

    def test_along_the_edge_changes_nothing(self):
        h = Hand(flat()); h.to_right_edge()
        h.move(10, steps=4)
        before = dict(h.push.frame)
        h.step(0, 15)
        self.assertEqual({k: before[k] for k in ('exit', 'entry', 'to')}, {k: h.push.frame[k] for k in ('exit', 'entry', 'to')})
        self.assertEqual(h.push.frame['peek'], before['peek'])


class TriggerTests(unittest.TestCase):
    def setUp(self):
        self.control = view.ViewControl(view.ViewState(NAMES), 'run', 'session', lambda name: None, None, None)
        self.addCleanup(self.control.join_focus, 2)
        self.addCleanup(self.control.stop_menu_binds)
        self.p = self.control.overview_pointer = uc.UnifiedPointer(self.control); self.addCleanup(self.p.close)
        self.p.set_geometry(G)
        self.control.state.zoomed = True

    def key(self, op):
        return frames(self.control.on_message({'op': op}))

    def test_only_a_hand_that_got_there_lands_in_both_modes(self):
        for mode in view.MAXIMIZED_MODES:
            with self.subTest(mode):
                s = self.control.state
                s.selected, s.maximized_mode = 0, mode
                out = last(frames(self.control.switch_desktop(1, panned=True)), 'view')
                self.assertIs(out['pan'], True)
                self.assertNotIn('cut', out); self.assertNotIn('center', out)
                self.assertEqual((out['selected'], out['mode']), (1, mode))

    def test_every_key_is_still_a_cut(self):
        for mode in view.MAXIMIZED_MODES:
            with self.subTest(mode):
                s = self.control.state
                s.selected, s.maximized_mode = 0, mode
                for op in ('next', 'prev'):
                    out = last(self.key(op), 'view')
                    self.assertIs(out['cut'], True)
                    self.assertNotIn('pan', out)

    def test_a_push_with_no_layout_is_a_cut(self):
        self.assertIs(last(frames(self.control.switch_desktop(1)), 'view')['cut'], True)
        self.p.geometry = None
        self.assertIsNone(self.p.pan_plan(1, 'right', 0.5, OUTPUTS[0]))

    def test_the_overview_and_a_click_never_pan(self):
        s = self.control.state
        s.zoomed = False
        out = last(frames(self.control.select_desktop(1)), 'view')
        self.assertNotIn('pan', out); self.assertNotIn('cut', out)
        self.assertFalse(self.p.can_switch())

    def test_typing_mode_and_a_video_have_no_push(self):
        s = self.control.state
        s.typing = True
        self.assertFalse(self.p.can_switch())
        self.assertEqual(self.control.switch_desktop(1, panned=True), b'')
        s.typing = False
        self.control.video_active = True
        self.assertFalse(self.p.can_switch())
        self.assertEqual(self.control.switch_desktop(1, panned=True), b'')
        self.control.video_active = False

    def test_the_loop_sends_the_newest_frame_once(self):
        a, b = plan(flat()).frame(10), plan(flat()).frame(20)
        self.p.set_pan(a); self.p.set_pan(b)
        out = frames(self.control.poll())
        self.assertEqual([f['payload'] for f in out if f['op'] == 'edge_pan'], [b])
        self.assertFalse(any(f['op'] == 'edge_pan' for f in frames(self.control.poll() or b'')))

    def test_a_refused_landing_sends_the_view_home(self):
        self.p.set_pan(plan(flat()).frame(10))
        self.control.state.typing = True
        self.p.request_switch(0, 1, panned=True)
        out = frames(self.control.poll())
        self.assertEqual([f['payload'] for f in out if f['op'] == 'edge_pan'][-1], {'active': False})
        self.assertEqual(self.control.state.selected, 0)


class HidingClient(FakeUnifiedClient):
    def hide_overview_cursor(self): pass
    def show_overview_cursor(self): pass


class SamplerTests(unittest.TestCase):
    def setUp(self):
        self.control = view.ViewControl(view.ViewState(NAMES), 'run', 'session', lambda name: None, None, None)
        self.addCleanup(self.control.join_focus, 2)
        self.addCleanup(self.control.stop_menu_binds)
        self.p = self.control.overview_pointer = uc.UnifiedPointer(self.control); self.addCleanup(self.p.close)
        self.p.set_geometry(G)
        self.control.state.zoomed = True

    def test_the_whole_way_frames_then_landing_then_the_cursor_at_the_entry_point(self):
        client = HidingClient(cursor=(X1 + 1000, 324))
        sampler = uc.UnifiedSampler(client, NAMES, self.p, BIND_ARGS).start()
        self.addCleanup(sampler.stop)
        seen, hidden = [], False
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline and self.control.state.selected == 0:
            client.cursor = (client.cursor[0] + 12, client.cursor[1])  # the hand pushing on past the edge
            seen += frames(self.control.poll() or b'')
            hidden = hidden or sampler.cursor.hidden is True
            time.sleep(0.004)
        self.assertEqual(self.control.state.selected, 1)
        pans = [f for f in seen if f['op'] == 'edge_pan' and f['payload']['active']]
        self.assertGreater(len(pans), 3)
        self.assertEqual(sorted(f['payload']['peek'] for f in pans), [f['payload']['peek'] for f in pans])
        landing = last(seen, 'view')
        self.assertIs(landing['pan'], True)
        self.assertLess(max(i for i, f in enumerate(seen) if f['op'] == 'edge_pan'), next(i for i, f in enumerate(seen) if f['op'] == 'view'))
        self.assertTrue(hidden)  # the dot, not the real cursor, for the way across
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not any(a[2] == 'XR-2' for a in client.args('confine')): time.sleep(0.005)
        entry = next(a for a in client.args('confine') if a[2] == 'XR-2')
        self.assertEqual(entry[0], X2 + uc.ENTRY_PX)
        self.assertAlmostEqual(entry[1], 324, delta=12)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and sampler.cursor.hidden is not False: time.sleep(0.005)
        self.assertIs(sampler.cursor.hidden, False)  # shown again where the dot went in

    def test_back_to_the_overview_mid_pan_ends_it(self):
        client = FakeUnifiedClient(cursor=(X1 + 1000, 300))
        sampler = uc.UnifiedSampler(client, NAMES, self.p, BIND_ARGS).start()
        self.addCleanup(sampler.stop)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and sampler.push.frame is None:
            client.cursor = (X2 + 2, 300)
            time.sleep(0.004)
        self.assertIsNotNone(sampler.push.frame)
        self.control.state.zoomed = False
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and sampler.push.frame is not None: time.sleep(0.005)
        self.assertIsNone(sampler.push.frame)
        self.assertEqual(self.control.state.selected, 0)


if __name__ == '__main__':
    unittest.main()
