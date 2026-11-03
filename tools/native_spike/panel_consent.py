"""P2-panel consent: the settings panel's confirmed scope. Always grants managed-output capture; never input/SDK."""
import json
import os
import re

from .geometry import validate_desktops
from .ipc import read_private_json, token
from .profiles import MODELS
from .supervisor import CAPTURE_ALLOW, CAPTURE_SCOPE

PANEL_GATE = 'P2-panel'
PANEL_KEYS = {'schema', 'gate', 'run_id', 'instance', 'output', 'recovery_output', 'model', 'allow', 'capture',
              'tracking', 'mode_switch', 'plugin', 'renderer', 'desktops', 'input', 'sdk'}
FIXED_KEYS = {'schema', 'gate', 'allow', 'capture', 'input', 'sdk'}
LUMA_MODE = {'width': 1920, 'height': 1200, 'refresh_hz': 120}
SHA256 = re.compile(r'[0-9a-f]{64}\Z')


def _artifact(value):
    if not isinstance(value, dict) or set(value) != {'path', 'sha256'}: raise ValueError('invalid artifact')
    path, sha = value['path'], value['sha256']
    if not isinstance(path, str) or not path.startswith('/') or '\0' in path: raise ValueError('artifact path must be absolute')
    if not isinstance(sha, str) or not SHA256.fullmatch(sha): raise ValueError('invalid sha256')
    return {'path': path, 'sha256': sha}


def _validate(data, instance=None, output=None):
    if not isinstance(data, dict) or set(data) != PANEL_KEYS: raise ValueError('invalid consent fields')
    if type(data['schema']) is not int or data['schema'] != 1 or data['gate'] != PANEL_GATE:
        raise ValueError(f'{PANEL_GATE} consent required')
    token(data['run_id']); token(data['instance']); token(data['output']); token(data['recovery_output'])
    if instance is not None and data['instance'] != instance: raise ValueError('consent scope mismatch')
    if output is not None and data['output'] != output: raise ValueError('consent scope mismatch')
    if len(data['run_id']) > 80: raise ValueError('run id too long')
    if data['recovery_output'] == data['output']: raise ValueError('independent physical recovery output required')
    if not isinstance(data['model'], str) or data['model'] not in MODELS: raise ValueError('unknown model')
    if not isinstance(data['capture'], str) or data['capture'] != CAPTURE_SCOPE or data['allow'] != CAPTURE_ALLOW:
        raise ValueError('exact managed-output capture scope required')
    tracking = data['tracking']
    if tracking is not False and (not isinstance(tracking, str) or tracking != 'xrlinuxdriver'):
        raise ValueError('tracking scope must be false or "xrlinuxdriver"')
    mode = data['mode_switch']
    if mode is not False:
        if data['model'] != 'luma-ultra': raise ValueError('mode switch is Luma Ultra only')
        if not isinstance(mode, dict) or set(mode) != set(LUMA_MODE) or any(type(mode[k]) is not int or mode[k] != v for k, v in LUMA_MODE.items()):
            raise ValueError('mode switch must be exactly 1920x1200@120')
    data['plugin'] = _artifact(data['plugin']); data['renderer'] = _artifact(data['renderer'])
    # The desktops the session starts with (1..MAX_DESKTOPS). The approved capture scope is the run's own managed outputs:
    # desktops a pill adds, removes or reshapes during the session stay inside it (desktops.py).
    data['desktops'] = validate_desktops(data['desktops'])
    if data['input'] is not False or data['sdk'] is not False: raise ValueError('input/SDK not authorized')
    return data


def build_consent(scope):
    expected = PANEL_KEYS - FIXED_KEYS
    if not isinstance(scope, dict) or set(scope) != expected: raise ValueError('invalid scope fields')
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
