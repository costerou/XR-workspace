"""Phase 6 corpus for the Rust rebuild (docs/rust-rebuild-plan.md): the supervisor's window placement decisions.

One golden file, rust/corpus/placement/plans.json, made only by this module from the shipped Python code: what adopt,
window_memory, desktops, fit_windows, send, resize and geometry decide for fixed Hyprland replies, refusals included. The
fixtures hold every window the supervisor must never move (pinned ones, windows with no `pinned` key, windows on special
workspaces, the XR Workspace panel under its current and its legacy title) next to ones it must, and more occupied
workspaces than desktops. rust/crates/xr-workspace/tests/placement_corpus.rs replays every case against the Rust port
and must get the same JSON, or refuse the same input with the same message.

Regenerate (after a deliberate change to one of those modules) with:

    TMPDIR=/tmp /usr/bin/python3 tests/native_spike/placement_corpus.py --write

test_placement_corpus.py fails when the file and the Python code disagree.
"""
import copy
import dataclasses
import json
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))

from tools.native_spike import adopt, desktops, fit_windows, geometry, resize, send, window_memory  # noqa: E402

PLACEMENT_FILE = ROOT / 'rust' / 'corpus' / 'placement' / 'plans.json'


def _address(i):
    return f'0x{0x55d0c0de0000 + i:x}'


def _client(i, workspace, name, cls, title, at, size, **extra):
    c = {'address': _address(i), 'mapped': True, 'hidden': False, 'at': at, 'size': size,
         'workspace': {'id': workspace, 'name': name}, 'floating': False, 'pseudo': False, 'monitor': 0,
         'class': cls, 'title': title, 'initialClass': cls, 'initialTitle': title, 'pid': 1000 + i, 'xwayland': False,
         'pinned': False, 'fullscreen': 0, 'fullscreenClient': 0, 'grouped': [], 'tags': [], 'swallowing': '0x0',
         'focusHistoryID': i}
    c.update(extra)
    return c


def _workspace(wid, name, monitor, layout='dwindle'):
    return {'id': wid, 'name': name, 'monitor': monitor, 'monitorID': 0, 'windows': 1, 'hasfullscreen': False,
            'lastwindow': '0x0', 'lastwindowtitle': '', 'ispersistent': False, 'tiledLayout': layout}


def _monitor(mid, name, width, height, scale, x, y, active, reserved=(0, 0, 0, 0)):
    return {'id': mid, 'name': name, 'description': name, 'make': '', 'model': '', 'serial': '', 'width': width,
            'height': height, 'refreshRate': 60.0, 'x': x, 'y': y, 'activeWorkspace': {'id': active, 'name': str(active)},
            'specialWorkspace': {'id': 0, 'name': ''}, 'reserved': list(reserved), 'scale': scale, 'transform': 0,
            'focused': mid == 0, 'dpmsStatus': True, 'vrr': False, 'disabled': False}


MONITORS = [
    _monitor(0, 'DP-1', 2560, 1440, 1.25, 0, 0, 1, reserved=(0, 26, 0, 0)),
    _monitor(1, 'XR-1', 1920, 1080, 1.0, 2048, 0, 11),
    _monitor(2, 'XR-2', 2560, 1080, 1.0, 3968, 0, 12, reserved=(0, 30, 0, 0)),
    _monitor(3, 'HDMI-A-1', 1920, 1080, 1.0, -1920, 0, 7),
]

WORKSPACES = [
    _workspace(1, '1', 'DP-1'), _workspace(2, '2', 'DP-1'), _workspace(3, '3', 'DP-1'), _workspace(4, '4', 'DP-1'),
    _workspace(5, '5', 'DP-1'), _workspace(6, '6', 'DP-1', 'scrolling'), _workspace(8, 'eight', 'DP-1'),
    _workspace(-98, 'special:scratch', 'DP-1'), _workspace(7, '7', 'HDMI-A-1'),
    _workspace(11, '11', 'XR-1', 'scrolling'), _workspace(12, '12', 'XR-2'), _workspace(13, '13', 'XR-2'),
    _workspace(-97, 'special:magic', 'XR-1'),
]

