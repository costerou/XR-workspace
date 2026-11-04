"""How close desktops get before they bump (bump-closer, 2026-11-03, after Cody: "allow the monitors to get closer before
they nudge each other out of the way"). What each desktop keeps round it: a couple of pixels between two neighbours
(CLEARANCE_PX on each), and above and below only the strips its level bar and pill really take. Measured on the canvas of a real run
(journal, 2026-11-03: 1920 x 1200, three desktops at spacing 0, grown to distance 31) as well as the fixture's, and that
nothing else undoes it: a neighbour's content stays its own, a still pointer moves nothing, and settling keeps the same rule.

Offline only. No compositor, renderer binary or glasses.
"""
import json
import math
from pathlib import Path
import random
import re
import unittest
import unittest.mock

from tools.native_spike import bump, free_pointer as fp, orbit, view
from tools.native_spike.geometry import DEFAULT_PLACEMENT

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = json.loads((ROOT / 'tests/native_spike/fixtures/overview_pointer_payloads.json').read_text())
G = fp.parse_overview_geometry(json.loads(FIXTURE['geometry']['line'])['overview_geometry'])
REAL = {'w': 1920, 'h': 1200, 'half': (0.413596, 0.258498), 'tiles': [(i * 640.0, 400.0, 640.0, 400.0) for i in range(3)]}
DESKTOPS = [{**DEFAULT_PLACEMENT, 'x': x, 'y': y, 'distance': d} for x, y, d in ((-13, 57, 31), (331, 59, 31), (-75, -182, 69))]
BORDER_PX = float(re.search(r'kBorderPx = ([\d.]+)f', (ROOT / 'spike/src/gl.cpp').read_text() + (ROOT / 'spike/include/xr/view.hpp').read_text()).group(1))


def place(**kw):
    return {**DEFAULT_PLACEMENT, **kw}


def px_per_radian(g):
    return g['w'] / (2 * g['half'][0])


def direction(g, i, p, x, y):
    return orbit._norm(orbit.place_point(g, g['tiles'][i], p, x, y))


def apart(g, i, a, j, b):
    return bump.apart(orbit.footprint(g, g['tiles'][i], a), orbit.footprint(g, g['tiles'][j], b))


def least(g, i, a, j, b, key, clear, step):
    """The value of b[key] nearest `a` that is clear of it, walking from `clear` (known to be clear) by `step`."""
    value = clear
    while apart(g, i, a, j, {**b, key: value + step}): value += step
    return value


def side_gap(g, a, b):
    """Canvas px between desktop 0's right edge and desktop 1's left edge at mid height, as you see them."""
    t0, t1 = g['tiles'][:2]
    return orbit._angle(direction(g, 0, a, t0[0] + t0[2], t0[1] + t0[3] / 2), direction(g, 1, b, t1[0], t1[1] + t1[3] / 2)) * px_per_radian(g)


def stack(g, d=100, shift=0, lower=None):
    """Desktop 1 straight above desktop 0 (`shift` tenths of a degree off to the right), as low as it goes: (below, above)."""
    below = {**(lower or place()), 'distance': d}
    above = place(x=orbit._diff(orbit.slot_azimuth(g, g['tiles'][0]) / orbit.TENTH, orbit.slot_azimuth(g, g['tiles'][1]) / orbit.TENTH), distance=d)
    above = {**above, 'x': round(above['x']) + shift}
    return below, {**above, 'y': least(g, 0, below, 1, above, 'y', 500, -1)}


def rect_in_view(g, i, p, rect):
    """A surface rectangle (x, y, w, h) on desktop i -> its span in view directions: (azimuth lo, hi, elevation lo, hi), radians."""
    x, y, w, h = rect
    dirs = [direction(g, i, p, *q) for q in ((x, y), (x + w, y), (x, y + h), (x + w, y + h))]
    az = [math.atan2(d[0], -d[2]) for d in dirs]
    el = [math.asin(d[1]) for d in dirs]
    return min(az), max(az), min(el), max(el)


def pill_on_surface(tile, p):
    x, y, w, h = fp.pill_rect(tile)
    (x0, y0), (x1, y1) = fp.glass_to_desktop(tile, p, x, y), fp.glass_to_desktop(tile, p, x + w, y + h)
    return (x0, y0, x1 - x0, y1 - y0)


class Baseline(unittest.TestCase):
    def test_neighbours_stop_a_couple_of_pixels_apart(self):
        self.assertEqual(2 * orbit.CLEARANCE_PX, 2)

    def test_the_frame_needs_no_more_than_that(self):
        # Only the selected desktop has a frame (one `highlight`, and for a moment in an edge pan's landing the one it left, fading out)
        # and it is drawn before every desktop's picture, so a neighbour covers it: it can neither meet another frame nor
        # show over the neighbour, however close they are.
        source = (ROOT / 'spike/src/gl.cpp').read_text()
        frame, pictures = source.index('n = s.build(s.border_mesh'), source.index('s.build(s.tile_meshes[i]')
        self.assertLess(frame, pictures)
        self.assertLess(source.index('frame_of(std::size_t(highlight), highlight_alpha);'), pictures)
        self.assertLess(source.index('frame_of(pan->left, pan->left_alpha);'), pictures)
        self.assertGreater(BORDER_PX, 2 * orbit.CLEARANCE_PX)  # it still shows, thinner, in the gap


