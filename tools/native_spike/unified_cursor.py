"""Unified overview cursor: the dot is the one real cursor. Wherever the dot is over a desktop, the real Hyprland cursor
is on that desktop's monitor at the matching point, so hover, clicks, scrolling and window drags reach the window there;
past a desktop's edge the dot carries on through the glasses' space onto the next desktop.

Design (dot-unified-cursor, 2026-11-02; replaces the input lock's held cursor for the run, input_lock.py):
  * Over a desktop (free_pointer desktop mode). The real cursor is the pointer: hand motion moves it, and the dot is
    drawn where it maps onto that desktop's surface (free_pointer._from_logical; anchor mode's reach round you too).
    It is hidden (overview_cursor.py), so the dot is the only pointer shown, but it is really there: hover and
    focus-follows-mouse, every button and the wheel go to the window under it, with no bind in their way, except the
    left button below. Moving the dot into a desktop (from free space, below) warps the real cursor to the matching
    point on that desktop's Hyprland monitor (free_pointer._enter, INSET_PX inside its edges).
  * Click to zoom. In desktop mode the left button is bound non-consuming (CLICK_BINDS: Hyprland's `non_consuming`, so
    the window gets the press and release as usual) to the run's resize_press / resize_release. A press and release
    with the dot no more than free_pointer.CLICK_PX apart on the same desktop is a click: the window had it, and the
    view zooms into that desktop as Ctrl+Alt+Up does (view.ViewControl.focus_desktop); the cursor stays where it was
    clicked (release_at). A press that moves further is a window drag or a text selection: no zoom.
  * Button held. While the left button is down in desktop mode the dot never turns free: it follows the real cursor,
    including onto the next monitor in Hyprland's layout (a window or selection dragged across), so the window that
    had the press also gets the release. A cursor that leaves the XR monitors while held lets go of the overview
    pointer (away).
  * Past the edge (free mode). The cursor reaching its desktop's edge pixel turns the dot free, as before: the cursor is
    hidden and parked just inside where it left (input_lock.park_point, PARK_MARGIN_PX in, so the window it was over
    keeps focus-follows-mouse focus; leash input_lock.LEASH_PX), and its motion moves the dot through the space round
    the desktops at that desktop's own scale. Moving ENTER_PX into any desktop, in 3D (front desktop first, every
    placement, curvature and orbit), hands the real cursor back there. While free there is no window under the dot,
    so nothing may reach one: the free mode grab is the input lock's (input_lock.lock_pointer_lua): the left button
    drives pills, corners, sliders, the help card and a desktop click (zoom), the other buttons only end a drag and the
    wheel is bound to nothing.
  * Warps. A keyboard focus change (Ctrl+Alt+Left / Right, the send-window keys) that moves the cursor more than
    free_pointer.JUMP_PX is followed in both modes: the dot goes to where the cursor landed on that desktop (desktop
    mode, the real cursor is the pointer again), or, off the XR monitors, the overview pointer lets go (away).
  * In and out of the overview, every viewing mode. Zooming out with the cursor on an XR monitor puts the dot right
    where the cursor is (desktop mode at once). Zooming in, typing mode and a VR video remove the binds and leave the
    cursor where it is, shown; after a click that zoomed in, at the clicked spot (release_at). Anchor, locked and
    follow modes all map through the same overview geometry, so the cursor tracks the dot in each.
  * Compositor state. One bind table (resize.BIND_TABLE) holds whichever set is live: CLICK_BINDS in desktop mode, the
    lock grab in free mode, and every transition replaces it in a single eval (enter_desktop_lua / the lock grab). Every
    existing removal path applies: run end (PointerSampler.stop), a config reload (re-applied), and a dead run, whose
    next left click (the window still gets it) runs the bind helper, which removes the whole table.
"""
import math
import time

from . import free_pointer as fp, input_lock as il, resize, view

CLICK_OPTS = '{ non_consuming = true }', '{ release = true, non_consuming = true }'


def click_binds_lua(python, client, control_socket, run_id, session):
    """Desktop mode: the left button's press and release, non-consuming (the window still gets them), to the bind
    helper's resize_press / resize_release, added to the free pointer's bind table. pcall'd: a refused bind is left
    out, never replaced by a consuming one."""
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


