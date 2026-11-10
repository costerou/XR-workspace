"""Phase 0 corpus for the Rust rebuild (docs/rust-rebuild-plan.md, phase 0 and the phase 6 parity gate).

Two golden files under rust/corpus/hyprland/, both made only by this module from the shipped Python code:

* lua.json: every generated-Lua string the supervisor (and the key helper's fixed cleanups) can send to Hyprland's
  /eval, from fixed inputs. One case per branch of each generator (GENERATORS), refusals included: the Rust port must
  give the same string for the same arguments, byte for byte, or refuse the same input.
* transcripts.json: the bytes HyprlandClient sends to Hyprland's request socket and the replies it gets, for every
  public HyprlandClient call that talks to the compositor, against a scripted stand-in compositor on a temporary
  socket (never the user's Hyprland).

Regenerate (after a deliberate change to a generator) with:

    TMPDIR=/tmp /usr/bin/python3 tests/native_spike/lua_corpus.py --write

test_lua_corpus.py fails when the files and the Python code disagree, and when a generator branch has no case.
"""
import json
import os
from pathlib import Path
import re
import socket
import sys
import tempfile
import threading
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))

from tools.native_spike import (adopt, columns, fit_windows, free_pointer, hyprland, input_lock, keybinds,  # noqa: E402
                                overview_cursor, privacy, resize, restore, sbs, send, unified_cursor, video, view,
                                window_memory, xr_control)

CORPUS = ROOT / 'rust' / 'corpus' / 'hyprland'
LUA_FILE, TRANSCRIPT_FILE = CORPUS / 'lua.json', CORPUS / 'transcripts.json'

# Functions whose every branch must be taken by some case (test_lua_corpus checks with sys.monitoring). Input
# validators they call (adopt._validated, restore.validate, window_memory._validated, privacy._name,
# keybinds.parse_chord) have their own unit tests; the cases below still include refusals from each.
GENERATORS = (
    adopt.lua, adopt._chunk,
    columns.scroll_lua,
    fit_windows.lua,
    free_pointer.grab_pointer_lua, free_pointer.release_pointer_lua,
    input_lock.lock_binds_lua, input_lock.lock_pointer_lua,
    keybinds.panel_on_lua,
    privacy.blank_lua, privacy.wake_lua,
    resize.install_resize_binds_lua, resize.remove_resize_binds_lua,
    restore.lua,
    sbs.wide_mode_lua, sbs.pro2_mode_lua, sbs._mode_lua,
    send.land_lua,
    unified_cursor.click_binds_lua, unified_cursor.enter_desktop_lua, unified_cursor.confine_lua,
    video.install_video_binds_lua, video.remove_video_binds_lua,
    view.warp_cursor_lua, view.restore_mode_lua, view.install_binds_lua, view._checked_binds, view._binds_lua,
    view._remove_lua, view.remove_binds_lua, view.install_send_binds_lua, view.remove_send_binds_lua,
    view.install_drag_binds_lua, view.remove_drag_binds_lua, view.install_menu_binds_lua,
    view.install_options_binds_lua, view.remove_menu_binds_lua,
    window_memory._rows, window_memory._batches, window_memory._moves_eval, window_memory.moves_lua,
    window_memory._state_eval, window_memory._order_eval, window_memory._end_eval, window_memory.settle_lua,
    window_memory.verify_lua,
)

# Fixed Lua sent as is: module constants (and the key helper's copies, which tests elsewhere compare).
CONSTANTS = (
    (keybinds, 'PANEL_OFF_LUA'), (privacy, 'WAKE_ALL_LUA'), (restore, 'UNPIN_LUA'), (resize, 'GRAB_LEAVE_LUA'),
    (overview_cursor, 'HIDE_LUA'), (overview_cursor, 'SHOW_LUA'), (input_lock, '_PIN_HOME_LUA'),
    (free_pointer, '_HIDE_LUA'), (free_pointer, '_SHOW_LUA'), (view, '_LEGACY_MOUSE_CLEANUP_LUA'),
    (fit_windows, '_LUA'), (restore, '_LUA'),
    (xr_control, 'LEAVE_KEY_PANEL'), (xr_control, 'REMOVE_VIDEO_BINDS'), (xr_control, 'WAKE_ALL_DISPLAYS'),
    (xr_control, 'REMOVE_MENU_BINDS'), (xr_control, 'REMOVE_RESIZE_BINDS'), (xr_control, 'REMOVE_DRAG_BINDS'),
    (xr_control, 'SHOW_OVERVIEW_CURSOR'),
)

# The bind helper's command line, as a run passes it.
BIND = {'python': '/usr/bin/python3', 'client': '/home/user/.local/share/viture-workspace/bin/0123456789ab/xr_control.py',
        'control_socket': '/run/user/1000/viture-workspace/run-1/control.sock', 'run_id': 'run-1', 'session': 'a1b2c3'}
