"""P2-panel consent: the settings panel's confirmed scope. Always grants managed-output capture; never input/SDK."""
import json
import os
import re

from .geometry import validate_desktops
from .ipc import read_private_json, token
from .profiles import MODELS
from .sbs import SCOPE_KEY as SBS_KEY, scope_ok as sbs_scope_ok
from .supervisor import CAPTURE_ALLOW, CAPTURE_SCOPE
from .tracking import DOF3, LUMA, SCOPES

PANEL_GATE = 'P2-panel'
PANEL_KEYS = {'schema', 'gate', 'run_id', 'instance', 'output', 'recovery_output', 'model', 'allow', 'capture',
              'tracking', 'mode_switch', 'plugin', 'renderer', 'desktops', 'input', 'sdk'}
FIXED_KEYS = {'schema', 'gate', 'allow', 'capture', 'input', 'sdk'}
LUMA_PIN = 'luma_driver'  # the pinned binary a consent with tracking 'luma' runs; present then and only then
# Present only for the 3DoF test driver, so a standard run's consent stays what older code reads (a rollback, a recovery).
# It is its own key: `tracking` stays "xrlinuxdriver", which the pose feed and the incident monitor test for.
DRIVER_KEY = 'tracking_driver'
LUMA_MODE = {'width': 1920, 'height': 1200, 'refresh_hz': 120}
SHA256 = re.compile(r'[0-9a-f]{64}\Z')


def _artifact(value):
    if not isinstance(value, dict) or set(value) != {'path', 'sha256'}: raise ValueError('invalid artifact')
    path, sha = value['path'], value['sha256']
    if not isinstance(path, str) or not path.startswith('/') or '\0' in path: raise ValueError('artifact path must be absolute')
    if not isinstance(sha, str) or not SHA256.fullmatch(sha): raise ValueError('invalid sha256')
    return {'path': path, 'sha256': sha}


def _keys(data):
    keys = PANEL_KEYS | {LUMA_PIN} if data.get('tracking') == LUMA else PANEL_KEYS
    keys = keys | {DRIVER_KEY} if DRIVER_KEY in data else keys
    # Present only where the depth engine is installed, so any other run's consent stays what older code reads.
    return keys | {SBS_KEY} if SBS_KEY in data else keys


def _validate(data, instance=None, output=None):
    if not isinstance(data, dict) or set(data) != _keys(data): raise ValueError('invalid consent fields')
    if type(data['schema']) is not int or data['schema'] != 1 or data['gate'] != PANEL_GATE:
        raise ValueError(f'{PANEL_GATE} consent required')
    token(data['run_id']); token(data['instance']); token(data['output']); token(data['recovery_output'])
    if instance is not None and data['instance'] != instance: raise ValueError('consent scope mismatch')
    if output is not None and data['output'] != output: raise ValueError('consent scope mismatch')
    if len(data['run_id']) > 80: raise ValueError('run id too long')
    if data['recovery_output'] == data['output']: raise ValueError('independent physical recovery output required')
    if not isinstance(data['model'], str) or data['model'] not in MODELS: raise ValueError('unknown model')
    if data['model'] == 'pro2': raise ValueError('VITURE PRO 2 driver and display support unverified; start consent unavailable')
    if not isinstance(data['capture'], str) or data['capture'] != CAPTURE_SCOPE or data['allow'] != CAPTURE_ALLOW:
        raise ValueError('exact managed-output capture scope required')
    tracking = data['tracking']
    if tracking is not False and (not isinstance(tracking, str) or tracking not in SCOPES):
        raise ValueError('tracking scope must be false, "xrlinuxdriver" or "luma"')
    if DRIVER_KEY in data and (data[DRIVER_KEY] != DOF3 or tracking != 'xrlinuxdriver'):
        raise ValueError('tracking driver must be "3dof", on XRLinuxDriver tracking')
    mode = data['mode_switch']
    if mode is not False:
        if data['model'] != 'luma-ultra': raise ValueError('mode switch is Luma Ultra only')
        if not isinstance(mode, dict) or set(mode) != set(LUMA_MODE) or any(type(mode[k]) is not int or mode[k] != v for k, v in LUMA_MODE.items()):
            raise ValueError('mode switch must be exactly 1920x1200@120')
    if SBS_KEY in data:  # sbs.py: following the glasses to their own 3D mode, once a person has held R1 on them
        if mode is False or data['model'] != 'luma-ultra' or not sbs_scope_ok(data[SBS_KEY]):
            raise ValueError('3D mode must be exactly 3840x1200 at 90 or 60, on a Luma Ultra run that switches modes')
    data['plugin'] = _artifact(data['plugin']); data['renderer'] = _artifact(data['renderer'])
    if LUMA_PIN in data: data[LUMA_PIN] = _artifact(data[LUMA_PIN])
    # The desktops the session starts with (1..MAX_DESKTOPS). The approved capture scope is the run's own managed outputs:
    # desktops a pill adds, removes or reshapes during the session stay inside it (desktops.py).
    data['desktops'] = validate_desktops(data['desktops'])
    if data['input'] is not False or data['sdk'] is not False: raise ValueError('input/SDK not authorized')
    return data


def build_consent(scope):
    if not isinstance(scope, dict) or set(scope) != _keys(scope) - FIXED_KEYS: raise ValueError('invalid scope fields')
    consent = {'schema': 1, 'gate': PANEL_GATE, 'allow': list(CAPTURE_ALLOW), 'capture': CAPTURE_SCOPE,
               'input': False, 'sdk': False, **json.loads(json.dumps(scope))}
    return _validate(consent)


def write_consent(directory_fd, consent):
    consent = _validate(json.loads(json.dumps(consent)))
    name = consent['run_id'] + '.json'
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=directory_fd)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            fd = -1
            stream.write(json.dumps(consent, separators=(',', ':'), sort_keys=True).encode())
            stream.flush(); os.fsync(stream.fileno())
        os.fsync(directory_fd)
    except BaseException:
        if fd >= 0: os.close(fd)
        try: os.unlink(name, dir_fd=directory_fd)
        except OSError: pass
        raise
    return name


def load_panel_consent(path, instance, output):
    token(instance); token(output)
    return _validate(read_private_json(path), instance, output)
