"""CPU-only release regressions: real owners, stateful backend fakes."""
import threading
import unittest
from tools.native_spike.privacy import PrivacyDisplay
from tools.native_spike.send import WindowSender
from test_privacy import Client as DpmsClient, desk
from test_send_window import Client as WindowClient
from test_immersive import FakeEngine, FakeSbs
from tools.native_spike.immersive import ImmersiveRun, EngineError


class Completions(unittest.TestCase):
    def result(self):
        event=threading.Event(); result=[]
        def done(success,detail): result.append((success,detail)); event.set()
        return event,result,done

    def test_privacy_wake_must_read_back(self):
        client=DpmsClient(desk(False),obey=False)
        owner=PrivacyDisplay(client,'DP-2','DP-1',notify=None)
        event,result,done=self.result()
        owner.request(done=done)
        self.assertTrue(event.wait(2)); self.assertFalse(result[0][0]); self.assertIn('on',result[0][1])
        owner.close()

    def test_privacy_off_and_on_success(self):
        owner=PrivacyDisplay(DpmsClient(),'DP-2','DP-1',notify=None)
        for expected in (False,True):
            event,result,done=self.result(); owner.request(done=done)
            self.assertTrue(event.wait(2)); self.assertTrue(result[0][0])
            self.assertEqual(owner.client.mons[0]['dpmsStatus'],expected)
        owner.close()

    def test_send_receipt_without_client_move_fails(self):
        class Client(WindowClient):
            def query(self,kind,*args,**kw):
                if kind=='clients': return [dict(self.window)]
                return super().query(kind,*args,**kw)
        owner=WindowSender(Client(),['XR-1'],'DP-1',notify=None)
        event,result,done=self.result(); owner.request('send_glasses','XR-1',done=done)
        self.assertTrue(event.wait(2)); self.assertFalse(result[0][0]); self.assertIn('readback',result[0][1])
        owner.close()

    def test_3d_engine_failure_completes_error(self):
        owner=ImmersiveRun(FakeEngine(fail=EngineError('model unavailable')),FakeSbs([]))
        event,result,done=self.result(); owner.request(['XR-1'],done=done)
        self.assertTrue(event.wait(2)); self.assertFalse(result[0][0]); self.assertIn('model unavailable',result[0][1])
        owner.close()

class OtherOwners(unittest.TestCase):
    def test_output_refusal_callback_is_error(self):
        import tempfile
        from test_desktops import Run
        from tools.native_spike.desktops import plan
        with tempfile.TemporaryDirectory() as tmp:
            run=Run(self)
            run.transport.refuse_reshape=True
            change=plan(run.manager.specs,[dict(x=0,y=0,distance=100,curvature=0)]*3,'ultrawide',0)
            result=[]
            run.manager.start(change,done=lambda ok,detail: result.append((ok,detail)))
            self.assertFalse(result[0][0]); self.assertTrue(result[0][1])

    def test_video_worker_failure_reaches_callback(self):
        from tools.native_spike.view import ViewControl,ViewState
        from tools.native_spike.video import VideoControl
        from unittest.mock import Mock
        c=ViewControl(ViewState(['XR-1']),'run','session',Mock(),Mock(),Mock())
        video=VideoControl(c,threads=False,notify=None)
        result=[]
        video.on_message({'op':'video_open','payload':{'path':'/no/such/file.mp4'}},done=lambda ok,detail: result.append((ok,detail)))
        video.poll()
        self.assertEqual(len(result),1); self.assertFalse(result[0][0]); self.assertTrue(result[0][1])

if __name__=='__main__': unittest.main()

