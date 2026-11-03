"""Scroll before switch: zoomed in on a scrolling-layout workspace, a push at the selected desktop's left or right edge
scrolls its columns first and switches desktops only once there are none left that way.

Design (scroll-before-switch, 2026-11-03; Cody: "In hyprland scrolling monitors, make sure it prioritizes the scrolling
before switching to the next monitor"). unified_cursor.EdgePush asks ColumnScroll.held on every sample with the cursor
at or past home's left / right edge; True holds the push there:
  * Which workspaces. The one active on home's monitor (j/monitors activeWorkspace) when j/workspaces gives it
    tiledLayout "scrolling" and no fullscreen window (hasfullscreen). Master, dwindle, a floating-only workspace, a
    fullscreen window, a monitor or workspace not found and any failed read are not scrolling: the push is exactly as
    before (EdgePush). Only the left and right edges: up and down are never asked.
  * End of columns, explicitly (columns_past). Home's logical rectangle from j/monitors (x, width / scale; width and
    height swapped for a 90 / 270 degree transform) and the workspace's tiled clients from j/clients (that workspace
    id, not floating, mapped, not hidden). A client is past the right edge when at.x + size.x > right + TOLERANCE_PX,
    past the left one when at.x < left - TOLERANCE_PX (1 px). None past an edge: the view is at its last (first)
    column that way. This is scroll-edge.py's own test (below), so the two never disagree about the end.
  * The hold. While a client is past that edge the push counts for nothing toward a switch and the cursor stays in
    the edge band (the last EDGE_PX, scroll-edge.py's band; a cursor pushed over onto the next monitor comes back 1 px
    inside the outermost pixel, unified_cursor.hold_point). The columns scroll one at a time: the nearest client past
    the edge gets the focus with cursor.no_warps for that one focus (scroll_lua, as scroll-edge.py does, so the
    layout brings it into view and the cursor stays at the edge), SCROLL_DWELL_S after the cursor reached the edge and
    then every SCROLL_REPEAT_S while it stays there.
  * The end, and the re-push rule. Once nothing is left past the edge, the push stays held for END_REST_S after the
    layout last changed (the scroll that brought the last column in), so a hand still pushing sees the last column
    land and does not carry straight on to the next desktop. After that the push counts from nothing as every push
    does (EdgePush.PUSH_PX): pushing on, or pushing again later, switches. A view already at its end (no change for
    END_REST_S) switches on the first push, as before. Right before a push switches it is read once more
    (confirm): a client found past the edge then holds it again, so no switch happens while a column is left.
  * scroll-edge.py (the desktop's own edge scroller, ~/.config/hypr/scripts/scroll-edge.py, outside this repo: polls
    j/cursorpos at 30 Hz and, with the cursor within 2 px of a scrolling workspace's left / right edge and a tiled client
    past it, focuses that client, then again every 0.35 s while held). The hold keeps the cursor inside its band, so
    while it acts on the XR outputs it does the scrolling and this yields: any change to the layout this did not make
    (scroll-edge.py, its click-to-reveal, a window opening) counts as the step, and after one this waits HELPER_GRACE_S
    (past scroll-edge.py's 0.35 s) before scrolling itself, reading the layout again first. Without it (not running,
    or with docs/scroll-edge-skip-xr.patch, which leaves XR-n edges to this) this scrolls. One step scrolls twice only
    if scroll-edge.py takes longer than SCROLL_DWELL_S to react (a loaded machine); after that it yields to it.
  * Cheap. Nothing is read with the cursor away from home's left / right edge. At one: j/monitors and j/workspaces,
    and j/clients only for a scrolling workspace, at most every REFRESH_S, and once more right before a scroll (and
    after it, the layout it landed on) or a switch.
"""
import math
import re

TOLERANCE_PX = 1         # a client this far past an edge, or less, is not past it (scroll-edge.py's own +1 / -1)
EDGE_PX = 2              # the last 2 px at the left / right edge are "at the edge" (scroll-edge.py EDGE_PX)
REFRESH_S = 0.1          # held at an edge: the layout is read again after this long
CONTINUOUS_S = 0.5       # a layout change counts as just now only if the read before it is this recent
SCROLL_DWELL_S = 0.15    # at an edge this long before this scrolls the first column (scroll-edge.py acts well within it)
SCROLL_REPEAT_S = 0.35   # then a column each time this long (scroll-edge.py COOLDOWN)
HELPER_GRACE_S = 0.8     # after a scroll this did not make, this long without another before this scrolls
OWN_SETTLE_S = 0.25      # this scroll's change to the layout is seen within this; none: that client does not move
END_REST_S = 0.6         # no column left: the push rests this long after the layout last changed
HOLD_GAP_S = 0.1         # a hold no sample has touched for this long is over (the cursor left the edge)
QUERY_TIMEOUT_S = 0.25   # each read, from the sampler thread (its cursorpos read has the same)
ADDRESS = re.compile(r'0x[0-9a-f]{1,16}\Z')
SIDES = ('left', 'right')