PANEL_BINDS = [('CTRL + ALT + K', 'zoom_in'), ('SHIFT + SUPER + T', 'typing_toggle')]  # the key bind panel's own keys
PANEL_SEND_BINDS = [('CTRL + ALT + Y', 'send_glasses'), ('CTRL + ALT + code:10', 'send_glasses_1')]
BAD_BIND = (('unsafe_path_type', {'python': 7}), ('unsafe_path', {'client': 'relative/xr_control.py'}),
            ('unsafe_token_type', {'run_id': 7}), ('unsafe_token', {'session': '-leading-dash'}))


def name_of(fn):
    return f'{fn.__module__.rsplit(".", 1)[1]}.{fn.__qualname__}'


def _address(i):
    return f'0x{0x55d0c0de0000 + i:x}'


def _bind_cases(fn, extra=None, label=None):
    """A bind-table generator: the valid call, then each refusal of its shared path and token checks."""
    extra = dict(extra or {})
    label = label or name_of(fn)
    out = [(label, fn, {**BIND, **extra})]
    for tag, bad in BAD_BIND: out.append((f'{label}.refuse_{tag}', fn, {**BIND, **extra, **bad}))
    return out


def _snapshot(windows=None, **over):
    s = {'schema': 1, 'run_id': 'run-1', 'monitor': 'DP-1', 'active': 2, 'focus': _address(1), 'focus_monitor': 'DP-1',
         'workspaces': [{'id': 1, 'whole': True}, {'id': 2, 'whole': False}],
         'windows': windows if windows is not None else [
             {'address': _address(1), 'workspace': 1, 'floating': False, 'at': [0, 30], 'size': [960, 1050],
              'fullscreen': 0, 'client': 0},
             {'address': _address(2), 'workspace': 2, 'floating': True, 'at': [100, 200], 'size': [800, 600],
              'fullscreen': 2, 'client': 1}]}
    s.update(over)
    return s


def _placement(**over):
    p = {'whole': [], 'windows': [], 'layouts': [], 'state': [], 'order': [], 'fullscreen': [], 'shown': [], 'placed': [],
         'focus': None, 'monitor': None}
    p.update(over)
    return p


def _sample_placement():
    a, b = _address(1), _address(2)
    return _placement(whole=[(1, 'XR-1', 61)], windows=[(a, 'XR-2', 71, 0), (b, 'XR-3', 81, 1)], layouts=[(a, 'scrolling')],
                      state=[(a, 1, 10, 20, 300, 400), (b, 0, 0, 0, 0, 0)], order=[[(a, 0, 300, 400), (b, 1, 300, 0)]],
                      fullscreen=[(a, 2, 2)], shown=[('XR-1', a)], focus=a, monitor='DP-1')


ONE_OF_EACH = {'workspaces': [(4, 'XR-2')], 'windows': [(_address(1), 'XR-1')]}
FIT = [{'address': _address(1), 'x': -40, 'y': 30, 'w': 0, 'h': 0},
       {'address': _address(2), 'x': 3800, 'y': 30, 'w': 1920, 'h': 1050}]


