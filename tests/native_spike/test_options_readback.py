import json
import unittest
from tools.native_spike.options_menu import OptionsMenu
from tools.native_spike.view import ViewControl, ViewState

class ReadbackTests(unittest.TestCase):
 def test_renderer_owned_change_waits_for_backend_receipt_and_value(self):
  c=ViewControl(ViewState(['XR-1']),'run','session',None,None,None)
  value=['10']
  def rows(*a): return [dict(id='gain',label='Gain',scope='monitor',kind='range',value=value[0],minimum=0,maximum=100,step=1,unit='%',enabled=True)]
  def apply(*a):
   value[0]='20'
   return (json.dumps(dict(op='view',request_id=42))+'\n').encode()
  m=OptionsMenu(c,rows=rows,apply=apply);m.open('XR-1')
  m.intent('options_set',row_id='gain',monitor='XR-1',scope='monitor',revision=m.snapshot()['revision'],value=20)
  m.refresh();self.assertEqual(m.snapshot()['status'],'pending')
  m.acknowledge(42);m.refresh();self.assertEqual(m.snapshot()['status'],'applied')

 def test_empty_action_return_never_counts_as_applied(self):
  c=ViewControl(ViewState(['XR-1']),'run','session',None,None,None)
  m=OptionsMenu(c,rows=lambda *a:[dict(id='send',label='Send',scope='monitor',kind='action',value='',enabled=True)],apply=lambda *a:b'')
  m.open('XR-1');m.intent('options_activate',row_id='send',monitor='XR-1',scope='monitor',revision=m.snapshot()['revision'],value='')
  m.refresh();self.assertEqual(m.snapshot()['status'],'pending')

 def test_native_placement_child_replaces_parent_surface(self):
  c=ViewControl(ViewState(['XR-1']),'run','session',None,None,None)
  c.options.open('XR-1');d=c.options.snapshot()
  c.options.intent('options_activate',row_id='menu_toggle',monitor='XR-1',scope='monitor',revision=d['revision'],value='')
  self.assertTrue(c.state.menu_open)
  self.assertFalse(c.options.snapshot()['open'])
