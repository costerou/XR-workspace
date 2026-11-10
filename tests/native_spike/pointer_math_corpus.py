"""Phase 6 corpus for the Rust rebuild (docs/rust-rebuild-plan.md): the pointer's pure maths.

rust/corpus/pointer/math.json is made only by this module, from the shipped tools/native_spike/orbit.py, neighbors.py,
restore_band.py, bump.py, edge_pan.py, free_pointer.py, options_geometry.py and unified_cursor.py, by recording the
calls the Python's own unit tests really make: every module-level function of the eight modules (private helpers too)
is wrapped, run the test suites below, and each call's arguments and result are kept. The classes are recorded too:
Footprint and Plan as calls (their constructor, and each method on a recorded instance), PillDrag, Pusher, Dwell and
TextHold as traces per instance (the constructor, then each outer method call with its result and the instance's state
after it), since their methods depend on what came before. The pointer's own classes (OverviewPointer, PointerSampler,
UnifiedPointer, UnifiedSampler, EdgePush) are not: they drive the functions, and their calls are what is recorded. The Rust
port (rust/crates/xr-workspace/src/supervisor/pointer_math_corpus_tests.rs) replays every case and must give the same
bytes.

Results are kept as the bytes `json.dumps(result, separators=(',', ':'))` writes (tuples as lists, a Footprint as
{"$footprint": {...}}, a Plan as {"$plan": {...}}, a Pusher as {"$pusher": its state}, bytes as {"$bytes": text}), so
an int stays `1` and a float `1.0`. Arguments are kept as JSON values in the same encoding.

Left out, and counted in the file's "skipped" (with "no recorded suite calls it" for a function none of them reach): a call whose arguments or result hold a non-finite float (JSON has none),
or something that is not plain data (a test's mock); and a call that does not give the recorded result when replayed
on its own (the tests that patch orbit.place_point / orbit._outline, and the footprints the lru_cache kept from them).

Each function keeps at most CAP distinct cases (BUDGET bytes), chosen by a hash of the case, so the choice does not
depend on the order the calls came in; each class at most TRACES traces of at most TRACE_CALLS calls.

Regenerate (after a deliberate change to one of the eight modules or the tests that drive them) with:

    TMPDIR=/tmp /usr/bin/python3 tests/native_spike/pointer_math_corpus.py --write

test_pointer_math_corpus.py fails when the file and the Python code disagree.
"""
import functools
import hashlib
import inspect
import io
import json
import math
from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for path in (ROOT, HERE):
    if str(path) not in sys.path: sys.path.insert(0, str(path))

from tools.native_spike import (bump, edge_pan, free_pointer, neighbors, options_geometry, orbit,  # noqa: E402
                                restore_band, unified_cursor)

CORPUS_FILE = ROOT / 'rust' / 'corpus' / 'pointer' / 'math.json'
MODULES = {'orbit': orbit, 'neighbors': neighbors, 'restore_band': restore_band, 'bump': bump, 'edge_pan': edge_pan,
           'free_pointer': free_pointer, 'options_geometry': options_geometry, 'unified_cursor': unified_cursor}
# Calls (classes whose methods are pure given the instance) and traces (instances that carry state from call to call).
CALL_CLASSES = {'orbit.Footprint': (orbit.Footprint, ('rotated',)), 'edge_pan.Plan': (edge_pan.Plan, ('frame',))}
TRACE_CLASSES = {'orbit.PillDrag': (orbit.PillDrag, ('follow',)), 'bump.Pusher': (bump.Pusher, ('move',)),
                 'restore_band.Dwell': (restore_band.Dwell, ('reset', 'restart', 'disarm', 'step')),
                 'free_pointer.TextHold': (free_pointer.TextHold, ('step', 'clear'))}