class SideBySide(unittest.TestCase):
    def test_level_neighbours_come_up_to_a_couple_of_pixels_apart(self):
        for g in (G, REAL):
            tick = math.radians(0.1) * px_per_radian(g)
            for d in (100, 69, 31):
                with self.subTest(g=g['w'], d=d):
                    a = place(distance=d, x=-20)
                    b = place(distance=d)
                    b['x'] = least(g, 0, a, 1, b, 'x', 700, -1)
                    self.assertFalse(apart(g, 0, a, 1, {**b, 'x': b['x'] - 1}))
                    gap = 2 * orbit.CLEARANCE_PX / (d / 100)  # on the surface: it grows with the desktop
                    self.assertGreaterEqual(side_gap(g, a, b), gap - 1.5)  # (a slot off to the side is a little further away)
                    self.assertLessEqual(side_gap(g, a, b), gap + tick + 1.5)  # (the wire's 0.1 degree step)

    def test_side_by_side_is_less_than_a_third_of_what_it_was(self):
        a = place(distance=31, x=-13, y=57)
        b = place(distance=31, y=59)
        b['x'] = least(REAL, 0, a, 1, b, 'x', 700, -1)
        self.assertLessEqual(b['x'], 321)                    # master's limit: 327
        self.assertLess(side_gap(REAL, a, b), 60)            # at mid height; 82 px on master
        level = place(distance=31, x=-13)
        c = place(distance=31)
        c['x'] = least(REAL, 0, level, 1, c, 'x', 700, -1)
        self.assertLess(side_gap(REAL, level, c), 12)        # level with your eyes they do not lean; 34 px on master

    def test_raised_desktops_lean_together_at_the_top(self):
        # Each faces you, so two raised ones meet at their top corners and open downward. What you see, not a margin.
        a, b = place(distance=31, x=-13, y=300), place(distance=31, y=300)
        b['x'] = least(REAL, 0, a, 1, b, 'x', 900, -1)
        t0, t1 = REAL['tiles'][:2]
        def at(f):
            return orbit._angle(direction(REAL, 0, a, t0[0] + t0[2], t0[1] + f * t0[3]), direction(REAL, 1, b, t1[0], t1[1] + f * t1[3]))
        self.assertLess(at(0), at(0.5))
        self.assertLess(at(0.5), at(1))


class Stacked(unittest.TestCase):
    def assert_handles_clear(self, g, below, above):
        """The pill under the upper desktop and the bar over the lower one show in full: on no picture, nor on each other."""
        pill = rect_in_view(g, 1, above, pill_on_surface(g['tiles'][1], above))
        bar = rect_in_view(g, 0, below, fp.level_bar_rect(g['tiles'][0]))
        share_x = pill[0] < bar[1] and bar[0] < pill[1]
        if share_x: self.assertGreaterEqual(pill[2], bar[3] - 1e-3)  # the pill's bottom is above the bar's top

    def test_right_above_each_other_the_pill_and_the_bar_both_fit(self):
        for g in (G, REAL):
            for d in (100, 69, 31):
                with self.subTest(g=g['w'], d=d):
                    below, above = stack(g, d)
                    self.assert_handles_clear(g, below, above)

    def test_it_keeps_exactly_the_room_of_the_pill_and_the_bar(self):
        g = REAL
        below, above = stack(g, 100)
        t = g['tiles'][0]
        lift = rect_in_view(g, 1, above, (t[0] + 640 + 300, t[1] + t[3], 40, 0))[2]
        top = rect_in_view(g, 0, below, (t[0] + 300, t[1], 40, 0))[3]
        gap = (lift - top) * px_per_radian(g)
        need = fp.PILL_GAP + fp.PILL_THICKNESS + fp.LEVEL_BAR_GAP + fp.LEVEL_BAR_H
        self.assertGreaterEqual(gap, need - 2)
        self.assertLessEqual(gap, need + math.radians(0.1) * px_per_radian(g) + 2)

    def test_off_to_one_side_only_the_bar_is_kept(self):
        # Offset past where the pill and the bar meet, the lower bar only has to clear the upper desktop's bottom edge: much
        # closer than right above each other, and closer than master's 46 px.
        g = REAL
        straight, offset = stack(g, 100), stack(g, 100, shift=70)
        self.assertLess(offset[1]['y'], straight[1]['y'] - 3)  # 7 degrees off: more than a pill wide
        self.assert_handles_clear(g, *offset)
        t = g['tiles'][1]
        lift = rect_in_view(g, 1, offset[1], (t[0] + 300, t[1] + t[3], 40, 0))[2]
        top = rect_in_view(g, 0, offset[0], (g['tiles'][0][0] + 300, g['tiles'][0][1], 40, 0))[3]
        self.assertLess((lift - top) * px_per_radian(g), 46)

    def test_past_the_handles_only_the_clearance_is_kept(self):
        # Off to one side by most of a desktop: the two only share a corner, and neither's handles are near the other.
        g = REAL
        below, above = stack(g, 100, shift=130)
        t0, t1 = g['tiles'][:2]
        lift = rect_in_view(g, 1, above, (t1[0] + 300, t1[1] + t1[3], 40, 0))[2]
        top = rect_in_view(g, 0, below, (t0[0] + 300, t0[1], 40, 0))[3]
        self.assertLessEqual((lift - top) * px_per_radian(g), 2 * orbit.CLEARANCE_PX + math.radians(0.1) * px_per_radian(g) + 1.5)

    def test_the_level_bar_counts(self):
        # master never counted it: a desktop could sit right on the bar of the one below and hide it.
        g = REAL
        below, above = stack(g, 100, shift=70)
        t0, t1 = g['tiles'][:2]
        x, y, w, h = fp.level_bar_rect(t0)
        for f in (0, 0.25, 0.5, 0.75, 1):
            q = fp.unproject(g, t1, above, fp.project(g, t0, below, x + w * f, y))
            self.assertLess(fp.inside_by(t1, q), 0, f)  # the top of the bar is not on the upper desktop's picture
        self.assertFalse(apart(g, 0, below, 1, {**above, 'y': above['y'] - 1}))

    def test_a_grown_desktop_keeps_the_room_its_grown_bar_takes(self):
        # The bar is on the desktop's surface and the pill on the glass: at distance 31 the bar looks 3.2 times its size
        # and the pill keeps its own.
        g = REAL
        small, grown = stack(g, 100), stack(g, 31)
        self.assert_handles_clear(g, *grown)
        top = rect_in_view(g, 0, grown[0], fp.level_bar_rect(g['tiles'][0]))
        self.assertGreater((top[3] - top[2]) * px_per_radian(g), 2.5 * fp.LEVEL_BAR_H)
        self.assertGreater(small[1]['y'], grown[0]['y'])

    def test_coming_from_cody_s_layout_they_can_get_closer(self):
        g = REAL
        a, b, c = DESKTOPS
        for i, j, p, q in ((0, 1, a, b), (0, 2, a, c), (1, 2, b, c)): self.assertTrue(apart(g, i, p, j, q), (i, j))
        self.assertLess(least(g, 0, a, 1, b, 'x', 700, -1), 327)   # master's limit for XR-2 next to XR-1
        fa, fb = orbit.footprint(g, g['tiles'][0], a), orbit.footprint(g, g['tiles'][1], b)
        y = -500
        while bump.apart(fa, orbit.footprint(g, g['tiles'][2], {**c, 'y': y + 1})) and bump.apart(fb, orbit.footprint(g, g['tiles'][2], {**c, 'y': y + 1})): y += 1
        self.assertGreater(y, -179)                                # master's limit for XR-3 under them


