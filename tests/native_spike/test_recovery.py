import copy
from pathlib import Path
import tempfile
import unittest
from tools.native_spike.journal import Journal, recover
from tools.native_spike.supervisor import Supervisor

ID = "12345678-1234-4234-8234-123456789abc"
NAME = "XR-" + ID

class FakeHypr:
    def __init__(self):
        self.outputs = [{"id": "physical", "name": "DP-1", "physical": True},
                        {"id": ID, "name": NAME, "physical": False}]
        self.workspaces = [{"id": 1, "output": NAME}]
        self.settings = {"input:follow_mouse": 0}
        self.commands = []
        self.fail_migration = False
        self.session = "session"
    def query(self, kind):
        return copy.deepcopy({"session": {"id": self.session}, "outputs": self.outputs,
                              "workspaces": self.workspaces, "settings": self.settings}[kind])
    def mutate(self, command) -> str:
        self.commands.append(command)
        fields = command.split()
        if fields[0] == "keyword": self.settings[fields[1]] = int(fields[2])
        elif fields[:2] == ["dispatch", "moveworkspacetomonitor"]:
            if not self.fail_migration:
                for ws in self.workspaces:
                    if ws["id"] == int(fields[2]): ws["output"] = fields[3]
        elif fields[:2] == ["output", "remove"]:
            self.outputs = [o for o in self.outputs if o["name"] != fields[2]]
        return "ok"

class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.j = Journal.open(Path(self.tmp.name) / "journal.json", "run", "session")
        self.hypr = FakeHypr()
    def output(self):
        return self.j.prepare({"kind": "output", "id": ID, "name": NAME, "before": None})
    def test_crash_after_prepare_before_confirm(self):
        self.output()
        result = recover(self.j, self.hypr)
        self.assertTrue(result.complete)
        self.assertEqual(self.hypr.workspaces[0]["output"], "DP-1")
        self.assertEqual(len(self.hypr.outputs), 1)
        calls = list(self.hypr.commands)
        self.assertTrue(recover(self.j, self.hypr).complete)
        self.assertEqual(calls, self.hypr.commands)
    def test_recovery_preserves_user_change(self):
        self.output()
        self.j.prepare({"kind": "workspace", "workspace": 1, "before": "DP-1", "applied": NAME})
        self.j.prepare({"kind": "setting", "key": "input:follow_mouse", "before": 1, "applied": 0})
        self.hypr.workspaces[0]["output"] = "DP-USER"
        self.hypr.settings["input:follow_mouse"] = 2
        self.assertTrue(recover(self.j, self.hypr).complete)
        self.assertEqual(self.hypr.workspaces[0]["output"], "DP-USER")
        self.assertEqual(self.hypr.settings["input:follow_mouse"], 2)
    def test_unknown_output_not_removed(self):
        oid = self.output(); self.hypr.outputs[1]["id"] = "other"
        self.assertEqual(recover(self.j, self.hypr).remaining, [oid])
        self.assertEqual(self.hypr.commands, [])
    def test_no_physical_fallback_preserves_resources(self):
        self.output(); self.hypr.outputs.pop(0)
        self.assertFalse(recover(self.j, self.hypr).complete)
        self.assertEqual(self.hypr.commands, [])
    def test_shutdown_mid_migration(self):
        self.output(); self.hypr.fail_migration = True
        self.assertFalse(recover(self.j, self.hypr).complete)
        self.assertFalse(any(c.startswith("output remove") for c in self.hypr.commands))
        self.hypr.fail_migration = False
        self.assertTrue(recover(self.j, self.hypr).complete)
    def test_disconnect_or_wrong_session_fails_closed(self):
        self.output(); self.hypr.session = "new-session"
        self.assertFalse(recover(self.j, self.hypr).complete)
        self.assertEqual(self.hypr.commands, [])
    def test_disconnected_output_with_stranded_workspace_is_incomplete(self):
        self.output(); self.hypr.outputs.pop()
        self.assertFalse(recover(self.j, self.hypr).complete)
        self.assertEqual(self.hypr.commands, [])

    def test_user_move_during_migration_is_preserved(self):
        self.output()
        original = self.hypr.query
        count = 0
        def query(kind):
            nonlocal count
            if kind == "workspaces":
                count += 1
                if count == 2: self.hypr.workspaces[0]["output"] = "USER"
            return original(kind)
        self.hypr.query = query
        self.assertTrue(recover(self.j, self.hypr).complete)
        self.assertEqual(self.hypr.workspaces[0]["output"], "USER")
        self.assertFalse(any("moveworkspace" in c for c in self.hypr.commands))

    def test_recovery_never_reports_complete_after_durability_failure(self):
        from unittest.mock import patch
        self.output()
        with patch("os.fsync", side_effect=OSError("disk error")):
            with self.assertRaises(OSError): recover(self.j, self.hypr)
        self.assertEqual(Journal.open(self.j.path, "run", "session").operations[0]["state"], "pending")
        self.assertTrue(recover(self.j, self.hypr).complete)

    def test_mutation_error_keeps_durable_pending(self):
        self.output()
        self.hypr.mutate = lambda command: "error"
        self.assertFalse(recover(self.j, self.hypr).complete)
        self.assertEqual(Journal.open(self.j.path, "run", "session").operations[0]["state"], "pending")

    def test_setting_readback_and_restore(self):
        self.j.prepare({"kind": "setting", "key": "input:follow_mouse", "before": 1, "applied": 0})
        self.assertTrue(recover(self.j, self.hypr).complete)
        self.assertEqual(self.hypr.settings["input:follow_mouse"], 1)

