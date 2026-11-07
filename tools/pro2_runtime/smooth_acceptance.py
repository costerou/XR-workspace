"""Offline motion acceptance for the Pro 2 feed: replay a pose capture through the renderer's pose path, measure the view.

Runs the `pro2_smooth_replay` binary (spike/tests/pro2_smooth_replay.cpp) on a capture and reads back what each 120 Hz frame
would show. Standard library only. No SDK, USB, compositor or GPU. Inputs are the private captures under
~/.local/state/viture-workspace/pro2-hil/ or a synthetic trace; only the aggregates below may be committed.

Reference path: the capture itself, smoothed by a centred Gaussian (non-causal, so it knows the future no renderer can), at
the frame's presentation time on the device clock. Times are the feed's own (host - device, at its floor), so "lag" here is
what software can see: from a sample's timestamp to its presentation. The panel's own delay is not measurable from a capture.

    moving frames     reference speed >= MOVING_DPS
    stuck_pct         moving frames that stepped under a tenth of the reference step: a held frame
    step_err_*        per-frame step minus the reference's, degrees, vector difference
    jerk_p95          second difference of the shown pose, degrees per frame per frame
    slow_jerk_p95     the same, on moving frames under SLOW_DPS: stepping shows most in a slow turn
    tilt              on captures with cues: how far the picture leans per 10 degrees of turn, through the real view, for each candidate frame
    lag_ms            how far behind the reference the view is, along the motion
    err_rms           shown pose against the reference, degrees
    overshoot_*       at each stop, how far the view went past the furthest the head itself went nearby, degrees
    rest_step_p95     per-frame step while the head is still (reference under REST_DPS), degrees: must not add jitter
"""
import argparse
import bisect
import json
import math
import os
from pathlib import Path
import subprocess
import sys

MOVING_DPS, SLOW_DPS, REST_DPS = 5.0, 15.0, 1.0
SIGMA_MS = 12.0
STOP_FROM_DPS, STOP_TO_DPS = 15.0, 3.0
DEG = 180 / math.pi
PIXELS_PER_DEG = 1920 / 39.25  # a 44.5 degree diagonal on 1920x1080 (docs/pro2/projection-policy.md), for reading the numbers
PHASES_MS = (0.0, 2.08, 4.17, 6.25)  # where the display grid falls on the capture
# The exact values the Pro 2 route ships: tau is scope.SMOOTH_MS, cap and constant are bridge.cpp kLookAhead, the brake is motion_smooth.hpp,
# the mount is display_policy.PRO2_MOUNT_PITCH.
SHIPPED = dict(tau=35.0, cap=40.0, constant=0.0, brake=3.0, brake_floor=4.0, mount=-11.0)
TILT_LIMIT = 0.3  # degrees of lean per 10 of turn, in the moving part of a turn, at the shipped mount pitch
READ_TO_PRESENT_MS = 16.6
ACCEPT = dict(stuck_pct=2.0, step_err_p95=0.12, jerk_p95=0.08, slow_jerk_p95=0.07, lag_ms=9.0, overshoot_max=0.30, rest_step_p95=0.016)


def qmul(a, b):
    ax, ay, az, aw = a; bx, by, bz, bw = b
    return (aw * bx + ax * bw + ay * bz - az * by, aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw, aw * bw - ax * bx - ay * by - az * bz)


def conj(q): return (-q[0], -q[1], -q[2], q[3])


def log(q):
    x, y, z, w = q
    if w < 0: x, y, z, w = -x, -y, -z, -w
    s = math.sqrt(x * x + y * y + z * z)
    k = 2.0 if s < 1e-12 else 2 * math.atan2(s, w) / s
    return (x * k, y * k, z * k)


def delta(a, b): return log(qmul(conj(a), b))
def norm(v): return math.sqrt(sum(c * c for c in v))
def sub(a, b): return tuple(x - y for x, y in zip(a, b))
def dot(a, b): return sum(x * y for x, y in zip(a, b))


def unit(q):
    n = math.sqrt(sum(c * c for c in q))
    return tuple(c / n for c in q)


def percentile(values, q):
    if not values: return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, math.ceil(q * len(ordered)) - 1))]