class Reachable(unittest.TestCase):
    """At the closest they get every handle still answers, and nothing takes a press from a neighbour's picture."""
    ICONS = ['bar', 'bar', 'bar']

    def test_stacked_the_pill_and_all_three_bar_segments_take_a_press(self):
        for g in (G, REAL):
            for d in (100, 31):
                with self.subTest(g=g['w'], d=d):
                    below, above = stack(g, d)
                    placements = [below, above] + [place(x=900, y=600)]
                    t0, t1 = g['tiles'][:2]
                    pill = pill_on_surface(t1, above)
                    centre = fp.project(g, t1, above, pill[0] + pill[2] / 2, pill[1] + pill[3] / 2)
                    self.assertEqual(fp.hit(g, placements, centre, icons=self.ICONS), ('pill', 1))
                    x, y, w, h = fp.level_bar_rect(t0)
                    for f, segment in ((1 / 6, 'ccw'), (1 / 2, 'level'), (5 / 6, 'cw')):
                        point = fp.project(g, t0, below, x + w * f, y + h * 0.75)  # its lower part: clear of the pill's padding
                        self.assertEqual(fp.hit(g, placements, point, icons=self.ICONS), ('roll', 0, segment))

    def picture_points(self, tile, reach=26.0, step=1.5):
        """Points on a desktop's picture all along its edges, from just past the ENTER_PX sliver to `reach` in."""
        tx, ty, tw, th = tile
        for inset in (fp.ENTER_PX + 0.25, 1.5, 3.0, 6.0, 12.0, reach):
            for f in [i / 40 for i in range(41)]:
                x, y = tx + inset + (tw - 2 * inset) * f, ty + inset + (th - 2 * inset) * f
                yield (tx + inset, y)
                yield (tx + tw - inset, y)
                yield (x, ty + inset)
                yield (x, ty + th - inset)
        for corner in ((tx, ty), (tx + tw, ty), (tx, ty + th), (tx + tw, ty + th)):
            for dx in (1.0, 3.0, 6.0, 12.0, 20.0):
                for dy in (1.0, 3.0, 6.0, 12.0, 20.0):
                    yield (corner[0] + (dx if corner[0] == tx else -dx), corner[1] + (dy if corner[1] == ty else -dy))

    def assert_content(self, g, placements, i):
        tile = g['tiles'][i]
        for q in self.picture_points(tile):
            h = fp.hit(g, placements, fp.project(g, tile, placements[i], *q), icons=self.ICONS[:len(placements)])
            self.assertEqual(h[:2] if h else h, ('desktop', i), q)

    def test_a_neighbour_s_resize_zones_never_take_its_picture_side_by_side(self):
        g = REAL
        for d in (100, 31):
            with self.subTest(d=d):
                a = place(distance=d, x=-20)
                b = place(distance=d)
                b['x'] = least(g, 0, a, 1, b, 'x', 700, -1)
                placements = [a, b, place(x=900, y=600)]
                self.assert_content(g, placements, 0)
                self.assert_content(g, placements, 1)

    def test_nor_when_the_nearer_one_is_a_different_size(self):
        g = REAL
        a = place(distance=69, x=-20)
        b = place(distance=31)
        b['x'] = least(g, 0, a, 1, b, 'x', 900, -1)
        placements = [a, b, place(x=900, y=600)]
        self.assert_content(g, placements, 0)
        self.assert_content(g, placements, 1)

    def test_a_pill_s_padding_never_takes_a_picture_under_it(self):
        g = REAL
        for shift in (70, 90, 120):  # the lower bar off past the pill; its picture right under the pill's padding
            with self.subTest(shift=shift):
                below, above = stack(g, 100, shift=shift)
                self.assert_content(g, [below, above, place(x=900, y=600)], 0)

    def test_the_drawn_pill_stays_on_top_of_a_picture(self):
        # An Apply can leave a pill over another desktop's picture: glass is drawn over the pictures and keeps its press.
        placements = [place(), place(), place()]
        placements[1] = place(y=-30, x=-40)  # (XR-2's pill over XR-1's picture is nearest: moved down and left)
        t1 = G['tiles'][1]
        pill = pill_on_surface(t1, placements[1])
        point = fp.project(G, t1, placements[1], pill[0] + pill[2] / 2, pill[1] + pill[3] / 2)
        self.assertEqual(fp.hit(G, placements, point), ('pill', 1))

    def test_a_desktop_really_in_front_keeps_its_zones_over_the_one_behind(self):
        # A grown XR-2 lying over flat XR-3 (not bumped: an Apply): it is in front, so its corners, edges and pill zone stay its
        # own, unlike a desktop that is only beside another.
        g = G
        p = [place(), place(distance=61), place()]
        t = g['tiles'][1]
        self.assertTrue(fp.in_front_of(g, p, 1, 2, (t[0] + t[2] + 3, t[1] + t[3] / 2)))
        for q, want in (((t[0] + t[2] + 3, t[1] + t[3] / 2), ('edge', 1, 'r')), ((t[0] + t[2] + 3, t[1] + t[3] + 3), ('corner', 1, 'br'))):
            self.assertEqual(fp.hit(g, p, fp.project(g, t, p[1], *q))[:3], want)

    def test_with_no_neighbour_the_handles_are_as_they_were(self):
        g = dict(G, tiles=[G['tiles'][1]])
        t = g['tiles'][0]
        p = [place()]
        for q, want in (((t[0] - 3, t[1] + t[3] / 2), ('edge', 0, 'l')), ((t[0] + t[2] + 3, t[1] + t[3] / 2), ('edge', 0, 'r')),
                        ((t[0] - 3, t[1] - 3), ('corner', 0, 'tl')), ((t[0] + t[2] / 2, t[1] + t[3] + 30), ('pill', 0))):
            self.assertEqual(fp.hit(g, p, fp.project(g, t, p[0], *q)), want)

    def test_a_flat_row_at_spacing_0_gives_each_desktop_its_picture_up_to_the_seam(self):
        tw = G['w'] / 3
        th = tw * 9 / 16
        g = {**G, 'tiles': [(i * tw, (G['h'] - th) / 2, tw, th) for i in range(3)]}
        flat = [place(), place(), place()]
        self.assert_content(g, flat, 1)

    def test_the_other_three_sides_still_resize_when_a_neighbour_takes_one(self):
        g = REAL
        a = place(x=-20)
        b = place()
        b['x'] = least(g, 0, a, 1, b, 'x', 700, -1)
        placements = [a, b, place(x=900, y=600)]
        t = g['tiles'][1]
        for q, want in (((t[0] + t[2] + 3, t[1] + t[3] / 2), ('edge', 1, 'r')), ((t[0] + t[2] / 2, t[1] - 3), ('edge', 1, 't')),
                        ((t[0] + t[2] + 3, t[1] - 3), ('corner', 1, 'tr'))):
            self.assertEqual(fp.hit(g, placements, fp.project(g, t, b, *q)), want)


