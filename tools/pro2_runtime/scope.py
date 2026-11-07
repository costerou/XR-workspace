"""Canonical supervised-scope/client types, stable across CLI and spawn workers."""
from dataclasses import dataclass
from pathlib import Path
import time
from tools.native_spike.ipc import read_private_json,strict_json
from tools.native_spike.hyprland import HyprlandClient
from .launch import validate_projection

SMOOTH_MS=35.0  # motion_smooth.hpp tau, checked by tools/pro2_runtime/smooth_acceptance.py SHIPPED and tests

@dataclass
class TestScope:
    prefix:str
    projection:float
    health_path:str
    nonce:str
    smooth_ms:float=SMOOTH_MS
    fixed_desktops:bool=True  # a supervised test keeps exactly the desktops its consent names; a panel session may change them
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


class BundleClient(HyprlandClient):
    def __init__(self,instance,runtime,root,manifest):
        super().__init__(instance,runtime);self.bundle_root=root;self.bundle_manifest=manifest
    def check_abi(self):
        """The plugin was built for one Hyprland: the running one, or no start."""
        version=strict_json(self._request('j/version'))  # not in the client's query list: it is only asked here
        if [version.get('version'),version.get('buildAquamarine')]!=self.bundle_manifest.get('ownership_abi'):raise ValueError('the bundle was built for another Hyprland')
    def _plugin_command(self,verb,path,data_home=None,recovery_pin=None):
        from tools.pro2_runtime.launch import validate_bundle_plugin
        if verb not in ('load','unload'):raise ValueError('supervised plugin verb required')
        validate_bundle_plugin(self.bundle_root,self.bundle_manifest,path)
        if self._request(f'/plugin {verb} {path}').decode().strip()!='ok':raise ValueError('supervised plugin command rejected')

