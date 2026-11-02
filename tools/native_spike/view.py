"""Glasses view controls for a managed capture run: zoom in/out, switch desktop, spacing.

Keyboard binds exist only while a run is active (user preference: Ctrl+Alt+arrows stay free for
text navigation otherwise). They run xr_control.py, which sends one frame to this run's private
control socket. Focus moves with Hyprland's monitor focus (the cursor follows).

Viewing modes (T2, Ctrl+Alt+V cycles): anchor (world-locked 3DoF), follow (world-locked inside a
dead zone, eases back in front) and locked (0DoF, head-locked). Ctrl+Alt+H toggles a help card in
the glasses listing every bind (rows come from BINDS, so it cannot drift). Optional cursor follow (Ctrl+Alt+F):
when the pointer crosses onto another managed output, the view switches to it (Hyprland focusedmon).
Head-pose prediction (Ctrl+Alt+P toggles; on by default since live run prd2 on 2026-10-31): the renderer extrapolates the pose by a
bounded horizon (measured software read->present latency plus the driver's per-glasses look-ahead).
Mode, cursor follow, prediction and spacing are saved in layout.json. The renderer shows a short pop-up
in the glasses whenever the viewing mode, cursor follow or prediction changes (and the viewing mode at start).
Per-desktop placement (position, distance, yaw/pitch/roll, curvature; geometry.PLACEMENT_BOUNDS) arrives as one absolute
`placement` message from the controller (panel), is applied by the renderer and saved in the glasses model's profile.
It is not a keyboard op and not listed in capabilities (the panel renders those as buttons).
Placement menu (Ctrl+Alt+L; Ctrl+Alt+P was already prediction): an in-glasses, keyboard-driven editor for the same placements.
While it is open a second, modal bind set (MENU_BINDS: arrows, Shift+arrows, Tab, R, Escape, Ctrl+Alt+M) is installed and it is removed
again on close. Every change is applied live through the same `placement` frame, sent to the renderer as a `placement_menu`
frame (the drawn overlay) and saved to the profile 500 ms after the last change, or at once when the menu closes.
Menu ops are not in USER_OPS, so they are not panel capabilities either.
Overview corner resize (resize.py): hovering a desktop's corner in the overview shows a handle; dragging it with the left
button changes that desktop's `distance` placement live (apparent size), saved like a menu change. resize_press and
resize_release come from modal mouse binds that exist only while the pointer is on a corner; they are not USER_OPS.
It is off while the placement menu is open, so the menu's mouse mode and a corner drag never share a click.

Send-window binds (send.py: Ctrl+Alt+G to the selected desktop, Ctrl+Alt+1/2/3 to XR-n, Ctrl+Alt+B back to the physical
fallback) are installed and removed with the run binds, in their own Lua table and eval. They are not in BINDS, so they
add no help card row (a 13th row does not fit); the Ctrl+Alt+H row names Ctrl+Alt+G/B instead.

Mouse mode (Ctrl+Alt+M while the menu is open, a modal menu bind): each menu row is drawn as a slider, and a round
pointer moves freely over the whole card, 1:1 with the mouse. A sampler thread reads Hyprland's cursor position every
MOUSE_SAMPLE_S, adds up the motion (re-centring the hidden cursor so it never stops at a screen edge) and wakes the
supervisor loop, which moves the pointer in card pixels and sends one small `menu_pointer` frame; the renderer draws it
as its own overlay on the next frame without re-rendering the card. The renderer reports the card's layout (tabs and
sliders, in card pixels) on its status socket (`menu_geometry`); clicks are hit-tested against it here. While mouse mode
is on, a left-click bind (MOUSE_BINDS) snaps the slider under the pointer to the pointer and drags it until release (a
click on a desktop tab selects it; elsewhere a click does nothing), the desktop cursor is hidden (runtime
`cursor.invisible`) and cursor follow is paused. Ctrl+Alt+M again or Escape leave mouse mode: the cursor is shown again
and put back where it was. Any mouse motion or click counts as input for the overlay idle close.
"""
import math
import json
import os
from pathlib import Path
import re
import socket
import threading
import time

from .send import SEND_OPS

DEFAULT_SPACING, MAX_SPACING, SPACING_STEP = 40, 400, 20
USER_OPS = ('zoom_in', 'zoom_out', 'next', 'prev', 'spacing_up', 'spacing_down', 'recenter', 'mode_next', 'cursor_follow',
            'prediction', 'help')
MODES = ('anchor', 'follow', 'locked')  # cycle order
# Hyprland Lua key strings join modifiers with " + " (e.g. "SUPER + SHIFT + R").
BINDS = (('CTRL + ALT + up', 'zoom_in'), ('CTRL + ALT + down', 'zoom_out'), ('CTRL + ALT + left', 'prev'),
         ('CTRL + ALT + right', 'next'), ('CTRL + ALT + bracketleft', 'spacing_down'), ('CTRL + ALT + bracketright', 'spacing_up'),
         ('CTRL + ALT + space', 'recenter'), ('CTRL + ALT + V', 'mode_next'), ('CTRL + ALT + F', 'cursor_follow'),
         ('CTRL + ALT + P', 'prediction'), ('CTRL + ALT + H', 'help'), ('CTRL + ALT + L', 'menu_toggle'),
         ('CTRL + ALT + O', 'video_open'))  # video_open: the bind helper shows a file picker first (video.py)
# Ctrl+Alt+H help card: what each bind does (every op in BINDS needs an entry; tests enforce it).
HELP_ACTIONS = {'zoom_in': 'Zoom into the selected desktop', 'zoom_out': 'Overview; drag a corner there to resize',
                'prev': 'Previous desktop', 'next': 'Next desktop', 'spacing_down': 'Less space between desktops',
                'spacing_up': 'More space between desktops', 'recenter': 'Recenter the view',
                'mode_next': 'Cycle viewing mode', 'cursor_follow': 'Toggle cursor follow', 'prediction': 'Toggle pose prediction',
                'help': 'Help; Ctrl+Alt+G/B: window to glasses/back',
                'menu_toggle': 'Placement menu (Ctrl+Alt+M: mouse)', 'video_open': 'Play a 180/360 VR video'}
# Placement menu: actions (shared contract with the renderer overlay and tests) and the modal binds present only while open.
MENU_OPS = ('menu_toggle', 'menu_desktop_next', 'menu_desktop_prev', 'menu_field_next', 'menu_field_prev', 'menu_inc', 'menu_dec',
            'menu_inc_big', 'menu_dec_big', 'menu_reset', 'menu_mouse', 'menu_back')
