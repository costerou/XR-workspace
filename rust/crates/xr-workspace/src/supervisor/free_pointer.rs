//! The overview pointer's module-level maths: a port of `tools/native_spike/free_pointer.py` lines 1-1055 (its
//! constants, the status-line parsers, the hit zones round each desktop, the projection between the canvas and a
//! placed desktop's surface, the frames it sends and the Lua it evals). `OverviewPointer` and `PointerSampler` are the
//! sampler's ([`super::sampler`]); the private helpers are public here, without the underscore.
//!
//! The Python's numbers keep the types its inputs give them (`inside_by` of an int tile is an int); the Rust is f64
//! throughout, and `pointer_math_corpus_tests` rebuilds the Python's types from the inputs. Placements are
//! [`orbit::Pose`]s (geometry.PLACEMENT_BOUNDS units, roll 0 where the dict has none); a hit is a [`Hit`].

pub use crate::lua::free_pointer::{
    HIDE_LUA, SHOW_LUA, grab_pointer_lua, release_pointer_lua,
};

use std::f64::consts::PI;

use super::environment::{BACKGROUNDS, BRIGHTNESS_FIELD};
use super::options_geometry;
use super::orbit::{self, Geometry, Pose, Tile, Vec3};
use super::python::{Invalid, Result, py_hypot, py_max, py_min, py_round, py_sum};
use super::view::{self, SCALE_PERCENTS};
use crate::geometry::{MAX_DESKTOPS, PLACEMENT_BOUNDS, PLACEMENT_KEYS, RESOLUTION_GROUPS, RESOLUTIONS};
use crate::pyjson::Json;
use crate::resize::{CORNERS, OutputRect};

/// `SAMPLE_S`: one cursor sample per this long in the overview.
pub const SAMPLE_S: f64 = 1.0 / 250.0;
/// `IDLE_S`: the poll outside the overview.
pub const IDLE_S: f64 = 0.1;
/// `OUTPUTS_REFRESH`: the XR monitors are read again this often.
pub const OUTPUTS_REFRESH: f64 = 2.0;
/// `EDGE_PX`: desktop mode, the cursor this close to its monitor's edge (logical px) has hit it.
pub const EDGE_PX: f64 = 0.5;
/// `ENTER_PX`: free mode, this far inside a desktop (tile px) is content.
pub const ENTER_PX: f64 = 0.5;
/// `EXIT_PX`: desktop mode, leaving at an edge puts the dot this far past it (logical px).
pub const EXIT_PX: f64 = 2.0;
/// `HELP_CLEAR_PX`: free mode over the help card, the dot enters a desktop only this far clear of the card.
pub const HELP_CLEAR_PX: f64 = 4.0;
/// `INSET_PX`: a cursor handed back lands at least this far inside its monitor (logical px).
pub const INSET_PX: f64 = 2.0;
/// `RECENTRE_PX`: free mode puts a hidden cursor this far from its parking point back.
pub const RECENTRE_PX: f64 = 200.0;
/// `JUMP_PX`: a cursor step this long is a warp or focus change, not a hand movement.
pub const JUMP_PX: f64 = 400.0;
/// `SAMPLE_JUMP_PX`: a single sample's motion beyond this is dropped.
pub const SAMPLE_JUMP_PX: i64 = 1500;
/// `CORNER_SHARE, CORNER_MIN, CORNER_MAX`: a corner's grab square (corner_zone).
pub const CORNER_SHARE: f64 = 0.06;
pub const CORNER_MIN: f64 = 10.0;
pub const CORNER_MAX: f64 = 28.0;
/// `EDGE_OUT_PX`: the resize band outside each edge (tile px).
pub const EDGE_OUT_PX: f64 = 10.0;
/// `EDGES`: the resize edges.
pub const EDGES: [&str; 4] = ["t", "b", "l", "r"];
/// `ZONES`: the zone contract's names (zone_of).
pub const ZONES: [&str; 6] = ["control", "rotate", "resize", "move", "content", "space"];
/// `POINTER_LOOKS`: the dot's looks (the `overview_pointer` frame's `look`).
pub const POINTER_LOOKS: [&str; 7] = ["resize_ew", "resize_ns", "resize_nwse", "resize_nesw", "move", "rotate", "text"];
/// `TEXT_SHAPES`: the names of a text cursor (is_text_shape).
pub const TEXT_SHAPES: [&str; 4] = ["text", "vertical-text", "xterm", "ibeam"];
/// `TEXT_BOUNCE_S, TEXT_SWEEP_S`: the beam's hold over a gap between lines (TextHold).
pub const TEXT_BOUNCE_S: f64 = 0.06;
pub const TEXT_SWEEP_S: f64 = 0.4;
/// `SHAPE_S, SHAPE_CHECK_S, SHAPE_SETTLE_S, SHAPE_RETRY_S`: the cursor shape reads, every SHAPE_S, every
/// SHAPE_CHECK_S while the plugin pushes, SHAPE_SETTLE_S after a warp, SHAPE_RETRY_S after a failure.
pub const SHAPE_S: f64 = 1.0 / 120.0;
pub const SHAPE_CHECK_S: f64 = 0.25;
pub const SHAPE_SETTLE_S: f64 = 1.0 / 30.0;
pub const SHAPE_RETRY_S: f64 = 1.0;
/// `RESIZE_LOOKS`: a resize handle's look.
pub const RESIZE_LOOKS: [(&str, &str); 8] = [
    ("l", "resize_ew"),
    ("r", "resize_ew"),
    ("t", "resize_ns"),
    ("b", "resize_ns"),
    ("tl", "resize_nwse"),
    ("br", "resize_nwse"),
    ("tr", "resize_nesw"),
    ("bl", "resize_nesw"),
];
/// `PILL_SHARE, PILL_MIN, PILL_MAX, PILL_THICKNESS, PILL_GAP`: the glass pill (glass.hpp glass_pill).
pub const PILL_SHARE: f64 = 0.24;
pub const PILL_MIN: f64 = 64.0;
pub const PILL_MAX: f64 = 160.0;
pub const PILL_THICKNESS: f64 = 14.0;
pub const PILL_GAP: f64 = 10.0;
/// `PILL_PAD_X, PILL_PAD_Y`: the padding round the pill that still grabs it.
pub const PILL_PAD_X: f64 = 10.0;
pub const PILL_PAD_Y: f64 = 10.0;
/// `BORDER_PX`: view.hpp kBorderPx, the selected desktop's frame, tile px.
pub const BORDER_PX: f64 = 6.0;
/// `PANEL_W, PANEL_H, PANEL_HEADER_H`: the placement panel (glass.hpp glass_panel).
pub const PANEL_W: f64 = 480.0;
pub const PANEL_H: f64 = 394.0;
pub const PANEL_HEADER_H: f64 = 42.0;
/// `PANEL_ROWS_TOP, PANEL_ROW_H, PANEL_KNOB_LO, PANEL_KNOB_HI`: the slider rows and their knob range.
pub const PANEL_ROWS_TOP: f64 = 46.0;
pub const PANEL_ROW_H: f64 = 34.0;
pub const PANEL_KNOB_LO: f64 = 191.0;
pub const PANEL_KNOB_HI: f64 = 367.0;
/// `PANEL_RES_TOP`: the Resolution row, under the sliders (182).
pub const PANEL_RES_TOP: f64 = PANEL_ROWS_TOP + PLACEMENT_KEYS.len() as f64 * PANEL_ROW_H;
/// `PANEL_RES_BOX`: its dropdown box, (x, y, w, h) panel px.
pub const PANEL_RES_BOX: [f64; 4] = [184.0, 186.0, 274.0, 26.0];
/// `PANEL_RES_ITEM_H, PANEL_RES_LIST_GAP, PANEL_RES_LIST_X, PANEL_RES_LIST_W`: its open list.
pub const PANEL_RES_ITEM_H: f64 = 23.0;
pub const PANEL_RES_LIST_GAP: f64 = 2.0;
pub const PANEL_RES_LIST_X: f64 = 10.0;
pub const PANEL_RES_LIST_W: f64 = 460.0;
/// `PANEL_SCALE_TOP`: the Scale row (216).
pub const PANEL_SCALE_TOP: f64 = PANEL_RES_TOP + PANEL_ROW_H;
/// `PANEL_SCALE_BOX`: its dropdown box.
pub const PANEL_SCALE_BOX: [f64; 4] = [184.0, 220.0, 274.0, 26.0];
/// `PANEL_SCALE_ITEM_H, PANEL_SCALE_LIST_GAP`: its open list.
pub const PANEL_SCALE_ITEM_H: f64 = 20.0;
pub const PANEL_SCALE_LIST_GAP: f64 = 2.0;
/// `PANEL_BACKGROUND_TOP`: the background's row (250).
pub const PANEL_BACKGROUND_TOP: f64 = PANEL_SCALE_TOP + PANEL_ROW_H;
/// `PANEL_BACKGROUND_BOX, PANEL_BACKGROUND_SWITCH`: its dropdown box and the switch beside it.
pub const PANEL_BACKGROUND_BOX: [f64; 4] = [184.0, 254.0, 216.0, 26.0];
pub const PANEL_BACKGROUND_SWITCH: [f64; 4] = [412.0, 254.0, 46.0, 26.0];
/// `PANEL_BACKGROUND_ITEM_H, PANEL_BACKGROUND_LIST_GAP`: its open list.
pub const PANEL_BACKGROUND_ITEM_H: f64 = 20.0;
pub const PANEL_BACKGROUND_LIST_GAP: f64 = 2.0;
/// `PANEL_BRIGHTNESS_TOP`: the Brightness slider's row (284).
pub const PANEL_BRIGHTNESS_TOP: f64 = PANEL_BACKGROUND_TOP + PANEL_ROW_H;
/// `PANEL_PAD`: the panel's left padding.
pub const PANEL_PAD: f64 = 22.0;
/// `PANEL_ACTIONS_TOP, PANEL_ACTION_H, PANEL_ACTION_W, PANEL_ACTION_GAP`: the desktop row's buttons.
pub const PANEL_ACTIONS_TOP: f64 = 324.0;
pub const PANEL_ACTION_H: f64 = 32.0;
pub const PANEL_ACTION_W: f64 = 138.0;
pub const PANEL_ACTION_GAP: f64 = 11.0;
/// `PANEL_KINDS`: what a press finds on the open panel's own desktop that belongs to the panel.
pub const PANEL_KINDS: [&str; 13] = [
    "pill",
    "panel",
    "slider",
    "corner",
    "edge",
    "action",
    "scale",
    "scale_box",
    "resolution",
    "resolution_box",
    "background",
    "background_box",
    "background_switch",
];
/// `PANEL_ACTIONS`: the desktop row's buttons, left to right.
pub const PANEL_ACTIONS: [&str; 3] = ["ultrawide", "add", "remove"];
/// `PANEL_SLOP_PX`: a press this far past either end of a slider's knob range still grabs it.
pub const PANEL_SLOP_PX: f64 = 12.0;
/// `CLICK_PX`: a pill press moving less than this (canvas px) before release is a click.
pub const CLICK_PX: f64 = 4.0;
/// `HOLD_S, WINDOW_DRAG_PX`: a press on a desktop held this long, or moved this far first, lifts its window.
pub const HOLD_S: f64 = 0.35;
pub const WINDOW_DRAG_PX: f64 = 12.0;
/// `GHOST_SCALE, GHOST_MIN_PX, GHOST_MAX_PX, GHOST_DEFAULT, MAX_GHOST_PX`: a dragged window's ghost (ghost_size).
pub const GHOST_SCALE: f64 = 0.3;
pub const GHOST_MIN_PX: f64 = 24.0;
pub const GHOST_MAX_PX: f64 = 160.0;
pub const GHOST_DEFAULT: (i64, i64) = (72, 45);
pub const MAX_GHOST_PX: i64 = 512;
/// `MIN_SCALE`: a corner drag cannot shrink the apparent size past this.
pub const MIN_SCALE: f64 = 0.05;
/// `RESIZE_OVERSHOOT_GAIN`: past the edge of the view a corner drag speeds up by this much (resize_reach).
pub const RESIZE_OVERSHOOT_GAIN: f64 = 8.0;
/// `RESIZE_DRAGS, MONITOR_DRAGS`: the drags that resize, and every drag of a monitor.
pub const RESIZE_DRAGS: [&str; 1] = ["resize"];
pub const MONITOR_DRAGS: [&str; 4] = ["resize", "pill", "slider", "roll"];
/// `MAX_POINTER_PX`: ipc.hpp kMaxPointerPx (view.MAX_POINTER_PX); the overview pointer may be negative.
pub const MAX_POINTER_PX: i64 = 8192;
/// `EDGE_SNAP_PX`: unproject's answer this close to a desktop's edge is on it (onto_edges).
pub const EDGE_SNAP_PX: f64 = 1e-6;
/// `MAX_POINTER_AZ, MAX_POINTER_EL`: ipc.hpp's bounds on the dot's direction, 1/100 degree.
pub const MAX_POINTER_AZ: i64 = 18000;
pub const MAX_POINTER_EL: i64 = 9000;
/// `LEVEL_BAR_W, LEVEL_BAR_H, LEVEL_BAR_GAP, LEVEL_BAR_PAD, LEVEL_BAR_SHARE, LEVEL_BAR_MIN_W`: the level bar
/// (glass.hpp kLevelBar*, tile px).
pub const LEVEL_BAR_W: f64 = 148.0;
pub const LEVEL_BAR_H: f64 = 26.0;
pub const LEVEL_BAR_GAP: f64 = 12.0;
pub const LEVEL_BAR_PAD: f64 = 4.0;
pub const LEVEL_BAR_SHARE: f64 = 0.25;
pub const LEVEL_BAR_MIN_W: f64 = 120.0;
/// `LEVEL_EDGE_CLEAR`: the band just outside every edge left to the edge's resize zone.
pub const LEVEL_EDGE_CLEAR: f64 = 6.0;
/// `ROLL_LEVEL, ROLL_STEP, MAX_ROLL`: how far from level counts as level, a turn segment's step, the wire's bound
/// (1/100 degree).
pub const ROLL_LEVEL: i64 = 50;
pub const ROLL_STEP: i64 = 200;
pub const MAX_ROLL: i64 = 9000;
/// `LEVEL_SETTLE_S`: after R, a desktop's measured tilts this long level it again.
pub const LEVEL_SETTLE_S: f64 = 0.5;
/// `ROLL_SIDES`: the level bar's turn segments, left and right.
pub const ROLL_SIDES: [&str; 2] = ["ccw", "cw"];
/// `IMMERSIVE_GAP, IMMERSIVE_MAIN_W, IMMERSIVE_TAIL_W, IMMERSIVE_CLEAR`: the 3D button (glass.hpp kImmersive*).
pub const IMMERSIVE_GAP: f64 = 2.0;
pub const IMMERSIVE_MAIN_W: f64 = 40.0;
pub const IMMERSIVE_TAIL_W: f64 = 28.0;
pub const IMMERSIVE_CLEAR: f64 = 16.0;
/// `LEVEL_SEGMENTS`: the level bar's segments left to right.
pub const LEVEL_SEGMENTS: [&str; 3] = ["ccw", "level", "cw"];
/// `ROLL_ICONS`: what a desktop shows (ipc.hpp RollPayload icons).
pub const ROLL_ICONS: [&str; 2] = ["none", "bar"];
/// `HELP_RECTS`: the help card's rects.
pub const HELP_RECTS: [&str; 4] = ["card", "prev", "indicator", "next"];

