"""Phase 6 corpus for the Rust rebuild (docs/rust-rebuild-plan.md): the view state and its frame encoders.

rust/corpus/view/view.json is made only by this module, from the shipped tools/native_spike/view.py (the parts outside
ViewControl): the bytes each frame encoder writes for fixed inputs, and its refusals; ViewState scripts (a run's view
driven op by op, each step with the view, placement panel and settings that follow); and layout.json as load_settings
reads it and save_settings writes it. The Rust port (rust/crates/xr-workspace/src/supervisor/) replays every case and must
give the same bytes, or refuse the same input.

A refusal is labelled by how the Rust port refuses it: "type" when its typed inputs cannot hold the value (a bool for an
int, a missing key, an unknown name), so the case's arguments must fail to decode; "value" when they can, so the port
must refuse with the Python's message.

Regenerate (after a deliberate change to view.py) with:

    TMPDIR=/tmp /usr/bin/python3 tests/native_spike/view_corpus.py --write

test_view_corpus.py fails when the file and the Python code disagree.
"""
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))

from tools.native_spike import send, view  # noqa: E402

CORPUS_FILE = ROOT / 'rust' / 'corpus' / 'view' / 'view.json'
ENVELOPE = {'run_id': 'run-1', 'session': 'a1b2c3', 'request_id': 7}

P0 = {'x': 0, 'y': 0, 'distance': 100, 'curvature': 0}
P1 = {'x': -600, 'y': 40, 'distance': 120, 'curvature': 30}
P2 = {'x': 1790, 'y': -900, 'distance': 10, 'curvature': 120}
BAD_X = {**P0, 'x': 1801}
SIZE = {'width': 1920, 'height': 1080, 'scale': 1.0}
WIDE = {'width': 3840, 'height': 1080, 'scale': 1.25}


def _menu(**extra):
    return {'open': True, 'desktop': 0, 'field': 'x', 'placements': [P0, P1], **extra}


