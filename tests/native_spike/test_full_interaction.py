"""Full interaction in the overview (anchor-no-click-maximize; Cody, 2026-11-03 2:49 PM): every window on every desktop
in the overview takes clicks, drags inside apps, typing, scrolling and right-clicks straight from the dot, as on a
normal screen. Only the XR handles keep their actions: the pills (and an open pill's panel), the level bars, the
corner resize handles and the pill drags that move a desktop, and each lies outside a desktop's content.

What the run controls, and what is checked here:
  * The mapping: the dot's hit point on a placed desktop (orbit round you, distance, curvature, roll: unproject) to that
    desktop's monitor pixel in Hyprland's logical layout (output scale: free_pointer.surface_to_layout), and back for
    the dot drawn over the cursor (layout_to_surface, project). Rolled, scaled, curved, moved and resized desktops.
  * Forwarding: the dot moving onto any desktop's content hands the real cursor to that desktop under the dot (desktop
    mode), where only the left button is bound, non-consuming, so the window gets every button, the wheel and drags;
    left presses there make no XR action, and a held button keeps the cursor on that desktop.
  * Focus and keyboard: the desktop under the dot becomes the selected one, and entering it focuses its monitor before
    the cursor warps (enter_desktop_lua), so the keyboard goes to that monitor's focused window and focus-follows-mouse
    picks the window under the dot. No key is bound by the pointer; a click into a window closes an open pill so its
    modal keys stop taking the typing.
  * Handles are not forwarded: on a pill, level bar, corner or edge the dot stays free (the run's grab), the press does
    its XR action and the cursor is never handed to a window; no handle covers a desktop's content.
  * Window drag (press and hold to lift a window onto another desktop) is off: holds and drags stay the app's.

Offline only: fake clocks, the free-pointer test rig, fake compositor clients and a stub `hl` run by a real Lua
interpreter. Nothing here runs in Hyprland or the glasses: whether Hyprland delivers the input as expected (focus on the
warp, non-consuming binds) is for the glasses run.
"""
import time
import unittest

from tools.native_spike import free_pointer as fp, input_lock as il, orbit, resize, unified_cursor as uc, view
from tools.native_spike.geometry import DEFAULT_PLACEMENT

from test_free_pointer import BIND_ARGS, G, MONITORS, NAMES, OUTPUTS, T0, T1, X1, X2, X3, frames, last
import test_input_lock as til
from test_unified_cursor import FakeUnifiedClient, MID1, UnifiedBase

T2 = G['tiles'][2]
# XR-2 at 150 % and XR-3 at 200 %: their logical size (what the cursor and Hyprland's layout use) is smaller.
SCALED_MONITORS = [MONITORS[0], MONITORS[1],
                   {'name': 'XR-2', 'x': 4480, 'y': 0, 'width': 1920, 'height': 1080, 'scale': 1.5},
                   {'name': 'XR-3', 'x': 5760, 'y': 0, 'width': 2560, 'height': 1440, 'scale': 2.0}]
SCALED = resize.outputs_from_monitors(SCALED_MONITORS, NAMES)
# Placements the mapping must survive (wire units: x / y tenths of a degree round you, distance %, curvature degrees,
# roll 1/100 degree: the run's levelling roll, which the free pointer adds to the drawn placement).
CASES = {
    'flat': {},
    'moved round you': {'x': 300, 'y': -120},
    'curved': {'curvature': 90},
    'resized bigger': {'distance': 50},
    'resized smaller': {'distance': 180},
    'rolled': {'roll': 1500},
    'rolled the other way, far': {'roll': -6000},
    'everything': {'x': -450, 'y': 200, 'curvature': 60, 'distance': 140, 'roll': 2500},
}
GRID = (0.01, 0.1, 0.33, 0.5, 0.71, 0.9, 0.99)  # shares of the width / height: corners, edges and middle


def placed(case):
    return {**DEFAULT_PLACEMENT, **CASES[case]}


