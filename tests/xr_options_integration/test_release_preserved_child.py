"""C1 entry into real placement child, then existing modal input/owner/persistence.

Owner cases run Python only. The separate native-wire companion is for the lead's
combined native gate; no native process is needed for owner assertions.
"""
import copy
import pytest
from tools.native_spike import view,profiles,geometry
from test_menu_end_to_end import MenuDriver

OPS=['menu_field_prev','menu_field_next','menu_desktop_next','menu_desktop_prev',
     'menu_inc','menu_dec','menu_inc_big','menu_dec_big','menu_reset','menu_back']


def exercise(op,tmp_path):
    placements=[dict(geometry.DEFAULT_PLACEMENT),dict(geometry.DEFAULT_PLACEMENT,x=100,y=20,curvature=10)]
    store=profiles.PlacementStore(tmp_path/'profiles.json','beast')
    store.save_desktops([dict(geometry.DEFAULT_DESKTOP)]*2,placements,[0,0])
    c=view.ViewControl(view.ViewState(['XR-1','XR-3'],placements=placements),'run','session',lambda n:None,
                       profiles.ProfileStore(tmp_path/'profiles.json','beast'),None,placements=store)
    d=MenuDriver(c);d.enter('XR-3','monitor','placement');d.focus('menu_toggle');d.key('Return')
    assert c.state.menu_open and not c.options.keyboard_owned
    before=copy.deepcopy(c.state.placements);field=c.state.menu_field;desktop=c.state.menu_desktop
    d.intent(op)
    if op=='menu_back':assert not c.state.menu_open
    elif op.startswith('menu_field_'):assert c.state.menu_field!=field and c.state.placements==before
    elif op.startswith('menu_desktop_'):assert c.state.menu_desktop!=desktop and c.state.names[c.state.menu_desktop]=='XR-1'
    elif op=='menu_reset':assert c.state.placements[desktop]==geometry.DEFAULT_PLACEMENT and c.state.placements[0]==before[0]
    else:assert c.state.placements[desktop]!=before[desktop] and c.state.placements[0]==before[0]
    if c.state.menu_open:d.intent('menu_back')
    c.flush()
    assert not c.state.menu_open and not c.options.keyboard_owned
    # Next renderer receives exactly the remembered live owner state, never a
    # second options persistence store. Existing profile loader checks fallback.
    if op in ['menu_inc','menu_dec','menu_inc_big','menu_dec_big','menu_reset']:
        assert profiles.load_profile(tmp_path/'profiles.json','beast')['placements']==c.state.placements
    return d


@pytest.mark.parametrize('op',OPS)
def test_placement_child_owner_input_and_close(op,tmp_path):exercise(op,tmp_path).teardown()


@pytest.mark.parametrize('op',OPS)
def test_placement_child_native_wire(op,tmp_path):
    d=exercise(op,tmp_path);d.native();d.teardown()


@pytest.mark.parametrize('strength',[50,100,150])
def test_c1_soft_standard_enhanced_values_use_actual_backend_and_save(strength,tmp_path):
    c=view.ViewControl(view.ViewState(['XR-1','XR-3']),'run','session',lambda n:None,
                       profiles.LayoutStore(tmp_path/'layout.json'),None)
    d=MenuDriver(c);d.enter('XR-3','workspace','3d');row=d.focus('immersive_strength')
    assert row['minimum']==0 and row['maximum']==200 and row['unit']=='%'
    if strength==100:
        d.key('Right');d.key('Left')  # prove activation/readback, not initial default
    else:
        for _ in range(abs(strength-100)):d.key('Left' if strength<100 else 'Right')
    d.add(c.poll())
    assert c.state.immersive['strength']==strength
    assert c.settings.load()['immersive']['strength']==strength
    assert c.immersive_run is None and not c.immersive_stereo
    assert c.options.snapshot()['status']!='error',c.options.snapshot()
    d.teardown()


@pytest.mark.parametrize('op',['menu_ultrawide','menu_add','menu_delete'])
def test_c1_placement_child_output_keys_reach_real_broker(op,tmp_path):
    import unittest
    from test_desktops import Run,SupervisorStandIn
    cleanup=unittest.TestCase();run=Run(cleanup)
    c=view.ViewControl(view.ViewState(run.manager.names()),'run','session',lambda n:None,
                       profiles.LayoutStore(tmp_path/'layout.json'),None)
    c.desktops=run.manager;c.attach_supervisor(SupervisorStandIn());c.state.sizes=run.manager.desktops()
    d=MenuDriver(c)
    try:
        d.enter('XR-3','monitor','placement');d.focus('menu_toggle');d.key('Return')
        assert c.state.menu_open and c.state.menu_desktop==2
        before=copy.deepcopy(run.manager.desktops())
        d.intent(op)
        if op=='menu_delete':
            assert c.state.remove_armed
            assert run.manager.desktops()==before
            d.intent(op)
        d.add(c.poll())
        if op=='menu_add':assert len(run.manager.names())==4
        elif op=='menu_delete':assert run.manager.names()==['XR-1','XR-2']
        else:assert run.manager.desktops()[2]['width']!=before[2]['width']
        assert not c.state.desktops_busy
        d.teardown()
    finally:run.manager.close();cleanup.doCleanups()
