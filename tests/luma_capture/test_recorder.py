import sys
import tempfile
import threading
import time
from pathlib import Path
import unittest

from tools.luma_capture import capfile, recorder as recorder_module
from tools.luma_capture.capfile import (F_CAPPED, F_COMPANION, F_GLASSES, F_KERNEL_TRUNCATED, F_POLICY_DROPPED,
                                        XFER_BULK, XFER_CTRL, XFER_INT, XFER_ISO)
from tools.luma_capture.recorder import Recorder, follow_file, follow_kernel, keep_payload
from tools.luma_capture.usbmon import Event

S = 10**9
T0 = 1_762_000_000 * S
ROOT = {'bus': 5, 'dev': 1, 'path': 'usb5', 'vid': 0x1d6b, 'pid': 2, 'hub': True, 'product': ''}
HUB = {'bus': 5, 'dev': 3, 'path': '5-2', 'vid': 0x1a86, 'pid': 0x8091, 'hub': True, 'product': ''}
CAMERA = {'bus': 5, 'dev': 5, 'path': '5-2.2', 'vid': 0x0c45, 'pid': 0x636b, 'hub': False, 'product': 'camera'}
HEADSET = {'bus': 5, 'dev': 2, 'path': '5-1', 'vid': 0x1532, 'pid': 0x0527, 'hub': False, 'product': 'headset'}
COMPANION = {'bus': 5, 'dev': 6, 'path': '5-2.3', 'vid': 0x35ca, 'pid': 0x1102, 'hub': False, 'product': 'mic'}


def glasses(dev=4):
    return {'bus': 5, 'dev': dev, 'path': '5-2.1', 'vid': 0x35ca, 'pid': 0x1104, 'hub': False, 'product': 'Luma'}


def event(*, dev=4, xfer=XFER_BULK, ep=0x81, kind='C', status=0, data=b'abcd', t=T0, length=None, urb=1, has_data=True):
    return Event(urb, kind, xfer, ep, dev, 5, False, has_data, t, status, len(data) if length is None else length,
                 len(data), bytes(8), data)


class Rig:
    def __init__(self, case, **options):
        folder = tempfile.TemporaryDirectory()
        case.addCleanup(folder.cleanup)
        self.dir = Path(folder.name) / 'capture'
        self.now = T0
        self.devices = [ROOT, HUB, glasses(), CAMERA, HEADSET, COMPANION]
        self.scans = 0
        self.log = []
        defaults = dict(segment_s=10, keep_segments=3, keep_start=1, post_roll_s=5)
        self.recorder = Recorder(object(), self.dir, 5, scan=self.scan, clock=lambda: self.now,
                                 log=self.log.append, **{**defaults, **options})
        self.recorder.start()
        case.addCleanup(lambda: self.recorder._writer and self.recorder._writer.close())

    def scan(self):
        self.scans += 1
        return self.devices

    def advance(self, seconds, step=1.0):
        """Time passes with traffic flowing, so periodic work runs as it does live."""
        end = self.now + int(seconds * S)
        while self.now < end:
            self.now = min(self.now + int(step * S), end)
            self.recorder.on_event(event(t=self.now))
            self.recorder.tick()

    def segments(self, folder):
        return sorted((self.dir / folder).rglob('*.lcap'))

    def usb(self):
        self.recorder._writer.flush()
        return [r for r in capfile.Reader(self.recorder._writer.path) if not r.is_note]

    def notes(self, kind=None):
        self.recorder._writer.flush()
        found = [r.note for path in self.dir.rglob('*.lcap') for r in capfile.Reader(path) if r.is_note]
        return [note for note in found if kind is None or note['type'] == kind]


class Policy(unittest.TestCase):
    def test_never_audio_or_video_samples(self):
        for device_class in ('glasses', 'hub', 'other', 'unknown'):
            self.assertFalse(keep_payload(XFER_ISO, device_class))

    def test_control_always(self):
        self.assertTrue(keep_payload(XFER_CTRL, 'other'))

    def test_other_devices_bulk_and_interrupt_never(self):
        self.assertFalse(keep_payload(XFER_BULK, 'other'))
        self.assertFalse(keep_payload(XFER_INT, 'other'))

    def test_the_glasses_and_hubs_yes(self):
        self.assertTrue(keep_payload(XFER_BULK, 'glasses'))
        self.assertTrue(keep_payload(XFER_INT, 'hub'))


