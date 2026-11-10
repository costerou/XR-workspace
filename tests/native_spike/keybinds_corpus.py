"""Phase 6 corpus for the Rust supervisor's key binds, bind check, options text and background (docs/rust-rebuild-plan.md).

Four golden files under rust/corpus/supervisor/, made only by this module from the shipped Python code:

* keybinds.json: tools/native_spike/keybinds.py. The bind tables, chord parsing and labels, the saved file (read and
  written), `hyprctl binds` parsing, conflicts, and scripted key bind panel sessions (BindControl): each step's frames
  byte for byte, the compositor calls, the toasts, the saved file and the panel's state.
* bind_check.json: tools/native_spike/bind_check.py. normalize, the run's chords, desktop chords and clashes from
  recorded binds, the harness Lua from given chunks, and check() against a stand-in Hyprland script.
* options_text.json: tools/native_spike/options_text.py. Key presses through the system libxkbcommon for several
  layouts, modifier states and compose sequences, and the inputs it refuses.
* environment.json: tools/native_spike/environment.py. Every function's results, and the renderer frames.

Regenerate (after a deliberate change to one of those modules) with:

    TMPDIR=/tmp /usr/bin/python3 tests/native_spike/keybinds_corpus.py --write

test_keybinds_corpus.py fails when the files and the Python code disagree. The Rust tests in
rust/crates/xr-workspace/src/supervisor/ hold the Rust port to them.
"""
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import threading
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT, ROOT / 'tests' / 'native_spike'):
    if str(p) not in sys.path: sys.path.insert(0, str(p))

from tools.native_spike import (bind_check, environment, input_lock, keybinds as kb, options_text,  # noqa: E402
                                resize, send, unified_cursor, view, window_memory)
import test_keybinds  # noqa: E402  (the `hyprctl binds` fixture text the Python tests use)

CORPUS = ROOT / 'rust' / 'corpus' / 'supervisor'
FILES = ('keybinds.json', 'bind_check.json', 'options_text.json', 'environment.json')

HYPRCTL, DESKTOP, OURS_ON_DELETE = test_keybinds.HYPRCTL, test_keybinds.DESKTOP, test_keybinds.OURS_ON_DELETE
UP, DOWN, PGUP, PGDN, HOME, END, ENTER, KP_ENTER, ESC = 111, 116, 112, 117, 110, 115, 36, 104, 9
J, K, L, R, DELETE, SPACE = 44, 45, 46, 27, 119, 65
SHIFT, CTRL, ALT, SUPER = 1, 4, 8, 64


def _error(fn, *args):
    try: return {'value': fn(*args)}
    except ValueError as exc: return {'error': str(exc)}


# --- keybinds.json --------------------------------------------------------------------------------------------------
CHORD_TEXTS = (
    'CTRL + ALT + K', 'ctrl+alt+k', 'CTRL+ALT+K', ' Ctrl +  Alt + k ', 'ALT + CTRL + up', 'SUPER + SHIFT + R',
    'shift + win + r', 'LOGO + j', 'mod4 + J', 'META + F1', 'MOD1 + Tab', 'control + delete', 'CTRL + ALT + prior',
    'CTRL + ALT + Next', 'CTRL + ALT + enter', 'CTRL + ALT + 1', 'CTRL + ALT + 0', 'CTRL + ALT + code:10',
    'CTRL + ALT + Page_Up', 'CTRL + SHIFT + ALT + SUPER + space', 'CTRL + ALT + bracketleft', 'CTRL + ALT + F12',
    # Refused.
    '', 'K', 'CTRL', 'CTRL + ', '+ K', 'CTRL + CTRL + K', 'CTRL + CONTROL + K', 'HYPER + K', 'CAPS + K',
    'CTRL + ALT + Escape', 'CTRL + ALT + mouse:272', 'CTRL + ALT + code:20', 'CTRL + ALT + F13', 'SHIFT + K',
    'CTRL + ALT + ' + 'x' * 60, 'CTRL + ALT + ' + 'K' * 52, 'CTRL + ALT + ' + 'K' * 51, 'CTRL + ALT + é',
    'CTRL\t+ ALT + K', 'CTRL + ALT + K\n', 'ctrl + alt + ß')
KEY_CHORD_MASKS = (0, 1, 2, 4, 5, 8, 9, 12, 13, 16, 20, 32, 64, 65, 68, 72, 76, 77, 128, 132, 255, 256, 260, -1, -4)
EXTRA_LABELS = ('CTRL + ALT + J', 'SUPER + SHIFT + code:19', 'CTRL + ALT + backslash', 'CTRL + ALT + apostrophe',
                'CTRL + SUPER + F7', 'CTRL + ALT + Return', 'CTRL + ALT + BackSpace', 'CTRL + ALT + Delete',
                'ALT + Insert', 'CTRL + ALT + Home', 'CTRL + grave', 'SUPER + Tab', 'CTRL + ALT + Escape')
LOAD_FILES = (
    None,  # missing
    '', 'not json', '[]', '{"schema": 1}', '{"schema": 2, "binds": {}}', '{"schema": "1", "binds": {}}',
    '{"schema": 1, "binds": []}', '{"schema": 1, "binds": {}}', '{"schema": 1.0, "binds": {"next": "ctrl+alt+j"}}',
    '{"schema": true, "binds": {"next": "ctrl+alt+j"}}',
    '{"schema": 1, "binds": {"next": "CTRL + ALT + J", "prev": "CTRL + ALT + left", "nope": "CTRL + ALT + Y",'
    ' "zoom_in": "J", "help": 3, "send_back": "super+shift+b"}}',
    '{"schema": 1, "binds": {"next": "CTRL + ALT + J", "next": "CTRL + ALT + M"}}',
    '{"schema": 1, "binds": {"binds_toggle": "CTRL + SUPER + J"}, "other": [1, 2]}',
    '﻿{"schema": 1, "binds": {"next": "CTRL + ALT + J"}}', '{"schema": 1, "binds": {"next": "CTRL + ALT + J"}} x',
    '  {"schema": 1, "binds": {"next": "CTRL + ALT + J"}}\n\n',
    '{"schema": 1, "binds": {"next": "CTRL + ALT + J", "prev": "CTRL + ALT + J"}}',
    b'{"schema": 1, "binds": {"next": "\xff"}}')
