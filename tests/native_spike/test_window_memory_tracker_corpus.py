"""rust/corpus/window_memory/tracker.json is what the shipped WindowTracker does (window_memory_tracker_corpus.py)."""
import unittest

import window_memory_tracker_corpus as corpus


class CorpusTests(unittest.TestCase):
    def test_the_corpus_is_current(self):
        self.assertEqual(corpus.FILE.read_text(), corpus.dumps(corpus.corpus()),
                         'stale: TMPDIR=/tmp /usr/bin/python3 tests/native_spike/window_memory_tracker_corpus.py --write')

    def test_every_step_kind_is_covered(self):
        steps = {s['do'] for case in corpus.scenarios() for s in case[4]}
        self.assertEqual(steps, {'place', 'finish', 'capture', 'close'})


if __name__ == '__main__':
    unittest.main()
