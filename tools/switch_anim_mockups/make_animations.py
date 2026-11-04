#!/usr/bin/env python3
"""Render the desktop switch animation mockups: one looping clip per design, a contact sheet and a page that plays them all.

Every clip has the same hand: the dot goes to the right edge of a maximized XR-1, pushes past it to XR-2, then goes to the top
edge and pushes past it to XR-3, placed above. See README.md for what is real and what is scripted.

Runs at the lowest priority on llvmpipe, so a render uses neither GPU, and touches nothing on the desktop: the desktops in
the frames are synthetic, nothing is captured, and nothing here starts, stops or talks to a run.
"""
from __future__ import annotations

import argparse
import html
import json
import math
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import cairo
import numpy as np
from PIL import Image, ImageDraw, ImageFont

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from tools.native_spike import edge_pan, neighbors, orbit  # noqa: E402
from tools.native_spike.geometry import DEFAULT_PLACEMENT  # noqa: E402

DEFAULT_BINARY = REPO / "build/switch-mockups/switch_preview"
DEFAULT_OUT = Path.home() / ".hermes/profiles/coder/previews/switch-anim"
FONT = "JetBrainsMono Nerd Font"
OLED_CAP = 191

W, H = 1920, 1200               # the glasses, one eye
DW, DH = 1920, 1080             # a desktop's pixels; maximized it is this size, centred
TOP, BOTTOM = 60, 1140          # the maximized desktop's rows on the canvas: the labels sit in the bands outside it
FPS = 60
DURATION = 4.5                  # one loop, seconds
FADE = 0.10                     # the loop's end fades to black, so the cut back to the start reads as a restart
GAP = 40                        # px between desktops in a strip: the overview's spacing
PUSH_S = 0.62                   # how long the hand pushes, each time
SETTLE_S = 0.54                 # what the renderer's landing takes after that (view.hpp: about 0.52 s)
ABOVE_DEG = 15.0                # how far above XR-2 XR-3 is placed
ALONG_RIGHT = 0.52              # where along the right edge the dot leaves
ALONG_UP = 0.55                 # where along the top edge

IDENTITY = (1, 0, 0, 0, 1, 0, 0, 0, 1)
kBorderPx = 6                   # view.hpp: the border, tile px
kDotPx = 8                       # view.hpp: kFreePointerRadiusPx + kFreePointerRingPx


def clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def lerp(a, b, t):
    return a + (b - a) * t


def smootherstep(t):
    t = clamp(t)
    return t * t * t * (t * (t * 6 - 15) + 10)


def smoothstep(t):
    t = clamp(t)
    return t * t * (3 - 2 * t)


def hermite(x0, v0, x1, dur, tau):
    """x0 at speed v0 (per second) to x1 at rest over dur seconds: the hand's speed carries on into a settle."""
    t = clamp(tau / dur)
    return (2 * t ** 3 - 3 * t ** 2 + 1) * x0 + (t ** 3 - 2 * t ** 2 + t) * dur * v0 + (3 * t ** 2 - 2 * t ** 3) * x1


def spring(x0, v0, x1, tau, omega=15.0, zeta=0.62):
    """A damped spring from x0 at speed v0 to x1: overshoots a little."""
    wd = omega * math.sqrt(1 - zeta * zeta)
    a = x1 - x0
    b = (zeta * omega * a - v0) / wd
    return x1 - math.exp(-zeta * omega * tau) * (a * math.cos(wd * tau) + b * math.sin(wd * tau))


# --- The world: three desktops, the hand ---------------------------------------------------------------------------------
@dataclass
class Segment:
    """One push: from desktop `frm` past `side` to desktop `to`."""
    frm: int
    to: int
    side: str
    start: float            # the hand reaches the edge
    plan: edge_pan.Plan
    d: tuple[int, int]      # canvas direction of the push
    span: float             # px from one maximized desktop to the next along it, gap included
    exit_uv: tuple[float, float]
    entry_uv: tuple[float, float]

    @property
    def end(self):
        return self.start + PUSH_S

    @property
    def total(self):
        return edge_pan.START_PX + self.plan.total_px

    def travel(self, t):
        """Logical px past the edge at t: the same in every clip."""
        return self.total * clamp((t - self.start) / PUSH_S) ** 1.2

    def when(self, px):
        return self.start + PUSH_S * clamp(px / self.total) ** (1 / 1.2)

    def speed(self, px):
        """Hand px per second where the travel is px."""
        x = max(clamp(px / self.total), 1e-3)
        return self.total * 1.2 * x ** (0.2 / 1.2) / PUSH_S


