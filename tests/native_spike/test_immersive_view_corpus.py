"""The view's 3D corpus (immersive_view_corpus.py) matches the shipped Python view.ViewControl."""
import unittest

import immersive_view_corpus as corpus


class CorpusTests(unittest.TestCase):
    def test_the_file_matches_the_python(self):
        self.assertTrue(corpus.FILE.exists(), f'missing {corpus.FILE}: run immersive_view_corpus.py --write')
        self.assertEqual(corpus.FILE.read_text(), corpus.render(corpus.build()),
                         'view.json is stale: TMPDIR=/tmp /usr/bin/python3 tests/native_spike/immersive_view_corpus.py --write')

    def test_every_toast_and_a_refusal_are_recorded(self):
        steps = [r for s in corpus.build()['scenarios'] for r in s['results']]
        toasts = {t for r in steps for t in r['toasts']}
        for text in ('could not start', 'engine stopped', 'could not switch', 'did not leave', 'once the VR video closes'):
            self.assertTrue(any(text in t for t in toasts), text)
        self.assertTrue(any(isinstance(r['result'], dict) and 'error' in r['result'] for r in steps))
        self.assertTrue(any(r['touched'] for r in steps) and any(r['saved'] for r in steps))


if __name__ == '__main__':
    unittest.main()
