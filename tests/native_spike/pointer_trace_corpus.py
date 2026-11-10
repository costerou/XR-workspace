"""Phase 6 corpus for the Rust rebuild (docs/rust-rebuild-plan.md): recorded overview pointer traces.

The overview pointer (free_pointer.OverviewPointer, unified_cursor.UnifiedPointer) is a state machine driven by the
sampler (feed), the run loop (poll) and the control socket (on_op and the view's other calls). Its unit tests drive it
through a real view.ViewControl on a fake clock. This module runs those tests with the pointer's view of its control
recorded, and writes every replayable run as a trace: rust/corpus/pointer/traces-<test module>.json.

A trace is the pointer's whole conversation with the outside, one step per outermost call into it:
  * `m`, `a`, `t`: the method, its arguments and the clock;
  * `s`, `c`: the view state fields (ViewState) and control attributes (CONTROL_ATTRS) that changed since the end of the
    last step, applied before the call (the test or the view changed them);
  * `calls`: each call the pointer made into its control, in order: name, arguments, result, and the state and control
    attributes after it returns (`s`, `c`, applied by a replay) and before it (`chk`, the pointer's own changes, which a
    replay checks). Callbacks passed to the control (a window lookup) are `{"$cb": n}`; `cb` lists those called
    during the call, with their arguments;
  * `r`: the result (frames are {"$b": text}); `post_s`, `post_c`: the state and control attributes the step ends
    with (the pointer's own changes, checked); `p`: the pointer fields in PROBE that changed (checked).
The Rust port (rust/crates/xr-workspace/src/supervisor/) replays each trace with a control that answers from the
trace, and must make the same calls with the same arguments and return the same bytes.

A run is kept only when it replays clean through the shipped Python pointer (ReplayControl below): a test that reaches
into the pointer's internals, drives it from another thread or uses the wall clock is left out, and the count of each
is printed. test_pointer_trace_corpus.py replays every stored trace through the current Python and fails when they
disagree, so the files stay true to the shipped code.

Regenerate (after a deliberate change to the pointer or its tests) with:

    TMPDIR=/tmp /usr/bin/python3 tests/native_spike/pointer_trace_corpus.py --write
"""
import collections
import functools
import io
import json
import math
from pathlib import Path
import sys
import threading
import unittest

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
for p in (str(ROOT), str(HERE)):
    if p not in sys.path: sys.path.insert(0, p)

from tools.native_spike import free_pointer as fp, unified_cursor as uc, view  # noqa: E402

CORPUS_DIR = ROOT / 'rust' / 'corpus' / 'pointer'
MODULES = ('test_free_pointer', 'test_free_pointer_focus', 'test_unified_cursor', 'test_edge_pan', 'test_glass_pills',
           'test_text_cursor', 'test_super_drag', 'test_window_drag', 'test_overview_cursor', 'test_bump',
           'test_directional_switch', 'test_full_interaction', 'test_layout_reset', 'test_window_memory_run', 'test_video',
           'test_web_video')
# Plain attributes of the control the pointer reads or writes (options: its menu's two the pointer touches).
CONTROL_ATTRS = ('video_active', 'keys_open', 'help_visible', 'reloads', 'next_id', 'run_id', 'session', 'save_due',
                 'last_input', 'options.visible', 'options.capture', 'options.preview', 'options.focused',
                 'options.keyboard_owned', 'sender')
# Pointer fields a replay checks after each step.
PROBE = ('mode', 'want', 'desktop', 'pointer', 'hover', 'gain', 'summoned', 'rolls', 'levelling', 'zoom_held', 'shown',
         'roll_names', 'binds_dropped')
# Pointer fields the view itself writes (view.ViewControl, managed.py): a step's `pf` holds those changed since the
# last step, set before the call.
EXTERNAL = ('rolls', 'roll_names', 'binds_dropped')
# Every public method of the two pointer classes is an entry; so are these (the view and the sampler call them).
EXTRA_ENTRIES = ('_poke',)


class Unreplayable(Exception):
    pass


# --- encoding -------------------------------------------------------------------------------------------------------
def enc(v):
    """JSON-ready form of a value: tuples as lists, bytes as {"$b": text}; anything else refuses the run."""
    if v is None or isinstance(v, (bool, int, str)): return v
    if isinstance(v, float):
        if not math.isfinite(v): return {'$f': repr(v)}
        return v
    if isinstance(v, (bytes, bytearray)): return {'$b': bytes(v).decode('utf-8')}
    if isinstance(v, (list, tuple)): return [enc(x) for x in v]
    if isinstance(v, dict):
        if not all(isinstance(k, str) for k in v): raise Unreplayable(f'non-string key in {v!r:.80}')
        return {k: enc(x) for k, x in v.items()}
    raise Unreplayable(f'unencodable {type(v).__name__}')


