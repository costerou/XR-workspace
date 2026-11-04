.pragma library

// Release authority is a reviewed build artifact, never a field from runtime status.
// Lead may populate {qa_commit, lead_commit, inventory_rows:116} only after both
// reviews accept functional equivalence for every inventory row. Null keeps fallback.
const OPTIONS_EQUIVALENCE = null;
function optionsRuntimeComplete(snapshot) {
    const evidence = OPTIONS_EQUIVALENCE;
    return !!evidence && evidence.inventory_rows === 116
        && /^[0-9a-f]{40}$/.test(evidence.qa_commit || "")
        && /^[0-9a-f]{40}$/.test(evidence.lead_commit || "")
        && ((snapshot || {}).capabilities || []).indexOf("options_runtime_complete") >= 0;
}

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

// Pixel aspect determines apparent size; desktop scale does not change it.
function previewRects(desktops, width, height, spacing) {
    if (!desktops.length || !Number.isFinite(width) || !Number.isFinite(height)
            || !Number.isFinite(spacing) || width <= 0 || height <= 0 || spacing < 0)
        return [];
    if (desktops.some(function(d) { return !d || !Number.isFinite(d.width)
            || !Number.isFinite(d.height) || d.width <= 0 || d.height <= 0; }))
        return [];
    const available = width - spacing * (desktops.length - 1);
    if (available <= 0) return [];
    const aspects = desktops.map(function(d) { return d.width / d.height; });
    const totalAspect = aspects.reduce(function(a, b) { return a + b; }, 0);
    const tileHeight = Math.min(height, available / totalAspect);
    let x = (width - tileHeight * totalAspect - spacing * (desktops.length - 1)) / 2;
    return aspects.map(function(aspect) {
        const rect = {x: x, y: (height - tileHeight) / 2,
                      width: tileHeight * aspect, height: tileHeight};
        x += rect.width + spacing;
        return rect;
    });
}

function largeLayoutWarning(desktops) {
    return desktops.reduce(function(sum, d) { return sum + d.width * d.height; }, 0) > 3 * 1920 * 1080;
}

// The glasses as the pre-start panel names them (the controller's model ids, tools/native_spike/profiles.py MODELS).
function glassesName(model) {
    return ({"luma-ultra": "Luma Ultra", "beast": "Beast"})[model] || "Glasses";
}

// The one line under Start: which glasses and which of their outputs the session would use.
function startStatus(snapshot) {
    const s = snapshot || {};
    return glassesName(s.model) + (s.output ? " on " + s.output : "");
}

// The latest incident the controller saved (tools/xr_controller/incident_monitor.py), one line: "" when there has been none.
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
function incidentLine(snapshot) {
    const incident = (snapshot || {}).incident;
    if (!incident || !incident.summary) return "";
    const at = new Date(incident.at);
    const pad = function(n) { return String(n).padStart(2, "0"); };
    const when = isNaN(at.getTime()) ? "" : " (" + MONTHS[at.getMonth()] + " " + at.getDate() + ", " + pad(at.getHours()) + ":" + pad(at.getMinutes()) + ")";
    return "Last problem" + when + ": " + incident.summary + ".";
}

function barColorRole(state) {
    if (state === "running") return "accent";
    if (["recovery-needed", "cli-recovery", "failed"].indexOf(state) >= 0) return "warning";
    return "muted";
}

function desktopEdit(width, height, scale) {
    if (!Number.isInteger(width) || !Number.isInteger(height)
            || width < 640 || width > 7680 || height < 480 || height > 4320
            || width % 2 || height % 2 || validScales(width, height).indexOf(scale) < 0)
        return null;
    return {width:width, height:height, scale:scale};
}