# The physical monitor DP-1 when a run starts: what adoption takes and what it must leave.
FALLBACK = [
    _client(1, 1, '1', 'firefox', 'Mozilla Firefox', [0, 26], [1022, 1126]),
    _client(2, 1, '1', 'kitty', 'kitty', [1026, 26], [1022, 1126]),
    _client(3, 2, '2', 'kitty', 'notes', [0, 26], [2048, 1126]),
    _client(4, 2, '2', 'mpv', 'pinned video', [100, 100], [640, 360], floating=True, pinned=True),
    _client(5, 3, '3', 'org.quickshell', 'XR Workspace', [0, 0], [600, 80], floating=True),
    _client(6, 4, '4', 'org.quickshell', 'VITURE Workspace', [0, 0], [600, 80], floating=True),
    _client(7, 4, '4', 'Alacritty', 'shell', [0, 26], [2048, 1126]),
    _client(8, 5, '5', 'obsidian', 'vault', [0, 26], [2048, 1126]),
    _client(9, 6, '6', 'org.quickshell', 'another shell window', [10, 40], [500, 300], floating=True),
    _client(10, 6, '6', 'code', 'main.rs', [0, 26], [2048, 1126], focusHistoryID=0),
    _client(11, -98, 'special:scratch', 'firefox', 'scratch', [0, 26], [2048, 1126]),
    _client(12, 7, '7', 'slack', 'Slack', [-1920, 0], [1920, 1080], monitor=3),
    _client(13, 8, 'eight', 'Spotify', 'Spotify', [300, 200], [1200, 800], floating=True),
]
del FALLBACK[7]['pinned']  # no `pinned` key: it stays (pinned is not False)

# The glasses' desktops while the run is live: what the window memory remembers and what it must leave out.
LIVE = [
    _client(21, 11, '11', 'firefox', 'Mozilla Firefox', [2048, 0], [960, 1080], monitor=1),
    _client(22, 11, '11', 'kitty', 'kitty', [3008, 0], [960, 540], monitor=1),
    _client(23, 11, '11', 'kitty', 'notes', [3009, 540], [960, 540], monitor=1),
    _client(24, 11, '11', 'org.quickshell', 'XR Workspace', [2048, 0], [600, 80], floating=True, monitor=1),
    _client(25, 12, '12', 'Alacritty', 'shell', [3968, 30], [2560, 1050], monitor=2, fullscreen=1, fullscreenClient=1),
    _client(26, 12, '12', 'Spotify', 'Spotify', [5000, 200.4], [1200, 800], floating=True, monitor=2),
    _client(27, 12, '12', 'mpv', 'pinned video', [4000, 100], [640, 360], floating=True, pinned=True, monitor=2),
    _client(28, -97, 'special:magic', 'firefox', 'scratch', [2048, 0], [1920, 1080], monitor=1),
    _client(29, 13, '13', 'code', 'main.rs', [3968, 30], [2560, 1050], monitor=2, mapped=False),
    _client(30, 13, '13', 'obsidian', 'vault', [3968, 30], [2560, 1050], monitor=2),
    _client(31, 13, '13', 'org.quickshell', 'VITURE Workspace', [3968, 0], [600, 80], floating=True, monitor=2),
]
del LIVE[-2]['pinned']


def _spec(n, x, width=1920, height=1080, scale=1.0):
    return {'id': f'id-{n}', 'name': f'XR-{n}', 'index': n - 1, 'width': width, 'height': height,
            'refresh_millihz': 120000, 'scale': scale, 'x': x, 'y': 0}


SPECS = [_spec(1, 2048), _spec(2, 3968, 2560, 1080)]
PLACEMENTS = [{'x': -300, 'y': 0, 'distance': 100, 'curvature': 0}, {'x': 300, 'y': 50, 'distance': 120, 'curvature': 30}]
FOUR = [_spec(1, 0), _spec(2, 1920), _spec(3, 3840), _spec(4, 5760)]
CENTRE = [{'x': 0, 'y': 0, 'distance': 100, 'curvature': 0}]


