"""Desktops anywhere around you in the overview (orbit.py): the orbit placement and its mirror of the renderer, the pill
drag's stretched reach and the re-grab of a pill placed anywhere, and the no-overlap guard. The pointer state machine,
pill clicks and gates are in test_free_pointer.py and test_glass_pills.py; the renderer side in the ctests view,
placement_contract, hud, glass and ipc.

Offline only. No compositor, renderer binary or glasses.
"""
import json
import math
from pathlib import Path
import time
import unittest

from tools.native_spike import free_pointer as fp, orbit
from tools.native_spike.geometry import DEFAULT_PLACEMENT, PLACEMENT_BOUNDS, validate_placements

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = json.loads((ROOT / 'tests/native_spike/fixtures/overview_pointer_payloads.json').read_text())
G = fp.parse_overview_geometry(json.loads(FIXTURE['geometry']['line'])['overview_geometry'])
T0, T1, T2 = G['tiles']
FLAT = [dict(DEFAULT_PLACEMENT) for _ in range(3)]
W, H = G['w'], G['h']


def at(i, **kw):
    out = [dict(p) for p in FLAT]
    out[i] = {**out[i], **kw}
    return out


def direction(point):
    x, y, z = point
    return math.degrees(math.atan2(x, -z)), math.degrees(math.atan2(y, math.hypot(x, z)))


def pill_centre(tile):
    x, y, w, h = fp.pill_rect(tile)
    return (x + w / 2, y + h / 2)


def pointer_for(az, el):
    """The canvas point whose stretched direction (anchor mode) is (az, el) degrees: bisection on the monotone map."""
    def solve(f, lo, hi, want):
        for _ in range(80):
            mid = (lo + hi) / 2
            if f(mid) < want: lo = mid
            else: hi = mid
        return (lo + hi) / 2
    hw, hh = G['half']
    u = solve(lambda u: math.degrees(orbit._azimuth(u, hw, True)), -orbit.REACH, orbit.REACH, az)
    v = solve(lambda v: math.degrees(orbit._elevation(u, v, hw, hh, True)), -orbit.REACH, orbit.REACH, el)
    return ((u + 1) * W / 2, (1 - v) * H / 2)


class OrbitGeometryTests(unittest.TestCase):
    def test_the_centred_desktop_at_its_slot_is_the_canvas_exactly(self):
        for q in ((T1[0], T1[1]), (T1[0] + T1[2] / 2, T1[1] + T1[3] / 2), (T1[0] + T1[2], T1[1] + T1[3] + 30)):
            p = fp.project(G, T1, DEFAULT_PLACEMENT, *q)  # (the fixture's slots are rounded to 0.01 px: not quite centred)
            self.assertAlmostEqual(p[0], q[0], places=3); self.assertAlmostEqual(p[1], q[1], places=3)

    def test_x_turns_round_you_and_y_lifts_along_the_meridian_rigidly(self):
        c = (T0[0] + T0[2] / 2, T0[1] + T0[3] / 2)
        a0, e0 = direction(orbit.place_point(G, T0, DEFAULT_PLACEMENT, *c))
        for x, y in ((900, 0), (-900, 0), (1800, 0), (0, 450), (0, -899), (1234, 300)):
            with self.subTest(x=x, y=y):
                p = {**DEFAULT_PLACEMENT, 'x': x, 'y': y}
                a, e = direction(orbit.place_point(G, T0, p, *c))
                self.assertAlmostEqual(e, e0 + y / 10, places=6)
                if abs(e) < 89: self.assertAlmostEqual((a - a0 - x / 10 + 540) % 360 - 180, 0, places=6)
                # a rigid turn about the eye: every distance from the eye and between points is kept
                for q in ((T0[0], T0[1]), (T0[0] + T0[2], T0[1] + T0[3] + 20)):
                    flat, moved = orbit.place_point(G, T0, DEFAULT_PLACEMENT, *q), orbit.place_point(G, T0, p, *q)
                    self.assertAlmostEqual(math.dist(flat, (0, 0, 0)), math.dist(moved, (0, 0, 0)), places=9)

    def test_free_pointer_uses_the_same_maths(self):
        p = {**DEFAULT_PLACEMENT, 'x': 700, 'y': -120, 'yaw': 30, 'curvature': 80, 'distance': 140}
        self.assertEqual(fp.place_point(G, T2, p, 1500, 600), orbit.place_point(G, T2, p, 1500, 600))

    def test_a_desktop_turned_far_round_projects_nowhere(self):
        # Past ~70 degrees the canvas point would exceed the wire's MAX_POINTER_PX; behind you it does not exist.
        for x in (750, 900, 1800, -1800):
            p = {**DEFAULT_PLACEMENT, 'x': x}
            self.assertIsNone(fp.project(G, T1, p, T1[0] + T1[2] / 2, T1[1] + T1[3] / 2), x)
        self.assertIsNotNone(fp.project(G, T1, {**DEFAULT_PLACEMENT, 'x': 400}, T1[0] + T1[2] / 2, T1[1] + T1[3] / 2))


