"""Placement menu wiring (MENU-WIRING): menu state machine, live placement + overlay frames, debounced save, modal binds."""
import json
from pathlib import Path
import tempfile
import threading
import unittest

from tools.native_spike import view
from tools.native_spike.geometry import DEFAULT_PLACEMENT, PLACEMENT_BOUNDS, PLACEMENT_KEYS
from tools.native_spike.ipc import decode_control
from tools.native_spike.profiles import LayoutStore

NAMES = ['XR-1', 'XR-2', 'XR-3']


def frames(data):
    return [json.loads(line) for line in (data or b'').decode().splitlines()]


class Clock:
    def __init__(self): self.now = 100.0
    def __call__(self): return self.now


class MemoryPlacements:
    def __init__(self, initial=None):
        self.saved = [dict(p) for p in (initial or [DEFAULT_PLACEMENT] * 3)]
        self.saves = 0
    def load(self): return [dict(p) for p in self.saved]
    def save(self, placements):
        assert len(placements) == 3
        self.saved = [dict(p) for p in placements]; self.saves += 1


class MenuStateTests(unittest.TestCase):
    def test_toggle_opens_on_the_selected_desktop_and_ops_are_ignored_while_closed(self):
        s = view.ViewState(NAMES)
        s.apply('next')
        for op in view.MENU_OPS[1:]:
            self.assertFalse(s.apply_menu(op))
        self.assertEqual(s.menu(), {'open': False, 'desktop': 0, 'field': 'x', 'placements': [DEFAULT_PLACEMENT] * 3})
        s.apply_menu('menu_toggle')
        self.assertEqual((s.menu_open, s.menu_desktop), (True, 1))
        s.apply_menu('menu_toggle')
        self.assertFalse(s.menu_open)

    def test_desktop_and_field_cycle_with_wraparound(self):
        s = view.ViewState(NAMES); s.apply_menu('menu_toggle')
        s.apply_menu('menu_desktop_prev'); self.assertEqual(s.menu_desktop, 2)
        s.apply_menu('menu_desktop_next'); self.assertEqual(s.menu_desktop, 0)
        seen = []
        for _ in PLACEMENT_KEYS:
            seen.append(s.menu_field); s.apply_menu('menu_field_next')
        self.assertEqual(tuple(seen), PLACEMENT_KEYS); self.assertEqual(s.menu_field, 'x')
        s.apply_menu('menu_field_prev'); self.assertEqual(s.menu_field, 'curvature')

    def test_two_output_run_cycles_two_desktops_and_pads_the_payload_to_three(self):
        s = view.ViewState(['XR-1', 'XR-2']); s.apply_menu('menu_toggle')
        s.apply_menu('menu_desktop_next'); s.apply_menu('menu_desktop_next')
        self.assertEqual(s.menu_desktop, 0)
        self.assertEqual(len(s.menu()['placements']), 3)

    def test_steps_clamp_to_bounds_and_reset_flattens_only_the_menu_desktop(self):
        s = view.ViewState(NAMES); s.apply_menu('menu_toggle')
        for field in PLACEMENT_KEYS:
            s.menu_field = field
            small, big = view.MENU_STEPS[field]
            start = s.placements[0][field]
            self.assertTrue(s.apply_menu('menu_inc')); self.assertEqual(s.placements[0][field], start + small)
            for _ in range(100): s.apply_menu('menu_inc_big')
            self.assertEqual(s.placements[0][field], PLACEMENT_BOUNDS[field][1])
            self.assertFalse(s.apply_menu('menu_inc'))  # at the bound: no change
            for _ in range(100): s.apply_menu('menu_dec_big')
            self.assertEqual(s.placements[0][field], PLACEMENT_BOUNDS[field][0])
            self.assertFalse(s.apply_menu('menu_dec'))
        s.apply_menu('menu_desktop_next'); s.menu_field = 'yaw'; s.apply_menu('menu_dec')
        self.assertEqual(s.placements[1]['yaw'], -1)
        s.apply_menu('menu_desktop_prev')
        self.assertTrue(s.apply_menu('menu_reset'))
        self.assertEqual(s.placements[0], DEFAULT_PLACEMENT); self.assertEqual(s.placements[1]['yaw'], -1)
        self.assertFalse(s.apply_menu('menu_reset'))  # already flat
        with self.assertRaises(ValueError): s.apply_menu('zoom_in')

    def test_menu_ops_are_not_panel_capabilities_but_decode_on_the_control_socket(self):
        self.assertFalse(set(view.MENU_OPS) & set(view.USER_OPS))
        for op in view.MENU_OPS:
            frame = (json.dumps({'schema': 1, 'request_id': 5, 'run_id': 'run', 'session': 's', 'op': op, 'payload': {}}) + '\n').encode()
            self.assertEqual(decode_control(frame)['op'], op)
        self.assertIn(('CTRL + ALT + L', 'menu_toggle'), view.BINDS)
        self.assertEqual(len({chord for chord, _ in view.BINDS}), len(view.BINDS))  # Ctrl+Alt+P stays prediction


