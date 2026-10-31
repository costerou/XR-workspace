"""Independent watchdog. Caller owns its Popen child, heartbeat FD and recovery adapter."""
import math
import multiprocessing
from dataclasses import dataclass
import selectors
import time


# Two sequential IPC requests, each bounded at two seconds, plus scheduling
# allowance. This is separate from (and never extends) heartbeat's two seconds.
GUARD_DEADLINE = 4.25
GUARD_PERIOD = 0.5


def _guard_worker(guard, sender):
    """Spawn-only worker: private deserialized client, no inherited renderer FDs."""
    import signal
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    with sender:
        while True:
            try:
                guard()
            except Exception:
                sender.send_bytes(b'F')
                return
            sender.send_bytes(b'V')
            time.sleep(GUARD_PERIOD)


@dataclass
class PhysicalPairGuard:
    client: object
    session: str
    output: str
    recovery_output: str

    def __call__(self):
        validate_physical_pair(self.client, self.session, self.output, self.recovery_output)


class Supervisor:
    def __init__(self, child, recovery, *, now=None, grace=0.5, guard=None):
        if not math.isfinite(grace) or grace < 0: raise ValueError("invalid grace")
        self.child, self.recovery = child, recovery
        self.last_heartbeat = time.monotonic() if now is None else now
        self.grace = grace
        self.stopping_at = None
        self.finished = False
        self.kill_sent = False
        self.result = None
        self.guard = guard
    def heartbeat(self, now):
        if self.stopping_at is None and math.isfinite(now) and now >= self.last_heartbeat:
            self.last_heartbeat = now
    def disconnect(self, now):
        if self.finished or self.stopping_at is not None: return
        self.stopping_at = now
        if self.child.poll() is None:
            try: self.child.terminate()
            except ProcessLookupError: pass
    def tick(self, now):
        if self.finished: return
        if self.child.poll() is None:
            if self.stopping_at is None and now - self.last_heartbeat >= 2:
                self.disconnect(now)
            if self.stopping_at is not None and not self.kill_sent and now - self.stopping_at >= self.grace:
                self.kill_sent = True
                try: self.child.kill()
                except ProcessLookupError: pass
            if self.child.poll() is None: return
        self.result = self.recovery()
        self.finished = True
    def run(self, heartbeat_socket, *, interval=0.05):
        """Run in supervisor process, not renderer. EOF means disconnect; only b'H' is valid."""
        if not 0 < interval <= 0.1: raise ValueError("invalid poll interval")
        # Own exactly one spawn worker and one one-way pipe for this run. Spawn
        # (never fork) excludes renderer/control/Wayland sockets and copies client
        # identity state; no thread or process concurrently mutates that client.
        worker = receiver = sender = None
        deadline = 0.0
        try:
            if self.guard is not None and self.child.poll() is None and self.stopping_at is None:
                context = multiprocessing.get_context('spawn')
                receiver, sender = context.Pipe(duplex=False)
                worker = context.Process(target=_guard_worker, args=(self.guard, sender))
                deadline = time.monotonic() + GUARD_DEADLINE
                worker.start()
                sender.close()
            with selectors.DefaultSelector() as selector:
                selector.register(heartbeat_socket, selectors.EVENT_READ)
                while not self.finished:
                    now = time.monotonic()
                    # Expiry precedes reading queued heartbeats: delayed bytes
                    # cannot revive an already expired watchdog.
                    self.tick(now)
                    if self.finished: break
                    if worker is not None and receiver is not None and self.stopping_at is None:
                        if now >= deadline or not worker.is_alive():
                            self.disconnect(now)
                        elif receiver.poll():
                            try: valid = receiver.recv_bytes(1) == b'V'
                            except (EOFError, OSError): valid = False
                            if not valid: self.disconnect(now)
                            else: deadline = now + GUARD_PERIOD + GUARD_DEADLINE
                    for key, _ in selector.select(interval):
                        now = time.monotonic()
                        self.tick(now)
                        if self.finished: break
                        data = heartbeat_socket.recv(256)
                        if data and all(byte == ord("H") for byte in data): self.heartbeat(now)
                        else:
                            self.disconnect(now)
                            selector.unregister(heartbeat_socket)
                    self.tick(time.monotonic())
            return self.result
        finally:
            # Reaping the renderer is never delayed by waiting on IPC. Once this
            # loop exits (including exceptions/interrupt), kill the exact worker,
            # join/reap it and close all pipe/process handles. No orphan threads.
            if worker is not None:
                if worker.pid is not None:
                    if worker.is_alive(): worker.kill()
                    worker.join()
                worker.close()
            if sender is not None: sender.close()
            if receiver is not None: receiver.close()


