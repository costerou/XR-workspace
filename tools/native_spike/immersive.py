"""Immersive 3D: any desktop turned into stereo 3D by an AI depth map, one desktop at a time or all at once.

This module is the one place the supervisor reads and writes the setting, and it owns what 3D switches on and off. The
depth engine makes a depth map per desktop, and the renderer draws each eye with it (the 3d-depth-engine and 3d-stereo-render
work); this module only says when.

The setting. DEFAULTS is all off. `global` is what every desktop does; `desktops` holds the exceptions, by XR-n name, so a
desktop can be 3D while the rest are flat and the other way round, and a desktop added later follows `global`. Only real
exceptions are kept (clean), so the toggle for all (toggled_all) simply clears them. Saved with the view settings
(view.DEFAULT_SETTINGS['immersive']): in the glasses model's profile for panel runs and layout.json for CLI runs. immersive(source) is the
accessor, as environment.environment is.

Where it is set: Ctrl+Alt+I (TOGGLE_BIND, a run bind in view.BINDS, so the help card, bind_check and the key bind panel see
it) for every desktop; the 3D button next to each desktop's level bar in the overview for that desktop (free_pointer.py,
glass.hpp immersive_button_rect: a press on it is view.ViewControl.immersive_click; its look is a placeholder until Cody
picks one from 3d-button-mockups); the panel, which sends the whole setting (SET_OP, absolute, like `placement`).

Three facts shape what happens next:

- The glasses are in side-by-side 3D as a whole, not per desktop. So they go into SBS while ANY desktop is 3D, and a flat
  desktop then simply draws the same picture for both eyes.
- A depth model keeps the GPU busy, so the engine process exists only while a desktop is 3D. Nothing about it runs
  otherwise: no thread, no process (ImmersiveRun starts its worker at the first request).
- Going up takes seconds (loading a model) and the device command can block, while the supervisor's loop is a watchdog that
  must never wait. So ImmersiveRun's worker does all of it off the loop; the loop only says what it wants (request) and picks up
  what happened (take_events, from ViewControl.poll).

The order is what makes it graceful. On: engine first, then the glasses, then the renderer, so an engine that will not start
never blinks the display. Off: the renderer first (at once, on the loop), then the glasses, then the engine. If the engine
or the glasses fail, at the start or later, whatever came up goes down again and a toast says why (TOASTS). ViewControl then
holds the run in 2D (`immersive_failed`) with the setting left as saved: the next press retries, and so does the next XR
start, once each. A VR video replaces the desktops, so 3D of the desktops stands down while one plays and comes back after.

The two interfaces, so the rest never knows what is behind them:

- Engine: start(desktops, cancelled) blocks until it is ready or raises EngineError (`cancelled()` true: stop waiting and
  return False); set_desktops(desktops); alive(); stop(), which never raises. ProcessEngine is the separate-process kind.
- Sbs: enter() and leave() raise SbsError. **NoDeviceSbs is what a real run gets today: its enter() always fails, so 3D
  falls back to 2D with a toast and no command ever reaches the glasses.** There is no command to send: the driver work
  (spike/driver/docs/display-control.md on drv-display-control, protocol.md on drv-protocol-research) found no public id for
  it on this device, bans guessing one, and recommends no display commands over USB at all. VITURE's SDK call for it fails
  while the IMU streams, and its way back to 2D is documented as 1920x1080@60, off our 120 Hz. So the likely adapter may be the
  glasses' own 3D button: enter() asks you to press it and waits for Hyprland to report the wide mode, with no USB at all.
  That is untested. Whatever it is owns the output's mode (3840x1200; docs/vr-video.md "Stereo output": the session mode
  and the run's consent cover 1920x1200@120 only, so this needs its own consent scope and a physical test), and goes in
  make_sbs below. restore_2d is its recovery hook: a crash must not leave a doubled picture, so recovery calls it first
  thing, as it does privacy.wake.

ProcessEngine's contract with the engine program (ENGINE_NAME beside the pinned renderer binary, so only an installed
copy ever runs: install.py does not ship one yet, and until it does the engine is reported as not installed):

    xr-depth-engine --desktops XR-1,XR-3
    stdout: one line `ready` once depth maps are flowing (READY_TIMEOUT_S)
    stdin:  one JSON line {"desktops":["XR-1"]} per change; end of input means exit at once

End of input is how a dead supervisor takes the engine with it, whatever killed it, and a run's systemd unit stops the whole
cgroup too. Nothing leaves the machine: the engine gets desktop names and reads its pictures from the renderer.

Wire: an `immersive` frame (spike/include/xr/ipc.hpp ImmersivePayload) on every change, and at a run's start only if a desktop
is 3D, so a renderer from before this feature never sees one while nothing is on.
"""
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import threading
import time