def _memory(instance='hypr-a'):
    context = {'XR-1': {'placement': PLACEMENTS[0], 'roll': 120}, 'XR-2': {'placement': PLACEMENTS[1], 'roll': None}}
    return window_memory.capture(LIVE, WORKSPACES, MONITORS, ['XR-2', 'XR-1'], instance, context)


def _moved(clients, delta):
    """The windows of LIVE as they are after Hyprland restarted: new addresses, all on DP-1's workspaces."""
    out = []
    for i, c in enumerate(clients):
        c = copy.deepcopy(c)
        c['address'] = _address(100 + i + delta)
        c['workspace'] = {'id': 1 + i % 3, 'name': str(1 + i % 3)}
        c['monitor'] = 0
        out.append(c)
    return out


def _adopt_cases():
    yield 'stays_unpinned', 'adopt.stays', [FALLBACK[0]]
    yield 'stays_pinned', 'adopt.stays', [FALLBACK[3]]
    yield 'stays_panel', 'adopt.stays', [FALLBACK[4]]
    yield 'stays_legacy_panel', 'adopt.stays', [FALLBACK[5]]
    yield 'stays_other_shell_window', 'adopt.stays', [FALLBACK[8]]
    yield 'stays_no_pinned_key', 'adopt.stays', [FALLBACK[7]]
    yield 'stays_pinned_null', 'adopt.stays', [{**FALLBACK[0], 'pinned': None}]
    yield 'occupied_fallback', 'adopt.occupied', [WORKSPACES, FALLBACK, 'DP-1']
    yield 'occupied_other_monitor', 'adopt.occupied', [WORKSPACES, FALLBACK, 'HDMI-A-1']
    yield 'occupied_glasses', 'adopt.occupied', [WORKSPACES, LIVE, 'XR-1']
    yield 'occupied_bad_address', 'adopt.occupied', [WORKSPACES, FALLBACK + [{**FALLBACK[0], 'address': 'x'}], 'DP-1']
    yield 'occupied_not_lists', 'adopt.occupied', [{}, FALLBACK, 'DP-1']
    unordered = [{**FALLBACK[1], 'at': [1, 'a']}, {**FALLBACK[0], 'at': [500, 900]}, FALLBACK[2]]
    yield 'occupied_layout_order', 'adopt.occupied', [WORKSPACES, unordered, 'DP-1']
    for names in (['XR-1'], ['XR-2', 'XR-1'], ['XR-1', 'XR-2', 'XR-3', 'XR-4']):
        yield f'plan_{len(names)}_desktops', 'adopt.plan', [WORKSPACES, FALLBACK, 'DP-1', names]
    yield 'plan_no_outputs', 'adopt.plan', [WORKSPACES, FALLBACK, 'DP-1', []]
    yield 'plan_bad_output', 'adopt.plan', [WORKSPACES, FALLBACK, 'DP-1', ['XR-1', 'DP-2']]
    yield 'plan_nothing_to_move', 'adopt.plan', [WORKSPACES, [FALLBACK[3], FALLBACK[4], FALLBACK[10]], 'DP-1', ['XR-1']]


