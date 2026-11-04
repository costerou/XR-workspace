"""The live plan is a script a person follows with real hardware, so its shape is checked."""
import re
import unittest
from pathlib import Path

from tools.luma_probe.udev import RULE_TEXT

PLAN = Path(__file__).parents[2] / 'spike/driver/docs/live-test-plan.md'
STEP = re.compile(r'^\*\*([A-Z])(\d+)\.', re.M)
BLOCK = re.compile(r'```sh\n(.*?)```', re.S)
STAGES = 'PDIFSCRLEXB'
NEEDS_NO_PASS_LINE = {'L3', 'X1'}  # L3 says plainly it is not measured; X1 is an instruction, X2 judges it


class PlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = PLAN.read_text()
        cls.blocks = BLOCK.findall(cls.text)

    def test_every_stage_exists(self):
        self.assertEqual({s for s, _ in STEP.findall(self.text)}, set(STAGES))

    def test_steps_are_numbered_without_gaps(self):
        numbers = {}
        for stage, number in STEP.findall(self.text):
            numbers.setdefault(stage, []).append(int(number))
        for stage, found in numbers.items():
            self.assertEqual(found, list(range(1, len(found) + 1)), stage)

    def test_every_step_says_what_passing_means(self):
        parts = STEP.split(self.text)
        for i in range(1, len(parts), 3):
            name = parts[i] + parts[i + 1]
            if name not in NEEDS_NO_PASS_LINE:
                self.assertIn('Pass', parts[i + 2], name)

    def test_required_coverage_is_present(self):
        for phrase in ('USB disconnect', '60 s', '30 min', 'XRLinuxDriver', 'unplug', 'CPU', 'latency'):
            self.assertIn(phrase.lower(), self.text.lower())

    def test_commands_never_control_xr_or_reset_the_device(self):
        banned = re.compile(r'systemctl\s+(--user\s+)?(start|stop|restart|kill)|hyprctl|usbreset|/authorized|/unbind|'
                            r'pkill|killall|reboot|shutdown|udevadm trigger')
        for block in self.blocks:
            self.assertIsNone(banned.search(block), block)

    def test_only_the_udev_step_uses_sudo(self):
        users = [b for b in self.blocks if 'sudo' in b]
        self.assertEqual(len(users), 1)
        self.assertIn('71-xr-luma.rules', users[0])

    def test_the_rule_in_the_plan_is_the_one_the_probe_prints(self):
        self.assertIn(RULE_TEXT.strip(), self.text)

    def test_driver_only_ever_runs_inside_the_network_namespace(self):
        for block in self.blocks:
            for line in block.splitlines():
                if '"$BIN"' in line and '--help' not in line:
                    self.assertIn('unshare --user --map-current-user --net', line)

    def test_a_kill_only_ever_names_this_plans_pid(self):
        for block in self.blocks:
            for line in re.findall(r'kill [^\n;]*', block):
                self.assertIn('"$PID"', line)

    def test_every_part_of_codys_bar_names_a_step(self):
        table = self.text[self.text.index('## Cody\'s bar'):self.text.index('## Two findings')]
        for want in ('Zero USB drops', 'latency', 'sample rate', 'drift', 'calibration', 'reconnect', 'features'):
            row = next((r for r in table.splitlines() if r.startswith('|') and want.lower() in r.lower()), None)
            self.assertIsNotNone(row, want)
            self.assertRegex(row, r'\b[A-Z]\d\b', want)

    def test_steps_named_in_the_bar_exist(self):
        table = self.text[self.text.index('## Cody\'s bar'):self.text.index('## Two findings')]
        steps = {a + b for a, b in STEP.findall(self.text)}
        for name in re.findall(r'\b([A-Z]\d)\b', table):
            self.assertIn(name, steps)

    def test_command_sending_features_wait_for_the_three_hour_gate(self):
        self.assertLess(self.text.index('## Stage X'), self.text.index('## Stage E'))
        stage = self.text[self.text.index('## Stage E'):self.text.index('## Stage B')]
        self.assertIn('3 h', stage)
        for flag in ('--feature-firmware-query', '--feature-wear-query'):
            self.assertIn(flag, stage)

    def test_compare_margins_step_matches_the_code(self):
        source = (PLAN.parents[3] / 'tools/luma_probe/feed.py').read_text()
        self.assertIn('live-test-plan.md, step F6', source)
        self.assertIn('**F6. Compare.**', self.text)