def expected_layout(tile, o, q):
    """Where surface pixel q is on output o (logical px), unrounded."""
    return (o['x'] + (q[0] - tile[0]) / tile[2] * o['width'], o['y'] + (q[1] - tile[1]) / tile[3] * o['height'])


class MappingTests(unittest.TestCase):
    """The dot's hit point -> the monitor pixel of the window under it, for every placement case and output scale."""
    def test_surface_to_layout_is_the_share_of_the_logical_size_for_every_scale(self):
        for scale, o, logical in ((1.0, OUTPUTS[1], (1920, 1080)), (1.5, SCALED[1], (1280, 720)), (2.0, SCALED[2], (1280, 720))):
            with self.subTest(scale=scale):
                self.assertEqual((o['width'], o['height']), logical)
                for fx in GRID:
                    for fy in GRID:
                        q = (T1[0] + fx * T1[2], T1[1] + fy * T1[3])
                        got = fp.surface_to_layout(T1, o, q)
                        want = expected_layout(T1, o, q)
                        self.assertLessEqual(abs(got[0] - want[0]), 0.5 + 1e-9); self.assertLessEqual(abs(got[1] - want[1]), 0.5 + 1e-9)
                        back = fp.layout_to_surface(T1, o, got[0] - o['x'], got[1] - o['y'])  # the dot over the cursor
                        self.assertLessEqual(abs(back[0] - q[0]), 0.5 * T1[2] / o['width'] + 1e-9)
                        self.assertLessEqual(abs(back[1] - q[1]), 0.5 * T1[3] / o['height'] + 1e-9)

    def test_the_cursor_always_lands_inside_the_monitor(self):
        for o in (OUTPUTS[1], SCALED[1], SCALED[2]):
            for q in ((T1[0], T1[1]), (T1[0] + T1[2], T1[1] + T1[3]), (T1[0] - 50, T1[1] + 9999)):
                x, y = fp.surface_to_layout(T1, o, q)
                self.assertTrue(o['x'] + fp.INSET_PX <= x <= o['x'] + o['width'] - 1 - fp.INSET_PX)
                self.assertTrue(o['y'] + fp.INSET_PX <= y <= o['y'] + o['height'] - 1 - fp.INSET_PX)

    def test_the_dot_maps_onto_the_window_pixel_under_it_on_rolled_scaled_curved_desktops(self):
        g = dict(G, tiles=[T1])  # one desktop: nothing in front of it
        for case in CASES:
            p = placed(case)
            for o in (OUTPUTS[1], SCALED[1], SCALED[2]):
                for fx in GRID:
                    for fy in GRID:
                        q = (T1[0] + fx * T1[2], T1[1] + fy * T1[3])
                        with self.subTest(case=case, scale=round(1920 / o['width'], 2), q=(fx, fy)):
                            dot = fp.project(g, T1, p, *q)  # where the dot is drawn on that pixel of the desktop
                            h = fp.hit(g, [p], dot)
                            self.assertEqual(h[:2], ('desktop', 0))  # content, even right in a corner
                            self.assertAlmostEqual(h[2][0], q[0], delta=0.1); self.assertAlmostEqual(h[2][1], q[1], delta=0.1)
                            got = fp.surface_to_layout(T1, o, h[2])  # the cursor goes there: the window under the dot
                            want = expected_layout(T1, o, q)
                            self.assertLessEqual(abs(got[0] - want[0]), 0.6); self.assertLessEqual(abs(got[1] - want[1]), 0.6)
                            seen = fp.layout_to_surface(T1, o, got[0] - o['x'], got[1] - o['y'])  # and the dot drawn for it
                            h2 = fp.hit(g, [p], fp.project(g, T1, p, *seen))
                            self.assertEqual(h2[:2], ('desktop', 0))
                            self.assertAlmostEqual(h2[2][0], seen[0], delta=0.1); self.assertAlmostEqual(h2[2][1], seen[1], delta=0.1)


class Base(UnifiedBase):
    def dot_to(self, point):
        return self.move_canvas(point[0] - self.p.pointer[0], point[1] - self.p.pointer[1])


