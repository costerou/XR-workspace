"""Overview input lock: in the overview the real cursor is held still and cannot click, and the mouse only drives the
overview's own pointer (the free pointer's dot, free_pointer.py). Input only: what the dot and cursor look like is the
free pointer's.

Superseded for the run (2026-11-02, dot-unified-cursor): managed.py wires unified_cursor.py, where the real cursor
follows the dot onto the desktop under it and only free mode (the dot off every desktop) holds it; that free mode is
this module's grab (lock_pointer_lua, park_point, LockedSampler's leash and warp handling). LockedPointer stays as the
library behind it and is still tested on its own.

Design (overview-uc-input-lock, 2026-11-02):
  * Capture. In the overview a cursor on one of the run's XR desktops (or one that Ctrl+Alt+Down's summon brings into
    the selected desktop) is captured at once: the pointer goes straight to the free pointer's free mode and stays there
    for the whole overview. The dot starts where the cursor was. Moving it into a desktop selects that desktop (the
    highlight; Ctrl+Alt+Up zooms into it) but never hands the real cursor back (LockedPointer._enter); clicking one
    selects it (Click to select, below). A
    cursor on the physical monitor is left alone, as before, so the panel stays usable; it is captured as soon as it
    comes onto an XR desktop.
  * Held still. The capture parks the hidden cursor where it was, kept PARK_MARGIN_PX inside its monitor so hand motion
    never reaches an edge or another monitor, and the sampler puts it back once it strays LEASH_PX (the free pointer's
    own leash is RECENTRE_PX). Hand motion only moves the dot; the window under the cursor, and with focus-follows-mouse
    the keyboard focus, stay as they were.
  * Click to select (anchor-no-click-maximize, 2026-11-03; before that a click zoomed in). A dot click on a desktop
    selects it and stays in the overview (free_pointer: Clicks; view.ViewControl.select_desktop): it never maximizes,
    only Ctrl+Alt+Up does. The held cursor is parked again on the clicked desktop's monitor, under the dot
    (LockedPointer._click_desktop), so focus-follows-mouse gives that monitor Hyprland's focus. The capture stays.
  * Moves that are not the hand. A focus change (Ctrl+Alt+Left / Right, the send-window keys, a zoom-in) warps the cursor
    to another monitor. A jump of more than free_pointer.JUMP_PX from the parking point is never pulled back: the dot
    follows it (or stays put if it is already on that desktop) and the cursor is parked there instead, or, if it landed
    off the XR desktops (a window sent back to the monitor), the capture ends and the cursor shows there.
  * Buttons and wheel. The capture's grab (lock_pointer_lua, one eval) is the free pointer's own (the left button goes
    to the run: pills, corners, sliders, the help card, a desktop click selects) plus, in the same bind table, the right,
    middle and side buttons (OTHER_BUTTONS) bound to the bind helper's resize_release, and the four wheel directions
    (WHEEL) bound to nothing. A resize_release with nothing held does nothing; with a drag held it ends the drag, as the
    left button's release would. Hyprland passes wheel events on to the window for binds.scroll_event_delay ms after a
    bound one, so the lock sets that to 0 and leaves a restore entry in the same table that puts it back when the table
    is removed. Chords with a modifier (Super + drag, Super + Shift + wheel) are not bound: the desktop's own binds still
    work.
  * Release. Leaving the overview (zoom in, typing mode, a VR video, no overview geometry) removes the binds and shows
    the cursor where it is (LockedSampler._release), never at the capture's starting point, which would undo a zoom-in's
    focus warp. Run end is the free pointer's (its bind removal). A run that dies with the lock on leaves the binds: the
    first press of any bound button runs the bind helper, which finds no run and removes the whole table (xr_control.py
    REMOVE_RESIZE_BINDS): every button, the wheel, the scroll delay and the hidden cursor come back. The wheel alone
    runs nothing, so it stays bound until that click.
  * No new cleanup path. Every removal of the free pointer's bind table (release, run end, a re-grab, the dead-run
    cleanup) takes the lock's binds and restore entry with it.
  * Not covered: touchpad scrolling (Hyprland binds only wheel scrolling), a desktop config that sets
    binds.pass_mouse_when_bound (Hyprland then passes bound clicks on to the window), and the hidden cursor can stray
    further while the sampler thread is starved.
"""
import math
import time

from . import free_pointer as fp, resize, view