class FakeChild:
    def __init__(self): self.signals = []; self.dead = False
    def poll(self): return 0 if self.dead else None
    def terminate(self): self.signals.append("TERM")
    def kill(self): self.signals.append("KILL"); self.dead = True

class SupervisorTests(unittest.TestCase):
    def test_unreaped_child_is_killed_once(self):
        class SlowChild(FakeChild):
            def kill(self): self.signals.append("KILL")
        child = SlowChild()
        s = Supervisor(child, lambda: None, now=0)
        for now in (2, 3, 4, 5): s.tick(now)
        self.assertEqual(child.signals, ["TERM", "KILL"])

    def test_run_with_real_synthetic_child_timeout(self):
        import socket
        import subprocess
        import sys
        import time
        child = subprocess.Popen([sys.executable, "-u", "-c",
            "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print('ready', flush=True); time.sleep(30)"],
            stdout=subprocess.PIPE, text=True)
        assert child.stdout is not None
        self.addCleanup(child.stdout.close)
        self.addCleanup(lambda: child.poll() is None and child.kill())
        self.assertEqual(child.stdout.readline().strip(), "ready")
        a, b = socket.socketpair(); self.addCleanup(a.close); self.addCleanup(b.close)
        recovered = []
        s = Supervisor(child, lambda: recovered.append(child.poll()), grace=0.05)
        started = time.monotonic()
        s.run(b)
        self.assertGreaterEqual(time.monotonic() - started, 2)
        self.assertEqual(child.wait(timeout=1), -9)
        self.assertEqual(recovered, [-9])

    def test_run_heartbeat_eof_recovers(self):
        import socket
        a, b = socket.socketpair(); self.addCleanup(b.close)
        a.close()
        child = FakeChild(); recovered = []
        Supervisor(child, lambda: recovered.append(True), grace=0).run(b)
        self.assertEqual(child.signals, ["TERM", "KILL"])
        self.assertEqual(recovered, [True])

    def test_heartbeat_timeout_kills_only_recorded_child_then_recovers(self):
        child = FakeChild(); unrelated = FakeChild(); recovered = []
        supervisor = Supervisor(child, lambda: recovered.append(True), now=0, grace=0.5)
        supervisor.tick(1.99)
        self.assertEqual(child.signals, [])
        supervisor.tick(2)
        self.assertEqual(child.signals, ["TERM"])
        self.assertEqual(recovered, [])
        supervisor.tick(2.5)
        self.assertEqual(child.signals, ["TERM", "KILL"])
        self.assertEqual(recovered, [True])
        supervisor.tick(3)
        self.assertEqual(recovered, [True]); self.assertEqual(unrelated.signals, [])
    def test_exit_disconnect_and_heartbeat(self):
        for reason in ("exit", "disconnect"):
            child = FakeChild(); recovered = []
            s = Supervisor(child, lambda: recovered.append(True), now=0)
            s.heartbeat(1.5); s.tick(3)
            self.assertEqual(child.signals, [])
            if reason == "exit": child.dead = True
            else: s.disconnect(3)
            s.tick(4)
            self.assertEqual(recovered, [True])
