"""Independent watchdog. Caller owns its Popen child, heartbeat FD and recovery adapter."""
import math
import selectors
import time


class Supervisor:
    def __init__(self, child, recovery, *, now=None, grace=0.5):
        if not math.isfinite(grace) or grace < 0: raise ValueError("invalid grace")
        self.child, self.recovery = child, recovery
        self.last_heartbeat = time.monotonic() if now is None else now
        self.grace = grace
        self.stopping_at = None
        self.finished = False
        self.kill_sent = False
        self.result = None
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
                for key, _ in selector.select(interval):
                    data = heartbeat_socket.recv(256)
                    now = time.monotonic()
                    if data and all(byte == ord("H") for byte in data): self.heartbeat(now)
                    else:
                        self.disconnect(now)
                        selector.unregister(heartbeat_socket)
                self.tick(time.monotonic())
        return self.result


def run_presentation(journal, adapter, output, wayland_socket, *, popen=None):
    """Internal launcher wiring, not public IPC and not a consent grant.

    Caller must have validated run-scoped P2 consent and created/verified its
    outputs. The production adapter currently blocks before spawn because it
    cannot prove ownership. A preconnected Wayland FD must have the same actual
    compositor peer as the raw IPC transport; ambient WAYLAND_DISPLAY is unused.
    """
    import json
    import os
    from pathlib import Path
    import socket
    import struct
    import subprocess
    from .ipc import token
    from .journal import recover
    token(output)
    adapter.client.require_output_ownership()
    if adapter.query('session') != {'id': journal.session}: raise ValueError('launcher session mismatch')
    peer = struct.unpack('3i', wayland_socket.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
    if peer[1] != os.getuid() or peer != adapter.client._peer[:3]: raise PermissionError('Wayland/IPC peer mismatch')
    renderer = Path(__file__).resolve().parents[2] / 'build/native-spike/xr-native-spike'
    parent, child_socket = socket.socketpair()
    with parent, child_socket:
        argv = [str(renderer), '--supervised-fd', str(child_socket.fileno()),
                '--wayland-fd', str(wayland_socket.fileno()), '--output', output,
                '--run-id', journal.run_id, '--session', journal.session]
        child = (popen or subprocess.Popen)(argv, shell=False, close_fds=True,
                    pass_fds=(child_socket.fileno(), wayland_socket.fileno()))
        child_socket.close()
        watchdog = Supervisor(child, lambda: recover(journal, adapter))
        try:
            parent.settimeout(0.25)
            parent.sendall((json.dumps({'schema': 1, 'request_id': 1, 'run_id': journal.run_id,
                'session': journal.session, 'op': 'start', 'payload': {}}) + '\n').encode())
            parent.settimeout(None)
        except OSError:
            watchdog.disconnect(time.monotonic())
        try:
            return watchdog.run(parent)
        except KeyboardInterrupt:
            watchdog.disconnect(time.monotonic())
            # Reap the exact owned child before any recovery, including startup failure.
            while not watchdog.finished:
                watchdog.tick(time.monotonic())
                if not watchdog.finished: time.sleep(0.05)
            return watchdog.result
