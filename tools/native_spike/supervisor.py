"""Independent watchdog. Caller owns its Popen child, heartbeat FD and recovery adapter."""
import math
from dataclasses import dataclass
import select
import selectors
import socket
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
    def __init__(self, child, recovery, *, now=None, grace=0.5, guard=None, control=None, status_socket=None):
        if not math.isfinite(grace) or grace < 0: raise ValueError("invalid grace")
        self.child, self.recovery = child, recovery
        self.last_heartbeat = time.monotonic() if now is None else now
        self.grace = grace
        self.stopping_at = None
        self.finished = False
        self.kill_sent = False
        self.result = None
        self.stop_reason = None  # why the supervisor ended the renderer, for the run's result; None if it ended by itself
        self.guard = guard
        self.status_socket = status_socket
        self.status = {}
        self._status_buffer = b''
        self._status_discard = False
        self.control = control  # optional ViewControl: user ops on the run's private control socket
        # The guard's spawn worker (run()); replace_guard swaps it while the loop runs (desktops.py).
        self._guard_worker = self._guard_receiver = self._guard_ready = None
        self._guard_deadline = 0.0
        self.guard_paused = False
        self.guard_restarts = 0
        attach = getattr(control, 'attach_supervisor', None)  # video.VideoControl reads the renderer's measured status
        if callable(attach): attach(self)
    def heartbeat(self, now):
        if self.stopping_at is None and math.isfinite(now) and now >= self.last_heartbeat:
            self.last_heartbeat = now
    def disconnect(self, now, reason=None):
        if self.finished or self.stopping_at is not None: return
        self.stopping_at = now
        if self.child.poll() is None:
            self.stop_reason = reason  # a renderer already gone was not stopped by anyone: its exit code tells that story
            try: self.child.terminate()
            except ProcessLookupError: pass
    def tick(self, now):
        if self.finished: return
        if self.child.poll() is None:
            if self.stopping_at is None and now - self.last_heartbeat >= 2:
                self.disconnect(now, 'heartbeat-timeout')
            if self.stopping_at is not None and not self.kill_sent and now - self.stopping_at >= self.grace:
                self.kill_sent = True
                try: self.child.kill()
                except ProcessLookupError: pass
            if self.child.poll() is None: return
        self.result = self.recovery()
        self.finished = True
    def status_bytes(self, data):
        from .ipc import strict_json
        # Bounded streaming parser, also used for the atomic datagrams from the renderer.
        for chunk in data.splitlines(keepends=True):
            if not self._status_discard: self._status_buffer += chunk
            if len(self._status_buffer) > 16384:
                self._status_buffer = b''; self._status_discard = True
            if not chunk.endswith(b'\n'): continue
            if not self._status_discard:
                try:
                    value = strict_json(self._status_buffer)
                    if isinstance(value, dict) and set(value) == {'overview_geometry'}:
                        # The overview's canvas and desktop slots, sent when they change (free pointer hit testing).
                        from .free_pointer import parse_overview_geometry
                        geometry = parse_overview_geometry(value['overview_geometry'])
                        handler = getattr(self.control, 'set_overview_geometry', None)
                        if handler is not None: handler(geometry)
                    elif isinstance(value, dict) and set(value) == {'roll_geometry'}:
                        # How far each desktop is from level (the free pointer's roll-level icons).
                        from .free_pointer import parse_roll_geometry
                        tilts = parse_roll_geometry(value['roll_geometry'])
                        handler = getattr(self.control, 'set_roll_geometry', None)
                        if handler is not None: handler(tilts)
                    elif isinstance(value, dict) and set(value) == {'help_geometry'}:
                        # The help card on the overview's canvas while it is up (free pointer clicks on its page arrows).
                        from .free_pointer import parse_help_geometry
                        handler = getattr(self.control, 'set_help_geometry', None)
                        try: geometry = parse_help_geometry(value['help_geometry'])
                        except ValueError: geometry = None  # never hit-test a stale card; the renderer sends it again
                        if handler is not None: handler(geometry)
                    else:
                        if not isinstance(value, dict) or set(value)-{'presented_hz','missed','capture_path','pose_age_ms','video','tracking','capture_failure_run'}:
                            raise ValueError('invalid status')
                        for k,v in value.items():
                            if k == 'capture_path': valid = v in ('dmabuf','shm')
                            elif k == 'video': valid = v in ('off','loading','playing','paused','ended','error')  # video.VIDEO_STATES
                            elif k == 'tracking': valid = type(v) is bool
                            elif k in ('missed', 'capture_failure_run'): valid = type(v) is int and v >= 0
                            else: valid = type(v) in (int,float) and math.isfinite(v) and v >= 0
                            if not valid: raise ValueError('invalid measured value')
                        self.status = value
                except (ValueError, TypeError): pass
            self._status_buffer = b''; self._status_discard = False

    # --- the guard worker ---------------------------------------------------------------------------------------------
    def _start_guard(self):
        """Own exactly one spawn worker and one one-way pipe for the current guard. Spawn (never fork) excludes
        renderer/control/Wayland sockets and copies client identity state; no thread or process concurrently mutates that
        client."""
        if self.guard is None or self.child.poll() is not None or self.stopping_at is not None: return
        import multiprocessing  # only runs with a guard use it; the controller imports this module too
        context = multiprocessing.get_context('spawn')
        receiver, sender = context.Pipe(duplex=False)
        worker = context.Process(target=_guard_worker, args=(self.guard, sender))
        self._guard_deadline = time.monotonic() + GUARD_DEADLINE
        try:
            worker.start()
        except BaseException:
            receiver.close()
            raise
        finally:
            sender.close()
        self._guard_worker, self._guard_receiver = worker, receiver
        # receiver.poll() builds a new selector on every call, and this runs on every loop pass (each heartbeat
        # and pointer sample): one poll object, the same readiness (any revents: a verdict, or EOF / error).
        self._guard_ready = select.poll()
        self._guard_ready.register(receiver.fileno(), select.POLLIN)

    def _stop_guard(self):
        """Kill the exact worker, join/reap it and close its pipe. No orphans."""
        worker, receiver = self._guard_worker, self._guard_receiver
        self._guard_worker = self._guard_receiver = self._guard_ready = None
        if worker is not None:
            if worker.pid is not None:
                if worker.is_alive(): worker.kill()
                worker.join()
            worker.close()
        if receiver is not None: receiver.close()

    def pause_guard(self):
        """A desktop change starts (desktops.py): the guard's fixed output list is about to stop being true, so its
        worker goes. The change's own broker readbacks stand in until replace_guard."""
        self._stop_guard()
        self.guard_paused = True

    def replace_guard(self, guard):
        """The change is done: a new guard for the run's outputs as they are now (None keeps none)."""
        self._stop_guard()
        self.guard_paused = False
        self.guard = guard
        self.guard_restarts += 1
        self._start_guard()

    def _check_guard(self, now):
        worker, receiver = self._guard_worker, self._guard_receiver
        if worker is None or receiver is None or self.stopping_at is not None: return
        if now >= self._guard_deadline: self.disconnect(now, 'guard-timeout')
        elif not worker.is_alive(): self.disconnect(now, 'guard-failed')
        elif self._guard_ready.poll(0):
            try: valid = receiver.recv_bytes(1) == b'V'
            except (EOFError, OSError): valid = False
            if not valid: self.disconnect(now, 'guard-failed')
            else: self._guard_deadline = now + GUARD_PERIOD + GUARD_DEADLINE

    def _control_ready(self, renderer_socket):
        """One bounded user op (peer UID, framing, replay checked by PrivateServer); never fatal."""
        try:
            conn, _ = self.control.server.socket.accept()
        except OSError:
            return
        with conn:
            try:
                message = self.control.server.receive(conn)
                self.control.server.queue.pop()
            except (ValueError, OSError, PermissionError):
                return
            if message['op'] == 'status':
                import json
                reply = dict(schema=1, request_id=message['request_id'], run_id=message['run_id'], session=message['session'],
                             **self.control.status_reply(self.status))
                try: conn.sendall((json.dumps(reply, allow_nan=False)+'\n').encode())
                except OSError: pass
                return
        self._send(self.control.on_message(message), renderer_socket)

    def _events_ready(self, renderer_socket, selector):
        """Hyprland event bytes (cursor follow). EOF/error just disables it; never fatal."""
        try: data = self.control.events.recv(65536)
        except BlockingIOError: return
        except OSError: data = b''
        if not data:
            selector.unregister(self.control.events)
            return
        self._send(self.control.on_events(data), renderer_socket)

    def _send(self, frame, renderer_socket):
        try:
            if frame is None or self.stopping_at is not None: return
            timed = False
            try:
                data = None if getattr(frame, 'fds', None) else bytes(frame)
                if data is not None:
                    # One non-blocking send first: a frame (up to 250 a second while the overview pointer moves) almost
                    # always fits the socket buffer. Only what does not fit takes the bounded blocking send below, with its
                    # two timeout switches; the bytes, their order and the 0.25 s bound are the same.
                    try: sent = renderer_socket.send(data, socket.MSG_DONTWAIT)
                    except BlockingIOError: sent = 0
                    if sent == len(data): return
                timed = True
                renderer_socket.settimeout(0.25)
                if data is None: _send_frame(renderer_socket, frame)
                else: renderer_socket.sendall(data[sent:])
            except OSError:
                self.disconnect(time.monotonic(), 'send-failed')
            finally:
                if timed: renderer_socket.settimeout(None)
        finally:
            close_fds = getattr(frame, 'close_fds', None)  # video.FdFrame: our copies of descriptors handed over
            if close_fds is not None: close_fds()

    def run(self, heartbeat_socket, *, interval=0.05, status_socket=None):
        """Run in supervisor process, not renderer. EOF means disconnect; only b'H' is valid."""
        if status_socket is None: status_socket = self.status_socket
        if not 0 < interval <= 0.1: raise ValueError("invalid poll interval")
        try:
            self._start_guard()
            with selectors.DefaultSelector() as selector:
                selector.register(heartbeat_socket, selectors.EVENT_READ)
                if status_socket is not None:
                    status_socket.setblocking(False)
                    selector.register(status_socket, selectors.EVENT_READ)
                if self.control is not None: selector.register(self.control.server.socket, selectors.EVENT_READ)
                if self.control is not None and getattr(self.control, 'events', None) is not None:
                    selector.register(self.control.events, selectors.EVENT_READ)
                wake = getattr(self.control, 'wake', None)  # overview free pointer sampler: run poll() now, not at the next tick
                if not isinstance(wake, socket.socket): wake = None
                if wake is not None: selector.register(wake, selectors.EVENT_READ)
                while not self.finished:
                    now = time.monotonic()
                    # Expiry precedes reading queued heartbeats: delayed bytes
                    # cannot revive an already expired watchdog.
                    self.tick(now)
                    if self.finished: break
                    self._check_guard(now)
                    for key, _ in selector.select(interval):
                        now = time.monotonic()
                        self.tick(now)
                        if self.finished: break
                        if self.control is not None and key.fileobj is self.control.server.socket:
                            self._control_ready(heartbeat_socket)
                            continue
                        if self.control is not None and key.fileobj is getattr(self.control, 'events', None):
                            self._events_ready(heartbeat_socket, selector)
                            continue
                        if wake is not None and key.fileobj is wake:
                            self.control.overview_pointer.drain()  # poll() below applies the new pointer sample
                            continue
                        if key.fileobj is status_socket:
                            try: data = status_socket.recv(16385)
                            except BlockingIOError: continue
                            except OSError: data = b''
                            if data: self.status_bytes(data)
                            else: selector.unregister(status_socket)
                            continue
                        try: data = heartbeat_socket.recv(256)
                        except OSError: data = b''  # reset (renderer gone with unread frames) == disconnect
                        if data and all(byte == ord("H") for byte in data): self.heartbeat(now)
                        else:
                            self.disconnect(now, 'heartbeat-invalid' if data else 'renderer-closed')
                            selector.unregister(heartbeat_socket)
                    self.tick(time.monotonic())
                    poll = getattr(self.control, 'poll', None)  # free pointer, debounced panel save, overlay idle close
                    if poll is not None:
                        out = poll(time.monotonic())
                        if isinstance(out, (bytes, bytearray)) and out: self._send(out, heartbeat_socket)  # keeps FdFrame descriptors
            return self.result
        finally:
            # Reaping the renderer is never delayed by waiting on IPC. Once this
            # loop exits (including exceptions/interrupt), kill the exact worker,
            # join/reap it and close all pipe/process handles. No orphan threads.
            self._stop_guard()


