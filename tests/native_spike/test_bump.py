"""Bumping in the overview (bump.py, monitor-collision-bump): a desktop moved or resized pushes the desktops it runs into out
of its way, smoothly, cascading, and they stay where they were pushed. Collision detection on the footprints as seen
from the eye (orbit, distance and curvature included), push resolution, cascades, no room, stability, a changed or restored
layout pushing overlapping desktops apart (settle), how a run uses it (the pill drag, the corner resize, the panel's keys
and sliders, the settings panel's Apply as given, the glide, the normal save path: pushed desktops are saved with the
layout like any other move), the hit-zone handles and Super + drag. The renderer's glide is in the ctests view
(glide_toward) and ipc (the frame's `glide`).

Offline only. No compositor, renderer binary or glasses.
"""
import json
import math
from pathlib import Path
import random
import re
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest import mock

from tools.native_spike import (bind_check, bump, free_pointer as fp, input_lock, ipc, managed, orbit, profiles, resize, unified_cursor,
                                view, xr_control)
from test_free_pointer import PointerBase, X1  # the free pointer driven like the sampler would (same directory)
from tools.native_spike.geometry import DEFAULT_PLACEMENT, PLACEMENT_BOUNDS, validate_placements
from control_wire import in_order

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = json.loads((ROOT / 'tests/native_spike/fixtures/overview_pointer_payloads.json').read_text())
G = fp.parse_overview_geometry(json.loads(FIXTURE['geometry']['line'])['overview_geometry'])
T0, T1, T2 = G['tiles']
FLAT = [dict(DEFAULT_PLACEMENT) for _ in range(3)]
TW = 440.0  # four desktops in a row (a pill's Add): the same canvas, narrower slots
G4 = {**G, 'tiles': [(20 + i * (TW + 20), 435.0, TW, TW * 9 / 16) for i in range(4)]}


def at(i, base=FLAT, **kw):
    out = [dict(p) for p in base]
    out[i] = {**out[i], **kw}
    return out


def prints(g, placements):
    return [orbit.footprint(g, g['tiles'][i], p) for i, p in enumerate(placements)]


def overlaps(g, placements):
    """Every pair's shared solid angle (orbit.overlap), keyed (i, j)."""
    f = prints(g, placements)
    return {(i, j): orbit.overlap(f[i], f[j]) for i in range(len(f)) for j in range(i + 1, len(f))}


def centre(g, i, p):
    """(azimuth, elevation) in degrees of desktop i's centre as placed."""
    t = g['tiles'][i]
    x, y, z = orbit.place_point(g, t, p, t[0] + t[2] / 2, t[1] + t[3] / 2)
    return math.degrees(math.atan2(x, -z)), math.degrees(math.atan2(y, math.hypot(x, z)))


def pill_centre(tile):
    x, y, w, h = fp.pill_rect(tile)
    return (x + w / 2, y + h / 2)


class CollisionTests(unittest.TestCase):
    """What collides is what you see: footprints on the sphere round your eye, not rectangles on a screen."""
    def assert_clear(self, g, placements, before=None):
        for pair, v in overlaps(g, placements).items():
            limit = before[pair] if before else 0.0
            self.assertLessEqual(v, limit + orbit.AREA_EPS, (pair, placements))

    def test_turn_is_the_orbit_rotation(self):
        for a0 in (-0.4, 0.0, 0.3):
            for x, y in ((0, 0), (123, -45), (-1700, 800), (1800, -900)):
                want, got = orbit.rotation(a0, x, y), bump.turn(a0, x, y)
                for i in range(3):
                    for j in range(3): self.assertAlmostEqual(got[i][j], want[i][j], places=12)

    def test_apart_agrees_with_the_overlap_area_everywhere(self):
        rng = random.Random(7)
        for _ in range(400):
            ps = [{'x': rng.randint(-400, 400), 'y': rng.randint(-300, 300), 'distance': rng.choice((15, 40, 100, 200)),
                   'curvature': rng.choice((0, 0, 40, 120))} for _ in range(2)]
            fa, fb = orbit.footprint(G, T0, ps[0]), orbit.footprint(G, T1, ps[1])
            area = orbit.overlap(fa, fb)
            if area > 1e-7: self.assertFalse(bump.apart(fa, fb), ps)  # clearly overlapping: they collide
            if bump.apart(fa, fb): self.assertLessEqual(area, orbit.AREA_EPS, ps)  # apart: nothing shared

    def test_the_default_row_is_clear_but_closer_than_a_degree(self):
        f = prints(G, FLAT)
        self.assertTrue(bump.apart(f[0], f[1]) and bump.apart(f[1], f[2]) and bump.apart(f[0], f[2]))
        self.assertFalse(bump.apart(orbit.footprint(G, T0, {**DEFAULT_PLACEMENT, 'x': 10}), f[1]))  # a degree right: touching

    def test_distance_and_curvature_change_what_collides(self):
        f1 = orbit.footprint(G, T1, DEFAULT_PLACEMENT)
        self.assertTrue(bump.apart(orbit.footprint(G, T0, DEFAULT_PLACEMENT), f1))
        self.assertFalse(bump.apart(orbit.footprint(G, T0, {**DEFAULT_PLACEMENT, 'distance': 85}), f1))  # bigger as you see it
        self.assertTrue(bump.apart(orbit.footprint(G, T0, {**DEFAULT_PLACEMENT, 'distance': 200, 'x': 30}), f1))  # smaller
        # Curving keeps the arc's length, so at the slot's distance it narrows a desktop, while close up (big) its edges
        # come round toward you and it covers more: XR-3 as near as it can be to a flat, big XR-2 collides with a curved one.
        big = {**DEFAULT_PLACEMENT, 'distance': 20}
        self.assertTrue(bump.apart(orbit.footprint(G, T0, {**DEFAULT_PLACEMENT, 'curvature': 120, 'x': 8}), f1))
        flat2 = orbit.footprint(G, T1, big)
        x = next(x for x in range(0, 900) if bump.apart(flat2, orbit.footprint(G, T2, {**DEFAULT_PLACEMENT, 'x': x})))
        self.assertGreater(x, 100)
        self.assertFalse(bump.apart(orbit.footprint(G, T1, {**big, 'curvature': 120}), orbit.footprint(G, T2, {**DEFAULT_PLACEMENT, 'x': x})))
        # Overlap on screen is what counts: a far desktop behind a near one still collides with it.
        self.assertFalse(bump.apart(orbit.footprint(G, T0, {**DEFAULT_PLACEMENT, 'x': 148, 'distance': 200}), f1))

    def test_desktops_round_you_collide_only_where_they_meet(self):
        f2 = orbit.footprint(G, T2, {**DEFAULT_PLACEMENT, 'x': 1700})
        self.assertTrue(bump.apart(orbit.footprint(G, T0, DEFAULT_PLACEMENT), f2))  # one in front, one behind you
        self.assertFalse(bump.apart(orbit.footprint(G, T0, {**DEFAULT_PLACEMENT, 'x': -1700}), orbit.footprint(G, T0, {**DEFAULT_PLACEMENT, 'x': 1800})))


