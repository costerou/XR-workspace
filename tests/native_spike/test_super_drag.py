"""Super drags through the dot (anchor-no-click-maximize; Cody, 2026-11-03 2:52 PM): Hyprland's own window move and resize,
Omarchy's `SUPER + mouse:272` / `SUPER + mouse:273` (checked with `hyprctl binds -j` on his Hyprland 0.56.2, efb5099: `__lua`
binds from default/hypr/bindings/tiling.lua), work in the overview, including dragging a window from one XR monitor and
dropping it on another. The drag itself stays Hyprland's. What the run does, and what is checked here:

  * Seeing it: non-consuming binds on the same chords (view.install_drag_binds_lua), with the buttons' release on
    ignore_mods binds so Super may be let go first. Hyprland runs every bind matching a chord, so the user's still fire.
  * Cross-monitor mapping: at a desktop's edge the dot goes on through space with nothing grabbed (carry mode), so the
    button stays Hyprland's; onto another desktop's content the cursor warps to the pixel under the dot, through the same
    mapping as any entry (rolled, scaled, curved, placed). In Hyprland's source a warp during a drag moves the window
    (moveCursor -> mouseMoveUnified -> moveMouse: start position + cursor delta) and its middle's monitor takes it.
  * Super suppresses the XR actions: in carry mode handles do nothing, no focus call is made, the view frame carries no
    `cut` or `center`, and maximized a push past an edge does not switch desktops. With the dot off the desktops a 1 ms
    timer ends the drag Hyprland began on whatever was under the parked cursor.
  * Super + right-drag resize: the window keeps the cursor: it stays on that desktop, pulled back, and never goes free.

Offline only: fake clocks, fake compositor clients and a stub `hl` run by a real Lua interpreter. How it feels, and that
Hyprland does what its source says, is for the glasses run. No compositor, renderer binary or glasses here.
"""
import json
import time
import unittest

from tools.native_spike import free_pointer as fp, ipc, resize, unified_cursor as uc, video, view, xr_control
from tools.native_spike.geometry import DEFAULT_PLACEMENT

from test_free_pointer import BIND_ARGS, G, MONITORS, NAMES, OUTPUTS, T0, T1, X1, X2, X3, frames, last
import test_input_lock as til
from test_unified_cursor import FakeUnifiedClient, MID1, UnifiedBase

T2 = G['tiles'][2]


def centre(tile):
    return (tile[0] + tile[2] / 2, tile[1] + tile[3] / 2)


class Base(UnifiedBase):
    def dot_to(self, point):
        return self.move_canvas(point[0] - self.p.pointer[0], point[1] - self.p.pointer[1])

    def onto(self, i, q=None):
        tile = G['tiles'][i]
        return self.dot_to(fp.project(G, tile, self.p._placement(i), *(q or centre(tile))))

    def super_op(self, op):
        return frames(self.control.on_message({'op': op, 'payload': {}}))

    def carrying(self, cursor=(X1 + 1919, 500)):
        """A Super drag on XR-1 taken to its right edge: the dot past it, the cursor waiting just inside."""
        self.at(X1 + 1500, 500)
        self.super_op('super_left_press')
        self.at(X1 + 1800, 500)
        out = self.at(*cursor)
        self.assertEqual((self.p.mode, self.p.want[1]), ('carry', 'carry'))
        self.cur = self.p.want[2]  # the sampler put it there
        return out


