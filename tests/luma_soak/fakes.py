"""A pretend machine for the live tests: nothing here reads the real USB tree, journal or pose feed."""
import ctypes
from datetime import datetime
import os
from pathlib import Path
import stat
import threading
import time

from tools.luma_soak.probes import FEED_DEVICE_MS_INDEX

IPC_CREAT, IPC_RMID = 0o1000, 0


def journal_line(t, message):
    return datetime.fromtimestamp(t).astimezone().isoformat() + f' host kernel: {message}'


class Machine:
    def __init__(self, root):
        self.root = Path(root)
        self.sysfs, self.proc = self.root / 'sysfs', self.root / 'proc'
        self.stream, self.audit = self.root / 'stream.log', self.root / 'audit.log'
        self.sysfs.mkdir()
        self.proc.mkdir()
        self.stream.write_text('')
        self.audit.write_text('')
        (self.proc / 'stat').write_text('cpu  100 0 100 800 0 0 0 0 0 0\ncpu0 50 0 50 400 0 0 0 0 0 0\n')
        self.journalctl = self.root / 'journalctl'
        self.journalctl.write_text(f'#!/bin/sh\ncase " $* " in *" -f "*) cat "{self.stream}"; exec sleep 60;; *) cat "{self.audit}";; esac\n')
        self.journalctl.chmod(self.journalctl.stat().st_mode | stat.S_IEXEC)

    def plug(self, path, ids, devnum):
        vid, pid = ids.split(':')
        folder = self.sysfs / path
        folder.mkdir(exist_ok=True)
        for name, value in (('idVendor', vid), ('idProduct', pid), ('devnum', devnum), ('product', 'x')):
            (folder / name).write_text(f'{value}\n')

    def glasses(self):
        self.plug('5-2', '1a86:8091', 3)
        self.plug('5-2.1', '35ca:1104', 4)

    def driver(self, pid=4242, name='xrDriver', exe=None):
        folder = self.proc / str(pid)
        (folder / 'task' / str(pid)).mkdir(parents=True)
        if exe is not None:  # the binary it runs, as /proc/PID/exe says; the target need not exist
            (folder / 'exe').symlink_to(exe)
        (folder / 'comm').write_text(f'{name}\n')
        (folder / 'stat').write_text(f'{pid} ({name} x) S ' + ' '.join(['0'] * 10) + ' 5 5 0\n')
        (folder / 'task' / str(pid) / 'schedstat').write_text('100 2000000 3\n')

    def argv(self, *extra):
        return ['--journalctl', str(self.journalctl), '--sysfs', str(self.sysfs), '--proc', str(self.proc), '--settle', '0', '--tick', '0.05',
                '--session', str(self.root / 'session.jsonl'), '--driver-log', str(self.root / 'driver.log'), '--feed', str(self.root / 'no-feed'), *extra]


class Feed:
    """A real SysV segment of our own, shaped like the driver's, with a thread bumping device_ms."""

    def __init__(self, path, rate_hz=200):
        self.path, self.rate = str(path), rate_hz
        Path(path).write_bytes(b'')
        self.libc = ctypes.CDLL(None)
        self.libc.ftok.argtypes = [ctypes.c_char_p, ctypes.c_int]
        self.libc.shmget.argtypes = [ctypes.c_int, ctypes.c_size_t, ctypes.c_int]
        self.libc.shmat.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
        self.libc.shmat.restype = ctypes.c_void_p
        self.libc.shmdt.argtypes = [ctypes.c_void_p]
        self.libc.shmctl.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
        self.id = self.libc.shmget(self.libc.ftok(self.path.encode(), 0), 64, IPC_CREAT | 0o600)
        assert self.id >= 0
        self.addr = self.libc.shmat(self.id, None, 0)
        self.value = ctypes.c_float.from_address(self.addr + FEED_DEVICE_MS_INDEX * 4)
        self.stopping = threading.Event()
        self.thread = None

    def run(self):
        def bump():
            while not self.stopping.wait(1 / self.rate):
                self.value.value += 16.0
        self.thread = threading.Thread(target=bump, daemon=True)
        self.thread.start()

    def close(self):
        self.stopping.set()
        if self.thread:
            self.thread.join()
        self.libc.shmdt(self.addr)
        self.libc.shmctl(self.id, IPC_RMID, None)