def dec(v):
    if isinstance(v, list): return [dec(x) for x in v]
    if isinstance(v, dict):
        if set(v) == {'$b'}: return v['$b'].encode('utf-8')
        if set(v) == {'$f'}: return float(v['$f'])
        return {k: dec(x) for k, x in v.items()}
    return v


def same(a, b):
    """Recorded value a against b, both encoded: floats to 1e-9 relative (libm), everything else exactly."""
    if isinstance(a, float) or isinstance(b, float):
        return isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool) \
            and not isinstance(b, bool) and math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9)
    if isinstance(a, list) and isinstance(b, list): return len(a) == len(b) and all(map(same, a, b))
    if isinstance(a, dict) and isinstance(b, dict): return a.keys() == b.keys() and all(same(a[k], b[k]) for k in a)
    return a == b and type(a) is type(b)


def state_snapshot(state):
    return {k: json.dumps(enc(v), sort_keys=True) for k, v in vars(state).items()}


def control_snapshot(control):
    out = {}
    for name in CONTROL_ATTRS:
        if name == 'sender':
            out[name] = json.dumps(getattr(control, 'sender', None) is not None)
            continue
        obj = control
        for part in name.split('.'):
            obj = getattr(obj, part, None)
        out[name] = json.dumps(enc(obj), sort_keys=True)
    return out


def diff(now, before):
    return {k: json.loads(v) for k, v in now.items() if before.get(k) != v}


def probe(pointer):
    return {k: json.dumps(enc(getattr(pointer, k, None)), sort_keys=True) for k in PROBE}


# --- recording ------------------------------------------------------------------------------------------------------
class Recording:
    def __init__(self, pointer, control, kind, kwargs):
        self.pointer, self.control, self.kind = pointer, control, kind
        self.thread = threading.get_ident()
        self.bad = None
        self.depth = 0
        self.steps = []
        self.callbacks = []
        self.state = state_snapshot(control.state)
        self.attrs = control_snapshot(control)
        self.probe = None
        self.init = {'class': kind, 'kwargs': kwargs, 'names': list(control.state.names),
                     'state': {k: json.loads(v) for k, v in self.state.items()},
                     'control': {k: json.loads(v) for k, v in self.attrs.items()}}
        self.call_log = None

    def fail(self, why):
        if self.bad is None: self.bad = why

    def sync(self):
        """The state and control attributes changed since the last sync."""
        s, c = state_snapshot(self.control.state), control_snapshot(self.control)
        out = diff(s, self.state), diff(c, self.attrs)
        self.state, self.attrs = s, c
        return out

    def case(self, name):
        return {'test': name, 'init': self.init, 'steps': self.steps}


RECORDINGS = []
_LOCAL = threading.local()


class RecordingControl:
    """What a recorded pointer sees as its control: the real ViewControl, every method call logged."""
    def __init__(self, rec, real):
        object.__setattr__(self, '_rec', rec)
        object.__setattr__(self, '_real', real)

    def __getattr__(self, name):
        rec, real = self._rec, self._real
        if name == 'sender':
            sender = getattr(real, 'sender', None)
            return None if sender is None else RecordingObject(rec, sender, 'sender')
        value = getattr(real, name)
        if name == 'options': return RecordingObject(rec, value, 'options')
        if name == 'state' or not callable(value) or isinstance(value, type): return value
        return functools.partial(_logged_call, rec, name, value)

    def __setattr__(self, name, value):
        setattr(self._real, name, value)


class RecordingObject:
    def __init__(self, rec, real, prefix):
        object.__setattr__(self, '_rec', rec)
        object.__setattr__(self, '_real', real)
        object.__setattr__(self, '_prefix', prefix)

    def __setattr__(self, name, value):
        setattr(self._real, name, value)

    def __getattr__(self, name):
        value = getattr(self._real, name)
        if not callable(value):
            if self._prefix == 'options': return value  # its plain fields are in the control snapshot
            self._rec.fail(f'{self._prefix}.{name} read')
            return value
        return functools.partial(_logged_call, self._rec, f'{self._prefix}.{name}', value)