/// `POINTER_HIDDEN`: the overview pointer the renderer starts with, as the frame's payload.
pub fn pointer_hidden() -> Json {
    Json::Object(vec![
        ("visible".into(), Json::Bool(false)),
        ("x".into(), Json::Int(0)),
        ("y".into(), Json::Int(0)),
        ("held".into(), Json::Bool(false)),
    ])
}

/// `ROLL_HIDDEN`: the `roll` state the renderer starts with.
pub fn roll_hidden() -> Json {
    Json::Object(vec![
        ("roll".into(), Json::Array(vec![Json::Int(0); MAX_DESKTOPS])),
        ("icons".into(), Json::Array(vec![Json::Str("none".into()); MAX_DESKTOPS])),
        ("hover".into(), Json::Int(-1)),
        ("side".into(), Json::Str("none".into())),
        ("active".into(), Json::Bool(false)),
    ])
}

/// What is under the dot (`hit`, `panel_hit`, `help_hit`): the Python's tuples, the kind first.
#[derive(Clone, Debug, PartialEq)]
pub enum Hit {
    /// `('pill', i)`: the pill, or the open panel's header strip.
    Pill(usize),
    /// `('panel', i)`: the rest of the open panel.
    Panel(usize),
    /// `('slider', i, field, lx)`: a slider row's knob range at panel x `lx`.
    Slider(usize, &'static str, f64),
    /// `('scale', i, percent)`: an item of the open Scale list.
    Scale(usize, i64),
    /// `('background', i, name)`: an item of the open Background list.
    Background(usize, &'static str),
    /// `('resolution', i, (w, h))`: an item of the open Resolution list.
    Resolution(usize, (i64, i64)),
    ResolutionBox(usize),
    ScaleBox(usize),
    BackgroundBox(usize),
    BackgroundSwitch(usize),
    /// `('action', i, name)`: a desktop-row button (PANEL_ACTIONS).
    Action(usize, &'static str),
    /// `('corner', i, 'tl' | 'tr' | 'bl' | 'br')`.
    Corner(usize, &'static str),
    /// `('edge', i, 't' | 'b' | 'l' | 'r')`.
    Edge(usize, &'static str),
    /// `('desktop', i, (px, py))`: a desktop's content at that tile pixel.
    Desktop(usize, [f64; 2]),
    /// `('maximize', i)`.
    Maximize(usize),
    /// `('immersive', i, 'main' | 'tail')`: the 3D button.
    Immersive(usize, &'static str),
    /// `('roll', i, segment)`: a level bar segment (LEVEL_SEGMENTS).
    Roll(usize, &'static str),
    /// `('help', 'prev' | 'indicator' | 'next' | 'card')`.
    Help(&'static str),
    /// `('options_gear', i)`: the gear beside a pill (OverviewPointer's hit, free_pointer.py line 1430).
    OptionsGear(usize),
}

impl Hit {
    /// The tuple's first item.
    pub fn kind(&self) -> &'static str {
        match self {
            Hit::Pill(_) => "pill",
            Hit::Panel(_) => "panel",
            Hit::Slider(..) => "slider",
            Hit::Scale(..) => "scale",
            Hit::Background(..) => "background",
            Hit::Resolution(..) => "resolution",
            Hit::ResolutionBox(_) => "resolution_box",
            Hit::ScaleBox(_) => "scale_box",
            Hit::BackgroundBox(_) => "background_box",
            Hit::BackgroundSwitch(_) => "background_switch",
            Hit::Action(..) => "action",
            Hit::Corner(..) => "corner",
            Hit::Edge(..) => "edge",
            Hit::Desktop(..) => "desktop",
            Hit::Maximize(_) => "maximize",
            Hit::Immersive(..) => "immersive",
            Hit::Roll(..) => "roll",
            Hit::Help(_) => "help",
            Hit::OptionsGear(_) => "options_gear",
        }
    }
}

/// A resize handle (`border_handle`): `('corner', name)` or `('edge', name)`.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Handle {
    Corner(&'static str),
    Edge(&'static str),
}

// --- the renderer's status lines -------------------------------------------------------------------------------------

fn finite_number(v: &Json) -> Option<f64> {
    match v {
        Json::Int(i) => Some(*i as f64),
        Json::Float(f) if f.is_finite() => Some(*f),
        _ => None,
    }
}

fn keys_are(value: &Json, keys: &[&str]) -> bool {
    value.as_object().is_some_and(|o| o.len() == keys.len() && keys.iter().all(|k| o.iter().any(|(n, _)| n == k)))
}

/// `parse_overview_geometry(value)`: the renderer's `overview_geometry` status line (spike/src/view.cpp
/// overview_geometry_json) as an [`orbit::Geometry`]: `w`, `h` ints in 1..=MAX_POINTER_PX, `half` two numbers in
/// 1e-3..=100, 1..=MAX_DESKTOPS tiles of four numbers within +-MAX_POINTER_PX, each with a positive size.
pub fn parse_overview_geometry(value: &Json) -> Result<Geometry> {
    let bad = || Invalid::new("invalid overview geometry");
    let number = |v: &Json, lo: f64, hi: f64| finite_number(v).filter(|n| lo <= *n && *n <= hi).ok_or_else(bad);
    if !keys_are(value, &["w", "h", "half", "tiles"]) {
        return Err(bad());
    }
    let limit = MAX_POINTER_PX as f64;
    let (w, h) = match (value.get("w").and_then(Json::as_int), value.get("h").and_then(Json::as_int)) {
        (Some(w), Some(h)) if 0 < w && w <= MAX_POINTER_PX && 0 < h && h <= MAX_POINTER_PX => (w, h),
        _ => return Err(bad()),
    };
    let (Some(Json::Array(half)), Some(Json::Array(tiles))) = (value.get("half"), value.get("tiles")) else {
        return Err(bad());
    };
    if half.len() != 2 || !(1..=MAX_DESKTOPS).contains(&tiles.len()) {
        return Err(bad());
    }
    let half = [number(&half[0], 1e-3, 100.0)?, number(&half[1], 1e-3, 100.0)?];
    let mut out = Vec::with_capacity(tiles.len());
    for t in tiles {
        let Json::Array(t) = t else { return Err(bad()) };
        if t.len() != 4 {
            return Err(bad());
        }
        let mut tile = [0.0; 4];
        for (slot, v) in tile.iter_mut().zip(t) {
            *slot = number(v, -limit, limit)?;
        }
        if tile[2] <= 0.0 || tile[3] <= 0.0 {
            return Err(bad());
        }
        out.push(tile);
    }
    Ok(Geometry { w, h, half, tiles: out })
}

/// The settled Restore geometry (`parse_maximize_geometry`): the maximized desktop as the renderer drew it.
#[derive(Clone, Debug, PartialEq)]
pub struct MaximizeGeometry {
    pub monitor: String,
    /// (w, h, tan half-FOV x, y).
    pub canvas: [f64; 4],
    pub tile: [f64; 4],
    /// (x, y, distance, curvature, roll), radians and metres.
    pub placement: [f64; 5],
    pub rect: [f64; 4],
    pub picture: [f64; 4],
    /// The padded hit mesh, a triangle strip in the world.
    pub hit_mesh: Vec<[f64; 3]>,
    /// The view's rotation, row-major.
    pub view: [f64; 9],
}

/// options_menu.monitor: a string of at most 32 UTF-8 bytes naming a managed output.
fn options_monitor(value: &Json) -> Result<String> {
    let Some(s) = value.as_str().filter(|s| s.len() <= 32) else {
        return Err(Invalid::new("invalid options string"));
    };
    let digits = s.strip_prefix("XR-").unwrap_or("");
    let ok = digits.starts_with(|c: char| ('1'..='9').contains(&c)) && digits.bytes().all(|b| b.is_ascii_digit());
    if !ok {
        return Err(Invalid::new("invalid options monitor"));
    }
    Ok(s.to_string())
}

fn maximize_numbers<const N: usize>(v: Option<&Json>) -> Result<[f64; N]> {
    let bad = || Invalid::new("invalid maximize coordinates");
    let Some(Json::Array(items)) = v else { return Err(bad()) };
    if items.len() != N {
        return Err(bad());
    }
    let mut out = [0.0; N];
    for (slot, item) in out.iter_mut().zip(items) {
        *slot = finite_number(item).filter(|n| n.abs() <= 1e6).ok_or_else(bad)?;
    }
    Ok(out)
}

/// `parse_maximize_geometry(value)`: validate the display-only settled Restore geometry (None: none); never accept
/// arbitrary meshes. Refused in the Python's order: the keys, the monitor, `maximized` true, the coordinates, the
/// extents, the mesh (3..=1024 points) and the view (a rotation, to 1e-3).
pub fn parse_maximize_geometry(value: &Json) -> Result<Option<MaximizeGeometry>> {
    if *value == Json::Null {
        return Ok(None);
    }
    let keys = ["monitor", "maximized", "canvas", "tile", "placement", "rect", "picture", "hit_mesh", "view"];
    if !keys_are(value, &keys) {
        return Err(Invalid::new("invalid maximize geometry"));
    }
    let monitor = options_monitor(value.get("monitor").unwrap_or(&Json::Null))?;
    if value.get("maximized") != Some(&Json::Bool(true)) {
        return Err(Invalid::new("unsettled maximize geometry"));
    }
    let canvas = maximize_numbers::<4>(value.get("canvas"))?;
    let tile = maximize_numbers::<4>(value.get("tile"))?;
    let placement = maximize_numbers::<5>(value.get("placement"))?;
    let rect = maximize_numbers::<4>(value.get("rect"))?;
    let picture = maximize_numbers::<4>(value.get("picture"))?;
    let view = maximize_numbers::<9>(value.get("view"))?;
    let limit = MAX_POINTER_PX as f64;
    if canvas.iter().any(|v| *v <= 0.0)
        || canvas[..2].iter().any(|v| *v > limit)
        || placement[2] <= 0.0
        || [2, 3].iter().any(|&i| tile[i] <= 0.0 || rect[i] <= 0.0)
    {
        return Err(Invalid::new("invalid maximize extent"));
    }
    let Some(Json::Array(mesh)) = value.get("hit_mesh") else {
        return Err(Invalid::new("invalid maximize mesh"));
    };
    if !(3..=1024).contains(&mesh.len()) {
        return Err(Invalid::new("invalid maximize mesh"));
    }
    let hit_mesh = mesh.iter().map(|v| maximize_numbers::<3>(Some(v))).collect::<Result<Vec<_>>>()?;
    for i in 0..3 {
        for j in 0..3 {
            let d = py_sum((0..3).map(|k| view[3 * i + k] * view[3 * j + k]));
            if (d - if i == j { 1.0 } else { 0.0 }).abs() > 1e-3 {
                return Err(Invalid::new("invalid maximize rotation"));
            }
        }
    }
    Ok(Some(MaximizeGeometry { monitor, canvas, tile, placement, rect, picture, hit_mesh, view }))
}

/// `band_ray(geometry, x, y)`: canvas point (x, y), where the band's dot is, as a world ray (the view undone).
pub fn band_ray(geometry: &MaximizeGeometry, x: f64, y: f64) -> Vec3 {
    let [w, h, hw, hh] = geometry.canvas;
    let eye = [(2.0 * x / w - 1.0) * hw, (1.0 - 2.0 * y / h) * hh, -1.0];
    let m = &geometry.view;
    std::array::from_fn(|i| py_sum((0..3).map(|j| m[j * 3 + i] * eye[j])))
}

/// `math.degrees`.
fn degrees(radians: f64) -> f64 {
    radians * (180.0 / PI)
}

/// `band_canvas(geometry, px, py)`: point (px, py) of the stepped desktop's surface on the canvas.
pub fn band_canvas(geometry: &MaximizeGeometry, px: f64, py: f64) -> [f64; 2] {
    let [w, h, hw, hh] = geometry.canvas;
    let [x, y, d, c, r] = geometry.placement;
    let placed = Pose {
        x: degrees(x) * 10.0,
        y: degrees(y) * 10.0,
        distance: d * 100.0,
        curvature: degrees(c),
        roll: degrees(r) * 100.0,
    };
    let world = orbit::place_point_on(w, h, [hw, hh], &geometry.tile, &placed, px, py);
    let m = &geometry.view;
    let e: [f64; 3] = std::array::from_fn(|i| py_sum((0..3).map(|j| m[i * 3 + j] * world[j])));
    [(e[0] / -e[2] / hw + 1.0) * w / 2.0, (1.0 - e[1] / -e[2] / hh) * h / 2.0]
}

fn dot(a: &Vec3, b: &Vec3) -> f64 {
    py_sum((0..3).map(|i| a[i] * b[i]))
}

fn sub(a: &Vec3, b: &Vec3) -> Vec3 {
    [a[0] - b[0], a[1] - b[1], a[2] - b[2]]
}

fn cross(a: &Vec3, b: &Vec3) -> Vec3 {
    [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]
}

/// `restore_mesh_hit(geometry, ray)`: Moller-Trumbore, the ray (None: false) from the eye against the padded triangle
/// strip.
pub fn restore_mesh_hit(geometry: &MaximizeGeometry, ray: Option<Vec3>) -> bool {
    let Some(ray) = ray else { return false };
    let mesh = &geometry.hit_mesh;
    for j in 0..mesh.len().saturating_sub(2) {
        let (a, b, c) = (&mesh[j], &mesh[j + 1], &mesh[j + 2]);
        let (e1, e2) = (sub(b, a), sub(c, a));
        let h = cross(&ray, &e2);
        let det = dot(&e1, &h);
        if det.abs() < 1e-10 {
            continue;
        }
        let inv = 1.0 / det;
        let v0 = [-a[0], -a[1], -a[2]];
        let u = dot(&v0, &h) * inv;
        let q = cross(&v0, &e1);
        let v = dot(&ray, &q) * inv;
        let t = dot(&e2, &q) * inv;
        if u >= -1e-7 && v >= -1e-7 && u + v <= 1.0 + 1e-7 && t > 0.0 {
            return true;
        }
    }
    false
}

/// `parse_roll_geometry(value)`: the renderer's `roll_geometry` status line (wayland.cpp take_roll_geometry), each
/// desktop's tilt from level (1/100 degree, within +-18000) or None where it is not measured.
pub fn parse_roll_geometry(value: &Json) -> Result<Vec<Option<i64>>> {
    let bad = || Invalid::new("invalid roll geometry");
    if !keys_are(value, &["tilt"]) {
        return Err(bad());
    }
    let Some(Json::Array(tilt)) = value.get("tilt") else { return Err(bad()) };
    if !(1..=MAX_DESKTOPS).contains(&tilt.len()) {
        return Err(bad());
    }
    tilt.iter()
        .map(|t| match t {
            Json::Null => Ok(None),
            Json::Int(v) if (-18000..=18000).contains(v) => Ok(Some(*v)),
            _ => Err(bad()),
        })
        .collect()
}

// --- the level bar and the 3D button -----------------------------------------------------------------------------------

/// `level_bar_scale(tile)`: what the level bar and corner caps are scaled by (glass.hpp level_bar_scale): 1, less on
/// a narrow desktop; 0 if empty.
pub fn level_bar_scale(tile: &Tile) -> f64 {
    let (tw, th) = (tile[2], tile[3]);
    if !(tw > 0.0 && th > 0.0) {
        return 0.0;
    }
    py_min(py_min(py_max(LEVEL_BAR_SHARE * tw, LEVEL_BAR_MIN_W), LEVEL_BAR_W), tw) / LEVEL_BAR_W
}

/// `level_bar_rect(tile)`: the level bar as drawn (glass.hpp level_bar_rect), (x, y, w, h) tile px centred above the
/// top edge; empty for an empty tile.
pub fn level_bar_rect(tile: &Tile) -> [f64; 4] {
    let s = level_bar_scale(tile);
    if s.is_nan() || s <= 0.0 {
        return [0.0; 4];
    }
    let (w, h) = (LEVEL_BAR_W * s, LEVEL_BAR_H * s);
    [tile[0] + (tile[2] - w) / 2.0, tile[1] - LEVEL_BAR_GAP * s - h, w, h]
}

/// The `restore_band` frame's own key order (run_id before request_id).
#[derive(serde::Serialize)]
struct RestoreBandFrame<'a> {
    schema: u32,
    run_id: &'a str,
    session: &'a str,
    request_id: u64,
    op: &'static str,
    payload: RestoreBandPayload,
}

#[derive(serde::Serialize)]
struct RestoreBandPayload {
    open: bool,
}

/// `restore_band_frame(run_id, session, request_id, is_open)`: the renderer's `restore_band` frame, the Step-Back Band
/// open or shut (ipc.hpp RestoreBandPayload). The Python's `type(is_open) is not bool` refusal is the type here.
pub fn restore_band_frame(run_id: &str, session: &str, request_id: u64, is_open: bool) -> String {
    let frame = RestoreBandFrame {
        schema: 1,
        run_id,
        session,
        request_id,
        op: "restore_band",
        payload: RestoreBandPayload { open: is_open },
    };
    let mut out = crate::pyjson::compact(&frame);
    out.push('\n');
    out
}

/// `maximize_rect(tile)`: the maximize button, left of the level bar.
pub fn maximize_rect(tile: &Tile) -> [f64; 4] {
    let s = level_bar_scale(tile);
    let [x, y, _, _] = level_bar_rect(tile);
    [x - 38.0 * s, y, 26.0 * s, 26.0 * s]
}

/// `maximize_hit(tile, q)`: tile point `q` (None: missed) on the maximize button, padded by 4 scaled px.
pub fn maximize_hit(tile: &Tile, q: Option<[f64; 2]>) -> bool {
    let [x, y, w, h] = maximize_rect(tile);
    let pad = 4.0 * level_bar_scale(tile);
    w > 0.0 && options_geometry::inside(&[x - pad, y - pad, w + 2.0 * pad, h + 2.0 * pad], q)
}

/// `level_bar_segment(tile, q)`: the segment (LEVEL_SEGMENTS) at tile point `q` (None: missed), or None (glass.hpp
/// level_bar_segment): the drawn bar padded by LEVEL_BAR_PAD, split at its thirds.
pub fn level_bar_segment(tile: &Tile, q: Option<[f64; 2]>) -> Option<&'static str> {
    let [x, y, w, h] = level_bar_rect(tile);
    let q = q?;
    if w.is_nan() || w <= 0.0 {
        return None;
    }
    let pad = LEVEL_BAR_PAD * level_bar_scale(tile);
    if q[0] < x - pad || q[0] > x + w + pad || q[1] < y - pad || q[1] > y + h + pad {
        return None;
    }
    let third = w / LEVEL_SEGMENTS.len() as f64;
    if q[0] < x + third {
        return Some(LEVEL_SEGMENTS[0]);
    }
    Some(if q[0] < x + 2.0 * third { LEVEL_SEGMENTS[1] } else { LEVEL_SEGMENTS[2] })
}

/// `_immersive_rects(tile)`: the 3D button's (segment, tail) rects, or None if the desktop is empty or too narrow.
pub fn immersive_rects(tile: &Tile) -> Option<([f64; 4], [f64; 4])> {
    let s = level_bar_scale(tile);
    if s.is_nan() || s <= 0.0 {
        return None;
    }
    let [bx, by, bw, bh] = level_bar_rect(tile);
    let main = [bx + bw + IMMERSIVE_GAP * s, by, IMMERSIVE_MAIN_W * s, bh];
    let tail = [main[0] + main[2] + IMMERSIVE_GAP * s, by, IMMERSIVE_TAIL_W * s, bh];
    (tail[0] + tail[2] <= tile[0] + tile[2] - IMMERSIVE_CLEAR * s).then_some((main, tail))
}

/// `immersive_main_rect(tile)`: the 3D segment as drawn (glass.hpp immersive_main_rect); empty without a button.
pub fn immersive_main_rect(tile: &Tile) -> [f64; 4] {
    immersive_rects(tile).map_or([0.0; 4], |r| r.0)
}

/// `immersive_tail_rect(tile)`: the tail as drawn (glass.hpp immersive_tail_rect); empty without a button.
pub fn immersive_tail_rect(tile: &Tile) -> [f64; 4] {
    immersive_rects(tile).map_or([0.0; 4], |r| r.1)
}

/// `immersive_part(tile, q)`: 'main' or 'tail' at tile point `q` (None: missed), or None (glass.hpp immersive_part),
/// padded by LEVEL_BAR_PAD like the bar, each gap split down its middle.
pub fn immersive_part(tile: &Tile, q: Option<[f64; 2]>) -> Option<&'static str> {
    let (main, tail) = immersive_rects(tile)?;
    let q = q?;
    let s = level_bar_scale(tile);
    let (pad, half) = (LEVEL_BAR_PAD * s, IMMERSIVE_GAP * s / 2.0);
    if q[1] < main[1] - pad || q[1] > main[1] + main[3] + pad || q[0] < main[0] - half || q[0] > tail[0] + tail[2] + pad
    {
        return None;
    }
    Some(if q[0] < tail[0] - half { "main" } else { "tail" })
}

// --- the levelling roll ------------------------------------------------------------------------------------------------

/// `roll_side(tilt, roll)`: the turn segment that levels a desktop, 'cw' if turned counter-clockwise from level by
/// more than ROLL_LEVEL, 'ccw' if clockwise, None if level or not measured (`tilt` None). 1/100 degree.
pub fn roll_side(tilt: Option<i64>, roll: i64) -> Option<&'static str> {
    let t = tilt? + roll;
    if t.abs() <= ROLL_LEVEL {
        return None;
    }
    Some(if t > 0 { "cw" } else { "ccw" })
}

/// `roll_click(tilt, roll, side)`: the levelling roll after a click on turn segment `side` ('ccw' / 'cw'): straight to
/// level when it points toward level, else ROLL_STEP further that way, within +-MAX_ROLL.
pub fn roll_click(tilt: Option<i64>, roll: i64, side: &str) -> Result<i64> {
    if !ROLL_SIDES.contains(&side) {
        return Err(Invalid::new("invalid roll side"));
    }
    let target = match tilt {
        Some(t) if roll_side(Some(t), roll) == Some(side) => -t,
        _ => roll + if side == "ccw" { ROLL_STEP } else { -ROLL_STEP },
    };
    Ok(target.clamp(-MAX_ROLL, MAX_ROLL))
}

/// `roll_drag(tilt, start, turn)`: the levelling roll while a turn segment is dragged, the press's roll plus the dot's
/// `turn` round the desktop's centre (rounded, ties to even), within +-MAX_ROLL; within ROLL_LEVEL of level it rests
/// at level.
pub fn roll_drag(tilt: Option<i64>, start: f64, turn: f64) -> i64 {
    let r = py_round(start + turn).clamp(-MAX_ROLL, MAX_ROLL);
    match tilt {
        Some(t) if (t + r).abs() <= ROLL_LEVEL && (-MAX_ROLL..=MAX_ROLL).contains(&-t) => -t,
        _ => r,
    }
}

/// `level_roll(tilt)`: the roll that levels a desktop, -tilt within +-MAX_ROLL where measured, else 0.
pub fn level_roll(tilt: Option<i64>) -> i64 {
    tilt.map_or(0, |t| (-t).clamp(-MAX_ROLL, MAX_ROLL))
}

fn object_keys_are(value: &Json, keys: &[&str]) -> bool {
    keys_are(value, keys)
}

/// `roll_frame(run_id, session, request_id, state)`: the `roll` frame (ipc.hpp RollPayload), `state` a dict of
/// `roll` (MAX_DESKTOPS ints within +-MAX_ROLL), `icons` (MAX_DESKTOPS of ROLL_ICONS), `hover` (-1..MAX_DESKTOPS-1),
/// `side` ('none' exactly when nothing is hovered, else a LEVEL_SEGMENTS name) and `active` (a bool).
pub fn roll_frame(run_id: &str, session: &str, request_id: u64, state: &Json) -> Result<String> {
    let bad = || Invalid::new("invalid roll state");
    if !object_keys_are(state, &["roll", "icons", "hover", "side", "active"]) {
        return Err(bad());
    }
    let field = |k: &str| state.get(k).cloned().unwrap_or(Json::Null);
    let (roll, icons, hover, side, active) = (field("roll"), field("icons"), field("hover"), field("side"), field("active"));
    let roll_ok = matches!(&roll, Json::Array(r) if r.len() == MAX_DESKTOPS
        && r.iter().all(|v| v.as_int().is_some_and(|v| (-MAX_ROLL..=MAX_ROLL).contains(&v))));
    let icons_ok = matches!(&icons, Json::Array(i) if i.len() == MAX_DESKTOPS
        && i.iter().all(|v| v.as_str().is_some_and(|s| ROLL_ICONS.contains(&s))));
    let hover_ok = hover.as_int().is_some_and(|h| -1 <= h && h < MAX_DESKTOPS as i64);
    let side_name = side.as_str().filter(|s| *s == "none" || LEVEL_SEGMENTS.contains(s));
    if !roll_ok
        || !icons_ok
        || !hover_ok
        || side_name.is_none()
        || (hover.as_int().unwrap_or(0) < 0) != (side_name == Some("none"))
        || active.as_bool().is_none()
    {
        return Err(bad());
    }
    let payload = Json::Object(vec![
        ("roll".into(), roll),
        ("icons".into(), icons),
        ("hover".into(), hover),
        ("side".into(), side),
        ("active".into(), active),
    ]);
    Ok(view::frame(run_id, session, request_id, "roll", payload))
}

// --- the help card -----------------------------------------------------------------------------------------------------

/// The help card's rects (`parse_help_geometry`), canvas px; the three controls only on a paged card.
#[derive(Clone, Debug, PartialEq)]
pub struct HelpGeometry {
    pub card: [f64; 4],
    pub prev: Option<[f64; 4]>,
    pub indicator: Option<[f64; 4]>,
    pub next: Option<[f64; 4]>,
}

impl HelpGeometry {
    /// `help_[name]`, for a HELP_RECTS name.
    pub fn rect(&self, name: &str) -> Option<[f64; 4]> {
        match name {
            "card" => Some(self.card),
            "prev" => self.prev,
            "indicator" => self.indicator,
            "next" => self.next,
            _ => None,
        }
    }
}

/// `parse_help_geometry(value)`: the renderer's `help_geometry` status line (hud.hpp help_geometry_json): `card` and,
/// on a paged card, all of `prev` / `indicator` / `next`, each four ints within +-MAX_POINTER_PX with a positive size;
/// None (no card up) for None.
pub fn parse_help_geometry(value: &Json) -> Result<Option<HelpGeometry>> {
    let bad = || Invalid::new("invalid help geometry");
    if *value == Json::Null {
        return Ok(None);
    }
    let Some(entries) = value.as_object() else { return Err(bad()) };
    if value.get("card").is_none() || entries.iter().any(|(k, _)| !HELP_RECTS.contains(&k.as_str())) {
        return Err(bad());
    }
    if entries.len() != 1 && entries.len() != HELP_RECTS.len() {
        return Err(bad());
    }
    let rect = |r: &Json| -> Result<[f64; 4]> {
        let Json::Array(r) = r else { return Err(bad()) };
        if r.len() != 4 {
            return Err(bad());
        }
        let mut out = [0.0; 4];
        for (slot, v) in out.iter_mut().zip(r) {
            match v.as_int() {
                Some(v) if (-MAX_POINTER_PX..=MAX_POINTER_PX).contains(&v) => *slot = v as f64,
                _ => return Err(bad()),
            }
        }
        if out[2] <= 0.0 || out[3] <= 0.0 {
            return Err(bad());
        }
        Ok(out)
    };
    let mut help = HelpGeometry { card: [0.0; 4], prev: None, indicator: None, next: None };
    for (name, r) in entries {
        let r = rect(r)?;
        match name.as_str() {
            "card" => help.card = r,
            "prev" => help.prev = Some(r),
            "indicator" => help.indicator = Some(r),
            _ => help.next = Some(r),
        }
    }
    Ok(Some(help))
}

/// `help_hit(help_, pointer, margin=0.0)`: `('help', 'prev' | 'indicator' | 'next' | 'card')` if canvas point `pointer`
/// is on the help card (`margin` canvas px round the card counts too), else None.
pub fn help_hit(help: Option<&HelpGeometry>, pointer: Option<[f64; 2]>, margin: f64) -> Option<Hit> {
    let (help, p) = (help?, pointer?);
    let inside =
        |r: [f64; 4], m: f64| r[0] - m <= p[0] && p[0] < r[0] + r[2] + m && r[1] - m <= p[1] && p[1] < r[1] + r[3] + m;
    for name in ["prev", "next", "indicator"] {
        if help.rect(name).is_some_and(|r| inside(r, 0.0)) {
            return Some(Hit::Help(name));
        }
    }
    inside(help.card, margin).then_some(Hit::Help("card"))
}

// --- the canvas and a placed desktop's surface --------------------------------------------------------------------------

/// `place_point(g, tile, p, px, py)`: the world position of canvas pixel (px, py) on a desktop laid out at `tile` with
/// placement `p` ([`orbit::place_point`]).
pub fn place_point(g: &Geometry, tile: &Tile, p: &Pose, px: f64, py: f64) -> Vec3 {
    orbit::place_point(g, tile, p, px, py)
}

/// `to_canvas(g, point)`: a world point seen from the eye, on the canvas plane in canvas px; None if it is not in
/// front or lands past MAX_POINTER_PX.
pub fn to_canvas(g: &Geometry, point: &Vec3) -> Option<[f64; 2]> {
    let [x, y, z] = *point;
    if z > -1e-6 {
        return None;
    }
    let [hw, hh] = g.half;
    let out = [(x / -z / hw + 1.0) * g.w as f64 / 2.0, (1.0 - y / -z / hh) * g.h as f64 / 2.0];
    let limit = MAX_POINTER_PX as f64;
    (out[0].abs() <= limit && out[1].abs() <= limit).then_some(out)
}

/// `project(g, tile, p, px, py)`: the pointer position (canvas px) whose dot lies on tile pixel (px, py) of a placed
/// desktop; anywhere round you.
pub fn project(g: &Geometry, tile: &Tile, p: &Pose, px: f64, py: f64) -> [f64; 2] {
    orbit::pointer_at(g, &place_point(g, tile, p, px, py))
}

/// `_flat(g, tile, p, px, py)`: where tile pixel (px, py) of a placed desktop appears on the overview plane, or None.
pub fn flat(g: &Geometry, tile: &Tile, p: &Pose, px: f64, py: f64) -> Option<[f64; 2]> {
    to_canvas(g, &place_point(g, tile, p, px, py))
}

/// `_onto_edges(tile, q)`: tile pixel `q`, put exactly on the desktop's edge when within EDGE_SNAP_PX of it.
pub fn onto_edges(tile: &Tile, q: [f64; 2]) -> [f64; 2] {
    let [tx, ty, tw, th] = *tile;
    let snap = |v: f64, lo: f64, hi: f64| {
        if (v - lo).abs() < EDGE_SNAP_PX {
            lo
        } else if (v - hi).abs() < EDGE_SNAP_PX {
            hi
        } else {
            v
        }
    };
    [snap(q[0], tx, tx + tw), snap(q[1], ty, ty + th)]
}

/// `math.radians`.
fn radians(degrees: f64) -> f64 {
    degrees * (PI / 180.0)
}

/// `unproject(g, tile, p, pointer)`: the tile pixel that appears under the dot at canvas point `pointer` on a placed
/// desktop's surface, or None when the dot's ray misses it. The ray is turned back by the desktop's orbit
/// ([`orbit::unorbit_ray`]), then Newton's method (16 steps) on the flat projection from the exact flat answer.
pub fn unproject(g: &Geometry, tile: &Tile, p: &Pose, pointer: [f64; 2]) -> Option<[f64; 2]> {
    let pointer = orbit::unorbit_ray(g, tile, p, pointer)?;
    let p = Pose { x: 0.0, y: 0.0, ..*p };
    let [tx, ty, tw, th] = *tile;
    let (cx, cy) = (tx + tw / 2.0, ty + th / 2.0);
    let d = p.distance / 100.0;
    let (mut ax, mut av) = ((pointer[0] - cx) * d, (cy - pointer[1]) * d);
    let roll = radians(p.roll / 100.0);
    if roll != 0.0 {
        (ax, av) = (roll.cos() * ax + roll.sin() * av, -roll.sin() * ax + roll.cos() * av);
    }
    let mut q = [cx + ax, cy - av];
    let limit = 4.0 * py_max(tw, th);
    for _ in 0..16 {
        let f = flat(g, tile, &p, q[0], q[1])?;
        let (ex, ey) = (f[0] - pointer[0], f[1] - pointer[1]);
        if ex.abs() < 0.01 && ey.abs() < 0.01 {
            return Some(onto_edges(tile, q));
        }
        let step = 0.5;
        let fx = flat(g, tile, &p, q[0] + step, q[1])?;
        let fy = flat(g, tile, &p, q[0], q[1] + step)?;
        let (a, b) = ((fx[0] - f[0]) / step, (fy[0] - f[0]) / step);
        let (c, e) = ((fx[1] - f[1]) / step, (fy[1] - f[1]) / step);
        let det = a * e - b * c;
        if det.abs() < 1e-9 {
            return None;
        }
        let (dx, dy) = ((e * ex - b * ey) / det, (a * ey - c * ex) / det);
        q = [q[0] - dx, q[1] - dy];
        if (q[0] - cx).abs() > limit || (q[1] - cy).abs() > limit {
            return None;
        }
    }
    let f = flat(g, tile, &p, q[0], q[1])?;
    (py_hypot(f[0] - pointer[0], f[1] - pointer[1]) < 0.5).then(|| onto_edges(tile, q))
}

/// `surface_to_layout(tile, output, q, inset=INSET_PX)`: desktop surface pixel `q` to the integer Hyprland layout pixel
/// of that spot on the desktop's monitor `output`, at least `inset` px inside every edge (the nearest pixel).
pub fn surface_to_layout(tile: &Tile, output: &OutputRect, q: [f64; 2], inset: f64) -> (i64, i64) {
    let [tx, ty, tw, th] = *tile;
    let lx = py_min(py_max((q[0] - tx) / tw * output.width, inset), output.width - 1.0 - inset);
    let ly = py_min(py_max((q[1] - ty) / th * output.height, inset), output.height - 1.0 - inset);
    ((output.x as f64 + lx + 0.5).trunc() as i64, (output.y as f64 + ly + 0.5).trunc() as i64)
}

/// `layout_to_surface(tile, output, lx, ly)`: monitor-local logical px (lx, ly) on `output` to desktop surface px
/// (surface_to_layout's inverse).
pub fn layout_to_surface(tile: &Tile, output: &OutputRect, lx: f64, ly: f64) -> [f64; 2] {
    let [tx, ty, tw, th] = *tile;
    [tx + lx / output.width * tw, ty + ly / output.height * th]
}

// --- the hit zones -------------------------------------------------------------------------------------------------------

/// `corner_zone(tile)`: half the size of a corner's grab square, tile px.
pub fn corner_zone(tile: &Tile) -> f64 {
    py_min(py_max(CORNER_SHARE * py_min(tile[2], tile[3]), CORNER_MIN), CORNER_MAX)
}

/// `border_handle(tile, q)`: the resize handle at tile pixel `q`, or None: a corner in a corner's square, else the
/// nearest edge within the border (EDGE_OUT_PX outside the desktop to ENTER_PX inside it).
pub fn border_handle(tile: &Tile, q: [f64; 2]) -> Option<Handle> {
    let depth = inside_by(tile, q);
    if depth >= ENTER_PX {
        return None;
    }
    let [tx, ty, tw, th] = *tile;
    let z = corner_zone(tile);
    let corners = [("tl", (tx, ty)), ("tr", (tx + tw, ty)), ("bl", (tx, ty + th)), ("br", (tx + tw, ty + th))];
    for (corner, (cx, cy)) in corners {
        if (q[0] - cx).abs() <= z && (q[1] - cy).abs() <= z {
            return Some(Handle::Corner(corner));
        }
    }
    if depth < -EDGE_OUT_PX {
        return None;
    }
    let gaps = [q[1] - ty, ty + th - q[1], q[0] - tx, tx + tw - q[0]];
    let mut best = 0;
    for (i, gap) in gaps.iter().enumerate().skip(1) {
        if *gap < gaps[best] {
            best = i;
        }
    }
    Some(Handle::Edge(EDGES[best]))
}

/// `zone_of(h)`: the zone contract's name (ZONES) for a hit.
pub fn zone_of(h: Option<&Hit>) -> &'static str {
    match h {
        None => "space",
        Some(Hit::Roll(..)) => "rotate",
        Some(Hit::Corner(..) | Hit::Edge(..)) => "resize",
        Some(Hit::Pill(_)) => "move",
        Some(Hit::Desktop(..)) => "content",
        Some(_) => "control",
    }
}

/// `is_text_shape(name)`: a requested cursor's name (None: none) is a text cursor, TEXT_SHAPES in any case and with
/// `_` for `-`.
pub fn is_text_shape(name: Option<&str>) -> bool {
    name.is_some_and(|n| TEXT_SHAPES.contains(&n.to_lowercase().replace('_', "-").as_str()))
}

/// `TextHold`: whether the beam is on. At once when the app asks for it, and at once when it stops, except that text
/// coming back within TEXT_BOUNCE_S of going is a gap between lines: for TEXT_SWEEP_S after that every leave waits
/// TEXT_BOUNCE_S first.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct TextHold {
    pub on: bool,
    /// The end of a leave's wait.
    pub until: Option<f64>,
    /// When the beam last went.
    pub left: Option<f64>,
    /// Leaves wait until then.
    pub sweep: f64,
}

impl TextHold {
    /// `TextHold()`.
    pub fn new() -> TextHold {
        TextHold::default()
    }

    /// `step(text, now)`: the app asks for text (or not) at `now`; whether the beam is on.
    pub fn step(&mut self, text: bool, now: f64) -> bool {
        if text {
            if self.until.is_some() || self.left.is_some_and(|left| now - left <= TEXT_BOUNCE_S) {
                self.sweep = now + TEXT_SWEEP_S;
            }
            (self.on, self.until, self.left) = (true, None, None);
        } else if self.on {
            if self.until.is_none() && now < self.sweep {
                self.until = Some(now + TEXT_BOUNCE_S);
            } else if self.until.is_none_or(|until| now >= until) {
                (self.on, self.until, self.left) = (false, None, Some(now));
            }
        }
        self.on
    }

    /// `clear()`.
    pub fn clear(&mut self) {
        *self = TextHold::default();
    }
}

/// `look_for(h)`: the dot's look over hit `h` (POINTER_LOOKS, handles only); None for the plain dot.
pub fn look_for(h: Option<&Hit>) -> Option<&'static str> {
    match h {
        Some(Hit::Corner(_, name) | Hit::Edge(_, name)) => {
            RESIZE_LOOKS.iter().find(|(k, _)| k == name).map(|(_, look)| *look)
        }
        Some(Hit::Pill(_)) => Some("move"),
        Some(Hit::Roll(..)) => Some("rotate"),
        _ => None,
    }
}

// --- the glass under a desktop ---------------------------------------------------------------------------------------------

/// `glass_anchor(tile)`: what glass hangs from (view.cpp glass_anchor), the middle of the border's outer edge under the
/// desktop.
pub fn glass_anchor(tile: &Tile) -> (f64, f64) {
    (tile[0] + tile[2] / 2.0, tile[1] + tile[3] + BORDER_PX)
}

fn glass_scale(p: &Pose) -> f64 {
    if p.distance > 0.0 { p.distance / 100.0 } else { 1.0 }
}

/// `glass_to_desktop(tile, p, gx, gy)`: glass px to the desktop surface px it is drawn at (view.cpp glass_to_desktop),
/// scaled by the desktop's distance about glass_anchor.
pub fn glass_to_desktop(tile: &Tile, p: &Pose, gx: f64, gy: f64) -> (f64, f64) {
    let k = glass_scale(p);
    let (ax, ay) = glass_anchor(tile);
    (ax + (gx - ax) * k, ay + (gy - ay) * k)
}

/// `desktop_to_glass(tile, p, q)`: glass_to_desktop's inverse for desktop surface px `q` (None stays None).
pub fn desktop_to_glass(tile: &Tile, p: &Pose, q: Option<[f64; 2]>) -> Option<[f64; 2]> {
    let q = q?;
    let k = glass_scale(p);
    let (ax, ay) = glass_anchor(tile);
    Some([ax + (q[0] - ax) / k, ay + (q[1] - ay) / k])
}

/// `project_glass(g, tile, p, gx, gy)`: the pointer position whose dot lies on glass point (gx, gy) under a placed
/// desktop.
pub fn project_glass(g: &Geometry, tile: &Tile, p: &Pose, gx: f64, gy: f64) -> [f64; 2] {
    let (x, y) = glass_to_desktop(tile, p, gx, gy);
    project(g, tile, p, x, y)
}

/// `pill_rect(tile)`: the drawn glass pill (glass.hpp glass_pill), centred PILL_GAP under the desktop.
pub fn pill_rect(tile: &Tile) -> [f64; 4] {
    let [tx, ty, tw, th] = *tile;
    let w = py_min(py_min(py_max(PILL_SHARE * tw, PILL_MIN), PILL_MAX), tw);
    [tx + (tw - w) / 2.0, ty + th + PILL_GAP, w, PILL_THICKNESS]
}

/// `panel_rect(tile)`: the placement panel (glass.hpp glass_panel), centred under the desktop, its top the pill's.
pub fn panel_rect(tile: &Tile) -> [f64; 4] {
    let [tx, ty, tw, th] = *tile;
    [tx + (tw - PANEL_W) / 2.0, ty + th + PILL_GAP, PANEL_W, PANEL_H]
}

/// `action_rect(i)`: the desktop row's button `i` (glass.hpp glass_panel_action), panel px.
pub fn action_rect(i: i64) -> [f64; 4] {
    [PANEL_PAD + i as f64 * (PANEL_ACTION_W + PANEL_ACTION_GAP), PANEL_ACTIONS_TOP, PANEL_ACTION_W, PANEL_ACTION_H]
}

/// `scale_item_rect(i)`: the Scale list's item `i` (view.SCALE_PERCENTS order, top down), ending PANEL_SCALE_LIST_GAP
/// above the box.
pub fn scale_item_rect(i: i64) -> [f64; 4] {
    let [bx, by, bw, _] = PANEL_SCALE_BOX;
    let top = by - PANEL_SCALE_LIST_GAP - SCALE_PERCENTS.len() as f64 * PANEL_SCALE_ITEM_H;
    [bx, top + i as f64 * PANEL_SCALE_ITEM_H, bw, PANEL_SCALE_ITEM_H]
}

/// `background_item_rect(i)`: the Background list's item `i` (environment.BACKGROUNDS order, top down), ending
/// PANEL_BACKGROUND_LIST_GAP above the box.
pub fn background_item_rect(i: i64) -> [f64; 4] {
    let [bx, by, bw, _] = PANEL_BACKGROUND_BOX;
    let top = by - PANEL_BACKGROUND_LIST_GAP - BACKGROUNDS.len() as f64 * PANEL_BACKGROUND_ITEM_H;
    [bx, top + i as f64 * PANEL_BACKGROUND_ITEM_H, bw, PANEL_BACKGROUND_ITEM_H]
}

/// `resolution_list_rect()`: the open Resolution list (glass.hpp glass_resolution_list), a heading row and as many item
/// rows as the largest RESOLUTION_GROUPS group, ending PANEL_RES_LIST_GAP above the box.
pub fn resolution_list_rect() -> [f64; 4] {
    let rows = 1 + RESOLUTION_GROUPS.iter().map(|(_, sizes)| sizes.len()).max().unwrap_or(0);
    let h = rows as f64 * PANEL_RES_ITEM_H;
    [PANEL_RES_LIST_X, PANEL_RES_BOX[1] - PANEL_RES_LIST_GAP - h, PANEL_RES_LIST_W, h]
}

/// `resolution_item_rect(i)`: the Resolution list's item `i` (geometry.RESOLUTIONS order) in its group's column, under
/// the column's heading; empty for `i` past the list.
pub fn resolution_item_rect(i: i64) -> [f64; 4] {
    if !(0..RESOLUTIONS.len() as i64).contains(&i) {
        return [0.0; 4];
    }
    let (mut column, mut row) = (0, i as usize);
    while row >= RESOLUTION_GROUPS[column].1.len() {
        row -= RESOLUTION_GROUPS[column].1.len();
        column += 1;
    }
    let [x, y, w, _] = resolution_list_rect();
    let cw = w / RESOLUTION_GROUPS.len() as f64;
    [x + column as f64 * cw, y + (1 + row) as f64 * PANEL_RES_ITEM_H, cw, PANEL_RES_ITEM_H]
}

/// `slider_fraction(lx)`: panel x to the fraction 0..1 along a slider's knob range.
pub fn slider_fraction(lx: f64) -> f64 {
    py_min(py_max((lx - PANEL_KNOB_LO) / (PANEL_KNOB_HI - PANEL_KNOB_LO), 0.0), 1.0)
}

fn within(r: [f64; 4], lx: f64, ly: f64) -> bool {
    r[0] <= lx && lx < r[0] + r[2] && r[1] <= ly && ly < r[1] + r[3]
}

/// `panel_hit(lx, ly, index, picker=False, resolution_picker=False, background_picker=False)`: what panel point
/// (lx, ly) of desktop `index`'s open panel is on. The header strip is the pill; an open list (`picker`: Scale,
/// `background_picker`, `resolution_picker`) takes the presses on it; then the boxes, the Background switch, the
/// Brightness slider, the desktop-row buttons and the placement sliders (none while the Scale or Resolution list is
/// open); else the panel.
pub fn panel_hit(lx: f64, ly: f64, index: usize, picker: bool, resolution_picker: bool, background_picker: bool) -> Hit {
    if ly < PANEL_HEADER_H {
        return Hit::Pill(index);
    }
    if picker {
        for (i, percent) in SCALE_PERCENTS.iter().enumerate() {
            if within(scale_item_rect(i as i64), lx, ly) {
                return Hit::Scale(index, *percent);
            }
        }
    }
    if background_picker {
        for (i, (name, _)) in BACKGROUNDS.iter().enumerate() {
            if within(background_item_rect(i as i64), lx, ly) {
                return Hit::Background(index, name);
            }
        }
    }
    if resolution_picker {
        for (i, size) in RESOLUTIONS.iter().enumerate() {
            if within(resolution_item_rect(i as i64), lx, ly) {
                return Hit::Resolution(index, *size);
            }
        }
        if within(resolution_list_rect(), lx, ly) {
            return Hit::Panel(index);
        }
    }
    if within(PANEL_RES_BOX, lx, ly) {
        return Hit::ResolutionBox(index);
    }
    if within(PANEL_SCALE_BOX, lx, ly) {
        return Hit::ScaleBox(index);
    }
    if within(PANEL_BACKGROUND_BOX, lx, ly) {
        return Hit::BackgroundBox(index);
    }
    if within(PANEL_BACKGROUND_SWITCH, lx, ly) {
        return Hit::BackgroundSwitch(index);
    }
    let on_knobs = (PANEL_KNOB_LO - PANEL_SLOP_PX..=PANEL_KNOB_HI + PANEL_SLOP_PX).contains(&lx);
    if (PANEL_BRIGHTNESS_TOP..PANEL_BRIGHTNESS_TOP + PANEL_ROW_H).contains(&ly) && on_knobs {
        return Hit::Slider(index, BRIGHTNESS_FIELD, lx);
    }
    for (i, name) in PANEL_ACTIONS.iter().enumerate() {
        if within(action_rect(i as i64), lx, ly) {
            return Hit::Action(index, name);
        }
    }
    let row = if ly >= PANEL_ROWS_TOP { ((ly - PANEL_ROWS_TOP) / PANEL_ROW_H).floor() as i64 } else { -1 };
    if (0..PLACEMENT_KEYS.len() as i64).contains(&row) && on_knobs {
        if picker || resolution_picker {
            return Hit::Panel(index);
        }
        return Hit::Slider(index, PLACEMENT_KEYS[row as usize], lx);
    }
    Hit::Panel(index)
}

// --- what is under the dot ---------------------------------------------------------------------------------------------------

/// `front_order(g, placements)`: desktop indices nearest first (the renderer's draw order reversed; a stable sort).
pub fn front_order(g: &Geometry, placements: &[Pose]) -> Vec<usize> {
    let depth = |i: usize| {
        let t = &g.tiles[i];
        let [x, y, z] = place_point(g, t, &placements[i], t[0] + t[2] / 2.0, t[1] + t[3] / 2.0);
        x * x + y * y + z * z
    };
    let n = g.tiles.len().min(placements.len());
    let mut keyed: Vec<(f64, usize)> = (0..n).map(|i| (depth(i), i)).collect();
    keyed.sort_by(|a, b| a.0.partial_cmp(&b.0).unwrap_or(std::cmp::Ordering::Equal));
    keyed.into_iter().map(|(_, i)| i).collect()
}

/// `hit`'s keyword arguments: the desktop whose pill is open into its panel (`open_`), each desktop's icons
/// (None, 'none' or 'bar') and which of the panel's lists is open.
#[derive(Clone, Copy, Debug, Default)]
pub struct HitOptions<'a> {
    pub open: Option<i64>,
    pub icons: Option<&'a [Option<&'a str>]>,
    pub picker: bool,
    pub resolution_picker: bool,
    pub background_picker: bool,
}

