"""The panel's real client test, run unchanged against the Rust controller.

`omarchy-plugin/cody.viture-workspace/tests/test_controller_client.py` drives ControllerClient.qml
in an offscreen Quickshell against the Python Server and Controller. This runs the same QML script
against `rust/crates/xr-workspace/examples/controller_socket.rs`: the Rust state machine behind
the Rust socket server, with the same offline host (fixed observation, no units, no spawns) and
the same transport cut after the second begin_start. The assertions are the original test's.

Run from the repository root:
python -m unittest tests.xr_controller.test_rust_panel_client -v
"""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[2]
PANEL = REPO / "omarchy-plugin" / "cody.viture-workspace"
RUST = REPO / "rust"


def panel_test():
    spec = importlib.util.spec_from_file_location(
        "panel_controller_client", PANEL / "tests" / "test_controller_client.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build():
    subprocess.run(["cargo", "build", "--quiet", "--offline", "-p", "xr-workspace",
                    "--example", "controller_socket"], cwd=RUST, check=True)
    return RUST / "target" / "debug" / "examples" / "controller_socket"


@unittest.skipUnless(shutil.which("quickshell") and shutil.which("cargo"),
                     "Quickshell and cargo required")
class RustClientIntegrationTests(unittest.TestCase):
    def test_real_server_replies_push_disconnect_and_reconnect(self):
        qml = panel_test().QML
        binary = build()
        with tempfile.TemporaryDirectory(prefix="xr-client-test-") as temporary:
            root = Path(temporary)
            socket_path = root / "controller.sock"
            server = subprocess.Popen([str(binary), str(socket_path)], stdin=subprocess.PIPE,
                                      stdout=subprocess.PIPE, text=True)
            try:
                self.assertEqual(server.stdout.readline(), "ready\n")
                shutil.copyfile(PANEL / "ControllerClient.qml", root / "ControllerClient.qml")
                (root / "shell.qml").write_text(qml.replace("SOCKET_PATH", str(socket_path)))
                environment = dict(os.environ, QT_QPA_PLATFORM="offscreen",
                                   QT_QPA_PLATFORMTHEME="", QT_QUICK_CONTROLS_STYLE="Basic",
                                   XDG_RUNTIME_DIR=temporary, XDG_CONFIG_HOME=temporary,
                                   XDG_STATE_HOME=temporary, XDG_CACHE_HOME=temporary)
                environment.pop("WAYLAND_DISPLAY", None)
                environment.pop("DISPLAY", None)
                result = subprocess.run(
                    ["quickshell", "-p", str(root / "shell.qml")],
                    env=environment, capture_output=True, text=True, timeout=15)
            finally:
                server.stdin.close()
                lines = server.stdout.read().splitlines()
                server.stdout.close()
                code = server.wait(timeout=5)
            self.assertEqual(code, 0, lines)
            self.assertFalse(socket_path.exists(), "socket left behind")
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output)
        self.assertIn("PASS real controller integration", output)
        self.assertNotIn("FAIL", output)
        requests = [json.loads(line.split(" ", 1)[1]) for line in lines
                    if line.startswith("request ")]
        self.assertIn("spawns 0", lines)
        self.assertEqual([m["op"] for m in requests],
                         ["status", "begin_start", "cancel", "begin_start", "status"])
        ids = [m["request_id"] for m in requests]
        self.assertEqual(ids, sorted(set(ids)))
        self.assertTrue(all(m["schema"] == 1 for m in requests))
        self.assertIn("state no-glasses", lines)


if __name__ == "__main__":
    unittest.main()
