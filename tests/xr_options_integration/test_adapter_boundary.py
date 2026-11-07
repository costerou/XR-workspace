"""Exercise the shared adapter API using the real authority, without starting a run."""
import importlib
import pytest
from tools.native_spike.view import ViewControl, ViewState


@pytest.mark.parametrize('scope', ['monitor', 'workspace'])
@pytest.mark.parametrize('category', ['3d', 'stabilization', 'layout', 'view', 'video', 'input', 'keybinds', 'windows', 'privacy', 'help', 'session'])
def test_rows_are_scoped_and_use_stable_monitor(scope, category):
    actions = importlib.import_module('tools.native_spike.options_actions')
    control = ViewControl(ViewState(['XR-1', 'XR-3']), 'run', 'session', None, None, None)
    rows = actions.options_rows(control, scope, 'XR-3', category)
    assert isinstance(rows, list) and len(rows) <= 64
    assert len({r['id'] for r in rows}) == len(rows)
    assert all(r['scope'] == scope for r in rows)


def test_unknown_action_cannot_be_reported_applied():
    actions = importlib.import_module('tools.native_spike.options_actions')
    control = ViewControl(ViewState(['XR-1']), 'run', 'session', None, None, None)
    try:
        result = actions.options_apply(control, '__unknown_qa_row__', None, 'XR-1', 'monitor')
    except (ValueError, KeyError):
        return
    assert result is not True
    assert not isinstance(result, dict) or result.get('status') != 'applied'
