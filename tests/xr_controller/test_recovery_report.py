"""Why a recovery did not finish reaches the person: recover-run's output, the panel, and the incident report.

Incident 20261104T022411Z: Recover exited 2 three times with nothing printed, and the report could only say "No clear cause".
Offline, in a scratch directory.
"""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock

from tools.native_spike.hyprland import OWNERSHIP_PLUGIN_NAME
from tools.native_spike.profiles import DEFAULT_PROFILE
from tools.xr_controller import __main__ as controller_main
from tools.xr_controller.incident_report import build_bundle, guess_cause, markdown, Scrubber
from tools.xr_controller.state import Controller

RUN = 'a' * 32
REAL_READ = controller_main.read_private_json
REASON = 'XR-1: the ownership plugin refused to remove it (state blocked)'


class RecoverRun(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name)
        self.state.chmod(0o700)
        self.consent = self.state / f'{RUN}.json'
        self.consent.write_text('{}')
        self.client = mock.Mock()
        self.client.plugins_loaded.side_effect = [[OWNERSHIP_PLUGIN_NAME], []]
        self.plugin = dict(path='/p/libxr-ownership.so', sha256='b' * 64)

    def reading(self, path, *a, **k):
        return dict(instance='i', output='DP-2') if Path(path) == self.consent else REAL_READ(path, *a, **k)

    def run_it(self, outcome=None, error=None):
        """main()'s recover-run. `outcome`: what recover_managed says, `error`: what it raises."""
        err = io.StringIO()
        recover = mock.Mock(return_value=outcome, side_effect=error)
        with mock.patch.dict(os.environ, XDG_RUNTIME_DIR=str(self.state), XDG_DATA_HOME=str(self.state)), \
             mock.patch.object(controller_main, 'read_private_json', self.reading), \
             mock.patch('tools.native_spike.panel_consent.load_panel_consent', return_value=dict(instance='i', plugin=self.plugin)), \
             mock.patch('tools.xr_controller.install.verify_recovery_pin'), \
             mock.patch.object(controller_main, 'HyprlandClient', return_value=self.client), \
             mock.patch('tools.native_spike.managed.recover_managed', recover), contextlib.redirect_stderr(err):
            code = controller_main.main(['recover-run', '--consent', str(self.consent), '--journal', str(self.state / 'journal.json')])
        return code, err.getvalue()

    def record(self):
        return self.state / f'{RUN}.recovery.json'

    def saved(self):
        self.assertEqual(stat.S_IMODE(self.record().stat().st_mode), 0o600)
        return json.loads(self.record().read_text())

    def test_an_incomplete_recovery_says_why(self):
        code, said = self.run_it(dict(complete=False, remaining=['x'], reasons=[REASON]))
        self.assertEqual(code, 2)
        self.assertIn(REASON, said)
        self.assertEqual(self.saved()['reasons'], [REASON])
        self.assertEqual(self.saved()['run_id'], RUN)
        self.client.plugin_unload.assert_not_called()

    def test_with_no_reason_given_it_still_says_something(self):
        code, said = self.run_it(dict(complete=False, remaining=['x']))
        self.assertEqual(code, 2)
        self.assertIn('did not finish', said)
        self.assertTrue(self.saved()['reasons'])

    def test_a_refusal_before_the_journal_is_reported_as_well(self):
        code, said = self.run_it(error=ValueError('consent does not match saved broker scope'))
        self.assertEqual(code, 2)
        self.assertIn('consent does not match saved broker scope', said)
        self.assertIn('consent does not match saved broker scope', self.saved()['reasons'][0])

    def test_the_plugin_gone_is_a_reason(self):
        self.client.plugins_loaded.side_effect = [[]]
        code, said = self.run_it(dict(complete=True, remaining=[]))
        self.assertEqual(code, 2)
        self.assertIn('ownership plugin unavailable', said)

    def test_a_finished_recovery_leaves_no_old_reason_and_clears_the_runs_failure(self):
        self.record().write_text(json.dumps(dict(run_id=RUN, reasons=[REASON]))); self.record().chmod(0o600)
        result = self.state / f'{RUN}.result.json'
        result.write_text(json.dumps(dict(run_id=RUN, phase='run', complete=False, renderer_returncode=0, remaining=['x'],
                                          error='ownership plugin retained for explicit recovery: ' + REASON, recovery=[REASON])))
        result.chmod(0o600)
        code, said = self.run_it(dict(complete=True, remaining=[], reasons=[]))
        self.assertEqual((code, said), (0, ''))
        self.assertFalse(self.record().exists())
        self.client.plugin_unload.assert_called_once()
        settled = json.loads(result.read_text())
        self.assertEqual((settled['complete'], settled['error'], settled['remaining']), (True, None, []))
        self.assertNotIn('recovery', settled)
        self.assertEqual(settled['renderer_returncode'], 0)

    def test_an_unload_that_does_not_hold_is_not_a_finish(self):
        self.client.plugins_loaded.side_effect = [[OWNERSHIP_PLUGIN_NAME], [OWNERSHIP_PLUGIN_NAME]]
        code, said = self.run_it(dict(complete=True, remaining=[], reasons=[]))
        self.assertEqual(code, 2)
        self.assertIn('plugin unload not confirmed', said)

    def test_the_controller_reads_past_the_record_when_listing_runs(self):
        self.consent.write_text(json.dumps(dict(run_id=RUN))); self.consent.chmod(0o600)
        self.run_record = self.run_it(dict(complete=False, remaining=['x'], reasons=[REASON]))
        host = controller_main.Host(self.state, self.state, self.state)
        real = controller_main.read_private_json
        seen = []
        def reading(path, *a, **k):
            seen.append(Path(path).name); return real(path, *a, **k)
        with mock.patch.object(controller_main, 'read_private_json', reading), \
             mock.patch.object(controller_main.Units, 'alive_many', lambda self, runs: {r: False for r in runs}), \
             mock.patch.object(controller_main, 'HyprlandClient', side_effect=OSError('no compositor')):
            host.probe()
        self.assertNotIn(f'{RUN}.recovery.json', seen)


