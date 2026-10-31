"""Synthetic report inputs, not observations of this host."""
from datetime import datetime, timezone
import json
import unittest
from tools.xr_probe.model import ProbeResult, SessionCandidate
from tools.xr_probe import report, session


class ReportTests(unittest.TestCase):
    def build(self, outputs=None, host=None, session=None, usb=None, drm=None):
        return report.build_report(
            session or ProbeResult('ok', SessionCandidate('fixture', 1, None, outputs or [], 'physical'), ''),
            usb or ProbeResult('ok', [], ''), drm or ProbeResult('ok', [], ''), host or {},
            collected_at=datetime.now(timezone.utc).isoformat())

    def test_mixed_session_nonphysical_outputs_are_inventory_not_evidence(self):
        for other in ({'name': 'HEADLESS-1'}, {'name': 'WL-1'},
                      {'name': 'DP-2', 'description': 'Headless PRIVATE_DESCRIPTION'}):
            with self.subTest(other=other):
                outputs = [{'name': 'DP-1', 'refreshRate': 60, 'disabled': False},
                           {**other, 'refreshRate': 120, 'disabled': False}]
                def run(argv):
                    data = ([{'instance': 'fixture', 'pid': 1}] if 'instances' in argv else outputs)
                    return ProbeResult('ok', json.dumps(data), '')
                discovered = session.discover_sessions(run)
                selected = session.select_session(discovered.data, None)
                # Structured future advertisement must obey the same output boundary.
                selected.data.outputs[1]['available_modes'] = [{'refreshRate': 120}]
                result = self.build(session=selected)
                self.assertEqual(result.session['status'], 'ok')
                self.assertEqual(len(result.session['outputs']), 2)
                for capability in ('active_120_hz', 'advertised_120_hz'):
                    with self.subTest(capability=capability):
                        self.assertEqual(result.capabilities[capability]['state'], 'untested')
                        self.assertEqual(result.capabilities[capability]['evidence'], [])
                self.assertNotIn('PRIVATE_DESCRIPTION', report.to_json(result))

    def test_unicode_line_separators_are_single_line(self):
        result = self.build([{'name': 'one\u2028# heading\u2029two'}])
        self.assertNotIn('\u2028', report.to_json(result))
        self.assertNotIn('\u2029', report.to_json(result))

    def test_nonphysical_candidate_cannot_establish_active_capability(self):
        candidate = SessionCandidate('fixture', 1, None, [{'refreshRate': 120, 'disabled': False}], 'headless_only')
        result = self.build(session=ProbeResult('ok', candidate, ''))
        self.assertEqual(result.session['status'], 'error')
        self.assertEqual(result.capabilities['active_120_hz']['state'], 'untested')

    def test_advertisement_never_proves_active_or_sustained(self):
        result = self.build([{'name': 'DP-1', 'available_modes': [{'width': 1920, 'height': 1080, 'refreshRate': 120}]}])
        self.assertEqual(result.capabilities['advertised_120_hz']['state'], 'advertised')
        self.assertEqual(result.capabilities['active_120_hz']['state'], 'untested')
        self.assertEqual(result.capabilities['sustained_120_hz']['state'], 'untested')
        self.assertEqual(result.capabilities['tracking_quality']['state'], 'untested')
        self.assertIn('available_modes', result.capabilities['advertised_120_hz']['evidence'][0])

    def test_allowlist_privacy_and_text_safety(self):
        hostile = '\n# Forged\x1b[31m\r</pre><script>bad</script>'
        secret = {'serial': 'SERIAL_SECRET', 'window_title': 'WINDOW_SECRET', 'environment': {'TOKEN': 'ENV_SECRET'}}
        field = {'value': hostile, 'status': 'ok', 'source': '/synthetic/product', **secret}
        result = self.build([{'name': hostile, **secret}],
                            {'architecture': 'x86_64', 'gpu': hostile,
                             'packages': {'hyprland': '1.2', 'TOKEN': 'PACKAGE_SECRET'}, **secret},
                            usb=ProbeResult('ok', [{'product': field, **secret}], 'DETAIL_SECRET'),
                            drm=ProbeResult('ok', [{'name': hostile, **secret}], 'DETAIL_SECRET'))
        text = report.to_json(result)
        for banned in ['SERIAL_SECRET', 'WINDOW_SECRET', 'ENV_SECRET', 'PACKAGE_SECRET', 'DETAIL_SECRET', 'window_title', 'serial']:
            self.assertNotIn(banned, text)
        self.assertEqual(result.host['architecture']['value'], 'x86_64')
        self.assertEqual(result.host['packages']['hyprland']['value'], '1.2')
        md = report.render_markdown(result)
        self.assertNotIn('\x1b', md)
        self.assertNotIn('\n# Forged', md)
        self.assertNotIn('<script>', md)
        self.assertEqual(md.count('</pre>'), 1)

    def test_failed_data_is_not_parsed(self):
        failed = ProbeResult('permission_denied', {'serial': 'SECRET'}, 'SECRET')
        result = self.build(session=failed, usb=failed, drm=failed)
        self.assertEqual(result.session['outputs'], [])
        self.assertIsNone(result.usb.data)
        self.assertIsNone(result.drm.data)
        self.assertNotIn('SECRET', report.to_json(result))
        self.assertEqual(self.build(session=ProbeResult('ok', [], '')).session['status'], 'error')

    def test_malformed_numbers_and_empty_modes_stay_unknown(self):
        for value in [10**400, float('inf'), float('nan'), True, '120', -1, {}, None]:
            with self.subTest(value=type(value).__name__):
                result = self.build([{'refreshRate': value, 'available_modes': [], 'disabled': False}])
                self.assertIsNone(result.session['outputs'][0]['active_mode']['refresh_hz']['value'])
                self.assertEqual(result.capabilities['active_120_hz']['state'], 'untested')
                self.assertEqual(result.capabilities['advertised_120_hz']['state'], 'untested')
                report.to_json(result)

    def test_plain_drm_modes_do_not_prove_refresh(self):
        result = self.build(drm=ProbeResult('ok', [{'modes': {'value': ['1920x1080', '120'], 'status': 'ok', 'source': '/synthetic/modes'}}], ''))
        self.assertEqual(result.capabilities['advertised_120_hz']['state'], 'untested')

    def test_timestamp_is_explicit_and_normalized_to_utc(self):
        empty = ProbeResult('ok', [], '')
        with self.assertRaises(ValueError):
            report.build_report(empty, empty, empty, {}, collected_at='2026-01-01T00:00:00')
        self.assertTrue(self.build().collected_at.endswith('+00:00'))

    def test_active_mode_is_not_sustained_or_available(self):
        result = self.build([{'name': 'DP-1', 'width': 1920, 'height': 1080,
                              'refreshRate': 120, 'disabled': False}])
        output = result.session['outputs'][0]
        self.assertEqual(output['active_mode']['refresh_hz']['value'], 120)
        self.assertEqual(output['available_modes']['status'], 'unknown')
        self.assertEqual(result.capabilities['active_120_hz']['state'], 'observed')
        self.assertEqual(result.capabilities['advertised_120_hz']['state'], 'untested')
        self.assertEqual(result.capabilities['sustained_120_hz']['state'], 'untested')
        self.assertEqual(result.capabilities['tracking_quality']['state'], 'untested')
        self.assertEqual(json.loads(report.to_json(result))['schema_version'], 1)
        self.assertIn('sustained_120_hz', report.render_markdown(result))
