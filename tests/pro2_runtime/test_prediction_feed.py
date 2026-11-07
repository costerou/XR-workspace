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


@unittest.skipUnless(os.environ.get('PRO2_RUNTIME_NAMESPACE_TEST')=='1','run via tests/pro2_runtime/run.py in private namespaces')
class PoseRateFlag(unittest.TestCase):
    """--pose-rate through the real bridge, against a simulated SDK. No claim about what real glasses deliver."""
    def bridge(self,*extra,sdk_rate=None):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);usb=root/'sysfs';(usb/'1-1').mkdir(parents=True)
            for name,value in dict(idVendor='35ca',idProduct='1301',product='VITURE PRO 2 XR Glasses',busnum='1',devnum='13').items():(usb/'1-1'/name).write_text(value+'\n')
            sdk=root/'fixture.so';binary=root/'bridge'
            subprocess.run(['cc','-shared','-fPIC','-pthread',str(ROOT/'tests/pro2_runtime_qa/sdk_fixture.c'),'-o',str(sdk)],cwd=ROOT,check=True,capture_output=True)
            subprocess.run(['c++','-std=c++20','-DPRO2_SYNTHETIC','-pthread','-Ispike/driver/include','tools/pro2_runtime/bridge.cpp','spike/driver/src/publish.cpp','-lcrypto','-ldl','-o',str(binary)],cwd=ROOT,check=True,capture_output=True)
            digest=hashlib.sha256(sdk.read_bytes()).hexdigest();status=root/'status';trace=root/'trace'
            env=dict(os.environ,PRO2_QA_TRACE=str(trace),**({'PRO2_QA_RATE':sdk_rate} if sdk_rate else {}))
            sdk_fd=os.open(sdk,os.O_RDONLY);status_fd=os.open(status,os.O_CREAT|os.O_WRONLY,0o600)
            try:
                done=subprocess.run([str(binary),'--supervised-pro2-test','--sdk-fd',str(sdk_fd),'--status-fd',str(status_fd),'--prefix',str(root/'pose_'),'--projection','44.5','--seconds','.3','--fixture-root',str(usb),'--fixture-hash',digest,*extra],
                    pass_fds=(sdk_fd,status_fd),env=env,capture_output=True,timeout=8)
            finally:os.close(sdk_fd);os.close(status_fd)
            events=[json.loads(l) for l in status.read_text().splitlines()]
            return done.returncode,events,(trace.read_text().split() if trace.exists() else [])
    def test_sixty_is_the_default_and_opens_rate_code_zero(self):
        code,events,trace=self.bridge()
        self.assertEqual(code,0,events);self.assertIn('open:0',trace);self.assertEqual(events[0]['pose_rate_hz'],60)
    def test_a_supported_rate_opens_its_own_code(self):
        code,events,trace=self.bridge('--pose-rate','120',sdk_rate='2')
        self.assertEqual(code,0,events);self.assertIn('open:2',trace);self.assertEqual(events[-1]['pose_rate_hz'],120)
        self.assertIn('gap_ms_counts',events[-1]);self.assertEqual(sum(events[-1]['gap_ms_counts']),events[-1]['samples']+1)
    def test_a_refused_rate_stops_it_and_never_falls_back_to_sixty(self):
        code,events,trace=self.bridge('--pose-rate','120')
        self.assertNotEqual(code,0);self.assertEqual(events[-1]['error'],'capability');self.assertFalse([t for t in trace if t.startswith('open')])
    def test_a_rate_outside_the_list_is_refused_before_the_sdk(self):
        for rate in ('500','1000','0','abc'):
            with self.subTest(rate=rate):
                code,events,trace=self.bridge('--pose-rate',rate)
                self.assertEqual(code,2);self.assertEqual((events,trace),([],[]))
    def test_a_long_deadline_needs_the_panel_route(self):
        self.assertEqual(launch.ROUTE_SECONDS,43200)
        with self.assertRaises(ValueError):launch.RuntimeSession('/nonexistent','/nonexistent',44.5,301,True)
