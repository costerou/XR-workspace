import contextlib
import io
import json
import tempfile
import unittest

from tools.luma_probe.__main__ import main
from fake_host import FakeHost


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.host = FakeHost(self.tmp.name)

    def run_cli(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(['--root', self.tmp.name, *argv])
        return code, out.getvalue()

    def test_absent_exit_code(self):
        self.assertEqual(self.run_cli()[0], 2)

    def test_ready_exit_code_and_text(self):
        self.host.glasses_on_hub()
        code, text = self.run_cli()
        self.assertEqual(code, 0)
        self.assertIn('ready', text.lower())

    def test_busy_exit_code(self):
        self.host.glasses_on_hub().process(77, 'xrDriver')
        self.assertEqual(self.run_cli()[0], 1)

    def test_json_output_parses(self):
        self.host.glasses_on_hub()
        code, text = self.run_cli('--json')
        data = json.loads(text)
        self.assertEqual((code, data['status']), (0, 'ready'))
        self.assertEqual(data['devices'][0]['interfaces'][0]['endpoints'][2]['address'], 0x83)
