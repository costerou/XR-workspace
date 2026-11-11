"""The glasses' 3D mode corpus (sbs_corpus.py) matches the shipped Python sbs.ButtonSbs."""
import unittest

import sbs_corpus as corpus


class CorpusTests(unittest.TestCase):
    def test_the_file_matches_the_python(self):
        self.assertTrue(corpus.FILE.exists(), f'missing {corpus.FILE}: run sbs_corpus.py --write')
        self.assertEqual(corpus.FILE.read_text(), corpus.render(corpus.build()),
                         'sbs.json is stale: TMPDIR=/tmp /usr/bin/python3 tests/native_spike/sbs_corpus.py --write')

    def test_every_way_in_and_out_is_recorded(self):
        data = corpus.build()
        calls = {c[0] for s in data['scenarios'] for c in s['calls']}
        self.assertEqual(calls, {'query', 'apply_wide_mode', 'apply_pro2_mode', 'apply_session_mode', 'park_presentation'})
        told = {t for s in data['scenarios'] for t in s['told']}
        for text in ('Hold R1', 'Hold L1', 'go back to 2D and 120 Hz', 'Click 3D'):
            self.assertTrue(any(text in t for t in told), text)
        errors = {r['error'] for s in data['scenarios'] for r in s['results'] if isinstance(r, dict)}
        for text in ('was R1 held?', 'they offered only', 'not observed', 'ValueError', 'no place'):
            self.assertTrue(any(text in e for e in errors), text)


if __name__ == '__main__':
    unittest.main()
