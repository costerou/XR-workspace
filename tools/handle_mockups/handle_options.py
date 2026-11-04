"""The handle design options: each draws one desktop's rotate and resize handles in tile px.

An option is a function of a Sketch. It draws on the tile's own coordinates (origin at its top-left corner, y down, the
tile W x H), only with colours from `s.t`, the M3 scheme handle_preview printed for the theme colour, which is the
glass_theme_scheme() the pills use. So every option follows the Omarchy theme the way the pills do, and every role is
already under the 191/255 OLED cap. `s.hot` says which handles are hovered.

Sideways room is short: tiles sit 40 px apart in the overview, so at rest nothing reaches more than 18 px past a left or
right edge. Black is see-through on the glasses, so a dark plate shows only over a desktop, never over empty space.
check_options.py measures both of those limits and the OLED cap on the drawn pixels.
"""
from __future__ import annotations

import math
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable

import cairo
import numpy as np
from PIL import Image, ImageFilter

FONT = "JetBrainsMono Nerd Font"
OLED_CAP = 191 / 255
GLOW_DENSITY = 2  # px per tile px of the soft layers
Rgb = tuple[float, float, float]


def mix(a: Rgb, b: Rgb, t: float) -> Rgb:
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t)


def deg(d: float) -> float:
    return math.radians(d)


class Theme:
    """The scheme's roles as attributes; `accent` is capped like every other role."""

    def __init__(self, scheme: dict[str, list[float]]):
        for name, value in scheme.items():
            setattr(self, name, tuple(value))
        self.seed: Rgb = tuple(c * OLED_CAP for c in scheme["accent"])  # the theme colour itself, as bright as a role may be


@dataclass(frozen=True)
class Hot:
    rotate: frozenset[int] = frozenset()  # 0 top-left (counter-clockwise), 1 top-right (clockwise)
    resize: frozenset[int] = frozenset()  # 0 top-left, 1 top-right, 2 bottom-left, 3 bottom-right


