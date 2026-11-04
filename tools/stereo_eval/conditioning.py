"""From an AI depth map to a comfortable disparity map: the reference for what the renderer should do.

Everything here is cheap image arithmetic a compute shader can do (box filters, tiles, per-pixel exponential smoothing) and
each stage can be switched off in a preset, so the sweep can show what it buys.

Input is "nearness": bigger is nearer, any scale. Depth Anything's relative output is this. It behaves like inverse
distance, and true disparity is linear in inverse distance, so the mapping to disparity below is a straight line.
"""
from dataclasses import dataclass
import math

import numpy as np
from PIL import Image

from .geometry import Optics

TILE = 16  # px, for the text and HUD detectors


def luma(rgb: np.ndarray) -> np.ndarray:
    """BT.709 luma, float32, 0..255."""
    return (rgb[..., 0] * 0.2126 + rgb[..., 1] * 0.7152 + rgb[..., 2] * 0.0722).astype(np.float32)


def box(a: np.ndarray, rx: int, ry: int | None = None) -> np.ndarray:
    """Mean over a (2ry+1) x (2rx+1) window; the edges repeat."""
    ry = rx if ry is None else ry
    out = a.astype(np.float32)
    if rx > 0:
        c = np.cumsum(np.pad(out, ((0, 0), (rx + 1, rx)), mode='edge'), axis=1, dtype=np.float64)
        out = ((c[:, 2 * rx + 1:] - c[:, :-(2 * rx + 1)]) / (2 * rx + 1)).astype(np.float32)
    if ry > 0:
        c = np.cumsum(np.pad(out, ((ry + 1, ry), (0, 0)), mode='edge'), axis=0, dtype=np.float64)
        out = ((c[2 * ry + 1:] - c[:-(2 * ry + 1)]) / (2 * ry + 1)).astype(np.float32)
    return out


def resize(a: np.ndarray, w: int, h: int) -> np.ndarray:
    if a.shape[1] == w and a.shape[0] == h:
        return a.astype(np.float32)
    return np.asarray(Image.fromarray(a.astype(np.float32), 'F').resize((w, h), Image.BILINEAR), dtype=np.float32)


def guided_filter(guide: np.ndarray, p: np.ndarray, r: int, eps: float) -> np.ndarray:
    """He et al.'s guided filter, grey guide in 0..1: snaps an upscaled depth map to the picture's edges in O(1) per pixel."""
    mi, mp = box(guide, r), box(p, r)
    cov = box(guide * p, r) - mi * mp
    var = box(guide * guide, r) - mi * mi
    a = cov / (var + eps)
    b = mp - a * mi
    return box(a, r) * guide + box(b, r)


def tiles(a: np.ndarray, t: int = TILE) -> np.ndarray:
    """Mean of each t x t tile, the partial last row and column dropped."""
    h, w = a.shape[0] // t, a.shape[1] // t
    return a[:h * t, :w * t].reshape(h, t, w, t).mean(axis=(1, 3))


def untile(m: np.ndarray, w: int, h: int, t: int = TILE) -> np.ndarray:
    """Nearest-neighbour back to w x h px; the leftover edge repeats the last tile."""
    out = np.repeat(np.repeat(m, t, axis=0), t, axis=1)
    return np.pad(out, ((0, h - out.shape[0]), (0, w - out.shape[1])), mode='edge')


def grow(m: np.ndarray, r: int) -> np.ndarray:
    return box(m.astype(np.float32), r) > 1e-4


def text_tiles(gray: np.ndarray) -> np.ndarray:
    """Which tiles hold text or UI: hard strokes on a flat ground. A photo or a game world has plenty of edges but hardly
    any truly flat pixels between them, so "edges plus flatness" separates them without a model."""
    gx = np.abs(np.diff(gray, axis=1, append=gray[:, -1:]))
    gy = np.abs(np.diff(gray, axis=0, append=gray[-1:, :]))
    grad = np.maximum(gx, gy)
    strong, flat = tiles((grad > 40).astype(np.float32)), tiles((grad < 3).astype(np.float32))
    hit = (strong >= 0.06) & (flat >= 0.30)
    return hit & (_row_run(hit) >= 3)  # text runs along a line; black patches in a photo trip the test in ones and twos


def _row_run(hit: np.ndarray) -> np.ndarray:
    """Length of the horizontal run of True each tile belongs to, allowing a one-tile gap (a space between words)."""
    out = np.zeros(hit.shape, np.int32)
    for y, row in enumerate(hit):
        x, n = 0, row.size
        while x < n:
            if not row[x]:
                x += 1
                continue
            end = x
            while end + 1 < n and (row[end + 1] or (end + 2 < n and row[end + 2])):
                end += 1
            out[y, x:end + 1] = end - x + 1
            x = end + 1
    return out


