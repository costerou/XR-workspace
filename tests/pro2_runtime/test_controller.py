import hashlib
import json
import os
from pathlib import Path
import pickle
import tempfile
import time
import unittest
from unittest.mock import Mock,patch
from tools.xr_controller.pro2_test import validate,execute,TestScope,ScopedGuard
from tools.pro2_diagnostic.candidate import SHA256
from tools.native_spike.hyprland import OWNERSHIP_PLUGIN_NAME

class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.bundle=self.root/'bundle';(self.bundle/'bin').mkdir(parents=True)
        files={}
        for name in ['pro2-sdk-bridge','xr-native-spike','libxr-ownership.so']:
            p=self.bundle/'bin'/name;p.write_bytes(b'synthetic');files['bin/'+name]=hashlib.sha256(p.read_bytes()).hexdigest()
        (self.bundle/'manifest.json').write_text(json.dumps(dict(schema=1,sdk_sha256=SHA256,files=files)))
        self.data=dict(schema=1,gate='pro2-supervised-v1',model='pro2',run_id='r1',instance='instance',output='DP-2',recovery_output='HDMI-A-1',seconds=5,projection=44.5,parent_authorized=True,exclusive_topology=True,approximate_optics=True,desktops=[dict(width=1920,height=1080,scale=1)])
    def test_normal_or_incomplete_consent_rejected_before_client(self):
        for key,value in [('gate','normal'),('model','beast'),('parent_authorized',False),('projection',None),('seconds',301)]:
            with self.subTest(key=key),self.assertRaises(ValueError):validate({**self.data,key:value})
    def test_test_route_scope_and_teardown(self):
        client=Mock();client.plugins_loaded.side_effect=[[],[OWNERSHIP_PLUGIN_NAME]]
        run=Mock(return_value=dict(complete=True,renderer_returncode=0))
        class Session:
            def __init__(self,root,directory,projection,seconds,approved):
                directory.mkdir();self.prefix=str(directory/'pose_');self.projection=projection;self.nonce='n';self.health_path=directory/'health.json'
                self.health_path.write_text(json.dumps(dict(nonce='n',ready=True,monotonic=time.monotonic())));self.health_path.chmod(0o600)
            def start(self):return self
            def close(self):pass
        with patch.dict(os.environ,XDG_RUNTIME_DIR=str(self.root)),patch('tools.xr_controller.pro2_test.monitor_rules.refuse_start'):
            result=execute(self.data,self.bundle,self.root,client_factory=lambda *a:client,session_factory=Session,run=run)
        self.assertTrue(result['complete'],result)
        kwargs=run.call_args.kwargs;self.assertIsInstance(kwargs['test_scope'],TestScope)
        self.assertEqual(run.call_args.args[1]['mode_switch'],False)
        self.assertEqual(run.call_args.args[1]['sbs_mode'],None)
        client.apply_session_mode.assert_not_called();client.plugin_unload.assert_called_once()
        self.assertEqual(pickle.loads(pickle.dumps(kwargs['test_scope'])).projection,44.5)
    def test_health_fails_closed(self):
        f=self.root/'health';f.write_text(json.dumps(dict(nonce='n',ready=True,monotonic=time.monotonic()-1)));f.chmod(0o600)
        with self.assertRaises(ValueError):TestScope('/private/pose_',44.5,str(f),'n').healthy()
