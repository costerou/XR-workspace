#!/usr/bin/env python3
"""Offline usbmon read() dump summary; never opens a device or emits payloads."""
import argparse,struct,mmap,json,collections,datetime
H=struct.Struct('<QBBBBHbbqiiII8s')
p=argparse.ArgumentParser();p.add_argument('files',nargs='+');p.add_argument('--out',required=True);a=p.parse_args()
rows=collections.defaultdict(lambda:[0,0,collections.Counter(),0,collections.Counter()]); first=None;last=None; errors=[];controls=[];count=0
for path in sorted(a.files):
 with open(path,'rb') as f:
  with mmap.mmap(f.fileno(),0,access=mmap.ACCESS_READ) as m:
   off=0
   while off<len(m):
    if off+48>len(m): raise ValueError((path,off,'short header'))
    v=H.unpack_from(m,off); uid,kind,xfer,ep,dev,bus,fs,fd,sec,usec,status,length,cap,setup=v
    if kind not in (83,67,69) or usec<0 or usec>=1000000 or off+48+cap>len(m):raise ValueError((path,off,v))
    t=sec+usec/1e6;first=t if first is None else min(first,t);last=t if last is None else max(last,t);count+=1
    key=(sec,dev,ep,xfer,chr(kind));r=rows[key];r[0]+=1;r[1]+=length;r[2][status]+=1
    if xfer==0 and kind==67:
     # iso descriptor array precedes captured packet data
     for i in range(min(struct.unpack_from('<I',setup,4)[0],cap//16)):
      st,ofs,ln,pad=struct.unpack_from('<iIII',m,off+48+16*i);r[3]+=ln;r[4][st]+=1
    if kind!=83 and status and len(errors)<10000: errors.append([t,dev,hex(ep),xfer,chr(kind),status,length])
    if xfer==2 and kind==83 and len(controls)<10000:controls.append([t,dev,hex(ep),setup.hex(),length])
    off+=48+cap
out={'first':first,'last':last,'events':count,'errors':errors,'controls':controls,'rows':[[*k,r[0],r[1],dict(r[2]),r[3],dict(r[4])] for k,r in sorted(rows.items())]}
with open(a.out,'w') as f:json.dump(out,f)
print(a.out,datetime.datetime.fromtimestamp(first).isoformat(),datetime.datetime.fromtimestamp(last).isoformat(),count,'errors',len(errors))
