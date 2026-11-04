"""Unified cursor: the dot is the one real cursor, and in the overview every desktop under it is a normal screen.

Design (dot-unified-cursor, 2026-11-02; replaces the input lock's held cursor for the run, input_lock.py; scope change the
same day: the cursor never switches desktops or monitors; dot-directional-switch, 2026-11-03: zoomed in, a push past
home's edge switches to the desktop placed that way; anchor-no-click-maximize, 2026-11-03: in the overview the dot gives
the real cursor to whichever desktop it moves onto, so every window on every desktop takes clicks, drags, right-clicks,
scrolling and typing, and a click never maximizes):
  * Home. The real cursor belongs to the selected desktop's monitor (view.ViewState.selected, "home"), zoomed in and in
    the overview. It never crosses onto another monitor in Hyprland's layout by hand (that layout is not where the
    desktops are round you): switching desktops is the dot moving onto another desktop in the overview (below), a key
    (Ctrl+Alt+Left / Right), or, zoomed in, a push past home's edge (below). Cursor follow (Ctrl+Alt+F) is gone
    (view.py).
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
  * Scroll before switch (columns.py, scroll-before-switch 2026-11-03; zoomed in, typing mode included, not during a VR
    video). On a scrolling-layout workspace with a column past home's left or right edge, the push there scrolls the
    columns first and never counts toward a switch: the cursor is held in the edge band (hold_point) while they scroll
    in one at a time. Once none is left that way, the push rests columns.END_REST_S and then switches as above. Every
    other layout, a fullscreen window, and the up and down edges: unchanged.
  * Overview, over home (free_pointer desktop mode). The real cursor is the pointer: hand motion moves it, and the dot is
    drawn where it maps onto home's surface (free_pointer._from_logical; every viewing mode). It is hidden
    (overview_cursor.py), but hover, focus-follows-mouse, every button, the wheel and the keyboard reach the window under
    it, as on any monitor. The left button is bound non-consuming (click_binds_lua: the window gets it as usual) to
    resize_press / resize_release, so the run sees it too: a press brings an open placement panel to that desktop and
    takes its modal keys off the keyboard, so they cannot take the typing meant for the window
    (view.ViewControl.desktop_press), and starts the held-button guard below. A click there is the
    window's alone: it never maximizes home (anchor-no-click-maximize, 2026-11-03; until then a press and release no
    more than free_pointer.CLICK_PX apart also zoomed in). Only Ctrl+Alt+Up maximizes. While the button is held the dot
    never turns free and the cursor stays on home (pulled back if Hyprland let it onto another monitor), so a drag inside
    an app keeps going and the window gets its release.
    Window drag (free_pointer.py; off, OverviewPointer.window_drag): with it on, holding the button free_pointer.HOLD_S
    with the dot no more than CLICK_PX from the press lifts the window under it instead (_lift_home): the dot turns free
    right there, as at an edge (the grab parks the hidden cursor and takes the release), the ghost follows it, and the
    release over another desktop moves the window there. Moving first is the window's own drag or selection.
  * Overview, off the desktops (free mode). The cursor reaching home's edge pixel turns the dot free: the cursor is
    hidden and parked just inside home where it left (input_lock.park_point; leash input_lock.LEASH_PX), and its motion
    moves the dot through space from just past that edge, on its resize handle (free_pointer._exit). Over edges, corners,
    pills, level bars, panels and the help card (the XR handles, all outside the desktops' content, and the dot's look
    shows which: free_pointer.hit, zone_of, look_for) it is only the dot: the free grab is the input lock's
    (input_lock.lock_pointer_lua), so no button or wheel reaches a window there. Moving onto any desktop's content hands
    the real cursor to that desktop, under the dot (free_pointer.OverviewPointer._enter: the dot's hit point through
    free_pointer.surface_to_layout), and makes it home (selected; the highlight moves there and Ctrl+Alt+Up maximizes
    it). The sampler's enter_desktop_lua focuses that monitor first, then warps the cursor, so the keyboard goes to that
    monitor's focused window and focus-follows-mouse picks the window under the dot. It feels like one cursor: the same
    dot, the same speed either side of an edge (_gain), and a look that always says what a press would do.
  * Super drags (Hyprland's own window move and resize, SUPER + mouse:272 / 273: view.SUPER_BINDS; Cody, 2026-11-03). The drag
    stays Hyprland's; the run only keeps out of its way and carries it across the gaps. Non-consuming binds beside the
    user's tell the run it began, and the buttons' release (ignore_mods: Super may go first) that it ended
    (view.install_drag_binds_lua, on_super). A move: inside a desktop nothing changes. At its edge the mode is `carry`, not
    free: the dot goes on through space from just past the edge (free_pointer._exit) with nothing grabbed, so the button
    stays Hyprland's, while the cursor, and the window, wait CARRY_INSET_PX inside the edge, put back once they stray
    CARRY_LEASH_PX so hand motion past a clamped edge still shows. Handles do nothing in carry mode. Onto any desktop's
    content (_carry) the cursor warps to the pixel under the dot, that desktop is home (a plain view frame: the highlight
    moves, no animation) and the mode is desktop again. No focus call: warping is enough. In Hyprland 0.56.2's source
    moveCursor warps, focuses that monitor and simulates motion; motion during a drag sets the window to its start
    position + the cursor's delta and, floating (a dragged tiled window is, centred on the cursor), gives it to the
    workspace of the monitor under its middle; the release re-tiles a tiled one there. Dropped with the cursor on a desktop
    after crossing, the window lands there even if its middle has not crossed (_land, send.WindowSender.land_now: only the
    window under the press, and only if Hyprland ended the drag on it). A drop between desktops leaves the window where
    it waited and the dot free. A resize: the window keeps the cursor, as for a held left button: it stays on home,
    pulled back, and never goes free. Maximized, the same binds stop the push past an edge (can_switch). With the dot free
    nothing is under it, so the bind itself ends the drag Hyprland began on the parked cursor. In typing mode the binds
    stay and a move turns the pointer on for as long as it lasts (overview; no pills or arrows: _handles_on), so it
    crosses as usual and the pointer goes off again at the drop; a resize needs no pointer, the plain confinement keeps
    its cursor on home.
  * Keys. Ctrl+Alt+Left / Right change the selected desktop: home moves with it, and the cursor and dot go to the middle
    of the new home (OverviewPointer desktop mode there; zoomed in, the confinement moves it). Any other jump of the
    cursor off home in the overview is pulled back home, or let go (away) if it landed on the physical monitor.
  * Compositor state. One bind table (resize.BIND_TABLE) holds whichever set is live: the click binds over home, the
    lock grab while free, and every switch replaces it in a single eval (enter_desktop_lua / the lock grab). No key is
    bound by the pointer: the keyboard is the focused window's (an open placement panel's modal keys are view.py's). Run end
    (PointerSampler.stop), a config reload (re-applied) and a dead run (its next left click still reaches the window and
    runs the bind helper, which removes the whole table) clean it up as before.
"""
import math
import time

