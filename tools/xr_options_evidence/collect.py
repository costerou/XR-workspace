#!/usr/bin/env python3
"""Offline QA runner and evidence index. Never launches a renderer/session/service.

Artifacts are supplied by the native owner. Hashing them establishes integrity,
not provenance, visual correctness, or hardware acceptance.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
SUITES = {
    'wire': ['tests/xr_options_integration'],
    'recovery': ['tests/native_spike/test_recovery.py', 'tests/native_spike/test_supervisor_faults.py',
                 'tests/native_spike/test_tracking.py', 'tests/native_spike/test_tracking_luma.py',
                 'tests/native_spike/test_sbs.py', 'tests/native_spike/test_immersive.py',
                 'tests/native_spike/test_stabilization.py'],
    'input_geometry': ['tests/native_spike/test_free_pointer.py', 'tests/native_spike/test_pointer_zones.py',
                       'tests/native_spike/test_roll_level.py', 'tests/native_spike/test_typing_mode.py',
                       'tests/native_spike/test_control_wire.py'],
}


def artifact(path):
    path = Path(path).resolve()
    if path.suffix.lower() not in {'.png', '.mp4'}:
        raise ValueError('native artifacts must be PNG or MP4')
    with path.open('rb') as stream:
        magic = stream.read(12)
        if path.suffix.lower() == '.png' and not magic.startswith(b'\x89PNG\r\n\x1a\n'):
            raise ValueError('invalid PNG signature')
        if path.suffix.lower() == '.mp4' and magic[4:8] != b'ftyp':
            raise ValueError('invalid MP4 signature')
        stream.seek(0)
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    return dict(path=str(path), bytes=path.stat().st_size, sha256=digest,
                provenance='owner-supplied; native origin and visual correctness unverified')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='new evidence directory')
    parser.add_argument('--suite', action='append', choices=SUITES)
    parser.add_argument('--decoder', type=Path)
    parser.add_argument('--native-build', type=Path, help='built offline pose/stabilization/geometry/transport tests')
    parser.add_argument('--artifact', action='append', default=[], type=Path)
    args = parser.parse_args(argv)
    args.output.mkdir(parents=True, exist_ok=False)
    env = dict(os.environ, PYTHONPATH=str(ROOT)+os.pathsep+str(ROOT/'tests/native_spike')+os.pathsep+str(ROOT/'tests/xr_options_integration'))
    if args.decoder:
        env['CONTROL_WIRE_CHECK'] = str(args.decoder.resolve(strict=True))
    records = []
    for suite in args.suite or list(SUITES):
        command = [sys.executable, '-m', 'pytest', '-q', *SUITES[suite]]
        log = args.output / (suite+'.log')
        try:
            with log.open('w') as stream:
                run = subprocess.run(command, cwd=ROOT, env=env, stdout=stream,
                                     stderr=subprocess.STDOUT, timeout=300)
            code = run.returncode
        except subprocess.TimeoutExpired:
            code = 124
        records.append(dict(suite=suite, command=command, cwd=str(ROOT), exit_code=code,
                            log=str(log.resolve())))
    if args.native_build:
        command = ['ctest', '--test-dir', str(args.native_build.resolve()),
                   '--output-on-failure', '--no-tests=error', '-R',
                   '^(pose|stabilization|view|glass|luma_transport)$']
        log = args.output/'native.log'
        try:
            with log.open('w') as stream:
                run = subprocess.run(command, cwd=ROOT, env=env, stdout=stream,
                                     stderr=subprocess.STDOUT, timeout=300)
            code = run.returncode
        except subprocess.TimeoutExpired:
            code = 124
        records.append(dict(suite='native', command=command, cwd=str(ROOT),
                            exit_code=code, log=str(log.resolve())))
    result = dict(schema=1, offline=True,
                  head=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                  decoder=str(args.decoder.resolve()) if args.decoder else None,
                  decoder_sha256=hashlib.sha256(args.decoder.read_bytes()).hexdigest() if args.decoder else None,
                  tests=records, artifacts=[artifact(p) for p in args.artifact],
                  limitations=['No live XR signoff', 'No artifact origin attestation',
                               'Existing regressions do not prove options-specific lifecycle or v2 maximize'])
    (args.output/'manifest.json').write_text(json.dumps(result, indent=2)+'\n')
    return int(any(r['exit_code'] for r in records))


if __name__ == '__main__':
    sys.exit(main())
