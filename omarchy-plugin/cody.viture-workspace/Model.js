.pragma library

// Most desktops a session can have: a glass pill's Add button stops there (tools/native_spike/geometry.py MAX_DESKTOPS;
// tests/native_spike/test_desktops.py compares them).
const MAX_DESKTOPS = 4;

// Hyprland output scales a desktop can have (tools/native_spike/geometry.py SCALES; test_scale_dropdown.py compares).
const SCALES = [1, 1.25, 1.5, 1.75, 2, 2.5, 3];

function validScales(w, h) {
    if (typeof w !== "number" || typeof h !== "number"
            || !Number.isInteger(w) || !Number.isInteger(h) || w <= 0 || h <= 0)
        return [];
    return SCALES.filter(function(s) {
        const n = s * 4;
        return w * 4 % n === 0 && h * 4 % n === 0;
    });
}

// Return fresh objects so an editor cannot mutate the preset table.
function presetsFor(shape) {
    const presets = {
        "16:9": [[1920, 1080], [2560, 1440]],
        "21:9": [[2560, 1080], [3440, 1440]],
        "32:9": [[3840, 1080], [5120, 1440]]
    };
    return (presets[shape] || []).map(function(p) {
        return {width: p[0], height: p[1]};
    });
}

function shapeOf(w, h) {
    for (const shape of ["16:9", "21:9", "32:9"]) {
        if (presetsFor(shape).some(function(p) { return p.width === w && p.height === h; }))
            return shape;
    }
    return "custom";
}

function largeLayoutWarning(desktops) {
    return desktops.reduce(function(sum, d) { return sum + d.width * d.height; }, 0) > 3 * 1920 * 1080;
}

// The glasses as the pre-start panel names them (the controller's model ids, tools/native_spike/profiles.py MODELS).
function glassesName(model) {
    return ({"luma-ultra": "Luma Ultra", "beast": "Beast", "pro2": "Pro 2"})[model] || "Glasses";
}

// The latest incident the controller saved (tools/xr_controller/incident_monitor.py), as Home's quiet history block:
// {title, text}, or null when there has been none.
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
function incidentNote(snapshot) {
    const incident = (snapshot || {}).incident;
    if (!incident || !incident.summary) return null;
    const at = new Date(incident.at);
    const pad = function(n) { return String(n).padStart(2, "0"); };
    const when = isNaN(at.getTime()) ? "" : " · " + MONTHS[at.getMonth()] + " " + at.getDate() + ", " + pad(at.getHours()) + ":" + pad(at.getMinutes());
    const text = incident.summary.charAt(0).toUpperCase() + incident.summary.slice(1);
    return {title: "Last problem" + when, text: /[.!?]$/.test(text) ? text : text + "."};
}

// The controller's reasons in the panel's own words: {title, text}. The controller strings themselves stay as they are
// (tools/xr_controller/detect.py, state.py, tracking.py LUMA_MISSING); anything not listed here is shown as it comes.
const DRIVER_MISSING = {title: "Head tracking driver is not installed.",
                        text: "XR can start with head tracking off (Session setup), or install the XR Luma driver."};
function reasonText(reason) {
    if (!reason) return null;
    const r = String(reason);
    if (r === "no glasses") return {title: "Plug in your VITURE glasses.", text: "This page updates by itself when they appear."};
    if (r === "multiple VITURE devices") return {title: "Unplug all but one pair of glasses.", text: "XR Workspace drives one pair at a time."};
    if (r === "presentation connector is not unambiguous")
        return {title: "The glasses show up on more than one display output.", text: "Unplug other VITURE displays so only one is left."};
    if (/bridge bundle is not installed/.test(r))
        return {title: "The Pro 2 bridge bundle is not installed.", text: "These glasses cannot start without it. Install it, then reopen this panel."};
    if (r === "choose a physical fallback output") return {title: "Where should your windows go when XR stops?", text: ""};
    if (/head tracking driver is not installed/i.test(r)) return DRIVER_MISSING;
    if (r === "busy") return {title: "", text: "XR Workspace is busy with another step. Try again in a moment."};
    return {title: "", text: r};
}

