"""Actual menu tokens and video worker jobs, no renderer/GPU/compositor."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from tools.native_spike.view import ViewControl,ViewState
from tools.native_spike.video import VideoControl
from tools.native_spike.options_actions import owner_callback,video_request
from tools.native_spike.supervisor import Supervisor
from test_video import info


class DeferredVideo(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'clip.mp4'; self.path.write_bytes(b'v'*100)
        self.c=ViewControl(ViewState(['XR-1','XR-3']),'run','session',None,None,None)
        self.v=self.c.options_video=VideoControl(self.c,threads=False,probe=lambda fd:info(w=4096,h=4096,codec='hevc'))
        self.addCleanup(self.v.close)
        self.c.options.open('XR-3')
        self.c.options.intent('options_scope',scope='workspace')

    def start(self,path=None):
        video_request(self.c,'video_open',{'path':str(path or self.path)},'editor.play','XR-3')
        return self.v._owner_pending[-1]['done'].options_token

    def test_deferred_open_keeps_original_token_and_waits_actual_receipts(self):
        token=self.start()
        out=self.v.poll(); self.addCleanup(out.close_fds)
        ids={f['request_id'] for f in map(json.loads,out.splitlines()) if f['op']=='video'}
        self.assertTrue(ids); self.assertTrue(ids<=self.c.options.pending_ids)
        self.assertEqual(self.c.options._owner_active,token)
        report=dict(opened=1,player={'state':'playing'},format_applied=dict(self.v.format),
                    playback=dict(state='playing',paused=False,position_s=0,duration_s=100,revision=1,command_replies=0,command_errors=0,command_error='',error=''),
                    quality_readback=dict(filter_requested='catmull-rom',deband_requested=False,filter_applied='catmull-rom',deband_applied=False))
        watcher=Supervisor(None,None,control=self.c)
        watcher.status_bytes((json.dumps({'owner_readback':dict(video=report,immersive=None,outputs=[])})+'\n').encode())
        self.v.poll(); self.c.poll()
        self.assertEqual(self.c.options.snapshot()['status'],'pending')
        for rid in ids: watcher.status_bytes((json.dumps({'control_applied':rid})+'\n').encode())
        self.c.poll()
        self.assertEqual(self.c.options.snapshot()['status'],'applied')

    def test_replaced_token_drops_old_job_and_closes_descriptor(self):
        old=self.start()
        result=self.v._results.get_nowait(); fd=result[1]; self.v._results.put(result)
        new=owner_callback(self.c,'privacy_display','XR-3','workspace')
        self.assertNotEqual(old,new.options_token)
        self.v.poll()
        self.assertFalse(self.v.active); self.assertFalse(self.v.opening)
        self.assertEqual(self.c.options._owner_active,new.options_token)
        self.assertFalse(self.c.options.pending_ids)
        self.assertEqual(self.c.options.snapshot()['status'],'pending')
        with self.assertRaises(OSError): os.fstat(fd)

    def test_old_refusal_does_not_poison_replacement_open(self):
        self.start(self.path.parent/'missing.mp4')
        new=self.start()
        out=self.v.poll(); self.addCleanup(out.close_fds)
        self.assertTrue(self.v.active); self.assertIsNone(self.v.error)
        self.assertEqual(self.c.options._owner_active,new)
        self.assertTrue(self.c.options.pending_ids)

    def test_namespace_changed_drops_deferred_job_without_new_receipt(self):
        self.start(); result=self.v._results.get_nowait(); fd=result[1]; self.v._results.put(result)
        self.c.session='replacement'
        self.v.poll()
        self.assertFalse(self.v.active); self.assertFalse(self.c.options.pending_ids)
        with self.assertRaises(OSError): os.fstat(fd)

class DeferredStreamRestart(unittest.TestCase):
    def test_seek_restart_registers_original_token_or_discards_replaced_job(self):
        from test_web_video import WebControlTests
        for replaced in (False,True):
            with self.subTest(replaced=replaced):
                f=WebControlTests(); f.setUp()
                try:
                    f.op('video_open',{'url':'https://cdn.example.com/clip.mp4'}); f.poll()
                    f.sup.status={'video':'playing'}
                    c=f.view; c.options_video=f.v
                    c.options.open('XR-3'); c.options.intent('options_scope',scope='workspace')
                    c.owner_readback={'video':{'player':{},'playback':{'position_s':20,'revision':1,'command_replies':0,'command_errors':0}}}
                    video_request(c,'video_seek_fwd',{},'video_seek_fwd','XR-3')
                    old=f.v._stream_seek_owner['done'].options_token
                    if replaced:
                        newer=owner_callback(c,'privacy_display','XR-3','workspace')
                        self.assertNotEqual(old,newer.options_token)
                    before=len(f.relays)
                    f.clock.now+=0.5; out=f.poll()
                    if replaced:
                        self.assertEqual(len(f.relays),before)
                        self.assertEqual(c.options._owner_active,newer.options_token)
                        self.assertFalse(c.options.pending_ids)
                    else:
                        self.assertEqual(len(f.relays),before+1)
                        frames=[json.loads(line) for line in out.splitlines()]
                        emitted={frame['request_id'] for frame in frames if frame['op']=='video' and frame['payload'].get('action')=='open'}
                        self.assertTrue(emitted); self.assertTrue(emitted<=c.options.pending_ids)
                        self.assertEqual(c.options._owner_active,old)
                finally:
                    f.v.close(); f.doCleanups()
