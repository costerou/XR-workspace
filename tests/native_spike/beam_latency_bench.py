"""How long the Serif Beam takes to follow an app, measured: the moment the compositor holds the new cursor name to the moment
the `overview_pointer` frame with the new look reaches the renderer's socket. That is all of the delay this project adds; the
app's own answer to the pointer comes before it, and the renderer draws what it has on its next frame (Presentation::
draw_test_pattern reads the look just before the draw) after it.

Three processes, so the interpreter lock is shared only where it is in a real run:
  compositor  a fake Hyprland: real unix sockets (.socket.sock one connection per request, .socket2.sock), `cursorpos`,
              `monitors` and the broker's `cursor_shape`; with --push it writes `xrcursor>>name` when the name changes, as
              the ownership plugin does.
  supervisor  the real thing from --tree: Supervisor.run, ViewControl, UnifiedPointer, UnifiedSampler, HyprlandClient and
              OwnershipBrokerClient. Only the compositor calls that change the desktop are the test suite's fakes.
  renderer    this process: heartbeats at 120 Hz, reads the frames, keeps the time.

    nice -n 19 python3 tests/native_spike/beam_latency_bench.py [--tree DIR] [--push] [--no-reader] [--cycles 200] [--json]

--tree runs another checkout's tools/ (git worktree or `git archive`), which is how a before and after compare. Nothing here
touches a real compositor, the glasses or the GPU. Numbers depend on the machine and on Python: read them against each other.
"""
import argparse
import json
import math
import os
from pathlib import Path
import random
import selectors
import socket
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
INSTANCE = 'a' * 40 + '_1_1'
FRAME_S = 1 / 120


def compositor(runtime, push, commands_fd):
    """The fake Hyprland. Reads newline-ended commands on `commands_fd`: a cursor name (`-` for none), `moving`, `still`, or
    `count` (writes the requests served so far to stdout)."""
    from test_free_pointer import MONITORS, X1
    directory = Path(runtime) / 'hypr' / INSTANCE
    directory.mkdir(parents=True, mode=0o700)
    selector = selectors.DefaultSelector()
    listeners = {}
    for name in ('.socket.sock', '.socket2.sock'):
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(directory / name)); server.listen(64); server.setblocking(False)
        selector.register(server, selectors.EVENT_READ); listeners[server] = name
    commands = os.fdopen(commands_fd, 'rb', buffering=0)
    selector.register(commands, selectors.EVENT_READ)
    print('ready', flush=True)
    shape, moving, step, served, events, pending = '', False, 0, {'cursorpos': 0, 'monitors': 0, 'cursor_shape': 0}, [], b''
    x, y = X1 + 960, 540
    monitors = json.dumps(MONITORS).encode()
    while True:
        for key, _ in selector.select(0.002):
            if key.fileobj is commands:
                data = commands.read(4096)
                if not data: return
                pending += data
                *lines, pending = pending.split(b'\n')
                for line in lines:
                    word = line.decode()
                    if word == 'moving': moving = True
                    elif word == 'still': moving = False
                    elif word == 'count': print(json.dumps(served), flush=True)
                    else:
                        shape = '' if word == '-' else word
                        if push:
                            for client in events: client.send(f'xrcursor>>{shape}\n'.encode())
                continue
            conn, _ = key.fileobj.accept()
            if listeners[key.fileobj] == '.socket2.sock':
                events.append(conn)
                continue
            conn.setblocking(True)
            with conn:
                request = conn.recv(65536).decode()
                if request == 'j/cursorpos':
                    served['cursorpos'] += 1
                    conn.sendall(json.dumps({'x': x, 'y': y}).encode())
                elif request == 'j/monitors all':
                    served['monitors'] += 1
                    conn.sendall(monitors)
                elif request.startswith('j/xrspike '):
                    served['cursor_shape'] += 1
                    e = json.loads(request[len('j/xrspike '):])
                    conn.sendall(json.dumps({k: e[k] for k in ('schema', 'session', 'run_id', 'request_id', 'epoch')}
                                            | {'result': {'shape': shape}}, separators=(',', ':')).encode())
        if moving:  # a hand on the mouse: a pixel at a time, staying on the desktop's content
            step += 1
            x = X1 + 960 + (step % 40)


