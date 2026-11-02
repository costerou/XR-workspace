"""Placement menu mouse mode: sliders driven by the mouse in the glasses (Ctrl+Alt+M while the menu is open).

Offline only: fake cursor samples, fake bind callables, Lua checked as text and (when Hyprland is installed) through
`Hyprland --verify-config` on a throwaway config. No compositor, renderer or glasses.
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
    """Hyprland cursorpos stand-in."""
    def __init__(self, x=1000.0, y=500.0): self.x, self.y, self.calls = x, y, 0
    def __call__(self):
        self.calls += 1
        return {'x': self.x, 'y': self.y}


def control(test, names=NAMES, clock=None, store=None, **kw):
    c = view.ViewControl(view.ViewState(names), 'run', 'session', lambda n: None, mock.Mock(), None, placements=store,
                         clock=clock or Clock(), **kw)
    test.addCleanup(c.stop_menu_binds, 1)
    test.addCleanup(c.join_focus, 2)
    return c


def send(c, op, request_id=None):
    return frames(c.on_message({'op': op, 'payload': {}, **({'request_id': request_id} if request_id else {})}))


def menu_of(out):
    menus = [f['payload'] for f in out if f['op'] == 'placement_menu']
    return menus[-1] if menus else None


def move(c, dx=0.0, dy=0.0, now=None):
    """Move the cursor by `dx, dy` in samples a hand could make (each under MOUSE_JUMP_PX), ticking the loop after each."""
    if c._cursor_last is None and c._cursor_sample is None:
        c._cursor_sample = (1000.0, 500.0); c.poll(now)  # baseline
    steps = max(1, int(max(abs(dx), abs(dy)) // (view.MOUSE_JUMP_PX / 2)) + 1)
    out = []
    for _ in range(steps):
        x, y = c._cursor_sample
        c._cursor_sample = (x + dx / steps, y + dy / steps)
        out += frames(c.poll(now))
    return out


class MouseModeStateTests(unittest.TestCase):
    def test_ctrl_alt_m_works_only_with_the_menu_open_and_toggles(self):
        c = control(self)
        self.assertEqual(send(c, 'menu_mouse'), [])  # menu closed: nothing (the bind only exists while it is open)
        self.assertIsNone(c.state.mouse)
        send(c, 'menu_toggle')
        on = menu_of(send(c, 'menu_mouse'))
        self.assertTrue(on['open'])
        self.assertEqual(on['pointer'], {'row': 0, 'x': 500, 'drag': False})  # on the selected field's knob (x = 0 is mid)
        off = menu_of(send(c, 'menu_mouse'))
        self.assertTrue(off['open']); self.assertNotIn('pointer', off)

    def test_pointer_starts_on_the_selected_knob(self):
        c = control(self)
        c.state.placements[0]['distance'] = 200
        send(c, 'menu_toggle'); send(c, 'menu_field_next'); send(c, 'menu_field_next')  # distance
        self.assertEqual(menu_of(send(c, 'menu_mouse'))['pointer'], {'row': 2, 'x': 1000, 'drag': False})

    def test_escape_leaves_mouse_mode_first_then_closes(self):
        store = Store(); c = control(self, store=store)
        send(c, 'menu_toggle'); send(c, 'menu_mouse')
        first = menu_of(send(c, 'menu_back'))
        self.assertTrue(first['open']); self.assertNotIn('pointer', first)
        self.assertFalse(menu_of(send(c, 'menu_back'))['open'])
        self.assertEqual(send(c, 'menu_back'), [])  # closed: nothing (a late key)

    def test_ctrl_alt_l_closes_the_menu_and_mouse_mode_together(self):
        c = control(self)
        send(c, 'menu_toggle'); send(c, 'menu_mouse')
        self.assertEqual(menu_of(send(c, 'menu_toggle')), {**c.state.menu(), 'open': False})
        self.assertIsNone(c.state.mouse)
        reopened = menu_of(send(c, 'menu_toggle'))
        self.assertNotIn('pointer', reopened)  # reopening is keyboard mode again

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
        self.assertEqual([f['op'] for f in out], ['placement', 'placement_menu'])
        self.assertEqual(c.state.placements[0]['x'], 10)
        send(c, 'menu_field_next'); send(c, 'menu_desktop_next'); send(c, 'menu_reset')
        self.assertEqual((c.state.menu_desktop, c.state.menu_field), (1, 'y'))
        self.assertIsNotNone(c.state.mouse)  # still in mouse mode


class PointerTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock(); self.store = Store()
        self.c = control(self, clock=self.clock, store=self.store)
        send(self.c, 'menu_toggle'); send(self.c, 'menu_mouse')

    def test_sideways_motion_moves_along_the_slider_and_clamps(self):
        out = move(self.c, dx=view.MOUSE_TRACK_PX / 4)
        self.assertEqual(menu_of(out)['pointer'], {'row': 0, 'x': 750, 'drag': False})
        self.assertNotIn('placement', [f['op'] for f in out])  # hovering changes nothing
        move(self.c, dx=10 * view.MOUSE_TRACK_PX)
        self.assertEqual(self.c.state.mouse['x'], 1000)
        move(self.c, dx=-3 * view.MOUSE_TRACK_PX)
        self.assertEqual(self.c.state.mouse['x'], 0)

    def test_up_and_down_step_whole_rows_with_the_selector_above(self):
        move(self.c, dy=view.MOUSE_ROW_PX * 0.6)
        self.assertEqual(self.c.state.mouse['row'], 0)  # less than a row: stays
        move(self.c, dy=view.MOUSE_ROW_PX * 0.6)
        self.assertEqual(self.c.state.mouse['row'], 1)  # the remainder adds up
        move(self.c, dy=-3 * view.MOUSE_ROW_PX)
        self.assertEqual(self.c.state.mouse['row'], -1)  # the desktop selector
        move(self.c, dy=-20 * view.MOUSE_ROW_PX)
        move(self.c, dy=view.MOUSE_ROW_PX)
        self.assertEqual(self.c.state.mouse['row'], 0)  # pushing past the top does not build up
        move(self.c, dy=50 * view.MOUSE_ROW_PX)
        self.assertEqual(self.c.state.mouse['row'], len(PLACEMENT_KEYS) - 1)

    def test_click_on_a_track_jumps_the_slider_there(self):
        move(self.c, dy=3 * view.MOUSE_ROW_PX, dx=-view.MOUSE_TRACK_PX * 0.25)  # yaw row, a quarter along
        out = send(self.c, 'menu_mouse_press')
        self.assertEqual([f['op'] for f in out], ['placement', 'placement_menu'])
        lo, hi = PLACEMENT_BOUNDS['yaw']
        self.assertEqual(self.c.state.placements[0]['yaw'], lo + round((hi - lo) / 4))
        self.assertEqual(out[0]['payload']['desktops'][0]['yaw'], -30)
        self.assertEqual((menu_of(out)['field'], menu_of(out)['pointer']['drag']), ('yaw', True))  # the clicked row is selected
        self.assertIsNotNone(self.c.save_due)

    def test_every_field_reaches_both_bounds_by_clicking_the_ends(self):
        for row, key in enumerate(PLACEMENT_KEYS):
            for end, x in ((0, -2 * view.MOUSE_TRACK_PX), (1, 2 * view.MOUSE_TRACK_PX)):
                with self.subTest(key=key, end=end):
                    self.c.state.mouse.update(row=row, drag=False)
                    move(self.c, dx=x)
                    send(self.c, 'menu_mouse_press'); send(self.c, 'menu_mouse_release')
                    self.assertEqual(self.c.state.placements[0][key], PLACEMENT_BOUNDS[key][end])

    def test_drag_updates_the_placement_live_and_release_stops_it(self):
        move(self.c, dy=6 * view.MOUSE_ROW_PX)  # curvature
        send(self.c, 'menu_mouse_press')
        values = []
        for _ in range(4):
            out = move(self.c, dx=view.MOUSE_TRACK_PX / 10, dy=view.MOUSE_ROW_PX * 3)  # vertical motion is ignored while held
            self.assertEqual([f['op'] for f in out], ['placement', 'placement_menu'])
            values.append(out[0]['payload']['desktops'][0]['curvature'])
        self.assertEqual(values, sorted(values)); self.assertEqual(len(set(values)), 4)
        self.assertEqual(self.c.state.mouse['row'], 6)
        send(self.c, 'menu_mouse_release')
        held = self.c.state.placements[0]['curvature']
        out = move(self.c, dx=view.MOUSE_TRACK_PX / 5)
        self.assertNotIn('placement', [f['op'] for f in out])
        self.assertEqual(self.c.state.placements[0]['curvature'], held)

    def test_drag_saves_like_the_keys_do(self):
        send(self.c, 'menu_mouse_press')
        move(self.c, dx=view.MOUSE_TRACK_PX / 10)
        self.c.poll(self.clock.now + view.MENU_SAVE_DELAY)
        self.assertEqual(self.store.saves, 1)
        self.assertEqual(self.store.saved[0]['x'], self.c.state.placements[0]['x'])

    def test_click_on_the_desktop_selector_picks_a_desktop(self):
        move(self.c, dy=-view.MOUSE_ROW_PX, dx=view.MOUSE_TRACK_PX * 0.4)  # x 900: XR-3
        out = send(self.c, 'menu_mouse_press')
        self.assertEqual([f['op'] for f in out], ['placement_menu'])
        self.assertEqual(menu_of(out)['desktop'], 2)
        self.assertFalse(self.c.state.mouse['drag'])
        move(self.c, dx=-view.MOUSE_TRACK_PX * 0.45)  # x 450: XR-2
        send(self.c, 'menu_mouse_press')
        self.assertEqual(self.c.state.menu_desktop, 1)

    def test_selector_ignores_a_desktop_this_run_does_not_have(self):
        c = control(self, names=['XR-1', 'XR-2'])
        send(c, 'menu_toggle'); send(c, 'menu_mouse')
        c.state.mouse.update(row=-1, x=950.0)
        self.assertEqual(send(c, 'menu_mouse_press'), [])
        self.assertEqual(c.state.menu_desktop, 0)

    def test_a_warp_sized_jump_only_re_baselines(self):
        move(self.c)
        x, y = self.c._cursor_sample
        self.c._cursor_sample = (x + view.MOUSE_JUMP_PX + 1, y)  # one sample: a warp, not a hand
        self.assertIsNone(self.c.poll()); self.assertEqual(self.c.state.mouse['x'], 500)
        move(self.c, dx=view.MOUSE_TRACK_PX / 10)
        self.assertEqual(self.c.state.mouse['x'], 600)

    def test_frames_are_valid_for_the_renderer(self):
        move(self.c, dy=-view.MOUSE_ROW_PX)
        send(self.c, 'menu_mouse_press')
        for _ in range(3): move(self.c, dx=37.3, dy=11)
        payload = self.c.state.menu()
        frame = view.placement_menu_frame('run', 'session', 9, payload)
        self.assertEqual(json.loads(frame)['payload'], payload)
        self.assertEqual(type(payload['pointer']['x']), int)


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
        self.assertFalse(menu_of(out)['open']); self.assertNotIn('pointer', menu_of(out))
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
        move(c, now=clock.now)
        for _ in range(5): c.poll(clock.now + 1)  # same sample again and again
        self.assertFalse(frames(c.poll(clock.now + self.IDLE))[0]['payload']['open'])


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

    def test_sampler_feeds_the_pointer_after_the_binds_land_and_stops_at_run_end(self):
        cursor = Cursor()
        landed = threading.Event()
        def mouse(on):
            if on: cursor.x, cursor.y = 2000.0, 700.0; landed.set()  # the install warps the cursor
        c = control(self, mouse_binds=mouse, menu_binds=lambda on: None, cursor=cursor)
        self.assertEqual(c.poll_interval(), None)
        send(c, 'menu_toggle'); send(c, 'menu_mouse'); c.join_focus(2)
        self.assertTrue(landed.wait(2))
        self.assertEqual(c.poll_interval(), view.MOUSE_POLL_S)
        deadline = time.monotonic() + 2
        while c._cursor_sample != (2000.0, 700.0) and time.monotonic() < deadline: time.sleep(0.01)
        c.poll(); self.assertEqual(c._cursor_last, (2000.0, 700.0))  # the warped position is the baseline, not a move
        self.assertEqual(c.state.mouse['x'], 500)
        cursor.x += view.MOUSE_TRACK_PX / 10
        deadline = time.monotonic() + 2
        while c.state.mouse['x'] == 500 and time.monotonic() < deadline: c.poll(); time.sleep(0.01)
        self.assertEqual(c.state.mouse['x'], 600)
        self.assertGreaterEqual(c.mouse_moves, 1)
        send(c, 'menu_mouse'); c.join_focus(2)
        calls = cursor.calls; time.sleep(0.1)
        self.assertLessEqual(cursor.calls - calls, 1)  # mouse mode off: sampling stops
        c.stop_menu_binds(1)
        self.assertFalse(c._sampler.is_alive())

    def test_bad_cursor_replies_are_ignored(self):
        replies = iter([OSError('gone'), {'x': 'a', 'y': 1}, {'x': float('nan'), 'y': 1}, [], {'x': 5, 'y': 6}])
        def cursor():
            r = next(replies, {'x': 5, 'y': 6})
            if isinstance(r, Exception): raise r
            return r
        c = control(self, cursor=cursor)
        send(c, 'menu_toggle'); send(c, 'menu_mouse')
        deadline = time.monotonic() + 2
        while c._cursor_sample is None and time.monotonic() < deadline: time.sleep(0.01)
        self.assertEqual(c._cursor_sample, (5.0, 6.0))

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

    def test_supervisor_ticks_faster_in_mouse_mode(self):
        from tools.native_spike.supervisor import Supervisor
        child = mock.Mock(); child.poll.return_value = None
        ticks = []
        class Control:
            events = None
            server = mock.Mock()
            def poll(self, now):
                ticks.append(now)
                return None
            def poll_interval(self): return 0.005
        a, b = socket.socketpair()
        with a, b, socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as dgram:
            Control.server.socket = dgram
            sup = Supervisor(child, lambda: 'done', control=Control())
            t = threading.Thread(target=sup.run, args=(a,), kwargs={'interval': 0.1}, daemon=True); t.start()
            time.sleep(0.25)
            count = len(ticks)
            sup.finished = True  # end the loop before its sockets close
            t.join(2)
        self.assertFalse(t.is_alive())
        self.assertGreater(count, 15)  # ~200 Hz asked; the 0.1 s default would give ~3

    def test_managed_run_wires_mouse_binds_and_the_cursor_query(self):
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
                client.query.return_value = {'x': 1, 'y': 2}
                deadline = time.monotonic() + 2
                while c._cursor_sample is None and time.monotonic() < deadline: time.sleep(0.01)
                self.assertIn(mock.call('cursorpos', timeout=0.25), client.query.call_args_list)
            client.remove_view_binds.assert_called_once()  # its Lua also leaves mouse mode
            self.assertIsNone(c.cursor)


class WireTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.cases = json.loads(FIXTURE.read_text())

    def test_shared_pointer_fixture(self):
        for p in self.cases['pointer_valid']:
            with self.subTest(p=p):
                self.assertEqual(json.loads(view.placement_menu_frame('run', 'session', 1, p))['payload'], p)
        for case in self.cases['pointer_invalid']:
            with self.subTest(case['why']), self.assertRaises(ValueError):
                view.placement_menu_frame('run', 'session', 1, case['payload'])

    def test_control_socket_accepts_the_mouse_ops_without_payload(self):
        for op in ('menu_mouse', 'menu_back', 'menu_mouse_press', 'menu_mouse_release'):
            wire = json.dumps({'schema': 1, 'request_id': 3, 'run_id': 'r', 'session': 's', 'op': op, 'payload': {}}) + '\n'
            self.assertEqual(decode_control(wire.encode())['op'], op)
            with self.assertRaises(ValueError):
                decode_control((wire.replace('"payload": {}', '"payload": {"x": 5}')).encode())
        self.assertEqual((set(view.MOUSE_OPS) | {'menu_mouse', 'menu_back'}) - set(xr_control.OPS), set())

    def test_help_card_names_mouse_mode_and_fits_the_renderer_limit(self):
        row = dict(view.help_rows())['Ctrl+Alt+L']
        self.assertIn('Ctrl+Alt+M', row); self.assertIn('mouse', row)
        self.assertLessEqual(len(row), 48)  # ipc.hpp kMaxHelpText
        self.assertEqual(len(view.help_rows()), len(view.BINDS))  # no new run bind: Ctrl+Alt+M lives in the menu
        self.assertIn(('CTRL + ALT + M', 'menu_mouse'), view.MENU_BINDS)
        self.assertNotIn('CTRL + ALT + M', [c for c, _ in view.BINDS])
        self.assertIn(row, (ROOT / 'spike/tests/placement_menu_style_test.cpp').read_text())  # the fit test uses the real row

    def test_mouse_ops_are_not_panel_capabilities(self):
        c = view.ViewControl(view.ViewState(NAMES), 'run', 'session', None, None, None)
        caps = c.status_reply({})['capabilities']
        self.assertFalse({'menu_mouse', 'menu_back', 'menu_mouse_press', 'menu_mouse_release'} & set(caps))

    def test_renderer_slider_bounds_are_the_placement_bounds(self):
        import re
        hud = (ROOT / 'spike/src/hud.cpp').read_text()
        fields = re.findall(r'\{"[^"]+", "[^"]*", (?:true|false), (?:true|false), (-?\d+), (-?\d+), (-?\d+)\}', hud)
        self.assertEqual([(int(lo), int(hi)) for lo, hi, _ in fields], [PLACEMENT_BOUNDS[k] for k in PLACEMENT_KEYS])
        self.assertEqual([int(flat) for _, _, flat in fields], [DEFAULT_PLACEMENT[k] for k in PLACEMENT_KEYS])


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
        client.install_mouse_binds(*ARGS); client.remove_mouse_binds()
        self.assertEqual(sent, [view.install_mouse_binds_lua(*ARGS), view.remove_mouse_binds_lua()])


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
