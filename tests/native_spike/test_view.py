"""Glasses view controls: zoom in/out, switch desktop, spacing; runtime binds; control client."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import unittest

from tools.native_spike.geometry import DEFAULT_PLACEMENT
from tools.native_spike import view
from tools.native_spike.profiles import LayoutStore
from tools.native_spike.ipc import decode_control

NAMES = ['XR-1', 'XR-2', 'XR-3']


class ViewStateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.settings = Path(self.tmp.name) / 'cfg' / 'layout.json'

    def test_transitions_focus_and_view(self):
        v = view.ViewState(NAMES, spacing=40)
        self.assertEqual(v.view(), {'zoomed': False, 'selected': 0, 'spacing': 40, 'mode': 'anchor', 'cursor_follow': False, 'prediction': True})
        self.assertEqual(v.apply('next'), 'XR-2')            # overview: highlight + focus
        self.assertEqual(v.apply('zoom_in'), 'XR-2')         # zoom into the highlighted desktop
        self.assertEqual(v.view(), {'zoomed': True, 'selected': 1, 'spacing': 40, 'mode': 'anchor', 'cursor_follow': False, 'prediction': True})
        self.assertEqual(v.apply('next'), 'XR-3')            # zoomed: slide + focus
        self.assertIsNone(v.apply('next'))                   # clamped at the last desktop: no change
        self.assertEqual(v.apply('prev'), 'XR-2')
        self.assertIsNone(v.apply('zoom_out'))               # back to overview keeps focus where it is
        self.assertEqual(v.view()['zoomed'], False)
        self.assertEqual(v.apply('prev'), 'XR-1'); self.assertIsNone(v.apply('prev'))
        with self.assertRaises(ValueError): v.apply('explode')

    def test_spacing_bounded_and_persisted(self):
        v = view.ViewState(NAMES, spacing=view.load_spacing(self.settings))
        self.assertEqual(v.view()['spacing'], view.DEFAULT_SPACING)
        for _ in range(100): v.apply('spacing_up')
        self.assertEqual(v.view()['spacing'], view.MAX_SPACING)
        view.save_spacing(self.settings, v.view()['spacing'])
        self.assertEqual(view.load_spacing(self.settings), view.MAX_SPACING)
        self.assertEqual(oct(self.settings.stat().st_mode & 0o777), '0o600')
        for _ in range(100): v.apply('spacing_down')
        self.assertEqual(v.view()['spacing'], 0)
        for bad in ('{"spacing": -1}', '{"spacing": 9999}', '{"spacing": true}', '[1]', 'not json', '{"spacing": 1.5}'):
            self.settings.write_text(bad)
            self.assertEqual(view.load_spacing(self.settings), view.DEFAULT_SPACING, bad)

    def test_renderer_frame_is_valid_control(self):
        frame = view.view_frame('run', 'session', 7, {'zoomed': True, 'selected': 2, 'spacing': 40, 'mode': 'follow'})
        self.assertTrue(frame.endswith(b'\n'))
        self.assertEqual(json.loads(frame)['op'], 'view')

    def test_user_ops_decode_and_others_rejected(self):
        from tools.native_spike import xr_control
        from tools.native_spike import resize
        self.assertEqual(xr_control.OPS, view.USER_OPS + view.MENU_OPS + view.MOUSE_OPS + resize.RESIZE_OPS)  # bind helper and supervisor agree
        self.assertIn('prediction', view.USER_OPS)
        for op in view.USER_OPS:
            frame = (json.dumps({'schema': 1, 'request_id': 5, 'run_id': 'run', 'session': 's', 'op': op, 'payload': {}}) + '\n').encode()
            self.assertEqual(decode_control(frame)['op'], op)
        bad = (json.dumps({'schema': 1, 'request_id': 5, 'run_id': 'run', 'session': 's', 'op': 'zoom_in', 'payload': {'x': 1}}) + '\n').encode()
        with self.assertRaises(ValueError): decode_control(bad)


class BindTests(unittest.TestCase):
    def test_help_bind_and_rows_cover_every_bind(self):
        self.assertIn(('CTRL + ALT + H', 'help'), view.BINDS)
        self.assertEqual({op for _, op in view.BINDS}, set(view.HELP_ACTIONS))
        self.assertEqual(set(view.USER_OPS) | {'menu_toggle', 'video_open', 'video_paste'}, set(view.HELP_ACTIONS))  # video.py, web_video.py
        self.assertEqual(view.chord_label('CTRL + ALT + bracketleft'), 'Ctrl+Alt+[')
        self.assertEqual(view.chord_label('CTRL + ALT + V'), 'Ctrl+Alt+V')
        for keys, action in view.help_rows():  # renderer limits: 1..48 printable ASCII, <= 24 rows
            for text in (keys, action): self.assertTrue(0 < len(text) <= 48 and text.isascii() and text.isprintable(), text)
        self.assertLessEqual(len(view.help_rows()), 24)


    def test_install_and_guarded_remove_lua(self):
        lua = view.install_binds_lua('/usr/bin/python3', '/opt/app/tools/native_spike/xr_control.py',
                                     '/run/user/1000/xr-workspace-spike/run/control.sock', 'run', 'session')
        for chord, op in view.BINDS:
            self.assertIn(f'hl.bind("{chord}", hl.dsp.exec_cmd(c .. "{op}"))', lua)
        self.assertTrue(lua.startswith('local c = "/usr/bin/python3 -I /opt/app/tools/native_spike/xr_control.py '
                                       '/run/user/1000/xr-workspace-spike/run/control.sock run session " xr_workspace_binds = {'))
        self.assertIn('xr_workspace_binds', lua)
        remove = view.remove_binds_lua()
        self.assertIn('HL.Keybind(expired)', remove)   # never remove an expired handle (0.56 segfault)
        self.assertIn('xr_workspace_binds = nil', remove)
        for bad in ('/run/user/1000/x"y', '/tmp/a b', 'relative/path', '/x;rm'):
            with self.subTest(bad), self.assertRaises(ValueError):
                view.install_binds_lua('/usr/bin/python3', '/opt/x.py', bad, 'run', 'session')

    def test_focus_command_allowlisted(self):
        self.assertEqual(view.focus_command('XR-2'), 'dispatch hl.dsp.focus({monitor="XR-2"})')
        for bad in ('DP-1', 'XR-4', 'XR-1"})', ''):
            with self.assertRaises(ValueError): view.focus_command(bad)


class ControlClientTests(unittest.TestCase):
    def test_client_sends_one_frame(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, 'c.sock')
        server = socket.socket(socket.AF_UNIX); server.bind(path); server.listen(1); self.addCleanup(server.close)
        got = []
        def accept():
            conn, _ = server.accept()
            with conn: got.append(conn.recv(4096))
        t = threading.Thread(target=accept); t.start()
        client = Path(view.__file__).with_name('xr_control.py')
        r = subprocess.run([sys.executable, '-I', str(client), path, 'run', 'session', 'zoom_in'], timeout=5)
        t.join(5)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(decode_control(got[0])['op'], 'zoom_in')
        bad = subprocess.run([sys.executable, '-I', str(client), path, 'run', 'session', 'rm -rf'], timeout=5)
        self.assertNotEqual(bad.returncode, 0)


if __name__ == '__main__':
    unittest.main()


class SupervisorControlTests(unittest.TestCase):
    """Real watchdog loop: a user op on the run's control socket reaches the renderer channel and focus."""
    def test_control_socket_drives_renderer_frame_and_focus(self):
        import time
        from tools.native_spike.ipc import PrivateServer
        from tools.native_spike.supervisor import Supervisor
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        runtime = Path(tmp.name) / 'rt'; runtime.mkdir(mode=0o700)
        settings = Path(tmp.name) / 'layout.json'
        focused, server = [], PrivateServer(runtime, 'run', 'session')
        self.addCleanup(server.close)
        control = view.ViewControl(view.ViewState(NAMES), 'run', 'session', focused.append, LayoutStore(settings), server)
        parent, renderer = socket.socketpair()
        self.addCleanup(parent.close); self.addCleanup(renderer.close)
        class Child:
            done = False
            def poll(self): return 0 if self.done else None
            def terminate(self): self.done = True
            def kill(self): self.done = True
        child = Child()
        sup = Supervisor(child, lambda: 'recovered', control=control)
        def drive():
            for op in ('next', 'zoom_in', 'spacing_up'):
                time.sleep(0.15)
                subprocess.run([sys.executable, '-I', str(Path(view.__file__).with_name('xr_control.py')),
                                str(server.path), 'run', 'session', op], timeout=5, check=True)
            time.sleep(0.3); renderer.sendall(b'H'); time.sleep(0.2); child.done = True
        t = threading.Thread(target=drive); t.start()
        renderer.sendall(b'H')
        self.assertEqual(sup.run(parent), 'recovered')
        t.join(5)
        frames = [json.loads(l) for l in renderer.recv(65536).decode().splitlines()]
        self.assertEqual([f['payload'] for f in frames],
                         [{'zoomed': False, 'selected': 1, 'spacing': 40, 'mode': 'anchor', 'cursor_follow': False, 'prediction': True},
                          {'zoomed': True, 'selected': 1, 'spacing': 40, 'mode': 'anchor', 'cursor_follow': False, 'prediction': True},
                          {'zoomed': True, 'selected': 1, 'spacing': 60, 'mode': 'anchor', 'cursor_follow': False, 'prediction': True}])
        ids = [f['request_id'] for f in frames]
        self.assertEqual(ids, sorted(ids)); self.assertGreater(ids[0], 1)  # start frame used 1
        control.join_focus(timeout=2)
        self.assertEqual(focused, ['XR-2', 'XR-2'])
        self.assertEqual(view.load_spacing(settings), 60)


