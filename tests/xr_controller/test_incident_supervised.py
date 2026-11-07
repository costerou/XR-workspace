"""Whose run is it? The real probe, controller and monitor together, against a fake unit manager and a scratch state folder.

A run the controller did not start, such as the supervised Pro 2 test of 2026-11-06, leaves its journal beside the controller's
own and keeps its consent somewhere else. That run was reported as killed while it was healthy. Nothing here touches the real
runtime folder, a real unit, or the real incidents folder.
"""
import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from tools.native_spike.hyprland import OWNERSHIP_PLUGIN_NAME
from tools.native_spike.panel_consent import build_consent, write_consent
from tools.native_spike.profiles import DEFAULT_PROFILE
from tools.xr_controller import __main__ as main
from tools.xr_controller.incident_monitor import HostSources, IncidentMonitor
from tools.xr_controller.incident_report import Reports, Scrubber
from tools.xr_controller.state import Controller

RUN = '2298c7a18df736b9b6c4bde5b5877031'
OTHER = '549bcebd15b354c2c168fabd58cc1e0c'
SUPERVISED = 'pro2-supervised-20261106-191126'
SESSION = 'a1' * 16
PIN = dict(path='/pin/file', sha256='a' * 64)
MONITORS = [dict(name='DP-2', width=1920, height=1200, refreshRate=120.0, dpmsStatus=True, disabled=False),
            dict(name='DP-1', width=5120, height=1440, refreshRate=240.0, dpmsStatus=True, disabled=False)]
OPERATION = dict(id='7d3a9f1e-4c1b-4f60-9a8e-0a4e7f3b2c11', state='confirmed',
                 operation=dict(kind='output', id='i', name='XR-1', index=0, before=None))
T0 = 1793750000.0


class Store:
    def load(self, model): return copy.deepcopy(DEFAULT_PROFILE)
    def save(self, model, profile): pass


class Units:
    """systemd, as far as the controller asks. A supervised run has no unit at all, so `up` never holds its name."""
    def __init__(self): self.up, self.broken, self.asked = set(), False, []
    def alive(self, run_id):
        self.asked.append(run_id)
        if self.broken: raise subprocess.TimeoutExpired('systemctl', 2)
        return run_id in self.up
    def control(self, run_id, session, op, payload=None):
        return dict(status=dict(presented_hz=120.0), view={}, capabilities=[]) if op == 'status' else True
    def stop(self, run_id): raise AssertionError(f'stopped {run_id}')


def journalctl(argv, **kwargs):
    """The monitor's commands all answer empty: these runs have no logs, and the host's are not read."""
    return subprocess.CompletedProcess(argv, 0, stdout='', stderr='')


