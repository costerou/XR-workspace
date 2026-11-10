"""Phase 0 journal corpus and the Python side of the phase 6 cross-recovery gate (docs/rust-rebuild-plan.md).

The journal format is frozen so that either supervisor can recover a run the other started. This module holds the
Python half of that proof:

* rust/corpus/journal/cases.json, made only from the shipped Python (tools/native_spike/journal.py):
  - `scenarios`: a run's journal as the Python writes it, from a script of Journal calls (`steps`), together with the
    compositor it was left on. Each scenario carries `cases`: one per fault-injection point in its recovery (none, then
    each kind of fault at each backend call a clean recovery makes), with what Python recovery did (`first`) and what a
    clean retry did next on the journal and compositor it left (`retry`).
  - `refused`: journal files the Python refuses to open, with the reason.
  The Rust port (rust/crates/xr-workspace/tests/cross_recovery.rs) recovers each Python journal and must do exactly
  what the Python did: same result, same journal bytes, same compositor calls.
* `recover` (stdin to stdout, JSON): Python recovery of journals someone else wrote. The Rust test sends the journals the
  Rust writes, and checks Python recovers them exactly as the Rust does.
* `hold-owner PATH` / `try-owner PATH`: the run's owner lock (managed_owner), so the test can check the two
  supervisors exclude each other.

The compositor is a scripted stand-in (Compositor), the same in both languages. Nothing here talks to Hyprland.

Regenerate after a deliberate change to the journal or recovery code:

    TMPDIR=/tmp /usr/bin/python3 tests/native_spike/journal_corpus.py --write

test_journal_corpus.py fails when the committed file and the Python code disagree.
"""
import copy
import itertools
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))

from tools.native_spike import journal as journal_module  # noqa: E402
from tools.native_spike.journal import Journal, managed_owner, recover  # noqa: E402

CORPUS_FILE = ROOT / 'rust' / 'corpus' / 'journal' / 'cases.json'

RUN, SESSION = 'run-0001', 'session-0001'
OUT1, OUT2 = '0b6f8a52-6a6e-4f3c-9d55-1f0d3f9a0c01', '0b6f8a52-6a6e-4f3c-9d55-1f0d3f9a0c02'
INSTANCE = 'a' * 40 + '_1700000000_123'
BROKER = {'instance': INSTANCE, 'output': 'DP-2', 'recovery_output': 'DP-1', 'epoch': 'epoch-1', 'fallback': 'DP-1'}
ISOLATED = {'mode': 'isolated-vm', 'instance': INSTANCE, 'output': 'Virtual-2', 'recovery_output': 'Virtual-1',
            'epoch': 'epoch-7', 'fallback': 'f' * 48, 'target': 'e' * 48,
            'boot_id': '01234567-89ab-cdef-0123-456789abcdef', 'runtime': '/run/user/1000/xr-vm',
            'proof': {'target': {'name': 'Virtual-2', 'device': '226:1', 'connector': 41},
                      'fallback': {'name': 'Virtual-1', 'device': '226:1', 'connector': 40}}}

# Fault kinds, injected at one backend call (`at`, counted from 0 over every query, mutate and evacuate_remove):
#   error       that call fails (ValueError)
#   disconnect  that call and every later one fail (OSError), as when the compositor goes away
#   session     from that call on, the compositor reports another session, as after a restart
#   refused     that mutate answers something other than "ok"
#   ignored     that mutate answers "ok" but changes nothing; that evacuate_remove refuses
FAULTS = ('error', 'disconnect', 'session', 'refused', 'ignored')
MUTATING = {'refused': ('mutate',), 'ignored': ('mutate', 'evacuate_remove')}


class Fault(Exception): pass