class MenuControlTests(unittest.TestCase):
    def control(self, store=None, binds=None, clock=None):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        c = view.ViewControl(view.ViewState(NAMES), 'run', 'session', lambda n: None, LayoutStore(Path(tmp.name) / 'l.json'),
                             None, placements=store, menu_binds=binds, clock=clock or Clock())
        self.addCleanup(c.join_focus, 2)
        return c

    def send(self, c, op):
        return frames(c.on_message({'op': op, 'payload': {}}))

    def test_every_change_emits_placement_then_menu_frames(self):
        c = self.control()
        opened = self.send(c, 'menu_toggle')
        self.assertEqual([f['op'] for f in opened], ['placement_menu'])
        self.assertEqual(opened[0]['payload'], {'open': True, 'desktop': 0, 'field': 'x', 'placements': [DEFAULT_PLACEMENT] * 3})
        self.assertEqual([f['op'] for f in self.send(c, 'menu_field_next')], ['placement_menu'])
        out = self.send(c, 'menu_inc_big')
        self.assertEqual([f['op'] for f in out], ['placement', 'placement_menu'])
        self.assertEqual(out[0]['payload']['desktops'][0]['y'], 10)
        self.assertEqual(out[1]['payload']['placements'][0]['y'], 10)
        ids = [f['request_id'] for f in out]; self.assertEqual(ids, sorted(ids))
        c.state.placements[0]['y'] = PLACEMENT_BOUNDS['y'][1]
        self.assertEqual(self.send(c, 'menu_inc'), [])  # clamped no-op: nothing sent
        closed = self.send(c, 'menu_toggle')
        self.assertEqual([f['op'] for f in closed], ['placement_menu']); self.assertFalse(closed[0]['payload']['open'])
        self.assertEqual(self.send(c, 'menu_inc'), [])  # closed: ignored
        self.assertEqual(c.status_reply({})['placements'][0]['y'], 100)  # panel sync via status

    def test_frames_decode_in_the_python_mirror_shape(self):
        frame = view.placement_menu_frame('run', 's', 3, {'open': True, 'desktop': 2, 'field': 'roll',
                                                          'placements': [DEFAULT_PLACEMENT] * 3})
        self.assertTrue(frame.endswith(b'\n') and frame.count(b'\n') == 1)
        for bad in ({'open': True, 'desktop': 3, 'field': 'x', 'placements': [DEFAULT_PLACEMENT] * 3},
                    {'open': True, 'desktop': 0, 'field': 'zoom', 'placements': [DEFAULT_PLACEMENT] * 3},
                    {'open': True, 'desktop': 0, 'field': 'x', 'placements': [DEFAULT_PLACEMENT] * 2},
                    {'open': 1, 'desktop': 0, 'field': 'x', 'placements': [DEFAULT_PLACEMENT] * 3}):
            with self.subTest(bad), self.assertRaises(ValueError): view.placement_menu_frame('run', 's', 3, bad)

    def test_save_is_debounced_and_closing_saves_at_once(self):
        store, clock = MemoryPlacements(), Clock()
        c = self.control(store, clock=clock)
        self.send(c, 'menu_toggle'); self.send(c, 'menu_inc'); self.send(c, 'menu_inc')
        c.poll(clock.now + view.MENU_SAVE_DELAY - 0.01); self.assertEqual(store.saves, 0)
        clock.now += 0.3; self.send(c, 'menu_inc')  # a further change restarts the delay
        c.poll(clock.now + view.MENU_SAVE_DELAY - 0.01); self.assertEqual(store.saves, 0)
        c.poll(clock.now + view.MENU_SAVE_DELAY); self.assertEqual(store.saves, 1); self.assertEqual(store.saved[0]['x'], 6)
        c.poll(clock.now + 10); self.assertEqual(store.saves, 1)  # nothing pending
        self.send(c, 'menu_dec'); self.send(c, 'menu_toggle')
        self.assertEqual(store.saves, 2); self.assertEqual(store.saved[0]['x'], 4)
        self.send(c, 'menu_toggle'); self.send(c, 'menu_toggle')  # no change: no save
        self.assertEqual(store.saves, 2)
        self.send(c, 'menu_toggle'); self.send(c, 'menu_reset'); c.flush()  # run end
        self.assertEqual(store.saves, 3); self.assertEqual(store.saved[0], DEFAULT_PLACEMENT)

    def test_two_output_run_keeps_the_third_saved_placement(self):
        third = {**DEFAULT_PLACEMENT, 'yaw': 30}
        store = MemoryPlacements([DEFAULT_PLACEMENT, DEFAULT_PLACEMENT, third])
        c = view.ViewControl(view.ViewState(['XR-1', 'XR-2']), 'run', 'session', lambda n: None, None, None, placements=store)
        c.on_message({'op': 'menu_toggle'}); c.on_message({'op': 'menu_inc'}); c.on_message({'op': 'menu_toggle'})
        self.assertEqual(store.saved[2], third); self.assertEqual(store.saved[0]['x'], 2)

    def test_panel_placement_while_open_updates_the_overlay_and_supersedes_a_pending_save(self):
        store = MemoryPlacements()
        c = self.control(store)
        self.send(c, 'menu_toggle'); self.send(c, 'menu_inc')
        panel = [dict(DEFAULT_PLACEMENT, distance=150)] * 3
        out = frames(c.on_message({'op': 'placement', 'payload': {'desktops': panel}}))
        self.assertEqual([f['op'] for f in out], ['placement', 'placement_menu'])
        self.assertEqual(out[1]['payload']['placements'][0]['distance'], 150)
        self.assertEqual(store.saves, 1); self.assertIsNone(c.save_due)
        self.send(c, 'menu_toggle')
        out = frames(c.on_message({'op': 'placement', 'payload': {'desktops': [DEFAULT_PLACEMENT] * 3}}))
        self.assertEqual([f['op'] for f in out], ['placement'])  # closed menu: no overlay frame

    def test_modal_binds_follow_open_close_off_loop_and_stop_at_run_end(self):
        calls, gate = [], threading.Event()
        def binds(open_):
            gate.wait(2); calls.append(open_)
        c = self.control(binds=binds)
        self.send(c, 'menu_toggle'); self.send(c, 'menu_toggle'); self.send(c, 'menu_toggle')  # open, close, open
        gate.set(); c.join_focus(2)
        self.assertEqual(calls[-1], True)  # ends installed, in step with the open menu
        self.assertTrue(c._menu_binds_installed)
        def boom(open_): raise OSError('busy')
        c.menu_binds = boom
        self.send(c, 'menu_toggle'); c.join_focus(2)  # failure is swallowed; state stays truthful
        self.assertTrue(c._menu_binds_installed)
        c.menu_binds = binds
        c.on_events(b'configreloaded>>\n')  # reload drops runtime binds
        self.assertFalse(c._menu_binds_installed)
        c.stop_menu_binds()
        self.send(c, 'menu_toggle'); c.join_focus(2)
        self.assertIsNone(c.menu_binds)
        self.assertFalse(c._menu_binds_installed)

    def test_reload_work_re_adds_an_open_menus_binds(self):
        calls = []
        c = self.control(binds=calls.append)
        c.on_reload = lambda: None
        self.send(c, 'menu_toggle'); c.join_focus(2)
        c.on_events(b'configreloaded>>\n'); c.join_focus(2)
        self.assertEqual(calls, [True, True])
        self.send(c, 'menu_toggle'); c.join_focus(2)
        c.on_events(b'configreloaded>>\n'); c.join_focus(2)
        self.assertEqual(calls, [True, True, False])  # closed menu: nothing re-added


