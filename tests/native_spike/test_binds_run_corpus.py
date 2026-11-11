"""The Rust run's Ctrl+Alt binds corpus (binds_run_corpus.py) matches the Python code it was taken from."""
import json
import unittest

import binds_run_corpus as corpus


class CorpusTests(unittest.TestCase):
    def test_the_file_matches_the_python(self):
        self.assertTrue(corpus.CORPUS.exists(), f'missing {corpus.CORPUS}: run binds_run_corpus.py --write')
        self.assertEqual(corpus.CORPUS.read_text(), corpus.render(corpus.build()),
                         'binds_run.json is stale: TMPDIR=/tmp /usr/bin/python3 tests/native_spike/binds_run_corpus.py --write')

    def test_the_session_mode_is_read_before_the_binds_go_in_and_they_go_out_last(self):
        data = json.loads(corpus.CORPUS.read_text())
        for name, scenario in data['scenarios'].items():
            with self.subTest(name):
                start, end = scenario['steps'][0]['wire'], scenario['steps'][-1]['wire']
                self.assertEqual(start[0][0], 'j/monitors all')
                self.assertIn('xr_workspace_binds = {', ''.join(w[0] for w in start[1:]))
                self.assertEqual(len(end), 1)
                self.assertTrue(end[0][0].startswith('/eval for _, b in ipairs(xr_workspace_binds or {})'))

    def test_a_reload_parks_before_it_puts_the_mode_back(self):
        reload = json.loads(corpus.CORPUS.read_text())['scenarios']['presentation']['steps'][2]['wire']
        rules = [w[0] for w in reload if w[0].startswith('/eval hl.monitor(')]
        self.assertEqual(len(rules), 2)
        self.assertIn('position = "-1920x9272"', rules[0])  # parked first, in the reset mode
        self.assertIn('mode = "1920x1200@120"', rules[1])  # then the session mode, where it was parked


if __name__ == '__main__':
    unittest.main()
