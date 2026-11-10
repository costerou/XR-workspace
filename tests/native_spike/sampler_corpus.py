"""Phase 6 corpus for the Rust rebuild (docs/rust-rebuild-plan.md): the free pointer's sampler, step by step.

The sampler (free_pointer.PointerSampler, input_lock.LockedSampler, unified_cursor.UnifiedSampler, with
unified_cursor.EdgePush, overview_cursor.OverviewCursor and columns.ColumnScroll inside) is a loop on its own thread
that reads the overview pointer and makes every compositor call the pointer needs. Its unit tests drive it from a real
thread (start / stop), so they cannot be replayed one iteration at a time. This module drives the shipped samplers
through scripted runs instead, one `_run` iteration per step, on a fake clock (time.monotonic patched), and writes them
to rust/corpus/pointer/sampler.json.

A case is one sampler: its class (`variant`), names, bind tuple, period and whether it reads cursor shapes. Each step:
  * input: `t` (the clock for the whole step), `p` (overview pointer fields set before it: what the pointer answers),
    `c` (fake client fields set before it: the cursor's position, a malformed reply, the calls that fail, the layout
    replies; the Rust replay takes each call's outcome from the recording instead), and `op`: `step` (one `_run`
    iteration, ended by its wait), `sample_restore` (with `outputs`), `set_names` (with `names`) or `stop`;
  * recorded: `events`, every call the sampler made into the pointer (only those with arguments or effects: the
    pointer's plain reads answer from `p`), the client and the shape reader, in order, each with its result; `wait`, the
    seconds the iteration waited; `ret`, sample_restore's answer; `state`, the sampler's attributes after it.
The pointer is a scripted fake (ScriptPointer): its answers are `p`, and pan_plan builds a ScriptPlan from its
arguments. The client is a fake that keeps the cursor where the calls put it.

test_sampler_corpus.py runs every stored case through the current Python again and fails when anything differs, so the
file stays true to the shipped code; the Rust port replays it in
rust/crates/xr-workspace/src/supervisor/sampler_corpus_tests.rs.

Regenerate (after a deliberate change to the samplers) with:

    TMPDIR=/tmp /usr/bin/python3 tests/native_spike/sampler_corpus.py --write
"""
import copy
import json
import math
from pathlib import Path
import sys
import threading
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
for p in (str(ROOT), str(HERE)):
    if p not in sys.path: sys.path.insert(0, p)

from tools.native_spike import free_pointer as fp, input_lock as il, unified_cursor as uc  # noqa: E402
from pointer_trace_corpus import enc, dec  # noqa: E402

CORPUS_FILE = ROOT / 'rust' / 'corpus' / 'pointer' / 'sampler.json'
CLASSES = {'plain': fp.PointerSampler, 'locked': il.LockedSampler, 'unified': uc.UnifiedSampler}
ERR = {'$err': True}
FAILS = (OSError, ValueError, TypeError, KeyError)


class Mismatch(AssertionError):
    pass


class Unscripted(RuntimeError):
    pass


# The pointer's answers before any `p`.
POINTER = {'overview': False, 'restore_sampling': False, 'want': [0, 'away', None], 'binds_dropped': False,
           'mode': 'away', 'handles_on': False, 'sync': 0, 'shape_gated': False, 'shape_pushed': False,
           'shape_serial': 0, 'reloads': 0, 'selected': 1, 'can_switch': False, 'can_scroll': False,
           'neighbours': {}, 'plan_px': None}


class ScriptPlan:
    """An edge_pan.Plan as EdgePush reads it: built from pan_plan's arguments (exit and entry its fraction along)."""
    def __init__(self, home, target, side, along, total_px):
        self.home, self.target, self.side, self.exit, self.entry, self.total_px = home, target, side, along, along, total_px

    def frame(self, travel):
        return {'target': self.target, 'travel': travel}

    def snap(self):
        return {'home': self.home, 'target': self.target, 'side': self.side, 'exit': self.exit, 'entry': self.entry,
                'total_px': self.total_px}


class _State:
    def __init__(self, pointer): self._p = pointer
    @property
    def selected(self): return self._p.s['selected']


