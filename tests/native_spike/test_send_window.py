"""Send the focused window to the glasses (Ctrl+Alt+G, Ctrl+Alt+1/2/3) or back to the physical monitor (Ctrl+Alt+B).

Offline only: a fake Hyprland client (activewindow / monitors / mutate), fake notify, Lua checked as text and (when
Hyprland is installed) through `Hyprland --verify-config` on throwaway configs. No compositor, renderer or glasses.
"""
import json
from pathlib import Path
import shutil
import tempfile
import threading
import time
import unittest
from unittest import mock

from tools.native_spike import bind_check, send, view, xr_control
from tools.native_spike.ipc import decode_control

ROOT = Path(__file__).resolve().parents[2]
NAMES = ['XR-1', 'XR-2', 'XR-3']
ARGS = ('/usr/bin/python3', '/home/cody/.t3/worktrees/viture-workspace/feature-placement-menu-integrate/tools/native_spike/xr_control.py',
        '/run/user/1000/xr-workspace-spike/' + 'a' * 32 + '/control.sock', 'a' * 32, 'e' * 64)
HYPRLAND = shutil.which('Hyprland')


def monitors(active=None):
    """DP-1 physical fallback, DP-2 the glasses (presentation), XR-1..3 the run's desktops; active workspace per name."""
    active = {'DP-1': 1, 'DP-2': 2, 'XR-1': 31, 'XR-2': 41, 'XR-3': 51, **(active or {})}
    return [{'id': i, 'name': n, 'activeWorkspace': {'id': active[n], 'name': str(active[n])}}
            for i, n in enumerate(['DP-1', 'DP-2', 'XR-1', 'XR-2', 'XR-3'])]


class Client:
    """Hyprland stand-in: the focused window, the monitors, and every mutation sent."""
    def __init__(self, window=None, mons=None, reply='ok'):
        self.window = {'address': '0x55aa01', 'monitor': 0, 'pinned': False, 'workspace': {'id': 1}} if window is None else window
        self.mons, self.reply, self.mutations, self.queries = mons or monitors(), reply, [], []
        self.gate = None  # optional threading.Event a mutation waits on
    def query(self, kind, argument=None, timeout=2.0):
        self.queries.append(kind)
        if kind == 'activewindow': return self.window
        if kind == 'monitors': return self.mons
        raise ValueError(kind)
    def mutate(self, command):
        from tools.native_spike.hyprland import HyprlandClient
        HyprlandClient._check_mutation(command)  # the real allowlist
        if self.gate is not None: self.gate.wait(5)
        self.mutations.append(command)
        return self.reply


def sender(client=None, names=NAMES, fallback='DP-1'):
    notes = []
    return send.WindowSender(client or Client(), names, fallback, notify=notes.append), notes


