"""Overview free pointer: in the overview the mouse moves freely through the glasses' space, not only over the desktops.

Design (feature/overview-free-pointer, 2026-11-02; glass pills, feature/glass-pills, 2026-11-02):
  * Entering the overview hands the mouse to a pointer that can go anywhere in the XR view: between desktops, below
    them, past their corners. The real cursor is confined to the XR-n monitor layout, so it cannot reach a desktop's
    glass pill (drawn under it) or go round a corner.
  * Two modes, switched by where the pointer is:
      - desktop: the pointer is inside a desktop. The real cursor is visible there (the capture paints it) and every
        click, hover, window drag and window-border resize goes to the window under it as usual (here the run never
        sees those clicks; the run's unified cursor sees left clicks through non-consuming binds, and on one an open
        placement panel comes to that desktop with its keys off the keyboard: unified_cursor.py). The pointer's place in space is
        the cursor's position mapped onto that desktop's surface.
      - free: the cursor reached the edge of its desktop, or crossed onto another monitor. The cursor is hidden and
        parked in the middle of its monitor (put back there whenever it strays RECENTRE_PX), and its motion moves a
        small dim dot drawn by the renderer on the overview's plane (`overview_pointer`). While free, the left button is
        bound to the run (`resize_press` / `resize_release`, the existing modal binds): see Clicks below. Moving
        ENTER_PX into a desktop hands the pointer back to the cursor without a click.
    The switch back to desktop mode needs the pointer ENTER_PX inside; leaving needs the very edge pixel and puts the
    dot EXIT_PX past the edge (_exit), on that edge's resize handle, so the dot is never left free over content and the
    modes never flap at an edge. Moving into a desktop (or clicking it) also selects it, so Ctrl+Alt+Up zooms into the
    desktop the pointer is on and the highlight shows it.
  * One cursor, one zone contract (anchor-no-click-maximize, 2026-11-03: every desktop in the overview is a normal screen,
    and the dot is a full cursor of its own; shared with the sibling branches: the level bar sits above the top edge, resize
    at edges and corners, content goes to apps). What the dot is over, most important first (hit, zone_of):
      - control: an open placement panel's body (drawn on top) and the help card (head-locked, the very top layer).
      - resize: a corner (the part of its corner_zone square in the border), then, after the pill and the bar, an
        edge. The border runs from EDGE_OUT_PX outside a desktop's edge to the ENTER_PX sliver inside it (border_handle).
      - move: a pill zone (on_pill: under the bottom edge), or an open panel's header strip, which is the pill.
      - content: the rest of a desktop's rectangle. There the dot is the real cursor (desktop mode) and the window gets
        every click, drag, right-click, scroll and key. In desktop mode that holds up to the very edge pixel.
      - rotate: a level bar's segment (level_bar_segment), drawn under the desktops, so only where no corner, pill or
        content is; from LEVEL_EDGE_CLEAR out it takes an edge's border, leaving the edge the band next to the desktop.
      - space: nothing.
    The dot shows which (look_for, the `overview_pointer` frame's `look`, marks the renderer draws round it: view.hpp
    PointerLook): a resize direction, move or rotate, and the plain dot otherwise, held through a drag. The mapping both
    ways is surface_to_layout / layout_to_surface (output logical px, so the output scale is in it) on top of unproject /
    project (orbit, distance, curvature, roll).
  * Glass pills (spike/include/xr/glass.hpp, mirrored here: pill_rect, panel_rect and the PILL_* / PANEL_* constants; the
    ctest `glass_contract` compares them): a glass pill (a Material Design 3 tonal capsule) sits PILL_GAP under every desktop, on that desktop's own
    surface. One at a time expands into the desktop's placement panel (PANEL_W x PANEL_H, centred under the desktop, its
    top where the pill's is): a header strip (the pill itself) and one slider row per placement setting. Glass keeps one
    size whatever the desktop's: a resize moves it with the desktop's bottom edge but never scales it, and it hangs from
    the outer edge of the selected desktop's border, which does grow, so it stays one gap under that border at every size
    (glass_to_desktop, view.hpp glass_mesh); pill_rect / panel_rect are glass px. The state is
    view.ViewState's menu_open / menu_desktop; Ctrl+Alt+L and the panel's modal keys drive the same state (view.py).
  * Clicks in free mode, tested front to back (hit): the open panel first (it is drawn on top), then each desktop's
    corners outside it (they win over its pill), its pill zone (the pill padded by PILL_PAD_X / PILL_PAD_Y, plus the gap
    up to the desktop's bottom edge) and the desktop itself.
      - pill, or the open panel's header: press and drag moves the desktop (its x / y placement, the pill staying under
        the pointer). The desktop does not move until the pointer is CLICK_PX away from the press, so a click never
        nudges it; a release before that is a click: it expands that pill (any other open panel collapses) or collapses
        it if it is the open one (view.ViewControl.pill_click, the same path as Ctrl+Alt+L: modal keys, save, idle).
        A key that changed which panel is open while the button was down (Escape, Tab, Ctrl+Alt+L) wins: that release
        is dropped, not a click, so it never undoes the key.
      - a slider of the open panel: that setting is highlighted and its value jumps to the pointer; holding and
        dragging moves it live; the release saves. The value is measured on the panel as it was at the press, so a
        setting that moves its own desktop never chases itself, and until the release the renderer draws the panel on
        that same press-time surface (the `placement_menu` frame's `surface`) while the desktop itself moves live, so
        the knob stays under the pointer. On release the panel joins its desktop again.
      - elsewhere in the open panel: nothing (it stays open).
      - corner or edge: resize (the `distance` placement, so the desktop keeps its shape: a corner follows the pointer
        along its diagonal, an edge along its normal; resize_distance). A release that resized nothing does nothing.
        Until anchor-no-click-maximize a corner's zone reached up to 28 px onto the desktop and took clicks there from
        the window; that part is the desktop's content now.
      - desktop: on content the dot is already the real cursor (desktop mode), so this only happens where it is still free
        there, next to the help card (HELP_CLEAR_PX): a click selects it and stays in the overview (anchor-no-click-maximize,
        2026-11-03; before that it maximized: view.ViewControl.select_desktop). Only Ctrl+Alt+Up (and the panel's Maximize)
        maximizes. The real cursor then comes back there under the dot (_enter: desktop mode, its monitor focused), so the
        next click goes to the window there; this one went to the run, not to a window. It comes on the release.
  * Window drag (overview-window-drag, 2026-11-03), OFF since anchor-no-click-maximize (OverviewPointer.window_drag
    False): a press, hold or drag on a desktop is its window's, so this never takes one from an app. With it on:
    pressing on a desktop and holding the button HOLD_S, or moving the dot more than WINDOW_DRAG_PX first, lifts the
    window under the press instead of selecting; a release before either is the click above. Lifted, the window's ghost (a small outline, its shape scaled down: ghost_size) follows the dot, and the
    desktop under the dot, if it is another one, is framed in the accent (the `overview_pointer` frame's `drag`). A
    release over another desktop moves the window onto the workspace that desktop shows (send.WindowSender.drop: one
    Hyprland move, follow off, so focus, cursor and the selected desktop stay as they were; the dot never selects a
    desktop on its own); a release anywhere else (its own desktop, a pill, empty space) puts nothing anywhere. Which
    window: the one shown at the press (send.window_at, looked up off the loop once it lifts); none there (an empty
    desktop), pinned windows and the help card's own clicks drag nothing. Only a press on a desktop's surface starts it:
    pills, corners (resize), the level bars, an open panel and the help card are hit first and keep their own drags. On
    the selected desktop (unified_cursor.py, desktop mode) the press already went to the window, so only holding still
    there lifts it (moving first is the window's own drag or selection); the lift then turns the dot free, as at an edge.
    A press while a panel is open on anything but that desktop's own pill / header, panel, sliders or corners, or a
    desktop, collapses the panel first (saved, modal keys removed); the press then carries on as listed (another desktop's
    pill still starts its drag or click, empty space does nothing more). A press on a desktop keeps it: selecting that
    desktop takes the panel there (view.ViewControl.select_desktop, follow_focus; Cody, 2026-11-03).
  * Geometry: the renderer reports the overview's canvas size, FOV split and each desktop's overview slot on its
    status socket (`overview_geometry`, only when it changes). A desktop change (a pill's Add / Remove / Ultrawide) keeps
    the last layout: the renderer does not report one again unless it changed, so a dropped one could be gone for the
    rest of the run (a Remove's second notice, the same desktops again). Until a layout with one slot per desktop
    arrives the pointer is off (active); one with the right count (Ultrawide, a Remove's second notice) is used until
    the renderer's next report replaces it. Hit testing maps the pointer onto each desktop's surface with the same
    placement maths as spike/src/view.cpp place_point (orbit, distance, curvature; always facing you), front desktop
    first. The overview's slots never move with head pose, so neither does this. The dot's direction is
    linear in the pointer (orbit.pointer_direction: the same angle per mouse pixel everywhere), and in anchor mode its
    reach goes all the way round you (orbit.reach): the renderer always draws it in that direction (the
    `overview_pointer` frame's az / el), and the hit test and the desktop-mode cursor mapping (project / unproject) read
    the same direction, so what the dot is drawn on is what it hits, anywhere round you.
  * Drags follow the pointer: a pill drag turns the desktop round you (its x / y placement, an orbit: orbit.py) so the
    point grabbed stays exactly under the dot across the dot's whole reach (in anchor mode all the way round you). A
    corner drag changes `distance` so the corner follows the pointer along its diagonal; a slider sets its setting. A
    drag never goes through another desktop: it bumps the desktops it runs into out of its way (bump.py; one Pusher for
    the whole drag), and every desktop goes out in the same `placement` frame. Only drags started on the move and resize
    handles do (RESIZE_DRAGS, _resize_to, orbit.PillDrag): content is the apps', and a Super + drag is Hyprland's window
    move or resize, whose press never reaches the run (resize.py). All are saved like a panel key change (0.5 s
    debounce, and at once on release), pushed desktops with them. Drags also end, and save, after
    OVERLAY_IDLE_CLOSE_S without pointer movement, on zoom-in, when a VR video opens and when Super goes down
    (super_down).
  * Help card (Ctrl+Alt+H, view.py): it is drawn over everything in the middle of the view, head-locked, so while it is
    up the renderer reports where it lies on the canvas under the current head rotation (`help_geometry`, the card and,
    on a paged card, its previous / next arrows and page indicator; parse_help_geometry). It is the top layer for the
    pointer: in desktop mode a cursor that comes onto the card, or that the card opens or moves over, turns into the
    free dot there (the card hides the desktop under it, so a click must not reach the window), and in free mode the dot stays free over the card, entering a desktop only HELP_CLEAR_PX clear of
    it. A click on the previous arrow turns to the previous page, on the next arrow or the indicator to the next
    (view.ViewControl.help_turn, the same frame as Ctrl+Alt+PgUp / PgDn); anywhere else on the card does nothing. A
    click on the card never collapses an open placement panel.
  * Level bar (glass.hpp level_bar_scale / level_bar_rect / level_bar_segment, mirrored here with the LEVEL_* constants; the
    `glass_contract` ctest compares them; arrow brackets round the top corners until 2026-11-03, Cody's pick of the handle
    mockups): in anchor mode the renderer reports how far each desktop is from level as you see it, gravity from the head
    tracker (`roll_geometry`, view.hpp desktop_tilt; the driver publishes no gravity, so it is estimated from the head,
    pose.hpp PoseState::up_; a recentre with your head tilted, or a pitched one seen off to the side, leaves desktops
    rolled). This class owns each desktop's levelling roll (`rolls`, hundredths of a degree, saved with the
    layout) and sends it with the bars (the wire's `icons`) in the `roll` frame. Every desktop shows one in the overview,
    level or not, measured or not: a segmented capsule LEVEL_BAR_GAP above its top edge, the pill's counterpart, all in tile
    px on the desktop's surface, so it moves, scales and rolls with the desktop (a resize keeps it the same share of the
    desktop). Its left and right segments turn the desktop counter-clockwise and clockwise: a click turns it that way
    (roll_click): straight to level when level lies that way (the side roll_side picks), else ROLL_STEP further; pressing
    and dragging either one rolls the desktop with the dot round its centre, either way, up to MAX_ROLL, with a ROLL_LEVEL
    detent at level. Its middle segment levels it (a click: level, the levelling half of R on an open panel; it never moves
    the desktop, and a press dragged off it does nothing). R on a desktop's open panel levels it too (level: as its
    placement goes back flat in its slot). Hit region (level_bar_segment): the drawn bar padded by LEVEL_BAR_PAD, which stays
    LEVEL_EDGE_CLEAR or more outside the desktop (the band along its edges is the edge resize's), and only where nothing
    else is: any desktop's content, a resize corner's zone and the pills all win over it (hit), and the renderer draws the
    bars under them. Only what the dot is on or holds lights up. The bars hide with the pills. Every hit test and cursor
    mapping uses the rolled surface (orbit.place_point's 'roll'); a maximized desktop is drawn level by the renderer alone
    (view.hpp zoomed_placements), so `rolls` stay the overview's and nothing here changes while maximized, a press on a
    bar cut off by a maximize included; the pill drag ignores the roll (a few degrees at most changes nothing there) and
    bumping counts it (bump.py: what collides is the desktop as drawn).
  * Corner caps (glass.hpp corner_cap_rect): a quiet rounded L round each corner of every desktop, drawn with its bar, the
    resize corners made visible. Their press is the corner's resize zone (border_handle); the corner under the dot or held
    is the `resize_handle` frame, and only that cap lights up.
  * Off in typing mode (Ctrl+Alt+T: the pills hide too), while zoomed (pills and panels are overview-only) and while a
    VR video plays (resize ops are in video.IGNORED_WHILE_PLAYING). An open panel no longer turns it off: the pointer
    is how a panel is clicked. Leaving the overview removes the binds and shows the cursor again.
  * OLED care: the dot is grey at 170/255 (184 while held; cards and glass cap at 191), drawn only in DOT_MODES, never
    moves on its own, and hides with the pills, bars and caps after OVERLAY_IDLE_CLOSE_S without pointer movement (an
    open panel stays until the view's own idle close, which pointer motion over it postpones). A click while hidden
    only wakes them.
  * Circle only (overview_cursor.py, display only): the dot is the overview's only pointer. In desktop mode too it is
    drawn, where the cursor maps onto the desktop, and the real cursor is hidden there as in free mode; it still does
    everything desktop mode says above. The sampler hides and shows it; it shows again away, outside the overview and
    at run end.
  * Threads: the sampler reads `cursorpos` every SAMPLE_S (250 Hz) in the overview and does every compositor call
    (binds, hide/show, warps); the supervisor loop owns all state and frames. A run that dies in free mode leaves
    the left-button bind; the next click runs xr_control.py, which removes it and shows the cursor again.
"""
import json
import math
import socket
import threading
import time

from . import bump, geometry, orbit, resize, view
from .geometry import PLACEMENT_BOUNDS
from .overview_cursor import DOT_MODES, OverviewCursor