def _memory_cases():
    context = {'XR-1': {'placement': PLACEMENTS[0], 'roll': 120}}
    yield 'capture_live', 'window_memory.capture', [LIVE, WORKSPACES, MONITORS, ['XR-2', 'XR-1'], 'hypr-a', context]
    yield 'capture_one_desktop', 'window_memory.capture', [LIVE, WORKSPACES, MONITORS, ['XR-1'], '', None]
    yield 'capture_fallback_is_not_a_desktop', 'window_memory.capture', [FALLBACK, WORKSPACES, MONITORS, ['XR-1'], '', None]
    yield 'capture_not_lists', 'window_memory.capture', [LIVE, None, MONITORS, ['XR-1'], '', None]
    memory = _memory()
    yield 'validate_live', 'window_memory.validate', [memory]
    yield 'validate_extra_key', 'window_memory.validate', [{**memory, 'more': 1}]
    yield 'validate_twice', 'window_memory.validate', [{**memory, 'windows': memory['windows'][:1] * 2}]
    yield 'validate_bad_window', 'window_memory.validate', [{**memory, 'windows': [{**memory['windows'][0], 'pid': -1}]}]
    yield 'validate_bad_desktop', 'window_memory.validate',\
        [{**memory, 'desktops': {'XR-1': {**memory['desktops']['XR-1'], 'roll': 99999}}}]
    yield 'validate_bad_name', 'window_memory.validate', [{**memory, 'desktops': {'DP-1': memory['desktops']['XR-1']}}]
    yield 'validate_long_instance', 'window_memory.validate', [{**memory, 'instance': 'x' * 129}]
    records = memory['windows']
    yield 'match_same_instance', 'window_memory.match', [records, LIVE, True, False]
    yield 'match_new_instance', 'window_memory.match', [records, _moved(LIVE, 0), False, False]
    yield 'match_exact', 'window_memory.match', [records, LIVE + _moved(LIVE, 0), True, True]
    yield 'match_exact_new_instance', 'window_memory.match', [records, _moved(LIVE, 0), False, True]
    restarted = _moved([c for c in LIVE if not window_memory.stays(c)], 0) + FALLBACK
    for names in (['XR-1', 'XR-2'], ['XR-1'], ['XR-2', 'XR-3', 'XR-1']):
        yield f'plan_restart_{len(names)}', 'window_memory.plan',\
            [memory, restarted, WORKSPACES, MONITORS, names, 'DP-1', 'hypr-b', None, False]
    active = {'address': _address(1), 'monitor': 'DP-1'}
    yield 'plan_same_instance', 'window_memory.plan',\
        [memory, restarted, WORKSPACES, MONITORS, ['XR-1', 'XR-2'], 'DP-1', 'hypr-a', active, False]
    yield 'plan_exact', 'window_memory.plan',\
        [memory, restarted, WORKSPACES, MONITORS, ['XR-1', 'XR-2'], 'DP-1', 'hypr-a', {'address': 'bad', 'monitor': '-'}, True]
    yield 'plan_whole_workspace', 'window_memory.plan',\
        [memory, FALLBACK, WORKSPACES, MONITORS, ['XR-1', 'XR-2'], 'DP-1', 'hypr-b', None, False]
    yield 'plan_nothing_remembered', 'window_memory.plan',\
        [{'instance': '', 'desktops': {}, 'windows': []}, FALLBACK, WORKSPACES, MONITORS, ['XR-1'], 'DP-1', '', None, False]
    yield 'plan_bad_outputs', 'window_memory.plan', [memory, restarted, WORKSPACES, MONITORS, ['XR-0'], 'DP-1', '', None, False]
    yield 'empty_nothing', 'window_memory.empty', [{'whole': [], 'windows': []}]
    yield 'empty_whole', 'window_memory.empty', [{'whole': [[1, 'XR-1', 11]], 'windows': []}]
    yield 'empty_windows', 'window_memory.empty', [{'whole': [], 'windows': [[_address(1), 'XR-1', 11, 0]]}]