/// `hit(g, placements, pointer, open_=None, icons=None, picker=False, resolution_picker=False,
/// background_picker=False)`: what is under canvas point `pointer` (None: nothing). The open panel first, then each
/// desktop front to back (a corner, the pill, the nearest edge's band, the content), then, where no corner, pill or
/// content is, the level bars and their buttons; an edge's band wins over what lies under it. Tested along the dot's
/// ray, so anything placed anywhere round you can be reached.
pub fn hit(g: Option<&Geometry>, placements: &[Pose], pointer: Option<[f64; 2]>, opts: &HitOptions) -> Option<Hit> {
    let (g, pointer) = (g?, pointer?);
    let n = g.tiles.len().min(placements.len());
    if let Some(open) = opts.open
        && 0 <= open
        && (open as usize) < n
    {
        let open = open as usize;
        let tile = &g.tiles[open];
        let q = desktop_to_glass(tile, &placements[open], unproject(g, tile, &placements[open], pointer));
        if let Some(q) = q {
            let [x, y, w, h] = panel_rect(tile);
            if x <= q[0] && q[0] < x + w && y <= q[1] && q[1] < y + h {
                return Some(panel_hit(
                    q[0] - x,
                    q[1] - y,
                    open,
                    opts.picker,
                    opts.resolution_picker,
                    opts.background_picker,
                ));
            }
        }
    }
    let order = front_order(g, placements);
    let mut qs = vec![None; n];
    for &i in &order {
        qs[i] = unproject(g, &g.tiles[i], &placements[i], pointer);
    }
    let picture = order.iter().copied().find(|&i| qs[i].is_some_and(|q| inside_by(&g.tiles[i], q) >= ENTER_PX));
    let (mut below, mut edge) = (None, None);
    for &i in &order {
        let Some(q) = qs[i] else { continue };
        let tile = &g.tiles[i];
        if let Some(picture) = picture
            && picture != i
            && !in_front_of(g, placements, i, picture, q)
        {
            if !on_drawn_pill(tile, desktop_to_glass(tile, &placements[i], Some(q))) {
                continue;
            }
            below = Some(Hit::Pill(i));
            break;
        }
        let border = border_handle(tile, q);
        match border {
            Some(Handle::Corner(c)) => below = Some(Hit::Corner(i, c)),
            _ if on_pill(tile, &placements[i], Some(q)) => below = Some(Hit::Pill(i)),
            Some(Handle::Edge(e)) => {
                if edge.is_none() {
                    edge = Some(Hit::Edge(i, e));
                }
                continue;
            }
            None if inside_by(tile, q) >= ENTER_PX => below = Some(Hit::Desktop(i, q)),
            None => continue,
        }
        break;
    }
    if below.is_some() {
        return edge.or(below);
    }
    for &i in &order {
        let bar = opts.icons.is_some_and(|icons| i < icons.len() && icons[i] == Some("bar"));
        if !bar {
            continue;
        }
        let tile = &g.tiles[i];
        if maximize_hit(tile, qs[i]) {
            return Some(Hit::Maximize(i));
        }
        if let Some(part) = immersive_part(tile, qs[i]) {
            return Some(Hit::Immersive(i, part));
        }
        if let Some(segment) = level_bar_segment(tile, qs[i]) {
            return Some(Hit::Roll(i, segment));
        }
    }
    edge
}

