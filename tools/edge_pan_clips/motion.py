"""The edge pan measured: layouts, the hand, and what a frame-by-frame trace of the real renderer's animation says about it.

Offline only. It runs spike/tests/edge_pan_trace.cpp, which has no GL and touches nothing, and reads its JSON lines. The hand is
the mockup's (tools/switch_anim_mockups on branch switch-anim-mockups, clip 1): edge_pan.Plan.frame at 250 Hz, then a landing.
"""
import json
import math
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path: sys.path.insert(0, str(REPO))
from tools.native_spike import edge_pan, neighbors, orbit  # noqa: E402
from tools.native_spike.geometry import DEFAULT_PLACEMENT  # noqa: E402

DEFAULT_TRACE = REPO / 'build/release/edge_pan_trace'
HAND_HZ = 250
PUSH_S = 0.62       # the mockup's: how long the hand pushes
PUSH_SHAPE = 1.2    # and how: it picks up speed
LEAD_S = 0.20       # still, maximized, before the hand arrives
TAIL_S = 1.10       # after the push: longer than any landing
FOLLOW, LOCKED, UNTRACKED = 2, 1, 0


def half_fov(diagonal_deg, w=1920, h=1200):
    """The renderer's split of a diagonal FOV (wayland.cpp update_fov)."""
    d, a = math.tan(math.radians(diagonal_deg) / 2), w / h
    return d * a / math.sqrt(1 + a * a), d / math.sqrt(1 + a * a)


def overview(sources, w, h, spacing):
    """view.cpp overview_layout."""
    s, n = max(0.0, min(400.0, spacing)), len(sources)
    aspects = sum(a / b for a, b in sources)
    tile_h = min(h, max(0.0, w - (n + 1) * s) / aspects)
    x = (w - (tile_h * aspects + (n - 1) * s)) / 2
    out = []
    for a, b in sources:
        tw = tile_h * a / b
        out.append((x, (h - tile_h) / 2, tw, tile_h))
        x += tw + s
    return out


class Layout:
    """Desktops as a run has them. placements: wire units (geometry.py); rolls: hundredths of a degree; logical: each desktop's
    output size in logical px, what the hand is measured in."""
    def __init__(self, name, sources, placements, spacing=40, rolls=None, logical=None, diagonal=46.0, w=1920, h=1200):
        self.name, self.sources, self.placements, self.spacing = name, list(sources), [dict(p) for p in placements], spacing
        self.rolls = list(rolls) if rolls is not None else [0] * len(self.sources)
        self.logical = list(logical) if logical is not None else list(self.sources)
        self.w, self.h, self.half = w, h, half_fov(diagonal, w, h)
        self.g = {'w': w, 'h': h, 'half': self.half, 'tiles': overview(self.sources, w, h, spacing)}

    def wire(self, mode):
        parts = [f'layout {self.w} {self.h} {self.half[0]:.9f} {self.half[1]:.9f} {self.spacing} {mode} {len(self.sources)}']
        for (a, b), p, r in zip(self.sources, self.placements, self.rolls):
            parts.append(f"{a} {b} {p['x'] * orbit.TENTH:.9f} {p['y'] * orbit.TENTH:.9f} {p['distance'] / 100:.6f} "
                         f"{math.radians(p['curvature']):.9f} {math.radians(r / 100):.9f}")
        return ' '.join(parts)

    def target(self, home, side, along):
        return neighbors.pick_at(self.g, self.placements, home, side, along)

    def plan(self, home, target, side, along):
        return edge_pan.plan(self.g, self.placements, home, target, side, along, self.logical[home])


def placed_at(g, index, az, el, distance=100, curvature=0):
    """The wire placement that puts desktop `index` of overview layout `g` in direction (az, el) degrees."""
    t = g['tiles'][index]
    (hw, hh), w, h = g['half'], g['w'], g['h']
    wx, wy = (2 * (t[0] + t[2] / 2) / w - 1) * hw, (1 - 2 * (t[1] + t[3] / 2) / h) * hh
    a0, e0 = math.degrees(math.atan2(wx, 1.0)), math.degrees(math.atan2(wy, math.sqrt(1 + wx * wx)))
    return {'x': round((az - a0) * 10), 'y': round((el - e0) * 10), 'distance': distance, 'curvature': curvature}


def scene(name, spots, sources=((1920, 1080),) * 3, spacing=40, rolls=None, diagonal=46.0):
    """A Layout from (az, el, distance, curvature) per desktop."""
    g = {'w': 1920, 'h': 1200, 'half': half_fov(diagonal), 'tiles': overview(list(sources), 1920, 1200, spacing)}
    return Layout(name, sources, [placed_at(g, i, *s) for i, s in enumerate(spots)], spacing=spacing, rolls=rolls, diagonal=diagonal)