class FacingTests(unittest.TestCase):
    """Every desktop faces the eye (a billboard; the facing settings yaw / pitch / roll are gone)."""
    def facing(self, tile, p):
        """(angle between the desktop's normal and the line of sight, sideways tilt of its top edge), degrees."""
        cx, cy = tile[0] + tile[2] / 2, tile[1] + tile[3] / 2
        c = orbit.place_point(G, tile, p, cx, cy)
        r, l = orbit.place_point(G, tile, p, cx + 1, cy), orbit.place_point(G, tile, p, cx - 1, cy)  # central: the arc's tangent
        t = orbit.place_point(G, tile, p, cx, cy - 1)
        right, up = [r[i] - l[i] for i in range(3)], [t[i] - c[i] for i in range(3)]
        n = orbit._cross(right, up)
        sight = math.degrees(orbit._angle(n, tuple(-v for v in c)))
        meridian = orbit._cross(c, (0.0, 1.0, 0.0))  # horizontal, square to the line of sight
        tilt = math.degrees(math.asin(abs(orbit._dot(orbit._norm(up), orbit._norm(meridian))))) if math.hypot(c[0], c[2]) > 1e-9 else 0.0
        return sight, tilt

    def test_every_desktop_faces_you_upright_at_its_slot_and_anywhere_round_you(self):
        for tile in G['tiles'] + [(10.0, 50.0, 300.0, 170.0), (1500.0, 900.0, 400.0, 225.0)]:
            for x in (-1800, -900, 0, 450, 1800):
                for y in (-900, 0, 300, 900):
                    for extra in ({}, {'distance': 50}, {'distance': 200}, {'curvature': 120}):
                        p = {**DEFAULT_PLACEMENT, 'x': x, 'y': y, **extra}
                        with self.subTest(tile=tile, x=x, y=y, extra=extra):
                            sight, tilt = self.facing(tile, p)
                            self.assertLess(sight, 1e-6)
                            if abs(y) < 900 and tile[1] + tile[3] / 2 == G['h'] / 2: self.assertLess(tilt, 1e-6)

    def test_while_dragged_it_keeps_facing_you(self):
        placements = [dict(p) for p in FLAT]
        press = pill_centre(T2)
        drag = orbit.PillDrag(G, 2, (0, 0), press)
        for k in range(1, 60):
            x, y = drag.follow((press[0] + 25 * k, press[1] - 9 * k), placements)
            placements[2] = {**placements[2], 'x': x, 'y': y}
            self.assertLess(self.facing(T2, placements[2])[0], 1e-6)

    def test_the_facing_settings_are_gone(self):
        self.assertEqual(set(DEFAULT_PLACEMENT), {'x', 'y', 'distance', 'curvature'})
        for key in ('yaw', 'pitch', 'roll'):
            with self.assertRaises(ValueError): validate_placements([{**DEFAULT_PLACEMENT, key: 0}] * 3)


