"""Overview free pointer (free_pointer.py): geometry contract, projection and hit testing, drag maths, the desktop / free
mode state machine, pill and corner drags, gates (zoom, typing, video, idle; an open placement panel is not one), the
sampler thread, compositor Lua and wiring. The glass pills' clicks, panel and sliders are in test_glass_pills.py.

Offline only: fake clocks, fake compositor clients and socketpairs. No compositor, renderer binary or glasses.
"""
import json
import math
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest import mock

from tools.native_spike import free_pointer as fp, orbit, resize, view, xr_control
from tools.native_spike.geometry import DEFAULT_PLACEMENT, PLACEMENT_BOUNDS
from tools.native_spike.profiles import LayoutStore

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = json.loads((ROOT / 'tests/native_spike/fixtures/overview_pointer_payloads.json').read_text())
G = fp.parse_overview_geometry(json.loads(FIXTURE['geometry']['line'])['overview_geometry'])
NAMES = ['XR-1', 'XR-2', 'XR-3']
# The managed outputs right of a 2560-wide physical monitor, touching each other (as plan_outputs lays them out).
MONITORS = [{'name': 'DP-1', 'x': 0, 'y': 0, 'width': 2560, 'height': 1440, 'scale': 1.0},
            {'name': 'XR-1', 'x': 2560, 'y': 0, 'width': 1920, 'height': 1080, 'scale': 1.0},
            {'name': 'XR-2', 'x': 4480, 'y': 0, 'width': 1920, 'height': 1080, 'scale': 1.0},
            {'name': 'XR-3', 'x': 6400, 'y': 0, 'width': 1920, 'height': 1080, 'scale': 1.0}]
OUTPUTS = resize.outputs_from_monitors(MONITORS, NAMES)
X1, X2, X3 = 2560, 4480, 6400
T0, T1 = G['tiles'][0], G['tiles'][1]
GAIN = T0[2] / 1920  # canvas px per cursor px on a flat desktop at distance 100
FLAT = [dict(DEFAULT_PLACEMENT) for _ in range(3)]
BIND_ARGS = ('/usr/bin/python3', '/x/tools/native_spike/xr_control.py', '/run/user/1000/c.sock', 'a' * 32, 'e' * 64)


def frames(data, ops=None):
    out = [json.loads(line) for line in (data or b'').decode().splitlines()]
    return [f for f in out if ops is None or f['op'] in ops]


def last(fs, op):
    return next((f['payload'] for f in reversed(fs) if f['op'] == op), None)


class Clock:
    def __init__(self): self.now = 100.0
    def __call__(self): return self.now


class MemoryPlacements:
    def __init__(self):
        self.saved = [dict(DEFAULT_PLACEMENT) for _ in range(3)]
        self.saves = 0
    def load(self): return [dict(p) for p in self.saved]
    def save(self, placements): self.saved = [dict(p) for p in placements]; self.saves += 1


class ContractTests(unittest.TestCase):
    def test_the_renderers_geometry_line_parses(self):
        self.assertEqual((G['w'], G['h']), (1920, 1200))
        self.assertEqual(G['half'], (0.36, 0.225))
        self.assertEqual(len(G['tiles']), 3)
        self.assertAlmostEqual(G['tiles'][1][0], 666.67)
        self.assertEqual(G['tiles'][0][1:], (435.0, 586.67, 330.0))

    def test_malformed_geometry_is_refused(self):
        good = json.loads(FIXTURE['geometry']['line'])['overview_geometry']
        bad = [None, [], {}, dict(good, w=0), dict(good, h=9000), dict(good, w=1.5), dict(good, half=[0.3]),
               dict(good, half=[0, 0.2]), dict(good, half=['0.3', 0.2]), dict(good, tiles=[]), dict(good, tiles=[[0, 0, 1, 1]] * 4),
               dict(good, tiles=[[0, 0, -5, 10]]), dict(good, tiles=[[0, 0, 5]]), dict(good, tiles=[[0, 0, 5, float('nan')]]),
               dict(good, tiles=[[0, 0, True, 5]]), dict(good, extra=1)]
        for value in bad:
            with self.subTest(value=value), self.assertRaises(ValueError): fp.parse_overview_geometry(value)

    def test_pointer_frames_match_the_renderers_fixture(self):
        for payload in FIXTURE['valid']:
            frame = json.loads(fp.overview_pointer_frame('run', 'session', 3, payload))
            self.assertEqual((frame['op'], frame['payload']), ('overview_pointer', payload))
        for case in FIXTURE['invalid']:
            with self.subTest(case['why']), self.assertRaises(ValueError): fp.overview_pointer_frame('run', 'session', 3, case['payload'])

    def test_supervisor_routes_the_geometry_line_and_ignores_a_bad_one(self):
        from tools.native_spike.supervisor import Supervisor
        control = mock.Mock()
        sup = Supervisor(mock.Mock(), lambda: None, control=control)
        sup.status_bytes(FIXTURE['geometry']['line'].encode() + b'\n')
        control.set_overview_geometry.assert_called_once_with(G)
        sup.status_bytes(b'{"overview_geometry":{"w":0}}\n')
        self.assertEqual(control.set_overview_geometry.call_count, 1)
        self.assertEqual(sup.status, {})  # never taken for run status


