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
from tools.native_spike import monitor_rules
from tools.native_spike.hyprland import HyprlandClient, ownership_plugin_count
from tools.native_spike.ipc import private_dir, read_private_json, check_peer, token, strict_json, MAX_FRAME
from tools.native_spike.profiles import load_profile, load_setup, save_profile, profiles_path
from tools.xr_probe.hardware import probe_usb, probe_drm
from .detect import identify
from .incident_monitor import HostSources, IncidentMonitor
from .incident_report import Reports, hook_path
from .install import install, load_installed, tracking_drivers
from .state import Controller
from .server import serve

REPO=Path(__file__).resolve().parents[2]


def isolated(module,*args):
    # -I excludes cwd, even with -m. Add this fixed checkout explicitly.
    return ['/usr/bin/python3','-I','-c',f'import sys,runpy; sys.path.insert(0,{str(REPO)!r}); runpy.run_module({module!r},run_name="__main__")',*map(str,args)]


class Store:
    def load(self,model): return load_profile(profiles_path(),model)
    def save(self,model,profile): save_profile(profiles_path(),model,profile)
    def setup(self,model): return load_setup(profiles_path(),model)  # what a start restores (strict; Controller.begin_start)


# What `systemctl is-active` prints, per unit. It exits 0 for a unit in ACTIVE_STATES; anything else is not alive.
ACTIVE_STATES=('active','reloading','refreshing')
UNIT_STATES=ACTIVE_STATES+('inactive','failed','activating','deactivating','maintenance')


class Units:
    def __init__(self,runtime): self.runtime=Path(runtime)
    def alive(self,run_id):
        return subprocess.run(['systemctl','--user','is-active','--quiet','viture-xr-'+token(run_id)],timeout=2).returncode==0
    def alive_many(self,run_ids):
        """{run_id: alive(run_id)} with one systemctl call for all of them (it prints each unit's state, one per line),
        not one per run: the probe asks this every second for every finished run still in the state directory. Any
        answer that is not exactly one known state per unit falls back to alive() for each."""
        units=['viture-xr-'+token(r) for r in run_ids]
        if len(units)<2: return {r:self.alive(r) for r in run_ids}
        done=subprocess.run(['systemctl','--user','is-active',*units],timeout=2,stdout=subprocess.PIPE,text=True)
        states=done.stdout.splitlines()
        if (len(states)!=len(units) or any(s not in UNIT_STATES for s in states)
                or (done.returncode==0)!=any(s in ACTIVE_STATES for s in states)):
            return {r:self.alive(r) for r in run_ids}
        return {r:s in ACTIVE_STATES for r,s in zip(run_ids,states)}
    def stop(self,run_id):
        # SIGINT reaches the runner's KeyboardInterrupt teardown, not its renderer child.
        subprocess.run(['systemctl','--user','kill','--kill-whom=main','--signal=SIGINT','viture-xr-'+token(run_id)],check=True,timeout=2)
    def control(self,run_id,session,op,payload=None):
        directory=self.runtime/'xr-workspace-spike'/token(run_id)
        fd=private_dir(directory)
        try:
            with socket.socket(socket.AF_UNIX) as conn:
                conn.settimeout(.25); conn.connect(f'/proc/self/fd/{fd}/control.sock'); check_peer(conn,os.getuid())
                if op is not None:
                    request_id=time.time_ns()
                    frame=dict(schema=1,request_id=request_id,run_id=run_id,session=token(session),op=op,payload=payload or {})
                    conn.sendall((json.dumps(frame,separators=(',',':'),allow_nan=False)+'\n').encode())
                    if op == 'status':
                        # The reply is one line (a few kB, every second while a run is up): read what has arrived, up to
                        # the newline, instead of one byte per call. Nothing after the line is used.
                        raw=bytearray(); deadline=time.monotonic()+.25
                        while len(raw)<MAX_FRAME:
                            remaining=deadline-time.monotonic()
                            if remaining<=0: return False
                            conn.settimeout(remaining)
                            chunk=conn.recv(MAX_FRAME-len(raw))
                            if not chunk: return False
                            end=chunk.find(b'\n')
                            raw.extend(chunk if end<0 else chunk[:end+1])
                            if end>=0:
                                reply=strict_json(raw)
                                if (not isinstance(reply,dict) or reply.get('schema')!=1 or reply.get('request_id')!=request_id
                                        or reply.get('run_id')!=run_id or reply.get('session')!=session): return False
                                return reply
                        return False
            return True
        except (OSError,ValueError): return False
        finally: os.close(fd)