class DragMappingTests(unittest.TestCase):
    def test_inside_the_view_the_stretch_is_the_pointers_own_direction(self):
        for pointer in ((0, 0), (W, H), (W / 3, H * 0.8), (W / 2, H / 2)):
            a, e = orbit.pointer_direction(G, pointer, True)
            b, f = orbit.pointer_direction(G, pointer, False)
            self.assertAlmostEqual(a, b, places=12); self.assertAlmostEqual(e, f, places=12)
            hw, hh = G['half']
            ray = ((2 * pointer[0] / W - 1) * hw, (1 - 2 * pointer[1] / H) * hh, -1)
            self.assertAlmostEqual(math.degrees(a), direction(ray)[0], places=9)
            self.assertAlmostEqual(math.degrees(e), direction(ray)[1], places=9)

    def test_the_edges_of_the_pointers_reach_are_behind_you_and_overhead(self):
        # REACH mirrors OverviewPointer._clamp (half a view past each edge): pushing the dot as far as it goes.
        clamp = fp.OverviewPointer._clamp(type('P', (), {'geometry': G})(), (1e9, -1e9))
        self.assertEqual(clamp, (W * (1 + orbit.REACH) / 2, H * (1 - orbit.REACH) / 2))
        self.assertAlmostEqual(math.degrees(orbit.pointer_direction(G, (W * 1.5, H / 2))[0]), 180)
        self.assertAlmostEqual(math.degrees(orbit.pointer_direction(G, (-W / 2, H / 2))[0]), -180)
        self.assertAlmostEqual(math.degrees(orbit.pointer_direction(G, (W / 2, -H / 2))[1]), 90)
        self.assertAlmostEqual(math.degrees(orbit.pointer_direction(G, (W / 2, H * 1.5))[1]), -90)
        self.assertLess(math.degrees(orbit.pointer_direction(G, (W * 1.5, H / 2), around=False)[0]), 40)  # follow / locked

    def test_the_stretch_is_smooth_and_monotone(self):
        hw, hh = G['half']
        last = None
        for i in range(-400, 401):
            u = i / 200
            a = orbit._azimuth(u, hw, True)
            if last is not None:
                self.assertGreater(a, last)
                self.assertLess(a - last, 0.05)  # no jump, the join at the view's edge included
            last = a
        eps = 1e-6
        slope_in = (orbit._azimuth(1, hw, True) - orbit._azimuth(1 - eps, hw, True)) / eps
        slope_out = (orbit._azimuth(1 + eps, hw, True) - orbit._azimuth(1, hw, True)) / eps
        self.assertAlmostEqual(slope_in, slope_out, places=3)

    def test_drag_target_wraps_snaps_and_stops_at_the_poles(self):
        press = pill_centre(T1)
        self.assertEqual(orbit.drag_target(G, T1, (0, 0), press, press), (0, 0))
        self.assertEqual(orbit.drag_target(G, T1, (0, 0), press, (press[0] + 4, press[1] - 3)), (0, 0))  # within 0.5 deg: snapped
        x, _ = orbit.drag_target(G, T1, (1700, 0), press, (press[0] + 600, press[1]))  # 170 + ~13 degrees
        self.assertTrue(-1800 <= x < 0)  # on round past behind you
        self.assertEqual(orbit.drag_target(G, T1, (0, 800), press, (press[0], -H))[1], 900)
        self.assertTrue(all(PLACEMENT_BOUNDS['x'][0] <= orbit.drag_target(G, T1, (s, 0), press, (px, press[1]))[0] <= PLACEMENT_BOUNDS['x'][1]
                            for s in (-1800, 0, 1800) for px in (-W / 2, 0, W, W * 1.5)))

    def test_the_grabbed_point_stays_on_the_dots_ray_across_its_whole_reach(self):
        # Regression (drag-cursor-sync): in anchor mode the drag read a direction stretched past the view's edge while
        # the dot is drawn on the plane, so the desktop ran ahead of the dot past the edge. Now, for every desktop,
        # distance and curvature, along straight runs to each edge and corner of the dot's reach (OverviewPointer._clamp),
        # the point grabbed at the press is on the dot's own ray (stretched round you in anchor mode, where the
        # renderer draws it) to within the wire's tenth of a degree.
        lo, hi = (-W / 2, -H / 2), (W * 1.5, H * 1.5)
        ends = [(lo[0], None), (hi[0], None), (None, lo[1]), (None, hi[1]), lo, hi, (lo[0], hi[1]), (hi[0], lo[1])]
        for i, tile in enumerate(G['tiles']):
            for extra in ({}, {'distance': 60}, {'distance': 180, 'curvature': 40}, {'curvature': 120}):
                p0 = {**DEFAULT_PLACEMENT, **extra}
                x, y, w, h = fp.pill_rect(tile)
                press = (x + w * 0.8, y + h / 2)  # off the pill's middle too
                grabbed = fp.unproject(G, tile, p0, press)
                for end, around in ((e, a) for e in ends for a in (False, True)):
                    end = (press[0] if end[0] is None else end[0], press[1] if end[1] is None else end[1])
                    for k in range(1, 41):
                        pointer = (press[0] + (end[0] - press[0]) * k / 40, press[1] + (end[1] - press[1]) * k / 40)
                        xy = orbit.drag_target(G, tile, (0, 0), press, pointer, around)
                        if 0 in xy or abs(xy[1]) == 900: continue  # snapped to the slot (SNAP), or at a pole: by design
                        if abs(math.degrees(orbit.pointer_direction(G, pointer, around)[1])) > 88.5:
                            continue  # the dot at a pole: an upright desktop brings a point off its middle only so close
                        seen = orbit.place_point(G, tile, {**p0, 'x': xy[0], 'y': xy[1]}, *grabbed)
                        off = math.degrees(orbit._angle(orbit._norm(seen), orbit.pointer_ray(G, pointer, around)))
                        with self.subTest(i=i, extra=extra, end=end, around=around, k=k):
                            self.assertLess(off, 0.08)  # was up to 180 degrees at the far edge of the reach