class Stable(unittest.TestCase):
    """Touching is a state, not a fight: nothing jitters there, and the other paths keep the same rule."""
    def test_held_against_a_neighbour_nothing_moves(self):
        placements = [place(), place(), place()]
        tile = G['tiles'][0]
        press = fp.project_glass(G, tile, placements[0], fp.pill_rect(tile)[0] + 20, fp.pill_rect(tile)[1] + 7)
        drag = orbit.PillDrag(G, 0, (0, 0), press)
        out = placements
        target = (press[0] + 120, press[1])
        for k in range(1, 41): out = drag.follow((press[0] + 120 * k / 40, press[1]), out)
        self.assertNotEqual(out[1]['x'], 0)
        for _ in range(60):
            again = drag.follow(target, out)
            self.assertEqual(again, out)
            out = again

    def test_shaking_against_a_neighbour_only_ever_pushes_it_one_way(self):
        placements = [place(), place(), place()]
        tile = G['tiles'][0]
        press = fp.project_glass(G, tile, placements[0], fp.pill_rect(tile)[0] + 20, fp.pill_rect(tile)[1] + 7)
        drag = orbit.PillDrag(G, 0, (0, 0), press)
        out, seen = placements, []
        for k in range(1, 201):
            shake = 3.0 * (1 if k % 2 else -1)
            out = drag.follow((press[0] + 0.6 * k + shake, press[1]), out)
            seen.append((out[1]['x'], out[2]['x'], out[1]['y']))
        self.assertEqual({s[2] for s in seen}, {0})                       # never up or down
        self.assertEqual([s[0] for s in seen], sorted(s[0] for s in seen))  # never back
        self.assertEqual([s[1] for s in seen], sorted(s[1] for s in seen))

    def test_a_stack_pressed_together_stays_put_under_a_still_pointer(self):
        g = REAL
        below, above = stack(g, 100)
        placements = [below, above, place(x=900, y=600)]
        tile = g['tiles'][1]
        press = fp.project_glass(g, tile, above, fp.pill_rect(tile)[0] + 20, fp.pill_rect(tile)[1] + 7)
        drag = orbit.PillDrag(g, 1, (above['x'], above['y']), press)
        out = placements
        target = (press[0], press[1] + 40)
        for k in range(1, 21): out = drag.follow((press[0], press[1] + 40 * k / 20), out)
        self.assertLess(out[0]['y'], 0)  # the pill bumped the bar: the desktop below gave way, straight down
        self.assertEqual(out[0]['x'], below['x'])
        for _ in range(40):
            again = drag.follow(target, out)
            self.assertEqual(again, out)
            out = again

    def test_touching_is_never_settled_apart(self):
        for g in (G, REAL):
            a = place(x=-20)
            b = place()
            b['x'] = least(g, 0, a, 1, b, 'x', 900, -1)
            placements = [a, b, place(x=-200, y=400)]
            out, moved = bump.settle(g, placements)
            self.assertEqual(moved, ())
            self.assertEqual(out, placements)

    def test_a_stack_at_the_limit_is_never_settled_apart(self):
        for g in (G, REAL):
            below, above = stack(g, 100)
            placements = [below, above, place(x=900, y=600)]
            self.assertEqual(bump.settle(g, placements), (placements, ()))

    def test_the_real_layouts_are_left_as_they_are(self):
        saved = [place(x=-87, y=122, distance=32), place(x=261, y=110, distance=34), place(x=-149, y=-119, distance=69)]  # profiles.json
        self.assertEqual(bump.settle(REAL, DESKTOPS), (DESKTOPS, ()))
        self.assertEqual(bump.settle(REAL, saved), (saved, ()))

    def test_a_layout_bumped_under_the_old_rule_stays_as_it_was(self):
        # What master left touching is clear now: an upgrade moves nothing that was side by side, at any size.
        for d in (100, 31):
            a = place(distance=d, x=-20)
            b = place(distance=d)
            tight = least(REAL, 0, a, 1, b, 'x', 900, -1)
            old = tight + math.ceil(6 / (d / 100) / (math.radians(0.1) * px_per_radian(REAL)))
            placements = [a, {**b, 'x': old}, place(x=900, y=600)]
            self.assertEqual(bump.settle(REAL, placements), (placements, ()), d)

    def test_a_stack_saved_with_its_pill_over_a_bar_is_parted_once_and_then_left_alone(self):
        g = REAL
        below, above = stack(g, 100)
        placements = [below, {**above, 'y': above['y'] - 4}, place(x=900, y=600)]  # 16 px closer: where master let it sit
        out, moved = bump.settle(g, placements, first=(0,))
        self.assertEqual(moved, (1,))
        self.assertTrue(apart(g, 0, out[0], 1, out[1]))
        self.assertEqual(bump.settle(g, out), (out, ()))

    def spaced(self, spacing, g=REAL, n=3):
        tw = (g['w'] - (n + 1) * spacing) / n
        th = tw * 9 / 16
        return {**g, 'tiles': [(spacing + i * (tw + spacing), (g['h'] - th) / 2, tw, th) for i in range(n)]}

    def test_the_default_spacing_and_every_step_of_it_keep_the_row_clear(self):
        # The row itself never needs pushing apart at any step of Ctrl+Alt+[ / ]; at 0 it touches and is exempt, as ever.
        for n in (2, 3, 4):
            flat = [place() for _ in range(n)]
            for spacing in range(view.MAX_SPACING, -1, -view.SPACING_STEP):
                with self.subTest(n=n, spacing=spacing):
                    g = self.spaced(spacing, n=n)
                    self.assertEqual(bump.settle(g, flat), (flat, ()))
                    if spacing:
                        for i in range(n - 1): self.assertTrue(apart(g, i, flat[i], i + 1, flat[i + 1]), (spacing, i))

    def test_a_spacing_change_and_back_pushes_once_at_most(self):
        # Desktops bumped together at one spacing, the row closed up by Ctrl+Alt+[ and opened again by ]: settling pushes
        # what really overlaps at the tighter step, by the same rule, and the way back pushes nothing.
        wide = self.spaced(40)
        tight = self.spaced(20)
        a = place(x=30)
        b = place(x=0)
        b['x'] = least(wide, 0, a, 1, b, 'x', 400, -1)
        placements = [a, b, place()]
        settled, moved = bump.settle(tight, placements)
        self.assertTrue(apart(tight, 0, settled[0], 1, settled[1]))
        self.assertEqual(bump.settle(tight, settled), (settled, ()))
        self.assertEqual(bump.settle(wide, settled), (settled, ()))

    def test_sinking_onto_a_lower_bar_presses_it_down_steadily(self):
        # XR-1 lifted clear above XR-2's top edge and sinking as it goes right: its corner, then its pill, press XR-2's bar
        # down. Only ever down and away, never a fling, nothing in the way of the pointer.
        tile = G['tiles'][0]
        x, y, w, h = fp.pill_rect(tile)
        press = (x + w / 2, y + h / 2)
        drag = orbit.PillDrag(G, 0, (0, 0), press)
        ps = [place(), place(), place()]
        for k in range(1, 40): ps = drag.follow((press[0], press[1] - 9 * k), ps)
        xs, ys = [], []
        for k in range(1, 80):
            ps = drag.follow((press[0] + 8 * k, press[1] - 9 * 39 + 2 * k), ps)
            xs.append(ps[1]['x']); ys.append(ps[1]['y'])
            for i, j in ((0, 1), (0, 2), (1, 2)): self.assertTrue(apart(G, i, ps[i], j, ps[j]), (k, i, j))
        self.assertLess(ys[-1], 0)
        self.assertEqual(ys, sorted(ys, reverse=True))
        self.assertEqual(xs, sorted(xs))
        self.assertLessEqual(max(a - b for a, b in zip(ys, ys[1:])), 12)  # the step where the pill reaches the bar too
        self.assertLessEqual(max(b - a for a, b in zip(xs, xs[1:])), 12)

    def test_sliding_past_a_lower_bar_and_back_moves_nothing_back(self):
        # A pushed desktop stays pushed; going back and forth over the same spot pushes nothing twice.
        g = REAL
        below, above = stack(g, 100)
        ps = [below, {**above, 'x': above['x'] + 90, 'y': above['y'] - 5}, place(x=900, y=600)]  # low enough to hit it on the way
        self.assertTrue(apart(g, 0, ps[0], 1, ps[1]))
        pusher = bump.Pusher(g, 1)
        out = pusher.move(ps, {'x': above['x']})
        back = pusher.move(out, {'x': ps[1]['x']})
        self.assertEqual(back[0], out[0])
        forth = pusher.move(back, {'x': above['x']})
        self.assertEqual(forth[0], out[0])

    def test_a_corner_beside_a_handle_slides_off_it(self):
        # Found by fuzzing square strips: XR-4's corner sat in the notch between grown XR-2's top edge and the end of its bar,
        # so lifting XR-2 could only have pushed XR-4 the bar's whole height at once, past what a push may do, and it stopped.
        g = {**REAL, 'tiles': [(i * 480.0, 465.0, 480.0, 270.0) for i in range(4)]}
        ps = [place(x=447, y=75), place(x=88, y=139, distance=31), place(x=302, y=-68, distance=39), place(x=68, y=336, distance=39)]
        ps[3]['y'] = least(g, 1, ps[1], 3, ps[3], 'y', 500, -1)  # right down against it
        pusher = bump.Pusher(g, 1)
        out = pusher.move(ps, {'x': 88, 'y': 190}, start={'x': 88, 'y': 139})
        self.assertFalse(pusher.blocked)
        self.assertEqual(out[1]['y'], 190)
        for i in range(4):
            for j in range(i + 1, 4): self.assertTrue(apart(g, i, out[i], j, out[j]), (i, j))

    def test_crowded_desktops_of_every_size_never_overlap_stick_or_jitter(self):
        # Packed together, any size, some curved; each drag driven straight into a neighbour, then held, then shaken on the
        # spot while still pressing into it. Deterministic. (A longer run of this is what chose the strips' shape.)
        canvases = [REAL, {**REAL, 'tiles': [(i * 480.0, 465.0, 480.0, 270.0) for i in range(4)]},
                    {**REAL, 'tiles': [(i * 960.0, 300.0, 960.0, 600.0) for i in range(2)]}]
        rng = random.Random(20261103)

        def clear(g, ps):
            f = [orbit.footprint(g, g['tiles'][i], q) for i, q in enumerate(ps)]
            return all(bump.apart(f[i], f[j]) for i in range(len(ps)) for j in range(i + 1, len(ps)))
        updates = blocked = 0
        for run in range(150):
            g = rng.choice(canvases)
            n = len(g['tiles'])
            ps, _ = bump.settle(g, [place(x=rng.randrange(-260, 260), y=rng.randrange(-160, 160), distance=rng.choice((100, 100, 69, 69, 50, 39, 31, 31)),
                                          curvature=rng.choice((0, 0, 0, 0, 30, 60))) for _ in range(n)])
            self.assertTrue(clear(g, ps), run)
            i = rng.randrange(n)
            tile, other = g['tiles'][i], rng.choice([k for k in range(n) if k != i])
            x, y, w, h = fp.pill_rect(tile)
            press = fp.project_glass(g, tile, ps[i], x + w / 2, y + h / 2)
            t2 = g['tiles'][other]
            aim = fp.project(g, t2, ps[other], t2[0] + t2[2] / 2, t2[1] + t2[3] / 2)
            drag = orbit.PillDrag(g, i, (ps[i]['x'], ps[i]['y']), press)
            out = ps
            for k in range(1, 41):
                pointer = (press[0] + (aim[0] - press[0]) * k / 40, press[1] + (aim[1] - press[1]) * k / 40)
                out = drag.follow(pointer, out)
                updates += 1
                blocked += drag.pusher.blocked
                self.assertTrue(clear(g, out), (run, k))
            self.assertEqual(drag.follow(pointer, out), out, run)                 # a still pointer moves nothing
            path = {k: [] for k in range(n) if k != i}
            for _ in range(20):
                out = drag.follow((pointer[0] + rng.uniform(-2.5, 2.5), pointer[1] + rng.uniform(-2.5, 2.5)), out)
                self.assertTrue(clear(g, out), run)
                for k in path: path[k].append((out[k]['x'], out[k]['y']))
            for k, seen in path.items():
                steps = [(b[0] - a[0], b[1] - a[1]) for a, b in zip(seen, seen[1:]) if a != b]
                self.assertFalse([1 for a, b in zip(steps, steps[1:]) if a[0] * b[0] < 0 or a[1] * b[1] < 0], (run, k))  # never back and forth
                self.assertLessEqual(sum(abs(orbit._diff(b[0], a[0])) + abs(b[1] - a[1]) for a, b in zip(seen, seen[1:])), 20, (run, k))
            self.assertEqual(bump.settle(g, out)[1], (), run)                     # and settling agrees with where it ended
        self.assertLess(blocked / updates, 0.01)  # (square strips: nearly 4%, master: 0.3%)

    def test_the_row_at_spacing_0_still_moves_freely(self):
        g = self.spaced(0)
        flat = [place(), place(), place()]
        pusher = bump.Pusher(g, 1)
        out = pusher.move(flat, {'y': 300})
        self.assertEqual(out[1]['y'], 300)
        self.assertEqual((out[0], out[2]), (flat[0], flat[2]))


