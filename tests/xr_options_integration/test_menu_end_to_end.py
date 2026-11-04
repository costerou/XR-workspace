"""C1 ingress -> real key dispatch -> adapter/owner -> real native decoder.

CPU decoder acceptance is deliberately NOT fabricated as a renderer receipt.
The positive authoritative owner state and negative pending/error are asserted;
full renderer application remains the separate native receipt release gate.
"""
import copy
import json
import os
import subprocess
import pytest
from tools.native_spike import view, profiles
from tools.native_spike.ipc import decode_control


class MenuDriver:
    def __init__(self, c): self.c=c; self.wire=bytearray(); self.rid=0
    def add(self, raw):
        if raw: self.wire.extend(raw)
    def intent(self, op, **payload):
        self.rid+=1
        msg=dict(schema=1,run_id=self.c.run_id,session=self.c.session,request_id=self.rid,op=op,payload=payload)
        self.add(self.c.on_message(decode_control((json.dumps(msg)+'\n').encode())))
    def enter(self, monitor, scope, category):
        self.intent('options_open',monitor=monitor)
        self.intent('options_scope',scope=scope)
        self.intent('options_category',category=category)
        state=self.c.options.snapshot()
        assert state['monitor']==monitor and state['scope']==scope and state['category']==category
        assert all(r['scope']==scope for r in state['rows'])
    def focus(self,id):
        rows=self.c.options.snapshot()['rows']
        index=next((i for i,r in enumerate(rows) if r['id']==id),None)
        assert index is not None, f'inert/unreachable C1 row {id}'
        assert rows[index]['enabled'], f'unexpected disabled C1 row {id}'
        self.intent('options_focus',focus=index)
        return rows[index]
    def key(self,key): self.add(self.c.options_key(key))
    def native(self):
        tool=os.environ.get('CONTROL_WIRE_CHECK')
        assert tool,'CONTROL_WIRE_CHECK required; native decode cannot be silently skipped'
        done=subprocess.run([tool,self.c.run_id,self.c.session],input=bytes(self.wire),capture_output=True,check=True,timeout=15)
        records=[json.loads(line) for line in done.stdout.splitlines()]
        assert records[-1]['accepted']==self.wire.count(b'\n'),records
        assert records[-1]['refused']==0,records
        ids=[json.loads(line)['request_id'] for line in self.wire.splitlines()]
        assert all(a<b for a,b in zip(ids,ids[1:])),ids
    def teardown(self):
        self.c.renderer_restarted()
        assert not self.c.options.snapshot()['open']
        assert not self.c.options.keyboard_owned


# Real owner state access is per setting. No invented row/provider in this test.
SETTINGS=[
 ('placement_x','monitor','placement',lambda c:c.state.placements[1]['x']),
 ('placement_y','monitor','placement',lambda c:c.state.placements[1]['y']),
 ('placement_distance','monitor','placement',lambda c:c.state.placements[1]['distance']),
 ('placement_curvature','monitor','placement',lambda c:c.state.placements[1]['curvature']),
 ('stabilization','workspace','stabilization',lambda c:c.state.stabilization),
 ('prediction','workspace','stabilization',lambda c:c.state.prediction),
 ('background','workspace','environment',lambda c:c.state.environment['background']),
 ('background_brightness','workspace','environment',lambda c:c.state.environment['brightness']),
 ('immersive_preset','workspace','3d',lambda c:c.state.immersive['preset']),
 ('immersive_strength','workspace','3d',lambda c:c.state.immersive['strength']),
 ('immersive_plane','workspace','3d',lambda c:c.state.immersive['convergence']),
 ('immersive_monitor_strength','monitor','3d',lambda c:c.state.immersive.get('strengths',{}).get('XR-3',c.state.immersive['strength'])),
]


@pytest.mark.parametrize('id,scope,category,read', SETTINGS,ids=[s[0] for s in SETTINGS])
def test_c1_setting_keyboard_owner_readback_native_wire_and_restart(id,scope,category,read,tmp_path):
    store=profiles.LayoutStore(tmp_path/'layout.json')
    c=view.ViewControl(view.ViewState(['XR-1','XR-3']),'run','session',lambda n:None,store,None)
    if id=='background_brightness': c.set_environment(dict(background='grid',last='grid',brightness=60))
    d=MenuDriver(c);d.enter('XR-3',scope,category)
    row=d.focus(id); before=copy.deepcopy(read(c)); untouched=copy.deepcopy(c.state.placements[0])
    d.key('Right' if row['kind'] in ('range','choice') else 'Return')
    after=read(c)
    assert after!=before, f'{id}: key accepted but owner state did not change'
    assert c.state.placements[0]==untouched
    state=c.options.snapshot()
    # No renderer execution occurred in this CPU test, so receipt-dependent
    # operations must not claim success merely because Python state changed.
    if c.options.pending_ids: assert state['status']=='pending'
    assert state['status']!='error',state
    d.native()
    d.teardown()


