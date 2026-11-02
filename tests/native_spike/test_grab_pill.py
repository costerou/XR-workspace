"""Overview grab pill (resize.py): hit strip, move math, overlap guard, drag state, hidden-cursor handling, Lua, wiring.

Offline only: fake clocks and fake compositor clients. No compositor, renderer binary or glasses.
"""
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest import mock

from tools.native_spike import resize, view, xr_control
from tools.native_spike.geometry import DEFAULT_PLACEMENT, PLACEMENT_BOUNDS
from tools.native_spike.profiles import LayoutStore

NAMES = ['XR-1', 'XR-2', 'XR-3']
MONITORS = [{'name': 'DP-1', 'x': 0, 'y': 0, 'width': 2560, 'height': 1440, 'scale': 1.0},
            {'name': 'XR-1', 'x': 2560, 'y': 0, 'width': 1920, 'height': 1080, 'scale': 1.0},
            {'name': 'XR-2', 'x': 4480, 'y': 0, 'width': 1920, 'height': 1080, 'scale': 1.0},
            {'name': 'XR-3', 'x': 6400, 'y': 0, 'width': 1920, 'height': 1080, 'scale': 1.0}]
OUTPUTS = resize.outputs_from_monitors(MONITORS, NAMES)
X1, X2, X3 = 2560, 4480, 6400
PILL1 = (X1 + 960, 20)  # top-centre of XR-1
ASPECTS = [16 / 9] * 3
FLAT = [dict(DEFAULT_PLACEMENT) for _ in range(3)]
BIND_ARGS = ('/usr/bin/python3', '/x/tools/native_spike/xr_control.py', '/run/user/1000/c.sock', 'a' * 32, 'e' * 64)


def frames(data, ops=None):
    out = [json.loads(line) for line in (data or b'').decode().splitlines()]
    return [f for f in out if ops is None or f['op'] in ops]


class Clock:
    def __init__(self): self.now = 100.0
    def __call__(self): return self.now


class MemoryPlacements:
    def __init__(self):
        self.saved = [dict(DEFAULT_PLACEMENT) for _ in range(3)]
        self.saves = 0
    def load(self): return [dict(p) for p in self.saved]
    def save(self, placements): self.saved = [dict(p) for p in placements]; self.saves += 1


class StripTests(unittest.TestCase):
    def test_top_centre_strip_of_each_desktop(self):
        z = resize.zone_size(1920, 1080)
        for pointer, expected in {PILL1: 0, (X1 + 960 - 280, 0): 0, (X1 + 960 + 287, z - 1): 0, (X2 + 960, 5): 1, (X3 + 900, 40): 2}.items():
            with self.subTest(pointer): self.assertEqual(resize.pill_at(pointer, OUTPUTS), expected)
        for pointer in ((X1 + 960, z), (X1 + 960 - 300, 5), (X1 + 960, 540), (100, 5), None):
            with self.subTest(pointer): self.assertIsNone(resize.pill_at(pointer, OUTPUTS))

    def test_corners_win_and_the_two_never_share_a_pixel(self):
        self.assertEqual(resize.target_at(PILL1, OUTPUTS), ('pill', 0))
        self.assertEqual(resize.target_at((X1 + 3, 3), OUTPUTS), ('corner', 0, 'tl'))
        self.assertIsNone(resize.target_at((X1 + 960, 540), OUTPUTS))
        narrow = resize.outputs_from_monitors([dict(MONITORS[1], width=640, height=480)], NAMES)
        z = resize.zone_size(640, 480)
        for lx in range(0, 640, 2):  # a small desktop: the strip shrinks, it never reaches a corner zone
            for ly in (0, z - 1):
                hit = resize.target_at((X1 + lx, ly), narrow)
                if lx < z or lx >= 640 - z: self.assertEqual(hit and hit[0], 'corner')
                elif hit is not None: self.assertEqual(hit, ('pill', 0))
        self.assertEqual(resize.pill_at((X1 + 320, 3), narrow), 0)


