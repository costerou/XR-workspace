"""Write-ahead ownership journal and conservative readback-driven recovery."""
from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import json
import os
from pathlib import Path
import stat
from typing import Protocol
import uuid
from .ipc import private_dir, strict_json, token


class HyprlandClient(Protocol):
    def query(self, kind: str) -> dict | list: ...
    def mutate(self, command: str) -> str: ...


@dataclass
class RecoveryResult:
    complete: bool
    remaining: list[str]


def validate_operation(op):
    if not isinstance(op, dict): raise ValueError("invalid operation")
    kind = op.get("kind")
    fields = {"setting": {"kind", "key", "before", "applied"},
              "workspace": {"kind", "workspace", "before", "applied"},
              "output": {"kind", "id", "name", "before"}}
    if not isinstance(kind, str) or kind not in fields or set(op) != fields[kind]: raise ValueError("unsupported operation")
    if kind == "setting":
        if op["key"] != "input:follow_mouse": raise ValueError("unsupported setting")
        if any(type(op[k]) is not int or not 0 <= op[k] <= 3 for k in ("before", "applied")):
            raise ValueError("invalid setting value")
    elif kind == "workspace":
        if type(op["workspace"]) is not int or not 0 < op["workspace"] <= 2147483647: raise ValueError("invalid workspace")
        token(op["before"]); token(op["applied"])
    else:
        if not isinstance(op["id"], str) or str(uuid.UUID(op["id"])) != op["id"] or op["name"] != "XR-" + op["id"] or op["before"] is not None:
            raise ValueError("invalid output ownership")
    return op


def _safe_file(fd):
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) != 0o600 or st.st_nlink != 1:
        raise PermissionError("journal/lock must be owned regular mode 0600 single-link file")


class Journal:
    path: Path
    run_id: str
    session: str
    _dir: int | None

    @classmethod
    def open(cls, path: Path, run_id: str, session: str):
        self = cls()
        self.path, self.run_id, self.session = Path(path), token(run_id), token(session)
        self.operations = []
        with self.transaction():
            if not self._exists: self._save()
        return self

    @contextmanager
    def transaction(self):
        fd = private_dir(self.path.parent)
        lock = None
        try:
            lock = os.open(self.path.name + ".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=fd)
            _safe_file(lock); fcntl.flock(lock, fcntl.LOCK_EX)
            self._dir = fd
            self._load()
            yield
        finally:
            if lock is not None: os.close(lock)
            os.close(fd)
            self._dir = None

    def _load(self):
        try:
            fd = os.open(self.path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self._dir)
        except FileNotFoundError:
            self._exists = False; self.operations = []; return
        self._exists = True
        with os.fdopen(fd, "rb") as stream:
            _safe_file(stream.fileno())
            raw = stream.read(1048577)
        if len(raw) > 1048576: raise ValueError("journal too large")
        data = strict_json(raw)
        if not isinstance(data, dict) or set(data) != {"schema", "run_id", "session", "operations"}:
            raise ValueError("invalid journal")
        if type(data["schema"]) is not int or data["schema"] != 1 or data["run_id"] != self.run_id or data["session"] != self.session:
            raise ValueError("wrong journal identity")
        if not isinstance(data["operations"], list): raise ValueError("invalid records")
        ids = set()
        for record in data["operations"]:
            if not isinstance(record, dict) or set(record) != {"id", "operation", "state"}: raise ValueError("invalid record")
            if not isinstance(record["id"], str) or str(uuid.UUID(record["id"])) != record["id"] or record["id"] in ids: raise ValueError("invalid record id")
            ids.add(record["id"])
            if record["state"] not in ("pending", "confirmed", "done"): raise ValueError("invalid state")
            validate_operation(record["operation"])
        self.operations = data["operations"]

    def _save(self):
        assert self._dir is not None
        data = {"schema": 1, "run_id": self.run_id, "session": self.session, "operations": self.operations}
        raw = json.dumps(data, allow_nan=False).encode()
        if len(raw) > 1048576: raise ValueError("journal too large")
        name = ".journal-" + str(uuid.uuid4())
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=self._dir)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw); stream.flush(); os.fsync(stream.fileno())
            os.replace(name, self.path.name, src_dir_fd=self._dir, dst_dir_fd=self._dir)
            os.fsync(self._dir)
        finally:
            try: os.unlink(name, dir_fd=self._dir)
            except FileNotFoundError: pass

    def prepare(self, operation: dict) -> str:
        op = json.loads(json.dumps(validate_operation(operation), allow_nan=False))
        oid = str(uuid.uuid4())
        with self.transaction():
            self.operations.append({"id": oid, "operation": op, "state": "pending"})
            self._save()
        return oid

    def confirm(self, operation_id: str, observed: dict) -> None:
        with self.transaction():
            record = next((r for r in self.operations if r["id"] == operation_id), None)
            if record is None or record["state"] == "done": raise ValueError("unknown/finished operation")
            op = record["operation"]
            expected = ({"id": op["id"], "name": op["name"], "physical": False} if op["kind"] == "output"
                        else {"value": op["applied"]} if op["kind"] == "setting" else {"output": op["applied"]})
            if not isinstance(observed, dict) or observed != expected or any(type(observed[k]) is not type(v) for k, v in expected.items()):
                raise ValueError("mutation not observed")
            record["state"] = "confirmed"; self._save()


