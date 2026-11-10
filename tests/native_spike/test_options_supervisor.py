import unittest
from tools.native_spike import options_menu as om
from tools.native_spike import options_geometry as geo

class State:
    names = ['XR-1', 'XR-3']
class Control:
    state = State()
    session = 'session'
    keys_open = False

class OptionsTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.value = '1'
        self.calls = []
        self.menu = om.OptionsMenu(Control(), clock=lambda: self.now,
            rows=lambda *args: [dict(id='distance', label='Distance', scope='monitor', kind='range', value=self.value,
                                    minimum=1, maximum=10, step=1, unit='m', enabled=True, choices=[])],
            apply=lambda *args: self.calls.append(args))
    def test_pending_requires_readback_and_stale_is_rejected(self):
        self.menu.open('XR-3')
        snap = self.menu.snapshot()
        fields = dict(row_id='distance', monitor='XR-3', scope='monitor', revision=snap['revision'], value=2)
        self.assertTrue(self.menu.intent('options_set', **fields))
        self.assertEqual(self.menu.snapshot()['status'], 'pending')
        self.assertFalse(self.menu.intent('options_set', **fields))
        self.value = '2'
        self.menu.refresh()
        self.assertEqual(self.menu.snapshot()['status'], 'applied')
    def test_focus_capture_escape_idle_and_desktop_handoff(self):
        self.menu.open('XR-1')
        self.menu.child = 'editor'
        self.assertTrue(self.menu.escape())
        self.assertTrue(self.menu.snapshot()['open'])
        self.menu.capture = {'monitor': 'XR-1'}
        self.now = 50
        self.menu.tick()
        self.assertTrue(self.menu.snapshot()['open'])
        self.menu.desktop_press()
        self.assertFalse(self.menu.keyboard_owned)
        self.menu.capture = None
        self.menu.tick()
        self.assertFalse(self.menu.snapshot()['open'])
    def test_strict_snapshot(self):
        self.menu.open('XR-1')
        good = self.menu.snapshot()
        for patch in ({'extra': 1}, {'focus': True}, {'monitor':'0'}, {'revision':-1}, {'message':'é'*129}):
            with self.assertRaises(ValueError): om.validate_snapshot(dict(good, **patch))
        bad = dict(good, rows=good['rows'] * 2)
        with self.assertRaises(ValueError): om.validate_snapshot(bad)
    def test_clipped_row_geometry(self):
        self.assertIsNone(geo.row_at(20, 87, 0, 64))
        self.assertEqual(geo.row_at(20, 88, 38, 64), 1)
        self.assertIsNone(geo.row_at(20, 392, 0, 64))

class WiringTests(unittest.TestCase):
    def control(self):
        from tools.native_spike import view
        return view.ViewControl(view.ViewState(['XR-1','XR-3']), 'run', 'session', lambda n: None, None, None)
    def test_closed_options_do_not_emit_unsolicited_frames(self):
        self.assertEqual(self.control()._options_frame(), b'')
    def test_open_maximized_keeps_identity_and_close_keeps_overview(self):
        c=self.control(); c.state.selected=1; c.state.apply('zoom_in')
        c.options._rows=lambda *args: []
        c.options_intent('options_open', monitor='XR-3')
        self.assertFalse(c.state.zoomed)
        self.assertEqual(c.state.selected,1)
        c.options_intent('options_close')
        self.assertFalse(c.state.zoomed)
    def test_maximize_toggle_retains_mode_and_placement(self):
        c=self.control(); c.state.maximized_mode='locked'
        before=list(c.state.placements)
        c.maximize_monitor('XR-3')
        self.assertTrue(c.state.zoomed)
        self.assertEqual(c.state.selected,1)
        self.assertEqual(c.state.mode,'locked')
        c.maximize_monitor('XR-3')
        self.assertFalse(c.state.zoomed)
        self.assertEqual(c.state.placements,before)
    def test_maximize_rect_has_required_gap(self):
        from tools.native_spike import free_pointer as fp
        for width in (100,600,2400):
            tile=(30,50,width,500)
            x,y,w,h=fp.maximize_rect(tile); bx,by,_,_=fp.level_bar_rect(tile)
            scale=fp.level_bar_scale(tile)
            self.assertAlmostEqual(bx-(x+w),12*scale)
            self.assertTrue(fp.maximize_hit(tile,(x+w/2,y+h/2)))

class KeyboardTests(unittest.TestCase):
    def test_range_steps_actual_units(self):
        c=WiringTests().control()
        c.options._rows=lambda *a: [dict(id='roll',label='Roll',scope='monitor',kind='range',value='-10',minimum=-90,maximum=90,step=5,unit='deg',enabled=True)]
        values=[]
        c.options._apply=lambda control,id,value,*a: values.append(value)
        c.options.open('XR-1'); c.options.intent('options_focus',focus=0)
        c.options_key('Right')
        self.assertEqual(values,[ -5 ])

class OcclusionTests(unittest.TestCase):
    def test_gear_over_another_desktop_takes_the_click(self):
        # The gear is glass like the placement pills: drawn over every desktop picture, so it is hit over them too.
        from tools.native_spike import view, free_pointer as fp, options_geometry as geo
        c=view.ViewControl(view.ViewState(['XR-1','XR-2']),'run','session',None,None,None)
        p=fp.OverviewPointer(c)
        g=dict(w=1920,h=1080,half=(.36,.225),tiles=[(500,200,300,200),(400,395,600,350)])
        p.geometry=g
        r=geo.gear_rect(fp.pill_rect(g['tiles'][0]))
        p.pointer=fp.project(g,g['tiles'][0],c.state.placements[0],r[0]+r[2]/2,r[1]+r[3]/2)
        self.assertEqual(p._options_hit(),('options_gear',0))

    def test_the_gear_under_the_dot_lights_like_a_hovered_pill(self):
        from tools.native_spike import view, free_pointer as fp
        c=view.ViewControl(view.ViewState(['XR-1','XR-2']),'run','session',None,None,None)
        p=fp.OverviewPointer(c)
        sent=[]; p._send=lambda frame,want: sent.append(dict(want)) or b'x'; p._handles_on=lambda: True
        p.mode,p.drag,p.hover='free',None,('options_gear',1)
        p._pills(False)
        self.assertEqual(sent[-1],{'visible':True,'desktop':1,'hover':False,'active':False,'gear':True})
        p.hover=None; p._pills(False)
        self.assertFalse(sent[-1]['gear'])
