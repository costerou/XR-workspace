"""Privacy mode during a run: Ctrl+Alt+D blanks the main physical display, so only the glasses show content.

The main display is the physical monitor that is neither the glasses' output (the run's presentation output, e.g. the
VITURE on DP-2) nor one of the run's virtual desktops (XR-1..XR-4), nor one whose description names VITURE: the run's
physical fallback (the output Stop and Recover return every window to, e.g. DP-1) when it qualifies, else the only
monitor that does. With two or more candidates and none of them the fallback, nothing is blanked and a notification says so.

How. DPMS, never disabling the monitor. A disabled monitor loses its workspaces: Hyprland moves them, and their
windows, onto another monitor (the glasses or an XR desktop), and the run's guard (supervisor.validate_physical_pair)
and recovery need the fallback connected and enabled to put windows back. DPMS only powers the panel down: the
monitor, its workspaces and windows, and the layout all stay as they are, and nothing about XR capture changes (the
renderer captures the headless XR-n outputs per output, never DP-1, and presents on the glasses' own output).

Hyprland 0.56.2 detail (src/config/shared/actions/ConfigActions.cpp Actions::dpms): every dpms dispatch also sets the
compositor-wide m_dpmsStateOn to the last state it applied, and with Omarchy's misc:key_press_enables_dpms and
misc:mouse_move_enables_dpms (both on) the next key press, modifier change or mouse move while it is false turns every
monitor back on: releasing Ctrl+Alt would undo the blank at once. So the blank is two dispatches in one eval: `off` for
the main display, then `on` for the glasses' output. That output is already on, so CMonitor::setDPMS returns at once
(no commit, no flicker), but the flag is true again and input no longer wakes the main display. A dispatch whose
monitor selector does not resolve applies to every monitor, so the generated Lua first checks that both names exist
(hl.get_monitors) in the same eval; nothing is dispatched otherwise. Names are validated physical connectors.

Toggle. The worker reads the main display's current dpmsStatus: on -> blank it; off -> turn it on. So if something
else turned it back on (an idle daemon's resume, a config reload that re-lit the panel), one press blanks it again.
Each change shows a desktop notification, which lands on the focused monitor, i.e. in the glasses.

Restore. The run turns the display back on at its end (close(), before the binds go and before recovery starts), and
recovery itself (managed.recover_restoring, managed.recover_managed: Stop, a renderer or controller crash, the panel's
Recover) turns the run's fallback back on first, so a crash never leaves it dark. If the run died and its binds
survived, Ctrl+Alt+D finds no run listening and xr_control.py turns every monitor's DPMS on instead. The guard
tolerates the fallback being DPMS-off (it still has to be connected, enabled and not mirrored); the glasses' output
still has to be on.
"""
import re
import threading

PRIVACY_OP = 'privacy_display'
PRIVACY_BIND = ('CTRL + ALT + D', PRIVACY_OP)
PHYSICAL = re.compile(r'(?:DP|HDMI-A|eDP|DVI-D)-[0-9]{1,3}\Z')  # supervisor.validate_physical_pair's connectors
VIRTUAL = re.compile(r'XR-[0-9]+\Z')


def _name(name):
    if not isinstance(name, str) or not PHYSICAL.match(name): raise ValueError('invalid physical output name')
    return name


def main_display(monitors, glasses, fallback=None):
    """Output name of the main physical display (module docstring), or None. `monitors`: Hyprland `monitors` JSON."""
    if not isinstance(monitors, list): raise ValueError('invalid monitors')
    found = []
    for m in monitors:
        if not isinstance(m, dict) or not isinstance(m.get('name'), str): continue
        name, description = m['name'], m.get('description')
        if name == glasses or VIRTUAL.match(name) or not PHYSICAL.match(name): continue
        if isinstance(description, str) and 'viture' in description.lower(): continue
        if m.get('disabled') is True or m.get('mirrorOf') not in (None, 'none'): continue
        found.append(name)
    if fallback in found: return fallback
    return found[0] if len(found) == 1 else None


def dpms_status(monitors, name):
    """True / False: `name`'s DPMS state; None if it is not there."""
    found = [m for m in monitors if isinstance(m, dict) and m.get('name') == name] if isinstance(monitors, list) else []
    if len(found) != 1 or type(found[0].get('dpmsStatus')) is not bool: return None
    return found[0]['dpmsStatus']