class Sketch:
    def __init__(self, width: float, height: float, margin: float, density: int, theme: Theme, hot: Hot):
        self.W, self.H, self.margin, self.t, self.hot = width, height, margin, theme, hot
        self.px_w = round((width + 2 * margin) * density)
        self.px_h = round((height + 2 * margin) * density)
        self.sx, self.sy = self.px_w / (width + 2 * margin), self.px_h / (height + 2 * margin)
        self.surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, self.px_w, self.px_h)
        self.cr = cairo.Context(self.surface)
        self._frame(self.cr, self.sx, self.sy)
        self.cr.set_line_cap(cairo.LINE_CAP_ROUND)
        self.cr.set_line_join(cairo.LINE_JOIN_ROUND)
        self.soft_w = math.ceil((width + 2 * margin) * GLOW_DENSITY)
        self.soft_h = math.ceil((height + 2 * margin) * GLOW_DENSITY)
        self.soft_rgb = np.zeros((self.soft_h, self.soft_w, 3), np.float32)  # added light (nothing in front of it)
        self.soft_alpha = np.zeros((self.soft_h, self.soft_w), np.float32)   # shadow

    def _frame(self, cr: cairo.Context, sx: float, sy: float) -> None:
        cr.scale(sx, sy)
        cr.translate(self.margin, self.margin)

    # --- Frames ------------------------------------------------------------------------------------------------------
    @contextmanager
    def corner(self, c: int, cr: cairo.Context | None = None):
        """Origin on tile corner c, +x and +y pointing out of the desktop there: the same drawing fits all four corners."""
        cr = cr or self.cr
        cr.save()
        cr.translate(self.W if c in (1, 3) else 0, self.H if c in (2, 3) else 0)
        cr.scale(1 if c in (1, 3) else -1, 1 if c in (2, 3) else -1)
        yield cr
        cr.restore()

    # --- Soft layers -------------------------------------------------------------------------------------------------
    def _blurred(self, shape: Callable[[cairo.Context], None], sigma: float) -> np.ndarray:
        mask = cairo.ImageSurface(cairo.FORMAT_A8, self.soft_w, self.soft_h)
        cr = cairo.Context(mask)
        self._frame(cr, GLOW_DENSITY, GLOW_DENSITY)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        shape(cr)
        mask.flush()
        image = Image.frombuffer("L", (self.soft_w, self.soft_h), bytes(mask.get_data()), "raw", "L", mask.get_stride(), 1)
        return np.asarray(image.filter(ImageFilter.GaussianBlur(sigma * GLOW_DENSITY)), np.float32) / 255

    def glow(self, color: Rgb, sigma: float, gain: float, shape: Callable[[cairo.Context], None]) -> None:
        """Added light round `shape`, the way gl.cpp's border glow adds to what is behind it."""
        self.soft_rgb += self._blurred(shape, sigma)[..., None] * np.array(color, np.float32) * gain

    def shadow(self, sigma: float, alpha: float, dy: float, shape: Callable[[cairo.Context], None]) -> None:
        def shifted(cr: cairo.Context) -> None:
            cr.save(); cr.translate(0, dy); shape(cr); cr.restore()
        blurred = self._blurred(shifted, sigma) * alpha
        self.soft_alpha = 1 - (1 - self.soft_alpha) * (1 - blurred)

    def finish(self) -> bytes:
        """Premultiplied RGBA, soft layers under the shapes, nothing past the OLED cap."""
        self.surface.flush()
        row = self.surface.get_stride() // 4
        body = np.frombuffer(self.surface.get_data(), np.uint8).reshape(self.px_h, row, 4)[:, :self.px_w]
        body = body[..., [2, 1, 0, 3]].astype(np.float32) / 255  # cairo's little-endian BGRA
        def up(a: np.ndarray) -> np.ndarray:
            return np.asarray(Image.fromarray(a).resize((self.px_w, self.px_h), Image.BICUBIC), np.float32)
        keep = 1 - body[..., 3:]
        for ch in range(3):
            body[..., ch] += up(self.soft_rgb[..., ch]) * keep[..., 0]
        body[..., 3] += up(self.soft_alpha) * keep[..., 0]
        self.peak = float(body[..., :3].max())  # before the clamp, so check_options.py can tell a design that relies on it
        body[..., :3] = np.minimum(body[..., :3], OLED_CAP)
        body[..., 3] = np.clip(body[..., 3], 0, 1)
        return (body * 255 + 0.5).astype(np.uint8).tobytes()

    # --- Shapes ------------------------------------------------------------------------------------------------------
    def disc(self, x: float, y: float, r: float, color: Rgb, alpha: float = 1, cr: cairo.Context | None = None) -> None:
        cr = cr or self.cr
        cr.set_source_rgba(*color, alpha)
        cr.new_path(); cr.arc(x, y, r, 0, math.tau); cr.fill()

    def ring(self, x: float, y: float, r: float, width: float, color: Rgb, alpha: float = 1, cr: cairo.Context | None = None) -> None:
        cr = cr or self.cr
        cr.set_source_rgba(*color, alpha); cr.set_line_width(width)
        cr.new_path(); cr.arc(x, y, r, 0, math.tau); cr.stroke()

    @staticmethod
    def round_rect(cr: cairo.Context, x: float, y: float, w: float, h: float, r: float) -> None:
        cr.new_sub_path()
        cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
        cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
        cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
        cr.arc(x + r, y + r, r, math.pi, 1.5 * math.pi)
        cr.close_path()

    def capsule(self, x: float, y: float, w: float, h: float, color: Rgb, alpha: float = 1, cr: cairo.Context | None = None) -> None:
        cr = cr or self.cr
        cr.set_source_rgba(*color, alpha)
        cr.new_path(); self.round_rect(cr, x, y, w, h, min(w, h) / 2); cr.fill()

    def line(self, points: list[tuple[float, float]], width: float, color: Rgb, alpha: float = 1, cr: cairo.Context | None = None) -> None:
        cr = cr or self.cr
        cr.set_source_rgba(*color, alpha); cr.set_line_width(width)
        cr.new_path(); cr.move_to(*points[0])
        for p in points[1:]:
            cr.line_to(*p)
        cr.stroke()

    def arc(self, r: float, a0: float, a1: float, width: float, color: Rgb, alpha: float = 1, cx: float = 0, cy: float = 0,
            cr: cairo.Context | None = None) -> None:
        cr = cr or self.cr
        cr.set_source_rgba(*color, alpha); cr.set_line_width(width)
        cr.new_path()
        (cr.arc if a1 >= a0 else cr.arc_negative)(cx, cy, r, a0, a1)
        cr.stroke()

    def head(self, x: float, y: float, dx: float, dy: float, length: float, half: float, color: Rgb, alpha: float = 1,
             cr: cairo.Context | None = None) -> None:
        """An arrowhead with its tip at (x, y), pointing along the unit vector (dx, dy); softly rounded like an M3 glyph."""
        cr = cr or self.cr
        bx, by = x - dx * length, y - dy * length
        cr.set_source_rgba(*color, alpha)
        cr.set_line_width(max(0.6, half * 0.35))
        cr.new_path()
        cr.move_to(x, y); cr.line_to(bx - dy * half, by + dx * half); cr.line_to(bx + dy * half, by - dx * half); cr.close_path()
        cr.fill_preserve(); cr.stroke()

    def arc_arrow(self, r: float, a0: float, a1: float, width: float, color: Rgb, alpha: float = 1, cx: float = 0, cy: float = 0,
                  head: float | None = None, both: bool = False, cr: cairo.Context | None = None) -> None:
        """An arc from a0 to a1 with a head at a1 (and at a0 with `both`), the head following the arc."""
        cr = cr or self.cr
        length = head if head is not None else width * 3.2
        half = length * 0.72
        turn = 1 if a1 >= a0 else -1
        room = length * 0.75 / r
        self.arc(r, a0 + (turn * room if both else 0), a1 - turn * room, width, color, alpha, cx, cy, cr)
        for a, t in ((a1, turn),) + (((a0, -turn),) if both else ()):
            tip = a + t * length * 0.25 / r   # the head's middle sits on the arc: aim it along the chord
            self.head(cx + r * math.cos(tip), cy + r * math.sin(tip), -t * math.sin(a), t * math.cos(a), length, half, color, alpha, cr)

    def spin(self, x: float, y: float, r: float, width: float, color: Rgb, clockwise: bool, alpha: float = 1, sweep: float = 285,
             cr: cairo.Context | None = None) -> None:
        """A circular arrow, M3's rotate_right (mirrored: rotate_left)."""
        cr = cr or self.cr
        cr.save(); cr.translate(x, y)
        if not clockwise:
            cr.scale(-1, 1)
        end = deg(-30)
        self.arc_arrow(r, end - deg(sweep), end, width, color, alpha, head=width * 2.5, cr=cr)
        cr.restore()

    def diag(self, x: float, y: float, half: float, width: float, color: Rgb, alpha: float = 1, tip: float | None = None,
             cr: cairo.Context | None = None) -> None:
        """A double-headed diagonal, M3's open_in_full: a shaft from (-half, -half) to (half, half) with L tips, at (x, y)."""
        cr = cr or self.cr
        tip = tip if tip is not None else half * 0.82
        self.line([(x - half, y - half), (x + half, y + half)], width, color, alpha, cr)
        self.line([(x + half - tip, y + half), (x + half, y + half), (x + half, y + half - tip)], width, color, alpha, cr)
        self.line([(x - half + tip, y - half), (x - half, y - half), (x - half, y - half + tip)], width, color, alpha, cr)

    def l_mark(self, gap: float, leg: float, bend: float, width: float, color: Rgb, cr: cairo.Context, alpha: float = 1) -> None:
        """In a corner frame: an L round the corner, `gap` outside the edges, its legs `leg` long, its elbow rounded."""
        cr.set_source_rgba(*color, alpha); cr.set_line_width(width)
        cr.new_path(); cr.move_to(gap - leg, gap)
        cr.arc_negative(gap - bend, gap - bend, bend, deg(90), deg(0))
        cr.line_to(gap, gap - leg)
        cr.stroke()

    def chevron(self, x: float, y: float, size: float, angle: float, width: float, color: Rgb, alpha: float = 1,
                cr: cairo.Context | None = None) -> None:
        """A chevron pointing along `angle`, its tip at (x, y)."""
        cr = cr or self.cr
        back = [(x - size * math.cos(angle + s * deg(45)), y - size * math.sin(angle + s * deg(45))) for s in (-1, 1)]
        self.line([back[0], (x, y), back[1]], width, color, alpha, cr)

    def text(self, x: float, y: float, value: str, size: float, color: Rgb, bold: bool = True, anchor: str = "centre") -> float:
        """Text with its vertical middle at y; returns its width."""
        cr = self.cr
        cr.select_font_face(FONT, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD if bold else cairo.FONT_WEIGHT_NORMAL)
        cr.set_font_size(size)
        box = cr.text_extents(value)
        left = x - box.x_advance / 2 if anchor == "centre" else x
        cr.set_source_rgb(*color)
        cr.move_to(left, y + size * 0.36)
        cr.show_text(value)
        cr.new_path()
        return box.x_advance