class OverlayIdleCloseTests(unittest.TestCase):
    """Image-retention protection: the help card and the menu close by themselves after OVERLAY_IDLE_CLOSE_S idle."""
    IDLE = view.OVERLAY_IDLE_CLOSE_S
    control, send = MenuControlTests.control, MenuControlTests.send

    def test_menu_closes_after_the_idle_time_saves_and_releases_its_keys(self):
        store, clock, calls = MemoryPlacements(), Clock(), []
        c = self.control(store, binds=calls.append, clock=clock)
        start = clock.now
        self.send(c, 'menu_toggle'); self.send(c, 'menu_inc'); c.join_focus(2)
        self.assertIsNone(c.poll(start + self.IDLE - 0.01))
        self.assertTrue(c.state.menu_open)
        out = frames(c.poll(start + self.IDLE))
        self.assertEqual([f['op'] for f in out], ['placement_menu']); self.assertFalse(out[0]['payload']['open'])
        self.assertFalse(c.state.menu_open)
        self.assertEqual((store.saves, store.saved[0]['x']), (1, 2))  # the pending change is saved at once
        c.join_focus(2); self.assertEqual(calls, [True, False])  # the arrows, Tab, R and Escape are released
        self.assertIsNone(c.poll(start + self.IDLE + 5))  # closed once; nothing more is sent
        self.assertTrue(frames(c.on_message({'op': 'menu_toggle'}))[0]['payload']['open'])  # Ctrl+Alt+L reopens it

    def test_each_key_or_panel_change_restarts_the_idle_time(self):
        clock = Clock(); c = self.control(clock=clock)
        self.send(c, 'menu_toggle')
        clock.now += self.IDLE - 1; self.send(c, 'menu_field_next')  # a key with no visible change still counts
        self.assertIsNone(c.poll(clock.now + self.IDLE - 0.01))
        clock.now += self.IDLE - 1
        c.on_message({'op': 'placement', 'payload': {'desktops': [DEFAULT_PLACEMENT] * 3}})  # panel slider
        self.assertIsNone(c.poll(clock.now + self.IDLE - 0.01)); self.assertTrue(c.state.menu_open)
        self.assertFalse(frames(c.poll(clock.now + self.IDLE))[0]['payload']['open'])

    def test_help_card_closes_after_the_idle_time_and_ctrl_alt_h_still_toggles(self):
        clock = Clock(); c = self.control(clock=clock)
        self.assertTrue(self.send(c, 'help')[0]['payload']['visible'])
        self.assertIsNone(c.poll(clock.now + self.IDLE - 0.01))
        out = frames(c.poll(clock.now + self.IDLE))
        self.assertEqual([(f['op'], f['payload']['visible']) for f in out], [('help', False)])
        self.assertFalse(c.help_visible)
        self.assertTrue(self.send(c, 'help')[0]['payload']['visible'])  # the next Ctrl+Alt+H opens it, not a no-op close

    def test_both_open_close_together_and_nothing_open_sends_nothing(self):
        clock = Clock(); c = self.control(clock=clock)
        self.assertIsNone(c.poll(clock.now + 10 * self.IDLE))  # idle with nothing open
        self.send(c, 'help'); self.send(c, 'menu_toggle')
        out = frames(c.poll(clock.now + self.IDLE))
        self.assertEqual([f['op'] for f in out], ['placement_menu', 'help'])
        ids = [f['request_id'] for f in out]; self.assertEqual(ids, sorted(set(ids)))

    def test_idle_time_is_sensible_and_matches_the_help_card_note(self):
        self.assertTrue(15 <= self.IDLE <= 120)
        root = Path(__file__).resolve().parents[2]
        import re
        found = re.findall(r'constexpr std::uint32_t kOverlayIdleCloseS = (\d+);', (root / 'spike/include/xr/hud.hpp').read_text())
        self.assertEqual([int(v) for v in found], [self.IDLE])
        footer = re.search(r'render_help_card\(.*?draw_centered\(im, footer', (root / 'spike/src/hud.cpp').read_text(), re.S).group(0)
        self.assertIn('std::to_string(kOverlayIdleCloseS)', footer)

    def test_supervisor_sends_idle_close_frames_to_the_renderer(self):
        import socket
        from unittest import mock
        from tools.native_spike.supervisor import Supervisor
        child = mock.Mock(); child.poll.side_effect = [None, None, None, 0, 0, 0, 0]
        frame = view.help_frame('run', 'session', 9, False)
        replies = iter([frame])
        control = mock.Mock(events=None); control.poll.side_effect = lambda now: next(replies, None)
        a, b = socket.socketpair()
        with a, b:
            control.server.socket = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            with control.server.socket:
                Supervisor(child, lambda: 'done', control=control).run(a, interval=0.01)
            b.settimeout(1); self.assertEqual(b.recv(4096), frame)