# The suites whose calls are recorded: the eight modules' own, and the pointer's and the panel's that drive them with
# the inputs a run really gives. Not their Sampler test classes: those drive the pointer's thread on the wall
# clock, so what they call depends on timing (and recording slows it past what they allow). Nor
# test_native_restore_acquisition: it runs only when XR_RESTORE_GEOMETRY_BINARY names a native build (as
# tests/run_all.sh sets it), so recording it would make the file depend on the build and the environment.
SUITES = ('test_orbit', 'test_bump', 'test_bump_gaps', 'test_edge_pan', 'test_free_pointer', 'test_unified_cursor',
          'test_pointer_zones', 'test_directional_switch', 'test_restore_pointer', 'test_glass_pills', 'test_roll_level',
          'test_grab_pill', 'test_free_pointer_focus', 'test_text_cursor', 'test_super_drag', 'test_window_drag',
          'test_overview_cursor', 'test_options_review', 'test_options_supervisor', 'test_resolution_dropdown',
          'test_scale_dropdown', 'test_help_pages', 'test_maximize_level', 'test_immersive', 'test_dot_click_focus',
          'test_panel_follows_focus', 'test_scroll_before_switch', 'test_environment')
# Tests that time the Python: recording slows it, so they may fail while recording (their calls are still kept).
TIMED = {'test_quick_enough_for_the_pointer_and_for_a_slider_jump', 'test_quick_enough_for_the_loop'}
# Tests outside a Sampler class that still run the pointer's thread on the wall clock: left out like those.
WALL_CLOCK = {'test_view_controls_hands_it_to_the_sampler'}
CAP, BUDGET = 60, 40000          # per function: distinct cases, bytes
TRACES, TRACE_CALLS, TRACE_BUDGET = 40, 30, 400000  # per class
EVERY = 101                      # past the first FIRST calls of a function, one call in EVERY is looked at
FIRST = 400


class Unencodable(Exception):
    pass


def encode(v):
    """A JSON-ready copy of `v`: tuples as lists, Footprint and Plan tagged."""
    if v is None or type(v) in (bool, int, str): return v
    if type(v) is float:
        if not math.isfinite(v): raise Unencodable('non-finite')
        return v
    if type(v) in (tuple, list): return [encode(x) for x in v]
    if type(v) is dict:
        if not all(type(k) is str for k in v): raise Unencodable('key')
        return {k: encode(x) for k, x in v.items()}
    if type(v) is orbit.Footprint:
        return {'$footprint': {'vertices': encode(v.vertices), 'normals': encode(v.normals), 'centre': encode(v.centre),
                               'radius': encode(v.radius), 'handles': encode(v.handles)}}
    if type(v) is edge_pan.Plan:
        return {'$plan': {k: encode(getattr(v, k)) for k in edge_pan.Plan.__slots__}}
    if type(v) is bump.Pusher:  # push()'s second result: what the later ports read of it
        return {'$pusher': encode(state('bump.Pusher', v))}
    if type(v) is bytes:  # frame_bytes(): the line is ASCII JSON
        return {'$bytes': v.decode('ascii')}
    raise Unencodable(type(v).__name__)


def decode(v):
    """encode()'s inverse, for the replay: lists stay lists (the modules index them like tuples)."""
    if type(v) is list: return [decode(x) for x in v]
    if type(v) is dict:
        if '$footprint' in v:
            f = v['$footprint']
            out = orbit.Footprint(None, decode(f['centre']), f['radius'], tuple(decode(f['handles'])))
            out.vertices, out.normals = decode(f['vertices']), decode(f['normals'])
            return out
        if '$plan' in v:
            out = object.__new__(edge_pan.Plan)
            for k, x in v['$plan'].items(): setattr(out, k, decode(x))
            return out
        return {k: decode(x) for k, x in v.items()}
    return v


def dump(v):
    return json.dumps(encode(v), separators=(',', ':'), allow_nan=False)


def state(name, obj):
    """The instance's observable state after a call, as the later ports read it."""
    if name == 'orbit.PillDrag':
        return {'at': obj.at, 'near': obj.near, 'local': obj.local}
    if name == 'bump.Pusher':
        return {'reached': obj.reached, 'pushed': obj.pushed, 'blocked': obj.blocked, 'steps': obj.steps,
                'memory': sorted([k, v] for k, v in obj.memory.items())}
    if name == 'free_pointer.TextHold':
        return {'on': obj.on, 'until': obj.until, 'left': obj.left, 'sweep': obj.sweep}
    return {'touched': obj.touched, 'anchor': obj.anchor, 'since': obj.since, 'armed': obj.armed}


