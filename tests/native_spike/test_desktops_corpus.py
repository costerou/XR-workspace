"""rust/corpus/desktops/manager.json is what the shipped DesktopManager does (desktops_corpus.py)."""
import unittest

import desktops_corpus as corpus


class CorpusTests(unittest.TestCase):
    def test_the_corpus_is_current(self):
        self.assertEqual(corpus.FILE.read_text(), corpus.dumps(corpus.corpus()),
                         'stale: TMPDIR=/tmp /usr/bin/python3 tests/native_spike/desktops_corpus.py --write')

    def test_every_action_and_fault_is_covered(self):
        actions = {a[0] for case in corpus.scenarios() for a in case[4]}
        self.assertEqual(actions, {'add', 'remove', 'ultrawide', 'scale', 'resolution', 'reset'})
        cases = {case[1] for case in corpus.scenarios()}
        self.assertEqual(cases, {'', 'create-fail', 'remove-refused', 'reshape-fail'})


if __name__ == '__main__':
    unittest.main()
