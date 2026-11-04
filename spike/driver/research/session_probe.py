#!/usr/bin/env python3
"""Watch a running xrDriver session from outside. Touches neither the driver nor the glasses.

Reads only: /proc/<pid> (thread CPU, mapped libraries), sysfs `urbnum` (USB transfers the kernel
counted) and the driver's pose_position shared memory, attached read-only. It never opens anything
in /dev, never starts or stops anything, and is safe to run during a real session.

    python3 -I spike/driver/research/session_probe.py --seconds 60 --out probe.json

What it answers: is the driver really running position tracking (position moves, a thread is doing
image work, transfers exceed the IMU rate), and what does that cost in CPU?
"""
import argparse
import ctypes
import json
import os
import struct
import sys
import time
from pathlib import Path

SYSFS = Path('/sys/bus/usb/devices')
POSITION_IPC = '/tmp/shader_runtime_pose_position'
GLASSES, CAMERA = ('35ca', '1104'), ('0c45', '636b')
# XRLinuxDriver: LENS_TO_PIVOT_CM (12.7) / lens_distance_ratio (0.05) is one unit of pose_position.
METRES_PER_POSITION_UNIT = 2.54


def parse_task_stat(text):
    """(name, cpu ticks, processor) from a /proc/<pid>/task/<tid>/stat line. The name may hold spaces and brackets."""
    rest = text[text.rindex(')') + 2:].split()
    return text[text.index('(') + 1:text.rindex(')')], int(rest[11]) + int(rest[12]), int(rest[36])


def find_device(ids, root=SYSFS):
    for d in sorted(root.iterdir()):
        try:
            if ((d / 'idVendor').read_text().strip(), (d / 'idProduct').read_text().strip()) == ids: return d
        except OSError:
            continue
    return None


def find_driver(proc=Path('/proc')):
    for d in proc.glob('[0-9]*'):
        try: argv0 = (d / 'cmdline').read_bytes().split(b'\0')[0]
        except OSError: continue
        if argv0 in (b'/usr/bin/xrDriver', b'xrDriver'): return int(d.name)
    return None


def threads(pid):
    out = {}
    for t in Path(f'/proc/{pid}/task').iterdir():
        try: out[t.name] = parse_task_stat((t / 'stat').read_text())
        except OSError: pass
    return out


def thread_cpu(before, after, seconds, ticks_per_s):
    """Per-thread percent of one core over the window, busiest first."""
    rows = []
    for tid, (name, ticks, processor) in after.items():
        spent = ticks - (before[tid][1] if tid in before else 0)
        rows.append({'tid': tid, 'name': name, 'cpu_percent': round(spent / ticks_per_s / seconds * 100, 2), 'processor': processor})
    return sorted(rows, key=lambda r: -r['cpu_percent'])


def counter(device):
    try: return int((device / 'urbnum').read_text())
    except (OSError, TypeError, ValueError): return None


class PositionView:
    """The driver's three-float pose_position, read-only. No lock: a torn read only blurs a range."""
    def __init__(self, path=POSITION_IPC):
        self.address = None
        libc = ctypes.CDLL('libc.so.6', use_errno=True)
        libc.ftok.argtypes = [ctypes.c_char_p, ctypes.c_int]
        libc.shmat.restype = ctypes.c_void_p
        libc.shmat.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
        key = libc.ftok(os.fsencode(path), 0)
        segment = libc.shmget(key, 0, 0) if key != -1 else -1
        if segment < 0: return
        address = libc.shmat(segment, None, 0o10000)  # SHM_RDONLY
        if address not in (None, ctypes.c_void_p(-1).value): self.address = address

    def read(self):
        return struct.unpack('<3f', ctypes.string_at(self.address, 12)) if self.address else None


def summarise(samples, cpu, seconds, libraries):
    glasses = [s['glasses_urbs'] for s in samples if s['glasses_urbs'] is not None]
    camera = [s['camera_urbs'] for s in samples if s['camera_urbs'] is not None]
    positions = [s['position'] for s in samples if s['position'] is not None]
    spread = [round((max(p[i] for p in positions) - min(p[i] for p in positions)) * METRES_PER_POSITION_UNIT, 6) for i in range(3)] if positions else None
    return {'seconds': round(seconds, 2), 'driver_cpu_percent': round(sum(r['cpu_percent'] for r in cpu), 2),
            'threads': cpu[:15], 'glasses_urbs_per_s': round((glasses[-1] - glasses[0]) / seconds) if len(glasses) > 1 else None,
            'camera_urbs': (camera[-1] - camera[0]) if len(camera) > 1 else None, 'position_spread_m': spread,
            'position_moved': bool(spread and max(spread) > 0), 'libraries': libraries}


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--seconds', type=float, default=60)
    parser.add_argument('--out')
    args = parser.parse_args()
    pid = find_driver()
    if pid is None: sys.exit('no xrDriver running: nothing to observe')
    glasses, camera = find_device(GLASSES), find_device(CAMERA)
    libraries = sorted({line.split()[-1] for line in Path(f'/proc/{pid}/maps').read_text().splitlines()
                        if any(n in line for n in ('carina', 'libglasses', 'opencv', 'dof3'))})
    view, ticks_per_s = PositionView(), os.sysconf('SC_CLK_TCK')
    start, first, samples = time.monotonic(), threads(pid), []
    last = first
    while time.monotonic() - start < args.seconds:
        time.sleep(0.5)
        try: last = threads(pid)
        except OSError: break  # the driver ended: report what was seen
        samples.append({'t': round(time.monotonic() - start, 3), 'glasses_urbs': counter(glasses), 'camera_urbs': counter(camera), 'position': view.read()})
    elapsed = time.monotonic() - start
    summary = summarise(samples, thread_cpu(first, last, elapsed, ticks_per_s), elapsed, libraries)
    print(json.dumps(summary, indent=1))
    if args.out: Path(args.out).write_text(json.dumps({'summary': summary, 'samples': samples}))


if __name__ == '__main__':
    main()