# --- 1 ---------------------------------------------------------------------------------------------------------------
def tonal_buttons(s: Sketch) -> None:
    t = s.t
    for side in (0, 1):
        hot = side in s.hot.rotate
        x, y, r = (46 if side == 0 else s.W - 46), -27, 19 if hot else 15.5
        if hot:
            s.glow(t.primary, 7, 0.5, lambda cr: s.disc(x, y, r + 2, (1, 1, 1), cr=cr))
            s.ring(x, y, r + 4, 1.4, t.primary, 0.8)
        s.disc(x, y, r, mix(t.primary_container, t.on_primary_container, 0.14 if hot else 0))
        s.spin(x, y, r * 0.53, 2.2, t.on_primary_container, clockwise=side == 1)
    for c in range(4):
        hot = c in s.hot.resize
        with s.corner(c) as cr:
            r, at = (11, 10.5) if hot else (8.5, 7.5)
            if hot:
                s.ring(at, at, r + 2.6, 1.2, t.primary, 0.8, cr)
            s.disc(at, at, r, mix(t.primary_container, t.on_primary_container, 0.14 if hot else 0), cr=cr)
            s.diag(at, at, r * 0.4, 1.5, t.on_primary_container, cr=cr)


# --- 2 ---------------------------------------------------------------------------------------------------------------
def hairline_corners(s: Sketch) -> None:
    t = s.t
    gap, bend = 9.0, 4.0
    for c in range(4):
        hot = c in s.hot.resize
        leg, width, colour = (32, 2.3, t.primary_bright) if hot else (20, 1.5, t.primary)
        with s.corner(c) as cr:
            s.l_mark(gap, leg, bend, width, colour, cr)
        if hot:
            s.glow(t.primary, 4, 0.35, lambda m, c=c, leg=leg: _with_corner(s, c, m, lambda f: s.l_mark(gap, leg, bend, 3, (1, 1, 1), f)))
    for side in (0, 1):
        hot = side in s.hot.rotate
        with s.corner(side) as cr:
            # A small arch over the top leg, coming down toward the corner: the way the corner goes when the desktop turns.
            if hot:
                s.arc_arrow(14, deg(178), deg(12), 2.3, t.primary_bright, cx=-24, cy=20, head=7.5, cr=cr)
            else:
                s.arc_arrow(10, deg(172), deg(10), 1.5, t.primary, cx=-20, cy=19, head=5.5, cr=cr)


