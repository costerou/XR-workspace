#!/usr/bin/env python3
"""Offline journal correlation. Reads journals/state, writes sanitized derived tables only."""
import collections,csv,datetime,json,pathlib,re,subprocess,zoneinfo
OUT=pathlib.Path(__file__).parent
TZ=zoneinfo.ZoneInfo('America/Detroit')
STATE=pathlib.Path.home()/'.local/state/viture-workspace'
def journal(*args):
    p=subprocess.Popen(['journalctl','--since','2026-11-03','--until','2026-11-04 11:05:00','-o','json','--no-pager',*args],stdout=subprocess.PIPE,text=True)
    rows=[json.loads(l) for l in p.stdout]; assert p.wait()==0
    return rows
def t(r):return int(r['__REALTIME_TIMESTAMP'])/1e6
def iso(ts):return datetime.datetime.fromtimestamp(ts,TZ).isoformat(timespec='milliseconds')
k=journal('_TRANSPORT=kernel'); u=journal('--user','--user-unit=viture-xr-*')
sessions={}; renderer=[]
for r in u:
    msg=r.get('MESSAGE',''); unit=r.get('USER_UNIT',r.get('_SYSTEMD_USER_UNIT',''))
    m=re.search(r'viture-xr-([0-9a-f]{32})',unit)
    if not m:continue
    rid=m[1]; s=sessions.setdefault(rid,{'id':rid,'first':t(r),'last':t(r),'start':None,'end':None})
    s['first']=min(s['first'],t(r));s['last']=max(s['last'],t(r))
    if msg.startswith('Started ') and 'tools.xr_controller.runner' in msg:s['start']=t(r)
    if 'Consumed ' in msg and s['end'] is None:s['end']=t(r)
    if msg.startswith('{'):
        try:a=json.loads(msg)
        except ValueError:continue
        if a.get('kind')=='managed_output_capture':
            renderer.append({'time':t(r),'run':rid,'buffer':a.get('surface',{}).get('buffer'),'stereo':a.get('stereo'),'composition':a.get('composition',{}).get('draw_swap')})
for rid,s in sessions.items():
    p=STATE/(rid+'.json'); consent=json.loads(p.read_text()) if p.exists() else {}
    s.update(driver=consent.get('tracking','unknown'),model=consent.get('model','unknown'),mode=consent.get('mode_switch'),binary=consent.get('luma_driver',{}).get('path',''),start=s['start'] or s['first'],end=s['end'] or s['last'])
# Follow identity on each physical port; includes new port1-5 only when QinHeng is there.
ident={};drops=[];enumerations=[]
for r in k:
    msg=r.get('MESSAGE',''); m=re.match(r'usb (5-2|1-5): (.*)',msg)
    if not m:continue
    port,body=m.groups(); now=t(r)
    if 'idVendor=' in body:ident[port]=re.search(r'idVendor=(\w+), idProduct=(\w+)',body).groups()
    if 'new high-speed USB device' in body:enumerations.append((now,port))
    if body.startswith('USB disconnect') and (port=='5-2' or ident.get(port)==('1a86','8091')):
        drops.append({'epoch':now,'time':iso(now),'port':port,'identity':':'.join(ident.get(port,('unknown','unknown')))})
log=[]
for line in (STATE/'luma-driver.log').read_text().splitlines():
    m=re.search(r' at=(\d+\.\d+)',line)
    if m:log.append((float(m[1]),line.split(' at=')[0]))
for d in drops:
    now=d['epoch']; active=[s for s in sessions.values() if s['start']<=now<=s['end']]
    s=active[-1] if active else None
    d['run']=s['id'] if s else '';d['driver']=s['driver'] if s else 'no-active-unit';d['model']=s['model'] if s else 'unknown'
    nxt=next((ts for ts,p in enumerations if ts>now and p==d['port']),None)
    d['reenumeration_s']=round(nxt-now,3) if nxt else None
    d['since_session_start_s']=round(now-s['start'],3) if s else None
    prev=[(ts,l) for ts,l in log if ts<now and ('stats ' in l or 'connected' in l)]
    d['last_luma_event']=prev[-1][1] if prev and now-prev[-1][0]<180 else ''
    rs=[r for r in renderer if r['time']>=now and r['time']<now+120 and (not s or r['run']==s['id'])]
    d['next_renderer_exit']=iso(rs[0]['time']) if rs else '';d['next_renderer_buffer']=str(rs[0]['buffer']) if rs else ''
with (OUT/'timeline.csv').open('w') as f:
    w=csv.DictWriter(f,fieldnames=list(drops[0]));w.writeheader();w.writerows(drops)
(OUT/'sessions.json').write_text(json.dumps(sorted(sessions.values(),key=lambda s:s['start']),indent=2)+'\n')
(OUT/'renderer-exits.json').write_text(json.dumps(renderer,indent=2)+'\n')
print('drops',len(drops),'by date',dict(collections.Counter(d['time'][:10] for d in drops)),'sessions',len(sessions),'renderer exits',len(renderer))
for d in drops:print(d['time'],d['port'],d['driver'],d['run'][:6],d['reenumeration_s'],d['since_session_start_s'])
