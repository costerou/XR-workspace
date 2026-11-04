#!/usr/bin/env python3
"""Read-only raw API0 capture audit. Retains headers and USB setup only, never image/IMU payloads."""
import collections,csv,json,mmap,pathlib,struct
OUT=pathlib.Path(__file__).parent
BASE=pathlib.Path.home()/'.local/state/viture-workspace/usb-captures/drops'
H=struct.Struct('<QBBBBHbbqiiII8s'); assert H.size==48
D=list(csv.DictReader((OUT/'timeline.csv').open()))
results=[]
for folder in sorted(BASE.iterdir()):
    if folder.name<'20261104-103858':continue
    events=[];files=[]
    for p in sorted(folder.glob('*.bin')):
        count=0
        with p.open('rb') as f,mmap.mmap(f.fileno(),0,access=mmap.ACCESS_READ) as m:
            off=0
            while off<len(m):
                assert off+48<=len(m),(p,off)
                uid,kind,xfer,ep,dev,bus,fs,fd,sec,usec,status,length,cap,setup=H.unpack_from(m,off)
                assert kind in b'SCE' and 0<=usec<1000000 and off+48+cap<=len(m),(p,off)
                tm=sec+usec/1e6
                # Retain compact metadata. Root-hub status payload is a four-byte port register.
                portstatus=m[off+48:off+52].hex() if dev==1 and xfer==2 and kind==67 and cap==4 else None
                events.append((tm,dev,ep,xfer,chr(kind),status,length,setup.hex() if xfer==2 and kind==83 and fs==0 else None,portstatus))
                count+=1;off+=48+cap
        files.append({'file':p.name,'events':count,'bytes':p.stat().st_size})
    if not events:continue
    lo=min(e[0] for e in events);hi=max(e[0] for e in events)
    for drop in D:
        ts=float(drop['epoch'])
        if drop['port']!='5-2' or not lo+5<=ts<=hi:continue
        # Exclude the last10ms to separate teardown from precursor errors.
        before=[e for e in events if ts-5<=e[0]<ts-.01]
        rate=collections.Counter((e[1],e[2],e[3],e[5]) for e in before if e[4]=='C')
        controls=[{'dt':round(e[0]-ts,6),'dev':e[1],'setup':e[7]} for e in events if ts-30<=e[0]<ts and e[7]]
        errors=[{'dt':round(e[0]-ts,6),'dev':e[1],'ep':e[2],'status':e[5]} for e in events if ts-.02<=e[0]<=ts+.002 and e[4]!='S' and e[5]]
        ports=[{'dt':round(e[0]-ts,6),'status':e[8]} for e in events if ts-.1<=e[0]<=ts+.01 and e[8]]
        results.append({'folder':folder.name,'drop':drop['time'],'coverage':[lo,hi],'files':files,'final_4_99s_completions':[[*k,n] for k,n in sorted(rate.items())],'controls_last30s':controls,'teardown_errors':errors,'root_port_status':ports})
(OUT/'capture-audit.json').write_text(json.dumps(results,indent=2)+'\n')
seen=set()
for r in results:
    if r['drop'] in seen:continue
    seen.add(r['drop']);print(r['drop'],'completions',r['final_4_99s_completions'],'ports',r['root_port_status'])
    print('port suspend/resume', [c for c in r['controls_last30s'] if c['setup'].startswith(('230302','230102'))])
    print('first errors',r['teardown_errors'][:3])