from . import columns as cols, free_pointer as fp, input_lock as il, neighbors, resize, view

# Zoomed in, pushing past an edge of home toward a desktop placed that way switches to it (EdgePush, neighbors.py).
PUSH_PX = 48              # logical px of push past one edge, each part within PUSH_WINDOW_S of the last, that switch
PUSH_WINDOW_S = 0.25      # a pause longer than this starts the push over
NUDGE_PX = 12             # an edge Hyprland clamps the cursor at (nothing beyond it in its layout): the cursor touching it
                          # is put back this far in, and each touch counts this much push (the hand's push is not seen)
SWITCH_COOLDOWN_S = 0.8   # after a switch no push counts for this long (the renderer's zoom out, turn, zoom in)
ENTRY_PX = 24             # the cursor enters the new desktop this far inside the edge it came in over
# A Super drag (carry mode) between desktops: the real cursor, and the window with it, waits this far inside the edge it
# left by (so hand motion past a clamped edge still shows), and goes back there once it strays CARRY_LEASH_PX. Small, because
# the window shows every pixel of that.
CARRY_INSET_PX, CARRY_LEASH_PX = 12, 6
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


def enter_desktop_lua(python, client, control_socket, run_id, session, x=None, y=None, monitor=None):
    """Into desktop mode: any free-mode grab removed (the cursor's own visibility back; overview_cursor keeps it
    hidden), the cursor warped to layout pixel (x, y) if given, then the click binds. One eval. With `monitor` (and a
    point), that output gets Hyprland's monitor focus first, so the keyboard goes to its focused window whatever the
    focus-follows-mouse setting; the warp after it then puts the cursor under the dot (a monitor focus alone moves it
    to that monitor's last window), and focus-follows-mouse picks the window there."""
    focus = ''
    if monitor is not None and x is not None:
        if not isinstance(monitor, str) or not view.MANAGED.match(monitor): raise ValueError('focus is limited to managed outputs')
        focus = f'pcall(function() hl.dispatch(hl.dsp.focus({{ monitor = "{monitor}" }})) end) '
    return focus + fp.release_pointer_lua(x, y) + ' ' + click_binds_lua(python, client, control_socket, run_id, session)


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