class Records(unittest.TestCase):
    def test_glasses_traffic_is_kept_and_marked(self):
        rig = Rig(self)
        rig.recorder.on_event(event())
        record = rig.usb()[0]
        self.assertEqual((record.data, record.dev, record.event), (b'abcd', 4, 'C'))
        self.assertTrue(record.flags & F_GLASSES)
        self.assertTrue(record.replayable)

    def test_camera_payload_is_thrown_away_but_the_event_stays(self):
        rig = Rig(self)
        rig.recorder.on_event(event(dev=5, data=b'pixels'))
        record = rig.usb()[0]
        self.assertEqual((record.data, record.urb_len), (b'', 6))
        self.assertTrue(record.flags & F_POLICY_DROPPED)
        self.assertFalse(record.flags & F_GLASSES)

    def test_a_16_kib_start_up_read_is_kept_whole(self):
        rig = Rig(self)
        rig.recorder.on_event(event(ep=0x82, data=bytes(16384)))
        record = rig.usb()[0]
        self.assertEqual(len(record.data), 16384)
        self.assertTrue(record.replayable)

    def test_a_camera_rate_endpoint_is_cut_down_but_keeps_its_headers(self):
        rig = Rig(self)
        for index in range(100):
            rig.recorder.on_event(event(ep=0x82, data=bytes(1 << 15), t=T0 + index))
        records = rig.usb()
        self.assertEqual(len(records[0].data), 1 << 15)
        self.assertEqual(len(records[-1].data), recorder_module.HEAD_BYTES)
        self.assertEqual(records[-1].urb_len, 1 << 15)
        self.assertTrue(records[-1].flags & F_CAPPED)
        self.assertLess(sum(len(r.data) for r in records), 2 << 20)

    def test_the_budget_is_per_second_and_per_endpoint(self):
        rig = Rig(self)
        for index in range(100):
            rig.recorder.on_event(event(ep=0x82, data=bytes(1 << 15), t=T0))
        rig.recorder.on_event(event(ep=0x81, data=bytes(1 << 15), t=T0))
        rig.recorder.on_event(event(ep=0x82, data=bytes(1 << 15), t=T0 + S))
        records = rig.usb()
        self.assertEqual(len(records[-2].data), 1 << 15)
        self.assertEqual(len(records[-1].data), 1 << 15)

    def test_data_the_fetch_buffer_left_behind_counts_as_capped(self):
        rig = Rig(self)
        big = event(data=bytes(100))
        big.data = big.data[:40]
        rig.recorder.on_event(big)
        record = rig.usb()[0]
        self.assertEqual(len(record.data), 40)
        self.assertTrue(record.flags & F_CAPPED)
        self.assertFalse(record.flags & F_KERNEL_TRUNCATED)

    def test_the_companion_reports_are_kept_for_the_buttons(self):
        rig = Rig(self)
        rig.recorder.on_event(event(dev=6, xfer=XFER_INT, data=b'\x01\x02'))
        record = rig.usb()[0]
        self.assertEqual(record.data, b'\x01\x02')
        self.assertTrue(record.flags & F_COMPANION)
        self.assertFalse(record.replayable)

    def test_the_companion_microphone_is_not(self):
        rig = Rig(self)
        rig.recorder.on_event(event(dev=6, xfer=XFER_ISO, data=b'sound'))
        self.assertEqual(rig.usb()[0].data, b'')

    def test_the_headset_on_the_same_bus_is_not_kept(self):
        rig = Rig(self)
        rig.recorder.on_event(event(dev=2, xfer=XFER_INT, data=b'buttons'))
        self.assertEqual(rig.usb()[0].data, b'')

    def test_big_transfers_are_capped_and_never_replayed(self):
        rig = Rig(self, max_payload=8)
        rig.recorder.on_event(event(data=bytes(100)))
        record = rig.usb()[0]
        self.assertEqual((len(record.data), record.urb_len), (8, 100))
        self.assertTrue(record.flags & F_CAPPED)
        self.assertFalse(record.replayable)

    def test_a_kernel_cut_transfer_is_marked(self):
        rig = Rig(self)
        rig.recorder.on_event(event(data=b'abcd', length=512))
        self.assertTrue(rig.usb()[0].flags & F_KERNEL_TRUNCATED)

    def test_a_read_being_posted_is_not_a_truncation(self):
        rig = Rig(self)
        rig.recorder.on_event(event(kind='S', data=b'', length=512, has_data=False))
        self.assertFalse(rig.usb()[0].flags & F_KERNEL_TRUNCATED)

    def test_every_segment_can_be_read_alone(self):
        rig = Rig(self)
        first = rig.recorder._writer.path
        rig.advance(11)
        for path in [rig.recorder._writer.path] + rig.segments('start'):
            rig.recorder._writer.flush()
            kinds = [r.note['type'] for r in capfile.Reader(path) if r.is_note][:2]
            self.assertEqual(kinds, ['clock', 'device_map'], path)
        self.assertFalse(first.exists())    # it moved to start/

    def test_the_output_folder_is_private(self):
        rig = Rig(self)
        self.assertEqual(rig.dir.stat().st_mode & 0o777, 0o700)