class PushTests(unittest.TestCase):
    def assert_clear(self, g, placements, before=None):
        CollisionTests.assert_clear(self, g, placements, before)

    def test_a_move_into_a_neighbour_pushes_it_just_out_of_the_way(self):
        # The slots are 40 px apart, less the two 6 px frames: about half a degree between the footprints.
        out, p = bump.push(G, 0, FLAT, {'x': 8})  # XR-1 0.8 degrees right, into XR-2
        self.assertEqual(out[0], {**DEFAULT_PLACEMENT, 'x': 8})  # the mover gets where it was asked
        self.assertEqual(p.pushed, (1,)); self.assertFalse(p.blocked)
        self.assertEqual(out[1]['y'], 0); self.assertGreater(out[1]['x'], 0); self.assertLess(out[1]['x'], 8)  # sideways, the least
        self.assertEqual(out[2], DEFAULT_PLACEMENT)  # XR-3 was not in the way yet
        self.assert_clear(G, out)
        f0 = orbit.footprint(G, T0, out[0])
        self.assertFalse(bump.apart(f0, orbit.footprint(G, T1, {**out[1], 'x': out[1]['x'] - 1})))  # a tenth less would touch
        out, p = bump.push(G, 0, FLAT, {'x': 60})  # six degrees: XR-2 is pushed into XR-3, which is pushed in turn
        self.assertEqual(p.pushed, (1, 2)); self.assert_clear(G, out)

    def test_pushing_comes_from_where_it_was_hit(self):
        lifted = at(0, x=132, y=150)  # XR-1 lifted right over XR-2: brought down onto it, XR-2 is pushed down, not aside
        out, p = bump.push(G, 0, lifted, {'y': 30})
        self.assertEqual(p.pushed, (1,)); self.assertLess(out[1]['y'], 0); self.assertEqual(out[1]['x'], 0)
        self.assert_clear(G, out)
        out, p = bump.push(G, 1, FLAT, {'x': -60})  # XR-2 left into XR-1: XR-1 goes left
        self.assertEqual(p.pushed, (0,)); self.assertLess(out[0]['x'], 0); self.assertEqual(out[0]['y'], 0)

    def test_cascading_pushes_every_desktop_in_the_way(self):
        out, p = bump.push(G, 0, FLAT, {'x': 300})
        self.assertEqual(p.pushed, (1, 2)); self.assertEqual(out[0]['x'], 300)
        self.assertGreater(out[2]['x'], 200); self.assertLess(out[1]['x'], 300)
        self.assert_clear(G, out)
        out, p = bump.push(G4, 3, [dict(DEFAULT_PLACEMENT)] * 4, {'x': -500})  # four desktops: the last pushes all three
        self.assertEqual(p.pushed, (0, 1, 2)); self.assert_clear(G4, out)
        self.assertTrue(out[0]['x'] < 0 and out[1]['x'] < 0 and out[2]['x'] < 0)

    def test_a_resize_pushes_its_neighbours_outward_both_ways(self):
        for distance in (60, 25, 10):  # bigger and bigger, to ten times its size (far wider than the view)
            out, p = bump.push(G, 1, FLAT, {'distance': distance})
            self.assertEqual(out[1]['distance'], distance); self.assertFalse(p.blocked)
            self.assertEqual(p.pushed, (0, 2))
            self.assertLess(out[0]['x'], 0); self.assertGreater(out[2]['x'], 0)
            self.assertAlmostEqual(-out[0]['x'], out[2]['x'], delta=2)  # the middle one grows evenly
            self.assert_clear(G, out)
        out, p = bump.push(G, 1, FLAT, {'distance': 150})  # smaller: nothing in the way, nothing pushed
        self.assertEqual((out, p.pushed), (at(1, distance=150), ()))

    def test_curving_a_desktop_pushes_too(self):
        big = at(1, distance=40)
        big, _ = bump.push(G, 1, FLAT, {'distance': 40})
        out, p = bump.push(G, 1, big, {'curvature': 120})
        self.assertEqual(out[1]['curvature'], 120); self.assert_clear(G, out)

    def test_it_never_jumps_through_a_desktop(self):
        # Straight through XR-2 to the far side in one update: the path is walked, so XR-2 is pushed ahead of it.
        out, _ = bump.push(G, 0, FLAT, {'x': 290})
        self.assertGreater(out[1]['x'], 200); self.assertLess(centre(G, 0, out[0])[0], centre(G, 1, out[1])[0])
        self.assert_clear(G, out)

    def test_pushed_desktops_stay_where_they_were_pushed(self):
        out, _ = bump.push(G, 0, FLAT, {'x': 120})
        back, p = bump.push(G, 0, out, {'x': 0})  # the mover goes home: nothing springs back
        self.assertEqual(back[0], DEFAULT_PLACEMENT); self.assertEqual(back[1:], out[1:]); self.assertEqual(p.pushed, ())
        smaller, _ = bump.push(G, 1, bump.push(G, 1, FLAT, {'distance': 30})[0], {'distance': 100})
        self.assertNotEqual(smaller[0], DEFAULT_PLACEMENT)  # a resize undone leaves the neighbours where it pushed them

    def test_round_behind_you_the_long_way_is_free(self):
        self.assertEqual(bump.push(G, 0, FLAT, {'x': -1700})[0][0]['x'], -1700)
        self.assertEqual(bump.push(G, 2, FLAT, {'x': 1700})[0], at(2, x=1700))

    def test_half_a_turn_goes_the_way_that_pushes_least(self):
        # From behind you to the slot is as far either way: XR-1 comes back round the left, where nothing is in the way.
        behind = at(0, x=-1800)
        out, p = bump.push(G, 0, behind, {'x': 0})
        self.assertEqual((out, p.pushed), (FLAT, ()))
        out, p = bump.push(G, 0, at(0, x=1800), {'x': 0})
        self.assertEqual((out, p.pushed), (FLAT, ()))
        out, p = bump.push(G, 0, FLAT, {'x': 1800})  # and out again: the value it was given, round the free side
        self.assertEqual((out[0]['x'], p.pushed), (1800, ()))

    def test_an_overlap_left_by_a_layout_change_is_tolerated_but_never_deeper(self):
        stuck = at(1, x=60)  # XR-2 into XR-3 (the settings panel of an older build, a spacing change...)
        before = overlaps(G, stuck)
        self.assertGreater(before[(1, 2)], 0)
        out, p = bump.push(G, 1, stuck, {'x': 0})  # out of it: free
        self.assertEqual((out, p.pushed), (FLAT, ()))
        out, p = bump.push(G, 1, stuck, {'x': 120})  # deeper in: XR-3 is pushed so that it never gets deeper
        self.assertEqual(out[1]['x'], 120); self.assertIn(2, p.pushed)
        self.assertLessEqual(overlaps(G, out)[(1, 2)], before[(1, 2)] + orbit.AREA_EPS)
        out, _ = bump.push(G, 2, stuck, {'y': 50})  # XR-3 itself can still move: the overlap may only shrink
        self.assertEqual(out[2]['y'], 50); self.assertLessEqual(overlaps(G, out)[(1, 2)], before[(1, 2)] + orbit.AREA_EPS)

    def test_a_huge_desktop_never_gets_its_neighbours_or_itself_stuck(self):
        huge = at(1, distance=10)  # set from an older panel: overlapping both neighbours
        before = overlaps(G, huge)
        for i in (0, 2):
            out, _ = bump.push(G, i, huge, {'y': 900 if i else -900})
            self.assertNotEqual(out[i]['y'], 0, i)
            self.assertLessEqual(overlaps(G, out)[tuple(sorted((i, 1)))], before[tuple(sorted((i, 1)))] + orbit.AREA_EPS)
        out, _ = bump.push(G, 1, huge, {'x': 1800})
        self.assertNotEqual(out[1]['x'], 0)

    def test_alone_or_without_a_layout_the_target_is_taken_as_asked(self):
        self.assertEqual(bump.push(None, 0, FLAT, {'x': 500, 'y': 100})[0], at(0, x=500, y=100))
        g1 = {**G, 'tiles': G['tiles'][:1]}
        self.assertEqual(bump.push(g1, 0, FLAT[:1], {'x': 500})[0], [{**DEFAULT_PLACEMENT, 'x': 500}])
        stale = {**G, 'tiles': G['tiles'][:2]}  # a layout from before a pill's Add: not trusted
        self.assertEqual(bump.push(stale, 0, FLAT, {'x': 60})[0], at(0, x=60))


