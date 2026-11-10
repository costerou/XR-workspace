"""Every supervisor test has a recorded fate for the Rust rebuild (docs/supervisor-test-fates.md).

test_fates.json lists each file in this directory, and for each test class how many tests it has and what
happens to them when the supervisor moves to Rust: `port` (rewritten as a Rust test of `target`), `wire`
(kept, and pointed at the Rust bytes through `via`) or `keep` (the code under test is not ported). Single
tests may be dropped, each with its reason. This test fails when a file, class or test appears or goes away
without the record saying so, which is what keeps coverage from being lost silently.

    TMPDIR=/tmp /usr/bin/python3 tests/native_spike/test_fates.py --summary
"""
import ast
import collections
import json
from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
RECORD = HERE / 'test_fates.json'
FATES = {'port', 'wire', 'keep'}
# These use pytest, which the system Python lacks: their top-level test functions are counted from the source.
PYTEST_FILES = {'test_stabilization.py'}
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))  # the other test files import tools.*, as `unittest discover` from the repo root allows


def load():
    return json.loads(RECORD.read_text())


def discovered():
    """{file name: {class name: [test method names]}} for every test file here, as unittest discovery sees it."""
    found = collections.defaultdict(lambda: collections.defaultdict(list))

    def walk(suite):
        for test in suite:
            if isinstance(test, unittest.TestSuite):
                walk(test)
            elif type(test).__module__ != 'unittest.loader':
                module = type(test).__module__.rsplit('.', 1)[-1]
                found[module + '.py'][type(test).__name__].append(test._testMethodName)

    walk(unittest.TestLoader().discover(str(HERE)))
    for name in PYTEST_FILES:
        tree = ast.parse((HERE / name).read_text())
        found[name]['(functions)'] = [n.name for n in tree.body if isinstance(n, ast.FunctionDef) and n.name.startswith('test')]
    return found


def summary(record):
    totals = collections.Counter()
    for entry in record['tests'].values():
        for cls in entry['classes'].values():
            dropped = len(cls.get('drop', {}))
            totals[cls['fate']] += cls['tests'] - dropped
            totals['drop'] += dropped
    return totals


class FatesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.record = load()
        cls.found = discovered()

    def test_every_file_here_has_a_fate(self):
        tests = {p.name for p in HERE.glob('test_*.py')}
        helpers = {p.name for p in HERE.glob('*.py')} - tests - {'__init__.py'}
        self.assertEqual(sorted(tests - set(self.record['tests'])), [], 'test files with no recorded fate')
        self.assertEqual(sorted(helpers - set(self.record['helpers'])), [], 'helper files with no recorded fate')
        for name, entry in self.record['helpers'].items():
            with self.subTest(helper=name):
                self.assertIn(entry['fate'], FATES)
                self.assertGreater(len(entry['reason']), 20)

    def test_a_file_leaves_only_once_its_ports_exist(self):
        # Phase 8 deletes Python tests. A record whose file is gone must say why, and every port must name its Rust test.
        for name, entry in self.record['tests'].items():
            if (HERE / name).exists():
                self.assertNotIn('retired', entry, name)
                continue
            with self.subTest(file=name):
                self.assertGreater(len(entry.get('retired', '')), 20, 'a removed file needs a "retired" reason')
                for cls_name, cls in entry['classes'].items():
                    if cls['fate'] == 'port':
                        self.assertTrue(cls.get('rust_test'), f'{name} {cls_name}: removed before its Rust port exists')

    def test_classes_and_counts_match_the_suite(self):
        for name, entry in self.record['tests'].items():
            if not (HERE / name).exists():
                continue
            with self.subTest(file=name):
                found = {cls: len(tests) for cls, tests in self.found.get(name, {}).items()}
                recorded = {cls: c['tests'] for cls, c in entry['classes'].items()}
                self.assertEqual(found, recorded, 'tests added or removed: record their fate in test_fates.json')

    def test_every_class_says_where_its_coverage_goes(self):
        for name, entry in self.record['tests'].items():
            for cls_name, cls in entry['classes'].items():
                with self.subTest(file=name, cls=cls_name):
                    self.assertIn(cls['fate'], FATES)
                    if cls['fate'] == 'port':
                        self.assertTrue(cls['target'].startswith('xr-workspace '), cls['target'])
                        if cls.get('rust_test'):
                            self.assertTrue((ROOT / cls['rust_test'].split('::')[0]).exists(), cls['rust_test'])
                    elif cls['fate'] == 'wire':
                        self.assertTrue((ROOT / cls['via']).exists(), cls['via'])
                        self.assertGreater(len(cls['how']), 20)
                    else:
                        self.assertGreater(len(entry.get('reason', '')), 20, 'a kept file says why')
                    if 'covers' in entry:
                        self.assertTrue((ROOT / entry['covers']).exists(), entry['covers'])

    def test_a_dropped_test_exists_and_says_why(self):
        for name, entry in self.record['tests'].items():
            for cls_name, cls in entry['classes'].items():
                for test, reason in cls.get('drop', {}).items():
                    with self.subTest(test=f'{name} {cls_name}.{test}'):
                        if (HERE / name).exists():
                            self.assertIn(test, self.found[name][cls_name])
                        self.assertGreater(len(reason), 40)
                        self.assertLess(len(cls['drop']), cls['tests'] + 1)

    def test_the_doc_carries_the_current_totals(self):
        totals = summary(self.record)
        doc = (ROOT / 'docs/supervisor-test-fates.md').read_text()
        line = '| ' + ' | '.join(str(totals[k]) for k in ('port', 'wire', 'keep', 'drop')) + f' | {sum(totals.values())} |'
        self.assertTrue(line in doc, f'docs/supervisor-test-fates.md needs the totals row {line} (run this file with --summary)')


if __name__ == '__main__':
    if sys.argv[1:] == ['--summary']:
        totals = summary(load())
        print('| ' + ' | '.join(str(totals[k]) for k in ('port', 'wire', 'keep', 'drop')) + f' | {sum(totals.values())} |')
    else:
        unittest.main()
