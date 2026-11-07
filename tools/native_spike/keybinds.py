"""Key bind panel (Ctrl+Alt+K by default): every XR Workspace run bind, rebindable live in the glasses, with conflicts.

The panel is a head-locked Material Design 3 card in the glasses (the renderer's `binds` op, spike/include/xr/
binds_panel.hpp). It lists every bind a run installs that you can change (CATALOG: the view binds, view.BINDS, and the
send-window binds, send.SEND_BINDS) with its action and its current keys. Ctrl+Alt+K (PANEL_BIND, a run bind like the
others, so rebindable too) opens it and closes it again.

Keys while it is open. The panel is modal: every key goes to it, and nothing else (no window, no Omarchy or XR bind)
sees it until it closes. Up / Down (PgUp / PgDn, Home / End) pick a bind, Enter changes it: the panel waits for the next
key combination and that becomes the bind, saved and in effect at once, with no XR restart. Esc cancels the change. R
puts the picked bind back to its default, Shift+R twice (within RESET_ARM_S) puts every bind back. Esc or the panel's own
keys close it. It closes by itself after view.OVERLAY_IDLE_CLOSE_S without a key (the OLED panels), a change waits at
most CAPTURE_TIMEOUT_S for its keys, and typing mode (Ctrl+Alt+T), a VR video, the help card and a placement panel all
close it.

How the keys reach it. Hyprland 0.56 binds cannot be taken back one by one: Keybind:remove() erases every bind with the
same modifiers and key, in every submap (Hyprland src/config/lua/objects/LuaKeybind.cpp keybindRemove). So the panel binds
no keys at all. Opening it (panel_on_lua) enters a Hyprland submap of its own (SUBMAP) that holds one catch-all bind,
created once and never removed, which swallows every key while the submap is active (Hyprland's own and Omarchy's binds
are inactive there too), and subscribes to Hyprland's `input.keyboard.key` event, which reports every key press with its
keycode before any bind runs. The subscription sends each press, with the Ctrl / Alt / Shift / Super keys then held, to
the run through the bind helper (xr_control.py `binds_key MODS CODE`), and the run decides what it means (BindControl.key).
Closing (PANEL_OFF_LUA) drops the subscription and leaves the submap; it is part of view.remove_binds_lua(), so a run's end,
a typing-mode switch and a config reload all leave the keyboard free. If the run is gone, the next key's helper finds no
run and gives the keyboard back the same way (xr_control.LEAVE_KEY_PANEL). Esc three times in a row gives it back in
the compositor itself, whatever the run and the helper do, and the subscription closes the panel after WATCHDOG_S
without a key.

Keys are named from their keycode with the US layout (KEYCODES; Omarchy's default, and this host's /etc/vconsole.conf):
the digits stay keycodes (code:10..code:19, as Omarchy and send.SEND_BINDS bind them). A new bind needs Ctrl, Alt or Super
(with or without Shift) and a letter, digit, F-key, arrow, navigation key, Space, Tab, Enter, Backspace or punctuation
(parse_chord). Esc stays the panel's.

Conflicts (find_conflicts). Two run binds on one chord would both run, and an XR bind on a chord the desktop also binds
would delete the desktop's bind when the run removes its own (the removal rule above). So:
  * a new bind on keys another XR bind already uses, or that Hyprland / Omarchy bind (read live from `hyprctl binds`:
    DesktopBinds), is refused: the panel keeps waiting, the row says what holds those keys and a toast repeats it;
  * on load (each XR start, and whenever the panel opens) a saved or default bind whose keys a Hyprland bind with a
    description holds (every Omarchy bind has one) is not installed at all ("off"), and a second XR bind on the same keys
    as an earlier one is off too; the row is highlighted, says why, and a toast at the start names the first one. An
    undescribed Hyprland bind on the same keys (a user's plain hl.bind, or the leftovers of a run that crashed: a Hyprland
    reload clears those) is flagged the same way but still installed, as before this panel existed.
The run's own binds are told apart in `hyprctl binds` by count: as many entries per chord as this run installed are its own.

Saved in ~/.config/viture-workspace/keybinds.json (config_path), mode 0600: {"schema": 1, "binds": {op: chord}}, only the
binds that differ from their default. A missing, unreadable or invalid file (or entry) means the defaults.
"""
from collections import Counter
import json
import os
from pathlib import Path
import queue
import threading
import time

from . import send, view
from .bind_check import normalize

