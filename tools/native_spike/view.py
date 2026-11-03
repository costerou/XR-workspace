"""Glasses view controls for a managed capture run: zoom in/out, switch desktop, spacing.

Keyboard binds exist only while a run is active (user preference: Ctrl+Alt+arrows stay free for
text navigation otherwise). They run xr_control.py, which sends one frame to this run's private
control socket. Focus moves with Hyprland's monitor focus (the cursor follows).

Viewing modes (T2, Ctrl+Alt+V cycles): anchor (world-locked 3DoF), follow (world-locked inside a
dead zone, eases back in front) and locked (0DoF, head-locked). Ctrl+Alt+H toggles a help card in
the glasses listing every bind (rows come from BINDS, so it cannot drift), grouped in HELP_SECTIONS. Rows that do not fit
the output's height flow onto further pages (the renderer decides how many): Ctrl+Alt+PgUp / PgDn, or a free-pointer
click on the card's arrows or "2 / 3" indicator, turn them; reopening the card starts on page 1. Optional cursor follow (Ctrl+Alt+F):
when the pointer crosses onto another managed output, the view switches to it (Hyprland focusedmon).
Head-pose prediction (Ctrl+Alt+P toggles; on by default since live run prd2 on 2026-10-31): the renderer extrapolates the pose by a
bounded horizon (measured software read->present latency plus the driver's per-glasses look-ahead).
Mode, cursor follow, prediction and spacing are saved in layout.json. The renderer shows a short pop-up
in the glasses whenever the viewing mode, cursor follow or prediction changes (and the viewing mode at start).
Per-desktop placement (position round you, distance, curvature; geometry.PLACEMENT_BOUNDS) arrives as one absolute
`placement` message from the controller (panel), is applied by the renderer and saved in the glasses model's profile.
It is not a keyboard op and not listed in capabilities (the panel renders those as buttons).
Placement pills (Ctrl+Alt+L; Ctrl+Alt+P was already prediction): in the overview the renderer draws a frosted-glass pill
under every desktop (spike/include/xr/glass.hpp). One at a time expands, with a short morph, into that desktop's own
placement panel: the same four settings the panel edits (left/right, down/up, distance, curvature) as sliders.
Ctrl+Alt+L expands the selected desktop's pill (zooming out to the overview first if zoomed in) or collapses whichever
is open; the overview free pointer clicks a pill to expand it (collapsing any other) and the pill, or anything outside
the panel, to collapse it. While a panel is open a second, modal bind set (MENU_BINDS: arrows, Shift+arrows, Tab, R,
Escape) is installed and it is removed again on close: Up/Down pick the setting, Left/Right adjust it (Shift: big step),
Tab / Shift+Tab move the open panel to the next / previous desktop, R puts that desktop back flat, Escape collapses.
Every change is applied live through the same `placement` frame, sent to the renderer as a `placement_menu` frame
(open + desktop = the expanded pill; while the free pointer holds a slider, also `surface`, the press-time placement the
renderer keeps the panel on) and saved to the profile 500 ms after the last change, or at once when the panel
collapses. Zooming in collapses it first. Menu ops are not in USER_OPS, so they are not panel capabilities either.
Overview free pointer (free_pointer.py): in the overview the mouse can leave a desktop and move freely in the glasses'
space as a small dim dot, to drag a desktop's pill (move), drag its corner (resize, the `distance` placement), click a
pill (expand / collapse its panel), press or drag an open panel's sliders, or click a desktop to focus it; inside a
desktop the real cursor works on windows as usual (a click there goes to the window, so it leaves an open panel
open). resize_press and resize_release come from modal left-button binds that exist only while the pointer is outside
the desktops; they are not USER_OPS.

Send-window binds (send.py: Ctrl+Alt+G to the selected desktop, Ctrl+Alt+1/2/3 to XR-n, Ctrl+Alt+B back to the physical
fallback) are installed and removed with the run binds, in their own Lua table and eval. They are not in BINDS, so they
add no help card row; the Ctrl+Alt+H row names Ctrl+Alt+G/B instead.

Typing mode (Ctrl+Alt+T, TYPING_OP): one chord that hands the keyboard back while the run keeps going. Entering it
collapses an open placement panel and closes the help card, then reinstalls the run's bind table with only the
Ctrl+Alt+T bind in it: the view binds, the send-window binds, the panel's modal binds, the overview free pointer (its
left-button binds, hidden cursor, dot and the pills) and a playing video's bare-key binds are all released, so arrows,
Space, Tab, Esc and other apps' Ctrl+Alt shortcuts reach the focused window. Ctrl+Alt+T again puts every bind back
(control mode). The mode is in the `view` payload, so the renderer shows a short, dimmed pop-up when it changes; it is
never saved and every run starts in control mode. Config reloads keep the current mode (after_reload in managed.py
reinstalls the matching set). Panel buttons keep working.

Legacy cleanup: builds before the glass pills had a menu "mouse mode" (Ctrl+Alt+M) with its own left-button binds and
hidden cursor. A run of such a build that died in mouse mode left those behind; remove_menu_binds_lua() (and the bind
helper xr_control.py, which still accepts the old op names for this only) removes them and shows the cursor again, a
no-op when there are none.
"""
import math
import json
import os
from pathlib import Path
import re
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
         ('CTRL + ALT + P', 'prediction'), ('CTRL + ALT + H', 'help'), ('CTRL + ALT + T', 'typing_toggle'),
         ('CTRL + ALT + L', 'menu_toggle'),
         ('CTRL + ALT + O', 'video_open'),  # video_open: the bind helper shows a file picker first (video.py)
         ('CTRL + ALT + U', 'video_paste'),  # video_paste: the helper sends the clipboard's web video link (docs/web-video.md)
         ('CTRL + ALT + Page_Up', 'help_prev'), ('CTRL + ALT + Page_Down', 'help_next'))  # help card pages (HELP_PAGE_OPS)
