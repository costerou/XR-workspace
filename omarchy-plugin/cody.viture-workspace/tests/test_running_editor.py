"""The running panel's 3D row, in the real RunningEditor.qml. It needs the shell's qs.Commons and, through VideoControls.qml,
Quickshell.Io, which qmltestrunner does not have, so this copies the panel into a temporary directory with stand-ins for those two
and runs the runner there."""
from pathlib import Path
import os
import re
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
    Component { id: launcher; Workspace.Launcher {} }
    Component { id: notice; Workspace.SessionNotice {} }
    function test_recovery_state_gate() {
        for (const state of ["idle", "running", "starting", "stopping", "failed", "cli-recovery", "external-run", "recovery-needed"]) {
            const item = createTemporaryObject(notice, testCase, {width:900, snapshot:{state:state,run_id:"run",reason:"fallback unavailable"}});
            verify(item !== null);
            const recover = findChild(item, "recoverButton");
            compare(recover.visible, state === "recovery-needed");
            if (recover.visible) {
                const sent = []; item.request.connect(m => sent.push(m));
                mouseClick(recover); compare(sent, [{op:"begin_recover"}]);
                item.busy = true; verify(!recover.enabled);
            }
            item.destroy();
        }
    }
    function test_unverified_runtime_capability_keeps_fallback() {
        const editor = createTemporaryObject(running, testCase, {width:900, snapshot:{state:"running",model:"beast",output:"DP-2",capabilities:["options_menu"],status:{presented_hz:89.5}}, settings:{}});
        verify(editor !== null);
        verify(findChild(editor, "runtimeEditors").visible);
        editor.snapshot = {state:"running",model:"beast",output:"DP-2",capabilities:["options_runtime_complete"],status:{presented_hz:89.5}};
        verify(findChild(editor, "runtimeEditors").visible);
        verify(findChild(editor, "runtimeEditors").enabled);
        verify(findChild(editor, "measuredStatus").visible);
        compare(editor.snapshot.status.presented_hz, 89.5);
        const stop = findChild(editor, "stopButton");
        verify(stop.visible && stop.enabled);
        const sent = []; editor.request.connect(m => sent.push(m));
        mouseClick(stop); compare(sent, [{op:"stop"}]);
        editor.busy = true; verify(!stop.enabled);
        editor.snapshot = {state:"running",capabilities:[]};
        verify(findChild(editor, "runtimeEditors").visible);
    }
    function test_stop_without_renderer_or_capabilities() {
        const editor = createTemporaryObject(running, testCase, {width:900, snapshot:{state:"running"}, settings:{}});
        verify(editor !== null);
        const sent = []; editor.request.connect(m => sent.push(m));
        const stop = findChild(editor, "stopButton");
        verify(stop.visible && stop.enabled);
        mouseClick(stop); compare(sent, [{op:"stop"}]);
        verify(findChild(editor, "runtimeEditors").visible);
    }
    function test_stabilization_before_start() {
        const editor = createTemporaryObject(launcher, testCase, {width:900, snapshot:{}, settings:{stabilization:25}});
        verify(editor !== null);
        const sent = []; editor.request.connect(m => sent.push(m));
        verify(findChild(editor, "stabilization_25").highlighted);
        findChild(editor, "stabilization_100").clicked();
        compare(sent[0].settings.stabilization, 100);
        compare(findChild(editor, "startupOptions").visible, false);
    }
    function test_stabilization() {
        const editor = createTemporaryObject(running, testCase, {width:900, snapshot:{view:{stabilization:60}}, settings:{stabilization:25}});
        const sent = []; editor.request.connect(m => sent.push(m));
        const light = findChild(editor, "stabilization_25");
        verify(light !== null);
        verify(findChild(editor, "stabilization_60").highlighted);
        light.clicked(); compare(sent[0].settings.stabilization, 25);
        const slider = findChild(editor, "stabilizationSlider");
        compare(slider.value, 60);
        slider.forceActiveFocus(); keyClick(Qt.Key_Right); compare(sent[1].settings.stabilization, 61);
        editor.busy = true;
        keyClick(Qt.Key_Right); keyClick(Qt.Key_Right);
        compare(sent.length, 2);  // keep the last drag value while a reply is pending
        editor.busy = false;
        tryCompare(sent, "length", 3);
        compare(sent[2].settings.stabilization, 63);
        editor.snapshot = {view:{stabilization:0}};
        compare(slider.value, 0);
        verify(findChild(editor, "stabilization_0").highlighted);
    }
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