class Levelled(unittest.TestCase):
    """What collides is the desktop as drawn. Levelling takes the lean out of a raised desktop off to the side, so ignoring
    the roll (master) had level neighbours bump with 100 px between them, all the way down the seam."""
    SAVED = [place(x=-87, y=122, distance=32), place(x=261, y=110, distance=34), place(x=-149, y=-119, distance=69)]  # profiles.json, 2026-11-03
    ROLLS = [436, -406, -84]

    def rolled(self, p, i):
        return {**p, 'roll': self.ROLLS[i]}

    def seam(self, a, b):
        """Canvas px between desktop 0's right edge and desktop 1's left edge as drawn, a few points down it."""
        t0, t1 = REAL['tiles'][:2]
        left = [direction(REAL, 1, b, t1[0], t1[1] + t1[3] * k / 200) for k in range(201)]
        return [min(orbit._angle(direction(REAL, 0, a, t0[0] + t0[2], t0[1] + t0[3] * f), d) for d in left) * px_per_radian(REAL)
                for f in (0.25, 0.5, 0.75)]

    def limit(self, rolls):
        pusher = bump.Pusher(REAL, 1, rolls)
        fa = pusher._print(0, (self.SAVED[0]['x'], self.SAVED[0]['y'], self.SAVED[0]['distance'], 0))
        x = 900
        while bump.apart(fa, pusher._print(1, (x - 1, self.SAVED[1]['y'], self.SAVED[1]['distance'], 0))): x -= 1
        return x

    def test_levelled_neighbours_come_right_up_to_each_other_along_the_whole_seam(self):
        x = self.limit(self.ROLLS)
        self.assertLess(x, 237)  # 244 on master, 237 ignoring the roll with the closer gap
        seam = self.seam(self.rolled(self.SAVED[0], 0), {**self.rolled(self.SAVED[1], 1), 'x': x})
        self.assertLess(max(seam), 35)  # 105..122 px on master
        self.assertLess(max(seam) - min(seam), 20)  # upright: nearly even, where unrolled it would open by a hundred

    def test_a_footprint_is_the_desktop_as_drawn(self):
        for roll in (436, -406, 1700, -9000):
            for tile, p in ((REAL['tiles'][0], place(x=-87, y=122, distance=32, roll=roll)), (G['tiles'][1], place(x=300, y=-200, curvature=60, roll=roll))):
                g = REAL if tile == REAL['tiles'][0] else G
                f = orbit.footprint(g, tile, p)
                for x, y in ((tile[0], tile[1]), (tile[0] + tile[2], tile[1]), (tile[0], tile[1] + tile[3]), (tile[0] + tile[2], tile[1] + tile[3])):
                    d = orbit._norm(orbit.place_point(g, tile, p, x, y))
                    self.assertTrue(all(orbit._dot(n, d) >= -1e-9 for n in f.normals), (roll, x, y))

    def test_a_pusher_uses_the_rolls_it_is_given(self):
        a, b = self.rolled(self.SAVED[0], 0), self.rolled(self.SAVED[1], 1)
        pusher = bump.Pusher(REAL, 1, self.ROLLS)
        unrolled = [dict(p) for p in self.SAVED]
        out = pusher.move(unrolled, {'x': 230})
        self.assertEqual((out[1]['x'], pusher.pushed), (230, ()))             # as drawn, there is still room
        plain = bump.Pusher(REAL, 1)
        out = plain.move(unrolled, {'x': 230})
        self.assertIn(0, plain.pushed)                                        # ignoring the roll it would have bumped
        self.assertTrue(apart(REAL, 0, a, 1, {**b, 'x': 230}))

    def test_settling_a_saved_levelled_layout_moves_nothing(self):
        self.assertEqual(bump.settle(REAL, self.SAVED, rolls=self.ROLLS), (self.SAVED, ()))
        x = self.limit(self.ROLLS)
        tight = [self.SAVED[0], {**self.SAVED[1], 'x': x}, self.SAVED[2]]
        self.assertEqual(bump.settle(REAL, tight, rolls=self.ROLLS), (tight, ()))  # a start keeps them as close as a drag left them

    def test_a_turned_desktop_that_swung_over_a_neighbour_may_only_get_clearer(self):
        # A roll is not a move: nothing pushed it. The next move of the two starts from there and may not go deeper.
        a = place(x=-20)
        b = place()
        b['x'] = least(REAL, 0, a, 1, b, 'x', 700, -1)
        rolls = [1200, 0, 0]
        self.assertFalse(bump.apart(orbit.footprint(REAL, REAL['tiles'][0], {**a, 'roll': 1200}), orbit.footprint(REAL, REAL['tiles'][1], b)))
        pusher = bump.Pusher(REAL, 0, rolls)
        out = pusher.move([a, b, place(x=900, y=600)], {'x': a['x'] - 40})
        self.assertEqual((out[0]['x'], out[1], pusher.pushed), (a['x'] - 40, b, ()))

    def test_each_way_of_moving_a_desktop_counts_the_rolls(self):
        seen = {}
        real, settle = bump.Pusher.__init__, bump.settle

        def spy(pusher, g, index, rolls=None):
            seen['rolls'] = rolls
            real(pusher, g, index, rolls)
        with unittest.mock.patch.object(bump.Pusher, '__init__', spy):
            drag = orbit.PillDrag(G, 0, (0, 0), (100.0, 800.0), self.ROLLS)
            drag.follow((110.0, 800.0), [place(), place(), place()])
            self.assertEqual(seen.pop('rolls'), self.ROLLS)
            bump.push(G, 0, [place(), place(), place()], {'x': 5}, self.ROLLS)
            self.assertEqual(seen.pop('rolls'), self.ROLLS)
            bump.settle(G, [place(), place(), place()], rolls=self.ROLLS)
            self.assertEqual(seen.pop('rolls'), self.ROLLS)
        free_pointer = (ROOT / 'tools/native_spike/free_pointer.py').read_text()
        self.assertIn("orbit.PillDrag(self.geometry, d['index'], d['xy'], d['start'], self.rolls)", free_pointer)
        self.assertIn("bump.Pusher(self.geometry, d['index'], self.rolls)", free_pointer)
        view_py = (ROOT / 'tools/native_spike/view.py').read_text()
        self.assertIn('bump.Pusher(g, i, self._rolls())', view_py)
        self.assertRegex(view_py, r'bump\.settle\(g, s\.placements, .*self\._rolls\(\)\)')