class World:
    def __init__(self, g):
        self.g, self.half = g, g['half']
        az = [edge_pan._slot(g, t)[0] for t in g['tiles']]
        self.placements = [dict(DEFAULT_PLACEMENT) for _ in range(3)]
        self.placements[2] = {**DEFAULT_PLACEMENT, 'x': round((az[1] - az[2]) / orbit.TENTH), 'y': round(math.radians(ABOVE_DEG) / orbit.TENTH)}
        self.wire = [(p['x'] * orbit.TENTH, p['y'] * orbit.TENTH, p['distance'] / 100, p['curvature'], 0.0) for p in self.placements]
        size = (DW, DH)
        right = neighbors.pick_at(g, self.placements, 0, 'right', ALONG_RIGHT)
        up = neighbors.pick_at(g, self.placements, 1, 'up', ALONG_UP)
        if right != 1 or up != 2:
            raise SystemExit(f"the layout does not do what the clip shows: right of XR-1 is {right}, above XR-2 is {up}")
        first = edge_pan.plan(g, self.placements, 0, 1, 'right', ALONG_RIGHT, size)
        second = edge_pan.plan(g, self.placements, 1, 2, 'up', ALONG_UP, size)
        self.a = Segment(0, 1, 'right', 0.50, first, (1, 0), DW + GAP, (1.0, ALONG_RIGHT), (0.0, first.entry))
        self.b = Segment(1, 2, 'up', 2.45, second, (0, -1), DH + GAP, (ALONG_UP, 0.0), (second.entry, 1.0))

    def dot_rest(self, t):
        """(desktop, u, v) of the dot while the desktop is simply maximized: the same in every clip."""
        a, b = self.a, self.b
        if t <= a.start:
            k = smootherstep((t - 0.12) / (a.start - 0.12))
            return 0, lerp(0.60, 1.0, k), lerp(0.66, ALONG_RIGHT, k)
        if t <= b.start:
            k = smootherstep((t - (b.start - 0.40)) / 0.40)
            return 1, lerp(a.entry_uv[0], ALONG_UP, k), lerp(a.entry_uv[1], 0.0, k)
        k = smootherstep((t - (b.end + SETTLE_S + 0.1)) / 0.45)
        return 2, lerp(b.entry_uv[0], 0.5, k), lerp(1.0, 0.72, k)


# --- What a frame is -----------------------------------------------------------------------------------------------------
@dataclass
class Card:
    """A desktop on the view, flat: its centre, its size as seen, how bright, and how much of the compositor's own border
    and glow it wears (0: none, as a maximized desktop)."""
    src: int
    cx: float = W / 2
    cy: float = H / 2
    w: float = DW
    h: float = DH
    level: float = 1.0
    border: float = 0.0

    def rect(self):
        """Its pixels on the view, its border included."""
        grow = kBorderPx * self.w / DW
        return (self.cx - self.w / 2 - grow, self.cy - self.h / 2 - grow, self.cx + self.w / 2 + grow, self.cy + self.h / 2 + grow)


def scene(cards: list[Card], dot=None, view=IDENTITY, highlight: int = -1, alpha: float = 0.0) -> str:
    """draw_and_swap's arguments, the maximized tile for every desktop drawn and all others out of the frame."""
    by = {c.src: c for c in cards}
    fields = ["scene", highlight, f"{alpha:.3f}", *view, *(dot or (-1, -1)), 3]
    for i in range(3):
        c = by.get(i)
        if c is None:  # behind you, a pixel wide: the source is not in this frame
            fields += [0, 0, 1, 1, math.pi, 0, 1, 0, 0, 1]
        else:
            fields += [0, TOP, DW, DH, 0, 0, 1, 0, 0, f"{c.level:.3f}"]
    return " ".join(str(v) for v in fields)


def flat(c: Card, half) -> str:
    """One card alone, moved and scaled on the view by draw_and_swap's `view`: a homography of the plane z = -1, so the
    desktop stays square on to you wherever it goes. A tile moved off centre would turn to face you instead (place_point)."""
    sx, sy = c.w / DW, c.h / DH
    dx, dy = (c.cx - W / 2) * 2 * half[0] / W, -(c.cy - H / 2) * 2 * half[1] / H
    return scene([c], None, (sx, 0, -dx, 0, sy, -dy, 0, 0, 1), c.src if c.border > 0 else -1, c.border)


@dataclass
class Frame:
    layers: list                    # (command, the card's rect or None), first at the back
    dot: tuple | None = None        # canvas px, drawn on top of them all
    mix: float | None = None        # two layers: the share of the second, instead of stacking them
    px: float = 0                   # the hand's px past the edge, for the bar
    commit: float = 1               # px where the hand hands over
    phase: str = ""


def seam(a, b, x):
    return lerp(a[0], b[0], x), lerp(a[1], b[1], x)


def on_card(c: Card, uv):
    return c.cx + (uv[0] - 0.5) * c.w, c.cy + (uv[1] - 0.5) * c.h


def stack(half, cards, dot=None):
    """A Frame of these cards, each over the ones before it."""
    return Frame([(flat(c, half), c.rect()) for c in cards], dot)


# --- The designs -----------------------------------------------------------------------------------------------------
# A mock design: commit(world, seg) is where the hand hands over, in px; transition(world, seg, t) draws the frame or says
# None once it has landed. Each is hand-driven up to its commit point and on its own from there.
class Design:
    number = 0
    name = ""
    line = ""
    real = False

    def commit(self, seg: Segment) -> float:
        return seg.total

    def transition(self, seg: Segment, t: float, world: World):
        raise NotImplementedError


