"""Real asynchronous owners against authoritative stateful API fakes.

An accepted mutation without readback must fail. No renderer ACK substitutes
for these callbacks. Never connects to a compositor or service.
"""
import copy
import re
import threading
import pytest
from tools.native_spike.send import WindowSender
from tools.native_spike.privacy import PrivacyDisplay
from tools.native_spike.view import ViewControl, ViewState


class Backend:
    def __init__(self, obey=True, dpms=True):
        self.obey=obey; self.queries=[]
        self.window=dict(address='0x55aa01',monitor=0,pinned=False,workspace=dict(id=1))
        self.monitors=[dict(id=i,name=name,description='VITURE' if name=='DP-2' else '',
                            dpmsStatus=dpms if name=='DP-1' else True,disabled=False,mirrorOf='none',
                            activeWorkspace=dict(id=ws,name=str(ws)))
                       for i,(name,ws) in enumerate([('DP-1',1),('DP-2',2),('XR-1',31),('XR-3',51)])]
    def query(self,kind,argument=None,timeout=2):
        self.queries.append(kind)
        return copy.deepcopy({'activewindow':self.window,'clients':[self.window],
                              'monitors':self.monitors,'workspaces':[dict(id=m['activeWorkspace']['id'],monitor=m['name']) for m in self.monitors]}[kind])
    def mutate(self,command):
        match=re.search(r'workspace = "(\d+)"',command)
        assert match, command
        if self.obey:
            ws=int(match[1]); self.window['workspace']['id']=ws
            self.window['monitor']=next(m['id'] for m in self.monitors if m['activeWorkspace']['id']==ws)
        return 'ok'  # forwarding alone deliberately succeeds even when readback fails
    def set_dpms(self,name,on,keep=None):
        if self.obey:
            next(m for m in self.monitors if m['name']==name)['dpmsStatus']=on


def callback():
    event=threading.Event(); results=[]
    def done(success,detail):
        assert type(success) is bool
        assert isinstance(detail,str) and len(detail.encode())<=256
        results.append((success,detail)); event.set()
    return event,results,done


@pytest.mark.parametrize('obey',[True,False])
def test_send_callback_verifies_client_workspace_not_forwarding(obey):
    backend=Backend(obey); owner=WindowSender(backend,['XR-1','XR-3'],'DP-1',notify=None)
    event,results,done=callback()
    try:
        owner.request('send_glasses','XR-3',done=done)
        assert event.wait(2), 'missing owner completion; no generic timeout in menu may hide this'
        assert len(results)==1 and results[0][0] is obey
        if obey: assert backend.window['workspace']['id']==51
        else: assert results[0][1] and backend.window['workspace']['id']==1
    finally: owner.close()


@pytest.mark.parametrize('dpms',[True,False])
@pytest.mark.parametrize('obey',[True,False])
def test_privacy_callback_reads_both_off_and_on(dpms,obey):
    backend=Backend(obey,dpms); owner=PrivacyDisplay(backend,'DP-2','DP-1',notify=None)
    event,results,done=callback()
    try:
        owner.request(done=done)
        assert event.wait(2), 'missing authoritative DPMS completion'
        assert len(results)==1 and results[0][0] is obey
        actual=backend.query('monitors')[0]['dpmsStatus']
        assert actual is (not dpms if obey else dpms)
        if not obey: assert results[0][1]
    finally: owner.close()


def test_owner_completion_is_queued_and_stale_namespace_ignored():
    c=ViewControl(ViewState(['XR-1','XR-3']),'run','session',None,None,None)
    c.options.open('XR-3')
    token=c.options.begin_owner('placement_x','XR-3','monitor')
    before=c.options.snapshot()
    thread=threading.Thread(target=lambda:c.options.finish_owner(token,False,'backend rollback: é'*30))
    thread.start(); thread.join(1)
    assert not thread.is_alive()
    assert c.options.snapshot()==before, 'worker must enqueue, never mutate UI state'
    c.session='replacement'
    c.options.tick()
    assert not c.options.snapshot()['open']
    assert c.options.snapshot()['status']!='applied'
