"""The phase 0 journal corpus (journal_corpus.py): the committed golden file matches the shipped Python journal and
recovery, and it covers every kind of record, fault and refusal the Rust port is held to."""
import json
import unittest

import journal_corpus as corpus


class JournalCorpusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.made = corpus.make()

    def test_committed_corpus_matches_python(self):
        committed = corpus.CORPUS_FILE.read_text()
        self.assertEqual(committed, corpus.dump(self.made),
                         'rust/corpus/journal/cases.json is stale: run TMPDIR=/tmp /usr/bin/python3 '
                         'tests/native_spike/journal_corpus.py --write')

    def test_every_fault_kind_is_injected(self):
        kinds = {case['fault']['kind'] for s in self.made['scenarios'] for case in s['cases'] if case['fault']}
        self.assertEqual(kinds, set(corpus.FAULTS))

    def test_every_record_kind_and_state_is_recovered(self):
        records = [r for s in self.made['scenarios'] for r in json.loads(s['journal'].lstrip('﻿'))['operations']]
        self.assertEqual({r['operation']['kind'] for r in records}, {'output', 'workspace', 'setting'})
        self.assertEqual({r['state'] for r in records}, {'pending', 'confirmed', 'done'})

    def test_faults_leave_intent_and_a_retry_finishes_what_it_can(self):
        for scenario in self.made['scenarios']:
            clean = scenario['cases'][0]
            self.assertIsNone(clean['fault'])
            for case in scenario['cases'][1:]:
                with self.subTest(scenario=scenario['name'], fault=case['fault']):
                    # A fault never completes more than a clean recovery, and a clean retry reaches the same end.
                    self.assertLessEqual(case['first']['complete'], clean['first']['complete'])
                    self.assertEqual(case['retry']['complete'], clean['first']['complete'])
                    self.assertEqual(case['retry']['compositor'], clean['first']['compositor'])

    def test_refusals(self):
        self.assertTrue(self.made['refused'])
        for case in self.made['refused']: self.assertIn('error', case)


if __name__ == '__main__':
    unittest.main()