def _band(o, cursor):
    """'right' / 'left': the cursor (on `o`) within columns.EDGE_PX of that edge, tested as scroll-edge.py does; or None."""
    lx = cursor[0] - o['x']
    if lx >= o['width'] - cols.EDGE_PX: return 'right'
    if lx <= cols.EDGE_PX - 1: return 'left'
    return None


def hold_point(o, cursor, side):
    """A push held at edge `side` ('left' / 'right') of `o` for its columns (columns.py): the cursor in the edge band,
    1 px in from the outermost pixel (so a 1 px push still stays on `o`), as far along the edge as it is."""
    x, y = inside(o, cursor)
    x = o['x'] + cols.EDGE_PX - 1 if side == 'left' else math.ceil(o['x'] + o['width'] - cols.EDGE_PX)
    return int(x), y


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
        counts for SWITCH_COOLDOWN_S.
      * `columns(edge, now, confirm=False)` (columns.ColumnScroll.held), if given, is asked about every sample at or past
        home's left / right edge (the edge band, columns.EDGE_PX, or over that edge by hand). True: the push is held
        there for a scrolling-layout workspace's columns. It counts for nothing (and starts over), the cursor stays in
        the band (one pushed over comes back to hold_point) and is never nudged. A push about to switch asks once more
        with `confirm`, and a True then holds it instead. None (or False every time): every push exactly as before."""
    def __init__(self):
        self.edge, self.amount, self.last, self.quiet_until = None, 0.0, None, -math.inf

    def reset(self):
        self.edge, self.amount, self.last = None, 0.0, None

    def step(self, outputs, home, cursor, previous, now, neighbour, columns=None):
        o = next((o for o in outputs if o['index'] == home), None)
        if o is None or cursor is None: return None, None
        at = _output_at(outputs, cursor)
        if at is not None and at['index'] == home:
            band = _band(o, cursor) if columns is not None else None
            if band is not None and columns(band, now):  # columns left that way: they scroll first, the cursor stays
                self.reset()
                return None, None
            sides = [s for s in _contact(o, cursor) if neighbour(s) is not None]
            if not sides or now < self.quiet_until: return None, None
            side, amount = sides[0], NUDGE_PX
            warp = _nudge(o, cursor, side)
        else:
            warp = confine_target(outputs, home, cursor, previous)
            if warp is None: return None, None
            side, amount = _crossing(o, cursor)
            if columns is not None and side in cols.SIDES and amount <= fp.JUMP_PX and columns(side, now):
                self.reset()
                return hold_point(o, cursor, side), None  # back into the band: the columns scroll first
            if amount > fp.JUMP_PX or now < self.quiet_until or neighbour(side) is None: return warp, None
        if side != self.edge or self.last is None or now - self.last > PUSH_WINDOW_S: self.edge, self.amount = side, 0.0
        self.amount += amount
        self.last = now
        if self.amount < PUSH_PX: return warp, None
        if columns is not None and side in cols.SIDES and columns(side, now, True):  # a column turned up: it scrolls first
            self.reset()
            return hold_point(o, cursor, side), None
        target = neighbour(side)
        self.reset()
        self.quiet_until = now + SWITCH_COOLDOWN_S
        return warp, (target, side, along(o, cursor, side))


class UnifiedPointer(fp.OverviewPointer):
    """The overview free pointer with the real cursor on home (module docstring)."""
    def __init__(self, control, clock=time.monotonic):
        super().__init__(control, clock)
        self.click = None  # over home, left button down: {'start', 'at', 'moved'} (the dot and time at the press)
        self.rehomes = self.pullbacks = self.window_clicks = self.follows = self.desktop_presses = 0  # diagnostics
        self._switch = None  # zoomed in: (home, target) the sampler asked the loop to switch to (EdgePush)
        self.edge_switches = 0  # diagnostics
        self.super_drag = None  # 'move' / 'resize': a Super drag of Hyprland's is under way (on_super)
        self.super_crossed = False  # it went through carry mode
        self.super_token = 0        # whose window lookup counts (_identify)
        self.landings = 0           # diagnostics: drops that asked for a landing
        self.super_window = None    # the window under the cursor at its press (send.window_at's dict), once looked up
        self.carries = 0            # diagnostics: Super drags that left a desktop

    def overview(self):
        """As free_pointer's, and in typing mode too while a Super move lasts: that is what carries it across the gaps. It
        needs nothing else of the overview (no pills, _handles_on) and goes off again with the drag. A resize stays on its
        desktop, which the plain confinement does in typing mode."""
        s = self.control.state
        if self.super_drag == 'move' and getattr(s, 'typing', False):
            return not s.zoomed and not getattr(self.control, 'video_active', False)
        return super().overview()

    def _handles_on(self):
        return self.active() and not getattr(self.control.state, 'typing', False)

    # --- zoomed in: the push past an edge -------------------------------------------------------------------------
    def can_switch(self):
        """A push past home's edge switches desktops: zoomed in, not in typing mode and with no VR video (as the
        Ctrl+Alt+Left / Right keys, whose binds typing mode and a video release)."""
        s = self.control.state
        return s.zoomed and not getattr(s, 'typing', False) and not getattr(self.control, 'video_active', False) \
            and self.super_drag is None  # never mid Super drag: no switch, the cursor just stays on home

    def can_scroll(self):
        """A push at home's left / right edge scrolls a scrolling-layout workspace's columns before it switches
        (columns.py): zoomed in, in typing mode too (switching is what needs the keys' binds; scrolling needs none),
        never during a VR video."""
        return self.control.state.zoomed and not getattr(self.control, 'video_active', False)

    def neighbour(self, edge):
        """The desktop placed past home's `edge` round you (neighbors.pick on the renderer's overview layout and the
        placements as drawn: without their rolls, home being level while maximized and a neighbour's roll turning it about
        its own centre), or None. Without a layout with one slot per desktop: the row order (neighbors.row_fallback).
        Read from the sampler thread: the layout and placements are replaced, never changed in place."""
        s = self.control.state
        home, count, g = s.selected, len(s.names), self.geometry
        if g is None or len(g['tiles']) != count: return neighbors.row_fallback(count, home, edge)
        return neighbors.pick(neighbors.frames(g, s.placements), home, edge)

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
        out = self._super_end() if self.super_drag is not None and now - self.last_move >= view.OVERLAY_IDLE_CLOSE_S else b''
        out += self._take_switch()
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
        if self.click is not None or self.super_drag == 'resize':  # held: the window keeps the cursor, which stays on home
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
        if self.super_drag == 'move': return self._start_carry(home, o, cursor, now)
        start = self._exit(home, lx, ly)  # just past the edge it reached: on that edge's resize handle
        if start is None: return self._wants()
        self.gain = self._gain(home)
        self.pointer = self._clamp(start)
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
        if self.click is None and self.super_drag is None: super()._onto_help()  # a held button stays with its window

    def _held(self):
        """The left button held on a window (desktop mode), or a Super drag: the dot is drawn held, as for a handle drag."""
        return (self.click is not None and self.mode == 'desktop') or self.super_drag is not None

    def _look(self):
        """A Super drag shows what it does, wherever the dot is; otherwise the zone contract (free_pointer._look)."""
        return {'move': 'move', 'resize': 'resize_nwse'}.get(self.super_drag) or super()._look()

    # --- Super drags ----------------------------------------------------------------------------------------------
    def on_super(self, op):
        """view.SUPER_OPS: Hyprland began a window move or resize under the cursor, or a button came up (module docstring:
        Super drags). Only the run's own idea of the drag changes here: the drag itself is Hyprland's."""
        if op not in view.SUPER_OPS: raise ValueError('unsupported Super drag operation')
        now = self.clock()
        self.last_move = now
        if op == 'super_release':
            self._land()
            return self._super_end() or None
        self.super_drag = 'move' if op == 'super_left_press' else 'resize'
        self.click = None
        self.super_crossed = False
        if self.super_drag == 'move': self._identify()
        if self.mode == 'off' and self.overview():  # typing mode: the last cursor seen is old, wait for the next sample
            with self._lock: self._abs, self._fresh = None, False
        out = self.control.pill_close() if self.active() and self.control.state.menu_open else b''  # the drag takes the window, keys and all
        if self.active() and self.mode == 'free':  # the hand beat the helper to the edge: the grab must not stay on the drag
            o = self._output(self._home())
            with self._lock: cursor = self._abs
            if o is not None and cursor is not None:
                if self.super_drag == 'move': out += self._start_carry(self._home(), o, cursor, now)
                else:
                    self.mode, self.desktop, self.hover = 'desktop', self._home(), None
                    self._set_want('desktop', inside(o, cursor), sync=True)
        return (out + self._frames(False)) or None if self.active() else out or None

    def _identify(self):
        """A Super move's press: ask (off the loop) which window is under the cursor, the one Hyprland just began dragging.
        An answer that comes after the next press belongs to no drag."""
        self.super_window = None
        sender = getattr(self.control, 'sender', None)
        if sender is None: return
        self._token += 1
        token = self._token
        self.super_token = token
        def found(window):  # the sender's worker thread
            with self._lock:
                if self.super_token == token: self.super_window = window
        sender.under_cursor(found)

    def _land(self):
        """A Super move's release after crossing desktops, with the cursor on one: the window lands on that monitor even if
        its middle has not crossed (send.WindowSender.land_now, off the loop). Dropped between desktops it stays."""
        with self._lock: window, self.super_window = self.super_window, None
        crossed, self.super_crossed = self.super_crossed, False
        sender, names = getattr(self.control, 'sender', None), self.control.state.names
        if self.super_drag != 'move' or not crossed or self.mode != 'desktop' or sender is None or self._home() >= len(names): return
        if isinstance(window, dict) and isinstance(window.get('address'), str) and not window.get('pinned'):
            self.landings += 1
            sender.land(window['address'], names[self._home()])

    def _super_end(self):
        """The Super drag's button came up (or its release was lost: _desktop_button, the idle close). Dropped between
        desktops, the dot is free there again; Hyprland left the window where the cursor waited. In typing mode the pointer
        was only on for the drag: it goes off, the dot with it."""
        self.super_drag = None
        if not self.active():
            if self.mode == 'off': return b''
            self.mode, self.hover, self.pointer = 'off', None, None
            self._set_want('off')
            return self._frames(False)
        if self.mode != 'carry': return self._frames(False)
        o = self._output(self.desktop)
        if o is None:
            self.mode, self.desktop, self.pointer = 'away', None, None
            self._set_want('away')
            return b''
        self.mode = 'free'
        self._set_want('free', self._park(o))
        self.hover = self._hit()
        return self._frames(False)

    def _start_carry(self, i, o, cursor, now):
        """A Super drag reached desktop i's edge: the dot goes on through space from just past it, as ever (free_pointer._exit),
        but nothing is grabbed, so the button stays Hyprland's and the drag goes on. The cursor, and the window, wait just
        inside the edge."""
        start = self._exit(i, cursor[0] - o['x'], cursor[1] - o['y'])
        if start is None: return self._wants()
        self.gain = self._gain(i)
        self.pointer = self._clamp(start)
        self.mode, self.desktop, self.hover, self.last_move = 'carry', i, None, now
        self.exits += 1
        self.carries += 1
        self.super_crossed = True
        self._set_want('carry', inside(o, cursor, CARRY_INSET_PX), sync=True)
        return b''

    def _carry(self, dx, dy, now):
        """Carry mode: the dot moves through space. Handles do nothing here. Onto any desktop's content the cursor warps to
        the matching pixel (a warp, no focus call, which would take the window from under the drag): Hyprland's drag follows
        the cursor there, by the same offset, and the window goes to that monitor's workspace with its middle. That desktop
        is home now: a plain view frame, so the highlight moves with no animation."""
        self.pointer = self._clamp((self.pointer[0] + dx, self.pointer[1] + dy))
        self.last_move = now
        i = fp.desktop_at(self.geometry, self._rolled(), self.pointer)
        if i is None: return b''
        tile = self._tile(i)
        q = fp.unproject(self.geometry, tile, self._placement(i), self.pointer)
        target = self._to_layout(i, q) if q is not None and fp.inside_by(tile, q) >= fp.ENTER_PX else None
        if target is None: return b''
        self.mode, self.desktop = 'desktop', i
        self.entries += 1
        self._set_want('desktop', target, sync=True)
        return self._select(i)

    def _park(self, o, local=None):
        """Free mode holds the hidden cursor just inside home where it left (input_lock.park_point), not in the middle:
        with focus-follows-mouse the window it was over keeps the focus."""
        cursor = (o['x'] + local[0], o['y'] + local[1]) if local is not None else self._abs
        return il.park_point(o, cursor)

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
        # A plain press means no button is held: a Super drag's release was lost. A plain release mid drag (Super let go
        # first) is nothing: its own release bind says when it ends.
        stale = self._super_end() if op == 'resize_press' and self.super_drag is not None else b''
        if self.mode == 'carry': return stale or None
        if self.mode == 'desktop' or (op == 'resize_release' and self.click is not None):
            return (stale + (self._desktop_button(op) or b'')) or None
        self.click = None
        return (stale + (super().on_op(op) or b'')) or None

    def _desktop_button(self, op):
        """Over home, the non-consuming left button: the window already has it. The press brings an open placement panel
        here with its modal keys off the keyboard (view.ViewControl.desktop_press: they would take the typing meant for the
        window) and is noted for the held-button guard (and, with window_drag on, a hold that lifts the window: _hold_home); a click
        is the window's alone and never maximizes home (anchor-no-click-maximize)."""
        now = self.clock()
        idle = now - self.last_move >= view.OVERLAY_IDLE_CLOSE_S
        self.last_move = now  # input: the dot and pills come back
        live = self.active() and self.mode == 'desktop' and self.pointer is not None
        out = b''
        if op == 'resize_press':
            self.click = {'start': self.pointer, 'at': now, 'moved': False} if live and not idle else None
            if live and self.control.state.menu_open:
                self.desktop_presses += 1
                out += self.control.desktop_press()
        else:
            self.click = None
            self.window_clicks += 1
        return (out + self._handle() + self._pills(False) + self._pointer_frame(False)) or None


    def _hold_home(self, now):
        """Over home with the button down, window_drag on: moving more than CLICK_PX first is the window's own drag
        (never lifts); held HOLD_S still, the window under the cursor lifts (_lift_home). Off: nothing, ever."""
        c = self.click
        if not self.window_drag or c['moved'] or self.pointer is None or c['start'] is None: return b''
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
        self.outputs = []       # the XR monitors as last read (resize.outputs_from_monitors), for enter_desktop's focus
        # Scroll before switch (columns.py): only with a client that can scroll (the run's HyprlandClient).
        self.columns = cols.ColumnScroll(self.client) if hasattr(self.client, 'scroll_to_window') else None

    def _run(self):
        outputs, refreshed = [], None
        while not self._stop.is_set():
            now = time.monotonic()
            try:
                if refreshed is None or self.names_changed or now - refreshed >= fp.OUTPUTS_REFRESH:
                    self.names_changed = False  # a pill's Add / Remove (set_names): read the monitors again at once
                    outputs = resize.outputs_from_monitors(self.client.query('monitors', timeout=0.5), self.names)
                    refreshed = now
                    self.outputs = outputs
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
        columns = self._columns(home) if self._pending is None and self.pointer.can_scroll() else None
        warp, switch = self.push.step(outputs, home, cursor, self._outside, now,
                                      self.pointer.neighbour if live else (lambda edge: None), columns)
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

    def _columns(self, home):
        """EdgePush's `columns` for home's monitor (columns.ColumnScroll.held), or None (no client that scrolls)."""
        if self.columns is None or not 0 <= home < len(self.names): return None
        name, held = self.names[home], self.columns.held
        return lambda edge, now, confirm=False: held(name, edge, now, confirm)

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
            elif kind == 'carry':  # a Super drag between desktops: no grab, the button stays Hyprland's
                if self.grabbed:  # (the hand beat the helper to the edge: on_super)
                    self.client.enter_desktop(*self.bind_args, *point)
                    self.grabbed, self.bound = False, True
                else: self.client.warp_cursor(*point)
                self.park, self._last = point, point
            elif kind == 'desktop' and self.bound and not self.grabbed:  # the click binds are in: only the cursor moves
                if point is not None: self.client.warp_cursor(*point)
            elif kind == 'desktop':  # into a desktop from free mode (or away): that monitor focused, the cursor under the dot
                self.ever_grabbed = True
                self.client.enter_desktop(*self.bind_args, *(point or ()), monitor=self._monitor(point))
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

    def _recentre(self, cursor):
        """Carry mode: the cursor, and the dragged window with it, goes back to where it waits once it strays
        CARRY_LEASH_PX: that is how hand motion past a clamped edge keeps showing. Otherwise the lock's own leash."""
        if self.pointer.want[1] != 'carry' or not self.pointer.overview(): return super()._recentre(cursor)
        if cursor is None or self.park is None or math.hypot(cursor[0] - self.park[0], cursor[1] - self.park[1]) <= CARRY_LEASH_PX: return
        try:
            self.client.warp_cursor(*self.park)
            self._last = self.park; self.recentres += 1
        except (OSError, ValueError, TypeError, KeyError, AttributeError): pass

    def _monitor(self, point):
        """The name of the run's XR monitor holding layout point `point`, or None."""
        o = _output_at(self.outputs, point) if point is not None else None
        return self.names[o['index']] if o is not None and 0 <= o['index'] < len(self.names) else None

    def _release(self):
        """Out of the overview: binds gone, the cursor shown where it is."""
        if not (self.grabbed or self.bound): return
        try:
            self.client.release_pointer()
            self.grabbed = self.bound = False
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            self.errors += 1
