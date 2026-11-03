"""Control-plane timing harness. Offline: fakes only, never the live compositor, renderer, glasses or controller service.

usage: python3 -m tools.native_spike.bench_control [--quick] [--json PATH] [--only NAME,...]

Benchmarks (one result block each; --json writes them all for a before / after comparison):
  hypr_query  HyprlandClient.query('cursorpos') against a fake Hyprland socket server in another process: client CPU
              and wall time per request. The overview's cursor sampler makes up to 250 of these a second.
  overview    The overview end to end: a real Supervisor loop with the run's ViewControl, the input lock's LockedPointer
              and LockedSampler, and a real HyprlandClient against the fake compositor, whose cursor moves 10 px every
              20 ms. A fake renderer process sends heartbeats at 120 Hz and times the overview_pointer frames it gets.
              Supervisor-process CPU (loop + sampler thread), samples per second, and cursor move -> dot frame latency.
  watchdog    The watchdog loop alone: 120 Hz heartbeats from the fake renderer and a real spawned guard worker (every
              managed run has one). Loop CPU per heartbeat.
  guard       validate_physical_pair, the watchdog guard's compositor check (every supervisor.GUARD_PERIOD while a run is
              up): compositor requests and CPU per check, and how long a spawned guard worker takes to its first verdict.
  control_op  A key press or overview click: `python3 -I xr_control.py SOCKET RUN SESSION OP` spawned like Hyprland's
              exec, until the run's PrivateServer holds the message; and the server's receive cost per frame.
  controller  The controller service's 1 s tick (Host.probe + Controller.tick) with STALE_RUNS finished runs in its state
              directory (as on a machine that has run a while), and Controller.snapshot(). Subprocesses per tick and CPU
              including children. Runs the real `systemctl --user is-active` on unit names that do not exist (read only)
              and reads the real USB / DRM sysfs (read only).
  startup     Interpreter start plus imports: the bind helper (per key press), the controller service, the panel runner.
"""
import argparse
import json
import os
from pathlib import Path
import resource
import secrets
import shutil
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time

REPO = Path(__file__).resolve().parents[2]
HELPER = Path(__file__).with_name('xr_control.py')
INSTANCE = 'a' * 40 + '_1700000000_1'
NAMES = ['XR-1', 'XR-2', 'XR-3']
STALE_RUNS = 30
# The renderer's overview layout (tests/native_spike/fixtures/overview_pointer_payloads.json).
GEOMETRY = {'w': 1920, 'h': 1200, 'half': [0.36, 0.225],
            'tiles': [[40.0, 435.0, 586.67, 330.0], [666.67, 435.0, 586.67, 330.0], [1293.33, 435.0, 586.67, 330.0]]}


def _monitor(i, name, x, w, h):
    return {'id': i, 'name': name, 'description': name, 'make': '', 'model': '', 'serial': '', 'width': w, 'height': h,
            'refreshRate': 120.0, 'x': x, 'y': 0, 'activeWorkspace': {'id': i + 1, 'name': str(i + 1)},
            'specialWorkspace': {'id': 0, 'name': ''}, 'reserved': [0, 0, 0, 0], 'scale': 1.0, 'transform': 0,
            'focused': i == 0, 'dpmsStatus': True, 'vrr': False, 'solitary': '0', 'activelyTearing': False,
            'directScanoutTo': '0', 'disabled': False, 'currentFormat': 'XRGB8888', 'mirrorOf': 'none',
            # A physical monitor lists its modes (a few dozen); the headless XR-n outputs list none.
            'availableModes': [] if name.startswith('XR-') else
            [f'{mw}x{mh}@{hz:.2f}Hz' for mw, mh in ((w, h), (1920, 1080), (1680, 1050), (1280, 720), (1024, 768))
             for hz in (120.0, 100.0, 75.0, 60.0, 59.94, 50.0)]}


