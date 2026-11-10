"""Drift fixtures (plan D6): what they keep, what they leave out, and how a recording is judged.

StoredFixtures checks the real recordings in rust/corpus/drift once they are there; until then it skips.
"""
import json
from pathlib import Path
import random
import struct
import tempfile
import unittest
from unittest import mock

from tools.luma_capture import capfile, drift, rawmon
from tools.luma_capture.__main__ import main

T0 = 1_794_000_000_000_000_000
MS = 1_000_000
CORPUS = Path(__file__).resolve().parents[2] / 'rust' / 'corpus' / 'drift'


def packet(seq, ticks, gyro=(0, 0, 0), accel=(0, 0, 8192), temp_c=40.0):
    return (rawmon.IMU_HEAD + struct.pack('<HHI3h3hI', seq & 0xffff, 16, ticks, *gyro, *accel,
                                          round((temp_c - 25) * 132.48)))


def recording(path, seconds, *, temp_from=40.0, temp_to=40.0, first_ticks=300, skip=(), bump_at=None, tilt_at=None,
              extra=()):
    """A still recording at 1 kHz, with noise, a temperature ramp and optionally a bump, a tilt or lost samples."""
    rng = random.Random(7)
    count = int(seconds * 1000)
    with capfile.Writer(path, start_ns=T0, bus=5) as writer:
        writer.write(capfile.note_record(T0, type='clock', realtime_ns=T0, monotonic_ns=1))
        writer.write(capfile.note_record(T0, type='start', bus=5, ring_bytes=1 << 24, uname='secret-host'))
        writer.write(capfile.note_record(T0, type='device_map', devices=[{'serial': 'SERIAL123'}], port='5-2'))
        writer.write(capfile.note_record(T0, type='mark', text='on the desk'))
        writer.write(capfile.Record(t_ns=T0, event='S', xfer=capfile.XFER_BULK, ep=0x04, bus=5, dev=13,
                                    flags=capfile.F_GLASSES, data=b'\xfa\x55\x95\x00'))
        writer.write(capfile.Record(t_ns=T0, event='C', xfer=capfile.XFER_BULK, ep=0x85, bus=5, dev=13,
                                    flags=capfile.F_GLASSES, data=b'\xfa\x55\xedSERIAL123'))
        for record in extra:
            writer.write(record)
        for i in range(count):
            if i in skip:
                continue
            t = T0 + i * MS
            gyro = [round(5 + rng.gauss(0, 2)) for _ in range(3)]
            if bump_at is not None and bump_at <= i < bump_at + 200:
                gyro[2] += 400            # about 24 °/s for 0.2 s
            accel = (0, 1430, 8065) if tilt_at is not None and i >= tilt_at else (0, 0, 8192)   # 10° roll
            accel = tuple(round(a + rng.gauss(0, 5)) for a in accel)
            temp = temp_from + (temp_to - temp_from) * i / count
            writer.write(capfile.Record(t_ns=t, event='S', xfer=capfile.XFER_INT, ep=0x81, urb_id=0xffff888100001000,
                                        bus=5, dev=13, flags=capfile.F_GLASSES, status=-115, urb_len=1024))
            writer.write(capfile.Record(t_ns=t, event='C', xfer=capfile.XFER_INT, ep=0x81, urb_id=0xffff888100001000,
                                        bus=5, dev=13, flags=capfile.F_GLASSES, urb_len=28,
                                        data=packet(i, first_ticks + i * 10, gyro, accel, temp)))
        writer.write(capfile.note_record(T0 + count * MS, type='driver_log', text='luma: calibrated /home/cody'))
        writer.write(capfile.note_record(T0 + count * MS, type='stop', reason='duration'))
    return path


