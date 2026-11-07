import importlib.util
from pathlib import Path
import pytest

p=Path(__file__).resolve().parents[2]/'tools/xr_options_evidence/record_routes.py'
spec=importlib.util.spec_from_file_location('_record_routes',p)
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)


@pytest.mark.parametrize('body,status', [('', 'passed'),('<failure/>','failed'),('<error/>','failed'),('<skipped/>','skipped')])
def test_executed_partial_route_never_promotes_full_release(body,status):
    ledger={'rows':[{'id':'video_play','implementation_status':'pending_integration'}]}
    cases={'video_play':{'tests':['actual::owner::scenario'],'exception':None}}
    xml=f'<testsuite><testcase name="test_functional_preserved_route[video_play]">{body}</testcase></testsuite>'.encode()
    out=r.record(ledger,xml,cases)['rows'][0]
    assert out['functional_owner_evidence']['status']==status
    assert out['implementation_status']=='pending_integration'


def test_unexecuted_route_is_not_evidence():
    out=r.record({'rows':[{'id':'x'}]},b'<testsuite/>',{'x':{'tests':[],'exception':None}})
    assert out['rows'][0]['functional_owner_evidence']['status']=='not_run'