class MoveTests(unittest.TestCase):
    def test_ctrl_alt_g_sends_to_the_selected_desktop_and_focus_follows(self):
        s, notes = sender()
        self.assertIsNone(s.move('send_glasses', 'XR-2'))
        self.assertEqual(s.client.mutations, ['dispatch hl.dsp.window.move({ workspace = "41", follow = true, window = "address:0x55aa01" })'])
        self.assertEqual(s.moves, 1)

    def test_numbered_binds_pick_their_desktop_whatever_is_selected(self):
        for op, ws in (('send_glasses_1', 31), ('send_glasses_2', 41), ('send_glasses_3', 51)):
            with self.subTest(op):
                s, _ = sender()
                self.assertIsNone(s.move(op, 'XR-2'))
                self.assertIn(f'workspace = "{ws}"', s.client.mutations[0])

    def test_a_desktop_this_run_does_not_have(self):
        s, _ = sender(names=['XR-1'])
        self.assertIn('XR-3 is not part of this session', s.move('send_glasses_3', 'XR-1'))
        self.assertEqual(s.client.mutations, [])
        self.assertEqual(s.client.queries, [])  # refused before asking the compositor

    def test_pinned_windows_never_go_to_the_glasses(self):
        # Recovery refuses to evacuate an XR output holding a pinned window: Stop/Recover would leave it incomplete.
        for pinned in (True, None, 'yes'):
            with self.subTest(pinned=pinned):
                win = {'address': '0x1', 'monitor': 0, 'pinned': pinned}
                if pinned is None: del win['pinned']
                s, _ = sender(Client(window=win))
                self.assertIn('Pinned', s.move('send_glasses', 'XR-1'))
                self.assertEqual(s.client.mutations, [])

    def test_no_focused_window_or_a_bad_address(self):
        for win in ({}, {'address': ''}, {'address': '0xZZ', 'monitor': 0, 'pinned': False},
                    {'address': '0x1"}) hl.exec("x', 'monitor': 0, 'pinned': False}, {'address': 5, 'pinned': False}):
            with self.subTest(win=win):
                s, _ = sender(Client(window=win))
                self.assertIsNotNone(s.move('send_glasses', 'XR-1'))
                self.assertEqual(s.client.mutations, [])

    def test_already_there_is_a_quiet_no_op(self):
        s, _ = sender(Client(window={'address': '0x1', 'monitor': 3, 'pinned': False}))
        self.assertIsNone(s.move('send_glasses', 'XR-2'))
        self.assertEqual(s.client.mutations, [])

    def test_only_a_regular_workspace_is_a_target(self):
        for ws in (0, -98, None, '41'):
            with self.subTest(ws=ws):
                mons = monitors(); mons[3]['activeWorkspace'] = {'id': ws}
                s, _ = sender(Client(mons=mons))
                self.assertIn('regular workspace', s.move('send_glasses', 'XR-2'))
                self.assertEqual(s.client.mutations, [])

    def test_missing_or_ambiguous_target_output(self):
        mons = [m for m in monitors() if m['name'] != 'XR-2']
        s, _ = sender(Client(mons=mons))
        self.assertIn('not available', s.move('send_glasses_2', 'XR-1'))
        s, _ = sender(Client(mons=monitors() + [dict(monitors()[3], id=9)]))
        self.assertIn('not available', s.move('send_glasses_2', 'XR-1'))

    def test_ctrl_alt_b_sends_an_xr_window_back_to_the_fallback(self):
        s, _ = sender(Client(window={'address': '0xabc', 'monitor': 4, 'pinned': False}))
        self.assertIsNone(s.move('send_back', 'XR-1'))
        self.assertEqual(s.client.mutations, ['dispatch hl.dsp.window.move({ workspace = "1", follow = true, window = "address:0xabc" })'])

    def test_ctrl_alt_b_leaves_windows_that_are_not_on_the_glasses(self):
        for monitor in (0, 1, 99):  # the fallback, the glasses' own presentation output, unknown
            with self.subTest(monitor=monitor):
                s, _ = sender(Client(window={'address': '0xabc', 'monitor': monitor, 'pinned': False}))
                self.assertIn('not on the glasses', s.move('send_back', 'XR-1'))
                self.assertEqual(s.client.mutations, [])

    def test_ctrl_alt_b_without_a_fallback(self):
        s, _ = sender(fallback=None)
        self.assertIn('no physical monitor', s.move('send_back', 'XR-1'))

    def test_a_pinned_window_on_the_glasses_is_refused_for_send_back_too(self):
        # Pinned after it was sent: Ctrl+Alt+B refuses it like any pinned move and says so (unpin, then send it back).
        s, _ = sender(Client(window={'address': '0xabc', 'monitor': 2, 'pinned': True}))
        self.assertIn('Pinned', s.move('send_back', 'XR-1'))

    def test_hyprland_refusing_the_move_is_reported(self):
        s, _ = sender(Client(reply='error: nope'))
        self.assertIn('did not move', s.move('send_glasses', 'XR-1'))
        self.assertEqual(s.moves, 0)