class CarryTests(Base):
    def test_inside_a_desktop_a_super_drag_is_just_the_cursor_moving(self):
        self.at(X1 + 600, 400)
        serial = self.p.want[0]
        out = self.super_op('super_left_press')
        self.assertEqual(self.p.super_drag, 'move')
        self.assertEqual((last(out, 'overview_pointer')['held'], last(out, 'overview_pointer')['look']), (True, 'move'))
        self.at(X1 + 900, 500)
        self.assertEqual((self.p.mode, self.p.want[0]), ('desktop', serial))  # nothing asked of the compositor
        self.assertEqual(self.super_op('super_release') and self.p.super_drag, None)
        self.assertFalse(self.p.sent['held']); self.assertNotIn('look', self.p.sent)

    def test_at_an_edge_the_dot_goes_on_and_nothing_is_grabbed(self):
        before = None
        self.at(X1 + 1500, 500)
        self.super_op('super_left_press')
        self.at(X1 + 1900, 500)
        before = self.p.pointer
        self.at(X1 + 1919, 500)
        self.assertEqual((self.p.mode, self.p.want[1]), ('carry', 'carry'))  # not 'free': the left button stays Hyprland's
        self.assertEqual(self.p.want[2], (X1 + 1919 - uc.CARRY_INSET_PX, 500))  # the cursor, and the window, wait just inside
        past = fp.project(G, T0, DEFAULT_PLACEMENT, T0[0] + T0[2] + fp.EXIT_PX * T0[2] / 1920, T0[1] + 500 / 1080 * T0[3])
        self.assertAlmostEqual(self.p.pointer[0], past[0], places=2); self.assertAlmostEqual(self.p.pointer[1], past[1], places=2)
        self.assertLess(abs(self.p.pointer[0] - before[0]), 30 * T0[2] / 1920)  # no jump
        self.assertEqual(self.p.sent['look'], 'move')

    def test_crossing_onto_another_desktop_warps_the_cursor_under_the_dot(self):
        self.carrying()
        self.assertEqual(self.control.state.selected, 0)
        q = (T1[0] + T1[2] * 0.25, T1[1] + T1[3] * 0.5)
        out = self.onto(1, q)
        self.assertEqual((self.p.mode, self.p.desktop, self.control.state.selected), ('desktop', 1, 1))
        self.assertEqual(self.p.want[1:], ('desktop', (X2 + 480, 540)))  # that very pixel of XR-2
        self.assertEqual(self.p.super_drag, 'move')  # still the same drag

    def test_the_mapping_holds_on_a_rolled_curved_resized_placed_desktop_at_any_scale(self):
        scaled = [MONITORS[0], MONITORS[1],
                  {'name': 'XR-2', 'x': 4480, 'y': 0, 'width': 2880, 'height': 1620, 'scale': 2.0},
                  {'name': 'XR-3', 'x': 5920, 'y': 0, 'width': 1920, 'height': 1080, 'scale': 1.0}]
        outs = resize.outputs_from_monitors(scaled, NAMES)
        for q_share in ((0.2, 0.3), (0.5, 0.5), (0.85, 0.9)):
            with self.subTest(q=q_share):
                self.setUp()
                self.control.state.placements[1] = {**DEFAULT_PLACEMENT, 'x': 300, 'y': -120, 'curvature': 60, 'distance': 140}
                self.p.rolls[1] = 2500
                self.at(X1 + 1500, 500)
                self.super_op('super_left_press')
                self.clock.now += 0.004
                self.p.feed((X1 + 1919, 500), (0.0, 0.0), outs, self.applied()); frames(self.control.poll())
                self.assertEqual(self.p.mode, 'carry')
                q = (T1[0] + T1[2] * q_share[0], T1[1] + T1[3] * q_share[1])
                aim = fp.project(G, T1, self.p._placement(1), *q)
                self.clock.now += 0.004
                self.p.feed(self.cur, ((aim[0] - self.p.pointer[0]) / self.p.gain, (aim[1] - self.p.pointer[1]) / self.p.gain), outs, self.applied())
                frames(self.control.poll())
                self.assertEqual((self.p.mode, self.p.desktop), ('desktop', 1))
                o = outs[1]
                self.assertLessEqual(abs(self.p.want[2][0] - (o['x'] + q_share[0] * o['width'])), 1)
                self.assertLessEqual(abs(self.p.want[2][1] - (o['y'] + q_share[1] * o['height'])), 1)

    def test_the_gap_between_desktops_is_only_the_dot(self):
        self.carrying()
        serial = self.p.want[0]
        gap = ((T0[0] + T0[2] + T1[0]) / 2, T0[1] + T0[3] / 2)
        out = self.dot_to(gap)
        self.assertEqual((self.p.mode, self.p.want[0]), ('carry', serial))
        self.assertIsNone(last(out, 'view'))
        self.assertEqual(self.control.state.selected, 0)

    def test_back_onto_the_desktop_it_left(self):
        self.carrying()
        self.dot_to((T0[0] + T0[2] + 3, T0[1] + T0[3] / 2))
        self.onto(0, (T0[0] + T0[2] - 30, T0[1] + T0[3] / 2))
        self.assertEqual((self.p.mode, self.p.desktop, self.control.state.selected), ('desktop', 0, 0))

    def test_on_from_one_desktop_across_another_and_onto_a_third(self):
        self.carrying()
        self.onto(1)
        self.at(*self.p.want[2])
        self.at(X2 + 1919, 540)
        self.assertEqual((self.p.mode, self.p.desktop), ('carry', 1))
        self.cur = self.p.want[2]
        self.onto(2, (T2[0] + 60, T2[1] + 100))
        self.assertEqual((self.p.mode, self.p.desktop, self.control.state.selected), ('desktop', 2, 2))
        self.assertEqual(self.p.carries, 2)

    def test_dropping_on_a_desktop_leaves_the_dot_there_as_the_real_cursor(self):
        self.carrying()
        self.onto(1)
        self.at(*self.p.want[2])
        self.super_op('super_release')
        self.assertIsNone(self.p.super_drag)
        self.assertEqual((self.p.mode, self.p.desktop, self.p.want[1]), ('desktop', 1, 'desktop'))
        self.at(X2 + 960, 300)  # the dot goes on as a plain cursor there
        self.at(X2 + 960, 0)
        self.assertEqual(self.p.mode, 'free')  # and now an edge is a handle again

    def test_dropping_between_desktops_frees_the_dot_where_it_is(self):
        self.carrying()
        self.dot_to(((T0[0] + T0[2] + T1[0]) / 2, T0[1] - 60))
        dot = self.p.pointer
        self.super_op('super_release')
        self.assertEqual((self.p.mode, self.p.want[1], self.control.state.selected), ('free', 'free', 0))
        self.assertEqual(self.p.pointer, dot)
        self.assertEqual(self.p._at(self.p.want[2])[0], 0)  # parked on the desktop it left

    def test_the_hand_beating_the_helper_to_the_edge_still_carries(self):
        # The press op takes a helper process to arrive: the cursor may have gone free already, grab and all.
        self.at(X1 + 1500, 500); self.at(X1 + 1919, 500)
        self.assertEqual((self.p.mode, self.p.want[1]), ('free', 'free'))
        self.super_op('super_left_press')
        self.assertEqual((self.p.mode, self.p.want[1]), ('carry', 'carry'))


