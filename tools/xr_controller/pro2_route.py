"""The Pro 2's route from the panel's normal Start: off until an explicit opt-in names a live-tested bundle.

The supervised test proved the pieces (the pinned bridge, the renderer feed, recovery). This runs the same pieces from the panel,
for as long as the session lasts, with the Pro 2's own settings. Nothing here touches a Luma or Beast start:

  - pro2-route.json, beside profiles.json, is the opt-in. Only `python -m tools.xr_controller pro2-route enable` writes it, and only
    with the result file of a completed supervised run. Absent or invalid, the Pro 2 stays exactly as blocked as it was.
  - The opt-in pins one bundle by manifest hash: its bridge, renderer and plugin, never installed.json. Rebuilding a bundle means
    enabling again, so a run cannot pick up an untested binary, and a Pro 2 rebuild cannot touch another model's pins.
  - pro2-settings.json holds the projection: 44.5 by default, 44.5 to 50 in 1 degree steps, approximate. It is its own file so
    that profiles.json, which every model shares, does not change shape.
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
from tools.native_spike.display_policy import PRO2_DEFAULT_PROJECTION, PRO2_PROJECTIONS
from tools.native_spike.ipc import read_private_json, token
from tools.native_spike.panel_consent import PRO2_KEY, PRO2_TRACKING, load_panel_consent
from tools.native_spike.profiles import ProfileStore, profiles_path

ROUTE_FILE, SETTINGS_FILE = 'pro2-route.json', 'pro2-settings.json'
ROUTE_KEYS = {'schema', 'model', 'bundle', 'manifest_sha256', 'confirmed_run'}
_HEX = re.compile(r'[0-9a-f]{64}\Z')


def _directory(config_dir=None): return Path(config_dir) if config_dir is not None else profiles_path().parent


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


def load_route(config_dir=None):
    """The opt-in with its pinned artifacts, None when there is none. ValueError when it cannot be trusted."""
    path = _directory(config_dir) / ROUTE_FILE
    if not path.exists(): return None
    data = read_private_json(path)
    if not isinstance(data, dict) or set(data) != ROUTE_KEYS or data['schema'] != 1 or data['model'] != 'pro2':
        raise ValueError('Pro 2 route file is invalid')
    token(data['confirmed_run'])
    if not isinstance(data['manifest_sha256'], str) or not _HEX.fullmatch(data['manifest_sha256']): raise ValueError('Pro 2 route file is invalid')
    from tools.pro2_runtime.launch import validate_bundle_location
    root = validate_bundle_location(data['bundle'])
    if manifest_sha(root) != data['manifest_sha256']: raise ValueError('the Pro 2 bundle changed since it was enabled')
    files = json.loads((root / 'manifest.json').read_text())['files']
    return {**data, 'plugin': dict(path=str(root / 'bin/libxr-ownership.so'), sha256=files['bin/libxr-ownership.so']),
            'renderer': dict(path=str(root / 'bin/xr-native-spike'), sha256=files['bin/xr-native-spike'])}


def probe(config_dir=None):
    """For the controller's probe: (route or None, why not). Never raises: a broken opt-in leaves the Pro 2 blocked."""
    try: return load_route(config_dir), None
    except (OSError, ValueError, KeyError, TypeError) as exc: return None, f'Pro 2 route is off: {exc}'


def load_projection(config_dir=None):
    try:
        value = read_private_json(_directory(config_dir) / SETTINGS_FILE).get('projection')
        return value if type(value) in (int, float) and float(value) in PRO2_PROJECTIONS else PRO2_DEFAULT_PROJECTION
    except (OSError, ValueError, AttributeError, OverflowError): return PRO2_DEFAULT_PROJECTION


def save_projection(value, config_dir=None):
    if type(value) not in (int, float) or float(value) not in PRO2_PROJECTIONS:
        raise ValueError('projection must be one of ' + ', '.join(f'{p:g}' for p in PRO2_PROJECTIONS))
    _write(_directory(config_dir) / SETTINGS_FILE, dict(schema=1, projection=float(value)))


def consent_block(route, projection, pose_rate_hz=60):
    return dict(bundle=route['bundle'], manifest_sha256=route['manifest_sha256'], projection=float(projection), pose_rate_hz=pose_rate_hz)