def functions():
    """(qualified name, module, attribute) of every module-level function of the eight modules."""
    out = []
    for mod_name, mod in MODULES.items():
        for attr, v in vars(mod).items():
            target = getattr(v, '__wrapped__', v)
            if inspect.isfunction(target) and target.__module__ == mod.__name__:
                out.append((f'{mod_name}.{attr}', mod, attr))
    return sorted(out)


def methods():
    """Every qualified name a case or trace may have."""
    names = [n for n, _, _ in functions()]
    for cls_name, (_, meths) in CALL_CLASSES.items():
        names += [cls_name] + [f'{cls_name}.{m}' for m in meths]
    return sorted(names + list(TRACE_CLASSES))


# --- recording -----------------------------------------------------------------------------------------------------------
class Recorder:
    def __init__(self):
        self.calls = {}   # name -> {key: case}
        self.counts = {}
        self.traces = {name: [] for name in TRACE_CLASSES}
        self.skipped = {}
        self.depth = {}   # id(instance) -> outer method depth

    def skip(self, name, why):
        self.skipped.setdefault(name, {}).setdefault(why, 0)
        self.skipped[name][why] += 1

    def look(self, name):
        n = self.counts.get(name, 0)
        self.counts[name] = n + 1
        return n < FIRST or n % EVERY == 0

    def keep(self, name, args, kwargs, run):
        """Run the call and, if it is looked at, keep (args, kwargs, result)."""
        if not self.look(name): return run()
        try:
            a, k = encode(list(args)), encode(dict(kwargs))
        except Unencodable as e:
            self.skip(name, f'arguments: {e}')
            return run()
        try:
            result = run()
        except Exception as e:
            case = {'args': a, 'kwargs': k, 'raises': f'{type(e).__name__}: {e}'}
            self.calls.setdefault(name, {}).setdefault(json.dumps([a, k], separators=(',', ':')), case)
            raise
        try:
            out = dump(result)
        except (Unencodable, ValueError) as e:
            self.skip(name, f'result: {e}')
            return result
        self.calls.setdefault(name, {}).setdefault(json.dumps([a, k], separators=(',', ':')),
                                                   {'args': a, 'kwargs': k, 'out': out})
        return result

    def wrap_function(self, name, mod, attr):
        f = getattr(mod, attr)

        @functools.wraps(f)
        def wrapper(*args, **kwargs):
            return self.keep(name, args, kwargs, lambda: f(*args, **kwargs))
        for extra in ('cache_clear', 'cache_info'):  # (an lru_cache's: the tests clear it)
            if hasattr(f, extra): setattr(wrapper, extra, getattr(f, extra))
        setattr(mod, attr, wrapper)
        return lambda: setattr(mod, attr, f)

    def wrap_call_class(self, name, cls, meths):
        undo = []
        init = cls.__init__

        def new_init(obj, *args, **kwargs):
            # A constructor's case: its arguments and the instance it makes.
            if not self.look(name): return init(obj, *args, **kwargs)
            try:
                a, k = encode(list(args)), encode(dict(kwargs))
            except Unencodable as e:
                self.skip(name, f'arguments: {e}')
                return init(obj, *args, **kwargs)
            init(obj, *args, **kwargs)
            try:
                out = dump(obj)
            except (Unencodable, ValueError) as e:
                self.skip(name, f'result: {e}')
                return
            self.calls.setdefault(name, {}).setdefault(json.dumps([a, k], separators=(',', ':')),
                                                       {'args': a, 'kwargs': k, 'out': out})
        cls.__init__ = new_init
        undo.append(lambda: setattr(cls, '__init__', init))
        for m in meths:
            f = getattr(cls, m)

            def method(obj, *args, _f=f, _name=f'{name}.{m}', **kwargs):
                return self.keep(_name, (obj,) + args, kwargs, lambda: _f(obj, *args, **kwargs))
            setattr(cls, m, method)
            undo.append(lambda m=m, f=f: setattr(cls, m, f))
        return undo

    def wrap_trace_class(self, name, cls, meths):
        undo = []
        init = cls.__init__
        traces = self.traces[name]

        def outer(obj, run, entry):
            key = id(obj)
            self.depth[key] = self.depth.get(key, 0) + 1
            try:
                result = run()
            finally:
                self.depth[key] -= 1
            if self.depth[key] == 0 and entry is not None:
                trace = obj.__dict__.get('_corpus_trace')
                if trace is not None and not trace.get('dead'):
                    try:
                        entry['out'], entry['state'] = dump(result), dump(state(name, obj))
                        trace['calls'].append(entry)
                    except (Unencodable, ValueError) as e:
                        self.skip(name, f'result: {e}')
                        trace['dead'] = True  # the rest of it cannot be replayed without this call
            return result

        def new_init(obj, *args, **kwargs):
            trace = None
            try:
                trace = {'init': {'args': encode(list(args)), 'kwargs': encode(dict(kwargs))}, 'calls': []}
            except Unencodable as e:
                self.skip(name, f'arguments: {e}')
            result = outer(obj, lambda: init(obj, *args, **kwargs), None)
            obj.__dict__['_corpus_trace'] = trace
            if trace is not None: traces.append(trace)
            return result

        cls.__init__ = new_init
        undo.append(lambda: setattr(cls, '__init__', init))
        for m in meths:
            f = getattr(cls, m)

            def method(obj, *args, _f=f, _m=m, **kwargs):
                trace = obj.__dict__.get('_corpus_trace')
                entry = None
                if trace is not None and not trace.get('dead') and self.depth.get(id(obj), 0) == 0:
                    try:
                        entry = {'method': _m, 'args': encode(list(args)), 'kwargs': encode(dict(kwargs))}
                    except Unencodable as e:
                        self.skip(name, f'arguments: {e}')
                        trace['dead'] = True
                return outer(obj, lambda: _f(obj, *args, **kwargs), entry)
            setattr(cls, m, method)
            undo.append(lambda m=m, f=f: setattr(cls, m, f))
        return undo

    def install(self):
        undo = [self.wrap_function(*f) for f in functions()]
        for name, (cls, meths) in CALL_CLASSES.items(): undo += self.wrap_call_class(name, cls, meths)
        for name, (cls, meths) in TRACE_CLASSES.items(): undo += self.wrap_trace_class(name, cls, meths)
        return lambda: [u() for u in reversed(undo)]


