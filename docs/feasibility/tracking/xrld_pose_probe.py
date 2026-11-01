"""Read-only probe of XRLinuxDriver's SysV IPC pose feed (/tmp/shader_runtime_*).

Attaches segments read-only, copies under the driver's process-shared mutex, never writes.
Prints update rate, record-timestamp age and yaw/pitch/roll of the current quaternion.
"""
import ctypes
import json
import math
import os
import sys
import time

libc = ctypes.CDLL('libc.so.6', use_errno=True)
libc.ftok.argtypes = [ctypes.c_char_p, ctypes.c_int]; libc.ftok.restype = ctypes.c_int
libc.shmget.argtypes = [ctypes.c_int, ctypes.c_size_t, ctypes.c_int]; libc.shmget.restype = ctypes.c_int
libc.shmat.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_int]; libc.shmat.restype = ctypes.c_void_p
libc.pthread_mutex_lock.argtypes = [ctypes.c_void_p]; libc.pthread_mutex_unlock.argtypes = [ctypes.c_void_p]
SHM_RDONLY = 0o10000
PREFIX = '/tmp/shader_runtime_'


def attach(name, readonly=True):
    path = PREFIX + name
    if not os.path.exists(path): raise FileNotFoundError(path)
    key = libc.ftok(path.encode(), 0)
    if key == -1: raise OSError(ctypes.get_errno(), 'ftok ' + path)
    shmid = libc.shmget(key, 0, 0)
    if shmid == -1: raise OSError(ctypes.get_errno(), 'shmget ' + path)
    addr = libc.shmat(shmid, None, SHM_RDONLY if readonly else 0)
    if addr in (None, ctypes.c_void_p(-1).value): raise OSError(ctypes.get_errno(), 'shmat ' + path)
    return addr


def euler(q):
    x, y, z, w = q
    yaw = math.degrees(math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z)))
    s = max(-1.0, min(1.0, 2 * (w * y - z * x)))
    pitch = math.degrees(math.asin(s))
    roll = math.degrees(math.atan2(2 * (w * x + y * z), 1 - 2 * (x * x + y * y)))
    return yaw, pitch, roll


def main(seconds):
    pose = attach('pose_orientation')
    # The mutex must be locked for writing; a read-only mapping cannot be locked, so map it RW.
    mutex = attach('pose_orientation_mutex', readonly=False)
    disabled = attach('disabled')
    floats = (ctypes.c_float * 16)
    start, last, changes, samples, next_sample = time.monotonic(), None, 0, [], 0.0
    lo, hi = [1e9] * 3, [-1e9] * 3
    while time.monotonic() - start < seconds:
        libc.pthread_mutex_lock(mutex)
        data = list(floats.from_address(pose))
        libc.pthread_mutex_unlock(mutex)
        if data != last:
            changes += 1; last = data
            ypr = euler(data[0:4])
            lo = [min(a, b) for a, b in zip(lo, ypr)]; hi = [max(a, b) for a, b in zip(hi, ypr)]
            t = time.monotonic() - start
            if t >= next_sample:
                next_sample += 0.1
                samples.append({'t': round(t, 2), 'ypr_deg': [round(v, 1) for v in ypr], 'ts_ms': [round(v) for v in data[12:15]]})
        time.sleep(0.0005)
    print(json.dumps({'seconds': seconds, 'distinct_updates': changes, 'update_hz': round(changes / seconds, 1),
                      'disabled_flag': bool(ctypes.c_bool.from_address(disabled).value),
                      'ypr_min_deg': [round(v, 1) for v in lo], 'ypr_max_deg': [round(v, 1) for v in hi], 'samples': samples}, indent=1))


if __name__ == '__main__':
    main(float(sys.argv[1]) if len(sys.argv) > 1 else 10.0)
