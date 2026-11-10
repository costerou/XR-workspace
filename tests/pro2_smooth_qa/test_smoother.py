"""MotionSmoother cases (smoother_cases.cpp), each its own process: a failure is an exit code, never an abort."""
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCES = ['tests/pro2_smooth_qa/smoother_cases.cpp', 'spike/src/motion_smooth.cpp', 'spike/src/predict.cpp', 'spike/src/pose.cpp', 'spike/driver/src/rt.cpp']


class Smoother(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.binary = Path(cls.tmp.name) / 'cases'
        subprocess.run(['c++', '-std=c++20', '-O1', '-pthread', '-Ispike/include', '-Ispike/driver/include', *SOURCES, '-o', str(cls.binary)], cwd=ROOT, check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls): cls.tmp.cleanup()

    def case(self, name):
        done = subprocess.run([str(self.binary), name], capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    # Held.
    def test_quaternion_sign_does_not_matter(self): self.case('sign_flip')
    def test_reset_is_a_fresh_smoother(self): self.case('reset_is_fresh')
    def test_ctrl_alt_p_mid_turn(self): self.case('toggle')
    def test_clock_going_back_huge_gaps_and_bad_input(self): self.case('clocks')
    def test_publisher_clock_restart_mid_turn(self): self.case('epoch')
    def test_small_recentre_eases_without_going_past(self):
        for name in ('recentre_2', 'recentre_5', 'recentre_6'):
            with self.subTest(name): self.case(name)
    def test_large_recentre_is_taken_at_once(self): self.case('recentre_20')
    def test_quick_turns_up_to_350_deg_s(self):
        for name in ('whip_150', 'whip_250', 'whip_350'):
            with self.subTest(name): self.case(name)
    def test_a_stall_at_moderate_speed_stays_under_the_cap(self): self.case('stall_50')

    # Defects the review found, now fixed.
    def test_recentre_just_under_the_snap_limit_swings_past_the_new_reference(self):
        for name in ('recentre_7',):
            with self.subTest(name): self.case(name)
    def test_fast_turn_is_read_as_a_jump(self):
        for name in ('whip_500', 'whip_700'):
            with self.subTest(name): self.case(name)
    def test_a_stall_goes_past_max_predict_deg(self):
        for name in ('stall_100', 'stall_200'):
            with self.subTest(name): self.case(name)
