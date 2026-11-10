//! Glass pills: a Material Design 3 tonal capsule under each XR desktop in the overview, on that desktop's own surface
//! (it faces you with its desktop, and a curved desktop curves its pill). Dragging a pill moves its desktop; clicking it
//! (or Ctrl+Alt+L for the selected desktop) expands it, with a smooth morph, into that desktop's placement panel: the
//! four placement sliders, the selected one highlighted. Only one panel is open at a time. The supervisor owns the state
//! (ipc.hpp PlacementMenuPayload: open + desktop = the expanded pill) and the hit testing
//! (tools/native_spike/free_pointer.py, which mirrors every geometry constant here; a contract test compares them).
//! Pure and offline-testable, except the fragment shader source, which [`glass_shade`] mirrors on the CPU.
//!
//! Look: Material Design 3 (material.rs), in an M3 scheme seeded by the Omarchy theme colour (glass_theme.rs). The pill
//! is a flat tonal capsule in the primary container at elevation 1; hovered it is lit glass (a 2 px rim and a top sheen
//! in the light role, the container lifted toward it), dragged its rim brightens, the sheen goes and the container dims,
//! and it lifts (elevation 2, 4); the change eases over [`GLASS_HOVER_MS`]. The open panel is a surface container (high)
//! with an outline-variant edge at elevation 3, the state layer on its header strip only. On the glasses' micro-OLED
//! panels black is see-through: every role sits under [`CARD_MAX_LEVEL`] (191/255), the panel is dark behind its text,
//! and nothing moves on its own.
//!
//! Ported from `spike/include/xr/glass.hpp` and `spike/src/glass.cpp`, f32 maths in the same order, so the geometry is
//! bit for bit the C++ one. Not ported here (pixels, not geometry): the panel and handle atlas rasterisers
//! (render_glass_panel, render_options_panel, render_handle_atlas, glass_draw), their atlas cell boxes, and the font.
//! [`GlassPills`] keeps the content's state and render bookkeeping; the renderer draws [`GlassPills::rendered`].

// `!(x > 0.0)` is the C++'s own test and is true for NaN, where `x <= 0.0` is not: kept as written.
#![allow(clippy::neg_cmp_op_on_partial_ord)]

use std::f32::consts::PI;

use crate::desktops::{
    MAX_DESKTOPS, RESOLUTION_COUNT, RESOLUTION_GROUP_COUNT, RESOLUTION_GROUPS, RESOLUTION_ROWS, SCALE_STEPS,
};
use crate::glass_theme::{GLASS_THEME_DEFAULT, glass_theme_scheme};
use crate::hud::{
    BACKGROUND_COUNT, CardFade, MENU_FADE_IN_MS, MENU_FADE_OUT_MS, MenuPlacement, PLACEMENT_MENU_DESKTOP_COUNT,
    PLACEMENT_MENU_FIELDS, PlacementMenuState,
};
#[cfg(doc)]
use crate::hud::CARD_MAX_LEVEL;
use crate::material::{
    MD_DISABLED_CONTAINER, MD_DISABLED_CONTENT, MD_ELEVATION_DIALOG, MD_ELEVATION_DRAGGED, MD_ELEVATION_HOVER,
    MD_ELEVATION_PILL, MD_STATE_DRAGGED, MD_STATE_HOVER, MdRgb, md_elevation,
};
use crate::view::{BORDER_PX, FRect, Placement, cclamp, cmax, cmin};

/// The pill <-> panel morph.
pub const GLASS_EXPAND_MS: f64 = MENU_FADE_IN_MS;
pub const GLASS_COLLAPSE_MS: f64 = MENU_FADE_OUT_MS;

// --- Geometry: canvas px on the desktop's surface, relative to its overview tile ------------------------------------
// Collapsed pill: a share of the desktop's width (clamped), centred, GLASS_PILL_GAP below its bottom edge as laid out,
// which is GLASS_PILL_GAP - BORDER_PX below the selected desktop's border. That gap to the border is the one a resize
// keeps (view.rs glass_to_desktop).
pub const GLASS_PILL_SHARE: f32 = 0.24;
pub const GLASS_PILL_MIN_W: f32 = 64.0;
pub const GLASS_PILL_MAX_W: f32 = 160.0;
pub const GLASS_PILL_H: f32 = 14.0;
pub const GLASS_PILL_GAP: f32 = 10.0;
const _: () = assert!(GLASS_PILL_GAP > BORDER_PX, "the pill hangs clear of the selected desktop's border");

// Expanded panel: PANEL_W x PANEL_H, centred under the desktop, its top where the pill's top is.
pub const PANEL_W: f32 = 480.0;
pub const PANEL_H: f32 = 394.0;
pub const PANEL_RADIUS: f32 = 18.0;

// Panel layout (panel px from its top-left). The header strip is the pill's own: dragging it moves the desktop and
// clicking it collapses the panel. Row i (field i, ipc.hpp WirePlacement order) spans
// [PANEL_ROWS_TOP + i * PANEL_ROW_H, + PANEL_ROW_H); its slider's knob centre runs PANEL_KNOB_LO..PANEL_KNOB_HI.
pub const PANEL_HEADER_H: f32 = 42.0;
pub const PANEL_ROWS_TOP: f32 = 46.0;
pub const PANEL_ROW_H: f32 = 34.0;
pub const PANEL_PAD: f32 = 22.0;
pub const PANEL_TRACK_X0: f32 = 184.0;
pub const PANEL_TRACK_X1: f32 = 374.0;
pub const PANEL_KNOB_R: f32 = 7.0;
pub const PANEL_KNOB_LO: f32 = PANEL_TRACK_X0 + PANEL_KNOB_R;
pub const PANEL_KNOB_HI: f32 = PANEL_TRACK_X1 - PANEL_KNOB_R;

// Under the sliders, the Resolution row (one PANEL_ROW_H row at PANEL_RESOLUTION_TOP): the label "Resolution" and an M3
// exposed dropdown menu (glass_resolution_box, the Scale box's twin) showing the desktop's pixel size ("1920x1080") and
// a drop-down arrow. A click on the box opens its list, an M3 menu RES_LIST_GAP above the box, drawn over the slider
// rows and as wide as their selected band (glass_resolution_list): one column per desktops.rs RESOLUTION_GROUPS shape
// (16:9, 16:10, Ultrawide), its name on top (glass_resolution_heading) and its RESOLUTIONS sizes under it
// (glass_resolution_item); the current size on the secondary container, every size M3 disabled while busy. Picking one
// resizes that desktop's output live (tools/native_spike/desktops.py RESOLUTION); free_pointer.py mirrors the geometry
// (glass_contract compares).
pub const PANEL_RESOLUTION_TOP: f32 = PANEL_ROWS_TOP + PLACEMENT_MENU_FIELDS as f32 * PANEL_ROW_H; // 182
pub const RES_BOX_X: f32 = PANEL_TRACK_X0;
pub const RES_BOX_H: f32 = 26.0;
pub const RES_BOX_W: f32 = PANEL_W - PANEL_PAD - RES_BOX_X; // 274 wide
pub const RES_BOX_Y: f32 = PANEL_RESOLUTION_TOP + (PANEL_ROW_H - RES_BOX_H) / 2.0; // 186
pub const RES_ITEM_H: f32 = 23.0;
pub const RES_LIST_GAP: f32 = 2.0;
pub const RES_LIST_X: f32 = 10.0;
pub const RES_LIST_W: f32 = PANEL_W - 2.0 * RES_LIST_X; // 460 wide: a column of 153 px per shape
/// The headings, then the largest group.
pub const RES_LIST_H: f32 = (RESOLUTION_ROWS + 1) as f32 * RES_ITEM_H;
pub const RES_LIST_Y: f32 = RES_BOX_Y - RES_LIST_GAP - RES_LIST_H; // 46
// The open list covers the four slider rows exactly (no row shows half hidden above it), under the header.
const _: () = assert!(RES_LIST_Y == PANEL_ROWS_TOP && RES_LIST_Y >= PANEL_HEADER_H, "the open list covers the slider rows");

