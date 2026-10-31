"""Synthetic fixtures only; never query the host compositor or XR hardware."""
import os
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

from tools.xr_probe.commands import run_probe


class CommandTests(unittest.TestCase):
    def fixture_probe(self, code, **limits):
        """Inject only at process creation; exercise the real pipe reader."""
        real_popen = subprocess.Popen
        children = []

        def spawn(argv, **kwargs):
            self.assertFalse(kwargs.get('shell', False))
            child = real_popen([sys.executable, '-c', code], **kwargs)
            children.append(child)
            return child

        started = time.monotonic()
        with patch('subprocess.Popen', side_effect=spawn):
            result = run_probe(['uname', '-m'], **limits)
        self.assertLess(time.monotonic() - started, 3)
        for child in children:
            self.assertIsNotNone(child.poll(), 'fixture child not reaped')
            with self.assertRaises(ChildProcessError):
                os.waitpid(child.pid, os.WNOHANG)
        return result

    def test_bounded_reader_success_timeout_and_overflow(self):
        cases = [
            ("print('synthetic-machine')", {}, 'ok', 'synthetic-machine\n'),
            ("import time; time.sleep(30)", {'timeout_s': 0.1}, 'timeout', ''),
            ("import os; os.write(1, b'x' * 1000000)", {'max_bytes': 128}, 'truncated', 'x' * 128),
            ("import os; os.write(2, b'e' * 1000000)", {'max_bytes': 128}, 'truncated', ''),
            ("import sys; print('bad', file=sys.stderr); sys.exit(3)", {}, 'error', ''),
        ]
        for code, limits, status, data in cases:
            with self.subTest(status=status, code=code):
                result = self.fixture_probe(code, **limits)
                self.assertEqual(result.status, status)
                self.assertEqual(result.data, data)
                self.assertLessEqual(len(result.detail.encode()), limits.get('max_bytes', 65536))

    def test_allowlist_and_spawn_errors(self):
        allowed = [
            ['hyprctl', 'instances', '-j'],
            ['hyprctl', '-i', 'synthetic_123_456', 'version', '-j'],
            ['hyprctl', '-i', 'synthetic_123_456', 'monitors', 'all', '-j'],
            ['uname', '-m'], ['lspci'],
            ['pkg-config', '--modversion', 'wayland-client'],
        ]
        for argv in allowed:
            for error, status in [(FileNotFoundError(), 'missing'),
                                  (PermissionError(), 'permission_denied'),
                                  (OSError('synthetic failure'), 'error')]:
                with self.subTest(argv=argv, status=status):
                    with patch('subprocess.Popen', side_effect=error) as spawn:
                        self.assertEqual(run_probe(argv).status, status)
                        self.assertEqual(spawn.call_args.args[0], argv)
                        self.assertFalse(spawn.call_args.kwargs.get('shell', False))

    def test_reader_boundary_and_cleanup_regressions(self):
        for code, status in [
            ("import os; os.write(1, b'x' * 128); os.write(2, b'e' * 128)", 'ok'),
            ("import os; os.write(1, b'\\xff' * 128)", 'ok'),
            ("import os; os.close(1); os.close(2); import time; time.sleep(30)", 'timeout'),
            ("import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)", 'timeout'),
            ("import os; os.write(1, b'x' * 129)", 'truncated'),
        ]:
            with self.subTest(code=code):
                result = self.fixture_probe(code, max_bytes=128, timeout_s=0.2)
                self.assertEqual(result.status, status)
                self.assertLessEqual(len(result.data.encode()), 128)
                self.assertLessEqual(len(result.detail.encode()), 128)

    def test_mocked_nonzero_exit_is_not_ok(self):
        from unittest.mock import Mock
        real_popen = subprocess.Popen
        children = []

        def spawn(argv, **kwargs):
            child = real_popen([sys.executable, '-c', "print('synthetic')"], **kwargs)
            children.append(child)
            real_wait = child.wait

            def nonzero_wait(*args, **kw):
                real_wait(*args, **kw)
                return 7

            child.wait = Mock(side_effect=nonzero_wait)
            return child

        with patch('subprocess.Popen', side_effect=spawn):
            result = run_probe(['uname', '-m'])
        self.assertEqual(result.status, 'error')
        self.assertIn('7', result.detail)
        for child in children:
            with self.assertRaises(ChildProcessError):
                os.waitpid(child.pid, os.WNOHANG)

    def test_invalid_limits_never_spawn(self):
        for limits in ({'timeout_s': 0}, {'timeout_s': -1},
                       {'timeout_s': float('nan')}, {'timeout_s': float('inf')},
                       {'timeout_s': 'bad'}, {'timeout_s': True},
                       {'max_bytes': 0}, {'max_bytes': -1},
                       {'max_bytes': 1.5}, {'max_bytes': True}):
            with self.subTest(limits=limits), patch('subprocess.Popen') as spawn:
                self.assertEqual(run_probe(['uname', '-m'], **limits).status, 'error')
                spawn.assert_not_called()

    def test_models_are_frozen_and_serialize_nested_results(self):
        from dataclasses import FrozenInstanceError
        from tools.xr_probe import model
        import json
        self.assertTrue(hasattr(model, 'SessionCandidate'))
        self.assertTrue(hasattr(model, 'HostReport'))
        result = model.ProbeResult('missing', None, 'synthetic')
        candidate = model.SessionCandidate('synthetic_1_2', 123, None, [], 'unknown')
        report = model.HostReport(1, '2026-01-01T00:00:00Z', {},
                                  model.to_dict(candidate), result, result, {})
        for obj, field, value in ((result, 'status', 'ok'),
                                  (candidate, 'pid', 1), (report, 'schema_version', 2)):
            with self.assertRaises(FrozenInstanceError):
                setattr(obj, field, value)
        serialized = model.to_dict(report)
        self.assertEqual(serialized['usb'], {'status': 'missing', 'data': None,
                                            'detail': 'synthetic'})
        self.assertEqual(json.loads(model.to_json(report)), serialized)
        serialized['session']['outputs'].append({'name': 'synthetic'})
        self.assertEqual(candidate.outputs, [])

    def test_mutations_and_unrecognized_argv_never_spawn(self):
        rejected = [
            [], ['hyprctl', 'reload'], ['hyprctl', 'dispatch', 'exit'],
            ['hyprctl', 'keyword', 'monitor', 'bad'], ['hyprctl', 'plugin', 'list'],
            ['hyprctl', 'output', 'create', 'headless'], ['hyprctl', 'systeminfo'],
            ['hyprctl', 'version', '-j'], ['/usr/bin/uname', '-m'],
            ['uname', '-m', ';', 'touch', '/forbidden'],
            ['pkg-config', '--modversion', '--variable=prefix'],
            ['pkg-config', '--modversion', 'unknown-dependency'],
            ['hyprctl', '-i', '../socket', 'version', '-j'],
            ['hyprctl', '-i', '--batch', 'version', '-j'],
            ['hyprctl', '-i', 'fixture;touch nope', 'version', '-j'],
            ['hyprctl', '-i', 'fixture_1_2', 'monitors', 'all', '-j', 'reload'],
        ]
        with patch('subprocess.Popen') as spawn:
            for argv in rejected:
                with self.subTest(argv=argv):
                    self.assertEqual(run_probe(argv).status, 'error')
            spawn.assert_not_called()


if __name__ == '__main__':
    unittest.main()
