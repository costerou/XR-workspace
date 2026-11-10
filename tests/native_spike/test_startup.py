"""The start-up timeline (tools/native_spike/startup.py): marks, the summary kept in the result, the first frame read from
the renderer's timing report, and the report command. Offline: a saved journal, never journalctl."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import time
import unittest

from tools.native_spike import startup

MS = 1_000_000
# A Luma start as the runner records it (ns on CLOCK_MONOTONIC), and the renderer's exit report as the unit journal holds
# it (metrics.cpp Metrics::report, one line on stderr, `journalctl --output=cat`).
MARKS = {'confirmed': 1000 * MS, 'runner': 1180 * MS, 'verify': 1420 * MS, 'tracking': 1440 * MS, 'plugin': 3240 * MS,
         'desktops': 3300 * MS, 'renderer': 3900 * MS, 'first_status': 4950 * MS}
FIRST_FRAME = 4060 * MS + 123
TIMING = json.dumps({'abandoned': 1, 'clock_conflict': False, 'clock_id': None, 'discarded': 0,
                     'first_presented_ns': FIRST_FRAME, 'kind': 'generated_pattern_presentation_timing',
                     'last_presented_ns': FIRST_FRAME + 60 * 1000 * MS, 'presented': 7200, 'schema': 1}, separators=(',', ':'))
JOURNAL = ['head tracking driver: /pinned/xr-luma-driver', 'luma: calibrated', TIMING,
           '{"kind":"control_stream","accepted":12,"refused":0}',
           # a renderer started again after a USB drop: its report is not the start-up's
           TIMING.replace(str(FIRST_FRAME), str(FIRST_FRAME + 90 * 1000 * MS))]


class MarkTests(unittest.TestCase):
    def setUp(self):
        startup.reset(); self.addCleanup(startup.reset)

    def test_a_names_first_mark_wins(self):
        startup.mark('renderer', 5); startup.mark('renderer', 9); startup.mark('first_status')
        self.assertEqual(startup.marks()['renderer'], 5)
        self.assertGreater(startup.marks()['first_status'], 0)
        startup.reset()
        self.assertEqual(startup.marks(), {})

    def test_a_files_write_time_moves_onto_the_monotonic_clock(self):
        with tempfile.NamedTemporaryFile() as f:
            os.utime(f.name, ns=(time.time_ns() - 2000 * MS,) * 2)
            ago = time.monotonic_ns() - startup.written_ns(f.name)
        self.assertAlmostEqual(ago / MS, 2000, delta=50)
        self.assertIsNone(startup.written_ns('/nonexistent/consent.json'))


class SummaryTests(unittest.TestCase):
    def test_each_phase_lasts_until_the_next_mark(self):
        record = startup.summary(MARKS)
        self.assertEqual(record['phases_ms'], {'confirmed': 180.0, 'runner': 240.0, 'verify': 20.0, 'tracking': 1800.0,
                                               'plugin': 60.0, 'desktops': 600.0, 'renderer': 1050.0})
        self.assertEqual((record['confirm_to_first_frame_ms'], record['first_frame_from']), (3950.0, 'first_status'))

    def test_without_a_confirm_there_is_no_total(self):
        record = startup.summary({k: v for k, v in MARKS.items() if k != 'confirmed'})
        self.assertNotIn('confirm_to_first_frame_ms', record)
        self.assertEqual(startup.summary({})['phases_ms'], {})

    def test_the_journal_gives_the_exact_first_frame(self):
        self.assertEqual(startup.first_presented_ns(JOURNAL), FIRST_FRAME)
        record = startup.with_first_frame({'startup': startup.summary(MARKS)}, JOURNAL)
        self.assertEqual(record['first_frame_from'], 'first_frame')
        self.assertEqual(record['confirm_to_first_frame_ms'], 3060.0)
        self.assertEqual(record['phases_ms']['renderer'], 160.0)  # the spawn to its first frame, then up to the status
        self.assertLess(record['marks_ns']['first_frame'], record['marks_ns']['first_status'])

    def test_no_valid_frame_or_no_report_leaves_the_upper_bound(self):
        for lines in ([], ['luma: calibrated'], [TIMING.replace(str(FIRST_FRAME), 'null')], ['{"kind":"generated_pattern_presentation_timing"']):
            self.assertIsNone(startup.first_presented_ns(lines), lines)
            self.assertEqual(startup.with_first_frame({'startup': startup.summary(MARKS)}, lines)['first_frame_from'], 'first_status')
        early = [TIMING.replace(str(FIRST_FRAME), str(MARKS['renderer'] - 1))]  # another run's clock: never before the spawn
        self.assertEqual(startup.with_first_frame({'startup': startup.summary(MARKS)}, early)['first_frame_from'], 'first_status')


class CommandTests(unittest.TestCase):
    def run_main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err): code = startup.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_it_prints_a_finished_runs_phases_with_the_journals_first_frame(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, journal = Path(tmp) / 'run1.result.json', Path(tmp) / 'journal.txt'
            result.write_text(json.dumps({'run_id': 'run1', 'phase': 'done', 'startup': startup.summary(MARKS)}))
            journal.write_text('\n'.join(JOURNAL) + '\n')
            code, out, _ = self.run_main(str(result), '--journal-file', str(journal))
            self.assertEqual(code, 0)
            self.assertIn('tracking          1800.0 ms', out)
            self.assertIn('confirm to first frame   3060.0 ms (first_frame)', out)
            code, out, _ = self.run_main(str(result), '--json')
            self.assertEqual(json.loads(out)['first_frame_from'], 'first_status')
            result.write_text(json.dumps({'run_id': 'run1', 'phase': 'done'}))
            code, _, err = self.run_main(str(result))
            self.assertEqual(code, 2); self.assertIn('no start-up record', err)


if __name__ == '__main__':
    unittest.main()
