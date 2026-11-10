"""The phase 0 corpus of a run's files on disk, for the Rust rebuild (docs/rust-rebuild-plan.md, phases 0, 1 and 6).

Every file is written by the shipped Python writers themselves, never by hand: the journal by `Journal`, the consent by
`write_consent`, `.progress.json` and `.result.json` by `runner._write_result`, `.recovery.json` by
`__main__.report_recovery`, and each recovery's reasons by `journal.recover` against a fake compositor. So the bytes are the
bytes a live run leaves, and a Rust reader or writer can be held to them exactly.

    TMPDIR=/tmp /usr/bin/python3 -m tests.native_spike.state_file_corpus   # rewrite tests/native_spike/fixtures/state_files

test_state_file_corpus.py fails when the checked-in corpus differs from what the writers produce today, so a change to a
format shows up as a fixture diff in review. The format is frozen by the plan: such a diff needs a decision, not a rewrite.

Layout, one folder per scenario under `runs/` (the state directory and the run's runtime folder, flattened):
  <run_id>.json            the consent (state dir)
  <run_id>.progress.json   the phase a run in flight last reached (state dir; removed once the result is written)
  <run_id>.result.json     how the run ended (state dir), `invalid.result.json` when the consent named no run
  <run_id>.recovery.json   why the last Recover did not finish (state dir; removed by one that finishes)
  journal.json             the write-ahead journal (runtime/xr-workspace-spike/<run_id>/journal.json)
  journal.before.json      not a live file: the journal as the Recover in this scenario found it
`consents/` holds one consent per supported scope. `pins/` holds the installer's pin files, one folder per install
(the data directory's viture-workspace/ folder): `installed.json` and `quiet-driver.pin.json` by `install.install` from a stand-in
build folder, `pro2-bundle.pin.json` by `pro2_route.write_pin`. The install runs in a temporary data directory, and its path in
the pins is then replaced by `/home/user/.local/share`, so the bytes are the same on every machine. `index.json` names what
each scenario covers.
"""
from contextlib import ExitStack, contextmanager
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
from unittest import mock
import uuid

from tools.native_spike import faults as run_faults, journal as journal_mod, sbs
from tools.native_spike.hyprland import OwnershipBlocked
from tools.native_spike.journal import Journal, recover
from tools.native_spike.ownership import REMOVAL_STEPS, OwnershipBrokerClient
from tools.native_spike.panel_consent import build_consent, write_consent
from tools.xr_controller import __main__ as controller, install, pro2_route
from tools.xr_controller.runner import _write_result

ROOT = Path(__file__).resolve().parent / 'fixtures' / 'state_files'
AT = 1762790400.0  # the `at` of every .recovery.json: 2025-11-10T16:00:00Z
INSTANCE = '0123456789abcdef0123456789abcdef01234567_1700000000_4242'
SESSION = '5e' * 32  # a journal's `session`: HyprlandClient.verified_session, sha256 of boot id, instance and peer
LEGACY_SESSION = '1e' * 32
SHA = 'ab' * 32
PHYSICAL, TARGET = 'DP-1', 'DP-2'
BROKER = {'instance': INSTANCE, 'output': TARGET, 'recovery_output': PHYSICAL, 'epoch': 'epoch-1', 'fallback': 'fallback-1'}
DESKTOP = {'width': 1920, 'height': 1080, 'scale': 1.0}
LUMA_MODE = {'width': 1920, 'height': 1200, 'refresh_hz': 120}
FOLLOW = 'input:follow_mouse'


def output_id(index):
    return f'00000000-0000-4000-8000-{index + 1:012d}'


def output_op(index):
    return {'kind': 'output', 'id': output_id(index), 'name': f'XR-{index + 1}', 'index': index, 'before': None}


def scope(run_id, **changes):
    base = {'run_id': run_id, 'instance': INSTANCE, 'output': TARGET, 'recovery_output': PHYSICAL, 'model': 'luma-ultra',
            'tracking': 'xrlinuxdriver', 'mode_switch': dict(LUMA_MODE),
            'plugin': {'path': '/home/user/.local/share/xr-workspace/plugin/' + SHA + '/libxr-ownership.so', 'sha256': SHA},
            'renderer': {'path': '/home/user/.local/share/xr-workspace/renderer/' + SHA + '/xr-native-spike', 'sha256': SHA},
            'desktops': [dict(DESKTOP) for _ in range(3)]}
    base.update(changes)
    return base