/// `on_pill(tile, p, q)`: tile pixel `q` (None: missed) of a desktop at placement `p` in its pill zone, the pill
/// padded by PILL_PAD_X / PILL_PAD_Y (6 px to its right) in glass px, plus the gap and border up to the bottom edge.
pub fn on_pill(tile: &Tile, p: &Pose, q: Option<[f64; 2]>) -> bool {
    let Some(q) = q else { return false };
    let [gx, gy] = desktop_to_glass(tile, p, Some(q)).unwrap_or(q);
    let [px, py, pw, ph] = pill_rect(tile);
    px - PILL_PAD_X <= gx
        && gx <= px + pw + py_min(PILL_PAD_X, 6.0)
        && tile[1] + tile[3] <= q[1]
        && gy <= py + ph + PILL_PAD_Y
}

/// `on_drawn_pill(tile, q)`: tile pixel `q` (None: missed) on the pill as drawn, without on_pill's padding.
pub fn on_drawn_pill(tile: &Tile, q: Option<[f64; 2]>) -> bool {
    let Some(q) = q else { return false };
    let [px, py, pw, ph] = pill_rect(tile);
    px <= q[0] && q[0] <= px + pw && py <= q[1] && q[1] <= py + ph
}

/// `in_front_of(g, placements, i, j, q)`: desktop `i` really covers desktop `j`'s picture at i's tile pixel `q`, just
/// outside i: i's own edge next to `q` is on that picture too.
pub fn in_front_of(g: &Geometry, placements: &[Pose], i: usize, j: usize, q: [f64; 2]) -> bool {
    let (tile, other) = (&g.tiles[i], &g.tiles[j]);
    let edge = (
        py_min(py_max(q[0], tile[0]), tile[0] + tile[2]),
        py_min(py_max(q[1], tile[1]), tile[1] + tile[3]),
    );
    let on = unproject(g, other, &placements[j], project(g, tile, &placements[i], edge.0, edge.1));
    on.is_some_and(|on| inside_by(other, on) >= ENTER_PX)
}

