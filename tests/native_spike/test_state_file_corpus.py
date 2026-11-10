"""The run-file corpus (state_file_corpus.py) is what the shipped writers produce today, reads back through the shipped
readers, and covers every journal state and every recovery reason."""
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest

from tests.native_spike import state_file_corpus as corpus
from tools.native_spike.ipc import read_private_json
from tools.native_spike.journal import Journal
from tools.native_spike.panel_consent import load_panel_consent

RESULT_KEYS = {'run_id', 'phase', 'complete', 'renderer_returncode', 'remaining', 'error'}
RESULT_EXTRA = {'stop_reason', 'usb_holds', 'recovery', 'faults'}
SOURCES = [Path(corpus.__file__).resolve().parents[2] / 'tools' / p
           for p in ('xr_controller/__main__.py', 'native_spike/managed.py', 'native_spike/journal.py')]


def tree(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(Path(root).rglob('*')) if p.is_file()}


class StateFileCorpusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = json.loads((corpus.ROOT / 'index.json').read_text())

    def private_copy(self, path):
        """A checked-in fixture as the readers require it: a 0600 file in a 0700 directory."""
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        os.chmod(tmp.name, 0o700)
        copy = Path(tmp.name) / path.name
        shutil.copyfile(path, copy); copy.chmod(0o600)
        return copy

    def test_corpus_is_what_the_writers_produce(self):
        with tempfile.TemporaryDirectory() as tmp:
            corpus.generate(Path(tmp) / 'out')
            fresh, kept = tree(Path(tmp) / 'out'), tree(corpus.ROOT)
        self.assertEqual(sorted(fresh), sorted(kept), 'regenerate: python3 -m tests.native_spike.state_file_corpus')
        for name in fresh: self.assertEqual(fresh[name], kept[name], f'{name} differs from what the writers produce')

    def test_journal_states_and_half_finished_runs(self):
        states = set()
        for name, entry in self.index['runs'].items():
            states |= set(entry.get('journal_states', []))
        self.assertEqual(states, {'pending', 'confirmed', 'done'})
        runs = self.index['runs']
        self.assertEqual(runs['in-flight-intent-only']['journal_states'], ['pending'])
        self.assertEqual(runs['in-flight-mid-create']['journal_states'], ['confirmed', 'pending'])
        self.assertEqual(runs['in-flight-desktop-removed']['journal_states'], ['confirmed', 'done'])
        for phase in ('verify', 'tracking', 'plugin', 'desktops'):
            self.assertTrue(any(name.startswith('in-flight') and json.loads(next((corpus.ROOT / 'runs' / name).glob('*.progress.json')).read_text())['phase'] == phase
                                for name in runs), phase)

    def test_every_recovery_reason_has_its_scenario(self):
        runs = self.index['runs']
        self.assertEqual(self.index['step_reasons'], corpus.STEP_REASONS)
        self.assertEqual(self.index['command_reasons'], list(corpus.COMMAND_REASONS))
        texts = set()
        for key in corpus.STEP_REASONS + list(corpus.COMMAND_REASONS):
            entry = runs['recover-' + key]
            path = corpus.ROOT / 'runs' / ('recover-' + key) / f'fx-recover-{key}.recovery.json'
            record = read_private_json(self.private_copy(path))
            self.assertEqual(set(record), {'run_id', 'complete', 'remaining', 'reasons', 'at'})
            self.assertIs(record['complete'], False)
            self.assertEqual(record['reasons'], entry['reasons'])
            self.assertTrue(record['reasons'], key)
            texts.add(tuple(record['reasons']))
            journal = json.loads((path.parent / 'journal.json').read_text())
            left = [r['id'] for r in journal['operations'] if r['state'] != 'done']
            if key in corpus.STEP_REASONS: self.assertEqual(sorted(record['remaining']), sorted(left), key)
            else: self.assertIn(record['remaining'], ([], left), key)
        self.assertEqual(len(texts), len(corpus.STEP_REASONS) + len(corpus.COMMAND_REASONS), 'each reason is a different text')
        # A command-level reason is the shipped code's own words, so the corpus fails when they change.
        source = '\n'.join(p.read_text() for p in SOURCES)
        for reason in filter(None, corpus.COMMAND_REASONS.values()): self.assertIn(reason.split('{')[0].split(': ')[0], source)
        self.assertIn('recovery did not finish, and nothing said why', source)
        # A finished Recover leaves no .recovery.json and rewrites the result clean.
        for name in ('recovered', 'ended-compositor-gone'):
            folder = corpus.ROOT / 'runs' / name
            self.assertFalse(list(folder.glob('*.recovery.json')))
            result = json.loads(next(folder.glob('*.result.json')).read_text())
            self.assertEqual((result['complete'], result['remaining'], result['error']), (True, [], None))
            self.assertNotIn('recovery', result)
            self.assertEqual(runs[name]['journal_states'], ['done'])
            before = json.loads((folder / 'journal.before.json').read_text())
            self.assertTrue({'pending', 'confirmed'} & {r['state'] for r in before['operations']})

    def test_files_read_back_through_the_shipped_readers(self):
        for folder in sorted((corpus.ROOT / 'runs').iterdir()):
            run_id = 'fx-' + folder.name
            consent = load_panel_consent(self.private_copy(folder / (run_id + '.json')), corpus.INSTANCE, corpus.TARGET)
            self.assertEqual(consent['run_id'], run_id)
            for name in ('journal.json', 'journal.before.json'):
                if not (folder / name).exists(): continue
                raw = json.loads((folder / name).read_text())
                copy = self.private_copy(folder / name)
                os.replace(copy, copy.parent / 'journal.json')
                journal = Journal.open(copy.parent / 'journal.json', run_id, raw['session'], existing_only=True)
                self.assertEqual(journal.operations, raw['operations'])
                self.assertEqual(journal.broker, raw.get('broker'))
            for path in folder.glob('*.progress.json'):
                self.assertEqual(read_private_json(self.private_copy(path)), {'run_id': run_id, 'phase': json.loads(path.read_text())['phase']})
            for path in folder.glob('*.result.json'):
                result = read_private_json(self.private_copy(path))
                self.assertTrue(RESULT_KEYS <= set(result) <= RESULT_KEYS | RESULT_EXTRA, path)
                self.assertEqual(result['run_id'], None if path.name == 'invalid.result.json' else run_id)
        for path in sorted((corpus.ROOT / 'consents').glob('*.json')):
            self.assertEqual(load_panel_consent(self.private_copy(path), corpus.INSTANCE, corpus.TARGET)['run_id'], path.stem)
        self.assertEqual(sorted(p.name for p in (corpus.ROOT / 'consents').glob('*.json')), sorted(self.index['consents']))


if __name__ == '__main__':
    unittest.main()
