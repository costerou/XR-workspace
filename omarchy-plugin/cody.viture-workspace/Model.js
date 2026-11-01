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