class Story(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state, self.runtime, self.data = self.root / 'state', self.root / 'run', self.root / 'data'
        for folder in (self.root, self.state, self.runtime, self.data): folder.mkdir(mode=0o700, exist_ok=True)
        self.fd = os.open(self.state, os.O_DIRECTORY)
        self.addCleanup(os.close, self.fd)
        self.units, self.now, self.toasts, self.spawned = Units(), T0, [], []
        host = main.Host(self.runtime, self.state, self.data)
        for patch in (mock.patch.dict(os.environ, HYPRLAND_INSTANCE_SIGNATURE='sig'),
                      mock.patch.object(main, 'HyprlandClient', side_effect=OSError('no compositor')),
                      mock.patch.object(main.monitor_rules, 'leftovers', return_value=[]),
                      mock.patch.object(main, 'tracking_drivers', return_value=[]),
                      mock.patch.object(main.Units, 'alive_many', lambda s, runs: {r: r in self.units.up for r in runs}),
                      mock.patch('tools.xr_controller.incident_monitor.log')):
            patch.start()
            self.addCleanup(patch.stop)
        # The compositor's side of the probe, which needs a live Hyprland; the journals are the real probe's.
        self.probe = lambda: dict(host.probe(), reason=None, model='luma-ultra', output='DP-2', monitors=MONITORS, installed=dict(plugin=PIN, renderer=PIN),
                                      plugins=[OWNERSHIP_PLUGIN_NAME])
        self.start()

    def start(self):
        """A controller (re)start: the same disk, nothing in memory."""
        self.monitor = IncidentMonitor(HostSources(self.runtime, self.state, sys_root=self.root, proc_root=self.root, run=journalctl),
                                       Reports(self.state / 'incidents'), notify=self.toasts.append, hand_on=lambda path: None,
                                       scrub=Scrubber(home='/home/testuser', user='testuser', host='testhost'), clock=lambda: self.now)
        self.controller = Controller(lambda: self.now, self.probe, lambda *a: self.spawned.append(a), self.units, Store(), self.fd, self.state, monitor=self.monitor)

    def tick(self, seconds=1):
        for _ in range(seconds):
            self.controller.tick()
            self.monitor.step(self.now)
            self.now += 1

    def journal(self, run_id, state='confirmed'):
        folder = self.runtime / 'xr-workspace-spike' / run_id
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = folder / 'journal.json'
        path.write_text(json.dumps(dict(schema=1, run_id=run_id, session=SESSION, operations=[dict(OPERATION, state=state)])))
        path.chmod(0o600)
        return path

    def supervised(self):
        """As pro2-xr-parent.py lays it out: the consent in the run's own folder, not `<state dir>/<run_id>.json`."""
        folder = self.state / 'pro2-hil' / SUPERVISED
        folder.mkdir(mode=0o700, parents=True)
        consent = folder / 'consent.json'
        consent.write_text(json.dumps(dict(schema=1, gate='pro2-supervised-v1', model='pro2', run_id=SUPERVISED, instance='sig', output='DP-2', recovery_output='DP-1')))
        consent.chmod(0o600)
        return self.journal(SUPERVISED)

    def own_run(self, run_id=RUN):
        scope = dict(run_id=run_id, instance='sig', output='DP-2', recovery_output='DP-1', model='luma-ultra', tracking=False, mode_switch=False,
                     desktops=[dict(width=1920, height=1200, scale=1.0)], renderer=PIN, plugin=PIN)
        write_consent(self.fd, build_consent(scope))
        self.units.up.add(run_id)
        return self.journal(run_id)

    def lose_consent(self, run_id=RUN): (self.state / f'{run_id}.json').chmod(0o640)  # read_private_json refuses it
    def restore_consent(self, run_id=RUN): (self.state / f'{run_id}.json').chmod(0o600)

    def reports(self):
        return [json.loads(path.read_text()) for path in sorted((self.state / 'incidents').glob('*.json'))]

    def kinds(self): return [report['kind'] for report in self.reports()]
    def snap(self): return self.controller.snapshot()


class SupervisedRunTests(Story):
    def test_a_healthy_supervised_run_is_no_incident(self):
        self.supervised()
        self.tick(90)  # longer than any hold, while the run is still live
        self.assertEqual(self.kinds(), [])
        self.assertEqual(self.toasts, [])
        self.journal(SUPERVISED, 'done')  # it ends the way the real one did
        self.tick(90)
        self.assertEqual(self.kinds(), [])
        self.assertEqual(self.snap()['state'], 'idle')

    def test_the_controller_says_what_it_sees(self):
        journal = self.supervised()
        self.tick()
        snap = self.snap()
        self.assertEqual(snap['state'], 'external-run')
        self.assertEqual(snap['journal'], str(journal))
        self.assertIsNone(snap['run_id'])

    def test_it_never_asks_systemd_about_a_unit_it_does_not_own(self):
        self.supervised()
        self.tick(5)
        self.assertEqual(self.units.asked, [])

    def test_start_recover_and_stop_stay_closed_while_it_runs(self):
        self.supervised()
        self.tick()
        for op in ('begin_start', 'begin_recover', 'stop'):
            with self.subTest(op=op):
                self.assertFalse(self.controller.request(dict(op=op))['ok'])
        self.assertEqual(self.spawned, [])
        self.assertEqual(self.units.asked, [])

    def test_a_start_offer_lapses_when_one_appears(self):
        self.tick()
        self.controller.state, self.controller.pending = 'confirming', dict(action='start', scope={}, scope_id='s')
        self.supervised()
        self.tick()
        self.assertIsNone(self.snap()['pending'])
        self.assertEqual(self.snap()['state'], 'external-run')

    def test_one_that_leaves_work_behind_stays_visible_and_unreported(self):
        self.supervised()
        self.tick(90)
        self.assertEqual(self.snap()['state'], 'external-run')
        self.assertEqual(self.kinds(), [])

    def test_a_controller_restart_under_it_is_no_incident(self):
        self.supervised()
        self.tick(5)
        self.start()
        self.tick(90)
        self.assertEqual(self.kinds(), [])

    def test_it_does_not_disturb_a_controller_run(self):
        self.own_run()
        self.tick(3)
        self.assertEqual(self.snap()['state'], 'running')
        self.supervised()
        self.tick(90)
        self.assertEqual(self.snap()['state'], 'running')
        self.assertEqual(self.snap()['run_id'], RUN)
        self.assertEqual(self.kinds(), [])

    def test_a_controller_run_that_ends_while_one_runs(self):
        self.own_run()
        self.supervised()
        self.tick(3)
        self.journal(RUN, 'done')
        self.units.up.clear()
        self.tick(30)
        self.assertEqual(self.snap()['state'], 'external-run')
        self.assertEqual(self.kinds(), ['run-killed'])  # the controller's run left no result; only the other one is ignored


class ControllerRunTests(Story):
    def test_a_killed_run_is_still_reported(self):
        self.own_run()
        self.tick(10)
        self.units.up.clear()  # the unit is gone, its journal unfinished, no result written
        self.tick(30)
        self.assertEqual(self.snap()['state'], 'recovery-needed')
        self.assertEqual(self.kinds(), ['run-killed'])
        self.assertEqual(self.reports()[0]['run_id'], RUN)

    def test_a_clean_stop_is_still_nothing(self):
        self.own_run()
        self.tick(10)
        self.journal(RUN, 'done')
        self.units.up.clear()
        (self.state / f'{RUN}.result.json').write_text(json.dumps(dict(run_id=RUN, phase='done', complete=True, renderer_returncode=0, remaining=[], error=None)))
        (self.state / f'{RUN}.result.json').chmod(0o600)
        self.tick(30)
        self.assertEqual(self.kinds(), [])

    def test_two_of_its_own_with_one_alive_are_not_a_killed_run(self):
        self.own_run()
        self.tick(5)
        self.own_run(OTHER)
        self.units.up.discard(OTHER)
        self.tick(30)
        self.assertEqual(self.snap()['state'], 'cli-recovery')
        self.assertEqual(self.kinds(), [])


class ConsentFailureTests(Story):
    def test_a_consent_it_cannot_read_is_not_a_killed_run(self):
        self.own_run()
        self.lose_consent()
        self.tick(90)
        self.assertEqual(self.snap()['state'], 'cli-recovery')
        self.assertEqual(self.kinds(), [])

    def test_losing_the_consent_mid_run_is_not_a_killed_run(self):
        self.own_run()
        self.tick(10)
        self.lose_consent()
        self.tick(90)
        self.assertEqual(self.snap()['state'], 'cli-recovery')
        self.assertEqual(self.kinds(), [])

    def test_the_run_carries_on_once_the_consent_reads_again(self):
        self.own_run()
        self.tick(10)
        self.lose_consent()
        self.tick(5)
        self.restore_consent()
        self.tick(30)
        self.assertEqual(self.snap()['state'], 'running')
        self.assertEqual(self.kinds(), [])
        self.units.up.clear()
        self.tick(30)  # and it is still watched
        self.assertEqual(self.kinds(), ['run-killed'])

    def test_a_dead_unit_is_evidence_even_with_no_consent(self):
        self.own_run()
        self.tick(10)
        self.lose_consent()
        self.tick(5)
        self.units.up.clear()
        self.tick(30)
        self.assertEqual(self.snap()['state'], 'cli-recovery')
        self.assertEqual(self.kinds(), ['run-killed'])

    def test_a_systemd_that_does_not_answer_proves_nothing(self):
        self.own_run()
        self.tick(10)
        self.lose_consent()
        self.units.broken = True
        self.tick(90)
        self.assertEqual(self.snap()['state'], 'cli-recovery')
        self.assertEqual(self.kinds(), [])

    def test_a_controller_restart_while_it_cannot_read_it(self):
        self.own_run()
        self.tick(10)
        self.lose_consent()
        self.tick(2)
        self.start()
        self.tick(90)
        self.assertEqual(self.kinds(), [])

    def test_a_restart_that_finds_a_dead_run_it_cannot_read(self):
        self.own_run()
        self.lose_consent()
        self.units.up.clear()
        self.start()
        self.tick(30)
        self.assertEqual(self.kinds(), ['controller-restart'])

    def test_a_consent_that_no_longer_validates(self):
        path = self.own_run() and self.state / f'{RUN}.json'
        self.tick(10)
        path.write_text(json.dumps(dict(json.loads(path.read_text()), gate='something-else')))
        self.tick(90)
        self.assertEqual(self.snap()['state'], 'cli-recovery')
        self.assertEqual(self.kinds(), [])


if __name__ == '__main__': unittest.main()
