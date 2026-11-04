"""Canonical class/strict guard/fresh-process pickle checks, no external endpoints."""
import ctypes
import json
import os
from pathlib import Path
import pickle
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT=Path(__file__).resolve().parents[2]
CHILD=r'''
import ctypes,json,pickle,sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,sys.argv[1])
with patch.object(ctypes,'CDLL',side_effect=AssertionError('no SDK')) as sdk:
 from tools.native_spike.hyprland import HyprlandClient
 with patch.object(HyprlandClient,'__init__',side_effect=AssertionError('no client construction')) as ctor:
  from tools.pro2_runtime.scope import TestScope,ScopedGuard,BundleClient
  from tools.xr_controller import pro2_test
  data=pickle.loads(Path(sys.argv[2]).read_bytes())
  assert type(data['scope']) is TestScope is pro2_test.TestScope
  assert type(data['guard']) is ScopedGuard is pro2_test.ScopedGuard
  assert type(data['client']) is BundleClient is pro2_test.BundleClient
  assert data['guard'].base.broker.transport is data['client']
  assert data['client'].bundle_manifest['files']=={}
  sdk.assert_not_called();ctor.assert_not_called()
  print(json.dumps(dict(canonical_scope=True,canonical_guard=True,canonical_client=True,client_constructed=False,sdk_loaded=False)))
'''


class CanonicalScopeQA(unittest.TestCase):
    def test_public_aliases_are_canonical_stable_module_classes(self):
        from tools.xr_controller import pro2_test
        for name in ('TestScope','ScopedGuard','BundleClient'):
            cls=getattr(pro2_test,name)
            self.assertEqual(cls.__module__,'tools.pro2_runtime.scope',name)
            import importlib
            self.assertIs(cls,getattr(importlib.import_module('tools.pro2_runtime.scope'),name))

    def test_structural_impostor_rejected_before_managed_or_client(self):
        from tools.native_spike import managed
        client=Mock()
        fake=SimpleNamespace(prefix='/offline/pose_',projection=44.5,health_path='/offline/health',nonce='n',healthy=lambda:None)
        with patch.object(managed,'_run_managed_impl') as start:
            with self.assertRaisesRegex(ValueError,'explicit Pro2 supervised test scope'):
                managed.run_pro2_test_managed(client,dict(model='pro2',mode_switch=False,sbs_mode=None),Path('/offline'),test_scope=fake)
            start.assert_not_called();self.assertFalse(client.mock_calls)

    def test_guard_and_bundle_client_pickle_into_fresh_interpreter(self):
        from tools.xr_controller.pro2_test import TestScope,ScopedGuard,BundleClient
        from tools.native_spike.managed import ManagedGuard
        from tools.native_spike.ownership import OwnershipBrokerClient
        with tempfile.TemporaryDirectory(prefix='pro2-pickle-qa-') as tmp:
            root=Path(tmp);client=BundleClient.__new__(BundleClient)
            client.bundle_root=root;client.bundle_manifest={'files':{}};client.instance='a'*40+'_1_2';client.runtime_dir=root
            client.directory=root/'hypr'/client.instance;client._peer=None;client._session=None;client._paths=None
            scope=TestScope('/offline/pose_',44.5,str(root/'health'),'n')
            guard=ScopedGuard(ManagedGuard(OwnershipBrokerClient(client,'run','session','DP-1'),'DP-2',[]),scope)
            path=root/'payload';path.write_bytes(pickle.dumps(dict(scope=scope,guard=guard,client=client)))
            child=subprocess.run(['unshare','-Ur','-i','-n','--',sys.executable,'-I','-c',CHILD,str(ROOT),str(path)],capture_output=True,text=True,timeout=10)
            self.assertEqual(child.returncode,0,child.stderr)
            self.assertTrue(json.loads(child.stdout)['canonical_client'])

    def test_actual_spawn_guard_worker_resolves_scope_class(self):
        from tools.xr_controller.pro2_test import TestScope,ScopedGuard
        from tools.native_spike.supervisor import Supervisor
        with tempfile.TemporaryDirectory(prefix='pro2-spawn-qa-') as tmp:
            path=Path(tmp)/'health';path.write_text(json.dumps(dict(nonce='n',ready=True,monotonic=time.monotonic())));path.chmod(0o600)
            child=Mock();child.poll.return_value=None
            watchdog=Supervisor(child,lambda:None,guard=ScopedGuard(bool,TestScope('/offline/pose_',44.5,str(path),'n')))
            try:
                watchdog._start_guard()
                self.assertTrue(watchdog._guard_receiver.poll(3),'spawn guard produced no verdict')
                self.assertEqual(watchdog._guard_receiver.recv_bytes(),b'V')
            finally:watchdog._stop_guard()
            self.assertIsNone(watchdog._guard_worker)