# Ctrl+Alt+PgUp / PgDn flip the open help card's pages (they do nothing while it is closed). Run binds, not modal ones:
# plain PgUp / PgDn stay with the focused window, and Ctrl+Alt+Left / Right already switch desktops.
HELP_PAGE_OPS = {'help_prev': -1, 'help_next': 1}
# Typing mode (module docstring): the one run bind that stays while every other bind is released. Ctrl+Alt+T is free in
# Omarchy (it binds Super+Ctrl+Alt+T, a different chord) and in this run's binds; bind_check.py checks the real config.
TYPING_OP = 'typing_toggle'
TYPING_BIND = next(b for b in BINDS if b[1] == TYPING_OP)
# Ctrl+Alt+H help card: what each bind does (every op in BINDS needs an entry; tests enforce it).
HELP_ACTIONS = {'zoom_in': 'Zoom into the selected desktop', 'zoom_out': 'Overview: free mouse, pills, corners',
                'prev': 'Previous desktop', 'next': 'Next desktop', 'spacing_down': 'Less space between desktops',
                'spacing_up': 'More space between desktops', 'recenter': 'Recenter the view',
                'mode_next': 'Cycle viewing mode', 'cursor_follow': 'Toggle cursor follow', 'prediction': 'Toggle pose prediction',
                'help': 'Help; Ctrl+Alt+G/B: window to glasses/back',
                'typing_toggle': 'Typing mode: XR keys off / on',
                'menu_toggle': 'Placement pill of the selected desktop', 'video_open': 'Play a 180/360 VR video',
                'video_paste': 'Play the browser video you copied', 'help_prev': 'Previous help page',
                'help_next': 'Next help page'}
# Placement pills: the panel's actions (shared contract with the renderer and tests; `menu_*` names kept from the
# placement menu the pills replaced) and the modal binds present only while a panel is open.
MENU_OPS = ('menu_toggle', 'menu_desktop_next', 'menu_desktop_prev', 'menu_field_next', 'menu_field_prev', 'menu_inc', 'menu_dec',
            'menu_inc_big', 'menu_dec_big', 'menu_reset', 'menu_back')
# menu_back (Escape) collapses the open panel; Tab / Shift+Tab move it to the next / previous desktop (only one is open).
MENU_BINDS = (('up', 'menu_field_prev'), ('down', 'menu_field_next'), ('left', 'menu_dec'), ('right', 'menu_inc'),
              ('SHIFT + left', 'menu_dec_big'), ('SHIFT + right', 'menu_inc_big'), ('Tab', 'menu_desktop_next'),
              ('SHIFT + Tab', 'menu_desktop_prev'), ('R', 'menu_reset'), ('Escape', 'menu_back'))
