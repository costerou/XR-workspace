"""Controller CLI and concrete offline-testable host adapters.

Only explicit `serve` probes the session. Only confirmed requests spawn a run.
`install` installs pins and a disabled unit; it never enables or starts it.
"""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from tools.native_spike.hyprland import HyprlandClient
from tools.native_spike.ipc import private_dir, read_private_json, check_peer, token
from tools.native_spike.profiles import load_profile, save_profile, profiles_path
from tools.xr_probe.hardware import probe_usb, probe_drm
from .detect import identify
from .install import install, load_installed
from .state import Controller
from .server import serve

REPO=Path(__file__).resolve().parents[2]


def isolated(module,*args):
    # -I excludes cwd, even with -m. Add this fixed checkout explicitly.
    return ['/usr/bin/python3','-I','-c',f'import sys,runpy; sys.path.insert(0,{str(REPO)!r}); runpy.run_module({module!r},run_name="__main__")',*map(str,args)]


class Store:
    def load(self,model): return load_profile(profiles_path(),model)
    def save(self,model,profile): save_profile(profiles_path(),model,profile)


class Units:
    def __init__(self,runtime): self.runtime=Path(runtime)
    def alive(self,run_id):
        return subprocess.run(['systemctl','--user','is-active','--quiet','viture-xr-'+token(run_id)],timeout=2).returncode==0
    def stop(self,run_id):
        # SIGINT reaches the runner's KeyboardInterrupt teardown, not its renderer child.
        subprocess.run(['systemctl','--user','kill','--kill-whom=main','--signal=SIGINT','viture-xr-'+token(run_id)],check=True,timeout=2)
    def control(self,run_id,session,op):
        directory=self.runtime/'xr-workspace-spike'/token(run_id)
        fd=private_dir(directory)
        try:
            with socket.socket(socket.AF_UNIX) as conn:
                conn.settimeout(.25); conn.connect(f'/proc/self/fd/{fd}/control.sock'); check_peer(conn,os.getuid())
                if op is not None:
                    conn.sendall((json.dumps(dict(schema=1,request_id=time.time_ns(),run_id=run_id,session=token(session),op=op,payload={}))+'\n').encode())
            return True
        except OSError: return False
        finally: os.close(fd)


class Host:
    def __init__(self,runtime,state_dir,data_home):
        self.runtime,self.state_dir,self.data_home=Path(runtime),Path(state_dir),Path(data_home)
    def probe(self):
        instance=os.environ.get('HYPRLAND_INSTANCE_SIGNATURE','')
        result=dict(model=None,output=None,reason=None,instance=instance,monitors=[],installed={},plugins=[],journals=[])
        root=self.runtime/'xr-workspace-spike'
        if root.exists():
            for path in sorted(root.glob('*/journal.json')):
                data=read_private_json(path,limit=1048576)
                if data.get('run_id')!=path.parent.name: raise ValueError('journal identity mismatch')
                if any(r.get('state')!='done' for r in data['operations']): result['journals'].append(dict(data,path=str(path)))
        # A controller restart can fall between launch and the first journal write,
        # or between completed recovery and plugin unload. Both still own a unit.
        known={j['run_id'] for j in result['journals']}
        for path in sorted(self.state_dir.glob('*.json')):
            if path.name.endswith('.result.json'): continue
            raw=read_private_json(path)
            run_id=raw.get('run_id')
            if run_id and path.name==run_id+'.json' and run_id not in known and Units(self.runtime).alive(run_id):
                result['journals'].append(dict(run_id=run_id,session=None,path=None,operations=[]))
        try:
            client=HyprlandClient(instance,self.runtime)
            monitors=client.query('monitors')
            result.update(identify(probe_usb(),probe_drm(),monitors),monitors=monitors,plugins=client.plugins_loaded())
            result['installed']=load_installed(self.data_home)
        except (OSError,ValueError) as exc: result['reason']=str(exc)
        return result
    def spawn(self,action,consent,journal):
        raw=read_private_json(consent); run_id=token(raw['run_id'])
        args=isolated('tools.xr_controller.runner','--consent',consent,'--state-dir',self.state_dir)
        if action=='recover': args=isolated('tools.xr_controller','recover-run','--consent',consent,'--journal',journal)
        subprocess.run(['systemd-run','--user','--unit=viture-xr-'+run_id,'--collect',
            '--property=WorkingDirectory='+str(REPO),'--setenv=XDG_RUNTIME_DIR='+str(self.runtime),
            '--setenv=XDG_DATA_HOME='+str(self.data_home),
            *['--setenv='+key+'='+os.environ[key] for key in ('XDG_CONFIG_HOME','XDG_STATE_HOME','HYPRLAND_INSTANCE_SIGNATURE') if key in os.environ],*args],check=True,timeout=5)


