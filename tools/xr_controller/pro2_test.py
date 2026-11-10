"""Explicit bounded Pro2 supervised-test controller. Normal panel starts stay blocked."""
import argparse
import hashlib
import json
import os
import signal
from pathlib import Path
import time
from tools.native_spike.ipc import read_private_json,token
from tools.native_spike import monitor_rules
from tools.native_spike.hyprland import HyprlandClient,ownership_plugin_count
from tools.native_spike.profiles import ProfileStore,profiles_path
from tools.native_spike.geometry import validate_desktops
from tools.native_spike.supervisor import CAPTURE_SCOPE
from tools.native_spike.display_policy import PRO2_MOUNT_PITCH
from tools.pro2_runtime.launch import RuntimeSession,validate_mount_pitch,validate_pose_rate,validate_projection,verify_manifest

from tools.pro2_runtime.scope import TestScope,ScopedGuard,BundleClient


def validate(data):
    keys={'schema','gate','model','run_id','instance','output','recovery_output','seconds','projection','parent_authorized','exclusive_topology','approximate_optics','desktops'}
    if not isinstance(data,dict) or set(data)-{'pose_rate_hz','mount_pitch_deg','level_gravity'}!=keys or type(data['schema']) is not int or data['schema']!=1 or data['gate']!='pro2-supervised-v1' or data['model']!='pro2':
        raise ValueError('explicit Pro2 supervised consent required')
    for field in ('parent_authorized','exclusive_topology','approximate_optics'):
        if data[field] is not True:raise ValueError('explicit bounded test assumptions required')
    for field in ('run_id','instance','output','recovery_output'):token(data[field])
    if len(data['run_id'])>80 or data['output']==data['recovery_output']:raise ValueError('independent recovery output required')
    validate_projection(data['projection'],True);validate_pose_rate(data.get('pose_rate_hz',60));mount=validate_mount_pitch(data.get('mount_pitch_deg',PRO2_MOUNT_PITCH))
    if type(data.get('level_gravity',False)) is not bool:raise ValueError('level_gravity must be true or false')
    if type(data['seconds']) is not int or not 1<=data['seconds']<=300:raise ValueError('bounded seconds required')
    return {**data,'mount_pitch_deg':mount,'desktops':validate_desktops(data['desktops'])}



def journal_unfinished(path):
    """True when recovery still needs the broker: the file stays after a finished recovery, so only its steps say. Unreadable: keep it."""
    try:return any(r.get('state')!='done' for r in read_private_json(path,limit=1048576)['operations'])
    except FileNotFoundError:return False
    except (OSError,ValueError,KeyError,TypeError,AttributeError):return True


def provider_summary(provider):
    """The bridge's closing counts, for the run's result: how many samples at what rate, and the error it stopped on (bridge.cpp
    tokens such as 'stale'). Aggregates only, no pose."""
    done=getattr(provider,'done',None)
    summary={k:done[k] for k in ('samples','seconds','pose_rate_hz','gap_ms_counts','error') if done.get(k) not in (None,'')} if isinstance(done,dict) else None
    failure=getattr(provider,'failure',None)
    return {**(summary or {}),'failure':failure} if failure else summary


def execute(data,bundle,state_dir,*,recover=False,client_factory=None,session_factory=RuntimeSession,run=None):
    from tools.native_spike.managed import run_pro2_test_managed,recover_pro2_test_managed
    data=validate(data);root,manifest=verify_manifest(bundle)
    runtime=Path(os.environ['XDG_RUNTIME_DIR']);client=client_factory(data['instance'],runtime) if client_factory else BundleClient(data['instance'],runtime,root,manifest)
    consent={**data,'tracking':'pro2-supervised','mode_switch':False,'sbs_mode':None,'capture':CAPTURE_SCOPE}
    journal=runtime/'xr-workspace-spike'/data['run_id']/'journal.json'
    if recover:
        result=recover_pro2_test_managed(client,consent,journal)
        if result.get('complete') and ownership_plugin_count(client.plugins_loaded())==1:
            client.plugin_unload(str(root/'bin/libxr-ownership.so'))
        return result
    outcome=run_session(client,consent,root,runtime,Path(state_dir),data['desktops'],projection=data['projection'],seconds=data['seconds'],
        pose_rate_hz=data.get('pose_rate_hz',60),mount_pitch_deg=data['mount_pitch_deg'],level_gravity=data.get('level_gravity',False),store=ProfileStore(Path(state_dir)/'pro2-test-profiles.json','pro2'),session_factory=session_factory,run=run)
    result,errors=outcome['run'],outcome['errors']
    return dict(run_id=data['run_id'],manifest_sha256=hashlib.sha256((root/'manifest.json').read_bytes()).hexdigest(),
                complete=bool(result and result.get('complete') and result.get('renderer_returncode')==0 and not errors),run=result,errors=errors,
                **({'provider':outcome['provider']} if outcome.get('provider') else {}))


