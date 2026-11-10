//! `free_pointer.OverviewPointer` (tools/native_spike/free_pointer.py lines 1056-2121): the overview pointer's state,
//! hover, drags and renderer frames, on the supervisor loop. The sampler thread hands in cursor samples ([`feed`]) and
//! applies [`want`] (what the cursor and the left button should be doing) with compositor calls.
//!
//! A port method by method; the Python's private methods keep their names without the underscore. Two seams stand in
//! for what the Python reaches through attributes:
//!
//! * [`Control`]: `view.ViewControl` (not ported), the state the pointer reads and writes and every call it makes on
//!   the control, its options menu and its window sender.
//! * the clock: a [`Clock`] (`time.monotonic` in the run, a fake in the tests).
//!
//! The Python's `_lock` (the sampler thread's `feed` against the loop's `poll`) is the caller's: the pointer is shared
//! with the sampler behind one mutex ([`super::sampler::SampledPointer`] takes it per call). What the sender's worker
//! threads hand back (a window lookup's answer) goes through a small [`Inbox`] of its own, since they answer while the
//! pointer may be locked; they wake the loop through the pointer's [`WakeFd`] (`_poke`).
//!
//! `rust/corpus/pointer/traces-*.json` holds the Python's runs and `pointer_trace_tests.rs` replays them.
//!
//! [`feed`]: OverviewPointer::feed
//! [`want`]: OverviewPointer::want

use std::f64::consts::PI;
use std::io;
use std::sync::{Arc, Mutex};

use super::bump::{Num, Patch, Pusher};
use super::edge_pan::PanFrame;
use super::free_pointer::{
    self as fp, GHOST_DEFAULT, HELP_CLEAR_PX, Hit, HitOptions, HelpGeometry, MaximizeGeometry, CLICK_PX, EDGE_PX,
    ENTER_PX, EXIT_PX, HOLD_S, JUMP_PX, LEVEL_SETTLE_S, MAX_POINTER_AZ, MAX_POINTER_EL, MAX_POINTER_PX, MAX_ROLL,
    MONITOR_DRAGS, PANEL_KINDS, RESIZE_DRAGS, RESIZE_LOOKS, TextHold, WINDOW_DRAG_PX,
};
use super::hook::WakeFd;
use super::options_geometry as og;
use super::orbit::{self, Geometry, PillDrag, Pose, Tile};
use super::python::{Invalid, Result, py_hypot, py_round};
use super::restore_band::{self, Dwell};
use super::sampler::{Clock, Mode, Want};
use super::view::{self, ViewState};
use crate::geometry::{MAX_DESKTOPS, Placement};
use crate::pyjson::{Json, float_repr};
use crate::resize::{CORNERS, OutputRect};

/// view.OVERLAY_IDLE_CLOSE_S: the pills, bars, caps and dot hide after this long without input (OLED idle close).
pub const OVERLAY_IDLE_CLOSE_S: f64 = super::keybinds::IDLE_CLOSE_S;
/// view.MENU_SAVE_DELAY: seconds after the last panel change (key, slider or pill drag) before the profile is written.
pub const MENU_SAVE_DELAY: f64 = 0.5;
/// resize.RESIZE_OPS: the left button's two ops.
pub const RESIZE_OPS: [&str; 2] = ["resize_press", "resize_release"];
/// options_menu.CATEGORIES: the options menu's tabs, in order (options_menu.py is not ported).
pub const OPTIONS_CATEGORIES: [&str; 11] =
    ["3d", "stabilization", "layout", "view", "video", "input", "keybinds", "windows", "privacy", "help", "session"];

/// options_menu.next_page: the first tab of the page after `category`'s, the last page leading back to the first.
pub fn options_next_page(category: &str) -> Option<&'static str> {
    let i = OPTIONS_CATEGORIES.iter().position(|c| *c == category)? as i64;
    let start = (i / og::TABS_PER_PAGE + 1) * og::TABS_PER_PAGE;
    Some(OPTIONS_CATEGORIES[if (start as usize) < OPTIONS_CATEGORIES.len() { start as usize } else { 0 }])
}

/// What a window sender's lookup hands back (on its worker thread): `send.window_at`'s dict, or None.
pub type Found = Box<dyn FnOnce(Json) + Send>;

/// The control the pointer drives (`view.ViewControl`, not ported): its state and attributes, and the calls the
/// pointer makes on it, its options menu (`control.options`) and its window sender (`control.sender`). Frames are the
/// renderer's bytes. Where the Python reads an attribute with `getattr(control, name, default)` the method has that
/// default.
pub trait Control {
    fn state(&self) -> &ViewState;
    fn state_mut(&mut self) -> &mut ViewState;
    fn video_active(&self) -> bool {
        false
    }
    fn keys_open(&self) -> bool {
        false
    }
    fn help_visible(&self) -> bool {
        false
    }
    /// `reloads`: config reloads seen.
    fn reloads(&self) -> i64;
    fn run_id(&self) -> &str;
    fn session(&self) -> &str;
    /// `next_id`: the next frame's request id.
    fn next_id(&self) -> u64;
    fn set_next_id(&mut self, id: u64);
    /// `save_due = at`: the profile is written then.
    fn set_save_due(&mut self, at: f64);
    /// `last_input = at`: an open panel stays up.
    fn set_last_input(&mut self, at: f64);
    /// `clock()`: the control's own clock.
    fn clock(&mut self) -> f64;
    /// `_placement_frame()`, `_menu_frame()`, `_frame()` (the view), `follow_focus()`, `flush()` (save now).
    fn placement_frame(&mut self) -> Vec<u8>;
    fn menu_frame(&mut self) -> Vec<u8>;
    fn frame(&mut self) -> Vec<u8>;
    fn follow_focus(&mut self) -> Vec<u8>;
    fn flush(&mut self);
    /// `select_desktop(i)`, with `panned=` only when given.
    fn select_desktop(&mut self, i: usize, panned: Option<bool>) -> Vec<u8>;
    /// `switch_desktop(i, panned=panned)`.
    fn switch_desktop(&mut self, i: usize, panned: bool) -> Vec<u8>;
    fn pill_click(&mut self, i: usize) -> Vec<u8>;
    fn pill_release(&mut self) -> Vec<u8>;
    fn pill_close(&mut self) -> Vec<u8>;
    /// `pill_slide(i, field, fraction)`, with `surface=` (the press-time wire placement) only on the press.
    fn pill_slide(&mut self, i: usize, field: &str, fraction: f64, surface: Option<&Placement>) -> Vec<u8>;
    fn panel_keys_on(&mut self);
    fn desktop_press(&mut self) -> Vec<u8>;
    /// `immersive_pointer(i, tail)` (`immersive_pointer(-1)`: `tail` None); a control without 3D sends nothing.
    fn immersive_pointer(&mut self, _i: i64, _tail: Option<bool>) -> Vec<u8> {
        Vec::new()
    }
    fn immersive_click(&mut self, i: usize) -> Vec<u8>;
    fn immersive_tail_click(&mut self, i: usize) -> Vec<u8>;
    fn desktop_action(&mut self, i: usize, name: &str) -> Vec<u8>;
    fn help_turn(&mut self, turn: i64) -> Vec<u8>;
    fn maximize_monitor(&mut self, name: &str) -> Vec<u8>;
    fn scale_close(&mut self) -> Vec<u8>;
    fn resolution_close(&mut self) -> Vec<u8>;
    fn background_close(&mut self) -> Vec<u8>;
    fn scale_box(&mut self, i: usize) -> Vec<u8>;
    fn scale_pick(&mut self, i: usize, percent: i64) -> Vec<u8>;
    fn resolution_box(&mut self, i: usize) -> Vec<u8>;
    fn resolution_pick(&mut self, i: usize, size: (i64, i64)) -> Vec<u8>;
    fn background_box(&mut self, i: usize) -> Vec<u8>;
    fn background_pick(&mut self, i: usize, name: &str) -> Vec<u8>;
    fn background_switch(&mut self, i: usize) -> Vec<u8>;
    /// `pan_frame(payload)`: the unified cursor's edge pan (None ends it).
    fn pan_frame(&mut self, frame: Option<&PanFrame>) -> Vec<u8>;