def _with_corner(s: Sketch, c: int, cr: cairo.Context, draw: Callable[[cairo.Context], None]) -> None:
    with s.corner(c, cr) as frame:
        draw(frame)


# --- 3 ---------------------------------------------------------------------------------------------------------------
def icon_chips(s: Sketch) -> None:
    t = s.t
    for side in (0, 1):
        hot = side in s.hot.rotate
        w, h = (92, 26) if hot else (56, 22)
        x = 40 if side == 0 else s.W - 40 - w
        y = -8 - h
        fill, ink = (t.primary, t.on_primary) if hot else (t.primary_container, t.on_primary_container)
        s.shadow(4, 0.35, 2, lambda cr: s.capsule(x, y, w, h, (0, 0, 0), cr=cr))
        s.capsule(x, y, w, h, fill)
        if hot:
            s.glow(t.primary, 6, 0.32, lambda cr: s.capsule(x, y, w, h, (1, 1, 1), cr=cr))
            s.spin(x + 16, y + h / 2, 6.5, 1.9, ink, clockwise=side == 1)
            s.text(x + 28, y + h / 2, "Rotate", 12, ink, anchor="left")
        else:
            s.spin(x + 15, y + h / 2, 5.6, 1.7, ink, clockwise=side == 1)
            s.text(x + 26, y + h / 2, "2\u00b0", 11.5, ink, anchor="left")
    for c in range(4):
        hot = c in s.hot.resize
        with s.corner(c) as cr:
            length, thick, gap = (34, 9, 12) if hot else (24, 7, 11)
            fill = t.primary_bright if hot else t.primary_container
            s.capsule(-length + 2, gap - thick / 2, length, thick, fill, cr=cr)
            s.capsule(gap - thick / 2, -length + 2, thick, length, fill, cr=cr)
            if hot:
                s.disc(gap + 1, gap + 1, 3, fill, cr=cr)


