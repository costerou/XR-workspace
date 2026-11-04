"""Offline receipt-timed comparison; never converts SDK frame or timestamp units."""
import argparse
import json
import math
import statistics


def compare(records):
    samples=[r for r in records if r.get('kind')=='pose' and not r['issues']]
    if not samples: raise ValueError('no valid SDK samples')
    start=samples[0]['host_monotonic_ns']; result={}
    for name,lo,hi in [('center',2,5),('left',7,10),('right',12,15),('up',17,20),('down',22,25),('return',27,30)]:
        chunk=[r for r in samples if lo<=(r['host_monotonic_ns']-start)/1e9<hi]
        if len(chunk)<10: raise ValueError('insufficient samples for '+name)
        ref=chunk[0]['sdk_values'][3:7]; qs=[]
        for row in chunk:
            q=row['sdk_values'][3:7]
            qs.append(q if sum(a*b for a,b in zip(q,ref))>=0 else [-v for v in q])
        q=[statistics.median(v) for v in zip(*qs)]; n=math.sqrt(sum(v*v for v in q))
        result[name]={'q_median_unit_for_comparison':[v/n for v in q],'samples':len(chunk)}
    # Relative quaternion q_center^-1 * q_segment; no axis remap or pitch offset.
    w,x,y,z=result['center']['q_median_unit_for_comparison']; a=(w,-x,-y,-z)
    for r in result.values():
        b=r['q_median_unit_for_comparison']; aw,ax,ay,az=a; bw,bx,by,bz=b
        r['relative_wxyz']=[aw*bw-ax*bx-ay*by-az*bz,aw*bx+ax*bw+ay*bz-az*by,
                            aw*by-ax*bz+ay*bw+az*bx,aw*bz+ax*by-ay*bx+az*bw]
    hypotheses={}
    for name,r in result.items():
        w,x,y,z=r['relative_wxyz']
        hypotheses[name]={'documented_NWU_identity_wxyz':[w,x,y,z],
                          'upstream_EUS_to_NWU_only_wxyz':[w,-z,-x,y]}
    return dict(frame='unverified SDK frame; no upstream conversion/pitch applied',segments=result,
                analysis_only_hypotheses=hypotheses,selected_mapping=None,
                optical_tilt='not measurable from relative quaternion comparison')

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('log');args=p.parse_args()
    with open(args.log) as f: records=[json.loads(line) for line in f]
    print(json.dumps(compare(records),indent=2))