class SuperSuppressesTests(Base):
    def assert_nothing_happened(self, out):
        self.assertFalse(any(f['op'] in ('placement', 'placement_menu') for f in out))
        self.assertEqual(self.control.state.placements, [dict(DEFAULT_PLACEMENT)] * 3)
        self.assertFalse(self.control.state.menu_open)
        self.assertIsNone(self.p.drag)
        self.assertEqual(self.p.rolls[:3], [0, 0, 0])

    def test_pills_arrows_and_handles_do_nothing_mid_drag(self):
        self.carrying()
        x, y, w, h = fp.pill_rect(T1)
        points = [fp.project_glass(G, T1, DEFAULT_PLACEMENT, x + w / 2, y + h / 2)]
        rx, ry, rw, rh = fp.roll_icon_rect(T1, 'ccw')
        points.append(fp.project(G, T1, DEFAULT_PLACEMENT, rx + rw / 2, ry + rh / 2))
        points.append(fp.project(G, T1, DEFAULT_PLACEMENT, T1[0] - 4, T1[1] - 4))  # a corner
        points.append(fp.project(G, T1, DEFAULT_PLACEMENT, T1[0] + 100, T1[1] - 4))  # an edge
        for point in points:
            with self.subTest(point=point):
                out = self.dot_to(point)
                self.assertEqual(self.p.mode, 'carry')
                self.assertEqual(self.p.sent['look'], 'move')  # the drag's own look, not the handle's
                self.assertFalse(self.p.pills['hover'])
                self.assertFalse(self.p.shown['visible'])
                self.assert_nothing_happened(out)
        self.assertEqual(self.control.state.selected, 0)

    def test_a_crossing_is_a_plain_view_frame_with_no_animation_flags(self):
        self.carrying()
        out = self.onto(1)
        views = [f['payload'] for f in out if f['op'] == 'view']
        self.assertEqual(len(views), 1)
        self.assertNotIn('cut', views[0]); self.assertNotIn('center', views[0])
        self.assertEqual((views[0]['selected'], views[0]['zoomed'], views[0]['mode']), (1, False, 'anchor'))

    def test_no_monitor_focus_call_mid_drag(self):
        self.carrying()
        self.onto(1)
        self.control.join_focus(2)
        self.assertEqual(self.focused, [])

    def test_a_release_with_super_let_go_first_is_ignored_until_its_own_release(self):
        self.carrying()
        self.assertIsNone(self.op('resize_release', ops=('view',)) or None)  # the plain release bind may fire too
        self.assertEqual((self.p.mode, self.p.super_drag), ('carry', 'move'))
        self.super_op('super_release')
        self.assertEqual((self.p.mode, self.p.super_drag), ('free', None))

    def test_maximized_a_super_drag_never_switches_desktops(self):
        self.op('zoom_in')
        self.assertTrue(self.p.can_switch())
        self.super_op('super_left_press')
        self.assertFalse(self.p.can_switch())
        self.p.request_switch(0, 1)  # asked just before the press
        self.assertIsNone(self.control.poll())
        self.assertEqual(self.control.state.selected, 0)
        self.super_op('super_release')
        self.assertTrue(self.p.can_switch())

    def test_a_lost_release_never_strands_the_dot(self):
        self.carrying()
        self.clock.now += view.OVERLAY_IDLE_CLOSE_S + 1
        frames(self.control.poll())
        self.assertIsNone(self.p.super_drag)
        self.assertEqual(self.p.mode, 'free')
        self.setUp()
        self.at(X1 + 600, 400)
        self.super_op('super_left_press')
        self.op('resize_press')  # a plain press: no button was held after all
        self.assertIsNone(self.p.super_drag)
        self.op('resize_release')

    def test_a_super_press_on_a_window_closes_an_open_pill(self):
        self.control.pill_click(1)
        self.at(X1 + 600, 400)
        self.super_op('super_left_press')
        self.assertFalse(self.control.state.menu_open)

    def test_the_ops_are_ignored_without_a_unified_pointer_and_while_a_video_plays(self):
        c = view.ViewControl(view.ViewState(NAMES), 'run', 'session', None, None, None)
        self.addCleanup(c.stop_menu_binds)
        self.assertIsNone(c.on_message({'op': 'super_left_press', 'payload': {}}))
        self.assertTrue(set(view.SUPER_OPS) <= set(video.IGNORED_WHILE_PLAYING))
        with self.assertRaises(ValueError): self.p.on_super('nope')


