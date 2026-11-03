"""Unified cursor: the dot is the one real cursor, and it only ever controls the selected desktop.

Design (dot-unified-cursor, 2026-11-02; replaces the input lock's held cursor for the run, input_lock.py; scope change the
same day: the cursor never switches desktops or monitors; dot-directional-switch, 2026-11-03: zoomed in, a push past
home's edge switches to the desktop placed that way):
  * Home. The real cursor belongs to the selected desktop's monitor (view.ViewState.selected, "home"), zoomed in and in
    the overview. It never crosses onto another monitor in Hyprland's layout by hand: switching desktops is a key
    (Ctrl+Alt+Left / Right), a click on a desktop in the overview, which zooms into it, or, zoomed in, a push past home's
    edge (below). Cursor follow (Ctrl+Alt+F) is gone (view.py).
  * Zoomed in (and in typing mode or during a VR video, whenever the overview pointer is off): the sampler keeps the
    cursor on home (confine_target). A cursor pushed onto another XR monitor, or off home onto the physical monitor,
    is put straight back at the nearest point inside home (INSET_PX in) and home gets Hyprland's monitor focus back
    (confine_lua). A jump of more than free_pointer.JUMP_PX onto the physical monitor is a warp, not the hand (a window
    sent back with Ctrl+Alt+B): it is left there. A cursor already on the physical monitor is left alone until it comes
    onto an XR monitor, and then goes to home.
  * Zoomed in, the push past an edge (EdgePush; not in typing mode or during a VR video). If a desktop is placed past
    that edge of home round you in 3D (neighbors.py: by direction from the eye, a cone round the push and the nearest;
    not the row order), pushing PUSH_PX past the edge switches to it: the loop selects it (ViewControl.switch_desktop,
    as Ctrl+Alt+Left / Right: the renderer zooms out, turns and zooms in, in anchor mode panning straight to it and
    centring it in view; every desktop stays where it is placed) and the
    sampler brings the cursor in just inside the opposite edge of the new home, as far along it as it left. Over an
    edge Hyprland lets it cross the overshoot counts; at an edge Hyprland clamps it at (the layout's outer edge, e.g.
    the top of the row) each touch counts NUDGE_PX and puts it that far back in, so a hand still pushing touches
    again. Nothing placed that way: the plain confinement above, and the cursor stays on home at the edge.
  * Overview, over home (free_pointer desktop mode). The real cursor is the pointer: hand motion moves it, and the dot is
    drawn where it maps onto home's surface (free_pointer._from_logical; every viewing mode). It is hidden
    (overview_cursor.py), but hover, focus-follows-mouse, every button and the wheel reach the window under it. The left
    button is bound non-consuming (click_binds_lua: the window gets it as usual) to resize_press / resize_release: a
    press and release with the dot no more than free_pointer.CLICK_PX apart is a click and zooms into home (the
    Ctrl+Alt+Up path). A drag or a selection does not zoom. While the button is held the dot never turns free and the
    cursor stays on home (pulled back if Hyprland let it onto another monitor), so the window gets its release.
    Window drag (free_pointer.py): holding the button free_pointer.HOLD_S with the dot no more than CLICK_PX from the
    press lifts the window under it instead (_lift_home): the dot turns free right there, as at an edge (the grab
    parks the hidden cursor and takes the release), the ghost follows it, and the release over another desktop moves
    the window there. Moving first is the window's own drag or selection and never lifts it.
  * Overview, off home (free mode). The cursor reaching home's edge pixel turns the dot free: the cursor is hidden and
    parked just inside home where it left (input_lock.park_point; leash input_lock.LEASH_PX), and its motion moves the
    dot through space. Over pills, corners, panels, the help card and the other desktops it is only the dot: the free
    grab is the input lock's (input_lock.lock_pointer_lua), so no button or wheel reaches a window, and the dot on
    another desktop neither selects it nor moves the cursor there. A click on another desktop zooms into it
    (free_pointer._click_desktop): that is how the cursor changes monitor. Moving ENTER_PX back into home hands the
    real cursor back there, under the dot.
  * Keys. Ctrl+Alt+Left / Right change the selected desktop: home moves with it, and the cursor and dot go to the middle
    of the new home (OverviewPointer desktop mode there; zoomed in, the confinement moves it). Any other jump of the
    cursor off home in the overview is pulled back home, or let go (away) if it landed on the physical monitor.
  * Compositor state. One bind table (resize.BIND_TABLE) holds whichever set is live: the click binds over home, the
    lock grab while free, and every switch replaces it in a single eval (enter_desktop_lua / the lock grab). Run end
    (PointerSampler.stop), a config reload (re-applied) and a dead run (its next left click still reaches the window and
    runs the bind helper, which removes the whole table) clean it up as before.
"""
import math
import time

