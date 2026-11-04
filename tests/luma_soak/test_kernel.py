from pathlib import Path
import unittest

from tools.luma_soak.kernel import UsbModel, parse_line

FIXTURE = Path(__file__).parent / 'fixtures/kernel_boot_2026-11-01_03.log'


def run(model, script):
    for t, message in script:
        model.line(t, message)
    return model


def enumerate_at(t, path, vid_pid, devnum):
    vid, pid = vid_pid.split(':')
    return [(t, f'usb {path}: new high-speed USB device number {devnum} using xhci_hcd'),
            (t, f'usb {path}: New USB device found, idVendor={vid}, idProduct={pid}, bcdDevice= 1.00')]


HUB_AND_GLASSES = enumerate_at(0, '5-2', '1a86:8091', 1) + enumerate_at(0, '5-2.1', '35ca:1104', 2) + enumerate_at(0, '5-2.2', '0c45:636b', 3)


class ParseTests(unittest.TestCase):
    def test_reads_short_iso_precise(self):
        t, message = parse_line('2026-11-03T01:21:47.843889-05:00 host kernel: usb 5-2: USB disconnect, device number 74')
        self.assertAlmostEqual(t, 1793686907.843889, places=3)
        self.assertEqual(message, 'usb 5-2: USB disconnect, device number 74')

    def test_ignores_what_is_not_a_kernel_line(self):
        self.assertIsNone(parse_line('-- Boot 927e51c18add452297c0d106c92c7c83 --'))
        self.assertIsNone(parse_line('-- No entries --'))
        self.assertIsNone(parse_line('2026-11-03Tnot-a-time host kernel: usb 5-2: USB disconnect, device number 1'))


class RealLogTests(unittest.TestCase):
    """Three days of real kernel lines, 2026-11-01 to 03, sanitized."""

    @classmethod
    def setUpClass(cls):
        cls.lines = FIXTURE.read_text().splitlines()
        cls.model = UsbModel()
        for line in cls.lines:
            if parsed := parse_line(line):
                cls.model.line(*parsed)
        cls.kinds = [cls.model.classify(c) for c in cls.model.clusters]

    def test_every_hub_disconnect_line_is_exactly_one_drop(self):
        raw = sum(1 for line in self.lines if 'usb 5-2: USB disconnect' in line)
        self.assertEqual(raw, 54)
        self.assertEqual(sum(1 for kind, _ in self.kinds if kind == 'drop'), raw)

    def test_controller_mouse_and_usb_stick_on_other_ports_are_not_drops(self):
        others = [m for c in self.model.clusters if self.model.classify(c)[0] == 'other' for m in c.members]
        self.assertEqual({m.product for m in others} >= {'Pro Controller', 'G-Wolves HTS Ultra 8K Wireless Mouse-N'}, True)
        self.assertFalse([m for m in others if m.path.startswith('5-2')])

    def test_no_disconnect_is_left_unidentified(self):
        self.assertNotIn('unattributed', [kind for kind, _ in self.kinds])

    def test_each_glasses_id_is_found_on_its_own_port(self):
        ports = {(m.id, m.path) for c in self.model.clusters for m in c.members if m.id in self.model.glasses_ids}
        self.assertEqual(ports, {('35ca:1104', '5-2.1'), ('35ca:1201', '5-2.3')})

    def test_a_warning_after_the_hub_has_gone_still_counts_as_the_glasses(self):
        self.assertTrue(any('attempt power cycle' in a['text'] and a['related'] for a in self.model.anomalies))