/// `inside_by(tile, q)`: how far tile pixel `q` is inside the tile (negative outside).
pub fn inside_by(tile: &Tile, q: [f64; 2]) -> f64 {
    let [tx, ty, tw, th] = *tile;
    py_min(py_min(py_min(q[0] - tx, tx + tw - q[0]), q[1] - ty), ty + th - q[1])
}

/// `resize_distance(start, corner, dx, dy, tile)`: the new `distance` for a resize drag of (dx, dy) canvas px on
/// handle `corner`, a corner along its diagonal and an edge along its normal, clamped to the distance bounds.
pub fn resize_distance(start: f64, corner: &str, dx: f64, dy: f64, tile: &Tile) -> Result<i64> {
    let (vx, vy) = if EDGES.contains(&corner) {
        let vx = match corner {
            "l" => -tile[2] / 2.0,
            "r" => tile[2] / 2.0,
            _ => 0.0,
        };
        let vy = match corner {
            "t" => -tile[3] / 2.0,
            "b" => tile[3] / 2.0,
            _ => 0.0,
        };
        (vx, vy)
    } else if CORNERS.contains(&corner) {
        let b = corner.as_bytes();
        ((if b[1] == b'l' { -1.0 } else { 1.0 }) * tile[2] / 2.0, (if b[0] == b't' { -1.0 } else { 1.0 }) * tile[3] / 2.0)
    } else {
        return Err(Invalid::new("invalid corner"));
    };
    let scale = py_max(MIN_SCALE, 100.0 / start + (dx * vx + dy * vy) / (vx * vx + vy * vy));
    let (lo, hi) = PLACEMENT_BOUNDS[2];
    Ok(py_round(100.0 / scale).max(lo).min(hi))
}