class NoRoomTests(unittest.TestCase):
    def test_never_past_straight_overhead_the_mover_stops_instead(self):
        ps = [{**DEFAULT_PLACEMENT, 'x': 140, 'y': 600}, {**DEFAULT_PLACEMENT, 'y': 880}, dict(DEFAULT_PLACEMENT)]
        out, p = bump.push(G, 0, ps, {'y': 900})  # XR-1 lifted into XR-2, which is nearly overhead
        self.assertTrue(p.blocked)
        self.assertLess(out[0]['y'], 900); self.assertGreater(out[0]['y'], 600)  # as far as there was room
        self.assertLessEqual(abs(out[1]['y']), orbit.Y_LIMIT)
        CollisionTests.assert_clear(self, G, out)
        validate_placements(out)

    def full_ring(self):
        """Three desktops grown to ten times the size and fully curved, one after the other: they fill the whole way
        round you, so the last one stops growing where it still fits."""
        ps, pushers = FLAT, []
        for i in (1, 0, 2):
            for key, value in (('curvature', 120), ('distance', 10)):
                ps, p = bump.push(G, i, ps, {key: value})
                pushers.append(p)
                CollisionTests.assert_clear(self, G, ps)
        return ps, pushers[-1]

    def test_a_resize_with_no_room_stops_at_the_largest_size_that_fits(self):
        ps, last = self.full_ring()
        self.assertTrue(last.blocked)
        self.assertGreater(ps[2]['distance'], 10); self.assertLess(ps[2]['distance'], 20)  # as big as there was room for
        self.assertEqual(type(ps[2]['distance']), int)
        self.assertEqual((ps[0]['distance'], ps[1]['distance']), (10, 10))

    def test_a_full_ring_turns_together_and_nothing_is_pushed_into_the_mover(self):
        # With no room left round you, carrying one sideways pushes the others round ahead of it; the last one comes round
        # behind the mover, which has moved on by as much, so the whole ring turns as one, in order, nothing overlapping.
        ps, _ = self.full_ring()
        for i, x in ((0, 300), (1, -300), (2, 300)):
            out, p = bump.push(G, i, ps, {'x': ps[i]['x'] + x})
            CollisionTests.assert_clear(self, G, out)
            self.assertEqual(out[i]['x'], orbit.wrap(ps[i]['x'] + x), i)
            for k in range(3):
                # All turned alike, but for the ring's free room: the last one stopped at a whole Distance, 3.6 degrees short
                # of closing it, and the desktop furthest round only starts moving once that is used up.
                self.assertAlmostEqual(orbit._diff(out[k]['x'], ps[k]['x']), x, delta=40, msg=(i, k))


class StabilityTests(unittest.TestCase):
    def test_the_same_edit_always_gives_the_same_result(self):
        for i, target in ((0, {'x': 333, 'y': -40}), (1, {'distance': 22}), (2, {'x': -250, 'curvature': 60})):
            self.assertEqual(bump.push(G, i, FLAT, target)[0], bump.push(G, i, FLAT, target)[0])

    def test_a_drag_held_against_a_neighbour_pushes_it_steadily_and_never_flips(self):
        # XR-1 lifted to half over XR-2, then dragged right while sinking a little: XR-2 is pushed sideways the whole way,
        # one way, steadily, never jumping or switching to up / down and back.
        press = pill_centre(T0)
        drag = orbit.PillDrag(G, 0, (0, 0), press)
        ps = [dict(p) for p in FLAT]
        for k in range(1, 30): ps = drag.follow((press[0], press[1] - 9 * k), ps)
        xs, ways = [], []
        for k in range(1, 80):
            ps = drag.follow((press[0] + 8 * k, press[1] - 9 * 29 + 2 * k), ps)
            xs.append(ps[1]['x']); ways.append(drag.pusher.memory.get(1))
            CollisionTests.assert_clear(self, G, ps)
        self.assertEqual(xs, sorted(xs)); self.assertGreater(xs[-1], 0)  # only ever further away
        self.assertLessEqual(max(b - a for a, b in zip(xs, xs[1:])), 12)  # step by step, no leaps
        seen = [w for w in ways if w is not None]
        self.assertTrue(seen and all(w == seen[0] for w in seen), ways)
        self.assertEqual(ps[1]['y'], 0)

    def test_hysteresis_keeps_the_way_a_desktop_was_pushed(self):
        # Corner on corner: pushed one way, a desktop keeps going that way while the other way is about as cheap.
        pusher = bump.Pusher(G, 0)
        out = pusher.move(FLAT, {'x': 60, 'y': 40})
        first = pusher.memory.get(1)
        self.assertIsNotNone(first)
        for k in range(1, 20):
            out = pusher.move(out, {'x': 60 + k, 'y': 40 - k})  # down and right: diagonal into the corner
            self.assertEqual(pusher.memory[1], first, k)
            CollisionTests.assert_clear(self, G, out)

    def test_a_jittering_pointer_moves_nothing_back_and_forth(self):
        press = pill_centre(T0)
        drag = orbit.PillDrag(G, 0, (0, 0), press)
        ps = [dict(p) for p in FLAT]
        ps = drag.follow((press[0] + 120, press[1]), ps)  # pushing XR-2
        pushed = ps[1]['x']
        seen = set()
        for k in range(30):  # the hand shakes a pixel or two either way
            ps = drag.follow((press[0] + 120 + (k % 3) - 1, press[1] + (k % 2)), ps)
            seen.add(ps[1]['x'])
        self.assertLessEqual(max(seen) - pushed, 2)  # at most the furthest the shake reached
        self.assertGreaterEqual(min(seen), pushed)  # and never back

    def test_pushes_never_fling_a_desktop(self):
        # Every walking step pushes by at most PUSH_REACH times its own travel (plus PUSH_SLACK): the path below has XR-1
        # sweep through all of the row in one update, and each desktop moves along with it.
        pusher = bump.Pusher(G, 0)
        steps = []
        original = pusher._settle

        def spy(state, travel):
            before = list(state)
            ok = original(state, travel)
            if ok: steps.append(max(abs(a[0] - b[0]) + abs(a[1] - b[1]) for k, (a, b) in enumerate(zip(state, before)) if k != 0))
            return ok
        pusher._settle = spy
        pusher.move(FLAT, {'x': 700})
        self.assertTrue(steps)
        self.assertLessEqual(max(steps), bump.PUSH_REACH * bump.STEP_TENTHS + bump.PUSH_SLACK + 1)

    def test_random_edits_never_leave_a_new_overlap_or_a_placement_out_of_bounds(self):
        rng = random.Random(3)
        for g in (G, G4):
            ps = [dict(DEFAULT_PLACEMENT) for _ in g['tiles']]
            for _ in range(40):
                i = rng.randrange(len(ps))
                kind = rng.random()
                if kind < 0.5: target = {'x': orbit.wrap(ps[i]['x'] + rng.randint(-400, 400)), 'y': max(-900, min(900, ps[i]['y'] + rng.randint(-200, 200)))}
                elif kind < 0.75: target = {'distance': rng.randint(*PLACEMENT_BOUNDS['distance'])}
                elif kind < 0.85: target = {'curvature': rng.randint(*PLACEMENT_BOUNDS['curvature'])}
                else: target = dict(DEFAULT_PLACEMENT)
                before = overlaps(g, ps)
                out, _ = bump.push(g, i, ps, target)
                validate_placements(out)
                for pair, v in overlaps(g, out).items():
                    self.assertLessEqual(v, before[pair] + orbit.AREA_EPS, (pair, ps, i, target))
                ps = out

    def test_quick_enough_for_the_pointer_and_for_a_slider_jump(self):
        press = pill_centre(T1)
        drag = orbit.PillDrag(G, 1, (0, 0), press)
        ps = [dict(p) for p in FLAT]
        worst = 0.0
        for k in range(1, 200):
            pointer = (press[0] + 9 * math.sin(k / 9) * k, press[1] - 4 * k + 300 * math.sin(k / 30))
            t = time.perf_counter()
            ps = drag.follow(pointer, ps)
            worst = max(worst, time.perf_counter() - t)
        self.assertLess(worst, 0.05)  # the pointer updates at up to 250 Hz; the loop coalesces motion
        t = time.perf_counter()
        bump.push(G, 0, FLAT, {'x': 1700})  # a slider click across half the room, pushing both others the whole way
        self.assertLess(time.perf_counter() - t, 0.25)