# Key-only ops that do nothing in typing mode: their binds are gone, so they can only come from a stale or racing bind.
# (The overview free pointer, and with it every pill click and slider, is off in typing mode: OverviewPointer.overview.)
IGNORED_WHILE_TYPING = MENU_OPS + SEND_OPS
MAX_POINTER_PX = 8192      # spike/include/xr/ipc.hpp kMaxPointerPx (the overview free pointer's dot, canvas px)
# (small, Shift) step per field, in the field's own units (geometry.PLACEMENT_BOUNDS); results are clamped to the bounds.
MENU_STEPS = {'x': (2, 150), 'y': (2, 100), 'distance': (5, 25), 'curvature': (2, 20)}
MENU_SAVE_DELAY = 0.5  # seconds after the last panel change (key, slider or pill drag) before the profile is written
# The help card and an open placement panel close by themselves after this many seconds without input (a run key, a
# free-pointer click or slider, pointer motion while a panel is open, or a panel placement change), so static text does
# not burn into the glasses' micro-OLED panels. Change it here; the
# help card's footer note repeats it from spike/include/xr/hud.hpp kOverlayIdleCloseS (a test keeps them equal).
OVERLAY_IDLE_CLOSE_S = 30
KEY_NAMES = {'CTRL': 'Ctrl', 'ALT': 'Alt', 'SHIFT': 'Shift', 'SUPER': 'Super', 'up': 'Up', 'down': 'Down', 'left': 'Left',
             'right': 'Right', 'bracketleft': '[', 'bracketright': ']', 'space': 'Space', 'Tab': 'Tab', 'Escape': 'Esc',
             'Page_Up': 'PgUp', 'Page_Down': 'PgDn',
             **{f'code:{10 + i}': str((i + 1) % 10) for i in range(10)}}  # Omarchy-style digit keycodes: code:10 is 1


def chord_label(chord):
    """'CTRL + ALT + bracketleft' -> 'Ctrl+Alt+['."""
    return '+'.join(KEY_NAMES.get(part, part.upper() if len(part) == 1 else part) for part in chord.split(' + '))


# Help card rows that are not key binds. Empty: the overview free pointer (free_pointer.py, mouse only) is named in the
# zoom_out row. The card sizes its text from the output (hud.cpp kHelpStyles) and splits rows it cannot fit into pages,
# so there is no row limit beyond the renderer's kMaxHelpRows (spike/tests/help_card_test.cpp). Extra rows get their own
# section at the end.
EXTRA_HELP_ROWS = ()

# Help card sections, in order: the op naming each row (a pair's first op). The renderer keeps a section on one page when
# it fits one and draws a faint rule between sections. An op in BINDS but in no section lands in the last one.
HELP_SECTIONS = (('zoom_in', 'zoom_out', 'prev', 'next', 'spacing_down', 'recenter'),  # moving around the desktops
                 ('mode_next', 'cursor_follow', 'typing_toggle'),                       # modes
                 ('menu_toggle', 'video_open'),                                          # placement and VR video
                 ('help', 'help_prev'))                                                  # this card


# Bind pairs shown as one help-card row, which keeps the card short. first op -> (second op, action).
HELP_PAIRS = {'cursor_follow': ('prediction', 'Toggle cursor follow / prediction'),  # frees the row Ctrl+Alt+T uses
              'spacing_down': ('spacing_up', 'Less / more space between desktops'),
              'video_open': ('video_paste', 'VR video: file / copied web link'),  # docs/web-video.md
              'help_prev': ('help_next', 'Help: previous / next page')}


def _help_sections():
    """[[row, ...], ...]: the help card's rows grouped by HELP_SECTIONS (empty sections dropped)."""
    chords = {op: chord for chord, op in BINDS}
    seconds = {second for second, _ in HELP_PAIRS.values()}

    def row(op):
        if op in HELP_PAIRS:
            second, action = HELP_PAIRS[op]
            a, b = chord_label(chords[op]), chord_label(chords[second])
            prefix = a[:a.rfind('+') + 1]  # 'Ctrl+Alt+[' and 'Ctrl+Alt+]' -> 'Ctrl+Alt+[ / ]'
            return [f'{a} / {b[len(prefix):]}' if b.startswith(prefix) else f'{a} / {b}', action]
        return [chord_label(chords[op]), HELP_ACTIONS[op]]
    placed = {op for section in HELP_SECTIONS for op in section}
    sections = [[row(op) for op in section if op in chords] for section in HELP_SECTIONS]
    rest = [row(op) for _, op in BINDS if op not in placed and op not in seconds]
    if rest: sections[-1] += rest
    if EXTRA_HELP_ROWS: sections.append([list(r) for r in EXTRA_HELP_ROWS])
    return [s for s in sections if s]


def help_rows():
    return [row for section in _help_sections() for row in section]


def help_sections():
    """Row count of each help card section, in order (the `help` frame's `sections`; they add up to help_rows())."""
    return [len(section) for section in _help_sections()]