def run_session(client,consent,root,runtime,state_dir,desktops,*,projection,seconds,pose_rate_hz=60,mount_pitch_deg=0.0,level_gravity=False,panel_route=False,store,session_factory=RuntimeSession,run=None):
    """The bridge, the plugin and the managed run, in order, and their teardown. Shared by the supervised test and the panel route."""
    from tools.native_spike.managed import run_pro2_test_managed
    provider=None;attempted=False;result=None;errors=[];started=False
    plugin=str(root/'bin/libxr-ownership.so')
    journal=runtime/'xr-workspace-spike'/consent['run_id']/'journal.json'
    try:
        monitor_rules.refuse_start(client)
        if ownership_plugin_count(client.plugins_loaded()):raise ValueError('ownership plugin already present')
        options={**({'pose_rate_hz':pose_rate_hz} if pose_rate_hz!=60 else {}),**({'panel_route':True} if panel_route else {}),**({'mount_pitch_deg':mount_pitch_deg} if mount_pitch_deg else {})}
        provider=session_factory(root,state_dir/consent['run_id'],projection,seconds,True,**options)
        provider.start()
        scope=TestScope(provider.prefix,provider.projection,str(provider.health_path),provider.nonce,fixed_desktops=not panel_route,level_gravity=level_gravity,
                      display=getattr(provider,'set_display',None));scope.healthy()
        attempted=True  # before the call: a load that took effect and then raised still has to be unloaded
        client.plugin_load(plugin)
        if ownership_plugin_count(client.plugins_loaded())!=1:raise ValueError('test ownership plugin load unconfirmed')
        client.park_presentation(consent['output'])
        started=True
        result=(run or run_pro2_test_managed)(client,consent,runtime,test_scope=scope,desktops=desktops,store=store,renderer=str(root/'bin/xr-native-spike'))
    except BaseException as exc:errors.append(str(exc) or type(exc).__name__)
    finally:
        preserve=started and ((result is not None and result.get('complete') is not True) or (result is None and journal_unfinished(journal)))
        if attempted and not preserve:
            try:
                if ownership_plugin_count(client.plugins_loaded()):
                    client.plugin_unload(plugin)
                    if ownership_plugin_count(client.plugins_loaded()):errors.append('plugin still loaded after unload')
            except Exception as exc:errors.append(str(exc))
        if preserve:errors.append('ownership retained for explicit recovery')
        if provider is not None:
            try:provider.close()
            except Exception as exc:errors.append(str(exc))
    return dict(run=result,errors=errors,provider=provider_summary(provider) if provider is not None else None)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle',required=True,type=Path);parser.add_argument('--consent',type=Path)
    parser.add_argument('--state-dir',type=Path);parser.add_argument('--recover',action='store_true')
    parser.add_argument('--preflight',action='store_true',help='verify bundle paths/hashes only; no consent, SDK, compositor or state mutation')
    args=parser.parse_args()
    if args.preflight:
        root,manifest=verify_manifest(args.bundle)
        print(json.dumps(dict(bundle=str(root),source_commit=manifest.get('source_commit'),files_verified=len(manifest['files']),plugin_command_path='validated',sdk_started=False,compositor_contacted=False)))
        return 0
    if args.consent is None or args.state_dir is None:parser.error('--consent and --state-dir required for execution/recovery')
    args.state_dir.mkdir(mode=0o700,parents=False,exist_ok=True)
    def stop(*_):raise KeyboardInterrupt('supervised test interrupted')
    signal.signal(signal.SIGTERM,stop)
    data=read_private_json(args.consent)
    validate(data)
    print(f"PRO2 SUPERVISED TEST: approximate diagonal {data['projection']} degrees; uncalibrated, no optical correction.",flush=True)
    result=execute(data,args.bundle,args.state_dir,recover=args.recover)
    from .runner import _write_result
    _write_result(args.state_dir,'pro2-test-result.json',result)
    print(json.dumps(result));return 0 if result.get('complete') else 2

if __name__=='__main__':raise SystemExit(main())