# --- a changed or restored layout ------------------------------------------------------------------------------------------
class SettleTests(unittest.TestCase):
    """bump.settle: desktops that overlap with no mover to push them are pushed apart."""
    def assert_clear(self, g, placements):
        CollisionTests.assert_clear(self, g, placements)

    def test_a_layout_with_nothing_overlapping_is_left_exactly_as_it_is(self):
        for g, ps in ((G, FLAT), (G, at(0, x=-300, y=120)), (G, bump.push(G, 0, FLAT, {'x': 300})[0]),
                      (G4, [dict(DEFAULT_PLACEMENT) for _ in range(4)])):
            self.assertEqual(bump.settle(g, ps), (ps, ()))

    def test_an_old_saved_overlap_is_pushed_apart(self):
        stuck = at(1, x=60)  # XR-2 over XR-3, saved before bumping or by an Apply
        self.assertGreater(overlaps(G, stuck)[(1, 2)], 0)
        out, moved = bump.settle(G, stuck)
        self.assertEqual(moved, (2,)); self.assertEqual(out[:2], stuck[:2])  # the later desktop gives way
        self.assert_clear(G, out)
        validate_placements(out)

    def test_it_is_the_nearest_clear_spot(self):
        out, _ = bump.settle(G, at(1, x=60))
        self.assertEqual(out[2]['y'], 0); self.assertGreater(out[2]['x'], 0)  # straight out, sideways: the short way
        near = {**out[2], 'x': out[2]['x'] - 1}
        self.assertFalse(bump.apart(orbit.footprint(G, T1, out[1]), orbit.footprint(G, T2, near)))  # a tenth less would touch

    def test_desktops_right_on_top_of_each_other_come_apart(self):
        ps = [{**DEFAULT_PLACEMENT, 'x': 132}, dict(DEFAULT_PLACEMENT), {**DEFAULT_PLACEMENT, 'x': -132}]
        out, moved = bump.settle(G, ps)
        self.assertEqual(len(moved), 2)
        self.assert_clear(G, out)

    def test_the_desktops_to_keep_stay_and_the_others_give_way(self):
        stuck = at(1, x=60)
        out, moved = bump.settle(G, stuck, (2,))
        self.assertEqual(moved, (1,)); self.assertEqual(out[2], stuck[2])
        self.assert_clear(G, out)
        out, moved = bump.settle(G, stuck, (9, -1, 2, 2))  # stale or repeated indices do no harm
        self.assertEqual(moved, (1,))

    def test_settled_stays_settled(self):
        once, _ = bump.settle(G, at(1, x=60))
        self.assertEqual(bump.settle(G, once), (once, ()))

    def test_the_row_at_spacing_zero_is_no_overlap(self):
        # Tiles touching: only the frames round them overlap, the desktops do not. They stay in their slots, flat.
        w = 640.0
        g = {**G, 'tiles': [(i * w, 435.0, w, w * 9 / 16) for i in range(3)]}
        self.assertGreater(overlaps(g, FLAT)[(0, 1)], 0)
        self.assertEqual(bump.settle(g, FLAT), (FLAT, ()))
        out, moved = bump.settle(g, at(1, x=20))  # a moved one does count
        self.assertTrue(moved)
        self.assertEqual(overlaps(g, out)[(1, 2)], 0)

    def test_a_huge_desktop_pushes_its_neighbours_clear(self):
        out, moved = bump.settle(G, at(1, distance=10), (1,))
        self.assertEqual(moved, (0, 2))
        self.assert_clear(G, out)

    def test_it_goes_round_a_desktop_that_is_in_the_way(self):
        # XR-3 over XR-2, with XR-1 hard up against XR-2's other side: its nearest clear spot is past nothing that blocks it.
        ps = [{**DEFAULT_PLACEMENT, 'x': 0}, dict(DEFAULT_PLACEMENT), {**DEFAULT_PLACEMENT, 'x': -132}]
        out, _ = bump.settle(G, ps)
        self.assert_clear(G, out)

    def test_near_a_pole_it_stays_in_bounds(self):
        ps = [{**DEFAULT_PLACEMENT, 'x': 132, 'y': 890}, {**DEFAULT_PLACEMENT, 'y': 890}, dict(DEFAULT_PLACEMENT)]
        out, _ = bump.settle(G, ps)
        validate_placements(out)
        self.assert_clear(G, out)

    def test_with_no_room_anywhere_it_stays_and_nothing_breaks(self):
        full = [{**DEFAULT_PLACEMENT, 'distance': 10, 'curvature': 120} for _ in range(3)]
        out, moved = bump.settle(G, full)
        validate_placements(out)
        self.assertEqual(bump.settle(G, full), (out, moved))  # the same every time
        self.assertEqual(bump.settle(G, out)[0], bump.settle(G, bump.settle(G, out)[0])[0])  # and no jitter

    def test_without_a_layout_or_alone_nothing_changes(self):
        stuck = at(1, x=60)
        self.assertEqual(bump.settle(None, stuck), (stuck, ()))
        self.assertEqual(bump.settle({**G, 'tiles': G['tiles'][:2]}, stuck), (stuck, ()))  # stale
        self.assertEqual(bump.settle({**G, 'tiles': G['tiles'][:1]}, stuck[:1]), (stuck[:1], ()))

    def test_random_layouts_all_come_out_clear(self):
        rng = random.Random(11)
        for g in (G, G4):
            for _ in range(40):
                ps = [{'x': rng.randint(-500, 500), 'y': rng.randint(-250, 250), 'distance': rng.choice((60, 100, 100, 150)),
                       'curvature': rng.choice((0, 0, 40))} for _ in g['tiles']]
                out, moved = bump.settle(g, ps)
                validate_placements(out)
                self.assert_clear(g, out)
                self.assertEqual(bump.settle(g, out), (out, ()))
                for i, (a, b) in enumerate(zip(ps, out)):
                    if i not in moved: self.assertEqual(a, b)  # only those that had to move moved
                    self.assertEqual((a['distance'], a['curvature']), (b['distance'], b['curvature']))  # never resized

    def test_quick_enough_for_the_loop(self):
        full = [{**DEFAULT_PLACEMENT, 'distance': 10, 'curvature': 120} for _ in range(3)]
        t = time.perf_counter()
        bump.settle(G, full)  # no room: every direction searched in full
        self.assertLess(time.perf_counter() - t, 0.2)


# --- in a run: ViewControl's keys, sliders and settings panel, and the glide ------------------------------------------------
class Layout:
    """The overview free pointer as ViewControl sees it: the renderer's overview layout."""
    drag = None
    def __init__(self, g): self.geometry = g
    def set_geometry(self, g): self.geometry = g
    def summon(self): pass
    def poll(self, now): return b''
    def level(self, i): return b''


class Settings:
    def load(self): return dict(view.DEFAULT_SETTINGS)
    def save(self, s): pass


class Store:
    """A placement store (profiles.PlacementStore's save): what the normal save path writes."""
    def __init__(self): self.saved = []
    def save(self, placements): self.saved.append([dict(p) for p in placements])


def run_control(g=G, placements=None, store=None):
    names = ['XR-1', 'XR-2', 'XR-3', 'XR-4'][:len(g['tiles']) if g is not None else 3]
    c = view.ViewControl(view.ViewState(names, placements=placements), 'run', 'session', lambda name: None, Settings(), None,
                         placements=store)
    c.overview_pointer = Layout(g)
    return c


def frames(out):
    in_order(out)
    return [json.loads(line) for line in (out or b'').splitlines() if line]


def placement(out):
    sent = [f['payload'] for f in frames(out) if f['op'] == 'placement']
    return sent[-1] if sent else None