class ScaledBase(Base):
    """The unified pointer fed SCALED outputs (XR-2 150 %, XR-3 200 %)."""
    OUTS = SCALED

    def at(self, x, y, advance=0.004, applied=None, ops=None):
        self.clock.now += advance
        self.cur = (x, y)
        self.p.feed((x, y), (0.0, 0.0), self.OUTS, self.applied() if applied is None else applied)
        return frames(self.control.poll(), ops)

    def move(self, dx, dy, advance=0.004, ops=None):
        self.clock.now += advance
        self.p.feed(self.cur, (float(dx), float(dy)), self.OUTS, self.applied())
        return frames(self.control.poll(), ops)

    def aim_drawn(self, i, q):
        """The free dot onto desktop i's tile pixel q as drawn (with its roll)."""
        aim = fp.project(G, G['tiles'][i], self.p._placement(i), *q)
        return self.move_canvas(aim[0] - self.p.pointer[0], aim[1] - self.p.pointer[1])


class PointerMappingTests(ScaledBase):
    def test_entering_a_rolled_curved_scaled_desktop_puts_the_cursor_under_the_dot_and_keeps_the_dot_there(self):
        for i, (case, q_share) in enumerate((('everything', (0.3, 0.7)), ('rolled', (0.8, 0.2))), start=1):
            with self.subTest(desktop=i, case=case):
                self.setUp()
                self.control.state.placements[i] = {k: v for k, v in placed(case).items() if k != 'roll'}
                self.p.rolls[i] = placed(case).get('roll', 0)
                tile, o = G['tiles'][i], self.OUTS[i]
                q = (tile[0] + q_share[0] * tile[2], tile[1] + q_share[1] * tile[3])
                self.free()
                self.aim_drawn(i, q)
                dot = self.p.pointer
                self.assertEqual((self.p.mode, self.p.desktop, self.control.state.selected), ('desktop', i, i))
                _, kind, point = self.p.want
                self.assertEqual(kind, 'desktop')
                want = expected_layout(tile, o, q)
                self.assertLessEqual(abs(point[0] - want[0]), 0.6); self.assertLessEqual(abs(point[1] - want[1]), 0.6)
                self.at(*point)  # the sampler's warp landed: from here the cursor drives the dot
                self.assertEqual(self.p.mode, 'desktop')
                tol = 0.75 * max(tile[2] / o['width'], tile[3] / o['height']) * 100 / self.p._placement(i)['distance'] * 1.5
                self.assertLessEqual(abs(self.p.pointer[0] - dot[0]), tol); self.assertLessEqual(abs(self.p.pointer[1] - dot[1]), tol)
                lx, ly = 0.6 * o['width'], 0.4 * o['height']  # the hand moves the cursor on that monitor
                self.at(o['x'] + lx, o['y'] + ly)
                h = self.p._hit()  # what the dot is drawn on now: that very pixel of the desktop
                self.assertEqual(h[:2], ('desktop', i))
                seen = fp.layout_to_surface(tile, o, lx, ly)
                self.assertAlmostEqual(h[2][0], seen[0], delta=0.1); self.assertAlmostEqual(h[2][1], seen[1], delta=0.1)


