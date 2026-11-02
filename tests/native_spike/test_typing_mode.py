"""Typing mode (Ctrl+Alt+T): one run bind that releases every other run bind, and gives them back."""
import json
import os
from pathlib import Path
import re
import tempfile
import unittest

from tools.native_spike import send, video, view, xr_control
from tools.native_spike.ipc import decode_control
from tools.native_spike.profiles import LayoutStore

NAMES = ['XR-1', 'XR-2', 'XR-3']
ARGS = ('/usr/bin/python3', '/opt/x/xr_control.py', '/run/user/1000/x/control.sock', 'run', 'session')
ROOT = Path(__file__).resolve().parents[2]


def frames(data):
    return [json.loads(line) for line in (data or b'').decode().splitlines()]


class Clock:
    def __init__(self): self.now = 100.0
    def __call__(self): return self.now


class ChordTests(unittest.TestCase):
    def test_the_toggle_is_ctrl_alt_t_and_clashes_with_no_other_project_bind(self):
        self.assertEqual(view.TYPING_BIND, ('CTRL + ALT + T', 'typing_toggle'))
        self.assertIn(view.TYPING_BIND, view.BINDS)
        from tools.native_spike import bind_check
        others = [c for c, op in view.BINDS if op != view.TYPING_OP] + [c for c, _ in send.SEND_BINDS + view.MENU_BINDS
                                                                         + view.MOUSE_BINDS + video.VIDEO_BINDS]
        self.assertNotIn(bind_check.normalize(view.TYPING_BIND[0]), {bind_check.normalize(c) for c in others})
        # Omarchy's own Ctrl+Alt+T relative is Super+Ctrl+Alt+T (show time): a different chord.
        self.assertNotEqual(bind_check.normalize('SUPER + CTRL + ALT + T'), bind_check.normalize(view.TYPING_BIND[0]))

    def test_it_is_a_key_op_not_a_panel_button(self):
        self.assertNotIn(view.TYPING_OP, view.USER_OPS)  # panel capabilities stay as they were
        self.assertIn(view.TYPING_OP, xr_control.OPS)
        frame = (json.dumps({'schema': 1, 'request_id': 5, 'run_id': 'run', 'session': 's', 'op': view.TYPING_OP,
                             'payload': {}}) + '\n').encode()
        self.assertEqual(decode_control(frame)['op'], view.TYPING_OP)
        with self.assertRaises(ValueError):
            decode_control((json.dumps({'schema': 1, 'request_id': 5, 'run_id': 'run', 'session': 's', 'op': view.TYPING_OP,
                                        'payload': {'on': True}}) + '\n').encode())

    def test_help_card_lists_it_and_still_has_twelve_rows(self):
        rows = view.help_rows()
        self.assertIn(['Ctrl+Alt+T', 'Typing mode: XR keys off / on'], rows)
        self.assertLessEqual(len(rows), 12)  # a 13th row does not fit the 960 px surface
        self.assertIn(['Ctrl+Alt+F / P', 'Toggle cursor follow / prediction'], rows)  # the pair that made room

    def test_readme_and_technical_docs_name_the_key(self):
        readme = (ROOT / 'README.md').read_text()
        self.assertRegex(readme, r'\| Ctrl\+Alt\+T \| Typing mode')
        self.assertIn('**Typing mode.** Press **Ctrl+Alt+T**', readme)
        self.assertIn('### Typing mode', (ROOT / 'docs/technical.md').read_text())


class BindLuaTests(unittest.TestCase):
    def test_typing_set_holds_only_the_toggle_in_the_same_table(self):
        lua = view.install_binds_lua(*ARGS, typing=True)
        self.assertEqual(lua.count('hl.bind('), 1)
        self.assertIn('hl.bind("CTRL + ALT + T", hl.dsp.exec_cmd(c .. "typing_toggle"))', lua)
        self.assertIn('xr_workspace_binds = {', lua)  # remove_binds_lua() at run end removes it like the full set
        full = view.install_binds_lua(*ARGS)
        self.assertEqual(full.count('hl.bind('), len(view.BINDS))
        self.assertIn('"typing_toggle"', full)
        with self.assertRaises(ValueError): view.install_binds_lua('/usr/bin/python3', '/opt/x.py', '/a b', 'r', 's', typing=True)

    def test_client_swaps_the_whole_set_in_one_eval(self):
        from tools.native_spike import hyprland as h
        sent = []
        client = h.HyprlandClient('a' * 40 + '_123_456', Path('/nonexistent'))
        client._eval = sent.append
        client.reinstall_view_binds(*ARGS, typing=True)
        # One eval: every run table goes (view, send, menu, mouse), then only the toggle comes back. No send-window binds.
        self.assertEqual(sent, [view.remove_binds_lua() + ' ' + view.install_binds_lua(*ARGS, typing=True)])
        for table in ('xr_workspace_binds = nil', 'xr_workspace_send_binds = nil', 'xr_workspace_menu_binds = nil',
                      'xr_workspace_mouse_binds = nil'):
            self.assertIn(table, sent[0])
        self.assertLessEqual(len(sent[0]), 4096)
        sent.clear(); client.reinstall_view_binds(*ARGS, typing=False)
        self.assertEqual(sent, [view.remove_binds_lua() + ' ' + view.install_binds_lua(*ARGS), view.install_send_binds_lua(*ARGS)])