    /// `control.options` is there (`getattr(control, 'options', None) is not None`).
    fn has_options(&self) -> bool {
        false
    }
    /// `options.snapshot()`: the menu's page (None while closed).
    fn options_snapshot(&mut self) -> Json {
        Json::Null
    }
    /// `options.visible is True`.
    fn options_visible(&self) -> bool {
        false
    }
    /// `options.capture`: the held range drag, as the pointer handed it in (None: none).
    fn options_capture(&self) -> Option<Json> {
        None
    }
    fn set_options_capture(&mut self, _capture: Option<Json>) {}
    /// `options.preview`: (row id, value text) or None.
    fn options_preview(&self) -> Json {
        Json::Null
    }
    fn set_options_preview(&mut self, _preview: Json) {}
    fn set_options_focused(&mut self, _focused: bool) {}
    /// `options.keyboard_owned is True`.
    fn options_keyboard_owned(&self) -> bool {
        false
    }
    fn options_touch(&mut self) {}
    fn options_refresh(&mut self) {}
    fn options_desktop_press(&mut self) {}
    /// `control.options_intent(name, **kwargs)`.
    fn options_intent(&mut self, _name: &str, _kwargs: Vec<(&'static str, Json)>) -> Vec<u8> {
        Vec::new()
    }
    /// `control._options_frame()`, `control.options_activate(row)`.
    fn options_frame(&mut self) -> Vec<u8> {
        Vec::new()
    }
    fn options_activate(&mut self, _row: i64) -> Vec<u8> {
        Vec::new()
    }

    /// `control.sender` is there (send.WindowSender: window moves, off the loop).
    fn has_sender(&self) -> bool {
        false
    }
    /// `sender.lookup(monitor, point, found)`: which window is shown at layout `point` on `monitor`.
    fn sender_lookup(&mut self, _monitor: &str, _point: (i64, i64), _found: Found) {}
    /// `sender.under_cursor(found)`: the window under the cursor.
    fn sender_under_cursor(&mut self, _found: Found) {}
    /// `sender.land(address, monitor)`: a Super-moved window lands on `monitor` now.
    fn sender_land(&mut self, _address: &str, _monitor: &str) {}
    /// `sender.drop(window, from, point, to)`: a lifted window onto another desktop.
    fn sender_drop(&mut self, _window: &Json, _from: &str, _point: Option<(i64, i64)>, _to: &str) {}
}

/// What the sender's worker threads hand the loop (the Python's fields under `_lock`).
#[derive(Debug, Default)]
pub struct Inbox {
    /// `_found`: (token, window) from a lookup, taken on the loop (`take_found`).
    pub found: Option<(u64, Json)>,
    /// The unified pointer's `super_token` and `super_window`: the Super drag's window, by `under_cursor`.
    pub super_token: u64,
    pub super_window: Option<Json>,
}

/// A window drag's window: None looking up, False none there, else `send.window_at`'s dict.
#[derive(Clone, Debug, PartialEq)]
pub enum Window {
    Pending,
    Gone,
    Found(Json),
}

/// `drag`, the Python's dict by kind.
#[derive(Clone, Debug)]
#[allow(clippy::large_enum_variant)] // one drag at a time, held in place
pub enum Drag {
    /// handle: a corner or an edge; `bump`: one Pusher for the whole drag.
    Resize { index: usize, handle: &'static str, start: [f64; 2], distance: i64, tile: Tile, bump: Option<Pusher> },
    /// moved: past CLICK_PX, so not a click; open_at_press: the open panel's desktop or None after the press, a click
    /// needs it unchanged; around: the dot's reach the drag keeps to its end.
    Pill {
        index: usize,
        start: [f64; 2],
        xy: (i64, i64),
        tile: Tile,
        moved: bool,
        open_at_press: Option<usize>,
        around: bool,
        orbit: Option<PillDrag>,
    },
    /// placement: the desktop's at the press, the surface both the drag and the renderer's panel use until the
    /// release.
    Slider { index: usize, field: &'static str, tile: Tile, placement: Placement },
    /// side: the bar's segment, 'level' a button that a drag cancels; from: its roll at the press, centre: the
    /// desktop's centre on the canvas, what the dot turns it round.
    Roll { index: usize, side: &'static str, start: [f64; 2], from: i64, centre: [f64; 2], moved: bool },
    /// on: tile px pressed, where a click lands; at: press time; point: layout px pressed on the desktop's monitor;
    /// target: desktop under the dot.
    Window {
        index: usize,
        on: [f64; 2],
        start: [f64; 2],
        at: f64,
        point: Option<(i64, i64)>,
        lifted: bool,
        window: Window,
        target: Option<usize>,
        token: Option<u64>,
    },
    Maximize { monitor: String, index: usize, start: [f64; 2] },
    /// A held options slider: `capture` is the dict the menu holds (`menu.capture is d`).
    OptionsRange { index: usize, tile: Tile, placement: Pose, row: Json, range: og::RangeRow, monitor: Json, scope: Json, capture: Json },
}

impl Drag {
    pub fn kind(&self) -> &'static str {
        match self {
            Drag::Resize { .. } => "resize",
            Drag::Pill { .. } => "pill",
            Drag::Slider { .. } => "slider",
            Drag::Roll { .. } => "roll",
            Drag::Window { .. } => "window",
            Drag::Maximize { .. } => "maximize",
            Drag::OptionsRange { .. } => "options_range",
        }
    }

    pub fn index(&self) -> usize {
        match *self {
            Drag::Resize { index, .. }
            | Drag::Pill { index, .. }
            | Drag::Slider { index, .. }
            | Drag::Roll { index, .. }
            | Drag::Window { index, .. }
            | Drag::Maximize { index, .. }
            | Drag::OptionsRange { index, .. } => index,
        }
    }
}

fn obj(entries: Vec<(&str, Json)>) -> Json {
    Json::Object(entries.into_iter().map(|(k, v)| (k.to_string(), v)).collect())
}

fn json_get<'a>(j: &'a Json, k: &str) -> &'a Json {
    j.get(k).unwrap_or(&Json::Null)
}

fn num(j: &Json) -> Option<Num> {
    match *j {
        Json::Int(v) => Some(Num::Int(v)),
        Json::Float(v) => Some(Num::Float(v)),
        _ => None,
    }
}

fn num_json(v: Num) -> Json {
    match v {
        Num::Int(v) => Json::Int(v),
        Num::Float(v) => Json::Float(v),
    }
}

/// Python's `str` of a number.
fn num_str(v: Num) -> String {
    match v {
        Num::Int(v) => v.to_string(),
        Num::Float(v) => float_repr(v),
    }
}

fn pose_json(p: &Pose) -> Json {
    obj(vec![
        ("x", Json::Float(p.x)),
        ("y", Json::Float(p.y)),
        ("distance", Json::Float(p.distance)),
        ("curvature", Json::Float(p.curvature)),
        ("roll", Json::Float(p.roll)),
    ])
}

fn json_pose(j: &Json) -> Option<Pose> {
    let f = |k: &str| num(j.get(k)?).map(Num::get);
    Some(Pose { x: f("x")?, y: f("y")?, distance: f("distance")?, curvature: f("curvature")?, roll: f("roll").unwrap_or(0.0) })
}

/// The Restore geometry without what follows the head (`view`, `picture`).
fn structural_eq(a: Option<&MaximizeGeometry>, b: Option<&MaximizeGeometry>) -> bool {
    match (a, b) {
        (None, None) => true,
        (Some(a), Some(b)) => {
            a.monitor == b.monitor
                && a.canvas == b.canvas
                && a.tile == b.tile
                && a.placement == b.placement
                && a.rect == b.rect
                && a.hit_mesh == b.hit_mesh
        }
        _ => false,
    }
}

fn clamp_i(v: i64, limit: i64) -> i64 {
    v.clamp(-limit, limit)
}

/// `round(math.degrees(a) * 100)` within +-limit.
fn hundredths(a: f64, limit: i64) -> i64 {
    clamp_i(py_round(a.to_degrees() * 100.0), limit)
}

/// The overview pointer (`OverviewPointer`). Fields keep the Python's names, the private ones without the underscore.
pub struct OverviewPointer<C: Control> {
    pub control: C,
    pub(crate) clock: Box<dyn Clock + Send>,
    pub(crate) inbox: Arc<Mutex<Inbox>>,
    pub(crate) wake: Arc<WakeFd>,
    /// Window drag (module docstring): off, so a press, hold or drag on a desktop always stays its window's
    /// (anchor-no-click-maximize). True brings back the press-and-hold lift onto another desktop.
    pub window_drag: bool,
    // The sampler's side (`_abs`, `_motion`, `_applied`, `_outputs`, `_fresh`).
    pub(crate) abs: Option<(i64, i64)>,
    pub(crate) motion: [f64; 2],
    pub(crate) applied: i64,
    pub(crate) fed_outputs: Vec<OutputRect>,
    pub(crate) fresh: bool,
    /// The newest word: the app under the pointer asks for a text cursor (feed_shape, push_shape).
    pub(crate) shape_text: bool,
    /// Counts pushes: a read that a push overtook says nothing (shape_serial, feed_shape).
    pub(crate) shape_serial: i64,
    /// What the last push said, whatever the gate did with it.
    pub(crate) push_text: bool,
    /// The sampler has a broker read: a run without one takes no push.
    pub shape_wired: bool,
    /// Pushes arrive and the reads agree with them, so the sampler only checks (SHAPE_CHECK_S).
    pub shape_pushed: bool,
    /// The beam's gate shut on a word: when it opens the sampler reads at SHAPE_S (step_text).
    pub shape_gated: bool,
    /// The Serif Beam is on (step_text, look).
    pub text_hold: TextHold,
    pub maximize_geometry: Option<MaximizeGeometry>,
    pub(crate) restore_output: Option<OutputRect>,
    pub(crate) restore_press: Option<(String, u64)>,
    pub(crate) restore_epoch: u64,
    pub dwell: Dwell,
    /// The dot while the band is open: canvas px.
    pub band_point: Option<[f64; 2]>,
    pub band_gain: f64,
    pub band_moved: f64,
    /// The renderer's band open.
    pub band_sent: bool,
    /// Maximized: when the left button went down (the click binds), None: it is up.
    pub zoom_held: Option<f64>,
    /// parse_overview_geometry, from the renderer.
    pub geometry: Option<Geometry>,
    /// The desktops changed since it came (layout_ready).
    pub layout_stale: bool,
    /// parse_help_geometry, from the renderer: the help card while it is up in the overview.
    pub help_geometry: Option<HelpGeometry>,
    /// resize.outputs_from_monitors, from the sampler.
    pub outputs: Vec<OutputRect>,
    /// off (not in the overview), away (cursor not on a desktop), desktop, free (and the unified cursor's carry).
    pub mode: Mode,
    /// Desktop index the cursor is on (desktop mode) or left from (free mode).
    pub desktop: Option<usize>,
    /// (x, y) canvas px.
    pub pointer: Option<[f64; 2]>,
    /// Canvas px per cursor px in free mode (the desktop's own scale when it was left).
    pub gain: f64,
    /// hit(): what a click would grab (free mode only).
    pub hover: Option<Hit>,
    pub drag: Option<Drag>,
    /// parse_roll_geometry, from the renderer: each desktop's tilt from level, or None.
    pub tilts: Vec<Option<i64>>,
    /// Each desktop's levelling roll, 1/100 degree (this run only).
    pub rolls: Vec<i64>,
    /// Whose roll each entry is (desktops_changed keeps them by name).
    pub roll_names: Vec<String>,
    pub roll_sent: Json,
    /// Desktop name -> until when its tilt reports level it again (R: level), in insertion order.
    pub levelling: Vec<(String, f64)>,
    pub last_move: f64,
    pub shown: Json,
    pub pills: Json,
    pub sent: Json,
    /// (serial, kind, layout point or None); read by the sampler.
    pub want: Want,
    /// Cursor samples count in desktop mode only once the sampler applied this serial.
    pub sync: i64,
    /// Ctrl+Alt+Down: bring a cursor that is off the desktops into the selected one.
    pub summoned: bool,
    /// A desktop change: summon the cursor once the pointer is back (desktops_changed).
    pub rehome: bool,
    /// Config reload: runtime binds and cursor state are gone (sampler redoes them).
    pub binds_dropped: bool,
    pub(crate) token: u64,
    // Diagnostics.
    pub roll_clicks: u64,
    pub roll_drags: u64,
    pub roll_levels: u64,
    pub entries: u64,
    pub exits: u64,
    pub drags: u64,
    pub moves: u64,
    pub focus_clicks: u64,
    pub pill_clicks: u64,
    pub slides: u64,
    pub outside_closes: u64,
    pub stale_clicks: u64,
    pub help_clicks: u64,
    /// Desktop-row button presses.
    pub actions: u64,
    /// Drag updates that pushed another desktop out of the way (bump.py).
    pub bumps: u64,
    /// Monitor drags stopped by Super going down (super_down).
    pub super_stops: u64,
    pub window_lifts: u64,
    pub window_drops: u64,
    pub window_cancels: u64,
    /// The unified cursor's state ([`super::unified_pointer`]); None: a plain OverviewPointer.
    pub(crate) ext: Option<Box<super::unified_pointer::Unified>>,
}

impl<C: Control> OverviewPointer<C> {
    /// `OverviewPointer(control, clock)`.
    pub fn new(control: C, mut clock: Box<dyn Clock + Send>) -> io::Result<Self> {
        let names = control.state().names.clone();
        let last_move = clock.now();
        let mut roll_sent = fp::roll_hidden();
        if let Json::Object(o) = &mut roll_sent {
            for (k, v) in o.iter_mut() {
                match k.as_str() {
                    "roll" => *v = Json::Array(vec![Json::Int(0); MAX_DESKTOPS]),
                    "icons" => *v = Json::Array(vec![Json::Str("none".into()); MAX_DESKTOPS]),
                    _ => {}
                }
            }
        }
        Ok(OverviewPointer {
            control,
            clock,
            inbox: Arc::new(Mutex::new(Inbox::default())),
            wake: Arc::new(WakeFd::new()?),
            window_drag: false,
            abs: None,
            motion: [0.0, 0.0],
            applied: 0,
            fed_outputs: Vec::new(),
            fresh: false,
            shape_text: false,
            shape_serial: 0,
            push_text: false,
            shape_wired: false,
            shape_pushed: false,
            shape_gated: false,
            text_hold: TextHold::new(),
            maximize_geometry: None,
            restore_output: None,
            restore_press: None,
            restore_epoch: 0,
            dwell: Dwell::new(),
            band_point: None,
            band_gain: 1.0,
            band_moved: 0.0,
            band_sent: false,
            zoom_held: None,
            geometry: None,
            layout_stale: false,
            help_geometry: None,
            outputs: Vec::new(),
            mode: Mode::Off,
            desktop: None,
            pointer: None,
            gain: 1.0,
            hover: None,
            drag: None,
            tilts: Vec::new(),
            rolls: vec![0; MAX_DESKTOPS],
            roll_names: names,
            roll_sent,
            levelling: Vec::new(),
            last_move,
            shown: fp::handle_hidden(),
            pills: fp::pills_hidden(),
            sent: fp::pointer_hidden(),
            want: Want { serial: 0, kind: Mode::Off, point: None },
            sync: 0,
            summoned: false,
            rehome: false,
            binds_dropped: false,
            token: 0,
            roll_clicks: 0,
            roll_drags: 0,
            roll_levels: 0,
            entries: 0,
            exits: 0,
            drags: 0,
            moves: 0,
            focus_clicks: 0,
            pill_clicks: 0,
            slides: 0,
            outside_closes: 0,
            stale_clicks: 0,
            help_clicks: 0,
            actions: 0,
            bumps: 0,
            super_stops: 0,
            window_lifts: 0,
            window_drops: 0,
            window_cancels: 0,
            ext: None,
        })
    }

    /// The fd that wakes the loop (`wake_reader`): any change the next poll should see.
    pub fn wake(&self) -> &Arc<WakeFd> {
        &self.wake
    }

    /// `close()`: the wake fd closes with the pointer.
    pub fn close(&mut self) {}

    pub(crate) fn now(&mut self) -> f64 {
        self.clock.now()
    }

    /// `overview()`: in the overview with nothing else owning the mouse: not zoomed, no VR video, not in typing mode
    /// (view.py, Ctrl+Alt+T: every run bind but the toggle is released, the left button and cursor too, and the pills
    /// hide), and no key bind panel (keybinds.py: its submap leaves the pointer's button binds inactive, so the cursor is
    /// given back). An open placement panel keeps it on: the pointer clicks its pill and drags its sliders.
    pub fn overview(&self) -> bool {
        if self.ext.is_some() {
            return self.u_overview();
        }
        self.overview_base()
    }

    pub(crate) fn overview_base(&self) -> bool {
        let s = self.control.state();
        !s.zoomed && !self.control.video_active() && !s.typing && !self.control.keys_open()
    }

    /// `restore_sampling()`: read-only cursor sampling at zoom; no grab until the exact UI mesh is hit.
    pub fn restore_sampling(&self) -> bool {
        let s = self.control.state();
        let Some(g) = &self.maximize_geometry else { return false };
        s.zoomed
            && !s.typing
            && !self.control.video_active()
            && !self.control.keys_open()
            && !self.ext.as_ref().is_some_and(|u| u.super_drag.is_some())
            && s.selected < s.names.len()
            && s.names[s.selected] == g.monitor
    }

    /// `set_maximize_geometry(geometry)`. Parse first: malformed datagrams cannot replace good geometry or acquire input.
    pub fn set_maximize_geometry(&mut self, geometry: &Json) -> Result<()> {
        let parsed = fp::parse_maximize_geometry(geometry)?;
        // `view` and `picture` follow the head: they are no new geometry.
        if !structural_eq(parsed.as_ref(), self.maximize_geometry.as_ref()) {
            self.restore_epoch += 1;
            self.restore_press = None;
        }
        let none = parsed.is_none();
        self.maximize_geometry = parsed;
        if none && self.mode == Mode::Restore {
            self.restore_release();
        }
        self.poke();
        Ok(())
    }