@pytest.mark.parametrize('row_id,category,expected',[
 ('next','view',lambda c:c.state.selected==1),
 ('prev','view',lambda c:c.state.selected==0),
 ('zoom_in','view',lambda c:c.state.zoomed and c.state.selected==1),
 ('mode_follow','view',lambda c:c.state.zoomed and c.state.mode=='follow' and c.state.selected==1),
 ('mode_locked','view',lambda c:c.state.zoomed and c.state.mode=='locked' and c.state.selected==1),
 ('spacing_up','layout',lambda c:c.state.spacing>40),
 ('spacing_down','layout',lambda c:c.state.spacing<40),
 ('menu_toggle','placement',lambda c:c.state.menu_open and c.state.menu_desktop==1),
 ('typing_toggle','input',lambda c:c.state.typing),
 ('help','help',lambda c:c.help_visible),
])
def test_c1_action_key_reaches_actual_view_owner(row_id,category,expected,tmp_path):
    c=view.ViewControl(view.ViewState(['XR-1','XR-3'],spacing=40),'run','session',lambda n:None,profiles.LayoutStore(tmp_path/'layout.json'),None)
    # next/prev act on the current selected monitor through the existing owner.
    scope='monitor' if row_id in ('next','prev','zoom_in','mode_follow','mode_locked','menu_toggle') else 'workspace'
    d=MenuDriver(c); d.enter('XR-3',scope,category);d.focus(row_id);d.key('Return')
    assert expected(c), f'{row_id}: actual owner state is inert or target changed'
    d.native();d.teardown();c.join_focus(2)


def test_unavailable_privacy_cannot_forward_or_report_success():
    c=view.ViewControl(view.ViewState(['XR-1']),'run','session',None,None,None)
    d=MenuDriver(c);d.enter('XR-1','workspace','privacy')
    i=next(i for i,r in enumerate(c.options.snapshot()['rows']) if r['id']=='privacy_display')
    assert not c.options.snapshot()['rows'][i]['enabled']
    d.intent('options_focus',focus=i);d.key('Return')
    assert c.options.snapshot()['status']!='applied'
    d.native();d.teardown()


@pytest.mark.parametrize('kind', ['send','privacy_off','privacy_on'])
@pytest.mark.parametrize('obey',[True,False])
def test_c1_async_action_actual_owner_backend_completion_failure_and_cleanup(kind,obey):
    from test_release_owner_callbacks import Backend
    from tools.native_spike.send import WindowSender
    from tools.native_spike.privacy import PrivacyDisplay
    backend=Backend(obey,dpms=kind!='privacy_on')
    c=view.ViewControl(view.ViewState(['XR-1','XR-3']),'run','session',lambda n:None,None,None)
    if kind=='send':
        owner=WindowSender(backend,c.state.names,'DP-1',notify=None);c.sender=owner
        scope,category,id='monitor','windows','send_glasses'
    else:
        owner=PrivacyDisplay(backend,'DP-2','DP-1',notify=None);c.privacy=owner
        scope,category,id='workspace','privacy','privacy_display'
    d=MenuDriver(c)
    try:
        d.enter('XR-3',scope,category);d.focus(id);d.key('Return')
        for thread in owner._threads:
            thread.join(2);assert not thread.is_alive()
        d.add(c.poll());d.native()
        snap=c.options.snapshot()
        assert snap['status']==('applied' if obey else 'error'),snap
        if not obey: assert snap['message'] and 'timed out' not in snap['message'].lower()
        if kind=='send': assert backend.window['workspace']['id']==(51 if obey else 1)
        else: assert backend.query('monitors')[0]['dpmsStatus'] is ((kind=='privacy_on') if obey else (kind!='privacy_on'))
        d.teardown()
    finally: owner.close()


@pytest.mark.parametrize('id',['resolution','scale','ultrawide','add_monitor','remove_monitor'])
@pytest.mark.parametrize('refuse',[False,True])
def test_c1_output_transaction_actual_broker_readback_or_rollback(id,refuse):
    import unittest
    from test_desktops import Run, SupervisorStandIn
    cleanup=unittest.TestCase()
    run=Run(cleanup)
    c=view.ViewControl(view.ViewState(run.manager.names()),'run','session',lambda n:None,None,None)
    c.desktops=run.manager;c.attach_supervisor(SupervisorStandIn())
    c.state.sizes=run.manager.desktops()
    run.transport.refuse_reshape=refuse
    run.transport.refuse_remove=refuse
    d=MenuDriver(c)
    before=copy.deepcopy(run.manager.desktops())
    if id=='add_monitor' and refuse: run.transport.fail_index=3
    try:
        scope='workspace' if id=='add_monitor' else 'monitor'
        d.enter('XR-3',scope,'layout' if id=='add_monitor' else 'output')
        row=d.focus(id);d.key('Right' if row['kind']=='choice' else 'Return')
        if id=='remove_monitor':
            assert c.options.confirmation is not None
            d.focus('confirmation.apply');d.key('Return')
        d.add(c.poll());d.native()
        if refuse:
            assert run.manager.desktops()==before, 'failed output operation must rollback authoritative specs'
            assert c.options.snapshot()['status']=='error', c.options.snapshot()
        else:
            assert run.manager.desktops()!=before, 'C1 action never reached real broker transaction'
            assert c.options.snapshot()['status'] in ('pending','applied'), 'native child must retain owner completion state'
        d.teardown()
    finally:
        run.manager.close();cleanup.doCleanups()