# DP-1 the desk monitor, DP-2 the glasses (parked far away), XR-1..3 right of DP-1 (as plan_outputs lays them out).
MONITORS = [_monitor(0, 'DP-1', 0, 2560, 1440), _monitor(1, 'DP-2', -1920, 1920, 1200),
            _monitor(2, 'XR-1', 2560, 1920, 1080), _monitor(3, 'XR-2', 4480, 1920, 1080), _monitor(4, 'XR-3', 6400, 1920, 1080)]

# The fake compositor: Hyprland's request socket (one request per connection, reply, close) answering the queries this
# code makes; `/eval` says ok and applies a numeric cursor.move (the sampler's park / warp). `stats` (not a Hyprland
# request) returns the request counts and the start of the cursor's oscillation. Runs as its own process, under an
# executable named Hyprland, so verified_session() accepts it.
FAKE_HYPRLAND = r'''
import json, os, re, socket, sys, time
directory, mode, period, monitors = sys.argv[1], sys.argv[2], float(sys.argv[3]), sys.argv[4].encode()
fd = os.open(os.path.join(directory, 'hyprland.lock'), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
os.write(fd, f'{os.getpid()}\nwayland-1\n'.encode()); os.close(fd)
listener = socket.socket(socket.AF_UNIX)
listener.bind(os.path.join(directory, '.socket.sock')); listener.listen(64)
counts, base, t0 = {}, [2560 + 960, 540], time.monotonic()
warp = re.compile(rb'cursor\.move\(\{ x = (-?[0-9]+), y = (-?[0-9]+) \}\)')
while True:
    conn, _ = listener.accept()
    with conn:
        req = conn.recv(65536)
        kind = req.split(b' ', 1)[0]
        counts[kind.decode()] = counts.get(kind.decode(), 0) + 1
        if kind == b'j/cursorpos':
            x = base[0] + (10 if mode == 'oscillate' and int((time.monotonic() - t0) / period) % 2 else 0)
            reply = b'{\n"x": %d,\n"y": %d\n}' % (x, base[1])
        elif kind == b'j/monitors': reply = monitors
        elif kind in (b'j/workspaces', b'j/plugin'): reply = b'[]'
        elif kind == b'j/getoption': reply = b'{"option": "input:follow_mouse", "int": 1, "set": false}'
        elif kind == b'stats': reply = json.dumps({'counts': counts, 't0': t0, 'period': period}).encode()
        else:
            moves = warp.findall(req)
            if moves: base[:] = [int(moves[-1][0]), int(moves[-1][1])]
            reply = b'ok'
        conn.sendall(reply)
'''

# The fake renderer: b'H' heartbeats at `hz`, reads every frame the supervisor sends and records when each
# overview_pointer frame arrived (CLOCK_MONOTONIC, shared with the fake compositor). SIGTERM writes the record and exits.
FAKE_RENDERER = r'''
import json, select, signal, socket, sys, time
sock = socket.socket(fileno=int(sys.argv[1])); out, interval = sys.argv[2], 1 / float(sys.argv[3])
stop = []
signal.signal(signal.SIGTERM, lambda *a: stop.append(1))
sock.setblocking(False)
frames, counts, buf, due = [], {}, b'', time.monotonic()
while not stop:
    now = time.monotonic()
    if now >= due:
        try: sock.send(b'H')
        except BlockingIOError: pass
        except OSError: break
        due += interval
        if due < now: due = now + interval
    r, _, _ = select.select([sock], [], [], max(0.0, due - time.monotonic()))
    if not r: continue
    try: data = sock.recv(1 << 20)
    except BlockingIOError: continue
    except OSError: break
    if not data: break
    t = time.monotonic()
    *lines, buf = (buf + data).split(b'\n')
    for line in lines:
        m = json.loads(line)
        counts[m['op']] = counts.get(m['op'], 0) + 1
        if m['op'] == 'overview_pointer': frames.append((t, m['payload']['x'], m['payload']['visible']))
with open(out, 'w') as f: json.dump({'frames': frames, 'counts': counts}, f)
'''


def pct(values, q):
    values = sorted(values)
    if not values: return float('nan')
    return values[min(len(values) - 1, int(round(q * (len(values) - 1))))]


