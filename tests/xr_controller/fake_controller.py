"""Offline canned panel socket and render harness. Never probes hardware or runs units.

python -m tests.xr_controller.fake_controller --render /tmp/xr-panel-renders
python -m tests.xr_controller.fake_controller --socket /tmp/controller.sock --state idle
Screenshots are offline render evidence, not proof of lifecycle behaviour.
"""
import argparse
import copy
import json
import os
import re
from pathlib import Path
import shutil
import socketserver
import subprocess
import tempfile
import threading

from tools.native_spike.profiles import DEFAULT_PROFILE

STATES = ('no-glasses', 'idle', 'confirming', 'starting', 'running', 'stopping',
          'recovery-needed', 'recover-confirming', 'cli-recovery', 'external-run', 'failed',
          'idle-editor', 'beast-confirming', 'running-unmeasured', 'running-problem', 'disconnected',
          # The pre-start pages (docs/mockups/plugin-prestart): Home's blockers, Setup per model, Confirm, Progress.
          'pro2-ready', 'choose-fallback', 'no-fallback', 'pro2-no-bridge', 'multiple-glasses', 'connector',
          'driver-missing', 'refused', 'service-failed', 'setup-pro2', 'setup-beast', 'pro2-confirming',
          'confirm-expired', 'recovery-unfinished',
          'bar-idle', 'bar-running', 'bar-recovery-needed', 'bar-no-glasses', 'bar-external-run', 'bar-disconnected')
# What the panel is opened with, and anything set on it before the grab (an error a refused request leaves).
OPEN = {'idle-editor': '{desktop:1}', 'setup-pro2': '{desktop:1}'}
PANEL_SETUP = {'refused': 'panel.error="busy";', 'setup-beast': 'panel.setupOpen=true;',
               'service-failed': 'panel.serviceTried=true; panel.serviceError="Job for viture-workspace-controller.service failed because the control process exited with error code.";'}


def fixture(state):
    original=state
    state=state.removeprefix('bar-')
    state={'idle-editor':'idle','beast-confirming':'confirming','running-unmeasured':'running','running-problem':'running',
           'pro2-ready':'idle','choose-fallback':'idle','no-fallback':'idle','pro2-no-bridge':'idle','multiple-glasses':'idle',
           'connector':'idle','driver-missing':'idle','refused':'idle','service-failed':'disconnected','setup-pro2':'idle',
           'setup-beast':'idle','pro2-confirming':'confirming','confirm-expired':'confirming',
           'recovery-unfinished':'recovery-needed'}.get(state,state)
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
                tracking_in_use=dict(key='luma',label='XR Luma driver',available=True,note=None))
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
    if original=='running-problem':  # the glasses' picture may be unusable: the panel is the way out
        snap.update(reason='head tracking lost; the session stops by itself after 60 s without it',
                    status=dict(presented_hz=0,missed=212,tracking=False),
                    incident=dict(summary='head tracking stopped; XR stopped and your windows came back', at='2026-11-09T14:02:00'))
    if state == 'starting': snap.update(run_id='offline', start_steps=['verify','tracking','plugin','desktops'], start_phase='tracking')
    if state == 'failed':
        snap.update(run_id='offline', reason='head tracking did not calibrate in 30 seconds; XR was not started and your windows stayed where they are')
    if original == 'recovery-unfinished':
        snap['reason']='Recover could not finish. XR-1 is still named by a monitor rule in your Hyprland monitors.lua.'
    if original in ('pro2-ready','setup-pro2','pro2-confirming','pro2-no-bridge'):
        snap.update(model='pro2', output='DP-3', fallback_candidates=['DP-1'],
                    tracking_in_use=dict(key='pro2-sdk',label='Pro 2 SDK bridge',available=True,note=None),
                    pro2=dict(projection=46.5,mount_pitch_deg=-8.5,pose_rate_hz=60,choices=[40,46.5,52],label='Pro 2 SDK bridge'))
        profile['desktops']=[dict(width=2560,height=1080,scale=1),dict(width=1920,height=1080,scale=1)]
        profile['placements']=profile['placements'][:2]
        profile['stabilization']=60
        scope.update(model='pro2',output='DP-3',desktops=profile['desktops'],tracking='pro2-sdk',mode_switch=False,
                     pro2_route=dict(projection=46.5),sbs_mode=dict(width=3840,height=1080,refresh_hz=[60]))
    if original == 'pro2-ready': snap['incident']=dict(summary='head tracking stopped; XR stopped and your windows came back', at='2026-11-03T14:02:00')
    if original == 'pro2-no-bridge':
        snap.update(output=None, reason='the Pro 2 bridge bundle is not installed (see docs/pro2/bring-up-runbook.md)'); snap.pop('pro2')
    if original == 'choose-fallback': snap['fallback']='choose'
    if original == 'no-fallback': snap.update(fallback=None, fallback_candidates=[])
    if original == 'multiple-glasses': snap.update(model='unknown', output=None, reason='multiple VITURE devices')
    if original == 'connector': snap.update(output=None, reason='presentation connector is not unambiguous')
    if original == 'driver-missing': snap['tracking_in_use']=dict(key='luma',label='XR Luma driver',available=False,note='Not installed')
    if original == 'setup-beast':
        snap.update(model='beast', fallback_candidates=['DP-1'],
                    tracking_in_use=dict(key='xrdriver',label='XRLinuxDriver',available=True,note=None))
        profile['desktops']=[dict(width=3440,height=1440,scale=1),dict(width=2560,height=1440,scale=1.25),dict(width=2560,height=1440,scale=1.25)]
        profile['stabilization']=0
    if original == 'confirm-expired': snap.pop('pending',None)
    if original == 'confirming': scope['sbs_mode']=dict(width=3840,height=1200,refresh_hz=[90,60])
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
        (theme/'colors.toml').write_text('foreground = "#e7eeea"\nbackground = "#1b1f1f"\naccent = "#b9e78a"\ncolor8 = "#707880"\n')
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
 Plugin.Panel { id:panel; socketPath: "SOCKET"; Component.onCompleted: { open(OPENED); SETUP } }
 Timer { interval:1100; running:true; onTriggered: {
   panel.renderSurface.grabToImage(function(result) { if (!result.saveToFile("PNG")) console.error("SAVE FAILED"); Qt.quit(); });
 } }
 Timer { interval:5000; running:true; onTriggered: { console.error("RENDER TIMEOUT"); Qt.quit(); } }
}
'''.replace('SOCKET',str(path)).replace('PNG',str(png)).replace('OPENED',OPEN.get(state,'{desktop:-1}')).replace('SETUP',PANEL_SETUP.get(state,''))
            if fixture(state)[0]['state']=='disconnected': source=source.replace(str(path),str(root/'absent.sock'))
            if state.startswith('bar-'):
                source=re.sub(r'Plugin\.Panel \{[^\n]*\}\n', lambda m: '''FloatingWindow { implicitWidth:100; implicitHeight:40; color: "#1b1f1f"
                    Rectangle { id:panel; property alias renderSurface:bar; anchors.fill:parent; color: "#1b1f1f"
                        Plugin.BarWidget { id:bar; socketPath: "SOCKET"; anchors.centerIn:parent; width:80; height:36 }
                    }
                }'''.replace('SOCKET',str(root/'absent.sock') if state=='bar-disconnected' else str(path))+'\n', source)
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