# menu_back (Escape): leaves mouse mode if it is on, otherwise closes the menu. Ctrl+Alt+M is free in Omarchy and in
# this run's binds (tools/native_spike/bind_check.py checks it against the real config with Hyprland --verify-config).
MENU_BINDS = (('up', 'menu_field_prev'), ('down', 'menu_field_next'), ('left', 'menu_dec'), ('right', 'menu_inc'),
              ('SHIFT + left', 'menu_dec_big'), ('SHIFT + right', 'menu_inc_big'), ('Tab', 'menu_desktop_next'),
              ('SHIFT + Tab', 'menu_desktop_prev'), ('R', 'menu_reset'), ('Escape', 'menu_back'),
              ('CTRL + ALT + M', 'menu_mouse'))
# Mouse mode only: the left button (press, and release with { release = true }). Clicks go to the menu, not to windows.
MOUSE_OPS = ('menu_mouse_press', 'menu_mouse_release')
MOUSE_BINDS = (('mouse:272', 'menu_mouse_press'), ('mouse:272', 'menu_mouse_release'))
# Pointer, in menu-card pixels (the card is drawn 1:1 in the glasses): free over the whole card.
MOUSE_GAIN = 1.0           # card pixels per cursor pixel (the cursor's own speed and acceleration already apply)
MOUSE_SAMPLE_S = 1 / 250   # cursor reads while mouse mode is on: faster than the glasses' 120 Hz
MOUSE_RECENTRE_PX = 200    # the hidden cursor is put back to where mouse mode started once it is this far away
MOUSE_JUMP_PX = 1500       # a single sample moving farther than this is a warp, not a hand movement: re-baseline
MOUSE_SLOP_PX = 12         # a click this far past either end of a slider still grabs it (the pointer's radius)
MAX_POINTER_PX = 8192      # spike/include/xr/ipc.hpp kMaxPointerPx
# (small, Shift) step per field, in the field's own units (geometry.PLACEMENT_BOUNDS); results are clamped to the bounds.
MENU_STEPS = {'x': (2, 10), 'y': (2, 10), 'distance': (5, 25), 'yaw': (1, 10), 'pitch': (1, 10), 'roll': (1, 10),
              'curvature': (2, 20)}
MENU_SAVE_DELAY = 0.5  # seconds after the last menu change before the profile is written
# The help card and the placement menu close by themselves after this many seconds without input (a run key or a
# panel placement change), so static text does not burn into the glasses' micro-OLED panels. Change it here; the
# help card's footer note repeats it from spike/include/xr/hud.hpp kOverlayIdleCloseS (a test keeps them equal).
OVERLAY_IDLE_CLOSE_S = 30
KEY_NAMES = {'CTRL': 'Ctrl', 'ALT': 'Alt', 'SHIFT': 'Shift', 'SUPER': 'Super', 'up': 'Up', 'down': 'Down', 'left': 'Left',
             'right': 'Right', 'bracketleft': '[', 'bracketright': ']', 'space': 'Space', 'Tab': 'Tab', 'Escape': 'Esc',
             **{f'code:{10 + i}': str((i + 1) % 10) for i in range(10)}}  # Omarchy-style digit keycodes: code:10 is 1


def chord_label(chord):
    """'CTRL + ALT + bracketleft' -> 'Ctrl+Alt+['."""
    return '+'.join(KEY_NAMES.get(part, part.upper() if len(part) == 1 else part) for part in chord.split(' + '))


# Help card rows that are not key binds. Empty: a 13th row makes the card 1008 px tall with JetBrainsMono, past the
# 960 px glasses surface, so the overview corner resize (resize.py, a mouse drag) is named in the zoom_out row instead.
EXTRA_HELP_ROWS = ()


# Bind pairs shown as one help-card row, so the card stays at 12 rows: a 13th row makes it 1008 px tall with
# JetBrainsMono, past the 960 px glasses surface. first op -> (second op, action).
HELP_PAIRS = {'spacing_down': ('spacing_up', 'Less / more space between desktops')}


def help_rows():
    chords = {op: chord for chord, op in BINDS}
    seconds = {second for second, _ in HELP_PAIRS.values()}
    rows = []
    for chord, op in BINDS:
        if op in seconds: continue
        if op in HELP_PAIRS:
            second, action = HELP_PAIRS[op]
            a, b = chord_label(chord), chord_label(chords[second])
            prefix = a[:a.rfind('+') + 1]  # 'Ctrl+Alt+[' and 'Ctrl+Alt+]' -> 'Ctrl+Alt+[ / ]'
            rows.append([f'{a} / {b[len(prefix):]}' if b.startswith(prefix) else f'{a} / {b}', action])
        else:
            rows.append([chord_label(chord), HELP_ACTIONS[op]])
    return rows + [list(row) for row in EXTRA_HELP_ROWS]


def help_frame(run_id, session, request_id, visible):
    return (json.dumps({'schema': 1, 'request_id': request_id, 'run_id': run_id, 'session': session, 'op': 'help',
                        'payload': {'visible': bool(visible), 'rows': help_rows()}}, separators=(',', ':')) + '\n').encode()
FOCUSEDMON = re.compile(r'focusedmon>>(XR-[1-3]),')
CONFIGRELOADED = re.compile(r'configreloaded>>')  # Omarchy theme switches reload the Hyprland config
COLOR = re.compile(r'(?:0x)?([0-9a-fA-F]{8})\Z')


def parse_border(reply):
    """Hyprland getoption general:col.active_border -> (r, g, b), or None if unrecognised.

    0.56 replies {"gradient": "AARRGGBB[ AARRGGBB...] <angle>deg"}; older ones {"int": ARGB}. A
    gradient uses its first colour.
    """
    if not isinstance(reply, dict): return None
    if isinstance(reply.get('gradient'), str):
        first = reply['gradient'].split()[:1]
        m = COLOR.match(first[0]) if first else None
        if not m: return None
        value = int(m.group(1), 16)
    elif type(reply.get('int')) is int and 0 <= reply['int'] <= 0xffffffff:
        value = reply['int']
    else:
        return None
    return ((value >> 16) & 255, (value >> 8) & 255, value & 255)


def style_frame(run_id, session, request_id, rgb):
    if len(rgb) != 3 or any(type(c) is not int or not 0 <= c <= 255 for c in rgb): raise ValueError('invalid colour')
    return (json.dumps({'schema': 1, 'request_id': request_id, 'run_id': run_id, 'session': session, 'op': 'style',
                        'payload': {'border': list(rgb)}}, separators=(',', ':')) + '\n').encode()
MANAGED = re.compile(r'XR-[1-3]\Z')
SAFE_PATH = re.compile(r'/[A-Za-z0-9_./:-]+\Z')
SAFE_TOKEN = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z')


def placement_frame(run_id, session, request_id, placements):
    from .geometry import validate_placement
    if not isinstance(placements, list) or not 1 <= len(placements) <= 3: raise ValueError('invalid placements')
    return (json.dumps({'schema': 1, 'request_id': request_id, 'run_id': run_id, 'session': session, 'op': 'placement',
                        'payload': {'desktops': [validate_placement(p) for p in placements]}}, separators=(',', ':')) + '\n').encode()


