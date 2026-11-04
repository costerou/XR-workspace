import tempfile
import unittest

from tools.luma_probe.assess import assess, render
from tools.luma_probe.sysfs import scan
from tools.luma_probe.udev import RULE_TEXT
from fake_host import FakeHost, SHIPPED_RULES


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.host = FakeHost(self.tmp.name)

    def verdict(self):
        return assess(scan(self.host.roots), self.host.roots)

    def codes(self, level=None):
        return [f.code for f in self.verdict().findings if level is None or f.level == level]


class VerdictTests(Case):
    def test_no_glasses(self):
        result = self.verdict()
        self.assertEqual((result.status, result.exit_code), ('absent', 2))

    def test_microphone_alone_does_not_count_as_glasses(self):
        self.host.device('5-2.3', '35ca', '1102', 'VITURE Microphone', speed='12')
        self.assertEqual(self.verdict().status, 'absent')

    def test_live_layout_is_ready(self):
        self.host.glasses_on_hub()
        result = self.verdict()
        self.assertEqual((result.status, result.exit_code), ('ready', 0), result.findings)
        self.assertIsNone(result.udev_commands)

    def test_says_the_glasses_are_a_usbfs_device(self):
        self.host.glasses_on_hub()
        self.assertIn('usbfs-route', self.codes('info'))

    def test_one_millisecond_interrupt_endpoint_is_named_as_a_guess(self):
        self.host.glasses_on_hub()
        text = next(f.text for f in self.verdict().findings if f.code == 'imu-endpoint-hint')
        self.assertIn('0x83', text)
        self.assertIn('unproven', text)


class AccessTests(Case):
    def test_no_rule_and_no_access_offers_the_rule(self):
        self.host.glasses_on_hub()
        (self.host.roots.udev[2] / '70-viture-xr.rules').unlink()
        self.host.glasses_node.chmod(0o600 & 0o000)
        result = self.verdict()
        self.assertEqual((result.status, result.exit_code), ('action', 1))
        self.assertIn(RULE_TEXT.strip(), result.udev_commands)

    def test_read_only_usbfs_is_not_enough(self):
        self.host.glasses_on_hub()
        (self.host.roots.udev[2] / '70-viture-xr.rules').unlink()
        self.host.glasses_node.chmod(0o440)
        self.assertIn('usbfs-no-access', self.codes('action'))

    def test_rule_present_but_no_access_says_replug_and_offers_no_second_rule(self):
        self.host.glasses_on_hub().glasses_node.chmod(0)
        result = self.verdict()
        self.assertIn('rule-not-applied', self.codes('action'))
        self.assertIsNone(result.udev_commands)

    def test_unreadable_microphone_hidraw_alone_is_not_blocking(self):
        self.host.glasses_on_hub()
        (self.host.roots.dev / 'hidraw10').chmod(0)
        result = self.verdict()
        self.assertEqual(result.status, 'ready')
        self.assertIn('hidraw-no-access', self.codes('info'))

    def test_missing_node_is_action(self):
        self.host.glasses_on_hub().glasses_node.unlink()
        self.assertIn('usbfs-no-access', self.codes('action'))


class AddOnlyRuleTests(Case):
    """The real failure of 2026-11-03: rebooted with the glasses plugged in, no ACL, 'Cannot open device'."""

    def shipped(self):
        self.host.glasses_on_hub()
        self.host.rules(SHIPPED_RULES)

    def test_after_a_boot_it_is_named_as_the_cause_and_the_rule_is_offered(self):
        self.shipped()
        self.host.glasses_node.chmod(0)
        result = self.verdict()
        self.assertEqual(result.status, 'action')
        self.assertIn('rule-add-only', self.codes('action'))
        self.assertIn(RULE_TEXT.strip(), result.udev_commands)

    def test_after_a_replug_it_works_but_warns_about_the_next_boot(self):
        self.shipped()
        result = self.verdict()
        self.assertEqual(result.status, 'ready')
        self.assertIn('rule-add-only', self.codes('warning'))
        self.assertIn(RULE_TEXT.strip(), result.udev_commands)


class BusyTests(Case):
    def test_running_xrdriver_blocks(self):
        self.host.glasses_on_hub().process(77, 'xrDriver')
        result = self.verdict()
        self.assertEqual((result.status, result.exit_code), ('busy', 1))
        self.assertIn('77', next(f.text for f in result.findings if f.code == 'driver-running'))

    def test_claimed_interface_blocks_even_with_no_known_driver(self):
        self.host.glasses_on_hub()
        driver = self.host.roots.sys / 'bus/usb/drivers/usbfs'
        driver.mkdir(parents=True, exist_ok=True)
        (self.host.roots.sys / 'bus/usb/devices/5-2.1:1.0/driver').symlink_to(driver)
        self.assertEqual(self.verdict().status, 'busy')
        self.assertIn('interface-claimed', self.codes('blocker'))

    def test_unknown_holder_is_named(self):
        self.host.glasses_on_hub().process(90, 'python3', holds=self.host.glasses_node)
        self.assertIn('python3', next(f.text for f in self.verdict().findings if f.code == 'node-held'))

    def test_busy_outranks_a_missing_rule(self):
        self.host.glasses_on_hub().process(77, 'xrDriver')
        (self.host.roots.udev[2] / '70-viture-xr.rules').unlink()
        self.host.glasses_node.chmod(0)
        result = self.verdict()
        self.assertEqual(result.status, 'busy')
        self.assertIn(RULE_TEXT.strip(), result.udev_commands)


class LinkTests(Case):
    def test_low_speed_link_warns(self):
        self.host.device('5-2', '35ca', '1104', 'VITURE Luma Ultra XR GLASSES', speed='12')
        self.assertIn('slow-link', self.codes('warning'))

    def test_autosuspend_on_the_hub_warns_but_does_not_block(self):
        self.host.glasses_on_hub()
        result = self.verdict()
        self.assertIn('autosuspend', self.codes('warning'))
        self.assertIn('5-2', next(f.text for f in result.findings if f.code == 'autosuspend'))
        self.assertEqual(result.status, 'ready')


class RenderTests(Case):
    def test_text_report_names_the_node_and_endpoints(self):
        self.host.glasses_on_hub()
        text = render(scan(self.host.roots), self.verdict())
        self.assertIn(str(self.host.glasses_node), text)
        self.assertIn('0x83 in interrupt 512 B every 1000 us', text)
        self.assertIn('hidraw10', text)

    def test_text_report_prints_the_rule_when_needed(self):
        self.host.glasses_on_hub()
        (self.host.roots.udev[2] / '70-viture-xr.rules').unlink()
        self.host.glasses_node.chmod(0)
        self.assertIn(RULE_TEXT.strip(), render(scan(self.host.roots), self.verdict()))

    def test_absent_report_is_short_and_useful(self):
        self.assertIn('No VITURE Luma Ultra', render(scan(self.host.roots), self.verdict()))