class MoveMathTests(unittest.TestCase):
    def test_directions_gain_clamp_and_snap(self):
        self.assertEqual(resize.move_offset((0, 0), 0, 0, 1920, 1080), (0, 0))
        self.assertEqual(resize.move_offset((0, 0), 960, 0, 1920, 1080), (100, 0))     # half its width: a full width right
        self.assertEqual(resize.move_offset((0, 0), 0, 270, 1920, 1080), (0, -50))     # down is -y
        self.assertEqual(resize.move_offset((0, 0), -192, -108, 1920, 1080), (-20, 20))
        self.assertEqual(resize.move_offset((0, 0), 99999, -99999, 1920, 1080), (PLACEMENT_BOUNDS['x'][1], PLACEMENT_BOUNDS['y'][1]))
        self.assertEqual(resize.move_offset((10, 0), -60, 0, 1920, 1080), (0, 0))      # 4 % from the slot: snaps
        self.assertEqual(resize.move_offset((10, 0), -38, 0, 1920, 1080), (6, 0))      # 6 %: free
        self.assertEqual(resize.move_offset((10, 0), -9, 0, 1920, 1080), (9, 0))

    def test_overview_rectangles_and_overlap(self):
        rects = resize.apparent_rects(ASPECTS, FLAT)
        self.assertAlmostEqual(rects[1][0], 1.5 * 16 / 9)
        self.assertTrue(all(resize.overlap_ok(i, FLAT, ASPECTS) for i in range(3)))
        pushed = [{**FLAT[0], 'x': 50}] + FLAT[1:]
        self.assertFalse(resize.overlap_ok(0, pushed, ASPECTS))
        self.assertTrue(resize.overlap_ok(0, [{**FLAT[0], 'x': 8}] + FLAT[1:], ASPECTS))  # a 10 % touch is allowed
        self.assertTrue(resize.overlap_ok(0, [{**FLAT[0], 'x': 60, 'y': 100}] + FLAT[1:], ASPECTS))  # above the row: free
        far = [{**FLAT[0], 'x': 50, 'distance': 200}] + FLAT[1:]  # half size: its overlap is smaller
        self.assertLess(resize._covered(0, far, ASPECTS), resize._covered(0, pushed, ASPECTS))

    def test_allowed_move_blocks_slides_and_never_traps(self):
        self.assertEqual(resize.allowed_move(0, (0, 0), (0, 80), FLAT, ASPECTS), (0, 80))
        x, y = resize.allowed_move(0, (0, 0), (60, 0), FLAT, ASPECTS)
        self.assertEqual(y, 0); self.assertLess(x, 15); self.assertGreater(x, 0)  # stops at the neighbour
        x, y = resize.allowed_move(0, (0, 0), (60, 30), FLAT, ASPECTS)  # slides: all the way up, x as far as it may
        self.assertEqual(y, 30); self.assertGreater(x, 10); self.assertLess(x, 20)
        lifted = [{**FLAT[0], 'y': 100}] + FLAT[1:]
        self.assertEqual(resize.allowed_move(0, (0, 100), (100, 100), lifted, ASPECTS), (100, 100))  # over the row: free
        stuck = [{**FLAT[0], 'x': 60}] + FLAT[1:]  # overlapping already (set from the panel): can still get out
        self.assertEqual(resize.allowed_move(0, (60, 0), (0, 0), stuck, ASPECTS), (0, 0))
        self.assertEqual(resize.allowed_move(0, (60, 0), (100, 0), stuck, ASPECTS)[0], 60)  # but not deeper in


class PillStateTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.clock, self.store = Clock(), MemoryPlacements()
        self.control = view.ViewControl(view.ViewState(NAMES), 'run', 'session', lambda n: None, LayoutStore(Path(tmp.name) / 'l.json'),
                                        None, placements=self.store, clock=self.clock)
        self.addCleanup(self.control.join_focus, 2)
        self.r = self.control.resize = resize.CornerResize(self.control, clock=self.clock)
        self.addCleanup(self.r.close)

    def at(self, x, y, advance=0.02, ops=None):
        self.clock.now += advance
        self.r.feed((x, y), OUTPUTS)
        return frames(self.control.poll(), ops)

    def op(self, name, ops=None):
        return frames(self.control.on_message({'op': name, 'payload': {}}), ops)

    def test_pills_show_in_the_overview_and_brighten_on_hover(self):
        first = self.at(X1 + 960, 540)
        self.assertEqual([f['op'] for f in first], ['move_handle'])
        self.assertEqual(first[0]['payload'], {'visible': True, 'desktop': 0, 'hover': False, 'active': False})
        self.assertFalse(self.r.binds_wanted)
        hover = self.at(*PILL1)
        self.assertEqual(hover, [{**hover[0], 'payload': {'visible': True, 'desktop': 0, 'hover': True, 'active': False}}])
        self.assertTrue(self.r.binds_wanted); self.assertFalse(self.r.grab_wanted)
        self.assertEqual(self.at(PILL1[0] + 5, PILL1[1]), [])  # still on it: nothing new, nothing moved
        self.assertEqual(self.at(X2 + 960, 10)[0]['payload'], {'visible': True, 'desktop': 1, 'hover': True, 'active': False})
        self.assertEqual(self.at(X2 + 960, 600)[0]['payload']['hover'], False)
        self.assertEqual(self.store.saves, 0)

    def test_drag_moves_live_hides_the_cursor_and_saves_on_release(self):
        self.at(*PILL1)
        pressed = self.op('resize_press', ops=('move_handle',))
        self.assertEqual(pressed[-1]['payload'], {'visible': True, 'desktop': 0, 'hover': True, 'active': True})
        self.assertTrue(self.r.grab_wanted); self.assertTrue(self.r.binds_wanted)
        out = self.at(PILL1[0], PILL1[1] + 108)  # down 10 % of its height
        self.assertEqual([f['op'] for f in out], ['placement'])
        self.assertEqual(out[0]['payload']['desktops'][0], {**DEFAULT_PLACEMENT, 'y': -20})
        self.assertEqual(self.store.saves, 0); self.assertIsNotNone(self.control.save_due)
        self.at(PILL1[0], PILL1[1] + 2000)   # a warp back to the middle: ignored, not a move
        self.assertEqual(self.control.state.placements[0]['y'], -20)
        self.at(PILL1[0] - 192, PILL1[1] + 2000 - 324)  # then up and left from there: keeps adding up
        self.assertEqual(self.control.state.placements[0], {**DEFAULT_PLACEMENT, 'x': -20, 'y': 40})
        released = self.op('resize_release', ops=('move_handle',))
        self.assertEqual(released[-1]['payload']['active'], False)
        self.assertFalse(self.r.grab_wanted)
        self.assertEqual(self.store.saves, 1)
        self.assertEqual(self.store.saved[0], {**DEFAULT_PLACEMENT, 'x': -20, 'y': 40})
        self.assertEqual(self.control.status_reply({})['placements'][0]['y'], 40)  # panel sync
        self.assertEqual(self.r.moves, 1); self.assertEqual(self.r.drags, 0)

    def test_drag_stops_at_a_neighbour_but_slides_over_it(self):
        self.at(*PILL1); self.op('resize_press')
        self.at(PILL1[0] + 300, PILL1[1])
        self.at(PILL1[0] + 600, PILL1[1])
        x = self.control.state.placements[0]['x']
        self.assertGreater(x, 0); self.assertLess(x, 15)
        self.at(PILL1[0] + 600, PILL1[1] - 300)  # half lifted: still mostly in the row, so it may not cross XR-2 yet
        self.assertLess(self.control.state.placements[0]['x'], 30)
        self.at(PILL1[0] + 600, PILL1[1] - 600)  # clear of the row: now it may pass over XR-2
        self.at(PILL1[0] + 900, PILL1[1] - 600)
        self.assertGreater(self.control.state.placements[0]['x'], 50)
        self.assertGreater(self.control.state.placements[0]['y'], 50)
        self.assertTrue(resize.overlap_ok(0, self.control.state.placements, ASPECTS))

    def test_corner_and_pill_never_both(self):
        corner = self.at(X1 + 3, 3, ops=('resize_handle', 'move_handle'))
        handle = [f for f in corner if f['op'] == 'resize_handle']
        self.assertEqual(handle[0]['payload']['corner'], 'tl')
        self.assertFalse(any(f['payload'].get('hover') for f in corner if f['op'] == 'move_handle'))
        self.op('resize_press')
        self.assertEqual(self.r.drag['kind'], 'corner'); self.assertFalse(self.r.grab_wanted)
        self.op('resize_release')
        self.at(*PILL1); self.op('resize_press')
        self.assertEqual(self.r.drag['kind'], 'pill')
        self.at(PILL1[0], PILL1[1] + 300)
        self.assertEqual(self.control.state.placements[0]['distance'], 100)  # a move never resizes
        self.assertEqual(self.r.shown['visible'], False)  # and shows no corner handle

    def test_off_while_the_menu_is_open_or_zoomed(self):
        self.at(*PILL1)
        self.control.on_message({'op': 'menu_toggle', 'payload': {}})
        hidden = self.at(PILL1[0] + 1, PILL1[1], ops=('move_handle',))
        self.assertEqual(hidden[-1]['payload'], {'visible': False, 'desktop': 0, 'hover': False, 'active': False})
        self.assertEqual(self.op('resize_press'), [])
        self.assertIsNone(self.r.drag); self.assertFalse(self.r.binds_wanted)
        self.control.on_message({'op': 'menu_toggle', 'payload': {}})
        self.assertTrue(self.at(*PILL1, ops=('move_handle',))[-1]['payload']['visible'])
        self.op('resize_press'); self.at(PILL1[0], PILL1[1] + 100)
        self.control.on_message({'op': 'zoom_in', 'payload': {}})
        out = self.at(PILL1[0], PILL1[1] + 400, ops=('move_handle',))
        self.assertEqual(out[-1]['payload']['visible'], False)
        self.assertIsNone(self.r.drag); self.assertFalse(self.r.grab_wanted)
        self.assertEqual(self.store.saves, 1)  # zooming ended the drag and saved it

    def test_idle_hides_the_pills_and_movement_brings_them_back(self):
        self.at(X1 + 960, 540)
        self.clock.now += view.OVERLAY_IDLE_CLOSE_S + 0.01
        hidden = frames(self.control.poll(), ('move_handle',))
        self.assertEqual(hidden[-1]['payload']['visible'], False)
        self.assertTrue(self.at(X1 + 961, 540, ops=('move_handle',))[-1]['payload']['visible'])

    def test_resize_ops_are_unchanged_so_video_and_menu_gates_cover_the_pill(self):
        self.assertEqual(resize.RESIZE_OPS, ('resize_press', 'resize_release'))
        self.assertEqual(xr_control.OPS[-2:], resize.RESIZE_OPS)
        self.assertFalse(any('move' in op or 'pill' in op for op in xr_control.OPS))


