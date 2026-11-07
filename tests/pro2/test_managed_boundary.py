"""Unsupported model cannot reach display/session operations via direct APIs."""
from pathlib import Path
from unittest import TestCase, mock
from tools.native_spike import managed


class ManagedBoundary(TestCase):
    def test_pro2_rejected_before_any_client_operation(self):
        for entry in ('run_managed', 'recover_managed'):
            with self.subTest(entry=entry):
                client = mock.Mock()
                with self.assertRaisesRegex(ValueError, 'PRO 2'):
                    getattr(managed, entry)(client, {'model': 'pro2'}, Path('/nonexistent/pro2'))
                self.assertEqual(client.mock_calls, [])