RELEASE_TEST = '''import QtQuick
import QtTest
import "panel" as Workspace
TestCase {
    id: testCase
    name: "ApprovedExternalSurface"
    when: windowShown
    visible: true
    width: 900; height: 860
    Component { id: running; Workspace.RunningEditor {} }
    Component { id: notice; Workspace.SessionNotice {} }
    Component { id: launcher; Workspace.Launcher {} }
    function test_approved_surface_and_capability_loss() {
        const editor = createTemporaryObject(running, testCase, {width:900,
            snapshot:{state:"running",model:"beast",output:"DP-2",names:["XR-1"],capabilities:["options_runtime_complete"],status:{presented_hz:89.5}}, settings:{}});
        verify(editor !== null);
        verify(editor.xrRuntimeComplete);
        const legacy = findChild(editor, "runtimeEditors");
        verify(!legacy.visible && !legacy.enabled);
        for (const name of ["immersiveAll", "immersiveStrengthSlider", "livePlacement"])
            verify(!findChild(editor, name).visible && !findChild(editor, name).enabled, name);
        const status = findChild(editor, "measuredStatus");
        verify(status.visible);
        verify(status.children.some(child => child.text === "Presented (Hz): 89.5"));
        const stop = findChild(editor, "stopButton");
        verify(stop.visible && stop.enabled);
        const sent = []; editor.request.connect(m => sent.push(m));
        mouseClick(stop); compare(sent, [{op:"stop"}]);
        editor.snapshot = {state:"running",capabilities:[]};
        verify(legacy.visible && legacy.enabled);
        // No renderer measurements/capabilities are needed for emergency Stop.
        verify(stop.visible && stop.enabled);
        mouseClick(stop); compare(sent.length, 2);
    }
    function test_recover_independent_of_approved_runtime() {
        const item = createTemporaryObject(notice, testCase, {width:900,snapshot:{state:"recovery-needed",run_id:"run"}});
        const recover = findChild(item, "recoverButton");
        verify(recover.visible && recover.enabled);
        const sent = []; item.request.connect(m => sent.push(m));
        mouseClick(recover); compare(sent, [{op:"begin_recover"}]);
        item.snapshot = {state:"running"}; verify(!recover.visible);
        item.snapshot = {state:"cli-recovery"}; verify(!recover.visible);
        item.snapshot = {state:"external-run",journal:"/run/journal.json"}; verify(!recover.visible);
    }
    function test_startup_preserved_in_approved_build() {
        const item = createTemporaryObject(launcher, testCase, {width:900,
            snapshot:{state:"idle",fallback:"DP-1",fallback_candidates:["DP-1"],model:"beast"},settings:{tracking:true,desktops:[]}});
        const start = findChild(item, "startButton");
        verify(start.visible && start.enabled);
        const sent = []; item.request.connect(m => sent.push(m));
        mouseClick(start); compare(sent, [{op:"begin_start"}]);
        findChild(item, "startupOptionsToggle").clicked();
        verify(findChild(item, "startupOptions").visible);
        verify(findChild(item, "trackingOption").visible);
    }
}
'''


@unittest.skipUnless(os.path.exists(RUNNER), 'needs qmltestrunner')
class RunningEditorTests(unittest.TestCase):
    def test_future_approved_external_surface(self):
        # A reviewed-build fixture in a disposable copy; never changes production approval.
        self.run_qml(RELEASE_TEST, approved=True, totals=5)

    def test_the_3d_row(self):
        self.run_qml(TEST, approved=False, totals=10)

    def run_qml(self, qml, approved, totals):
        with tempfile.TemporaryDirectory() as tmp:
            commons = Path(tmp) / 'imports' / 'qs' / 'Commons'
            commons.mkdir(parents=True)
            (commons / 'qmldir').write_text('module qs.Commons\nsingleton Color 1.0 Color.qml\n')
            (commons / 'Color.qml').write_text(COLOR)
            shutil.copytree(PLUGIN, Path(tmp) / 'panel', ignore=shutil.ignore_patterns('tests', '__pycache__'))
            (Path(tmp) / 'panel' / 'VideoControls.qml').write_text(VIDEO)
            model = Path(tmp) / 'panel' / 'Model.js'
            # Always exercise the absent-approval path, even after a future release activation.
            record = '{qa_commit:"' + 'a'*40 + '",lead_commit:"' + 'b'*40 + '",inventory_rows:116}' if approved else 'null'
            model.write_text(re.sub(r'const OPTIONS_EQUIVALENCE = .*?;',
                                    'const OPTIONS_EQUIVALENCE = ' + record + ';', model.read_text(), count=1))
            (Path(tmp) / 'tst_running.qml').write_text(qml)
            done = subprocess.run([RUNNER, '-input', tmp, '-import', str(Path(tmp) / 'imports')], capture_output=True, text=True, timeout=60,
                                  env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen'})
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn(f'Totals: {totals} passed', done.stdout)


if __name__ == '__main__':
    unittest.main()
