"""Private fake wire endpoints. Run by a copied Python executable named Hyprland."""
import json,os,re,socket,threading,sys,time,struct
from pathlib import Path
root=Path(sys.argv[1]);instance=sys.argv[2];directory=root/'hypr'/instance;directory.mkdir(parents=True,mode=0o700)
(directory/'hyprland.lock').write_text(str(os.getpid())+'\nwayland-1\n')
trace=root/'wire.jsonl';registry={};plugins=[]
def log(event):
 with open(trace,'a') as f:f.write(json.dumps(event)+'\n')
def mon(name,id,x,y,width=1920,height=1080):return dict(name=name,id=id,x=x,y=y,width=width,height=height,refreshRate=120,scale=1,transform=0,disabled=False,dpmsStatus=True,mirrorOf='none',activeWorkspace=dict(id=1,name='1'),reserved=[0,0,0,0],model='VITURE' if name=='DP-2' else 'ordinary',description='fixture',availableModes=['1920x1080@120.00Hz'])
physical=[mon('DP-1',0,0,0),mon('DP-2',1,1920,0)]
def monitors():
 out=list(physical)
 for r in registry.values():
  if r['state']=='live':
   o=r['observation'];m=mon(r['name'],len(out),o['x'],o['y'],o['width'],o['height']);m['activeWorkspace']=dict(id=o['workspace'],name=str(o['workspace']));out.append(m)
 return out
def respond(wire,caller_pid):
 log(dict(request=wire[:100],kind='wire'))
 if wire=='j/cursorpos':return dict(x=100,y=100)
 if wire in ('j/monitors','j/monitors all'):return monitors()
 if wire=='j/workspaces':return [dict(id=1,name='1',monitor='DP-1',monitorID=0,windows=0)]
 if wire in ('j/clients','j/binds'):return []
 if wire=='binds':return ''
 if wire=='j/activewindow':return {}
 if wire.startswith('j/getoption'):return dict(int=0,float=0,str='',data='rgba(ffffffff)')
 if wire=='j/plugin list':return [dict(name=p) for p in plugins]
 if wire.startswith('/plugin load '):plugins.append('xr-ownership');log(dict(stage='plugin-load'));return 'ok'
 if wire.startswith('/plugin unload '):plugins.clear();log(dict(stage='plugin-unload'));return 'ok'
 if wire.startswith('/eval '):
  if 'hl.monitor(' in wire:
   n=re.search(r'output\s*=\s*"([^"]+)"',wire);pos=re.search(r'position\s*=\s*"(-?\d+)x(-?\d+)"',wire)
   if n and pos:
    for m in physical:
     if m['name']==n[1]:m['x']=int(pos[1]);m['y']=int(pos[2])
  log(dict(stage='eval',binds='hl.bind' in wire,restore='local ow=' in wire));return 'ok'
 if wire.startswith('j/xrspike '):
  j=json.loads(wire[10:]);p=j['payload'];op=j['op'];log(dict(stage='broker-'+op,caller_pid=caller_pid))
  if op=='hello':result=dict(qualified=True,fallback='physical-handle')
  elif op in ('cursor','cursor_shape'):result=dict(name='default')
  else:
   if op=='create':
    path=root/'xr-workspace-spike'/j['run_id']/'journal.json'
    records=json.loads(path.read_text())['operations'];assert any(r['operation']['id']==p['id'] and r['state']=='pending' for r in records)
    registry[p['id']]=dict(id=p['id'],name=p['name'],state='live',physical=False,fallback_usable=True,observation=dict(**{k:p[k] for k in ('width','height','refresh_millihz','scale','x','y')},workspace=61+10*p['index'],enabled=True,dpms=True,mirrored=False,transform=0,workspaces=[dict(id=61+10*p['index'],name=str(61+10*p['index']),special=False)],windows=[]))
   if op=='create' and os.environ.get('PRO2_CLI_CASE')=='create-fail':registry[p['id']]['state']='failed'
   if p.get('id') not in registry:return dict(error='unproven UUID')
   if op=='evacuate_remove':registry[p['id']]['state']='dead';registry[p['id']]['observation']=None
   result=registry[p['id']]
  return dict(**{k:j[k] for k in ('schema','session','run_id','request_id')},epoch='epoch-one',result=result)
 raise RuntimeError('unhandled fixture request '+wire[:100])
def serve(path,kind):
 s=socket.socket(socket.AF_UNIX);s.bind(str(path));s.listen(100)
 while True:
  conn,_=s.accept()
  def handle(c):
   try:
    if kind=='rpc':
     data=c.recv(200000).decode();peer=struct.unpack('3i',c.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12));result=respond(data,peer[0]);c.sendall((result if isinstance(result,str) else json.dumps(result)).encode())
    else:
     while c.recv(1024):pass
   except Exception as e:log(dict(error=repr(e)))
   finally:c.close()
  threading.Thread(target=handle,args=(conn,),daemon=True).start()
for path,kind in [(directory/'.socket.sock','rpc'),(directory/'.socket2.sock','events'),(root/'wayland-1','wayland')]:threading.Thread(target=serve,args=(path,kind),daemon=True).start()
(root/'fake-ready').write_text('ready')
while True:time.sleep(.1)