class HyprlandViewRequestTests(unittest.TestCase):
    def test_requests_are_exact_and_allowlisted(self):
        from tools.native_spike.hyprland import HyprlandClient
        c = HyprlandClient('a' * 40 + '_1_2', Path('/run/user/1000'))
        sent = []
        c._request = lambda wire: sent.append(wire) or b'ok'
        c.mutate(view.focus_command('XR-3'))
        c.install_view_binds('/usr/bin/python3', '/opt/a/xr_control.py', '/run/user/1000/xr-workspace-spike/run/control.sock', 'run', 's')
        c.remove_view_binds()
        self.assertEqual(sent[0], '/dispatch hl.dsp.focus({monitor="XR-3"})')
        self.assertTrue(sent[1].startswith('/eval local c = "') and ' xr_workspace_binds = {' in sent[1])
        self.assertEqual(sent[2], '/eval ' + view.remove_binds_lua())
        for bad in ('dispatch hl.dsp.focus({monitor="DP-1"})', 'dispatch hl.dsp.exec_cmd("x")', 'eval os.execute("x")'):
            with self.assertRaises(ValueError): c.mutate(bad)
        c._request = lambda wire: b'some lua error'
        with self.assertRaises(ValueError): c.remove_view_binds()


class SupervisorResetTests(unittest.TestCase):
    def test_renderer_exit_with_unread_frame_is_a_disconnect(self):
        """A renderer that exits before reading a queued view frame resets the socket (ECONNRESET)."""
        from tools.native_spike.supervisor import Supervisor
        parent, renderer = socket.socketpair()
        self.addCleanup(parent.close)
        class Child:
            done = False
            def poll(self): return 0 if self.done else None
            def terminate(self): self.done = True
            def kill(self): self.done = True
        child = Child()
        parent.sendall(b'{"op":"view"}\n')     # queued, never read
        renderer.close()                       # renderer gone with unread data; child alive until terminated
        self.assertEqual(Supervisor(child, lambda: 'recovered').run(parent), 'recovered')


class ViewModeAndCursorFollowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.settings = Path(self.tmp.name) / 'cfg' / 'layout.json'

    def control(self, state=None, focused=None):
        return view.ViewControl(state or view.ViewState(NAMES), 'run', 'session',
                                (focused if focused is not None else []).append, LayoutStore(self.settings), None)

    def test_mode_cycles_and_is_saved_with_spacing(self):
        c = self.control()
        modes = [json.loads(c.on_message({'op': 'mode_next'}))['payload']['mode'] for _ in range(3)]
        self.assertEqual(modes, ['follow', 'locked', 'anchor'])
        c.on_message({'op': 'mode_next'}); c.on_message({'op': 'spacing_up'})
        self.assertEqual(view.load_settings(self.settings), {'spacing': 60, 'mode': 'follow', 'cursor_follow': False, 'prediction': True})
        self.assertEqual(oct(self.settings.stat().st_mode & 0o777), '0o600')
        self.assertIn(('CTRL + ALT + V', 'mode_next'), view.BINDS)
        self.assertIn(('CTRL + ALT + F', 'cursor_follow'), view.BINDS)
        self.assertIn(('CTRL + ALT + P', 'prediction'), view.BINDS)

    def test_prediction_toggles_reaches_renderer_and_is_saved(self):
        c = self.control()
        self.assertTrue(view.DEFAULT_SETTINGS['prediction'])             # on by default (user choice after live prd2)
        frame = json.loads(c.on_message({'op': 'prediction'}))
        self.assertEqual(frame['op'], 'view')
        self.assertEqual(frame['payload'], {'zoomed': False, 'selected': 0, 'spacing': 40, 'mode': 'anchor', 'cursor_follow': False,
                                            'prediction': False})
        self.assertFalse(view.load_settings(self.settings)['prediction'])  # an explicit off is kept
        self.assertTrue(json.loads(c.on_message({'op': 'prediction'}))['payload']['prediction'])
        self.assertTrue(view.load_settings(self.settings)['prediction'])
        self.assertFalse(view.ViewState(NAMES, prediction=False).view()['prediction'])
        with self.assertRaises(ValueError): view.ViewState(NAMES, prediction='yes')

    def test_settings_fields_fall_back_independently(self):
        self.settings.parent.mkdir(parents=True)
        self.settings.write_text('{"spacing": 80}')  # file written before modes existed
        self.assertEqual(view.load_settings(self.settings), {'spacing': 80, 'mode': 'anchor', 'cursor_follow': False, 'prediction': True})
        self.settings.write_text('{"spacing": 9999, "mode": "orbit", "cursor_follow": 1, "prediction": "on"}')
        self.assertEqual(view.load_settings(self.settings), view.DEFAULT_SETTINGS)
        for bad in ({'spacing': 40, 'mode': 'orbit', 'cursor_follow': False, 'prediction': True}, {'spacing': 40, 'mode': 'anchor'},
                    {'spacing': 40, 'mode': 'anchor', 'cursor_follow': 'yes'}):
            with self.subTest(bad), self.assertRaises(ValueError): view.save_settings(self.settings, bad)
        with self.assertRaises(ValueError): view.ViewState(NAMES, mode='orbit')

    def test_help_toggles_card_with_every_bind_and_leaves_view_alone(self):
        c = self.control(focused=[])
        before = c.state.view()
        shown = json.loads(c.on_message({'op': 'help'}))
        self.assertEqual(shown['op'], 'help')
        self.assertTrue(shown['payload']['visible'])
        rows = shown['payload']['rows']
        self.assertEqual(rows, view.help_rows())
        self.assertEqual(len(rows), len(view.BINDS) - len(view.HELP_PAIRS) + len(view.EXTRA_HELP_ROWS))
        self.assertLessEqual(len(rows), 12)  # 13 rows overflow the 960 px glasses surface
        keys = [k for k, _ in rows]
        def covered(label):  # its own row, or one half of a paired row such as 'Ctrl+Alt+[ / ]'
            prefix, key = label[:label.rfind('+') + 1], label[label.rfind('+') + 1:]
            return label in keys or any(k.startswith(prefix) and key in k[len(prefix):].split(' / ') for k in keys)
        for chord, op in view.BINDS:
            with self.subTest(op=op): self.assertTrue(covered(view.chord_label(chord)))
        self.assertIn(['Ctrl+Alt+[ / ]', 'Less / more space between desktops'], rows)
        self.assertIn(['Ctrl+Alt+O / U', 'VR video: file / copied web link'], rows)
        self.assertIn(['Ctrl+Alt+H', 'Show / hide this help'], shown['payload']['rows'])
        hidden = json.loads(c.on_message({'op': 'help'}))
        self.assertFalse(hidden['payload']['visible'])
        self.assertGreater(hidden['request_id'], shown['request_id'])
        self.assertEqual(c.state.view(), before)
        self.assertEqual(view.load_settings(self.settings), view.DEFAULT_SETTINGS)  # never saved

    def test_cursor_follow_switches_only_when_enabled_and_only_managed(self):
        focused = []
        c = self.control(focused=focused)
        self.assertIsNone(c.on_events(b'focusedmon>>XR-2,7\n'))          # off by default: ignored
        toggled = json.loads(c.on_message({'op': 'cursor_follow'}))       # renderer shows the cursor pop-up
        self.assertEqual(toggled['payload'], {'zoomed': False, 'selected': 0, 'spacing': 40, 'mode': 'anchor', 'cursor_follow': True, 'prediction': True})
        self.assertTrue(view.load_settings(self.settings)['cursor_follow'])
        frame = c.on_events(b'workspace>>3\nfocusedmon>>XR-')            # split across reads
        self.assertIsNone(frame)
        frame = c.on_events(b'3,8\nfocusedmonv2>>XR-3,8\n')
        self.assertEqual(json.loads(frame)['payload']['selected'], 2)
        self.assertIsNone(c.on_events(b'focusedmon>>XR-3,8\n'))           # already selected
        for other in (b'focusedmon>>DP-1,1\n', b'focusedmon>>XR-9,1\n', b'focusedmon>>XR-1\n', b'\xff\xfe\n'):
            self.assertIsNone(c.on_events(other), other)
        self.assertIsNone(c.on_events(b'x' * 70000))                      # bounded buffer
        self.assertEqual(json.loads(c.on_events(b'\nfocusedmon>>XR-1,1\n'))['payload']['selected'], 0)
        self.assertEqual(focused, [])                                     # pointer already there: no focus dispatch
        self.assertEqual(c.cursor_switches, 2)


