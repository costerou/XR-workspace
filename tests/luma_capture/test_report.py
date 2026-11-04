import json
import random
import tempfile
from pathlib import Path
import unittest

from tools.luma_capture import capfile
from tools.luma_capture.capfile import F_GLASSES
from tools.luma_capture.protocol import Framing
from tools.luma_capture.report import analyze, render

import synth
from synth import MS, S, T0


class Base(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.dir = Path(folder.name)

    def capture(self, build, name='a.lcap'):
        return build.write(self.dir / name)


class Rates(Base):
    def test_counts_msgid_0007_and_finds_the_field_itself(self):
        build = synth.session(10)
        result = analyze([self.capture(build)])
        self.assertEqual(result['msgid_0x0007']['total'], build.polled)
        self.assertEqual(result['msgid_0x0007']['peak_per_s'], 2)     # the start-up one shares a second with a poll
        self.assertAlmostEqual(result['msgid_0x0007']['per_s'], build.polled / result['seconds_with_traffic'], 2)
        self.assertEqual(result['framing']['source'], 'inferred')
        self.assertIn('offset=14', result['framing']['spec'])
        self.assertEqual(result['commands']['MsgID 0x0015']['total'], 1)

    def test_a_flood_shows_up_as_a_flood(self):
        build = synth.session(4)
        for index in range(500):
            build.command(T0 + 3 * S + index * MS, 0x0007)
        result = analyze([self.capture(build)])
        self.assertGreaterEqual(result['msgid_0x0007']['peak_per_s'], 500)
        self.assertFalse(result['endpoints']['ep82']['stream'])

    def test_a_steady_flood_of_replies_is_still_not_a_stream(self):
        build = synth.session(4)
        for index in range(800):
            build.command(T0 + 100 * MS + index * 5 * MS, 0x0007)
        result = analyze([self.capture(build)])
        self.assertTrue(result['endpoints']['ep81']['stream'])
        self.assertFalse(result['endpoints']['ep82']['stream'])

    def test_the_stream_is_recognised_and_measured(self):
        result = analyze([self.capture(synth.session(5))])
        self.assertTrue(result['endpoints']['ep81']['stream'])
        self.assertAlmostEqual(result['endpoints']['ep81']['per_s'], 1000, delta=5)
        self.assertFalse(result['endpoints']['ep82']['stream'])
        self.assertFalse(result['endpoints']['ep04']['stream'])

    def test_send_latency(self):
        result = analyze([self.capture(synth.session(5))])
        self.assertAlmostEqual(result['send_latency_ms']['ep04']['p50'], 0.2, places=3)

    def test_a_vendor_control_command_is_counted(self):
        build = synth.session(3)
        build.usb(T0 + S, 'S', 0x80, xfer=2, status=-115, length=8, setup=bytes((0xc0, 0x51, 0, 0, 0, 0, 8, 0)))
        result = analyze([self.capture(build)])
        self.assertEqual(result['commands']['control request 0x51']['total'], 1)

    def test_a_standard_control_request_is_not_a_command(self):
        build = synth.session(3)
        build.usb(T0 + S, 'S', 0x80, xfer=2, status=-115, length=18, setup=bytes((0x80, 6, 0, 1, 0, 0, 18, 0)))
        result = analyze([self.capture(build)])
        self.assertFalse([key for key in result['commands'] if 'control' in key])


class UnknownFraming(Base):
    def test_no_settled_framing_prints_no_count_at_all(self):
        path = self.capture(synth.session(5, echo=False))
        result = analyze([path])
        self.assertIsNone(result['msgid_0x0007'])
        self.assertEqual(result['framing']['source'], 'undetermined')
        text = render(result)
        self.assertIn('Cannot be counted', text)
        self.assertNotIn('0 sent', text)
        self.assertIn('candidate', text)

    def test_the_shape_table_still_counts_every_transfer(self):
        build = synth.session(5, echo=False)
        result = analyze([self.capture(build)])
        self.assertEqual(result['commands']['ep04 len=64 abcd']['total'], build.polled + 4)

    def test_a_given_framing_overrules_inference(self):
        build = synth.session(5, echo=False)
        result = analyze([self.capture(build)], framing=Framing(14, 'little', b'\xab\xcd'))
        self.assertEqual(result['msgid_0x0007']['total'], build.polled)
        self.assertEqual(result['framing']['source'], 'given')

    def test_a_known_framing_with_no_0007_is_a_real_zero(self):
        build = synth.Builder()
        for index in range(30):
            build.command(T0 + index * 10 * MS, 0x0009)
        result = analyze([self.capture(build)], framing=Framing(14, 'little', b'\xab\xcd'))
        self.assertEqual(result['msgid_0x0007']['total'], 0)
        self.assertIn('0 sent', render(result))


class Disconnect(Base):
    def drop(self, **options):
        return analyze([self.capture(synth.session(10, drop_at=8, **options))])['drops'][0]

    def test_one_disconnect_at_the_first_sign(self):
        result = analyze([self.capture(synth.session(10, drop_at=8))])
        self.assertEqual(len(result['drops']), 1)
        drop = result['drops'][0]
        self.assertEqual(drop['t_ns'], T0 + 8 * S + MS)
        self.assertTrue(any('USB disconnect' in signal for signal in drop['signals']))
        self.assertTrue(any('ESHUTDOWN' in signal for signal in drop['signals']))

    def test_the_order_of_what_stopped(self):
        order = {item['what']: item['offset_s'] for item in self.drop()['order']}
        self.assertAlmostEqual(order['last command before the drop'], -0.501, places=3)
        self.assertAlmostEqual(order['first failed transfer'], 0.002, places=3)
        self.assertAlmostEqual(order['hub reports the port disconnected'], -0.001, delta=0.0002)
        self.assertAlmostEqual(order['first SDK error line'], 0.001, places=3)

    def test_the_stream_stopped_before_the_port_went(self):
        drop = self.drop()
        self.assertAlmostEqual(drop['stream_last']['ep81'], -0.051, delta=0.002)
        self.assertLess(drop['stream_gap']['ep81'], 1.5)

    def test_a_stuck_command_shows_as_in_flight(self):
        drop = self.drop()
        self.assertEqual(len(drop['in_flight']), 1)
        self.assertEqual(drop['in_flight'][0]['text'], 'command MsgID 0x0007')
        self.assertAlmostEqual(drop['in_flight'][0]['age_s'], 0.501, places=3)

    def test_the_failure_row_names_the_command_that_failed(self):
        rows = [row['text'] for row in self.drop()['rows']]
        self.assertIn('FAILED command MsgID 0x0007 (ESHUTDOWN)', rows)

    def test_the_timeline_is_in_order_and_hides_only_the_stream(self):
        rows = self.drop()['rows']
        times = [row['t_ns'] for row in rows]
        self.assertEqual(times, sorted(times))
        joined = '\n'.join(row['text'] for row in rows)
        self.assertNotIn('stream', joined)
        self.assertIn('hub GET_PORT_STATUS port 2 -> power; changed: connection', joined)
        self.assertIn('kernel: usb 5-2: USB disconnect', joined)
        self.assertIn('driver_log: [E][libglasses]', joined)
        self.assertIn('reply MsgID 0x0007', joined)

    def test_second_by_second(self):
        seconds = self.drop()['seconds']
        self.assertGreater(seconds[-2]['stream'], 950)
        self.assertEqual(seconds[-2]['target'], 1)
        self.assertEqual(seconds[-1]['commands'], 1)
        self.assertEqual(seconds[0]['failed'], 1)
        self.assertLess(seconds[-1]['stream'], seconds[-2]['stream'])

    def test_the_window_is_as_wide_as_asked(self):
        path = self.capture(synth.session(10, drop_at=8))
        drop = analyze([path], window_s=2)['drops'][0]
        self.assertGreaterEqual(min(row['offset_s'] for row in drop['rows']), -2)
        self.assertEqual(min(drop['seconds']), -2)

    def test_row_limit_keeps_the_rows_nearest_the_end(self):
        drop = analyze([self.capture(synth.session(10, drop_at=8))], rows=3)['drops'][0]
        self.assertEqual(len(drop['rows']), 3)
        self.assertGreater(drop['rows_dropped'], 0)
        self.assertEqual(drop['rows'][-1]['text'], 'FAILED command MsgID 0x0007 (ESHUTDOWN)')

    def test_a_timed_out_transfer_is_told_apart_from_a_failed_one(self):
        build = synth.session(10, drop_at=8)
        stuck = build.command(T0 + 7 * S, 0x0007, complete=False)
        build.usb(T0 + 7 * S + 500 * MS, 'C', 0x04, urb_id=stuck, status=-2)
        order = {item['what']: item['offset_s'] for item in analyze([self.capture(build)])['drops'][0]['order']}
        self.assertAlmostEqual(order['first cancelled transfer (a timeout)'], -0.501, places=3)

    def test_failed_transfers_with_no_kernel_line_are_marked_unconfirmed(self):
        build = synth.session(4)
        build.usb(T0 + 2 * S, 'C', 0x82, status=-108)
        result = analyze([self.capture(build)])
        self.assertFalse(result['drops'][0]['confirmed'])
        text = render(result)
        self.assertIn('Disconnects: 0 (and 1 unconfirmed)', text)
        self.assertIn('UNCONFIRMED', text)

    def test_a_kernel_line_confirms(self):
        result = analyze([self.capture(synth.session(10, drop_at=8))])
        self.assertTrue(result['drops'][0]['confirmed'])
        self.assertIn('Disconnects: 1\n', render(result))

    def test_two_drops_seconds_apart_are_two(self):
        build = synth.session(4)
        build.note(T0 + S, type='kernel', text='usb 5-2: USB disconnect, device number 3')
        build.note(T0 + 3 * S + 100 * MS, type='kernel', text='usb 5-2: USB disconnect, device number 4')
        result = analyze([self.capture(build)])
        self.assertEqual(len(result['drops']), 2)

    def test_overlapping_windows_both_get_the_shared_traffic(self):
        build = synth.session(10)
        build.note(T0 + 5 * S, type='kernel', text='usb 5-2: USB disconnect, device number 3')
        build.note(T0 + 8 * S, type='kernel', text='usb 5-2: USB disconnect, device number 4')
        drops = analyze([self.capture(build)])['drops']
        self.assertTrue(drops[0]['seconds'])
        shared = T0 + 4 * S + 500 * MS + 0
        for drop in drops:
            self.assertTrue(any(row['t_ns'] == shared for row in drop['rows']), drop['clock'])

    def test_three_drops_inside_one_window_all_see_the_same_traffic(self):
        build = synth.session(10)
        for tenths, number in ((30, 3), (55, 4), (80, 5)):      # 2.5 s apart, just past the clustering limit
            build.note(T0 + tenths * 100 * MS, type='kernel', text=f'usb 5-2: USB disconnect, device number {number}')
        drops = analyze([self.capture(build)], window_s=6)['drops']
        self.assertEqual(len(drops), 3)
        shared = T0 + 2 * S + 500 * MS
        for drop in drops:
            self.assertTrue(any(row['t_ns'] == shared for row in drop['rows']), drop['clock'])

    def test_another_device_leaving_is_not_counted(self):
        build = synth.session(4)
        build.note(T0 + S, type='kernel', text='usb 5-1: USB disconnect, device number 2')
        self.assertEqual(analyze([self.capture(build)])['drops'], [])


class Honesty(Base):
    def test_lost_events_are_a_warning(self):
        build = synth.session(3)
        build.note(T0 + S, type='usbmon_stats', dropped=44, since_last=44)
        text = render(analyze([self.capture(build)]))
        self.assertIn('usbmon lost 44 events', text)

    def test_a_cut_off_file_is_a_warning(self):
        path = self.capture(synth.session(3))
        path.write_bytes(path.read_bytes()[:-5])
        result = analyze([path])
        self.assertEqual(result['damaged_files'], [str(path)])
        self.assertIn('WARNING: cut off mid-record', render(result))

    def test_missing_logs_are_said(self):
        text = render(analyze([self.capture(synth.session(3))]))
        self.assertIn('no kernel log lines', text)
        self.assertIn('no driver log lines', text)

    def test_nothing_on_the_wire(self):
        build = synth.Builder()
        build.note(T0, type='start')
        result = analyze([self.capture(build)])
        self.assertEqual(result['usb_events'], 0)
        self.assertIn('none', render(result))

    def test_json_output_is_real_json(self):
        result = analyze([self.capture(synth.session(10, drop_at=8))])
        self.assertEqual(json.loads(json.dumps(result))['drops'][0]['clock'], result['drops'][0]['clock'])


class Sets(Base):
    def test_a_folder_of_segments_reads_as_one_capture(self):
        whole = synth.session(10, drop_at=8)
        middle = T0 + 4 * S
        first, second = synth.Builder(), synth.Builder()
        first.records = [r for r in whole.records if r.t_ns < middle]
        second.records = [r for r in whole.records if r.t_ns >= middle]
        self.capture(first, 'one.lcap')
        self.capture(second, 'two.lcap')
        parts = analyze([self.dir])
        self.assertEqual(parts['msgid_0x0007']['total'], whole.polled)
        self.assertEqual(len(parts['drops']), 1)
        self.assertEqual(len(parts['drops'][0]['in_flight']), 1)


class DocumentedFraming(Base):
    def session(self, seconds=6):
        build = synth.Builder(v1=True)
        build.devices(T0)
        for index, msgid in enumerate((0x0001, 0x100b, 0x0007)):
            build.command(T0 + index * 10 * MS, msgid)
        for second in range(1, seconds):
            build.command(T0 + second * S, 0x0007)
        build.stream(T0 + 50 * MS, T0 + seconds * S)
        return build

    def test_the_frames_prove_themselves_and_no_guessing_happens(self):
        build = self.session()
        result = analyze([self.capture(build)])
        self.assertEqual(result['framing']['source'], 'documented')
        self.assertEqual(result['framing']['candidates'], [])
        self.assertEqual(result['msgid_0x0007']['total'], 6)
        self.assertEqual(result['framing']['frames_with_good_crc'], 2 * (3 + 5))
        text = render(result)
        self.assertIn('proven here', text)
        self.assertIn('MsgID 0x100b', text)

    def test_a_bad_crc_is_a_warning(self):
        build = self.session()
        build.usb(T0 + S, 'S', 0x06, synth.v1_frame(7, bad_crc=True), status=-115)
        result = analyze([self.capture(build)])
        self.assertEqual(result['framing']['frames_with_bad_crc'], 1)
        self.assertIn('failed their CRC', render(result))

    def test_sensor_bytes_that_look_like_a_frame_are_not_bad_frames(self):
        build = self.session()
        build.stream(T0 + 2 * S, T0 + 3 * S, ep=0x83, make=lambda i: b'\xff\xfe' + bytes(60))
        result = analyze([self.capture(build)])
        self.assertTrue(result['endpoints']['ep83']['stream'])
        self.assertEqual(result['framing']['frames_with_bad_crc'], 0)

    def test_replies_are_counted_on_their_own(self):
        result = analyze([self.capture(self.session())])
        self.assertEqual(result['incoming']['MsgID 0x0007']['total'], 6)

    def test_unprompted_events_show_up_as_incoming(self):
        build = self.session()
        for index in range(3):
            build.usb(T0 + S + index * MS, 'C', 0x87, synth.v1_frame(0x1234, reply=True))
        self.assertEqual(analyze([self.capture(build)])['incoming']['MsgID 0x1234']['total'], 3)


class StreamHealth(Base):
    def imu(self, *, jitter=None, seconds=4, samples=1, period=MS):
        rng = random.Random(9)
        build = synth.Builder(v1=True)
        build.command(T0, 0x100b)
        build.stream(T0 + 50 * MS, T0 + seconds * S, jitter=jitter, period=period,
                     make=lambda i: synth.imu_packet(i, rng=rng, samples=samples, period_us=period // 1000 // samples))
        return analyze([self.capture(build)])['streams']['ep81']

    def test_rate_and_even_spacing(self):
        stream = self.imu()
        self.assertAlmostEqual(stream['hz'], 1000, delta=1)
        self.assertEqual((stream['p50_us'], stream['max_us'], stream['gaps'], stream['outages']), (1000, 1000, 0, 0))

    def test_a_stall_is_seen(self):
        stream = self.imu(jitter=lambda i: 20 * MS if i == 500 else 0)
        self.assertEqual(stream['gaps'], 1)
        self.assertEqual(stream['max_us'], 21000)
        self.assertEqual(stream['p99_us'], 1000)

    def test_an_outage_is_not_counted_as_jitter(self):
        stream = self.imu(jitter=lambda i: 2 * S if i == 500 else 0)
        self.assertEqual((stream['outages'], stream['max_us']), (1, 1000))

    def test_the_device_clock_is_found_and_read_in_microseconds(self):
        found = self.imu()['profile']
        clock = found['clocks'][0]
        self.assertEqual((clock['offset'], clock['width'], clock['endian'], clock['step']), (2, 4, 'little', 1000))
        self.assertEqual(clock['unit'], 'us')
        self.assertLess(abs(clock['drift_ppm']), 3000)

    def test_gravity_finds_the_accelerometer(self):
        found = self.imu()['profile']['gravity']
        real = next(item for item in found if item['offset'] == 18)
        self.assertEqual((real['format'], real['unit']), ('float32 little', 'g'))
        self.assertAlmostEqual(real['length'], 1.0, places=2)
        self.assertEqual(found[0]['format'], 'float32 little')    # floats rank above chance integers

    def test_several_samples_in_one_transfer_are_counted(self):
        found = self.imu(samples=4, period=4 * MS)['profile']
        self.assertEqual((found['samples_per_transfer'], found['sample_spacing_bytes']), (4, 30))
        self.assertEqual(found['magic'], 'c53a')

    def test_the_report_says_all_this(self):
        rng = random.Random(9)
        build = synth.Builder(v1=True)
        build.command(T0, 0x100b)
        build.stream(T0 + 50 * MS, T0 + 3 * S, make=lambda i: synth.imu_packet(i, rng=rng))
        text = render(analyze([self.capture(build)]))
        self.assertIn('Stream ep81', text)
        self.assertIn('possible clock: offset 2', text)
        self.assertIn('possible accelerometer: offset 18, float32 little', text)
        self.assertNotIn('OUT ep06', text)     # a completed send is not a second command

    def test_random_noise_claims_nothing(self):
        found = analyze([self.capture(synth.session(3))])['streams']['ep81']['profile']
        self.assertEqual(found['gravity'], [])
        self.assertEqual(found['clocks'], [])


class StillPoses(Base):
    def posed(self, axes, *, g=1.0):
        rng = random.Random(9)
        build = synth.Builder(v1=True)
        build.command(T0, 0x100b)
        for pose, axis in enumerate(axes):
            start = T0 + 50 * MS + pose * 4 * S
            build.note(start, type='mark', text=f'still pose {pose}')
            build.stream(start, start + 3 * S, make=lambda i, a=axis: synth.imu_packet(i, rng=rng, g=g, axis=a))
        return build

    def test_a_pose_on_each_axis_leaves_only_the_real_accelerometer(self):
        pinned = analyze([self.capture(self.posed((1, 0, 2)))])['streams']['ep81']['accelerometer']
        self.assertEqual([(item['offset'], item['format'], item['unit']) for item in pinned],
                         [(18, 'float32 little', 'g')])

    def test_two_poses_may_leave_a_neighbouring_window_and_the_real_one_stays(self):
        pinned = analyze([self.capture(self.posed((1, 0)))])['streams']['ep81']['accelerometer']
        self.assertEqual([item['offset'] for item in pinned], [14, 18])

    def test_metres_per_second_are_told_from_g(self):
        pinned = analyze([self.capture(self.posed((1, 0, 2), g=9.80665))])['streams']['ep81']['accelerometer']
        self.assertEqual([(item['offset'], item['unit']) for item in pinned], [(18, 'm/s2')])

    def test_the_report_names_it(self):
        self.assertIn('ACCELEROMETER, by still poses: offset 18',
                      render(analyze([self.capture(self.posed((1, 0, 2)))])))

    def test_one_pose_claims_nothing(self):
        self.assertIsNone(analyze([self.capture(self.posed((1,)))])['streams']['ep81']['accelerometer'])

    def test_random_noise_is_not_pinned(self):
        build = synth.session(9)
        build.note(T0 + S, type='mark', text='still one')
        build.note(T0 + 5 * S, type='mark', text='still two')
        self.assertEqual(analyze([self.capture(build)])['streams']['ep81']['accelerometer'], [])


class Connections(Base):
    def test_shows_what_was_sent_before_the_stream_started(self):
        build = synth.Builder(v1=True)
        build.devices(T0)
        build.command(T0, 0x100b)
        build.command(T0 + 300 * MS, 0x0007)
        build.stream(T0 + 1500 * MS, T0 + 4 * S)
        build.command(T0 + 3 * S, 0x0007)       # long after the stream: not part of start-up
        connection = analyze([self.capture(build)])['connections'][0]
        self.assertEqual(connection['stream_after_s'], 1.5)
        self.assertEqual(connection['commands'], 2)
        text = [row['text'] for row in connection['rows']]
        self.assertEqual(text[0], 'command MsgID 0x100b (enable status reports)')
        self.assertTrue(any('reply MsgID 0x0007' in line for line in text))

    def test_a_stream_that_never_came_is_said(self):
        build = synth.Builder(v1=True)
        build.command(T0, 0x100b)
        result = analyze([self.capture(build)])
        self.assertIsNone(result['connections'][0]['stream_after_s'])
        self.assertIn('the stream never started', render(result))

    def test_a_failure_during_start_up_shows(self):
        build = synth.Builder(v1=True)
        build.command(T0, 0x100b)
        read = build.usb(T0 + 100 * MS, 'S', 0x82, status=-115, length=16384)
        build.usb(T0 + 300 * MS, 'C', 0x82, urb_id=read, status=-2)
        rows = analyze([self.capture(build)])['connections'][0]['rows']
        self.assertTrue(any(row['text'].startswith('FAILED') and 'cancelled' in row['status'] for row in rows))

    def test_each_reconnection_gets_its_own_sequence(self):
        build = synth.Builder(v1=True)
        build.command(T0, 0x100b)
        build.usb(T0 + S, 'S', 0x06, synth.v1_frame(0x100b), status=-115, dev=9, flags=F_GLASSES)
        self.assertEqual([c['dev'] for c in analyze([self.capture(build)])['connections']], [synth.GLASSES_DEV, 9])


class Reconnect(Base):
    def test_how_long_a_reconnect_took_and_the_gap_to_the_second_drop(self):
        build = synth.Builder(v1=True)
        build.devices(T0)
        build.command(T0, 0x100b)
        build.stream(T0 + 10 * MS, T0 + S)
        build.note(T0 + S, type='kernel', text='usb 5-2: USB disconnect, device number 3')
        build.usb(T0 + 3 * S, 'S', 0x06, synth.v1_frame(0x100b), status=-115, dev=9, flags=F_GLASSES)
        build.stream(T0 + 3500 * MS, T0 + 6 * S, dev=9)
        build.note(T0 + 6 * S, type='kernel', text='usb 5-2: USB disconnect, device number 9')
        result = analyze([self.capture(build)])
        first, second = result['drops']
        self.assertEqual(first['first_command_after_s'], 2.0)
        self.assertEqual(first['first_stream_after_s'], 2.5)
        self.assertEqual(second['since_previous_s'], 5.0)
        self.assertIsNone(second['first_stream_after_s'])
        self.assertIn('Back: first command +2.0 s, stream +2.5 s', render(result))


class Marks(Base):
    def test_what_followed_a_mark_is_listed(self):
        build = synth.session(4)
        build.note(T0 + 2 * S, type='mark', text='pressed brightness up')
        build.usb(T0 + 2100 * MS, 'C', 0x84, b'\x01\x02', dev=6, flags=capfile.F_COMPANION)
        result = analyze([self.capture(build)])
        self.assertEqual(result['marks'][0]['text'], 'pressed brightness up')
        rows = [row['text'] for row in result['marks'][0]['rows']]
        self.assertIn('companion ep84 len=2', rows)
        self.assertIn('Mark at', render(result))
        self.assertIn('companion ep84', result['endpoints'])


if __name__ == '__main__':
    unittest.main()
