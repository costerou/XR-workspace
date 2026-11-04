"""Mutation tests: a missing row or wrong scope cannot yield coverage success."""
import copy
import importlib.util
import json
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('contract_validator', HERE / 'validate.py')
v = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v)


class ContractValidation(unittest.TestCase):
    def setUp(self):
        self.inv = json.loads((HERE / 'inventory.json').read_text())
        self.doc = (v.ROOT / 'docs/xr-options-acceptance.md').read_text()

    def test_complete(self):
        self.assertEqual([], v.validate(self.doc, self.inv))

    def test_missing_runtime_route(self):
        doc = '\n'.join(x for x in self.doc.splitlines() if not x.startswith('| video_browse |'))
        self.assertTrue(any('video_browse' in e for e in v.validate(doc, self.inv)))

    def test_wrong_scope(self):
        doc = self.doc.replace('| stabilization | stabilization {strength:0..100} | workspace |',
                               '| stabilization | stabilization {strength:0..100} | selected monitor |')
        self.assertTrue(any('stabilization' in e for e in v.validate(doc, self.inv)))

    def test_duplicate_inventory(self):
        inv = copy.deepcopy(self.inv)
        inv['rows'][-1] = inv['rows'][0]
        self.assertTrue(any('116 unique' in e for e in v.validate(self.doc, inv)))

    def test_missing_maximize_gate(self):
        self.assertIn('missing acceptance gate A07', v.validate(self.doc.replace('- [ ] A07 ', ''), self.inv))

    def test_duplicate_route(self):
        row = next(x for x in self.doc.splitlines() if x.startswith('| zoom_in |'))
        self.assertTrue(any('duplicate ledger' in e for e in v.validate(self.doc + '\n' + row, self.inv)))


if __name__ == '__main__':
    unittest.main()