def lua_cases():
    """(name, generator, kwargs). Names are unique and stable: they key the golden file."""
    w1, w2 = _address(1), _address(2)
    many = [(_address(i), f'XR-{i % 3 + 1}') for i in range(150)]
    cases = [
        # adopt.py: windows then workspaces; split across evals once over the limit.
        ('adopt.empty', adopt.lua, {'moves': {'workspaces': [], 'windows': []}}),
        ('adopt.one_of_each', adopt.lua, {'moves': ONE_OF_EACH}),
        ('adopt.windows_only', adopt.lua, {'moves': {'workspaces': [], 'windows': [(w1, 'XR-1'), (w2, 'XR-3')]}}),
        ('adopt.split', adopt.lua, {'moves': {'workspaces': [(i, 'XR-2') for i in range(1, 11)], 'windows': many}}),
        ('adopt.refuse_shape', adopt.lua, {'moves': {'windows': []}}),
        ('adopt.refuse_workspace', adopt.lua, {'moves': {'workspaces': [(0, 'XR-1')], 'windows': []}}),
        ('adopt.refuse_address', adopt.lua, {'moves': {'workspaces': [], 'windows': [('55d0', 'XR-1')]}}),
        ('adopt.refuse_output', adopt.lua, {'moves': {'workspaces': [], 'windows': [(w1, 'DP-1')]}}),
        # columns.py
        ('columns.scroll', columns.scroll_lua, {'address': w1}),
        ('columns.scroll.refuse_type', columns.scroll_lua, {'address': 1}),
        ('columns.scroll.refuse_address', columns.scroll_lua, {'address': '0xZZ'}),
        # fit_windows.py: BATCH windows per eval.
        ('fit_windows.empty', fit_windows.lua, {'moves': []}),
        ('fit_windows.move_and_resize', fit_windows.lua, {'moves': FIT}),
        ('fit_windows.two_batches', fit_windows.lua, {'moves': [
            {'address': _address(i), 'x': i, 'y': 2 * i, 'w': 0, 'h': 0} for i in range(fit_windows.BATCH + 1)]}),
        ('fit_windows.refuse_list', fit_windows.lua, {'moves': {}}),
        ('fit_windows.refuse_size', fit_windows.lua, {'moves': [{'address': w1, 'x': 0, 'y': 0, 'w': 5, 'h': 0}]}),
        # free_pointer.py
        *_bind_cases(free_pointer.grab_pointer_lua, {'x': 960, 'y': 540}),
        ('free_pointer.grab_pointer_lua.refuse_position', free_pointer.grab_pointer_lua, {**BIND, 'x': 960.5, 'y': 540}),
        ('free_pointer.release_pointer', free_pointer.release_pointer_lua, {}),
        ('free_pointer.release_pointer_at', free_pointer.release_pointer_lua, {'x': -1920, 'y': 1200}),
        # input_lock.py
        *_bind_cases(input_lock.lock_binds_lua),
        ('input_lock.lock_pointer', input_lock.lock_pointer_lua, {**BIND, 'x': 4800, 'y': 600}),
        # keybinds.py
        *_bind_cases(keybinds.panel_on_lua),
        # privacy.py
        ('privacy.blank', privacy.blank_lua, {'target': 'eDP-1', 'glasses': 'DP-2'}),
        ('privacy.blank.refuse_same', privacy.blank_lua, {'target': 'DP-2', 'glasses': 'DP-2'}),
        ('privacy.blank.refuse_name', privacy.blank_lua, {'target': 'XR-1', 'glasses': 'DP-2'}),
        ('privacy.wake', privacy.wake_lua, {'target': 'HDMI-A-1'}),
        ('privacy.wake.refuse_name', privacy.wake_lua, {'target': 'eDP-1"); os.exit() --'}),
        # resize.py
        *_bind_cases(resize.install_resize_binds_lua),
        ('resize.remove_resize_binds', resize.remove_resize_binds_lua, {}),
        # restore.py: one eval under restore.EVAL_LIMIT.
        ('restore.layout', restore.lua, {'snapshot': _snapshot()}),
        ('restore.nothing_shown', restore.lua, {'snapshot': _snapshot(windows=[], active=None, focus=None, focus_monitor=None)}),
        ('restore.refuse_too_large', restore.lua, {'snapshot': _snapshot(windows=[
            {'address': _address(i), 'workspace': 1, 'floating': True, 'at': [1000000 + i, 1000000], 'size': [1000000, 1000000],
             'fullscreen': 0, 'client': 0} for i in range(400)])}),
        ('restore.refuse_monitor', restore.lua, {'snapshot': _snapshot(monitor='XR-1')}),
        ('restore.refuse_window', restore.lua, {'snapshot': _snapshot(windows=[
            {'address': w1, 'workspace': 9, 'floating': False, 'at': [0, 0], 'size': [1, 1], 'fullscreen': 0, 'client': 0}])}),
        # sbs.py
        ('sbs.wide_luma_90', sbs.wide_mode_lua, {'name': 'DP-2', 'x': 1920, 'y': 0, 'scale': 1, 'rate': 90.0}),
        ('sbs.wide_luma_60_scaled', sbs.wide_mode_lua, {'name': 'HDMI-A-1', 'x': -3840, 'y': 100, 'scale': 1.25, 'rate': 60}),
        ('sbs.wide_pro2', sbs.wide_mode_lua, {'name': 'DP-2', 'x': 0, 'y': 0, 'scale': 1.0, 'rate': 60.0, 'model': 'pro2'}),
        ('sbs.wide.refuse_rate', sbs.wide_mode_lua, {'name': 'DP-2', 'x': 0, 'y': 0, 'scale': 1.0, 'rate': 120.0}),
        ('sbs.wide.refuse_rate_type', sbs.wide_mode_lua, {'name': 'DP-2', 'x': 0, 'y': 0, 'scale': 1.0, 'rate': '90'}),
        ('sbs.wide.refuse_model', sbs.wide_mode_lua, {'name': 'DP-2', 'x': 0, 'y': 0, 'scale': 1.0, 'rate': 90.0, 'model': 'x'}),
        ('sbs.wide.refuse_name', sbs.wide_mode_lua, {'name': 'eDP-1', 'x': 0, 'y': 0, 'scale': 1.0, 'rate': 90.0}),
        ('sbs.wide.refuse_x', sbs.wide_mode_lua, {'name': 'DP-2', 'x': 0.5, 'y': 0, 'scale': 1.0, 'rate': 90.0}),
        ('sbs.wide.refuse_y', sbs.wide_mode_lua, {'name': 'DP-2', 'x': 0, 'y': None, 'scale': 1.0, 'rate': 90.0}),
        ('sbs.wide.refuse_scale_type', sbs.wide_mode_lua, {'name': 'DP-2', 'x': 0, 'y': 0, 'scale': '1', 'rate': 90.0}),
        ('sbs.wide.refuse_scale', sbs.wide_mode_lua, {'name': 'DP-2', 'x': 0, 'y': 0, 'scale': 5.0, 'rate': 90.0}),
        ('sbs.wide.refuse_scale_small', sbs.wide_mode_lua, {'name': 'DP-2', 'x': 0, 'y': 0, 'scale': 0.2, 'rate': 90.0}),
        ('sbs.pro2_2d_120', sbs.pro2_mode_lua, {'name': 'DP-2', 'x': 1920, 'y': 0, 'scale': 1.0, 'rate': 120.0}),
        ('sbs.pro2_2d_59_94', sbs.pro2_mode_lua, {'name': 'DP-2', 'x': 1920, 'y': 0, 'scale': 1.5, 'rate': 59.94}),
        ('sbs.pro2_2d.refuse_rate', sbs.pro2_mode_lua, {'name': 'DP-2', 'x': 0, 'y': 0, 'scale': 1.0, 'rate': 144.0}),
        # send.py
        ('send.land', send.land_lua, {'address': w1, 'workspace': 7}),
        ('send.land_at', send.land_lua, {'address': w1, 'workspace': 2147483647, 'x': -100, 'y': 1_000_000}),
        ('send.land.refuse_address', send.land_lua, {'address': 'address:0x1', 'workspace': 7}),
        ('send.land.refuse_address_type', send.land_lua, {'address': 0x55d0c0de0001, 'workspace': 7}),
        ('send.land.refuse_workspace_range', send.land_lua, {'address': w1, 'workspace': 2147483648}),
        ('send.land.refuse_half_position_y', send.land_lua, {'address': w1, 'workspace': 7, 'y': 5}),
        ('send.land.refuse_position_below', send.land_lua, {'address': w1, 'workspace': 7, 'x': -1_000_001, 'y': 0}),
        ('send.land.refuse_workspace', send.land_lua, {'address': w1, 'workspace': 0}),
        ('send.land.refuse_workspace_type', send.land_lua, {'address': w1, 'workspace': True}),
        ('send.land.refuse_half_position', send.land_lua, {'address': w1, 'workspace': 7, 'x': 5}),
        ('send.land.refuse_position_type', send.land_lua, {'address': w1, 'workspace': 7, 'x': 5.0, 'y': 5}),
        ('send.land.refuse_position_range', send.land_lua, {'address': w1, 'workspace': 7, 'x': 5, 'y': 1_000_001}),
        # unified_cursor.py
        *_bind_cases(unified_cursor.click_binds_lua),
        ('unified_cursor.enter_desktop', unified_cursor.enter_desktop_lua, BIND),
        ('unified_cursor.enter_desktop_at', unified_cursor.enter_desktop_lua, {**BIND, 'x': 2880, 'y': 600}),
        ('unified_cursor.enter_desktop_at_focus', unified_cursor.enter_desktop_lua,
         {**BIND, 'x': 2880, 'y': 600, 'monitor': 'XR-2'}),
        ('unified_cursor.enter_desktop_focus_without_point', unified_cursor.enter_desktop_lua, {**BIND, 'monitor': 'XR-2'}),
        ('unified_cursor.enter_desktop.refuse_monitor', unified_cursor.enter_desktop_lua,
         {**BIND, 'x': 1, 'y': 1, 'monitor': 'DP-1'}),
        ('unified_cursor.enter_desktop.refuse_monitor_type', unified_cursor.enter_desktop_lua,
         {**BIND, 'x': 1, 'y': 1, 'monitor': 2}),
        ('unified_cursor.confine', unified_cursor.confine_lua, {'x': 960, 'y': 600, 'name': 'XR-1'}),
        ('unified_cursor.confine.refuse_name', unified_cursor.confine_lua, {'x': 960, 'y': 600, 'name': 'XR-5'}),
        ('unified_cursor.confine.refuse_name_type', unified_cursor.confine_lua, {'x': 960, 'y': 600, 'name': None}),
        # video.py
        *_bind_cases(video.install_video_binds_lua),
        ('video.remove_video_binds', video.remove_video_binds_lua, {}),
        # view.py
        ('view.warp_cursor', view.warp_cursor_lua, {'x': -1_000_000, 'y': 1_000_000}),
        ('view.warp_cursor.refuse_x_type', view.warp_cursor_lua, {'x': 1.0, 'y': 0}),
        ('view.warp_cursor.refuse_y_range', view.warp_cursor_lua, {'x': 0, 'y': -1_000_001}),
        ('view.warp_cursor.refuse_x_range', view.warp_cursor_lua, {'x': 1_000_001, 'y': 0}),
        ('view.restore_mode', view.restore_mode_lua, {'mode': {'name': 'DP-2', 'x': 1920, 'y': 0, 'scale': 1.0}}),
        ('view.restore_mode_scaled', view.restore_mode_lua, {'mode': {'name': 'eDP-1', 'x': -1536, 'y': 0, 'scale': 1.25}}),
        ('view.restore_mode_int_scale', view.restore_mode_lua, {'mode': {'name': 'DP-2', 'x': 0, 'y': 0, 'scale': 1}}),
        ('view.restore_mode.refuse_name', view.restore_mode_lua, {'mode': {'name': 'XR-1', 'x': 0, 'y': 0, 'scale': 1.0}}),
        *_bind_cases(view.install_binds_lua),
        ('view.install_binds_lua.typing', view.install_binds_lua, {**BIND, 'typing': True}),
        ('view.install_binds_lua.panel_binds', view.install_binds_lua, {**BIND, 'binds': PANEL_BINDS}),
        ('view.install_binds_lua.panel_binds_typing', view.install_binds_lua, {**BIND, 'typing': True, 'binds': PANEL_BINDS}),
        ('view.install_binds_lua.refuse_duplicate_op', view.install_binds_lua,
         {**BIND, 'binds': [('CTRL + ALT + K', 'zoom_in'), ('CTRL + ALT + J', 'zoom_in')]}),
        ('view.install_binds_lua.refuse_unknown_op', view.install_binds_lua, {**BIND, 'binds': [('CTRL + ALT + K', 'shell')]}),
        ('view.install_binds_lua.refuse_chord_type', view.install_binds_lua, {**BIND, 'binds': [(5, 'zoom_in')]}),
        ('view.install_binds_lua.refuse_chord_form', view.install_binds_lua, {**BIND, 'binds': [('ctrl+alt+k', 'zoom_in')]}),
        ('view.binds_table_release', view._binds_lua,
         {'table': 'xr_workspace_binds', 'binds': [('CTRL + ALT + up', 'zoom_in'), ('SUPER + mouse:272', 'super_release')],
          'base': '/usr/bin/python3 -I /x/xr_control.py /run/c.sock run-1 a1b2c3'}),
        ('view.remove_binds', view.remove_binds_lua, {}),
        *_bind_cases(view.install_send_binds_lua),
        ('view.install_send_binds_lua.panel_binds', view.install_send_binds_lua, {**BIND, 'binds': PANEL_SEND_BINDS}),
        ('view.install_send_binds_lua.refuse_op', view.install_send_binds_lua, {**BIND, 'binds': [('CTRL + ALT + Y', 'zoom_in')]}),
        ('view.remove_send_binds', view.remove_send_binds_lua, {}),
        *_bind_cases(view.install_drag_binds_lua),
        ('view.remove_drag_binds', view.remove_drag_binds_lua, {}),
        *_bind_cases(view.install_menu_binds_lua),
        *_bind_cases(view.install_options_binds_lua),
        ('view.remove_menu_binds', view.remove_menu_binds_lua, {}),
        # window_memory.py: the start-of-run placement.
        ('window_memory.moves.empty', window_memory.moves_lua, {'placement': _placement()}),
        ('window_memory.moves.sample', window_memory.moves_lua, {'placement': _sample_placement()}),
        ('window_memory.moves.split_groups', window_memory.moves_lua, {'placement': _placement(
            whole=[(i, 'XR-1', 60 + i) for i in range(1, 41)],
            windows=[(_address(i), f'XR-{i % 2 + 2}', 70 + i % 7, i % 2) for i in range(60)])}),
        ('window_memory.moves.cut_group', window_memory.moves_lua, {'placement': _placement(
            windows=[(_address(i), 'XR-2', 71, 1) for i in range(200)])}),
        ('window_memory.moves.refuse_shape', window_memory.moves_lua, {'placement': {'whole': []}}),
        ('window_memory.moves.refuse_output', window_memory.moves_lua, {'placement': _placement(whole=[(1, 'DP-1', 61)])}),
        ('window_memory.settle.empty', window_memory.settle_lua, {'placement': _placement()}),
        ('window_memory.settle.sample', window_memory.settle_lua, {'placement': _sample_placement()}),
        ('window_memory.settle.layouts_only', window_memory.settle_lua, {'placement': _placement(layouts=[(w1, 'scrolling')])}),
        ('window_memory.settle.state_only', window_memory.settle_lua, {'placement': _placement(state=[(w1, 1, 5, 6, 7, 8)])}),
        ('window_memory.settle.focus_only', window_memory.settle_lua, {'placement': _placement(focus=w1)}),
        ('window_memory.settle.monitor_only', window_memory.settle_lua, {'placement': _placement(monitor='DP-1')}),
        ('window_memory.settle.long_order', window_memory.settle_lua, {'placement': _placement(
            order=[[(_address(i), i, 1000, 1000) for i in range(250)], []])}),
        ('window_memory.settle.many_fullscreen', window_memory.settle_lua, {'placement': _placement(
            fullscreen=[(_address(i), i % 4, (i + 1) % 4) for i in range(200)], shown=[('XR-1', w1)], focus=w2)}),
        ('window_memory.settle.refuse_layout', window_memory.settle_lua, {'placement': _placement(layouts=[(w1, 'Scrolling')])}),
        ('window_memory.settle.refuse_monitor', window_memory.settle_lua, {'placement': _placement(monitor='-DP')}),
        ('window_memory.verify', window_memory.verify_lua, {}),
    ]
    names = [c[0] for c in cases]
    assert len(names) == len(set(names)), 'duplicate case name'
    return cases


