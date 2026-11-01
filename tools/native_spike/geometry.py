"""Per-desktop geometry rules; mirrored by the broker (C++) and the settings QML."""
PRESETS = {'16:9': ((1920, 1080), (2560, 1440)),
           '21:9': ((2560, 1080), (3440, 1440)),
           '32:9': ((3840, 1080), (5120, 1440))}
SCALES = (1.0, 1.25, 1.5, 1.75, 2.0)
CUSTOM_BOUNDS = {'width': (640, 7680), 'height': (480, 4320)}
DEFAULT_DESKTOP = {'width': 1920, 'height': 1080, 'scale': 1.0}


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