def load_capture(path):
    """Host ms, device ms from the first sample, and the quaternion the bridge publishes (bridge.cpp, H1), x y z w."""
    rows = []
    with open(path) as stream:
        for line in stream:
            row = json.loads(line)
            if row.get('kind') != 'pose': continue
            v = row['sdk_values']
            rows.append((row['host_monotonic_ns'] / 1e6, row['timestamp_raw'] / 1e6, unit((-v[6], -v[4], v[5], v[3]))))
    if len(rows) < 4: raise ValueError('capture too short')
    h0, d0 = rows[0][0], rows[0][1]
    return [(h - h0, d - d0, q) for h, d, q in rows]


def synthetic(seconds=20.0, seed=7):
    """A head on the measured cadence (15, 15, 20 ms) with turns and stops, for tests that must run without private data."""
    import random
    r = random.Random(seed)
    rows, t, i, angle, speed = [], 0.0, 0, 0.0, 0.0
    gaps = (15.0, 15.0, 20.0)
    while t < seconds * 1000:
        phase = t % 3000
        wanted = 30 * math.sin(math.pi * phase / 1200) if phase < 1200 else 0.0
        if 1500 <= phase < 2500: wanted = -12 * math.sin(math.pi * (phase - 1500) / 1000)
        speed = wanted
        gap = gaps[i % 3]
        angle += speed * gap / 1000
        half = math.radians(angle + r.gauss(0, .012)) / 2
        rows.append((t + r.uniform(.2, .8), t, (0.0, 0.0, math.sin(half), math.cos(half))))
        t += gap; i += 1
    return rows


def reference(rows):
    """The smoothed path: device times and quaternions (relative samples, sign-aligned)."""
    times = [r[1] for r in rows]
    aligned = []
    for r in rows:
        q = r[2]
        if aligned and dot(aligned[-1], q) < 0: q = tuple(-c for c in q)
        aligned.append(q)
    out, low = [], 0
    for i, t in enumerate(times):
        while times[low] < t - 3 * SIGMA_MS: low += 1
        total = [0.0] * 4; j = low
        while j < len(times) and times[j] <= t + 3 * SIGMA_MS:
            # Each sample stands for the time around it: unweighted, the path would carry the 15, 15, 20 cadence itself.
            w = math.exp(-.5 * ((times[j] - t) / SIGMA_MS) ** 2) * (times[min(j + 1, len(times) - 1)] - times[max(j - 1, 0)])
            for k in range(4): total[k] += w * aligned[j][k]
            j += 1
        out.append(unit(total))
    return times, out


class Path_:
    def __init__(self, times, quats): self.times, self.quats, self.index = times, quats, 0
    def at(self, t):
        ts, qs = self.times, self.quats
        while self.index + 2 < len(ts) and ts[self.index + 1] <= t: self.index += 1
        i = self.index
        u = max(0.0, min(1.0, (t - ts[i]) / (ts[i + 1] - ts[i])))
        return unit(tuple(a + (b - a) * u for a, b in zip(qs[i], qs[i + 1])))


def replay(binary, rows, *, mode, phase=0.0, tau=35.0, cap=40.0, constant=0.0, brake=3.0, brake_floor=4.0, stabilization=25, r2p=READ_TO_PRESENT_MS, period=8.3333, read_jitter=0.0, level=0):
    text = ''.join(f'{h:.6f} {d:.6f} {q[0]:.9f} {q[1]:.9f} {q[2]:.9f} {q[3]:.9f}\n' for h, d, q in rows)
    args = [str(binary), '--mode', mode, '--phase', str(phase), '--tau', str(tau), '--cap', str(cap), '--constant', str(constant),
            '--brake', str(brake), '--brake-floor', str(brake_floor), '--stabilization', str(stabilization), '--r2p', str(r2p), '--period', str(period), '--read-jitter', str(read_jitter), '--level', str(level)]
    done = subprocess.run(args, input=text, capture_output=True, text=True, check=True, timeout=60)
    lines = done.stdout.splitlines()
    head = lines[0].split()
    if head[0] != 'center': raise ValueError('replay output')
    frames = []
    for line in lines[1:]:
        f = list(map(float, line.split()))
        frames.append(dict(present=f[0], device=f[1], q=tuple(f[2:6]), horizon=f[6], predicted=f[7], yaw=f[8], roll=f[9]))
    return (tuple(map(float, head[1:5])), tuple(map(float, head[5:9]))), frames, done.stderr