PRO2_BUNDLE = '/home/user/.local/share/xr-workspace/pro2/' + SHA[:12]
CONSENTS = {
    # name: (scope changes, what it covers)
    'luma-ultra-xrlinuxdriver': ({}, 'the default start: Luma Ultra, XRLinuxDriver tracking, 1920x1200@120 mode switch'),
    'luma-ultra-luma-driver': ({'tracking': 'luma', 'luma_driver': {'path': '/home/user/.local/share/xr-workspace/luma/' + SHA + '/xr-luma-driver', 'sha256': SHA}},
                               'our own Luma driver: `tracking` "luma" and its pinned binary under `luma_driver`'),
    'luma-ultra-3dof': ({'tracking_driver': '3dof'}, 'the 3DoF test driver: `tracking_driver` beside `tracking` "xrlinuxdriver"'),
    'luma-ultra-sbs': ({'sbs_mode': dict(sbs.SCOPE)}, '3D mode: `sbs_mode` scope on a Luma Ultra with its mode switch'),
    'beast-no-tracking': ({'model': 'beast', 'tracking': False, 'mode_switch': False, 'desktops': [dict(DESKTOP)]},
                          'no head tracking, no mode switch, one desktop'),
    'pro2-route': ({'model': 'pro2', 'tracking': 'pro2-sdk', 'mode_switch': False,
                    'plugin': {'path': PRO2_BUNDLE + '/bin/libxr-ownership.so', 'sha256': SHA},
                    'renderer': {'path': PRO2_BUNDLE + '/bin/xr-native-spike', 'sha256': SHA},
                    'pro2_route': {'bundle': PRO2_BUNDLE, 'manifest_sha256': SHA, 'projection': 46.5, 'pose_rate_hz': 90,
                                   'mount_pitch_deg': -4.0}},
                   'the VITURE Pro 2 route: its pinned bundle and settings under `pro2_route`'),
}

# Every reason a journal recovery can leave against a step (journal.recover), and every reason a Recover can give before
# it reaches the journal (__main__.recover_command). Each one has a scenario `recover-<key>` below.
STEP_REASONS = ['session-changed', 'session-mismatch', 'disconnected', 'mutation-failed', 'readback-missing', 'not-confirmed', 'broker-refused',
                'broker-fallback-unusable', 'broker-blocked'] + ['broker-' + step for step in REMOVAL_STEPS]
COMMAND_REASONS = {
    'no-reason': None,  # complete False and no reason given: recover_command's own words
    'journal-missing': 'recovery journal missing',
    'scope-mismatch': 'consent does not match saved broker scope',
    'not-canonical': 'managed recovery requires the canonical runtime journal',
    'plugin-pin': 'recovery plugin pin unavailable: {path} hash mismatch',
    'plugin-unavailable': 'consented ownership plugin unavailable for recovery',
}


