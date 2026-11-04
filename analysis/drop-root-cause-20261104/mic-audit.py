#!/usr/bin/env python3
"""Passive metadata-only audit of mic35ca:1102 in retained capture windows."""
import collections,csv,datetime,json,mmap,pathlib,re,struct,subprocess
P=pathlib.Path(__file__).parent;BASE=pathlib.Path.home()/'.local/state/viture-workspace/usb-captures'
H=struct.Struct('<QBBBBHbbqiiII8s')
rows=[json.loads(l) for l in subprocess.check_output(['journalctl','-k','-b','-o','json','--no-pager'],text=True).splitlines()]
address={}; identities={}; maps=[]
for r in rows:
 m=re.match(r'usb ([15]-[\d.]+): (.*)',r.get('MESSAGE',''))
 if not m:continue
 port,msg=m.groups();bus=int(port.split('-')[0]);t=int(r['__REALTIME_TIMESTAMP'])/1e6
 n=re.search(r'new (?:full|high)-speed USB device number (\d+)',msg)
 if n:address[port]=int(n[1])
 n=re.search(r'idVendor=(\w+), idProduct=(\w+)',msg)
 if n and port in address:maps.append((t,bus,address[port],':'.join(n.groups()),port))
# In this boot these addresses do not identify different products; include
# descriptor requests that precede the journal's identity announcement.
products=collections.defaultdict(set)
for ts,b,d,v,p in maps:products[(b,d)].add(v)
assert all(len(v)==1 for v in products.values()), 'address reuse requires time-bounded mapping'
def identity(t,bus,dev):
 return next(iter(products.get((bus,dev), {'unknown'})))
def events(paths):
 for path in paths:
  with path.open('rb') as f,mmap.mmap(f.fileno(),0,access=mmap.ACCESS_READ) as m:
   off=0
   while off<len(m):
    assert off+48<=len(m),(path,off)
    uid,kind,xfer,ep,dev,bus,fs,fd,sec,usec,status,length,cap,setup=H.unpack_from(m,off)
    assert kind in b'SCE' and off+48+cap<=len(m) and 0<=usec<1000000
    yield (sec+usec/1e6,bus,dev,chr(kind),xfer,ep,status,length)
    off+=48+cap
# Use one best covering folder per historical drop; duplicates are not independent events.
a=json.loads((P/'capture-audit.json').read_text());unique={}
for v in a:unique.setdefault(v['drop'],v)
result=[]
for stamp,r in unique.items():
 t=datetime.datetime.fromisoformat(stamp).timestamp(); paths=sorted((BASE/'drops'/r['folder']).glob('*.bin'))
 selected=[e for e in events(paths) if t-30<=e[0]<t-.01]
 mic=[e for e in selected if identity(e[0],e[1],e[2])=='35ca:1102']
 result.append({'drop':stamp,'folder':r['folder'],'window':'preceding30s excluding last10ms','mic_events':len(mic),'mic_last5s_events':sum(e[0]>=t-5 for e in mic),'mic_completion_errors':dict(collections.Counter(str(e[6]) for e in mic if e[3]!='S' and e[6])),'mic_event_types':[[*k,n] for k,n in collections.Counter((e[3],e[4],e[5]) for e in mic).items()]})
(P/'mic-audit.json').write_text(json.dumps(result,indent=2)+'\n')
for r in result:print(r['drop'],'mic30s',r['mic_events'],'mic5s',r['mic_last5s_events'],'errors',r['mic_completion_errors'])