class WorkerTests(unittest.TestCase):
    def wait(self, cond):
        deadline = time.monotonic() + 3
        while not cond() and time.monotonic() < deadline: time.sleep(0.005)
        self.assertTrue(cond())

    def test_requests_run_off_the_loop_and_notify_refusals(self):
        s, notes = sender(Client(window={'address': '0x1', 'monitor': 0, 'pinned': True}))
        s.request('send_glasses', 'XR-1')
        self.wait(lambda: notes)
        self.assertIn('Pinned', notes[0])

    def test_compositor_errors_never_escape(self):
        client = Client(); client.query = mock.Mock(side_effect=TimeoutError('gone'))
        s, notes = sender(client)
        s.request('send_glasses', 'XR-1')
        self.wait(lambda: notes)
        self.assertEqual(notes, ['Could not move the window.'])

    def test_close_waits_for_a_move_in_flight_and_no_move_starts_after(self):
        # Stop/Recover: the run closes the sender before recovery evacuates the XR outputs. A move already sent lands
        # first (so recovery sees the window and moves it back); a key pressed afterwards moves nothing.
        client = Client(); client.gate = threading.Event()
        s, notes = sender(client)
        s.request('send_glasses', 'XR-1')
        self.wait(lambda: s._lock.locked())
        closed = threading.Event()
        threading.Thread(target=lambda: (s.close(), closed.set()), daemon=True).start()
        time.sleep(0.05)
        self.assertFalse(closed.is_set())  # still waiting on the move
        client.gate.set()
        self.assertTrue(closed.wait(3))
        self.assertEqual(len(client.mutations), 1)
        s.request('send_glasses', 'XR-2'); s._work('send_glasses', 'XR-2')
        time.sleep(0.05)
        self.assertEqual(len(client.mutations), 1)
        self.assertEqual(notes, [])

    def test_close_is_bounded_when_the_compositor_hangs(self):
        client = Client(); client.gate = threading.Event()
        s, _ = sender(client)
        s.request('send_glasses', 'XR-1')
        self.wait(lambda: s._lock.locked())
        t0 = time.monotonic(); s.close(timeout=0.2)
        self.assertLess(time.monotonic() - t0, 1.5)
        self.assertTrue(s.closed)
        client.gate.set()


class AllowlistTests(unittest.TestCase):
    def test_move_command_is_validated(self):
        self.assertEqual(send.move_command('0xdeadbeef', 7),
                         'dispatch hl.dsp.window.move({ workspace = "7", follow = true, window = "address:0xdeadbeef" })')
        for address, ws in (('0x1', 0), ('0x1', -1), ('0x1', True), ('0x1', 2147483648), ('0x1', '7'), ('1', 7),
                            ('0x1 ', 7), ('0x' + '1' * 17, 7), (None, 7)):
            with self.subTest(address=address, ws=ws), self.assertRaises(ValueError): send.move_command(address, ws)

    def test_hyprland_client_accepts_only_this_move(self):
        from tools.native_spike import hyprland as h
        sent = []
        client = h.HyprlandClient('a' * 40 + '_123_456', Path('/nonexistent'))
        client._request = lambda req, timeout=h.DEADLINE: sent.append(req) or b'ok'
        self.assertEqual(client.mutate(send.move_command('0xabc', 41)), 'ok')
        self.assertEqual(sent, [b'/dispatch hl.dsp.window.move({ workspace = "41", follow = true, window = "address:0xabc" })'.decode()])
        for bad in ('dispatch hl.dsp.window.move({ workspace = "41", follow = false, window = "address:0xabc" })',
                    'dispatch hl.dsp.window.move({ workspace = "0", follow = true, window = "address:0xabc" })',
                    'dispatch hl.dsp.window.move({ workspace = "-98", follow = true, window = "address:0xabc" })',
                    'dispatch hl.dsp.window.move({ workspace = "4294967296", follow = true, window = "address:0xabc" })',
                    'dispatch hl.dsp.window.move({ workspace = "41", follow = true, window = "address:0xabc" }) hl.exec_cmd("x")',
                    'dispatch hl.dsp.window.move({ monitor = "DP-1" })',
                    'dispatch hl.dsp.window.pin({ window = "address:0xabc" })'):
            with self.subTest(bad), self.assertRaises(ValueError): client.mutate(bad)


