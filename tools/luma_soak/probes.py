"""Read-only looks at the machine. Nothing here opens the USB device or takes the feed's mutex.

sysfs attributes are the kernel's cached copies, reading them sends nothing on the wire.
"""
import ctypes
import os
from pathlib import Path
import time

SHM_RDONLY = 0o10000
DEFAULT_FEED = '/tmp/shader_runtime_pose_orientation'
FEED_DEVICE_MS_INDEX = 12  # spike/src/pose.cpp: 16 floats, v[12] is the newest sample's device_ms
DRIVER_NAMES = ('xrDriver', 'xr-luma-driver')


def _read(path):
    try:
        return Path(path).read_text(errors='replace').strip()
    except OSError:
        return None


def _exe(proc, pid):
    """The binary a process runs: /proc/PID/exe, else argv[0] (tracking.py starts the driver by its full pinned path)."""
    try:
        return os.readlink(f'{proc}/{pid}/exe')
    except OSError:
        pass
    try:
        argv0 = Path(f'{proc}/{pid}/cmdline').read_bytes().split(b'\0')[0].decode(errors='replace')
    except OSError:
        return None
    return argv0 or None


def usb_tree(root='/sys/bus/usb/devices'):
    """{port path: {id, product, devnum}} for every USB device; root hubs and interfaces are left out."""
    tree = {}
    try:
        names = os.listdir(root)
    except OSError:
        return tree
    for name in names:
        if name.startswith('usb') or ':' in name:
            continue
        vendor, product = _read(f'{root}/{name}/idVendor'), _read(f'{root}/{name}/idProduct')
        if vendor and product:
            devnum = _read(f'{root}/{name}/devnum')
            tree[name] = {'id': f'{vendor}:{product}'.lower(), 'product': _read(f'{root}/{name}/product'),
                          'devnum': int(devnum) if devnum and devnum.isdigit() else None}
    return tree


def tree_changes(before, after, watched):
    """What happened to the watched devices between two snapshots. A new devnum is a re-enumeration: the device left and came back."""
    out = []
    old = {p: d for p, d in before.items() if d['id'] in watched}
    new = {p: d for p, d in after.items() if d['id'] in watched}
    for path, device in old.items():
        now = new.get(path)
        if now is None:
            out.append({'kind': 'gone', 'path': path, 'id': device['id'], 'devnum': device['devnum']})
        elif now['id'] != device['id'] or now['devnum'] != device['devnum']:
            out.append({'kind': 'renumbered', 'path': path, 'id': now['id'], 'devnum': now['devnum'], 'was': device['devnum']})
    out.extend({'kind': 'appeared', 'path': p, 'id': d['id'], 'devnum': d['devnum']} for p, d in new.items() if p not in old)
    return out


