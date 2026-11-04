"""Records the published head pose and compares two recordings. Works on either driver's feed.

    python -m tools.luma_probe.feed record --seconds 60 --out run.csv
    python -m tools.luma_probe.feed report run.csv
    python -m tools.luma_probe.feed compare old.csv new.csv
    python -m tools.luma_probe.feed attitude run.csv      once a second: yaw, pitch, roll, in degrees
    python -m tools.luma_probe.feed moves run.csv         once a second: how far the glasses turned about their own axes

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
# Cody's bar is absolute, so the old driver being as bad is no excuse. Proposed limits, see step F6.
_MAX_HITCH_MS = 20.0
_MAX_DRIFT_DEG_PER_MIN = 0.5
_DRIFT_NOISE = 0.1
_PLACEMENT_NOISE_DEG = 0.5
MOUNT_PITCH_DEG = -8.5  # the publisher's default: spike/driver/include/luma/publish.hpp
_RATE_NOISE = 0.98  # the recorder polls from Python and can miss the odd sample, on either driver


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
    """Whether `new` matches `old` within measuring noise and meets the bar. Limits: live-test-plan.md, step F6."""
    drift = new['still_drift_deg_per_min']
    checks = (('rate', 'rate_hz', new['rate_hz'] >= _RATE_NOISE * old['rate_hz']),
              ('gaps over 100 ms', 'gaps_over_100ms', new['gaps_over_100ms'] == 0),
              ('longest hitch, ms', 'max_gap_ms', new['max_gap_ms'] <= _MAX_HITCH_MS),
              ('still drift, deg/min', 'still_drift_deg_per_min',
               drift <= old['still_drift_deg_per_min'] + _DRIFT_NOISE and drift <= _MAX_DRIFT_DEG_PER_MIN),
              ('return error, deg', 'return_error_deg',
               new['return_error_deg'] <= old['return_error_deg'] + _PLACEMENT_NOISE_DEG))
    lines = [f"{'PASS' if ok else 'FAIL'}  {label}: old {old[key]:.2f}, new {new[key]:.2f}" for label, key, ok in checks]
    return all(ok for _, _, ok in checks), lines


def attitude(row):
    """Yaw left positive, pitch nose-down positive, roll right-ear-down positive, in degrees, for x forward, y left, z up."""
    x, y, z, w = row.x, row.y, row.z, row.w
    return (math.degrees(math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))),
            math.degrees(math.asin(max(-1.0, min(1.0, 2 * (w * y - z * x))))),
            math.degrees(math.atan2(2 * (w * x + y * z), 1 - 2 * (x * x + y * y))))


def attitude_lines(rows):
    """One line per second of a recording, so a slow head movement can be read off by eye."""
    lines, next_ns = [], None
    for row in rows:
        if next_ns is None or row.host_ns >= next_ns:
            next_ns = row.host_ns + 1_000_000_000
            yaw, pitch, roll = attitude(row)
            lines.append(f'yaw {yaw:7.1f}  pitch {pitch:7.1f}  roll {roll:7.1f}')
    return lines


def _multiply(a, b):
    return (a[0] * b[0] - a[1] * b[1] - a[2] * b[2] - a[3] * b[3], a[0] * b[1] + a[1] * b[0] + a[2] * b[3] - a[3] * b[2],
            a[0] * b[2] - a[1] * b[3] + a[2] * b[0] + a[3] * b[1], a[0] * b[3] + a[1] * b[2] - a[2] * b[1] + a[3] * b[0])


def _without_mount(row):
    """(w, x, y, z) of the glasses, taking off the publisher's mount_pitch_deg (publish.hpp): feed = pose * pitch(MOUNT)."""
    half = math.radians(-MOUNT_PITCH_DEG) / 2
    return _multiply((row.w, row.x, row.y, row.z), (math.cos(half), 0.0, math.sin(half), 0.0))


def turned(a, b):
    """The rotation from row a to row b in the glasses' own frame, degrees: (right ear down, nose down, turn left)."""
    p, q = _without_mount(a), _without_mount(b)
    w, x, y, z = _multiply((p[0], -p[1], -p[2], -p[3]), q)
    if w < 0: w, x, y, z = -w, -x, -y, -z
    sine = math.sqrt(x * x + y * y + z * z)
    if sine < 1e-9: return 0.0, 0.0, 0.0
    scale = math.degrees(2 * math.atan2(sine, w)) / sine
    return x * scale, y * scale, z * scale


def moves_lines(rows):
    """One line per second. Euler angles go wild near straight up or down and read oddly upside down; this does not."""
    lines = ['  second  right-ear-down  nose-down  turn-left']
    start = 0
    for at, row in enumerate(rows):
        if row.host_ns - rows[start].host_ns >= 1_000_000_000 or at == len(rows) - 1:
            ear, nose, left = turned(rows[start], row)
            lines.append(f'{(rows[start].host_ns - rows[0].host_ns) / 1e9:8.0f}  {ear:14.1f}  {nose:9.1f}  {left:9.1f}')
            start = at
    return lines


def main(argv=None):
    parser = argparse.ArgumentParser(prog='python -m tools.luma_probe.feed')
    commands = parser.add_subparsers(dest='command', required=True)
    p = commands.add_parser('record')
    p.add_argument('--seconds', type=float, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--feed', default=DEFAULT_FEED)
    commands.add_parser('report').add_argument('file', type=Path)
    commands.add_parser('attitude').add_argument('file', type=Path)
    commands.add_parser('moves').add_argument('file', type=Path)
    p = commands.add_parser('compare')
    p.add_argument('old', type=Path)
    p.add_argument('new', type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == 'record':
            print(f'{record(args.feed, args.out, args.seconds)} samples in {args.out}')
        elif args.command == 'attitude':
            print('\n'.join(attitude_lines(load(args.file))))
        elif args.command == 'moves':
            print('\n'.join(moves_lines(load(args.file))))
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
