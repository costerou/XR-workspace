"""Overview corner resize and glass pill moves, shared pieces (resize.py): monitor rectangles, the modal left-button
binds and their removal, and the help card. The pointer, hit testing, drags and wiring are tested in test_free_pointer.py
and test_glass_pills.py.

Offline only: fake compositor clients. No compositor, renderer binary or glasses.
"""
import json
from pathlib import Path
import unittest
from unittest import mock

from tools.native_spike import resize, view, xr_control
from tools.native_spike.ipc import decode_control

NAMES = ['XR-1', 'XR-2', 'XR-3']
# Three managed outputs as plan_outputs lays them out right of a 2560-wide physical monitor; XR-2 at 125 % scale.
MONITORS = [{'name': 'DP-1', 'x': 0, 'y': 0, 'width': 2560, 'height': 1440, 'scale': 1.0},
            {'name': 'XR-1', 'x': 2560, 'y': 0, 'width': 1920, 'height': 1080, 'scale': 1.0},
            {'name': 'XR-2', 'x': 4480, 'y': 0, 'width': 2560, 'height': 1440, 'scale': 1.25},
            {'name': 'XR-3', 'x': 6528, 'y': 0, 'width': 1920, 'height': 1080, 'scale': 1.0}]
OUTPUTS = resize.outputs_from_monitors(MONITORS, NAMES)
LIVE_BIND_ARGS = ('/usr/bin/python3', '/home/cody/.t3/worktrees/viture-workspace/feature-placement-menu-integrate/tools/native_spike/xr_control.py',
                  '/run/user/1000/xr-workspace-spike/' + 'a' * 32 + '/control.sock', 'a' * 32, 'e' * 64)


class GeometryTests(unittest.TestCase):
    def test_outputs_are_logical_rectangles_in_run_order(self):
        self.assertEqual([o['index'] for o in OUTPUTS], [0, 1, 2])
        self.assertEqual((OUTPUTS[1]['x'], OUTPUTS[1]['width'], OUTPUTS[1]['height']), (4480, 2048.0, 1152.0))
        # Missing or malformed outputs are skipped, never guessed.
        broken = [dict(MONITORS[1], width='1920'), dict(MONITORS[3], scale=0)]
        self.assertEqual(resize.outputs_from_monitors(broken, NAMES), [])
        self.assertEqual([o['index'] for o in resize.outputs_from_monitors(MONITORS[2:3], NAMES)], [1])
        self.assertEqual(resize.outputs_from_monitors(None, NAMES), [])