class RunTests(unittest.TestCase):
    def test_a_panel_key_that_runs_into_a_neighbour_bumps_it_and_glides(self):
        c = run_control()
        c.on_message({'op': 'menu_toggle'})  # XR-1's panel, Left/right highlighted
        out = c.on_message({'op': 'menu_inc_big'})  # Shift+Right: 15 degrees, straight into XR-2 and XR-3
        s = c.state.placements
        self.assertEqual(s[0]['x'], view.MENU_STEPS['x'][1])
        self.assertGreater(s[1]['x'], 0); self.assertGreater(s[2]['x'], 0)
        sent = placement(out)
        self.assertEqual(sent, {'desktops': s, 'glide': view.GLIDE_MS})  # every desktop, together, gliding
        self.assertEqual([f['payload']['placements'] for f in frames(out) if f['op'] == 'placement_menu'][-1], s)  # the panel shows it
        self.assertEqual(c.bumps, 1)
        CollisionTests.assert_clear(self, G, s)

    def test_a_key_that_bumps_nothing_eases_as_before(self):
        c = run_control()
        c.on_message({'op': 'menu_toggle'})
        out = c.on_message({'op': 'menu_dec'})  # XR-1 0.2 degrees left: nothing there
        self.assertEqual(placement(out), {'desktops': at(0, x=-view.MENU_STEPS['x'][0])})  # no glide
        out = c.on_message({'op': 'menu_dec_big'})  # 15 degrees left: a long way, and still nothing there
        self.assertNotIn('glide', placement(out))

    def test_a_slider_click_that_bumps_nothing_stays_as_it_is(self):
        c = run_control()
        c.on_message({'op': 'menu_toggle'})
        out = c.pill_slide(0, 'distance', (150 - 10) / 190, surface=dict(DEFAULT_PLACEMENT))  # smaller: nothing to push
        self.assertEqual(c.state.placements[0]['distance'], 150)
        self.assertNotIn('glide', placement(out))
        c.pill_release()

    def test_reset_walks_home_and_pushes_whatever_sits_in_its_slot(self):
        c = run_control(placements=[{**DEFAULT_PLACEMENT, 'y': 300}, {**DEFAULT_PLACEMENT, 'x': -140}, dict(DEFAULT_PLACEMENT)])
        c.on_message({'op': 'menu_toggle'})  # XR-1 lifted; XR-2 moved into XR-1's slot
        out = c.on_message({'op': 'menu_reset'})  # R: XR-1 comes back down to its slot, XR-2 pushed out of it
        s = c.state.placements
        self.assertEqual(s[0], DEFAULT_PLACEMENT); self.assertNotEqual(s[1], {**DEFAULT_PLACEMENT, 'x': -140})
        self.assertEqual(placement(out)['glide'], view.GLIDE_MS)
        CollisionTests.assert_clear(self, G, s)

    def test_a_slider_press_glides_and_its_drag_streams(self):
        c = run_control()
        c.on_message({'op': 'menu_toggle'})
        out = c.pill_slide(0, 'distance', (40 - 10) / 190, surface=dict(DEFAULT_PLACEMENT))  # pressed at 40: grows, pushing
        self.assertEqual(c.state.placements[0]['distance'], 40)
        self.assertEqual(placement(out)['glide'], view.GLIDE_MS)
        pusher = c._pusher
        out = c.pill_slide(0, 'distance', (30 - 10) / 190)  # held and dragged on: one edit, no glide
        self.assertEqual(c.state.placements[0]['distance'], 30); self.assertNotIn('glide', placement(out))
        self.assertIs(c._pusher, pusher)  # the same Pusher for the whole slider drag (it remembers which way it pushed)
        c.pill_release()
        self.assertIsNone(c._pusher)
        CollisionTests.assert_clear(self, G, c.state.placements)

    def test_the_settings_panel_apply_applies_exactly_as_given(self):
        c = run_control()
        over = at(2, x=-80)  # XR-3 eight degrees left, onto XR-2
        out = c.on_message({'op': 'placement', 'payload': {'desktops': over}})
        self.assertEqual(c.state.placements, over)  # nothing pushed, the overlap stays
        self.assertGreater(overlaps(G, over)[(1, 2)], 0)
        self.assertEqual(placement(out), {'desktops': over})  # no glide
        self.assertEqual(c.bumps, 0)
        self.assertIsNone(c.poll())  # and nothing settles it: the layout did not change
        self.assertEqual(c.state.placements, over)

    def test_without_the_overview_layout_edits_apply_as_given(self):
        c = run_control(g=None)
        c.on_message({'op': 'menu_toggle'})
        out = c.on_message({'op': 'menu_inc_big'})
        self.assertEqual(c.state.placements, at(0, x=view.MENU_STEPS['x'][1]))
        self.assertNotIn('glide', placement(out))

    def test_a_key_that_bumps_is_saved_with_the_pushed_desktops_like_any_key(self):
        store = Store()
        c = run_control(store=store)
        c.on_message({'op': 'menu_toggle'})
        c.on_message({'op': 'menu_inc_big'})  # into XR-2 and XR-3
        self.assertEqual(store.saved, [])  # debounced, as any panel key
        self.assertIsNotNone(c.save_due)
        c.poll(c.save_due)  # MENU_SAVE_DELAY later
        self.assertEqual(store.saved, [c.state.placements])
        self.assertNotEqual(store.saved[0][1], DEFAULT_PLACEMENT)  # XR-2 where it was pushed
        self.assertNotEqual(store.saved[0][2], DEFAULT_PLACEMENT)

    def test_a_panel_collapsing_saves_the_bump_at_once(self):
        store = Store()
        c = run_control(store=store)
        c.on_message({'op': 'menu_toggle'}); c.on_message({'op': 'menu_inc_big'}); c.on_message({'op': 'menu_toggle'})
        self.assertEqual(store.saved, [c.state.placements])
        self.assertNotEqual(store.saved[0][1], DEFAULT_PLACEMENT)

    def test_a_slider_that_bumps_is_saved_like_any_slider(self):
        store = Store()
        c = run_control(store=store)
        c.on_message({'op': 'menu_toggle'})
        c.pill_slide(0, 'distance', (40 - 10) / 190, surface=dict(DEFAULT_PLACEMENT))
        c.pill_slide(0, 'distance', (30 - 10) / 190)
        self.assertIsNotNone(c.save_due)
        c.pill_release(); c.flush()  # the release (free_pointer._end_drag)
        self.assertEqual(store.saved, [c.state.placements])
        self.assertEqual(store.saved[0][0]['distance'], 30); self.assertNotEqual(store.saved[0][1], DEFAULT_PLACEMENT)

    def test_a_settings_panel_apply_is_saved_at_once_as_given(self):
        store = Store()
        c = run_control(store=store)
        c.on_message({'op': 'placement', 'payload': {'desktops': at(2, x=-80)}})
        self.assertEqual(store.saved, [at(2, x=-80)])

    def test_a_run_saves_bumped_positions_in_the_profile_with_the_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp) / 'run'; runtime.mkdir(mode=0o700)
            client = mock.Mock(runtime_dir=runtime, event_stream=None)
            client.query.return_value = []
            store = profiles.ProfileStore(Path(tmp) / 'profiles.json', 'luma-ultra')
            with managed.view_controls(client, 'run', 'session', ['XR-1', 'XR-2', 'XR-3'], store=store) as control:
                control.set_overview_geometry(G)  # the renderer's layout: bumping is on
                control.on_message({'op': 'menu_toggle'}); control.on_message({'op': 'menu_inc_big'})  # XR-1 into XR-2, XR-3
                moved = [dict(p) for p in control.state.placements]
                self.assertGreater(control.bumps, 0)
                self.assertGreater(moved[1]['x'], 0); self.assertGreater(moved[2]['x'], 0)
            self.assertEqual(profiles.load_profile(store.path, store.model)['placements'], moved)  # saved by the run's end

    # --- the layout changing -------------------------------------------------------------------------------------------
    def test_a_restored_layout_that_overlaps_is_pushed_apart_when_the_layout_arrives(self):
        store = Store()
        stuck = at(1, x=60)
        c = run_control(g=None, placements=stuck, store=store)
        self.assertIsNone(c.poll())  # no layout yet
        c.set_overview_geometry(G)
        out = c.poll()
        s = c.state.placements
        self.assertEqual(s[:2], stuck[:2]); self.assertNotEqual(s[2], stuck[2])
        CollisionTests.assert_clear(self, G, s)
        self.assertEqual(placement(out), {'desktops': s, 'glide': view.GLIDE_MS})  # it bumped: it glides
        self.assertEqual(store.saved, [])  # saved like any move: after the delay
        c.poll(c.save_due)
        self.assertEqual(store.saved, [s])
        self.assertIsNone(c.poll())  # once

    def test_a_layout_with_no_overlap_sends_nothing(self):
        c = run_control(g=None)
        c.set_overview_geometry(G)
        self.assertIsNone(c.poll())
        self.assertEqual((c.bumps, c.save_due), (0, None))

    def test_the_spacing_or_a_desktop_change_settles_too_through_the_layout_they_report(self):
        moved = at(1, x=5)  # half a degree right of its slot: just clear of XR-3 in the row as it is
        self.assertEqual(overlaps(G, moved)[(1, 2)], 0)
        w = 640.0
        closer = {**G, 'tiles': [(i * w, 435.0, w, w * 9 / 16) for i in range(3)]}  # less spacing: the slots came closer
        self.assertGreater(overlaps(closer, moved)[(1, 2)], 0)
        c = run_control(placements=moved)
        c.set_overview_geometry(closer)
        self.assertEqual(placement(c.poll())['glide'], view.GLIDE_MS)
        self.assertEqual(overlaps(closer, c.state.placements)[(1, 2)], 0)

    def test_the_selected_desktop_and_the_open_panels_stay_put(self):
        stuck = at(1, x=60)
        c = run_control(g=None, placements=stuck)
        c.state.selected = 2
        c.set_overview_geometry(G); c.poll()
        self.assertEqual(c.state.placements[2], stuck[2]); self.assertNotEqual(c.state.placements[1], stuck[1])
        c = run_control(g=None, placements=stuck)
        c.state.open_menu(2)
        c.set_overview_geometry(G)
        frames_out = frames(c.poll())
        self.assertEqual(c.state.placements[2], stuck[2])
        self.assertEqual([f['payload']['placements'] for f in frames_out if f['op'] == 'placement_menu'][-1], c.state.placements)

    def test_it_waits_for_a_drag_and_a_desktop_change(self):
        stuck = at(1, x=60)
        c = run_control(g=None, placements=stuck)
        c.overview_pointer.drag = {'kind': 'pill'}
        c.set_overview_geometry(G)
        self.assertIsNone(c.poll()); self.assertEqual(c.state.placements, stuck)
        c.overview_pointer.drag = None
        c.state.desktops_busy = True
        self.assertIsNone(c.poll()); self.assertEqual(c.state.placements, stuck)
        c.state.desktops_busy = False
        self.assertIsNotNone(c.poll())
        CollisionTests.assert_clear(self, G, c.state.placements)

    def test_it_waits_for_a_layout_reset_which_places_every_desktop_itself(self):
        # Ctrl+Alt+R (remember-layout-reset-bind, its `reset` state) restores the spacing, so a new layout arrives mid-reset.
        stuck = at(1, x=60)
        c = run_control(g=None, placements=stuck)
        c.reset = {}
        c.set_overview_geometry(G)
        self.assertEqual(c._settle(), b''); self.assertEqual(c.state.placements, stuck)
        c.reset = None
        self.assertNotEqual(c._settle(), b'')

    def test_a_layout_for_other_desktops_waits_for_the_right_one(self):
        stuck = at(1, x=60)
        c = run_control(g=None, placements=stuck)
        c.set_overview_geometry({**G, 'tiles': G['tiles'][:2]})  # stale: from before a pill's Add
        self.assertIsNone(c.poll()); self.assertEqual(c.state.placements, stuck)
        c.set_overview_geometry(G)
        self.assertIsNotNone(c.poll())

    def test_an_apply_overlap_is_pushed_apart_by_the_next_layout_change(self):
        c = run_control()
        c.on_message({'op': 'placement', 'payload': {'desktops': at(2, x=-80)}})
        c.set_overview_geometry(dict(G))
        self.assertIsNotNone(c.poll())
        CollisionTests.assert_clear(self, G, c.state.placements)

    def test_glide_frames_are_checked(self):
        frame = json.loads(view.placement_frame('run', 'session', 9, FLAT, glide=view.GLIDE_MS))
        self.assertEqual(frame['payload'], {'desktops': FLAT, 'glide': view.GLIDE_MS})
        self.assertNotIn('glide', json.loads(view.placement_frame('run', 'session', 9, FLAT))['payload'])
        for bad in (0, -1, view.MAX_GLIDE_MS + 1, 1.5, True, '240'):
            with self.assertRaises(ValueError): view.placement_frame('run', 'session', 9, FLAT, glide=bad)
        self.assertLess(view.GLIDE_MS, view.MAX_GLIDE_MS)
        # The renderer's bound (spike/include/xr/ipc.hpp) is the same.
        self.assertIn(f'kMaxPlacementGlideMs = {view.MAX_GLIDE_MS};', (ROOT / 'spike/include/xr/ipc.hpp').read_text())


