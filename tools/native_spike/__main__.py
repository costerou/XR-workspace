"""Explicit CLI. Import/help never connects to a compositor."""
import argparse
import json
import os
from pathlib import Path
import stat
import sys
from .hyprland import HyprlandClient, RecoveryAdapter, OwnershipBlocked, monitors, plan_outputs
from .ipc import strict_json, token
from .journal import Journal, recover


def load_consent(path, instance, output):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        st = os.fstat(stream.fileno())
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) != 0o600 or st.st_nlink != 1:
            raise PermissionError('consent must be owned regular single-link mode 0600')
        raw = stream.read(16385)
    if len(raw) > 16384: raise ValueError('consent too large')
    data = strict_json(raw)
    keys = {'schema', 'gate', 'run_id', 'instance', 'output', 'recovery_output', 'allow',
            'capture', 'input', 'sdk', 'mode_switch'}
    if not isinstance(data, dict) or set(data) != keys: raise ValueError('invalid consent fields')
    if type(data['schema']) is not int or data['schema'] != 1 or data['gate'] != 'P2': raise ValueError('P2 consent required')
    if data['instance'] != instance or data['output'] != output: raise ValueError('consent scope mismatch')
    token(data['run_id']); token(output); token(data['recovery_output'])
    if len(data['run_id']) > 80: raise ValueError('run id too long')
    if data['recovery_output'] == output: raise ValueError('independent physical recovery output required')
    if data['allow'] != ['headless-outputs', 'test-pattern']: raise ValueError('exact test-pattern scope required')
    if any(data[k] is not False for k in ('capture', 'input', 'sdk', 'mode_switch')):
        raise ValueError('capture/input/SDK/mode transitions not authorized by this CLI')
    return data


def preflight(client):
    identity = client.verified_session()
    snapshot = monitors(client.query('monitors'))
    return {'session': identity['id'], 'monitors': snapshot,
            'managed_output_ownership': 'blocked', 'presentation': 'not exercised',
            'capture': False, 'sdk': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description='Native XR spike: fail-closed P2 test-pattern qualification')
    commands = parser.add_subparsers(dest='command', required=True)
    p = commands.add_parser('preflight'); p.add_argument('--instance', required=True)
    p = commands.add_parser('run'); p.add_argument('--instance', required=True)
    p.add_argument('--output', required=True); p.add_argument('--consent-file', type=Path, required=True)
    p = commands.add_parser('recover'); p.add_argument('--journal', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        # Consent check occurs before runtime lookup, socket connect, or file creation.
        consent = load_consent(args.consent_file, args.instance, args.output) if args.command == 'run' else None
        if args.command == 'recover':
            # No saved independent incarnation continuity survives restart. In particular,
            # do not scan sessions or infer an instance/output from a journal filename.
            raise OwnershipBlocked('restart recovery blocked: no independent persisted incarnation proof; '
                                   'journal preserved. Do not remove outputs by name.')
        runtime = Path(os.environ['XDG_RUNTIME_DIR'])
        client = HyprlandClient(args.instance, runtime)
        result = preflight(client)
        if args.command == 'preflight':
            print(json.dumps(result, sort_keys=True)); return 0
        adapter = RecoveryAdapter(client, approved_physical=consent['recovery_output'])
        if not any(o['physical'] for o in adapter.query('outputs')):
            raise ValueError('approved usable independent physical recovery output missing')
        target = [m for m in result['monitors'] if m['name'] == consent['output'] and not m['disabled'] and m['dpmsStatus']]
        if len(target) != 1: raise ValueError('approved presentation output missing')
        plan_outputs(result['monitors'], client.query('workspaces'), consent['run_id'])
        client.require_output_ownership()  # stop BEFORE journaling, output creation or renderer launch
        raise OwnershipBlocked('managed lifecycle not qualified; no renderer launched')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f'blocked: {exc}', file=sys.stderr); return 2

if __name__ == '__main__': raise SystemExit(main())