class ManagedMenuWiringTests(unittest.TestCase):
    def test_run_wires_modal_binds_and_saves_a_pending_change_at_run_end(self):
        from unittest import mock
        from tools.native_spike import managed, profiles
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp) / 'run'; runtime.mkdir(mode=0o700)
            client = mock.Mock(runtime_dir=runtime, event_stream=None)
            client.query.return_value = []
            store = profiles.ProfileStore(Path(tmp) / 'profiles.json', 'luma-ultra')
            with managed.view_controls(client, 'run', 'session', NAMES, store=store) as control:
                control.on_message({'op': 'menu_toggle'}); control.join_focus(2)
                client.install_menu_binds.assert_called_once()
                self.assertEqual(client.install_menu_binds.call_args.args, client.install_view_binds.call_args.args)
                control.on_message({'op': 'menu_desktop_next'}); control.on_message({'op': 'menu_inc_big'})
                self.assertIsNotNone(control.save_due)
                self.assertEqual(profiles.PlacementStore(store.path, store.model).load()[1]['x'], 0)  # not yet saved
            self.assertEqual(profiles.PlacementStore(store.path, store.model).load()[1]['x'], 10)
            client.remove_view_binds.assert_called_once()
            self.assertIsNone(control.menu_binds)

    def test_supervisor_loop_polls_the_debounced_save(self):
        import socket
        from unittest import mock
        from tools.native_spike.supervisor import Supervisor
        child = mock.Mock(); child.poll.side_effect = [None, None, 0, 0, 0, 0]
        control = mock.Mock(events=None)
        a, b = socket.socketpair()
        with a, b:
            control.server.socket = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            with control.server.socket:
                Supervisor(child, lambda: 'done', control=control).run(a, interval=0.01)
        self.assertTrue(control.poll.called)