SAMPLE_S = 1 / 250        # cursor reads per second in the overview (faster than the glasses' 120 Hz)
IDLE_S = 0.1              # not in the overview: just notice when it is again
OUTPUTS_REFRESH = 2.0     # seconds between XR-n monitor geometry reads
EDGE_PX = 0.5             # desktop mode: the cursor this close to its monitor's edge (logical px) has hit it
ENTER_PX = 0.5            # free mode: this far inside a desktop (tile px) is content: the real cursor takes over
EXIT_PX = 2.0             # desktop mode: leaving at an edge puts the dot this far past it (logical px): on the resize edge
HELP_CLEAR_PX = 4.0       # free mode over the help card: the dot enters a desktop only this far clear of the card
INSET_PX = 2              # a cursor handed back lands at least this far inside its monitor (logical px)
RECENTRE_PX = 200         # free mode: the hidden cursor goes back to its parking point once it strays this far
JUMP_PX = 400             # a cursor step this long is a warp or focus change (keyboard), not a hand movement
SAMPLE_JUMP_PX = 1500     # a single sample's motion beyond this is dropped (warps the sampler did not make)
# Grab zones, canvas px: corners are squares centred on the corner (half inside, half outside the desktop).
CORNER_SHARE, CORNER_MIN, CORNER_MAX = 0.06, 10.0, 28.0
# The resize edges: the band EDGE_OUT_PX (tile px) outside each edge, and the ENTER_PX sliver inside it (border_handle).
EDGE_OUT_PX = 10.0
EDGES = ('t', 'b', 'l', 'r')
# The zone contract (shared with the sibling branches: the level bar sits above the top edge, resize at edges and corners,
# content goes to apps). What the dot is over, most important first (hit): the open panel, a corner, a pill, a desktop's
# content, a level bar, an edge; else empty space. zone_of names them.
ZONES = ('control', 'rotate', 'resize', 'move', 'content', 'space')
# The dot's look over each (the `overview_pointer` frame's `look`; spike/include/xr/view.hpp PointerLook): content, empty
# space and controls (an open panel's sliders and buttons, the help card) keep the plain dot, and `text` is the Serif Beam
# over text on a desktop's content (is_text_shape, OverviewPointer._look).
POINTER_LOOKS = ('resize_ew', 'resize_ns', 'resize_nwse', 'resize_nesw', 'move', 'rotate', 'text')
# The Serif Beam: the names of a text cursor, as an app asks by name (wp_cursor_shape: text, vertical-text) or as a cursor
# theme calls it (xterm, ibeam). Asked for by picture (wl_pointer.set_cursor), a cursor has no name: the plain dot.
TEXT_SHAPES = frozenset({'text', 'vertical-text', 'xterm', 'ibeam'})
TEXT_BOUNCE_S = 0.06      # text, something else, text again this fast is a gap between lines, and the beam rides out the next one
TEXT_SWEEP_S = 0.4        # how long after a gap leaving text still waits TEXT_BOUNCE_S; otherwise it is at once (TextHold)
SHAPE_S = 1 / 120         # desktop mode: how often the sampler asks what cursor the app wants, with no push (ownership.cursor_reader)
SHAPE_CHECK_S = 0.25      # the same once `xrcursor` lines arrive (OverviewPointer.push_shape): no plugin sends them since 2026-11-03
SHAPE_SETTLE_S = 1 / 30   # the app's time to answer the pointer's arrival, before a read means anything
SHAPE_RETRY_S = 1.0       # after a failed read
RESIZE_LOOKS = {'l': 'resize_ew', 'r': 'resize_ew', 't': 'resize_ns', 'b': 'resize_ns',
                'tl': 'resize_nwse', 'br': 'resize_nwse', 'tr': 'resize_nesw', 'bl': 'resize_nesw'}
# Glass pill (glass.hpp glass_pill: share of the desktop's width clamped to px, height, gap under the desktop as laid out) and
# the padding around it that still grabs it; the zone also covers the gap and the border up to the desktop's bottom edge.
PILL_SHARE, PILL_MIN, PILL_MAX, PILL_THICKNESS, PILL_GAP = 0.24, 64.0, 160.0, 14.0, 10.0
PILL_PAD_X, PILL_PAD_Y = 10.0, 10.0
BORDER_PX = 6.0              # view.hpp kBorderPx: the selected desktop's frame, tile px
# Expanded pill = placement panel (glass.hpp glass_panel and its layout, panel px from its top-left): the header strip
# (the pill itself: drag to move, click to collapse), then one row per placement setting (geometry.PLACEMENT_KEYS order)
# whose slider knob centre runs PANEL_KNOB_LO (low bound) .. PANEL_KNOB_HI (high bound).
PANEL_W, PANEL_H, PANEL_HEADER_H = 480.0, 394.0, 42.0
PANEL_ROWS_TOP, PANEL_ROW_H, PANEL_KNOB_LO, PANEL_KNOB_HI = 46.0, 34.0, 191.0, 367.0
# Under the sliders, the Resolution row (glass.hpp glass_resolution_box / glass_resolution_item): a dropdown box showing
# the desktop's pixel size; a press on it opens (or closes) its list (resolution_list_rect), drawn above the box over the
# slider rows and as wide as their selected band: one column per geometry.RESOLUTION_GROUPS shape, its name on top
# and its sizes under it. While the list is open it takes the presses on it (('resolution', i, (w, h)) picks a size:
# view.ViewControl.resolution_pick), and a press anywhere else on the panel closes it.
PANEL_RES_TOP = PANEL_ROWS_TOP + len(geometry.PLACEMENT_KEYS) * PANEL_ROW_H  # 182
PANEL_RES_BOX = (184.0, 186.0, 274.0, 26.0)      # x, y, w, h (panel px)
PANEL_RES_ITEM_H, PANEL_RES_LIST_GAP = 23.0, 2.0   # the open list covers the four slider rows exactly
PANEL_RES_LIST_X, PANEL_RES_LIST_W = 10.0, 460.0
# Under that, the Scale row (glass.hpp glass_scale_box / glass_scale_item): a dropdown box showing the desktop's
# output scale; a press on it opens (or closes) its list, one item per view.SCALE_PERCENTS step from the top down, drawn
# above the box over the Resolution row and the slider rows. While the list is open it takes the presses there
# (('scale', i, percent) picks a step: view.ViewControl.scale_pick), and a press anywhere else on the panel closes it.
PANEL_SCALE_TOP = PANEL_RES_TOP + PANEL_ROW_H  # 216
PANEL_SCALE_BOX = (184.0, 220.0, 274.0, 26.0)    # x, y, w, h (panel px)
PANEL_SCALE_ITEM_H, PANEL_SCALE_LIST_GAP = 20.0, 2.0
# Under that, the background's rows (glass.hpp glass_background_box / _switch / _item, environment.py): its dropdown, as the
# Scale one, with the switch beside it, and the Brightness slider on the placement sliders' knob range.
PANEL_BACKGROUND_TOP = PANEL_SCALE_TOP + PANEL_ROW_H  # 250
PANEL_BACKGROUND_BOX = (184.0, 254.0, 216.0, 26.0)     # x, y, w, h (panel px)
PANEL_BACKGROUND_SWITCH = (412.0, 254.0, 46.0, 26.0)
PANEL_BACKGROUND_ITEM_H, PANEL_BACKGROUND_LIST_GAP = 20.0, 2.0
PANEL_BRIGHTNESS_TOP = PANEL_BACKGROUND_TOP + PANEL_ROW_H  # 284
# Under that, the desktop row (glass.hpp glass_panel_action): one button per PANEL_ACTIONS entry, side by side from
# the panel's left padding (PANEL_PAD), each PANEL_ACTION_W wide with PANEL_ACTION_GAP between them. A press on one is
# that action for the panel's desktop (view.ViewControl.desktop_action; desktops.py does the work).
PANEL_PAD = 22.0
PANEL_ACTIONS_TOP, PANEL_ACTION_H, PANEL_ACTION_W, PANEL_ACTION_GAP = 324.0, 32.0, 138.0, 11.0
# What a press finds on the open panel's own desktop that belongs to the panel: not a click outside it (OverviewPointer.on_op).
PANEL_KINDS = ('pill', 'panel', 'slider', 'corner', 'edge', 'action', 'scale', 'scale_box', 'resolution', 'resolution_box',
               'background', 'background_box', 'background_switch')
PANEL_ACTIONS = ('ultrawide', 'add', 'remove')
MAX_DESKTOPS = geometry.MAX_DESKTOPS
PANEL_SLOP_PX = 12.0      # a press this far past either end of a slider's knob range still grabs it (the knob and the dot)
CLICK_PX = 4.0            # a pill press moving less than this (canvas px) before release is a click: the desktop stays put
HOLD_S = 0.35             # a press on a desktop held this long (without WINDOW_DRAG_PX of motion) lifts its window
WINDOW_DRAG_PX = 12.0     # ... or moved this far (canvas px) from the press first; a release before either is a click
# The dragged window's ghost: its apparent size times GHOST_SCALE, the longer side kept within GHOST_MIN_PX..GHOST_MAX_PX
# (canvas px; ipc.hpp kMaxGhostPx bounds the wire); GHOST_DEFAULT until the window's size is known.
GHOST_SCALE, GHOST_MIN_PX, GHOST_MAX_PX, GHOST_DEFAULT = 0.3, 24, 160, (72, 45)
MAX_GHOST_PX = 512
MIN_SCALE = 0.05          # a corner drag cannot shrink the apparent size past this (the distance bound clamps first)
# Past the edge of the view a corner drag speeds up by this much (resize_reach): the pointer stops half a view past each
# edge (OverviewPointer._clamp), so a corner following it 1:1 could not take a desktop to the distance bound's ten times.
RESIZE_OVERSHOOT_GAIN = 8.0
POINTER_HIDDEN = {'visible': False, 'x': 0, 'y': 0, 'held': False}
# Bumping hooks into the hit-zone contract's handles, never the content (bump.py): a drag started on a resize handle
# (RESIZE_DRAGS: the contract's corner and edge handles, `resize` with its `handle`) resizes through
# _resize_to, one on a move handle (the pill or the open panel's header) moves through orbit.PillDrag, and both bump. A
# press on content (hit: 'desktop') is the apps' and never starts either; nor do the level bars, sliders (ViewControl
# bumps those) or a window drag.
RESIZE_DRAGS = ('resize',)
# What Super going down stops (OverviewPointer.super_down): every drag of a monitor. A window drag is the window's.
MONITOR_DRAGS = RESIZE_DRAGS + ('pill', 'slider', 'roll')
MAX_POINTER_PX = view.MAX_POINTER_PX  # spike/include/xr/ipc.hpp kMaxPointerPx; the overview pointer may be negative
EDGE_SNAP_PX = 1e-6  # tile px: unproject's answer this close to a desktop's edge is on it (_onto_edges)
MAX_POINTER_AZ, MAX_POINTER_EL = 18000, 9000  # ipc.hpp kMaxPointerAz / kMaxPointerEl: the dot's direction, 1/100 degree
# Level bar (glass.hpp kLevelBar*: tile px on the desktop's surface, so it moves and scales with it): its size and gap above
# the top edge on a desktop wide enough, the padding past what is drawn that still grabs it, and the share of the desktop's
# width it takes on a narrower one, not under LEVEL_BAR_MIN_W (the whole design scales: level_bar_scale).
LEVEL_BAR_W, LEVEL_BAR_H, LEVEL_BAR_GAP, LEVEL_BAR_PAD = 148.0, 26.0, 12.0, 4.0
LEVEL_BAR_SHARE, LEVEL_BAR_MIN_W = 0.25, 120.0
# The hit-zone contract round a desktop, from the inside out: its content (its apps' own clicks, drags and scrolling);
# the band LEVEL_EDGE_CLEAR wide just outside every edge (the selected desktop's border), left to the edge's resize zone,
# and the corners' zones (corner_zone), which reach round each corner inside and out; then the bar, from LEVEL_EDGE_CLEAR
# out; then empty space. The bar never takes a press from any of the others.
LEVEL_EDGE_CLEAR = 6.0
# How far from level (1/100 degree) counts as level, and the wire's bound on a levelling roll (ipc.hpp kMaxRoll).
ROLL_LEVEL = 50
ROLL_STEP = 200  # a click on a turn segment that does not point toward level turns the desktop 2 degrees that way
MAX_ROLL = 9000
LEVEL_SETTLE_S = 0.5  # after R, a desktop's measured tilts this long level it again (OverviewPointer.level)
ROLL_SIDES = ('ccw', 'cw')  # the level bar's turn segments, left and right
# The 3D button, Connected Tail (glass.hpp kImmersive*: tile px, scaled with the level bar): IMMERSIVE_GAP right of the bar on
# its line and as tall, the 3D segment for this desktop, and IMMERSIVE_GAP right of that the tail for every desktop. On a
# desktop too narrow to hold both short of the top-right corner's cap (IMMERSIVE_CLEAR) there is neither: they never sit on a
# resize corner.
IMMERSIVE_GAP, IMMERSIVE_MAIN_W, IMMERSIVE_TAIL_W, IMMERSIVE_CLEAR = 2.0, 40.0, 28.0, 16.0
LEVEL_SEGMENTS = ('ccw', 'level', 'cw')  # its segments left to right (glass.hpp LevelSegment; ipc.hpp RollPayload side)
ROLL_ICONS = ('none', 'bar')  # what a desktop shows (ipc.hpp RollPayload icons)
ROLL_HIDDEN = {'roll': [0] * geometry.MAX_DESKTOPS, 'icons': ['none'] * geometry.MAX_DESKTOPS, 'hover': -1, 'side': 'none',
               'active': False}  # the renderer's start


def parse_overview_geometry(value):
    """The renderer's `overview_geometry` status line (spike/src/view.cpp overview_geometry_json) -> validated dict
    {'w', 'h', 'half': (tan half-FOV x, y), 'tiles': [(x, y, w, h) canvas px] in managed-output order}. ValueError if bad."""
    def number(v, lo, hi):
        if type(v) not in (int, float) or not math.isfinite(v) or not lo <= v <= hi: raise ValueError('invalid overview geometry')
        return float(v)
    if not isinstance(value, dict) or set(value) != {'w', 'h', 'half', 'tiles'}: raise ValueError('invalid overview geometry')
    w, h = value['w'], value['h']
    if type(w) is not int or type(h) is not int or not 0 < w <= MAX_POINTER_PX or not 0 < h <= MAX_POINTER_PX:
        raise ValueError('invalid overview geometry')
    half, tiles = value['half'], value['tiles']
    if not isinstance(half, list) or len(half) != 2 or not isinstance(tiles, list) or not 1 <= len(tiles) <= MAX_DESKTOPS:
        raise ValueError('invalid overview geometry')
    half = tuple(number(v, 1e-3, 100.0) for v in half)
    out = []
    for t in tiles:
        if not isinstance(t, list) or len(t) != 4: raise ValueError('invalid overview geometry')
        x, y, tw, th = (number(v, -MAX_POINTER_PX, MAX_POINTER_PX) for v in t)
        if tw <= 0 or th <= 0: raise ValueError('invalid overview geometry')
        out.append((x, y, tw, th))
    return {'w': w, 'h': h, 'half': half, 'tiles': out}


def parse_roll_geometry(value):
    """The renderer's `roll_geometry` status line (wayland.cpp take_roll_geometry) -> list of each desktop's tilt from level
    (int, 1/100 degree, counter-clockwise positive) or None where it is not measured. ValueError if bad."""
    if not isinstance(value, dict) or set(value) != {'tilt'} or not isinstance(value['tilt'], list) or not 1 <= len(value['tilt']) <= MAX_DESKTOPS:
        raise ValueError('invalid roll geometry')
    out = []
    for t in value['tilt']:
        if t is not None and (type(t) is not int or not -18000 <= t <= 18000): raise ValueError('invalid roll geometry')
        out.append(t)
    return out


def level_bar_scale(tile):
    """What the level bar and corner caps are scaled by (glass.hpp level_bar_scale): 1, less on a narrow desktop; 0 if empty."""
    tw, th = tile[2], tile[3]
    if not (tw > 0 and th > 0): return 0.0
    return min(max(LEVEL_BAR_SHARE * tw, LEVEL_BAR_MIN_W), LEVEL_BAR_W, tw) / LEVEL_BAR_W


def level_bar_rect(tile):
    """The level bar as drawn (glass.hpp level_bar_rect): (x, y, w, h) tile px on the desktop's surface, centred above its
    top edge. Empty for an empty tile."""
    s = level_bar_scale(tile)
    if not s > 0: return (0.0, 0.0, 0.0, 0.0)
    w, h = LEVEL_BAR_W * s, LEVEL_BAR_H * s
    return (tile[0] + (tile[2] - w) / 2, tile[1] - LEVEL_BAR_GAP * s - h, w, h)


def maximize_rect(tile):
    s = level_bar_scale(tile)
    x, y, _, _ = level_bar_rect(tile)
    return x - 38*s, y, 26*s, 26*s

def maximize_hit(tile, q):
    from .options_geometry import inside
    x,y,w,h = maximize_rect(tile)
    pad = 4*level_bar_scale(tile)
    return w > 0 and inside((x-pad,y-pad,w+2*pad,h+2*pad), q)


