"""Overview drag bar (grab pill), shared pieces (resize.py): the overlap guard, the ops and the cursor restore on bind
removal. Hit zones, the bar drag itself and the hidden cursor are tested in test_free_pointer.py.

Offline only. No compositor, renderer binary or glasses.
"""
import unittest
from unittest import mock

from tools.native_spike import resize, xr_control
from tools.native_spike.geometry import DEFAULT_PLACEMENT

ASPECTS = [16 / 9] * 3
FLAT = [dict(DEFAULT_PLACEMENT) for _ in range(3)]
BIND_ARGS = ('/usr/bin/python3', '/x/tools/native_spike/xr_control.py', '/run/user/1000/c.sock', 'a' * 32, 'e' * 64)


class MoveMathTests(unittest.TestCase):
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


class OpsTests(unittest.TestCase):
    def test_resize_ops_are_unchanged_so_video_and_menu_gates_cover_the_pill(self):
        self.assertEqual(resize.RESIZE_OPS, ('resize_press', 'resize_release'))
        self.assertTrue(set(resize.RESIZE_OPS) <= set(xr_control.OPS))  # the pill adds no op of its own
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
