"""The level bar, pressed with the tilts the renderer reports (level_frame_test --dump: the pose feed as XRLinuxDriver really
publishes it, which is relative to wherever the glasses were when calibration finished, through PoseState and desktop_tilt).
Every way of levelling a desktop lands on level, a few degrees from where it was, and the maximize-level rules still hold.

The bug this pins (Cody, 2026-11-03, in the glasses): the feed's z was taken for gravity, so with the glasses upside down as
calibration finished every desktop measured about -165 degrees, and the middle segment turned it to the +90 degree bound.

usage: level_contract_test.py LEVEL_FRAME_TEST REPO_ROOT
"""
import json
import math
from pathlib import Path
import subprocess
import sys
import unittest

dump, root = sys.argv[1], sys.argv[2]
sys.path[:0] = [root, str(Path(root) / 'tests' / 'native_spike')]
from tools.native_spike import free_pointer as fp  # noqa: E402
from test_glass_pills import Base, G, T0, T1, T2, last  # noqa: E402
from test_roll_level import FLAT, grip  # noqa: E402

SCENARIOS = json.loads(subprocess.run([dump, '--dump'], check=True, capture_output=True, text=True).stdout)
TILES = (T0, T1, T2)
NEAR = 300  # the tilts are the head's, not the world's: within 3 degrees of the truth
SMALL = 3000  # nobody recentres further off than this in these scenarios


class EveryWayOfLevelling(Base):
    def setUp(self):
        super().setUp()
        self.free()

    def each(self):
        for name, s in SCENARIOS.items():
            with self.subTest(name):
                self.control.set_roll_geometry(s['tilt'])
                self.p.rolls = [0] * fp.MAX_DESKTOPS
                yield s['tilt'], s['truth']

    def landed(self, i, tilt, truth):
        """Desktop i ended level: the roll that cancels its tilt, near the one that cancels the true tilt, and small."""
        r = self.p.rolls[i]
        self.assertEqual(r, -tilt[i])
        self.assertLessEqual(abs(r), SMALL, 'not a levelling roll')
        self.assertLessEqual(abs(r + truth[i]), NEAR)

    def test_the_middle_segment(self):
        for tilt, truth in self.each():
            for i, tile in enumerate(TILES):
                self.to(*grip(tile, 'level', roll=self.p.rolls[i]))
                self.assertEqual(self.p.hover, ('roll', i, 'level'))
                r = last(self.press() + self.release(), 'roll')
                self.landed(i, tilt, truth)
                if tilt[i]: self.assertEqual(r['roll'][i], -tilt[i])  # (no frame goes out when nothing changed)
                self.clock.now += fp.LEVEL_SETTLE_S + 0.1

    def test_the_turn_segment_on_the_side_level_lies(self):
        for tilt, truth in self.each():
            for i, tile in enumerate(TILES):
                side = fp.roll_side(tilt[i], 0)
                if side is None: continue  # level already
                self.to(*grip(tile, side))
                self.assertEqual(self.p.hover, ('roll', i, side))
                self.press(); self.release()
                self.landed(i, tilt, truth)

    def test_the_other_turn_segment_is_a_step_away(self):
        for tilt, _ in self.each():
            for i, tile in enumerate(TILES):
                side = {'cw': 'ccw', 'ccw': 'cw'}.get(fp.roll_side(tilt[i], 0))
                if side is None: continue
                self.to(*grip(tile, side))
                self.press(); self.release()
                self.assertEqual(self.p.rolls[i], fp.ROLL_STEP if side == 'ccw' else -fp.ROLL_STEP)

    def test_r_on_an_open_panel(self):
        for tilt, truth in self.each():
            self.key('menu_toggle')
            self.key('menu_reset')
            self.landed(0, tilt, truth)  # XR-1, the selected desktop
            self.key('menu_toggle')
            self.clock.now += fp.LEVEL_SETTLE_S + 0.1

    def test_a_drag_rests_at_level_on_the_way_through(self):
        def turned(tile, start, deg):
            cx, cy = fp.project(G, tile, FLAT, tile[0] + tile[2] / 2, tile[1] + tile[3] / 2)
            a, r = math.atan2(cy - start[1], start[0] - cx) + math.radians(deg), math.hypot(start[0] - cx, start[1] - cy)
            return cx + r * math.cos(a), cy - r * math.sin(a)
        for tilt, truth in self.each():
            for i, tile in enumerate(TILES):
                if abs(tilt[i]) < 200: continue  # the drag has to get off the press point first
                start = grip(tile, 'cw')
                self.to(*start)
                self.press()
                self.to(*turned(tile, start, -tilt[i] / 100))
                self.assertEqual(self.p.rolls[i], -tilt[i])  # held at level, within the detent
                self.release()
                self.landed(i, tilt, truth)

    def test_a_maximize_changes_nothing_a_level_did(self):
        for tilt, truth in self.each():
            self.to(*grip(T0, 'level'))
            self.press(); self.release()
            self.landed(0, tilt, truth)
            levelled = list(self.p.rolls)
            self.key('zoom_in')
            self.p.set_roll_geometry([None] * len(tilt))  # the renderer measures in anchor mode, from the overview, only
            self.clock.now += 0.05; self.control.poll()
            self.assertEqual(self.p.rolls, levelled)
            self.key('zoom_out')
            self.clock.now += fp.LEVEL_SETTLE_S + 0.1; self.control.poll()
            self.assertEqual(self.p.rolls, levelled)
            self.control.set_roll_geometry(tilt)
            self.free()


if __name__ == '__main__':
    unittest.main(argv=sys.argv[:1])