# --- 4 ---------------------------------------------------------------------------------------------------------------
def orbit_knobs(s: Sketch) -> None:
    t = s.t
    orbit, bead = 44.0, deg(122)
    for side in (0, 1):
        hot = side in s.hot.rotate
        with s.corner(side) as cr:
            if hot:
                s.arc_arrow(orbit, deg(80), deg(164), 2.0, t.primary_bright, head=8, both=True, cr=cr)
                x, y = orbit * math.cos(bead), orbit * math.sin(bead)
                s.ring(x, y, 12.5, 1.3, t.primary, 0.75, cr)
                s.disc(x, y, 8.5, t.primary_bright, cr=cr)
            else:
                cr.set_dash([0.01, 4.4])
                s.arc(orbit, deg(86), deg(158), 2.2, t.on_surface_variant, cr=cr)
                cr.set_dash([])
                s.disc(orbit * math.cos(bead), orbit * math.sin(bead), 6, t.primary, cr=cr)
    for c in range(4):
        hot = c in s.hot.resize
        with s.corner(c) as cr:
            at = 9.0
            if hot:
                s.ring(at, at, 11.5, 1.3, t.primary, 0.75, cr)
                s.disc(at, at, 7.5, t.primary_bright, cr=cr)
            else:
                s.ring(at, at, 7.2, 1.0, t.outline_variant, cr=cr)
                s.disc(at, at, 4.2, t.primary, cr=cr)


# --- 5 ---------------------------------------------------------------------------------------------------------------
def _streak(cr: cairo.Context, r: float, a0: float, a1: float, width: float, colour: Rgb, alpha: float = 1) -> None:
    """A stroke of light along an arc, fading out at both ends."""
    steps = 48
    cr.set_line_width(width)
    for i in range(steps):
        u0, u1 = i / steps, (i + 1.15) / steps
        fade = math.sin(math.pi * (i + 0.5) / steps) ** 0.8
        cr.set_source_rgba(*colour, alpha * fade)
        cr.new_path()
        (cr.arc if a1 >= a0 else cr.arc_negative)(0, 0, r, a0 + (a1 - a0) * u0, a0 + (a1 - a0) * min(u1, 1))
        cr.stroke()


def glow_nubs(s: Sketch) -> None:
    t = s.t
    for c in range(4):
        hot = c in s.hot.resize
        r, a0, a1, w, gain = (16.5, -2, 92, 3.6, 1.0) if hot else (15.0, 12, 78, 2.6, 0.55)
        colour = t.primary_bright if hot else t.seed
        def light(cr: cairo.Context, c: int = c, r: float = r, a0: float = a0, a1: float = a1) -> None:
            _with_corner(s, c, cr, lambda f: s.arc(r, deg(a0), deg(a1), 5, (1, 1, 1), cr=f))
        s.glow(t.seed, 5.5, gain, light)
        with s.corner(c) as cr:
            _streak(cr, r, deg(a0), deg(a1), w, colour)
    for side in (0, 1):
        hot = side in s.hot.rotate
        # A long shallow streak above the top edge, curving the way the desktop turns; it points on hover.
        length, y, w, gain = (120, -15, 3.6, 1.1) if hot else (74, -13, 2.6, 0.6)
        radius = 420.0
        mid = 88 if side == 0 else s.W - 88
        half = length / 2 / radius
        centre_y = y + radius
        def path(cr: cairo.Context, mid: float = mid, half: float = half, centre_y: float = centre_y) -> None:
            cr.set_line_width(5); cr.new_path()
            cr.arc(mid, centre_y, radius, -math.pi / 2 - half, -math.pi / 2 + half); cr.stroke()
        s.glow(t.seed, 5.5, gain, path)
        cr = s.cr
        cr.save(); cr.translate(mid, centre_y)
        _streak(cr, radius, -math.pi / 2 - half, -math.pi / 2 + half, w, t.primary_bright if hot else t.seed)
        if hot:
            turn = 1 if side == 1 else -1
            a = -math.pi / 2 + turn * (half + 10 / radius)
            s.chevron(radius * math.cos(a), radius * math.sin(a), 8, 0 if side == 1 else math.pi, 2.2, t.primary_bright, cr=cr)
        cr.restore()