// Whether the head tracking a start would ask for is missing: the Luma driver not installed while head tracking is on.
// begin_start refuses that (state.py _tracking_scope), so Start is disabled; with tracking off nothing is missing.
function driverMissing(snapshot, settings) {
    const inUse = (snapshot || {}).tracking_in_use;
    return !!inUse && inUse.key === "luma" && inUse.available === false && !!(settings || {}).tracking;
}

// Why Start cannot run now, mirroring begin_start's refusals (tools/xr_controller/state.py), or null. The controller still
// refuses on its own; this only keeps the button from offering what it would refuse. {kind, tone, title, text}.
function startBlocker(snapshot, settings) {
    const s = snapshot || {};
    if (s.state !== "idle" && s.state !== "no-glasses") return null;
    function block(kind, tone, words) { return {kind: kind, tone: tone, title: words.title, text: words.text}; }
    if (!s.model) return block("no-glasses", "quiet", reasonText("no glasses"));
    if (s.model === "unknown") return block("glasses", "warn", reasonText(s.reason || "multiple VITURE devices"));
    if (s.model === "pro2" && !s.pro2) return block("pro2-bridge", "warn", reasonText("the Pro 2 bridge bundle is not installed"));
    if (!s.output) return block("output", "warn", reasonText(s.reason || "presentation connector is not unambiguous"));
    if (s.fallback === "choose") return block("choose", "warn", reasonText("choose a physical fallback output"));
    if (s.fallback === null)
        return block("no-fallback", "warn", {title: "Connect a normal monitor.",
                     text: "XR needs one besides the glasses: your windows go back to it when the session stops."});
    if (driverMissing(s, settings)) return block("driver", "warn", DRIVER_MISSING);
    return null;
}

// The device card at the top of every page but Setup and Confirm: {title, sub, status, tone, dim}.
function deviceCard(snapshot, connected, settings) {
    const s = snapshot || {};
    if (!connected) return {title: "Service not running", sub: "The panel cannot see your glasses without it.", status: "Offline", tone: "err", dim: true};
    if (!s.model) return {title: "No glasses connected", sub: "Looking for VITURE glasses…", status: "Waiting", tone: "muted", dim: true};
    if (s.model === "unknown") return {title: "VITURE glasses", sub: "More than one pair connected", status: "Cannot start", tone: "warn", dim: false};
    const title = glassesName(s.model);
    if (s.model === "pro2" && !s.output && !s.pro2)
        return {title: title, sub: "Connected · bridge not installed", status: "Cannot start", tone: "warn", dim: false};
    const sub = s.output ? "Connected on " + s.output : "Connected";
    const status = {confirming: ["Confirming", "fg"], starting: ["Starting", "warn"], stopping: ["Stopping", "warn"],
                    running: ["Running", "accent"], failed: ["Failed", "err"], "recovery-needed": ["Recovery needed", "warn"],
                    "cli-recovery": ["Manual recovery", "warn"], "external-run": ["In use", "warn"]}[s.state];
    if (status) return {title: title, sub: sub, status: status[0], tone: status[1], dim: false};
    const blocker = startBlocker(s, settings);
    if (blocker) return {title: title, sub: sub, status: blocker.kind === "choose" ? "One choice needed" : "Cannot start", tone: "warn", dim: false};
    return {title: title, sub: sub, status: "Ready", tone: "fg", dim: false};
}