class SuperResizeTests(Base):
    def test_the_window_keeps_the_cursor_on_its_desktop(self):
        self.control.state.selected = 1
        self.at(X2 + 1500, 500)
        out = self.super_op('super_right_press')
        self.assertEqual(self.p.super_drag, 'resize')
        self.assertEqual((last(out, 'overview_pointer')['held'], last(out, 'overview_pointer')['look']), (True, 'resize_nwse'))
        self.at(X2 + 1800, 700)
        self.assertEqual(self.p.mode, 'desktop')
        self.at(X2 + 1919, 700)
        self.assertEqual((self.p.mode, self.p.desktop), ('desktop', 1))  # an edge is no way out: neither free nor carry
        self.at(X3 + 40, 700)  # Hyprland let it onto XR-3
        self.assertEqual((self.p.mode, self.p.desktop, self.control.state.selected), ('desktop', 1, 1))
        self.assertEqual(self.p._at(self.p.want[2])[0], 1)  # pulled back
        self.super_op('super_release')
        self.assertIsNone(self.p.super_drag)
        self.at(X2 + 1500, 500); self.at(X2 + 1500, 0)
        self.assertEqual(self.p.mode, 'free')  # ordinary again

    def test_right_drag_forwarding_needs_nothing_of_the_run(self):
        # The right button is not bound over a desktop at all, plain or Super, so the press and its motion are Hyprland's.
        lua = uc.enter_desktop_lua(*BIND_ARGS, 6100, 700, monitor='XR-3')
        self.assertNotIn('mouse:273', lua)
        self.at(X1 + 600, 400)
        before = dict(self.control.state.placements[0])
        self.super_op('super_right_press')
        self.at(X1 + 900, 500)
        self.super_op('super_release')
        self.assertEqual((self.p.mode, self.control.state.placements[0]), ('desktop', before))

    def test_the_hand_beating_the_helper_comes_back_to_the_desktop(self):
        self.at(X1 + 1500, 500); self.at(X1 + 1919, 500)
        self.assertEqual(self.p.mode, 'free')
        self.super_op('super_right_press')
        self.assertEqual((self.p.mode, self.p.want[1]), ('desktop', 'desktop'))


