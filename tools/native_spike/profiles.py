"""Per-glasses profiles: view settings plus per-desktop geometry and placement, tracking, mode switch and fallback output.

`desktops` (pixels/scale) feed consent and the broker; `placements` (position round you, distance, curvature) are
presentation-only and can change during a run. Profiles saved before placements existed load with flat defaults; those
saved with the removed yaw / pitch / roll settings keep everything else (geometry.legacy_placement).

A profile holds 1..geometry.MAX_DESKTOPS desktops, one placement and one levelling roll each (`rolls`, the overview's roll
arrows: geometry.MAX_ROLL). Together they are the monitor setup (SETUP_KEYS), and every XR start restores the last
session's (remember-layout-reset-bind, 2026-11-03, which undid the fresh three-flat start of xr-default-three-monitors):
the count, each desktop's resolution and scale, where it sits round you, its distance, curvature and roll. A run saves
the setup whenever it changes (a pill's Add, Remove, Ultrawide or Scale: desktops.py; a placement or roll: view.py,
debounced) and once more at its end (managed.view_controls), always with an atomic replace (save_profile).

load_setup is the start's strict read: the saved setup is used whole or not at all. Nothing saved yet, an unreadable or
corrupt file, or any invalid desktop, placement or roll gives the default three flat 1920x1080 desktops instead
(default_setup) and the reason, which the controller and the run log. A setup whose outputs the broker then refuses to
create is reset to the default too (discard_unusable), so the next start does not fail the same way. Ctrl+Alt+R (view.py
RESET_OP) puts a live run back to the default and saves it (PlacementStore.reset), keeping the layout it replaced, spacing
included, as a one-step backup in the profile's `previous_setup` (each real reset overwrites it; a reset of a layout that
already is the default leaves it). Nothing reads the backup back yet: it is there for a future restore, or by hand.

`tracking_driver` is the head tracking driver a start launches (the panel's Startup option): tracking.DRIVERS. The
controller's start decides what becomes of a choice that is not installed: see Controller._tracking_scope.

Where the windows were is not part of the setup: window_memory.py keeps that in its own file beside this one, which nothing
here touches, so a reset or a discarded setup leaves it alone.

load_profile, by contrast, cleans each field on its own (the panel and the view settings): placements missing for a
desktop are flat, extra ones are dropped, and rolls missing (profiles saved before rolls were kept) are level.
"""
import json
import os
from pathlib import Path
import sys
import tempfile

from . import view
from .tracking import DRIVERS, XRDRIVER
from .environment import environment
from .immersive import immersive
from .quality import video_quality
from .geometry import (DEFAULT_DESKTOP, DEFAULT_PLACEMENT, MAX_DESKTOPS, legacy_placement, validate_desktop, validate_desktops,
                       validate_placement, validate_placements, validate_rolls)
from .ipc import token

MODELS = ('beast', 'luma-ultra', 'unknown')
SCHEMA = 2
DEFAULT_PROFILE = {**view.DEFAULT_SETTINGS, 'desktops': [dict(DEFAULT_DESKTOP) for _ in range(3)],
                   'placements': [dict(DEFAULT_PLACEMENT) for _ in range(3)], 'rolls': [0] * 3, 'tracking': True,
                   'tracking_driver': XRDRIVER, 'mode_switch': True, 'fallback': None, 'previous_setup': None}

# The monitor setup a start restores (module docstring), and the default it falls back to: three flat 1920x1080 desktops
# at 100%, level. Ctrl+Alt+R (view.RESET_OP) resets a live run to the same.
SETUP_KEYS = ('desktops', 'placements', 'rolls')
DEFAULT_SETUP_DESKTOPS = 3
NOTHING_SAVED = 'nothing saved yet'  # load_setup's reason when there is no setup to restore (not an error)


def default_setup():
    n = DEFAULT_SETUP_DESKTOPS
    return {'desktops': [dict(DEFAULT_DESKTOP) for _ in range(n)], 'placements': [dict(DEFAULT_PLACEMENT) for _ in range(n)],
            'rolls': [0] * n}