def _logged_call(rec, name, fn, *args, **kwargs):
    log = rec.call_log
    if log is None:  # a control call outside any entry (a thread, or the test calling a closure)
        rec.fail(f'control call {name} outside an entry')
        return fn(*args, **kwargs)
    entry = {'n': name}
    chk_s, chk_c = rec.sync()
    if chk_s: entry['chk'] = chk_s
    if chk_c: entry['chk_c'] = chk_c
    wrapped = []
    for a in args:
        if callable(a):
            k = len(rec.callbacks)
            rec.callbacks.append(a)
            wrapped.append(_callback(rec, entry, k, a))
        else:
            wrapped.append(a)
    try:
        entry['a'] = [{'$cb': rec.callbacks.index(a)} if callable(a) else enc(a) for a in args]
        if kwargs: entry['k'] = enc(kwargs)
    except Unreplayable as e:
        rec.fail(f'{name} args: {e}')
        entry['a'] = None
    log.append(entry)
    try:
        result = fn(*wrapped, **kwargs)
    except Exception as e:  # noqa: BLE001 - recorded, re-raised
        rec.fail(f'{name} raised {type(e).__name__}')
        raise
    try:
        entry['r'] = enc(result)
    except Unreplayable as e:
        rec.fail(f'{name} result: {e}')
    s, c = rec.sync()
    if s: entry['s'] = s
    if c: entry['c'] = c
    return result


def _callback(rec, entry, k, fn):
    def call(*args):
        if rec.call_log is None or entry not in rec.call_log or rec.depth != 1:
            rec.fail('callback called outside its call')
        else:
            try: entry.setdefault('cb', []).append([k, enc(list(args))])
            except Unreplayable as e: rec.fail(f'callback args: {e}')
        return fn(*args)
    return call


def _entry(name, fn):
    @functools.wraps(fn)
    def run(self, *args, **kwargs):
        rec = self.__dict__.get('_trace_rec')
        if rec is None or rec.depth:
            return fn(self, *args, **kwargs)
        if threading.get_ident() != rec.thread: rec.fail(f'{name} from another thread')
        step = {'m': name, 't': enc(rec.pointer.clock())}
        try: step['a'] = enc(list(args))
        except Unreplayable as e: rec.fail(f'{name} args: {e}'); step['a'] = None
        if kwargs:
            try: step['k'] = enc(kwargs)
            except Unreplayable as e: rec.fail(f'{name} kwargs: {e}')
        s, c = rec.sync()
        if s: step['s'] = s
        if c: step['c'] = c
        now = probe(self)
        pf = {k: json.loads(now[k]) for k in EXTERNAL if now[k] != rec.probe.get(k)}
        if pf:
            step['pf'] = pf
            rec.probe.update({k: now[k] for k in EXTERNAL})
        calls = rec.call_log = []
        rec.depth = 1
        try:
            result = fn(self, *args, **kwargs)
        except Exception as e:  # noqa: BLE001
            step['raise'] = type(e).__name__
            rec.steps.append(step)
            raise
        finally:
            rec.depth = 0
            rec.call_log = None
        if calls: step['calls'] = calls
        try: step['r'] = enc(result)
        except Unreplayable as e: rec.fail(f'{name} result: {e}')
        s, c = rec.sync()
        if s: step['post_s'] = s
        if c: step['post_c'] = c
        p = probe(self)
        changed = {k: json.loads(v) for k, v in p.items() if rec.probe is None or rec.probe.get(k) != v}
        rec.probe = p
        if changed: step['p'] = changed
        rec.steps.append(step)
        return result
    return run


def entries(cls):
    return sorted(n for n, v in vars(cls).items() if callable(v) and (not n.startswith('_') or n in EXTRA_ENTRIES)
                  and not isinstance(v, (staticmethod, classmethod)))


_INSTALLED = []


