"""Offline sanitized stationary summary. Reads JSONL only, never opens SDK/devices."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import statistics


def quantiles(values):
    ordered=sorted(values)
    return {name:ordered[round((len(ordered)-1)*q)] for name,q in [('min',0),('p05',.05),('median',.5),('p95',.95),('max',1)]}


def slope(xs,ys):
    mx=statistics.mean(xs);my=statistics.mean(ys)
    variance=sum((x-mx)**2 for x in xs)
    if not variance: raise ValueError('no host time span')
    return sum((x-mx)*(y-my) for x,y in zip(xs,ys))/variance


def summarize(records):
    poses=[r for r in records if r.get('kind')=='pose']
    if len(poses)<3: raise ValueError('need at least three samples')
    values=[r['sdk_values'] for r in poses]
    if any(len(v)!=7 or any(type(x) not in (int,float) or not math.isfinite(x) for x in v) for v in values):
        raise ValueError('invalid callback payload')
    timestamps=[r['timestamp_raw'] for r in poses];hosts=[r['host_monotonic_ns'] for r in poses]
    if any(type(t)!=int or not 0<=t<2**64 for t in timestamps): raise ValueError('invalid SDK timestamp')
    if any(type(t)!=int for t in hosts): raise ValueError('invalid host timestamp')
    ds=[b-a for a,b in zip(timestamps,timestamps[1:])];dh=[b-a for a,b in zip(hosts,hosts[1:])]
    if min(ds)<=0 or min(dh)<=0: raise ValueError('non-increasing timestamp')
    x=[(h-hosts[0])/1e9 for h in hosts];y=[t-timestamps[0] for t in timestamps]
    rate=slope(x,y)
    blocks=[]
    for start in range(0,int(x[-1]),3):
        indices=[i for i,t in enumerate(x) if start<=t<start+3]
        if len(indices)>=30 and x[indices[-1]]-x[indices[0]]>=2:
            blocks.append(slope([x[i] for i in indices],[y[i] for i in indices]))
    intercept=statistics.mean(y)-rate*statistics.mean(x)
    residual_ms=[(yi-(rate*xi+intercept))/rate*1000 for xi,yi in zip(x,y)]
    norms=[math.sqrt(sum(a*a for a in v[3:7])) for v in values]
    if min(norms)<=0: raise ValueError('zero quaternion')
    reference=[v/norms[0] for v in values[0][3:7]]
    def angle(v,n):
        dot=abs(sum(a*b/n for a,b in zip(reference,v[3:7])))
        return math.degrees(2*math.acos(min(1,dot)))
    firmware=[r for r in records if r.get('kind')=='firmware_query']
    version=firmware[-1].get('version') if firmware else None
    if not isinstance(version,str) or not re.fullmatch(r'[0-9._]+',version): version='not-exported'
    contracts=[r for r in records if r.get('kind')=='contract']
    pin=contracts[-1].get('library_sha256') if contracts else None
    if not isinstance(pin,str) or not re.fullmatch('[0-9a-f]{64}',pin): pin='not-exported'
    return dict(samples=len(poses),duration_host_s=x[-1],observed_hz=(len(poses)-1)/x[-1],
                issue_samples=sum(bool(r.get('issues')) for r in poses),host_interval_ns=quantiles(dh),
                sdk_delta_raw=quantiles(ds),sdk_ticks_per_host_second_ols=rate,
                sdk_ticks_per_host_second_endpoint=y[-1]/x[-1],three_second_block_slopes=blocks,
                residual_receipt_ms=quantiles(residual_ms),quaternion_norm=quantiles(norms),
                angular_distance_from_first_deg=quantiles([angle(v,n) for v,n in zip(values,norms)]),
                firmware_status=firmware[-1].get('status') if firmware else None,firmware_version=version,
                library_sha256=pin,cleanup=[dict(error_count=len(r.get('errors',[])),queue_dropped=r.get('queue_dropped')) for r in records if r.get('kind')=='cleanup'],
                timestamp_unit='unvalidated; paired clock fit only',frame='unvalidated',optical_tilt='unvalidated')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('capture',type=Path);a=p.parse_args()
    data=a.capture.read_bytes();records=[json.loads(line) for line in data.splitlines()]
    result=summarize(records);result['capture_sha256']=hashlib.sha256(data).hexdigest()
    print(json.dumps(result,indent=2,allow_nan=False))