class _Control:
    def __init__(self, pointer): self._p, self.state = pointer, _State(pointer)
    @property
    def reloads(self): return self._p.s['reloads']


class ScriptPointer:
    """The overview pointer, answering from `s`; every call with arguments or an effect is logged."""
    def __init__(self, log):
        self.log, self.s = log, copy.deepcopy(POINTER)
        self.control = _Control(self)

    def _ev(self, call, *args, out=None, logged_out=False):
        e = {'call': call, 'args': list(args)}
        if logged_out: e['out'] = out
        self.log.append(e)
        return out

    def overview(self): return self.s['overview']
    def restore_sampling(self): return self.s['restore_sampling']
    def _restore_release(self): self._ev('restore_release')
    def _handles_on(self): return self.s['handles_on']
    def shape_serial(self): return self.s['shape_serial']
    def can_switch(self): return self.s['can_switch']
    def can_scroll(self): return self.s['can_scroll']

    @property
    def want(self):
        serial, kind, point = self.s['want']
        return serial, kind, tuple(point) if point is not None else None

    @property
    def binds_dropped(self): return self.s['binds_dropped']
    @binds_dropped.setter
    def binds_dropped(self, v):
        self._ev('binds_dropped', v)
        self.s['binds_dropped'] = v

    @property
    def mode(self): return self.s['mode']
    @property
    def _sync(self): return self.s['sync']
    @property
    def shape_gated(self): return self.s['shape_gated']
    @property
    def shape_pushed(self): return self.s['shape_pushed']

    @property
    def shape_wired(self): raise Unscripted('shape_wired read')
    @shape_wired.setter
    def shape_wired(self, v): self._ev('shape_wired', v)

    def feed(self, cursor, motion, outputs, applied):
        self._ev('feed', cursor, motion, [output(o) for o in outputs], applied)

    def feed_shape(self, name, serial): self._ev('feed_shape', name, serial)
    def set_pan(self, frame): self._ev('set_pan', frame)
    def request_switch(self, home, target, panned): self._ev('request_switch', home, target, panned)

    def neighbour(self, side, t):
        return self._ev('neighbour', side, t, out=self.s['neighbours'].get(side), logged_out=True)

    def pan_plan(self, target, side, t, o):
        px = self.s['plan_px']
        plan = ScriptPlan(self.s['selected'], target, side, t, px) if px is not None else None
        self._ev('pan_plan', target, side, t, output(o), out=plan.snap() if plan else None, logged_out=True)
        return plan

    def __getattr__(self, name):
        raise Unscripted(f'pointer.{name}')


def output(o):
    return {k: o[k] for k in ('index', 'x', 'y', 'width', 'height')}


def monitor(name, x, y=0, w=1920, h=1080, scale=1.0, ws=1):
    return {'name': name, 'x': x, 'y': y, 'width': w, 'height': h, 'scale': scale, 'transform': 0,
            'activeWorkspace': {'id': ws}}


MONITORS = [monitor('XR-1', 0, ws=1), monitor('XR-2', 1920, ws=2), monitor('XR-3', 3840, ws=3), monitor('DP-1', 0, 1080)]
NAMES = ['XR-1', 'XR-2', 'XR-3']
BIND = ['/usr/bin/python3', '/x/client.py', '/run/c.sock', 'run', 'session']
CLIENT = {'pos': [960, 540], 'reply': None, 'fail': [], 'monitors': MONITORS, 'workspaces': [], 'clients': [],
          'shape': None}


