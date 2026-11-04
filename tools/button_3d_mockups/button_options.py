"""The 3D button designs: each draws one desktop's button beside its level bar, in tile px.

An option draws on a handle_mockups Sketch: tile coordinates (origin at the top-left corner, y down), colours only from
`s.t`, the M3 scheme of the theme colour from glass_theme_scheme(), the call that colours the pills and the bar. Geometry
starts from the real bar (free_pointer.level_bar_rect), and every length is times the bar's scale, so a button shrinks with
its bar on a narrow desktop and follows it when the level bar's constants change.

A toggle's colours come from M3: a quiet tonal container when off, the filled primary when on. The bar's own idle fill
(primary container) is deliberately not the off look: beside the bar it could not be told from on. primary_bright, the
bar's dragged look, is kept for hovering a lit button: it is nearly white, so as the resting on look it loses the theme.

check_button_options.py measures these drawings: where they sit against the hit zones, the OLED cap, the theme hue, and
how far apart off, on and global are.
"""
from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import cairo

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "handle_mockups")]
from handle_options import FONT, Hot, Rgb, Sketch, Theme, mix  # noqa: E402,F401
from native_spike import free_pointer  # noqa: E402

Rect = tuple[float, float, float, float]
OFF, ON, GLOBAL = "off", "on", "global"
STATES = (OFF, ON, GLOBAL)
BAR_GAP = 8.0  # bar to a separate button, as much as a corner cap stands off its edges


@dataclass(frozen=True)
class Frame:
    """What one desktop's button shows in one frame."""
    state: str = OFF
    hover: str = ""  # the control under the dot: "3d", "all" or "" for none

    @property
    def lit(self) -> bool:
        return self.state != OFF


class Geo:
    """The real bar on a tile of this size, and the scale everything else takes."""

    def __init__(self, width: float, height: float):
        self.tile = (0.0, 0.0, width, height)
        self.x, self.y, self.w, self.h = free_pointer.level_bar_rect(self.tile)
        self.k = free_pointer.level_bar_scale(self.tile)
        self.cy = self.y + self.h / 2
        self.right = self.x + self.w
        self.pad = free_pointer.LEVEL_BAR_PAD * self.k

    def beside(self, width: float, gap: float = BAR_GAP) -> Rect:
        return (self.right + gap * self.k, self.y, width * self.k, self.h)

    def before(self, width: float, gap: float = BAR_GAP) -> Rect:
        return (self.x - (gap + width) * self.k, self.y, width * self.k, self.h)


def tile_geo(s: Sketch) -> Geo:
    return Geo(s.W, s.H)


def colours(s: Sketch, f: Frame, hover: bool | None = None) -> tuple[Rgb, Rgb]:
    """(container, content) of a toggle."""
    return toggle_roles(s.t, f.lit, bool(f.hover) if hover is None else hover)


def toggle_roles(t: Theme, lit: bool, hot: bool) -> tuple[Rgb, Rgb]:
    """Off is tonal, the bar's own primary container under the dot (M3's 8% state layer is lost at this size: the check
    measured no visible change); on is the primary, the primary bright under the dot."""
    if lit:
        return (t.primary_bright if hot else t.primary), t.on_primary
    return (t.primary_container, t.on_primary_container) if hot else (t.secondary_container, t.on_secondary_container)


