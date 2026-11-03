"""Key bind panel (Ctrl+Alt+K, tools/native_spike/keybinds.py): the catalog, chords, the saved file, conflicts with each
other and with `hyprctl binds`, the panel's keys, live rebinding through the compositor client, the help card, the bind
helper and the compositor Lua."""
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import unittest
from unittest import mock

from tools.native_spike import bind_check, hyprland, keybinds as kb, send, video, view, xr_control
from tools.native_spike.ipc import decode_control
from tools.native_spike.profiles import LayoutStore

ROOT = Path(__file__).resolve().parents[2]
NAMES = ['XR-1', 'XR-2', 'XR-3']
ARGS = ('/usr/bin/python3', '/opt/x/xr_control.py', '/run/user/1000/x/control.sock', 'run', 'session')
# Keycodes (xkb = evdev + 8) and modmasks (Hyprland HL_MODIFIER_*).
UP, DOWN, ENTER, ESC, R, K, J, PGDN, END, HOME = 111, 116, 36, 9, 27, 45, 44, 117, 115, 110
SHIFT, CTRL, ALT, SUPER = 1, 4, 8, 64
# Plain `hyprctl binds` as Hyprland 0.56.2 prints it (trimmed): Omarchy's described binds, a keycode bind, a mouse bind,
# a user's undescribed bind, a submap's catch-all, and one of this run's own (undescribed) binds.
HYPRCTL = '''bindd
\tmodmask: 12
\tsubmap:
\tkey: DELETE
\tkeycode: 0
\tcatchall: false
\tdescription: Close all windows
\tdispatcher: __lua
\targ: 83

bindd
\tmodmask: 12
\tsubmap:
\tkey: TAB
\tkeycode: 0
\tcatchall: false
\tdescription: Focus on next monitor
\tdispatcher: __lua
\targ: 185

bindd
\tmodmask: 64
\tsubmap:
\tkey: SUPER + code:10
\tkeycode: 0
\tcatchall: false
\tdescription: Switch to workspace 1
\tdispatcher: __lua
\targ: 201

bindm
\tmodmask: 64
\tsubmap:
\tkey: mouse:272
\tkeycode: 0
\tcatchall: false
\tdescription: Move window
\tdispatcher: __lua
\targ: 300

bind
\tmodmask: 5
\tsubmap:
\tkey: S
\tkeycode: 0
\tcatchall: false
\tdescription:
\tdispatcher: __lua
\targ: 310

bind
\tmodmask: 0
\tsubmap: resize
\tkey:
\tkeycode: 0
\tcatchall: true
\tdescription:
\tdispatcher: __lua
\targ: 311

bind
\tmodmask: 12
\tsubmap:
\tkey: V
\tkeycode: 0
\tcatchall: false
\tdescription:
\tdispatcher: __lua
\targ: 374

'''
# What a run reads before it installs its own binds (managed.view_controls): the same, without the run's Ctrl+Alt+V.
DESKTOP = HYPRCTL[:HYPRCTL.rindex('bind\n\tmodmask: 12')]
# A bind the run installed on keys Omarchy also binds, as `hyprctl binds` then lists it: twice.
OURS_ON_DELETE = 'bind\n\tmodmask: 12\n\tsubmap: \n\tkey: Delete\n\tkeycode: 0\n\tcatchall: false\n\tdescription: \n\tdispatcher: __lua\n\targ: 400\n\n'


def frames(data):
    return [json.loads(line) for line in (data or b'').decode().splitlines()]


def binds_payload(data):
    found = [f['payload'] for f in frames(data) if f['op'] == 'binds']
    return found[-1] if found else None


class Clock:
    def __init__(self): self.now = 100.0
    def __call__(self): return self.now


class FakeClient:
    """The compositor calls BindControl makes (hyprland.HyprlandClient's), recorded."""
    def __init__(self, text=''):
        self.calls, self.text = [], text
        self.fail_open = False
    def reinstall_view_binds(self, *args, **kw): self.calls.append(('reinstall', args, kw))
    def open_key_panel(self, *args):
        self.calls.append(('open', args))
        if self.fail_open: raise ValueError('compositor rejected bind change: no key subscription')
    def close_key_panel(self): self.calls.append(('close',))
    def binds_text(self, timeout=2.0): self.calls.append(('read',)); return self.text


