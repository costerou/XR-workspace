"""Independent synthetic boundary checks. Run inside fresh user+IPC namespaces."""
import ctypes
import math
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from tools.pro2_diagnostic.adapter import ReplayAdapter
from tools.pro2_diagnostic.candidate import EXACT, SDK, capture


class ReplayBoundaryQA(unittest.TestCase):
    def test_faults_disable_and_cannot_resume(self):
        good = [0, 0, 0, 1, 0, 0, 0]
        for values, stamp, host in [(good, 0, 2), (good, 2, 0),
                                    (good, 2, 250000002), (good, 2**64, 2),
                                    ([0, 0, 0, math.inf, 0, 0, 0], 2, 2),
                                    ([0, 0, 0, 0, 0, 0, 0], 2, 2)]:
            with self.subTest(values=values, stamp=stamp, host=host):
                adapter = ReplayAdapter(replay_only=True)
                adapter.push(good, 1, 1)
                with self.assertRaises(ValueError): adapter.push(values, stamp, host)
                self.assertFalse(adapter.active)
                self.assertEqual(adapter.history, [])
                with self.assertRaises(ValueError): adapter.push(good, 3, 3)

    def test_h1_signs_without_optical_offset(self):
        a = .1
        for source, expected in [([math.sin(a), 0, 0], [0, -math.sin(a), 0]),
                                 ([0, math.sin(a), 0], [0, 0, math.sin(a)]),
                                 ([0, 0, math.sin(a)], [-math.sin(a), 0, 0])]:
            adapter = ReplayAdapter(replay_only=True)
            packet = adapter.push([0, 0, 0, math.cos(a)] + source, 1, 1)
            for actual, wanted in zip(packet[:4], expected + [math.cos(a)]):
                self.assertAlmostEqual(actual, wanted, places=6)
            self.assertEqual(packet[15], 0)

    def test_sign_continuity_and_close(self):
        adapter = ReplayAdapter(replay_only=True)
        first = adapter.push([0, 0, 0, 1, 0, 0, 0], 1, 1)
        second = adapter.push([0, 0, 0, -1, 0, 0, 0], 16666668, 16666668)
        self.assertEqual(first[:4], second[:4])
        adapter.close(); adapter.close()
        self.assertFalse(adapter.active)
        with self.assertRaises(ValueError): adapter.push([0, 0, 0, 1, 0, 0, 0], 33333335, 33333335)


class CandidateBoundaryQA(unittest.TestCase):
    def test_bad_identity_never_invokes_loader(self):
        for rows in ([], [EXACT, EXACT], [('35ca', '1104', EXACT[2])],
                     [('35ca', '1201', EXACT[2])], [('35ca', '1301', 'VITURE Pro')],
                     [('ffff', '1301', EXACT[2])]):
            with self.subTest(rows=rows):
                loader = Mock(side_effect=AssertionError('loader reached'))
                with self.assertRaises(ValueError): capture(rows, loader, 0, lambda _: None)
                loader.assert_not_called()

    def test_hash_failure_precedes_any_dlopen(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / 'not-a-library'
            fake.write_bytes(b'offline fixture only')
            with patch.object(ctypes, 'CDLL') as load:
                with self.assertRaisesRegex(ValueError, 'hash changed'): SDK(str(fake), '0' * 64)
                load.assert_not_called()
