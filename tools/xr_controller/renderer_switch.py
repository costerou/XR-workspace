"""Which renderer a Start launches: the C++ `xr-native-spike` (the default) or the Rust `xr-renderer`.

One setting, read at every Start, so switching either way takes effect at the next run and needs no restart:
  - $XDG_CONFIG_HOME/viture-workspace/renderer.json, {"schema": 1, "renderer": "rust" | "cpp"}. No file is C++.
  - `python -m tools.xr_controller renderer set rust|cpp`, `clear` or `status` (a hidden subcommand).

Rust launches the binary `install --rust-renderer` pinned (install.py RUST_RENDERER_PIN, bin/<hash>/rust/xr-renderer). Where
it is read:
  - At Start (Host.scope, the controller's scope_transform): the consent names the renderer the setting picks now, so the
    confirm box says which one will run. The Rust pin is hashed here too, so a changed binary fails the Start before the click.
  - In the run (runner.execute, and runner.rs for the Rust supervisor): the consent must name that same renderer, and its
    bytes are hashed again just before the launch.
With rust set and no trustworthy Rust renderer the Start fails and says how to switch back. It never launches the C++ one
instead: a run meant to prove the Rust renderer must not be C++ without anyone knowing (supervisor_switch.py's rule).

What stays on C++ and its own path whatever the setting:
  - A Pro 2 run: its renderer is the one in its pinned bundle (pro2_route.py), and this file is not read for it.
  - Recover (`recover-run`), which launches no renderer.
  - A run in the isolated VM (isolated.py), whose controller has its own scope_transform and checks installed.json itself.
No depth engine sits beside the Rust renderer, so a Rust Start offers no 3D (immersive.find_engine).
"""
import json
import os
from pathlib import Path
import tempfile

from tools.native_spike import immersive, sbs
from tools.native_spike.ipc import read_private_json
from .install import RUST_RENDERER_PIN, load_rust_renderer, verify_pinned

SETTING_NAME = 'renderer.json'
CHOICES = ('rust', 'cpp')
BACK = 'switch back with `python -m tools.xr_controller renderer set cpp`'


def setting_path(config_home=None):
    home = Path(config_home) if config_home is not None else Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config')
    return home / 'viture-workspace' / SETTING_NAME


def read_setting(path=None):
    """'rust' or 'cpp', None with no file. ValueError when the file cannot be trusted: a start then fails rather than guessing
    which renderer was meant. A file others can read raises PermissionError (read_private_json)."""
    path = Path(path or setting_path())
    if not path.exists(): return None
    try: data = read_private_json(path)
    except ValueError: data = None
    if type(data) is not dict or set(data) != {'schema', 'renderer'} or type(data['schema']) is not int or data['schema'] != 1 \
            or data['renderer'] not in CHOICES:
        raise ValueError(f'{path.name} is invalid; {BACK} or clear it')
    return data['renderer']


def save_setting(choice, path=None):
    if choice not in CHOICES: raise ValueError('renderer must be one of ' + ', '.join(CHOICES))
    path = Path(path or setting_path())
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.' + path.name + '.', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w') as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(dict(schema=1, renderer=choice), stream); stream.flush(); os.fsync(stream.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True); raise


def clear_setting(path=None):
    Path(path or setting_path()).unlink(missing_ok=True)


def choice(config_home=None):
    """The setting for a Start: 'rust' or 'cpp'. Any doubt about the file is a ValueError naming the way back."""
    try: return read_setting(setting_path(config_home)) or 'cpp'
    except OSError as exc: raise ValueError(f'{SETTING_NAME} unreadable: {exc}; {BACK}') from None


def rust_renderer(data_home):
    """The pinned Rust renderer, its bytes checked against its pin now. ValueError, naming the way back, when it cannot be trusted."""
    try: pin = load_rust_renderer(data_home)
    except ValueError as exc: raise ValueError(f'the Rust renderer cannot start: {exc}; {BACK}') from None
    if pin is None:
        raise ValueError(f'the Rust renderer is selected but not installed (no {RUST_RENDERER_PIN}: '
                         f'run `install --build <dir> --rust-renderer <xr-renderer>`); {BACK}')
    try: verify_pinned(pin)
    except ValueError: raise ValueError(f"the Rust renderer cannot start: {pin['path']} changed since it was installed; {BACK}") from None
    return pin


def scope(scope, data_home, config_home=None):
    """The controller's scope for a Start, with the renderer the setting picks. A Pro 2 scope is its bundle's, unchanged."""
    if scope.get('model') == 'pro2' or choice(config_home) != 'rust': return scope
    scope['renderer'] = rust_renderer(data_home)
    if immersive.find_engine(scope['renderer']['path']) is None: scope.pop(sbs.SCOPE_KEY, None)  # no 3D it could not start
    return scope


def start_renderer(consent, installed, data_home, config_home=None):
    """The renderer pin a run launches (runner.execute): the consent's, which must be the one the setting picks now, its bytes
    unchanged. `installed` is load_installed's, whose renderer is the C++ one."""
    if choice(config_home) == 'rust':
        pin = rust_renderer(data_home)
        if consent != pin: raise ValueError(f'consent renderer is not the pinned Rust renderer; {BACK}')
        return pin
    if consent != installed.get('renderer'): raise ValueError('consent renderer is not the pinned install')
    verify_pinned(installed['renderer'])
    return installed['renderer']


def status(data_home, config_home=None):
    """For `renderer status`: the setting, and whether a Rust start would pass its pin check now."""
    picked = read_setting(setting_path(config_home))
    result = dict(renderer=picked or 'cpp', setting=picked)
    try: result['rust_renderer'] = rust_renderer(data_home)['path']
    except ValueError as exc: result['rust_renderer'], result['rust_error'] = None, str(exc)
    return result