// Under that, the Scale row (one PANEL_ROW_H row at PANEL_SCALE_TOP): the label "Scale" and an M3 exposed dropdown menu
// (glass_scale_box: a filled text field) showing the desktop's Hyprland output scale ("125%") and a drop-down arrow. A
// click on the box opens its list, an M3 menu: one item per desktops.rs SCALE_PERCENTS step (100..300%), top down
// (glass_scale_item), SCALE_LIST_GAP above the box, drawn over the Resolution row and the slider rows; the current step
// on the secondary container, steps that do not divide the desktop's resolution (or any while busy) M3 disabled.
pub const PANEL_SCALE_TOP: f32 = PANEL_RESOLUTION_TOP + PANEL_ROW_H; // 216
pub const SCALE_BOX_X: f32 = PANEL_TRACK_X0;
pub const SCALE_BOX_H: f32 = RES_BOX_H;
pub const SCALE_BOX_W: f32 = PANEL_W - PANEL_PAD - SCALE_BOX_X; // 274 wide
pub const SCALE_BOX_Y: f32 = PANEL_SCALE_TOP + (PANEL_ROW_H - SCALE_BOX_H) / 2.0; // 220
/// Seven steps fit between the header and the box.
pub const SCALE_ITEM_H: f32 = 20.0;
pub const SCALE_LIST_GAP: f32 = 2.0;
const _: () = assert!(
    SCALE_BOX_Y - SCALE_LIST_GAP - SCALE_STEPS as f32 * SCALE_ITEM_H >= PANEL_HEADER_H,
    "the open list stays under the header"
);

// Under that, the background's two rows, the same in every desktop's panel. The Background row (at
// PANEL_BACKGROUND_TOP): the label, an M3 exposed dropdown (glass_background_box, the Scale box's look) showing the
// background, and an M3 switch (glass_background_switch) that turns it off and back to the last one used, as Ctrl+Alt+E
// does. A click on the box opens its list, one item per BACKGROUNDS row (glass_background_item), BACKGROUND_LIST_GAP
// above the box, drawn over the rows above it. The Brightness row (at PANEL_BRIGHTNESS_TOP): a slider like the
// placement ones, on their knob range, filled from 100%, BRIGHTNESS_MIN..BRIGHTNESS_MAX, M3 disabled with no
// background. free_pointer.py mirrors the geometry (glass_contract compares).
pub const PANEL_BACKGROUND_TOP: f32 = PANEL_SCALE_TOP + PANEL_ROW_H; // 250
pub const BACKGROUND_SWITCH_W: f32 = 46.0;
pub const BACKGROUND_SWITCH_H: f32 = 26.0;
pub const BACKGROUND_BOX_GAP: f32 = 12.0;
pub const BACKGROUND_SWITCH_X: f32 = PANEL_W - PANEL_PAD - BACKGROUND_SWITCH_W; // 412
pub const BACKGROUND_BOX_X: f32 = PANEL_TRACK_X0;
pub const BACKGROUND_BOX_H: f32 = SCALE_BOX_H;
pub const BACKGROUND_BOX_W: f32 = BACKGROUND_SWITCH_X - BACKGROUND_BOX_GAP - BACKGROUND_BOX_X; // 216 wide
pub const BACKGROUND_BOX_Y: f32 = PANEL_BACKGROUND_TOP + (PANEL_ROW_H - BACKGROUND_BOX_H) / 2.0; // 254
pub const BACKGROUND_ITEM_H: f32 = SCALE_ITEM_H;
pub const BACKGROUND_LIST_GAP: f32 = SCALE_LIST_GAP;
pub const PANEL_BRIGHTNESS_TOP: f32 = PANEL_BACKGROUND_TOP + PANEL_ROW_H; // 284
const _: () = assert!(
    BACKGROUND_BOX_Y - BACKGROUND_LIST_GAP - BACKGROUND_COUNT as f32 * BACKGROUND_ITEM_H >= PANEL_HEADER_H,
    "the open list stays under the header"
);

// Under that, the desktop row: PANEL_ACTIONS buttons side by side, button i spanning
// [PANEL_PAD + i * (PANEL_ACTION_W + PANEL_ACTION_GAP), + PANEL_ACTION_W) x [PANEL_ACTIONS_TOP, + PANEL_ACTION_H)
// (glass_panel_action): Ultrawide (toggles the desktop between 16:9 and ultrawide), Add (a new desktop), Remove (this
// desktop; asks for a second click). tools/native_spike/desktops.py does the work; free_pointer.py mirrors the geometry.
pub const PANEL_ACTIONS: usize = 3;
pub const PANEL_ACTIONS_TOP: f32 = PANEL_BRIGHTNESS_TOP + PANEL_ROW_H + 6.0; // 324
pub const PANEL_ACTION_H: f32 = 32.0;
pub const PANEL_ACTION_W: f32 = 138.0;
pub const PANEL_ACTION_GAP: f32 = 11.0;
const _: () = assert!(
    PANEL_PAD * 2.0 + PANEL_ACTION_W * PANEL_ACTIONS as f32 + PANEL_ACTION_GAP * (PANEL_ACTIONS - 1) as f32 == PANEL_W,
    "the desktop row spans the panel between its paddings"
);
pub const PANEL_FOOTER_TOP: f32 = PANEL_ACTIONS_TOP + PANEL_ACTION_H + 8.0; // 364
/// Em sizes with the system font.
pub const PANEL_TEXT_PX: u32 = 24;
pub const PANEL_HINT_PX: u32 = 18;
pub const PANEL_ACTION_PX: u32 = 20;

/// options_geometry.hpp kRestoreStepMs (a float there, promoted to double where [`RestoreStep`] uses it).
const RESTORE_STEP_MS: f64 = 220.0;

// A small grabber capsule at the top centre of an open panel (where the pill was).
pub const GRABBER_W: f32 = 44.0;
pub const GRABBER_H: f32 = 5.0;
pub const GRABBER_TOP: f32 = 8.0;
const _: () = assert!(PANEL_FOOTER_TOP + PANEL_HINT_PX as f32 + 6.0 <= PANEL_H, "footer fits the panel");
const _: () = assert!(PANEL_H == PANEL_FOOTER_TOP + 30.0, "the panel ends 30 px below its footer's top (free_pointer.py PANEL_H)");

// Level bar (tools/native_spike/free_pointer.py level_bar_scale / level_bar_rect / level_bar_segment / LEVEL_*, compared
// by the glass_contract test): in the overview every desktop has one segmented capsule centred above its top edge, the
// pill's counterpart: turn left, level, turn right. Its left and right segments turn the desktop counter-clockwise and
// clockwise: a click a step (or to level, when level lies that way), a drag either way. Its middle segment levels it.
// It is tile px on the desktop's own surface, unlike the glass, so it moves, scales and rolls with its desktop: a resize
// (its distance) or a move (its orbit) carries it along and it stays the same share of the desktop. Drawn LEVEL_BAR_W x
// LEVEL_BAR_H, LEVEL_BAR_GAP above the edge on a desktop at least LEVEL_BAR_W / LEVEL_BAR_SHARE wide; on a narrower one
// the whole design scales down (level_bar_scale) to LEVEL_BAR_SHARE of its width, not under LEVEL_BAR_MIN_W, and on one
// narrower than that to its width.
pub const LEVEL_BAR_W: f32 = 148.0;
pub const LEVEL_BAR_H: f32 = 26.0;
pub const LEVEL_BAR_GAP: f32 = 12.0;
pub const LEVEL_BAR_PAD: f32 = 4.0;
pub const LEVEL_BAR_SHARE: f32 = 0.25;
pub const LEVEL_BAR_MIN_W: f32 = 120.0;
/// The hit-zone contract round a desktop (free_pointer.py LEVEL_EDGE_CLEAR, hit), from the inside out: its content (its
/// apps); a band LEVEL_EDGE_CLEAR wide just outside every edge (the selected desktop's border) for the edge's resize
/// zone, and the corners' resize zones round each corner; then the bar; then empty space.
pub const LEVEL_EDGE_CLEAR: f32 = 6.0;
const _: () = assert!(LEVEL_BAR_GAP - LEVEL_BAR_PAD >= LEVEL_EDGE_CLEAR, "the bar's hit region stays out of the band along the edge");
const _: () = assert!(LEVEL_BAR_GAP >= BORDER_PX, "the bar stays clear of the selected desktop's border");