def placement_menu_frame(run_id, session, request_id, menu):
    from .geometry import PLACEMENT_KEYS, validate_placements
    keys = {'open', 'desktop', 'field', 'placements'}
    if not isinstance(menu, dict) or set(menu) != keys: raise ValueError('invalid menu')
    if type(menu['open']) is not bool or type(menu['desktop']) is not int or not 0 <= menu['desktop'] <= 2 \
            or menu['field'] not in PLACEMENT_KEYS:
        raise ValueError('invalid menu')
    payload = {'open': menu['open'], 'desktop': menu['desktop'], 'field': menu['field'],
               'placements': validate_placements(menu['placements'])}
    return (json.dumps({'schema': 1, 'request_id': request_id, 'run_id': run_id, 'session': session, 'op': 'placement_menu',
                        'payload': payload}, separators=(',', ':')) + '\n').encode()


def menu_pointer_frame(run_id, session, request_id, pointer):
    """Mouse-mode pointer for the renderer: {visible, x, y, held}, x/y in menu-card pixels (spike/src/ipc.cpp checks the same)."""
    if not isinstance(pointer, dict) or set(pointer) != {'visible', 'x', 'y', 'held'} or type(pointer['visible']) is not bool \
            or type(pointer['held']) is not bool or any(type(pointer[k]) is not int or not 0 <= pointer[k] <= MAX_POINTER_PX for k in 'xy'):
        raise ValueError('invalid menu pointer')
    return (json.dumps({'schema': 1, 'request_id': request_id, 'run_id': run_id, 'session': session, 'op': 'menu_pointer',
                        'payload': dict(pointer)}, separators=(',', ':')) + '\n').encode()


def parse_menu_geometry(value):
    """The renderer's `menu_geometry` status line (spike/src/hud.cpp menu_geometry_json) -> a validated dict:
    {'w', 'h', 'tabs': [(x0, y0, x1, y1)] * 3, 'sliders': [(field index, top, bottom, lo_x, hi_x), ...]}. ValueError if malformed."""
    from .geometry import PLACEMENT_KEYS
    def ints(seq, n):
        if not isinstance(seq, list) or len(seq) != n or any(type(v) is not int or not -MAX_POINTER_PX <= v <= MAX_POINTER_PX for v in seq):
            raise ValueError('invalid menu geometry')
        return tuple(seq)
    if not isinstance(value, dict) or set(value) != {'w', 'h', 'tabs', 'sliders'}: raise ValueError('invalid menu geometry')
    w, h = value['w'], value['h']
    if type(w) is not int or type(h) is not int or not 0 < w <= MAX_POINTER_PX or not 0 < h <= MAX_POINTER_PX:
        raise ValueError('invalid menu geometry')
    if not isinstance(value['tabs'], list) or len(value['tabs']) != 3 or not isinstance(value['sliders'], list) \
            or not 1 <= len(value['sliders']) <= len(PLACEMENT_KEYS):
        raise ValueError('invalid menu geometry')
    tabs = [ints(t, 4) for t in value['tabs']]
    sliders = [ints(sl, 5) for sl in value['sliders']]
    fields = [sl[0] for sl in sliders]
    if any(not x0 < x1 or not y0 < y1 for x0, y0, x1, y1 in tabs) or len(set(fields)) != len(fields) \
            or any(not 0 <= f < len(PLACEMENT_KEYS) or not top < bottom or not lo < hi for f, top, bottom, lo, hi in sliders):
        raise ValueError('invalid menu geometry')
    return {'w': w, 'h': h, 'tabs': tabs, 'sliders': sliders}


def warp_cursor_lua(x, y):
    """Put the (hidden) cursor at layout pixel (x, y): mouse mode re-centres it so it never stops at a screen edge."""
    if any(type(v) is not int or not -1_000_000 <= v <= 1_000_000 for v in (x, y)): raise ValueError('invalid cursor position')
    return f'pcall(function() hl.dispatch(hl.dsp.cursor.move({{ x = {x}, y = {y} }})) end)'


