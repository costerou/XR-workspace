#!/usr/bin/env python3
"""Measures the drawn options against what cursor_options.py promises (see README.md).

Every number comes from real frames of cursor_preview on flat test desktops, with today's dot as the reference:
  size      the outline's width x height, in px. The dot is 16 wide; the move look is 35 across.
  centre    how far the shape's middle is from the click point, in px.
  peak      the brightest pixel at the held level (the dot's brightest), against the 191/255 OLED cap.
  light     the light it puts on the panel, as a share of the dot's.
  covers    how many pixels of what is behind it go solid, as a share of the dot's.
  stem      solid grey pixels on one row across the stem, at four sideways positions between two pixels. In a run the head
            moves the cursor by fractions of a pixel, and with no anti-aliasing a stem that changes here would shimmer.
Prints a table; exits non-zero if an option breaks a limit.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from cursor_options import HELD, LEVEL, MARK_GAP, MARK_HALF, MARK_LEN, OLED_CAP, OPTIONS, RADIUS, RING, SEGMENTS  # noqa: E402
from make_cursor_mockups import DEFAULT_BINARY, DOT_X, Batch, centred, desktop_pixels  # noqa: E402

CROP = centred(80, 80)
MAX_WIDTH, MAX_HEIGHT = 16, 35   # the dot's width, the move look's reach
STEM_ROW = {1: 8, 2: 8, 3: 5, 4: 5, 5: 5, 6: 5, 7: 2, 8: 11}  # px below the click point: a row of plain stem
OFFSETS = (0.0, 0.25, 0.5, 0.75)
ACCENT = "7aa2f7"  # a colour with a hue, so the glow's cap is measured on something brighter than a grey theme


def main() -> int:
    info = json.loads(subprocess.run([str(DEFAULT_BINARY), "info", ACCENT], capture_output=True, text=True, check=True).stdout)
    dot = info["dot"]
    wrong = [name for name, mine, theirs in (("level", LEVEL, dot["level"]), ("held", HELD, dot["held"]), ("radius", RADIUS, dot["radius"]),
                                             ("ring", RING, dot["ring"]), ("segments", SEGMENTS, dot["segments"]), ("gap", MARK_GAP, dot["gap"]),
                                             ("len", MARK_LEN, dot["len"]), ("half", MARK_HALF, dot["half"])) if abs(mine - theirs) > 1e-5]
    if wrong:
        print(f"cursor_options.py no longer matches view.hpp: {', '.join(wrong)}")
        return 1
    primary = tuple(info["scheme"]["primary"])
    W, H = info["desktop"]
    with tempfile.TemporaryDirectory(prefix=".check-", dir=HERE.parents[1] / "build") as name:
        work = Path(name)
        for kind in ("flat0", "flat128", "side"):
            desktop_pixels(kind, W, H, work / f"{kind}.bgra")
        batch = Batch(DEFAULT_BINARY, ACCENT, work, primary, 1)
        batch.add("empty", "flat128", None, CROP, cursor=False)
        batch.add("dot-mid", "flat128", None, CROP)
        batch.add("dot-black", "flat0", None, CROP, level=HELD)
        for o in OPTIONS:
            tag = f"{o.number:02d}"
            batch.add(f"{tag}-mid", "flat128", o.shape, CROP)
            batch.add(f"{tag}-black", "flat0", o.shape, CROP, level=HELD)
            for k, dx in enumerate(OFFSETS):
                batch.add(f"{tag}-off{k}", "flat128", o.shape, CROP, x=DOT_X + dx)
        frames, renderer = batch.run()
    print(f"measured on {renderer}, {W} x {H} desktops, theme colour #{ACCENT}\n")

    def pixels(key: str) -> np.ndarray:
        return np.asarray(frames[key], np.int32)

    empty = pixels("empty")
    grey = round(LEVEL * 255)

    def solid(mid: np.ndarray) -> np.ndarray:  # black ring or grey ink, not the glow
        return (mid.max(axis=2) <= 2) | (np.abs(mid - grey).max(axis=2) <= 2)

    dot_light = pixels("dot-black").sum()
    dot_solid = int(solid(pixels("dot-mid")).sum())
    cap = round(OLED_CAP * 255)
    failed = False
    print(f"{'option':<20} {'size':>8} {'centre':>7} {'peak':>5} {'light':>6} {'covers':>7}  stem px per position")
    print(f"{'-- the dot today':<20} {'16 x 16':>8} {'0.0':>7} {pixels('dot-black').max():>5} {'100%':>6} {'100%':>7}")
    for o in OPTIONS:
        tag = f"{o.number:02d}"
        mid = pixels(f"{tag}-mid")
        ink = solid(mid)
        ys, xs = np.nonzero(ink)
        width, height = int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1)
        # A pixel's centre is at +0.5, and a run's frame sits on a whole px: the crop's middle is the corner between four pixels.
        off = max(abs((xs.min() + xs.max() + 1) / 2 - CROP[2] / 2), abs((ys.min() + ys.max() + 1) / 2 - CROP[3] / 2))
        black = pixels(f"{tag}-black")
        peak = int(black.max())
        light, covers = black.sum() / dot_light, int(ink.sum()) / dot_solid
        row = CROP[3] // 2 + STEM_ROW[o.number]
        stems = []
        for k in range(len(OFFSETS)):
            shot = pixels(f"{tag}-off{k}")
            stems.append(int((np.abs(shot[row] - grey).max(axis=1) <= 2).sum()))
        flags = []
        if width > MAX_WIDTH or height > MAX_HEIGHT:
            flags.append("SIZE")
        if off > 1:
            flags.append("CENTRE")
        if peak > cap:
            flags.append("CAP")
        if len(set(stems)) > 1:
            flags.append("SHIMMER")
        failed |= bool(flags)
        print(f"{tag} {o.name:<17} {f'{width} x {height}':>8} {off:>7.1f} {peak:>5} {light:>6.0%} {covers:>7.0%}  {' '.join(map(str, stems))}  {' '.join(flags) or 'ok'}")
    print(f"\nlimits: {MAX_WIDTH} px wide, {MAX_HEIGHT} px tall, 1 px off centre, peak {cap}, stem the same at every position")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