// The consent's `tracking` (tools/native_spike/tracking.py SCOPES) and `tracking_driver`. The Luma scope covers the handover
// too: the click agrees to it. The 3DoF test never hands over.
function trackingLine(tracking, driver) {
    if (tracking === "luma") return "Head tracking via the XR Luma driver, network blocked. If it does not calibrate, XRLinuxDriver takes over, also network blocked.";
    if (tracking && driver === "3dof") return "Head tracking via the 3DoF test build of XRLinuxDriver, network blocked.";
    return tracking ? "Head tracking via XRLinuxDriver, network blocked." : "Head tracking off (not requested).";
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
    lines.push({text:trackingLine(scope.tracking, scope.tracking_driver), optional:!!scope.tracking, key:scope.tracking ? "tracking" : "", unavailable:!scope.tracking});
    if (scope.mode_switch) {
        const m = scope.mode_switch;
        lines.push({text:"Switch " + scope.output + " to " + m.width + "×" + m.height + "@" + m.refresh_hz + " for this session only.", optional:true, key:"mode_switch"});
    } else {
        required(scope.model === "beast" ? "Beast: native 120 Hz mode; no mode switch. This is not measured presentation cadence." : "No display mode switch requested.");
    }
    if (scope.sbs_mode) required("If you turn on 3D and hold R1 on the glasses: follow them to " + scope.sbs_mode.width + "×" + scope.sbs_mode.height + " (" + scope.sbs_mode.refresh_hz.join(" or ") + " Hz), and hold R1 again to go back to 2D. Nothing is sent to the glasses.");
    return lines;
}

// The running session's view for the panel (maximize-follow, 2026-11-03): the overview is always anchored; a maximized
// desktop follows you or is locked to your view. `view` is the supervisor's view payload ({} before the first status).
function viewLabel(view) {
    if (!view || view.mode === undefined) return "awaiting measured state";
    return view.zoomed ? "Maximized · " + view.mode : "Overview · anchor";
}

// Panel button text for a run capability (an operation the supervisor advertises); unknown ones show their name.
const OPERATION_LABELS = {prev:"◀ Prev", next:"Next ▶", zoom_in:"Maximize", zoom_out:"Overview", recenter:"Recenter",
                          mode_next:"Maximized: follow / locked", spacing_up:"Spacing +", spacing_down:"Spacing −",
                          prediction:"Pose prediction"};
function operationLabel(op) { return OPERATION_LABELS[op] || op; }

// Immersive 3D (tools/native_spike/immersive.py, the same rules): {global, desktops, preset, strength, convergence}, `desktops` the exceptions by XR-n name.
// The panel sends the whole setting; the supervisor applies and saves it, and the next status shows it.
function immersiveOn(setting, name) {
    if (!setting) return false;
    const exceptions = setting.desktops || {};
    return exceptions[name] !== undefined ? exceptions[name] : !!setting.global;
}
// All desktops: anything 3D goes flat, all of it; nothing 3D, everything goes 3D.
function immersiveAll(setting, names) {
    return Object.assign({}, setting, {global:!names.some(n => immersiveOn(setting, n)), desktops:{}});
}
// One desktop the other way, the rest as they are. An exception that says what `global` says is dropped.
function immersiveDesktop(setting, name) {
    const every = !!(setting && setting.global), exceptions = Object.assign({}, (setting && setting.desktops) || {});
    if (immersiveOn(setting, name) === every) exceptions[name] = !every;
    else delete exceptions[name];
    return Object.assign({}, setting, {global:every, desktops:exceptions});
}
// The panel's look controls; a preset brings its own screen plane (immersive.with_preset). Soft, Standard, Enhanced are VITURE's names.
const IMMERSIVE_PRESETS = [{key:"desktop", label:"Desktop", convergence:92}, {key:"video", label:"Video", convergence:70}, {key:"games", label:"Games", convergence:75}];
const IMMERSIVE_STRENGTHS = [{value:50, label:"Soft"}, {value:100, label:"Standard"}, {value:150, label:"Enhanced"}];
function immersivePreset(setting, key) {
    const preset = IMMERSIVE_PRESETS.find(p => p.key === key);
    return preset ? Object.assign({}, setting, {preset:key, convergence:preset.convergence}) : setting;
}
function immersiveStrength(setting, value) { return Object.assign({}, setting, {strength:Math.round(value)}); }
function immersiveConvergence(setting, value) { return Object.assign({}, setting, {convergence:Math.round(value)}); }
// The line beside the 3D buttons, from the run's `immersive` status.
function immersiveLabel(status) {
    if (!status) return "";
    if (status.failed) return "Fell back to 2D: press again to retry";
    if (status.stereo) return "3D on";
    if (!(status.names || []).length) return "Off";
    if (status.installed === false) return "Depth engine not installed";
    if (status.phase === "engine") return "Loading the depth model…";
    if (status.phase === "glasses") return "Hold R1 on the glasses";
    return "Starting…";
}

