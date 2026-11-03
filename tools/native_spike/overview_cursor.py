"""Circle only: in the overview the free pointer's dot is the only pointer shown; the real cursor is hidden.

Design (overview-uc-circle-only, 2026-11-02). Display only: what the cursor does is free_pointer.py's and unchanged.
  * While the overview pointer is on the XR desktops (free_pointer.OverviewPointer mode in DOT_MODES: inside a desktop,
    or free between them) the renderer draws the dot (free_pointer._pointer_frame) and the real cursor is hidden with
    Hyprland's `cursor.invisible`, so the captured desktops no longer paint it. Inside a desktop the dot sits where the
    cursor maps onto that desktop's surface, and the hidden cursor still hovers, clicks and drags the window under it.
  * The cursor shows again on the physical monitor (mode away), outside the overview (zoomed in, typing mode, a VR video:
    OverviewPointer.overview), and at run end.
  * Compositor state: HIDE_LUA remembers the cursor's own visibility once in TABLE and hides it; SHOW_LUA puts that back
    and forgets it. Free mode keeps its own record in resize.GRAB_TABLE (it hides the cursor too, and its hand-back and
    bind removal restore what it recorded). The two are kept consistent so their calls can run in any order: while
    TABLE exists it holds the cursor's own visibility and the grab record says hidden (a grab made meanwhile records it
    hidden; a hide made during a grab takes over the grab's record), so a hand-back into a desktop keeps the cursor
    hidden. SHOW_LUA during a grab hands the visibility back to the grab record and leaves the cursor hidden, so the
    grab's own restore brings it back.
  * Threads: the free pointer's sampler thread makes these calls (PointerSampler: after its binds, every sample) through
    OverviewCursor, which only calls when the wanted state changes. Its state starts unknown, so a run's first sample
    always sends one and puts back a cursor a killed run left hidden. A config reload resets `cursor.invisible`, so the
    next sample sends again, at most once per RELOAD_REAPPLY_S (later reloads wait, never dropped) in case hiding the
    cursor itself reloads: it can never loop. Run end (PointerSampler.stop) shows it after removing the binds.
  * A killed run (stopped runs clean up): xr_control.py's stale-bind cleanups (a dead run's left button or panel key)
    send SHOW_LUA after their own code. A run killed with neither bind left (inside a desktop, no panel open) leaves the
    cursor hidden until the next run starts or Hyprland reloads its config.
"""
import time

from .resize import GRAB_TABLE

DOT_MODES = ('desktop', 'free', 'carry')  # OverviewPointer modes with the dot drawn and the real cursor hidden
RELOAD_REAPPLY_S = 2.0           # config reloads: at most one forced re-send per this long (free mode's regrab limit)
TABLE = 'xr_workspace_overview_cursor'
HIDE_LUA = (f'do local g = {GRAB_TABLE} if {TABLE} == nil then local invisible '
            'if g then invisible = g.invisible == true '
            'else local ok, hidden = pcall(hl.get_config, "cursor.invisible") invisible = ok and hidden == true end '
            f'{TABLE} = {{ invisible = invisible }} end '
            'if g then g.invisible = true end '
            'pcall(hl.config, { cursor = { invisible = true } }) end')
# xr_control.SHOW_OVERVIEW_CURSOR repeats this byte for byte (that helper is stdlib-only; a test compares).
SHOW_LUA = (f'do local s = {TABLE} {TABLE} = nil if s then '
            f'if {GRAB_TABLE} then {GRAB_TABLE}.invisible = s.invisible '
            'else pcall(hl.config, { cursor = { invisible = s.invisible } }) end end end')


class OverviewCursor:
    """The real cursor's visibility for one run, applied from the sampler thread. `hidden` is what was last applied:
    None (unknown: nothing yet, or a config reload since), True or False."""
    def __init__(self, control, clock=time.monotonic):
        self.control, self.clock = control, clock
        self._reloads = getattr(control, 'reloads', 0)  # view.ViewControl counts config reloads
        self._reload_due, self._forced_at = False, None
        self.hidden = None
        self.errors = 0

    def step(self, client, hidden):
        """Hide (True) or show (False) the cursor unless it already is; a failed call is retried on the next step."""
        reloads = getattr(self.control, 'reloads', 0)
        if reloads != self._reloads: self._reloads, self._reload_due = reloads, True
        if self._reload_due and self.hidden is False: self._reload_due = False  # shown: the reload changed nothing
        if self._reload_due:
            now = self.clock()
            if self._forced_at is None or now - self._forced_at >= RELOAD_REAPPLY_S:
                self._reload_due, self._forced_at, self.hidden = False, now, None  # it put the config value back
        if hidden == self.hidden: return
        call = getattr(client, 'hide_overview_cursor' if hidden else 'show_overview_cursor', None)
        if call is None: return
        try: call()
        except (OSError, ValueError, TypeError, KeyError):
            self.errors += 1
            return
        self.hidden = hidden

    def stop(self, client):
        """Run end, after the free pointer's binds are removed: show it (best effort) unless it is known shown."""
        if self.hidden is False: return
        call = getattr(client, 'show_overview_cursor', None)
        if call is None: return
        try: call()
        except (OSError, ValueError, TypeError, KeyError): self.errors += 1
        else: self.hidden = False