/// `resize_reach(g, point)`: canvas point `point` as a corner drag measures it, unchanged inside the view and each
/// axis RESIZE_OVERSHOOT_GAIN times as far past an edge.
pub fn resize_reach(g: &Geometry, point: [f64; 2]) -> [f64; 2] {
    let axis = |v: f64, size: f64| {
        if v < 0.0 {
            v * RESIZE_OVERSHOOT_GAIN
        } else if v > size {
            size + (v - size) * RESIZE_OVERSHOOT_GAIN
        } else {
            v
        }
    };
    [axis(point[0], g.w as f64), axis(point[1], g.h as f64)]
}

/// `desktop_at(g, placements, pointer)`: the desktop whose rectangle (not its pill or the outer half of its corners) is
/// under canvas point `pointer`, front to back, or None: where a dragged window would land.
pub fn desktop_at(g: Option<&Geometry>, placements: &[Pose], pointer: Option<[f64; 2]>) -> Option<usize> {
    let (g, pointer) = (g?, pointer?);
    front_order(g, placements).into_iter().find(|&i| {
        let tile = &g.tiles[i];
        unproject(g, tile, &placements[i], pointer)
            .is_some_and(|q| tile[0] <= q[0] && q[0] < tile[0] + tile[2] && tile[1] <= q[1] && q[1] < tile[1] + tile[3])
    })
}

