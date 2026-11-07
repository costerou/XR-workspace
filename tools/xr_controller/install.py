"""Pinned binary install: the panel only ever loads/launches these copies, never build/ outputs."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile

from tools.native_spike.tracking import DOF3, DRIVER as STANDARD_DRIVER, LUMA, XRDRIVER

RENDERER, PLUGIN, SHIM = 'xr-native-spike', 'libxr-ownership.so', 'libxr-quiet-driver.so'
SHIM_PIN = 'quiet-driver.pin.json'
LUMA_DRIVER, LUMA_KEY = 'xr-luma-driver', 'luma_driver'
PRO2_DIR = 'pro2'  # bundles live in <root>/pro2/<manifest hash>; pro2_route.py reads the pin, this writes it
ENGINE, ENGINE_PYTHON = 'xr-depth-engine', ('.cache', 'viture-depth', 'venv', 'bin', 'python')
REPO = Path(__file__).resolve().parents[2]
_HEX = re.compile(r'[0-9a-f]{64}')
# The 3DoF test driver (docs/tracking-3dof-ab.md), built by tools/xrdriver_3dof/build.sh. Pinned here, not in installed.json:
# `install` rewrites that file and an older controller refuses any extra key, so a rollback stays clean. A rebuild changes
# the hash, so it needs a new value here; until then the option refuses to start rather than run something else.
DRIVER_3DOF_VERSION = '2.11.9-958908-pr147-pr148-3dof'
DRIVER_3DOF_SHA256 = '623c3dd03d204a125b1cdc32307a985bd2597d408c27edf1770df052fc44dcb8'


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


def _luma_output(build_dir):
    """The built driver, if this build has one (XR_ENABLE_LUMA_DRIVER): beside the renderer or in its own build folder."""
    for path in (build_dir / LUMA_DRIVER, build_dir / 'driver' / LUMA_DRIVER):
        if path.is_file(): return path
    return None


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


def _write_engine(target, home=None, repo=None):
    """The AI depth engine's launcher, beside the renderer, where tools/native_spike/immersive.py looks for it (docs/immersive-3d.md).
    The engine is Python in the model's own environment (tools/depth_engine/setup.sh, 21 GB, no sudo); this names that and this
    checkout, and sits at a low priority so the renderer always goes first. No environment, no launcher: 3D then says it is not
    installed. Optional, like the quiet driver. Its name, not a pin: what it runs is a checkout, as the supervisor is."""
    python = Path(home or Path.home()).joinpath(*ENGINE_PYTHON)
    dest = Path(target) / ENGINE
    if not python.is_file():
        dest.unlink(missing_ok=True)
        return None
    repo = str(repo or REPO)
    if not re.fullmatch(r'[A-Za-z0-9_./-]+', repo) or not re.fullmatch(r'[A-Za-z0-9_./-]+', str(python)): raise ValueError('unsafe path for the depth engine')
    run = f"import sys,runpy; sys.path.insert(0,'{repo}'); sys.argv[0]='xr-depth-engine'; runpy.run_module('tools.depth_engine',run_name='__main__')"
    _atomic_write(dest, f'#!/bin/sh\nexec nice -n 5 {python} -I -c "{run}" serve --supervised "$@"\n'.encode(), 0o500)
    return dest


def load_shim(data_home):
    """The installed quiet driver's path, verified, or None."""
    try: entry = json.loads((_root(data_home) / SHIM_PIN).read_text())
    except FileNotFoundError: return None
    verify_pinned(entry)
    return entry['path']


