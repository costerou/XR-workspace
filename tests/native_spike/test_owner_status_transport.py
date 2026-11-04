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
 def test_real_video_wrapper_forwards_geometry_readback_receipts_and_refusals(self):
  from tools.native_spike.video import VideoControl
  from tools.native_spike.free_pointer import OverviewPointer
  from tests.native_spike.test_restore_pointer import geometry
  c=self.control();p=OverviewPointer(c);c.overview_pointer=p;self.addCleanup(p.close)
  wrapped=VideoControl(c);watch=Supervisor(None,None,control=wrapped)
  g=geometry();g['monitor']='XR-1'
  watch.status_bytes((json.dumps(dict(maximize_geometry=g))+'\n').encode())
  self.assertEqual(p.maximize_geometry['monitor'],'XR-1')
  report=dict(video=None,immersive=None,outputs=[dict(name='XR-1',ready=True,width=800,height=600)])
  watch.status_bytes((json.dumps(dict(owner_readback=report))+'\n').encode())
  self.assertEqual(c.owner_readback,report);self.assertEqual(wrapped.owner_readback,report)
  m=c.options;m.open('XR-1');token=m.begin_owner('action','XR-1','monitor');m.pending_ids={42,43}
  watch.status_bytes(b'{"control_applied":42}\n');self.assertEqual(m.pending_ids,{43})
  watch.status_bytes(b'{"control_refused":{"request_id":43,"op":"view","detail":"native rejected"}}\n');m.tick()
  self.assertEqual(m.snapshot()['status'],'error')
  watch.status_bytes(b'{"maximize_geometry":null}\n');self.assertIsNone(p.maximize_geometry)
 def test_authoritative_immersive_report_reaches_engine_owner(self):
  from types import SimpleNamespace
  c=self.control();seen=[];c.immersive_run=SimpleNamespace(observe_renderer=seen.append)
  report=dict(video=None,immersive=None,outputs=[])
  watch=Supervisor(None,None,control=c)
  watch.status_bytes((json.dumps(dict(owner_readback=report))+'\n').encode())
  self.assertEqual(seen,[None])
