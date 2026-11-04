"""Live desktop changes from a glass pill in the glasses: Ultrawide, Add, Remove, Resolution and Scale.

The open placement panel (an expanded glass pill, glass.hpp) has a row of three buttons under its sliders, also on keys
while it is open (view.MENU_BINDS: U, N, Delete):

    Ultrawide   that desktop toggles between 16:9 and 21:9 at its own height (geometry.toggle_ultrawide)
    Add new     a new desktop, the size of the one whose pill it is (standard 16:9 if that one is ultrawide), named
                with the lowest free number XR-1..XR-MAX_DESKTOPS
    Remove      that desktop goes; its windows move to the run's physical fallback monitor (exactly what Stop does to
                them). The first press only arms the button ("Remove?"); a second one within REMOVE_ARM_S removes.

Above that row, the panel's Scale dropdown (SCALE): that desktop's Hyprland output scale becomes the picked step of
geometry.SCALES (only the ones that divide its resolution exactly can be picked: geometry.valid_scales). Its pixel size
stays; its logical width changes, so the desktops to its right slide along the row (a reshape like Ultrawide's). The
profile takes the new scale with the desktops, so the next session starts with it.

Above Scale, the Resolution dropdown (RESOLUTION): that desktop's pixel size becomes the picked one of
geometry.RESOLUTIONS (16:9, 16:10 and ultrawide sizes), live and in place, by the same reshape: the broker swaps the
headless output's monitor rule, which re-sizes only that output (no other output is touched, nothing is modeset, the run
goes on), its workspaces and windows stay on it, and the renderer keeps showing its last frame until one at the new
size arrives. Its scale stays if it divides the new size, else it becomes the nearest step that does
(geometry.with_resolution). Its placement in the glasses stays too: the overview gives every desktop the same height,
so only a change of shape makes it wider or narrower where it is (the renderer eases that, and its neighbours' shift,
view.hpp SizeEase). The profile takes the new size with the desktops.

Whatever makes a desktop smaller (a smaller Resolution, Ultrawide off, a larger Scale) also pulls its floating windows
back inside it (fit_windows.py), on the same worker once the broker steps are done.

Every desktop of a run is a managed output the ownership broker made (spike/ownership/plugin.cpp), side by side in
Hyprland's layout in name order, starting where the run's first desktop started. A change is a plan (plan(), pure)
of broker steps in a safe order: remove first, then the outputs that move left or shrink (left to right), then the
ones that move right or grow (right to left), then creations, so no two outputs ever share layout space.

  * Creation is write-ahead journaled like the run's first outputs (ownership.create_live): a crash at any point leaves
    a journal recovery removes.
  * Reshape (ownership.reshape) swaps the broker's monitor rule and reads the output back; an output that does not read
    back exactly is untrusted, and the run's guard ends the run (recovery removes it like any other).
  * Removal is the recovery transaction itself (evacuate_remove: windows to the fallback, reachability proof, output
    destroyed), then the journal record is done.

The work runs on a worker thread, never on the supervisor loop (a broker call may take up to its 2 s deadline, and the
renderer's heartbeat is checked there). The loop (view.ViewControl) pauses the run's guard first, hands the renderer
its new desktop set (`desktops` frame: spike/include/xr/ipc.hpp DesktopsPayload; a removed desktop's capture stops
before its output goes), and once the worker is done restarts the guard on the outputs as they now are. The profile
takes the new desktops and placements, so the next session starts with them.

Ctrl+Alt+R (view.RESET_OP) is one more plan, RESET: every desktop back to the default three, 1920x1080 at 100% and flat
(profiles.default_setup). The three lowest-numbered desktops stay and are reshaped, and missing ones are added with the
lowest free numbers; one plan, the same broker steps in the same safe order. A fourth desktop goes, and its windows go to
the last desktop that stays (normally XR-3), not to the fallback as Remove's do: just before the removal the worker calls
`move_windows` (adopt.adopt: whole workspaces, each still its own workspace there, Omarchy's block renumbering included).
Anything that could not move is still evacuated to the fallback by the removal itself, the safety net of every removal.

Before any removal the worker tells the window memory (`leaving`, window_memory.py), which keeps that desktop's windows
remembered for it: if the same desktop is added again in the run, they go straight back.

Nothing here widens what the session may do: the outputs are the run's own managed outputs (the consent's capture
scope), created and removed only through the broker, the windows a removal moves go where Stop would move them, and the
windows a shrink pulls in stay on that output.
"""
from dataclasses import dataclass, field
import threading
import time
import uuid