def _desktop_cases():
    for action, index, extra in (('ultrawide', 0, {}), ('ultrawide', 1, {}), ('add', 0, {}), ('add', 1, {}),
                                 ('remove', 0, {}), ('remove', 1, {}), ('scale', 0, {'scale': 1.5}),
                                 ('scale', 1, {'scale': 1.25}), ('scale', 0, {'scale': 1.75}),
                                 ('resolution', 0, {'size': [2560, 1440]}), ('resolution', 1, {'size': [1280, 721]}),
                                 ('resolution', 0, {'size': [1920, 1080]}), ('reset', 1, {}), ('add', 2, {}),
                                 ('remove', -1, {})):
        name = f"plan_{action}_{index}" + ''.join(f'_{v}' for v in extra.values()).replace(' ', '')
        yield name, 'desktops.plan', [SPECS, PLACEMENTS, action, index, None, [], extra.get('scale'), extra.get('size')]
    yield 'plan_add_held', 'desktops.plan', [SPECS, PLACEMENTS, 'add', 0, [100, 0], ['XR-3'], None, None]
    yield 'plan_add_full', 'desktops.plan', [FOUR, PLACEMENTS + PLACEMENTS, 'add', 0, None, [], None, None]
    yield 'plan_reset_four', 'desktops.plan', [FOUR, PLACEMENTS + PLACEMENTS, 'reset', 0, None, [], None, None]
    yield 'plan_reset_one', 'desktops.plan', [SPECS[:1], CENTRE, 'reset', 0, None, [], None, None]
    yield 'plan_remove_last', 'desktops.plan', [SPECS[:1], CENTRE, 'remove', 0, None, [], None, None]
    yield 'plan_ultrawide_scaled', 'desktops.plan', [[_spec(3, 0, 1920, 1080, 1.5)], CENTRE, 'ultrawide', 0, None, [], None, None]
    yield 'plan_placements_mismatch', 'desktops.plan', [SPECS, CENTRE, 'add', 0, None, [], None, None]
    yield 'plan_gap_names', 'desktops.plan', [[_spec(1, 0), _spec(3, 1920)], PLACEMENTS, 'add', 1, None, [], None, None]


def _fit_cases():
    floating = [
        _client(41, 12, '12', 'a', 'inside', [4000, 100], [800, 600], floating=True),
        _client(42, 12, '12', 'b', 'off the right', [6000, 100], [800, 600], floating=True),
        _client(43, 12, '12', 'c', 'too big', [3968, 0], [3000, 1200], floating=True),
        _client(44, 12, '12', 'd', 'tiled', [9000, 0], [800, 600]),
        _client(45, 12, '12', 'e', 'fullscreen', [9000, 0], [800, 600], floating=True, fullscreen=2),
        _client(46, 13, '13', 'f', 'hidden', [9000, 0], [800, 600], floating=True, hidden=True),
        _client(47, 11, '11', 'g', 'other output', [9000, 0], [800, 600], floating=True),
        _client(48, 12, '12', 'h', 'floats', [3900.6, -20.5], [100.5, 100], floating=True),
        _client(49, 12, '12', 'i', 'empty', [6000, 0], [0, 100], floating=True),
        _client(50, -97, 'special:magic', 'j', 'special', [9000, 0], [800, 600], floating=True),
    ]
    floating[-1]['workspace'] = {'id': 12, 'name': '12'}
    floating[5]['workspace'] = {'id': 13, 'name': '13'}
    spec = _spec(2, 3968, 2560, 1080)
    yield 'free_area_reserved', 'fit_windows.free_area', [spec, MONITORS]
    yield 'free_area_none', 'fit_windows.free_area', [_spec(1, 2048), MONITORS]
    yield 'free_area_bad_reserved', 'fit_windows.free_area', [spec, [{**MONITORS[2], 'reserved': [0, 2000, 0, 0]}]]
    yield 'free_area_twice', 'fit_windows.free_area', [spec, MONITORS + MONITORS]
    yield 'plan_shrunk', 'fit_windows.plan', [floating, WORKSPACES, MONITORS, spec]
    yield 'plan_scaled', 'fit_windows.plan', [floating, WORKSPACES, MONITORS, _spec(2, 3968, 2560, 1080, 2.0)]
    yield 'plan_not_lists', 'fit_windows.plan', [floating, {}, MONITORS, spec]