class ViewState:
    def __init__(self, names, spacing=DEFAULT_SPACING, mode='anchor', cursor_follow=False, prediction=True, placements=None):
        if not names or len(names) > 3 or any(not MANAGED.match(n) for n in names): raise ValueError('managed outputs required')
        if mode not in MODES or type(cursor_follow) is not bool or type(prediction) is not bool: raise ValueError('invalid view mode')
        self.names, self.zoomed, self.selected = list(names), False, 0
        self.spacing = min(max(int(spacing), 0), MAX_SPACING)
        self.mode, self.cursor_follow, self.prediction = mode, cursor_follow, prediction
        from .geometry import DEFAULT_PLACEMENT, validate_placement
        # One per managed output, in order; a saved profile always holds three (extra entries are ignored).
        placements = list(placements or [])[:len(self.names)]
        self.placements = [validate_placement(p) for p in placements] + \
            [dict(DEFAULT_PLACEMENT) for _ in range(len(self.names) - len(placements))]
        # Placement menu; never saved, starts closed. `menu_desktop` indexes self.names.
        self.menu_open, self.menu_desktop, self.menu_field = False, 0, 'x'
        # Mouse mode pointer {'x', 'y' (card pixels, float), 'held': field key or None, 'placed': bool}, or None when off;
        # only while open. `geometry` is the renderer's menu layout (parse_menu_geometry), None until it reports one.
        self.mouse, self.geometry = None, None

    def menu(self):
        """`placement_menu` payload: always three placements (desktops beyond this run's outputs are shown flat)."""
        from .geometry import DEFAULT_PLACEMENT
        placements = [dict(p) for p in self.placements] + [dict(DEFAULT_PLACEMENT) for _ in range(3 - len(self.placements))]
        return {'open': self.menu_open, 'desktop': self.menu_desktop, 'field': self.menu_field, 'placements': placements}

    def pointer(self):
        """`menu_pointer` payload. Shown once mouse mode is on and the card's layout is known."""
        m = self.mouse
        if m is None or not m['placed']: return {'visible': False, 'x': 0, 'y': 0, 'held': False}
        return {'visible': True, 'x': int(math.floor(m['x'] + 0.5)), 'y': int(math.floor(m['y'] + 0.5)), 'held': m['held'] is not None}

    def _slider(self, field):
        from .geometry import PLACEMENT_KEYS
        if self.geometry is None: return None
        return next((sl for sl in self.geometry['sliders'] if sl[0] == PLACEMENT_KEYS.index(field)), None)

    def _knob_x(self, slider, field):
        from .geometry import PLACEMENT_BOUNDS
        lo, hi = PLACEMENT_BOUNDS[field]
        return slider[3] + (slider[4] - slider[3]) * (self.placements[self.menu_desktop][field] - lo) / (hi - lo)

    def _place_pointer(self):
        """First position once the layout is known: on the selected setting's knob, else the middle of the card."""
        g, m = self.geometry, self.mouse
        if g is None or m is None or m['placed']: return
        slider = self._slider(self.menu_field)
        if slider is not None: m['x'], m['y'] = self._knob_x(slider, self.menu_field), (slider[1] + slider[2]) / 2
        else: m['x'], m['y'] = g['w'] / 2, g['h'] / 2
        m['placed'] = True

    def set_geometry(self, geometry):
        """The renderer laid the card out (again). The pointer is placed or kept inside the card. True if it moved."""
        before = self.pointer()
        self.geometry = geometry
        if self.mouse is not None:
            self._place_pointer()
            self.mouse['x'] = min(max(self.mouse['x'], 0.0), geometry['w'] - 1.0)
            self.mouse['y'] = min(max(self.mouse['y'], 0.0), geometry['h'] - 1.0)
            if self.mouse['held'] is not None and self._slider(self.mouse['held']) is None: self.mouse['held'] = None
        return self.pointer() != before

    def _set_from_pointer(self):
        """A held slider takes the value under the pointer (clamped to its bounds). True if a placement changed."""
        from .geometry import PLACEMENT_BOUNDS
        field = self.mouse['held']
        slider = self._slider(field)
        if slider is None: return False
        lo, hi = PLACEMENT_BOUNDS[field]
        t = min(max((self.mouse['x'] - slider[3]) / (slider[4] - slider[3]), 0.0), 1.0)
        value = lo + int(math.floor(t * (hi - lo) + 0.5))
        current = self.placements[self.menu_desktop]
        if current[field] == value: return False
        self.placements = [{**current, field: value} if i == self.menu_desktop else p for i, p in enumerate(self.placements)]
        return True

    def mouse_move(self, dx, dy):
        """Cursor motion in mouse mode (Hyprland layout pixels): the pointer moves freely over the card. True if a held
        slider changed a placement."""
        m, g = self.mouse, self.geometry
        if m is None or g is None or not m['placed']: return False
        m['x'] = min(max(m['x'] + dx * MOUSE_GAIN, 0.0), g['w'] - 1.0)
        m['y'] = min(max(m['y'] + dy * MOUSE_GAIN, 0.0), g['h'] - 1.0)
        return self._set_from_pointer() if m['held'] is not None else False

    def _press(self):
        """Left button down: the slider under the pointer snaps to it and is held; a desktop tab is selected; elsewhere
        nothing happens. True if a placement changed."""
        from .geometry import PLACEMENT_KEYS
        m, g = self.mouse, self.geometry
        if m is None or g is None or not m['placed']: return False
        x, y = m['x'], m['y']
        for field, top, bottom, lo_x, hi_x in g['sliders']:
            if top <= y < bottom and lo_x - MOUSE_SLOP_PX <= x <= hi_x + MOUSE_SLOP_PX:
                self.menu_field = m['held'] = PLACEMENT_KEYS[field]
                return self._set_from_pointer()
        for desktop, (x0, y0, x1, y1) in enumerate(g['tabs']):
            if x0 <= x < x1 and y0 <= y < y1 and desktop < len(self.names): self.menu_desktop = desktop
        return False

    def _mouse_off(self):
        self.mouse = None

    def apply_menu(self, op):
        """Update menu state and, for adjust/reset ops, the selected desktop's placement (clamped). True if placements changed.

        Only menu_toggle acts while the menu is closed (its other binds exist only while open; a late key is ignored).
        Mouse ops (press/release) act only in mouse mode.
        """
        from .geometry import DEFAULT_PLACEMENT, PLACEMENT_BOUNDS, PLACEMENT_KEYS
        if op not in MENU_OPS + MOUSE_OPS: raise ValueError('unsupported menu operation')
        if op == 'menu_back' and self.mouse is not None: op = 'menu_mouse'  # Escape leaves mouse mode first
        elif op == 'menu_back': op = 'menu_toggle' if self.menu_open else None
        if op == 'menu_toggle':
            self.menu_open = not self.menu_open
            if self.menu_open: self.menu_desktop = self.selected  # open on the desktop being looked at
            else: self._mouse_off()
            return False
        if not self.menu_open: return False
        if op == 'menu_mouse':
            if self.mouse is not None: self._mouse_off()
            else:  # the pointer starts on the selected setting's knob (once the layout is known)
                self.mouse = {'x': 0.0, 'y': 0.0, 'held': None, 'placed': False}
                self._place_pointer()
            return False
        if op in MOUSE_OPS:
            if self.mouse is None: return False
            if op == 'menu_mouse_release':
                self.mouse['held'] = None
                return False
            return self._press()
        if op in ('menu_desktop_next', 'menu_desktop_prev'):
            self.menu_desktop = (self.menu_desktop + (1 if op == 'menu_desktop_next' else -1)) % len(self.names)
            return False
        if op in ('menu_field_next', 'menu_field_prev'):
            i = PLACEMENT_KEYS.index(self.menu_field) + (1 if op == 'menu_field_next' else -1)
            self.menu_field = PLACEMENT_KEYS[i % len(PLACEMENT_KEYS)]
            return False
        current = self.placements[self.menu_desktop]
        if op == 'menu_reset':
            updated = dict(DEFAULT_PLACEMENT)
        else:
            small, big = MENU_STEPS[self.menu_field]
            step = (big if op.endswith('_big') else small) * (1 if op.startswith('menu_inc') else -1)
            lo, hi = PLACEMENT_BOUNDS[self.menu_field]
            updated = {**current, self.menu_field: min(max(current[self.menu_field] + step, lo), hi)}
        if updated == current: return False
        self.placements = [updated if i == self.menu_desktop else p for i, p in enumerate(self.placements)]
        return True

    def set_placements(self, placements):
        """Absolute placements, one per managed output. True if anything changed."""
        from .geometry import validate_placement
        if not isinstance(placements, list) or len(placements) != len(self.names): raise ValueError('one placement per desktop required')
        placements = [validate_placement(p) for p in placements]
        if placements == self.placements: return False
        self.placements = placements
        return True

    def view(self):
        """Renderer payload. Cursor follow is acted on supervisor-side; the renderer only displays it."""
        return {'zoomed': self.zoomed, 'selected': self.selected, 'spacing': self.spacing, 'mode': self.mode,
                'cursor_follow': self.cursor_follow, 'prediction': self.prediction}

    def settings(self):
        return {'spacing': self.spacing, 'mode': self.mode, 'cursor_follow': self.cursor_follow, 'prediction': self.prediction}

    def cursor_entered(self, name):
        """Pointer focus moved to `name`; with cursor follow on, select it. True if the view changed."""
        if not self.cursor_follow or name not in self.names: return False
        index = self.names.index(name)
        if index == self.selected: return False
        self.selected = index
        return True

    def apply(self, op):
        """Update state; return the output to focus, or None."""
        if op not in USER_OPS: raise ValueError('unsupported view operation')
        if op in ('recenter', 'help'): return None  # head-tracking reference / overlay only; the view layout is unchanged
        if op == 'mode_next':
            self.mode = MODES[(MODES.index(self.mode) + 1) % len(MODES)]
            return None
        if op == 'cursor_follow':
            self.cursor_follow = not self.cursor_follow
            return None
        if op == 'prediction':
            self.prediction = not self.prediction
            return None
        if op == 'zoom_in':
            self.zoomed = True
            return self.names[self.selected]
        if op == 'zoom_out':
            self.zoomed = False
            return None
        if op in ('next', 'prev'):
            target = self.selected + (1 if op == 'next' else -1)
            if not 0 <= target < len(self.names): return None
            self.selected = target
            return self.names[target]  # overview and zoomed: highlight/slide and focus
        self.spacing = min(max(self.spacing + (SPACING_STEP if op == 'spacing_up' else -SPACING_STEP), 0), MAX_SPACING)
        return None