def validate_physical_pair(client, session, output, recovery_output):
    """Read-only conservative connector check; not output ownership proof."""
    import re
    from .hyprland import monitors
    from .ipc import token
    token(output); token(recovery_output)
    if output == recovery_output: raise ValueError('independent physical recovery output required')
    if client.verified_session() != {'id': session}: raise ValueError('launcher session mismatch')
    snapshot = monitors(client.query('monitors'))
    for name in (output, recovery_output):
        if not re.fullmatch(r'(?:DP|HDMI-A|eDP|DVI-D)-[0-9]+', name):
            raise ValueError('approved output must be a physical connector')
        matches = [m for m in snapshot if m['name'] == name and not m['disabled'] and m['dpmsStatus'] and m['mirrorOf'] == 'none']
        if len(matches) != 1: raise ValueError('approved usable physical output missing')


def validate_pattern_duration(value):
    if value is not None and (type(value) is not int or not 1 <= value <= 60000):
        raise ValueError('animation duration must be integer 1..60000 ms')


def run_presentation_only(client, run_id, session, output, recovery_output, wayland_socket, *, popen=None, animate_pattern_ms=None):
    """Consume socket; caller has checked presentation-only consent. No journal."""
    with wayland_socket:
        validate_pattern_duration(animate_pattern_ms)
        validate_physical_pair(client, session, output, recovery_output)
        return _launch(client, run_id, session, output, wayland_socket, None, popen=popen,
                       guard=PhysicalPairGuard(client, session, output, recovery_output), animate_pattern_ms=animate_pattern_ms)


def run_presentation(journal, adapter, output, wayland_socket, *, popen=None):
    """Managed path remains ownership-gated; consumes socket even on rejection."""
    from .journal import recover
    try:
        adapter.require_output_ownership()
        if adapter.query('session') != {'id': journal.session}: raise ValueError('launcher session mismatch')
        return _launch(adapter.client, journal.run_id, journal.session, output, wayland_socket,
                       lambda: recover(journal, adapter), popen=popen)
    finally:
        if wayland_socket is not None: wayland_socket.close()


CAPTURE_SCOPE = 'managed-outputs'
CAPTURE_ALLOW = ['headless-outputs', 'capture-managed-outputs']


def capture_arguments(names, output):
    """Renderer argv for capture of the run's own managed outputs only (XR-1..XR-3), never `output`."""
    import re
    if names is None: return []
    if (not isinstance(names, (list, tuple)) or not 1 <= len(names) <= 3 or len(set(names)) != len(names)
            or any(not isinstance(n, str) or not re.fullmatch(r'XR-[1-3]', n) or n == output for n in names)):
        raise ValueError("capture is limited to the run's own managed outputs")
    return ['--capture', ','.join(sorted(names))]


def _launch(client, run_id, session, output, wayland_socket, recovery, *, popen=None, guard=None, animate_pattern_ms=None,
            capture=None):
    """Shared child lifetime only; authorization remains in distinct callers."""
    import json
    import os
    from pathlib import Path
    import socket
    import struct
    import subprocess
    from .ipc import token
    validate_pattern_duration(animate_pattern_ms)
    if capture is not None and animate_pattern_ms is not None: raise ValueError('capture and generated animation are exclusive')
    capture_argv = capture_arguments(capture, output)
    token(output); token(run_id); token(session)
    peer = struct.unpack('3i', wayland_socket.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
    if peer[1] != os.getuid() or peer != client._peer[:3]: raise PermissionError('Wayland/IPC peer mismatch')
    renderer = Path(__file__).resolve().parents[2] / 'build/native-spike/xr-native-spike'
    parent, child_socket = socket.socketpair()
    with parent, child_socket:
        argv = [str(renderer), '--supervised-fd', str(child_socket.fileno()),
                '--wayland-fd', str(wayland_socket.fileno()), '--output', output,
                '--run-id', run_id, '--session', session]
        if animate_pattern_ms is not None:
            argv += ['--animate-pattern-ms', str(animate_pattern_ms)]
        argv += capture_argv
        try:
            child = (popen or subprocess.Popen)(argv, shell=False, close_fds=True,
                        pass_fds=(child_socket.fileno(), wayland_socket.fileno()))
        finally:
            # No parent reference may retain compositor surfaces after child exit.
            wayland_socket.close()
        child_socket.close()
        watchdog = Supervisor(child, recovery if recovery is not None else lambda: child.poll(), guard=guard)
        try:
            try:
                parent.settimeout(0.25)
                parent.sendall((json.dumps({'schema': 1, 'request_id': 1, 'run_id': run_id,
                    'session': session, 'op': 'start', 'payload': {}}) + '\n').encode())
                parent.settimeout(None)
            except OSError:
                watchdog.disconnect(time.monotonic())
            return watchdog.run(parent)
        except KeyboardInterrupt:
            watchdog.disconnect(time.monotonic())
            while not watchdog.finished:
                watchdog.tick(time.monotonic())
                if not watchdog.finished: time.sleep(0.05)
            return watchdog.result
        finally:
            # Also reap on selector/transport errors. If recovery itself raised,
            # the child is already reaped: do not retry a failed journal write.
            if child.poll() is None:
                watchdog.disconnect(time.monotonic())
                while not watchdog.finished:
                    watchdog.tick(time.monotonic())
                    if not watchdog.finished: time.sleep(0.05)