# --- 6 ---------------------------------------------------------------------------------------------------------------
def soft_arcs(s: Sketch) -> None:
    t = s.t
    for c in range(4):
        hot = c in s.hot.resize
        leg, width, colour, alpha = (46, 8.5, t.primary_bright, 0.96) if hot else (32, 6.5, t.primary, 0.72)
        with s.corner(c) as cr:
            s.l_mark(11, leg, 18, width, colour, cr, alpha)
        if hot:
            s.glow(t.primary, 6, 0.4, lambda m, c=c, leg=leg, width=width: _with_corner(s, c, m, lambda f: s.l_mark(11, leg, 18, width + 2, (1, 1, 1), f)))
    bow = 190.0  # the bars curve like a piece of a big circle, the hint that they turn
    for side in (0, 1):
        hot = side in s.hot.rotate
        half, y, width = (40 / bow, -24, 10) if hot else (26 / bow, -19, 6.5)
        mid = 96 if side == 0 else s.W - 96
        tilt = (-0.09 if side == 0 else 0.09)
        colour, alpha = (t.primary_bright, 0.96) if hot else (t.primary, 0.72)
        def bar(cr: cairo.Context, colour: Rgb = colour, alpha: float = alpha, mid: float = mid, y: float = y, half: float = half, width: float = width, tilt: float = tilt) -> None:
            s.arc(bow, -math.pi / 2 + tilt - half, -math.pi / 2 + tilt + half, width, colour, alpha, cx=mid - bow * math.sin(tilt), cy=y + bow * math.cos(tilt), cr=cr)
        if hot:
            s.glow(t.primary, 6, 0.4, lambda cr: bar(cr, (1, 1, 1), 1, width=width + 2))
        bar(s.cr)
        if hot:
            turn = 1 if side == 1 else -1
            a = -math.pi / 2 + tilt + turn * (half + 15 / bow)
            cx, cy = mid - bow * math.sin(tilt), y + bow * math.cos(tilt)
            s.chevron(cx + bow * math.cos(a), cy + bow * math.sin(a), 8, a + turn * math.pi / 2, 3, t.primary_bright, 0.96)


# --- 7 ---------------------------------------------------------------------------------------------------------------
def ghost_hover(s: Sketch) -> None:
    t = s.t
    for c in range(4):
        hot = c in s.hot.resize
        with s.corner(c) as cr:
            if not hot:
                s.disc(9, 9, 1.6, t.outline, 0.9, cr)
                continue
            s.disc(12, 12, 16, t.surface_container_highest, 0.9, cr)
            s.ring(12, 12, 16, 1.0, t.outline_variant, cr=cr)
            s.diag(12, 12, 6.5, 1.9, t.primary_bright, cr=cr)
    for side in (0, 1):
        hot = side in s.hot.rotate
        x, y = (s.W * 0.11 if side == 0 else s.W * 0.89), -24
        if not hot:
            continue
        s.glow(t.primary, 8, 0.25, lambda cr: s.disc(x, y, 18, (1, 1, 1), cr=cr))
        s.disc(x, y, 18, t.surface_container_highest, 0.9)
        s.ring(x, y, 18, 1.0, t.outline_variant)
        s.spin(x, y, 9, 2.3, t.primary_bright, clockwise=side == 1)