PARK_MARGIN_PX = 100    # the capture parks the cursor at least this far inside its monitor (logical px)
LEASH_PX = 32           # a parked cursor that strays further is put back (logical px)
# Bound to the run while captured, besides the free pointer's left button: right, middle and side / extra buttons.
OTHER_BUTTONS = ('mouse:273', 'mouse:274', 'mouse:275', 'mouse:276', 'mouse:277', 'mouse:278', 'mouse:279')
OTHER_BUTTON_OP = 'resize_release'  # a release with nothing held: nothing (it ends a drag in progress)
WHEEL = ('mouse_up', 'mouse_down', 'mouse_left', 'mouse_right')
SCROLL_DELAY = 'binds.scroll_event_delay'

# A re-grab (config reload, a warp followed) goes through the free pointer's bind removal, which puts the cursor back
# where the first grab found it; pin that to where the cursor is now, so a re-grab never bounces it through there.
_PIN_HOME_LUA = (f'if {resize.GRAB_TABLE} then local p = hl.get_cursor_pos() '
                 f'if p then {resize.GRAB_TABLE}.home = {{ x = p.x, y = p.y }} end end')


def lock_binds_lua(python, client, control_socket, run_id, session):
    """The lock's binds, added to the free pointer's bind table (which must hold the grab's binds already): the other
    buttons to the bind helper's resize_release, the wheel to nothing, and Hyprland's scroll event delay at 0 with an
    entry that restores it when the table is removed. Every bind is pcall'd: one Hyprland refuses is left out."""
    for p in (python, client, control_socket):
        if not isinstance(p, str) or not view.SAFE_PATH.match(p): raise ValueError('unsafe path for bind command')
    for t in (run_id, session):
        if not isinstance(t, str) or not view.SAFE_TOKEN.match(t): raise ValueError('unsafe token for bind command')
    buttons = ', '.join(f'"{b}"' for b in OTHER_BUTTONS)
    wheel = ', '.join(f'"{w}"' for w in WHEEL)
    return (f'do local t = {resize.BIND_TABLE} or {{}} {resize.BIND_TABLE} = t '
            f'local ok, d = pcall(hl.get_config, "{SCROLL_DELAY}") '
            'if ok and type(d) == "number" and d ~= 0 and pcall(hl.config, { binds = { scroll_event_delay = 0 } }) then '
            't[#t + 1] = { remove = function() pcall(hl.config, { binds = { scroll_event_delay = d } }) end } end '
            f'local c = "{python} -I {client} {control_socket} {run_id} {session} {OTHER_BUTTON_OP}" '
            f'for _, k in ipairs({{ {buttons} }}) do local ok, b = pcall(hl.bind, k, hl.dsp.exec_cmd(c)) '
            'if ok and b then t[#t + 1] = b end end '
            f'for _, k in ipairs({{ {wheel} }}) do local ok, b = pcall(hl.bind, k, function() end) '
            'if ok and b then t[#t + 1] = b end end end')


def lock_pointer_lua(python, client, control_socket, run_id, session, x, y):
    """Capture or re-park: the free pointer's grab (left-button binds, cursor hidden and parked at layout pixel (x, y))
    plus the lock's binds, in one eval."""
    return (_PIN_HOME_LUA + ' ' + fp.grab_pointer_lua(python, client, control_socket, run_id, session, x, y) + ' ' +
            lock_binds_lua(python, client, control_socket, run_id, session))


def park_point(output, cursor=None):
    """Integer layout pixel where the capture holds the cursor on `output` (a resize.outputs_from_monitors entry):
    `cursor` itself, kept PARK_MARGIN_PX inside each edge (the middle, on an output too small for that), or the
    output's middle if no cursor is given."""
    def axis(lo, size, v):
        margin = min(PARK_MARGIN_PX, (size - 1) / 2)
        return int(lo + size / 2) if v is None else int(min(max(v, lo + margin), lo + size - 1 - margin))
    return (axis(output['x'], output['width'], None if cursor is None else cursor[0]),
            axis(output['y'], output['height'], None if cursor is None else cursor[1]))


