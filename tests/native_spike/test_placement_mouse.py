"""Placement menu mouse mode: sliders driven by a free, smooth pointer in the glasses (Ctrl+Alt+M while the menu is open).

The pointer lives in menu-card pixels and moves 1:1 with the cursor's motion; clicks are hit-tested against the card
layout the renderer reports (`menu_geometry`). Offline only: fake cursor samples, a fixed card layout, fake bind
callables, Lua checked as text and (when Hyprland is installed) through `Hyprland --verify-config` on a throwaway
config. No compositor, renderer or glasses.
"""
import json
from pathlib import Path
import shutil
import socket
import tempfile
import threading
import time
import unittest
from unittest import mock

from tools.native_spike import bind_check, view, xr_control
from tools.native_spike.geometry import DEFAULT_PLACEMENT, PLACEMENT_BOUNDS, PLACEMENT_KEYS
from tools.native_spike.ipc import decode_control

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).with_name('fixtures') / 'placement_menu_payloads.json'
NAMES = ['XR-1', 'XR-2', 'XR-3']
ARGS = ('/usr/bin/python3', '/home/cody/.t3/worktrees/viture-workspace/feature-placement-menu-integrate/tools/native_spike/xr_control.py',
        '/run/user/1000/xr-workspace-spike/' + 'a' * 32 + '/control.sock', 'a' * 32, 'e' * 64)
HYPRLAND = shutil.which('Hyprland')
# A status line exactly as the renderer writes it (spike/src/hud.cpp menu_geometry_json): a 1229x733 card, three tabs,
# seven sliders 64 px apart whose knobs travel from x 780 to 1020.
GEOMETRY_LINE = ('{"menu_geometry":{"w":1229,"h":733,"tabs":[[492,92,579,148],[579,92,667,148],[667,92,754,148]],"sliders":['
                 + ','.join(f'[{i},{180 + 64 * i},{244 + 64 * i},780,1020]' for i in range(7)) + ']}}')
GEOMETRY = view.parse_menu_geometry(json.loads(GEOMETRY_LINE)['menu_geometry'])


def frames(data):
    return [json.loads(line) for line in (data or b'').decode().splitlines()]


class Clock:
    def __init__(self): self.now = 100.0
    def __call__(self): return self.now


class Store:
    def __init__(self): self.saved, self.saves = [dict(DEFAULT_PLACEMENT) for _ in range(3)], 0
    def load(self): return [dict(p) for p in self.saved]
    def save(self, placements): self.saved, self.saves = [dict(p) for p in placements], self.saves + 1


class Cursor:
    """Hyprland cursorpos stand-in; warp() moves it like hl.dsp.cursor.move."""
    def __init__(self, x=1000, y=500): self.x, self.y, self.calls, self.warps = x, y, 0, []
    def __call__(self):
        self.calls += 1
        return {'x': self.x, 'y': self.y}
    def warp(self, x, y):
        self.warps.append((x, y)); self.x, self.y = x, y


def control(test, names=NAMES, clock=None, store=None, geometry=GEOMETRY, **kw):
    c = view.ViewControl(view.ViewState(names), 'run', 'session', lambda n: None, mock.Mock(), None, placements=store,
                         clock=clock or Clock(), **kw)
    test.addCleanup(c.stop_menu_binds, 1)
    test.addCleanup(c.join_focus, 2)
    if geometry is not None: c.set_menu_geometry(geometry)
    return c


def send(c, op):
    return frames(c.on_message({'op': op, 'payload': {}}))


def of(out, op):
    found = [f['payload'] for f in out if f['op'] == op]
    return found[-1] if found else None


def move(c, dx=0.0, dy=0.0, now=None):
    """Hand the loop cursor motion (as the sampler would) and tick it once."""
    with c._motion_lock:
        c._motion[0] += dx; c._motion[1] += dy
    return frames(c.poll(now))


def slider(field):
    return next(sl for sl in GEOMETRY['sliders'] if sl[0] == PLACEMENT_KEYS.index(field))


def to(c, x, y):
    """Move the pointer to card pixel (x, y) by relative motion."""
    m = c.state.mouse
    return move(c, x - m['x'], y - m['y'])