def _number(v):
    return type(v) in (int, float) and math.isfinite(v)


def _pair(v):
    return isinstance(v, list) and len(v) == 2 and all(_number(n) for n in v)


def logical_rect(m):
    """Monitor `m`'s (j/monitors) layout rectangle (x, y, w, h) in logical px, as scroll-edge.py computes it."""
    w, h, x, y, scale = (m.get(k) for k in ('width', 'height', 'x', 'y', 'scale'))
    transform = m.get('transform', 0)
    if not all(_number(v) for v in (w, h, x, y, scale)) or scale <= 0 or w <= 0 or h <= 0 or type(transform) is not int:
        raise ValueError('invalid monitor geometry')
    if transform % 2: w, h = h, w
    return x, y, w / scale, h / scale


def columns_past(rect, clients, workspace):
    """The tiled clients of `workspace` past the left and right edge of `rect` (logical_rect), nearest first (the one
    to focus next), and the layout as it is (every tiled client's address, position and size, to see it change):
    {'left': (address, ...), 'right': (...), 'layout': (...)}. Raises ValueError on a malformed tiled client."""
    x, _, w, _ = rect
    right = x + w
    if not isinstance(clients, list): raise ValueError('invalid clients')
    tiled = []
    for c in clients:
        if not isinstance(c, dict) or not isinstance(c.get('workspace'), dict) or c['workspace'].get('id') != workspace:
            continue
        if c.get('floating') or not c.get('mapped', True) or c.get('hidden'): continue
        if not isinstance(c.get('address'), str) or not ADDRESS.match(c['address']) or not _pair(c.get('at')) or not _pair(c.get('size')):
            raise ValueError('invalid client')
        tiled.append(c)
    past_right = sorted((c for c in tiled if c['at'][0] + c['size'][0] > right + TOLERANCE_PX), key=lambda c: c['at'][0])
    past_left = sorted((c for c in tiled if c['at'][0] < x - TOLERANCE_PX), key=lambda c: -(c['at'][0] + c['size'][0]))
    return {'left': tuple(c['address'] for c in past_left), 'right': tuple(c['address'] for c in past_right),
            'layout': tuple(sorted((c['address'], *c['at'], *c['size']) for c in tiled))}


def read(client, name, timeout=QUERY_TIMEOUT_S):
    """Monitor `name`'s columns now: {'name', 'workspace', 'scrolling', 'left', 'right', 'layout'} (left / right / layout
    empty when not scrolling), or None (no such monitor, or disabled). Raises on a failed or malformed read."""
    monitors = client.query('monitors', timeout=timeout)
    if not isinstance(monitors, list): raise ValueError('invalid monitors')
    m = next((m for m in monitors if isinstance(m, dict) and m.get('name') == name), None)
    if m is None or m.get('disabled'): return None
    ws = m['activeWorkspace']['id']
    if type(ws) is not int: raise ValueError('invalid workspace')
    rect = logical_rect(m)
    workspaces = client.query('workspaces', timeout=timeout)
    if not isinstance(workspaces, list): raise ValueError('invalid workspaces')
    w = next((w for w in workspaces if isinstance(w, dict) and w.get('id') == ws), None)
    out = {'name': name, 'workspace': ws, 'scrolling': False, 'left': (), 'right': (), 'layout': ()}
    if w is None or w.get('tiledLayout') != 'scrolling' or w.get('hasfullscreen'): return out
    out.update(columns_past(rect, client.query('clients', timeout=timeout), ws), scrolling=True)
    return out


def scroll_lua(address):
    """Bring window `address`'s column into view: focus it, as scroll-edge.py does, with cursor.no_warps on for that one
    focus (the cursor stays at the edge, not warped to the window), the option put back as it was. Generated Lua."""
    if not isinstance(address, str) or not ADDRESS.match(address): raise ValueError('invalid window address')
    return ('do local ok, w = pcall(hl.get_config, "cursor.no_warps") '
            'pcall(hl.config, { cursor = { no_warps = true } }) '
            f'pcall(function() hl.dispatch(hl.dsp.focus({{ window = "address:{address}" }})) end) '
            'pcall(hl.config, { cursor = { no_warps = ok and w == true } }) end')