from . import free_pointer as fp, input_lock as il, neighbors, resize, view

# Zoomed in, pushing past an edge of home toward a desktop placed that way switches to it (EdgePush, neighbors.py).
PUSH_PX = 48              # logical px of push past one edge, each part within PUSH_WINDOW_S of the last, that switch
PUSH_WINDOW_S = 0.25      # a pause longer than this starts the push over
NUDGE_PX = 12             # an edge Hyprland clamps the cursor at (nothing beyond it in its layout): the cursor touching it
                          # is put back this far in, and each touch counts this much push (the hand's push is not seen)
SWITCH_COOLDOWN_S = 0.8   # after a switch no push counts for this long (the renderer's zoom out, turn, zoom in)
ENTRY_PX = 24             # the cursor enters the new desktop this far inside the edge it came in over
PENDING_S = 0.5           # a requested switch the loop has not made by then is dropped

CLICK_OPTS = '{ non_consuming = true }', '{ release = true, non_consuming = true }'


def click_binds_lua(python, client, control_socket, run_id, session):
    """Over home: the left button's press and release, non-consuming (the window still gets them), to the bind helper's
    resize_press / resize_release, added to the free pointer's bind table. pcall'd: a refused bind is left out, never
    replaced by a consuming one."""
    for p in (python, client, control_socket):
        if not isinstance(p, str) or not view.SAFE_PATH.match(p): raise ValueError('unsafe path for bind command')
    for t in (run_id, session):
        if not isinstance(t, str) or not view.SAFE_TOKEN.match(t): raise ValueError('unsafe token for bind command')
    base = f'{python} -I {client} {control_socket} {run_id} {session}'
    binds = ' '.join(f'local ok{i}, b{i} = pcall(hl.bind, "{resize.RESIZE_BUTTON}", hl.dsp.exec_cmd(c .. "{op}"), {opts}) '
                     f'if ok{i} and b{i} then t[#t + 1] = b{i} end'
                     for i, (op, opts) in enumerate(zip(resize.RESIZE_OPS, CLICK_OPTS)))
    return f'do local t = {resize.BIND_TABLE} or {{}} {resize.BIND_TABLE} = t local c = "{base} " {binds} end'


def enter_desktop_lua(python, client, control_socket, run_id, session, x=None, y=None):
    """Into desktop mode: any free-mode grab removed (the cursor's own visibility back; overview_cursor keeps it
    hidden), the cursor warped to layout pixel (x, y) if given, then the click binds. One eval."""
    return fp.release_pointer_lua(x, y) + ' ' + click_binds_lua(python, client, control_socket, run_id, session)


def confine_lua(x, y, name):
    """Zoomed in: the cursor back at layout pixel (x, y) on home and home's monitor focused again. Generated Lua."""
    if not isinstance(name, str) or not view.MANAGED.match(name): raise ValueError('confinement is limited to managed outputs')
    return view.warp_cursor_lua(x, y) + f' pcall(function() hl.dispatch(hl.dsp.focus({{ monitor = "{name}" }})) end)'


def _output_at(outputs, cursor):
    return next((o for o in outputs if 0 <= cursor[0] - o['x'] < o['width'] and 0 <= cursor[1] - o['y'] < o['height']), None)


def inside(o, cursor, inset=fp.INSET_PX):
    """The integer layout pixel of output `o` nearest `cursor`, at least `inset` px inside every edge."""
    return (int(min(max(cursor[0], o['x'] + inset), o['x'] + o['width'] - 1 - inset)),
            int(min(max(cursor[1], o['y'] + inset), o['y'] + o['height'] - 1 - inset)))


