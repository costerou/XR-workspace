import json
import unittest
from tools.native_spike.options_text import TextInput

class TextRuntimeTests(unittest.TestCase):
    def test_actual_keyboard_names_and_shift_are_used(self):
        t=TextInput.from_names(dict(rules='evdev',model='pc105',layout='us',variant='',options=''))
        try:
            self.assertEqual(t.key(38),('text','a'))
            t.hypr_modifiers(1)
            self.assertEqual(t.key(38),('text','A'))
        finally: t.close()
