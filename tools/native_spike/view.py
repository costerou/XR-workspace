"""Glasses view controls for a managed capture run: maximize / overview, switch desktop, spacing.

Keyboard binds exist only while a run is active (user preference: Ctrl+Alt+arrows stay free for
text navigation otherwise). They run xr_control.py, which sends one frame to this run's private
control socket. The selected desktop's monitor gets Hyprland's focus (and the cursor with it).

Maximized and the overview (maximize-follow, 2026-11-03; INAIR Pod style). The overview shows every desktop; a
maximized desktop (the `zoomed` state: Ctrl+Alt+Up, or the panel's Maximize) fills the view. A click on a desktop in the
overview only selects it (anchor-no-click-maximize, 2026-11-03: select_desktop); it never maximizes. The viewing mode
(T2: anchor, world-locked 3DoF; follow, eases back in front of you; locked, 0DoF head-locked) follows from that state
(ViewState.mode): the overview is always anchor; a maximized desktop is follow or locked. While maximized, Ctrl+Alt+V
toggles follow and locked (MAXIMIZED_MODES), and that choice sticks: every later maximize in the run uses it, until
Ctrl+Alt+V again (it is not saved, so each XR start is follow again). In the overview Ctrl+Alt+V changes nothing and a
toast says why (MODE_OVERVIEW_TOAST). Un-maximizing (Ctrl+Alt+Down, Ctrl+Alt+L, a pill) returns to anchor, and the renderer puts the
room back exactly as it was left (spike pose.hpp ModeController::Change), so every desktop is where it was. Every run
starts in the overview, in anchor; the saved mode is no longer read. Maximized, the cursor pushed past an edge pans to the
desktop that way by hand (edge_pan.py) and every other switch cuts straight to the desktop. Ctrl+Alt+H toggles a help card in
the glasses listing every bind (rows come from BINDS, so it cannot drift), grouped in HELP_SECTIONS. Rows that do not fit
the output's height flow onto further pages (the renderer decides how many): Ctrl+Alt+PgUp / PgDn, or a free-pointer
click on the card's arrows or "2 / 3" indicator, turn them; reopening the card starts on page 1. The cursor stays on the
selected desktop's monitor (unified_cursor.py), and the view changes desktop by key, by the dot moving onto a desktop in
the overview (that desktop takes the cursor and every window there works as on a normal screen), or, maximized, by
pushing the cursor past an edge toward a desktop placed that way round you (switch_desktop; neighbors.py). Cursor follow (Ctrl+Alt+F, the monitor the cursor
crossed onto in Hyprland's layout) was removed on 2026-11-02.
Head-pose prediction (Ctrl+Alt+P toggles; on by default since live run prd2 on 2026-10-31): the renderer extrapolates the pose by a
bounded horizon (measured software read->present latency plus the driver's per-glasses look-ahead).
Prediction, spacing and the VR video quality settings (quality.py) are saved in layout.json (its `mode` stays for the
file's schema, always "anchor", and is not read; a `cursor_switch_animate` from the removed Ctrl+Alt+A is ignored).
The renderer shows a short pop-up in the glasses when Ctrl+Alt+V changes the viewing mode or prediction changes (and
the viewing mode at start), not for the mode change a maximize or un-maximize makes.
Per-desktop placement (position round you, distance, curvature; geometry.PLACEMENT_BOUNDS) arrives as one absolute
`placement` message from the controller (panel), is applied by the renderer and saved in the glasses model's profile.
It is not a keyboard op and not listed in capabilities (the panel renders those as buttons).
An edit of one desktop in the overview (the pill panel's keys and sliders, the free pointer's drags) bumps the desktops it
runs into out of its way (bump.py, ViewControl._bump); a discrete one that bumped glides (GLIDE_MS). The panel's Apply is
the exception: it applies exactly as given, even over another desktop. When the layout itself changes (the renderer
reports a new overview layout: a run's start with a restored setup, spacing, a desktop added, removed or widened), the
desktops that overlap now are pushed apart (bump.settle, ViewControl._settle). Pushed desktops are placements like any
other and are saved with them.
Placement pills (Ctrl+Alt+L; Ctrl+Alt+P was already prediction): in the overview the renderer draws a glass pill
under every desktop (spike/include/xr/glass.hpp). One at a time expands, with a short morph, into that desktop's own
placement panel: the same four settings the panel edits (left/right, down/up, distance, curvature) as sliders.
Ctrl+Alt+L expands the selected desktop's pill (back to the overview first if maximized) or collapses whichever
is open; the overview free pointer clicks a pill to expand it (collapsing any other) and the pill, or anything outside
the panel, to collapse it. While a panel is open a second, modal bind set (MENU_BINDS: arrows, Shift+arrows, Tab, R,
Escape) is installed and it is removed again on close: Up/Down pick the setting, Left/Right adjust it (Shift: big step),
Tab / Shift+Tab move the open panel to the next / previous desktop, R puts that desktop back flat in its slot and
levels it (no roll, free_pointer.OverviewPointer.level), Escape collapses.
An open panel also follows the focus: when the selection moves to another desktop in the overview that desktop's panel
opens and the old one collapses (ViewControl.follow_focus). With every pill collapsed a selection change opens nothing.
Every change is applied live through the same `placement` frame, sent to the renderer as a `placement_menu` frame
(open + desktop = the expanded pill; while the free pointer holds a slider, also `surface`, the press-time placement the
renderer keeps the panel on) and saved to the profile 500 ms after the last change, or at once when the panel
collapses. Maximizing collapses it first. Menu ops are not in USER_OPS, so they are not panel capabilities either.
Overview free pointer (free_pointer.py): in the overview the mouse can leave a desktop and move freely in the glasses'
space as a small dim dot, to drag a desktop's pill (move), drag its corner (resize, the `distance` placement), click a
pill (expand / collapse its panel), press or drag an open panel's sliders, roll or level a desktop with its level bar, or move onto
any desktop (no click maximizes: only Ctrl+Alt+Up does); inside a desktop the real cursor works on windows as usual (a
click there goes to the window, and the unified cursor's non-consuming bind also brings an open panel to that desktop
and takes its keys off the keyboard: ViewControl.desktop_press).
resize_press and resize_release come from left-button binds: modal (consuming) while the pointer is outside the
desktops, non-consuming over them (unified_cursor.py); they are not USER_OPS.

Desktop row (desktops.py): an open panel also has Ultrawide, Add new and Remove buttons (free-pointer presses; keys U, N
and Shift+Delete while it is open: MENU_BINDS). ViewControl.desktop_action plans the change, pauses the run's guard, tells the
renderer the new desktop set (`desktops` frame) and hands the broker steps to desktops.DesktopManager's worker; poll()
collects the result, sends the view, placement and panel frames for the new set, restarts the guard and saves the
profile. Remove asks for a second press within desktops.REMOVE_ARM_S. Above that row, the Scale dropdown (a free-pointer
click opens its list, a click on a step picks it: ViewControl.scale_box / scale_pick) sets that desktop's Hyprland output
scale through the same path (desktops.SCALE), and above Scale the Resolution dropdown (ViewControl.resolution_box /
resolution_pick; geometry.RESOLUTIONS) its pixel size, live, keeping a scale that still divides it (else the nearest one
that does: desktops.RESOLUTION). Only one of the two lists is open at a time. The run then has 1..geometry.MAX_DESKTOPS desktops,
any of XR-1..XR-4 (a removed one leaves a gap in the numbers until Add fills it).

Remembered setup and Ctrl+Alt+R (remember-layout-reset-bind, 2026-11-03). Every XR start restores the last session's
monitor setup: the desktops (count, resolution, scale), each one's placement and its levelling roll (profiles.py
load_setup; three flat desktops when nothing is saved or the save is unusable). The run saves it on every change and at
its end (ViewControl.save_setup). Ctrl+Alt+R (RESET_OP, a run bind in BINDS like the others) resets every desktop live to
the default three flat 1920x1080 desktops at 100%, with the default spacing. It asks first: the first press only shows a
toast, and a second one within RESET_ARM_S resets (otherwise nothing happens). Then an open panel collapses, a maximized
desktop goes back to the overview, the saved setup is replaced by the default at once, keeping the layout it replaces as
a one-step backup (profiles.PlacementStore.reset), and every desktop eases flat and level over RESET_ANIM_S while a fourth
one drops away out of the view (RESET_LEAVING_PLACEMENT). One desktops.RESET plan then reshapes, removes or adds desktops
through the same path as the pill's buttons (the removed desktop's windows go to the last one that stays), its `desktops`
frames carrying `ease`, so the renderer takes the tiles to their new layout along the same curve (view.hpp TileEase), and
a toast says it is done. The maximized follow / locked choice is untouched (it is never saved). Ignored while a VR video
plays or a desktop change is still running (a toast says so); off in typing mode (its bind is released, and a stale one
does nothing: layout_reset).

Window memory (window_memory.py): Hyprland's window events and a Super drag's end mark where the windows are as changed, poll
hands that to the tracker's worker once no desktop change is running, and every XR start puts the windows back. Ctrl+Alt+R
leaves it alone. It has no bind.

Send-window binds (send.py: Ctrl+Alt+G to the selected desktop, Ctrl+Alt+1..4 to XR-n, Ctrl+Alt+B back to the physical
fallback) are installed and removed with the run binds, in their own Lua table and eval. They are not in BINDS, so they
add no help card row; the Ctrl+Alt+H row names Ctrl+Alt+G/B instead.

Privacy mode (Ctrl+Alt+D, privacy.py): the main physical display (the run's fallback, e.g. DP-1) DPMS off / on, so
only the glasses show content; the run and recovery turn it back on. A run bind with its own help card row; off in
typing mode like the other run binds.

Typing mode (Ctrl+Alt+T, TYPING_OP): one chord that hands the keyboard back while the run keeps going. Entering it
collapses an open placement panel and closes the help card, then reinstalls the run's bind table with only the
Ctrl+Alt+T bind in it: the view binds, the send-window binds, the panel's modal binds, the overview free pointer (its
left-button binds, hidden cursor, dot and the pills) and a playing video's bare-key binds are all released, so arrows,
Space, Tab, Esc and other apps' Ctrl+Alt shortcuts reach the focused window. Ctrl+Alt+T again puts every bind back
(control mode). The mode is in the `view` payload, so the renderer shows a short, dimmed pop-up when it changes; it is
never saved and every run starts in control mode. Config reloads keep the current mode (after_reload in managed.py
reinstalls the matching set). Panel buttons keep working.

The background (environment.py): an optional 360 degree scene behind the desktops and a VR video, the TRON grid for now, in
the theme's colour. The placement panel has a Background dropdown and switch (background_box / background_pick /
background_switch) and a Brightness slider (a fifth `field`, brightness, that is not a placement), the same in every
desktop's panel; Ctrl+Alt+E goes between None and the last background used. Saved with the view settings; the renderer
turns it with the head pose whatever the viewing mode.

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

from . import edge_pan
from . import environment as _environment
from . import quality as _quality
from .environment import BRIGHTNESS_FIELD, SET_OP as ENVIRONMENT_SET_OP, TOGGLE_BIND as ENVIRONMENT_BIND, TOGGLE_OP as ENVIRONMENT_OP
from .geometry import MAX_DESKTOPS
from .privacy import PRIVACY_BIND, PRIVACY_OP
from .send import SEND_OPS

DEFAULT_SPACING, MAX_SPACING, SPACING_STEP = 40, 400, 20
USER_OPS = ('zoom_in', 'zoom_out', 'next', 'prev', 'spacing_up', 'spacing_down', 'recenter', 'mode_next',
            'prediction', 'help')
MODES = ('anchor', 'follow', 'locked')  # every viewing mode (the renderer's; the panel's set_settings)
# Ctrl+Alt+V's cycle while maximized; a maximize always starts in the first. The overview is always anchor (ViewState.mode).
MAXIMIZED_MODES = ('follow', 'locked')
# Hyprland Lua key strings join modifiers with " + " (e.g. "SUPER + SHIFT + R").
BINDS = (('CTRL + ALT + up', 'zoom_in'), ('CTRL + ALT + down', 'zoom_out'), ('CTRL + ALT + left', 'prev'),
         ('CTRL + ALT + right', 'next'), ('CTRL + ALT + bracketleft', 'spacing_down'), ('CTRL + ALT + bracketright', 'spacing_up'),
         ('CTRL + ALT + space', 'recenter'), ('CTRL + ALT + V', 'mode_next'),
         ('CTRL + ALT + P', 'prediction'), ('CTRL + ALT + H', 'help'), ('CTRL + ALT + T', 'typing_toggle'),
         ('CTRL + ALT + L', 'menu_toggle'),
         ('CTRL + ALT + R', 'layout_reset'),  # every desktop back to three flat ones, the save too (RESET_OP)
         ('CTRL + ALT + O', 'video_open'),  # video_open: the bind helper shows a file picker first (video.py)
         ('CTRL + ALT + U', 'video_paste'),  # video_paste: the helper sends the clipboard's web video link (docs/web-video.md)
         ('CTRL + ALT + Page_Up', 'help_prev'), ('CTRL + ALT + Page_Down', 'help_next'),  # help card pages (HELP_PAGE_OPS)
         PRIVACY_BIND,  # Ctrl+Alt+D: main display off / on (privacy.py)
         ('CTRL + ALT + C', 'playback_menu_toggle'),  # VR video: playback menu hidden / shown (PLAYBACK_MENU_OP)
         ('CTRL + ALT + K', 'binds_toggle'),  # the key bind panel (BINDS_PANEL_OP, keybinds.py)
         ENVIRONMENT_BIND)  # Ctrl+Alt+E: the background, None <-> the last one used (environment.py)
# Ctrl+Alt+PgUp / PgDn flip the open help card's pages (they do nothing while it is closed). Run binds, not modal ones:
# plain PgUp / PgDn stay with the focused window, and Ctrl+Alt+Left / Right already switch desktops.
HELP_PAGE_OPS = {'help_prev': -1, 'help_next': 1}
# Super drags: Hyprland's own window move and resize, Omarchy's `SUPER + mouse:272` / `SUPER + mouse:273`
# (default/hypr/bindings/tiling.lua; on this build `hyprctl binds -j` lists them as `__lua` binds with mouse false: the
# Lua drag dispatchers end themselves on release). The run binds the same chords non-consuming, beside them, to see a drag
# begin, and the buttons' release (ignore_mods: Super may be let go first) to see it end: install_drag_binds_lua,
# unified_cursor.py. Hyprland runs every bind matching a chord, so this takes nothing from the user's binds. These binds
# are never removed, only disarmed: Keybind:remove() would erase the user's on the same chords too.
SUPER_OPS = ('super_left_press', 'super_right_press', 'super_release')
SUPER_BINDS = (('SUPER + mouse:272', 'super_left_press'), ('SUPER + mouse:273', 'super_right_press'))
SUPER_BUTTONS = ('mouse:272', 'mouse:273')
DRAG_TABLE = 'xr_workspace_drag_binds'
DRAG_RUN = 'xr_workspace_drag_run'  # the bind helper's command line for the running run; unset: the Super drag binds are inert
# Typing mode (module docstring): the one run bind that stays while every other bind is released. Ctrl+Alt+T is free in
# Omarchy (it binds Super+Ctrl+Alt+T, a different chord) and in this run's binds; bind_check.py checks the real config.
TYPING_OP = 'typing_toggle'
TYPING_BIND = next(b for b in BINDS if b[1] == TYPING_OP)
# Ctrl+Alt+K: the key bind panel (keybinds.py): every run bind listed with its keys, each one changed live in the glasses,
# conflicts with each other and with Hyprland / Omarchy binds shown. K for keys; Ctrl+Alt+K is free in Omarchy (its Ctrl
# + Alt binds are Delete, Tab and Shift+Tab; Super+Ctrl+K and Super+Alt+K are different chords) and in this run's binds,
# and bind_check.py checks the real config. A run bind like the others: the panel can change it too.
BINDS_PANEL_OP = 'binds_toggle'
# Ctrl+Alt+A (anchor-mode cursor switch: animate / jump, `cursor_switch_animate`) was removed with maximize-follow
# (2026-11-03): a maximized desktop is never anchor, so nothing read it. Its bind, help row, toasts and saved setting are
# gone; a `cursor_switch_animate` still in a saved layout.json or profile is ignored (load_settings, profiles._view_part).
# Ctrl+Alt+C: hides / shows the VR video playback menu (the controls card, video.VideoControl.menu_hidden). Hidden, the
# card no longer pops up when a video opens or on a play / seek / format key; Up / Down and Ctrl+Alt+H still show it on
# demand, and a playback error still shows it (the card says why). Shown, the card appears at once if a video plays.
# Per run, with a toast. Not an op starting video_ (ipc.decode_control routes those to video.validate_request). Ctrl+Alt+H
# is the help card, and Ctrl+Alt+M must reach the focused window (docs/placement-menu-test-plan.md G38). Ctrl+Alt+C is free
# in Omarchy and this run's binds; bind_check.py checks the real config.
PLAYBACK_MENU_OP = 'playback_menu_toggle'
PLAYBACK_MENU_TOASTS = {True: 'VR video menu hidden: it no longer pops up on play or seek. Ctrl+Alt+C shows it again.',
                        False: 'VR video menu shown: it pops up on play and seek. Ctrl+Alt+C hides it.'}
# Ctrl+Alt+R: reset every desktop to the default three flat ones and replace the saved setup with them (module docstring,
# ViewControl.layout_reset). R for reset, as the open panel's bare R puts one desktop back flat. The help card shows it on
# the recenter row (HELP_PAIRS). Ctrl+Alt+R is free in
# Omarchy (its Ctrl+Alt binds are Delete, Tab and Shift+Tab; Super+Ctrl+R, Super+Ctrl+Alt+R and Super+Shift+Ctrl+R are
# its reminder keys, other chords) and in this run's binds; bind_check.py checks the real config.
RESET_OP = 'layout_reset'
RESET_ARM_S = 3.0   # the reset's second press must come this soon after the first (like desktops.REMOVE_ARM_S)
RESET_ANIM_S = 0.45  # each stage of the reset eases this long (smootherstep; spike/include/xr/view.hpp kTileEaseMs too)
RESET_TOASTS = {'confirm': 'Press Ctrl+Alt+R again to reset all monitors',
                'done': 'All monitors reset to three flat 1920x1080 desktops at 100%.',
                'moved': " {source}'s windows are now on {target}.",
                'video': 'Close the VR video first (Esc), then Ctrl+Alt+R resets the desktops.',
                'busy': 'A desktop change is still running; press Ctrl+Alt+R again in a moment.',
                'failed': 'Desktop reset stopped: {error}'}
# Ctrl+Alt+V in the overview (maximize-follow): nothing to change, the overview is always anchored. Says why, once per press.
MODE_OVERVIEW_TOAST = ('The overview always stays fixed in the room (anchor). Maximize a desktop (select it, then Ctrl+Alt+Up), '
                       'then Ctrl+Alt+V switches it between follow and locked; your choice is kept until XR restarts.')
# The settings panel's Place tab while a desktop is maximized: the change waits for the overview (ViewState.hold_placements).
PLACE_HELD_TOAST = 'Placement change kept: it applies when you go back to the overview.'
# Ctrl+Alt+H help card: what each bind does (every op in BINDS needs an entry; tests enforce it).
HELP_ACTIONS = {'zoom_in': 'Maximize the selected desktop', 'zoom_out': 'Overview: any desktop; pills, bar, corners',
                'prev': 'Previous desktop', 'next': 'Next desktop', 'spacing_down': 'Less space between desktops',
                'spacing_up': 'More space between desktops', 'recenter': 'Recenter the view',
                RESET_OP: 'Reset to 3 flat desktops (press twice)',
                'mode_next': 'Maximized: follow / locked', 'prediction': 'Toggle pose prediction',
                'help': 'Help; Ctrl+Alt+G/B: window to glasses/back',
                'typing_toggle': 'Typing mode: XR keys off / on',
                'menu_toggle': 'Placement pill; R in it resets, levels', 'video_open': 'Play a 180/360 VR video',
                'video_paste': 'Play the browser video you copied', 'help_prev': 'Previous help page',
                'help_next': 'Next help page', PRIVACY_OP: 'Privacy: main display off / on',
                PLAYBACK_MENU_OP: 'VR video: hide / show playback controls',
                BINDS_PANEL_OP: 'Key binds: change keys, see conflicts',
                ENVIRONMENT_OP: 'Background: none / last used'}
# Placement pills: the panel's actions (shared contract with the renderer and tests; `menu_*` names kept from the
# placement menu the pills replaced) and the modal binds present only while a panel is open.
MENU_OPS = ('menu_toggle', 'menu_desktop_next', 'menu_desktop_prev', 'menu_field_next', 'menu_field_prev', 'menu_inc', 'menu_dec',
            'menu_inc_big', 'menu_dec_big', 'menu_reset', 'menu_back', 'menu_ultrawide', 'menu_add', 'menu_delete')
# The open panel's desktop row on keys (desktops.ACTIONS): the same as pressing its buttons.
MENU_DESKTOP_OPS = {'menu_ultrawide': 'ultrawide', 'menu_add': 'add', 'menu_delete': 'remove'}
# menu_back (Escape) collapses the open panel; Tab / Shift+Tab move it to the next / previous desktop (only one is open).
# Remove is on Shift+Delete, not Delete: bare Delete is the desktop's push-to-talk here, and collapsing the panel would erase it
# (Keybind:remove() takes every bind on the same modifiers and key).
MENU_BINDS = (('up', 'menu_field_prev'), ('down', 'menu_field_next'), ('left', 'menu_dec'), ('right', 'menu_inc'),
              ('SHIFT + left', 'menu_dec_big'), ('SHIFT + right', 'menu_inc_big'), ('Tab', 'menu_desktop_next'),
              ('SHIFT + Tab', 'menu_desktop_prev'), ('R', 'menu_reset'), ('Escape', 'menu_back'),
              ('U', 'menu_ultrawide'), ('N', 'menu_add'), ('SHIFT + Delete', 'menu_delete'))
# Key-only ops that do nothing in typing mode: their binds are gone, so they can only come from a stale or racing bind.
# (The overview free pointer, and with it every pill click and slider, is off in typing mode: OverviewPointer.overview.)
IGNORED_WHILE_TYPING = MENU_OPS + SEND_OPS + (PRIVACY_OP, PLAYBACK_MENU_OP, BINDS_PANEL_OP, 'binds_key', ENVIRONMENT_OP)
MAX_POINTER_PX = 8192      # spike/include/xr/ipc.hpp kMaxPointerPx (the overview free pointer's dot, canvas px)
# (small, Shift) step per field, in the field's own units (geometry.PLACEMENT_BOUNDS); results are clamped to the bounds.
MENU_STEPS = {'x': (2, 150), 'y': (2, 100), 'distance': (5, 25), 'curvature': (2, 20)}
MENU_SAVE_DELAY = 0.5  # seconds after the last panel change (key, slider or pill drag) before the profile is written
# Bumping (bump.py): a discrete edit (a panel key, a slider press) that pushed other desktops out of the way, and a layout
# change that pushed desktops apart, ask the renderer to glide every desktop there together over GLIDE_MS, along one
# smootherstep curve, instead of its quick exponential easing (drags stream their updates and keep that; an edit that
# pushed nothing moves as it always did). spike/include/xr/ipc.hpp
# kMaxPlacementGlideMs bounds the wire (MAX_GLIDE_MS).
GLIDE_MS = 240
MAX_GLIDE_MS = 1000
# The help card and an open placement panel close by themselves after this many seconds without input (a run key, a
# free-pointer click or slider, pointer motion while a panel is open, or a panel placement change), so static text does
# not burn into the glasses' micro-OLED panels. Change it here; the
# help card's footer note repeats it from spike/include/xr/hud.hpp kOverlayIdleCloseS (a test keeps them equal).
OVERLAY_IDLE_CLOSE_S = 30
KEY_NAMES = {'CTRL': 'Ctrl', 'ALT': 'Alt', 'SHIFT': 'Shift', 'SUPER': 'Super', 'up': 'Up', 'down': 'Down', 'left': 'Left',
             'right': 'Right', 'bracketleft': '[', 'bracketright': ']', 'space': 'Space', 'Tab': 'Tab', 'Escape': 'Esc',
             'Page_Up': 'PgUp', 'Page_Down': 'PgDn', 'Return': 'Enter', 'BackSpace': 'Backspace', 'Delete': 'Del',
             'Insert': 'Ins', 'comma': ',', 'period': '.', 'slash': '/', 'semicolon': ';', 'apostrophe': "'", 'minus': '-',
             'equal': '=', 'grave': '`', 'backslash': '\\',  # keys the bind panel can give a bind (keybinds.BINDABLE)
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
HELP_SECTIONS = (('zoom_in', 'zoom_out', 'prev', 'spacing_down', 'recenter'),          # moving around the desktops
                 ('mode_next', 'prediction', 'typing_toggle', PRIVACY_OP, ENVIRONMENT_OP),  # modes
                 ('menu_toggle', 'video_open'),                                          # placement and VR video
                 ('help', 'help_prev'))                                                  # this card (and the key bind panel)


# Bind pairs shown as one help-card row, which keeps the card short. first op -> (second op, action); the second may be a
# tuple of ops, all on the one row ('Ctrl+Alt+O / U / C').
HELP_PAIRS = {'prev': ('next', 'Previous / next desktop'),  # shares a row so the background's keeps the card at its roomiest text
              'spacing_down': ('spacing_up', 'Less / more space between desktops'),
              # Ctrl+Alt+R shares the recenter row (both put things back) for the same reason as Ctrl+Alt+C below
              'recenter': (RESET_OP, 'Recenter / reset to 3 flat desktops'),
              # docs/web-video.md; Ctrl+Alt+C shares the row, so the card keeps its row count (help_card_test: roomiest text)
              'video_open': (('video_paste', PLAYBACK_MENU_OP), 'VR video: file / web link / hide controls'),
              'help_prev': ('help_next', 'Help: previous / next page'),
              # The key bind panel shares the Ctrl+Alt+H row ('Ctrl+Alt+H / K'), so the card keeps its row count and its
              # roomiest text (help_card_test). _help_action writes this text with the run's send-window keys.
              'help': (BINDS_PANEL_OP, 'Help/binds; Ctrl+Alt+G/B: window to XR/back')}


def _pair_seconds(op):
    """The ops sharing `op`'s help row after it (HELP_PAIRS), as a tuple."""
    second = HELP_PAIRS[op][0]
    return second if isinstance(second, tuple) else (second,)