class ChordTests(unittest.TestCase):
    def test_the_toggle_is_ctrl_alt_k_a_run_bind_that_clashes_with_no_other_project_bind(self):
        self.assertEqual(kb.PANEL_BIND, ('CTRL + ALT + K', 'binds_toggle'))
        self.assertIn(kb.PANEL_BIND, view.BINDS)
        others = [c for c, op in view.BINDS if op != kb.PANEL_OP] + [c for c, _ in send.SEND_BINDS + view.MENU_BINDS + video.VIDEO_BINDS]
        self.assertNotIn(bind_check.normalize('CTRL + ALT + K'), {bind_check.normalize(c) for c in others})
        # Omarchy's K binds are Super+Ctrl+K and Super+Alt+K: other chords.
        for omarchy in ('SUPER + CTRL + K', 'SUPER + ALT + K'):
            self.assertNotEqual(bind_check.normalize(omarchy), bind_check.normalize('CTRL + ALT + K'))
        self.assertNotIn(kb.PANEL_OP, view.USER_OPS)  # a key, not a panel button

    @unittest.skipUnless(Path('/usr/share/omarchy/default/hypr').is_dir(), 'Omarchy not installed')
    def test_omarchys_own_bind_files_never_bind_it(self):
        text = '\n'.join(p.read_text(errors='replace') for p in Path('/usr/share/omarchy/default/hypr').rglob('*.lua'))
        found = re.findall(r'o\.bind\w*\(\s*"([^"]+)"', text) + re.findall(r'hl\.bind\(\s*"([^"]+)"', text)
        chords = {bind_check.normalize(m) for m in found if 'mouse' not in m and not m.rstrip().endswith('+')}
        self.assertNotIn(bind_check.normalize('CTRL + ALT + K'), chords)
        self.assertIn(bind_check.normalize('CTRL + ALT + DELETE'), chords)  # the parse finds Omarchy's Ctrl+Alt binds

    def test_parse_chord_writes_hyprland_lua_and_needs_a_real_modifier(self):
        self.assertEqual(kb.parse_chord('ctrl+alt+k'), 'CTRL + ALT + K')
        self.assertEqual(kb.parse_chord('SUPER + SHIFT + CTRL + j'), 'CTRL + SHIFT + SUPER + J')
        self.assertEqual(kb.parse_chord('CTRL + ALT + 1'), 'CTRL + ALT + code:10')  # digits by keycode, as Omarchy
        self.assertEqual(kb.parse_chord('CTRL + ALT + Prior'), 'CTRL + ALT + Page_Up')
        self.assertEqual(kb.parse_chord('win + Up'), 'SUPER + up')
        for bad in ('K', 'SHIFT + K', 'CTRL + ALT + Escape', 'CTRL + ALT + F13', 'CTRL + CTRL + K', 'HYPER + K', 'CTRL +',
                    '', 'CTRL + ALT + mouse:272', 'x' * 70, None):
            with self.subTest(bad), self.assertRaises(ValueError): kb.parse_chord(bad)
        for chord, _ in view.BINDS + send.SEND_BINDS:  # every default is already in this form
            self.assertEqual(kb.parse_chord(chord), chord)

    def test_key_presses_become_chords_with_us_names(self):
        self.assertEqual(kb.key_chord(CTRL | ALT, K), 'CTRL + ALT + K')
        self.assertEqual(kb.key_chord(CTRL | ALT | SHIFT, 34), 'CTRL + ALT + SHIFT + bracketleft')
        self.assertEqual(kb.key_chord(SUPER, 10), 'SUPER + code:10')
        self.assertEqual(kb.key_chord(CTRL | 16, J), 'CTRL + J')  # Num Lock (MOD2) does not count
        for mods, code in ((0, K), (SHIFT, K), (CTRL, ESC), (CTRL, 200), (CTRL, 37)):
            self.assertIsNone(kb.key_chord(mods, code))
        self.assertEqual(kb.BINDABLE, frozenset(kb.KEYCODES.values()) - {'Escape'})
        for code, name in kb.KEYCODES.items():  # every key a press can name has a help-card label
            if name != 'Escape': self.assertTrue(view.chord_label(kb.key_chord(CTRL, code)).startswith('Ctrl+'))
        self.assertEqual(view.chord_label('CTRL + SUPER + comma'), 'Ctrl+Super+,')
        self.assertEqual(view.chord_label('CTRL + ALT + Return'), 'Ctrl+Alt+Enter')

    def test_catalog_lists_every_run_and_send_bind_once_with_a_label(self):
        ops = [op for op, _ in kb.CATALOG]
        self.assertEqual(sorted(ops), sorted([op for _, op in view.BINDS] + list(send.SEND_OPS)))
        self.assertEqual(len(ops), len(set(ops)))
        self.assertEqual(ops[0], 'zoom_in')
        self.assertEqual(ops.index('binds_toggle'), ops.index('help') + 1)  # it shares the help card's row
        for op, chord in kb.CATALOG:
            self.assertTrue(0 < len(kb.label(op)) <= kb.MAX_LABEL and kb.label(op).isascii(), op)
            self.assertEqual(kb.DEFAULTS[op], chord)


class SavedFileTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / 'viture-workspace' / 'keybinds.json'

    def test_missing_unreadable_or_foreign_files_mean_the_defaults(self):
        self.assertEqual(kb.load(self.path).overrides, {})
        self.path.parent.mkdir(parents=True)
        for text in ('not json', '[]', '{"schema": 2, "binds": {"next": "CTRL + ALT + J"}}', '{"schema": 1}'):
            self.path.write_text(text)
            self.assertEqual(kb.load(self.path).overrides, {}, text)

    def test_only_valid_overrides_are_kept_and_saved_privately(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps({'schema': 1, 'binds': {'next': 'ctrl+alt+j', 'prev': 'K', 'nope': 'CTRL + ALT + X',
                                                                 'help': 'CTRL + ALT + H', 'zoom_in': 5}}))
        km = kb.load(self.path)
        self.assertEqual(km.overrides, {'next': 'CTRL + ALT + J'})  # bare K, unknown op, the default and a number dropped
        self.assertEqual(km.chord('next'), 'CTRL + ALT + J')
        self.assertEqual(km.chord('prev'), 'CTRL + ALT + left')
        km.set('prev', 'CTRL + ALT + SHIFT + left')
        kb.save(self.path, km)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual(json.loads(self.path.read_text()), {'schema': 1, 'binds': {'next': 'CTRL + ALT + J', 'prev': 'CTRL + ALT + SHIFT + left'}})
        self.assertEqual(kb.load(self.path).overrides, km.overrides)
        km.set('next', 'CTRL + ALT + right')  # its default: no longer an override
        km.reset('prev')
        kb.save(self.path, km)
        self.assertEqual(json.loads(self.path.read_text())['binds'], {})
        self.assertEqual(kb.config_path(), view.settings_path().with_name('keybinds.json'))