def _jsonable(value):
    if isinstance(value, (list, tuple)): return [_jsonable(v) for v in value]
    if isinstance(value, dict): return {k: _jsonable(v) for k, v in value.items()}
    return value


def run_case(fn, kwargs):
    try: return {'lua': fn(**kwargs)}
    except (ValueError, TypeError) as exc: return {'refused': f'{type(exc).__name__}: {exc}'}


def lua_corpus():
    out = {'schema': 1, 'source': 'tests/native_spike/lua_corpus.py', 'cases': []}
    for name, fn, kwargs in lua_cases():
        out['cases'].append({'name': name, 'generator': name_of(fn), 'args': _jsonable(kwargs), **run_case(fn, kwargs)})
    for module, const in CONSTANTS:
        label = f'{module.__name__.rsplit(".", 1)[1]}.{const}'
        out['cases'].append({'name': label, 'generator': 'const:' + label, 'args': None, 'lua': getattr(module, const)})
    return out


# --- request/reply transcripts -------------------------------------------------------------------------------------

INSTANCE = '0123456789abcdef0123456789abcdef01234567_1700000000_1'
MODES = ['3840x1200@90.00Hz', '3840x1200@60.00Hz', '1920x1200@120.00Hz', '1920x1080@120.00Hz', '1920x1080@60.00Hz']


