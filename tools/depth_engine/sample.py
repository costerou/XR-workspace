"""Test pictures with a known answer, made here so the repo carries no image licences.

street():  a checker road running to a horizon, with a large near block and a small far block.
desktop(): windows over a gradient wallpaper, with text-like rows.
"""
from __future__ import annotations

import numpy as np


def _rgba(rgb: np.ndarray) -> np.ndarray:
    alpha = np.full(rgb.shape[:2] + (1,), 255, dtype=np.uint8)
    return np.concatenate([np.clip(rgb, 0, 255).astype(np.uint8), alpha], axis=2)


def street(w: int = 1920, h: int = 1200) -> np.ndarray:
    horizon = int(h * 0.42)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    sky = np.stack([120 + 80 * yy / horizon, 160 + 60 * yy / horizon, 235 - 20 * yy / horizon], axis=2)
    img = sky
    below = yy > horizon
    z = np.where(below, (h - horizon) / np.maximum(yy - horizon, 1), 1)  # Distance along the road.
    x = (xx - w / 2) / w * z * 6
    check = ((np.floor(x) + np.floor(z * 3)) % 2).astype(np.float32)
    fade = np.clip(z / 12, 0, 1)[..., None]
    road = np.stack([70 + 90 * check, 75 + 90 * check, 70 + 85 * check], axis=2) * (1 - fade) + np.array([150, 160, 170]) * fade
    img = np.where(below[..., None], road, img)

    def block(x0, y0, x1, y1, colour):
        img[y0:y1, x0:x1] = colour
        img[y0:y0 + max((y1 - y0) // 12, 2), x0:x1] = np.array(colour) * 1.35
        img[y0:y1, x1 - max((x1 - x0) // 8, 2):x1] = np.array(colour) * 0.6

    block(int(w * .62), int(h * .55), int(w * .92), int(h * .97), (190, 50, 45))    # Near: stands low in the frame.
    block(int(w * .20), int(h * .36), int(w * .27), int(h * .47), (60, 90, 170))    # Far: stands near the horizon.
    return _rgba(img)


def desktop(w: int = 1920, h: int = 1200) -> np.ndarray:
    yy = np.linspace(0, 1, h, dtype=np.float32)[:, None]
    img = np.stack([20 + 40 * yy * np.ones((h, w)), 30 + 30 * yy * np.ones((h, w)), 60 + 90 * yy * np.ones((h, w))], axis=2)
    rng = np.random.default_rng(3)
    for x0, y0, x1, y1, bg, fg in [(.04, .08, .49, .66, (24, 26, 34), (170, 210, 160)), (.52, .05, .96, .48, (245, 245, 245), (40, 40, 40)),
                                   (.54, .53, .94, .92, (12, 12, 12), (120, 230, 120))]:
        l, t, r, b = int(x0 * w), int(y0 * h), int(x1 * w), int(y1 * h)
        img[t + 8:b + 8, l + 8:r + 8] = (8, 8, 12)
        img[t:b, l:r] = bg
        img[t:t + 30, l:r] = (50, 52, 60)
        for row in range(t + 44, b - 14, 17):
            for word in range(rng.integers(3, 12)):
                sx = l + 14 + word * 60 + int(rng.integers(0, 12))
                img[row:row + 9, sx:min(sx + int(rng.integers(20, 54)), r - 8)] = fg
    return _rgba(img)