class UnifiedPointer(fp.OverviewPointer):
    """The overview free pointer with the real cursor following the dot (module docstring)."""
    def __init__(self, control, clock=time.monotonic):
        super().__init__(control, clock)
        self.click = None  # desktop mode, left button down: {'index', 'start'} (the dot at the press)
        self.follows = self.window_clicks = 0  # diagnostics

    # --- modes ----------------------------------------------------------------------------------------------------
    def poll(self, now):
        if not self.active() or now - self.last_move >= view.OVERLAY_IDLE_CLOSE_S: self.click = None
        out = self._follow(now) if self.mode == 'free' and self.active() else b''
        return out + super().poll(now)

    def _absolute(self, cursor, now):
        if self.mode == 'desktop' and self.click is not None:
            out = self._held(cursor, now)
        else:
            out = super()._absolute(cursor, now)
        # The click binds go with desktop mode (no warp: the cursor is already where it is), and nothing is bound away.
        if self.mode == 'desktop' and self.want[1] != 'desktop': self._set_want('desktop')
        elif self.mode == 'away' and self.want[1] not in ('away', 'off'): self._set_want('away')
        return out

    def _held(self, cursor, now):
        """Left button down in desktop mode: the dot stays the real cursor, onto whichever XR monitor it is on."""
        at = self._at(cursor)
        if at is None:
            self.mode, self.desktop, self.pointer, self.click = 'away', None, None, None
            return b''
        out = b''
        if at[0] != self.desktop:
            self.desktop = at[0]
            out += self._select(at[0])
        point = self._from_logical(*at)
        if point is not None and point != self.pointer: self.pointer, self.last_move = point, now
        return out

    def _onto_help(self):
        if self.click is None: super()._onto_help()  # a held button stays with its window until released

    def _park(self, o, local=None):
        """Free mode holds the hidden cursor just inside where it left the desktop (input_lock.park_point), never in
        the middle of the monitor: with focus-follows-mouse the window it was over keeps the focus."""
        cursor = (o['x'] + local[0], o['y'] + local[1]) if local is not None else self._abs
        return il.park_point(o, cursor)

    def _follow(self, now):
        """Free mode: a parked cursor more than JUMP_PX away was warped by a focus change, not the hand. The dot goes
        where it landed (desktop mode there), or the pointer lets go if that is off the XR monitors."""
        _, kind, park = self.want
        with self._lock: cursor, applied = self._abs, self._applied
        if kind != 'free' or park is None or cursor is None or applied < self._sync: return b''
        if math.hypot(cursor[0] - park[0], cursor[1] - park[1]) <= fp.JUMP_PX: return b''
        self.follows += 1
        out = self._end_drag()
        self.hover = None
        at = self._at(cursor)
        if at is None:
            self.mode, self.desktop, self.pointer = 'away', None, None
            self._set_want('away')
            return out
        self.mode, self.desktop = 'desktop', at[0]
        self.pointer = self._from_logical(*at)
        self._set_want('desktop')  # the grab goes, the click binds come back; the cursor stays where it landed
        return out + self._select(at[0])

    # --- the left button ------------------------------------------------------------------------------------------
    def on_op(self, op):
        if op not in resize.RESIZE_OPS: raise ValueError('unsupported resize operation')
        if self.mode == 'desktop' or (op == 'resize_release' and self.click is not None):
            return self._desktop_button(op)
        self.click = None
        return super().on_op(op)

    def _desktop_button(self, op):
        """Desktop mode's non-consuming left button: the window already has it; a click (no drag) also zooms in."""
        now = self.clock()
        idle = now - self.last_move >= view.OVERLAY_IDLE_CLOSE_S
        self.last_move = now  # input: the dot and pills come back
        out = b''
        live = self.active() and self.mode == 'desktop' and self.pointer is not None
        if op == 'resize_press':
            self.click = {'index': self.desktop, 'start': self.pointer} if live and not idle else None
        else:
            c, self.click = self.click, None
            self.window_clicks += 1
            if c is not None and live and c['index'] == self.desktop \
                    and math.hypot(self.pointer[0] - c['start'][0], self.pointer[1] - c['start'][1]) <= fp.CLICK_PX:
                with self._lock: cursor = self._abs
                self.focus_clicks += 1
                self.release_at = (int(cursor[0]), int(cursor[1])) if cursor is not None else None  # stays where clicked
                out += self.control.focus_desktop(c['index'])
        return (out + self._handle() + self._pills(False) + self._pointer_frame(False)) or None


class UnifiedSampler(il.LockedSampler):
    """The free pointer's sampler for the unified cursor: free mode grabs with the lock's grab (short leash, warps left
    alone); desktop mode installs the non-consuming click binds and warps the cursor to where the dot went in."""
    def __init__(self, client, names, pointer, bind_args, period=fp.SAMPLE_S):
        super().__init__(client, names, pointer, bind_args, period)
        self.bound = False  # the click binds are installed (desktop mode)

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
