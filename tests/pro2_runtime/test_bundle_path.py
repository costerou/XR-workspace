"""Real supervised command validation, offline request spy, never compositor/SDK."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
from tools.xr_controller.pro2_test import BundleClient,execute
from tools.pro2_runtime.launch import verify_manifest
from tools.pro2_diagnostic.candidate import SHA256

class BundlePathTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='pro2-release-review-z3nsa4vu-');self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'pro2-runtime-release/runtime';(self.root/'bin').mkdir(parents=True)
        files={}
        for name in ('pro2-sdk-bridge','xr-native-spike','libxr-ownership.so'):
            p=self.root/'bin'/name;p.write_bytes(b'synthetic artifact');files['bin/'+name]=hashlib.sha256(p.read_bytes()).hexdigest()
        self.manifest=dict(schema=1,sdk_sha256=SHA256,files=files)
        (self.root/'manifest.json').write_text(json.dumps(self.manifest))
    def client(self):
        client=BundleClient.__new__(BundleClient);client.bundle_root=self.root;client.bundle_manifest=self.manifest
        client._request=Mock(return_value=b'ok');return client
    def test_release_layout_real_load_and_unload_validation(self):
        verify_manifest(self.root);client=self.client();path=str(self.root/'bin/libxr-ownership.so')
        client.plugin_load(path);client.plugin_unload(path)
        self.assertEqual([c.args[0] for c in client._request.call_args_list],['/plugin load '+path,'/plugin unload '+path])
    def test_wrong_path_and_changed_pin_never_request(self):
        client=self.client()
        for path in (str(self.root/'other.so'),str(self.root/'bin/../bin/libxr-ownership.so')):
            with self.assertRaises(ValueError):client.plugin_load(path)
        (self.root/'bin/libxr-ownership.so').write_bytes(b'changed')
        with self.assertRaises(ValueError):client.plugin_load(str(self.root/'bin/libxr-ownership.so'))
        client._request.assert_not_called()
    def test_unsupported_location_preflight_precedes_client_and_sdk(self):
        from test_controller import ControllerTests
        # Use that test's valid scoped consent without constructing any real client.
        source=ControllerTests();source.setUp();self.addCleanup(source.tmp.cleanup)
        bad=self.root.parent/'unsafe bundle';self.root.rename(bad)
        client=Mock(side_effect=AssertionError('no compositor client'));session=Mock(side_effect=AssertionError('no SDK owner'))
        with patch.dict(os.environ,XDG_RUNTIME_DIR=self.tmp.name),self.assertRaises(ValueError):
            execute(source.data,bad,Path(self.tmp.name),client_factory=client,session_factory=session)
        client.assert_not_called();session.assert_not_called()
    def test_actual_controller_plugin_rejection_closes_synthetic_owner(self):
        import time
        from test_controller import ControllerTests
        from tools.native_spike.hyprland import OWNERSHIP_PLUGIN_NAME
        source=ControllerTests();source.setUp();self.addCleanup(source.tmp.cleanup)
        client=self.client();client._request.return_value=b'rejected by synthetic compositor'
        client.plugins_loaded=Mock(return_value=[]);client.park_presentation=Mock()
        events=[]
        class Session:
            def __init__(self,root,directory,projection,seconds,approved,**_):
                directory.mkdir();self.prefix=str(directory/'pose_');self.projection=projection;self.nonce='n';self.health_path=directory/'health.json'
            def start(self):
                events.append('start');self.health_path.write_text(json.dumps(dict(nonce='n',ready=True,monotonic=time.monotonic())));self.health_path.chmod(0o600)
            def close(self):events.append('close')
        run=Mock(side_effect=AssertionError('must not render on plugin rejection'))
        with patch.dict(os.environ,XDG_RUNTIME_DIR=self.tmp.name),patch('tools.xr_controller.pro2_test.monitor_rules.refuse_start'):
            result=execute(source.data,self.root,Path(self.tmp.name),client_factory=lambda *a:client,session_factory=Session,run=run)
        self.assertFalse(result['complete']);self.assertEqual(events,['start','close']);run.assert_not_called()
        self.assertEqual(client._request.call_count,1)
        self.assertIn('supervised plugin command rejected',result['errors'])
        client.park_presentation.assert_not_called()