class Host:
    def __init__(self,runtime,state_dir,data_home):
        self.runtime,self.state_dir,self.data_home=Path(runtime),Path(state_dir),Path(data_home)
    def probe(self):
        instance=os.environ.get('HYPRLAND_INSTANCE_SIGNATURE','')
        result=dict(model=None,output=None,reason=None,instance=instance,monitors=[],installed={},plugins=[],journals=[],external=[],
                    tracking_drivers=tracking_drivers(self.data_home))
        root=self.runtime/'xr-workspace-spike'
        if root.exists():
            for path in sorted(root.glob('*/journal.json')):
                data=read_private_json(path,limit=1048576)
                if data.get('run_id')!=path.parent.name: raise ValueError('journal identity mismatch')
                if not any(r.get('state')!='done' for r in data['operations']): continue
                # The controller writes a run's consent here before it spawns it, so that file is what makes a run its own. A run
                # started any other way (a supervised test, the CLI) shares this runtime folder and keeps its consent elsewhere.
                own=os.path.lexists(self.state_dir/(data['run_id']+'.json'))
                result['journals' if own else 'external'].append(dict(data,path=str(path)))
        # A controller restart can fall between launch and the first journal write,
        # or between completed recovery and plugin unload. Both still own a unit.
        known={j['run_id'] for j in result['journals']}
        runs=[]
        for path in sorted(self.state_dir.glob('*.json')):
            if path.name.endswith(('.result.json', '.progress.json', '.recovery.json')): continue
            # One unreadable consent costs that run its details (Controller._cli_recovery), not the controller its whole probe.
            try: raw=read_private_json(path)
            except (OSError,ValueError): continue
            run_id=raw.get('run_id')
            if run_id and path.name==run_id+'.json' and run_id not in known: runs.append(run_id)
        alive=Units(self.runtime).alive_many(runs) if runs else {}
        for run_id in runs:
            if alive[run_id]:
                phase=None
                try:
                    progress=read_private_json(self.state_dir/(run_id+'.progress.json'))
                    if progress.get('run_id')==run_id: phase=progress.get('phase')
                except FileNotFoundError: pass
                result['journals'].append(dict(run_id=run_id,session=None,path=None,operations=[],phase=phase))
        try:
            client=HyprlandClient(instance,self.runtime)
            monitors=client.query('monitors')
            result.update(identify(probe_usb(),probe_drm(),monitors),monitors=monitors,plugins=client.plugins_loaded())
            result['installed']=load_installed(self.data_home)
            result['tracking_drivers']=tracking_drivers(self.data_home,result['installed'])
        except (OSError,ValueError) as exc: result['reason']=str(exc)
        try: result['config_rules']=monitor_rules.leftovers()  # what Recover would take out; the box says so before the click
        except (OSError,ValueError): result['config_rules']=[]
        return result
    def spawn(self,action,consent,journal,rules=()):
        raw=read_private_json(consent); run_id=token(raw['run_id'])
        args=isolated('tools.xr_controller.runner','--consent',consent,'--state-dir',self.state_dir)
        if action=='recover':
            args=isolated('tools.xr_controller','recover-run','--consent',consent,'--journal',journal,*[a for name in rules for a in ('--clear-rule',name)])
        subprocess.run(['systemd-run','--user','--unit=viture-xr-'+run_id,'--collect',
            '--property=WorkingDirectory='+str(REPO),'--setenv=XDG_RUNTIME_DIR='+str(self.runtime),
            '--setenv=XDG_DATA_HOME='+str(self.data_home),
            *['--setenv='+key+'='+os.environ[key] for key in ('XDG_CONFIG_HOME','XDG_STATE_HOME','HYPRLAND_INSTANCE_SIGNATURE') if key in os.environ],*args],check=True,timeout=5)


def incident_monitor(runtime,state_dir,config_home):
    """The incident monitor (incident_monitor.py), started. Nothing about it may keep the controller from serving."""
    try:
        monitor=IncidentMonitor(HostSources(runtime,state_dir),Reports(Path(state_dir)/'incidents'),hook=hook_path(config_home))
        monitor.start()
        return monitor
    except Exception as exc:
        print(f'incident monitor not started: {exc}',file=sys.stderr)
        return None


MAX_REASONS=8  # of an unfinished Recover, in its saved report: one per output or window at most


