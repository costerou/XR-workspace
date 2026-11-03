"""Per-glasses profiles: view settings plus per-desktop geometry and placement, tracking, mode switch and fallback output.

`desktops` (pixels/scale) feed consent and the broker; `placements` (position round you, distance, curvature) are
presentation-only and can change during a run. Profiles saved before placements existed load with flat defaults; those
saved with the removed yaw / pitch / roll settings keep everything else (geometry.legacy_placement).

A profile holds 1..geometry.MAX_DESKTOPS desktops and one placement each. A run saves its live desktops back (a pill's Add,
Remove, Ultrawide or Scale: desktops.py), but every XR start begins fresh (startup_desktops, and managed.view_controls):
three desktops, all flat, whatever count and placements were saved. Since monitor-collision-bump (2026-11-03) a run
never writes placements either: where desktops were moved, resized or pushed lasts that session only (save_desktops
stores them flat). Placements missing for a desktop are flat; extra ones are dropped.
"""
import json
import os
from pathlib import Path
import tempfile

from . import view
from .quality import video_quality
from .geometry import (DEFAULT_DESKTOP, DEFAULT_PLACEMENT, MAX_DESKTOPS, legacy_placement, validate_desktop, validate_desktops,
                       validate_placement, validate_placements)
from .ipc import token

MODELS = ('beast', 'luma-ultra', 'unknown')
SCHEMA = 2
DEFAULT_PROFILE = {**view.DEFAULT_SETTINGS, 'desktops': [dict(DEFAULT_DESKTOP) for _ in range(3)],
                   'placements': [dict(DEFAULT_PLACEMENT) for _ in range(3)], 'tracking': True, 'mode_switch': True, 'fallback': None}


STARTUP_DESKTOPS = 3


def startup_desktops(saved):
    """Every XR start's desktops: always three. The saved ones' size and scale (the panel's desktop editor) are kept
    for the first three; a saved count (a pill's Add / Remove) is not: missing ones are 1920x1080 at 100%."""
    kept = [validate_desktop(dict(d)) for d in list(saved or [])[:STARTUP_DESKTOPS]]
    return kept + [dict(DEFAULT_DESKTOP) for _ in range(STARTUP_DESKTOPS - len(kept))]


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
    for key in ('tracking', 'mode_switch'):
        out[key] = data[key] if type(data.get(key)) is bool else True
    out['fallback'] = _fallback(data.get('fallback'))
    return out


def _view_part(data):
    out = {**view.DEFAULT_SETTINGS, 'video_quality': video_quality(data)}
    if type(data.get('spacing')) is int and 0 <= data['spacing'] <= view.MAX_SPACING: out['spacing'] = data['spacing']
    if data.get('mode') in view.MODES: out['mode'] = data['mode']
    if type(data.get('prediction')) is bool: out['prediction'] = data['prediction']  # an old cursor_follow is dropped
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
    out = {**profile, 'desktops': desktops, 'placements': validate_placements(profile['placements'], len(desktops))}
    if any(type(profile[k]) is not bool for k in ('tracking', 'mode_switch')): raise ValueError('invalid profile')
    if profile['fallback'] is not None: token(profile['fallback'])
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
    """Per-desktop placements of one glasses model's profile; every other profile field is preserved."""
    def __init__(self, path, model):
        self.path, self.model = Path(path), _model(model)

    def load(self):
        return load_profile(self.path, self.model)['placements']

    def save(self, placements):
        """One per desktop of the profile; more are dropped, missing ones kept as saved."""
        profile = load_profile(self.path, self.model)
        placements = validate_placements(placements)
        saved = profile['placements']
        full = (placements + saved[len(placements):])[:len(profile['desktops'])]
        full += [dict(DEFAULT_PLACEMENT) for _ in range(len(profile['desktops']) - len(full))]
        save_profile(self.path, self.model, {**profile, 'placements': full})

    def save_desktops(self, desktops, placements):
        """A run's desktops changed (desktops.py): the profile takes them (the first three's sizes and scales are what the
        next start uses, startup_desktops), with every placement flat: placements last one session, so the run's own
        (checked, one per desktop) are not kept. Every other profile field is preserved."""
        desktops = validate_desktops(desktops)
        validate_placements(placements, len(desktops))
        save_profile(self.path, self.model, {**load_profile(self.path, self.model), 'desktops': desktops,
                                             'placements': [dict(DEFAULT_PLACEMENT) for _ in desktops]})


class LayoutStore:
    """The v1 layout.json store (CLI default)."""
    def __init__(self, path):
        self.path = Path(path)

    def load(self):
        return view.load_settings(self.path)

    def save(self, view_settings):
        view.save_settings(self.path, view_settings)
