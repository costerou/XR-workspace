#!/usr/bin/env python3
"""Offline API0 reconstruction. No device access or peripheral payload retention."""
import collections,datetime,hashlib,json,mmap,pathlib,struct,zoneinfo
P=pathlib.Path(__file__).parent;BASE=pathlib.Path.home()/'.local/state/viture-workspace/usb-captures/drops';TZ=zoneinfo.ZoneInfo('America/Detroit')
H=struct.Struct('<QBBBBHbbqiiII8s');assert H.size==48
def time(s):return datetime.datetime.fromisoformat('2026-11-04T'+s+'-05:00').timestamp()
def iso(t):return datetime.datetime.fromtimestamp(t,TZ).isoformat(timespec='microseconds')
def read(folders):
 files={}; manifests=[];events=[]
 for folder in folders:
  for p in sorted((BASE/folder).glob('*.bin')):
   digest=hashlib.sha256(p.read_bytes()).hexdigest()
   if p.name in files:
    assert files[p.name][1]==digest,'overlapping chunks differ';continue
   files[p.name]=(p,digest)
 for name,(p,digest) in sorted(files.items()):
  count=0
  with p.open('rb') as f,mmap.mmap(f.fileno(),0,access=mmap.ACCESS_READ) as m:
   off=0
   while off<len(m):
    assert off+48<=len(m),(p,off)
    uid,kind,xfer,ep,dev,bus,fs,fd,sec,usec,status,length,cap,setup=H.unpack_from(m,off)
    assert kind in b'SCE' and 0<=usec<1000000 and off+48+cap<=len(m),(p,off)
    payload=None
    # Only short known Luma command framing; no IMU, audio, camera or string descriptors.
    if xfer==3 and ep in (4,133) and 0<cap<=8 and m[off+48:off+50]==b'\xfa\x55':payload=m[off+48:off+48+cap].hex()
    if dev==1 and xfer==2 and kind==67 and cap==4:payload=m[off+48:off+52].hex()
    events.append({'t':sec+usec/1e6,'uid':uid,'kind':chr(kind),'xfer':xfer,'ep':ep,'dev':dev,'bus':bus,'status':status,'length':length,'cap':cap,'setup':setup.hex() if xfer==2 and kind==83 and fs==0 else None,'payload':payload})
    off+=48+cap;count+=1
  manifests.append({'file':name,'sha256':digest,'events':count,'bytes':p.stat().st_size})
 assert all(a['t']<=b['t'] for a,b in zip(events,events[1:])),'out-of-order timestamps'
 return events,manifests

def summary(ev,lo,hi,dev,bus):
 rows=[e for e in ev if lo<=e['t']<hi and e['dev']==dev and e['bus']==bus]
 return {'start':iso(lo),'end':iso(hi),'device':dev,'bus':bus,'counts':[[*k,n] for k,n in sorted(collections.Counter((e['kind'],hex(e['ep']),e['xfer'],e['status'],e['length']) for e in rows).items())],'errors':[dict(time=iso(e['t']),endpoint=hex(e['ep']),kind=e['kind'],status=e['status'],length=e['length']) for e in rows if e['kind']!='S' and e['status'] not in (0,-2)],'commands':[dict(time=iso(e['t']),endpoint=hex(e['ep']),payload=e['payload'],length=e['length']) for e in rows if e['kind']=='S' and e['ep']<128],'control_requests':[{'time':iso(e['t']),'setup':e['setup']} for e in rows if e['setup']]}
new,files=read(['20261104-111603','20261104-111613'])
# Initial bursts of8 MCU read submissions identify queue generations, not ioctls.
starts=[];last=None
for e in new:
 if e['dev']==27 and e['bus']==1 and e['ep']==133 and e['kind']=='S':
  if last is None or e['t']-last>1:starts.append(e['t'])
  last=e['t']