# --- the handles of the hit-zone contract, never the content ------------------------------------------------------------
def free_pointer_binds():
    """The free pointer's left-button binds as installed: (chord, op, options text), from the generated Lua."""
    lua = resize.install_resize_binds_lua(*bind_check.DUMMY_ARGS)
    return [(chord, op, opts) for chord, op, opts in
            re.findall(r'hl\.bind\("([^"]+)", hl\.dsp\.exec_cmd\(c \.\. "(\w+)"\)(?:, \{ ([^}]*) \})?\)', lua)]


class HandleContractTests(PointerBase):
    """Bumping hooks into the shared hit-zone contract's handles (free_pointer.RESIZE_DRAGS / _resize_to for resizes,
    orbit.PillDrag for moves), never the content: a press and drag that starts on a resize handle resizes and bumps,
    one on a move handle moves and bumps, and one on content (hit: 'desktop', or the real cursor has it there) is the
    apps' and changes no placement. hit() itself, which points are which zone, is the contract's (anchor-no-click-
    maximize, rotate-bracket-arrows): this holds for every point tried, whatever the zones."""
    pointer = None  # a pointer class other than fp.OverviewPointer (the run's: UnifiedPointer)

    def setUp(self):
        super().setUp()
        if self.pointer is not None:
            self.p = self.control.overview_pointer = self.pointer(self.control, clock=self.clock)
            self.addCleanup(self.p.close)
            self.control.set_overview_geometry(G)

    def dot_onto(self, i, q, glass=False):
        """Free off the top edge of the desktop the cursor is on (XR-1 to begin with), then the dot onto desktop i's
        surface pixel q (glass px with `glass`)."""
        if self.p.mode != 'free':
            x = X1 + 1920 * (self.p.desktop or 0)
            self.at(x + 960, 300); self.at(x + 1900, 0)
        place = fp.project_glass if glass else fp.project
        aim = place(G, G['tiles'][i], self.control.state.placements[i], *q)
        self.move_canvas(aim[0] - self.p.pointer[0], aim[1] - self.p.pointer[1])

    def points(self, i):
        """Points round and on desktop i: deep in it, in and round each corner, on and past each edge, on its pill."""
        tx, ty, tw, th = G['tiles'][i]
        out = [('middle', (tx + tw / 2, ty + th / 2), False)]
        for name, (cx, cy, sx, sy) in (('tl', (tx, ty, 1, 1)), ('tr', (tx + tw, ty, -1, 1)), ('bl', (tx, ty + th, 1, -1)),
                                       ('br', (tx + tw, ty + th, -1, -1))):
            for d in (-6, 2, 8, 20, 40):  # negative: off the desktop, round the corner
                out.append((f'{name} {d}', (cx + sx * d, cy + sy * d), False))
        for name, q in (('top', (tx + tw / 2, ty)), ('left', (tx, ty + th / 2)), ('right', (tx + tw, ty + th / 2))):
            for d in (-5, 2, 12):
                n = {'top': (0, 1), 'left': (1, 0), 'right': (-1, 0)}[name]
                out.append((f'{name} {d}', (q[0] + n[0] * d, q[1] + n[1] * d), False))
        x, y, w, h = fp.pill_rect(G['tiles'][i])
        out.append(('pill', (x + w / 2, y + h / 2), True))
        return out

    def test_only_a_handle_ever_moves_resizes_or_bumps_and_content_never_does(self):
        seen = set()
        for name, q, glass in self.points(1):
            with self.subTest(point=name):
                self.control.state.placements = [dict(p) for p in FLAT]
                self.p.drag = None
                self.dot_onto(1, q, glass)
                under = self.p.hover if self.p.mode == 'free' else ('cursor',)  # desktop mode: the real cursor, on content
                kind = under[0] if under else 'space'
                seen.add(kind)
                self.op('resize_press')
                started = (self.p.drag or {}).get('kind')
                for _ in range(3): self.move_canvas(30, -30)
                self.op('resize_release')
                s = self.control.state.placements
                if kind in ('desktop', 'cursor', 'space'):  # content (or nothing): the apps', no XR action
                    self.assertNotIn(started, fp.RESIZE_DRAGS + ('pill',))
                    self.assertEqual(s, FLAT)
                elif kind in ('corner', 'edge'):  # a resize handle: it resized, and nothing overlaps
                    self.assertIn(started, fp.RESIZE_DRAGS)
                    self.assertNotEqual(s[1]['distance'], 100)
                    CollisionTests.assert_clear(self, G, s)
                elif kind == 'pill':  # the move handle: it moved, and nothing overlaps
                    self.assertEqual(started, 'pill')
                    self.assertNotEqual((s[1]['x'], s[1]['y']), (0, 0))
                    CollisionTests.assert_clear(self, G, s)
        self.assertTrue({'pill', 'corner'} <= seen and seen & {'desktop', 'cursor'}, seen)  # every kind was tried

    def test_a_bumped_desktop_is_saved_like_any_move(self):
        x, y, w, h = fp.pill_rect(T0)
        self.dot_onto(0, (x + w / 2, y + h / 2), glass=True)
        self.button(True)
        for _ in range(3): self.move_canvas(40, 0)
        s = [dict(p) for p in self.control.state.placements]
        self.assertGreater(s[1]['x'], 0)  # XR-2 pushed
        self.assertEqual(self.store.saves, 0); self.assertIsNotNone(self.control.save_due)  # debounced, as any drag
        self.button(False)
        self.assertEqual(self.store.saved, s); self.assertEqual(self.store.saves, 1)  # at once on release, with the pushed ones

    def test_a_resize_that_bumps_is_saved_like_any_resize(self):
        self.dot_onto(0, (T0[0] + T0[2] + 5, T0[1] - 5))
        self.button(True)
        for _ in range(4): self.move_canvas(40, -40)
        self.button(False)
        s = self.control.state.placements
        self.assertLess(s[0]['distance'], 100); self.assertGreater(s[1]['x'], 0)
        self.assertEqual(self.store.saved, s)

    def test_a_corner_drag_grows_into_the_neighbour_and_bumps_it(self):
        self.dot_onto(0, (T0[0] + T0[2] + 5, T0[1] - 5))  # XR-1's top-right corner, off the desktop
        self.assertEqual(self.p.hover, ('corner', 0, 'tr'))
        self.op('resize_press')
        for _ in range(4): self.move_canvas(40, -40)  # outward: bigger, into XR-2
        self.op('resize_release')
        s = self.control.state.placements
        self.assertLess(s[0]['distance'], 100); self.assertGreater(s[1]['x'], 0)  # XR-2 bumped out of the way
        self.assertGreater(self.p.bumps, 0)
        CollisionTests.assert_clear(self, G, s)

    def test_the_contracts_resize_drag_bumps_the_same_way(self):
        # The shared contract names a resize drag 'resize', with its handle (a corner or an edge): it goes through the
        # same _resize_to, so it bumps like a corner drag here.
        self.dot_onto(0, (T0[0] + T0[2] + 5, T0[1] - 5))
        self.p.drag = {'kind': 'resize', 'index': 0, 'handle': 'tr', 'start': self.p.pointer, 'distance': 100, 'tile': T0}
        for _ in range(4): self.move_canvas(40, -40)
        s = self.control.state.placements
        self.assertLess(s[0]['distance'], 100); self.assertGreater(s[1]['x'], 0)
        CollisionTests.assert_clear(self, G, s)


    # --- Super + drag is the window's (Hyprland's bindm movewindow / resizewindow), never a monitor's --------------------
    def button(self, down, mods=()):
        """The left button going down or up with modifiers `mods` held: what Hyprland runs of the free pointer's binds.
        A bind fires when its modifiers match exactly, unless it has ignore_mods (the binds here have none of their own:
        BindRuleTests)."""
        for chord, op, opts in free_pointer_binds():
            if ('release' in opts) != (not down): continue
            if mods and 'ignore_mods' not in opts: continue
            self.op(op)

    def test_with_super_held_no_handle_starts_a_monitor_move_resize_or_bump(self):
        for name, q, glass in self.points(1):
            with self.subTest(point=name):
                self.control.state.placements = [dict(p) for p in FLAT]
                self.p.drag = None
                self.dot_onto(1, q, glass)
                self.button(True, {'SUPER'})  # Hyprland's bindm takes it: a window move, nothing reaches the run
                self.assertIsNone(self.p.drag)
                for _ in range(3): self.move_canvas(30, -30)
                self.button(False, {'SUPER'})
                self.assertIsNone(self.p.drag)
                self.assertEqual(self.control.state.placements, FLAT)
        self.assertEqual(self.p.bumps, 0)

    def test_super_let_go_before_the_button_starts_nothing_either(self):
        x, y, w, h = fp.pill_rect(T0)
        self.dot_onto(0, (x + w / 2, y + h / 2), glass=True)
        self.assertEqual(self.p.hover, ('pill', 0))
        self.button(True, {'SUPER'})
        self.move_canvas(40, 0)
        self.button(False)  # the button comes up with no modifier: the release reaches the run, with nothing held
        self.move_canvas(40, 0)
        self.assertIsNone(self.p.drag)
        self.assertEqual(self.control.state.placements, FLAT)

    def super_key(self):
        """A Super key going down: what its bind runs (resize.py), through the control socket's path."""
        return self.op(resize.CANCEL_OP)

    def pill_drag(self, i=0):
        x, y, w, h = fp.pill_rect(G['tiles'][i])
        self.dot_onto(i, (x + w / 2, y + h / 2), glass=True)
        self.assertEqual(self.p.hover, ('pill', i))
        self.button(True)  # no modifier: the monitor's drag

    def test_super_going_down_stops_a_monitor_move_where_it_is(self):
        self.pill_drag()
        for _ in range(3): self.move_canvas(40, 0)
        moved = [dict(p) for p in self.control.state.placements]
        self.assertNotEqual(moved, FLAT)
        self.super_key()
        self.assertIsNone(self.p.drag); self.assertEqual(self.p.super_stops, 1)
        self.assertEqual(self.store.saved, moved)  # saved as on a release, pushed desktops with it
        for _ in range(3): self.move_canvas(40, 0)
        self.assertEqual(self.control.state.placements, moved)  # nothing follows the dot any more, or bumps
        self.button(False, {'SUPER'})  # the button comes up later: nothing held
        self.button(False)
        self.assertFalse(self.control.state.menu_open)  # and no click
        self.assertEqual(self.control.state.placements, moved)

    def test_super_going_down_stops_a_resize_where_it_is(self):
        self.dot_onto(0, (T0[0] + T0[2] + 5, T0[1] - 5))
        self.assertEqual(self.p.hover, ('corner', 0, 'tr'))
        self.button(True)
        for _ in range(2): self.move_canvas(40, -40)
        size = [dict(p) for p in self.control.state.placements]
        self.assertLess(size[0]['distance'], 100)
        self.super_key()
        self.assertIsNone(self.p.drag)
        for _ in range(2): self.move_canvas(40, -40)
        self.assertEqual(self.control.state.placements, size)

    def test_super_going_down_before_a_held_pill_has_moved_is_no_click_and_no_move(self):
        self.pill_drag()
        self.super_key()
        self.move_canvas(40, 0)
        self.button(False)
        self.assertEqual(self.control.state.placements, FLAT)
        self.assertFalse(self.control.state.menu_open); self.assertEqual(self.p.pill_clicks, 0)

    def test_super_going_down_stops_a_held_slider_and_its_panel_goes_back_on_its_desktop(self):
        self.control.pill_click(0)  # XR-1's panel open
        x, y, _, _ = fp.panel_rect(T0)
        row = fp.PANEL_ROWS_TOP + fp.PANEL_ROW_H * 2.5  # Distance
        self.dot_onto(0, (x + fp.PANEL_KNOB_LO + 40, y + row), glass=True)
        self.assertEqual(self.p.hover[:3], ('slider', 0, 'distance'))
        self.button(True)
        self.assertEqual(self.p.drag['kind'], 'slider'); self.assertIsNotNone(self.control.state.menu_surface)
        self.super_key()
        self.assertIsNone(self.p.drag); self.assertIsNone(self.control.state.menu_surface)
        s = [dict(p) for p in self.control.state.placements]
        self.move_canvas(60, 0)
        self.assertEqual(self.control.state.placements, s)

    def test_super_going_down_leaves_a_window_drag_and_an_idle_pointer_alone(self):
        self.assertIsNone(self.control.on_message({'op': resize.CANCEL_OP, 'payload': {}}))  # nothing held: nothing
        self.dot_onto(1, (T1[0] - 4, T1[1] + T1[3] / 2))
        # (A window drag: since the dot is one cursor it only starts over content the help card keeps free.)
        self.p.drag = self.p._window_press(1, (T1[0] + 2, T1[1] + T1[3] / 2), self.p.pointer, (100, 100), self.clock.now)
        self.super_key()
        self.assertEqual(self.p.drag['kind'], 'window')
        self.assertEqual(self.p.super_stops, 0)
        self.p.drag = None

    def test_a_monitor_drag_let_go_with_super_held_still_ends(self):
        # The release ignores modifiers too, so a drag ends however its button comes up: also if the Super keys' pcall'd
        # binds were refused and super_down never came.
        self.pill_drag()
        for _ in range(3): self.move_canvas(40, 0)
        moved = [dict(p) for p in self.control.state.placements]
        self.assertNotEqual(moved, FLAT)
        self.button(False, {'SUPER'})
        self.assertIsNone(self.p.drag)
        for _ in range(3): self.move_canvas(40, 0)
        self.assertEqual(self.control.state.placements, moved)  # nothing follows the dot any more

    def test_a_layout_arriving_during_a_drag_settles_when_the_drag_ends(self):
        self.control.poll()  # (the setUp's layout)
        self.control.state.placements = at(2, x=-80)  # overlapping XR-2, from an Apply
        self.pill_drag(0)
        self.control.set_overview_geometry(dict(G))
        self.control.poll()
        self.assertEqual(self.control.state.placements[2]['x'], -80)
        self.button(False)
        self.control.poll()
        CollisionTests.assert_clear(self, G, self.control.state.placements)