def settings_path():
    base = os.environ.get('XDG_CONFIG_HOME') or str(Path.home() / '.config')
    return Path(base) / 'viture-workspace' / 'layout.json'


DEFAULT_SETTINGS = {'spacing': DEFAULT_SPACING, 'mode': 'anchor', 'cursor_follow': False, 'prediction': True}


def load_settings(path):
    """Each field validated on its own; anything invalid or missing falls back to its default."""
    out = dict(DEFAULT_SETTINGS)
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return out
    if not isinstance(data, dict): return out
    if type(data.get('spacing')) is int and 0 <= data['spacing'] <= MAX_SPACING: out['spacing'] = data['spacing']
    if data.get('mode') in MODES: out['mode'] = data['mode']
    if type(data.get('cursor_follow')) is bool: out['cursor_follow'] = data['cursor_follow']
    if type(data.get('prediction')) is bool: out['prediction'] = data['prediction']
    return out


def save_settings(path, settings):
    if not isinstance(settings, dict) or set(settings) != set(DEFAULT_SETTINGS) or not _valid(settings):
        raise ValueError('invalid settings')
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(path.name + '.tmp')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as f: json.dump(settings, f)
    os.replace(tmp, path)


def _valid(settings):
    return (type(settings.get('spacing')) is int and 0 <= settings['spacing'] <= MAX_SPACING and settings.get('mode') in MODES
            and type(settings.get('cursor_follow')) is bool and type(settings.get('prediction')) is bool)


def load_spacing(path):
    return load_settings(path)['spacing']


def save_spacing(path, spacing):
    if type(spacing) is not int or not 0 <= spacing <= MAX_SPACING: raise ValueError('invalid spacing')
    save_settings(path, {**load_settings(path), 'spacing': spacing})


def view_frame(run_id, session, request_id, state):
    return (json.dumps({'schema': 1, 'request_id': request_id, 'run_id': run_id, 'session': session, 'op': 'view',
                        'payload': state}, separators=(',', ':')) + '\n').encode()


def focus_command(name):
    if not isinstance(name, str) or not MANAGED.match(name): raise ValueError('focus is limited to managed outputs')
    return f'dispatch hl.dsp.focus({{monitor="{name}"}})'


SESSION_MODE = (1920, 1200, 120)  # user grant: session-only Luma/Beast mode on the approved output
PHYSICAL = re.compile(r'(?:DP|HDMI-A|eDP)-[0-9]+\Z')


def session_mode(monitor):
    """The approved output's placement if it is in the granted session mode, else None."""
    if not isinstance(monitor, dict) or not PHYSICAL.match(str(monitor.get('name', ''))): return None
    if (monitor.get('width'), monitor.get('height')) != SESSION_MODE[:2]: return None
    if not isinstance(monitor.get('refreshRate'), (int, float)) or abs(monitor['refreshRate'] - SESSION_MODE[2]) > 0.5: return None
    x, y, scale = monitor.get('x'), monitor.get('y'), monitor.get('scale')
    if type(x) is not int or type(y) is not int or not isinstance(scale, (int, float)) or not 0.25 <= scale <= 4: return None
    return {'name': monitor['name'], 'x': x, 'y': y, 'scale': float(scale)}


def restore_mode_lua(mode):
    """Re-apply the granted session mode after a config reload reset it (fixed shape, validated)."""
    if session_mode({**mode, 'width': SESSION_MODE[0], 'height': SESSION_MODE[1], 'refreshRate': SESSION_MODE[2]}) != mode:
        raise ValueError('invalid session mode')
    w, h, r = SESSION_MODE
    return (f'hl.monitor({{ output = "{mode["name"]}", mode = "{w}x{h}@{r}", position = "{mode["x"]}x{mode["y"]}", '
            f'scale = {mode["scale"]:g} }})')


def install_binds_lua(python, client, control_socket, run_id, session):
    for p in (python, client, control_socket):
        if not isinstance(p, str) or not SAFE_PATH.match(p): raise ValueError('unsafe path for bind command')
    for t in (run_id, session):
        if not isinstance(t, str) or not SAFE_TOKEN.match(t): raise ValueError('unsafe token for bind command')
    return _binds_lua('xr_workspace_binds', BINDS, f'{python} -I {client} {control_socket} {run_id} {session}')


def _binds_lua(table, binds, base):
    # The shared command prefix is a Lua local: one copy per eval keeps the chunk well under the 4096-character limit.
    calls = ', '.join(f'hl.bind("{chord}", hl.dsp.exec_cmd(c .. "{op}"){", { release = true }" if op.endswith("_release") else ""})'
                      for chord, op in binds)
    return f'local c = "{base} " {table} = {{ {calls} }}'


def _remove_lua(table):
    # Hyprland 0.56: remove() on an expired handle (after unbind/reload) segfaults; probe first.
    return (f'for _, b in ipairs({table} or {{}}) do '
            f'if tostring(b) ~= "HL.Keybind(expired)" then b:remove() end end {table} = nil')


def remove_binds_lua():
    """Run end / reinstall: the view binds, the send-window binds and any open menu's modal binds."""
    return _remove_lua('xr_workspace_binds') + ' ' + remove_send_binds_lua() + ' ' + remove_menu_binds_lua()


def install_send_binds_lua(python, client, control_socket, run_id, session):
    """Send-window binds (send.SEND_BINDS); a separate table and eval so the view binds' reinstall stays one eval."""
    from .send import SEND_BINDS
    for p in (python, client, control_socket):
        if not isinstance(p, str) or not SAFE_PATH.match(p): raise ValueError('unsafe path for bind command')
    for t in (run_id, session):
        if not isinstance(t, str) or not SAFE_TOKEN.match(t): raise ValueError('unsafe token for bind command')
    return remove_send_binds_lua() + ' ' + _binds_lua('xr_workspace_send_binds', SEND_BINDS,
                                                      f'{python} -I {client} {control_socket} {run_id} {session}')


def remove_send_binds_lua():
    return _remove_lua('xr_workspace_send_binds')