def level_bar_segment(tile, q):
    """The segment (LEVEL_SEGMENTS) at tile point `q` (None: missed), or None (glass.hpp level_bar_segment): the drawn bar
    padded by LEVEL_BAR_PAD, split at its thirds, the padding belonging to the segment it borders. Where it overlaps a
    corner's zone, a pill or content, they win (hit)."""
    x, y, w, h = level_bar_rect(tile)
    if q is None or not w > 0: return None
    pad = LEVEL_BAR_PAD * level_bar_scale(tile)
    if q[0] < x - pad or q[0] > x + w + pad or q[1] < y - pad or q[1] > y + h + pad: return None
    third = w / len(LEVEL_SEGMENTS)
    if q[0] < x + third: return LEVEL_SEGMENTS[0]
    return LEVEL_SEGMENTS[1] if q[0] < x + 2 * third else LEVEL_SEGMENTS[2]


def _immersive_rects(tile):
    """The 3D button's (segment, tail) rects, or None if the desktop is empty or too narrow for them."""
    s = level_bar_scale(tile)
    if not s > 0: return None
    bx, by, bw, bh = level_bar_rect(tile)
    main = (bx + bw + IMMERSIVE_GAP * s, by, IMMERSIVE_MAIN_W * s, bh)
    tail = (main[0] + main[2] + IMMERSIVE_GAP * s, by, IMMERSIVE_TAIL_W * s, bh)
    return (main, tail) if tail[0] + tail[2] <= tile[0] + tile[2] - IMMERSIVE_CLEAR * s else None


def immersive_main_rect(tile):
    """The 3D button's 3D segment as drawn (glass.hpp immersive_main_rect): (x, y, w, h) tile px. Empty if there is no button."""
    rects = _immersive_rects(tile)
    return rects[0] if rects is not None else (0.0, 0.0, 0.0, 0.0)


def immersive_tail_rect(tile):
    """The 3D button's tail as drawn (glass.hpp immersive_tail_rect). Empty if there is no button."""
    rects = _immersive_rects(tile)
    return rects[1] if rects is not None else (0.0, 0.0, 0.0, 0.0)


def immersive_part(tile, q):
    """'main' or 'tail' at tile point `q` (None: missed), or None (glass.hpp immersive_part): padded by LEVEL_BAR_PAD like the
    bar above, below and past the tail's end. The gaps are too narrow for padding, so each is split down its middle."""
    rects = _immersive_rects(tile)
    if q is None or rects is None: return None
    main, tail = rects
    s = level_bar_scale(tile)
    pad, half = LEVEL_BAR_PAD * s, IMMERSIVE_GAP * s / 2
    if q[1] < main[1] - pad or q[1] > main[1] + main[3] + pad or q[0] < main[0] - half or q[0] > tail[0] + tail[2] + pad: return None
    return 'main' if q[0] < tail[0] - half else 'tail'


def roll_side(tilt, roll):
    """The turn segment that levels a desktop: 'cw' (the bar's right one) if it is turned counter-clockwise from level by
    more than ROLL_LEVEL (its top-right corner is the higher one), 'ccw' if clockwise, None if level or not measured
    (`tilt` None). `tilt` from the renderer (without the roll), `roll` the levelling roll applied, both 1/100 degree."""
    if tilt is None: return None
    t = tilt + roll
    if abs(t) <= ROLL_LEVEL: return None
    return 'cw' if t > 0 else 'ccw'


def roll_click(tilt, roll, side):
    """The levelling roll after a click on desktop's `side` turn segment: straight to level when it points toward level
    (roll_side), else ROLL_STEP further that way (counter-clockwise positive), within +-MAX_ROLL. Both ways always work,
    on a level desktop and on one whose tilt is not measured (follow mode, no tracking) too."""
    if side not in ROLL_SIDES: raise ValueError('invalid roll side')
    if tilt is not None and roll_side(tilt, roll) == side: target = -tilt
    else: target = roll + (ROLL_STEP if side == 'ccw' else -ROLL_STEP)
    return min(max(int(target), -MAX_ROLL), MAX_ROLL)


def roll_drag(tilt, start, turn):
    """The levelling roll while a turn segment is dragged: the roll at the press plus the dot's `turn` round the desktop's
    centre (1/100 degree, counter-clockwise positive), either way whichever segment was pressed, within +-MAX_ROLL; within
    ROLL_LEVEL of level (when measured) it rests at level."""
    r = min(max(round(start + turn), -MAX_ROLL), MAX_ROLL)
    if tilt is not None and abs(tilt + r) <= ROLL_LEVEL and -MAX_ROLL <= -tilt <= MAX_ROLL: r = -tilt
    return r


def level_roll(tilt):
    """The levelling roll that levels a desktop (OverviewPointer.level: R on its open panel, the bar's middle segment): -tilt
    where its tilt from level is measured (anchor mode with head tracking), within +-MAX_ROLL; else 0, no roll (upright as
    placed)."""
    return 0 if tilt is None else min(max(-int(tilt), -MAX_ROLL), MAX_ROLL)


def roll_frame(run_id, session, request_id, state):
    """`roll` frame (ipc.hpp RollPayload): {'roll': MAX_DESKTOPS ints, 'icons': MAX_DESKTOPS of 'none' | 'bar', 'hover':
    -1..MAX_DESKTOPS-1, 'side': 'none' | 'ccw' | 'level' | 'cw' (which segment of the hovered desktop's bar), 'active'}."""
    if not isinstance(state, dict) or set(state) != set(ROLL_HIDDEN): raise ValueError('invalid roll state')
    roll, icons, hover, side, active = state['roll'], state['icons'], state['hover'], state['side'], state['active']
    if not isinstance(roll, list) or len(roll) != MAX_DESKTOPS or any(type(r) is not int or not -MAX_ROLL <= r <= MAX_ROLL for r in roll) \
            or not isinstance(icons, list) or len(icons) != MAX_DESKTOPS or any(i not in ROLL_ICONS for i in icons) \
            or type(hover) is not int or not -1 <= hover < MAX_DESKTOPS or side not in ('none',) + LEVEL_SEGMENTS \
            or (hover < 0) != (side == 'none') or type(active) is not bool:
        raise ValueError('invalid roll state')
    return (json.dumps({'schema': 1, 'request_id': request_id, 'run_id': run_id, 'session': session, 'op': 'roll',
                        'payload': {'roll': roll, 'icons': icons, 'hover': hover, 'side': side, 'active': active}},
                       separators=(',', ':')) + '\n').encode()


HELP_RECTS = ('card', 'prev', 'indicator', 'next')


def parse_help_geometry(value):
    """The renderer's `help_geometry` status line (spike/include/xr/hud.hpp help_geometry_json) -> {'card': (x, y, w, h),
    and 'prev' / 'indicator' / 'next' on a paged card} in canvas px, or None (no card up in the overview). ValueError if
    bad."""
    if value is None: return None
    if not isinstance(value, dict) or 'card' not in value or not set(value) <= set(HELP_RECTS):
        raise ValueError('invalid help geometry')
    controls = set(value) - {'card'}
    if controls and controls != {'prev', 'indicator', 'next'}: raise ValueError('invalid help geometry')
    out = {}
    for name, r in value.items():
        if not isinstance(r, list) or len(r) != 4 or any(type(v) is not int or not -MAX_POINTER_PX <= v <= MAX_POINTER_PX for v in r) \
                or r[2] <= 0 or r[3] <= 0:
            raise ValueError('invalid help geometry')
        out[name] = tuple(r)
    return out


def help_hit(help_, pointer, margin=0.0):
    """('help', 'prev' | 'indicator' | 'next' | 'card') if canvas point `pointer` is on the help card (`help_`, from
    parse_help_geometry; `margin` canvas px around the card counts too), else None."""
    if not help_ or pointer is None: return None
    def inside(r, m=0.0): return r[0] - m <= pointer[0] < r[0] + r[2] + m and r[1] - m <= pointer[1] < r[1] + r[3] + m
    for name in ('prev', 'next', 'indicator'):
        if name in help_ and inside(help_[name]): return ('help', name)
    return ('help', 'card') if inside(help_['card'], margin) else None


def place_point(g, tile, p, px, py):
    """World position of canvas pixel (px, py) on a desktop laid out at `tile` with wire placement `p`
    (geometry.PLACEMENT_BOUNDS units). Mirrors spike/src/view.cpp place_point exactly (orbit.place_point)."""
    return orbit.place_point(g, tile, p, px, py)


def to_canvas(g, point):
    """A world point seen from the eye, on the canvas plane (z = -1) in canvas px; None if it is not in front or lands
    past MAX_POINTER_PX (a desktop turned far round you: nowhere the pointer, or a frame, can go)."""
    x, y, z = point
    if z > -1e-6: return None
    hw, hh = g['half']
    out = ((x / -z / hw + 1) * g['w'] / 2, (1 - y / -z / hh) * g['h'] / 2)
    return out if abs(out[0]) <= MAX_POINTER_PX and abs(out[1]) <= MAX_POINTER_PX else None


def project(g, tile, p, px, py):
    """The pointer position (canvas px) whose dot (orbit.pointer_direction) lies on tile pixel (px, py) of a placed
    desktop: anywhere round you, never None."""
    return orbit.pointer_at(g, place_point(g, tile, p, px, py))


def _flat(g, tile, p, px, py):
    """Where tile pixel (px, py) of a placed desktop appears on the overview plane (canvas px), or None."""
    return to_canvas(g, place_point(g, tile, p, px, py))


def _onto_edges(tile, q):
    """Tile pixel q, put exactly on the desktop's edge when within EDGE_SNAP_PX of it: the dot leaves a desktop on its
    edge (OverviewPointer._absolute), and the ray's round trip back to it is exact only to ~1e-13 px."""
    tx, ty, tw, th = tile
    def snap(v, lo, hi): return lo if abs(v - lo) < EDGE_SNAP_PX else hi if abs(v - hi) < EDGE_SNAP_PX else v
    return (snap(q[0], tx, tx + tw), snap(q[1], ty, ty + th))


def unproject(g, tile, p, pointer):
    """The tile pixel (px, py) that appears under the dot at canvas point `pointer` on a placed desktop's surface, or None
    when the dot's ray (orbit.pointer_ray) misses that surface. The ray is met on the overview's plane, turned back first
    by the desktop's orbit (x / y, a rotation about the eye: orbit.unorbit_ray). Newton's method on that flat
    projection, started from the exact flat answer."""
    pointer = orbit.unorbit_ray(g, tile, p, pointer)
    if pointer is None: return None
    p = {**p, 'x': 0, 'y': 0}
    tx, ty, tw, th = tile
    cx, cy = tx + tw / 2, ty + th / 2
    d = p['distance'] / 100
    ax, av = (pointer[0] - cx) * d, (cy - pointer[1]) * d  # as you see it (y up); a rolled desktop is turned back first
    roll = math.radians(p.get('roll', 0) / 100)
    if roll: ax, av = math.cos(roll) * ax + math.sin(roll) * av, -math.sin(roll) * ax + math.cos(roll) * av
    q = [cx + ax, cy - av]
    limit = 4 * max(tw, th)
    for _ in range(16):
        f = _flat(g, tile, p, q[0], q[1])
        if f is None: return None
        ex, ey = f[0] - pointer[0], f[1] - pointer[1]
        if abs(ex) < 0.01 and abs(ey) < 0.01: return _onto_edges(tile, q)
        step = 0.5
        fx, fy = _flat(g, tile, p, q[0] + step, q[1]), _flat(g, tile, p, q[0], q[1] + step)
        if fx is None or fy is None: return None
        a, b = (fx[0] - f[0]) / step, (fy[0] - f[0]) / step
        c, e = (fx[1] - f[1]) / step, (fy[1] - f[1]) / step
        det = a * e - b * c
        if abs(det) < 1e-9: return None
        dx, dy = (e * ex - b * ey) / det, (a * ey - c * ex) / det
        q = [q[0] - dx, q[1] - dy]
        if abs(q[0] - cx) > limit or abs(q[1] - cy) > limit: return None
    f = _flat(g, tile, p, q[0], q[1])
    return _onto_edges(tile, q) if f is not None and math.hypot(f[0] - pointer[0], f[1] - pointer[1]) < 0.5 else None


def surface_to_layout(tile, output, q, inset=INSET_PX):
    """Desktop surface pixel q (tile px: where the dot's ray meets the placed desktop, unproject, which undoes its orbit
    round you, distance, curvature and roll) -> the integer Hyprland layout pixel of that spot on the desktop's monitor
    `output` (resize.outputs_from_monitors: logical px, so the output scale is in it), at least `inset` px inside every
    edge. The real cursor goes there so the window under the dot gets the input."""
    tx, ty, tw, th = tile
    lx = min(max((q[0] - tx) / tw * output['width'], inset), output['width'] - 1 - inset)
    ly = min(max((q[1] - ty) / th * output['height'], inset), output['height'] - 1 - inset)
    return (int(output['x'] + lx + 0.5), int(output['y'] + ly + 0.5))  # the nearest pixel (lx, ly >= 0)


def layout_to_surface(tile, output, lx, ly):
    """The inverse of surface_to_layout: monitor-local logical px (lx, ly) on `output` -> desktop surface pixel (tile
    px). project() of that is where the dot is drawn for the cursor there."""
    tx, ty, tw, th = tile
    return (tx + lx / output['width'] * tw, ty + ly / output['height'] * th)


def corner_zone(tile):
    """Half-size of a corner's grab square, tile px. Only the part of the square in the resize border (border_handle:
    outside the desktop, or within ENTER_PX of its edge) is its resize handle: the rest of the desktop is content."""
    return min(max(CORNER_SHARE * min(tile[2], tile[3]), CORNER_MIN), CORNER_MAX)


def border_handle(tile, q):
    """The resize handle at tile pixel q of a desktop's surface, or None: ('corner', 'tl'|'tr'|'bl'|'br') in a corner's
    square (corner_zone), else ('edge', 't'|'b'|'l'|'r') for the nearest edge, within the border: EDGE_OUT_PX outside the
    desktop to ENTER_PX inside it. Deeper inside is content, further out is nothing of this desktop's."""
    depth = inside_by(tile, q)
    if depth >= ENTER_PX: return None
    tx, ty, tw, th = tile
    z = corner_zone(tile)
    for corner, (cx, cy) in (('tl', (tx, ty)), ('tr', (tx + tw, ty)), ('bl', (tx, ty + th)), ('br', (tx + tw, ty + th))):
        if abs(q[0] - cx) <= z and abs(q[1] - cy) <= z: return ('corner', corner)
    if depth < -EDGE_OUT_PX: return None
    gaps = {'t': q[1] - ty, 'b': ty + th - q[1], 'l': q[0] - tx, 'r': tx + tw - q[0]}
    return ('edge', min(EDGES, key=lambda e: gaps[e]))


def zone_of(h):
    """The zone contract's name (ZONES) for a hit(): 'rotate' (a level bar segment), 'resize' (a corner or an edge), 'move' (a
    pill, or the open panel's header strip, which is the pill), 'content' (a desktop's own windows), 'control' (the rest
    of an open panel, the help card), 'space' (nothing)."""
    if h is None: return 'space'
    return {'roll': 'rotate', 'corner': 'resize', 'edge': 'resize', 'pill': 'move', 'desktop': 'content'}.get(h[0], 'control')


def is_text_shape(name):
    """A requested cursor's name (a plain word, or None for none) is a text cursor: TEXT_SHAPES, in any case and with `_`
    for `-`, as cursor themes spell them both ways."""
    return isinstance(name, str) and name.lower().replace('_', '-') in TEXT_SHAPES


class TextHold:
    """Whether the beam is on: at once when the app asks for it, and at once when it stops, with one exception. Text that comes
    back within TEXT_BOUNCE_S of going is the gap between two lines, so for TEXT_SWEEP_S after that every leave waits
    TEXT_BOUNCE_S first: the sweep down a page holds the beam, and only the first gap of a sweep can show the dot."""
    def __init__(self):
        self.on, self.until = False, None  # until: the end of a leave's wait
        self.left, self.sweep = None, 0.0  # left: when the beam last went; sweep: leaves wait until then

    def step(self, text, now):
        if text:
            if self.until is not None or (self.left is not None and now - self.left <= TEXT_BOUNCE_S):
                self.sweep = now + TEXT_SWEEP_S
            self.on, self.until, self.left = True, None, None
        elif self.on:
            if self.until is None and now < self.sweep: self.until = now + TEXT_BOUNCE_S
            elif self.until is None or now >= self.until: self.on, self.until, self.left = False, None, now
        return self.on

    def clear(self):
        self.on, self.until, self.left, self.sweep = False, None, None, 0.0