class ScriptClient:
    """hyprland.HyprlandClient as the sampler calls it: each call logged with its outcome; the cursor kept where the
    calls put it; a call named in `fail` raises OSError."""
    def __init__(self, log):
        self.log, self.s = log, copy.deepcopy(CLIENT)

    def _call(self, call, args, out=None):
        failed = call in self.s['fail'] or (call == 'query' and f'query:{args[0]}' in self.s['fail'])
        self.log.append({'call': call, 'args': list(args), 'out': ERR if failed else out})
        if failed: raise OSError(call)
        return out

    def _move(self, x, y): self.s['pos'] = [x, y]

    def query(self, kind, timeout):
        if kind == 'cursorpos':
            out = self.s['reply'] if self.s['reply'] is not None else {'x': self.s['pos'][0], 'y': self.s['pos'][1]}
        else: out = self.s[kind]
        return copy.deepcopy(self._call('query', [kind, timeout], copy.deepcopy(out)))

    def grab_pointer(self, *a):
        self._call('grab_pointer', a); self._move(*a[-2:])

    def lock_pointer(self, *a):
        self._call('lock_pointer', a); self._move(*a[-2:])

    def release_pointer(self, *a):
        self._call('release_pointer', a)
        if a: self._move(*a)

    def warp_cursor(self, x, y):
        self._call('warp_cursor', [x, y]); self._move(x, y)

    def remove_resize_binds(self): self._call('remove_resize_binds', [])

    def enter_desktop(self, *a, monitor=None):
        self._call('enter_desktop', [*a, monitor])
        if len(a) == 7: self._move(*a[5:])

    def arm_click_binds(self, *a): self._call('arm_click_binds', a)

    def confine_cursor(self, x, y, name):
        self._call('confine_cursor', [x, y, name]); self._move(x, y)

    def hide_overview_cursor(self): self._call('hide_overview_cursor', [])
    def show_overview_cursor(self): self._call('show_overview_cursor', [])


class ScrollingClient(ScriptClient):
    def scroll_to_window(self, address): self._call('scroll_to_window', [address])


class Clock:
    def __init__(self): self.t = 0.0
    def __call__(self): return self.t


class StepStop:
    """The sampler's stop event: wait() ends one step and hands control back; set() ends the loop."""
    def __init__(self):
        self.flag, self.waited = False, None
        self.go, self.back = threading.Semaphore(0), threading.Semaphore(0)

    def is_set(self): return self.flag
    def set(self): self.flag = True

    def wait(self, secs):
        self.waited = secs
        self.back.release()
        self.go.acquire()
        return self.flag


def state(s, variant):
    c = s.cursor
    out = {'applied': s.applied, 'grabbed': s.grabbed, 'ever_grabbed': s.ever_grabbed, 'park': s.park, '_last': s._last,
           '_regrabbed_at': s._regrabbed_at, 'errors': s.errors, 'samples': s.samples, 'recentres': s.recentres,
           'shape_errors': s.shape_errors, 'names_changed': s.names_changed, '_shape_due': s._shape_due,
           '_shape_read': s._shape_read, '_shape_open': s._shape_open, 'names': s.names,
           'cursor': {'_reloads': c._reloads, '_reload_due': c._reload_due, '_forced_at': c._forced_at,
                      'hidden': c.hidden, 'errors': c.errors},
           '_restore_sampling_active': getattr(s, '_restore_sampling_active', False)}
    if variant == 'unified':
        p = s.push
        track = None if p.track is None else {**p.track, 'plan': p.track['plan'].snap()}
        col = s.columns
        out.update(bound=s.bound, confines=s.confines, _outside=s._outside, _pending=s._pending,
                   push={'edge': p.edge, 'amount': p.amount, 'last': p.last, 'quiet_until': p.quiet_until,
                         'track': track, 'frame': p.frame, 'panned': p.panned, 'pulled_back': p.pulled_back},
                   outputs=[output(o) for o in s.outputs],
                   columns=None if col is None else {
                       'snap': col.snap, 'read_name': col.read_name, 'read_at': col.read_at, 'hold': col.hold,
                       'changed': col.changed, 'foreign': col.foreign, 'own': col.own, 'stuck': col.stuck,
                       'scrolls': col.scrolls, 'yields': col.yields, 'reads': col.reads, 'errors': col.errors})
    return enc(out)


