"""The controller and the incident monitor together: what the tick hands over, and that the monitor can never hurt the controller."""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from tools.native_spike.hyprland import OWNERSHIP_PLUGIN_NAME
from tools.native_spike.profiles import DEFAULT_PROFILE
from tools.xr_controller import __main__ as main
from tools.xr_controller.incident_monitor import IncidentMonitor
from tools.xr_controller.incident_report import Reports
from tools.xr_controller.state import Controller
from tests.xr_controller.test_incident_monitor import Sources

RUN = 'a' * 32
PIN = dict(path='/pin', sha256='0' * 64)
MONITORS = [dict(name='DP-2', width=1920, height=1200, refreshRate=119.99, dpmsStatus=True, disabled=False, activeWorkspace=dict(name='secret workspace'),
                 description='VITURE serial 1234', serial='1234'),
            dict(name='DP-1', width=5120, height=1440, refreshRate=240.0, dpmsStatus=True, disabled=False)]


class Store:
    def load(self, model): return copy.deepcopy(DEFAULT_PROFILE)
    def save(self, model, profile): pass


class Units:
    def __init__(self): self.up, self.stopped, self.reply = True, [], dict(status=dict(presented_hz=120.0), view={}, capabilities=[])
    def alive(self, run_id): return self.up
    def control(self, run_id, session, op, payload=None): return self.reply if op == 'status' else True
    def stop(self, run_id): self.stopped.append(run_id)


class Recorder:
    latest = None
    def __init__(self): self.seen = []
    def observe(self, observation): self.seen.append(observation)


class WiringCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        os.chmod(self.path, 0o700)
        self.fd = os.open(self.path, os.O_DIRECTORY)
        self.addCleanup(os.close, self.fd)
        self.observed = dict(model='luma-ultra', output='DP-2', reason=None, instance='sig', monitors=MONITORS, plugins=[OWNERSHIP_PLUGIN_NAME, 'hyprbars'],
                             installed=dict(plugin=PIN, renderer=PIN), journals=[])
        self.units = Units()
        self.now = 0.0

    def controller(self, monitor):
        return Controller(lambda: self.now, lambda: copy.deepcopy(self.observed), lambda *a: None, self.units, Store(), self.fd, self.path, monitor=monitor)

    def running(self, controller):
        """A run up and answering, the way the probe reports one."""
        self.observed['journals'] = [dict(run_id=RUN, session='s', path='/j', operations=[dict(state='pending')])]
        with mock.patch.object(controller, '_consent', lambda journal: dict(model='luma-ultra')):
            controller.tick()


class ObservationTests(WiringCase):
    def test_every_tick_reports(self):
        recorder = Recorder()
        c = self.controller(recorder)
        c.tick()
        self.assertEqual(len(recorder.seen), 2)  # the one in the constructor, too
        self.assertEqual(recorder.seen[-1]['state'], 'idle')
        self.assertIsNone(recorder.seen[-1]['run_id'])
        self.assertIsNone(recorder.seen[-1]['status'])

    def test_a_running_session(self):
        recorder = Recorder()
        c = self.controller(recorder)
        self.running(c)
        seen = recorder.seen[-1]
        self.assertEqual((seen['state'], seen['run_id'], seen['instance'], seen['plugins']), ('running', RUN, 'sig', 1))
        self.assertEqual(seen['status'], dict(presented_hz=120.0))

    def test_only_named_monitor_fields_leave_the_controller(self):
        recorder = Recorder()
        c = self.controller(recorder)
        self.running(c)
        self.assertEqual(recorder.seen[-1]['monitors'][0], dict(name='DP-2', width=1920, height=1200, refresh=119.99, dpms=True))
        self.assertNotIn('secret', json.dumps(recorder.seen[-1]))

    def test_no_status_reply_is_no_status_not_an_empty_one(self):
        recorder = Recorder()
        c = self.controller(recorder)
        self.units.reply = False
        self.running(c)
        self.assertIsNone(recorder.seen[-1]['status'])

    def test_a_stop_is_flagged_before_the_next_tick(self):
        recorder = Recorder()
        c = self.controller(recorder)
        self.running(c)
        self.assertTrue(c.request(dict(op='stop'))['ok'])
        self.assertEqual(self.units.stopped, [RUN])
        with mock.patch.object(c, '_consent', lambda journal: dict(model='luma-ultra')): c.tick()
        self.assertEqual(recorder.seen[-1]['stop_requested'], RUN)

    def test_a_failed_tick_still_reports(self):
        recorder = Recorder()
        c = self.controller(recorder)
        c.probe = lambda: (_ for _ in ()).throw(ValueError('broken'))
        c.tick()
        self.assertEqual(recorder.seen[-1]['state'], 'failed')

    def test_the_observation_is_a_copy(self):
        recorder = Recorder()
        c = self.controller(recorder)
        self.running(c)
        recorder.seen[-1]['status']['presented_hz'] = 0
        self.assertEqual(c.live['status']['presented_hz'], 120.0)


