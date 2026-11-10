import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from unittest import mock
from tools.native_spike import immersive, sbs
from tools.native_spike.profiles import DEFAULT_PROFILE
from tools.native_spike.view import DEFAULT_SETTINGS
from tools.native_spike.geometry import DEFAULT_DESKTOP
from tools.native_spike.panel_consent import load_panel_consent, build_consent
from tools.xr_controller.state import Controller
from tools.native_spike.hyprland import OWNERSHIP_PLUGIN_NAME
HYPRBARS = 'hyprbars'  # Omarchy titlebars workspace mode

class Store:
    def __init__(self): self.profile=copy.deepcopy(DEFAULT_PROFILE)
    def load(self,model): return copy.deepcopy(self.profile)
    def save(self,model,p): self.profile=copy.deepcopy(p)

class Units:
    active=False
    def alive(self,r): return self.active
    def control(self,r,s,op): return True

class StateTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name); self.fd=os.open(self.path,os.O_DIRECTORY); self.addCleanup(os.close,self.fd)
        self.observation=dict(model='beast',output='DP-2',reason=None,instance='instance',monitors=[dict(name='DP-2'),dict(name='DP-1')],journals=[] ,installed={k:dict(path='/p/'+k,sha256='a'*64) for k in ('plugin','renderer')},plugins=[])
        self.now=0; self.calls=[]; self.units=Units()
        self.c=Controller(lambda:self.now,lambda:copy.deepcopy(self.observation),lambda *a:self.calls.append(a),self.units,Store(),self.fd,self.path)
    def choose_driver(self,available=True,data=b'driver',pin=None):
        # The hidden recovery setting on a Luma Ultra, asking for the 3DoF test build in a temp folder, offered by the probe as
        # Host.probe offers it. Nothing is executed.
        binary=self.path/'xrDriver'
        if available: binary.write_bytes(data)
        self.observation.update(model='luma-ultra',tracking_override='3dof',tracking_drivers=[dict(key='xrdriver',label='XRLinuxDriver',path='/usr/bin/xrDriver',sha256=None,available=True),
            dict(key='3dof',label='3DoF test',path=str(binary),sha256=pin or hashlib.sha256(b'driver').hexdigest(),available=available),
            dict(key='luma',label='XR Luma driver',path='',sha256=None,available=False)])
        self.c.tick()
        return str(binary)
    def begin(self):
        reply=self.c.request(dict(op='begin_start')); self.assertTrue(reply['ok'],reply); return reply
    def confirm(self):
        r=self.begin(); return self.c.request(dict(op='confirm_start',scope_id=r['scope_id']))
    def test_standard_driver_adds_nothing_to_the_scope(self):
        scope=self.begin()['scope']
        self.assertEqual(scope['tracking'],'xrlinuxdriver')
        self.assertNotIn('tracking_driver',scope)

    def test_3dof_choice_is_in_the_scope_and_the_written_consent(self):
        self.choose_driver()
        reply=self.begin()
        self.assertEqual((reply['scope']['tracking'],reply['scope']['tracking_driver']),('xrlinuxdriver','3dof'))
        self.assertTrue(self.c.request(dict(op='confirm_start',scope_id=reply['scope_id']))['ok'])
        self.assertEqual(json.loads((self.path/(reply['scope']['run_id']+'.json')).read_text())['tracking_driver'],'3dof')

    def test_unticking_head_tracking_drops_the_driver_from_the_consent(self):
        self.choose_driver()
        reply=self.begin()
        self.assertTrue(self.c.request(dict(op='confirm_start',scope_id=reply['scope_id'],optional=dict(tracking=False)))['ok'])
        consent=json.loads((self.path/(reply['scope']['run_id']+'.json')).read_text())
        self.assertIs(consent['tracking'],False); self.assertNotIn('tracking_driver',consent)

    def test_head_tracking_off_never_checks_the_driver(self):
        self.choose_driver(available=False)
        self.assertTrue(self.c.request(dict(op='set_settings',settings=dict(tracking=False)))['ok'])
        self.assertIs(self.begin()['scope']['tracking'],False)

    def test_missing_or_changed_build_refuses_the_start_rather_than_run_the_standard_driver(self):
        for label,kwargs,why in (('missing',dict(available=False),'not available'),('changed',dict(data=b'other'),'pinned binary changed')):
            with self.subTest(label):
                self.choose_driver(**kwargs)
                reply=self.c.request(dict(op='begin_start'))
                self.assertFalse(reply['ok']); self.assertIn(why,reply['error']); self.assertIsNone(reply['pending'])
                self.assertEqual(self.c.state,'idle')

    def test_a_probe_that_does_not_offer_the_3dof_build_refuses_it(self):
        self.choose_driver()
        del self.observation['tracking_drivers'][1]
        self.c.tick()
        reply=self.c.request(dict(op='begin_start'))
        self.assertFalse(reply['ok']); self.assertIn('not available',reply['error'])

    def test_a_path_cannot_be_saved_as_the_driver(self):
        for bad in ('/usr/bin/sh','/tmp/evil/xrDriver','standard'):
            self.assertFalse(self.c.request(dict(op='set_settings',settings=dict(tracking_driver=bad)))['ok'])

    def in_use(self,**observed):
        self.observation.update(observed); self.c.tick()
        return self.c.snapshot()['tracking_in_use']

    def test_the_panel_gets_a_fact_and_no_list_of_choices(self):
        self.assertNotIn('tracking_drivers',self.c.snapshot()); self.assertNotIn('luma_available',self.c.snapshot())
        self.assertIsNone(self.in_use(model=None))

    def test_each_model_shows_its_driver(self):
        pin=dict(path='/p/luma',sha256='b'*64)
        luma=self.in_use(model='luma-ultra',installed=dict(self.observation['installed'],luma_driver=pin))
        self.assertEqual((luma['key'],luma['label'],luma['available'],luma['note']),('luma','XR Luma driver',True,None))
        for model in ('beast','unknown'):
            with self.subTest(model):
                other=self.in_use(model=model)
                self.assertEqual((other['key'],other['label'],other['available']),('xrdriver','XRLinuxDriver',True))
                self.assertIn('Automatic',other['note'])
        pro2=self.in_use(model='pro2',pro2_route=dict(bundle='/b'))
        self.assertEqual((pro2['key'],pro2['label'],pro2['available']),('pro2-sdk','Pro 2 SDK bridge',True))
        self.assertFalse(self.in_use(model='pro2',pro2_route=None)['available'])

    def test_a_luma_without_our_driver_says_so(self):
        got=self.in_use(model='luma-ultra')
        self.assertEqual((got['key'],got['available']),('luma',False)); self.assertIn('Not installed',got['note'])

    def test_the_recovery_setting_shows_on_a_luma_and_nowhere_else(self):
        for key,label in (('xrdriver','XRLinuxDriver'),('3dof','XRLinuxDriver 3DoF test')):
            got=self.in_use(model='luma-ultra',tracking_override=key)
            self.assertEqual((got['key'],got['label']),(key,label)); self.assertIn('Recovery setting',got['note'])
        self.assertIn('Automatic',self.in_use(model='beast',tracking_override='xrdriver')['note'])
        self.assertEqual(self.in_use(model='pro2',pro2_route=dict(bundle='/b'),tracking_override='xrdriver')['key'],'pro2-sdk')

    def test_a_running_session_shows_what_its_consent_says(self):
        self.with_luma()
        r=self.begin(); self.assertTrue(self.c.request(dict(op='confirm_start',scope_id=r['scope_id']))['ok'])
        self.c.state='running'
        self.c.observed['tracking_override']='xrdriver'  # a setting changed mid-session is the next start's
        self.assertEqual(self.c.snapshot()['tracking_in_use']['label'],'XR Luma driver')

    def test_a_stale_panel_cannot_save_another_driver(self):
        self.with_luma()
        for bad in ('xrdriver','3dof'):
            reply=self.c.request(dict(op='set_settings',settings=dict(tracking_driver=bad)))
            self.assertFalse(reply['ok']); self.assertIn('not a choice',reply['error'])

    def test_the_build_going_away_drops_the_open_scope(self):
        self.choose_driver(); self.begin()
        self.observation['tracking_drivers'][1]['available']=False
        self.c.tick()
        self.assertIsNone(self.c.snapshot()['pending'])

    def test_the_driver_cannot_change_during_a_run(self):
        self.assertIn('tracking_driver',DEFAULT_PROFILE)
        self.assertNotIn('tracking_driver',DEFAULT_SETTINGS)  # only view settings pass set_settings while running
    def test_snapshot_exposes_fallback_choices_and_pending_scope(self):
        self.observation['monitors'] += [dict(name='DP-3'), dict(name='XR-1'), dict(name='DP-4',disabled=True)]
        self.c.tick()
        snap=self.c.snapshot()
        self.assertEqual(snap['fallback'], 'choose')
        self.assertEqual(snap['fallback_candidates'], ['DP-1','DP-3'])
        self.assertIsNone(snap['pending'])
        self.assertTrue(self.c.request(dict(op='set_settings',settings={'fallback':'DP-3'}))['ok'])
        reply=self.begin()
        snap=self.c.snapshot()
        self.assertEqual(snap['fallback'],'DP-3')
        self.assertEqual(snap['pending']['scope_id'],reply['scope_id'])
        snap['pending']['scope']['desktops'].clear()
        self.assertEqual(len(self.c.snapshot()['pending']['scope']['desktops']),3)
        self.c.request(dict(op='cancel'))
        self.assertIsNone(self.c.snapshot()['pending'])

    def with_luma(self):
        self.observation['model']='luma-ultra'
        self.observation['installed']['luma_driver']=dict(path='/p/luma',sha256='b'*64); self.c.tick()

    def test_models_without_a_driver_of_ours_run_xrlinuxdriver_with_nothing_to_choose(self):
        self.observation['installed']['luma_driver']=dict(path='/p/luma',sha256='b'*64)
        for model in ('beast','unknown'):
            with self.subTest(model):
                self.observation['model']=model; self.c.tick()
                self.c.store.profile['tracking_driver']='luma'  # a saved choice from before: tonight's Pro 2 on master was exactly this
                scope=self.begin()['scope']
                self.assertEqual(scope['tracking'],'xrlinuxdriver'); self.assertNotIn('luma_driver',scope)
                self.c.request(dict(op='cancel'))

    def test_a_saved_standard_on_a_luma_runs_our_driver(self):
        self.with_luma()
        for saved in ('xrdriver','3dof'):
            with self.subTest(saved):
                self.c.store.profile['tracking_driver']=saved
                self.assertEqual(self.begin()['scope']['tracking'],'luma')
                self.c.request(dict(op='cancel'))

    def test_the_recovery_setting_runs_xrlinuxdriver_on_a_luma(self):
        self.with_luma(); self.observation['tracking_override']='xrdriver'; self.c.tick()
        scope=self.begin()['scope']
        self.assertEqual(scope['tracking'],'xrlinuxdriver'); self.assertNotIn('luma_driver',scope); self.assertNotIn('tracking_driver',scope)

    def test_the_recovery_setting_changing_drops_the_open_scope(self):
        self.with_luma(); self.begin()
        self.observation['tracking_override']='xrdriver'; self.c.tick()
        self.assertIsNone(self.c.snapshot()['pending'])

    def test_luma_keeps_its_mode_switch_and_3d_scope(self):
        self.with_luma()
        with mock.patch('tools.xr_controller.state.immersive.find_engine',return_value='/engine'): scope=self.begin()['scope']
        self.assertEqual(scope['mode_switch'],dict(width=1920,height=1200,refresh_hz=120)); self.assertIn('sbs_mode',scope)

    def test_the_wait_the_panel_quotes_is_this_drivers(self):
        for scope,seconds in (('luma',30),('pro2-sdk',10),('xrlinuxdriver',120),(False,120)):
            with self.subTest(scope), mock.patch.object(self.c,'_consent',return_value=dict(tracking=scope)):
                self.assertEqual(self.c._tracking_wait(dict(run_id='r')),seconds)
        with mock.patch.object(self.c,'_consent',side_effect=ValueError('gone')): self.assertEqual(self.c._tracking_wait(dict(run_id='r')),120)

    def test_luma_goes_into_the_consent_with_its_pin(self):
        self.with_luma()
        r=self.begin(); self.assertEqual(r['scope']['tracking'],'luma')
        self.assertTrue(self.c.request(dict(op='confirm_start',scope_id=r['scope_id']))['ok'])
        consent=load_panel_consent(self.path/(r['scope']['run_id']+'.json'),'instance','DP-2')
        self.assertEqual((consent['tracking'],consent['luma_driver']),('luma',dict(path='/p/luma',sha256='b'*64)))

    def test_unticking_tracking_drops_the_luma_pin(self):
        self.with_luma()
        r=self.begin(); self.assertTrue(self.c.request(dict(op='confirm_start',scope_id=r['scope_id'],optional={'tracking':False}))['ok'])
        consent=load_panel_consent(self.path/(r['scope']['run_id']+'.json'),'instance','DP-2')
        self.assertIs(consent['tracking'],False); self.assertNotIn('luma_driver',consent)

    def test_luma_never_carries_the_3dof_key_and_3dof_never_carries_the_luma_pin(self):
        self.with_luma()
        self.assertNotIn('tracking_driver',self.begin()['scope'])
        self.c.request(dict(op='cancel'))
        self.choose_driver()
        scope=self.begin()['scope']
        self.assertEqual((scope['tracking'],scope['tracking_driver']),('xrlinuxdriver','3dof'))
        self.assertNotIn('luma_driver',scope)

    def test_luma_not_installed_refuses_the_start_and_never_runs_xrlinuxdriver(self):
        self.observation['model']='luma-ultra'; self.c.tick()
        reply=self.c.request(dict(op='begin_start'))
        self.assertFalse(reply['ok']); self.assertIn('not installed',reply['error']); self.assertIsNone(reply['pending'])
        self.assertEqual(self.c.state,'idle')

    def test_tracking_off_starts_with_or_without_our_driver(self):
        self.observation['model']='luma-ultra'; self.c.tick()
        self.assertTrue(self.c.request(dict(op='set_settings',settings={'tracking':False}))['ok'])
        scope=self.begin()['scope']
        self.assertIs(scope['tracking'],False); self.assertNotIn('luma_driver',scope)

    def test_driver_choice_is_locked_while_running_and_validated(self):
        self.assertFalse(self.c.request(dict(op='set_settings',settings={'tracking_driver':'other'}))['ok'])
        self.c.state='running'; self.c.run=dict(run_id='r',session='s')
        reply=self.c.request(dict(op='set_settings',settings={'tracking_driver':'luma'}))
        self.assertFalse(reply['ok']); self.assertIn('locked',reply['error'])

    def test_start_scope_restores_the_saved_desktops(self):
        # remember-layout-reset-bind: the last session's count, resolutions and scales come back (profiles.load_setup).
        wide={'width':2560,'height':1080,'scale':1.0}; sharp={'width':2560,'height':1440,'scale':1.25}
        self.c.store.profile.update(desktops=[wide,sharp,wide,DEFAULT_DESKTOP],placements=[dict(x=10,y=0,distance=150,curvature=30)]*4,
                                    rolls=[0,250,-90,0])
        with contextlib.redirect_stderr(io.StringIO()) as log: scope=self.begin()['scope']
        self.assertEqual(scope['desktops'],[wide,sharp,wide,DEFAULT_DESKTOP])
        self.assertIn('restoring the saved monitor setup (4 desktops: 2560x1080@1, 2560x1440@1.25',log.getvalue())
        self.c.request(dict(op='cancel'))
        self.c.store.profile.update(desktops=[wide],placements=[dict(x=0,y=0,distance=100,curvature=0)],rolls=[0])
        with contextlib.redirect_stderr(io.StringIO()): self.assertEqual(self.begin()['scope']['desktops'],[wide])  # one is a setup too

    def test_start_scope_falls_back_to_three_flat_desktops_with_the_reason_logged(self):
        from tools.native_spike import profiles
        reasons=iter(['profiles.json is corrupt (not JSON)',profiles.NOTHING_SAVED])
        self.c.store.setup=lambda model:(profiles.default_setup(),next(reasons))  # as the real store's strict read reports
        with contextlib.redirect_stderr(io.StringIO()) as log: scope=self.begin()['scope']
        self.assertEqual(scope['desktops'],[DEFAULT_DESKTOP]*3)
        self.assertIn('saved monitor setup not used: profiles.json is corrupt (not JSON); three flat desktops instead',log.getvalue())
        self.c.request(dict(op='cancel'))
        with contextlib.redirect_stderr(io.StringIO()) as log: self.assertEqual(self.begin()['scope']['desktops'],[DEFAULT_DESKTOP]*3)
        self.assertIn('no saved monitor setup yet; three flat desktops',log.getvalue())

    def test_the_real_store_reads_the_saved_setup_strictly(self):
        from tools.xr_controller.__main__ import Store as RealStore
        from tools.native_spike import profiles
        with tempfile.TemporaryDirectory() as config, mock.patch.dict(os.environ,{'XDG_CONFIG_HOME':config}):
            path=profiles.profiles_path(); store=RealStore()
            self.assertEqual(store.setup('beast'),(profiles.default_setup(),profiles.NOTHING_SAVED))
            wide={'width':2560,'height':1080,'scale':1.0}
            profiles.PlacementStore(path,'beast').save_desktops([wide]*2,[dict(x=5,y=0,distance=90,curvature=0)]*2,[100,-100])
            self.assertEqual(store.setup('beast'),({'desktops':[wide]*2,'placements':[dict(x=5,y=0,distance=90,curvature=0)]*2,'rolls':[100,-100]},None))
            path.write_text('{"schema": 2, "profiles": {"beast": {"desktops": [{"width": 1921, "height": 1080, "scale": 1.0}]}}}')
            setup,reason=store.setup('beast')
            self.assertEqual(setup,profiles.default_setup()); self.assertIn('saved desktops are invalid',reason)

    def test_tracking_start_failures_reach_the_panel_word_for_word(self):
        import json
        from tools.native_spike.tracking import CRASHED,USB_DENIED
        for message in (CRASHED,USB_DENIED):
            with self.subTest(message):
                self.confirm(); run_id=self.c.run['run_id']
                result=self.path/(run_id+'.result.json')
                result.write_text(json.dumps(dict(run_id=run_id,phase='tracking',complete=False,renderer_returncode=None,remaining=None,error=message)))
                result.chmod(0o600)
                self.now+=10; self.c.tick()
                self.assertEqual(self.c.snapshot()['reason'],message)

    def test_runner_failure_stays_visible_until_next_start(self):
        import json
        self.confirm(); run_id=self.c.run['run_id']
        result=self.path/(run_id+'.result.json')
        result.write_text(json.dumps(dict(run_id=run_id,phase='mode',complete=False,
            renderer_returncode=None,remaining=None,error='session mode not observed')))
        result.chmod(0o600)
        self.now=10; self.c.tick()
        self.assertEqual(self.c.snapshot()['state'],'idle')
        self.assertIn('session mode not observed',self.c.snapshot()['reason'])
        self.c.tick()
        self.assertIn('session mode not observed',self.c.snapshot()['reason'])
        self.begin()
        self.assertIsNone(self.c.snapshot()['reason'])

    def test_no_start_without_confirm(self): self.begin(); self.assertEqual(self.calls,[])
    def test_confirm_writes_consent_matching_scope(self):
        r=self.begin(); self.c.request(dict(op='confirm_start',scope_id=r['scope_id']))
        self.assertEqual(load_panel_consent(self.path/(r['scope']['run_id']+'.json'),'instance','DP-2'),build_consent(r['scope']))
    def test_confirm_with_stale_scope_id_refused(self):
        self.begin(); self.assertFalse(self.c.request(dict(op='confirm_start',scope_id='stale'))['ok'])
    def test_unplug_cancels_confirmation(self):
        r=self.begin(); self.observation.update(model=None,output=None); self.c.tick()
        self.assertEqual(self.c.snapshot()['state'],'no-glasses'); self.assertFalse(self.c.request(dict(op='confirm_start',scope_id=r['scope_id']))['ok'])
    def test_double_start_is_single_run(self):
        self.assertTrue(self.confirm()['ok']); self.assertFalse(self.c.request(dict(op='begin_start'))['ok']); self.assertEqual(len(self.calls),1)
    def test_never_autostarts_on_plug(self): self.c.tick(); self.assertEqual(self.calls,[])
    def journal(self, consent=True):
        r=self.begin(); self.c.request(dict(op='confirm_start',scope_id=r['scope_id']))
        if not consent: (self.path/(r['scope']['run_id']+'.json')).unlink()
        self.now=10
        self.observation['journals']=[dict(run_id=r['scope']['run_id'],session='session',path='/runtime/journal.json',operations=[dict(state='pending')])]
    def test_snapshot_includes_only_reported_status(self):
        self.journal(); self.units.active=True
        calls=[]
        def control(r,s,op):
            if op=='status':
                calls.append(op)
                return dict(capabilities=['prediction'],view={'selected':1},status={'missed':2})
            return True
        self.units.control=control
        self.c.tick()
        self.assertEqual(self.c.snapshot()['status'], {'missed':2})
        self.assertEqual(self.c.snapshot()['view'], {'selected':1})
        self.c.tick(); self.assertEqual(len(calls),1)
        self.now+=1; self.c.tick(); self.assertEqual(len(calls),2)
        self.units.control=lambda *args: True
        self.now+=1; self.c.tick()
        self.assertEqual(self.c.snapshot()['status'], {})  # unavailable reply clears old measurements
        self.units.active=False; self.c.tick()
        self.assertEqual(self.c.snapshot()['status'], {})

    def test_rediscovers_live_run(self):
        self.journal(); self.units.active=True; self.c.tick(); self.assertEqual(self.c.snapshot()['state'],'running')
    def test_journal_without_unit_is_recovery_needed(self):
        self.journal(); self.c.tick(); self.assertEqual(self.c.snapshot()['state'],'recovery-needed')
    def test_cli_journal_is_cli_recovery(self):
        self.journal(False); self.c.tick(); self.assertEqual(self.c.snapshot()['state'],'cli-recovery'); self.assertFalse(self.c.request(dict(op='begin_recover'))['ok'])
    def test_recover_requires_confirm(self):
        self.journal(); self.c.tick(); n=len(self.calls); r=self.c.request(dict(op='begin_recover')); self.assertEqual(len(self.calls),n)
        self.assertTrue(self.c.request(dict(op='confirm_recover',scope_id=r['scope_id']))['ok']); self.assertEqual(len(self.calls),n+1)
    def test_resolution_locked_while_running(self):
        self.journal(); self.units.active=True; self.c.tick(); self.assertFalse(self.c.request(dict(op='set_settings',settings={'desktops':[]}))['ok'])
    def test_changed_fallback_invalidates_scope(self):
        r=self.begin(); self.observation['monitors'].pop(); self.c.tick()
        self.assertFalse(self.c.request(dict(op='confirm_start',scope_id=r['scope_id']))['ok']); self.assertEqual(self.calls,[])
    def test_optional_can_only_reduce_scope(self):
        r=self.begin(); reply=self.c.request(dict(op='confirm_start',scope_id=r['scope_id'],optional={'tracking':False}))
        self.assertTrue(reply['ok']); self.assertIs(load_panel_consent(self.path/(r['scope']['run_id']+'.json'),'instance','DP-2')['tracking'],False)
    def test_spawn_failure_consumes_confirmation(self):
        def fail(*args): raise OSError('spawn failed')
        self.c.spawn=fail; r=self.begin()
        self.assertFalse(self.c.request(dict(op='confirm_start',scope_id=r['scope_id']))['ok'])
        self.assertFalse(self.c.request(dict(op='confirm_start',scope_id=r['scope_id']))['ok'])
    def test_recovery_confirmation_not_invalidated_by_tick(self):
        self.journal(); self.c.tick(); r=self.c.request(dict(op='begin_recover')); self.c.tick()
        self.assertTrue(self.c.request(dict(op='confirm_recover',scope_id=r['scope_id']))['ok'])
    def test_running_view_settings_forwarded(self):
        self.journal(); self.units.active=True; self.c.tick(); operations=[]
        self.units.control=lambda r,s,op: operations.append(op) or True
        self.c.live['view']=dict(zoomed=True,mode='follow')  # a maximized desktop, following (the last status)
        reply=self.c.request(dict(op='set_settings',settings={'mode':'locked','spacing':60,'prediction':False}))
        self.assertTrue(reply['ok'],reply); self.assertEqual(operations,['mode_next','spacing_up','prediction'])
    def test_running_stabilization_forwarded_and_invalid_refused(self):
        self.journal(); self.units.active=True; self.c.tick(); sent=[]
        self.units.control=lambda r,s,op,payload=None: sent.append((op,payload)) or True
        for strength in (0,25,47,60,100):
            reply=self.c.request(dict(op='set_settings',settings={'stabilization':strength}))
            self.assertTrue(reply['ok'],reply)
            self.assertEqual(sent[-1],('stabilization',{'strength':strength}))
        before=len(sent)
        for strength in (-1,101,True,1.5):
            self.assertFalse(self.c.request(dict(op='set_settings',settings={'stabilization':strength}))['ok'])
        self.assertEqual(len(sent),before)

    def test_running_environment_forwarded(self):
        # The background goes to a run as one absolute message, like placements (environment.py); the supervisor saves what
        # it applied, so the controller writes nothing itself.
        self.journal(); self.units.active=True; self.c.tick(); sent=[]
        self.units.control=lambda r,s,op,payload=None: sent.append((op,payload)) or True
        grid=dict(background='grid',last='grid',brightness=60)
        reply=self.c.request(dict(op='set_settings',settings={'environment':grid}))
        self.assertTrue(reply['ok'],reply); self.assertEqual(sent,[('environment',grid)])
        sent.clear()
        reply=self.c.request(dict(op='set_settings',settings={'environment':dict(background='none',last='grid',brightness=100)}))
        self.assertTrue(reply['ok'],reply); self.assertEqual(sent,[])  # what it already holds: nothing to send
        self.assertFalse(self.c.request(dict(op='set_settings',settings={'environment':dict(grid,background='red')}))['ok'])
    def test_running_immersive_forwarded(self):
        # 3D goes to a run as one absolute message, and the supervisor saves what it applied (immersive.py).
        self.journal(); self.units.active=True; self.c.tick(); sent=[]
        self.units.control=lambda r,s,op,payload=None: sent.append((op,payload)) or True
        one={**immersive.DEFAULTS,'desktops':{'XR-2':True}}
        reply=self.c.request(dict(op='set_settings',settings={'immersive':one}))
        self.assertTrue(reply['ok'],reply); self.assertEqual(sent,[('immersive',one)])
        off={**immersive.DEFAULTS}
        sent.clear(); self.assertTrue(self.c.request(dict(op='set_settings',settings={'immersive':off}))['ok'])
        self.assertEqual(sent,[])  # the same as saved: nothing to send
        self.c.live['immersive']=dict(failed=True)
        self.assertTrue(self.c.request(dict(op='set_settings',settings={'immersive':off}))['ok'])
        self.assertEqual(sent,[('immersive',off)])  # after a fall back to 2D the same press is a new try
        self.assertFalse(self.c.request(dict(op='set_settings',settings={'immersive':dict(off,desktops={'DP-1':True})}))['ok'])
    def test_immersive_in_the_snapshot_only_while_running(self):
        self.assertIsNone(self.c.snapshot()['immersive'])
        self.journal(); self.units.active=True; self.c.tick()
        self.c.live['immersive']=dict(names=['XR-1'],stereo=True,failed=False)
        self.assertEqual(self.c.snapshot()['immersive']['names'],['XR-1'])
    def scoped(self,engine=True):
        """Luma Ultra glasses with the run's 120 Hz option on, the depth engine beside the renderer or not."""
        self.observation['model']='luma-ultra'; self.observation['installed']['luma_driver']=dict(path='/p/luma',sha256='b'*64)
        profile=self.c.store.load('luma-ultra'); profile['mode_switch']=True; self.c.store.save('luma-ultra',profile)
        patcher=mock.patch.object(immersive,'find_engine',lambda renderer:'/pinned/xr-depth-engine' if engine else None)
        patcher.start(); self.addCleanup(patcher.stop)
    def test_the_3d_scope_is_offered_only_where_the_engine_is(self):
        self.scoped(engine=False)
        self.assertNotIn('sbs_mode',self.begin()['scope'])
    def test_the_3d_scope_is_listed_and_reaches_the_consent(self):
        self.scoped()
        reply=self.begin()
        self.assertEqual(reply['scope']['sbs_mode'],sbs.SCOPE)
        self.assertTrue(self.c.request(dict(op='confirm_start',scope_id=reply['scope_id']))['ok'])
        self.assertEqual(json.loads((self.path/(reply['scope']['run_id']+'.json')).read_text())['sbs_mode'],sbs.SCOPE)
    def test_unticking_120_hz_takes_the_3d_scope_with_it(self):
        # It rests on the mode switch: left in, the consent would be refused and Start with it.
        self.scoped()
        reply=self.begin()
        self.assertIn('sbs_mode',reply['scope'])
        done=self.c.request(dict(op='confirm_start',scope_id=reply['scope_id'],optional=dict(mode_switch=False)))
        self.assertTrue(done['ok'],done)
        self.assertNotIn('sbs_mode',json.loads((self.path/(reply['scope']['run_id']+'.json')).read_text()))
    def test_immersive_saved_while_idle_and_off_by_default(self):
        off={**immersive.DEFAULTS}
        self.assertEqual(self.c.request(dict(op='get_settings'))['settings']['immersive'],off)
        on=dict(zip(('global','desktops'),(True,{})))  # a panel older than the look controls
        self.assertTrue(self.c.request(dict(op='set_settings',settings={'immersive':on}))['ok'])
        self.assertEqual(self.c.request(dict(op='get_settings'))['settings']['immersive'],{**off,**on})
        look={**off,**on,'preset':'video','strength':150,'convergence':70}
        self.assertTrue(self.c.request(dict(op='set_settings',settings={'immersive':look}))['ok'])
        self.assertEqual(self.c.request(dict(op='get_settings'))['settings']['immersive'],look)
        self.assertFalse(self.c.request(dict(op='set_settings',settings={'immersive':{**look,'strength':999}}))['ok'])
    def test_environment_saved_while_idle(self):
        grid=dict(background='grid',last='grid',brightness=60)
        self.assertTrue(self.c.request(dict(op='set_settings',settings={'environment':grid}))['ok'])
        self.assertEqual(self.c.request(dict(op='get_settings'))['settings']['environment'],grid)
    def test_running_mode_follows_the_live_view(self):
        # Maximize-follow: the overview is always anchor and a maximized desktop follow or locked, so a panel mode button
        # is one mode_next from the live view (never counted from the saved mode, which no longer changes), or refused.
        self.journal(); self.units.active=True; self.c.tick(); operations=[]
        self.units.control=lambda r,s,op: operations.append(op) or True
        def mode(want): return self.c.request(dict(op='set_settings',settings={'mode':want}))
        self.c.live['view']=dict(zoomed=False,mode='anchor')
        self.assertTrue(mode('anchor')['ok']); self.assertEqual(operations,[])  # already there
        for want in ('follow','locked'):
            reply=mode(want); self.assertFalse(reply['ok']); self.assertIn('maximize a desktop',reply['error'])
        self.c.live['view']=dict(zoomed=True,mode='locked')
        self.assertTrue(mode('locked')['ok']); self.assertEqual(operations,[])
        self.assertTrue(mode('follow')['ok']); self.assertEqual(operations,['mode_next'])
        reply=mode('anchor'); self.assertFalse(reply['ok']); self.assertIn('Overview',reply['error'])
        self.assertEqual(operations,['mode_next'])
        self.assertEqual(self.c.store.profile['mode'],DEFAULT_PROFILE['mode'])  # nothing saved by the controller
    def test_idle_placements_saved_to_model_profile(self):
        tilted=dict(x=10,y=0,distance=150,curvature=60)
        reply=self.c.request(dict(op='set_settings',settings={'placements':[tilted]*3}))
        self.assertTrue(reply['ok'],reply); self.assertEqual(self.c.store.profile['placements'],[tilted]*3)
        self.assertFalse(self.c.request(dict(op='set_settings',settings={'placements':[dict(tilted,curvature=500)]*3}))['ok'])
        self.assertEqual(self.c.store.profile['placements'],[tilted]*3)
    def test_running_placements_forwarded_absolute(self):
        self.journal(); self.units.active=True; self.c.tick(); sent=[]
        self.units.control=lambda r,s,op,payload=None: sent.append((op,payload)) or True
        tilted=dict(x=0,y=-10,distance=100,curvature=0)
        reply=self.c.request(dict(op='set_settings',settings={'placements':[tilted]*3}))
        self.assertTrue(reply['ok'],reply); self.assertTrue(reply['forwarded'])
        self.assertEqual(sent,[('placement',{'desktops':[tilted]*3})])
        self.assertEqual(self.c.store.profile['placements'],DEFAULT_PROFILE['placements'])  # the supervisor saves applied values
        for bad in ({'placements':[tilted]*3,'mode':'follow'},{'placements':[dict(tilted,curvature=121)]*3},{'placements':[tilted]}):
            with self.subTest(bad=bad): self.assertFalse(self.c.request(dict(op='set_settings',settings=bad))['ok'])
        self.assertEqual(len(sent),1)
        self.units.control=lambda r,s,op,payload=None: False
        self.assertFalse(self.c.request(dict(op='set_settings',settings={'placements':[tilted]*3}))['ok'])
    def test_running_snapshot_reports_live_placements(self):
        self.journal(); self.units.active=True
        live=[dict(x=1,y=2,distance=100,curvature=6)]*3
        self.units.control=lambda r,s,op,payload=None: dict(capabilities=['next'],view={},status={},placements=live) if op=='status' else True
        self.c.tick(); self.assertEqual(self.c.snapshot()['placements'],live)
        self.units.active=False; self.observation['journals']=[]; self.c.tick()
        self.assertIsNone(self.c.snapshot()['placements'])
    def test_live_unit_without_journal_blocks_second_start(self):
        self.confirm(); self.units.active=True
        self.observation['journals']=[dict(run_id=self.c.run['run_id'],session=None,path=None,operations=[])]
        self.c.tick()
        self.assertEqual(self.c.snapshot()['state'],'starting')
        self.assertFalse(self.c.request(dict(op='begin_start'))['ok'])
    def test_tracking_startup_reports_progress_and_can_be_cancelled(self):
        self.confirm(); self.units.active=True
        self.observation['journals']=[dict(run_id=self.c.run['run_id'],session=None,path=None,operations=[],phase='tracking')]
        self.c.tick()
        self.assertIn('calibrat', self.c.snapshot()['reason'])
        stopped=[]; self.units.stop=lambda run: stopped.append(run)
        self.assertTrue(self.c.request(dict(op='stop'))['ok'])
        self.c.tick()
        self.assertEqual(self.c.state, 'stopping')
        self.assertEqual(stopped, [self.c.run['run_id']])

    def test_start_progress_names_the_steps_and_the_one_under_way(self):
        self.assertEqual((self.c.snapshot()['start_steps'],self.c.snapshot()['start_phase']),(None,None))
        self.confirm(); self.units.active=True
        steps=['verify','tracking','plugin','desktops']
        for reported,shown in ((None,'verify'),('verify','tracking'),('tracking','tracking'),('plugin','plugin'),('desktops','desktops'),('mode',None)):
            with self.subTest(reported):
                self.observation['journals']=[dict(run_id=self.c.run['run_id'],session=None,path=None,operations=[],phase=reported)]
                self.c.tick(); snap=self.c.snapshot()
                self.assertEqual((snap['state'],snap['start_steps'],snap['start_phase']),('starting',steps,shown))

    def test_start_progress_skips_tracking_the_user_switched_off(self):
        r=self.begin(); self.c.request(dict(op='confirm_start',scope_id=r['scope_id'],optional=dict(tracking=False))); self.units.active=True
        self.observation['journals']=[dict(run_id=self.c.run['run_id'],session=None,path=None,operations=[],phase='verify')]
        self.c.tick(); snap=self.c.snapshot()
        self.assertEqual((snap['start_steps'],snap['start_phase']),(['verify','plugin','desktops'],'plugin'))
        self.units.active=False; self.observation['journals']=[]
        self.c.state='idle'; self.c.run=None
        self.assertIsNone(self.c.snapshot()['start_steps'])

    def test_lost_control_is_not_reported_as_fresh_startup(self):
        self.journal(); self.units.active=True; self.c.tick()
        self.units.control=lambda *args: False
        self.c.tick()
        self.assertEqual(self.c.state, 'failed')
        self.assertIn('Stop', self.c.snapshot()['reason'])
        self.units.stop=lambda run: None
        self.assertTrue(self.c.request(dict(op='stop'))['ok'])

    def test_probe_failure_is_visible_and_cancels_confirmation(self):
        self.begin()
        def fail(): raise OSError('probe unavailable')
        self.c.probe=fail; self.c.tick()
        self.assertEqual(self.c.snapshot()['state'],'failed'); self.assertIsNone(self.c.pending)
        self.assertEqual(self.c.snapshot()['reason'],'probe unavailable')

    # Unrelated plugins (Omarchy's hyprbars stays loaded all session) must not block; only 'xr-ownership' counts.
    def test_hyprbars_does_not_block_begin_start(self):
        self.observation['plugins'] = [HYPRBARS]; self.c.tick()
        self.assertTrue(self.confirm()['ok']); self.assertEqual(len(self.calls), 1)

    def test_our_broker_still_blocks_begin_start(self):
        self.observation['plugins'] = [HYPRBARS, OWNERSHIP_PLUGIN_NAME]; self.c.tick()
        reply = self.c.request(dict(op='begin_start'))
        self.assertFalse(reply['ok']); self.assertIn('ownership plugin already loaded', reply['error'])