class FullTurnTests(unittest.TestCase):
    def test_pointer_at_is_the_dots_direction_inverted(self):
        for around in (False, True):
            for i in range(-20, 21):
                for j in range(-20, 21):
                    pointer = (W / 2 + i * W / 20 * (1 if around else 0.5), H / 2 + j * H / 20 * (1 if around else 0.5))
                    back = orbit.pointer_at(G, orbit.pointer_ray(G, pointer, around), around)
                    if around and abs(j) == 20: continue  # straight up / down: every azimuth is the same place
                    if around and abs(i) == 20 and back[0] != pointer[0]: back = (W - back[0], back[1])  # behind: +-180
                    with self.subTest(around=around, i=i, j=j):
                        self.assertAlmostEqual(back[0], pointer[0], places=5); self.assertAlmostEqual(back[1], pointer[1], places=5)

    def test_in_anchor_mode_every_direction_round_you_is_one_drag_away_under_the_dot(self):
        press = pill_centre(T1)
        for az in range(-170, 180, 20):
            for el in (-70, -30, 0, 30, 70):
                pointer = pointer_for(az, el)
                xy = orbit.drag_target(G, T1, (0, 0), press, pointer, around=True)
                seen = orbit._norm(orbit.place_point(G, T1, {**DEFAULT_PLACEMENT, 'x': xy[0], 'y': xy[1]}, *press))
                with self.subTest(az=az, el=el):
                    self.assertLessEqual(abs(pointer[0] - W / 2), W * orbit.REACH / 2 + 1e-6)
                    self.assertLess(math.degrees(orbit._angle(seen, orbit.pointer_ray(G, pointer, True))), 0.6)  # SNAP at most
                    self.assertAlmostEqual(direction(seen)[0], az, delta=0.6)