class SupervisorCursorFollowTests(unittest.TestCase):
    def test_event_socket_drives_renderer_and_eof_is_not_fatal(self):
        import time
        from tools.native_spike.supervisor import Supervisor
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        control = view.ViewControl(view.ViewState(NAMES, cursor_follow=True), 'run', 'session', lambda n: None,
                                   LayoutStore(Path(tmp.name) / 'l.json'), type('S', (), {'socket': socket.socketpair()[0]})())
        hypr, events = socket.socketpair(); events.setblocking(False)
        control.events = events
        self.addCleanup(hypr.close); self.addCleanup(events.close)
        parent, renderer = socket.socketpair()
        self.addCleanup(parent.close); self.addCleanup(renderer.close)
        class Child:
            done = False
            def poll(self): return 0 if self.done else None
            def terminate(self): self.done = True
            def kill(self): self.done = True
        child = Child()
        sup = Supervisor(child, lambda: 'recovered', control=control)
        def drive():
            time.sleep(0.15); hypr.sendall(b'focusedmon>>XR-2,7\n')
            time.sleep(0.15); hypr.close()                          # compositor stream ends: follow just stops
            time.sleep(0.15); renderer.sendall(b'H'); time.sleep(0.15); child.done = True
        t = threading.Thread(target=drive); t.start()
        renderer.sendall(b'H')
        self.assertEqual(sup.run(parent), 'recovered')
        t.join(5)
        frames = [json.loads(l) for l in renderer.recv(65536).decode().splitlines()]
        self.assertEqual([f['payload']['selected'] for f in frames], [1])