// Stabilization's named stops (the strengths the old four buttons set) and the name a strength reads as: the last stop it reaches.
const STABILIZATION_STOPS = [{name: "Off", value: 0}, {name: "Light", value: 25}, {name: "Medium", value: 60}, {name: "Strong", value: 100}];
function stabilizationName(value) {
    let name = "Off";
    STABILIZATION_STOPS.forEach(function(stop) { if (value >= stop.value) name = stop.name; });
    return name;
}
function stabilizationOf(settings) {
    const v = (settings || {}).stabilization;
    return typeof v === "number" ? v : 25;
}

// "3 · 1920×1080 · 100%", or the sizes one by one when they differ.
function desktopsSummary(desktops) {
    const ds = desktops || [];
    if (!ds.length) return "—";
    const size = function(d) { return d.width + "×" + d.height; };
    const same = ds.every(function(d) { return d.width === ds[0].width && d.height === ds[0].height && d.scale === ds[0].scale; });
    return ds.length + " · " + (same ? size(ds[0]) + " · " + Math.round(ds[0].scale * 100) + "%" : ds.map(size).join(", "));
}

// Home's read-only "This session" rows: {key, value, warn}. Rows that do not apply to the connected glasses are left out.
function sessionRows(snapshot, settings) {
    const s = snapshot || {}, st = settings || {}, rows = [];
    const inUse = s.tracking_in_use || {};
    rows.push({key: "Desktops", value: desktopsSummary(st.desktops), warn: false});
    if (s.model === "pro2") rows.push({key: "Head tracking", value: "Always on · " + (inUse.label || "Pro 2 SDK bridge"), warn: false});
    else rows.push({key: "Head tracking", value: st.tracking ? "On · " + (inUse.label || "") : "Off", warn: driverMissing(s, st)});
    if (s.model === "luma-ultra") rows.push({key: "Refresh rate", value: st.mode_switch ? "120 Hz for this session" : "Unchanged", warn: false});
    if (s.model === "beast") rows.push({key: "Refresh rate", value: "Native 120 Hz", warn: false});
    if (s.model === "pro2" && s.pro2) rows.push({key: "Projection", value: s.pro2.projection + "° · approximate", warn: false});
    const fallback = s.fallback === "choose" ? "Not chosen" : s.fallback === null ? "No monitor" : s.fallback || "—";
    rows.push({key: "Windows return to", value: fallback, warn: s.fallback === "choose" || s.fallback === null});
    const strength = stabilizationOf(st);
    rows.push({key: "Stabilization", value: stabilizationName(strength) + " · " + strength + "%", warn: false});
    return rows;
}

// The start's steps for the progress page, from the controller's start_steps / start_phase (state.py _start_progress):
// [{key, label, note, status: "done" | "now" | "todo"}]. An older controller without them gives [] and the list is hidden.
function startSteps(snapshot) {
    const s = snapshot || {}, steps = s.start_steps || [];
    const now = steps.indexOf(s.start_phase);
    const luma = (s.tracking_in_use || {}).key === "luma";
    const labels = {verify: "Checking the installed files",
                    tracking: luma ? "Calibrating head tracking" : "Starting head tracking",
                    plugin: "Loading the session plugin",
                    desktops: "Creating XR desktops and starting the picture"};
    const notes = {tracking: luma ? "Keep the glasses still. Up to 30 seconds." : s.model === "pro2" ? "Pro 2 SDK bridge." : ""};
    return steps.map(function(key, i) {
        return {key: key, label: labels[key] || key, note: i === now ? notes[key] || "" : "",
                status: now < 0 ? "todo" : i < now ? "done" : i === now ? "now" : "todo"};
    });
}

