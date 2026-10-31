"""Synthetic collectors only; no fixtures are published as host evidence."""
import contextlib
from datetime import datetime, timezone
import importlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.xr_probe.model import ProbeResult, SessionCandidate


class CliTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('tools.xr_probe.__main__'),
                             'Executable CLI has not been implemented')
        self.cli = importlib.import_module('tools.xr_probe.__main__')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.output = self.root / 'reports'
        self.candidate = SessionCandidate('synthetic-instance', 123, None,
            [{'name': 'DP-1', 'width': 1920, 'height': 1080,
              'refreshRate': 60, 'disabled': False}], 'physical')
        self.kw = dict(discover=lambda: ProbeResult('ok', [self.candidate], ''),
                       usb=lambda: ProbeResult('ok', [], ''),
                       drm=lambda: ProbeResult('ok', [], ''),
                       host=lambda instance: {'architecture': 'synthetic'})
        self.stdout, self.stderr = io.StringIO(), io.StringIO()

    def run_cli(self, *args):
        with contextlib.redirect_stdout(self.stdout), contextlib.redirect_stderr(self.stderr):
            return self.cli.main(list(args), **self.kw)

    def collect(self, *args):
        return self.run_cli('--output-dir', str(self.output), *args)

    def test_missing_glasses_schema_and_real_utc_clock(self):
        before = datetime.now(timezone.utc)
        self.assertEqual(self.collect(), 0)
        data = json.loads((self.output / 'host-report.json').read_text())
        self.assertEqual(data['schema_version'], 1)
        self.assertEqual(data['usb'], {'status': 'ok', 'data': [], 'detail': ''})
        self.assertEqual(data['session']['classification'], 'physical')
        self.assertTrue(all(v['state'] == 'untested' for v in data['capabilities'].values()))
        self.assertLessEqual(before, datetime.fromisoformat(data['collected_at']))
        self.assertLessEqual(datetime.fromisoformat(data['collected_at']), datetime.now(timezone.utc))
        self.assertIn('tracking remain untested', (self.output / 'host-report.md').read_text())
        self.assertIn('No matching VITURE USB', self.stdout.getvalue())

    def test_ambiguous_sessions_require_selection(self):
        other = SessionCandidate('other', 456, None, [], 'physical')
        self.kw['discover'] = lambda: ProbeResult('ok', [self.candidate, other], '')
        self.assertEqual(self.collect(), 2)
        self.assertFalse(self.output.exists())
        self.assertEqual(self.collect('--instance', 'synthetic-instance'), 0)

    def test_bad_directory(self):
        self.output.write_text('not a directory')
        self.assertEqual(self.collect(), 3)
        self.assertEqual(self.output.read_text(), 'not a directory')

    def test_protect_either_existing_report_and_explicit_overwrite(self):
        self.output.mkdir()
        md = self.output / 'host-report.md'
        md.write_text('prior evidence')
        self.assertEqual(self.collect(), 2)
        self.assertEqual(md.read_text(), 'prior evidence')
        self.assertFalse((self.output / 'host-report.json').exists())
        self.assertEqual(self.collect('--overwrite'), 0)
        self.assertNotEqual(md.read_text(), 'prior evidence')

    def test_second_replace_error_rolls_back_both_files(self):
        self.assertEqual(self.collect(), 0)
        paths = [self.output / ('host-report.' + ext) for ext in ('json', 'md')]
        old = [p.read_bytes() for p in paths]
        real_replace = os.replace
        calls = 0
        def fail_second(src, dst):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError('synthetic write failure')
            return real_replace(src, dst)
        with patch.object(self.cli.os, 'replace', side_effect=fail_second):
            self.assertEqual(self.collect('--overwrite'), 3)
        self.assertEqual([p.read_bytes() for p in paths], old)
        self.assertEqual(sorted(p.name for p in self.output.iterdir()), sorted(p.name for p in paths))

    def test_new_pair_second_replace_failure_removes_partial_report(self):
        real_replace = os.replace
        def fail_md(src, dst):
            if Path(dst).suffix == '.md':
                raise OSError('synthetic write failure')
            return real_replace(src, dst)
        with patch.object(self.cli.os, 'replace', side_effect=fail_md):
            self.assertEqual(self.collect(), 3)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_staging_failure_preserves_prior_pair(self):
        self.assertEqual(self.collect(), 0)
        paths = [self.output / ('host-report.' + ext) for ext in ('json', 'md')]
        old = [p.read_bytes() for p in paths]
        with patch.object(self.cli.os, 'fsync', side_effect=OSError('synthetic disk failure')):
            self.assertEqual(self.collect('--overwrite'), 3)
        self.assertEqual([p.read_bytes() for p in paths], old)
        self.assertFalse((self.output / '.xr-probe.lock').exists())

    def test_failed_rollback_preserves_recoverable_prior_evidence(self):
        self.assertEqual(self.collect(), 0)
        paths = [self.output / ('host-report.' + ext) for ext in ('json', 'md')]
        old = [p.read_bytes() for p in paths]
        real_replace = os.replace
        calls = 0
        def fail_after_first(src, dst):
            nonlocal calls
            calls += 1
            if calls > 1:
                raise OSError('synthetic persistent rename failure')
            return real_replace(src, dst)
        with patch.object(self.cli.os, 'replace', side_effect=fail_after_first):
            self.assertEqual(self.collect('--overwrite'), 3)
        lock = self.output / '.xr-probe.lock'
        stage, = lock.iterdir()
        self.assertEqual([(stage / f'old-{i}').read_bytes() for i in range(2)], old)
        self.assertEqual(self.collect('--overwrite'), 2)
        self.assertIn('recovery', self.stderr.getvalue())

    def test_collection_failures_nonzero_no_raw_error_leak(self):
        self.kw['discover'] = lambda: ProbeResult('timeout', None, 'PRIVATE')
        self.assertEqual(self.collect(), 3)
        self.assertFalse(self.output.exists())
        self.assertNotIn('PRIVATE', self.stderr.getvalue())

    def test_huge_refresh_stops_at_discovery_before_selection_or_report(self):
        from tools.xr_probe.session import discover_sessions
        def run(argv):
            rows = ([{'instance': 'synthetic-instance', 'pid': 123}]
                    if argv == ['hyprctl', 'instances', '-j'] else
                    [{'name': 'DP-1', 'refreshRate': 120},
                     {'name': 'DP-2', 'refreshRate': 10**400}])
            return ProbeResult('ok', json.dumps(rows), '')
        self.kw['discover'] = lambda: discover_sessions(run)
        def forbidden(*args):
            self.fail('failed discovery must not select or collect capabilities')
        self.kw.update(usb=forbidden, drm=forbidden, host=forbidden)
        with patch.object(self.cli, 'select_session', side_effect=forbidden):
            self.assertEqual(self.collect(), 3)
        self.assertIn('Session discovery failed: error', self.stderr.getvalue())
        self.assertFalse(self.output.exists())
        self.assertEqual(self.stdout.getvalue(), '')

    def test_inventory_failure_writes_honest_partial_report_exit_three(self):
        self.kw['usb'] = lambda: ProbeResult('permission_denied', None, 'PRIVATE')
        self.assertEqual(self.collect(), 3)
        text = (self.output / 'host-report.json').read_text()
        self.assertEqual(json.loads(text)['usb']['status'], 'permission_denied')
        self.assertNotIn('PRIVATE', text)

    def test_list_only_no_hardware_or_host_collection(self):
        def forbidden(*args):
            self.fail('list must not collect hardware')
        self.kw.update(usb=forbidden, drm=forbidden, host=forbidden)
        self.assertEqual(self.run_cli('--list-sessions'), 0)
        rows = json.loads(self.stdout.getvalue())
        self.assertEqual(rows, [{'instance': 'synthetic-instance', 'classification': 'physical'}])
        self.assertFalse(self.output.exists())

    def test_invalid_arguments_exit_two(self):
        for args in [[], ['--list-sessions', '--output-dir', str(self.output)],
                     ['--list-sessions', '--instance', 'x'], ['--list-sessions', '--overwrite']]:
            with self.subTest(args=args):
                self.assertEqual(self.run_cli(*args), 2)

    def test_symlink_report_refused_even_with_overwrite(self):
        self.output.mkdir()
        victim = self.root / 'victim'
        victim.write_text('keep')
        (self.output / 'host-report.json').symlink_to(victim)
        self.assertEqual(self.collect('--overwrite'), 2)
        self.assertEqual(victim.read_text(), 'keep')

    def test_host_allowlisted_queries_and_optional_unknowns(self):
        seen = []
        def run(argv):
            seen.append(argv)
            responses = {'uname': 'x86_64\n', 'lspci': '00:00 Other device\n01:00 VGA compatible controller: Synthetic GPU\n',
                         'hyprctl': '{"version":"synthetic-version"}', 'pkg-config': 'synthetic-libdrm\n'}
            return ProbeResult('ok', responses[argv[0]], '')
        host = self.cli.collect_host('synthetic-instance', run=run)
        self.assertEqual(host['architecture'], 'x86_64')
        self.assertEqual(host['gpu'], '01:00 VGA compatible controller: Synthetic GPU')
        self.assertEqual(host['packages'], {'hyprland': 'synthetic-version', 'libdrm': 'synthetic-libdrm'})
        self.assertEqual(seen, [['uname', '-m'], ['lspci'], ['hyprctl', '-i', 'synthetic-instance', 'version', '-j'], ['pkg-config', '--modversion', 'libdrm']])
        self.assertEqual(self.cli.collect_host('synthetic-instance', run=lambda argv: ProbeResult('missing', None, 'PRIVATE')), {'packages': {}})
