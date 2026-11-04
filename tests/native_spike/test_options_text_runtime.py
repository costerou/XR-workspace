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

    def test_runtime_uses_main_keyboard_and_releases_on_close(self):
        from types import SimpleNamespace
        from tools.native_spike.options_editors import NativeEditors
        from tools.native_spike.options_text import RuntimeText
        c=SimpleNamespace(keys=None)
        c.options_editor=NativeEditors(c);c.options_editor.open('url')
        class Client:
            def _request(self,request):
                assert request=='j/devices'
                return json.dumps({'keyboards':[dict(main=True,rules='evdev',model='pc105',layout='us',variant='',options='')]})
        translate=RuntimeText(c,Client())
        self.assertEqual(translate(1,38),'A')
        c.options_editor.close()
        self.assertIsNone(c.options_editor.text_input)