# (encoder, arguments, how the Rust port refuses it when the Python does). Arguments are the Python's keyword
# arguments after the envelope (run_id, session, request_id).
FRAME_CASES = (
    ('help_frame', {'visible': True}, None),
    ('help_frame', {'visible': False}, None),
    ('help_frame', {'visible': True, 'turn': 1}, None),
    ('help_frame', {'visible': True, 'turn': -1}, None),
    ('help_frame', {'visible': True, 'turn': 2}, 'value'),
    ('help_frame', {'visible': True, 'turn': True}, 'type'),
    ('help_frame', {'visible': True, 'binds': [['CTRL + ALT + K', 'zoom_in'], ['SHIFT + SUPER + T', 'typing_toggle'],
                                                ['CTRL + ALT + left', 'prev'], ['CTRL + ALT + right', 'next']]}, None),
    ('help_frame', {'visible': True, 'send_binds': [['CTRL + ALT + Y', 'send_glasses'], ['CTRL + ALT + N', 'send_back']]},
     None),
    ('style_frame', {'rgb': [51, 102, 153]}, None),
    ('style_frame', {'rgb': [0, 0, 255]}, None),
    ('style_frame', {'rgb': [0, 0, 256]}, 'type'),
    ('style_frame', {'rgb': [1, 2]}, 'type'),
    ('desktops_frame', {'names': ['XR-3', 'XR-1']}, None),
    ('desktops_frame', {'names': ['XR-1', 'XR-2', 'XR-4'], 'renew': ['XR-4', 'XR-2'], 'ease': True}, None),
    ('desktops_frame', {'names': ['XR-2'], 'ease': False}, None),
    ('desktops_frame', {'names': []}, 'value'),
    ('desktops_frame', {'names': ['XR-1', 'XR-2', 'XR-3', 'XR-4', 'XR-1']}, 'value'),
    ('desktops_frame', {'names': ['XR-1', 'XR-1']}, 'value'),
    ('desktops_frame', {'names': ['XR-5']}, 'value'),
    ('desktops_frame', {'names': ['DP-1']}, 'value'),
    ('desktops_frame', {'names': ['XR-1'], 'renew': ['XR-1', 'XR-1']}, 'value'),
    ('desktops_frame', {'names': ['XR-1'], 'renew': ['XR-2']}, 'value'),
    ('placement_frame', {'placements': [P0, P1, P2]}, None),
    ('placement_frame', {'placements': [P1], 'glide': 240}, None),
    ('placement_frame', {'placements': [P1], 'glide': 1000}, None),
    ('placement_frame', {'placements': [{'curvature': 0, 'distance': 50, 'y': 1, 'x': 2}]}, None),
    ('placement_frame', {'placements': []}, 'value'),
    ('placement_frame', {'placements': [P0] * 5}, 'value'),
    ('placement_frame', {'placements': [BAD_X]}, 'value'),
    ('placement_frame', {'placements': [{**P0, 'y': 901, 'x': 1801}]}, 'value'),
    ('placement_frame', {'placements': [{**P0, 'distance': 9}]}, 'value'),
    ('placement_frame', {'placements': [{**P0, 'curvature': 121}]}, 'value'),
    ('placement_frame', {'placements': [{**P0, 'x': True}]}, 'type'),
    ('placement_frame', {'placements': [{**P0, 'x': 1.0}]}, 'type'),
    ('placement_frame', {'placements': [{'x': 0, 'y': 0, 'distance': 100}]}, 'type'),
    ('placement_frame', {'placements': [{**P0, 'yaw': 0}]}, 'type'),
    ('placement_frame', {'placements': [P0], 'glide': 0}, 'value'),
    ('placement_frame', {'placements': [P0], 'glide': 1001}, 'value'),
    ('placement_frame', {'placements': [P0], 'glide': 2.5}, 'type'),
    ('placement_menu_frame', {'menu': _menu()}, None),
    ('placement_menu_frame', {'menu': {**_menu(open=False, desktop=1, field='brightness')}}, None),
    ('placement_menu_frame', {'menu': _menu(
        brightness=150, background='grid', background_picker=False, scales=[100, 125], scale=125, resolution=[1920, 1080],
        resolution_picker=True, picker=False, busy=True, armed=True, wide=True, names=[1, 3], surface=P2)}, None),
    ('placement_menu_frame', {'menu': _menu(picker=True, scale=300, scales=[], background='smoke')}, None),
    ('placement_menu_frame', {'menu': _menu(open=False, picker=False, resolution_picker=False, background_picker=False)},
     None),
    ('placement_menu_frame', {'menu': _menu(placements=[])}, 'value'),
    ('placement_menu_frame', {'menu': _menu(placements=[BAD_X])}, 'value'),
    ('placement_menu_frame', {'menu': _menu(desktop=2)}, 'value'),
    ('placement_menu_frame', {'menu': _menu(desktop=-1)}, 'type'),
    ('placement_menu_frame', {'menu': _menu(open=False, surface=P0)}, 'value'),
    ('placement_menu_frame', {'menu': _menu(surface=BAD_X)}, 'value'),
    ('placement_menu_frame', {'menu': _menu(names=[1])}, 'value'),
    ('placement_menu_frame', {'menu': _menu(names=[2, 1])}, 'value'),
    ('placement_menu_frame', {'menu': _menu(names=[1, 1])}, 'value'),
    ('placement_menu_frame', {'menu': _menu(names=[0, 1])}, 'value'),
    ('placement_menu_frame', {'menu': _menu(names=[1, 5])}, 'value'),
    ('placement_menu_frame', {'menu': _menu(open=False, picker=True)}, 'value'),
    ('placement_menu_frame', {'menu': _menu(open=False, background_picker=True)}, 'value'),
    ('placement_menu_frame', {'menu': _menu(picker=True, resolution_picker=True)}, 'value'),
    ('placement_menu_frame', {'menu': _menu(resolution_picker=True, background_picker=True)}, 'value'),
    ('placement_menu_frame', {'menu': _menu(resolution=[1921, 1080])}, 'value'),
    ('placement_menu_frame', {'menu': _menu(resolution=[640, 478])}, 'value'),
    ('placement_menu_frame', {'menu': _menu(resolution=[1920])}, 'type'),
    ('placement_menu_frame', {'menu': _menu(brightness=9)}, 'value'),
    ('placement_menu_frame', {'menu': _menu(brightness=201)}, 'value'),
    ('placement_menu_frame', {'menu': _menu(brightness=True)}, 'type'),
    ('placement_menu_frame', {'menu': _menu(scale=110)}, 'value'),
    ('placement_menu_frame', {'menu': _menu(scale=1.25)}, 'type'),
    ('placement_menu_frame', {'menu': _menu(scales=[125, 100])}, 'value'),
    ('placement_menu_frame', {'menu': _menu(scales=[100, 100])}, 'value'),
    ('placement_menu_frame', {'menu': _menu(scales=[100, 110])}, 'value'),
    ('placement_menu_frame', {'menu': _menu(background='fog')}, 'value'),
    ('placement_menu_frame', {'menu': _menu(field='yaw')}, 'type'),
    ('placement_menu_frame', {'menu': _menu(open=1)}, 'type'),
    ('placement_menu_frame', {'menu': _menu(wide=0)}, 'type'),
    ('placement_menu_frame', {'menu': _menu(extra=True)}, 'type'),
    ('placement_menu_frame', {'menu': {'open': True, 'desktop': 0, 'field': 'x'}}, 'type'),
    ('view_frame', {'state': {'zoomed': True, 'selected': 2, 'spacing': 400, 'mode': 'locked', 'cursor_follow': False,
                              'prediction': False, 'typing': True, 'stabilization': 0}}, None),
    ('view_frame', {'state': {'zoomed': False, 'selected': 0, 'spacing': 40, 'mode': 'anchor', 'cursor_follow': False,
                              'prediction': True, 'typing': False, 'stabilization': 25}}, None),
    ('focus_command', {'name': 'XR-2'}, None),
    ('focus_command', {'name': 'XR-0'}, 'value'),
    ('focus_command', {'name': 'XR-1 '}, 'value'),
    ('focus_command', {'name': 'DP-1'}, 'value'),
    ('parse_border', {'reply': {'gradient': 'ff336699 ff000000 45deg'}}, None),
    ('parse_border', {'reply': {'gradient': '0xAB4D63C0'}}, None),
    ('parse_border', {'reply': {'gradient': '  ee9c2265' + chr(0x2003) + 'x'}}, None),
    ('parse_border', {'reply': {'gradient': 'ff33669'}}, None),
    ('parse_border', {'reply': {'gradient': 'ff336699ff'}}, None),
    ('parse_border', {'reply': {'gradient': '0x0xff336699'}}, None),
    ('parse_border', {'reply': {'gradient': 'rgba(ff336699)'}}, None),
    ('parse_border', {'reply': {'gradient': ''}}, None),
    ('parse_border', {'reply': {'gradient': 7, 'int': 4293443583}}, None),
    ('parse_border', {'reply': {'int': 4294967295}}, None),
    ('parse_border', {'reply': {'int': 4294967296}}, None),
    ('parse_border', {'reply': {'int': -1}}, None),
    ('parse_border', {'reply': {'int': True}}, None),
    ('parse_border', {'reply': {'int': 1.0}}, None),
    ('parse_border', {'reply': {'gradient': 'ff336699', 'int': 1}}, None),
    ('parse_border', {'reply': {}}, None),
    ('parse_border', {'reply': [1, 2]}, None),
    ('parse_border', {'reply': 'ff336699'}, None),
    ('chord_label', {'chord': 'CTRL + ALT + bracketleft'}, None),
    ('chord_label', {'chord': 'SHIFT + SUPER + t'}, None),
    ('chord_label', {'chord': 'CTRL + ALT + code:19'}, None),
    ('chord_label', {'chord': 'CTRL + ALT + F12'}, None),
    ('chord_label', {'chord': 'ALT + ' + chr(0xdf)}, None),
    ('help_rows', {}, None),
    ('help_rows', {'binds': [['CTRL + ALT + X', 'video_open'], ['CTRL + ALT + Y', 'playback_menu_toggle'],
                             ['SUPER + H', 'help'], ['CTRL + ALT + H', 'binds_toggle'], ['CTRL + ALT + 1', 'privacy_display'],
                             ['CTRL + ALT + 2', 'typing_toggle']]}, None),
    ('help_rows', {'binds': [['CTRL + ALT + up', 'zoom_in'], ['SUPER + up', 'zoom_in']]}, None),
    ('help_rows', {'binds': [['CTRL + ALT + H', 'help']],
                   'send_binds': [['CTRL + ALT + G', 'send_glasses'], ['SUPER + SHIFT + B', 'send_back']]}, None),
    ('help_rows', {'binds': [['CTRL + ALT + H', 'help']],
                   'send_binds': [['SHIFT + SUPER + bracketleft', 'send_glasses'],
                                  ['SHIFT + SUPER + ALT + bracketright', 'send_back']]}, None),
    ('help_rows', {'binds': [['CTRL + ALT + H', 'help']], 'send_binds': [['CTRL + ALT + G', 'send_glasses']]}, None),
    ('help_rows', {'binds': [['CTRL + ALT + H', 'help']], 'send_binds': [list(b) for b in send.SEND_BINDS]}, None),
    ('help_sections', {}, None),
    ('help_sections', {'binds': [['CTRL + ALT + O', 'video_open'], ['CTRL + ALT + K', 'binds_toggle']]}, None),
    ('help_sections', {'binds': [['CTRL + ALT + next', 'next']]}, None),
    ('smootherstep', {'t': 0.5}, None),
    ('smootherstep', {'t': 0.1}, None),
    ('smootherstep', {'t': -3}, None),
    ('smootherstep', {'t': 7.5}, None),
    ('smootherstep', {'t': 0.999}, None),
    ('eased_to', {'p': P0, 'to': P1, 'e': 0.5}, None),
    ('eased_to', {'p': {**P0, 'x': 1700}, 'to': {**P0, 'x': -1700}, 'e': 0.5}, None),
    ('eased_to', {'p': {**P0, 'x': 1700}, 'to': {**P0, 'x': -1700}, 'e': 0.75}, None),
    ('eased_to', {'p': {**P0, 'x': -1750}, 'to': {**P0, 'x': 1750}, 'e': 0.9}, None),
    ('eased_to', {'p': {'x': 1, 'y': 1, 'distance': 11, 'curvature': 1}, 'to': {'x': 2, 'y': 2, 'distance': 12, 'curvature': 2},
                  'e': 0.5}, None),
    ('eased_to', {'p': P1, 'to': P2, 'e': 1.0}, None),
    ('eased_to', {'p': P1, 'to': P2, 'e': 0.0}, None),
    ('eased_to', {'p': P2, 'to': view.RESET_LEAVING_PLACEMENT, 'e': 0.3}, None),
    ('scale_percent', {'scale': 1.25}, None),
    ('scale_percent', {'scale': 1.755}, None),
)


