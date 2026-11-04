"""The text cursor design options: each is a flat shape in canvas px round the cursor's hot spot (0, 0), y down.

Every option is built the way gl.cpp draws the dot and its marks: a black outline RING px wider than the shape, then the
shape in flat grey on top, with no anti-aliasing. So a design has the dot's grey, its ring and its hard edge, and only
needs triangles to reach the renderer. Stems are a whole number of px wide, because a width like 2.5 would flip between
2 and 3 px as the head moves (check_cursor_options.py measures that). The hot spot is the click point, so it is where the
dot's centre is, and every shape is centred on it.

The dot itself has no theme colour: it is neutral grey at 170/255, 184 while held, in the renderer. Only the Glow Bar takes
a colour from the M3 scheme, for its halo, and every role is under the 191/255 OLED cap.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

# view.hpp, compared with `cursor_preview info` by check_cursor_options.py.
LEVEL, HELD = 170 / 255, 184 / 255
RADIUS, RING = 6.0, 2.0
SEGMENTS = 20
MARK_GAP, MARK_LEN, MARK_HALF = 2.5, 5.0, 3.5
OLED_CAP = 191 / 255

RING_GLOW_START = 2  # a glow starts under the black ring

Point = tuple[float, float]
Rgb = tuple[float, float, float]


# --- Primitives: triangles(grow) gives this shape fattened by `grow` px, for the black outline drawn under it ---------------
class Prim:
    def triangles(self, grow: float) -> list[Point]:
        raise NotImplementedError


def fan(points: Sequence[Point]) -> list[Point]:
    centre = (sum(p[0] for p in points) / len(points), sum(p[1] for p in points) / len(points))
    out: list[Point] = []
    for a, b in zip(points, [*points[1:], points[0]]):
        out += [centre, a, b]
    return out


def capsule_points(x: float, y0: float, y1: float, r: float, segments: int = SEGMENTS) -> list[Point]:
    """Vertical capsule round the segment (x, y0)-(x, y1), closed loop, the same point count for any size."""
    half = segments // 2
    top = [(x + r * math.cos(math.pi + math.pi * i / half), y0 + r * math.sin(math.pi + math.pi * i / half)) for i in range(half + 1)]
    bottom = [(x + r * math.cos(math.pi * i / half), y1 + r * math.sin(math.pi * i / half)) for i in range(half + 1)]
    return top + bottom


@dataclass(frozen=True)
class Disc(Prim):
    x: float
    y: float
    r: float

    def triangles(self, grow: float) -> list[Point]:
        r = self.r + grow
        return fan([(self.x + r * math.cos(math.tau * i / SEGMENTS), self.y + r * math.sin(math.tau * i / SEGMENTS)) for i in range(SEGMENTS)])


@dataclass(frozen=True)
class Box(Prim):
    x0: float
    y0: float
    x1: float
    y1: float

    def triangles(self, grow: float) -> list[Point]:
        a, b, c, d = self.x0 - grow, self.y0 - grow, self.x1 + grow, self.y1 + grow
        return [(a, b), (c, b), (c, d), (a, b), (c, d), (a, d)]


@dataclass(frozen=True)
class VCapsule(Prim):
    x: float
    y0: float
    y1: float
    r: float

    def triangles(self, grow: float) -> list[Point]:
        return fan(capsule_points(self.x, self.y0, self.y1, self.r + grow))


@dataclass(frozen=True)
class HCapsule(Prim):
    x0: float
    x1: float
    y: float
    r: float

    def triangles(self, grow: float) -> list[Point]:
        half, middle = (self.x1 - self.x0) / 2, (self.x0 + self.x1) / 2
        return [(middle + y, self.y + x) for x, y in fan(capsule_points(0, -half, half, self.r + grow))]


@dataclass(frozen=True)
class Arrow(Prim):
    """view.cpp direction_marks: one arrowhead, its base centred at (bx, by), pointing along (ux, uy)."""
    bx: float
    by: float
    ux: float
    uy: float

    def triangles(self, grow: float) -> list[Point]:
        ox, oy = self.bx - self.ux * grow, self.by - self.uy * grow
        length, half = MARK_LEN + 2 * grow, MARK_HALF + grow
        return [(ox + self.ux * length, oy + self.uy * length), (ox - self.uy * half, oy + self.ux * half), (ox + self.uy * half, oy - self.ux * half)]


@dataclass(frozen=True)
class Band(Prim):
    """A capsule's rim, r_in to r_out round the segment: the middle stays see-through. The black outline goes outside only."""
    y0: float
    y1: float
    r_in: float
    r_out: float

    def triangles(self, grow: float) -> list[Point]:
        outer, inner = capsule_points(0, self.y0, self.y1, self.r_out + grow), capsule_points(0, self.y0, self.y1, self.r_in)
        out: list[Point] = []
        for i in range(len(outer)):
            j = (i + 1) % len(outer)
            out += [outer[i], outer[j], inner[j], outer[i], inner[j], inner[i]]
        return out


def flare(s: float) -> float:
    """The Morph Beam's half width at height s (-1..1): 1 px through the middle, flaring out to 4 at each end, the last row
    pulled in so the corners are soft."""
    a = abs(s)
    w = 1.0 if a < 0.5 else 1.0 + 3.0 * ((a - 0.5) / 0.5) ** 2
    return w - 0.9 if a > 0.98 else w


