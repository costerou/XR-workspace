import json
import os
from pathlib import Path
import socket
import tempfile
import unittest
from tools.native_spike.ipc import decode_control, ControlQueue, PrivateServer, check_peer


def wire(**kw):
    data = dict(schema=1, request_id=1, run_id="run", session="session", op="status", payload={})
    data.update(kw)
    return (json.dumps(data) + "\n").encode()

class IPCTests(unittest.TestCase):
    def test_decode_and_strict_contract(self):
        self.assertEqual(decode_control(wire())["op"], "status")
        self.assertEqual(decode_control(wire(op="select", payload={"output_id": "out"}))["payload"], {"output_id": "out"})
        for raw in (wire(schema=2), wire(schema=True), wire(request_id=0), wire(command="evil"),
                    wire(payload={"x": float("nan")}), wire(op="exec"), wire()[:-1],
                    b" " * 16384 + b"\n", b'{"schema":1,"schema":1}\n',
                    wire(op="select", payload={"output_id": "x; exec evil"})):
            with self.subTest(raw=raw[:100]):
                with self.assertRaises(ValueError): decode_control(raw)
    def test_invalid_utf8_is_a_protocol_error(self):
        with self.assertRaises(ValueError): decode_control(b"\xff\n")

    def test_queue_overflow_and_replay_session(self):
        q = ControlQueue("run", "session")
        for i in range(1, 9): q.push(wire(request_id=i))
        with self.assertRaises(ValueError): q.push(wire(request_id=9))
        self.assertEqual(q.pop()["request_id"], 1)
        with self.assertRaises(ValueError): q.push(wire(request_id=8))
        with self.assertRaises(ValueError): q.push(wire(request_id=9, session="wrong"))
        q.push(wire(request_id=9))
    def test_socket_rejects_wrong_uid_symlink_duplicate_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp)
            with PrivateServer(runtime, "run", "session") as server:
                self.assertEqual(server.path.stat().st_mode & 0o777, 0o600)
                self.assertEqual(server.path.parent.stat().st_mode & 0o777, 0o700)
                client = socket.socket(socket.AF_UNIX); self.addCleanup(client.close)
                client.connect(str(server.path)); conn, _ = server.socket.accept(); self.addCleanup(conn.close)
                check_peer(conn, os.getuid())
                with self.assertRaises(PermissionError): check_peer(conn, os.getuid() + 1)
                client.sendall(wire())
                self.assertEqual(server.receive(conn)["op"], "status")
                client.sendall(b'{"schema":1,"schema":1}\n')
                with self.assertRaises(ValueError): server.receive(conn)
            (runtime / "xr-workspace-spike" / "other").symlink_to(runtime)
            with self.assertRaises((ValueError, OSError)):
                PrivateServer(runtime, "other", "session")
    def test_socket_partial_and_multiple_frames(self):
        with tempfile.TemporaryDirectory() as tmp:
            with PrivateServer(Path(tmp), "run", "session") as server:
                a, b = socket.socketpair(); self.addCleanup(a.close); self.addCleanup(b.close)
                a.sendall(wire()[:-1]); a.shutdown(socket.SHUT_WR)
                with self.assertRaises(ValueError): server.receive(b)
