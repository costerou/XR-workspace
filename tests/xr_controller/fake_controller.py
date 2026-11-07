"""Offline canned panel socket and render harness. Never probes hardware or runs units.

python -m tests.xr_controller.fake_controller --render /tmp/xr-panel-renders
python -m tests.xr_controller.fake_controller --socket /tmp/controller.sock --state idle
Screenshots are offline render evidence, not proof of lifecycle behaviour.
"""
import argparse
import copy
import json
import os
from pathlib import Path
import shutil
import socketserver
import subprocess
import tempfile
import threading

from tools.native_spike.profiles import DEFAULT_PROFILE

STATES = ('no-glasses', 'idle', 'confirming', 'starting', 'running', 'stopping',
          'recovery-needed', 'recover-confirming', 'cli-recovery', 'external-run', 'failed',
          'idle-editor', 'beast-confirming', 'running-unmeasured', 'disconnected',
          'bar-idle', 'bar-running', 'bar-recovery-needed')


def fixture(state):
    original=state
    state=state.removeprefix('bar-')
    state={'idle-editor':'idle','beast-confirming':'confirming','running-unmeasured':'running'}.get(state,state)
    profile = copy.deepcopy(DEFAULT_PROFILE)
    profile['desktops'] = [dict(width=1920,height=1080,scale=1),
                           dict(width=3840,height=1080,scale=1),
                           dict(width=3440,height=1440,scale=1.25)]
    # A wraparound arrangement: outer desktops angled in, the middle ultrawide curved.
    profile['placements'] = [dict(x=0,y=0,distance=100,curvature=0),
                             dict(x=0,y=0,distance=100,curvature=60),
                             dict(x=0,y=10,distance=120,curvature=0)]
    scope = dict(run_id='offline', model='luma-ultra', output='DP-2', recovery_output='DP-1',
                 desktops=profile['desktops'], tracking='xrlinuxdriver',
                 mode_switch=dict(width=1920,height=1200,refresh_hz=120))
    snap = dict(state='confirming' if state=='recover-confirming' else state,
                model='luma-ultra', output='DP-2', reason=None, fallback='DP-1',
                fallback_candidates=['DP-1','HDMI-A-1'], status={}, capabilities=[],
                tracking_drivers=[dict(key='xrdriver',label='Standard',path='/usr/bin/xrDriver',sha256=None,available=True),
                                  dict(key='3dof',label='3DoF test',path='/offline/xrDriver',sha256='a'*64,available=True),
                                  dict(key='luma',label='New driver',path='/offline/xr-luma-driver',sha256='b'*64,available=True)])
    if state == 'no-glasses': snap.update(model=None,output=None)
    if state in ('confirming','recover-confirming'):
        snap['pending'] = dict(action='recover' if state=='recover-confirming' else 'start',scope=scope,scope_id='offline-scope')
    if state == 'running':
        snap.update(desktops=profile['desktops'], placements=profile['placements'], view=dict(selected=1,mode='anchor',spacing=40,prediction=True),
                    display_mode='1920×1200@120',uptime=840,
                    capabilities=['prev','next','zoom_out','zoom_in','recenter','mode_next','spacing_down','spacing_up','prediction'],
                    status=dict(presented_hz=119.8,missed=3,capture_path='dmabuf',pose_age_ms=4))
    if state == 'failed': snap['reason']='Pinned binary changed; reinstall before starting.'
    if state in ('cli-recovery','external-run'): snap['journal']='/scratch/xr-workspace-spike/cli-run/journal.json'
    if original=='beast-confirming':
        snap['model']='beast'; scope.update(model='beast',mode_switch=False)
    if original=='running-unmeasured': snap['status']={}
    return snap, profile, scope


class FakeServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    def __init__(self, path, state):
        self.snapshot, self.profile, self.scope = fixture(state)
        self.requests = []
        super().__init__(str(path), Handler)


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        while line := self.rfile.readline(16385):
            msg=json.loads(line)
            self.server.requests.append(msg)
            op=msg.get('op'); reply=dict(self.server.snapshot,ok=True)
            if op=='get_settings': reply['settings']=self.server.profile
            elif op=='set_settings': self.server.profile.update(msg['settings'])
            elif op in ('begin_start','begin_recover'):
                pending=dict(action='start' if op=='begin_start' else 'recover',scope=self.server.scope,scope_id='offline-scope')
                self.server.snapshot.update(state='confirming',pending=pending); reply.update(pending)
            elif op=='cancel': self.server.snapshot.update(state='idle',pending=None)
            elif op in ('confirm_start','confirm_recover'): self.server.snapshot.update(state='starting',pending=None)
            elif op=='stop': self.server.snapshot['state']='stopping'
            self.wfile.write((json.dumps(dict(reply,schema=1,request_id=msg['request_id']))+'\n').encode())
            if op not in ('status','get_settings'):
                self.wfile.write((json.dumps(dict(self.server.snapshot,schema=1,event='state'))+'\n').encode())
            self.wfile.flush()


