"""Phase 6 corpus for the Rust rebuild: the order of a managed run's steps (docs/rust-rebuild-plan.md, phase 6).

One golden file, rust/corpus/supervisor/run_order.json, made only by this module from the shipped managed.py. Each case
runs one of managed.py's entry points (run_managed, recover_managed, recover_ended) with every step it takes replaced
by a recorder, and keeps the ordered list of steps it took and what it returned or raised. The Rust port
(xr-workspace's managed module) replays each case against the same recorder and must give the same list.

The orchestration itself is managed.py's real code: _run_managed_impl, _run_owned, recover_restoring and the recovery
entry points, with ManagedGuard. What is replaced is what each step does to the compositor, the broker or the disk.
Steps that never raise in the shipped code (privacy.wake, immersive.restore_2d, monitor_rules, restore.unpin,
restore.restore, restore.take, send.notify, _left_on) are only recorded. Every other step is also made to fail, once
per place it is taken, so each failure point has its own case: what ran before it, what recovery ran after it, and
the error that came out.

Regenerate (after a deliberate change to managed.py's order) with:

    TMPDIR=/tmp /usr/bin/python3 tests/native_spike/run_order_corpus.py --write

test_run_order_corpus.py fails when the file and the Python disagree.
"""
from contextlib import contextmanager, ExitStack
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))

from tools.native_spike import (adopt, desktops, immersive, ipc, managed, monitor_rules, privacy, restore,  # noqa: E402
                                sbs, send, window_memory)
from tools.native_spike.journal import RecoveryResult  # noqa: E402
from tools.native_spike.supervisor import CAPTURE_SCOPE  # noqa: E402

CORPUS = ROOT / 'rust' / 'corpus' / 'supervisor'
RUN_ORDER_FILE = CORPUS / 'run_order.json'

RUNTIME = Path('/run/user/1000')
RUN_ID, SESSION, INSTANCE = 'run-1', 's1', 'inst'
OUTPUT, FALLBACK = 'DP-1', 'HDMI-A-1'
SPECS = [{'id': 'u1', 'name': 'XR-1'}, {'id': 'u2', 'name': 'XR-2'}]
CANONICAL = RUNTIME / 'xr-workspace-spike' / RUN_ID / 'journal.json'
TIMEOUT_TEXT = 'Head tracking did not come back, so XR stopped. Your windows are back on your monitor.'

# Every knob a case may set; a case's config is these with its own values over them. Both the recorder here and the
# Rust test's recorder read the same config.
DEFAULTS = {
    'model': 'luma-ultra',
    'capture': False,          # a capture (panel) run: adoption, the desktop pills, the renderer's capture names
    'memory': False,           # window memory for the run's desktops (a store was given)
    'take': False,             # the pre-XR layout snapshot could be read at start
    'saved': False,            # a layout snapshot is saved in the state directory (restore.load finds it)
    'saved_setup': False,      # the desktops are a saved setup, which goes back to default if it cannot be created
    'fresh': True,             # the journal is new (no operations, no broker)
    'complete': True,          # what the journal's recovery returns
    'remaining': [],
    'reasons': [],
    'cleared': [],             # monitor_rules.clear's reasons
    'named': [],               # monitor_rules.named's reasons
    'left_on': [],             # _left_on's reasons
    'stop_reason': None,       # why the supervisor ended the renderer
    'output_names': ['XR-1', 'XR-2'],  # the journal's outputs, for recovery
    'clear_rules': [],         # desktops whose monitor rule a person agreed to have taken out
    'canonical': True,         # the journal path given to recovery is the canonical one
    'saved_scope': 'match',    # the journal's saved broker scope: match, other (a different output) or none
    'fail': [],                # [[step, occurrence], ...]: each raises OSError('<step> failed') its n-th time (from 0)
}

# Steps whose shipped code can raise: each gets a failure case per occurrence.
FALLIBLE = {
    'session.verify', 'pair.validate', 'outputs.plan', 'broker.open', 'broker.from_journal', 'broker.require_ownership',
    'private_dir', 'owner.lock', 'journal.open', 'journal.read_session', 'journal.check_fresh', 'journal.bind_broker',
    'outputs.create', 'setup.discard_unusable', 'desktops.manager', 'broker.verify', 'window_memory.open',
    'window_memory.place', 'adopt', 'window_memory.finish', 'broker.cursor_reader', 'view_controls.enter',
    'view_controls.exit', 'continuity.open', 'continuity.close', 'renderer.keep', 'journal.output_names', 'layout.load',
    'journal.recover', 'layout.discard', 'journal.compositor_ended',
}


