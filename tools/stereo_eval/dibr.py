"""Depth-image-based rendering: one picture plus a disparity map gives two eyes. CPU reference; the renderer's shader may
differ in how it walks the map, and the metrics score its output the same way (`python -m tools.stereo_eval sbs`).

Each eye is one row-by-row warp. Sources are visited in the direction that lets the nearer surface win; where neighbouring
sources land more than `GAP_PX` apart a hole has opened behind a foreground edge, and only then does fill run. With d = 0
a row maps onto itself, so a pixel pinned to the plane comes out identical to the source. Every output colour is a convex mix
or a copy of source pixels, so the eyes can never be brighter than the source (the OLED brightness cap holds).
"""
from dataclasses import dataclass

import numpy as np

GAP_PX = 1.5


@dataclass
class Eye:
    image: np.ndarray      # float32 H x W x 3, 0..255
    holes: np.ndarray      # bool, filled pixels
    disparity: np.ndarray  # px, the map as seen from this eye


def _warp_row(row, disp, direction, fill, x):
    """row: W x 3; disp: this eye's shift in px, already scaled by its share; direction: -1 left eye, +1 right eye."""
    w = row.shape[0]
    t = x + disp
    # d < 0 is near. The left eye moves near things right and the right eye moves them left; scanning with the motion keeps
    # the nearer surface where they fold.
    if direction < 0:
        keep = t >= np.maximum.accumulate(t)
        idx = np.flatnonzero(keep)
    else:
        keep = t <= np.minimum.accumulate(t[::-1])[::-1]
        idx = np.flatnonzero(keep)
    tk = t[idx]
    u = x
    out = np.empty_like(row)
    for c in range(row.shape[1]):
        out[:, c] = np.interp(u, tk, row[idx, c])
    warped = np.interp(u, tk, disp[idx])
    holes = np.zeros(w, bool)

    gaps = np.flatnonzero(np.diff(tk) > GAP_PX)
    spans = [(tk[i], tk[i + 1], idx[i], idx[i + 1]) for i in gaps]
    if tk[0] > 0.5:
        spans.append((-1.0, tk[0], None, idx[0]))
    if tk[-1] < w - 1.5:
        spans.append((tk[-1], float(w), idx[-1], None))
    for a, b, ia, ib in spans:
        first, last = max(int(np.floor(a)) + 1, 0), min(int(np.ceil(b)) - 1, w - 1)
        if last < first:
            continue
        holes[first:last + 1] = True
        if ia is None:
            far_is_right = True
        elif ib is None:
            far_is_right = False
        else:
            far_is_right = disp[ib] * direction > disp[ia] * direction  # disp is d times this eye's share
        far, near = (ib, ia) if far_is_right else (ia, ib)
        n = last - first + 1
        if fill == 'black':
            out[first:last + 1] = 0
        elif fill == 'stretch' or (fill == 'foreground' and near is None):
            out[first:last + 1] = row[far]
        elif fill == 'foreground':
            out[first:last + 1] = row[near]
        elif fill == 'mirror':
            # Reflect the background about the hole's edge: it keeps real texture where a smear leaves streaks.
            step = 1 if far_is_right else -1
            dist = np.arange(n, 0, -1) if far_is_right else np.arange(1, n + 1)
            src = np.clip(far + step * dist, 0, w - 1)
            out[first:last + 1] = row[src]
        else:
            raise ValueError(f'unknown fill {fill!r}')
        warped[first:last + 1] = disp[far]
    return out, holes, warped


def render_eye(rgb: np.ndarray, d_px: np.ndarray, side: str, fill: str = 'mirror', split: float = 0.5) -> Eye:
    """side 'left' or 'right'; `split` is the left eye's share of the disparity, the right takes the rest. 0.5 shares it."""
    h, w = d_px.shape
    image = np.empty((h, w, 3), np.float32)
    holes = np.empty((h, w), bool)
    warped = np.empty((h, w), np.float32)
    x = np.arange(w, dtype=np.float64)
    src = rgb.astype(np.float32)
    share, direction = (-split, -1) if side == 'left' else (1 - split, 1)
    dd = d_px.astype(np.float64) * share
    if not dd.any():
        return Eye(src.copy(), np.zeros((h, w), bool), np.zeros((h, w), np.float32))
    for y in range(h):
        if dd[y].any():
            image[y], holes[y], warped[y] = _warp_row(src[y], dd[y], direction, fill, x)
        else:
            image[y], holes[y], warped[y] = src[y], False, 0
    # warped holds the shifted amount; back to the pair's disparity.
    return Eye(image, holes, warped / share if share else warped)


def render_pair(rgb: np.ndarray, d_px: np.ndarray, fill: str = 'mirror', split: float = 0.5) -> tuple[Eye, Eye]:
    return render_eye(rgb, d_px, 'left', fill, split), render_eye(rgb, d_px, 'right', fill, split)
