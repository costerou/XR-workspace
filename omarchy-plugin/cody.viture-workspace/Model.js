.pragma library

function validScales(w, h) {
    if (typeof w !== "number" || typeof h !== "number"
            || !Number.isInteger(w) || !Number.isInteger(h) || w <= 0 || h <= 0)
        return [];
    return [1, 1.25, 1.5, 1.75, 2].filter(function(s) {
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

function scopeLines(scope, action) {
    if (!scope) return [];
    const lines = [];
    function required(text) { lines.push({text:text, optional:false, key:""}); }
    if (action === "recover") {
        required("Move windows back to " + scope.recovery_output + ".");
        required("Remove this run's XR outputs: " + (scope.desktops || []).map((d,i) => "XR-" + (i+1)).join(", ") + ".");
        required("Unload this run's ownership plugin.");
        return lines;
    }
    required("Show on " + scope.model + " (" + scope.output + "). Windows return to " + scope.recovery_output + " on stop or failure.");
    required("Create XR desktops and capture only those, in memory.");
    (scope.desktops || []).forEach((d,i) => required("XR-" + (i+1) + ": " + d.width + " × " + d.height + " · " + d.scale*100 + "%"));
    required("Load the pinned ownership plugin for this session.");
    lines.push({text:scope.tracking ? "Head tracking via XRLinuxDriver, network blocked." : "Head tracking off (not requested).", optional:!!scope.tracking, key:scope.tracking ? "tracking" : "", unavailable:!scope.tracking});
    if (scope.mode_switch) {
        const m = scope.mode_switch;
        lines.push({text:"Switch " + scope.output + " to " + m.width + "×" + m.height + "@" + m.refresh_hz + " for this session only.", optional:true, key:"mode_switch"});
    } else {
        required(scope.model === "beast" ? "Beast: native 120 Hz mode; no mode switch. This is not measured presentation cadence." : "No display mode switch requested.");
    }
    return lines;
}

// Translate a preview click into the existing relative operations. The caller
// serializes replies, stops on refusal/disconnect, and never changes view locally.
function selectOperations(selected, target, capabilities) {
    if (!Number.isInteger(selected) || !Number.isInteger(target)
            || selected < 0 || selected > 2 || target < 0 || target > 2) return [];
    const operation = target > selected ? "next" : "prev";
    if (capabilities.indexOf(operation) < 0) return [];
    return Array(Math.abs(target-selected)).fill(operation);
}

// Per-desktop placement: integer fields, mirrored from tools/native_spike/geometry.py PLACEMENT_BOUNDS
// (tests/native_spike/test_placement.py compares the tables). Presentation only; editable while running.
const PLACEMENT_BOUNDS = {x: [-100, 100], y: [-100, 100], distance: [50, 200], yaw: [-60, 60], pitch: [-45, 45], roll: [-45, 45], curvature: [0, 120]};
const PLACEMENT_KEYS = ["x", "y", "distance", "yaw", "pitch", "roll", "curvature"];

function placementBounds(key) { return (PLACEMENT_BOUNDS[key] || [0, 0]).slice(); }

function defaultPlacement() { return {x:0, y:0, distance:100, yaw:0, pitch:0, roll:0, curvature:0}; }

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

function samePlacement(a, b) {
    return !!a && !!b && PLACEMENT_KEYS.every(function(k) { return a[k] === b[k]; });
}

function isFlat(p) {
    const d = defaultPlacement();
    return !!p && PLACEMENT_KEYS.every(function(k) { return p[k] === d[k]; });
}

// Three placements for the profile with entry `index` replaced; missing/invalid entries become flat.
function withPlacement(placements, index, placement) {
    const out = [0, 1, 2].map(function(i) {
        return placementEdit((placements || [])[i]) || defaultPlacement();
    });
    if (Number.isInteger(index) && index >= 0 && index < 3 && placementEdit(placement)) out[index] = placementEdit(placement);
    return out;
}

function placementSummary(p) {
    if (!placementEdit(p) || isFlat(p)) return "flat, in line";
    const parts = [];
    if (p.x || p.y) parts.push("offset " + p.x + "%, " + p.y + "%");
    if (p.distance !== 100) parts.push("distance " + p.distance + "%");
    if (p.yaw || p.pitch || p.roll) parts.push("yaw " + p.yaw + "° pitch " + p.pitch + "° roll " + p.roll + "°");
    if (p.curvature) parts.push("curved " + p.curvature + "°");
    return parts.join(" · ");
}

// Approximate top-down preview of placed tiles: offset (y up), apparent size 100/distance, yaw as foreshortening,
// roll as rotation (QML rotation is clockwise). Curvature is listed, not drawn. Flat placements return rects unchanged.
function placedRects(rects, placements) {
    return rects.map(function(r, i) {
        const p = placementEdit((placements || [])[i]) || defaultPlacement();
        const k = 100 / p.distance;
        const w = r.width * k * Math.cos(p.yaw * Math.PI / 180), h = r.height * k * Math.cos(p.pitch * Math.PI / 180);
        // The renderer scales the centre with distance, so the apparent offset does not shrink.
        const cx = r.x + r.width / 2 + p.x / 100 * r.width, cy = r.y + r.height / 2 - p.y / 100 * r.height;
        return {x: cx - w / 2, y: cy - h / 2, width: w, height: h, rotation: -p.roll};
    });
}