class DesktopBindTests(unittest.TestCase):
    def test_parse_plain_hyprctl_binds(self):
        entries = kb.parse_binds(HYPRCTL)
        self.assertEqual(len(entries), 7)
        self.assertEqual(entries[2]['key'], 'SUPER + code:10')
        self.assertEqual(entries[0]['description'], 'Close all windows')
        self.assertTrue(entries[5]['catchall'])

    def test_keyboard_chords_with_a_real_modifier_count_and_the_runs_own_are_left_out(self):
        d = kb.DesktopBinds.from_text(HYPRCTL, ours=['CTRL + ALT + V'])
        self.assertEqual(d.find('CTRL + ALT + Delete'), ('Close all windows', True))
        self.assertEqual(d.find('ctrl + alt + tab'), ('Focus on next monitor', True))
        self.assertEqual(d.find('SUPER + 1'), ('Switch to workspace 1', True))  # keycode bind, named in the plain form
        self.assertEqual(d.find('CTRL + SHIFT + S'), ('a Hyprland bind with no description', False))
        self.assertIsNone(d.find('CTRL + ALT + V'))       # this run's own
        self.assertIsNone(d.find('SUPER + mouse:272'))    # mouse
        self.assertIsNone(d.find('CTRL + ALT + K'))
        # Without counting it as ours, the run's Ctrl+Alt+V reads as an undescribed desktop bind.
        self.assertEqual(kb.DesktopBinds.from_text(HYPRCTL).find('CTRL + ALT + V')[1], False)

    def test_conflicts_say_with_what_and_turn_the_binds_off_that_would_delete_a_desktop_bind(self):
        km = kb.KeyMap({'next': 'CTRL + ALT + Delete', 'prev': 'CTRL + SHIFT + S', 'recenter': 'CTRL + ALT + up'})
        notes, off = kb.find_conflicts(km, kb.DesktopBinds.from_text(HYPRCTL))
        self.assertEqual(off, {'next', 'recenter'})  # recenter: the same keys as Maximize, which comes first
        self.assertIn('Close all windows', notes['next']); self.assertTrue(notes['next'].startswith('Off'))
        self.assertIn('no description', notes['prev'])     # flagged, still installed
        self.assertIn(kb.label('zoom_in'), notes['recenter'])
        self.assertIn(kb.label('recenter'), notes['zoom_in'])
        self.assertEqual(kb.find_conflicts(kb.KeyMap(), kb.DesktopBinds.from_text(DESKTOP)), ({}, set()))
        self.assertIn('mode_next', kb.find_conflicts(kb.KeyMap(), kb.DesktopBinds.from_text(HYPRCTL))[0])  # a leftover: flagged
        self.assertEqual(kb.find_conflicts(km, None)[1], {'recenter'})  # desktop unknown: only XR against XR
        for note in notes.values(): self.assertTrue(len(note) <= kb.MAX_TEXT and note.isascii())