def confine_target(outputs, home, cursor, previous=None):
    """Zoomed in: where to put the cursor back on the home output (index `home` in `outputs`, resize.outputs_from_monitors
    entries), or None to leave it. On another XR output: back. Off the XR outputs (the physical monitor): back only if the
    previous sample was on home and it is within free_pointer.JUMP_PX of home (the hand, not a warp)."""
    o = next((o for o in outputs if o['index'] == home), None)
    if o is None or cursor is None: return None
    now = _output_at(outputs, cursor)
    if now is not None and now['index'] == home: return None
    if now is None:
        prev = _output_at(outputs, previous) if previous is not None else None
        if prev is None or prev['index'] != home: return None
        dx = max(o['x'] - cursor[0], 0, cursor[0] - (o['x'] + o['width'] - 1))
        dy = max(o['y'] - cursor[1], 0, cursor[1] - (o['y'] + o['height'] - 1))
        if math.hypot(dx, dy) > fp.JUMP_PX: return None
    return inside(o, cursor)


def _contact(o, cursor):
    """The edges of output `o` whose outermost pixel the cursor (on `o`) is on, horizontal first."""
    lx, ly = cursor[0] - o['x'], cursor[1] - o['y']
    sides = []
    if lx < 1: sides.append('left')
    elif lx >= o['width'] - 1: sides.append('right')
    if ly < 1: sides.append('up')
    elif ly >= o['height'] - 1: sides.append('down')
    return sides


def _crossing(o, cursor):
    """(edge, px past it) of a cursor off output `o`: the axis it is furthest past."""
    lx, ly = cursor[0] - o['x'], cursor[1] - o['y']
    dx = lx - min(max(lx, 0), o['width'] - 1)
    dy = ly - min(max(ly, 0), o['height'] - 1)
    if abs(dx) >= abs(dy): return ('right' if dx > 0 else 'left'), abs(dx)
    return ('down' if dy > 0 else 'up'), abs(dy)


def _nudge(o, cursor, side):
    """The cursor NUDGE_PX back inside `o` from edge `side`, the other coordinate kept."""
    x, y = inside(o, cursor)
    if side == 'left': x = o['x'] + NUDGE_PX
    elif side == 'right': x = o['x'] + o['width'] - 1 - NUDGE_PX
    elif side == 'up': y = o['y'] + NUDGE_PX
    else: y = o['y'] + o['height'] - 1 - NUDGE_PX
    return inside(o, (x, y))


def along(o, cursor, side):
    """How far along edge `side` of `o` the cursor is, 0..1 (left / top to right / bottom)."""
    lx, ly = inside(o, cursor, 0)[0] - o['x'], inside(o, cursor, 0)[1] - o['y']
    if side in ('up', 'down'): return lx / max(o['width'] - 1, 1)
    return ly / max(o['height'] - 1, 1)


def entry_point(o, side, t, inset=ENTRY_PX):
    """Where the cursor enters output `o` after a push past the old desktop's `side` edge: just inside the opposite edge
    (pushed up, it comes in at the bottom), fraction `t` along it."""
    w, h = o['width'] - 1, o['height'] - 1
    if side == 'up': local = (round(t * w), h - inset)
    elif side == 'down': local = (round(t * w), inset)
    elif side == 'left': local = (w - inset, round(t * h))
    else: local = (inset, round(t * h))
    return inside(o, (o['x'] + local[0], o['y'] + local[1]))


class EdgePush:
    """Zoomed in: the confinement (confine_target) plus the push past an edge of home that switches desktops.

    step() takes each cursor sample and returns (warp, switch): warp, a layout pixel on home to put the cursor at, or
    None; switch, (target index, edge, fraction along it) once the push past one edge reached PUSH_PX, or None.
    `neighbour(edge)` gives the desktop placed past home's edge, or None (no switch that way: plain confinement).
      * Past an edge Hyprland lets it over (onto another monitor): the overshoot counts and the cursor goes back, as
        confine_target does. A jump of more than free_pointer.JUMP_PX is a warp, never a push.
      * On an edge Hyprland clamps it at (its outermost pixel, nothing beyond in the layout), with a neighbour that way:
        the touch counts NUDGE_PX and the cursor is put NUDGE_PX back in, so a hand still pushing touches again.
      * The push is per edge and starts over after PUSH_WINDOW_S without one, or on another edge; after a switch none
        counts for SWITCH_COOLDOWN_S."""
    def __init__(self):
        self.edge, self.amount, self.last, self.quiet_until = None, 0.0, None, -math.inf

    def reset(self):
        self.edge, self.amount, self.last = None, 0.0, None

    def step(self, outputs, home, cursor, previous, now, neighbour):
        o = next((o for o in outputs if o['index'] == home), None)
        if o is None or cursor is None: return None, None
        at = _output_at(outputs, cursor)
        if at is not None and at['index'] == home:
            sides = [s for s in _contact(o, cursor) if neighbour(s) is not None]
            if not sides or now < self.quiet_until: return None, None
            side, amount = sides[0], NUDGE_PX
            warp = _nudge(o, cursor, side)
        else:
            warp = confine_target(outputs, home, cursor, previous)
            if warp is None: return None, None
            side, amount = _crossing(o, cursor)
            if amount > fp.JUMP_PX or now < self.quiet_until or neighbour(side) is None: return warp, None
        if side != self.edge or self.last is None or now - self.last > PUSH_WINDOW_S: self.edge, self.amount = side, 0.0
        self.amount += amount
        self.last = now
        if self.amount < PUSH_PX: return warp, None
        target = neighbour(side)
        self.reset()
        self.quiet_until = now + SWITCH_COOLDOWN_S
        return warp, (target, side, along(o, cursor, side))


