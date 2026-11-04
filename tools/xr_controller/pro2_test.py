"""Explicit bounded Pro2 supervised-test controller. Normal panel starts stay blocked."""
import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import time
from tools.native_spike.ipc import read_private_json,token
from tools.native_spike import monitor_rules
from tools.native_spike.hyprland import HyprlandClient,ownership_plugin_count,PLUGIN_PATH
from tools.native_spike.profiles import ProfileStore,profiles_path
from tools.native_spike.geometry import validate_desktops
from tools.native_spike.supervisor import CAPTURE_SCOPE
from tools.pro2_runtime.launch import RuntimeSession,validate_projection,verify_manifest

@dataclass
class TestScope:
    prefix:str
    projection:float
    health_path:str
    nonce:str
    def healthy(self):
        validate_projection(self.projection,True)
        data=read_private_json(Path(self.health_path))
        age=time.monotonic()-data['monotonic']
        if data.get('nonce')!=self.nonce or data.get('ready') is not True or not 0<=age<=.5:
            raise ValueError('Pro2 supervised provider stale/unready')

@dataclass
class ScopedGuard:
    base:object
    scope:TestScope
    def __call__(self):self.scope.healthy();self.base()


def validate(data):
    keys={'schema','gate','model','run_id','instance','output','recovery_output','seconds','projection','parent_authorized','exclusive_topology','approximate_optics','desktops'}
    if not isinstance(data,dict) or set(data)!=keys or type(data['schema']) is not int or data['schema']!=1 or data['gate']!='pro2-supervised-v1' or data['model']!='pro2':
        raise ValueError('explicit Pro2 supervised consent required')
    for field in ('parent_authorized','exclusive_topology','approximate_optics'):
        if data[field] is not True:raise ValueError('explicit bounded test assumptions required')
    for field in ('run_id','instance','output','recovery_output'):token(data[field])
    if len(data['run_id'])>80 or data['output']==data['recovery_output']:raise ValueError('independent recovery output required')
    validate_projection(data['projection'],True)
    if type(data['seconds']) is not int or not 1<=data['seconds']<=300:raise ValueError('bounded seconds required')
    return {**data,'desktops':validate_desktops(data['desktops'])}


class BundleClient(HyprlandClient):
    def __init__(self,instance,runtime,root,manifest):
        super().__init__(instance,runtime);self.bundle_root=root;self.bundle_manifest=manifest
    def _plugin_command(self,verb,path,data_home=None,recovery_pin=None):
        from tools.pro2_runtime.launch import pinned_fd
        if verb not in ('load','unload') or path!=str(self.bundle_root/'bin/libxr-ownership.so') or not PLUGIN_PATH.fullmatch(path):
            raise ValueError('supervised bundle plugin pin required')
        fd=pinned_fd(path,self.bundle_manifest['files']['bin/libxr-ownership.so']);os.close(fd)
        if self._request(f'/plugin {verb} {path}').decode().strip()!='ok':raise ValueError('supervised plugin command rejected')


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
    provider=None;loaded=False;result=None;errors=[];started=False
    plugin=str(root/'bin/libxr-ownership.so')
    try:
        monitor_rules.refuse_start(client)
        if ownership_plugin_count(client.plugins_loaded()):raise ValueError('ownership plugin already present')
        provider=session_factory(root,Path(state_dir)/data['run_id'],data['projection'],data['seconds'],True)
        provider.start()
        scope=TestScope(provider.prefix,provider.projection,str(provider.health_path),provider.nonce);scope.healthy()
        client.plugin_load(plugin)
        loaded=True
        if ownership_plugin_count(client.plugins_loaded())!=1:raise ValueError('test ownership plugin load unconfirmed')
        client.park_presentation(data['output'])
        started=True
        result=(run or run_pro2_test_managed)(client,consent,runtime,test_scope=scope,desktops=data['desktops'],
            store=ProfileStore(Path(state_dir)/'pro2-test-profiles.json','pro2'),renderer=str(root/'bin/xr-native-spike'))
    except BaseException as exc:errors.append(str(exc) or type(exc).__name__)
    finally:
        preserve=started and ((result is not None and result.get('complete') is not True) or (result is None and journal.exists()))
        if loaded and not preserve:
            try:client.plugin_unload(plugin)
            except Exception as exc:errors.append(str(exc))
        if preserve:errors.append('ownership retained for explicit pro2-test --recover')
        if provider is not None:
            try:provider.close()
            except Exception as exc:errors.append(str(exc))
    return dict(run_id=data['run_id'],complete=bool(result and result.get('complete') and result.get('renderer_returncode')==0 and not errors),run=result,errors=errors)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle',required=True,type=Path);parser.add_argument('--consent',required=True,type=Path)
    parser.add_argument('--state-dir',required=True,type=Path);parser.add_argument('--recover',action='store_true')
    args=parser.parse_args();args.state_dir.mkdir(mode=0o700,parents=False,exist_ok=True)
    result=execute(read_private_json(args.consent),args.bundle,args.state_dir,recover=args.recover)
    from .runner import _write_result
    _write_result(args.state_dir,'pro2-test-result.json',result)
    print(json.dumps(result));return 0 if result.get('complete') else 2

if __name__=='__main__':raise SystemExit(main())