OVERRIDES = ({}, {'next': 'CTRL + ALT + J'}, {'zoom_in': 'CTRL + ALT + J', 'next': 'CTRL + SUPER + J'},
             {'send_back': 'SUPER + SHIFT + B', 'binds_toggle': 'CTRL + SUPER + K', 'help': 'ctrl+alt+f1'},
             {'next': 'CTRL + ALT + Delete'}, {'next': 'CTRL + ALT + L'}, {'next': 'CTRL + ALT + right'},
             {'zoom_in': 'CTRL + ALT + J', 'zoom_out': 'CTRL + ALT + J', 'prev': 'CTRL + ALT + J'},
             {'prev': 'SUPER + code:10', 'next': 'SUPER + 1', 'help': 'CTRL + SHIFT + S'},
             {'nope': 'CTRL + ALT + J', 'help': 'nonsense'})
ASCII_TEXTS = ('plain', '  padded  ', 'tab\there', 'line\nbreak', 'café', '☃ snow', 'x' * 47, 'x' * 48,
               'x' * 49, 'y' * 44 + '   z', '\x7f\x00', '', '   ', 'a' * 45 + ' ' * 10, '\U0001f600' * 50)
EXTRA_BINDS = (
    '', 'garbage', 'bind\n\tmodmask: x\n\tkey: K\n', 'bind\n\tmodmask: 12\n\tkey: K\n',
    'bind\r\n\tmodmask: 12\r\n\tkey: K\r\n\tdescription: crlf one\r\n',
    'binde\n  modmask : +12 \n  key: CTRL + ALT + k\n  catchall: true\n\nbindd\n\tmodmask: 1_2\n\tkey: SUPER + Prior\n'
    '\tdescription: x: y\n\n\n\n   \n\nnot a bind\n\tmodmask: 12\n',
    'bind\n\tmodmask: -1\n\tkey: J\n\tdescription: everything\n\nbind\n\tmodmask: 268\n\tkey: switch:on\n\n'
    'bind\n\tmodmask: 76\n\tkey: \n\nbind\n\tmodmask: 77\n\tkey: code:19\n\tdescription: Move to 10 → there\n',
    'bind\n\tmodmask: 12\n\tkey: Delete\n\tdescription: \n\nbind\n\tmodmask: 12\n\tkey: delete\n\tdescription: '
    + 'd' * 70 + '\n\nbind\n\tmodmask: 12\n\tkey: DELETE\n\tdescription: second\n')
FIND_CHORDS = ('CTRL + ALT + Delete', 'ALT + CTRL + delete', 'CTRL + ALT + Tab', 'SUPER + code:10', 'SUPER + 1',
               'CTRL + SHIFT + S', 'CTRL + ALT + K', 'CTRL + ALT + J', 'SUPER + Page_Up', 'SUPER + SHIFT + code:19',
               'SUPER + prior')


def _binds_cases():
    out = []
    for text in (HYPRCTL, DESKTOP, HYPRCTL + OURS_ON_DELETE) + EXTRA_BINDS:
        for ours in ((), ('CTRL + ALT + Delete',), ('CTRL + ALT + Delete', 'CTRL + ALT + DELETE', 'SUPER + 1')):
            d = kb.DesktopBinds.from_text(text, ours)
            out.append({'text': text, 'ours': list(ours), 'entries': kb.parse_binds(text),
                        'chords': sorted([sorted(mods), key, [list(e) for e in found]] for (mods, key), found in d.chords.items()),
                        'find': [[c, list(d.find(c)) if d.find(c) else None] for c in FIND_CHORDS]})
    return out


def _conflict_cases():
    out = []
    for overrides in OVERRIDES:
        for text in (None, HYPRCTL, DESKTOP, HYPRCTL + OURS_ON_DELETE):
            desktop = kb.DesktopBinds.from_text(text) if text is not None else None
            notes, off = kb.find_conflicts(kb.KeyMap(overrides), desktop)
            out.append({'overrides': overrides, 'desktop': text, 'notes': notes, 'off': sorted(off)})
    return out


def _load_cases(tmp):
    out = []
    for i, text in enumerate(LOAD_FILES):
        path = tmp / f'load{i}.json'
        if text is not None: path.write_bytes(text if isinstance(text, bytes) else text.encode())
        out.append({'file': text.decode('latin-1') if isinstance(text, bytes) else text, 'bytes': isinstance(text, bytes),
                    'overrides': kb.load(path).overrides})
    # A directory where the file should be: unreadable, the defaults.
    (tmp / 'dir.json').mkdir()
    out.append({'file': None, 'directory': True, 'overrides': kb.load(tmp / 'dir.json').overrides})
    return out


def _save_cases(tmp):
    out = []
    for i, overrides in enumerate(OVERRIDES):
        path = tmp / f'save{i}' / 'deeper' / 'keybinds.json'
        kb.save(path, kb.KeyMap(overrides))
        mode = stat.S_IMODE(path.stat().st_mode)
        out.append({'overrides': overrides, 'text': path.read_text(), 'mode': mode,
                    'leftover_tmp': path.with_name(path.name + '.tmp').exists()})
    return out


class _SyncThread:
    """keybinds.threading.Thread for the traces: the work runs at start(), so each step's compositor calls are fixed."""
    def __init__(self, target, args=(), daemon=None):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)

    def is_alive(self):
        return False

    def join(self, timeout=None):
        pass