class ControlTests(unittest.TestCase):
    def control(self, **kw):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.settings = LayoutStore(Path(tmp.name) / 'l.json')
        self.typing, self.menu = [], []
        c = view.ViewControl(view.ViewState(NAMES), 'run', 'session', lambda n: None, self.settings, None,
                             typing_binds=self.typing.append, menu_binds=self.menu.append, clock=Clock(), **kw)
        self.addCleanup(c.join_focus, 2)
        return c

    def send(self, c, op):
        out = frames(c.on_message({'op': op, 'payload': {}}))
        c.join_focus(2)
        return out

    def test_every_run_starts_in_control_mode_and_the_mode_is_never_saved(self):
        state = view.ViewState(NAMES)
        self.assertFalse(state.typing)
        self.assertFalse(state.view()['typing'])
        self.assertNotIn('typing', state.settings())
        c = self.control()
        self.send(c, view.TYPING_OP)
        self.assertNotIn('typing', view.load_settings(self.settings.path))

    def test_toggle_swaps_the_binds_and_tells_the_renderer(self):
        c = self.control()
        on = self.send(c, view.TYPING_OP)
        self.assertEqual([f['op'] for f in on], ['view'])
        self.assertTrue(on[0]['payload']['typing'])
        self.assertEqual(self.typing, [True])
        off = self.send(c, view.TYPING_OP)
        self.assertFalse(off[0]['payload']['typing'])
        self.assertEqual(self.typing, [True, False])

    def test_entering_closes_the_menu_and_the_help_card(self):
        c = self.control()
        self.send(c, 'menu_toggle'); self.send(c, 'help')
        self.assertTrue(c.state.menu_open and c.help_visible)
        out = self.send(c, view.TYPING_OP)
        self.assertEqual([f['op'] for f in out], ['placement_menu', 'help', 'view'])
        self.assertFalse(out[0]['payload']['open']); self.assertFalse(out[1]['payload']['visible'])
        self.assertFalse(c.state.menu_open); self.assertFalse(c.help_visible)
        self.assertEqual(self.menu[-1], False)  # the menu keys went back too
        # Leaving typing mode does not reopen anything.
        self.assertEqual([f['op'] for f in self.send(c, view.TYPING_OP)], ['view'])

    def test_late_key_only_ops_are_ignored_but_panel_ops_still_work(self):
        c = self.control()
        self.send(c, view.TYPING_OP)
        for op in ('menu_toggle', 'menu_inc', 'menu_mouse', 'menu_mouse_press') + send.SEND_OPS:
            with self.subTest(op): self.assertEqual(self.send(c, op), [])
        self.assertFalse(c.state.menu_open)
        zoomed = self.send(c, 'zoom_in')  # a panel button (USER_OPS) in typing mode
        self.assertTrue(zoomed[0]['payload']['zoomed'] and zoomed[0]['payload']['typing'])
        self.send(c, view.TYPING_OP)
        self.assertEqual([f['op'] for f in self.send(c, 'menu_toggle')], ['placement_menu'])  # keys are back

    def test_a_config_reload_keeps_the_mode(self):
        c = self.control()
        reloaded = []
        c.on_reload = lambda: reloaded.append(c.state.typing)
        self.send(c, view.TYPING_OP)
        c.on_events(b'configreloaded>>\n'); c.join_focus(2)
        self.assertEqual(reloaded, [True])
        self.assertEqual(self.typing, [True])  # on_reload put the toggle-only set back; no extra swap
        self.assertTrue(c._typing_installed)

    def test_a_failed_swap_is_retried_by_the_next_toggle(self):
        c = self.control()
        calls = []
        def flaky(on):
            calls.append(on)
            if len(calls) == 1: raise OSError('busy')
        c.typing_binds = flaky
        self.send(c, view.TYPING_OP)
        self.assertFalse(c._typing_installed)
        self.send(c, view.TYPING_OP)  # back to control: nothing to undo
        self.send(c, view.TYPING_OP)
        self.assertEqual(calls, [True, True])
        self.assertTrue(c._typing_installed)

    def test_run_end_stops_further_swaps(self):
        c = self.control()
        c.stop_menu_binds()
        c.on_message({'op': view.TYPING_OP}); c.join_focus(2)
        self.assertEqual(self.typing, [])

    def test_free_pointer_is_off_in_typing_mode(self):
        from tools.native_spike import free_pointer
        c = self.control()
        r = c.overview_pointer = free_pointer.OverviewPointer(c); self.addCleanup(r.close)
        self.assertTrue(r.overview())
        self.send(c, view.TYPING_OP)
        self.assertFalse(r.overview())
        self.send(c, view.TYPING_OP)
        self.assertTrue(r.overview())


class VideoTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.clip = Path(tmp.name) / 'Beach_360_TB.mp4'; self.clip.write_bytes(b'v' * 100)
        self.clock = Clock()
        self.view = view.ViewControl(view.ViewState(NAMES), 'run1', 's1', lambda n: None, LayoutStore(Path(tmp.name) / 'l.json'),
                                     None, clock=self.clock)
        self.video_binds = []
        info = {'width': 4096, 'height': 4096, 'codec': 'hevc', 'fps': 30.0, 'duration': 60.0, 'projection': None,
                'coverage': None, 'stereo': None}
        self.v = video.VideoControl(self.view, binds=self.video_binds.append, probe=lambda fd: info, threads=False,
                                    clock=self.clock)

    def join(self):
        for t in self.v._bind_threads + self.view._menu_threads: t.join(2)

    def test_typing_mode_releases_a_playing_videos_keys_and_gives_them_back(self):
        self.assertIsNone(self.v.on_message({'op': 'video_open', 'payload': {'path': str(self.clip)}}))
        out = self.v.poll(self.clock()); self.addCleanup(out.close_fds)
        self.join(); self.assertEqual(self.video_binds, [True])
        frame = frames(self.v.on_message({'op': view.TYPING_OP}))
        self.assertTrue(frame[-1]['payload']['typing'])
        self.join(); self.assertEqual(self.video_binds, [True, False])
        self.v.on_events(b'configreloaded>>\n'); self.join()
        self.assertEqual(self.video_binds, [True, False])  # a reload in typing mode does not bring them back
        self.v.on_message({'op': view.TYPING_OP}); self.join()
        self.assertEqual(self.video_binds, [True, False, True])
        self.assertTrue(self.v.active)  # the video kept playing throughout


class ManagedWiringTests(unittest.TestCase):
    def test_run_wires_the_swap_and_a_reload_reinstalls_the_current_mode(self):
        from unittest import mock
        from tools.native_spike import managed
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp) / 'run'; runtime.mkdir(mode=0o700)
            client = mock.Mock(runtime_dir=runtime, event_stream=None)
            client.query.return_value = []
            with managed.view_controls(client, 'run', 'session', NAMES, store=LayoutStore(Path(tmp) / 'l.json')) as control:
                args = client.install_view_binds.call_args.args
                control.on_message({'op': view.TYPING_OP}); control.join_focus(2)
                client.reinstall_view_binds.assert_called_once_with(*args, typing=True)
                client.reinstall_view_binds.reset_mock()
                control.on_events(b'configreloaded>>\n'); control.join_focus(2)
                client.reinstall_view_binds.assert_called_once_with(*args, typing=True)
                client.reinstall_view_binds.reset_mock()
                control.on_message({'op': view.TYPING_OP}); control.join_focus(2)
                client.reinstall_view_binds.assert_called_once_with(*args, typing=False)
                client.reinstall_view_binds.reset_mock()
                control.on_events(b'configreloaded>>\n'); control.join_focus(2)
                client.reinstall_view_binds.assert_called_once_with(*args)  # control mode: the reload call as before
            client.remove_view_binds.assert_called_once()  # run end removes the table, toggle included


if __name__ == '__main__':
    unittest.main()