def look_for(h):
    """The dot's look over hit `h` (POINTER_LOOKS, handles only), None for the plain dot (content, controls, empty space)."""
    zone = zone_of(h)
    if zone == 'resize': return RESIZE_LOOKS[h[2]]
    return {'move': 'move', 'rotate': 'rotate'}.get(zone)


def glass_anchor(tile):
    """What glass hangs from (spike/src/view.cpp glass_anchor): the middle of the border's outer edge under the desktop."""
    return (tile[0] + tile[2] / 2, tile[1] + tile[3] + BORDER_PX)


def glass_to_desktop(tile, p, gx, gy):
    """Glass px (pill_rect / panel_rect) -> the desktop surface px it is drawn at (spike/src/view.cpp glass_to_desktop):
    scaled by the desktop's distance about glass_anchor, so the glass keeps its size however big the desktop is, and one
    gap to the border, which grows with the desktop."""
    k = p['distance'] / 100 if p['distance'] > 0 else 1.0
    ax, ay = glass_anchor(tile)
    return (ax + (gx - ax) * k, ay + (gy - ay) * k)


def desktop_to_glass(tile, p, q):
    """The inverse of glass_to_desktop for desktop surface px `q` (None stays None)."""
    if q is None: return None
    k = p['distance'] / 100 if p['distance'] > 0 else 1.0
    ax, ay = glass_anchor(tile)
    return (ax + (q[0] - ax) / k, ay + (q[1] - ay) / k)


def project_glass(g, tile, p, gx, gy):
    """The pointer position (canvas px) whose dot lies on glass point (gx, gy) under a placed desktop (project)."""
    return project(g, tile, p, *glass_to_desktop(tile, p, gx, gy))


def pill_rect(tile):
    """The drawn glass pill (glass.hpp glass_pill): (x, y, w, h) canvas px on the desktop's surface, centred PILL_GAP
    under the desktop."""
    tx, ty, tw, th = tile
    w = min(min(max(PILL_SHARE * tw, PILL_MIN), PILL_MAX), tw)
    return (tx + (tw - w) / 2, ty + th + PILL_GAP, w, PILL_THICKNESS)


def panel_rect(tile):
    """The expanded pill, the placement panel (glass.hpp glass_panel): (x, y, w, h) canvas px on the desktop's surface,
    centred under the desktop with its top where the pill's top is."""
    tx, ty, tw, th = tile
    return (tx + (tw - PANEL_W) / 2, ty + th + PILL_GAP, PANEL_W, PANEL_H)


def action_rect(i):
    """The desktop row's button i (glass.hpp glass_panel_action): (x, y, w, h) panel px from the panel's top-left."""
    return (PANEL_PAD + i * (PANEL_ACTION_W + PANEL_ACTION_GAP), PANEL_ACTIONS_TOP, PANEL_ACTION_W, PANEL_ACTION_H)


def scale_item_rect(i):
    """The Scale list's item i (glass.hpp glass_scale_item; view.SCALE_PERCENTS order, top down): (x, y, w, h) panel px.
    The list ends PANEL_SCALE_LIST_GAP above the box."""
    from .view import SCALE_PERCENTS
    bx, by, bw, _ = PANEL_SCALE_BOX
    top = by - PANEL_SCALE_LIST_GAP - len(SCALE_PERCENTS) * PANEL_SCALE_ITEM_H
    return (bx, top + i * PANEL_SCALE_ITEM_H, bw, PANEL_SCALE_ITEM_H)


def background_item_rect(i):
    """The Background list's item i (glass.hpp glass_background_item; environment.BACKGROUNDS order, top down): (x, y, w, h)
    panel px. The list ends PANEL_BACKGROUND_LIST_GAP above the box."""
    from .environment import BACKGROUNDS
    bx, by, bw, _ = PANEL_BACKGROUND_BOX
    top = by - PANEL_BACKGROUND_LIST_GAP - len(BACKGROUNDS) * PANEL_BACKGROUND_ITEM_H
    return (bx, top + i * PANEL_BACKGROUND_ITEM_H, bw, PANEL_BACKGROUND_ITEM_H)


def resolution_list_rect():
    """The open Resolution list (glass.hpp glass_resolution_list): (x, y, w, h) panel px, PANEL_RES_LIST_W wide, a heading
    row and as many item rows as the largest geometry.RESOLUTION_GROUPS group, ending PANEL_RES_LIST_GAP above the box."""
    rows = 1 + max(len(sizes) for _, sizes in geometry.RESOLUTION_GROUPS)
    h = rows * PANEL_RES_ITEM_H
    return (PANEL_RES_LIST_X, PANEL_RES_BOX[1] - PANEL_RES_LIST_GAP - h, PANEL_RES_LIST_W, h)


def resolution_item_rect(i):
    """The Resolution list's item i (glass.hpp glass_resolution_item; geometry.RESOLUTIONS order): (x, y, w, h) panel px,
    in its group's column (one per geometry.RESOLUTION_GROUPS entry, left to right), its row in the group under the
    column's heading. Empty (zero) for i past the list."""
    if not 0 <= i < len(geometry.RESOLUTIONS): return (0.0, 0.0, 0.0, 0.0)
    column, row = 0, i
    while row >= len(geometry.RESOLUTION_GROUPS[column][1]): row -= len(geometry.RESOLUTION_GROUPS[column][1]); column += 1
    x, y, w, _ = resolution_list_rect()
    cw = w / len(geometry.RESOLUTION_GROUPS)
    return (x + column * cw, y + (1 + row) * PANEL_RES_ITEM_H, cw, PANEL_RES_ITEM_H)


def slider_fraction(lx):
    """Panel x (px from the panel's left) -> fraction 0..1 along a slider's knob range (glass.cpp draws the knob there)."""
    return min(max((lx - PANEL_KNOB_LO) / (PANEL_KNOB_HI - PANEL_KNOB_LO), 0.0), 1.0)


def panel_hit(lx, ly, index, picker=False, resolution_picker=False, background_picker=False):
    """What panel point (lx, ly) (panel px from its top-left, inside the panel) is on: ('pill', index) for the header
    strip, ('scale', index, percent) on an item of the open Scale list (`picker`: it is open; it covers the sliders
    under it), ('background', index, name) on an item of the open Background list (`background_picker`; it covers what is
    under it), ('resolution', index, (width, height)) on an item of the open Resolution list (`resolution_picker`; the
    rest of that list is ('panel', index)), ('resolution_box', index), ('scale_box', index) and ('background_box', index)
    on the boxes, ('background_switch', index) on the Background switch, ('slider', index, field, lx) on a slider row's knob
    range (PANEL_SLOP_PX either side; the Brightness row is one, field environment.BRIGHTNESS_FIELD), ('action', index, name)
    on a desktop-row button (PANEL_ACTIONS), else ('panel', index)."""
    from .environment import BACKGROUNDS, BRIGHTNESS_FIELD
    from .geometry import PLACEMENT_KEYS, RESOLUTIONS
    from .view import SCALE_PERCENTS
    if ly < PANEL_HEADER_H: return ('pill', index)
    if picker:
        for i, percent in enumerate(SCALE_PERCENTS):
            x, y, w, h = scale_item_rect(i)
            if x <= lx < x + w and y <= ly < y + h: return ('scale', index, percent)
    if background_picker:
        for i, (name, _) in enumerate(BACKGROUNDS):
            x, y, w, h = background_item_rect(i)
            if x <= lx < x + w and y <= ly < y + h: return ('background', index, name)
    if resolution_picker:
        for i, size in enumerate(RESOLUTIONS):
            x, y, w, h = resolution_item_rect(i)
            if x <= lx < x + w and y <= ly < y + h: return ('resolution', index, size)
        x, y, w, h = resolution_list_rect()
        if x <= lx < x + w and y <= ly < y + h: return ('panel', index)  # a column's heading or an empty cell
    x, y, w, h = PANEL_RES_BOX
    if x <= lx < x + w and y <= ly < y + h: return ('resolution_box', index)
    x, y, w, h = PANEL_SCALE_BOX
    if x <= lx < x + w and y <= ly < y + h: return ('scale_box', index)
    x, y, w, h = PANEL_BACKGROUND_BOX
    if x <= lx < x + w and y <= ly < y + h: return ('background_box', index)
    x, y, w, h = PANEL_BACKGROUND_SWITCH
    if x <= lx < x + w and y <= ly < y + h: return ('background_switch', index)
    if PANEL_BRIGHTNESS_TOP <= ly < PANEL_BRIGHTNESS_TOP + PANEL_ROW_H and PANEL_KNOB_LO - PANEL_SLOP_PX <= lx <= PANEL_KNOB_HI + PANEL_SLOP_PX:
        return ('slider', index, BRIGHTNESS_FIELD, lx)
    for i, name in enumerate(PANEL_ACTIONS):
        x, y, w, h = action_rect(i)
        if x <= lx < x + w and y <= ly < y + h: return ('action', index, name)
    row = math.floor((ly - PANEL_ROWS_TOP) / PANEL_ROW_H) if ly >= PANEL_ROWS_TOP else -1
    if 0 <= row < len(PLACEMENT_KEYS) and PANEL_KNOB_LO - PANEL_SLOP_PX <= lx <= PANEL_KNOB_HI + PANEL_SLOP_PX:
        if picker or resolution_picker: return ('panel', index)  # a list is open: no slider grabs (the press closes it)
        return ('slider', index, PLACEMENT_KEYS[row], lx)
    return ('panel', index)


def front_order(g, placements):
    """Desktop indices nearest first (the reverse of the renderer's back-to-front draw order)."""
    def depth(i):
        t = g['tiles'][i]
        x, y, z = place_point(g, t, placements[i], t[0] + t[2] / 2, t[1] + t[3] / 2)
        return x * x + y * y + z * z
    n = min(len(g['tiles']), len(placements))
    return sorted(range(n), key=depth)


def hit(g, placements, pointer, open_=None, icons=None, picker=False, resolution_picker=False, background_picker=False):
    """What is under canvas point `pointer`. `open_`: the desktop whose pill is expanded into its panel, or None. That
    panel is drawn on top, so it is tested first (panel_hit: ('pill', i) for its header, ('slider', i, field, panel x),
    ('panel', i)). Then each desktop front to back: ('corner', index, 'tl'|'tr'|'bl'|'br'), ('pill', index), ('edge',
    index, 't'|'b'|'l'|'r'), ('desktop', index, (px, py) tile pixel); then, where no corner, pill or content is, the level
    bars: ('roll', index, 'ccw'|'level'|'cw') (`icons`: per desktop None, 'none' or 'bar') and, with each, its 3D button:
    ('immersive', index, 'main'|'tail'). These also take an edge's
    border from LEVEL_EDGE_CLEAR out. None if nothing (the zone contract, ZONES: zone_of). Tested along the
    dot's ray (orbit.pointer_ray, where the renderer draws the dot), so anything placed anywhere round you can be reached.
    Handles never cover content: a point on a desktop's own rectangle (deeper than the ENTER_PX sliver at its edge) is
    ('desktop', ...) unless the open panel, drawn over the desktops, is there. The resize border (border_handle), the
    pill zone and the bars lie outside it, and where they reach over the picture of a desktop beside it that is the
    neighbour's: bumping lets neighbours closer than these zones are wide (bump.py). Only the pill as drawn stays: glass is
    drawn over the pictures. A desktop really in front of another (in_front_of) keeps its zones over the one behind."""
    if g is None or pointer is None: return None
    n = min(len(g['tiles']), len(placements))
    if type(open_) is int and 0 <= open_ < n:
        tile = g['tiles'][open_]
        q = desktop_to_glass(tile, placements[open_], unproject(g, tile, placements[open_], pointer))
        if q is not None:
            x, y, w, h = panel_rect(tile)
            if x <= q[0] < x + w and y <= q[1] < y + h: return panel_hit(q[0] - x, q[1] - y, open_, picker, resolution_picker, background_picker)
    order = front_order(g, placements)
    qs = {i: unproject(g, g['tiles'][i], placements[i], pointer) for i in order}
    picture = next((i for i in order if qs[i] is not None and inside_by(g['tiles'][i], qs[i]) >= ENTER_PX), None)  # the one on top
    below = edge = None  # a corner, pill or desktop under the dot: it takes the press; the nearest edge band
    for i in order:
        q = qs[i]
        if q is None: continue
        tile = g['tiles'][i]
        if picture is not None and picture != i and not in_front_of(g, placements, i, picture, q):  # beside it: only the glass shows
            if not on_drawn_pill(tile, desktop_to_glass(tile, placements[i], q)): continue
            below = ('pill', i)
            break
        border = border_handle(tile, q)
        if border is not None and border[0] == 'corner': below = ('corner', i, border[1])
        elif on_pill(tile, placements[i], q): below = ('pill', i)
        elif border is not None:
            if edge is None: edge = ('edge', i, border[1])  # the nearest edge's band; desktops behind it still count
            continue
        elif inside_by(tile, q) >= ENTER_PX: below = ('desktop', i, q)
        else: continue
        break
    # A bar only ever takes a press where nothing else is: a desktop's content (its apps' own clicks, drags and scrolling),
    # a resize corner's zone (which reaches round the corner, inside and out) and a pill all win over it. The renderer draws
    # the bars and caps before every desktop and under the glass (gl.cpp), so a bar shows exactly where it can be pressed.
    # An edge's border gives way to a bar from LEVEL_EDGE_CLEAR out, which leaves it the band next to the desktop. Where
    # two bars overlap (desktops moved over each other) the nearer desktop's wins.
    if below is not None: return edge if edge is not None else below
    for i in order:
        if icons is None or i >= len(icons) or icons[i] != 'bar': continue
        if maximize_hit(g['tiles'][i], qs[i]): return ('maximize', i)
        part = immersive_part(g['tiles'][i], qs[i])  # shows with the bar, under the same rules; asked first: see immersive_part
        if part is not None: return ('immersive', i, part)
        segment = level_bar_segment(g['tiles'][i], qs[i])
        if segment is not None: return ('roll', i, segment)
    return edge


def on_pill(tile, p, q):
    """True if tile pixel `q` of a desktop at placement `p` (None: missed) is in its pill zone: the pill padded by
    PILL_PAD_X / PILL_PAD_Y, glass px, plus the gap and the border up to the desktop's bottom edge. That edge is the
    desktop's own, so it is measured on the surface: the zone stops there at every size and never takes the content."""
    if q is None: return False
    gx, gy = desktop_to_glass(tile, p, q)
    px, py, pw, ph = pill_rect(tile)
    return px - PILL_PAD_X <= gx <= px + pw + min(PILL_PAD_X, 6) and tile[1] + tile[3] <= q[1] and gy <= py + ph + PILL_PAD_Y


def on_drawn_pill(tile, q):
    """True if tile pixel `q` (None: missed) is on a desktop's pill as drawn, without the padding on_pill adds."""
    if q is None: return False
    px, py, pw, ph = pill_rect(tile)
    return px <= q[0] <= px + pw and py <= q[1] <= py + ph


def in_front_of(g, placements, i, j, q):
    """True if desktop i really covers desktop j's picture at i's tile pixel `q`, just outside i: i's own edge next to q is on
    that picture too. False if the two are only beside each other, however close: i's edge stops short of it."""
    tile, other = g['tiles'][i], g['tiles'][j]
    edge = (min(max(q[0], tile[0]), tile[0] + tile[2]), min(max(q[1], tile[1]), tile[1] + tile[3]))
    on = unproject(g, other, placements[j], project(g, tile, placements[i], *edge))
    return on is not None and inside_by(other, on) >= ENTER_PX


def inside_by(tile, q):
    """How far tile pixel q is inside the tile (canvas px; negative outside)."""
    tx, ty, tw, th = tile
    return min(q[0] - tx, tx + tw - q[0], q[1] - ty, ty + th - q[1])