class WiringTests(unittest.TestCase):
    def test_ops_on_the_wire_and_in_the_helper(self):
        self.assertEqual(set(send.SEND_OPS), {op for _, op in send.SEND_BINDS})
        self.assertEqual(set(send.SEND_OPS) - set(xr_control.OPS), set())
        for op in send.SEND_OPS:
            wire = json.dumps({'schema': 1, 'request_id': 3, 'run_id': 'r', 'session': 's', 'op': op, 'payload': {}}) + '\n'
            self.assertEqual(decode_control(wire.encode())['op'], op)

    def test_not_panel_capabilities_and_not_view_ops(self):
        c = view.ViewControl(view.ViewState(NAMES), 'run', 'session', None, None, None)
        self.assertFalse(set(send.SEND_OPS) & set(c.status_reply({})['capabilities']))
        self.assertFalse(set(send.SEND_OPS) & set(view.USER_OPS + view.MENU_OPS + xr_control.LEGACY_OPS))

    def test_view_control_routes_with_the_selected_desktop_and_sends_no_frame(self):
        c = view.ViewControl(view.ViewState(NAMES), 'run', 'session', None, None, None)
        self.assertIsNone(c.on_message({'op': 'send_glasses', 'payload': {}}))  # no sender: ignored
        c.sender = mock.Mock()
        c.state.apply('next')
        self.assertIsNone(c.on_message({'op': 'send_glasses', 'payload': {}}))
        self.assertIsNone(c.on_message({'op': 'send_back', 'payload': {}}))
        self.assertEqual(c.sender.request.call_args_list, [mock.call('send_glasses', 'XR-2'), mock.call('send_back', 'XR-2')])

    def test_managed_run_closes_the_sender_before_the_binds_go(self):
        from tools.native_spike import managed
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp) / 'run'; runtime.mkdir(mode=0o700)
            client = mock.Mock(runtime_dir=runtime, event_stream=None)
            client.query.return_value = []
            order = []
            client.remove_view_binds.side_effect = lambda: order.append('binds')
            with mock.patch.dict('os.environ', {'XDG_CONFIG_HOME': tmp}):
                with managed.view_controls(client, 'run', 'session', NAMES, fallback='DP-1') as c:
                    self.assertIsInstance(c.sender, send.WindowSender)
                    self.assertEqual((c.sender.names, c.sender.fallback), (NAMES, 'DP-1'))
                    close = c.sender.close
                    c.sender.close = lambda *a: (order.append('sender'), close(*a))
            self.assertEqual(order, ['sender', 'binds'])
            self.assertTrue(c.sender.closed)

    def test_run_owned_passes_the_fallback_and_recovers_after_the_controls_close(self):
        import inspect
        from tools.native_spike import managed
        src = inspect.getsource(managed._run_owned)
        self.assertRegex(src, r'view_controls\(client, journal\.run_id, session, capture, output, store,\s+fallback\)')
        self.assertLess(src.index('view_controls('), src.index('result = recover('))  # recovery after the with block