class MouseModeStateTests(unittest.TestCase):
    def test_ctrl_alt_m_works_only_with_the_menu_open_and_toggles(self):
        c = control(self)
        self.assertEqual(send(c, 'menu_mouse'), [])  # menu closed: nothing (the bind only exists while it is open)
        self.assertIsNone(c.state.mouse)
        send(c, 'menu_toggle')
        on = send(c, 'menu_mouse')
        self.assertEqual([f['op'] for f in on], ['menu_pointer'])  # the card itself does not change
        lo_x, hi_x = slider('x')[3:]
        self.assertEqual(of(on, 'menu_pointer'), {'visible': True, 'x': (lo_x + hi_x) // 2, 'y': (180 + 244) // 2, 'held': False})
        off = send(c, 'menu_mouse')
        self.assertEqual(of(off, 'menu_pointer'), {'visible': False, 'x': 0, 'y': 0, 'held': False})

    def test_pointer_starts_on_the_selected_knob(self):
        c = control(self)
        c.state.placements[0]['distance'] = 200
        send(c, 'menu_toggle'); send(c, 'menu_field_next'); send(c, 'menu_field_next')  # distance
        sl = slider('distance')
        self.assertEqual(of(send(c, 'menu_mouse'), 'menu_pointer'), {'visible': True, 'x': sl[4], 'y': (sl[1] + sl[2]) // 2, 'held': False})

    def test_pointer_waits_for_the_layout(self):
        c = control(self, geometry=None)
        send(c, 'menu_toggle')
        self.assertEqual(send(c, 'menu_mouse'), [])  # no layout reported yet: nothing to show, clicks do nothing
        self.assertEqual(send(c, 'menu_mouse_press'), [])
        c.set_menu_geometry(GEOMETRY)
        out = frames(c.poll())
        self.assertTrue(of(out, 'menu_pointer')['visible'])
        smaller = dict(GEOMETRY, w=500, h=300, sliders=GEOMETRY['sliders'][:2])  # e.g. a smaller output
        c.set_menu_geometry(smaller)
        p = of(frames(c.poll()), 'menu_pointer')
        self.assertLessEqual(p['x'], 499); self.assertLessEqual(p['y'], 299)  # kept on the card

    def test_escape_leaves_mouse_mode_first_then_closes(self):
        c = control(self, store=Store())
        send(c, 'menu_toggle'); send(c, 'menu_mouse')
        first = send(c, 'menu_back')
        self.assertFalse(of(first, 'menu_pointer')['visible']); self.assertIsNone(of(first, 'placement_menu'))
        self.assertFalse(of(send(c, 'menu_back'), 'placement_menu')['open'])
        self.assertEqual(send(c, 'menu_back'), [])  # closed: nothing (a late key)

    def test_ctrl_alt_l_closes_the_menu_and_mouse_mode_together(self):
        c = control(self)
        send(c, 'menu_toggle'); send(c, 'menu_mouse')
        out = send(c, 'menu_toggle')
        self.assertFalse(of(out, 'placement_menu')['open']); self.assertFalse(of(out, 'menu_pointer')['visible'])
        self.assertIsNone(c.state.mouse)
        self.assertNotIn('menu_pointer', [f['op'] for f in send(c, 'menu_toggle')])  # reopening is keyboard mode again

    def test_mouse_ops_are_ignored_outside_mouse_mode(self):
        c = control(self)
        send(c, 'menu_toggle')
        before = c.state.menu()
        for op in ('menu_mouse_press', 'menu_mouse_release'):
            self.assertEqual(send(c, op), [])
        self.assertEqual(c.state.menu(), before)

    def test_keyboard_still_works_in_mouse_mode(self):
        c = control(self)
        send(c, 'menu_toggle'); send(c, 'menu_mouse')
        out = send(c, 'menu_inc_big')
        self.assertEqual([f['op'] for f in out], ['placement', 'placement_menu'])  # the pointer stays where it is
        self.assertEqual(c.state.placements[0]['x'], 10)
        send(c, 'menu_field_next'); send(c, 'menu_desktop_next'); send(c, 'menu_reset')
        self.assertEqual((c.state.menu_desktop, c.state.menu_field), (1, 'y'))
        self.assertIsNotNone(c.state.mouse)


class PointerTests(unittest.TestCase):
    """The pointer is free and smooth: card pixels, 1:1 with the cursor, never snapped to a row or a slider."""
    def setUp(self):
        self.clock = Clock(); self.store = Store()
        self.c = control(self, clock=self.clock, store=self.store)
        send(self.c, 'menu_toggle'); send(self.c, 'menu_mouse')

    def test_motion_moves_the_pointer_one_to_one_anywhere_on_the_card(self):
        start = self.c.state.pointer()
        path = [(3, 0), (0, 1), (-7, 5), (13, -2), (0.4, 0.4), (0.4, 0.4)]  # tiny moves count: sub-pixel motion adds up
        x, y = start['x'], start['y']
        for dx, dy in path:
            out = move(self.c, dx, dy)
            x, y = x + dx, y + dy
            self.assertEqual([f['op'] for f in out], ['menu_pointer'] if out else [])
        p = self.c.state.pointer()
        self.assertEqual((p['x'], p['y']), (round(x), round(y)))
        for target in ((5, 5), (100, 40), (1200, 700), (614, 366)):  # labels, title, corners: everywhere is reachable
            p = of(to(self.c, *target), 'menu_pointer')
            self.assertEqual((p['x'], p['y']), target)

    def test_hovering_changes_nothing_but_the_pointer(self):
        before = (self.c.state.menu(), [dict(p) for p in self.c.state.placements])
        for _ in range(20): out = move(self.c, 11, 7)
        self.assertEqual({f['op'] for f in out}, {'menu_pointer'})
        self.assertEqual((self.c.state.menu(), self.c.state.placements), before)

    def test_the_pointer_stays_on_the_card(self):
        move(self.c, -5000, -5000)
        self.assertEqual(self.c.state.pointer()['x'], 0); self.assertEqual(self.c.state.pointer()['y'], 0)
        move(self.c, 1e6, 1e6)
        self.assertEqual((self.c.state.pointer()['x'], self.c.state.pointer()['y']), (1228, 732))
        out = move(self.c, -10, 0)
        self.assertEqual(of(out, 'menu_pointer')['x'], 1218)  # no build-up past the edge: it comes straight back

    def test_one_pointer_frame_per_tick_with_the_latest_position(self):
        with self.c._motion_lock: self.c._motion = [40.0, 4.0]
        out = frames(self.c.poll())
        self.assertEqual([f['op'] for f in out], ['menu_pointer'])
        self.assertIsNone(self.c.poll())  # nothing new: nothing sent

    def test_click_on_a_slider_snaps_it_to_the_pointer(self):
        sl = slider('yaw')
        to(self.c, sl[3] + (sl[4] - sl[3]) / 4, sl[1] + 5)  # a quarter along, near the top of the row
        out = send(self.c, 'menu_mouse_press')
        self.assertEqual([f['op'] for f in out], ['placement', 'placement_menu', 'menu_pointer'])
        lo, hi = PLACEMENT_BOUNDS['yaw']
        self.assertEqual(out[0]['payload']['desktops'][0]['yaw'], lo + (hi - lo) // 4)
        self.assertEqual(of(out, 'placement_menu')['field'], 'yaw')  # the clicked row is selected
        self.assertTrue(of(out, 'menu_pointer')['held'])
        self.assertIsNotNone(self.c.save_due)

    def test_click_off_any_slider_does_nothing(self):
        before = (self.c.state.menu(), [dict(p) for p in self.c.state.placements])
        for x, y in ((100, slider('yaw')[1] + 20), (900, 20), (900, 700), (slider('x')[3] - 30, slider('x')[1] + 10)):
            to(self.c, x, y)
            self.assertEqual(send(self.c, 'menu_mouse_press'), [], (x, y))
            send(self.c, 'menu_mouse_release')
        self.assertEqual((self.c.state.menu(), self.c.state.placements), before)

    def test_every_field_reaches_both_bounds_and_the_ends_have_slop(self):
        for key in PLACEMENT_KEYS:
            sl = slider(key)
            for end, x in ((0, sl[3] - view.MOUSE_SLOP_PX), (1, sl[4] + view.MOUSE_SLOP_PX)):
                with self.subTest(key=key, end=end):
                    to(self.c, x, sl[2] - 2)
                    send(self.c, 'menu_mouse_press'); send(self.c, 'menu_mouse_release')
                    self.assertEqual(self.c.state.placements[0][key], PLACEMENT_BOUNDS[key][end])

    def test_drag_follows_the_pointer_live_until_release(self):
        sl = slider('curvature')
        to(self.c, sl[3], (sl[1] + sl[2]) / 2)
        send(self.c, 'menu_mouse_press')
        values = []
        for _ in range(4):
            out = move(self.c, 24, 100)  # vertical motion moves the pointer freely; the held slider keeps its value from x
            self.assertEqual([f['op'] for f in out], ['placement', 'placement_menu', 'menu_pointer'])
            values.append(out[0]['payload']['desktops'][0]['curvature'])
        self.assertEqual(values, sorted(values)); self.assertEqual(len(set(values)), 4)
        self.assertEqual(values[-1], round(96 / (sl[4] - sl[3]) * 120))
        self.assertFalse(of(send(self.c, 'menu_mouse_release'), 'menu_pointer')['held'])
        held = self.c.state.placements[0]['curvature']
        out = move(self.c, 30, 0)
        self.assertEqual([f['op'] for f in out], ['menu_pointer'])
        self.assertEqual(self.c.state.placements[0]['curvature'], held)

    def test_drag_past_the_end_clamps(self):
        sl = slider('pitch')
        to(self.c, (sl[3] + sl[4]) / 2, sl[1] + 3)
        send(self.c, 'menu_mouse_press')
        move(self.c, 5000, 0)
        self.assertEqual(self.c.state.placements[0]['pitch'], PLACEMENT_BOUNDS['pitch'][1])
        move(self.c, -5000, 0)
        self.assertEqual(self.c.state.placements[0]['pitch'], PLACEMENT_BOUNDS['pitch'][0])

    def test_drag_saves_like_the_keys_do(self):
        sl = slider('x')
        to(self.c, sl[3], sl[1] + 1)
        send(self.c, 'menu_mouse_press')
        move(self.c, 10, 0)
        self.c.poll(self.clock.now + view.MENU_SAVE_DELAY)
        self.assertEqual(self.store.saves, 1)
        self.assertEqual(self.store.saved[0]['x'], self.c.state.placements[0]['x'])

    def test_click_on_a_desktop_tab_picks_it(self):
        for desktop in (2, 1, 0):
            x0, y0, x1, y1 = GEOMETRY['tabs'][desktop]
            to(self.c, (x0 + x1) / 2, (y0 + y1) / 2)
            out = send(self.c, 'menu_mouse_press')
            self.assertEqual(of(out, 'placement_menu')['desktop'], desktop)
            self.assertNotIn('placement', [f['op'] for f in out])  # selecting a desktop moves nothing
            send(self.c, 'menu_mouse_release')

    def test_tab_ignores_a_desktop_this_run_does_not_have(self):
        c = control(self, names=['XR-1', 'XR-2'])
        send(c, 'menu_toggle'); send(c, 'menu_mouse')
        x0, y0, x1, y1 = GEOMETRY['tabs'][2]
        to(c, (x0 + x1) / 2, (y0 + y1) / 2)
        self.assertEqual(send(c, 'menu_mouse_press'), [])
        self.assertEqual(c.state.menu_desktop, 0)

    def test_frames_are_valid_for_the_renderer(self):
        sl = slider('roll')
        to(self.c, sl[3] + 33.3, sl[1] + 9.7)
        send(self.c, 'menu_mouse_press')
        for _ in range(3): move(self.c, 37.3, 11)
        for payload in (self.c.state.pointer(), self.c.state.menu()):
            op = 'menu_pointer' if 'held' in payload else 'placement_menu'
            frame = (view.menu_pointer_frame if op == 'menu_pointer' else view.placement_menu_frame)('run', 'session', 9, payload)
            self.assertEqual(json.loads(frame)['payload'], payload)
        self.assertEqual(type(self.c.state.pointer()['x']), int)


class ActivityTests(unittest.TestCase):
    """Any mouse input in mouse mode counts as activity for the overlay idle close."""
    IDLE = view.OVERLAY_IDLE_CLOSE_S

    def test_motion_keeps_the_menu_up_and_stillness_closes_it(self):
        clock = Clock(); calls = []
        c = control(self, clock=clock, menu_binds=lambda on: calls.append(('menu', on)), mouse_binds=lambda on: calls.append(('mouse', on)))
        send(c, 'menu_toggle'); send(c, 'menu_mouse')
        for _ in range(3):
            clock.now += self.IDLE - 1
            move(c, dx=1, now=clock.now)
            self.assertTrue(c.state.menu_open)
        self.assertIsNone(c.poll(clock.now + self.IDLE - 0.01))
        out = frames(c.poll(clock.now + self.IDLE))
        self.assertFalse(of(out, 'placement_menu')['open']); self.assertFalse(of(out, 'menu_pointer')['visible'])
        self.assertIsNone(c.state.mouse)
        c.join_focus(2)
        self.assertEqual(calls[-2:], [('mouse', False), ('menu', False)])  # cursor back, then the menu keys released

    def test_clicks_count_as_activity(self):
        clock = Clock(); c = control(self, clock=clock)
        send(c, 'menu_toggle'); send(c, 'menu_mouse')
        clock.now += self.IDLE - 1
        send(c, 'menu_mouse_press'); send(c, 'menu_mouse_release')
        self.assertIsNone(c.poll(clock.now + self.IDLE - 0.01))
        self.assertTrue(c.state.menu_open)

    def test_a_still_cursor_does_not_count(self):
        clock = Clock(); c = control(self, clock=clock)
        send(c, 'menu_toggle'); send(c, 'menu_mouse')
        for _ in range(5): move(c, 0, 0, now=clock.now + 1)
        self.assertFalse(of(frames(c.poll(clock.now + self.IDLE)), 'placement_menu')['open'])


class WiringTests(unittest.TestCase):
    def test_binds_follow_mouse_mode_in_order_off_the_loop(self):
        calls = []
        c = control(self, menu_binds=lambda on: calls.append(('menu', on)), mouse_binds=lambda on: calls.append(('mouse', on)))
        send(c, 'menu_toggle'); c.join_focus(2)
        send(c, 'menu_mouse'); c.join_focus(2)
        send(c, 'menu_mouse'); c.join_focus(2)
        send(c, 'menu_mouse'); c.join_focus(2)
        send(c, 'menu_toggle'); c.join_focus(2)
        self.assertEqual(calls, [('menu', True), ('mouse', True), ('mouse', False), ('mouse', True), ('mouse', False), ('menu', False)])

    def test_a_failed_mouse_bind_change_never_breaks_the_menu(self):
        def boom(on): raise OSError('busy')
        menu = []
        c = control(self, menu_binds=menu.append, mouse_binds=boom)
        send(c, 'menu_toggle'); send(c, 'menu_mouse'); c.join_focus(2)
        self.assertIsNotNone(c.state.mouse)  # the pointer still works; Escape still leaves
        send(c, 'menu_back'); send(c, 'menu_back'); c.join_focus(2)
        self.assertEqual(menu, [True, False])

    def wait(self, cond, timeout=2.0):
        deadline = time.monotonic() + timeout
        while not cond() and time.monotonic() < deadline: time.sleep(0.002)
        return cond()

    def test_sampler_gathers_motion_wakes_the_loop_and_stops_with_mouse_mode(self):
        cursor = Cursor()
        landed = threading.Event()
        def mouse(on):
            if on: cursor.warp(2000, 700); landed.set()  # the install moves the cursor to its monitor's middle
        c = control(self, mouse_binds=mouse, menu_binds=lambda on: None, cursor=cursor, warp=cursor.warp)
        send(c, 'menu_toggle'); send(c, 'menu_mouse'); c.join_focus(2)
        self.assertTrue(landed.wait(2))
        self.assertTrue(self.wait(lambda: cursor.calls >= 3))
        start = c.state.pointer()
        self.assertEqual(c._take_motion(), (0.0, 0.0))  # the warped start is the baseline, not a movement
        c.drain_mouse_wake()
        cursor.x += 17; cursor.y -= 4
        self.assertTrue(self.wait(lambda: c._motion != [0.0, 0.0]))
        import select
        self.assertTrue(select.select([c.mouse_wake], [], [], 1)[0])  # the loop is woken, not left to its 50 ms tick
        p = of(frames(c.poll()), 'menu_pointer')
        self.assertEqual((p['x'] - start['x'], p['y'] - start['y']), (17, -4))
        # Sampling is fast: well over the glasses' 120 Hz.
        n0, t0 = cursor.calls, time.monotonic(); time.sleep(0.2)
        self.assertGreater((cursor.calls - n0) / (time.monotonic() - t0), 120)
        send(c, 'menu_mouse'); c.join_focus(2)
        calls = cursor.calls; time.sleep(0.1)
        self.assertLessEqual(cursor.calls - calls, 1)  # mouse mode off: sampling stops
        c.stop_menu_binds(1)
        self.assertFalse(c._sampler.is_alive())

    def test_the_hidden_cursor_is_recentred_so_no_edge_stops_the_pointer(self):
        cursor = Cursor(1000, 500)
        c = control(self, mouse_binds=lambda on: None, menu_binds=lambda on: None, cursor=cursor, warp=cursor.warp)
        send(c, 'menu_toggle'); send(c, 'menu_mouse'); c.join_focus(2)
        self.assertTrue(self.wait(lambda: cursor.calls >= 2))
        start = c.state.pointer()
        total = 0
        for _ in range(12):  # a long sweep right: 12 x 60 px, far past the re-centring distance
            cursor.x += 60; total += 60
            self.assertTrue(self.wait(lambda: c._motion[0] > 0)); c.poll()
        self.assertTrue(cursor.warps and all(w == (1000, 500) for w in cursor.warps))
        self.assertLessEqual(abs(cursor.x - 1000), view.MOUSE_RECENTRE_PX + 60)
        self.assertEqual(c.state.pointer()['x'] - start['x'], min(total, 1228 - start['x']))  # nothing lost to the warps

    def test_bad_cursor_replies_are_ignored(self):
        replies = iter([OSError('gone'), {'x': 'a', 'y': 1}, {'x': float('nan'), 'y': 1}, [], {'x': 5, 'y': 6}, {'x': 9, 'y': 6}])
        def cursor():
            r = next(replies, {'x': 9, 'y': 6})
            if isinstance(r, Exception): raise r
            return r
        c = control(self, cursor=cursor)
        send(c, 'menu_toggle'); send(c, 'menu_mouse')
        self.assertTrue(self.wait(lambda: c._motion == [4.0, 0.0]))

    def test_cursor_follow_pauses_in_mouse_mode(self):
        c = control(self)
        c.state.cursor_follow = True
        send(c, 'menu_toggle'); send(c, 'menu_mouse')
        self.assertIsNone(c.on_events(b'focusedmon>>XR-2,2\n'))
        self.assertEqual(c.state.selected, 0)
        send(c, 'menu_mouse')
        self.assertIsNotNone(c.on_events(b'focusedmon>>XR-3,3\n'))
        self.assertEqual(c.state.selected, 2)

    def test_reload_reinstalls_mouse_mode_but_never_loops(self):
        calls = []
        c = control(self, menu_binds=lambda on: calls.append(('menu', on)), mouse_binds=lambda on: calls.append(('mouse', on)))
        c.on_reload = lambda: None
        send(c, 'menu_toggle'); send(c, 'menu_mouse'); c.join_focus(2)
        calls.clear()
        c.on_events(b'configreloaded>>\n'); c.join_focus(2)
        self.assertEqual(calls, [('menu', True), ('mouse', True)])
        calls.clear()
        c.on_events(b'configreloaded>>\n'); c.join_focus(2)  # at once again: as if hiding the cursor reloaded the config
        self.assertEqual(calls, [('menu', True)])

    def test_supervisor_wakes_on_motion_and_routes_the_menu_geometry(self):
        from tools.native_spike.supervisor import Supervisor
        child = mock.Mock(); child.poll.return_value = None
        c = control(self, geometry=None, clock=time.monotonic)  # the supervisor ticks poll() with the real clock
        send(c, 'menu_toggle'); send(c, 'menu_mouse')
        a, b = socket.socketpair()
        status_parent, status_child = socket.socketpair(type=socket.SOCK_DGRAM)
        with a, b, status_parent, status_child, socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as dgram:
            c.server = mock.Mock(); c.server.socket = dgram
            sup = Supervisor(child, lambda: 'done', control=c, status_socket=status_parent)
            t = threading.Thread(target=sup.run, args=(a,), kwargs={'interval': 0.1}, daemon=True); t.start()
            status_child.send((GEOMETRY_LINE + '\n').encode())  # the renderer reports the card layout
            b.settimeout(1)
            got = frames(b.recv(65536))
            self.assertTrue(of(got, 'menu_pointer')['visible'])
            self.assertEqual(sup.status, {})  # geometry is not run status
            start = time.monotonic()
            move_at = start
            with c._motion_lock: c._motion[0] += 25
            c._poke()
            got = frames(b.recv(65536))
            latency = time.monotonic() - move_at
            sup.finished = True; t.join(2)
        self.assertFalse(t.is_alive())
        self.assertEqual(of(got, 'menu_pointer')['x'] - (slider('x')[3] + slider('x')[4]) // 2, 25)
        self.assertLess(latency, 0.03)  # woken at once (the loop's own tick is 0.1 s here)

    def test_malformed_geometry_is_ignored(self):
        from tools.native_spike.supervisor import Supervisor
        c = control(self, geometry=None)
        sup = Supervisor(mock.Mock(), lambda: 'done', control=c)
        for bad in ('{"menu_geometry":{"w":0,"h":1,"tabs":[],"sliders":[]}}', '{"menu_geometry":[1]}',
                    GEOMETRY_LINE.replace('[0,180,244,780,1020]', '[0,180,244,1020,780]'),
                    GEOMETRY_LINE.replace('[1,244', '[0,244'), '{"menu_geometry":{},"missed":1}'):
            sup.status_bytes((bad + '\n').encode())
            self.assertIsNone(c.state.geometry, bad)
        sup.status_bytes((GEOMETRY_LINE + '\n').encode())
        self.assertEqual(c.state.geometry, GEOMETRY)
        sup.status_bytes(b'{"presented_hz":119.9,"missed":0}\n')
        self.assertEqual(sup.status, {'presented_hz': 119.9, 'missed': 0})  # ordinary status still works

    def test_managed_run_wires_mouse_binds_the_cursor_query_and_the_warp(self):
        from tools.native_spike import managed, profiles
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp) / 'run'; runtime.mkdir(mode=0o700)
            client = mock.Mock(runtime_dir=runtime, event_stream=None)
            client.query.return_value = []
            store = profiles.ProfileStore(Path(tmp) / 'profiles.json', 'luma-ultra')
            with managed.view_controls(client, 'run', 'session', NAMES, store=store) as c:
                c.on_message({'op': 'menu_toggle'}); c.on_message({'op': 'menu_mouse'}); c.join_focus(2)
                client.install_mouse_binds.assert_called_once()
                self.assertEqual(client.install_mouse_binds.call_args.args, client.install_view_binds.call_args.args)
                self.assertIs(c.warp, client.warp_cursor)
                client.query.return_value = {'x': 1, 'y': 2}
                deadline = time.monotonic() + 2
                while mock.call('cursorpos', timeout=0.25) not in client.query.call_args_list and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertIn(mock.call('cursorpos', timeout=0.25), client.query.call_args_list)
            client.remove_view_binds.assert_called_once()  # its Lua also leaves mouse mode
            self.assertIsNone(c.cursor)


class WireTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.cases = json.loads(FIXTURE.read_text())

    def test_shared_menu_pointer_fixture(self):
        for p in self.cases['menu_pointer_valid']:
            with self.subTest(p=p):
                self.assertEqual(json.loads(view.menu_pointer_frame('run', 'session', 1, p))['payload'], p)
        for case in self.cases['menu_pointer_invalid']:
            with self.subTest(case['why']), self.assertRaises(ValueError):
                view.menu_pointer_frame('run', 'session', 1, case['payload'])

    def test_placement_menu_carries_no_pointer(self):
        menu = view.ViewState(NAMES).menu()
        with self.assertRaises(ValueError): view.placement_menu_frame('run', 'session', 1, {**menu, 'pointer': {'row': 0, 'x': 0, 'drag': False}})

    def test_geometry_parser(self):
        self.assertEqual(GEOMETRY['w'], 1229); self.assertEqual(len(GEOMETRY['sliders']), 7)
        windowed = json.loads(GEOMETRY_LINE)['menu_geometry']; windowed['sliders'] = windowed['sliders'][2:5]
        self.assertEqual([sl[0] for sl in view.parse_menu_geometry(windowed)['sliders']], [2, 3, 4])  # small outputs: a window
        for bad in ({}, {'w': 1, 'h': 1, 'tabs': [[0, 0, 1, 1]] * 3, 'sliders': []}, dict(windowed, w=True),
                    dict(windowed, tabs=[[0, 0, 1, 1]] * 2), dict(windowed, sliders=[[7, 0, 1, 0, 1]]),
                    dict(windowed, sliders=[[0, 5, 5, 0, 1]]), dict(windowed, h=10 ** 6), dict(windowed, extra=1)):
            with self.subTest(bad=bad), self.assertRaises(ValueError): view.parse_menu_geometry(bad)

    def test_control_socket_accepts_the_mouse_ops_without_payload(self):
        for op in ('menu_mouse', 'menu_back', 'menu_mouse_press', 'menu_mouse_release'):
            wire = json.dumps({'schema': 1, 'request_id': 3, 'run_id': 'r', 'session': 's', 'op': op, 'payload': {}}) + '\n'
            self.assertEqual(decode_control(wire.encode())['op'], op)
            with self.assertRaises(ValueError):
                decode_control((wire.replace('"payload": {}', '"payload": {"x": 5}')).encode())
        self.assertEqual((set(view.MOUSE_OPS) | {'menu_mouse', 'menu_back'}) - set(xr_control.OPS), set())
        for op in ('menu_pointer', 'menu_geometry'):  # renderer-bound / renderer-sent only: never on the control socket
            wire = json.dumps({'schema': 1, 'request_id': 3, 'run_id': 'r', 'session': 's', 'op': op, 'payload': {}}) + '\n'
            with self.assertRaises(ValueError): decode_control(wire.encode())

    def test_help_card_names_mouse_mode_and_fits_the_renderer_limit(self):
        row = dict(view.help_rows())['Ctrl+Alt+L']
        self.assertIn('Ctrl+Alt+M', row); self.assertIn('mouse', row)
        self.assertLessEqual(len(row), 48)  # ipc.hpp kMaxHelpText
        self.assertEqual(len(view.help_rows()), len(view.BINDS) - len(view.HELP_PAIRS))  # no row for Ctrl+Alt+M: it lives in the menu
        self.assertIn(('CTRL + ALT + M', 'menu_mouse'), view.MENU_BINDS)
        self.assertNotIn('CTRL + ALT + M', [c for c, _ in view.BINDS])

    def test_mouse_ops_are_not_panel_capabilities(self):
        c = view.ViewControl(view.ViewState(NAMES), 'run', 'session', None, None, None)
        self.addCleanup(c.stop_menu_binds, 1)
        caps = c.status_reply({})['capabilities']
        self.assertFalse({'menu_mouse', 'menu_back', 'menu_mouse_press', 'menu_mouse_release'} & set(caps))

    def test_renderer_slider_bounds_are_the_placement_bounds(self):
        import re
        hud = (ROOT / 'spike/src/hud.cpp').read_text()
        fields = re.findall(r'\{"[^"]+", "[^"]*", (?:true|false), (?:true|false), (-?\d+), (-?\d+), (-?\d+)\}', hud)
        self.assertEqual([(int(lo), int(hi)) for lo, hi, _ in fields], [PLACEMENT_BOUNDS[k] for k in PLACEMENT_KEYS])
        self.assertEqual([int(flat) for _, _, flat in fields], [DEFAULT_PLACEMENT[k] for k in PLACEMENT_KEYS])

    def test_renderer_geometry_line_shape_matches_the_parser(self):
        import re
        hud = (ROOT / 'spike/src/hud.cpp').read_text()
        self.assertIn('{\\"menu_geometry\\":{\\"w\\":', hud)
        self.assertIn('\\"sliders\\":[', hud)
        self.assertRegex(GEOMETRY_LINE, r'^\{"menu_geometry":\{"w":\d+,"h":\d+,"tabs":\[\[')


class MouseLuaTests(unittest.TestCase):
    def test_install_is_guarded_hides_the_cursor_and_binds_press_and_release(self):
        lua = view.install_mouse_binds_lua(*ARGS)
        self.assertLess(lua.index('HL.Keybind(expired)'), lua.index('hl.bind('))  # replaces a surviving set
        self.assertIn('hl.bind("mouse:272", hl.dsp.exec_cmd(c .. "menu_mouse_press"))', lua)
        self.assertIn('hl.bind("mouse:272", hl.dsp.exec_cmd(c .. "menu_mouse_release"), { release = true })', lua)
        self.assertIn('pcall(hl.config, { cursor = { invisible = true } })', lua)
        self.assertIn('if xr_workspace_mouse == nil then', lua)  # a reinstall keeps the original cursor state
        self.assertLessEqual(len(lua), 4096)
        self.assertNotIn('os.execute', lua); self.assertNotIn('io.', lua)
        for bad in ('/tmp/a b', 'relative', '/x"y'):
            with self.subTest(bad), self.assertRaises(ValueError): view.install_mouse_binds_lua('/usr/bin/python3', '/opt/x.py', bad, 'r', 's')

    def test_every_removal_path_restores_the_cursor(self):
        restore = 'pcall(hl.config, { cursor = { invisible = s.invisible } })'
        for name, lua in (('mouse off', view.remove_mouse_binds_lua()), ('menu closed', view.remove_menu_binds_lua()),
                          ('run end', view.remove_binds_lua()), ('dead run key', xr_control.REMOVE_MENU_BINDS)):
            with self.subTest(name):
                self.assertIn('xr_workspace_mouse_binds = nil', lua)
                self.assertIn(restore, lua)
                self.assertIn('hl.dsp.cursor.move({ x = s.home.x, y = s.home.y })', lua)
        self.assertEqual(xr_control.REMOVE_MENU_BINDS, view.remove_menu_binds_lua())

    def test_reload_reinstall_still_fits_one_eval(self):
        self.assertLessEqual(len(view.remove_binds_lua() + ' ' + view.install_binds_lua(*ARGS)), 4096)
        self.assertLessEqual(len(view.install_menu_binds_lua(*ARGS)), 4096)

    def test_a_dead_runs_click_cleans_up(self):
        dead = '/nonexistent/control.sock'
        with mock.patch.object(xr_control, 'drop_stale_menu_binds') as drop:
            self.assertEqual(xr_control.main(['x', dead, 'run', 's', 'menu_mouse_press']), 1)
            drop.assert_called_once()

    def test_client_methods_send_generated_lua_only(self):
        from tools.native_spike import hyprland as h
        sent = []
        client = h.HyprlandClient('a' * 40 + '_123_456', Path('/nonexistent'))
        client._eval = sent.append
        client.install_mouse_binds(*ARGS); client.remove_mouse_binds(); client.warp_cursor(1920, -40)
        self.assertEqual(sent, [view.install_mouse_binds_lua(*ARGS), view.remove_mouse_binds_lua(), view.warp_cursor_lua(1920, -40)])

    def test_warp_lua_is_fixed_and_validated(self):
        self.assertEqual(view.warp_cursor_lua(10, 20), 'pcall(function() hl.dispatch(hl.dsp.cursor.move({ x = 10, y = 20 })) end)')
        for bad in ((1.5, 2), ('1', 2), (True, 2), (10 ** 7, 0), (0, float('nan'))):
            with self.subTest(bad=bad), self.assertRaises(ValueError): view.warp_cursor_lua(*bad)


class BindCheckTests(unittest.TestCase):
    """tools/native_spike/bind_check.py: the run's chords against a desktop config, through Hyprland's own loader."""
    def test_normalize(self):
        self.assertEqual(bind_check.normalize('CTRL + ALT + M'), bind_check.normalize('alt+control+m'))
        self.assertEqual(bind_check.normalize('SUPER + mouse:272'), (frozenset({'SUPER'}), 'mouse:272'))
        self.assertNotEqual(bind_check.normalize('mouse:272'), bind_check.normalize('SUPER + mouse:272'))

    def test_clashes_from_records(self):
        rec = [('desktop', 'bind', 'SUPER + M'), ('desktop', 'bind', 'CTRL + ALT + M'), ('desktop', 'unbind', 'CTRL + ALT + M'),
               ('desktop', 'bind', 'CTRL + ALT + TAB')]
        self.assertEqual(bind_check.clashes(rec), [])
        problems = bind_check.clashes(rec + [('desktop', 'bind', 'ALT + CTRL + m')])
        self.assertEqual(len(problems), 1); self.assertIn('menu_mouse', problems[0])
        with mock.patch.object(view, 'MENU_BINDS', view.MENU_BINDS + (('CTRL + ALT + H', 'menu_reset'),)):
            self.assertIn('bound twice', ' '.join(bind_check.clashes([])))  # Ctrl+Alt+H is help during a run

    def test_this_projects_chords_do_not_clash_with_each_other(self):
        self.assertEqual(bind_check.clashes([]), [])
        chords = [c for _, c, _ in bind_check.run_chords()]
        self.assertIn('CTRL + ALT + M', chords)

    @unittest.skipUnless(HYPRLAND, 'Hyprland not installed')
    def test_verify_config_finds_a_clash_and_loads_our_lua(self):
        with tempfile.TemporaryDirectory() as home:
            hypr = Path(home) / '.config' / 'hypr'; hypr.mkdir(parents=True)
            cfg = hypr / 'hyprland.lua'
            cfg.write_text('hl.bind("SUPER + Q", hl.dsp.exec_cmd("true"))\n'
                           'local t = hl.timer(function() end, { timeout = 1000, type = "repeat" })\n')  # timers: Hyprland 0.56 exit crash
            status, lines = bind_check.check(cfg, HYPRLAND, home)
            self.assertEqual(status, 0, lines)
            self.assertIn('config ok', lines[0])
            cfg.write_text('hl.bind("SUPER + Q", hl.dsp.exec_cmd("true"))\nhl.bind("ALT + CTRL + M", hl.dsp.exec_cmd("true"))\n')
            status, lines = bind_check.check(cfg, HYPRLAND, home)
            self.assertEqual(status, 1, lines)
            self.assertTrue(any('Ctrl' in l or 'CTRL + ALT + M' in l for l in lines[2:]), lines)
            cfg.write_text('hl.bind("SUPER + Q", hl.dsp.exec_cmd("true"))\nhl.nope()\n')
            self.assertEqual(bind_check.check(cfg, HYPRLAND, home)[0], 2)
            self.assertFalse((Path(home) / '.local').exists() and any((Path(home) / '.local').rglob('*.tsv')))  # nothing left behind

    def test_missing_binary_or_config_cannot_check(self):
        with tempfile.TemporaryDirectory() as home:
            self.assertEqual(bind_check.check(Path(home) / 'none.lua', '/bin/true', home)[0], 2)
        with mock.patch.object(shutil, 'which', return_value=None):
            self.assertEqual(bind_check.check(None, None, '/nonexistent')[0], 2)


if __name__ == '__main__':
    unittest.main()
