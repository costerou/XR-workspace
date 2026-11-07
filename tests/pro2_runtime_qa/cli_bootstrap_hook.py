"""Test-only interpreter hook for the exact packaged shell entrypoint.
No real SDK, compositor, or plugin requests. Used only inside QA namespaces.
"""
import ctypes
import json
import os
from pathlib import Path
import sys
import time
from unittest.mock import patch


def main():
    args=sys.argv[1:]
    if args[:2]!=['-I','-c']:raise RuntimeError('unexpected packaged interpreter invocation')
    code=args[2];root=Path(args[3]);sys.path.insert(0,str(root))
    config=json.loads(Path(os.environ['PRO2_QA_BOOTSTRAP_CONFIG']).read_text())
    trace=[];loaded=[]
    def record(event,**data):trace.append(dict(event=event,**data))
    from tools.pro2_runtime import launch
    from tools.native_spike import hyprland,managed,monitor_rules
    class Provider:
        def __init__(self,bundle,directory,projection,seconds,approved,**_):
            directory.mkdir(mode=0o700);self.prefix=str(directory/'pose_');self.projection=projection
            self.nonce='fixture';self.health_path=directory/'health.json'
        def start(self):
            self.health_path.write_text(json.dumps(dict(nonce=self.nonce,ready=True,monotonic=time.monotonic())));self.health_path.chmod(0o600)
            record('provider-start-simulated');return self
        def close(self):record('provider-close-simulated')
    def client_init(self,instance,runtime):self.instance=instance;self.runtime_dir=runtime;record('client-init-fake')
    def request(self,wire):
        if wire.startswith('/plugin load '):loaded[:]=[hyprland.OWNERSHIP_PLUGIN_NAME];record('plugin-load-spy');return b'ok'
        if wire.startswith('/plugin unload '):loaded.clear();record('plugin-unload-spy');return b'ok'
        raise AssertionError('unexpected external transport: '+wire)
    original=managed.run_pro2_test_managed
    def managed_entry(*a,**kw):
        record('managed-entry',scope_module=type(kw['test_scope']).__module__,client_module=type(a[0]).__module__)
        return original(*a,**kw)
    sys.argv=['-c',str(root),*args[4:]]
    try:
        with patch.object(ctypes,'CDLL',side_effect=AssertionError('real library load forbidden')), \
             patch.object(launch,'RuntimeSession',Provider), \
             patch.object(hyprland.HyprlandClient,'__init__',client_init), \
             patch.object(hyprland.HyprlandClient,'_request',request), \
             patch.object(hyprland.HyprlandClient,'plugins_loaded',lambda self:list(loaded)), \
             patch.object(hyprland.HyprlandClient,'park_presentation',lambda self,out:record('park-fake')), \
             patch.object(monitor_rules,'refuse_start',lambda client:None), \
             patch.object(managed,'run_pro2_test_managed',managed_entry):
            exec(compile(code,'<actual-packaged-entry>','exec'),globals())
    finally:
        Path(config['trace']).write_text(json.dumps(trace))


if __name__=='__main__':main()
