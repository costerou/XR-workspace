"""The serving loop: newest frame of each desktop in, smoothed depth map out."""
from __future__ import annotations

import os
import time
import zlib

from . import shm as S
from .backends import resize_rgba, to_half
from .scheduler import Scheduler
from .temporal import TemporalFilter, luma_of


class Stats:
    def __init__(self):
        self.inferred = self.unchanged = self.batches = 0


class Engine:
    def __init__(self, shm: S.DepthShm, backend, scheduler: Scheduler | None = None, clock=time.monotonic_ns):
        self.shm = shm
        self.backend = backend
        self.scheduler = scheduler or Scheduler()
        self.clock = clock
        self.stats = Stats()
        self._seen: dict[int, int] = {}
        self._crc: dict[int, int] = {}
        self._pending: dict[int, S.Frame] = {}
        self._filters: dict[int, TemporalFilter] = {}
        self._last_ns: dict[int, int] = {}
        self._pid = os.getpid()

    def _collect(self) -> tuple[list[int], set[int]]:
        focused = set()
        for ch in range(self.shm.channels):
            control = self.shm.control(ch)
            if not control & S.ENABLED:
                # Switched off: forget it, so switching back on starts from a fresh map rather than a stale one.
                self._pending.pop(ch, None)
                self._filters.pop(ch, None)
                self._crc.pop(ch, None)
                self._last_ns.pop(ch, None)
                self.scheduler.forget(ch)
                continue
            if control & S.FOCUSED:
                focused.add(ch)
            frame = self.shm.fetch(S.INPUT, ch, self._seen.get(ch, 0))
            if frame is None or frame.format not in (S.FMT_RGBA8, S.FMT_BGRA8):
                continue
            self._seen[ch] = frame.source_seq
            crc = zlib.crc32(frame.pixels.data)
            if crc == self._crc.get(ch):  # Capture re-delivers still desktops; the same picture needs no second look.
                self.stats.unchanged += 1
                continue
            self._crc[ch] = crc
            self._pending[ch] = frame
        return sorted(self._pending), focused

    def step(self) -> int:
        """One pass; returns how many maps were published."""
        now_ns = self.clock()
        self.shm.heartbeat(now_ns, self._pid)
        ready, focused = self._collect()
        picks = self.scheduler.pick(now_ns / 1e9, ready, focused)
        if not picks:
            return 0
        frames = [self._pending.pop(ch) for ch in picks]
        pixels = []
        for f in frames:
            px = f.pixels[..., [2, 1, 0, 3]] if f.format == S.FMT_BGRA8 else f.pixels
            if self.backend.size:
                px = resize_rgba(px, *self.backend.size)
            pixels.append(px)
        if len({p.shape for p in pixels}) == 1:
            maps = self.backend.infer(pixels)
        else:  # Desktops of different shapes cannot share a batch.
            maps = [self.backend.infer([p])[0] for p in pixels]
        self.stats.batches += 1
        done = 0
        for ch, f, px, depth in zip(picks, frames, pixels, maps):
            filt = self._filters.setdefault(ch, TemporalFilter())
            end_ns = self.clock()
            last = self._last_ns.get(ch)
            dt = 1 / 60 if last is None else min((end_ns - last) / 1e9, 1.0)
            self._last_ns[ch] = end_ns
            smooth, cut, flat = filt.update(depth, luma_of(px), dt)
            flags = (S.FLAG_SCENE_CUT if cut else 0) | (S.FLAG_FLAT if flat else 0)
            if self.shm.publish(S.OUTPUT, ch, to_half(smooth), S.FMT_R16F, f.source_seq, f.source_ns, self.clock(), flags):
                done += 1
            self.stats.inferred += 1
        self.scheduler.ran(picks, now_ns / 1e9)
        return done

    def run(self, should_stop) -> None:
        while not should_stop():
            if self.step() == 0:
                time.sleep(0.001)