def require(consent, config_dir=None):
    """Before a run does anything: the opt-in still stands, for exactly the bundle this consent names. (root, manifest)."""
    from tools.pro2_runtime.launch import verify_manifest
    route = load_route(config_dir)
    block = consent[PRO2_KEY]
    if route is None: raise ValueError('the Pro 2 route is not enabled')
    if route['bundle'] != block['bundle'] or route['manifest_sha256'] != block['manifest_sha256']:
        raise ValueError('the Pro 2 route changed since this start was confirmed')
    if consent['plugin'] != route['plugin'] or consent['renderer'] != route['renderer']: raise ValueError('Pro 2 consent is not the pinned bundle')
    return verify_manifest(block['bundle'])


def enable(bundle, evidence, config_dir=None):
    """The orchestrator's switch, after a live test: `evidence` is that run's pro2-test-result.json."""
    from tools.pro2_runtime.launch import verify_manifest
    root, _ = verify_manifest(bundle)
    result = read_private_json(evidence)
    run = result.get('run') if isinstance(result, dict) else None
    if not isinstance(result, dict) or result.get('complete') is not True or result.get('errors') or not isinstance(run, dict) \
            or run.get('complete') is not True or run.get('renderer_returncode') != 0:
        raise ValueError('that supervised run did not complete cleanly')
    run_id = token(result.get('run_id'))
    if not run_id.startswith('pro2-supervised-'): raise ValueError('not a supervised Pro 2 run')
    if result.get('manifest_sha256') != manifest_sha(root): raise ValueError('that run did not use this bundle')
    _write(_directory(config_dir) / ROUTE_FILE, dict(schema=1, model='pro2', bundle=str(root), manifest_sha256=manifest_sha(root), confirmed_run=run_id))
    return load_route(config_dir)


def disable(config_dir=None):
    (_directory(config_dir) / ROUTE_FILE).unlink(missing_ok=True)


def execute(consent_path, state_dir, *, config_dir=None, client_factory=None, session_factory=None, run=None):
    """One panel session, with the supervised test's own steps (pro2_test.run_session). The result in the runner's shape."""
    from tools.pro2_runtime.launch import ROUTE_SECONDS, RuntimeSession
    from tools.pro2_runtime.scope import BundleClient
    from .pro2_test import run_session
    raw = read_private_json(consent_path)
    consent = load_panel_consent(consent_path, raw.get('instance'), raw.get('output'))
    root, manifest = require(consent, config_dir)
    block = consent[PRO2_KEY]
    runtime = Path(os.environ['XDG_RUNTIME_DIR'])
    client = client_factory(consent['instance'], runtime) if client_factory else BundleClient(consent['instance'], runtime, root, manifest)
    if client_factory is None: client.check_abi()
    outcome = run_session(client, {**consent, 'sbs_mode': None}, root, runtime, Path(state_dir), consent['desktops'],
                          projection=block['projection'], seconds=ROUTE_SECONDS, pose_rate_hz=block['pose_rate_hz'], panel_route=True,
                          store=ProfileStore(profiles_path(), 'pro2'), session_factory=session_factory or RuntimeSession, run=run)
    done = outcome['run'] or {}
    return dict(complete=bool(done.get('complete')) and not outcome['errors'], renderer_returncode=done.get('renderer_returncode'),
                remaining=done.get('remaining'), error='; '.join(outcome['errors']) or None,
                **({'stop_reason': done['stop_reason']} if done.get('stop_reason') else {}),
                **({'recovery': done['reasons']} if done.get('reasons') else {}),
                **({'provider': outcome['provider']} if outcome.get('provider') else {}))


def recover(consent_path, journal, clear_rules=(), *, client_factory=None):
    """Recover a panel session with the bundle it ran. A later `disable` must not strand the run, so no opt-in is needed here."""
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
    result = recover_pro2_test_managed(client, {**consent, 'sbs_mode': None}, Path(journal), clear_rules=clear_rules)
    if result['complete']:
        client.plugin_unload(consent['plugin']['path'])
        if ownership_plugin_count(client.plugins_loaded()): raise ValueError('plugin unload not confirmed')
    return result
