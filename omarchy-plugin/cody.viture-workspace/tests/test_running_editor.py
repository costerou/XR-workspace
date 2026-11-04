"""The running panel's 3D row, in the real RunningEditor.qml. It needs the shell's qs.Commons and, through VideoControls.qml,
Quickshell.Io, which qmltestrunner does not have, so this copies the panel into a temporary directory with stand-ins for those two
and runs the runner there."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

PLUGIN = Path(__file__).resolve().parents[1]
RUNNER = shutil.which('qmltestrunner') or '/usr/lib/qt6/bin/qmltestrunner'
COLOR = 'pragma Singleton\nimport QtQuick\nQtObject { property color background:"black"; property color accent:"blue"; property color muted:"gray" }\n'
VIDEO = 'import QtQuick\nItem { property var snapshot; property bool busy; signal request(var message) }\n'
TEST = '''import QtQuick
import QtTest
import "panel" as Workspace

TestCase {
    id: testCase
    name: "RunningEditor3D"
    when: windowShown
    visible: true
    width: 900; height: 860
    Component { id: running; Workspace.RunningEditor {} }
    function test_row() {
        const snapshot = {model:"luma-ultra", output:"DP-2", names:["XR-1","XR-2"], capabilities:[], view:{}, status:{},
                          immersive:{setting:{global:false, desktops:{"XR-2":true}}, names:["XR-2"], stereo:true, failed:false}};
        const editor = createTemporaryObject(running, testCase, {width:900, snapshot:snapshot, settings:{}});
        verify(editor !== null);
        const sent = [];
        editor.request.connect(m => sent.push(m));
        const all = findChild(editor, "immersiveAll"), one = findChild(editor, "immersive_XR-1"), two = findChild(editor, "immersive_XR-2");
        verify(all !== null && one !== null && two !== null);
        verify(!one.highlighted && two.highlighted && !all.highlighted);
        compare(findChild(editor, "immersiveState").text, "3D on");
        one.clicked();
        compare(sent[0].settings.immersive.desktops, {"XR-1":true, "XR-2":true});
        all.clicked();
        compare(sent[1].settings.immersive.global, false); compare(sent[1].settings.immersive.desktops, {});
    }
    function test_the_look_controls() {
        const look = {global:true, desktops:{}, preset:"desktop", strength:100, convergence:92};
        const snapshot = {model:"luma-ultra", output:"DP-2", names:["XR-1"], capabilities:[], view:{}, status:{},
                          immersive:{setting:look, names:["XR-1"], stereo:false, failed:false, phase:"glasses", installed:true}};
        const editor = createTemporaryObject(running, testCase, {width:900, snapshot:snapshot, settings:{}});
        const sent = [];
        editor.request.connect(m => sent.push(m));
        compare(findChild(editor, "immersiveState").text, "Hold R1 on the glasses");
        verify(findChild(editor, "immersivePreset_desktop").highlighted && !findChild(editor, "immersivePreset_video").highlighted);
        verify(findChild(editor, "immersiveStrength_100").highlighted);
        compare(findChild(editor, "immersivePlane").value, 92);
        findChild(editor, "immersivePreset_video").clicked();
        compare(sent[0].settings.immersive, {global:true, desktops:{}, preset:"video", strength:100, convergence:70});
        findChild(editor, "immersiveStrength_150").clicked();
        compare(sent[1].settings.immersive.strength, 150);
        compare(sent[1].settings.immersive.preset, "desktop");  // the panel sends what the run last said, not its own guess
    }
    function test_no_status_yet_reads_the_saved_setting() {
        const editor = createTemporaryObject(running, testCase, {width:900, snapshot:{names:["XR-1"], capabilities:[], status:{}},
                                                                 settings:{immersive:{global:true, desktops:{}}}});
        const sent = [];
        editor.request.connect(m => sent.push(m));
        findChild(editor, "immersive_XR-1").clicked();
        compare(sent[0].settings.immersive.global, true); compare(sent[0].settings.immersive.desktops, {"XR-1":false});
    }
}
'''


@unittest.skipUnless(os.path.exists(RUNNER), 'needs qmltestrunner')
class RunningEditorTests(unittest.TestCase):
    def test_the_3d_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            commons = Path(tmp) / 'imports' / 'qs' / 'Commons'
            commons.mkdir(parents=True)
            (commons / 'qmldir').write_text('module qs.Commons\nsingleton Color 1.0 Color.qml\n')
            (commons / 'Color.qml').write_text(COLOR)
            shutil.copytree(PLUGIN, Path(tmp) / 'panel', ignore=shutil.ignore_patterns('tests', '__pycache__'))
            (Path(tmp) / 'panel' / 'VideoControls.qml').write_text(VIDEO)
            (Path(tmp) / 'tst_running.qml').write_text(TEST)
            done = subprocess.run([RUNNER, '-input', tmp, '-import', str(Path(tmp) / 'imports')], capture_output=True, text=True, timeout=60,
                                  env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen'})
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn('Totals: 5 passed', done.stdout)  # initTestCase, the three tests, cleanupTestCase


if __name__ == '__main__':
    unittest.main()