// Translate a preview click into the existing relative operations. The caller
// serializes replies, stops on refusal/disconnect, and never changes view locally.
function selectOperations(selected, target, capabilities) {
    if (!Number.isInteger(selected) || !Number.isInteger(target)
            || selected < 0 || selected >= MAX_DESKTOPS || target < 0 || target >= MAX_DESKTOPS) return [];
    const operation = target > selected ? "next" : "prev";
    if (capabilities.indexOf(operation) < 0) return [];
    return Array(Math.abs(target-selected)).fill(operation);
}

// Per-desktop placement: integer fields, mirrored from tools/native_spike/geometry.py PLACEMENT_BOUNDS
// (tests/native_spike/test_placement.py compares the tables). Presentation only; editable while running.
// x / y turn the desktop round you in tenths of a degree (tools/native_spike/orbit.py): x to the right (+-1800 is behind
// you), y up (900 straight overhead). Distance is whole percent, curvature whole degrees. Every desktop always faces
// you, upright, so there is no facing setting (the turning angles were removed on 2026-11-02: a placement that still
// carries them is refused here; older saved profiles load without them, tools/native_spike/profiles.py).
const PLACEMENT_BOUNDS = {x: [-1800, 1800], y: [-900, 900], distance: [10, 200], curvature: [0, 120]};
const PLACEMENT_KEYS = ["x", "y", "distance", "curvature"];
const PLACEMENT_UNITS = {x: "°", y: "°", distance: "%", curvature: "°"};

// Slider labels, shared by the editor.
const PLACEMENT_LABELS = {x: "Left / right", y: "Down / up", distance: "Distance", curvature: "Curvature"};

function placementLabel(key) { return PLACEMENT_LABELS[key] || ""; }

function placementBounds(key) { return (PLACEMENT_BOUNDS[key] || [0, 0]).slice(); }

// A field's value as the editor shows it: "12.5°" / "-3°" for the orbit's tenths, "100%", "20°". "" for a value that
// is not an integer or a key that is not a placement field. -0 shows as "0°" (String(-0) is "0"; v < 0 is false).
function placementValueText(key, v) {
    if (!Number.isInteger(v) || !Object.prototype.hasOwnProperty.call(PLACEMENT_UNITS, key)) return "";
    if (key !== "x" && key !== "y") return v + PLACEMENT_UNITS[key];
    const a = Math.abs(v);
    return (v < 0 ? "-" : "") + Math.floor(a / 10) + (a % 10 ? "." + (a % 10) : "") + "°";
}

function defaultPlacement() { return {x:0, y:0, distance:100, curvature:0}; }

// A fresh, exact-key placement if every field is an in-range integer, else null.
function placementEdit(p) {
    if (!p || typeof p !== "object" || Object.keys(p).length !== PLACEMENT_KEYS.length) return null;
    const out = {};
    for (const key of PLACEMENT_KEYS) {
        const v = p[key], b = PLACEMENT_BOUNDS[key];
        if (!Number.isInteger(v) || v < b[0] || v > b[1]) return null;
        out[key] = v;
    }
    return out;
}

