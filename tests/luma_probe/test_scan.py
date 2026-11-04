import struct
import tempfile
import unittest

from tools.luma_probe.sysfs import parse_acl_users, scan
from fake_host import FakeHost


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.host = FakeHost(self.tmp.name)

    def by_name(self, result):
        return {d.name: d for d in result.devices}


class TopologyTests(Case):
    def test_finds_both_viture_devices_and_not_the_others(self):
        self.assertEqual(sorted(self.by_name(scan(self.host.glasses_on_hub().roots))), ['5-2.1', '5-2.3'])

    def test_empty_host_finds_nothing(self):
        self.assertEqual(scan(self.host.roots).devices, [])

    def test_hub_and_camera_are_reported_as_neighbours(self):
        d = self.by_name(scan(self.host.glasses_on_hub().roots))['5-2.1']
        self.assertEqual([(h.name, h.vid_pid, h.power_control) for h in d.upstream], [('5-2', '1a86:8091', 'auto')])
        self.assertEqual([n.name for n in d.neighbours], ['5-2.2', '5-2.3'])

    def test_glasses_directly_on_a_root_port_have_no_upstream(self):
        self.host.device('5-2', '35ca', '1104', 'VITURE Luma Ultra XR GLASSES')
        self.assertEqual(scan(self.host.roots).devices[0].upstream, [])

    def test_node_path_follows_the_live_bus_and_device_number(self):
        d = self.by_name(scan(self.host.glasses_on_hub().roots))['5-2.1']
        self.assertEqual(d.node.path, str(self.host.glasses_node))


class InterfaceTests(Case):
    def test_glasses_interface_has_no_hidraw_and_no_driver(self):
        i = self.by_name(scan(self.host.glasses_on_hub().roots))['5-2.1'].interfaces[0]
        self.assertEqual((i.number, i.driver, i.hidraw), (0, None, []))
        self.assertEqual(len(i.endpoints), 7)

    def test_microphone_hid_interfaces_list_their_hidraw_nodes(self):
        d = self.by_name(scan(self.host.glasses_on_hub().roots))['5-2.3']
        nodes = {i.number: [n.path.rsplit('/', 1)[1] for n in i.hidraw] for i in d.interfaces}
        self.assertEqual((nodes[3], nodes[4], nodes[2]), (['hidraw10'], ['hidraw11'], []))

    def test_each_interface_is_listed_once_despite_alt_settings(self):
        d = self.by_name(scan(self.host.glasses_on_hub().roots))['5-2.3']
        self.assertEqual([i.number for i in d.interfaces], [0, 1, 2, 3, 4])

    def test_hid_report_descriptor_gives_size_and_usage_page(self):
        i = self.by_name(scan(self.host.glasses_on_hub().roots))['5-2.3'].interfaces[3]
        self.assertEqual((i.hid_report_bytes, i.hid_usage_page), (33, 0x0c))

    def test_claimed_interface_shows_usbfs(self):
        self.host.glasses_on_hub()
        driver = self.host.roots.sys / 'bus/usb/drivers/usbfs'
        driver.mkdir(parents=True, exist_ok=True)
        (self.host.roots.sys / 'bus/usb/devices/5-2.1:1.0/driver').symlink_to(driver)
        self.assertEqual(self.by_name(scan(self.host.roots))['5-2.1'].interfaces[0].driver, 'usbfs')


class NodeTests(Case):
    def test_rw_node_is_usable(self):
        node = self.by_name(scan(self.host.glasses_on_hub().roots))['5-2.1'].node
        self.assertTrue(node.exists and node.readable and node.writable)
        self.assertEqual(node.mode, '0660')

    def test_read_only_node_is_not_writable(self):
        self.host.glasses_on_hub().glasses_node.chmod(0o440)
        node = self.by_name(scan(self.host.roots))['5-2.1'].node
        self.assertTrue(node.readable)
        self.assertFalse(node.writable)

    def test_missing_node_is_reported_not_raised(self):
        self.host.glasses_on_hub().glasses_node.unlink()
        node = self.by_name(scan(self.host.roots))['5-2.1'].node
        self.assertFalse(node.exists or node.readable or node.writable)


class AclTests(unittest.TestCase):
    def test_parses_named_users_from_the_kernel_format(self):
        entry = lambda tag, perm, ident: struct.pack('<HHI', tag, perm, ident)
        raw = struct.pack('<I', 2) + entry(1, 6, 0xffffffff) + entry(2, 6, 1000) + entry(4, 6, 0xffffffff) \
            + entry(0x10, 6, 0xffffffff) + entry(0x20, 0, 0xffffffff)
        self.assertEqual(parse_acl_users(raw), {1000: 'rw'})

    def test_damaged_value_gives_no_users(self):
        self.assertEqual(parse_acl_users(b'\x02'), {})
        self.assertEqual(parse_acl_users(None), {})


class ProcessTests(Case):
    def test_process_holding_the_node_is_named(self):
        self.host.glasses_on_hub().process(4242, 'xrDriver', holds=self.host.glasses_node)
        d = self.by_name(scan(self.host.roots))['5-2.1']
        self.assertEqual([(p.pid, p.comm) for p in d.holders], [(4242, 'xrDriver')])

    def test_tracking_driver_running_is_seen_without_a_held_node(self):
        self.host.glasses_on_hub().process(77, 'xrDriver')
        self.host.process(78, 'xr-luma-driver')
        self.host.process(79, 'bash')
        self.assertEqual(sorted(p.comm for p in scan(self.host.roots).drivers), ['xr-luma-driver', 'xrDriver'])

    def test_zombie_driver_does_not_count(self):
        self.host.glasses_on_hub().process(77, 'xrDriver', state='Z')
        self.assertEqual(scan(self.host.roots).drivers, [])

    def test_a_script_named_like_a_driver_does_not_count(self):
        self.host.glasses_on_hub().process(77, 'xr-luma-driver', exe='/usr/bin/python3.14')
        self.assertEqual(scan(self.host.roots).drivers, [])

    def test_real_binary_counts_and_is_named(self):
        self.host.glasses_on_hub().process(77, 'xr-luma-driver', exe='/opt/build/xr-luma-driver')
        self.assertEqual([p.exe for p in scan(self.host.roots).drivers], ['/opt/build/xr-luma-driver'])

    def test_unreadable_exe_still_counts(self):
        self.host.glasses_on_hub().process(77, 'xrDriver')
        self.assertEqual(len(scan(self.host.roots).drivers), 1)

    def test_a_process_name_cut_to_fifteen_bytes_still_matches_its_binary(self):
        self.host.glasses_on_hub().process(77, 'xr-luma-driver', exe='/opt/xr-luma-driver (deleted)')
        self.assertEqual(len(scan(self.host.roots).drivers), 1)

    def test_process_that_vanishes_is_skipped(self):
        self.host.glasses_on_hub()
        (self.host.roots.proc / '555').mkdir()
        self.assertEqual(scan(self.host.roots).drivers, [])
