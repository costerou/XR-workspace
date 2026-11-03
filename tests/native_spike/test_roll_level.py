"""Level bar, supervisor side (free_pointer.py): the renderer's `roll_geometry` line, the bar on every desktop in the
overview (level, tilted or unmeasured), which turn segment levels it, its geometry (a segmented capsule above the top edge,
in tile px), hit testing as it moves, scales and rolls with the desktop (resize, move, roll), the zone contract round it,
a click on a turn segment (to level when it lies that way, else a step), a drag that rolls either way, the middle segment
levelling and moving nothing else, the `roll` and `resize_handle` frames lighting only what the dot is on, and the bars
hiding with the pills.

Offline only: a fake clock, the renderer's overview geometry fixture. No compositor, renderer binary or glasses.
"""
import json
import math
from pathlib import Path
import unittest

from tools.native_spike import free_pointer as fp, supervisor
from tools.native_spike.geometry import DEFAULT_PLACEMENT
from test_glass_pills import Base, G, T0, T1, frames, last, pill_centre

FLAT = dict(DEFAULT_PLACEMENT)
ROOT = Path(__file__).resolve().parents[2]


def on_bar(tile, segment):
    """The tile point in the middle of a segment of the desktop's level bar."""
    x, y, w, h = fp.level_bar_rect(tile)
    return (x + w * (fp.LEVEL_SEGMENTS.index(segment) + 0.5) / 3, y + h / 2)


def grip(tile, segment, placement=FLAT, roll=0):
    """Where the dot grabs a segment of the desktop's bar (canvas px), on the desktop as placed (with `roll`)."""
    return fp.project(G, tile, {**placement, 'roll': roll}, *on_bar(tile, segment))


