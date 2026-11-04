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
            if id in ('resolution','scale','ultrawide'):
                # BrokerTransport deliberately invalidates ownership on refused
                # reshape. Preserved safety contract keeps untrusted requested
                # specs for the guard; no bypass can manufacture a rollback.
                from tools.native_spike.journal import recover
                assert run.manager.desktops()!=before
                assert any(e['state']=='invalid' for e in run.transport.registry.values())
                assert not recover(run.journal,run.broker).complete
            else:
                assert run.manager.desktops()==before, 'trusted refusal must preserve authoritative specs'
            assert c.options.snapshot()['status']=='error', c.options.snapshot()
            assert c.options.snapshot()['message'] and 'timed out' not in c.options.snapshot()['message'].lower()
        else:
            assert run.manager.desktops()!=before, 'C1 action never reached real broker transaction'
            assert c.options.snapshot()['status'] in ('pending','applied'), 'native child must retain owner completion state'
        d.teardown()
    finally:
        run.manager.close();cleanup.doCleanups()


@pytest.mark.parametrize('id',['placement_flat','menu_reset'])
def test_c1_flat_reset_changes_only_target_via_native_placement_owner(id,tmp_path):
    from tools.native_spike.geometry import DEFAULT_PLACEMENT
    c=view.ViewControl(view.ViewState(['XR-1','XR-3'],placements=[dict(DEFAULT_PLACEMENT),dict(DEFAULT_PLACEMENT,x=250)]),
                       'run','session',lambda n:None,profiles.LayoutStore(tmp_path/'layout.json'),None)
    d=MenuDriver(c);d.enter('XR-3','monitor','placement');d.focus(id);d.key('Return')
    assert c.state.placements==[DEFAULT_PLACEMENT,DEFAULT_PLACEMENT]
    d.native();d.teardown()


@pytest.mark.parametrize('id',['roll_ccw','roll_cw','roll_level'])
def test_c1_roll_keys_use_actual_pointer_owner_and_target(id):
    from tools.native_spike.free_pointer import OverviewPointer
    c=view.ViewControl(view.ViewState(['XR-1','XR-3']),'run','session',lambda n:None,None,None)
    p=c.overview_pointer=OverviewPointer(c)
    try:
        c.set_roll_geometry([300,-600]);before=list(p.rolls)
        d=MenuDriver(c);d.enter('XR-3','monitor','placement');d.focus(id);d.key('Return')
        assert p.rolls[0]==before[0] and p.rolls[1]!=before[1]
        d.native();d.teardown()
    finally:p.close()


@pytest.mark.parametrize('id',['help_prev','help_next'])
def test_advertised_help_page_row_opens_native_help_and_reaches_a_page(id):
    c=view.ViewControl(view.ViewState(['XR-1','XR-3']),'run','session',lambda n:None,None,None)
    d=MenuDriver(c);d.enter('XR-3','workspace','help');d.focus(id);d.key('Return')
    assert c.help_visible, f'{id} advertised while no help child exists, but key activation is inert'
    d.native();d.teardown()


def test_c1_background_toggle_and_recenter_are_not_inert(tmp_path):
    c=view.ViewControl(view.ViewState(['XR-1','XR-3']),'run','session',lambda n:None,None,None)
    c.settings=profiles.LayoutStore(tmp_path/'layout.json')
    d=MenuDriver(c);d.enter('XR-3','workspace','environment');d.focus('environment_toggle');d.key('Return')
    assert c.options.snapshot()['status']!='error',c.options.snapshot()
    assert c.state.environment['background']!='none'
    d.enter('XR-3','workspace','view');d.focus('recenter');d.key('Return')
    frames=[json.loads(line) for line in d.wire.splitlines()]
    assert any(frame['op']=='recenter' for frame in frames)
    d.native();d.teardown()