class ForwardingTests(Base):
    def test_every_desktop_takes_the_real_cursor_when_the_dot_moves_onto_it(self):
        for i in (1, 2, 0):
            with self.subTest(desktop=i):
                self.setUp()
                self.control.state.selected = 0
                self.free()
                tile = G['tiles'][i]
                out = self.aim(i, (tile[0] + tile[2] * 0.4, tile[1] + tile[3] * 0.6))
                self.assertEqual((self.p.mode, self.p.desktop, self.control.state.selected), ('desktop', i, i))
                _, kind, point = self.p.want
                self.assertEqual((kind, self.p._at(point)[0]), ('desktop', i))  # the cursor goes onto that monitor
                if i: self.assertEqual(last(out, 'view')['selected'], i)
                self.assertFalse(self.control.state.zoomed)

    def test_left_clicks_and_drags_inside_a_window_make_no_xr_action(self):
        self.free()
        self.aim(2, centre(T2))
        self.at(*self.p.want[2])
        before = [dict(p) for p in self.control.state.placements]
        for _ in range(3):  # clicks
            out = self.op('resize_press') + self.op('resize_release')
            self.assertFalse(any(f['op'] in ('view', 'placement', 'placement_menu') for f in out))
        self.op('resize_press')  # a drag inside the app: across the window, past its edge, and held still a while
        for x, y in ((X3 + 400, 300), (X3 + 1500, 900), (X3 + 1919, 900), (X3 + 1950, 900)):
            self.at(x, y)
            self.assertEqual((self.p.mode, self.p.desktop), ('desktop', 2))  # never free while held
        self.assertEqual(self.p._at(self.p.want[2])[0], 2)  # pulled back onto XR-3, not handed to another monitor
        self.clock.now += fp.HOLD_S * 4
        self.control.poll()
        self.op('resize_release')
        self.assertEqual((self.p.mode, self.p.window_lifts), ('desktop', 0))
        self.assertEqual(self.control.state.placements, before)
        self.assertEqual((self.control.state.zoomed, self.control.state.selected), (False, 2))

    def test_a_held_button_never_carries_the_cursor_to_another_desktop(self):
        self.free()
        self.aim(1, centre(T1))
        self.at(*self.p.want[2])
        self.op('resize_press')
        self.at(X2 + 1919, 500); self.at(X3 + 30, 500)  # Hyprland let the drag onto XR-3
        self.assertEqual((self.p.mode, self.p.desktop, self.control.state.selected), ('desktop', 1, 1))
        self.assertEqual(self.p._at(self.p.want[2])[0], 1)
        self.op('resize_release')

    def test_ctrl_alt_up_maximizes_the_desktop_under_the_dot(self):
        self.free()
        self.aim(2, centre(T2))
        out = self.op('zoom_in')
        self.assertEqual((last(out, 'view')['selected'], last(out, 'view')['zoomed']), (2, True))


class FocusAndKeyboardTests(Base):
    def test_a_click_into_a_window_closes_an_open_pill_and_releases_its_keys(self):
        calls = []
        self.control.menu_binds = calls.append
        self.control.pill_click(0)  # XR-1's pill open: arrows, Tab, R, U, N, Delete, Esc are its keys now
        self.free()
        self.aim(1, centre(T1))
        self.at(*self.p.want[2])
        self.assertTrue(self.control.state.menu_open)  # moving over a window leaves it open
        out = self.op('resize_press')  # the click goes to the window (non-consuming) and closes the pill
        self.assertFalse(self.control.state.menu_open)
        self.assertFalse(last(out, 'placement_menu')['open'])
        self.op('resize_release')
        for t in self.control._menu_threads: t.join(2)
        self.assertEqual(calls, [True, False])  # the modal keys installed, then removed: typing goes to the window
        self.assertEqual(self.p.mode, 'desktop')

    def test_the_selected_desktop_follows_the_dot_for_the_keys_that_act_on_it(self):
        sent = []

        class Sender:
            def request(self, op, name): sent.append((op, name))
        self.control.sender = Sender()
        self.free()
        self.aim(2, centre(T2))
        self.op('send_glasses')  # Ctrl+Alt+G: the focused window to the selected desktop
        self.assertEqual(sent, [('send_glasses', 'XR-3')])


