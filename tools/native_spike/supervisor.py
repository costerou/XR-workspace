"""Independent watchdog. Caller owns its Popen child, heartbeat FD and recovery adapter."""
import math
import selectors
import time


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
        self.next_guard = 0.0
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
        with selectors.DefaultSelector() as selector:
            selector.register(heartbeat_socket, selectors.EVENT_READ)
            while not self.finished:
                now = time.monotonic()
                if self.guard is not None and self.stopping_at is None and now >= self.next_guard:
                    try: self.guard()
                    except (OSError, ValueError, KeyError, TypeError): self.disconnect(time.monotonic())
                    self.next_guard = time.monotonic() + 0.5
                for key, _ in selector.select(interval):
                    data = heartbeat_socket.recv(256)
                    now = time.monotonic()
                    if data and all(byte == ord("H") for byte in data): self.heartbeat(now)
                    else:
                        self.disconnect(now)
                        selector.unregister(heartbeat_socket)
                self.tick(time.monotonic())
        return self.result


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
        matches = [m for m in snapshot if m['name'] == name and not m['disabled'] and m['dpmsStatus']]
        if len(matches) != 1: raise ValueError('approved usable physical output missing')


def run_presentation_only(client, run_id, session, output, recovery_output, wayland_socket, *, popen=None):
    """Consume socket; caller has checked presentation-only consent. No journal."""
    with wayland_socket:
        validate_physical_pair(client, session, output, recovery_output)
        return _launch(client, run_id, session, output, wayland_socket, None, popen=popen,
                       guard=lambda: validate_physical_pair(client, session, output, recovery_output))


def run_presentation(journal, adapter, output, wayland_socket, *, popen=None):
    """Managed path remains ownership-gated; consumes socket even on rejection."""
    from .journal import recover
    try:
        adapter.client.require_output_ownership()
        if adapter.query('session') != {'id': journal.session}: raise ValueError('launcher session mismatch')
        return _launch(adapter.client, journal.run_id, journal.session, output, wayland_socket,
                       lambda: recover(journal, adapter), popen=popen)
    finally:
        if wayland_socket is not None: wayland_socket.close()


def _launch(client, run_id, session, output, wayland_socket, recovery, *, popen=None, guard=None):
    """Shared child lifetime only; authorization remains in distinct callers."""
    import json
    import os
    from pathlib import Path
    import socket
    import struct
    import subprocess
    from .ipc import token
    token(output); token(run_id); token(session)
    peer = struct.unpack('3i', wayland_socket.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
    if peer[1] != os.getuid() or peer != client._peer[:3]: raise PermissionError('Wayland/IPC peer mismatch')
    renderer = Path(__file__).resolve().parents[2] / 'build/native-spike/xr-native-spike'
    parent, child_socket = socket.socketpair()
    with parent, child_socket:
        argv = [str(renderer), '--supervised-fd', str(child_socket.fileno()),
                '--wayland-fd', str(wayland_socket.fileno()), '--output', output,
                '--run-id', run_id, '--session', session]
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