def setup_from(entry):
    """(setup, None) when `entry` (one glasses model's profile as saved, or None) holds a usable monitor setup, else
    (default_setup(), why). Strict, unlike load_profile: the setup is used whole or not at all. Two older shapes still
    restore: placements saved with the removed yaw / pitch / roll (geometry.legacy_placement) and a profile from before
    rolls were kept (level)."""
    if entry is None: return default_setup(), NOTHING_SAVED
    if not isinstance(entry, dict): return default_setup(), 'the saved profile is not an object'
    if 'desktops' not in entry: return default_setup(), NOTHING_SAVED
    try: desktops = validate_desktops(entry['desktops'])
    except ValueError as exc: return default_setup(), f'the saved desktops are invalid ({exc})'
    placements = entry.get('placements')
    if placements is None: placements = [dict(DEFAULT_PLACEMENT) for _ in desktops]  # saved before placements existed
    try:
        if not isinstance(placements, list): raise ValueError('not a list')
        placements = validate_placements([legacy_placement(p) for p in placements], len(desktops))
    except ValueError as exc: return default_setup(), f'the saved placements are invalid ({exc})'
    rolls = entry.get('rolls')
    if rolls is None: rolls = [0] * len(desktops)  # saved before rolls were kept: level
    try: rolls = validate_rolls(rolls, len(desktops))
    except ValueError as exc: return default_setup(), f'the saved rolls are invalid ({exc})'
    return {'desktops': desktops, 'placements': placements, 'rolls': rolls}, None


def load_setup(path, model):
    """The monitor setup the next start of `model` restores: (setup, None), or (default_setup(), why) when nothing is
    saved yet or what is saved is unusable (module docstring)."""
    _model(model)
    path = Path(path)
    try: text = path.read_text()
    except FileNotFoundError: return default_setup(), NOTHING_SAVED
    except OSError as exc: return default_setup(), f'{path.name} could not be read ({exc.strerror or exc})'
    try: data = json.loads(text)
    except ValueError: return default_setup(), f'{path.name} is corrupt (not JSON)'
    if not isinstance(data, dict) or data.get('schema') != SCHEMA or not isinstance(data.get('profiles'), dict):
        return default_setup(), f'{path.name} is corrupt (no schema {SCHEMA} profiles)'
    return setup_from(data['profiles'].get(model))


def _previous(value):
    """`value` as a valid `previous_setup` (a monitor setup plus its spacing), or None."""
    if not isinstance(value, dict): return None
    setup, reason = setup_from(value)
    spacing = value.get('spacing')
    if reason is not None or type(spacing) is not int or not 0 <= spacing <= view.MAX_SPACING: return None
    return {**setup, 'spacing': spacing}


def log(message):
    """One line on stderr (the controller's and the run's systemd journals)."""
    print(f'xr-workspace: {message}', file=sys.stderr, flush=True)


def describe_setup(setup):
    """'4 desktops: 2560x1080@1, ...' for a log line."""
    sizes = ', '.join(f"{d['width']}x{d['height']}@{d['scale']:g}" for d in setup['desktops'])
    return f"{len(setup['desktops'])} desktop{'s' if len(setup['desktops']) != 1 else ''}: {sizes}"


def discard_unusable(path, model, desktops, error):
    """A start could not create the outputs of the saved setup (`desktops`: the broker refused them or did not read them
    back, `error`). Unless the profile changed meanwhile, its setup goes back to the default (PlacementStore.reset) so the
    next start falls back instead of failing the same way; the log keeps the setup it replaced. True if it reset."""
    setup, reason = load_setup(path, model)
    if reason is not None or setup['desktops'] != desktops: return False
    log(f'the saved monitor setup could not be created ({error}); the next start uses three flat desktops. '
        f'It was: {json.dumps(setup, separators=(",", ":"))}')
    try: PlacementStore(path, model).reset()
    except (OSError, ValueError) as exc:
        log(f'the saved monitor setup could not be reset ({exc})')
        return False
    return True


def profiles_path():
    base = os.environ.get('XDG_CONFIG_HOME') or str(Path.home() / '.config')
    return Path(base) / 'viture-workspace' / 'profiles.json'


