import copy
import os
from pathlib import Path
import tempfile
import unittest
from tools.native_spike.profiles import DEFAULT_PROFILE
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
    def begin(self):
        reply=self.c.request(dict(op='begin_start')); self.assertTrue(reply['ok'],reply); return reply
    def confirm(self):
        r=self.begin(); return self.c.request(dict(op='confirm_start',scope_id=r['scope_id']))
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

    def test_start_scope_is_always_three_desktops(self):
        # A saved count (a pill's Add) and saved placements are not restored at start (profiles.startup_desktops).
        wide={'width':2560,'height':1080,'scale':1.0}
        self.c.store.profile.update(desktops=[wide]*4,placements=[dict(x=10,y=0,distance=150,curvature=30)]*4)
        self.assertEqual(self.begin()['scope']['desktops'],[wide]*3)
        self.c.request(dict(op='cancel'))
        self.c.store.profile.update(desktops=[wide],placements=[dict(x=0,y=0,distance=100,curvature=0)])
        self.assertEqual(self.begin()['scope']['desktops'],[wide,DEFAULT_DESKTOP,DEFAULT_DESKTOP])

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