def help_frame(run_id, session, request_id, visible, turn=0):
    """Renderer `help` frame: the card shown or hidden with every row and its sections; `turn` -1 / 1 flips a shown
    card to the previous / next page (the renderer knows how many pages fit and wraps round)."""
    if type(turn) is not int or turn not in (-1, 0, 1): raise ValueError('invalid help turn')
    payload = {'visible': bool(visible), 'rows': help_rows(), 'sections': help_sections()}
    if turn: payload['turn'] = turn
    return (json.dumps({'schema': 1, 'request_id': request_id, 'run_id': run_id, 'session': session, 'op': 'help',
                        'payload': payload}, separators=(',', ':')) + '\n').encode()
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
    """Renderer `placement_menu` frame: open + desktop = that desktop's pill is expanded into its placement panel (open
    false: every pill collapsed), field = the highlighted setting, placements = what the panel shows (always three).
    Optional `surface`, open panels only: while the free pointer holds one of its sliders, the placement the panel is
    drawn on (its desktop's at the press; spike/include/xr/ipc.hpp PlacementMenuPayload)."""
    from .geometry import PLACEMENT_KEYS, validate_placement, validate_placements
    keys = {'open', 'desktop', 'field', 'placements'}
    if not isinstance(menu, dict) or set(menu) - {'surface'} != keys: raise ValueError('invalid menu')
    if type(menu['open']) is not bool or type(menu['desktop']) is not int or not 0 <= menu['desktop'] <= 2 \
            or menu['field'] not in PLACEMENT_KEYS or ('surface' in menu and menu['open'] is not True):
        raise ValueError('invalid menu')
    payload = {'open': menu['open'], 'desktop': menu['desktop'], 'field': menu['field'],
               'placements': validate_placements(menu['placements'])}
    if 'surface' in menu: payload['surface'] = validate_placement(menu['surface'])
    return (json.dumps({'schema': 1, 'request_id': request_id, 'run_id': run_id, 'session': session, 'op': 'placement_menu',
                        'payload': payload}, separators=(',', ':')) + '\n').encode()


def warp_cursor_lua(x, y):
    """Put the cursor at layout pixel (x, y): the overview free pointer parks its hidden cursor (and re-centres it, so it
    never stops at a screen edge) and hands it back where the pointer re-enters a desktop."""
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
        # Typing mode (Ctrl+Alt+T): every run bind but the toggle released. Never saved; each run starts in control mode.
        self.typing = False
        # Placement pills; never saved, every pill starts collapsed. menu_open + menu_desktop (an index into self.names):
        # that desktop's pill is expanded into its panel (only one ever is); menu_field: the highlighted setting.
        self.menu_open, self.menu_desktop, self.menu_field = False, 0, 'x'
        # While the free pointer holds one of the open panel's sliders: the desktop's placement at the press, which the
        # renderer draws the panel on (hold_surface). None otherwise; any collapse or switch of the open panel drops it.
        self.menu_surface = None

    def menu(self):
        """`placement_menu` payload: always three placements (desktops beyond this run's outputs are shown flat), plus
        `surface` while a slider of the open panel is held."""
        from .geometry import DEFAULT_PLACEMENT
        placements = [dict(p) for p in self.placements] + [dict(DEFAULT_PLACEMENT) for _ in range(3 - len(self.placements))]
        out = {'open': self.menu_open, 'desktop': self.menu_desktop, 'field': self.menu_field, 'placements': placements}
        if self.menu_open and self.menu_surface is not None: out['surface'] = dict(self.menu_surface)
        return out

    def open_menu(self, desktop):
        """Expand `desktop`'s pill into its panel; an open one on another desktop collapses (only one is ever open)."""
        if type(desktop) is not int or not 0 <= desktop < len(self.names): raise ValueError('invalid desktop')
        self.menu_open, self.menu_desktop, self.menu_surface = True, desktop, None

    def hold_surface(self, placement):
        """The free pointer pressed one of the open panel's sliders: draw the panel on `placement` (the desktop's at the
        press, which the drag is measured on) until hold_surface(None) at the release. Ignored with every pill collapsed."""
        from .geometry import validate_placement
        placement = None if placement is None else validate_placement(placement)
        self.menu_surface = placement if self.menu_open else None

    def slide(self, field, t):
        """The open panel's slider for `field` pressed or dragged at fraction `t` of its track (0 = low bound, 1 = high):
        that setting becomes the highlighted one and takes the whole-unit value there (geometry.PLACEMENT_BOUNDS).
        True if a placement changed. Nothing happens while every pill is collapsed."""
        from .geometry import PLACEMENT_BOUNDS
        if field not in PLACEMENT_BOUNDS or type(t) not in (int, float) or not math.isfinite(t): raise ValueError('invalid slider')
        if not self.menu_open: return False
        self.menu_field = field
        lo, hi = PLACEMENT_BOUNDS[field]
        value = lo + int(math.floor(min(max(t, 0.0), 1.0) * (hi - lo) + 0.5))
        current = self.placements[self.menu_desktop]
        if current[field] == value: return False
        self.placements = [{**current, field: value} if i == self.menu_desktop else p for i, p in enumerate(self.placements)]
        return True

    def apply_menu(self, op):
        """Update the pills' state and, for adjust/reset ops, the open desktop's placement (clamped). True if placements
        changed.

        menu_toggle expands the selected desktop's pill, or collapses the open one; menu_back (Escape) collapses. The
        other ops act only while a panel is open (their binds exist only then; a late key is ignored).
        """
        from .geometry import DEFAULT_PLACEMENT, PLACEMENT_BOUNDS, PLACEMENT_KEYS
        if op not in MENU_OPS: raise ValueError('unsupported menu operation')
        if op == 'menu_back': op = 'menu_toggle' if self.menu_open else None
        if op == 'menu_toggle':
            if self.menu_open: self.menu_open, self.menu_surface = False, None
            else: self.open_menu(self.selected)  # the pill of the desktop being looked at
            return False
        if not self.menu_open: return False
        if op in ('menu_desktop_next', 'menu_desktop_prev'):  # the open panel moves to the next / previous desktop's pill
            self.menu_desktop = (self.menu_desktop + (1 if op == 'menu_desktop_next' else -1)) % len(self.names)
            self.menu_surface = None
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
            value = current[self.menu_field] + step
            if self.menu_field == 'x':  # the turn round you carries on round past behind you (orbit.py), never stops
                from .orbit import wrap
                value = wrap(value)
            updated = {**current, self.menu_field: min(max(value, lo), hi)}
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
                'cursor_follow': self.cursor_follow, 'prediction': self.prediction, 'typing': self.typing}

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
            return self.names[target]  # overview: highlight; zoomed: the renderer zooms out, turns to it and zooms in; focused
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