class Compositor:
    """The normalized recovery view (journal.RecoveryBackend) of a scripted compositor, with one injected fault.
    `calls` lists every call made, in order, the failed ones included."""
    def __init__(self, state, fault=None):
        self.state = copy.deepcopy(state)
        self.fault = fault
        self.calls = []
        self.refusal = None

    def _call(self, kind, text):
        n = len(self.calls)
        self.calls.append(text)
        fault = self.fault
        if fault is None or n < fault['at']: return None
        if fault['kind'] == 'disconnect': raise OSError('compositor disconnected')
        if fault['kind'] == 'session': return 'session'
        if n != fault['at']: return None
        if fault['kind'] == 'error': raise ValueError('injected fault')
        return fault['kind'] if kind in MUTATING[fault['kind']] else None

    def query(self, kind):
        effect = self._call('query', 'query ' + kind)
        if kind == 'session':
            return {'id': 'other-session' if effect == 'session' else self.state['session']}
        return copy.deepcopy(self.state[kind])

    def mutate(self, command):
        effect = self._call('mutate', 'mutate ' + command)
        if effect == 'refused': return 'refused'
        if effect == 'ignored': return 'ok'
        fields = command.split()
        if fields[0] == 'keyword' and len(fields) == 3:
            self.state['settings'][fields[1]] = int(fields[2])
        elif fields[:2] == ['dispatch', 'moveworkspacetomonitor'] and len(fields) == 4:
            for ws in self.state['workspaces']:
                if ws['id'] == int(fields[2]): ws['output'] = fields[3]
        else:
            return 'unknown command'
        return 'ok'

    def evacuate_remove(self, output_id, output_name):
        self.refusal = None
        effect = self._call('evacuate_remove', f'evacuate_remove {output_id} {output_name}')
        if effect == 'ignored':
            self.refusal = 'injected refusal'
            return False
        outputs = self.state['outputs']
        found = next((o for o in outputs if o['id'] == output_id), None)
        if found is None: return True  # never created, or already gone
        if found['name'] != output_name or found['physical'] is not False:
            self.refusal = 'output name/UUID mismatch'
            return False
        fallback = next((o for o in outputs if o['physical'] is True), None)
        if fallback is None:
            self.refusal = 'no physical output can take its windows'
            return False
        for ws in self.state['workspaces']:
            if ws['output'] == output_name: ws['output'] = fallback['name']
        outputs.remove(found)
        return True


def _private_dir():
    path = Path(tempfile.mkdtemp(prefix='xr-journal-'))
    os.chmod(path, 0o700)
    return path


def _remove(path):
    for child in path.iterdir(): child.unlink()
    path.rmdir()


def _error(exc):
    """A refusal in words both languages share: what the strict JSON and UUID parsers say is library text."""
    text = str(exc) or type(exc).__name__
    if isinstance(exc, json.JSONDecodeError) or text == 'nonfinite number': return 'invalid JSON'
    if text.startswith('invalid literal for int() with base 16'): return 'badly formed hexadecimal UUID string'
    return text


def recover_one(request):
    """Python recovery of one journal file (`journal`, its text) for `run_id`/`session`, against `compositor` with
    `fault`: {'error'} when the journal does not open, else the result, the journal file after and the compositor."""
    directory = _private_dir()
    try:
        path = directory / 'journal.json'
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as stream: stream.write(request['journal'].encode())
        compositor = Compositor(request['compositor'], request['fault'])
        try:
            journal = Journal.open(path, request['run_id'], request['session'], existing_only=True)
            result = recover(journal, compositor)
        except (OSError, ValueError) as exc:
            return {'error': _error(exc), 'calls': compositor.calls}
        return {'complete': result.complete, 'remaining': result.remaining, 'reasons': result.reasons,
                'journal': path.read_text(), 'compositor': compositor.state, 'calls': compositor.calls}
    finally:
        _remove(directory)


def _operation(op):
    """A step's operation, its keys in the order the supervisor writes them (ownership.py for outputs)."""
    order = {'output': ('kind', 'id', 'name', 'index', 'before'),
             'setting': ('kind', 'key', 'before', 'applied'),
             'workspace': ('kind', 'workspace', 'before', 'applied')}[op['kind']]
    return {key: op[key] for key in order}


