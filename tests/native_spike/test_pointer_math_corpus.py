"""The phase 6 pointer maths corpus (pointer_math_corpus.py): the committed golden file matches the shipped Python's
orbit, neighbors, restore_band, bump and edge_pan. The Rust port replays it in
rust/crates/xr-workspace/src/supervisor/pointer_math_corpus_tests.rs."""
import json
import unittest

import pointer_math_corpus as corpus


class PointerMathCorpusTests(unittest.TestCase):
    def test_golden_file_matches_the_python(self):
        made = corpus.dumps(corpus.corpus())
        self.assertEqual(json.loads(corpus.CORPUS_FILE.read_text()), json.loads(made),
                         'regenerate: tests/native_spike/pointer_math_corpus.py --write')
        self.assertEqual(corpus.CORPUS_FILE.read_text(), made)

    def test_every_function_and_class_is_recorded_or_said_why_not(self):
        data = json.loads(corpus.CORPUS_FILE.read_text())
        for name in corpus.methods():
            with self.subTest(name=name):
                kept = data['cases'].get(name) or data['traces'].get(name)
                self.assertTrue(kept or name in data['skipped'], name)
        self.assertGreater(sum(len(v) for v in data['cases'].values()), 2000)
        self.assertGreater(sum(len(t['calls']) for v in data['traces'].values() for t in v), 500)

    def test_a_changed_result_fails_the_replay(self):
        data = json.loads(corpus.CORPUS_FILE.read_text())
        case = dict(data['cases']['orbit.wrap'][0])
        self.assertEqual(corpus.replay_call('orbit.wrap', case), corpus.expected(case))
        case['out'] = case['out'] + '0'
        self.assertNotEqual(corpus.replay_call('orbit.wrap', case), corpus.expected(case))


if __name__ == '__main__':
    unittest.main()
