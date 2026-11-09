"""Exercise the real panel and socket client against a slow, offline controller."""
import json
import os
from pathlib import Path
import shutil
import socketserver
import subprocess
import tempfile
import threading
import time
import unittest

from tests.xr_controller.fake_controller import fixture


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        snapshot, settings, _ = fixture("idle")
        while line := self.rfile.readline(16385):
            message = json.loads(line)
            self.server.requests.append(message["op"])
            reply = dict(snapshot, schema=1, request_id=message["request_id"], ok=True)
            if message["op"] == "get_settings":
                time.sleep(0.08)
                reply["settings"] = settings
            elif message["op"] == "refused":
                reply.update(ok=False, error="Request refused for testing")
            elif message["op"] == "change_page":
                snapshot["state"] = "failed"
            self.wfile.write((json.dumps(reply) + "\n").encode())
            if message["op"] in ("refused", "change_page"):
                # A routine telemetry push must not erase an actionable error.
                self.wfile.write((json.dumps(dict(snapshot, schema=1, event="state")) + "\n").encode())
            self.wfile.flush()


QML = '''import QtQuick
import Quickshell
import "plugin" as Plugin
ShellRoot {
    id: root
    property int step: 0
    function fail(message) { console.error("FAIL", message); Qt.quit(); }
    function find(item, name) {
        if (item.objectName === name) return item;
        for (const child of item.children || []) {
            const match = find(child, name);
            if (match) return match;
        }
        return null;
    }
    Plugin.Panel { id: panel; socketPath: "SOCKET"; Component.onCompleted: open({}) }
    Timer {
        interval: 500; running: true; repeat: true
        onTriggered: {
            if (root.step === 0) {
                if (panel.settingsId !== 0) { root.fail("settings request never settled"); return; }
                if (!panel.settings.desktops) { root.fail("settings not applied"); return; }
                panel.selectedDesktop = 0;
                root.step++;
            } else if (root.step === 1) {
                const editor = root.find(panel.renderSurface, "desktopEditor");
                if (!editor) { root.fail("desktop editor missing"); return; }
                editor.draftWidth = 1600;
                editor.draftHeight = 900;
                for (let n = 0; n < 20; n++) panel.loadSettings();
                root.step++;
            } else if (root.step === 2) {
                if (panel.settingsId !== 0) { root.fail("refresh burst did not settle"); return; }
                const editor = root.find(panel.renderSurface, "desktopEditor");
                if (editor.draftWidth !== 1600 || editor.draftHeight !== 900) {
                    root.fail("unchanged settings erased desktop draft"); return;
                }
                panel.request({op:"refused"});
                root.step++;
            } else {
                if (panel.error !== "Request refused for testing") { root.fail("telemetry erased request error"); return; }
                if (panel.busy) { root.fail("refused request left panel busy"); return; }
                console.log("PASS panel request lifecycle"); Qt.quit();
            }
        }
    }
    Timer { interval: 8000; running: true; onTriggered: root.fail("timeout"); }
}
'''

QML_MOTION = '''import QtQuick
import Quickshell
import qs.Commons as Commons
import "plugin" as Plugin
ShellRoot {
    id: root
    property var page: null
    property int step: 0
    property int ticks: 0
    property int pageChanges: 0
    property bool sawEntrance: false
    function fail(message) { console.error("FAIL", message); Qt.quit(); }
    function find(item, name) {
        if (item.objectName === name) return item;
        for (const child of item.children || []) {
            const match = find(child, name);
            if (match) return match;
        }
        return null;
    }
    Plugin.Panel { id: panel; socketPath: "SOCKET"; Component.onCompleted: { Commons.Style.reduceMotion = false; open({}); } }
    Connections {
        target: root.page
        function onItemChanged() { root.pageChanges++; }
        function onOpacityChanged() {
            if (root.step === 1 && root.page.opacity < 1)
                root.fail("telemetry replayed a page entrance");
            if (root.step === 2 && root.page.opacity > 0 && root.page.opacity < 1)
                root.sawEntrance = true;
        }
    }
    Timer {
        interval: 20; running: true; repeat: true
        onTriggered: {
            if (root.step === 0) {
                const page = root.find(panel.renderSurface, "panelPage");
                if (!panel.settings.desktops || panel.settingsId !== 0 || !page || page.opacity !== 1) return;
                root.page = page;
                root.step = 1;
                panel.request({op:"refused"});
            } else if (root.step === 1) {
                if (++root.ticks < 20) return;
                if (root.pageChanges !== 0 || root.page.opacity !== 1)
                    { root.fail("same-state telemetry replaced the page"); return; }
                if (panel.error !== "Request refused for testing")
                    { root.fail("refused telemetry not received"); return; }
                root.step = 2;
                panel.request({op:"change_page"});
            } else if (root.pageChanges === 1 && root.page.opacity === 1 && root.sawEntrance) {
                console.log("PASS panel page motion"); Qt.quit();
            }
        }
    }
    Timer { interval: 8000; running: true; onTriggered: root.fail("timeout page motion"); }
}
'''


@unittest.skipUnless(shutil.which("quickshell") and Path("/usr/share/omarchy/shell/Commons").exists(),
                     "Quickshell and Omarchy required")
class PanelRequestTests(unittest.TestCase):
    def test_coalesced_refresh_and_persistent_error(self):
        requests = self.run_panel(QML, "PASS panel request lifecycle")
        # Initial load and telemetry refresh plus one coalesced burst: bounded
        # independently of the twenty callers, with a final fresh read.
        self.assertLessEqual(requests.count("get_settings"), 5, requests)
        self.assertEqual(requests.count("refused"), 1)

    def test_page_transition_without_replaying_on_telemetry(self):
        requests = self.run_panel(QML_MOTION, "PASS panel page motion")
        self.assertEqual(requests.count("change_page"), 1)

    def run_panel(self, qml, success):
        with tempfile.TemporaryDirectory(prefix="xr-panel-requests-") as temporary:
            root = Path(temporary)
            shutil.copytree(Path(__file__).resolve().parents[1], root / "plugin",
                            ignore=shutil.ignore_patterns("tests", "__pycache__"))
            for name in ("Commons", "Ui"):
                (root / name).symlink_to(Path("/usr/share/omarchy/shell") / name)
            server = socketserver.ThreadingUnixStreamServer(str(root / "controller.sock"), Handler)
            server.daemon_threads = True
            server.requests = []
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            (root / "shell.qml").write_text(qml.replace("SOCKET", str(root / "controller.sock")))
            environment = dict(os.environ, HOME=temporary, XDG_RUNTIME_DIR=temporary,
                               XDG_CONFIG_HOME=temporary, XDG_STATE_HOME=temporary, XDG_CACHE_HOME=temporary,
                               QT_QPA_PLATFORM="offscreen", QT_QPA_PLATFORMTHEME="", QT_QUICK_CONTROLS_STYLE="Basic")
            environment.pop("WAYLAND_DISPLAY", None)
            environment.pop("DISPLAY", None)
            try:
                result = subprocess.run(["quickshell", "-p", str(root / "shell.qml")],
                                        env=environment, capture_output=True, text=True, timeout=12)
            finally:
                server.shutdown()
                server.server_close()
                worker.join(timeout=3)
            output = result.stdout + result.stderr
            self.assertEqual(result.returncode, 0, output)
            self.assertIn(success, output)
            self.assertNotIn("FAIL", output)
            return server.requests
