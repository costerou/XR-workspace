import tempfile
import unittest
from pathlib import Path
from tools.native_spike.view import ViewControl,ViewState
from tools.native_spike import options_geometry as geo
from tools.native_spike.options_editors import NativeEditors

class ReviewRegressions(unittest.TestCase):
    def control(self): return ViewControl(ViewState(['XR-1']),'run','session',lambda name:None,None,None)
    def test_keyboard_header_scope_category_and_focus_scroll(self):
        c=self.control();c.options.open('XR-1');c.options_key('Shift+Tab')
        self.assertLess(c.options.snapshot()['focus'],0)
        c.options.intent('options_focus',focus=-4);c.options_key('Return')
        self.assertEqual(c.options.snapshot()['scope'],'workspace')
        c.options.intent('options_focus',focus=-3);c.options_key('Right')
        self.assertEqual(c.options.snapshot()['category'],'output')
        c.options._rows=lambda *a:[dict(id=str(i),label=str(i),scope='workspace',kind='action',value='',enabled=True) for i in range(30)]
        c.options.refresh();c.options.intent('options_focus',focus=20)
        self.assertGreater(c.options.snapshot()['scroll'],0)
    def test_slider_matches_native_track_min_mid_max_and_stationary_knob(self):
        row=dict(minimum=-180,maximum=180,step=1)
        for x,want in ((300,-180),(375,0),(450,180)):
            self.assertEqual(geo.range_value(row,x),want)
    def test_file_path_edits_commit_only_on_enter(self):
        with tempfile.TemporaryDirectory() as root:
            child=Path(root)/'narrow';child.mkdir()
            c=self.control();c.options_editor=NativeEditors(c,root);c.options.open('XR-1');c.options_editor.open('file');c.options.refresh()
            c.options_editor.text='';c.options_editor.text_field='editor.path'
            c.options_text(str(child))
            self.assertEqual(c.options_editor.directory,Path(root))
            c.options_key('Return')
            self.assertEqual(c.options_editor.directory,child)
    def test_unicode_file_labels_obey_wire_byte_bounds(self):
        with tempfile.TemporaryDirectory() as root:
            (Path(root)/('é'*60)).touch()
            editor=NativeEditors(self.control(),root);editor.open('file')
            self.assertTrue(all(len(r['label'].encode())<=96 for r in editor.rows()))