def resize_distance(start, corner, dx, dy, tile):
    """New `distance` for a resize drag of (dx, dy) canvas px on handle `corner`: a corner ('tl', 'tr', 'bl', 'br')
    follows the pointer along its diagonal, an edge ('t', 'b', 'l', 'r') along its normal (apparent size is
    100 / distance, about the desktop's centre, so the desktop keeps its shape either way). Clamped to the distance
    bounds."""
    if corner in EDGES:
        vx = {'l': -tile[2] / 2, 'r': tile[2] / 2}.get(corner, 0.0)
        vy = {'t': -tile[3] / 2, 'b': tile[3] / 2}.get(corner, 0.0)
        scale = max(MIN_SCALE, 100 / start + (dx * vx + dy * vy) / (vx * vx + vy * vy))
        lo, hi = PLACEMENT_BOUNDS['distance']
        return min(max(round(100 / scale), lo), hi)
    if corner not in resize.CORNERS: raise ValueError('invalid corner')
    vx = (-1 if corner[1] == 'l' else 1) * tile[2] / 2
    vy = (-1 if corner[0] == 't' else 1) * tile[3] / 2
    scale = max(MIN_SCALE, 100 / start + (dx * vx + dy * vy) / (vx * vx + vy * vy))
    lo, hi = PLACEMENT_BOUNDS['distance']
    return min(max(round(100 / scale), lo), hi)


def resize_reach(g, point):
    """Canvas point `point` as a corner drag measures it: unchanged inside the view, each axis RESIZE_OVERSHOOT_GAIN times
    as far past an edge. Monotonic, so dragging back in shrinks the desktop the same way."""
    def axis(v, size):
        if v < 0: return v * RESIZE_OVERSHOOT_GAIN
        return size + (v - size) * RESIZE_OVERSHOOT_GAIN if v > size else v
    return (axis(point[0], g['w']), axis(point[1], g['h']))


def desktop_at(g, placements, pointer):
    """The desktop whose surface is under canvas point `pointer` (front to back, the rectangle only: not its pill or the
    outer half of its corners), or None. Where a dragged window would land."""
    if g is None or pointer is None: return None
    for i in front_order(g, placements):
        tile = g['tiles'][i]
        q = unproject(g, tile, placements[i], pointer)
        if q is not None and tile[0] <= q[0] < tile[0] + tile[2] and tile[1] <= q[1] < tile[1] + tile[3]: return i
    return None


def ghost_size(window, output, tile, placement):
    """(w, h) canvas px of a dragged window's ghost: `window` (send.window_at: its 'w' / 'h' in layout px) as it appears
    on its desktop (`output` its monitor, `tile` its overview slot, `placement` its distance), times GHOST_SCALE, the
    longer side kept within GHOST_MIN_PX..GHOST_MAX_PX. GHOST_DEFAULT if any of it is unknown."""
    try:
        k = tile[2] / output['width'] * 100 / placement['distance'] * GHOST_SCALE
        w, h = float(window['w']) * k, float(window['h']) * k
    except (TypeError, KeyError, ZeroDivisionError, IndexError):
        return GHOST_DEFAULT
    if not (math.isfinite(w) and math.isfinite(h)) or w <= 0 or h <= 0: return GHOST_DEFAULT
    longer = max(w, h)
    f = min(max(longer, GHOST_MIN_PX), GHOST_MAX_PX) / longer
    return (min(max(int(round(w * f)), 1), MAX_GHOST_PX), min(max(int(round(h * f)), 1), MAX_GHOST_PX))


def overview_pointer_frame(run_id, session, request_id, pointer):
    """Renderer `overview_pointer` frame: {visible, x, y, held} and, while the dot is shown, {az, el}: x/y canvas
    px, az / el the dot's direction in hundredths of a degree (orbit.pointer_direction; the renderer draws the dot
    there). While a window is dragged also {drag: {w, h, target}}: the ghost's size (canvas px, 1..MAX_GHOST_PX) and the
    desktop it would land on (-1: none). Also {look: POINTER_LOOKS}, over a handle (look_for: a resize direction, move or
    rotate) or over text on a desktop's content (text); absent: the plain dot. spike/src/ipc.cpp checks the same."""
    keys = {'visible', 'x', 'y', 'held'}
    if isinstance(pointer, dict) and 'look' in pointer:
        if pointer['look'] not in POINTER_LOOKS: raise ValueError('invalid overview pointer')
        keys = keys | {'look'}
    drag = pointer.get('drag') if isinstance(pointer, dict) else None
    if isinstance(pointer, dict) and 'drag' in pointer and (not isinstance(drag, dict) or set(drag) != {'w', 'h', 'target'}
                             or any(type(drag[k]) is not int or not 1 <= drag[k] <= MAX_GHOST_PX for k in 'wh')
                             or type(drag['target']) is not int or not -1 <= drag['target'] < MAX_DESKTOPS):
        raise ValueError('invalid overview pointer')
    if isinstance(pointer, dict) and 'drag' in pointer: keys = keys | {'drag'}
    if (not isinstance(pointer, dict) or set(pointer) not in (keys, keys | {'az', 'el'}) or type(pointer['visible']) is not bool
            or type(pointer['held']) is not bool
            or any(type(pointer[k]) is not int or not -MAX_POINTER_PX <= pointer[k] <= MAX_POINTER_PX for k in 'xy')
            or any(type(pointer[k]) is not int or not -limit <= pointer[k] <= limit
                   for k, limit in (('az', MAX_POINTER_AZ), ('el', MAX_POINTER_EL)) if k in pointer)):
        raise ValueError('invalid overview pointer')
    return (json.dumps({'schema': 1, 'request_id': request_id, 'run_id': run_id, 'session': session, 'op': 'overview_pointer',
                        'payload': dict(pointer)}, separators=(',', ':')) + '\n').encode()


# Compositor Lua (generated, fixed shape; every cursor call in pcall so a refused change never breaks the binds).
# Free mode: remember the cursor's visibility and position once (the same table the bind removal restores, so a run
# that dies in free mode gives the cursor back on the next click), hide it and park it at (x, y).
_HIDE_LUA = (f'if {resize.GRAB_TABLE} == nil then '
             'local ok, hidden = pcall(hl.get_config, "cursor.invisible") local p = hl.get_cursor_pos() '
             f'{resize.GRAB_TABLE} = {{ invisible = ok and hidden == true, home = p and {{ x = p.x, y = p.y }} }} end '
             'pcall(hl.config, { cursor = { invisible = true } })')
# Back to desktop mode: the left button goes back to windows and the cursor's own visibility returns (not its old
# position: it is then warped to where the pointer is).
_SHOW_LUA = (f'do local s = {resize.GRAB_TABLE} {resize.GRAB_TABLE} = nil if s then '
             'pcall(hl.config, { cursor = { invisible = s.invisible } }) end end')


def grab_pointer_lua(python, client, control_socket, run_id, session, x, y):
    """Enter free mode: the left-button binds, the cursor hidden and parked at layout pixel (x, y). One eval."""
    return resize.install_resize_binds_lua(python, client, control_socket, run_id, session) + ' ' + _HIDE_LUA + ' ' + \
        view.warp_cursor_lua(x, y)


def release_pointer_lua(x=None, y=None):
    """Leave free mode for desktop mode: binds removed, cursor shown and (if given) put at layout pixel (x, y). One eval."""
    out = view._remove_lua(resize.BIND_TABLE) + ' ' + _SHOW_LUA
    return out + (' ' + view.warp_cursor_lua(x, y) if x is not None else '')