class PureTests(unittest.TestCase):
    def test_the_segment_that_levels_is_the_one_on_the_higher_side(self):
        self.assertEqual(fp.roll_side(300, 0), 'cw')     # turned counter-clockwise: top-right is higher, turn it clockwise
        self.assertEqual(fp.roll_side(-300, 0), 'ccw')
        self.assertIsNone(fp.roll_side(300, -300))       # levelled
        self.assertIsNone(fp.roll_side(fp.ROLL_LEVEL, 0))  # within half a degree counts as level
        self.assertEqual(fp.roll_side(fp.ROLL_LEVEL + 1, 0), 'cw')
        self.assertIsNone(fp.roll_side(None, 0))         # not measured (no tracking, not anchor mode)
        self.assertEqual(fp.roll_side(100, -400), 'ccw')  # rolled past level the other way

    def test_a_click_turns_the_segments_way_to_level_or_a_step(self):
        # The turn segment that points toward level levels it at once; the other one (and both on a level or unmeasured
        # desktop) turns it ROLL_STEP its own way: counter-clockwise positive.
        self.assertEqual(fp.roll_click(300, 0, 'cw'), -300)
        self.assertEqual(fp.roll_click(300, 0, 'ccw'), fp.ROLL_STEP)
        self.assertEqual(fp.roll_click(-300, 0, 'ccw'), 300)
        self.assertEqual(fp.roll_click(-300, 0, 'cw'), -fp.ROLL_STEP)
        for tilt in (0, None, 40):
            self.assertEqual(fp.roll_click(tilt, 0, 'ccw'), fp.ROLL_STEP)
            self.assertEqual(fp.roll_click(tilt, 0, 'cw'), -fp.ROLL_STEP)
        self.assertEqual(fp.roll_click(0, fp.ROLL_STEP, 'cw'), 0)  # one step back the other way: level again
        self.assertEqual(fp.roll_click(None, fp.MAX_ROLL - 50, 'ccw'), fp.MAX_ROLL)  # bounded
        self.assertEqual(fp.roll_click(None, -fp.MAX_ROLL, 'cw'), -fp.MAX_ROLL)
        self.assertEqual(fp.roll_click(15000, 0, 'cw'), -fp.MAX_ROLL)
        with self.assertRaises(ValueError): fp.roll_click(0, 0, 'level')  # the middle segment levels, it does not step

    def test_a_drag_turns_either_way_with_a_detent_at_level(self):
        self.assertEqual(fp.roll_drag(None, 0, 1234.4), 1234)
        self.assertEqual(fp.roll_drag(None, 0, -1234.4), -1234)
        self.assertEqual(fp.roll_drag(300, 0, -500), -500)          # past level: free
        self.assertEqual(fp.roll_drag(300, 0, 500), 500)            # away from level: free too
        self.assertEqual(fp.roll_drag(300, 0, -280), -300)          # within ROLL_LEVEL of level: rests there
        self.assertEqual(fp.roll_drag(0, 0, 30), 0)                 # a level desktop needs more than ROLL_LEVEL to leave
        self.assertEqual(fp.roll_drag(0, 0, 60), 60)
        self.assertEqual(fp.roll_drag(None, 100, 20000), fp.MAX_ROLL)
        self.assertEqual(fp.roll_drag(None, -100, -20000), -fp.MAX_ROLL)

    def test_the_bar_sits_above_the_top_edge_in_three_segments(self):
        # A share of the desktop's width (clamped), the whole design scaling with it, centred above the top edge; hit on what
        # is drawn padded by LEVEL_BAR_PAD, split at its thirds, the padding going with the segment it borders.
        self.assertEqual(fp.level_bar_scale((0, 0, 4000, 2000)), 1.0)
        self.assertEqual(fp.level_bar_scale((0, 0, 592, 333)), 1.0)
        self.assertAlmostEqual(fp.level_bar_scale(T0), fp.LEVEL_BAR_SHARE * T0[2] / fp.LEVEL_BAR_W)
        self.assertAlmostEqual(fp.level_bar_scale((0, 0, 440, 247)), fp.LEVEL_BAR_MIN_W / fp.LEVEL_BAR_W)
        self.assertAlmostEqual(fp.level_bar_scale((0, 0, 60, 34)), 60 / fp.LEVEL_BAR_W)  # never wider than the desktop
        self.assertEqual(fp.level_bar_scale((0, 0, 0, 10)), 0.0)
        self.assertEqual(fp.level_bar_rect((0, 0, 0, 10)), (0.0, 0.0, 0.0, 0.0))
        self.assertIsNone(fp.level_bar_segment((0, 0, 0, 10), (0, 0)))
        self.assertIsNone(fp.level_bar_segment(T0, None))
        tx, ty, tw, _ = T0
        x, y, w, h = fp.level_bar_rect(T0)
        s = fp.level_bar_scale(T0)
        self.assertAlmostEqual(x + w / 2, tx + tw / 2)
        self.assertAlmostEqual(y + h, ty - fp.LEVEL_BAR_GAP * s)
        self.assertAlmostEqual(h / w, fp.LEVEL_BAR_H / fp.LEVEL_BAR_W)
        pad, mid = fp.LEVEL_BAR_PAD * s, y + h / 2
        for at, want in ((x - pad + 0.1, 'ccw'), (x + w / 6, 'ccw'), (x + w / 3 - 0.1, 'ccw'), (x + w / 3 + 0.1, 'level'), (x + w / 2, 'level'),
                         (x + 2 * w / 3 - 0.1, 'level'), (x + 2 * w / 3 + 0.1, 'cw'), (x + w + pad - 0.1, 'cw')):
            self.assertEqual(fp.level_bar_segment(T0, (at, mid)), want, at)
        for q in ((x - pad - 0.1, mid), (x + w + pad + 0.1, mid), (x + w / 2, y - pad - 0.1), (x + w / 2, y + h + pad + 0.1),
                  (tx + 30, ty - fp.LEVEL_BAR_GAP), (tx + tw - 30, ty - fp.LEVEL_BAR_GAP)):  # nothing out by the corners
            self.assertIsNone(fp.level_bar_segment(T0, q), q)
        self.assertLessEqual(y + h + pad, ty - fp.LEVEL_EDGE_CLEAR * s)  # the band along the edge stays the edge's

    def test_the_bar_moves_scales_and_rolls_with_its_desktop(self):
        # Through a resize (distance), a move (the orbit) and a roll the bar stays on the desktop: where it is drawn (the
        # same tile px on the desktop as placed) is what is hit, and where it was before the change is not that segment any
        # more. On the surface it keeps its share of the desktop's width.
        placements = [dict(FLAT) for _ in range(3)]
        def span(p, a, b):  # the distance in space between two of XR-2's tile points
            return math.dist(fp.place_point(G, T1, p, *a), fp.place_point(G, T1, p, *b))
        tx, ty, tw, _ = T1
        x, y, w, _ = fp.level_bar_rect(T1)
        # (placement, whether it carries the bar well clear of where it was: a 40 degree curve barely moves it). Each keeps
        # XR-2 clear of its neighbours: where a bar lies on another desktop's content, that content takes the press
        # (test_the_bar_never_takes_a_press_from_content).
        for p, moved in (({**FLAT, 'distance': 180}, True), (FLAT, False), ({**FLAT, 'y': 200, 'distance': 60}, True),
                         ({**FLAT, 'y': -180}, True), ({**FLAT, 'x': 30, 'y': 150, 'distance': 140}, True),
                         ({**FLAT, 'curvature': 40}, False), ({**FLAT, 'roll': 1500}, True),
                         ({**FLAT, 'y': 160, 'distance': 70, 'roll': -2500}, True)):
            placed = [placements[0], p, placements[2]]
            for segment in fp.LEVEL_SEGMENTS:
                point = grip(T1, segment, p, p.get('roll', 0))
                self.assertEqual(fp.hit(G, placed, point, icons=['bar'] * 3), ('roll', 1, segment), (p, segment))
                if moved:  # the same spot on the desktop as it was before (flat, at its slot) is that segment's no longer
                    self.assertNotEqual(fp.hit(G, placed, grip(T1, segment), icons=['bar'] * 3), ('roll', 1, segment), (p, segment))
            got = span(p, (x, y), (x + w, y)) / span(p, (tx, ty), (tx + tw, ty))
            self.assertAlmostEqual(got, w / tw, delta=0.03 * w / tw, msg=p)

    def test_roll_geometry_line(self):
        self.assertEqual(fp.parse_roll_geometry({'tilt': [120, None, -18000]}), [120, None, -18000])
        self.assertEqual(fp.parse_roll_geometry({'tilt': [1, 2, 3, 4]}), [1, 2, 3, 4])  # up to MAX_DESKTOPS
        for bad in ({'tilt': []}, {'tilt': [1, 2, 3, 4, 5]}, {'tilt': [1.5]}, {'tilt': [18001]}, {'tilt': [True]}, {'tilt': 1},
                    {'tilt': [1], 'x': 1}, None, []):
            with self.assertRaises(ValueError): fp.parse_roll_geometry(bad)

    def test_roll_frame(self):
        state = {'roll': [-300, 0, 9000, 0], 'icons': ['bar', 'none', 'bar', 'bar'], 'hover': 0, 'side': 'cw', 'active': True}
        f = json.loads(fp.roll_frame('run', 's', 9, state))
        self.assertEqual((f['op'], f['request_id'], f['payload']), ('roll', 9, state))
        json.loads(fp.roll_frame('run', 's', 9, fp.ROLL_HIDDEN))
        self.assertEqual(json.loads(fp.roll_frame('run', 's', 9, {**state, 'side': 'level'}))['payload']['side'], 'level')
        for bad in ({**state, 'roll': [0, 0, 0]}, {**state, 'roll': [0, 0, 0, 9001]}, {**state, 'icons': ['up', 'none', 'none', 'none']},
                    {**state, 'icons': ['both', 'none', 'none', 'none']}, {**state, 'icons': ['cw', 'none', 'none', 'none']},
                    {**state, 'hover': 4}, {**state, 'active': 1}, {**state, 'x': 1}, {**state, 'side': 'up'},
                    {**state, 'side': 'none'}, {**state, 'hover': -1}, {k: v for k, v in state.items() if k != 'side'}):
            with self.assertRaises(ValueError): fp.roll_frame('run', 's', 9, bad)

    def test_a_rolled_desktop_is_hit_where_it_is_drawn(self):
        p = {**FLAT, 'roll': 2000}
        for q in ((T1[0] + 30, T1[1] + 40), (T1[0] + T1[2] - 5, T1[1] + T1[3] / 2)):
            seen = fp.project(G, T1, p, *q)
            back = fp.unproject(G, T1, p, seen)
            self.assertAlmostEqual(back[0], q[0], delta=0.05); self.assertAlmostEqual(back[1], q[1], delta=0.05)
            self.assertNotAlmostEqual(fp.project(G, T1, FLAT, *q)[1], seen[1], delta=1)  # the roll moved it

    def test_the_bar_never_takes_a_press_from_content(self):
        # Cody (2026-11-03, anchor-no-click-maximize): presses on a desktop's content go to its apps. Showing the bars only
        # ever changes what a press does where it would otherwise hit nothing or an edge's border past the band next to the
        # desktop: never on any desktop's content, a resize corner or a pill, whoever's bar is drawn there. Dense grids over
        # the fixture's row, the row with one desktop resized over its neighbours, rolled desktops, four desktops with no
        # space between them, and a narrow desktop whose bar reaches its corners.
        w = 1920 / 4
        tight = {'w': 1920, 'h': 1200, 'half': (0.41421356, 0.25888), 'tiles': [(i * w, 450.0, w, w * 9 / 16) for i in range(4)]}
        narrow = {**G, 'tiles': [(900.0, 500.0, 120.0, 68.0)]}
        layouts = ((G, [FLAT] * 3), (G, [FLAT, {**FLAT, 'distance': 55}, FLAT]), (G, [{**FLAT, 'roll': 2000}, FLAT, {**FLAT, 'roll': -1200}]),
                   (tight, [FLAT] * 4), (tight, [{**FLAT, 'roll': 1500}] * 4), (narrow, [FLAT]))
        for g, placements in layouts:
            icons, bars = ['bar'] * len(placements), 0
            for i, tile in enumerate(g['tiles']):  # a 3 px grid over each bar and past it, on its desktop as placed
                x, y, bw, bh = fp.level_bar_rect(tile)
                for u in range(-12, int(bw) + 12, 3):
                    for v in range(-12, int(bh) + 24, 3):
                        point = fp.project(g, tile, placements[i], x + u, y + v)
                        bare, shown = fp.hit(g, placements, point), fp.hit(g, placements, point, icons=icons)
                        if bare is not None and bare[0] != 'edge': self.assertEqual(shown, bare, (i, u, v, placements))
                        elif shown != bare:
                            self.assertEqual(shown[0], 'roll', (i, u, v))
                            bars += 1
            self.assertGreater(bars, 50, placements)  # and the bars are there to press

    def test_in_a_tight_overview_every_bar_can_still_be_pressed(self):
        # Four desktops with no space between them, rolled either way, and the usual 20 px: each bar stands clear above its
        # desktop's top edge, and all three of its segments stay reachable.
        for gap in (0, 20):
            w = (1920 - 5 * gap) / 4
            tiles = [(gap + i * (w + gap), 450.0, w, w * 9 / 16) for i in range(4)]
            g = {'w': 1920, 'h': 1200, 'half': (0.41421356, 0.25888), 'tiles': tiles}
            for roll in (0, 1500, -1500):
                placements = [{**FLAT, 'roll': roll}] * 4
                for i in range(4):
                    for segment in fp.LEVEL_SEGMENTS:
                        point = fp.project(g, tiles[i], placements[i], *on_bar(tiles[i], segment))
                        self.assertEqual(fp.hit(g, placements, point, icons=['bar'] * 4), ('roll', i, segment), (gap, roll, i, segment))
        # A bigger desktop reaching over its neighbour's bar takes those presses (its content covers it).
        w = 1920 / 4
        tiles = [(i * w, 450.0, w, w * 9 / 16) for i in range(4)]
        g = {'w': 1920, 'h': 1200, 'half': (0.41421356, 0.25888), 'tiles': tiles}
        big = [FLAT, {**FLAT, 'distance': 45}, FLAT, FLAT]
        point = fp.project(g, tiles[0], FLAT, *on_bar(tiles[0], 'cw'))
        self.assertEqual(fp.hit(g, big, point, icons=['bar'] * 4)[:2], ('desktop', 1))

    def test_nearer_desktops_bar_wins_where_two_overlap(self):
        # Two desktops moved over each other's bars: the nearer one's segment takes the press.
        near = {**FLAT, 'distance': 80}
        tile = T0
        g = {**G, 'tiles': [tile, tile]}
        point = fp.project(g, tile, near, *on_bar(tile, 'level'))
        self.assertEqual(fp.hit(g, [{**FLAT, 'distance': 80}, near], point, icons=['bar', 'bar'])[0], 'roll')
        self.assertEqual(fp.hit(g, [FLAT, near], point, icons=['none', 'bar']), ('roll', 1, 'level'))

    def test_the_band_along_each_edge_is_left_to_the_edge_resize(self):
        # The shared hit-zone contract (Cody, 2026-11-03): a desktop's content goes to its apps; the band LEVEL_EDGE_CLEAR
        # wide just outside every edge is the edge's resize zone, and the corners' zones reach round each corner; the bar
        # begins past that band (the edge's border reaches further only where no bar is). A press in the band under the
        # bar is never the bar's.
        placements, icons = [FLAT] * 3, ['bar'] * 3
        s = fp.level_bar_scale(T0)
        ty = T0[1]
        for segment in fp.LEVEL_SEGMENTS:
            x = on_bar(T0, segment)[0]
            for d in (0.5, 3.0, fp.LEVEL_EDGE_CLEAR - 0.3):
                self.assertEqual(fp.hit(G, placements, fp.project(G, T0, FLAT, x, ty - d * s), icons=icons), ('edge', 0, 't'), (segment, d))
            for d in (fp.LEVEL_BAR_GAP - fp.LEVEL_BAR_PAD + 0.3, fp.LEVEL_BAR_GAP, fp.LEVEL_BAR_GAP + 5):
                self.assertEqual(fp.hit(G, placements, fp.project(G, T0, FLAT, x, ty - d * s), icons=icons), ('roll', 0, segment), (segment, d))
        self.assertEqual(fp.level_roll(300), -300)
        self.assertEqual(fp.level_roll(-12000), fp.MAX_ROLL)
        self.assertEqual(fp.level_roll(None), 0)

    def test_the_zone_order_round_a_desktop(self):
        # panel > corner > pill > content > bar > edge > space, with the bars shown: each zone where the contract puts it.
        tx, ty, tw, th = T1
        placements = [FLAT] * 3
        px, py, pw, ph = fp.pill_rect(T1)
        def at(q, open_=None):
            return fp.hit(G, placements, fp.project(G, T1, FLAT, *q), open_=open_, icons=['bar'] * 3)
        self.assertEqual(at((tx - 4, ty - 4))[:3], ('corner', 1, 'tl'))
        self.assertEqual(at((px + pw / 2, py + ph / 2)), ('pill', 1))
        self.assertEqual(at((tx + tw / 2, ty + th / 2))[:2], ('desktop', 1))
        self.assertEqual(at(on_bar(T1, 'level')), ('roll', 1, 'level'))
        self.assertEqual(at((tx + tw / 2, ty - 3)), ('edge', 1, 't'))
        self.assertIsNone(at((tx + tw / 2, ty - 60)))
        self.assertEqual(fp.zone_of(at((tx + tw / 2, py + 60), open_=1)), 'control')  # the open panel over everything under it
        self.assertEqual(at((tx + 30, ty - fp.LEVEL_BAR_GAP)), None)       # the bar's not out by the corners

    def test_the_corner_keeps_its_zone_and_the_caps_lie_in_it(self):
        # A press on a corner's cap grabs the corner. The bar never reaches a corner's zone on a desktop wide enough, and a
        # press on one, bars shown or not, resizes; beyond that zone along the edge it is the edge's.
        placements = [FLAT] * 3
        tx, ty, tw, th = T0
        z = fp.corner_zone(T0)
        for name, cx, cy, ox, oy in (('tl', tx, ty, -1, -1), ('tr', tx + tw, ty, 1, -1), ('bl', tx, ty + th, -1, 1), ('br', tx + tw, ty + th, 1, 1)):
            for icons in (None, ['bar'] * 3):
                for q in ((cx + ox * 8.5, cy), (cx, cy + oy * 8.5), (cx + ox * 7, cy + oy * 7), (cx - ox * 3.5, cy + oy * 8.5), (cx + ox * 8.5, cy - oy * 3.5)):
                    self.assertEqual(fp.hit(G, placements, fp.project(G, T0, FLAT, *q), icons=icons)[:3], ('corner', 0, name), (name, q))
            self.assertEqual(fp.hit(G, placements, fp.project(G, T0, FLAT, cx - ox * (z + 2), cy + oy * 5), icons=['bar'] * 3)[0], 'edge')
        x, _, w, _ = fp.level_bar_rect(T0)
        self.assertGreater(x - fp.LEVEL_BAR_PAD, tx + z)
        self.assertLess(x + w + fp.LEVEL_BAR_PAD, tx + tw - z)