def text_mask(gray: np.ndarray) -> np.ndarray:
    """Pixel mask of text tiles, one tile of margin all round. Boolean, gray's size."""
    return untile(grow(text_tiles(gray), 1), gray.shape[1], gray.shape[0])


def soft_mask(mask: np.ndarray, r: int) -> np.ndarray:
    """1 exactly on the mask, easing to 0 over r px outside it: pixels inside stay pixel-identical to the source."""
    return np.clip(box(grow(mask, r).astype(np.float32), r), 0, 1) if r > 0 else mask.astype(np.float32)


def edge_window(w: int, h: int, fraction: float) -> np.ndarray:
    """0 at the frame's edge, 1 past `fraction` of the width (the same px from all four sides), smooth in between. Keeps
    the screen's border on the plane, so nothing is cut off by the frame while it stands in front of it."""
    if fraction <= 0:
        return np.ones((h, w), np.float32)
    band = max(1.0, fraction * w)
    ramp = lambda n: np.clip(np.minimum(np.arange(n), np.arange(n)[::-1]) / band, 0, 1)
    s = lambda t: t * t * (3 - 2 * t)
    return np.outer(s(ramp(h)), s(ramp(w))).astype(np.float32)


def soft_clip(d: np.ndarray, near: float, far: float) -> np.ndarray:
    """Linear in the middle, easing onto -near and +far: a compression and never a visible flat shelf."""
    def side(x, limit):
        if limit <= 0:
            return np.zeros_like(x)
        knee = 0.6 * limit
        over = np.maximum(x - knee, 0)
        return np.minimum(x, knee) + (limit - knee) * np.tanh(over / (limit - knee))
    return np.where(d >= 0, side(d, far), -side(-d, near))


def content_box(gray: np.ndarray, level: float = 12.0) -> tuple[int, int, int, int] | None:
    """(x0, y0, x1, y1) of the picture inside black letterbox or pillarbox bars, x1 and y1 exclusive; None if all black.
    A monocular depth model reads black bars as the nearest thing in the frame, so they must never reach it or the budget."""
    rows, cols = np.flatnonzero(gray.max(axis=1) > level), np.flatnonzero(gray.max(axis=0) > level)
    if rows.size == 0 or cols.size == 0:
        return None
    return int(cols[0]), int(rows[0]), int(cols[-1]) + 1, int(rows[-1]) + 1


def time_constant(dt_ms: float, tau_ms: float) -> float:
    """Blend factor for one step of an exponential smoother; independent of the depth rate."""
    return 1.0 if tau_ms <= 0 else 1 - math.exp(-dt_ms / tau_ms)


@dataclass
class Conditioned:
    disparity_arcmin: np.ndarray  # float32, frame size
    nearness: np.ndarray          # after filtering, 0..1-ish
    pinned: np.ndarray            # 1 where text or HUD is pinned to the plane
    gain: float
    cut: bool