from .fit_windows import plan as fit_plan
from .geometry import (DEFAULT_DESKTOP, DEFAULT_PLACEMENT, MAX_DESKTOPS, is_ultrawide, logical_height, logical_width,
                       toggle_ultrawide, validate_desktop, with_resolution, with_scale)

ACTIONS = ('ultrawide', 'add', 'remove')  # the panel's desktop row (its buttons, free_pointer.PANEL_ACTIONS)
SCALE = 'scale'  # the panel's Scale dropdown: plan(..., SCALE, index, scale=s)
RESOLUTION = 'resolution'  # the panel's Resolution dropdown: plan(..., RESOLUTION, index, size=(width, height))
RESET = 'reset'  # Ctrl+Alt+R: every desktop back to the default (module docstring): plan(..., RESET, 0)
RESET_COUNT = 3  # profiles.DEFAULT_SETUP_DESKTOPS
REMOVE_ARM_S = 4.0  # the Remove button's second press must come this soon after the first
REFRESH_MILLIHZ = 120000


def number(name):
    """'XR-3' -> 3."""
    return int(name.split('-', 1)[1])


def desktop_of(spec):
    return validate_desktop({'width': spec['width'], 'height': spec['height'], 'scale': spec['scale']})


@dataclass
class Plan:
    """What one action does. `specs`: the run's outputs afterwards, in name order (each a broker create spec). Steps, in
    order: `remove` (spec or None), `reshape` (specs, already ordered), `create` (specs). `placements`: one per desktop
    of `specs`. `focus`: the index in `specs` the panel should be on afterwards (None: collapse it)."""
    action: str
    specs: list
    remove: dict | None = None
    reshape: list = field(default_factory=list)
    create: list = field(default_factory=list)
    placements: list = field(default_factory=list)
    focus: int | None = None
    resized: list = field(default_factory=list)  # names whose pixel size changes (their capture restarts)
    windows_to: str | None = None  # RESET: where the removed desktop's windows go first (DesktopManager.move_windows)
    shrunk: list = field(default_factory=list)   # names whose logical size gets smaller (fit_windows.py pulls windows in)

    def steps(self):
        out = [('remove', self.remove)] if self.remove is not None else []
        return out + [('reshape', s) for s in self.reshape] + [('create', s) for s in self.create]


def lay_out(desktops, origin):
    """[(name, desktop)] in any order -> create specs (no ids) side by side in name order from `origin` (x, y)."""
    x, y = origin
    out = []
    for name, d in sorted(desktops, key=lambda nd: number(nd[0])):
        out.append({'name': name, 'index': number(name) - 1, 'width': d['width'], 'height': d['height'],
                    'refresh_millihz': REFRESH_MILLIHZ, 'scale': d['scale'], 'x': x, 'y': y})
        x += logical_width(d)
        if x > 2147483647: raise ValueError('layout overflow')
    return out