/// The bar's segments, left to right: what `side` is on the wire (ipc.hpp RollPayload) and [`GlassFrame::bar_segment`].
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
#[repr(i8)]
pub enum LevelSegment {
    #[default]
    None = -1,
    Ccw = 0,
    Level = 1,
    Cw = 2,
}
pub const LEVEL_SEGMENTS: usize = 3;

// Corner caps: a quiet rounded L wrapped round each corner of every desktop in the overview, drawn with the bar and
// scaled with it, the resize corners made visible. CAP_GAP outside the edges, legs CAP_LEG long and CAP_STROKE wide;
// the corner under the dot or held lights up, legs CAP_LEG_HOT long and CAP_STROKE_HOT wide. CAP_OUTER past the corner
// to CAP_INNER in from it holds every state, the box each is drawn in (corner_cap_rect). Pressing one is the corner's
// resize zone (free_pointer.py border_handle); the glass_contract test checks that the zone covers the idle cap.
pub const CAP_GAP: f32 = 8.5;
pub const CAP_LEG: f32 = 12.0;
pub const CAP_LEG_HOT: f32 = 22.0;
pub const CAP_STROKE: f32 = 2.4;
pub const CAP_STROKE_HOT: f32 = 3.2;
pub const CAP_BEND: f32 = 5.0;
pub const CAP_OUTER: f32 = 11.0;
pub const CAP_INNER: f32 = 16.0;
const _: () = assert!(CAP_GAP - CAP_STROKE_HOT / 2.0 >= LEVEL_EDGE_CLEAR, "a cap stays clear of the selected desktop's 6 px border");
const _: () = assert!(
    CAP_GAP + CAP_STROKE_HOT / 2.0 <= CAP_OUTER && CAP_LEG_HOT - CAP_GAP + CAP_STROKE_HOT / 2.0 <= CAP_INNER,
    "every state fits the box"
);

// The 3D button, Connected Tail (tools/native_spike/immersive.py; free_pointer.py immersive_main_rect /
// immersive_tail_rect / immersive_part / IMMERSIVE_*, compared by the glass_contract test): two controls joined to the
// bar in M3's connected-button style. Tile px, scaled like the bar, on its line and as tall: IMMERSIVE_GAP right of the
// bar the "3D" segment, this desktop's toggle, IMMERSIVE_GAP right of that the tail, three small desktops, every desktop
// at once. Corners IMMERSIVE_INNER_R but the tail's outer end, which is the capsule's. Shown with the bar, under the
// same rules (never takes a press from content, a corner or a pill). A desktop too narrow to hold both IMMERSIVE_CLEAR
// short of its top-right corner has neither: the rects are empty.
pub const IMMERSIVE_GAP: f32 = 2.0;
pub const IMMERSIVE_MAIN_W: f32 = 40.0;
pub const IMMERSIVE_TAIL_W: f32 = 28.0;
pub const IMMERSIVE_INNER_R: f32 = 4.0;
pub const IMMERSIVE_CLEAR: f32 = CAP_INNER;
/// The 3D's em size, tile px.
pub const IMMERSIVE_TEXT_PX: f32 = 14.5;
// The tail's desktops.
pub const IMMERSIVE_TICK_W: f32 = 4.2;
pub const IMMERSIVE_TICK_H: f32 = 9.0;
pub const IMMERSIVE_TICK_GAP: f32 = 1.8;
pub const IMMERSIVE_TICK_R: f32 = 1.1;

/// Which control is at a tile point. Above, below and past the tail's end the zone is the drawn shape padded by
/// LEVEL_BAR_PAD * level_bar_scale, like the bar. The gaps are too narrow for padding, so each is split down its middle:
/// the bar's padding gives way to the button there (free_pointer.hit asks the button first) and a press goes to what it
/// is next to.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
#[repr(u8)]
pub enum ImmersivePart {
    #[default]
    None = 0,
    Main = 1,
    Tail = 2,
}

// The handle atlas (render_handle_atlas, not ported here): HANDLE_ATLAS_SCALE px per tile px, cells
// HANDLE_ATLAS_GUTTER px apart so that bilinear filtering stays in its cell.
pub const HANDLE_ATLAS_SCALE: u32 = 3;
pub const HANDLE_ATLAS_GUTTER: u32 = 2;

// --- Look -------------------------------------------------------------------------------------------------------------
// Pill states (GlassFrame::pill_levels). Hover = the pointer is on the pill (or the open panel's header); active = it is
// being dragged (or pressed). A level eases between them over GLASS_HOVER_MS (GlassLevels), so every look in between is
// drawn too: by_state interpolates linearly from idle to hover to active.
pub const GLASS_IDLE: f32 = 1.0;
pub const GLASS_HOVER: f32 = 1.4;
pub const GLASS_ACTIVE: f32 = 1.7;
/// The ease from one state to the next.
pub const GLASS_HOVER_MS: f64 = 140.0;
/// = CARD_MAX_LEVEL: the brightest any glass pixel may be.
pub const GLASS_CAP: f32 = 191.0 / 255.0;
/// Canvas px drawn around the shape for its shadow.
pub const GLASS_SHADOW_MARGIN: f32 = 32.0;
/// The open panel's opacity (the pill is opaque): a hint of what is behind it.
pub const PANEL_FILL: f32 = 0.94;
// Lit glass (the hover look of every pill-family control: pills, the gear, Maximize, Restore, and the focused or
// selected row of a panel). Hover lights the glass from above: a GLASS_RIM_PX rim of the light colour just inside the
// edge (GLASS_RIM_HOVER), a sheen of it over the top (GLASS_SHEEN_HOVER at the top, fading out by GLASS_SHEEN_END of
// the height) and the container lifted GLASS_LIFT toward it. Pressed: the rim stronger (GLASS_RIM_ACTIVE), no sheen, and
// the container GLASS_PRESS_DIM darker, pushed in. No line is thinner than 2 px, nothing glows past the shape.
pub const GLASS_RIM_PX: f32 = 2.0;
pub const GLASS_RIM_HOVER: f32 = 0.55;
pub const GLASS_RIM_ACTIVE: f32 = 0.70;
pub const GLASS_SHEEN_HOVER: f32 = 0.34;
pub const GLASS_SHEEN_END: f32 = 0.6;
pub const GLASS_LIFT: f32 = 0.10;
pub const GLASS_PRESS_DIM: f32 = 0.12;

/// One glass shape in M3 terms: a container colour at `fill` opacity, a `rim_px` outline just inside its edge, a state
/// layer (the content colour at `state_alpha`) over its top `state_px`, fading out over `state_soft` px centred there
/// (1: a hard edge), and an elevation shadow below it.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct GlassLook {
    pub container: [f32; 3],
    pub outline: [f32; 3],
    pub state: [f32; 3],
    pub fill: f32,
    pub outline_alpha: f32,
    pub state_alpha: f32,
    pub state_px: f32,
    pub shadow: f32,
    pub shadow_sigma: f32,
    pub shadow_dy: f32,
    pub rim_px: f32,
    pub state_soft: f32,
}

impl Default for GlassLook {
    fn default() -> Self {
        Self {
            container: [0.0; 3],
            outline: [0.0; 3],
            state: [0.0; 3],
            fill: 0.0,
            outline_alpha: 0.0,
            state_alpha: 0.0,
            state_px: 0.0,
            shadow: 0.0,
            shadow_sigma: 1.0,
            shadow_dy: 0.0,
            rim_px: 1.0,
            state_soft: 1.0,
        }
    }
}

/// The shader's `style` uniform is this many vec4s (glass_style).
pub const GLASS_STYLE_VECS: usize = 5;

/// The panel's content fades in over the last quarter of the expansion.
pub const CONTENT_FROM: f32 = 0.75;

// --- Helpers (glass.cpp's own, not view.rs's: this lerp is exact at both ends) ------------------------------------------