@pytest.mark.parametrize('op,target',[('send_glasses_1',31),('send_glasses_2',41),('send_glasses_3',51),('send_glasses_4',61),('send_back',1)])
@pytest.mark.parametrize('obey',[True,False])
def test_c1_each_numbered_send_and_back_verifies_actual_target(op,target,obey):
    from test_release_owner_callbacks import Backend
    from tools.native_spike.send import WindowSender
    backend=Backend(obey)
    for name,workspace in [('XR-2',41),('XR-4',61)]:
        backend.monitors.append(dict(id=len(backend.monitors),name=name,activeWorkspace=dict(id=workspace,name=str(workspace))))
    if op=='send_back':backend.window.update(monitor=3,workspace=dict(id=51))
    c=view.ViewControl(view.ViewState(['XR-1','XR-2','XR-3','XR-4']),'run','session',lambda n:None,None,None)
    owner=c.sender=WindowSender(backend,c.state.names,'DP-1',notify=None)
    d=MenuDriver(c)
    try:
        d.enter('XR-3','workspace' if op=='send_back' else 'monitor','windows');d.focus(op);d.key('Return')
        for thread in owner._threads:thread.join(2);assert not thread.is_alive()
        d.add(c.poll());d.native()
        expected=target if obey else (51 if op=='send_back' else 1)
        assert backend.window['workspace']['id']==expected
        assert c.options.snapshot()['status']==('applied' if obey else 'error'),c.options.snapshot()
        d.teardown()
    finally:owner.close()


@pytest.mark.parametrize('count,id,category,scope',[(1,'remove_monitor','output','monitor'),(4,'add_monitor','layout','workspace')])
def test_c1_output_limits_disabled_and_cannot_be_activated(count,id,category,scope):
    import unittest
    from test_desktops import Run,D169
    cleanup=unittest.TestCase();run=Run(cleanup,[D169]*count)
    c=view.ViewControl(view.ViewState(run.manager.names()),'run','session',lambda n:None,None,None)
    c.desktops=run.manager;c.state.sizes=run.manager.desktops();d=MenuDriver(c)
    try:
        d.enter(c.state.names[-1],scope,category)
        index=next(i for i,r in enumerate(c.options.snapshot()['rows']) if r['id']==id)
        assert not c.options.snapshot()['rows'][index]['enabled']
        before=copy.deepcopy(run.manager.specs)
        d.intent('options_focus',focus=index);d.key('Return')
        assert run.manager.specs==before and not c.state.desktops_busy
        assert c.options.snapshot()['status']!='applied'
        d.native();d.teardown()
    finally:run.manager.close();cleanup.doCleanups()


def test_c1_reset_has_distinct_confirmation_and_authoritative_no_step_completion():
    import test_layout_reset as legacy_reset
    f=legacy_reset.ResetTests();f.setUp()
    try:
        f.start([legacy_reset.D169]*3,[legacy_reset.FLAT]*3,[0]*3)
        c=f.c;d=MenuDriver(c);d.enter('XR-3','workspace','layout');d.focus('layout_reset');d.key('Return')
        assert c.options.confirmation is not None and c.reset is None
        label=c.options.snapshot()['rows'][0]['label'].lower()
        assert 'migration' in label and 'physical fallback' not in label
        d.focus('confirmation.apply');d.key('Return')
        for delta in (0,.6,1.2,2):
            f.now=100+delta;d.add(c.poll(f.now))
        assert c.reset is None
        assert c.state.names==['XR-1','XR-2','XR-3']
        assert c.options.snapshot()['status'] in ('pending','applied'),c.options.snapshot()
        d.native();d.teardown()
    finally:f.doCleanups()


def test_c1_stop_requires_confirmation_and_uses_existing_unwind():
    c=view.ViewControl(view.ViewState(['XR-1']),'run','session',lambda n:None,None,None)
    d=MenuDriver(c);d.enter('XR-1','workspace','session');d.focus('stop');d.key('Return')
    assert c.options.confirmation is not None
    d.focus('confirmation.apply')
    with pytest.raises(KeyboardInterrupt):d.key('Return')
    d.native();d.teardown()