from .geometry import MAX_DESKTOPS

DEFAULTS = {'global': False, 'desktops': {}}
KEYS = tuple(DEFAULTS)
TOGGLE_OP, SET_OP = 'immersive_toggle', 'immersive'
# Ctrl+Alt+I: I for immersive. Free: Omarchy's own Ctrl+Alt binds are Delete, Tab and Shift+Tab, and Ctrl+Alt+3 is the
# send-window bind to XR-3. Ctrl+Alt+M must stay with the focused window (view.py). bind_check.py checks the real config.
TOGGLE_BIND = ('CTRL + ALT + I', TOGGLE_OP)
ENGINE_NAME = 'xr-depth-engine'
READY_TIMEOUT_S = 20.0  # the first start loads a model
STOP_WAIT_S = 2.0       # after end of input, then SIGTERM, then as long again, then SIGKILL
WATCH_S = 0.25          # how often the worker looks at a running engine
CLOSE_WAIT_S = 3.0      # a run's end waits this long for the worker
TOASTS = {'engine': '3D is off: the depth engine could not start ({why}). Back to 2D.',
          'died': '3D is off: the depth engine stopped ({why}). Back to 2D.',
          'sbs': '3D is off: the glasses could not switch to 3D mode ({why}). Back to 2D.',
          'leave': 'The glasses did not leave 3D mode ({why}). Unplug them or use their button if the picture looks doubled.',
          'video': 'Saved: 3D applies to the desktops, once the VR video closes.'}
DESKTOP_NAMES = tuple(f'XR-{i + 1}' for i in range(MAX_DESKTOPS))


def _name_ok(name):
    return isinstance(name, str) and name in DESKTOP_NAMES


def clean(value):
    """Each field validated on its own; anything missing or invalid falls back to its default, and an exception that
    says what `global` already says is dropped. Never raises."""
    data = value if isinstance(value, dict) else {}
    every = data['global'] if type(data.get('global')) is bool else False
    raw = data.get('desktops') if isinstance(data.get('desktops'), dict) else {}
    return {'global': every, 'desktops': dict(sorted((n, v) for n, v in raw.items() if _name_ok(n) and type(v) is bool and v != every))}


def is_valid(value):
    return isinstance(value, dict) and set(value) == set(KEYS) and clean(value) == value


def validate(value):
    if not is_valid(value): raise ValueError('invalid immersive')
    return {'global': value['global'], 'desktops': dict(value['desktops'])}


def immersive(source):
    """The accessor: the cleaned setting from a settings dict (its 'immersive' entry) or an object with an `immersive`
    attribute (view.ViewState). Missing or invalid: all off."""
    return clean(source.get('immersive') if isinstance(source, dict) else getattr(source, 'immersive', None))


def desktop_on(setting, name):
    setting = clean(setting)
    return setting['desktops'].get(name, setting['global'])


def on_names(setting, names):
    """Those of `names` that are 3D, in order."""
    return [n for n in names if desktop_on(setting, n)]


def toggled_all(setting, names):
    """Ctrl+Alt+I: anything 3D goes flat, all of it; nothing 3D, everything goes 3D. The exceptions go either way."""
    return {'global': not on_names(setting, names), 'desktops': {}}


def toggled_desktop(setting, name):
    """The 3D button: this desktop the other way, the rest as they are."""
    setting = clean(setting)
    return clean({**setting, 'desktops': {**setting['desktops'], name: not desktop_on(setting, name)}})