def strip_cards(seg, o, level_from=1.0, level_to=1.0):
    dx, dy = seg.d
    home = Card(seg.frm, W / 2 - o * dx, H / 2 - o * dy, level=level_from)
    other = Card(seg.to, W / 2 + (seg.span - o) * dx, H / 2 + (seg.span - o) * dy, level=level_to)
    return home, other


class Carousel(Design):
    number, name = 3, "Carousel"
    line = "Desktops sit in one strip and slide past at full size. The hand carries the first half, then it glides home. No zoom."
    SETTLE = 0.44

    def transition(self, seg, t, world):
        half = seg.span / 2
        if t <= seg.end:
            o = half * seg.travel(t) / seg.total
        else:
            if t > seg.end + self.SETTLE: return None
            o = hermite(half, half / PUSH_S * 1.2, seg.span, self.SETTLE, t - seg.end)
        home, other = strip_cards(seg, o)
        dot = seam(on_card(home, seg.exit_uv), on_card(other, seg.entry_uv), smoothstep(o / seg.span * 2.2 - 0.3))
        return stack(world.half, [home, other], dot)


class Peek(Design):
    number, name = 4, "Peek + spring"
    line = "Pushing drags the neighbour in from the edge against a rubber band. Past 60% it lets go and springs across."
    TAIL = 0.72
    SHARE = 0.60

    def commit(self, seg):
        return seg.total * self.SHARE

    def peek(self, seg, px):
        f = px / self.commit(seg)
        return seg.span * 0.24 * (1 - math.exp(-1.7 * f)) / (1 - math.exp(-1.7))

    def transition(self, seg, t, world):
        px = seg.travel(t)
        cut = seg.when(self.commit(seg))
        if t <= cut:
            o = self.peek(seg, px)
            f = px / self.commit(seg)
            level = lerp(1, 0.80, f)
            home, other = strip_cards(seg, o, level, lerp(0.55, 0.85, f))
        else:
            if t > cut + self.TAIL: return None
            start = self.peek(seg, self.commit(seg))
            v = (self.peek(seg, self.commit(seg)) - self.peek(seg, self.commit(seg) * 0.98)) / (self.commit(seg) * 0.02) * seg.speed(self.commit(seg))
            o = spring(start, v, seg.span, t - cut)
            k = smoothstep((t - cut) / 0.3)
            home, other = strip_cards(seg, o, lerp(0.80, 0.6, k), lerp(0.85, 1, k))
        dot = seam(on_card(home, seg.exit_uv), on_card(other, seg.entry_uv), smoothstep((o / seg.span - 0.1) / 0.3))
        return stack(world.half, [home, other], dot)


class Swing(Design):
    number, name = 5, "Room swing"
    line = "The desktops are walls of a room round you. The room swings a quarter turn, hinged at the seam, with perspective."
    SETTLE = 0.46
    HELD = math.radians(30)  # how far the hand takes it

    def faces(self, seg, theta, half):
        """Each face as draw_and_swap's `view`: a 3x3 homography from the canvas plane z = -1 onto that wall (see README.md)."""
        a = half[0] if seg.side == 'right' else half[1] * DH / H
        p = (0, 0, -1 + a)  # the hinge axis: the middle of the room, between you and the desktop
        c, s = math.cos(theta), math.sin(theta)
        if seg.side == 'right':
            pose = [((1 * c, 0, -s), (0, 1, 0), (-a * s, 0, -a * c)), ((s, 0, c), (0, 1, 0), (a * c, 0, -a * s))]
        else:
            pose = [((1, 0, 0), (0, c, -s), (0, -a * s, -a * c)), ((1, 0, 0), (0, s, c), (0, a * c, -a * s))]
        views = []
        for r1, r2, rel in pose:
            q = tuple(p[i] + rel[i] for i in range(3))
            views.append((r1[0], r2[0], -q[0], r1[1], r2[1], -q[1], r1[2], r2[2], -q[2]))
        return views

    def transition(self, seg, t, world):
        if t <= seg.end:
            theta = self.HELD * seg.travel(t) / seg.total
        else:
            if t > seg.end + self.SETTLE: return None
            theta = hermite(self.HELD, self.HELD / PUSH_S * 1.2, math.pi / 2, self.SETTLE, t - seg.end)
        va, vb = self.faces(seg, theta, world.half)
        home, other = Card(seg.frm, level=0.45 + 0.55 * math.cos(theta)), Card(seg.to, level=0.45 + 0.55 * math.sin(theta))
        over = theta > math.pi / 4
        return Frame([(scene([home], None if over else on_card(home, seg.exit_uv), va), None),  # two walls meet at a seam, never overlap
                      (scene([other], on_card(other, seg.entry_uv) if over else None, vb), None)])