def _short_pair(a, b):
    """'Ctrl+Alt+G' and 'Ctrl+Alt+B' -> 'Ctrl+Alt+G/B' (the second without the modifiers they share)."""
    prefix = a[:a.rfind('+') + 1]
    return f'{a}/{b[len(prefix):]}' if b.startswith(prefix) else f'{a}/{b}'


def _help_action(send_binds):
    """The Ctrl+Alt+H / K row's text (HELP_PAIRS['help']), naming the send-window keys as `send_binds` (the key bind
    panel's, None: send.SEND_BINDS) have them."""
    from .send import SEND_BINDS
    default = HELP_PAIRS['help'][1]
    if send_binds is None or tuple(send_binds) == SEND_BINDS: return default
    keys = {o: chord_label(c) for c, o in send_binds}
    if 'send_glasses' not in keys or 'send_back' not in keys: return default
    text = f"Help/binds; {_short_pair(keys['send_glasses'], keys['send_back'])}: window to XR/back"
    return text if len(text) <= 48 else 'Help/binds (window keys: see key binds)'


def _help_sections(binds=None, send_binds=None):
    """[[row, ...], ...]: the help card's rows grouped by HELP_SECTIONS (empty sections dropped). `binds` / `send_binds`:
    the run's keys when the key bind panel changed them (keybinds.py); None: BINDS / send.SEND_BINDS."""
    binds = BINDS if binds is None else tuple(binds)
    chords = {op: chord for chord, op in binds}
    seconds = {s for op in HELP_PAIRS for s in _pair_seconds(op)}

    def row(op):
        if op in HELP_PAIRS:
            a = chord_label(chords[op])
            prefix = a[:a.rfind('+') + 1]  # 'Ctrl+Alt+[' and 'Ctrl+Alt+]' -> 'Ctrl+Alt+[ / ]'
            label = a
            for second in _pair_seconds(op):
                if second not in chords: continue
                b = chord_label(chords[second])
                label += f' / {b[len(prefix):]}' if b.startswith(prefix) else f' / {b}'
            return [label, _help_action(send_binds) if op == 'help' else HELP_PAIRS[op][1]]
        return [chord_label(chords[op]), HELP_ACTIONS[op]]
    placed = {op for section in HELP_SECTIONS for op in section}
    sections = [[row(op) for op in section if op in chords] for section in HELP_SECTIONS]
    rest = [row(op) for _, op in binds if op not in placed and op not in seconds]
    if rest: sections[-1] += rest
    if EXTRA_HELP_ROWS: sections.append([list(r) for r in EXTRA_HELP_ROWS])
    return [s for s in sections if s]