class Reconnect(unittest.TestCase):
    def test_the_glasses_are_recognised_at_their_new_address(self):
        rig = Rig(self)
        rig.devices = [ROOT, HUB, glasses(dev=9), CAMERA]
        rig.now += S
        rig.recorder.on_event(event(dev=9))
        self.assertTrue(rig.usb()[0].flags & F_GLASSES)

    def test_going_and_coming_back_are_both_written_down(self):
        rig = Rig(self, stop_after_drops=None)
        rig.advance(1)
        rig.devices = [ROOT]
        rig.advance(1)
        rig.devices = [ROOT, HUB, glasses(dev=9)]
        rig.advance(1)
        self.assertEqual(len(rig.notes('glasses_gone')), 1)
        self.assertEqual(rig.notes('glasses_back'), [{'type': 'glasses_back', 'dev': 9}])

    def test_a_new_number_between_two_scans_is_still_a_drop(self):
        rig = Rig(self, stop_after_drops=None)
        rig.advance(1)
        rig.devices = [ROOT, HUB, glasses(dev=9)]
        rig.advance(1)
        self.assertEqual(rig.notes('glasses_gone')[-1]['why'], 'new device number')
        self.assertEqual(len(rig.notes('glasses_back')), 1)

    def test_glasses_plugged_in_after_the_start_are_not_a_return(self):
        rig = Rig(self)
        rig.recorder._glasses_dev = None
        rig.recorder._seen_glasses = False
        rig.devices = [ROOT, HUB, glasses(dev=9)]
        rig.advance(1)
        self.assertEqual(rig.notes('glasses_back'), [])
        self.assertEqual(rig.notes('glasses_gone'), [])

    def test_a_device_not_yet_listed_is_kept_rather_than_lost(self):
        rig = Rig(self)
        rig.now += S
        rig.recorder.on_event(event(dev=77, data=b'hello'))
        record = rig.usb()[0]
        self.assertEqual(record.data, b'hello')
        self.assertFalse(record.flags & F_GLASSES)

    def test_a_chatty_unknown_device_cannot_flood_sysfs(self):
        rig = Rig(self)
        rig.now += S
        before = rig.scans
        for _ in range(500):
            rig.recorder.on_event(event(dev=77))
        self.assertEqual(rig.scans - before, 1)

    def test_a_changed_device_map_is_written_down(self):
        rig = Rig(self)
        rig.devices = [ROOT, HUB, glasses(dev=9)]
        rig.advance(1)
        maps = rig.notes('device_map')
        self.assertEqual(maps[-1]['port'], '5-2')
        self.assertEqual({d['dev'] for d in maps[-1]['devices']}, {1, 3, 9})


class Rolling(unittest.TestCase):
    def test_keeps_the_start_and_the_latest_only(self):
        rig = Rig(self)
        rig.advance(100)
        self.assertEqual(len(rig.segments('start')), 1)
        self.assertEqual(len(rig.segments('rolling')), 4)     # 3 closed plus the open one
        self.assertEqual(rig.segments('drops'), [])

    def test_stops_at_the_disk_budget(self):
        rig = Rig(self, max_total_bytes=500)
        rig.advance(30)
        self.assertIn('disk budget', rig.recorder.stop_reason)

    def test_duration(self):
        rig = Rig(self, duration_s=5)
        rig.advance(4)
        self.assertIsNone(rig.recorder.stop_reason)
        rig.advance(2)
        self.assertEqual(rig.recorder.stop_reason, 'duration reached')


