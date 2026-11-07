#!/usr/bin/env python3
"""Matched, descriptive journal-window comparison; no causal/significance claim."""
import bisect,collections,csv,json,pathlib,random,re,subprocess
P=pathlib.Path(__file__).parent
sessions={s['id']:s for s in json.loads((P/'sessions.json').read_text())}
drops=list(csv.DictReader((P/'timeline.csv').open()));seen=set();cases=[];excluded=collections.Counter()
for d in drops:
 s=sessions.get(d['run']);t=float(d['epoch'])
 if d['identity']!='1a86:8091' or d['model']!='luma-ultra' or not s:
  excluded['unknown_identity_or_model_or_no_session']+=1;continue
 if d['run'] in seen:excluded['later_same_session']+=1;continue
 seen.add(d['run'])
 if not d['reenumeration_s'] or float(d['reenumeration_s'])>30:excluded['slow_return']+=1;continue
 if s['start']+40>=t:excluded['too_short']+=1;continue
 cases.append((t,s['start'],d['time']))
patterns={'mtp':r'mtp-probe|libmtp','udev':r'udev','wireplumber':r'wireplumber','pipewire':r'pipewire','power':r'power-profiles|upower|tlp|tuned','steam':r'steam','logind':r'logind','reset_or_authorize':r'reset|authorized to connect|power cycle|unbind','renderer':r'renderer|xr-overlay|xr_overlay','systemd':r'systemd','usb_kernel':r'kernel.*(?:usb |xhci|uvcvideo|hid-generic)','xr_driver':r'xrlinuxdriver|xr-luma-driver|luma:|viture-workspace'}
rx={k:re.compile(v,re.I) for k,v in patterns.items()};times={k:[] for k in rx}
p=subprocess.Popen(['journalctl','--since','2026-11-03','--until','2026-11-04 11:02','-o','json','--no-pager','--output-fields=MESSAGE,SYSLOG_IDENTIFIER,_COMM,_SYSTEMD_UNIT,_SYSTEMD_USER_UNIT'],stdout=subprocess.PIPE,text=True)
n=0
for line in p.stdout:
 r=json.loads(line);n+=1;t=int(r['__REALTIME_TIMESTAMP'])/1e6
 msg=' '.join(str(r.get(k,'')) for k in ['SYSLOG_IDENTIFIER','_COMM','_SYSTEMD_UNIT','_SYSTEMD_USER_UNIT','MESSAGE'])
 for k,pat in rx.items():
  if pat.search(msg):times[k].append(t)
assert p.wait()==0
rng=random.Random(20261104);controls=[rng.uniform(s+10,t-30) for t,s,_ in cases for i in range(10)]
result={'journal_records':n,'cases':[v[2] for v in cases],'controls':len(controls),'seed':20261104,'exclusions':dict(excluded),'patterns':patterns,'windows':{}}
for width in [5,10]:
 result['windows'][width]={}
 for k,ts in times.items():
  counts=[]
  for group in [[v[0] for v in cases],controls]:
   c=[bisect.bisect_left(ts,t)-bisect.bisect_left(ts,t-width) for t in group]
   counts.append({'windows':sum(v>0 for v in c),'messages':sum(c)})
  result['windows'][width][k]={'cases':counts[0],'controls':counts[1]}
(P/'correlation.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