class _Host:
    """The ViewControl seam BindControl uses (Rust: supervisor::keybinds::Host)."""
    def __init__(self):
        self.state = type('State', (), {'typing': False, 'menu_open': False})()
        self.options = type('Options', (), {'keyboard_owned': False})()
        self.video_active = self.help_visible = False
        self.run_id, self.session, self.next_id = 'run', 'session', 1
        self.now = 1000.0
        self.last_input = None
        self._menu_lock = threading.RLock()
        self.log = []
        self.menu_binds = lambda on: self.log.append(['menu_binds', on])
        self.options_binds = lambda on: self.log.append(['options_binds', on])

    def clock(self):
        return self.now

    def _help_toggle(self):
        self.help_visible = False
        self.log.append(['help_closed'])
        return b'<help closed>\n'

    def _on_menu(self, op):
        assert op == 'menu_toggle'
        self.state.menu_open = False
        self.log.append(['menu_closed'])
        return b'<menu closed>\n'


class _Client:
    def __init__(self, text='', fail=()):
        self.text, self.fail, self.calls = text, set(fail), []

    def _maybe(self, what):
        if what in self.fail: raise OSError(f'{what} failed')

    def reinstall_view_binds(self, *args, typing=False, binds=None, send_binds=None):
        self.calls.append(['reinstall', typing, [list(b) for b in binds] if binds is not None else None,
                           [list(b) for b in send_binds] if send_binds is not None else None])
        self._maybe('reinstall')

    def open_key_panel(self, *args):
        self.calls.append(['open'])
        self._maybe('open')

    def close_key_panel(self):
        self.calls.append(['close'])
        self._maybe('close')

    def binds_text(self):
        self.calls.append(['read'])
        self._maybe('read')
        return self.text


def _snapshot(k, host):
    kwargs = k.bind_kwargs()
    return {'open': k.open, 'capturing': k.capturing, 'selected': k.selected, 'status': k.status, 'alert': k.alert,
            'overrides': dict(sorted(k.keymap.overrides.items())), 'off': sorted(k.off), 'conflicts': k.conflicts,
            'version': k.version, 'installed_version': k.installed_version, 'panel_installed': k._panel_installed,
            'desktop_known': k.desktop is not None, 'reading': k._reading, 'last_input': k.last_input,
            'host_last_input': host.last_input,
            'bind_kwargs': None if not kwargs else [[list(b) for b in kwargs['binds']], [list(b) for b in kwargs['send_binds']]],
            'installed_chords': k.installed_chords()}


def _trace(name, steps, overrides=None, desktop=None, client=None, path='file', notify=True):
    """One scripted panel session. `client`: None (no compositor) or {'text', 'fail'}. `path`: 'file', 'blocked' (its
    directory is a regular file: saving fails), or None (not saved)."""
    with tempfile.TemporaryDirectory() as tmp, mock.patch.object(kb.threading, 'Thread', _SyncThread):
        tmp = Path(tmp)
        if path == 'blocked': (tmp / 'blocked').write_text('')
        file = tmp / 'cfg' / 'keybinds.json' if path == 'file' else tmp / 'blocked' / 'keybinds.json' if path == 'blocked' else None
        host = _Host()
        fake = _Client(**client) if client is not None else None
        toasts = []
        k = kb.BindControl(host, kb.KeyMap(overrides or {}), file, client=fake, bind_args=('ARGS',),
                           desktop=kb.DesktopBinds.from_text(desktop) if desktop is not None else None,
                           notify=toasts.append if notify else None)
        k.installed()
        out_steps, saved = [], None
        for step in steps:
            calls0, toasts0, log0 = len(fake.calls) if fake else 0, len(toasts), len(host.log)
            kind, out = step[0], None
            if kind == 'msg': out = k.on_message(step[1])
            elif kind == 'key': out = k.on_message({'op': kb.WIRE_OP, 'payload': {'mods': step[1], 'code': step[2]}})
            elif kind == 'toggle': out = k.on_message({'op': kb.PANEL_OP, 'payload': {}})
            elif kind == 'poll': out = k.poll(host.now)
            elif kind == 'advance': host.now += step[1]
            elif kind == 'host': setattr(host.state if step[1] in ('typing', 'menu_open') else host, step[1], step[2])
            elif kind == 'client': setattr(fake, step[1], step[2] if step[1] != 'fail' else set(step[2]))
            elif kind == 'startup_toast': out = k.startup_toast()
            elif kind == 'after_reload': k.after_reload()
            elif kind == 'installed': k.installed()
            elif kind == 'stop': k.stop()
            else: raise AssertionError(kind)
            if isinstance(out, bytes): out = out.decode()
            now_saved = file.read_text() if file is not None and file.exists() else None
            out_steps.append({'step': list(step), 'out': out, 'calls': fake.calls[calls0:] if fake else [],
                              'toasts': toasts[toasts0:], 'host': host.log[log0:],
                              'saved': now_saved if now_saved != saved else None, 'state': _snapshot(k, host)})
            saved = now_saved
        return {'name': name, 'overrides': overrides or {}, 'desktop': desktop, 'client': client, 'path': path,
                'notify': notify, 'steps': out_steps}