class UnifiedPointer(fp.OverviewPointer):
    """The overview free pointer with the real cursor on home (module docstring)."""
    def __init__(self, control, clock=time.monotonic):
        super().__init__(control, clock)
        self.click = None  # over home, left button down: {'start', 'at', 'moved'} (the dot and time at the press)
        self.rehomes = self.pullbacks = self.window_clicks = self.follows = 0  # diagnostics
        self._switch = None  # zoomed in: (home, target) the sampler asked the loop to switch to (EdgePush)
        self.edge_switches = 0  # diagnostics

    # --- zoomed in: the push past an edge -------------------------------------------------------------------------
    def can_switch(self):
        """A push past home's edge switches desktops: zoomed in, not in typing mode and with no VR video (as the
        Ctrl+Alt+Left / Right keys, whose binds typing mode and a video release)."""
        s = self.control.state
        return s.zoomed and not getattr(s, 'typing', False) and not getattr(self.control, 'video_active', False)

    def neighbour(self, edge):
        """The desktop placed past home's `edge` round you (neighbors.pick on the renderer's overview layout and the
        placements as drawn), or None. Without a layout with one slot per desktop: the row order (neighbors.row_fallback).
        Read from the sampler thread: the layout and placements are replaced, never changed in place."""
        s = self.control.state
        home, count, g = s.selected, len(s.names), self.geometry
        if g is None or len(g['tiles']) != count: return neighbors.row_fallback(count, home, edge)
        return neighbors.pick(neighbors.frames(g, self._rolled()), home, edge)

    def request_switch(self, home, target):
        """Sampler thread: the push past home's edge reached a neighbour; the loop switches (poll)."""
        with self._lock: self._switch = (home, target)
        self._poke()

    def _take_switch(self):
        with self._lock: req, self._switch = self._switch, None
        if req is None or not self.can_switch() or self._home() != req[0]: return b''
        out = self.control.switch_desktop(req[1])
        if out: self.edge_switches += 1
        return out

    def _home(self):
        return self.control.state.selected

    # --- modes ----------------------------------------------------------------------------------------------------
    def poll(self, now):
        if not self.active() or now - self.last_move >= view.OVERLAY_IDLE_CLOSE_S: self.click = None
        out = self._take_switch()
        if self.active() and self.mode in fp.DOT_MODES and self.desktop != self._home():
            out += self._to_home(now)  # a key changed the selected desktop: the cursor goes with it
            self.rehomes += 1
        elif self.mode == 'free' and self.active():
            out += self._follow(now)
        out += super().poll(now)
        if self.click is not None and self.mode == 'desktop' and self.active(): out += self._hold_home(now)
        return out

    def _to_home(self, now):
        """Desktop mode in the middle of home, the cursor warped there (a key switch, or a cursor that came onto
        another XR monitor)."""
        i = self._home()
        o = self._output(i)
        out = self._end_drag()
        if o is None:
            self.mode, self.desktop, self.pointer = 'away', None, None
            return out
        self.mode, self.desktop, self.hover, self.click = 'desktop', i, None, None
        self.pointer = self._from_logical(i, o['width'] / 2, o['height'] / 2)
        self.last_move = now
        self._set_want('desktop', inside(o, (o['x'] + o['width'] / 2, o['y'] + o['height'] / 2)), sync=True)
        return out

    def _absolute(self, cursor, now):
        """Away / desktop mode: the cursor's own position decides, on home only."""
        home = self._home()
        o = self._output(home)
        if o is None:
            self.mode, self.desktop, self.pointer = 'away', None, None
            return self._wants()
        at = self._at(cursor)
        if self.mode == 'away':
            if at is None and not self.summoned: return self._wants()
            self.summoned = False
            if at is None or at[0] != home: return self._to_home(now) + self._wants()  # Ctrl+Alt+Down, or another XR monitor
            self.mode = 'desktop'
        self.summoned = False
        self.desktop = home
        lx, ly = cursor[0] - o['x'], cursor[1] - o['y']
        w, h = o['width'], o['height']
        if fp.EDGE_PX <= lx <= w - 1 - fp.EDGE_PX and fp.EDGE_PX <= ly <= h - 1 - fp.EDGE_PX:
            point = self._from_logical(home, lx, ly)
            if point is not None and point != self.pointer: self.pointer, self.last_move = point, now
            self._onto_help()
            return self._wants()
        cx, cy = min(max(lx, 0), w - 1), min(max(ly, 0), h - 1)
        over = (lx - cx, ly - cy)
        if self.click is not None:  # held: the window keeps the cursor, which stays on home
            if at is None or at[0] != home:
                self.pullbacks += 1
                self._set_want('desktop', inside(o, cursor), sync=True)
            point = self._from_logical(home, cx, cy)
            if point is not None and point != self.pointer: self.pointer, self.last_move = point, now
            return self._wants()
        if math.hypot(*over) > fp.JUMP_PX:  # not the hand
            if at is None:  # a window sent back to the physical monitor took the cursor: let go
                self.mode, self.desktop, self.pointer = 'away', None, None
                return self._wants()
            self.pullbacks += 1
            return self._to_home(now) + self._wants()
        start = self._from_logical(home, cx, cy)
        if start is None: return self._wants()
        self.gain = self._gain(home)
        self.pointer = self._clamp((start[0] + over[0] * self.gain, start[1] + over[1] * self.gain))
        self.mode, self.last_move = 'free', now
        self.exits += 1
        self._set_want('free', self._park(o, (cx, cy)))  # the grab parks it back on home
        self.hover = self._hit()
        return self._wants()

    def _wants(self):
        """The click binds go with desktop mode, and nothing is bound away from the desktops."""
        if self.mode == 'desktop' and self.want[1] != 'desktop': self._set_want('desktop')
        elif self.mode == 'away' and self.want[1] not in ('away', 'off'): self._set_want('away')
        return b''

    def _onto_help(self):
        if self.click is None: super()._onto_help()  # a held button stays with its window until released

    def _park(self, o, local=None):
        """Free mode holds the hidden cursor just inside home where it left (input_lock.park_point), not in the middle:
        with focus-follows-mouse the window it was over keeps the focus."""
        cursor = (o['x'] + local[0], o['y'] + local[1]) if local is not None else self._abs
        return il.park_point(o, cursor)

    def _enter(self, i, q):
        """The free dot went into desktop i: only home takes the real cursor back. Another desktop stays the dot's (a
        click there zooms into it); it is not selected."""
        return super()._enter(i, q) if i == self._home() else b''

    def _follow(self, now):
        """Free mode: a parked cursor more than JUMP_PX away was moved by something other than the hand. On home: the
        real cursor is the pointer there again. Elsewhere on the XR monitors: parked back on home. Off them: let go."""
        _, kind, park = self.want
        with self._lock: cursor, applied = self._abs, self._applied
        if kind != 'free' or park is None or cursor is None or applied < self._sync: return b''
        if math.hypot(cursor[0] - park[0], cursor[1] - park[1]) <= fp.JUMP_PX: return b''
        self.follows += 1
        at = self._at(cursor)
        if at is not None and at[0] != self._home():
            self.pullbacks += 1
            self._set_want('free', park, sync=True)  # the grab warps it back to its parking point
            return b''
        out = self._end_drag()
        self.hover = None
        if at is None:
            self.mode, self.desktop, self.pointer = 'away', None, None
            self._set_want('away')
            return out
        self.mode = 'desktop'
        self.pointer = self._from_logical(*at)
        self._set_want('desktop')  # the grab goes, the click binds come back; the cursor stays where it is
        return out

    # --- the left button ------------------------------------------------------------------------------------------
    def on_op(self, op):
        if op not in resize.RESIZE_OPS: raise ValueError('unsupported resize operation')
        if self.mode == 'desktop' or (op == 'resize_release' and self.click is not None):
            return self._desktop_button(op)
        self.click = None
        return super().on_op(op)

    def _desktop_button(self, op):
        """Over home, the non-consuming left button: the window already has it; a click (no drag) also zooms in."""
        now = self.clock()
        idle = now - self.last_move >= view.OVERLAY_IDLE_CLOSE_S
        self.last_move = now  # input: the dot and pills come back
        out = b''
        live = self.active() and self.mode == 'desktop' and self.pointer is not None
        if op == 'resize_press':
            self.click = {'start': self.pointer, 'at': now, 'moved': False} if live and not idle else None
        else:
            c, self.click = self.click, None
            self.window_clicks += 1
            if c is not None and live and math.hypot(self.pointer[0] - c['start'][0], self.pointer[1] - c['start'][1]) <= fp.CLICK_PX:
                with self._lock: cursor = self._abs
                self.focus_clicks += 1
                self.release_at = (int(cursor[0]), int(cursor[1])) if cursor is not None else None  # stays where clicked
                out += self.control.focus_desktop(self._home())
        return (out + self._handle() + self._pills(False) + self._pointer_frame(False)) or None


    def _hold_home(self, now):
        """Over home with the button down: moving more than CLICK_PX first is the window's own drag (never lifts); held
        HOLD_S still, the window under the cursor lifts (_lift_home)."""
        c = self.click
        if c['moved'] or self.pointer is None or c['start'] is None: return b''
        if math.hypot(self.pointer[0] - c['start'][0], self.pointer[1] - c['start'][1]) > fp.CLICK_PX:
            c['moved'] = True
            return b''
        if now - c['at'] < fp.HOLD_S: return b''
        return self._lift_home(c, now)

    def _lift_home(self, c, now):
        """The held press on home becomes a window drag, and the dot turns free where it is (the grab hides and parks the
        cursor and takes the left button's release, which ends the drag in OverviewPointer.on_op)."""
        home = self._home()
        o, tile, p = self._output(home), self._tile(home), self._placement(home)
        with self._lock: cursor = self._abs
        if o is None or tile is None or p is None or cursor is None: return b''
        q = fp.unproject(self.geometry, tile, p, self.pointer) or (tile[0] + tile[2] / 2, tile[1] + tile[3] / 2)
        self.click = None
        self.drag = self._window_press(home, q, self.pointer, (int(cursor[0]), int(cursor[1])), c['at'])
        self._lift(self.drag)
        self.drag['target'] = fp.desktop_at(self.geometry, self._rolled(), self.pointer)
        self.gain = self._gain(home)
        self.mode, self.desktop, self.hover, self.last_move = 'free', home, None, now
        self.exits += 1
        self._set_want('free', self._park(o, (cursor[0] - o['x'], cursor[1] - o['y'])))
        return self._frames(False)