PANEL_OP = view.BINDS_PANEL_OP
PANEL_BIND = next(b for b in view.BINDS if b[1] == PANEL_OP)
WIRE_OP = 'binds_key'  # the subscription's key presses: payload {"mods": modmask, "code": xkb keycode}
OPS = (PANEL_OP, WIRE_OP)
SUBMAP = 'xr_workspace_keys'
SCHEMA = 1
CAPTURE_TIMEOUT_S = 15  # a change with no keys pressed is cancelled after this long
RESET_ARM_S = 4.0       # Shift+R resets every bind only when pressed twice within this
WATCHDOG_S = 90         # the compositor closes the panel after this long without a key, whatever the run does
PAGE = 8                # PgUp / PgDn move this many rows
MAX_TEXT = 96           # spike/include/xr/binds_panel.hpp kMaxBindText (notes, hint, status)
MAX_LABEL = 48          # its kMaxBindLabel (action, keys, title)

# Hyprland modmask bits (src/devices/IKeyboard.hpp HL_MODIFIER_*), for `hyprctl binds` and the key subscription.
MODMASK = ((1, 'SHIFT'), (2, 'CAPS'), (4, 'CTRL'), (8, 'ALT'), (16, 'MOD2'), (32, 'MOD3'), (64, 'SUPER'), (128, 'MOD5'))
MOD_ORDER = ('CTRL', 'ALT', 'SHIFT', 'SUPER')  # how a chord is written (the defaults: 'CTRL + ALT + K')
MOD_NAMES = {'CTRL': 'CTRL', 'CONTROL': 'CTRL', 'ALT': 'ALT', 'MOD1': 'ALT', 'SHIFT': 'SHIFT', 'SUPER': 'SUPER',
             'WIN': 'SUPER', 'LOGO': 'SUPER', 'MOD4': 'SUPER', 'META': 'SUPER'}
# xkb keycode (evdev + 8) -> Hyprland key name, US layout. Digits stay keycodes (layout-independent, as Omarchy binds them).
KEYCODES = {
    9: 'Escape', **{10 + i: f'code:{10 + i}' for i in range(10)}, 20: 'minus', 21: 'equal', 22: 'BackSpace', 23: 'Tab',
    **dict(zip(range(24, 34), 'QWERTYUIOP')), 34: 'bracketleft', 35: 'bracketright', 36: 'Return',
    **dict(zip(range(38, 47), 'ASDFGHJKL')), 47: 'semicolon', 48: 'apostrophe', 49: 'grave', 51: 'backslash',
    **dict(zip(range(52, 59), 'ZXCVBNM')), 59: 'comma', 60: 'period', 61: 'slash', 65: 'space',
    **{67 + i: f'F{i + 1}' for i in range(10)}, 95: 'F11', 96: 'F12', 110: 'Home', 111: 'up', 112: 'Page_Up',
    113: 'left', 114: 'right', 115: 'End', 116: 'down', 117: 'Page_Down', 118: 'Insert', 119: 'Delete'}
# Keys a bind may use: every named key but Esc (the panel's cancel).
BINDABLE = frozenset(KEYCODES.values()) - {'Escape'}
_KEY_LOOKUP = {**{k.lower(): k for k in BINDABLE}, 'prior': 'Page_Up', 'next': 'Page_Down', 'enter': 'Return',
               **{str((i + 1) % 10): f'code:{10 + i}' for i in range(10)}}
# Modifier keys (and Caps / Num Lock, AltGr): the subscription does not report them on their own. Their keycodes, with
# the modmask bit each sets while held (hl.is_key_down(keycode)).
MODIFIER_KEYS = ((37, 4), (105, 4), (64, 8), (108, 8), (50, 1), (62, 1), (133, 64), (134, 64))
SILENT_KEYS = tuple(code for code, _ in MODIFIER_KEYS) + (66, 77, 92)
ENTER_CODES = (36, 104)  # Enter and the keypad's Enter: change the picked bind

# Panel labels for ops whose help-card text says something else (view.HELP_ACTIONS), and the send-window binds.
LABELS = {'help': 'Help card (every key)', 'zoom_out': 'Overview', PANEL_OP: 'Key binds (this panel)',
          'menu_toggle': 'Placement pill of the selected desktop',
          'send_glasses': 'Send window to the selected desktop', 'send_back': 'Send window back to the main display',
          **{f'send_glasses_{n}': f'Send window to XR-{n}' for n in range(1, 5)}}


def _catalog():
    """(op, default chord) for every bind the panel lists, in its order: the help card's sections (each pair's ops
    together), the other run binds, then the send-window binds."""
    defaults = {op: chord for chord, op in view.BINDS}
    order = []
    for section in view.HELP_SECTIONS:
        for op in section:
            order.append(op)
            if op in view.HELP_PAIRS: order += list(view._pair_seconds(op))
    order += [op for _, op in view.BINDS if op not in order]
    return tuple((op, defaults[op]) for op in order if op in defaults) + tuple((op, chord) for chord, op in send.SEND_BINDS)


