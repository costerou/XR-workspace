"""C1 internal URL/clipboard ingress with real video owner and offline web API fakes."""
import pytest
import test_web_video as legacy_web
from tools.native_spike import xr_control
from test_menu_end_to_end import MenuDriver
from test_release_internal_text import insert,press


@pytest.mark.parametrize('entry',['url','clipboard'])
@pytest.mark.parametrize('refused',[False,True])
def test_c1_internal_web_entry_preserves_pending_or_specific_worker_error(entry,refused,monkeypatch):
    fixture=legacy_web.WebControlTests();fixture.setUp()
    c,v=fixture.view,fixture.v;c.options_video=v
    # Explicit offline API seam: neither live clipboard nor network is accessed.
    url='https://cdn.example.com/'+('forbidden.mp4' if refused else 'clip.mp4')
    monkeypatch.setattr(xr_control,'read_clipboard',lambda:url)
    d=MenuDriver(c);opened=None
    try:
        d.enter(c.state.names[-1],'workspace','video')
        if entry=='clipboard':press(d,'video_paste')
        else:
            press(d,'video_play');press(d,'editor.clear');insert(d,url)
            assert c.options_editor.text==url
            press(d,'editor.submit')
        opened=fixture.poll();d.add(opened);d.add(c.poll());d.native()
        assert fixture.probed and fixture.probed[0][0]==url
        if refused:
            assert not v.active
            assert c.options.snapshot()['status']=='error',c.options.snapshot()
            assert '403' in c.options.snapshot()['message']
        else:
            assert v.active and c.video_active
            assert fixture.relays and not fixture.relays[0].stopped
            assert c.options.snapshot()['status']=='pending'
        d.teardown()
    finally:
        v.on_message(dict(op='video_exit',payload={}))
        for thread in v._bind_threads:thread.join(2)
        fixture.doCleanups()


def test_c1_empty_clipboard_reports_error_without_starting_worker(monkeypatch):
    fixture=legacy_web.WebControlTests();fixture.setUp()
    c=fixture.view;c.options_video=fixture.v
    monkeypatch.setattr(xr_control,'read_clipboard',lambda:None)
    d=MenuDriver(c)
    try:
        d.enter(c.state.names[-1],'workspace','video');press(d,'video_paste')
        assert c.options.snapshot()['status']=='error'
        assert 'empty' in c.options.snapshot()['message'].lower()
        assert not fixture.probed and not fixture.relays
        d.native();d.teardown()
    finally:fixture.doCleanups()