def summary(values, scale=1.0, digits=1):
    return {'p50': round(pct(values, 0.5) * scale, digits), 'p90': round(pct(values, 0.9) * scale, digits),
            'p99': round(pct(values, 0.99) * scale, digits),
            'mean': round(statistics.fmean(values) * scale, digits) if values else None, 'n': len(values)}


def children_cpu():
    r = resource.getrusage(resource.RUSAGE_CHILDREN)
    return r.ru_utime + r.ru_stime


class FakeCompositor:
    """The fake Hyprland in a private runtime directory (removed on close)."""
    def __init__(self, mode='static', period=0.02):
        self.tmp = Path(tempfile.mkdtemp(prefix='bench-cp-'))
        self.runtime = self.tmp / 'run'
        self.runtime.mkdir(mode=0o700)
        (self.runtime / 'hypr').mkdir(mode=0o700)
        self.directory = self.runtime / 'hypr' / INSTANCE
        self.directory.mkdir(mode=0o700)
        # verified_session() checks the peer's executable is named Hyprland: a copy of this interpreter under that name.
        exe = self.tmp / 'Hyprland'
        shutil.copy2(os.path.realpath(sys.executable), exe)
        env = dict(os.environ, PYTHONHOME=sys.base_prefix)
        self.proc = subprocess.Popen([str(exe), '-I', '-c', FAKE_HYPRLAND, str(self.directory), mode, str(period),
                                      json.dumps(MONITORS)], env=env, stdin=subprocess.DEVNULL)
        deadline = time.monotonic() + 10
        while not (self.directory / '.socket.sock').exists():
            if self.proc.poll() is not None or time.monotonic() > deadline: raise RuntimeError('fake compositor did not start')
            time.sleep(0.01)

    def client(self):
        from .hyprland import HyprlandClient
        return HyprlandClient(INSTANCE, self.runtime)

    def stats(self):
        with socket.socket(socket.AF_UNIX) as s:
            s.connect(str(self.directory / '.socket.sock')); s.sendall(b'stats')
            data = b''
            while chunk := s.recv(65536): data += chunk
        return json.loads(data)

    def close(self):
        self.proc.terminate(); self.proc.wait()
        shutil.rmtree(self.tmp, ignore_errors=True)


def bench_hypr_query(quick):
    n = 2000 if quick else 10000
    fake = FakeCompositor()
    try:
        client = fake.client()
        for _ in range(200): client.query('cursorpos', timeout=0.25)  # warm up
        walls = []
        c0 = time.process_time()
        for _ in range(n):
            t = time.perf_counter()
            client.query('cursorpos', timeout=0.25)
            walls.append(time.perf_counter() - t)
        cpu = time.process_time() - c0
    finally:
        fake.close()
    return {'requests': n, 'client_cpu_us_per_request': round(cpu / n * 1e6, 1), 'wall_us': summary(walls, 1e6),
            'sampler_cpu_pct_at_250hz': round(cpu / n * 250 * 100, 2)}