def _send_frame(sock, frame):
    """Frame bytes; a video.FdFrame's descriptors travel with its first byte (SCM_RIGHTS on the stream socketpair)."""
    import socket
    fds = getattr(frame, 'fds', None)
    data = bytes(frame)
    if not fds:
        sock.sendall(data); return
    sent = socket.send_fds(sock, [data], list(fds))
    if sent < len(data): sock.sendall(data[sent:])


def validate_physical_pair(client, session, output, recovery_output, recovery_asleep_ok=False):
    """Read-only conservative connector check; not output ownership proof. `recovery_asleep_ok`: the run's guard lets
    the recovery output be DPMS-off (privacy mode, privacy.py); it must still be connected, enabled and not mirrored."""
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
        asleep_ok = recovery_asleep_ok and name == recovery_output
        matches = [m for m in snapshot if m['name'] == name and not m['disabled'] and (m['dpmsStatus'] or asleep_ok)
                   and m['mirrorOf'] == 'none']
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
    """Renderer argv for capture of the run's own managed outputs only (XR-1..XR-MAX_DESKTOPS), never `output`."""
    import re
    from .geometry import MAX_DESKTOPS
    if names is None: return []
    if (not isinstance(names, (list, tuple)) or not 1 <= len(names) <= MAX_DESKTOPS or len(set(names)) != len(names)
            or any(not isinstance(n, str) or not re.fullmatch(f'XR-[1-{MAX_DESKTOPS}]', n) or n == output for n in names)):
        raise ValueError("capture is limited to the run's own managed outputs")
    return ['--capture', ','.join(sorted(names))]