def _traces():
    cl = {'text': ''}
    t = []
    t.append(_trace('open_browse_close', [
        ('toggle',), ('key', 0, DOWN), ('key', 0, UP), ('key', 0, UP), ('key', 0, PGDN), ('key', 0, PGDN),
        ('key', 0, PGDN), ('key', 0, PGDN), ('key', 0, PGUP), ('key', 0, END), ('key', 0, HOME), ('key', CTRL | ALT, 113),
        ('key', SHIFT, DOWN), ('key', 0, 200), ('key', 0, 66), ('toggle',), ('toggle',), ('key', CTRL | ALT, K),
        ('key', 0, ESC), ('key', 0, ESC)], client=cl))
    t.append(_trace('rebind_live_and_save', [
        ('toggle',), ('key', 0, DOWN), ('key', 0, DOWN), ('key', 0, DOWN), ('key', 0, ENTER), ('key', CTRL | ALT, J),
        ('poll',), ('key', 0, ENTER), ('key', CTRL | ALT, J), ('key', 0, KP_ENTER), ('key', CTRL | ALT | SHIFT, 114),
        ('key', 0, DOWN), ('key', 0, ENTER), ('key', SUPER | 2 | 16, 10), ('key', 0, ESC)], client=cl))
    t.append(_trace('escape_cancels_then_closes', [
        ('toggle',), ('key', 0, ENTER), ('key', SHIFT, ESC), ('key', 0, ESC), ('key', 0, ESC), ('key', 0, ESC)], client=cl))
    t.append(_trace('own_toggle_keys_close', [
        ('toggle',), ('key', CTRL | ALT, K), ('key', CTRL | SUPER, J), ('toggle',), ('key', CTRL | SUPER | 2, J)],
        overrides={kb.PANEL_OP: 'CTRL + SUPER + J'}, client=cl))
    t.append(_trace('unbindable_keys_keep_waiting', [
        ('toggle',), ('key', 0, ENTER), ('key', 0, J), ('key', SHIFT, J), ('key', CTRL, 191), ('key', CTRL, 37),
        ('key', 2, J), ('key', 'x', J), ('key', CTRL, '44'), ('key', CTRL | ALT, ESC), ('key', 0, ESC)], client=cl))
    t.append(_trace('refused_by_another_xr_bind', [
        ('toggle',), ('key', 0, ENTER), ('key', CTRL | ALT, L), ('key', CTRL | ALT, 116), ('key', CTRL | ALT, J),
        ('key', 0, DOWN), ('key', 0, ENTER), ('key', CTRL | ALT, 116)], client=cl))
    t.append(_trace('refused_by_hyprland', [
        ('client', 'text', HYPRCTL), ('toggle',), ('poll',), ('key', 0, ENTER), ('key', CTRL | ALT, DELETE),
        ('key', SUPER, 10), ('key', CTRL | SHIFT, 39), ('key', CTRL | ALT, 23)], desktop=DESKTOP, client=cl))
    t.append(_trace('saved_conflict_off_and_reset', [
        ('startup_toast',), ('client', 'text', HYPRCTL), ('toggle',), ('poll',), ('key', 0, DOWN), ('key', 0, DOWN),
        ('key', 0, DOWN), ('key', 0, R), ('key', 0, R), ('poll',)],
        overrides={'next': 'CTRL + ALT + Delete'}, desktop=DESKTOP, client=cl))
    t.append(_trace('startup_toast_variants', [('startup_toast',)],
                    overrides={'next': 'CTRL + ALT + Delete', kb.PANEL_OP: 'CTRL + ALT + Tab', 'help': 'CTRL + ALT + L'},
                    desktop=DESKTOP, client=cl))
    t.append(_trace('startup_toast_more', [('startup_toast',), ('toggle',), ('poll',)],
                    overrides={'next': 'CTRL + ALT + J', 'prev': 'CTRL + ALT + J', 'help': 'CTRL + SHIFT + S'},
                    desktop=DESKTOP, client=cl))
    t.append(_trace('no_conflicts_no_toast', [('startup_toast',), ('toggle',)], desktop=DESKTOP, client=cl))
    t.append(_trace('reset_one_and_all', [
        ('toggle',), ('key', 0, R), ('key', 0, R), ('key', SHIFT, R), ('advance', 1.0), ('key', SHIFT, R),
        ('key', SHIFT, R), ('advance', kb.RESET_ARM_S + 0.1), ('poll',), ('key', SHIFT, R), ('key', 0, DOWN),
        ('key', SHIFT, R), ('advance', kb.RESET_ARM_S), ('key', SHIFT, R), ('key', SHIFT, R), ('key', SHIFT, R)],
        overrides={'zoom_in': 'CTRL + ALT + J', 'next': 'CTRL + SUPER + J'}, client=cl))
    t.append(_trace('capture_timeout_and_idle_close', [
        ('toggle',), ('key', 0, ENTER), ('advance', kb.CAPTURE_TIMEOUT_S - 0.5), ('poll',), ('advance', 0.6), ('poll',),
        ('advance', view.OVERLAY_IDLE_CLOSE_S - 1), ('poll',), ('advance', 1.0), ('poll',), ('poll',)], client=cl))
    t.append(_trace('arm_expires_in_poll', [
        ('toggle',), ('key', SHIFT, R), ('advance', 2.0), ('poll',), ('advance', 2.0), ('poll',), ('poll',)],
        overrides={'next': 'CTRL + ALT + J'}, client=cl))
    t.append(_trace('open_fails_closes_and_says_so', [
        ('toggle',), ('poll',), ('poll',), ('client', 'fail', []), ('toggle',), ('poll',)],
        client={'text': '', 'fail': ['open']}))
    t.append(_trace('read_fails_keeps_desktop', [
        ('toggle',), ('poll',), ('toggle',), ('client', 'fail', []), ('client', 'text', HYPRCTL), ('toggle',), ('poll',)],
        desktop=DESKTOP, client={'text': '', 'fail': ['read']}))
    t.append(_trace('reinstall_and_close_fail', [
        ('toggle',), ('key', 0, ENTER), ('key', CTRL | ALT, J), ('poll',), ('key', 0, ESC), ('client', 'fail', []),
        ('key', 0, ENTER), ('poll',), ('toggle',), ('key', 0, ESC)],
        client={'text': '', 'fail': ['reinstall', 'close']}))
    t.append(_trace('reading_updates_conflicts', [
        ('client', 'text', HYPRCTL + OURS_ON_DELETE), ('toggle',), ('poll',), ('poll',), ('toggle',),
        ('client', 'text', ''), ('toggle',), ('poll',)],
        overrides={'next': 'CTRL + ALT + Delete'}, client=cl))
    t.append(_trace('typing_video_help_menu', [
        ('host', 'typing', True), ('toggle',), ('host', 'typing', False), ('host', 'help_visible', True), ('toggle',),
        ('toggle',), ('host', 'menu_open', True), ('toggle',), ('toggle',), ('host', 'video_active', True), ('toggle',),
        ('host', 'video_active', False), ('host', 'help_visible', True), ('host', 'menu_open', True), ('toggle',)],
        client=cl))
    t.append(_trace('typing_installed_chords', [
        ('host', 'typing', True), ('installed',), ('toggle',), ('host', 'typing', False), ('toggle',), ('key', 0, ENTER),
        ('host', 'typing', True), ('key', CTRL | ALT, J), ('host', 'typing', False), ('poll',)], client=cl))
    t.append(_trace('reinstall_restores_menu_binds', [
        ('toggle',), ('key', 0, ENTER), ('host', 'menu_open', True), ('key', CTRL | ALT, J), ('host', 'menu_open', False),
        ('key', 0, ENTER), ('key', CTRL | ALT, 58)], client=cl))
    t.append(_trace('after_reload', [
        ('after_reload',), ('toggle',), ('poll',), ('after_reload',), ('poll',), ('toggle',), ('after_reload',)],
        client=cl))
    t.append(_trace('no_compositor_no_toasts', [
        ('toggle',), ('key', 0, ENTER), ('key', CTRL | ALT, J), ('key', 0, ENTER), ('key', CTRL | ALT, L),
        ('startup_toast',), ('poll',), ('key', 0, ESC)], client=None, notify=False))
    t.append(_trace('not_saved', [('toggle',), ('key', 0, ENTER), ('key', CTRL | ALT, J), ('key', 0, R)],
                    client=cl, path='blocked'))
    t.append(_trace('no_path', [('toggle',), ('key', 0, ENTER), ('key', CTRL | ALT, J)], client=cl, path=None))
    t.append(_trace('stale_keys_close_the_compositor_panel', [
        ('key', 0, ENTER), ('client', 'fail', ['close']), ('toggle',), ('toggle',), ('key', 0, ENTER),
        ('client', 'fail', []), ('key', 0, ENTER), ('key', 0, ENTER)], client=cl))
    t.append(_trace('messages_ignored', [
        ('msg', {'op': 'other', 'payload': {}}), ('msg', {'op': kb.WIRE_OP}), ('msg', {'op': kb.WIRE_OP, 'payload': None}),
        ('msg', {'op': kb.WIRE_OP, 'payload': {'mods': True, 'code': ENTER}}), ('msg', {}),
        ('toggle',), ('msg', {'op': kb.WIRE_OP, 'payload': {'mods': 0, 'code': 36.0}}),
        ('msg', {'op': kb.WIRE_OP, 'payload': {'mods': 0, 'code': ENTER, 'extra': 1}})], client=cl))
    t.append(_trace('change_with_hyprland_conflict_toast', [
        ('client', 'text', HYPRCTL), ('toggle',), ('poll',), ('key', 0, ENTER), ('key', CTRL | SHIFT, 39),
        ('key', 0, DOWN), ('key', 0, ENTER), ('key', CTRL | ALT, J)], desktop=DESKTOP, client=cl))
    t.append(_trace('change_while_conflicts', [('toggle',), ('key', 0, ENTER), ('key', CTRL | ALT, J)],
                    overrides={'help': 'CTRL + SHIFT + S'}, desktop=DESKTOP, client=cl))
    t.append(_trace('stop_stops_compositor_work', [
        ('toggle',), ('stop',), ('key', 0, ENTER), ('key', CTRL | ALT, J), ('toggle',), ('toggle',)], client=cl))
    return t


