import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.luma_probe.sysfs import Roots
from tools.luma_probe.udev import RULE_FILE, RULE_TEXT, add_only, covers, install_commands, rule_files, scan_rules
from fake_host import FakeHost, FIXED_RULES, SHIPPED_RULES


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.host = FakeHost(self.tmp.name)

    def cover(self, text, name='70-x.rules', where=2):
        self.host.rules(text, name, where)
        return covers(scan_rules(self.host.roots))


class CoverageTests(Case):
    def test_fixed_rules_cover_usb_and_hidraw(self):
        self.assertEqual(self.cover(FIXED_RULES), {'usb': True, 'hidraw': True})

    def test_add_only_rule_does_not_last_past_a_reboot(self):
        self.assertEqual(self.cover(SHIPPED_RULES), {'usb': False, 'hidraw': True})

    def test_a_hiddev_rule_says_nothing_about_the_usb_device_node(self):
        text = 'SUBSYSTEM=="usb", KERNEL=="hiddev[0-9]*", ATTRS{idVendor}=="35ca", MODE="0660", TAG+="uaccess"\n'
        self.assertFalse(self.cover(text)['usb'])

    def test_add_only_rule_is_recognised(self):
        self.host.rules(SHIPPED_RULES)
        self.assertTrue(add_only(scan_rules(self.host.roots)))

    def test_fixed_rule_is_not_add_only(self):
        self.host.rules(FIXED_RULES)
        self.assertFalse(add_only(scan_rules(self.host.roots)))

    def test_new_rule_alongside_the_add_only_one_covers_usb(self):
        self.host.rules(SHIPPED_RULES)
        self.host.rules(RULE_TEXT, RULE_FILE, 0)
        self.assertTrue(covers(scan_rules(self.host.roots))['usb'])

    def test_commented_rule_does_not_count(self):
        self.assertEqual(self.cover('#' + FIXED_RULES.replace('\n', '\n#')), {'usb': False, 'hidraw': False})

    def test_uppercase_vendor_never_matches_sysfs(self):
        self.assertFalse(self.cover(FIXED_RULES.replace('35ca', '35CA'))['usb'])

    def test_rule_that_grants_nothing_does_not_count(self):
        self.assertFalse(self.cover('SUBSYSTEM=="usb", ATTRS{idVendor}=="35ca", ENV{X}="1"\n')['usb'])

    def test_rule_for_another_product_does_not_count(self):
        text = 'SUBSYSTEM=="usb", ATTRS{idVendor}=="35ca", ATTRS{idProduct}=="1011", TAG+="uaccess"\n'
        self.assertFalse(self.cover(text)['usb'])

    def test_rule_for_the_glasses_product_counts(self):
        text = 'SUBSYSTEM=="usb", ATTRS{idVendor}=="35ca", ATTRS{idProduct}=="1104", MODE="0666"\n'
        self.assertTrue(self.cover(text)['usb'])

    def test_rule_split_over_a_continuation_line_counts(self):
        self.assertTrue(self.cover('SUBSYSTEM=="usb", ATTRS{idVendor}=="35ca", \\\n  TAG+="uaccess"\n')['usb'])


class PrecedenceTests(Case):
    def test_etc_file_masks_the_same_name_in_lib(self):
        self.host.rules(FIXED_RULES)
        self.host.rules('# emptied by hand\n', '70-viture-xr.rules', 0)
        self.assertEqual(covers(scan_rules(self.host.roots)), {'usb': False, 'hidraw': False})

    def test_only_rules_files_are_read(self):
        self.host.rules(FIXED_RULES, '70-viture-xr.rules.bak')
        self.assertEqual(rule_files(self.host.roots), [])

    def test_missing_directories_are_not_an_error(self):
        roots = Roots(sys=Path('/nonexistent'), dev=Path('/nonexistent'), proc=Path('/nonexistent'),
                      udev=(Path('/nonexistent/a'),))
        self.assertEqual(rule_files(roots), [])


class RuleTextTests(unittest.TestCase):
    def test_rule_covers_both_routes(self):
        with tempfile.TemporaryDirectory() as d:
            host = FakeHost(d)
            host.rules(RULE_TEXT, RULE_FILE)
            self.assertEqual(covers(scan_rules(host.roots)), {'usb': True, 'hidraw': True})

    def test_rule_file_sorts_before_the_uaccess_step(self):
        self.assertLess(RULE_FILE, '73-seat-late.rules')

    @unittest.skipUnless(shutil.which('udevadm'), 'needs udevadm')
    def test_rule_passes_udevadm_verify(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / RULE_FILE
            path.write_text(RULE_TEXT)
            result = subprocess.run(['udevadm', 'verify', str(path)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_commands_install_then_reload_and_never_trigger(self):
        commands = install_commands()
        self.assertIn(f'/etc/udev/rules.d/{RULE_FILE}', commands)
        self.assertIn(RULE_TEXT.strip(), commands)
        self.assertIn('sudo udevadm control --reload-rules', commands)
        self.assertNotIn('udevadm trigger', commands)
