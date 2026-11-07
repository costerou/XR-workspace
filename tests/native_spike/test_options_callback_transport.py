import unittest
from types import SimpleNamespace
from tools.native_spike.view import ViewControl,ViewState

class CallbackTransportTests(unittest.TestCase):
 def control(self):return ViewControl(ViewState(['XR-1']),'run','session',None,None,None)
 def test_output_callback_consumed_only_at_start(self):
  c=self.control();calls=[];done=lambda *a: calls.append(a)
  class Manager:
   def start(self,change,done=None):self.seen=done;return True
  manager=c.desktops=Manager();c.options_output_done=done
  change=SimpleNamespace(remove=None)
  c._start_change(change)
  self.assertIs(manager.seen,done);self.assertIsNone(c.options_output_done)
  self.assertEqual(calls,[]) # Transport does not invent completion.
 def test_output_start_refusal_reports_owner_failure(self):
  c=self.control();calls=[];c.options_output_done=lambda *a:calls.append(a)
  c.desktops=SimpleNamespace(start=lambda change,done=None:False)
  c._start_change(SimpleNamespace(remove=None))
  self.assertEqual(calls,[(False,'Output owner refused transaction')])
 def test_immersive_callback_is_one_shot_not_each_poll(self):
  c=self.control();calls=[];requests=[]
  c.immersive_run=SimpleNamespace(request=lambda wanted,**kw:requests.append((wanted,kw)))
  done=lambda *a:calls.append(a);c.options_immersive_done=done
  c._immersive_sync();c._immersive_sync()
  self.assertIs(requests[0][1]['done'],done)
  self.assertEqual(requests[1][1],{})
  self.assertEqual(calls,[])
 def test_refused_remove_start_restores_prospectively_released_view_identity(self):
  c=ViewControl(ViewState(['XR-1','XR-3']),'run','session',None,None,None)
  c.state.selected=1
  before=list(c.state.names)
  c.desktops=SimpleNamespace(start=lambda change,done=None:False)
  calls=[];c.options_output_done=lambda *a:calls.append(a)
  c._start_change(SimpleNamespace(remove={'name':'XR-3'}))
  self.assertEqual(c.state.names,before)
  self.assertEqual(c.state.selected,1)
  self.assertEqual(calls,[(False,'Output owner refused transaction')])
 def test_readback_aware_worker_always_receives_actual_setting_without_callback(self):
  c=self.control();requests=[]
  c.immersive_run=SimpleNamespace(observe_renderer=lambda value:None,request=lambda wanted,**kw:requests.append(kw))
  c._immersive_sync();c._immersive_sync()
  self.assertEqual([r['setting'] for r in requests],[c.state.immersive,c.state.immersive])
