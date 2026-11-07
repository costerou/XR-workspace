"""Run build/offline release suites in a private environment on renderD129 only.
No install, live compositor, bus, service or device-control commands are included.
"""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time

ROOT=Path(__file__).resolve().parents[2]


def descendants(root):
    parents={}
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit(): continue
        try: parents[int(proc.name)]=int((proc/'stat').read_text().rsplit(')',1)[1].split()[1])
        except (OSError,ValueError,IndexError): pass
    found={root}
    while True:
        added={pid for pid,parent in parents.items() if parent in found}
        if added<=found:return found
        found|=added


def execute(name,args,env,out,manifest,timeout=1800):
    start=time.monotonic();nodes={};violation=None
    with (out/(name+'.log')).open('w') as log:
        process=subprocess.Popen(args,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        while process.poll() is None:
            for pid in descendants(process.pid):
                try:
                    for fd in Path(f'/proc/{pid}/fd').iterdir():
                        target=os.readlink(fd)
                        if target.startswith('/dev/dri/'):
                            nodes.setdefault(str(pid),set()).add(target)
                            if target not in ('/dev/dri/renderD129',):violation=f'test PID {pid} opened unapproved GPU node {target}'
                except OSError:pass
            if violation or time.monotonic()-start>timeout:
                # This process group was created above, solely for this test command.
                os.killpg(process.pid,signal.SIGTERM)
                try:process.wait(timeout=5)
                except subprocess.TimeoutExpired:os.killpg(process.pid,signal.SIGKILL);process.wait()
                break
            time.sleep(.5)
        rc=process.wait()
    record=dict(name=name,command=args,exit_code=rc,seconds=round(time.monotonic()-start,2),gpu_nodes={p:sorted(v) for p,v in nodes.items()},isolation_error=violation)
    manifest['commands'].append(record)
    (out/'release-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(name,rc,record['seconds'],flush=True)
    return rc==0 and violation is None


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--build',type=Path,default=ROOT/'build/integration-release')
    parser.add_argument('--python',type=Path,default=ROOT/'build/test-venv/bin/python')
    parser.add_argument('--skip-build',action='store_true')
    parser.add_argument('--suite',action='append',choices=('configure','build','ctest','python','qml','bind'))
    args=parser.parse_args();out=args.output.absolute();out.mkdir(parents=True,exist_ok=False)
    build=args.build.absolute();python=args.python.absolute()
    manifest=dict(head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),commands=[],gpu='/dev/dri/renderD129',live_session_access=False)
    with tempfile.TemporaryDirectory(prefix='xr-release-private-') as temp:
        private=Path(temp)
        for child in ('home','config','runtime'):(private/child).mkdir(mode=0o700)
        fixture=private/'bind-fixture';fixture.mkdir()
        (fixture/'hyprland.lua').write_text('hl.bind("SUPER + F12", hl.dsp.no_op())\n')
        env={k:v for k,v in os.environ.items() if not k.startswith(('WAYLAND_','HYPRLAND_','DBUS_')) and k not in ('DISPLAY','XAUTHORITY')}
        env.update(HOME=str(private/'home'),XDG_CONFIG_HOME=str(private/'config'),XDG_RUNTIME_DIR=str(private/'runtime'),
                   DRI_PRIME='pci-0000_12_00_0!',XR_BENCH_RENDER_NODE='/dev/dri/renderD129',XR_DEPTH_TEST_NODE='/dev/dri/renderD129',
                   CONTROL_WIRE_CHECK=str(build/'control_wire_check'),XR_RESTORE_GEOMETRY_BINARY=str(build/'restore_band_test'),
                   XR_NATIVE_REPORT_BINARY=str(out/'native_report'),PYTHONPATH=str(ROOT)+':'+str(ROOT/'tests/native_spike'))
        commands=[]
        if not args.skip_build:
            commands += [('configure',['cmake','-S','spike','-B',str(build),'-DCMAKE_BUILD_TYPE=Release','-DXR_ENABLE_OWNERSHIP_PLUGIN=ON','-DXR_FAST_BUILD_TOOLS=OFF']),
                         ('build',['cmake','--build',str(build),'--clean-first','-j','4'])]
        commands += [('ctest',['ctest','--test-dir',str(build),'--output-on-failure','--output-junit',str(out/'ctest.xml'),'-j','2'])]
        for directory in sorted((ROOT/'tests').iterdir()):
            if directory.is_dir() and any(directory.rglob('test_*.py')):commands.append(('python-'+directory.name,[str(python),'-m','pytest',str(directory),'-q','--junitxml',str(out/('python-'+directory.name+'.xml'))]))
        commands.append(('qml',[str(python),'-m','unittest','discover','-s','omarchy-plugin/cody.viture-workspace/tests']))
        commands.append(('bind',[str(python),'-m','tools.native_spike.bind_check','--config',str(fixture/'hyprland.lua'),
                                '--hyprland','/usr/bin/Hyprland','--keybinds',str(private/'keybinds.json')]))
        passed=True
        for name,command in commands:
            if args.suite and name.split('-')[0] not in args.suite: continue
            if name.startswith('python-') and not (out/'native_report').exists():
                good=execute('native-report-build',[str(python),str(ROOT/'tools/xr_options_evidence/build_native_report.py'),
                             '--build',str(build),'--output',str(out/'native_report')],env,out,manifest)
                passed &= good
                if not good:break
            good=execute(name,command,env,out,manifest)
            passed &= good
            if name in ('configure','build') and not good:break
        manifest['passed']=passed;(out/'release-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
        return 0 if passed else 1

if __name__=='__main__':raise SystemExit(main())
