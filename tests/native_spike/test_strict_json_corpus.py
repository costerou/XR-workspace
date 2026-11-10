"""rust/corpus/json/strict_json.json still holds the shipped decoders' verdicts (strict_json_corpus.py makes it).

Any run checks the Python verdicts against tools/native_spike/ipc.strict_json. With CONTROL_WIRE_CHECK naming the built
control_wire_check (ctest control_wire), the whole file must regenerate byte for byte, C++ verdicts included.
"""
import json
import os
import unittest

import strict_json_corpus as corpus


class StrictJsonCorpus(unittest.TestCase):
    def setUp(self):
        self.text = corpus.FILE.read_text()
        self.cases = json.loads(self.text)['cases']

    def test_python_verdicts_hold(self):
        for case in self.cases:
            raw = bytes.fromhex(case['hex']) if 'hex' in case else case['text']
            self.assertEqual(corpus.python_verdict(raw), case['python'], case)

    def test_divergences_are_decided_rules(self):
        for case in self.cases:
            if case['python'] == case['cpp']: self.assertNotIn('divergence', case)
            else: self.assertEqual(case['rust'], case[corpus.DIVERGENCES[case['divergence']][0]], case)

    @unittest.skipUnless(os.environ.get('CONTROL_WIRE_CHECK'), 'needs the built control_wire_check')
    def test_file_regenerates(self):
        self.assertEqual(corpus.dump(corpus.build(os.environ['CONTROL_WIRE_CHECK'])), self.text)


if __name__ == '__main__':
    unittest.main()