// The bar mark's colour: full foreground when a start is possible, accent while a session starts or runs, warning when an
// earlier session or another one needs attention, muted otherwise (no glasses, no output, service off).
function barColorRole(state, output) {
    if (["running", "starting", "stopping"].indexOf(state) >= 0) return "accent";
    if (["recovery-needed", "cli-recovery", "failed", "external-run"].indexOf(state) >= 0) return "warning";
    if (state === "idle" && output) return "foreground";
    return "muted";
}
function barTooltip(connected, snapshot) {
    const s = snapshot || {};
    if (!connected) return "XR Workspace · service not running";
    if (!s.model) return "XR Workspace · no glasses";
    const words = {confirming: "waiting for your confirmation", starting: "starting", running: "running", stopping: "stopping",
                   "recovery-needed": "recovery needed", "cli-recovery": "recovery needed", failed: "did not start",
                   "external-run": "in use by another session"}[s.state];
    if (words) return "XR Workspace · " + words;
    return "XR Workspace · " + (s.output && s.model !== "unknown" ? glassesName(s.model) + " ready" : "cannot start");
}

function desktopEdit(width, height, scale) {
    if (!Number.isInteger(width) || !Number.isInteger(height)
            || width < 640 || width > 7680 || height < 480 || height > 4320
            || width % 2 || height % 2 || validScales(width, height).indexOf(scale) < 0)
        return null;
    return {width:width, height:height, scale:scale};
}

// The consent's `tracking` (tools/native_spike/tracking.py SCOPES) and `tracking_driver`. Nothing hands over to another driver.
function trackingLine(tracking, driver) {
    if (tracking === "pro2-sdk") return "Head tracking via the pinned Pro 2 SDK bridge, network blocked. If it stops, XR stops and your windows come back.";
    if (tracking === "luma") return "Head tracking via the XR Luma driver, network blocked. If it does not calibrate in 30 seconds, XR does not start and your windows stay where they are.";
    if (tracking && driver === "3dof") return "Head tracking via the 3DoF test build of XRLinuxDriver, network blocked.";
    return tracking ? "Head tracking via XRLinuxDriver, network blocked." : "Head tracking off (not requested).";
}

// The Pro 2's projection, always with its caveat: docs/pro2/projection-policy.md.
function projectionLine(degrees) {
    return "Projection " + degrees + " degrees diagonal, approximate: not calibrated, no optical or tilt correction.";
}
// The Pro 2's settings for the next start, in the Startup options note: changed with `pro2-settings`, not here.
function pro2SettingsLine(pro2) {
    return pro2 ? "Mount pitch " + pro2.mount_pitch_deg + "°, " + pro2.pose_rate_hz + " Hz pose rate, smoothing on." : "";
}
function projectionChoices(pro2) {
    return ((pro2 || {}).choices || []).map(function(p) { return {label: p + "° (approximate)", value: p}; });
}

function scopeLines(scope, action, rules) {
    if (!scope) return [];
    const lines = [];
    function required(text) { lines.push({text:text, optional:false, key:""}); }
    if (action === "recover") {
        required("Move windows back to " + scope.recovery_output + ".");
        // Desktops may have been added or removed in the glasses: the journal, not the start scope, lists the outputs.
        required("Remove this run's XR outputs (XR-1 to XR-" + MAX_DESKTOPS + ", whichever it still has).");
        required("Unload this run's ownership plugin.");
        // tools/native_spike/monitor_rules.py: Omarchy's scale, used on an XR desktop, wrote these; a copy of the file is kept.
        (rules || []).forEach(r => required("Take the " + r.name + " rule out of your Hyprland monitors.lua (line " + r.line + "). Omarchy's scale control added it, and a copy of the file is kept."));
        return lines;
    }
    required("Show on " + scope.model + " (" + scope.output + "). Windows return to " + scope.recovery_output + " on stop or failure.");
    required("Create XR desktops and capture only those, in memory.");
    (scope.desktops || []).forEach((d,i) => required("XR-" + (i+1) + ": " + d.width + " × " + d.height + " · " + d.scale*100 + "%"));
    required("In the glasses, a desktop's glass pill can make it ultrawide, add one (up to " + MAX_DESKTOPS + ") or remove it; its windows then return to " + scope.recovery_output + ".");
    required("Load the pinned ownership plugin for this session.");
    if (scope.pro2_route) {  // nothing optional: its tracking is the route (tools/xr_controller/pro2_route.py)
        required(trackingLine(scope.tracking));
        required(projectionLine(scope.pro2_route.projection));
    } else
        lines.push({text:trackingLine(scope.tracking, scope.tracking_driver), optional:!!scope.tracking, key:scope.tracking ? "tracking" : "", unavailable:!scope.tracking});
    if (scope.mode_switch) {
        const m = scope.mode_switch;
        lines.push({text:"Switch " + scope.output + " to " + m.width + "×" + m.height + "@" + m.refresh_hz + " for this session only.", optional:true, key:"mode_switch"});
    } else {
        required(scope.model === "beast" ? "Beast: native 120 Hz mode; no mode switch. This is not measured presentation cadence." : scope.sbs_mode ? "Keep the current display mode at startup." : "No display mode switch requested.");
    }
    if (scope.sbs_mode) {
        const button = scope.model === "pro2" ? "L1" : "R1";
        required("If you turn on 3D and hold " + button + " on the glasses: follow them to " + scope.sbs_mode.width + "×" + scope.sbs_mode.height + " (" + scope.sbs_mode.refresh_hz.join(" or ") + " Hz), and hold " + button + " again to go back to 2D. Nothing is sent to the glasses.");
    }
    return lines;
}