class LuaTests(unittest.TestCase):
    def test_install_is_guarded_and_binds_by_keycode(self):
        lua = view.install_send_binds_lua(*ARGS)
        self.assertLess(lua.index('HL.Keybind(expired)'), lua.index('hl.bind('))  # replaces a surviving set
        for chord, op in send.SEND_BINDS:
            self.assertIn(f'hl.bind("{chord}", hl.dsp.exec_cmd(c .. "{op}"))', lua)
        self.assertIn('"CTRL + ALT + code:10"', lua)
        self.assertLessEqual(len(lua), 4096)
        self.assertNotIn('os.execute', lua); self.assertNotIn('io.', lua)
        for bad in ('/tmp/a b', 'relative', '/x"y'):
            with self.subTest(bad), self.assertRaises(ValueError): view.install_send_binds_lua('/usr/bin/python3', '/opt/x.py', bad, 'r', 's')

    def test_run_end_removes_them_and_the_reload_reinstall_still_fits(self):
        self.assertIn('xr_workspace_send_binds = nil', view.remove_binds_lua())
        self.assertLessEqual(len(view.remove_binds_lua() + ' ' + view.install_binds_lua(*ARGS)), 4096)

    def test_client_installs_and_reinstalls_them_with_the_view_binds(self):
        from tools.native_spike import hyprland as h
        sent = []
        client = h.HyprlandClient('a' * 40 + '_123_456', Path('/nonexistent'))
        client._eval = sent.append
        client.install_view_binds(*ARGS)
        self.assertEqual(sent, [view.install_binds_lua(*ARGS), view.install_send_binds_lua(*ARGS)])
        sent.clear(); client.reinstall_view_binds(*ARGS)
        self.assertEqual(sent, [view.remove_binds_lua() + ' ' + view.install_binds_lua(*ARGS), view.install_send_binds_lua(*ARGS)])
        sent.clear(); client.remove_view_binds()
        self.assertEqual(sent, [view.remove_binds_lua()])

    def test_a_failed_send_install_leaves_no_view_binds_behind(self):
        from tools.native_spike import hyprland as h
        sent = []
        client = h.HyprlandClient('a' * 40 + '_123_456', Path('/nonexistent'))
        def evaluate(code):
            sent.append(code)
            if 'xr_workspace_send_binds = {' in code: raise ValueError('compositor rejected bind change')
        client._eval = evaluate
        with self.assertRaises(ValueError): client.install_view_binds(*ARGS)
        self.assertEqual(sent[-1], view.remove_binds_lua())

    def test_labels(self):
        self.assertEqual([view.chord_label(c) for c, _ in send.SEND_BINDS],
                         ['Ctrl+Alt+G', 'Ctrl+Alt+1', 'Ctrl+Alt+2', 'Ctrl+Alt+3', 'Ctrl+Alt+B'])


class NoSessionTests(unittest.TestCase):
    def test_a_send_key_with_no_run_listening_only_notifies(self):
        with mock.patch.object(xr_control, 'notify_no_session') as note, \
                mock.patch.object(xr_control, 'drop_stale_menu_binds') as drop:
            for op in send.SEND_OPS:
                self.assertEqual(xr_control.main(['x', '/nonexistent/control.sock', 'run', 's', op]), 1)
            self.assertEqual(note.call_count, len(send.SEND_OPS))
            drop.assert_not_called()
        with mock.patch.object(xr_control, 'notify_no_session') as note:
            xr_control.main(['x', '/nonexistent/control.sock', 'run', 's', 'zoom_in'])
            note.assert_not_called()  # only the send keys say anything

    def test_notify_no_session_is_a_fixed_argv_and_never_raises(self):
        calls = []
        with mock.patch('shutil.which', return_value='/usr/bin/notify-send'):
            self.assertTrue(xr_control.notify_no_session({'PATH': '/usr/bin'}, run=lambda argv, **kw: calls.append((argv, kw))))
            self.assertFalse(xr_control.notify_no_session({'PATH': '/usr/bin'}, run=mock.Mock(side_effect=OSError('no'))))
        argv, kw = calls[0]
        self.assertEqual(argv[0], '/usr/bin/notify-send'); self.assertEqual(argv[-1], xr_control.NO_SESSION)
        self.assertNotIn('shell', kw)
        with mock.patch('shutil.which', return_value=None):
            self.assertFalse(xr_control.notify_no_session({'PATH': '/usr/bin'}))

    def test_supervisor_notify_is_best_effort(self):
        calls = []
        with tempfile.TemporaryDirectory() as runtime, mock.patch('shutil.which', return_value='/usr/bin/notify-send'):
            Path(runtime, 'bus').touch()
            self.assertTrue(send.notify('hi', run=lambda argv, **kw: calls.append((argv, kw)),
                                        environ={'PATH': '/usr/bin', 'XDG_RUNTIME_DIR': runtime}))
            self.assertEqual(calls[0][1]['env']['DBUS_SESSION_BUS_ADDRESS'], f'unix:path={runtime}/bus')
            self.assertFalse(send.notify('hi', run=mock.Mock(side_effect=OSError('x')), environ={'PATH': '/usr/bin'}))
        self.assertEqual(calls[0][0][-1], 'hi')