    /// `_restore_release()`: the band shuts: the app gets its cursor back on the column the dot was on, LAND_PX under
    /// the edge, and the rest starts over.
    pub fn restore_release(&mut self) {
        self.restore_press = None;
        let (output, point) = (self.restore_output, self.band_point);
        let mut target = None;
        let land = restore_band::LAND_PX as f64;
        if let (Some(o), Some(point), Some(g)) = (output, point, &self.maximize_geometry) {
            let [x0, _, x1, _] = g.picture;
            #[allow(clippy::manual_clamp)] // the Python's max(0, min(1, ...))
            let along = if x1 > x0 { ((point[0] - x0) / (x1 - x0)).max(0.0).min(1.0) } else { 0.5 };
            target = Some(((o.x as f64 + along * (o.width - 1.0)) as i64, (o.y as f64 + land) as i64));
        } else if let Some(o) = output {
            target = Some(((o.x as f64 + o.width / 2.0) as i64, (o.y as f64 + land) as i64));
        }
        self.band_point = None;
        self.dwell.disarm();
        if self.mode == Mode::Restore {
            self.mode = Mode::Off;
            self.set_want(Mode::Desktop, target, true);
        }
    }

    /// `band_hot()`: the dot on Restore's own mesh.
    pub fn band_hot(&self) -> bool {
        match (&self.maximize_geometry, self.band_point) {
            (Some(g), Some(p)) => fp::restore_mesh_hit(g, Some(fp::band_ray(g, p[0], p[1]))),
            _ => false,
        }
    }

    /// `_restore_frame()`: the dot while the band is open, and the band's own frame on any change.
    fn restore_frame(&mut self) -> Vec<u8> {
        let mut out = Vec::new();
        let is_open = self.mode == Mode::Restore;
        if is_open != self.band_sent {
            self.band_sent = is_open;
            out.extend(self.send(|r, s, id| Ok(fp::restore_band_frame(r, s, id, is_open))));
        }
        let ray = match (&self.maximize_geometry, self.band_point) {
            (Some(g), Some(p)) if is_open => Some(fp::band_ray(g, p[0], p[1])),
            _ => None,
        };
        let visible = ray.is_some();
        let mut want = vec![
            ("visible", Json::Bool(visible)),
            ("x", Json::Int(0)),
            ("y", Json::Int(0)),
            ("held", Json::Bool(visible && self.restore_press.is_some())),
        ];
        if let Some(ray) = ray {
            let az = ray[0].atan2(-ray[2]);
            let el = ray[1].atan2(py_hypot(ray[0], ray[2]));
            want.push(("az", Json::Int(hundredths(az, MAX_POINTER_AZ))));
            want.push(("el", Json::Int(hundredths(el, MAX_POINTER_EL))));
        }
        let want = obj(want);
        if want == self.sent {
            return out;
        }
        self.sent = want.clone();
        out.extend(self.send(|r, s, id| fp::overview_pointer_frame(r, s, id, &want)));
        out
    }

    /// `_band_blocked(now)`: what a rest does not count through: a held button (a lost release expires), and a hand's
    /// edge pan.
    fn band_blocked(&mut self, now: f64) -> bool {
        if self.zoom_held.is_some_and(|t| now - t >= restore_band::HELD_MAX_S as f64) {
            self.zoom_held = None;
        }
        self.zoom_held.is_some() || self.ext.as_ref().is_some_and(|u| u.pan.is_some())
    }

    /// `_band_open(cursor, output, now)`: the rest is up: the dot takes over where the cursor was, on the picture's top
    /// edge as it ends up stepped back.
    fn band_open(&mut self, cursor: (i64, i64), output: OutputRect, now: f64) {
        let Some(g) = &self.maximize_geometry else { return };
        let [x0, y0, x1, _] = g.picture;
        self.band_gain = if output.width != 0.0 { (x1 - x0) / output.width } else { 1.0 };
        self.band_point = Some([x0 + (cursor.0 - output.x) as f64 * self.band_gain, y0]);
        self.band_moved = now;
        self.restore_output = Some(output);
        self.mode = Mode::Restore;
        let middle = ((output.x as f64 + output.width / 2.0) as i64, (output.y as f64 + output.height / 2.0) as i64);
        self.set_want(Mode::Free, Some(middle), true);
    }

    fn poll_restore(&mut self, cursor: Option<(i64, i64)>, motion: [f64; 2], applied: i64, now: f64) -> Vec<u8> {
        let Some(g) = self.maximize_geometry.clone() else { return Vec::new() };
        let names = &self.control.state().names;
        let output =
            self.outputs.iter().find(|o| o.index < names.len() && names[o.index] == g.monitor).copied();
        let Some(output) = output else {
            self.dwell.reset();
            if self.mode == Mode::Restore {
                self.restore_release();
            }
            return self.restore_frame();
        };
        if self.mode != Mode::Restore {
            let mut out = Vec::new();
            if self.mode != Mode::Off {
                // maximized from free or desktop mode: what active() going false does, as this path comes first
                out.extend(self.end_drag());
                (self.mode, self.hover, self.pointer, self.summoned) = (Mode::Off, None, None, false);
                self.set_want(Mode::Off, None, false);
            }
            if applied >= self.sync {
                let blocked = self.band_blocked(now);
                if self.dwell.step(cursor, Some(&output), blocked, now)
                    && let Some(cursor) = cursor
                {
                    self.band_open(cursor, output, now);
                }
            }
            out.extend(self.restore_frame());
            return out;
        }
        if applied >= self.sync && (motion[0] != 0.0 || motion[1] != 0.0) {
            if let Some([x, y]) = self.band_point {
                let (w, h) = (g.canvas[0], g.canvas[1]);
                self.band_point = Some([
                    (x + motion[0] * self.band_gain).max(2.0).min(w - 2.0),
                    (y + motion[1] * self.band_gain).max(2.0).min(h - 2.0),
                ]);
            }
            self.band_moved = now;
        }
        if let Some([x, y]) = self.band_point {
            let [x0, y0, x1, y1] = g.picture;
            let back = x0 <= x && x < x1 && y0 + restore_band::RETURN_PX as f64 <= y && y < y1;
            if (back && self.restore_press.is_none()) || now - self.band_moved >= restore_band::IDLE_S as f64 {
                self.restore_release();
            }
        }
        self.restore_frame()
    }

    /// `_restore_op(op)`: the left button with the band open, consumed by the grab. A press on Restore is Restore's; a
    /// press anywhere else shuts it.
    fn restore_op(&mut self, op: &str) -> Option<Vec<u8>> {
        if op == "resize_press" {
            if self.mode == Mode::Restore && self.restore_sampling() {
                if self.band_hot() {
                    let monitor = self.maximize_geometry.as_ref().map(|g| g.monitor.clone()).unwrap_or_default();
                    self.restore_press = Some((monitor, self.restore_epoch));
                } else {
                    self.restore_release();
                }
            }
            return Some(self.restore_frame());
        }
        let pressed = self.restore_press.take();
        let valid = self.restore_sampling()
            && pressed.as_ref().is_some_and(|(m, e)| {
                self.maximize_geometry.as_ref().is_some_and(|g| &g.monitor == m) && *e == self.restore_epoch
            })
            && self.band_hot();
        if self.mode == Mode::Restore && pressed.is_some() && !valid {
            self.restore_release();
        }
        if !valid {
            return Some(self.restore_frame());
        }
        self.restore_release();
        let mut out = self.restore_frame();
        out.extend(self.control.maximize_monitor(&pressed.expect("valid").0));
        Some(out)
    }

    /// `active()`.
    pub fn active(&self) -> bool {
        self.overview() && self.layout_ready() && !self.outputs.is_empty()
    }

    /// `_handles_on()`: the pills, level bars and corner caps show (idle aside): whenever the pointer is on.
    pub fn handles_on(&self) -> bool {
        if self.ext.is_some() {
            return self.u_handles_on();
        }
        self.active()
    }

    /// `_layout_ready()`: a renderer layout to hit-test with. After a desktop change the last one is kept, and used once
    /// it has one slot per desktop (until the renderer's next report, which replaces it).
    fn layout_ready(&self) -> bool {
        let Some(g) = &self.geometry else { return false };
        !self.layout_stale || g.tiles.len() == self.control.state().names.len()
    }

    // --- inputs ---------------------------------------------------------------------------------------------------

    pub fn set_geometry(&mut self, geometry: Option<Geometry>) {
        (self.geometry, self.layout_stale) = (geometry, false);
        self.poke();
    }

    pub fn set_help_geometry(&mut self, geometry: Option<HelpGeometry>) {
        self.help_geometry = geometry;
        self.poke();
    }

    /// `set_roll_geometry(tilts)`: the renderer's tilts (parse_roll_geometry). A desktop R just levelled (level) is
    /// levelled again on a measured one until LEVEL_SETTLE_S after the key.
    pub fn set_roll_geometry(&mut self, tilts: Vec<Option<i64>>) {
        self.tilts = tilts;
        let now = self.now();
        self.levelling.retain(|(_, until)| now <= *until);
        let names = self.control.state().names.clone();
        for (i, name) in names.iter().take(MAX_DESKTOPS).enumerate() {
            if self.levelling.iter().any(|(n, _)| n == name)
                && let Some(Some(tilt)) = self.tilts.get(i)
            {
                self.rolls[i] = fp::level_roll(Some(*tilt));
            }
        }
        self.poke();
    }

    /// `level(i)`: R on desktop i's open panel (view.ViewControl._on_menu, menu_reset) as its placement goes back flat
    /// in its slot, and a click on its level bar's middle segment, which moves nothing else: its rotation goes back to
    /// level (level_roll): no levelling roll, or, where its tilt from level is measured, the roll that cancels it. A
    /// reset may move the desktop, and with it its tilt, which the renderer reports only when it changes, so its
    /// measured tilts in the next LEVEL_SETTLE_S level it again where it went (set_roll_geometry). A bar drag on it
    /// ends. Returns the `roll` frame (empty if nothing changed).
    pub fn level(&mut self, i: i64) -> Vec<u8> {
        let names = &self.control.state().names;
        if !(0 <= i && (i as usize) < names.len().min(MAX_DESKTOPS)) {
            return Vec::new();
        }
        let i = i as usize;
        let name = names[i].clone();
        if matches!(&self.drag, Some(Drag::Roll { index, .. }) if *index == i) {
            self.drag = None;
        }
        self.rolls[i] = fp::level_roll(self.tilt(i));
        let now = self.now();
        let until = now + LEVEL_SETTLE_S;
        match self.levelling.iter_mut().find(|(n, _)| *n == name) {
            Some(entry) => entry.1 = until,
            None => self.levelling.push((name, until)),
        }
        self.roll_levels += 1;
        let idle = now - self.last_move >= OVERLAY_IDLE_CLOSE_S;
        self.roll_frame(idle)
    }

    pub fn summon(&mut self) {
        self.summoned = true;
    }

    /// `desktops_changed()`: the run's desktops changed (a pill's Add / Remove / Ultrawide): every index may mean another
    /// desktop now. Any drag ends where it is, and hit testing waits for the sampler's new monitors and a renderer layout
    /// with one slot per desktop (layout_ready). The last layout and tilts are kept, never dropped: the renderer reports
    /// them only when they change, so a dropped one may never come again (dot-cursor-new-desktop). Once the pointer is
    /// back it brings the cursor onto the selected desktop (rehome), wherever the change left it.
    pub fn desktops_changed(&mut self) {
        (self.drag, self.hover) = (None, None);
        let _ = self.set_maximize_geometry(&Json::Null); // no capture/press survives output identity changes
        self.layout_stale = true;
        let names = self.control.state().names.clone(); // each desktop keeps its own levelling roll and last measured tilt
        let roll_of = |n: &String| self.roll_names.iter().position(|m| m == n).and_then(|k| self.rolls.get(k)).copied();
        let mut rolls: Vec<i64> = names.iter().map(|n| roll_of(n).unwrap_or(0)).collect();
        rolls.resize(rolls.len().max(MAX_DESKTOPS), 0);
        rolls.truncate(names.len().max(MAX_DESKTOPS));
        // ([0] * (MAX_DESKTOPS - len(names)): more names than that leaves the list as long as they are.)
        let tilt_of = |n: &String| {
            // dict(zip(...)): the last of a repeated name wins.
            self.roll_names.iter().zip(&self.tilts).rev().find(|(m, _)| *m == n).and_then(|(_, t)| *t)
        };
        self.tilts =
            if self.tilts.len() == self.roll_names.len() { names.iter().map(tilt_of).collect() } else { Vec::new() };
        self.rolls = rolls;
        self.roll_names = names;
        self.outputs = Vec::new();
        self.fed_outputs = Vec::new();
        if self.desktop.is_some_and(|d| d >= self.control.state().names.len()) {
            self.desktop = None;
        }
        if self.mode != Mode::Off {
            // back in through away, as on entering the overview: the cursor is placed afresh
            (self.mode, self.hover, self.pointer) = (Mode::Off, None, None);
            self.set_want(Mode::Off, None, false);
        }
        self.rehome = true;
        self.poke();
    }

