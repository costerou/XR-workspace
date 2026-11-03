#!/usr/bin/env python3
"""A/B the frame delivery path of two renderer builds with capture_e2e_bench (loopback compositor).

Usage: compare.py --bench BUILD/capture_e2e_bench --before OLD/xr-native-spike --after NEW/xr-native-spike
                  [--modes pattern,cpu,gl-shm,gl-dmabuf] [--trials 5] [--seconds 4] [--tracking] [--capture-hz 119.7]
                  [--malloc-shim BUILD/libxr_malloc_count.so] [--json OUT]
Trials alternate before/after so machine load hits both alike; the table shows medians over trials.
Offline only: the renderer talks to the in-process loopback compositor, never to the desktop.
"""
import argparse
import json
import statistics
import subprocess
import sys

METRICS = [
    ('commits_per_s', 'fps', None),
    ('callback_to_commit_us', 'cb->commit us p50', 'p50'),
    ('callback_to_commit_us', 'cb->commit us p95', 'p95'),
    ('capture_to_present_ms', 'capture->present ms mean', 'mean'),
    ('capture_to_present_ms', 'capture->present ms p95', 'p95'),
    ('captured_frames_per_s', 'captured fr/s', None),
    ('shown_capture_frames_per_s', 'shown fr/s', None),
    ('renderer_cpu_ms_per_s', 'renderer CPU ms/s', None),
    ('main_loop_wakes_per_s', 'loop wakes/s', None),
    ('renderer_voluntary_switches_per_s', 'ctx switches/s', None),
    ('allocations_per_s', 'allocations/s', None),
]


def value(run, key, sub):
    v = run.get(key)
    if isinstance(v, dict):
        return v.get(sub)
    return v


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--bench', required=True)
    p.add_argument('--before', required=True)
    p.add_argument('--after', required=True)
    p.add_argument('--modes', default='pattern,cpu,gl-shm,gl-dmabuf')
    p.add_argument('--trials', type=int, default=5)
    p.add_argument('--seconds', type=float, default=4)
    p.add_argument('--capture-hz', default='119.7')
    p.add_argument('--tracking', action='store_true')
    p.add_argument('--malloc-shim')
    p.add_argument('--json')
    a = p.parse_args()
    results = {}
    for mode in a.modes.split(','):
        for trial in range(a.trials):
            for label, renderer in (('before', a.before), ('after', a.after)) if trial % 2 == 0 else (('after', a.after), ('before', a.before)):
                cmd = [a.bench, '--mode', mode, '--seconds', str(a.seconds), '--capture-hz', a.capture_hz, '--renderer', renderer]
                if a.tracking and mode != 'pattern':
                    cmd.append('--tracking')
                if a.malloc_shim:
                    cmd += ['--malloc-shim', a.malloc_shim]
                out = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
                if out.returncode == 77:
                    print(f'{mode}: skipped ({out.stderr.strip()})', file=sys.stderr)
                    break
                try:
                    run = json.loads(out.stdout)
                except json.JSONDecodeError:
                    print(f'{mode} {label}: no result (rc {out.returncode}) {out.stderr[-400:]}', file=sys.stderr)
                    continue
                if run.get('renderer_rc') != 0 or not run.get('window_complete'):
                    print(f'{mode} {label}: dropped (renderer rc {run.get("renderer_rc")}, window complete {run.get("window_complete")})',
                          file=sys.stderr)
                    continue
                results.setdefault(mode, {}).setdefault(label, []).append(run)
                print(f'  {mode} trial {trial + 1} {label}: fps {run.get("commits_per_s")} cpu {run.get("renderer_cpu_ms_per_s")}', file=sys.stderr)
    table = {}
    for mode, runs in results.items():
        table[mode] = {}
        for key, name, sub in METRICS:
            row = {}
            for label in ('before', 'after'):
                vals = [value(r, key, sub) for r in runs.get(label, [])]
                vals = [v for v in vals if isinstance(v, (int, float))]
                row[label] = round(statistics.median(vals), 2) if vals else None
            if row['before'] is not None or row['after'] is not None:
                table[mode][name] = row
    for mode, rows in table.items():
        print(f'\n{mode}{" (tracking)" if a.tracking and mode != "pattern" else ""}: median of {a.trials} trials, {a.seconds:g} s each')
        print(f'  {"metric":28} {"before":>10} {"after":>10}')
        for name, row in rows.items():
            fmt = lambda v: f'{v:10.2f}' if isinstance(v, (int, float)) else f'{"-":>10}'
            print(f'  {name:28} {fmt(row["before"])} {fmt(row["after"])}')
    if a.json:
        with open(a.json, 'w') as f:
            json.dump({'table': table, 'runs': results}, f, indent=1)
    return 0


if __name__ == '__main__':
    sys.exit(main())
