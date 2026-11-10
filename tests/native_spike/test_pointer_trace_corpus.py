"""The phase 6 pointer traces (pointer_trace_corpus.py): every committed trace replays through the shipped Python
pointer with the same calls, frames and state, and the trace files are the modules the generator records. The Rust port
replays them in rust/crates/xr-workspace/src/supervisor/pointer_trace_tests.rs."""
import json
import unittest

import pointer_trace_corpus as corpus


class PointerTraceCorpusTests(unittest.TestCase):
    def test_every_trace_replays_through_the_python(self):
        total = 0
        for module in corpus.MODULES:
            cases = json.loads(corpus.corpus_file(module).read_text())
            self.assertTrue(cases, module)
            for case in cases:
                with self.subTest(trace=case['test']):
                    corpus.replay(case)
                total += len(case['steps'])
        self.assertGreater(total, 5000)

    def test_the_files_are_the_recorded_modules(self):
        files = sorted(p.name for p in corpus.CORPUS_DIR.glob('traces-*.json'))
        self.assertEqual(files, sorted(corpus.corpus_file(m).name for m in corpus.MODULES))

    def test_a_changed_frame_fails_the_replay(self):
        cases = json.loads(corpus.corpus_file('test_free_pointer').read_text())
        case = next(c for c in cases if any(isinstance(s.get('r'), dict) and s['r'].get('$b') for s in c['steps']))
        step = next(s for s in case['steps'] if isinstance(s.get('r'), dict) and s['r'].get('$b'))
        step['r'] = {'$b': step['r']['$b'].replace('"op"', '"oq"', 1)}
        with self.assertRaises(corpus.Mismatch):
            corpus.replay(case)


if __name__ == '__main__':
    unittest.main()
