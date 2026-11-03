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
    """The canvas point whose dot (orbit.pointer_direction) is in direction (az, el) degrees."""
    a, e = math.radians(az), math.radians(el)
    return orbit.pointer_at(G, (math.cos(e) * math.sin(a), math.sin(e), -math.cos(e) * math.cos(a)))


def in_view(pointer):
    """True if the dot at canvas point `pointer` is inside the view the overview was laid out in (on its plane)."""
    q = orbit.plane_at(G, orbit.pointer_ray(G, pointer))
    return q is not None and 0 <= q[0] <= W and 0 <= q[1] <= H


ANCHOR_REACH = orbit.reach(G, True)
LO, HI = (W * (1 - ANCHOR_REACH[0]) / 2, H * (1 - ANCHOR_REACH[1]) / 2), (W * (1 + ANCHOR_REACH[0]) / 2, H * (1 + ANCHOR_REACH[1]) / 2)


class OrbitGeometryTests(unittest.TestCase):
    def test_the_centred_desktop_at_its_slot_is_the_canvas_exactly(self):
        for q in ((T1[0], T1[1]), (T1[0] + T1[2] / 2, T1[1] + T1[3] / 2), (T1[0] + T1[2], T1[1] + T1[3] + 30)):
            p = fp.to_canvas(G, fp.place_point(G, T1, DEFAULT_PLACEMENT, *q))  # (the fixture's slots are rounded to 0.01 px)
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

    def test_a_desktop_turned_far_round_is_off_the_plane_but_still_under_a_dot(self):
        # Past ~70 degrees the plane point would exceed the wire's MAX_POINTER_PX; behind you it does not exist. The dot
        # (project: the pointer whose direction is that point's) reaches it anywhere round you.
        c = (T1[0] + T1[2] / 2, T1[1] + T1[3] / 2)
        for x in (750, 900, 1800, -1800):
            p = {**DEFAULT_PLACEMENT, 'x': x}
            self.assertIsNone(fp.to_canvas(G, fp.place_point(G, T1, p, *c)), x)
            pointer = fp.project(G, T1, p, *c)
            seen = orbit._norm(fp.place_point(G, T1, p, *c))
            self.assertLess(math.degrees(orbit._angle(seen, orbit.pointer_ray(G, pointer))), 1e-6)
        self.assertIsNotNone(fp.to_canvas(G, fp.place_point(G, T1, {**DEFAULT_PLACEMENT, 'x': 400}, *c)))


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
    def test_the_dots_angle_is_linear_in_the_pointer_all_the_way_round(self):
        # Regression (drag-speed-linear): the dot was the pointer's ray through the plane inside the view (atan) and, in
        # anchor mode, a quadratic stretch from the view's edge to behind you over half a view, so past the edge each
        # mouse pixel turned the dot up to ~12x further. Now every pixel is the same angle, both axes, the whole reach.
        hw, hh = G['half']
        rate = (math.degrees(2 * hw / W), math.degrees(2 * hh / H))  # the plane's own rate at the view's centre
        self.assertAlmostEqual(rate[0], rate[1], places=6)  # square pixels: the same angle per pixel either way
        for k in range(int(LO[0]), int(HI[0])):
            a0 = math.degrees(orbit.pointer_direction(G, (k, H / 2))[0])
            a1 = math.degrees(orbit.pointer_direction(G, (k + 1, H / 2))[0])
            self.assertAlmostEqual(a1 - a0, rate[0], places=9, msg=k)
        for k in range(int(LO[1]) + 1, int(HI[1]) - 1):
            for x in (W / 2, 0, LO[0], W * 2.7):  # the same at any azimuth (no coupling between the axes)
                e0 = math.degrees(orbit.pointer_direction(G, (x, k))[1])
                e1 = math.degrees(orbit.pointer_direction(G, (x, k + 1))[1])
                self.assertAlmostEqual(e0 - e1, rate[1], places=9, msg=(x, k))

    def test_the_edges_of_the_pointers_reach_are_behind_you_and_overhead(self):
        def clamp(mode, point):
            me = type('P', (), {'geometry': G, 'drag': None, 'control': type('C', (), {'state': type('S', (), {'mode': mode})()})()})()
            me._around = lambda: fp.OverviewPointer._around(me)
            return fp.OverviewPointer._clamp(me, point)
        self.assertEqual(clamp('anchor', (1e9, -1e9)), (HI[0], LO[1]))
        self.assertEqual(clamp('anchor', (-1e9, 1e9)), (LO[0], HI[1]))
        self.assertAlmostEqual(math.degrees(orbit.pointer_direction(G, (HI[0], H / 2))[0]), 180)
        self.assertAlmostEqual(math.degrees(orbit.pointer_direction(G, (LO[0], H / 2))[0]), -180)
        self.assertAlmostEqual(math.degrees(orbit.pointer_direction(G, (W / 2, LO[1]))[1]), 90)
        self.assertAlmostEqual(math.degrees(orbit.pointer_direction(G, (W / 2, HI[1]))[1]), -90)
        for mode in ('follow', 'locked'):  # the view turns with your head: half a view past each edge
            self.assertEqual(clamp(mode, (1e9, -1e9)), (W * (1 + orbit.REACH) / 2, H * (1 - orbit.REACH) / 2))
            self.assertLess(math.degrees(orbit.pointer_direction(G, (W * 1.5, H / 2))[0]), 45)

    def test_a_drag_turns_the_desktop_the_same_angle_for_every_mouse_pixel(self):
        # Regression (drag-speed-linear): a pill dragged at a steady mouse speed turned the desktop faster and faster past
        # the view's edge. Now equal pointer steps turn the grabbed point equal angles all the way round you, and the
        # guarded drag (neighbours parked overhead and underfoot, out of the way) does the same.
        hw, _ = G['half']
        press = pill_centre(T1)
        grabbed = fp.unproject(G, T1, DEFAULT_PLACEMENT, press)
        placements = [{**DEFAULT_PLACEMENT, 'y': 900}, dict(DEFAULT_PLACEMENT), {**DEFAULT_PLACEMENT, 'y': -900}]
        drag = orbit.PillDrag(G, 1, (0, 0), press)
        step = 24  # canvas px per pointer update
        want = math.degrees(2 * hw / W) * step
        last = None
        for sign in (1, -1):
            for k in range(1, int((HI[0] - press[0]) / step) - 1):
                pointer = (press[0] + sign * step * k, press[1])
                xy = orbit.drag_target(G, T1, (0, 0), press, pointer)
                x, y = drag.follow(pointer, placements) if sign == 1 else (None, None)
                if x is not None: placements[1] = {**placements[1], 'x': x, 'y': y}
                for got in [xy] + ([(x, y)] if x is not None else []):
                    az = direction(orbit.place_point(G, T1, {**DEFAULT_PLACEMENT, 'x': got[0], 'y': got[1]}, *grabbed))[0]
                    with self.subTest(sign=sign, k=k):
                        if got[0] == 0: continue  # snapped to the slot (SNAP)
                        # under the dot, so it turned the dot's own angle: k steps of the same size, to the tenth
                        self.assertAlmostEqual(az, math.degrees(orbit.pointer_direction(G, pointer)[0]), delta=0.08)
                        self.assertAlmostEqual(az - math.degrees(orbit.pointer_direction(G, press)[0]), sign * want * k, delta=0.08)
            last = sign
        self.assertEqual(last, -1)

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
        lo, hi = LO, HI
        ends = [(lo[0], None), (hi[0], None), (None, lo[1]), (None, hi[1]), lo, hi, (lo[0], hi[1]), (hi[0], lo[1])]
        for i, tile in enumerate(G['tiles']):
            for extra in ({}, {'distance': 60}, {'distance': 180, 'curvature': 40}, {'curvature': 120}):
                p0 = {**DEFAULT_PLACEMENT, **extra}
                x, y, w, h = fp.pill_rect(tile)
                press = (x + w * 0.8, y + h / 2)  # off the pill's middle too
                grabbed = fp.unproject(G, tile, p0, press)
                for end in ends:
                    end = (press[0] if end[0] is None else end[0], press[1] if end[1] is None else end[1])
                    for k in range(1, 41):
                        pointer = (press[0] + (end[0] - press[0]) * k / 40, press[1] + (end[1] - press[1]) * k / 40)
                        xy = orbit.drag_target(G, tile, (0, 0), press, pointer)
                        if 0 in xy or abs(xy[1]) == 900: continue  # snapped to the slot (SNAP), or at a pole: by design
                        if abs(math.degrees(orbit.pointer_direction(G, pointer)[1])) > 88.5:
                            continue  # the dot at a pole: an upright desktop brings a point off its middle only so close
                        seen = orbit.place_point(G, tile, {**p0, 'x': xy[0], 'y': xy[1]}, *grabbed)
                        off = math.degrees(orbit._angle(orbit._norm(seen), orbit.pointer_ray(G, pointer)))
                        with self.subTest(i=i, extra=extra, end=end, k=k):
                            self.assertLess(off, 0.08)  # was up to 180 degrees at the far edge of the reach