def run(case):
    """Run one case's steps through the shipped sampler: the steps with their recordings filled in."""
    log, clock = [], Clock()
    pointer = ScriptPointer(log)
    client = (ScrollingClient if case.get('scrolls') else ScriptClient)(log)

    def read_shape():
        out = client.s['shape']
        log.append({'call': 'read_shape', 'args': [], 'out': ERR if out == ERR else out})
        if out == ERR: raise OSError('shape')
        return out

    stop = StepStop()
    with mock.patch('time.monotonic', clock):
        sampler = CLASSES[case['variant']](client, case['names'], pointer, tuple(case['bind_args']), case['period'],
                                           read_shape if case['shape'] else None)
        sampler.cursor.clock = clock   # its default is the real monotonic, bound at import
        sampler._stop = stop
        init = list(log); log.clear()
        failure = []

        def body():
            stop.go.acquire()
            try: sampler._run()
            except BaseException as e: failure.append(e)
            finally: stop.back.release()

        worker = threading.Thread(target=body, daemon=True)
        sampler._thread = worker
        worker.start()
        steps = []
        for spec in case['steps']:
            clock.t = spec['t']
            pointer.s.update(copy.deepcopy(dec(spec.get('p', {}))))
            client.s.update(copy.deepcopy(dec(spec.get('c', {}))))
            op, ret, stop.waited = spec['op'], None, None
            if op == 'step':
                stop.go.release(); stop.back.acquire()
                if failure: raise failure[0]
            elif op == 'sample_restore':
                ret = sampler.sample_restore([dict(o) for o in spec['outputs']])
            elif op == 'set_names':
                sampler.set_names(spec['names'])
            elif op == 'stop':
                stop.set(); stop.go.release(); stop.back.acquire()
                sampler.stop(timeout=5)
                if failure: raise failure[0]
            else: raise ValueError(op)
            steps.append({**{k: v for k, v in spec.items() if k not in ('events', 'wait', 'ret', 'state')},
                          'events': enc(log), 'wait': stop.waited, 'ret': ret, 'state': state(sampler, case['variant'])})
            log.clear()
        if not stop.flag:
            stop.set(); stop.go.release(); stop.back.acquire()
        worker.join(5)
    return {**{k: v for k, v in case.items() if k not in ('steps', 'pointer', 'init')}, 'pointer': enc(POINTER),
            'init': enc(init), 'steps': steps}


# ----- the scripted runs -----

def case(name, variant, steps, shape=False, scrolls=False, names=NAMES, period=fp.SAMPLE_S):
    return {'name': name, 'variant': variant, 'names': list(names), 'bind_args': list(BIND), 'period': period,
            'shape': shape, 'scrolls': scrolls, 'steps': steps}


class Script:
    """Steps on a clock: `step(**p)` one `_run` iteration `dt` after the last, pointer fields `p`, client fields `c`."""
    def __init__(self, t=10.0): self.t, self.steps = t, []

    def add(self, op, dt, p=None, c=None, **extra):
        self.t = round(self.t + dt, 6)
        s = {'t': self.t, 'op': op, **extra}
        if p: s['p'] = enc(p)
        if c: s['c'] = enc(c)
        self.steps.append(s)
        return self

    def step(self, dt=fp.SAMPLE_S, c=None, **p): return self.add('step', dt, p, c)
    def moves(self, points, dt=fp.SAMPLE_S, **p):
        for i, xy in enumerate(points): self.step(dt, c={'pos': list(xy)}, **(p if i == 0 else {}))
        return self