def help_rows(binds=None, send_binds=None):
    return [row for section in _help_sections(binds, send_binds) for row in section]


def help_sections(binds=None, send_binds=None):
    """Row count of each help card section, in order (the `help` frame's `sections`; they add up to help_rows())."""
    return [len(section) for section in _help_sections(binds, send_binds)]


def help_frame(run_id, session, request_id, visible, turn=0, binds=None, send_binds=None):
    """Renderer `help` frame: the card shown or hidden with every row and its sections; `turn` -1 / 1 flips a shown
    card to the previous / next page (the renderer knows how many pages fit and wraps round). `binds` / `send_binds`:
    the run's keys (help_rows)."""
    if type(turn) is not int or turn not in (-1, 0, 1): raise ValueError('invalid help turn')
    payload = {'visible': bool(visible), 'rows': help_rows(binds, send_binds), 'sections': help_sections(binds, send_binds)}
    if turn: payload['turn'] = turn
    return (json.dumps({'schema': 1, 'request_id': request_id, 'run_id': run_id, 'session': session, 'op': 'help',
                        'payload': payload}, separators=(',', ':')) + '\n').encode()
CONFIGRELOADED = re.compile(r'configreloaded>>')  # Omarchy theme switches reload the Hyprland config
# What changes where windows are: the window memory reads Hyprland again (window_memory.py). Not titles or focus: they change
# all the time, and the run's end reads those anyway.
WINDOW_EVENT = re.compile(r'(?:openwindow|closewindow|movewindow|movewindowv2|changefloatingmode|fullscreen|workspace|workspacev2|'
                          r'moveworkspace|moveworkspacev2|renameworkspace)>>')
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
MANAGED = re.compile(r'XR-[1-4]\Z')  # geometry.MAX_DESKTOPS
SAFE_PATH = re.compile(r'/[A-Za-z0-9_./:-]+\Z')
SAFE_TOKEN = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z')


def desktops_frame(run_id, session, request_id, names, renew=(), ease=False):
    """Renderer `desktops` frame (spike/include/xr/ipc.hpp DesktopsPayload): the managed outputs to capture from now on,
    the ones whose capture restarts (reshaped) and, for Ctrl+Alt+R, `ease`: the tiles ease to their new layout."""
    names, renew = list(names), list(renew)
    if (not 1 <= len(names) <= MAX_DESKTOPS or len(set(names)) != len(names) or any(not MANAGED.match(n) for n in names)
            or len(set(renew)) != len(renew) or any(n not in names for n in renew)):
        raise ValueError('invalid desktops')
    payload = {'outputs': sorted(names)}
    if renew: payload['renew'] = sorted(renew)
    if ease: payload['ease'] = True
    return (json.dumps({'schema': 1, 'request_id': request_id, 'run_id': run_id, 'session': session, 'op': 'desktops',
                        'payload': payload}, separators=(',', ':')) + '\n').encode()


def placement_frame(run_id, session, request_id, placements, glide=None):
    """Renderer `placement` frame: one placement per desktop. `glide` (ms, 1..MAX_GLIDE_MS): every desktop slides to its
    new placement along one smootherstep curve over that time, together (spike view.hpp glide_toward), instead of the
    renderer's quick exponential easing; a discrete edit that bumped other desktops out of the way asks for it."""
    from .geometry import validate_placement
    if not isinstance(placements, list) or not 1 <= len(placements) <= MAX_DESKTOPS: raise ValueError('invalid placements')
    payload = {'desktops': [validate_placement(p) for p in placements]}
    if glide is not None:
        if type(glide) is not int or not 1 <= glide <= MAX_GLIDE_MS: raise ValueError('invalid glide')
        payload['glide'] = glide
    return (json.dumps({'schema': 1, 'request_id': request_id, 'run_id': run_id, 'session': session, 'op': 'placement',
                        'payload': payload}, separators=(',', ':')) + '\n').encode()


DEFAULT_RESET_PLACEMENT = {'x': 0, 'y': 0, 'distance': 100, 'curvature': 0}  # geometry.DEFAULT_PLACEMENT (a test compares)
# Where a desktop the reset will remove eases to: down under your feet at half size, out of the view, so it leaves rather
# than vanishes (geometry.PLACEMENT_BOUNDS' limits).
RESET_LEAVING_PLACEMENT = {'x': 0, 'y': -900, 'distance': 200, 'curvature': 0}


def smootherstep(t):
    """0..1 -> 0..1, eased in and out (the renderer's view animations use the same curve, spike/src/view.cpp)."""
    t = min(max(float(t), 0.0), 1.0)
    return t * t * t * (t * (6 * t - 15) + 10)


def eased_to(p, to, e):
    """Placement `p` a fraction `e` (0..1) of the way to placement `to`, whole units: Ctrl+Alt+R's ease. The turn round
    you (x) goes the short way, as the renderer's own easing does (a desktop behind you comes round)."""
    out = {}
    for key, b in to.items():
        a = p[key]
        if key == 'x':
            turn = (b - a + 1800) % 3600 - 1800  # shortest signed turn, tenths of a degree (orbit._diff)
            v = int(round(a + turn * e))
            out[key] = (v + 1800) % 3600 - 1800 if not -1800 <= v <= 1800 else v  # orbit.wrap
        else: out[key] = int(round(a + (b - a) * e))
    return out


# The Scale dropdown's steps in percent: geometry.SCALES (mirrored by spike/include/xr/glass.hpp kScalePercents and the
# broker's spike/ownership/registry.cpp validDesktop; tests compare them).
SCALE_PERCENTS = (100, 125, 150, 175, 200, 250, 300)


def scale_percent(scale):
    return int(round(scale * 100))


def placement_menu_frame(run_id, session, request_id, menu):
    """Renderer `placement_menu` frame: open + desktop = that desktop's pill is expanded into its placement panel (open
    false: every pill collapsed), field = the highlighted setting, placements = what the panel shows (one per desktop).
    Optional `surface`, open panels only: while the free pointer holds one of its sliders, the placement the panel is
    drawn on (its desktop's at the press; spike/include/xr/ipc.hpp PlacementMenuPayload). Optional desktop row: `names`
    (each desktop's XR-n number, increasing), `wide` (the open desktop is ultrawide), `armed` (Remove asks for a second
    press), `busy` (a desktop change is in progress). Optional Scale dropdown: `scale` (the open desktop's output scale
    in percent, one of SCALE_PERCENTS), `scales` (the percents it can take, increasing), `picker` (its list is open; open
    panels only). Optional Resolution dropdown: `resolution` (the open desktop's [width, height] in pixels, a size the
    broker accepts), `resolution_picker` (its list is open; open panels only, never with `picker`). Optional background
    (environment.py): `background` (a name), `brightness` (percent), `background_picker` (its list is open; open panels
    only, one list at a time); `field` may be environment.BRIGHTNESS_FIELD."""
    from .geometry import PLACEMENT_KEYS, validate_desktop, validate_placement, validate_placements
    keys = {'open', 'desktop', 'field', 'placements'}
    if not isinstance(menu, dict) or not keys <= set(menu) or \
            set(menu) - keys - {'surface', 'names', 'wide', 'armed', 'busy', 'scale', 'scales', 'picker', 'resolution',
                                'resolution_picker', 'background', 'brightness', 'background_picker'}:
        raise ValueError('invalid menu')
    placements = validate_placements(menu['placements'])
    if type(menu['open']) is not bool or type(menu['desktop']) is not int or not 0 <= menu['desktop'] < len(placements) \
            or menu['field'] not in PLACEMENT_KEYS + (BRIGHTNESS_FIELD,) or ('surface' in menu and menu['open'] is not True):
        raise ValueError('invalid menu')
    payload = {'open': menu['open'], 'desktop': menu['desktop'], 'field': menu['field'], 'placements': placements}
    if 'surface' in menu: payload['surface'] = validate_placement(menu['surface'])
    if 'names' in menu:
        names = menu['names']
        if (not isinstance(names, list) or len(names) != len(placements) or any(type(n) is not int for n in names)
                or any(not 1 <= n <= MAX_DESKTOPS for n in names) or names != sorted(set(names))):
            raise ValueError('invalid menu')
        payload['names'] = list(names)
    for key in ('wide', 'armed', 'busy', 'picker', 'resolution_picker', 'background_picker'):
        if key in menu:
            if type(menu[key]) is not bool: raise ValueError('invalid menu')
            payload[key] = menu[key]
    lists = [bool(payload.get(k)) for k in ('picker', 'resolution_picker', 'background_picker')]
    if any(lists) and menu['open'] is not True: raise ValueError('invalid menu')
    if sum(lists) > 1: raise ValueError('invalid menu')  # one list at a time
    if 'resolution' in menu:
        size = menu['resolution']
        if not isinstance(size, list) or len(size) != 2: raise ValueError('invalid menu')
        try: validate_desktop({'width': size[0], 'height': size[1], 'scale': 1.0})
        except ValueError: raise ValueError('invalid menu') from None
        payload['resolution'] = list(size)
    if 'background' in menu:
        if not isinstance(menu['background'], str) or menu['background'] not in _environment.NAMES: raise ValueError('invalid menu')
        payload['background'] = menu['background']
    if 'brightness' in menu:
        lo, hi = _environment.BRIGHTNESS_BOUNDS
        if type(menu['brightness']) is not int or not lo <= menu['brightness'] <= hi: raise ValueError('invalid menu')
        payload['brightness'] = menu['brightness']
    if 'scale' in menu:
        if type(menu['scale']) is not int or menu['scale'] not in SCALE_PERCENTS: raise ValueError('invalid menu')
        payload['scale'] = menu['scale']
    if 'scales' in menu:
        scales = menu['scales']
        if not isinstance(scales, list) or any(type(v) is not int or v not in SCALE_PERCENTS for v in scales) \
                or scales != sorted(set(scales)):
            raise ValueError('invalid menu')
        payload['scales'] = list(scales)
    return (json.dumps({'schema': 1, 'request_id': request_id, 'run_id': run_id, 'session': session, 'op': 'placement_menu',
                        'payload': payload}, separators=(',', ':')) + '\n').encode()


def warp_cursor_lua(x, y):
    """Put the cursor at layout pixel (x, y): the overview free pointer parks its hidden cursor (and re-centres it, so it
    never stops at a screen edge) and hands it back where the pointer re-enters a desktop."""
    if any(type(v) is not int or not -1_000_000 <= v <= 1_000_000 for v in (x, y)): raise ValueError('invalid cursor position')
    return f'pcall(function() hl.dispatch(hl.dsp.cursor.move({{ x = {x}, y = {y} }})) end)'