def _line(name, *parts):
    return ' '.join([name, *[str(p) for p in parts if p not in (None, '')]])


class Recorder:
    def __init__(self, config):
        self.config, self.trace, self.seen = config, [], {}

    def step(self, name, *parts):
        self.trace.append(_line(name, *parts))
        count = self.seen.get(name, 0)
        self.seen[name] = count + 1
        if name in FALLIBLE and [name, count] in self.config['fail']: raise OSError(f'{name} failed')

    def note(self, name, *parts):
        """A step that never raises."""
        self.trace.append(_line(name, *parts))


def _fakes(rec):
    """Every step managed.py takes, recorded: (target, attribute, replacement) for patch.object."""
    cfg = rec.config

    class Client:
        instance, runtime_dir = INSTANCE, RUNTIME
        def verified_session(self):
            rec.step('session.verify')
            return {'id': SESSION}
        def query(self, kind): return []
        def adopt_windows(self, *a, **k): raise AssertionError('adopt.adopt is recorded instead')
        @contextmanager
        def connect_wayland(self, session): yield None

    class Broker:
        epoch, fallback = 1, 'fb'
        def __init__(self, client, run_id, session, fallback):
            rec.step('broker.open')
            self.transport, self.session, self.approved_physical = client, session, fallback
        @classmethod
        def from_journal(cls, client, journal):
            rec.step('broker.from_journal')
            broker = cls.__new__(cls)
            broker.transport, broker.session, broker.approved_physical = client, journal.session, FALLBACK
            return broker
        def require_output_ownership(self): rec.step('broker.require_ownership')
        def cursor_reader(self): rec.step('broker.cursor_reader')
        def inspect(self, output_id): return output_id
        def verify(self, inspected, spec): rec.step('broker.verify', spec['name'])

    def scope():
        if cfg['saved_scope'] == 'none': return None
        return {'instance': INSTANCE, 'output': OUTPUT if cfg['saved_scope'] == 'match' else 'DP-9',
                'recovery_output': FALLBACK}

    class Journal:
        path = CANONICAL
        @classmethod
        def open(cls, path, run_id, session, *, existing_only=False):
            rec.step('journal.open', 'existing' if existing_only else None)
            journal = cls()
            journal.run_id, journal.session = run_id, session
            journal.operations = [] if cfg['fresh'] else [{'id': 'op-1'}]
            journal.broker = scope() if existing_only else None
            return journal
        @contextmanager
        def transaction(self):
            rec.step('journal.check_fresh')
            yield
        def bind_broker(self, context):
            rec.step('journal.bind_broker')
            self.broker = context
        def output_names(self):
            rec.step('journal.output_names')
            return list(cfg['output_names'])
        def compositor_ended(self): rec.step('journal.compositor_ended')

    @contextmanager
    def managed_owner(path):
        rec.step('owner.lock')
        try: yield
        finally: rec.note('owner.unlock')

    def private_dir(directory, create=False):
        rec.step('private_dir', Path(directory).relative_to(RUNTIME))
        return os.open('/dev/null', os.O_RDONLY)

    def validate_physical_pair(client, session, output, fallback, recovery_asleep_ok=False):
        rec.step('pair.validate', output, fallback, 'asleep-ok' if recovery_asleep_ok else None)

    def plan_outputs(monitors, workspaces, run_id, wanted):
        rec.step('outputs.plan')
        return [dict(s) for s in SPECS]

    def create_outputs(specs, journal, broker): rec.step('outputs.create', *[s['name'] for s in specs])

    def setup_failed(store, wanted):
        if not cfg['saved_setup']: return None
        return lambda exc: rec.step('setup.discard_unusable')

    class Manager:
        def __init__(self, broker, journal, specs, guard_factory=None, windows=None):
            rec.step('desktops.manager')
            self.specs, self.factory = specs, guard_factory
        def guard(self):
            guard = self.factory(self.specs)
            guard.from_desktops = True
            return guard

    class Memory:
        def place(self, names): rec.step('window_memory.place', *names)
        def finish(self): rec.step('window_memory.finish')

    def tracker_for(client, store, fallback, names):
        rec.step('window_memory.open')
        return Memory() if cfg['memory'] else None

    class Control:
        immersive_run = depth_memory = None

    @contextmanager
    def view_controls(*args, **kwargs):
        rec.step('view_controls.enter')
        try: yield Control()
        finally: rec.step('view_controls.exit')

    def keep_renderer(launch, client, output, report, control, restore_output, holds, **kwargs):
        rec.step('renderer.keep')
        launch()
        if cfg['stop_reason']: report['stop_reason'] = cfg['stop_reason']
        return 0

    def launch(client, run_id, session, output, wayland, plugin, *, guard, capture, **kwargs):
        rec.note('renderer.launch', 'guard=' + ('desktops' if getattr(guard, 'from_desktops', False) else 'base'),
                 'capture=' + (' '.join(capture) if capture else 'none'))

    continuity = []
    real_close = os.close

    def memfd_create(name, flags=0):
        rec.step('continuity.open')
        continuity.append(os.open('/dev/null', os.O_RDONLY))
        return continuity[-1]

    def close(fd):
        real_close(fd)
        if fd in continuity: rec.step('continuity.close')

    def recover(journal, broker):
        rec.step('journal.recover')
        return RecoveryResult(cfg['complete'], list(cfg['remaining']), list(cfg['reasons']))

    def load(run_id, layouts=None):
        rec.step('layout.load')
        return {'snapshot': 'saved'} if cfg['saved'] else None

    def take(client, fallback, run_id, layouts=None):
        rec.note('layout.take')
        return {'snapshot': 'taken'} if cfg['take'] else None

    def read_private_json(path, *a, **k):
        rec.step('journal.read_session')
        return {'session': SESSION}

    def joined(name, names): return _line(name, *names)

    return [
        (managed, 'OwnershipBrokerClient', Broker), (managed, 'Journal', Journal), (managed, 'managed_owner', managed_owner),
        (managed, 'private_dir', private_dir), (managed, 'validate_physical_pair', validate_physical_pair),
        (managed, 'plan_outputs', plan_outputs), (managed, 'monitors', lambda raw: raw),
        (managed, 'create_outputs', create_outputs), (managed, '_setup_failed', setup_failed),
        (managed, '_restore_output', lambda client, consent, output: None),
        (managed, 'view_controls', view_controls), (managed, 'keep_renderer', keep_renderer), (managed, '_launch', launch),
        (managed, 'recover', recover),
        (managed, '_left_on', lambda client, names: (rec.note(joined('left_on', names)), list(cfg['left_on']))[1]),
        (desktops, 'DesktopManager', Manager), (window_memory, 'tracker_for', tracker_for),
        (adopt, 'adopt', lambda client, fallback, names, notify=None: rec.step('adopt', *names)),
        (os, 'memfd_create', memfd_create), (os, 'close', close),
        (privacy, 'wake', lambda client, name, deadline=2.0: rec.note('privacy.wake', name)),
        (sbs, 'ButtonSbs', lambda client, output, notify: output),
        (immersive, 'restore_2d', lambda target=None: rec.note('sbs.restore_2d', target)),
        (monitor_rules, 'clear', lambda client, names, notify=None: (rec.note(joined('monitor_rules.clear', names)),
                                                                      list(cfg['cleared']))[1]),
        (monitor_rules, 'named', lambda client, names: (rec.note(joined('monitor_rules.named', names)),
                                                         list(cfg['named']))[1]),
        (restore, 'unpin', lambda client: rec.note('layout.unpin')), (restore, 'load', load), (restore, 'take', take),
        (restore, 'restore', lambda client, snapshot, notify=None: rec.note('layout.restore', snapshot['snapshot'])),
        (restore, 'discard', lambda run_id, layouts=None: rec.step('layout.discard')),
        (send, 'notify', lambda text, *a, **k: rec.note('notify', text)),
        (ipc, 'read_private_json', read_private_json),
    ], Client()