class BindTests(unittest.TestCase):
    def test_press_and_release_binds_on_the_left_button_only(self):
        lua = resize.install_resize_binds_lua(*LIVE_BIND_ARGS)
        self.assertTrue(lua.startswith(resize.remove_resize_binds_lua()))  # replaces a surviving set, never doubled
        self.assertEqual(lua.count('hl.bind('), 2)
        self.assertEqual(lua.count('hl.bind("mouse:272", hl.dsp.exec_cmd(c .. "resize_press"))'), 1)
        self.assertEqual(lua.count('hl.bind("mouse:272", hl.dsp.exec_cmd(c .. "resize_release"), { release = true })'), 1)
        self.assertIn('local c = "{} -I {} {} {} {} "'.format(*LIVE_BIND_ARGS), lua)
        self.assertNotIn('os.execute', lua); self.assertNotIn('io.', lua)
        self.assertLess(len(lua), 4096)  # HyprlandClient._eval cap; a separate eval from the run binds

    def test_unsafe_arguments_are_refused(self):
        for i, bad in ((0, 'python3'), (1, '/x/"; os.execute("y'), (3, 'run id'), (4, '')):
            args = list(LIVE_BIND_ARGS); args[i] = bad
            with self.subTest(bad), self.assertRaises(ValueError): resize.install_resize_binds_lua(*args)

    def test_the_bind_helper_knows_the_ops_and_the_fixed_removal_text(self):
        i = xr_control.OPS.index(resize.RESIZE_OPS[0])
        self.assertEqual(xr_control.OPS[i:i + 2], resize.RESIZE_OPS)  # then send.SEND_OPS, view.TYPING_OP and LEGACY_OPS
        self.assertEqual(xr_control.REMOVE_RESIZE_BINDS, resize.remove_resize_binds_lua())
        self.assertFalse(set(resize.RESIZE_OPS) & set(view.USER_OPS))  # not panel buttons
        for op in resize.RESIZE_OPS:
            frame = (json.dumps({'schema': 1, 'request_id': 5, 'run_id': 'run', 'session': 's', 'op': op, 'payload': {}}) + '\n').encode()
            self.assertEqual(decode_control(frame)['op'], op)
            with self.assertRaises(ValueError):  # no payload smuggling (no sizes from the bind side)
                decode_control((json.dumps({'schema': 1, 'request_id': 5, 'run_id': 'run', 'session': 's', 'op': op,
                                            'payload': {'distance': 50}}) + '\n').encode())

    def test_dead_run_resize_click_releases_the_left_button(self):
        dead = '/nonexistent/control.sock'
        with mock.patch.object(xr_control, 'drop_stale_menu_binds') as drop:
            self.assertEqual(xr_control.main(['x', dead, 'run', 's', 'resize_press']), 1)
            drop.assert_called_once_with(code=xr_control.REMOVE_RESIZE_BINDS)
            self.assertEqual(xr_control.main(['x', dead, 'run', 's', 'zoom_in']), 1)
            self.assertEqual(drop.call_count, 1)

    def test_hyprland_client_sends_generated_lua_only(self):
        from tools.native_spike import hyprland as h
        client = h.HyprlandClient('f' * 40 + '_1793506481_1554975112', Path('/nonexistent'))
        sent = []
        client._request = lambda wire: sent.append(wire) or b'ok'
        client.install_resize_binds(*LIVE_BIND_ARGS); client.remove_resize_binds()
        self.assertEqual(sent, ['/eval ' + resize.install_resize_binds_lua(*LIVE_BIND_ARGS), '/eval ' + resize.remove_resize_binds_lua()])

    def test_run_bind_set_and_allowlists_are_unchanged(self):
        from tools.native_spike import hyprland as h
        self.assertNotIn('mouse', view.install_binds_lua(*LIVE_BIND_ARGS))
        self.assertEqual(h.QUERY, {'monitors', 'workspaces', 'clients', 'activewindow', 'getoption', 'cursorpos'})


class HelpCardFitTests(unittest.TestCase):
    def test_the_fit_tested_help_card_is_the_real_one_and_the_free_pointer_is_on_it(self):
        # help_card_test checks the help card fits 1920x1200 @1.25 (960 px tall) on one page at full size, with three rows
        # to spare, and pages any row count without clipping at every scale from 1 to 2. It must check the rows and
        # sections the run really sends (a row added only here once went untested). No row cap: rows that do not fit
        # flow onto further pages.
        import re
        src = (Path(__file__).resolve().parents[2] / 'spike/tests/help_card_test.cpp').read_text()
        body = re.search(r'std::vector<HelpRow> help_rows\(\) \{.*?\n\}', src, re.S).group(0)
        found = re.findall(r'\{"([^"]+)", "([^"]+)"(, true)?\}', body)
        self.assertEqual([[k, a] for k, a, _ in found], view.help_rows())
        starts = [i for i, (_, _, section) in enumerate(found) if section]  # rows starting a section after the first
        self.assertEqual(starts, [sum(view.help_sections()[:n]) for n in range(1, len(view.help_sections()))])
        self.assertTrue(any('free mouse' in action and 'pills' in action and 'corner' in action
                            for _, action in view.help_rows()))


if __name__ == '__main__':
    unittest.main()