class StaleMenuBindTests(unittest.TestCase):
    """Supervisor gone with the menu open: a menu key removes the modal binds through the compositor socket."""
    def setUp(self):
        import os, socket
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.instance = 'a' * 40 + '_1_2'
        d = Path(self.tmp.name) / 'hypr' / self.instance; d.mkdir(parents=True)
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); self.addCleanup(self.server.close)
        self.server.bind(str(d / '.socket.sock')); self.server.listen(2)
        self.received = []
        def serve():
            for _ in range(1):
                conn, _ = self.server.accept()
                with conn: self.received.append(conn.recv(4096)); conn.sendall(b'ok')
        self.thread = threading.Thread(target=serve, daemon=True); self.thread.start()
        self.env = {'XDG_RUNTIME_DIR': self.tmp.name, 'HYPRLAND_INSTANCE_SIGNATURE': self.instance}

    def test_fixed_lua_matches_view_and_reaches_the_compositor(self):
        from tools.native_spike import xr_control
        self.assertEqual(xr_control.REMOVE_MENU_BINDS, view.remove_menu_binds_lua())
        self.assertTrue(xr_control.drop_stale_menu_binds(self.env))
        self.thread.join(2)
        self.assertEqual(self.received, [('/eval ' + view.remove_menu_binds_lua()).encode()])

    def test_dead_run_menu_key_cleans_up_and_view_keys_do_not(self):
        from unittest import mock
        from tools.native_spike import xr_control
        dead = str(Path(self.tmp.name) / 'nobody.sock')
        with mock.patch.object(xr_control, 'drop_stale_menu_binds') as drop:
            self.assertEqual(xr_control.main(['x', dead, 'run', 's', 'zoom_in']), 1); drop.assert_not_called()
            self.assertEqual(xr_control.main(['x', dead, 'run', 's', 'menu_inc']), 1); drop.assert_called_once()
            self.assertEqual(xr_control.main(['x', dead, 'run', 's', 'menu_toggle']), 1); self.assertEqual(drop.call_count, 2)

    def test_untrusted_environment_is_refused(self):
        from tools.native_spike import xr_control
        for env in ({}, {**self.env, 'XDG_RUNTIME_DIR': 'relative'}, {**self.env, 'HYPRLAND_INSTANCE_SIGNATURE': '../x'}):
            with self.subTest(env): self.assertFalse(xr_control.drop_stale_menu_binds(env))
        self.assertFalse(xr_control.drop_stale_menu_binds({**self.env, 'HYPRLAND_INSTANCE_SIGNATURE': 'b' * 40 + '_1_2'}))
        self.assertTrue(xr_control.drop_stale_menu_binds(self.env)); self.thread.join(2)  # unblock the server