def _model(model):
    if model not in MODELS: raise ValueError('unknown model')
    return model


def _read_profiles(path):
    try: data = json.loads(Path(path).read_text())
    except (OSError, ValueError): return {}
    if not isinstance(data, dict) or data.get('schema') != SCHEMA or not isinstance(data.get('profiles'), dict): return {}
    return data['profiles']


def _desktop(value):
    try: return validate_desktop(value)
    except ValueError: return dict(DEFAULT_DESKTOP)


def _placement(value):
    try: return validate_placement(legacy_placement(value))  # saved with yaw / pitch / roll: kept without them
    except ValueError: return dict(DEFAULT_PLACEMENT)


def _fallback(value):
    if value is None: return None
    try: return token(value)
    except ValueError: return None


def _clean(data):
    """Each field validated on its own; anything invalid or missing falls back to its default."""
    out = _view_part(data)
    desktops = data.get('desktops')
    if not isinstance(desktops, list) or not 1 <= len(desktops) <= MAX_DESKTOPS: desktops = [None] * 3
    out['desktops'] = [_desktop(d) for d in desktops]
    placements = data.get('placements')
    placements = placements if isinstance(placements, list) and len(placements) == len(desktops) else []
    out['placements'] = [_placement(placements[i]) if i < len(placements) else dict(DEFAULT_PLACEMENT) for i in range(len(desktops))]
    try: out['rolls'] = validate_rolls(data.get('rolls'), len(desktops))
    except ValueError: out['rolls'] = [0] * len(desktops)  # missing (saved before rolls were kept), or invalid: level
    out['previous_setup'] = _previous(data.get('previous_setup'))
    for key in ('tracking', 'mode_switch'):
        out[key] = data[key] if type(data.get(key)) is bool else True
    out['tracking_driver'] = data['tracking_driver'] if data.get('tracking_driver') in DRIVERS else XRDRIVER
    out['fallback'] = _fallback(data.get('fallback'))
    return out


def _view_part(data):
    out = {**view.DEFAULT_SETTINGS, 'video_quality': video_quality(data), 'environment': environment(data), 'immersive': immersive(data)}
    if type(data.get('spacing')) is int and 0 <= data['spacing'] <= view.MAX_SPACING: out['spacing'] = data['spacing']
    if data.get('mode') in view.MODES: out['mode'] = data['mode']
    if type(data.get('prediction')) is bool: out['prediction'] = data['prediction']
    if type(data.get('stabilization')) is int and 0 <= data['stabilization'] <= 100: out['stabilization'] = data['stabilization']
    # A `cursor_switch_animate` (Ctrl+Alt+A, removed 2026-11-03) is ignored like any unknown key: the cleaned profile drops it.
    return out


def load_profile(path, model):
    _model(model)
    path = Path(path)
    raw = _read_profiles(path).get(model)
    if isinstance(raw, dict): return _clean(raw)
    return _clean(view.load_settings(path.with_name('layout.json')))  # seed from the v1 file, else defaults


def _check(profile):
    if not isinstance(profile, dict) or set(profile) != set(DEFAULT_PROFILE): raise ValueError('invalid profile')
    if not view._valid({k: profile[k] for k in view.DEFAULT_SETTINGS}): raise ValueError('invalid profile')
    desktops = validate_desktops(profile['desktops'])
    rolls = profile['rolls']  # one per desktop: a caller that changed the count without them gets level ones, extras go
    if not isinstance(rolls, list): raise ValueError('invalid profile')
    rolls = validate_rolls(list(rolls), len(rolls))[:len(desktops)]
    out = {**profile, 'desktops': desktops, 'placements': validate_placements(profile['placements'], len(desktops)),
           'rolls': rolls + [0] * (len(desktops) - len(rolls))}
    if any(type(profile[k]) is not bool for k in ('tracking', 'mode_switch')): raise ValueError('invalid profile')
    if profile['tracking_driver'] not in DRIVERS: raise ValueError('invalid profile')
    if profile['fallback'] is not None: token(profile['fallback'])
    previous = profile['previous_setup']
    if previous is not None:
        out['previous_setup'] = _previous(previous)
        if out['previous_setup'] is None: raise ValueError('invalid profile')
    return out