class Crossfade(Design):
    number, name = 6, "Crossfade + nudge"
    line = "The desktop nudges a little toward the push, then the two fade across in 0.3 s while the new one eases into place."
    FADE_S = 0.32
    SHARE = 0.4
    TRAVEL = 220

    def commit(self, seg):
        return seg.total * self.SHARE

    def transition(self, seg, t, world):
        dx, dy = seg.d
        cut = seg.when(self.commit(seg))
        nudge = self.TRAVEL * 0.5
        if t <= cut:
            o = nudge * seg.travel(t) / self.commit(seg)
            home = Card(seg.frm, W / 2 - o * dx, H / 2 - o * dy)
            return stack(world.half, [home], on_card(home, seg.exit_uv))
        if t > cut + self.FADE_S: return None
        k = (t - cut) / self.FADE_S
        o = lerp(nudge, self.TRAVEL, k)
        q = self.TRAVEL * (1 - smootherstep(k))
        home = Card(seg.frm, W / 2 - o * dx, H / 2 - o * dy)
        other = Card(seg.to, W / 2 + q * dx, H / 2 + q * dy)
        share = smoothstep(k)
        return Frame([(flat(home, world.half), None), (flat(other, world.half), None)], seam(on_card(home, seg.exit_uv), on_card(other, seg.entry_uv), share), mix=share)


class Tile(Design):
    number, name = 7, "Edge tile"
    line = "A small live tile of the neighbour slides in at the edge, at its overview size. Past 55% it grows into the full desktop."
    GROW = 0.42
    SHARE = 0.55
    TW, TH = 586.67, 330.0
    INSET = 54

    def commit(self, seg):
        return seg.total * self.SHARE

    def small(self, seg, f):
        """The tile's centre at f of the way in."""
        k = 1 - (1 - f) ** 2
        if seg.side == 'right':
            y = clamp(TOP + seg.exit_uv[1] * DH, TOP + self.TH / 2 + 24, BOTTOM - self.TH / 2 - 24)
            return W + self.TW / 2 - k * (self.TW + self.INSET), y
        x = clamp(seg.exit_uv[0] * DW, self.TW / 2 + 24, W - self.TW / 2 - 24)
        return x, TOP - self.TH / 2 + k * (self.TH + self.INSET)

    def transition(self, seg, t, world):
        cut = seg.when(self.commit(seg))
        if t <= cut:
            f, grow = seg.travel(t) / self.commit(seg), 0.0
        else:
            if t > cut + self.GROW: return None
            f, grow = 1.0, smootherstep((t - cut) / self.GROW)
        cx, cy = self.small(seg, f)
        tile = Card(seg.to, lerp(cx, W / 2, grow), lerp(cy, H / 2, grow), lerp(self.TW, DW, grow), lerp(self.TH, DH, grow), border=1 - grow)
        home = Card(seg.frm, level=lerp(lerp(1, 0.7, f), 0.3, grow))
        near_edge = (tile.cx - tile.w / 2, tile.cy + (seg.entry_uv[1] - 0.5) * tile.h) if seg.side == 'right' else (tile.cx + (seg.entry_uv[0] - 0.5) * tile.w, tile.cy + tile.h / 2)
        at_edge = on_card(home, seg.exit_uv)
        dot = seam(at_edge, near_edge, smoothstep(f * 2.2)) if t <= cut else near_edge
        return stack(world.half, [home, tile], dot)


class Sheet(Design):
    number, name = 8, "Sheet over"
    line = "The desktop you are on barely moves: it dims and steps back, and the neighbour slides over it as one sheet from the side it lies on."
    SETTLE = 0.40

    def transition(self, seg, t, world):
        dx, dy = seg.d
        span = DW if seg.side == 'right' else DH
        half = span / 2
        if t <= seg.end:
            s = half * seg.travel(t) / seg.total
        else:
            if t > seg.end + self.SETTLE: return None
            s = hermite(half, half / PUSH_S * 1.2, span, self.SETTLE, t - seg.end)
        k = s / span
        scale = 1 - 0.05 * k
        home = Card(seg.frm, W / 2 - 0.06 * s * dx, H / 2 - 0.06 * s * dy, DW * scale, DH * scale, level=lerp(1, 0.38, k))
        sheet = Card(seg.to, W / 2 + (span - s) * dx, H / 2 + (span - s) * dy, border=1 - smoothstep((k - 0.8) / 0.2))
        return stack(world.half, [home, sheet], on_card(sheet, seg.entry_uv))


class Today(Design):
    number, name = 1, "Hand pan + settle"
    line = "Today's. The view turns 1:1 with the hand while it pulls back to show both, then settles onto the target in about 0.5 s."
    real = True


class Hop(Design):
    number, name = 2, "Zoom hop"
    line = "The old clip. A short push fires it: zoom out to the overview, turn to the target, zoom in. Then the hand has no say."
    real = True
    FIRE = edge_pan.START_PX + edge_pan.PEEK_PX

    def commit(self, seg):
        return self.FIRE


DESIGNS = [Today(), Hop(), Carousel(), Peek(), Swing(), Crossfade(), Tile(), Sheet()]


def phase_of(design, seg, t):
    if seg.travel(t) < design.commit(seg): return "the hand drives it"
    return "settling by itself"


class Timeline:
    """What to draw at each moment of a mock design."""

    def __init__(self, design, world):
        self.d, self.w = design, world

    def rest(self, t, commit):
        desktop, u, v = self.w.dot_rest(t)
        c = Card(desktop)
        return Frame([(scene([c], on_card(c, (u, v))), None)], commit=commit, phase=f"on XR-{desktop + 1}")

    def frame(self, t):
        d, w = self.d, self.w
        seg = w.a if t < w.b.start else w.b
        commit = d.commit(seg)
        if t < seg.start: return self.rest(t, commit)
        made = d.transition(seg, t, w)
        if made is None: return self.rest(t, commit)
        made.px, made.commit, made.phase = seg.travel(t), commit, phase_of(d, seg, t)
        return made