class KeyboardState(unittest.TestCase):
    def test_runtime_honors_group_and_locked_caps(self):
        import json
        from types import SimpleNamespace
        from tools.native_spike.options_editors import NativeEditors
        from tools.native_spike.options_text import RuntimeText
        c=SimpleNamespace(keys=None)
        c.options_editor=NativeEditors(c); c.options_editor.open('url')
        class Client:
            def _request(self,request):
                return json.dumps({'keyboards':[dict(main=True,rules='evdev',model='pc105',layout='us,de',variant='',options='',active_layout_index=1)]})
        translate=RuntimeText(c,Client())
        with self.assertRaisesRegex(ValueError,'internal character'): translate(0,29)
        self.assertEqual(translate(0,29,state={'depressed':0,'latched':0,'locked':0,'group':1}),'z') # physical US Y, active German group
        self.assertEqual(translate(0,38,state={'depressed':0,'latched':0,'locked':2,'group':1}),'A')
        c.options_editor.close()

class ScreenText(unittest.TestCase):
    def test_pointer_controls_buffer_and_unicode_scalar(self):
        from types import SimpleNamespace
        from tools.native_spike.options_editors import NativeEditors
        c=SimpleNamespace(keys=None)
        editor=NativeEditors(c); editor.open('url')
        editor.apply('editor.char.lower','h'); editor.apply('editor.insert')
        self.assertEqual(editor.text,'h')
        editor.apply('editor.clear')
        for index,digit in enumerate('01f600'): editor.apply(f'editor.hex.{index}',digit)
        editor.apply('editor.unicode')
        self.assertEqual(editor.text,'😀')
        for index,digit in enumerate('00d800'): editor.apply(f'editor.hex.{index}',digit)
        with self.assertRaises(ValueError): editor.apply('editor.unicode')
        self.assertLessEqual(len(editor.rows()),64)

class ReadbackGates(unittest.TestCase):
    def video(self):
        from tools.native_spike.view import ViewControl,ViewState
        from tools.native_spike.video import VideoControl
        from test_immersive import Store
        c=ViewControl(ViewState(['XR-1']),'run','session',None,Store(),None)
        c.owner_readback={}
        v=VideoControl(c,threads=False)
        return c,v

    def test_inactive_quality_saved_vs_active_actual(self):
        c,v=self.video(); result=[]
        v._set_quality({'filter':'sharp','deband':True},done=lambda ok,detail:result.append((ok,detail)))
        c.owner_readback={'video':{'quality_readback':{'filter_requested':'sharp','deband_requested':True,'filter_applied':None,'deband_applied':None}}}
        v._poll_owner_readback()
        self.assertEqual(result,[(True,'Saved for next video')])
        v.active=True; result.clear()
        v._set_quality({'filter':'bilinear','deband':False},done=lambda ok,detail:result.append((ok,detail)))
        c.owner_readback={'video':{'quality_readback':{'filter_requested':'bilinear','deband_requested':False,'filter_applied':None,'deband_applied':None}}}
        v._poll_owner_readback(); self.assertFalse(result)
        c.owner_readback={'video':{'quality_readback':{'filter_applied':'bilinear','deband_applied':False}}}
        v._poll_owner_readback(); self.assertTrue(result[0][0])

    def test_video_pause_waits_actual_property_and_native_error(self):
        c,v=self.video(); v.active=True
        old={'playback':{'state':'playing','paused':False,'revision':1,'command_errors':0,'command_replies':0}}
        c.owner_readback={'video':old}; result=[]
        v.on_message({'op':'video_pause'},done=lambda ok,detail:result.append((ok,detail)))
        c.owner_readback={'video':{'playback':{**old['playback'],'command_replies':1}}}
        v._poll_owner_readback(); self.assertFalse(result)
        c.owner_readback={'video':{'playback':{**old['playback'],'command_errors':1,'command_error':'mpv command refused'}}}
        v._poll_owner_readback(); self.assertEqual(result,[(False,'mpv command refused')])

    def test_3d_engine_sbs_not_enough_without_drawing(self):
        owner=ImmersiveRun(FakeEngine(),FakeSbs([])); event=threading.Event(); result=[]
        owner.request(['XR-1'],done=lambda ok,detail:(result.append((ok,detail)),event.set()))
        from test_immersive import wait_for
        wait_for(lambda:owner.phase=='on')
        self.assertFalse(event.is_set())
        owner.observe_renderer({'available':True,'physical_sbs':True,'desktops':[{'monitor':'XR-1','drawing':False,'depth_ready':True}]})
        self.assertFalse(event.is_set())
        owner.observe_renderer({'available':True,'physical_sbs':True,'desktops':[{'monitor':'XR-1','drawing':True,'depth_ready':True}]})
        self.assertTrue(event.wait(1)); self.assertTrue(result[0][0]); owner.close()