class PointerTests(Base):
    def setUp(self):
        super().setUp()
        self.control.set_roll_geometry([300, None, -40])  # XR-1 3 degrees counter-clockwise; XR-2 unmeasured; XR-3 level

    def roll(self, fs):
        return last(fs, 'roll')

    def test_the_bar_shows_on_every_desktop_in_the_overview_and_hides_when_idle(self):
        # Regression (Cody, 2026-11-03): the icons showed only on a desktop more than half a degree off level (and none at
        # all where the tilt is not measured: follow mode, no tracking), so a level desktop could not be rolled at all.
        # Every desktop shows its bar in the overview, tilted (XR-1), unmeasured (XR-2) or level (XR-3).
        self.free()
        r = self.p.roll_sent  # went out with the first overview frames
        self.assertEqual(r, {'roll': [0, 0, 0, 0], 'icons': ['bar', 'bar', 'bar', 'none'], 'hover': -1, 'side': 'none',
                             'active': False})
        self.clock.now += 31
        r = self.roll(frames(self.control.poll()))
        self.assertEqual(r['icons'], ['none'] * 4)

    def test_a_removed_desktop_takes_its_roll_with_it_and_the_others_keep_theirs(self):
        # Regression (merge of roll-level icons with live Add / Remove, 2026-11-02): rolls were per index, so removing
        # XR-2 handed its roll to XR-3. They are kept by name now, and so are the tilts (dot-cursor-new-desktop: the
        # renderer reports them only when they change, so dropped ones could stay gone) until its next report.
        self.p.rolls = [-300, 200, 150, 0]
        self.state.set_desktops(['XR-1', 'XR-3'], [FLAT, FLAT], [False, False])
        self.p.desktops_changed()
        self.assertEqual(self.p.rolls, [-300, 150, 0, 0])
        self.assertEqual(self.p.tilts, [300, -40])
        self.state.set_desktops(['XR-1', 'XR-2', 'XR-3'], [FLAT] * 3, [False] * 3)  # added back: level, as a new desktop
        self.p.desktops_changed()
        self.assertEqual(self.p.rolls, [-300, 0, 150, 0])
        self.assertEqual(self.p.tilts, [300, None, -40])  # not measured yet

    def test_a_click_levels_it_at_once(self):
        self.free()
        hover = self.roll(self.to(*grip(T0, 'cw')))
        self.assertEqual((hover['hover'], hover['side'], hover['active']), (0, 'cw', False))
        self.assertEqual(self.p.hover, ('roll', 0, 'cw'))
        r = self.roll(self.press() + self.release())
        self.assertEqual(r['roll'], [-300, 0, 0, 0])
        self.assertEqual(r['icons'], ['bar', 'bar', 'bar', 'none'])  # level now: still there, it can go either way
        self.assertEqual(self.p.roll_clicks, 1)
        self.assertEqual(self.state.placements, [FLAT] * 3)  # a session roll, never a saved placement
        self.assertEqual(self.store.saves, 0)
        hover = self.roll(self.to(*grip(T0, 'ccw', roll=-300)))  # the other segment lights up on its own
        self.assertEqual((hover['hover'], hover['side']), (0, 'ccw'))

    def turned(self, tile, start, deg, roll=0):
        """`start` turned counter-clockwise as you see it by `deg` round the desktop's centre."""
        cx, cy = fp.project(G, tile, {**FLAT, 'roll': roll}, tile[0] + tile[2] / 2, tile[1] + tile[3] / 2)
        a = math.atan2(cy - start[1], start[0] - cx) + math.radians(deg)
        r = math.hypot(start[0] - cx, start[1] - cy)
        return cx + r * math.cos(a), cy - r * math.sin(a)

    def test_a_drag_rolls_either_way_past_level_and_back(self):
        self.free()
        start = grip(T0, 'cw')
        self.to(*start)
        self.press()
        r = self.roll(self.to(*self.turned(T0, start, -2)))  # (the bar is near the centre: a degree stays under CLICK_PX)
        self.assertTrue(r['active'])
        self.assertEqual((r['hover'], r['side']), (0, 'cw'))
        self.assertAlmostEqual(r['roll'][0], -200, delta=3)
        self.to(*self.turned(T0, start, -2.9))  # within half a degree of level: it rests there
        self.assertEqual(self.p.rolls[0], -300)
        self.to(*self.turned(T0, start, -10))  # on past level
        self.assertAlmostEqual(self.p.rolls[0], -1000, delta=3)
        self.to(*self.turned(T0, start, 6))  # back the other way, past where it started
        self.assertAlmostEqual(self.p.rolls[0], 600, delta=3)
        self.release()
        self.assertEqual(self.p.roll_drags, 1)
        self.assertEqual(self.p.roll_clicks, 0)

    def test_both_directions_on_every_desktop(self):
        # Regression (Cody, 2026-11-03: "rolling still only goes one way"): each desktop showed only the arrow that
        # levels it, a drag was clamped between its roll at the press and level, and a level desktop showed nothing, so
        # a desktop could only ever be turned toward level. Now on every desktop, tilted either way, level or unmeasured,
        # both turn segments are there; a click on each turns it its own way (to level when level lies that way, else a
        # step), and a drag from either turns it both ways.
        self.free()
        for i, tile in enumerate(G['tiles']):
            for tilt in (300, -300, 2500, -2500, 0, None):
                tilts = [None] * 3; tilts[i] = tilt
                self.control.set_roll_geometry(tilts)
                for side in fp.ROLL_SIDES:
                    way = 1 if side == 'ccw' else -1  # counter-clockwise positive, as you see it
                    self.p.rolls = [0] * 4
                    self.assertEqual(self.p._icons()[i], 'bar')
                    self.to(*grip(tile, side))
                    self.assertEqual(self.p.hover, ('roll', i, side), (i, tilt, side))
                    r = self.roll(self.press() + self.release())
                    want = -tilt if tilt is not None and abs(tilt) > fp.ROLL_LEVEL and -tilt * way > 0 else way * fp.ROLL_STEP
                    self.assertEqual(r['roll'][i], want, (i, tilt, side))
                    self.assertEqual(math.copysign(1, r['roll'][i]), way)  # always the segment's way
                    self.assertEqual(r['icons'][i], 'bar')
                    # Drag: from 0, turn the dot round the desktop's centre one way, then the other, past the start.
                    for first in (1, -1):
                        self.p.rolls = [0] * 4
                        start = grip(tile, side)
                        self.to(*start)
                        self.press()
                        self.to(*self.turned(tile, start, first * 5))
                        self.assertAlmostEqual(self.p.rolls[i], first * 500, delta=5, msg=(i, tilt, side, first))
                        self.to(*self.turned(tile, start, -first * 5))
                        self.assertAlmostEqual(self.p.rolls[i], -first * 500, delta=5, msg=(i, tilt, side, first))
                        self.release()

    def test_the_bar_follows_a_corner_resize_and_a_pill_move(self):
        # Cody (2026-11-03): the handles must move with resizing the desktop. Resize XR-1 from its bottom-right corner, then
        # move it by its pill: each time its bar is above the top edge where it went, a press there rolls it, and where it
        # was before is nothing of its.
        self.free()
        tx, ty, tw, th = T0
        self.to(*fp.project(G, T0, FLAT, tx + tw + 3, ty + th + 3))
        self.assertEqual(self.p.hover, ('corner', 0, 'br'))
        self.press(); self.by(24, 14); self.release()  # bigger, still clear of XR-2 (whose corners would win)
        resized = dict(self.state.placements[0])
        self.assertLess(resized['distance'], 96)
        for segment in fp.LEVEL_SEGMENTS:
            self.to(*grip(T0, segment, resized))
            self.assertEqual(self.p.hover, ('roll', 0, segment))
        self.to(*pill_centre(T0, resized))
        self.assertEqual(self.p.hover, ('pill', 0))
        self.press(); self.by(-40, 0); self.release()  # away from XR-2, the dot still under the desktop, so still free
        moved = dict(self.state.placements[0])
        self.assertNotEqual((moved['x'], moved['y']), (resized['x'], resized['y']))
        for side in fp.ROLL_SIDES:  # the second one on the desktop as the first click turned it
            self.to(*grip(T0, side, moved, roll=self.p.rolls[0]))
            self.assertEqual(self.p.hover, ('roll', 0, side))
            self.press(); self.release()  # (the turn may leave the dot off the segment: the hover is not checked after it)
        self.assertEqual(self.p.roll_clicks, 2)
        self.assertEqual(self.p.rolls[0], -300)  # 'ccw' a step away from level, then 'cw' straight to level

    def test_a_press_on_a_corner_cap_resizes(self):
        # The cap is the corner's resize zone made visible: pressing it grabs the corner, and rolls nothing.
        self.free()
        tx, ty, tw, _ = T0
        self.to(*fp.project(G, T0, FLAT, tx + tw + 8.5, ty - 2.5))  # on its side leg
        self.assertEqual(self.p.hover, ('corner', 0, 'tr'))
        self.press()
        self.assertEqual((self.p.drag['kind'], self.p.drag['handle']), ('resize', 'tr'))
        self.by(25, -15); self.release()
        self.assertLess(self.state.placements[0]['distance'], 100)
        self.assertEqual((self.p.roll_clicks, self.p.roll_drags, self.p.rolls[0]), (0, 0, 0))

    def test_the_middle_segment_levels_and_moves_nothing_else(self):
        # XR-1 measures 3 degrees off, and has been moved and resized and rolled further: a click levels it, the roll R
        # would give it, and leaves its placement alone. Unmeasured, like R, it takes the levelling roll off.
        self.free()
        moved = {**FLAT, 'x': 40, 'y': -30, 'distance': 90, 'curvature': 20}
        self.state.placements[0] = dict(moved)
        self.p.rolls = [1700, -900, 400, 0]
        self.to(*grip(T0, 'level', moved, roll=1700))
        self.assertEqual(self.p.hover, ('roll', 0, 'level'))
        self.press()
        self.assertEqual(self.p.rolls[:3], [1700, -900, 400])  # on the release, like the turn segments' clicks
        r = self.roll(self.release())
        self.assertEqual(r['roll'], [-300, -900, 400, 0])
        self.assertEqual(self.state.placements[0], moved)
        self.assertEqual((self.p.roll_levels, self.p.roll_clicks, self.p.roll_drags), (1, 0, 0))
        self.to(*grip(T1, 'level', roll=-900))
        self.press(); r = self.roll(self.release())
        self.assertEqual(r['roll'], [-300, 0, 400, 0])  # XR-2 is not measured: no roll
        self.assertEqual(self.p.roll_levels, 2)
        self.assertEqual(self.store.saves, 0)  # a session roll, never a saved placement (poll saves it with the layout)

    def test_the_middle_segment_gives_the_same_roll_as_r(self):
        self.free()
        for tilt in (300, -1250, None):
            self.control.set_roll_geometry([tilt, None, None])
            self.p.rolls = [800, 0, 0, 0]
            self.to(*grip(T0, 'level', roll=800))
            self.press(); self.release()
            by_bar = self.p.rolls[0]
            self.p.rolls = [800, 0, 0, 0]
            self.p.level(0)
            self.assertEqual(by_bar, self.p.rolls[0], tilt)

    def test_a_press_dragged_off_the_middle_segment_does_nothing(self):
        self.free()
        self.p.rolls = [800, 0, 0, 0]
        start = grip(T0, 'level', roll=800)
        self.to(*start)
        self.press()
        self.assertEqual((self.p.roll_sent['hover'], self.p.roll_sent['side']), (0, 'level'))
        self.to(start[0] + 40, start[1] - 30)
        self.assertEqual(self.p.rolls[0], 800)  # it is no roll handle
        self.assertEqual((self.p.roll_sent['hover'], self.p.roll_sent['side'], self.p.roll_sent['active']), (-1, 'none', False))
        self.assertNotIn('look', self.p.sent)
        self.release()
        self.assertEqual((self.p.rolls[0], self.p.roll_levels, self.p.roll_drags, self.p.roll_clicks), (800, 0, 0, 0))

    def test_super_stops_a_press_on_any_segment(self):
        self.free()
        for segment in fp.LEVEL_SEGMENTS:
            self.p.rolls = [800, 0, 0, 0]
            self.to(*grip(T0, segment, roll=800))
            self.press()
            self.assertEqual(self.p.drag['kind'], 'roll')
            self.p.super_down()
            self.assertIsNone(self.p.drag)
            self.release()  # it is no click: the button's later release finds nothing held
            self.assertEqual((self.p.rolls[0], self.p.roll_levels, self.p.roll_clicks), (800, 0, 0), segment)

    def test_only_what_the_dot_is_on_lights(self):
        # One segment at a time on one desktop, or one cap; nothing else. The `roll` frame says which segment, the
        # `resize_handle` frame which corner, and they are never lit together. A dragged one stays lit and is `active`.
        self.free()
        tx, ty, tw, th = T0
        def lit():
            r = self.p.roll_sent
            cap = (self.p.shown['desktop'], self.p.shown['corner']) if self.p.shown['visible'] else None
            return (r['hover'], r['side']), cap
        for i, tile in enumerate(G['tiles']):
            for segment in fp.LEVEL_SEGMENTS:
                self.to(*grip(tile, segment))
                self.assertEqual(lit(), ((i, segment), None))
        self.to(*fp.project(G, T0, FLAT, tx + tw + 8.5, ty + th + 2.5))  # a cap's leg: the segment goes out
        self.assertEqual(lit(), ((-1, 'none'), (0, 'br')))
        self.to(*fp.project(G, T0, FLAT, tx + tw / 2, ty - 60))           # empty space: nothing
        self.assertEqual(lit(), ((-1, 'none'), None))
        self.to(*fp.project(G, T0, FLAT, tx + tw + 8.5, ty + th / 2))     # an edge has the dot's look only
        self.assertEqual(lit(), ((-1, 'none'), None))
        self.to(*grip(T0, 'cw'))
        self.press(); self.by(40, 0)  # sideways: a release on content would hand the dot to the real cursor
        self.assertEqual(lit(), ((0, 'cw'), None))
        self.assertTrue(self.p.roll_sent['active'])
        self.release()
        self.p.rolls = [0] * 4  # back upright, so the corner is where it was
        self.to(*fp.project(G, T0, FLAT, tx + tw + 8.5, ty + th + 2.5))
        self.press(); self.by(6, 6)
        self.assertEqual(lit(), ((-1, 'none'), (0, 'br')))
        self.assertTrue(self.p.shown['active'])
        self.release()

    def test_the_dot_shows_rotate_on_every_segment(self):
        self.free()
        for segment in fp.LEVEL_SEGMENTS:
            self.to(*grip(T0, segment))
            self.assertEqual(self.p.sent.get('look'), 'rotate', segment)
            self.assertEqual(fp.zone_of(self.p.hover), 'rotate')

    def test_r_on_an_open_panel_levels_its_desktop_too(self):
        # Cody (2026-11-03): R in placement mode (the pill's "R flat") also resets the desktop's rotation and tilt to level.
        # XR-1's tilt is measured (3 degrees counter-clockwise): level is a -3 degree roll. XR-2's is not: no roll at all.
        self.free()
        self.p.rolls = [1700, -900, 400, 0]
        self.key('menu_toggle')  # XR-1's panel (the selected desktop)
        self.key('menu_inc')     # moved a little, so R has a placement to reset too
        self.assertNotEqual(self.state.placements[0], FLAT)
        out = self.key('menu_reset')
        self.assertEqual(self.state.placements[0], FLAT)  # as before: flat in its slot
        self.assertEqual(last(out, 'roll')['roll'], [-300, -900, 400, 0])  # and level; the others keep theirs
        self.assertEqual(self.p.roll_levels, 1)
        self.key('menu_desktop_next')  # XR-2's panel: already flat, still levelled
        out = self.key('menu_reset')
        self.assertNotIn('placement', [f['op'] for f in out])
        self.assertEqual(last(out, 'roll')['roll'], [-300, 0, 400, 0])
        # The reset may move a desktop, so its tilt changes: its next measured tilts level it again for LEVEL_SETTLE_S.
        self.control.set_roll_geometry([500, None, -40])
        self.assertEqual(self.p.rolls[:3], [-500, 0, 400])  # XR-2 not measured yet: it stays as R left it
        self.control.set_roll_geometry([500, -250, -40])
        self.assertEqual(self.p.rolls[:3], [-500, 250, 400])  # XR-3 was not reset: its roll is its own
        self.clock.now += fp.LEVEL_SETTLE_S + 0.1
        self.control.set_roll_geometry([800, -600, -40])
        self.assertEqual(self.p.rolls[:3], [-500, 250, 400])  # settled: tilts are only reported again
        # Pressing a turn segment takes over at once, even while the reset settles.
        self.key('menu_reset')  # XR-2 again
        self.assertEqual(self.p.rolls[1], 600)
        self.key('menu_back')
        self.to(*grip(T1, 'ccw', roll=600))
        self.assertEqual(self.p.hover, ('roll', 1, 'ccw'))
        self.press(); self.release()
        self.assertEqual(self.p.rolls[1], 600 + fp.ROLL_STEP)
        self.control.set_roll_geometry([800, -700, -40])
        self.assertEqual(self.p.rolls[1], 600 + fp.ROLL_STEP)

    def test_r_with_no_panel_open_levels_nothing(self):
        self.p.rolls = [1700, 0, 0, 0]
        self.key('menu_reset')  # a late key after the panel closed: ignored, as for the placement
        self.assertEqual((self.p.rolls[0], self.p.roll_levels), (1700, 0))

    def test_the_footer_and_the_help_card_say_r_levels(self):
        from tools.native_spike import view
        src = (ROOT / 'spike/src/glass.cpp').read_text()
        self.assertIn('R reset"', src); self.assertIn('R RESET"', src)
        self.assertEqual(dict(view.help_rows())['Ctrl+Alt+L'], 'Placement pill; R in it resets, levels')

    def test_hit_testing_uses_the_rolled_desktop(self):
        self.control.set_roll_geometry([3000, None, None])  # 30 degrees off
        self.free()
        self.p.rolls[0] = -1500  # half levelled: the bar is on the turned surface
        self.to(*grip(T0, 'cw', roll=-1500))
        self.assertEqual(self.p.hover, ('roll', 0, 'cw'))
        self.to(*grip(T0, 'ccw', roll=-1500))
        self.assertEqual(self.p.hover, ('roll', 0, 'ccw'))
        self.to(*grip(T0, 'cw', roll=0))  # where it would be unrolled: nothing there
        self.assertNotEqual(self.p.hover, ('roll', 0, 'cw'))

    def test_a_new_tilt_from_the_renderer_changes_which_segment_levels(self):
        self.free()
        self.to(*grip(T1, 'ccw'))
        self.press(); self.release()  # XR-2 not measured: a step counter-clockwise
        self.assertEqual(self.p.rolls[1], fp.ROLL_STEP)
        self.control.set_roll_geometry([300, -600, 0])  # XR-2 now measured: tilted clockwise, the step not enough
        self.to(*grip(T1, 'ccw', roll=fp.ROLL_STEP))
        self.press(); self.release()
        self.assertEqual(self.p.rolls[1], 600)  # level lies counter-clockwise: straight there
        self.to(*grip(T1, 'ccw', roll=600))  # where the turned desktop's segment is now
        self.press(); self.release()
        self.assertEqual(self.p.rolls[1], 600 + fp.ROLL_STEP)  # level now: a step on past it


class StatusLineTests(unittest.TestCase):
    def test_the_supervisor_routes_roll_geometry_to_the_view_control(self):
        got = []
        class Control:
            def set_roll_geometry(self, tilts): got.append(tilts)
        sup = supervisor.Supervisor.__new__(supervisor.Supervisor)
        sup.control, sup._status_buffer, sup._status_discard, sup.status = Control(), b'', False, None
        sup.status_bytes(b'{"roll_geometry":{"tilt":[120,null]}}\n')
        sup.status_bytes(b'{"roll_geometry":{"tilt":[1.5]}}\n')  # malformed: dropped
        self.assertEqual(got, [[120, None]])


if __name__ == '__main__':
    unittest.main()
