"""Overview glass pill moves, shared pieces (resize.py): the ops and the cursor restore on bind removal; a smoke test of
the no-overlap guard (orbit.py, in depth in test_orbit.py). Hit zones, the pill drag itself and the hidden cursor are
tested in test_free_pointer.py; pill clicks, the placement panel and its sliders in test_glass_pills.py.

Offline only. No compositor, renderer binary or glasses.
"""
import json
from pathlib import Path
import unittest
from unittest import mock

from tools.native_spike import free_pointer as fp, orbit, resize, xr_control
from tools.native_spike.geometry import DEFAULT_PLACEMENT

ROOT = Path(__file__).resolve().parents[2]
G = fp.parse_overview_geometry(json.loads(json.loads((ROOT / 'tests/native_spike/fixtures/overview_pointer_payloads.json')
                                                     .read_text())['geometry']['line'])['overview_geometry'])
FLAT = [dict(DEFAULT_PLACEMENT) for _ in range(3)]
BIND_ARGS = ('/usr/bin/python3', '/x/tools/native_spike/xr_control.py', '/run/user/1000/c.sock', 'a' * 32, 'e' * 64)


class MoveMathTests(unittest.TestCase):
    """The move maths moved to orbit.py with 360-degree placement (2026-11-02); test_orbit.py covers it in depth."""
    def test_the_old_flat_guard_is_gone_and_the_orbit_guard_allows_no_overlap(self):
        for name in ('apparent_rects', 'overlap_ok', 'allowed_move', 'MAX_OVERLAP', 'SNAP_PCT'):
            self.assertFalse(hasattr(resize, name), name)
        x, y = orbit.allowed_move(G, 0, (0, 0), (600, 0), FLAT)  # straight into XR-2: stops before it touches
        self.assertEqual(y, 0); self.assertLess(x, 9)
        self.assertEqual(orbit.overlap(orbit.footprint(G, G['tiles'][0], {**FLAT[0], 'x': x}), orbit.footprint(G, G['tiles'][1], FLAT[1])), 0)
        self.assertEqual(orbit.allowed_move(G, 0, (0, 0), (0, 300), FLAT), (0, 300))  # straight up: free
        lifted = [{**FLAT[0], 'y': 300}] + FLAT[1:]
        self.assertEqual(orbit.allowed_move(G, 0, (0, 300), (400, 300), lifted), (400, 300))  # over the row: free


class OpsTests(unittest.TestCase):
    def test_resize_ops_are_unchanged_so_video_and_typing_gates_cover_the_pill(self):
        self.assertEqual(resize.RESIZE_OPS, ('resize_press', 'resize_release'))
        self.assertTrue(set(resize.RESIZE_OPS) <= set(xr_control.OPS))  # the pill (move, click, panel) adds no op of its own
        self.assertFalse(any('move' in op or 'pill' in op for op in xr_control.OPS))


class LuaTests(unittest.TestCase):
    def test_bind_removal_also_brings_a_hidden_cursor_back(self):
        self.assertIn(resize.GRAB_LEAVE_LUA, resize.remove_resize_binds_lua())
        self.assertEqual(xr_control.REMOVE_RESIZE_BINDS, resize.remove_resize_binds_lua())
        self.assertIn(resize.GRAB_LEAVE_LUA, resize.install_resize_binds_lua(*BIND_ARGS))  # a reinstall starts clean


class DeadRunTests(unittest.TestCase):
    def test_dead_run_release_click_restores_the_cursor(self):
        with mock.patch.object(xr_control, 'drop_stale_menu_binds') as drop:
            self.assertEqual(xr_control.main(['x', '/nonexistent/c.sock', 'run', 's', 'resize_release']), 1)
        drop.assert_called_once_with(code=xr_control.REMOVE_RESIZE_BINDS)
        self.assertIn('xr_workspace_grab', xr_control.REMOVE_RESIZE_BINDS)


if __name__ == '__main__':
    unittest.main()