def run_real(design, world, renderer, frame_times, wanted):
    """Today's and the old clip: the renderer's own ViewAnimator takes the same commands a run sends it."""
    renderer.send("placements " + " ".join(f"{v:.7f}" for p in world.wire for v in p))
    renderer.send("start 0 0")
    state = {id(world.a): 'rest', id(world.b): 'rest'}
    previous = -1 / FPS
    hop = isinstance(design, Hop)
    for number, t in enumerate(frame_times):
        ms = previous
        while ms + 0.004 <= t:  # the hand's samples, about 250 Hz
            ms += 0.004
            seg = world.a if ms < world.b.start else world.b
            px = seg.travel(ms)
            if ms < seg.start or state[id(seg)] == 'landing': continue
            if hop:
                if px >= Hop.FIRE:
                    renderer.send(f"target {seg.to} 1 0 0 {ms * 1000:.3f}")
                    state[id(seg)] = 'landing'
                continue
            if px > edge_pan.START_PX:
                f = seg.plan.frame(px - edge_pan.START_PX)
                side = edge_pan.SIDES.index(f['side'])
                renderer.send(f"pan {f['from']} {f['to']} {side} {f['exit'] / 1e4} {f['entry'] / 1e4} {f['peek'] / 1e4} {f['cross'] / 1e4} {f['turn'] / 1e4 * math.pi} {f['zoom'] / 1e4} {ms * 1000:.3f}")
            if px >= seg.total:
                renderer.send(f"target {seg.to} 0 0 1 {ms * 1000:.3f}")
                state[id(seg)] = 'landing'
        previous = t
        seg = world.a if t < world.b.start else world.b
        landing = t >= seg.start and state[id(seg)] == 'landing'
        settled = landing and t > seg.end + SETTLE_S
        rest = world.dot_rest(t)
        if t < seg.start:
            a, ua, va, b, ub, vb = rest[0], rest[1], rest[2], -1, 0, 0
        elif not landing:
            a, ua, va, b, ub, vb = seg.frm, *seg.exit_uv, -1, 0, 0
        elif settled:
            a, ua, va, b, ub, vb = rest[0], rest[1], rest[2], -1, 0, 0
        elif hop:
            a, ua, va, b, ub, vb = seg.frm, *seg.exit_uv, seg.to, *seg.entry_uv
        else:
            a, ua, va, b, ub, vb = seg.to, *seg.entry_uv, -1, 0, 0
        px = 0 if t < seg.start or settled else seg.travel(t)
        phase = f"on XR-{rest[0] + 1}" if t < seg.start or settled else phase_of(design, seg, t)
        if number in wanted:
            pixels = renderer.draw(f"frame {t * 1000:.3f} {a} {ua} {va} {b} {ub} {vb}")
            yield number, t, Frame([], px=px, commit=design.commit(seg), phase=phase), pixels
        else:
            renderer.send(f"tick {t * 1000:.3f}")


def composed(renderer, made):
    """The layers as one picture. A card covers what is behind it; its glow outside that is the brighter of the two, as
    gl.cpp lays a glow over a background. The dot goes on last, whole, with its black ring."""
    layers = [(renderer.draw(c), rect) for c, rect in made.layers]
    out = layers[0][0]
    if made.mix is not None:
        out = (out * (1 - made.mix) + layers[1][0] * made.mix).astype(np.uint8)
    else:
        for pixels, rect in layers[1:]:
            below = out
            out = np.maximum(below, pixels)
            if rect:
                x0, y0, x1, y1 = (round(v) for v in rect)
                x0, y0, x1, y1 = max(x0, 0), max(y0, 0), min(x1, W), min(y1, H)
                if x1 > x0 and y1 > y0: out[y0:y1, x0:x1] = pixels[y0:y1, x0:x1]
    if made.dot:
        spot = renderer.draw(scene([], made.dot))
        reach = kDotPx + 1
        cx, cy = made.dot
        x0, y0, x1, y1 = max(round(cx) - reach, 0), max(round(cy) - reach, 0), min(round(cx) + reach + 1, W), min(round(cy) + reach + 1, H)
        if x1 > x0 and y1 > y0:
            ys, xs = np.mgrid[y0:y1, x0:x1]
            inside = (xs - cx) ** 2 + (ys - cy) ** 2 <= kDotPx ** 2
            out = out.copy() if not out.flags.writeable else out
            out[y0:y1, x0:x1][inside] = spot[y0:y1, x0:x1][inside]
    return out


def run_mock(design, world, renderer, frame_times, wanted):
    timeline = Timeline(design, world)
    for number, t in enumerate(frame_times):
        if number not in wanted: continue
        made = timeline.frame(t)
        yield number, t, made, composed(renderer, made)


