"""Seeded mixed-flow roundtrips and corruptions through the actual C++ decoder."""
import copy
import json
import os
import random
import subprocess
from tools.native_spike.options_menu import CATEGORIES, validate_snapshot


def test_seeded_snapshots_and_corruptions():
    rng=random.Random(0xB1C1)
    frames=[]
    for n in range(200):
        lo=rng.randint(-1800,0);hi=rng.randint(1,1800)
        row=dict(id=f'row{n}',label='Signed orbit é',scope='monitor',kind='range',value=str(rng.randint(lo,hi)),minimum=lo,maximum=hi,step=1,unit='0.1 deg',enabled=True)
        state=dict(open=True,namespace='session',monitor=rng.choice(['XR-1','XR-3']),scope='monitor',category=CATEGORIES[n%len(CATEGORIES)],focus=rng.randint(-4,0),scroll=rng.randint(0,4096),revision=rng.randint(0,2**32-1),status=rng.choice(['idle','pending','applied','error']),message='',rows=[row])
        validate_snapshot(state)
        frames.append(state)
        bad=copy.deepcopy(state)
        kind=n%5
        if kind==0: bad['rows'][0]['step']=float('nan')
        elif kind==1: bad['rows'][0]['label']='é'*49
        elif kind==2: bad['namespace']='another-session'
        elif kind==3: bad['rows'].append(copy.deepcopy(row))
        else: bad['focus']=True
        frames.append(bad)
    wire=b''.join((json.dumps(dict(schema=1,run_id='run',session='session',request_id=i,op='options_menu',payload=p))+'\n').encode() for i,p in enumerate(frames,1))
    tool=os.environ['CONTROL_WIRE_CHECK']
    result=subprocess.run([tool,'run','session'],input=wire,capture_output=True,check=True,timeout=15)
    records=[json.loads(line) for line in result.stdout.splitlines()]
    assert records[-1]==dict(accepted=200,refused=200)
