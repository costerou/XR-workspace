"""python3 -m tools.latency_baseline: record a run's pose age and read-to-present p50/p95 (phase 0 baseline). Read-only.

    python3 -m tools.latency_baseline --latest            the newest finished Luma run
    python3 -m tools.latency_baseline --run RUN_ID [--out FILE]
    python3 -m tools.latency_baseline --run RUN_ID --log saved.log    a saved `journalctl -o short-iso` log

A run with late latch on (renderer-flags --late-latch-guard-ms) also gets `d4_verdict` (PASS, FAIL or INCONCLUSIVE) and
`d4_reasons`: the D4 gate, read_to_present p50 <= 9 ms and p95 <= 11 ms with no discarded frame and no missed latch over
at least --min-minutes. It does not change the exit code.

Exit codes: 0 PASS (a Luma run of at least --min-minutes with both measurements), 2 INCONCLUSIVE (reasons printed), 1 error.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from .baseline import MIN_DURATION_S, baseline, latest_run, read_json

STATE = Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local/state') / 'viture-workspace'
RUN_ID = re.compile(r'[0-9a-f]{32}')


def journal(run_id):
    done = subprocess.run(['journalctl', '--user', '-u', f'viture-xr-{run_id}', '--no-pager', '-o', 'short-iso'],
                          capture_output=True, text=True, timeout=60)
    if done.returncode != 0: raise OSError(f'journalctl failed: {done.stderr.strip()[:200]}')
    return done.stdout.splitlines()


def main(argv=None):
    parser = argparse.ArgumentParser(prog='latency_baseline', description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    which = parser.add_mutually_exclusive_group(required=True)
    which.add_argument('--run', help='run id (32 hex digits)')
    which.add_argument('--latest', action='store_true', help='the newest finished run with the --tracking driver')
    parser.add_argument('--log', type=Path, help='read this saved journal instead of journalctl')
    parser.add_argument('--state-dir', type=Path, default=STATE, help='the controller state folder (consent and result files)')
    parser.add_argument('--tracking', default='luma', help="the consent's tracking value the run must have (default luma)")
    parser.add_argument('--min-minutes', type=float, default=MIN_DURATION_S / 60)
    parser.add_argument('--out', type=Path, help='also write the record here (JSON)')
    args = parser.parse_args(argv)
    run_id = latest_run(args.state_dir, args.tracking) if args.latest else args.run
    if not run_id:
        print(f'no finished {args.tracking} run in {args.state_dir}', file=sys.stderr); return 2
    if not RUN_ID.fullmatch(run_id):
        print('a run id is 32 hex digits', file=sys.stderr); return 1
    try:
        lines = args.log.read_text(errors='replace').splitlines() if args.log else journal(run_id)
        consent, result = read_json(args.state_dir / f'{run_id}.json'), read_json(args.state_dir / f'{run_id}.result.json')
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f'cannot read the run: {exc}', file=sys.stderr); return 1
    record = baseline(lines, run_id, consent, result, args.min_minutes * 60, args.tracking)
    text = json.dumps(record, indent=2)
    print(text)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + '\n')
    return 0 if record['verdict'] == 'PASS' else 2


if __name__ == '__main__':
    sys.exit(main())