    /// `feed(cursor, motion, outputs, applied)`, sampler thread: latest cursor (global logical px or None), motion since
    /// the last sample, monitor rectangles and the last `want` serial it applied. Wakes the loop when anything changed.
    pub fn feed(&mut self, cursor: Option<(i64, i64)>, motion: (f64, f64), outputs: &[OutputRect], applied: i64) -> bool {
        match self.feed_changed(cursor, motion, outputs, applied) {
            Json::Bool(b) => b,
            _ => true,
        }
    }

    /// `feed`'s value as the Python's `or` chain gives it: True, a motion's nonzero part, or a bool.
    pub(crate) fn feed_changed(
        &mut self,
        cursor: Option<(i64, i64)>,
        motion: (f64, f64),
        outputs: &[OutputRect],
        applied: i64,
    ) -> Json {
        let changed = if cursor != self.abs {
            Json::Bool(true)
        } else if motion.0 != 0.0 {
            Json::Float(motion.0)
        } else if motion.1 != 0.0 {
            Json::Float(motion.1)
        } else {
            Json::Bool(outputs != self.fed_outputs.as_slice() || applied != self.applied)
        };
        self.abs = cursor;
        self.fed_outputs = outputs.to_vec();
        self.applied = applied;
        self.motion[0] += motion.0;
        self.motion[1] += motion.1;
        let any = changed != Json::Bool(false);
        if any {
            self.fresh = true;
            self.poke();
        }
        changed
    }

    /// `shape_serial()`, sampler thread, before a read: hand it back to feed_shape.
    pub fn shape_serial(&self) -> i64 {
        self.shape_serial
    }

    /// `feed_shape(name, serial)`, sampler thread: the cursor the app under the pointer asked for, by name, None for none
    /// or a failed read. A read from before the newest push (`serial`: shape_serial at its start) is older than that push
    /// and counts for nothing. One that disagrees with the last push means a push went missing, so the sampler reads at
    /// SHAPE_S until the next one arrives. Wakes the loop on a change, and at every read while a leave waits.
    pub fn feed_shape(&mut self, name: Option<&str>, serial: Option<i64>) {
        let text = fp::is_text_shape(name);
        if serial.is_some_and(|s| s != self.shape_serial) {
            return;
        }
        if text != self.push_text {
            self.shape_pushed = false;
        }
        let changed = text != self.shape_text;
        self.shape_text = text;
        if changed || self.text_hold.until.is_some() {
            self.poke();
        }
    }

    /// `push_shape(name)`, loop thread: the plugin says the cursor changed (ViewControl.on_events; None for none). The
    /// poll that follows in this same pass of the loop sends the frame. Before the sampler has applied the pointer's last
    /// warp the name may be the place it left, so that one only counts as proof that pushes arrive: the read after the
    /// warp gets the truth.
    pub fn push_shape(&mut self, name: Option<&str>) {
        let text = fp::is_text_shape(name);
        if !self.shape_wired {
            return;
        }
        self.shape_serial += 1;
        (self.push_text, self.shape_pushed) = (text, true);
        if self.applied >= self.sync {
            self.shape_text = text;
        }
    }

    pub(crate) fn poke(&self) {
        self.wake.signal(); // full or closed: the loop's own 50 ms tick picks it up
    }

    /// `drain()`: the wake fd's pokes so far.
    pub fn drain(&self) {
        self.wake.drain();
    }

    fn take(&mut self) -> (bool, Option<(i64, i64)>, [f64; 2], i64) {
        let (fresh, cursor, applied) = (self.fresh, self.abs, self.applied);
        let motion = std::mem::replace(&mut self.motion, [0.0, 0.0]);
        self.fresh = false;
        self.outputs = self.fed_outputs.clone();
        (fresh, cursor, motion, applied)
    }

    // --- helpers --------------------------------------------------------------------------------------------------

    pub(crate) fn set_want(&mut self, kind: Mode, point: Option<(i64, i64)>, sync: bool) {
        let serial = self.want.serial + 1;
        self.want = Want { serial, kind, point };
        if sync {
            self.sync = serial;
        }
    }

    /// `_open()`: the desktop whose pill is expanded into its panel, or None.
    pub(crate) fn open(&self) -> Option<usize> {
        let s = self.control.state();
        s.menu_open.then_some(s.menu_desktop)
    }

    /// `_help()`: the help card's canvas geometry while the card is open (the renderer may still report a closing one).
    fn help(&self) -> Option<&HelpGeometry> {
        if self.control.help_visible() { self.help_geometry.as_ref() } else { None }
    }

    /// `_hit()`: hit(), with the help card on top of everything while it is open.
    pub(crate) fn hit(&mut self) -> Option<Hit> {
        if let Some(h) = self.on_help(0.0) {
            return Some(h);
        }
        if let Some(h) = self.options_hit() {
            return Some(h);
        }
        let s = self.control.state();
        let icons = self.icons();
        let opts = HitOptions {
            open: self.open().map(|i| i as i64),
            icons: Some(&icons),
            picker: s.scale_picker,
            resolution_picker: s.resolution_picker,
            background_picker: s.background_picker,
        };
        fp::hit(self.geometry.as_ref(), &self.rolled(), self.pointer, &opts)
    }

    /// `_options_hit()`: the open options panel, then any desktop's gear.
    fn options_hit(&mut self) -> Option<Hit> {
        let (g, pointer) = (self.geometry.clone()?, self.pointer?);
        let d = if self.control.has_options() { self.control.options_snapshot() } else { Json::Null };
        let placements = self.rolled();
        let truthy = !matches!(&d, Json::Null) && d.as_object().is_none_or(|o| !o.is_empty());
        if truthy && self.control.options_visible() {
            let names = &self.control.state().names;
            if let Some(i) = d.get("monitor").and_then(Json::as_str).and_then(|m| names.iter().position(|n| n == m))
                && i < g.tiles.len()
            {
                let tile = g.tiles[i];
                let captured = self.control.options_capture();
                let p = match captured.as_ref().and_then(|c| json_pose(json_get(c, "placement"))) {
                    Some(p) => p,
                    None => placements[i],
                };
                let q = fp::desktop_to_glass(&tile, &p, fp::unproject(&g, &tile, &p, pointer));
                let r = og::panel_rect(&tile, &fp::pill_rect(&tile));
                if og::inside(&r, q) {
                    let q = q.expect("inside");
                    return Some(Hit::OptionsPanel(i, q[0] - r[0], q[1] - r[1]));
                }
            }
        }
        let order = fp::front_order(&g, &placements);
        let qs: Vec<(usize, Option<[f64; 2]>)> =
            order.iter().map(|&i| (i, fp::unproject(&g, &g.tiles[i], &placements[i], pointer))).collect();
        for (i, q) in qs {
            // the gear is glass, drawn over every desktop picture like the pills (on_drawn_pill)
            let tile = g.tiles[i];
            let q = fp::desktop_to_glass(&tile, &placements[i], q);
            if og::gear_hit(&fp::pill_rect(&tile), q) {
                return Some(Hit::OptionsGear(i));
            }
        }
        None
    }

    /// `options_wheel(delta)`: the outer wheel subscription must pass through when False is returned.
    pub fn options_wheel(&mut self, delta: f64) -> (bool, Vec<u8>) {
        if !matches!(self.options_hit(), Some(Hit::OptionsPanel(..))) {
            return (false, Vec::new());
        }
        let delta = (delta as i64).clamp(-4096, 4096);
        (true, self.control.options_intent("options_scroll", vec![("delta", Json::Int(delta))]))
    }

    /// `_on_help(margin)`: help_hit for the dot: the card is reported on the overview's plane, so the dot's ray is met
    /// there.
    pub(crate) fn on_help(&self, margin: f64) -> Option<Hit> {
        let (help, pointer, g) = (self.help()?, self.pointer?, self.geometry.as_ref()?);
        fp::help_hit(Some(help), orbit::plane_at(g, &orbit::pointer_ray(g, pointer)), margin)
    }

    /// `_rolled()`: the placements as drawn: each with its levelling roll (orbit.place_point's 'roll') when it has one.
    pub(crate) fn rolled(&self) -> Vec<Pose> {
        self.control
            .state()
            .placements
            .iter()
            .enumerate()
            .map(|(i, p)| Pose::rolled(*p, self.rolls.get(i).copied().unwrap_or(0) as f64))
            .collect()
    }

    pub(crate) fn tilt(&self, i: usize) -> Option<i64> {
        self.tilts.get(i).copied().flatten()
    }

    /// `_icons()`: per desktop what it shows (the wire's icons): its level bar on every desktop, level or not, measured
    /// or not (a desktop can always be rolled either way; they only hide with the pills, roll_frame).
    fn icons(&self) -> Vec<Option<&'static str>> {
        vec![Some("bar"); self.control.state().placements.len()]
    }

    /// `_around()`: anchor mode: the dot's reach goes all the way round you (orbit.reach). A drag keeps the reach it
    /// started with.
    fn around(&self) -> bool {
        match &self.drag {
            Some(Drag::Pill { around, .. }) => *around,
            _ => self.control.state().mode() == view::Mode::Anchor,
        }
    }

    pub(crate) fn output(&self, index: Option<usize>) -> Option<OutputRect> {
        let index = index?;
        self.outputs.iter().find(|o| o.index == index).copied()
    }

    /// `_placement(i)`: desktop i's placement as drawn (with its levelling roll), for hit testing and cursor mapping.
    pub(crate) fn placement(&self, i: usize) -> Option<Pose> {
        self.rolled().get(i).copied()
    }

    pub(crate) fn tile(&self, i: usize) -> Option<Tile> {
        self.geometry.as_ref().and_then(|g| g.tiles.get(i).copied())
    }

    /// `_from_logical(i, lx, ly)`: monitor-local logical px on desktop i -> where the dot is drawn (canvas px;
    /// layout_to_surface, project).
    #[allow(clippy::wrong_self_convention)] // the Python's name
    pub(crate) fn from_logical(&self, i: usize, lx: f64, ly: f64) -> Option<[f64; 2]> {
        let (o, tile, p) = (self.output(Some(i))?, self.tile(i)?, self.placement(i)?);
        let [sx, sy] = fp::layout_to_surface(&tile, &o, lx, ly);
        Some(fp::project(self.geometry.as_ref()?, &tile, &p, sx, sy))
    }

    /// `_exit(i, lx, ly)`: where the dot goes free when the cursor at monitor-local (lx, ly) on desktop i reaches its
    /// edge pixel (or past it, onto another monitor): EXIT_PX logical px beyond each edge it reached, plus as far past it
    /// as Hyprland let the cursor go, mapped onto the desktop's surface (layout_to_surface extends past its edges) and
    /// projected. So the dot never stays free over content: it sits on that edge's (or corner's) resize handle, and any
    /// move back inside hands the real cursor back (ENTER_PX). None without the desktop's monitor or layout.
    pub(crate) fn exit(&self, i: usize, lx: f64, ly: f64) -> Option<[f64; 2]> {
        let o = self.output(Some(i))?;
        let axis = |v: f64, size: f64| {
            if v < EDGE_PX {
                v.min(0.0) - EXIT_PX
            } else if v > size - 1.0 - EDGE_PX {
                (v + 1.0).max(size) + EXIT_PX
            } else {
                v
            }
        };
        self.from_logical(i, axis(lx, o.width), axis(ly, o.height))
    }

    /// `_to_layout(i, q)`: tile pixel q of desktop i -> integer layout pixel inside its monitor (surface_to_layout,
    /// INSET_PX in).
    pub(crate) fn to_layout(&self, i: usize, q: [f64; 2]) -> Option<(i64, i64)> {
        let (o, tile) = (self.output(Some(i))?, self.tile(i)?);
        Some(fp::surface_to_layout(&tile, &o, q, fp::INSET_PX))
    }

    pub(crate) fn gain_of(&self, i: usize) -> f64 {
        match (self.output(Some(i)), self.tile(i), self.placement(i)) {
            (Some(o), Some(tile), Some(p)) => tile[2] / o.width * 100.0 / p.distance,
            _ => self.gain,
        }
    }

    /// `_clamp(point)`: the pointer kept within its reach (orbit.reach): in anchor mode to directly behind you and the
    /// poles. (Without a layout, which the Python never meets here, the point as it is.)
    pub(crate) fn clamp(&self, point: [f64; 2]) -> [f64; 2] {
        let Some(g) = &self.geometry else { return point };
        let (ru, rv) = orbit::reach(g, self.around());
        let (w, h) = (g.w as f64, g.h as f64);
        [
            point[0].max(w * (1.0 - ru) / 2.0).min(w * (1.0 + ru) / 2.0),
            point[1].max(h * (1.0 - rv) / 2.0).min(h * (1.0 + rv) / 2.0),
        ]
    }