def _consent(cfg):
    consent = {'run_id': RUN_ID, 'model': cfg['model'], 'instance': INSTANCE, 'output': OUTPUT,
               'recovery_output': FALLBACK}
    if cfg['capture']: consent['capture'] = CAPTURE_SCOPE
    return consent


def run_case(entry, config):
    """One case: the steps `entry` took with `config`, and its result (the fields Rust compares) or its error."""
    cfg = dict(DEFAULTS, **config)
    rec = Recorder(cfg)
    fakes, client = _fakes(rec)
    path = CANONICAL if cfg['canonical'] else RUNTIME / 'elsewhere' / 'journal.json'
    with ExitStack() as stack:
        for target, name, value in fakes: stack.enter_context(patch.object(target, name, value))
        try:
            if entry == 'run':
                out = managed.run_managed(client, _consent(cfg), RUNTIME, store=object() if cfg['memory'] else None)
                result = {'renderer_returncode': out['renderer_returncode'], 'stop_reason': out.get('stop_reason')}
            elif entry == 'recover':
                out = managed.recover_managed(client, _consent(cfg), path, clear_rules=tuple(cfg['clear_rules']))
                result = {}
            elif entry == 'ended':
                out = managed.recover_ended(RUNTIME, _consent(cfg), path)
                result = {}
            else: raise ValueError(entry)
            outcome = {'ok': dict(result, complete=out['complete'], remaining=out['remaining'], reasons=out['reasons'])}
        except Exception as exc:  # noqa: BLE001 - the corpus records whatever came out
            outcome = {'error': str(exc)}
    return rec.trace, rec.seen, outcome