class SafetyTests(WiringCase):
    def test_a_monitor_that_raises_cannot_stop_a_tick(self):
        class Broken:
            latest = None
            def observe(self, observation): raise RuntimeError('monitor bug')
        with contextlib.redirect_stderr(io.StringIO()):
            c = self.controller(Broken())
            c.tick()
        self.assertEqual(c.state, 'idle')

    def test_a_monitor_bug_is_said_once(self):
        class Broken:
            latest = None
            def observe(self, observation): raise RuntimeError('monitor bug')
        with contextlib.redirect_stderr(io.StringIO()) as said:
            c = self.controller(Broken())
            for _ in range(5): c.tick()
        self.assertEqual(said.getvalue().count('monitor bug'), 1)

    def test_a_controller_with_no_monitor_works_as_before(self):
        c = self.controller(None)
        self.assertIsNone(c.snapshot()['incident'])


class SnapshotTests(WiringCase):
    def test_the_latest_incident_reaches_the_panel(self):
        recorder = Recorder()
        recorder.latest = dict(id='x', kind='usb-drop', summary='the line', at='2026-11-04T01:06:12Z', path='/p')
        c = self.controller(recorder)
        self.assertEqual(c.snapshot()['incident']['summary'], 'the line')
        c.snapshot()['incident']['summary'] = 'changed'
        self.assertEqual(recorder.latest['summary'], 'the line')

    def test_no_incident_yet(self):
        self.assertIsNone(self.controller(Recorder()).snapshot()['incident'])

    def test_a_new_incident_changes_the_snapshot_so_the_panel_hears(self):
        recorder = Recorder()
        c = self.controller(recorder)
        before = c.snapshot()
        recorder.latest = dict(id='x', kind='usb-drop', summary='the line', at='t', path='/p')
        self.assertNotEqual(c.snapshot(), before)


class EndToEndTests(WiringCase):
    """The real Controller into the real IncidentMonitor; only the host is fake."""
    def test_a_run_that_dies_reaches_the_snapshot(self):
        host = Sources()
        host.consents[RUN] = dict(run_id=RUN, instance='sig', output='DP-2', tracking='xrlinuxdriver', model='luma-ultra', renderer=PIN, plugin=PIN)
        clock = [1793750000.0]
        toasts = []
        with mock.patch('tools.xr_controller.incident_monitor.log'):
            monitor = IncidentMonitor(host, Reports(self.path / 'incidents'), notify=toasts.append, hand_on=lambda path: None, clock=lambda: clock[0])
            c = self.controller(monitor)
            self.running(c)
            monitor.step(clock[0])
            self.units.up = False
            self.observed['journals'] = []
            host.results[RUN] = dict(run_id=RUN, phase='done', complete=True, renderer_returncode=1, remaining=[], error=None)
            (self.path / f'{RUN}.result.json').write_text(json.dumps(host.results[RUN]))
            os.chmod(self.path / f'{RUN}.result.json', 0o600)
            for _ in range(10):
                clock[0] += 1
                c.tick()
                monitor.step(clock[0])
        incident = c.snapshot()['incident']
        self.assertEqual(incident['kind'], 'renderer-exit')
        self.assertTrue(Path(incident['path']).exists())
        self.assertEqual(len(toasts), 1)
        self.assertEqual(c.state, 'idle')


class ServiceWiringTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        os.chmod(self.root, 0o700)

    def test_the_service_builds_and_starts_a_monitor(self):
        with mock.patch.object(IncidentMonitor, 'start') as start:
            monitor = main.incident_monitor(self.root / 'run', self.root, self.root / 'cfg')
        start.assert_called_once()
        self.assertEqual(monitor.reports.directory, self.root / 'incidents')
        self.assertEqual(monitor.hook, self.root / 'cfg/viture-workspace/on-incident')

    def test_it_never_creates_the_hook(self):
        with mock.patch.object(IncidentMonitor, 'start'): main.incident_monitor(self.root / 'run', self.root, self.root / 'cfg')
        self.assertFalse((self.root / 'cfg').exists())

    def test_a_monitor_that_cannot_start_leaves_the_controller_running(self):
        with mock.patch.object(IncidentMonitor, '__init__', side_effect=RuntimeError('no')), contextlib.redirect_stderr(io.StringIO()):
            self.assertIsNone(main.incident_monitor(self.root / 'run', self.root, self.root / 'cfg'))


class PanelSourceTests(unittest.TestCase):
    PANEL = Path(__file__).resolve().parents[2] / 'omarchy-plugin/cody.viture-workspace'

    def test_the_panel_shows_the_line_as_plain_text(self):
        # It holds log-derived words: never rich text. Home shows it in a Note, whose lines are PanelText (plain text).
        home = (self.PANEL / 'HomePage.qml').read_text()
        self.assertIn('Model.incidentNote(snapshot)', home)
        self.assertIn('textFormat: Text.PlainText', (self.PANEL / 'PanelText.qml').read_text())
        note = (self.PANEL / 'Note.qml').read_text()
        self.assertEqual(note.count('PanelText {'), 2, 'the note title and text')

    def test_the_running_page_shows_the_incident_and_the_measured_values(self):
        # The running page keeps the last problem and one health line (Model.healthLine reads tracking and capture_failure_run).
        self.assertIn('Model.incidentNote(snapshot)', (self.PANEL / 'RunningPage.qml').read_text())
        model = (self.PANEL / 'Model.js').read_text()
        for key in ('status.tracking', 'status.capture_failure_run'): self.assertIn(key, model)


if __name__ == '__main__': unittest.main()