CATALOG = _catalog()
DEFAULTS = dict(CATALOG)
RUN_OPS = tuple(op for _, op in view.BINDS)
SEND_OPS = tuple(op for _, op in send.SEND_BINDS)


def label(op):
    return LABELS.get(op) or view.HELP_ACTIONS[op]


def parse_chord(text):
    """A bind's keys from 'CTRL + ALT + K', 'ctrl+alt+k' or the helper's 'CTRL+ALT+K', as written in Hyprland Lua
    ('CTRL + ALT + K'): modifiers in MOD_ORDER, then the key. ValueError unless it has Ctrl, Alt or Super (Shift may join
    them) and a BINDABLE key."""
    if not isinstance(text, str) or not 0 < len(text) <= 64: raise ValueError('invalid keys')
    parts = [p.strip() for p in text.split('+')]
    if len(parts) < 2 or not all(parts): raise ValueError('invalid keys')
    mods = set()
    for p in parts[:-1]:
        name = MOD_NAMES.get(p.upper())
        if name is None or name in mods: raise ValueError('invalid keys')
        mods.add(name)
    key = _KEY_LOOKUP.get(parts[-1].lower())
    if key is None: raise ValueError('that key cannot be bound')
    if not mods - {'SHIFT'}: raise ValueError('a bind needs Ctrl, Alt or Super')
    return ' + '.join([m for m in MOD_ORDER if m in mods] + [key])


def key_chord(mods, code):
    """The subscription's press (modmask, keycode) as a chord string, or None if that is no possible bind."""
    key = KEYCODES.get(code)
    if key is None or type(mods) is not int or mods < 0: return None
    names = [name for bit, name in MODMASK if mods & bit and name in MOD_ORDER]
    try: return parse_chord(' + '.join(names + [key]))
    except ValueError: return None


def chord_label(chord):
    return view.chord_label(chord)


def config_path():
    return view.settings_path().with_name('keybinds.json')


class KeyMap:
    """The effective binds: every CATALOG op's default, or its saved override."""
    def __init__(self, overrides=None):
        self.overrides = {}
        for op, chord in dict(overrides or {}).items():
            if op not in DEFAULTS: continue
            try: chord = parse_chord(chord)
            except ValueError: continue
            if chord != DEFAULTS[op]: self.overrides[op] = chord

    def chord(self, op):
        return self.overrides.get(op, DEFAULTS[op])

    def items(self):
        return [(op, self.chord(op)) for op, _ in CATALOG]

    def custom(self, op):
        return op in self.overrides

    def set(self, op, chord):
        if op not in DEFAULTS: raise ValueError('unknown bind')
        chord = parse_chord(chord)
        if chord == DEFAULTS[op]: self.overrides.pop(op, None)
        else: self.overrides[op] = chord

    def reset(self, op=None):
        if op is None: self.overrides.clear()
        else: self.overrides.pop(op, None)


def load(path):
    """The saved KeyMap at `path` (defaults if it is missing or unreadable; invalid entries are dropped)."""
    try: data = json.loads(Path(path).read_text())
    except (OSError, ValueError): return KeyMap()
    if not isinstance(data, dict) or data.get('schema') != SCHEMA or not isinstance(data.get('binds'), dict): return KeyMap()
    return KeyMap({op: chord for op, chord in data['binds'].items() if isinstance(op, str) and isinstance(chord, str)})


def save(path, keymap):
    """keybinds.json, written atomically, mode 0600: the overrides only."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(path.name + '.tmp')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as f: json.dump({'schema': SCHEMA, 'binds': dict(sorted(keymap.overrides.items()))}, f, indent=1)
    os.replace(tmp, path)


# --- the desktop's binds (`hyprctl binds`) ------------------------------------------------------------------------
def _ascii(text, limit):
    text = ''.join(c if ' ' <= c < '\x7f' else '?' for c in str(text)).strip()
    return text if len(text) <= limit else text[:limit - 3].rstrip() + '...'


def parse_binds(text):
    """Every bind in plain `hyprctl binds` output, as dicts: modmask, submap, key, catchall, description, dispatcher.
    (The plain form names a keycode bind's key, 'CTRL + ALT + code:10'; Hyprland 0.56's JSON leaves it empty.)"""
    out = []
    for block in str(text).replace('\r', '').split('\n\n'):
        lines = [line for line in block.split('\n') if line.strip()]
        if not lines or not lines[0].startswith('bind'): continue
        fields = {}
        for line in lines[1:]:
            name, sep, value = line.strip().partition(':')
            if sep: fields[name.strip()] = value.strip()
        try: modmask = int(fields.get('modmask', ''))
        except ValueError: continue
        out.append({'modmask': modmask, 'submap': fields.get('submap', ''), 'key': fields.get('key', ''),
                    'catchall': fields.get('catchall') == 'true', 'description': fields.get('description', ''),
                    'dispatcher': fields.get('dispatcher', '')})
    return out


def _entry_chord(entry):
    """A `hyprctl binds` entry's keys, normalized (bind_check.normalize), or None if no XR bind could share them."""
    if entry['submap'] or entry['catchall']: return None
    mods = [name for bit, name in MODMASK if entry['modmask'] & bit]
    if not set(mods) & {'CTRL', 'ALT', 'SUPER'}: return None  # an XR bind always has one of these
    key = entry['key'].split('+')[-1].strip() if '+' in entry['key'] else entry['key'].strip()
    if not key or key.startswith(('mouse', 'switch:')): return None
    try: return normalize(' + '.join(mods + [key]))
    except ValueError: return None


