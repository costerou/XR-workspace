"""Seeded all-kind wire fuzz + real supervisor snapshots; functional gates are separate.

The decoder test must run with CONTROL_WIRE_CHECK. Generation-only tests are CPU
Python and contact no compositor/service/device. A category label is wire data,
never proof that its actions work.
"""
import copy
import json
import os
import random
import subprocess
from tools.native_spike.options_menu import CATEGORIES, KINDS, validate_snapshot
from tools.native_spike.view import ViewControl, ViewState
from tools.native_spike.video import VideoControl
from tools.native_spike.options_editors import NativeEditors


def actual_snapshots(directory):
    (directory/'Été_映画.mp4').touch()
    c=ViewControl(ViewState(['XR-1','XR-3']),'run','session',lambda n:None,None,None)
    c.options_video=VideoControl(c,threads=False)
    c.options_editor=NativeEditors(c,directory)
    c.state.sizes=[dict(width=1920,height=1080,scale=1.0)]*2
    states=[]
    def save():
        state=c.options.snapshot();validate_snapshot(state);states.append(state)
    def row(id):
        index=next(i for i,r in enumerate(c.options.snapshot()['rows']) if r['id']==id)
        c.options_intent('options_focus',focus=index);c.options_key('Return');save()
    c.options_intent('options_open',monitor='XR-3');save()
    for scope in ('monitor','workspace'):
        for category in CATEGORIES:
            c.options_intent('options_scope',scope=scope)
            c.options_intent('options_category',category=category);save()
            # These are real focused header states, not arbitrary negative ints.
            for focus in (-4,-3,-2,-1):
                c.options_intent('options_focus',focus=focus);save()
            rows=c.options.snapshot()['rows']
            if rows:
                c.options_intent('options_focus',focus=len(rows)-1);save()
                c.options_key('PageDown');save();c.options_key('PageUp');save()
    c.options_intent('options_scope',scope='workspace')
    c.options_intent('options_category',category='video')
    row('video_browse')
    assert c.options_editor.kind=='file'
    assert any('Été_映画' in r['label'] for r in states[-1]['rows'])
    c.options_intent('options_focus',focus=len(states[-1]['rows'])-1);save()
    assert states[-1]['scroll']>0
    c.options_key('Escape');save()
    assert c.options_editor.kind is None and c.options.snapshot()['open']
    row('video_play')
    assert c.options_editor.kind=='url'
    # Exercise buffered native text snapshots only; never Submit a network URL.
    c.options_text('https://例子.invalid/Été?q=映画');save()
    c.options_key('Escape');save()
    for id,category in [('stop','session'),('layout_reset','layout')]:
        c.options_intent('options_category',category=category)
        row(id)
        assert c.options.confirmation is not None
        assert {r['id'] for r in states[-1]['rows']}=={'confirmation.apply','confirmation.cancel'}
        c.options_key('Escape');save()
    c.options_intent('options_close');save()
    assert not states[-1]['open']
    return states


def seeded_pairs():
    rng=random.Random(0xB1C104)
    for n in range(240):
        kind=KINDS[n%len(KINDS)]
        row=dict(id=f'row{n}',label='Signed orbit é',scope='monitor',kind=kind,
                 value='λ' if kind=='text' else 'false' if kind=='toggle' else str(n),enabled=kind!='readonly')
        if kind=='range':row.update(minimum=-1800,maximum=1800,step=rng.choice([.1,1,5]),unit='0.1 deg')
        if kind=='choice':row.update(value='Été',choices=['Été','映画','Other'])
        state=dict(open=True,namespace='session',monitor=rng.choice(['XR-1','XR-3']),scope='monitor',
                   category=CATEGORIES[n%len(CATEGORIES)],focus=(-4,-3,-2,-1,0)[n%5],
                   scroll=rng.choice([0,38,304,4096]),revision=rng.choice([0,2**32-1,rng.randrange(2**32)]),
                   status=('idle','pending','applied','error')[(n//6)%4],message='',rows=[row])
        validate_snapshot(state)
        bad=copy.deepcopy(state);mutation=(n//6)%12
        if mutation==0:bad['rows'][0]['step']=float('nan')
        elif mutation==1:bad['rows'][0]['label']='é'*49
        elif mutation==2:bad['namespace']='another-session'
        elif mutation==3:bad['rows'].append(copy.deepcopy(row))
        elif mutation==4:bad['focus']=True
        elif mutation==5:bad['rows'][0]['kind']='dropdown'
        elif mutation==6:bad['rows'][0]['choices']=['x']*33
        elif mutation==7:bad['rows'][0]['unknown']='ignored?'
        elif mutation==8:bad['scroll']=4097
        elif mutation==9:bad['message']='é'*129
        elif mutation==10:bad['status']='forwarded'
        else:bad['rows'][0]['enabled']=1
        yield state,bad


def test_real_snapshot_generation_covers_actual_rows_children_and_headers(tmp_path):
    states=actual_snapshots(tmp_path)
    assert {s['category'] for s in states}==set(CATEGORIES)
    assert {s['scope'] for s in states}=={'monitor','workspace'}
    assert {r['kind'] for s in states for r in s['rows']}==set(KINDS)
    assert {-4,-3,-2,-1} <= {s['focus'] for s in states}
    assert any(s['scroll']>0 for s in states)
    assert any('confirmation.apply' in [r['id'] for r in s['rows']] for s in states)


def test_seeded_snapshots_and_corruptions(tmp_path):
    pairs=list(seeded_pairs());real=actual_snapshots(tmp_path)
    assert {p[0]['rows'][0]['kind'] for p in pairs}==set(KINDS)
    states=[p for pair in pairs for p in pair]+real
    wire=b''.join((json.dumps(dict(schema=1,run_id='run',session='session',request_id=i,
                                    op='options_menu',payload=p))+'\n').encode() for i,p in enumerate(states,1))
    tool=os.environ['CONTROL_WIRE_CHECK']
    result=subprocess.run([tool,'run','session'],input=wire,capture_output=True,check=True,timeout=20)
    records=[json.loads(line) for line in result.stdout.splitlines()]
    assert records[-1]==dict(accepted=len(pairs)+len(real),refused=len(pairs)),records
    # Every corrupt frame is isolated; every following real/valid frame survives.
    assert [r['id'] for r in records[:-1]]==list(range(2,2*len(pairs)+1,2))
