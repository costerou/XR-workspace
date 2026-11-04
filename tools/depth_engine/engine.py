"""The serving loop: newest frame of each desktop in, smoothed depth map out."""
from __future__ import annotations

import os
import time
import zlib

import numpy as np

from . import shm as S
from .backends import resize_rgba, to_half
from .scheduler import Scheduler
from .temporal import TemporalFilter, luma_of
from .refinement import refine, limit_steps


BAR_LEVEL = 12        # Brightest a black bar may be, of 255.
BAR_MIN_SHARE = 0.03  # Of the frame's side: anything thinner is a dark edge of the picture, not a bar.
BOX_HOLD = 5          # Frames a new picture box has to last: a dark scene must not make the crop flicker.


def picture_box(rgba: np.ndarray, level: int = BAR_LEVEL) -> tuple[int, int, int, int] | None:
    """(x0, y0, x1, y1) of the picture inside black letterbox or pillarbox bars, x1 and y1 exclusive; None for no bars, or
    an all-black frame. The model reads black bars as the nearest thing on screen, which pushes the picture behind the plane
    (docs/3d-comfort.md), so they must never reach it."""
    bright = rgba[..., :3].max(axis=2)
    rows, cols = np.flatnonzero(bright.max(axis=1) > level), np.flatnonzero(bright.max(axis=0) > level)
    if rows.size == 0 or cols.size == 0:
        return None
    h, w = rgba.shape[:2]
    x0, y0, x1, y1 = int(cols[0]), int(rows[0]), int(cols[-1]) + 1, int(rows[-1]) + 1
    if (x0 + w - x1) < w * BAR_MIN_SHARE:
        x0, x1 = 0, w
    if (y0 + h - y1) < h * BAR_MIN_SHARE:
        y0, y1 = 0, h
    return None if (x1 - x0, y1 - y0) == (w, h) else (x0, y0, x1, y1)


def paste_depth(depth: np.ndarray, box: tuple[int, int, int, int], h: int, w: int) -> np.ndarray:
    """The map of a cropped picture, back in the whole frame. The bars take the picture's median, so they sit on its middle
    distance instead of pulling the range towards them."""
    x0, y0, x1, y1 = box
    out = np.full((h, w), float(np.median(depth)), np.float32)
    ys = (np.arange(y1 - y0) * depth.shape[0] / (y1 - y0)).astype(int)
    xs = (np.arange(x1 - x0) * depth.shape[1] / (x1 - x0)).astype(int)
    out[y0:y1, x0:x1] = depth[ys][:, xs]
    return out


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
        self._box: dict[int, tuple] = {}  # Channel: (the box in use, frames a different one has lasted).
        self._pid = os.getpid()

    def _stable_box(self, ch: int, box):
        held, waited = self._box.get(ch, (box, 0))
        if box == held:
            self._box[ch] = (held, 0)
            return held
        if waited + 1 >= BOX_HOLD:
            self._box[ch] = (box, 0)
            return box
        self._box[ch] = (held, waited + 1)
        return held

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
                self._box.pop(ch, None)
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
        pixels, wholes, boxes = [], [], []
        for ch, f in zip(picks, frames):
            px = f.pixels[..., [2, 1, 0, 3]] if f.format == S.FMT_BGRA8 else f.pixels
            box = self._stable_box(ch, picture_box(px))
            whole = resize_rgba(px, *self.backend.size) if self.backend.size else px
            if box is not None:
                px = px[box[1]:box[3], box[0]:box[2]]
                if self.backend.size:
                    px = resize_rgba(px, *self.backend.size)
            else:
                px = whole
            pixels.append(px)
            wholes.append(whole)
            boxes.append(box)
        if len({p.shape for p in pixels}) == 1:
            maps = self.backend.infer(pixels)
        else:  # Desktops of different shapes cannot share a batch.
            maps = [self.backend.infer([p])[0] for p in pixels]
        self.stats.batches += 1
        done = 0
        for ch, f, px, model_rgb, depth, box in zip(picks, frames, wholes, pixels, maps, boxes):
            depth = refine(depth, luma_of(model_rgb))
            if box is not None:
                # In the frame's own coordinates, whatever size the model works at.
                scale_x, scale_y = px.shape[1] / f.w, px.shape[0] / f.h
                depth = paste_depth(depth, (round(box[0] * scale_x), round(box[1] * scale_y), round(box[2] * scale_x), round(box[3] * scale_y)),
                                    px.shape[0], px.shape[1])
            filt = self._filters.setdefault(ch, TemporalFilter())
            end_ns = self.clock()
            last = self._last_ns.get(ch)
            dt = 1 / 60 if last is None else min((end_ns - last) / 1e9, 1.0)
            self._last_ns[ch] = end_ns
            gray = luma_of(px)
            smooth, cut, flat = filt.update(depth, gray, dt)
            flags = (S.FLAG_SCENE_CUT if cut else 0) | (S.FLAG_FLAT if flat else 0)
            if self.shm.publish(S.OUTPUT, ch, to_half(limit_steps(smooth)), S.FMT_R16F, f.source_seq, f.source_ns, self.clock(), flags):
                done += 1
            self.stats.inferred += 1
        self.scheduler.ran(picks, now_ns / 1e9)
        return done

    def run(self, should_stop) -> None:
        while not should_stop():
            if self.step() == 0:
                time.sleep(0.001)
