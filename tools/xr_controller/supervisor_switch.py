"""Which supervisor a run unit runs: the Python runner (the default) or the Rust `xr-workspace supervisor`.

One setting, read at every Start (Host.spawn), so switching either way takes effect at the next run and needs no restart:
  - $XDG_CONFIG_HOME/viture-workspace/supervisor.json, {"schema": 1, "supervisor": "rust" | "python"}. No file is Python.
  - `python -m tools.xr_controller supervisor set rust|python`, `clear` or `status` (a hidden subcommand).

Rust runs the binary the Rust installer pinned (`xr-workspace install`): workspace.pin.json names a bundle under
$XDG_DATA_HOME/viture-workspace/workspace/, and that bundle's manifest.json, whose hash the pin holds, holds the binary's hash.
Both are checked before every Rust start. With rust set and no trustworthy pin the start fails and says how to switch back: it
never falls back to Python quietly, since a week meant to prove Rust must not be Python without anyone knowing (the same rule
as the 3DoF driver's refusal).

What stays on Python whatever the setting:
  - Recover (`recover-run`). The journal format is shared and frozen, so the Python recovery cleans up a Rust run too.
  - A Pro 2 run. The Rust supervisor does not port that route (runner.rs PRO2_NOT_PORTED) and would only fail it.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile

from tools.native_spike.ipc import read_private_json

SETTING_NAME = 'supervisor.json'
CHOICES = ('rust', 'python')
WORKSPACE_PIN, WORKSPACE_DIR, BINARY = 'workspace.pin.json', 'workspace', 'bin/xr-workspace'
PIN_KEYS = {'schema', 'bundle', 'manifest_sha256'}
MAX_MANIFEST = 4 << 20  # bytes; the workspace manifest lists every file of tools/, packaging/ and omarchy-plugin/
_HEX = re.compile(r'[0-9a-f]{64}\Z')
BACK = 'switch back with `python -m tools.xr_controller supervisor set python`'


def setting_path(config_home=None):
    home = Path(config_home) if config_home is not None else Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config')
    return home / 'viture-workspace' / SETTING_NAME


def read_setting(path=None):
    """'rust' or 'python', None with no file. ValueError when the file cannot be trusted: a start then fails rather than
    guessing which supervisor was meant."""
    path = Path(path or setting_path())
    if not path.exists(): return None
    try: data = read_private_json(path)
    except ValueError: data = None
    if type(data) is not dict or set(data) != {'schema', 'supervisor'} or data['schema'] != 1 or data['supervisor'] not in CHOICES:
        raise ValueError(f'{path.name} is invalid; {BACK} or clear it')
    return data['supervisor']


def save_setting(choice, path=None):
    if choice not in CHOICES: raise ValueError('supervisor must be one of ' + ', '.join(CHOICES))
    path = Path(path or setting_path())
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.' + path.name + '.', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w') as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(dict(schema=1, supervisor=choice), stream); stream.flush(); os.fsync(stream.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True); raise


def clear_setting(path=None):
    Path(path or setting_path()).unlink(missing_ok=True)


def _read_regular(path, limit=None):
    """A regular file's bytes, opened without following a symlink at the last component."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    with os.fdopen(fd, 'rb') as f:
        if not stat.S_ISREG(os.fstat(f.fileno()).st_mode): raise ValueError(f'{path} is not a regular file')
        data = f.read() if limit is None else f.read(limit + 1)
    if limit is not None and len(data) > limit: raise ValueError(f'{path} is too large')
    return data


def rust_binary(data_home):
    """The pinned `xr-workspace` binary, checked against its pin now. ValueError, naming the way back, when it cannot be trusted."""
    from tools.pro2_runtime.launch import validate_bundle_location
    root = Path(data_home) / 'viture-workspace'
    try:
        pin = read_private_json(root / WORKSPACE_PIN)
    except FileNotFoundError:
        raise ValueError(f'the Rust supervisor is selected but not installed (no {WORKSPACE_PIN}: run `xr-workspace install`); {BACK}') from None
    try:
        if type(pin) is not dict or set(pin) != PIN_KEYS or pin['schema'] != 1 or type(pin['bundle']) is not str \
                or type(pin['manifest_sha256']) is not str or not _HEX.fullmatch(pin['manifest_sha256']):
            raise ValueError(f'{WORKSPACE_PIN} is invalid')
        bundle = validate_bundle_location(pin['bundle'])
        if bundle.parent != root / WORKSPACE_DIR: raise ValueError('the workspace bundle is not one the installer manages')
        text = _read_regular(bundle / 'manifest.json', MAX_MANIFEST)
        if hashlib.sha256(text).hexdigest() != pin['manifest_sha256']: raise ValueError('the workspace bundle changed since it was installed')
        files = json.loads(text).get('files')
        want = files.get(BINARY) if type(files) is dict else None
        if type(want) is not str or not _HEX.fullmatch(want): raise ValueError(f'the workspace manifest pins no {BINARY}')
        binary = bundle / BINARY
        if hashlib.sha256(_read_regular(binary)).hexdigest() != want: raise ValueError(f'{binary} changed since it was installed')
        return binary
    except (OSError, ValueError, AttributeError) as exc:
        raise ValueError(f'the Rust supervisor cannot start: {exc}; {BACK}') from None


def run_argv(consent_raw, consent, state_dir, data_home, config_home=None):
    """The Rust supervisor's command for this Start, or None for the Python runner."""
    if read_setting(setting_path(config_home)) != 'rust' or consent_raw.get('model') == 'pro2': return None
    return [str(rust_binary(data_home)), 'supervisor', '--consent', str(consent), '--state-dir', str(state_dir)]


def status(data_home, config_home=None):
    """For `supervisor status`: the setting, and whether a Rust start would pass its pin check now."""
    choice = read_setting(setting_path(config_home))
    result = dict(supervisor=choice or 'python', setting=choice)
    try: result['rust_binary'] = str(rust_binary(data_home))
    except ValueError as exc: result['rust_binary'], result['rust_error'] = None, str(exc)
    return result