/// `ghost_size(window, output, tile, placement)`: (w, h) canvas px of a dragged window's ghost, the window's layout
/// size (`window`: its 'w' / 'h'; it or its monitor `output` None if unknown) as it appears on its desktop times GHOST_SCALE, the longer side
/// within GHOST_MIN_PX..GHOST_MAX_PX, each within 1..MAX_GHOST_PX. GHOST_DEFAULT if any of it is unknown or not
/// finite (the Python's ZeroDivisionError too).
pub fn ghost_size(window: Option<[f64; 2]>, output: Option<&OutputRect>, tile: &Tile, placement: &Pose) -> (i64, i64) {
    let (Some([ww, wh]), Some(output)) = (window, output) else { return GHOST_DEFAULT };
    if output.width == 0.0 || placement.distance == 0.0 {
        return GHOST_DEFAULT;
    }
    let k = tile[2] / output.width * 100.0 / placement.distance * GHOST_SCALE;
    let (w, h) = (ww * k, wh * k);
    if !(w.is_finite() && h.is_finite()) || w <= 0.0 || h <= 0.0 {
        return GHOST_DEFAULT;
    }
    let longer = py_max(w, h);
    let f = py_min(py_max(longer, GHOST_MIN_PX), GHOST_MAX_PX) / longer;
    (py_round(w * f).clamp(1, MAX_GHOST_PX), py_round(h * f).clamp(1, MAX_GHOST_PX))
}

