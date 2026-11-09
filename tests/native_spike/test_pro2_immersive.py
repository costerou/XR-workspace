"""Pro 2 SBS stays inside its validated production route and healthy display handovers."""
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from tests.xr_controller.test_pro2_route import Base
from tools.native_spike import immersive, managed, panel_consent
from tools.pro2_runtime.scope import TestScope
from tools.xr_controller import pro2_route

SCOPE = dict(width=3840, height=1080, refresh_hz=[60])


class ProductionScope(Base):
    def consent(self):
        route = self.enable()
        return panel_consent.build_consent(dict(run_id='r1',instance='i',output='DP-2',recovery_output='DP-1',
            model='pro2',tracking='pro2-sdk',mode_switch=False,sbs_mode=SCOPE,
            desktops=[dict(width=1920,height=1080,scale=1)],plugin=route['plugin'],renderer=route['renderer'],
            pro2_route=pro2_route.consent_block(route,44.5)))

    def test_only_production_route_can_start_3d_with_a_healthy_scope(self):
        scope = TestScope('/pose',44.5,'/health','nonce',fixed_desktops=False)
        with patch.object(scope,'healthy'), patch.object(managed,'_run_managed_impl',return_value='started'):
            self.assertEqual(managed.run_pro2_test_managed(Mock(),self.consent(),self.dir,test_scope=scope),'started')
            scope.fixed_desktops=True
            with self.assertRaises(ValueError):
                managed.run_pro2_test_managed(Mock(),self.consent(),self.dir,test_scope=scope)

    def test_wrong_scope_is_refused_before_client_access(self):
        consent=self.consent();consent['sbs_mode']=dict(width=3840,height=1200,refresh_hz=[90,60])
        client=Mock();scope=TestScope('/pose',44.5,'/health','nonce',fixed_desktops=False)
        with self.assertRaises(ValueError):managed.run_pro2_test_managed(client,consent,self.dir,test_scope=scope)
        self.assertEqual(client.mock_calls,[])


class AdapterSelection(TestCase):
    def test_model_selects_l1_adapter_and_rejects_luma_scope(self):
        client=Mock()
        adapter=immersive.make_sbs(client,'DP-2',dict(model='pro2',sbs_mode=SCOPE))
        self.assertNotIsInstance(adapter,immersive.NoDeviceSbs)
        self.assertEqual(adapter.model,'pro2')
        self.assertIsInstance(immersive.make_sbs(client,'DP-2',dict(model='pro2',
            sbs_mode=dict(width=3840,height=1200,refresh_hz=[90,60]))),immersive.NoDeviceSbs)


class Reconnect(TestCase):
    def test_tracking_or_usb_loss_during_display_wait_ends_the_hold(self):
        for failure in ('tracking', 'usb'):
            with self.subTest(failure=failure):
                calls=[];report={}
                def launch():
                    calls.append(1); report['stop_reason']='renderer-closed'; return 1
                scope=SimpleNamespace(healthy=Mock(side_effect=[None,ValueError('stale')] if failure=='tracking' else None))
                holds=managed.survive.Holds();holds.revive=lambda:False
                identity=[(1,30),(1,30),(1,31)] if failure=='usb' else [(1,30)]*3
                with patch.object(managed.survive,'usb_identity',side_effect=identity), \
                     patch.object(managed.survive,'output_ok',return_value=False):
                    managed.keep_renderer(launch,Mock(),'DP-2',report,None,lambda:None,holds,
                        switching=lambda:False,tell=lambda _:None,pro2_scope=scope)
                self.assertEqual(len(calls),1)
                self.assertEqual([entry['resumed'] for entry in holds.record],[False])

    def test_pro2_display_handover_restarts_but_usb_loss_or_stale_tracking_stops(self):
        for usb_after,healthy,restarts in (((1,30),True,True),((1,31),True,False),((1,30),False,False)):
            with self.subTest(usb_after=usb_after,healthy=healthy):
                report={};calls=[]
                def launch():
                    calls.append(1)
                    report['stop_reason']='renderer-closed' if len(calls)==1 else 'interrupted'
                    return 1
                scope=SimpleNamespace(healthy=Mock(side_effect=None if healthy else ValueError('stale')))
                holds=managed.survive.Holds();holds.revive=Mock(side_effect=AssertionError('no provider restart'))
                with patch.object(managed.survive,'usb_identity',side_effect=lambda **_: (1,30) if not calls else usb_after), \
                     patch.object(managed.survive,'output_ok',return_value=True), \
                     patch.object(managed.survive,'wait_for_glasses',return_value=True):
                    managed.keep_renderer(launch,Mock(),'DP-2',report,None,lambda:None,holds,
                        switching=lambda:True,tell=lambda _:None,pro2_scope=scope)
                self.assertEqual(len(calls),2 if restarts else 1)