def save_profile(path, model, profile):
    _model(model)
    profile = _check(profile)
    path = Path(path)
    profiles = {k: v for k, v in _read_profiles(path).items() if k in MODELS and k != model}
    profiles[model] = profile
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.profiles.', suffix='.tmp')
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w') as f:
            json.dump({'schema': SCHEMA, 'profiles': profiles}, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try: os.unlink(tmp)
        except OSError: pass
        raise


class ProfileStore:
    """View-settings store backed by one glasses model's profile; other profile fields are preserved."""
    def __init__(self, path, model):
        self.path, self.model = Path(path), _model(model)

    def load(self):
        p = load_profile(self.path, self.model)
        return {k: p[k] for k in view.DEFAULT_SETTINGS}

    def save(self, view_settings):
        if not isinstance(view_settings, dict) or set(view_settings) != set(view.DEFAULT_SETTINGS) or not view._valid(view_settings):
            raise ValueError('invalid settings')
        save_profile(self.path, self.model, {**load_profile(self.path, self.model), **view_settings})


class PlacementStore:
    """The monitor setup (desktops, placements, rolls) of one glasses model's profile; every other profile field is
    preserved."""
    def __init__(self, path, model):
        self.path, self.model = Path(path), _model(model)

    def load(self):
        return load_profile(self.path, self.model)['placements']

    def load_setup(self):
        """What the next start restores: (setup, None) or (default_setup(), why) (module docstring)."""
        return load_setup(self.path, self.model)

    def save(self, placements):
        """One placement per desktop of the profile; more are dropped, missing ones kept as saved."""
        self.save_layout(placements, None)

    def save_layout(self, placements, rolls):
        """save() plus each desktop's levelling roll (None: the saved ones stay), cut or padded the same way."""
        profile = load_profile(self.path, self.model)
        n = len(profile['desktops'])
        placements = validate_placements(placements)
        saved = profile['placements']
        full = (placements + saved[len(placements):])[:n]
        full += [dict(DEFAULT_PLACEMENT) for _ in range(n - len(full))]
        out = {**profile, 'placements': full}
        if rolls is not None:
            rolls = validate_rolls(rolls, len(rolls))
            out['rolls'] = (list(rolls) + profile['rolls'][len(rolls):] + [0] * n)[:n]
        save_profile(self.path, self.model, out)

    def save_desktops(self, desktops, placements, rolls=None):
        """The run's whole monitor setup (a desktop change, desktops.py, or the run's end): the profile takes its desktops,
        their placements and their rolls (None: level), so the next start restores them. Every other profile field is
        preserved."""
        desktops = validate_desktops(desktops)
        save_profile(self.path, self.model, {**load_profile(self.path, self.model), 'desktops': desktops,
                                             'placements': validate_placements(placements, len(desktops)),
                                             'rolls': validate_rolls([0] * len(desktops) if rolls is None else rolls, len(desktops))})

    def reset(self, previous=None):
        """The profile's setup becomes the default (default_setup: three flat 1920x1080 desktops at 100%, level): a saved
        setup the broker could not create, or Ctrl+Alt+R. Ctrl+Alt+R passes `previous`, the layout it replaces ({desktops
        (None: the profile's), placements, rolls, spacing}): spacing goes back to its default too and that layout is kept
        as the one-step backup, unless it already is the default, which would only wipe a backup worth keeping. Every other
        profile field is preserved."""
        profile = load_profile(self.path, self.model)
        out = {**profile, **default_setup()}
        if previous is not None:
            out['spacing'] = view.DEFAULT_SPACING
            kept = _previous({**previous, 'desktops': previous['desktops'] or profile['desktops']})
            if kept is not None and kept != {**default_setup(), 'spacing': view.DEFAULT_SPACING}: out['previous_setup'] = kept
        save_profile(self.path, self.model, out)


class LayoutStore:
    """The v1 layout.json store (CLI default)."""
    def __init__(self, path):
        self.path = Path(path)

    def load(self):
        return view.load_settings(self.path)

    def save(self, view_settings):
        view.save_settings(self.path, view_settings)