def blank_lua(target, glasses):
    """Main display off, then the (already on) glasses' output `on` so input does not wake it; both must exist."""
    target, glasses = _name(target), _name(glasses)
    if target == glasses: raise ValueError('the main display cannot be the glasses')
    return (f'local t,g="{target}","{glasses}" local a,b=false,false '
            'for _,m in ipairs(hl.get_monitors()) do if m.name==t then a=true elseif m.name==g then b=true end end '
            'if a and b then hl.dispatch(hl.dsp.dpms({action="off",monitor=t})) '
            'hl.dispatch(hl.dsp.dpms({action="on",monitor=g})) end')


def wake_lua(target):
    """The display back on (only if it exists: an unresolved selector would apply to every monitor)."""
    target = _name(target)
    return (f'local t="{target}" for _,m in ipairs(hl.get_monitors()) do if m.name==t then '
            'hl.dispatch(hl.dsp.dpms({action="on",monitor=t})) break end end')


# Stale bind (xr_control.py, no run listening): every monitor on. Fixed Lua; the same text is in xr_control.py.
WAKE_ALL_LUA = 'hl.dispatch(hl.dsp.dpms({action="on"}))'


def wake(client, name, deadline=2.0):
    """Recovery: `name` back on if it is DPMS-off. Best effort, never raises; True if it was turned on."""
    if not hasattr(client, 'set_dpms') or not isinstance(name, str) or not PHYSICAL.match(name): return False
    try:
        if dpms_status(client.query('monitors', timeout=deadline), name) is not False: return False
        client.set_dpms(name, True)
        return True
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return False


def _default_notify(message):
    from .send import notify
    return notify(message)


class PrivacyDisplay:
    """Supervisor side of Ctrl+Alt+D: one toggle at a time on a worker thread; close() turns the display back on."""
    def __init__(self, client, glasses, fallback, notify=_default_notify, deadline=2.0):
        self.client, self.glasses, self.fallback, self.notify, self.deadline = client, glasses, fallback, notify, deadline
        self._lock = threading.Lock()  # held for a whole toggle; close() waits on it
        self.closed = False
        self.blanked = None            # the output this run turned off, while it is off
        self._threads = []

    def request(self):
        """From the supervisor loop: never blocks on the compositor."""
        if self.closed: return
        t = threading.Thread(target=self._work, daemon=True); t.start()
        self._threads = [x for x in self._threads if x.is_alive()] + [t]

    def _work(self):
        with self._lock:
            if self.closed: return
            try: message = self.toggle()
            except (OSError, ValueError, TypeError, KeyError, AttributeError): message = 'Could not change the main display.'
        if message and self.notify is not None: self.notify(message)

    def toggle(self):
        """Do it now (caller holds the lock). Returns the message for the user."""
        monitors = self.client.query('monitors', timeout=self.deadline)
        target = main_display(monitors, self.glasses, self.fallback)
        if target is None: return 'No single main display found to turn off.'
        on = dpms_status(monitors, target)
        if on is None: return f'{target} is not available.'
        if on:
            self.client.set_dpms(target, False, keep=self.glasses)
            if dpms_status(self.client.query('monitors', timeout=self.deadline), target) is not False:
                return f'{target} did not turn off.'
            self.blanked = target
            return f'Privacy on: {target} is off, only the glasses show content. Ctrl+Alt+D turns it back on.'
        self.client.set_dpms(target, True)
        self.blanked = None
        return f'Privacy off: {target} is back on.'

    def close(self, timeout=None):
        """Run end, before the binds go and recovery starts: no toggle starts after this; the display comes back on."""
        self.closed = True
        got = self._lock.acquire(timeout=3 * self.deadline + 0.5 if timeout is None else timeout)
        try:
            if self.blanked is not None:
                try: self.client.set_dpms(self.blanked, True)
                except (OSError, ValueError, TypeError, KeyError, AttributeError): pass  # recovery wakes it again
                self.blanked = None
        finally:
            if got: self._lock.release()
        for t in self._threads: t.join(0.1)
