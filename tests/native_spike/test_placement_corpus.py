"""The phase 6 placement corpus (placement_corpus.py): the committed golden file matches the shipped Python, and its
fixtures hold every window the supervisor must leave where it is."""
import json
import unittest

import placement_corpus as corpus
from tools.native_spike import adopt


class PlacementCorpusTest(unittest.TestCase):
    def test_file_matches_the_python(self):
        self.assertEqual(corpus.main([]), 0, 'run: TMPDIR=/tmp /usr/bin/python3 tests/native_spike/placement_corpus.py --write')

    def test_every_function_has_a_case_and_a_refusal_where_it_can_refuse(self):
        cases = json.loads(corpus.PLACEMENT_FILE.read_text(encoding='utf-8'))['cases']
        self.assertEqual({c['function'] for c in cases}, set(corpus.FUNCTIONS))
        never = {'adopt.stays', 'window_memory.match', 'window_memory.empty', 'resize.outputs_from_monitors',
                 'geometry.toggle_ultrawide', 'geometry.valid_scales', 'geometry.nearest_scale', 'fit_windows.free_area'}
        refused = {c['function'] for c in cases if 'refused' in c}
        self.assertEqual(set(corpus.FUNCTIONS) - never, refused)

    def test_fixtures_hold_every_window_that_stays(self):
        for clients in (corpus.FALLBACK, corpus.LIVE):
            self.assertTrue(any(c.get('pinned') is True for c in clients))
            self.assertTrue(any('pinned' not in c for c in clients))
            self.assertTrue(any(c['workspace']['name'].startswith('special') for c in clients))
            self.assertTrue(any(c['class'] == adopt.PANEL_CLASS and c['title'] == adopt.PANEL_TITLE for c in clients))
            self.assertTrue(any(c['class'] == adopt.PANEL_CLASS and c['title'] in adopt.LEGACY_PANEL_TITLES for c in clients))
        # More occupied workspaces than desktops: the ones past the last desktop go to the last.
        self.assertGreater(len(adopt.occupied(corpus.WORKSPACES, corpus.FALLBACK, 'DP-1')), 4)


if __name__ == '__main__':
    unittest.main()
