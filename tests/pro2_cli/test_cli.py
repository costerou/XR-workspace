"""Genuine packaged shell -> child main -> actual managed/journal/_launch.
Only external SDK and compositor/renderer endpoints are synthetic.
"""
import hashlib,json,os,shutil,subprocess,sys,tempfile,time,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];HERE=Path(__file__).resolve().parent
@unittest.skipUnless(os.environ.get('PRO2_CLI_NS')=='1','use tests/pro2_cli/run.py')
class FullCliTests(unittest.TestCase):
    def run_case(self,case):
        with tempfile.TemporaryDirectory(prefix='p2cli-') as tmp:
            p=Path(tmp);bundle=p/'pro2-runtime-release/runtime';shutil.copytree(os.environ.get('PRO2_QA_CLI_BUNDLE',str(ROOT/'build/pro2-cli-fixed-ready')),bundle,ignore=shutil.ignore_patterns('__pycache__'))
            for f in bundle.rglob('*'):
                if f.is_file():f.chmod(0o700 if f.parent==bundle/'bin' else 0o600)
            sdk=p/'fixture.so';probe=p/'probe';bridge=bundle/'bin/pro2-sdk-bridge'
            for cmd in (
                ['cc','-shared','-fPIC','-pthread','tests/pro2_runtime_qa/sdk_fixture.c','-o',str(sdk)],
                ['c++','-std=c++20','-DPRO2_SYNTHETIC','-pthread','-Ispike/driver/include','tools/pro2_runtime/bridge.cpp','spike/driver/src/publish.cpp','-lcrypto','-ldl','-o',str(bridge)],
                ['c++','-std=c++20','-pthread','-Ispike/include','tests/pro2_runtime/probe.cpp','spike/src/pose.cpp','-o',str(probe)]):subprocess.run(cmd,cwd=ROOT,check=True,capture_output=True)
            (bundle/'bin/xr-native-spike').write_text((HERE/'renderer.py').read_text().replace('#!/usr/bin/env python3','#!'+sys.executable));(bundle/'bin/xr-native-spike').chmod(0o700)
            manifest=json.loads((bundle/'manifest.json').read_text());manifest['sdk_sha256']=hashlib.sha256(sdk.read_bytes()).hexdigest()
            manifest['files']={str(f.relative_to(bundle)):hashlib.sha256(f.read_bytes()).hexdigest() for f in bundle.rglob('*') if f.is_file() and f.name!='manifest.json'}
            (bundle/'manifest.json').write_text(json.dumps(manifest))
            usb=p/'u';(usb/'1-1').mkdir(parents=True)
            for k,v in dict(idVendor='35ca',idProduct='1301',product='VITURE PRO 2 XR Glasses',busnum='1',devnum='14').items():(usb/'1-1'/k).write_text(v+'\n')
            runtime=p/'r';runtime.mkdir(mode=0o700);state=p/'s';state.mkdir(mode=0o700);home=p/'h';home.mkdir()
            instance='a'*40+'_123_456';consent=p/'consent.json'
            consent.write_text(json.dumps(dict(schema=1,gate='pro2-supervised-v1',model='pro2',run_id='genuine-cli',instance=instance,output='DP-2',recovery_output='DP-1',seconds=20,projection=44.5,parent_authorized=True,exclusive_topology=True,approximate_optics=True,desktops=[dict(width=1920,height=1080,scale=1)])));consent.chmod(0o600)
            env=dict(os.environ,HOME=str(home),XDG_CONFIG_HOME=str(home/'config'),XDG_STATE_HOME=str(home/'state'),XDG_RUNTIME_DIR=str(runtime),PRO2_CLI_FIXTURE_SDK=str(sdk),PRO2_CLI_FIXTURE_USB=str(usb),PRO2_CLI_PROBE=str(probe),PRO2_QA_CASE='open' if case=='sdk-open-fail' else 'normal',PRO2_QA_TRACE=str(p/'sdk-trace'))
            fake=p/'Hyprland';shutil.copyfile(sys.executable,fake);fake.chmod(0o700)
            env['PRO2_CLI_CASE']=case
            server=subprocess.Popen([str(fake),str(HERE/'compositor.py'),str(runtime),instance],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            shim=p/'shim';shim.mkdir();(shim/'notify-send').write_text('#!/bin/sh\nexit 0\n');(shim/'notify-send').chmod(0o700);python=shim/'python3'
            python.write_text('#!/bin/sh\nexec '+sys.executable+' -I '+str(HERE/'bootstrap.py')+' "$@"\n');python.chmod(0o700);env['PATH']=str(shim)+':'+env['PATH'];env['DBUS_SESSION_BUS_ADDRESS']='unix:path='+str(runtime/'no-dbus');env.pop('PULSE_SERVER',None);env['PRO2_CLI_CASE']=case
            try:
                deadline=time.monotonic()+3
                while not (runtime/'fake-ready').exists() and server.poll() is None and time.monotonic()<deadline:time.sleep(.01)
                if not (runtime/'fake-ready').exists():
                    if server.poll() is None:server.terminate()
                    out,err=server.communicate(timeout=3)
                    self.fail(dict(stage='fake compositor startup',returncode=server.returncode,runtime=str(runtime),stdout=out.decode(errors='replace'),stderr=err.decode(errors='replace')))
                # Exercise filesystem socket paths exceeding Linux sockaddr_un,
                # while the real client uses its existing pinned-dirfd transport.
                sockets=[runtime/'hypr'/instance/name for name in ('.socket.sock','.socket2.sock')]
                self.assertGreater(len(os.fsencode(sockets[1])),107)
                self.assertTrue(all(path.is_socket() for path in [*sockets,runtime/'wayland-1']))
                child=subprocess.Popen([str(bundle/'bin/pro2-supervised-test'),'--consent',str(consent),'--state-dir',str(state)],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
                stdout,stderr=child.communicate(timeout=30)
                from types import SimpleNamespace
                result=SimpleNamespace(returncode=child.returncode,stdout=stdout,stderr=stderr)
                report=json.loads((state/'pro2-test-result.json').read_text())
                wire=[json.loads(l) for l in (runtime/'wire.jsonl').read_text().splitlines()] if (runtime/'wire.jsonl').exists() else []
                stages=[r.get('stage') for r in wire]
                debug=dict(renderer=json.loads((runtime/'renderer.json').read_text()) if (runtime/'renderer.json').exists() else None,returncode=result.returncode,report=report,stages=stages[-25:],stderr=result.stderr[-1500:],wire_errors=[r for r in wire if 'error'in r])
                self.assertFalse(debug['wire_errors'],debug)
                self.assertEqual(result.returncode,0 if case=='success' else 2,debug)
                self.assertEqual(report['complete'],case=='success',debug)
                if case=='sdk-open-fail':
                    self.assertNotIn('plugin-load',stages);self.assertIsNone(report['run'])
                    self.assertFalse((runtime/'xr-workspace-spike/genuine-cli/journal.json').exists())
                    self.assertIn('destroy',(p/'sdk-trace').read_text());self.assertFalse(list((state/'genuine-cli').glob('pose_*')))
                    return
                self.assertIn('broker-create',stages);self.assertIn('broker-evacuate_remove',stages)
                if case not in ('create-fail','recovery-stuck'):
                    self.assertTrue(any(r.get('binds') for r in wire));self.assertTrue((runtime/'renderer.json').exists())
                if case=='success':
                    self.assertTrue(any(r.get('stage')=='broker-inspect' and r.get('caller_pid')!=child.pid for r in wire),'real spawned guard must inspect through private wire')
                sdk_trace=(p/'sdk-trace').read_text();self.assertIn('destroy',sdk_trace);self.assertNotIn('WRONG_OWNER',sdk_trace)
                self.assertFalse(list((state/'genuine-cli').glob('pose_*')))
                if case=='renderer-fail':self.assertEqual(report['run']['renderer_returncode'],9,debug)
                if case=='create-fail':  # recovery finished inside the run, so nothing is left to hold the plugin for
                    self.assertFalse([e for e in report['errors'] if 'retained' in e],debug)
                if case=='recovery-stuck':  # it could not: the plugin stays, and a second CLI --recover finishes the job
                    self.assertNotIn('plugin-unload',stages)
                    self.assertIn('ownership retained for explicit recovery',report['errors'])
                    before=(p/'sdk-trace').read_text()
                    recovered=subprocess.run([str(bundle/'bin/pro2-supervised-test'),'--consent',str(consent),'--state-dir',str(state),'--recover'],env=env,capture_output=True,text=True,timeout=10)
                    self.assertEqual(recovered.returncode,0,(recovered.stdout,recovered.stderr))
                    self.assertEqual((p/'sdk-trace').read_text(),before,'recover must not touch SDK')
                    stages=[json.loads(l).get('stage') for l in (runtime/'wire.jsonl').read_text().splitlines()]
                self.assertIn('plugin-unload',stages)
                journal=json.loads((runtime/'xr-workspace-spike/genuine-cli/journal.json').read_text());self.assertTrue(all(r['state']=='done' for r in journal['operations']))
            except BaseException:
                server.terminate()
                out,err=server.communicate(timeout=3)
                print('fixture compositor stdout: '+out.decode(errors='replace')[-8000:],file=sys.stderr)
                print('fixture compositor stderr: '+err.decode(errors='replace')[-8000:],file=sys.stderr)
                raise
            finally:
                server.terminate()
                try:server.communicate(timeout=3)
                except subprocess.TimeoutExpired:server.kill();server.communicate()

    def test_real_packaged_cli(self):self.run_case('success')
    def test_renderer_failure_rolls_back(self):self.run_case('renderer-fail')
    def test_partial_output_failure_rolls_back(self):self.run_case('create-fail')
    def test_unfinished_recovery_keeps_the_plugin_for_recover(self):self.run_case('recovery-stuck')
    def test_sdk_open_failure_before_plugin(self):self.run_case('sdk-open-fail')
