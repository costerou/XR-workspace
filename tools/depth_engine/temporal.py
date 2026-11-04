"""Temporal smoothing: per-frame AI depth flickers, and in stereo flicker reads as a surface that swims.

Two different things flicker, so there are two stages:

1. The whole map. The model's output has no fixed scale: the same scene comes back brighter or darker from one frame to
   the next. align() fits each new map to the smoothed one, using only pixels whose picture did not change, which cancels
   the jump exactly. The 0..1 limits shown to the renderer come from the smoothed map and are eased, so they never jump.
2. Each pixel. Static areas are eased heavily. A pixel eases faster when its picture changed or its depth moved by more
   than noise, so a moving window edge does not drag a ghost.

A scene cut (most of the picture changed at once) resets both, so a new video shot never fades through the old depth.
Pure numpy with an injectable time step, so all of it is tested without a GPU.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class TemporalConfig:
    pixel_tau: float = 0.12        # Seconds: easing of a static pixel.
    range_tau: float = 0.35        # Seconds: easing of the normalisation limits.
    luma_low: float = 4 / 255      # Picture change under this is treated as noise...
    luma_high: float = 24 / 255    # ...and at this much the pixel follows at once.
    depth_low: float = 0.03        # The same for a change of depth with no change of picture.
    depth_high: float = 0.18
    cut_pixels: float = 0.55       # Share of pixels with a large picture change that makes a scene cut.
    cut_luma: float = 32 / 255
    flat_range: float = 1e-3       # A spread under this has no depth to show.
    align_still: float = 0.15      # Share of pixels that must be still to align a map to the last one.


def luma_of(rgba: np.ndarray) -> np.ndarray:
    """0..1 luma from h x w x 4 uint8. Green dominates, so BGRA and RGBA read near enough the same."""
    px = rgba.astype(np.float32)
    return (px[..., 0] * 0.25 + px[..., 1] * 0.5 + px[..., 2] * 0.25) / 255.0


def _ramp(x: np.ndarray, low: float, high: float) -> np.ndarray:
    t = np.clip((x - low) / (high - low), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


class TemporalFilter:
    def __init__(self, config: TemporalConfig | None = None):
        self.cfg = config or TemporalConfig()
        self.reset()

    def reset(self) -> None:
        self._state = self._luma = None
        self._lo = self._hi = 0.0

    @staticmethod
    def _limits(depth: np.ndarray) -> tuple[float, float]:
        step = max(depth.shape[0] // 96, 1)
        sample = depth[::step, ::step].ravel()
        low_i, high_i = int(sample.size * 0.02), int(sample.size * 0.98)
        parts = np.partition(sample, (low_i, high_i))
        return float(parts[low_i]), float(parts[high_i])

    def align(self, disparity: np.ndarray, still: np.ndarray, lo: float, hi: float) -> np.ndarray:
        """Into the smoothed map's units: a scale and offset fitted on the still pixels, or, with too few of those to
        trust, the new map's limits laid over the old map's. `lo` and `hi` are the new map's own limits."""
        step = max(disparity.shape[0] // 48, 1)
        mask = still[::step, ::step]
        if mask.mean() >= self.cfg.align_still:
            x = disparity[::step, ::step][mask]
            y = self._state[::step, ::step][mask]
            var = float(x.var())
            if var >= self.cfg.flat_range ** 2:
                scale = float(((x - x.mean()) * (y - y.mean())).mean()) / var
                if scale > 0:  # Not positive: the maps disagree about what is near, so there is nothing to fit.
                    return disparity * scale + (float(y.mean()) - scale * float(x.mean()))
        s_lo, s_hi = self._limits(self._state)
        if s_hi - s_lo < self.cfg.flat_range:  # The last map was blank: start again from this one.
            s_lo, s_hi = 0.0, 1.0
        return (disparity - lo) / (hi - lo) * (s_hi - s_lo) + s_lo

    def update(self, disparity: np.ndarray, luma: np.ndarray, dt: float) -> tuple[np.ndarray, bool, bool]:
        """One map in; (smoothed 0..1 with 1 near, scene cut, flat) out. `luma` is the same size as `disparity`."""
        if not np.isfinite(disparity).all():
            disparity = np.nan_to_num(disparity, nan=0.0, posinf=0.0, neginf=0.0)
        cfg = self.cfg
        first = self._state is None or self._state.shape != disparity.shape
        cut = first
        change = None
        if not first:
            change = np.abs(luma - self._luma)
            cut = float((change > cfg.cut_luma).mean()) >= cfg.cut_pixels
        self._luma = luma
        lo, hi = self._limits(disparity)
        if cut:
            # Start in 0..1, so a blank frame's limits cannot be taken for a range.
            span = hi - lo
            self._state = ((disparity - lo) / span if span >= cfg.flat_range else disparity * 0).astype(np.float32)
            self._lo, self._hi = 0.0, 1.0
            if span < cfg.flat_range:
                return np.full_like(self._state, 0.5), not first, True
            return np.clip(self._state, 0.0, 1.0), not first, False
        if hi - lo < cfg.flat_range:
            return np.full_like(self._state, 0.5), False, True
        aligned = self.align(disparity, change < cfg.luma_low, lo, hi)
        base = 1.0 - np.exp(-dt / cfg.pixel_tau)
        delta = aligned - self._state
        follow = np.maximum(_ramp(change, cfg.luma_low, cfg.luma_high), _ramp(np.abs(delta), cfg.depth_low, cfg.depth_high))
        self._state = (self._state + delta * (base + (1.0 - base) * follow)).astype(np.float32)
        s_lo, s_hi = self._limits(self._state)
        a = 1.0 - np.exp(-dt / cfg.range_tau)
        self._lo += (s_lo - self._lo) * a
        self._hi += (s_hi - self._hi) * a
        span = max(self._hi - self._lo, cfg.flat_range)
        return np.clip((self._state - self._lo) / span, 0.0, 1.0), False, False