def build(steps, run_id=RUN, session=SESSION):
    """The journal file a run leaves after `steps`, written by the Python. A step with `error` must be refused with it."""
    directory = _private_dir()
    try:
        path = directory / 'journal.json'
        journal = Journal.open(path, run_id, session)
        ids = []
        for step in steps:
            try:
                if step['call'] == 'bind_broker': journal.bind_broker(step['broker'])
                elif step['call'] == 'prepare': ids.append(journal.prepare(_operation(step['operation'])))
                elif step['call'] == 'confirm': journal.confirm(ids[step['record']], step['observed'])
                elif step['call'] == 'output_removed': journal.output_removed(step['id'])
                elif step['call'] == 'compositor_ended': journal.compositor_ended()
                else: raise AssertionError(step)
            except ValueError as exc:
                if step.get('error') != str(exc): raise
            else:
                if 'error' in step: raise AssertionError(f'step not refused: {step}')
        return path.read_text()
    finally:
        _remove(directory)


def _output(index, oid):
    return {'kind': 'output', 'id': oid, 'name': f'XR-{index + 1}', 'index': index, 'before': None}


def _seen(index, oid):
    return {'id': oid, 'name': f'XR-{index + 1}', 'physical': False}


BIND = {'call': 'bind_broker', 'broker': BROKER}
PREP1, PREP2 = {'call': 'prepare', 'operation': _output(0, OUT1)}, {'call': 'prepare', 'operation': _output(1, OUT2)}
WS = {'call': 'prepare', 'operation': {'kind': 'workspace', 'workspace': 3, 'before': 'DP-1', 'applied': 'XR-1'}}
SET = {'call': 'prepare', 'operation': {'kind': 'setting', 'key': 'input:follow_mouse', 'before': 1, 'applied': 0}}


def _confirm(record, observed): return {'call': 'confirm', 'record': record, 'observed': observed}


DP1, DP2 = {'id': 'unowned:DP-1', 'name': 'DP-1', 'physical': True}, {'id': 'unowned:DP-2', 'name': 'DP-2', 'physical': False}
XR1, XR2 = {'id': OUT1, 'name': 'XR-1', 'physical': False}, {'id': OUT2, 'name': 'XR-2', 'physical': False}


def _compositor(outputs, workspaces=(), settings=None, session=SESSION):
    return {'session': session, 'outputs': list(outputs), 'workspaces': [dict(w) for w in workspaces],
            'settings': {'input:follow_mouse': 1} if settings is None else settings}


FULL_STEPS = [BIND, PREP1, _confirm(0, _seen(0, OUT1)), WS, _confirm(1, {'output': 'XR-1'}),
              SET, _confirm(2, {'value': 0}), PREP2, _confirm(3, _seen(1, OUT2))]
FULL = _compositor([DP1, DP2, XR1, XR2], [{'id': 1, 'output': 'DP-1'}, {'id': 3, 'output': 'XR-1'},
                                          {'id': 4, 'output': 'XR-2'}], {'input:follow_mouse': 0})

