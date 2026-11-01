"""Per-glasses profiles: view settings plus per-desktop geometry, tracking, mode switch and fallback output."""
import json
import os
from pathlib import Path
import tempfile

from . import view
from .geometry import DEFAULT_DESKTOP, validate_desktop
from .ipc import token

MODELS = ('beast', 'luma-ultra', 'unknown')
SCHEMA = 2
DEFAULT_PROFILE = {**view.DEFAULT_SETTINGS, 'desktops': [dict(DEFAULT_DESKTOP) for _ in range(3)],
                   'tracking': True, 'mode_switch': True, 'fallback': None}


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


def _fallback(value):
    if value is None: return None
    try: return token(value)
    except ValueError: return None


def _clean(data):
    """Each field validated on its own; anything invalid or missing falls back to its default."""
    out = _view_part(data)
    desktops = data.get('desktops')
    out['desktops'] = [_desktop(desktops[i]) if isinstance(desktops, list) and len(desktops) == 3 else dict(DEFAULT_DESKTOP)
                       for i in range(3)]
    for key in ('tracking', 'mode_switch'):
        out[key] = data[key] if type(data.get(key)) is bool else True
    out['fallback'] = _fallback(data.get('fallback'))
    return out


def _view_part(data):
    out = dict(view.DEFAULT_SETTINGS)
    if type(data.get('spacing')) is int and 0 <= data['spacing'] <= view.MAX_SPACING: out['spacing'] = data['spacing']
    if data.get('mode') in view.MODES: out['mode'] = data['mode']
    for key in ('cursor_follow', 'prediction'):
        if type(data.get(key)) is bool: out[key] = data[key]
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
    d = profile['desktops']
    if not isinstance(d, list) or len(d) != 3: raise ValueError('invalid profile')
    out = {**profile, 'desktops': [validate_desktop(x) for x in d]}
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


class LayoutStore:
    """The v1 layout.json store (CLI default)."""
    def __init__(self, path):
        self.path = Path(path)

    def load(self):
        return view.load_settings(self.path)

    def save(self, view_settings):
        view.save_settings(self.path, view_settings)
