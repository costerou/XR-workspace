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