class MenuBindLuaTests(unittest.TestCase):
    # A realistic worst case: long worktree path, 32-hex run id, 64-hex session.
    ARGS = ('/usr/bin/python3', '/home/cody/.t3/worktrees/viture-workspace/feature-placement-menu-wiring/tools/native_spike/xr_control.py',
            '/run/user/1000/xr-workspace-spike/06422ac48c7cdd7272b533f4d7508559/control.sock', '06422ac48c7cdd7272b533f4d7508559', 'a' * 64)

    def test_menu_binds_lua_is_guarded_replacing_and_within_eval_limits(self):
        lua = view.install_menu_binds_lua(*self.ARGS)
        self.assertLess(lua.index('HL.Keybind(expired)'), lua.index('hl.bind('))  # replaces a surviving set
        for chord, op in view.MENU_BINDS:
            self.assertIn(f'hl.bind("{chord}", hl.dsp.exec_cmd(c .. "{op}"))', lua)
        # Every menu op has a key: Ctrl+Alt+L (menu_toggle) is a run bind, the rest are modal; Escape is menu_back.
        self.assertEqual({op for _, op in view.MENU_BINDS} | {op for _, op in view.BINDS if op in view.MENU_OPS}, set(view.MENU_OPS))
        self.assertLessEqual(len(lua), 4096)
        self.assertLessEqual(len(view.remove_binds_lua() + ' ' + view.install_binds_lua(*self.ARGS)), 4096)
        self.assertIn('xr_workspace_menu_binds = nil', view.remove_binds_lua())  # run end clears an open menu's binds
        for bad in ('/tmp/a b', 'relative', '/x"y'):
            with self.subTest(bad), self.assertRaises(ValueError): view.install_menu_binds_lua('/usr/bin/python3', '/opt/x.py', bad, 'r', 's')

    def test_client_methods_send_generated_lua_only(self):
        from tools.native_spike import hyprland as h
        sent = []
        client = h.HyprlandClient('a' * 40 + '_123_456', Path('/nonexistent'))
        client._eval = sent.append
        client.install_menu_binds(*self.ARGS); client.remove_menu_binds()
        self.assertEqual(sent, [view.install_menu_binds_lua(*self.ARGS), view.remove_menu_binds_lua()])


if __name__ == '__main__':
    unittest.main()
