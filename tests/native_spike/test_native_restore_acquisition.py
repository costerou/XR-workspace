"""Release gate: production native serializer/mesh -> real supervisor input route.

Build native CPU target restore_app_bound_test, then set
XR_RESTORE_GEOMETRY_BINARY=/path/to/restore_app_bound_test. No GPU/device needed.
"""
import json
import math
import os
from pathlib import Path
import subprocess
import unittest
from tools.native_spike import free_pointer as fp, unified_cursor as uc, view

class NativeRestoreTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  executable=os.environ.get('XR_RESTORE_GEOMETRY_BINARY')
  if not executable: raise unittest.SkipTest('CPU native Restore serializer fixture not selected')
  if not Path(executable).is_file(): raise RuntimeError('Build CPU restore_app_bound_test first')
  result=subprocess.run([executable,'--dump-json'],capture_output=True,text=True,timeout=20,check=True)
  cls.geometries=[json.loads(line)['maximize_geometry'] for line in result.stdout.splitlines()]
  if len(cls.geometries)!=108: raise AssertionError('Native Restore fixture must emit all108 production geometry cases')
 def test_real_native_inset_mesh_acquires_clicks_and_restores_without_app_press(self):
  visible=0
  for geometry in self.geometries:
   with self.subTest(tile=geometry['tile'],placement=geometry['placement']):
    c=view.ViewControl(view.ViewState(['XR-1','XR-3']),'run','session',lambda n:None,None,None)
    c.state.selected=1;c.state.maximized_mode='locked';c.state.apply('zoom_in')
    original=[dict(p) for p in c.state.placements]
    p=uc.UnifiedPointer(c);c.overview_pointer=p
    output=dict(index=1,x=1920,y=80,width=1920,height=1080)
    class Client:
     def __init__(self):self.calls=[];self.position=(0,0)
     def query(self,*a,**kw):return dict(x=self.position[0],y=self.position[1])
     def lock_pointer(self,*args):self.calls.append('grab');self.position=args[-2:]
     def release_pointer(self,*args):self.calls.append('release');self.position=args or self.position
     def warp_cursor(self,*args):self.position=args
     def remove_resize_binds(self):self.calls.append('remove')
    client=Client();sampler=uc.UnifiedSampler(client,['XR-1','XR-3'],p,())
    try:
     c.set_maximize_geometry(geometry)
     tx,ty,tw,th=geometry['tile'];rx,ry,rw,rh=geometry['rect']
     # Compute only an admissible REAL integer app pixel; do not alter native mesh.
     client.position=(output['x']+int((rx+rw/2-tx)/tw*output['width']),output['y']+int((ry+rh/2-ty)/th*output['height']))
     expected=client.position
     ray=fp.restore_cursor_ray(geometry,output,expected)
     if ray is None:
      sampler.sample_restore([output]);p.poll(1)
      self.assertNotEqual(p.mode,'restore');self.assertEqual(client.calls,[])
      continue
     visible+=1
     self.assertTrue(fp.restore_mesh_hit(geometry,ray))
     for tick in (1,2,3):sampler.sample_restore([output]);p.poll(tick)
     self.assertEqual(client.calls,['grab'])
     self.assertEqual(p.mode,'restore')
     # Only after modal UI grab exists does the button route through ViewControl.
     c.on_message({'op':'resize_press'});self.assertTrue(c.state.zoomed)
     frame=c.on_message({'op':'resize_release'})
     self.assertIn(b'"zoomed":false',frame)
     self.assertFalse(c.state.zoomed);self.assertEqual(c.state.selected,1)
     self.assertEqual(c.state.maximized_mode,'locked');self.assertEqual(c.state.placements,original)
     sampler.sample_restore([output])
     self.assertIn('release',client.calls)
     self.assertEqual(client.position,expected)
    finally:p.close()
  self.assertEqual(visible,108,'Native fixture puts Restore in actual visible camera for every scenario')