class HelpCardTests(unittest.TestCase):
    def test_help_row_names_the_send_keys_and_the_card_keeps_twelve_rows(self):
        row = dict(view.help_rows())['Ctrl+Alt+H']
        self.assertIn('Ctrl+Alt+G', row); self.assertIn('B', row)
        self.assertLessEqual(len(row), 48)  # ipc.hpp kMaxHelpText
        self.assertEqual(len(view.help_rows()), 12)  # 945 px of 960; a 13th row does not fit


class BindCheckTests(unittest.TestCase):
    def test_digits_and_their_keycodes_are_one_key(self):
        self.assertEqual(bind_check.normalize('CTRL + ALT + 1'), bind_check.normalize('ALT + CTRL + code:10'))
        self.assertEqual(bind_check.normalize('SUPER + 0'), bind_check.normalize('SUPER + code:19'))
        self.assertNotEqual(bind_check.normalize('CTRL + ALT + 1'), bind_check.normalize('CTRL + ALT + code:11'))

    def test_send_binds_are_checked(self):
        chords = [(w, c) for w, c, _ in bind_check.run_chords()]
        for chord, _ in send.SEND_BINDS: self.assertIn(('send', chord), chords)
        self.assertEqual(bind_check.clashes([]), [])
        for desktop in ('CTRL + ALT + 2', 'CTRL + ALT + code:11', 'ctrl + alt + g'):
            with self.subTest(desktop):
                problems = bind_check.clashes([('desktop', 'bind', desktop)])
                self.assertEqual(len(problems), 1); self.assertIn('send', problems[0])
        with mock.patch.object(send, 'SEND_BINDS', send.SEND_BINDS + (('CTRL + ALT + H', 'send_back'),)):
            self.assertIn('bound twice', ' '.join(bind_check.clashes([])))

    @unittest.skipUnless(HYPRLAND, 'Hyprland not installed')
    def test_verify_config_loads_the_send_binds_and_finds_a_digit_clash(self):
        with tempfile.TemporaryDirectory() as home:
            hypr = Path(home) / '.config' / 'hypr'; hypr.mkdir(parents=True)
            cfg = hypr / 'hyprland.lua'
            cfg.write_text('hl.bind("SUPER + Q", hl.dsp.exec_cmd("true"))\n')
            status, lines = bind_check.check(cfg, HYPRLAND, home)
            self.assertEqual(status, 0, lines)
            self.assertIn('Ctrl+Alt+G, Ctrl+Alt+1, Ctrl+Alt+2, Ctrl+Alt+3, Ctrl+Alt+B', lines[-1])
            cfg.write_text('hl.bind("CTRL + ALT + 3", hl.dsp.exec_cmd("true"))\n')
            status, lines = bind_check.check(cfg, HYPRLAND, home)
            self.assertEqual(status, 1, lines)
            self.assertTrue(any('send_glasses_3' in l for l in lines), lines)

    @unittest.skipUnless(HYPRLAND, 'Hyprland not installed')
    def test_hyprland_builds_the_move_dispatcher(self):
        # Same keys as Omarchy's own move-window bind. --verify-config only builds it; nothing is dispatched.
        import subprocess
        with tempfile.TemporaryDirectory() as home:
            cfg, out = Path(home) / 'h.lua', Path(home) / 'out.txt'
            args = send.move_command('0x55aa', 41).split('dispatch ', 1)[1]
            cfg.write_text(f'local ok, d = pcall(function() return {args} end)\n'
                           f'local f = io.open("{out}", "w") f:write(tostring(ok), " ", tostring(d)) f:close()\n')
            subprocess.run([HYPRLAND, '--verify-config', '-c', str(cfg)], env={'HOME': home, 'XDG_RUNTIME_DIR': home,
                           'PATH': '/usr/bin:/bin', 'HYPRLAND_INSTANCE_SIGNATURE': 'xr-send-test'}, cwd=home, timeout=60,
                           stdin=subprocess.DEVNULL, capture_output=True)
            self.assertEqual(out.read_text(), 'true HL.Dispatcher')


if __name__ == '__main__':
    unittest.main()