def _encode(value):
    """A case's output in the corpus: frames as text, floats as their repr (exact), tuples as lists."""
    if isinstance(value, bytes): return value.decode()
    if isinstance(value, float): return repr(value)
    if isinstance(value, tuple): return [_encode(v) for v in value]
    return value


def _run(call, by):
    """{'out': ...}, or {'refused': message, 'by': by} when the call raises ValueError."""
    try:
        out = call()
    except ValueError as e:
        if by is None: raise AssertionError(f'refused without a label: {e}') from None
        return {'refused': str(e), 'by': by}
    if by is not None: raise AssertionError(f'labelled {by} but not refused')
    return {'out': _encode(out)}


def frame_cases():
    out = []
    for name, kwargs, by in FRAME_CASES:
        fn = getattr(view, name)
        envelope = tuple(ENVELOPE.values()) if name.endswith('_frame') else ()
        out.append({'fn': name, 'args': kwargs, **_run(lambda: fn(*envelope, **kwargs), by)})
    return out


# ViewState scripts: constructor keyword arguments, then steps [method, args, label]. A 'set' step assigns an
# attribute the way ViewControl does.
SCRIPTS = (
    ('keys in the overview and maximized', {'names': ['XR-1', 'XR-2', 'XR-3']}, (
        ('apply', ['next']), ('apply', ['next']), ('apply', ['next']), ('apply', ['prev']), ('apply', ['mode_next']),
        ('apply', ['zoom_in']), ('apply', ['mode_next']), ('apply', ['zoom_out']), ('apply', ['zoom_in']),
        ('apply', ['mode_next']), ('apply', ['prev']), ('apply', ['prev']), ('apply', ['prev']), ('apply', ['zoom_out']),
        ('apply', ['prediction']), ('apply', ['recenter']), ('apply', ['help']), ('apply', ['typing_toggle'], 'value'),
        ('apply', ['menu_toggle'], 'value'), ('set', ['typing', True]),
    )),
    ('spacing clamps', {'names': ['XR-2'], 'spacing': 390, 'prediction': False, 'stabilization': 0}, (
        ('apply', ['spacing_up']), ('apply', ['spacing_up']), *[('apply', ['spacing_down'])] * 21,
    )),
    ('spacing out of range at the start', {'names': ['XR-1'], 'spacing': 999, 'stabilization': 100}, ()),
    ('negative spacing at the start', {'names': ['XR-4'], 'spacing': -5}, ()),
    ('placement panel keys', {'names': ['XR-1', 'XR-3'], 'placements': [P1, P2, P0]}, (
        ('apply_menu', ['menu_inc']), ('apply_menu', ['menu_back']), ('apply_menu', ['menu_toggle']),
        ('apply_menu', ['menu_inc']), ('apply_menu', ['menu_inc_big']), ('apply_menu', ['menu_dec_big']),
        ('apply_menu', ['menu_dec']), ('apply_menu', ['menu_desktop_next']), ('apply_menu', ['menu_inc_big']),
        ('apply_menu', ['menu_inc_big']), ('apply_menu', ['menu_field_next']), ('apply_menu', ['menu_inc_big']),
        ('apply_menu', ['menu_inc_big']), ('apply_menu', ['menu_field_prev']), ('apply_menu', ['menu_field_prev']),
        ('apply_menu', ['menu_dec']), ('apply_menu', ['menu_field_next']), ('apply_menu', ['menu_field_next']),
        ('apply_menu', ['menu_field_next']), ('apply_menu', ['menu_dec_big']), ('apply_menu', ['menu_dec_big']),
        ('apply_menu', ['menu_field_next']), ('apply_menu', ['menu_inc']), ('apply_menu', ['menu_field_next']),
        ('apply_menu', ['menu_inc_big']), ('apply_menu', ['menu_inc_big']), ('apply_menu', ['menu_field_next']),
        ('apply_menu', ['menu_dec']), ('apply_menu', ['menu_reset']), ('apply_menu', ['menu_reset']),
        ('apply_menu', ['menu_desktop_prev']), ('apply_menu', ['menu_desktop_prev']), ('apply_menu', ['menu_reset']),
        ('set', ['remove_armed', True]), ('set', ['scale_picker', True]), ('apply_menu', ['menu_ultrawide']),
        ('set', ['background_picker', True]), ('apply_menu', ['menu_add']), ('apply_menu', ['menu_delete']),
        ('set', ['remove_armed', True]), ('apply_menu', ['menu_desktop_next']), ('set', ['remove_armed', True]),
        ('apply_menu', ['menu_back']), ('apply_menu', ['menu_dec']), ('apply_menu', ['zoom_in'], 'value'),
    )),
    ('x wraps round', {'names': ['XR-1'], 'placements': [{**P0, 'x': 1700}]}, (
        ('open_menu', [0]), ('apply_menu', ['menu_inc_big']), ('apply_menu', ['menu_dec_big']),
        ('apply_menu', ['menu_dec_big']), ('slide', ['x', 1.0]), ('apply_menu', ['menu_inc']),
    )),
    ('sliders', {'names': ['XR-1', 'XR-2']}, (
        ('slide', ['x', 0.5]), ('open_menu', [1]), ('slide', ['y', 0.25]), ('slide', ['y', 0.25]),
        ('slide', ['distance', -2]), ('slide', ['curvature', 7]), ('slide', ['x', 0.0001]), ('slide', ['x', 0.99999]),
        ('slide', ['distance', 0.5]), ('slide', ['brightness', 0.5], 'value'), ('slide', ['yaw', 0.5], 'value'),
        ('slide', ['x', True], 'type'), ('slide', ['x', 'half'], 'type'),
        ('hold_surface', [P2]), ('hold_surface', [None]), ('hold_surface', [BAD_X], 'value'),
        ('open_menu', [2], 'value'), ('open_menu', [-1], 'value'), ('open_menu', [True], 'type'),
        ('hold_surface', [P1]), ('open_menu', [0]), ('hold_surface', [P1]), ('apply_menu', ['menu_desktop_next']),
        ('hold_surface', [P1]), ('apply_menu', ['menu_toggle']), ('hold_surface', [P2]),
    )),
    ('placements set and held', {'names': ['XR-1', 'XR-2', 'XR-4'], 'placements': [P1]}, (
        ('set_placements', [[P0, P0, P0]]), ('set_placements', [[P0, P0, P0]]), ('set_placements', [[P0, P0]], 'value'),
        ('set_placements', [[P0, P0, BAD_X]], 'value'), ('hold_placements', [[P1, P0, P2]]),
        ('hold_placements', [[P1, P0]], 'value'), ('set_placements', [[P1, P1, P1]]), ('hold_placements', [[P0, P1, P1]]),
        ('hold_placements', [[P1, P1, P1]]),
    )),
    ('desktop changes', {'names': ['XR-1', 'XR-2', 'XR-3'], 'placements': [P0, P1, P2]}, (
        ('apply', ['next']), ('apply', ['next']), ('open_menu', [2]),
        ('set_desktops', [['XR-1', 'XR-3'], [P2, P0], [False, True]]),
        ('set_desktops', [['XR-1', 'XR-2', 'XR-3'], [P0, P1, P2], [True, False, False]], {'focus': 1, 'sizes': [SIZE, WIDE, SIZE]}),
        ('set', ['scale_picker', True]), ('set', ['resolution_picker', True]), ('set', ['desktops_busy', True]),
        ('set_desktops', [['XR-2', 'XR-4'], [P0, P1], [False, False]], {'sizes': [WIDE, SIZE]}),
        ('set_desktops', [['XR-4'], [P1], [False]], {'focus': 3}),
        ('set_desktops', [['XR-1', 'XR-4'], [P0, P1], [False, False]], {'focus': -1, 'sizes': [SIZE]}, 'value'),
        ('set_desktops', [['XR-1', 'XR-4'], [P0, P1], [False, False]], {'focus': 0, 'sizes': [SIZE, SIZE]}),
        ('apply', ['zoom_in']),
        ('set_desktops', [['XR-1', 'XR-2', 'XR-4'], [P0, P0, P1], [False, False, False]], {'focus': 1}),
        ('set_desktops', [[], [], []], 'value'), ('set_desktops', [['XR-1', 'XR-9'], [P0, P0], [False, False]], 'value'),
        ('set_desktops', [['XR-1'], [P0, P0], [False]], 'value'), ('set_desktops', [['XR-1'], [P0], []], 'value'),
        ('apply', ['zoom_out']), ('apply_menu', ['menu_toggle']),
        ('set_desktops', [['XR-2', 'XR-3'], [P0, P0], [False, False]]),
    )),
    ('background and settings', {'names': ['XR-1'], 'environment': {'background': 'smoke', 'last': 'grid', 'brightness': 9},
                                 'video_quality': {'filter': 'linear', 'deband': True},
                                 'immersive': {'global': True, 'desktops': {'XR-1': False, 'XR-2': True}, 'preset': 'games',
                                               'strengths': {'XR-1': 20}}}, (
        ('open_menu', [0]), ('apply_menu', ['menu_field_prev']), ('apply_menu', ['menu_inc']),
        ('set', ['environment', {'background': 'none', 'last': 'smoke', 'brightness': 180}]),
        ('set', ['background_picker', True]), ('apply_menu', ['menu_field_next']),
    )),
    ('all-invalid strengths make settings refuse', {'names': ['XR-1'], 'immersive': {'strengths': {'XR-1': 999}}}, ()),
    ('garbage settings at the start', {'names': ['XR-1'], 'environment': [1], 'video_quality': 'x', 'immersive': 3}, ()),
    ('no outputs', {'names': []}, (), 'value'),
    ('five outputs', {'names': ['XR-1', 'XR-2', 'XR-3', 'XR-4', 'XR-1']}, (), 'value'),
    ('an unmanaged output', {'names': ['XR-1', 'HDMI-A-1']}, (), 'value'),
    ('prediction must be a bool', {'names': ['XR-1'], 'prediction': 1}, (), 'type'),
    ('stabilization out of range', {'names': ['XR-1'], 'stabilization': 101}, (), 'value'),
    ('stabilization must be an int', {'names': ['XR-1'], 'stabilization': True}, (), 'type'),
    ('a bad placement', {'names': ['XR-1', 'XR-2'], 'placements': [P0, BAD_X]}, (), 'value'),
    ('a bad placement past the outputs is dropped', {'names': ['XR-1'], 'placements': [P0, BAD_X]}, ()),
)


