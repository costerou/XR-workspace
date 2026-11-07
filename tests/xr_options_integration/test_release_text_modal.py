"""V4 real modal dispatch and compositor-reported layout, no live subscriptions."""
import json
import pytest
from tools.native_spike import keybinds as kb
from tools.native_spike.view import ViewControl, ViewState
from tools.native_spike.options_editors import NativeEditors
from tools.native_spike.options_text import RuntimeText


def control():
    return ViewControl(ViewState(['XR-1','XR-3']), 'run','session',lambda n: None,None,None)


class Keyboard:
    def __init__(self, layout, group=0):
        self.data=dict(main=True,rules='evdev',model='pc105',layout=layout,
                       variant='',options='',active_layout_index=group)
        self.queries=[]
    def _request(self, request):
        assert request=='j/devices'
        self.queries.append(request)
        return json.dumps(dict(keyboards=[self.data]))


@pytest.mark.parametrize('layout,group,mods,expected', [
    ('us',0,0,'a'), ('us',0,1,'A'), ('us',0,2,'A'),
    ('de',0,0,'z'), ('us,ru',1,0,'ф'), ('us,ru',1,1,'Ф')])
def test_missing_authoritative_keyboard_state_fails_closed_through_real_dispatch(layout, group, mods, expected):
    c=control(); c.options_editor=NativeEditors(c)
    c.options.open('XR-3'); c.options_editor.open('url'); c.options.refresh()
    client=Keyboard(layout,group); c.options_key_text=RuntimeText(c,client)
    code=29 if layout=='de' else 38
    try:
        c.on_message(dict(op='binds_key',payload=dict(mods=mods,code=code)))
        assert c.options_editor.text=='', 'keyboard names/heldmods are not authoritative lock/group state'
        data=c.options.snapshot()
        notice=data['message']+' '.join(r['label']+' '+r['value'] for r in data['rows'])
        assert any(w in notice.lower() for w in ('unavailable','on-screen','onscreen','hardware')), notice
        c.options_key('Escape')
        assert c.options_editor.text_input is None
        assert c.options.snapshot()['open']  # innermost Escape consumed once
    finally: c.options_editor.close()


def test_native_keybind_child_capture_escape_and_keyboard_reacquisition(tmp_path):
    from test_keybinds import FakeClient, ARGS
    c=control(); client=FakeClient(); subscriptions=[]
    c.options_binds=subscriptions.append
    c.keys=kb.BindControl(c,kb.KeyMap(),tmp_path/'binds.json',client=client,bind_args=ARGS)
    try:
        c.options_intent('options_open',monitor='XR-3')
        c.options_intent('options_scope',scope='workspace')
        c.options_intent('options_category',category='keybinds')
        d=c.options.snapshot()
        c.options_intent('options_activate',row_id='binds_toggle',monitor='XR-3',scope='workspace',revision=d['revision'],value='')
        c.keys.stop(); c.keys.stopped=False
        assert c.keys_open
        assert not c.options.keyboard_owned
        for t in list(c._menu_threads): t.join(2)
        assert subscriptions[-1] is False
        c.on_message(dict(op='binds_key',payload=dict(mods=0,code=36)))
        assert c.keys.capturing
        c.on_message(dict(op='binds_key',payload=dict(mods=0,code=9)))
        assert not c.keys.capturing and c.keys_open
        c.on_message(dict(op='binds_key',payload=dict(mods=0,code=9)))
        assert not c.keys_open
        c.poll()
        assert c.options.snapshot()['open']
        assert c.options.keyboard_owned
        for t in list(c._menu_threads): t.join(2)
        assert subscriptions[-1] is True
        c.on_message(dict(op='binds_key',payload=dict(mods=0,code=9)))
        assert not c.options.snapshot()['open']
    finally:
        c.keys.stop(); c.join_focus(2)
