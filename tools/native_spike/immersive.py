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
it) for every desktop; the 3D button joined to each desktop's level bar in the overview (Connected Tail, Cody's pick of
3d-button-mockups, option 02; free_pointer.py, glass.hpp immersive_main_rect), whose 3D segment is that desktop
(view.ViewControl.immersive_click) and whose tail is every desktop (immersive_tail_click, toggled_every); the panel, which sends
the whole setting (SET_OP, absolute, like `placement`).

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
- Sbs: enter(cancelled) blocks until the glasses are side by side, False if `cancelled()` came true first; leave(); both raise
  SbsError. **sbs.ButtonSbs is what a run with the consent's sbs_mode scope gets: enter() asks you to hold R1 on the glasses and
  waits for Hyprland to offer 3840x1200, and no byte reaches the glasses over USB (sbs.py has why).** A run without that scope
  gets NoDeviceSbs, whose enter() always fails, so 3D falls back to 2D with a toast. An Sbs may also have parked() and
  settle(): leave() left the glasses wide for a person to bring back, and the worker watches for that. restore_2d is the
  recovery hook, called first thing, as privacy.wake is: nothing a recovery can do changes the glasses' mode, so it says how.

ProcessEngine's contract with the engine program (ENGINE_NAME beside the pinned renderer binary, written by install.py only if
tools/depth_engine/setup.sh has been run, so only an installed copy ever runs and without one the engine is reported as not
installed):

    xr-depth-engine --desktops XR-1,XR-3 --shm-fd N
    stdout: one line `ready` once the model is up and the shared memory attached (READY_TIMEOUT_S)
    stdin:  one JSON line {"desktops":["XR-1"]} per change; end of input means exit at once

The shared memory (DepthMemory) is a memfd made once per run and inherited by every renderer and the engine: it is only ever in
memory, it needs no name on any filesystem, and a renderer started after a drop finds the engine still attached.

End of input is how a dead supervisor takes the engine with it, whatever killed it, and a run's systemd unit stops the whole
cgroup too. Nothing leaves the machine: the engine gets desktop names and reads its pictures from the renderer.

Wire: an `immersive` frame (spike/include/xr/ipc.hpp ImmersivePayload) on every change, and at a run's start only if a desktop
is 3D, so a renderer from before this feature never sees one while nothing is on.