def _or_refusal(call):
    try:
        return call()
    except ValueError as e:
        return {'refused': str(e)}


def _snapshot(state):
    """What follows a step: the view frame, the panel's frame (or its refusal: two lists open at once) and the settings
    (or their refusal)."""
    return {'view': view.view_frame(*ENVELOPE.values(), state.view()).decode(),
            'menu': _or_refusal(lambda: view.placement_menu_frame(*ENVELOPE.values(), state.menu()).decode()),
            'settings': _or_refusal(lambda: json.dumps(state.settings(), separators=(',', ':'))),
            'wanted': state.wanted_placements()}


def script_cases():
    out = []
    for name, init, steps, *init_by in SCRIPTS:
        init_by = init_by[0] if init_by else None
        case = {'name': name, 'init': init}
        try:
            state = view.ViewState(**init)
        except ValueError as e:
            if init_by is None: raise
            out.append({**case, 'refused': str(e), 'by': init_by})
            continue
        assert init_by is None, name
        case['start'] = _snapshot(state)
        case['steps'] = []
        for step in steps:
            method, args, *rest = step
            kwargs = rest.pop(0) if rest and isinstance(rest[0], dict) else {}
            by = rest[0] if rest else None
            if method == 'set':
                call = lambda: setattr(state, args[0], args[1])  # noqa: E731
            else:
                call = lambda: getattr(state, method)(*args, **kwargs)  # noqa: E731
            result = _run(call, by)
            case['steps'].append({'call': method, 'args': args, **({'kwargs': kwargs} if kwargs else {}), **result,
                                  **_snapshot(state)})
        out.append(case)
    return out


