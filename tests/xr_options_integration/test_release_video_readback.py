"""Retained video owner callbacks require typed native observation, never ACK alone."""
import copy
import json
import pytest
import test_video as legacy_video
from tools.native_spike.supervisor import Supervisor
from tools.native_spike import video
from test_menu_end_to_end import MenuDriver


def report():
    return dict(opened=1,player=dict(state='playing'),
                format_applied=dict(projection='360',stereo='tb',fov=360),
                quality_readback=dict(filter_requested='linear',deband_requested=False,
                                      filter_applied='linear',deband_applied=False),
                playback=dict(state='playing',paused=False,position_s=20,duration_s=100,
                              revision=1,command_replies=0,command_errors=0,command_error='',error=''))


def publish(watch,value):
    watch.status_bytes((json.dumps(dict(owner_readback=dict(video=value,immersive=None,outputs=[])))+'\n').encode())


@pytest.mark.parametrize('op',['video_pause','video_seek_back','video_seek_fwd','video_seek_back_big','video_seek_fwd_big','video_format'])
@pytest.mark.parametrize('outcome',['success','native_error'])
def test_video_owner_reports_only_after_observed_native_result(op,outcome):
    f=legacy_video.ControlTests();f.setUp();calls=[]
    try:
        opened=f.open();v=f.v;c=f.view;watch=Supervisor(None,None,control=c)
        before=report();publish(watch,before)
        emitted=v.on_message(dict(op=op,payload={}),done=lambda *a:calls.append(a))
        v.poll();assert calls==[], 'accepted/forwarded request cannot complete owner'
        actual=copy.deepcopy(before)
        actual['playback']['revision']=2
        if outcome=='native_error':
            actual['playback'].update(command_errors=1,command_error='Native command refused by player')
        elif op=='video_pause': actual['playback']['paused']=True;actual['playback']['state']='paused'
        elif op=='video_format': actual['format_applied']=dict(v.format)
        else:
            actual['playback']['position_s']=min(100,max(0,20+video.SEEK_S[op]))
            actual['playback']['command_replies']=1
        publish(watch,actual);v.poll()
        assert len(calls)==1 and calls[0][0] is (outcome=='success'),calls
        if outcome=='native_error': assert 'refused' in calls[0][1].lower()
        d=MenuDriver(c);d.add(opened);d.add(emitted);d.native()
        v.on_message(dict(op='video_exit',payload={}))
        assert not c.video_active
    finally:f.join_binds();f.doCleanups()


@pytest.mark.parametrize('setting,value',[('filter','nearest'),('deband',True)])
def test_quality_change_waits_for_applied_property_and_propagates_error(setting,value):
    f=legacy_video.ControlTests();f.setUp();calls=[]
    try:
        opened=f.open();v=f.v;c=f.view;watch=Supervisor(None,None,control=c)
        before=report();publish(watch,before)
        target={**v.quality(),setting:value}
        emitted=v._set_quality(target,done=lambda *a:calls.append(a))
        v.poll();assert calls==[]
        requested=copy.deepcopy(before)
        requested['quality_readback'].update(filter_requested=target['filter'],deband_requested=target['deband'])
        publish(watch,requested);v.poll();assert calls==[], 'desired quality cannot stand in for applied properties'
        actual=copy.deepcopy(requested)
        actual['quality_readback'].update(filter_applied=target['filter'],deband_applied=target['deband'])
        publish(watch,actual);v.poll();assert calls and calls[0][0] is True
        d=MenuDriver(c);d.add(opened);d.add(emitted);d.native()
    finally:f.join_binds();f.doCleanups()


def test_open_failure_is_backend_error_even_when_child_closed(tmp_path):
    f=legacy_video.ControlTests();f.setUp();calls=[]
    try:
        v=f.v
        v.on_message(dict(op='video_open',payload=dict(path=str(tmp_path/'missing.mp4'))),done=lambda *a:calls.append(a))
        v.poll()
        assert len(calls)==1 and calls[0][0] is False
        assert calls[0][1] and 'timed out' not in calls[0][1].lower()
    finally:f.join_binds();f.doCleanups()