class DesktopBinds:
    """The desktop's keyboard binds, by normalized chord: [(description, described)] (described: it has one, as every
    Omarchy bind does). `ours`: chords this run had installed when they were read (one entry each is the run's own)."""
    def __init__(self, entries=(), ours=()):
        grouped = {}
        for entry in entries:
            chord = _entry_chord(entry)
            if chord is not None: grouped.setdefault(chord, []).append(entry)
        mine = Counter(normalize(c) for c in ours)
        self.chords = {}
        for chord, found in grouped.items():
            found = sorted(found, key=lambda e: bool(e['description']))  # the run's own binds have no description
            kept = found[mine.get(chord, 0):]
            if kept:
                self.chords[chord] = [(_ascii(e['description'], 60) if e['description'] else '', bool(e['description']))
                                      for e in kept]

    @classmethod
    def from_text(cls, text, ours=()):
        return cls(parse_binds(text), ours)

    def find(self, chord):
        """(what holds `chord`, described) or None."""
        found = self.chords.get(normalize(chord))
        if not found: return None
        described = [d for d, has in found if has]
        return (described[0], True) if described else ('a Hyprland bind with no description', False)


def find_conflicts(keymap, desktop):
    """({op: note}, off) for keymap: every bind that shares its keys with another XR bind or a desktop bind gets a note
    saying with what; `off` holds the ones not installed (a described desktop bind holds the keys, or an earlier XR bind
    in CATALOG order does). desktop None: unknown, only XR binds are compared."""
    notes, off, first = {}, set(), {}
    for op, chord in keymap.items():
        key = normalize(chord)
        held = desktop.find(chord) if desktop is not None else None
        if held is not None and held[1]:
            notes[op], off = f'Off: Hyprland binds {chord_label(chord)} to "{held[0]}"', off | {op}
            continue
        if key in first:
            other = first[key]
            notes[op] = f'Off: {chord_label(chord)} is already {label(other)}'
            notes.setdefault(other, f'{label(op)} has the same keys (off)')
            off.add(op)
            continue
        first[key] = op
        if held is not None: notes[op] = f'Hyprland also binds {chord_label(chord)} ({held[0]})'
    return {op: _ascii(n, MAX_TEXT) for op, n in notes.items()}, off


# --- compositor Lua ---------------------------------------------------------------------------------------------
# The panel off: the key subscription and the watchdog go, and the compositor leaves the panel's submap (only that one:
# never a submap of the user's). Safe with nothing open, and after a config reload (a fresh Lua state: only the submap
# is left). Part of view.remove_binds_lua(); xr_control.LEAVE_KEY_PANEL repeats it (tests compare).
PANEL_OFF_LUA = ('if xr_workspace_keys_off then xr_workspace_keys_off() end '
                 f'if hl.get_current_submap() == "{SUBMAP}" then hl.dispatch(hl.dsp.submap("reset")) end')