class PanelTests(unittest.TestCase):
    def control(self, keymap=None, text='', client=True, notify=True):
        """A view control with a key bind panel; `text`: the desktop's binds read at the start (and by the panel)."""
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.menu, self.toasts = [], []
        self.clock = Clock()
        c = view.ViewControl(view.ViewState(NAMES), 'run', 'session', lambda n: None, LayoutStore(self.dir / 'layout.json'),
                             None, menu_binds=self.menu.append, clock=self.clock)
        self.client = FakeClient(text) if client else None
        desktop = kb.DesktopBinds.from_text(text) if text else None
        c.keys = kb.BindControl(c, keymap or kb.KeyMap(), self.dir / 'keybinds.json', client=self.client, bind_args=ARGS,
                                desktop=desktop, notify=self.toasts.append if notify else None)
        c.keys.installed()
        self.addCleanup(c.keys.stop)
        self.addCleanup(c.join_focus, 2)
        return c

    def settle(self, c):
        c.keys.stop(); c.keys.stopped = False  # let the off-loop work finish, then go on
        c.join_focus(2)

    def press(self, c, mods, code):
        out = c.on_message({'op': kb.WIRE_OP, 'payload': {'mods': mods, 'code': code}})
        self.settle(c)
        return binds_payload(out)

    def open(self, c):
        out = c.on_message({'op': kb.PANEL_OP, 'payload': {}})
        self.settle(c)
        return binds_payload(out)

    def test_opening_shows_every_bind_and_takes_the_keyboard(self):
        c = self.control()
        p = self.open(c)
        self.assertTrue(p['visible'] and c.keys_open)
        self.assertEqual(len(p['rows']), len(kb.CATALOG))
        self.assertEqual(p['rows'][0], {'action': kb.label('zoom_in'), 'keys': 'Ctrl+Alt+Up', 'note': '', 'state': 'normal'})
        self.assertEqual(p['selected'], 0)
        self.assertEqual(p['title'], 'Key binds')
        self.assertIn(('open', ARGS), self.client.calls)
        self.assertIn(('read',), self.client.calls)  # the newest Hyprland binds, for conflicts
        closed = binds_payload(c.on_message({'op': kb.PANEL_OP, 'payload': {}}))  # Ctrl+Alt+K again
        self.settle(c)
        self.assertEqual(closed, {'visible': False})
        self.assertEqual(self.client.calls[-1], ('close',))
        self.assertFalse(c.keys_open)

    def test_payloads_fit_the_renderers_limits(self):
        c = self.control(kb.KeyMap({'next': 'CTRL + ALT + Delete'}), HYPRCTL)
        p = self.open(c)
        for row in p['rows']:
            self.assertEqual(set(row), {'action', 'keys', 'note', 'state'})
            self.assertTrue(0 < len(row['action']) <= 48 and 0 < len(row['keys']) <= 48 and len(row['note']) <= 96)
            self.assertIn(row['state'], ('normal', 'custom', 'conflict', 'capture'))
        self.assertLessEqual(len(p['hint']), 96); self.assertLessEqual(len(p['status']), 96)
        self.assertLessEqual(len(frames(kb.binds_frame('run', 's', 1, p))[0]['payload']['rows']), 64)
        self.assertLess(len(kb.binds_frame('run', 's', 1, p)), 16384)  # one control frame (ipc.cpp)

    def test_up_down_page_home_end_pick_a_row(self):
        c = self.control()
        self.open(c)
        self.assertEqual(self.press(c, 0, DOWN)['selected'], 1)
        self.assertEqual(self.press(c, 0, UP)['selected'], 0)
        self.assertEqual(self.press(c, 0, UP)['selected'], 0)  # stays at the top
        self.assertEqual(self.press(c, 0, PGDN)['selected'], kb.PAGE)
        self.assertEqual(self.press(c, 0, END)['selected'], len(kb.CATALOG) - 1)
        self.assertEqual(self.press(c, 0, HOME)['selected'], 0)
        self.assertIn('Esc closes', self.press(c, CTRL | ALT, 113)['status'])  # another key: says what works

    def test_enter_then_new_keys_rebinds_live_and_saves(self):
        c = self.control()
        self.open(c)
        self.press(c, 0, DOWN); self.press(c, 0, DOWN); self.press(c, 0, DOWN)  # Next desktop
        waiting = self.press(c, 0, ENTER)
        self.assertEqual(waiting['rows'][3]['state'], 'capture')
        self.assertEqual(waiting['rows'][3]['keys'], 'Press keys...')
        self.assertIn('New keys for Next desktop', waiting['status'])
        self.client.calls.clear()
        done = self.press(c, CTRL | ALT, J)
        self.assertEqual(done['rows'][3], {'action': 'Next desktop', 'keys': 'Ctrl+Alt+J', 'note': '', 'state': 'custom'})
        self.assertIn('Next desktop is now Ctrl+Alt+J', done['status'])
        self.assertEqual(json.loads((self.dir / 'keybinds.json').read_text())['binds'], {'next': 'CTRL + ALT + J'})
        # Live: the run binds go back in with the new keys (the reinstall also closes the panel), then the panel reopens.
        kind, args, kw = self.client.calls[0]
        self.assertEqual((kind, args), ('reinstall', ARGS))
        self.assertIn(('CTRL + ALT + J', 'next'), kw['binds'])
        self.assertFalse(kw['typing'])
        self.assertEqual(self.client.calls[1], ('open', ARGS))
        lua = view.install_binds_lua(*ARGS, binds=kw['binds'])
        self.assertIn('hl.bind("CTRL + ALT + J", hl.dsp.exec_cmd(c .. "next"))', lua)
        self.assertNotIn('"CTRL + ALT + right"', lua)
        # The help card follows.
        help_rows = frames(c.on_message({'op': 'help', 'payload': {}}))[-1]['payload']['rows']
        self.assertIn(['Ctrl+Alt+J', 'Next desktop'], help_rows)

    def test_escape_cancels_a_change_and_then_closes_the_panel(self):
        c = self.control()
        self.open(c)
        self.press(c, 0, ENTER)
        cancelled = self.press(c, 0, ESC)
        self.assertTrue(cancelled['visible'])
        self.assertEqual(cancelled['rows'][0]['state'], 'normal')
        self.assertIn('cancelled', cancelled['status'])
        self.assertEqual(self.press(c, 0, ESC), {'visible': False})
        self.assertEqual(c.keys.keymap.overrides, {})

    def test_the_toggle_keys_close_the_panel_from_inside_it(self):
        c = self.control(kb.KeyMap({kb.PANEL_OP: 'CTRL + SUPER + J'}))
        self.open(c)
        self.assertTrue(self.press(c, CTRL | ALT, K)['visible'])  # not the toggle any more
        self.assertEqual(self.press(c, CTRL | SUPER, J), {'visible': False})

    def test_keys_that_cannot_be_a_bind_are_explained_and_the_change_keeps_waiting(self):
        c = self.control()
        self.open(c)
        self.press(c, 0, ENTER)
        self.assertIn('Hold Ctrl, Alt or Super', self.press(c, 0, J)['status'])
        self.assertIn('Hold Ctrl, Alt or Super', self.press(c, SHIFT, J)['status'])
        self.assertIn('cannot be bound', self.press(c, CTRL, 191)['status'])
        self.assertTrue(c.keys.capturing)

    def test_keys_another_xr_bind_has_are_refused_and_both_rows_say_so(self):
        c = self.control()
        self.open(c)
        self.press(c, 0, ENTER)  # Maximize
        refused = self.press(c, CTRL | ALT, 46)  # Ctrl+Alt+L: the placement pill
        self.assertTrue(refused['alert'])
        self.assertIn(f"Ctrl+Alt+L is already {kb.label('menu_toggle')}", refused['status'])
        pill = next(r for r in refused['rows'] if r['action'] == kb.label('menu_toggle'))
        self.assertEqual(pill['state'], 'conflict'); self.assertIn('Ctrl+Alt+L', pill['note'])
        self.assertTrue(c.keys.capturing and c.keys.keymap.overrides == {})
        self.assertEqual(len(self.toasts), 1); self.assertIn('Ctrl+Alt+L', self.toasts[0])
        self.assertEqual(self.press(c, CTRL | ALT, J)['rows'][0]['keys'], 'Ctrl+Alt+J')  # other keys still work

    def test_keys_hyprland_holds_are_refused_with_its_description(self):
        c = self.control(text=DESKTOP)
        self.client.text = HYPRCTL
        self.open(c)
        self.press(c, 0, ENTER)
        refused = self.press(c, CTRL | ALT, 119)  # Ctrl+Alt+Delete: Omarchy's close all windows
        self.assertIn('Close all windows', refused['status'])
        self.assertEqual(refused['rows'][0]['state'], 'conflict')
        self.assertIn('Close all windows', refused['rows'][0]['note'])
        self.assertIn('Close all windows', self.toasts[-1])
        refused = self.press(c, SUPER, 10)  # Super+1, a keycode bind
        self.assertIn('Switch to workspace 1', refused['status'])
        self.assertEqual(c.keys.keymap.overrides, {})

    def test_a_saved_conflict_is_off_highlighted_and_toasted_at_the_start(self):
        km = kb.KeyMap({'next': 'CTRL + ALT + Delete'})
        c = self.control(km, DESKTOP)
        self.client.text = HYPRCTL  # the panel's own read, with the run's binds in it
        self.assertIn('next', c.keys.off)
        self.assertNotIn('next', [op for _, op in c.keys.run_binds()])
        self.assertEqual(c.keys.bind_kwargs()['binds'], tuple(b for b in view.BINDS if b[1] != 'next'))
        toast = c.keys.startup_toast()
        c.keys.stop()
        self.assertEqual(self.toasts, [toast])
        self.assertIn('Next desktop', toast); self.assertIn('Close all windows', toast); self.assertIn('Ctrl+Alt+K', toast)
        c.keys.stopped = False
        p = self.open(c)
        row = p['rows'][3]
        self.assertEqual(row['state'], 'conflict'); self.assertTrue(row['note'].startswith('Off'))
        self.assertTrue(p['alert'])
        self.assertIn('1 bind with conflicts', p['status'])
        # Reset puts it back to its default: no conflict, installed again.
        self.press(c, 0, DOWN); self.press(c, 0, DOWN); self.press(c, 0, DOWN)
        fixed = self.press(c, 0, R)
        self.assertEqual(fixed['rows'][3]['state'], 'normal')
        self.assertNotIn('next', c.keys.off)
        self.assertEqual(c.keys.bind_kwargs(), {})  # all defaults: the run binds are exactly what they always were
        self.assertIn(('reinstall', ARGS, {'typing': False}), self.client.calls)

    def test_no_conflicts_means_no_toast_and_no_bind_arguments(self):
        c = self.control(text=DESKTOP)
        self.assertIsNone(c.keys.startup_toast())
        self.assertEqual(c.keys.bind_kwargs(), {})
        self.assertEqual(self.toasts, [])

    def test_r_resets_one_and_shift_r_twice_resets_all(self):
        c = self.control(kb.KeyMap({'zoom_in': 'CTRL + ALT + J', 'next': 'CTRL + SUPER + J'}))
        self.open(c)
        self.assertIn('back to Ctrl+Alt+Up', self.press(c, 0, R)['status'])
        self.assertEqual(c.keys.keymap.overrides, {'next': 'CTRL + SUPER + J'})
        self.assertIn('already has its default', self.press(c, 0, R)['status'])
        self.assertIn('again', self.press(c, SHIFT, R)['status'])
        self.assertEqual(c.keys.keymap.overrides, {'next': 'CTRL + SUPER + J'})
        self.assertIn('Every bind is back', self.press(c, SHIFT, R)['status'])
        self.assertEqual(c.keys.keymap.overrides, {})
        self.assertEqual(json.loads((self.dir / 'keybinds.json').read_text())['binds'], {})
        # The arm expires.
        self.press(c, SHIFT, R)
        self.clock.now += kb.RESET_ARM_S + 0.1
        c.poll()
        self.assertIn('again', self.press(c, SHIFT, R)['status'])

    def test_a_change_waits_only_so_long_and_the_panel_closes_when_idle(self):
        c = self.control()
        self.open(c)
        self.press(c, 0, ENTER)
        self.clock.now += kb.CAPTURE_TIMEOUT_S + 0.1
        out = binds_payload(c.poll())
        self.assertFalse(c.keys.capturing); self.assertIn('unchanged', out['status'])
        self.clock.now += view.OVERLAY_IDLE_CLOSE_S + 0.1
        self.assertEqual(binds_payload(c.poll()), {'visible': False})
        self.settle(c)
        self.assertEqual(self.client.calls[-1], ('close',))

    def test_a_panel_that_cannot_take_the_keyboard_closes_and_says_so(self):
        c = self.control()
        self.client.fail_open = True
        self.open(c)
        self.assertEqual(binds_payload(c.poll()), {'visible': False})
        self.settle(c)
        self.assertIn('could not take the keyboard', self.toasts[-1])

    def test_the_desktops_binds_read_at_opening_update_the_conflicts(self):
        c = self.control(kb.KeyMap({'next': 'CTRL + ALT + Delete'}))
        self.assertEqual(c.keys.off, set())  # unknown at first: installed as before
        self.client.text = HYPRCTL + OURS_ON_DELETE  # Omarchy's Ctrl+Alt+Delete, and the run's own
        self.open(c)
        p = binds_payload(c.poll())
        self.assertEqual(p['rows'][3]['state'], 'conflict')
        self.assertIn('next', c.keys.off)
        self.settle(c)
        reinstall = [call for call in self.client.calls if call[0] == 'reinstall']
        self.assertTrue(reinstall and ('CTRL + ALT + Delete', 'next') not in reinstall[-1][2]['binds'])

    def test_typing_mode_a_video_the_help_card_and_a_placement_panel_close_or_block_it(self):
        c = self.control()
        self.open(c)
        frames(c.on_message({'op': view.TYPING_OP, 'payload': {}}))
        self.assertFalse(c.keys_open)
        self.assertIsNone(c.on_message({'op': kb.PANEL_OP, 'payload': {}}))  # its bind is gone in typing mode
        c.on_message({'op': view.TYPING_OP, 'payload': {}})
        self.open(c)
        out = frames(c.on_message({'op': 'help', 'payload': {}}))
        self.assertEqual([f['op'] for f in out], ['binds', 'help'])  # one centred card at a time
        self.assertFalse(c.keys_open)
        self.open(c)
        self.assertFalse(c.help_visible)  # and the other way round
        out = frames(c.on_message({'op': 'menu_toggle', 'payload': {}}))
        self.assertIn('binds', [f['op'] for f in out])
        self.assertFalse(c.keys_open)
        c.on_message({'op': 'menu_toggle', 'payload': {}})
        c.video_active = True
        self.assertIsNone(c.on_message({'op': kb.PANEL_OP, 'payload': {}}))
        self.assertFalse(c.keys_open)

    def test_a_video_opening_closes_it(self):
        c = self.control()
        v = video.VideoControl(c, binds=None, notify=lambda t: None)
        self.open(c)
        out = v._open(os.open('/dev/null', os.O_RDONLY), 'clip', {'projection': '360', 'stereo': 'mono', 'fov': 180}, 'file', [])
        for fd in out.fds: os.close(fd)
        self.assertIn('binds', [f['op'] for f in frames(bytes(out))])
        self.assertFalse(c.keys_open)

    def test_the_free_pointer_lets_go_while_it_is_open(self):
        from tools.native_spike import free_pointer
        c = self.control()
        pointer = free_pointer.OverviewPointer(c)
        self.addCleanup(pointer.close)
        self.assertTrue(pointer.overview())
        self.open(c)
        self.assertFalse(pointer.overview())

    def test_a_reload_brings_the_open_panels_subscription_back(self):
        c = self.control()
        self.open(c)
        self.client.calls.clear()
        c.keys.after_reload()
        self.assertEqual(self.client.calls[0], ('open', ARGS))

    def test_without_a_compositor_or_toasts_it_still_works(self):
        c = self.control(client=False, notify=False)
        p = self.open(c)
        self.assertTrue(p['visible'])
        self.press(c, 0, ENTER)
        self.assertEqual(self.press(c, CTRL | ALT, J)['rows'][0]['keys'], 'Ctrl+Alt+J')

    def test_stale_or_closed_keys_do_nothing_but_close_the_compositors_panel_again(self):
        c = self.control()
        self.assertIsNone(c.on_message({'op': kb.WIRE_OP, 'payload': {'mods': 0, 'code': ENTER}}))
        self.settle(c)
        self.assertEqual(self.client.calls, [])  # nothing open, nothing installed: nothing to do
        c.keys._panel_installed = True  # a close that failed: the compositor still has it
        self.assertIsNone(c.on_message({'op': kb.WIRE_OP, 'payload': {'mods': 0, 'code': ENTER}}))
        self.settle(c)
        self.assertEqual(self.client.calls, [('close',)])
        self.assertFalse(c.keys_open)
        c.keys = None
        self.assertIsNone(c.on_message({'op': kb.PANEL_OP, 'payload': {}}))