def _monitor(i, name, **over):
    m = {'id': i, 'name': name, 'description': f'Synthetic {name}', 'width': 1920, 'height': 1080, 'refreshRate': 60.0,
         'x': 1920 * i, 'y': 0, 'scale': 1.0, 'transform': 0, 'disabled': False, 'dpmsStatus': True, 'mirrorOf': 'none',
         'activeWorkspace': {'id': i + 1, 'name': str(i + 1)}, 'reserved': [0, 26, 0, 0], 'availableModes': MODES}
    m.update(over)
    return m


class Compositor:
    """A scripted stand-in for Hyprland's request socket: one request per connection, as Hyprland serves it. It applies
    hl.monitor() rules to its monitor list (so the mode read-backs see them), answers the n-th /eval with
    `eval_replies[n]` (the last one repeats), and keeps every exchange."""
    def __init__(self, root, eval_replies=(b'ok',)):
        self.monitors = [_monitor(0, 'eDP-1', width=2880, height=1800, scale=1.5),
                         _monitor(1, 'DP-2', width=1920, height=1200, refreshRate=120.0, x=1920)]
        self.eval_replies, self.exchanges = list(eval_replies), []
        directory = root / 'hypr' / INSTANCE
        directory.mkdir(parents=True, mode=0o700)
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(str(directory / '.socket.sock'))
        self.server.listen(8)
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _reply(self, request):
        text = request.decode('utf-8')
        if text.startswith('/eval '):
            reply = self.eval_replies.pop(0) if len(self.eval_replies) > 1 else self.eval_replies[0]
            rule = re.fullmatch(r'/eval hl\.monitor\(\{ output = "([^"]+)", mode = "(\d+)x(\d+)@([0-9.]+)", '
                                r'position = "(-?\d+)x(-?\d+)", scale = ([0-9.]+)(?:, transform = \d+)? \}\)', text)
            if rule and reply == b'ok':
                m = next(m for m in self.monitors if m['name'] == rule[1])
                m.update(width=int(rule[2]), height=int(rule[3]), refreshRate=float(rule[4]), x=int(rule[5]),
                         y=int(rule[6]), scale=float(rule[7]))
            return reply
        replies = {
            'j/monitors all': json.dumps(self.monitors).encode(),
            'j/workspaces': json.dumps([{'id': 1, 'name': '1', 'monitor': 'eDP-1', 'windows': 2},
                                        {'id': 2, 'name': '2', 'monitor': 'DP-2', 'windows': 0}]).encode(),
            'j/clients': json.dumps([{'address': '0x55d0c0de0001', 'workspace': {'id': 1, 'name': '1'}, 'floating': False,
                                      'at': [0, 26], 'size': [1920, 1174], 'class': 'Alacritty', 'title': 'shell'}]).encode(),
            'j/activewindow': b'{"address":"0x55d0c0de0001","class":"Alacritty"}',
            'j/cursorpos': b'{"x":960,"y":540}',
            'j/getoption input:follow_mouse': b'{"option":"input:follow_mouse","int":1,"set":true}',
            'j/getoption general:col.active_border': b'{"option":"general:col.active_border","custom":"ff33ccff","set":true}',
            'binds': b'bind\n\tmodmask: 12\n\tsubmap: \n\tkey: up\n\tkeycode: 0\n\tdispatcher: __lua\n\targ: \n\n',
            'j/plugin list': b'[{"name":"xr-ownership","author":"XR Workspace","handle":"0x5555","version":"1"}]',
            '/reload': b'ok',
        }
        if text.startswith(('/dispatch ', '/keyword ', '/plugin ')): return b'ok'
        return replies.get(text, b'unknown request')

    def _serve(self):
        with self.server:
            while True:
                try: conn, _ = self.server.accept()
                except OSError: return
                with conn:
                    request = bytearray(conn.recv(65536))
                    conn.settimeout(0.05)  # the client sends one request and then waits: read until it pauses
                    try:
                        while part := conn.recv(65536): request.extend(part)
                    except TimeoutError: pass
                    if request == b'stop': return
                    reply = self._reply(bytes(request))
                    self.exchanges.append({'request': bytes(request).decode('utf-8'), 'reply': reply.decode('utf-8')})
                    try: conn.sendall(reply)
                    except (BrokenPipeError, ConnectionResetError): pass

    def close(self):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.connect(self.server.getsockname()); s.sendall(b'stop')
        self.thread.join(3)


