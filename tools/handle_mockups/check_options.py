#!/usr/bin/env python3
"""Measures the drawn options against what handle_options.py promises (see README.md).

Per option and theme, on the pixels of its overlay: how far it reaches past a side edge at rest (tiles sit 40 px apart, so
the limit is 18 px), whether a solid shape lands on the desktop's content, the brightest pixel against the 191/255 OLED
cap, and whether it really takes its colour from the theme. Prints a table; exits non-zero if an option breaks a limit.
"""
from __future__ import annotations

import colorsys
import json
import math
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from handle_options import OLED_CAP, OPTIONS, Hot, Sketch, Theme  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
BINARY = REPO / "build/mockups/handle_preview"
MARGIN, DENSITY = 80, 2
SIDE_LIMIT = 18.0
# Antialiased strokes stacked on one pixel round up in cairo, by at most 2 levels on a few hundred of 7 million pixels.
# Sketch.finish clamps them, so no output pixel passes the cap; more than this would mean a design is too bright.
CAP_SLACK = 2
ACCENTS = {"grey": "8d8d8d", "blue": "7aa2f7", "red": "f7768e", "green": "9ece6a"}


def overlay(option, theme: Theme, width: float, height: float, hot: Hot) -> tuple[np.ndarray, float, float]:
    s = Sketch(width, height, MARGIN, DENSITY, theme, hot)
    option.draw(s)
    pixels = np.frombuffer(s.finish(), np.uint8).reshape(s.px_h, s.px_w, 4)
    return pixels, s.sx, s.peak


def hue_of(pixels: np.ndarray) -> float | None:
    """The mean hue of the drawn, coloured pixels, translucent ones by their own colour (the overlay is premultiplied)."""
    drawn = pixels[pixels[..., 3] > 100].astype(np.float32)
    if not len(drawn):
        return None
    solid = drawn[:, :3] / drawn[:, 3:]
    angle = [colorsys.rgb_to_hsv(*p)[0] * math.tau for p in solid[:: max(1, len(solid) // 4000)] if max(p) - min(p) > 0.04]
    if not angle:
        return None
    return float(np.angle(np.mean(np.exp(1j * np.array(angle)))) / math.tau % 1)


def main() -> int:
    failed = False
    info = {name: json.loads(subprocess.run([str(BINARY), "info", hex_], capture_output=True, text=True, check=True).stdout)
            for name, hex_ in ACCENTS.items()}
    tile = info["grey"]["tiles"][1]
    W, H = tile[2], tile[3]
    print(f"{'option':<20} {'side reach':>10} {'hot reach':>10} {'on content':>10} {'peak':>5}  theme hue error (deg)")
    for option in OPTIONS:
        theme = Theme(info["blue"]["scheme"])
        idle, density, idle_peak = overlay(option, theme, W, H, Hot())
        hot_all = Hot(frozenset({0, 1}), frozenset({0, 1, 2, 3}))
        hot, _, hot_peak = overlay(option, theme, W, H, hot_all)
        margin_px = MARGIN * density

        def reach(pixels: np.ndarray) -> float:
            columns = np.nonzero((pixels[..., 3] > 128).any(axis=0))[0]
            left = (margin_px - columns.min()) / density
            right = (columns.max() + 1 - margin_px) / density - W
            return max(left, right)

        def on_content(pixels: np.ndarray) -> int:
            x0, x1 = round(margin_px) + 1, round(margin_px + W * density) - 1
            y0, y1 = round(margin_px) + 1, round(margin_px + H * density) - 1
            return int((pixels[y0:y1, x0:x1, 3] > 128).sum())

        side, side_hot = reach(idle), reach(hot)
        content = on_content(idle) + on_content(hot)
        peak = round(max(idle_peak, hot_peak) * 255)  # before the clamp in Sketch.finish
        errors = []
        for name in ("blue", "red", "green"):
            at_rest, _, _ = overlay(option, Theme(info[name]["scheme"]), W, H, Hot())
            lit, _, _ = overlay(option, Theme(info[name]["scheme"]), W, H, hot_all)
            pixels = np.concatenate((at_rest.reshape(-1, 4), lit.reshape(-1, 4)))[None]  # both states: a lit handle is near white
            drawn, want = hue_of(pixels), hue_of(np.array([[[*(int(ACCENTS[name][i:i + 2], 16) for i in (0, 2, 4)), 255]]], np.uint8))
            errors.append(None if drawn is None else abs((drawn - want + 0.5) % 1 - 0.5) * 360)
        grey, _, _ = overlay(option, Theme(info["grey"]["scheme"]), W, H, hot_all)
        grey_solid = grey[grey[..., 3] > 200][:, :3].astype(int)
        grey_chroma = int((grey_solid.max(axis=1) - grey_solid.min(axis=1)).max()) if len(grey_solid) else 0
        flags = []
        if side > SIDE_LIMIT:
            flags.append("SIDE")
        if content:
            flags.append("CONTENT")
        if peak > round(OLED_CAP * 255) + CAP_SLACK:
            flags.append("CAP")
        if any(e is None or e > 20 for e in errors) or grey_chroma > 6:
            flags.append("THEME")
        failed |= bool(flags)
        shown = " ".join("  n/a" if e is None else f"{e:5.1f}" for e in errors)
        print(f"{option.number:02d} {option.name:<17} {side:>9.1f}p {side_hot:>9.1f}p {content:>10} {peak:>5}  {shown}  grey chroma {grey_chroma}  {' '.join(flags) or 'ok'}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
