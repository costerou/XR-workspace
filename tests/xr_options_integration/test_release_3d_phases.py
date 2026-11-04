"""C1 3D intent through real worker, fake engine/SBS, truthful phase/readback."""
import json
import pytest
from tools.native_spike.view import ViewControl,ViewState
from tools.native_spike import immersive
from tools.native_spike.supervisor import Supervisor
from test_immersive import FakeEngine,FakeSbs,wait_for
from test_menu_end_to_end import MenuDriver


@pytest.mark.parametrize('scope,id',[('monitor','immersive_monitor'),('workspace','immersive_all')])
@pytest.mark.parametrize('failure',[False,True])
def test_c1_3d_desired_engine_physical_and_native_drawing_remain_distinct(scope,id,failure):
    log=[];notes=[]
    engine=FakeEngine(log,fail=immersive.EngineError('engine failed at authoritative startup') if failure else None)
    run=immersive.ImmersiveRun(engine,FakeSbs(log))
    c=ViewControl(ViewState(['XR-1','XR-3']),'run','session',lambda n:None,None,None)
    c.immersive_run=run;c.notify=notes.append
    d=MenuDriver(c)
    try:
        d.enter('XR-3',scope,'3d');d.focus(id);d.key('Return')
        wait_for(lambda:(d.add(c.poll()), c.immersive_failed if failure else c.immersive_stereo)[1], 'engine/SBS owner result')
        d.native()
        if failure:
            assert c.options.snapshot()['status']=='error',c.options.snapshot()
            assert 'engine' in c.options.snapshot()['message'].lower()
            assert not c.immersive_stereo
            assert not any(item[0]=='enter' for item in log)
        else:
            assert c.options.snapshot()['status']=='pending', 'engine/SBS success cannot prove native drawing'
            assert engine.up
            assert log.index(next(item for item in log if item[0]=='start')) < log.index(('enter',))
        # Restart never grants a synthetic drawing/physical receipt.
        d.teardown()
    finally:run.close()