def install_menu_binds_lua(python, client, control_socket, run_id, session):
    """Modal placement-menu binds (bare arrows, Tab, R, Escape); replaces any surviving set, so never doubled."""
    for p in (python, client, control_socket):
        if not isinstance(p, str) or not SAFE_PATH.match(p): raise ValueError('unsafe path for bind command')
    for t in (run_id, session):
        if not isinstance(t, str) or not SAFE_TOKEN.match(t): raise ValueError('unsafe token for bind command')
    return remove_menu_binds_lua() + ' ' + _binds_lua('xr_workspace_menu_binds', MENU_BINDS,
                                                      f'{python} -I {client} {control_socket} {run_id} {session}')


def remove_menu_binds_lua():
    """Menu closed, run end, or (xr_control.py) a menu key with no run listening: also leaves mouse mode."""
    return _remove_lua('xr_workspace_menu_binds') + ' ' + remove_mouse_binds_lua()


# Mouse mode, entering: remember whether the cursor was hidden and where it was (only the first time, so a reinstall
# after a config reload keeps the original), move it to the middle of its monitor so it has room to move in every
# direction, and hide it. Every compositor call is wrapped in pcall: a refused cursor change never blocks the binds.
_MOUSE_ENTER_LUA = ('if xr_workspace_mouse == nil then '
                    'local ok, hidden = pcall(hl.get_config, "cursor.invisible") local p = hl.get_cursor_pos() '
                    'xr_workspace_mouse = { invisible = ok and hidden == true, home = p and { x = p.x, y = p.y } } '
                    'local m = hl.get_monitor_at_cursor() '
                    'if m then pcall(function() hl.dispatch(hl.dsp.cursor.move({ x = m.x + m.width / m.scale / 2, '
                    'y = m.y + m.height / m.scale / 2 })) end) end end '
                    'pcall(hl.config, { cursor = { invisible = true } })')
# Leaving: the cursor's own visibility back, and the cursor back where mouse mode found it.
_MOUSE_LEAVE_LUA = ('do local s = xr_workspace_mouse xr_workspace_mouse = nil if s then '
                    'pcall(hl.config, { cursor = { invisible = s.invisible } }) '
                    'if s.home then pcall(function() hl.dispatch(hl.dsp.cursor.move({ x = s.home.x, y = s.home.y })) end) end '
                    'end end')


def install_mouse_binds_lua(python, client, control_socket, run_id, session):
    """Mouse mode on: hide the cursor and bind the left button (press and release) to the menu."""
    for p in (python, client, control_socket):
        if not isinstance(p, str) or not SAFE_PATH.match(p): raise ValueError('unsafe path for bind command')
    for t in (run_id, session):
        if not isinstance(t, str) or not SAFE_TOKEN.match(t): raise ValueError('unsafe token for bind command')
    return (_remove_lua('xr_workspace_mouse_binds') + ' ' + _MOUSE_ENTER_LUA + ' ' +
            _binds_lua('xr_workspace_mouse_binds', MOUSE_BINDS, f'{python} -I {client} {control_socket} {run_id} {session}'))


def remove_mouse_binds_lua():
    return _remove_lua('xr_workspace_mouse_binds') + ' ' + _MOUSE_LEAVE_LUA


