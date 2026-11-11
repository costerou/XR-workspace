"""The panel's pages (docs/mockups/plugin-prestart, and the running page of docs/mockups/plugin-poststart), one state at a time, in the real Quickshell with the Omarchy shell kit.

Each page is created on its own with a snapshot from a fixture: what it shows, whether Start (or Recover, or Cancel) is offered,
and what a click sends. qmltestrunner cannot load these pages (they use qs.Ui, which needs Quickshell), so this runs a small
shell that checks everything and prints PASS or the first FAIL. wl-copy is a stand-in that records what it was given."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

PLUGIN = Path(__file__).resolve().parents[1]

QML = r'''import QtQuick
import Quickshell
import "plugin" as Plugin
ShellRoot {
    id: root
    property var sent: []
    property int failures: 0
    function fail(message) { console.error("FAIL", message); root.failures++; }
    function check(ok, message) { if (!ok) fail(message); }
    function find(item, name) {
        if (!item) return null;
        if (item.objectName === name) return item;
        for (const child of item.children || []) {
            const match = find(child, name);
            if (match) return match;
        }
        if (item.contentItem && item.contentItem !== item) return find(item.contentItem, name);
        return null;
    }
    function shown(item, name) { const it = find(item, name); return !!it && it.visible; }
    function page(component, props) {
        const item = component.createObject(surface, Object.assign({width: 520, height: 600}, props));
        root.sent = [];
        item.request.connect(m => root.sent.push(m));
        return item;
    }
    readonly property var ready: ({state:"idle", model:"luma-ultra", output:"DP-2", fallback:"DP-1", fallback_candidates:["DP-1"],
                                   tracking_in_use:{key:"luma", label:"XR Luma driver", available:true, note:null}})
    readonly property var settings: ({desktops:[{width:1920,height:1080,scale:1},{width:3840,height:1080,scale:1}], tracking:true,
                                      mode_switch:true, stabilization:25, fallback:"DP-1"})
    function at(patch) { return Object.assign({}, ready, patch); }

    Component { id: home; Plugin.HomePage {} }
    Component { id: setup; Plugin.SetupPage {} }
    Component { id: confirm; Plugin.ConfirmPage {} }
    Component { id: progress; Plugin.ProgressPage {} }
    Component { id: recovery; Plugin.RecoveryPage {} }
    Component { id: running; Plugin.RunningPage {} }

    function testHome() {
        let p = page(home, {snapshot: ready, settings: settings});
        check(find(p, "startButton").enabled, "home: ready Start enabled");
        find(p, "startButton").clicked();
        check(JSON.stringify(sent) === JSON.stringify([{op:"begin_start"}]), "home: Start sends begin_start " + JSON.stringify(sent));
        check(!shown(p, "blockerNote") && !shown(p, "incidentNote") && !shown(p, "errorNote"), "home: ready shows no notes");
        check(shown(p, "sessionRows") && shown(p, "sessionSetup"), "home: session rows");
        check(find(p, "deviceCard").info.status === "Ready", "home: card Ready");
        let opened = 0; p.openSetup.connect(() => opened++);
        find(p, "sessionSetup").clicked(); check(opened === 1, "home: Session setup opens Setup");
        p.busy = true; check(!find(p, "startButton").enabled, "home: busy disables Start"); p.destroy();

        // Every begin_start refusal disables Start and says why.
        const blocked = {
            "no-glasses": [{state:"no-glasses", model:null, output:null}, settings],
            glasses: [at({model:"unknown", output:null, reason:"multiple VITURE devices"}), settings],
            "pro2-bridge": [at({model:"pro2", output:null}), settings],
            output: [at({output:null, reason:"presentation connector is not unambiguous"}), settings],
            choose: [at({fallback:"choose", fallback_candidates:["DP-1","HDMI-A-1"]}), settings],
            "no-fallback": [at({fallback:null, fallback_candidates:[]}), settings],
            driver: [at({tracking_in_use:{key:"luma", label:"XR Luma driver", available:false, note:"Not installed"}}), settings],
        };
        for (const kind in blocked) {
            p = page(home, {snapshot: blocked[kind][0], settings: blocked[kind][1]});
            check(!find(p, "startButton").enabled, "home: " + kind + " disables Start");
            check(shown(p, "blockerNote") && p.blocker.kind === kind, "home: " + kind + " blocker shown");
            check(shown(p, "fallbackChoice") === (kind === "choose"), "home: fallback choice only for choose (" + kind + ")");
            check(shown(p, "sessionRows") === (kind !== "no-glasses" && kind !== "glasses" && kind !== "pro2-bridge"), "home: rows for " + kind);
            if (kind === "choose") {
                find(p, "fallbackChoice").changed("HDMI-A-1");
                check(sent.length === 1 && sent[0].op === "set_settings" && sent[0].settings.fallback === "HDMI-A-1", "home: choose sends fallback");
            }
            p.destroy();
        }
        // Driver missing but head tracking off: nothing is missing, Start is offered.
        p = page(home, {snapshot: at({tracking_in_use:{key:"luma", available:false}}), settings: Object.assign({}, settings, {tracking:false})});
        check(find(p, "startButton").enabled, "home: tracking off with no driver can start"); p.destroy();

        p = page(home, {snapshot: at({incident:{summary:"head tracking stopped", at:"2026-11-03T14:02:00"}}), settings: settings, error: "busy"});
        check(shown(p, "incidentNote") && find(find(p, "incidentNote"), "noteText").text === "Head tracking stopped.", "home: incident note");
        check(shown(p, "errorNote") && find(find(p, "errorNote"), "noteText").text.indexOf("busy with another step") >= 0, "home: refusal reworded");
        let dismissed = 0; p.dismissError.connect(() => dismissed++);
        find(find(p, "errorNote"), "dismissNote").visible && find(p, "errorNote").dismissed();
        check(dismissed === 1, "home: error dismissible"); p.destroy();

        p = page(home, {snapshot: {}, settings: {}, connected: false});
        check(!find(p, "startButton").enabled && shown(p, "serviceNote") && shown(p, "startService"), "home: offline offers Start service");
        check(!shown(p, "sessionRows"), "home: offline has no rows");
        let starts = 0; p.startService.connect(() => starts++); find(p, "startService").clicked();
        check(starts === 1, "home: Start service"); p.destroy();
    }

    function testSetup() {
        let p = page(setup, {snapshot: ready, settings: settings, selected: -1});
        check(shown(p, "editorHint") && !find(p, "desktopEditor"), "setup: hint until a tile is picked");
        check(shown(p, "trackingOption") && shown(p, "modeSwitchOption") && !shown(p, "beastRate") && !shown(p, "pro2ProjectionOption"), "setup: luma rows");
        check(!shown(p, "fallbackCombo"), "setup: one candidate is plain text");
        check(shown(p, "largeLayout") === false, "setup: two desktops are not large");
        let picked = -1; p.select.connect(i => picked = i);
        find(p, "tile1").children[1].clicked(null);
        check(picked === 1, "setup: tile click selects " + picked);
        find(p, "trackingOption").toggled();
        check(sent.length === 1 && sent[0].op === "set_settings" && sent[0].settings.tracking === false, "setup: tracking switch saves " + JSON.stringify(sent));
        p.selected = 1;
        const editor = find(p, "desktopEditor");
        check(!!editor && editor.draftWidth === 3840, "setup: editor for XR-2");
        check(shown(editor, "segment:32:9"), "setup: shape segments");
        editor.commit(2560, 1080, 1);
        const last = sent[sent.length - 1];
        check(last.op === "set_settings" && last.settings.desktops.length === 2 && last.settings.desktops[1].width === 2560
              && last.settings.desktops[0].width === 1920, "setup: preset saves at once " + JSON.stringify(last));
        p.destroy();

        p = page(setup, {snapshot: at({model:"pro2", fallback_candidates:["DP-1","HDMI-A-1"],
                                       pro2:{projection:46.5, mount_pitch_deg:-8.5, pose_rate_hz:60, choices:[40,46.5,52]}}), settings: settings});
        check(!shown(p, "trackingOption") && !shown(p, "modeSwitchOption") && shown(p, "pro2ProjectionOption") && shown(p, "pro2Note"), "setup: pro2 rows");
        check(shown(p, "fallbackCombo"), "setup: two candidates give a choice");
        find(p, "pro2ProjectionCombo").changed("52");
        check(sent.length === 1 && sent[0].op === "set_pro2_projection" && sent[0].projection === 52, "setup: projection " + JSON.stringify(sent));
        p.destroy();

        p = page(setup, {snapshot: at({model:"beast", tracking_in_use:{key:"xrdriver", label:"XRLinuxDriver", available:true}}),
                         settings: Object.assign({}, settings, {desktops:[{width:5120,height:1440,scale:1},{width:3840,height:1080,scale:1},{width:3840,height:1080,scale:1}]})});
        check(shown(p, "beastRate") && !shown(p, "modeSwitchOption") && shown(p, "largeLayout"), "setup: beast rows and large layout");
        find(p, "stabilizationSlider").released(60);
        check(sent.length === 1 && sent[0].settings.stabilization === 60, "setup: stabilization " + JSON.stringify(sent));
        p.destroy();
    }

    function testConfirm() {
        const scope = {model:"luma-ultra", output:"DP-2", recovery_output:"DP-1", desktops:[{width:1920,height:1080,scale:1}],
                       tracking:"luma", mode_switch:{width:1920,height:1200,refresh_hz:120}};
        let p = page(confirm, {pending: {action:"start", scope_id:"s", scope: scope}});
        check(find(p, "confirmTitle").text === "Start XR on Luma Ultra?", "confirm: title " + find(p, "confirmTitle").text);
        check(shown(p, "tracking") && shown(p, "mode_switch"), "confirm: two switches");
        find(p, "tracking").toggled();
        find(p, "confirmScope").clicked();
        check(sent.length === 1 && sent[0].op === "confirm_start" && sent[0].scope_id === "s" && sent[0].optional.tracking === false
              && sent[0].optional.mode_switch === true, "confirm: start " + JSON.stringify(sent));
        sent = []; find(p, "cancelScope").clicked();
        check(JSON.stringify(sent) === JSON.stringify([{op:"cancel"}]), "confirm: cancel");
        p.busy = true; check(!find(p, "confirmScope").enabled && !find(p, "cancelScope").enabled, "confirm: busy");
        p.destroy();

        p = page(confirm, {pending: {action:"start", scope_id:"s", scope: Object.assign({}, scope, {model:"pro2", tracking:"pro2-sdk", mode_switch:false, pro2_route:{projection:46.5}})}});
        check(!shown(p, "tracking") && !shown(p, "mode_switch"), "confirm: pro2 has nothing optional");
        find(p, "confirmScope").clicked();
        check(sent.length === 1 && sent[0].op === "confirm_start", "confirm: pro2 start " + JSON.stringify(sent));
        p.destroy();

        p = page(confirm, {pending: {action:"recover", scope_id:"s", scope: scope}});
        check(find(p, "confirmTitle").text === "Recover the last session?" && find(p, "confirmScope").text === "Recover", "confirm: recover title");
        find(p, "confirmScope").clicked();
        check(sent.length === 1 && sent[0].op === "confirm_recover", "confirm: recover sends " + JSON.stringify(sent));
        p.destroy();

        p = page(confirm, {pending: null});
        check(shown(p, "expired") && !find(p, "confirmScope").enabled, "confirm: expired");
        find(p, "backToStart").clicked();
        check(JSON.stringify(sent) === JSON.stringify([{op:"cancel"}]), "confirm: expired goes back");
        p.destroy();
    }

    function testProgress() {
        let p = page(progress, {snapshot: at({state:"starting", run_id:"r", start_steps:["verify","tracking","plugin","desktops"], start_phase:"tracking"})});
        check(shown(p, "startSteps") && shown(p, "step:tracking") && shown(p, "cancelStart") && !shown(p, "cleanUp"), "progress: starting");
        find(p, "cancelStart").clicked();
        check(JSON.stringify(sent) === JSON.stringify([{op:"stop"}]), "progress: cancel start stops");
        p.destroy();
        p = page(progress, {snapshot: at({state:"starting", run_id:"r"})});
        check(!shown(p, "startSteps") && shown(p, "progressBar"), "progress: an older controller has no steps"); p.destroy();
        p = page(progress, {snapshot: at({state:"stopping", run_id:"r"})});
        check(!shown(p, "cancelStart") && !shown(p, "startSteps"), "progress: stopping"); p.destroy();
        p = page(progress, {snapshot: at({state:"failed", run_id:"r", reason:"head tracking did not calibrate"})});
        check(shown(p, "failureNote") && shown(p, "cleanUp") && !shown(p, "cancelStart"), "progress: failed");
        find(p, "cleanUp").clicked();
        check(JSON.stringify(sent) === JSON.stringify([{op:"stop"}]), "progress: clean up stops");
        p.destroy();
        p = page(progress, {snapshot: at({state:"failed", reason:"x"})});
        check(!shown(p, "cleanUp"), "progress: no run_id, no clean up"); p.destroy();
    }

    function testRecovery() {
        let p = page(recovery, {snapshot: at({state:"recovery-needed", run_id:"r", reason:"fallback unavailable"})});
        check(shown(p, "recoverButton") && !shown(p, "unfinishedNote") && !shown(p, "commandBox"), "recovery: needed");
        find(p, "recoverButton").clicked();
        check(JSON.stringify(sent) === JSON.stringify([{op:"begin_recover"}]), "recovery: Recover begins");
        p.busy = true; check(!find(p, "recoverButton").enabled, "recovery: busy");
        p.destroy();
        p = page(recovery, {snapshot: at({state:"recovery-needed", run_id:"r", reason:"Recover could not finish. XR-1 is still named."})});
        check(shown(p, "unfinishedNote") && find(find(p, "unfinishedNote"), "noteText").text === "XR-1 is still named.", "recovery: unfinished"); p.destroy();
        p = page(recovery, {snapshot: at({state:"cli-recovery", journal:"/run/x/journal.json"})});
        check(!shown(p, "recoverButton") && shown(p, "commandBox") && shown(p, "copyCommand") && shown(p, "copyJournal"), "recovery: cli");
        find(p, "copyJournal").clicked();
        p.destroy();
        p = page(recovery, {snapshot: at({state:"external-run", journal:"/run/y/journal.json"})});
        check(!shown(p, "recoverButton") && !shown(p, "commandBox") && shown(p, "journalBox") && shown(p, "copyJournal"), "recovery: external run"); p.destroy();
    }

    function testRunning() {
        const live = at({state:"running", display_mode:"1920×1200@120", uptime:840, capabilities:["prev","next","recenter"],
                         status:{presented_hz:119.8, missed:3}});
        let p = page(running, {snapshot: live});
        check(find(p, "deviceCard").info.status === "Running", "running: card");
        check(!shown(p, "reasonNote") && !shown(p, "incidentNote"), "running: no notes");
        check(find(p, "healthLine").text === "Head tracking: XR Luma driver · 119.8 Hz · 3 missed frames", "running: health " + find(p, "healthLine").text);
        find(p, "stopButton").clicked();
        find(p, "recenterButton").clicked();
        check(JSON.stringify(sent) === JSON.stringify([{op:"stop"}, {op:"view_op", operation:"recenter"}]), "running: Stop, Recenter " + JSON.stringify(sent));
        // Nothing else to press: every session option is in the glasses.
        for (const gone of ["runtimeEditors", "measuredStatus", "immersiveRow", "livePlacement", "stabilizationSlider"])
            check(!find(p, gone), "running: no " + gone);
        p.busy = true; check(!find(p, "stopButton").enabled && !find(p, "recenterButton").enabled, "running: busy"); p.destroy();

        p = page(running, {snapshot: at({state:"running", reason:"head tracking lost; reconnecting", status:{presented_hz:0, missed:212, tracking:false},
                                         incident:{summary:"head tracking stopped", at:"2026-11-09T14:02:00"}})});
        check(shown(p, "reasonNote") && shown(p, "incidentNote") && find(p, "deviceCard").info.tone === "warn", "running: problem notes");
        check(!shown(p, "recenterButton"), "running: no Recenter without the capability");
        check(find(p, "footerText").text.indexOf("windows back on DP-1") >= 0, "running: way out " + find(p, "footerText").text);
        check(find(p, "stopButton").enabled, "running: Stop stays"); p.destroy();
    }

    Component { id: header; Plugin.PanelHeader {} }
    Component { id: heading; Plugin.PageHeading {} }
    Component { id: text; Plugin.PanelText {} }
    Component { id: label; Plugin.SectionLabel {} }

    // The type roles read Tone's tokens: the header title is ui-strong, the heading ui-heading, its line ui-small.
    function testTokens() {
        let h = header.createObject(surface);
        const t = find(h, "pageTitle");
        check(t.font.pixelSize === 13 && t.font.weight === Font.Bold && t.text === "XR Workspace", "tokens: header title strong");
        h.destroy();
        h = heading.createObject(surface, {title: "Confirm", text: "Line"});
        check(find(h, "heading").font.pixelSize === 22 && find(h, "heading").font.weight === Font.Bold, "tokens: heading");
        h.destroy();
        h = text.createObject(surface);
        check(h.font.pixelSize === 12 && h.font.weight === Font.Normal, "tokens: body is the default role");
        h.role = "cta"; check(h.font.pixelSize === 24 && h.font.weight === Font.ExtraBold && h.font.letterSpacing === 1, "tokens: cta");
        h.role = "nonsense"; check(h.font.pixelSize === 12, "tokens: an unknown role falls back to body");
        h.destroy();
        h = label.createObject(surface);
        check(h.font.pixelSize === 10 && h.font.weight === Font.Bold, "tokens: section label is caption");
        h.destroy();
    }

    FloatingWindow {
        implicitWidth: 520; implicitHeight: 660
        Item { id: surface; anchors.fill: parent }
    }
    Timer {
        interval: 300; running: true
        onTriggered: {
            for (const test of [root.testHome, root.testSetup, root.testConfirm, root.testProgress, root.testRecovery, root.testRunning, root.testTokens]) {
                try { test(); } catch (e) { root.fail(e + " " + e.stack); }
            }
            if (root.failures === 0) console.log("PASS prestart pages");
            Qt.quit();
        }
    }
    Timer { interval: 10000; running: true; onTriggered: { root.fail("timeout"); Qt.quit(); } }
}
'''


@unittest.skipUnless(shutil.which('quickshell') and Path('/usr/share/omarchy/shell/Ui').exists(), 'Quickshell and Omarchy required')
class PrestartPageTests(unittest.TestCase):
    def test_each_page_and_state(self):
        with tempfile.TemporaryDirectory(prefix='xr-prestart-') as temporary:
            root = Path(temporary)
            shutil.copytree(PLUGIN, root / 'plugin', ignore=shutil.ignore_patterns('tests', '__pycache__'))
            for name in ('Commons', 'Ui'):
                (root / name).symlink_to(Path('/usr/share/omarchy/shell') / name)
            # The recovery page's Copy buttons run wl-copy: record what they hand it instead of touching the clipboard.
            (root / 'bin').mkdir()
            copied = root / 'copied'
            (root / 'bin' / 'wl-copy').write_text(f'#!/bin/sh\nprintf %s "$1" > {copied}\n')
            (root / 'bin' / 'wl-copy').chmod(0o755)
            (root / 'shell.qml').write_text(QML)
            environment = dict(os.environ, HOME=temporary, XDG_RUNTIME_DIR=temporary, XDG_CONFIG_HOME=temporary,
                               XDG_STATE_HOME=temporary, XDG_CACHE_HOME=temporary, QT_QPA_PLATFORM='offscreen',
                               QT_QPA_PLATFORMTHEME='', QT_QUICK_CONTROLS_STYLE='Basic',
                               PATH=str(root / 'bin') + os.pathsep + os.environ.get('PATH', ''))
            environment.pop('WAYLAND_DISPLAY', None)
            environment.pop('DISPLAY', None)
            result = subprocess.run(['quickshell', '-p', str(root / 'shell.qml')], env=environment, capture_output=True, text=True, timeout=20)
            output = result.stdout + result.stderr
            self.assertEqual(result.returncode, 0, output)
            self.assertNotIn('FAIL', output)
            self.assertIn('PASS prestart pages', output)
            for bad in ('TypeError', 'ReferenceError', 'is not a type', 'Cannot assign'):
                self.assertNotIn(bad, output)
            for _ in range(50):
                if copied.exists(): break
                subprocess.run(['sleep', '0.05'])
            self.assertEqual(copied.read_text(), '/run/x/journal.json')


if __name__ == '__main__':
    unittest.main()