def plan(specs, placements, action, index, origin=None, held=(), scale=None, size=None):
    """The broker steps for `action` on desktop `index` of `specs` (the run's live outputs in name order, with ids).
    `placements`: one per spec. `held`: names Add must not take besides those of `specs` (DesktopManager.held: a failed
    create's journal record holds its name until recovery). `scale`: the new output scale for SCALE (geometry.with_scale).
    `size`: the new (width, height) for RESOLUTION (geometry.with_resolution). ValueError if the action cannot be done
    (Add at MAX_DESKTOPS or with no free name, Remove of the last one, a scale that does not divide the desktop exactly,
    a size the broker would refuse). RESET ignores `index` beyond checking it."""
    if action not in ACTIONS + (SCALE, RESOLUTION, RESET): raise ValueError('unknown desktop action')
    if not specs or type(index) is not int or not 0 <= index < len(specs): raise ValueError('no such desktop')
    if len(placements) != len(specs): raise ValueError('one placement per desktop required')
    specs = sorted(specs, key=lambda s: number(s['name']))
    origin = origin or (specs[0]['x'], specs[0]['y'])
    by_name = {s['name']: s for s in specs}
    placed = {s['name']: dict(p) for s, p in zip(specs, placements)}
    desktops = {s['name']: desktop_of(s) for s in specs}
    target = specs[index]['name']
    removed = windows_to = None
    if action == 'ultrawide':
        desktops[target] = toggle_ultrawide(desktops[target])
    elif action == SCALE:
        desktops[target] = with_scale(desktops[target], scale)
    elif action == RESOLUTION:
        if not isinstance(size, (tuple, list)) or len(size) != 2: raise ValueError('a resolution is a width and a height')
        desktops[target] = with_resolution(desktops[target], *size)
    elif action == RESET:
        names = sorted(desktops, key=number)
        extra = names[RESET_COUNT:]
        if len(extra) > 1: raise ValueError(f'a reset removes one desktop at most ({len(extra)} over {RESET_COUNT})')
        if extra:
            removed, windows_to = by_name[extra[0]], names[RESET_COUNT - 1]
            del desktops[extra[0]], placed[extra[0]]
        while len(desktops) < RESET_COUNT:
            free = next((n for n in range(1, MAX_DESKTOPS + 1) if f'XR-{n}' not in desktops and f'XR-{n}' not in held), None)
            if free is None: raise ValueError('no free desktop name until the session restarts')
            desktops[f'XR-{free}'] = None
        for name in desktops: desktops[name], placed[name] = dict(DEFAULT_DESKTOP), dict(DEFAULT_PLACEMENT)
    elif action == 'add':
        if len(specs) >= MAX_DESKTOPS: raise ValueError(f'at most {MAX_DESKTOPS} desktops')
        free = next((n for n in range(1, MAX_DESKTOPS + 1) if f'XR-{n}' not in desktops and f'XR-{n}' not in held), None)
        if free is None: raise ValueError('no free desktop name until the session restarts')
        model = desktops[target]
        target = f'XR-{free}'
        desktops[target] = toggle_ultrawide(model) if is_ultrawide(model) else model
        placed[target] = dict(DEFAULT_PLACEMENT)
    else:
        if len(specs) <= 1: raise ValueError('a session keeps at least one desktop')
        removed = by_name[target]
        del desktops[target], placed[target]
    laid = lay_out(list(desktops.items()), origin)
    after, moves, creates, resized, shrunk = [], [], [], [], []
    for s in laid:
        old = by_name.get(s['name'])
        if old is None:
            s = {'id': str(uuid.uuid4()), **s}
            creates.append(s)
        else:
            s = {'id': old['id'], **s}
            if any(s[k] != old[k] for k in ('width', 'height', 'scale', 'x', 'y')): moves.append((old, s))
            if any(s[k] != old[k] for k in ('width', 'height', 'scale')): resized.append(s['name'])
            before, now = desktop_of(old), desktop_of(s)
            if logical_width(now) < logical_width(before) or logical_height(now) < logical_height(before): shrunk.append(s['name'])
        after.append(s)
    # Never two outputs on the same layout space: what moves left or shrinks goes first, left to right; then what moves
    # right or grows, right to left.
    def right(s): return s['x'] + logical_width(desktop_of(s))
    first = sorted([(o, n) for o, n in moves if n['x'] <= o['x'] and right(n) <= right(o)], key=lambda m: m[1]['x'])
    rest = sorted([(o, n) for o, n in moves if (o, n) not in first], key=lambda m: -m[1]['x'])
    names = [s['name'] for s in after]
    focus = names.index(target) if action not in ('remove', RESET) else None
    return Plan(action, after, removed, [n for _, n in first + rest], creates, [placed[n] for n in names], focus, resized,
                windows_to, shrunk)