class LuaAndWireTests(unittest.TestCase):
    def test_the_panel_lua_enters_its_own_submap_and_reports_keys(self):
        lua = kb.panel_on_lua(*ARGS)
        self.assertLess(len(lua), 4096)  # hyprland.HyprlandClient._eval
        self.assertIn('local c = "/usr/bin/python3 -I /opt/x/xr_control.py /run/user/1000/x/control.sock run session binds_key "', lua)
        self.assertIn('hl.define_submap("xr_workspace_keys", function() s.catchall = hl.bind("catchall", hl.dsp.no_op(), { ignore_mods = true }) end)', lua)
        self.assertIn('if not s.catchall or tostring(s.catchall) == "HL.Keybind(expired)"', lua)  # made once, never removed
        self.assertNotIn(':remove() end) end s.catchall', lua)
        self.assertIn('pcall(hl.on, "input.keyboard.key"', lua)
        self.assertIn('hl.exec_cmd(c .. m .. " " .. code)', lua)
        self.assertIn('if s.esc >= 3', lua)  # Esc three times: the compositor gives the keyboard back by itself
        self.assertIn(f'os.time() - s.seen > {kb.WATCHDOG_S}', lua)
        self.assertIn('pcall(hl.dispatch, hl.dsp.submap("xr_workspace_keys"))', lua)
        self.assertTrue(lua.endswith('if hl.get_current_submap() ~= "xr_workspace_keys" then xr_workspace_keys_off() error("no key submap") end'))
        self.assertIn('error("no key subscription")', lua)
        self.assertIn('error("no catch-all")', lua)
        self.assertIn('if not s.timer then', lua)  # one watchdog per Hyprland config, enabled while open
        self.assertLess(lua.index('error("no catch-all")'), lua.index('pcall(hl.on,'))  # no subscription without the submap's catch-all
        for code, bit in kb.MODIFIER_KEYS: self.assertIn(f'{{ {code}, {bit} }}', lua)
        for bad in (('/usr/bin/python3', '/a b', '/s', 'r', 's'), ('/usr/bin/python3', '/a', '/s', 'r;x', 's')):
            with self.assertRaises(ValueError): kb.panel_on_lua(*bad)

    def test_closing_is_part_of_every_bind_removal_and_the_helper_repeats_it(self):
        self.assertIn(kb.PANEL_OFF_LUA, view.remove_binds_lua())
        self.assertEqual(xr_control.LEAVE_KEY_PANEL, kb.PANEL_OFF_LUA)
        self.assertIn('hl.get_current_submap() == "xr_workspace_keys"', kb.PANEL_OFF_LUA)  # never a submap of the user's

    def test_reinstalls_stay_under_the_eval_limit_with_the_longest_keys(self):
        longest = 'CTRL + ALT + SHIFT + SUPER + bracketright'
        self.assertEqual(kb.parse_chord(longest), longest)
        binds = tuple((longest, op) for _, op in view.BINDS)
        real = ('/usr/bin/python3', '/home/cody/.t3/worktrees/viture-workspace/feature-placement-menu-integrate/tools/native_spike/xr_control.py',
                '/run/user/1000/xr-workspace-spike/' + 'r' * 36 + '/control.sock', 'r' * 36, 's' * 40)
        with self.assertRaises(ValueError): view.install_binds_lua(*real, binds=binds + binds[:1])  # an op twice
        self.assertLess(len(view.remove_binds_lua() + ' ' + view.install_binds_lua(*real, binds=binds)), 4096)
        sends = tuple((longest, op) for _, op in send.SEND_BINDS)
        self.assertLess(len(view.install_send_binds_lua(*real, binds=sends)), 4096)
        self.assertLess(len(kb.panel_on_lua(*real)), 4096)

    def test_custom_binds_are_checked_before_they_reach_lua(self):
        for bad in ((('CTRL + ALT + J', 'nope'),), (('K', 'next'),), (('ctrl + alt + j', 'next'),), (('CTRL + ALT + J"', 'next'),)):
            with self.subTest(bad), self.assertRaises(ValueError): view.install_binds_lua(*ARGS, binds=bad)
        with self.assertRaises(ValueError): view.install_send_binds_lua(*ARGS, binds=(('CTRL + ALT + J', 'next'),))
        typing = view.install_binds_lua(*ARGS, typing=True, binds=(('CTRL + SUPER + T', 'typing_toggle'), ('CTRL + ALT + J', 'next')))
        self.assertEqual(typing.count('hl.bind('), 1)
        self.assertIn('"CTRL + SUPER + T"', typing)
        self.assertEqual(view.install_binds_lua(*ARGS), view.install_binds_lua(*ARGS, binds=view.BINDS))  # defaults unchanged

    def test_the_client_evals_generated_lua_and_reads_plain_binds(self):
        client = hyprland.HyprlandClient('a' * 40 + '_123_456', Path('/nonexistent'))
        sent, requests = [], []
        client._eval = sent.append
        client._request = lambda request, timeout=2.0: requests.append(request) or HYPRCTL.encode()
        client.open_key_panel(*ARGS)
        client.close_key_panel()
        self.assertEqual(sent, [kb.panel_on_lua(*ARGS), kb.PANEL_OFF_LUA])
        self.assertEqual(client.binds_text(), HYPRCTL)
        self.assertEqual(requests, ['binds'])  # plain, not j/binds
        with self.assertRaises(ValueError): client.binds_text(timeout=0)
        sent.clear()
        custom = (('CTRL + ALT + J', 'next'),)
        client.reinstall_view_binds(*ARGS, binds=custom, send_binds=(('CTRL + ALT + N', 'send_back'),))
        self.assertEqual(sent, [view.remove_binds_lua() + ' ' + view.install_binds_lua(*ARGS, binds=custom),
                                view.install_send_binds_lua(*ARGS, binds=(('CTRL + ALT + N', 'send_back'),))])

    def test_the_control_wire_takes_key_presses_and_nothing_else(self):
        def frame(op, payload):
            return (json.dumps({'schema': 1, 'request_id': 5, 'run_id': 'run', 'session': 's', 'op': op, 'payload': payload}) + '\n').encode()
        self.assertEqual(decode_control(frame(kb.PANEL_OP, {}))['op'], kb.PANEL_OP)
        self.assertEqual(decode_control(frame(kb.WIRE_OP, {'mods': 12, 'code': 45}))['payload'], {'mods': 12, 'code': 45})
        for bad in ({}, {'mods': 12}, {'mods': 12, 'code': 7}, {'mods': 256, 'code': 45}, {'mods': '12', 'code': 45},
                    {'mods': 12, 'code': 45, 'x': 1}, {'mods': True, 'code': 45}):
            with self.subTest(bad), self.assertRaises(ValueError): decode_control(frame(kb.WIRE_OP, bad))
        with self.assertRaises(ValueError): decode_control(frame(kb.PANEL_OP, {'open': True}))

    def test_the_helper_sends_key_presses_and_gives_the_keyboard_back_without_a_run(self):
        sent, stale = [], []
        with mock.patch.object(xr_control, 'send', lambda *a: sent.append(a)):
            self.assertEqual(xr_control.main(['x', '/run/s.sock', 'run', 'sess', 'binds_key', '12', '45']), 0)
        self.assertEqual(sent, [('/run/s.sock', 'run', 'sess', 'binds_key', {'mods': 12, 'code': 45})])
        frame = xr_control.control_frame('run', 'sess', 'binds_key', {'mods': 12, 'code': 45}, 7)
        self.assertEqual(decode_control(frame.encode())['payload'], {'mods': 12, 'code': 45})
        for bad in (['x', '/run/s.sock', 'run', 'sess', 'binds_key'], ['x', '/run/s.sock', 'run', 'sess', 'binds_key', '12', 'x'],
                    ['x', '/run/s.sock', 'run', 'sess', 'binds_key', '12', '4'], ['x', '/run/s.sock', 'run', 'sess', 'next', '12', '45'],
                    ['x', '/run/s.sock', 'run', 'sess', 'binds_key', '1234', '45']):
            with self.subTest(bad): self.assertEqual(xr_control.main(bad), 2)
        def dead(*a): raise OSError('no run')
        with mock.patch.object(xr_control, 'send', dead), mock.patch.object(xr_control, '_eval_stale', lambda env, code: stale.append(code)):
            self.assertEqual(xr_control.main(['x', '/run/s.sock', 'run', 'sess', 'binds_key', '0', '9']), 1)
            self.assertEqual(xr_control.main(['x', '/run/s.sock', 'run', 'sess', 'binds_toggle']), 1)
        self.assertEqual(stale, [xr_control.LEAVE_KEY_PANEL] * 2)