SCENARIOS = [
    # A run that ends before it changed anything.
    ('bound, nothing changed', [BIND], _compositor([DP1, DP2])),
    # Crash after the intent, before the output was made: nothing to remove.
    ('output intent, never created', [BIND, PREP1], _compositor([DP1, DP2])),
    # Crash after the output was made, before its readback was confirmed: it is still removed.
    ('output created, not confirmed', [BIND, PREP1], _compositor([DP1, DP2, XR1], [{'id': 2, 'output': 'XR-1'}])),
    ('output confirmed', [BIND, PREP1, _confirm(0, _seen(0, OUT1))],
     _compositor([DP1, DP2, XR1], [{'id': 1, 'output': 'DP-1'}, {'id': 2, 'output': 'XR-1'}])),
    # A desktop removed during the run is done; only the other one is undone.
    ('second desktop removed during the run',
     [BIND, PREP1, _confirm(0, _seen(0, OUT1)), PREP2, _confirm(1, _seen(1, OUT2)), {'call': 'output_removed', 'id': OUT2}],
     _compositor([DP1, DP2, XR1], [{'id': 2, 'output': 'XR-1'}])),
    # Every kind of record: undone in reverse order, outputs last.
    ('outputs, workspace move and setting', FULL_STEPS, FULL),
    # The person changed the workspace and the setting since: their change is kept.
    ('user changed workspace and setting', FULL_STEPS,
     _compositor([DP1, DP2, XR1, XR2], [{'id': 3, 'output': 'DP-2'}], {'input:follow_mouse': 2})),
    # Ambiguous: two workspaces with one id, the workspace's old monitor gone, the setting unreadable.
    ('ambiguous workspace', FULL_STEPS,
     _compositor([DP1, DP2, XR1, XR2], [{'id': 3, 'output': 'XR-1'}, {'id': 3, 'output': 'XR-1'}], {})),
    ('workspace monitor gone', FULL_STEPS,
     _compositor([DP2, XR1, XR2], [{'id': 3, 'output': 'XR-1'}], {'input:follow_mouse': 0})),
    # An output with the journal's name but another UUID is not this run's: kept.
    ('output name reused by another', [BIND, PREP1, _confirm(0, _seen(0, OUT1))],
     _compositor([DP1, DP2, {'id': OUT1, 'name': 'XR-7', 'physical': False}])),
    # The compositor restarted: another session, so nothing is touched and everything is kept.
    ('other session', FULL_STEPS, dict(FULL, session='session-0002')),
    # The compositor exited during the run: every record is done.
    ('compositor ended', FULL_STEPS + [{'call': 'compositor_ended'}], _compositor([DP1, DP2])),
    # An isolated-VM broker scope, and the refusals of the journal's own calls.
    ('isolated broker, refused calls',
     [{'call': 'bind_broker', 'broker': ISOLATED}, {'call': 'bind_broker', 'broker': BROKER,
                                                    'error': 'cannot rebind existing journal authority'},
      PREP1, dict(_confirm(0, {'id': OUT1, 'name': 'XR-1', 'physical': True}), error='mutation not observed'),
      {'call': 'output_removed', 'id': OUT1, 'error': 'output removal before its creation was confirmed'},
      {'call': 'output_removed', 'id': OUT2, 'error': 'unknown output'},
      _confirm(0, _seen(0, OUT1)), _confirm(0, _seen(0, OUT1)),
      {'call': 'prepare', 'operation': {'kind': 'output', 'id': OUT2, 'name': 'XR-3', 'index': 1, 'before': None},
       'error': 'output name does not match journal index'},
      {'call': 'prepare', 'operation': {'kind': 'setting', 'key': 'input:follow_mouse', 'before': 1, 'applied': 4},
       'error': 'invalid setting value'}],
     _compositor([{'id': 'unowned:Virtual-1', 'name': 'Virtual-1', 'physical': True}, XR1],
                 [{'id': 5, 'output': 'XR-1'}])),
    # A journal from before broker binding existed has no broker key.
    ('no broker', [PREP1, _confirm(0, _seen(0, OUT1))], _compositor([DP1, XR1])),
    # No physical output can take the windows: the output stays, and says why.
    ('no fallback', [BIND, PREP1, _confirm(0, _seen(0, OUT1))], _compositor([DP2, XR1], [{'id': 2, 'output': 'XR-1'}])),
]


def _uuids():
    """Fixed record ids, so the committed corpus is the same each time it is made."""
    counter = itertools.count(1)
    return lambda: uuid.UUID(int=(0x5eed << 112) | (4 << 76) | (2 << 62) | next(counter))


def _faults(calls):
    """Every fault-injection point of a recovery that made `calls`: none, then each kind at each call it applies to."""
    points = [None]
    for at, call in enumerate(calls):
        kind_of_call = call.split(' ', 1)[0]
        for kind in FAULTS:
            if kind in MUTATING and kind_of_call not in MUTATING[kind]: continue
            points.append({'at': at, 'kind': kind})
    return points


def scenario_cases(journal_text, compositor, run_id=RUN, session=SESSION):
    def request(text, state, fault):
        return {'journal': text, 'run_id': run_id, 'session': session, 'compositor': state, 'fault': fault}
    clean = recover_one(request(journal_text, compositor, None))
    cases = []
    for fault in _faults(clean['calls']):
        first = recover_one(request(journal_text, compositor, fault))
        retry = recover_one(request(first['journal'], first['compositor'], None)) if 'journal' in first else None
        cases.append({'fault': fault, 'first': first, 'retry': retry})
    return cases


