"""Export real supervisor snapshots for the offline native raster sequence.
No compositor, devices, sockets, persistence or installed services are used.
"""
import json
import sys
from tools.native_spike.view import ViewControl, ViewState
from tools.native_spike.video import VideoControl


def frames():
    c=ViewControl(ViewState(['XR-1','XR-3']),'run','session',None,None,None)
    c.options_video=VideoControl(c,threads=False)
    def snapshot(): return c.options.snapshot()
    yield snapshot()
    c.options_intent('options_open',monitor='XR-3');yield snapshot()
    c.options_intent('options_category',category='3d');yield snapshot()
    c.options_intent('options_scope',scope='workspace');yield snapshot()
    c.options_intent('options_category',category='stabilization');yield snapshot()
    d=snapshot()
    c.options_intent('options_set',row_id='stabilization',monitor='XR-3',scope='workspace',revision=d['revision'],value=25)
    yield snapshot()  # pending: deliberately no fabricated renderer receipt
    c.options_intent('options_category',category='environment');yield snapshot()
    c.options_intent('options_category',category='video');yield snapshot()
    d=snapshot()
    c.options_intent('options_activate',row_id='video_play',monitor='XR-3',scope='workspace',revision=d['revision'],value='')
    yield snapshot()
    c.options_text('https://example.invalid/video');yield snapshot()
    c.options_key('Escape');yield snapshot()
    c.options_intent('options_category',category='session');yield snapshot()
    c.options_intent('options_close');yield snapshot()

if __name__=='__main__':
    for i,state in enumerate(frames(),1):
        print(json.dumps(dict(schema=1,run_id='run',session='session',request_id=i,op='options_menu',payload=state),allow_nan=False))