def measure(rows, center, frames, period=8.3333):
    """One replay's per-frame lists, ready to pool over phases."""
    offset = min(h - d for h, d, _ in rows)
    center, turn = center if len(center) == 2 else (center, (0.0, 0.0, 0.0, 1.0))
    relative = [(h, d, unit(qmul(qmul(conj(center), q), turn))) for h, d, q in rows]
    times, smooth = reference(relative)
    path = Path_(times, smooth)
    shown = [f['q'] for f in frames]
    truth = [path.at(f['present'] - offset) for f in frames]
    n = len(frames)
    truth_step = [delta(truth[i], truth[i + 1]) for i in range(n - 1)]
    shown_step = [delta(shown[i], shown[i + 1]) for i in range(n - 1)]
    per_s = 1000 / period
    speed = [norm(s) * DEG * per_s for s in truth_step]
    out = dict(moving=0, stuck=0, step_err=[], jerk=[], slow_jerk=[], along=[], speed_sum=0.0, err=[], rest=[], overshoot=[], predicted=[], horizon=[])
    for i in range(n - 2):
        if speed[i] >= MOVING_DPS:
            out['moving'] += 1
            if speed[i] < SLOW_DPS: out['slow_jerk'].append(norm(sub(shown_step[i + 1], shown_step[i])) * DEG)
            if norm(shown_step[i]) < .1 * norm(truth_step[i]): out['stuck'] += 1
            out['step_err'].append(norm(sub(shown_step[i], truth_step[i])) * DEG)
            out['jerk'].append(norm(sub(shown_step[i + 1], shown_step[i])) * DEG)
            direction = truth_step[i]
            size = norm(direction)
            behind = delta(shown[i], truth[i])  # positive along the motion: the view is behind
            out['along'].append(dot(behind, direction) / size * DEG)
            out['speed_sum'] += speed[i]
            out['err'].append(norm(behind) * DEG)
        elif speed[i] < REST_DPS:
            out['rest'].append(norm(shown_step[i]) * DEG)
        out['predicted'].append(frames[i]['predicted']); out['horizon'].append(frames[i]['horizon'])
    # Stops: fast within the last 80 ms, then slow. How far past the stop the view went, along the way it was going.
    span = int(round(80 / period))
    settle = int(round(200 / period))
    i = span
    while i < n - 2 - settle:
        if speed[i] < STOP_TO_DPS and max(speed[i - span:i]) >= STOP_FROM_DPS:
            way = delta(truth[i - span], truth[i])
            size = norm(way)
            if size > 1e-6:
                axis = tuple(c / size for c in way)
                window = range(i - span // 2, i + settle)
                furthest = max(dot(delta(truth[i], truth[k]), axis) for k in window)  # a pause may carry on the same way
                out['overshoot'].append(max(0.0, (max(dot(delta(truth[i], shown[k]), axis) for k in window) - furthest) * DEG))
            i += settle
        else: i += 1
    return out


def pool(parts):
    total = dict(moving=0, stuck=0, step_err=[], jerk=[], slow_jerk=[], along=[], speed_sum=0.0, err=[], rest=[], overshoot=[], predicted=[], horizon=[])
    for p in parts:
        for key, value in p.items():
            if isinstance(value, list): total[key].extend(value)
            else: total[key] += value
    return total


def summary(p):
    moving = max(p['moving'], 1)
    lag = (sum(p['along']) / moving) / (p['speed_sum'] / moving) * 1000 if p['speed_sum'] else 0.0
    rms = lambda v: math.sqrt(sum(x * x for x in v) / len(v)) if v else 0.0
    return dict(moving_frames=p['moving'], stuck_pct=round(100 * p['stuck'] / moving, 2),
                step_err_rms=round(rms(p['step_err']), 4), step_err_p95=round(percentile(p['step_err'], .95), 4),
                step_err_max=round(max(p['step_err'] or [0]), 3), jerk_p95=round(percentile(p['jerk'], .95), 4),
                slow_jerk_p95=round(percentile(p['slow_jerk'], .95), 4), slow_frames=len(p['slow_jerk']),
                lag_ms=round(lag, 1), err_rms=round(rms(p['err']), 3), stops=len(p['overshoot']),
                overshoot_p95=round(percentile(p['overshoot'], .95), 3), overshoot_max=round(max(p['overshoot'] or [0]), 3),
                rest_step_p95=round(percentile(p['rest'], .95), 4), rest_frames=len(p['rest']),
                horizon_max_ms=round(max(p['horizon'] or [0]), 2), predicted_max_deg=round(max(p['predicted'] or [0]), 3))


def tilt(frames, begin_ms=None, end_ms=None):
    """How far the picture leans for each 10 degrees of turn, least squares: positive when a turn to the right leans it left."""
    window = [f for f in frames if (begin_ms is None or f['present'] >= begin_ms) and (end_ms is None or f['present'] < end_ms) and math.isfinite(f['roll'])]
    if len(window) < 8: return dict(frames=len(window), roll_per_10deg_yaw=0.0)
    mean_y, mean_r = sum(f['yaw'] for f in window) / len(window), sum(f['roll'] for f in window) / len(window)
    spread = sum((f['yaw'] - mean_y) ** 2 for f in window) or 1.0
    furthest = {side: max((f for f in window if f['yaw'] * side > 0), key=lambda f: abs(f['yaw']), default=None) for side in (1, -1)}
    return dict(frames=len(window), roll_per_10deg_yaw=round(10 * sum((f['yaw'] - mean_y) * (f['roll'] - mean_r) for f in window) / spread, 3),
                right=furthest[1] and dict(yaw=round(furthest[1]['yaw'], 1), roll=round(furthest[1]['roll'], 2)),
                left=furthest[-1] and dict(yaw=round(furthest[-1]['yaw'], 1), roll=round(furthest[-1]['roll'], 2)))


def mounted(rows, degrees):
    """The feed as the bridge publishes it with this mount pitch: q * pitch about the left axis (luma::Publisher mount_pitch_deg)."""
    half = math.radians(degrees) / 2
    turn = (0.0, math.sin(half), 0.0, math.cos(half))
    return [(host, device, unit(qmul(q, turn))) for host, device, q in rows]


def turn_lean(frames, begin_ms, end_ms):
    """Lean per 10 degrees of turn, on the capture's turns, three ways. `fast`: the moving middle of the left to right sweep. `end_to_end`:
    between its two ends. `whole`: every frame. They need not agree: the head's own path differs in and out of motion."""
    window = [f for f in frames if begin_ms <= f['present'] < end_ms and math.isfinite(f['roll'])]
    low, high = min(range(len(window)), key=lambda i: window[i]['yaw']), max(range(len(window)), key=lambda i: window[i]['yaw'])
    first, last = sorted((low, high))
    moving = [window[i] for i in range(first + 1, last) if abs(window[i + 1]['yaw'] - window[i - 1]['yaw']) / (window[i + 1]['present'] - window[i - 1]['present']) > .015]
    slope = lambda part: 10 * tilt_slope(part)
    return dict(fast=round(slope(moving), 3), fast_frames=len(moving), whole=round(slope(window), 3),
                end_to_end=round(10 * (window[high]['roll'] - window[low]['roll']) / (window[high]['yaw'] - window[low]['yaw']), 3),
                right=dict(yaw=round(window[high]['yaw'], 1), roll=round(window[high]['roll'], 2)), left=dict(yaw=round(window[low]['yaw'], 1), roll=round(window[low]['roll'], 2)))


def tilt_slope(frames):
    mean_y, mean_r = sum(f['yaw'] for f in frames) / len(frames), sum(f['roll'] for f in frames) / len(frames)
    return sum((f['yaw'] - mean_y) * (f['roll'] - mean_r) for f in frames) / (sum((f['yaw'] - mean_y) ** 2 for f in frames) or 1.0)


def evaluate(binary, rows, *, mode, **options):
    parts = []
    for phase in PHASES_MS:
        center, frames, _ = replay(binary, rows, mode=mode, phase=phase, **options)
        parts.append(measure(rows, center, frames))
    return summary(pool(parts))


def cadence(rows):
    gaps = [b[1] - a[1] for a, b in zip(rows, rows[1:])]
    return dict(samples=len(rows), mean_hz=round(1000 * len(gaps) / sum(gaps), 2),
                gaps_15ms_pct=round(100 * sum(1 for g in gaps if g < 17) / len(gaps), 1),
                gaps_20ms_pct=round(100 * sum(1 for g in gaps if g >= 17) / len(gaps), 1))


def turn_window(captures, name):
    """The capture's left and right turns, by its cues.json: (first ms, last ms) on the replay's clock, or None."""
    path = next((p for p in captures if p.stem == name), None)
    cues = path.parent / 'cues.json' if path else None
    if cues is None or not cues.exists(): return None
    marks = {c['label']: c['host_monotonic_ns'] / 1e6 for c in json.loads(cues.read_text())}
    if not {'left', 'up'} <= set(marks): return None
    with open(path) as stream: start = next(json.loads(l)['host_monotonic_ns'] for l in stream if '"pose"' in l[:20]) / 1e6
    return marks['left'] - start, marks['up'] - start


def failures(result):
    """The acceptance limits this result misses (empty: passes)."""
    return [f'{k} {result[k]} over {limit}' for k, limit in ACCEPT.items() if result[k] > limit]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--replay', required=True, type=Path)
    parser.add_argument('--captures', nargs='*', type=Path, default=[])
    parser.add_argument('--synthetic', action='store_true')
    parser.add_argument('--tau', type=float, default=SHIPPED['tau'])
    parser.add_argument('--brake', type=float, default=SHIPPED['brake'])
    args = parser.parse_args(argv)
    sets = {p.stem: load_capture(p) for p in args.captures}  # as captured: `smoothed` below adds the shipped mount, as the bridge does
    if args.synthetic: sets['synthetic'] = synthetic()
    report, failed = {}, []
    for name, rows in sets.items():
        before = evaluate(args.replay, rows, mode='hold', level=0)  # the first live test
        after = evaluate(args.replay, mounted(rows, SHIPPED['mount']), mode='smooth', tau=args.tau, cap=SHIPPED['cap'], constant=SHIPPED['constant'], brake=args.brake, brake_floor=SHIPPED['brake_floor'])
        legacy = evaluate(args.replay, rows, mode='legacy', cap=SHIPPED['cap'], constant=SHIPPED['constant'], level=0)
        jittered = evaluate(args.replay, mounted(rows, SHIPPED['mount']), mode='smooth', tau=args.tau, cap=SHIPPED['cap'], constant=SHIPPED['constant'], brake=args.brake,
                            brake_floor=SHIPPED['brake_floor'], read_jitter=2.0)
        report[name] = dict(cadence=cadence(rows), hold=before, legacy_prediction=legacy, smoothed=after, smoothed_2ms_read_jitter=jittered)
        turns = turn_window(args.captures, name)
        if turns:  # what Cody saw: the turns of the directional capture, before and after levelling
            lean = {}
            for key, mount, level in (('no_mount', 0.0, 0), ('gravity_level_at_19.5', 0.0, 1), ('upstream_minus_8.5', -8.5, 0), ('shipped_mount', SHIPPED['mount'], 0)):
                lean[key] = turn_lean(replay(args.replay, mounted(rows, mount), mode='hold', level=level, stabilization=0)[1], *turns)
            report[name]['tilt'] = lean
            if abs(lean['shipped_mount']['fast']) > TILT_LIMIT: failed.append(f"{name}: tilt {lean['shipped_mount']['fast']} over {TILT_LIMIT}")
        if name == 'synthetic':  # for tests without private data: a head this clean only proves the direction, not the limits
            if after['jerk_p95'] * 2 > before['jerk_p95'] or after['stuck_pct'] * 5 > before['stuck_pct']: failed.append('synthetic: not smoother than hold')
        elif name != 'stationary': failed += [f'{name}: {f}' for f in failures(after)]
        elif after['rest_step_p95'] > before['rest_step_p95'] + ACCEPT['rest_step_p95']: failed.append('stationary: rest jitter added')
    print(json.dumps(dict(schema=1, parameters=dict(SHIPPED, tau=args.tau, brake=args.brake, stabilization=25), limits=ACCEPT, results=report, failed=failed), indent=2))
    return 1 if failed else 0


if __name__ == '__main__': sys.exit(main())