def renderer_frame(run_id, session, request_id, flags, stereo, hover=-1):
    """Renderer `immersive` frame (ipc.hpp ImmersivePayload): `flags`, one bool per desktop in managed-output order (their
    buttons light; padded to MAX_DESKTOPS); `stereo`: the glasses are in SBS with depth flowing, so draw those desktops in
    3D; `hover`: the desktop whose button is under the dot, -1 none."""
    desktops = (list(flags) + [False] * MAX_DESKTOPS)[:MAX_DESKTOPS]
    return (json.dumps({'schema': 1, 'request_id': request_id, 'run_id': run_id, 'session': session, 'op': 'immersive',
                        'payload': {'desktops': desktops, 'stereo': bool(stereo), 'hover': hover}}, separators=(',', ':')) + '\n').encode()


class EngineError(Exception):
    """The depth engine could not start, or stopped. Its text goes in a toast."""


class SbsError(Exception):
    """The glasses could not change mode. Its text goes in a toast."""


class NoDeviceSbs:
    """Today's only Sbs (module docstring): the device command is not in this repo yet, so entering fails and nothing is sent."""
    def enter(self):
        raise SbsError('the 3D mode command is not available yet')

    def leave(self):
        pass


def make_sbs():
    """The run's Sbs. One line to change when the driver's command lands."""
    return NoDeviceSbs()


def restore_2d(sbs=None):
    """Recovery: glasses left in SBS by a crash go back to 2D, before anything else is put back. Never raises."""
    try: (sbs if sbs is not None else make_sbs()).leave()
    except (SbsError, OSError, ValueError): pass


def find_engine(renderer):
    """The engine beside the pinned renderer binary, or None: only an installed copy ever runs."""
    if not renderer: return None
    path = Path(renderer).with_name(ENGINE_NAME)
    return path if path.is_file() and os.access(path, os.X_OK) else None


class ProcessEngine:
    """The depth engine as its own process (the contract is in the module docstring). `binary` None: not installed.
    `spawn`: subprocess.Popen, for tests."""
    def __init__(self, binary, spawn=subprocess.Popen, ready_timeout=READY_TIMEOUT_S, stop_wait=STOP_WAIT_S):
        self.binary, self.spawn, self.ready_timeout, self.stop_wait = binary, spawn, ready_timeout, stop_wait
        self.child = None

    def start(self, desktops, cancelled=lambda: False):
        """Blocks until the engine says `ready`. True; False if `cancelled()` came true first (it is stopped again);
        raises EngineError if it failed, leaving nothing running."""
        if self.binary is None: raise EngineError('it is not installed')
        try:
            # Its own session, so stop() reaches anything it started. stderr is dropped: nothing may block on a full pipe.
            self.child = self.spawn([str(self.binary), '--desktops', ','.join(desktops)], stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True)
        except (OSError, ValueError) as exc:
            raise EngineError(f'it would not run: {type(exc).__name__}') from exc
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(self.child.stdout, selectors.EVENT_READ)
                deadline, seen = time.monotonic() + self.ready_timeout, b''
                while True:
                    if cancelled():
                        self.stop()
                        return False
                    left = deadline - time.monotonic()
                    if left <= 0: raise EngineError('it took too long to load')
                    if not selector.select(min(left, 0.1)): continue
                    chunk = os.read(self.child.stdout.fileno(), 4096)
                    if not chunk: raise EngineError('it quit while starting')
                    seen = (seen + chunk)[-4096:]
                    if any(line.strip() == b'ready' for line in seen.split(b'\n')[:-1]): return True
        except EngineError:
            self.stop()
            raise
        except (OSError, ValueError) as exc:
            self.stop()
            raise EngineError(f'it could not be read: {type(exc).__name__}') from exc

    def set_desktops(self, desktops):
        try:
            self.child.stdin.write(json.dumps({'desktops': list(desktops)}).encode() + b'\n')
            self.child.stdin.flush()
        except (OSError, ValueError, AttributeError) as exc:
            raise EngineError('it stopped listening') from exc

    def alive(self):
        return self.child is not None and self.child.poll() is None

    def stop(self):
        """End of input, then SIGTERM, then SIGKILL, each waited for. Never raises; safe to call twice."""
        child, self.child = self.child, None
        if child is None: return
        for stream in (child.stdin, child.stdout):
            try:
                if stream is not None: stream.close()
            except (OSError, ValueError): pass
        for sig in (None, signal.SIGTERM, signal.SIGKILL):
            if sig is not None:
                try: os.killpg(child.pid, sig)
                except (OSError, ValueError): pass
            try:
                child.wait(self.stop_wait)
                return
            except subprocess.TimeoutExpired: pass