# layout.json texts load_settings reads (None: no file). NaN, Infinity and numbers past a float's range are left out:
# json.loads takes them and the Rust parser does not (view.rs load_settings says so).
LOAD_CASES = (
    None, '', 'not json', '[]', '"x"', '{}', '{"spacing": 120}', '{"spacing": 401}', '{"spacing": -1}',
    '{"spacing": true}', '{"spacing": 40.0}', '{"spacing": 100000000000000000000}', '{"spacing": 1, "spacing": 2}',
    '{"mode": "locked", "prediction": false, "stabilization": 100}', '{"mode": "spin", "prediction": 0, "stabilization": 101}',
    '{"mode": ["anchor"], "stabilization": -1}',
    '{"video_quality": {"filter": "linear", "deband": true}, "environment": {"background": "grid", "last": "smoke"}}',
    '{"video_quality": {"filter": "fog", "deband": "yes"}, "environment": "grid", "immersive": []}',
    '{"immersive": {"global": true, "desktops": {"XR-4": false, "XR-1": true, "XR-2": false}, "preset": "video", "strength": 150}}',
    # Strengths in name order: the Rust port keeps them in a map sorted by name, the Python in the input's order.
    '{"immersive": {"strengths": {"XR-1": 200, "XR-2": 10, "XR-3": 201, "XR-9": 5}, "convergence": 101, "preset": "games"}}',
    '{"immersive": {"strengths": {"XR-1": 999}}}',
    '{"spacing": 80, "cursor_switch_animate": true, "extra": {"a": [1, 2]}}',
    ' {"spacing": 60}\n\n',
    '{"spacing": 60} x',
    '{"spacing": 6' + '0' * 400 + '}',
)