class ViewState:
    def __init__(self, names, spacing=DEFAULT_SPACING, prediction=True, placements=None, video_quality=None, environment=None):
        if not names or len(names) > MAX_DESKTOPS or any(not MANAGED.match(n) for n in names): raise ValueError('managed outputs required')
        if type(prediction) is not bool: raise ValueError('invalid prediction')
        # Every run starts in the overview, anchored (maximize-follow): `zoomed` is the maximized state, and the viewing
        # mode follows from it (the `mode` property). There is no saved mode to start in any more.
        self.names, self.zoomed, self.selected = list(names), False, 0
        self.held = {}  # hold_placements: desktop name -> the placement the panel asked for while maximized
        self.spacing = min(max(int(spacing), 0), MAX_SPACING)
        self.prediction = prediction
        # The maximized desktop's mode (MAXIMIZED_MODES). Ctrl+Alt+V toggles it while maximized and it sticks: every later
        # maximize in this run uses it. Never saved: each run (each XR start) begins with follow.
        self.maximized_mode = MAXIMIZED_MODES[0]
        # VR video quality (quality.py: read it with quality.video_quality); changed by the video's quality menu and
        # saved with the view settings. Missing or invalid settings fall back to quality.DEFAULTS.
        from .quality import clean
        self.video_quality = clean(video_quality)
        # The background (environment.py: read it with environment.environment); saved with the view settings. Its dropdown's
        # list is open (like scale_picker, never saved, and any collapse or switch of the open panel closes it).
        self.environment = _environment.clean(environment)
        self.background_picker = False
        from .geometry import DEFAULT_PLACEMENT, validate_placement
        # One per managed output, in order (a saved profile holds one per desktop; extra entries are ignored).
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
        # The panel's desktop row (desktops.py): which desktops are ultrawide (one per name), Remove armed (a second press
        # removes; any collapse or switch of the open panel disarms it), a desktop change in progress.
        self.wide = [False] * len(self.names)
        self.remove_armed = False
        self.desktops_busy = False
        # The Scale dropdown: each desktop's size and output scale (geometry desktops, one per name; None: unknown, as
        # in runs without a desktop manager, and the dropdown is not shown), and its list open (like remove_armed, any
        # collapse or switch of the open panel closes it).
        self.sizes = None
        self.scale_picker = False
        # The Resolution dropdown above it (it shows the same `sizes`): its list open, never together with the Scale list.
        self.resolution_picker = False

    def numbers(self):
        """Each desktop's XR-n number, in order."""
        return [int(n.split('-', 1)[1]) for n in self.names]

    def set_desktops(self, names, placements, wide, focus=None, sizes=None):
        """The run's desktops changed (desktops.py). The selection stays on its desktop if it is still there (else the
        nearest one left); `focus` (an index into `names` or None): the panel opens there, unless maximized (panels are
        the overview's), else it stays on its desktop if that is still there, else it collapses. Remove is disarmed and
        the Scale and Resolution lists closed. `sizes`: the desktops' sizes and scales (one per name; None: unknown)."""
        from .geometry import validate_placement
        names = list(names)
        if not names or len(names) > MAX_DESKTOPS or any(not MANAGED.match(n) for n in names): raise ValueError('managed outputs required')
        if len(placements) != len(names) or len(wide) != len(names): raise ValueError('one placement per desktop required')
        if sizes is not None and len(sizes) != len(names): raise ValueError('one size per desktop required')
        before = self.names
        selected = before[self.selected] if self.selected < len(before) else None
        open_on = before[self.menu_desktop] if self.menu_open and self.menu_desktop < len(before) else None
        self.names = names
        self.placements = [validate_placement(p) for p in placements]
        self.wide = [bool(w) for w in wide]
        self.sizes = None if sizes is None else [dict(d) for d in sizes]
        self.selected = names.index(selected) if selected in names else min(self.selected, len(names) - 1)
        if focus is not None and 0 <= focus < len(names) and not self.zoomed:
            self.menu_open, self.menu_desktop = True, focus
        elif open_on in names:
            self.menu_desktop = names.index(open_on)
        else:
            self.menu_open, self.menu_desktop = False, min(self.menu_desktop, len(names) - 1)
        self.menu_surface = None
        self.remove_armed = self.scale_picker = self.resolution_picker = self.background_picker = False

    def menu(self):
        """`placement_menu` payload: one placement per desktop and the desktop row (names, wide, armed, busy), plus
        `surface` while a slider of the open panel is held."""
        desktop = min(self.menu_desktop, len(self.names) - 1)
        out = {'open': self.menu_open, 'desktop': desktop, 'field': self.menu_field,
               'placements': [dict(p) for p in self.placements], 'names': self.numbers(),
               'wide': bool(self.wide[desktop]) if desktop < len(self.wide) else False,
               'armed': self.menu_open and self.remove_armed, 'busy': self.desktops_busy}
        size = self.size(desktop)
        if size is not None:
            from .geometry import valid_scales
            out['scale'] = scale_percent(size['scale'])
            out['scales'] = [scale_percent(v) for v in valid_scales(size['width'], size['height'])]
            out['picker'] = self.menu_open and self.scale_picker
            out['resolution'] = [size['width'], size['height']]
            out['resolution_picker'] = self.menu_open and self.resolution_picker
        if self.menu_open and self.menu_surface is not None: out['surface'] = dict(self.menu_surface)
        out['background'], out['brightness'] = self.environment['background'], self.environment['brightness']
        out['background_picker'] = self.menu_open and self.background_picker
        return out

    def size(self, desktop):
        """Desktop `desktop`'s size and output scale (a geometry desktop), or None if unknown."""
        if self.sizes is None or len(self.sizes) != len(self.names) or not 0 <= desktop < len(self.sizes): return None
        return self.sizes[desktop]

    def open_menu(self, desktop):
        """Expand `desktop`'s pill into its panel; an open one on another desktop collapses (only one is ever open)."""
        if type(desktop) is not int or not 0 <= desktop < len(self.names): raise ValueError('invalid desktop')
        self.menu_open, self.menu_desktop, self.menu_surface = True, desktop, None
        self.remove_armed = self.scale_picker = self.resolution_picker = self.background_picker = False

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
        # any panel key closes an open list (they cover the sliders the keys adjust)
        self.scale_picker = self.resolution_picker = self.background_picker = False
        if op in MENU_DESKTOP_OPS: return False  # the desktop row: ViewControl.desktop_action, never a placement change
        if op == 'menu_back': op = 'menu_toggle' if self.menu_open else None
        if op == 'menu_toggle':
            if self.menu_open: self.menu_open, self.menu_surface, self.remove_armed = False, None, False
            else: self.open_menu(self.selected)  # the pill of the desktop being looked at
            return False
        if not self.menu_open: return False
        if op in ('menu_desktop_next', 'menu_desktop_prev'):  # the open panel moves to the next / previous desktop's pill
            self.menu_desktop = (self.menu_desktop + (1 if op == 'menu_desktop_next' else -1)) % len(self.names)
            self.menu_surface = None
            self.remove_armed = False
            return False
        if op in ('menu_field_next', 'menu_field_prev'):
            fields = PLACEMENT_KEYS + (BRIGHTNESS_FIELD,)
            i = fields.index(self.menu_field) + (1 if op == 'menu_field_next' else -1)
            self.menu_field = fields[i % len(fields)]
            return False
        current = self.placements[self.menu_desktop]
        if op == 'menu_reset':
            updated = dict(DEFAULT_PLACEMENT)
        elif self.menu_field == BRIGHTNESS_FIELD:  # not a placement: ViewControl._on_menu changes it
            return False
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

    def hold_placements(self, placements):
        """Absolute placements from the settings panel while maximized: they wait for the overview (ViewControl
        _release_placements), where they can be seen, a maximized desktop's view being aimed at it. Only the desktops
        they change are held, by name, so a desktop change meanwhile leaves the others alone. They replace what was held:
        the panel sends every desktop's and shows wanted_placements."""
        from .geometry import validate_placement
        if not isinstance(placements, list) or len(placements) != len(self.names): raise ValueError('one placement per desktop required')
        placements = [validate_placement(p) for p in placements]
        self.held = {n: p for n, p, now in zip(self.names, placements, self.placements) if p != now}

    def wanted_placements(self):
        """The placements with anything held: what the panel shows and what a run ending while maximized saves."""
        return [dict(self.held.get(n, p)) for n, p in zip(self.names, self.placements)]

    @property
    def mode(self):
        """The viewing mode (MODES), from the maximized state: the overview is always anchor; maximized, follow or locked
        (maximized_mode: the last Ctrl+Alt+V choice this run, follow until then). The renderer gets it in every view frame."""
        return self.maximized_mode if self.zoomed else 'anchor'

    def view(self):
        """Renderer payload. `cursor_follow` is always false: the feature is gone, but the renderer's view decoder
        (spike/src/ipc.cpp) still requires the key, so an installed renderer keeps working."""
        return {'zoomed': self.zoomed, 'selected': self.selected, 'spacing': self.spacing, 'mode': self.mode,
                'cursor_follow': False, 'prediction': self.prediction, 'typing': self.typing}

    def settings(self):
        """The saved view settings. `mode` stays for the saved file's schema (profiles.py, an installed panel), always
        'anchor': every run starts in the anchored overview and the saved mode is never read (maximize-follow)."""
        return {'spacing': self.spacing, 'mode': 'anchor', 'prediction': self.prediction,
                'video_quality': dict(self.video_quality), 'environment': dict(self.environment)}

    def apply(self, op):
        """Update state; return the output to focus, or None."""
        if op not in USER_OPS: raise ValueError('unsupported view operation')
        if op in ('recenter', 'help'): return None  # head-tracking reference / overlay only; the view layout is unchanged
        if op == 'mode_next':  # maximized: follow <-> locked; the overview is always anchor (ViewControl says so)
            if self.zoomed:
                self.maximized_mode = MAXIMIZED_MODES[(MAXIMIZED_MODES.index(self.maximized_mode) + 1) % len(MAXIMIZED_MODES)]
            return None
        if op == 'prediction':
            self.prediction = not self.prediction
            return None
        if op == 'zoom_in':  # maximize, in the mode last chosen this run (maximized_mode: sticky, follow at first)
            self.zoomed = True
            return self.names[self.selected]
        if op == 'zoom_out':  # un-maximize: the overview, anchor (the renderer puts the room back as it was)
            self.zoomed = False
            return None
        if op in ('next', 'prev'):
            target = self.selected + (1 if op == 'next' else -1)
            if not 0 <= target < len(self.names): return None
            self.selected = target
            return self.names[target]  # overview: highlight; maximized: a cut straight to it (follow / locked); focused
        self.spacing = min(max(self.spacing + (SPACING_STEP if op == 'spacing_up' else -SPACING_STEP), 0), MAX_SPACING)
        return None


def settings_path():
    base = os.environ.get('XDG_CONFIG_HOME') or str(Path.home() / '.config')
    return Path(base) / 'viture-workspace' / 'layout.json'


DEFAULT_SETTINGS = {'spacing': DEFAULT_SPACING, 'mode': 'anchor', 'prediction': True,
                    'video_quality': dict(_quality.DEFAULTS),  # quality.py owns its defaults
                    'environment': dict(_environment.DEFAULTS)}  # as environment.py does its own


def load_settings(path):
    """Each field validated on its own; anything invalid or missing falls back to its default. A `cursor_follow`
    saved before 2026-11-02 and a `cursor_switch_animate` saved before 2026-11-03 (Ctrl+Alt+A) are ignored (both
    features were removed)."""
    out = {**DEFAULT_SETTINGS, 'video_quality': dict(_quality.DEFAULTS), 'environment': dict(_environment.DEFAULTS)}
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return out
    if not isinstance(data, dict): return out
    if type(data.get('spacing')) is int and 0 <= data['spacing'] <= MAX_SPACING: out['spacing'] = data['spacing']
    if data.get('mode') in MODES: out['mode'] = data['mode']
    if type(data.get('prediction')) is bool: out['prediction'] = data['prediction']
    out['video_quality'] = _quality.video_quality(data)
    out['environment'] = _environment.environment(data)
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
            and type(settings.get('prediction')) is bool and _quality.is_valid(settings.get('video_quality'))
            and _environment.is_valid(settings.get('environment')))


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


def install_binds_lua(python, client, control_socket, run_id, session, typing=False, binds=None):
    """The run's Ctrl+Alt binds; in typing mode only the Ctrl+Alt+T toggle (TYPING_BIND), in the same table. `binds`:
    (chord, op) pairs to install instead of BINDS (the key bind panel's keys, keybinds.BindControl.run_binds)."""
    for p in (python, client, control_socket):
        if not isinstance(p, str) or not SAFE_PATH.match(p): raise ValueError('unsafe path for bind command')
    for t in (run_id, session):
        if not isinstance(t, str) or not SAFE_TOKEN.match(t): raise ValueError('unsafe token for bind command')
    binds = BINDS if binds is None else _checked_binds(binds, {op for _, op in BINDS})
    return _binds_lua('xr_workspace_binds', tuple(b for b in binds if b[1] == TYPING_OP) if typing else binds,
                      f'{python} -I {client} {control_socket} {run_id} {session}')


def _checked_binds(binds, ops):
    """(chord, op) pairs from the key bind panel, re-checked before they go into Lua: known ops, each at most once, and
    chords keybinds.parse_chord writes back unchanged."""
    from .keybinds import parse_chord
    binds = tuple((c, o) for c, o in binds)
    if len({o for _, o in binds}) != len(binds) or any(o not in ops for _, o in binds): raise ValueError('invalid binds')
    for chord, _ in binds:
        if not isinstance(chord, str) or parse_chord(chord) != chord: raise ValueError('invalid binds')
    return binds


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
    """Run end / reinstall: the view binds, the send-window binds, the Super drag binds, an open placement panel's modal
    binds, and an open key bind panel (keybinds.PANEL_OFF_LUA: its key subscription, and its submap left)."""
    from .keybinds import PANEL_OFF_LUA
    return (_remove_lua('xr_workspace_binds') + ' ' + remove_send_binds_lua() + ' ' + remove_drag_binds_lua() + ' ' +
            remove_menu_binds_lua() + ' ' + PANEL_OFF_LUA)


