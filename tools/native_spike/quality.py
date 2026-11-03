"""VR video quality settings and their in-glasses menu (docs/vr-video.md#quality-menu).

This module is the one place the supervisor reads and writes video quality settings:

- SETTINGS lists every setting the menu shows, in menu order: its key, label and allowed values (wire value, label).
- DEFAULTS holds each setting's default. They equal what the renderer does with no `quality` frame at all (its own
  defaults in spike/include/xr/video.hpp VideoQuality), so an empty or old layout.json changes nothing. Other work on
  render internals owns these values; change them here and in VideoQuality together, never in the menu code.
- video_quality(source) is the accessor: the cleaned settings from a saved settings dict (layout.json, a glasses
  profile) or anything holding them as a `video_quality` attribute (view.ViewState). Every reader goes through it.

The settings are saved with the view settings (view.DEFAULT_SETTINGS['video_quality']), so they live in layout.json
for CLI runs and in the glasses model's profile (profiles.json) for panel runs.

What is exposed and why only this: each setting applies live to the playing video and is cheap. `filter` is how the
projection pass reconstructs the decoded frame where it is magnified (spike/src/video_gl.hpp; the same names as the
renderer's XR_VIDEO_FILTER): Catmull-Rom (a Keys cubic, a = -0.5, the default), Sharp (a = -0.75: more acutance, a
little more ringing), Bilinear, or Nearest (hard texel edges). Minified areas always use trilinear mipmaps.
`linear`, the first menu's name for bilinear, still reads as bilinear from a saved layout.json or profile. `deband` is mpv's debanding pass while it converts each frame into the texture (off by default in mpv),
which smooths the banding of compressed skies and gradients at a small GPU cost per decoded frame. Not exposed:
render scale (the projection is drawn 1:1 into the output), the session's resolution and refresh rate (fixed by the
consented session mode, 1920x1200@120), the desktops' capture resolution (set per desktop in the panel before a
session), and codec or bitrate (nothing is encoded: files are decoded locally and web streams are relayed with
`-c copy`).
"""
from collections import namedtuple

Setting = namedtuple('Setting', 'key label choices')  # choices: ((wire value, label), ...)

SETTINGS = (
    Setting('filter', 'Texture filter', (('catmull-rom', 'Catmull-Rom'), ('sharp', 'Sharp'), ('bilinear', 'Bilinear'),
                                         ('nearest', 'Nearest pixels'))),
    Setting('deband', 'Deband', ((False, 'Off'), (True, 'On'))),
)
DEFAULTS = {'filter': 'catmull-rom', 'deband': False}  # the renderer's own defaults (VideoQuality); see the module doc
KEYS = tuple(s.key for s in SETTINGS)
ALIASES = {'filter': {'linear': 'bilinear'}}  # older saved names -> the current wire value
MAX_LABEL = 24  # renderer: kMaxQualityText


def _values(setting):
    return [v for v, _ in setting.choices]


def _ok(setting, value):
    return any(type(value) is type(v) and value == v for v in _values(setting))  # True is not 1


def clean(value):
    """Each setting validated on its own; anything missing or invalid falls back to its default. Never raises."""
    data = value if isinstance(value, dict) else {}
    data = {k: ALIASES.get(k, {}).get(v, v) if isinstance(v, str) else v for k, v in data.items()}
    return {s.key: data[s.key] if s.key in data and _ok(s, data[s.key]) else DEFAULTS[s.key] for s in SETTINGS}


def is_valid(value):
    return isinstance(value, dict) and set(value) == set(KEYS) and all(_ok(s, value[s.key]) for s in SETTINGS)


def validate(value):
    if not is_valid(value): raise ValueError('invalid video quality')
    return {k: value[k] for k in KEYS}


def video_quality(source):
    """The accessor: cleaned quality settings from a settings dict (its 'video_quality' entry) or an object with a
    `video_quality` attribute (view.ViewState). Missing or invalid: the defaults."""
    raw = source.get('video_quality') if isinstance(source, dict) else getattr(source, 'video_quality', None)
    return clean(raw)


def step(quality, row, delta):
    """`quality` with the setting on menu `row` moved `delta` choices along (wrapping round)."""
    quality = clean(quality)
    s = SETTINGS[row % len(SETTINGS)]
    values = _values(s)
    i = next(i for i, v in enumerate(values) if type(v) is type(quality[s.key]) and v == quality[s.key])
    return {**quality, s.key: values[(i + delta) % len(values)]}


def value_label(setting, value):
    return next(label for v, label in setting.choices if type(v) is type(value) and v == value)


def renderer_payload(quality):
    """`video` frame payload applying the settings (spike/src/video_ipc.cpp)."""
    return {'action': 'quality', **validate(quality)}


def menu_payload(quality, visible, selected):
    """`video` frame payload drawing the menu on the controls card: one row per setting, the selected one marked."""
    quality = clean(quality)
    rows = [{'label': s.label, 'value': value_label(s, quality[s.key])} for s in SETTINGS]
    return {'action': 'menu', 'visible': bool(visible), 'selected': selected % len(SETTINGS) if visible else 0, 'rows': rows}
