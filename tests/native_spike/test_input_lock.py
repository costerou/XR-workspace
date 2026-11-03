"""Overview input lock (input_lock.py): capture instead of desktop mode, the held cursor and its leash, warps made by focus
changes followed, the release on leaving the overview, the lock's button and wheel binds (also run by a real Lua
interpreter against a stub `hl`), a dead run's cleanup, bind_check and the run's wiring.

Offline only: fake clocks, fake compositor clients and a stub `hl` (Hyprland itself only as --verify-config, through
bind_check, when installed). No compositor, renderer binary or glasses.
"""
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest import mock

from tools.native_spike import bind_check, free_pointer as fp, input_lock as il, orbit, resize, view, xr_control
from tools.native_spike.geometry import DEFAULT_PLACEMENT
from tools.native_spike.profiles import LayoutStore

from test_free_pointer import BIND_ARGS, G, GAIN, MONITORS, NAMES, OUTPUTS, T0, T1, X1, X2, X3, FakeClient, PointerBase, last

T2 = G['tiles'][2]
HYPRLAND = shutil.which('Hyprland')
# Long but valid paths: the whole lock grab must still fit one eval (HyprlandClient._eval's 4096 limit).
LONG_ARGS = ('/usr/bin/python3', '/home/someone/' + 'a' * 150 + '/tools/native_spike/xr_control.py',
             '/run/user/1000/' + 'b' * 150 + '/control.sock', 'r' * 32, 's' * 64)


class LockBase(PointerBase):
    """PointerBase with the run's pointer, the input lock's: feeds stand in for the sampler (each sample after it has
    applied every request), so a fed cursor is where the hidden cursor really is."""
    def setUp(self):
        super().setUp()
        self.p = self.control.overview_pointer = il.LockedPointer(self.control, clock=self.clock)
        self.addCleanup(self.p.close)
        self.control.set_overview_geometry(G)

    def on_pill(self, i=0):
        """Captured in XR-1's middle, then the dot onto desktop i's glass pill."""
        self.at(X1 + 960, 540)
        x, y, w, h = fp.pill_rect(G['tiles'][i])
        out = self.move_canvas(x + w / 2 - self.p.pointer[0], y + h / 2 - self.p.pointer[1])
        self.assertEqual(self.p.hover, ('pill', i))
        return out


class CaptureTests(LockBase):
    def test_a_cursor_on_a_desktop_is_captured_where_it_is(self):
        out = self.at(X1 + 960, 540)
        self.assertEqual(self.p.mode, 'free')  # no desktop mode: the dot is the pointer from the start
        self.assertEqual(self.p.want[1:], ('free', (X1 + 960, 540)))  # held where it was: the window under it keeps focus
        self.assertAlmostEqual(self.p.pointer[0], T0[0] + T0[2] / 2, places=2)
        self.assertAlmostEqual(self.p.pointer[1], T0[1] + T0[3] / 2, places=2)
        self.assertAlmostEqual(self.p.gain, GAIN)
        self.assertTrue(last(out, 'overview_pointer')['visible'])
        self.assertEqual(self.p.captures, 1)

    def test_no_edge_is_needed_and_the_held_cursor_stays_clear_of_every_edge(self):
        self.at(X1 + 700, 0)
        self.assertEqual(self.p.want[1:], ('free', (X1 + 700, il.PARK_MARGIN_PX)))
        top = fp.project(G, T0, DEFAULT_PLACEMENT, T0[0] + 700 / 1920 * T0[2], T0[1])  # XR-1 faces you: turned, not flat
        self.assertAlmostEqual(self.p.pointer[1], top[1], places=2)  # the dot starts where the cursor was
        o, m = OUTPUTS[0], il.PARK_MARGIN_PX
        self.assertEqual(il.park_point(o, (X1, 0)), (X1 + m, m))
        self.assertEqual(il.park_point(o, (X1 + 1919, 1079)), (X1 + 1919 - m, 1079 - m))
        self.assertEqual(il.park_point(o), (X1 + 960, 540))
        x, y = il.park_point({'index': 0, 'x': 10, 'y': 20, 'width': 150, 'height': 100}, (10, 20))
        self.assertLessEqual(abs(x - 85), 1); self.assertLessEqual(abs(y - 70), 1)  # too small for the margin: the middle

    def test_a_cursor_on_the_physical_monitor_is_left_alone_until_it_comes_onto_a_desktop(self):
        out = self.at(100, 100)
        self.assertEqual(self.p.mode, 'away'); self.assertEqual(self.p.want[1], 'away')  # no binds: the panel is usable
        self.assertIsNone(last(out, 'overview_pointer'))
        self.at(X2 + 3, 500)
        self.assertEqual(self.p.mode, 'free')
        self.assertEqual(self.p.want[1:], ('free', (X2 + il.PARK_MARGIN_PX, 500)))
        self.assertEqual(self.control.state.selected, 1)

    def test_ctrl_alt_down_captures_a_cursor_from_the_monitor_in_the_selected_desktop(self):
        self.control.state.selected = 2
        self.op('zoom_in'); self.at(100, 100)
        self.assertEqual(self.p.mode, 'off')
        self.op('zoom_out')
        self.at(100, 101)  # (a sample that differs: an unchanged one is not news)
        self.assertEqual(self.p.want[1:], ('free', (X3 + 960, 540)))
        self.assertAlmostEqual(self.p.pointer[0], T2[0] + T2[2] / 2, places=2)
        self.assertFalse(self.p.summoned)

    def test_nothing_without_the_renderers_geometry(self):
        self.p.geometry = None
        self.assertEqual(self.at(X1 + 960, 540), [])
        self.assertEqual(self.p.mode, 'off')