@dataclass(frozen=True)
class Morph(Prim):
    """The dot at t = 0, the flared beam at t = 1: one strip of rows, so a renderer only has to blend two rows of numbers."""
    t: float

    def triangles(self, grow: float) -> list[Point]:
        rows = 28
        hd, hb = RADIUS + grow, 11.0 + grow
        height = hd + (hb - hd) * self.t
        left: list[Point] = []
        right: list[Point] = []
        for i in range(rows + 1):
            s = -1 + 2 * i / rows
            disc = hd * math.sqrt(max(1 - s * s, 0))
            w = disc + (flare(s) + grow - disc) * self.t
            y = height * s
            left.append((-w, y)); right.append((w, y))
        out: list[Point] = []
        for i in range(rows):
            out += [left[i], right[i], right[i + 1], left[i], right[i + 1], left[i + 1]]
        return out


# --- Shapes ---------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Glow:
    """A soft halo round `prim`, under the outline, in the theme's primary: copies from widest to narrowest, each pixel keeping
    the brighter of the halo and what is behind it, as gl.cpp's glow over the grid does. So it lifts a dark window, and on a
    bright one it vanishes: it neither greys the page nor bleaches the letters."""
    prim: Prim
    peak: float         # the middle's strength, 0..1: 0.34 of the primary is about the border glow's 0.18
    sigma: float        # px, its falloff
    reach: int          # px past the shape

    def steps(self) -> list[tuple[float, float]]:
        """(grow, strength) of each copy, widest first."""
        return [(float(g), self.peak * math.exp(-0.5 * (g / self.sigma) ** 2)) for g in range(self.reach, RING_GLOW_START - 1, -1)]


@dataclass(frozen=True)
class Shape:
    ink: tuple[Prim, ...]
    glow: Glow | None = None

    def layers(self, level: float, primary: Rgb) -> list[tuple[Rgb, float, list[Point]]]:
        """Bottom first: the glow, the black outline, the grey shape."""
        out: list[tuple[Rgb, float, list[Point]]] = []
        if self.glow:
            for grow, strength in self.glow.steps():
                out.append((primary, strength, self.glow.prim.triangles(grow)))
        out.append(((0.0, 0.0, 0.0), 1.0, [p for prim in self.ink for p in prim.triangles(RING)]))
        out.append(((level, level, level), 1.0, [p for prim in self.ink for p in prim.triangles(0.0)]))
        return out


def write_shape(path, shape: Shape, level: float, primary: Rgb) -> None:
    lines = []
    for (r, g, b), alpha, points in shape.layers(level, primary):
        lines.append(f"{r:.6f} {g:.6f} {b:.6f} {alpha:.6f} {len(points)}")
        lines.append(" ".join(f"{x:.4f} {y:.4f}" for x, y in points))
    path.write_text("\n".join(lines) + "\n")


@dataclass(frozen=True)
class Option:
    number: int
    name: str
    blurb: str
    shape: Shape                                # at rest over text
    frames: tuple[Shape, ...] = ()              # an animation from the dot, first to last


BEAM_H = 11.0                                   # half the stem, px: a 26 px tall cursor with its ring
STEM = Box(-1, -BEAM_H, 1, BEAM_H)              # 2 px wide, the ring's weight
SERIFS = (HCapsule(-4, 4, -BEAM_H, 1), HCapsule(-4, 4, BEAM_H, 1))

OPTIONS: list[Option] = [
    Option(1, "Serif Beam", "The classic I-beam: a thin 2 px stem with a short rounded serif at each end, in the dot's grey and black ring.",
           Shape((STEM, *SERIFS))),
    Option(2, "Dot Beam", "The same I-beam with a small version of the dot as its centre, so it still reads as the dot and the click point stays marked.",
           Shape((STEM, *SERIFS, Disc(0, 0, 4)))),
    Option(3, "Hollow Pill", "A vertical capsule outline. Its middle stays see-through, so the letter under the cursor can still be read.",
           Shape((Band(-8, 8, 3, 5),))),
    Option(4, "Stretched Dot", "The dot squeezed into a solid upright bar with about as much grey as the dot, so it is about as bright on the panel.",
           Shape((VCapsule(0, -7, 7, 3),))),
    Option(5, "Caret Bar", "A plain bar with no serifs, standing on a tiny caret cut from the same arrowhead the resize look uses.",
           Shape((VCapsule(0, -10, 8, 1), Arrow(0, 12, 0, -1)))),
    Option(6, "Glow Bar", "A plain thin bar in a soft halo, the one part that takes the theme colour. The halo shows on dark windows and fades away on bright ones.",
           Shape((VCapsule(0, -10, 10, 1),), Glow(VCapsule(0, -10, 10, 1), peak=0.34, sigma=5.0, reach=12))),
    Option(7, "Morph Beam", "The dot draws itself out into a flared beam in about a tenth of a second, and back. Shown in four frames.",
           Shape((Morph(1.0),)), frames=(Shape((Morph(0.0),)), Shape((Morph(0.34),)), Shape((Morph(0.68),)), Shape((Morph(1.0),)))),
    Option(8, "Marks Beam", "The dot stays exactly as it is and gains two small T marks above and below it, the way the resize and move looks add arrows.",
           Shape((Disc(0, 0, RADIUS), Box(-1, -13, 1, -MARK_GAP - RADIUS - RING), HCapsule(-3, 3, -13, 1),
                  Box(-1, MARK_GAP + RADIUS + RING, 1, 13), HCapsule(-3, 3, 13, 1)))),
]