def install():
    """Patch the pointer classes so every instance on a fake clock with a ViewControl is recorded."""
    if _INSTALLED: return
    for cls in (fp.OverviewPointer, uc.UnifiedPointer):
        for name in entries(cls):
            setattr(cls, name, _entry(name, vars(cls)[name]))
    original = fp.OverviewPointer.__init__

    def init(self, control, *args, **kwargs):
        clock = kwargs.get('clock', args[0] if args else None)
        record = isinstance(control, view.ViewControl) and clock is not None and not _wall(clock) and len(args) <= 1
        if not record:
            return original(self, control, *args, **kwargs)
        try: kw = enc({k: v for k, v in kwargs.items() if k != 'clock'})
        except Unreplayable: return original(self, control, *args, **kwargs)
        rec = Recording(self, control, type(self).__name__, kw)
        rec.test = getattr(_LOCAL, 'test', None)
        rec.depth = 1  # the constructor's own calls are the replay's constructor's
        rec.call_log = []
        try:
            original(self, RecordingControl(rec, control), *args, **kwargs)
        finally:
            rec.depth = 0
            if rec.call_log: rec.fail('control calls in the constructor')
            rec.call_log = None
        rec.sync()
        rec.probe = probe(self)
        rec.init['probe'] = {k: json.loads(v) for k, v in rec.probe.items()}
        self.__dict__['_trace_rec'] = rec
        RECORDINGS.append(rec)
    fp.OverviewPointer.__init__ = init
    _INSTALLED.append(True)


def _wall(clock):
    import time
    return clock in (time.monotonic, time.time, time.perf_counter)


# --- replay ---------------------------------------------------------------------------------------------------------
class ReplayClock:
    def __init__(self, now): self.now = now
    def __call__(self): return self.now


class ReplayOptions:
    def __init__(self, control):
        self._control = control
        self.visible = self.capture = self.preview = self.focused = self.keyboard_owned = None

    def __getattr__(self, name):
        if name.startswith('__'): raise AttributeError(name)
        return functools.partial(self._control._answer, f'options.{name}')


class ReplaySender:
    def __init__(self, control): self._control = control

    def __getattr__(self, name):
        return functools.partial(self._control._answer, f'sender.{name}')


class Mismatch(AssertionError):
    pass


class ReplayControl:
    """A control that answers the pointer from a trace: each call must be the recorded one, in order."""
    def __init__(self, init):
        self.state = view.ViewState(init['names'])
        self.options = ReplayOptions(self)
        self._calls = None
        self._apply_state(init['state'])
        self._apply_attrs(init['control'])

    def _apply_state(self, fields):
        for k, v in fields.items(): setattr(self.state, k, _restore(k, dec(v)))

    def _apply_attrs(self, fields):
        for k, v in fields.items():
            v = dec(v)
            if k == 'sender': self.sender = ReplaySender(self) if v else None
            elif k.startswith('options.'): setattr(self.options, k.split('.', 1)[1], v)
            else: setattr(self, k, v)

    def _check(self, where, fields, attrs):
        if fields:
            now = {k: json.loads(v) for k, v in state_snapshot(self.state).items()}
            for k, v in fields.items():
                if not same(v, now.get(k)): raise Mismatch(f'{where}: state.{k} {now.get(k)!r:.200} != {v!r:.200}')
        if attrs:
            now = {k: json.loads(v) for k, v in control_snapshot(self).items()}
            for k, v in attrs.items():
                if not same(v, now.get(k)): raise Mismatch(f'{where}: control.{k} {now.get(k)!r:.120} != {v!r:.120}')

    def _answer(self, name, *args, **kwargs):
        if self._calls is None or not self._calls: raise Mismatch(f'unrecorded call {name}{args!r:.200}')
        entry = self._calls.pop(0)
        if entry['n'] != name: raise Mismatch(f'call {name} where the trace has {entry["n"]}')
        self._check(f'before {name}', entry.get('chk'), entry.get('chk_c'))
        got = [{'$cb': None} if callable(a) else enc(a) for a in args]
        want = [{'$cb': None} if isinstance(a, dict) and set(a) == {'$cb'} else a for a in entry['a']]
        if not same(want, got): raise Mismatch(f'{name} args {got!r:.300} != {want!r:.300}')
        if not same(entry.get('k', {}), enc(kwargs)): raise Mismatch(f'{name} keywords {kwargs!r:.200} != {entry.get("k")!r:.200}')
        for k, cb_args in entry.get('cb', ()):
            i = [j for j, a in enumerate(entry['a']) if a == {'$cb': k}][0]
            args[i](*dec(cb_args))
        self._apply_state(entry.get('s', {}))
        self._apply_attrs(entry.get('c', {}))
        return dec(entry.get('r'))

    def __getattr__(self, name):
        if name.startswith('__'): raise AttributeError(name)
        return functools.partial(self._answer, name)


def _restore(field, value):
    """A ViewState field from its JSON form (the fields that are tuples in the Python)."""
    return value