class FullTurnTests(unittest.TestCase):
    def test_pointer_at_is_the_dots_direction_inverted(self):
        if True:
            for i in range(-20, 21):
                for j in range(-20, 21):
                    pointer = (W / 2 + i * (HI[0] - W / 2) / 20, H / 2 + j * (HI[1] - H / 2) / 20)
                    back = orbit.pointer_at(G, orbit.pointer_ray(G, pointer))
                    if abs(j) == 20: continue  # straight up / down: every azimuth is the same place
                    if abs(i) == 20 and abs(back[0] - pointer[0]) > 1: back = (W - back[0], back[1])  # behind: +-180
                    with self.subTest(i=i, j=j):
                        self.assertAlmostEqual(back[0], pointer[0], places=5); self.assertAlmostEqual(back[1], pointer[1], places=5)

    def test_in_anchor_mode_every_direction_round_you_is_one_drag_away_under_the_dot(self):
        press = pill_centre(T1)
        for az in range(-170, 180, 20):
            for el in (-70, -30, 0, 30, 70):
                pointer = pointer_for(az, el)
                xy = orbit.drag_target(G, T1, (0, 0), press, pointer)
                seen = orbit._norm(orbit.place_point(G, T1, {**DEFAULT_PLACEMENT, 'x': xy[0], 'y': xy[1]}, *press))
                with self.subTest(az=az, el=el):
                    self.assertLessEqual(abs(pointer[0] - W / 2), HI[0] - W / 2 + 1e-6)
                    self.assertLess(math.degrees(orbit._angle(seen, orbit.pointer_ray(G, pointer))), 0.6)  # SNAP at most
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
                    self.assertLessEqual(abs(pointer[0] - W / 2), HI[0] - W / 2 + 1e-6)
                    h = fp.hit(G, placements, pointer)
                    if not in_view(pointer): self.assertEqual(h, ('pill', 2))
                    else: self.assertIsNotNone(h)  # inside the view the dot's own ray: the pill, or a desktop over it

    def test_inside_the_view_the_dot_hits_what_is_on_the_plane_under_it(self):
        # (the centred desktop at its slot is the canvas; the others are turned to face you)
        placements = at(0, curvature=60)
        q = fp.hit(G, placements, orbit.from_plane(G, (T1[0] + 30, T1[1] + 40)))
        self.assertEqual(q[:2], ('desktop', 1))
        self.assertAlmostEqual(q[2][0], T1[0] + 30, places=2); self.assertAlmostEqual(q[2][1], T1[1] + 40, places=2)
        self.assertEqual(fp.hit(G, FLAT, orbit.from_plane(G, pill_centre(T1))), ('pill', 1))
        for corner in ((T2[0] + 1, T2[1] + 1), (T2[0] + T2[2] - 1, T2[1] + T2[3] - 1)):
            self.assertEqual(fp.hit(G, FLAT, orbit.from_plane(G, corner))[:2], ('corner', 2))
        for empty in ((W / 2, H - 1), (5, 5)):
            self.assertIsNone(fp.hit(G, placements, orbit.from_plane(G, empty)))

    def test_a_regrabbed_pill_follows_the_dot_round_you(self):
        placements = at(2, x=1200)  # far round to your right, out of the view
        az, el = direction(orbit.place_point(G, T2, placements[2], *pill_centre(T2)))
        press = pointer_for(az, el)
        self.assertEqual(fp.hit(G, placements, press), ('pill', 2))
        drag = orbit.PillDrag(G, 2, (1200, 0), press)
        for k in range(1, 30):
            pointer = (press[0] - 6 * k, press[1] - 8 * k)
            x, y = drag.follow(pointer, placements)
            placements[2] = {**placements[2], 'x': x, 'y': y}
            if y == 0: continue  # held at the slot's height (SNAP, half a degree)
            got = orbit._norm(orbit.place_point(G, T2, placements[2], *pill_centre(T2)))
            self.assertLess(math.degrees(orbit._angle(got, orbit.pointer_ray(G, pointer))), 0.08)  # on the dot


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