class RegrabTests(unittest.TestCase):
    def test_a_pill_anywhere_round_you_can_be_hovered_and_grabbed_again(self):
        for x in range(-1800, 1801, 300):
            for y in (-500, 0, 400):
                placements = at(2, x=x, y=y)
                with self.subTest(x=x, y=y):
                    validate_placements(placements)
                    az, el = direction(orbit.place_point(G, T2, placements[2], *pill_centre(T2)))
                    pointer = pointer_for(az, el)
                    self.assertLessEqual(abs(pointer[0] - W / 2), W * orbit.REACH / 2 + 1e-6)
                    h = fp.hit(G, placements, pointer, around=True)
                    if orbit.beyond_view(G, pointer): self.assertEqual(h, ('pill', 2))
                    else: self.assertIsNotNone(h)  # inside the view the dot's own ray: the pill, or a desktop over it

    def test_without_the_stretch_nothing_changes_inside_the_view(self):
        placements = at(1, yaw=30, curvature=60)
        for pointer in ((T0[0] + 30, T0[1] + 40), pill_centre(T1), (W / 2, H - 1), (5, 5)):
            self.assertEqual(fp.hit(G, placements, pointer, around=True), fp.hit(G, placements, pointer))

    def test_a_regrabbed_pill_follows_the_dot_round_you(self):
        placements = at(2, x=1200)  # far round to your right, out of the view
        az, el = direction(orbit.place_point(G, T2, placements[2], *pill_centre(T2)))
        press = pointer_for(az, el)
        self.assertEqual(fp.hit(G, placements, press, around=True), ('pill', 2))
        self.assertIsNone(fp.hit(G, placements, press))  # follow / locked: the dot there is in front, on nothing
        drag = orbit.PillDrag(G, 2, (1200, 0), press, around=True)
        for k in range(1, 30):
            pointer = (press[0] - 6 * k, press[1] - 8 * k)
            x, y = drag.follow(pointer, placements)
            placements[2] = {**placements[2], 'x': x, 'y': y}
            if y == 0: continue  # held at the slot's height (SNAP, half a degree)
            got = orbit._norm(orbit.place_point(G, T2, placements[2], *pill_centre(T2)))
            self.assertLess(math.degrees(orbit._angle(got, orbit.pointer_ray(G, pointer, True))), 0.08)  # on the dot


class FootprintTests(unittest.TestCase):
    def test_the_default_row_does_not_overlap(self):
        f = [orbit.footprint(G, t, p) for t, p in zip(G['tiles'], FLAT)]
        for a, b in ((0, 1), (1, 2), (0, 2)): self.assertEqual(orbit.overlap(f[a], f[b]), 0, (a, b))

    def test_overlap_grows_with_cover_and_is_symmetric(self):
        b = orbit.footprint(G, T1, DEFAULT_PLACEMENT)
        last = 0
        for x in (20, 40, 80, 120):
            a = orbit.footprint(G, T0, {**DEFAULT_PLACEMENT, 'x': x})
            v = orbit.overlap(a, b)
            self.assertGreater(v, last); last = v
            self.assertAlmostEqual(v, orbit.overlap(b, a), places=9)

    def test_curved_turned_and_distant_desktops_have_sane_footprints(self):
        for p in ({**DEFAULT_PLACEMENT, 'curvature': 120, 'distance': 50, 'yaw': 60, 'pitch': -45, 'roll': 45},
                  {**DEFAULT_PLACEMENT, 'x': 1800, 'y': 900, 'distance': 200}):
            f = orbit.footprint(G, T1, p)
            self.assertIsNotNone(f.vertices)
            self.assertLess(math.degrees(f.radius), 60)
            self.assertAlmostEqual(orbit.overlap(f, f), orbit._area(f.vertices), places=9)


