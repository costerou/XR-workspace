"""The incident corpus (incident_corpus.py): the committed golden file matches what the shipped Python monitor leaves, and
the gate scenario is an injected renderer crash that the report explains."""
import json
import unittest

from tests.xr_controller import incident_corpus as corpus


class IncidentCorpusTest(unittest.TestCase):
    def test_file_matches_the_python(self):
        self.assertEqual(corpus.FILE.read_text(), corpus.dumps(corpus.corpus()),
                         'regenerate: TMPDIR=/tmp /usr/bin/python3 -m tests.xr_controller.incident_corpus --write')

    def test_the_gate_is_an_injected_renderer_crash(self):
        crash = next(s for s in json.loads(corpus.FILE.read_text())['scenarios'] if s['name'] == 'renderer_crash')
        files = crash['expect']['files']
        report = json.loads(next(text for name, text in files.items() if name.endswith('-renderer-crash.json')))
        self.assertEqual(report['renderer']['signal'], 'SIGSEGV')
        self.assertIn('_ZN2xr12Presentation4drawEv', report['cause']['text'])
        self.assertEqual(crash['expect']['handed'], [name for name in files if name.endswith('.json') and not name.startswith('.')])

    def test_every_scenario_is_named_once(self):
        names = [build.__name__ for build in corpus.SCENARIOS]
        self.assertEqual(len(names), len(set(names)))


if __name__ == '__main__':
    unittest.main()
