"""The phase 6 run-order corpus (run_order_corpus.py): the committed golden file matches managed.py, and every failure
point of every scenario has a case."""
import json
import unittest

import run_order_corpus as corpus


class RunOrderCorpusTest(unittest.TestCase):
    def test_file_matches_the_python(self):
        expected = corpus.dumps(corpus.run_order_corpus())
        self.assertEqual(json.loads(corpus.RUN_ORDER_FILE.read_text()), json.loads(expected),
                         'regenerate: tests/native_spike/run_order_corpus.py --write')
        self.assertEqual(corpus.RUN_ORDER_FILE.read_text(), expected)

    def test_every_fallible_step_fails_somewhere(self):
        failed = {step for case in corpus.run_order_corpus()['cases'] for step, _ in case['config'].get('fail', [])}
        self.assertEqual(corpus.FALLIBLE - failed, set())

    def test_recovery_wakes_the_fallback_first_and_removes_outputs_last(self):
        # The invariants the order carries (docs/rust-rebuild-plan.md, "Invariants"): once recovery starts, the fallback
        # is woken before anything else is put back, and the journal (outputs last) only after the windows are back.
        for case in corpus.run_order_corpus()['cases']:
            trace = [line.split(' ')[0] for line in case['trace']]
            if 'journal.recover' not in trace: continue
            with self.subTest(case['name']):
                wake = len(trace) - 1 - trace[::-1].index('privacy.wake')
                self.assertEqual(trace[wake:wake + 2], ['privacy.wake', 'sbs.restore_2d'])
                recovering = trace[wake:]
                self.assertLess(recovering.index('layout.unpin'), recovering.index('journal.recover'))
                if 'layout.restore' in recovering:
                    self.assertLess(recovering.index('layout.restore'), recovering.index('journal.recover'))


if __name__ == '__main__':
    unittest.main()
