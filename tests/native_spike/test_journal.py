import json
import os
from pathlib import Path
import tempfile
import unittest
from tools.native_spike.journal import Journal


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "journal.json"

    def test_prepare_is_durable_and_confirmation_survives_reopen(self):
        journal = Journal.open(self.path, "run", "session")
        op = {"kind": "setting", "key": "input:follow_mouse", "before": 1, "applied": 0}
        oid = journal.prepare(op)
        self.assertEqual(json.loads(self.path.read_text())["operations"][0]["state"], "pending")
        journal.confirm(oid, {"value": 0})
        self.assertEqual(Journal.open(self.path, "run", "session").operations[0]["state"], "confirmed")
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_reject_unsafe_operations_and_identity(self):
        journal = Journal.open(self.path, "run", "session")
        for op in ({"kind": "command", "command": "exec evil"},
                   {"kind": "setting", "key": "evil", "before": 1, "applied": 0},
                   {"kind": "workspace", "workspace": "1;exec evil", "before": "DP-1", "applied": "XR-a"}):
            with self.assertRaises(ValueError): journal.prepare(op)
        with self.assertRaises(ValueError): Journal.open(self.path, "other", "session")
        with self.assertRaises(ValueError): Journal.open(self.path, "run", "other")

    def test_reject_symlink_permissions_and_duplicate_keys(self):
        self.path.write_text('{}')
        self.path.chmod(0o644)
        with self.assertRaises((ValueError, OSError)): Journal.open(self.path, "run", "session")
        self.path.chmod(0o600)
        self.path.write_text('{"schema":1,"schema":1}')
        with self.assertRaises(ValueError): Journal.open(self.path, "run", "session")
        link = self.path.with_name("link")
        link.symlink_to(self.path)
        with self.assertRaises((ValueError, OSError)): Journal.open(link, "run", "session")

    def test_confirm_rejects_boolean_as_integer(self):
        j = Journal.open(self.path, "run", "session")
        oid = j.prepare({"kind": "setting", "key": "input:follow_mouse", "before": 1, "applied": 0})
        with self.assertRaises(ValueError): j.confirm(oid, {"value": False})

    def test_prepare_rejects_nondict_kind_and_bad_uuid(self):
        j = Journal.open(self.path, "run", "session")
        for operation in ({"kind": []}, {"kind": "output", "id": None, "name": "XR-foo", "before": None}):
            with self.assertRaises(ValueError): j.prepare(operation)

    def test_stale_instances_do_not_lose_records(self):
        first = Journal.open(self.path, "run", "session")
        second = Journal.open(self.path, "run", "session")
        op = {"kind": "setting", "key": "input:follow_mouse", "before": 1, "applied": 0}
        a = first.prepare(op); b = second.prepare(op)
        self.assertEqual([r["id"] for r in Journal.open(self.path, "run", "session").operations], [a, b])

    def test_atomic_replace_is_fsynced_before_prepare_returns(self):
        from unittest.mock import patch
        j = Journal.open(self.path, "run", "session")
        events = []
        real_sync, real_replace = os.fsync, os.replace
        def sync(fd):
            events.append("directory" if __import__("stat").S_ISDIR(os.fstat(fd).st_mode) else "file")
            return real_sync(fd)
        def replace(*args, **kwargs):
            events.append("replace")
            return real_replace(*args, **kwargs)
        with patch("os.fsync", side_effect=sync), patch("os.replace", side_effect=replace):
            j.prepare({"kind": "setting", "key": "input:follow_mouse", "before": 1, "applied": 0})
        self.assertEqual(events, ["file", "replace", "directory"])

    def test_confirm_requires_matching_observation(self):
        j = Journal.open(self.path, "run", "session")
        oid = j.prepare({"kind": "setting", "key": "input:follow_mouse", "before": 1, "applied": 0})
        with self.assertRaises(ValueError): j.confirm(oid, {"value": 2})