class LuaTests(unittest.TestCase):
    """install_drag_binds_lua run against the stub `hl` (test_input_lock.LuaRunTests)."""
    STUB, run_lua = til.LuaRunTests.STUB, til.LuaRunTests.run_lua
    RIG = r'''
local fired, execs, drags = {}, {}, 0
local timers = {}
hl.dsp.window = { drag = function() return { drag = true } end }
local plain_dispatch = hl.dispatch
hl.dispatch = function(p) if p.drag then drags = drags + 1 else plain_dispatch(p) end end
hl.exec_cmd = function(c) execs[#execs + 1] = c:match("(%S+)$") end
hl.timer = function(f, o) timers[#timers + 1] = { f = f, o = o } end
hl.bind = function(chord, action, opts)
  local key = chord .. ((opts and opts.release) and "^" or "")
  fired[key] = { f = action, o = opts }
  state.binds[key] = "fn"
  return setmetatable({ remove = function() state.binds[key] = nil end }, { __tostring = function() return "HL.Keybind" end })
end
local function press(k) fired[k].f() end
local function run_timers() for _, t in ipairs(timers) do print("timer", t.o.timeout, t.o.type) t.f() end timers = {} end
local function list() return "[" .. table.concat(execs, " ") .. "]" end
'''

    def binds(self):
        return view.install_drag_binds_lua(*BIND_ARGS)

    def test_the_binds_are_non_consuming_and_the_releases_ignore_modifiers(self):
        out = self.run_lua('\n'.join([self.RIG, self.binds(),
            'for _, k in ipairs({ "SUPER + mouse:272", "SUPER + mouse:273" }) do print(k, tostring(fired[k].o.non_consuming), tostring(fired[k].o.release)) end',
            'for _, k in ipairs({ "mouse:272^", "mouse:273^" }) do print(k, tostring(fired[k].o.non_consuming), tostring(fired[k].o.ignore_mods)) end',
            'print("count", #xr_workspace_drag_binds)']))
        self.assertEqual(out, [['SUPER + mouse:272', 'true', 'nil'], ['SUPER + mouse:273', 'true', 'nil'],
                               ['mouse:272^', 'true', 'true'], ['mouse:273^', 'true', 'true'], ['count', '4']])

    def test_a_drag_over_a_desktop_tells_the_run_once_each_way(self):
        out = self.run_lua('\n'.join([self.RIG, self.binds(),
            'press("mouse:272^")', 'print("plain release", list())',  # a plain click: nothing runs
            'press("SUPER + mouse:272")', 'print("press", list())', 'press("mouse:272^")', 'print("release", list())',
            'press("mouse:272^")', 'print("again", list())',
            'press("SUPER + mouse:273")', 'press("mouse:273^")', 'print("right", list())', 'print("drags", drags)']))
        self.assertEqual(out, [['plain release', '[]'], ['press', '[super_left_press]'],
                               ['release', '[super_left_press super_release]'], ['again', '[super_left_press super_release]'],
                               ['right', '[super_left_press super_release super_right_press super_release]'], ['drags', '0']])

    def test_with_the_dot_free_the_drag_on_the_parked_cursor_is_ended_and_the_run_hears_nothing(self):
        out = self.run_lua('\n'.join([self.RIG, self.binds(), 'xr_workspace_grab = {}',
            'press("SUPER + mouse:272")', 'run_timers()', 'press("mouse:272^")',
            'press("SUPER + mouse:273")', 'run_timers()', 'press("mouse:273^")', 'print("run", list())', 'print("drags", drags)']))
        self.assertEqual(out, [['timer', '1', 'oneshot'], ['timer', '1', 'oneshot'], ['run', '[]'], ['drags', '2']])

    def test_run_end_and_a_reinstall_leave_one_set(self):
        out = self.run_lua('\n'.join([self.RIG, self.binds(), self.binds(), 'print("twice", #xr_workspace_drag_binds)',
                                      view.remove_binds_lua(), 'print("gone", tostring(xr_workspace_drag_binds))']))
        self.assertEqual(out, [['twice', '4'], ['gone', 'nil']])

    def test_a_refused_bind_is_left_out(self):
        out = self.run_lua('\n'.join([self.RIG, 'local plain = hl.bind hl.bind = function(k, a, o) if k == "SUPER + mouse:273" then error("no") end return plain(k, a, o) end',
                                      self.binds(), 'print("count", #xr_workspace_drag_binds)']))
        self.assertEqual(out, [['count', '3']])

    def test_paths_and_tokens_are_checked_and_the_dead_run_cleanup_is_the_same_text(self):
        with self.assertRaises(ValueError): view.install_drag_binds_lua('/usr/bin/python3;rm', *BIND_ARGS[1:])
        with self.assertRaises(ValueError): view.install_drag_binds_lua(*BIND_ARGS[:3], 'bad token', BIND_ARGS[4])
        self.assertEqual(xr_control.REMOVE_DRAG_BINDS, view.remove_drag_binds_lua())
        self.assertLessEqual(len(view.install_drag_binds_lua(*til.LONG_ARGS)), 4096)
        self.assertLessEqual(len(view.remove_binds_lua() + ' ' + view.install_binds_lua(*til.LONG_ARGS)), 4096)

    def test_the_helper_and_the_control_socket_take_the_ops(self):
        for op in view.SUPER_OPS:
            self.assertIn(op, xr_control.OPS)
            message = {'schema': 1, 'request_id': 1, 'run_id': 'run', 'session': 'session', 'op': op, 'payload': {}}
            self.assertEqual(ipc.decode_control((json.dumps(message) + '\n').encode())['op'], op)