class CpuSampler:
    """System, busiest core, driver CPU, and how long the driver's threads sat runnable but unscheduled."""
    RESCAN_S = 5.0

    def __init__(self, proc='/proc', names=DRIVER_NAMES, clock=time.monotonic):
        self.proc, self.names, self.clock = proc, tuple(names), clock
        self.pids, self.scanned, self.last_stat, self.last_driver, self.last_at = [], None, None, {}, None
        self.exes = {}  # pid: the binary it runs, so a report says which build of a driver it watched
        self.tick = os.sysconf('SC_CLK_TCK')

    def _scan(self):
        found = []
        try:
            entries = os.listdir(self.proc)
        except OSError:
            entries = []
        for name in entries:
            if name.isdigit() and _read(f'{self.proc}/{name}/comm') in self.names:
                found.append(int(name))
        self.pids = sorted(found)
        self.exes = {pid: self.exes.get(pid) or _exe(self.proc, pid) for pid in self.pids}

    def _system(self):
        cores = {}
        for line in (_read(f'{self.proc}/stat') or '').splitlines():
            parts = line.split()
            if parts and parts[0].startswith('cpu'):
                values = [int(v) for v in parts[1:9]]
                cores[parts[0]] = (sum(values), values[3] + values[4])
        return cores

    def _driver(self, pid):
        """(CPU ticks, run delay in ns) over all threads, None once it has gone."""
        stat = _read(f'{self.proc}/{pid}/stat')
        if stat is None:
            return None
        fields = stat.rpartition(')')[2].split()  # the name may hold spaces; utime and stime are fields 14 and 15
        ticks = int(fields[11]) + int(fields[12])
        delay = 0
        try:
            for task in os.listdir(f'{self.proc}/{pid}/task'):
                parts = (_read(f'{self.proc}/{pid}/task/{task}/schedstat') or '').split()
                delay += int(parts[1]) if len(parts) > 1 else 0
        except OSError:
            pass
        return ticks, delay

    def sample(self):
        now = self.clock()
        if self.scanned is None or now - self.scanned >= self.RESCAN_S or not self.pids:
            self._scan()
            self.scanned = now
        out = {'driver_pids': list(self.pids)}
        dt = now - self.last_at if self.last_at is not None else None
        stat = self._system()
        if self.last_stat and stat:
            busy = {}
            for name, (total, idle) in stat.items():
                was = self.last_stat.get(name)
                if was and total > was[0]:
                    busy[name] = 100.0 * (1 - (idle - was[1]) / (total - was[0]))
            if 'cpu' in busy:
                out['system_pct'] = round(busy.pop('cpu'), 1)
            if busy:
                out['core_max_pct'] = round(max(busy.values()), 1)
        driver = {}
        for pid in self.pids:
            if (read := self._driver(pid)) is not None:
                driver[pid] = read
        self.pids = sorted(driver)
        out['driver_pids'] = list(self.pids)
        if exes := sorted({self.exes[pid] for pid in self.pids if self.exes.get(pid)}):
            out['driver_exes'] = exes
        if dt and driver:
            ticks = delay = 0
            for pid, (t, d) in driver.items():
                was = self.last_driver.get(pid)
                if was:
                    ticks += t - was[0]
                    delay += d - was[1]
            out['driver_pct'] = round(100.0 * ticks / self.tick / dt, 1)
            out['run_delay_ms'] = round(delay / 1e6, 2)
        self.last_stat, self.last_driver, self.last_at = stat, driver, now
        return out


class FeedValue:
    """Attaches the pose segment read-only and reads one aligned float, so it needs no mutex: the mutex
    guards a 16-float copy, and a lone aligned device_ms cannot tear on x86. It changes only when a sample lands."""

    def __init__(self, path=DEFAULT_FEED, index=FEED_DEVICE_MS_INDEX):
        self.path, self.index, self.addr = str(path), index, None
        self.libc = ctypes.CDLL(None, use_errno=True)
        self.libc.ftok.argtypes = [ctypes.c_char_p, ctypes.c_int]
        self.libc.ftok.restype = ctypes.c_int
        self.libc.shmget.argtypes = [ctypes.c_int, ctypes.c_size_t, ctypes.c_int]
        self.libc.shmget.restype = ctypes.c_int
        self.libc.shmat.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
        self.libc.shmat.restype = ctypes.c_void_p
        self.libc.shmdt.argtypes = [ctypes.c_void_p]

    def attach(self):
        self.close()
        key = self.libc.ftok(self.path.encode(), 0)
        segment = self.libc.shmget(key, 0, 0) if key != -1 else -1
        if segment < 0:
            return False
        addr = self.libc.shmat(segment, None, SHM_RDONLY)
        if addr in (None, ctypes.c_void_p(-1).value):
            return False
        self.addr = addr
        return True

    def read(self):
        if self.addr is None:
            return None
        return ctypes.c_uint32.from_address(self.addr + self.index * 4).value  # the raw bits: NaN never equals itself

    def close(self):
        if self.addr is not None:
            self.libc.shmdt(self.addr)
            self.addr = None


def clock_offset():
    """CLOCK_BOOTTIME minus CLOCK_MONOTONIC: it grows only across a suspend."""
    return time.clock_gettime(time.CLOCK_BOOTTIME) - time.clock_gettime(time.CLOCK_MONOTONIC)
