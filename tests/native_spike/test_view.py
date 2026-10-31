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

from tools.native_spike import view
from tools.native_spike.ipc import decode_control

NAMES = ['XR-1', 'XR-2', 'XR-3']


class ViewStateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.settings = Path(self.tmp.name) / 'cfg' / 'layout.json'

    def test_transitions_focus_and_view(self):
        v = view.ViewState(NAMES, spacing=40)
        self.assertEqual(v.view(), {'zoomed': False, 'selected': 0, 'spacing': 40})
        self.assertEqual(v.apply('next'), 'XR-2')            # overview: highlight + focus
        self.assertEqual(v.apply('zoom_in'), 'XR-2')         # zoom into the highlighted desktop
        self.assertEqual(v.view(), {'zoomed': True, 'selected': 1, 'spacing': 40})
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
        frame = view.view_frame('run', 'session', 7, {'zoomed': True, 'selected': 2, 'spacing': 40})
        self.assertTrue(frame.endswith(b'\n'))
        self.assertEqual(json.loads(frame)['op'], 'view')

    def test_user_ops_decode_and_others_rejected(self):
        for op in view.USER_OPS:
            frame = (json.dumps({'schema': 1, 'request_id': 5, 'run_id': 'run', 'session': 's', 'op': op, 'payload': {}}) + '\n').encode()
            self.assertEqual(decode_control(frame)['op'], op)
        bad = (json.dumps({'schema': 1, 'request_id': 5, 'run_id': 'run', 'session': 's', 'op': 'zoom_in', 'payload': {'x': 1}}) + '\n').encode()
        with self.assertRaises(ValueError): decode_control(bad)


class BindTests(unittest.TestCase):
    def test_install_and_guarded_remove_lua(self):
        lua = view.install_binds_lua('/usr/bin/python3', '/opt/app/tools/native_spike/xr_control.py',
                                     '/run/user/1000/xr-workspace-spike/run/control.sock', 'run', 'session')
        for chord, op in view.BINDS:
            self.assertIn(f'hl.bind("{chord}"', lua); self.assertIn(f' {op}")', lua)
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
        control = view.ViewControl(view.ViewState(NAMES), 'run', 'session', focused.append, settings, server)
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
                         [{'zoomed': False, 'selected': 1, 'spacing': 40}, {'zoomed': True, 'selected': 1, 'spacing': 40},
                          {'zoomed': True, 'selected': 1, 'spacing': 60}])
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
        self.assertTrue(sent[1].startswith('/eval xr_workspace_binds = {') and sent[2] == '/eval ' + view.remove_binds_lua())
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
