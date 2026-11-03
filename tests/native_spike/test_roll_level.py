"""Roll brackets, supervisor side (free_pointer.py): the renderer's `roll_geometry` line, both brackets on every desktop
in the overview (level, tilted or unmeasured), which one levels it, their geometry (an arrow bracket hugging each top
corner from outside, a share of the desktop in tile px), hit testing as they move, scale and roll with the desktop
(resize, move, roll), resize corners always winning over them, a click (to level when it lies that way, else a step), a
drag that rolls either way, the `roll` frame, and the brackets hiding with the pills.

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


def along(tile, side, t):
    """Tile point on the bracket's centre line, `t` 0..1 from the top arm's end over the corner to the arrow's tip."""
    tx, ty, tw, _ = tile
    arm, gap = fp.roll_arm(tile), fp.ROLL_GAP * fp.roll_scale(tile)
    out, corner = (1, tx + tw) if side == 'cw' else (-1, tx)
    arc = math.pi / 2 * gap
    total = 2 * arm + arc
    d = t * total
    if d <= arm: return (corner - out * (arm - d), ty - gap)
    if d <= arm + arc:
        a = (d - arm) / gap
        return (corner + out * gap * math.sin(a), ty - gap * math.cos(a))
    return (corner + out * gap, ty + (d - arm - arc))


def grip(tile, side, placement=FLAT, roll=0, where='side'):
    """Where the dot grabs desktop's `side` bracket (canvas px): on its side arm (or top arm) halfway between the corner's
    resize zone and the arm's end, on the desktop as placed (with `roll`)."""
    tx, ty, tw, _ = tile
    arm, gap, z = fp.roll_arm(tile), fp.ROLL_GAP * fp.roll_scale(tile), fp.corner_zone(tile)
    out, corner = (1, tx + tw) if side == 'cw' else (-1, tx)
    q = (corner + out * gap, ty + (z + arm) / 2) if where == 'side' else (corner - out * (z + arm) / 2, ty - gap)
    return fp.project(G, tile, {**placement, 'roll': roll}, *q)


