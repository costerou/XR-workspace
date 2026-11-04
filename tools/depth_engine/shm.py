"""Python side of spike/include/xr/depth.hpp: the same file, byte for byte.

Sequence numbers go through numpy integer views, so each one is a single aligned store; bytes-at-a-time packing could
expose half a number to the renderer. x86 keeps stores in order, which is all a seqlock writer needs.
"""
from __future__ import annotations

import mmap
import os
from dataclasses import dataclass

import numpy as np

MAGIC = 0x31445258
VERSION = 1
SLOTS = 3
MAX_CHANNELS = 16
HEADER_BYTES = 4096
CHANNEL_HEADER_BYTES = 320
META_BYTES = 48
OFF_ENGINE_NS = 48
OFF_ENGINE_PID = 56
OFF_META_IN = 16
OFF_META_OUT = OFF_META_IN + SLOTS * META_BYTES

FMT_RGBA8, FMT_BGRA8, FMT_R16F, FMT_R32F = 1, 2, 3, 4
BYTES_PER_PIXEL = {FMT_RGBA8: 4, FMT_BGRA8: 4, FMT_R16F: 2, FMT_R32F: 4}
FLAG_SCENE_CUT, FLAG_FLAT = 1, 2
ENABLED, FOCUSED = 1, 2
INPUT, OUTPUT = 0, 1

PATCH = 14
MIN_TALL = 266
LONG_HIGH = 518
LONG_BALANCED = 420


def round_patch(value: float) -> int:
    # lround: halves go away from zero, unlike Python's round().
    return max(1, int(value / PATCH + 0.5)) * PATCH


def input_size(src_w: int, src_h: int, long_side: int) -> tuple[int, int]:
    """depth_input_size() of depth.hpp."""
    if src_w <= 0 or src_h <= 0 or long_side <= 0:
        return 0, 0
    aspect = src_w / src_h
    if aspect >= 1:
        w, h = round_patch(long_side), round_patch(long_side / aspect)
    else:
        h, w = round_patch(long_side), round_patch(long_side * aspect)
    if h < MIN_TALL:
        h, w = MIN_TALL, round_patch(MIN_TALL * aspect)
    return w, h


def channel_stride(in_w: int, in_h: int, out_w: int, out_h: int) -> int:
    n = CHANNEL_HEADER_BYTES + SLOTS * in_w * in_h * 4 + SLOTS * out_w * out_h * 4
    return (n + 4095) // 4096 * 4096


def shm_bytes(channels: int, in_w: int, in_h: int, out_w: int, out_h: int) -> int:
    return HEADER_BYTES + channel_stride(in_w, in_h, out_w, out_h) * channels


@dataclass
class Frame:
    w: int
    h: int
    source_seq: int
    source_ns: int
    done_ns: int
    format: int
    flags: int
    pixels: np.ndarray  # h x w x 4 uint8, or h x w float


