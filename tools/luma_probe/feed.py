"""Records the published head pose and compares two recordings. Works on either driver's feed.

    python -m tools.luma_probe.feed record --seconds 60 --out run.csv
    python -m tools.luma_probe.feed report run.csv
    python -m tools.luma_probe.feed compare old.csv new.csv

The feed is the 16 floats spike/src/pose.cpp reads: x y z w three times, then three timestamps. The
segment is attached read-only and the driver's mutex is left alone on purpose, so this cannot stall a
driver or the renderer. A read can therefore tear; a tear that breaks the quaternion's length is
dropped, and one that does not is two neighbouring samples, which is harmless at these scales.
"""
import argparse
import contextlib
import csv
import ctypes
import math
import os
from pathlib import Path
import struct
import sys
import time
from typing import NamedTuple

DEFAULT_FEED = '/tmp/shader_runtime_pose_orientation'
_SEGMENT_BYTES = 64
_SHM_RDONLY = 0o10000
_MAX_NORM_ERROR = 0.05
_MIN_ROWS = 10
_END_WINDOW_S = 5.0
_GAP_NS = 100_000_000


class Row(NamedTuple):
    host_ns: int
    device_ms: float
    x: float
    y: float
    z: float
    w: float


def decode(raw, host_ns):
    if len(raw) < _SEGMENT_BYTES:
        return None
    values = struct.unpack_from('<16f', raw)
    q, device_ms = values[:4], values[12]
    if not all(math.isfinite(v) for v in (*q, device_ms)):
        return None
    norm = math.sqrt(sum(v * v for v in q))
    if abs(norm - 1) > _MAX_NORM_ERROR:
        return None
    return Row(host_ns, device_ms, *(v / norm for v in q))


@contextlib.contextmanager
def read_segment(path):
    """Yields a function returning the feed's bytes. The attachment is read-only."""
    if not os.path.exists(path):
        raise ValueError(f'feed absent: {path} (is a tracking driver running?)')
    libc = ctypes.CDLL(None, use_errno=True)
    libc.ftok.argtypes = [ctypes.c_char_p, ctypes.c_int]
    libc.shmat.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
    libc.shmat.restype = ctypes.c_void_p
    libc.shmdt.argtypes = [ctypes.c_void_p]
    key = libc.ftok(os.fsencode(path), 0)
    segment = -1 if key == -1 else libc.shmget(key, 0, 0)
    if segment < 0:
        raise ValueError(f'feed segment missing for {path}')
    address = libc.shmat(segment, None, _SHM_RDONLY)
    if address in (None, ctypes.c_void_p(-1).value):
        raise ValueError(f'feed attach failed for {path}')
    try:
        yield lambda: ctypes.string_at(address, _SEGMENT_BYTES)
    finally:
        libc.shmdt(address)