def _launch(client, run_id, session, output, wayland_socket, recovery, *, popen=None, guard=None, animate_pattern_ms=None,
            capture=None, control=None, pose_feed=False, renderer=None, report=None):
    """Shared child lifetime only; authorization remains in distinct callers. `report`: a dict that gets the stop reason."""
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
    if pose_feed:
        if not capture_argv: raise ValueError('head tracking requires managed-output capture')
        capture_argv += ['--pose-feed', 'xrld']
    token(output); token(run_id); token(session)
    peer = struct.unpack('3i', wayland_socket.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
    if peer[1] != os.getuid() or peer != client._peer[:3]: raise PermissionError('Wayland/IPC peer mismatch')
    if renderer is None: renderer = Path(__file__).resolve().parents[2] / 'build/native-spike/xr-native-spike'
    parent, child_socket = socket.socketpair()
    status_parent, status_child = socket.socketpair(type=socket.SOCK_DGRAM)
    with parent, child_socket, status_parent, status_child:
        argv = [str(renderer), '--supervised-fd', str(child_socket.fileno()),
                '--wayland-fd', str(wayland_socket.fileno()), '--output', output,
                '--run-id', run_id, '--session', session]
        argv += ['--status-fd', str(status_child.fileno())]
        if animate_pattern_ms is not None:
            argv += ['--animate-pattern-ms', str(animate_pattern_ms)]
        argv += capture_argv
        try:
            # No renderer disk writes: GPU drivers would otherwise persist a shader cache.
            env = dict(os.environ, MESA_SHADER_CACHE_DISABLE='true')
            child = (popen or subprocess.Popen)(argv, shell=False, close_fds=True, stdin=subprocess.DEVNULL, env=env,
                        pass_fds=(child_socket.fileno(), wayland_socket.fileno(), status_child.fileno()))
        finally:
            # No parent reference may retain compositor surfaces after child exit.
            wayland_socket.close()
        child_socket.close()
        status_child.close()
        watchdog = Supervisor(child, recovery if recovery is not None else lambda: child.poll(), guard=guard, control=control, status_socket=status_parent)
        try:
            try:
                parent.settimeout(0.25)
                parent.sendall((json.dumps({'schema': 1, 'request_id': 1, 'run_id': run_id,
                    'session': session, 'op': 'start', 'payload': {}}) + '\n').encode())
                if control is not None: parent.sendall(control.initial_frame())  # saved spacing, overview
                parent.settimeout(None)
            except OSError:
                watchdog.disconnect(time.monotonic(), 'send-failed')
            return watchdog.run(parent)
        except KeyboardInterrupt:
            watchdog.disconnect(time.monotonic(), 'interrupted')
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
            if report is not None: report['stop_reason'] = watchdog.stop_reason