class DepthShm:
    def __init__(self, fd: int, size: int):
        self._mm = mmap.mmap(fd, size)
        self._u32 = np.frombuffer(self._mm, dtype=np.uint32, count=size // 4)
        self._u64 = np.frombuffer(self._mm, dtype=np.uint64, count=size // 8)
        self.bytes = size

    @classmethod
    def create(cls, path: str, channels: int, in_w: int, in_h: int, out_w: int, out_h: int) -> "DepthShm":
        if not 0 < channels <= MAX_CHANNELS or min(in_w, in_h, out_w, out_h) <= 0:
            raise ValueError("bad depth shm size")
        size = shm_bytes(channels, in_w, in_h, out_w, out_h)
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.ftruncate(fd, size)
            shm = cls(fd, size)
        finally:
            os.close(fd)
        u32 = shm._u32
        u32[1], u32[2], u32[3] = VERSION, channels, SLOTS
        u32[4], u32[5], u32[6], u32[7] = in_w, in_h, out_w, out_h
        shm._u64[4] = channel_stride(in_w, in_h, out_w, out_h)
        shm._u64[5] = size
        u32[0] = MAGIC
        shm._load()
        return shm

    @classmethod
    def open(cls, path: str) -> "DepthShm":
        fd = os.open(path, os.O_RDWR)
        try:
            size = os.fstat(fd).st_size
            if size < HEADER_BYTES:
                raise ValueError("depth shm too short")
            shm = cls(fd, size)
        finally:
            os.close(fd)
        shm._load()
        return shm

    def _load(self) -> None:
        u32, u64 = self._u32, self._u64
        if int(u32[0]) != MAGIC or int(u32[1]) != VERSION or int(u32[3]) != SLOTS or not 0 < int(u32[2]) <= MAX_CHANNELS:
            raise ValueError("not a depth shm of this version")
        self.channels = int(u32[2])
        self.in_w, self.in_h, self.out_w, self.out_h = (int(v) for v in u32[4:8])
        self.stride = int(u64[4])
        if int(u64[5]) != shm_bytes(self.channels, self.in_w, self.in_h, self.out_w, self.out_h) or self.stride != channel_stride(
                self.in_w, self.in_h, self.out_w, self.out_h) or self.bytes < int(u64[5]):
            raise ValueError("depth shm layout mismatch")

    def close(self) -> None:
        del self._u32, self._u64
        self._mm.close()

    def _chan(self, ch: int) -> int:
        return HEADER_BYTES + ch * self.stride

    def _slot_bytes(self, side: int) -> int:
        return self.in_w * self.in_h * 4 if side == INPUT else self.out_w * self.out_h * 4

    def _pixels_at(self, ch: int, side: int, slot: int) -> int:
        base = self._chan(ch) + CHANNEL_HEADER_BYTES
        if side == INPUT:
            return base + slot * self._slot_bytes(INPUT)
        return base + SLOTS * self._slot_bytes(INPUT) + slot * self._slot_bytes(OUTPUT)

    def _meta_at(self, ch: int, side: int, slot: int) -> int:
        return self._chan(ch) + (OFF_META_IN if side == INPUT else OFF_META_OUT) + slot * META_BYTES

    def publish(self, side: int, ch: int, pixels: np.ndarray, fmt: int, source_seq: int, source_ns: int,
                done_ns: int = 0, flags: int = 0) -> bool:
        """Write a frame without waiting for the reader. False if it does not fit."""
        if not 0 <= ch < self.channels or fmt not in BYTES_PER_PIXEL:
            return False
        h, w = pixels.shape[:2]
        max_w, max_h = (self.in_w, self.in_h) if side == INPUT else (self.out_w, self.out_h)
        data = memoryview(pixels if pixels.flags.c_contiguous else pixels.copy()).cast("B")
        if w <= 0 or h <= 0 or w > max_w or h > max_h or data.nbytes != w * h * BYTES_PER_PIXEL[fmt]:
            return False
        latest_i = (self._chan(ch) + (0 if side == INPUT else 4)) // 4
        slot = (int(self._u32[latest_i]) + 1) % SLOTS
        meta = self._meta_at(ch, side, slot)
        mi, m8 = meta // 4, meta // 8
        seq = int(self._u32[mi])
        self._u32[mi] = (seq + 1) & 0xffffffff
        at = self._pixels_at(ch, side, slot)
        self._mm[at:at + data.nbytes] = data
        self._u32[mi + 1], self._u32[mi + 2], self._u32[mi + 3] = w, h, fmt
        self._u64[m8 + 2], self._u64[m8 + 3], self._u64[m8 + 4] = source_seq, source_ns, done_ns
        self._u32[mi + 10] = flags
        self._u32[mi] = (seq + 2) & 0xffffffff
        self._u32[latest_i] = slot
        return True

    def fetch(self, side: int, ch: int, seen: int = 0) -> Frame | None:
        """The newest frame with a source_seq other than `seen`, as a copy. Never blocks."""
        if not 0 <= ch < self.channels:
            return None
        latest_i = (self._chan(ch) + (0 if side == INPUT else 4)) // 4
        for _ in range(4):
            slot = int(self._u32[latest_i])
            if slot >= SLOTS:
                return None
            meta = self._meta_at(ch, side, slot)
            mi, m8 = meta // 4, meta // 8
            before = int(self._u32[mi])
            if before == 0:
                return None
            if before & 1:
                continue
            w, h, fmt = (int(v) for v in self._u32[mi + 1:mi + 4])
            source_seq, source_ns, done_ns = (int(v) for v in self._u64[m8 + 2:m8 + 5])
            flags = int(self._u32[mi + 10])
            if source_seq == seen:
                if int(self._u32[mi]) == before:
                    return None
                continue
            bpp = BYTES_PER_PIXEL.get(fmt, 0)
            size = w * h * bpp
            if not bpp or w <= 0 or h <= 0 or size > self._slot_bytes(side):
                continue
            at = self._pixels_at(ch, side, slot)
            raw = np.frombuffer(self._mm, dtype=np.uint8, count=size, offset=at).copy()
            if int(self._u32[mi]) != before:
                continue
            if fmt == FMT_R16F:
                px = raw.view(np.float16).reshape(h, w)
            elif fmt == FMT_R32F:
                px = raw.view(np.float32).reshape(h, w)
            else:
                px = raw.reshape(h, w, 4)
            return Frame(w, h, source_seq, source_ns, done_ns, fmt, flags, px)
        return None

    def set_control(self, ch: int, control: int) -> None:
        self._u32[(self._chan(ch) + 8) // 4] = control

    def control(self, ch: int) -> int:
        return int(self._u32[(self._chan(ch) + 8) // 4])

    def heartbeat(self, now_ns: int, pid: int) -> None:
        self._u32[OFF_ENGINE_PID // 4] = pid
        self._u64[OFF_ENGINE_NS // 8] = now_ns

    def engine_ns(self) -> int:
        return int(self._u64[OFF_ENGINE_NS // 8])