def panel_on_lua(python, client, control_socket, run_id, session):
    """The panel on: its submap (with its never-removed catch-all, made once per Hyprland config) entered, and every key
    press reported to the run (module docstring). Replaces any subscription left from before."""
    for p in (python, client, control_socket):
        if not isinstance(p, str) or not view.SAFE_PATH.match(p): raise ValueError('unsafe path for bind command')
    for t in (run_id, session):
        if not isinstance(t, str) or not view.SAFE_TOKEN.match(t): raise ValueError('unsafe token for bind command')
    held = ', '.join(f'{{ {code}, {bit} }}' for code, bit in MODIFIER_KEYS)
    silent = ', '.join(f'[{code}] = true' for code in SILENT_KEYS)
    return (f'local c = "{python} -I {client} {control_socket} {run_id} {session} {WIRE_OP} " '
            'if xr_workspace_keys_off then xr_workspace_keys_off() end '
            'local s = xr_workspace_keys or {} xr_workspace_keys = s '
            'if not s.catchall or tostring(s.catchall) == "HL.Keybind(expired)" then '
            f'hl.define_submap("{SUBMAP}", function() s.catchall = hl.bind("catchall", hl.dsp.no_op(), {{ ignore_mods = true }}) end) end '
            'if not s.catchall or tostring(s.catchall) == "HL.Keybind(expired)" then error("no catch-all") end '
            f'local held, silent = {{ {held} }}, {{ {silent} }} '
            's.seen, s.esc = os.time(), 0 '
            'xr_workspace_keys_off = function() xr_workspace_keys_off = nil '
            'if s.sub then pcall(function() s.sub:remove() end) end s.sub = nil '
            'if s.timer then pcall(function() s.timer:set_enabled(false) end) end '
            f'if hl.get_current_submap() == "{SUBMAP}" then hl.dispatch(hl.dsp.submap("reset")) end end '
            'local ok, sub = pcall(hl.on, "input.keyboard.key", function(code, _, state) '
            'if state ~= 1 or silent[code] then return end '
            's.seen = os.time() '
            'local m, bits = 0, {} '
            'for _, k in ipairs(held) do if not bits[k[2]] and hl.is_key_down(k[1]) then bits[k[2]] = true m = m + k[2] end end '
            'if code == 9 and m == 0 then s.esc = s.esc + 1 else s.esc = 0 end '
            'if s.esc >= 3 and xr_workspace_keys_off then xr_workspace_keys_off() return end '
            'hl.exec_cmd(c .. m .. " " .. code) end) '
            # No subscription: the keyboard is never taken (the eval fails, and the run says so: BindControl.poll).
            'if not ok or not sub then xr_workspace_keys_off() error("no key subscription") end s.sub = sub '
            # One watchdog per Hyprland config (timers live until a reload): enabled while the panel is open.
            'if not s.timer then pcall(function() s.timer = hl.timer(function() '
            f'if os.time() - s.seen > {WATCHDOG_S} and xr_workspace_keys_off then xr_workspace_keys_off() end end, '
            '{ timeout = 5000, type = "repeat" }) end) end '
            'if s.timer then pcall(function() s.timer:set_enabled(true) end) end '
            f'pcall(hl.dispatch, hl.dsp.submap("{SUBMAP}")) '
            # Not in the submap after all: no subscription may outlive it (every key would run the helper).
            f'if hl.get_current_submap() ~= "{SUBMAP}" then xr_workspace_keys_off() error("no key submap") end')


def binds_frame(run_id, session, request_id, payload):
    return (json.dumps({'schema': 1, 'request_id': request_id, 'run_id': run_id, 'session': session, 'op': 'binds',
                        'payload': payload}, separators=(',', ':')) + '\n').encode()


FAILED = object()  # BindControl._reads: the panel's keyboard could not be taken
HINT = 'Up/Down pick   Enter change   R reset   Shift+R reset all   Esc close'
CAPTURE_HINT = 'Press the new keys (Ctrl, Alt or Super with a key)   Esc cancel'


