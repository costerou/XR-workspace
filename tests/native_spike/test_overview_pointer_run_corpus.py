"""The Rust run's overview pointer corpus (overview_pointer_run_corpus.py) matches the Python code it was taken from."""
import json
import unittest

import overview_pointer_run_corpus as corpus


class CorpusTests(unittest.TestCase):
    def test_the_file_matches_the_python(self):
        self.assertTrue(corpus.CORPUS.exists(), f'missing {corpus.CORPUS}: run overview_pointer_run_corpus.py --write')
        self.assertEqual(corpus.CORPUS.read_text(), corpus.render(corpus.build()),
                         'overview_pointer_run.json is stale: '
                         'TMPDIR=/tmp /usr/bin/python3 tests/native_spike/overview_pointer_run_corpus.py --write')

    def test_the_sampler_binds_with_the_runs_args_and_stop_removes_them(self):
        data = json.loads(corpus.CORPUS.read_text())
        for name, s in data['scenarios'].items():
            self.assertTrue(any(corpus.ARGS[2] in e for e in s['effects']), name)  # the binds call the run's helper
            self.assertIn('xr_workspace_resize_binds', s['stop'][0], name)  # the binds go first, then the cursor shows
            self.assertIn('xr_workspace_overview_cursor', s['stop'][-1], name)
        self.assertEqual(data['scenarios']['text']['pointer']['look'], 'text')
        self.assertIsNotNone(data['scenarios']['text']['first_shape_read'])


if __name__ == '__main__':
    unittest.main()