/// Exactly a at 0, b at 1.
fn lerp(a: f32, b: f32, t: f32) -> f32 {
    a * (1.0 - t) + b * t
}

/// `std::isnan(v) ? 0 : std::clamp(v, 0, 1)`.
fn clamp01(v: f32) -> f32 {
    if v.is_nan() { 0.0 } else { cclamp(v, 0.0, 1.0) }
}

/// GLSL smoothstep.
fn smooth(e0: f32, e1: f32, x: f32) -> f32 {
    let t = clamp01((x - e0) / (e1 - e0));
    t * t * (3.0 - 2.0 * t)
}

/// A value per pill state (GLASS_IDLE / GLASS_HOVER / GLASS_ACTIVE), linear in between, held outside.
fn by_state(level: f32, idle: f32, hover: f32, active: f32) -> f32 {
    if !(level > GLASS_IDLE) {
        return idle;
    }
    if level >= GLASS_ACTIVE {
        return active;
    }
    if level <= GLASS_HOVER {
        return lerp(idle, hover, (level - GLASS_IDLE) / (GLASS_HOVER - GLASS_IDLE));
    }
    lerp(hover, active, (level - GLASS_HOVER) / (GLASS_ACTIVE - GLASS_HOVER))
}

// --- Geometry ---------------------------------------------------------------------------------------------------------

/// The collapsed pill under `t`. Empty for an empty tile.
pub fn glass_pill(t: &FRect) -> FRect {
    if t.width <= 0.0 || t.height <= 0.0 {
        return FRect::default();
    }
    let w = cmin(cclamp(GLASS_PILL_SHARE * t.width, GLASS_PILL_MIN_W, GLASS_PILL_MAX_W), t.width);
    FRect::new(t.x + (t.width - w) / 2.0, t.y + t.height + GLASS_PILL_GAP, w, GLASS_PILL_H)
}

/// The expanded panel under `t`. Empty for an empty tile.
pub fn glass_panel(t: &FRect) -> FRect {
    if t.width <= 0.0 || t.height <= 0.0 {
        return FRect::default();
    }
    FRect::new(t.x + (t.width - PANEL_W) / 2.0, t.y + t.height + GLASS_PILL_GAP, PANEL_W, PANEL_H)
}

/// The shape `k` (eased 0..1) of the way from pill to panel.
pub fn glass_morph(t: &FRect, k: f32) -> FRect {
    let k = clamp01(k);
    let a = glass_pill(t);
    let b = glass_panel(t);
    let w = lerp(a.width, b.width, k);
    let h = lerp(a.height, b.height, k);
    FRect::new(t.x + (t.width - w) / 2.0, a.y, w, h) // grows from the pill's top centre
}

/// The morph's corner radius (the pill is fully rounded).
pub fn glass_radius(t: &FRect, k: f32) -> f32 {
    let m = glass_morph(t, k);
    cmin(lerp(GLASS_PILL_H / 2.0, PANEL_RADIUS, clamp01(k)), cmin(m.width, m.height) / 2.0)
}

/// What the bar and the corner caps are scaled by: 1, less on a narrow desktop. 0 for an empty tile.
pub fn level_bar_scale(t: &FRect) -> f32 {
    if !(t.width > 0.0) || !(t.height > 0.0) {
        return 0.0;
    }
    // std::min({a, b, c}): the first smallest.
    cmin(cmin(cmax(LEVEL_BAR_SHARE * t.width, LEVEL_BAR_MIN_W), LEVEL_BAR_W), t.width) / LEVEL_BAR_W
}

/// The bar as drawn: tile px on the desktop's surface, centred above its top edge. Empty for an empty tile.
pub fn level_bar_rect(t: &FRect) -> FRect {
    let s = level_bar_scale(t);
    let w = LEVEL_BAR_W * s;
    let h = LEVEL_BAR_H * s;
    if !(s > 0.0) {
        return FRect::default();
    }
    FRect::new(t.x + (t.width - w) / 2.0, t.y - LEVEL_BAR_GAP * s - h, w, h)
}

/// The segment at tile point (px, py): the drawn bar padded by LEVEL_BAR_PAD * level_bar_scale, split at its thirds
/// (the padding belongs to the segment it borders). None outside it. Only where nothing else is: any desktop's content,
/// a corner's resize zone and a pill all win over it (free_pointer.py hit), and the renderer draws the bar under them.
pub fn level_bar_segment(t: &FRect, px: f32, py: f32) -> LevelSegment {
    let r = level_bar_rect(t);
    let pad = LEVEL_BAR_PAD * level_bar_scale(t);
    if !(r.width > 0.0) || px < r.x - pad || px > r.x + r.width + pad || py < r.y - pad || py > r.y + r.height + pad {
        return LevelSegment::None;
    }
    let third = r.width / LEVEL_SEGMENTS as f32;
    if px < r.x + third {
        return LevelSegment::Ccw;
    }
    if px < r.x + 2.0 * third { LevelSegment::Level } else { LevelSegment::Cw }
}

/// Corner `corner`'s cap box (0 top-left, 1 top-right, 2 bottom-left, 3 bottom-right), tile px. Empty for an empty tile
/// or corner > 3.
pub fn corner_cap_rect(t: &FRect, corner: u32) -> FRect {
    let s = level_bar_scale(t);
    if !(s > 0.0) || corner > 3 {
        return FRect::default();
    }
    let right = corner == 1 || corner == 3;
    let bottom = corner >= 2;
    let size = (CAP_OUTER + CAP_INNER) * s;
    FRect::new(
        if right { t.x + t.width - CAP_INNER * s } else { t.x - CAP_OUTER * s },
        if bottom { t.y + t.height - CAP_INNER * s } else { t.y - CAP_OUTER * s },
        size,
        size,
    )
}

/// Both controls' rects, or None on a desktop too narrow for them.
fn immersive_rects(t: &FRect) -> Option<(FRect, FRect)> {
    let s = level_bar_scale(t);
    if !(s > 0.0) {
        return None;
    }
    let bar = level_bar_rect(t);
    let main = FRect::new(bar.x + bar.width + IMMERSIVE_GAP * s, bar.y, IMMERSIVE_MAIN_W * s, bar.height);
    let tail = FRect::new(main.x + main.width + IMMERSIVE_GAP * s, bar.y, IMMERSIVE_TAIL_W * s, bar.height);
    (tail.x + tail.width <= t.x + t.width - IMMERSIVE_CLEAR * s).then_some((main, tail))
}

/// The 3D button's "3D" segment, tile px. Empty on a desktop too narrow for the button.
pub fn immersive_main_rect(t: &FRect) -> FRect {
    immersive_rects(t).map_or_else(FRect::default, |(main, _)| main)
}

/// The 3D button's tail, tile px. Empty on a desktop too narrow for the button.
pub fn immersive_tail_rect(t: &FRect) -> FRect {
    immersive_rects(t).map_or_else(FRect::default, |(_, tail)| tail)
}

/// Which 3D control is at tile point (px, py) ([`ImmersivePart`]).
pub fn immersive_part(t: &FRect, px: f32, py: f32) -> ImmersivePart {
    let Some((main, tail)) = immersive_rects(t) else {
        return ImmersivePart::None;
    };
    let s = level_bar_scale(t);
    let pad = LEVEL_BAR_PAD * s;
    let half = IMMERSIVE_GAP * s / 2.0;
    if py < main.y - pad || py > main.y + main.height + pad || px < main.x - half || px > tail.x + tail.width + pad {
        return ImmersivePart::None;
    }
    if px < tail.x - half { ImmersivePart::Main } else { ImmersivePart::Tail }
}

/// Desktop-row button `action`, panel px. Empty for action >= PANEL_ACTIONS.
pub fn glass_panel_action(action: usize) -> FRect {
    if action >= PANEL_ACTIONS {
        return FRect::default();
    }
    FRect::new(
        PANEL_PAD + action as f32 * (PANEL_ACTION_W + PANEL_ACTION_GAP),
        PANEL_ACTIONS_TOP,
        PANEL_ACTION_W,
        PANEL_ACTION_H,
    )
}