class Rollback(unittest.TestCase):
    def test_partial_reshape_restores_authoritative_original(self):
        from test_desktops import Run
        from tools.native_spike.desktops import plan
        from copy import deepcopy
        run=Run(self); original=deepcopy(run.manager.specs)
        reshape=run.broker.reshape; calls=[]
        def fault_after_mutation(spec):
            reshape(spec); calls.append(spec['name'])
            if len(calls)==1: raise OSError('lost response after verified mutation')
        run.broker.reshape=fault_after_mutation
        change=plan(run.manager.specs,[dict(x=0,y=0,distance=100,curvature=0)]*3,'ultrawide',0)
        result=[]; run.manager.start(change,done=lambda ok,detail:result.append((ok,detail)))
        self.assertEqual(len(result),1); self.assertFalse(result[0][0]); self.assertIn('restored and verified',result[0][1])
        self.assertEqual(run.manager.specs,original)
        for spec in original: run.broker.verify(run.broker.inspect(spec['id']),spec)

class SupersedingRequests(unittest.TestCase):
    def test_callbackless_tail_override_cancels_pending_menu(self):
        gate=threading.Event(); owner=ImmersiveRun(FakeEngine(gate=gate),FakeSbs([]))
        result=[]
        try:
            owner.request(['XR-1'],done=lambda ok,detail:result.append((ok,detail)))
            owner.request([]) # existing Connected Tail/native bind API
            self.assertEqual(len(result),1)
            self.assertFalse(result[0][0]); self.assertIn('superseded',result[0][1])
            owner.observe_renderer({'enabled':False,'physical_sbs':False})
            self.assertEqual(len(result),1)
        finally: owner.close()

class CandidateFocus(unittest.TestCase):
    def test_visible_default_candidate_is_selected_without_choice_event(self):
        from types import SimpleNamespace
        from tools.native_spike.options_editors import NativeEditors
        editor=NativeEditors(SimpleNamespace(keys=None)); editor.open('url')
        editor.apply('editor.char.digits','9')
        displayed=next(r for r in editor.rows() if r['id']=='editor.char.punctuation')['value']
        self.assertEqual(displayed,':')
        self.assertTrue(editor.select_candidate('editor.char.punctuation'))
        editor.apply('editor.insert')
        self.assertEqual(editor.text,':')

class TerminalEventOrdering(unittest.TestCase):
    def test_failure_event_waits_for_completion_enqueue_without_holding_condition(self):
        import queue
        owner=ImmersiveRun(FakeEngine(fail=EngineError('deterministic failure')),FakeSbs([]))
        callback_entered=threading.Event(); release_callback=threading.Event()
        completions=queue.SimpleQueue()
        def done(success,detail):
            callback_entered.set()
            if not release_callback.wait(2): raise AssertionError('test callback was not released')
            completions.put((success,detail))
        try:
            owner.request(['XR-1'],done=done)
            self.assertTrue(callback_entered.wait(2))
            # A callback is deliberately paused before enqueue. take_events must
            # remain nonblocking, and must not expose its terminal event yet.
            self.assertTrue(owner._cond.acquire(timeout=1))
            owner._cond.release()
            self.assertFalse(any(e[0]=='failed' for e in owner.take_events()))
            release_callback.set()
            from test_immersive import wait_for
            observed=[]
            wait_for(lambda:(observed.extend(owner.take_events()),any(e[0]=='failed' for e in observed))[1])
            self.assertEqual(completions.get_nowait(),(False,'engine: deterministic failure'))
            self.assertTrue(completions.empty())
        finally:
            release_callback.set(); owner.close()