class LockTests(LockBase):
    def test_moving_into_a_desktop_selects_it_but_never_hands_the_cursor_back(self):
        self.at(X1 + 960, 540)
        serial = self.p.want[0]
        out = self.move_canvas(T1[0] + T1[2] / 2 - self.p.pointer[0], 0)  # deep into XR-2
        self.assertEqual(self.p.mode, 'free')
        self.assertEqual(self.p.want[0], serial)  # the real cursor stays held on XR-1
        self.assertEqual(self.p.hover[:2], ('desktop', 1))
        self.assertEqual(self.control.state.selected, 1)  # Ctrl+Alt+Up zooms into the desktop the dot is on
        self.assertEqual(last(out, 'view')['selected'], 1)
        self.assertTrue(last(out, 'overview_pointer')['visible'])

    def test_a_click_on_a_desktop_selects_it_and_reaches_no_window(self):
        self.at(X2 + 960, 540)
        self.control.state.selected = 0
        out = self.op('resize_press')  # the left button is the run's while captured
        self.assertEqual(self.p.mode, 'free')
        self.assertEqual(self.control.state.selected, 1)
        self.assertEqual(last(out, 'view')['selected'], 1)
        self.assertEqual(self.p.want[1:], ('free', (X2 + 960, 540)))  # no hand-back to the window under the dot
        self.assertEqual(self.p.focus_clicks, 1)
        self.assertEqual(self.op('resize_release'), [])

    def test_the_other_buttons_do_nothing_but_end_a_drag(self):
        self.at(X1 + 960, 540)
        out = self.op(il.OTHER_BUTTON_OP)  # a right, middle or side click
        self.assertFalse(any(f['op'] in ('placement', 'view', 'placement_menu') for f in out))
        self.assertIsNone(self.p.drag); self.assertEqual(self.p.mode, 'free')
        self.on_pill(0); self.op('resize_press'); self.move_canvas(30, 0)
        moved = dict(self.control.state.placements[0])
        self.op(il.OTHER_BUTTON_OP)
        self.assertIsNone(self.p.drag); self.assertEqual(self.store.saved[0], moved)

    def test_pill_clicks_pill_drags_and_corner_drags_still_work(self):
        self.on_pill(0)
        self.op('resize_press'); self.op('resize_release')
        self.assertTrue(self.control.state.menu_open); self.assertEqual(self.control.state.menu_desktop, 0)
        self.op('menu_back')
        self.on_pill(0); self.op('resize_press')
        self.move_canvas(0, -T0[3] * 1.3); self.move_canvas(T0[2] * 0.2, 0)  # lifted clear of XR-2 (no overlap), then right
        want = orbit.drag_target(G, (0, 0), self.p.drag['start'], self.p.pointer)  # the turn round you it asks for
        self.op('resize_release')
        self.assertEqual((self.store.saved[0]['x'], self.store.saved[0]['y']), want)
        corner = fp.project(G, T0, self.control.state.placements[0], T0[0] + T0[2], T0[1])  # the moved desktop's top right
        self.move_canvas(corner[0] + 4 - self.p.pointer[0], corner[1] - 4 - self.p.pointer[1])
        self.assertEqual(self.p.hover, ('corner', 0, 'tr'))
        self.op('resize_press'); self.move_canvas(30, -30); self.op('resize_release')
        self.assertLess(self.store.saved[0]['distance'], 100)
        self.assertEqual(self.p.mode, 'free')

    def test_a_keyboard_focus_warp_is_followed_and_held_there(self):
        self.at(X1 + 960, 540)
        out = self.at(X2 + 960, 540)  # Ctrl+Alt+Right: Hyprland's focus warped the hidden cursor to XR-2's middle
        self.assertEqual(self.p.mode, 'free'); self.assertEqual(self.p.follows, 1)
        self.assertEqual(self.p.want[1:], ('free', (X2 + 960, 540)))  # held there now, not pulled back to XR-1
        self.assertAlmostEqual(self.p.pointer[0], T1[0] + T1[2] / 2, places=2)
        self.assertEqual(self.control.state.selected, 1)
        self.assertTrue(last(out, 'overview_pointer')['visible'])

    def test_a_warp_onto_the_desktop_the_dot_is_on_moves_only_the_held_cursor(self):
        self.at(X1 + 960, 540)
        self.move_canvas(T1[0] + T1[2] * 0.25 - self.p.pointer[0], 0)  # the dot onto XR-2 (a click there focuses it)
        pointer = self.p.pointer
        self.at(X2 + 960, 540)  # Hyprland's focus warped the hidden cursor to XR-2's middle
        self.assertEqual(self.p.want[1:], ('free', (X2 + 960, 540)))
        self.assertEqual(self.p.pointer, pointer)  # the dot stays where it was clicked
        self.assertEqual(self.control.state.selected, 1)

    def test_a_warp_ends_a_drag_and_saves_it(self):
        self.on_pill(0); self.op('resize_press'); self.move_canvas(30, 0)
        moved = dict(self.control.state.placements[0])
        self.at(X3 + 960, 540)
        self.assertIsNone(self.p.drag); self.assertEqual(self.store.saved[0], moved)
        self.assertEqual(self.control.state.placements[0], moved)  # the jump did not drag the desktop along

    def test_a_warp_off_the_desktops_ends_the_capture_there(self):
        self.at(X1 + 960, 540)
        out = self.at(1200, 700)  # a window sent back to the monitor took the cursor with it
        self.assertEqual(self.p.mode, 'away'); self.assertEqual(self.p.want[1:], ('away', None))
        self.assertFalse(last(out, 'overview_pointer')['visible'])
        self.at(1250, 700)
        self.assertEqual(self.p.mode, 'away')  # left alone on the monitor
        self.at(X1 + 400, 300)
        self.assertEqual(self.p.mode, 'free')  # captured again on a desktop

    def test_a_stray_within_the_jump_distance_is_the_leashs_not_a_warp(self):
        self.at(X1 + 960, 540)
        serial, pointer = self.p.want[0], self.p.pointer
        self.at(X1 + 960 + fp.JUMP_PX - 1, 540)  # strayed far (a starved sampler), still not a warp
        self.assertEqual(self.p.want[0], serial); self.assertEqual(self.p.pointer, pointer)
        self.assertEqual(self.p.follows, 0)

    def test_samples_from_before_the_cursor_was_held_are_ignored(self):
        self.control.state.selected = 2
        self.op('zoom_in'); self.at(100, 100); self.op('zoom_out')
        self.at(100, 101)  # summoned: captured in XR-3's middle
        serial = self.p.want[0]
        self.assertEqual(self.p.want[1:], ('free', (X3 + 960, 540)))
        self.at(100, 102, applied=serial - 1)  # the sampler has not parked it yet: not a warp off the desktops
        self.assertEqual(self.p.mode, 'free')
        self.at(X3 + 960, 540)
        self.assertEqual(self.p.mode, 'free'); self.assertEqual(self.p.follows, 0)

    def test_zoom_in_typing_mode_and_a_video_end_the_capture(self):
        self.at(X1 + 960, 540)
        self.op('zoom_in'); self.at(X1 + 960, 540)
        self.assertEqual(self.p.mode, 'off'); self.assertEqual(self.p.want[1], 'off')
        self.op('zoom_out'); self.at(X1 + 960, 540)
        self.assertEqual(self.p.mode, 'free')
        self.op(view.TYPING_OP); self.at(X1 + 960, 540)
        self.assertEqual(self.p.want[1], 'off')
        self.op(view.TYPING_OP); self.at(X1 + 960, 540)
        self.assertEqual(self.p.mode, 'free')
        self.control.video_active = True; self.at(X1 + 960, 540)
        self.assertEqual(self.p.want[1], 'off')