/// Button labels as drawn (the pixel font is ASCII; so are these). Remove reads "Remove?" while armed.
pub fn glass_panel_action_label(action: usize, armed: bool) -> &'static str {
    match action {
        0 => "Ultrawide",
        1 => "Add new",
        2 if armed => "Remove?",
        2 => "Remove",
        _ => "",
    }
}

/// Whether a button does anything now: nothing while busy; Add below MAX_DESKTOPS desktops, Remove above one.
pub fn glass_panel_action_enabled(action: usize, state: &PlacementMenuState) -> bool {
    if state.busy || action >= PANEL_ACTIONS {
        return false;
    }
    match action {
        1 => (state.count as usize) < PLACEMENT_MENU_DESKTOP_COUNT,
        2 => state.count > 1,
        _ => true,
    }
}

/// The Scale dropdown's box, panel px.
pub fn glass_scale_box() -> FRect {
    FRect::new(SCALE_BOX_X, SCALE_BOX_Y, SCALE_BOX_W, SCALE_BOX_H)
}

/// Scale list item `step`, panel px. Empty for step >= SCALE_STEPS.
pub fn glass_scale_item(step: usize) -> FRect {
    if step >= SCALE_STEPS {
        return FRect::default();
    }
    let top = SCALE_BOX_Y - SCALE_LIST_GAP - SCALE_STEPS as f32 * SCALE_ITEM_H;
    FRect::new(SCALE_BOX_X, top + step as f32 * SCALE_ITEM_H, SCALE_BOX_W, SCALE_ITEM_H)
}

/// Whether Scale step `step` can be picked now: it divides the desktop's resolution (state.scales), nothing is busy.
pub fn glass_scale_enabled(step: usize, state: &PlacementMenuState) -> bool {
    !state.busy && step < SCALE_STEPS && (state.scales >> step) & 1 != 0
}

/// Text of a scale in percent as drawn: "125%"; "--" for 0 (unknown).
pub fn glass_scale_label(percent: u16) -> String {
    if percent != 0 { format!("{percent}%") } else { "--".to_owned() }
}

/// The Background dropdown's box, panel px.
pub fn glass_background_box() -> FRect {
    FRect::new(BACKGROUND_BOX_X, BACKGROUND_BOX_Y, BACKGROUND_BOX_W, BACKGROUND_BOX_H)
}

/// The Background on/off switch, panel px.
pub fn glass_background_switch() -> FRect {
    FRect::new(BACKGROUND_SWITCH_X, BACKGROUND_BOX_Y, BACKGROUND_SWITCH_W, BACKGROUND_SWITCH_H)
}

/// Background list item `item`, panel px. Empty for item >= BACKGROUND_COUNT.
pub fn glass_background_item(item: usize) -> FRect {
    if item >= BACKGROUND_COUNT {
        return FRect::default();
    }
    let top = BACKGROUND_BOX_Y - BACKGROUND_LIST_GAP - BACKGROUND_COUNT as f32 * BACKGROUND_ITEM_H;
    FRect::new(BACKGROUND_BOX_X, top + item as f32 * BACKGROUND_ITEM_H, BACKGROUND_BOX_W, BACKGROUND_ITEM_H)
}

/// The Resolution dropdown's box, panel px.
pub fn glass_resolution_box() -> FRect {
    FRect::new(RES_BOX_X, RES_BOX_Y, RES_BOX_W, RES_BOX_H)
}

/// The open Resolution list's whole area, panel px.
pub fn glass_resolution_list() -> FRect {
    FRect::new(RES_LIST_X, RES_LIST_Y, RES_LIST_W, RES_LIST_H)
}

/// Resolution list column `group`'s heading, panel px. Empty for group >= RESOLUTION_GROUP_COUNT.
pub fn glass_resolution_heading(group: usize) -> FRect {
    if group >= RESOLUTION_GROUP_COUNT {
        return FRect::default();
    }
    let w = RES_LIST_W / RESOLUTION_GROUP_COUNT as f32;
    FRect::new(RES_LIST_X + group as f32 * w, RES_LIST_Y, w, RES_ITEM_H)
}

/// Resolution list item `i` (RESOLUTIONS order), panel px. Empty for i >= RESOLUTION_COUNT.
pub fn glass_resolution_item(i: usize) -> FRect {
    if i >= RESOLUTION_COUNT {
        return FRect::default();
    }
    let mut group = 0;
    let mut row = i;
    while row >= RESOLUTION_GROUPS[group].count {
        row -= RESOLUTION_GROUPS[group].count;
        group += 1;
    }
    let column = glass_resolution_heading(group);
    FRect::new(column.x, column.y + (1 + row) as f32 * RES_ITEM_H, column.width, RES_ITEM_H)
}

/// Whether size `i` can be picked now: nothing is busy (every listed size is one the desktop can take).
pub fn glass_resolution_enabled(i: usize, state: &PlacementMenuState) -> bool {
    !state.busy && i < RESOLUTION_COUNT
}

/// Text of a size as drawn: "1920x1080"; "--" for 0 x 0 (unknown).
pub fn glass_resolution_label(width: u16, height: u16) -> String {
    if width != 0 && height != 0 { format!("{width}x{height}") } else { "--".to_owned() }
}

// --- Look -------------------------------------------------------------------------------------------------------------

/// M3's state layer opacity for the open panel's header: 0 idle, MD_STATE_HOVER at GLASS_HOVER, MD_STATE_DRAGGED at
/// GLASS_ACTIVE.
pub fn glass_state_opacity(level: f32) -> f32 {
    by_state(level, 0.0, MD_STATE_HOVER, MD_STATE_DRAGGED)
}

/// Lit glass by state: the rim's opacity (0, GLASS_RIM_HOVER, GLASS_RIM_ACTIVE).
pub fn glass_rim_opacity(level: f32) -> f32 {
    by_state(level, 0.0, GLASS_RIM_HOVER, GLASS_RIM_ACTIVE)
}

/// Lit glass by state: the sheen's opacity (0, GLASS_SHEEN_HOVER, 0).
pub fn glass_sheen_opacity(level: f32) -> f32 {
    by_state(level, 0.0, GLASS_SHEEN_HOVER, 0.0)
}

/// Lit glass for a control `height` px tall: `container` (opaque) lifted, lit and pressed in `light` by `level`, its rim
/// `rim_px` wide (an atlas drawn at k px per canvas px passes k * GLASS_RIM_PX; the C++ default is GLASS_RIM_PX). No
/// shadow.
pub fn glass_lit_look(container: [f32; 3], light: [f32; 3], level: f32, height: f32, rim_px: f32) -> GlassLook {
    let mut k = GlassLook::default();
    for i in 0..3 {
        // lifted toward the light while hovered, pushed in (darker) while pressed
        k.container[i] = by_state(
            level,
            container[i],
            lerp(container[i], light[i], GLASS_LIFT),
            container[i] * (1.0 - GLASS_PRESS_DIM),
        );
    }
    k.fill = 1.0;
    k.outline = light;
    k.outline_alpha = glass_rim_opacity(level);
    k.rim_px = rim_px;
    k.state = light;
    k.state_alpha = glass_sheen_opacity(level);
    k.state_px = GLASS_SHEEN_END * height / 2.0; // full at the top, gone by GLASS_SHEEN_END
    k.state_soft = GLASS_SHEEN_END * height;
    k
}