PLUGIN = '/home/user/.local/share/viture-workspace/bin/0123456789ab/libxr-ownership.so'
B = tuple(BIND.values())


def transcript_scenarios():
    """(name, call, eval_replies). `call(client)` makes the HyprlandClient calls; the transcript is what went over the
    wire."""
    w1 = _address(1)
    return [
        ('query.monitors', lambda c: c.query('monitors'), (b'ok',)),
        ('query.workspaces', lambda c: c.query('workspaces'), (b'ok',)),
        ('query.clients', lambda c: c.query('clients'), (b'ok',)),
        ('query.activewindow', lambda c: c.query('activewindow'), (b'ok',)),
        ('query.cursorpos', lambda c: c.query('cursorpos'), (b'ok',)),
        ('query.getoption_follow_mouse', lambda c: c.query('getoption', 'input:follow_mouse'), (b'ok',)),
        ('query.getoption_active_border', lambda c: c.query('getoption', 'general:col.active_border'), (b'ok',)),
        ('mutate.follow_mouse', lambda c: c.mutate('keyword input:follow_mouse 2'), (b'ok',)),
        ('mutate.move_workspace', lambda c: c.mutate('dispatch moveworkspacetomonitor 4 XR-2'), (b'ok',)),
        ('mutate.focus_desktop', lambda c: c.mutate(view.focus_command('XR-1')), (b'ok',)),
        ('mutate.send_window', lambda c: c.mutate(send.move_command(w1, 7)), (b'ok',)),
        ('mutate.drop_window', lambda c: c.mutate(send.move_command(w1, 7, follow=False)), (b'ok',)),
        ('binds_text', lambda c: c.binds_text(), (b'ok',)),
        ('reload_config', lambda c: c.reload_config(), (b'ok',)),
        ('plugins_loaded', lambda c: c.plugins_loaded(), (b'ok',)),
        ('plugin_load', lambda c: c.plugin_load(PLUGIN, data_home=Path('/nonexistent')), (b'ok',)),
        ('plugin_unload', lambda c: c.plugin_unload(PLUGIN, data_home=Path('/nonexistent')), (b'ok',)),
        ('install_view_binds', lambda c: c.install_view_binds(*B), (b'ok',)),
        ('install_view_binds.rejected', lambda c: c.install_view_binds(*B), (b'error: bad bind',)),
        ('install_view_binds.send_rejected', lambda c: c.install_view_binds(*B), (b'ok', b'error: bad bind', b'ok')),
        ('install_view_binds.panel_binds', lambda c: c.install_view_binds(
            *B, binds=PANEL_BINDS, send_binds=PANEL_SEND_BINDS), (b'ok',)),
        ('reinstall_view_binds.panel_binds', lambda c: c.reinstall_view_binds(
            *B, binds=PANEL_BINDS, send_binds=PANEL_SEND_BINDS), (b'ok',)),
        ('reinstall_view_binds', lambda c: c.reinstall_view_binds(*B), (b'ok',)),
        ('reinstall_view_binds.typing', lambda c: c.reinstall_view_binds(*B, typing=True), (b'ok',)),
        ('remove_view_binds', lambda c: c.remove_view_binds(), (b'ok',)),
        ('land_window', lambda c: c.land_window(w1, 2147483647, -100, 1_000_000), (b'ok',)),
        ('adopt_windows', lambda c: c.adopt_windows(ONE_OF_EACH), (b'ok',)),
        ('place_windows.moves', lambda c: c.place_windows(_sample_placement(), 'moves'), (b'ok',)),
        ('place_windows.settle', lambda c: c.place_windows(_sample_placement(), 'settle'), (b'ok',)),
        ('fit_windows', lambda c: c.fit_windows(FIT), (b'ok',)),
        ('restore_layout', lambda c: c.restore_layout(_snapshot()), (b'ok',)),
        ('unpin_xr_windows', lambda c: c.unpin_xr_windows(), (b'ok',)),
        ('install_options_binds', lambda c: c.install_options_binds(*B), (b'ok',)),
        ('open_key_panel', lambda c: c.open_key_panel(*B), (b'ok',)),
        ('close_key_panel', lambda c: c.close_key_panel(), (b'ok',)),
        ('set_dpms.off', lambda c: c.set_dpms('eDP-1', False, keep='DP-2'), (b'ok',)),
        ('set_dpms.on', lambda c: c.set_dpms('HDMI-A-1', True), (b'ok',)),
        ('apply_session_mode', lambda c: c.apply_session_mode('DP-2'), (b'ok',)),
        ('restore_session_mode', lambda c: c.restore_session_mode({'name': 'DP-2', 'x': 1920, 'y': 0, 'scale': 1.0}), (b'ok',)),
        ('apply_wide_mode', lambda c: c.apply_wide_mode('DP-2', 90.0), (b'ok',)),
        ('apply_wide_mode.rejected', lambda c: c.apply_wide_mode('DP-2', 90.0), (b'error: no such mode',)),
        ('apply_pro2_mode', lambda c: c.apply_pro2_mode('DP-2', 60.0), (b'ok',)),
        ('park_presentation', lambda c: c.park_presentation('DP-2'), (b'ok',)),
        ('install_video_binds', lambda c: c.install_video_binds(*B), (b'ok',)),
        ('remove_video_binds', lambda c: c.remove_video_binds(), (b'ok',)),
        ('install_menu_binds', lambda c: c.install_menu_binds(*B), (b'ok',)),
        ('remove_menu_binds', lambda c: c.remove_menu_binds(), (b'ok',)),
        ('install_resize_binds', lambda c: c.install_resize_binds(*B), (b'ok',)),
        ('remove_resize_binds', lambda c: c.remove_resize_binds(), (b'ok',)),
        ('grab_pointer', lambda c: c.grab_pointer(*B, 960, 540), (b'ok',)),
        ('release_pointer', lambda c: c.release_pointer(), (b'ok',)),
        ('release_pointer.at', lambda c: c.release_pointer(-1920, 1200), (b'ok',)),
        ('warp_cursor', lambda c: c.warp_cursor(-1_000_000, 1_000_000), (b'ok',)),
        ('hide_overview_cursor', lambda c: c.hide_overview_cursor(), (b'ok',)),
        ('show_overview_cursor', lambda c: c.show_overview_cursor(), (b'ok',)),
        ('lock_pointer', lambda c: c.lock_pointer(*B, 4800, 600), (b'ok',)),
        ('enter_desktop', lambda c: c.enter_desktop(*B, 2880, 600, 'XR-2'), (b'ok',)),
        ('arm_click_binds', lambda c: c.arm_click_binds(*B), (b'ok',)),
        ('confine_cursor', lambda c: c.confine_cursor(960, 600, 'XR-1'), (b'ok',)),
        ('scroll_to_window', lambda c: c.scroll_to_window(w1), (b'ok',)),
        ('verified_session.not_hyprland', lambda c: c.verified_session(), (b'ok',)),
    ]


