"""The socket corpus (socket_corpus.py) is what the shipped controller, key helper and run socket produce and decide today."""
import json
from pathlib import Path
import unittest

from tests.xr_controller import socket_corpus as corpus

QML = Path(__file__).resolve().parents[2] / 'omarchy-plugin' / 'cody.viture-workspace'


class SocketCorpusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kept = corpus.FILE.read_text()
        cls.corpus = json.loads(cls.kept)

    def test_corpus_is_what_the_shipped_code_produces(self):
        self.assertEqual(corpus.dump(corpus.generate()), self.kept,
                         'regenerate: TMPDIR=/tmp python3 -m tests.xr_controller.socket_corpus --write')

    def test_every_panel_op_is_captured(self):
        sent = {json.loads(r['text'])['op'] for r in self.corpus['panel_requests']}
        source = '\n'.join(p.read_text() for p in QML.rglob('*.qml'))
        for op in ('status', 'get_settings', 'set_settings', 'set_pro2_projection', 'begin_start', 'confirm_start',
                   'begin_recover', 'confirm_recover', 'cancel', 'stop', 'video_open', 'view_op'):
            self.assertIn(op, sent)
        for op in sent - {'frobnicate', 'video_open'}:  # the made-up op, and video_open, which the controller takes but no panel control sends yet
            self.assertIn(f'"{op}"', source, op)

    def test_both_directions_and_every_writer(self):
        kinds = {m['kind'] for m in self.corpus['controller_messages']}
        self.assertEqual(kinds, {'reply', 'state'})
        replies = [json.loads(m['text']) for m in self.corpus['controller_messages'] if m['kind'] == 'reply']
        self.assertTrue(any(r['ok'] for r in replies) and any(not r['ok'] for r in replies))
        self.assertEqual({f['writer'] for f in self.corpus['run_frames']}, {'controller', 'key_helper'})
        self.assertEqual(len(self.corpus['status_replies']), 2)
        for m in self.corpus['controller_messages']:
            if m['kind'] == 'state': self.assertEqual(list(json.loads(m['text']))[2:], self.corpus['snapshot_keys'])

    def test_verdicts_cover_both_outcomes(self):
        for key in ('run_cases', 'panel_cases'):
            self.assertEqual({c['verdict'] for c in self.corpus[key]}, {'accepted', 'refused'}, key)


if __name__ == '__main__':
    unittest.main()