class Base(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.dir = Path(folder.name)
        # A 20 s recording stands in for 20 minutes: same rules, a sixtieth of the time.
        patch = mock.patch.object(drift, 'MIN_SECONDS', 19)
        patch.start()
        self.addCleanup(patch.stop)

    def summary(self, **options):
        return drift.summarise([recording(self.dir / 'raw.lcap', options.pop('seconds', 20), **options)])


class Fixture(Base):
    def test_keeps_the_imu_stream_whole_and_nothing_identifying(self):
        raw = recording(self.dir / 'raw.lcap', 3)
        out = self.dir / 'desk-still.lcap.xz'
        self.assertEqual(drift.write_fixture([raw], out), 3000)
        kept = list(capfile.Reader(out))
        usb = [r for r in kept if not r.is_note]
        source = [r for r in capfile.Reader(raw) if r.replayable and r.ep == 0x81]
        self.assertEqual([r.data for r in usb], [r.data for r in source])
        self.assertEqual([r.t_ns for r in usb], [r.t_ns for r in source])
        self.assertTrue(all(r.replayable and r.urb_id == 0 for r in usb))
        self.assertEqual([r.note['type'] for r in kept if r.is_note], ['clock', 'mark', 'stop'])
        plain = self.dir / 'plain.lcap'
        drift.write_fixture([out], plain)
        for secret in (b'SERIAL123', b'secret-host', b'/home/cody', b'\x00\x10\x00\x81\x88\xff\xff'):
            self.assertNotIn(secret, plain.read_bytes())

    def test_compressed_and_plain_hold_the_same_records(self):
        raw = recording(self.dir / 'raw.lcap', 2)
        drift.write_fixture([raw], self.dir / 'a.lcap.xz')
        drift.write_fixture([raw], self.dir / 'a.lcap')
        self.assertEqual(list(capfile.Reader(self.dir / 'a.lcap.xz')), list(capfile.Reader(self.dir / 'a.lcap')))
        self.assertLess((self.dir / 'a.lcap.xz').stat().st_size, (self.dir / 'a.lcap').stat().st_size / 3)

    def test_a_cut_off_compressed_file_reads_as_truncated(self):
        drift.write_fixture([recording(self.dir / 'raw.lcap', 2)], self.dir / 'a.lcap.xz')
        cut = self.dir / 'cut.lcap.xz'
        cut.write_bytes((self.dir / 'a.lcap.xz').read_bytes()[:-200])
        reader = capfile.Reader(cut)
        self.assertLess(len(list(reader)), 2000 + 3)
        self.assertTrue(reader.truncated)

    def test_something_else_on_the_imu_endpoint_is_refused(self):
        odd = capfile.Record(t_ns=T0, event='C', xfer=capfile.XFER_INT, ep=0x81, bus=5, dev=13,
                             flags=capfile.F_GLASSES, data=b'\x01' * 28)
        with self.assertRaisesRegex(rawmon.RawError, 'not an IMU packet'):
            drift.write_fixture([recording(self.dir / 'raw.lcap', 1, extra=[odd])], self.dir / 'x.lcap')

    def test_no_imu_leaves_no_file(self):
        empty = self.dir / 'empty.lcap'
        capfile.Writer(empty, start_ns=T0, bus=5).close()
        with self.assertRaises(rawmon.RawError):
            drift.write_fixture([empty], self.dir / 'x.lcap')
        self.assertFalse((self.dir / 'x.lcap').exists())


class Judging(Base):
    def test_a_quiet_desk_still_passes_and_reports_its_span(self):
        s = self.summary(temp_from=44.0, temp_to=45.0)
        self.assertEqual(drift.problems(s, 'desk-still'), [])
        self.assertEqual((s.samples, s.lost), (20000, 0))
        self.assertAlmostEqual(s.temp_start_c, 44.0, delta=0.06)
        self.assertAlmostEqual(s.temp_end_c, 45.0, delta=0.06)
        self.assertAlmostEqual(s.temp_span_c, 1.0, delta=0.1)
        self.assertAlmostEqual(s.gyro_bias_start_dps[0], 5 / 16.4, delta=0.02)
        self.assertLess(s.tilt_max_deg, 0.3)
        self.assertLess(s.jolt_max_dps, 0.3)
        self.assertEqual(s.marks, ['on the desk'])

    def test_too_short(self):
        self.assertIn('needs 19 s', drift.problems(self.summary(seconds=10), 'desk-still')[0])

    def test_a_bump_is_caught_where_it_happened(self):
        s = self.summary(bump_at=12_000)
        found = drift.problems(s, 'desk-still')
        self.assertEqual(len(found), 1)
        self.assertIn('touched', found[0])
        self.assertAlmostEqual(s.jolt_at_s, 12.1, delta=0.2)

    def test_moving_the_glasses_is_caught(self):
        found = drift.problems(self.summary(tilt_at=15_000), 'desk-still')
        self.assertTrue(any('were moved' in p for p in found), found)

    def test_lost_samples_count_across_the_sequence_wrap(self):
        s = self.summary(seconds=70, skip=set(range(65_530, 65_545)))
        self.assertEqual(s.lost, 15)
        self.assertEqual(drift.problems(s, 'desk-still'), [])
        s = self.summary(skip=set(range(1000, 1100)))
        self.assertIn('100 samples lost', drift.problems(s, 'desk-still')[0])

    def test_a_disconnect_or_recorder_loss_fails(self):
        notes = [capfile.note_record(T0, type='glasses_gone'), capfile.note_record(T0, type='usbmon_stats',
                                                                                     dropped=9, since_last=9)]
        found = drift.problems(self.summary(extra=notes), 'desk-still')
        self.assertEqual(len(found), 2, found)

    def test_warm_up_must_start_cold_and_rise(self):
        self.assertEqual(drift.problems(self.summary(temp_from=31, temp_to=40), 'warm-up'), [])
        found = drift.problems(self.summary(temp_from=44, temp_to=45, first_ticks=3_600_000), 'warm-up')
        self.assertEqual(len(found), 2, found)
        self.assertIn('not a cold start', found[0])
        self.assertIn('not cold', found[1])

    def test_unknown_kind(self):
        with self.assertRaises(ValueError):
            drift.problems(drift.Summary(), 'walk')


class Command(Base):
    def test_drift_fixture_writes_the_sidecar_and_exit_code(self):
        raw = recording(self.dir / 'raw.lcap', 20, temp_from=31, temp_to=40)
        out = self.dir / 'warm-up.lcap.xz'
        with mock.patch('sys.stdout'):
            self.assertEqual(main(['drift-fixture', str(raw), '--kind', 'warm-up', '-o', str(out)]), 0)
        info = json.loads((self.dir / 'warm-up.json').read_text())
        self.assertEqual(info['problems'], [])
        self.assertEqual(info['summary']['samples'], 20000)
        self.assertAlmostEqual(info['temp_span_c'], 9.0 * 19 / 20, delta=0.05)   # 1 s means at each end
        self.assertEqual(info, drift.describe(out, drift.summarise([out]), 'warm-up'))
        with mock.patch.object(drift, 'MIN_SECONDS', 1200), mock.patch('sys.stdout'):
            self.assertEqual(main(['drift-summary', str(out), '--kind', 'warm-up']), 1)
            self.assertEqual(main(['drift-summary', str(out)]), 0)


@unittest.skipUnless(CORPUS.is_dir() and any(CORPUS.glob('*.json')), f'no drift recordings in {CORPUS} yet')
class StoredFixtures(unittest.TestCase):
    """The gate: both recordings are stored, unchanged, and each is what its kind must be."""

    def test_both_kinds_are_stored_and_pass(self):
        found = {json.loads(path.read_text())['kind']: path for path in CORPUS.glob('*.json')}
        self.assertEqual(set(found), set(drift.KINDS))
        for kind, path in found.items():
            with self.subTest(kind):
                info = json.loads(path.read_text())
                fixture = CORPUS / info['file']
                self.assertEqual(info, drift.describe(fixture, drift.summarise([fixture]), kind))
                self.assertEqual(info['problems'], [])
                self.assertGreater(info['temp_span_c'], 0)


if __name__ == '__main__':
    unittest.main()
