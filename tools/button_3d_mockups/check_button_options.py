#!/usr/bin/env python3
"""Measures the drawn 3D button options against what button_options.py promises (see README.md).

Per option, on the pixels of its overlay and the real hit-zone code (free_pointer): that it stays out of the level bar, the
band along the edge and the corners' zones, on the Luma Ultra tile and down to the narrowest desktop it reports; that no
solid pixel lands on a desktop's content or past a side edge; the 191/255 OLED cap; a hue that follows the theme on three
colours and no tint on a grey one; and that off, on, hover and global can be told apart. Prints a table; exits non-zero if
an option breaks a limit.
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
from button_options import GLOBAL, OFF, ON, OPTIONS, Frame, Geo, Hot, Option, Sketch, Theme, free_pointer, toggle_roles  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
BINARY = REPO / "build/button3d/button_preview"
MARGIN, DENSITY = 80, 2
TILE = (586.667, 366.667)      # a Luma Ultra desktop in the overview
OLED_CAP_LEVEL, CAP_SLACK = 191, 2  # as handle_mockups: a few antialiased pixels round up; Sketch.finish clamps them
MIN_TONE_STEP = 0.06           # mean luma step, 0..1, between off and on over a control
MIN_CHANGED = 0.03             # share of a control's box that must change between two states
MIN_CONTRAST = 3.0             # M3's minimum for icons and large text, WCAG ratio
HUE_LIMIT = 25.0               # degrees
ACCENTS = {"grey": "8d8d8d", "blue": "7aa2f7", "red": "f7768e", "green": "9ece6a"}
LUMA = np.array([0.2126, 0.7152, 0.0722], np.float32)


def pixels(option: Option, theme: Theme, frame: Frame, width: float = TILE[0], height: float = TILE[1]) -> tuple[np.ndarray, float]:
    s = Sketch(width, height, MARGIN, DENSITY, theme, Hot())
    option.draw(s, frame)
    return np.frombuffer(s.finish(), np.uint8).reshape(s.px_h, s.px_w, 4), s.peak


def box_of(array: np.ndarray, r: tuple[float, float, float, float], pad: float = 5) -> np.ndarray:
    x, y, w, h = [(v + MARGIN) * DENSITY if i < 2 else v * DENSITY for i, v in enumerate(r)]
    p = pad * DENSITY
    return array[int(y - p):int(y + h + p), int(x - p):int(x + w + p)]


def hue_of(px: np.ndarray) -> float | None:
    """Mean hue of the opaque, coloured pixels."""
    drawn = px[px[..., 3] > 200].astype(np.float32)
    if not len(drawn):
        return None
    solid = drawn[:, :3] / drawn[:, 3:]
    angle = [colorsys.rgb_to_hsv(*p)[0] * math.tau for p in solid[:: max(1, len(solid) // 4000)] if max(p) - min(p) > 0.04]
    return float(np.angle(np.mean(np.exp(1j * np.array(angle)))) / math.tau % 1) if angle else None


def linear_luma(rgb) -> float:
    return float(np.dot(np.array(rgb, np.float32), LUMA))


def contrast(a, b) -> float:
    """WCAG ratio. The scheme's roles are display-encoded."""
    def lin(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    la, lb = [float(np.dot([lin(c) for c in rgb], LUMA)) for rgb in (a, b)]
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def zone_problems(option: Option, width: float, height: float) -> list[str]:
    """What the real zone code says about the controls' drawn rects on a tile of this size."""
    g = Geo(width, height)
    tile = g.tile
    out = []
    bar = (g.x, g.y, g.w, g.h)
    rects = option.hits(g)
    names = list(rects)
    for name, (x, y, w, h) in rects.items():
        if x < bar[0] + bar[2] and bar[0] < x + w:
            out.append(f"{name} overlaps the bar")
        if y + h > -free_pointer.LEVEL_EDGE_CLEAR:
            out.append(f"{name} in the edge band")
        for i in range(9):
            for j in range(5):
                q = (x + w * i / 8, y + h * j / 4)
                handle = free_pointer.border_handle(tile, q)  # from LEVEL_EDGE_CLEAR out, the bar's row beats an edge's
                if handle is not None and handle[0] == "corner":
                    out.append(f"{name} in a resize zone at {q[0]:.0f},{q[1]:.0f}")
                    break
            else:
                continue
            break
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            ra, rb = rects[a], rects[b]
            if ra[0] < rb[0] + rb[2] and rb[0] < ra[0] + ra[2]:
                out.append(f"{a} overlaps {b}")
    return out


def narrowest(option: Option) -> int:
    """The narrowest 16:10 desktop, tile px, on which the zones hold."""
    best = 0
    for width in range(586, 119, -2):
        if zone_problems(option, width, width * 0.625):
            break
        best = width
    return best


def measure(option: Option, themes: dict[str, Theme]) -> tuple[dict, list[str]]:
    """Numbers for the table, and every limit broken."""
    failed = list(zone_problems(option, *TILE))
    geo = Geo(*TILE)
    blue = themes["blue"]
    main = option.hits(geo)["3d"]
    every = option.hits(geo).get("all", main)
    W, H = TILE
    frames = {"off": Frame(OFF), "on": Frame(ON), "global": Frame(GLOBAL), "off_hover": Frame(OFF, "3d"), "on_hover": Frame(ON, "3d"),
              "global_hover": Frame(GLOBAL, "3d")}
    if "all" in option.hits(geo):
        frames["all_hover"] = Frame(OFF, "all")
    drawn = {name: pixels(option, blue, f) for name, f in frames.items()}
    peak = max(p for _, p in drawn.values())
    if round(peak * 255) > OLED_CAP_LEVEL + CAP_SLACK:
        failed.append(f"peak {peak * 255:.0f} over the cap")
    margin_px = MARGIN * DENSITY
    for name, (px, _) in drawn.items():
        solid = px[..., 3] > 128
        x0, x1 = margin_px, round(margin_px + W * DENSITY)
        y0 = margin_px
        if solid[y0:, x0:x1].sum():
            failed.append(f"{name}: solid pixels on the content")
        if solid[:, :x0].sum() or solid[:, x1:].sum():
            failed.append(f"{name}: past a side edge")

    def changed(a: str, b: str, r) -> float:
        pa, pb = box_of(drawn[a][0], r).astype(np.int16), box_of(drawn[b][0], r).astype(np.int16)
        return float((np.abs(pa - pb).max(axis=2) > 24).mean())

    def tone(name: str, r) -> float:
        return linear_luma(box_of(drawn[name][0], r, 0)[..., :3].reshape(-1, 3).mean(axis=0) / 255)

    step = abs(tone("on", main) - tone("off", main))
    if step < MIN_TONE_STEP:
        failed.append(f"on and off only {step:.2f} apart")
    union = (min(main[0], every[0]), main[1], max(main[0] + main[2], every[0] + every[2]) - min(main[0], every[0]), main[3])
    pairs = {"on/off": changed("on", "off", main), "global/on": changed("global", "on", union), "hover/off": changed("off_hover", "off", main),
             "hover/on": changed("on_hover", "on", main)}
    for name, value in pairs.items():
        if value < MIN_CHANGED:
            failed.append(f"{name}: {value:.1%} of the box changes")
    errors = []
    for name in ("blue", "red", "green"):
        hue = hue_of(pixels(option, themes[name], Frame(ON))[0])
        accent = colorsys.rgb_to_hsv(*(int(ACCENTS[name][i:i + 2], 16) / 255 for i in (0, 2, 4)))[0]
        if hue is None:
            failed.append(f"{name}: no hue")
            continue
        error = abs((hue - accent + 0.5) % 1 - 0.5) * 360
        errors.append(error)
        if error > HUE_LIMIT:
            failed.append(f"{name}: hue {error:.0f} degrees off the theme")
    if hue_of(pixels(option, themes["grey"], Frame(ON))[0]) is not None:
        failed.append("a tint of its own on the grey theme")
    return {"peak": round(peak * 255), "step": step, "global": pairs["global/on"], "hue": max(errors, default=0), "narrowest": narrowest(option)}, failed


def role_contrasts(themes: dict[str, Theme]) -> dict[str, float]:
    return {f"{name} {'on' if lit else 'off'}{' hover' if hot else ''}": contrast(*toggle_roles(t, lit, hot))
            for name, t in themes.items() for lit in (False, True) for hot in (False, True)}


def main() -> int:
    themes = {name: Theme(json.loads(subprocess.run([str(BINARY), "info", hex_], capture_output=True, text=True, check=True).stdout)["scheme"])
              for name, hex_ in ACCENTS.items()}
    bad = False
    print(f"{'option':<18} {'peak':>4} {'on-off':>7} {'global':>7} {'hue err':>8} {'min width':>10}")
    for option in OPTIONS:
        row, failed = measure(option, themes)
        print(f"{option.number:02d} {option.name:<15} {row['peak']:>4} {row['step']:>7.2f} {row['global']:>7.0%} {row['hue']:>7.1f}d {row['narrowest']:>9}px")
        for why in failed:
            print(f"   FAIL {why}")
            bad = True
    worst = min(role_contrasts(themes).items(), key=lambda kv: kv[1])
    print(f"weakest label contrast: {worst[1]:.1f}:1 ({worst[0]})")
    if worst[1] < MIN_CONTRAST:
        print("   FAIL under 3:1")
        bad = True
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