def video_row():
    """The clip Cody picked: three default desktops in a row, XR-3 lifted 15 degrees over XR-2."""
    sources = ((1920, 1080),) * 3
    g = {'w': 1920, 'h': 1200, 'half': half_fov(46.0), 'tiles': overview(list(sources), 1920, 1200, 40)}
    az = [edge_pan._slot(g, t)[0] for t in g['tiles']]
    return Layout('row', sources, [DEFAULT_PLACEMENT, DEFAULT_PLACEMENT, {**DEFAULT_PLACEMENT, 'x': round((az[1] - az[2]) / orbit.TENTH), 'y': 150}])


def codys_layout():
    """Cody's luma-ultra setup as saved on 2026-11-04 (~/.config/viture-workspace/profiles.json), kept here so nothing reads his
    config. The 52 degree diagonal is the Luma Ultra's published FOV: the driver's own value was not recorded anywhere to read."""
    return Layout('cody', ((1920, 1200),) * 3,
                  [{'x': -71, 'y': 93, 'distance': 38, 'curvature': 0}, {'x': 196, 'y': 91, 'distance': 39, 'curvature': 0},
                   {'x': -165, 'y': -118, 'distance': 71, 'curvature': 0}],
                  spacing=0, rolls=[38, -65, -22], logical=[(1920, 1200), (1536, 960), (1536, 960)], diagonal=52.0)


def named_layouts():
    """name: (Layout, home, side, along). The ones the clips and the metrics table show."""
    u = lambda d: 1 / (d / 100)
    return {
        'row': (video_row(), 0, 'right', 0.52),
        'row-up': (video_row(), 1, 'up', 0.55),
        'cody': (codys_layout(), 0, 'right', 0.5),
        'cody-back': (codys_layout(), 1, 'left', 0.5),
        'cody-down': (codys_layout(), 0, 'down', 0.95),
        'big-3x': (scene('big-3x', [(-22, 0, 33, 0), (22, 0, 33, 0), (0, -130, 33, 0)]), 0, 'right', 0.5),
        'big-10x': (scene('big-10x', [(-60, 0, 10, 0), (60, 0, 10, 0), (180, 0, 100, 0)]), 0, 'right', 0.5),
        'small': (scene('small', [(-8, 0, 200, 0), (8, 0, 200, 0), (0, -30, 200, 0)]), 0, 'right', 0.5),
        'moved-up': (scene('moved-up', [(-10, -12, 100, 0), (2, 22, 100, 0), (160, 0, 100, 0)]), 0, 'up', 0.6),
        'moved-down': (scene('moved-down', [(0, 20, 60, 0), (-4, -24, 60, 0), (160, 0, 100, 0)]), 0, 'down', 0.5),
        'behind': (scene('behind', [(0, 0, 100, 0), (150, 8, 100, 0), (-90, 0, 100, 0)]), 0, 'right', 0.5),
        'curved': (scene('curved', [(-26, 0, 50, 60), (26, 4, 50, 60), (0, -40, 50, 60)]), 0, 'right', 0.4),
        'rolled': (scene('rolled', [(-30, 4, 60, 0), (30, -6, 60, 0), (0, -50, 60, 0)], rolls=[1800, -2500, 0]), 0, 'right', 0.5),
        'mixed': (scene('mixed', [(-16, 0, 150, 0), (30, 6, 30, 0), (0, -40, 100, 0)]), 0, 'right', 0.5),
    }


def hand(plan, seconds=PUSH_S):
    """Logical px past the edge at t seconds after the hand reached it, and when it got to the end of the plan."""
    total = edge_pan.START_PX + plan.total_px
    return (lambda t: total * max(0.0, min(1.0, t / seconds)) ** PUSH_SHAPE), total


def pan_line(f, t_ms):
    return (f"pan {f['from']} {f['to']} {edge_pan.SIDES.index(f['side'])} {f['exit'] / 1e4} {f['entry'] / 1e4} {f['peek'] / 1e4} "
            f"{f['cross'] / 1e4} {f['turn'] / 1e4 * math.pi} {f['zoom'] / 1e4} {t_ms:.3f}")


def edge_uv(side, f):
    """(u, v) on a desktop of the point `f` along `side`."""
    return {'right': (1.0, f), 'left': (0.0, f), 'up': (f, 0.0), 'down': (f, 1.0)}[side]