class EventStreamTests(unittest.TestCase):
    def test_event_stream_uses_socket2_with_peer_checks(self):
        from tools.native_spike import hyprland as h
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        root = Path(tmp.name); root.chmod(0o700)
        instance = 'a' * 40 + '_123_456'
        d = root / 'hypr' / instance; d.mkdir(parents=True, mode=0o700); (root / 'hypr').chmod(0o700)
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); server.bind(str(d / '.socket2.sock')); server.listen(1)
        self.addCleanup(server.close)
        conn = h.HyprlandClient(instance, root).event_stream()
        self.addCleanup(conn.close)
        peer, _ = server.accept(); self.addCleanup(peer.close)
        peer.sendall(b'focusedmon>>XR-1,1\n')
        import select
        select.select([conn], [], [], 2)
        self.assertEqual(conn.recv(100), b'focusedmon>>XR-1,1\n')
        self.assertFalse(conn.getblocking())
        with self.assertRaises(ValueError): h.HyprlandClient(instance, root)._connect(0, '../x')


class ThemeAccentTests(unittest.TestCase):
    def test_parse_border(self):
        self.assertEqual(view.parse_border({'option': 'general:col.active_border', 'gradient': 'ff8d8d8d 0deg', 'set': True}), (0x8d, 0x8d, 0x8d))
        self.assertEqual(view.parse_border({'gradient': 'ee33ccff ee00ff99 45deg'}), (0x33, 0xcc, 0xff))  # first colour
        self.assertEqual(view.parse_border({'int': 0xff112233}), (0x11, 0x22, 0x33))
        for bad in (None, [], {}, {'gradient': ''}, {'gradient': 'zz 0deg'}, {'gradient': 'ff8d8d 0deg'}, {'int': -1}, {'int': True}):
            self.assertIsNone(view.parse_border(bad), bad)

    def test_initial_and_reload_send_style_only_on_change(self):
        replies = [{'gradient': 'ff8d8d8d 0deg'}]
        c = view.ViewControl(view.ViewState(NAMES), 'run', 'session', lambda n: None,
                             LayoutStore(Path(tempfile.mkdtemp()) / 'l.json'), None, border=lambda: replies[-1])
        frames = [json.loads(l) for l in c.initial_frame().decode().splitlines()]
        self.assertEqual([f['op'] for f in frames], ['view', 'style'])
        self.assertEqual(frames[1]['payload'], {'border': [0x8d, 0x8d, 0x8d]})
        self.assertGreater(frames[1]['request_id'], frames[0]['request_id'])
        for f in frames: decode_control_renderer_shape(self, f)
        self.assertIsNone(c.on_events(b'configreloaded>>\n'))                 # same theme: nothing sent
        replies.append({'gradient': 'ffc4a7e7 0deg'})                       # theme switched
        frame = json.loads(c.on_events(b'configreloaded>>\n'))
        self.assertEqual((frame['op'], frame['payload']), ('style', {'border': [0xc4, 0xa7, 0xe7]}))
        def broken(): raise OSError('compositor busy')
        c.border = broken
        self.assertIsNone(c.on_events(b'configreloaded>>\n'))                 # lookup failure is never fatal

    def test_query_allowlist_and_timeout(self):
        from tools.native_spike import hyprland as h
        client = h.HyprlandClient('a' * 40 + '_123_456', Path('/nonexistent'))
        for args in (('getoption', 'general:col.inactive_border'), ('getoption', 'decoration:rounding')):
            with self.assertRaises(ValueError): client.query(*args)
        with self.assertRaises(ValueError): client.query('getoption', 'general:col.active_border', timeout=5)


