"""The Pro 2's route from the panel's normal Start: our own SDK bridge, installed and hash-pinned by `tools.xr_controller install`.

The supervised test proved the pieces (the pinned bridge, the renderer feed, recovery). This runs the same pieces from the panel,
for as long as the session lasts, with the Pro 2's own settings. Nothing here touches a Luma or Beast start:

  - pro2-bundle.pin.json, beside installed.json, names one bundle under the data directory's pro2/ folder by manifest hash: its
    bridge, renderer and plugin, never installed.json. Only the installer writes it (install.install_pro2_bundle), so a run
    cannot pick up an unpinned binary, and a Pro 2 rebuild cannot touch another model's pins. There is no enable step.
    Absent or invalid, the Pro 2 stays exactly as blocked as it was. The opt-in file this replaced is ignored.
  - pro2-settings.json holds the projection (44.5 by default, 44.5 to 50 in 1 degree steps, approximate), the mount pitch (-8.5,
    -11 the alternative) and the pose rate (60). It is its own file so that profiles.json, which every model shares, does not
    change shape. Each value is read on its own: one that cannot be read is this model's default.
  - Nothing falls back. No bundle means no start, never XRLinuxDriver, and a projection that cannot be read is this
    model's 44.5, never Luma's 52 or the renderer's 46.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from tools.native_spike import monitor_rules
from tools.native_spike.display_policy import PRO2_DEFAULT_PROJECTION, PRO2_MOUNT_BOUNDS, PRO2_MOUNT_PITCH, PRO2_POSE_RATE, PRO2_PROJECTIONS
from tools.native_spike.ipc import read_private_json
from tools.native_spike.panel_consent import PRO2_KEY, PRO2_RATES, PRO2_TRACKING, load_panel_consent
from tools.native_spike.profiles import ProfileStore, profiles_path

PIN_FILE, BUNDLE_DIR, SETTINGS_FILE = 'pro2-bundle.pin.json', 'pro2', 'pro2-settings.json'
PIN_KEYS = {'schema', 'model', 'bundle', 'manifest_sha256'}
_HEX = re.compile(r'[0-9a-f]{64}\Z')


def _directory(config_dir=None): return Path(config_dir) if config_dir is not None else profiles_path().parent


def install_root(data_home=None):
    """Where the installer keeps its pins (install._root): the pin file here, the bundles in pro2/."""
    home = Path(data_home) if data_home is not None else Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local/share')
    return home / 'viture-workspace'


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.' + path.name + '.', suffix='.tmp')
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w') as stream:
            json.dump(data, stream); stream.flush(); os.fsync(stream.fileno())
        os.replace(tmp, path)
    except BaseException:
        try: os.unlink(tmp)
        except OSError: pass
        raise


def manifest_sha(root):
    return hashlib.sha256((Path(root) / 'manifest.json').read_bytes()).hexdigest()


def write_pin(bundle, data_home=None):
    """The installer's step (install.install_pro2_bundle): this bundle is the one a Pro 2 start runs."""
    from tools.pro2_runtime.launch import verify_manifest
    root, _ = verify_manifest(bundle)
    _write(install_root(data_home) / PIN_FILE, dict(schema=1, model='pro2', bundle=str(root), manifest_sha256=manifest_sha(root)))
    return load_route(data_home)


def load_route(data_home=None):
    """The installed bundle with its pinned artifacts, None when none is installed. ValueError when it cannot be trusted."""
    install = install_root(data_home)
    path = install / PIN_FILE
    if not path.exists(): return None
    data = read_private_json(path)
    if not isinstance(data, dict) or set(data) != PIN_KEYS or data['schema'] != 1 or data['model'] != 'pro2':
        raise ValueError('Pro 2 pin file is invalid')
    if not isinstance(data['manifest_sha256'], str) or not _HEX.fullmatch(data['manifest_sha256']): raise ValueError('Pro 2 pin file is invalid')
    from tools.pro2_runtime.launch import validate_bundle_location
    root = validate_bundle_location(data['bundle'])
    if root.parent != install / BUNDLE_DIR: raise ValueError('the Pro 2 bundle is not one the installer manages')
    if manifest_sha(root) != data['manifest_sha256']: raise ValueError('the Pro 2 bundle changed since it was installed')
    files = json.loads((root / 'manifest.json').read_text())['files']
    return {**data, 'plugin': dict(path=str(root / 'bin/libxr-ownership.so'), sha256=files['bin/libxr-ownership.so']),
            'renderer': dict(path=str(root / 'bin/xr-native-spike'), sha256=files['bin/xr-native-spike'])}