    /// `_at(cursor)`: (index, lx, ly) of the XR monitor holding layout point `cursor`, or None.
    pub(crate) fn at(&self, cursor: (i64, i64)) -> Option<(usize, f64, f64)> {
        self.outputs.iter().find_map(|o| {
            let (lx, ly) = ((cursor.0 - o.x) as f64, (cursor.1 - o.y) as f64);
            (0.0 <= lx && lx < o.width && 0.0 <= ly && ly < o.height).then_some((o.index, lx, ly))
        })
    }

    /// `_select(i)`: the desktop the pointer went into (or clicked) becomes the selected one: Ctrl+Alt+Up zooms into it,
    /// and an open panel goes with it (view.ViewControl.follow_focus). Returns a view frame if that changed.
    pub(crate) fn select(&mut self, i: usize) -> Vec<u8> {
        let s = self.control.state();
        if i >= s.names.len() || s.selected == i {
            return Vec::new();
        }
        self.control.state_mut().selected = i;
        let mut out = self.control.frame();
        out.extend(self.control.follow_focus());
        out
    }

    /// `_enter(i, q)`: free -> desktop mode on desktop i at tile pixel q: the cursor comes back there, visible.
    pub(crate) fn enter(&mut self, i: usize, q: [f64; 2]) -> Vec<u8> {
        let Some(target) = self.to_layout(i, q) else { return Vec::new() };
        (self.mode, self.desktop, self.hover) = (Mode::Desktop, Some(i), None);
        self.set_want(Mode::Desktop, Some(target), true);
        self.entries += 1;
        self.select(i)
    }

    /// `_click_desktop(i, q)`: a dot click on desktop i at tile pixel q: select it and stay in the overview
    /// (view.ViewControl.select_desktop; anchor-no-click-maximize: a click never maximizes, Ctrl+Alt+Up does). The real
    /// cursor comes back there under the dot (enter), which gives that monitor Hyprland's focus.
    fn click_desktop(&mut self, i: usize, q: [f64; 2]) -> Vec<u8> {
        self.focus_clicks += 1;
        let mut out = self.control.select_desktop(i, None);
        if !self.active() || self.control.state().selected != i {
            return out; // stale: zoomed, typing, a video, gone
        }
        out.extend(self.enter(i, q));
        out
    }

    // --- the loop -------------------------------------------------------------------------------------------------

    /// `poll(now)`: supervisor tick: apply the newest cursor sample; returns renderer frames.
    pub fn poll(&mut self, now: f64) -> Vec<u8> {
        if self.ext.is_some() {
            return self.u_poll(now);
        }
        self.poll_base(now)
    }

    pub(crate) fn poll_base(&mut self, now: f64) -> Vec<u8> {
        let (mut fresh, cursor, motion, applied) = self.take();
        if self.restore_sampling() {
            return self.poll_restore(cursor, motion, applied, now);
        }
        self.dwell.reset();
        self.zoom_held = None;
        if self.mode == Mode::Restore {
            self.restore_release();
        }
        let mut out = if self.band_sent { self.restore_frame() } else { Vec::new() };
        if !self.active() {
            if self.mode != Mode::Off {
                out.extend(self.end_drag());
                (self.mode, self.hover, self.pointer, self.summoned) = (Mode::Off, None, None, false);
                self.set_want(Mode::Off, None, false);
            }
        } else {
            if self.mode == Mode::Off {
                (self.mode, self.desktop, self.pointer) = (Mode::Away, None, None);
                self.set_want(Mode::Away, None, false);
                fresh = true; // the cursor where it already is counts: a still mouse never sends another sample
                if self.rehome {
                    (self.summoned, self.rehome) = (true, false);
                }
            }
            self.take_found();
            if self.mode == Mode::Free {
                if motion[0] != 0.0 || motion[1] != 0.0 {
                    out.extend(self.move_by(motion[0] * self.gain, motion[1] * self.gain, now));
                }
                out.extend(self.hold(now));
            } else if self.mode == Mode::Carry {
                // a Super drag between desktops (unified_cursor.py)
                if motion[0] != 0.0 || motion[1] != 0.0 {
                    out.extend(self.carry(motion[0] * self.gain, motion[1] * self.gain, now));
                }
            } else if let Some(cursor) = cursor.filter(|_| fresh && applied >= self.sync) {
                out.extend(self.absolute(cursor, now));
            } else {
                self.onto_help(); // no cursor motion: the card may have come over the cursor itself
            }
        }
        let idle = now - self.last_move >= OVERLAY_IDLE_CLOSE_S;
        if idle {
            out.extend(self.end_drag());
            self.hover = None;
        }
        self.take_found(); // a lookup answered meanwhile (at once, for a cached one) shows in this frame
        self.step_text(now);
        out.extend(self.frames(idle));
        out
    }

    /// `_absolute(cursor, now)`: away / desktop mode: the cursor's own position decides.
    pub(crate) fn absolute(&mut self, cursor: (i64, i64), now: f64) -> Vec<u8> {
        if self.ext.is_some() {
            return self.u_absolute(cursor, now);
        }
        self.absolute_base(cursor, now)
    }

    pub(crate) fn absolute_base(&mut self, cursor: (i64, i64), now: f64) -> Vec<u8> {
        let mut out = Vec::new();
        let at = self.at(cursor);
        if self.mode == Mode::Away {
            let Some(at) = at else {
                if self.summoned {
                    // Ctrl+Alt+Down with the cursor elsewhere: into the middle of the selected desktop
                    self.summoned = false;
                    let i = self.control.state().selected;
                    if let Some(tile) = self.tile(i)
                        && self.output(Some(i)).is_some()
                    {
                        (self.mode, self.desktop) = (Mode::Desktop, Some(i));
                        let target = self.to_layout(i, [tile[0] + tile[2] / 2.0, tile[1] + tile[3] / 2.0]);
                        self.set_want(Mode::Desktop, target, true);
                    }
                }
                return out;
            };
            (self.mode, self.desktop) = (Mode::Desktop, Some(at.0));
            out.extend(self.select(at.0));
        }
        self.summoned = false;
        let Some(o) = self.output(self.desktop) else {
            (self.mode, self.pointer) = (Mode::Away, None);
            return out;
        };
        let desktop = self.desktop.expect("an output");
        let (lx, ly) = ((cursor.0 - o.x) as f64, (cursor.1 - o.y) as f64);
        let (w, h) = (o.width, o.height);
        if EDGE_PX <= lx && lx <= w - 1.0 - EDGE_PX && EDGE_PX <= ly && ly <= h - 1.0 - EDGE_PX {
            let point = self.from_logical(desktop, lx, ly);
            if point.is_some() && point != self.pointer {
                (self.pointer, self.last_move) = (point, now);
            }
            self.onto_help();
            return out;
        }
        let (cx, cy) = (lx.max(0.0).min(w - 1.0), ly.max(0.0).min(h - 1.0));
        if py_hypot(lx - cx, ly - cy) > JUMP_PX {
            // a keyboard focus change or warp, not the hand: follow the cursor
            if let Some((i, ax, ay)) = at {
                self.desktop = Some(i);
                out.extend(self.select(i));
                self.pointer = self.from_logical(i, ax, ay);
            } else {
                (self.mode, self.pointer) = (Mode::Away, None);
            }
            return out;
        }
        let Some(start) = self.exit(desktop, lx, ly) else { return out };
        self.gain = self.gain_of(desktop);
        self.pointer = Some(self.clamp(start));
        (self.mode, self.last_move) = (Mode::Free, now);
        self.exits += 1;
        let park = self.park(&o, Some((cx, cy)));
        self.set_want(Mode::Free, Some(park), false);
        self.hover = self.hit();
        out
    }

    /// `_onto_help()`: desktop mode with the pointer on the open help card (the cursor moved onto it, or the card opened
    /// or turned with the head over a still cursor) or the options menu, which can hang over the desktop below its own:
    /// they are on top, so the pointer turns into the free dot right there and the left button goes to the run, not to
    /// the window hidden under them.
    pub(crate) fn onto_help(&mut self) {
        if self.ext.is_some() {
            return self.u_onto_help();
        }
        self.onto_help_base()
    }

    pub(crate) fn onto_help_base(&mut self) {
        let Some(o) = self.output(self.desktop) else { return };
        if self.mode != Mode::Desktop {
            return;
        }
        let over_menu = self.control.has_options()
            && self.control.options_visible()
            && matches!(self.options_hit(), Some(Hit::OptionsPanel(..)));
        if self.on_help(0.0).is_none() && !over_menu {
            return;
        }
        let desktop = self.desktop.expect("an output");
        self.gain = self.gain_of(desktop);
        self.mode = Mode::Free;
        self.exits += 1;
        let park = self.park(&o, None);
        self.set_want(Mode::Free, Some(park), false);
        self.hover = self.hit();
    }

    /// `_park(o, local)`: where free mode parks the hidden cursor on output `o` (`local`: where it left the desktop,
    /// monitor-local px): the middle of the monitor, so hand motion has room before any edge.
    pub(crate) fn park(&self, o: &OutputRect, local: Option<(f64, f64)>) -> (i64, i64) {
        if self.ext.is_some() {
            return self.u_park(o, local);
        }
        ((o.x as f64 + o.width / 2.0) as i64, (o.y as f64 + o.height / 2.0) as i64)
    }

    /// `_move(dx, dy, now)`: free mode: the pointer moves by the cursor's motion; a drag follows it, otherwise it may go
    /// into a desktop.
    pub(crate) fn move_by(&mut self, dx: f64, dy: f64, now: f64) -> Vec<u8> {
        if self.ext.is_some() {
            return self.u_move(dx, dy, now);
        }
        self.move_base(dx, dy, now)
    }

    pub(crate) fn move_base(&mut self, dx: f64, dy: f64, now: f64) -> Vec<u8> {
        let Some(p) = self.pointer else { return Vec::new() };
        self.pointer = Some(self.clamp([p[0] + dx, p[1] + dy]));
        self.last_move = now;
        if self.control.state().menu_open {
            self.control.set_last_input(now); // moving over an open panel keeps it up
        }
        if self.control.has_options() && self.control.options_visible() {
            self.control.options_touch(); // and the options menu
        }
        if self.drag.is_some() {
            return self.apply_drag();
        }
        self.settle()
    }

    /// `_settle()`: the free dot with nothing held: what is under it now and, on a desktop's content, the real cursor
    /// there.
    pub(crate) fn settle(&mut self) -> Vec<u8> {
        self.hover = self.hit();
        if let Some(Hit::Desktop(i, q)) = self.hover
            && self.tile(i).is_some_and(|t| fp::inside_by(&t, q) >= ENTER_PX)
            && self.on_help(HELP_CLEAR_PX).is_none()
        {
            // and HELP_CLEAR_PX clear of the help card
            return self.enter(i, q);
        }
        Vec::new()
    }

    /// `on_op(op)`: resize_press / resize_release: the left button, bound only in free mode (module docstring: Clicks).
    /// Frames or None; any other op is the Python's ValueError.
    pub fn on_op(&mut self, op: &str) -> Result<Option<Vec<u8>>> {
        if self.ext.is_some() {
            return self.u_on_op(op);
        }
        self.on_op_base(op)
    }