class ImmersiveRun:
    """A run's 3D: its worker takes the engine and the glasses to what the loop asked for (module docstring). request()
    and take_events() never block. Events: ('active', names) up and drawing; ('off',) all down; ('failed', kind, why)
    fell back to 2D, kind a TOASTS key; ('leave', why) the glasses would not leave SBS."""
    def __init__(self, engine, sbs):
        self.engine, self.sbs = engine, sbs
        self._cond = threading.Condition()
        self._target, self._current = [], []  # what the loop wants; what is really up
        self._events = []
        self._stopping = self._reassert = False
        self._thread = None
        self.starts = 0  # diagnostics: engine starts

    def request(self, desktops):
        """Bring up these desktops ([]: all of it down). The worker starts here, so a run that never uses 3D never has one."""
        desktops = list(desktops)
        with self._cond:
            if self._stopping or desktops == self._target: return
            self._target = desktops
            if desktops and self._thread is None:
                self._thread = threading.Thread(target=self._work, daemon=True, name='xr-immersive')
                self._thread.start()
            self._cond.notify_all()

    def reassert(self):
        """A config reload reset the output's mode (managed.after_reload): if the glasses are in SBS, say so again."""
        with self._cond:
            self._reassert = bool(self._current)
            self._cond.notify_all()

    def take_events(self):
        with self._cond:
            events, self._events = self._events, []
        return events

    def close(self, timeout=CLOSE_WAIT_S):
        """Run end: everything down, before recovery. Bounded: an adapter that hangs cannot hold recovery up, so the
        engine is then stopped from here and the worker abandoned (a daemon)."""
        with self._cond:
            self._stopping = True
            self._target = []
            self._cond.notify_all()
            thread = self._thread
        if thread is None: return
        thread.join(timeout)
        if thread.is_alive(): self.engine.stop()

    def _emit(self, *event):
        with self._cond: self._events.append(event)

    def _cancelled(self):
        with self._cond: return self._stopping or not self._target

    def _down(self):
        """The glasses, then the engine. Never raises."""
        try: self.sbs.leave()
        except Exception as exc: self._emit('leave', str(exc) or type(exc).__name__)  # any adapter error: the engine must still stop
        self.engine.stop()
        with self._cond: self._current = []

    def _fail(self, kind, why):
        """Back to flat from any point. The target goes too: the loop latches this until a new press (ViewControl)."""
        with self._cond: was_up, self._target, self._reassert = bool(self._current), [], False
        if was_up or kind == 'sbs': self._down()
        else: self.engine.stop()
        self._emit('failed', kind, why)

    def _up(self, target):
        self.starts += 1
        try:
            if not self.engine.start(target, self._cancelled): return
        except EngineError as exc: return self._fail('engine', str(exc))
        except Exception as exc: return self._fail('engine', type(exc).__name__)  # a broken adapter is a failure, not a dead worker
        if self._cancelled():
            self.engine.stop()
            return
        try: self.sbs.enter()
        except Exception as exc: return self._fail('sbs', str(exc) or type(exc).__name__)
        with self._cond: self._current = list(target)
        self._emit('active', list(target))

    def _work(self):
        while True:
            with self._cond:
                if not self._stopping and not self._reassert and self._target == self._current:
                    self._cond.wait(WATCH_S if self._current else None)
                stopping, target, reassert = self._stopping, list(self._target), self._reassert
                current = list(self._current)
                self._reassert = False
            if stopping:
                if current: self._down()
                return
            if current and not self.engine.alive(): self._fail('died', 'it quit')
            elif target == current and not reassert: continue
            elif not target:
                self._down()
                self._emit('off')
            elif not current: self._up(target)
            else: self._change(target, current, reassert)

    def _change(self, target, current, reassert):
        try:
            if reassert: self.sbs.enter()
        except Exception as exc: return self._fail('sbs', str(exc) or type(exc).__name__)
        if target == current: return
        try: self.engine.set_desktops(target)
        except Exception as exc: return self._fail('died', str(exc) or type(exc).__name__)
        with self._cond: self._current = list(target)
        self._emit('active', list(target))