def free_run(variant):
    s = Script()
    s.step(dt=0)                                                    # outside: shown, nothing held
    s.step(dt=fp.IDLE_S)
    s.step(overview=True, want=[1, 'free', [2880, 540]], mode='free')     # the grab, the cursor hidden
    s.moves([(2890, 545), (2920, 560), (3000, 600), (3150, 700)])   # the hand; strays past the leash / recentre
    s.moves([(2880, 1000), (900, 540)])                              # a jump (a warp by another)
    s.step(c={'pos': [3500, 540]})
    s.step(binds_dropped=True)                                      # a reload took the binds: regrab
    s.step(binds_dropped=True)                                      # again too soon: no loop
    s.step(dt=2.5, c={'pos': [2880, 540]})                          # and the monitors read again
    s.step(want=[2, 'desktop', [400, 300]], mode='desktop')         # handed back into a desktop at a point
    s.step(want=[3, 'free', [2880, 540]], mode='free', c={'fail': ['grab_pointer', 'lock_pointer']})
    s.step()                                                        # the failed grab retried
    s.step(c={'fail': []})
    s.step(want=[4, 'away', None], mode='away')                     # the pointer left: hand back without a point
    s.step(want=[5, 'free', [2880, 540]], mode='free')
    s.step(want=[6, 'off', None], mode='off')                       # off: the binds go
    s.step(want=[7, 'free', [2880, 540]], mode='free')
    s.step(c={'reply': {'x': True, 'y': 3}})                        # malformed replies count as errors
    s.step(c={'reply': {'x': 3}})
    s.step(c={'reply': {'x': math.inf, 'y': 3}})
    s.step(c={'reply': None, 'fail': ['query:cursorpos']})
    s.step(c={'fail': []})
    s.step(reloads=1)                                               # a config reload: the hidden cursor sent again
    s.step(reloads=2)
    s.step(dt=2.1, reloads=3)
    s.add('set_names', 0.001, names=['XR-1', 'XR-3'])
    s.step()
    s.step(dt=fp.IDLE_S, overview=False, mode='away')               # out of the overview: binds gone, cursor shown
    s.step(dt=fp.IDLE_S, c={'fail': ['remove_resize_binds', 'release_pointer', 'show_overview_cursor']})
    s.step(dt=fp.IDLE_S, overview=True, want=[8, 'free', [2880, 540]], mode='free', c={'fail': []})
    s.add('stop', 0.01)
    return case(f'{variant}_free', variant, s.steps)


def shape_run(variant):
    s = Script()
    s.step(overview=True, want=[1, 'desktop', [400, 300]], mode='desktop', handles_on=True, sync=1,
           c={'shape': 'text'})
    for i in range(6): s.step(dt=0.005)
    s.step(dt=0.04)
    s.step(dt=0.01, shape_gated=True)
    s.step(dt=0.01, shape_gated=False, shape_pushed=True, shape_serial=3)
    s.step(dt=0.1)
    s.step(dt=0.2)
    s.step(dt=0.05, c={'shape': None})
    s.step(dt=0.3, c={'shape': ERR})                                # a failed read: the plain dot, and a wait
    s.step(dt=0.5, c={'shape': 'pointer'})
    s.step(dt=0.6)
    s.step(dt=0.01, sync=5)                                         # a warp not yet applied: no read
    s.step(dt=0.01, want=[2, 'desktop', [410, 300]], sync=2)
    s.step(dt=0.05)
    s.step(dt=0.05, handles_on=False)
    s.step(dt=0.05, handles_on=True, mode='free', want=[3, 'free', [2880, 540]])
    s.step(dt=0.05, mode='desktop', want=[4, 'desktop', [400, 300]])
    s.add('stop', 0.01)
    return case(f'{variant}_shape', variant, s.steps, shape=True)


def restore_run(variant):
    s = Script()
    s.step(dt=0, c={'pos': [2880, 100]})
    s.step(restore_sampling=True)                                   # UI ownership asks for samples
    s.step(mode='restore', want=[1, 'free', [2880, 540]])           # the pointer owns it: grab, leash
    s.moves([(2880, 560), (2900, 700), (2880, 545)])
    s.step(dt=2.5)
    s.step(restore_sampling=False)                                  # gate shut while active: released
    s.step(mode='away', want=[2, 'away', None])
    s.step(dt=fp.IDLE_S)
    s.step(restore_sampling=True, mode='off', want=[3, 'off', None])  # sampling, not owned (the click binds, unified)
    s.step(c={'fail': ['query:monitors']}, dt=2.5)
    s.step(c={'fail': []})
    s.add('sample_restore', 0.01, outputs=enc([output(o) for o in uc_outputs()]), p={'mode': 'restore'})
    s.add('sample_restore', 0.01, outputs=[], p={'restore_sampling': False, 'mode': 'away'})
    s.add('sample_restore', 0.01, outputs=[])
    s.step(restore_sampling=False)
    s.step(dt=fp.IDLE_S)
    s.add('stop', 0.01)
    return case(f'{variant}_restore', variant, s.steps)


