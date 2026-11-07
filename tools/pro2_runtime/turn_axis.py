"""How a real head turns, from the Luma's raw IMU: the check on what a Pro 2 turn should look like. Offline; aggregates only.

Reads the sanitised 20 minute USB capture of a worn Luma Ultra (accelerometer and gyro at 1 kHz, so real gravity and real rotation, with
no vendor fusion in between). It answers two questions the Pro 2's captures, with two usable turns, cannot:

  - does the head turn about gravity, or about an axis fixed to the head? `slope` is the fit of turn axis tilt against gravity tilt
    across turns: 1 is gravity, 0 is fixed to the head.
  - how much does the Luma's picture lean in those turns, through the renderer's real view (pro2_smooth_replay), by each frame?

Needs numpy. Usage: python3 -m tools.pro2_runtime.turn_axis --capture FILE.lcap --replay BUILD/pro2_smooth_replay
"""
import argparse
import json
import math
from pathlib import Path
import struct
import sys

import numpy as np

from tools.luma_capture.capfile import Reader
from tools.pro2_runtime import smooth_acceptance as A

MOUNT = -8.5  # what the Luma's publisher applies: luma::PublisherOptions, not a Pro 2 value


def imu(path):
    """100 Hz mean samples: seconds, gyro deg/s, specific force in g, both forward, left, up (docs: forward = -y, left = +x, up = +z)."""
    rows = []
    for record in Reader(path):
        if record.ep != 0x81 or record.event != 'C' or len(record.data) < 28: continue
        for offset in range(0, len(record.data) - 27, 28):
            packet = record.data[offset:offset + 28]
            if packet[:4] == b'\xaa\x8f\x00\x88': rows.append((struct.unpack_from('<I', packet, 8)[0], *struct.unpack_from('<6h', packet, 12)))
    raw = np.array(rows, dtype=np.float64)
    n = len(raw) // 10
    t = raw[:n * 10, 0].reshape(n, 10).mean(1)
    gyro = raw[:n * 10, 1:4].reshape(n, 10, 3).mean(1) / 16.4
    force = raw[:n * 10, 4:7].reshape(n, 10, 3).mean(1) / 8192
    flu = lambda v: np.c_[-v[:, 1], v[:, 0], v[:, 2]]
    w, f = flu(gyro), flu(force)
    return (t - t[0]) * 1e-4, w - np.median(w[np.linalg.norm(w, axis=1) < 4], 0), f


def turns(t, w, f):
    """Each real turn's axis, and gravity from the quiet time around it (a turn's own acceleration would bias it)."""
    speed = np.linalg.norm(w, axis=1)
    moving = (speed > 15) & (np.abs(w[:, 2]) > .8 * speed)
    out, i, step = [], 0, np.median(np.diff(t))
    while i < len(t):
        if not moving[i]: i += 1; continue
        j = i
        while j + 1 < len(t) and moving[j + 1]: j += 1
        if t[j] - t[i] >= .12:
            around = np.r_[np.searchsorted(t, t[i] - 1):i, j + 1:np.searchsorted(t, t[j] + 1)]
            quiet = around[speed[around] < 8]
            size = np.sum(w[i:j + 1, 2]) * step
            if len(quiet) >= int(.15 / step) and abs(size) >= 10:
                axis = w[i:j + 1].sum(0); axis = axis * np.sign(axis[2])
                g = f[quiet].mean(0)
                out.append((math.degrees(math.atan2(axis[0], axis[2])), math.degrees(math.atan2(g[0], g[2]))))
        i = j + 1
    return np.array(out)