    pub(crate) fn on_op_base(&mut self, op: &str) -> Result<Option<Vec<u8>>> {
        if !RESIZE_OPS.contains(&op) {
            return Err(Invalid::new("unsupported resize operation"));
        }
        if self.mode == Mode::Restore || self.restore_press.is_some() {
            return Ok(self.restore_op(op));
        }
        if self.restore_sampling() {
            // maximized, the non-consuming binds: the app's own click, and no rest
            self.zoom_held = if op == "resize_press" { Some(self.now()) } else { None };
            self.dwell.restart();
            return Ok(None);
        }
        let now = self.now();
        let idle = now - self.last_move >= OVERLAY_IDLE_CLOSE_S;
        self.last_move = now; // a click is input: the pointer, pills, bars and caps come back
        let mut out = Vec::new();
        if op == "resize_release" {
            if matches!(self.drag, Some(Drag::Roll { .. })) && !self.overview() {
                self.drag = None; // maximized since the press: a maximized desktop is level, so no turn lands behind it
            }
            let d = self.drag.clone();
            out.extend(self.apply_drag());
            if let Some(Drag::OptionsRange { row, monitor, scope, capture, range, .. }) = &d {
                if self.control.options_capture().as_ref() == Some(capture) {
                    let value = self.options_range_value(d.as_ref().expect("a drag"), *range);
                    self.control.set_options_preview(Json::Null);
                    self.control.set_options_capture(None);
                    match value {
                        None => self.control.options_refresh(),
                        Some(value) => {
                            // against the menu as it is now: readbacks may have changed the page during the drag (the
                            // row is still checked by id, monitor and scope)
                            let revision = json_get(&self.control.options_snapshot(), "revision").clone();
                            let kwargs = vec![
                                ("row_id", json_get(row, "id").clone()),
                                ("monitor", monitor.clone()),
                                ("scope", scope.clone()),
                                ("revision", revision),
                                ("value", num_json(value)),
                            ];
                            out.extend(self.control.options_intent("options_set", kwargs));
                        }
                    }
                }
                self.drag = None;
            }
            if let Some(Drag::Maximize { monitor, index, .. }) = &d {
                self.drag = None;
                if self.hit() == Some(Hit::Maximize(*index)) {
                    // a button: dragged off it, its release does nothing
                    out.extend(self.control.maximize_monitor(monitor));
                }
            }
            match &d {
                Some(Drag::Pill { moved: false, index, open_at_press, .. }) => {
                    // a click, not a drag: expand / collapse
                    if self.open() == *open_at_press {
                        self.drag = None;
                        self.pill_clicks += 1;
                        out.extend(self.control.pill_click(*index));
                    } else {
                        // a key changed the open panel while the button was down: that key wins, the release is dropped
                        self.stale_clicks += 1;
                    }
                }
                Some(Drag::Window { index, on, lifted, .. }) => {
                    self.drag = None;
                    if *lifted {
                        out.extend(self.drop_window(d.as_ref().expect("a drag")));
                    } else {
                        out.extend(self.click_desktop(*index, *on)); // a click, as before: select it
                    }
                }
                Some(Drag::Roll { moved: false, side, index, from, .. }) => {
                    if *side == "level" {
                        out.extend(self.level(*index as i64)); // the middle segment: level, nothing else moves
                    } else {
                        // a click on a turn segment: that way, to level or a step
                        self.roll_clicks += 1;
                        self.rolls[*index] = fp::roll_click(self.tilt(*index), *from, side).expect("a roll side");
                    }
                }
                _ => {}
            }
            out.extend(self.end_drag()); // (a release on a resize handle that resized nothing does nothing)
            if let Some(d) = &d
                && self.mode == Mode::Free
                && self.pointer.is_some()
            {
                // the drag kept the dot's look; it is over: what is under the dot now, the real cursor if that is
                // content (a dropped window and a slider, whose panel is in use, leave the dot free until it next moves;
                // so does a resize that pushed a neighbour right up to the handle, which can leave the dot on that
                // neighbour's picture: a resize never changes the selection).
                let h = self.hit();
                let neighbour = matches!(h, Some(Hit::Desktop(i, _)) if i != d.index());
                if matches!(d, Drag::Window { .. } | Drag::Slider { .. })
                    || (RESIZE_DRAGS.contains(&d.kind()) && neighbour)
                {
                    self.hover = h;
                } else {
                    out.extend(self.settle());
                }
            }
        } else if self.active() && self.mode == Mode::Free && !idle && self.pointer.is_some() {
            out.extend(self.end_drag()); // a press during a drag: its release was lost; finish that one first
            match self.press(now, &mut out) {
                Pressed::Return(r) => return Ok(r),
                Pressed::Frames => {}
            }
        }
        out.extend(self.frames(false));
        Ok((!out.is_empty()).then_some(out))
    }

    /// `on_op`'s press branch, from its hit on.
    fn press(&mut self, now: f64, out: &mut Vec<u8>) -> Pressed {
        let pointer = self.pointer.expect("a pointer");
        let open_ = self.open();
        let h = self.hit();
        self.hover = h.clone();
        if let Some(Hit::Help(name)) = h {
            // the help card is on top: its arrows and indicator turn pages
            self.help_clicks += 1;
            if name != "card" {
                out.extend(self.control.help_turn(if name == "prev" { -1 } else { 1 }));
            }
            out.extend(self.frames(false));
            return Pressed::Return((!out.is_empty()).then(|| std::mem::take(out)));
        }
        if let Some(Hit::OptionsGear(i)) = h {
            let monitor = Json::Str(self.control.state().names[i].clone());
            out.extend(self.control.options_intent("options_open", vec![("monitor", monitor)]));
            return Pressed::Return(Some(std::mem::take(out)));
        }
        if let Some(Hit::OptionsPanel(i, x, y)) = h {
            self.options_press(i, x, y, pointer, out);
            return Pressed::Return(Some(std::mem::take(out)));
        }
        if self.control.has_options() && self.control.options_visible() {
            // a press off the menu, which is open and drawn on top
            if matches!(h, Some(Hit::Desktop(..))) {
                // a desktop is no outside (as for a placement panel): the keys go back
                self.control.options_desktop_press();
                out.extend(self.control.options_frame());
            } else {
                self.outside_closes += 1;
                out.extend(self.control.options_intent("options_close", Vec::new())); // and the press carries on: it
                // was not on the menu
            }
        }
        if let Some(Hit::Maximize(i)) = h {
            let monitor = self.control.state().names[i].clone();
            self.drag = Some(Drag::Maximize { monitor, index: i, start: pointer });
            return Pressed::Return(Some(std::mem::take(out)));
        }
        let index_of = |h: &Option<Hit>| -> Option<usize> {
            match h.as_ref()? {
                Hit::Pill(i)
                | Hit::Panel(i)
                | Hit::Slider(i, ..)
                | Hit::Scale(i, _)
                | Hit::Background(i, _)
                | Hit::Resolution(i, _)
                | Hit::ResolutionBox(i)
                | Hit::ScaleBox(i)
                | Hit::BackgroundBox(i)
                | Hit::BackgroundSwitch(i)
                | Hit::Action(i, _)
                | Hit::Corner(i, _)
                | Hit::Edge(i, _)
                | Hit::Desktop(i, _)
                | Hit::Maximize(i)
                | Hit::Immersive(i, _)
                | Hit::Roll(i, _)
                | Hit::OptionsGear(i)
                | Hit::OptionsPanel(i, ..) => Some(*i),
                Hit::Help(_) => None,
            }
        };
        let own = open_.is_some()
            && h.as_ref().is_some_and(|h| PANEL_KINDS.contains(&h.kind()))
            && index_of(&h) == open_;
        if own {
            self.control.panel_keys_on(); // the panel is in use: its keys are back if a window had them
        }
        let kind = h.as_ref().map(Hit::kind);
        let s = self.control.state();
        let (scale, resolution, background) = (s.scale_picker, s.resolution_picker, s.background_picker);
        if open_.is_some() && !own && kind != Some("desktop") {
            self.outside_closes += 1;
            out.extend(self.control.pill_close()); // a click outside collapses the open panel; the press carries on
            // (what it hit was not on that panel, so the panel's going changes nothing about it). A desktop is no
            // outside: selecting it brings the panel there.
        } else if scale && !matches!(kind, Some("scale" | "scale_box")) {
            out.extend(self.control.scale_close()); // a press on the panel away from the open Scale list closes it
        } else if resolution && !matches!(kind, Some("resolution" | "resolution_box")) {
            out.extend(self.control.resolution_close()); // ... and away from the open Resolution list
        } else if background && !matches!(kind, Some("background" | "background_box")) {
            out.extend(self.control.background_close()); // ... and away from the open Background list
        }
        match h {
            Some(Hit::Roll(i, side)) => {
                let tile = self.tile(i).expect("a hit's tile");
                if let Some(name) = self.control.state().names.get(i).cloned() {
                    self.levelling.retain(|(n, _)| *n != name); // yours now
                }
                let p = self.placement(i).expect("a hit's placement");
                let g = self.geometry.as_ref().expect("a hit's layout");
                let centre = fp::project(g, &tile, &p, tile[0] + tile[2] / 2.0, tile[1] + tile[3] / 2.0);
                self.drag = Some(Drag::Roll { index: i, side, start: pointer, from: self.rolls[i], centre, moved: false });
            }
            Some(Hit::Corner(i, handle) | Hit::Edge(i, handle)) => {
                // a resize handle: the border round the desktop, never its content (border_handle)
                let (tile, p) = (self.tile(i).expect("a hit's tile"), self.control.state().placements[i]);
                self.drag =
                    Some(Drag::Resize { index: i, handle, start: pointer, distance: p.distance, tile, bump: None });
                self.drags += 1;
            }
            Some(Hit::Pill(i)) => {
                // the desktop moves only once the pointer is CLICK_PX away; a release before that is a click
                let (tile, p) = (self.tile(i).expect("a hit's tile"), self.control.state().placements[i]);
                self.drag = Some(Drag::Pill {
                    index: i,
                    start: pointer,
                    xy: (p.x, p.y),
                    tile,
                    moved: false,
                    open_at_press: self.open(), // after any click-outside collapse above
                    around: self.around(),      // the dot's reach the drag keeps to its end
                    orbit: None,
                });
            }
            Some(Hit::Slider(i, field, lx)) => {
                let placement = self.control.state().placements[i]; // the wire placement (no roll)
                let tile = self.tile(i).expect("a hit's tile");
                self.drag = Some(Drag::Slider { index: i, field, tile, placement });
                self.slides += 1;
                // The value jumps to the pointer; the panel stays drawn on the press-time placement until the release.
                out.extend(self.control.pill_slide(i, field, fp::slider_fraction(lx), Some(&placement)));
            }
            Some(Hit::Immersive(i, part)) => {
                // the 3D button, like the panel's switches: this desktop, or the tail: all
                self.actions += 1;
                out.extend(if part == "main" {
                    self.control.immersive_click(i)
                } else {
                    self.control.immersive_tail_click(i)
                });
            }
            Some(Hit::Action(i, name)) => {
                // the desktop row: Ultrawide / Add / Remove for the panel's desktop
                self.actions += 1;
                out.extend(self.control.desktop_action(i, name));
            }
            Some(Hit::ScaleBox(i)) => out.extend(self.control.scale_box(i)), // the Scale dropdown opens / closes its list
            Some(Hit::Scale(i, percent)) => {
                // a step of the open list: that desktop's output scale
                self.actions += 1;
                out.extend(self.control.scale_pick(i, percent));
            }
            Some(Hit::ResolutionBox(i)) => out.extend(self.control.resolution_box(i)),
            Some(Hit::Resolution(i, size)) => {
                // a size of the open list: that desktop's resolution, live
                self.actions += 1;
                out.extend(self.control.resolution_pick(i, size));
            }
            Some(Hit::BackgroundBox(i)) => out.extend(self.control.background_box(i)),
            Some(Hit::Background(i, name)) => {
                // an item of the open list: that background
                self.actions += 1;
                out.extend(self.control.background_pick(i, name));
            }
            Some(Hit::BackgroundSwitch(i)) => {
                // as Ctrl+Alt+E
                self.actions += 1;
                out.extend(self.control.background_switch(i));
            }
            Some(Hit::Desktop(i, q)) => {
                // a click selects it on release; a hold or drag lifts its window
                let point = self.to_layout(i, q);
                self.drag = Some(window_press(i, q, pointer, point, now));
            }
            // ('panel', i): elsewhere on the open panel, nothing; None: empty space, nothing (beyond the collapse above)
            _ => {}
        }
        Pressed::Frames
    }

    /// The press on the open options panel at panel px (x, y).
    fn options_press(&mut self, i: usize, x: f64, y: f64, pointer: [f64; 2], out: &mut Vec<u8>) {
        self.control.set_options_focused(true);
        let d = self.control.options_snapshot();
        let rows = json_get(&d, "rows");
        let count = match rows {
            Json::Array(r) => r.len() as i64,
            _ => 0,
        };
        let scroll = num(json_get(&d, "scroll")).map_or(0.0, Num::get);
        let row = og::row_at(x, y, scroll, count);
        let category = json_get(&d, "category").as_str().unwrap_or("");
        let category_index = OPTIONS_CATEGORIES.iter().position(|c| *c == category).unwrap_or(0) as i64;
        let (header, tab) = (og::header_at(x, y), og::tab_at(x, y, category_index, OPTIONS_CATEGORIES.len() as i64));
        let scope = json_get(&d, "scope").clone();
        if header == Some("close") {
            out.extend(self.control.options_intent("options_close", Vec::new()));
        } else if header == Some("scope") {
            let other = if scope.as_str() == Some("monitor") { "workspace" } else { "monitor" };
            out.extend(self.control.options_intent("options_scope", vec![("scope", Json::Str(other.into()))]));
        } else if header == Some("next") {
            let next = options_next_page(category).unwrap_or(OPTIONS_CATEGORIES[0]);
            out.extend(self.control.options_intent("options_category", vec![("category", Json::Str(next.into()))]));
        } else if let Some(tab) = tab {
            let category = Json::Str(OPTIONS_CATEGORIES[tab as usize].into());
            out.extend(self.control.options_intent("options_category", vec![("category", category)]));
        } else if let Some(row) = row {
            out.extend(self.control.options_intent("options_focus", vec![("focus", Json::Int(row))]));
            let r = match rows {
                Json::Array(r) => r.get(row as usize).cloned().unwrap_or(Json::Null),
                _ => Json::Null,
            };
            let range = (json_get(&r, "kind").as_str() == Some("range"))
                .then(|| {
                    Some(og::RangeRow {
                        minimum: num(r.get("minimum")?)?,
                        maximum: num(r.get("maximum")?)?,
                        step: num(r.get("step")?)?,
                    })
                })
                .flatten();
            if let Some(range) = range {
                if !((og::TRACK_X0 - 5) as f64 <= x && x <= (og::TRACK_X1 + 5) as f64) {
                    out.extend(self.control.options_frame());
                    return;
                }
                let tile = self.tile(i).expect("a hit's tile");
                let placement = self.rolled()[i];
                let monitor = json_get(&d, "monitor").clone();
                let capture = obj(vec![
                    ("kind", Json::Str("options_range".into())),
                    ("index", Json::Int(i as i64)),
                    ("start", Json::Array(vec![Json::Float(pointer[0]), Json::Float(pointer[1])])),
                    ("tile", Json::Array(tile.iter().map(|v| Json::Float(*v)).collect())),
                    ("placement", pose_json(&placement)),
                    ("row", r.clone()),
                    ("monitor", monitor.clone()),
                    ("scope", scope.clone()),
                    ("revision", json_get(&d, "revision").clone()),
                ]);
                self.drag = Some(Drag::OptionsRange {
                    index: i,
                    tile,
                    placement,
                    row: r,
                    range,
                    monitor,
                    scope,
                    capture: capture.clone(),
                });
                self.control.set_options_capture(Some(capture));
            } else {
                out.extend(self.control.options_activate(row));
            }
        }
        out.extend(self.control.options_frame());
    }

