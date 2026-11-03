"""Roll-level icons, supervisor side (free_pointer.py): the renderer's `roll_geometry` line, which icon a desktop shows
(above its higher top corner, the way that levels it, none when level or unmeasured), the icon's fixed apparent size,
hit testing on rolled desktops (the icon before the corner), a click that levels at once, a drag that rolls toward level
and never past it, the `roll` frame, and the icons hiding with the pills.

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
        for bad in ({'tilt': []}, {'tilt': [1, 2, 3, 4]}, {'tilt': [1.5]}, {'tilt': [18001]}, {'tilt': [True]}, {'tilt': 1},
                    {'tilt': [1], 'x': 1}, None, []):
            with self.assertRaises(ValueError): fp.parse_roll_geometry(bad)

    def test_roll_frame(self):
        state = {'roll': [-300, 0, 9000], 'icons': ['cw', 'none', 'ccw'], 'hover': 0, 'active': True}
        f = json.loads(fp.roll_frame('run', 's', 9, state))
        self.assertEqual((f['op'], f['request_id'], f['payload']), ('roll', 9, state))
        for bad in ({**state, 'roll': [0, 0]}, {**state, 'roll': [0, 0, 9001]}, {**state, 'icons': ['up', 'none', 'none']},
                    {**state, 'hover': 3}, {**state, 'active': 1}, {**state, 'x': 1}):
            with self.assertRaises(ValueError): fp.roll_frame('run', 's', 9, bad)

    def test_a_rolled_desktop_is_hit_where_it_is_drawn(self):
        p = {**FLAT, 'roll': 2000}
        for q in ((T1[0] + 30, T1[1] + 40), (T1[0] + T1[2] - 5, T1[1] + T1[3] / 2)):
            seen = fp.project(G, T1, p, *q)
            back = fp.unproject(G, T1, p, seen)
            self.assertAlmostEqual(back[0], q[0], delta=0.05); self.assertAlmostEqual(back[1], q[1], delta=0.05)
            self.assertNotAlmostEqual(fp.project(G, T1, FLAT, *q)[1], seen[1], delta=1)  # the roll moved it

    def test_the_icon_wins_over_the_corner_it_sits_above_and_only_when_shown(self):
        x, y, w, h = fp.roll_icon_rect(T0, 'cw')
        point = fp.project(G, T0, FLAT, x + w - 3, y + h - 1)  # inside the top-right corner's grab zone too
        placements = [FLAT] * 3
        self.assertEqual(fp.hit(G, placements, point)[:2], ('corner', 0))
        self.assertEqual(fp.hit(G, placements, point, icons=['cw', None, None]), ('roll', 0, 'cw'))
        self.assertEqual(fp.hit(G, placements, point, icons=['ccw', None, None])[:2], ('corner', 0))  # the other side's


class PointerTests(Base):
    def setUp(self):
        super().setUp()
        self.control.set_roll_geometry([300, None, -40])  # XR-1 3 degrees counter-clockwise; XR-2 unmeasured; XR-3 level

    def roll(self, fs):
        return last(fs, 'roll')

    def test_icons_show_only_on_a_tilted_desktop_and_hide_when_idle(self):
        self.free()
        r = self.p.roll_sent  # went out with the first overview frames
        self.assertEqual(r, {'roll': [0, 0, 0], 'icons': ['cw', 'none', 'none'], 'hover': -1, 'active': False})
        self.clock.now += 31
        r = self.roll(frames(self.control.poll()))
        self.assertEqual(r['icons'], ['none', 'none', 'none'])

    def test_a_click_levels_it_at_once(self):
        self.free()
        hover = self.roll(self.to(*icon_centre(T0, 'cw')))
        self.assertEqual((hover['hover'], hover['active']), (0, False))
        self.assertEqual(self.p.hover, ('roll', 0, 'cw'))
        r = self.roll(self.press() + self.release())
        self.assertEqual(r['roll'], [-300, 0, 0])
        self.assertEqual(r['icons'], ['none', 'none', 'none'])  # level now: no icon
        self.assertEqual(self.p.roll_clicks, 1)
        self.assertEqual(self.state.placements, [FLAT] * 3)  # a session roll, never a saved placement
        self.assertEqual(self.store.saves, 0)

    def test_a_drag_rolls_toward_level_and_never_past_it(self):
        self.free()
        start = icon_centre(T0, 'cw')
        self.to(*start)
        self.press()
        cx, cy = fp.project(G, T0, FLAT, T0[0] + T0[2] / 2, T0[1] + T0[3] / 2)
        def turned(deg):  # the press point turned clockwise as you see it by deg round the desktop's centre
            a = math.atan2(cy - start[1], start[0] - cx) - math.radians(deg)
            r = math.hypot(start[0] - cx, start[1] - cy)
            return cx + r * math.cos(a), cy - r * math.sin(a)
        r = self.roll(self.to(*turned(1)))
        self.assertTrue(r['active'])
        self.assertAlmostEqual(r['roll'][0], -100, delta=3)
        self.assertEqual(r['icons'][0], 'cw')  # 2 degrees still to go
        r = self.roll(self.to(*turned(10)))  # far past level: stops there
        self.assertEqual(r['roll'][0], -300)
        r = self.roll(self.to(*turned(-5)) + self.to(*turned(-6)))  # back the other way: never past where it started
        self.assertEqual(self.p.rolls[0], 0)
        self.release()
        self.assertEqual(self.p.roll_drags, 1)
        self.assertEqual(self.p.roll_clicks, 0)

    def test_hit_testing_uses_the_rolled_desktop(self):
        self.control.set_roll_geometry([3000, None, None])  # 30 degrees off
        self.free()
        self.p.rolls[0] = -1500  # half levelled: the icon is still up, on the turned surface
        self.assertEqual(self.p._icons()[0], 'cw')
        self.to(*icon_centre(T0, 'cw', roll=-1500))
        self.assertEqual(self.p.hover, ('roll', 0, 'cw'))
        self.to(*icon_centre(T0, 'cw', roll=0))  # where it would be unrolled: nothing there
        self.assertNotEqual(self.p.hover, ('roll', 0, 'cw'))

    def test_a_new_tilt_from_the_renderer_brings_the_icon_back(self):
        self.free()
        self.to(*icon_centre(T0, 'cw'))
        self.press(); self.release()
        self.control.set_roll_geometry([300, -250, 0])  # XR-2 now measured: tilted clockwise
        r = self.roll(self.by(1, 0))
        self.assertEqual(r['icons'], ['none', 'ccw', 'none'])


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
