"""Roll-level icons, supervisor side (free_pointer.py): the renderer's `roll_geometry` line, both arrows on every desktop
in the overview (level, tilted or unmeasured), which arrow levels it, the icon's fixed apparent size, hit testing on
rolled desktops (the icon before the corner), a click (to level when it lies that way, else a step that way), a drag
that rolls either way, the `roll` frame, and the icons hiding with the pills.

Offline only: a fake clock, the renderer's overview geometry fixture. No compositor, renderer binary or glasses.
"""
import json
import math
import unittest

from tools.native_spike import free_pointer as fp, supervisor
from tools.native_spike.geometry import DEFAULT_PLACEMENT
from test_glass_pills import Base, G, T0, T1, frames, last

FLAT = dict(DEFAULT_PLACEMENT)


def icon_centre(tile, side, placement=FLAT, roll=0):
    x, y, w, h = fp.roll_icon_rect(tile, side, placement['distance'] / 100)
    return fp.project(G, tile, {**placement, 'roll': roll}, x + w / 2, y + h / 2)


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

    def test_the_icon_is_one_size_whatever_the_desktop(self):
        for tile in ((100, 300, 459, 258), (0, 0, 4000, 2000), (10, 40, 120, 60)):
            l, r = fp.roll_icon_rect(tile, 'ccw'), fp.roll_icon_rect(tile, 'cw')
            self.assertEqual((l[2], l[3], r[2], r[3]), (fp.ROLL_ICON_PX,) * 4)
            self.assertEqual(l[0], tile[0]); self.assertAlmostEqual(r[0] + r[2], tile[0] + tile[2])
            self.assertAlmostEqual(l[1] + l[3], tile[1] - fp.ROLL_ICON_GAP)
        # Twice as far (a corner resize): twice the tile px, so the same size as you see it.
        tile = G['tiles'][1]
        near, far = ({**FLAT, 'distance': d} for d in (100, 200))
        def apparent(p):
            x, y, w, h = fp.roll_icon_rect(tile, 'cw', p['distance'] / 100)
            a, b = fp._flat(G, tile, p, x, y + h / 2), fp._flat(G, tile, p, x + w, y + h / 2)  # on the overview's plane
            return math.hypot(b[0] - a[0], b[1] - a[1])
        self.assertAlmostEqual(apparent(near), apparent(far), delta=0.05)
        self.assertAlmostEqual(apparent(near), fp.ROLL_ICON_PX, delta=1.0)
        with self.assertRaises(ValueError): fp.roll_icon_rect(tile, 'left')

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

    def test_an_icon_wins_over_a_nearer_neighbour_in_a_tight_four_desktop_overview(self):
        # Regression (2026-11-03, merge widening to four desktops): the icons are drawn over every desktop, but the hit
        # test went desktop by desktop, nearest first, so a nearer neighbour's corner zone or rolled surface took the
        # press meant for the icon above the next desktop's corner (the outer desktops' inward icons, and on the right
        # half the top-left, counter-clockwise ones): those desktops could not be rolled that way.
        w = (1920 - 5 * 0) / 4
        tiles = [(i * w, 450.0, w, w * 9 / 16) for i in range(4)]
        g = {'w': 1920, 'h': 1200, 'half': (0.41421356, 0.25888), 'tiles': tiles}
        for i, side, roll in ((0, 'cw', 0), (3, 'ccw', 0), (2, 'ccw', 1500), (3, 'ccw', 1500), (0, 'cw', -1500), (1, 'cw', -1500)):
            placements = [{**FLAT, 'roll': roll}] * 4
            icons = [None] * 4; icons[i] = side
            x, y, w_, h = fp.roll_icon_rect(tiles[i], side)
            for fx, fy in ((0.5, 0.5), (0.1, 0.1), (0.9, 0.9), (0.1, 0.9), (0.9, 0.1)):
                point = fp.project(g, tiles[i], placements[i], x + w_ * fx, y + h * fy)
                self.assertEqual(fp.hit(g, placements, point, icons=icons), ('roll', i, side), (i, side, roll, fx, fy))
            # Hidden, the same point is the neighbour's again.
            point = fp.project(g, tiles[i], placements[i], x + w_ / 2, y + h / 2)
            self.assertNotEqual((fp.hit(g, placements, point) or ('',))[0], 'roll')

    def test_every_arrow_of_four_desktops_is_hit_when_all_show_both(self):
        # The overview shows both arrows on every desktop; with the usual 20 px spacing each one is reachable, rolled
        # either way, whatever is nearer.
        w = (1920 - 5 * 20) / 4
        tiles = [(20 + i * (w + 20), 450.0, w, w * 9 / 16) for i in range(4)]
        g = {'w': 1920, 'h': 1200, 'half': (0.41421356, 0.25888), 'tiles': tiles}
        for roll in (0, 1500, -1500):
            placements = [{**FLAT, 'roll': roll}] * 4
            for i in range(4):
                for side in fp.ROLL_SIDES:
                    x, y, w_, h = fp.roll_icon_rect(tiles[i], side)
                    for fx, fy in ((0.5, 0.5), (0.1, 0.1), (0.9, 0.9)):
                        point = fp.project(g, tiles[i], placements[i], x + w_ * fx, y + h * fy)
                        self.assertEqual(fp.hit(g, placements, point, icons=['both'] * 4), ('roll', i, side), (i, side, roll, fx, fy))

    def test_the_icon_wins_over_the_corner_it_sits_above_and_only_when_shown(self):
        x, y, w, h = fp.roll_icon_rect(T0, 'cw')
        point = fp.project(G, T0, FLAT, x + w - 3, y + h - 1)  # inside the top-right corner's grab zone too
        placements = [FLAT] * 3
        self.assertEqual(fp.hit(G, placements, point)[:2], ('corner', 0))
        self.assertEqual(fp.hit(G, placements, point, icons=['cw', None, None]), ('roll', 0, 'cw'))
        self.assertEqual(fp.hit(G, placements, point, icons=['ccw', None, None])[:2], ('corner', 0))  # the other side's
        self.assertEqual(fp.hit(G, placements, point, icons=['both', None, None]), ('roll', 0, 'cw'))
        # Both icons always show, so the corner keeps the gap under its icon and everything beyond the desktop's side:
        # a resize grabbed from just outside the corner still resizes. The padding up and inward stays the icon's.
        for side, corner in (('cw', 'tr'), ('ccw', 'tl')):
            x, y, w, h = fp.roll_icon_rect(T0, side)
            out = x + w + 2 if side == 'cw' else x - 2
            for q, want in (((out, y + h - 2), ('corner', 0, corner)),                        # beyond the side
                            ((x + w / 2, y + h + 2), ('corner', 0, corner)),                  # the gap under it
                            ((x + w / 2, y - 2), ('roll', 0, side)),                          # padding above
                            ((x - 2 if side == 'cw' else x + w + 2, y + h / 2), ('roll', 0, side))):  # inward
                self.assertEqual(fp.hit(G, placements, fp.project(G, T0, FLAT, *q), icons=['both'] * 3), want, (side, q))


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
        hover = self.roll(self.to(*icon_centre(T0, 'cw')))
        self.assertEqual((hover['hover'], hover['side'], hover['active']), (0, 'cw', False))
        self.assertEqual(self.p.hover, ('roll', 0, 'cw'))
        r = self.roll(self.press() + self.release())
        self.assertEqual(r['roll'], [-300, 0, 0, 0])
        self.assertEqual(r['icons'], ['both', 'both', 'both', 'none'])  # level now: still both, it can go either way
        self.assertEqual(self.p.roll_clicks, 1)
        self.assertEqual(self.state.placements, [FLAT] * 3)  # a session roll, never a saved placement
        self.assertEqual(self.store.saves, 0)
        hover = self.roll(self.to(*icon_centre(T0, 'ccw', roll=-300)))  # the other arrow lights up on its own
        self.assertEqual((hover['hover'], hover['side']), (0, 'ccw'))

    def turned(self, tile, start, deg, roll=0):
        """`start` turned counter-clockwise as you see it by `deg` round the desktop's centre."""
        cx, cy = fp.project(G, tile, {**FLAT, 'roll': roll}, tile[0] + tile[2] / 2, tile[1] + tile[3] / 2)
        a = math.atan2(cy - start[1], start[0] - cx) + math.radians(deg)
        r = math.hypot(start[0] - cx, start[1] - cy)
        return cx + r * math.cos(a), cy - r * math.sin(a)

    def test_a_drag_rolls_either_way_past_level_and_back(self):
        self.free()
        start = icon_centre(T0, 'cw')
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
                    self.to(*icon_centre(tile, side))
                    self.assertEqual(self.p.hover, ('roll', i, side), (i, tilt, side))
                    r = self.roll(self.press() + self.release())
                    want = -tilt if tilt is not None and abs(tilt) > fp.ROLL_LEVEL and -tilt * way > 0 else way * fp.ROLL_STEP
                    self.assertEqual(r['roll'][i], want, (i, tilt, side))
                    self.assertEqual(math.copysign(1, r['roll'][i]), way)  # always the arrow's way
                    self.assertEqual(r['icons'][i], 'both')
                    # Drag: from 0, turn the dot round the desktop's centre one way, then the other, past the start.
                    for first in (1, -1):
                        self.p.rolls = [0] * 4
                        start = icon_centre(tile, side)
                        self.to(*start)
                        self.press()
                        self.to(*self.turned(tile, start, first * 5))
                        self.assertAlmostEqual(self.p.rolls[i], first * 500, delta=5, msg=(i, tilt, side, first))
                        self.to(*self.turned(tile, start, -first * 5))
                        self.assertAlmostEqual(self.p.rolls[i], -first * 500, delta=5, msg=(i, tilt, side, first))
                        self.release()

    def test_hit_testing_uses_the_rolled_desktop(self):
        self.control.set_roll_geometry([3000, None, None])  # 30 degrees off
        self.free()
        self.p.rolls[0] = -1500  # half levelled: the icons are on the turned surface
        self.to(*icon_centre(T0, 'cw', roll=-1500))
        self.assertEqual(self.p.hover, ('roll', 0, 'cw'))
        self.to(*icon_centre(T0, 'ccw', roll=-1500))
        self.assertEqual(self.p.hover, ('roll', 0, 'ccw'))
        self.to(*icon_centre(T0, 'cw', roll=0))  # where it would be unrolled: nothing there
        self.assertNotEqual(self.p.hover, ('roll', 0, 'cw'))

    def test_a_new_tilt_from_the_renderer_changes_which_arrow_levels(self):
        self.free()
        self.to(*icon_centre(T1, 'ccw'))
        self.press(); self.release()  # XR-2 not measured: a step counter-clockwise
        self.assertEqual(self.p.rolls[1], fp.ROLL_STEP)
        self.control.set_roll_geometry([300, -600, 0])  # XR-2 now measured: tilted clockwise, the step not enough
        self.to(*icon_centre(T1, 'ccw', roll=fp.ROLL_STEP))
        self.press(); self.release()
        self.assertEqual(self.p.rolls[1], 600)  # level lies counter-clockwise: straight there
        self.to(*icon_centre(T1, 'ccw', roll=600))  # where the turned desktop's arrow is now
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