function isFlat(p) {
    const d = defaultPlacement();
    return !!p && PLACEMENT_KEYS.every(function(k) { return p[k] === d[k]; });
}

// One placement per desktop (`count`, default: as many as given, else 3) with entry `index` replaced; missing/invalid
// entries become flat.
function withPlacement(placements, index, placement, count) {
    const n = Number.isInteger(count) && count >= 1 && count <= MAX_DESKTOPS ? count
            : Math.min(Math.max((placements || []).length, 3), MAX_DESKTOPS);
    const out = [];
    for (let i = 0; i < n; ++i) out.push(placementEdit((placements || [])[i]) || defaultPlacement());
    if (Number.isInteger(index) && index >= 0 && index < n && placementEdit(placement)) out[index] = placementEdit(placement);
    return out;
}

// A desktop's tile label: its XR-n name (`names` from a running session, where a removed desktop leaves a gap), else
// XR-(index+1).
function desktopName(names, index) {
    return (names && typeof names[index] === "string") ? names[index] : "XR-" + (index + 1);
}

function placementSummary(p) {
    if (!placementEdit(p) || isFlat(p)) return "flat, in line";
    const parts = [];
    if (p.x || p.y) {
        const moved = [];
        if (p.x) moved.push(placementValueText("x", Math.abs(p.x)) + (p.x > 0 ? " right" : " left"));
        if (p.y) moved.push(placementValueText("y", Math.abs(p.y)) + (p.y > 0 ? " up" : " down"));
        parts.push("moved " + moved.join(", "));
    }
    if (p.distance !== 100) parts.push("distance " + p.distance + "%");
    if (p.curvature) parts.push("curved " + p.curvature + "°");
    return parts.join(" · ");
}

// Approximate front view of placed tiles: the turn round you as an offset (a desktop is about PREVIEW_HEIGHT_DEG tall
// in the overview; the centre is kept inside the row, so a desktop beside or behind you shows at the row's edge),
// apparent size 100/distance. Every desktop faces you, so a tile is never foreshortened or rotated. Curvature is
// listed, not drawn. Flat placements return rects unchanged.
const PREVIEW_HEIGHT_DEG = 7;
function placedRects(rects, placements) {
    if (!rects || !rects.length) return [];  // previewRects gives [] when nothing fits; Math.min() of nothing is Infinity
    const left = Math.min.apply(null, rects.map(function(r) { return r.x; }));
    const right = Math.max.apply(null, rects.map(function(r) { return r.x + r.width; }));
    return rects.map(function(r, i) {
        const p = placementEdit((placements || [])[i]) || defaultPlacement();
        const k = 100 / p.distance;
        const w = r.width * k, h = r.height * k;
        const perDeg = r.height / PREVIEW_HEIGHT_DEG;  // the orbit turns the desktop: its apparent size stays
        const cx = Math.min(Math.max(r.x + r.width / 2 + p.x / 10 * perDeg, left), right);
        const cy = Math.min(Math.max(r.y + r.height / 2 - p.y / 10 * perDeg, r.y), r.y + r.height);
        return {x: cx - w / 2, y: cy - h / 2, width: w, height: h};
    });
}

// The panel's Head tracking driver choices: what the controller can start, plus the saved one if it has gone missing, so
// the box shows what is wrong. index is -1 for a saved value that is no choice at all.
function trackingDriverChoices(drivers, saved) {
    return (drivers || []).filter(d => d.available || d.key === saved)
        .map(d => ({label:d.label + (d.available ? "" : " (missing)"), key:d.key}));
}
function trackingDriverIndex(choices, saved) { return choices.findIndex(c => c.key === saved); }

function immersiveDesktopStrength(setting, name, value) {
    const strengths = Object.assign({}, setting.strengths || {});
    strengths[name] = Math.round(value);
    return Object.assign({}, setting, {strengths:strengths});
}
