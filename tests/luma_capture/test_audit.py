import struct
import tempfile
from pathlib import Path
import unittest

from tools.luma_capture.audit import audit, compare, passed, render_audit, render_compare
from tools.luma_capture.capfile import XFER_CTRL
from tools.luma_capture.report import analyze

import synth
from synth import MS, S, T0


def control(request_type, request, value=0, index=0, length=0):
    return struct.pack('<BBHHH', request_type, request, value, index, length)


class Base(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.dir = Path(folder.name)

    def good(self):
        """A driver as the rules ask: one start command, two reads always queued, then silence."""
        build = synth.Builder(v1=True)
        build.devices(T0)
        build.command(T0, 0x100b)
        build.stream(T0 + 50 * MS, T0 + 15 * S, queued=True)
        return build

    def rules(self, build, name='a.lcap', baseline=None):
        rules = audit(analyze([build.write(self.dir / name)]), baseline)
        return {item['rule']: item for item in rules}

    def verdict(self, build, rule):
        return next(item for key, item in self.rules(build).items() if rule in key)


class Clean(Base):
    def test_a_well_behaved_driver_passes_everything_it_can_be_judged_on(self):
        rules = self.rules(self.good())
        self.assertEqual([item['rule'] for item in rules.values() if item['ok'] is False], [])
        self.assertTrue(passed(list(rules.values())))
        self.assertIsNone(self.verdict(self.good(), 'after a disconnect')['ok'])     # unknown is not a pass


class Forbidden(Base):
    def test_clear_halt(self):
        build = self.good()
        build.usb(T0 + S, 'S', 0x00, xfer=XFER_CTRL, setup=control(0x02, 1, 0, 0x83), status=-115)
        self.assertIn('clear halt x1', self.verdict(build, 'No reset')['detail'])

    def test_set_interface(self):
        build = self.good()
        build.usb(T0 + S, 'S', 0x00, xfer=XFER_CTRL, setup=control(0x01, 11), status=-115)
        self.assertFalse(self.verdict(build, 'No reset')['ok'])

    def test_the_kernels_enumeration_is_excused(self):
        build = self.good()
        build.usb(T0 + S, 'S', 0x80, xfer=XFER_CTRL, setup=control(0x80, 6, 0x0200), status=-115)
        build.usb(T0 + S + MS, 'S', 0x00, xfer=XFER_CTRL, setup=control(0x00, 9, 1), status=-115)
        self.assertTrue(self.verdict(build, 'No reset')['ok'])

    def test_a_second_set_configuration_is_not(self):
        build = self.good()
        build.usb(T0 + S, 'S', 0x80, xfer=XFER_CTRL, setup=control(0x80, 6, 0x0200), status=-115)
        build.usb(T0 + S + MS, 'S', 0x00, xfer=XFER_CTRL, setup=control(0x00, 9, 1), status=-115)
        build.usb(T0 + 2 * S, 'S', 0x00, xfer=XFER_CTRL, setup=control(0x00, 9, 1), status=-115)
        self.assertIn('SET_CONFIGURATION x1', self.verdict(build, 'No reset')['detail'])

    def test_a_reset_out_of_nowhere_counts(self):
        build = self.good()
        build.usb(T0 + 5 * S, 'S', 0x00, xfer=XFER_CTRL, dev=synth.ROOT_HUB, setup=control(0x23, 3, 4, 2), status=-115)
        self.assertIn('port reset x1', self.verdict(build, 'No reset')['detail'])

    def test_a_reset_after_a_disconnect_is_the_kernel_enumerating(self):
        build = self.good()
        build.note(T0 + 5 * S, type='kernel', text='usb 5-2: USB disconnect, device number 3')
        build.usb(T0 + 5 * S + 100 * MS, 'S', 0x00, xfer=XFER_CTRL, dev=synth.ROOT_HUB, setup=control(0x23, 3, 4, 2),
                  status=-115)
        self.assertTrue(self.verdict(build, 'No reset')['ok'])


class Periodic(Base):
    def test_the_current_drivers_poll_fails(self):
        build = self.good()
        for second in range(1, 15):
            build.command(T0 + second * S, 0x0007)
        verdict = self.verdict(build, 'periodic')
        self.assertFalse(verdict['ok'])
        self.assertIn('MsgID 0x0007 x4', verdict['detail'])

    def test_the_start_up_burst_is_allowed(self):
        build = self.good()
        for index in range(20):
            build.command(T0 + index * 100 * MS, 0x0001)
        self.assertTrue(self.verdict(build, 'periodic')['ok'])


class Writes(Base):
    def test_overlapping_writes_fail(self):
        build = self.good()
        build.usb(T0 + S, 'S', 0x06, synth.v1_frame(1), status=-115)
        build.usb(T0 + S + MS, 'S', 0x04, bytes.fromhex('fa55800000'), status=-115)
        self.assertFalse(self.verdict(build, 'One write')['ok'])

    def test_one_after_another_passes(self):
        build = self.good()
        build.command(T0 + S, 0x0001)
        build.command(T0 + 2 * S, 0x0001)
        self.assertTrue(self.verdict(build, 'One write')['ok'])

    def test_a_bad_crc_fails(self):
        build = self.good()
        build.command(T0 + S, 0x0001)
        build.usb(T0 + 2 * S, 'S', 0x06, synth.v1_frame(1, bad_crc=True), status=-115)
        self.assertFalse(self.verdict(build, 'good CRC')['ok'])


class Reads(Base):
    def test_one_read_at_a_time_fails(self):
        build = synth.Builder(v1=True)
        build.command(T0, 0x100b)
        build.stream(T0 + 50 * MS, T0 + 3 * S)
        verdict = self.verdict(build, 'Two reads')
        self.assertFalse(verdict['ok'])
        self.assertIn('ep81', verdict['detail'])

    def test_two_reads_pass(self):
        self.assertTrue(self.verdict(self.good(), 'Two reads')['ok'])

    def test_too_short_to_tell(self):
        build = synth.Builder(v1=True)
        build.command(T0, 0x100b)
        build.stream(T0 + 50 * MS, T0 + 300 * MS, queued=True)
        self.assertIsNone(self.verdict(build, 'Two reads')['ok'])

    def test_reading_a_silent_endpoint_fails(self):
        build = self.good()
        build.usb(T0 + S, 'S', 0x83, status=-115, length=512, xfer=1)
        verdict = self.verdict(build, 'never answer')
        self.assertFalse(verdict['ok'])
        self.assertEqual(verdict['detail'], 'ep83')


class Settle(Base):
    def reconnect(self, after_ms):
        build = self.good()
        build.note(T0 + 5 * S, type='kernel', text='usb 5-2: USB disconnect, device number 3')
        build.usb(T0 + 5 * S + after_ms * MS, 'S', 0x06, synth.v1_frame(0x100b), status=-115, dev=9, flags=2)
        return build

    def test_rushing_back_fails(self):
        self.assertFalse(self.verdict(self.reconnect(800), 'after a disconnect')['ok'])

    def test_waiting_passes(self):
        self.assertTrue(self.verdict(self.reconnect(2500), 'after a disconnect')['ok'])


class FlashingPipe(Base):
    def test_mcu_traffic_needs_a_baseline(self):
        build = self.good()
        build.usb(T0 + S, 'S', 0x04, bytes.fromhex('fa55800000'), status=-115)
        verdict = self.verdict(build, 'flashing pipe')
        self.assertIsNone(verdict['ok'])
        self.assertIn('MCU 0x80', verdict['detail'])

    def test_something_the_baseline_never_sent_fails(self):
        before = self.good()
        baseline = analyze([before.write(self.dir / 'base.lcap')])
        after = self.good()
        after.usb(T0 + S, 'S', 0x04, bytes.fromhex('fa55990000'), status=-115)
        rules = self.rules(after, baseline=baseline)
        self.assertEqual(rules['Sends nothing the baseline did not']['detail'], 'MCU 0x99')
        self.assertFalse(rules['Sends nothing the baseline did not']['ok'])

    def test_matching_the_baseline_passes(self):
        baseline = analyze([self.good().write(self.dir / 'base.lcap')])
        self.assertTrue(self.rules(self.good(), baseline=baseline)['Sends nothing the baseline did not']['ok'])


class Render(Base):
    def test_unknowns_are_not_passes(self):
        text = render_audit(audit(analyze([self.good().write(self.dir / 'a.lcap')])))
        self.assertIn('pass', text)
        self.assertIn('  ? ', text)

    def test_compare_shows_the_quiet_driver_winning(self):
        noisy = synth.session(14)
        before = analyze([noisy.write(self.dir / 'before.lcap')])
        after = analyze([self.good().write(self.dir / 'after.lcap')])
        rows = {what: (a, b) for what, a, b in compare(before, after)}
        self.assertGreater(rows['Commands per second once running'][0], 0)
        self.assertEqual(rows['Commands per second once running'][1], 0)
        self.assertGreater(rows['Wire rules failed'][0], rows['Wire rules failed'][1])
        self.assertIn('Commands per second', render_compare(compare(before, after)))


if __name__ == '__main__':
    unittest.main()