class Strips(unittest.TestCase):
    """The strips follow the handles themselves: the supervisor's own pill_rect, glass_to_desktop and level_bar_rect."""
    PLACES = (place(), place(distance=50), place(distance=31), place(distance=200), place(curvature=60), place(curvature=120, distance=50),
              place(x=400, y=300), place(x=-900, y=-500, distance=69))

    def inside(self, piece, d):
        return all(orbit._dot(n, d) >= -1e-9 for n in piece.normals)

    def test_a_desktop_is_its_frame_and_two_strips(self):
        f = orbit.footprint(G, G['tiles'][1], place())
        self.assertEqual(len(f.handles), 2)
        self.assertTrue(all(len(h.vertices) == 4 for h in f.handles))

    def test_every_handle_as_drawn_is_inside_what_the_desktop_keeps(self):
        for g in (G, REAL):
            for i, tile in enumerate(g['tiles']):
                for p in self.PLACES:
                    with self.subTest(g=g['w'], i=i, p=p):
                        f = orbit.footprint(g, tile, p)
                        pieces = (f,) + f.handles
                        px, py, pw, ph = pill_on_surface(tile, p)
                        bx, by, bw, bh = fp.level_bar_rect(tile)
                        for x, y in ((px, py + ph), (px + pw, py + ph), (px + pw / 2, py + ph), (bx, by), (bx + bw, by), (bx + bw / 2, by)):
                            d = direction(g, i, p, x, y)
                            self.assertTrue(any(self.inside(q, d) for q in pieces), (x, y))

    def test_a_strip_slopes_out_to_the_edge_of_the_desktop(self):
        # 45 degrees on the surface: the near side wider than the handle by the strip's depth, each way.
        tile = G['tiles'][1]
        near_edge = tile[1] - orbit.CLEARANCE_PX
        x, y, w, h = fp.level_bar_rect(tile)
        recorded = []
        with unittest.mock.patch.object(orbit, 'place_point', lambda g, t, p, u, v: recorded.append((u, v)) or (u, v, -1.0)):
            orbit._strips(G, tile, place())
        depth = near_edge - y
        self.assertEqual(recorded[:4], [(x - depth, near_edge), (x + w + depth, near_edge), (x + w, y), (x, y)])
        tx, ty, tw, th = narrow = (900.0, 500.0, 150.0, 84.0)  # hardly wider than its bar: within the desktop's own width
        recorded.clear()
        with unittest.mock.patch.object(orbit, 'place_point', lambda g, t, p, u, v: recorded.append((u, v)) or (u, v, -1.0)):
            orbit._strips(G, narrow, place())
        for u, _ in recorded: self.assertTrue(tx - orbit.CLEARANCE_PX <= u <= tx + tw + orbit.CLEARANCE_PX, u)

    def test_the_corners_of_a_strip_are_the_handle_s_own(self):
        tile = G['tiles'][1]
        p = place()
        f = orbit.footprint(G, tile, p)
        bar, pill = f.handles
        bx, by, bw, bh = fp.level_bar_rect(tile)
        tops = [direction(G, 1, p, x, by) for x in (bx, bx + bw)]
        for d in tops: self.assertLess(min(orbit._angle(d, v) for v in bar.vertices), 1e-6)
        px, py, pw, ph = fp.pill_rect(tile)
        bottoms = [direction(G, 1, p, x, py + ph) for x in (px, px + pw)]
        for d in bottoms: self.assertLess(min(orbit._angle(d, v) for v in pill.vertices), 1e-6)

    def test_the_cone_holds_every_strip(self):
        for p in self.PLACES:
            f = orbit.footprint(G, G['tiles'][1], p)
            for h in f.handles:
                for v in h.vertices: self.assertLessEqual(orbit._angle(f.centre, v), f.radius + 1e-9)

    def test_a_handle_inside_the_margin_takes_no_strip(self):
        # A desktop this narrow scales its bar down to well under the margin, and the pill stays on the glass.
        tile = (900.0, 500.0, 3.0, 2.0)
        self.assertEqual(len(orbit._strips(G, tile, place())), 1)
        self.assertEqual(len(orbit.footprint(G, tile, place()).handles), 1)

    def test_turned_footprints_carry_their_strips(self):
        tile = G['tiles'][1]
        p = place(x=300, y=200)
        f = orbit.footprint(G, tile, p)
        flat = orbit.footprint(G, tile, place())
        m = orbit.rotation(orbit.slot_azimuth(G, tile), 300, 200)
        for got, base in zip(f.handles, flat.handles):
            for v, w in zip(got.vertices, base.vertices):
                self.assertAlmostEqual(orbit._angle(v, orbit.apply(m, w)), 0, places=9)

    def test_overlap_adds_what_each_piece_shares(self):
        # A pill over another desktop's picture is an overlap, and a deeper one is a bigger one (the "not deeper" rule).
        g = REAL
        below, above = stack(g, 100)
        fa = orbit.footprint(g, g['tiles'][0], below)
        last = 0
        self.assertEqual(orbit.overlap(fa, orbit.footprint(g, g['tiles'][1], above)), 0)
        for down in (2, 4, 6, 8, 12, 20):
            v = orbit.overlap(fa, orbit.footprint(g, g['tiles'][1], {**above, 'y': above['y'] - down}))
            self.assertGreater(v, last)
            last = v


if __name__ == '__main__':
    unittest.main()