def fuse(t, w, f):
    """Gyro integration held to gravity by a weak correction: quaternions x, y, z, w, body to reference."""
    q, out = np.array([0, 0, 0, 1.]), np.zeros((len(t), 4))
    for i in range(len(t)):
        dt = min(max(t[i] - t[i - 1], 0), .1) if i else .01
        step = np.radians(w[i]) * dt
        size = np.linalg.norm(f[i])
        if abs(size - 1) < .1:
            u = q[:3]
            up = np.array([0, 0, 1.]) + 2 * np.cross(-u, np.cross(-u, [0, 0, 1.]) + q[3] * np.array([0, 0, 1.]))
            step = step + np.cross(up, f[i] / size) * -.02
        q = np.array(A.qmul(tuple(q), (step[0] / 2, step[1] / 2, step[2] / 2, 1.))); q /= np.linalg.norm(q)
        out[i] = q
    return out


def lean(frames):
    """Per 10 degrees of turn, for each turn: end to end, and in its moving middle."""
    y, r = np.array([f['yaw'] for f in frames]), np.array([f['roll'] for f in frames])
    v, out, i = np.r_[np.zeros(5), y[5:] - y[:-5]] * 24, [], 0
    while i < len(y):
        if abs(v[i]) < 15: i += 1; continue
        way, j = np.sign(v[i]), i
        while j + 1 < len(y) and v[j + 1] * way >= 8: j += 1
        a, b = max(0, i - 12), min(len(y) - 1, j + 12)
        if abs(y[b] - y[a]) >= 10 and np.isfinite(r[a]) and np.isfinite(r[b]) and j > i + 2:
            out.append((10 * (r[b] - r[a]) / (y[b] - y[a]), 10 * np.polyfit(y[i:j + 1], r[i:j + 1], 1)[0]))
        i = j + 1
    return out


def through_view(binary, t, q, mount, level):
    ms, rows = t * 1000, A.mounted([(a, a, tuple(b)) for a, b in zip(t * 1000, q)], mount)
    found = []
    for start in np.arange(0, ms[-1] - 30000, 30000):  # the canvas recentred every 30 s, so a turn stays near it
        first, last = np.searchsorted(ms, [start, start + 30000])
        part = [(h - ms[first], d - ms[first], s) for h, d, s in rows[first:last:2]]
        if len(part) > 100: found += lean(A.replay(binary, part, mode='hold', level=level, stabilization=0, r2p=0.0)[1])
    x = np.array(found)
    show = lambda c: dict(median=round(float(np.median(x[:, c])), 2), iqr=[round(float(v), 2) for v in np.percentile(x[:, c], [25, 75])])
    return dict(turns=len(x), end_to_end=show(0), fast=show(1))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--capture', required=True, type=Path); parser.add_argument('--replay', required=True, type=Path)
    args = parser.parse_args()
    t, w, f = imu(args.capture)
    seen = turns(t, w, f); near = seen[np.abs(seen[:, 1]) < 30]
    slope, offset = np.polyfit(near[:, 1], near[:, 0], 1)
    rng = np.random.default_rng(1)
    boot = [np.polyfit(*near[rng.integers(0, len(near), len(near))][:, [1, 0]].T, 1)[0] for _ in range(2000)]
    q = fuse(t, w, f)
    print(json.dumps(dict(schema=1, capture_seconds=round(float(t[-1])), turns=len(near), axis_tilt_median=round(float(np.median(near[:, 0])), 2),
                          gravity_tilt_median=round(float(np.median(near[:, 1])), 2), gravity_tilt_range=[round(float(near[:, 1].min()), 1), round(float(near[:, 1].max()), 1)],
                          slope=round(float(slope), 2), slope_90pct=[round(float(v), 2) for v in np.percentile(boot, [5, 95])], offset=round(float(offset), 2), r=round(float(np.corrcoef(near[:, 1], near[:, 0])[0, 1]), 2),
                          lean_per_10deg=dict(real_path_mount=through_view(args.replay, t, q, MOUNT, 0), no_mount=through_view(args.replay, t, q, 0.0, 0), gravity_level=through_view(args.replay, t, q, 0.0, 1))), indent=2))


if __name__ == '__main__': sys.exit(main())
