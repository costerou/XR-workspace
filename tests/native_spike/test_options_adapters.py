import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from tools.native_spike.view import ViewControl, ViewState
from tools.native_spike.options_actions import options_rows, options_apply
from tools.native_spike.options_editors import NativeEditors


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.store = Mock()
        self.c = ViewControl(ViewState(['XR-1','XR-3']), 'run', 'session', Mock(), self.store, Mock())

    def test_scope_and_removed_output_rejected(self):
        with self.assertRaises(ValueError): options_apply(self.c,'stabilization',50,'XR-3','monitor')
        with self.assertRaises(ValueError): options_rows(self.c,'monitor','XR-2','3d')
        self.assertEqual(options_rows(self.c,'monitor','XR-3','stabilization'),[])

    def test_what_the_pill_or_a_click_already_does_is_not_in_the_menu(self):
        from tools.native_spike.options_actions import CATEGORIES
        self.assertFalse({'placement','output','environment'} & set(CATEGORIES))
        shown = {r['id'] for scope in ('monitor','workspace') for cat in CATEGORIES for r in options_rows(self.c,scope,'XR-3',cat)}
        for gone in ('menu_toggle','menu_reset','placement_flat','placement_x','roll_level','resolution','scale','ultrawide','add_monitor',
                     'remove_monitor','background','background_brightness','environment_toggle','zoom_in','prev','next','send_glasses_1',
                     'video_pause','video_seek_fwd','video_format','video_quality','video_controls','playback_menu_toggle','video_exit'):
            self.assertNotIn(gone, shown)
            with self.assertRaises(ValueError): options_apply(self.c,gone,None,'XR-3','monitor')

    def test_the_privacy_row_says_what_it_does_and_whether_it_is_on(self):
        def privacy_row(): return next(r for r in options_rows(self.c,'workspace','XR-1','privacy') if r['id']=='privacy_display')
        self.assertEqual((privacy_row()['label'], privacy_row()['enabled'], privacy_row()['value']), ('Privacy: main display off', False, ''))
        self.c.privacy = Mock(blanked=None)
        self.assertEqual((privacy_row()['enabled'], privacy_row()['value']), (True, 'off'))
        self.c.privacy.blanked = 'DP-1'
        self.assertEqual(privacy_row()['value'], 'on')

    def test_quality_calls_real_video_owner_and_validates(self):
        from tools.native_spike.video import VideoControl
        video = VideoControl(self.c, threads=False)
        self.c.options_video = video
        options_apply(self.c,'quality_deband',True,'XR-1','workspace')
        self.assertTrue(self.c.state.video_quality['deband'])
        options_apply(self.c,'quality_deband','False','XR-1','workspace')
        self.assertFalse(self.c.state.video_quality['deband'])
        with self.assertRaises(ValueError): options_apply(self.c,'quality_deband','invalid','XR-1','workspace')

    def test_file_child_calls_video_and_text_never_runs_shell(self):
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp)/'video.mp4'; file.write_bytes(b'not decoded in this test')
            editor = NativeEditors(self.c,tmp)
            self.c.options_video = Mock()
            editor.open('file')
            self.assertEqual(editor.rows()[2]['label'],'video.mp4')
            editor.apply('editor.file.0')
            self.c.options_video.on_message.assert_called_once()
            args,kwargs=self.c.options_video.on_message.call_args
            self.assertEqual(args,({'op':'video_open','payload':{'path':str(file)}},))
            self.assertTrue(callable(kwargs['done']))
            editor.open('url')
            editor.apply('editor.text','$(touch /tmp/no)')
            with self.assertRaises(ValueError): editor.apply('editor.play')
            self.assertEqual(editor.kind,'url')

class NativeOwnerTests(unittest.TestCase):
    def test_real_keybind_capture_cancel_and_reset(self):
        from tools.native_spike.keybinds import BindControl
        c = ViewControl(ViewState(['XR-1']), 'run', 'session', Mock(), Mock(), Mock())
        c.keys = BindControl(c)
        editor = NativeEditors(c)
        self.assertIn(b'binds',editor.open('keybinds'))
        self.assertTrue(c.keys.open)
        editor.apply('editor.bind.0')
        self.assertTrue(c.keys.capturing)
        # Existing binds_key route handles capture; scalar options values stay scalar.
        c.keys.key(0,9)
        self.assertFalse(c.keys.capturing)
        editor.apply('editor.bind.reset')
        editor.close()
        self.assertFalse(c.keys.open)

    def test_real_xkb_text_shift_backspace_and_submit(self):
        keymap = '''xkb_keymap {
          xkb_keycodes { include "evdev+aliases(qwerty)" };
          xkb_types { include "complete" };
          xkb_compatibility { include "complete" };
          xkb_symbols { include "pc+us+inet(evdev)" };
        };'''
        c = ViewControl(ViewState(['XR-1']), 'run', 'session', Mock(), Mock(), Mock())
        c.options_video = Mock()
        editor = NativeEditors(c)
        editor.open('url')
        editor.begin_text(keymap)
        editor.key(38) # a
        editor.text_input.modifiers(depressed=1)
        editor.key(38) # A
        editor.text_input.modifiers()
        self.assertEqual(editor.text,'aA')
        editor.key(22)
        self.assertEqual(editor.text,'a')
        editor.apply('editor.text','https://example.com/video')
        editor.key(36)
        c.options_video.on_message.assert_called_once()
        args,kwargs=c.options_video.on_message.call_args
        self.assertEqual(args,({'op':'video_open','payload':{'url':'https://example.com/video'}},))
        self.assertTrue(callable(kwargs['done']))
        self.assertIsNone(editor.text_input)

if __name__ == '__main__': unittest.main()

class SessionAdapterTests(unittest.TestCase):
    def test_native_stop_uses_existing_interrupt_unwind(self):
        c=ViewControl(ViewState(['XR-1']),'run','session',None,None,None)
        self.assertIn('stop',[r['id'] for r in options_rows(c,'workspace','XR-1','session')])
        with self.assertRaises(KeyboardInterrupt): options_apply(c,'stop','confirmed','XR-1','workspace')
        with self.assertRaises(ValueError): options_apply(c,'stop','','XR-1','workspace')

class ViewAdapterTests(unittest.TestCase):
    def test_follow_locked_choices_maximize_target_through_existing_actions(self):
        c=ViewControl(ViewState(['XR-1','XR-3']),'run','session',lambda name: None,None,None)
        options_apply(c,'mode_locked','','XR-3','monitor')
        self.assertTrue(c.state.zoomed)
        self.assertEqual(c.state.selected,1)
        self.assertEqual(c.state.mode,'locked')
