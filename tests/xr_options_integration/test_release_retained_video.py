"""Actual retained video editor while C1 is unavailable during playback."""
import json
import pytest
from tools.native_spike.ipc import decode_control
import test_video as legacy_video
from test_menu_end_to_end import MenuDriver


@pytest.mark.parametrize('op,action',[
 ('video_pause','pause'),('video_seek_back','seek'),('video_seek_fwd','seek'),
 ('video_seek_back_big','seek'),('video_seek_fwd_big','seek'),('video_format','format'),
 ('video_up','controls'),('video_down','controls'),('video_controls','controls'),
 ('playback_menu_toggle','controls'),('video_exit','close')])
def test_playing_video_retains_native_action_ingress_and_frame(op,action):
    fixture=legacy_video.ControlTests();fixture.setUp()
    try:
        opened=fixture.open();c=fixture.view;v=fixture.v
        assert v.active and c.video_active
        # Opening C1 during playback is forbidden; retained editor remains active.
        before=c.state.view()
        c.options_intent('options_open',monitor=c.state.names[0])
        assert not c.options.snapshot()['open'] and v.active
        wire=(json.dumps(dict(schema=1,run_id='run1',session='s1',request_id=100,
                             op=op,payload={}))+'\n').encode()
        emitted=v.on_message(decode_control(wire))
        assert emitted, f'{op}: inert retained native action'
        frames=[json.loads(line) for line in bytes(emitted).splitlines()]
        assert any(f['payload'].get('action')==action for f in frames),frames
        d=MenuDriver(c);d.add(bytes(opened));d.add(emitted);d.native()
        if op=='video_exit': assert not c.video_active and c.state.view()==before
        else: assert c.video_active
        v.on_message(dict(op='video_exit',payload={}))
        assert not c.video_active
    finally:
        fixture.join_binds();fixture.doCleanups()


@pytest.mark.parametrize('setting',['filter','deband'])
def test_retained_quality_editor_opens_changes_and_cancels_without_seeking(setting):
    fixture=legacy_video.ControlTests();fixture.setUp()
    try:
        opened=fixture.open();v=fixture.v;c=fixture.view
        emitted=bytearray(bytes(opened))
        emitted.extend(v.on_message(dict(op='video_quality',payload={})))
        assert v.menu
        from tools.native_spike import quality
        settings=list(quality.SETTINGS)
        index=next(i for i,s in enumerate(settings) if s.key==setting)
        for _ in range(index):
            emitted.extend(v.on_message(dict(op='video_down',payload={})) or b'')
        before=v.quality()[setting]
        emitted.extend(v.on_message(dict(op='video_seek_fwd',payload={})) or b'')
        assert v.quality()[setting]!=before
        frames=[json.loads(line) for line in emitted.splitlines()]
        assert not any(f['payload'].get('action')=='seek' for f in frames)
        d=MenuDriver(c);d.add(emitted);d.native()
        v.on_message(dict(op='video_exit',payload={}))
        assert not v.menu and c.video_active
        v.on_message(dict(op='video_exit',payload={}))
        assert not c.video_active
    finally:
        fixture.join_binds();fixture.doCleanups()