def _journal_text(operations, *, broker=BROKER, run_id=RUN, session=SESSION, schema=1, extra=None):
    data = {'schema': schema, 'run_id': run_id, 'session': session, 'operations': operations}
    if broker is not None: data['broker'] = broker
    if extra: data.update(extra)
    return json.dumps(data)


def _record(op, state='confirmed', rid='5eed0000-0000-4000-8000-000000000001'):
    return {'id': rid, 'operation': op, 'state': state}


GOOD = _output(0, OUT1)
REFUSED = [
    ('not JSON', '{"schema": 1,'),
    ('NaN', '{"schema": NaN, "run_id": "run-0001", "session": "session-0001", "operations": []}'),
    ('duplicate key', '{"schema": 1, "schema": 1, "run_id": "run-0001", "session": "session-0001", "operations": []}'),
    ('a list', '[]'),
    ('extra key', _journal_text([], extra={'note': 'x'})),
    ('schema 2', _journal_text([], schema=2)),
    ('schema true', _journal_text([], schema=True)),
    ('schema 1.0', _journal_text([]).replace('"schema": 1', '"schema": 1.0')),
    ('another run', _journal_text([], run_id='run-0002')),
    ('another session', _journal_text([], session='session-0002')),
    ('operations not a list', _journal_text({})),
    ('record not a dict', _journal_text([1])),
    ('record extra key', _journal_text([dict(_record(GOOD), note=1)])),
    ('record id upper case', _journal_text([_record(GOOD, rid='5EED0000-0000-4000-8000-000000000001')])),
    ('record id malformed', _journal_text([_record(GOOD, rid='5eed')])),
    ('record id with braces', _journal_text([_record(GOOD, rid='{5eed0000-0000-4000-8000-000000000001}')])),
    ('record id not a string', _journal_text([_record(GOOD, rid=7)])),
    ('duplicate record id', _journal_text([_record(GOOD), _record(_output(1, OUT2))])),
    ('unknown state', _journal_text([_record(GOOD, state='undone')])),
    ('operation not a dict', _journal_text([_record([])])),
    ('unknown kind', _journal_text([_record({'kind': 'monitor', 'name': 'DP-1'})])),
    ('output extra key', _journal_text([_record(dict(GOOD, physical=False))])),
    ('output id malformed', _journal_text([_record(dict(GOOD, id='XR-1'))])),
    ('output id upper case', _journal_text([_record(dict(GOOD, id=OUT1.upper()))])),
    ('output before set', _journal_text([_record(dict(GOOD, before='DP-1'))])),
    ('output index negative', _journal_text([_record(dict(GOOD, index=-1))])),
    ('output index bool', _journal_text([_record(dict(GOOD, index=False))])),
    ('output name bad token', _journal_text([_record(dict(GOOD, name='XR 1'))])),
    ('output name does not match', _journal_text([_record(dict(GOOD, name='XR-2'))])),
    ('setting unknown key', _journal_text([_record({'kind': 'setting', 'key': 'general:gaps_in', 'before': 1, 'applied': 0})])),
    ('setting out of range', _journal_text([_record({'kind': 'setting', 'key': 'input:follow_mouse', 'before': 1, 'applied': 9})])),
    ('workspace zero', _journal_text([_record({'kind': 'workspace', 'workspace': 0, 'before': 'DP-1', 'applied': 'XR-1'})])),
    ('workspace too big', _journal_text([_record({'kind': 'workspace', 'workspace': 2147483648, 'before': 'DP-1', 'applied': 'XR-1'})])),
    ('workspace bad monitor', _journal_text([_record({'kind': 'workspace', 'workspace': 1, 'before': '', 'applied': 'XR-1'})])),
    ('broker missing key', _journal_text([], broker={k: v for k, v in BROKER.items() if k != 'epoch'})),
    ('broker bad token', _journal_text([], broker=dict(BROKER, epoch='a b'))),
    ('broker bad instance', _journal_text([], broker=dict(BROKER, instance='abc'))),
    ('broker outputs alias', _journal_text([], broker=dict(BROKER, recovery_output='DP-2'))),
    ('isolated bad mode', _journal_text([], broker=dict(ISOLATED, mode='vm'))),
    ('isolated bad handle', _journal_text([], broker=dict(ISOLATED, target='E' * 48))),
    ('isolated handles alias', _journal_text([], broker=dict(ISOLATED, target='f' * 48))),
    ('isolated bad boot id', _journal_text([], broker=dict(ISOLATED, boot_id='x'))),
    ('isolated relative runtime', _journal_text([], broker=dict(ISOLATED, runtime='run/xr'))),
    ('isolated proof name', _journal_text([], broker=dict(ISOLATED, proof=dict(ISOLATED['proof'], target=dict(ISOLATED['proof']['target'], name='Virtual-3'))))),
    ('isolated proof device', _journal_text([], broker=dict(ISOLATED, proof=dict(ISOLATED['proof'], target=dict(ISOLATED['proof']['target'], device='0:1'))))),
    ('isolated proof connector', _journal_text([], broker=dict(ISOLATED, proof=dict(ISOLATED['proof'], target=dict(ISOLATED['proof']['target'], connector=0))))),
    ('isolated proof alias', _journal_text([], broker=dict(ISOLATED, proof=dict(ISOLATED['proof'], target=dict(ISOLATED['proof']['target'], connector=40))))),
]
# Accepted oddities the Rust must also accept: a UTF-8 byte order mark, and records whose keys come in another order
# (kept in that order when the journal is written back).
ACCEPTED = [
    ('byte order mark', '﻿' + _journal_text([_record(GOOD)])),
    ('keys in another order', '{"operations": [{"state": "pending", "operation": {"before": null, "index": 0, "name": "XR-1", '
     f'"id": "{OUT1}", "kind": "output"}}, "id": "5eed0000-0000-4000-8000-000000000001"}}], "session": "session-0001", '
     '"run_id": "run-0001", "schema": 1}'),
]


