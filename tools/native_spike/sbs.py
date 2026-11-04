"""The glasses' side-by-side 3D mode (docs/immersive-3d.md has the evidence).

How the Luma Ultra gets there. VITURE's manual: hold the R1 button on the glasses. Their SDK's table lists 3840x1200 at 60 and 90 Hz
for this model, and nothing faster. There is no USB command to send instead: the only frames ever captured from VITURE's
software are the tracking ones (spike/driver/docs/capture-findings.md), no mode set has been seen on the wire, firmware
flashing shares the channel, and an id is never guessed at the glasses. So nothing here touches USB: a person holds the button,
which is also the only consent a change of the glasses' own mode could need, and this follows what DisplayPort then says.

What happens on DisplayPort has never been seen. The 2D EDID offers no wide mode, and the dot clock it allows is below
3840x1200@60's, so the wide modes can only come from a new EDID after the press. If Hyprland then lists them, this asks for one
the way the run asks for 1920x1200@120 (ButtonSbs._apply: a Lua hl.monitor rule, read back). The press may well look like the
glasses unplugged for a moment: the renderer ends, survive.py holds, and its restore (managed._restore_output) asks here which
mode to put back, which is why a press does not end the run. `switching()` tells it that this one was expected.

Going back is the same button. leave() only says so and returns: the picture is already flat, the same in both eyes, so
staying in side-by-side costs 120 Hz and nothing else, and a run must not wait on a person. settle() watches for the wide
modes going, and puts 120 Hz back.
"""
import re
import time

from .hyprland import monitors

WIDE = (3840, 1200)
RATES = (90.0, 60.0)      # best first: VITURE's table stops at 90
ENTER_WAIT_S = 45.0       # to find the button, wearing the glasses
POLL_S = 0.25
EXPECT_S = 20.0           # a renderer ending this soon after a change of mode is that change, not a fault
MODE = re.compile(r'(\d+)x(\d+)@([0-9.]+)Hz\Z')
SCOPE_KEY = 'sbs_mode'    # the consent's own scope: panel_consent.py
SCOPE = {'width': WIDE[0], 'height': WIDE[1], 'refresh_hz': [int(r) for r in RATES]}
PROMPT_ON = 'Hold R1 on the glasses to switch them to 3D.'
PROMPT_OFF = 'Hold R1 on the glasses to go back to 2D and 120 Hz.'
ON_FAILED = 'the glasses never offered 3840x1200: was R1 held?'
ONLY_NARROW = 'they offered only {modes}, and 3D here needs 3840x1200'


class SbsError(Exception):
    """The glasses could not change mode. Its text goes in a toast."""


def offered(monitor):
    """Every (width, height, hz) Hyprland lists for this monitor. Anything it does not parse is not a mode."""
    out = []
    for text in (monitor.get('availableModes') or []) if isinstance(monitor, dict) else ():
        m = MODE.match(text) if isinstance(text, str) else None
        if m: out.append((int(m[1]), int(m[2]), float(m[3])))
    return out


def best_wide(monitor):
    """The rate to ask for, or None: the glasses are not offering 3840x1200 at a rate VITURE lists."""
    modes = offered(monitor)
    for rate in RATES:
        for width, height, hz in modes:
            if (width, height) == WIDE and abs(hz - rate) < 0.5: return rate
    return None


def is_wide(monitor):
    """Being driven at 3840x1200 now."""
    return isinstance(monitor, dict) and (monitor.get('width'), monitor.get('height')) == WIDE


def wide_mode_lua(name, x, y, scale, rate):
    """The rule for a wide mode: fixed shape, like view.restore_mode_lua, from values checked here."""
    if not re.fullmatch(r'(?:DP|HDMI-A)-[0-9]+', str(name)) or type(x) is not int or type(y) is not int: raise ValueError('invalid wide mode')
    if not isinstance(scale, (int, float)) or not 0.25 <= scale <= 4 or rate not in RATES: raise ValueError('invalid wide mode')
    return f'hl.monitor({{ output = "{name}", mode = "{WIDE[0]}x{WIDE[1]}@{rate:g}", position = "{x}x{y}", scale = {float(scale):g} }})'


def scope_ok(value):
    return value == SCOPE


class ButtonSbs:
    """The Sbs of a real run (immersive.py). Everything it does to Hyprland is reading the monitor list, and one mode rule."""
    def __init__(self, client, output, tell=lambda text: None, *, clock=time.monotonic, sleep=time.sleep, enter_wait=ENTER_WAIT_S):
        self.client, self.output, self.tell = client, output, tell
        self.clock, self.sleep, self.enter_wait = clock, sleep, enter_wait
        self.left_at = None     # when leave() last asked for the button: the glasses are still wide, until settle() sees otherwise
        self.expect_until = 0.0

    def _monitor(self):
        """The output, or None while it is away (a press may do that)."""
        try: found = [m for m in monitors(self.client.query('monitors')) if m['name'] == self.output]
        except (OSError, ValueError, TimeoutError, KeyError, TypeError): return None
        return found[0] if len(found) == 1 else None

    def _apply(self, monitor, rate):
        apply = getattr(self.client, 'apply_wide_mode', None)
        if apply is None: raise SbsError('this client cannot change mode')
        try: apply(self.output, rate)
        except (OSError, ValueError, TimeoutError) as exc: raise SbsError(str(exc) or type(exc).__name__) from exc

    def _expect(self):
        self.expect_until = self.clock() + EXPECT_S

    def switching(self):
        """A change of mode is under way or just happened, so the output going away is that and not a drop."""
        return self.left_at is not None or self.clock() < self.expect_until

    def enter(self, cancelled=lambda: False):
        """Until the glasses are wide and driven wide. False if `cancelled()` came true first. Raises SbsError if the button was
        not held in time or the mode would not take."""
        self.left_at = None
        monitor = self._monitor()
        rate = best_wide(monitor)
        if rate is None:
            self.tell(PROMPT_ON)
            self._expect()
            deadline, narrow = self.clock() + self.enter_wait, []
            while rate is None:
                if cancelled(): return False
                if self.clock() >= deadline:
                    raise SbsError(ONLY_NARROW.format(modes=', '.join(f'{w}x{h}' for w, h in narrow)) if narrow else ON_FAILED)
                self.sleep(POLL_S)
                self._expect()
                monitor = self._monitor()
                rate = best_wide(monitor)
                narrow = sorted({(w, h) for w, h, _ in offered(monitor) if w >= 2 * h}) or narrow
        self._expect()
        if not is_wide(monitor) or abs(float(monitor.get('refreshRate', 0)) - rate) >= 0.5: self._apply(monitor, rate)
        return True

    def leave(self):
        """Never waits: the renderer is flat by now, and only a person can press the button. Glasses that are not wide: nothing."""
        monitor = self._monitor()
        if best_wide(monitor) is None and not is_wide(monitor): return
        if self.left_at is None:
            self.left_at = self.clock()
            self.tell(PROMPT_OFF)
        self._expect()

    def parked(self):
        return self.left_at is not None

    def settle(self):
        """Glasses still wide after leave(): True once they have gone back, with the run's session mode on again."""
        monitor = self._monitor()
        if monitor is None: return False  # the press took the output: survive.py is on it
        if best_wide(monitor) is not None or is_wide(monitor): return False
        self.left_at = None
        apply = getattr(self.client, 'apply_session_mode', None)
        if apply is not None:
            try: apply(self.output)
            except (OSError, ValueError, TimeoutError): pass  # survive.py's restore tries as well; a run does not end here
        return True