def supervisor(tree, runtime, control_fd, wired):
    """The real supervisor loop and sampler from `tree`, on the fake compositor."""
    sys.path.insert(0, str(tree))
    from tools.native_spike import ownership, supervisor as sv, unified_cursor as uc, view
    from tools.native_spike.hyprland import HyprlandClient
    from test_free_pointer import BIND_ARGS, G, NAMES
    from test_unified_cursor import MID1, FakeUnifiedClient

    transport = HyprlandClient(INSTANCE, Path(runtime))

    class Client(FakeUnifiedClient):  # real reads, fake changes
        def query(self, kind, argument=None, timeout=0.25):
            return transport.query(kind, argument, timeout=timeout)

    class Server:  # listening, or the loop's selector sees a dead socket and spins
        socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    Server.socket.bind(''); Server.socket.listen(1)

    class Child:
        def poll(self): return None

    broker = ownership.OwnershipBrokerClient(transport, 'run', 'session', 'DP-1')
    broker.epoch = 'epoch-one'
    control = view.ViewControl(view.ViewState(NAMES), 'run', 'session', lambda name: None, None, Server())
    pointer = control.overview_pointer = uc.UnifiedPointer(control)
    pointer.set_geometry(G)
    control.wake = pointer.wake_reader
    control.events = transport.event_stream()
    reader = broker.cursor_reader() if wired else None
    uc.UnifiedSampler(Client(cursor=MID1), NAMES, pointer, BIND_ARGS, cursor_shape=reader).start()
    sv.Supervisor(Child(), lambda: None, grace=30.0, control=control).run(socket.socket(fileno=control_fd))


def look_of(line):
    """The look an `overview_pointer` frame carries (None: the plain dot), or `...` for any other frame."""
    try: frame = json.loads(line)
    except ValueError: return ...
    if frame.get('op') != 'overview_pointer': return ...
    return frame['payload'].get('look') if frame['payload'].get('visible') else ...


class Renderer:
    def __init__(self, sock):
        self.sock, self.buffer, self.look, self.heartbeat_at = sock, b'', ..., 0.0

    def pump(self, seconds):
        """Heartbeats and frames for `seconds`, or until the look changes: the time it changed, else None. A change that comes
        and goes in one read of the socket still returns."""
        end = time.monotonic() + seconds
        while True:
            now = time.monotonic()
            if now >= self.heartbeat_at:
                self.sock.send(b'H')
                self.heartbeat_at = now + FRAME_S
            wait = min(self.heartbeat_at, end) - now
            if wait <= 0 and now >= end: return None
            selected = selectors.DefaultSelector()
            selected.register(self.sock, selectors.EVENT_READ)
            ready = selected.select(max(wait, 0))
            selected.close()
            if not ready: continue
            data = self.sock.recv(1 << 20)
            at = time.monotonic()
            if not data: raise RuntimeError('supervisor closed the socket')
            self.buffer += data
            *lines, self.buffer = self.buffer.split(b'\n')
            changed = False
            for line in lines:
                look = look_of(line)
                if look is ... or look == self.look: continue
                self.look, changed = look, True
            if changed: return at

    def until(self, wanted, seconds=2.0):
        end = time.monotonic() + seconds
        while self.look != wanted:
            if time.monotonic() >= end: raise RuntimeError(f'look stayed {self.look!r}, wanted {wanted!r}')
            self.pump(0.05)


def cpu_s(pid):
    """A process's user + system CPU seconds."""
    fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
    return (int(fields[11]) + int(fields[12])) / os.sysconf('SC_CLK_TCK')


def percentile(values, share):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, math.ceil(share * len(ordered)) - 1)]


