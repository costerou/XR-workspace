import QtQuick
import QtTest
import "../Model.js" as Model

TestCase {
    id: testCase
    name: "WorkspaceModel"
    when: windowShown
    visible:true
    width:700; height:860
    function test_scales() {
        compare(Model.validScales(1920, 1080), [1, 1.25, 1.5, 2, 2.5, 3])
        compare(Model.validScales(2560, 1440), [1, 1.25, 2, 2.5])
        compare(Model.SCALES, [1, 1.25, 1.5, 1.75, 2, 2.5, 3])
        compare(Model.validScales(3440, 1440), [1, 1.25, 2, 2.5])
        compare(Model.validScales(5120, 1440), [1, 1.25, 2, 2.5])
        compare(Model.validScales(3840, 1080), [1, 1.25, 1.5, 2, 2.5, 3])
        compare(Model.validScales(0, 1080), [])
        compare(Model.validScales(true, 1080), [])
    }
    function test_presets() {
        compare(Model.presetsFor("16:9"), [{width:1920,height:1080}, {width:2560,height:1440}])
        compare(Model.presetsFor("21:9"), [{width:2560,height:1080}, {width:3440,height:1440}])
        compare(Model.presetsFor("32:9"), [{width:3840,height:1080}, {width:5120,height:1440}])
        compare(Model.presetsFor("custom"), [])
        let copy = Model.presetsFor("16:9")
        copy[0].width = 1
        compare(Model.presetsFor("16:9")[0].width, 1920)
    }
    function test_shapes() {
        compare(Model.shapeOf(3440, 1440), "21:9")
        compare(Model.shapeOf(2560, 1080), "21:9")
        compare(Model.shapeOf(3840, 1080), "32:9")
        compare(Model.shapeOf(2560, 1440), "16:9")
        compare(Model.shapeOf(1000, 800), "custom")
    }
    function test_colors() {
        for (const state of ["running", "starting", "stopping"]) compare(Model.barColorRole(state, "DP-2"), "accent");
        for (const state of ["recovery-needed", "cli-recovery", "failed", "external-run"]) compare(Model.barColorRole(state, "DP-2"), "warning");
        compare(Model.barColorRole("idle", "DP-2"), "foreground");  // glasses seen and ready: no longer the same grey as none
        compare(Model.barColorRole("idle", null), "muted");
        for (const state of ["no-glasses", "confirming", "unknown", undefined]) compare(Model.barColorRole(state, "DP-2"), "muted");
    }
    function test_barTooltip() {
        compare(Model.barTooltip(false, {state:"running"}), "XR Workspace · service not running");
        compare(Model.barTooltip(true, {state:"no-glasses"}), "XR Workspace · no glasses");
        compare(Model.barTooltip(true, {state:"idle", model:"luma-ultra", output:"DP-2"}), "XR Workspace · Luma Ultra ready");
        compare(Model.barTooltip(true, {state:"idle", model:"unknown"}), "XR Workspace · cannot start");
        compare(Model.barTooltip(true, {state:"running", model:"beast"}), "XR Workspace · running");
        compare(Model.barTooltip(true, {state:"recovery-needed", model:"beast"}), "XR Workspace · recovery needed");
        compare(Model.barTooltip(true, {state:"cli-recovery", model:"beast"}), "XR Workspace · recovery needed");
    }
    function test_glassesName() {
        compare(Model.glassesName("luma-ultra"), "Luma Ultra");
        compare(Model.glassesName("beast"), "Beast");
        compare(Model.glassesName("pro2"), "Pro 2");
        compare(Model.glassesName("unknown"), "Glasses");
        compare(Model.glassesName(undefined), "Glasses");
    }
    function test_incidentNote() {
        compare(Model.incidentNote({}), null);
        compare(Model.incidentNote(undefined), null);
        compare(Model.incidentNote({incident:null}), null);
        compare(Model.incidentNote({incident:{kind:"usb-drop"}}), null);
        const at = new Date(2026, 10, 3, 20, 6, 12);
        compare(Model.incidentNote({incident:{summary:"the glasses' USB connection dropped and the session ended", at:at.toISOString()}}),
                {title:"Last problem · Nov 3, 20:06", text:"The glasses' USB connection dropped and the session ended."});
        compare(Model.incidentNote({incident:{summary:"the XR renderer crashed", at:"not a time"}}), {title:"Last problem", text:"The XR renderer crashed."});
    }
    function test_reasonText() {
        compare(Model.reasonText(null), null);
        compare(Model.reasonText("multiple VITURE devices").title, "Unplug all but one pair of glasses.");
        compare(Model.reasonText("XR Luma driver: head tracking driver is not installed"), Model.DRIVER_MISSING);
        compare(Model.reasonText("busy").text, "XR Workspace is busy with another step. Try again in a moment.");
        compare(Model.reasonText("something new"), {title:"", text:"something new"});  // shown as it comes
    }
    // Start is disabled exactly where begin_start would refuse (tools/xr_controller/state.py).
    function test_startBlocker() {
        const ready = {state:"idle", model:"luma-ultra", output:"DP-2", fallback:"DP-1", tracking_in_use:{key:"luma", available:true}};
        const on = {tracking:true};
        compare(Model.startBlocker(ready, on), null);
        const kind = (patch, settings) => (Model.startBlocker(Object.assign({}, ready, patch), settings || on) || {}).kind;
        compare(kind({state:"no-glasses", model:null, output:null}), "no-glasses");
        compare(kind({model:"unknown", output:null, reason:"multiple VITURE devices"}), "glasses");
        compare(kind({model:"pro2"}), "pro2-bridge");
        compare(kind({model:"pro2", pro2:{projection:46.5}}), undefined);
        compare(kind({output:null}), "output");
        compare(kind({fallback:"choose"}), "choose");
        compare(kind({fallback:null}), "no-fallback");
        compare(kind({tracking_in_use:{key:"luma", available:false}}), "driver");
        compare(kind({tracking_in_use:{key:"luma", available:false}}, {tracking:false}), undefined);  // tracking off: nothing missing
        compare(kind({state:"running"}), undefined);
    }
    function test_deviceCard() {
        compare(Model.deviceCard({}, false).status, "Offline");
        compare(Model.deviceCard({}, true).title, "No glasses connected");
        const card = Model.deviceCard({state:"idle", model:"luma-ultra", output:"DP-2", fallback:"DP-1"}, true, {});
        compare([card.title, card.sub, card.status], ["Luma Ultra", "Connected on DP-2", "Ready"]);
        compare(Model.deviceCard({state:"idle", model:"luma-ultra", output:"DP-2", fallback:"choose"}, true, {}).status, "One choice needed");
        compare(Model.deviceCard({state:"running", model:"beast", output:"DP-2"}, true, {}).tone, "accent");
        compare(Model.deviceCard({state:"idle", model:"pro2"}, true, {}).sub, "Connected · bridge not installed");
    }
    function test_sessionRows() {
        const settings = {desktops:[{width:1920,height:1080,scale:1}], tracking:true, mode_switch:true, stabilization:60};
        const luma = Model.sessionRows({model:"luma-ultra", fallback:"DP-1", tracking_in_use:{key:"luma", label:"XR Luma driver", available:true}}, settings);
        compare(luma.map(r => r.key), ["Desktops", "Head tracking", "Refresh rate", "Windows return to", "Stabilization"]);
        compare(luma.map(r => r.value), ["1 · 1920×1080 · 100%", "On · XR Luma driver", "120 Hz for this session", "DP-1", "Medium · 60%"]);
        const pro2 = Model.sessionRows({model:"pro2", fallback:"choose", pro2:{projection:46.5}}, settings);
        compare(pro2.map(r => r.key), ["Desktops", "Head tracking", "Projection", "Windows return to", "Stabilization"]);
        verify(pro2[3].warn); compare(pro2[3].value, "Not chosen");
        compare(Model.desktopsSummary([{width:1920,height:1080,scale:1}, {width:3440,height:1440,scale:1.25}]), "2 · 1920×1080, 3440×1440");
        compare(Model.stabilizationName(0), "Off"); compare(Model.stabilizationName(59), "Light"); compare(Model.stabilizationName(100), "Strong");
        compare(Model.stabilizationOf({}), 25);
    }
    function test_startSteps() {
        compare(Model.startSteps({}), []);  // an older controller: no list
        const luma = Model.startSteps({start_steps:["verify","tracking","plugin","desktops"], start_phase:"tracking", tracking_in_use:{key:"luma"}});
        compare(luma.map(s => s.status), ["done", "now", "todo", "todo"]);
        compare(luma[1].label, "Calibrating head tracking");
        compare(luma[1].note, "Keep the glasses still. Up to 30 seconds.");
        compare(luma[0].note, "");
        const pro2 = Model.startSteps({model:"pro2", start_steps:["tracking","desktops"], start_phase:"desktops"});
        compare(pro2.map(s => s.label), ["Starting head tracking", "Creating XR desktops and starting the picture"]);
        compare(pro2.map(s => s.status), ["done", "now"]);
        compare(Model.startSteps({start_steps:["verify"], start_phase:null})[0].status, "todo");
    }
    function test_confirmGroups() {
        const scope = {model:"luma-ultra", output:"DP-2", recovery_output:"DP-1", desktops:[{width:1920,height:1080,scale:1}],
                       tracking:"luma", mode_switch:{width:1920,height:1200,refresh_hz:120}};
        const groups = Model.confirmGroups(scope, "start");
        // Every scope line is still there, grouped; only the two real choices are optional.
        const lines = [].concat.apply([], groups.map(g => g.lines));
        compare(lines.length, Model.scopeLines(scope, "start").length);
        compare(lines.filter(l => l.optional).map(l => l.key).sort(), ["mode_switch", "tracking"]);
        verify(Model.confirmFooter(scope, "start").indexOf("only reduce") >= 0);
        compare(Model.confirmFooter(Object.assign({}, scope, {model:"pro2", pro2_route:{projection:46.5}}), "start"), "Nothing here is optional for the Pro 2.");
        compare(Model.confirmFooter(scope, "recover"), "All of these steps are required.");
    }
    function test_warning() {
        verify(Model.largeLayoutWarning([{width:1920,height:1080}, {width:5120,height:1440}, {width:1920,height:1080}]))
        verify(!Model.largeLayoutWarning(Array(3).fill({width:1920,height:1080})))
    }
    function test_scopeLinesNameTheTrackingDriver() {
        const scope = {model:"luma-ultra", output:"DP-2", recovery_output:"DP-1", desktops:[], tracking:"xrlinuxdriver", mode_switch:false};
        const line = s => Model.scopeLines(s, "start").filter(l => l.key === "tracking")[0];
        compare(line(scope).text, "Head tracking via XRLinuxDriver, network blocked.");
        const test = Object.assign({tracking_driver:"3dof"}, scope);
        compare(line(test).text, "Head tracking via the 3DoF test build of XRLinuxDriver, network blocked.");
        verify(line(test).optional);
    }
    function test_trackingLinesSayNothingTakesOver() {
        const luma = Model.trackingLine("luma");
        verify(luma.indexOf("XR Luma driver") >= 0); verify(luma.indexOf("does not start") >= 0); verify(luma.indexOf("XRLinuxDriver") < 0);
        verify(Model.trackingLine("pro2-sdk").indexOf("XRLinuxDriver") < 0);
    }
    function test_trackingInUseWarns() {
        verify(!Model.trackingInUseWarns(undefined));  // an older controller
        verify(!Model.trackingInUseWarns({key:"luma", label:"XR Luma driver", available:true, note:null}));
        verify(!Model.trackingInUseWarns({key:"xrdriver", label:"XRLinuxDriver", available:true, note:"Automatic: our own drivers do not support these glasses."}));
        verify(Model.trackingInUseWarns({key:"luma", label:"XR Luma driver", available:false, note:"Not installed"}));
        for (const key of ["xrdriver", "3dof"]) verify(Model.trackingInUseWarns({key:key, label:"x", available:true, note:"Recovery setting on."}));
    }
    // The running page (docs/mockups/plugin-poststart): state, one health line, and the way out when something is wrong.
    function test_runningPage() {
        const luma = {key:"luma", label:"XR Luma driver", available:true, note:null};
        const ok = {state:"running", model:"luma-ultra", output:"DP-2", fallback:"DP-1", display_mode:"1920×1200@120", uptime:840,
                    tracking_in_use:luma, status:{presented_hz:119.8, missed:3, capture_path:"dmabuf", pose_age_ms:4}};
        verify(!Model.runningProblem(ok));
        compare(Model.runningCard(ok), {title:"Luma Ultra", sub:"On DP-2 · 1920×1200@120 · 14 min", status:"Running", tone:"accent", dim:false});
        compare(Model.healthLine(ok), "Head tracking: XR Luma driver · 119.8 Hz · 3 missed frames");
        verify(Model.runningFooter(ok).indexOf("in the glasses") >= 0);
        compare(Model.runningCard({model:"beast", uptime:12}).sub, "Connected · under a minute");
        compare(Model.healthLine({status:{}}), "Head tracking: off · waiting for the first measurement");
        compare(Model.healthLine({tracking_in_use:luma, status:{presented_hz:90, missed:1, capture_failure_run:2}}),
                "Head tracking: XR Luma driver · 90 Hz · 1 missed frame · 2 capture failures in a row");
        const lost = Object.assign({}, ok, {status:{presented_hz:0, missed:212, tracking:false}});
        verify(Model.runningProblem(lost));
        compare(Model.healthLine(lost), "Head tracking: lost · 0 Hz · 212 missed frames");
        compare(Model.runningCard(lost).tone, "warn");
        compare(Model.runningFooter(lost), "If the glasses show nothing usable, Stop XR ends the session and puts your windows back on DP-1.");
        verify(Model.runningProblem(Object.assign({}, ok, {reason:"tracking reconnecting"})));
        verify(Model.runningProblem(Object.assign({}, ok, {tracking_in_use:{key:"luma", label:"XR Luma driver", available:true, note:"Recovery setting on."}})));
        verify(Model.runningFooter(Object.assign({}, lost, {fallback:"choose"})).indexOf("your other monitor") >= 0);
    }
    function test_pro2SettingsLine() {
        compare(Model.pro2SettingsLine(undefined), "");
        compare(Model.pro2SettingsLine({mount_pitch_deg:-11, pose_rate_hz:120}), "Mount pitch -11°, 120 Hz pose rate, smoothing on.");
    }
    function test_scopeLines() {
        const scope = {model:"luma-ultra", output:"DP-2", recovery_output:"DP-1",
            desktops:[{width:3840,height:1080,scale:1.25}], tracking:"xrlinuxdriver",
            mode_switch:{width:1920,height:1200,refresh_hz:120}};
        const lines = Model.scopeLines(scope, "start");
        compare(lines.filter(l => l.optional).map(l => l.key), ["tracking", "mode_switch"]);
        verify(lines.some(l => l.key === "tracking" && l.text === "Head tracking via XRLinuxDriver, network blocked."));
        // The Luma scope says nothing takes over: no calibration, no session.
        const luma = Model.scopeLines(Object.assign({}, scope, {tracking:"luma"}), "start").filter(l => l.key === "tracking");
        compare(luma.length, 1);
        verify(luma[0].optional && /XR Luma driver/.test(luma[0].text) && /XR does not start/.test(luma[0].text));
        verify(lines.some(l => l.text.indexOf("3840 × 1080 · 125%") >= 0));
        verify(lines.some(l => l.text.indexOf("DP-1") >= 0));
        verify(Model.scopeLines(scope, "recover").every(l => !l.optional));
        // A line Recover takes out of monitors.lua is part of what the click agrees to; none, no line about it.
        const rule = Model.scopeLines(scope, "recover", [{name:"XR-1", line:9, removable:true}]);
        verify(rule.some(l => l.text.indexOf("XR-1") >= 0 && l.text.indexOf("monitors.lua (line 9)") >= 0 && !l.optional));
        verify(!Model.scopeLines(scope, "recover", []).some(l => l.text.indexOf("monitors.lua") >= 0));
        // The pills can change the desktops during the session: the start scope says so, the recover scope does not
        // list the start's desktops (the run may have others by then).
        verify(lines.some(l => !l.optional && l.text.indexOf("ultrawide") >= 0 && l.text.indexOf("up to " + Model.MAX_DESKTOPS) >= 0));
        verify(Model.scopeLines(scope, "recover").some(l => l.text.indexOf("XR-" + Model.MAX_DESKTOPS) >= 0));
        compare(Model.scopeLines(Object.assign({}, scope, {model:"beast", mode_switch:false}), "start").filter(l => l.key === "mode_switch").length, 0);
    }
    function test_desktopEdit() {
        compare(Model.desktopEdit(3840,1080,1.75), null);
        compare(Model.desktopEdit(641,1080,1), null);
        compare(Model.desktopEdit(7682,1080,1), null);
        compare(Model.desktopEdit(3440,1440,1.25), {width:3440,height:1440,scale:1.25});
        compare(Model.desktopEdit(1920,1080,3), {width:1920,height:1080,scale:3});
        compare(Model.desktopEdit(1920,1080,2.25), null);
    }

    function test_pro2StartNamesItsTrackingAndApproximateProjection() {
        const scope = {model:"pro2", output:"DP-2", recovery_output:"DP-1", desktops:[], tracking:"pro2-sdk", mode_switch:false,
                       pro2_route:{projection:46.5}};
        const lines = Model.scopeLines(scope, "start");
        verify(lines.every(l => !l.optional), "nothing on a Pro 2 start can be unticked");
        verify(lines.some(l => l.text.indexOf("pinned Pro 2 SDK bridge") >= 0));
        const projection = lines.filter(l => l.text.indexOf("46.5") >= 0)[0];
        verify(projection.text.indexOf("approximate") >= 0 && projection.text.indexOf("not calibrated") >= 0, projection.text);
    }

    function test_pro2StereoConsentNamesItsOwnButtonAndObservedMode() {
        const scope = {model:"pro2", output:"DP-2", recovery_output:"DP-1", desktops:[], tracking:"pro2-sdk", mode_switch:false,
                       pro2_route:{projection:46.5}, sbs_mode:{width:3840,height:1080,refresh_hz:[60]}};
        const lines = Model.scopeLines(scope, "start");
        const stereo = lines.filter(l => l.text.indexOf("3840×1080") >= 0)[0];
        verify(stereo !== undefined);
        verify(stereo.text.indexOf("hold L1") >= 0 && stereo.text.indexOf("60 Hz") >= 0, stereo.text);
        verify(stereo.text.indexOf("R1") < 0 && stereo.text.indexOf("1200") < 0, stereo.text);
        verify(!stereo.optional);
    }
}