def bench_overview(quick):
    from . import view
    from .input_lock import LockedPointer, LockedSampler
    from .free_pointer import parse_overview_geometry
    from .ipc import PrivateServer
    from .profiles import LayoutStore
    from .supervisor import Supervisor
    warmup, window = (0.7, 2.0) if quick else (1.0, 5.0)
    fake = FakeCompositor('oscillate', 0.02)
    run_id, session = 'bench' + secrets.token_hex(4), 's' * 64
    out = fake.tmp / 'renderer.json'
    parent, child = socket.socketpair()
    status_parent, status_child = socket.socketpair(type=socket.SOCK_DGRAM)
    proc = subprocess.Popen([sys.executable, '-I', '-c', FAKE_RENDERER, str(child.fileno()), str(out), '120'],
                            pass_fds=(child.fileno(),), stdin=subprocess.DEVNULL)
    child.close(); status_child.close()
    server = PrivateServer(fake.runtime, run_id, session)

    class Placements:
        def __init__(self): self.saved = None
        def load(self): return self.saved or []
        def save(self, value): self.saved = value

    control = view.ViewControl(view.ViewState(NAMES), run_id, session, lambda name: None,
                               LayoutStore(fake.tmp / 'layout.json'), server, placements=Placements())
    pointer = control.overview_pointer = LockedPointer(control)
    control.wake = pointer.wake_reader
    control.set_overview_geometry(parse_overview_geometry(GEOMETRY))
    client = fake.client()
    bind_args = (sys.executable, str(HELPER), str(server.path), run_id, session)
    sampler = LockedSampler(client, NAMES, pointer, bind_args).start()
    watchdog = Supervisor(proc, lambda: proc.poll(), control=control, status_socket=status_parent)
    marks = {}
    loop_clock = time.pthread_getcpuclockid(threading.get_ident())  # this thread runs the supervisor loop
    sampler_clock = time.pthread_getcpuclockid(sampler._thread.ident)

    def timer():
        time.sleep(warmup)
        marks['t0'], marks['c0'], marks['s0'] = time.monotonic(), time.process_time(), sampler.samples
        marks['l0'], marks['p0'] = time.clock_gettime(loop_clock), time.clock_gettime(sampler_clock)
        time.sleep(window)
        marks['t1'], marks['c1'], marks['s1'] = time.monotonic(), time.process_time(), sampler.samples
        marks['l1'], marks['p1'] = time.clock_gettime(loop_clock), time.clock_gettime(sampler_clock)
        proc.terminate()
    t = threading.Thread(target=timer, daemon=True); t.start()
    try:
        watchdog.run(parent)
        t.join()
    finally:
        sampler.stop(); control.join_focus(2); pointer.close(); server.close()
        parent.close(); status_parent.close()
        if proc.poll() is None: proc.kill(); proc.wait()
        stats = fake.stats()
        record = json.loads(out.read_text()) if out.exists() else {'frames': [], 'counts': {}}
        fake.close()
    elapsed = marks['t1'] - marks['t0']
    # Cursor move -> dot frame: the k-th oscillation step happens at t0 + k * period; its frame is the first
    # overview_pointer frame after it that moved the dot.
    period, origin = stats['period'], stats['t0']
    frames = [f for f in record['frames'] if f[2]]
    latencies, missed, j = [], 0, 0
    k = int((marks['t0'] - origin) / period) + 1
    while origin + k * period < marks['t1'] - period:
        tk = origin + k * period
        while j < len(frames) and frames[j][0] < tk: j += 1
        if j < len(frames) and frames[j][0] - tk < period and (j == 0 or frames[j][1] != frames[j - 1][1]):
            latencies.append(frames[j][0] - tk)
        else:
            missed += 1
        k += 1
    samples = marks['s1'] - marks['s0']
    return {'window_s': round(elapsed, 2), 'supervisor_cpu_pct': round((marks['c1'] - marks['c0']) / elapsed * 100, 2),
            'loop_thread_cpu_pct': round((marks['l1'] - marks['l0']) / elapsed * 100, 2),
            'sampler_thread_cpu_pct': round((marks['p1'] - marks['p0']) / elapsed * 100, 2),
            'sampler_cpu_us_per_sample': round((marks['p1'] - marks['p0']) / max(samples, 1) * 1e6, 1),
            'samples_per_s': round(samples / elapsed, 1),
            'move_to_frame_ms': summary(latencies, 1e3, 2), 'moves_without_frame': missed,
            'renderer_frames': record['counts'], 'compositor_requests': stats['counts'],
            'sampler_errors': sampler.errors, 'pointer_mode': pointer.mode}