def keybinds_corpus():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        load, save = _load_cases(tmp), _save_cases(tmp)
    codes = sorted(set(kb.KEYCODES) | {0, 7, 8, 37, 50, 64, 66, 104, 133, 191, 255})
    labels = [c for _, c in kb.CATALOG] + list(EXTRA_LABELS)
    return {
        'comment': 'tools/native_spike/keybinds.py, made by tests/native_spike/keybinds_corpus.py: the Rust port '
                   '(rust/crates/xr-workspace/src/supervisor/keybinds.rs) must give the same results.',
        'tables': {'catalog': [[op, c, kb.label(op)] for op, c in kb.CATALOG], 'run_ops': list(kb.RUN_OPS),
                   'send_ops': list(kb.SEND_OPS), 'keycodes': {str(k): v for k, v in kb.KEYCODES.items()},
                   'key_names': view.KEY_NAMES, 'panel_op': kb.PANEL_OP, 'wire_op': kb.WIRE_OP, 'typing_op': view.TYPING_OP,
                   'hint': kb.HINT, 'capture_hint': kb.CAPTURE_HINT, 'idle_close_s': view.OVERLAY_IDLE_CLOSE_S,
                   'capture_timeout_s': kb.CAPTURE_TIMEOUT_S, 'reset_arm_s': kb.RESET_ARM_S, 'page': kb.PAGE},
        'parse_chord': [{'text': t, **_error(kb.parse_chord, t)} for t in CHORD_TEXTS],
        'key_chord': [[m, c, kb.key_chord(m, c)] for c in codes for m in KEY_CHORD_MASKS],
        'chord_label': [[c, kb.chord_label(c)] for c in labels],
        'ascii': [[t, n, kb._ascii(t, n)] for t in ASCII_TEXTS for n in (48, 96, 60)],
        'keymap': [{'overrides': o, 'effective': kb.KeyMap(o).overrides, 'items': kb.KeyMap(o).items()} for o in OVERRIDES],
        'load': load, 'save': save, 'binds': _binds_cases(), 'conflicts': _conflict_cases(),
        'frame': [{'run_id': 'run', 'session': 's', 'request_id': 7, 'payload': {'visible': False},
                   'frame': kb.binds_frame('run', 's', 7, {'visible': False}).decode()}],
        'traces': _traces(),
    }