# --- 8 ---------------------------------------------------------------------------------------------------------------
def dial_ticks(s: Sketch) -> None:
    t = s.t
    centre_r = 36.0
    for side in (0, 1):
        hot = side in s.hot.rotate
        with s.corner(side) as cr:
            first, last, step = (78, 174, 6) if hot else (84, 168, 7)
            count = round((last - first) / step)
            for i in range(count + 1):
                a = deg(first + i * step)
                major = i % 3 == 0
                long = (9 if major else 5) * (1.35 if hot else 1)
                colour = t.primary if hot else (t.on_surface_variant if major else t.outline)
                s.line([(centre_r * math.cos(a), centre_r * math.sin(a)),
                        ((centre_r + long) * math.cos(a), (centre_r + long) * math.sin(a))], 1.8 if hot else 1.4, colour, cr=cr)
            pointer = deg(126)
            tip_r = centre_r + (14 if hot else 11)
            x, y = tip_r * math.cos(pointer), tip_r * math.sin(pointer)
            s.head(x, y, -math.cos(pointer), -math.sin(pointer), 7.5 if hot else 5.5, 4.6 if hot else 3.3, t.primary_bright if hot else t.primary, cr=cr)
        if hot:
            s.text(s.W - 92 if side == 1 else 92, -28, "0.0\u00b0", 13, t.primary_bright)
    for c in range(4):
        hot = c in s.hot.resize
        with s.corner(c) as cr:
            colour = t.primary_bright if hot else t.on_surface_variant
            width = 2.0 if hot else 1.4
            for k, gap in enumerate((9.0, 14.0) if not hot else (9.0, 15.0, 21.0)):
                leg = 8.0 if not hot else 11.0
                s.line([(gap - leg, gap), (gap, gap), (gap, gap - leg)], width, colour, 1 - k * 0.3, cr)


# --- 9 ---------------------------------------------------------------------------------------------------------------
def _tab(cr: cairo.Context, x0: float, x1: float, height: float, radius: float, flare: float) -> None:
    """A folder tab standing on y = 0 between x0 and x1, its foot flared into the edge."""
    cr.new_path()
    cr.move_to(x0 - flare, 0)
    cr.curve_to(x0 - flare * 0.35, 0, x0, -flare * 0.35, x0, -flare)
    cr.line_to(x0, -height + radius)
    cr.arc(x0 + radius, -height + radius, radius, math.pi, 1.5 * math.pi)
    cr.line_to(x1 - radius, -height)
    cr.arc(x1 - radius, -height + radius, radius, 1.5 * math.pi, 0)
    cr.line_to(x1, -flare)
    cr.curve_to(x1, -flare * 0.35, x1 + flare * 0.35, 0, x1 + flare, 0)
    cr.close_path()


def docked_tabs(s: Sketch) -> None:
    t = s.t
    for side in (0, 1):
        hot = side in s.hot.rotate
        w, h = (62, 30) if hot else (50, 24)
        x = 24 if side == 0 else s.W - 24 - w
        fill, ink = (t.primary, t.on_primary) if hot else (t.primary_container, t.on_primary_container)
        if hot:
            s.glow(t.primary, 6, 0.32, lambda cr: (_tab(cr, x, x + w, h, 9, 6), cr.fill()))
        cr = s.cr
        cr.set_source_rgb(*fill)
        _tab(cr, x, x + w, h, 9, 6)
        cr.fill()
        s.spin(x + w / 2, -h / 2 - 0.5, 7.2 if hot else 6, 2.1 if hot else 1.8, ink, clockwise=side == 1)
    for c in range(4):
        hot = c in s.hot.resize
        with s.corner(c) as cr:
            r = 24.0 if hot else 18.0
            fill, ink = (t.primary, t.on_primary) if hot else (t.primary_container, t.on_primary_container)
            cr.set_source_rgb(*fill)
            cr.new_path(); cr.move_to(0, 0); cr.line_to(r, 0); cr.arc(0, 0, r, 0, math.pi / 2); cr.close_path(); cr.fill()
            at = r * 0.47
            s.diag(at, at, r * 0.2, 1.5 if hot else 1.35, ink, tip=r * 0.2, cr=cr)


