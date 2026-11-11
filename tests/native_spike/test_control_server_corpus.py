"""rust/corpus/sockets/server.json (control_server_corpus.py) stays true: what the shipped PrivateServer and control_request /
serve_without_renderer do today, case for case, and every run frame case of frames.json is in it."""
import json
import unittest

import control_server_corpus as cs


class ControlServerCorpusTests(unittest.TestCase):
    def test_the_file_is_what_the_python_does(self):
        self.assertEqual(json.loads(cs.CORPUS.read_text()), cs.build())

    def test_every_run_frame_case_is_served(self):
        served = {c['about'] for c in json.loads(cs.CORPUS.read_text())['cases']}
        for case in json.loads(cs.FRAMES.read_text())['run_cases']:
            self.assertIn(case['about'], served)


if __name__ == '__main__':
    unittest.main()
