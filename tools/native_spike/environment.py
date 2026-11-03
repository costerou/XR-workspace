"""The TRON grid: an optional 360 degree environment behind the desktops (spike/include/xr/environment.hpp draws it).

This module is the one place the supervisor reads and writes its setting, as quality.py is for the video quality:

- DEFAULTS: `on` (off by default, so nothing changes for anyone who never presses the key) and `color`: 'cyan' (classic
  TRON) or 'theme' (the Omarchy theme accent, the same colour as the highlight border, followed live: a theme switch
  recolours it). Off keeps the colour: the next on comes back in it.
- environment(source) is the accessor: the cleaned setting from a saved settings dict (layout.json, a glasses profile) or
  anything holding it as an `environment` attribute (view.ViewState). Every reader goes through it.
- The setting is saved with the view settings (view.DEFAULT_SETTINGS['environment']), so it lives in layout.json for CLI
  runs and in the glasses model's profile (profiles.json) for panel runs, and each XR start comes back to it.

Keys: run binds in view.BINDS, the central bind table (so the help card, bind_check and the key bind panel see them).
Ctrl+Alt+E (TOGGLE_BIND) fades it in and out. Ctrl+Alt+Shift+E (COLOR_BIND) switches cyan / theme, a crossfade, and turns
the grid on if it was off, so the key always shows what it did: the VR video's quality menu (Q, video.py) has a row for
it too, but that menu exists only while a video plays. Ctrl+Alt+E was free when it was chosen: Omarchy binds Super+Ctrl+E,
Super+Shift+E and Super+Ctrl+Alt+E, all different chords (Ctrl+Alt+A, free since it was removed, was kept away from a new
meaning); bind_check.py checks both against the real config.

Wire: an `environment` frame (spike/include/xr/ipc.hpp EnvironmentPayload) when the setting changes, and at the start of a
run only if it is on, so a renderer from before this feature never sees one while it is off. The grid is world-locked in
the renderer in every viewing mode, and a VR video replaces it (as it replaces the desktops), so nothing here depends on
the view.
"""
import json

COLORS = ('cyan', 'theme')  # wire values; renderer: EnvironmentColor
DEFAULTS = {'on': False, 'color': 'cyan'}
KEYS = tuple(DEFAULTS)
TOGGLE_OP, COLOR_OP = 'environment_toggle', 'environment_color'
OPS = (TOGGLE_OP, COLOR_OP)
TOGGLE_BIND = ('CTRL + ALT + E', TOGGLE_OP)
COLOR_BIND = ('CTRL + ALT + SHIFT + E', COLOR_OP)
MENU_LABEL = 'TRON grid'
MENU_VALUES = {'cyan': 'TRON cyan', 'theme': 'Theme colour'}  # at most quality.MAX_LABEL (renderer: kMaxQualityText)
MENU_OFF = 'Off'


def clean(value):
    """Each field validated on its own; anything missing or invalid falls back to its default. Never raises."""
    data = value if isinstance(value, dict) else {}
    return {'on': data['on'] if type(data.get('on')) is bool else DEFAULTS['on'],
            'color': data['color'] if isinstance(data.get('color'), str) and data['color'] in COLORS else DEFAULTS['color']}


def is_valid(value):
    return isinstance(value, dict) and set(value) == set(KEYS) and type(value['on']) is bool and \
        isinstance(value['color'], str) and value['color'] in COLORS


def validate(value):
    if not is_valid(value): raise ValueError('invalid environment')
    return {k: value[k] for k in KEYS}


def environment(source):
    """The accessor: the cleaned setting from a settings dict (its 'environment' entry) or an object with an `environment`
    attribute (view.ViewState). Missing or invalid: the defaults."""
    raw = source.get('environment') if isinstance(source, dict) else getattr(source, 'environment', None)
    return clean(raw)


def toggled(env):
    """Ctrl+Alt+E: on <-> off, in the same colour."""
    env = clean(env)
    return {**env, 'on': not env['on']}


def recolored(env):
    """Ctrl+Alt+Shift+E: the other colour, and on (the key shows what it did)."""
    env = clean(env)
    return {'on': True, 'color': COLORS[(COLORS.index(env['color']) + 1) % len(COLORS)]}


def _choices(env):
    return [{'on': False, 'color': env['color']}] + [{'on': True, 'color': c} for c in COLORS]


def stepped(env, delta):
    """The quality menu's row: Off, TRON cyan, Theme colour, wrapping round; Off keeps the colour."""
    env = clean(env)
    i = 1 + COLORS.index(env['color']) if env['on'] else 0
    return _choices(env)[(i + delta) % (len(COLORS) + 1)]


def menu_row(env):
    """The quality menu's row (quality.menu_payload `extra`)."""
    env = clean(env)
    return {'label': MENU_LABEL, 'value': MENU_VALUES[env['color']] if env['on'] else MENU_OFF}


def operations(current, target):
    """The run ops (TOGGLE_OP, COLOR_OP) that take a running session's `current` setting to `target`, for the controller:
    settings travel to a run as relative operations (xr_controller/state.py)."""
    current, target = clean(current), clean(target)
    ops = []
    if target['color'] != current['color']:
        ops.append(COLOR_OP)
        current = recolored(current)
    if target['on'] != current['on']: ops.append(TOGGLE_OP)
    return ops


def renderer_frame(run_id, session, request_id, env):
    """Renderer `environment` frame (spike/include/xr/ipc.hpp EnvironmentPayload)."""
    return (json.dumps({'schema': 1, 'request_id': request_id, 'run_id': run_id, 'session': session, 'op': 'environment',
                        'payload': validate(env)}, separators=(',', ':')) + '\n').encode()
