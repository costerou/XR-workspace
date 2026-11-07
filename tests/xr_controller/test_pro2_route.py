"""The Pro 2's route from the panel's Start: off by default, explicit, its own settings, no way round to Luma's."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
from tools.native_spike import panel_consent as pc, profiles
from tools.native_spike.display_policy import PRO2_PROJECTIONS
from tools.native_spike.hyprland import OWNERSHIP_PLUGIN_NAME
from tools.pro2_diagnostic.candidate import SHA256
from tools.xr_controller import pro2_route as route
from tools.xr_controller.detect import identify
from tools.xr_controller.state import Controller

USB=[dict(vendor_id='35ca',product_id='1301',product='VITURE PRO 2 XR Glasses')]
DRM=[dict(name='card1-DP-2',status='connected')]
MONITORS=[dict(name='DP-2',model='VITURE',disabled=False),dict(name='DP-1',model='Odyssey G93SD',disabled=False,physical=True,dpmsStatus=True,mirrorOf='none')]


class Base(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup)
        self.dir=Path(t.name).resolve();self.config=self.dir/'config';self.config.mkdir(mode=0o700)
        self.bundle=self.make_bundle(self.dir/'bundle')
        self.evidence=self.dir/'result.json'
        self.write_evidence()
    def write_evidence(self,bundle=None):
        self.evidence.write_text(json.dumps(dict(run_id='pro2-supervised-1',manifest_sha256=route.manifest_sha(bundle or self.bundle),complete=True,errors=[],run=dict(complete=True,renderer_returncode=0))));self.evidence.chmod(0o600)
    def make_bundle(self,root):
        (root/'bin').mkdir(parents=True);files={}
        for name in ('pro2-sdk-bridge','xr-native-spike','libxr-ownership.so'):
            p=root/'bin'/name;p.write_bytes(b'inert '+name.encode());files['bin/'+name]=hashlib.sha256(p.read_bytes()).hexdigest()
        (root/'manifest.json').write_text(json.dumps(dict(schema=1,sdk_sha256=SHA256,files=files)))
        return root
    def enable(self):return route.enable(self.bundle,self.evidence,self.config)


class OffByDefault(Base):
    def test_no_opt_in_means_the_old_refusal(self):
        self.assertIsNone(route.load_route(self.config))
        r=identify(USB,DRM,MONITORS)
        self.assertEqual((r['model'],r['output']),('pro2',None));self.assertIn('unverified',r['reason'])
    def test_a_route_for_it_gives_the_exact_connector(self):
        r=identify(USB,DRM,MONITORS,('pro2',))
        self.assertEqual((r['model'],r['output'],r['reason']),('pro2','DP-2',None))
    def test_other_models_ignore_it(self):
        luma=[dict(vendor_id='35ca',product_id='1104',product='VITURE Luma Ultra XR GLASSES')]
        self.assertEqual(identify(luma,DRM,MONITORS,('pro2',)),identify(luma,DRM,MONITORS))
    def test_enable_needs_a_clean_completed_supervised_run(self):
        for change in (dict(manifest_sha256='0'*64),dict(complete=False),dict(errors=['x']),dict(run_id='r'),dict(run=dict(complete=False,renderer_returncode=0)),dict(run=dict(complete=True,renderer_returncode=1))):
            good=json.loads(self.evidence.read_text());self.evidence.write_text(json.dumps({**good,**change}))
            with self.subTest(change=change),self.assertRaises(ValueError):route.enable(self.bundle,self.evidence,self.config)
            self.evidence.write_text(json.dumps(good))
        self.assertIsNone(route.load_route(self.config))
    def test_a_changed_bundle_turns_it_off_until_enabled_again(self):
        self.enable();self.assertIsNotNone(route.load_route(self.config))
        (self.bundle/'manifest.json').write_text(json.dumps(dict(schema=1,sdk_sha256=SHA256,files={})))
        got,why=route.probe(self.config);self.assertIsNone(got);self.assertIn('changed',why)
    def test_a_broken_opt_in_never_raises_into_the_probe(self):
        (self.config/route.ROUTE_FILE).write_text('{');(self.config/route.ROUTE_FILE).chmod(0o600)
        got,why=route.probe(self.config);self.assertIsNone(got);self.assertTrue(why)
    def test_the_running_hyprland_must_be_the_bundles(self):
        from tools.pro2_runtime.scope import BundleClient
        client=BundleClient.__new__(BundleClient);client.bundle_manifest=dict(ownership_abi=['0.55','0.10'])
        client._request=Mock(return_value=json.dumps(dict(version='0.55',buildAquamarine='0.10')).encode())
        client.check_abi();client._request.assert_called_once_with('j/version')
        client._request=Mock(return_value=json.dumps(dict(version='0.56',buildAquamarine='0.10')).encode())
        with self.assertRaisesRegex(ValueError,'another Hyprland'):client.check_abi()
    def test_disable(self):
        self.enable();route.disable(self.config);self.assertIsNone(route.load_route(self.config))


class Projection(Base):
    def test_default_and_every_step(self):
        self.assertEqual(route.load_projection(self.config),44.5)
        self.assertEqual(PRO2_PROJECTIONS,(44.5,45.5,46.5,47.5,48.5,49.5,50.0))
        for p in PRO2_PROJECTIONS:
            route.save_projection(p,self.config);self.assertEqual(route.load_projection(self.config),p)
    def test_refused_values(self):
        for bad in (44,45,50.5,52,46,float('nan'),True,'44.5',None):
            with self.subTest(bad=bad),self.assertRaises(ValueError):route.save_projection(bad,self.config)
    def test_an_unreadable_file_is_this_models_default_not_another(self):
        (self.config/route.SETTINGS_FILE).write_text(json.dumps(dict(schema=1,projection=52)));(self.config/route.SETTINGS_FILE).chmod(0o600)
        self.assertEqual(route.load_projection(self.config),44.5)
    def test_the_file_is_private_and_profiles_are_untouched(self):
        path=self.dir/'profiles.json';profiles.save_profile(path,'luma-ultra',profiles.load_profile(path,'luma-ultra'))
        before=path.read_bytes();route.save_projection(46.5,self.config)
        self.assertEqual(path.read_bytes(),before)
        self.assertEqual(os.stat(self.config/route.SETTINGS_FILE).st_mode&0o777,0o600)


class Consent(Base):
    def scope(self,**over):
        r=self.enable()
        s=dict(run_id='r1',instance='i',output='DP-2',recovery_output='DP-1',model='pro2',tracking='pro2-sdk',mode_switch=False,
               desktops=[dict(width=1920,height=1080,scale=1)],plugin=r['plugin'],renderer=r['renderer'],pro2_route=route.consent_block(r,44.5))
        return {**s,**over}
    def test_the_route_block_is_what_makes_a_pro2_consent_valid(self):
        self.assertEqual(pc.build_consent(self.scope())['pro2_route']['projection'],44.5)
        s=self.scope();s.pop('pro2_route')
        with self.assertRaisesRegex(ValueError,'PRO 2.*unverified'):pc.build_consent(s)
    def test_nothing_of_luma_can_ride_along(self):
        block=self.scope()['pro2_route']
        for over in (dict(tracking='xrlinuxdriver'),dict(tracking='luma'),dict(tracking=False),dict(mode_switch=dict(pc.LUMA_MODE)),
                     dict(pro2_route={**block,'projection':52.0}),dict(pro2_route={**block,'projection':46.0}),dict(pro2_route={**block,'pose_rate_hz':1000}),
                     dict(plugin=dict(path='/elsewhere/libxr-ownership.so',sha256='a'*64)),dict(renderer=dict(path='/elsewhere/r',sha256='a'*64))):
            with self.subTest(over=over),self.assertRaises(ValueError):pc.build_consent(self.scope(**over))
    def test_the_key_is_refused_on_any_other_model(self):
        with self.assertRaisesRegex(ValueError,'Pro 2 only'):pc.build_consent(self.scope(model='luma-ultra'))
    def test_require_follows_the_opt_in_at_run_time(self):
        consent=pc.build_consent(self.scope())
        self.assertEqual(route.require(consent,self.config)[0],self.bundle)
        route.disable(self.config)
        with self.assertRaisesRegex(ValueError,'not enabled'):route.require(consent,self.config)
        other=self.make_bundle(self.dir/'other');self.write_evidence(other);route.enable(other,self.evidence,self.config)
        with self.assertRaisesRegex(ValueError,'changed since'):route.require(consent,self.config)


class Store:
    def __init__(self):self.profile=profiles.load_profile(Path('/nonexistent/profiles.json'),'pro2')
    def load(self,model):return self.profile
    def save(self,model,profile):self.profile=profile


class Panel(Base):
    def controller(self,model='pro2',enabled=True):
        info=self.enable() if enabled else None
        self.observed=dict(model=model,output='DP-2' if enabled or model!='pro2' else None,reason=None if enabled else 'unverified',instance='i',monitors=MONITORS,installed={},plugins=[],journals=[],tracking_drivers=[],**({'pro2_route':info} if info else {}))
        fd=os.open(self.dir,os.O_RDONLY);self.addCleanup(os.close,fd)
        spawn=self.spawn=Mock();units=Mock();units.alive.return_value=False
        c=Controller(lambda:0.0,lambda:self.observed,spawn,units,Store(),fd,self.dir)
        c.route_config=self.config
        return c
    def test_snapshot_has_a_row_only_for_a_pro2_with_the_route(self):
        self.assertEqual(self.controller().snapshot()['pro2'],dict(projection=44.5,choices=list(PRO2_PROJECTIONS),label='approximate'))
        self.assertNotIn('pro2',self.controller(enabled=False).snapshot())
        self.assertNotIn('pro2',self.controller(model='luma-ultra',enabled=False).snapshot())
    def test_blocked_without_opt_in_says_why(self):
        c=self.controller(enabled=False);r=c.request(dict(op='begin_start'))
        self.assertFalse(r['ok']);self.assertIn('unverified',r['error'])
    def test_start_uses_the_saved_projection_and_the_pinned_bundle(self):
        c=self.controller()
        self.assertTrue(c.request(dict(op='set_pro2_projection',projection=46.5))['ok'])
        r=c.request(dict(op='begin_start'));self.assertTrue(r['ok'],r)
        scope=r['scope']
        self.assertEqual(scope['pro2_route']['projection'],46.5);self.assertEqual(scope['pro2_route']['pose_rate_hz'],60)
        self.assertEqual(scope['tracking'],'pro2-sdk');self.assertIs(scope['mode_switch'],False)
        self.assertTrue(scope['renderer']['path'].startswith(str(self.bundle)))
        done=c.request(dict(op='confirm_start',scope_id=r['scope_id'],optional=dict(tracking=False)));self.assertTrue(done['ok'],done)
        written=json.loads((self.dir/(scope['run_id']+'.json')).read_text())
        self.assertEqual(written['tracking'],'pro2-sdk')   # the panel cannot turn it into a run without its route
        self.spawn.assert_called_once()
    def test_a_refused_projection_changes_nothing(self):
        c=self.controller();r=c.request(dict(op='set_pro2_projection',projection=52))
        self.assertFalse(r['ok']);self.assertEqual(c.snapshot()['pro2']['projection'],44.5)
    def test_projection_cannot_be_set_for_another_model(self):
        c=self.controller(model='luma-ultra',enabled=False)
        self.assertFalse(c.request(dict(op='set_pro2_projection',projection=46.5))['ok'])
        self.assertFalse((self.config/route.SETTINGS_FILE).exists())


class Run(Base):
    def consent_file(self):
        r=self.enable()
        c=pc.build_consent(dict(run_id='r1',instance='i',output='DP-2',recovery_output='DP-1',model='pro2',tracking='pro2-sdk',mode_switch=False,
            desktops=[dict(width=1920,height=1080,scale=1)],plugin=r['plugin'],renderer=r['renderer'],pro2_route=route.consent_block(r,47.5)))
        p=self.dir/'r1.json';p.write_text(json.dumps(c));p.chmod(0o600);return p
    def session(self):
        seen=self.seen={}
        class Session:
            def __init__(s,root,directory,projection,seconds,approved,pose_rate_hz=60,panel_route=False):
                seen.update(root=root,projection=projection,seconds=seconds,rate=pose_rate_hz,panel_route=panel_route)
                directory.mkdir();s.prefix=str(directory/'pose_');s.projection=projection;s.nonce='n';s.health_path=directory/'health.json';s.done=dict(samples=9,seconds=1.5,pose_rate_hz=60,gap_ms_counts=[0,0,0,6,3,0])
                s.health_path.write_text(json.dumps(dict(nonce='n',ready=True,monotonic=__import__('time').monotonic())));s.health_path.chmod(0o600)
            def start(s):return s
            def close(s):seen['closed']=True
        return Session
    def run_it(self,**result):
        client=Mock();client.plugins_loaded.side_effect=[[],[OWNERSHIP_PLUGIN_NAME],[OWNERSHIP_PLUGIN_NAME],[]]
        run=Mock(return_value=dict(complete=True,renderer_returncode=0,remaining=[],**result))
        path=self.consent_file()
        with patch.dict(os.environ,XDG_RUNTIME_DIR=str(self.dir)),patch('tools.xr_controller.pro2_test.monitor_rules.refuse_start'),patch.object(route,'profiles_path',return_value=self.dir/'profiles.json'):
            out=route.execute(path,self.dir,config_dir=self.config,client_factory=lambda *a:client,session_factory=self.session(),run=run)
        return out,run,client
    def test_a_session_runs_the_bundle_with_its_projection_and_a_session_deadline(self):
        out,run,client=self.run_it(stop_reason='interrupted')
        self.assertEqual(out['complete'],True);self.assertEqual(out['stop_reason'],'interrupted');self.assertEqual(out['provider']['samples'],9)
        self.assertEqual((self.seen['projection'],self.seen['rate'],self.seen['panel_route'],self.seen['seconds']),(47.5,60,True,43200))
        self.assertTrue(self.seen['closed'])
        kwargs=run.call_args.kwargs
        self.assertIs(kwargs['test_scope'].fixed_desktops,False);self.assertEqual(kwargs['store'].model,'pro2')
        self.assertEqual(kwargs['renderer'],str(self.bundle/'bin/xr-native-spike'))
        client.plugin_unload.assert_called_once_with(str(self.bundle/'bin/libxr-ownership.so'))
        client.apply_session_mode.assert_not_called()
    def test_a_disabled_route_stops_a_consented_start_before_anything_runs(self):
        path=self.consent_file();route.disable(self.config)
        client=Mock(side_effect=AssertionError('no client'))
        with patch.dict(os.environ,XDG_RUNTIME_DIR=str(self.dir)),self.assertRaisesRegex(ValueError,'not enabled'):
            route.execute(path,self.dir,config_dir=self.config,client_factory=client,session_factory=Mock(side_effect=AssertionError('no SDK')))
    def test_the_runner_takes_a_pro2_consent_to_the_route_and_writes_a_result(self):
        from tools.xr_controller import runner
        path=self.consent_file()
        with patch('tools.xr_controller.pro2_route.execute',return_value=dict(complete=True,renderer_returncode=0,remaining=[],error=None)) as go:
            result=runner.execute(path,self.dir)
        go.assert_called_once();self.assertEqual((result['phase'],result['run_id']),('done','r1'))
        with patch('tools.xr_controller.pro2_route.execute',side_effect=ValueError('no')):
            result=runner.execute(path,self.dir)
        self.assertEqual((result['error'],result['complete']),('no',False))
    def test_recovery_uses_the_bundle_even_after_disable(self):
        path=self.consent_file();route.disable(self.config)
        (self.bundle/'bin/xr-native-spike').write_bytes(b'changed since')    # recovery never starts it
        client=Mock();client.plugins_loaded.side_effect=[[OWNERSHIP_PLUGIN_NAME],[]]
        with patch.dict(os.environ,XDG_RUNTIME_DIR=str(self.dir)),patch('tools.native_spike.managed.recover_pro2_test_managed',return_value=dict(complete=True)) as rec:
            self.assertTrue(route.recover(path,self.dir/'journal.json',('XR-1',),client_factory=lambda *a:client)['complete'])
        self.assertEqual(rec.call_args.kwargs['clear_rules'],('XR-1',))
        client.plugin_unload.assert_called_once_with(str(self.bundle/'bin/libxr-ownership.so'))
