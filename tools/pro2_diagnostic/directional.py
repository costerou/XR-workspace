"""Offline cue-aligned aggregate analysis; exports no raw/median pose quaternions."""
import math
import statistics
from .adapter import basis


def mul(a,b):
    w,x,y,z=a;v,i,j,k=b
    return [w*v-x*i-y*j-z*k,w*i+x*v+y*k-z*j,w*j-x*k+y*v+z*i,w*k+x*j-y*i+z*v]


def conj(q):return [q[0],-q[1],-q[2],-q[3]]

def median_q(rows):
    qs=[r['sdk_values'][3:7] for r in rows];ref=qs[0]
    qs=[q if sum(a*b for a,b in zip(q,ref))>=0 else [-v for v in q] for q in qs]
    q=[statistics.median(v) for v in zip(*qs)];n=math.sqrt(sum(v*v for v in q))
    if not n:raise ValueError('zero median')
    return [v/n for v in q]


def rotation(q):
    if q[0]<0:q=[-v for v in q]
    n=math.sqrt(sum(v*v for v in q[1:]))
    return dict(angle_deg=math.degrees(2*math.atan2(n,q[0])),axis_xyz=[v/n for v in q[1:]] if n>1e-12 else [0]*3)


def aggregate(records,cues,*,roll=False):
    poses=[r for r in records if r.get('kind')=='pose']
    if not poses or any(r.get('issues') for r in poses):raise ValueError('missing/invalid samples')
    first=poses[0]['host_monotonic_ns'];last=poses[-1]['host_monotonic_ns']
    expected=['center','roll_left','roll_right','return'] if roll else ['center','left','right','up','down','return']
    cues=[c for c in cues if c['label'] in expected]
    if [c['label'] for c in cues]!=expected:raise ValueError('cue order')
    times=[c['host_monotonic_ns'] for c in cues]
    if any(b<=a for a,b in zip(times,times[1:])):raise ValueError('cue time order')
    result={};medians={}
    for i,c in enumerate(cues):
        start=c['host_monotonic_ns']+2000000000
        end=min(c['host_monotonic_ns']+5000000000,times[i+1] if i+1<len(times) else last,last)
        rows=[r for r in poses if start<=r['host_monotonic_ns']<=end]
        if len(rows)<10:raise ValueError('insufficient settled samples')
        medians[c['label']]=median_q(rows)
        result[c['label']]=dict(cue_offset_s=(times[i]-first)/1e9,
            settled_bounds_s=[(start-first)/1e9,(end-first)/1e9],samples=len(rows),
            actual_sample_span_s=(rows[-1]['host_monotonic_ns']-rows[0]['host_monotonic_ns'])/1e9)
    q0=medians['center']
    for name,r in result.items():
        r['compositions']={}
        for composition,q in [('body',mul(conj(q0),medians[name])),('reference',mul(medians[name],conj(q0)))]:
            r['compositions'][composition]={}
            for h,relative in [('H0_identity',q),('H1_upstream_basis',basis(q))]:
                metric=rotation(relative)
                axis=0 if name in ('roll_left','roll_right') else 2 if name in ('left','right') else 1 if name in ('up','down') else None
                if axis is not None:metric['off_expected_axis_fraction']=math.sqrt(max(0,1-metric['axis_xyz'][axis]**2))
                r['compositions'][composition][h]=metric
    pairs={}
    for composition in ('body','reference'):
        def axis(name):return result[name]['compositions'][composition]['H0_identity']['axis_xyz']
        def dot(a,b):return sum(x*y for x,y in zip(axis(a),axis(b)))
        pairs[composition]=dict(roll_left_right_axis_dot=dot('roll_left','roll_right')) if roll else dict(left_right_axis_dot=dot('left','right'),up_down_axis_dot=dot('up','down'),left_up_axis_dot=dot('left','up'))
    return dict(capture_span_s=(last-first)/1e9,holds=result,pair_metrics=pairs,
                selected_production_mapping=None,optical_tilt='unvalidated',roll='unvalidated')