class ColumnScroll:
    """The sampler thread's scroll-before-switch state (module docstring). `client`: query(kind, timeout=) and
    scroll_to_window(address) (hyprland.HyprlandClient). One instance per run, used from the sampler thread only."""
    def __init__(self, client):
        self.client = client
        self.snap, self.read_name, self.read_at = None, None, -math.inf  # last read (None: none, or it failed)
        self.hold = None            # [name, side, since, last sample]: the cursor at / past that edge of home
        self.changed = -math.inf    # when the layout was last seen to change
        self.foreign = False        # that change was not this one's scroll (scroll-edge.py, a reveal, a new window)
        self.own = None             # (workspace, layout before, at): this one's scroll, its change not seen yet
        self.stuck = None           # (workspace, layout): a scroll that moved nothing; nothing past while it stays so
        self.scrolls = self.yields = self.reads = self.errors = 0  # diagnostics

    def held(self, name, side, now, confirm=False):
        """The cursor is at, or pushed past, monitor `name`'s `side` ('left' / 'right') edge. True: the push is held
        there and counts for nothing (a column is left that way, scrolled here unless scroll-edge.py does; or the end
        came under END_REST_S ago). `confirm`: a push about to switch: read again first, and only answer."""
        if side not in SIDES: raise ValueError('scrolling is left and right only')
        h = self.hold
        if h is None or h[0] != name or h[1] != side or now - h[3] > HOLD_GAP_S: h = self.hold = [name, side, now, now]
        h[3] = now
        snap = self._read(name, now, fresh=confirm)
        if snap is None or not snap['scrolling']: return False
        if self._past(snap, side):
            if not confirm: self._scroll(name, side, now)
            return True
        return now - self.changed < END_REST_S

    def _past(self, snap, side):
        if self.stuck == (snap['workspace'], snap['layout']): return ()
        return snap[side]

    def _read(self, name, now, fresh=False):
        """The layout of monitor `name`, read at most every REFRESH_S unless `fresh`; None if not readable. A change
        from the read before (same workspace, that read CONTINUOUS_S old at most) is a change just now."""
        if not fresh and self.read_name == name and now - self.read_at < REFRESH_S: return self.snap
        prev, prev_at = (self.snap, self.read_at) if self.read_name == name else (None, -math.inf)
        try:
            snap = read(self.client, name)
        except (OSError, ValueError, TypeError, KeyError, AttributeError, IndexError):
            self.errors += 1
            snap = None
        self.reads += 1
        self.snap, self.read_name, self.read_at = snap, name, now
        own = self.own
        if own is not None and now - own[2] > OWN_SETTLE_S:  # the focus moved nothing: that client cannot come in
            self.stuck, self.own = (own[0], own[1]), None
        if snap is None or prev is None or prev['workspace'] != snap['workspace']:
            # Nothing to compare with (a new monitor or workspace, a failed read): a new start, nothing changed yet.
            self.changed, self.foreign, self.own = -math.inf, False, None
            if snap is None or self.stuck is None or self.stuck[0] != snap['workspace']: self.stuck = None
        elif snap['layout'] != prev['layout'] and now - prev_at <= CONTINUOUS_S:
            self.foreign = self.own is None  # after this one's scroll, the change is that scroll's
            if self.foreign: self.yields += 1
            self.changed, self.own = now, None
        return snap

    def _scroll(self, name, side, now):
        """Held with a column left that way: scroll it in, unless it is not time yet or scroll-edge.py just did."""
        if self.own is not None: return  # the last scroll's change is not seen yet
        wait = HELPER_GRACE_S if self.foreign else SCROLL_REPEAT_S
        if now < self.hold[2] + SCROLL_DWELL_S or now < self.changed + wait: return
        before, ws = self.changed, self.snap['workspace']
        snap = self._read(name, now, fresh=True)  # the newest layout: scroll-edge.py may just have scrolled it
        if snap is None or not snap['scrolling'] or snap['workspace'] != ws or self.changed != before:
            return  # it did (that was this step), or the workspace changed under the hold: a new start
        past = self._past(snap, side)
        if not past: return
        try:
            self.client.scroll_to_window(past[0])
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            self.errors += 1
            return
        self.scrolls += 1
        self.own = (snap['workspace'], snap['layout'], now)
        self._read(name, now, fresh=True)  # Hyprland lays the columns out on the dispatch: normally seen at once