// The confirm page: the same scope as scopeLines, grouped under plain headings. [{label, lines:[{text, key, optional, chips}]}].
// Only the start's tracking and mode switch are optional (`key` set); they can only reduce the scope.
function confirmGroups(scope, action, rules) {
    if (!scope) return [];
    function line(text, extra) { return Object.assign({text: text, key: "", optional: false, chips: []}, extra || {}); }
    if (action === "recover") {
        const groups = [{label: "Windows", lines: [line("Move windows back to " + scope.recovery_output + ".")]},
                        {label: "Desktops", lines: [line("Remove this run's XR outputs (XR-1 to XR-" + MAX_DESKTOPS + ", whichever it still has).")]},
                        {label: "Software", lines: [line("Unload this run's ownership plugin.")]}];
        const hypr = (rules || []).map(r => line("Take the " + r.name + " rule out of your Hyprland monitors.lua (line " + r.line + "). Omarchy's scale control added it, and a copy of the file is kept."));
        if (hypr.length) groups.push({label: "Hyprland", lines: hypr});
        return groups;
    }
    const glasses = [line("Show on " + glassesName(scope.model) + " (" + scope.output + ").")];
    if (scope.mode_switch) {
        const m = scope.mode_switch;
        glasses.push(line("Switch " + scope.output + " to " + m.width + "×" + m.height + "@" + m.refresh_hz + " for this session only.", {key: "mode_switch", optional: true}));
    } else
        glasses.push(line(scope.model === "beast" ? "Beast: native 120 Hz mode; no mode switch. This is not measured presentation cadence." : scope.sbs_mode ? "Keep the current display mode at startup." : "No display mode switch requested."));
    if (scope.pro2_route) glasses.push(line(projectionLine(scope.pro2_route.projection)));
    const groups = [
        {label: "Glasses", lines: glasses},
        {label: "Desktops", lines: [line("Create XR desktops and capture only those, in memory.",
                                         {chips: (scope.desktops || []).map((d, i) => "XR-" + (i + 1) + " · " + d.width + "×" + d.height + " · " + Math.round(d.scale * 100) + "%")}),
                                    line("In the glasses, a desktop's pill can make it ultrawide, add one (up to " + MAX_DESKTOPS + ") or remove it.")]},
        {label: "Windows", lines: [line("Return to " + scope.recovery_output + " on stop or failure, and when a desktop is removed.")]},
        {label: "Tracking", lines: [scope.pro2_route ? line(trackingLine(scope.tracking))
                                    : line(trackingLine(scope.tracking, scope.tracking_driver), {key: scope.tracking ? "tracking" : "", optional: !!scope.tracking})]},
        {label: "Software", lines: [line("Load the pinned ownership plugin for this session.")]}];
    if (scope.sbs_mode) {
        const button = scope.model === "pro2" ? "L1" : "R1";
        groups.push({label: "3D", lines: [line("If you turn on 3D and hold " + button + " on the glasses: follow them to " + scope.sbs_mode.width + "×" + scope.sbs_mode.height + " (" + scope.sbs_mode.refresh_hz.join(" or ") + " Hz), and hold " + button + " again to go back to 2D. Nothing is sent to the glasses.")]});
    }
    return groups;
}
// The confirm page's footer line.
function confirmFooter(scope, action) {
    if (action === "recover") return "All of these steps are required.";
    if (scope && scope.pro2_route) return "Nothing here is optional for the Pro 2.";
    const optional = scope && (scope.tracking || scope.mode_switch);
    return optional ? "Switches can only reduce what this session does." : "Nothing here is optional for this session.";
}

