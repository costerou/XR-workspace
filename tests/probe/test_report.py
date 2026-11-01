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

    def test_exact_mode_tokens_not_rounded(self):
        luma = ['1920x1080@60.00Hz', '1920x1200@120.00Hz', '1920x1200@90.00Hz',
                '1920x1200@60.00Hz', '1920x1080@90.00Hz']
        beast = ['1920x1080@120.00Hz', '1920x1080@119.88Hz']
        for tokens in (luma, beast):
            result = self.build([{'name': 'DP-2', 'availableModes': tokens}])
            modes = result.session['outputs'][0]['available_modes']['value']
            self.assertIsNotNone(modes, 'availableModes tokens were dropped/unparsed')
            self.assertEqual([m['raw']['value'] for m in modes], tokens)
            for i, mode in enumerate(modes):
                self.assertEqual(mode['raw']['source'], f'session.data.outputs[0].availableModes[{i}]')
                self.assertEqual(mode['refresh_hz']['source'], mode['raw']['source'])
            self.assertEqual(result.capabilities['sustained_120_hz']['state'], 'untested')
            self.assertEqual(result.capabilities['tracking_quality']['state'], 'untested')
        self.assertEqual(modes[1]['refresh_hz']['value'], 119.88)
        self.assertEqual(result.capabilities['advertised_120_hz']['evidence'],
                         ['session.data.outputs[0].availableModes[0]'])
        modes = self.build([{'name': 'DP-2', 'availableModes': luma}]).session['outputs'][0]['available_modes']['value']
        self.assertFalse(any(m['height']['value'] == 1080 and m['refresh_hz']['value'] == 120 for m in modes))

    def test_near_120_decimal_cannot_round_into_advertisement(self):
        result = self.build([{'name': 'DP-1', 'availableModes': ['1920x1080@119.999999999999999999Hz']}])
        self.assertEqual(result.capabilities['advertised_120_hz']['state'], 'untested')

    def test_mode_parser_full_grammar_and_positive_finite_values(self):
        parser = report.parse_available_mode
        self.assertEqual(parser('1920x1080@119.88Hz'),
                         dict(width=1920, height=1080, refreshRate=119.88, raw='1920x1080@119.88Hz'))
        self.assertEqual(parser('001x002@060.00Hz'),
                         dict(width=1, height=2, refreshRate=60.0, raw='001x002@060.00Hz'))
        for token in (None, 42, {}, '', '1920x1080', '1920x1080@nanHz', '1920x1080@infHz',
                      '1920x1080@1e2Hz', '0x1080@120Hz', '1920x0@120Hz', '1920x1080@0Hz',
                      '-1x1080@120Hz', '1920x1080@-1Hz', '1920x1080@120Hz\n',
                      ' 1920x1080@120Hz', '1920x1080@120Hzjunk', '１９２０x1080@120Hz',
                      '1x1@' + '9' * 65 + 'Hz'):
            with self.subTest(token=token):
                self.assertIsNone(parser(token))

    def test_raw_mode_unknown_truncated_and_original_indexes(self):
        for raw in (None, [], {}, [42], ['bad']):
            field = self.build([{'availableModes': raw}]).session['outputs'][0]['available_modes']
            self.assertEqual(field['status'], 'unknown')
        for raw in ([None, '1920x1080@120Hz'], ['x' * 65, '1920x1080@120Hz']):
            result = self.build([{'name': 'DP-1', 'availableModes': raw}])
            self.assertEqual(result.capabilities['advertised_120_hz']['evidence'],
                             ['session.data.outputs[0].availableModes[1]'])
        field = self.build([{'availableModes': ['1920x1080@60Hz'] * 257}]).session['outputs'][0]['available_modes']
        self.assertEqual(field['status'], 'truncated')
        self.assertEqual(len(field['value']), 256)
        field = self.build([{'availableModes': ['1920x1080@60Hz'], 'availableModes_status': 'truncated'}]).session['outputs'][0]['available_modes']
        self.assertEqual(field['status'], 'truncated')

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