class LuaTests(unittest.TestCase):
    """The compositor side of entering a desktop, run against the stub `hl` (test_input_lock.LuaRunTests)."""
    STUB, run_lua, lock = til.LuaRunTests.STUB, til.LuaRunTests.run_lua, til.LuaRunTests.lock
    ORDER = r'''
local plain_bind = hl.bind
local nc = {}
hl.bind = function(chord, action, opts)
  local key = chord .. ((opts and opts.release) and "^" or "")
  nc[key] = opts and opts.non_consuming == true or false
  return plain_bind(chord, action, opts)
end
local order = {}
hl.dsp.focus = function(t) return { focus = t.monitor } end
local plain_dispatch = hl.dispatch
hl.dispatch = function(p)
  if p.focus then order[#order + 1] = "focus:" .. p.focus else order[#order + 1] = "move:" .. p.x .. "," .. p.y plain_dispatch(p) end
end
local function bound()
  local keys = {}
  for k in pairs(state.binds) do keys[#keys + 1] = k .. "=" .. tostring(nc[k]) end
  table.sort(keys)
  return table.concat(keys, " ")
end
'''

    def test_entering_focuses_the_monitor_then_puts_the_cursor_under_the_dot(self):
        out = self.run_lua('\n'.join([
            self.ORDER, self.lock(3520, 540), 'order = {}',
            uc.enter_desktop_lua(*BIND_ARGS, 6100, 700, monitor='XR-3'), 'show("desktop")',
            'print("order", table.concat(order, " "))', 'print("bound", bound())']))
        self.assertEqual(out[0], ['desktop', 'false', '6100', '700', '2', '300', '2'])  # shown, scroll delay given back
        self.assertEqual(out[1], ['order', 'focus:XR-3 move:6100,700'])  # the keyboard's monitor first, then the cursor
        self.assertEqual(out[2], ['bound', 'mouse:272=true mouse:272^=true'])  # left button only, non-consuming

    def test_over_a_window_no_other_button_wheel_or_key_is_bound(self):
        lua = uc.enter_desktop_lua(*BIND_ARGS, 6100, 700, monitor='XR-3')
        for chord in il.OTHER_BUTTONS + il.WHEEL: self.assertNotIn(f'"{chord}"', lua)  # right, middle, side, wheel: the window's
        self.assertEqual(lua.count('hl.bind'), 2)
        self.assertEqual(lua.count('non_consuming = true'), 2)
        free = il.lock_pointer_lua(*BIND_ARGS, 3520, 540)  # off the desktops (handles, empty space) they are the run's
        for chord in il.OTHER_BUTTONS + il.WHEEL: self.assertIn(f'"{chord}"', free)

    def test_no_focus_without_a_point_and_only_managed_outputs(self):
        self.assertNotIn('focus', uc.enter_desktop_lua(*BIND_ARGS, monitor='XR-2'))  # the cursor already there: left alone
        self.assertNotIn('focus', uc.enter_desktop_lua(*BIND_ARGS, 4000, 500))
        for bad in ('DP-1', 'XR-1"); os.exit(', 3):
            with self.subTest(bad=bad), self.assertRaises(ValueError): uc.enter_desktop_lua(*BIND_ARGS, 4000, 500, monitor=bad)