class ProjectionTests(unittest.TestCase):
    def test_desktops_at_their_slots_map_and_map_back(self):
        # The centred desktop is the canvas itself; the side ones face you, so they are turned toward the eye.
        for tile in G['tiles']:
            for q in ((tile[0], tile[1]), (tile[0] + tile[2] / 2, tile[1] + tile[3] / 2), (tile[0] + tile[2] - 1, tile[1] + 7)):
                p = fp.project(G, tile, DEFAULT_PLACEMENT, *q)
                if tile is T1: self.assertAlmostEqual(p[0], q[0], places=3); self.assertAlmostEqual(p[1], q[1], places=3)
                back = fp.unproject(G, tile, DEFAULT_PLACEMENT, p)
                self.assertAlmostEqual(back[0], q[0], delta=0.05); self.assertAlmostEqual(back[1], q[1], delta=0.05)
        centre = fp.project(G, T0, DEFAULT_PLACEMENT, T0[0] + T0[2] / 2, T0[1] + T0[3] / 2)
        self.assertAlmostEqual(centre[0], T0[0] + T0[2] / 2, delta=0.01)  # a side desktop's centre stays where its slot is

    def test_the_orbit_turns_the_desktop_round_you_and_distance_scales_it_about_its_centre(self):
        cx, cy = T0[0] + T0[2] / 2, T0[1] + T0[3] / 2
        def direction(p):
            x, y, z = fp.place_point(G, T0, p, cx, cy)
            return math.degrees(math.atan2(x, -z)), math.degrees(math.atan2(y, math.hypot(x, z))), math.dist((x, y, z), (0, 0, 0))
        a0, e0, r0 = direction(DEFAULT_PLACEMENT)
        a, e, r = direction({**DEFAULT_PLACEMENT, 'x': 500, 'y': 200, 'distance': 200})  # 50 degrees right, 20 up, twice as far
        self.assertAlmostEqual(a, a0 + 50, places=4); self.assertAlmostEqual(e, e0 + 20, places=4)
        self.assertAlmostEqual(r, 2 * r0, places=6)
        self.assertGreater(direction({**DEFAULT_PLACEMENT, 'x': 1800})[0] % 360, 90)  # behind you
        def size(p):  # the angle from its centre to a corner, seen from the eye
            c, k = fp.place_point(G, T0, p, cx, cy), fp.place_point(G, T0, p, T0[0], T0[1])
            return math.acos(sum(a * b for a, b in zip(c, k)) / (math.dist(c, (0, 0, 0)) * math.dist(k, (0, 0, 0))))
        self.assertAlmostEqual(size({**DEFAULT_PLACEMENT, 'x': 500, 'distance': 200}), size(DEFAULT_PLACEMENT) / 2, delta=0.02 * size(DEFAULT_PLACEMENT))
        self.assertAlmostEqual(size({**DEFAULT_PLACEMENT, 'x': -1200, 'y': 300}), size(DEFAULT_PLACEMENT), places=9)  # a rigid turn
        self.assertLess(fp.project(G, T0, {**DEFAULT_PLACEMENT, 'y': 20}, cx, cy)[1], cy)  # +y is up

    def test_unproject_inverts_tilted_and_curved_placements(self):
        for p in ({**DEFAULT_PLACEMENT, 'curvature': 90, 'distance': 60},
                  {**DEFAULT_PLACEMENT, 'x': -100, 'y': 100, 'distance': 200},
                  {**DEFAULT_PLACEMENT, 'curvature': 120, 'distance': 140, 'y': -40}):
            for q in ((T1[0] + 3, T1[1] + 3), (T1[0] + T1[2] / 2, T1[1] + T1[3] / 2), (T1[0] + T1[2] - 3, T1[1] + T1[3] - 3)):
                with self.subTest(p=p, q=q):
                    back = fp.unproject(G, T1, p, fp.project(G, T1, p, *q))
                    self.assertIsNotNone(back)
                    self.assertAlmostEqual(back[0], q[0], delta=0.25); self.assertAlmostEqual(back[1], q[1], delta=0.25)

    def test_at_the_extreme_bounds_the_answer_is_still_on_the_surface_under_the_pointer(self):
        # Fully curved, turned and tilted, a desktop can fold so two of its pixels line up; either is under the pointer.
        p = {**DEFAULT_PLACEMENT, 'curvature': 120, 'distance': 50}
        for q in ((T1[0] + 3, T1[1] + 3), (T1[0] + T1[2] / 2, T1[1] + T1[3] / 2)):
            target = fp.project(G, T1, p, *q)
            back = fp.unproject(G, T1, p, target)
            self.assertIsNotNone(back)
            again = fp.project(G, T1, p, *back)
            self.assertLess(math.hypot(again[0] - target[0], again[1] - target[1]), 0.5)

    def test_a_point_behind_the_eye_is_not_on_the_canvas(self):
        self.assertIsNone(fp.to_canvas(G, (0.1, 0.1, 0.5)))

    def test_desktops_turned_round_you_are_hit_on_their_own_surface_and_never_from_behind(self):
        for x, y in ((150, 0), (-300, 80), (0, -250), (420, 300)):
            p = {**DEFAULT_PLACEMENT, 'x': x, 'y': y, 'curvature': 40}
            for q in ((T1[0] + 5, T1[1] + 5), (T1[0] + T1[2] / 2, T1[1] + T1[3] / 2)):
                with self.subTest(x=x, y=y, q=q):
                    back = fp.unproject(G, T1, p, fp.project(G, T1, p, *q))
                    self.assertAlmostEqual(back[0], q[0], delta=0.25); self.assertAlmostEqual(back[1], q[1], delta=0.25)
        behind = [dict(DEFAULT_PLACEMENT), {**DEFAULT_PLACEMENT, 'x': 1800}, dict(DEFAULT_PLACEMENT)]
        self.assertIsNone(fp.project(G, T1, behind[1], T1[0] + T1[2] / 2, T1[1] + T1[3] / 2))  # not on the canvas
        self.assertIsNone(fp.unproject(G, T1, behind[1], (T1[0] + T1[2] / 2, T1[1] + T1[3] / 2)))
        self.assertIsNone(fp.hit(G, behind, (T1[0] + T1[2] / 2, T1[1] + T1[3] / 2)))  # where its slot was: nothing now


class HitTests(unittest.TestCase):
    def test_pill_corners_desktop_and_space(self):
        pill = fp.pill_rect(T0)
        mid = pill[0] + pill[2] / 2
        self.assertEqual(pill[1], T0[1] + T0[3] + fp.PILL_GAP)  # under the desktop
        self.assertEqual(fp.hit(G, FLAT, (mid, pill[1] + 2)), ('pill', 0))
        self.assertEqual(fp.hit(G, FLAT, (mid, T0[1] + T0[3] + 1)), ('pill', 0))     # the gap over the pill grabs it too
        self.assertEqual(fp.hit(G, FLAT, (mid, pill[1] + pill[3] + 9)), ('pill', 0))  # a little below it as well
        self.assertIsNone(fp.hit(G, FLAT, (mid, pill[1] + pill[3] + 12)))
        self.assertIsNone(fp.hit(G, FLAT, (mid, T0[1] - 3)))  # nothing above the desktop (the old drag bar's place)
        self.assertIsNone(fp.hit(G, FLAT, (T0[0] + 20, T0[1] - 40)))
        self.assertEqual(fp.hit(G, FLAT, (T0[0] - 8, T0[1] - 8)), ('corner', 0, 'tl'))  # outside the desktop: now reachable
        self.assertEqual(fp.hit(G, FLAT, (T0[0] + T0[2] + 10, T0[1] + T0[3] + 10)), ('corner', 0, 'br'))
        self.assertEqual(fp.hit(G, FLAT, (T1[0] + T1[2] - 3, T1[1] + 2)), ('corner', 1, 'tr'))
        self.assertEqual(fp.hit(G, FLAT, (T1[0] + 3, T1[1] + T1[3] - 3)), ('corner', 1, 'bl'))
        h = fp.hit(G, FLAT, (T1[0] + 200, T1[1] + 100))
        self.assertEqual(h[:2], ('desktop', 1)); self.assertAlmostEqual(h[2][0], T1[0] + 200, places=2)
        self.assertIsNone(fp.hit(G, FLAT, ((T0[0] + T0[2] + T1[0]) / 2, T0[1] + 150)))  # between desktops
        self.assertIsNone(fp.hit(None, FLAT, (0, 0))); self.assertIsNone(fp.hit(G, FLAT, None))

    def test_the_pill_zone_never_reaches_a_corner(self):
        x0, y0 = G['w'] / 2 - 30, G['h'] / 2 - 20  # centred, so it is the canvas itself
        tile = (x0, y0, 60.0, 40.0)  # a very small desktop: the pill is wider than the space between the corners
        g = dict(G, tiles=[tile])
        z = fp.corner_zone(tile)
        for x in range(-12, 72, 2):
            h = fp.hit(g, FLAT[:1], (x0 + x, y0 + 40 + 3))  # the gap under the desktop
            if abs(x) <= z or abs(x - 60) <= z: self.assertEqual(h and h[0], 'corner', x)  # corners win
            elif h is not None: self.assertEqual(h, ('pill', 0), x)

    def test_the_front_desktop_wins_where_desktops_overlap(self):
        near = [{**DEFAULT_PLACEMENT, 'x': 60, 'distance': 60}, dict(DEFAULT_PLACEMENT), dict(DEFAULT_PLACEMENT)]  # XR-1 in front of XR-2
        centre = fp.project(G, T0, near[0], T0[0] + T0[2] / 2, T0[1] + T0[3] / 2)
        self.assertEqual(fp.hit(G, near, centre)[:2], ('desktop', 0))
        self.assertEqual(fp.front_order(G, near)[0], 0)

    def test_tilted_desktops_are_hit_on_their_own_surface(self):
        tilted = [dict(DEFAULT_PLACEMENT), {**DEFAULT_PLACEMENT, 'curvature': 60}, dict(DEFAULT_PLACEMENT)]
        q = (T1[0] + 500, T1[1] + 300)
        h = fp.hit(G, tilted, fp.project(G, T1, tilted[1], *q))
        self.assertEqual(h[:2], ('desktop', 1))
        self.assertAlmostEqual(h[2][0], q[0], delta=0.05); self.assertAlmostEqual(h[2][1], q[1], delta=0.05)


