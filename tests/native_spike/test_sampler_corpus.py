"""The phase 6 sampler runs (sampler_corpus.py): every committed scripted run replays through the shipped Python
samplers with the same calls, arguments, outcomes and state, and the file is exactly what the generator records. The Rust
port replays it in rust/crates/xr-workspace/src/supervisor/sampler_corpus_tests.rs."""
import json
import unittest

import sampler_corpus as corpus


class SamplerCorpusTests(unittest.TestCase):
    def test_every_run_replays_through_the_python(self):
        stored = json.loads(corpus.CORPUS_FILE.read_text())
        self.assertGreaterEqual(len(stored), 18)
        for case in stored:
            with self.subTest(run=case['name']):
                corpus.replay(case)
        self.assertGreater(sum(len(c['steps']) for c in stored), 400)

    def test_the_file_is_the_recorded_corpus(self):
        self.assertEqual(corpus.CORPUS_FILE.read_text(), corpus.dump(corpus.record()))

    def test_a_changed_event_fails_the_replay(self):
        stored = json.loads(corpus.CORPUS_FILE.read_text())
        case = next(c for c in stored if c['name'] == 'unified_pan')
        event = next(e for s in case['steps'] for e in s['events'] if e['call'] == 'confine_cursor')
        event['args'][0] += 1
        with self.assertRaises(corpus.Mismatch):
            corpus.replay(case)


if __name__ == '__main__':
    unittest.main()
