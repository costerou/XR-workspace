import unittest
from tools.pro2_runtime.launch import validate_projection
class ContractTests(unittest.TestCase):
    def test_projection_requires_explicit_test_scope(self):
        for value,approved in [(None,True),(44.5,False),(float('nan'),True),(9,True),(81,True)]:
            with self.assertRaises(ValueError):validate_projection(value,approved)
        self.assertEqual(validate_projection(44.5,True),44.5)