class Drops(unittest.TestCase):
    def test_a_drop_saves_what_came_before_and_after(self):
        rig = Rig(self, stop_after_drops=None)
        rig.advance(45)
        rig.recorder.note('kernel', text='usb 5-2: USB disconnect, device number 3')
        rig.advance(4)
        self.assertEqual(rig.segments('drops'), [])
        rig.advance(2)
        saved = rig.segments('drops')
        self.assertGreaterEqual(len(saved), 3)
        self.assertEqual(rig.recorder.drops_saved, 1)
        times = [r.t_ns for path in saved for r in capfile.Reader(path) if not r.is_note]
        self.assertLess(min(times), T0 + 25 * S)      # well before the drop
        self.assertGreater(max(times), T0 + 49 * S)   # into the post-roll
        self.assertIn('drop detected', rig.log[0])

    def test_the_second_drop_of_a_pair_lands_in_the_same_folder(self):
        rig = Rig(self, stop_after_drops=None)
        rig.advance(5)
        rig.recorder.note('kernel', text='usb 5-2: USB disconnect, device number 3')
        rig.advance(3)
        rig.recorder.note('kernel', text='usb 5-2: USB disconnect, device number 4')
        rig.advance(7)
        self.assertEqual(rig.recorder.drops_saved, 1)
        self.assertEqual(len(list((rig.dir / 'drops').iterdir())), 1)

    def test_glasses_vanishing_from_sysfs_counts_even_with_no_kernel_log(self):
        rig = Rig(self, stop_after_drops=None)
        rig.advance(2)
        rig.devices = [ROOT]
        rig.advance(7)
        self.assertEqual(rig.recorder.drops_saved, 1)
        self.assertTrue(rig.notes('glasses_gone'))

    def test_a_flushed_endpoint_alone_saves_nothing(self):
        rig = Rig(self)
        rig.advance(2)
        rig.recorder.on_event(event(status=-108, data=b''))     # what SET_INTERFACE does to a posted read
        rig.advance(10)
        self.assertEqual(rig.recorder.drops_saved, 0)

    def test_another_device_leaving_is_not_a_drop(self):
        rig = Rig(self)
        rig.advance(2)
        rig.recorder.note('kernel', text='usb 5-1: USB disconnect, device number 2')
        rig.advance(10)
        self.assertEqual(rig.recorder.drops_saved, 0)

    def test_stops_after_enough_drops(self):
        rig = Rig(self, stop_after_drops=1)
        rig.advance(2)
        rig.recorder.note('glasses_gone')
        rig.advance(6)
        self.assertEqual(rig.recorder.stop_reason, '1 drops saved')

    def test_stopping_during_the_post_roll_still_saves_it(self):
        rig = Rig(self)
        rig.advance(2)
        rig.recorder.note('glasses_gone')
        rig.recorder.stop('asked to stop')
        rig.recorder.finish()
        self.assertEqual(rig.recorder.drops_saved, 1)
        self.assertTrue(rig.segments('drops'))


class Loss(unittest.TestCase):
    def test_events_the_kernel_lost_are_written_down(self):
        rig = Rig(self)
        lost = [0]
        rig.recorder.source = type('S', (), {'stats': lambda _: (0, lost[0])})()
        rig.advance(1)
        lost[0] = 12
        rig.advance(1)
        self.assertEqual(rig.notes('usbmon_stats')[-1], {'type': 'usbmon_stats', 'dropped': 12, 'since_last': 12})


