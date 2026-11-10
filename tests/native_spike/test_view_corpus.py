"""The phase 6 view corpus (view_corpus.py): the committed golden file matches the shipped Python's frames, state
steps and settings files. The Rust port replays it in rust/crates/xr-workspace/src/supervisor/corpus_tests.rs."""
import json
import unittest

import view_corpus as corpus


class ViewCorpusTests(unittest.TestCase):
    def test_golden_file_matches_the_python(self):
        made = corpus.dumps(corpus.corpus())
        self.assertEqual(json.loads(corpus.CORPUS_FILE.read_text()), json.loads(made),
                         'regenerate: tests/native_spike/view_corpus.py --write')
        self.assertEqual(corpus.CORPUS_FILE.read_text(), made)

    def test_every_refusal_says_how_the_types_take_it(self):
        data = json.loads(corpus.CORPUS_FILE.read_text())
        cases = data['frames'] + data['save_settings'] + data['scripts']
        cases += [step for script in data['scripts'] for step in script.get('steps', [])]
        refused = [case for case in cases if 'refused' in case]
        self.assertTrue(refused)
        for case in refused:
            self.assertIn(case.get('by'), ('type', 'value'), case)


if __name__ == '__main__':
    unittest.main()
