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
  if len(cls.geometries)!=216: raise AssertionError('Native Restore fixture must emit all 216 production geometry cases')
 def test_real_native_band_through_the_sampler_without_an_app_press(self):
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
     client.position=(output['x']+300,output['y'])
     for tick in range(40):sampler.sample_restore([output]);p.poll(1+tick*.01)
     self.assertEqual(p.mode,'restore')
     sampler.sample_restore([output]);p.poll(1.5)
     self.assertEqual(client.calls,['grab'])       # only now, after the rest
     rx,ry,rw,rh=geometry['rect']
     cx,cy=fp.band_canvas(geometry,rx+rw/2,ry+rh/2)
     for step in range(60):      # a hand: short samples, the leash taking the hidden cursor back
      dx,dy=(cx-p.band_point[0])/p.band_gain,(cy-p.band_point[1])/p.band_gain
      if abs(dx)<1 and abs(dy)<1:break
      k=min(1,100/math.hypot(dx,dy))
      client.position=(client.position[0]+dx*k,client.position[1]+dy*k)
      sampler.sample_restore([output]);p.poll(1.6+step*.01)
     self.assertTrue(p.band_hot())
     # Only after modal UI grab exists does the button route through ViewControl.
     c.on_message({'op':'resize_press'});self.assertTrue(c.state.zoomed)
     frame=c.on_message({'op':'resize_release'})
     self.assertIn(b'"zoomed":false',frame)
     self.assertFalse(c.state.zoomed);self.assertEqual(c.state.selected,1)
     self.assertEqual(c.state.maximized_mode,'locked');self.assertEqual(c.state.placements,original)
     sampler.sample_restore([output])
     self.assertIn('release',client.calls)
    finally:p.close()
