"""The saved-setup reset corpus (setup_corpus.py): the committed golden file matches the shipped Python."""
import json
import unittest

import setup_corpus as corpus


class SetupCorpusTest(unittest.TestCase):
    def test_file_matches_the_python(self):
        self.assertEqual(corpus.main([]), 0, 'run: TMPDIR=/tmp /usr/bin/python3 tests/native_spike/setup_corpus.py --write')

    def test_it_holds_a_reset_a_kept_setup_and_a_default_that_asks_nothing(self):
        cases = json.loads(corpus.SETUP_FILE.read_text(encoding='utf-8'))['cases']
        self.assertTrue(any(c['before'] != c['after'] for c in cases))
        self.assertTrue(any(c['asked'] and c['before'] == c['after'] for c in cases))
        self.assertTrue(any(not c['asked'] for c in cases))
        self.assertTrue(any('could not be reset' in c['log'] for c in cases))