def decode_control_renderer_shape(test, frame):
    test.assertEqual(set(frame), {'schema', 'request_id', 'run_id', 'session', 'op', 'payload'})


class ConfigReloadTests(unittest.TestCase):
    MON = {'name': 'DP-2', 'width': 1920, 'height': 1200, 'refreshRate': 119.999, 'x': 4096, 'y': 0, 'scale': 1.25}

    def test_session_mode_only_for_the_granted_mode(self):
        self.assertEqual(view.session_mode(self.MON), {'name': 'DP-2', 'x': 4096, 'y': 0, 'scale': 1.25})
        for change in ({'width': 1920, 'height': 1080}, {'refreshRate': 60.0}, {'name': 'XR-1'}, {'name': 'DP-2"})'},
                       {'x': 1.5}, {'scale': 9}):
            self.assertIsNone(view.session_mode({**self.MON, **change}), change)
        self.assertIsNone(view.session_mode(None))

    def test_restore_lua_is_fixed_shape(self):
        lua = view.restore_mode_lua(view.session_mode(self.MON))
        self.assertEqual(lua, 'hl.monitor({ output = "DP-2", mode = "1920x1200@120", position = "4096x0", scale = 1.25 })')
        for bad in ({'name': 'DP-2"}) os.exit()--', 'x': 0, 'y': 0, 'scale': 1.0}, {'name': 'DP-2', 'x': '0', 'y': 0, 'scale': 1.0}):
            with self.assertRaises(ValueError): view.restore_mode_lua(bad)

    def test_reload_event_runs_reload_work_off_loop_and_refreshes_style(self):
        done = threading.Event()
        c = view.ViewControl(view.ViewState(NAMES), 'run', 'session', lambda n: None, LayoutStore(Path(tempfile.mkdtemp()) / 'l.json'), None,
                             border=lambda: {'gradient': 'ff112233 0deg'}, on_reload=done.set)
        frame = c.on_events(b'configreloaded>>\n')
        self.assertEqual(json.loads(frame)['op'], 'style')
        self.assertTrue(done.wait(2)); self.assertEqual(c.reloads, 1)
        def boom(): raise OSError('busy')
        c.on_reload = boom
        c.on_events(b'configreloaded>>\n'); c.join_focus(2)                   # failure is swallowed

    def test_reinstall_removes_then_binds_in_one_eval(self):
        from tools.native_spike import hyprland as h
        sent = []
        client = h.HyprlandClient('a' * 40 + '_123_456', Path('/nonexistent'))
        client._eval = sent.append
        client.reinstall_view_binds('/usr/bin/python3', '/opt/x/xr_control.py', '/run/user/1000/x/control.sock', 'run', 'session')
        self.assertEqual(len(sent), 1)
        self.assertLess(sent[0].index('HL.Keybind(expired)'), sent[0].index('hl.bind('))
        self.assertLessEqual(len(sent[0]), 4096)
        self.assertEqual(sent[0].count('hl.bind('), len(view.BINDS))
        client.restore_session_mode(view.session_mode(self.MON))
        self.assertIn('1920x1200@120', sent[1])