class Run(unittest.TestCase):
    def test_the_whole_loop_ends_cleanly(self):
        rig_dir = tempfile.TemporaryDirectory()
        self.addCleanup(rig_dir.cleanup)
        batches = [[event(t=T0 + i)] for i in range(5)]
        now = [T0]

        class Source:
            def read_batch(self, _):
                now[0] += S
                return batches.pop(0) if batches else []

        recorder = Recorder(Source(), Path(rig_dir.name) / 'c', 5, scan=lambda: [glasses()], clock=lambda: now[0],
                            segment_s=100, duration_s=8)
        recorder.run()
        self.assertEqual(recorder.stop_reason, 'duration reached')
        files = list(Path(rig_dir.name).rglob('*.lcap'))
        records = [r for path in files for r in capfile.Reader(path)]
        self.assertEqual(len([r for r in records if not r.is_note]), 5)
        self.assertTrue(any(r.is_note and r.note['type'] == 'stop' for r in records))


class Followers(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / 'driver.log'

    def wait_for(self, condition):
        deadline = time.monotonic() + 5
        while not condition() and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertTrue(condition())

    def test_only_lines_added_after_the_start_count_and_rewrites_are_followed(self):
        self.path.write_text('old line\n')
        lines, stop = [], threading.Event()
        thread = threading.Thread(target=follow_file, args=(self.path, lines.append, stop), kwargs={'poll_s': 0.005})
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(stop.set)
        time.sleep(0.05)
        with open(self.path, 'a') as file:
            file.write('[E][libglasses] Failed to send USB command - MsgID: 0x0007\n')
        self.wait_for(lambda: len(lines) == 1)
        self.path.write_text('new\n')
        self.wait_for(lambda: len(lines) == 2)
        self.assertEqual(lines, ['[E][libglasses] Failed to send USB command - MsgID: 0x0007', 'new'])

    def test_a_log_that_appears_later_is_picked_up(self):
        lines, stop = [], threading.Event()
        thread = threading.Thread(target=follow_file, args=(self.path, lines.append, stop), kwargs={'poll_s': 0.005})
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(stop.set)
        time.sleep(0.03)
        self.path.write_text('')
        time.sleep(0.03)
        with open(self.path, 'a') as file:
            file.write('hello\n')
        self.wait_for(lambda: lines == ['hello'])

    def test_kernel_lines_keep_the_journal_time_and_only_usb_ones_pass(self):
        script = ("print('1762000000.123456 host kernel: usb 5-2: USB disconnect, device number 3');"
                  "print('1762000001.000000 host kernel: wlan0: link down');"
                  "print('1762000002.500000 host kernel: xhci_hcd 0000:12:00.4: ERROR');"
                  "print('-- Logs begin')")
        seen, stop = [], threading.Event()
        follow_kernel(lambda t_ns, text: seen.append((t_ns, text)), stop, command=(sys.executable, '-c', script))
        stop.set()
        self.assertEqual(seen, [(1762000000123456000, 'usb 5-2: USB disconnect, device number 3'),
                                (1762000002500000000, 'xhci_hcd 0000:12:00.4: ERROR')])

    def test_no_journalctl_is_reported_not_fatal(self):
        problems = []
        follow_kernel(lambda *_: None, threading.Event(), command=('/nonexistent/x',), on_error=problems.append)
        self.assertEqual(len(problems), 1)

    def test_a_mark_reaches_the_capture(self):
        rig = Rig(self)
        stop = recorder_module.start_followers(rig.recorder, self.path, kernel_command=('/nonexistent/x',))
        self.addCleanup(stop.set)
        time.sleep(0.1)     # the follower starts at the end of the file as it stands
        recorder_module.add_mark(rig.dir, 'pressed\nbrightness up')
        self.wait_for(lambda: bool(rig.notes('mark')))
        self.assertEqual(rig.notes('mark')[0]['text'], 'pressed brightness up')

    def test_a_follower_line_reaches_the_capture_as_a_note(self):
        rig = Rig(self)
        self.path.write_text('')
        stop = recorder_module.start_followers(rig.recorder, self.path,
                                               kernel_command=(sys.executable, '-c',
                                                               "print('1762000000.5 h kernel: usb 5-2: hello')"))
        self.addCleanup(stop.set)
        self.wait_for(lambda: bool(rig.notes('kernel')))
        time.sleep(0.05)
        with open(self.path, 'a') as file:
            file.write('driver says\n')
        self.wait_for(lambda: bool(rig.notes('driver_log')))
        self.assertEqual(rig.notes('driver_log')[0]['text'], 'driver says')


if __name__ == '__main__':
    unittest.main()
