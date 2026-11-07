"""Execute actual owner/preserved-path regressions for each inventory feature.

These are functional owner tests, not symbol checks. Results establish ONLY the
specific assertions in the mapped tests. Options ingress, readback and cleanup
release gates remain separate and must also pass before equivalence is claimed.
No live processes/services/devices are used by the selected scenarios.
"""
import importlib.util
import io
import json
from pathlib import Path
import sys
import unittest
import pytest
from tools.native_spike import view

ROOT=Path(view.__file__).resolve().parents[2]
CASES=json.loads((Path(__file__).with_name('route_cases.json')).read_text())
MODULES={}


def load(path):
    if path not in MODULES:
        spec=importlib.util.spec_from_file_location('_xr_qa_'+path.replace('/','_').replace('.','_'), ROOT/path)
        module=importlib.util.module_from_spec(spec)
        sys.modules[spec.name]=module
        spec.loader.exec_module(module)
        MODULES[path]=module
    return MODULES[path]


@pytest.mark.parametrize('route', sorted(CASES))
def test_functional_preserved_route(route,tmp_path):
    for node in CASES[route]['tests']:
        path,*parts=node.split('::')
        module=load(path)
        if len(parts)==1:  # existing pytest owner persistence functions
            getattr(module,parts[0])(tmp_path)
        else:
            case=getattr(module,parts[0])(parts[1])
            stream=io.StringIO()
            result=unittest.TextTestRunner(stream=stream,verbosity=2).run(case)
            assert result.testsRun==1 and not result.skipped, (node,stream.getvalue())
            assert result.wasSuccessful(), (node,stream.getvalue())
