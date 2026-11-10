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

Pro 2 uses L1 instead. Its observed 3D mode is 3840x1080 at 60 Hz. The same button-driven flow follows only that advertised
mode, then restores the previously observed 1080p refresh after L1 returns to 2D, if the glasses still offer it.

A Pro 2 panel session needs no button. Its bridge owns VITURE's SDK, and the run hands that switch in as `glasses`
(pro2_runtime.launch.RuntimeSession.set_display). enter() then turns the glasses to 3D and leave() turns them back to
1920x1080@120, with no prompt, and everything after it is the same readback as a press. If the switch cannot be sent, the
prompt comes back. leave() is only reached once no desktop is 3D (immersive.ImmersiveRun._down).
"""
import math
import re
import sys
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
PRO2_SCOPE = {'width': 3840, 'height': 1080, 'refresh_hz': [60]}
PROMPT_ON = 'Hold R1 on the glasses to switch them to 3D.'
PROMPT_OFF = 'Hold R1 on the glasses to go back to 2D and 120 Hz.'
ON_FAILED = 'the glasses never offered 3840x1200: was R1 held?'
ONLY_NARROW = 'they offered only {modes}, and 3D here needs 3840x1200'


def trace(step):
    """One journal line per step of a 2D/3D switch, stamped with CLOCK_MONOTONIC (the clock of the renderer's exit-report
    stall timestamps, and the journal's monotonic time), so the switch timeline is read from the run's journal."""
    print(f'xr-workspace: switch: t={time.monotonic():.3f} {step}', file=sys.stderr, flush=True)


class SbsError(Exception):
    """The glasses could not change mode. Its text goes in a toast."""


def offered(monitor):
    """Every (width, height, hz) Hyprland lists for this monitor. Anything it does not parse is not a mode."""
    out = []
    for text in (monitor.get('availableModes') or []) if isinstance(monitor, dict) else ():
        m = MODE.match(text) if isinstance(text, str) else None
        if m: out.append((int(m[1]), int(m[2]), float(m[3])))
    return out


def _mode(model):
    if model == 'pro2': return (3840, 1080), (60.0,)
    if model == 'luma-ultra': return WIDE, RATES
    raise ValueError('unsupported 3D glasses model')


def best_wide(monitor, model='luma-ultra'):
    """The rate to ask for, or None: the glasses are not offering this model's supported side-by-side mode."""
    try: wide, rates = _mode(model)
    except ValueError: return None
    modes = offered(monitor)
    for rate in rates:
        for width, height, hz in modes:
            if (width, height) == wide and abs(hz - rate) < 0.5: return rate
    return None


def is_wide(monitor, model='luma-ultra'):
    """Being driven at this model's side-by-side dimensions now."""
    try: wide, _ = _mode(model)
    except ValueError: return False
    return isinstance(monitor, dict) and (monitor.get('width'), monitor.get('height')) == wide


def wide_mode_lua(name, x, y, scale, rate, model='luma-ultra'):
    """The rule for a wide mode: fixed shape, like view.restore_mode_lua, from values checked here."""
    wide, rates = _mode(model)
    if type(rate) not in (int, float) or rate not in rates: raise ValueError('invalid wide mode')
    return _mode_lua(name, x, y, scale, *wide, rate)


def _mode_lua(name, x, y, scale, width, height, rate):
    if not re.fullmatch(r'(?:DP|HDMI-A)-[0-9]+', str(name)) or type(x) is not int or type(y) is not int: raise ValueError('invalid wide mode')
    if type(scale) not in (int, float) or not 0.25 <= scale <= 4: raise ValueError('invalid wide mode')
    return f'hl.monitor({{ output = "{name}", mode = "{width}x{height}@{rate:g}", position = "{x}x{y}", scale = {float(scale):g} }})'


def scope_ok(value, model='luma-ultra'):
    expected = PRO2_SCOPE if model == 'pro2' else SCOPE if model == 'luma-ultra' else None
    return (expected is not None and isinstance(value, dict) and value == expected
            and type(value.get('width')) is int and type(value.get('height')) is int
            and isinstance(value.get('refresh_hz'), list) and all(type(rate) is int for rate in value['refresh_hz']))


def narrow_rate(monitor, model='luma-ultra'):
    """The observed, supported 2D refresh, never a guessed rate for a wide or absent output."""
    try: wide, _ = _mode(model)
    except ValueError: return None
    if not isinstance(monitor, dict) or (monitor.get('width'), monitor.get('height')) != (wide[0] // 2, wide[1]): return None
    rate = monitor.get('refreshRate')
    allowed = (60, 90, 120) if model == 'pro2' else (120,)
    if type(rate) in (int, float) and math.isfinite(rate) and any(abs(rate - hz) < 0.5 for hz in allowed): return rate
    return None


def pro2_2d_rate(monitor, prefer=None):
    """The 1920x1080 rate a Pro 2 back in 2D is to be driven at, or None while it is not back (still offering 3D, or away).
    The offered modes say it is back, not the current one: the run's wide rule keeps Hyprland driving 3840x1080 after the glasses
    stop offering it (probed). `prefer`, the rate seen before 3D, if offered; else the current 2D rate; else the fastest offered."""
    if not isinstance(monitor, dict) or best_wide(monitor, 'pro2') is not None: return None
    rates = [hz for w, h, hz in offered(monitor) if (w, h) == (1920, 1080)]
    allowed = [hz for hz in (120, 90, 60) if any(abs(r - hz) < 0.5 for r in rates)]
    if prefer is not None and any(abs(prefer - hz) < 0.5 for hz in allowed): return prefer
    current = narrow_rate(monitor, 'pro2')
    return current if current is not None else allowed[0] if allowed else None


def pro2_mode_lua(name, x, y, scale, rate):
    """Restore only Pro 2's 1080p 2D timings; callers also require the timing in the current EDID."""
    if narrow_rate({'width': 1920, 'height': 1080, 'refreshRate': rate}, 'pro2') is None:
        raise ValueError('invalid Pro 2 2D rate')
    return _mode_lua(name, x, y, scale, 1920, 1080, rate)


class ButtonSbs:
    """The Sbs of a real run (immersive.py). Everything it does to Hyprland is reading the monitor list, and one mode rule."""
    def __init__(self, client, output, tell=lambda text: None, *, model='luma-ultra', clock=time.monotonic, sleep=time.sleep, enter_wait=ENTER_WAIT_S,
                 glasses=None):
        self.model = model
        self.glasses = glasses  # switch(on): the glasses' own 3D mode, in place of the button; None asks for the button
        self.wide, _ = _mode(model)
        self.button = 'L1' if model == 'pro2' else 'R1'
        self.narrow_refresh = None
        self.client, self.output, self.tell = client, output, tell
        self.clock, self.sleep, self.enter_wait = clock, sleep, enter_wait
        self.left_at = None     # when leave() last asked for the button: the glasses are still wide, until settle() sees otherwise
        self.expect_until = 0.0
        self.hand_wide = False

    def _monitor(self):
        """The output, or None while it is away (a press may do that)."""
        try: found = [m for m in monitors(self.client.query('monitors')) if m['name'] == self.output]
        except (OSError, ValueError, TimeoutError, KeyError, TypeError): return None
        return found[0] if len(found) == 1 else None

    def follow_by_hand(self):
        monitor = self._monitor()
        if monitor is not None:
            wide = is_wide(monitor, self.model)
            narrow_refresh = narrow_rate(monitor, self.model)
            if not wide and narrow_refresh is None: return
            if not self.hand_wide and not self.switching():
                if narrow_refresh is not None: self.narrow_refresh = narrow_refresh
            if wide and not self.hand_wide and not self.switching():
                self.tell('The glasses are in 3D. Click 3D to enable desktop depth.')
            if wide != self.hand_wide: self._expect()
            self.hand_wide = wide

    def _apply(self, monitor, rate):
        apply = getattr(self.client, 'apply_wide_mode', None)
        if apply is None: raise SbsError('this client cannot change mode')
        try:
            trace(f'enter: wide rule {self.wide[0]}x{self.wide[1]}@{rate:g}')
            apply(self.output, rate, **({'model': self.model} if self.model == 'pro2' else {}))
            trace('enter: wide mode read back')
            self._park()
            trace('enter: parked')
        except (OSError, ValueError, TimeoutError) as exc: raise SbsError(str(exc) or type(exc).__name__) from exc

    def _park(self):
        """The output sits left of the desktops by its own width (hyprland.presentation_placement), which a mode change just changed."""
        park = getattr(self.client, 'park_presentation', None)
        if park is not None: park(self.output)

    def _expect(self):
        self.expect_until = self.clock() + EXPECT_S

    def _switch(self, on):
        """Tell the glasses to change mode. False: nobody can, so a person has to press."""
        if self.glasses is None: return False
        trace(f"glasses {'3D' if on else '2D'} requested")
        try: self.glasses(on)
        except (OSError, ValueError) as exc:
            trace(f'glasses switch failed: {exc}')
            return False
        trace(f"glasses {'3D' if on else '2D'} sent")
        return True

    def switching(self):
        """A change of mode is under way or just happened, so the output going away is that and not a drop."""
        return (self.left_at is not None and self.clock() - self.left_at < ENTER_WAIT_S) or self.clock() < self.expect_until

    def enter(self, cancelled=lambda: False):
        """Until the glasses are wide and driven wide. False if `cancelled()` came true first. Raises SbsError if the button was
        not held in time or the mode would not take."""
        self.left_at = None
        monitor = self._monitor()
        narrow_refresh = narrow_rate(monitor, self.model)
        if narrow_refresh is not None: self.narrow_refresh = narrow_refresh
        rate = best_wide(monitor, self.model)
        if rate is None:
            if not self._switch(True): self.tell(f'Hold {self.button} on the glasses to switch them to 3D.')
            self._expect()
            deadline, narrow = self.clock() + self.enter_wait, []
            while rate is None:
                if cancelled(): return False
                if self.clock() >= deadline:
                    shape = f'{self.wide[0]}x{self.wide[1]}'
                    raise SbsError(f"they offered only {', '.join(f'{w}x{h}' for w, h in narrow)}, and 3D here needs {shape}" if narrow
                                   else f'the glasses never offered {shape}: was {self.button} held?')
                self.sleep(POLL_S)
                self._expect()
                monitor = self._monitor()
                rate = best_wide(monitor, self.model)
                if rate is not None: trace('enter: wide mode offered')
                narrow = sorted({(w, h) for w, h, _ in offered(monitor) if w >= 2 * h}) or narrow
        self._expect()
        if not is_wide(monitor, self.model) or abs(float(monitor.get('refreshRate', 0)) - rate) >= 0.5: self._apply(monitor, rate)
        self.hand_wide = True
        return True

    def leave(self):
        """Never waits: the renderer is flat by now. Switches the glasses back if it can, else asks for the button; settle()
        follows either. Glasses that are not wide: nothing."""
        monitor = self._monitor()
        if best_wide(monitor, self.model) is None and not is_wide(monitor, self.model): return
        if self.left_at is None:
            self.left_at = self.clock()
            if not self._switch(False): self.tell('Hold L1 on the glasses to go back to 2D.' if self.model == 'pro2' else PROMPT_OFF)
        self._expect()

    def parked(self):
        return self.left_at is not None

    def left_by_hand(self):
        """3D is up, but the mode button was held: the glasses are back in 2D on their own. False while the output is away: that is a hold."""
        monitor = self._monitor()
        if self.model == 'pro2': left = narrow = pro2_2d_rate(monitor) is not None
        else:
            left = monitor is not None and best_wide(monitor, self.model) is None and not is_wide(monitor, self.model)
            narrow = narrow_rate(monitor, self.model) is not None
        if left and narrow:
            self._expect()
            self.hand_wide = False
            if self.left_at is None: self.left_at = self.clock()
        return left

    def settle(self):
        """Glasses still wide after leave(): True once they have gone back, with the run's session mode on again."""
        monitor = self._monitor()
        if monitor is None: return False  # the press took the output: survive.py is on it
        if self.model == 'pro2':
            rate = pro2_2d_rate(monitor, self.narrow_refresh)
            if rate is None: return False
            current = narrow_rate(monitor, self.model)
            trace(f'settle: 2D offered, driven {current}, want {rate:g}')
            if current is None or abs(current - rate) >= 0.5:
                apply = getattr(self.client, 'apply_pro2_mode', None)
                if apply is None: return False
                try: apply(self.output, rate)
                except (OSError, ValueError, TimeoutError) as exc:
                    trace(f'settle: 2D rule failed: {exc}')
                    return False
                trace('settle: 2D mode read back')
            self._park()
            trace('settle: parked')
            self.left_at = None
            return True
        if best_wide(monitor, self.model) is not None or is_wide(monitor, self.model): return False
        self.left_at = None
        apply = getattr(self.client, 'apply_session_mode', None)
        if apply is not None:
            try:
                apply(self.output)
                self._park()
            except (OSError, ValueError, TimeoutError): pass  # survive.py's restore tries as well; a run does not end here
        return True