class SamplerTests(unittest.TestCase):
    """The run's wiring: ViewControl, UnifiedPointer and the real UnifiedSampler thread against a fake compositor client."""
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

    def op(self, name):
        return frames(self.control.on_message({'op': name, 'payload': {}}))

    def start(self):
        client = FakeUnifiedClient(cursor=MID1)
        sampler = uc.UnifiedSampler(client, NAMES, self.p, BIND_ARGS).start(); self.addCleanup(sampler.stop)
        self.assertTrue(self.wait(lambda: sampler.bound and self.p.mode == 'desktop'))
        return client, sampler

    def test_a_drag_from_xr_1_onto_xr_2_never_grabs_or_focuses_and_warps_the_cursor_over(self):
        client, sampler = self.start()
        self.op('super_left_press')
        client.cursor = (X1 + 1919, 500)  # the hand at XR-1's right edge
        self.assertTrue(self.wait(lambda: self.p.mode == 'carry' and client.count('warp') == 1))
        self.assertEqual(client.args('warp'), [(X1 + 1919 - uc.CARRY_INSET_PX, 500)])  # just inside: the window waits there
        self.assertTrue(sampler.bound); self.assertFalse(sampler.grabbed)
        q = (T1[0] + T1[2] * 0.25, T1[1] + T1[3] * 0.5)
        dot = fp.project(G, T1, DEFAULT_PLACEMENT, *q)
        self.p.pointer = (dot[0] - self.p.gain, dot[1])  # one hand pixel short of it: the motion below lands it there
        self.p.feed(client.cursor, (1.0, 0.0), OUTPUTS, sampler.applied)
        self.assertTrue(self.wait(lambda: self.p.mode == 'desktop' and client.count('warp') == 2))
        at = client.args('warp')[-1]
        self.assertLessEqual(abs(at[0] - (X2 + 480)), 1); self.assertLessEqual(abs(at[1] - 540), 1)
        self.assertEqual(self.control.state.selected, 1)
        self.op('super_release')
        time.sleep(0.03); self.control.poll()
        self.assertEqual((client.count('lock'), client.count('enter'), client.count('confine'), client.count('release')), (0, 1, 0, 0))
        self.assertEqual(client.focused, [None])  # only the first entry: no focus call mid drag
        self.control.join_focus(2)
        self.assertEqual(self.focused, [])

    def test_the_leash_keeps_hand_motion_showing_and_stays_short(self):
        client, sampler = self.start()
        self.op('super_left_press')
        client.cursor = (X1 + 1919, 500)
        self.assertTrue(self.wait(lambda: self.p.mode == 'carry' and client.count('warp') == 1))
        park = client.args('warp')[0]
        before = self.p.pointer
        client.cursor = (park[0] + uc.CARRY_LEASH_PX + 4, park[1])  # the hand pushing on
        self.assertTrue(self.wait(lambda: client.count('warp') == 2))
        self.assertEqual(client.args('warp')[-1], park)
        self.assertTrue(self.wait(lambda: self.p.pointer[0] > before[0]))  # the dot took that motion
        client.cursor = (park[0] + uc.CARRY_LEASH_PX - 1, park[1])  # inside the leash: left alone
        time.sleep(0.03)
        self.assertEqual(client.count('warp'), 2)

    def test_dropped_between_desktops_the_free_grab_comes_back(self):
        client, sampler = self.start()
        self.op('super_left_press')
        client.cursor = (X1 + 1919, 500)
        self.assertTrue(self.wait(lambda: self.p.mode == 'carry' and client.count('warp') == 1))
        self.op('super_release')
        self.assertTrue(self.wait(lambda: sampler.grabbed and client.count('lock') == 1))
        self.assertEqual(self.p.mode, 'free')

    def test_the_hand_beating_the_helper_drops_the_grab_again(self):
        client, sampler = self.start()
        client.cursor = (X1 + 1919, 500)
        self.assertTrue(self.wait(lambda: sampler.grabbed and self.p.mode == 'free'))
        self.op('super_left_press')
        self.assertTrue(self.wait(lambda: self.p.mode == 'carry' and not sampler.grabbed and sampler.bound))
        self.assertEqual(self.p._at(client.args('enter')[-1][5:])[0], 0)  # the drag goes on from XR-1

    def test_maximized_the_push_past_an_edge_does_not_switch_mid_super_drag(self):
        self.control.state.zoomed = True
        client = FakeUnifiedClient(cursor=(X1 + 1900, 500))
        sampler = uc.UnifiedSampler(client, NAMES, self.p, BIND_ARGS).start(); self.addCleanup(sampler.stop)
        self.op('super_left_press')
        for k in range(30):
            client.cursor = (X2 + 10 + k, 500)  # pushed past XR-1's right edge, onto XR-2
            self.wait(lambda: client.count('confine') > k, timeout=0.2)
        self.assertEqual(self.control.state.selected, 0)  # kept on XR-1, however hard it is pushed
        self.assertGreater(client.count('confine'), 0)


if __name__ == '__main__':
    unittest.main()
