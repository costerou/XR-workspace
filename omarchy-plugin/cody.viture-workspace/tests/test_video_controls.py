"""Offline: the panel's VR video controls (VideoControls.qml) in offscreen Quickshell, with a stand-in file picker.

Run from the repository root:
python -m unittest discover -s omarchy-plugin/cody.viture-workspace/tests -p 'test_*.py' -v
No compositor, controller, glasses or real picker is used.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

QML = """
import QtQuick
import Quickshell
ShellRoot {
    id: shell
    property var sent: []
    property int step: 0
    function fail(message) { console.error("FAIL " + message); Qt.quit(); }
    function findText(item, name) {
        if (item.objectName === name) return item.text;
        for (const c of item.children) { const t = findText(c, name); if (t !== null) return t; }
        return null;
    }
    FloatingWindow {
        visible: true; implicitWidth: 640; implicitHeight: 400
        VideoControls {
            id: controls
            anchors.fill: parent
            snapshot: ({})
            pickerCommand: ["/bin/sh", "-c", "printf '%s\\\\n' /tmp/picked_180_LR.mp4"]
            pasteCommand: ["/bin/sh", "-c", "printf '%s' 'https://www.example.com/watch?v=42'"]
            onRequest: message => shell.sent.push(message)
        }
    }
    Timer {
        interval: 50; running: true; repeat: true
        onTriggered: {
            if (shell.step === 0) {
                controls.play("/home/me/Videos/a_360.mp4");
                controls.play("relative.mp4");
                controls.play("  ");
                controls.browse();
                shell.step = 1;
            } else if (shell.step === 1 && shell.sent.length >= 2) {
                controls.snapshot = {video: {active: true, title: "a_360.mp4", label: "360° mono", state: "playing", source: "shape",
                                             warnings: ["h264 7680x3840 decodes in software here and may stutter"], error: null}};
                for (const c of controls.controls) controls.control(c[0]);
                shell.step = 2;
            } else if (shell.step === 2) {
                if (!controls.playing) { shell.fail("playing state"); return; }
                controls.snapshot = {video: {active: false, error: "file not found"}};
                shell.step = 3;
            } else if (shell.step === 3) {
                // Web videos: a typed link, the clipboard (stand-in wl-paste) and refusals of anything else.
                controls.play("https://cdn.example.com/b_360.mp4");
                controls.playClipboard("shopping list");
                if (!controls.pasteError) { shell.fail("paste error"); return; }
                controls.playClipboard("");
                controls.paste();
                shell.step = 4;
            } else if (shell.step === 4 && shell.sent.length >= 13) {
                controls.snapshot = {video: {active: true, title: "Sunset beach", label: "180° side-by-side 3D", state: "playing", source: "player",
                                             stream: {site: "cdn.example.com", kind: "progressive", position: 3725.4, duration: 5400, seeking: false}}};
                shell.step = 5;
            } else if (shell.step === 5) {
                const text = shell.findText(controls, "videoStream");
                if (text !== "Streaming from cdn.example.com (progressive) · 1:02:05 / 1:30:00") { shell.fail("stream label " + text); return; }
                console.log("SENT " + JSON.stringify(shell.sent));
                console.log("PASS video controls");
                Qt.quit();
            }
        }
    }
    Timer { interval: 10000; running: true; onTriggered: shell.fail("timeout at step " + shell.step) }
}
"""


@unittest.skipUnless(shutil.which("quickshell"), "Quickshell runtime required")
class VideoControlsTests(unittest.TestCase):
    def test_paths_picker_and_controls(self):
        with tempfile.TemporaryDirectory(prefix="xr-video-qml-") as temporary:
            root = Path(temporary)
            shutil.copyfile(Path(__file__).resolve().parents[1] / "VideoControls.qml", root / "VideoControls.qml")
            (root / "shell.qml").write_text(QML)
            environment = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_QPA_PLATFORMTHEME="", QT_QUICK_CONTROLS_STYLE="Basic",
                               XDG_RUNTIME_DIR=temporary, XDG_CONFIG_HOME=temporary, XDG_STATE_HOME=temporary, XDG_CACHE_HOME=temporary)
            environment.pop("WAYLAND_DISPLAY", None)
            environment.pop("DISPLAY", None)
            result = subprocess.run(["quickshell", "-p", str(root / "shell.qml")], env=environment, capture_output=True,
                                    text=True, timeout=20)
            output = result.stdout + result.stderr
            self.assertIn("PASS video controls", output, output)
            self.assertNotIn("FAIL", output)
            line = next(l for l in output.splitlines() if "SENT " in l)
            sent = json.loads(line[line.index("SENT ") + 5:])
            self.assertEqual(sent[0], {"op": "video_open", "path": "/home/me/Videos/a_360.mp4"})
            self.assertEqual(sent[1], {"op": "video_open", "path": "/tmp/picked_180_LR.mp4"})  # from the picker
            self.assertEqual([m["operation"] for m in sent[2:11]],
                             ["video_pause", "video_seek_back_big", "video_seek_back", "video_seek_fwd", "video_seek_fwd_big",
                              "video_format", "video_controls", "recenter", "video_exit"])
            self.assertTrue(all(m["op"] == "view_op" for m in sent[2:11]))
            self.assertEqual(sent[11], {"op": "video_open", "url": "https://cdn.example.com/b_360.mp4"})
            self.assertEqual(sent[12], {"op": "video_open", "url": "https://www.example.com/watch?v=42"})
            self.assertEqual(len(sent), 13)  # "shopping list" and an empty clipboard send nothing
            from tools.native_spike.web_video import validate_url
            validate_url(sent[12]["url"])  # the run accepts what the panel forwards
            from tools.native_spike.video import KEY_OPS  # every button is an op the controller forwards
            from tools.native_spike.view import USER_OPS
            self.assertTrue({m["operation"] for m in sent[2:11]} <= set(KEY_OPS) | set(USER_OPS))
            self.assertEqual({m["operation"] for m in sent[2:11]} - {"recenter"}, set(KEY_OPS))