def install_binds_lua(python, client, control_socket, run_id, session, typing=False):
    """The run's Ctrl+Alt binds; in typing mode only the Ctrl+Alt+T toggle (TYPING_BIND), in the same table."""
    for p in (python, client, control_socket):
        if not isinstance(p, str) or not SAFE_PATH.match(p): raise ValueError('unsafe path for bind command')
    for t in (run_id, session):
        if not isinstance(t, str) or not SAFE_TOKEN.match(t): raise ValueError('unsafe token for bind command')
    return _binds_lua('xr_workspace_binds', (TYPING_BIND,) if typing else BINDS,
                      f'{python} -I {client} {control_socket} {run_id} {session}')


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
    """Run end / reinstall: the view binds, the send-window binds and an open placement panel's modal binds."""
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
    """Modal placement-panel binds (bare arrows, Tab, R, Escape); replaces any surviving set, so never doubled."""
    for p in (python, client, control_socket):
        if not isinstance(p, str) or not SAFE_PATH.match(p): raise ValueError('unsafe path for bind command')
    for t in (run_id, session):
        if not isinstance(t, str) or not SAFE_TOKEN.match(t): raise ValueError('unsafe token for bind command')
    return remove_menu_binds_lua() + ' ' + _binds_lua('xr_workspace_menu_binds', MENU_BINDS,
                                                      f'{python} -I {client} {control_socket} {run_id} {session}')


# Legacy cleanup (module docstring): the left-button binds and hidden cursor of the old menu "mouse mode", which builds
# before the glass pills installed. This build never creates xr_workspace_mouse_binds or xr_workspace_mouse, so the tail
# is a no-op unless a run of an older build died in that mode: then the binds go and the cursor's own visibility and
# position come back. Kept byte for byte (xr_control.REMOVE_MENU_BINDS repeats it; a test compares).
_LEGACY_MOUSE_CLEANUP_LUA = (_remove_lua('xr_workspace_mouse_binds') + ' '
                             'do local s = xr_workspace_mouse xr_workspace_mouse = nil if s then '
                             'pcall(hl.config, { cursor = { invisible = s.invisible } }) '
                             'if s.home then pcall(function() hl.dispatch(hl.dsp.cursor.move({ x = s.home.x, y = s.home.y })) end) end '
                             'end end')


def remove_menu_binds_lua():
    """Panel collapsed, run end, or (xr_control.py) a panel key with no run listening; plus the legacy cleanup above."""
    return _remove_lua('xr_workspace_menu_binds') + ' ' + _LEGACY_MOUSE_CLEANUP_LUA