def probe(data_home=None):
    """For the controller's probe: (route or None, why not). Never raises: a broken pin leaves the Pro 2 blocked."""
    try:
        route = load_route(data_home)
        return route, None if route else 'The Pro 2 bridge bundle is not installed, so these glasses cannot start. Run the install again.'
    except (OSError, ValueError, KeyError, TypeError) as exc: return None, f'Pro 2 route is off: {exc}'


def _valid_projection(value): return type(value) in (int, float) and float(value) in PRO2_PROJECTIONS
def _valid_mount(value): return type(value) in (int, float) and PRO2_MOUNT_BOUNDS[0] <= value <= PRO2_MOUNT_BOUNDS[1]
def _valid_rate(value): return type(value) is int and value in PRO2_RATES


def load_settings(config_dir=None):
    """The Pro 2's settings for a start, each on its own, each this model's default if it cannot be read."""
    try: saved = read_private_json(_directory(config_dir) / SETTINGS_FILE)
    except (OSError, ValueError): saved = {}
    if not isinstance(saved, dict): saved = {}
    def value(key, valid, default):
        try: return saved[key] if valid(saved.get(key)) else default
        except OverflowError: return default
    return dict(projection=float(value('projection', _valid_projection, PRO2_DEFAULT_PROJECTION)),
                mount_pitch_deg=float(value('mount_pitch_deg', _valid_mount, PRO2_MOUNT_PITCH)),
                pose_rate_hz=value('pose_rate_hz', _valid_rate, PRO2_POSE_RATE))


def save_settings(changes, config_dir=None):
    """Only what `changes` names moves. The whole file is written, so a default that later changes does not stay behind: a value
    someone set is kept and one nobody set is left out."""
    checks = dict(projection=(_valid_projection, 'projection must be one of ' + ', '.join(f'{p:g}' for p in PRO2_PROJECTIONS)),
                  mount_pitch_deg=(_valid_mount, f'mount pitch must be {PRO2_MOUNT_BOUNDS[0]:g} to {PRO2_MOUNT_BOUNDS[1]:g} degrees'),
                  pose_rate_hz=(_valid_rate, 'pose rate must be one of ' + ', '.join(map(str, PRO2_RATES))))
    if not changes or set(changes) - set(checks): raise ValueError('unknown Pro 2 setting')
    for key, value in changes.items():
        try: ok = checks[key][0](value)
        except OverflowError: ok = False
        if not ok: raise ValueError(checks[key][1])
    path = _directory(config_dir) / SETTINGS_FILE
    try: saved = read_private_json(path)
    except (OSError, ValueError): saved = {}
    kept = {k: v for k, v in saved.items() if k in checks} if isinstance(saved, dict) else {}
    _write(path, dict(schema=1, **{**kept, **{k: float(v) if k != 'pose_rate_hz' else v for k, v in changes.items()}}))
    return load_settings(config_dir)


def load_projection(config_dir=None): return load_settings(config_dir)['projection']
def save_projection(value, config_dir=None): save_settings(dict(projection=value), config_dir)


def consent_block(route, projection, pose_rate_hz=PRO2_POSE_RATE, mount_pitch_deg=PRO2_MOUNT_PITCH):
    return dict(bundle=route['bundle'], manifest_sha256=route['manifest_sha256'], projection=float(projection), pose_rate_hz=pose_rate_hz, mount_pitch_deg=float(mount_pitch_deg))


