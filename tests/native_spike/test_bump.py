"""Bumping in the overview (bump.py, monitor-collision-bump): a desktop moved or resized pushes the desktops it runs into out
of its way, smoothly, cascading, and they stay where they were pushed. Collision detection on the footprints as seen
from the eye (orbit, distance and curvature included), push resolution, cascades, no room, stability, how a run uses it
(the pill drag, the corner resize, the panel's keys and sliders, the settings panel, the glide), and that no placement
outlives the session. The renderer's glide is in the ctests view (glide_toward) and ipc (the frame's `glide`).

Offline only. No compositor, renderer binary or glasses.
"""
import json
import math
from pathlib import Path
import random
import time
import unittest

from tools.native_spike import bump, free_pointer as fp, orbit, view
from tools.native_spike.geometry import DEFAULT_PLACEMENT, PLACEMENT_BOUNDS, validate_placements

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
        self.assertFalse(bump.apart(orbit.footprint(G, T0, {**DEFAULT_PLACEMENT, 'distance': 90}), f1))  # bigger as you see it
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
                self.assertAlmostEqual(orbit._diff(out[k]['x'], ps[k]['x']), x, delta=12, msg=(i, k))  # all turned alike


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
        for k in range(1, 40): ps = drag.follow((press[0], press[1] - 9 * k), ps)
        xs, ways = [], []
        for k in range(1, 80):
            ps = drag.follow((press[0] + 8 * k, press[1] - 9 * 39 + 2 * k), ps)
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


# --- in a run: ViewControl's keys, sliders and settings panel, and the glide ------------------------------------------------
class Layout:
    """The overview free pointer as ViewControl sees it: the renderer's overview layout."""
    def __init__(self, g): self.geometry = g
    def summon(self): pass


class Settings:
    def load(self): return dict(view.DEFAULT_SETTINGS)
    def save(self, s): pass


def run_control(g=G, placements=None):
    names = ['XR-1', 'XR-2', 'XR-3', 'XR-4'][:len(g['tiles']) if g is not None else 3]
    c = view.ViewControl(view.ViewState(names, placements=placements), 'run', 'session', lambda name: None, Settings(), None)
    c.overview_pointer = Layout(g)
    return c


def frames(out):
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

    def test_the_settings_panel_apply_bumps_too(self):
        c = run_control()
        out = c.on_message({'op': 'placement', 'payload': {'desktops': at(2, x=-80)}})  # XR-3 eight degrees left, into XR-2
        s = c.state.placements
        self.assertEqual(s[2]['x'], -80); self.assertLess(s[1]['x'], 0); self.assertLess(s[0]['x'], 0)
        self.assertEqual(placement(out)['glide'], view.GLIDE_MS)
        CollisionTests.assert_clear(self, G, s)
        # Two desktops changed at once (an older panel's stale copy): applied as given, as before.
        both = at(0, base=at(2, x=-80), x=200)
        c.on_message({'op': 'placement', 'payload': {'desktops': both}})
        self.assertEqual(c.state.placements, both)

    def test_without_the_overview_layout_edits_apply_as_given(self):
        c = run_control(g=None)
        c.on_message({'op': 'menu_toggle'})
        out = c.on_message({'op': 'menu_inc_big'})
        self.assertEqual(c.state.placements, at(0, x=view.MENU_STEPS['x'][1]))
        self.assertNotIn('glide', placement(out))

    def test_glide_frames_are_checked(self):
        frame = json.loads(view.placement_frame('run', 'session', 9, FLAT, glide=view.GLIDE_MS))
        self.assertEqual(frame['payload'], {'desktops': FLAT, 'glide': view.GLIDE_MS})
        self.assertNotIn('glide', json.loads(view.placement_frame('run', 'session', 9, FLAT))['payload'])
        for bad in (0, -1, view.MAX_GLIDE_MS + 1, 1.5, True, '240'):
            with self.assertRaises(ValueError): view.placement_frame('run', 'session', 9, FLAT, glide=bad)
        self.assertLess(view.GLIDE_MS, view.MAX_GLIDE_MS)
        # The renderer's bound (spike/include/xr/ipc.hpp) is the same.
        self.assertIn(f'kMaxPlacementGlideMs = {view.MAX_GLIDE_MS};', (ROOT / 'spike/include/xr/ipc.hpp').read_text())


if __name__ == '__main__':
    unittest.main()
