"""The Rust run's key bind panel corpus (keybinds_run_corpus.py) matches the Python code it was taken from."""
import json
import unittest

import keybinds_run_corpus as corpus


class CorpusTests(unittest.TestCase):
    def test_the_file_matches_the_python(self):
        self.assertTrue(corpus.CORPUS.exists(), f'missing {corpus.CORPUS}: run keybinds_run_corpus.py --write')
        self.assertEqual(corpus.CORPUS.read_text(), corpus.render(corpus.build()),
                         'keybinds_run.json is stale: TMPDIR=/tmp /usr/bin/python3 tests/native_spike/keybinds_run_corpus.py --write')

    def test_a_conflicting_saved_bind_is_left_out_of_the_install(self):
        data = json.loads(corpus.CORPUS.read_text())
        start = data['scenarios']['conflict']['steps'][0]
        self.assertEqual(start['wire'][0][0], 'binds')  # the desktop's binds are read before anything is installed
        install = start['wire'][1][0]
        self.assertNotIn('"next"', install)
        self.assertIn('CTRL + ALT + J', install)
        self.assertTrue(start['toasts'][0].startswith('Key bind conflict: Next desktop'))


if __name__ == '__main__':
    unittest.main()
