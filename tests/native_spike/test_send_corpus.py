"""The window sender's corpus (send_corpus.py) matches the shipped Python send.WindowSender."""
import unittest

import send_corpus as corpus


class CorpusTests(unittest.TestCase):
    def test_the_file_matches_the_python(self):
        self.assertTrue(corpus.FILE.exists(), f'missing {corpus.FILE}: run send_corpus.py --write')
        self.assertEqual(corpus.FILE.read_text(), corpus.render(corpus.build()),
                         'sender.json is stale: TMPDIR=/tmp /usr/bin/python3 tests/native_spike/send_corpus.py --write')

    def test_every_refusal_is_recorded(self):
        notes = {n for s in corpus.build()['scenarios'] for n in s['notes']}
        for text in ('is not part of this session.', 'Pinned windows', 'No focused window', 'Could not identify',
                     'is not available.', 'regular workspace.', 'not on the glasses', 'no physical monitor',
                     'did not move', 'Could not move the window.', 'readback mismatch'):
            self.assertTrue(any(text in n for n in notes), text)


if __name__ == '__main__':
    unittest.main()