# --- 10 --------------------------------------------------------------------------------------------------------------
def level_bar(s: Sketch) -> None:
    t = s.t
    w, h = 148, 26
    x, y = (s.W - w) / 2, -12 - h
    s.shadow(4, 0.35, 2, lambda cr: s.capsule(x, y, w, h, (0, 0, 0), cr=cr))
    s.capsule(x, y, w, h, t.primary_container)
    third = w / 3
    if 1 in s.hot.rotate:
        s.glow(t.primary, 6, 0.32, lambda cr: s.capsule(x + 2 * third - 1, y, third + 1, h, (1, 1, 1), cr=cr))
        s.capsule(x + 2 * third - 1, y + 2, third - 1, h - 4, t.primary)
    if 0 in s.hot.rotate:
        s.capsule(x + 2, y + 2, third - 1, h - 4, t.primary)
    for k in (1, 2):
        s.line([(x + k * third, y + 7), (x + k * third, y + h - 7)], 1.0, t.on_primary_container, 0.3)
    mid = y + h / 2
    s.spin(x + third / 2, mid, 6.2, 1.9, t.on_primary if 0 in s.hot.rotate else t.on_primary_container, clockwise=False)
    s.spin(x + 2.5 * third, mid, 6.2, 1.9, t.on_primary if 1 in s.hot.rotate else t.on_primary_container, clockwise=True)
    s.line([(x + 1.5 * third - 11, mid), (x + 1.5 * third - 4, mid)], 1.8, t.on_primary_container)
    s.line([(x + 1.5 * third + 4, mid), (x + 1.5 * third + 11, mid)], 1.8, t.on_primary_container)
    s.disc(x + 1.5 * third, mid, 2.2, t.on_primary_container)
    for c in range(4):
        hot = c in s.hot.resize
        with s.corner(c) as cr:
            leg, width, colour = (22.0, 3.2, t.primary_bright) if hot else (12.0, 2.4, t.primary)
            s.l_mark(9.0, leg, 5.0, width, colour, cr)


@dataclass(frozen=True)
class Option:
    number: int
    name: str
    blurb: str
    draw: Callable[[Sketch], None]
    rotate_at: Callable[[float, float], tuple[float, float]]   # the hot rotate handle, tile px: the dot sits here
    resize_at: Callable[[float, float], tuple[float, float]]   # the hot resize handle, tile px
    rotate_view: Callable[[float], float] | None = None        # the rotate detail view's centre x, when not at the corner


# rotate_at / resize_at: the dot's tile px on the hot handle, at its edge so the hover state stays visible.
OPTIONS = [
    Option(1, "Tonal Buttons", "Round M3 tonal icon buttons: rotate above each top corner, a small resize button on every corner.",
           tonal_buttons, lambda w, h: (w - 24, -38), lambda w, h: (w + 25, h + 26)),
    Option(2, "Hairline Corners", "Thin L-marks on all four corners with a small curved arrow over each top one; almost no ink.",
           hairline_corners, lambda w, h: (w - 32, -44), lambda w, h: (w + 18, h + 18)),
    Option(3, "Icon Chips", "Small capsules in the pill's own language: rotate chips that spell out on hover, short capsules along the corners.",
           icon_chips, lambda w, h: (w - 34, -34), lambda w, h: (w + 18, h + 18)),
    Option(4, "Orbit Knobs", "A dot knob on every corner; a bead on a dotted orbit over each top corner, the orbit turning solid on hover.",
           orbit_knobs, lambda w, h: (w - 45, -50), lambda w, h: (w + 24, h + 24)),
    Option(5, "Glow Nubs", "Streaks of the theme's light round the corners and above the top edge, blooming on hover.",
           glow_nubs, lambda w, h: (w - 88, -30), lambda w, h: (w + 21, h + 21)),
    Option(6, "Soft Arcs", "visionOS-style frosted corners and curved grab bars above the top, with the pill as the window bar.",
           soft_arcs, lambda w, h: (w - 96, -44), lambda w, h: (w + 20, h + 20)),
    Option(7, "Ghost Hover", "Nothing but four faint specks until the dot arrives; then a frosted button fades in under it.",
           ghost_hover, lambda w, h: (w * 0.89 + 20, -38), lambda w, h: (w + 28, h + 29)),
    Option(8, "Dial Ticks", "A fine instrument scale over each top corner with a pointer, crop-mark chevrons on the corners, the angle on hover.",
           dial_ticks, lambda w, h: (w - 10, -58), lambda w, h: (w + 30, h + 31)),
    Option(9, "Docked Tabs", "Folder tabs growing out of the top edge and solid ears on the corners: part of the frame, not floating on it.",
           docked_tabs, lambda w, h: (w - 24, -36), lambda w, h: (w + 30, h + 30)),
    Option(10, "Level Bar", "One segmented capsule above the top edge, left turn, level, right turn, mirroring the pill below; quiet corner caps.",
           level_bar, lambda w, h: (w / 2 + 74, -46), lambda w, h: (w + 18, h + 18), rotate_view=lambda w: w / 2),
]