    /// `super_down()`: Super went down (resize.py's bind on the Super keys, there while the left button is the run's):
    /// Super + drag is a window's, so a monitor drag in progress (MONITOR_DRAGS; it began without Super) stops where it
    /// is, saved as on a release. It is no click: the button's later release finds nothing held. Frames or None.
    pub fn super_down(&mut self) -> Option<Vec<u8>> {
        if !self.drag.as_ref().is_some_and(|d| MONITOR_DRAGS.contains(&d.kind())) {
            return None;
        }
        self.super_stops += 1;
        let mut out = self.end_drag();
        out.extend(self.frames(false));
        (!out.is_empty()).then_some(out)
    }

    // --- window drag ------------------------------------------------------------------------------------------------

    /// `_hold(now)`: free mode, every tick: a press held HOLD_S lifts its window even if the dot never moved.
    fn hold(&mut self, now: f64) -> Vec<u8> {
        let Some(Drag::Window { lifted: false, at, .. }) = &self.drag else { return Vec::new() };
        if !self.window_drag || now - at < HOLD_S {
            return Vec::new();
        }
        let mut d = self.drag.take().expect("a drag");
        self.lift(&mut d);
        if let Drag::Window { target, .. } = &mut d {
            *target = fp::desktop_at(self.geometry.as_ref(), &self.rolled(), self.pointer);
        }
        self.drag = Some(d);
        Vec::new()
    }

    /// `_lift(d)`: the press becomes a window drag: ask (off the loop) which window is shown where it was pressed.
    pub(crate) fn lift(&mut self, d: &mut Drag) {
        let Drag::Window { lifted, window, point, index, token, .. } = d else { return };
        *lifted = true;
        self.window_lifts += 1;
        let names = &self.control.state().names;
        let (Some(p), true, true) = (*point, self.control.has_sender(), *index < names.len()) else {
            *window = Window::Gone; // nothing can be moved: the drag shows nothing and its release does nothing
            return;
        };
        let monitor = names[*index].clone();
        self.token += 1;
        let t = self.token;
        *token = Some(t);
        let (inbox, wake) = (Arc::clone(&self.inbox), Arc::clone(&self.wake));
        let found: Found = Box::new(move |window| {
            // the sender's worker thread
            inbox.lock().unwrap_or_else(|e| e.into_inner()).found = Some((t, window));
            wake.signal();
        });
        self.control.sender_lookup(&monitor, p, found);
    }

    /// `_take_found()`: the lookup's answer, for the drag that asked (a later drag ignores an earlier one's).
    pub(crate) fn take_found(&mut self) {
        let found = self.inbox.lock().unwrap_or_else(|e| e.into_inner()).found.take();
        let Some((t, answer)) = found else { return };
        if let Some(Drag::Window { token: Some(token), window, .. }) = &mut self.drag
            && *token == t
        {
            let pinned = answer.get("pinned").is_some_and(truthy);
            *window = if answer.as_object().is_some() && !pinned { Window::Found(answer) } else { Window::Gone };
        }
    }

    /// `_drop_window(d)`: release of a lifted window: onto the desktop under the dot if that is another one, else
    /// nowhere.
    fn drop_window(&mut self, d: &Drag) -> Vec<u8> {
        self.take_found();
        let Drag::Window { index, window, point, .. } = d else { return Vec::new() };
        let target = if self.pointer.is_some() {
            fp::desktop_at(self.geometry.as_ref(), &self.rolled(), self.pointer)
        } else {
            None
        };
        let names = self.control.state().names.clone();
        match target {
            Some(t)
                if t != *index
                    && *window != Window::Gone
                    && self.control.has_sender()
                    && *index < names.len()
                    && t < names.len() =>
            {
                self.window_drops += 1;
                let w = match window {
                    Window::Found(w) => w.clone(),
                    _ => Json::Null,
                };
                self.control.sender_drop(&w, &names[*index], *point, &names[t]);
            }
            _ => self.window_cancels += 1,
        }
        Vec::new()
    }

    /// `_drag_display()`: the `overview_pointer` frame's `drag` while a lifted window is dragged, else None.
    fn drag_display(&self) -> Option<Json> {
        let Some(Drag::Window { lifted: true, window, index, target, .. }) = &self.drag else { return None };
        if *window == Window::Gone {
            return None;
        }
        let i = *index;
        let (w, h) = match window {
            Window::Found(win) => {
                let size = |k: &str| num(win.get(k)?).map(Num::get);
                let wh = size("w").zip(size("h")).map(|(w, h)| [w, h]);
                match (self.tile(i), self.placement(i)) {
                    (Some(tile), Some(p)) => fp::ghost_size(wh, self.output(Some(i)).as_ref(), &tile, &p),
                    _ => GHOST_DEFAULT,
                }
            }
            _ => GHOST_DEFAULT,
        };
        let t = target.filter(|t| *t != i && *t < MAX_DESKTOPS).map_or(-1, |t| t as i64);
        Some(obj(vec![("w", Json::Int(w)), ("h", Json::Int(h)), ("target", Json::Int(t))]))
    }

    pub(crate) fn frames(&mut self, idle: bool) -> Vec<u8> {
        let mut out = self.handle();
        out.extend(self.pills_frame(idle));
        out.extend(self.pointer_frame(idle));
        out.extend(self.roll_frame(idle));
        out.extend(self.immersive_hover(idle));
        out
    }

    /// `_immersive_hover(idle)`: the 3D segment or tail under the dot lights, as a bar's segment does.
    fn immersive_hover(&mut self, idle: bool) -> Vec<u8> {
        let lit = self.drag.is_none() && self.mode == Mode::Free && !idle && self.overview();
        match &self.hover {
            Some(Hit::Immersive(i, part)) if lit => {
                let (i, tail) = (*i as i64, *part == "tail");
                self.control.immersive_pointer(i, Some(tail))
            }
            _ => self.control.immersive_pointer(-1, None),
        }
    }

    /// `_options_range_value(d)`: the value a held options slider stands at now, measured on the panel as it was at the
    /// press; None off its surface.
    fn options_range_value(&self, d: &Drag, range: og::RangeRow) -> Option<Num> {
        let Drag::OptionsRange { tile, placement, .. } = d else { return None };
        let g = self.geometry.as_ref()?;
        let q = fp::desktop_to_glass(tile, placement, fp::unproject(g, tile, placement, self.pointer?))?;
        Some(og::range_value(&range, Num::Float(q[0] - og::panel_rect(tile, &fp::pill_rect(tile))[0])))
    }

    pub(crate) fn apply_drag(&mut self) -> Vec<u8> {
        let Some(pointer) = self.pointer else { return Vec::new() };
        let Some(mut d) = self.drag.take() else { return Vec::new() };
        let out = self.apply(&mut d, pointer);
        // (the drag may have ended meanwhile: nothing in apply ends it)
        self.drag = Some(d);
        out
    }

    fn apply(&mut self, d: &mut Drag, pointer: [f64; 2]) -> Vec<u8> {
        let start = match d {
            Drag::Maximize { .. } => return Vec::new(),
            Drag::OptionsRange { range, .. } => {
                // the knob and value follow the hand; the owner hears at the release
                let range = *range;
                let value = self.options_range_value(d, range);
                let Drag::OptionsRange { row, capture, .. } = &*d else { unreachable!() };
                if let Some(value) = value
                    && self.control.options_capture().as_ref() == Some(capture)
                {
                    let preview = Json::Array(vec![json_get(row, "id").clone(), Json::Str(num_str(value))]);
                    if self.control.options_preview() != preview {
                        self.control.set_options_preview(preview);
                        self.control.options_refresh();
                        return self.control.options_frame();
                    }
                }
                return Vec::new();
            }
            Drag::Slider { index, field, tile, placement } => {
                // Measured on the panel as it was at the press, where the renderer keeps drawing it until the release
                // (the frame's `surface`): a setting that moves its own desktop would otherwise move the panel and chase
                // itself. A key that collapsed or moved the panel meanwhile dropped that surface: the rest of this drag
                // does nothing.
                if self.control.state().menu_surface.is_none() {
                    return Vec::new();
                }
                let roll = self.rolls.get(*index).copied().unwrap_or(0);
                let placed = Pose::rolled(*placement, roll as f64);
                let Some(g) = &self.geometry else { return Vec::new() };
                let Some(q) = fp::desktop_to_glass(tile, &placed, fp::unproject(g, tile, &placed, pointer)) else {
                    return Vec::new();
                };
                let fraction = fp::slider_fraction(q[0] - fp::panel_rect(tile)[0]);
                return self.control.pill_slide(*index, field, fraction, None);
            }
            Drag::Resize { start, .. }
            | Drag::Pill { start, .. }
            | Drag::Roll { start, .. }
            | Drag::Window { start, .. } => *start,
        };
        let (dx, dy) = (pointer[0] - start[0], pointer[1] - start[1]);
        let placements = match d {
            Drag::Window { lifted, .. } => {
                if !*lifted {
                    if !self.window_drag || py_hypot(dx, dy) <= WINDOW_DRAG_PX {
                        return Vec::new(); // still a click (or a hold: hold)
                    }
                    self.lift(d);
                }
                let at = fp::desktop_at(self.geometry.as_ref(), &self.rolled(), self.pointer);
                if let Drag::Window { target, .. } = d {
                    *target = at;
                }
                return Vec::new(); // the ghost and the target go out with the dot (pointer_frame)
            }
            Drag::Roll { moved, side, index, from, centre, .. } => {
                // the desktop turns with the dot round its centre, either way (a detent at level)
                if !*moved {
                    if py_hypot(dx, dy) <= CLICK_PX {
                        return Vec::new(); // still a click
                    }
                    *moved = true;
                    if *side != "level" {
                        self.roll_drags += 1;
                    }
                }
                if *side == "level" {
                    return Vec::new(); // a button: dragged off, its release does nothing
                }
                let [cx, cy] = *centre;
                let turn = (cy - pointer[1]).atan2(pointer[0] - cx) - (cy - start[1]).atan2(start[0] - cx);
                let turn = (turn + PI).rem_euclid(2.0 * PI) - PI; // counter-clockwise as you see it
                self.rolls[*index] = fp::roll_drag(self.tilt(*index), *from as f64, turn.to_degrees() * 100.0);
                return Vec::new(); // the `roll` frame goes out with the others (roll_frame)
            }
            Drag::Resize { index, handle, distance, tile, bump, .. } => {
                // a resize handle: the size it asks for, bumping what it grows into
                let Some(g) = &self.geometry else { return Vec::new() };
                let ([ax, ay], [bx, by]) = (fp::resize_reach(g, start), fp::resize_reach(g, pointer));
                let Ok(distance) = fp::resize_distance(*distance as f64, handle, bx - ax, by - ay, tile) else {
                    return Vec::new();
                };
                self.resize_to(*index, bump, distance)
            }
            Drag::Pill { index, xy, moved, orbit, .. } => {
                // a move handle (the pill, or the open panel's header strip): the turn round you, bumping what it meets
                if !*moved {
                    if py_hypot(dx, dy) <= CLICK_PX {
                        return Vec::new(); // still a click
                    }
                    *moved = true;
                    self.moves += 1;
                }
                let Some(g) = &self.geometry else { return Vec::new() };
                // the turn round you keeping the grabbed point under the dot, pushing what it meets (orbit.py)
                let rolls = self.rolls.iter().map(|r| *r as f64).collect();
                let drag = orbit.get_or_insert_with(|| PillDrag::new(g.clone(), *index, *xy, start, Some(rolls)));
                drag.follow(pointer, &self.control.state().placements)
            }
            _ => return Vec::new(),
        };
        let index = d.index();
        let c = &mut self.control;
        if placements == c.state().placements {
            return Vec::new();
        }
        if placements.iter().zip(&c.state().placements).enumerate().any(|(i, (p, q))| i != index && p != q) {
            self.bumps += 1;
        }
        c.state_mut().placements = placements;
        let due = c.clock() + MENU_SAVE_DELAY;
        c.set_save_due(due);
        let mut out = c.placement_frame();
        if c.state().menu_open {
            out.extend(c.menu_frame());
        }
        out
    }