class BindRuleTests(unittest.TestCase):
    """Super + drag stays Hyprland's: what makes that true of the binds the run installs."""
    def test_the_press_matches_modifiers_exactly_and_the_release_ignores_them(self):
        binds = free_pointer_binds()
        self.assertEqual([(chord, op) for chord, op, _ in binds], [(resize.RESIZE_BUTTON, 'resize_press'), (resize.RESIZE_BUTTON, 'resize_release')])
        self.assertEqual(binds[0][2], '')  # no option at all on the press: no ignore_mods, no transparent, no non_consuming
        self.assertEqual(binds[1][2], 'release = true, ignore_mods = true')

    def test_the_super_keys_are_watched_without_taking_them(self):
        lua = resize.install_resize_binds_lua(*bind_check.DUMMY_ARGS)
        keys = re.findall(r'pcall\(hl\.bind, "([^"]+)", hl\.dsp\.exec_cmd\(c \.\. "(\w+)"\), \{ ([^}]*) \}\)', lua)
        self.assertEqual(keys, [(key, resize.CANCEL_OP, 'ignore_mods = true, non_consuming = true') for key in resize.SUPER_KEYS])
        self.assertEqual(resize.SUPER_KEYS, ('Super_L', 'Super_R'))
        self.assertEqual(lua.count(f'{resize.BIND_TABLE}[') + lua.count('t[#t + 1] = b'), 2)  # in the table every removal clears

    def test_no_bind_of_the_run_takes_a_super_chord(self):
        args = bind_check.DUMMY_ARGS
        for lua in (view.install_binds_lua(*args), view.install_menu_binds_lua(*args), view.install_send_binds_lua(*args),
                    resize.install_resize_binds_lua(*args), unified_cursor.click_binds_lua(*args),
                    input_lock.lock_binds_lua(*args)):
            self.assertNotIn('SUPER', lua)
        for lua in (view.install_binds_lua(*args), view.install_menu_binds_lua(*args), unified_cursor.click_binds_lua(*args),
                    input_lock.lock_binds_lua(*args)):
            self.assertNotIn('ignore_mods', lua)

    @unittest.skipUnless(shutil.which('Hyprland'), 'needs Hyprland (--verify-config only: no compositor)')
    def test_hyprland_takes_the_super_keys_binds(self):
        # Through the real hl API, in a throwaway HOME, as bind_check does: both pcall'd binds taken, with their options.
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            out = tmp / 'binds.txt'
            config = tmp / 'hyprland.lua'
            config.write_text(resize.install_resize_binds_lua(*bind_check.DUMMY_ARGS) + f"""
local f = assert(io.open("{out}", "w"))
for _, b in ipairs({resize.BIND_TABLE}) do f:write(tostring(b.key), " ", tostring(b.non_consuming), " ", tostring(b.ignore_mods), "\\n") end
f:close()
""")
            env = {'HOME': str(tmp), 'PATH': '/usr/bin:/bin', 'XDG_CONFIG_HOME': str(tmp), 'XDG_STATE_HOME': str(tmp), 'XDG_RUNTIME_DIR': str(tmp),
                   'LANG': 'C.UTF-8'}
            done = subprocess.run([shutil.which('Hyprland'), '--verify-config', '-c', str(config)], env=env, cwd=tmp, timeout=30,
                                  capture_output=True, text=True)
            self.assertEqual(done.returncode, 0, done.stdout[-400:])
            binds = out.read_text().splitlines()
        self.assertEqual(len(binds), 4)
        self.assertEqual(binds[2:], [f'{key} true true' for key in resize.SUPER_KEYS])

    def test_the_cancel_op_reaches_the_run_and_a_dead_run_cleans_up_on_it(self):
        self.assertIn(resize.CANCEL_OP, xr_control.HELPER_OPS)
        frame = (json.dumps({'schema': 1, 'request_id': 1, 'run_id': 'run', 'session': 's', 'op': resize.CANCEL_OP, 'payload': {}}) + '\n').encode()
        self.assertEqual(ipc.decode_control(frame)['op'], resize.CANCEL_OP)
        with mock.patch.object(xr_control, 'drop_stale_menu_binds') as drop:
            self.assertEqual(xr_control.main(['x', '/nonexistent/c.sock', 'run', 's', resize.CANCEL_OP]), 1)
        drop.assert_called_once_with(code=xr_control.REMOVE_RESIZE_BINDS)


class UnifiedHandleContractTests(HandleContractTests):
    """The same with the run's own pointer (unified_cursor.UnifiedPointer)."""
    from tools.native_spike.unified_cursor import UnifiedPointer as pointer


if __name__ == '__main__':
    unittest.main()