class BindControl:
    """The panel, supervisor side (module docstring). Lives in ViewControl.keys; its compositor work (the panel's key
    subscription, the run binds reinstalled with new keys, reading `hyprctl binds`) runs off the supervisor loop, under
    the view control's bind lock, through `client` (hyprland.HyprlandClient: open_key_panel, close_key_panel,
    reinstall_view_binds, binds_text; None: no compositor, as in tests of the state alone)."""
    def __init__(self, control, keymap=None, path=None, client=None, bind_args=None, desktop=None, notify=None, clock=None):
        self.control = control
        self.keymap = keymap if keymap is not None else KeyMap()
        self.path, self.client, self.bind_args = path, client, bind_args
        self.desktop = desktop    # DesktopBinds, or None (unknown)
        self.notify = notify      # (text) -> None: a toast (a desktop notification, in the glasses)
        self.clock = clock or control.clock
        self.open = self.capturing = False
        self.selected = 0
        self.status, self.alert = '', False
        self.refused = None       # (op, note): the row a refused change pointed at, while the change still waits
        self.capture_at = self.armed_at = None
        self.last_input = self.clock()
        self.version = 0          # bumps on every keymap change
        self.installed_version = 0  # the keymap the run binds were last installed with
        self._panel_installed = False
        self._installed = None    # (run binds, send binds) last installed (managed.view_controls sets the first)
        self._threads = []
        self._reads = queue.Queue()
        self._reading = False
        self.stopped = False
        self.conflicts, self.off = find_conflicts(self.keymap, self.desktop)

    # --- binds to install ----------------------------------------------------------------------------------------
    def run_binds(self):
        """view.BINDS with this keymap's keys: (chord, op), the ops that are off left out."""
        return tuple((self.keymap.chord(op), op) for op in RUN_OPS if op not in self.off)

    def send_binds(self):
        return tuple((self.keymap.chord(op), op) for op in SEND_OPS if op not in self.off)

    def bind_kwargs(self):
        """Keyword arguments for the client's install_view_binds / reinstall_view_binds: none with every default
        installed (the bind Lua is then exactly what it always was), else `binds` and `send_binds`."""
        if self.run_binds() == view.BINDS and self.send_binds() == send.SEND_BINDS: return {}
        return {'binds': self.run_binds(), 'send_binds': self.send_binds()}

    def installed(self):
        """The run binds were installed with this keymap (managed.view_controls, at the start): remembered for
        installed_chords."""
        self._installed = (self.run_binds(), self.send_binds())
        self.installed_version = self.version

    def installed_chords(self):
        """The chords this run's bind tables hold now (typing mode: only the Ctrl+Alt+T toggle)."""
        run, sends = self._installed if self._installed is not None else ((), ())
        if getattr(self.control.state, 'typing', False): return [c for c, op in run if op == view.TYPING_OP]
        return [c for c, _ in run + sends]

    def startup_toast(self):
        """At the start: one toast naming the first conflict (and how many), or None."""
        if not self.conflicts: return None
        op = next(o for o, _ in CATALOG if o in self.conflicts)
        more = len(self.conflicts) - 1
        toggle = chord_label(self.keymap.chord(PANEL_OP)) if PANEL_OP not in self.off else 'the key bind panel'
        text = (f'Key bind conflict: {label(op)}: {self.conflicts[op]}.' + (f' {more} more.' if more else '') +
                f' {toggle} shows them all.')
        self._tell(text)
        return text

    def _recompute(self):
        before = self.off
        self.conflicts, self.off = find_conflicts(self.keymap, self.desktop)
        if self.off != before: self.version += 1  # a bind turned off or back on: reinstall

    # --- frames --------------------------------------------------------------------------------------------------
    def payload(self):
        if not self.open: return {'visible': False}
        rows = []
        for i, (op, chord) in enumerate(self.keymap.items()):
            note = self.conflicts.get(op, '')
            state = 'conflict' if note else 'custom' if self.keymap.custom(op) else 'normal'
            keys = chord_label(chord)
            if self.capturing and i == self.selected:
                state, keys = 'capture', 'Press keys...'
            if self.refused is not None and self.refused[0] == op: state, note = 'conflict', self.refused[1]
            rows.append({'action': _ascii(label(op), MAX_LABEL), 'keys': _ascii(keys, MAX_LABEL), 'note': note, 'state': state})
        return {'visible': True, 'title': 'Key binds', 'rows': rows, 'selected': self.selected,
                'hint': CAPTURE_HINT if self.capturing else HINT, 'status': _ascii(self.status or self._summary(), MAX_TEXT),
                'alert': bool(self.alert or (not self.status and self.conflicts))}

    def _summary(self):
        n = len(self.conflicts)
        if n: return f'{n} bind{"s" if n > 1 else ""} with conflicts (marked). Change them, or R to reset.'
        if self.desktop is None: return 'Hyprland binds not read yet: conflicts with them unknown.'
        return 'No conflicts with each other or with Hyprland / Omarchy binds.'

    def _frame(self):
        control = self.control
        frame = binds_frame(control.run_id, control.session, control.next_id, self.payload())
        control.next_id += 1
        return frame

    # --- ops -----------------------------------------------------------------------------------------------------
    def on_message(self, message):
        op = message.get('op')
        if op == PANEL_OP: return self.toggle() or None
        if op == WIRE_OP:
            payload = message.get('payload') or {}
            return self.key(payload.get('mods'), payload.get('code')) or None
        return None

    def toggle(self):
        return self.close() if self.open else self.open_panel()

    def open_panel(self):
        c = self.control
        if self.open or self.stopped or getattr(c.state, 'typing', False) or getattr(c, 'video_active', False): return b''
        out = b''
        if c.help_visible: out += c._help_toggle()  # one centred card at a time
        if c.state.menu_open: out += c._on_menu('menu_toggle') or b''  # its keys and the panel's would clash
        self.open, self.capturing, self.refused, self.armed_at = True, False, None, None
        self.status, self.alert = '', False
        self.last_input = self.clock()
        self._read_desktop()  # the newest Hyprland binds, for conflicts (results in poll)
        self._sync()
        return out + self._frame()

    def close(self):
        if not self.open: return b''
        self.open = self.capturing = False
        self.refused = self.armed_at = self.capture_at = None
        self.status, self.alert = '', False
        self._sync()
        return self._frame()

    def key(self, mods, code):
        """A key pressed while the panel is open (the compositor's subscription): browse or capture. Frames."""
        if type(mods) is not int or type(code) is not int: return b''
        if not self.open:  # the compositor still holds the keyboard for a closed panel (a failed close): close it again
            self._sync()
            return b''
        self.last_input = self.control.last_input = self.clock()
        name = KEYCODES.get(code)
        plain = mods & ~1 == 0  # nothing held but (maybe) Shift
        if self.capturing:
            if name == 'Escape' and mods == 0: return self._cancel('Change cancelled.')
            chord = key_chord(mods, code)
            if chord is None:
                if plain and name is not None:
                    return self._say('Hold Ctrl, Alt or Super too (Esc cancels).')
                return self._say('That key cannot be bound here. Try another (Esc cancels).')
            return self._capture(chord)
        if name == 'Escape' and mods == 0: return self.close()
        chord = key_chord(mods, code)
        if chord is not None and normalize(chord) == normalize(self.keymap.chord(PANEL_OP)): return self.close()
        if mods == 0 and name in ('up', 'down', 'Page_Up', 'Page_Down', 'Home', 'End'):
            last = len(CATALOG) - 1
            step = {'up': -1, 'down': 1, 'Page_Up': -PAGE, 'Page_Down': PAGE, 'Home': -last, 'End': last}[name]
            self.selected = min(max(self.selected + step, 0), last)
            self.armed_at, self.status, self.alert = None, '', False
            return self._frame()
        if mods == 0 and code in ENTER_CODES:
            self.capturing, self.capture_at, self.refused, self.armed_at = True, self.clock(), None, None
            op = CATALOG[self.selected][0]
            self.status, self.alert = f'New keys for {label(op)} (now {chord_label(self.keymap.chord(op))}).', False
            return self._frame()
        if name == 'R' and mods == 0: return self._reset(CATALOG[self.selected][0])
        if name == 'R' and mods == 1: return self._reset_all()
        return self._say('Esc closes the panel; Up / Down and Enter change a bind.')

    def _say(self, text, alert=False):
        self.status, self.alert = text, alert
        return self._frame()

    def _cancel(self, text):
        self.capturing, self.refused, self.capture_at = False, None, None
        return self._say(text)

    def _capture(self, chord):
        op = CATALOG[self.selected][0]
        if normalize(chord) == normalize(self.keymap.chord(op)) and op not in self.off:
            return self._cancel(f'{label(op)} stays {chord_label(chord)}.')
        other = next((o for o, c in self.keymap.items() if o != op and o not in self.off and normalize(c) == normalize(chord)), None)
        held = self.desktop.find(chord) if self.desktop is not None else None
        if other is not None:
            text = f'{chord_label(chord)} is already {label(other)}. Press other keys, or Esc.'
            self.refused = (other, _ascii(f'Has {chord_label(chord)}: change this one first to use it', MAX_TEXT))
        elif held is not None:
            text = f'Hyprland binds {chord_label(chord)} to "{held[0]}". Press other keys, or Esc.'
            self.refused = (op, _ascii(f'{chord_label(chord)} is taken: Hyprland "{held[0]}"', MAX_TEXT))
        else:
            self.keymap.set(op, chord)
            self.capturing, self.refused, self.capture_at = False, None, None
            return self._changed(f'{label(op)} is now {chord_label(chord)}.')
        self._tell(text)
        return self._say(_ascii(text, MAX_TEXT), alert=True)

    def _reset(self, op):
        if not self.keymap.custom(op):
            return self._say(f'{label(op)} already has its default keys ({chord_label(DEFAULTS[op])}).')
        self.keymap.reset(op)
        return self._changed(f'{label(op)} is back to {chord_label(DEFAULTS[op])}.')

    def _reset_all(self):
        now = self.clock()
        if self.armed_at is None or now - self.armed_at >= RESET_ARM_S:
            self.armed_at = now
            return self._say('Press Shift+R again to put every bind back to its default.')
        self.armed_at = None
        if not self.keymap.overrides: return self._say('Every bind already has its default keys.')
        self.keymap.reset()
        return self._changed('Every bind is back to its default keys.')

    def _changed(self, text):
        """The keymap changed: saved, conflicts again, the run binds reinstalled (live), the panel redrawn."""
        self.version += 1
        alert = False
        if self.path is not None:
            try: save(self.path, self.keymap)
            except OSError as exc:
                text, alert = f'{text} Not saved: {exc.strerror or exc}.', True
        self._recompute()
        if self.conflicts and not alert:
            self._tell(f'Key binds: {len(self.conflicts)} with conflicts. They are marked in the panel.')
        self._sync()
        return self._say(_ascii(text, MAX_TEXT), alert)

    def _tell(self, text):
        if self.notify is None: return
        def work():
            try: self.notify(text)
            except (OSError, ValueError, TypeError): pass
        t = threading.Thread(target=work, daemon=True); t.start(); self._threads.append(t)

    # --- the supervisor loop -------------------------------------------------------------------------------------
    def poll(self, now):
        out = b''
        while True:
            try: desktop = self._reads.get_nowait()
            except queue.Empty: break
            if desktop is FAILED:
                if self.open:
                    out += self.close()
                    self._tell('The key bind panel could not take the keyboard, so it closed. Your binds are unchanged.')
                continue
            self._reading = False
            self.desktop = desktop
            before = (self.conflicts, self.off)
            self._recompute()
            if self.off != before[1]: self._sync()
            if self.open and (self.conflicts, self.off) != before: out += self._frame()
        if not self.open: return out
        if self.capturing and self.capture_at is not None and now - self.capture_at >= CAPTURE_TIMEOUT_S:
            out += self._cancel('No keys pressed: the bind is unchanged.')
        if self.armed_at is not None and now - self.armed_at >= RESET_ARM_S:
            self.armed_at = None
            out += self._say('')
        if now - self.last_input >= view.OVERLAY_IDLE_CLOSE_S: out += self.close()
        return out

    # --- compositor work (off the loop) ----------------------------------------------------------------------------
    def _read_desktop(self):
        if self.client is None or not hasattr(self.client, 'binds_text') or self._reading: return
        self._reading = True
        t = threading.Thread(target=self._read_work, daemon=True); t.start(); self._threads.append(t)

    def _read_work(self):
        try:
            with self.control._menu_lock:  # ordered with bind changes: the run's own chords are counted right
                desktop = DesktopBinds.from_text(self.client.binds_text(), self.installed_chords())
        except (OSError, ValueError, TypeError):
            desktop = self.desktop
        self._reads.put(desktop)  # poll() takes it on the next loop tick

    def _sync(self, force=False):
        if self.client is None or self.stopped: return
        self._threads = [t for t in self._threads if t.is_alive()]
        t = threading.Thread(target=self._work, args=(force,), daemon=True); t.start(); self._threads.append(t)

    def _work(self, force=False):
        c = self.control
        with c._menu_lock:
            if self.stopped: return
            try:
                if self.installed_version != self.version:
                    typing = bool(getattr(c.state, 'typing', False))
                    version = self.version
                    kwargs = self.bind_kwargs()
                    self.client.reinstall_view_binds(*self.bind_args, typing=typing, **kwargs)
                    self._installed = (self.run_binds(), self.send_binds())
                    self.installed_version = version
                    c._typing_installed = typing
                    self._panel_installed = False  # view.remove_binds_lua() closed it too
                    c._menu_binds_installed = False
                    c._options_binds_installed = False  # the same table
                    if c.state.menu_open and c.menu_binds is not None:
                        c.menu_binds(True)
                        c._menu_binds_installed = True
                    elif c.options.keyboard_owned and c.options_binds is not None:
                        c.options_binds(True)
                        c._options_binds_installed = True
                want = self.open
                if want != self._panel_installed or (force and want):
                    if want: self.client.open_key_panel(*self.bind_args)
                    else: self.client.close_key_panel()
                    self._panel_installed = want
            except (OSError, ValueError, TypeError, KeyError):
                # Best effort: Esc, the idle close and the run's end still give the keyboard back. A panel that could not
                # take the keyboard closes again, and says so (poll).
                if self.open and not self._panel_installed: self._reads.put(FAILED)

    def after_reload(self):
        """A Hyprland config reload dropped every runtime bind and the Lua state (managed.after_reload put the run
        binds back with this keymap): the panel's subscription comes back if it is open."""
        with self.control._menu_lock:
            self._panel_installed = False
            self.installed()  # managed.after_reload installed this keymap's binds
        self._work(force=True)
        self._read_desktop()  # the reload may have changed the desktop's binds

    def stop(self, timeout=2.0):
        """Run end: no compositor change after this (view.remove_binds_lua() closes an open panel)."""
        self.stopped = True
        deadline = time.monotonic() + timeout
        for t in self._threads: t.join(max(0.0, deadline - time.monotonic()))