class HandleTests(Base):
    """The XR handles keep their actions and are never forwarded to a window."""
    def assert_not_forwarded(self, serial, selected):
        self.assertEqual(self.p.mode, 'free')  # the run's grab: the window under nothing gets the press
        self.assertEqual(self.p.want[0], serial)  # the cursor stayed parked: never warped onto a window
        self.assertEqual(self.p.want[1], 'free')
        self.assertEqual(self.control.state.selected, selected)

    def test_a_pill_click_opens_it_and_a_pill_drag_moves_the_desktop(self):
        self.free()
        serial = self.p.want[0]
        x, y, w, h = fp.pill_rect(T1)
        self.dot_to(fp.project_glass(G, T1, DEFAULT_PLACEMENT, x + w / 2, y + h / 2))
        self.assertEqual(self.p.hover, ('pill', 1))
        self.op('resize_press'); self.op('resize_release')
        self.assertEqual((self.control.state.menu_open, self.control.state.menu_desktop), (True, 1))
        self.assert_not_forwarded(serial, 0)
        self.op('resize_press'); self.move_canvas(0, 40); self.op('resize_release')  # the monitor-move drag
        self.assertNotEqual(self.control.state.placements[1]['y'], 0)
        self.assert_not_forwarded(serial, 0)

    def test_a_level_bar_segment_turns_its_desktop(self):
        self.free()
        serial = self.p.want[0]
        bx, by, bw, bh = fp.level_bar_rect(T1)
        self.dot_to(fp.project(G, T1, DEFAULT_PLACEMENT, bx + bw * 5 / 6, by + bh / 2))  # its right segment
        self.assertEqual(self.p.hover, ('roll', 1, 'cw'))
        self.op('resize_press'); self.op('resize_release')
        self.assertNotEqual(self.p.rolls[1], 0)
        self.assert_not_forwarded(serial, 0)

    def test_an_edge_handle_resizes_its_desktop(self):
        self.free()
        serial = self.p.want[0]
        self.dot_to(fp.project(G, T1, DEFAULT_PLACEMENT, T1[0] + T1[2] + 4, T1[1] + T1[3] / 2))  # just right of XR-2's right edge
        self.assertEqual(self.p.hover, ('edge', 1, 'r'))
        self.op('resize_press'); self.move_canvas(30, 0); self.op('resize_release')
        self.assertLess(self.control.state.placements[1]['distance'], 100)
        self.assert_not_forwarded(serial, 0)

    def test_a_corner_cap_resizes_its_desktop(self):
        self.free()
        serial = self.p.want[0]
        self.dot_to(fp.project(G, T1, DEFAULT_PLACEMENT, T1[0] + T1[2] + 5, T1[1] + T1[3] + 5))  # outside XR-2's bottom right
        self.assertEqual(self.p.hover, ('corner', 1, 'br'))
        self.op('resize_press'); self.move_canvas(25, 25); self.op('resize_release')
        self.assertLess(self.control.state.placements[1]['distance'], 100)
        self.assert_not_forwarded(serial, 0)

    def test_no_handle_covers_a_desktops_content_and_every_handle_lies_outside_it(self):
        # Every desktop, placed and rolled, with its level bar showing: a dense sweep over and round each one.
        self.control.state.placements = [{**DEFAULT_PLACEMENT, 'x': -40, 'curvature': 60},
                                         {**DEFAULT_PLACEMENT, 'y': 80, 'distance': 140},
                                         {**DEFAULT_PLACEMENT, 'x': 120, 'distance': 70}]
        self.p.rolls[:3] = [1200, 0, -2500]
        drawn = self.p._rolled()
        seen = set()
        for i, tile in enumerate(G['tiles']):
            tx, ty, tw, th = tile
            for fx in [k / 40 for k in range(-4, 45)]:
                for fy in [k / 24 for k in range(-6, 31)]:
                    q = (tx + fx * tw, ty + fy * th)
                    pointer = fp.project(G, tile, drawn[i], *q)
                    h = fp.hit(G, drawn, pointer, icons=self.p._icons())
                    if h is None: continue
                    own = fp.unproject(G, G['tiles'][h[1]], drawn[h[1]], pointer)
                    inside = own is not None and fp.inside_by(G['tiles'][h[1]], own) >= fp.ENTER_PX
                    seen.add(h[0])
                    if h[0] == 'desktop': continue
                    with self.subTest(desktop=i, q=(fx, fy), hit=h[:3]):
                        self.assertFalse(inside)  # a handle is never on its desktop's content (the border's edge sliver at most)
                    if 0 < fx < 1 and 0 < fy < 1 and h[1] == i:
                        self.fail(f'{h} covers desktop {i} at {(fx, fy)}')
        self.assertEqual(seen, {'desktop', 'pill', 'corner', 'edge', 'roll'})


