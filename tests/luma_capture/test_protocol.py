import random
import struct
import unittest

from tools.luma_capture import protocol
from tools.luma_capture.capfile import (F_GLASSES, F_POLICY_DROPPED, F_SETUP, Record, XFER_BULK, XFER_CTRL, XFER_INT,
                                        note_record)
from tools.luma_capture.protocol import (Framing, Labeler, describe_control, drop_signal, infer_framing,
                                         kernel_disconnect, port_of, status_name)

import synth


def setup(request_type, request, value=0, index=0, length=0):
    return struct.pack('<BBHHH', request_type, request, value, index, length)


class Control(unittest.TestCase):
    def test_hub_port_requests(self):
        self.assertEqual(describe_control(setup(0xa3, 0, 0, 2, 4)), 'hub GET_PORT_STATUS port 2')
        self.assertEqual(describe_control(setup(0x23, 3, 4, 2)), 'hub SET RESET port 2')
        self.assertEqual(describe_control(setup(0x23, 1, 16, 2)), 'hub CLEAR C_CONNECTION port 2')
        self.assertEqual(describe_control(setup(0x23, 3, 2, 1)), 'hub SET SUSPEND port 1')

    def test_standard_requests(self):
        self.assertEqual(describe_control(setup(0x80, 6, 0x0100, 0, 18)), 'GET_DESCRIPTOR device')
        self.assertEqual(describe_control(setup(0x00, 9, 1)), 'SET_CONFIGURATION value=1')
        self.assertEqual(describe_control(setup(0x02, 1, 0, 0x82)), 'CLEAR_HALT ep 0x82')

    def test_vendor_requests_show_their_numbers(self):
        self.assertEqual(describe_control(setup(0xc0, 0x51, 2, 3, 8)),
                         'vendor request 0x51 value=0x0002 index=0x0003 len=8')


class PortStatus(unittest.TestCase):
    def label(self, status, change):
        labeler = Labeler()
        ask = Record(1, event='S', xfer=XFER_CTRL, ep=0x80, urb_id=9, dev=1, flags=F_SETUP,
                     setup=setup(0xa3, 0, 0, 2, 4))
        labeler.label(ask)
        answer = Record(2, event='C', xfer=XFER_CTRL, ep=0x80, urb_id=9, dev=1, data=struct.pack('<HH', status, change))
        return labeler.label(answer)

    def test_a_lost_connection_reads_as_one(self):
        label = self.label(0x0100, 0x0001)
        self.assertEqual(label.kind, 'hub')
        self.assertIn('-> power; changed: connection', label.text)

    def test_a_healthy_port(self):
        self.assertIn('-> connected,enabled,power,high-speed; changed: none', self.label(0x0503, 0).text)

    def test_over_current_is_named(self):
        self.assertIn('over-current', self.label(0x0108, 0x0008).text)

    def test_a_completion_without_its_submit_does_not_crash(self):
        record = Record(2, event='C', xfer=XFER_CTRL, ep=0x80, urb_id=9, dev=1)
        self.assertEqual(Labeler().label(record).kind, 'control')


class Status(unittest.TestCase):
    def test_names(self):
        self.assertEqual(status_name(-71), 'EPROTO')
        self.assertEqual(status_name(-108), 'ESHUTDOWN')
        self.assertEqual(status_name(-5), 'errno -5')


class Framings(unittest.TestCase):
    def test_reads_the_field(self):
        framing = Framing(4, 'big', b'\xab')
        self.assertEqual(framing.msgid(b'\xab\0\0\0\x00\x07'), 7)
        self.assertEqual(Framing(4).msgid(b'\xab\0\0\0\x07\x00'), 7)

    def test_wrong_magic_or_too_short_is_no_msgid(self):
        self.assertIsNone(Framing(4, magic=b'\xab').msgid(b'\xcd\0\0\0\x07\0'))
        self.assertIsNone(Framing(4).msgid(b'\0\0\0\0\x07'))

    def test_parse(self):
        framing = Framing.parse('offset=14,endian=big,magic=fffe')
        self.assertEqual((framing.offset, framing.endian, framing.magic), (14, 'big', b'\xff\xfe'))
        self.assertEqual(Framing.parse('offset=0x0e').offset, 14)
        self.assertEqual(Framing.parse(framing.spec()).offset, 14)

    def test_parse_refuses_nonsense(self):
        for text in ('endian=big', 'offset=1,colour=red', 'offset=1,endian=middle'):
            with self.assertRaises(ValueError):
                Framing.parse(text)