def make():
    scenarios = []
    with patch.object(journal_module.uuid, 'uuid4', _uuids()):
        for name, steps, compositor in SCENARIOS:
            text = build(steps)
            scenarios.append({'name': name, 'run_id': RUN, 'session': SESSION, 'steps': steps,
                              'compositor': compositor, 'journal': text, 'cases': scenario_cases(text, compositor)})
        for name, text in ACCEPTED:
            compositor = _compositor([DP1, XR1])
            scenarios.append({'name': name, 'run_id': RUN, 'session': SESSION, 'steps': None, 'compositor': compositor,
                              'journal': text, 'cases': scenario_cases(text, compositor)})
    refused = []
    for name, text in REFUSED:
        outcome = recover_one({'journal': text, 'run_id': RUN, 'session': SESSION, 'compositor': _compositor([DP1]), 'fault': None})
        if 'error' not in outcome: raise AssertionError(f'{name}: not refused')
        refused.append({'name': name, 'run_id': RUN, 'session': SESSION, 'journal': text, 'error': outcome['error']})
    return {'scenarios': scenarios, 'refused': refused}


def dump(corpus):
    return json.dumps(corpus, indent=1, sort_keys=False) + '\n'


def main(argv):
    if argv == ['--write']:
        CORPUS_FILE.parent.mkdir(parents=True, exist_ok=True)
        CORPUS_FILE.write_text(dump(make()))
        return 0
    if argv == ['recover']:
        json.dump([recover_one(request) for request in json.load(sys.stdin)], sys.stdout)
        return 0
    if len(argv) == 2 and argv[0] == 'hold-owner':
        with managed_owner(argv[1]):
            print('held', flush=True)
            sys.stdin.read()
        return 0
    if len(argv) == 2 and argv[0] == 'try-owner':
        try:
            with managed_owner(argv[1]): print('acquired')
        except BlockingIOError:
            print('busy')
        return 0
    print('usage: journal_corpus.py --write | recover | hold-owner PATH | try-owner PATH', file=sys.stderr)
    return 2


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