# --- bind_check.json ------------------------------------------------------------------------------------------------
NORMALIZE_TEXTS = ('SUPER + SHIFT + R', 'shift + win + r', 'CTRL + ALT + Prior', 'CTRL + ALT + next', 'SUPER + 1',
                   'SUPER + code:10', 'SUPER + 0', 'mouse:272', 'SUPER + mouse:272', 'Escape', 'ctrl+alt+k',
                   'CTRL + + K', ' + K', 'HYPER + K', 'CTRL + ', '', '+', 'CTRL + ALT + PAGE_UP', 'MOD4 + MOD1 + K',
                   'CONTROL + LOGO + META + x', 'CTRL + ALT + É')


def _records(lines):
    return [tuple(line.split('\t', 2)) for line in lines if line.count('\t') >= 2]


RECORD_SETS = {
    'empty': [],
    'omarchy': ['desktop\tbind\tSUPER + RETURN', 'desktop\tbind\tCTRL + ALT + DELETE', 'desktop\tbind\tSUPER + code:10',
                'desktop\tbind\tSUPER + SHIFT + code:10', 'desktop\tbind\tSUPER + mouse:272', 'desktop\tunbind\tSUPER + RETURN',
                'desktop\tbind\tSUPER + RETURN', 'desktop\tbind\tsuper + return', 'desktop\tbind\t',
                'desktop\tother\tCTRL + ALT + K', 'run\tbind\tCTRL + ALT + up', 'desktop\tloaded\tok'],
    'clash': ['desktop\tbind\tCTRL + ALT + K', 'desktop\tbind\tCTRL + ALT + 1', 'desktop\tbind\tCTRL + ALT + up',
              'desktop\tunbind\tCTRL + ALT + up', 'desktop\tbind\tmouse:273', 'desktop\tbind\tSHIFT + Tab',
              'desktop\tbind\tCTRL + ALT + prior'],
}


def _check_cases(tmp):
    """check() against a stand-in Hyprland: a script that writes the harness's records file itself."""
    home = tmp / 'home'
    (home / '.config' / 'hypr').mkdir(parents=True)
    config = home / '.config' / 'hypr' / 'hyprland.lua'
    config.write_text('-- stand-in\n')
    state = home / '.local' / 'state' / 'omarchy'
    (state / 'current').mkdir(parents=True)
    (state / 'current' / 'theme').write_text('x')
    (state / 'toggles').write_text('file, not a directory')
    fake = tmp / 'Hyprland'
    out = []
    for name, (records, stdout, code) in {
        'ok': (['desktop\tbind\tSUPER + RETURN', 'desktop\tloaded\tok', 'run\tbind\tCTRL + ALT + up', 'run\tloaded\tok'],
               'Config parsing result: ok\nmore', 0),
        'clash': (['desktop\tbind\tCTRL + ALT + K', 'desktop\tloaded\tok', 'run\tloaded\tok'], 'Config parsing result: fine', 0),
        'config_errors': (['desktop\tloaded\tok', 'run\tloaded\tok'], 'noise\nConfig parsing result: 2 errors\nx', 3),
        'desktop_failed': (['desktop\tloaded\tboom: line 3'], '', 1),
        'run_failed': (['desktop\tloaded\tok', 'run\tloaded\tno key submap'], '', 0),
        'no_run_result': (['desktop\tloaded\tok'], '', 0),
        'no_records': (None, 'Config parsing result: crashed', 1),
        'no_records_stderr': (None, '', 1),
    }.items():
        lines = '\n'.join(records) + '\n' if records is not None else None
        # The harness's first line names the records file: local out = assert(io.open("PATH", "w"))
        write = ('out=$(sed -n \'1s/^local out = assert(io.open("\\(.*\\)", "w"))$/\\1/p\' "$3")\n'
                 f'printf %s {_sh(lines)} > "$out"\n') if lines is not None else ''
        fake.write_text('#!/bin/sh\n' + write + f'printf %s {_sh(stdout)}\necho "stderr says no" >&2\nexit {code}\n')
        fake.chmod(0o755)
        for keybinds_text in (None, '{"schema": 1, "binds": {"next": "CTRL + ALT + J", "send_back": "SUPER + SHIFT + B"}}'):
            kfile = tmp / 'keybinds.json'
            if keybinds_text is None: kfile.unlink(missing_ok=True)
            else: kfile.write_text(keybinds_text)
            status, report = bind_check.check(config, str(fake), home, keybinds_file=kfile)
            out.append({'name': name, 'keybinds': keybinds_text, 'status': status, 'report': report})
    status, report = bind_check.check(home / 'missing.lua', str(fake), home, keybinds_file=tmp / 'none.json')
    # The report names the config path: the temporary directory is written as <tmp>.
    out.append({'name': 'no_config', 'config': 'missing.lua', 'keybinds': None, 'status': status,
                'report': [line.replace(str(tmp), '<tmp>') for line in report]})
    return out


def _sh(text):
    return "'" + text.replace("'", "'\\''") + "'"


def harness_chunks(keymap):
    """bind_check.harness_lua's chunks for `keymap` (the generated Lua is the Lua generators' port, so the Rust bind
    check takes these as input). bind_check_corpus() checks they are the ones harness_lua joins."""
    binds = tuple((keymap.chord(op), op) for op in kb.RUN_OPS)
    sends = tuple((keymap.chord(op), op) for op in kb.SEND_OPS)
    a = bind_check.DUMMY_ARGS
    return [view.install_binds_lua(*a, typing=True, binds=binds), view.remove_binds_lua(),
            view.install_binds_lua(*a, binds=binds), view.install_send_binds_lua(*a, binds=sends),
            kb.panel_on_lua(*a), kb.PANEL_OFF_LUA,
            view.install_menu_binds_lua(*a), resize.install_resize_binds_lua(*a), input_lock.lock_binds_lua(*a),
            resize.remove_resize_binds_lua(),
            unified_cursor.click_binds_lua(*a) + f' assert(#{resize.BIND_TABLE} == 2, "the unified cursor\'s click binds were refused")',
            resize.remove_resize_binds_lua(),
            view.install_drag_binds_lua(*a) +
            f' assert(#{view.DRAG_TABLE} == {len(view.SUPER_BINDS) + len(view.SUPER_BUTTONS)}, "the Super drag binds were refused")',
            view.remove_binds_lua(), *window_memory.verify_lua()]


