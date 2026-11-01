"""Offline integration: real controller/server, injected adapters, offscreen QML.

Run from the repository root:
python -m unittest discover -s omarchy-plugin/cody.viture-workspace/tests -p 'test_*.py' -v
No compositor, hardware, real runtime directory, or systemd is used.
"""
import copy
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import unittest

from tools.native_spike.profiles import DEFAULT_PROFILE
from tools.xr_controller.server import Server
from tools.xr_controller.state import Controller


class Store:
    def load(self, model):
        return copy.deepcopy(DEFAULT_PROFILE)


class Units:
    def alive(self, run_id):
        raise AssertionError("unexpected unit lookup")

    def control(self, *args):
        raise AssertionError("unexpected run control")


@unittest.skipUnless(shutil.which("quickshell"), "Quickshell runtime required")
class ClientIntegrationTests(unittest.TestCase):
    def test_real_server_replies_push_disconnect_and_reconnect(self):
        with tempfile.TemporaryDirectory(prefix="xr-client-test-") as temporary:
            root = Path(temporary)
            fd = os.open(root, os.O_DIRECTORY)
            self.addCleanup(os.close, fd)
            observation = dict(
                model="beast", output="DP-2", reason=None, instance="offline",
                monitors=[dict(name="DP-2"), dict(name="DP-1")],
                journals=[], plugins=[],
                installed={key: dict(path="/fake/" + key, sha256="a" * 64)
                           for key in ("plugin", "renderer")})
            spawns, requests = [], []
            controller = Controller(time.monotonic, lambda: copy.deepcopy(observation),
                                    lambda *args: spawns.append(args), Units(), Store(), fd, root)
            original_request = controller.request

            def request(message):
                requests.append(copy.deepcopy(message))
                return original_request(message)

            controller.request = request
            server = Server(controller, root / "controller.sock")
            stop = threading.Event()
            errors = []

            def serve():
                try:
                    dropped = False
                    while not stop.is_set():
                        server.step(0.01)
                        # Cut the transport after a second real begin_start request was
                        # handled, before its queued reply is written. Mutating
                        # requests must never be replayed across this boundary.
                        if not dropped and sum(m["op"] == "begin_start" for m in requests) == 2:
                            for client in list(server.clients):
                                server._drop(client)
                            observation.update(model=None, output=None)
                            controller.tick()
                            dropped = True
                except BaseException as exc:
                    errors.append(exc)
                finally:
                    server.close()

            thread = threading.Thread(target=serve)
            thread.start()
            source = Path(__file__).resolve().parents[1] / "ControllerClient.qml"
            shutil.copyfile(source, root / "ControllerClient.qml")
            (root / "shell.qml").write_text(QML.replace("SOCKET_PATH", str(server.path)))
            environment = dict(os.environ, QT_QPA_PLATFORM="offscreen",
                               QT_QPA_PLATFORMTHEME="", QT_QUICK_CONTROLS_STYLE="Basic",
                               XDG_RUNTIME_DIR=temporary, XDG_CONFIG_HOME=temporary,
                               XDG_STATE_HOME=temporary, XDG_CACHE_HOME=temporary)
            environment.pop("WAYLAND_DISPLAY", None)
            environment.pop("DISPLAY", None)
            try:
                result = subprocess.run(
                    ["quickshell", "-p", str(root / "shell.qml")],
                    env=environment, capture_output=True, text=True, timeout=15)
            finally:
                stop.set()
                thread.join(timeout=3)
            self.assertFalse(thread.is_alive())
            self.assertFalse(errors, errors)
            output = result.stdout + result.stderr
            self.assertEqual(result.returncode, 0, output)
            self.assertIn("PASS real controller integration", output)
            self.assertNotIn("FAIL", output)
            self.assertEqual(spawns, [])
            self.assertEqual([m["op"] for m in requests],
                             ["status", "begin_start", "cancel", "begin_start", "status"])
            ids = [m["request_id"] for m in requests]
            self.assertEqual(ids, sorted(set(ids)))
            self.assertTrue(all(m["schema"] == 1 for m in requests))
            self.assertEqual(controller.snapshot()["state"], "no-glasses")


QML = """
import QtQuick
import Quickshell
ShellRoot {
    property int phase: 0
    property bool confirmedReply: false
    property bool cancelledReply: false
    property int pendingId: -1
    function fail(message: string): void { console.error("FAIL", message); Qt.quit(); }
    ControllerClient {
        id: client
        socketPath: "SOCKET_PATH"
        onReplied: (id, reply) => {
            if (phase === 0 && reply.ok && reply.state === "idle") {
                if (snapshot.model !== "beast") { fail("initial snapshot"); return; }
                phase = 1;
                client.send({op:"begin_start"});
            } else if (phase === 1 && reply.ok && reply.scope_id) {
                if (reply.scope.output !== "DP-2") { fail("scope reply"); return; }
                confirmedReply = true;
            } else if (phase === 2 && reply.ok && reply.state === "idle") {
                cancelledReply = true;
            } else if (phase === 3 && id === pendingId && !reply.ok) {
                if (reply.error !== "controller not running"
                        || Object.keys(snapshot).length !== 0
                        || Object.keys(pending).length !== 0) {
                    fail("disconnect did not clear pending state"); return;
                }
                phase = 4;
            } else if (phase === 4 && reply.ok && reply.state === "no-glasses") {
                if (snapshot.model !== null || !connected) {
                    fail("reconnected snapshot"); return;
                }
                console.log("PASS real controller integration");
                Qt.quit();
            } else { fail("unexpected reply in phase " + phase); }
        }
        onStateChanged: {
            if (phase === 1 && snapshot.state === "confirming") {
                if (!confirmedReply || snapshot.model !== "beast") {
                    fail("confirmation push"); return;
                }
                phase = 2;
                client.send({op:"cancel"});
            } else if (phase === 2 && snapshot.state === "idle") {
                if (!cancelledReply) { fail("cancel push"); return; }
                phase = 3;
                pendingId = client.send({op:"begin_start"});
            }
        }
    }
    Timer {
        interval: 12000; running: true
        onTriggered: fail("timeout phase " + phase)
    }
}
"""
