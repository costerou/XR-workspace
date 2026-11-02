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
        reply=self.c.request(dict(op='set_settings',settings={'mode':'follow','spacing':60,'prediction':False}))
        self.assertTrue(reply['ok'],reply); self.assertEqual(operations,['mode_next','spacing_up','prediction'])
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


class DesktopCountTests(unittest.TestCase):
    """Configurable managed-desktop count: startup-only, bounded, consented exactly, per model."""
    setUp, begin, journal = StateTests.setUp, StateTests.begin, StateTests.journal
    def count(self, n): return self.c.request(dict(op='set_desktop_count', count=n))

    def test_count_changes_startup_layout_and_consent(self):
        for n in (1, 6, 2):
            with self.subTest(n=n):
                self.assertTrue(self.count(n)['ok'])
                self.assertEqual(len(self.c.store.profile['desktops']), n)
        self.assertTrue(self.count(5)['ok'])
        r = self.begin()
        self.assertEqual(len(r['scope']['desktops']), 5)
        self.c.request(dict(op='confirm_start', scope_id=r['scope_id']))
        consent = load_panel_consent(self.path/(r['scope']['run_id']+'.json'), 'instance', 'DP-2')
        self.assertEqual(len(consent['desktops']), 5)

    def test_growing_copies_last_desktop_and_shrinking_keeps_first(self):
        wide = {'width': 3840, 'height': 1080, 'scale': 1.0}
        self.c.store.profile['desktops'] = [{'width': 1920, 'height': 1080, 'scale': 1.0}, wide]
        self.assertTrue(self.count(4)['ok'])
        self.assertEqual(self.c.store.profile['desktops'][1:], [wide] * 3)
        self.assertTrue(self.count(1)['ok'])
        self.assertEqual(self.c.store.profile['desktops'], [{'width': 1920, 'height': 1080, 'scale': 1.0}])

    def test_out_of_range_or_malformed_count_refused_without_saving(self):
        before = copy.deepcopy(self.c.store.profile)
        for bad in (0, 7, -1, 3.0, '3', True, None):
            with self.subTest(bad=bad):
                reply = self.count(bad)
                self.assertFalse(reply['ok'])
                self.assertEqual(self.c.store.profile, before)
        self.assertFalse(self.c.request(dict(op='set_settings', settings={'desktops': []}))['ok'])
        self.assertFalse(self.c.request(dict(op='set_settings', settings={'desktops': [before['desktops'][0]] * 7}))['ok'])
        self.assertEqual(self.c.store.profile, before)

    def test_set_settings_accepts_any_valid_count(self):
        d = {'width': 2560, 'height': 1440, 'scale': 1.25}
        self.assertTrue(self.c.request(dict(op='set_settings', settings={'desktops': [d] * 6}))['ok'])
        self.assertEqual(self.c.store.profile['desktops'], [d] * 6)

    def test_snapshot_reports_limits(self):
        self.assertEqual(self.c.snapshot()['desktop_limits'], {'min': 1, 'max': 6})
        self.assertIsNone(self.c.snapshot()['desktops'])

    def test_count_locked_while_confirming_running_or_recovering(self):
        self.begin()
        self.assertFalse(self.count(4)['ok'])            # a pending scope cannot drift from what is shown
        self.c.request(dict(op='cancel'))
        self.journal(); self.c.tick()
        self.assertEqual(self.c.snapshot()['state'], 'recovery-needed')
        self.assertFalse(self.count(4)['ok'])
        self.units.active = True; self.c.tick()
        self.assertEqual(self.c.snapshot()['state'], 'running')
        self.assertFalse(self.count(4)['ok'])

    def test_running_snapshot_shows_consented_desktops_not_later_profile(self):
        self.assertTrue(self.count(4)['ok'])
        self.journal(); self.units.active = True; self.c.tick()
        self.c.store.profile['desktops'] = self.c.store.profile['desktops'][:1]  # e.g. edited by another tool
        snap = self.c.snapshot()
        self.assertEqual(snap['state'], 'running')
        self.assertEqual(len(snap['desktops']), 4)


class RealStoreCountTests(unittest.TestCase):
    """The controller's store is the per-glasses-model profiles file."""
    def test_count_saved_per_model(self):
        from unittest import mock
        from tools.xr_controller.__main__ import Store
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, XDG_CONFIG_HOME=tmp):
            fd = os.open(tmp, os.O_DIRECTORY); self.addCleanup(os.close, fd)
            obs = dict(model='beast', output='DP-2', reason=None, instance='instance', monitors=[dict(name='DP-2'), dict(name='DP-1')],
                       journals=[], installed={k: dict(path='/p/'+k, sha256='a'*64) for k in ('plugin', 'renderer')}, plugins=[])
            c = Controller(lambda: 0, lambda: copy.deepcopy(obs), lambda *a: None, Units(), Store(), fd, tmp)
            self.assertTrue(c.request(dict(op='set_desktop_count', count=6))['ok'])
            obs['model'] = 'luma-ultra'; c.tick()
            self.assertEqual(len(c.request(dict(op='get_settings'))['settings']['desktops']), 3)
            self.assertTrue(c.request(dict(op='set_desktop_count', count=1))['ok'])
            obs['model'] = 'beast'; c.tick()
            self.assertEqual(len(c.request(dict(op='get_settings'))['settings']['desktops']), 6)
            obs['model'] = 'luma-ultra'; c.tick()
            self.assertEqual(len(c.request(dict(op='get_settings'))['settings']['desktops']), 1)