// Worth a second look: the driver in use (Controller._driver_in_use) is not installed, or the recovery setting is still on.
// The automatic XRLinuxDriver of other models is neither.
function trackingInUseWarns(inUse) { return !!inUse && (!inUse.available || /Recovery/.test(inUse.note || "")); }

// The running page (docs/mockups/plugin-poststart). Everything a session can change is in the glasses; the panel keeps what
// the glasses cannot give you when their picture is unusable: the state, Stop, Recenter and what is wrong.
// Something to look at: the controller's reason, a driver that warns, or head tracking the renderer reports lost.
function runningProblem(snapshot) {
    const s = snapshot || {};
    return !!s.reason || trackingInUseWarns(s.tracking_in_use) || (s.status || {}).tracking === false;
}
// The device card while running: where, which mode, for how long.
function runningCard(snapshot) {
    const s = snapshot || {};
    const parts = [s.output ? "On " + s.output : "Connected"];
    if (s.display_mode) parts.push(s.display_mode);
    if (Number.isFinite(s.uptime)) parts.push(s.uptime < 60 ? "under a minute" : Math.floor(s.uptime / 60) + " min");
    const problem = runningProblem(s);
    return {title: glassesName(s.model), sub: parts.join(" · "), status: problem ? "Needs a look" : "Running", tone: problem ? "warn" : "accent", dim: false};
}
// One health line from the session's measurements (the glasses' Session tab has them all): head tracking, rate, missed frames.
function healthLine(snapshot) {
    const s = snapshot || {}, status = s.status || {}, inUse = s.tracking_in_use;
    const tracking = status.tracking === false ? "lost"
                   : inUse ? inUse.label + (inUse.note ? " (" + inUse.note + ")" : "") : "off";
    const parts = ["Head tracking: " + tracking];
    if (Number.isFinite(status.presented_hz)) parts.push(status.presented_hz + " Hz");
    if (Number.isFinite(status.missed)) parts.push(status.missed + (status.missed === 1 ? " missed frame" : " missed frames"));
    if (Number.isFinite(status.capture_failure_run) && status.capture_failure_run > 0)
        parts.push(status.capture_failure_run + " capture failures in a row");
    if (!Number.isFinite(status.presented_hz)) parts.push("waiting for the first measurement");
    return parts.join(" · ");
}
// The footer: where everything else is, or the way out when the glasses' picture may be unusable.
function runningFooter(snapshot) {
    const s = snapshot || {};
    if (!runningProblem(s)) return "Everything else is in the glasses: the gear beside a desktop's pill. Ctrl+Alt+H lists the keys.";
    const fallback = s.fallback && s.fallback !== "choose" ? s.fallback : "your other monitor";
    return "If the glasses show nothing usable, Stop XR ends the session and puts your windows back on " + fallback + ".";
}