# The scenarios: each entry point's paths through managed.py. Each one's failure cases come from its clean trace.
SCENARIOS = (
    ('run-plain', 'run', {}),
    ('run-capture', 'run', {'capture': True, 'memory': True, 'take': True, 'cleared': ['rule left for XR-1']}),
    ('run-capture-no-snapshot', 'run', {'capture': True, 'saved': True}),
    ('run-incomplete', 'run', {'complete': False, 'remaining': ['op-2'], 'reasons': ['XR-2: busy'],
                               'named': ['rule names XR-2'], 'left_on': ['kitty is still on XR-2, workspace 7']}),
    ('run-tracking-timeout', 'run', {'stop_reason': 'tracking-timeout'}),
    ('run-other-stop-reason', 'run', {'stop_reason': 'heartbeat'}),
    ('run-saved-setup', 'run', {'saved_setup': True}),
    ('run-saved-setup-uncreatable', 'run', {'saved_setup': True, 'fail': [['outputs.create', 0]]}),
    ('run-existing-journal', 'run', {'fresh': False}),
    ('run-pro2', 'run', {'model': 'pro2'}),
    ('recover', 'recover', {'clear_rules': ['XR-1'], 'saved': True, 'cleared': ['rule left for XR-1'],
                            'named': ['rule names XR-2']}),
    ('recover-incomplete', 'recover', {'complete': False, 'remaining': ['op-1'], 'reasons': ['XR-1: busy'],
                                       'left_on': ['kitty is still on XR-1, workspace 3']}),
    ('recover-other-scope', 'recover', {'saved_scope': 'other'}),
    ('recover-not-canonical', 'recover', {'canonical': False}),
    ('recover-pro2', 'recover', {'model': 'pro2'}),
    ('ended', 'ended', {}),
    ('ended-other-scope', 'ended', {'saved_scope': 'other'}),
    ('ended-no-scope', 'ended', {'saved_scope': 'none'}),
    ('ended-not-canonical', 'ended', {'canonical': False}),
)


def run_order_corpus():
    cases = []
    for name, entry, config in SCENARIOS:
        trace, seen, outcome = run_case(entry, config)
        cases.append({'name': name, 'entry': entry, 'config': config, 'trace': trace, 'outcome': outcome})
        for step in sorted(s for s in seen if s in FALLIBLE):
            for n in range(seen[step]):
                if [step, n] in config.get('fail', []): continue
                failing = dict(config, fail=[*config.get('fail', []), [step, n]])
                trace, _, outcome = run_case(entry, failing)
                cases.append({'name': f'{name}/fail {step} #{n}', 'entry': entry, 'config': failing, 'trace': trace,
                              'outcome': outcome})
    return {'about': 'Ordered steps of managed.py runs and recoveries, made by tests/native_spike/run_order_corpus.py.',
            'defaults': DEFAULTS, 'fallible': sorted(FALLIBLE), 'cases': cases}


def dumps(value):
    return json.dumps(value, indent=1, ensure_ascii=False) + '\n'


def main(argv):
    if argv not in ([], ['--write']): raise SystemExit('usage: run_order_corpus.py [--write]')
    text = dumps(run_order_corpus())
    if argv == ['--write']:
        CORPUS.mkdir(parents=True, exist_ok=True)
        RUN_ORDER_FILE.write_text(text)
        print('wrote', RUN_ORDER_FILE.relative_to(ROOT))
        return 0
    stale = not RUN_ORDER_FILE.exists() or RUN_ORDER_FILE.read_text() != text
    if stale: print('stale:', RUN_ORDER_FILE.relative_to(ROOT))
    return 1 if stale else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