def record(path, out, seconds, poll_s=0.0005, clock=time.monotonic_ns):
    """Writes each new sample to `out`, which must not exist yet. Returns how many."""
    count, last = 0, None
    with read_segment(path) as read, open(out, 'x', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(Row._fields)
        start = clock()
        while True:
            now = clock()
            if now - start > seconds * 1e9:
                return count
            row = decode(read(), now)
            if row and row.device_ms != last:
                writer.writerow(row)
                last, count = row.device_ms, count + 1
            if poll_s:
                time.sleep(poll_s)


def load(path):
    with open(path, newline='') as f:
        return [Row(int(r['host_ns']), *(float(r[k]) for k in Row._fields[1:])) for r in csv.DictReader(f)]


def angle_deg(a, b):
    dot = min(1.0, abs(sum(p * q for p, q in zip(a, b))))
    return math.degrees(2 * math.acos(dot))


def _mean_q(rows):
    first = rows[0][2:]
    total = [0.0] * 4
    for row in rows:
        sign = 1 if sum(p * q for p, q in zip(row[2:], first)) >= 0 else -1
        for i, value in enumerate(row[2:]):
            total[i] += sign * value
    norm = math.sqrt(sum(v * v for v in total))
    return tuple(v / norm for v in total)


def _between(rows, start_ns, end_ns):
    return [r for r in rows if start_ns <= r.host_ns <= end_ns]


def analyse(rows):
    """For the fixed run: still at the start, moved, put back in the same spot, still at the end."""
    if len(rows) < _MIN_ROWS or rows[-1].host_ns <= rows[0].host_ns:
        raise ValueError(f'too few samples to judge: {len(rows)}')
    t0, t1 = rows[0].host_ns, rows[-1].host_ns
    span = t1 - t0
    steps = sorted(b.host_ns - a.host_ns for a, b in zip(rows, rows[1:]))
    # Host and glasses share no clock, so the offset between them means nothing, only how much it moves.
    offsets = sorted(r.host_ns / 1e6 - r.device_ms for r in rows)
    window = min(int(_END_WINDOW_S * 1e9), span // 3)
    third = span // 3
    edge = third // 10  # drift is read from the two ends of the opening still part
    head = _mean_q(_between(rows, t0, t0 + edge))
    tail = _mean_q(_between(rows, t0 + third - edge, t0 + third))
    drift = angle_deg(head, tail) / ((third - edge) / 60e9)
    back = angle_deg(_mean_q(_between(rows, t0, t0 + window)), _mean_q(_between(rows, t1 - window, t1)))
    return {'samples': len(rows), 'seconds': span / 1e9, 'rate_hz': (len(rows) - 1) / (span / 1e9),
            'p50_gap_ms': steps[len(steps) // 2] / 1e6, 'p99_gap_ms': steps[len(steps) * 99 // 100] / 1e6,
            'max_gap_ms': steps[-1] / 1e6, 'gaps_over_100ms': sum(s > _GAP_NS for s in steps),
            'arrival_spread_ms': offsets[len(offsets) * 99 // 100] - offsets[0],
            'still_drift_deg_per_min': drift, 'return_error_deg': back}


def compare(old, new):
    """Whether `new` is at least as good as `old`. The margins are in live-test-plan.md, step F6."""
    checks = (('rate', 'rate_hz', new['rate_hz'] >= 0.9 * old['rate_hz']),
              ('gaps over 100 ms', 'gaps_over_100ms', new['gaps_over_100ms'] == 0),
              ('still drift, deg/min', 'still_drift_deg_per_min',
               new['still_drift_deg_per_min'] <= old['still_drift_deg_per_min'] + 0.5),
              ('return error, deg', 'return_error_deg', new['return_error_deg'] <= old['return_error_deg'] + 1.0))
    lines = [f"{'PASS' if ok else 'FAIL'}  {label}: old {old[key]:.2f}, new {new[key]:.2f}" for label, key, ok in checks]
    return all(ok for _, _, ok in checks), lines


def main(argv=None):
    parser = argparse.ArgumentParser(prog='python -m tools.luma_probe.feed')
    commands = parser.add_subparsers(dest='command', required=True)
    p = commands.add_parser('record')
    p.add_argument('--seconds', type=float, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--feed', default=DEFAULT_FEED)
    commands.add_parser('report').add_argument('file', type=Path)
    p = commands.add_parser('compare')
    p.add_argument('old', type=Path)
    p.add_argument('new', type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == 'record':
            print(f'{record(args.feed, args.out, args.seconds)} samples in {args.out}')
        elif args.command == 'report':
            for key, value in analyse(load(args.file)).items():
                print(f'{key}: {value:.3f}' if isinstance(value, float) else f'{key}: {value}')
        else:
            passed, lines = compare(analyse(load(args.old)), analyse(load(args.new)))
            print('\n'.join(lines))
            return 0 if passed else 1
    except (OSError, ValueError) as exc:
        print(f'blocked: {exc}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