def install(build_dir, data_home):
    build_dir = Path(build_dir)
    sources = {'renderer': build_dir / RENDERER, 'plugin': build_dir / PLUGIN}
    luma = _luma_output(build_dir)
    if luma: sources[LUMA_KEY] = luma  # main() refuses a build without it: here an older build dir can still be read back
    try:
        shas = {key: _sha(path) for key, path in sources.items()}
    except OSError as e:
        raise ValueError(f'build output unreadable: {e}') from None
    # The Luma driver joins the hash, so a new driver alone gets its own folder and a running session's pins stay true.
    combined = hashlib.sha256(''.join(shas.values()).encode('ascii')).hexdigest()[:12]
    root = _root(data_home)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    (root / 'bin').mkdir(mode=0o700, exist_ok=True)
    target = root / 'bin' / combined
    target.mkdir(mode=0o700, exist_ok=True)
    entry = {}
    for key, source in sources.items():
        dest = target / source.name
        entry[key] = {'path': str(dest), 'sha256': shas[key]}
        try:
            verify_pinned(entry[key]); continue  # identical bytes already pinned
        except ValueError:
            pass
        fd, tmp = tempfile.mkstemp(dir=target, prefix='.tmp-')
        try:
            with os.fdopen(fd, 'wb') as out, open(source, 'rb') as src:
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
    _write_engine(target)
    _atomic_write(root / 'installed.json', json.dumps(entry, indent=2).encode(), 0o600)
    return entry


def install_pro2_bundle(build_dir, data_home, builder=None):
    """Build the Pro 2's bundle (bridge, renderer, plugin, this checkout's tools) and pin it as the one a Pro 2 start runs. The
    folder is named by manifest hash and never replaced, like bin/: a running session's pins stay true, and the bundle before
    this one stays for a rollback. Compiles the bridge; loads no SDK and opens no device. Nothing is pinned unless it all worked."""
    from . import pro2_route
    if builder is None:
        from tools.pro2_runtime.package import build as builder
    root = _root(data_home)
    (root / PRO2_DIR).mkdir(mode=0o700, parents=True, exist_ok=True)
    staging = root / PRO2_DIR / ('.stage-' + os.urandom(4).hex())
    try:
        builder(build_dir, staging)
        name = pro2_route.manifest_sha(staging)[:12]
        final = root / PRO2_DIR / name
        if final.exists():
            if pro2_route.manifest_sha(final)[:12] != name: raise ValueError('an installed Pro 2 bundle changed')
        else: os.rename(staging, final)
    finally:
        if staging.exists(): shutil.rmtree(staging)
    return pro2_route.write_pin(final, data_home)


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
    if type(data) is not dict or set(data) - {LUMA_KEY} != {'plugin', 'renderer'}: raise ValueError('malformed installed.json')
    for entry in data.values(): _check_entry(entry, root / 'bin')
    return data


def verify_pinned(entry):
    try:
        ok = _sha(entry['path']) == entry['sha256']
    except (OSError, ValueError, KeyError, TypeError):
        ok = False
    if not ok: raise ValueError('pinned binary changed')


def tracking_drivers(data_home, installed=None):
    """Every driver a start can run, for the controller; the panel gets no list (Controller._driver_in_use), and which one runs
    is decided per model. Read every controller tick, so `available` is only a stat, no hash. `installed` is the probe's
    installed.json, whose pin says where our driver is."""
    path = _root(data_home) / 'xrdriver-3dof' / DRIVER_3DOF_VERSION / 'xrDriver'
    luma = (installed or {}).get(LUMA_KEY) or {}
    return [dict(key=XRDRIVER, label='XRLinuxDriver', path=STANDARD_DRIVER, sha256=None, available=True),
            dict(key=DOF3, label='3DoF test', path=str(path), sha256=DRIVER_3DOF_SHA256, available=path.is_file()),
            dict(key=LUMA, label='XR Luma driver', path=luma.get('path', ''), sha256=luma.get('sha256'), available=bool(luma))]


def verify_tracking_driver(choice):
    """Refuses a test build that is missing or changed. The standard driver belongs to pacman and is not pinned."""
    if choice['sha256'] is not None: verify_pinned(choice)


def tracking_driver_path(key, data_home):
    """The binary a run's consent names ('xrdriver' or '3dof'), its pin checked now. Never falls back to the other one:
    a 3DoF session that quietly ran the standard driver would make the test's result wrong. The Luma driver is not here:
    its pin travels in the consent."""
    for choice in tracking_drivers(data_home):
        if choice['key'] == key and key != LUMA:
            verify_tracking_driver(choice)
            return choice['path']
    raise ValueError('unknown tracking driver')


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