def samples(build):
    found = []
    for record in sorted(build.records, key=lambda r: r.t_ns):
        if record.is_note or record.ep == 0x81 or not record.data or record.dev != synth.GLASSES_DEV:
            continue
        if (record.is_in and record.event == 'C') or (not record.is_in and record.event == 'S'):
            found.append((record.t_ns, record.is_in, record.data))
    return found


class Inference(unittest.TestCase):
    def test_finds_the_field_wherever_it_is(self):
        for offset in (2, 6, 14, 20):
            for endian in ('little', 'big'):
                result = infer_framing(samples(synth.session(6, offset=offset, endian=endian)))
                self.assertEqual((result.framing.offset, result.framing.endian), (offset, endian))
                self.assertEqual(result.framing.magic, b'\xab\xcd')
                self.assertEqual(result.framing.source, 'inferred')

    def test_without_replies_echoing_it_stays_a_candidate(self):
        result = infer_framing(samples(synth.session(6, echo=False)))
        self.assertIsNone(result.framing)
        self.assertEqual(result.verdict, 'undetermined')
        self.assertTrue(any(c.framing.offset == 14 for c in result.candidates))

    def test_a_field_that_never_changes_is_not_a_msgid(self):
        rng = random.Random(3)
        traffic = []
        for index in range(40):
            command = bytearray(rng.randbytes(32))
            command[4:6] = (7).to_bytes(2, 'little')     # a constant 7, say a protocol version
            reply = bytearray(rng.randbytes(32))
            reply[4:6] = (7).to_bytes(2, 'little')
            traffic += [(index * 10**7, False, bytes(command)), (index * 10**7 + 1000, True, bytes(reply))]
        self.assertIsNone(infer_framing(traffic).framing)

    def test_a_clock_that_happens_to_pass_through_seven_is_rejected(self):
        rng = random.Random(4)
        traffic = []
        for index in range(200):
            command = bytearray(rng.randbytes(32))
            command[8:10] = index.to_bytes(2, 'little')
            reply = bytearray(rng.randbytes(32))
            reply[8:10] = index.to_bytes(2, 'little')
            traffic += [(index * 10**7, False, bytes(command)), (index * 10**7 + 1000, True, bytes(reply))]
        self.assertIsNone(infer_framing(traffic).framing)

    def test_two_fields_fitting_equally_well_settle_nothing(self):
        rng = random.Random(5)
        traffic = []
        for index in range(60):
            msgid = (7, 9, 11)[index % 3]
            command = bytearray(rng.randbytes(32))
            command[4:6] = command[10:12] = msgid.to_bytes(2, 'little')
            reply = bytearray(rng.randbytes(32))
            reply[4:6] = reply[10:12] = msgid.to_bytes(2, 'little')
            traffic += [(index * 10**7, False, bytes(command)), (index * 10**7 + 1000, True, bytes(reply))]
        result = infer_framing(traffic)
        self.assertIsNone(result.framing)
        self.assertGreaterEqual(len(result.candidates), 2)

    def test_no_commands_at_all(self):
        result = infer_framing([])
        self.assertIsNone(result.framing)
        self.assertEqual(result.verdict, 'not seen')

    def test_a_late_reply_is_not_evidence(self):
        build = synth.session(6)
        traffic = [(t if is_in is False else t + 5 * 10**8, is_in, data) for t, is_in, data in samples(build)]
        self.assertIsNone(infer_framing(sorted(traffic, key=lambda s: s[0])).framing)