class Compositor:
    """The normalized recovery view (journal.RecoveryBackend) of one compositor, with the failures recovery must survive."""
    def __init__(self, live_outputs=(), session=SESSION, settings=None, workspaces=()):
        self.session = session
        self.outputs = [{'id': 'physical', 'name': PHYSICAL, 'physical': True}] + [
            {'id': output_id(i), 'name': f'XR-{i + 1}', 'physical': False} for i in live_outputs]
        self.settings = {FOLLOW: 0} if settings is None else settings
        self.workspaces = [dict(w) for w in workspaces]
        self.refusal = None
        self.reply = None      # a broker reply that is not a proven removal (ownership._refused)
        self.raise_on = None   # an exception evacuate_remove raises
        self.mutation = 'ok'
        self.disconnected = False
        self.session_error = None  # what the broker raises for a session it cannot prove (OwnershipBrokerClient.query)

    def query(self, kind):
        if self.disconnected: raise ConnectionRefusedError(111, 'Connection refused')
        if kind == 'session' and self.session_error is not None: raise self.session_error
        return json.loads(json.dumps({'session': {'id': self.session}, 'outputs': self.outputs,
                                      'workspaces': self.workspaces, 'settings': self.settings}[kind]))

    def mutate(self, command):
        if self.mutation != 'ok': return self.mutation
        words = command.split()
        if words[0] == 'keyword': self.settings[words[1]] = int(words[2])
        elif words[:2] == ['dispatch', 'moveworkspacetomonitor']:
            for w in self.workspaces:
                if w['id'] == int(words[2]): w['output'] = words[3]
        return 'ok'

    def evacuate_remove(self, oid, name):
        self.refusal = None
        if self.raise_on is not None: raise self.raise_on
        if self.reply is not None:
            self.refusal = OwnershipBrokerClient._refused(SimpleNamespace(approved_physical=PHYSICAL), self.reply)
            return False
        live = next((o for o in self.outputs if o['id'] == oid and o['name'] == name and not o['physical']), None)
        if live is None: return True  # already gone: the broker reports it dead
        self.outputs.remove(live)
        for w in self.workspaces:
            if w['output'] == name: w['output'] = PHYSICAL
        return True


def broker_reply(blocked=None, state='blocked', fallback_usable=True):
    reply = {'id': output_id(0), 'name': 'XR-1', 'state': state, 'physical': False, 'observation': None, 'fallback_usable': fallback_usable}
    if blocked is not None: reply['blocked'] = blocked
    return reply


@contextmanager
def deterministic():
    """Fixed record ids and recovery times, so the corpus is the same bytes on every run."""
    def uuid4():
        Run.uuids += 1
        return uuid.UUID(f'00000000-0000-4000-a000-{Run.uuids:012d}')
    with ExitStack() as stack:
        stack.enter_context(mock.patch.object(journal_mod.uuid, 'uuid4', uuid4))
        stack.enter_context(mock.patch.object(controller.time, 'time', lambda: AT))
        yield


class Run:
    """One scenario's state and runtime folders, written by the real writers."""
    uuids = 0  # journal record ids (and its temp names) count from 1 in each scenario, so one scenario's change moves no other

    def __init__(self, base, name, run_id=None, **consent_changes):
        Run.uuids = 0
        self.name, self.run_id = name, run_id or 'fx-' + name
        self.state = base / 'state' / name
        self.runtime = base / 'runtime' / 'xr-workspace-spike' / self.run_id
        for d in (self.state, self.runtime): d.mkdir(parents=True, mode=0o700)
        self.consent_path = self.state / (self.run_id + '.json')
        self.journal = None
        self.extra = {}
        self.write_consent(**consent_changes)

    def write_consent(self, **changes):
        fd = os.open(self.state, os.O_RDONLY | os.O_DIRECTORY)
        try: write_consent(fd, build_consent(scope(self.run_id, **changes)))
        finally: os.close(fd)

    def progress(self, phase):
        _write_result(self.state, self.run_id + '.progress.json', dict(run_id=self.run_id, phase=phase))

    def result(self, **fields):
        result = {'run_id': self.run_id, 'phase': 'done', 'complete': True, 'renderer_returncode': 0, 'remaining': [], 'error': None}
        result.update(fields)
        _write_result(self.state, f"{result['run_id'] or 'invalid'}.result.json", result)

    def open_journal(self, broker=True, session=SESSION):
        self.journal = Journal.open(self.runtime / 'journal.json', self.run_id, session)
        if broker: self.journal.bind_broker(dict(BROKER))
        return self.journal

    def outputs(self, states):
        """One output record per state, in order: 'pending' (intent only), 'confirmed' (read back) or 'done' (removed)."""
        j = self.journal or self.open_journal()
        ids = []
        for index, state in enumerate(states):
            ids.append(j.prepare(output_op(index)))
            if state != 'pending': j.confirm(ids[-1], {'id': output_id(index), 'name': f'XR-{index + 1}', 'physical': False})
            if state == 'done': j.output_removed(output_id(index))
        return ids

    def recover(self, compositor, *, keep_before=True):
        if keep_before: shutil.copyfile(self.runtime / 'journal.json', self.runtime / 'journal.before.json')
        result = recover(self.journal, compositor)
        with mock.patch('sys.stderr'): controller.report_recovery(self.consent_path, result.reasons, result.remaining)
        return result

    def files(self):
        return sorted([p for d in (self.state, self.runtime) for p in d.iterdir()
                       if not p.name.startswith('.') and not p.name.endswith(('.lock', '.owner'))], key=lambda p: p.name)