class LuaTests(unittest.TestCase):
    def test_bind_removal_also_brings_a_hidden_cursor_back(self):
        self.assertIn(resize.GRAB_LEAVE_LUA, resize.remove_resize_binds_lua())
        self.assertEqual(xr_control.REMOVE_RESIZE_BINDS, resize.remove_resize_binds_lua())
        self.assertIn(resize.GRAB_LEAVE_LUA, resize.install_resize_binds_lua(*BIND_ARGS))  # a reinstall starts clean

    def test_cursor_lua_is_fixed_guarded_and_separate_from_mouse_mode(self):
        for lua in (resize.GRAB_ENTER_LUA, resize.GRAB_RECENTRE_LUA, resize.GRAB_LEAVE_LUA):
            self.assertNotIn('exec', lua); self.assertNotIn('os.', lua); self.assertNotIn('io.', lua)
            self.assertNotIn('xr_workspace_mouse', lua)  # the menu's mouse mode keeps its own saved state
            self.assertEqual(lua.count('hl.dispatch('), lua.count('pcall(function() hl.dispatch('))
            self.assertLess(len(lua), 1024)
        self.assertIn('if xr_workspace_grab == nil then', resize.GRAB_ENTER_LUA)  # home saved once per drag
        self.assertNotIn('xr_workspace_grab', resize.GRAB_RECENTRE_LUA)

    def test_hyprland_client_sends_exactly_that_lua(self):
        from tools.native_spike import hyprland as h
        client = h.HyprlandClient('f' * 40 + '_1793506481_1554975112', Path('/nonexistent'))
        sent = []
        client._request = lambda wire: sent.append(wire) or b'ok'
        client.grab_cursor(); client.recentre_cursor(); client.release_cursor()
        self.assertEqual(sent, ['/eval ' + resize.GRAB_ENTER_LUA, '/eval ' + resize.GRAB_RECENTRE_LUA, '/eval ' + resize.GRAB_LEAVE_LUA])