class DesktopManager:
    """The run's managed outputs and the worker that changes them (module docstring).

    `broker`: ownership.OwnershipBrokerClient of the run; `journal`: its journal; `specs`: the outputs the run created
    (managed.plan_outputs). `guard_factory(specs)`: the run's guard for a set of outputs (managed.ManagedGuard).
    `store`: optional profiles.PlacementStore (save_desktops). `windows`: optional Hyprland client (query, fit_windows), to
    pull a shrunk desktop's floating windows back inside it. `move_windows(source, target)`: optional, moves one output's
    windows to another before a RESET removal (managed.view_controls; off the supervisor loop, on the worker). `leaving(name,
    target)`: optional, told before any removal, on the worker, so the window memory can keep that desktop's windows.
    `threads=False` runs the steps inline (tests)."""
    def __init__(self, broker, journal, specs, *, guard_factory=None, store=None, threads=True, windows=None):
        self.broker, self.journal, self.guard_factory, self.store, self.threads = broker, journal, guard_factory, store, threads
        self.windows = windows
        self.move_windows = None
        self.leaving = None  # optional (name, target or None) -> None: a desktop is about to go (window_memory.py), on the worker
        self.specs = sorted((dict(s) for s in specs), key=lambda s: number(s['name']))
        if not self.specs: raise ValueError('a run has at least one desktop')
        self.origin = (self.specs[0]['x'], self.specs[0]['y'])
        self._lock = threading.Lock()
        self._worker = None
        self._result = None
        self._stop = False
        self.changes = self.failures = self.fitted = self.fit_failures = 0  # diagnostics

    # what the run's other parts read
    def names(self):
        return [s['name'] for s in self.specs]

    def held(self):
        """Every name the run's journal still holds (journal.live_outputs: its outputs, and a failed create's record until
        recovery), which ownership.create_live refuses to create again. Add picks among the others."""
        try: return {o['name'] for o in self.journal.live_outputs()}
        except OSError as exc: raise ValueError(f'the run journal could not be read: {exc}') from exc

    def desktops(self):
        return [desktop_of(s) for s in self.specs]

    def wide(self):
        return [is_ultrawide(desktop_of(s)) for s in self.specs]

    def busy(self):
        return self._worker is not None and self._worker.is_alive() or self._result is not None

    def guard(self):
        return self.guard_factory([dict(s) for s in self.specs]) if self.guard_factory is not None else None

    # the change itself
    def start(self, change):
        """Run `change` (a Plan) on the worker. False if one is still running or not yet collected."""
        with self._lock:
            if self._stop or self.busy(): return False
            if self.threads:
                self._worker = threading.Thread(target=self._run, args=(change,), daemon=True)
                self._worker.start()
                return True
        self._run(change)  # inline (tests): the loop thread is the only caller, so nothing starts meanwhile
        return True

    def _run(self, change):
        error = None
        done = []
        try:
            for kind, spec in change.steps():
                if self._stop: raise ValueError('the session is ending')
                if kind == 'remove':
                    if self.leaving is not None: self.leaving(spec['name'], change.windows_to)  # never raises: its memory is kept
                    if change.windows_to is not None and self.move_windows is not None:
                        self.move_windows(spec['name'], change.windows_to)  # best effort: the removal evacuates what stays
                    if not self.broker.remove_live(spec['id'], spec['name'], self.journal):
                        raise ValueError(f"{spec['name']} could not be removed (a pinned window, or a window that would "
                                         f"not move); it stays")
                    self.specs = [s for s in self.specs if s['id'] != spec['id']]
                elif kind == 'reshape':
                    # Recorded before the call: if it fails, the output may hold either geometry and is untrusted either
                    # way; the guard checks the new one (and ends the run if it does not hold).
                    self.specs = sorted([s for s in self.specs if s['id'] != spec['id']] + [dict(spec)], key=lambda s: number(s['name']))
                    self.broker.reshape(spec)
                else:
                    self.broker.create_live(spec, self.journal)
                    self.specs = sorted(self.specs + [dict(spec)], key=lambda s: number(s['name']))
                done.append(kind)
            self._fit_windows(change)
        except Exception as exc:  # broker refusals, OwnershipBlocked, transport errors: reported, never fatal here
            error = str(exc) or type(exc).__name__
        with self._lock:
            self._result = (change, error, done)

    def _fit_windows(self, change):
        """The change went through: floating windows that would stick out of a desktop it shrank go back inside it
        (fit_windows.py). Best effort: a window left sticking out is no reason to fail a change that worked."""
        if self.windows is None or self._stop or not change.shrunk: return
        try:
            clients, workspaces, monitors = (self.windows.query(kind) for kind in ('clients', 'workspaces', 'monitors'))
            by_name = {s['name']: s for s in change.specs}
            moves = [m for name in change.shrunk if name in by_name for m in fit_plan(clients, workspaces, monitors, by_name[name])]
            if moves and not self._stop: self.windows.fit_windows(moves)
            self.fitted += len(moves)
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            self.fit_failures += 1

    def take_result(self):
        """(plan, error or None, steps done) once the worker finished, else None. Collected once."""
        with self._lock:
            if self._result is None: return None
            if self._worker is not None and self._worker.is_alive(): return None
            result, self._result, self._worker = self._result, None, None
        if result[1] is None: self.changes += 1
        else: self.failures += 1
        return result

    def save(self, placements, rolls=None):
        """The profile takes the run's desktops now, their placements and (if known) their levelling rolls (best
        effort)."""
        if self.store is None or len(placements) != len(self.specs): return
        if rolls is not None and len(rolls) != len(self.specs): rolls = None
        args = (self.desktops(), [dict(p) for p in placements]) + (() if rolls is None else (list(rolls),))
        try: self.store.save_desktops(*args)
        except (OSError, ValueError, AttributeError): pass

    def close(self, timeout=None):
        """Run end, before recovery: no new change starts, the step in flight finishes (each is bounded by the broker's
        deadline), and no later one runs."""
        with self._lock: self._stop = True
        worker = self._worker
        if worker is not None: worker.join(timeout)


def wait_idle(manager, timeout=5.0, poll=0.01):
    """Tests: wait for the worker to finish (not collected)."""
    deadline = time.monotonic() + timeout
    while manager._worker is not None and manager._worker.is_alive() and time.monotonic() < deadline: time.sleep(poll)