def retained(remaining, reasons):
    """runner.execute's result for a run that stopped with steps left: the plugin stays loaded for an explicit Recover."""
    return dict(phase='run', complete=False, remaining=remaining, recovery=reasons,
                error='ownership plugin retained for explicit recovery' + (': ' + '; '.join(reasons) if reasons else ''))


def build(base):
    """Write every scenario under `base`; return the index of what each covers."""
    index = {'consents': {}, 'runs': {}}
    runs = []
    def run(name, about, **changes):
        r = Run(base, name, **changes)
        r.about = about
        runs.append(r)
        return r

    # In flight: the phases runner.main reports before the run's result exists.
    for phase in ('verify', 'tracking', 'plugin'):
        run(f'in-flight-{phase}', f'a run in flight at phase "{phase}": consent and progress only, no journal yet').progress(phase)
    r = run('in-flight-intent-only', 'half-finished: the journal holds its first output as intent only (pending), never read back')
    r.progress('desktops'); r.outputs(['pending'])
    r = run('in-flight-mid-create', 'half-finished: one output confirmed, the next prepared but not confirmed')
    r.progress('desktops'); r.outputs(['confirmed', 'pending'])
    r = run('in-flight-running', 'a run in flight with every desktop up: three confirmed outputs')
    r.progress('desktops'); r.outputs(['confirmed'] * 3)
    r = run('in-flight-desktop-removed', 'a run in flight after a pill removed its second desktop: that record is done')
    r.progress('desktops'); r.outputs(['confirmed', 'done', 'confirmed'])
    r = run('in-flight-unbound', 'half-finished before the broker was bound: a journal with no `broker` and no records')
    r.progress('desktops'); r.open_journal(broker=False)

    # Ended.
    r = run('ended-clean', 'a clean Stop: result done, every output record done')
    r.outputs(['done'] * 3); r.result(stop_reason='interrupted')
    r = run('ended-usb-hold', 'a clean run that waited out a USB drop and a renderer stop reason')
    r.outputs(['done'] * 2); r.result(usb_holds=[{'seconds': 4.2, 'resumed': True}], stop_reason='interrupted')
    r = run('ended-refused-verify', 'refused before anything moved: no journal, the reason in `error`')
    r.result(phase='verify', complete=False, renderer_returncode=None, remaining=None,
             error='consent plugin is not the pinned install')
    r = run('ended-refused-tracking', 'refused at tracking, with the fault the runner records for it')
    faults = run_faults.Faults()
    with mock.patch('sys.stderr'): faults.record(run_faults.RUN, ValueError('head tracking never reported a pose'), fatal=True)
    r.result(phase='tracking', complete=False, renderer_returncode=None, remaining=None,
             error='head tracking never reported a pose', faults=faults.report())
    r = Run(base, 'ended-invalid-consent', run_id='fx-ended-invalid-consent', **{})
    r.about = 'a consent that named no valid run: the result is `invalid.result.json` with `run_id` null'
    runs.append(r)
    _write_result(r.state, 'invalid.result.json', {'run_id': None, 'phase': 'verify', 'complete': False, 'renderer_returncode': None,
                                                   'remaining': None, 'error': 'invalid consent fields'})
    r = run('ended-pro2', 'a Pro 2 run, ended by its own route (runner.execute_pro2)', **CONSENTS['pro2-route'][0])
    r.result()
    r = run('ended-compositor-gone', 'Hyprland exited mid-run; Recover closed every record (Journal.compositor_ended)')
    ids = r.outputs(['confirmed', 'confirmed']); r.result(**retained(ids, []))
    shutil.copyfile(r.runtime / 'journal.json', r.runtime / 'journal.before.json')
    r.journal.compositor_ended(); controller.report_recovery(r.consent_path, [], [])

    # A run that stopped with steps left, then a Recover that finished: no .recovery.json, the result rewritten clean.
    r = run('recovered', 'a stopped run with outputs left (one pending, one confirmed), then a Recover that finished')
    ids = r.outputs(['confirmed', 'pending'])
    r.result(**retained(ids, ['XR-1: the compositor did not confirm it']))
    r.recover(Compositor(live_outputs=[0, 1]))

    # A Recover that did not finish, once per reason a step can stay.
    def step(key, about, comp, states=('confirmed',), legacy=None):
        r = run('recover-' + key, about)
        if legacy is None: ids = r.outputs(list(states))
        else:
            j = r.open_journal(broker=False, session=LEGACY_SESSION)
            ids = []
            for op in legacy:
                ids.append(j.prepare(op))
                j.confirm(ids[-1], {'value': op['applied']} if op['kind'] == 'setting' else {'output': op['applied']})
        r.result(**retained(ids, []))
        result = r.recover(comp)
        assert not result.complete and result.reasons, (key, result)
        r.reasons = result.reasons
        return r

    follow = {'kind': 'setting', 'key': FOLLOW, 'before': 1, 'applied': 0}
    gone = Compositor(live_outputs=[0]); gone.session_error = OwnershipBlocked('compositor session changed')
    step('session-changed', 'the broker cannot prove the compositor is the one the journal names', gone)
    step('session-mismatch', 'legacy supervisor journal: the connected compositor is another session',
         Compositor(session='2e' * 32), legacy=[follow])
    off = Compositor(live_outputs=[0]); off.disconnected = True
    step('disconnected', 'the compositor socket refused the connection', off)
    failing = Compositor(session=LEGACY_SESSION); failing.mutation = 'error'
    step('mutation-failed', 'legacy supervisor journal: a setting undo the compositor refused', failing, legacy=[follow])
    step('readback-missing', 'legacy supervisor journal: the setting is absent from readback',
         Compositor(session=LEGACY_SESSION, settings={}), legacy=[follow])
    move = {'kind': 'workspace', 'workspace': 3, 'before': 'HDMI-A-1', 'applied': 'XR-1'}
    step('not-confirmed', 'legacy supervisor journal: the workspace\'s first monitor is gone, so it is not moved back',
         Compositor(session=LEGACY_SESSION, workspaces=[{'id': 3, 'output': 'XR-1'}]), legacy=[move])
    for blocked in REMOVAL_STEPS:
        comp = Compositor(live_outputs=[0]); comp.reply = broker_reply(blocked)
        step('broker-' + blocked, f'the ownership plugin refused the removal at step "{blocked}"', comp)
    comp = Compositor(live_outputs=[0]); comp.reply = broker_reply(state='live')
    step('broker-refused', 'the ownership plugin refused without naming a step', comp)
    comp = Compositor(live_outputs=[0]); comp.reply = broker_reply('evacuate', fallback_usable=False)
    step('broker-fallback-unusable', 'refused, and the fallback monitor cannot take the windows', comp)
    comp = Compositor(live_outputs=[0, 1]); comp.raise_on = OwnershipBlocked('output name/UUID mismatch')
    step('broker-blocked', 'the broker proved the output is not ours; a second, pending output stayed with it too', comp,
         states=('confirmed', 'pending'))

    # A Recover refused before it reached the journal: nothing done, and why (__main__.recover_command).
    for key, reason in COMMAND_REASONS.items():
        r = run('recover-' + key, f'a Recover refused before the journal: {reason or "it said nothing"}')
        ids = r.outputs(['confirmed'])
        r.result(**retained(ids, []))
        if reason is None:
            stub = mock.patch.object(controller, 'recover_pinned', return_value={'complete': False, 'remaining': ids, 'reasons': []})
        else:
            exc = (OwnershipBlocked if 'broker' in reason or 'canonical' in reason else ValueError)(
                reason.format(path=scope(r.run_id)['plugin']['path']))
            stub = mock.patch.object(controller, 'recover_pinned', side_effect=exc)
        with stub, mock.patch('sys.stderr'): assert controller.recover_command(r.consent_path, r.runtime / 'journal.json') == 2
        r.reasons = json.loads((r.state / (r.run_id + '.recovery.json')).read_text())['reasons']

    for r in runs:
        entry = {'about': r.about, 'files': [p.name for p in r.files()]}
        if (r.runtime / 'journal.json').exists():
            entry['journal_states'] = sorted({rec['state'] for rec in json.loads((r.runtime / 'journal.json').read_text())['operations']})
        if hasattr(r, 'reasons'): entry['reasons'] = r.reasons
        index['runs'][r.name] = entry

    consent_dir = base / 'state' / 'consents'
    consent_dir.mkdir(mode=0o700)
    fd = os.open(consent_dir, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for name, (changes, about) in CONSENTS.items():
            write_consent(fd, build_consent(scope(name, **changes)))
            index['consents'][name + '.json'] = about
    finally: os.close(fd)
    index['step_reasons'], index['command_reasons'] = STEP_REASONS, list(COMMAND_REASONS)
    return runs, consent_dir, index


DATA_HOME = '/home/user/.local/share'
PINS = {
    # folder: (build outputs, what it covers)
    'luma-shim': (('xr-native-spike', 'libxr-ownership.so', 'xr-luma-driver', 'libxr-quiet-driver.so'),
                  'a full install: renderer, plugin and our Luma driver in installed.json, the quiet driver in its own pin'),
    'plain': (('xr-native-spike', 'libxr-ownership.so'), 'an older build: renderer and plugin only, no quiet driver pin'),
}


def build_pins(base):
    """Each install's pin files, written by the installer into a temporary data directory. Returns {folder: [paths]}."""
    pins = {}
    for name, (outputs, _) in PINS.items():
        build_dir, data_home = base / 'pins-build' / name, base / 'pins-data' / name
        build_dir.mkdir(parents=True)
        for output in outputs: (build_dir / output).write_bytes(output.encode() + b' stand-in build output\n')
        with mock.patch.object(install, '_write_engine'): install.install(build_dir, data_home)
        pins[name] = (data_home, sorted(p for p in (data_home / 'viture-workspace').glob('*.json')))
    data_home = base / 'pins-data' / 'pro2'
    with (mock.patch('tools.pro2_runtime.launch.verify_manifest', return_value=(Path(PRO2_BUNDLE), None)),
          mock.patch.object(pro2_route, 'manifest_sha', return_value=SHA),
          mock.patch.object(pro2_route, 'load_route', return_value=None)):
        pro2_route.write_pin(Path(PRO2_BUNDLE), data_home)
    pins['pro2'] = (data_home, [data_home / 'viture-workspace' / pro2_route.PIN_FILE])
    return pins


def generate(out):
    """The corpus written to `out` (replaced). Returns the index."""
    out = Path(out)
    with tempfile.TemporaryDirectory() as tmp, deterministic():
        base = Path(tmp)
        os.chmod(base, 0o700)
        runs, consent_dir, index = build(base)
        if out.exists(): shutil.rmtree(out)
        (out / 'runs').mkdir(parents=True)
        for r in runs:
            (out / 'runs' / r.name).mkdir()
            for p in r.files(): shutil.copyfile(p, out / 'runs' / r.name / p.name)
        shutil.copytree(consent_dir, out / 'consents')
        index['pins'] = {}
        for name, (data_home, paths) in build_pins(base).items():
            (out / 'pins' / name).mkdir(parents=True)
            for p in paths:
                (out / 'pins' / name / p.name).write_bytes(p.read_bytes().replace(str(data_home).encode(), DATA_HOME.encode()))
            index['pins'][name] = {'about': PINS[name][1] if name in PINS else 'the Pro 2 route: the bundle a Pro 2 start runs',
                                   'files': [p.name for p in paths]}
        (out / 'index.json').write_text(json.dumps(index, indent=1, sort_keys=True) + '\n')
    return index


if __name__ == '__main__':
    generate(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT)
    print(f'wrote {ROOT if len(sys.argv) < 2 else sys.argv[1]}')