# --- Renderer, desktops, labels ------------------------------------------------------------------------------------------
class Renderer:
    SIZE = W * H * 4

    def __init__(self, binary, accent, desktops):
        env = dict(os.environ, LIBGL_ALWAYS_SOFTWARE="1", GALLIUM_DRIVER="llvmpipe", LP_NUM_THREADS="4", EGL_LOG_LEVEL="fatal")
        self.p = subprocess.Popen([str(binary), "render", accent, str(desktops)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=env)

    def send(self, line):
        self.p.stdin.write(line.encode() + b"\n")

    def draw(self, line):
        self.send(line)
        self.p.stdin.flush()
        data = self.p.stdout.read(self.SIZE)
        if len(data) != self.SIZE: raise SystemExit(f"switch_preview stopped: {line[:80]}")
        return np.frombuffer(data, np.uint8).reshape(H, W, 4)

    def close(self):
        self.p.stdin.close()
        self.p.wait()


class Theme:
    def __init__(self, scheme):
        for name, value in scheme.items():
            setattr(self, name, tuple(value))


TERMINAL = ["~/code/viture-workspace  master", "$ ctest --test-dir build/release -j 1", "100% tests passed, 0 tests failed out of 70", "$ git status", "On branch master", "nothing to commit, working tree clean", "", "~/code/viture-workspace  master", "$ _"]
EDITOR = ["fn next_desktop(from: Desktop, push: Edge) {", "    let target = nearest_within(from, push, 45.0);", "    match target {", "        Some(t) => view.turn_toward(t),", "        None => cursor.stop_at_edge(),", "    }", "}", "", "// row neighbours sit under a degree apart"]
NOTES = ["Tuesday", "", "- try the switch mockups in the glasses", "- pick the one that does not make you feel it", "- XR-3 is up here: push up from XR-2", "", "Groceries", "- oat milk, coffee, limes"]


def desktop_pixels(index, t, path):
    surface = cairo.ImageSurface(cairo.FORMAT_RGB24, DW, DH)
    cr = cairo.Context(surface)
    cr.set_source_rgb(*t.surface_container); cr.paint()
    cr.select_font_face(FONT, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
    cr.set_font_size(19); cr.set_source_rgb(*t.on_surface_variant)
    cr.move_to(26, 28); cr.show_text("1  2  3")
    cr.move_to(DW - 92, 28); cr.show_text("00:35")
    x, y, w, h = 70, 70, DW - 140, DH - 130
    cr.set_source_rgb(*t.surface_container_low); cr.rectangle(x, y, w, h); cr.fill()
    cr.set_source_rgb(*t.primary); cr.set_line_width(2); cr.rectangle(x, y, w, h); cr.stroke()
    cr.select_font_face(FONT, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
    cr.set_font_size(240); cr.set_source_rgb(*t.surface_container_highest)  # which desktop this is, from across the room
    extent = cr.text_extents(f"XR-{index + 1}")
    cr.move_to(x + w - extent.width - 60, y + h - 60); cr.show_text(f"XR-{index + 1}")
    cr.select_font_face(FONT, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
    lines, size = ((TERMINAL, 34), (EDITOR, 34), (NOTES, 40))[index]
    cr.set_font_size(size)
    for i, text in enumerate(lines):
        cr.set_source_rgb(*(t.primary if text.startswith(("~", "//", "Tuesday", "Groceries")) else t.on_surface))
        cr.move_to(x + 48, y + 80 + i * size * 1.46); cr.show_text(text)
    surface.flush()
    path.write_bytes(bytes(surface.get_data()))


def font_file(style):
    return subprocess.run(["fc-match", "-f", "%{file}", f"{FONT}:{style}"], capture_output=True, text=True, check=True).stdout


class Labels:
    """The bands above and below the maximized desktop: number, name, what is real, the push bar. Under the OLED cap."""
    BAR_X, BAR_Y, BAR_W, BAR_H = 560, 1164, 800, 12

    def __init__(self, design, theme):
        self.t = theme
        self.bold, self.regular = ImageFont.truetype(font_file("bold"), 30), ImageFont.truetype(font_file("regular"), 24)
        self.ink = self.rgb(theme.on_surface)
        self.dim = self.rgb(theme.on_surface_variant)
        self.fill = self.rgb(theme.primary)
        self.track = self.rgb(theme.surface_container_high)
        self.edge = self.rgb(theme.on_surface)
        self.design = design
        self.cache = {}
        top = Image.new("RGB", (W, 52), (0, 0, 0))
        d = ImageDraw.Draw(top)
        d.text((28, 8), f"{design.number}  {design.name}", font=self.bold, fill=self.ink)
        tag = "real renderer + real animator" if design.real else "real renderer, scripted motion"
        d.text((W - 28 - d.textlength(tag, font=self.regular), 13), tag, font=self.regular, fill=self.dim)
        self.top = np.array(top)

    @staticmethod
    def rgb(c):
        return tuple(round(v * 255) for v in c)

    def bottom(self, phase):
        if phase not in self.cache:
            band = Image.new("RGB", (W, 52), (0, 0, 0))
            d = ImageDraw.Draw(band)
            d.text((28, 12), "hand past the edge", font=self.regular, fill=self.dim)
            d.text((self.BAR_X + self.BAR_W + 36, 12), phase, font=self.regular, fill=self.ink)
            self.cache[phase] = np.array(band)
        return self.cache[phase]

    def paint(self, pixels, made):
        out = pixels.copy()
        out[0:52, :, :3] = self.top
        band = self.bottom(made.phase).copy()
        x, y, w, h = self.BAR_X, self.BAR_Y - (H - 52), self.BAR_W, self.BAR_H
        band[y:y + h, x:x + w] = self.track
        share = clamp(made.px / made.commit) if made.commit else 0
        band[y:y + h, x:x + round(w * share)] = self.fill
        d = 3
        band[y - 6:y + h + 6, x + w - d:x + w] = self.edge  # the hand's part ends here
        out[H - 52:H, :, :3] = band
        return out


def encode_mp4(path):
    return subprocess.Popen(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
                             "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-tune", "animation", "-g", "30", "-threads", "4", "-pix_fmt", "yuv420p",
                             "-movflags", "+faststart", str(path)], stdin=subprocess.PIPE)


def encode_gif(mp4, gif):
    graph = "fps=30,scale=960:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=128:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-threads", "4", "-i", str(mp4), "-filter_complex", graph, "-loop", "0", str(gif)], check=True)


# --- Contact sheet and page ----------------------------------------------------------------------------------------------
def key_times(world):
    return [s.start + d for s in (world.a, world.b) for d in (0.30, PUSH_S - 0.02, PUSH_S + 0.20)]


def contact_sheet(designs, shots, times, theme, accent, theme_name, out):
    tw, th, gutter, left, head = 480, 300, 10, 300, 92
    page = Image.new("RGB", (left + len(times) * (tw + gutter) + 20, head + len(designs) * (th + gutter) + 20), (0, 0, 0))
    d = ImageDraw.Draw(page)
    ink, dim = Labels.rgb(theme.on_surface), Labels.rgb(theme.on_surface_variant)
    bold, regular, small = ImageFont.truetype(font_file("bold"), 30), ImageFont.truetype(font_file("regular"), 22), ImageFont.truetype(font_file("regular"), 18)
    d.text((20, 16), "Switching desktops with the cursor", font=bold, fill=ink)
    d.text((20, 54), f"{theme_name} #{accent}. Same hand in every row. First three frames: push right, XR-1 to XR-2. Last three: push up, XR-2 to XR-3.", font=regular, fill=dim)
    for row, design in enumerate(designs):
        y = head + row * (th + gutter)
        d.text((20, y + 8), str(design.number), font=bold, fill=ink)
        d.text((20, y + 48), design.name, font=regular, fill=ink)
        d.text((20, y + 80), "real animator" if design.real else "scripted motion", font=small, fill=dim)
        for col, t in enumerate(times):
            page.paste(Image.fromarray(shots[design.number][col]).resize((tw, th), Image.LANCZOS), (left + col * (tw + gutter), y))
    page.save(out)


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Switch Animations</title>
<style>
:root{{color-scheme:dark;--bg:#000;--ink:{ink};--dim:{dim};--line:{line}}}
*{{box-sizing:border-box}}
body{{margin:0;padding:24px;background:var(--bg);color:var(--ink);font:15px/1.5 "JetBrainsMono Nerd Font",ui-monospace,monospace}}
h1{{font-size:22px;margin:0 0 4px}}
p{{margin:0;color:var(--dim)}}
.bar{{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin:16px 0 20px}}
button{{background:#000;color:var(--ink);border:1px solid var(--line);border-radius:999px;padding:6px 16px;font:inherit;cursor:pointer}}
button[aria-pressed=true]{{background:var(--line)}}
.grid{{display:grid;gap:20px;grid-template-columns:repeat(auto-fit,minmax(min(100%,440px),1fr))}}
figure{{margin:0}}
video{{display:block;width:100%;aspect-ratio:16/10;background:#000;border:1px solid var(--line);border-radius:6px}}
figcaption{{padding-top:8px}}
figcaption b{{display:block}}
figcaption span{{color:var(--dim)}}
.sheet{{display:block;width:100%;margin-top:32px;border:1px solid var(--line);border-radius:6px}}
</style></head><body>
<h1>Switching desktops with the cursor</h1>
<p>The same hand in every clip: push right from XR-1 to XR-2, then up to XR-3. The bar shows the push, and its end mark is where the hand hands over. {theme_name}, one eye at 1920 x 1200.</p>
<div class="bar">
<button id="sync">Restart all together</button>
<button id="toggle">Pause</button>
<button data-rate="1" aria-pressed="true">1x</button>
<button data-rate="0.5" aria-pressed="false">0.5x</button>
<button data-rate="0.25" aria-pressed="false">0.25x</button>
</div>
<div class="grid">
{figures}
</div>
<a href="contact-sheet.png"><img class="sheet" src="contact-sheet.png" alt="Key frames of every option"></a>
<script>
const videos=[...document.querySelectorAll('video')];
const toggle=document.getElementById('toggle');
let paused=false;
function restart(){{videos.forEach(v=>{{v.currentTime=0;if(!paused)v.play().catch(()=>{{}})}})}}
document.getElementById('sync').onclick=restart;
toggle.onclick=()=>{{paused=!paused;videos.forEach(v=>paused?v.pause():v.play().catch(()=>{{}}));toggle.textContent=paused?'Play':'Pause'}};
document.querySelectorAll('[data-rate]').forEach(b=>b.onclick=()=>{{
  videos.forEach(v=>v.playbackRate=Number(b.dataset.rate));
  document.querySelectorAll('[data-rate]').forEach(o=>o.setAttribute('aria-pressed',o===b));
}});
</script>
</body></html>
"""


def write_page(designs, theme, theme_name, path):
    figures = "\n".join(
        f'<figure><video src="option-{d.number:02d}.mp4" autoplay loop muted playsinline></video>'
        f'<figcaption><b>{d.number}. {html.escape(d.name)}</b><span>{html.escape(d.line)} {"Real renderer and real animator." if d.real else "Real renderer, scripted motion."}</span></figcaption></figure>'
        for d in designs)
    hex_of = lambda c: "#%02x%02x%02x" % Labels.rgb(c)
    path.write_text(PAGE.format(ink=hex_of(theme.on_surface), dim=hex_of(theme.on_surface_variant), line=hex_of(theme.outline_variant), figures=figures, theme_name=html.escape(theme_name)))


# --- Main ----------------------------------------------------------------------------------------------------------------
def active_theme():
    """The Omarchy theme's name and accent. hyprctl may not reach Hyprland from here, and its files say the same."""
    name = subprocess.run(["omarchy-theme-current"], capture_output=True, text=True, check=True).stdout.strip()
    for line in Path(f"/usr/share/omarchy/themes/{name.lower()}/colors.toml").read_text().splitlines():
        if line.strip().startswith("accent"):
            return name, line.split('"')[1].lstrip("#").lower()
    raise SystemExit(f"no accent in the theme {name}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--accent", help="theme colour rrggbb (default: the active Omarchy theme's accent)")
    parser.add_argument("--theme-name")
    parser.add_argument("--only", help="comma separated option numbers; no contact sheet or page")
    parser.add_argument("--stills", help="comma separated seconds: only PNGs of these moments, named option-NN-at-S.png")
    parser.add_argument("--no-gif", action="store_true")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--binary", type=Path, default=DEFAULT_BINARY)
    args = parser.parse_args()
    os.nice(19)
    if args.accent:
        name, accent = args.theme_name or "custom", args.accent.lstrip("#").lower()
    else:
        name, accent = active_theme()
    info = json.loads(subprocess.run([str(args.binary), "info", accent], capture_output=True, text=True, check=True).stdout)
    theme = Theme(info["scheme"])
    world = World({'w': info['w'], 'h': info['h'], 'half': tuple(info['half']), 'tiles': [tuple(t) for t in info['tiles']]})
    print(f"right: {world.a.total:.0f} px, zoom {world.a.plan.zoom:.3f}; up: {world.b.total:.0f} px, zoom {world.b.plan.zoom:.3f}", flush=True)

    work = args.out / ".desktops"
    work.mkdir(parents=True, exist_ok=True)
    for i in range(3): desktop_pixels(i, theme, work / f"desktop-{i}.bgra")

    designs = [d for d in DESIGNS if not args.only or str(d.number) in args.only.split(",")]
    frames = round(DURATION * FPS)
    times = [i / FPS for i in range(frames)]
    keys = key_times(world)
    key_frames = [round(t * FPS) for t in keys]
    shots = {}
    too_bright = 0
    renderer = Renderer(args.binary, accent, work)
    for design in designs:
        labels = Labels(design, theme)
        if args.stills:
            moments = [float(v) for v in args.stills.split(",")]
            wanted = {round(v * FPS) for v in moments}
        else:
            wanted = set(range(frames))
        encoder = None if args.stills else encode_mp4(args.out / f"option-{design.number:02d}.mp4")
        run = run_real if design.real else run_mock
        for number, t, made, pixels in run(design, world, renderer, times, wanted):
            shown = labels.paint(pixels, made)
            fade = clamp((DURATION - t) / FADE)
            if fade < 1: shown = (shown * fade).astype(np.uint8)
            over = int((shown[:, :, :3] > OLED_CAP).sum())
            too_bright += over
            if args.stills:
                Image.fromarray(shown[:, :, :3]).save(args.out / f"option-{design.number:02d}-at-{t:.2f}.png")
            else:
                encoder.stdin.write(shown.tobytes())
                if number in key_frames:
                    shots.setdefault(design.number, []).append(shown[:, :, :3].copy())
        if encoder:
            encoder.stdin.close()
            if encoder.wait(): raise SystemExit("ffmpeg failed")
            if not args.no_gif: encode_gif(args.out / f"option-{design.number:02d}.mp4", args.out / f"option-{design.number:02d}.gif")
        print(f"option {design.number} done", flush=True)
    renderer.close()
    print(f"channels over the {OLED_CAP}/255 cap: {too_bright}", flush=True)
    if not args.only and not args.stills:
        contact_sheet(designs, shots, keys, theme, accent, name, args.out / "contact-sheet.png")
        write_page(designs, theme, name, args.out / "index.html")
    shutil.rmtree(work)
    return 1 if too_bright else 0


if __name__ == "__main__":
    sys.exit(main())
