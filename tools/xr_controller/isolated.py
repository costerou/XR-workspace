"""Explicit disposable-guest controller. Requires BOTH one-run VM-isolated and P2-panel consent.

No normal-mode fallback, physical classification, fake detection or environment bypass.
The VM consent fixes the boot/session/runtime/pair/run id; the panel click fixes pins
and geometry. All VM proof and saved broker authority checks are the existing ones.
"""
import argparse
import copy
import json
import os
from pathlib import Path
import sys
import time

from tools.native_spike import isolated as vm
from tools.native_spike.hyprland import HyprlandClient
from tools.native_spike.ipc import private_dir, read_private_json
from tools.native_spike.panel_consent import load_panel_consent
from tools.native_spike.profiles import ProfileStore, profiles_path
from .__main__ import Host, Units, Store, isolated as command
from .install import load_installed, verify_pinned
from .runner import _write_result
from .server import serve
from .state import Controller


def load_vm(path):
    raw=read_private_json(path)
    return vm.load_isolated_consent(path,raw['instance'],raw['output'])


def authorize(path,panel,client):
    consent=load_vm(path)
    for key in ('run_id','instance','output','recovery_output','capture','allow','input','sdk','mode_switch'):
        if panel[key]!=consent[key]: raise ValueError('VM/panel scope mismatch: '+key)
    if panel['tracking']!=consent.get('tracking',False): raise ValueError('VM/panel tracking scope mismatch')
    vm.check_scope(client,consent,Path('/'))
    return consent


def bind_controller(controller,consent):
    def scope(scope):
        for key in ('instance','output','recovery_output'):
            if scope[key]!=consent[key]: raise ValueError('VM controller pair changed')
        scope.update(run_id=consent['run_id'],tracking=consent.get('tracking',False),mode_switch=False)
        return scope
    controller.scope_transform=scope


class GuestHost(Host):
    def __init__(self,runtime,state,data,path):
        super().__init__(runtime,state,data)
        self.path=Path(path); self.vm=load_vm(path)
        self.client=HyprlandClient(self.vm['instance'],self.runtime)
        vm.check_scope(self.client,self.vm,Path('/'))

    def probe(self):
        # No USB impersonation: explicit guest-only virtual pair, labelled unknown.
        vm.check_scope(self.client,self.vm,Path('/'))
        vm.validate_virtual_outputs(self.client,self.vm['session'],(self.vm['output'],self.vm['recovery_output']))
        result=super().probe()
        if any(j['run_id']!=self.vm['run_id'] for j in result['journals']):
            raise ValueError('foreign run in guest runtime')
        result.update(model='unknown',output=self.vm['output'],reason='isolated VM; virtual outputs, no glasses')
        return result

    def spawn(self,action,consent,journal):
        import subprocess
        raw=read_private_json(consent)
        authorize(self.path,raw,self.client)
        args=command('tools.xr_controller.isolated',action,'--vm-consent',self.path,'--consent',consent,'--state-dir',self.state_dir)
        if journal: args+=['--journal',journal]
        subprocess.run(['systemd-run','--user','--unit=viture-xr-'+self.vm['run_id'],'--collect',
            '--property=WorkingDirectory='+str(Path(__file__).resolve().parents[2]),
            *['--setenv='+k+'='+os.environ[k] for k in ('XDG_RUNTIME_DIR','XDG_DATA_HOME','XDG_CONFIG_HOME','XDG_STATE_HOME','HYPRLAND_INSTANCE_SIGNATURE') if k in os.environ],*args],check=True,timeout=5)


def execute(action,path,panel_path,state,journal=None):
    consent=load_vm(path)  # includes forbidden-instance rejection BEFORE client construction
    panel=load_panel_consent(panel_path,consent['instance'],consent['output'])
    client=HyprlandClient(consent['instance'],Path(consent['runtime']))
    authorize(path,panel,client)  # guest kernel proof before any plugin operation
    pins=load_installed(Path(os.environ['XDG_DATA_HOME']))
    for k in ('plugin','renderer'):
        if panel[k]!=pins[k]: raise ValueError('panel pin mismatch')
        verify_pinned(pins[k])
    plugin=pins['plugin']['path']; loaded=False
    try:
        if action=='start':
            if client.plugins_loaded(): raise ValueError('ownership plugin already loaded')
            client.plugin_load(plugin); loaded=True
            if client.plugins_loaded()!=['xr-ownership']: raise ValueError('plugin load not confirmed')
            result=vm.run_isolated(client,consent,Path(consent['runtime']),desktops=panel['desktops'],
                store=ProfileStore(profiles_path(),panel['model']),renderer=pins['renderer']['path'])
        else:
            if client.plugins_loaded()!=['xr-ownership']: raise ValueError('saved broker unavailable')
            result=vm.recover_isolated(client,consent,Path(journal))
        # Preserve broker epoch if recovery is incomplete. Never unload that authority.
        if result['complete']:
            client.plugin_unload(plugin)
            if client.plugins_loaded(): raise ValueError('plugin unload not confirmed')
        _write_result(state,consent['run_id']+'.result.json',dict(result,run_id=consent['run_id']))
        return 0 if result['complete'] else 2
    except BaseException:
        # No journal means no saved authority to preserve. A crashed runner otherwise
        # deliberately leaves the broker loaded for explicit isolated recovery.
        canonical=Path(consent['runtime'])/'xr-workspace-spike'/consent['run_id']/'journal.json'
        if loaded and not canonical.exists(): client.plugin_unload(plugin)
        raise


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('action',choices=('serve','start','recover'))
    ap.add_argument('--vm-consent',required=True,type=Path)
    ap.add_argument('--consent',type=Path); ap.add_argument('--state-dir',type=Path); ap.add_argument('--journal',type=Path)
    args=ap.parse_args(argv)
    consent=load_vm(args.vm_consent)
    if args.action!='serve':
        if not args.consent or not args.state_dir or (args.action=='recover' and not args.journal): ap.error('missing run arguments')
        return execute(args.action,args.vm_consent,args.consent,args.state_dir,args.journal)
    runtime=Path(os.environ['XDG_RUNTIME_DIR'])
    if runtime!=Path(consent['runtime']) or os.environ.get('HYPRLAND_INSTANCE_SIGNATURE')!=consent['instance']:
        raise ValueError('guest controller environment mismatch')
    state=Path(os.environ['XDG_STATE_HOME'])/'viture-workspace'
    fd=private_dir(state,create=True)
    import fcntl
    lock=os.open('controller.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600,dir_fd=fd)
    try:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        host=GuestHost(runtime,state,Path(os.environ['XDG_DATA_HOME']),args.vm_consent)
        controller=Controller(time.monotonic,host.probe,host.spawn,Units(runtime),Store(),fd,state)
        bind_controller(controller,consent)
        socket=runtime/'viture-workspace/controller.sock'
        os.close(private_dir(socket.parent,create=True))
        if socket.exists():
            import stat
            st=socket.lstat()
            if not stat.S_ISSOCK(st.st_mode) or st.st_uid!=os.getuid(): raise ValueError('unsafe controller socket')
            socket.unlink()
        serve(controller,socket)
    finally: os.close(lock); os.close(fd)
    return 0


if __name__=='__main__': sys.exit(main())