class WindowDragOffTests(Base):
    def test_it_is_off_in_the_run(self):
        self.assertFalse(fp.OverviewPointer.window_drag)
        self.assertFalse(self.p.window_drag)

    def test_holding_still_on_a_window_never_lifts_it(self):
        self.free()
        self.aim(1, centre(T1))
        self.at(*self.p.want[2])
        self.op('resize_press')  # e.g. a long press on a button, or the start of a slow selection
        for _ in range(5):
            self.clock.now += fp.HOLD_S
            self.control.poll()
        self.assertEqual((self.p.mode, self.p.want[1], self.p.window_lifts), ('desktop', 'desktop', 0))
        self.at(X2 + 1200, 700)  # then drags: the app's
        self.assertEqual((self.p.mode, self.p.window_lifts), ('desktop', 0))
        self.op('resize_release')
        self.assertIsNone(self.p.drag)

    def test_a_press_and_drag_on_content_the_dot_is_free_over_lifts_nothing(self):
        self.free()
        dot = fp.project(G, T1, DEFAULT_PLACEMENT, T1[0] + T1[2] * 0.3, T1[1] + T1[3] / 2)
        self.op('help')  # next to the help card the dot stays free over content (HELP_CLEAR_PX)
        plane = orbit.plane_at(G, orbit.pointer_ray(G, dot))
        self.control.set_help_geometry({'card': (plane[0] + 1, plane[1] - 60, 300, 120)})
        self.dot_to(dot)
        self.assertEqual((self.p.mode, self.p.hover[:2]), ('free', ('desktop', 1)))
        self.op('resize_press')
        self.move_canvas(fp.WINDOW_DRAG_PX * 3, 0)
        self.clock.now += fp.HOLD_S * 2
        self.control.poll()
        self.assertEqual(self.p.window_lifts, 0)
        self.op('resize_release')
        self.assertEqual(self.p.window_lifts, 0)


class SamplerTests(unittest.TestCase):
    """The run's wiring end to end: ViewControl, UnifiedPointer and the real UnifiedSampler thread against a fake
    compositor client."""
    def setUp(self):
        self.focused = []
        self.control = view.ViewControl(view.ViewState(NAMES), 'run', 'session', self.focused.append, None, None)
        self.addCleanup(self.control.join_focus, 2)
        self.addCleanup(self.control.stop_menu_binds)
        self.p = self.control.overview_pointer = uc.UnifiedPointer(self.control); self.addCleanup(self.p.close)
        self.p.set_geometry(G)

    def wait(self, predicate, timeout=2.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.control.poll()
            if predicate(): return True
            time.sleep(0.003)
        return predicate()

    def test_each_desktop_the_dot_reaches_gets_its_monitor_focus_and_the_cursor_under_the_dot(self):
        client = FakeUnifiedClient(cursor=MID1)
        sampler = uc.UnifiedSampler(client, NAMES, self.p, BIND_ARGS).start(); self.addCleanup(sampler.stop)
        self.assertTrue(self.wait(lambda: sampler.bound and self.p.mode == 'desktop'))
        for i, edge in ((2, (X1 + 1919, 540)), (1, (X3, 540))):
            client.cursor = edge  # off the current desktop's edge: free
            self.assertTrue(self.wait(lambda: sampler.grabbed and self.p.mode == 'free'))
            tile = G['tiles'][i]
            q = (tile[0] + tile[2] * 0.3, tile[1] + tile[3] * 0.6)
            entries = client.count('enter')
            dot = fp.project(G, tile, DEFAULT_PLACEMENT, *q)
            self.p.pointer = (dot[0] - self.p.gain, dot[1])  # one hand pixel short of it: the motion below lands it there
            self.p.feed(client.cursor, (1.0, 0.0), OUTPUTS, sampler.applied)
            self.assertTrue(self.wait(lambda: client.count('enter') == entries + 1 and sampler.bound and not sampler.grabbed))
            at = client.args('enter')[-1][5:]
            self.assertEqual(client.focused[-1], NAMES[i])
            want = expected_layout(tile, OUTPUTS[i], q)
            self.assertLessEqual(abs(at[0] - want[0]), 0.6); self.assertLessEqual(abs(at[1] - want[1]), 0.6)
            self.assertEqual(self.control.state.selected, i)
            client.cursor = at
            time.sleep(0.03); self.control.poll()
            self.assertEqual((self.p.mode, self.p.desktop), ('desktop', i))
        self.assertEqual(client.focused, [None, 'XR-3', 'XR-2'])
        self.assertEqual(client.count('release'), 0)  # still the overview
        self.control.join_focus(2)
        self.assertEqual(self.focused, [])  # no separate focus call (it would warp the cursor off the dot)


def centre(tile):
    return (tile[0] + tile[2] / 2, tile[1] + tile[3] / 2)


if __name__ == '__main__':
    unittest.main()