/// The look `t` (eased 0..1) of the way from pill to panel, `accent` the Omarchy theme colour (glass_theme_scheme): the
/// primary container as lit glass in the primary bright (glass_lit_look), then the surface container (high) with a 1 px
/// outline-variant edge and the on-surface state layer over its header strip (PANEL_HEADER_H), the elevation rising
/// from the pill's (1, or 2 hovered / 4 dragged) to the panel's (3, or 4 while its header is dragged).
pub fn glass_look(accent: MdRgb, level: f32, t: f32) -> GlassLook {
    let t = clamp01(t);
    let m = glass_theme_scheme(accent);
    let lit = glass_lit_look(m.primary_container, m.primary_bright, level, GLASS_PILL_H, GLASS_RIM_PX);
    let mut k = GlassLook::default();
    for i in 0..3 {
        k.container[i] = lerp(lit.container[i], m.surface_container_high[i], t); // lit glass, then a dialog surface
        k.state[i] = lerp(lit.state[i], m.on_surface[i], t); // the sheen, then the header's state layer
        k.outline[i] = lerp(lit.outline[i], m.outline_variant[i], t); // the rim, then the panel's outlined edge
    }
    k.outline_alpha = lerp(lit.outline_alpha, 1.0, t);
    k.rim_px = lerp(lit.rim_px, 1.0, t);
    k.fill = lerp(1.0, PANEL_FILL, t);
    k.state_alpha = lerp(lit.state_alpha, glass_state_opacity(level), t);
    k.state_px = lerp(lit.state_px, PANEL_HEADER_H, t); // the pill's sheen, the open panel's header strip
    k.state_soft = lerp(lit.state_soft, 1.0, t);
    let pill = by_state(level, MD_ELEVATION_PILL, MD_ELEVATION_HOVER, MD_ELEVATION_DRAGGED);
    let panel = by_state(level, MD_ELEVATION_DIALOG, MD_ELEVATION_DIALOG, MD_ELEVATION_DRAGGED);
    let shadow = md_elevation(lerp(pill, panel, t));
    k.shadow = shadow.alpha;
    k.shadow_sigma = shadow.sigma;
    k.shadow_dy = shadow.dy;
    k
}

/// The open panel's drag handle (M3: on-surface-variant at 40%), more opaque while hovered or dragged. No shadow.
pub fn grabber_look(level: f32) -> GlassLook {
    GlassLook {
        container: glass_theme_scheme(GLASS_THEME_DEFAULT).on_surface_variant, // a neutral grey: it reads on any theme
        fill: by_state(level, 0.40, 0.55, 0.70),
        ..GlassLook::default()
    }
}

/// One pixel (canvas px centre px, py) of a glass shape `rect` with corner `radius` at `opacity`: premultiplied RGBA,
/// every colour channel at most GLASS_CAP. The GLSL fragment shader ([`glass_fragment_shader`]) computes exactly this.
pub fn glass_shade(k: &GlassLook, rect: &FRect, radius: f32, px: f32, py: f32, opacity: f32) -> [f32; 4] {
    let cx = rect.x + rect.width / 2.0;
    let cy = rect.y + rect.height / 2.0;
    let hw = rect.width / 2.0;
    let hh = rect.height / 2.0;
    let rounded_box = |qx: f32, qy: f32| {
        let dx = qx.abs() - (hw - radius);
        let dy = qy.abs() - (hh - radius);
        let ox = cmax(dx, 0.0);
        let oy = cmax(dy, 0.0);
        (ox * ox + oy * oy).sqrt() + cmin(cmax(dx, dy), 0.0) - radius
    };
    let d = rounded_box(px - cx, py - cy);
    let inside = clamp01(0.5 - d);
    let top = py - (cy - hh);
    let st = k.state_alpha * clamp01((k.state_px - top) / cmax(k.state_soft, 1.0) + 0.5); // over the top state_px
    let mut a = k.fill;
    let mut c = [0.0f32; 3];
    for ((v, &container), &state) in c.iter_mut().zip(&k.container).zip(&k.state) {
        *v = (container * (1.0 - st) + state * st) * a;
    }
    let b = cmin(clamp01(0.5 - d), clamp01(d + k.rim_px + 0.5)) * k.outline_alpha; // rim_px just inside the edge
    for (v, &outline) in c.iter_mut().zip(&k.outline) {
        *v = *v * (1.0 - b) + outline * b;
    }
    a = a + b * (1.0 - a);
    for v in &mut c {
        *v *= inside;
    }
    a *= inside;
    let ds = cmax(rounded_box(px - cx, py - cy - k.shadow_dy), 0.0);
    let sigma = cmax(k.shadow_sigma, 1e-3);
    let s = k.shadow * (-ds * ds / (2.0 * sigma * sigma)).exp() * (1.0 - inside);
    a = a + s * (1.0 - a);
    [
        cmin(c[0], GLASS_CAP) * opacity,
        cmin(c[1], GLASS_CAP) * opacity,
        cmin(c[2], GLASS_CAP) * opacity,
        a * opacity,
    ]
}

/// The shader's `style` uniform, five vec4s: container + fill, outline + its alpha, state layer + its alpha,
/// (state_px, shadow, shadow_sigma, shadow_dy), (rim_px, state_soft, 0, 0). One glUniform4fv(style, GLASS_STYLE_VECS,
/// ...) sets every style input.
pub fn glass_style(k: &GlassLook) -> [f32; 4 * GLASS_STYLE_VECS] {
    [
        k.container[0], k.container[1], k.container[2], k.fill,
        k.outline[0], k.outline[1], k.outline[2], k.outline_alpha,
        k.state[0], k.state[1], k.state[2], k.state_alpha,
        k.state_px, k.shadow, k.shadow_sigma, k.shadow_dy,
        k.rim_px, k.state_soft, 0.0, 0.0,
    ]
}

/// GLSL ES 1.00 fragment shader. Varying `p` = canvas px; uniforms rect (centre x, y, half w, half h), radius,
/// style[GLASS_STYLE_VECS] (glass_style), opacity. Premultiplied output: blend with GL_ONE, GL_ONE_MINUS_SRC_ALPHA.
/// Mirrors glass_shade() line for line.
pub fn glass_fragment_shader() -> &'static str {
    concat!(
        "#ifdef GL_FRAGMENT_PRECISION_HIGH\nprecision highp float;\n#else\nprecision mediump float;\n#endif\n",
        "varying vec2 p; uniform vec4 rect; uniform float radius; uniform vec4 style[5]; uniform float opacity;\n",
        "const float kCap = 0.74901961;\n",
        "float box(vec2 q) { vec2 d = abs(q) - (rect.zw - vec2(radius));\n",
        "  return length(max(d, 0.0)) + min(max(d.x, d.y), 0.0) - radius; }\n",
        "void main() {\n",
        "  float d = box(p - rect.xy);\n",
        "  float inside = clamp(0.5 - d, 0.0, 1.0);\n",
        "  float top = p.y - (rect.y - rect.w);\n",
        "  float st = style[2].a * clamp((style[3].x - top) / max(style[4].y, 1.0) + 0.5, 0.0, 1.0);\n",
        "  float a = style[0].a;\n",
        "  vec3 c = (style[0].rgb * (1.0 - st) + style[2].rgb * st) * a;\n",
        "  float b = min(clamp(0.5 - d, 0.0, 1.0), clamp(d + style[4].x + 0.5, 0.0, 1.0)) * style[1].a;\n",
        "  c = c * (1.0 - b) + style[1].rgb * b; a = a + b * (1.0 - a);\n",
        "  c *= inside; a *= inside;\n",
        "  float ds = max(box(p - rect.xy - vec2(0.0, style[3].w)), 0.0);\n",
        "  float sigma = max(style[3].z, 0.001);\n",
        "  float s = style[3].y * exp(-ds * ds / (2.0 * sigma * sigma)) * (1.0 - inside);\n",
        "  a = a + s * (1.0 - a);\n",
        "  gl_FragColor = vec4(min(c, vec3(kCap)) * opacity, a * opacity);\n",
        "}\n",
    )
}

/// Footer hint as drawn (font: arrows; pixel font: ASCII).
pub fn glass_panel_hint(ascii: bool) -> &'static str {
    if ascii { "U/D FIELD  L/R ADJUST  TAB  R RESET" } else { "\u{2191}\u{2193} field  \u{2190}\u{2192} adjust  Tab next  R reset" }
}

/// A desktop-row button's M3 colours in a scheme.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct PanelButtonStyle {
    pub container: [f32; 3],
    pub content: [f32; 3],
    pub container_alpha: f32,
    pub content_alpha: f32,
}

