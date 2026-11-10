"""Canonical supervised-scope/client types, stable across CLI and spawn workers."""
from dataclasses import dataclass
import os
from pathlib import Path
import stat
import time
from tools.native_spike.ipc import read_private_json,strict_json
from tools.native_spike.hyprland import HyprlandClient
from .launch import validate_projection

HEALTH_MAX_AGE=2.0  # seconds; the bridge itself stops on 100 ms of silence, so this only asks that its monitor is alive


def read_health(path):
    """read_private_json, for a file that is replaced 50 times a second. An open can land just before a replace, and the file it holds
    is then a whole, valid, unlinked one with no links left: nlink 0 is the old copy, not an attack. That refusal ended a guard."""
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as stream:
        st=os.fstat(stream.fileno())
        if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.getuid() or stat.S_IMODE(st.st_mode)!=0o600 or st.st_nlink>1:
            raise PermissionError('health must be owned regular single-link mode 0600')
        return strict_json(stream.read(4096))


SMOOTH_MS=35.0  # motion_smooth.hpp tau, checked by tools/pro2_runtime/smooth_acceptance.py SHIPPED and tests

@dataclass
class TestScope:
    prefix:str
    projection:float
    health_path:str
    nonce:str
    smooth_ms:float=SMOOTH_MS
    level_gravity:bool=False  # the comparison: level to the SDK's gravity at each recentre, in place of the mount pitch alone
    fixed_desktops:bool=True  # a supervised test keeps exactly the desktops its consent names; a panel session may change them
    display:object=None  # switch(on): the bridge's SDK puts the glasses in 3D or back in 2D at 120 Hz (RuntimeSession.set_display)
    def __getstate__(self):
        """The run guard's spawned worker gets a pickled copy (supervisor._start_guard): it checks health only, and the
        display switch (bound to the bridge session and its lock) stays in the run's process."""
        return {**self.__dict__,'display':None}
    def healthy(self):
        validate_projection(self.projection,True)
        data=read_health(self.health_path)
        age=time.monotonic()-data['monotonic']
        if data.get('nonce')!=self.nonce or data.get('ready') is not True or not 0<=age<=HEALTH_MAX_AGE:
            raise ValueError(f"Pro2 supervised provider stale/unready: age {age:.3f} s, {data.get('reason') or 'no reason given'}")

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