def script(layout, plan, fps=120, mode=FOLLOW, seconds=PUSH_S, lead=LEAD_S, tail=TAIL_S, back_at=None):
    """The trace's commands for one push: the hand 1:1 to the end of the plan and a landing. back_at (0..1 of the push): it stops
    there and pulls back the same way instead."""
    travel, total = hand(plan, seconds)
    home, target = plan.home, plan.target
    lines = [layout.wire(mode), f'start {home}']
    end = lead + seconds * (2 if back_at else 1) + tail
    landed = False
    samples = int(end * HAND_HZ)
    next_frame = 0.0
    on_home, on_target = (home, *edge_uv(plan.side, plan.exit)), (target, *edge_uv(edge_pan.OPPOSITE[plan.side], plan.entry))
    for i in range(samples + 1):
        now = i / HAND_HZ
        while next_frame <= now:
            a, u, v = on_target if landed and back_at is None else on_home
            lines.append(f'frame {next_frame * 1000:.3f} {a} {u:.4f} {v:.4f}')  # the trace reads the time, the clips the dot
            next_frame += 1 / fps
        t = now - lead
        if t < 0 or landed: continue
        if back_at is not None:
            turn = seconds * back_at
            if t > 2 * turn:
                landed = True
                lines.append(f'off {now * 1000:.3f}')
                continue
            px = travel(t if t < turn else 2 * turn - t)
        else:
            px = travel(t)
        if px > edge_pan.START_PX:
            lines.append(pan_line(plan.frame(min(px - edge_pan.START_PX, plan.total_px)), now * 1000))
        if back_at is None and px >= total:
            lines.append(f'target 1 {target} 0 0 1 {now * 1000:.3f}')
            landed = True
    return lines


def trace(lines, binary=DEFAULT_TRACE):
    out = subprocess.run([str(binary)], input='\n'.join(lines) + '\n', capture_output=True, text=True, check=True).stdout
    return [json.loads(line) for line in out.splitlines()]


def _unit(v):
    n = math.sqrt(sum(a * a for a in v))
    return tuple(a / n for a in v) if n else v


def _angle(a, b):
    a, b = _unit(a), _unit(b)
    x = (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])
    return math.atan2(math.sqrt(sum(c * c for c in x)), sum(p * q for p, q in zip(a, b)))


def reversals(values, floor):
    """The turns of a series: how many times it changes direction by more than `floor`, and the largest such move back."""
    count, worst = 0, 0.0
    going, peak = 0, values[0] if values else 0.0
    for v in values[1:]:
        d = v - peak
        if going == 0:
            if abs(d) > floor: going, peak = (1 if d > 0 else -1), v
        elif going * d >= 0:
            peak = v
        elif abs(d) > floor:
            count += 1
            worst = max(worst, abs(d))
            going, peak = -going, v
    return count, worst


def outside(values):
    """How far a series goes past the range of its two ends, as a share of that range's upper end."""
    lo, hi = min(values[0], values[-1]), max(values[0], values[-1])
    return max(max(values) - hi, lo - min(values), 0.0) / max(hi, 1e-6)


def _polygon(d, near=1e-3):
    """The desktop's corners on the view plane, or None if any is behind you."""
    pts = []
    for x, y, z in d['corners']:
        if z >= -near: return None
        pts.append((x / -z, y / -z))
    return pts


def _clip(subject, a, b):
    out = []
    for i, p in enumerate(subject):
        q = subject[(i + 1) % len(subject)]
        side = lambda r: (b[0] - a[0]) * (r[1] - a[1]) - (b[1] - a[1]) * (r[0] - a[0])
        sp, sq = side(p), side(q)
        if sp >= 0: out.append(p)
        if sp * sq < 0:
            t = sp / (sp - sq)
            out.append((p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t))
    return out


def _area(p):
    return abs(sum(p[i][0] * p[(i + 1) % len(p)][1] - p[(i + 1) % len(p)][0] * p[i][1] for i in range(len(p)))) / 2 if len(p) > 2 else 0.0


def overlap(a, b):
    """Area of desktops a and b's overlap on the view plane, over the smaller of the two; 0 if either is partly behind you."""
    pa, pb = _polygon(a), _polygon(b)
    if pa is None or pb is None: return 0.0
    if sum(pa[i][0] * pa[(i + 1) % 4][1] - pa[(i + 1) % 4][0] * pa[i][1] for i in range(4)) < 0: pa = pa[::-1]
    out = pb
    for i in range(4):
        if len(out) < 3: return 0.0
        out = _clip(out, pa[i], pa[(i + 1) % 4])
    small = min(_area(pa), _area(pb))
    return _area(out) / small if small > 0 else 0.0


