"""Actual C1 file child -> VideoControl worker -> typed failure/readback.

Local temp files and metadata fake only; no external picker, network or GPU.
"""
import json
import pytest
from tools.native_spike.view import ViewControl,ViewState
from tools.native_spike.video import VideoControl
from tools.native_spike.options_editors import NativeEditors
from tools.native_spike.supervisor import Supervisor
from test_video import info
from test_menu_end_to_end import MenuDriver


@pytest.mark.parametrize('removed_before_submit',[False,True])
def test_c1_file_child_keeps_worker_failure_and_never_applies_from_typed_report_alone(tmp_path,removed_before_submit):
    path=tmp_path/'clip.mp4';path.write_bytes(b'v'*100)
    c=ViewControl(ViewState(['XR-1','XR-3']),'run','session',lambda n:None,None,None)
    v=c.options_video=VideoControl(c,probe=lambda fd:info(w=4096,h=4096,codec='hevc'),threads=False)
    c.options_editor=NativeEditors(c,tmp_path)
    d=MenuDriver(c);d.enter('XR-3','workspace','video');d.focus('video_browse');d.key('Return')
    assert c.options_editor.kind=='file'
    row=next(r['id'] for r in c.options.snapshot()['rows'] if r['label']=='clip.mp4')
    if removed_before_submit:path.unlink()
    d.focus(row);d.key('Return')
    opened=v.poll();d.add(opened);d.add(c.poll())
    try:
        d.native()
        if removed_before_submit:
            assert c.options.snapshot()['status']=='error',c.options.snapshot()
            assert c.options.snapshot()['message'] and 'timed out' not in c.options.snapshot()['message'].lower()
            assert not v.active
        else:
            assert v.active and c.video_active
            assert c.options.snapshot()['status']=='pending'
            # Explicit fake native backend update proves typed observation alone
            # is insufficient without the correlated actual renderer receipt.
            native=dict(opened=1,player=dict(state='playing'),format_applied=dict(v.format),
                        playback=dict(state='playing',paused=False,position_s=0,duration_s=100,
                                      revision=1,command_replies=0,command_errors=0,command_error='',error=''),
                        quality_readback=dict(filter_requested=v.quality()['filter'],deband_requested=v.quality()['deband'],
                                             filter_applied=v.quality()['filter'],deband_applied=v.quality()['deband']))
            watch=Supervisor(None,None,control=c)
            watch.status_bytes((json.dumps(dict(owner_readback=dict(video=native,immersive=None,outputs=[])))+'\n').encode())
            assert c.owner_readback is not None, 'native fixture rejected by authoritative schema'
            assert c.owner_readback['video']==native
            d.add(v.poll());d.add(c.poll());d.native()
            assert c.options.snapshot()['status']=='pending', 'async native frame was never received/applied by a renderer'
        d.teardown()
    finally:
        if hasattr(opened,'close_fds'):opened.close_fds()
        v.on_message(dict(op='video_exit',payload={}))
        for thread in v._bind_threads:thread.join(2)
