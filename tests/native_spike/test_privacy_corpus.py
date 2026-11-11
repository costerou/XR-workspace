"""The privacy corpus (privacy_corpus.py): the committed rust/corpus/supervisor/privacy.json matches the shipped Python."""
import json
import unittest

import privacy_corpus as corpus


class PrivacyCorpusTests(unittest.TestCase):
    def test_the_committed_corpus_is_what_the_python_sends(self):
        self.assertEqual(json.loads(corpus.FILE.read_text()), json.loads(corpus.lua_corpus.dumps(corpus.corpus())))


if __name__ == '__main__':
    unittest.main()