def bench_watchdog(quick):
    """The watchdog loop alone (no view control): heartbeats at 120 Hz from a fake renderer process and a real spawned
    guard worker checking the fake compositor every GUARD_PERIOD, as in every managed run."""
    from .supervisor import PhysicalPairGuard, Supervisor
    warmup, window = (0.7, 2.0) if quick else (1.0, 5.0)
    fake = FakeCompositor()
    out = fake.tmp / 'renderer.json'
    parent, child = socket.socketpair()
    proc = subprocess.Popen([sys.executable, '-I', '-c', FAKE_RENDERER, str(child.fileno()), str(out), '120'],
                            pass_fds=(child.fileno(),), stdin=subprocess.DEVNULL)
    child.close()
    client = fake.client()
    session = client.verified_session()['id']
    watchdog = Supervisor(proc, lambda: proc.poll(), guard=PhysicalPairGuard(client, session, 'DP-2', 'DP-1'))
    marks, beats = {}, [0]
    heartbeat = watchdog.heartbeat

    def counted(now):
        beats[0] += 1
        heartbeat(now)
    watchdog.heartbeat = counted
    loop_clock = time.pthread_getcpuclockid(threading.get_ident())

    def timer():
        time.sleep(warmup)
        marks['t0'], marks['l0'], marks['b0'] = time.monotonic(), time.clock_gettime(loop_clock), beats[0]
        time.sleep(window)
        marks['t1'], marks['l1'], marks['b1'] = time.monotonic(), time.clock_gettime(loop_clock), beats[0]
        proc.terminate()
    t = threading.Thread(target=timer, daemon=True); t.start()
    try:
        result = watchdog.run(parent)
        t.join()
    finally:
        parent.close()
        if proc.poll() is None: proc.kill(); proc.wait()
        fake.close()
    elapsed, cpu, n = marks['t1'] - marks['t0'], marks['l1'] - marks['l0'], marks['b1'] - marks['b0']
    return {'window_s': round(elapsed, 2), 'heartbeats_per_s': round(n / elapsed, 1),
            'loop_cpu_pct': round(cpu / elapsed * 100, 2), 'loop_cpu_us_per_heartbeat': round(cpu / max(n, 1) * 1e6, 1),
            'stopped_by': 'renderer exit' if result is not None else 'unexpected'}


def bench_guard(quick):
    from .supervisor import PhysicalPairGuard, _guard_worker, validate_physical_pair
    import multiprocessing
    n = 100 if quick else 400
    fake = FakeCompositor()
    try:
        client = fake.client()
        session = client.verified_session()['id']
        before = fake.stats()['counts']
        walls = []
        c0 = time.process_time()
        for _ in range(n):
            t = time.perf_counter()
            validate_physical_pair(client, session, 'DP-2', 'DP-1')
            walls.append(time.perf_counter() - t)
        cpu = time.process_time() - c0
        after = fake.stats()['counts']
        requests = sum(after.values()) - sum(before.values()) - 1  # minus the stats request itself
        monitor_dumps = after.get('j/monitors', 0) - before.get('j/monitors', 0)
        # The ownership broker verifies the session like this before every broker call (three per guard period).
        c0 = time.process_time()
        for _ in range(n): client.verified_session()
        session_cpu = time.process_time() - c0
        # A spawned guard worker (as Supervisor.run starts one per run): start until its first verdict.
        firsts = []
        context = multiprocessing.get_context('spawn')
        for _ in range(2 if quick else 5):
            receiver, sender = context.Pipe(duplex=False)
            worker = context.Process(target=_guard_worker, args=(PhysicalPairGuard(client, session, 'DP-2', 'DP-1'), sender))
            t = time.perf_counter(); worker.start(); sender.close()
            if not receiver.poll(10): raise RuntimeError('guard worker gave no verdict')
            verdict = receiver.recv_bytes(1)
            firsts.append(time.perf_counter() - t)
            worker.kill(); worker.join(); worker.close(); receiver.close()
            if verdict != b'V': raise RuntimeError('guard worker failed')
    finally:
        fake.close()
    return {'checks': n, 'requests_per_check': round(requests / n, 2), 'monitor_dumps_per_check': round(monitor_dumps / n, 2),
            'cpu_us_per_check': round(cpu / n * 1e6, 1), 'verified_session_cpu_us': round(session_cpu / n * 1e6, 1),
            'wall_us': summary(walls, 1e6), 'worker_first_verdict_ms': summary(firsts, 1e3)}