class ViewControl:
    """Supervisor-side handler: user op -> state, async focus, saved spacing, renderer view frame."""
    def __init__(self, state, run_id, session, focus, settings, server, first_request_id=2, border=None, on_reload=None,
                 placements=None, menu_binds=None, clock=time.monotonic, typing_binds=None):
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
        self.last_input = clock()  # monotonic time of the last run key / click / panel change (overlay idle close)
        self.placements = placements  # optional store with save(list of 3); see profiles.PlacementStore
        self.menu_binds = menu_binds  # optional (open: bool) -> None: install/remove the panel's modal binds (compositor call)
        self.clock = clock
        self.save_due = None          # monotonic deadline of a pending debounced placement save
        self._menu_lock = threading.Lock()
        self._menu_binds_installed = False
        self._menu_threads = []
        self.overview_pointer = None  # optional free_pointer.OverviewPointer (overview free pointer); set by managed.view_controls
        self.wake = None    # optional socket the supervisor loop also waits on (the free pointer's sampler pokes it)
        self.sender = None  # optional send.WindowSender (Ctrl+Alt+G/1-3/B); set by managed.view_controls
        # optional (typing: bool) -> None: swap the run's bind table between the full set and the Ctrl+Alt+T toggle alone
        # (a compositor call, run off the loop under _menu_lock so it is ordered with the panel's own bind changes).
        self.typing_binds = typing_binds
        self._typing_installed = False  # the bind set last put in place (False: the full set from install_view_binds)

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

    def _menu_update(self, change):
        """Apply `change` (() -> True if a placement changed) to the pills' state: the live placement frame first (if one
        changed; saved after MENU_SAVE_DELAY), then the `placement_menu` frame (if anything it shows changed). A panel
        collapsing saves at once; the modal keys follow the panel being open (installed / removed off the loop)."""
        before, was_open = self.state.menu(), self.state.menu_open
        changed = change()
        out = b''
        if changed:
            out += self._placement_frame()
            self.save_due = self.clock() + MENU_SAVE_DELAY
        if was_open and not self.state.menu_open: self.flush()  # collapsing saves at once
        if was_open != self.state.menu_open: self._sync_menu_binds()
        if self.state.menu() != before: out += self._menu_frame()
        return out

    def _zoom_out(self):
        """To the overview (the pills and panels are drawn there only), as Ctrl+Alt+Down does. A view frame."""
        if self.overview_pointer is not None: self.overview_pointer.summon()  # a cursor off the desktops comes into one
        self.state.apply('zoom_out')
        return self._frame()

    def _on_menu(self, op):
        """Panel key op. Ctrl+Alt+L while zoomed in zooms out first, so the expanding pill is seen."""
        out = self._zoom_out() if op == 'menu_toggle' and not self.state.menu_open and self.state.zoomed else b''
        return (out + self._menu_update(lambda: self.state.apply_menu(op))) or None

    # --- the overview free pointer's pill clicks and sliders (free_pointer.OverviewPointer) ------------------------------
    def pill_click(self, desktop):
        """A click on `desktop`'s pill (or its open panel's header): expand it, collapsing any other (only one is open),
        or collapse it if it is the open one. Same path as Ctrl+Alt+L: the modal keys are installed / removed, a
        collapsing panel is saved at once and the idle close restarts. Renderer frames (bytes)."""
        if type(desktop) is not int or not 0 <= desktop < len(self.state.names): return b''
        if self.state.typing or self.video_active: return b''  # the free pointer is off then; a stale click does nothing
        self.last_input = self.clock()
        if self.state.menu_open and self.state.menu_desktop == desktop: return self._on_menu('menu_toggle') or b''
        if self.state.menu_open: self.flush()  # the other desktop's panel collapses: its changes are saved at once
        out = self._zoom_out() if self.state.zoomed else b''  # (the free pointer only clicks in the overview anyway)
        return out + self._menu_update(lambda: self.state.open_menu(desktop) or False)

    def pill_close(self):
        """A press outside the open panel: collapse it (saved at once, modal keys removed). Frames (bytes), b'' if none."""
        if not self.state.menu_open: return b''
        return self._on_menu('menu_toggle') or b''

    def pill_slide(self, desktop, field, t, surface=None):
        """The free pointer pressed or drags the open panel's `field` slider at fraction `t` of its track: that setting is
        highlighted and takes the value there, applied live and saved like a key change (MENU_SAVE_DELAY, at once on
        release). `surface` (the press): the desktop's placement then, which the renderer draws the panel on until
        pill_release, so the knob stays under the pointer the drag is measured with. Ignored unless `desktop`'s panel is
        the open one. Frames (bytes)."""
        if not self.state.menu_open or self.state.menu_desktop != desktop: return b''
        self.last_input = self.clock()  # a held slider keeps the panel up

        def change():
            if surface is not None: self.state.hold_surface(surface)
            return self.state.slide(field, t)
        return self._menu_update(change)

    def pill_release(self):
        """The held slider was let go (or its drag ended): the open panel goes back onto its desktop's live placement.
        Frames (bytes), b'' if nothing was held."""
        if self.state.menu_surface is None: return b''
        return self._menu_update(lambda: self.state.hold_surface(None) or False)

    def focus_desktop(self, desktop):
        """A circle-cursor (free pointer) click on `desktop` in the overview: select it and zoom into it, leaving the
        overview, exactly as Ctrl+Alt+Up does on the selected desktop (an open panel collapses first, saved; its output
        gets Hyprland's monitor focus off the loop; one view frame). The free pointer and the input lock then let go of
        the mouse as on any zoom-in. Frames (bytes), b'' for a stale click."""
        if type(desktop) is not int or not 0 <= desktop < len(self.state.names): return b''
        if self.state.zoomed or self.state.typing or self.video_active: return b''  # no free pointer then: a stale click
        self.state.selected = desktop
        return self.on_message({'op': 'zoom_in', 'payload': {}}) or b''

    def set_overview_geometry(self, geometry):
        """Supervisor: the renderer's overview layout (free_pointer.parse_overview_geometry), for free-pointer hit testing."""
        if self.overview_pointer is not None: self.overview_pointer.set_geometry(geometry)

    def _sync_menu_binds(self, force=False):
        if self.menu_binds is None: return
        # Compositor call: never on the watchdog loop. The worker applies the state current when it runs, serialized,
        # so a fast open/close/open cannot leave the binds out of step with the panel.
        t = threading.Thread(target=self._menu_bind_work, args=(force,), daemon=True); t.start()
        self._threads.append(t); self._menu_threads.append(t)

    def _menu_bind_work(self, force):
        with self._menu_lock:
            binds, want = self.menu_binds, self.state.menu_open
            if binds is not None and (want != self._menu_binds_installed or (force and want)):
                try:
                    binds(want)
                    self._menu_binds_installed = want
                except (OSError, ValueError, TypeError, KeyError): pass  # best-effort; Ctrl+Alt+L still collapses the panel

    def poll(self, now=None):
        """Supervisor loop tick: the overview free pointer, a debounced panel placement save once it is due, and closing
        the help card and an open panel once they have been up OVERLAY_IDLE_CLOSE_S without input. Frames or None."""
        now = self.clock() if now is None else now
        out = self.overview_pointer.poll(now) if self.overview_pointer is not None else b''  # pill / corner drags, sliders
        if self.save_due is not None and now >= self.save_due: self.flush()
        if now - self.last_input >= OVERLAY_IDLE_CLOSE_S:
            if self.state.menu_open: out += self._on_menu('menu_toggle') or b''  # saves at once and releases the panel keys
            if self.help_visible: out += self._help_toggle()
        return out or None

    def _typing_toggle(self):
        """Ctrl+Alt+T: typing mode on (an open panel collapses, the help card closes, every run bind but this one goes,
        and the free pointer with its pills is off) or back off."""
        out = b''
        if not self.state.typing:
            if self.state.menu_open: out += self._on_menu('menu_toggle') or b''  # saves; releases the panel keys
            if self.help_visible: out += self._help_toggle()
        self.state.typing = not self.state.typing
        self._sync_typing_binds()
        return out + self._frame()  # the renderer's pop-up shows the new mode

    def _sync_typing_binds(self):
        if self.typing_binds is None: return
        t = threading.Thread(target=self._typing_bind_work, daemon=True); t.start()
        self._threads.append(t); self._menu_threads.append(t)

    def _typing_bind_work(self, force=False):
        with self._menu_lock:
            binds, want = self.typing_binds, self.state.typing
            if binds is None or (want == self._typing_installed and not force): return
            try:
                binds(want)
                self._typing_installed = want
                if want: self._menu_binds_installed = False  # the swap removed those too
            except (OSError, ValueError, TypeError, KeyError): pass  # best effort; Ctrl+Alt+T again retries

    def _help_toggle(self):
        self.help_visible = not self.help_visible  # the renderer starts a reopened card on page 1
        frame = help_frame(self.run_id, self.session, self.next_id, self.help_visible)
        self.next_id += 1
        return frame

    def help_turn(self, turn):
        """Ctrl+Alt+PgUp / PgDn, or a free-pointer click on the card's arrows or page indicator: the open help card flips
        to the previous (-1) or next (1) page. Nothing while it is closed. A `help` frame (bytes) or b''."""
        if not self.help_visible or turn not in (-1, 1): return b''
        self.last_input = self.clock()  # turning pages keeps the card up
        frame = help_frame(self.run_id, self.session, self.next_id, True, turn)
        self.next_id += 1
        return frame

    def set_help_geometry(self, geometry):
        """Supervisor: where the help card and its page controls lie on the overview's canvas (free_pointer.
        parse_help_geometry; None when the card is not up in the overview), for free-pointer hit testing."""
        if self.overview_pointer is not None: self.overview_pointer.set_help_geometry(geometry)

    def flush(self):
        """Write any pending panel / drag placement save now (panel collapse, drag release, run end)."""
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
            self.last_input = self.clock()  # any run key, click or panel change keeps an open card or panel up
        if isinstance(message, dict) and message.get('op') == 'placement':
            return self._on_placement(message.get('payload'))
        if isinstance(message, dict) and message.get('op') == TYPING_OP:
            return self._typing_toggle()
        if isinstance(message, dict) and self.state.typing and message.get('op') in IGNORED_WHILE_TYPING: return None
        if isinstance(message, dict) and message.get('op') in MENU_OPS:
            return self._on_menu(message['op'])
        if isinstance(message, dict) and message.get('op') in ('resize_press', 'resize_release'):  # resize.RESIZE_OPS
            return self.overview_pointer.on_op(message['op']) if self.overview_pointer is not None else None
        if isinstance(message, dict) and message.get('op') in SEND_OPS:  # the move itself runs off the loop
            if self.sender is not None: self.sender.request(message['op'], self.state.names[self.state.selected])
            return None
        if isinstance(message, dict) and message.get('op') in HELP_PAGE_OPS:
            return self.help_turn(HELP_PAGE_OPS[message['op']]) or None
        if not isinstance(message, dict) or message.get('op') not in USER_OPS: return None
        if message['op'] == 'help': return self._help_toggle()
        if message['op'] == 'recenter':
            frame = (json.dumps({'schema': 1, 'request_id': self.next_id, 'run_id': self.run_id, 'session': self.session,
                                 'op': 'recenter', 'payload': {}}, separators=(',', ':')) + '\n').encode()
            self.next_id += 1
            return frame
        # Zooming in collapses an open panel first (saved, keys released): pills and panels live in the overview.
        out = (self._on_menu('menu_toggle') or b'') if message['op'] == 'zoom_in' and self.state.menu_open else b''
        before = self.state.view()
        if message['op'] == 'zoom_out' and self.state.zoomed and self.overview_pointer is not None:
            self.overview_pointer.summon()  # a cursor off the desktops comes into the selected one
        target = self.state.apply(message['op'])
        if target is not None:
            # Never block the watchdog loop on a compositor reply.
            t = threading.Thread(target=self._focus, args=(target,), daemon=True); t.start(); self._threads.append(t)
        if message['op'] in ('spacing_up', 'spacing_down', 'mode_next', 'cursor_follow', 'prediction'):
            try: self.settings.save(self.state.settings())  # a settings store (profiles.LayoutStore/ProfileStore)
            except (OSError, ValueError): pass
        if self.state.view() != before: out += self._frame()
        return out or None

    def _on_placement(self, payload):
        """Absolute placements from the panel; applied live and saved to the glasses profile. Invalid: ignored."""
        try:
            if not isinstance(payload, dict) or not self.state.set_placements(payload.get('desktops')): return None
        except ValueError:
            return None
        self.save_due = None  # this save also covers any pending pill-panel change
        self._save_placements()
        # An open glass panel shows the panel's values too.
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
            if m and self.state.cursor_entered(m.group(1)):
                changed = True; self.cursor_switches += 1
            if CONFIGRELOADED.match(text): reloaded = True
        if reloaded:
            self.reloads += 1
            self._menu_binds_installed = False  # a reload drops runtime binds; the reload work re-adds an open panel's
            if self.overview_pointer is not None: self.overview_pointer.binds_dropped = True  # the sampler redoes them if wanted
            if self.on_reload is not None:  # binds and session mode: compositor calls, never on the watchdog loop
                t = threading.Thread(target=self._reload_work, daemon=True); t.start(); self._threads.append(t)
        out = (self._frame() if changed else b'') + (self._style_frame() if reloaded else b'')
        return out or None

    def _reload_work(self):
        with self._menu_lock:  # ordered with a typing-mode swap in flight
            typing = self.state.typing  # on_reload (managed.after_reload) reinstalls the set for this mode
            try: self.on_reload()
            except (OSError, ValueError, TypeError, KeyError): pass  # best-effort; the run itself is unaffected
            self._typing_installed = typing
        self._typing_bind_work()  # Ctrl+Alt+T pressed during the reload: put the matching set in place
        self._menu_bind_work(force=True)  # after the view binds: re-add an open panel's modal keys

    def _focus(self, target):
        try: self.focus(target)
        except (OSError, ValueError): pass  # focus is best-effort; the view still changes

    def stop_menu_binds(self, timeout=2.5):
        """Run end: no later panel or typing bind change, and wait (bounded in total) for any in flight, so none outlives
        the run."""
        self.menu_binds = self.typing_binds = None
        deadline = time.monotonic() + timeout
        for t in self._menu_threads: t.join(max(0.0, deadline - time.monotonic()))

    def join_focus(self, timeout=2.0):
        for t in self._threads: t.join(timeout)
        self._threads = [t for t in self._threads if t.is_alive()]
