import unittest

from tools.luma_probe.sysfs import parse_descriptors, poll_period_us
from fake_host import GLASSES_DESCRIPTORS, MIC_DESCRIPTORS


class RealDescriptorTests(unittest.TestCase):
    def test_glasses_have_one_vendor_interface_with_seven_endpoints(self):
        interfaces = parse_descriptors(GLASSES_DESCRIPTORS)
        self.assertEqual([(i.number, i.alt, i.cls) for i in interfaces], [(0, 0, 0)])
        self.assertEqual([e.address for e in interfaces[0].endpoints], [0x81, 0x82, 0x83, 0x04, 0x85, 0x06, 0x87])

    def test_endpoint_direction_type_and_size(self):
        ep = {e.address: e for e in parse_descriptors(GLASSES_DESCRIPTORS)[0].endpoints}
        self.assertEqual((ep[0x83].direction, ep[0x83].kind, ep[0x83].max_packet), ('in', 'interrupt', 512))
        self.assertEqual((ep[0x04].direction, ep[0x04].kind), ('out', 'bulk'))

    def test_microphone_alt_settings_are_kept_apart(self):
        interfaces = parse_descriptors(MIC_DESCRIPTORS)
        self.assertEqual([(i.number, i.alt, i.cls) for i in interfaces],
                         [(0, 0, 1), (1, 0, 1), (1, 1, 1), (2, 0, 3), (3, 0, 3), (4, 0, 3)])
        self.assertEqual([hex(e.address) for e in interfaces[4].endpoints], ['0x84', '0x3'])

    def test_masked_max_packet_ignores_high_bandwidth_bits(self):
        raw = bytes([9, 4, 0, 0, 1, 255, 0, 0, 0, 7, 5, 0x81, 3, 0x00, 0x0c, 1])  # 1024 with 1 extra transaction
        self.assertEqual(parse_descriptors(raw)[0].endpoints[0].max_packet, 1024)


class DamagedDescriptorTests(unittest.TestCase):
    def test_zero_length_descriptor_stops_instead_of_looping(self):
        self.assertEqual(parse_descriptors(b'\x00\x00\x00'), [])

    def test_truncated_endpoint_is_dropped(self):
        raw = bytes([9, 4, 0, 0, 1, 255, 0, 0, 0, 7, 5, 0x81])
        self.assertEqual(parse_descriptors(raw)[0].endpoints, [])

    def test_endpoint_before_any_interface_is_ignored(self):
        self.assertEqual(parse_descriptors(bytes([7, 5, 0x81, 3, 0, 2, 4])), [])


class PollPeriodTests(unittest.TestCase):
    def test_high_speed_interval_is_a_power_of_two_of_microframes(self):
        self.assertEqual(poll_period_us(4, 480), 1000)
        self.assertEqual(poll_period_us(3, 480), 500)

    def test_full_speed_interval_is_milliseconds(self):
        self.assertEqual(poll_period_us(1, 12), 1000)

    def test_zero_interval_has_no_period(self):
        self.assertIsNone(poll_period_us(0, 480))