class StatusReplyTests(unittest.TestCase):
    def test_status_op_reply_shape(self):
        c = view.ViewControl(view.ViewState(['XR-1']), 'run', 'session', None, None, None)
        self.assertEqual(c.status_reply({'missed':2}), dict(capabilities=list(view.USER_OPS), view=c.state.view(), status={'missed':2},
                                                            placements=[dict(DEFAULT_PLACEMENT)]))
        self.assertNotIn('placement', c.status_reply({})['capabilities'])  # the panel renders capabilities as buttons
        self.assertIsNone(c.on_message({'op':'status'}))

    def test_status_reply_over_real_private_socket(self):
        from tools.xr_controller.__main__ import Units
        from tools.native_spike.ipc import PrivateServer
        from tools.native_spike.supervisor import Supervisor
        with tempfile.TemporaryDirectory() as tmp:
            runtime=Path(tmp)/'rt'; runtime.mkdir(mode=0o700)
            with PrivateServer(runtime,'run','session') as server:
                c=view.ViewControl(view.ViewState(['XR-1']),'run','session',None,None,server)
                sup=Supervisor(None,None,control=c); sup.status={'missed':7}
                parent,renderer=socket.socketpair()
                try:
                    result=[]
                    thread=threading.Thread(target=lambda:result.append(Units(runtime).control('run','session','status')))
                    thread.start(); sup._control_ready(parent); thread.join(2)
                    self.assertIsInstance(result[0],dict)
                    self.assertEqual(result[0]['status'],{'missed':7})
                    renderer.setblocking(False)
                    with self.assertRaises(BlockingIOError): renderer.recv(1024)
                finally: parent.close(); renderer.close()