def recover_run(consent_path,journal):
    from tools.native_spike.panel_consent import load_panel_consent
    from tools.native_spike.managed import recover_managed
    from .install import verify_pinned
    raw=read_private_json(consent_path)
    consent=load_panel_consent(consent_path,raw['instance'],raw['output'])
    data_home=Path(os.environ.get('XDG_DATA_HOME',Path.home()/'.local/share'))
    pinned=load_installed(data_home)
    if pinned['plugin']!=consent['plugin']: raise ValueError('recovery plugin pin mismatch')
    verify_pinned(pinned['plugin'])
    client=HyprlandClient(consent['instance'],Path(os.environ['XDG_RUNTIME_DIR']))
    # Recover only the consented broker; never load a replacement with a new epoch.
    if client.plugins_loaded()!=['xr-ownership']: raise ValueError('consented ownership plugin unavailable for recovery')
    result=recover_managed(client,consent,Path(journal))
    if result['complete']:
        client.plugin_unload(pinned['plugin']['path'],data_home=data_home)
        if 'xr-ownership' in client.plugins_loaded(): raise ValueError('plugin unload not confirmed')
    return 0 if result['complete'] else 2


def install_unit(config_home):
    target=Path(config_home)/'systemd/user'
    target.mkdir(parents=True,exist_ok=True)
    template=(REPO/'packaging/viture-workspace-controller.service').read_text()
    # systemd quoting uses double quotes; Python code contains only fixed repo/module paths.
    def quote(s): return '"'+s.replace('\\','\\\\').replace('"','\\"').replace('%','%%')+'"'
    text=template.replace('@REPO@',str(REPO).replace('%','%%')).replace('@EXEC@',' '.join(quote(a) for a in isolated('tools.xr_controller','serve')))
    from .install import _atomic_write
    _atomic_write(target/'viture-workspace-controller.service',text.encode(),0o644)
    subprocess.run(['systemctl','--user','daemon-reload'],check=True,timeout=5)


def main(argv=None):
    ap=argparse.ArgumentParser(); sub=ap.add_subparsers(dest='command',required=True)
    sub.add_parser('serve'); p=sub.add_parser('install'); p.add_argument('--build',type=Path,required=True)
    p=sub.add_parser('recover-run'); p.add_argument('--consent',required=True); p.add_argument('--journal',required=True)
    args=ap.parse_args(argv)
    data=Path(os.environ.get('XDG_DATA_HOME',Path.home()/'.local/share'))
    if args.command=='install':
        try:
            entry=install(args.build,data)
            install_unit(os.environ.get('XDG_CONFIG_HOME') or Path.home()/'.config')
            print(json.dumps(entry,indent=2)); return 0
        except (OSError,ValueError,subprocess.SubprocessError) as exc:
            print(f'install failed: {exc}',file=sys.stderr); return 1
    if args.command=='recover-run': return recover_run(args.consent,args.journal)
    runtime=Path(os.environ['XDG_RUNTIME_DIR'])
    base=Path(os.environ.get('XDG_STATE_HOME',Path.home()/'.local/state'))
    base.mkdir(parents=True,exist_ok=True)
    state=base/'viture-workspace'; fd=private_dir(state,create=True)
    import fcntl
    lock=os.open('controller.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600,dir_fd=fd)
    try:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        host=Host(runtime,state,data)
        controller=Controller(time.monotonic,host.probe,host.spawn,Units(runtime),Store(),fd,state)
        socket_path=runtime/'viture-workspace/controller.sock'
        directory=private_dir(socket_path.parent,create=True)
        try:
            try:
                import stat
                st=os.stat('controller.sock',dir_fd=directory,follow_symlinks=False)
                if not stat.S_ISSOCK(st.st_mode) or st.st_uid!=os.getuid(): raise ValueError('unsafe stale controller socket')
                # Lifetime lock excludes another controller; only unlink the known socket.
                os.unlink('controller.sock',dir_fd=directory)
            except FileNotFoundError: pass
        finally: os.close(directory)
        serve(controller,socket_path)
    finally: os.close(lock); os.close(fd)
    return 0


if __name__=='__main__': sys.exit(main())