def report_recovery(consent_path,reasons,remaining=()):
    """Where `<run_id>.recovery.json` comes from (the consent names the run and sits in the state directory): an unfinished
    Recover says which journal steps are left (`remaining`, none for a refusal before the journal was read) and why, on stderr
    and for the panel and the incident report. A finished one leaves no such file, and the run's result stops saying it
    failed: the panel would go on showing that after the desktop was back."""
    from .runner import _write_result
    state,run_id=Path(consent_path).parent,Path(consent_path).stem
    if reasons:
        if remaining: print('recovery incomplete: remaining: '+', '.join(remaining),file=sys.stderr)
        for reason in reasons: print('recovery incomplete: '+reason,file=sys.stderr)
        _write_result(state,run_id+'.recovery.json',dict(run_id=run_id,complete=False,remaining=list(remaining),reasons=reasons[:MAX_REASONS],at=time.time()))
        return
    (state/(run_id+'.recovery.json')).unlink(missing_ok=True)
    try: result=read_private_json(state/(run_id+'.result.json'))
    except FileNotFoundError: return
    if result.get('run_id')==run_id:
        result.pop('recovery',None)
        _write_result(state,run_id+'.result.json',dict(result,complete=True,remaining=[],error=None))


def recover_command(consent_path,journal,clear_rules=()):
    """`recover-run`: 0 once the run is cleaned up, else 2, always saying why."""
    remaining=[]
    try:
        result=recover_pinned(consent_path,journal,clear_rules)
        remaining=result.get('remaining') or []
        code,reasons=(0,[]) if result['complete'] else (2,result.get('reasons') or ['recovery did not finish, and nothing said why'])
    except (OSError,ValueError,KeyError,TypeError) as exc:
        code,reasons=2,[str(exc) or type(exc).__name__]
    try: report_recovery(consent_path,reasons,remaining)
    except (OSError,ValueError) as exc: print(f'recovery report not written: {exc}',file=sys.stderr)
    return code


def recover_run(consent_path,journal):
    return 0 if recover_pinned(consent_path,journal)['complete'] else 2


def recover_pinned(consent_path,journal,clear_rules=()):
    from tools.native_spike.panel_consent import load_panel_consent
    from tools.native_spike.managed import recover_managed
    from .install import verify_recovery_pin
    raw=read_private_json(consent_path)
    consent=load_panel_consent(consent_path,raw['instance'],raw['output'])
    data_home=Path(os.environ.get('XDG_DATA_HOME',Path.home()/'.local/share'))
    # Recover with the plugin this run pinned, even if an install has since upgraded
    # installed.json: that older broker is the one still loaded and holding the journal's state.
    plugin=consent['plugin']
    try: verify_recovery_pin(plugin,data_home)
    except ValueError as exc: raise ValueError(f'recovery plugin pin unavailable: {exc}') from None
    client=HyprlandClient(consent['instance'],Path(os.environ['XDG_RUNTIME_DIR']))
    # Recover only the consented broker; never load a replacement with a new epoch.
    if ownership_plugin_count(client.plugins_loaded())!=1: raise ValueError('consented ownership plugin unavailable for recovery')
    # `clear_rules`: what the confirm box listed and the click agreed to (state.py begin_recover); nothing else is edited.
    result=recover_managed(client,consent,Path(journal),clear_rules=clear_rules)
    if result['complete']:
        client.plugin_unload(plugin['path'],data_home=data_home,recovery_pin=plugin)
        if ownership_plugin_count(client.plugins_loaded()): raise ValueError('plugin unload not confirmed')
    return result


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
    p.add_argument('--clear-rule',action='append',default=[],type=monitor_rules.desktop,help='a desktop whose monitors.lua rule the confirm box listed')
    args=ap.parse_args(argv)
    data=Path(os.environ.get('XDG_DATA_HOME',Path.home()/'.local/share'))
    if args.command=='install':
        try:
            entry=install(args.build,data)
            install_unit(os.environ.get('XDG_CONFIG_HOME') or Path.home()/'.config')
            print(json.dumps(entry,indent=2)); return 0
        except (OSError,ValueError,subprocess.SubprocessError) as exc:
            print(f'install failed: {exc}',file=sys.stderr); return 1
    if args.command=='recover-run': return recover_command(args.consent,args.journal,args.clear_rule)
    runtime=Path(os.environ['XDG_RUNTIME_DIR'])
    base=Path(os.environ.get('XDG_STATE_HOME',Path.home()/'.local/state'))
    base.mkdir(parents=True,exist_ok=True)
    state=base/'viture-workspace'; fd=private_dir(state,create=True)
    import fcntl
    lock=os.open('controller.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600,dir_fd=fd)
    try:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        host=Host(runtime,state,data)
        monitor=incident_monitor(runtime,state,os.environ.get('XDG_CONFIG_HOME') or Path.home()/'.config')
        controller=Controller(time.monotonic,host.probe,host.spawn,Units(runtime),Store(),fd,state,monitor=monitor)
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