class DragMathTests(unittest.TestCase):
    def test_the_pill_stays_under_the_pointer(self):
        # orbit.drag_target (mapping, reach, wrap and snap: test_orbit.py) keeps the pill under the dot inside the view.
        x, y, w, h = fp.pill_rect(T1)
        press = (x + w / 2, y + h / 2)
        for move in ((150, 0), (-220, -90), (60, 140)):
            with self.subTest(move=move):
                pointer = (press[0] + move[0], press[1] + move[1])
                p = {**DEFAULT_PLACEMENT, **dict(zip('xy', orbit.drag_target(G, (0, 0), press, pointer)))}
                seen = fp.project(G, T1, p, *press)
                self.assertLess(math.hypot(seen[0] - pointer[0], seen[1] - pointer[1]), 6)
        self.assertEqual(orbit.drag_target(G, (0, 0), press, press), (0, 0))
        self.assertEqual(PLACEMENT_BOUNDS['x'], (-orbit.X_LIMIT, orbit.X_LIMIT))

    def test_the_corner_follows_the_pointer_along_its_diagonal(self):
        w, h = T0[2], T0[3]
        for corner, (sx, sy) in {'tl': (-1, -1), 'tr': (1, -1), 'bl': (-1, 1), 'br': (1, 1)}.items():
            with self.subTest(corner):
                self.assertEqual(fp.resize_distance(100, corner, 0, 0, T0), 100)
                self.assertEqual(fp.resize_distance(100, corner, sx * w / 2, sy * h / 2, T0), 50)    # corner twice as far out
                self.assertEqual(fp.resize_distance(100, corner, -sx * w / 6, -sy * h / 6, T0), 150)  # inward: smaller
                self.assertEqual(fp.resize_distance(200, corner, sx * w / 4, sy * h / 4, T0), 100)  # from half size
        lo, hi = PLACEMENT_BOUNDS['distance']
        self.assertEqual(fp.resize_distance(100, 'br', 1e6, 1e6, T0), lo)
        self.assertEqual(fp.resize_distance(100, 'br', -1e6, -1e6, T0), hi)
        with self.assertRaises(ValueError): fp.resize_distance(100, 'middle', 0, 0, T0)