def text(s: Sketch, words: str, cx: float, cy: float, size: float, color: Rgb, alpha: float = 1) -> None:
    """Bold, centred on its ink."""
    cr = s.cr
    cr.select_font_face(FONT, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
    cr.set_font_size(size)
    e = cr.text_extents(words)
    cr.set_source_rgba(*color, alpha)
    cr.move_to(cx - e.width / 2 - e.x_bearing, cy - e.height / 2 - e.y_bearing)
    cr.show_text(words)


def segment(s: Sketch, r: Rect, left: float, right: float, color: Rgb, alpha: float = 1) -> None:
    """A rect with its own corner radius per side: M3's connected button group."""
    x, y, w, h = r
    cr = s.cr
    cr.set_source_rgba(*color, alpha)
    cr.new_path()
    cr.arc(x + w - right, y + right, right, -math.pi / 2, 0)
    cr.arc(x + w - right, y + h - right, right, 0, math.pi / 2)
    cr.arc(x + left, y + h - left, left, math.pi / 2, math.pi)
    cr.arc(x + left, y + left, left, math.pi, 1.5 * math.pi)
    cr.close_path()
    cr.fill()


def round_box(s: Sketch, x: float, y: float, w: float, h: float, r: float, color: Rgb, alpha: float = 1, line: float = 0) -> None:
    cr = s.cr
    cr.set_source_rgba(*color, alpha)
    cr.new_path()
    s.round_rect(cr, x, y, w, h, r)
    if line:
        cr.set_line_width(line); cr.stroke()
    else:
        cr.fill()


def halo_ring(s: Sketch, r: Rect, gap: float, line: float, color: Rgb) -> None:
    x, y, w, h = r
    round_box(s, x - gap, y - gap, w + 2 * gap, h + 2 * gap, h / 2 + gap, color, line=line)


def desk_ticks(s: Sketch, cx: float, cy: float, k: float, color: Rgb) -> None:
    """Three small desktops in a row: 'all of them'. It reads at this size where the word would crowd."""
    w, h, gap = 4.2 * k, 9 * k, 1.8 * k
    x = cx - 1.5 * w - gap
    for i in range(3):
        round_box(s, x + i * (w + gap), cy - h / 2, w, h, 1.1 * k, color)


def cube(s: Sketch, cx: float, cy: float, r: float, line: float, color: Rgb, filled: bool) -> None:
    """An isometric cube, M3's view_in_ar without the corners."""
    dx, dy = r * 0.866, r * 0.5
    top, right_up, right_down, bottom, left_down, left_up = ((cx, cy - r), (cx + dx, cy - dy), (cx + dx, cy + dy), (cx, cy + r),
                                                             (cx - dx, cy + dy), (cx - dx, cy - dy))
    if filled:
        cr = s.cr
        cr.set_source_rgba(*color, 0.35)
        cr.new_path(); cr.move_to(*top); cr.line_to(*right_up); cr.line_to(cx, cy); cr.line_to(*left_up); cr.close_path(); cr.fill()
    s.line([top, right_up, right_down, bottom, left_down, left_up, top], line, color)
    s.line([left_up, (cx, cy), right_up], line, color)
    s.line([(cx, cy), bottom], line, color)


# --- The options ---------------------------------------------------------------------------------------------------------
def twin_pill(s: Sketch, f: Frame) -> None:
    g = tile_geo(s)
    r = g.beside(44)
    fill, ink = colours(s, f)
    s.capsule(*r, fill)
    text(s, "3D", r[0] + r[2] / 2, g.cy, 14.5 * g.k, ink)
    if f.state == GLOBAL:  # an M3 small badge, set off from the capsule by a ring of the scheme's surface
        bx, by = r[0] + r[2] - 3 * g.k, r[1] + 3 * g.k
        s.disc(bx, by, 6.6 * g.k, s.t.surface)
        s.disc(bx, by, 4.6 * g.k, s.t.on_surface)


def connected(s: Sketch, f: Frame) -> None:
    g = tile_geo(s)
    gap, inner = 2 * g.k, 4 * g.k
    main = (g.right + gap, g.y, 40 * g.k, g.h)
    tail = (main[0] + main[2] + gap, g.y, 28 * g.k, g.h)
    fill, ink = colours(s, f, f.hover == "3d")
    segment(s, main, inner, inner, fill)
    text(s, "3D", main[0] + main[2] / 2, g.cy, 14.5 * g.k, ink)
    every = f.state == GLOBAL
    fill, ink = colours(s, Frame(GLOBAL if every else OFF), f.hover == "all")
    segment(s, tail, inner, g.h / 2, fill)
    desk_ticks(s, tail[0] + tail[2] / 2 - 1 * g.k, g.cy, g.k, ink)


def cube_orb(s: Sketch, f: Frame) -> None:
    g = tile_geo(s)
    r = g.beside(g.h / g.k)
    cx, rad = r[0] + r[2] / 2, g.h / 2
    fill, ink = colours(s, f)
    s.disc(cx, g.cy, rad, fill)
    cube(s, cx, g.cy, 8.2 * g.k, 1.5 * g.k, ink, f.lit)
    if f.state == GLOBAL:
        s.ring(cx, g.cy, rad + 3.5 * g.k, 2 * g.k, s.t.primary)


def parallax_sheets(s: Sketch, f: Frame) -> None:
    """Two screens, left and right eye. At rest they are one; on, they part: the button shows what the filter does."""
    g = tile_geo(s)
    r = g.beside(46)
    cx = r[0] + r[2] / 2
    t = s.t
    hot = bool(f.hover)
    if f.lit:
        s.capsule(*r, t.primary_bright if hot else t.primary)
    else:
        round_box(s, r[0] + g.k, r[1] + g.k, r[2] - 2 * g.k, r[3] - 2 * g.k, r[3] / 2 - g.k, t.primary if hot else t.outline, line=1.6 * g.k)
    w, h = 15 * g.k, 12 * g.k
    split = {OFF: 1.2, ON: 4.2, GLOBAL: 5.6}[f.state] * g.k
    back, front = (t.on_primary, t.on_primary) if f.lit else ((t.primary, t.primary) if hot else (t.on_surface_variant, t.on_surface_variant))
    y = g.cy - h / 2
    round_box(s, cx - w / 2 - split, y - split * 0.3, w, h, 2.2 * g.k, back, 0.45)
    if f.state == GLOBAL:
        round_box(s, cx - w / 2, y, w, h, 2.2 * g.k, back, 0.65)
    round_box(s, cx - w / 2 + split, y + split * 0.3, w, h, 2.2 * g.k, front)


def switch_chip(s: Sketch, f: Frame) -> None:
    """A word and the panel's own M3 switch, shrunk: no one has to learn what lit means."""
    g = tile_geo(s)
    r = g.beside(82)
    t = s.t
    hot = bool(f.hover)
    s.capsule(*r, t.surface_container_highest if not hot else mix(t.surface_container_highest, t.on_surface, 0.32))
    word = "ALL" if f.state == GLOBAL else "3D"
    text(s, word, r[0] + 22 * g.k, g.cy, 13.5 * g.k, t.on_surface if f.lit else t.on_surface_variant)
    tw, th = 36 * g.k, 18 * g.k
    tx, ty = r[0] + r[2] - tw - 4 * g.k, g.cy - th / 2
    if f.lit:
        s.capsule(tx, ty, tw, th, t.primary_bright if hot else t.primary)
        s.disc(tx + tw - th / 2, g.cy, 6.2 * g.k, t.on_primary)
    else:
        s.capsule(tx, ty, tw, th, t.surface_container_highest)
        round_box(s, tx, ty, tw, th, th / 2, t.outline, line=1.5 * g.k)
        s.disc(tx + th / 2, g.cy, 4.2 * g.k, t.outline)


def balanced_pair(s: Sketch, f: Frame) -> None:
    """3D to the right of the bar, ALL to the left: the whole top handle stays centred over the desktop."""
    g = tile_geo(s)
    right, left = g.beside(44), g.before(44)
    fill, ink = colours(s, f, f.hover == "3d")
    s.capsule(*right, fill)
    text(s, "3D", right[0] + right[2] / 2, g.cy, 14.5 * g.k, ink)
    fill, ink = colours(s, Frame(GLOBAL if f.state == GLOBAL else OFF), f.hover == "all")
    s.capsule(*left, fill)
    text(s, "ALL", left[0] + left[2] / 2, g.cy, 12 * g.k, ink)


def strength_pips(s: Sketch, f: Frame) -> None:
    """Each click steps off, low, medium, high: the button is also the strength. Global goes round it as a ring."""
    g = tile_geo(s)
    r = g.beside(66)
    fill, ink = colours(s, f)
    s.capsule(*r, fill)
    text(s, "3D", r[0] + 20 * g.k, g.cy, 14.5 * g.k, ink)
    level = 0 if not f.lit else 2
    x = r[0] + 37 * g.k
    for i in range(3):
        h = (6 + 3.5 * i) * g.k
        s.capsule(x + i * 6.5 * g.k, g.cy - h / 2, 3.4 * g.k, h, ink, 1 if i < level else 0.32)
    if f.state == GLOBAL:
        halo_ring(s, r, 3 * g.k, 2 * g.k, s.t.primary)


def reveal_chip(s: Sketch, f: Frame) -> None:
    """Nearly nothing at rest; the dot opens it into a labelled chip. When on, it stays small but glows."""
    g = tile_geo(s)
    t = s.t
    small = (g.right + BAR_GAP * g.k, g.cy - 8 * g.k, 30 * g.k, 16 * g.k)
    if f.hover:
        words = {OFF: "3D here", ON: "3D on", GLOBAL: "3D all"}[f.state]
        r = g.beside(76)
        fill, ink = colours(s, f)
        s.capsule(*r, fill)
        text(s, words, r[0] + r[2] / 2, g.cy, 13.5 * g.k, ink)
        return
    if f.lit:
        s.glow(t.primary, 5 * g.k, 0.55, lambda cr: (s.round_rect(cr, *small, small[3] / 2), cr.fill()))
        s.capsule(*small, t.primary)
        if f.state == GLOBAL:
            r = g.beside(58)
            s.glow(t.primary, 5 * g.k, 0.55, lambda cr: (s.round_rect(cr, r[0], small[1], r[2], small[3], small[3] / 2), cr.fill()))
            s.capsule(r[0], small[1], r[2], small[3], t.primary)
            text(s, "3D ALL", r[0] + r[2] / 2, g.cy, 11 * g.k, t.on_primary)
        else:
            text(s, "3D", small[0] + small[2] / 2, g.cy, 11 * g.k, t.on_primary)
    else:
        round_box(s, small[0] + 0.7 * g.k, small[1] + 0.7 * g.k, small[2] - 1.4 * g.k, small[3] - 1.4 * g.k, small[3] / 2, t.outline, line=1.4 * g.k)
        text(s, "3D", small[0] + small[2] / 2, g.cy, 10.5 * g.k, t.on_surface_variant)


@dataclass(frozen=True)
class Option:
    number: int
    name: str
    blurb: str
    all_how: str  # how a person turns on every desktop from the overview
    draw: Callable[[Sketch, Frame], None]
    hits: Callable[[Geo], dict[str, Rect]]  # the controls' drawn rects, widest state, by what clicking does


def _one(width: float) -> Callable[[Geo], dict[str, Rect]]:
    return lambda g: {"3d": g.beside(width)}


OPTIONS = (
    Option(1, "Twin Pill", "A second capsule the bar's height, the word 3D on it. Quiet tonal when off, the theme's primary when on. Global adds an M3 badge.",
           "Shift+click, or the key with Shift", twin_pill, _one(44)),
    Option(2, "Connected Tail", "Joined to the bar in M3's connected-button style: small inner corners and a 2 px gap, so the top handle reads as one tool. "
           "A tail with three little desktops is the one-click global.",
           "Click the tail", connected,
           lambda g: {"3d": (g.right + 2 * g.k, g.y, 40 * g.k, g.h), "all": (g.right + 44 * g.k, g.y, 28 * g.k, g.h)}),
    Option(3, "Cube Orb", "A round icon button with the M3 view-in-AR cube and no text. The smallest filled option. Global draws an orbit round it.",
           "Shift+click, or the key with Shift", cube_orb, lambda g: {"3d": g.beside(g.h / g.k)}),
    Option(4, "Parallax Sheets", "An outlined capsule holding two screens, the left and right eye. Off they sit on top of each other; on they part. "
           "Global adds a third.", "Shift+click, or the key with Shift", parallax_sheets, _one(46)),
    Option(5, "Switch Chip", "A word and the panel's M3 switch, shrunk. The plainest to read and the widest. The word becomes ALL when global.",
           "Shift+click, or the key with Shift", switch_chip, _one(82)),
    Option(6, "Balanced Pair", "3D right of the bar, ALL to its left, so the top handle stays centred. Global has its own visible button on every desktop.",
           "Click ALL", balanced_pair, lambda g: {"3d": g.beside(44), "all": g.before(44)}),
    Option(7, "Strength Pips", "Each click steps off, low, medium, high, shown as three rising pips, so the button doubles as the strength control. Global rings it.",
           "Shift+click, or the key with Shift", strength_pips, _one(66)),
    Option(8, "Reveal Chip", "Nearly nothing at rest, a small outline. The dot opens it into a labelled chip; when on it stays small and glows.",
           "Shift+click, or the key with Shift", reveal_chip, _one(76)),
)