def require(consent, data_home=None):
    """Before a run does anything: this is still exactly the bundle installed when the consent was given. (root, manifest)."""
    from tools.pro2_runtime.launch import verify_manifest
    route = load_route(data_home)
    block = consent[PRO2_KEY]
    if route is None: raise ValueError('the Pro 2 bridge bundle is not installed')
    if route['bundle'] != block['bundle'] or route['manifest_sha256'] != block['manifest_sha256']:
        raise ValueError('the Pro 2 route changed since this start was confirmed')
    if consent['plugin'] != route['plugin'] or consent['renderer'] != route['renderer']: raise ValueError('Pro 2 consent is not the pinned bundle')
    return verify_manifest(block['bundle'])


def release_tracking_driver(runtime):
    """A driver outlives its run, and swapping pairs is normal: the Luma's would still be waiting for its glasses, and the bridge
    refuses to start beside any provider. Only ever the one this app started (tracking.stop checks pid and start tick): a driver
    started by anything else stays, and the bridge's own check refuses."""
    from tools.native_spike import tracking
    tracking.stop(runtime)


def execute(consent_path, state_dir, *, data_home=None, client_factory=None, session_factory=None, run=None):
    """One panel session, with the supervised test's own steps (pro2_test.run_session). The result in the runner's shape."""
    from tools.pro2_runtime.launch import ROUTE_SECONDS, RuntimeSession
    from tools.pro2_runtime.scope import BundleClient
    from .pro2_test import run_session
    raw = read_private_json(consent_path)
    consent = load_panel_consent(consent_path, raw.get('instance'), raw.get('output'))
    root, manifest = require(consent, data_home)
    block = consent[PRO2_KEY]
    runtime = Path(os.environ['XDG_RUNTIME_DIR'])
    release_tracking_driver(runtime)
    client = client_factory(consent['instance'], runtime) if client_factory else BundleClient(consent['instance'], runtime, root, manifest)
    if client_factory is None: client.check_abi()
    outcome = run_session(client, consent, root, runtime, Path(state_dir), consent['desktops'],
                          projection=block['projection'], seconds=ROUTE_SECONDS, pose_rate_hz=block['pose_rate_hz'], mount_pitch_deg=block['mount_pitch_deg'], panel_route=True,
                          store=ProfileStore(profiles_path(), 'pro2'), session_factory=session_factory or RuntimeSession, run=run)
    done = outcome['run'] or {}
    return dict(complete=bool(done.get('complete')) and not outcome['errors'], renderer_returncode=done.get('renderer_returncode'),
                remaining=done.get('remaining'), error='; '.join(outcome['errors']) or None,
                **({'stop_reason': done['stop_reason']} if done.get('stop_reason') else {}),
                **({'recovery': done['reasons']} if done.get('reasons') else {}),
                **({'provider': outcome['provider']} if outcome.get('provider') else {}))


def recover(consent_path, journal, clear_rules=(), *, client_factory=None):
    """Recover a panel session with the bundle it ran. A later install must not strand the run, so the pin is not needed here."""
    from tools.native_spike.managed import recover_pro2_test_managed
    from tools.native_spike.hyprland import ownership_plugin_count
    from tools.pro2_runtime.launch import validate_bundle_location, validate_bundle_plugin
    from tools.pro2_runtime.scope import BundleClient
    raw = read_private_json(consent_path)
    consent = load_panel_consent(consent_path, raw['instance'], raw['output'])
    root = validate_bundle_location(consent[PRO2_KEY]['bundle'])
    manifest = json.loads((root / 'manifest.json').read_text())
    # Only the plugin is used here: the broker this run loaded. A changed renderer must not stand between the user and their windows.
    if manifest['files'].get('bin/libxr-ownership.so') != consent['plugin']['sha256']: raise ValueError('the plugin this run pinned is gone')
    validate_bundle_plugin(root, manifest)
    runtime = Path(os.environ['XDG_RUNTIME_DIR'])
    client = client_factory(consent['instance'], runtime) if client_factory else BundleClient(consent['instance'], runtime, root, manifest)
    if ownership_plugin_count(client.plugins_loaded()) != 1: raise ValueError('consented ownership plugin unavailable for recovery')
    result = recover_pro2_test_managed(client, consent, Path(journal), clear_rules=clear_rules)
    if result['complete']:
        client.plugin_unload(consent['plugin']['path'])
        if ownership_plugin_count(client.plugins_loaded()): raise ValueError('plugin unload not confirmed')
    return result