def bench_control_op(quick):
    from .ipc import PrivateServer
    n = 10 if quick else 30
    tmp = Path(tempfile.mkdtemp(prefix='bench-cp-'))
    try:
        runtime = tmp / 'run'; runtime.mkdir(mode=0o700)
        run_id, session = 'bench' + secrets.token_hex(4), 's' * 64
        with PrivateServer(runtime, run_id, session) as server:
            server.socket.settimeout(5)
            delivered, exits, received = [], [], []
            for i in range(n + 2):
                t = time.perf_counter()
                p = subprocess.Popen([sys.executable, '-I', str(HELPER), str(server.path), run_id, session,
                                      'resize_press' if i % 2 else 'resize_release'], stdin=subprocess.DEVNULL)
                conn, _ = server.socket.accept()
                with conn:
                    message = server.receive(conn); server.queue.pop()
                t_recv = time.perf_counter()
                p.wait()
                if i >= 2:  # the first two warm the page cache
                    delivered.append(t_recv - t); exits.append(time.perf_counter() - t); received.append(message['op'])
            # The server half alone: a whole frame already sent, then PrivateServer.receive (peer check, read, decode).
            frames = 2000 if quick else 10000
            base = time.time_ns()
            payloads = [(json.dumps({'schema': 1, 'request_id': base + k, 'run_id': run_id, 'session': session,
                                     'op': 'resize_press', 'payload': {}}, separators=(',', ':')) + '\n').encode()
                        for k in range(frames)]
            cpu = 0.0
            for raw in payloads:
                a, b = socket.socketpair()
                with a, b:
                    b.sendall(raw); b.close()
                    c0 = time.process_time()
                    server.receive(a); server.queue.pop()
                    cpu += time.process_time() - c0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    floor = [_run([sys.executable, '-I', '-c', 'pass']) for _ in range(n)]
    return {'presses': n, 'press_to_server_ms': summary(delivered, 1e3), 'helper_exit_ms': summary(exits, 1e3),
            'bare_interpreter_ms': summary(floor, 1e3), 'server_receive_us_per_frame': round(cpu / frames * 1e6, 1)}


def _run(argv, env=None):
    t = time.perf_counter()
    subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env, check=False)
    return time.perf_counter() - t