def replay(case):
    """Replay one trace through the shipped Python pointer; raises Mismatch at the first difference."""
    init = case['init']
    control = ReplayControl(init)
    clock = ReplayClock(0.0)
    cls = {'OverviewPointer': fp.OverviewPointer, 'UnifiedPointer': uc.UnifiedPointer}[init['class']]
    pointer = cls(control, clock=clock, **dec(init['kwargs']))
    try:
        seen = {k: json.loads(v) for k, v in probe(pointer).items()}
        for k, v in init['probe'].items():
            if not same(v, seen[k]): raise Mismatch(f'init: {k} {seen[k]!r} != {v!r}')
        for n, step in enumerate(case['steps']):
            where = f'step {n} {step["m"]}'
            clock.now = dec(step['t'])
            control._apply_state(step.get('s', {}))
            control._apply_attrs(step.get('c', {}))
            for k, v in step.get('pf', {}).items(): setattr(pointer, k, dec(v))
            control._calls = list(step.get('calls', ()))
            try:
                result = getattr(pointer, step['m'])(*dec(step['a']), **dec(step.get('k', {})))
            except Mismatch as e:
                raise Mismatch(f'{where}: {e}') from None
            except Exception as e:  # noqa: BLE001
                if step.get('raise') == type(e).__name__: continue
                raise Mismatch(f'{where}: raised {type(e).__name__}: {e}') from None
            if 'raise' in step: raise Mismatch(f'{where}: did not raise {step["raise"]}')
            if control._calls: raise Mismatch(f'{where}: missing calls {[c["n"] for c in control._calls]}')
            control._calls = None
            if not same(step.get('r'), enc(result)): raise Mismatch(f'{where}: result {enc(result)!r:.300} != {step.get("r")!r:.300}')
            control._check(where, step.get('post_s'), step.get('post_c'))
            got = {k: json.loads(v) for k, v in probe(pointer).items()}
            for k, v in step.get('p', {}).items():
                if not same(v, got[k]): raise Mismatch(f'{where}: pointer.{k} {got[k]!r:.200} != {v!r:.200}')
    finally:
        pointer.close()


# --- generation -----------------------------------------------------------------------------------------------------
class _NamedResult(unittest.TextTestResult):
    def startTest(self, test):
        _LOCAL.test = test.id()
        super().startTest(test)


def record(modules=MODULES):
    """Run each module's tests with recording on; {module: [case]} plus a count of left-out runs by reason."""
    install()
    out, dropped = {}, collections.Counter()
    for module in modules:
        RECORDINGS.clear()
        suite = unittest.defaultTestLoader.loadTestsFromName(module)
        stream = io.StringIO()
        result = unittest.TextTestRunner(stream=stream, resultclass=_NamedResult, verbosity=0).run(suite)
        failed = {t.id() for t, _ in result.failures + result.errors}
        cases = []
        for rec in RECORDINGS:
            name = rec.test or '?'
            if name in failed: dropped['test failed while recorded'] += 1; continue
            if rec.bad: dropped[rec.bad.split(':')[0]] += 1; continue
            if not {s['m'] for s in rec.steps} - {'set_geometry', 'close'}: continue  # set up only
            case = rec.case(name)
            try:
                replay(json.loads(json.dumps(case)))
            except Mismatch as e:
                dropped['does not replay: ' + str(e).split(':')[1].strip().split(' ')[0]] += 1
                continue
            cases.append(case)
        names = collections.Counter(c['test'] for c in cases)
        seen = collections.Counter()
        for c in cases:  # a test that makes several pointers: number them
            if names[c['test']] > 1:
                seen[c['test']] += 1
                c['test'] = f'{c["test"]}#{seen[c["test"]]}'
        out[module] = cases
    return out, dropped


def dump(cases):
    return json.dumps(cases, separators=(',', ':'), ensure_ascii=False) + '\n'


def corpus_file(module):
    return CORPUS_DIR / f'traces-{module.removeprefix("test_")}.json'


def main():
    corpus, dropped = record()
    total = 0
    for module, cases in corpus.items():
        total += len(cases)
        steps = sum(len(c['steps']) for c in cases)
        text = dump(cases)
        print(f'{module}: {len(cases)} traces, {steps} steps, {len(text) // 1024} KiB')
        if '--write' in sys.argv:
            CORPUS_DIR.mkdir(parents=True, exist_ok=True)
            corpus_file(module).write_text(text)
    print(f'{total} traces; left out: {dict(dropped.most_common())}')


if __name__ == '__main__':
    main()