def uc_outputs():
    import tools.native_spike.resize as resize
    return resize.outputs_from_monitors(MONITORS, NAMES)


def unified_desktop():
    s = Script()
    s.step(overview=True, want=[1, 'desktop', [2400, 300]], mode='desktop')   # in from away: binds, that monitor
    s.step(want=[2, 'desktop', [2410, 310]])                                 # the binds in: only the cursor moves
    s.step(want=[3, 'desktop', None])
    s.step(want=[4, 'free', [2880, 540]], mode='free')                       # free: the lock's grab
    s.moves([(2890, 540), (2930, 560), (3300, 900)])
    s.step(want=[5, 'carry', [2000, 500]], mode='carry')                     # carry from a grab: enter_desktop
    s.moves([(2003, 500), (2010, 500), (2020, 520)])                         # its short leash
    s.step(want=[6, 'carry', [2100, 500]])                                   # carry without a grab: warp
    s.step(want=[7, 'carry', None])                                          # no point: a failure, retried
    s.step(want=[8, 'desktop', [400, 300]], mode='desktop')
    s.step(want=[9, 'desktop', [5000, 300]])
    s.step(want=[10, 'away', None], mode='away')                             # released
    s.step(want=[11, 'desktop', None], mode='desktop')                       # in without a point
    s.step(binds_dropped=True)                                               # a reload: the click binds count
    s.step(want=[12, 'off', None], mode='off')
    s.step(want=[13, 'desktop', [400, 300]], mode='desktop', c={'fail': ['enter_desktop']})
    s.step(c={'fail': []})
    s.step(dt=fp.IDLE_S, overview=False, mode='away')
    s.add('stop', 0.01)
    return case('unified_desktop', 'unified', s.steps)


def unified_confine():
    s = Script()
    s.step(dt=0, c={'pos': [2880, 540]})                                     # home XR-2, on it: fast
    s.step(c={'pos': [4000, 540]})                                           # on XR-3: back on home
    s.step(c={'pos': [2880, 1300]})                                          # off the XR monitors from home: back
    s.step(c={'pos': [500, 2000]})                                           # off from home, far: left
    s.step(dt=fp.IDLE_S, c={'pos': [500, 1500]})                             # still off
    s.step(dt=fp.IDLE_S, c={'pos': [3000, 500], 'fail': ['confine_cursor']})
    s.step(c={'pos': [4000, 500]})                                           # a failed confinement
    s.step(c={'fail': [], 'reply': {'x': 'a', 'y': 1}})
    s.step(dt=fp.IDLE_S, c={'reply': None, 'pos': [2900.5, 600.25]})         # floats kept outside the overview
    s.step(selected=7, c={'pos': [2900, 600]})                               # home not among the outputs
    s.step(dt=fp.IDLE_S, selected=1)
    s.step(c={'monitors': [], 'pos': [2900, 600]}, dt=2.5)                   # no XR monitor: nothing to sample
    s.step(dt=fp.IDLE_S)
    s.step(dt=2.5, c={'monitors': MONITORS})
    s.step(restore_sampling=True)                                            # maximized: the click binds
    s.step(c={'fail': ['arm_click_binds']}, restore_sampling=False)
    s.step(restore_sampling=True)
    s.step(c={'fail': []})
    s.step()
    s.step(restore_sampling=False)                                           # gate shut: released
    s.add('stop', 0.01)
    return case('unified_confine', 'unified', s.steps)


