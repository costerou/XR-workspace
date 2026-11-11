"""The desktop manager's corpus for the Rust rebuild (rust/crates/xr-workspace/VIEW_CONTROLS.md, `desktops`, level 1).

rust/corpus/desktops/manager.json: for each scenario (the DesktopManager cases of test_desktops.py, as the pills and
Ctrl+Alt+R ask for them), the shipped desktops.DesktopManager runs its broker steps through the shipped
ownership.OwnershipBrokerClient and hyprland.HyprlandClient against a scripted Hyprland with an ownership plugin (this
file's `serve`, run by a copy of the Python executable named `Hyprland`, as the clients check the peer's name; never
the user's Hyprland). The run's first desktops are created first (`create_journaled`), then each action is planned
(`plan(manager.specs, placements, action, index, held=manager.held())`, as view.desktop_action plans it), started and
collected. Each action keeps the requests sent, its outcome (an error, the steps done), the desktops after it, the
manager's counts and the journal.

The plugin here answers from state, so the Rust replay (rust/crates/xr-workspace/tests/desktops_corpus.rs) runs the same
`serve` for view_controls::desktops::Manager, and the requests, outcomes, desktops, counts and journal must be the
same. Output ids, journal record ids and the session are random: both sides name them by first appearance (U0, U1, ...,
SESSION) in the requests, then the journal, then the desktops.

Regenerate (after a deliberate change) with:

    TMPDIR=/tmp /usr/bin/python3 tests/native_spike/desktops_corpus.py --write

With no argument it exits 1 when the file and the Python disagree (test_desktops_corpus.py).
"""
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[2]
FILE = ROOT / 'rust' / 'corpus' / 'desktops' / 'manager.json'
INSTANCE = '0123456789abcdef0123456789abcdef01234567_1700000000_1'
RUN_ID = 'run-1'
FALLBACK = 'DP-1'
EPOCH = 'epoch-one'
IDS = ['11111111-1111-4111-8111-11111111111' + str(i) for i in range(1, 5)]


# --- the scripted compositor (`serve`, run as `Hyprland`) -------------------------------------------------------------
def monitor(name, mid, x, y, width=1920, height=1080, scale=1.0, workspace=1):
    return dict(name=name, id=mid, x=x, y=y, width=width, height=height, refreshRate=120.0, scale=scale, transform=0,
                disabled=False, dpmsStatus=True, mirrorOf='none', activeWorkspace=dict(id=workspace, name=str(workspace)),
                reserved=[0, 0, 0, 0], model='fixture', description='fixture', focused=mid == 0, make='fixture',
                serial='', specialWorkspace=dict(id=0, name=''), vrr=False)