def install_send_binds_lua(python, client, control_socket, run_id, session, binds=None):
    """Send-window binds (send.SEND_BINDS, or `binds`: the key bind panel's); a separate table and eval so the view
    binds' reinstall stays one eval."""
    from .send import SEND_BINDS, SEND_OPS as ops
    for p in (python, client, control_socket):
        if not isinstance(p, str) or not SAFE_PATH.match(p): raise ValueError('unsafe path for bind command')
    for t in (run_id, session):
        if not isinstance(t, str) or not SAFE_TOKEN.match(t): raise ValueError('unsafe token for bind command')
    binds = SEND_BINDS if binds is None else _checked_binds(binds, set(ops))
    return remove_send_binds_lua() + ' ' + _binds_lua('xr_workspace_send_binds', binds,
                                                      f'{python} -I {client} {control_socket} {run_id} {session}')


def remove_send_binds_lua():
    return _remove_lua('xr_workspace_send_binds')


def install_drag_binds_lua(python, client, control_socket, run_id, session):
    """The run's view of Super drags (SUPER_BINDS): non-consuming, beside the user's own binds, in their own table, in
    typing mode too (they take no key, and the drags work there). With
    the real cursor on a desktop, a press tells the run (helper op) and the buttons' next release, whatever the modifiers
    are by then, does too; other releases run nothing, so a plain click spawns no helper. With the dot free (the grab's
    table is set) nothing is under the dot, but Hyprland has just started dragging whatever is under the parked cursor:
    a 1 ms timer ends that drag (outside a bind hl.dsp.window.drag() is the drag's release) and the run hears nothing.
    pcall'd: a refused bind is left out.

    Never removed: Hyprland's Keybind:remove() erases every bind with the same modifiers and key, in every submap
    (keybinds.py), so taking back `SUPER + mouse:272` would delete the user's own Move window with it. They are made once
    per Hyprland config (a reload drops them, and their handles then read expired) and this only arms them: DRAG_RUN is
    the helper's command line, and without it (remove_drag_binds_lua) they do nothing. The release binds are on the Super
    chords too, with ignore_mods: on bare buttons they would go whenever the free pointer's or input lock's binds on
    those buttons do."""
    from .resize import GRAB_TABLE
    for p in (python, client, control_socket):
        if not isinstance(p, str) or not SAFE_PATH.match(p): raise ValueError('unsafe path for bind command')
    for t in (run_id, session):
        if not isinstance(t, str) or not SAFE_TOKEN.match(t): raise ValueError('unsafe token for bind command')
    presses = ' '.join(f'bind("{chord}", press("{op}"), {{ non_consuming = true }})' for chord, op in SUPER_BINDS)
    releases = ' '.join(f'bind("SUPER + {b}", release, {{ release = true, non_consuming = true, ignore_mods = true }})' for b in SUPER_BUTTONS)
    return (f'{DRAG_RUN} = "{python} -I {client} {control_socket} {run_id} {session} " do local live = false '
            f'for _, b in ipairs({DRAG_TABLE} or {{}}) do if tostring(b) ~= "HL.Keybind(expired)" then live = true end end '
            'if not live then local t, held = {}, false '
            'local function bind(k, f, o) local ok, b = pcall(hl.bind, k, f, o) if ok and b then t[#t + 1] = b end end '
            f'local function press(op) return function() if not {DRAG_RUN} then return end if {GRAB_TABLE} then '
            'hl.timer(function() hl.dispatch(hl.dsp.window.drag()) end, { timeout = 1, type = "oneshot" }) return end '
            f'held = true hl.exec_cmd({DRAG_RUN} .. op) end end '
            f'local function release() if held then held = false if {DRAG_RUN} then hl.exec_cmd({DRAG_RUN} .. "{SUPER_OPS[2]}") end end end '
            f'{presses} {releases} {DRAG_TABLE} = t end end')