def record(call, eval_replies=(b'ok',)):
    """One scenario against a fresh stand-in compositor: its exchanges, and how the call ended."""
    from tools.xr_controller import install
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        os.chmod(root, 0o700)
        (root / 'hypr').mkdir(mode=0o700)
        compositor = Compositor(root, eval_replies)
        try:
            client = hyprland.HyprlandClient(INSTANCE, root)
            with patch.object(install, 'load_installed', return_value={'plugin': {'path': PLUGIN}}):
                try: result = {'returned': _jsonable(call(client))}
                except ValueError as exc: result = {'raised': f'ValueError: {exc}'}
        finally: compositor.close()
        return {'exchanges': compositor.exchanges, **result}


def transcript_corpus():
    out = {'schema': 1, 'source': 'tests/native_spike/lua_corpus.py', 'instance': INSTANCE,
           'note': 'One request per connection. Requests and replies are the exact bytes, as UTF-8 text.', 'scenarios': []}
    for name, call, eval_replies in transcript_scenarios():
        out['scenarios'].append({'name': name, 'eval_replies': [r.decode() for r in eval_replies],
                                 **record(call, eval_replies)})
    return out


def dumps(value):
    return json.dumps(value, indent=1, ensure_ascii=False) + '\n'


def main(argv):
    if argv not in ([], ['--write']): raise SystemExit('usage: lua_corpus.py [--write]')
    files = ((LUA_FILE, dumps(lua_corpus())), (TRANSCRIPT_FILE, dumps(transcript_corpus())))
    stale = [path for path, text in files if not path.exists() or path.read_text() != text]
    if argv == ['--write']:
        CORPUS.mkdir(parents=True, exist_ok=True)
        for path, text in files: path.write_text(text)
        print('wrote', *(str(p.relative_to(ROOT)) for p, _ in files))
        return 0
    for path in stale: print('stale:', path.relative_to(ROOT))
    return 1 if stale else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