class GuardTests(unittest.TestCase):
    def test_a_move_into_a_neighbour_stops_before_it_touches(self):
        x, y = orbit.allowed_move(G, 0, (0, 0), (300, 0), FLAT)
        self.assertEqual(y, 0); self.assertGreaterEqual(x, 0); self.assertLess(x, 9)  # 40 px gap, ~0.9 degrees
        f = orbit.footprint(G, T0, {**DEFAULT_PLACEMENT, 'x': x})
        self.assertEqual(orbit.overlap(f, orbit.footprint(G, T1, DEFAULT_PLACEMENT)), 0)

    def test_it_slides_along_the_neighbour_and_the_corner_comes_first(self):
        moved = orbit.allowed_move(G, 0, (0, 0), (300, 300), FLAT)
        self.assertNotEqual(moved, (0, 0))
        f = orbit.footprint(G, T0, {**DEFAULT_PLACEMENT, 'x': moved[0], 'y': moved[1]})
        self.assertEqual(orbit.overlap(f, orbit.footprint(G, T1, DEFAULT_PLACEMENT)), 0)
        if moved != (300, 300):  # an L-shaped slide hands back its corner first...
            self.assertTrue(moved[0] in (0, 300) or moved[1] in (0, 300))
            self.assertEqual(orbit.allowed_move(G, 0, moved, (300, 300), FLAT), (300, 300))  # ...and the rest next time

    def test_no_jumping_through_a_neighbour(self):
        # Straight through XR-2 to the far side in one update: the path is walked, so it stops at XR-2.
        self.assertLess(orbit.allowed_move(G, 0, (0, 0), (290, 0), FLAT)[0], 9)

    def test_round_behind_you_the_long_way_is_free(self):
        self.assertEqual(orbit.allowed_move(G, 0, (0, 0), (-1700, 0), FLAT), (-1700, 0))
        self.assertEqual(orbit.allowed_move(G, 2, (0, 0), (1700, 0), FLAT), (1700, 0))

    def test_an_overlap_left_by_the_panel_never_gets_deeper_and_never_moves_to_another_desktop(self):
        stuck = at(1, x=60)  # XR-2 pushed into XR-3 from the panel
        self.assertEqual(orbit.allowed_move(G, 1, (60, 0), (0, 0), stuck), (0, 0))  # can get out
        self.assertLessEqual(orbit.allowed_move(G, 1, (60, 0), (200, 0), stuck)[0], 60)  # not deeper in
        x, _ = orbit.allowed_move(G, 1, (60, 0), (-900, 0), stuck)  # out of XR-3, but not into XR-1 instead
        f = orbit.footprint(G, T1, {**DEFAULT_PLACEMENT, 'x': x})
        self.assertEqual(orbit.overlap(f, orbit.footprint(G, T0, DEFAULT_PLACEMENT)), 0)

    def test_a_desktop_held_against_a_neighbour_never_flips_round_the_other_way(self):
        placements = [dict(p) for p in FLAT]
        press = pill_centre(T0)
        drag = orbit.PillDrag(G, 0, (0, 0), press)
        xs = []
        for k in range(1, 121):  # XR-1 pushed right into XR-2, the pointer carried on to the far edge of its reach
            x, y = drag.follow((press[0] + k * 20, press[1]), placements)
            placements[0] = {**placements[0], 'x': x, 'y': y}
            xs.append(x)
        self.assertTrue(all(0 <= x < 9 for x in xs), xs[-5:])

    def test_a_panel_key_meanwhile_is_carried_on_from(self):
        placements = [dict(p) for p in FLAT]
        press = pill_centre(T2)
        drag = orbit.PillDrag(G, 2, (0, 0), press)
        placements[2]['x'] = drag.follow((press[0] + 50, press[1]), placements)[0]
        placements[2]['x'] = 1795  # a key turned it round behind you meanwhile
        x, _ = drag.follow((press[0] + 51, press[1]), placements)
        self.assertTrue(abs(orbit._diff(x, 1795)) <= 1800)
        validate_placements(placements)

    def test_alone_or_without_geometry_the_target_is_taken_as_asked(self):
        self.assertEqual(orbit.allowed_move(None, 0, (0, 0), (500, 100), FLAT), (500, 100))
        g1 = {**G, 'tiles': G['tiles'][:1]}
        self.assertEqual(orbit.allowed_move(g1, 0, (0, 0), (500, 100), FLAT[:1]), (500, 100))

    def test_a_drag_is_quick_enough_for_the_pointer(self):
        placements = [dict(p) for p in FLAT]
        press = pill_centre(T1)
        drag = orbit.PillDrag(G, 1, (0, 0), press)
        worst = 0.0
        for k in range(1, 200):
            pointer = (press[0] + 9 * math.sin(k / 9) * k, press[1] - 4 * k)
            t = time.perf_counter()
            x, y = drag.follow(pointer, placements)
            worst = max(worst, time.perf_counter() - t)
            placements[1] = {**placements[1], 'x': x, 'y': y}
        self.assertLess(worst, 0.05)  # the pointer updates at up to 250 Hz; the loop coalesces motion


if __name__ == '__main__':
    unittest.main()
