"""Strict, bounded local control transport. No command execution in this module."""
from collections import deque
import json
import os
from pathlib import Path
import re
import socket
import stat
import struct

MAX_FRAME = 16384
TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")


def token(value):
    if not isinstance(value, str) or not TOKEN.fullmatch(value):
        raise ValueError("invalid identifier")
    return value


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result: raise ValueError("duplicate key")
        result[key] = value
    return result


def strict_json(raw):
    def invalid(value): raise ValueError("nonfinite number")
    try:
        return json.loads(raw, object_pairs_hook=_pairs, parse_constant=invalid)
    except (UnicodeError, RecursionError, OverflowError) as exc:
        raise ValueError("invalid JSON") from exc


def decode_control(raw):
    if not isinstance(raw, bytes) or len(raw) > MAX_FRAME or not raw.endswith(b"\n") or b"\n" in raw[:-1]:
        raise ValueError("invalid frame")
    message = strict_json(raw.decode("utf-8", errors="strict"))
    fields = {"schema", "request_id", "run_id", "session", "op", "payload"}
    if not isinstance(message, dict) or set(message) != fields: raise ValueError("invalid fields")
    if type(message["schema"]) is not int or message["schema"] != 1: raise ValueError("invalid schema")
    rid = message["request_id"]
    if type(rid) is not int or not 0 < rid <= 2**64 - 1: raise ValueError("invalid request id")
    token(message["run_id"]); token(message["session"])
    op, payload = message["op"], message["payload"]
    if op not in ("status", "start", "stop", "select", "recover", "zoom_in", "zoom_out", "next", "prev",
                  "spacing_up", "spacing_down", "recenter", "mode_next", "cursor_follow", "prediction"): raise ValueError("invalid operation")
    if not isinstance(payload, dict): raise ValueError("invalid payload")
    if op == "select":
        if set(payload) != {"output_id"}: raise ValueError("invalid select")
        token(payload["output_id"])
    elif payload: raise ValueError("unexpected payload")
    return message


class ControlQueue:
    def __init__(self, run_id, session):
        self.run_id, self.session = token(run_id), token(session)
        self.last = 0
        self.messages = deque()
    def push(self, raw):
        msg = decode_control(raw)
        if msg["run_id"] != self.run_id or msg["session"] != self.session: raise ValueError("wrong run/session")
        if msg["request_id"] <= self.last: raise ValueError("replayed request")
        if len(self.messages) >= 8: raise ValueError("queue full")
        self.messages.append(msg); self.last = msg["request_id"]
        return msg
    def pop(self):
        return self.messages.popleft()


def private_dir(path, create=False):
    """Open every component no-follow, then validate the private leaf directory."""
    path = Path(path).absolute()
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        parts = path.parts[1:]
        for i, part in enumerate(parts):
            if part in (".", ".."): raise ValueError("unsafe path")
            if create and i == len(parts) - 1:
                try: os.mkdir(part, 0o700, dir_fd=fd)
                except FileExistsError: pass
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd); fd = nxt
        st = os.fstat(fd)
        if st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) != 0o700:
            raise PermissionError("directory must be owned and mode 0700")
        return fd
    except BaseException:
        os.close(fd); raise


def check_peer(conn, uid):
    _, peer_uid, _ = struct.unpack("3i", conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
    if peer_uid != uid: raise PermissionError("wrong peer UID")


class PrivateServer:
    def __init__(self, runtime, run_id, session):
        token(run_id); token(session)
        root = Path(runtime) / "xr-workspace-spike"
        fd = private_dir(runtime); os.close(fd)
        fd = private_dir(root, create=True); os.close(fd)
        self.directory_fd = private_dir(root / run_id, create=True)
        self.path = root / run_id / "control.sock"
        self.queue = ControlQueue(run_id, session)
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            # Anchored path prevents parent rename/symlink races. Never unlink an existing socket.
            self.socket.bind(f"/proc/self/fd/{self.directory_fd}/control.sock")
            os.chmod("control.sock", 0o600, dir_fd=self.directory_fd, follow_symlinks=False)
            self.socket.listen(8)
            self.identity = os.stat("control.sock", dir_fd=self.directory_fd, follow_symlinks=False).st_ino
        except BaseException:
            self.socket.close(); os.close(self.directory_fd); raise
    def receive(self, conn):
        check_peer(conn, os.getuid())
        conn.settimeout(0.25)
        raw = bytearray()
        try:
            while len(raw) < MAX_FRAME:
                chunk = conn.recv(1)
                if not chunk: raise ValueError("truncated message")
                raw.extend(chunk)
                if chunk == b"\n": return self.queue.push(bytes(raw))
        except (TimeoutError, ConnectionError) as exc:
            raise ValueError("incomplete message") from exc
        raise ValueError("oversized message")
    def close(self):
        if self.directory_fd is None: return
        self.socket.close()
        try:
            st = os.stat("control.sock", dir_fd=self.directory_fd, follow_symlinks=False)
            if st.st_ino == self.identity and stat.S_ISSOCK(st.st_mode):
                os.unlink("control.sock", dir_fd=self.directory_fd)
        finally:
            os.close(self.directory_fd); self.directory_fd = None
    def __enter__(self): return self
    def __exit__(self, *args): self.close()


def read_private_json(path, limit=16384):
    """Owned regular single-link 0600 file, no-follow, bounded strict JSON."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        st = os.fstat(stream.fileno())
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) != 0o600 or st.st_nlink != 1:
            raise PermissionError('consent must be owned regular single-link mode 0600')
        raw = stream.read(limit + 1)
    if len(raw) > limit: raise ValueError('consent too large')
    return strict_json(raw)
