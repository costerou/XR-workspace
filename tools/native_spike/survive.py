"""A run outlives a USB drop of the glasses (docs/usb-drops.md).

The hub in the glasses can go at any time, and the second drop of a pair takes the display with it. The renderer cannot live
without its output, but everything that matters is not in it: the XR desktops are the compositor's, their windows stay on them,
and the renderer rebuilds itself from the supervisor's state, as at any start. So a drop ends the renderer and not the run: the
session waits for the glasses, puts their mode back and starts a new renderer. Stop works throughout (a KeyboardInterrupt, as
ever), and a wait that runs out ends the run the ordinary way, windows restored.
"""
from collections import deque
from pathlib import Path
import time

GLASSES_USB = ('35ca', '1104')       # the Luma Ultra's vendor interface: the one the driver opens
SYS_USB = Path('/sys/bus/usb/devices')
HOLD_LIMIT_S = 120.0                 # longer than any drop on record that was not a person unplugging them
SETTLE_S = 3.0                       # up this long without a flicker: the hub drops in pairs up to 28 s apart, and a pair
                                     # caught here costs a new hold, not the session
POLL_S = 0.25
MAX_HOLDS = 8                        # in HOLD_WINDOW_S: past this it is a bad cable, and the desktops belong on a monitor
HOLD_WINDOW_S = 600.0
# Ends that are final whatever the glasses did. A tracking timeout is the supervisor giving up on an outage after the limit
# (docs/technical.md, Head tracking drops), and the USB number has moved by then, so it would otherwise read as a drop.
NOT_A_DROP = ('interrupted', 'tracking-timeout')


def usb_identity(root=SYS_USB):
    """(bus, device number) of the glasses, or None. The number rises at every enumeration, so a change is a drop, however
    quick. Port names are not used: the glasses' port depends on where the cable is."""
    try:
        for device in root.iterdir():
            try:
                if (device / 'idVendor').read_text().strip() == GLASSES_USB[0] and (device / 'idProduct').read_text().strip() == GLASSES_USB[1]:
                    return int((device / 'busnum').read_text()), int((device / 'devnum').read_text())
            except (OSError, ValueError):
                continue
    except OSError:
        pass
    return None


def monitors_of(client):
    from .hyprland import monitors
    return monitors(client.query('monitors'))


def output_ok(client, output):
    """The output is there and in use. Any failure reading it is the caller's: a compositor gone is not a drop."""
    return any(m['name'] == output and not m['disabled'] for m in monitors_of(client))


def should_hold(returncode, stop_reason, *, output_ok, usb_before, usb_now):
    """Was this renderer's end a drop of the glasses? Only with evidence: a crash with the glasses fine is still a crash."""
    if stop_reason in NOT_A_DROP: return False
    if returncode == 0 and stop_reason is None: return False
    return not output_ok or (usb_before is not None and usb_before != usb_now)


class Holds:
    """This run's holds, and the limit on them."""
    def __init__(self, clock=time.monotonic):
        self.clock, self.record, self._recent, self._since = clock, [], deque(), None

    def begin(self):
        now = self.clock()
        while self._recent and now - self._recent[0] > HOLD_WINDOW_S: self._recent.popleft()
        if len(self._recent) >= MAX_HOLDS: return False
        self._recent.append(now)
        self._since = now
        return True

    def finish(self, resumed):
        self.record.append({'seconds': round(self.clock() - self._since, 1), 'resumed': resumed})

    @property
    def survived(self): return sum(1 for h in self.record if h['resumed'])


def wait_for_glasses(ready, *, restore, serve, clock=None, limit=HOLD_LIMIT_S, settle=None):
    """Until `ready()` has held for `settle` seconds and `restore()` took: True. False when `limit` runs out. `serve(seconds)`
    is the wait: it answers the panel meanwhile. A restore that fails (the mode not read back yet) is another try, not the end."""
    clock = clock or time.monotonic
    settle = SETTLE_S if settle is None else settle
    end = clock() + limit
    since = None
    while clock() < end:
        if not ready(): since = None
        else:
            now = clock()
            if since is None: since = now
            if now - since >= settle:
                try:
                    restore()
                    return True
                except (OSError, ValueError, TimeoutError):
                    since = None
        serve(POLL_S)
    return False


def revive_tracking(runtime, tracking):
    """The driver reconnects by itself, so a live one is left alone, even mid-calibration; only a dead one is started. False:
    no head tracking for now, which is a still picture and not a reason to lose the session."""
    try:
        if not tracking.status(runtime)['running']: tracking.restart_same(runtime)
        return True
    except (OSError, ValueError):
        return False