def _send_cases():
    yield 'landing_inside', 'send.landing', [{'at': [2100, 50], 'size': [800, 600]}, MONITORS[1]]
    yield 'landing_off_edges', 'send.landing', [{'at': [5000, -400], 'size': [800, 600]}, MONITORS[1]]
    yield 'landing_too_big', 'send.landing', [{'at': [2500, 10], 'size': [2500, 2000]}, MONITORS[1]]
    yield 'landing_scaled', 'send.landing', [{'at': [2100.5, 1200.5], 'size': [800, 600]}, MONITORS[0]]
    yield 'landing_bad', 'send.landing', [{'at': [1], 'size': [800, 600]}, MONITORS[1]]
    yield 'landing_bad_scale', 'send.landing', [{'at': [1, 2], 'size': [800, 600]}, {**MONITORS[1], 'scale': 0}]
    stack = LIVE + [_client(60, 11, '11', 'mpv', 'float', [2100, 100], [400, 400], floating=True, monitor=1, focusHistoryID=5),
                    _client(61, 12, '12', 'mpv', 'pinned elsewhere', [2100, 100], [400, 400], pinned=True, monitor=1,
                            focusHistoryID=9)]
    for name, output, x, y in (('tiled', 'XR-1', 2500, 900), ('floating_over_tiled', 'XR-1', 2200, 200),
                               ('fullscreen', 'XR-2', 4500, 500), ('nothing', 'XR-1', 9999, 9999), ('no_output', 'XR-9', 0, 0),
                               ('pinned', 'XR-2', 4100, 150)):
        yield f'window_at_{name}', 'send.window_at', [stack, MONITORS, output, x, y]
    yield 'window_at_bad_monitors', 'send.window_at', [stack, {}, 'XR-1', 0, 0]
    for op in send.SEND_OPS + ('send_nowhere',):
        yield f'target_for_{op}', 'send.target_for', [op, ['XR-1', 'XR-2'], 'XR-2', 'DP-1']
    yield 'target_for_back_none', 'send.target_for', ['send_back', ['XR-1'], None, None]
    yield 'move_follow', 'send.move_command', [_address(3), 12, True]
    yield 'move_stay', 'send.move_command', [_address(3), 12, False]
    yield 'move_bad_address', 'send.move_command', ['address:0x1', 12, True]
    yield 'move_bad_workspace', 'send.move_command', [_address(3), 0, True]


def _other_cases():
    yield 'outputs_from_monitors', 'resize.outputs_from_monitors', [MONITORS, ['XR-2', 'XR-1', 'XR-3', 'DP-1']]
    yield 'outputs_from_monitors_odd', 'resize.outputs_from_monitors',\
        [[{**MONITORS[1], 'scale': 5}, {**MONITORS[2], 'width': 0}, {**MONITORS[0], 'x': 1.0}, MONITORS[0]], ['XR-1', 'XR-2', 'DP-1']]
    yield 'outputs_from_monitors_not_list', 'resize.outputs_from_monitors', [{}, ['XR-1']]
    for name, d in (('hd', {'width': 1920, 'height': 1080, 'scale': 1.0}), ('hd_scaled', {'width': 1920, 'height': 1080, 'scale': 1.5}),
                    ('ultrawide', {'width': 2560, 'height': 1080, 'scale': 1.0}), ('qhd', {'width': 2560, 'height': 1440, 'scale': 1.25}),
                    ('odd_height', {'width': 1000, 'height': 1000, 'scale': 2.5}), ('uw_4k', {'width': 5120, 'height': 2160, 'scale': 2.0})):
        yield f'toggle_ultrawide_{name}', 'geometry.toggle_ultrawide', [d]
    yield 'with_scale', 'geometry.with_scale', [{'width': 2560, 'height': 1440, 'scale': 1.0}, 1.25]
    yield 'with_scale_inexact', 'geometry.with_scale', [{'width': 1920, 'height': 1080, 'scale': 1.0}, 1.75]
    yield 'with_resolution_keeps', 'geometry.with_resolution', [{'width': 1920, 'height': 1080, 'scale': 1.5}, 2560, 1440]
    yield 'with_resolution_nearest', 'geometry.with_resolution', [{'width': 2560, 'height': 1440, 'scale': 1.25}, 1366, 768]
    yield 'with_resolution_odd', 'geometry.with_resolution', [{'width': 1920, 'height': 1080, 'scale': 1.0}, 1921, 1080]
    yield 'with_resolution_small', 'geometry.with_resolution', [{'width': 1920, 'height': 1080, 'scale': 1.0}, 320, 240]
    yield 'valid_scales', 'geometry.valid_scales', [2560, 1440]
    yield 'nearest_scale', 'geometry.nearest_scale', [2560, 1440, 1.75]
    yield 'validate_desktop_bad_keys', 'geometry.validate_desktop', [{'width': 1920, 'height': 1080}]
    yield 'validate_desktop_float', 'geometry.validate_desktop', [{'width': 1920.0, 'height': 1080, 'scale': 1}]
    yield 'validate_placement', 'geometry.validate_placement', [PLACEMENTS[1]]
    yield 'validate_placement_out', 'geometry.validate_placement', [{**PLACEMENTS[1], 'distance': 5}]