    /// `_resize_to(d, distance)`: every desktop's placement for resize drag `d` (any resize handle of the hit-zone
    /// contract: a corner; the edges too where they are handles) asking for `distance`: the desktop takes it, growing
    /// pushes the desktops it runs into out of its way and with no room left it stops growing (bump.py; one Pusher for
    /// the whole drag).
    fn resize_to(&self, index: usize, bump: &mut Option<Pusher>, distance: i64) -> Vec<Placement> {
        let rolls = self.rolls.iter().map(|r| *r as f64).collect();
        let pusher = bump.get_or_insert_with(|| Pusher::new(self.geometry.clone(), index, Some(rolls)));
        pusher.move_to(&self.control.state().placements, &Patch { distance: Some(distance), ..Patch::default() }, None)
    }

    /// `_end_drag()`: release, idle, zoom-in, typing mode or a video: the drag ends and is saved at once. A held slider's
    /// panel goes back onto its desktop's live placement (a `placement_menu` frame without `surface`).
    pub(crate) fn end_drag(&mut self) -> Vec<u8> {
        let Some(d) = self.drag.take() else { return Vec::new() };
        if matches!(d, Drag::OptionsRange { .. }) {
            self.control.set_options_capture(None);
        }
        let out = if matches!(d, Drag::Slider { .. }) { self.control.pill_release() } else { Vec::new() };
        self.control.flush(); // saved at once on release (no-op if nothing changed)
        out
    }

    // --- frames ---------------------------------------------------------------------------------------------------

    /// `_send(make, state)`: a frame with the control's next request id. The pointer only builds frames that pass the
    /// checks (an invalid one is a bug here, as the Python's ValueError would be).
    pub(crate) fn send(&mut self, make: impl FnOnce(&str, &str, u64) -> Result<String>) -> Vec<u8> {
        let c = &mut self.control;
        let id = c.next_id();
        let frame = make(c.run_id(), c.session(), id).expect("the pointer's own frame is valid");
        c.set_next_id(id + 1);
        frame.into_bytes()
    }

    fn handle(&mut self) -> Vec<u8> {
        // The resize_handle frame lights a corner's cap; an edge has the dot's resize look only (look).
        let target = match (&self.drag, &self.hover) {
            (Some(Drag::Resize { index, handle, .. }), _) if CORNERS.contains(handle) => Some((*index, *handle, true)),
            (None, Some(Hit::Corner(i, corner))) if self.mode == Mode::Free => Some((*i, *corner, false)),
            _ => None,
        };
        let want = match target {
            Some((i, corner, active)) => obj(vec![
                ("visible", Json::Bool(true)),
                ("desktop", Json::Int(i as i64)),
                ("corner", Json::Str(corner.into())),
                ("active", Json::Bool(active)),
            ]),
            None => with(&self.shown, &[("visible", Json::Bool(false)), ("active", Json::Bool(false))]),
        };
        if want == self.shown {
            return Vec::new();
        }
        self.shown = want.clone();
        self.send(|r, s, id| fp::handle_frame(r, s, id, &want))
    }

    /// `_pills(idle)`: `move_handle`: the glass pills show under every desktop in the overview while the pointer moved
    /// in the last 30 s (OLED idle close; an open panel is drawn regardless). hover: the pointer is on that pill or the
    /// open panel's header (or holds it); active: that pill is being dragged (moved past CLICK_PX); gear: the pointer is
    /// on that desktop's options gear, which lights like a hovered pill.
    fn pills_frame(&mut self, idle: bool) -> Vec<u8> {
        let visible = self.handles_on() && !idle;
        let free = self.drag.is_none() && self.mode == Mode::Free;
        let (target, hover, active, gear) = match (&self.drag, &self.hover) {
            (Some(Drag::Pill { index, moved, .. }), _) => (Json::Int(*index as i64), true, *moved, false),
            (_, Some(Hit::Pill(i))) if free => (Json::Int(*i as i64), true, false, false),
            (_, Some(Hit::OptionsGear(i))) if free => (Json::Int(*i as i64), false, false, true),
            _ => (json_get(&self.pills, "desktop").clone(), false, false, false),
        };
        let want = obj(vec![
            ("visible", Json::Bool(visible)),
            ("desktop", target),
            ("hover", Json::Bool(hover && visible)),
            ("active", Json::Bool(active && visible)),
            ("gear", Json::Bool(gear && visible)),
        ]);
        if want == self.pills {
            return Vec::new();
        }
        self.pills = want.clone();
        self.send(|r, s, id| fp::pill_frame(r, s, id, &want))
    }

    /// `_roll_frame(idle)`: `roll`: every desktop's levelling roll, and the level bars and corner caps shown with the
    /// pills (pills: in the overview, not idle). hover / side: the desktop and segment under the dot or held, the only
    /// one lit; active: it is being dragged. A press dragged off the middle segment lights nothing: its release will do
    /// nothing.
    pub(crate) fn roll_frame(&mut self, idle: bool) -> Vec<u8> {
        let visible = self.handles_on() && !idle;
        let mut icons: Vec<Json> = self
            .icons()
            .iter()
            .map(|side| Json::Str(if visible { side.unwrap_or("none") } else { "none" }.into()))
            .take(MAX_DESKTOPS)
            .collect();
        icons.resize(MAX_DESKTOPS, Json::Str("none".into()));
        let free = self.drag.is_none() && self.mode == Mode::Free;
        let (mut hover, mut side, mut active) = match (&self.drag, &self.hover) {
            (Some(Drag::Roll { side: "level", moved, index, .. }), _) => {
                if *moved { (-1, "none", false) } else { (*index as i64, "level", false) }
            }
            (Some(Drag::Roll { index, side, moved, .. }), _) => (*index as i64, *side, *moved),
            (_, Some(Hit::Roll(i, s))) if free => (*i as i64, *s, false),
            _ => (-1, "none", false),
        };
        if !visible {
            (hover, side, active) = (-1, "none", false);
        }
        let mut rolls: Vec<Json> =
            self.rolls.iter().take(MAX_DESKTOPS).map(|r| Json::Int((*r).clamp(-MAX_ROLL, MAX_ROLL))).collect();
        rolls.resize(MAX_DESKTOPS, Json::Int(0));
        let want = obj(vec![
            ("roll", Json::Array(rolls)),
            ("icons", Json::Array(icons)),
            ("hover", Json::Int(hover)),
            ("side", Json::Str(side.into())),
            ("active", Json::Bool(active)),
        ]);
        if want == self.roll_sent {
            return Vec::new();
        }
        self.roll_sent = want.clone();
        self.send(|r, s, id| fp::roll_frame(r, s, id, &want))
    }

    /// `_held()`: a button held on a window (unified_cursor: the non-consuming click binds); none here.
    fn held(&self) -> bool {
        if self.ext.is_some() {
            return self.u_held();
        }
        false
    }

    /// `_carry(dx, dy, now)`: carry mode's motion (unified_cursor.UnifiedPointer); no such mode here.
    fn carry(&mut self, dx: f64, dy: f64, now: f64) -> Vec<u8> {
        if self.ext.is_some() {
            return self.u_carry(dx, dy, now);
        }
        Vec::new()
    }

    /// `_text_allowed()`: the beam's gate. The dot is the real cursor on a desktop's content (ZONES: an open panel over
    /// it or the sliver at its edge is not), the run holds nothing, and the handles are on: the overview, never
    /// maximized, in typing mode, during a video or with the key bind panel.
    fn text_allowed(&mut self) -> bool {
        if self.ext.as_ref().is_some_and(|u| u.super_drag.is_some()) {
            return false;
        }
        self.mode == Mode::Desktop && self.drag.is_none() && self.handles_on() && {
            let h = self.hit();
            fp::zone_of(h.as_ref()) == "content"
        }
    }

    /// `_step_text(now)`: this tick's beam (TextHold). Off at once when the gate shuts: a handle wins with no wait, and a
    /// word from before counts for nothing, so back in the overview the dot waits for a fresh read (shape_gated: no push
    /// comes for a cursor that did not change).
    fn step_text(&mut self, now: f64) {
        let text = self.shape_text;
        if !text && !self.text_hold.on && !self.shape_gated {
            return;
        }
        if self.text_allowed() {
            self.shape_gated = false;
            self.text_hold.step(text, now);
            return;
        }
        self.shape_gated = true;
        self.text_hold.clear();
        self.shape_text = false;
    }

    /// `_look()`: the dot's look (POINTER_LOOKS; None: the plain dot), the zone contract on screen: what it holds, else
    /// what it is over in free mode. In desktop mode it is on a desktop's content, where it is the real cursor: the beam
    /// where the app asks for a text cursor (step_text), else plain.
    fn look(&self) -> Option<&'static str> {
        if self.ext.is_some() {
            return self.u_look();
        }
        self.look_base()
    }

    pub(crate) fn look_base(&self) -> Option<&'static str> {
        if let Some(d) = &self.drag {
            return match d {
                Drag::Roll { side: "level", moved: true, .. } => None, // dragged off the button
                Drag::Resize { handle, .. } => RESIZE_LOOKS.iter().find(|(k, _)| k == handle).map(|(_, l)| *l),
                Drag::Pill { .. } => Some("move"),
                Drag::Roll { .. } => Some("rotate"),
                _ => None,
            };
        }
        if self.mode == Mode::Free {
            return fp::look_for(self.hover.as_ref());
        }
        (self.mode == Mode::Desktop && self.text_hold.on).then_some("text")
    }

    /// `_pointer_frame(idle)`: the dot: in desktop and free mode (the real cursor is hidden in both: overview_cursor.py),
    /// hidden when idle.
    pub(crate) fn pointer_frame(&mut self, idle: bool) -> Vec<u8> {
        let want = match (self.pointer, &self.geometry) {
            (Some(p), Some(g)) if self.mode.dot() && !idle && self.active() => {
                // Always drawn in its direction (linear, anywhere round you), at the whole canvas pixel the frame carries.
                let (px, py) = ((p[0] + 0.5).floor(), (p[1] + 0.5).floor());
                let (az, el) = orbit::pointer_direction(g, [px, py]);
                let (x, y) = (clamp_i(px as i64, MAX_POINTER_PX), clamp_i(py as i64, MAX_POINTER_PX));
                let mut want = vec![
                    ("visible", Json::Bool(true)),
                    ("x", Json::Int(x)),
                    ("y", Json::Int(y)),
                    ("held", Json::Bool(self.drag.is_some() || self.held())),
                    ("az", Json::Int(hundredths(az, MAX_POINTER_AZ))),
                    ("el", Json::Int(hundredths(el, MAX_POINTER_EL))),
                ];
                if let Some(drag) = self.drag_display() {
                    want.push(("drag", drag));
                }
                if let Some(look) = self.look() {
                    want.push(("look", Json::Str(look.into())));
                }
                obj(want)
            }
            _ => {
                let kept: Vec<(String, Json)> = self
                    .sent
                    .as_object()
                    .unwrap_or(&[])
                    .iter()
                    .filter(|(k, _)| k != "drag" && k != "look")
                    .cloned()
                    .collect();
                with(&Json::Object(kept), &[("visible", Json::Bool(false)), ("held", Json::Bool(false))])
            }
        };
        if want == self.sent {
            return Vec::new();
        }
        self.sent = want.clone();
        self.send(|r, s, id| fp::overview_pointer_frame(r, s, id, &want))
    }
}

/// What `on_op`'s press branch ends in: its own return, or the frames after it.
enum Pressed {
    Return(Option<Vec<u8>>),
    Frames,
}

/// `_window_press(i, q, start, point, now)`: a press on desktop i at tile pixel q (dot at canvas `start`, layout pixel
/// `point` on its monitor): a window drag not lifted yet. Released as it is, it is a click.
pub(crate) fn window_press(i: usize, q: [f64; 2], start: [f64; 2], point: Option<(i64, i64)>, now: f64) -> Drag {
    Drag::Window { index: i, on: q, start, at: now, point, lifted: false, window: Window::Pending, target: None, token: None }
}

/// Python's truth of a JSON value.
pub(crate) fn truthy(j: &Json) -> bool {
    match j {
        Json::Null => false,
        Json::Bool(b) => *b,
        Json::Int(v) => *v != 0,
        Json::Float(v) => *v != 0.0,
        Json::Str(s) => !s.is_empty(),
        Json::Array(a) => !a.is_empty(),
        Json::Object(o) => !o.is_empty(),
    }
}

/// `{**d, k: v, ..}`: `d` with those keys set, a new key at the end.
fn with(d: &Json, set: &[(&str, Json)]) -> Json {
    let mut entries = d.as_object().unwrap_or(&[]).to_vec();
    for (k, v) in set {
        match entries.iter_mut().find(|(n, _)| n == k) {
            Some(e) => e.1 = v.clone(),
            None => entries.push((k.to_string(), v.clone())),
        }
    }
    Json::Object(entries)
}
