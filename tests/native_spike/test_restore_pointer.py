"""Offline restore acquisition through the real cursor sampler feed and click route."""
import copy
import unittest
from tools.native_spike import free_pointer as fp, view, orbit, unified_cursor as uc

OUTPUT = dict(index=1, x=1000, y=0, width=800, height=600)

def geometry():
    # A settled, flat native surface. UI mesh intentionally over content for reachable route testing.
    return dict(monitor='XR-3', maximized=True, canvas=[800,600,1,0.75],
                tile=[0,0,800,600], placement=[0,0,1,0,0],rect=[350,250,100,100],
                hit_mesh=[[-.125,.125,-1],[-.125,-.125,-1],[.125,.125,-1],[.125,-.125,-1]],
                view=[1,0,0,0,1,0,0,0,1])

class RestoreTests(unittest.TestCase):
    def control(self):
        c=view.ViewControl(view.ViewState(['XR-1','XR-3']), 'run','session',lambda n:None,None,None)
        c.state.selected=1; c.state.apply('zoom_in')
        p=fp.OverviewPointer(c); c.overview_pointer=p
        self.addCleanup(p.close)
        c.set_maximize_geometry(geometry())
        return c,p
    def test_acquire_only_ui_and_release_to_app(self):
        c,p=self.control()
        p.feed((1100,100),(0,0),[OUTPUT],0); p.poll(0)
        self.assertNotEqual(p.want[1],'free')
        p.feed((1400,300),(0,0),[OUTPUT],p.want[0]); p.poll(1)
        self.assertEqual(p.want[1],'free')
        self.assertEqual(p.mode,'restore')
        p.feed((1400,300),(150,0),[OUTPUT],p.want[0]); p.poll(2)
        self.assertEqual(p.want[1],'desktop')
        self.assertEqual(p.want[2],(1550,300))
    def test_press_release_runs_real_restore_preserving_selection_and_placements(self):
        c,p=self.control(); original=copy.deepcopy(c.state.placements)
        p.feed((1400,300),(0,0),[OUTPUT],0); p.poll(1)
        p.on_op('resize_press')
        self.assertTrue(c.state.zoomed)
        result=p.on_op('resize_release')
        self.assertFalse(c.state.zoomed)
        self.assertEqual(c.state.selected,1)
        self.assertEqual(c.state.placements,original)
        self.assertIn(b'"zoomed":false',result)
    def test_removed_or_changed_geometry_cancels_held_restore(self):
        c,p=self.control()
        p.feed((1400,300),(0,0),[OUTPUT],0); p.poll(1); p.on_op('resize_press')
        c.set_maximize_geometry(None)
        p.on_op('resize_release')
        self.assertTrue(c.state.zoomed)
        self.assertNotEqual(p.want[1],'free')
    def test_actual_above_desktop_mesh_cannot_be_acquired_from_app_pixels(self):
        c,p=self.control(); g=geometry()
        g['hit_mesh']=[[x,y+1,z] for x,y,z in g['hit_mesh']]
        c.set_maximize_geometry(g)
        for y in (0,1,100,300,599):
            p.feed((1400,y),(0,0),[OUTPUT],p.want[0]); p.poll(y)
            self.assertNotEqual(p.want[1],'free')
    def test_malformed_geometry_rejected(self):
        for patch in ({'extra':1},{'view':[1]*9},{'hit_mesh':[[float('nan'),0,1]]},{'monitor':'0'}):
            c,p=self.control()
            with self.assertRaises(ValueError): fp.parse_maximize_geometry(dict(geometry(),**patch))

class RestoreSamplerTests(unittest.TestCase):
    def test_sampler_grabs_only_after_hit_and_removes_subscription_on_exit(self):
        c=view.ViewControl(view.ViewState(['XR-1','XR-3']),'run','session',lambda n:None,None,None)
        c.state.selected=1; c.state.apply('zoom_in')
        p=uc.UnifiedPointer(c); c.overview_pointer=p; self.addCleanup(p.close)
        c.set_maximize_geometry(geometry())
        class Client:
            def __init__(self): self.position=(1100,100); self.calls=[]
            def query(self,*args,**kwargs): return dict(x=self.position[0],y=self.position[1])
            def grab_pointer(self,*args): self.calls.append(('grab',args)); self.position=args[-2:]
            lock_pointer=grab_pointer
            def release_pointer(self,*args): self.calls.append(('release',args)); self.position=args or self.position
            def remove_resize_binds(self): self.calls.append(('remove',))
        client=Client(); sampler=uc.UnifiedSampler(client,['XR-1','XR-3'],p,())
        self.assertFalse(sampler.sample_restore([OUTPUT])); p.poll(1)
        self.assertEqual(client.calls,[])
        client.position=(1400,300)
        sampler.sample_restore([OUTPUT]); p.poll(2)
        self.assertTrue(sampler.sample_restore([OUTPUT])); p.poll(3)
        self.assertTrue(sampler.sample_restore([OUTPUT])); p.poll(4)
        self.assertEqual(client.calls[0][0],'grab')
        client.position=(1550,300)
        sampler.sample_restore([OUTPUT]); p.poll(5)
        sampler.sample_restore([OUTPUT]); p.poll(6)
        self.assertIn(('release',(1550,300)),client.calls)
        self.assertFalse(sampler.grabbed)
    def test_projection_uses_native_placement_and_actual_view(self):
        import math
        g=fp.parse_maximize_geometry(geometry())
        g['placement'][0]=math.pi/2
        # Azimuth rotates the surface center to world +X. The actual view
        # rotates it back into the eye; using raw screen UV would lose this.
        g['view']=[0,0,1,0,1,0,-1,0,0]
        ray=fp.restore_cursor_ray(g,OUTPUT,(1400,300))
        self.assertAlmostEqual(ray[0],1)
        self.assertAlmostEqual(ray[2],0)
        g['view']=[1,0,0,0,1,0,0,0,1]
        self.assertIsNone(fp.restore_cursor_ray(g,OUTPUT,(1400,300)))