def remove_drag_binds_lua():
    """Disarms the Super drag binds; never removes them (install_drag_binds_lua)."""
    return f'{DRAG_RUN} = nil'


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
        self.events = None       # optional Hyprland event socket (config reloads); owned by the caller
        self._event_buffer = b''
        self.border = border     # optional () -> Hyprland getoption reply for general:col.active_border
        self.accent = None       # last theme colour sent to the renderer
        self.on_reload = on_reload  # optional () -> None, run off-loop after Hyprland config reloads
        self.reloads = 0
        self.help_visible = False  # Ctrl+Alt+H card; never saved, starts hidden each run
        self.video_active = False  # a VR video plays in place of the desktops (video.VideoControl sets it)
        self.last_input = clock()  # monotonic time of the last run key / click / panel change (overlay idle close)
        self.placements = placements  # optional store with save(one per desktop); see profiles.PlacementStore
        self.menu_binds = menu_binds  # optional (open: bool) -> None: install/remove the panel's modal binds (compositor call)
        self.clock = clock
        self.save_due = None          # monotonic deadline of a pending debounced placement save
        self.environment_save_due = None  # the same for a Brightness drag (the view settings)
        self._pusher = None           # bump.Pusher of the desktop the panel keys / sliders last moved (kept while it is)
        self._settle_due = False      # the renderer reported a new overview layout: overlaps to push apart (_settle)
        self.bumps = 0                # diagnostics: edits that pushed another desktop out of the way
        self._menu_lock = threading.Lock()
        self._menu_binds_installed = False
        self.menu_keys_off = False    # a press on a desktop gave the keyboard back to the window, the panel staying (desktop_press)
        self._menu_threads = []
        self.overview_pointer = None  # optional free_pointer.OverviewPointer (overview free pointer); set by managed.view_controls
        self.wake = None    # optional socket the supervisor loop also waits on (the free pointer's sampler pokes it)
        self.sender = None  # optional send.WindowSender (Ctrl+Alt+G/1-4/B); set by managed.view_controls
        self.privacy = None  # optional privacy.PrivacyDisplay (Ctrl+Alt+D); set by managed.view_controls
        # The open panel's desktop row (desktops.py): the run's DesktopManager (managed runs only), the supervisor whose
        # guard a change pauses and replaces (attach_supervisor), callbacks told the new output names once a change is
        # done (the free pointer's sampler), and `notify` (text) -> None for a refusal (a desktop notification).
        self.desktops = None
        self.supervisor = None
        self.desktop_listeners = []
        self.notify = None
        self.remove_armed_at = None
        self._desktops_before = None  # names and placements when the change in flight started
        # Ctrl+Alt+R in progress (layout_reset): {'at', 'names', 'from', 'to' (placements), 'rolls', 'stage': 'ease' |
        # 'desktops', 'moved': (source, target) of the removed desktop's windows, or None}.
        self.reset = None
        self.reset_armed_at = None  # the first Ctrl+Alt+R press: a second one within RESET_ARM_S resets
        self.resets = 0  # diagnostics
        self._rolls_seen = None  # (names, rolls) poll last saw: a level bar's change is saved like a placement change
        # optional (typing: bool) -> None: swap the run's bind table between the full set and the Ctrl+Alt+T toggle alone
        # (a compositor call, run off the loop under _menu_lock so it is ordered with the panel's own bind changes).
        self.typing_binds = typing_binds
        self._typing_installed = False  # the bind set last put in place (False: the full set from install_view_binds)
        self.keys = None  # optional keybinds.BindControl (the key bind panel, Ctrl+Alt+K); set by managed.view_controls
        self.windows = None  # optional window_memory.WindowTracker (where each window is); set by managed.view_controls

    @property
    def keys_open(self):
        """The key bind panel is open: it has the keyboard (keybinds.py), and the overview free pointer lets go of the
        mouse meanwhile (free_pointer.OverviewPointer.overview)."""
        return self.keys is not None and self.keys.open

    def _help_binds(self):
        """help_frame's keys: the key bind panel's (every bind, on or off); none without the panel (the defaults)."""
        if self.keys is None: return {}
        km = self.keys.keymap
        from .keybinds import RUN_OPS, SEND_OPS as KEY_SEND_OPS
        return {'binds': tuple((km.chord(op), op) for op in RUN_OPS), 'send_binds': tuple((km.chord(op), op) for op in KEY_SEND_OPS)}

    def attach_supervisor(self, supervisor):
        self.supervisor = supervisor

    def initial_frame(self):
        return self._frame() + self._placement_frame(initial=True) + self._style_frame() + self._environment_frame(initial=True)

    def _placement_frame(self, initial=False, glide=False):
        from .geometry import DEFAULT_PLACEMENT
        # The renderer starts flat; an all-default start needs no frame.
        if initial and all(p == DEFAULT_PLACEMENT for p in self.state.placements): return b''
        frame = placement_frame(self.run_id, self.session, self.next_id, self.state.placements, glide=GLIDE_MS if glide else None)
        self.next_id += 1
        return frame

    def _environment_frame(self, initial=False):
        """The background's setting for the renderer; a run's start sends it only if there is one (the renderer starts with none)."""
        if initial and not _environment.on(self.state.environment): return b''
        frame = _environment.renderer_frame(self.run_id, self.session, self.next_id, self.state.environment)
        self.next_id += 1
        return frame

    def _save_environment(self):
        self.environment_save_due = None
        try: self.settings.save(self.state.settings())
        except (OSError, ValueError): pass  # best effort, like the other view settings: the run keeps going

    def set_environment(self, env):
        """The background changes (Ctrl+Alt+E, the panel, the quality menu's row, the controller): applied live and saved with
        the view settings. The renderer's frames (an open panel shows it too), b'' if nothing changed."""
        env = _environment.validate(env)
        if env == self.state.environment: return b''
        self.state.environment = env
        self._save_environment()
        return self._environment_frame() + (self._menu_frame() if self.state.menu_open else b'')

    def _brightness(self, env):
        """The panel's Brightness row: applied live, saved after MENU_SAVE_DELAY (a drag sends one per pointer move) and at once
        when flush() comes (a release, a collapse). Nothing with no background: the row is M3 disabled. Frames (bytes)."""
        if not _environment.on(self.state.environment) or env == self.state.environment: return b''
        self.state.environment = env
        self.environment_save_due = self.clock() + MENU_SAVE_DELAY
        return self._environment_frame()

    def _overview_layout(self):
        """The renderer's overview layout (free_pointer.parse_overview_geometry) while it has one slot per desktop, else
        None (none yet, or stale after a desktop change): what bumping judges collisions on."""
        op = self.overview_pointer
        g = getattr(op, 'geometry', None)
        return g if g is not None and len(g['tiles']) == len(self.state.placements) else None

    def _bump(self, before, keep=False):
        """A key or slider edit changed one desktop's placement (`before`: every placement until then): it goes there along
        the straight path from where it was, pushing every desktop it runs into out of its way, and stops where there is
        no room (bump.py). `keep`: the same edit goes on (a held slider), so the Pusher keeps which way it pushed each
        desktop. True if another desktop moved. No layout yet: applied as given."""
        from . import bump
        after = self.state.placements
        changed = [i for i, (p, q) in enumerate(zip(before, after)) if p != q]
        g = self._overview_layout()
        if len(changed) != 1 or len(before) != len(after) or g is None:
            self._pusher = None
            return False
        i = changed[0]
        pusher = self._pusher
        if not (keep and pusher is not None and pusher.index == i and pusher.g is g): pusher = bump.Pusher(g, i, self._rolls())
        self.state.placements = pusher.move(before, after[i])
        self._pusher = pusher if keep else None
        if not pusher.pushed: return False
        self.bumps += 1
        return True

    def _settle(self):
        """The layout changed (set_overview_geometry): desktops that overlap now, a restored setup's, or after a spacing or
        desktop change, are pushed apart (bump.settle), the open panel's and the selected desktop staying put, gliding and
        saved like any move. Not under a drag, a desktop change or a layout reset (which places every desktop itself): it
        stays due until they are over. Frames (bytes)."""
        if not self._settle_due: return b''
        from . import bump
        s, op = self.state, self.overview_pointer
        g = self._overview_layout()
        if g is None or getattr(op, 'drag', None) is not None or s.desktops_busy or getattr(self, 'reset', None) is not None:
            return b''
        self._settle_due = False
        placements, moved = bump.settle(g, s.placements, ([s.menu_desktop] if s.menu_open else []) + [s.selected], self._rolls())
        if not moved: return b''
        s.placements = placements
        self.bumps += 1
        self.save_due = self.clock() + MENU_SAVE_DELAY
        return self._placement_frame(glide=True) + (self._menu_frame() if s.menu_open else b'')

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

    def _frame(self, **flags):
        """A view frame, with a cursor switch's flags (cursor_switch_flags) added to the payload."""
        frame = view_frame(self.run_id, self.session, self.next_id, {**self.state.view(), **flags})
        self.next_id += 1
        return frame

    def status_reply(self, status):
        reply = dict(capabilities=list(USER_OPS), view=self.state.view(), status=dict(status),
                     placements=self.state.wanted_placements(), names=list(self.state.names))
        if self.desktops is not None: reply['desktops'] = self.desktops.desktops()  # live sizes (Ultrawide, Add)
        return reply

    def _menu_frame(self):
        frame = placement_menu_frame(self.run_id, self.session, self.next_id, self.state.menu())
        self.next_id += 1
        return frame

    def _menu_update(self, change, keep=False, glide=True):
        """Apply `change` (() -> True if a placement changed) to the pills' state: the desktop it changed goes there
        pushing the desktops it runs into out of its way (_bump; `keep`: a held slider, one edit), then the live placement
        frame (if one changed; saved after MENU_SAVE_DELAY, pushed desktops with it; `glide`: a discrete edit that pushed
        glides there, GLIDE_MS), then the `placement_menu` frame (if anything it shows changed). A panel collapsing saves
        at once; the modal keys follow the panel being open (installed / removed off the loop)."""
        before, was_open, placed = self.state.menu(), self.state.menu_open, list(self.state.placements)
        changed = change()
        out = b''
        if self.state.menu_open and not was_open and self.keys_open: out += self.keys.close()  # their keys would clash
        if changed:
            pushed = self._bump(placed, keep=keep)
            if self.state.placements != placed: out += self._placement_frame(glide=glide and pushed)
            self.save_due = self.clock() + MENU_SAVE_DELAY
        if was_open and not self.state.menu_open: self.flush()  # collapsing saves at once
        if was_open != self.state.menu_open: self._sync_menu_binds()
        if self.state.menu() != before: out += self._menu_frame()
        return out

    def _zoom_out(self):
        """Un-maximize to the overview (the pills and panels are drawn there only; anchor), as Ctrl+Alt+Down does. A view
        frame."""
        if self.overview_pointer is not None: self.overview_pointer.summon()  # a cursor off the desktops comes into one
        self.state.apply('zoom_out')
        return self._frame() + self._release_placements()

    def _release_placements(self):
        """Back in the overview: the Place tab's change held while maximized (ViewState.hold_placements) applies now,
        as given, gliding there as the desktop shrinks, and is saved. Frames, b'' if nothing was waiting."""
        s = self.state
        if s.zoomed or not s.held: return b''
        placements, s.held = s.wanted_placements(), {}
        if not s.set_placements(placements): return b''
        self.save_due = None  # this save also covers any pending pill-panel change
        self._save_placements()
        return self._placement_frame(glide=True)

    def _on_menu(self, op):
        """Panel key op. Ctrl+Alt+L while maximized goes back to the overview first, so the expanding pill is seen."""
        if op in MENU_DESKTOP_OPS:  # U / N / Shift+Delete: the open panel's desktop row
            return (self.desktop_action(self.state.menu_desktop, MENU_DESKTOP_OPS[op]) if self.state.menu_open else b'') or None
        if op in ('menu_inc', 'menu_dec', 'menu_inc_big', 'menu_dec_big') and self.state.menu_open and self.state.menu_field == BRIGHTNESS_FIELD:
            step = _environment.BRIGHTNESS_STEPS[op.endswith('_big')] * (1 if op.startswith('menu_inc') else -1)
            env = self.state.environment
            frames = []

            def change():  # (apply_menu closes an open list)
                frames.append(self._brightness(_environment.brightened(env, env['brightness'] + step)))
                return self.state.apply_menu(op)
            out = self._menu_update(change)
            return (b''.join(frames) + out) or None
        out = self._zoom_out() if op == 'menu_toggle' and not self.state.menu_open and self.state.zoomed else b''
        level = self.state.menu_desktop if op == 'menu_reset' and self.state.menu_open else None
        levelled = b''
        if level is not None and self.overview_pointer is not None:  # R: its rotation back to level too, not only its placement.
            levelled = self.overview_pointer.level(level)  # First: bumping counts the roll, and it should not push for one going away
        out += self._menu_update(lambda: self.state.apply_menu(op))
        return (out + levelled) or None

    # --- the overview free pointer's pill clicks and sliders (free_pointer.OverviewPointer) ------------------------------
    def follow_focus(self):
        """The selection just moved to another desktop in the overview: an open panel goes with it, that desktop's pill
        expanding and the old one collapsing (its changes saved at once, the panel's keys staying), as Tab does. With every
        pill collapsed nothing opens. Renderer frames (bytes), b'' if the panel stayed."""
        s = self.state
        if not s.menu_open or s.zoomed or s.typing or self.video_active or s.selected == s.menu_desktop: return b''
        self.flush()
        return self._menu_update(lambda: s.open_menu(s.selected) or False)

    def desktop_press(self):
        """A left press on a desktop's content with a panel open (unified_cursor: the window has the click). The panel stays
        (Cody, 2026-11-03: clicking another desktop moves it there, so a press on a desktop is no click outside it), on that
        desktop if it was on another, and its modal keys come off: they are plain keys (R, U, N, Shift+Delete, arrows, Tab, Escape),
        and the typing is the window's now. A press on the panel or its pill brings them back (panel_keys_on), and a collapse
        forgets it. Renderer frames (bytes), b'' if the panel stayed where it was."""
        out = self.follow_focus()
        if self.state.menu_open and not self.menu_keys_off:
            self.menu_keys_off = True
            self._sync_menu_binds()
        return out

    def panel_keys_on(self):
        """A press on the open panel or its pill: the panel is in use again, so its modal keys are back (desktop_press)."""
        if not self.menu_keys_off: return
        self.menu_keys_off = False
        self._sync_menu_binds()

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
        release), bumping what it runs into (the press may jump far: it glides; held and dragged it is one edit, one
        Pusher). `surface` (the press): the desktop's placement then, which the renderer draws the panel on until
        pill_release, so the knob stays under the pointer the drag is measured with. Ignored unless `desktop`'s panel is
        the open one. Frames (bytes)."""
        if not self.state.menu_open or self.state.menu_desktop != desktop: return b''
        self.last_input = self.clock()  # a held slider keeps the panel up
        frames = []

        def change():
            if surface is not None: self.state.hold_surface(surface)
            if field != BRIGHTNESS_FIELD: return self.state.slide(field, t)
            self.state.menu_field = field
            frames.append(self._brightness(_environment.slid(self.state.environment, t)))
            return False
        if surface is not None: self._pusher = None  # a new press: a new edit
        out = self._menu_update(change, keep=True, glide=surface is not None)
        return b''.join(frames) + out

    def pill_release(self):
        """The held slider was let go (or its drag ended): the open panel goes back onto its desktop's live placement.
        Frames (bytes), b'' if nothing was held."""
        self._pusher = None  # that slider edit is over
        if self.state.menu_surface is None: return b''
        return self._menu_update(lambda: self.state.hold_surface(None) or False)

    # --- the open panel's background row (environment.py) -------------------------------------------------------------
    def _open_panel(self, desktop):
        s = self.state
        return type(desktop) is int and s.menu_open and s.menu_desktop == desktop and not s.typing and not self.video_active

    def background_box(self, desktop):
        """A free-pointer click on the open panel's Background box: its list opens, or closes if it is open. Frames (bytes)."""
        if not self._open_panel(desktop): return b''
        self.last_input = self.clock()
        s = self.state
        s.background_picker, s.scale_picker, s.resolution_picker, s.remove_armed = not s.background_picker, False, False, False
        return self._menu_frame()

    def background_close(self):
        """A press on the open panel away from the Background list: the list closes. Frames (bytes), b'' if it was closed."""
        if not self.state.background_picker: return b''
        self.state.background_picker = False
        return self._menu_frame()

    def background_pick(self, desktop, name):
        """A click on an item of the open Background list: the list closes and that background fades in. Frames (bytes)."""
        if not self._open_panel(desktop) or not self.state.background_picker: return b''
        self.last_input = self.clock()
        self.state.background_picker = False
        return self.set_environment(_environment.picked(self.state.environment, name)) or self._menu_frame()

    def background_switch(self, desktop):
        """A click on the open panel's switch: as Ctrl+Alt+E. Frames (bytes)."""
        if not self._open_panel(desktop): return b''
        self.last_input = self.clock()
        self.state.background_picker = False
        return self.set_environment(_environment.toggled(self.state.environment))

    # --- the open panel's desktop row (desktops.py) -----------------------------------------------------------------
    def scale_box(self, desktop):
        """A free-pointer click on `desktop`'s open panel's Scale box: its list of scales opens, or closes if it is open.
        Nothing while a desktop change is in progress, or without the run's desktops. Frames (bytes), b'' if nothing."""
        s = self.state
        if self.desktops is None or type(desktop) is not int or not s.menu_open or s.menu_desktop != desktop: return b''
        if s.typing or self.video_active or s.size(desktop) is None: return b''
        self.last_input = self.clock()
        if not s.scale_picker and (s.desktops_busy or self.desktops.busy()): return b''
        s.scale_picker, s.remove_armed, s.resolution_picker, s.background_picker = not s.scale_picker, False, False, False
        return self._menu_frame()

    def scale_close(self):
        """A press on the open panel away from the Scale list: the list closes. Frames (bytes), b'' if it was closed."""
        if not self.state.scale_picker: return b''
        self.state.scale_picker = False
        return self._menu_frame()

    def scale_pick(self, desktop, percent):
        """A click on a step (in percent, SCALE_PERCENTS) of `desktop`'s open Scale list: the list closes and, if the step
        divides the desktop's resolution and is not its scale already, that desktop's Hyprland output takes it, live
        (desktops.SCALE, through desktop_action: guard paused, broker reshape, renderer told, profile saved)."""
        from .geometry import valid_scales
        s = self.state
        if not s.scale_picker or not s.menu_open or s.menu_desktop != desktop: return b''
        size = s.size(desktop)
        s.scale_picker = False
        if size is None or percent not in SCALE_PERCENTS or percent == scale_percent(size['scale']) or \
                percent / 100 not in valid_scales(size['width'], size['height']):
            self.last_input = self.clock()
            return self._menu_frame()  # a dimmed step, or the current one: nothing changes
        return self.desktop_action(desktop, 'scale', scale=percent / 100) or self._menu_frame()

    def resolution_box(self, desktop):
        """A free-pointer click on `desktop`'s open panel's Resolution box: its list of sizes (geometry.RESOLUTIONS) opens,
        or closes if it is open; an open Scale list closes. Nothing while a desktop change is in progress, or without the
        run's desktops. Frames (bytes), b'' if nothing."""
        s = self.state
        if self.desktops is None or type(desktop) is not int or not s.menu_open or s.menu_desktop != desktop: return b''
        if s.typing or self.video_active or s.size(desktop) is None: return b''
        self.last_input = self.clock()
        if not s.resolution_picker and (s.desktops_busy or self.desktops.busy()): return b''
        s.resolution_picker, s.scale_picker, s.background_picker, s.remove_armed = not s.resolution_picker, False, False, False
        return self._menu_frame()

    def resolution_close(self):
        """A press on the open panel away from the Resolution list: the list closes. Frames (bytes), b'' if it was closed."""
        if not self.state.resolution_picker: return b''
        self.state.resolution_picker = False
        return self._menu_frame()

    def resolution_pick(self, desktop, size):
        """A click on a size ((width, height), one of geometry.RESOLUTIONS) of `desktop`'s open Resolution list: the list
        closes and, unless it is the desktop's size already, that desktop's Hyprland output takes it, live and in place
        (desktops.RESOLUTION, through desktop_action: guard paused, broker reshape, renderer told, profile saved). Its scale
        stays if it divides the new size, else it becomes the nearest step that does; the Scale list then offers the
        steps of the new size."""
        from .geometry import RESOLUTIONS
        s = self.state
        if not s.resolution_picker or not s.menu_open or s.menu_desktop != desktop: return b''
        current = s.size(desktop)
        s.resolution_picker = False
        size = tuple(size) if isinstance(size, (tuple, list)) else None
        if current is None or size not in RESOLUTIONS or size == (current['width'], current['height']):
            self.last_input = self.clock()
            return self._menu_frame()  # its size already (or not a listed one): nothing changes
        return self.desktop_action(desktop, 'resolution', size=size) or self._menu_frame()

    def desktop_action(self, desktop, action, scale=None, size=None):
        """Ultrawide / Add / Remove pressed on `desktop`'s open panel (or U / N / Shift+Delete), a Scale step picked (action
        desktops.SCALE with `scale`) or a Resolution picked (desktops.RESOLUTION with `size`). Remove's first press only
        arms it. Starts the change (module docstring) and returns the frames that go before it: the panel showing it busy
        and, for a removal, the renderer's new desktop set (its capture stops before the output goes). b'' if nothing
        starts."""
        from .desktops import ACTIONS, REMOVE_ARM_S, RESOLUTION, SCALE, plan
        s = self.state
        if self.desktops is None or action not in ACTIONS + (SCALE, RESOLUTION) or type(desktop) is not int or \
                not 0 <= desktop < len(s.names): return b''
        if s.typing or self.video_active or not s.menu_open or s.menu_desktop != desktop: return b''
        now = self.last_input = self.clock()
        if s.desktops_busy or self.desktops.busy(): return b''
        s.scale_picker = s.resolution_picker = s.background_picker = False
        if action == 'remove' and not (s.remove_armed and self.remove_armed_at is not None and now - self.remove_armed_at < REMOVE_ARM_S):
            s.remove_armed, self.remove_armed_at = True, now  # "Remove?": a second press removes
            return self._menu_frame()
        s.remove_armed = False
        try: change = plan(self.desktops.specs, s.placements, action, desktop, held=self.desktops.held(), scale=scale, size=size)
        except ValueError as exc:
            self._tell(str(exc))
            return self._menu_frame()
        return self._start_change(change)

    def _start_change(self, change):
        """Start the desktops.Plan `change` (a panel button, or Ctrl+Alt+R's RESET): the frames that go before it."""
        s = self.state
        self.flush()  # a pending panel change is saved first
        self._desktops_before = (list(s.names), [dict(p) for p in s.placements], list(s.wide))
        out = b''
        if change.remove is not None:  # the renderer lets go of that output before the broker removes it
            gone = s.names.index(change.remove['name'])
            keep = [n for n in s.names if n != change.remove['name']]
            out += self._desktops_frame(keep)
            s.set_desktops(keep, [p for i, p in enumerate(s.placements) if i != gone], [w for i, w in enumerate(s.wide) if i != gone],
                           sizes=None if s.sizes is None else [d for i, d in enumerate(s.sizes) if i != gone])
            self._sync_menu_binds()  # its panel collapsed
            out += self._frame() + self._placement_frame()
            self._desktops_changed()
        if self.supervisor is not None: self.supervisor.pause_guard()
        s.desktops_busy = True
        if not self.desktops.start(change):
            s.desktops_busy = False
            if self.supervisor is not None: self.supervisor.replace_guard(self.desktops.guard())
        return out + self._menu_frame()

    def _desktops_frame(self, names, renew=()):
        frame = desktops_frame(self.run_id, self.session, self.next_id, names, renew, ease=self.reset is not None)
        self.next_id += 1
        return frame

    def _desktops_changed(self):
        names = list(self.state.names)
        if self.overview_pointer is not None: self.overview_pointer.desktops_changed()
        if self.sender is not None: self.sender.set_names(names)
        for listener in self.desktop_listeners:
            try: listener(names)
            except (OSError, ValueError, TypeError, KeyError): pass

    def _desktops_done(self, change, error, done):
        """The worker finished `change` (error: why it stopped early, None if it all went through). The run's outputs are
        what the broker reports now: the renderer, the view, the panel, the guard and the profile follow them."""
        from .geometry import DEFAULT_PLACEMENT
        s, manager = self.state, self.desktops
        names_before, placements_before, _ = self._desktops_before or (list(s.names), list(s.placements), list(s.wide))
        self._desktops_before = None
        by_name = dict(zip(names_before, placements_before))
        if error is None: by_name.update(zip([x['name'] for x in change.specs], change.placements))
        names = manager.names()
        placements = [dict(by_name.get(n, DEFAULT_PLACEMENT)) for n in names]
        focus = None
        if error is None and change.focus is not None: focus = names.index(change.specs[change.focus]['name'])
        was_open = s.menu_open
        s.set_desktops(names, placements, manager.wide(), focus, sizes=manager.desktops())
        s.desktops_busy = False
        resized = {n for n in change.resized if n in names}  # a slide along the row keeps its capture
        out = self._desktops_frame(names, sorted(resized)) + self._frame() + self._placement_frame() + self._menu_frame()
        if was_open != s.menu_open: self._sync_menu_binds()
        if self.supervisor is not None: self.supervisor.replace_guard(manager.guard())
        manager.save(s.placements, self._rolls())
        self._desktops_changed()
        if self.windows is not None: self.windows.mark()  # a shrink pulled windows in, a removal moved them
        from .desktops import RESET
        if change.action == RESET and self.reset is not None: return out + self._reset_done(error)  # it says how it went
        if error is not None: self._tell(error)
        return out

    # --- Ctrl+Alt+R: every desktop back to the default three flat ones (module docstring) ----------------------------
    def layout_reset(self):
        """Ctrl+Alt+R. The first press only asks (a toast); a second within RESET_ARM_S resets: an open panel collapses, a
        maximized desktop goes back to the overview (its follow / locked choice stays), the saved setup is replaced by
        the default with the layout it replaces kept as a backup, the spacing goes back to its default (the renderer eases
        it), then every desktop eases flat and level (poll, RESET_ANIM_S), one the plan will remove dropping away, before
        the desktops change (_reset_desktops). Frames (bytes), b'' when it only asks or is ignored (a toast says why)."""
        s = self.state
        if self.reset is not None or s.typing: return b''  # one at a time; in typing mode its key is released (stale)
        if self.video_active:
            self._tell(RESET_TOASTS['video'])
            return b''
        if s.desktops_busy or (self.desktops is not None and self.desktops.busy()):
            self._tell(RESET_TOASTS['busy'])
            return b''
        now = self.clock()
        if self.reset_armed_at is None or now - self.reset_armed_at >= RESET_ARM_S:
            self.reset_armed_at = now
            self._tell(RESET_TOASTS['confirm'])
            return b''
        self.reset_armed_at = None
        out = (self._on_menu('menu_toggle') or b'') if s.menu_open else b''  # saved first, panel keys released
        if s.zoomed: out += self._zoom_out()  # the reset is seen in the overview
        from .desktops import RESET_COUNT, number
        self.save_due = None  # the default replaces anything pending
        rolls = self._rolls() or [0] * len(s.names)
        leaving = sorted(s.names, key=number)[RESET_COUNT:] if self.desktops is not None else []
        self.reset = {'at': now, 'names': list(s.names), 'from': [dict(p) for p in s.placements], 'rolls': rolls, 'stage': 'ease',
                      'to': [RESET_LEAVING_PLACEMENT if n in leaving else DEFAULT_RESET_PLACEMENT for n in s.names], 'moved': None}
        self.resets += 1
        reset_store = getattr(self.placements, 'reset', None)  # profiles.PlacementStore: the saved setup replaced now
        if reset_store is not None:
            live = self.desktops.desktops() if self.desktops is not None else None
            previous = {'desktops': live, 'placements': self.reset['from'], 'rolls': rolls, 'spacing': s.spacing}
            try: reset_store(previous)
            except (OSError, ValueError): pass
        if s.spacing != DEFAULT_SPACING:
            s.spacing = DEFAULT_SPACING
            out += self._frame()
            try:
                if self.settings is not None: self.settings.save(s.settings())
            except (OSError, ValueError): pass
        s.desktops_busy = self.desktops is not None  # the panel's desktop row waits for it
        return out + self._reset_tick(now)

    def _reset_tick(self, now):
        """poll(): the reset's ease, every desktop's placement and roll RESET_ANIM_S along a smootherstep to flat and
        level (the renderer's own easing smooths between ticks); at its end the desktops change."""
        r, s = self.reset, self.state
        if r is None or r['stage'] != 'ease': return b''
        t = 1.0 if s.names != r['names'] or RESET_ANIM_S <= 0 else (now - r['at']) / RESET_ANIM_S
        e = smootherstep(t)
        out = b''
        if s.names == r['names']:
            placements = [eased_to(p, to, e) for p, to in zip(r['from'], r['to'])]
            if placements != s.placements:
                s.placements = placements
                out += self._placement_frame() + (self._menu_frame() if s.menu_open else b'')
            self._set_rolls([int(round(x * (1 - e))) for x in r['rolls']])  # the free pointer's `roll` frame sends them
        if t >= 1: out += self._reset_desktops()
        return out

    def _reset_desktops(self):
        """The ease is done: the desktops themselves become the default three (desktops.RESET: one plan through the
        panel buttons' path), or the reset is done if they already are."""
        from .desktops import RESET, plan
        s = self.state
        self.reset['stage'] = 'desktops'
        self._set_rolls([0] * len(s.names))
        if self.desktops is None: return self._reset_done(None)
        if self.video_active: return self._reset_done('a VR video opened')
        try: change = plan(self.desktops.specs, s.placements, RESET, 0, held=self.desktops.held())
        except ValueError as exc: return self._reset_done(str(exc))
        if not change.steps(): return self._reset_done(None)
        if change.remove is not None and change.windows_to is not None:
            self.reset['moved'] = (change.remove['name'], change.windows_to)
        s.desktops_busy = False  # _start_change sets it while the change runs
        out = self._start_change(change)
        return out if s.desktops_busy else out + self._reset_done('the desktops could not be changed now')

    def _reset_done(self, error):
        """The reset finished (error: why it stopped short, None if it all went through): the live setup is saved for
        the next start (the default, or what a failed step left) and a toast says how it went."""
        s = self.state
        moved, self.reset = self.reset['moved'], None
        s.desktops_busy = False
        self._set_rolls([0] * len(s.names))
        self._rolls_seen = None
        flat = [dict(DEFAULT_RESET_PLACEMENT) for _ in s.names]  # also a desktop left underfoot by a refused removal
        out = b''
        if s.placements != flat:
            s.placements = flat
            out += self._placement_frame()
        self.save_setup()
        if error is not None: self._tell(RESET_TOASTS['failed'].format(error=error))
        elif moved is None: self._tell(RESET_TOASTS['done'])
        else: self._tell(RESET_TOASTS['done'] + RESET_TOASTS['moved'].format(source=moved[0], target=moved[1]))
        return out + (self._menu_frame() if s.menu_open else b'')

    # --- the levelling rolls (the overview pointer's level bars own them, free_pointer.py) --------------------------
    def _rolls(self):
        """Each desktop's levelling roll in state.names order, or None without an overview pointer."""
        p = self.overview_pointer
        if p is None or not hasattr(p, 'rolls'): return None
        by_name = dict(zip(getattr(p, 'roll_names', self.state.names), p.rolls))
        return [int(by_name.get(n, 0)) for n in self.state.names]

    def _set_rolls(self, rolls):
        p = self.overview_pointer
        if p is None or not hasattr(p, 'rolls'): return
        index = {n: i for i, n in enumerate(getattr(p, 'roll_names', self.state.names))}
        for name, roll in zip(self.state.names, rolls):
            if name in index and index[name] < len(p.rolls): p.rolls[index[name]] = roll

    def _watch_rolls(self, now):
        """poll(): a level bar click or drag changed a levelling roll: saved MENU_SAVE_DELAY after the last change, like
        a placement (flush: at once when a drag ends). A desktop change only takes the new names in (it saved them)."""
        rolls = self._rolls()
        if rolls is None: return
        seen, self._rolls_seen = self._rolls_seen, (list(self.state.names), rolls)
        if seen is not None and seen[0] == self.state.names and seen[1] != rolls: self.save_due = now + MENU_SAVE_DELAY

    def save_setup(self):
        """The whole monitor setup for the next start (profiles.py): the run's desktops as the broker has them, their
        placements and rolls. At the run's end (managed.view_controls) and after Ctrl+Alt+R; a reset still running at the
        end saves the default it was going to. Best effort, never raises."""
        store = self.placements
        if store is None: return
        try:
            if self.reset is not None:
                if hasattr(store, 'reset'): store.reset()
                return
            placements, rolls = self.state.wanted_placements(), self._rolls()  # a change still held: the next start's
            live = self.desktops.desktops() if self.desktops is not None else None
            if live is not None and len(live) == len(placements) and hasattr(store, 'save_desktops'):
                store.save_desktops(live, placements, rolls)
            elif rolls is not None and hasattr(store, 'save_layout'): store.save_layout(placements, rolls)
            else: store.save(placements)
        except (OSError, ValueError): pass

    def window_context(self):
        """Each desktop's placement and levelling roll, as the window memory keeps them with it (window_memory.py)."""
        s = self.state
        rolls = self._rolls() or [0] * len(s.names)
        return {name: {'placement': dict(p), 'roll': r} for name, p, r in zip(list(s.names), list(s.placements), rolls)}

    def _tell(self, text):
        if self.notify is None: return
        def work():
            try: self.notify(text)
            except (OSError, ValueError, TypeError): pass
        t = threading.Thread(target=work, daemon=True); t.start(); self._threads.append(t)

    def select_desktop(self, desktop):
        """A circle-cursor (free pointer) click on `desktop` in the overview: select it, and stay in the overview, anchored
        (anchor-no-click-maximize, 2026-11-03: a click never maximizes; Ctrl+Alt+Up and the panel's Maximize do). The
        highlight moves there, as Ctrl+Alt+Left / Right move it. The free pointer then brings the real cursor back onto
        that desktop under the dot, and that warp gives its monitor Hyprland's focus (focus-follows-mouse; no focus thread
        here: Hyprland's monitor focus would warp the cursor away from the dot). An open panel goes to that desktop
        (follow_focus; free_pointer: Clicks, a press on a desktop does not collapse it). Frames (bytes): a view frame and
        the panel's if it moved, b'' when stale (zoomed in, typing mode, a VR video, no such desktop) or nothing changed."""
        if type(desktop) is not int or not 0 <= desktop < len(self.state.names): return b''
        if self.state.zoomed or self.state.typing or self.video_active: return b''  # no free pointer then: a stale click
        if desktop == self.state.selected: return self.follow_focus()  # Tab may have taken the panel elsewhere
        self.state.selected = desktop
        return self._frame() + self.follow_focus()

    def switch_desktop(self, desktop, panned=False):
        """Maximized, the cursor pushed past the selected desktop's edge toward `desktop` (unified_cursor.EdgePush; the
        desktop placed that way round you, neighbors.py): select it. `panned`: the hand's pan got there, and it lands from
        there (edge_pan.py); otherwise a cut. cursor_switch_flags decides. The sampler gives its monitor focus and
        brings the cursor in over the matching edge (no focus thread here: that would warp the cursor to the middle).
        One view frame, or b'' when stale (not maximized, typing mode, a VR video, no such desktop, already selected)."""
        if type(desktop) is not int or not 0 <= desktop < len(self.state.names) or desktop == self.state.selected: return b''
        if not self.state.zoomed or self.state.typing or self.video_active: return b''
        self.last_input = self.clock()
        self.state.selected = desktop
        return self._frame(**self.cursor_switch_flags(panned))

    def pan_frame(self, payload):
        """One `edge_pan` frame (edge_pan.py; None: over). Display only."""
        frame = edge_pan.frame_bytes(self.run_id, self.session, self.next_id, payload)
        self.next_id += 1
        return frame

    def cursor_switch_flags(self, panned=False):
        """The one decision for how a cursor switch looks (spike/include/xr/ipc.hpp ViewPayload). Follow and locked (every
        maximized desktop, maximize-follow): `pan`, landing the hand's pan (`panned`, edge_pan.py); otherwise `cut`, straight
        onto the desktop with no zoom out / turn / zoom in (their aim then centres it). Anchor mode: `center`, the switch
        pans straight to the desktop and lands with it centred (anchor is the overview's mode now, where no cursor switch
        happens)."""
        if self.state.mode == 'anchor': return {'center': True}
        return {'pan': True} if panned else {'cut': True}

    def set_overview_geometry(self, geometry):
        """Supervisor: the renderer's overview layout (free_pointer.parse_overview_geometry), for free-pointer hit testing.
        It only reports one that changed, so desktops may overlap now: poll pushes them apart (_settle)."""
        if self.overview_pointer is not None: self.overview_pointer.set_geometry(geometry)
        self._settle_due = True

    def set_roll_geometry(self, tilts):
        """Supervisor: each desktop's tilt from level (free_pointer.parse_roll_geometry), for the roll-level icons."""
        if self.overview_pointer is not None: self.overview_pointer.set_roll_geometry(tilts)

    def _sync_menu_binds(self, force=False):
        if not self.state.menu_open: self.menu_keys_off = False  # the next panel starts with its keys
        if self.menu_binds is None: return
        # Compositor call: never on the watchdog loop. The worker applies the state current when it runs, serialized,
        # so a fast open/close/open cannot leave the binds out of step with the panel.
        t = threading.Thread(target=self._menu_bind_work, args=(force,), daemon=True); t.start()
        self._threads.append(t); self._menu_threads.append(t)

    def _menu_bind_work(self, force):
        with self._menu_lock:
            binds, want = self.menu_binds, self.state.menu_open and not self.menu_keys_off
            if binds is not None and (want != self._menu_binds_installed or (force and want)):
                try:
                    binds(want)
                    self._menu_binds_installed = want
                except (OSError, ValueError, TypeError, KeyError): pass  # best-effort; Ctrl+Alt+L still collapses the panel

    def poll(self, now=None):
        """Supervisor loop tick: the overview free pointer, a debounced panel placement save once it is due, and closing
        the help card and an open panel once they have been up OVERLAY_IDLE_CLOSE_S without input. Frames or None."""
        now = self.clock() if now is None else now
        out = self._reset_tick(now) if self.reset is not None else b''  # Ctrl+Alt+R easing every desktop flat (its rolls
        # go out with the pointer's `roll` frame just below)
        out += self.overview_pointer.poll(now) if self.overview_pointer is not None else b''  # pill / corner drags, sliders
        if self.reset is None: self._watch_rolls(now)  # a level bar's change is saved like a placement
        if self.save_due is not None and now >= self.save_due: self.flush()
        if self.environment_save_due is not None and now >= self.environment_save_due: self._save_environment()
        if self.desktops is not None:  # a desktop change done; Remove disarms after a while
            from .desktops import REMOVE_ARM_S
            result = self.desktops.take_result()
            if result is not None: out += self._desktops_done(*result)
            if self.state.remove_armed and (self.remove_armed_at is None or now - self.remove_armed_at >= REMOVE_ARM_S):
                self.state.remove_armed = False
                out += self._menu_frame()
        out += self._settle()
        if self.windows is not None and not self.state.desktops_busy: self.windows.poll(now)  # off the loop; never mid-change
        if now - self.last_input >= OVERLAY_IDLE_CLOSE_S:
            if self.state.menu_open: out += self._on_menu('menu_toggle') or b''  # saves at once and releases the panel keys
            if self.help_visible: out += self._help_toggle()
        if self.keys is not None: out += self.keys.poll(now)  # the key bind panel: its own idle close, a change's timeout
        return out or None

    def _typing_toggle(self):
        """Ctrl+Alt+T: typing mode on (an open panel collapses, the help card closes, every run bind but this one goes,
        and the free pointer with its pills is off) or back off."""
        out = b''
        if not self.state.typing:
            if self.state.menu_open: out += self._on_menu('menu_toggle') or b''  # saves; releases the panel keys
            if self.help_visible: out += self._help_toggle()
            if self.keys_open: out += self.keys.close()
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
        out = self.keys.close() if not self.help_visible and self.keys_open else b''  # one centred card at a time
        self.help_visible = not self.help_visible  # the renderer starts a reopened card on page 1
        frame = help_frame(self.run_id, self.session, self.next_id, self.help_visible, **self._help_binds())
        self.next_id += 1
        return out + frame

    def help_turn(self, turn):
        """Ctrl+Alt+PgUp / PgDn, or a free-pointer click on the card's arrows or page indicator: the open help card flips
        to the previous (-1) or next (1) page. Nothing while it is closed. A `help` frame (bytes) or b''."""
        if not self.help_visible or turn not in (-1, 1): return b''
        self.last_input = self.clock()  # turning pages keeps the card up
        frame = help_frame(self.run_id, self.session, self.next_id, True, turn, **self._help_binds())
        self.next_id += 1
        return frame

    def set_help_geometry(self, geometry):
        """Supervisor: where the help card and its page controls lie on the overview's canvas (free_pointer.
        parse_help_geometry; None when the card is not up in the overview), for free-pointer hit testing."""
        if self.overview_pointer is not None: self.overview_pointer.set_help_geometry(geometry)

    def flush(self):
        """Write any pending panel / drag placement save now (panel collapse, drag release, run end), and a pending
        brightness."""
        if self.environment_save_due is not None: self._save_environment()
        if self.save_due is None: return
        self.save_due = None
        self._save_placements()

    def _save_placements(self):
        if self.placements is None or self.reset is not None: return  # a reset saves its own result (_reset_done)
        placements, rolls = [dict(p) for p in self.state.placements], self._rolls()
        save_layout = getattr(self.placements, 'save_layout', None) if rolls is not None else None
        try:
            if save_layout is not None: save_layout(placements, rolls)  # with the rolls (profiles.PlacementStore)
            else: self.placements.save(placements)  # one per desktop
        except (OSError, ValueError): pass

    def on_message(self, message):
        if isinstance(message, dict) and isinstance(message.get('op'), str):
            self.last_input = self.clock()  # any run key, click or panel change keeps an open card or panel up
        if isinstance(message, dict) and message.get('op') == 'placement':
            return self._on_placement(message.get('payload'))
        if isinstance(message, dict) and message.get('op') == 'resize_cancel':  # resize.CANCEL_OP: Super went down
            return self.overview_pointer.super_down() if self.overview_pointer is not None else None
        if isinstance(message, dict) and message.get('op') == TYPING_OP:
            return self._typing_toggle()
        if isinstance(message, dict) and self.state.typing and message.get('op') in IGNORED_WHILE_TYPING: return None
        if isinstance(message, dict) and message.get('op') in MENU_OPS:
            return self._on_menu(message['op'])
        if isinstance(message, dict) and message.get('op') == RESET_OP:  # Ctrl+Alt+R (typing mode: layout_reset ignores it)
            return self.layout_reset() or None
        if isinstance(message, dict) and message.get('op') in ('resize_press', 'resize_release'):  # resize.RESIZE_OPS
            return self.overview_pointer.on_op(message['op']) if self.overview_pointer is not None else None
        if isinstance(message, dict) and message.get('op') in SUPER_OPS:  # unified_cursor.py: Super drags
            # A drag inside a workspace sends Hyprland no event: the memory reads where it ended.
            if message['op'] == SUPER_OPS[2] and self.windows is not None: self.windows.mark()
            on_super = getattr(self.overview_pointer, 'on_super', None)
            return on_super(message['op']) if on_super is not None else None
        if isinstance(message, dict) and message.get('op') in SEND_OPS:  # the move itself runs off the loop
            if self.sender is not None: self.sender.request(message['op'], self.state.names[self.state.selected])
            return None
        if isinstance(message, dict) and message.get('op') == ENVIRONMENT_OP:  # Ctrl+Alt+E: None <-> the last background
            return self.set_environment(_environment.toggled(self.state.environment)) or None
        if isinstance(message, dict) and message.get('op') == ENVIRONMENT_SET_OP:  # the controller: absolute, like `placement`
            try: return self.set_environment(message.get('payload')) or None
            except ValueError: return None
        if isinstance(message, dict) and message.get('op') == PRIVACY_OP:  # the compositor call runs off the loop
            if self.privacy is not None: self.privacy.request()
            return None
        if isinstance(message, dict) and message.get('op') in HELP_PAGE_OPS:
            return self.help_turn(HELP_PAGE_OPS[message['op']]) or None
        if isinstance(message, dict) and message.get('op') in (BINDS_PANEL_OP, 'binds_key'):  # keybinds.OPS
            return self.keys.on_message(message) if self.keys is not None else None
        if not isinstance(message, dict) or message.get('op') not in USER_OPS: return None
        if message['op'] == 'help': return self._help_toggle()
        if message['op'] == 'recenter':
            frame = (json.dumps({'schema': 1, 'request_id': self.next_id, 'run_id': self.run_id, 'session': self.session,
                                 'op': 'recenter', 'payload': {}}, separators=(',', ':')) + '\n').encode()
            self.next_id += 1
            return frame
        # Maximizing collapses an open panel first (saved, keys released): pills and panels live in the overview.
        out = (self._on_menu('menu_toggle') or b'') if message['op'] == 'zoom_in' and self.state.menu_open else b''
        if message['op'] == 'mode_next' and not self.state.zoomed:
            self._tell(MODE_OVERVIEW_TOAST)  # the overview is always anchored: nothing changes, say why
        before = self.state.view()
        if message['op'] == 'zoom_out' and self.state.zoomed and self.overview_pointer is not None:
            self.overview_pointer.summon()  # a cursor off the desktops comes into the selected one
        # Maximized (follow / locked): Ctrl+Alt+Left / Right move straight to the desktop, no overview step (`cut`, as
        # cursor_switch_flags).
        direct = self.state.mode != 'anchor' and self.state.zoomed and message['op'] in ('next', 'prev')
        target = self.state.apply(message['op'])
        if target is not None:
            # Never block the watchdog loop on a compositor reply.
            t = threading.Thread(target=self._focus, args=(target,), daemon=True); t.start(); self._threads.append(t)
        if message['op'] in ('spacing_up', 'spacing_down', 'prediction'):  # (the mode is not saved: maximize-follow)
            try: self.settings.save(self.state.settings())  # a settings store (profiles.LayoutStore/ProfileStore)
            except (OSError, ValueError): pass
        if self.state.view() != before:
            out += self._frame(cut=True) if direct and self.state.selected != before['selected'] else self._frame()
        out += self._release_placements()  # un-maximized by that op
        if self.state.selected != before['selected']: out += self.follow_focus()
        return out or None

    def _on_placement(self, payload):
        """Absolute placements from the panel; applied exactly as given (no bumping, even over another desktop), live, and
        saved to the glasses profile. While maximized they wait for the overview instead (ViewState.hold_placements), and a
        toast says so. Invalid: ignored."""
        s = self.state
        try:
            if not isinstance(payload, dict): return None
            if s.zoomed:
                waiting = bool(s.held)
                s.hold_placements(payload.get('desktops'))
                if s.held and not waiting: self._tell(PLACE_HELD_TOAST)
                return None
            if not s.set_placements(payload.get('desktops')): return None
        except ValueError:
            return None
        self.save_due = None  # this save also covers any pending pill-panel change
        self._save_placements()
        # An open glass panel shows the panel's values too.
        return self._placement_frame() + (self._menu_frame() if self.state.menu_open else b'')

    def on_events(self, data):
        """Hyprland socket2 bytes: a config reload re-applies the run's binds and style, and a window change has the window
        memory read Hyprland again (window_memory.py). Monitor focus never switches the view (cursor follow was removed)."""
        self._event_buffer += data
        if len(self._event_buffer) > 65536: self._event_buffer = self._event_buffer[-4096:]  # bounded; resync on newline
        *lines, self._event_buffer = self._event_buffer.split(b'\n')
        reloaded = moved = False
        for line in lines:
            text = line.decode('utf-8', errors='replace')
            if CONFIGRELOADED.match(text): reloaded = True
            elif WINDOW_EVENT.match(text): moved = True
        if moved and self.windows is not None: self.windows.mark()
        if reloaded:
            self.reloads += 1
            self._menu_binds_installed = False  # a reload drops runtime binds; the reload work re-adds an open panel's
            if self.overview_pointer is not None: self.overview_pointer.binds_dropped = True  # the sampler redoes them if wanted
            if self.on_reload is not None:  # binds and session mode: compositor calls, never on the watchdog loop
                t = threading.Thread(target=self._reload_work, daemon=True); t.start(); self._threads.append(t)
        return (self._style_frame() if reloaded else b'') or None

    def _reload_work(self):
        with self._menu_lock:  # ordered with a typing-mode swap in flight
            typing = self.state.typing  # on_reload (managed.after_reload) reinstalls the set for this mode
            try: self.on_reload()
            except (OSError, ValueError, TypeError, KeyError): pass  # best-effort; the run itself is unaffected
            self._typing_installed = typing
        self._typing_bind_work()  # Ctrl+Alt+T pressed during the reload: put the matching set in place
        self._menu_bind_work(force=True)  # after the view binds: re-add an open panel's modal keys
        if self.keys is not None: self.keys.after_reload()  # an open key bind panel's key subscription

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