def serve(root, case, windows):
    """One request per connection, answered from the registry; every request appended to `root/wire.jsonl`."""
    root = Path(root)
    directory = root / 'hypr' / INSTANCE
    directory.mkdir(parents=True, mode=0o700)
    (directory / 'hyprland.lock').write_text(f'{os.getpid()}\nwayland-1\n')
    (directory / 'hyprland.lock').chmod(0o600)
    registry, faults = {}, {'reshape': 0, 'remove': 0}
    windows = json.loads(windows)

    def space(index): return 61 + 10 * index

    def live(): return [r for r in registry.values() if r['state'] == 'live']

    def monitors():
        out = [monitor('DP-1', 0, 0, 0), monitor('DP-2', 1, 1920, 0, workspace=2)]
        for i, r in enumerate(live()):
            o = r['observation']
            out.append(monitor(r['name'], 10 + i, o['x'], o['y'], o['width'], o['height'], o['scale'], o['workspace']))
        return out

    def workspaces():
        out = [dict(id=1, name='1', monitor='DP-1', monitorID=0, windows=0), dict(id=2, name='2', monitor='DP-2', monitorID=1, windows=0)]
        for r in live():
            w = r['observation']['workspace']
            out.append(dict(id=w, name=str(w), monitor=r['name'], monitorID=0, windows=sum(1 for c in windows if space(c['index']) == w)))
        return out

    def clients():
        return [dict(address=c['address'], mapped=True, hidden=False, at=c['at'], size=c['size'],
                     workspace=dict(id=space(c['index']), name=str(space(c['index']))), floating=c['floating'], pseudo=False,
                     monitor=0, **{'class': 'kitty'}, title='t', initialClass='kitty', initialTitle='t', pid=4242,
                     xwayland=False, pinned=False, fullscreen=0, fullscreenClient=0, grouped=[], tags=[], swallowing='0x0',
                     focusHistoryID=0) for c in windows]

    def observe(p):
        return dict(**{k: p[k] for k in ('width', 'height', 'refresh_millihz', 'scale', 'x', 'y')}, workspace=space(p['index']),
                    enabled=True, dpms=True, mirrored=False, transform=0,
                    workspaces=[dict(id=space(p['index']), name=str(space(p['index'])), special=False)], windows=[])

    def broker(j):
        p, op = j['payload'], j['op']
        if op == 'hello': return dict(qualified=True, fallback='physical-handle')
        if op == 'create':
            registry[p['id']] = dict(id=p['id'], name=p['name'], state='live', physical=False, fallback_usable=True,
                                     observation=observe(p))
            if case == 'create-fail' and p['index'] == 3: registry[p['id']]['state'] = 'failed'
        if p.get('id') not in registry: return None
        r = registry[p['id']]
        if op == 'reshape':
            faults['reshape'] += 1
            if not (case == 'reshape-fail' and faults['reshape'] == 1): r['observation'] = observe(p)
        if op == 'evacuate_remove':
            faults['remove'] += 1
            if case == 'remove-refused': return dict(r, blocked='evacuate')
            r['state'], r['observation'] = 'dead', None
        return r

    def respond(text):
        if text == 'j/cursorpos': return dict(x=100, y=100)
        if text in ('j/monitors', 'j/monitors all'): return monitors()
        if text == 'j/workspaces': return workspaces()
        if text == 'j/clients': return clients()
        if text.startswith('/eval '): return 'ok'
        if text.startswith('j/xrspike '):
            j = json.loads(text[len('j/xrspike '):])
            result = broker(j)
            if result is None: return dict(error='ownership blocked')
            return dict(**{k: j[k] for k in ('schema', 'session', 'run_id', 'request_id')}, epoch=EPOCH, result=result)
        return dict(error='unhandled request')

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    os.chdir(directory)
    server.bind('.socket.sock')
    server.listen(16)
    (root / 'fake-ready').write_text('ready')
    while True:
        conn, _ = server.accept()
        with conn:
            request = bytearray(conn.recv(65536))
            conn.settimeout(0.05)  # one request, then the client waits: read until it pauses
            try:
                while part := conn.recv(65536): request.extend(part)
            except (TimeoutError, socket.timeout): pass
            text = bytes(request).decode('utf-8')
            if text == 'stop': return
            with open(root / 'wire.jsonl', 'a') as f: f.write(json.dumps({'request': text}) + '\n')
            reply = respond(text)
            try: conn.sendall((reply if isinstance(reply, str) else json.dumps(reply)).encode())
            except (BrokenPipeError, ConnectionResetError): pass


class Fake:
    """`serve` as a child process named Hyprland in `root`."""
    def __init__(self, root, case, windows):
        self.root = Path(root)
        hyprland = self.root / 'Hyprland'
        shutil.copy('/usr/bin/python3', hyprland)
        self.proc = subprocess.Popen([str(hyprland), '-I', __file__, 'serve', str(root), case, json.dumps(windows)])
        deadline = time.monotonic() + 10
        while not (self.root / 'fake-ready').exists():
            if time.monotonic() > deadline: raise RuntimeError('the fake compositor did not start')
            time.sleep(0.01)
        self.seen = 0

    def requests(self):
        """The requests since the last call."""
        try: lines = (self.root / 'wire.jsonl').read_text().splitlines()
        except FileNotFoundError: lines = []
        out, self.seen = [json.loads(l)['request'] for l in lines[self.seen:]], len(lines)
        return out

    def close(self):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.connect(str(self.root / 'hypr' / INSTANCE / '.socket.sock')); s.sendall(b'stop')
        self.proc.wait(5)


# --- the scenarios -------------------------------------------------------------------------------------------------
def desktops(*sizes):
    """The run's first desktops, side by side right of DP-1 and DP-2 (hyprland.plan_outputs's place)."""
    out, x = [], 3840
    for i, (w, h, s) in enumerate(sizes):
        out.append({'id': IDS[i], 'name': f'XR-{i + 1}', 'index': i, 'width': w, 'height': h, 'refresh_millihz': 120000,
                    'scale': s, 'x': x, 'y': 0})
        x += int(w / s)
    return out


HD = (1920, 1080, 1.0)
UW = (2560, 1080, 1.0)
STICKING_OUT = {'address': '0x55aa01', 'index': 0, 'floating': True, 'at': [5000, 100], 'size': [1200, 600]}
ON_XR4 = {'address': '0x55aa04', 'index': 3, 'floating': False, 'at': [9600, 0], 'size': [1920, 1080]}


