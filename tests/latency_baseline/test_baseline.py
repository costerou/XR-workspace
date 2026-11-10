"""tools.latency_baseline over a real run's journal (a 9 s Pro 2 run, 2026-11-10, host name and launch line removed)
and variants of it. The renderer's exit report format is the same for every glasses; only the consent says Luma."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import time
import unittest

from tools.latency_baseline import baseline as b
from tools.latency_baseline.__main__ import main

FIXTURE = Path(__file__).parent / 'fixtures' / 'pro2_run_2026-11-10.log'
RUN = 'c322d6e5465643a84c3287ac02014aaa'
LUMA = {'run_id': RUN, 'tracking': 'luma'}


def lines():
    return FIXTURE.read_text().splitlines()


def edited(edit):
    """The fixture with `edit(report)` applied to each exit report's JSON."""
    out = []
    for line in lines():
        head, brace, body = line.partition(': {')
        if brace and b.exit_report(line) is not None:
            report = json.loads('{' + body)
            edit(report)
            line = head + ': ' + json.dumps(report, separators=(',', ':'))
        out.append(line)
    return out


def long_run(report, seconds=1800.5):
    if report['kind'] == b.TIMING: report['observation_duration_ns'] = int(seconds * 1e9)


class RealJournalTests(unittest.TestCase):
    def test_reads_the_real_exit_reports(self):
        r = b.baseline(lines(), RUN, {'tracking': 'pro2-sdk'}, {'stop_reason': 'guard-failed'}, min_duration_s=5, require_tracking='pro2-sdk')
        self.assertEqual(r['verdict'], 'PASS', r['reasons'])
        self.assertEqual((r['lives'], r['duration_s'], r['presented'], r['discarded'], r['presented_hz']), (1, 8.942, 1019, 0, 113.847))
        self.assertTrue(r['clock_monotonic'])
        self.assertEqual(r['read_to_present_ms'], dict(count=1019, p50_ms=16.125, p95_ms=16.625, mean_ms=16.042306, max_ms=79.531814, dropped=2))
        self.assertEqual(r['pose_age_ms'], dict(basis='relative', count=1021, p50_ms=5.625, p95_ms=11.125, mean_ms=5.830877, max_ms=15.044265))
        self.assertEqual(r['stop_reason'], 'guard-failed')

    def test_a_short_run_or_other_glasses_is_not_a_luma_baseline(self):
        r = b.baseline(lines(), RUN, {'tracking': 'pro2-sdk'})
        self.assertEqual(r['verdict'], 'INCONCLUSIVE')
        self.assertEqual(len(r['reasons']), 2)
        self.assertIn("not 'luma'", r['reasons'][0])
        self.assertIn('ran 9 s, under 1800 s', r['reasons'][1])
        self.assertEqual(r['pose_age_ms']['p50_ms'], 5.625)  # still reported, just not accepted

    def test_a_30_minute_luma_run_passes(self):
        r = b.baseline(edited(long_run), RUN, LUMA)
        self.assertEqual((r['verdict'], r['tracking'], r['duration_s']), ('PASS', 'luma', 1800.5))
        self.assertEqual(r['read_to_present_ms']['p95_ms'], 16.625)
        self.assertEqual(r['pose_age_ms']['p95_ms'], 11.125)


class EdgeTests(unittest.TestCase):
    def test_no_exit_report(self):
        r = b.baseline([l for l in lines() if b.exit_report(l) is None], RUN, LUMA)
        self.assertEqual(r['verdict'], 'INCONCLUSIVE')
        self.assertIn('no renderer exit report', r['reasons'][0])

    def test_the_longest_renderer_life_is_the_measurement(self):
        short = lines()
        def p50(report):
            long_run(report)
            if report['kind'] == b.CAPTURE: report['latency']['read_to_present']['p50_ms'] = 9.0
        r = b.baseline(short + edited(p50) + short, RUN, LUMA)
        self.assertEqual((r['verdict'], r['lives'], r['other_lives_s']), ('PASS', 3, [8.942, 8.942]))
        self.assertEqual(r['read_to_present_ms']['p50_ms'], 9.0)

    def test_a_foreign_clock_is_refused(self):
        def clock(report):
            long_run(report)
            if report['kind'] == b.CAPTURE: report['latency']['clock_monotonic'] = False
        r = b.baseline(edited(clock), RUN, LUMA)
        self.assertEqual(r['verdict'], 'INCONCLUSIVE')
        self.assertIn('CLOCK_MONOTONIC', r['reasons'][0])

    def test_no_pose_samples_is_refused(self):
        def untracked(report):
            long_run(report)
            if report['kind'] == b.CAPTURE: report['latency']['pose_age_relative'] = dict(count=0, dropped=0, mean_ms=0, p50_ms=0, p95_ms=0, max_ms=0)
        r = b.baseline(edited(untracked), RUN, LUMA)
        self.assertEqual(r['reasons'], ['no pose age samples (no pose feed, or tracking never started)'])

    def test_a_report_with_no_latency_does_not_crash(self):
        def strip(report):
            long_run(report)
            report.pop('latency', None)
        r = b.baseline(edited(strip), RUN, LUMA)
        self.assertEqual(r['verdict'], 'INCONCLUSIVE')
        self.assertEqual(r['read_to_present_ms']['count'], None)


class CliTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def run_main(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = main(['--state-dir', str(self.dir), *argv])
        return code, out.getvalue()

    def write_run(self, run_id, tracking, age_s=0):
        (self.dir / f'{run_id}.json').write_text(json.dumps({'run_id': run_id, 'tracking': tracking}))
        result = self.dir / f'{run_id}.result.json'
        result.write_text(json.dumps({'run_id': run_id, 'stop_reason': 'user'}))
        t = time.time() - age_s
        os.utime(result, (t, t))

    def test_records_a_luma_run_from_a_saved_log(self):
        self.write_run(RUN, 'luma')
        log, out = self.dir / 'run.log', self.dir / 'record.json'
        log.write_text('\n'.join(edited(long_run)) + '\n')
        code, text = self.run_main('--run', RUN, '--log', str(log), '--out', str(out))
        self.assertEqual(code, 0)
        record = json.loads(out.read_text())
        self.assertEqual(record, json.loads(text))
        self.assertEqual((record['verdict'], record['stop_reason'], record['pose_age_ms']['p50_ms']), ('PASS', 'user', 5.625))

    def test_inconclusive_exits_2(self):
        self.write_run(RUN, 'luma')
        log = self.dir / 'run.log'
        log.write_text(FIXTURE.read_text())
        self.assertEqual(self.run_main('--run', RUN, '--log', str(log))[0], 2)

    def test_latest_picks_the_newest_luma_run(self):
        self.write_run('a' * 32, 'luma', age_s=100)
        self.write_run('b' * 32, 'luma', age_s=50)
        self.write_run('c' * 32, 'pro2-sdk', age_s=0)
        self.assertEqual(b.latest_run(self.dir), 'b' * 32)
        self.assertIsNone(b.latest_run(self.dir, 'xreal'))

    def test_a_bad_run_id_is_refused(self):
        self.assertEqual(self.run_main('--run', '../x')[0], 1)


if __name__ == '__main__':
    unittest.main()