FUNCTIONS = {
    'adopt.stays': adopt.stays, 'adopt.occupied': adopt.occupied, 'adopt.plan': adopt.plan,
    'window_memory.capture': window_memory.capture, 'window_memory.validate': window_memory.validate,
    'window_memory.match': window_memory.match, 'window_memory.plan': window_memory.plan,
    'window_memory.empty': window_memory.empty,
    'desktops.plan': lambda specs, placements, action, index, origin, held, scale, size: desktops.plan(
        specs, placements, action, index, tuple(origin) if origin else None, tuple(held), scale,
        tuple(size) if size else None),
    'fit_windows.free_area': fit_windows.free_area, 'fit_windows.plan': fit_windows.plan,
    'send.landing': send.landing, 'send.window_at': send.window_at, 'send.target_for': send.target_for,
    'send.move_command': send.move_command,
    'resize.outputs_from_monitors': resize.outputs_from_monitors,
    'geometry.toggle_ultrawide': geometry.toggle_ultrawide, 'geometry.with_scale': geometry.with_scale,
    'geometry.with_resolution': geometry.with_resolution, 'geometry.valid_scales': geometry.valid_scales,
    'geometry.nearest_scale': geometry.nearest_scale, 'geometry.validate_desktop': geometry.validate_desktop,
    'geometry.validate_placement': geometry.validate_placement,
}


def _jsonable(v):
    if dataclasses.is_dataclass(v): return _jsonable(dataclasses.asdict(v))
    if isinstance(v, dict): return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)): return [_jsonable(x) for x in v]
    return v


class _Ids:
    """Stable ids for the outputs desktops.plan creates (a run uses uuid.uuid4)."""
    def __init__(self): self.n = 0

    def __call__(self):
        self.n += 1
        return f'00000000-0000-4000-8000-{self.n:012d}'


def run_case(name, function, args):
    case = {'name': name, 'function': function, 'args': copy.deepcopy(args)}
    with patch.object(desktops.uuid, 'uuid4', _Ids()):
        try:
            case['result'] = _jsonable(FUNCTIONS[function](*copy.deepcopy(args)))
        except ValueError as exc:
            case['refused'] = f'ValueError: {exc}'
    return case


def cases():
    out = []
    for group in (_adopt_cases, _memory_cases, _desktop_cases, _fit_cases, _send_cases, _other_cases):
        out.extend(run_case(*c) for c in group())
    names = [c['name'] for c in out]
    seen = {f"{c['function']}:{c['name']}" for c in out}
    assert len(seen) == len(names), 'duplicate case names'
    return out


def build():
    return {'schema': 1, 'source': 'tests/native_spike/placement_corpus.py', 'cases': cases()}


def dumps(value):
    return json.dumps(value, indent=1, ensure_ascii=False) + '\n'


def main(argv):
    text = dumps(build())
    if '--write' in argv:
        PLACEMENT_FILE.parent.mkdir(parents=True, exist_ok=True)
        PLACEMENT_FILE.write_text(text, encoding='utf-8')
        return 0
    if not PLACEMENT_FILE.exists() or PLACEMENT_FILE.read_text(encoding='utf-8') != text:
        print(f'stale: {PLACEMENT_FILE.relative_to(ROOT)}')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