def unified_push(plan_px):
    s = Script()
    s.step(dt=0, can_switch=True, neighbours={'right': 2, 'up': 0}, plan_px=plan_px, c={'pos': [3700, 540]})
    x = 3700
    for i in range(14):                                                       # pushed over onto XR-3, back each time
        x = 3839 + 8
        s.step(dt=0.02, c={'pos': [x + (i % 3), 540 + i]})
    s.step(dt=0.02)
    s.step(dt=0.02, selected=2, c={'pos': [3850, 560]})                      # the loop switched: in over the edge
    s.step(dt=0.02, c={'pos': [3900, 560]})
    s.step(dt=1.0, c={'pos': [3840, 10]})                                    # cooldown over; nothing that way
    s.step(dt=0.02, c={'pos': [3842, 0]})                                    # on XR-3's top edge: up is XR-1 (0)
    for i in range(8): s.step(dt=0.02, c={'pos': [3842 + i, 0]})
    s.step(dt=0.3, c={'pos': [3842, 0]})                                     # a pause: the push starts over
    s.step(dt=0.02, can_switch=False, c={'pos': [3842, 0]})                  # no switch now: reset
    s.step(dt=0.02, can_switch=True, selected=2, c={'pos': [3842, 0]})
    for i in range(5): s.step(dt=0.02, c={'pos': [3842, 0]})
    s.step(dt=0.1, c={'pos': [3842, 0]})
    s.step(dt=0.7, c={'pos': [3842, 500]})                                   # the requested switch never came: dropped
    s.add('stop', 0.01)
    return case(f'unified_push_{"pan" if plan_px else "cut"}', 'unified', s.steps)


def unified_pan():
    s = Script()
    s.step(dt=0, can_switch=True, neighbours={'right': 2, 'left': 0}, plan_px=120, c={'pos': [3700, 540]})

    def start(y=540):
        for i in range(3): s.step(dt=0.02, c={'pos': [3849, y]})             # past START_PX: the pan starts, held

    start()
    for x in (3780, 3790, 3760): s.step(dt=0.02, c={'pos': [x, 540]})       # the hand on, then back: pulled back
    s.step(dt=0.9, c={'pos': [3700, 540]})
    start()
    for dt in (0.02, 0.3, 0.35): s.step(dt=dt, c={'pos': [3775, 540]})     # a rest: pulled back
    s.step(dt=0.9, c={'pos': [3700, 540]})
    start(700)
    for i in range(6): s.step(dt=0.02, c={'pos': [3800, 700]})             # all the way: the switch
    for i in range(3): s.step(dt=0.02, c={'pos': [3800, 700]})
    s.step(dt=0.02, selected=2, c={'pos': [3800, 700]})                    # the loop switched: in over the edge
    s.step(dt=0.02, c={'pos': [4200, 700]})
    s.step(dt=0.9, selected=1, c={'pos': [3700, 700]})
    start()
    s.step(dt=0.02, overview=True, want=[1, 'free', [2880, 540]], mode='free')  # back to the overview mid-pan
    s.step(dt=0.02, overview=False, mode='away', c={'pos': [3700, 540]})
    start()
    s.step(dt=0.02, selected=0, c={'pos': [3790, 540]})                    # a key took the selection
    s.step(dt=0.02, c={'pos': [500, 540]})
    s.step(dt=0.02, selected=1, c={'pos': [1921, 540]})
    for i in range(3): s.step(dt=0.02, c={'pos': [1910, 300]})             # the other way, onto XR-1
    s.step(dt=0.02, plan_px=None, c={'pos': [1960, 300]})                  # the plan gone mid-pan
    for x in (1980, 2000, 1900): s.step(dt=0.02, c={'pos': [x, 300]})
    s.add('stop', 0.01)
    return case('unified_pan', 'unified', s.steps)


def clients(*placed, ws=2):
    return [{'address': a, 'workspace': {'id': ws}, 'floating': False, 'mapped': True, 'hidden': False,
             'at': [x, 0], 'size': [w, 1080]} for a, x, w in placed]