class Labels(unittest.TestCase):
    framing = Framing(14, 'little', b'\xab\xcd')

    def test_a_command_with_known_framing(self):
        data = synth.Builder().payload(7)
        label = Labeler(self.framing).label(Record(1, event='S', ep=4, flags=F_GLASSES, data=data))
        self.assertEqual((label.kind, label.msgid), ('command', 7))
        self.assertEqual(label.text, 'command MsgID 0x0007')

    def test_a_command_without_framing_is_described_by_shape(self):
        record = Record(1, event='S', ep=4, flags=F_GLASSES, urb_len=64, data=b'\xab\xcd\0\0')
        label = Labeler().label(record)
        self.assertIsNone(label.msgid)
        self.assertEqual(label.text, 'OUT ep04 len=64 abcd')

    def test_stream_and_reply_and_posted_read(self):
        labeler = Labeler(self.framing, {0x81})
        data = synth.Builder().payload(7)
        kinds = [labeler.label(Record(1, event='C', ep=0x81, flags=F_GLASSES, urb_len=60)).kind,
                 labeler.label(Record(1, event='C', ep=0x82, flags=F_GLASSES, data=data)).kind,
                 labeler.label(Record(1, event='S', ep=0x82, flags=F_GLASSES)).kind]
        self.assertEqual(kinds, ['stream', 'reply', 'read'])

    def test_other_devices_say_their_payload_was_not_kept(self):
        record = Record(1, event='C', ep=0x82, dev=9, xfer=XFER_BULK, flags=F_POLICY_DROPPED)
        self.assertIn('payload not kept', Labeler().label(record).text)

    def test_a_hub_status_change_interrupt(self):
        label = Labeler().label(Record(1, event='C', ep=0x81, dev=1, xfer=XFER_INT, data=b'\x04'))
        self.assertEqual(label.text, 'dev1 status change bitmap 04')


class Disconnects(unittest.TestCase):
    def test_the_port_is_the_assembly(self):
        self.assertEqual(port_of('5-2.1'), '5-2')
        self.assertEqual(port_of('5-2'), '5-2')
        self.assertIsNone(port_of(None))

    def test_only_this_assembly_counts(self):
        self.assertTrue(kernel_disconnect('usb 5-2: USB disconnect, device number 3', '5-2'))
        self.assertTrue(kernel_disconnect('usb 5-2.1: USB disconnect, device number 4', '5-2'))
        self.assertFalse(kernel_disconnect('usb 5-1: USB disconnect, device number 2', '5-2'))
        self.assertFalse(kernel_disconnect('usb 1-4: USB disconnect, device number 9', '5-2'))
        self.assertFalse(kernel_disconnect('usb 5-22: USB disconnect, device number 9', '5-2'))
        self.assertFalse(kernel_disconnect('usb 5-2: new high-speed USB device', '5-2'))

    def test_with_no_port_known_any_disconnect_counts(self):
        self.assertTrue(kernel_disconnect('usb 5-1: USB disconnect, device number 2'))

    def test_signals(self):
        gone = Record(1, event='C', ep=0x81, flags=F_GLASSES, status=-108)
        self.assertIn('ESHUTDOWN', drop_signal(gone))
        self.assertIn('ENODEV', drop_signal(Record(1, event='C', ep=4, flags=F_GLASSES, status=-19)))
        self.assertIn('sysfs', drop_signal(note_record(1, type='glasses_gone')))
        self.assertIn('kernel', drop_signal(note_record(1, type='kernel', text='usb 5-2: USB disconnect, x'), '5-2'))

    def test_only_the_kernel_and_sysfs_confirm(self):
        self.assertTrue(protocol.confirms_drop('kernel: usb 5-2: USB disconnect'))
        self.assertTrue(protocol.confirms_drop('gone from sysfs'))
        self.assertFalse(protocol.confirms_drop('transfer failed ESHUTDOWN on ep81'))

    def test_non_signals(self):
        for record in (Record(1, event='C', ep=0x81, status=-108),             # some other device
                       Record(1, event='C', ep=0x81, flags=F_GLASSES, status=-2),   # a timeout or a clean close
                       Record(1, event='C', ep=0x81, flags=F_GLASSES, status=-71),
                       Record(1, event='S', ep=0x81, flags=F_GLASSES, status=-108),
                       note_record(1, type='kernel', text='usb 5-1: USB disconnect, x')):
            self.assertIsNone(drop_signal(record, '5-2'))


if __name__ == '__main__':
    unittest.main()
