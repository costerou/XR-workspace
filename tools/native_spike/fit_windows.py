"""A desktop that shrinks keeps its floating windows: the ones that would stick out are pulled back inside it.

Tiled windows re-tile to a resized output by themselves and Hyprland carries an output's floating windows along when it
slides along the row (CMonitor::moveTo), but nothing keeps a floating window inside an output that got smaller. So once a
change that shrinks a desktop's logical size is through (desktops.DesktopManager: a smaller Resolution, Ultrawide off, a
larger Scale), every floating window on that output's regular workspaces, shown or not, that sticks out of it moves the
least it takes to be inside, into the part of the output the bar leaves free. One larger than that also shrinks to fit.
A window that is inside already is left alone, and so are fullscreen ones (Hyprland sizes those), windows hidden in a
group (their group's shown window moves the group) and anything on a special workspace or another output.

plan() is pure, from Hyprland's `clients`, `workspaces` and `monitors` JSON. lua() is the generated eval
(hyprland.HyprlandClient.fit_windows): the same absolute hl.dsp.window.resize and move as Stop's restore.py, each in its
own pcall, resize first because Hyprland resizes a floating window about its centre. Only addresses and whole pixels
reach it. The windows stay on their output, so recovery and Stop are unchanged.
"""
import math

from .geometry import logical_height, logical_width, validate_desktop
from .send import ADDRESS

BATCH = 48     # windows in one eval: at the longest addresses and coordinates, still under the client's 4096-byte limit
MAX_COORD = 2147483647
_LUA = ('for _,e in ipairs(M) do pcall(function() local s="address:"..e[1] '
        'if e[4]>0 then hl.dispatch(hl.dsp.window.resize({x=e[4],y=e[5],window=s})) end '
        'hl.dispatch(hl.dsp.window.move({x=e[2],y=e[3],window=s})) end) end')


def _number(v):
    return type(v) in (int, float) and math.isfinite(v)


def _pair(v):
    return v if isinstance(v, list) and len(v) == 2 and all(_number(n) for n in v) else None


def free_area(spec, monitors):
    """Output `spec` (a broker spec) as (x, y, w, h) logical layout px, and the part of it Hyprland leaves free of the bar
    and other reserved edges (`monitors` JSON `reserved`: left, top, right, bottom). The whole output if that says nothing
    usable."""
    d = validate_desktop({k: spec[k] for k in ('width', 'height', 'scale')})
    whole = (spec['x'], spec['y'], logical_width(d), logical_height(d))
    x, y, w, h = whole
    found = [m for m in monitors if isinstance(m, dict) and m.get('name') == spec['name']]
    reserved = found[0].get('reserved') if len(found) == 1 else None
    if isinstance(reserved, list) and len(reserved) == 4 and all(type(v) is int and v >= 0 for v in reserved):
        left, top, right, bottom = reserved
        if w - left - right > 0 and h - top - bottom > 0: return whole, (x + left, y + top, w - left - right, h - top - bottom)
    return whole, whole


def plan(clients, workspaces, monitors, spec):
    """The moves that bring output `spec`'s floating windows inside it, as it is now: {'address', 'x', 'y', 'w', 'h'} (x, y
    the window's new top-left in layout px; w, h its new size, or 0 to keep it). See the module docstring."""
    if not all(isinstance(v, list) for v in (clients, workspaces, monitors)): raise ValueError('invalid compositor state')
    (dx, dy, dw, dh), (ux, uy, uw, uh) = free_area(spec, monitors)
    ids = {w['id'] for w in workspaces if isinstance(w, dict) and w.get('monitor') == spec['name']
           and type(w.get('id')) is int and w['id'] > 0}
    moves = []
    for c in clients:
        if not isinstance(c, dict) or c.get('floating') is not True or c.get('mapped') is False or c.get('hidden') is True: continue
        if c.get('fullscreen') not in (None, False, 0): continue
        workspace = c.get('workspace')
        if not isinstance(workspace, dict) or workspace.get('id') not in ids: continue
        if not isinstance(c.get('address'), str) or not ADDRESS.match(c['address']): continue
        at, size = _pair(c.get('at')), _pair(c.get('size'))
        if at is None or size is None or size[0] <= 0 or size[1] <= 0: continue
        x, y, w, h = (int(round(v)) for v in at + size)
        if dx <= x and dy <= y and x + w <= dx + dw and y + h <= dy + dh: continue  # inside already
        nw, nh = min(w, uw), min(h, uh)
        moves.append({'address': c['address'], 'x': max(ux, min(x, ux + uw - nw)), 'y': max(uy, min(y, uy + uh - nh)),
                      'w': nw if (nw, nh) != (w, h) else 0, 'h': nh if (nw, nh) != (w, h) else 0})
    return moves


def _validated(moves):
    if not isinstance(moves, list): raise ValueError('invalid window moves')
    for m in moves:
        if not isinstance(m, dict) or set(m) != {'address', 'x', 'y', 'w', 'h'} or not isinstance(m['address'], str) \
                or not ADDRESS.match(m['address']): raise ValueError('invalid window moves')
        if any(type(m[k]) is not int or abs(m[k]) > MAX_COORD for k in ('x', 'y')) \
                or any(type(m[k]) is not int or not 0 <= m[k] <= MAX_COORD for k in ('w', 'h')) or (m['w'] == 0) != (m['h'] == 0):
            raise ValueError('invalid window moves')
    return moves


def lua(moves):
    """The evals for `moves` (plan()), BATCH windows each; ValueError for anything else."""
    moves = _validated(moves)
    out = []
    for i in range(0, len(moves), BATCH):
        rows = ','.join(f'{{"{m["address"]}",{m["x"]},{m["y"]},{m["w"]},{m["h"]}}}' for m in moves[i:i + BATCH])
        out.append(f'local M={{{rows}}} ' + _LUA)
    return out