def _normalized(text):
    r = _error(bind_check.normalize, text)
    return {'value': [sorted(r['value'][0]), r['value'][1]]} if 'value' in r else r


def bind_check_corpus():
    keymaps = [kb.KeyMap(o) for o in ({}, {'next': 'CTRL + ALT + J', 'send_back': 'SUPER + SHIFT + B'},
                                     {'next': 'CTRL + ALT + K', 'help': 'SHIFT + SUPER + 1'})]
    harness = []
    for km in keymaps:
        chunks = harness_chunks(km)
        lua = bind_check.harness_lua('/tmp/h/.config/hypr/hyprland.lua', '/tmp/h/binds "x".tsv', km)
        assert '\n'.join(f'do {c} end' for c in chunks) in lua, 'harness_chunks no longer matches harness_lua'
        harness.append({'overrides': km.overrides, 'config': '/tmp/h/.config/hypr/hyprland.lua',
                        'out': '/tmp/h/binds "x".tsv', 'chunks': chunks, 'lua': lua})
    with tempfile.TemporaryDirectory() as tmp:
        check = _check_cases(Path(tmp))
    return {
        'comment': 'tools/native_spike/bind_check.py, made by tests/native_spike/keybinds_corpus.py: the Rust port '
                   '(rust/crates/xr-workspace/src/supervisor/bind_check.rs) must give the same results.',
        'normalize': [{'text': t, **_normalized(t)} for t in NORMALIZE_TEXTS],
        'run_chords': [{'overrides': km.overrides, 'chords': [list(c) for c in bind_check.run_chords(km)]} for km in keymaps],
        'records': [{'name': n, 'lines': lines,
                     'desktop_chords': sorted([sorted(m), k, v] for (m, k), v in bind_check.desktop_chords(_records(lines)).items()),
                     'clashes': [{'overrides': km.overrides, 'problems': bind_check.clashes(_records(lines), km)} for km in keymaps]}
                    for n, lines in RECORD_SETS.items()],
        'harness': harness, 'check': check,
        'state_dirs': list(bind_check.STATE_DIRS), 'dummy_args': list(bind_check.DUMMY_ARGS),
    }


# --- options_text.json ----------------------------------------------------------------------------------------------
LAYOUTS = (
    {'rules': 'evdev', 'model': 'pc105', 'layout': 'us', 'variant': '', 'options': ''},
    {'layout': 'us', 'variant': 'intl'},
    {'layout': 'de', 'variant': 'nodeadkeys', 'options': ''},
    {'layout': 'fr'},
    {'layout': 'us,ru', 'options': 'grp:alt_shift_toggle,caps:escape'},
    {'layout': 'gb', 'options': 'compose:ralt'},
)
# (what, args): 'hypr' modifiers(mask) through hypr_modifiers, 'mods' raw masks, 'key' a keycode.
SCRIPT = (('key', 38), ('key', 24), ('key', 10), ('key', 20), ('key', 34), ('key', 47), ('key', 48), ('key', 49),
          ('key', 51), ('key', 61), ('key', 65), ('key', 23), ('key', 22), ('key', 36), ('key', 104), ('key', 9),
          ('key', 111), ('key', 50), ('key', 119), ('key', 94), ('key', 192),
          ('hypr', 1), ('key', 38), ('key', 10), ('key', 48), ('key', 49), ('key', 94),
          ('hypr', 2), ('key', 38), ('key', 10), ('hypr', 4), ('key', 38), ('key', 36), ('hypr', 0),
          ('mods', [0, 0, 0, 1]), ('key', 38), ('key', 39), ('mods', [0, 0, 0, 0]),
          ('mods', [0, 0, 2, 0]), ('key', 38), ('mods', [0, 0, 0, 0]),
          # Dead and compose sequences (us intl: ' then e is é; ' then space is '; ' then ' is ´).
          ('key', 48), ('key', 26), ('key', 48), ('key', 65), ('key', 48), ('key', 48), ('key', 48), ('key', 9),
          ('hypr', 1), ('key', 49), ('hypr', 0), ('key', 38), ('key', 48), ('key', 61), ('key', 26),
          ('mods', [0x80, 0, 0, 0]), ('key', 26), ('key', 108), ('key', 48), ('key', 26), ('mods', [0, 0, 0, 0]),
          ('key', 34), ('key', 26), ('key', 35), ('key', 38), ('key', 21), ('key', 54))
BAD_NAMES = (None, [], {}, {'layout': ''}, {'layout': 'us', 'variant': 3}, {'layout': 'us', 'options': 'x' * 257},
             {'layout': 'us', 'options': 'é' * 129}, {'layout': 'zz-not-a-layout'}, {'layout': 'us', 'variant': 'nope'})


def _run_script(t, script):
    out = []
    for what, arg in script:
        if what == 'hypr': t.hypr_modifiers(arg); out.append(None)
        elif what == 'mods': t.modifiers(*arg); out.append(None)
        else: out.append(list(t.key(arg)))
    return out