class ViewControl:
    """Supervisor-side handler: user op -> state, async focus, saved spacing, renderer view frame."""
    def __init__(self, state, run_id, session, focus, settings, server, first_request_id=2, border=None, on_reload=None,
                 placements=None, menu_binds=None, clock=time.monotonic, mouse_binds=None, cursor=None, warp=None):
        self.state, self.run_id, self.session = state, run_id, session
        self.focus, self.settings, self.server = focus, settings, server
        self.next_id = first_request_id
        self._threads = []
        self.events = None       # optional Hyprland event socket (cursor follow); owned by the caller
        self._event_buffer = b''
        self.cursor_switches = 0
        self.border = border     # optional () -> Hyprland getoption reply for general:col.active_border
        self.accent = None       # last theme colour sent to the renderer
        self.on_reload = on_reload  # optional () -> None, run off-loop after Hyprland config reloads
        self.reloads = 0
        self.help_visible = False  # Ctrl+Alt+H card; never saved, starts hidden each run
        self.video_active = False  # a VR video plays in place of the desktops (video.VideoControl sets it)
        self.last_input = clock()  # monotonic time of the last run key / panel change (overlay idle close)
        self.placements = placements  # optional store with save(list of 3); see profiles.PlacementStore
        self.menu_binds = menu_binds  # optional (open: bool) -> None: install/remove the modal menu binds (compositor call)
        self.clock = clock
        self.save_due = None          # monotonic deadline of a pending debounced placement save
        self._menu_lock = threading.Lock()
        self._menu_binds_installed = False
        self._menu_threads = []
        self.resize = None  # optional resize.CornerResize (overview corner drag); set by managed.view_controls
        self.wake = None    # optional socket the supervisor loop also waits on (the resize pointer poller pokes it)
        self.mouse_binds = mouse_binds  # optional (on: bool) -> None: mouse mode's click binds and hidden cursor
        self.cursor = cursor            # optional () -> Hyprland cursorpos reply {'x', 'y'}; sampled off-loop in mouse mode
        self.warp = warp                # optional (x, y) -> None: put the hidden cursor back (warp_cursor_lua), off-loop
        self._mouse_binds_installed = False
        self._mouse_forced_at = None    # last reinstall after a config reload (rate limited)
        self._motion_lock = threading.Lock()
        self._motion = [0.0, 0.0]       # cursor motion the sampler has seen and the loop has not applied yet
        self._rebase = False            # set once the mouse binds (and the cursor warp) landed: next sample is the baseline
        self._sampler = None
        self._sampling = threading.Event()
        self._pointer_sent = self.state.pointer()  # last menu_pointer payload sent to the renderer
        # The sampler wakes the supervisor loop through this socket (supervisor.py waits on `mouse_wake`), so a mouse
        # movement reaches the renderer within a sample, not at the loop's next 50 ms tick.
        self.mouse_wake, self._mouse_wake_writer = socket.socketpair()
        self.mouse_wake.setblocking(False); self._mouse_wake_writer.setblocking(False)
        self.mouse_moves = 0            # diagnostic: loop ticks that moved the pointer
        self.recentres = 0              # diagnostic: hidden-cursor re-centrings
        self.sender = None              # optional send.WindowSender (Ctrl+Alt+G/1-3/B); set by managed.view_controls

    def initial_frame(self):
        return self._frame() + self._placement_frame(initial=True) + self._style_frame()

    def _placement_frame(self, initial=False):
        from .geometry import DEFAULT_PLACEMENT
        # The renderer starts flat; an all-default start needs no frame.
        if initial and all(p == DEFAULT_PLACEMENT for p in self.state.placements): return b''
        frame = placement_frame(self.run_id, self.session, self.next_id, self.state.placements)
        self.next_id += 1
        return frame

    def _style_frame(self):
        """Theme border colour for the highlight; empty if unknown or unchanged. Never fatal."""
        if self.border is None: return b''
        try: rgb = parse_border(self.border())
        except (OSError, ValueError, TypeError, KeyError): rgb = None  # incl. clients without the option
        if rgb is None or rgb == self.accent: return b''
        self.accent = rgb
        frame = style_frame(self.run_id, self.session, self.next_id, rgb)
        self.next_id += 1
        return frame

    def _frame(self):
        frame = view_frame(self.run_id, self.session, self.next_id, self.state.view())
        self.next_id += 1
        return frame

    def status_reply(self, status):
        return dict(capabilities=list(USER_OPS), view=self.state.view(), status=dict(status),
                    placements=[dict(p) for p in self.state.placements])

    def _menu_frame(self):
        frame = placement_menu_frame(self.run_id, self.session, self.next_id, self.state.menu())
        self.next_id += 1
        return frame

    def _on_menu(self, op):
        """Menu op: live placement frame first (if changed), then the overlay frame (if anything changed)."""
        before = self.state.menu()
        was_open, was_mouse = self.state.menu_open, self.state.mouse is not None
        changed = self.state.apply_menu(op)
        out = b''
        if changed:
            out += self._placement_frame()
            self.save_due = self.clock() + MENU_SAVE_DELAY
        if was_open and not self.state.menu_open: self.flush()  # closing saves at once
        if (was_open, was_mouse) != (self.state.menu_open, self.state.mouse is not None):
            if self.state.mouse is None: self._sampling.clear()
            self._sync_menu_binds()
        if self.state.menu() != before: out += self._menu_frame()
        out += self._pointer_frame()
        return out or None

    def _pointer_frame(self):
        """A menu_pointer frame if the pointer changed since the last one sent (moves, shows, hides, holds)."""
        pointer = self.state.pointer()
        if pointer == self._pointer_sent: return b''
        self._pointer_sent = pointer
        frame = menu_pointer_frame(self.run_id, self.session, self.next_id, pointer)
        self.next_id += 1
        return frame

    def set_menu_geometry(self, geometry):
        """Supervisor: the renderer's menu layout (parse_menu_geometry). Places or clamps the pointer; the loop sends it."""
        if self.state.set_geometry(geometry): self._poke()

    def _poke(self):
        try: self._mouse_wake_writer.send(b'.')
        except OSError: pass  # full or closed: the loop's own tick picks it up

    def drain_mouse_wake(self):
        try:
            while self.mouse_wake.recv(4096): pass
        except OSError: pass

    def _take_motion(self):
        with self._motion_lock:
            (dx, dy), self._motion = self._motion, [0.0, 0.0]
        return dx, dy

    def _mouse_tick(self, now):
        """Supervisor loop: apply the motion gathered since the last tick. A held slider follows the pointer live."""
        dx, dy = self._take_motion()
        if not dx and not dy: return b''
        self.last_input = now  # mouse motion keeps the menu up
        self.mouse_moves += 1
        before = self.state.menu()
        out = b''
        if self.state.mouse_move(dx, dy):
            out += self._placement_frame()
            self.save_due = now + MENU_SAVE_DELAY
        if self.state.menu() != before: out += self._menu_frame()
        return out

    def _start_sampling(self):
        self._rebase = True
        if self.cursor is None: return
        self._sampling.set()
        if self._sampler is None or not self._sampler.is_alive():
            self._sampler = threading.Thread(target=self._sample_cursor, daemon=True); self._sampler.start()

    def _sample_cursor(self):
        """Off-loop: read the cursor every MOUSE_SAMPLE_S while mouse mode is on (a read-only query), add up its motion,
        and wake the loop. The hidden cursor is put back where sampling started once it strays MOUSE_RECENTRE_PX, so a
        screen edge never stops the pointer."""
        last = home = None
        while self.cursor is not None:
            if not self._sampling.wait(0.5):
                last = home = None
                continue
            cursor = self.cursor
            if cursor is None: break
            if self._rebase: self._rebase, last, home = False, None, None
            try:
                reply = cursor()
                x, y = reply['x'], reply['y']
                if not (isinstance(x, (int, float)) and isinstance(y, (int, float)) and math.isfinite(x) and math.isfinite(y)):
                    raise ValueError('cursor position')
                x, y = float(x), float(y)
            except (OSError, ValueError, TypeError, KeyError):
                time.sleep(MOUSE_SAMPLE_S)
                continue  # a missed sample just means no motion this time
            if last is None: last = home = (x, y)
            dx, dy = x - last[0], y - last[1]
            last = (x, y)
            if (dx or dy) and abs(dx) <= MOUSE_JUMP_PX and abs(dy) <= MOUSE_JUMP_PX:  # larger: a warp, not a hand
                with self._motion_lock:
                    self._motion[0] += dx; self._motion[1] += dy
                self._poke()
            warp = self.warp
            if warp is not None and math.hypot(x - home[0], y - home[1]) > MOUSE_RECENTRE_PX:
                try:
                    warp(int(home[0]), int(home[1]))
                    last = home; self.recentres += 1
                except (OSError, ValueError, TypeError, KeyError): home = last  # could not move it: measure from here
            time.sleep(MOUSE_SAMPLE_S)

    def _sync_menu_binds(self, force=False):
        if self.menu_binds is None and self.mouse_binds is None:
            if self.state.mouse is not None: self._start_sampling()
            return
        # Compositor call: never on the watchdog loop. The worker applies the state current when it runs, serialized,
        # so a fast open/close/open cannot leave the binds out of step with the menu.
        t = threading.Thread(target=self._menu_bind_work, args=(force,), daemon=True); t.start()
        self._threads.append(t); self._menu_threads.append(t)

    def _menu_bind_work(self, force, mouse_allowed=True):
        with self._menu_lock:
            binds, want = self.menu_binds, self.state.menu_open
            mouse, want_mouse = self.mouse_binds, self.state.mouse is not None
            # Leaving mouse mode first (cursor back), then the menu keys; entering in the opposite order.
            if mouse is not None and not want_mouse and self._mouse_binds_installed:
                try: mouse(False)
                except (OSError, ValueError, TypeError, KeyError): pass  # the menu's own removal also leaves mouse mode
                self._mouse_binds_installed = False
            if binds is not None and (want != self._menu_binds_installed or (force and want)):
                try:
                    binds(want)
                    self._menu_binds_installed = want
                    if not want: self._mouse_binds_installed = False  # remove_menu_binds_lua leaves mouse mode too
                except (OSError, ValueError, TypeError, KeyError): pass  # best-effort; Ctrl+Alt+L still closes the menu
            if want_mouse and mouse_allowed and self.state.mouse is not None and (not self._mouse_binds_installed or force):
                if mouse is not None:
                    try:
                        mouse(True)
                        self._mouse_binds_installed = True
                    except (OSError, ValueError, TypeError, KeyError): pass  # the pointer still moves; Esc still leaves
                if self.state.mouse is not None: self._start_sampling()  # after the cursor moved to its start point

    def poll(self, now=None):
        """Supervisor loop tick: write a debounced menu placement save once it is due, and close the help card and the
        placement menu once they have been up OVERLAY_IDLE_CLOSE_S without input. Returns renderer frames or None."""
        now = self.clock() if now is None else now
        out = self.resize.poll(now) if self.resize is not None else b''  # pointer hover / corner drag
        out += self._mouse_tick(now) if self.state.mouse is not None else b''
        if self.save_due is not None and now >= self.save_due: self.flush()
        if now - self.last_input >= OVERLAY_IDLE_CLOSE_S:
            if self.state.menu_open: out += self._on_menu('menu_toggle') or b''  # saves at once and releases the menu keys
            if self.help_visible: out += self._help_toggle()
        out += self._pointer_frame()  # at most one per tick: the latest position
        return out or None

    def _help_toggle(self):
        self.help_visible = not self.help_visible
        frame = help_frame(self.run_id, self.session, self.next_id, self.help_visible)
        self.next_id += 1
        return frame

    def flush(self):
        """Write any pending menu placement save now (menu close, run end)."""
        if self.save_due is None: return
        self.save_due = None
        self._save_placements()

    def _save_placements(self):
        if self.placements is None: return
        from .geometry import DEFAULT_PLACEMENT
        try:  # a profile always holds three; keep any desktops beyond this run's outputs unchanged
            saved = list(self.placements.load())
            full = self.state.placements + saved[len(self.state.placements):]
            full += [dict(DEFAULT_PLACEMENT)] * (3 - len(full))
            self.placements.save(full[:3])
        except (OSError, ValueError): pass

    def on_message(self, message):
        if isinstance(message, dict) and isinstance(message.get('op'), str):
            self.last_input = self.clock()  # any run key or panel change keeps an open card up
        if isinstance(message, dict) and message.get('op') == 'placement':
            return self._on_placement(message.get('payload'))
        if isinstance(message, dict) and message.get('op') in MENU_OPS + MOUSE_OPS:
            return self._on_menu(message['op'])
        if isinstance(message, dict) and message.get('op') in ('resize_press', 'resize_release'):  # resize.RESIZE_OPS
            return self.resize.on_op(message['op']) if self.resize is not None else None
        if isinstance(message, dict) and message.get('op') in SEND_OPS:  # the move itself runs off the loop
            if self.sender is not None: self.sender.request(message['op'], self.state.names[self.state.selected])
            return None
        if not isinstance(message, dict) or message.get('op') not in USER_OPS: return None
        if message['op'] == 'help': return self._help_toggle()
        if message['op'] == 'recenter':
            frame = (json.dumps({'schema': 1, 'request_id': self.next_id, 'run_id': self.run_id, 'session': self.session,
                                 'op': 'recenter', 'payload': {}}, separators=(',', ':')) + '\n').encode()
            self.next_id += 1
            return frame
        before = self.state.view()
        target = self.state.apply(message['op'])
        if target is not None:
            import threading
            # Never block the watchdog loop on a compositor reply.
            t = threading.Thread(target=self._focus, args=(target,), daemon=True); t.start(); self._threads.append(t)
        if message['op'] in ('spacing_up', 'spacing_down', 'mode_next', 'cursor_follow', 'prediction'):
            try: self.settings.save(self.state.settings())  # a settings store (profiles.LayoutStore/ProfileStore)
            except (OSError, ValueError): pass
        return self._frame() if self.state.view() != before else None

    def _on_placement(self, payload):
        """Absolute placements from the panel; applied live and saved to the glasses profile. Invalid: ignored."""
        try:
            if not isinstance(payload, dict) or not self.state.set_placements(payload.get('desktops')): return None
        except ValueError:
            return None
        self.save_due = None  # this save also covers any pending menu change
        self._save_placements()
        # An open menu shows the panel's values too.
        return self._placement_frame() + (self._menu_frame() if self.state.menu_open else b'')

    def on_events(self, data):
        """Hyprland socket2 bytes; returns a renderer frame if cursor follow switched the view."""
        self._event_buffer += data
        if len(self._event_buffer) > 65536: self._event_buffer = self._event_buffer[-4096:]  # bounded; resync on newline
        *lines, self._event_buffer = self._event_buffer.split(b'\n')
        changed = reloaded = False
        for line in lines:
            text = line.decode('utf-8', errors='replace')
            m = FOCUSEDMON.match(text)
            if m and self.state.mouse is None and self.state.cursor_entered(m.group(1)):  # paused in mouse mode
                changed = True; self.cursor_switches += 1
            if CONFIGRELOADED.match(text): reloaded = True
        if reloaded:
            self.reloads += 1
            self._menu_binds_installed = False  # a reload drops runtime binds; the reload work re-adds an open menu's
            if self.resize is not None: self.resize.binds_dropped = True  # the pointer poller re-adds them if still wanted
            self._mouse_binds_installed = False
            if self.on_reload is not None:  # binds and session mode: compositor calls, never on the watchdog loop
                import threading
                t = threading.Thread(target=self._reload_work, daemon=True); t.start(); self._threads.append(t)
        out = (self._frame() if changed else b'') + (self._style_frame() if reloaded else b'')
        return out or None

    def _reload_work(self):
        try: self.on_reload()
        except (OSError, ValueError, TypeError, KeyError): pass  # best-effort; the run itself is unaffected
        # After the view binds: re-add the modal menu binds if the menu is open, and mouse mode's. Should hiding the
        # cursor ever itself count as a config reload, the rate limit stops a reinstall loop (mouse mode then needs
        # Ctrl+Alt+M twice).
        now = time.monotonic()
        mouse = self._mouse_forced_at is None or now - self._mouse_forced_at >= 2.0
        if mouse and self.state.mouse is not None: self._mouse_forced_at = now
        self._menu_bind_work(force=True, mouse_allowed=mouse)

    def _focus(self, target):
        try: self.focus(target)
        except (OSError, ValueError): pass  # focus is best-effort; the view still changes

    def stop_menu_binds(self, timeout=2.5):
        """Run end: no later menu bind change, and wait (bounded in total) for any in flight, so none outlives the run."""
        self.menu_binds = self.mouse_binds = self.cursor = self.warp = None
        self._sampling.set()  # wake the cursor sampler so it sees cursor=None and ends
        deadline = time.monotonic() + timeout
        for t in self._menu_threads + ([self._sampler] if self._sampler else []): t.join(max(0.0, deadline - time.monotonic()))
        for sock in (self.mouse_wake, self._mouse_wake_writer):
            try: sock.close()
            except OSError: pass

    def join_focus(self, timeout=2.0):
        for t in self._threads: t.join(timeout)
        self._threads = [t for t in self._threads if t.is_alive()]