class ModelTests(unittest.TestCase):
    def test_hub_and_children_are_one_drop(self):
        model = run(UsbModel(), HUB_AND_GLASSES + [(10.0, 'usb 5-2: USB disconnect, device number 1'), (10.0, 'usb 5-2.1: USB disconnect, device number 2'),
                                                   (10.3, 'usb 5-2.2: USB disconnect, device number 3')])
        self.assertEqual(len(model.clusters), 1)
        self.assertEqual(model.classify(model.clusters[0]), ('drop', 'hub'))
        self.assertEqual({m.id for m in model.clusters[0].members}, {'1a86:8091', '35ca:1104', '0c45:636b'})

    def test_two_drops_seconds_apart_stay_two(self):
        again = enumerate_at(11, '5-2', '1a86:8091', 4)
        model = run(UsbModel(), HUB_AND_GLASSES + [(10.0, 'usb 5-2: USB disconnect, device number 1')] + again + [(12.4, 'usb 5-2: USB disconnect, device number 4')])
        self.assertEqual(len(model.clusters), 2)

    def test_glasses_alone_is_a_drop(self):
        model = run(UsbModel(), HUB_AND_GLASSES + [(10.0, 'usb 5-2.1: USB disconnect, device number 2')])
        self.assertEqual(model.classify(model.clusters[0]), ('drop', 'glasses'))

    def test_camera_alone_is_a_sibling_not_a_drop(self):
        model = run(UsbModel(), HUB_AND_GLASSES + [(10.0, 'usb 5-2.2: USB disconnect, device number 3')])
        self.assertEqual(model.classify(model.clusters[0]), ('sibling', None))

    def test_an_identified_device_elsewhere_is_ignored(self):
        model = run(UsbModel(), enumerate_at(0, '1-4', '057e:2009', 9) + [(10.0, 'usb 1-4: USB disconnect, device number 9')])
        self.assertEqual(model.classify(model.clusters[0]), ('other', None))

    def test_an_unknown_port_is_unattributed_never_ignored(self):
        model = run(UsbModel(), [(10.0, 'usb 5-2: USB disconnect, device number 1')])
        self.assertEqual(model.classify(model.clusters[0]), ('unattributed', None))

    def test_port_hint_makes_an_unknown_port_a_drop(self):
        model = run(UsbModel(port='5-2'), [(10.0, 'usb 5-2.3: USB disconnect, device number 1')])
        self.assertEqual(model.classify(model.clusters[0]), ('drop', 'port'))

    def test_port_prefix_is_a_path_not_a_string(self):
        model = run(UsbModel(port='5-2'), [(10.0, 'usb 5-20: USB disconnect, device number 1')])
        self.assertEqual(model.classify(model.clusters[0]), ('unattributed', None))

    def test_an_unknown_device_on_a_port_the_glasses_use_is_a_drop(self):
        tree = {'5-2': {'id': '1a86:8091', 'product': None, 'devnum': 3}}
        model = run(UsbModel(tree=tree), [(5.0, 'usb 5-2: USB disconnect, device number 3'), (50.0, 'usb 5-2: USB disconnect, device number 8')])
        self.assertEqual([model.classify(c) for c in model.clusters], [('drop', 'hub'), ('drop', 'port')])

    def test_seeded_tree_identifies_a_device_enumerated_before_the_soak(self):
        tree = {'5-2': {'id': '1a86:8091', 'product': 'USB HUB', 'devnum': 3}, '5-2.1': {'id': '35ca:1104', 'product': 'x', 'devnum': 4}}
        model = run(UsbModel(tree=tree), [(10.0, 'usb 5-2.1: USB disconnect, device number 4')])
        self.assertEqual(model.classify(model.clusters[0]), ('drop', 'glasses'))

    def test_a_re_enumerated_port_takes_its_new_identity(self):
        model = run(UsbModel(), enumerate_at(0, '5-2.1', '35ca:1104', 1) + enumerate_at(1, '5-2.1', '057e:2009', 2) + [(10.0, 'usb 5-2.1: USB disconnect, device number 2')])
        self.assertEqual({m.id for m in model.clusters[0].members}, {'057e:2009'})

    def test_reset_of_the_glasses_is_a_related_anomaly(self):
        model = run(UsbModel(), HUB_AND_GLASSES + [(10.0, 'usb 5-2.1: reset high-speed USB device number 2 using xhci_hcd')])
        self.assertEqual([a['related'] for a in model.anomalies], [True])
        self.assertEqual(model.clusters, [])

    def test_reset_elsewhere_is_not_related(self):
        model = run(UsbModel(), HUB_AND_GLASSES + [(10.0, 'usb 1-4: reset full-speed USB device number 9 using xhci_hcd')])
        self.assertEqual([a['related'] for a in model.anomalies], [False])

    def test_port_power_cycle_maps_to_the_child_path(self):
        model = run(UsbModel(), HUB_AND_GLASSES + [(10.0, 'usb 5-2-port3: attempt power cycle')])
        self.assertEqual(model.anomalies[0]['path'], '5-2.3')

    def test_host_controller_trouble_counts_for_everyone(self):
        model = run(UsbModel(), [(10.0, 'xhci_hcd 0000:12:00.4: xHCI host not responding to stop endpoint command')])
        self.assertEqual([a['related'] for a in model.anomalies], [True])

    def test_arrivals_are_the_glasses_only(self):
        model = run(UsbModel(), HUB_AND_GLASSES)
        self.assertEqual(model.arrivals, [0])