def _session(journal, hypr):
    if hypr.query("session") != {"id": journal.session}: raise ValueError("session disconnected or changed")


def _mutate(journal, hypr, command):
    _session(journal, hypr)
    if hypr.mutate(command) != "ok": raise ValueError("mutation failed")


def _undo(journal, hypr, op):
    _session(journal, hypr)
    if op["kind"] == "setting":
        current = hypr.query("settings")[op["key"]]
        if current != op["applied"]: return True  # User change or mutation never happened.
        _mutate(journal, hypr, f"keyword {op['key']} {op['before']}")
        return hypr.query("settings")[op["key"]] == op["before"]
    if op["kind"] == "workspace":
        matches = [w for w in hypr.query("workspaces") if w["id"] == op["workspace"]]
        if not matches: return True
        if len(matches) != 1: return False
        if matches[0]["output"] != op["applied"]: return True
        outputs = hypr.query("outputs")
        if not any(o["name"] == op["before"] and o["physical"] is True for o in outputs): return False
        _mutate(journal, hypr, f"dispatch moveworkspacetomonitor {op['workspace']} {op['before']}")
        return any(w["id"] == op["workspace"] and w["output"] == op["before"] for w in hypr.query("workspaces"))
    outputs = hypr.query("outputs")
    matches = [o for o in outputs if o["name"] == op["name"] or o["id"] == op["id"]]
    if not matches:
        # Disappearance alone is not evidence that stranded workspaces are reachable.
        return not any(w["output"] == op["name"] for w in hypr.query("workspaces"))
    if len(matches) != 1 or matches[0] != {"id": op["id"], "name": op["name"], "physical": False}: return False
    fallback = next((o for o in outputs if o["physical"] is True and o["name"] != op["name"]), None)
    if fallback is None: return False
    destination = token(fallback["name"])
    for workspace in hypr.query("workspaces"):
        if workspace["output"] != op["name"]: continue
        wid = workspace["id"]
        if type(wid) is not int or not 0 < wid <= 2147483647: return False
        # Re-query immediately before moving, preserving intervening user moves.
        current = [w for w in hypr.query("workspaces") if w["id"] == wid]
        if len(current) != 1: return False
        if current[0]["output"] != op["name"]: continue
        _mutate(journal, hypr, f"dispatch moveworkspacetomonitor {wid} {destination}")
        if not any(w["id"] == wid and w["output"] == destination for w in hypr.query("workspaces")): return False
    # Revalidate identity and fallback after migration and before removal.
    outputs = hypr.query("outputs")
    if matches[0] not in outputs or fallback not in outputs: return False
    if any(w["output"] == op["name"] for w in hypr.query("workspaces")): return False
    _mutate(journal, hypr, f"output remove {op['name']}")
    return not any(o["name"] == op["name"] or o["id"] == op["id"] for o in hypr.query("outputs"))


def recover(journal: Journal, hypr: HyprlandClient) -> RecoveryResult:
    with journal.transaction():
        # Reverse mutation order; outputs last even if caller prepared out of order.
        records = list(reversed(journal.operations))
        records.sort(key=lambda r: r["operation"]["kind"] == "output")
        for record in records:
            if record["state"] == "done": continue
            try:
                done = _undo(journal, hypr, record["operation"])
                if done: _session(journal, hypr)
            except (OSError, ValueError, KeyError, TypeError):
                # Preserve durable intent on disconnect, ambiguity, or failed readback.
                continue
            if done:
                record["state"] = "done"
                # Storage failures are fatal, never disguised as successful recovery.
                journal._save()
        remaining = [r["id"] for r in journal.operations if r["state"] != "done"]
        return RecoveryResult(not remaining, remaining)
