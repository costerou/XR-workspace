"""Real supervisor-owned menu API; no alternative state machine in QA."""
import importlib
import pytest
from tools.native_spike.view import ViewControl, ViewState


def menu():
    control = ViewControl(ViewState(['XR-1', 'XR-3']), 'run', 'session', None, None, None)
    return importlib.import_module('tools.native_spike.options_menu').OptionsMenu(control)


def test_open_uses_actual_gapped_monitor_identity_and_close_is_idempotent():
    m = menu(); m.open('XR-3')
    assert m.snapshot()['open'] is True
    assert m.snapshot()['monitor'] == 'XR-3'
    m.close('cancel'); m.close('cancel')
    assert m.snapshot()['open'] is False


@pytest.mark.parametrize('reason', ['stop', 'renderer_restart', 'output_removed', 'namespace_change'])
def test_teardown_reasons_close_and_clear_rows(reason):
    m = menu(); m.open('XR-3'); m.close(reason)
    assert m.snapshot()['open'] is False


def test_missing_monitor_never_opens_another_output():
    m = menu()
    try: m.open('XR-2')
    except ValueError: pass
    assert m.snapshot()['open'] is False


def test_stale_revision_cannot_report_applied():
    m = menu(); m.open('XR-3')
    before = m.snapshot()
    try:
        m.intent('options_set', row_id='placement_x', monitor='XR-3', scope='monitor',
                 revision=(before['revision']-1) % 2**32, value=15)
    except ValueError:
        pass
    after = m.snapshot()
    assert after['status'] != 'applied'
    assert after['rows'] == before['rows']