windows=[]
for i,t in enumerate(starts):
 hi=starts[i+1] if i+1<len(starts) else time('11:16:03.308238')
 # IMU queue is submitted immediately before MCU queue.
 lo=t-.005
 w=summary(new,lo,hi-.005 if i+1<len(starts) else hi,27,1)
 w['queue_start']=iso(t)
 first_complete=next(e['t'] for e in new if e['dev']==27 and e['kind']=='C' and e['t']>=lo)
 w['initial_submissions_before_first_completion']=[[hex(ep),sum(e['kind']=='S' and e['dev']==27 and e['ep']==ep and lo<=e['t']<first_complete for e in new)] for ep in (129,133)]
 good=[e for e in new if e['dev']==27 and e['kind']=='C' and e['ep']==129 and e['status']==0 and lo<=e['t']<hi-.005]
 w['successful_imu']={'count':len(good),'first':iso(good[0]['t']) if good else None,'last':iso(good[-1]['t']) if good else None,'max_gap_ms':max((b['t']-a['t'] for a,b in zip(good,good[1:])),default=0)*1000}
 w['cancelled']=sum(e['kind']=='C' and e['dev']==27 and e['status']==-2 and lo<=e['t']<hi-.005 for e in new)
 windows.append(w)
full=summary(new,time('11:15:20'),time('11:16:03.308238'),27,1)
# Compare known clean restart and old first/secondary drops.
clean,cf=read(['20261104-104604']);old,of=read(['20261104-105733','20261104-105745'])
commanded,cmdfiles=read(['20261104-103858'])
comparisons=[summary(commanded,time('10:39:01'),time('10:39:10'),39,5),summary(clean,time('10:44:49'),time('10:45:20'),39,5),summary(old,time('10:55:55'),time('10:57:33.414243'),56,5),summary(old,time('10:57:35'),time('10:57:44.875235'),60,5)]
# Per second all-device success/error counts (payload not stored).
seconds=collections.Counter((int(e['t']),e['bus'],e['dev'],e['kind'],hex(e['ep']),e['status']) for e in new)
result={'files':files,'coverage':[iso(new[0]['t']),iso(new[-1]['t'])],'event_count':len(new),'queue_generations':windows,'pre_hub_luma':full,'comparison_files':cmdfiles+cf+of,'comparisons':comparisons,'per_second':[[*k,n] for k,n in sorted(seconds.items())]}
result['critical_events']=[dict(time=iso(e['t']),device=e['dev'],event=e['kind'],ep=hex(e['ep']),status=e['status'],length=e['length'],payload=e['payload']) for e in new if e['dev']==27 and e['kind']=='C' and ((time('11:15:24.83')<=e['t']<time('11:15:24.85')) or (time('11:15:28.4585')<=e['t']<time('11:15:28.465')))]
result['post_hub_commands']=[dict(time=iso(e['t']),device=e['dev'],length=e['length'],payload=e['payload']) for e in new if e['dev'] in (29,34) and e['ep']==4 and e['kind']=='S']
result['old_teardown_errors']=[dict(time=iso(e['t']),device=e['dev'],ep=hex(e['ep']),status=e['status']) for e in old if e['kind']=='C' and e['status'] and ((time('10:57:33.4')<=e['t']<time('10:57:33.43')) or (time('10:57:44.86')<=e['t']<time('10:57:44.9')))]
(P/'precursor-audit.json').write_text(json.dumps(result,indent=2)+'\n')
print('new events',len(new),'files',len(files),'generations',len(windows))
for w in windows:
 print(w['queue_start'],'initial',w['initial_submissions_before_first_completion'],'cancels',w['cancelled'],'commands',w['commands'],'errors',w['errors'][:12])
print('ALL commands',full['commands'],'controls',full['control_requests'])
for c in comparisons:print('COMPARE',c['start'],c['end'],'errors',c['errors'][:20],'commands',c['commands'],'counts',c['counts'])
