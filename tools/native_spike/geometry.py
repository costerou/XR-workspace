"""Per-desktop geometry rules; mirrored by the broker (C++) and the settings QML."""
PRESETS = {'16:9': ((1920, 1080), (2560, 1440)),
           '21:9': ((2560, 1080), (3440, 1440)),
           '32:9': ((3840, 1080), (5120, 1440))}
SCALES = (1.0, 1.25, 1.5, 1.75, 2.0)
CUSTOM_BOUNDS = {'width': (640, 7680), 'height': (480, 4320)}
DEFAULT_DESKTOP = {'width': 1920, 'height': 1080, 'scale': 1.0}
# A run has 1..MAX_DESKTOPS managed desktops, XR-1..XR-MAX_DESKTOPS: the pills' Add and Remove (desktops.py) change the
# count live. Mirrors spike/include/xr/desktops.hpp kMaxDesktops (the glass_contract ctest compares them), the broker's
# index bound (spike/ownership/registry.cpp) and the panel's Model.js MAX_DESKTOPS (test_desktops.py).
MAX_DESKTOPS = 4
# Ultrawide (a pill's Ultrawide button): 21:9 at the desktop's own height, and back to 16:9. The common sizes by height;
# any other height gets the ratio, rounded to a multiple of 8 pixels.
ULTRAWIDE_WIDTHS = {1080: 2560, 1440: 3440, 2160: 5120}
STANDARD_WIDTHS = {1080: 1920, 1440: 2560, 2160: 3840}


def exact_scale(width: int, height: int, scale: float) -> bool:
    # Integer arithmetic: scale is a multiple of 1/4, so scale*4 is an integer.
    n = round(scale * 4)
    return n > 0 and n / 4 == scale and width * 4 % n == 0 and height * 4 % n == 0


def valid_scales(width: int, height: int) -> list[float]:
    return [s for s in SCALES if exact_scale(width, height, s)]


def validate_desktop(d: object) -> dict:
    if not isinstance(d, dict) or set(d) != {'width', 'height', 'scale'}:
        raise ValueError('desktop must have exactly width, height, scale')
    w, h, s = d['width'], d['height'], d['scale']
    if type(w) is not int or type(h) is not int: raise ValueError('width and height must be integers')
    if type(s) not in (int, float) or s not in SCALES: raise ValueError('unsupported scale')
    s = float(s)
    for v, key in ((w, 'width'), (h, 'height')):
        lo, hi = CUSTOM_BOUNDS[key]
        if not lo <= v <= hi or v % 2: raise ValueError(f'{key} out of bounds or odd')
    if not exact_scale(w, h, s): raise ValueError('scale does not divide resolution exactly')
    return {'width': w, 'height': h, 'scale': s}


def logical_width(d) -> int:
    return int(d['width'] / d['scale'])


def is_ultrawide(d) -> bool:
    """Wider than 2:1 (21:9 is 2.37, 16:9 is 1.78; 32:9 counts too)."""
    return d['width'] > 2 * d['height']


def toggle_ultrawide(d) -> dict:
    """The desktop the Ultrawide button makes of `d`: 21:9 at its height if it is not ultrawide, else 16:9 at its height.
    Height and scale stay; if the new width cannot keep the scale exact (validate_desktop), the nearest width that can
    (within 64 px), else scale 1. Always a valid desktop."""
    d = validate_desktop(d)
    h, scale = d['height'], d['scale']
    table, ratio = (STANDARD_WIDTHS, 16 / 9) if is_ultrawide(d) else (ULTRAWIDE_WIDTHS, 64 / 27)
    lo, hi = CUSTOM_BOUNDS['width']
    width = min(max(table.get(h) or int(round(h * ratio / 8)) * 8, lo), hi)
    for step in range(0, 33):
        for w in (width - 2 * step, width + 2 * step):
            if lo <= w <= hi and exact_scale(w, h, scale): return validate_desktop({'width': w, 'height': h, 'scale': scale})
    return validate_desktop({'width': width, 'height': h, 'scale': 1.0})


# Per-desktop placement in the glasses view (presentation only: never sent to the broker or consent).
# Integer fields, all relative to the desktop's slot in the overview/zoom layout:
#   x, y       orbit round your head in tenths of a degree (orbit.py): x turns the desktop round you (+x right; -1800
#              and 1800 are both directly behind you), y lifts it (+y up; 900 straight overhead).
#   distance   percent of the layout plane distance (100 = in the plane; 200 = twice as far, half the apparent size;
#              10 = a tenth as far, ten times the size: a desktop can fill well beyond the field of view)
#   curvature  horizontal cylindrical arc in degrees subtended by the desktop's width; 0 = flat.
#              Arc length equals the flat width, so desktop pixels are bent, never stretched.
# Every desktop always faces you, upright (a billboard toward the eye, orbit.py): there is no facing setting. The yaw,
# pitch and roll settings were removed on 2026-11-02; legacy_placement drops them from older saved placements.
# Mirrored by spike/src/ipc.cpp (kPlacementBounds) and omarchy-plugin Model.js (PLACEMENT_BOUNDS); tests compare them.
PLACEMENT_BOUNDS = {'x': (-1800, 1800), 'y': (-900, 900), 'distance': (10, 200), 'curvature': (0, 120)}
PLACEMENT_KEYS = ('x', 'y', 'distance', 'curvature')
DEFAULT_PLACEMENT = {'x': 0, 'y': 0, 'distance': 100, 'curvature': 0}
REMOVED_PLACEMENT_KEYS = ('yaw', 'pitch', 'roll')  # facing settings: every desktop faces you now


def validate_placement(p: object) -> dict:
    if not isinstance(p, dict) or set(p) != set(PLACEMENT_KEYS):
        raise ValueError('placement must have exactly ' + ', '.join(PLACEMENT_KEYS))
    for key in PLACEMENT_KEYS:
        lo, hi = PLACEMENT_BOUNDS[key]
        if type(p[key]) is not int or not lo <= p[key] <= hi: raise ValueError(f'placement {key} must be an integer {lo}..{hi}')
    return {key: p[key] for key in PLACEMENT_KEYS}


def legacy_placement(p: object) -> object:
    """A placement saved before the facing settings were removed (yaw, pitch, roll alongside today's keys) without them;
    anything else unchanged (validate_placement judges it)."""
    if isinstance(p, dict) and set(p) == set(PLACEMENT_KEYS) | set(REMOVED_PLACEMENT_KEYS):
        return {key: p[key] for key in PLACEMENT_KEYS}
    return p


def validate_placements(value: object, count: int | None = None) -> list:
    """One placement per desktop: exactly `count`, or (None) 1..MAX_DESKTOPS."""
    if count is None:
        if not isinstance(value, list) or not 1 <= len(value) <= MAX_DESKTOPS:
            raise ValueError(f'1 to {MAX_DESKTOPS} placements required')
    elif not isinstance(value, list) or len(value) != count: raise ValueError(f'exactly {count} placements required')
    return [validate_placement(p) for p in value]


def validate_desktops(value: object) -> list:
    """A run's desktops: 1..MAX_DESKTOPS valid desktops."""
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_DESKTOPS:
        raise ValueError(f'1 to {MAX_DESKTOPS} desktops required')
    return [validate_desktop(d) for d in value]