def render(destination, states=STATES):
    destination=Path(destination).resolve(); destination.mkdir(parents=True,exist_ok=True)
    repo=Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory(prefix='xr-panel-offline-') as tmp:
        root=Path(tmp); plugin=root/'plugin'
        shutil.copytree(repo/'omarchy-plugin/cody.viture-workspace',plugin)
        # Quickshell's qs import is the scratch config root. Use the installed shell's
        # actual read-only components, while HOME/XDG paths all point to scratch.
        for name in ('Commons','Ui'):
            (root/name).symlink_to(Path('/usr/share/omarchy/shell')/name)
        theme=root/'.local/state/omarchy/current/theme'
        theme.mkdir(parents=True)
        (theme/'colors.toml').write_text('foreground = "#e7eeea"\nbackground = "#1b1f1f"\naccent = "#b9e78a"\n')
        environment=dict(os.environ,HOME=tmp,XDG_RUNTIME_DIR=tmp,XDG_CONFIG_HOME=tmp,
                         XDG_STATE_HOME=tmp,XDG_CACHE_HOME=tmp,QT_QPA_PLATFORM='offscreen',
                         QT_QPA_PLATFORMTHEME='',QT_QUICK_CONTROLS_STYLE='Basic',QSG_RHI_BACKEND='software',QT_QUICK_BACKEND='software')
        environment.pop('WAYLAND_DISPLAY',None); environment.pop('DISPLAY',None)
        for state in states:
            path=root/'controller.sock'
            server=FakeServer(path,state)
            worker=threading.Thread(target=server.serve_forever,daemon=True); worker.start()
            png=destination/(state+'.png')
            source='''import QtQuick
import Quickshell
import "plugin" as Plugin
ShellRoot {
 Plugin.Panel { id:panel; socketPath: "SOCKET"; Component.onCompleted: open({desktop:DESKTOP}) }
 Timer { interval:1100; running:true; onTriggered: {
   panel.renderSurface.grabToImage(function(result) { if (!result.saveToFile("PNG")) console.error("SAVE FAILED"); Qt.quit(); });
 } }
 Timer { interval:5000; running:true; onTriggered: { console.error("RENDER TIMEOUT"); Qt.quit(); } }
}
'''.replace('SOCKET',str(path)).replace('PNG',str(png)).replace('DESKTOP','1' if state=='idle-editor' else '-1')
            if state=='disconnected': source=source.replace(str(path),str(root/'absent.sock'))
            if state.startswith('bar-'):
                source=source.replace('Plugin.Panel { id:panel; socketPath: "'+str(path)+'"; Component.onCompleted: open({desktop:-1}) }', '''FloatingWindow { implicitWidth:100; implicitHeight:40; color: "#1b1f1f"
                    Rectangle { id:panel; property alias renderSurface:bar; anchors.fill:parent; color: "#1b1f1f"
                        Plugin.BarWidget { id:bar; socketPath: "SOCKET"; anchors.centerIn:parent; width:80; height:36 }
                    }
                }'''.replace('SOCKET',str(path)))
            (root/'shell.qml').write_text(source)
            try:
                result=subprocess.run(['quickshell','-p',str(root/'shell.qml')],env=environment,capture_output=True,text=True,timeout=10)
                log=result.stdout+result.stderr
                (destination/(state+'.log')).write_text(log)
                if result.returncode or not png.exists() or any(x in log for x in ('TypeError','ReferenceError','is not a type','Cannot assign','RENDER TIMEOUT')):
                    raise RuntimeError(state+': '+log)
            finally:
                server.shutdown(); server.server_close(); worker.join(); path.unlink(missing_ok=True)
            unexpected=[m for m in server.requests if m.get('op') not in ('status','get_settings')]
            if unexpected: raise AssertionError('render caused mutation: '+repr(unexpected))
            print('offline render:',state,png,flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--render-state',choices=STATES); parser.add_argument('--render'); parser.add_argument('--socket'); parser.add_argument('--state',choices=STATES,default='idle')
    args=parser.parse_args()
    if args.render: render(args.render, [args.render_state] if args.render_state else STATES)
    elif args.socket:
        with FakeServer(args.socket,args.state) as server: server.serve_forever()
    else: parser.error('--render or --socket required')
