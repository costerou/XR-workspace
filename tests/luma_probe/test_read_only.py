"""The promise that matters most: this tool cannot touch the glasses."""
import ast
import builtins
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import tools.luma_probe
from tools.luma_probe.assess import assess
from tools.luma_probe.sysfs import read_bytes, read_text, scan
from fake_host import FakeHost

PACKAGE = Path(tools.luma_probe.__file__).parent
SOURCES = sorted(PACKAGE.glob('*.py'))
FORBIDDEN_MODULES = {'fcntl', 'socket', 'usb', 'hid', 'subprocess'}
FORBIDDEN_CALLS = {'ioctl', 'chmod', 'chown', 'mkdir', 'remove', 'unlink', 'rename', 'symlink', 'system', 'popen',
                   'setxattr', 'write_bytes'}


class SourceTests(unittest.TestCase):
    def trees(self):
        return [(path.name, ast.parse(path.read_text())) for path in SOURCES]

    def test_nothing_that_can_talk_to_a_device_is_imported(self):
        for name, tree in self.trees():
            for node in ast.walk(tree):
                modules = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                    [node.module or ''] if isinstance(node, ast.ImportFrom) else []
                for module in modules:
                    self.assertNotIn(module.split('.')[0], FORBIDDEN_MODULES, name)

    def test_no_call_can_change_the_system(self):
        for name, tree in self.trees():
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    called = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, 'id', '')
                    self.assertNotIn(called, FORBIDDEN_CALLS, name)

    def test_files_are_opened_in_one_place_only(self):
        openers = []
        for name, tree in self.trees():
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    called = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, 'id', '')
                    if called in ('open', 'fdopen', 'read_text', 'read_bytes') and name != 'sysfs.py':
                        openers.append((name, node.lineno, called))
        allowed = {'feed.py'}  # writes the log file the user named; see its own tests
        self.assertEqual([o for o in openers if o[0] not in allowed and o[2] in ('open', 'fdopen')], [])


class ReadGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)

    def test_refuses_a_link_to_a_character_device(self):
        (self.base / 'attr').symlink_to('/dev/null')
        self.assertIsNone(read_bytes(self.base / 'attr'))

    def test_refuses_a_fifo_without_hanging(self):
        os.mkfifo(self.base / 'pipe')
        self.assertIsNone(read_text(self.base / 'pipe'))

    def test_missing_file_is_none(self):
        self.assertIsNone(read_text(self.base / 'nothing'))

    def test_opens_with_read_only_flags(self):
        (self.base / 'attr').write_text('x\n')
        real = os.open
        seen = []
        with patch('os.open', lambda p, f, *a, **k: (seen.append(f), real(p, f, *a, **k))[1]):
            self.assertEqual(read_text(self.base / 'attr'), 'x')
        self.assertTrue(seen)
        self.assertTrue(all(f & os.O_ACCMODE == os.O_RDONLY for f in seen))


class ScanTests(unittest.TestCase):
    def test_a_full_scan_never_opens_a_device_node(self):
        with tempfile.TemporaryDirectory() as d:
            host = FakeHost(d).glasses_on_hub()
            host.process(77, 'xrDriver', holds=host.glasses_node)
            dev = str(host.roots.dev)
            opened = []
            real_os_open, real_open = os.open, builtins.open

            def spy_os(path, flags, *a, **k):
                opened.append((str(path), flags))
                return real_os_open(path, flags, *a, **k)

            def spy_open(path, *a, **k):
                opened.append((str(path), a))
                return real_open(path, *a, **k)

            with patch('os.open', spy_os), patch('builtins.open', spy_open):
                assess(scan(host.roots), host.roots)
            self.assertTrue(opened)
            self.assertEqual([p for p, _ in opened if p.startswith(dev)], [])
            self.assertEqual([f for _, f in opened if isinstance(f, int) and f & os.O_ACCMODE != os.O_RDONLY], [])