class RendererContractTests(unittest.TestCase):
    def test_the_payload_limits_and_states_are_the_renderers(self):
        hpp = (ROOT / 'spike/include/xr/binds_panel.hpp').read_text()
        limits = dict(re.findall(r'(kMaxBind\w+) = (\d+)', hpp))
        self.assertEqual(int(limits['kMaxBindLabel']), kb.MAX_LABEL)
        self.assertEqual(int(limits['kMaxBindText']), kb.MAX_TEXT)
        self.assertLessEqual(len(kb.CATALOG), int(limits['kMaxBindRows']))
        self.assertIn('enum class BindRowState : std::uint8_t { normal, custom, conflict, capture };', hpp)
        ipc = (ROOT / 'spike/src/ipc.cpp').read_text()
        for state in ('normal', 'custom', 'conflict', 'capture'): self.assertIn(f'state == "{state}"', ipc)
        self.assertIn('else if (name == "binds") op = ControlOp::binds;', ipc)


class ManagedTests(unittest.TestCase):
    def test_the_run_reads_the_desktops_binds_first_and_leaves_a_conflicting_bind_out(self):
        from tools.native_spike import managed
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        store = LayoutStore(Path(tmp.name) / 'layout.json')
        Path(tmp.name, 'keybinds.json').write_text(json.dumps({'schema': 1, 'binds': {'next': 'CTRL + ALT + Delete', 'prev': 'CTRL + ALT + J'}}))
        calls = []

        class Client:
            runtime_dir = Path(tmp.name)
            def binds_text(self, timeout=2.0): calls.append('read'); return DESKTOP
            def install_view_binds(self, *args, **kw): calls.append(('install', kw))
            def remove_view_binds(self): calls.append('remove')
            def open_key_panel(self, *args): calls.append('open')
            def close_key_panel(self): calls.append('close')
            def reinstall_view_binds(self, *args, **kw): calls.append(('reinstall', kw))
        runtime = Path(tmp.name) / 'rt'
        runtime.mkdir(mode=0o700)
        Client.runtime_dir = runtime
        with managed.view_controls(Client(), 'run1', 's1', NAMES, store=store) as control:
            keys = control.view.keys
            self.assertEqual(calls[0], 'read')
            install = calls[1][1]
            self.assertNotIn('next', [op for _, op in install['binds']])
            self.assertIn(('CTRL + ALT + J', 'prev'), install['binds'])
            self.assertEqual(install['send_binds'], send.SEND_BINDS)
            self.assertEqual(keys.path, Path(tmp.name) / 'keybinds.json')
            self.assertIn('next', keys.conflicts)
        self.assertEqual(calls[-1], 'remove')


if __name__ == '__main__':
    unittest.main()
