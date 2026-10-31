"""Explicit CLI. Import/help never connects to a compositor."""
import argparse
import json
import os
from pathlib import Path
import sys
from .hyprland import HyprlandClient, monitors
from .ipc import read_private_json, token



def load_consent(path, instance, output, *, presentation_only=False, animate_pattern_ms=None):
    data = read_private_json(path)
    keys = {'schema', 'gate', 'run_id', 'instance', 'output', 'recovery_output', 'allow',
            'capture', 'input', 'sdk', 'mode_switch'}
    if animate_pattern_ms is not None:
        from .supervisor import validate_pattern_duration
        validate_pattern_duration(animate_pattern_ms)
        keys.add('animate_pattern_ms')
        if not presentation_only or not isinstance(data, dict) or type(data.get('animate_pattern_ms')) is not int or data['animate_pattern_ms'] != animate_pattern_ms:
            raise ValueError('exact generated animation duration consent required')
    if not isinstance(data, dict) or set(data) != keys: raise ValueError('invalid consent fields')
    gate = 'P2-presentation-only' if presentation_only else 'P2'
    if type(data['schema']) is not int or data['schema'] != 1 or data['gate'] != gate: raise ValueError(f'{gate} consent required')
    if data['instance'] != instance or data['output'] != output: raise ValueError('consent scope mismatch')
    token(data['run_id']); token(output); token(data['recovery_output'])
    if len(data['run_id']) > 80: raise ValueError('run id too long')
    if data['recovery_output'] == output: raise ValueError('independent physical recovery output required')
    from .supervisor import CAPTURE_SCOPE, CAPTURE_ALLOW
    if not presentation_only and type(data['capture']) is str and data['capture'] == CAPTURE_SCOPE:
        # Explicit managed-output capture grant: only the run's own XR-n outputs, in memory.
        if data['allow'] != CAPTURE_ALLOW: raise ValueError('exact managed-output capture scope required')
    else:
        allowed = ['test-pattern'] if presentation_only else ['headless-outputs', 'test-pattern']
        if data['allow'] != allowed or data['capture'] is not False: raise ValueError('exact test-pattern scope required')
    if any(data[k] is not False for k in ('input', 'sdk', 'mode_switch')):
        raise ValueError('input/SDK/mode transitions not authorized by this CLI')
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
    for command in ('run', 'present'):
        p = commands.add_parser(command); p.add_argument('--instance', required=True)
        p.add_argument('--output', required=True); p.add_argument('--consent-file', type=Path, required=True)
        if command == 'present':
            p.add_argument('--animate-pattern-ms', help='generated SHM animation, 1..60000 ms; exact consent required (default static)')
    p = commands.add_parser('recover'); p.add_argument('--journal', type=Path, required=True)
    p.add_argument('--instance', required=True); p.add_argument('--output', required=True)
    p.add_argument('--consent-file', type=Path, required=True)
    # Explicit isolated-VM mode: separate commands and consent gate, never a fallback.
    for command in ('isolated-run', 'isolated-recover'):
        p = commands.add_parser(command, help='disposable isolated VM only; separate VM-isolated consent')
        p.add_argument('--instance', required=True); p.add_argument('--output', required=True)
        p.add_argument('--consent-file', type=Path, required=True)
        if command == 'isolated-recover': p.add_argument('--journal', type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command in ('isolated-run', 'isolated-recover'):
        return _isolated(args)
    try:
        animation = getattr(args, 'animate_pattern_ms', None)
        if animation is not None:
            import re
            if not re.fullmatch(r'[1-9][0-9]{0,4}', animation): raise ValueError('invalid animation duration')
            animation = int(animation)
        # Consent check occurs before runtime lookup, socket connect, or file creation.
        consent = load_consent(args.consent_file, args.instance, args.output,
                               presentation_only=args.command == 'present', animate_pattern_ms=animation) if args.command in ('run', 'present', 'recover') else None
        runtime = Path(os.environ['XDG_RUNTIME_DIR'])
        client = HyprlandClient(args.instance, runtime)
        if args.command == 'recover':
            from .managed import recover_managed
            result = recover_managed(client, consent, args.journal)
            print(json.dumps(result, sort_keys=True))
            return 0 if result['complete'] else 2
        result = preflight(client)
        if args.command == 'preflight':
            print(json.dumps(result, sort_keys=True)); return 0
        if args.command == 'present':
            from .supervisor import run_presentation_only, validate_physical_pair
            validate_physical_pair(client, result['session'], args.output, consent['recovery_output'])
            wayland = client.connect_wayland(result['session'])
            returncode = run_presentation_only(client, consent['run_id'], result['session'],
                                              args.output, consent['recovery_output'], wayland, animate_pattern_ms=animation)
            return 0 if returncode == 0 else 2
        from .managed import run_managed
        result = run_managed(client, consent, runtime)
        print(json.dumps(result, sort_keys=True))
        return 0 if result['complete'] and result['renderer_returncode'] == 0 else 2
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f'blocked: {exc}', file=sys.stderr); return 2

def _isolated(args):
    from . import isolated
    try:
        # Forbidden instance and consent are checked before runtime lookup or connect.
        consent = isolated.load_isolated_consent(args.consent_file, args.instance, args.output)
        runtime = Path(os.environ['XDG_RUNTIME_DIR'])
        client = HyprlandClient(args.instance, runtime)
        if args.command == 'isolated-recover':
            result = isolated.recover_isolated(client, consent, args.journal)
            print(json.dumps(result, sort_keys=True))
            return 0 if result['complete'] else 2
        result = isolated.run_isolated(client, consent, runtime)
        print(json.dumps(result, sort_keys=True))
        return 0 if result['complete'] and result['renderer_returncode'] == 0 else 2
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f'blocked: {exc}', file=sys.stderr); return 2

if __name__ == '__main__': raise SystemExit(main())
