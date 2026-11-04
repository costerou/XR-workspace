"""The background behind the desktops and behind a VR video (spike/include/xr/environment.hpp draws it).

This module is the one place the supervisor reads and writes its setting, as quality.py is for the video quality:

- BACKGROUNDS lists the choices, in menu order: (wire name, label). 'none' is the plain black passthrough, 'grid' the TRON
  grid, 'smoke' drifting fog. Adding a background: a row here, the same row in environment.hpp kBackgrounds (the glass_contract ctest compares
  them) and a scene in environment_gl.cpp; the menus and the saved setting follow from the table.
- DEFAULTS: `background` (None, so nothing changes for anyone who never picks one), `last` (the last background that was
  not None, what Ctrl+Alt+E comes back to) and `brightness` in percent (BRIGHTNESS_BOUNDS; 100 is the scene as designed).
  The colour is not a setting: it is the Omarchy theme's, the same accent as the highlight border and glow, in the renderer.
- environment(source) is the accessor: the cleaned setting from a saved settings dict (layout.json, a glasses profile) or
  anything holding it as an `environment` attribute (view.ViewState). Every reader goes through it.
- The setting is saved with the view settings (view.DEFAULT_SETTINGS['environment']), so it lives in layout.json for CLI
  runs and in the glasses model's profile (profiles.json) for panel runs, and each XR start comes back to it.

Where it is set: the placement panel's Background dropdown, its switch and its Brightness slider (view.ViewControl,
free_pointer.py; the same in every desktop's panel), the VR video quality menu's row (video.py: that menu exists only while
a video plays) and Ctrl+Alt+E (TOGGLE_BIND), a run bind in view.BINDS, the central bind table, so the help card, bind_check
and the key bind panel see it. It goes between None and the last background. Ctrl+Alt+E was free when it was chosen: Omarchy
binds Super+Ctrl+E, Super+Shift+E and Super+Ctrl+Alt+E, all different chords; bind_check.py checks it against the real config.

Wire: an `environment` frame (spike/include/xr/ipc.hpp EnvironmentPayload) when the setting changes, and at the start of a
run only if there is a background, so a renderer from before this feature never sees one while there is none. The grid is
world-locked in the renderer in every viewing mode, so nothing here depends on the view. The controller reaches a running
session with SET_OP, one absolute message like `placement`.
"""
import json
import math

BACKGROUNDS = (('none', 'None'), ('grid', 'Grid'), ('smoke', 'Smoke'))  # (wire name, label): spike/include/xr/environment.hpp kBackgrounds
NAMES = tuple(name for name, _ in BACKGROUNDS)
NONE = NAMES[0]
BRIGHTNESS_BOUNDS, DEFAULT_BRIGHTNESS = (10, 200), 100  # renderer: kBrightnessMin / Max / Default
BRIGHTNESS_STEPS = (5, 25)     # the placement panel's Left / Right on the Brightness row: (small, Shift)
BRIGHTNESS_FIELD = 'brightness'  # the panel's `field` while its Brightness row is the selected one
DEFAULTS = {'background': NONE, 'last': NAMES[1], 'brightness': DEFAULT_BRIGHTNESS}
KEYS = tuple(DEFAULTS)
TOGGLE_OP, SET_OP = 'environment_toggle', 'environment'
TOGGLE_BIND = ('CTRL + ALT + E', TOGGLE_OP)
MENU_LABEL = 'Background'  # at most quality.MAX_LABEL (renderer: kMaxQualityText), as the labels are


def _brightness_ok(value):
    return type(value) is int and BRIGHTNESS_BOUNDS[0] <= value <= BRIGHTNESS_BOUNDS[1]


def _name_ok(value):
    return isinstance(value, str) and value in NAMES


def clean(value):
    """Each field validated on its own; anything missing or invalid falls back to its default. Never raises."""
    data = value if isinstance(value, dict) else {}
    background = data['background'] if _name_ok(data.get('background')) else NONE
    last = data['last'] if _name_ok(data.get('last')) and data['last'] != NONE else DEFAULTS['last']
    return {'background': background, 'last': background if background != NONE else last,
            'brightness': data['brightness'] if _brightness_ok(data.get('brightness')) else DEFAULT_BRIGHTNESS}


def is_valid(value):
    return isinstance(value, dict) and set(value) == set(KEYS) and _name_ok(value['background']) and _name_ok(value['last']) and \
        value['last'] != NONE and value['background'] in (NONE, value['last']) and _brightness_ok(value['brightness'])


def validate(value):
    if not is_valid(value): raise ValueError('invalid environment')
    return {k: value[k] for k in KEYS}


def environment(source):
    """The accessor: the cleaned setting from a settings dict (its 'environment' entry) or an object with an `environment`
    attribute (view.ViewState). Missing or invalid: the defaults."""
    raw = source.get('environment') if isinstance(source, dict) else getattr(source, 'environment', None)
    return clean(raw)


def on(env):
    return clean(env)['background'] != NONE


def picked(env, name):
    """The background `name` chosen. None keeps the last one for Ctrl+Alt+E."""
    if not _name_ok(name): raise ValueError('invalid background')
    return clean({**clean(env), 'background': name})


def toggled(env):
    """Ctrl+Alt+E and the panel's switch: None <-> the last background used."""
    env = clean(env)
    return picked(env, NONE if env['background'] != NONE else env['last'])


def stepped(env, delta):
    """The quality menu's row: the next or previous background, wrapping round."""
    env = clean(env)
    return picked(env, NAMES[(NAMES.index(env['background']) + delta) % len(NAMES)])


def brightened(env, value):
    """The brightness set to `value` percent, kept within BRIGHTNESS_BOUNDS."""
    return {**clean(env), 'brightness': min(max(int(value), BRIGHTNESS_BOUNDS[0]), BRIGHTNESS_BOUNDS[1])}


def slid(env, t):
    """The Brightness slider pressed or dragged at fraction `t` of its track (0 = low bound, 1 = high): whole percents."""
    if type(t) not in (int, float) or not math.isfinite(t): raise ValueError('invalid slider')
    lo, hi = BRIGHTNESS_BOUNDS
    return brightened(env, lo + int(math.floor(min(max(t, 0.0), 1.0) * (hi - lo) + 0.5)))


def label(env):
    """The background's label as the menus show it."""
    return dict(BACKGROUNDS)[clean(env)['background']]


def menu_row(env):
    """The quality menu's row (quality.menu_payload `extra`)."""
    return {'label': MENU_LABEL, 'value': label(env)}


def renderer_frame(run_id, session, request_id, env):
    """Renderer `environment` frame (spike/include/xr/ipc.hpp EnvironmentPayload)."""
    env = validate(env)
    payload = {'background': env['background'], 'brightness': env['brightness']}
    return (json.dumps({'schema': 1, 'request_id': request_id, 'run_id': run_id, 'session': session, 'op': 'environment',
                        'payload': payload}, separators=(',', ':')) + '\n').encode()