/// `overview_pointer_frame(run_id, session, request_id, pointer)`: the renderer's `overview_pointer` frame. `pointer`
/// is {visible, x, y, held} (bools, and ints within +-MAX_POINTER_PX), with {az, el} while the dot is shown (ints within
/// +-MAX_POINTER_AZ / +-MAX_POINTER_EL), {drag: {w, h, target}} while a window is dragged (1..=MAX_GHOST_PX, and
/// -1..MAX_DESKTOPS) and {look} (POINTER_LOOKS) over a handle or text. The payload is `pointer`, keys in its order.
pub fn overview_pointer_frame(run_id: &str, session: &str, request_id: u64, pointer: &Json) -> Result<String> {
    let bad = || Invalid::new("invalid overview pointer");
    let Some(entries) = pointer.as_object() else { return Err(bad()) };
    let mut keys = vec!["visible", "x", "y", "held"];
    if let Some(look) = pointer.get("look") {
        if !look.as_str().is_some_and(|l| POINTER_LOOKS.contains(&l)) {
            return Err(bad());
        }
        keys.push("look");
    }
    if let Some(drag) = pointer.get("drag") {
        let size_ok = |k: &str| drag.get(k).and_then(Json::as_int).is_some_and(|v| (1..=MAX_GHOST_PX).contains(&v));
        let target_ok = drag.get("target").and_then(Json::as_int).is_some_and(|t| -1 <= t && t < MAX_DESKTOPS as i64);
        if !keys_are(drag, &["w", "h", "target"]) || !size_ok("w") || !size_ok("h") || !target_ok {
            return Err(bad());
        }
        keys.push("drag");
    }
    let mut with_dot = keys.clone();
    with_dot.extend(["az", "el"]);
    if !keys_are(pointer, &keys) && !keys_are(pointer, &with_dot) {
        return Err(bad());
    }
    let int_within = |k: &str, limit: i64| pointer.get(k).and_then(Json::as_int).is_some_and(|v| -limit <= v && v <= limit);
    let ok = pointer.get("visible").and_then(Json::as_bool).is_some()
        && pointer.get("held").and_then(Json::as_bool).is_some()
        && int_within("x", MAX_POINTER_PX)
        && int_within("y", MAX_POINTER_PX)
        && [("az", MAX_POINTER_AZ), ("el", MAX_POINTER_EL)]
            .iter()
            .all(|(k, limit)| pointer.get(k).is_none() || int_within(k, *limit));
    if !ok {
        return Err(bad());
    }
    Ok(view::frame(run_id, session, request_id, "overview_pointer", Json::Object(entries.to_vec())))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_panel_constants_are_the_pythons() {
        assert_eq!(PANEL_RES_TOP, 182.0);
        assert_eq!(PANEL_SCALE_TOP, 216.0);
        assert_eq!(PANEL_BACKGROUND_TOP, 250.0);
        assert_eq!(PANEL_BRIGHTNESS_TOP, 284.0);
        assert_eq!(resolution_list_rect(), [10.0, 46.0, 460.0, 138.0]);
        assert_eq!(resolution_item_rect(2), [10.0, 115.0, 153.33333333333334, 23.0]);
        assert_eq!(resolution_item_rect(12), [0.0; 4]);
    }

    #[test]
    fn a_text_hold_rides_out_the_gap_between_lines() {
        let mut t = TextHold::new();
        assert!(t.step(true, 0.0));
        assert!(!t.step(false, 0.1));
        assert!(t.step(true, 0.12)); // back within TEXT_BOUNCE_S: a sweep
        assert!(t.step(false, 0.2)); // the leave waits
        assert!(!t.step(false, 0.3));
        t.clear();
        assert_eq!(t, TextHold::default());
    }
}
