"""fixtures/control_frames.json (control_corpus.py) stays true: its frames are what the Python encoders send today, it covers
every renderer op with frames the renderer takes and frames it refuses, and (with CONTROL_WIRE_CHECK, ctest control_wire)
every recorded verdict is still the renderer decoder's."""
import json
import os
import re
import unittest

import control_corpus as cc


class ControlCorpusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = json.loads(cc.FIXTURE.read_text())

    def test_ops_are_the_renderers(self):
        text = (cc.ROOT / 'spike/include/xr/ipc.hpp').read_text()
        enum = re.search(r'enum class ControlOp \{([^}]*)\}', text).group(1)
        self.assertEqual(tuple(name.strip() for name in enum.split(',')), cc.OPS)
        self.assertEqual(tuple(self.fixture['ops']), cc.OPS)

    def test_every_op_has_accepted_and_refused_frames(self):
        for op, cases in self.fixture['ops'].items():
            with self.subTest(op=op):
                self.assertTrue(cases['encoded'])
                self.assertTrue(any(case['verdict'] != 'accepted' for case in cases['mutated']))
                for line in cases['encoded'] + [case['frame'] for case in cases['mutated']]:
                    self.assertEqual(json.loads(line)['op'], op)

    def test_frames_match_the_encoders(self):
        built = cc.build()
        for op in cc.OPS:
            with self.subTest(op=op):
                self.assertEqual(built['ops'][op]['sender'], self.fixture['ops'][op]['sender'])
                self.assertEqual(built['ops'][op]['encoded'], self.fixture['ops'][op]['encoded'])
                self.assertEqual([(c['what'], c['frame']) for c in built['ops'][op]['mutated']],
                                 [(c['what'], c['frame']) for c in self.fixture['ops'][op]['mutated']])
        self.assertEqual(built['envelope'], [{'what': c['what'], 'frames': c['frames']} for c in self.fixture['envelope']])

    @unittest.skipUnless(os.environ.get('CONTROL_WIRE_CHECK'), 'needs the control_wire_check binary (ctest control_wire)')
    def test_verdicts_are_the_decoders(self):
        tool = os.environ['CONTROL_WIRE_CHECK']
        for op, cases in self.fixture['ops'].items():
            for line in cases['encoded']:
                with self.subTest(op=op, frame=line[:120]):
                    self.assertEqual(cc.verdict([line], tool), 'accepted')
            for case in cases['mutated']:
                with self.subTest(op=op, what=case['what']):
                    self.assertEqual(cc.verdict([case['frame']], tool), case['verdict'])
        for case in self.fixture['envelope']:
            with self.subTest(what=case['what']):
                self.assertEqual(cc.verdict(case['frames'], tool), case['verdict'])


if __name__ == '__main__':
    unittest.main()