The look: `preset` (PRESETS, docs/3d-comfort.md's numbers; Desktop is the default because it is the one that keeps text safe),
`strength` in percent (Soft 50, Standard 100, Enhanced 150) and `convergence`, the screen plane as a percentile of each map's
nearness. Choosing a preset also sets its convergence (with_preset).
"""
import json
import mmap
import os
from pathlib import Path
import selectors
import signal
import struct
import subprocess
import threading
import time

from .geometry import MAX_DESKTOPS
from .sbs import ButtonSbs, SbsError, scope_ok

# near, far: arcminutes either side of the plane. text: percent of the parallax left on small text. range_ms: how slowly the plane
# follows the scene. convergence: the preset's default plane. tests/native_spike/test_immersive.py holds these to
# tools/stereo_eval/presets.json.
PRESETS = {'desktop': {'near': 4, 'far': 14, 'convergence': 92, 'text': 0, 'range_ms': 3000},
           'video': {'near': 14, 'far': 36, 'convergence': 70, 'text': 100, 'range_ms': 1000},
           'games': {'near': 10, 'far': 30, 'convergence': 75, 'text': 0, 'range_ms': 800}}
DEFAULTS = {'global': False, 'desktops': {}, 'preset': 'desktop', 'strength': 100, 'convergence': PRESETS['desktop']['convergence']}
KEYS = tuple(DEFAULTS)
STRENGTHS = (50, 100, 150)  # the panel's Soft, Standard, Enhanced; anything from 0 to STRENGTH_MAX is valid
STRENGTH_MAX = 200
TOGGLE_OP, SET_OP = 'immersive_toggle', 'immersive'
# Ctrl+Alt+I: I for immersive. Free: Omarchy's own Ctrl+Alt binds are Delete, Tab and Shift+Tab, and Ctrl+Alt+3 is the
# send-window bind to XR-3. Ctrl+Alt+M must stay with the focused window (view.py). bind_check.py checks the real config.
TOGGLE_BIND = ('CTRL + ALT + I', TOGGLE_OP)
ENGINE_NAME = 'xr-depth-engine'
READY_TIMEOUT_S = 30.0  # the first start loads a model
HEARTBEAT_LIMIT_S = 3.0  # a running engine silent this long is hung, whatever its process says
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


def _percent(value, top, default):
    return value if type(value) is int and 0 <= value <= top else default


def clean(value):
    """Each field validated on its own; anything missing or invalid falls back to its default, and an exception that
    says what `global` already says is dropped. Never raises."""
    data = value if isinstance(value, dict) else {}
    every = data['global'] if type(data.get('global')) is bool else False
    raw = data.get('desktops') if isinstance(data.get('desktops'), dict) else {}
    preset = data['preset'] if isinstance(data.get('preset'), str) and data['preset'] in PRESETS else DEFAULTS['preset']
    strengths = data.get('strengths')
    overrides = ({'strengths': {n: v for n, v in strengths.items()
                               if _name_ok(n) and type(v) is int and 0 <= v <= STRENGTH_MAX}}
                 if isinstance(strengths, dict) and strengths else {})
    return {**overrides, 'global': every, 'desktops': dict(sorted((n, v) for n, v in raw.items() if _name_ok(n) and type(v) is bool and v != every)),
            'preset': preset, 'strength': _percent(data.get('strength'), STRENGTH_MAX, DEFAULTS['strength']),
            'convergence': _percent(data.get('convergence'), 100, PRESETS[preset]['convergence'])}


def is_valid(value):
    return isinstance(value, dict) and set(value) in (set(KEYS), set(KEYS) | {'strengths'}) and clean(value) == value


def validate(value):
    if not is_valid(value): raise ValueError('invalid immersive')
    return {**value, 'desktops': dict(value['desktops'])}


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
    return {**clean(setting), 'global': not on_names(setting, names), 'desktops': {}}


def toggled_every(setting, names):
    """The tail, which is lit when every desktop is 3D: lit, all flat; unlit, however many are on, all 3D."""
    return {**clean(setting), 'global': len(on_names(setting, names)) != len(names), 'desktops': {}}


def toggled_desktop(setting, name):
    """The 3D button: this desktop the other way, the rest as they are."""
    setting = clean(setting)
    return clean({**setting, 'desktops': {**setting['desktops'], name: not desktop_on(setting, name)}})


def with_preset(setting, preset):
    """Another preset, with its screen plane."""
    setting = clean(setting)
    if preset not in PRESETS: raise ValueError('invalid immersive preset')
    return {**setting, 'preset': preset, 'convergence': PRESETS[preset]['convergence']}


def wire_params(setting):
    """The `params` of an `immersive` frame."""
    setting = clean(setting)
    p = PRESETS[setting['preset']]
    overrides = ({'strengths': [setting['strengths'].get(n, setting['strength']) for n in DESKTOP_NAMES]}
                 if setting.get('strengths') else {})
    return {**overrides, 'strength': setting['strength'], 'convergence': setting['convergence'], 'near': p['near'], 'far': p['far'],
            'text': p['text'], 'range_ms': p['range_ms']}


def renderer_frame(run_id, session, request_id, flags, stereo, hover=-1, tail=False, every=False, params=None):
    """Renderer `immersive` frame (ipc.hpp ImmersivePayload): `flags`, one bool per desktop in managed-output order (their
    3D segments light; padded to MAX_DESKTOPS); `every`: all the run's desktops are 3D, so the tails light; `stereo`: the
    glasses are in SBS with depth flowing, so draw those desktops in 3D; `hover`: the desktop whose button is under the dot,
    -1 none; `tail`: the dot is on that button's tail; `params`: wire_params."""
    desktops = (list(flags) + [False] * MAX_DESKTOPS)[:MAX_DESKTOPS]
    return (json.dumps({'schema': 1, 'request_id': request_id, 'run_id': run_id, 'session': session, 'op': 'immersive',
                        'payload': {'desktops': desktops, 'global': bool(every), 'stereo': bool(stereo), 'hover': hover,
                                    'tail': bool(tail), 'params': params if params is not None else wire_params(None)}},
                       separators=(',', ':')) + '\n').encode()


class EngineError(Exception):
    """The depth engine could not start, or stopped. Its text goes in a toast."""


class NoDeviceSbs:
    """The Sbs of a run without the consent's sbs_mode scope: entering fails, and nothing happens to the glasses or the output."""
    def enter(self, cancelled=None):
        raise SbsError('3D mode is not part of this run: stop and start it again')

    def leave(self):
        pass


def make_sbs(client=None, output=None, consent=None, tell=lambda text: None):
    """The run's Sbs: the glasses' own button, if this run's consent has the scope."""
    if client is None or output is None or not isinstance(consent, dict) or not scope_ok(consent.get('sbs_mode')): return NoDeviceSbs()
    return ButtonSbs(client, output, tell)


def restore_2d(sbs=None):
    """Recovery: glasses left in side-by-side by a crash, before anything else is put back: says how to bring them back. Never raises."""
    try: (sbs if sbs is not None else make_sbs()).leave()
    except Exception: pass  # whatever an adapter does: recovery goes on


def find_engine(renderer):
    """The engine beside the pinned renderer binary, or None: only an installed copy ever runs."""
    if not renderer: return None
    path = Path(renderer).with_name(ENGINE_NAME)
    return path if path.is_file() and os.access(path, os.X_OK) else None


class DepthMemory:
    """The shared memory between the renderers and the engine (spike/include/xr/depth.hpp, spike/src/depth_link.hpp
    kDepthLinkShm): a memfd of the exact size, blank until the first renderer that needs it lays it out. No filesystem name,
    so a desktop's pictures can only ever be in memory. The supervisor holds it for the run, so it outlives every renderer and
    the engine sees one memory throughout. tests/native_spike/test_immersive.py holds this to the C++ size."""
    CHANNELS, IN_W, IN_H = MAX_DESKTOPS, 420, 266
    HEADER, CHANNEL_HEADER, SLOTS, HEARTBEAT_AT = 4096, 320, 3, 48  # DepthShmHeader::engine_ns

    @classmethod
    def size(cls):
        stride = (cls.CHANNEL_HEADER + 2 * cls.SLOTS * cls.IN_W * cls.IN_H * 4 + 4095) // 4096 * 4096
        return cls.HEADER + stride * cls.CHANNELS

    def __init__(self):
        self.fd = os.memfd_create('xr-depth', os.MFD_CLOEXEC)
        try: os.ftruncate(self.fd, self.size())
        except OSError:
            os.close(self.fd)
            raise

    def heartbeat_age(self, now_ns=None):
        """Seconds since the engine last wrote its heartbeat, None before it first has. Never raises."""
        try:
            with mmap.mmap(self.fd, 4096, prot=mmap.PROT_READ) as view:
                beat = struct.unpack_from('<Q', view, self.HEARTBEAT_AT)[0]
        except (OSError, ValueError): return None
        if not beat: return None
        return max(0, (time.monotonic_ns() if now_ns is None else now_ns) - beat) / 1e9

    def close(self):
        if self.fd >= 0: os.close(self.fd)
        self.fd = -1


class ProcessEngine:
    """The depth engine as its own process (the contract is in the module docstring). `binary` None: not installed.
    `memory`: DepthMemory, inherited by it. `spawn`: subprocess.Popen, for tests."""
    def __init__(self, binary, spawn=subprocess.Popen, ready_timeout=READY_TIMEOUT_S, stop_wait=STOP_WAIT_S, memory=None):
        self.binary, self.spawn, self.ready_timeout, self.stop_wait, self.memory = binary, spawn, ready_timeout, stop_wait, memory
        self.child = None

    def start(self, desktops, cancelled=lambda: False):
        """Blocks until the engine says `ready`. True; False if `cancelled()` came true first (it is stopped again);
        raises EngineError if it failed, leaving nothing running."""
        if self.binary is None: raise EngineError('it is not installed')
        try:
            # Its own session, so stop() reaches anything it started. stderr is dropped: nothing may block on a full pipe.
            extra, inherited = ([], {}) if self.memory is None else (['--shm-fd', str(self.memory.fd)], {'pass_fds': (self.memory.fd,)})
            self.child = self.spawn([str(self.binary), '--desktops', ','.join(desktops), *extra], stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True, **inherited)
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
        """Running, and still writing its heartbeat: a hung model call leaves a live process that makes no maps."""
        if self.child is None or self.child.poll() is not None: return False
        age = self.memory.heartbeat_age() if self.memory is not None else None
        return age is None or age < HEARTBEAT_LIMIT_S

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
    fell back to 2D, kind a TOASTS key; ('leave', why) the glasses would not leave SBS. `phase`, for the panel: 'off', 'engine'
    (loading the model), 'glasses' (waiting for the button) or 'on'."""
    def __init__(self, engine, sbs):
        self.engine, self.sbs = engine, sbs
        self._cond = threading.Condition()
        self._target, self._current = [], []  # what the loop wants; what is really up
        self._events = []
        self._stopping = self._reassert = False
        self._thread = None
        self.starts = 0  # diagnostics: engine starts
        self.phase = 'off'

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

    def switching(self):
        """The glasses are changing mode, so the output going for a moment is expected (sbs.py)."""
        check = getattr(self.sbs, 'switching', None)
        return bool(check is not None and check())

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
        self.phase = 'off'

    def _parked(self):
        """leave() left the glasses wide, for a person to bring back."""
        parked = getattr(self.sbs, 'parked', None)
        return parked is not None and parked()

    def _left_by_hand(self):
        check = getattr(self.sbs, 'left_by_hand', None)
        try: return check is not None and check()
        except Exception: return False

    def _settle(self):
        try: self.sbs.settle()
        except Exception as exc: self._emit('leave', str(exc) or type(exc).__name__)

    def _fail(self, kind, why):
        """Back to flat from any point. The target goes too: the loop latches this until a new press (ViewControl)."""
        with self._cond: was_up, self._target, self._reassert = bool(self._current), [], False
        if was_up or kind == 'sbs': self._down()
        else: self.engine.stop()
        self.phase = 'off'
        self._emit('failed', kind, why)

    def _up(self, target):
        self.starts += 1
        self.phase = 'engine'
        try:
            if not self.engine.start(target, self._cancelled):
                self.phase = 'off'
                return
        except EngineError as exc: return self._fail('engine', str(exc))
        except Exception as exc: return self._fail('engine', type(exc).__name__)  # a broken adapter is a failure, not a dead worker
        if self._cancelled():
            self.engine.stop()
            self.phase = 'off'
            return
        self.phase = 'glasses'
        try: entered = self.sbs.enter(self._cancelled)
        except Exception as exc: return self._fail('sbs', str(exc) or type(exc).__name__)
        if entered is False:  # the press was called off
            self.engine.stop()
            self.phase = 'off'
            return
        if not self.engine.alive(): return self._fail('died', 'it quit')  # it had up to a minute alone
        with self._cond: self._current = list(target)
        self.phase = 'on'
        self._emit('active', list(target))

    def _work(self):
        while True:
            with self._cond:
                if not self._stopping and not self._reassert and self._target == self._current:
                    self._cond.wait(WATCH_S if self._current or self._parked() else None)
                stopping, target, reassert = self._stopping, list(self._target), self._reassert
                current = list(self._current)
                self._reassert = False
            if stopping:
                if current: self._down()
                return
            if current and not self.engine.alive(): self._fail('died', 'it quit')
            elif current and self._left_by_hand(): self._fail('sbs', 'R1 took them back to 2D')
            elif target == current and not reassert:
                if not current and self._parked(): self._settle()
                continue
            elif not target:
                self._down()
                self._emit('off')
            elif not current: self._up(target)
            else: self._change(target, current, reassert)

    def _change(self, target, current, reassert):
        try:
            if reassert: self.sbs.enter(self._cancelled)
        except Exception as exc: return self._fail('sbs', str(exc) or type(exc).__name__)
        if target == current: return
        try: self.engine.set_desktops(target)
        except Exception as exc: return self._fail('died', str(exc) or type(exc).__name__)
        with self._cond: self._current = list(target)
        self._emit('active', list(target))