def unified_columns():
    s = Script()
    scrolling = [{'id': 2, 'tiledLayout': 'scrolling', 'hasfullscreen': False}]
    s.step(dt=0, can_switch=True, can_scroll=True, neighbours={'right': 2, 'left': 0},
           c={'pos': [3000, 540], 'workspaces': scrolling,
              'clients': clients(('0x1', 1920, 1200), ('0x2', 3130, 1200))})
    for i in range(8): s.step(dt=0.03, c={'pos': [3839, 540]})              # at the right edge, a column past it
    s.step(dt=0.03, c={'clients': clients(('0x1', 720, 1200), ('0x2', 1930, 1200)), 'pos': [3839, 540]})  # it scrolled
    for i in range(6): s.step(dt=0.03, c={'pos': [3839, 540]})
    for i in range(6): s.step(dt=0.12, c={'pos': [3850, 540]})              # the end rested: the push counts again
    s.step(dt=0.03, c={'pos': [2000, 540], 'fail': ['query:workspaces']})
    s.step(dt=0.03, c={'pos': [1920, 540]})                                 # at the left edge, a failed read
    s.step(dt=0.03, c={'fail': [], 'workspaces': [{'id': 2, 'tiledLayout': 'master'}], 'pos': [1921, 540]})
    s.step(dt=0.2, c={'workspaces': scrolling, 'clients': clients(('0x3', 500, 1200), ('0x4', 1920, 1920)),
                       'pos': [1920, 540]})
    for i in range(8): s.step(dt=0.1, c={'pos': [1919, 540], 'fail': ['scroll_to_window'] if i < 4 else []})
    s.add('stop', 0.01)
    return case('unified_columns', 'unified', s.steps, scrolls=True)


def idle_runs():
    out = []
    for variant in ('plain', 'locked', 'unified'):
        s = Script()
        for dt in (0, fp.IDLE_S, fp.IDLE_S, 2.5, fp.IDLE_S):
            s.step(dt=dt, c={'monitors': []})
        s.step(dt=0.01, overview=True, want=[1, 'free', [100, 100]], mode='free', c={'monitors': MONITORS})
        s.step()
        s.step(dt=0.01, overview=False, mode='away')
        s.step(dt=fp.IDLE_S)
        s.add('stop', 0.01)
        out.append(case(f'{variant}_idle', variant, s.steps))
    return out


def cases():
    out = idle_runs()
    for variant in ('plain', 'locked', 'unified'):
        out += [free_run(variant), shape_run(variant), restore_run(variant)]
    out += [unified_desktop(), unified_confine(), unified_push(None), unified_push(90), unified_pan(), unified_columns()]
    return out


def record():
    return [run(c) for c in cases()]


def dump(corpus):
    return json.dumps(corpus, indent=1, allow_nan=False) + '\n'


def same(a, b):
    if isinstance(a, bool) or isinstance(b, bool): return a is b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)): return a == b
    if isinstance(a, list) and isinstance(b, list): return len(a) == len(b) and all(map(same, a, b))
    if isinstance(a, dict) and isinstance(b, dict): return a.keys() == b.keys() and all(same(a[k], b[k]) for k in a)
    return a == b


def replay(stored):
    """Run a stored case through the shipped Python again; raises Mismatch at the first difference."""
    got = run(json.loads(json.dumps(stored)))
    if not same(got['pointer'], stored['pointer']): raise Mismatch(f'{stored["name"]}: the pointer\'s answers changed')
    if not same(got['init'], stored['init']): raise Mismatch(f'{stored["name"]}: init {got["init"]} != {stored["init"]}')
    if len(got['steps']) != len(stored['steps']):
        raise Mismatch(f'{stored["name"]}: {len(got["steps"])} steps != {len(stored["steps"])} stored')
    for i, (g, s) in enumerate(zip(got['steps'], stored['steps'])):
        for k in ('events', 'wait', 'ret', 'state'):
            if not same(g[k], s[k]):
                raise Mismatch(f'{stored["name"]} step {i} {k}:\n  python {json.dumps(g[k])}\n  stored {json.dumps(s[k])}')


def main():
    corpus = record()
    steps = sum(len(c['steps']) for c in corpus)
    events = sum(len(s['events']) for c in corpus for s in c['steps'])
    calls = {}
    for c in corpus:
        for s in c['steps']:
            for e in s['events']: calls[e['call']] = calls.get(e['call'], 0) + 1
    print(f'{len(corpus)} cases, {steps} steps, {events} events: {dict(sorted(calls.items()))}')
    if '--write' in sys.argv:
        CORPUS_FILE.write_text(dump(corpus))


if __name__ == '__main__':
    main()