def bench_controller(quick):
    import tools.xr_controller.__main__ as cm
    from tools.xr_controller.state import Controller
    from tools.xr_probe.hardware import probe_drm, probe_usb
    ticks = 5 if quick else 15
    fake = FakeCompositor()
    saved_env = {k: os.environ.get(k) for k in ('HYPRLAND_INSTANCE_SIGNATURE', 'XDG_CONFIG_HOME')}
    real_run, calls = subprocess.run, []

    def counting_run(*a, **k):
        calls.append(a[0][0] if a and a[0] else '?')
        return real_run(*a, **k)
    try:
        os.environ['HYPRLAND_INSTANCE_SIGNATURE'] = INSTANCE
        os.environ['XDG_CONFIG_HOME'] = str(fake.tmp / 'config')
        state = fake.tmp / 'state'; state.mkdir(mode=0o700)
        data = fake.tmp / 'data'
        pin = data / 'viture-workspace' / 'bin' / ('0' * 12)
        pin.mkdir(parents=True)
        (data / 'viture-workspace' / 'installed.json').write_text(json.dumps(
            {'plugin': {'path': str(pin / 'libxr-ownership.so'), 'sha256': '1' * 64},
             'renderer': {'path': str(pin / 'xr-native-spike'), 'sha256': '2' * 64}}))
        for _ in range(STALE_RUNS):  # finished runs: their consent and result files stay in the state directory
            run_id = secrets.token_hex(16)
            for name, body in ((run_id + '.json', {'run_id': run_id, 'schema': 1}),
                               (run_id + '.result.json', {'run_id': run_id, 'phase': 'done', 'complete': True})):
                fd = os.open(state / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                os.write(fd, json.dumps(body).encode()); os.close(fd)
        host = cm.Host(fake.runtime, state, data)
        fd = os.open(state, os.O_RDONLY | os.O_DIRECTORY)
        subprocess.run = counting_run
        try:
            controller = cm.Controller(time.monotonic, host.probe, host.spawn, cm.Units(fake.runtime), cm.Store(), fd, state)
            walls, cpus, kids, per_tick = [], [], [], []
            for _ in range(ticks):
                calls.clear()
                t, c, k = time.perf_counter(), time.process_time(), children_cpu()
                controller.tick()
                walls.append(time.perf_counter() - t); cpus.append(time.process_time() - c); kids.append(children_cpu() - k)
                per_tick.append(len(calls))
            snap = []
            for _ in range(200):
                t = time.perf_counter(); controller.snapshot(); snap.append(time.perf_counter() - t)
        finally:
            subprocess.run = real_run
            os.close(fd)
        parts = {}
        for name, fn in (('probe_usb', probe_usb), ('probe_drm', probe_drm),
                         ('hyprland_monitors_and_plugins', lambda: (fake.client().query('monitors'), fake.client().plugins_loaded()))):
            ts = []
            for _ in range(20):
                t = time.perf_counter(); fn(); ts.append(time.perf_counter() - t)
            parts[name + '_ms'] = round(pct(ts, 0.5) * 1e3, 3)
        assert controller.state in ('idle', 'no-glasses'), controller.state
    finally:
        for k, v in saved_env.items():
            if v is None: os.environ.pop(k, None)
            else: os.environ[k] = v
        fake.close()
    return {'stale_runs': STALE_RUNS, 'ticks': ticks, 'subprocesses_per_tick': summary(per_tick, 1, 1),
            'tick_wall_ms': summary(walls, 1e3), 'tick_cpu_ms': summary(cpus, 1e3),
            'tick_children_cpu_ms': summary(kids, 1e3),
            'service_cpu_pct_at_1hz': round((statistics.fmean(cpus) + statistics.fmean(kids)) * 100, 2),
            'snapshot_us': summary(snap, 1e6), 'parts': parts, 'state': controller.state}


def bench_startup(quick):
    n = 8 if quick else 20
    def p50(argv): return round(pct([_run(argv) for _ in range(n)], 0.5) * 1e3, 1)
    prefix = f'import sys; sys.path.insert(0, {str(REPO)!r}); '
    return {'bare_interpreter_ms': p50([sys.executable, '-I', '-c', 'pass']),
            'bind_helper_ms': p50([sys.executable, '-I', str(HELPER)]),
            'controller_imports_ms': p50([sys.executable, '-I', '-c', prefix + 'import tools.xr_controller.__main__']),
            'runner_imports_ms': p50([sys.executable, '-I', '-c', prefix + 'import tools.xr_controller.runner, '
                                      'tools.native_spike.view, tools.native_spike.input_lock, tools.native_spike.send, '
                                      'tools.native_spike.video, tools.native_spike.profiles'])}


BENCHES = {'hypr_query': bench_hypr_query, 'overview': bench_overview, 'watchdog': bench_watchdog, 'guard': bench_guard,
           'control_op': bench_control_op, 'controller': bench_controller, 'startup': bench_startup}


def main(argv=None):
    ap = argparse.ArgumentParser(prog='python3 -m tools.native_spike.bench_control', description=__doc__.split('\n')[0])
    ap.add_argument('--quick', action='store_true', help='fewer iterations')
    ap.add_argument('--json', type=Path, help='also write the results here')
    ap.add_argument('--only', help='comma-separated benchmark names: ' + ', '.join(BENCHES))
    args = ap.parse_args(argv)
    names = args.only.split(',') if args.only else list(BENCHES)
    results = {}
    for name in names:
        results[name] = BENCHES[name](args.quick)
        print(f'{name}: {json.dumps(results[name])}', flush=True)
    if args.json: args.json.write_text(json.dumps(results, indent=2) + '\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
