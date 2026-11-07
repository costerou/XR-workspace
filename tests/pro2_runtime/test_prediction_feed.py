"""Why prediction never ran on the Pro 2 feed: real bridge, real Publisher, real PoseFeed and predict.cpp; simulated SDK."""
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
class PredictionFeed(unittest.TestCase):
    def feed(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'bin').mkdir();usb=root/'sysfs';(usb/'1-1').mkdir(parents=True)
            for name,value in dict(idVendor='35ca',idProduct='1301',product='VITURE PRO 2 XR Glasses',busnum='1',devnum='13').items():(usb/'1-1'/name).write_text(value+'\n')
            sdk=root/'fixture.so';binary=root/'bin/pro2-sdk-bridge';probe=root/'probe'
            for cmd in (
                ['cc','-shared','-fPIC','-pthread',str(ROOT/'tests/pro2_runtime_qa/sdk_fixture.c'),'-o',str(sdk)],
                ['c++','-std=c++20','-DPRO2_SYNTHETIC','-pthread','-Ispike/driver/include','tools/pro2_runtime/bridge.cpp','spike/driver/src/publish.cpp','-lcrypto','-ldl','-o',str(binary)],
                ['c++','-std=c++20','-pthread','-Ispike/include','tests/pro2_runtime/predict_probe.cpp','spike/src/predict.cpp','spike/src/pose.cpp','-o',str(probe)]):
                subprocess.run(cmd,cwd=ROOT,check=True,capture_output=True)
            digest=hashlib.sha256(sdk.read_bytes()).hexdigest()
            for name in ('xr-native-spike','libxr-ownership.so'):(root/'bin'/name).write_bytes(b'fixture')
            files={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (root/'bin').iterdir()}
            (root/'manifest.json').write_text(json.dumps(dict(schema=1,sdk_sha256=digest,files=files)))
            class Session(launch.RuntimeSession):
                def command(self,*args):return super().command(*args)+['--fixture-root',str(usb),'--fixture-hash',digest]
            with patch.object(launch,'LIBRARY',str(sdk)),patch.object(launch,'SHA256',digest),patch.object(launch,'inventory',return_value=[('35ca','1301','VITURE PRO 2 XR Glasses')]),patch.object(launch,'admit'),patch.object(launch,'competing',return_value=False):
                session=Session(root,root/'run',44.5,4,True)
                try:
                    session.start();session.healthy()
                    done=subprocess.run([str(probe),session.prefix],capture_output=True,timeout=6)
                    self.assertEqual(done.returncode,0,done.stderr)
                    return json.loads(done.stdout)
                finally:session.close()

    def test_every_sample_can_be_predicted(self):
        seen=self.feed()
        self.assertGreater(seen['distinct'],5,seen)
        # The history is fine: this much already held for the live run that predicted nothing.
        self.assertEqual(seen['gaps_ok'],seen['distinct']-3,seen)
        self.assertEqual(seen['omega_ok'],seen['distinct']-3,seen)
        # The bound is what was missing: an all-zero look-ahead has no cap, and look_ahead_from refuses an unbounded one.
        self.assertTrue(seen['look_ahead_valid'],seen)
        self.assertEqual(seen['horizon_ok'],seen['distinct']-3,seen)
        self.assertEqual(seen['cap_ms'],40,seen)
        self.assertAlmostEqual(seen['horizon_ms'],24.6,places=3)
