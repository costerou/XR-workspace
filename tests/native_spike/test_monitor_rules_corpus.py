"""The phase 6 monitor rules corpus (monitor_rules_corpus.py): the committed golden file matches monitor_rules.py."""
import json
import unittest

import monitor_rules_corpus as corpus


class MonitorRulesCorpusTest(unittest.TestCase):
    def test_file_matches_the_python(self):
        expected = corpus.dumps(corpus.monitor_rules_corpus())
        self.assertEqual(json.loads(corpus.MONITOR_RULES_FILE.read_text()), json.loads(expected),
                         'regenerate: tests/native_spike/monitor_rules_corpus.py --write')
        self.assertEqual(corpus.MONITOR_RULES_FILE.read_text(), expected)

    def test_only_one_line_rules_go_and_every_other_byte_stays(self):
        for case in corpus.monitor_rules_corpus()['cases']:
            if 'raw' not in case: continue
            lines = case['raw'].splitlines(keepends=True)
            for entry in case['remove']:
                if entry.get('result') is None: continue
                with self.subTest(case['name'], names=entry['names']):
                    gone = {r['line'] for r in case['find'] if r['removable'] and r['name'] in entry['names']}
                    self.assertTrue(gone)
                    self.assertEqual(entry['result'], ''.join(l for n, l in enumerate(lines, 1) if n not in gone))


if __name__ == '__main__':
    unittest.main()