class FakeClient:
    def __init__(self):
        self.pointer, self.calls, self.lock = PILL1, [], threading.Lock()
    def query(self, kind, argument=None, timeout=2.0):
        with self.lock: self.calls.append(kind)
        if kind == 'monitors': return MONITORS
        return {'x': self.pointer[0], 'y': self.pointer[1]}
    def _call(self, name):
        with self.lock: self.calls.append(name)
    def install_resize_binds(self, *a): self._call('install')
    def remove_resize_binds(self): self._call('remove')
    def grab_cursor(self): self._call('grab')
    def recentre_cursor(self): self._call('recentre')
    def release_cursor(self): self._call('release')
    def count(self, name):
        with self.lock: return self.calls.count(name)


class PollerGrabTests(unittest.TestCase):
    def wait(self, predicate, timeout=2.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate(): return True
            time.sleep(0.005)
        return predicate()

    def test_grab_hides_recentres_near_edges_and_releases(self):
        control = view.ViewControl(view.ViewState(NAMES), 'run', 'session', None, None, None)
        r = control.resize = resize.CornerResize(control); self.addCleanup(r.close)
        client = FakeClient()
        poller = resize.PointerPoller(client, NAMES, r, BIND_ARGS, period=0.005).start()
        self.assertTrue(self.wait(lambda: r._sample[0] == PILL1)); control.poll()
        self.assertTrue(self.wait(lambda: client.count('install') == 1))
        control.on_message({'op': 'resize_press', 'payload': {}})
        self.assertTrue(r.grab_wanted)
        self.assertTrue(self.wait(lambda: client.count('grab') == 1))
        self.assertTrue(self.wait(lambda: client.count('recentre') >= 1))  # PILL1 is 20 px from the top edge
        client.pointer = (X1 + 960, 540)  # in the middle: no more re-centring
        time.sleep(0.05); n = client.count('recentre'); time.sleep(0.05)
        self.assertEqual(client.count('recentre'), n)
        control.on_message({'op': 'resize_release', 'payload': {}})
        self.assertTrue(self.wait(lambda: client.count('release') == 1))
        poller.stop()
        self.assertEqual(client.count('grab'), 1)

    def test_stop_mid_drag_puts_the_cursor_back_through_the_bind_removal(self):
        control = view.ViewControl(view.ViewState(NAMES), 'run', 'session', None, None, None)
        r = control.resize = resize.CornerResize(control); self.addCleanup(r.close)
        client = FakeClient()
        poller = resize.PointerPoller(client, NAMES, r, BIND_ARGS, period=0.005).start()
        self.assertTrue(self.wait(lambda: r._sample[0] == PILL1)); control.poll()
        control.on_message({'op': 'resize_press', 'payload': {}})
        self.assertTrue(self.wait(lambda: poller.grabbed))
        poller.stop()
        self.assertGreaterEqual(client.count('remove'), 1)  # remove_resize_binds_lua carries GRAB_LEAVE_LUA
        self.assertFalse(poller.grabbed)

    def test_dead_run_release_click_restores_the_cursor(self):
        with mock.patch.object(xr_control, 'drop_stale_menu_binds') as drop:
            self.assertEqual(xr_control.main(['x', '/nonexistent/c.sock', 'run', 's', 'resize_release']), 1)
        drop.assert_called_once_with(code=xr_control.REMOVE_RESIZE_BINDS)
        self.assertIn('xr_workspace_grab', xr_control.REMOVE_RESIZE_BINDS)


if __name__ == '__main__':
    unittest.main()
