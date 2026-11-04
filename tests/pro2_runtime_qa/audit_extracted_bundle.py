"""Audit copied archive extraction + packaged preflight/dispatch without SDK/client."""
import argparse
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
from audit_bundle import audit

SPY = r'''
import ctypes,json,sys
from pathlib import Path
from unittest.mock import Mock,patch
root=Path(sys.argv[1]).resolve()
sys.path.insert(0,str(root))
with patch.object(ctypes,'CDLL',side_effect=AssertionError('SDK/library load forbidden')) as library:
 from tools.pro2_runtime import launch
 from tools.xr_controller import pro2_test
 from tools.pro2_runtime import scope
 from tools.native_spike.hyprland import HyprlandClient,PLUGIN_PATH
 assert Path(launch.__file__).resolve().is_relative_to(root)
 assert Path(pro2_test.__file__).resolve().is_relative_to(root)
 assert Path(scope.__file__).resolve().is_relative_to(root)
 for name in ('TestScope','ScopedGuard','BundleClient'):
  assert getattr(pro2_test,name) is getattr(scope,name)
  assert getattr(pro2_test,name).__module__=='tools.pro2_runtime.scope'
 with patch.object(HyprlandClient,'__init__',side_effect=AssertionError('real client construction forbidden')) as init:
  actual,manifest=launch.verify_manifest(root)
  client=pro2_test.BundleClient.__new__(pro2_test.BundleClient)
  client.bundle_root=actual;client.bundle_manifest=manifest
  client._request=Mock(return_value=b'ok')
  plugin=str(actual/'bin/libxr-ownership.so')
  assert PLUGIN_PATH.fullmatch(plugin) is None
  client.plugin_load(plugin);client.plugin_unload(plugin)
  assert [call.args[0] for call in client._request.call_args_list]==['/plugin load '+plugin,'/plugin unload '+plugin]
  init.assert_not_called();library.assert_not_called()
  print(json.dumps(dict(source_commit=manifest['source_commit'],packaged_modules=True,pin_verified=True,
    load_unload_requests_spied=2,real_client_constructed=False,sdk_loaded=False,compositor_contacted=False)))
'''


def run(bundle, source):
    original=audit(bundle, source)
    with tempfile.TemporaryDirectory(prefix='pro2-release-qa-') as tmp:
        base=Path(tmp);archive=base/'review.tar'
        with tarfile.open(archive,'w') as stream:
            stream.add(Path(bundle),arcname='pro2-runtime-release/runtime')
        extracted=base/'unpacked';extracted.mkdir(mode=0o700)
        with tarfile.open(archive) as stream:
            stream.extractall(extracted,filter='data')
        root=extracted/'pro2-runtime-release/runtime'
        copied=audit(root, source)
        if copied!=original:raise ValueError('extraction differs from reviewed bundle')
        command=['unshare','-Ur','-i','-n','--']
        preflight=subprocess.run([*command,str(root/'bin/pro2-supervised-test'),'--preflight'],capture_output=True,text=True,check=True,timeout=30)
        result=json.loads(preflight.stdout)
        if result.get('sdk_started') is not False or result.get('compositor_contacted') is not False or result.get('source_commit')!=original['source_commit']:
            raise ValueError('preflight scope mismatch')
        dispatch=subprocess.run([*command,'python','-I','-c',SPY,str(root)],capture_output=True,text=True,check=True,timeout=30)
        spy=json.loads(dispatch.stdout)
        if audit(root,source)!=original:raise ValueError('artifact changed during preflight/spy')
        return dict(bundle=original,extraction_identical=True,packaged_preflight=result,packaged_dispatch=spy)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('bundle',type=Path);parser.add_argument('--source',required=True)
    args=parser.parse_args()
    print(json.dumps(run(args.bundle,args.source),indent=2))