class LockedPointer(fp.OverviewPointer):
    """The overview free pointer with the input lock: a cursor on an XR desktop is captured into free mode instead of
    being the pointer itself (desktop mode), and warps made by focus changes are followed."""
    def __init__(self, control, clock=time.monotonic):
        super().__init__(control, clock)
        self.captures = self.follows = 0  # diagnostics

    def poll(self, now):
        out = self._follow(now) if self.mode == 'free' and self.active() else b''
        return out + super().poll(now)

    def _absolute(self, cursor, now):
        """Away (no desktop mode while locked): a cursor that is on an XR desktop, or that Ctrl+Alt+Down summoned, is
        captured. One elsewhere (the physical monitor) is left alone."""
        at = self._at(cursor)
        if at is None:
            if not self.summoned: return b''
            self.summoned = False
            i = self.control.state.selected
            o, tile = self._output(i), self._tile(i)
            if o is None or tile is None: return b''
            return self._capture(i, (tile[0] + tile[2] / 2, tile[1] + tile[3] / 2), park_point(o), now)
        self.summoned = False
        i, lx, ly = at
        o = self._output(i)
        point = self._from_logical(i, lx, ly) or self._from_logical(i, o['width'] / 2, o['height'] / 2)
        if point is None: return b''
        return self._capture(i, point, park_point(o, cursor), now)

    def _capture(self, i, point, park, now):
        """Into free mode on desktop i with the dot at canvas `point` and the cursor held at layout pixel `park`."""
        self.mode, self.desktop, self.gain = 'free', i, self._gain(i)
        self.pointer, self.last_move = self._clamp(point), now
        self.captures += 1
        self._set_want('free', park, sync=True)  # samples count again once the sampler has parked it there
        self.hover = self._hit()
        return self._select(i)

    def _enter(self, i, q):
        """The dot went into desktop i: select it; the real cursor stays where it is held."""
        return self._select(i)

    def _click_desktop(self, i, q):
        """A dot click on desktop i at tile pixel q: selected, still in the overview (OverviewPointer._click_desktop),
        and the held cursor parked on its monitor under the dot, so focus-follows-mouse gives that monitor Hyprland's
        focus. The capture stays: the dot is still the pointer."""
        out = super()._click_desktop(i, q)
        o, at = self._output(i), self._to_layout(i, q)
        if self.mode == 'free' and self.control.state.selected == i and o is not None and at is not None:
            self.desktop = i
            self._set_want('free', park_point(o, at), sync=True)
        return out

    def _follow(self, now):
        """A captured cursor more than JUMP_PX from its parking point was warped there by a focus change, not by the
        hand: the dot follows it onto that desktop and it is held there, or the capture ends if it is off the XR
        desktops. A drag in progress ends first (saved)."""
        _, kind, park = self.want
        with self._lock: cursor, applied = self._abs, self._applied
        if kind != 'free' or park is None or cursor is None or applied < self._sync: return b''
        if math.hypot(cursor[0] - park[0], cursor[1] - park[1]) <= fp.JUMP_PX: return b''
        self.follows += 1
        out = self._end_drag()
        at = self._at(cursor)
        if at is None:
            self.mode, self.desktop, self.pointer, self.hover = 'away', None, None, None
            self._set_want('away')  # the sampler shows the cursor where it landed
            return out
        h = self.hover
        if h is not None and h[0] in ('desktop', 'pill', 'corner', 'edge', 'roll') and h[1] == at[0]:
            # Onto the desktop the dot is already on (a focus change onto it): only the held cursor moves there.
            self._set_want('free', park_point(self._output(at[0]), cursor), sync=True)
            return out + self._select(at[0])
        return out + self._absolute(cursor, now)


class _LockingClient:
    """The sampler's compositor client: free_pointer.PointerSampler grabs with grab_pointer; under the lock that is
    lock_pointer (the same grab plus the lock's binds, one eval). Every other call is the client's own."""
    def __init__(self, client): self._client = client
    def __getattr__(self, name): return getattr(self._client, name)
    def grab_pointer(self, *args): return self._client.lock_pointer(*args)


class LockedSampler(fp.PointerSampler):
    """The free pointer's sampler with the lock: grabs with lock_pointer, a short leash, warps by others left alone, and
    a release that leaves the cursor where it is."""
    def __init__(self, client, names, pointer, bind_args, period=fp.SAMPLE_S):
        super().__init__(_LockingClient(client), names, pointer, bind_args, period)

    def _sample(self):
        cursor, motion = super()._sample()
        if math.hypot(*motion) > fp.JUMP_PX: return cursor, (0.0, 0.0)  # a warp, never hand motion (LockedPointer._follow)
        return cursor, motion

    def _recentre(self, cursor):
        """Put the held cursor back once it strays LEASH_PX. One that jumped more than JUMP_PX was warped by a focus
        change: left there (the loop follows it and re-parks)."""
        if not self.grabbed or cursor is None or self.park is None or not self.pointer.overview(): return
        d = math.hypot(cursor[0] - self.park[0], cursor[1] - self.park[1])
        if d <= LEASH_PX or d > fp.JUMP_PX: return
        try:
            self.client.warp_cursor(*self.park)
            self._last = self.park; self.recentres += 1
        except (OSError, ValueError, TypeError, KeyError, AttributeError): pass

    def _release(self):
        """Out of the overview: the binds (the lock's too) go and the cursor shows where it is."""
        if not self.grabbed: return
        try:
            self.client.release_pointer()
            self.grabbed = False
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            self.errors += 1
