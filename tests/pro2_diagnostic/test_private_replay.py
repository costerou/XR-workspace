"""Opt-in local private JSONL replay; never copies or prints pose contents."""
import json
import os
from pathlib import Path
import unittest
from tools.pro2_diagnostic.adapter import replay

class PrivateReplayTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('PRO2_PRIVATE_REPLAY'),'private capture opt-in only')
    def test_private_capture_contract(self):
        rows=[json.loads(line) for line in Path(os.environ['PRO2_PRIVATE_REPLAY']).read_text().splitlines()]
        result=replay(rows)
        self.assertGreater(result['samples'],800)
        self.assertLess(result['maximum_output_norm_squared_error'],2e-7)
        self.assertTrue(result['teardown_closed'])
        self.assertFalse(result['teardown_active'])
        self.assertFalse(result['published_to_ipc'])