def _flat(suite):
    for t in suite:
        if isinstance(t, unittest.TestSuite): yield from _flat(t)
        else: yield t


def record():
    """Run SUITES with every function and class of the eight modules recorded."""
    # Loaded (so imported) before the recording starts: what the suites' modules compute as they are imported is
    # left out, so the file is the same whether or not something imported them first (a unittest run of several).
    suite = unittest.TestSuite(t for t in _flat(unittest.defaultTestLoader.loadTestsFromNames(SUITES))
                               if 'Sampler' not in type(t).__name__ and t.id().split('.')[-1] not in WALL_CLOCK)
    rec = Recorder()
    uninstall = rec.install()
    try:
        result = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(suite)
    finally:
        uninstall()
    bad = [(t, tb) for t, tb in result.failures + result.errors if t.id().split('.')[-1] not in TIMED]
    if bad:
        for t, tb in bad: print(t, tb[-1500:], file=sys.stderr)
        raise SystemExit(f'the recorded suites failed: {len(bad)} failed')
    return rec


# --- replay ----------------------------------------------------------------------------------------------------------------
def resolve(name):
    """The real callable for a case's name."""
    for cls_name, (cls, meths) in CALL_CLASSES.items():
        if name == cls_name: return lambda *a, **k: cls(*a, **k)
        for m in meths:
            if name == f'{cls_name}.{m}': return getattr(cls, m)
    mod, attr = name.split('.', 1)
    return getattr(MODULES[mod], attr)


def replay_call(name, case):
    """The case's outcome through the current Python: {'out': bytes} or {'raises': text}."""
    args = decode(case['args'])
    if name == 'orbit._footprint': args = [_tuples(a) for a in args]  # (an lru_cache key: hashable)
    try:
        result = resolve(name)(*args, **decode(case['kwargs']))
    except Exception as e:
        return {'raises': f'{type(e).__name__}: {e}'}
    return {'out': dump(result)}


