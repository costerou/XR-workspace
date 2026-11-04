"""Pinned binary install: the panel only ever loads/launches these copies, never build/ outputs."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile

RENDERER, PLUGIN, SHIM = 'xr-native-spike', 'libxr-ownership.so', 'libxr-quiet-driver.so'
SHIM_PIN = 'quiet-driver.pin.json'
_HEX = re.compile(r'[0-9a-f]{64}')


def _root(data_home): return Path(data_home) / 'viture-workspace'


def _sha(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    with os.fdopen(fd, 'rb') as f:
        if not stat.S_ISREG(os.fstat(f.fileno()).st_mode): raise ValueError('not a regular file')
        digest = hashlib.sha256()
        for chunk in iter(lambda: f.read(1 << 20), b''): digest.update(chunk)
    return digest.hexdigest()


def _atomic_write(path, data, mode):
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.tmp-')
    try:
        with os.fdopen(fd, 'wb') as f:
            os.fchmod(f.fileno(), mode)
            f.write(data); f.flush(); os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True); raise


def _pin_shim(build_dir, target):
    """The quiet driver (docs/usb-drops.md), beside the renderer and pinned the same way. Optional: a build without it, or an
    older install, runs the plain driver."""
    source = Path(build_dir) / SHIM
    if not source.exists(): return None
    sha = _sha(source)
    dest = Path(target) / SHIM
    entry = {'path': str(dest), 'sha256': sha}
    try:
        verify_pinned(entry); return entry
    except ValueError:
        pass
    fd, tmp = tempfile.mkstemp(dir=target, prefix='.tmp-')
    try:
        with os.fdopen(fd, 'wb') as out, open(source, 'rb') as src:
            shutil.copyfileobj(src, out); out.flush(); os.fsync(out.fileno())
        os.chmod(tmp, 0o500)
        if _sha(tmp) != sha: raise ValueError('build output changed during install')
        os.replace(tmp, dest)
    except BaseException:
        Path(tmp).unlink(missing_ok=True); raise
    return entry


def load_shim(data_home):
    """The installed quiet driver's path, verified, or None."""
    try: entry = json.loads((_root(data_home) / SHIM_PIN).read_text())
    except FileNotFoundError: return None
    verify_pinned(entry)
    return entry['path']


def install(build_dir, data_home):
    build_dir = Path(build_dir)
    try:
        shas = {'renderer': _sha(build_dir / RENDERER), 'plugin': _sha(build_dir / PLUGIN)}
    except OSError as e:
        raise ValueError(f'build output unreadable: {e}') from None
    combined = hashlib.sha256((shas['renderer'] + shas['plugin']).encode('ascii')).hexdigest()[:12]
    root = _root(data_home)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    (root / 'bin').mkdir(mode=0o700, exist_ok=True)
    target = root / 'bin' / combined
    target.mkdir(mode=0o700, exist_ok=True)
    entry = {}
    for key, name in (('renderer', RENDERER), ('plugin', PLUGIN)):
        dest = target / name
        entry[key] = {'path': str(dest), 'sha256': shas[key]}
        try:
            verify_pinned(entry[key]); continue  # identical bytes already pinned
        except ValueError:
            pass
        fd, tmp = tempfile.mkstemp(dir=target, prefix='.tmp-')
        try:
            with os.fdopen(fd, 'wb') as out, open(build_dir / name, 'rb') as src:
                shutil.copyfileobj(src, out); out.flush(); os.fsync(out.fileno())
            os.chmod(tmp, 0o500)
            if _sha(tmp) != shas[key]: raise ValueError('build output changed during install')
            os.replace(tmp, dest)
        except BaseException:
            Path(tmp).unlink(missing_ok=True); raise
    # Its own file: installed.json is read strictly, by whatever worktree a run comes from.
    shim = _pin_shim(build_dir, target)
    if shim is not None: _atomic_write(root / SHIM_PIN, json.dumps(shim, indent=2).encode(), 0o600)
    else: (root / SHIM_PIN).unlink(missing_ok=True)
    _atomic_write(root / 'installed.json', json.dumps(entry, indent=2).encode(), 0o600)
    return entry


def _check_entry(entry, bin_dir):
    if type(entry) is not dict or set(entry) != {'path', 'sha256'}: raise ValueError('malformed installed.json')
    path, sha = entry['path'], entry['sha256']
    if type(path) is not str or type(sha) is not str or not _HEX.fullmatch(sha): raise ValueError('malformed installed.json')
    p = Path(path)
    if not p.is_absolute() or os.path.normpath(path) != path or not p.is_relative_to(bin_dir) or p == bin_dir:
        raise ValueError('pinned path outside install directory')


def load_installed(data_home):
    root = _root(data_home)
    try:
        data = json.loads((root / 'installed.json').read_text())
    except (OSError, ValueError) as e:
        raise ValueError(f'installed.json unreadable: {e}') from None
    if type(data) is not dict or set(data) != {'plugin', 'renderer'}: raise ValueError('malformed installed.json')
    for entry in data.values(): _check_entry(entry, root / 'bin')
    return data


def verify_pinned(entry):
    try:
        ok = _sha(entry['path']) == entry['sha256']
    except (OSError, ValueError, KeyError, TypeError):
        ok = False
    if not ok: raise ValueError('pinned binary changed')


def verify_recovery_pin(entry, data_home):
    """A run's consent-pinned binary: must live under our install bin dir and still hash-match.
    Used only by recovery, so a run interrupted before an upgrade can be finished with its own plugin."""
    _check_entry(entry, _root(data_home) / 'bin')
    verify_pinned(entry)


def install_plugin(repo, config_home):
    """Explicit install only. Callers/tests choose the config root; never start a shell."""
    source = Path(repo).resolve() / 'omarchy-plugin' / 'cody.viture-workspace'
    if not (source / 'manifest.json').is_file():
        raise ValueError('plugin source missing')
    parent = Path(config_home) / 'omarchy' / 'plugins'
    parent.mkdir(parents=True, exist_ok=True)
    link = parent / source.name
    if link.is_symlink():
        if link.resolve() == source:
            return link
        link.unlink()
    elif link.exists():
        raise ValueError('refusing to replace existing non-symlink plugin')
    link.symlink_to(source, target_is_directory=True)
    return link