/// A desktop-row button's M3 colours in `accent`'s scheme (the C++ default is GLASS_THEME_DEFAULT): filled tonal
/// (secondary container), the primary while on (Ultrawide), the error container while armed (Remove?), M3 disabled when
/// it does nothing now (on-surface at 12% / 38%).
pub fn glass_panel_button_style(action: usize, state: &PlacementMenuState, accent: MdRgb) -> PanelButtonStyle {
    let m = glass_theme_scheme(accent);
    let style = |container, content, container_alpha, content_alpha| PanelButtonStyle {
        container,
        content,
        container_alpha,
        content_alpha,
    };
    if !glass_panel_action_enabled(action, state) {
        return style(m.on_surface, m.on_surface, MD_DISABLED_CONTAINER, MD_DISABLED_CONTENT);
    }
    if action == 2 && state.armed {
        return style(m.error_container, m.on_error_container, 1.0, 1.0); // asks before it removes
    }
    if action == 0 && state.wide {
        return style(m.primary, m.on_primary, 1.0, 1.0); // a toggle that is on
    }
    style(m.secondary_container, m.on_secondary_container, 1.0, 1.0) // filled tonal
}

// --- Animation --------------------------------------------------------------------------------------------------------

/// The Step-Back Band's ease (options_geometry.hpp), on the renderer's monotonic clock: from wherever it is to open or
/// shut.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct RestoreStep {
    from: f32,
    to: f32,
    start_ms: f64,
}

impl Default for RestoreStep {
    fn default() -> Self {
        Self { from: 0.0, to: 0.0, start_ms: -1e9 }
    }
}

impl RestoreStep {
    pub fn set(&mut self, open: bool, now_ms: f64) {
        let to = if open { 1.0 } else { 0.0 };
        if to == self.to {
            return;
        }
        self.from = self.value(now_ms);
        self.to = to;
        self.start_ms = now_ms;
    }

    pub fn value(&self, now_ms: f64) -> f32 {
        let t = clamp01(((now_ms - self.start_ms) / RESTORE_STEP_MS) as f32);
        self.from + (self.to - self.from) * t * t * (3.0 - 2.0 * t)
    }

    pub fn animating(&self, now_ms: f64) -> bool {
        now_ms - self.start_ms < RESTORE_STEP_MS
    }
}

/// What the compositor draws this frame (GlassPills::frame and the Wayland loop fill it; overview only). The C++ image
/// pointers are left out: the content is [`GlassPills::rendered`], redrawn when [`GlassFrame::content_dirty`].
#[derive(Clone, Debug, PartialEq)]
pub struct GlassFrame {
    /// The options panel opens from its gear the way a placement pill opens into its panel: the gear's glass grows into
    /// the panel (rect, radius, colour, elevation), the grabber fades in, then the content. `options_expand` is the
    /// eased CardFade progress (0: the gear, 1: the panel), `options_desktop` the desktop it opens on. The gear under the
    /// dot takes the pill's hover levels (`gear_levels`, eased like `pill_levels`).
    pub options: bool,
    pub options_expand: f32,
    pub options_desktop: i32,
    pub options_generation: u64,
    pub gear_levels: [f32; PLACEMENT_MENU_DESKTOP_COUNT],
    /// The Step-Back Band's Restore pill on the focused desktop, 0: not drawn.
    pub restore_alpha: f32,
    /// Collapsed pills under every desktop.
    pub pills: bool,
    /// Each desktop's pill state (GLASS_IDLE .. GLASS_ACTIVE), eased (GlassLevels): hovered or dragged, or on its way.
    pub pill_levels: [f32; PLACEMENT_MENU_DESKTOP_COUNT],
    /// Eased expansion per desktop.
    pub expand: [f32; PLACEMENT_MENU_DESKTOP_COUNT],
    pub content: i32,
    pub generation: u64,
    pub content_alpha: f32,
    /// The content shown needs drawing again (the C++ image() renders lazily here): draw [`GlassPills::rendered`].
    pub content_dirty: bool,
    /// Drawn last, over every other pill: the open panel's desktop (or, while collapsing, the most expanded one). The
    /// supervisor's hit testing treats the open panel as the top layer (free_pointer.hit), so this is what keeps the two
    /// in step where a moved desktop's pill overlaps it. -1: none.
    pub top: i32,
    /// The open panel's drawing surface while the supervisor holds one of its sliders: the desktop's placement at the
    /// press (the desktop itself follows its live placement). The supervisor measures the drag on that same surface, so
    /// the knob stays under the pointer. None: the panel rides on its desktop's live placement.
    pub surface_desktop: i32,
    pub surface: Option<Placement>,
    /// The level bars and corner caps (drawn with the pills: `pills` true) of the desktops in `bars`. Only one thing
    /// lights at a time, what the dot is on or holds: the segment `bar_segment` of desktop `bar_hover`, the cap on
    /// corner `cap_corner` of desktop `cap_hover`; `bar_active` / `cap_active` while dragged. They are drawn before every
    /// desktop (any desktop's content covers them, the glass goes over them), on their desktop's live placement, never on
    /// the held panel's `surface`.
    pub bars: [bool; PLACEMENT_MENU_DESKTOP_COUNT],
    pub bar_hover: i32,
    pub bar_segment: LevelSegment,
    pub bar_active: bool,
    pub cap_hover: i32,
    pub cap_corner: u32,
    pub cap_active: bool,
    /// The 3D buttons, next to the bars and only with them: the desktops that are 3D, whether every desktop is (the
    /// tails light), and the one under the dot (-1: none), on its tail or its segment.
    pub immersive: [bool; PLACEMENT_MENU_DESKTOP_COUNT],
    pub immersive_global: bool,
    pub immersive_tail: bool,
    pub immersive_hover: i32,
}

impl Default for GlassFrame {
    fn default() -> Self {
        Self {
            options: false,
            options_expand: 0.0,
            options_desktop: -1,
            options_generation: 0,
            gear_levels: [GLASS_IDLE; PLACEMENT_MENU_DESKTOP_COUNT],
            restore_alpha: 0.0,
            pills: false,
            pill_levels: [GLASS_IDLE; PLACEMENT_MENU_DESKTOP_COUNT],
            expand: [0.0; PLACEMENT_MENU_DESKTOP_COUNT],
            content: -1,
            generation: 0,
            content_alpha: 0.0,
            content_dirty: false,
            top: -1,
            surface_desktop: -1,
            surface: None,
            bars: [false; PLACEMENT_MENU_DESKTOP_COUNT],
            bar_hover: -1,
            bar_segment: LevelSegment::None,
            bar_active: false,
            cap_hover: -1,
            cap_corner: 0,
            cap_active: false,
            immersive: [false; PLACEMENT_MENU_DESKTOP_COUNT],
            immersive_global: false,
            immersive_tail: false,
            immersive_hover: -1,
        }
    }
}

/// Renderer placement from wire units (ipc.hpp WirePlacement: x / y the orbit round you in tenths of a degree, distance
/// in percent, curvature in degrees) to radians and a distance factor: what set_placements applies. Roll stays 0.
pub fn placement_from_wire(w: &MenuPlacement) -> Placement {
    const RAD: f32 = PI / 180.0; // x / y (the orbit) are in tenths of a degree
    Placement {
        x: w[0] as f32 * RAD / 10.0,
        y: w[1] as f32 * RAD / 10.0,
        distance: w[2] as f32 / 100.0,
        curvature: w[3] as f32 * RAD,
        roll: 0.0,
    }
}

/// The glass draw order: `back_to_front` (view.rs draw_order) with desktop `top` moved last (drawn over the others).
pub fn glass_draw_order(mut order: Vec<usize>, top: i32) -> Vec<usize> {
    if let Ok(top) = usize::try_from(top)
        && let Some(i) = order.iter().position(|&d| d == top)
    {
        order[i..].rotate_left(1);
    }
    order
}

#[derive(Clone, Copy, Debug, PartialEq)]
struct Ease {
    from: f32,
    to: f32,
    since_ms: f64,
}

impl Default for Ease {
    fn default() -> Self {
        Self { from: GLASS_IDLE, to: GLASS_IDLE, since_ms: -1e9 }
    }
}

