import json
import unittest
from tools.native_spike.supervisor import Supervisor
from tools.native_spike.view import ViewControl,ViewState

class StatusTests(unittest.TestCase):
 def control(self):return ViewControl(ViewState(['XR-1']),'run','session',None,None,None)
 def test_readback_does_not_replace_measured_status(self):
  c=self.control();watch=Supervisor(None,None,control=c)
  watch.status_bytes(b'{"presented_hz":60}\n')
  data=dict(video=None,immersive=None,outputs=[])
  watch.status_bytes((json.dumps(dict(owner_readback=data))+'\n').encode())
  self.assertEqual(c.owner_readback,data)
  self.assertEqual(watch.status,{'presented_hz':60})
 def test_nonfinite_overbound_and_bad_top_keys_rejected(self):
  c=self.control();watch=Supervisor(None,None,control=c)
  for data in ({'video':None},dict(video=None,immersive={},outputs=[],unknown=True),dict(video={'opened':float('nan')},immersive={},outputs=[])):
   watch.status_bytes((json.dumps(dict(owner_readback=data))+'\n').encode())
   self.assertIsNone(c.owner_readback)
 def test_refusal_correlates_id_and_does_not_complete_other_request(self):
  c=self.control();m=c.options;m.open('XR-1');token=m.begin_owner('action','XR-1','monitor');m.pending_ids={42}
  watch=Supervisor(None,None,control=c)
  watch.status_bytes(b'{"control_refused":{"request_id":41,"op":"view","detail":"wrong request"}}\n');m.tick()
  self.assertEqual(m.snapshot()['status'],'pending')
  watch.status_bytes(b'{"control_refused":{"request_id":42,"op":"view","detail":"bad output"}}\n');m.tick()
  self.assertEqual(m.snapshot()['status'],'error')