class OverviewPointer:
    """Pointer state, hover, drags and renderer frames, on the supervisor loop. The sampler thread hands in cursor samples
    (feed) and applies `want` (what the cursor and the left button should be doing) with compositor calls."""
    # Window drag (module docstring): off, so a press, hold or drag on a desktop always stays its window's
    # (anchor-no-click-maximize). True brings back the press-and-hold lift onto another desktop.
    window_drag = False

    def __init__(self, control, clock=time.monotonic):
        self.control, self.clock = control, clock
        self._lock = threading.Lock()
        self._abs, self._motion, self._applied, self._outputs, self._fresh = None, [0.0, 0.0], 0, [], False
        self._shape_text = False   # the newest word: the app under the pointer asks for a text cursor (feed_shape, push_shape)
        self._shape_serial = 0     # counts pushes: a read that a push overtook says nothing (shape_serial, feed_shape)
        self._push_text = False    # what the last push said, whatever the gate did with it
        self.shape_wired = False   # the sampler has a broker read: a run without one takes no push
        self.shape_pushed = False  # pushes arrive and the reads agree with them, so the sampler only checks (SHAPE_CHECK_S)
        self.shape_gated = False   # the beam's gate shut on a word: when it opens the sampler reads at SHAPE_S (_step_text)
        self.text_hold = TextHold()  # the Serif Beam is on (_step_text, _look)
        self.geometry = None       # parse_overview_geometry, from the renderer
        self.layout_stale = False  # the desktops changed since it came (_layout_ready)
        self.help_geometry = None  # parse_help_geometry, from the renderer: the help card while it is up in the overview
        self.outputs = []          # resize.outputs_from_monitors, from the sampler
        self.mode = 'off'          # off (not in the overview), away (cursor not on a desktop), desktop, free
        self.desktop = None        # desktop index the cursor is on (desktop mode) or left from (free mode)
        self.pointer = None        # (x, y) canvas px
        self.gain = 1.0            # canvas px per cursor px in free mode (the desktop's own scale when it was left)
        self.hover = None          # hit(): what a click would grab (free mode only)
        # Resize: {'kind': 'resize', 'index', 'handle', 'start', 'distance', 'tile'}  (handle: a corner or an edge)
        # Pill:   {'kind': 'pill', 'index', 'start', 'xy', 'tile', 'moved', 'open_at_press'}  (moved: past CLICK_PX, so
        #         not a click; open_at_press: the open panel's desktop or None after the press, a click needs it unchanged)
        # Slider: {'kind': 'slider', 'index', 'field', 'tile', 'placement'}  (placement: the desktop's at the press, the
        #         surface both the drag and the renderer's panel use until the release)
        # Roll:   {'kind': 'roll', 'index', 'side', 'start', 'from', 'centre', 'moved'}  (side: the bar's segment, 'level' a
        #         button that a drag cancels; from: its roll at the press, centre: the desktop's centre on the canvas, what
        #         the dot turns it round)
        # Window: {'kind': 'window', 'index', 'on', 'start', 'at', 'point', 'lifted', 'window', 'target', 'token'}  (on:
        #         tile px pressed, where a click lands; at: press time; point: layout px pressed on the desktop's monitor;
        #         window: None looking up, False none there, else send.window_at's dict; target: desktop under the dot)
        self.drag = None
        self.tilts = []            # parse_roll_geometry, from the renderer: each desktop's tilt from level, or None
        self.rolls = [0] * MAX_DESKTOPS  # each desktop's levelling roll, 1/100 degree (this run only)
        self.roll_names = list(control.state.names)  # whose roll each entry is (desktops_changed keeps them by name)
        self.roll_sent = {**ROLL_HIDDEN, 'roll': [0] * MAX_DESKTOPS, 'icons': ['none'] * MAX_DESKTOPS}
        self.roll_clicks = self.roll_drags = self.roll_levels = 0  # diagnostics
        self.levelling = {}  # desktop name -> until when its tilt reports level it again (R: level)
        self.last_move = clock()
        self.shown, self.pills, self.sent = dict(resize.HIDDEN), dict(resize.PILLS_HIDDEN), dict(POINTER_HIDDEN)
        self.want = (0, 'off', None)  # (serial, 'off'|'away'|'desktop'|'free', layout point or None); read by the sampler
        self._sync = 0                # cursor samples count in desktop mode only once the sampler applied this serial
        self.summoned = False         # Ctrl+Alt+Down: bring a cursor that is off the desktops into the selected one
        self.rehome = False           # a desktop change: summon the cursor once the pointer is back (desktops_changed)
        self.binds_dropped = False    # config reload: runtime binds and cursor state are gone (sampler redoes them)
        self.entries = self.exits = self.drags = self.moves = self.focus_clicks = 0  # diagnostics
        self.pill_clicks = self.slides = self.outside_closes = self.stale_clicks = self.help_clicks = 0  # diagnostics
        self.actions = 0  # diagnostics: desktop-row button presses
        self.bumps = 0    # diagnostics: drag updates that pushed another desktop out of the way (bump.py)
        self.super_stops = 0  # diagnostics: monitor drags stopped by Super going down (super_down)
        self.window_lifts = self.window_drops = self.window_cancels = 0  # diagnostics: window drags
        self._found = None  # (token, window) from the sender's lookup thread, taken on the loop (_take_found)
        self._token = 0
        reader, writer = socket.socketpair()
        reader.setblocking(False); writer.setblocking(False)
        self.wake_reader, self._wake_writer = reader, writer

    def close(self):
        self.wake_reader.close(); self._wake_writer.close()

    def overview(self):
        """In the overview with nothing else owning the mouse: not zoomed, no VR video, not in typing mode (view.py,
        Ctrl+Alt+T: every run bind but the toggle is released, the left button and cursor too, and the pills hide), and
        no key bind panel (keybinds.py: its submap leaves the pointer's button binds inactive, so the cursor is given back).
        An open placement panel keeps it on: the pointer clicks its pill and drags its sliders."""
        s = self.control.state
        return (not s.zoomed and not getattr(self.control, 'video_active', False) and not getattr(s, 'typing', False)
                and not getattr(self.control, 'keys_open', False))

    def active(self):
        return self.overview() and self._layout_ready() and len(self.outputs) > 0

    def _handles_on(self):
        """The pills, level bars and corner caps show (idle aside): whenever the pointer is on."""
        return self.active()

    def _layout_ready(self):
        """A renderer layout to hit-test with. After a desktop change the last one is kept, and used once it has one slot
        per desktop (until the renderer's next report, which replaces it)."""
        g = self.geometry
        if g is None: return False
        return not self.layout_stale or len(g['tiles']) == len(self.control.state.names)

    # --- inputs ---------------------------------------------------------------------------------------------------
    def set_geometry(self, geometry):
        self.geometry, self.layout_stale = geometry, False
        self._poke()

    def set_help_geometry(self, geometry):
        self.help_geometry = geometry
        self._poke()

    def set_roll_geometry(self, tilts):
        """The renderer's tilts (parse_roll_geometry). A desktop R just levelled (level) is levelled again on a measured one
        until LEVEL_SETTLE_S after the key."""
        self.tilts = list(tilts)
        now = self.clock()
        self.levelling = {n: until for n, until in self.levelling.items() if now <= until}
        for i, name in enumerate(self.control.state.names[:MAX_DESKTOPS]):
            if name in self.levelling and i < len(self.tilts) and self.tilts[i] is not None: self.rolls[i] = level_roll(self.tilts[i])
        self._poke()

    def level(self, i):
        """R on desktop i's open panel (view.ViewControl._on_menu, menu_reset) as its placement goes back flat in its slot,
        and a click on its level bar's middle segment, which moves nothing else: its rotation goes back to level
        (level_roll): no levelling roll, or, where its tilt from level is measured, the roll that cancels it. A reset may
        move the desktop, and with it its tilt, which the renderer reports only when it changes, so its measured tilts in the
        next LEVEL_SETTLE_S level it again where it went (set_roll_geometry). A bar drag on it ends. Returns the `roll` frame
        (b'' if nothing changed)."""
        names = self.control.state.names
        if not 0 <= i < min(len(names), MAX_DESKTOPS): return b''
        d = self.drag
        if d is not None and d['kind'] == 'roll' and d['index'] == i: self.drag = None
        self.rolls[i] = level_roll(self._tilt(i))
        now = self.clock()
        self.levelling[names[i]] = now + LEVEL_SETTLE_S
        self.roll_levels += 1
        return self._roll_frame(now - self.last_move >= view.OVERLAY_IDLE_CLOSE_S)

    def summon(self):
        self.summoned = True

    def desktops_changed(self):
        """The run's desktops changed (a pill's Add / Remove / Ultrawide): every index may mean another desktop now. Any
        drag ends where it is, and hit testing waits for the sampler's new monitors and a renderer layout with one slot per
        desktop (_layout_ready). The last layout and tilts are kept, never dropped: the renderer reports them only when
        they change, so a dropped one may never come again (dot-cursor-new-desktop). Once the pointer is back it brings
        the cursor onto the selected desktop (rehome), wherever the change left it."""
        self.drag = self.hover = None
        self.layout_stale = True
        names = list(self.control.state.names)  # each desktop keeps its own levelling roll and last measured tilt
        by_name = dict(zip(self.roll_names, self.rolls))
        self.rolls = [by_name.get(n, 0) for n in names] + [0] * (MAX_DESKTOPS - len(names))
        by_name = dict(zip(self.roll_names, self.tilts))
        self.tilts = [by_name.get(n) for n in names] if len(self.tilts) == len(self.roll_names) else []
        self.roll_names = names
        self.outputs = []
        with self._lock: self._outputs = []
        if self.desktop is not None and self.desktop >= len(self.control.state.names): self.desktop = None
        if self.mode != 'off':  # back in through away, as on entering the overview: the cursor is placed afresh
            self.mode, self.hover, self.pointer = 'off', None, None
            self._set_want('off')
        self.rehome = True
        self._poke()

    def feed(self, cursor, motion, outputs, applied):
        """Sampler thread: latest cursor (global logical px or None), motion since the last sample, monitor rectangles and
        the last `want` serial it applied. Wakes the loop when anything changed."""
        with self._lock:
            changed = cursor != self._abs or motion[0] or motion[1] or outputs != self._outputs or applied != self._applied
            self._abs, self._outputs, self._applied = cursor, outputs, applied
            self._motion[0] += motion[0]; self._motion[1] += motion[1]
            if changed: self._fresh = True
        if changed: self._poke()
        return changed

    def shape_serial(self):
        """Sampler thread, before a read: hand it back to feed_shape."""
        with self._lock: return self._shape_serial

    def feed_shape(self, name, serial=None):
        """Sampler thread: the cursor the app under the pointer asked for, by name, None for none or a failed read. A read from
        before the newest push (`serial`: shape_serial at its start) is older than that push and counts for nothing. One that
        disagrees with the last push means a push went missing, so the sampler reads at SHAPE_S until the next one arrives.
        Wakes the loop on a change, and at every read while a leave waits."""
        text = is_text_shape(name)
        with self._lock:
            if serial is not None and serial != self._shape_serial: return
            if text != self._push_text: self.shape_pushed = False
            changed = text != self._shape_text
            self._shape_text = text
        if changed or self.text_hold.until is not None: self._poke()

    def push_shape(self, name):
        """Loop thread: the plugin says the cursor changed (ViewControl.on_events; None for none). The poll that follows in this
        same pass of the loop sends the frame. Before the sampler has applied the pointer's last warp the name may be the
        place it left, so that one only counts as proof that pushes arrive: the read after the warp gets the truth."""
        text = is_text_shape(name)
        with self._lock:
            if not self.shape_wired: return
            self._shape_serial += 1
            self._push_text, self.shape_pushed = text, True
            if self._applied >= self._sync: self._shape_text = text

    def _poke(self):
        try: self._wake_writer.send(b'.')
        except OSError: pass  # full or closed: the loop's own 50 ms tick picks it up

    def drain(self):
        try:
            while self.wake_reader.recv(4096): pass
        except OSError: pass

    def _take(self):
        with self._lock:
            fresh, cursor, outputs, applied = self._fresh, self._abs, self._outputs, self._applied
            motion, self._motion, self._fresh = tuple(self._motion), [0.0, 0.0], False
        self.outputs = outputs
        return fresh, cursor, motion, applied

    # --- helpers --------------------------------------------------------------------------------------------------
    def _set_want(self, kind, point=None, sync=False):
        serial = self.want[0] + 1
        self.want = (serial, kind, point)
        if sync: self._sync = serial

    def _open(self):
        """The desktop whose pill is expanded into its panel, or None."""
        s = self.control.state
        return s.menu_desktop if s.menu_open else None

    def _help(self):
        """The help card's canvas geometry while the card is open (the renderer may still report a closing one)."""
        return self.help_geometry if getattr(self.control, 'help_visible', False) else None

    def _hit(self):
        """hit(), with the help card on top of everything while it is open."""
        return self._on_help() or self._options_hit() or hit(self.geometry, self._rolled(), self.pointer, self._open(), icons=self._icons(),
                                      picker=bool(getattr(self.control.state, 'scale_picker', False)),
                                      resolution_picker=bool(getattr(self.control.state, 'resolution_picker', False)),
                                      background_picker=bool(getattr(self.control.state, 'background_picker', False)))

    def _options_hit(self):
        from . import options_geometry as og
        g = self.geometry
        if g is None or self.pointer is None: return None
        menu = getattr(self.control, 'options', None)
        d = menu.snapshot() if menu is not None else None
        placements = self._rolled()
        if d and d['open'] and d['monitor'] in self.control.state.names:
            i = self.control.state.names.index(d['monitor'])
            if i < len(g['tiles']):
                tile = g['tiles'][i]
                p = menu.capture['placement'] if menu.capture else placements[i]
                q = desktop_to_glass(tile, p, unproject(g, tile, p, self.pointer))
                r = og.panel_rect(tile, pill_rect(tile))
                if og.inside(r, q): return ('options_panel', i, q[0]-r[0], q[1]-r[1])
        for i in front_order(g, placements):
            tile = g['tiles'][i]
            q = desktop_to_glass(tile, placements[i], unproject(g, tile, placements[i], self.pointer))
            if og.gear_hit(pill_rect(tile), q): return ('options_gear', i)
        return None

    def options_wheel(self, delta):
        """Outer wheel subscription must pass through when False is returned."""
        h = self._options_hit()
        if h is None or h[0] != 'options_panel': return False, b''
        return True, self.control.options_intent('options_scroll', delta=min(max(int(delta), -4096),4096))

    def _on_help(self, margin=0.0):
        """help_hit for the dot: the card is reported on the overview's plane, so the dot's ray is met there."""
        help_ = self._help()
        if not help_ or self.pointer is None or self.geometry is None: return None
        return help_hit(help_, orbit.plane_at(self.geometry, orbit.pointer_ray(self.geometry, self.pointer)), margin)

    def _rolled(self):
        """The placements as drawn: each with its levelling roll (orbit.place_point's 'roll') when it has one."""
        return [{**p, 'roll': self.rolls[i]} if i < len(self.rolls) and self.rolls[i] else p
                for i, p in enumerate(self.control.state.placements)]

    def _tilt(self, i):
        return self.tilts[i] if i < len(self.tilts) else None

    def _icons(self):
        """Per desktop what it shows (the wire's icons): its level bar on every desktop, level or not, measured or not (a
        desktop can always be rolled either way; they only hide with the pills, _roll_frame)."""
        return ['bar'] * len(self.control.state.placements)

    def _around(self):
        """Anchor mode: the dot's reach goes all the way round you (orbit.reach). A drag keeps the reach it started with."""
        d = getattr(self, 'drag', None)
        return d['around'] if d is not None and 'around' in d else self.control.state.mode == 'anchor'

    def _output(self, index):
        return next((o for o in self.outputs if o['index'] == index), None)

    def _placement(self, i):
        """Desktop i's placement as drawn (with its levelling roll), for hit testing and cursor mapping."""
        p = self._rolled()
        return p[i] if i < len(p) else None

    def _tile(self, i):
        g = self.geometry
        return g['tiles'][i] if g is not None and i < len(g['tiles']) else None

    def _from_logical(self, i, lx, ly):
        """Monitor-local logical px on desktop i -> where the dot is drawn (canvas px; layout_to_surface, project)."""
        o, tile, p = self._output(i), self._tile(i), self._placement(i)
        if o is None or tile is None or p is None: return None
        return project(self.geometry, tile, p, *layout_to_surface(tile, o, lx, ly))

    def _exit(self, i, lx, ly):
        """Where the dot goes free when the cursor at monitor-local (lx, ly) on desktop i reaches its edge pixel (or past
        it, onto another monitor): EXIT_PX logical px beyond each edge it reached, plus as far past it as Hyprland let the
        cursor go, mapped onto the desktop's surface (layout_to_surface extends past its edges) and projected. So the dot
        never stays free over content: it sits on that edge's (or corner's) resize handle, and any move back inside
        hands the real cursor back (ENTER_PX). None without the desktop's monitor or layout."""
        o = self._output(i)
        if o is None: return None
        def axis(v, size):
            if v < EDGE_PX: return min(v, 0.0) - EXIT_PX
            if v > size - 1 - EDGE_PX: return max(v + 1, size) + EXIT_PX
            return v
        return self._from_logical(i, axis(lx, o['width']), axis(ly, o['height']))

    def _to_layout(self, i, q):
        """Tile pixel q of desktop i -> integer layout pixel inside its monitor (surface_to_layout, INSET_PX in)."""
        o, tile = self._output(i), self._tile(i)
        if o is None or tile is None: return None
        return surface_to_layout(tile, o, q)

    def _gain(self, i):
        o, tile, p = self._output(i), self._tile(i), self._placement(i)
        if o is None or tile is None or p is None: return self.gain
        return tile[2] / o['width'] * 100 / p['distance']

    def _clamp(self, point):
        """The pointer kept within its reach (orbit.reach): in anchor mode to directly behind you and the poles."""
        g = self.geometry
        ru, rv = orbit.reach(g, self._around())
        return (min(max(point[0], g['w'] * (1 - ru) / 2), g['w'] * (1 + ru) / 2),
                min(max(point[1], g['h'] * (1 - rv) / 2), g['h'] * (1 + rv) / 2))

    def _at(self, cursor):
        """(index, lx, ly) of the XR monitor holding layout point `cursor`, or None."""
        for o in self.outputs:
            lx, ly = cursor[0] - o['x'], cursor[1] - o['y']
            if 0 <= lx < o['width'] and 0 <= ly < o['height']: return o['index'], lx, ly
        return None

    def _select(self, i):
        """The desktop the pointer went into (or clicked) becomes the selected one: Ctrl+Alt+Up zooms into it, and an open
        panel goes with it (view.ViewControl.follow_focus). Returns a view frame if that changed."""
        s = self.control.state
        if i >= len(s.names) or s.selected == i: return b''
        s.selected = i
        return self.control._frame() + self.control.follow_focus()

    def _enter(self, i, q):
        """Free -> desktop mode on desktop i at tile pixel q: the cursor comes back there, visible."""
        target = self._to_layout(i, q)
        if target is None: return b''
        self.mode, self.desktop, self.hover = 'desktop', i, None
        self._set_want('desktop', target, sync=True)
        self.entries += 1
        return self._select(i)

    def _click_desktop(self, i, q):
        """A dot click on desktop i at tile pixel q: select it and stay in the overview (view.ViewControl.select_desktop;
        anchor-no-click-maximize: a click never maximizes, Ctrl+Alt+Up does). The real cursor comes back there under the
        dot (_enter), which gives that monitor Hyprland's focus."""
        self.focus_clicks += 1
        out = self.control.select_desktop(i)
        if not self.active() or self.control.state.selected != i: return out  # stale: zoomed, typing, a video, gone
        return out + self._enter(i, q)

    # --- the loop -------------------------------------------------------------------------------------------------
    def poll(self, now):
        """Supervisor tick: apply the newest cursor sample; returns renderer frames (bytes)."""
        fresh, cursor, motion, applied = self._take()
        out = b''
        if not self.active():
            if self.mode != 'off':
                out += self._end_drag()
                self.mode, self.hover, self.pointer, self.summoned = 'off', None, None, False
                self._set_want('off')
        else:
            if self.mode == 'off':
                self.mode, self.desktop, self.pointer = 'away', None, None
                self._set_want('away')
                fresh = True  # the cursor where it already is counts: a still mouse never sends another sample
                if self.rehome: self.summoned, self.rehome = True, False
            self._take_found()
            if self.mode == 'free':
                if motion[0] or motion[1]: out += self._move(motion[0] * self.gain, motion[1] * self.gain, now)
                out += self._hold(now)
            elif self.mode == 'carry':  # a Super drag between desktops (unified_cursor.py)
                if motion[0] or motion[1]: out += self._carry(motion[0] * self.gain, motion[1] * self.gain, now)
            elif fresh and cursor is not None and applied >= self._sync:
                out += self._absolute(cursor, now)
            else:
                self._onto_help()  # no cursor motion: the card may have come over the cursor itself
        idle = now - self.last_move >= view.OVERLAY_IDLE_CLOSE_S
        if idle:
            out += self._end_drag()
            self.hover = None
        self._take_found()  # a lookup answered meanwhile (at once, for a cached one) shows in this frame
        self._step_text(now)
        return out + self._frames(idle)

    def _absolute(self, cursor, now):
        """Away / desktop mode: the cursor's own position decides."""
        out = b''
        at = self._at(cursor)
        if self.mode == 'away':
            if at is None:
                if self.summoned:  # Ctrl+Alt+Down with the cursor elsewhere: into the middle of the selected desktop
                    self.summoned = False
                    i = self.control.state.selected
                    tile = self._tile(i)
                    if tile is not None and self._output(i) is not None:
                        self.mode, self.desktop = 'desktop', i
                        self._set_want('desktop', self._to_layout(i, (tile[0] + tile[2] / 2, tile[1] + tile[3] / 2)), sync=True)
                return out
            self.mode, self.desktop = 'desktop', at[0]
            out += self._select(at[0])
        self.summoned = False
        o = self._output(self.desktop)
        if o is None:
            self.mode, self.pointer = 'away', None
            return out
        lx, ly = cursor[0] - o['x'], cursor[1] - o['y']
        w, h = o['width'], o['height']
        if EDGE_PX <= lx <= w - 1 - EDGE_PX and EDGE_PX <= ly <= h - 1 - EDGE_PX:
            point = self._from_logical(self.desktop, lx, ly)
            if point is not None and point != self.pointer: self.pointer, self.last_move = point, now
            self._onto_help()
            return out
        cx, cy = min(max(lx, 0), w - 1), min(max(ly, 0), h - 1)
        over = (lx - cx, ly - cy)
        if math.hypot(*over) > JUMP_PX:  # a keyboard focus change or warp, not the hand: follow the cursor
            if at is not None:
                self.desktop = at[0]
                out += self._select(at[0])
                self.pointer = self._from_logical(at[0], at[1], at[2])
            else:
                self.mode, self.pointer = 'away', None
            return out
        start = self._exit(self.desktop, lx, ly)
        if start is None: return out
        self.gain = self._gain(self.desktop)
        self.pointer = self._clamp(start)
        self.mode, self.last_move = 'free', now
        self.exits += 1
        self._set_want('free', self._park(o, (cx, cy)))
        self.hover = self._hit()
        return out

    def _onto_help(self):
        """Desktop mode with the pointer on the open help card (the cursor moved onto it, or the card opened or turned
        with the head over a still cursor): the card is on top, so the pointer turns into the free dot right there and
        the left button goes to the run, not to the window hidden under the card."""
        o = self._output(self.desktop)
        if self.mode != 'desktop' or o is None or self._on_help() is None: return
        self.gain = self._gain(self.desktop)
        self.mode = 'free'
        self.exits += 1
        self._set_want('free', self._park(o))
        self.hover = self._hit()

    def _park(self, o, local=None):
        """Where free mode parks the hidden cursor on output `o` (`local`: where it left the desktop, monitor-local px):
        the middle of the monitor, so hand motion has room before any edge."""
        return (int(o['x'] + o['width'] / 2), int(o['y'] + o['height'] / 2))

    def _move(self, dx, dy, now):
        """Free mode: the pointer moves by the cursor's motion; a drag follows it, otherwise it may go into a desktop."""
        self.pointer = self._clamp((self.pointer[0] + dx, self.pointer[1] + dy))
        self.last_move = now
        if self.control.state.menu_open: self.control.last_input = now  # moving over an open panel keeps it up
        if self.drag is not None: return self._apply_drag()
        return self._settle()

    def _settle(self):
        """The free dot with nothing held: what is under it now and, on a desktop's content, the real cursor there."""
        self.hover = self._hit()
        if self.hover is not None and self.hover[0] == 'desktop' and inside_by(self._tile(self.hover[1]), self.hover[2]) >= ENTER_PX \
                and self._on_help(HELP_CLEAR_PX) is None:  # and HELP_CLEAR_PX clear of the help card
            return self._enter(self.hover[1], self.hover[2])
        return b''

    def on_op(self, op):
        """resize_press / resize_release: the left button, bound only in free mode (module docstring: Clicks)."""
        if op not in resize.RESIZE_OPS: raise ValueError('unsupported resize operation')
        now = self.clock()
        idle = now - self.last_move >= view.OVERLAY_IDLE_CLOSE_S
        self.last_move = now  # a click is input: the pointer, pills, bars and caps come back
        out = b''
        if op == 'resize_release':
            if self.drag is not None and self.drag['kind'] == 'roll' and not self.overview():
                self.drag = None  # maximized since the press: a maximized desktop is level, so no turn lands behind it
            d = self.drag
            out += self._apply_drag()
            if d is not None and d['kind'] == 'options_range':
                menu = self.control.options
                if menu.capture is d:
                    q = desktop_to_glass(d['tile'], d['placement'], unproject(self.geometry,d['tile'],d['placement'],self.pointer))
                    if q is not None:
                        from . import options_geometry as og
                        x = q[0]-og.panel_rect(d['tile'],pill_rect(d['tile']))[0]
                        r = d['row']
                        value=r['minimum']+min(max((x-og.PAD)/(og.W-2*og.PAD),0),1)*(r['maximum']-r['minimum'])
                        if r['step']>0: value=r['minimum']+round((value-r['minimum'])/r['step'])*r['step']
                        out += self.control.options_intent('options_set',row_id=r['id'],monitor=d['monitor'],scope=d['scope'],revision=d['revision'],value=min(max(value,r['minimum']),r['maximum']))
                    menu.capture=None
                self.drag=None
            if d is not None and d['kind'] == 'maximize':
                self.drag = None
                out += self.control.maximize_monitor(d['monitor'])
            if d is not None and d['kind'] == 'pill' and not d['moved']:  # a click, not a drag: expand / collapse
                if self._open() == d['open_at_press']:
                    self.drag = None
                    self.pill_clicks += 1
                    out += self.control.pill_click(d['index'])
                else:  # a key changed the open panel while the button was down: that key wins, the release is dropped
                    self.stale_clicks += 1
            elif d is not None and d['kind'] == 'window':
                self.drag = None
                if d['lifted']: out += self._drop_window(d)
                else: out += self._click_desktop(d['index'], d['on'])  # a click, as before: select it
            elif d is not None and d['kind'] == 'roll' and not d['moved']:
                if d['side'] == 'level': out += self.level(d['index'])  # the middle segment: level, nothing else moves
                else:  # a click on a turn segment: that way, to level or a step
                    self.roll_clicks += 1
                    self.rolls[d['index']] = roll_click(self._tilt(d['index']), d['from'], d['side'])
            out += self._end_drag()  # (a release on a resize handle that resized nothing does nothing)
            if d is not None and self.mode == 'free' and self.pointer is not None:  # the drag kept the dot's look; it is over:
                # What is under the dot now, the real cursor if that is content (a dropped window and a slider, whose panel
                # is in use, leave the dot free until it next moves; so does a resize that pushed a neighbour right up to the
                # handle, which can leave the dot on that neighbour's picture: a resize never changes the selection).
                h = self._hit()
                if d['kind'] in ('window', 'slider') or (d['kind'] in RESIZE_DRAGS and h is not None and h[0] == 'desktop' and h[1] != d['index']):
                    self.hover = h
                else: out += self._settle()
        elif self.active() and self.mode == 'free' and not idle and self.pointer is not None:
            out += self._end_drag()  # a press during a drag: its release was lost; finish that one first
            open_ = self._open()
            self.hover = h = self._hit()
            if h is not None and h[0] == 'help':  # the help card is on top: its arrows and indicator turn pages
                self.help_clicks += 1
                if h[1] != 'card': out += self.control.help_turn(-1 if h[1] == 'prev' else 1)
                return (out + self._frames(False)) or None
            if h is not None and h[0] == 'options_gear':
                return self.control.options_intent('options_open', monitor=self.control.state.names[h[1]])
            if h is not None and h[0] == 'options_panel':
                from . import options_geometry as og
                menu = self.control.options
                menu.focused = True
                d = menu.snapshot()
                row = og.row_at(h[2], h[3], d['scroll'], len(d['rows']))
                if h[3] < 44:
                    if h[2] >= 530: out += self.control.options_intent('options_close')
                    elif 350 <= h[2] < 440: out += self.control.options_intent('options_scope', scope='workspace' if d['scope']=='monitor' else 'monitor')
                    elif 440 <= h[2] < 530:
                        from .options_menu import CATEGORIES
                        out += self.control.options_intent('options_category', category=CATEGORIES[(CATEGORIES.index(d['category'])//4*4+4)%len(CATEGORIES)])
                elif 44 <= h[3] < 80:
                    from .options_menu import CATEGORIES
                    n = CATEGORIES.index(d['category'])//4*4 + int((h[2]-16)//142)
                    if h[2]>=16 and 0 <= n < len(CATEGORIES): out += self.control.options_intent('options_category', category=CATEGORIES[n])
                elif row is not None:
                    out += self.control.options_intent('options_focus', focus=row)
                    r = d['rows'][row]
                    if r['kind']=='range' and all(k in r for k in ('minimum','maximum','step')):
                        i = h[1]
                        self.drag = dict(kind='options_range', index=i, start=self.pointer, tile=self._tile(i),
                                         placement=dict(self._rolled()[i]), row=dict(r), monitor=d['monitor'],
                                         scope=d['scope'], revision=d['revision'])
                        menu.capture=self.drag
                    else: out += self.control.options_activate(row)
                return out + self.control._options_frame()
            if h is not None and h[0] == 'maximize':
                self.drag = {'kind': 'maximize', 'monitor': self.control.state.names[h[1]], 'index': h[1], 'start': self.pointer}
                return out
            own = open_ is not None and h is not None and h[1] == open_ and h[0] in PANEL_KINDS
            if own: self.control.panel_keys_on()  # the panel is in use: its keys are back if a window had them
            if open_ is not None and not own and not (h is not None and h[0] == 'desktop'):
                self.outside_closes += 1
                out += self.control.pill_close()  # a click outside collapses the open panel; the press carries on (what
                # it hit was not on that panel, so the panel's going changes nothing about it). A desktop is no outside:
                # selecting it brings the panel there.
            elif getattr(self.control.state, 'scale_picker', False) and (h is None or h[0] not in ('scale', 'scale_box')):
                out += self.control.scale_close()  # a press on the panel away from the open Scale list closes it
            elif getattr(self.control.state, 'resolution_picker', False) and (h is None or h[0] not in ('resolution', 'resolution_box')):
                out += self.control.resolution_close()  # ... and away from the open Resolution list
            elif getattr(self.control.state, 'background_picker', False) and (h is None or h[0] not in ('background', 'background_box')):
                out += self.control.background_close()  # ... and away from the open Background list
            if h is not None and h[0] == 'roll':
                i, tile = h[1], self._tile(h[1])
                if i < len(self.control.state.names): self.levelling.pop(self.control.state.names[i], None)  # yours now
                p = self._placement(i)
                centre = project(self.geometry, tile, p, tile[0] + tile[2] / 2, tile[1] + tile[3] / 2)
                self.drag = {'kind': 'roll', 'index': i, 'side': h[2], 'start': self.pointer, 'from': self.rolls[i],
                             'centre': centre if centre is not None else (tile[0] + tile[2] / 2, tile[1] + tile[3] / 2), 'moved': False}
            elif h is not None and h[0] in ('corner', 'edge', 'pill'):
                i, tile, p = h[1], self._tile(h[1]), self._placement(h[1])
                if h[0] != 'pill':  # a resize handle: the border round the desktop, never its content (border_handle)
                    self.drag = {'kind': 'resize', 'index': i, 'handle': h[2], 'start': self.pointer, 'distance': p['distance'], 'tile': tile}
                    self.drags += 1
                else:  # the desktop moves only once the pointer is CLICK_PX away; a release before that is a click
                    self.drag = {'kind': 'pill', 'index': i, 'start': self.pointer, 'xy': (p['x'], p['y']), 'tile': tile, 'moved': False,
                                 'open_at_press': self._open(),  # after any click-outside collapse above
                                 'around': self._around()}  # the dot's reach the drag keeps to its end
            elif h is not None and h[0] == 'slider':
                i = h[1]
                self.drag = {'kind': 'slider', 'index': i, 'field': h[2], 'tile': self._tile(i),
                             'placement': dict(self.control.state.placements[i])}  # the wire placement (no roll)
                self.slides += 1
                # The value jumps to the pointer; the panel stays drawn on the press-time placement until the release.
                out += self.control.pill_slide(i, h[2], slider_fraction(h[3]), surface=self.drag['placement'])
            elif h is not None and h[0] == 'immersive':  # the 3D button, like the panel's switches: this desktop, or the tail: all
                self.actions += 1
                out += self.control.immersive_click(h[1]) if h[2] == 'main' else self.control.immersive_tail_click(h[1])
            elif h is not None and h[0] == 'action':  # the desktop row: Ultrawide / Add / Remove for the panel's desktop
                self.actions += 1
                out += self.control.desktop_action(h[1], h[2])
            elif h is not None and h[0] == 'scale_box':  # the Scale dropdown opens / closes its list
                out += self.control.scale_box(h[1])
            elif h is not None and h[0] == 'scale':  # a step of the open list: that desktop's output scale
                self.actions += 1
                out += self.control.scale_pick(h[1], h[2])
            elif h is not None and h[0] == 'resolution_box':  # the Resolution dropdown opens / closes its list
                out += self.control.resolution_box(h[1])
            elif h is not None and h[0] == 'resolution':  # a size of the open list: that desktop's resolution, live
                self.actions += 1
                out += self.control.resolution_pick(h[1], h[2])
            elif h is not None and h[0] == 'background_box':  # the Background dropdown opens / closes its list
                out += self.control.background_box(h[1])
            elif h is not None and h[0] == 'background':  # an item of the open list: that background
                self.actions += 1
                out += self.control.background_pick(h[1], h[2])
            elif h is not None and h[0] == 'background_switch':  # as Ctrl+Alt+E
                self.actions += 1
                out += self.control.background_switch(h[1])
            elif h is not None and h[0] == 'desktop':  # a click selects it on release; a hold or drag lifts its window
                self.drag = self._window_press(h[1], h[2], self.pointer, self._to_layout(h[1], h[2]), now)
            # ('panel', i): elsewhere on the open panel, nothing; None: empty space, nothing (beyond the collapse above)
        return (out + self._frames(False)) or None

    def super_down(self):
        """Super went down (resize.py's bind on the Super keys, there while the left button is the run's): Super + drag is
        a window's, so a monitor drag in progress (MONITOR_DRAGS; it began without Super) stops where it is, saved as on a
        release. It is no click: the button's later release finds nothing held. Frames or None."""
        d = self.drag
        if d is None or d['kind'] not in MONITOR_DRAGS: return None
        self.super_stops += 1
        return (self._end_drag() + self._frames(False)) or None

    # --- window drag ------------------------------------------------------------------------------------------------
    def _window_press(self, i, q, start, point, now):
        """A press on desktop i at tile pixel q (dot at canvas `start`, layout pixel `point` on its monitor): a window drag
        not lifted yet. Released as it is, it is a click."""
        return {'kind': 'window', 'index': i, 'on': q, 'start': start, 'at': now, 'point': point, 'lifted': False,
                'window': None, 'target': None, 'token': None}

    def _hold(self, now):
        """Free mode, every tick: a press held HOLD_S lifts its window even if the dot never moved."""
        d = self.drag
        if not self.window_drag or d is None or d['kind'] != 'window' or d['lifted'] or now - d['at'] < HOLD_S: return b''
        self._lift(d)
        d['target'] = desktop_at(self.geometry, self._rolled(), self.pointer)
        return b''

    def _lift(self, d):
        """The press becomes a window drag: ask (off the loop) which window is shown where it was pressed."""
        d['lifted'] = True
        self.window_lifts += 1
        sender = getattr(self.control, 'sender', None)
        names = self.control.state.names
        if sender is None or d['point'] is None or d['index'] >= len(names):
            d['window'] = False  # nothing can be moved: the drag shows nothing and its release does nothing
            return
        self._token += 1
        d['token'] = token = self._token
        def found(window):  # the sender's worker thread
            with self._lock: self._found = (token, window)
            self._poke()
        sender.lookup(names[d['index']], d['point'], found)

    def _take_found(self):
        """The lookup's answer, for the drag that asked (a later drag ignores an earlier one's)."""
        with self._lock: found, self._found = self._found, None
        d = self.drag
        if found is None or d is None or d['kind'] != 'window' or d['token'] != found[0]: return
        window = found[1]
        d['window'] = window if isinstance(window, dict) and not window.get('pinned') else False

    def _drop_window(self, d):
        """Release of a lifted window: onto the desktop under the dot if that is another one, else nowhere."""
        self._take_found()
        target = desktop_at(self.geometry, self._rolled(), self.pointer) if self.pointer is not None else None
        names = self.control.state.names
        sender = getattr(self.control, 'sender', None)
        if target is None or target == d['index'] or d['window'] is False or sender is None \
                or d['index'] >= len(names) or target >= len(names):
            self.window_cancels += 1
            return b''
        self.window_drops += 1
        sender.drop(d['window'], names[d['index']], d['point'], names[target])
        return b''

    def _drag_display(self):
        """The `overview_pointer` frame's `drag` while a lifted window is dragged, else None."""
        d = self.drag
        if d is None or d['kind'] != 'window' or not d['lifted'] or d['window'] is False: return None
        i = d['index']
        w, h = ghost_size(d['window'], self._output(i), self._tile(i), self._placement(i)) if d['window'] else GHOST_DEFAULT
        t = d['target']
        return {'w': w, 'h': h, 'target': t if t is not None and t != i and t < MAX_DESKTOPS else -1}

    def _frames(self, idle):
        return self._handle() + self._pills(idle) + self._pointer_frame(idle) + self._roll_frame(idle) + self._immersive_hover(idle)

    def _immersive_hover(self, idle):
        """The 3D segment or tail under the dot lights, as a bar's segment does."""
        h = self.hover if self.drag is None and self.mode == 'free' and not idle and self.overview() else None
        tell = getattr(self.control, 'immersive_pointer', None)  # (a control without 3D)
        if tell is None: return b''
        return tell(h[1], h[2] == 'tail') if h is not None and h[0] == 'immersive' else tell(-1)

    def _apply_drag(self):
        d = self.drag
        if d is None or self.pointer is None: return b''
        c = self.control
        if d['kind'] in ('maximize', 'options_range'): return b''
        if d['kind'] == 'slider':
            # Measured on the panel as it was at the press, where the renderer keeps drawing it until the release (the
            # frame's `surface`): a setting that moves its own desktop would otherwise move the panel and chase itself.
            # A key that collapsed or moved the panel meanwhile dropped that surface: the rest of this drag does nothing.
            if c.state.menu_surface is None: return b''
            roll = self.rolls[d['index']] if d['index'] < len(self.rolls) else 0
            placed = {**d['placement'], 'roll': roll}
            q = desktop_to_glass(d['tile'], placed, unproject(self.geometry, d['tile'], placed, self.pointer))
            if q is None: return b''
            return c.pill_slide(d['index'], d['field'], slider_fraction(q[0] - panel_rect(d['tile'])[0]))
        dx, dy = self.pointer[0] - d['start'][0], self.pointer[1] - d['start'][1]
        if d['kind'] == 'window':
            if not d['lifted']:
                if not self.window_drag or math.hypot(dx, dy) <= WINDOW_DRAG_PX: return b''  # still a click (or a hold: _hold)
                self._lift(d)
            d['target'] = desktop_at(self.geometry, self._rolled(), self.pointer)
            return b''  # the ghost and the target go out with the dot (_pointer_frame)
        if d['kind'] == 'roll':  # the desktop turns with the dot round its centre, either way (a detent at level)
            if not d['moved']:
                if math.hypot(dx, dy) <= CLICK_PX: return b''  # still a click
                d['moved'] = True
                if d['side'] != 'level': self.roll_drags += 1
            if d['side'] == 'level': return b''  # a button: dragged off, its release does nothing
            cx, cy = d['centre']
            turn = math.atan2(cy - self.pointer[1], self.pointer[0] - cx) - math.atan2(cy - d['start'][1], d['start'][0] - cx)
            turn = (turn + math.pi) % (2 * math.pi) - math.pi  # counter-clockwise as you see it
            self.rolls[d['index']] = roll_drag(self._tilt(d['index']), d['from'], math.degrees(turn) * 100)
            return b''  # the `roll` frame goes out with the others (_roll_frame)
        if d['kind'] in RESIZE_DRAGS:  # a resize handle: the size it asks for, bumping what it grows into
            (ax, ay), (bx, by) = resize_reach(self.geometry, d['start']), resize_reach(self.geometry, self.pointer)
            placements = self._resize_to(d, resize_distance(d['distance'], d['handle'], bx - ax, by - ay, d['tile']))
        else:  # a move handle (the pill, or the open panel's header strip): the turn round you, bumping what it meets
            if not d['moved']:
                if math.hypot(dx, dy) <= CLICK_PX: return b''  # still a click
                d['moved'] = True
                self.moves += 1
            if 'orbit' not in d:  # the turn round you keeping the grabbed point under the dot, pushing what it meets (orbit.py)
                d['orbit'] = orbit.PillDrag(self.geometry, d['index'], d['xy'], d['start'], self.rolls)
            placements = d['orbit'].follow(self.pointer, c.state.placements)
        if placements == c.state.placements: return b''
        if any(p != q for i, (p, q) in enumerate(zip(placements, c.state.placements)) if i != d['index']): self.bumps += 1
        c.state.placements = placements
        c.save_due = c.clock() + view.MENU_SAVE_DELAY
        return c._placement_frame() + (c._menu_frame() if c.state.menu_open else b'')

    def _resize_to(self, d, distance):
        """Every desktop's placement for resize drag `d` (any resize handle of the hit-zone contract: a corner; the edges
        too where they are handles) asking for `distance`: the desktop takes it, growing pushes the desktops it runs into
        out of its way and with no room left it stops growing (bump.py; one Pusher for the whole drag)."""
        if 'bump' not in d: d['bump'] = bump.Pusher(self.geometry, d['index'], self.rolls)
        return d['bump'].move(self.control.state.placements, {'distance': distance})

    def _end_drag(self):
        """Release, idle, zoom-in, typing mode or a video: the drag ends and is saved at once. A held slider's panel goes
        back onto its desktop's live placement (a `placement_menu` frame without `surface`)."""
        d = self.drag
        if d is None: return b''
        self.drag = None
        if d['kind']=='options_range': self.control.options.capture=None
        out = self.control.pill_release() if d['kind'] == 'slider' else b''
        self.control.flush()  # saved at once on release (no-op if nothing changed)
        return out

    # --- frames ---------------------------------------------------------------------------------------------------
    def _send(self, make, state):
        c = self.control
        frame = make(c.run_id, c.session, c.next_id, state)
        c.next_id += 1
        return frame

    def _handle(self):
        d, h = self.drag, self.hover
        # The resize_handle frame lights a corner's cap; an edge has the dot's resize look only (_look).
        if d is not None and d['kind'] == 'resize' and d['handle'] in resize.CORNERS: target, active = (d['index'], d['handle']), True
        elif d is None and self.mode == 'free' and h is not None and h[0] == 'corner': target, active = h[1:], False
        else: target, active = None, False
        want = ({'visible': True, 'desktop': target[0], 'corner': target[1], 'active': active}
                if target is not None else {**self.shown, 'visible': False, 'active': False})
        if want == self.shown: return b''
        self.shown = want
        return self._send(resize.handle_frame, want)

    def _pills(self, idle):
        """`move_handle`: the glass pills show under every desktop in the overview while the pointer moved in the last
        30 s (OLED idle close; an open panel is drawn regardless). hover: the pointer is on that pill or the open panel's
        header (or holds it); active: that pill is being dragged (moved past CLICK_PX)."""
        visible = self._handles_on() and not idle
        d, h = self.drag, self.hover
        if d is not None and d['kind'] == 'pill': target, hover, active = d['index'], True, d['moved']
        elif d is None and self.mode == 'free' and h is not None and h[0] == 'pill': target, hover, active = h[1], True, False
        else: target, hover, active = self.pills['desktop'], False, False
        want = {'visible': visible, 'desktop': target, 'hover': hover and visible, 'active': active and visible}
        if want == self.pills: return b''
        self.pills = want
        return self._send(resize.pill_frame, want)

    def _roll_frame(self, idle):
        """`roll`: every desktop's levelling roll, and the level bars and corner caps shown with the pills (_pills: in the
        overview, not idle). hover / side: the desktop and segment under the dot or held, the only one lit; active: it is
        being dragged. A press dragged off the middle segment lights nothing: its release will do nothing."""
        visible = self._handles_on() and not idle
        icons = [(side or 'none') if visible else 'none' for side in self._icons()][:MAX_DESKTOPS]
        icons += ['none'] * (MAX_DESKTOPS - len(icons))
        d, h = self.drag, self.hover
        if d is not None and d['kind'] == 'roll' and d['side'] == 'level':
            hover, side, active = (-1, 'none', False) if d['moved'] else (d['index'], 'level', False)
        elif d is not None and d['kind'] == 'roll': hover, side, active = d['index'], d['side'], d['moved']
        elif d is None and self.mode == 'free' and h is not None and h[0] == 'roll': hover, side, active = h[1], h[2], False
        else: hover, side, active = -1, 'none', False
        if not visible: hover, side, active = -1, 'none', False
        rolls = [min(max(int(r), -MAX_ROLL), MAX_ROLL) for r in self.rolls[:MAX_DESKTOPS]] + [0] * (MAX_DESKTOPS - len(self.rolls[:MAX_DESKTOPS]))
        want = {'roll': rolls, 'icons': icons, 'hover': hover, 'side': side, 'active': active}
        if want == self.roll_sent: return b''
        self.roll_sent = want
        return self._send(roll_frame, want)

    def _held(self):
        """A button held on a window (unified_cursor: the non-consuming click binds); none here."""
        return False

    def _carry(self, dx, dy, now):
        """Carry mode's motion (unified_cursor.UnifiedPointer); no such mode here."""
        return b''

    def _text_allowed(self):
        """The beam's gate. The dot is the real cursor on a desktop's content (ZONES: an open panel over it or the sliver at
        its edge is not), the run holds nothing, and the handles are on: the overview, never maximized, in typing mode, during
        a video or with the key bind panel."""
        return self.mode == 'desktop' and self.drag is None and self._handles_on() and zone_of(self._hit()) == 'content'

    def _step_text(self, now):
        """This tick's beam (TextHold). Off at once when the gate shuts: a handle wins with no wait, and a word from before
        counts for nothing, so back in the overview the dot waits for a fresh read (shape_gated: no push comes for a cursor
        that did not change)."""
        with self._lock: text = self._shape_text
        if not text and not self.text_hold.on and not self.shape_gated: return
        if self._text_allowed():
            self.shape_gated = False
            self.text_hold.step(text, now)
            return
        self.shape_gated = True
        self.text_hold.clear()
        with self._lock: self._shape_text = False

    def _look(self):
        """The dot's look (POINTER_LOOKS; None: the plain dot), the zone contract on screen: what it holds, else what it is
        over in free mode. In desktop mode it is on a desktop's content, where it is the real cursor: the beam where the app
        asks for a text cursor (_step_text), else plain."""
        d = self.drag
        if d is not None:
            if d['kind'] == 'roll' and d['side'] == 'level' and d['moved']: return None  # dragged off the button
            return {'resize': RESIZE_LOOKS.get(d.get('handle')), 'pill': 'move', 'roll': 'rotate'}.get(d['kind'])
        if self.mode == 'free': return look_for(self.hover)
        return 'text' if self.mode == 'desktop' and self.text_hold.on else None

    def _pointer_frame(self, idle):
        """The dot: in desktop and free mode (the real cursor is hidden in both: overview_cursor.py), hidden when idle."""
        p = self.pointer
        if self.mode in DOT_MODES and p is not None and not idle and self.active():
            # Always drawn in its direction (linear, anywhere round you), at the whole canvas pixel the frame carries.
            px, py = (int(math.floor(c + 0.5)) for c in p)
            az, el = orbit.pointer_direction(self.geometry, (px, py))
            x, y = (min(max(c, -MAX_POINTER_PX), MAX_POINTER_PX) for c in (px, py))
            want = {'visible': True, 'x': x, 'y': y, 'held': self.drag is not None or self._held(),
                    'az': min(max(round(math.degrees(az) * 100), -MAX_POINTER_AZ), MAX_POINTER_AZ),
                    'el': min(max(round(math.degrees(el) * 100), -MAX_POINTER_EL), MAX_POINTER_EL)}
            drag = self._drag_display()
            if drag is not None: want['drag'] = drag
            look = self._look()
            if look is not None: want['look'] = look
        else:
            want = {k: v for k, v in self.sent.items() if k not in ('drag', 'look')}
            want.update(visible=False, held=False)
        if want == self.sent: return b''
        self.sent = want
        return self._send(overview_pointer_frame, want)


class PointerSampler:
    """Background cursor reads (250 Hz in the overview) and every compositor call this feature makes: the left-button
    binds, hiding / showing / parking the cursor and handing it back. stop() undoes them before returning."""
    def __init__(self, client, names, pointer, bind_args, period=SAMPLE_S, cursor_shape=None):
        self.client, self.names, self.pointer, self.bind_args, self.period = client, list(names), pointer, bind_args, period
        self.cursor_shape = cursor_shape  # () -> the requested cursor's name or None (ownership.cursor_reader); None: no reads
        pointer.shape_wired = cursor_shape is not None
        self._shape_due = 0.0     # no read before this, monotonic: the app's time to answer, or the wait after a failure
        self._shape_read = 0.0    # when the last read was
        self._shape_open = False  # the beam's gate was shut: the next read waits no SHAPE_CHECK_S, and it is the first after it
        self.shape_errors = 0
        self.names_changed = False
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self.applied = 0          # last want serial applied
        self.grabbed = False      # binds installed and cursor hidden (free mode); removal shows it again
        self.ever_grabbed = False
        self.park = None          # where the hidden cursor is kept
        self._last = None         # previous cursor sample (motion is measured from it)
        self._regrabbed_at = None
        self.errors = self.samples = self.recentres = 0
        self.cursor = OverviewCursor(pointer.control)  # circle only: the real cursor hidden while the dot is the pointer

    def start(self):
        self._thread.start()
        return self

    def set_names(self, names):
        """The run's desktops changed: read their monitors again at once (the loop thread picks this up)."""
        self.names = list(names)
        self.names_changed = True

    def stop(self, timeout=2.5):
        self._stop.set()
        self._thread.join(timeout)
        if self.ever_grabbed:  # best effort even if the thread is stuck; xr_control drops stale ones on the next click
            try: self.client.remove_resize_binds()  # also shows the cursor again and puts it back
            except (OSError, ValueError, TypeError, KeyError): pass
            self.grabbed = False
        self.cursor.stop(self.client)  # and the overview's hidden cursor (any order with the line above is safe)

    def _run(self):
        outputs, refreshed = [], None
        while not self._stop.is_set():
            if not self.pointer.overview():
                self._release()
                self.cursor.step(self.client, False)  # the normal cursor outside the overview
                self.applied = None  # re-apply the current want when the overview is back
                self._last = None
                self._stop.wait(IDLE_S)
                continue
            now = time.monotonic()
            try:
                if refreshed is None or self.names_changed or now - refreshed >= OUTPUTS_REFRESH:
                    self.names_changed = False
                    outputs = resize.outputs_from_monitors(self.client.query('monitors', timeout=0.5), list(self.names))
                    refreshed = now
            except (OSError, ValueError, TypeError, KeyError):
                self.errors += 1
            self._apply()
            self.cursor.step(self.client, self.pointer.mode in DOT_MODES)  # hidden while the dot is the pointer
            cursor, motion = self._sample()
            self.pointer.feed(cursor, motion, outputs, self.applied if self.applied is not None else -1)
            self._recentre(cursor)
            self._read_shape()
            self._stop.wait(self.period)

    def _read_shape(self):
        """The dot on a desktop's content: what cursor the app under it wants, every SHAPE_S (OverviewPointer.feed_shape), or
        every SHAPE_CHECK_S while the plugin pushes each change (push_shape) and the reads agree. Not until the sampler has
        applied the pointer's last warp and the app has had SHAPE_SETTLE_S to answer it. A failed read is the plain dot, and
        the next one waits SHAPE_RETRY_S."""
        read, p, now = self.cursor_shape, self.pointer, time.monotonic()
        if read is None: return
        if p.mode != 'desktop' or not p._handles_on() or self.applied is None or self.applied < p._sync:
            self._shape_due, self._shape_read = now + SHAPE_SETTLE_S, 0.0  # the read once it is over is due at once
            return
        gated = p.shape_gated
        self._shape_open = self._shape_open or gated  # a cursor that did not change sends no push, however long the gate shut
        every = SHAPE_CHECK_S if p.shape_pushed and not self._shape_open else SHAPE_S
        if now < self._shape_due or now - self._shape_read < every: return
        self._shape_read, self._shape_open = now, gated
        serial = p.shape_serial()
        try: name = read()
        except (OSError, ValueError, TypeError, KeyError):
            self.shape_errors += 1
            self._shape_due, name = now + SHAPE_RETRY_S, None
        p.feed_shape(name, serial)

    def _sample(self):
        try:
            pos = self.client.query('cursorpos', timeout=0.25)
            x, y = pos['x'], pos['y']
            if type(x) not in (int, float) or type(y) not in (int, float) or not math.isfinite(x) or not math.isfinite(y):
                raise ValueError('cursor position')
        except (OSError, ValueError, TypeError, KeyError):
            self.errors += 1
            return None, (0.0, 0.0)
        self.samples += 1
        cursor = (int(x), int(y))
        last, self._last = self._last, cursor
        if last is None: return cursor, (0.0, 0.0)
        dx, dy = cursor[0] - last[0], cursor[1] - last[1]
        if abs(dx) > SAMPLE_JUMP_PX or abs(dy) > SAMPLE_JUMP_PX: return cursor, (0.0, 0.0)
        return cursor, (float(dx), float(dy))

    def _recentre(self, cursor):
        """Free mode: the hidden cursor has strayed from its parking point; put it back (motion is measured from there)."""
        if not self.grabbed or cursor is None or self.park is None: return
        if math.hypot(cursor[0] - self.park[0], cursor[1] - self.park[1]) <= RECENTRE_PX: return
        try:
            self.client.warp_cursor(*self.park)
            self._last = self.park; self.recentres += 1
        except (OSError, ValueError, TypeError, KeyError, AttributeError): pass

    def _apply(self):
        serial, kind, point = self.pointer.want
        if self.pointer.binds_dropped:  # a config reload removed the binds and showed the cursor
            self.pointer.binds_dropped = False
            if self.grabbed:
                now = time.monotonic()
                if self._regrabbed_at is None or now - self._regrabbed_at >= 2.0:  # never a reload loop
                    self._regrabbed_at = now
                    self.grabbed = False
                    self.applied = None
        if serial == self.applied: return
        try:
            if kind == 'free':
                if point is None: return
                self.ever_grabbed = True  # set first: a failed or partial grab is still undone at stop
                self.client.grab_pointer(*self.bind_args, *point)
                self.grabbed, self.park, self._last = True, point, point
            elif kind == 'off':
                self._release()
            elif point is not None:
                self.client.release_pointer(*point)
                self.grabbed, self._last = False, point
            elif self.grabbed:
                self.client.release_pointer()
                self.grabbed = False
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            self.errors += 1
            return  # retried on the next sample
        self.applied = serial

    def _release(self):
        if not self.grabbed: return
        try:
            self.client.remove_resize_binds()  # binds gone, cursor shown and put back where free mode found it
            self.grabbed = False
        except (OSError, ValueError, TypeError, KeyError):
            self.errors += 1