class Store:
    def __init__(self): self.profile = copy.deepcopy(DEFAULT_PROFILE)
    def load(self, model): return copy.deepcopy(self.profile)
    def save(self, model, p): self.profile = copy.deepcopy(p)


class Units:
    def alive(self, r): return False
    def control(self, r, s, op): return True


class Panel(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        self.fd = os.open(self.path, os.O_DIRECTORY); self.addCleanup(os.close, self.fd)
        from tools.native_spike.panel_consent import build_consent, write_consent
        installed = {k: dict(path='/p/' + k, sha256='a' * 64) for k in ('plugin', 'renderer')}
        scope = dict(run_id=RUN, instance='instance', output='DP-2', recovery_output='DP-1', model='beast', tracking=False, mode_switch=False,
                     desktops=[dict(width=1920, height=1200, scale=1.0)], **installed)
        write_consent(self.fd, build_consent(scope))
        operation = dict(id='rec', operation=dict(kind='output', id='i', name='XR-1', index=0, before=None), state='confirmed')
        self.observation = dict(model='beast', output='DP-2', reason=None, instance='instance', monitors=[dict(name='DP-2'), dict(name='DP-1')],
                                installed=installed, plugins=[OWNERSHIP_PLUGIN_NAME], config_rules=[],
                                journals=[dict(run_id=RUN, session='session', path=str(self.path / 'journal.json'), operations=[operation])])
        self.controller = Controller(lambda: 100, lambda: copy.deepcopy(self.observation), lambda *a: None, Units(), Store(), self.fd, self.path)

    def failed(self, *reasons):
        record = self.path / f'{RUN}.recovery.json'
        record.write_text(json.dumps(dict(run_id=RUN, complete=False, reasons=list(reasons)))); record.chmod(0o600)

    def test_recover_needed_shows_why_the_last_recover_stopped(self):
        self.failed(REASON)
        self.controller.tick()
        snap = self.controller.snapshot()
        self.assertEqual(snap['state'], 'recovery-needed')
        self.assertIn(REASON, snap['reason'])

    def test_nothing_is_shown_before_a_first_attempt(self):
        self.controller.tick()
        self.assertIsNone(self.controller.snapshot()['reason'])

    def test_a_record_of_the_wrong_shape_says_nothing(self):
        self.failed()
        record = self.path / f'{RUN}.recovery.json'
        record.write_text(json.dumps(dict(run_id=RUN, reasons='not a list'))); record.chmod(0o600)
        self.controller.tick()
        self.assertIsNone(self.controller.snapshot()['reason'])

    def test_a_record_for_another_run_says_nothing(self):
        record = self.path / f'{RUN}.recovery.json'
        record.write_text(json.dumps(dict(run_id='b' * 32, reasons=[REASON]))); record.chmod(0o600)
        self.controller.tick()
        self.assertIsNone(self.controller.snapshot()['reason'])

    def test_an_unreadable_record_costs_only_the_reason(self):
        record = self.path / f'{RUN}.recovery.json'
        record.write_text('not json'); record.chmod(0o600)
        self.controller.tick()
        self.assertEqual(self.controller.snapshot()['state'], 'recovery-needed')

    def test_the_confirm_box_says_a_config_line_will_go(self):
        self.observation['config_rules'] = [dict(name='XR-1', line=9, removable=True), dict(name='XR-3', line=10, removable=True)]
        self.controller.tick()
        reply = self.controller.request(dict(op='begin_recover'))
        self.assertTrue(reply['ok'], reply)
        self.assertEqual(reply['rules'], [dict(name='XR-1', line=9, removable=True)])  # XR-3 is no output of this run

    def test_the_confirm_box_is_quiet_when_there_is_none(self):
        self.controller.tick()
        self.assertEqual(self.controller.request(dict(op='begin_recover'))['rules'], [])


class ReadingTheRecord(unittest.TestCase):
    """HostSources.recovery: read for the incident report, which must never fail on it."""
    def setUp(self):
        from tools.xr_controller.incident_monitor import HostSources
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        self.sources = HostSources(self.path, self.path)

    def write(self, data):
        record = self.path / f'{RUN}.recovery.json'
        record.write_text(json.dumps(data)); record.chmod(0o600)

    def test_none_before_a_first_attempt(self):
        self.assertIsNone(self.sources.recovery(RUN))

    def test_its_reasons(self):
        self.write(dict(run_id=RUN, reasons=[REASON]))
        self.assertEqual(self.sources.recovery(RUN), dict(reasons=[REASON]))

    def test_another_runs_or_the_wrong_shape(self):
        self.write(dict(run_id='b' * 32, reasons=[REASON]))
        self.assertIsNone(self.sources.recovery(RUN))
        self.write(dict(run_id=RUN, reasons='not a list'))
        self.assertEqual(self.sources.recovery(RUN), dict(reasons=[]))


class Report(unittest.TestCase):
    def episode(self, kind):
        from tools.xr_controller.incident_detect import Episode, Signal
        episode = Episode(RUN, 1793750000.0)
        episode.add(Signal(1793750000.0, kind, 'controller', f'Recover ran, and the session still has unfinished work: {REASON}'))
        return episode

    def bundle(self, recovery):
        scrub = Scrubber(home='/home/testuser', user='testuser', host='testhost')
        return build_bundle(self.episode('recovery-failed'), dict(recovery=recovery), 1793750010.0, scrub)

    def test_the_bundle_holds_the_reason_and_the_cause_names_it(self):
        bundle = self.bundle(dict(reasons=[REASON]))
        self.assertEqual(bundle['recovery']['reasons'], [REASON])
        self.assertIn(REASON, bundle['cause']['text'])
        self.assertEqual(bundle['cause']['confidence'], 'high')
        self.assertIn(REASON, bundle['timeline'][0]['detail'])
        self.assertIn(REASON, markdown(bundle))

    def test_a_stop_that_kept_the_plugin_gives_its_reasons_too(self):
        guess = guess_cause(self.episode('run-failed'), dict(result=dict(error='ownership plugin retained for explicit recovery', recovery=[REASON])))
        self.assertIn(REASON, guess['text'])

    def test_no_reason_is_no_section_of_guesses(self):
        bundle = self.bundle(None)
        self.assertEqual(bundle['recovery'], {'reasons': []})
        self.assertEqual(bundle['cause']['confidence'], 'low')


if __name__ == '__main__': unittest.main()
