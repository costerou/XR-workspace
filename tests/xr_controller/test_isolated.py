"""Explicit VM controller boundary; synthetic proof is injected only in these offline tests."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from tools.native_spike.panel_consent import build_consent
from tools.native_spike.isolated import FORBIDDEN_INSTANCE
from tools.xr_controller import isolated
from tests.xr_controller.test_runner import DESKTOPS, INSTANCE


class IsolationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.vm=dict(schema=1,gate='VM-isolated',mode='isolated-vm',run_id='vmtest',instance=INSTANCE,
            boot_id='12345678-1234-1234-1234-123456789abc',session='session1',runtime=str(self.root),
            output='Virtual-1',recovery_output='Virtual-2',allow=['headless-outputs','capture-managed-outputs'],
            capture='managed-outputs',input=False,sdk=False,mode_switch=False)
        self.path=self.root/'vm.json'; self.write(self.vm)
        pin=dict(path='/tmp/bin',sha256='a'*64)
        self.panel=build_consent(dict(run_id='vmtest',instance=INSTANCE,output='Virtual-1',recovery_output='Virtual-2',
            model='unknown',tracking=False,mode_switch=False,plugin=pin,renderer=pin,desktops=DESKTOPS))
        self.client=Mock(instance=INSTANCE,runtime_dir=self.root)

    def write(self,data):
        self.path.write_text(json.dumps(data)); self.path.chmod(0o600)

    def test_dual_scope_proof_checked_and_geometry_preserved(self):
        with patch.object(isolated.vm,'check_scope') as check:
            result=isolated.authorize(self.path,self.panel,self.client)
        check.assert_called_once_with(self.client,self.vm,Path('/'))
        self.assertEqual(result,self.vm)
        self.assertEqual(self.panel['desktops'],DESKTOPS)

    def test_kernel_proof_failure_fails_closed(self):
        with patch.object(isolated.vm,'check_scope',side_effect=ValueError('not a VM')):
            with self.assertRaisesRegex(ValueError,'not a VM'):
                isolated.authorize(self.path,self.panel,self.client)
        self.assertEqual(self.client.mock_calls,[])

    def test_cross_mode_panel_record_is_not_vm_authority(self):
        self.write(self.panel)
        with self.assertRaises(ValueError): isolated.authorize(self.path,self.panel,self.client)

    def test_scope_cannot_expand_or_cross_runs(self):
        for key,value in [('run_id','other'),('output','DP-2'),('recovery_output','Virtual-3'),
                          ('tracking','xrlinuxdriver'),('mode_switch',dict(width=1920,height=1200,refresh_hz=120))]:
            with self.subTest(key=key),patch.object(isolated.vm,'check_scope'):
                with self.assertRaises(ValueError):
                    isolated.authorize(self.path,{**self.panel,key:value},self.client)

    def test_known_host_rejected_before_connection(self):
        self.write({**self.vm,'instance':FORBIDDEN_INSTANCE})
        with self.assertRaises(ValueError):
            isolated.load_vm(self.path)

    def test_execute_refuses_bad_proof_before_loading_plugin(self):
        panelpath=self.root/'panel.json'
        panelpath.write_text(json.dumps(self.panel)); panelpath.chmod(0o600)
        with patch.object(isolated,'HyprlandClient',return_value=self.client), \
             patch.object(isolated.vm,'check_scope',side_effect=ValueError('bad proof')):
            with self.assertRaisesRegex(ValueError,'bad proof'):
                isolated.execute('start',self.path,panelpath,self.root)
        self.assertEqual(self.client.mock_calls,[])

    def test_renderer_crash_is_not_a_successful_unit_exit(self):
        pins={k:self.panel[k] for k in ('plugin','renderer')}
        result=dict(complete=True,remaining=[],renderer_returncode=-9)
        self.client.plugins_loaded.side_effect=[[],['xr-ownership'],[]]
        with patch.dict(os.environ,XDG_DATA_HOME=str(self.root)), \
             patch.object(isolated,'load_vm',return_value=self.vm), \
             patch.object(isolated,'load_panel_consent',return_value=self.panel), \
             patch.object(isolated,'HyprlandClient',return_value=self.client), \
             patch.object(isolated,'authorize'), \
             patch.object(isolated,'load_installed',return_value=pins), \
             patch.object(isolated,'verify_pinned'), \
             patch.object(isolated.vm,'run_isolated',return_value=result), \
             patch.object(isolated,'_write_result') as write:
            self.assertEqual(isolated.execute('start',self.path,self.path,self.root),2)
        self.client.plugin_unload.assert_called_once()
        self.assertEqual(write.call_args.args[-1]['renderer_returncode'],-9)

    def test_isolated_controller_keeps_one_run_authority(self):
        # begin_start must use the pre-authorized one-run id; status does not invent another.
        from tests.xr_controller.test_state import StateTests
        fixture=StateTests(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        controller=fixture.c
        fixture.observation.update(instance=INSTANCE,output="Virtual-1",monitors=[dict(name="Virtual-1"),dict(name="Virtual-2")])
        fixture.c.tick()
        isolated.bind_controller(controller,self.vm)
        reply=controller.request(dict(op='begin_start'))
        self.assertTrue(reply['ok'],reply)
        self.assertEqual(reply['scope']['run_id'],'vmtest')