class Conditioner:
    """Stateful: one per desktop. step() once per depth frame."""

    def __init__(self, preset: dict, optics: Optics = Optics(), fps: float = 30.0):
        self.p, self.optics, self.dt = preset, optics, 1000.0 / fps
        self.prev_gray = self.n = self.range = self.text_ema = self.motion = self.hud = self.box = None
        self.ease, self.box_age = 1.0, 0

    def _content(self, gray):
        """The picture's box. It widens at once and narrows only after a second, so a dark scene does not look like bars."""
        h, w = gray.shape
        if not self.p.get('letterbox', True):
            return 0, 0, w, h
        now = content_box(gray)
        if now is None:
            return self.box or (0, 0, w, h)
        if self.box is None or (now[0] <= self.box[0] and now[1] <= self.box[1] and now[2] >= self.box[2] and now[3] >= self.box[3]):
            self.box, self.box_age = now, 0
        elif now != self.box:
            self.box_age += self.dt
            if self.box_age > 1000:
                self.box, self.box_age = now, 0
        return self.box

    def _nearness(self, nearness, gray, inside):
        h, w = gray.shape
        n = resize(nearness, w, h)
        low, top = np.percentile(n[inside], [0.1, 99.9])
        n = (n - low) / max(float(top - low), 1e-6)
        if self.p.get('guided_radius', 0) > 0:
            n = guided_filter(gray / 255, n, self.p['guided_radius'], self.p.get('guided_eps', 1e-3))
        lo2, hi2 = np.percentile(n[inside], [2, 98])
        return n, float(hi2 - lo2) * float(top - low) / max(abs(float(top)), 1e-6)  # spread relative to the map's level

    def _temporal(self, n, gray, cut):
        tau_still, tau_move = self.p.get('temporal_still_ms', 0), self.p.get('temporal_motion_ms', 0)
        if self.n is None or cut or (tau_still <= 0 and tau_move <= 0):
            self.n = n
            return n
        change = box(np.abs(gray - self.prev_gray), 4)
        t = np.clip((change - 2) / 10, 0, 1)  # luma levels: 2 is sensor or codec noise, 12 is a moved edge
        a_still, a_move = time_constant(self.dt, tau_still), time_constant(self.dt, tau_move)
        self.n += (a_still + (a_move - a_still) * t) * (n - self.n)
        return self.n

    def _hud(self, gray):
        """Tiles that stay still while the rest of the frame moves are overlays (HUD, minimap, letterbox)."""
        if not self.p.get('hud_lock') or self.prev_gray is None:
            return None
        delta = tiles(np.abs(gray - self.prev_gray))
        a = time_constant(self.dt, 250)
        self.motion = delta if self.motion is None else self.motion + a * (delta - self.motion)
        moving = float((self.motion > 3).mean())
        if self.hud is None:
            self.hud = np.zeros_like(delta)
        if moving > 0.25:  # only a moving scene can tell an overlay from a still background
            self.hud += time_constant(self.dt, self.p.get('hud_ms', 400)) * ((self.motion < 1.0) - self.hud)
        return self.hud > 0.5

    def step(self, rgb: np.ndarray, nearness: np.ndarray) -> Conditioned:
        p, gray = self.p, luma(rgb)
        h, w = gray.shape
        cut = self.prev_gray is not None and float(np.abs(gray - self.prev_gray).mean()) > p.get('cut_luma', 30)
        x0, y0, x1, y1 = self._content(gray)
        inside = np.zeros((h, w), bool)
        inside[y0:y1, x0:x1] = True
        n, spread = self._nearness(nearness, gray, inside)
        n = self._temporal(n, gray, cut)

        pin = np.zeros((h, w), bool)
        if p.get('text_pin', 0) > 0:
            current = text_mask(gray)
            a = time_constant(self.dt, p.get('text_ms', 200))
            self.text_ema = current.astype(np.float32) if self.text_ema is None or cut else \
                self.text_ema + a * (current.astype(np.float32) - self.text_ema)
            pin |= self.text_ema > 0.5
        hud = self._hud(gray)
        if hud is not None:
            pin |= untile(hud, w, h)

        keep = (~pin & inside)[::8, ::8]
        sample = n[::8, ::8][keep] if keep.mean() > 0.02 else n[::8, ::8].ravel()
        now = np.percentile(sample, [p.get('range_lo_pct', 2), p.get('anchor_pct', 70), p.get('range_hi_pct', 98)])
        now = np.append(now, spread)
        if self.range is None or cut:
            self.range = now.astype(np.float64)
        else:
            self.range += time_constant(self.dt, p.get('range_ms', 1000)) * (now - self.range)
        lo, anchor, hi, spread = self.range

        # A flat scene has no depth to show: the model's noise must not be stretched to the whole budget.
        span = hi - lo
        gain = float(np.clip((spread - p.get('flat_span', 0.05)) / max(p.get('full_span', 0.25) - p.get('flat_span', 0.05), 1e-6), 0, 1))
        gain = gain * gain * (3 - 2 * gain)
        total = (p['near_arcmin'] + p['far_arcmin']) * p.get('strength', 1.0) * gain
        d = soft_clip(-(n - anchor) / max(span, 1e-3) * total, p['near_arcmin'], p['far_arcmin'])
        if p.get('soften_px', 0) > 0:
            r = p['soften_px']
            d = box(box(d, r), r)
        soft = soft_mask(pin, p.get('pin_feather_px', 6))
        frame = np.zeros((h, w), np.float32)
        frame[y0:y1, x0:x1] = edge_window(x1 - x0, y1 - y0, p.get('edge_fade', 0.0))
        d = d * (1 - soft * (1 - p.get('text_keep', 0.0))) * frame  # text_keep: share of the parallax left on pinned pixels

        if cut and p.get('cut_ease_ms', 0) > 0:
            self.ease = 0.0
        elif self.ease < 1:
            self.ease = min(1.0, self.ease + self.dt / p['cut_ease_ms'])
        self.prev_gray = gray
        return Conditioned((d * self.ease).astype(np.float32), n, soft, gain, cut)