class UnifiedSampler(il.LockedSampler):
    """The free pointer's sampler for the unified cursor. In the overview: free mode grabs with the lock's grab (short
    leash, warps left to the loop), desktop mode installs the non-consuming click binds and warps the cursor where the
    dot went in. Outside it: the cursor kept on home (confine_target), sampled at the overview's rate while it is on an
    XR monitor."""
    def __init__(self, client, names, pointer, bind_args, period=fp.SAMPLE_S):
        super().__init__(client, names, pointer, bind_args, period)
        self.bound = False   # the click binds are installed (desktop mode)
        self.confines = 0    # diagnostics: cursors put back on home while zoomed in
        self._outside = None  # previous cursor sample outside the overview
        self.push = EdgePush()  # zoomed in: the push past home's edge that switches desktops
        self._pending = None    # (target, edge, fraction along it, asked at): a switch asked of the loop, not yet seen

    def _run(self):
        outputs, refreshed = [], None
        while not self._stop.is_set():
            now = time.monotonic()
            try:
                if refreshed is None or self.names_changed or now - refreshed >= fp.OUTPUTS_REFRESH:
                    self.names_changed = False  # a pill's Add / Remove (set_names): read the monitors again at once
                    outputs = resize.outputs_from_monitors(self.client.query('monitors', timeout=0.5), self.names)
                    refreshed = now
            except (OSError, ValueError, TypeError, KeyError):
                self.errors += 1
            if not self.pointer.overview():
                self._release()
                self.cursor.step(self.client, False)  # the normal cursor outside the overview
                self.applied = None  # re-apply the current want when the overview is back
                self._last = None
                fast = self._confine(outputs)
                self._stop.wait(self.period if fast else fp.IDLE_S)
                continue
            self._outside = None
            self._apply()
            self.cursor.step(self.client, self.pointer.mode in fp.DOT_MODES)  # hidden while the dot is the pointer
            cursor, motion = self._sample()
            self.pointer.feed(cursor, motion, outputs, self.applied if self.applied is not None else -1)
            self._recentre(cursor)
            self._stop.wait(self.period)

    def _confine(self, outputs):
        """Outside the overview: put a cursor that left home back on it, and switch desktops on a push past home's edge
        toward one (EdgePush). True while it is on an XR monitor (sample fast)."""
        if not outputs: return False
        try:
            pos = self.client.query('cursorpos', timeout=0.25)
            x, y = pos['x'], pos['y']
            if type(x) not in (int, float) or type(y) not in (int, float) or not math.isfinite(x) or not math.isfinite(y):
                raise ValueError('cursor position')
        except (OSError, ValueError, TypeError, KeyError):
            self.errors += 1
            return False
        cursor, now = (x, y), time.monotonic()
        home = self.pointer.control.state.selected
        if self._pending is not None:
            target, edge, t, asked = self._pending
            o = next((o for o in outputs if o['index'] == target), None)
            if home == target and o is not None:  # the loop switched: the cursor comes in over the matching edge
                self._pending = None
                entry = entry_point(o, edge, t)
                try:
                    self.client.confine_cursor(*entry, self.names[target])  # also gives the new home monitor focus
                    cursor = entry
                except (OSError, ValueError, TypeError, KeyError, AttributeError, IndexError):
                    self.errors += 1
                self._outside = cursor
                return True
            if home == target or now - asked >= PENDING_S: self._pending = None
        live = self._pending is None and self.pointer.can_switch()
        warp, switch = self.push.step(outputs, home, cursor, self._outside, now,
                                      self.pointer.neighbour if live else (lambda edge: None))
        if not live: self.push.reset()
        if warp is not None:
            try:
                self.client.confine_cursor(*warp, self.names[home])
                cursor = warp
                self.confines += 1
            except (OSError, ValueError, TypeError, KeyError, AttributeError, IndexError):
                self.errors += 1
        if switch is not None and 0 <= switch[0] < len(self.names):
            self._pending = (*switch, now)
            self.pointer.request_switch(home, switch[0])
        self._outside = cursor
        return _output_at(outputs, cursor) is not None

    def _apply(self):
        serial, kind, point = self.pointer.want
        if self.pointer.binds_dropped:  # a config reload removed the binds and showed the cursor
            self.pointer.binds_dropped = False
            if self.grabbed or self.bound:
                now = time.monotonic()
                if self._regrabbed_at is None or now - self._regrabbed_at >= 2.0:  # never a reload loop
                    self._regrabbed_at = now
                    self.grabbed = self.bound = False
                    self.applied = None
        if serial == self.applied: return
        try:
            if kind == 'free':
                if point is None: return
                self.ever_grabbed = True  # set first: a failed or partial grab is still undone at stop
                self.client.grab_pointer(*self.bind_args, *point)  # the lock's grab (input_lock._LockingClient)
                self.grabbed, self.bound, self.park, self._last = True, False, point, point
            elif kind == 'desktop' and self.bound and not self.grabbed:  # the click binds are in: only the cursor moves
                if point is not None: self.client.warp_cursor(*point)
            elif kind == 'desktop':
                self.ever_grabbed = True
                self.client.enter_desktop(*self.bind_args, *(point or ()))
                self.grabbed, self.bound = False, True
                if point is not None: self._last = point
            elif kind == 'off':
                self._release()
            elif self.grabbed or self.bound:
                self.client.release_pointer()
                self.grabbed = self.bound = False
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            self.errors += 1
            return  # retried on the next sample
        self.applied = serial

    def _release(self):
        """Out of the overview: binds gone, the cursor shown where it is (after a click that zoomed in: where it was
        clicked, release_at)."""
        if not (self.grabbed or self.bound): return
        at = self.pointer.release_at
        try:
            if at is not None: self.client.release_pointer(*at)
            else: self.client.release_pointer()
            self.grabbed = self.bound = False
            self.pointer.release_at = None
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            self.errors += 1