class PureTests(unittest.TestCase):
    def test_the_icon_is_on_the_higher_side_and_points_the_way_that_levels(self):
        self.assertEqual(fp.roll_side(300, 0), 'cw')     # turned counter-clockwise: top-right is higher, turn it clockwise
        self.assertEqual(fp.roll_side(-300, 0), 'ccw')
        self.assertIsNone(fp.roll_side(300, -300))       # levelled
        self.assertIsNone(fp.roll_side(fp.ROLL_LEVEL, 0))  # within half a degree counts as level
        self.assertEqual(fp.roll_side(fp.ROLL_LEVEL + 1, 0), 'cw')
        self.assertIsNone(fp.roll_side(None, 0))         # not measured (no tracking, not anchor mode)
        self.assertEqual(fp.roll_side(100, -400), 'ccw')  # rolled past level the other way

    def test_a_click_turns_the_arrows_way_to_level_or_a_step(self):
        # The arrow that points toward level levels it at once; the other one (and both on a level or unmeasured
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
        with self.assertRaises(ValueError): fp.roll_click(0, 0, 'both')

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

    def test_icon_sides(self):
        self.assertEqual(fp.roll_icon_sides('both'), ('ccw', 'cw'))
        self.assertEqual(fp.roll_icon_sides('cw'), ('cw',))
        self.assertEqual(fp.roll_icon_sides('none'), ())
        self.assertEqual(fp.roll_icon_sides(None), ())

    def test_the_bracket_hugs_its_corner_a_share_of_the_desktop(self):
        # Arms a share of the shorter side (clamped), the stroke ROLL_GAP outside the edge, round the corner; the hit region
        # is outside the desktop only and never reaches the middle of the top edge.
        self.assertAlmostEqual(fp.roll_arm(T0), fp.ROLL_ARM_SHARE * T0[3])
        self.assertEqual(fp.roll_arm((0, 0, 300, 160)), fp.ROLL_ARM_MIN)
        self.assertAlmostEqual(fp.roll_arm((10, 40, 120, 60)), fp.ROLL_ARM_MAX_SHARE * 60)
        self.assertEqual((fp.roll_arm((0, 0, 0, 10)), fp.roll_scale((0, 0, 0, 10))), (0.0, 0.0))
        self.assertEqual(fp.roll_scale(T0), 1.0)
        self.assertEqual(fp.roll_distance((0, 0, 0, 10), 'cw', (0, 0)), math.inf)
        with self.assertRaises(ValueError): fp.roll_distance(T0, 'left', (0, 0))
        tx, ty, tw, th = T0
        arm, reach = fp.roll_arm(T0), fp.ROLL_HEAD_HALF + fp.ROLL_PAD
        for side, corner, out in (('ccw', tx, -1), ('cw', tx + tw, 1)):
            self.assertAlmostEqual(fp.roll_distance(T0, side, (corner, ty)), fp.ROLL_GAP)  # round the corner, ROLL_GAP out
            for t in (0.0, 0.2, 0.5, 0.8, 1.0):
                q = along(T0, side, t)
                self.assertAlmostEqual(fp.roll_distance(T0, side, q), 0.0, places=6)
                self.assertTrue(fp.on_roll(T0, side, q), (side, t))
            self.assertTrue(fp.on_roll(T0, side, (corner + out * (fp.ROLL_GAP + reach - 0.3), ty + arm / 2)))
            self.assertFalse(fp.on_roll(T0, side, (corner + out * (fp.ROLL_GAP + reach + 0.3), ty + arm / 2)))
            self.assertFalse(fp.on_roll(T0, side, (corner - out * 0.5, ty + arm / 2)))  # on the desktop: its own
            self.assertFalse(fp.on_roll(T0, side, (corner + out * fp.ROLL_GAP, ty + arm + reach + 0.3)))  # past the tip
            self.assertFalse(fp.on_roll(T0, side, (tx + tw / 2, ty - fp.ROLL_GAP)))  # the middle of the top edge
            self.assertFalse(fp.on_roll(T0, side, None))
        self.assertLess(2 * arm + 2 * reach, tw)  # the two brackets' regions never meet over the top edge

    def test_brackets_move_scale_and_roll_with_their_desktop(self):
        # Through a resize (distance), a move (the orbit) and a roll the brackets stay on the desktop's corners: where the
        # bracket is drawn (the same tile px on the desktop as placed) is what is hit, the same share of the desktop as
        # you see it, and where it was before the change is not that bracket any more.
        placements = [dict(FLAT) for _ in range(3)]
        def apparent(p, a, b):  # the angle between two of XR-2's tile points as seen from the eye
            pa, pb = fp.place_point(G, T1, p, *a), fp.place_point(G, T1, p, *b)
            dot = sum(x * y for x, y in zip(pa, pb))
            return math.acos(min(1.0, dot / math.sqrt(sum(x * x for x in pa) * sum(y * y for y in pb))))
        tx, ty, tw, _ = T1
        share = None
        # (placement, whether it carries the corner well clear of where it was: a 40 degree curve barely moves it). Each
        # keeps XR-2 clear of its neighbours: where a bracket lies on another desktop's content, that content takes the
        # press (test_a_bracket_never_takes_a_press_from_content).
        for p, moved in (({**FLAT, 'distance': 180}, True), (FLAT, False), ({**FLAT, 'y': 200, 'distance': 60}, True),
                         ({**FLAT, 'y': -180}, True), ({**FLAT, 'x': 30, 'y': 150, 'distance': 140}, True),
                         ({**FLAT, 'curvature': 40}, False), ({**FLAT, 'roll': 1500}, True),
                         ({**FLAT, 'y': 160, 'distance': 70, 'roll': -2500}, True)):
            placed = [placements[0], p, placements[2]]
            for side in fp.ROLL_SIDES:
                for where in ('side', 'top'):
                    point = grip(T1, side, p, p.get('roll', 0), where)
                    self.assertEqual(fp.hit(G, placed, point, icons=['both'] * 3), ('roll', 1, side), (p, side, where))
                    # The same spot on the desktop as it was before (flat, at its slot) is that bracket's no longer.
                    if moved:
                        old = grip(T1, side, FLAT, 0, where)
                        self.assertNotEqual(fp.hit(G, placed, old, icons=['both'] * 3), ('roll', 1, side), (p, side, where))
                # The arm's apparent length keeps its share of the desktop's apparent width (it scales with the desktop).
                corner = (tx + tw, ty) if side == 'cw' else (tx, ty)
                tip = along(T1, side, 1.0)
                got = apparent(p, corner, tip) / apparent(p, (tx, ty), (tx + tw, ty))
                share = share or got
                self.assertAlmostEqual(got, share, delta=0.03 * share)
        self.assertAlmostEqual(share, math.hypot(fp.ROLL_GAP, fp.roll_arm(T1)) / tw, delta=0.01)

    def test_roll_geometry_line(self):
        self.assertEqual(fp.parse_roll_geometry({'tilt': [120, None, -18000]}), [120, None, -18000])
        self.assertEqual(fp.parse_roll_geometry({'tilt': [1, 2, 3, 4]}), [1, 2, 3, 4])  # up to MAX_DESKTOPS
        for bad in ({'tilt': []}, {'tilt': [1, 2, 3, 4, 5]}, {'tilt': [1.5]}, {'tilt': [18001]}, {'tilt': [True]}, {'tilt': 1},
                    {'tilt': [1], 'x': 1}, None, []):
            with self.assertRaises(ValueError): fp.parse_roll_geometry(bad)

    def test_roll_frame(self):
        state = {'roll': [-300, 0, 9000, 0], 'icons': ['cw', 'none', 'ccw', 'both'], 'hover': 0, 'side': 'cw', 'active': True}
        f = json.loads(fp.roll_frame('run', 's', 9, state))
        self.assertEqual((f['op'], f['request_id'], f['payload']), ('roll', 9, state))
        json.loads(fp.roll_frame('run', 's', 9, fp.ROLL_HIDDEN))
        for bad in ({**state, 'roll': [0, 0, 0]}, {**state, 'roll': [0, 0, 0, 9001]}, {**state, 'icons': ['up', 'none', 'none', 'none']},
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

    def stroke(self, g, placements, i, side, at_least):
        """Hit along desktop i's `side` bracket, 41 points from the cap to the tip: each is that bracket, a resize corner
        (round the corner, where a neighbour's reaches), a neighbour's edge band next to it or a desktop's content (a
        neighbour reaching over it: the renderer draws the brackets under every desktop); at least `at_least` of them are
        the bracket."""
        tile = g['tiles'][i]
        hits = 0
        for k in range(41):
            point = fp.project(g, tile, placements[i], *along(tile, side, k / 40))
            got = fp.hit(g, placements, point, icons=['both'] * len(placements))
            if got == ('roll', i, side): hits += 1
            else: self.assertIn(got[0], ('corner', 'edge', 'desktop'), (i, side, k, got))
        self.assertGreaterEqual(hits, at_least, (i, side))
        return hits

    def test_a_bracket_never_takes_a_press_from_content(self):
        # Cody (2026-11-03, anchor-no-click-maximize): presses on a desktop's content go to its apps. Showing the brackets
        # only ever changes what a press does where it would otherwise hit nothing or an edge's border past the band next
        # to the desktop: never on any desktop's content, a resize corner or a pill, whoever's bracket is drawn there. Dense grids over the fixture's row, the row with one
        # desktop resized over its neighbours, rolled desktops, and four desktops with no space between them.
        w = 1920 / 4
        tight = {'w': 1920, 'h': 1200, 'half': (0.41421356, 0.25888), 'tiles': [(i * w, 450.0, w, w * 9 / 16) for i in range(4)]}
        layouts = ((G, [FLAT] * 3), (G, [FLAT, {**FLAT, 'distance': 55}, FLAT]), (G, [{**FLAT, 'roll': 2000}, FLAT, {**FLAT, 'roll': -1200}]),
                   (tight, [FLAT] * 4), (tight, [{**FLAT, 'roll': 1500}] * 4))
        for g, placements in layouts:
            icons, rolls = ['both'] * len(placements), 0
            for i, tile in enumerate(g['tiles']):  # a 4 px grid round each bracket, on its desktop as placed
                arm, reach = fp.roll_arm(tile), fp.ROLL_GAP + fp.ROLL_HEAD_HALF + fp.ROLL_PAD + 4
                for side in fp.ROLL_SIDES:
                    corner, out = (tile[0] + tile[2], 1) if side == 'cw' else (tile[0], -1)
                    for u in range(-int(arm) - 16, int(reach), 4):
                        for v in range(-int(reach), int(arm) + 16, 4):
                            point = fp.project(g, tile, placements[i], corner + out * u, tile[1] + v)
                            bare = fp.hit(g, placements, point)
                            shown = fp.hit(g, placements, point, icons=icons)
                            if bare is not None and bare[0] != 'edge': self.assertEqual(shown, bare, (i, side, u, v, placements))
                            elif shown != bare: self.assertEqual(shown[0], 'roll'); rolls += 1
            self.assertGreater(rolls, 300, placements)  # and the brackets are there to press

    def test_in_a_tight_overview_brackets_lie_under_neighbours_content(self):
        # Four desktops with no space between them: a bracket's side arm lies on its neighbour's content, and both
        # corners' resize zones reach round it. The content and the corners take those presses; every bracket stays
        # reachable along its top arm, rolled either way.
        w = 1920 / 4
        tiles = [(i * w, 450.0, w, w * 9 / 16) for i in range(4)]
        g = {'w': 1920, 'h': 1200, 'half': (0.41421356, 0.25888), 'tiles': tiles}
        for roll in (0, 1500, -1500):
            placements = [{**FLAT, 'roll': roll}] * 4
            for i in range(4):
                for side in fp.ROLL_SIDES:
                    self.stroke(g, placements, i, side, 8)
        placements = [FLAT] * 4
        point = fp.project(g, tiles[1], FLAT, *along(tiles[1], 'ccw', 0.9))  # XR-2's left side arm, on XR-1's content
        self.assertEqual(fp.hit(g, placements, point, icons=['both'] * 4)[:2], ('desktop', 0))
        point = fp.project(g, tiles[1], FLAT, *along(tiles[1], 'ccw', 0.2))  # its top arm, above its own top edge
        self.assertEqual(fp.hit(g, placements, point, icons=['both'] * 4), ('roll', 1, 'ccw'))
        # A bigger desktop reaching over its neighbours' brackets takes those presses (its content covers them).
        big = [FLAT, {**FLAT, 'distance': 60}, FLAT, FLAT]
        point = fp.project(g, tiles[0], FLAT, *along(tiles[0], 'cw', 0.5))
        self.assertEqual(fp.hit(g, big, point, icons=['both'] * 4)[:2], ('desktop', 1))

    def test_every_bracket_of_four_desktops_is_hit_along_its_stroke(self):
        # The usual 20 px between four desktops: neighbours' side arms share the gap, a few px apart. The bracket whose
        # stroke is nearer the dot wins, so level, every bracket is hit all along its stroke outside the corners' zones;
        # rolled either way, a neighbour's turned content may reach over part of a side arm.
        w = (1920 - 5 * 20) / 4
        tiles = [(20 + i * (w + 20), 450.0, w, w * 9 / 16) for i in range(4)]
        g = {'w': 1920, 'h': 1200, 'half': (0.41421356, 0.25888), 'tiles': tiles}
        for roll in (0, 1500, -1500):
            placements = [{**FLAT, 'roll': roll}] * 4
            for i in range(4):
                for side in fp.ROLL_SIDES:
                    hits = self.stroke(g, placements, i, side, 8)
                    if roll == 0: self.assertGreaterEqual(hits, 20, (i, side))

    def test_the_band_along_each_edge_is_left_to_the_edge_resize(self):
        # The shared hit-zone contract (Cody, 2026-11-03): a desktop's content goes to its apps; the band ROLL_EDGE_CLEAR
        # wide just outside every edge is the edge's resize zone, and the corners' zones reach round each corner; the
        # brackets begin past that band (the edge's border reaches further only where no bracket is). A press in the band
        # beside or under a bracket's arm is never the bracket's.
        tx, ty, tw, _ = T0
        arm, z = fp.roll_arm(T0), fp.corner_zone(T0)
        placements, icons = [FLAT] * 3, ['both'] * 3
        for side, corner, out in (('cw', tx + tw, 1), ('ccw', tx, -1)):
            def spots(d): return ((corner + out * d, ty + (z + arm) / 2), (corner - out * (z + arm) / 2, ty - d))  # side, top arm
            for d in (0.5, 3.0, fp.ROLL_EDGE_CLEAR - 0.3):
                for q, edge in zip(spots(d), ('r' if side == 'cw' else 'l', 't')):
                    self.assertEqual(fp.hit(G, placements, fp.project(G, T0, FLAT, *q), icons=icons), ('edge', 0, edge), (side, d, q))
            for d in (fp.ROLL_EDGE_CLEAR + 0.3, fp.ROLL_GAP, fp.ROLL_GAP + fp.ROLL_HEAD_HALF):  # (further out, in the 40 px gap, nears the neighbour's)
                for q in spots(d):
                    self.assertEqual(fp.hit(G, placements, fp.project(G, T0, FLAT, *q), icons=icons), ('roll', 0, side), (side, d, q))
        self.assertEqual(fp.level_roll(300), -300)
        self.assertEqual(fp.level_roll(-12000), fp.MAX_ROLL)
        self.assertEqual(fp.level_roll(None), 0)

    def test_the_corner_always_wins_over_the_bracket_round_it(self):
        # The bracket hugs its corner from outside, but the corner's resize zone stays the resize's: a corner drag from
        # anywhere round it still resizes, the bracket showing or not. Beyond that zone, along its arms, the bracket takes
        # the press; inside the desktop never (its pixels stay the desktop's, the corner's square included: border_handle).
        placements = [FLAT] * 3
        tx, ty, tw, th = T0
        z, arm = fp.corner_zone(T0), fp.roll_arm(T0)
        for side, corner, name, out in (('cw', tx + tw, 'tr', 1), ('ccw', tx, 'tl', -1)):
            for icons in (None, ['both'] * 3, [side, None, None]):
                for q in ((corner + out * fp.ROLL_GAP * math.sqrt(0.5), ty - fp.ROLL_GAP * math.sqrt(0.5)),  # the arc
                          (corner, ty - fp.ROLL_GAP), (corner + out * fp.ROLL_GAP, ty),                     # its ends
                          (corner - out * (z - 1), ty - fp.ROLL_GAP), (corner + out * fp.ROLL_GAP, ty + z - 1),
                          (corner + out * (z - 0.5), ty - z + 0.5), (corner - out * 3, ty - 3)):           # the near part
                    self.assertEqual(fp.hit(G, placements, fp.project(G, T0, FLAT, *q), icons=icons), ('corner', 0, name), (side, icons, q))
            for q in ((corner + out * fp.ROLL_GAP, ty + z + 2), (corner + out * fp.ROLL_GAP, ty + arm),     # the side arm, the tip
                      (corner - out * (z + 2), ty - fp.ROLL_GAP), (corner - out * arm, ty - fp.ROLL_GAP)):  # the top arm, the cap
                point = fp.project(G, T0, FLAT, *q)
                self.assertEqual(fp.hit(G, placements, point, icons=['both'] * 3), ('roll', 0, side), (side, q))
                self.assertEqual(fp.hit(G, placements, point, icons=[side, None, None]), ('roll', 0, side), (side, q))
                self.assertIsNone(fp.hit(G, placements, point))  # hidden: empty space, as before
                other = 'ccw' if side == 'cw' else 'cw'
                self.assertIsNone(fp.hit(G, placements, point, icons=[other, None, None]))
            # Just inside the desktop beside its side arm: the desktop's.
            for q in ((corner - out * 2, ty + arm / 2), (corner - out * 3, ty + 3)):
                self.assertEqual(fp.hit(G, placements, fp.project(G, T0, FLAT, *q), icons=['both'] * 3)[:2], ('desktop', 0))
        # The bottom corners and the pill are untouched.
        self.assertEqual(fp.hit(G, placements, fp.project(G, T0, FLAT, tx + tw + 5, ty + th + 5), icons=['both'] * 3), ('corner', 0, 'br'))
        self.assertEqual(fp.hit(G, placements, fp.project(G, T0, FLAT, tx + tw / 2, ty + th + 14), icons=['both'] * 3), ('pill', 0))


class PointerTests(Base):
    def setUp(self):
        super().setUp()
        self.control.set_roll_geometry([300, None, -40])  # XR-1 3 degrees counter-clockwise; XR-2 unmeasured; XR-3 level

    def roll(self, fs):
        return last(fs, 'roll')

    def test_both_icons_show_on_every_desktop_in_the_overview_and_hide_when_idle(self):
        # Regression (Cody, 2026-11-03): the icons showed only on a desktop more than half a degree off level (and none at
        # all where the tilt is not measured: follow mode, no tracking), so a level desktop could not be rolled at all.
        # Every desktop shows both arrows in the overview now, tilted (XR-1), unmeasured (XR-2) or level (XR-3).
        self.free()
        r = self.p.roll_sent  # went out with the first overview frames
        self.assertEqual(r, {'roll': [0, 0, 0, 0], 'icons': ['both', 'both', 'both', 'none'], 'hover': -1, 'side': 'none',
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
        self.assertEqual(r['icons'], ['both', 'both', 'both', 'none'])  # level now: still both, it can go either way
        self.assertEqual(self.p.roll_clicks, 1)
        self.assertEqual(self.state.placements, [FLAT] * 3)  # a session roll, never a saved placement
        self.assertEqual(self.store.saves, 0)
        hover = self.roll(self.to(*grip(T0, 'ccw', roll=-300)))  # the other arrow lights up on its own
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
        r = self.roll(self.to(*self.turned(T0, start, -1)))
        self.assertTrue(r['active'])
        self.assertEqual((r['hover'], r['side']), (0, 'cw'))
        self.assertAlmostEqual(r['roll'][0], -100, delta=3)
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
        # both arrows are there; a click on each turns it its own way (to level when level lies that way, else a step),
        # and a drag from either arrow turns it both ways.
        self.free()
        for i, tile in enumerate(G['tiles']):
            for tilt in (300, -300, 2500, -2500, 0, None):
                tilts = [None] * 3; tilts[i] = tilt
                self.control.set_roll_geometry(tilts)
                for side in fp.ROLL_SIDES:
                    way = 1 if side == 'ccw' else -1  # counter-clockwise positive, as you see it
                    self.p.rolls = [0] * 4
                    self.assertEqual(self.p._icons()[i], 'both')
                    self.to(*grip(tile, side))
                    self.assertEqual(self.p.hover, ('roll', i, side), (i, tilt, side))
                    r = self.roll(self.press() + self.release())
                    want = -tilt if tilt is not None and abs(tilt) > fp.ROLL_LEVEL and -tilt * way > 0 else way * fp.ROLL_STEP
                    self.assertEqual(r['roll'][i], want, (i, tilt, side))
                    self.assertEqual(math.copysign(1, r['roll'][i]), way)  # always the arrow's way
                    self.assertEqual(r['icons'][i], 'both')
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

    def test_the_brackets_follow_a_corner_resize_and_a_pill_move(self):
        # Cody (2026-11-03): the brackets must move with resizing the desktop. Resize XR-1 from its bottom-right corner,
        # then move it by its pill: each time its brackets are round the corners where they went, a press there rolls it,
        # and where they were before is nothing of theirs.
        self.free()
        tx, ty, tw, th = T0
        before = {side: grip(T0, side) for side in fp.ROLL_SIDES}
        self.to(*fp.project(G, T0, FLAT, tx + tw + 3, ty + th + 3))
        self.assertEqual(self.p.hover, ('corner', 0, 'br'))
        self.press(); self.by(24, 14); self.release()  # bigger, still clear of XR-2 (whose corners would win)
        resized = dict(self.state.placements[0])
        self.assertLess(resized['distance'], 96)
        for side in fp.ROLL_SIDES:
            self.to(*grip(T0, side, resized))
            self.assertEqual(self.p.hover, ('roll', 0, side))
            # (Tested in place: the old spot is now on the bigger desktop, and moving the dot onto it would hand it back
            # to the cursor.)
            self.assertNotEqual(fp.hit(G, self.p._rolled(), before[side], icons=self.p._icons()), ('roll', 0, side))
        self.to(*pill_centre(T0, resized))
        self.assertEqual(self.p.hover, ('pill', 0))
        self.press(); self.by(-40, 0); self.release()  # away from XR-2, the dot still under the desktop, so still free
        moved = dict(self.state.placements[0])
        self.assertNotEqual((moved['x'], moved['y']), (resized['x'], resized['y']))
        for side in fp.ROLL_SIDES:  # the second one on the desktop as the first click turned it
            self.to(*grip(T0, side, moved, roll=self.p.rolls[0]))
            self.assertEqual(self.p.hover, ('roll', 0, side))
            self.press(); self.release()  # (the turn may leave the dot off the bracket: the hover is not checked after it)
        self.assertEqual(self.p.roll_clicks, 2)
        self.assertEqual(self.p.rolls[0], -300)  # 'ccw' a step away from level, then 'cw' straight to level

    def test_a_press_on_the_brackets_bend_resizes(self):
        # The bend round the corner is inside the corner's resize zone: pressing there grabs the corner, not the bracket.
        self.free()
        tx, ty, tw, _ = T0
        g = fp.ROLL_GAP * math.sqrt(0.5)
        self.to(*fp.project(G, T0, FLAT, tx + tw + g, ty - g))
        self.assertEqual(self.p.hover, ('corner', 0, 'tr'))
        self.press()
        self.assertEqual((self.p.drag['kind'], self.p.drag['handle']), ('resize', 'tr'))
        self.by(25, -15); self.release()
        self.assertLess(self.state.placements[0]['distance'], 100)
        self.assertEqual((self.p.roll_clicks, self.p.roll_drags, self.p.rolls[0]), (0, 0, 0))

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
        # Pressing a bracket takes over at once, even while the reset settles.
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
        self.p.rolls[0] = -1500  # half levelled: the icons are on the turned surface
        self.to(*grip(T0, 'cw', roll=-1500))
        self.assertEqual(self.p.hover, ('roll', 0, 'cw'))
        self.to(*grip(T0, 'ccw', roll=-1500))
        self.assertEqual(self.p.hover, ('roll', 0, 'ccw'))
        self.to(*grip(T0, 'cw', roll=0))  # where it would be unrolled: nothing there
        self.assertNotEqual(self.p.hover, ('roll', 0, 'cw'))

    def test_a_new_tilt_from_the_renderer_changes_which_arrow_levels(self):
        self.free()
        self.to(*grip(T1, 'ccw'))
        self.press(); self.release()  # XR-2 not measured: a step counter-clockwise
        self.assertEqual(self.p.rolls[1], fp.ROLL_STEP)
        self.control.set_roll_geometry([300, -600, 0])  # XR-2 now measured: tilted clockwise, the step not enough
        self.to(*grip(T1, 'ccw', roll=fp.ROLL_STEP))
        self.press(); self.release()
        self.assertEqual(self.p.rolls[1], 600)  # level lies counter-clockwise: straight there
        self.to(*grip(T1, 'ccw', roll=600))  # where the turned desktop's arrow is now
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
