"""Recover after the run's compositor has exited.

Incident 20261109T011152Z: Hyprland segfaulted (libaquamarine) after run c74b4a44 failed, and came back as a new instance. Every
Recover then dialled the old instance's request socket ([Errno 2] .socket.sock) and could never finish, though the XR outputs,
workspace moves and setting its journal recorded had all ended with that compositor. Offline, in a scratch directory.
"""
import contextlib
import io
import json
import os
from pathlib import Path
import socket
import tempfile
import unittest
from unittest import mock

from tools.native_spike.hyprland import instance_ended
from tools.native_spike.journal import Journal
from tools.xr_controller import __main__ as controller_main

RUN = 'c' * 32
OLD = 'e' * 40 + '_1794116390_864763619'
SESSION = 'f' * 64
BROKER = dict(instance=OLD, output='DP-2', recovery_output='DP-1', epoch='a' * 48, fallback='b' * 48)


def instance_dir(runtime, instance=OLD):
    path = Path(runtime) / 'hypr' / instance
    path.mkdir(parents=True, exist_ok=True)
    return path


class InstanceEnded(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(dir='/tmp'); self.addCleanup(tmp.cleanup)
        self.runtime = Path(tmp.name)
        self.dir = instance_dir(self.runtime)

    def listen(self):
        sock = socket.socket(socket.AF_UNIX); self.addCleanup(sock.close)
        sock.bind(str(self.dir / '.socket.sock'))

    def test_a_live_instance_has_not_ended(self):
        self.listen()
        (self.dir / 'hyprland.lock').write_text(f'{os.getpid()}\nwayland-1\n')
        self.assertFalse(instance_ended(self.runtime, OLD))

    def test_socket_and_lock_gone_is_an_ended_instance(self):
        # What the crashed instance left: its log and event socket, no request socket, no lock.
        (self.dir / 'hyprland.log').write_text('')
        self.assertTrue(instance_ended(self.runtime, OLD))

    def test_a_lock_naming_no_hyprland_is_an_ended_instance(self):
        (self.dir / 'hyprland.lock').write_text(f'{os.getpid()}\nwayland-1\n')  # this Python, not Hyprland
        self.assertTrue(instance_ended(self.runtime, OLD))

    def test_a_lock_naming_a_dead_process_is_an_ended_instance(self):
        (self.dir / 'hyprland.lock').write_text('2147483646\nwayland-1\n')
        self.assertTrue(instance_ended(self.runtime, OLD))

    def test_an_unreadable_lock_proves_nothing(self):
        (self.dir / 'hyprland.lock').write_text('garbage')
        self.assertFalse(instance_ended(self.runtime, OLD))

    def test_a_socket_alone_proves_nothing(self):
        self.listen()
        self.assertFalse(instance_ended(self.runtime, OLD))

    def test_a_malformed_instance_proves_nothing(self):
        self.assertFalse(instance_ended(self.runtime, '../x'))


class RecoverEnded(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(dir='/tmp'); self.addCleanup(tmp.cleanup)
        root = Path(tmp.name); root.chmod(0o700)
        self.runtime, self.state = root / 'run', root / 'state'
        for path in (self.runtime, self.state): path.mkdir(mode=0o700)
        instance_dir(self.runtime)  # ended: no request socket, no lock
        self.journal_path = self.runtime / 'xr-workspace-spike' / RUN / 'journal.json'
        self.journal_path.parent.mkdir(parents=True, mode=0o700)
        (self.runtime / 'xr-workspace-spike').chmod(0o700)
        journal = Journal.open(self.journal_path, RUN, SESSION)
        journal.bind_broker(BROKER)
        for index in range(3):
            name = f'XR-{index + 1}'; oid = f'0000000{index}-0000-4000-8000-000000000000'
            op = journal.prepare(dict(kind='output', id=oid, name=name, index=index, before=None))
            journal.confirm(op, dict(id=oid, name=name, physical=False))
        self.consent = self.state / f'{RUN}.json'
        self.consent.write_text('{}')
        self.layouts = self.state / 'viture-workspace' / 'layouts'
        self.layouts.mkdir(parents=True)
        (self.layouts / f'{RUN}.json').write_text('{}')

    def run_it(self, consent):
        err = io.StringIO()
        with mock.patch.dict(os.environ, XDG_RUNTIME_DIR=str(self.runtime), XDG_STATE_HOME=str(self.state)), \
             mock.patch.object(controller_main, 'read_private_json', side_effect=lambda p, *a, **k: dict(instance=OLD, output='DP-2', model='pro2')
                               if Path(p) == self.consent else REAL_READ(p, *a, **k)), \
             mock.patch('tools.native_spike.panel_consent.load_panel_consent', return_value=consent), \
             mock.patch.object(controller_main, 'HyprlandClient', side_effect=AssertionError('dialled the ended compositor')), \
             mock.patch.object(controller_main.pro2_route, 'recover', side_effect=AssertionError('dialled the ended compositor')), \
             contextlib.redirect_stderr(err):
            code = controller_main.main(['recover-run', '--consent', str(self.consent), '--journal', str(self.journal_path)])
        return code, err.getvalue()

    def states(self):
        return [r['state'] for r in json.loads(self.journal_path.read_text())['operations']]

    def test_recover_finishes_when_the_runs_compositor_has_ended(self):
        code, said = self.run_it(dict(BROKER, run_id=RUN))
        self.assertEqual(code, 0, said)
        self.assertEqual(self.states(), ['done'] * 3)
        self.assertFalse((self.layouts / f'{RUN}.json').exists())  # its windows were the ended compositor's
        self.assertFalse((self.state / f'{RUN}.recovery.json').exists())

    def test_a_consent_for_another_scope_is_refused_and_the_journal_kept(self):
        code, said = self.run_it(dict(BROKER, run_id=RUN, recovery_output='HDMI-A-1'))
        self.assertEqual(code, 2)
        self.assertIn('consent does not match saved broker scope', said)
        self.assertEqual(self.states(), ['confirmed'] * 3)

    def test_a_live_run_holding_its_journal_is_not_touched(self):
        from tools.native_spike.journal import managed_owner
        with managed_owner(self.journal_path):
            code, _ = self.run_it(dict(BROKER, run_id=RUN))
        self.assertEqual(code, 2)
        self.assertEqual(self.states(), ['confirmed'] * 3)


REAL_READ = controller_main.read_private_json


if __name__ == '__main__':
    unittest.main()
