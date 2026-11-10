"""The Rust supervisor's key bind, bind check, options text and background corpus (keybinds_corpus.py) matches the
Python code it was taken from."""
import json
import unittest

import keybinds_corpus as corpus


class CorpusTests(unittest.TestCase):
    def test_the_files_match_the_python(self):
        built = corpus.build()
        for name in corpus.FILES:
            with self.subTest(name):
                path = corpus.CORPUS / name
                self.assertTrue(path.exists(), f'missing {path}: run keybinds_corpus.py --write')
                self.assertEqual(path.read_text(), corpus.render(built[name]),
                                 f'{name} is stale: TMPDIR=/tmp /usr/bin/python3 tests/native_spike/keybinds_corpus.py --write')

    def test_every_catalog_bind_and_panel_branch_has_cases(self):
        data = json.loads((corpus.CORPUS / 'keybinds.json').read_text())
        statuses = {s['state']['status'].split(' ')[0] for t in data['traces'] for s in t['steps']}
        for word in ('New', 'Hold', 'That', 'Change', 'Esc', 'Press', 'Every', 'No'):
            self.assertIn(word, statuses)
        toasts = [x for t in data['traces'] for s in t['steps'] for x in s['toasts']]
        self.assertTrue(any('could not take the keyboard' in x for x in toasts))
        self.assertTrue(any(x.startswith('Key binds: ') for x in toasts))
        self.assertTrue(any('Not saved' in s['state']['status'] for t in data['traces'] for s in t['steps']))
        self.assertEqual(len(data['tables']['catalog']), 28)

    def test_the_layouts_give_different_text(self):
        data = json.loads((corpus.CORPUS / 'options_text.json').read_text())
        first = {json.dumps(layout['results']) for layout in data['layouts']}
        self.assertEqual(len(first), len(data['layouts']))
        self.assertTrue(any(r == ['text', 'é'] for layout in data['layouts'] for r in layout['results']))


if __name__ == '__main__':
    unittest.main()
