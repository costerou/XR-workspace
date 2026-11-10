"""Simulated CSDK -> actual launcher -> native Publisher/reader/PoseState."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from tests.pro2_bridge import build_bridge, implementation
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
                ['c++','-std=c++20','-pthread','-Ispike/include','tests/pro2_runtime/probe.cpp','spike/src/pose.cpp','-o',str(probe)]):subprocess.run(cmd,cwd=ROOT,check=True,capture_output=True)
            build_bridge(binary)
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

    def test_a_reader_holding_the_pose_lock_skips_writes_and_does_not_end_the_bridge(self):
        # A renderer preempted while it copies a sample holds the lock: publish() skips that write (luma/publish.hpp).
        # It ended the whole session ('publish') right after a 3D-off renderer restart (run 36128eab).
        import time
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'bin').mkdir();usb=root/'sysfs';(usb/'1-1').mkdir(parents=True)
            for name,value in dict(idVendor='35ca',idProduct='1301',product='VITURE PRO 2 XR Glasses',busnum='1',devnum='13').items():(usb/'1-1'/name).write_text(value+'\n')
            sdk=root/'fixture.so';binary=root/'bin/pro2-sdk-bridge';holder=root/'hold_lock'
            for cmd in (
                ['cc','-shared','-fPIC','-pthread',str(ROOT/'tests/pro2_runtime_qa/sdk_fixture.c'),'-o',str(sdk)],
                ['cc','-pthread','tests/pro2_runtime/hold_lock.c','-o',str(holder)]):subprocess.run(cmd,cwd=ROOT,check=True,capture_output=True)
            build_bridge(binary)
            digest=hashlib.sha256(sdk.read_bytes()).hexdigest()
            for name in ('xr-native-spike','libxr-ownership.so'):(root/'bin'/name).write_bytes(b'fixture')
            files={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (root/'bin').iterdir()}
            (root/'manifest.json').write_text(json.dumps(dict(schema=1,sdk_sha256=digest,files=files)))
            class SyntheticSession(launch.RuntimeSession):
                def command(self,*args):return super().command(*args)+['--fixture-root',str(usb),'--fixture-hash',digest]
            with patch.object(launch,'LIBRARY',str(sdk)),patch.object(launch,'SHA256',digest),patch.object(launch,'inventory',return_value=[('35ca','1301','VITURE PRO 2 XR Glasses')]),patch.object(launch,'competing',return_value=False):
                session=SyntheticSession(root,root/'run',44.5,3,True)
                try:
                    session.start()
                    held=subprocess.run([str(holder),session.prefix,'200'],capture_output=True,timeout=5)  # the fixture's 16 ms samples land under it
                    self.assertEqual(held.returncode,0,held.stderr)
                    time.sleep(.1)
                    session.healthy()
                finally:session.close()
                self.assertEqual(session.done['error'],'')

    def test_3d_switch_reaches_the_sdk_on_the_owner_thread_with_tracking_live(self):
        import time
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'bin').mkdir();usb=root/'sysfs';(usb/'1-1').mkdir(parents=True);trace=root/'trace'
            for name,value in dict(idVendor='35ca',idProduct='1301',product='VITURE PRO 2 XR Glasses',busnum='1',devnum='13').items():(usb/'1-1'/name).write_text(value+'\n')
            sdk=root/'fixture.so';binary=root/'bin/pro2-sdk-bridge'
            subprocess.run(['cc','-shared','-fPIC','-pthread',str(ROOT/'tests/pro2_runtime_qa/sdk_fixture.c'),'-o',str(sdk)],cwd=ROOT,check=True,capture_output=True)
            build_bridge(binary)
            digest=hashlib.sha256(sdk.read_bytes()).hexdigest()
            for name in ('xr-native-spike','libxr-ownership.so'):(root/'bin'/name).write_bytes(b'fixture')
            files={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (root/'bin').iterdir()}
            (root/'manifest.json').write_text(json.dumps(dict(schema=1,sdk_sha256=digest,files=files)))
            class SyntheticSession(launch.RuntimeSession):
                def command(self,*args):return super().command(*args)+['--fixture-root',str(usb),'--fixture-hash',digest]
            with patch.object(launch,'LIBRARY',str(sdk)),patch.object(launch,'SHA256',digest),patch.object(launch,'inventory',return_value=[('35ca','1301','VITURE PRO 2 XR Glasses')]),patch.object(launch,'competing',return_value=False),patch.dict(os.environ,PRO2_QA_TRACE=str(trace)):
                session=SyntheticSession(root,root/'run',44.5,3,True)
                try:
                    session.start()
                    for on,mode in ((True,0x32),(False,0x34)):
                        session.set_display(on)
                        deadline=time.monotonic()+2
                        while (session.last_display or {}).get('mode')!=mode and time.monotonic()<deadline:
                            with session.lock:session.poll()
                            time.sleep(.01)
                        self.assertEqual(session.last_display,dict(event='display',mode=mode,result=0))
                        session.healthy()  # tracking never paused for it
                finally:session.close()
                self.assertEqual(session.done['error'],'')
                with self.assertRaises(OSError):session.set_display(True)
            lines=trace.read_text().split()
            self.assertEqual([x for x in lines if x.startswith('display:')],['display:50','display:52'])  # never WRONG_OWNER

    @unittest.skipUnless(implementation()=='rust','reconnect is the Rust bridge only (D12); set PRO2_BRIDGE_RUST_DIR')
    def test_session_survives_unplug_and_replug_with_tracking_back(self):
        """D12 gate offline: the glasses leave sysfs for 0.6 s mid-run and come back renumbered. The launcher keeps reporting
        healthy, and a native reader gets poses again from the new SDK session, all without a restart."""
        import time
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'bin').mkdir();usb=root/'sysfs';(usb/'1-1').mkdir(parents=True);trace=root/'trace'
            for name,value in dict(idVendor='35ca',idProduct='1301',product='VITURE PRO 2 XR Glasses',busnum='1',devnum='13').items():(usb/'1-1'/name).write_text(value+'\n')
            sdk=root/'fixture.so';binary=root/'bin/pro2-sdk-bridge';probe=root/'probe'
            for cmd in (
                ['cc','-shared','-fPIC','-pthread',str(ROOT/'tests/pro2_runtime_qa/sdk_fixture.c'),'-o',str(sdk)],
                ['c++','-std=c++20','-pthread','-Ispike/include','tests/pro2_runtime/probe.cpp','spike/src/pose.cpp','-o',str(probe)]):subprocess.run(cmd,cwd=ROOT,check=True,capture_output=True)
            build_bridge(binary)
            digest=hashlib.sha256(sdk.read_bytes()).hexdigest()
            for name in ('xr-native-spike','libxr-ownership.so'):(root/'bin'/name).write_bytes(b'fixture')
            files={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (root/'bin').iterdir()}
            (root/'manifest.json').write_text(json.dumps(dict(schema=1,sdk_sha256=digest,files=files)))
            class SyntheticSession(launch.RuntimeSession):
                def command(self,*args):return super().command(*args)+['--fixture-root',str(usb),'--fixture-hash',digest]
            def wait_for(predicate,seconds=3):
                deadline=time.monotonic()+seconds
                while not predicate() and time.monotonic()<deadline:
                    with session.lock:session.healthy()  # healthy the whole way through: no restart, no failed guard
                    time.sleep(.01)
                self.assertTrue(predicate())
            with patch.object(launch,'LIBRARY',str(sdk)),patch.object(launch,'SHA256',digest),patch.object(launch,'inventory',return_value=[('35ca','1301','VITURE PRO 2 XR Glasses')]),patch.object(launch,'competing',return_value=False),patch.dict(os.environ,PRO2_QA_TRACE=str(trace)):
                session=SyntheticSession(root,root/'run',44.5,4,True)
                try:
                    session.start()
                    (usb/'1-1').rename(root/'unplugged')
                    wait_for(lambda:(session.link or {}).get('event')=='lost')
                    self.assertEqual(session.link['reason'],'unplugged')
                    for _ in range(60):
                        with session.lock:session.healthy()
                        time.sleep(.01)
                    (root/'unplugged'/'devnum').write_text('14\n');(root/'unplugged').rename(usb/'1-1')
                    wait_for(lambda:session.reconnects==1)
                    self.assertGreater(session.link['down_ms'],550)
                    result=subprocess.run([str(probe),session.prefix],capture_output=True,timeout=5)
                    self.assertEqual(result.returncode,0,result.stderr)
                    self.assertGreater(json.loads(result.stdout)['native_pose_samples'],5)
                    session.healthy()
                finally:session.close()
                self.assertEqual((session.done['error'],session.done['cleanup_errors']),('',0))
                self.assertFalse(list((root/'run').glob('pose_*')))
            self.assertEqual(trace.read_text().split().count('create'),2)
