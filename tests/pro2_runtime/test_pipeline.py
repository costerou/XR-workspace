"""Simulated CSDK -> actual launcher -> native Publisher/reader/PoseState."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from tools.pro2_runtime import launch

ROOT=Path(__file__).resolve().parents[2]
@unittest.skipUnless(os.environ.get('PRO2_RUNTIME_NAMESPACE_TEST')=='1','run via tests/pro2_runtime/run.py in private namespaces')
class PipelineTests(unittest.TestCase):
    def test_actual_wrapper_sdk_and_native_reader(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'bin').mkdir();usb=root/'sysfs';(usb/'1-1').mkdir(parents=True)
            for name,value in dict(idVendor='35ca',idProduct='1301',product='VITURE PRO 2 XR Glasses',busnum='1',devnum='13').items():(usb/'1-1'/name).write_text(value+'\n')
            sdk=root/'fixture.so';binary=root/'bin/pro2-sdk-bridge';probe=root/'probe'
            for cmd in (
                ['cc','-shared','-fPIC','-pthread',str(ROOT/'tests/pro2_runtime_qa/sdk_fixture.c'),'-o',str(sdk)],
                ['c++','-std=c++20','-DPRO2_SYNTHETIC','-pthread','-Ispike/driver/include','tools/pro2_runtime/bridge.cpp','spike/driver/src/publish.cpp','-lcrypto','-ldl','-o',str(binary)],
                ['c++','-std=c++20','-pthread','-Ispike/include','tests/pro2_runtime/probe.cpp','spike/src/pose.cpp','-o',str(probe)]):subprocess.run(cmd,cwd=ROOT,check=True,capture_output=True)
            digest=hashlib.sha256(sdk.read_bytes()).hexdigest()
            for name in ('xr-native-spike','libxr-ownership.so'):(root/'bin'/name).write_bytes(b'fixture')
            files={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (root/'bin').iterdir()}
            (root/'manifest.json').write_text(json.dumps(dict(schema=1,sdk_sha256=digest,files=files)))
            class SyntheticSession(launch.RuntimeSession):
                def command(self,*args):return super().command(*args)+['--fixture-root',str(usb),'--fixture-hash',digest]
            with patch.object(launch,'LIBRARY',str(sdk)),patch.object(launch,'SHA256',digest),patch.object(launch,'inventory',return_value=[('35ca','1301','VITURE PRO 2 XR Glasses')]),patch.object(launch,'competing',return_value=False):
                session=SyntheticSession(root,root/'run',44.5,2,True)
                try:
                    session.start();session.healthy()
                    result=subprocess.run([str(probe),session.prefix],capture_output=True,timeout=5)
                    self.assertEqual(result.returncode,0,result.stderr)
                    self.assertGreater(json.loads(result.stdout)['native_pose_samples'],5)
                finally:session.close()
                self.assertEqual(session.done['cleanup_errors'],0)
                self.assertFalse(list((root/'run').glob('pose_*')))