class HugeResizeMathTests(unittest.TestCase):
    """Distance's floor is 10 (ten times the size, well past the field of view): the drag maths and hit testing there."""
    def test_a_corner_drag_reaches_ten_times_and_clamps_there(self):
        w, h = T0[2], T0[3]
        self.assertEqual(PLACEMENT_BOUNDS['distance'], (10, 200))
        self.assertEqual(fp.resize_distance(100, 'br', 9 * w / 2, 9 * h / 2, T0), 10)  # the corner ten times as far out
        self.assertEqual(fp.resize_distance(100, 'tl', -4 * w / 2, -4 * h / 2, T0), 20)  # five times
        self.assertEqual(fp.resize_distance(10, 'br', -9 * w / 2, -9 * h / 2, T0), 100)  # and back
        self.assertEqual(fp.resize_distance(10, 'br', 1e6, 1e6, T0), 10)

    def test_past_the_view_the_drag_speeds_up_monotonically(self):
        gain = fp.RESIZE_OVERSHOOT_GAIN
        self.assertGreater(gain, 1)
        for point in ((0, 0), (G['w'], G['h']), (500, 300)): self.assertEqual(fp.resize_reach(G, point), point)
        self.assertEqual(fp.resize_reach(G, (G['w'] + 10, -10)), (G['w'] + 10 * gain, -10 * gain))
        xs = [fp.resize_reach(G, (x, 0))[0] for x in range(-G['w'] // 2, G['w'] * 3 // 2 + 1, 10)]
        self.assertTrue(all(b > a for a, b in zip(xs, xs[1:])))
        # Within the pointer's reach (half a view past each edge) the right-hand desktop reaches the floor.
        start = (T0[0] + T0[2], T0[1])
        a, b = fp.resize_reach(G, start), fp.resize_reach(G, (G['w'] * 1.5, start[1]))
        self.assertEqual(fp.resize_distance(100, 'tr', b[0] - a[0], b[1] - a[1], T0), PLACEMENT_BOUNDS['distance'][0])

    def test_hit_testing_on_a_huge_desktop(self):
        # XR-2 at ten times the size is nearest and covers the whole view: everywhere is on it, and the tile pixel found
        # projects back under the pointer, flat or curved.
        for extra in ({}, {'curvature': 120}, {'curvature': 60, 'y': 50}):
            with self.subTest(extra=extra):
                placements = [dict(DEFAULT_PLACEMENT), {**DEFAULT_PLACEMENT, 'distance': 10, **extra}, dict(DEFAULT_PLACEMENT)]
                self.assertEqual(fp.front_order(G, placements)[0], 1)
                for pointer in ((G['w'] / 2, G['h'] / 2), (300, 500), (1700, 700)):
                    h = fp.hit(G, placements, pointer)
                    self.assertEqual(h[:2], ('desktop', 1), pointer)
                    seen = fp.project(G, T1, placements[1], *h[2])
                    self.assertLess(math.hypot(seen[0] - pointer[0], seen[1] - pointer[1]), 0.5)
        # Its corners are far outside the view (where the pointer cannot go): only the keys or the panel shrink it.
        huge = {**DEFAULT_PLACEMENT, 'distance': 10}
        for corner in ((T1[0], T1[1]), (T1[0] + T1[2], T1[1] + T1[3])):
            seen = fp.project(G, T1, huge, *corner)
            self.assertTrue(seen is None or not -G['w'] / 2 <= seen[0] <= G['w'] * 1.5)


class PointerBase(unittest.TestCase):
    """A ViewControl with an OverviewPointer, driven like the sampler would (feed), on a fake clock."""
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.clock, self.store = Clock(), MemoryPlacements()
        self.focused = []
        self.control = view.ViewControl(view.ViewState(NAMES), 'run', 'session', self.focused.append,
                                        LayoutStore(Path(tmp.name) / 'l.json'), None, placements=self.store, clock=self.clock)
        self.addCleanup(self.control.join_focus, 2)
        self.addCleanup(self.control.stop_menu_binds)
        self.p = self.control.overview_pointer = fp.OverviewPointer(self.control, clock=self.clock)
        self.addCleanup(self.p.close)
        self.control.set_overview_geometry(G)
        self.cur = None

    def applied(self):
        return self.p.want[0]  # the sampler applied every request

    def at(self, x, y, advance=0.004, applied=None, ops=None):
        self.clock.now += advance
        self.cur = (x, y)
        self.p.feed((x, y), (0.0, 0.0), OUTPUTS, self.applied() if applied is None else applied)
        return frames(self.control.poll(), ops)

    def move(self, dx, dy, advance=0.004, ops=None):
        """Cursor motion in free mode (layout px), the hidden cursor staying parked."""
        self.clock.now += advance
        self.p.feed(self.cur, (float(dx), float(dy)), OUTPUTS, self.applied())
        return frames(self.control.poll(), ops)

    def move_canvas(self, dx, dy, ops=None):
        return self.move(dx / self.p.gain, dy / self.p.gain, ops=ops)

    def op(self, name, ops=None):
        return frames(self.control.on_message({'op': name, 'payload': {}}), ops)

    def free_at_top(self, lx=960):
        """Push the cursor against XR-1's top edge: free mode just above that spot."""
        self.at(X1 + 960, 300)
        return self.at(X1 + lx, 0)

    def on_pill(self, i=0):
        """Free mode from XR-1's bottom edge, then the dot onto desktop i's glass pill (under the desktop)."""
        self.at(X1 + 960, 540); self.at(X1 + 960, 1079)
        x, y, w, h = fp.pill_rect(G['tiles'][i])
        aim = fp.project(G, G['tiles'][i], self.control.state.placements[i], x + w / 2, y + h / 2)
        out = self.move_canvas(aim[0] - self.p.pointer[0], aim[1] - self.p.pointer[1])
        self.assertEqual(self.p.hover, ('pill', i))
        return out


class ModeTests(PointerBase):
    def test_inside_a_desktop_the_real_cursor_is_the_pointer(self):
        out = self.at(X1 + 960, 540)
        self.assertEqual(self.p.mode, 'desktop')
        self.assertEqual(self.p.want[1], 'away')  # no binds, cursor untouched: clicks go to windows
        self.assertAlmostEqual(self.p.pointer[0], T0[0] + T0[2] / 2, places=2)
        self.assertAlmostEqual(self.p.pointer[1], T0[1] + T0[3] / 2, places=2)
        self.assertEqual(last(out, 'overview_pointer'), {'visible': True, 'x': round(T0[0] + T0[2] / 2), 'y': round(T0[1] + T0[3] / 2),
                                                         'held': False})  # the dot there: the cursor itself is hidden
        self.assertTrue(last(out, 'move_handle')['visible'])  # the pills are up in the overview

    def test_the_edge_hands_over_to_the_free_pointer(self):
        out = self.free_at_top()
        self.assertEqual(self.p.mode, 'free')
        self.assertEqual(self.p.want[1:], ('free', (X1 + 960, 540)))  # binds, hidden cursor parked mid-monitor
        self.assertEqual(last(out, 'overview_pointer'), {'visible': True, 'x': round(T0[0] + T0[2] / 2), 'y': round(T0[1]), 'held': False})
        self.assertAlmostEqual(self.p.gain, GAIN)
        for edge in ((X1, 500), (X1 + 1919, 500), (X1 + 700, 1079)):  # every side
            self.setUp()
            self.at(X1 + 960, 540); self.at(*edge)
            self.assertEqual(self.p.mode, 'free', edge)

    def test_free_motion_moves_the_dot_anywhere_outside_the_desktops(self):
        self.free_at_top()
        out = self.move(0, -400)
        y = T0[1] - 400 * GAIN
        self.assertEqual(last(out, 'overview_pointer')['y'], round(y))
        self.assertEqual(self.p.mode, 'free')
        out = self.move(-10000, -10000)
        self.assertEqual(last(out, 'overview_pointer'), {'visible': True, 'x': -G['w'] // 2, 'y': -G['h'] // 2, 'held': False})

    def test_crossing_onto_the_next_monitor_goes_through_xr_space(self):
        self.at(X1 + 1900, 540)
        self.at(X2 + 6, 540)  # Hyprland lets the cursor onto XR-2; in the glasses there is a gap first
        self.assertEqual(self.p.mode, 'free')
        self.assertAlmostEqual(self.p.pointer[0], fp.project(G, T0, DEFAULT_PLACEMENT, T0[0] + 1919 * GAIN, 0)[0] + 7 * GAIN, places=2)
        out = self.move_canvas(T1[0] - self.p.pointer[0] + fp.ENTER_PX + 1, 0)
        self.assertEqual(self.p.mode, 'desktop')
        self.assertEqual(self.control.state.selected, 1)  # the desktop the pointer went into is selected
        self.assertEqual(last(out, 'view')['selected'], 1)
        kind, (x, y) = self.p.want[1], self.p.want[2]
        self.assertEqual(kind, 'desktop'); self.assertEqual(y, 540)
        self.assertTrue(X2 + fp.INSET_PX <= x <= X2 + 20)

    def test_back_into_a_desktop_needs_a_margin_so_the_modes_never_flap(self):
        self.free_at_top()
        self.move_canvas(0, fp.ENTER_PX - 1)
        self.assertEqual(self.p.mode, 'free')
        out = self.move_canvas(0, 2)
        self.assertEqual(self.p.mode, 'desktop')
        self.assertTrue(last(out, 'overview_pointer')['visible'])  # the dot stays: the cursor comes back hidden
        target = self.p.want[2]
        self.assertEqual(target[0], X1 + 960)
        self.assertGreaterEqual(target[1] - 0, fp.ENTER_PX / GAIN - 1)

    def test_samples_from_before_the_handback_landed_are_ignored(self):
        self.free_at_top()
        self.move_canvas(0, 20)
        want = self.p.want
        before = self.p.pointer
        self.at(X1 + 960, 540, applied=want[0] - 1)  # the hidden, parked cursor: not where the pointer is
        self.assertEqual(self.p.pointer, before)
        self.at(*want[2])
        self.assertAlmostEqual(self.p.pointer[1], before[1], delta=1)

    def test_a_keyboard_jump_to_another_desktop_follows_the_cursor(self):
        self.at(X1 + 960, 540)
        out = self.at(X3 + 960, 540)  # focus moved by a key: the cursor was warped there
        self.assertEqual((self.p.mode, self.p.desktop), ('desktop', 2))
        self.assertEqual(last(out, 'view')['selected'], 2)
        self.at(100, 100)  # the physical monitor: leave it alone
        self.assertEqual(self.p.mode, 'away'); self.assertIsNone(self.p.pointer)

    def test_a_cursor_off_the_desktops_is_left_alone_until_it_comes_in(self):
        out = self.at(100, 100)
        self.assertEqual(self.p.mode, 'away'); self.assertEqual(self.p.want[1], 'away')
        self.assertIsNone(last(out, 'overview_pointer'))
        self.at(X2 + 300, 300)
        self.assertEqual((self.p.mode, self.p.desktop), ('desktop', 1))

    def test_ctrl_alt_down_brings_the_cursor_into_the_selected_desktop(self):
        self.op('zoom_in'); self.at(100, 100)
        self.assertEqual(self.p.mode, 'off')
        self.op('zoom_out')
        self.at(100, 100); self.at(100, 101)
        self.assertEqual(self.p.mode, 'desktop')
        self.assertEqual(self.p.want[1:], ('desktop', (X1 + 960, 540)))
        self.at(X1 + 960, 540)
        self.assertFalse(self.p.summoned)

    def test_leaving_the_overview_gives_the_cursor_back(self):
        self.free_at_top()
        out = self.op('zoom_in') + self.at(X1 + 960, 540)
        self.assertEqual(self.p.mode, 'off'); self.assertEqual(self.p.want[1], 'off')
        self.assertFalse(last(out, 'overview_pointer')['visible'])
        self.assertFalse(last(out, 'move_handle')['visible'])
        self.op('zoom_out'); self.at(X1 + 960, 540)
        self.assertEqual(self.p.mode, 'desktop')

    def test_nothing_without_the_renderers_geometry(self):
        self.p.geometry = None
        out = self.at(X1 + 960, 0)
        self.assertEqual(self.p.mode, 'off'); self.assertEqual(out, [])


class ClickTests(PointerBase):
    def test_click_on_a_desktop_focuses_it_and_hands_the_cursor_back_there(self):
        self.at(X2 + 960, 540); self.at(X2 + 960, 0)  # XR-2 top edge: free, still on XR-2's top row
        self.control.state.selected = 0
        out = self.op('resize_press')
        self.assertEqual(self.p.mode, 'desktop')
        self.assertEqual(self.control.state.selected, 1)
        self.assertEqual(last(out, 'view')['selected'], 1)
        self.assertEqual(self.p.want[1:], ('desktop', (X2 + 960, fp.INSET_PX)))
        self.assertEqual(self.p.focus_clicks, 1)
        self.assertEqual(self.op('resize_release'), [])

    def test_click_in_empty_space_does_nothing(self):
        self.free_at_top()
        self.move(0, -300)
        out = self.op('resize_press') + self.op('resize_release')
        self.assertIsNone(self.p.drag); self.assertEqual(self.p.mode, 'free')
        self.assertFalse(any(f['op'] in ('placement', 'view') for f in out))

    def test_drag_the_pill_moves_the_desktop_live_and_saves_on_release(self):
        out = self.on_pill(0)
        self.assertEqual(last(out, 'move_handle'), {'visible': True, 'desktop': 0, 'hover': True, 'active': False})
        out = self.op('resize_press')
        self.assertTrue(last(out, 'overview_pointer')['held'])
        self.assertFalse(self.p.pills['active'])  # held, not moving yet: a release now would be a click
        out = self.move_canvas(0, -T0[3] * 1.3)  # lifted clear of the row (it may not overlap XR-2)...
        self.assertEqual(last(out, 'move_handle')['active'], True)
        out = self.move_canvas(T0[2] * 0.2, 0)   # ...then to the right
        p = self.control.state.placements[0]
        want = orbit.drag_target(G, (0, 0), self.p.drag['start'], self.p.pointer)
        self.assertEqual((p['x'], p['y']), want); self.assertGreater(p['x'], 0); self.assertGreater(p['y'], 0)
        self.assertEqual(frames_of(out, 'placement')['desktops'][0]['x'], want[0])
        self.assertEqual(self.store.saves, 0)  # debounced while dragging
        pill = fp.pill_rect(T0)  # the pill is still under the pointer
        centre = fp.project(G, T0, p, pill[0] + pill[2] / 2, pill[1] + pill[3] / 2)
        self.assertLess(math.hypot(centre[0] - self.p.pointer[0], centre[1] - self.p.pointer[1]), 8)
        self.op('resize_release')
        self.assertEqual((self.store.saved[0]['x'], self.store.saved[0]['y']), want)
        self.assertIsNone(self.p.drag)
        self.assertFalse(self.control.state.menu_open)  # a drag is not a click: the pill did not expand

    def test_pill_drag_respects_the_overlap_guard(self):
        self.on_pill(0); self.op('resize_press')
        self.move_canvas(T0[2] * 0.6, 0)  # straight into XR-2: stops just before it touches
        self.assertGreater(self.control.state.placements[0]['x'], 0)
        self.assertLess(self.control.state.placements[0]['x'], 9)  # the slots are 40 px (~0.9 degrees) apart
        self.assertEqual(orbit.overlap(orbit.footprint(G, T0, self.control.state.placements[0]), orbit.footprint(G, T1, FLAT[1])), 0)
        self.op('resize_release')

    def test_in_anchor_mode_a_pill_drag_reaches_behind_you_and_round_the_other_side(self):
        self.on_pill(2); self.op('resize_press')
        self.move_canvas(G['w'] * 3, 0)  # push the dot to the far right of its reach
        self.assertEqual(self.p.pointer[0], G['w'] * 1.5)
        x = self.control.state.placements[2]['x']
        self.assertGreater(x, 1500)  # all the way round to behind you (XR-3's slot is right of centre)
        for _ in range(40): self.move_canvas(-G['w'] / 10, 0)  # back the other way, round your right side
        back = self.control.state.placements[2]
        self.assertLess(abs(back['x']), 20)  # in front again, stopped beside XR-2 rather than passing through it
        self.assertEqual(orbit.overlap(orbit.footprint(G, G['tiles'][2], back), orbit.footprint(G, T1, FLAT[1])), 0)
        self.op('resize_release')
        self.assertEqual(self.store.saved[2]['x'], self.control.state.placements[2]['x'])

    def test_a_pill_far_round_you_lights_up_under_the_stretched_pointer_and_drags_on(self):
        from tests.native_spike.test_orbit import direction, pointer_for
        far = {**DEFAULT_PLACEMENT, 'x': 1200}  # XR-3 turned 120 degrees round to your right: out of the view
        self.control.state.placements = [dict(DEFAULT_PLACEMENT), dict(DEFAULT_PLACEMENT), far]
        self.at(X1 + 960, 540); self.at(X1 + 960, 1079)  # free mode
        x, y, w, h = fp.pill_rect(G['tiles'][2])
        aim = pointer_for(*direction(fp.place_point(G, G['tiles'][2], far, x + w / 2, y + h / 2)))
        out = self.move_canvas(aim[0] - self.p.pointer[0], aim[1] - self.p.pointer[1])
        self.assertEqual(self.p.hover, ('pill', 2))
        self.assertEqual(last(out, 'move_handle'), {'visible': True, 'desktop': 2, 'hover': True, 'active': False})
        self.op('resize_press')
        self.move_canvas(-40, 0)
        self.assertLess(self.control.state.placements[2]['x'], 1200)  # back toward the front
        self.op('resize_release')
        self.assertEqual(self.store.saved[2]['x'], self.control.state.placements[2]['x'])
        self.assertIsNone(fp.hit(G, self.control.state.placements, aim))  # follow / locked: no stretch, nothing there

    def test_a_mode_change_mid_drag_does_not_make_the_desktop_jump(self):
        self.on_pill(2); self.op('resize_press')
        self.move_canvas(G['w'] * 0.5, 0)
        before = self.control.state.placements[2]['x']
        self.op('mode_next')  # Ctrl+Alt+V: anchor -> follow
        self.assertNotEqual(self.control.state.mode, 'anchor')
        self.move_canvas(1, 0)
        self.assertLess(abs(self.control.state.placements[2]['x'] - before), 30)  # the reach it began with carries on
        self.op('resize_release')

    def test_a_cursor_on_a_desktop_turned_behind_you_never_breaks_the_pointer(self):
        self.control.state.placements = [dict(DEFAULT_PLACEMENT), {**DEFAULT_PLACEMENT, 'x': 1800}, {**DEFAULT_PLACEMENT, 'x': 800}]
        for cursor in ((X2 + 960, 540), (X2 + 960, 0), (X3 + 5, 300), (X3 + 1919, 300), (X1 + 960, 540), (X1 + 960, 1079)):
            out = self.at(*cursor)  # keyboard warps or hand motion onto them: no exception, every frame in range
            for f in out:
                if f['op'] == 'overview_pointer':
                    self.assertLessEqual(abs(f['payload']['x']), fp.MAX_POINTER_PX)
                    self.assertLessEqual(abs(f['payload']['y']), fp.MAX_POINTER_PX)
        self.move_canvas(G['w'] * 3, G['h'] * 3)
        self.assertEqual(self.p.pointer, (G['w'] * 1.5, G['h'] * 1.5))

    def test_in_follow_and_locked_modes_the_desktop_only_follows_the_dot(self):
        self.control.state.mode = 'locked'
        self.on_pill(2); self.op('resize_press')
        self.move_canvas(G['w'] * 3, 0)
        self.assertLess(self.control.state.placements[2]['x'], 450)  # the dot's own direction, not round behind you
        self.op('resize_release')

    def test_drag_a_corner_past_the_view_to_make_it_huge_and_back(self):
        self.free_at_top(lx=1919)
        self.move_canvas(4, -4)
        self.assertEqual(self.p.hover, ('corner', 0, 'tr'))
        start = self.p.pointer
        self.op('resize_press')
        self.move_canvas(G['w'] - start[0] + 60, 0)  # 60 px past the right edge: faster than 1:1
        linear = fp.resize_distance(100, 'tr', self.p.pointer[0] - start[0], 0, T0)
        self.assertLess(self.control.state.placements[0]['distance'], linear)
        out = self.move_canvas(G['w'] * 3, -G['h'] * 3)  # as far as the pointer goes: ten times the size
        self.assertEqual(self.p.pointer, (G['w'] * 1.5, -G['h'] / 2))
        self.assertEqual(self.control.state.placements[0]['distance'], 10)
        self.assertEqual(last(out, 'placement')['desktops'][0]['distance'], 10)
        self.move_canvas(start[0] - self.p.pointer[0], start[1] - self.p.pointer[1])  # back where it began: its old size
        self.assertEqual(self.control.state.placements[0]['distance'], 100)
        self.move_canvas(G['w'] * 3, -G['h'] * 3)
        self.op('resize_release')
        self.assertEqual(self.store.saved[0]['distance'], 10)  # saved like any size

    def test_drag_a_corner_from_outside_to_resize(self):
        out = self.free_at_top(lx=1919)  # the top-right corner's zone reaches outside the desktop
        self.assertEqual(last(out, 'resize_handle'), {'visible': True, 'desktop': 0, 'corner': 'tr', 'active': False})
        self.move_canvas(4, -4)
        self.assertEqual(self.p.hover, ('corner', 0, 'tr'))
        self.assertEqual(self.p.shown, {'visible': True, 'desktop': 0, 'corner': 'tr', 'active': False})
        self.assertTrue(last(self.op('resize_press'), 'resize_handle')['active'])
        out = self.move_canvas(T0[2] / 2, -T0[3] / 2)  # the corner twice as far from the centre: twice the size
        self.assertEqual(self.control.state.placements[0]['distance'], 50)
        self.assertEqual(last(out, 'placement')['desktops'][0]['distance'], 50)
        self.move_canvas(-T0[2] * 0.4, T0[3] * 0.4)
        size = self.control.state.placements[0]['distance']
        self.assertTrue(50 < size < 100)
        self.op('resize_release')
        self.assertEqual(self.store.saved[0]['distance'], size)
        self.assertEqual(self.p.mode, 'free')  # a drag never hands the cursor back by itself

    def test_a_lost_release_is_finished_by_the_next_press(self):
        self.on_pill(0); self.op('resize_press')
        self.move_canvas(T0[2] * 0.1, -T0[3] * 0.2)
        moved = dict(self.control.state.placements[0])
        self.assertNotEqual(moved, DEFAULT_PLACEMENT)
        self.op('resize_press')  # its release never came
        self.assertEqual(self.store.saved[0], moved)

    def test_in_desktop_mode_the_ops_do_nothing(self):
        self.at(X1 + 960, 540)
        self.assertEqual(self.op('resize_press'), [])
        self.assertIsNone(self.p.drag)


def frames_of(fs, op):
    return last(fs, op)


class GateTests(PointerBase):
    def test_on_while_a_placement_panel_is_open_and_a_drag_carries_on(self):
        # The glass pills replaced the head-locked menu card: the free pointer is how an open panel is clicked.
        self.on_pill(0); self.op('resize_press')
        self.move_canvas(T0[2] * 0.1, -T0[3] * 1.3)  # lifted clear of the row: no overlap guard in the way
        out = self.op('menu_toggle') + self.move_canvas(T0[2] * 0.3, 0)
        self.assertTrue(self.control.state.menu_open)
        self.assertEqual(self.p.mode, 'free'); self.assertEqual(self.p.want[1], 'free')
        self.assertIsNotNone(self.p.drag)
        want = orbit.drag_target(G, (0, 0), self.p.drag['start'], self.p.pointer)[0]
        self.assertGreater(want, 0)
        self.assertEqual(self.control.state.placements[0]['x'], want)
        self.assertEqual(last(out, 'placement_menu')['placements'][0]['x'], want)  # the open panel shows the drag
        self.assertTrue(last(out, 'overview_pointer')['visible'])
        self.op('resize_release')
        self.assertEqual(self.store.saved[0]['x'], want)
        self.assertTrue(self.control.state.menu_open)

    def test_off_in_typing_mode_and_a_drag_is_saved(self):
        self.on_pill(0); self.op('resize_press')
        self.move_canvas(T0[2] * 0.1, -T0[3] * 0.3)
        moved = dict(self.control.state.placements[0])
        out = self.op(view.TYPING_OP) + self.move_canvas(T0[2] * 0.3, 0)
        self.assertEqual(self.p.mode, 'off'); self.assertEqual(self.p.want[1], 'off')
        self.assertIsNone(self.p.drag)
        self.assertEqual(self.control.state.placements[0], moved); self.assertEqual(self.store.saved[0], moved)
        self.assertFalse(last(out, 'overview_pointer')['visible']); self.assertFalse(last(out, 'move_handle')['visible'])
        self.assertEqual(self.op('resize_press', ops=('placement',)), [])
        self.op(view.TYPING_OP)
        self.at(X1 + 960, 540)
        self.assertEqual(self.p.mode, 'desktop')

    def test_off_while_a_vr_video_plays(self):
        from tools.native_spike import video
        self.assertIn('resize_press', video.IGNORED_WHILE_PLAYING); self.assertIn('resize_release', video.IGNORED_WHILE_PLAYING)
        self.free_at_top()
        self.control.video_active = True
        out = self.move(0, -50)
        self.assertEqual(self.p.mode, 'off')
        self.assertFalse(last(out, 'overview_pointer')['visible']); self.assertFalse(last(out, 'move_handle')['visible'])
        self.control.video_active = False
        self.at(X1 + 960, 540)
        self.assertEqual(self.p.mode, 'desktop')

    def test_idle_hides_everything_and_a_click_only_wakes_it(self):
        self.on_pill(0)
        self.clock.now += view.OVERLAY_IDLE_CLOSE_S + 0.01
        out = frames(self.control.poll())
        self.assertFalse(last(out, 'overview_pointer')['visible'])
        self.assertFalse(last(out, 'move_handle')['visible'])
        out = self.op('resize_press')
        self.assertIsNone(self.p.drag)  # the hidden pointer grabbed nothing
        self.assertTrue(last(out, 'overview_pointer')['visible'])
        self.op('resize_release')
        self.assertFalse(self.control.state.menu_open)  # and clicked nothing

    def test_idle_mid_drag_ends_and_saves_it(self):
        self.on_pill(0); self.op('resize_press'); self.move_canvas(30, 0)
        moved = dict(self.control.state.placements[0])
        self.assertNotEqual(moved, DEFAULT_PLACEMENT)
        self.clock.now += view.OVERLAY_IDLE_CLOSE_S + 0.01
        self.control.poll()
        self.assertIsNone(self.p.drag); self.assertEqual(self.store.saved[0], moved)

    def test_a_config_reload_asks_the_sampler_to_redo_the_cursor_state(self):
        self.control.on_events(b'configreloaded>>\n')
        self.assertTrue(self.p.binds_dropped)

    def test_levels_stay_dim(self):
        import re
        src = (ROOT / 'spike/include/xr/view.hpp').read_text()
        levels = re.search(r'kFreePointerLevel = ([0-9.]+)f / 255\.f, kFreePointerHeldLevel = ([0-9.]+)f / 255\.f', src)
        self.assertLessEqual(float(levels.group(1)), 191); self.assertLessEqual(float(levels.group(2)), 191)


class LuaTests(unittest.TestCase):
    def test_grab_binds_hides_once_and_parks_in_one_eval(self):
        lua = fp.grab_pointer_lua(*BIND_ARGS, 3520, 540)
        self.assertTrue(lua.startswith(resize.install_resize_binds_lua(*BIND_ARGS)))
        self.assertIn('if xr_workspace_grab == nil then', lua)  # where it was is remembered once
        self.assertIn('pcall(hl.config, { cursor = { invisible = true } })', lua)
        self.assertTrue(lua.endswith(view.warp_cursor_lua(3520, 540)))
        self.assertLess(len(lua), 4096)
        with self.assertRaises(ValueError): fp.grab_pointer_lua(*BIND_ARGS, 3520.5, 540)

    def test_release_shows_the_cursor_where_the_pointer_is_not_where_it_was(self):
        lua = fp.release_pointer_lua(4500, 30)
        self.assertTrue(lua.startswith(view._remove_lua(resize.BIND_TABLE)))
        self.assertIn('invisible = s.invisible', lua); self.assertNotIn('s.home', lua)
        self.assertTrue(lua.endswith(view.warp_cursor_lua(4500, 30)))
        self.assertNotIn('cursor.move', fp.release_pointer_lua())

    def test_lua_is_fixed_and_guarded(self):
        for lua in (fp.grab_pointer_lua(*BIND_ARGS, 1, 2), fp.release_pointer_lua(1, 2)):
            self.assertNotIn('os.', lua); self.assertNotIn('io.', lua)
            self.assertNotIn('xr_workspace_mouse', lua)  # never the old menu mouse mode's tables (view.py legacy cleanup)
            self.assertEqual(lua.count('hl.dispatch('), lua.count('pcall(function() hl.dispatch('))

    def test_a_dead_runs_left_button_gives_the_cursor_back(self):
        self.assertEqual(xr_control.REMOVE_RESIZE_BINDS, resize.remove_resize_binds_lua())
        self.assertIn(resize.GRAB_LEAVE_LUA, xr_control.REMOVE_RESIZE_BINDS)  # visibility and position restored
        with mock.patch.object(xr_control, 'drop_stale_menu_binds') as drop:
            self.assertEqual(xr_control.main(['x', '/nonexistent/c.sock', 'run', 's', 'resize_press']), 1)
        drop.assert_called_once_with(code=xr_control.REMOVE_RESIZE_BINDS)

    def test_hyprland_client_sends_exactly_that_lua(self):
        from tools.native_spike import hyprland as h
        client = h.HyprlandClient('f' * 40 + '_1793506481_1554975112', Path('/nonexistent'))
        sent = []
        client._request = lambda wire: sent.append(wire) or b'ok'
        client.grab_pointer(*BIND_ARGS, 3520, 540); client.release_pointer(4500, 30); client.release_pointer()
        self.assertEqual(sent, ['/eval ' + fp.grab_pointer_lua(*BIND_ARGS, 3520, 540), '/eval ' + fp.release_pointer_lua(4500, 30),
                                '/eval ' + fp.release_pointer_lua()])
        self.assertFalse(hasattr(client, 'grab_cursor'))  # the pill-drag-only cursor grab is gone


class LuaRunTests(unittest.TestCase):
    """The generated Lua, run by a real Lua interpreter against a stub `hl` (no compositor): it loads, hides the cursor
    once, hands it back at the pointer, and the bind removal (also a dead run's) restores where free mode found it."""
    STUB = r'''
local log = {}
local state = { invisible = false, x = 100, y = 200, binds = 0 }
hl = { dsp = { cursor = {}, exec_cmd = function(c) return c end } }
function hl.get_config(k) return state.invisible end
function hl.get_cursor_pos() return { x = state.x, y = state.y } end
function hl.config(t) state.invisible = t.cursor.invisible end
function hl.dsp.cursor.move(p) return p end
function hl.dispatch(p) state.x, state.y = p.x, p.y end
function hl.bind(chord, cmd, opts)
  state.binds = state.binds + 1
  return setmetatable({ remove = function() state.binds = state.binds - 1 end }, { __tostring = function() return "HL.Keybind" end })
end
local function show(tag) print(tag, state.invisible, state.x, state.y, state.binds) end
'''

    def run_lua(self, body):
        import shutil, subprocess
        lua = shutil.which('lua') or shutil.which('luajit')
        if lua is None: self.skipTest('no Lua interpreter')
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / 'run.lua'
            script.write_text(self.STUB + body)
            done = subprocess.run([lua, str(script)], capture_output=True, text=True, timeout=10)
        self.assertEqual(done.returncode, 0, done.stderr)
        return [line.split('\t') for line in done.stdout.splitlines()]

    def test_grab_release_and_dead_run_cleanup(self):
        out = self.run_lua('\n'.join([
            fp.grab_pointer_lua(*BIND_ARGS, 3520, 540), 'show("grab")',
            'state.x, state.y = 3600, 700',
            fp.grab_pointer_lua(*BIND_ARGS, 3520, 540), 'show("regrab")',  # a reload: binds once, still hidden, parked
            fp.release_pointer_lua(4500, 30), 'show("release")',
            fp.grab_pointer_lua(*BIND_ARGS, 3520, 540), 'show("grab2")',
            resize.remove_resize_binds_lua(), 'show("dead")',
            resize.remove_resize_binds_lua(), 'show("again")']))
        self.assertEqual(out, [['grab', 'true', '3520', '540', '2'], ['regrab', 'true', '3520', '540', '2'],
                               ['release', 'false', '4500', '30', '0'], ['grab2', 'true', '3520', '540', '2'],
                               ['dead', 'false', '4500', '30', '0'],  # back where free mode found it, visible
                               ['again', 'false', '4500', '30', '0']])


class FakeClient:
    def __init__(self, cursor=(X1 + 960, 540)):
        self.cursor, self.calls, self.lock = cursor, [], threading.Lock()
        self.fail_grab = False
    def query(self, kind, argument=None, timeout=2.0):
        with self.lock: self.calls.append(kind)
        if kind == 'monitors': return MONITORS
        if kind == 'cursorpos':
            if self.cursor is None: raise TimeoutError('gone')
            return {'x': self.cursor[0], 'y': self.cursor[1]}
        raise ValueError(kind)
    def _call(self, *item):
        with self.lock: self.calls.append(item)
    def grab_pointer(self, *args):
        self._call('grab', args)
        if self.fail_grab: raise ValueError('rejected')
        self.cursor = args[-2:]  # parked
    def release_pointer(self, *args):
        self._call('release', args)
        if args: self.cursor = args
    def remove_resize_binds(self): self._call('remove')
    def warp_cursor(self, x, y):
        self._call('warp', (x, y)); self.cursor = (x, y)
    def count(self, kind):
        with self.lock: return sum(1 for c in self.calls if c == kind or (isinstance(c, tuple) and c[0] == kind))
    def args(self, kind):
        with self.lock: return [c[1] for c in self.calls if isinstance(c, tuple) and c[0] == kind]


class SamplerTests(unittest.TestCase):
    def setUp(self):
        self.control = view.ViewControl(view.ViewState(NAMES), 'run', 'session', None, None, None)
        self.addCleanup(self.control.stop_menu_binds)
        self.p = self.control.overview_pointer = fp.OverviewPointer(self.control); self.addCleanup(self.p.close)
        self.p.set_geometry(G)

    def wait(self, predicate, timeout=2.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.control.poll()
            if predicate(): return True
            time.sleep(0.003)
        return predicate()

    def start(self, client):
        sampler = fp.PointerSampler(client, NAMES, self.p, BIND_ARGS).start()
        self.addCleanup(sampler.stop)
        return sampler

    def test_samples_fast_grabs_at_the_edge_recentres_and_hands_back(self):
        client = FakeClient()
        sampler = self.start(client)
        self.assertTrue(self.wait(lambda: self.p.mode == 'desktop'))
        t0, n0 = time.monotonic(), sampler.samples
        time.sleep(0.2)
        self.assertGreater((sampler.samples - n0) / (time.monotonic() - t0), 120)  # well past the glasses' 120 Hz
        client.cursor = (X1 + 960, 0)
        self.assertTrue(self.wait(lambda: client.count('grab') == 1))
        self.assertEqual(client.args('grab'), [BIND_ARGS + (X1 + 960, 540)])
        self.assertTrue(self.wait(lambda: self.p.mode == 'free'))
        client.cursor = (X1 + 960, 540 - 250)  # the hidden cursor strays: put back, motion still counted
        self.assertTrue(self.wait(lambda: client.count('warp') >= 1))
        self.assertLess(self.p.pointer[1], T0[1] - 250 * GAIN + 1)
        client.cursor = (X1 + 960, 540 + 250 + 120)  # down, into the desktop
        self.assertTrue(self.wait(lambda: client.count('release') == 1))
        self.assertTrue(self.wait(lambda: self.p.mode == 'desktop'))
        sampler.stop()
        self.assertGreaterEqual(client.count('remove'), 1)  # a run that ever grabbed always cleans up at the end

    def test_no_reads_and_no_grab_outside_the_overview(self):
        for gate in ('zoomed', 'typing', 'video'):
            with self.subTest(gate):
                client = FakeClient(cursor=(X1 + 960, 0))
                self.control.state.zoomed = gate == 'zoomed'; self.control.state.typing = gate == 'typing'
                self.control.video_active = gate == 'video'
                sampler = self.start(client)
                time.sleep(0.12)
                sampler.stop()
                self.assertEqual(client.count('cursorpos'), 0); self.assertEqual(client.count('grab'), 0)
        self.control.state.zoomed = self.control.state.typing = self.control.video_active = False

    def test_an_open_placement_panel_does_not_stop_it(self):
        client = FakeClient(cursor=(X1 + 960, 0))
        self.control.state.open_menu(0)
        self.start(client)
        self.assertTrue(self.wait(lambda: client.count('grab') == 1))
        self.assertGreater(client.count('cursorpos'), 0)

    def test_leaving_the_overview_in_free_mode_removes_the_binds(self):
        client = FakeClient(cursor=(X1 + 960, 0))
        self.start(client)
        self.assertTrue(self.wait(lambda: client.count('grab') == 1))
        self.control.state.zoomed = True
        self.assertTrue(self.wait(lambda: client.count('remove') == 1))

    def test_read_errors_and_refused_grabs_are_survived(self):
        client = FakeClient(cursor=None)
        sampler = self.start(client)
        self.assertTrue(self.wait(lambda: sampler.errors >= 3))
        client.fail_grab = True
        client.cursor = (X1 + 960, 0)
        self.assertTrue(self.wait(lambda: client.count('grab') >= 2))  # retried
        self.assertFalse(sampler.grabbed)
        client.fail_grab = False
        self.assertTrue(self.wait(lambda: sampler.grabbed))

    def test_a_config_reload_redoes_the_grab(self):
        client = FakeClient(cursor=(X1 + 960, 0))
        self.start(client)
        self.assertTrue(self.wait(lambda: client.count('grab') == 1))
        self.control.on_events(b'configreloaded>>\n')
        self.assertTrue(self.wait(lambda: client.count('grab') == 2))
        self.control.on_events(b'configreloaded>>\n')  # at most once per 2 s: never a reload loop
        time.sleep(0.1)
        self.assertEqual(client.count('grab'), 2)


class WiringTests(unittest.TestCase):
    def test_the_supervisor_loop_wakes_on_a_sample_and_sends_the_dot(self):
        from tools.native_spike.supervisor import Supervisor
        control = view.ViewControl(view.ViewState(NAMES), 'run', 'session', None, None, None)
        self.addCleanup(control.stop_menu_binds)
        p = control.overview_pointer = fp.OverviewPointer(control); self.addCleanup(p.close)
        p.set_geometry(G)
        control.wake = p.wake_reader
        control.server = mock.Mock()
        p.feed((X1 + 960, 300), (0, 0), OUTPUTS, 0); control.poll()
        p.feed((X1 + 960, 0), (0, 0), OUTPUTS, p.want[0])
        child = mock.Mock(); child.poll.side_effect = [None] * 6 + [0] * 4
        a, b = socket.socketpair()
        with a, b, socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as dummy:
            control.server.socket = dummy
            Supervisor(child, lambda: 'done', control=control).run(a, interval=0.05)
            b.setblocking(False)
            sent = b.recv(65536)
        self.assertTrue(last(frames(sent), 'overview_pointer')['visible'])
        with self.assertRaises(BlockingIOError): p.wake_reader.recv(16)  # drained

    def test_view_controls_starts_the_sampler_and_cleans_up_before_the_view_binds(self):
        from tools.native_spike import input_lock, managed  # the run's pointer is the input lock's (test_input_lock.py)
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        client = mock.Mock(runtime_dir=Path(tmp.name))
        client.query.side_effect = lambda kind, *a, **k: (MONITORS if kind == 'monitors' else
                                                          {'x': X1 + 960, 'y': 0} if kind == 'cursorpos' else None)
        client.event_stream.side_effect = OSError('no events')
        with managed.view_controls(client, 'run', 'session', NAMES, store=LayoutStore(Path(tmp.name) / 'l.json')) as control:
            self.assertIsInstance(control.overview_pointer, fp.OverviewPointer)
            self.assertIs(control.wake, control.overview_pointer.wake_reader)
            control.set_overview_geometry(G)
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and not client.lock_pointer.called:
                control.poll(); time.sleep(0.01)
            self.assertTrue(client.lock_pointer.called)
            self.assertEqual(client.lock_pointer.call_args.args[:5], client.install_view_binds.call_args.args)
            self.assertEqual(client.lock_pointer.call_args.args[5:], (X1 + 960, input_lock.PARK_MARGIN_PX))  # held where it was
        client.remove_resize_binds.assert_called()
        names = [c[0] for c in client.method_calls]
        self.assertLess(max(i for i, n in enumerate(names) if n == 'remove_resize_binds'), names.index('remove_view_binds'))
        self.assertEqual(control.overview_pointer.wake_reader.fileno(), -1)  # closed with the run


if __name__ == '__main__':
    unittest.main()
