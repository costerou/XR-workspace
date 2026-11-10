"""How long a start takes, phase by phase (docs/rust-rebuild-plan.md section 4: the phase 0 start-up baseline).

Each mark is the moment a phase began, in CLOCK_MONOTONIC nanoseconds, the clock the renderer's presentation timestamps use.
The runner holds one run per process, so the timeline is the process's own, and a name's first mark wins: a renderer started
again after a USB drop (survive.py) is not part of the start-up.

The marks, in the order a start reaches them (a run without head tracking has no `tracking`):
  confirmed     Start confirmed: the controller writes the run's consent then (state.py confirm_start), so its mtime
  runner        the runner process reached Python (runner.py, before its imports): systemd-run and the interpreter
  verify        consent and pins checked (the Pro 2 route: its bundle and ABI)
  tracking      the tracking driver or the Pro 2 bridge started, up to calibrated / healthy
  plugin        the ownership plugin loaded and read back
  desktops      session mode, presentation parked, XR outputs created, windows adopted
  renderer      the renderer process spawned (supervisor._launch), up to its first frame
  first_status  the renderer's first status line saying a frame was presented or discarded (sent once a second: an upper
                bound on the first frame, never earlier than it)
  first_frame   the first presented frame's own timestamp, from the renderer's timing report on its stderr (the run's unit
                journal), added by `python -m tools.native_spike.startup` when it can read it

`python -m tools.native_spike.startup <state-dir>/<run_id>.result.json [--journal]` prints the phases of a finished run.
"""
import argparse
import json
import os
import subprocess
import sys
import time

CLOCK = 'CLOCK_MONOTONIC'
TIMING_KIND = 'generated_pattern_presentation_timing'  # the renderer's exit report (metrics.cpp Metrics::report)

_marks = {}


def reset():
    _marks.clear()


def mark(name, ns=None):
    """`name` began now (or at `ns`). Only its first mark counts."""
    if name not in _marks: _marks[name] = time.monotonic_ns() if ns is None else int(ns)


def marks():
    return dict(_marks)


def written_ns(path):
    """When `path` was last written, moved onto the monotonic clock. None if it cannot be read."""
    try: mtime = os.stat(path, follow_symlinks=False).st_mtime_ns
    except OSError: return None
    return time.monotonic_ns() - (time.time_ns() - mtime)


def summary(marks):
    """The record kept in `.progress.json` and `.result.json`: the marks, each phase's length up to the next mark (ms), and
    confirm to first frame (the exact frame when known, else the first status: an upper bound)."""
    marks = {k: int(v) for k, v in marks.items()}
    order = sorted(marks.items(), key=lambda item: item[1])
    phases = {name: round((after - at) / 1e6, 3) for (name, at), (_, after) in zip(order, order[1:])}
    out = {'clock': CLOCK, 'marks_ns': marks, 'phases_ms': phases}
    end = marks.get('first_frame', marks.get('first_status'))
    if 'confirmed' in marks and end is not None:
        out['confirm_to_first_frame_ms'] = round((end - marks['confirmed']) / 1e6, 3)
        out['first_frame_from'] = 'first_frame' if 'first_frame' in marks else 'first_status'
    return out


def first_presented_ns(lines):
    """The first presented frame of the run's first renderer: `first_presented_ns` of the first timing report in `lines`
    (the unit journal, one renderer stderr line each). None when there is none, or it has no valid frame."""
    for line in lines:
        start = line.find('{')
        if start < 0 or TIMING_KIND not in line: continue
        try: report = json.loads(line[start:])
        except ValueError: continue
        if isinstance(report, dict) and report.get('kind') == TIMING_KIND:
            ns = report.get('first_presented_ns')
            return ns if type(ns) is int and ns > 0 else None
    return None


def unit_journal(run_id):
    """The run unit's journal lines (controller __main__.spawn names the unit). Read only."""
    out = subprocess.run(['journalctl', '--user', '--unit=viture-xr-' + run_id, '--output=cat', '--no-pager'],
                         capture_output=True, text=True, timeout=30, check=False)
    return out.stdout.splitlines()


def with_first_frame(result, lines):
    """`result`'s start-up summary with `first_frame` from the journal `lines`, when they hold it."""
    marks = dict((result.get('startup') or {}).get('marks_ns') or {})
    ns = first_presented_ns(lines)
    if ns is not None and ns >= marks.get('renderer', 0): marks['first_frame'] = ns
    return summary(marks)


def table(record):
    rows = [f"{name:<13} {ms:>10.1f} ms" for name, ms in record['phases_ms'].items()]
    if 'confirm_to_first_frame_ms' in record:
        rows.append(f"{'confirm to first frame':<24} {record['confirm_to_first_frame_ms']:.1f} ms ({record['first_frame_from']})")
    return '\n'.join(rows)


def main(argv=None):
    ap = argparse.ArgumentParser(prog='tools.native_spike.startup', description='start-up phases of a finished run')
    ap.add_argument('result', help='<state-dir>/<run_id>.result.json')
    ap.add_argument('--journal', action='store_true', help="add the exact first frame from the run unit's journal")
    ap.add_argument('--journal-file', help='the same, from saved `journalctl --output=cat` lines')
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args(argv)
    with open(args.result) as f: result = json.load(f)
    if not isinstance(result, dict) or not isinstance(result.get('startup'), dict):
        print('no start-up record in this result (a runner from before the start-up baseline)', file=sys.stderr); return 2
    lines = []
    if args.journal_file:
        with open(args.journal_file) as f: lines = f.read().splitlines()
    elif args.journal and result.get('run_id'): lines = unit_journal(result['run_id'])
    record = with_first_frame(result, lines)
    print(json.dumps(record, indent=1, sort_keys=True) if args.json else table(record))
    return 0


if __name__ == '__main__':
    sys.exit(main())