class FakeLockClient(FakeClient):
    def lock_pointer(self, *args):
        self._call('lock', args)
        if self.fail_grab: raise ValueError('rejected')
        self.cursor = args[-2:]  # held there

    def grab_pointer(self, *args):
        raise AssertionError('the lock grabs with lock_pointer')


class SamplerTests(unittest.TestCase):
    def setUp(self):
        self.control = view.ViewControl(view.ViewState(NAMES), 'run', 'session', None, None, None)
        self.addCleanup(self.control.stop_menu_binds)
        self.p = self.control.overview_pointer = il.LockedPointer(self.control); self.addCleanup(self.p.close)
        self.p.set_geometry(G)

    def wait(self, predicate, timeout=2.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.control.poll()
            if predicate(): return True
            time.sleep(0.003)
        return predicate()

    def start(self, client):
        sampler = il.LockedSampler(client, NAMES, self.p, BIND_ARGS).start()
        self.addCleanup(sampler.stop)
        return sampler

    def test_captures_where_the_cursor_is_with_the_lock_grab(self):
        client = FakeLockClient(cursor=(X1 + 960, 540))
        self.start(client)
        self.assertTrue(self.wait(lambda: client.count('lock') == 1))
        self.assertEqual(client.args('lock'), [BIND_ARGS + (X1 + 960, 540)])
        self.assertTrue(self.wait(lambda: self.p.mode == 'free'))
        time.sleep(0.05)
        self.assertEqual(client.count('warp'), 0)  # nothing moved it

    def test_the_leash_puts_a_stray_cursor_back_and_the_dot_still_moves(self):
        client = FakeLockClient(cursor=(X1 + 960, 540))
        sampler = self.start(client)
        self.assertTrue(self.wait(lambda: sampler.grabbed and self.p.pointer is not None))
        start = self.p.pointer
        client.cursor = (X1 + 960 + il.LEASH_PX + 8, 540)  # the hand moved it 40 px
        self.assertTrue(self.wait(lambda: client.count('warp') >= 1))
        self.assertEqual(client.args('warp')[0], (X1 + 960, 540))
        self.assertTrue(self.wait(lambda: self.p.pointer[0] > start[0] + 30 * GAIN))  # the motion moved the dot
        n = client.count('warp')
        client.cursor = (X1 + 960 + il.LEASH_PX - 2, 540)  # inside the leash: left there
        time.sleep(0.05)
        self.assertEqual(client.count('warp'), n)

    def test_a_warp_is_not_pulled_back_nor_counted_as_motion(self):
        client = FakeLockClient(cursor=(X1 + 1919, 540))
        sampler = self.start(client)
        self.assertTrue(self.wait(lambda: client.count('lock') == 1))
        self.assertEqual(client.args('lock')[0], BIND_ARGS + (X1 + 1919 - il.PARK_MARGIN_PX, 540))
        self.assertTrue(self.wait(lambda: sampler.grabbed))
        client.cursor = (X2 + 960, 540)  # a focus change: 1061 px, under the free pointer's own SAMPLE_JUMP_PX
        self.assertTrue(self.wait(lambda: client.count('lock') == 2))
        self.assertEqual(client.args('lock')[1], BIND_ARGS + (X2 + 960, 540))
        self.assertNotIn((X1 + 1919 - il.PARK_MARGIN_PX, 540), client.args('warp'))  # never pulled back to XR-1
        self.assertTrue(self.wait(lambda: self.control.state.selected == 1))
        self.assertAlmostEqual(self.p.pointer[0], T1[0] + T1[2] / 2, delta=1)  # the dot followed; no 1061 px of motion

    def test_leaving_the_overview_shows_the_cursor_where_it_is(self):
        client = FakeLockClient(cursor=(X1 + 960, 540))
        sampler = self.start(client)
        self.assertTrue(self.wait(lambda: sampler.grabbed))
        self.control.state.zoomed = True
        self.assertTrue(self.wait(lambda: client.count('release') == 1))
        self.assertEqual(client.args('release'), [()])  # no position: never back where it was captured
        self.assertEqual(client.count('remove'), 0)
        self.assertFalse(sampler.grabbed)
        self.control.state.zoomed = False
        self.assertTrue(self.wait(lambda: client.count('lock') == 2))  # captured again in the overview
        sampler.stop()
        self.assertGreaterEqual(client.count('remove'), 1)  # run end: the free pointer's own bind removal

    def test_a_cursor_on_the_monitor_is_not_grabbed(self):
        client = FakeLockClient(cursor=(100, 100))
        sampler = self.start(client)
        self.assertTrue(self.wait(lambda: sampler.samples > 20))
        self.assertEqual(client.count('lock'), 0); self.assertEqual(client.count('warp'), 0)

    def test_a_config_reload_locks_again_where_it_is_held(self):
        client = FakeLockClient(cursor=(X1 + 960, 540))
        self.start(client)
        self.assertTrue(self.wait(lambda: client.count('lock') == 1))
        self.control.on_events(b'configreloaded>>\n')
        self.assertTrue(self.wait(lambda: client.count('lock') == 2))
        self.assertEqual(client.args('lock')[1], BIND_ARGS + (X1 + 960, 540))


class LuaTests(unittest.TestCase):
    def test_the_lock_is_the_free_pointers_grab_plus_its_binds_in_one_eval(self):
        grab = fp.grab_pointer_lua(*BIND_ARGS, 3520, 540)
        lua = il.lock_pointer_lua(*BIND_ARGS, 3520, 540)
        self.assertIn(grab, lua)
        self.assertTrue(lua.endswith(il.lock_binds_lua(*BIND_ARGS)))
        self.assertLess(lua.index(f'{resize.GRAB_TABLE}.home ='), lua.index(grab))  # pinned before the grab's bind removal
        for chord in il.OTHER_BUTTONS + il.WHEEL: self.assertIn(f'"{chord}"', lua)
        self.assertIn(' '.join(BIND_ARGS[:1] + ('-I',) + BIND_ARGS[1:]) + ' resize_release"', lua)
        self.assertNotIn('os.', lua); self.assertNotIn('io.', lua)
        self.assertLess(len(il.lock_pointer_lua(*LONG_ARGS, 3520, 540)), 4096)
        with self.assertRaises(ValueError): il.lock_binds_lua('/usr/bin/python3;rm', *BIND_ARGS[1:])
        with self.assertRaises(ValueError): il.lock_binds_lua(*BIND_ARGS[:3], 'bad token', BIND_ARGS[4])
        with self.assertRaises(ValueError): il.lock_pointer_lua(*BIND_ARGS, 3520.5, 540)

    def test_hyprland_client_sends_exactly_that_lua(self):
        from tools.native_spike import hyprland as h
        client = h.HyprlandClient('f' * 40 + '_1793506481_1554975112', Path('/nonexistent'))
        sent = []
        client._request = lambda wire: sent.append(wire) or b'ok'
        client.lock_pointer(*BIND_ARGS, 3520, 540)
        self.assertEqual(sent, ['/eval ' + il.lock_pointer_lua(*BIND_ARGS, 3520, 540)])

    def test_a_dead_runs_click_on_any_bound_button_removes_the_whole_table(self):
        self.assertIn(il.OTHER_BUTTON_OP, xr_control.OPS)
        with mock.patch.object(xr_control, 'drop_stale_menu_binds') as drop:
            self.assertEqual(xr_control.main(['x', '/nonexistent/c.sock', 'run', 's', il.OTHER_BUTTON_OP]), 1)
        drop.assert_called_once_with(code=xr_control.REMOVE_RESIZE_BINDS)
        self.assertTrue(xr_control.REMOVE_RESIZE_BINDS.startswith(view._remove_lua(resize.BIND_TABLE)))


class LuaRunTests(unittest.TestCase):
    """The generated Lua, run by a real Lua interpreter against a stub `hl` (no compositor)."""
    STUB = r'''
local state = { invisible = false, x = 100, y = 200, delay = 300, binds = {}, moves = {} }
local refuse = {}
hl = { dsp = { cursor = {}, exec_cmd = function(c) return { exec = c } end } }
function hl.get_config(k)
  if k == "cursor.invisible" then return state.invisible end
  if k == "binds.scroll_event_delay" then
    if state.delay == nil then error("no such option") end
    return state.delay
  end
  error("unknown option " .. k)
end
function hl.get_cursor_pos() return { x = state.x, y = state.y } end
function hl.config(t)
  if t.cursor then state.invisible = t.cursor.invisible end
  if t.binds then state.delay = t.binds.scroll_event_delay end
end
function hl.dsp.cursor.move(p) return p end
function hl.dispatch(p) state.x, state.y = p.x, p.y state.moves[#state.moves + 1] = p.x .. "," .. p.y end
function hl.bind(chord, action, opts)
  if refuse[chord] then error("unknown key " .. chord) end
  local key = chord .. ((opts and opts.release) and "^" or "")
  state.binds[key] = type(action) == "function" and "nothing" or action.exec:match("(%S+)$")
  local h = { remove = function() state.binds[key] = nil end }
  return setmetatable(h, { __tostring = function() return "HL.Keybind" end })
end
local function count() local n = 0 for _ in pairs(state.binds) do n = n + 1 end return n end
local function show(tag)
  print(tag, tostring(state.invisible), state.x, state.y, count(), tostring(state.delay), #(xr_workspace_resize_binds or {}))
end
'''

    def run_lua(self, body):
        lua = shutil.which('lua') or shutil.which('luajit')
        if lua is None: self.skipTest('no Lua interpreter')
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / 'run.lua'
            script.write_text(self.STUB + body)
            done = subprocess.run([lua, str(script)], capture_output=True, text=True, timeout=10)
        self.assertEqual(done.returncode, 0, done.stderr)
        return [line.split('\t') for line in done.stdout.splitlines()]

    def lock(self, x=3520, y=540):
        return il.lock_pointer_lua(*BIND_ARGS, x, y)

    def test_lock_then_leave_the_overview(self):
        out = self.run_lua('\n'.join([
            self.lock(), 'show("lock")',
            'print("ops", state.binds["mouse:272"], state.binds["mouse:272^"], state.binds["mouse:273"], '
            'state.binds["mouse:279"], state.binds["mouse_up"], state.binds["mouse_right"])',
            fp.release_pointer_lua(), 'show("release")']))
        n = 2 + len(il.OTHER_BUTTONS) + len(il.WHEEL)
        self.assertEqual(out, [['lock', 'true', '3520', '540', str(n), '0', str(n + 1)],  # binds + the delay's restore entry
                               ['ops', 'resize_press', 'resize_release', 'resize_release', 'resize_release', 'nothing', 'nothing'],
                               ['release', 'false', '3520', '540', '0', '300', '0']])  # shown where it is held

    def test_a_dead_runs_cleanup_gives_everything_back(self):
        out = self.run_lua('\n'.join([
            self.lock(), 'state.x, state.y = 3600, 700',  # the run died; the hidden cursor moved on
            xr_control.REMOVE_RESIZE_BINDS, 'show("dead")']))
        self.assertEqual(out, [['dead', 'false', '100', '200', '0', '300', '0']])  # back where the capture found it

    def test_a_relock_never_bounces_through_where_the_capture_started(self):
        out = self.run_lua('\n'.join([
            self.lock(), 'state.x, state.y = 5440, 540', 'state.moves = {}',  # a focus warp, followed
            self.lock(5440, 540), 'show("relock")', 'print("moves", table.concat(state.moves, " "))',
            fp.release_pointer_lua(), 'show("release")']))
        n = 2 + len(il.OTHER_BUTTONS) + len(il.WHEEL)
        self.assertEqual(out[0], ['relock', 'true', '5440', '540', str(n), '0', str(n + 1)])  # once each, never doubled
        self.assertNotIn('100,200', out[1][1])  # never through the first capture's spot
        self.assertEqual(out[2], ['release', 'false', '5440', '540', '0', '300', '0'])  # the user's delay, not the lock's 0

    def test_a_refused_bind_is_left_out_and_an_unknown_delay_is_left_alone(self):
        out = self.run_lua('\n'.join([
            'refuse["mouse_left"] = true', 'state.delay = nil',
            self.lock(), 'show("lock")', resize.remove_resize_binds_lua(), 'show("removed")']))
        n = 2 + len(il.OTHER_BUTTONS) + len(il.WHEEL) - 1
        self.assertEqual(out, [['lock', 'true', '3520', '540', str(n), 'nil', str(n)],
                               ['removed', 'false', '100', '200', '0', 'nil', '0']])

    def test_a_delay_already_zero_needs_no_restore(self):
        out = self.run_lua('\n'.join(['state.delay = 0', self.lock(), 'show("lock")', fp.release_pointer_lua(), 'show("release")']))
        n = 2 + len(il.OTHER_BUTTONS) + len(il.WHEEL)
        self.assertEqual([o[5:] for o in out], [['0', str(n)], ['0', '0']])


class BindCheckTests(unittest.TestCase):
    def test_the_lock_binds_are_checked_and_clash_with_nothing_of_ours(self):
        chords = bind_check.run_chords()
        self.assertEqual([(c, op) for w, c, op in chords if w == 'lock'],
                         [(b, il.OTHER_BUTTON_OP) for b in il.OTHER_BUTTONS] + [(w, 'nothing') for w in il.WHEEL])
        self.assertEqual(bind_check.clashes([]), [])
        problems = bind_check.clashes([('desktop', 'bind', 'mouse:274'), ('desktop', 'bind', 'SUPER + SHIFT + mouse_down')])
        self.assertEqual(len(problems), 1); self.assertIn('mouse:274', problems[0])  # a modifier chord is another bind

    def test_the_harness_runs_the_lock_binds_into_the_free_pointers_table_and_removes_them(self):
        lua = bind_check.harness_lua('/tmp/x/hyprland.lua', '/tmp/x/out.tsv')
        install = lua.index(resize.install_resize_binds_lua(*bind_check.DUMMY_ARGS))
        lock = lua.index(il.lock_binds_lua(*bind_check.DUMMY_ARGS))
        self.assertLess(install, lock)
        self.assertGreater(lua.index(resize.remove_resize_binds_lua(), lock), lock)

    @unittest.skipUnless(HYPRLAND, 'Hyprland not installed')
    def test_verify_config_loads_the_lock_binds_and_finds_a_clash(self):
        with tempfile.TemporaryDirectory() as home:
            hypr = Path(home) / '.config' / 'hypr'; hypr.mkdir(parents=True)
            cfg = hypr / 'hyprland.lua'
            cfg.write_text('hl.bind("SUPER + mouse:273", hl.dsp.exec_cmd("true"))\n')  # a modifier chord: no clash
            status, lines = bind_check.check(cfg, HYPRLAND, home)
            self.assertEqual(status, 0, lines)
            self.assertIn("input lock's other buttons and wheel", lines[-1])
            cfg.write_text('hl.bind("mouse:275", hl.dsp.exec_cmd("true"))\n')
            status, lines = bind_check.check(cfg, HYPRLAND, home)
            self.assertEqual(status, 1, lines)
            self.assertTrue(any('mouse:275' in l and 'lock' in l for l in lines), lines)


class WiringTests(unittest.TestCase):
    def test_the_run_captures_with_the_lock(self):
        from tools.native_spike import managed
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        client = mock.Mock(runtime_dir=Path(tmp.name))
        client.query.side_effect = lambda kind, *a, **k: (MONITORS if kind == 'monitors' else
                                                          {'x': X2 + 960, 'y': 540} if kind == 'cursorpos' else None)
        client.event_stream.side_effect = OSError('no events')
        with managed.view_controls(client, 'run', 'session', NAMES, store=LayoutStore(Path(tmp.name) / 'l.json')) as control:
            self.assertIsInstance(control.overview_pointer, il.LockedPointer)
            control.set_overview_geometry(G)
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and not client.lock_pointer.called:
                control.poll(); time.sleep(0.01)
            self.assertEqual(client.lock_pointer.call_args.args[5:], (X2 + 960, 540))
            self.assertEqual(control.state.selected, 1)
        client.grab_pointer.assert_not_called()
        client.remove_resize_binds.assert_called()  # run end


if __name__ == '__main__':
    unittest.main()