def load_cases():
    out = []
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'layout.json'
        for text in LOAD_CASES:
            if text is None:
                path.unlink(missing_ok=True)
            else:
                path.write_text(text)
            out.append({'text': text, 'settings': json.dumps(view.load_settings(path), separators=(',', ':'))})
    return out


DEFAULT = view.DEFAULT_SETTINGS


def _with(**changes):
    return {**DEFAULT, **changes}


# (settings, how the Rust port refuses it when the Python does).
SAVE_CASES = (
    (DEFAULT, None),
    (_with(spacing=400, mode='follow', prediction=False, stabilization=0,
           video_quality={'filter': 'nearest', 'deband': True},
           environment={'background': 'smoke', 'last': 'smoke', 'brightness': 10},
           immersive={'strengths': {'XR-1': 200, 'XR-3': 0}, 'global': True, 'desktops': {'XR-1': False, 'XR-4': False},
                      'preset': 'video', 'strength': 0, 'convergence': 100}), None),
    (_with(environment={'background': 'none', 'last': 'grid', 'brightness': 200}), None),
    (_with(spacing=401), 'value'),
    (_with(spacing=-1), 'value'),
    (_with(stabilization=101), 'value'),
    (_with(environment={'background': 'grid', 'last': 'smoke', 'brightness': 100}), 'value'),
    (_with(environment={'background': 'none', 'last': 'none', 'brightness': 100}), 'value'),
    (_with(environment={'background': 'none', 'last': 'grid', 'brightness': 201}), 'value'),
    (_with(immersive={**DEFAULT['immersive'], 'strength': 201}), 'value'),
    (_with(immersive={**DEFAULT['immersive'], 'convergence': 101}), 'value'),
    (_with(immersive={**DEFAULT['immersive'], 'desktops': {'XR-1': False}}), 'value'),
    (_with(immersive={**DEFAULT['immersive'], 'desktops': {'XR-9': True}}), 'value'),
    (_with(immersive={'strengths': {}, **DEFAULT['immersive']}), 'value'),
    (_with(immersive={'strengths': {'XR-1': 201}, **DEFAULT['immersive']}), 'value'),
    (_with(spacing=True), 'type'),
    (_with(mode='spin'), 'type'),
    (_with(prediction=1), 'type'),
    (_with(video_quality={'filter': 'linear', 'deband': False}), 'type'),
    (_with(video_quality={'filter': 'sharp'}), 'type'),
    (_with(environment={'background': 'fog', 'last': 'grid', 'brightness': 100}), 'type'),
    (_with(immersive={**DEFAULT['immersive'], 'preset': 'film'}), 'type'),
    (_with(immersive={**DEFAULT['immersive'], 'extra': 1}), 'type'),
    ({k: v for k, v in DEFAULT.items() if k != 'mode'}, 'type'),
    ({**DEFAULT, 'extra': 1}, 'type'),
)


def save_cases():
    out = []
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'layout.json'
        for settings, by in SAVE_CASES:
            path.unlink(missing_ok=True)
            result = _run(lambda: view.save_settings(path, settings), by)
            if 'out' in result: result = {'bytes': path.read_text()}
            out.append({'settings': settings, **result})
    return out


def corpus():
    return {'comment': 'Made by tests/native_spike/view_corpus.py from tools/native_spike/view.py; do not edit by hand.',
            'envelope': ENVELOPE, 'frames': frame_cases(), 'scripts': script_cases(), 'load_settings': load_cases(),
            'save_settings': save_cases()}


def dumps(value):
    return json.dumps(value, indent=1, ensure_ascii=False) + '\n'


def main(argv):
    if argv != ['--write']:
        print('usage: view_corpus.py --write', file=sys.stderr)
        return 2
    CORPUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    CORPUS_FILE.write_text(dumps(corpus()))
    print(CORPUS_FILE)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