def scenarios():
    """(name, compositor case, first desktops, windows, actions). An action: [name, index] or [name, index, value]
    (scale: the new scale; resolution: [width, height])."""
    three = desktops(HD, HD, HD)
    return [
        ('add', '', three, [], [['add', 0]]),
        ('add_until_full', '', three, [], [['add', 2], ['add', 0]]),
        ('add_from_an_ultrawide_is_standard', '', desktops(HD, UW), [], [['add', 1]]),
        ('remove_the_middle_one', '', three, [], [['remove', 1]]),
        ('remove_the_last_one_left', '', desktops(HD), [], [['remove', 0]]),
        ('ultrawide_then_back_pulls_a_window_in', '', three, [STICKING_OUT], [['ultrawide', 0], ['ultrawide', 0]]),
        ('scale_shrinks_and_slides', '', three, [STICKING_OUT], [['scale', 0, 1.5]]),
        ('scale_that_does_not_divide', '', three, [], [['scale', 0, 1.75]]),
        ('resolution', '', three, [], [['resolution', 2, [2560, 1440]]]),
        ('reset_from_four_moves_xr4_onto_xr3', '', desktops(UW, HD, (2560, 1440, 1.25), HD), [ON_XR4], [['reset', 0]]),
        ('reset_of_the_default_does_nothing', '', three, [], [['reset', 0]]),
        ('reset_from_one_adds_two', '', desktops(UW), [], [['reset', 0]]),
        ('a_refused_create', 'create-fail', three, [], [['add', 0], ['add', 0]]),
        ('a_refused_remove', 'remove-refused', three, [], [['remove', 2]]),
        ('a_refused_reshape_is_put_back', 'reshape-fail', three, [], [['ultrawide', 0]]),
    ]


def normalize(texts, names):
    """Each UUID and session hex in `texts`, in order, named by first appearance (`names` carries over)."""
    pattern = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[0-9a-f]{64}')
    out = []
    for text in texts:
        for found in pattern.findall(text):
            names.setdefault(found, 'SESSION' if len(found) == 64 else f'U{len(names)}')
        out.append(pattern.sub(lambda m: names[m.group(0)], text))
    return out


def record(name, case, first, windows, actions):
    sys.path.insert(0, str(ROOT))
    from tools.native_spike import desktops as dm, hyprland, managed, ownership
    from tools.native_spike.journal import Journal
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        os.chmod(root, 0o700)
        (root / 'hypr').mkdir(mode=0o700)
        (root / 'journal').mkdir(mode=0o700)
        fake = Fake(root, case, windows)
        names, steps = {}, []
        try:
            client = hyprland.HyprlandClient(INSTANCE, root)
            session = client.verified_session()['id']
            broker = ownership.OwnershipBrokerClient(client, RUN_ID, session, FALLBACK)
            journal = Journal.open(root / 'journal' / 'journal.json', RUN_ID, session)
            broker.require_output_ownership()
            broker.create_journaled(first, journal)
            manager = dm.DesktopManager(broker, journal, first, guard_factory=lambda live: [s['name'] for s in live],
                                        threads=False, windows=client)
            manager.move_windows = managed._window_mover(client)
            normalize(fake.requests(), names)
            for action in actions:
                kind, index = action[0], action[1]
                extra = {'scale': action[2]} if kind == 'scale' else {'size': tuple(action[2])} if kind == 'resolution' else {}
                step = {'action': action}
                try:
                    change = dm.plan(manager.specs, [dict(p) for p in [dm.DEFAULT_PLACEMENT] * len(manager.specs)], kind, index,
                                     held=manager.held(), **extra)
                except ValueError as exc:
                    step['plan_error'] = str(exc)
                    change = None
                if change is not None:
                    step['started'] = manager.start(change)
                    _, error, done = manager.take_result()
                    step.update(error=error, done=done)
                requests = normalize(fake.requests(), names)
                text = (root / 'journal' / 'journal.json').read_text()
                step['journal'] = normalize([text], names)[0]
                step['desktops'] = json.loads(normalize([json.dumps(manager.specs)], names)[0])
                step['guard'] = manager.guard()
                step['counts'] = {k: getattr(manager, k) for k in ('changes', 'failures', 'fitted', 'fit_failures')}
                step['requests'] = requests
                steps.append(step)
            manager.close()
        finally: fake.close()
        return {'name': name, 'case': case, 'first': first, 'windows': windows, 'steps': steps}


def corpus():
    return {'schema': 1, 'source': 'tests/native_spike/desktops_corpus.py', 'instance': INSTANCE, 'run_id': RUN_ID,
            'fallback': FALLBACK, 'note': 'One request per connection. UUIDs and the session named by first appearance.',
            'scenarios': [record(*s) for s in scenarios()]}


def dumps(value):
    return json.dumps(value, indent=1, ensure_ascii=False) + '\n'


def main(argv):
    if argv[:1] == ['serve']: return serve(*argv[1:])
    if argv not in ([], ['--write']): raise SystemExit('usage: desktops_corpus.py [--write]')
    text = dumps(corpus())
    if argv == ['--write']:
        FILE.parent.mkdir(parents=True, exist_ok=True)
        FILE.write_text(text)
        print('wrote', FILE.relative_to(ROOT))
        return 0
    if not FILE.exists() or FILE.read_text() != text:
        print('stale:', FILE.relative_to(ROOT))
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