def summary(values):
    return {'median_ms': round(percentile(values, 0.5) * 1000, 2), 'p95_ms': round(percentile(values, 0.95) * 1000, 2),
            'worst_ms': round(max(values) * 1000, 2), 'n': len(values)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--tree', default=str(ROOT))
    parser.add_argument('--push', action='store_true', help='the compositor announces changes on socket2')
    parser.add_argument('--no-reader', action='store_true', help='no cursor reads at all: the CPU floor')
    parser.add_argument('--cycles', type=int, default=200)
    parser.add_argument('--cpu-seconds', type=float, default=5.0, help='each CPU window; 0 skips them')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--role', help=argparse.SUPPRESS)
    parser.add_argument('--runtime', help=argparse.SUPPRESS)
    parser.add_argument('--fd', type=int, help=argparse.SUPPRESS)
    args = parser.parse_args()
    sys.path.insert(0, str(HERE))
    if args.role == 'compositor':
        sys.path.insert(0, str(args.tree))
        return compositor(args.runtime, args.push, args.fd)
    if args.role == 'supervisor':
        return supervisor(args.tree, args.runtime, args.fd, not args.no_reader)

    runtime = tempfile.mkdtemp(prefix='bb-', dir='/tmp')  # short: unix socket paths are limited to 107 bytes
    os.chmod(runtime, 0o700)
    commands_read, commands_write = os.pipe()
    ours, theirs = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    base = [sys.executable, __file__, '--tree', args.tree, '--runtime', runtime]
    fake = subprocess.Popen(base + ['--role', 'compositor', '--fd', str(commands_read)] + (['--push'] if args.push else []),
                            pass_fds=[commands_read], stdout=subprocess.PIPE, text=True)
    children = [fake]
    try:
        fake.stdout.readline()
        real = subprocess.Popen(base + ['--role', 'supervisor', '--fd', str(theirs.fileno())] + (['--no-reader'] if args.no_reader else []),
                                pass_fds=[theirs.fileno()])
        children.append(real)
        theirs.close()
        send = lambda word: os.write(commands_write, word.encode() + b'\n')
        renderer = Renderer(ours)
        renderer.pump(1.0)
        report = {'tree': args.tree, 'push': args.push, 'reader': not args.no_reader}

        def window(moving):
            send('moving' if moving else 'still')
            renderer.pump(0.3)
            send('count')
            first = json.loads(fake.stdout.readline())
            start, began = cpu_s(real.pid), time.monotonic()
            deadline = began + args.cpu_seconds
            while time.monotonic() < deadline: renderer.pump(0.25)
            cpu, took = cpu_s(real.pid) - start, time.monotonic() - began
            send('count')
            last = json.loads(fake.stdout.readline())
            return {'cpu_percent_of_one_core': round(100 * cpu / took, 2),
                    'reads_per_s': round((last['cursor_shape'] - first['cursor_shape']) / took, 1),
                    'samples_per_s': round((last['cursorpos'] - first['cursorpos']) / took, 1)}

        def sweep(gap_ms, lines=30, line_s=0.06):
            """The pointer down a page: `lines` lines, a gap of gap_ms after each. How many times the dot shows in the gaps, and how
            long it then takes to leave for good."""
            send('text'); renderer.until('text'); renderer.pump(0.5)
            dots = 0
            for _ in range(lines):
                send('default')
                if renderer.pump(gap_ms / 1000) is not None and renderer.look is None: dots += 1
                send('text')
                renderer.pump(line_s)
                if renderer.look != 'text': renderer.until('text')
            began = time.monotonic()
            send('default')
            changed = renderer.pump(2.0)
            renderer.pump(0.5)
            return {'dot_flashes': dots, 'of_gaps': lines, 'leave_after_ms': round((changed - began) * 1000, 1)}

        if args.cpu_seconds:
            if args.push and not args.no_reader:  # the sampler only checks once it has seen a push
                send('text'); renderer.until('text')
            send('-'); renderer.until(None)  # every run measures the plain dot: the beam adds a hit test per tick in all of them
            report['still'] = window(False)
            report['moving'] = window(True)
        send('moving')
        if not args.no_reader:
            rng = random.Random(1)
            enter, leave = [], []
            send('-'); renderer.until(None)
            for _ in range(args.cycles):
                for name, wanted, out in (('text', 'text', enter), ('default', None, leave)):
                    renderer.pump(rng.uniform(0.12, 0.22))
                    began = time.monotonic()
                    send(name)
                    changed = renderer.pump(2.0)
                    if changed is None or renderer.look != wanted: raise RuntimeError(f'no change to {wanted!r}')
                    out.append(changed - began)
            report['enter'], report['leave'] = summary(enter), summary(leave)
            if args.cycles >= 20: report['sweep'] = {f'gap_{ms}ms': sweep(ms) for ms in (20, 50, 80)}
        if args.json: print(json.dumps(report))
        else: print(json.dumps(report, indent=2))
    finally:
        os.close(commands_write)
        for child in children:
            child.terminate()
        for child in children:
            try: child.wait(5)
            except subprocess.TimeoutExpired: child.kill()
        subprocess.run(['rm', '-rf', runtime], check=False)


if __name__ == '__main__':
    main()
