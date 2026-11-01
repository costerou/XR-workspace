import copy
import os
from pathlib import Path
import tempfile
import unittest
from tools.native_spike.profiles import DEFAULT_PROFILE
from tools.native_spike.panel_consent import load_panel_consent, build_consent
from tools.xr_controller.state import Controller

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
        reply=self.c.request(dict(op='set_settings',settings={'mode':'follow','spacing':60,'prediction':False}))
        self.assertTrue(reply['ok'],reply); self.assertEqual(operations,['mode_next','spacing_up','prediction'])
    def test_live_unit_without_journal_blocks_second_start(self):
        self.confirm(); self.units.active=True
        self.observation['journals']=[dict(run_id=self.c.run['run_id'],session=None,path=None,operations=[])]
        self.c.tick()
        self.assertEqual(self.c.snapshot()['state'],'starting')
        self.assertFalse(self.c.request(dict(op='begin_start'))['ok'])
    def test_probe_failure_is_visible_and_cancels_confirmation(self):
        self.begin()
        def fail(): raise OSError('probe unavailable')
        self.c.probe=fail; self.c.tick()
        self.assertEqual(self.c.snapshot()['state'],'failed'); self.assertIsNone(self.c.pending)
        self.assertEqual(self.c.snapshot()['reason'],'probe unavailable')