/// Pill states eased per desktop: set() a target state, level() is where it is now, GLASS_HOVER_MS from the old level to
/// the new one (ease-out cubic), so the pill the pointer leaves dims while the next one lights.
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct GlassLevels {
    eases: [Ease; PLACEMENT_MENU_DESKTOP_COUNT],
}

impl GlassLevels {
    pub fn set(&mut self, i: usize, target: f32, now_ms: f64) {
        if i >= self.eases.len() || self.eases[i].to == target {
            return;
        }
        self.eases[i] = Ease { from: self.level(i, now_ms), to: target, since_ms: now_ms };
    }

    pub fn level(&self, i: usize, now_ms: f64) -> f32 {
        let Some(e) = self.eases.get(i) else {
            return GLASS_IDLE;
        };
        let u = clamp01(((now_ms - e.since_ms) / GLASS_HOVER_MS) as f32);
        let eased = 1.0 - (1.0 - u) * (1.0 - u) * (1.0 - u);
        lerp(e.from, e.to, eased)
    }

    pub fn animating(&self, now_ms: f64) -> bool {
        self.eases.iter().any(|e| e.from != e.to && now_ms - e.since_ms < GLASS_HOVER_MS)
    }
}

const _: () = assert!(PLACEMENT_MENU_DESKTOP_COUNT == MAX_DESKTOPS, "one fade per desktop");

/// The placement panels' state: which pill is expanded, the content shown, and its render bookkeeping. The C++ class
/// also owns the rendered image and the font; here the renderer draws [`GlassPills::rendered`] in [`GlassPills::accent`]
/// whenever [`GlassPills::ensure`] says to.
#[derive(Clone, Debug)]
pub struct GlassPills {
    fades: [CardFade; PLACEMENT_MENU_DESKTOP_COUNT],
    rendered: PlacementMenuState,
    /// Only while open.
    surface: Option<MenuPlacement>,
    /// The last frame drawn outside the overview (-1: the last one was in it).
    hidden_ms: f64,
    generation: u64,
    opened: u64,
    closed: u64,
    renders: u64,
    dirty: bool,
    accent: MdRgb,
}

impl Default for GlassPills {
    fn default() -> Self {
        Self {
            fades: [CardFade::new(GLASS_EXPAND_MS, GLASS_COLLAPSE_MS); PLACEMENT_MENU_DESKTOP_COUNT],
            rendered: PlacementMenuState::default(),
            surface: None,
            hidden_ms: -1.0,
            generation: 0,
            opened: 0,
            closed: 0,
            renders: 0,
            dirty: false,
            accent: GLASS_THEME_DEFAULT,
        }
    }
}

impl GlassPills {
    /// Apply the supervisor's state: the open desktop's pill expands (any other collapses), and the content is marked
    /// for a re-render (lazily, on the next [`GlassPills::ensure`]) when anything it shows changed. `surface`: the
    /// placement the open panel is drawn on while a slider is held (GlassFrame::surface). True if anything to draw
    /// changed.
    pub fn set(&mut self, state: &PlacementMenuState, now_ms: f64, surface: Option<MenuPlacement>) -> bool {
        let mut changed = false;
        if state.open && (self.generation == 0 || *state != self.rendered) {
            self.rendered = state.clone();
            self.dirty = true;
            self.generation += 1;
            changed = true;
        } else if !state.open && self.rendered.open {
            self.rendered.open = false; // keep the last content for the collapse
            changed = true;
        }
        let held = if state.open { surface } else { None };
        if held != self.surface {
            self.surface = held;
            changed = true;
        }
        for (d, fade) in self.fades.iter_mut().enumerate() {
            let want = state.open && state.desktop as usize == d;
            if fade.set(want, now_ms) {
                if want {
                    self.opened += 1;
                } else {
                    self.closed += 1;
                }
                changed = true;
            }
        }
        changed
    }

    /// The font changed (C++ set_font): existing content is drawn again, under the same generation.
    pub fn font_changed(&mut self) {
        self.dirty = self.generation != 0;
    }

    /// The theme colour (Presentation::set_accent) of the panel's highlights. A change re-renders existing content
    /// (lazily, under a new generation) and returns true; the same colour, or no content yet, returns false.
    pub fn set_accent(&mut self, accent: MdRgb) -> bool {
        if accent == self.accent {
            return false;
        }
        self.accent = accent;
        if self.generation == 0 {
            return false; // nothing rendered yet: the first render uses it
        }
        self.dirty = true; // a new generation: the compositor uploads the re-rendered content
        self.generation += 1;
        true
    }

    /// C++ ensure() / image(): true if the content must be drawn now (it is counted as rendered).
    pub fn ensure(&mut self) -> bool {
        if !self.dirty {
            return false;
        }
        self.dirty = false;
        self.renders += 1;
        true
    }

    /// This frame's expansion, content and draw order. Pills and panels are drawn in the overview only: while
    /// `overview` is false (zoomed in or still zooming out) an expanding pill is held where it is (CardFade::hold), so a
    /// panel opened from a zoomed-in view (Ctrl+Alt+L zooms out first) still morphs from the pill once it can be seen.
    /// Everything is 0 / -1 while not in the overview. Leaves pills / hover for the caller.
    pub fn frame(&mut self, overview: bool, now_ms: f64) -> GlassFrame {
        let mut out = GlassFrame::default();
        if !overview {
            // Unseen: an expanding pill keeps its whole morph for when the overview is reached (a collapse just runs
            // out).
            self.hold_hidden(now_ms);
            self.hidden_ms = now_ms;
            return out;
        }
        self.hold_hidden(now_ms);
        self.hidden_ms = -1.0;
        let mut most = 0.0f32;
        for i in 0..out.expand.len() {
            out.expand[i] = self.expand(i, now_ms);
            if out.expand[i] > most {
                most = out.expand[i];
                out.top = i as i32;
            }
        }
        if self.rendered.open {
            out.top = self.rendered.desktop as i32;
        }
        out.content_alpha = self.content_alpha(now_ms);
        if out.content_alpha > 0.0 {
            out.content = self.content_desktop();
            out.content_dirty = self.ensure();
            out.generation = self.generation;
        }
        if self.rendered.open
            && let Some(surface) = &self.surface
        {
            out.surface_desktop = self.rendered.desktop as i32;
            out.surface = Some(placement_from_wire(surface));
        }
        out
    }

    fn hold_hidden(&mut self, now_ms: f64) {
        if self.hidden_ms >= 0.0 {
            for f in &mut self.fades {
                if f.visible() {
                    f.hold(self.hidden_ms, now_ms);
                }
            }
        }
    }

    /// Eased 0..1.
    pub fn expand(&self, desktop: usize, now_ms: f64) -> f32 {
        self.fades.get(desktop).map_or(0.0, |f| f.alpha(now_ms))
    }

    pub fn animating(&self, now_ms: f64) -> bool {
        self.fades.iter().any(|f| f.animating(now_ms))
    }

    pub fn any_expanded(&self, now_ms: f64) -> bool {
        self.fades.iter().any(|f| f.level(now_ms) > 0.0)
    }

    pub fn content_desktop(&self) -> i32 {
        if self.generation != 0 { self.rendered.desktop as i32 } else { -1 }
    }

    /// Of content_desktop(), 0 until CONTENT_FROM.
    pub fn content_alpha(&self, now_ms: f64) -> f32 {
        let d = self.content_desktop();
        if d < 0 {
            return 0.0;
        }
        smooth(CONTENT_FROM, 1.0, self.expand(d as usize, now_ms))
    }

    /// The content to draw: the last open state (kept for the collapse).
    pub fn rendered(&self) -> &PlacementMenuState {
        &self.rendered
    }

    pub fn accent(&self) -> MdRgb {
        self.accent
    }

    pub fn generation(&self) -> u64 {
        self.generation
    }

    pub fn opened(&self) -> u64 {
        self.opened
    }

    pub fn closed(&self) -> u64 {
        self.closed
    }

    pub fn renders(&self) -> u64 {
        self.renders
    }

    pub fn open(&self) -> bool {
        self.rendered.open
    }

    pub fn surface(&self) -> Option<&MenuPlacement> {
        self.surface.as_ref()
    }
}