def options_text_corpus():
    layouts = []
    for names in LAYOUTS:
        t = options_text.TextInput.from_names(names)
        try: layouts.append({'names': names, 'results': _run_script(t, SCRIPT)})
        finally: t.close()
    refused = []
    for names in BAD_NAMES:
        try:
            options_text.TextInput.from_names(names).close()
            refused.append({'names': names, 'error': None})
        except ValueError as exc: refused.append({'names': names, 'error': str(exc)})
    t = options_text.TextInput.from_names(LAYOUTS[0])
    bad = []
    try:
        for code in (7, 65536, -1, 8, 65535, True, 38.0):
            bad.append(['key', code, _error(lambda c: list(t.key(c)), code)])
        for mods in ([-1, 0, 0, 0], [0, 0, 0, 0x100000000], [0xffffffff, 0, 0, 0], [0, True, 0, 0], [0, 0, 0.0, 0]):
            bad.append(['mods', mods, _error(lambda m: t.modifiers(*m), mods)])
        bad.append(['key', 38, _error(lambda c: list(t.key(c)), 38)])
    finally: t.close()
    no_compose = options_text.TextInput.from_names(LAYOUTS[1])
    no_compose.close()
    try: options_text.TextInput('xkb_keymap { nonsense }').close(); keymap_error = None
    except ValueError as exc: keymap_error = str(exc)
    try: options_text.TextInput('x' * (1024 * 1024 + 1)); big_error = None
    except ValueError as exc: big_error = str(exc)
    return {
        'comment': 'tools/native_spike/options_text.py through the system libxkbcommon, made by '
                   'tests/native_spike/keybinds_corpus.py: the Rust port (rust/crates/xr-workspace/src/supervisor/'
                   'options_text.rs) must give the same results. Each result is [action, text], or null for a modifier step.',
        'script': [list(s) for s in SCRIPT], 'layouts': layouts, 'refused_names': refused, 'refused_input': bad,
        'bad_keymap': keymap_error, 'big_keymap': big_error,
    }


# --- environment.json -----------------------------------------------------------------------------------------------
ENV_VALUES = (
    None, 3, 'grid', [], {}, {'background': 'grid'}, {'background': 'smoke', 'last': 'grid', 'brightness': 150},
    {'background': 'none', 'last': 'smoke', 'brightness': 100}, {'background': 'none', 'last': 'none', 'brightness': 100},
    {'background': 'fog', 'last': 'grid', 'brightness': 100}, {'background': 'grid', 'last': 'grid', 'brightness': 9},
    {'background': 'grid', 'last': 'grid', 'brightness': 201}, {'background': 'grid', 'last': 'grid', 'brightness': 200},
    {'background': 'grid', 'last': 'grid', 'brightness': 10}, {'background': 'grid', 'last': 'grid', 'brightness': 100.0},
    {'background': 'grid', 'last': 'grid', 'brightness': True}, {'background': 'grid', 'last': 'smoke', 'brightness': 50},
    {'background': 'none', 'last': 'grid', 'brightness': 100, 'extra': 1}, {'background': 'GRID', 'last': 7, 'brightness': '100'},
    {'background': None, 'last': 'smoke'}, {'last': 'smoke', 'brightness': 42},
)
SLIDES = (0, 1, 0.5, -1, 2, 0.25, 0.0025, 0.002777, 0.0027777778, 0.997, 0.5 / 190, 1.5 / 190, 2.5 / 190, 0.999999)


def environment_corpus():
    cases = []
    for v in ENV_VALUES:
        c = environment.clean(v)
        cases.append({
            'value': v, 'clean': c, 'is_valid': environment.is_valid(v), 'validate': _error(environment.validate, v),
            'environment_dict': environment.environment({'environment': v}), 'on': environment.on(v),
            'toggled': environment.toggled(v), 'stepped': [environment.stepped(v, d) for d in (-4, -1, 0, 1, 2, 7)],
            'picked': [_error(environment.picked, v, n) for n in ('none', 'grid', 'smoke', 'fog', None)],
            'brightened': [environment.brightened(v, b) for b in (-5, 9, 10, 77, 200, 201, 10 ** 6)],
            'slid': [environment.slid(v, t) for t in SLIDES], 'label': environment.label(v), 'menu_row': environment.menu_row(v),
        })
    frames = []
    for env in (environment.clean(None), {'background': 'smoke', 'last': 'smoke', 'brightness': 10},
                {'background': 'none', 'last': 'grid', 'brightness': 200}, {'background': 'grid'}):
        r = _error(lambda e: environment.renderer_frame('run-1', 'sess', 12, e).decode(), env)
        frames.append({'run_id': 'run-1', 'session': 'sess', 'request_id': 12, 'env': env, **r})
    bad_slides = [[t, _error(environment.slid, None, t)] for t in (float('nan'), float('inf'), '0.5', None, True)]
    return {
        'comment': 'tools/native_spike/environment.py, made by tests/native_spike/keybinds_corpus.py: the Rust port '
                   '(rust/crates/xr-workspace/src/supervisor/environment.rs) must give the same results. Non-JSON '
                   'slider inputs (NaN, infinity) are written as strings in bad_slides.',
        'backgrounds': [list(b) for b in environment.BACKGROUNDS], 'defaults': environment.DEFAULTS,
        'bounds': list(environment.BRIGHTNESS_BOUNDS), 'steps': list(environment.BRIGHTNESS_STEPS),
        'ops': [environment.TOGGLE_OP, environment.SET_OP], 'toggle_bind': list(environment.TOGGLE_BIND),
        'menu_label': environment.MENU_LABEL, 'slides': list(SLIDES), 'cases': cases, 'frames': frames,
        'bad_slides': [[repr(t) if isinstance(t, float) else t, r] for t, r in bad_slides],
    }


# --- files ----------------------------------------------------------------------------------------------------------
def build():
    return {'keybinds.json': keybinds_corpus(), 'bind_check.json': bind_check_corpus(),
            'options_text.json': options_text_corpus(), 'environment.json': environment_corpus()}


def render(data):
    return json.dumps(data, indent=1, ensure_ascii=False) + '\n'


def main(argv):
    built = build()
    if '--write' in argv:
        CORPUS.mkdir(parents=True, exist_ok=True)
        for name, data in built.items(): (CORPUS / name).write_text(render(data))
        return 0
    stale = [name for name, data in built.items()
             if not (CORPUS / name).exists() or (CORPUS / name).read_text() != render(data)]
    for name in stale: print(f'stale: rust/corpus/supervisor/{name}')
    return 1 if stale else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
