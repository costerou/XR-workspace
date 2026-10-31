"""Synthetic fixtures only; no live compositor access in this suite."""
import json
import unittest

from tools.xr_probe.model import ProbeResult, SessionCandidate
from tools.xr_probe import session


def instance(name='synthetic_a', **changes):
    return dict(instance=name, pid=42, wl_socket='wayland-1', **changes)


def output(name='DP-1', description='Synthetic panel', **changes):
    return dict(name=name, description=description, make='Synthetic', model='Panel',
                width=1920, height=1080, refreshRate=120.0, disabled=False,
                **changes)


def candidate(name='synthetic_a', kind='physical'):
    return SessionCandidate(name, 42, 'wayland-1', [], kind)


class SessionTests(unittest.TestCase):
    def discover(self, instances=None, outputs=None):
        calls = []
        def run(argv):
            calls.append(argv)
            if argv == ['hyprctl', 'instances', '-j']:
                return ProbeResult('ok', json.dumps(instances if instances is not None else [instance()]), '')
            return ProbeResult('ok', json.dumps(outputs if outputs is not None else [output()]), '')
        return session.discover_sessions(run), calls

    def test_ambiguous_physical_sessions_require_selection(self):
        result = session.select_session([candidate(), candidate('synthetic_b')], None)
        self.assertEqual(result.status, 'error')
        self.assertIsNone(result.data)
        self.assertIn('ambiguous', result.detail.lower())

    def test_single_physical_selected(self):
        expected = candidate()
        result = session.select_session([candidate('headless', 'headless_only'), expected], None)
        self.assertEqual(result.status, 'ok')
        self.assertIs(result.data, expected)

    def test_requested_physical_selected_exactly(self):
        expected = candidate('synthetic_b')
        result = session.select_session([candidate(), expected], 'synthetic_b')
        self.assertEqual(result.status, 'ok')
        self.assertIs(result.data, expected)

    def test_headless_is_not_physical(self):
        result, _ = self.discover(outputs=[output('HEADLESS-1', 'Headless output')])
        self.assertEqual(result.status, 'ok')
        self.assertEqual(result.data[0].classification, 'headless_only')
        for requested in (None, 'synthetic_a'):
            selected = session.select_session(result.data, requested)
            self.assertEqual(selected.status, 'error')
            self.assertIsNone(selected.data)
            self.assertIn('physical', selected.detail.lower())

    def test_missing_requested_instance(self):
        result = session.select_session([candidate()], 'absent')
        self.assertEqual(result.status, 'error')
        self.assertIsNone(result.data)
        self.assertIn('not found', result.detail.lower())

    def test_explicit_instance_commands_and_sanitized_outputs(self):
        result, calls = self.discover(
            instances=[instance(), instance('synthetic_b')],
            outputs=[output(activeWorkspace={'name': 'PRIVATE'}, serial='PRIVATE', solitary='PRIVATE')])
        self.assertEqual(calls, [['hyprctl', 'instances', '-j'],
                                 ['hyprctl', '-i', 'synthetic_a', 'monitors', 'all', '-j'],
                                 ['hyprctl', '-i', 'synthetic_b', 'monitors', 'all', '-j']])
        self.assertEqual(result.status, 'ok')
        self.assertEqual(len(result.data), 2)
        self.assertIsInstance(result.data[0], SessionCandidate)
        self.assertEqual(result.data[0].classification, 'physical')
        self.assertEqual(result.data[0].outputs, [dict(name='DP-1', make='Synthetic', model='Panel',
                           width=1920, height=1080, refreshRate=120.0, disabled=False, classification='physical')])
        self.assertEqual(result.data[0].wayland_socket, 'wayland-1')

    def test_malformed_instances_json(self):
        for raw in ('{', '{}', 'null', '[1]', '[{}]', '[{"instance":"x","pid":true}]'):
            with self.subTest(raw=raw):
                result = session.discover_sessions(lambda argv: ProbeResult('ok', raw, ''))
                self.assertEqual(result.status, 'error')
                self.assertIsNone(result.data)

    def test_invalid_instance_fields_prevent_output_probe(self):
        invalid = [dict(instance='-1', pid=42), dict(instance='0', pid=42),
                   dict(instance='bad/path', pid=42), dict(instance='valid', pid=0),
                   dict(instance='valid', pid='42'), dict(instance='valid', pid=42, wl_socket=1)]
        for entry in invalid:
            with self.subTest(entry=entry):
                result, calls = self.discover(instances=[instance(), entry])
                self.assertEqual(result.status, 'error')
                self.assertEqual(calls, [['hyprctl', 'instances', '-j']])

    def test_duplicate_instances_rejected(self):
        result, calls = self.discover(instances=[instance(), instance()])
        self.assertEqual(result.status, 'error')
        self.assertEqual(len(calls), 1)
        self.assertEqual(session.select_session([candidate(), candidate()], 'synthetic_a').status, 'error')

    def test_optional_socket_and_empty_discovery(self):
        result, _ = self.discover(instances=[dict(instance='valid', pid=42)])
        self.assertEqual(result.status, 'ok')
        self.assertIsNone(result.data[0].wayland_socket)
        result, calls = self.discover(instances=[])
        self.assertEqual(result, ProbeResult('ok', [], ''))
        self.assertEqual(len(calls), 1)
        self.assertEqual(session.select_session([], None).status, 'error')

    def test_output_probe_permission_denied(self):
        def run(argv):
            if argv == ['hyprctl', 'instances', '-j']:
                return ProbeResult('ok', json.dumps([instance()]), '')
            return ProbeResult('permission_denied', 'not JSON', 'denied')
        result = session.discover_sessions(run)
        self.assertEqual(result.status, 'permission_denied')
        self.assertIsNone(result.data)
        self.assertIn('synthetic_a', result.detail)

    def test_failed_probes_never_parse_or_expose_partial_stdout(self):
        for status in ('missing', 'permission_denied', 'timeout', 'error', 'truncated'):
            for failing_stage in ('instances', 'monitors'):
                with self.subTest(status=status, stage=failing_stage):
                    def run(argv):
                        if failing_stage == 'instances' or 'monitors' in argv:
                            return ProbeResult(status, 'PRIVATE partial', 'failure')
                        return ProbeResult('ok', json.dumps([instance()]), '')
                    result = session.discover_sessions(run)
                    self.assertEqual(result.status, status)
                    self.assertIsNone(result.data)
                    self.assertNotIn('PRIVATE', result.detail)

    def test_huge_refresh_returns_error_without_partial_candidates(self):
        huge = output()
        huge['refreshRate'] = 10**400
        try:
            result, _ = self.discover(outputs=[output(), huge])
        except OverflowError:
            self.fail('huge JSON refresh escaped discovery result contract')
        self.assertEqual(result.status, 'error')
        self.assertIsNone(result.data)
        self.assertIn('Malformed monitor', result.detail)

    def test_malformed_outputs(self):
        for outputs in ({}, [1], [{}], [dict(name=42)], [dict(name='DP-1', description=4)],
                        [dict(name='DP-1', width=True)], [dict(name='DP-1', refreshRate=float('nan'))],
                        [dict(name='DP-1', disabled='false')]):
            with self.subTest(outputs=outputs):
                result, _ = self.discover(outputs=outputs)
                self.assertEqual(result.status, 'error')
                self.assertIsNone(result.data)

    def test_mixed_output_classification_survives_privacy_filter(self):
        result, _ = self.discover(outputs=[output(), output('HEADLESS-1'),
                                          output('DP-2', 'Headless PRIVATE')])
        self.assertEqual(result.data[0].classification, 'physical')
        self.assertEqual([o.get('classification') for o in result.data[0].outputs],
                         ['physical', 'headless_only', 'unknown'])
        self.assertNotIn('PRIVATE', repr(result.data[0].outputs))
        self.assertNotIn('description', repr(result.data[0].outputs))

    def test_conservative_classification(self):
        cases = [([], 'unknown'), ([output('mystery')], 'unknown'),
                 ([output('WL-1', 'Wayland output')], 'unknown'),
                 ([output('DP-1', 'Headless output')], 'unknown'),
                 ([output('HDMI-A-1')], 'physical'), ([output('eDP-1')], 'physical'),
                 ([output('HEADLESS-1'), output('mystery')], 'unknown'),
                 ([output('HEADLESS-1'), output()], 'physical')]
        for outputs, expected in cases:
            with self.subTest(outputs=outputs):
                result, _ = self.discover(outputs=outputs)
                self.assertEqual(result.status, 'ok')
                self.assertEqual(result.data[0].classification, expected)

    def test_unknown_never_selected_even_explicitly(self):
        unknown = candidate('uncertain', 'unknown')
        for candidates, request in (([unknown], None), ([unknown], 'uncertain'),
                                    ([candidate(), unknown], None)):
            with self.subTest(request=request, candidates=candidates):
                result = session.select_session(candidates, request)
                self.assertEqual(result.status, 'error')
                self.assertIsNone(result.data)


if __name__ == '__main__':
    unittest.main()