class Motion:
    """What one push did. `frames`: the trace; home, target: the two desktops."""
    def __init__(self, frames, home, target):
        self.frames, self.home, self.target = frames, home, target
        self.t = [f['t'] / 1000 for f in frames]
        self.aim = [tuple(f['aim']) for f in frames]
        self.home_h = [f['desktops'][home]['seen'][1] for f in frames]
        self.home_w = [f['desktops'][home]['seen'][0] for f in frames]
        self.target_h = [f['desktops'][target]['seen'][1] for f in frames]
        self.target_w = [f['desktops'][target]['seen'][0] for f in frames]
        self.zoom = [f['zoom'] for f in frames]
        self.home_roll = [f['desktops'][home]['roll'] for f in frames]
        self.target_roll = [f['desktops'][target]['roll'] for f in frames]
        self.first = next((i for i, f in enumerate(frames) if f['switching']), 0)
        self.last = len(frames) - 1
        while self.last > 0 and not frames[self.last - 1]['switching'] and self._same(self.last - 1, self.last): self.last -= 1
        self.commit = next((i for i, f in enumerate(frames) if f['landing']), None)
        self.speed = [0.0] + [_angle(self.aim[i], self.aim[i + 1]) / max(self.t[i + 1] - self.t[i], 1e-6) for i in range(len(frames) - 1)]
        self.home_rate = self._rate(self.home_h)
        self.target_rate = self._rate(self.target_h)

    def _same(self, a, b):
        x, y = self.frames[a], self.frames[b]
        return x['aim'] == y['aim'] and all(p['seen'] == q['seen'] for p, q in zip(x['desktops'], y['desktops']))

    def _rate(self, values):
        return [0.0] + [math.log(values[i + 1] / values[i]) / max(self.t[i + 1] - self.t[i], 1e-6) for i in range(len(values) - 1)]

    def span(self, values):
        return values[self.first - 1 if self.first else 0:self.last + 1]

    def left(self):
        """The angle still to go, frame by frame: from the view's axis to where it ends."""
        return [_angle(a, self.aim[self.last]) for a in self.aim]

    def seconds(self):
        return self.t[self.last] - self.t[self.first]

    def stall(self, share=0.08):
        """The longest stretch in seconds, between the first and last moves, with the view all but still: under `share` of its
        peak speed while there is still more than 1 degree to go."""
        speed = self.speed[self.first:self.last + 1]
        if not speed: return 0.0
        peak, left = max(speed), self.left()[self.first:self.last + 1]
        moved = next((i for i, v in enumerate(speed) if v > peak * share), 0)
        best = run = 0.0
        for i in range(moved, len(speed)):
            if speed[i] < peak * share and left[i] > math.radians(1):
                run += self.t[self.first + i] - self.t[self.first + i - 1]
                best = max(best, run)
            else: run = 0.0
        return best

    def dip(self):
        """How far the view's speed falls between two faster stretches: 0 for one smooth rise and fall, 1 if it stops dead and
        starts again. The share of the lower of the two peaks lost in the valley between them."""
        speed = self.speed[self.first:self.last + 1]
        best = 0.0
        high = low = speed[0] if speed else 0.0
        for v in speed:
            if v > high and high == low: high = low = v
            elif v < low: low = v
            elif high > 0 and v > low: best = max(best, (min(high, v) - low) / max(min(high, v), 1e-6) if v > low * 1.5 else 0.0)
            if v > high: high = low = v
        return best

    def jolt(self):
        """The biggest change of the view's speed from one frame to the next, deg/s."""
        speed = self.speed[self.first:self.last + 2]
        return math.degrees(max((abs(speed[i + 1] - speed[i]) for i in range(len(speed) - 1)), default=0.0))

    def size_jolt(self):
        """The biggest step of a desktop's seen size in one frame, as a share of it."""
        out = 0.0
        for v in (self.home_h, self.target_h):
            s = self.span(v)
            out = max(out, max((abs(s[i + 1] / s[i] - 1) for i in range(len(s) - 1)), default=0.0))
        return out

    def order_pops(self):
        """Frames where home and target change places in the draw order while they overlap on the view: (overlap share, frame)."""
        out = []
        for i in range(1, len(self.frames)):
            a, b = self.frames[i - 1], self.frames[i]
            ha, ta = a['order'].index(self.home), a['order'].index(self.target)
            hb, tb = b['order'].index(self.home), b['order'].index(self.target)
            if (ha < ta) != (hb < tb):
                out.append((overlap(b['desktops'][self.home], b['desktops'][self.target]), i))
        return out

    def summary(self):
        floor = 0.002
        hs, ts = self.span(self.home_h), self.span(self.target_h)
        left = self.span(self.left())
        return {
            'seconds': self.seconds(), 'zoom_min': min(self.span(self.zoom)),
            'home': (hs[0], min(hs), max(hs), hs[-1]), 'target': (ts[0], min(ts), max(ts), ts[-1]),
            'home_turns': reversals([math.log(v) for v in hs], floor)[0], 'target_turns': reversals([math.log(v) for v in ts], floor)[0],
            'home_past': outside(hs), 'target_past': outside(ts),
            'back': reversals(left, math.radians(0.05))[1], 'stall': self.stall(), 'dip': self.dip(), 'jolt': self.jolt(), 'size_jolt': self.size_jolt(),
            'peak_deg_s': math.degrees(max(self.speed[self.first:self.last + 1])), 'pops': self.order_pops(),
        }
