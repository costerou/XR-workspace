"""Complete internal text path using real C1 key focus/activation, no OS injection."""
import pytest
from tools.native_spike.view import ViewControl, ViewState
from tools.native_spike.video import VideoControl
from tools.native_spike.options_editors import NativeEditors
from test_menu_end_to_end import MenuDriver


def choose(d,id,want):
    row=d.focus(id)
    assert row['kind']=='choice' and len(row['choices'])<=32
    assert want in row['choices']
    current=row['choices'].index(row['value']);target=row['choices'].index(want)
    right=(target-current)%len(row['choices']);left=(current-target)%len(row['choices'])
    for _ in range(min(right,left)):
        d.key('Right' if right<=left else 'Left')
    assert next(r for r in d.c.options.snapshot()['rows'] if r['id']==id)['value']==want


def press(d,id): d.focus(id);d.key('Return')


def scalar(d,code):
    for n,digit in enumerate(f'{code:06x}'): choose(d,f'editor.hex.{n}',digit)
    press(d,'editor.unicode')


def insert(d,text):
    for char in text:
        groups={k:v for k,v in NativeEditors.CHARSETS.items()}
        group=next((g for g,chars in groups.items() if char in chars),None)
        if group is None: scalar(d,ord(char))
        else: choose(d,'editor.char.'+group,char);press(d,'editor.insert')


def picker(root):
    c=ViewControl(ViewState(['XR-1','XR-3']),'run','session',lambda n:None,None,None)
    c.options_video=VideoControl(c,threads=False)
    c.options_editor=NativeEditors(c,root)
    d=MenuDriver(c);d.enter('XR-3','workspace','video');press(d,'video_browse')
    assert c.options_editor.kind=='file'
    return c,d


def test_unicode_folder_narrowing_error_recovery_via_internal_controls(tmp_path):
    child=tmp_path/'é';child.mkdir();(child/'clip.mp4').touch()
    c,d=picker(tmp_path)
    press(d,'editor.clear');insert(d,str(child)+'x')
    assert c.options_editor.directory==tmp_path, 'buffer must not change folder before Submit'
    press(d,'editor.submit')
    assert c.options.snapshot()['status']=='error'
    assert c.options_editor.directory==tmp_path
    press(d,'editor.backspace');press(d,'editor.submit')
    assert c.options_editor.directory==child
    assert any(r['label']=='clip.mp4' for r in c.options.snapshot()['rows'])
    assert c.options.snapshot()['status']!='error'
    assert c.options.snapshot()['scroll']>0, 'keyboard must reach clipped internal controls'
    d.native();d.teardown()


@pytest.mark.parametrize('bad',[0,10,0xd800,0xdfff,0x110000])
def test_unicode_scalar_rejection_through_menu_leaves_buffer_intact(tmp_path,bad):
    c,d=picker(tmp_path);before=c.options_editor.text
    scalar(d,bad)
    assert c.options_editor.text==before
    assert c.options.snapshot()['status']=='error'
    assert len(c.options.snapshot()['message'].encode())<=256
    d.native();d.teardown()


def test_all_character_groups_and_256_byte_bound_through_real_menu(tmp_path):
    c,d=picker(tmp_path);press(d,'editor.clear')
    insert(d,'aZ9:/?')
    assert c.options_editor.text=='aZ9:/?'
    press(d,'editor.clear')
    # Unicode four-byte scalars prove byte bound, not codepoint bound.
    for _ in range(64): scalar(d,0x1f680)
    assert len(c.options_editor.text.encode())==256
    scalar(d,0x1f680)
    assert len(c.options_editor.text.encode())==256
    assert c.options.snapshot()['status']=='error'
    press(d,'editor.cancel')
    assert c.options_editor.kind is None and c.options_editor.text_input is None
    assert c.options.snapshot()['open']
    d.native();d.teardown()