def _tuples(v):
    return tuple(_tuples(x) for x in v) if type(v) is list else v


def replay_trace(name, trace):
    """The trace's calls' (out, state) through the current Python."""
    cls = TRACE_CLASSES[name][0]
    obj = cls(*decode(trace['init']['args']), **decode(trace['init']['kwargs']))
    out = []
    for call in trace['calls']:
        result = getattr(obj, call['method'])(*decode(call['args']), **decode(call['kwargs']))
        out.append({'out': dump(result), 'state': dump(state(name, obj))})
    return out


def expected(case):
    return {'raises': case['raises']} if 'raises' in case else {'out': case['out']}


def _hash(text):
    return hashlib.sha256(text.encode()).hexdigest()


def select(rec):
    """The cases and traces to keep: replayable, at most CAP / BUDGET per function, chosen by hash."""
    cases, skipped = {}, {n: dict(w) for n, w in rec.skipped.items()}
    orbit._footprint.cache_clear()
    for name, by_key in rec.calls.items():
        good = []
        for key, case in by_key.items():
            if replay_call(name, case) != expected(case):
                skipped.setdefault(name, {}).setdefault('replay differs (a test patched it)', 0)
                skipped[name]['replay differs (a test patched it)'] += 1
                continue
            good.append((_hash(key), len(json.dumps(case)), case))
        good.sort(key=lambda t: t[0])
        kept, size = [], 0
        smallest = min(good, key=lambda t: (t[1], t[0]), default=None)
        if smallest is not None: kept, size = [smallest], smallest[1]
        for h, n, case in good:
            if len(kept) >= CAP: break
            if case is smallest[2] or size + n > BUDGET: continue
            kept.append((h, n, case))
            size += n
        cases[name] = [c for _, _, c in sorted(kept, key=lambda t: t[0])]
    traces = {}
    for name, all_traces in rec.traces.items():
        good = {}
        for t in all_traces:
            calls = t['calls']  # (a trace gone dead keeps the calls before it: they replay on their own)
            if not calls: continue
            t = {'init': t['init'], 'calls': calls[:TRACE_CALLS]}
            key = json.dumps(t, separators=(',', ':'))
            if key in good: continue
            want = [{'out': c['out'], 'state': c['state']} for c in t['calls']]
            try:
                same = replay_trace(name, t) == want
            except Exception:
                same = False
            if not same:
                skipped.setdefault(name, {}).setdefault('replay differs (a test patched it)', 0)
                skipped[name]['replay differs (a test patched it)'] += 1
                continue
            good[key] = t
        kept, size = [], 0
        for key in sorted(good, key=_hash):
            if len(kept) >= TRACES: break
            n = len(key)
            if kept and size + n > TRACE_BUDGET: continue
            kept.append(good[key])
            size += n
        traces[name] = sorted(kept, key=lambda t: _hash(json.dumps(t, separators=(',', ':'))))
    return cases, traces, skipped


def corpus():
    rec = record()
    cases, traces, skipped = select(rec)
    for name in methods():  # (the Rust port's own unit tests cover these)
        if not cases.get(name) and not traces.get(name) and name not in skipped:
            skipped[name] = {'no recorded suite calls it': 0}
    return {'schema': 1, 'cases': {k: cases[k] for k in sorted(cases)}, 'traces': {k: traces[k] for k in sorted(traces)},
            'skipped': {k: {w: n for w, n in sorted(skipped[k].items())} for k in sorted(skipped)}}


def dumps(value):
    return json.dumps(value, separators=(',', ':'), allow_nan=False) + '\n'


def main(argv):
    if argv != ['--write']:
        print('usage: pointer_math_corpus.py --write', file=sys.stderr)
        return 2
    data = corpus()
    CORPUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    CORPUS_FILE.write_text(dumps(data))
    counts = {k: len(v) for k, v in data['cases'].items()}
    counts.update({k: f'{len(v)} traces, {sum(len(t["calls"]) for t in v)} calls' for k, v in data['traces'].items()})
    for k in methods():
        print(f'{k}: {counts.get(k, 0)}')
    print('skipped:', json.dumps(data['skipped']))
    print(f'{CORPUS_FILE}: {CORPUS_FILE.stat().st_size} bytes')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
