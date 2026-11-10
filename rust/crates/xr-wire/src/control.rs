//! The supervisor-to-renderer control wire (protocol 3 in `docs/rust-rebuild-plan.md`): one JSON
//! object per line with exactly the keys `schema`, `request_id`, `run_id`, `session`, `op` and
//! `payload`, for the 23 ops the renderer takes.
//!
//! [`decode`] ports `decode_control` in `spike/src/ipc.cpp` (and `decode_video_payload` in
//! `spike/src/video_ipc.cpp`) check for check, with the same refusal codes and details. [`Stream`]
//! ports `ControlStream` with its `ControlQueue`: line splitting, the run and session identity, the
//! replay floor and the overflow skip. [`encode`] writes a frame byte for byte as the Python sender
//! of its op does. `tests/control_corpus.rs` holds all three to
//! `tests/native_spike/fixtures/control_frames.json` and the `*_payloads.json` fixtures.
//!
//! Payloads decode into typed values in wire units: the edge pan's fractions stay ten-thousandths,
//! where the C++ turns `turn` and `way` into radians as it decodes.
//!
//! Number typing follows nlohmann: a non-negative integer literal is unsigned, a negative one signed,
//! anything with a fraction or exponent, or past the 64-bit range, a float. Where they differ, the
//! Rust is stricter: `-0` is a float to serde_json, so it is refused where an integer is required,
//! and the nesting limit is checked before parsing, so a frame both too deep and malformed elsewhere
//! is refused as `nesting limit`, not `not JSON`.

use std::collections::BTreeSet;

use crate::json::{self, Style, Value};

/// The largest `options_menu` frame, and the longest run of bytes without a newline a [`Stream`]
/// keeps (`kMaxControlPending`).
pub const MAX_OPTIONS_MENU_FRAME: usize = 524_288;
/// The largest frame of any other op.
pub const MAX_FRAME: usize = 16_384;
/// The most arrays and objects a value may sit inside.
pub const MAX_DEPTH: usize = json::MAX_DEPTH;

const MAX_DESKTOPS: usize = 4;
const SCALE_PERCENTS: [u64; 7] = [100, 125, 150, 175, 200, 250, 300];
const BACKGROUNDS: [&str; 3] = ["none", "grid", "smoke"];

/// The 23 control ops, in `ControlOp` order.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum Op {
    Status,
    Start,
    Stop,
    Select,
    Recover,
    View,
    Recenter,
    Style,
    Help,
    Placement,
    PlacementMenu,
    OptionsMenu,
    ResizeHandle,
    Video,
    MoveHandle,
    OverviewPointer,
    Roll,
    Desktops,
    Environment,
    Binds,
    EdgePan,
    Immersive,
    RestoreBand,
}

impl Op {
    pub const ALL: [Op; 23] = [
        Op::Status,
        Op::Start,
        Op::Stop,
        Op::Select,
        Op::Recover,
        Op::View,
        Op::Recenter,
        Op::Style,
        Op::Help,
        Op::Placement,
        Op::PlacementMenu,
        Op::OptionsMenu,
        Op::ResizeHandle,
        Op::Video,
        Op::MoveHandle,
        Op::OverviewPointer,
        Op::Roll,
        Op::Desktops,
        Op::Environment,
        Op::Binds,
        Op::EdgePan,
        Op::Immersive,
        Op::RestoreBand,
    ];

    /// The op's name on the wire.
    pub fn name(self) -> &'static str {
        match self {
            Op::Status => "status",
            Op::Start => "start",
            Op::Stop => "stop",
            Op::Select => "select",
            Op::Recover => "recover",
            Op::View => "view",
            Op::Recenter => "recenter",
            Op::Style => "style",
            Op::Help => "help",
            Op::Placement => "placement",
            Op::PlacementMenu => "placement_menu",
            Op::OptionsMenu => "options_menu",
            Op::ResizeHandle => "resize_handle",
            Op::Video => "video",
            Op::MoveHandle => "move_handle",
            Op::OverviewPointer => "overview_pointer",
            Op::Roll => "roll",
            Op::Desktops => "desktops",
            Op::Environment => "environment",
            Op::Binds => "binds",
            Op::EdgePan => "edge_pan",
            Op::Immersive => "immersive",
            Op::RestoreBand => "restore_band",
        }
    }

    pub fn from_name(name: &str) -> Option<Op> {
        Op::ALL.into_iter().find(|op| op.name() == name)
    }

    /// The code a malformed payload of this op is refused with, and its detail.
    fn payload_refusal(self) -> Refusal {
        let code = match self {
            Op::OptionsMenu => "invalid_options_menu",
            Op::PlacementMenu => "invalid_menu",
            Op::ResizeHandle => "invalid_resize_handle",
            Op::RestoreBand => "invalid_restore_band",
            Op::MoveHandle => "invalid_move_handle",
            Op::OverviewPointer => "invalid_overview_pointer",
            Op::EdgePan => "invalid_edge_pan",
            Op::Roll => "invalid_roll",
            Op::Immersive => "invalid_immersive",
            Op::Binds => "invalid_binds",
            Op::Environment => "invalid_environment",
            _ => return invalid(format!("bad {} payload", self.name())),
        };
        Refusal {
            code,
            detail: format!("Ignored malformed {} payload", self.name()),
        }
    }
}

/// Why a frame was refused: `Error{code, detail}` in `ipc.hpp`.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Refusal {
    pub code: &'static str,
    pub detail: String,
}

impl Refusal {
    /// `ignorable_control_error`: a malformed display-only payload, which stops nothing even under
    /// `--control-errors fatal`. `invalid_edge_pan` and `invalid_restore_band` are not among them.
    pub fn ignorable(&self) -> bool {
        matches!(
            self.code,
            "invalid_options_menu"
                | "invalid_menu"
                | "invalid_resize_handle"
                | "invalid_move_handle"
                | "invalid_overview_pointer"
                | "invalid_roll"
                | "invalid_environment"
                | "invalid_binds"
                | "invalid_immersive"
        )
    }
}

fn invalid(detail: impl Into<String>) -> Refusal {
    Refusal {
        code: "invalid_control",
        detail: detail.into(),
    }
}

/// An accepted frame.
#[derive(Clone, Debug, PartialEq)]
pub struct Message {
    pub request_id: u64,
    pub run_id: String,
    pub session: String,
    pub op: Op,
    pub payload: Payload,
    /// The payload as it came, keys in frame order, for [`encode`].
    pub raw_payload: Value,
}

/// A desktop's placement: tenths of a degree left/right and down/up, distance and curvature.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Placement {
    pub x: i32,
    pub y: i32,
    pub distance: i32,
    pub curvature: i32,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ViewMode {
    Anchor,
    Locked,
    Follow,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct View {
    pub zoomed: bool,
    pub selected: u32,
    pub spacing: u32,
    pub mode: ViewMode,
    pub cursor_follow: bool,
    pub prediction: bool,
    pub typing: bool,
    pub center: bool,
    pub cut: bool,
    pub pan: bool,
    /// 25 when absent.
    pub stabilization: u32,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Help {
    pub visible: bool,
    pub rows: Vec<(String, String)>,
    pub sections: Vec<u32>,
    /// 1, -1, or 0 when absent.
    pub turn: i32,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Placements {
    pub desktops: Vec<Placement>,
    /// 0 when absent.
    pub glide_ms: u32,
}

/// `placement_menu`'s `field`: a placement's field, or the background's brightness.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum MenuField {
    X,
    Y,
    Distance,
    Curvature,
    Brightness,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct PlacementMenu {
    pub open: bool,
    pub desktop: u32,
    pub field: MenuField,
    pub placements: Vec<Placement>,
    /// The desktops' output numbers, 1..=4; 1, 2, 3, 4 when absent.
    pub numbers: Vec<u8>,
    pub wide: bool,
    pub armed: bool,
    pub busy: bool,
    pub picker: bool,
    pub resolution_picker: bool,
    pub background_picker: bool,
    pub resolution: Option<(u16, u16)>,
    /// Index into none, grid, smoke.
    pub background: Option<u8>,
    pub brightness: Option<u16>,
    pub scale: Option<u16>,
    /// A bit per entry of the scale steps 100..300.
    pub scales: u8,
    pub surface: Option<Placement>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct OptionsRow {
    pub id: String,
    pub label: String,
    pub scope: String,
    pub kind: String,
    pub value: String,
    pub unit: String,
    pub minimum: f64,
    pub maximum: f64,
    pub step: f64,
    pub enabled: bool,
    pub choices: Vec<String>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct OptionsMenu {
    pub open: bool,
    pub namespace: String,
    pub monitor: String,
    pub scope: String,
    pub category: String,
    pub focus: i32,
    pub scroll: u32,
    pub revision: u32,
    pub status: String,
    pub message: String,
    pub rows: Vec<OptionsRow>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Corner {
    TopLeft,
    TopRight,
    BottomLeft,
    BottomRight,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct ResizeHandle {
    pub visible: bool,
    pub desktop: u32,
    pub corner: Corner,
    pub active: bool,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Projection {
    Equirect360,
    Equirect180,
    Fisheye,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Stereo {
    Mono,
    Sbs,
    Tb,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct VideoFormat {
    pub projection: Projection,
    pub stereo: Stereo,
    pub fov_deg: u32,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum VideoFilter {
    CatmullRom,
    Sharp,
    /// Also the first menu's `linear`.
    Bilinear,
    Nearest,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Video {
    Open {
        format: VideoFormat,
        title: String,
    },
    Format(VideoFormat),
    Close,
    Pause,
    Controls {
        visible: bool,
    },
    Quality {
        filter: VideoFilter,
        deband: bool,
    },
    Menu {
        visible: bool,
        selected: u32,
        rows: Vec<(String, String)>,
    },
    Seek {
        seconds: i32,
    },
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct MoveHandle {
    pub visible: bool,
    pub desktop: u32,
    pub hover: bool,
    pub active: bool,
    pub gear: bool,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum PointerLook {
    Plain,
    ResizeEw,
    ResizeNs,
    ResizeNwse,
    ResizeNesw,
    Move,
    Rotate,
    Text,
}

/// The ghost a drag in the overview draws: its size and the desktop it would land on (-1 none).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Ghost {
    pub w: i32,
    pub h: i32,
    pub target: i32,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct OverviewPointer {
    pub visible: bool,
    pub x: i32,
    pub y: i32,
    pub held: bool,
    /// Azimuth and elevation in hundredths of a degree, when the frame has them.
    pub direction: Option<(i32, i32)>,
    pub drag: Option<Ghost>,
    pub look: PointerLook,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum RollSide {
    None,
    Ccw,
    Level,
    Cw,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Roll {
    pub roll: [i32; 4],
    pub bars: [bool; 4],
    pub hover: i32,
    pub side: RollSide,
    pub active: bool,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Desktops {
    /// Sorted.
    pub outputs: Vec<String>,
    pub renew: Vec<String>,
    pub ease: bool,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Environment {
    /// Index into none, grid, smoke.
    pub background: u8,
    pub brightness: u16,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum BindState {
    Normal,
    Custom,
    Conflict,
    Capture,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct BindRow {
    pub action: String,
    pub keys: String,
    pub note: String,
    pub state: BindState,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Binds {
    pub visible: bool,
    pub title: String,
    pub hint: String,
    pub status: String,
    pub alert: bool,
    pub selected: u32,
    pub rows: Vec<BindRow>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum PanSide {
    Right,
    Left,
    Up,
    Down,
}

/// An edge pan in wire units: every fraction in ten-thousandths (`turn` of half a turn, `way` of a
/// whole one).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct EdgePanMove {
    pub from: u32,
    pub to: u32,
    pub side: PanSide,
    pub turn: u16,
    pub way: u16,
    pub exit: u16,
    pub entry: u16,
    pub peek: u16,
    pub cross: u16,
    pub zoom: u16,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct ImmersiveParams {
    pub strength: i32,
    pub convergence: i32,
    pub near_arcmin: i32,
    pub far_arcmin: i32,
    pub text: i32,
    pub range_ms: i32,
    pub strengths: Option<[i32; 4]>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Immersive {
    pub desktops: [bool; 4],
    pub global: bool,
    pub stereo: bool,
    pub hover: i32,
    pub tail: bool,
    pub params: ImmersiveParams,
}

/// A decoded payload, one variant per op shape.
#[derive(Clone, Debug, PartialEq)]
pub enum Payload {
    /// `status`, `start`, `stop`, `recover` and `recenter`.
    Empty,
    Select {
        output_id: String,
    },
    View(View),
    Style {
        border: [u8; 3],
    },
    Help(Help),
    Placement(Placements),
    PlacementMenu(PlacementMenu),
    OptionsMenu(OptionsMenu),
    ResizeHandle(ResizeHandle),
    Video(Video),
    MoveHandle(MoveHandle),
    OverviewPointer(OverviewPointer),
    Roll(Roll),
    Desktops(Desktops),
    Environment(Environment),
    Binds(Binds),
    /// `None` when the pan is inactive.
    EdgePan(Option<EdgePanMove>),
    Immersive(Immersive),
    RestoreBand {
        open: bool,
    },
}

// ---- Value helpers: nlohmann's type tests on our `Value`.

fn object(v: &Value) -> Option<&[(String, Value)]> {
    match v {
        Value::Object(e) => Some(e),
        _ => None,
    }
}

fn array(v: &Value) -> Option<&[Value]> {
    match v {
        Value::Array(a) => Some(a),
        _ => None,
    }
}

fn boolean(v: &Value) -> Option<bool> {
    match v {
        Value::Bool(b) => Some(*b),
        _ => None,
    }
}

/// `is_number_unsigned()`.
fn unsigned(v: &Value) -> Option<u64> {
    match v {
        Value::Number(n) => n.as_u64(),
        _ => None,
    }
}

/// `is_number_integer()`, widened so no bound check can wrap.
fn integer(v: &Value) -> Option<i128> {
    v.as_int()
}

fn int_in(v: &Value, lo: i128, hi: i128) -> Option<i32> {
    integer(v)
        .filter(|n| (lo..=hi).contains(n))
        .map(|n| n as i32)
}

fn uint_in(v: &Value, lo: u64, hi: u64) -> Option<u32> {
    unsigned(v)
        .filter(|n| (lo..=hi).contains(n))
        .map(|n| n as u32)
}

fn len(v: &Value) -> usize {
    match v {
        Value::Object(e) => e.len(),
        Value::Array(a) => a.len(),
        _ => 0,
    }
}

fn has(v: &Value, key: &str) -> bool {
    v.get(key).is_some()
}

/// An object with exactly `keys` (keys never repeat, so the count and membership suffice).
fn exact(v: &Value, keys: &[&str]) -> bool {
    object(v).is_some_and(|e| e.len() == keys.len()) && keys.iter().all(|k| has(v, k))
}

/// A string of at most `n` bytes.
fn short_str(v: &Value, n: usize) -> Option<&str> {
    v.as_str().filter(|s| s.len() <= n)
}

fn one_of(v: &Value, names: &[&str]) -> Option<usize> {
    let s = v.as_str()?;
    names.iter().position(|n| *n == s)
}

/// Printable ASCII of `lo..=hi` bytes.
fn ascii(v: &Value, lo: usize, hi: usize) -> Option<String> {
    let s = v.as_str()?;
    ((lo..=hi).contains(&s.len()) && s.bytes().all(|c| (0x20..0x7f).contains(&c)))
        .then(|| s.to_owned())
}

/// An identity or output id: 1..=128 bytes of `[A-Za-z0-9_.:-]`, starting alphanumeric.
pub fn is_token(s: &str) -> bool {
    let b = s.as_bytes();
    !b.is_empty()
        && b.len() <= 128
        && b[0].is_ascii_alphanumeric()
        && b.iter()
            .all(|c| c.is_ascii_alphanumeric() || b"_.:-".contains(c))
}

fn token(v: &Value) -> Option<&str> {
    v.as_str().filter(|s| is_token(s))
}

// ---- Payloads. Each returns `None` for a payload the renderer refuses.

const PLACEMENT_BOUNDS: [(&str, i128, i128); 4] = [
    ("x", -1800, 1800),
    ("y", -900, 900),
    ("distance", 10, 200),
    ("curvature", 0, 120),
];

fn placement(v: &Value) -> Option<Placement> {
    if object(v)?.len() != PLACEMENT_BOUNDS.len() {
        return None;
    }
    let mut f = [0i32; 4];
    for (i, (key, lo, hi)) in PLACEMENT_BOUNDS.into_iter().enumerate() {
        f[i] = int_in(v.get(key)?, lo, hi)?;
    }
    Some(Placement {
        x: f[0],
        y: f[1],
        distance: f[2],
        curvature: f[3],
    })
}

fn select(p: &Value) -> Option<Payload> {
    if len(p) != 1 {
        return None;
    }
    Some(Payload::Select {
        output_id: token(p.get("output_id")?)?.to_owned(),
    })
}

fn view(p: &Value) -> Option<View> {
    let optional_bool = |key| match p.get(key) {
        None => Some(false),
        Some(v) => boolean(v),
    };
    let (center, cut, pan) = (
        optional_bool("center")?,
        optional_bool("cut")?,
        optional_bool("pan")?,
    );
    let extra = ["center", "cut", "pan", "stabilization"]
        .iter()
        .filter(|k| has(p, k))
        .count();
    if len(p) != 7 + extra {
        return None;
    }
    let typing = boolean(p.get("typing")?)?;
    let prediction = boolean(p.get("prediction")?)?;
    let cursor_follow = boolean(p.get("cursor_follow")?)?;
    let zoomed = boolean(p.get("zoomed")?)?;
    let selected = unsigned(p.get("selected")?).filter(|&n| n < MAX_DESKTOPS as u64)? as u32;
    let spacing = uint_in(p.get("spacing")?, 0, 400)?;
    let mode = p.get("mode")?.as_str()?;
    let stabilization = match p.get("stabilization") {
        None => 25,
        Some(v) => uint_in(v, 0, 100)?,
    };
    let mode = match mode {
        "anchor" => ViewMode::Anchor,
        "locked" => ViewMode::Locked,
        "follow" => ViewMode::Follow,
        _ => return None,
    };
    Some(View {
        zoomed,
        selected,
        spacing,
        mode,
        cursor_follow,
        prediction,
        typing,
        center,
        cut,
        pan,
        stabilization,
    })
}

fn style(p: &Value) -> Option<Payload> {
    let border = array(p.get("border")?)?;
    if len(p) != 1 || border.len() != 3 {
        return None;
    }
    let mut c = [0u8; 3];
    for (i, v) in border.iter().enumerate() {
        c[i] = uint_in(v, 0, 255)? as u8;
    }
    Some(Payload::Style { border: c })
}

const MAX_HELP_ROWS: usize = 64;
const MAX_HELP_TEXT: usize = 48;

fn help(p: &Value) -> Option<Help> {
    let visible = boolean(p.get("visible")?)?;
    let rows = array(p.get("rows")?)?;
    if rows.len() > MAX_HELP_ROWS
        || len(p) != 2 + has(p, "sections") as usize + has(p, "turn") as usize
    {
        return None;
    }
    let mut help = Help {
        visible,
        rows: Vec::new(),
        sections: Vec::new(),
        turn: 0,
    };
    for row in rows {
        let cells = array(row).filter(|c| c.len() == 2)?;
        help.rows.push((
            ascii(&cells[0], 1, MAX_HELP_TEXT)?,
            ascii(&cells[1], 1, MAX_HELP_TEXT)?,
        ));
    }
    if let Some(sections) = p.get("sections") {
        let sections = array(sections).filter(|s| s.len() <= MAX_HELP_ROWS)?;
        for n in sections {
            help.sections.push(uint_in(n, 1, MAX_HELP_ROWS as u64)?);
        }
        if help.sections.iter().map(|&n| n as usize).sum::<usize>() != help.rows.len() {
            return None;
        }
    }
    if let Some(turn) = p.get("turn") {
        help.turn = integer(turn).filter(|&t| t == 1 || t == -1)? as i32;
    }
    Some(help)
}

fn placements(p: &Value) -> Option<Placements> {
    let glide = p.get("glide");
    let desktops = array(p.get("desktops")?)?;
    if len(p) != 1 + glide.is_some() as usize
        || desktops.is_empty()
        || desktops.len() > MAX_DESKTOPS
    {
        return None;
    }
    let glide_ms = match glide {
        None => 0,
        Some(g) => uint_in(g, 1, 1000)?,
    };
    Some(Placements {
        desktops: desktops.iter().map(placement).collect::<Option<_>>()?,
        glide_ms,
    })
}

fn options_menu(p: &Value, session: &str) -> Option<OptionsMenu> {
    const KEYS: [&str; 11] = [
        "open",
        "namespace",
        "monitor",
        "scope",
        "category",
        "focus",
        "scroll",
        "revision",
        "status",
        "message",
        "rows",
    ];
    const CATEGORIES: [&str; 11] = [
        "3d",
        "stabilization",
        "layout",
        "view",
        "video",
        "input",
        "keybinds",
        "windows",
        "privacy",
        "help",
        "session",
    ];
    const ROW_KEYS: [&str; 11] = [
        "id", "label", "scope", "kind", "value", "minimum", "maximum", "step", "unit", "enabled",
        "choices",
    ];
    const KINDS: [&str; 6] = ["action", "toggle", "range", "choice", "text", "readonly"];
    if !exact(p, &KEYS) {
        return None;
    }
    let open = boolean(p.get("open")?)?;
    let namespace = short_str(p.get("namespace")?, 64).filter(|s| *s == session)?;
    let monitor = short_str(p.get("monitor")?, 32)?;
    let m = monitor.as_bytes();
    if m.len() < 4
        || !monitor.starts_with("XR-")
        || m[3] == b'0'
        || !m[3..].iter().all(u8::is_ascii_digit)
    {
        return None;
    }
    let scope = p.get("scope")?.as_str()?;
    one_of(p.get("scope")?, &["monitor", "workspace"])?;
    let category = &CATEGORIES[one_of(p.get("category")?, &CATEGORIES)?];
    let status = ["idle", "pending", "applied", "error"]
        [one_of(p.get("status")?, &["idle", "pending", "applied", "error"])?];
    let focus = int_in(p.get("focus")?, -4, 63)?;
    let scroll = int_in(p.get("scroll")?, 0, 4096)? as u32;
    let revision = integer(p.get("revision")?).filter(|n| (0..=4_294_967_295).contains(n))? as u32;
    let message = short_str(p.get("message")?, 256)?;
    let rows = array(p.get("rows")?).filter(|r| r.len() <= 64)?;
    let mut ids = BTreeSet::new();
    let mut out = Vec::new();
    for r in rows {
        let entries = object(r)?;
        if entries.iter().any(|(k, _)| !ROW_KEYS.contains(&k.as_str())) {
            return None;
        }
        let ranged = r.get("kind").and_then(Value::as_str) == Some("range");
        let number = |key| match r.get(key) {
            None if ranged => None,
            None => Some(0.0),
            Some(Value::Number(n)) => n.as_f64().filter(|x| x.is_finite()),
            Some(_) => None,
        };
        let id = short_str(r.get("id")?, 64)?;
        let label = short_str(r.get("label")?, 96)?;
        let value = short_str(r.get("value")?, 256)?;
        let unit = match r.get("unit") {
            None => "",
            Some(u) => short_str(u, 16)?,
        };
        let enabled = boolean(r.get("enabled")?)?;
        if r.get("scope")?.as_str() != Some(scope) {
            return None;
        }
        let kind = KINDS[one_of(r.get("kind")?, &KINDS)?];
        if !ids.insert(id) {
            return None;
        }
        let (minimum, maximum, step) = (number("minimum")?, number("maximum")?, number("step")?);
        let choices = match r.get("choices") {
            None => Vec::new(),
            Some(c) => array(c)
                .filter(|c| c.len() <= 32)?
                .iter()
                .map(|c| short_str(c, 96).map(str::to_owned))
                .collect::<Option<_>>()?,
        };
        out.push(OptionsRow {
            id: id.into(),
            label: label.into(),
            scope: scope.into(),
            kind: kind.into(),
            value: value.into(),
            unit: unit.into(),
            minimum,
            maximum,
            step,
            enabled,
            choices,
        });
    }
    Some(OptionsMenu {
        open,
        namespace: namespace.into(),
        monitor: monitor.into(),
        scope: scope.into(),
        category: (*category).into(),
        focus,
        scroll,
        revision,
        status: status.into(),
        message: message.into(),
        rows: out,
    })
}

fn background_name(v: &Value) -> Option<u8> {
    one_of(v, &BACKGROUNDS).map(|i| i as u8)
}

fn brightness_percent(v: &Value) -> Option<u16> {
    uint_in(v, 10, 200).map(|n| n as u16)
}

fn placement_menu(p: &Value) -> Option<PlacementMenu> {
    const OPTIONAL: [&str; 13] = [
        "surface",
        "names",
        "wide",
        "armed",
        "busy",
        "scale",
        "scales",
        "picker",
        "resolution",
        "resolution_picker",
        "background",
        "brightness",
        "background_picker",
    ];
    object(p)?;
    let optional = OPTIONAL.iter().filter(|k| has(p, k)).count();
    let open = boolean(p.get("open")?)?;
    let list = array(p.get("placements")?)?;
    if len(p) != 4 + optional || list.is_empty() || list.len() > MAX_DESKTOPS {
        return None;
    }
    let desktop = unsigned(p.get("desktop")?).filter(|&d| d < list.len() as u64)? as u32;
    let field = match p.get("field")?.as_str()? {
        "x" => MenuField::X,
        "y" => MenuField::Y,
        "distance" => MenuField::Distance,
        "curvature" => MenuField::Curvature,
        "brightness" => MenuField::Brightness,
        _ => return None,
    };
    let placements = list.iter().map(placement).collect::<Option<Vec<_>>>()?;
    let mut numbers: Vec<u8> = (1..=list.len() as u8).collect();
    if let Some(names) = p.get("names") {
        let names = array(names).filter(|n| n.len() == list.len())?;
        let mut last = 0;
        for (i, n) in names.iter().enumerate() {
            let n = unsigned(n).filter(|&n| n > last && n <= MAX_DESKTOPS as u64)?;
            numbers[i] = n as u8;
            last = n;
        }
    }
    let flag = |key| match p.get(key) {
        None => Some(false),
        Some(v) => boolean(v),
    };
    let (wide, armed, busy) = (flag("wide")?, flag("armed")?, flag("busy")?);
    let (picker, resolution_picker, background_picker) = (
        flag("picker")?,
        flag("resolution_picker")?,
        flag("background_picker")?,
    );
    let pickers = picker as u8 + resolution_picker as u8 + background_picker as u8;
    if (pickers > 0 && !open) || pickers > 1 {
        return None;
    }
    let resolution = match p.get("resolution") {
        None => None,
        Some(r) => {
            let r = array(r).filter(|r| r.len() == 2)?;
            let (w, h) = (unsigned(&r[0])?, unsigned(&r[1])?);
            if !(640..=7680).contains(&w) || !(480..=4320).contains(&h) || w % 2 != 0 || h % 2 != 0
            {
                return None;
            }
            Some((w as u16, h as u16))
        }
    };
    let background = match p.get("background") {
        None => None,
        Some(v) => Some(background_name(v)?),
    };
    let brightness = match p.get("brightness") {
        None => None,
        Some(v) => Some(brightness_percent(v)?),
    };
    let step = |v: &Value| unsigned(v).and_then(|n| SCALE_PERCENTS.iter().position(|&s| s == n));
    let scale = match p.get("scale") {
        None => None,
        Some(v) => Some(SCALE_PERCENTS[step(v)?] as u16),
    };
    let mut scales = 0u8;
    if let Some(list) = p.get("scales") {
        let list = array(list).filter(|l| l.len() <= SCALE_PERCENTS.len())?;
        let mut last = None;
        for v in list {
            let i = step(v)?;
            if last.is_some_and(|l| i <= l) {
                return None;
            }
            scales |= 1 << i;
            last = Some(i);
        }
    }
    let surface = match p.get("surface") {
        None => None,
        Some(_) if !open => return None,
        Some(v) => Some(placement(v)?),
    };
    Some(PlacementMenu {
        open,
        desktop,
        field,
        placements,
        numbers,
        wide,
        armed,
        busy,
        picker,
        resolution_picker,
        background_picker,
        resolution,
        background,
        brightness,
        scale,
        scales,
        surface,
    })
}

/// `XR-1`..`XR-4`, each at most once, `min..=4` of them.
fn desktop_names(v: &Value, min: usize) -> Option<Vec<String>> {
    let list = array(v).filter(|l| (min..=MAX_DESKTOPS).contains(&l.len()))?;
    let mut out: Vec<String> = Vec::new();
    for n in list {
        let name = n.as_str()?;
        let b = name.as_bytes();
        if b.len() != 4
            || !name.starts_with("XR-")
            || !(b'1'..=b'0' + MAX_DESKTOPS as u8).contains(&b[3])
        {
            return None;
        }
        if out.iter().any(|o| o == name) {
            return None;
        }
        out.push(name.to_owned());
    }
    Some(out)
}

fn desktops(p: &Value) -> Option<Desktops> {
    if len(p) != 1 + has(p, "renew") as usize + has(p, "ease") as usize {
        return None;
    }
    let mut outputs = desktop_names(p.get("outputs")?, 1)?;
    let ease = match p.get("ease") {
        None => false,
        Some(v) => boolean(v)?,
    };
    let renew = match p.get("renew") {
        None => Vec::new(),
        Some(v) => desktop_names(v, 0)?,
    };
    if renew.iter().any(|r| !outputs.contains(r)) {
        return None;
    }
    outputs.sort();
    Some(Desktops {
        outputs,
        renew,
        ease,
    })
}

fn resize_handle(p: &Value) -> Option<ResizeHandle> {
    if object(p)?.len() != 4 {
        return None;
    }
    let visible = boolean(p.get("visible")?)?;
    let active = boolean(p.get("active")?)?;
    let desktop = unsigned(p.get("desktop")?).filter(|&d| d < MAX_DESKTOPS as u64)? as u32;
    let corner = match p.get("corner")?.as_str()? {
        "tl" => Corner::TopLeft,
        "tr" => Corner::TopRight,
        "bl" => Corner::BottomLeft,
        "br" => Corner::BottomRight,
        _ => return None,
    };
    Some(ResizeHandle {
        visible,
        desktop,
        corner,
        active,
    })
}

fn restore_band(p: &Value) -> Option<Payload> {
    if object(p)?.len() != 1 {
        return None;
    }
    Some(Payload::RestoreBand {
        open: boolean(p.get("open")?)?,
    })
}

fn move_handle(p: &Value) -> Option<MoveHandle> {
    let gear = p.get("gear");
    if object(p)?.len() != 4 + gear.is_some() as usize {
        return None;
    }
    let visible = boolean(p.get("visible")?)?;
    let hover = boolean(p.get("hover")?)?;
    let active = boolean(p.get("active")?)?;
    let gear = match gear {
        None => false,
        Some(g) => boolean(g)?,
    };
    let desktop = unsigned(p.get("desktop")?).filter(|&d| d < MAX_DESKTOPS as u64)? as u32;
    Some(MoveHandle {
        visible,
        desktop,
        hover,
        active,
        gear,
    })
}

const MAX_BIND_ROWS: usize = 64;
const MAX_BIND_LABEL: usize = 48;
const MAX_BIND_TEXT: usize = 96;

fn binds(p: &Value) -> Option<Binds> {
    object(p)?;
    let visible = boolean(p.get("visible")?)?;
    let mut out = Binds {
        visible,
        title: String::new(),
        hint: String::new(),
        status: String::new(),
        alert: false,
        selected: 0,
        rows: Vec::new(),
    };
    if !visible {
        return (len(p) == 1).then_some(out);
    }
    if len(p) != 7 {
        return None;
    }
    out.title = ascii(p.get("title")?, 1, MAX_BIND_LABEL)?;
    out.hint = ascii(p.get("hint")?, 1, MAX_BIND_TEXT)?;
    out.status = ascii(p.get("status")?, 0, MAX_BIND_TEXT)?;
    out.alert = boolean(p.get("alert")?)?;
    let rows = array(p.get("rows")?).filter(|r| !r.is_empty() && r.len() <= MAX_BIND_ROWS)?;
    out.selected = unsigned(p.get("selected")?).filter(|&s| s < rows.len() as u64)? as u32;
    for r in rows {
        if object(r)?.len() != 4 {
            return None;
        }
        let state = match r.get("state")?.as_str()? {
            "normal" => BindState::Normal,
            "custom" => BindState::Custom,
            "conflict" => BindState::Conflict,
            "capture" => BindState::Capture,
            _ => return None,
        };
        out.rows.push(BindRow {
            action: ascii(r.get("action")?, 1, MAX_BIND_LABEL)?,
            keys: ascii(r.get("keys")?, 1, MAX_BIND_LABEL)?,
            note: ascii(r.get("note")?, 0, MAX_BIND_TEXT)?,
            state,
        });
    }
    Some(out)
}

fn roll(p: &Value) -> Option<Roll> {
    if object(p)?.len() != 5 {
        return None;
    }
    let rolls = array(p.get("roll")?).filter(|r| r.len() == MAX_DESKTOPS)?;
    let icons = array(p.get("icons")?).filter(|r| r.len() == MAX_DESKTOPS)?;
    let active = boolean(p.get("active")?)?;
    let mut out = Roll {
        roll: [0; 4],
        bars: [false; 4],
        hover: 0,
        side: RollSide::None,
        active,
    };
    for i in 0..MAX_DESKTOPS {
        out.roll[i] = int_in(&rolls[i], -9000, 9000)?;
        out.bars[i] = one_of(&icons[i], &["none", "bar"])? == 1;
    }
    out.hover = int_in(p.get("hover")?, -1, MAX_DESKTOPS as i128 - 1)?;
    out.side = match p.get("side")?.as_str()? {
        "none" => RollSide::None,
        "ccw" => RollSide::Ccw,
        "level" => RollSide::Level,
        "cw" => RollSide::Cw,
        _ => return None,
    };
    ((out.hover < 0) == (out.side == RollSide::None)).then_some(out)
}

fn edge_pan(p: &Value) -> Option<Payload> {
    let active = boolean(object(p).and(p.get("active"))?)?;
    if !active {
        return (len(p) == 1).then_some(Payload::EdgePan(None));
    }
    if len(p) != 11 {
        return None;
    }
    let desktop = |key| {
        unsigned(p.get(key)?)
            .filter(|&d| d < MAX_DESKTOPS as u64)
            .map(|d| d as u32)
    };
    let fraction = |key| uint_in(p.get(key)?, 0, 10_000).map(|n| n as u16);
    let side = match p.get("side")?.as_str()? {
        "right" => PanSide::Right,
        "left" => PanSide::Left,
        "up" => PanSide::Up,
        "down" => PanSide::Down,
        _ => return None,
    };
    Some(Payload::EdgePan(Some(EdgePanMove {
        from: desktop("from")?,
        to: desktop("to")?,
        side,
        turn: fraction("turn")?,
        way: fraction("way")?,
        exit: fraction("exit")?,
        entry: fraction("entry")?,
        peek: fraction("peek")?,
        cross: fraction("cross")?,
        zoom: fraction("zoom")?,
    })))
}

fn overview_pointer(p: &Value) -> Option<OverviewPointer> {
    let entries = object(p)?;
    let directed = has(p, "az") || has(p, "el");
    let drag = p.get("drag");
    let look = p.get("look");
    if entries.len()
        != if directed { 6 } else { 4 } + drag.is_some() as usize + look.is_some() as usize
    {
        return None;
    }
    const LOOKS: [(&str, PointerLook); 7] = [
        ("resize_ew", PointerLook::ResizeEw),
        ("resize_ns", PointerLook::ResizeNs),
        ("resize_nwse", PointerLook::ResizeNwse),
        ("resize_nesw", PointerLook::ResizeNesw),
        ("move", PointerLook::Move),
        ("rotate", PointerLook::Rotate),
        ("text", PointerLook::Text),
    ];
    let look = match look {
        None => PointerLook::Plain,
        Some(v) => {
            let name = v.as_str()?;
            LOOKS.iter().find(|(n, _)| *n == name)?.1
        }
    };
    let visible = boolean(p.get("visible")?)?;
    let held = boolean(p.get("held")?)?;
    let x = int_in(p.get("x")?, -8192, 8192)?;
    let y = int_in(p.get("y")?, -8192, 8192)?;
    let direction = if directed {
        Some((
            int_in(p.get("az")?, -18000, 18000)?,
            int_in(p.get("el")?, -9000, 9000)?,
        ))
    } else {
        None
    };
    let drag = match drag {
        None => None,
        Some(d) => {
            if object(d)?.len() != 3 {
                return None;
            }
            Some(Ghost {
                w: int_in(d.get("w")?, 1, 512)?,
                h: int_in(d.get("h")?, 1, 512)?,
                target: int_in(d.get("target")?, -1, MAX_DESKTOPS as i128 - 1)?,
            })
        }
    };
    Some(OverviewPointer {
        visible,
        x,
        y,
        held,
        direction,
        drag,
        look,
    })
}

fn immersive(p: &Value) -> Option<Immersive> {
    if !exact(
        p,
        &["desktops", "global", "stereo", "hover", "tail", "params"],
    ) {
        return None;
    }
    let d = array(p.get("desktops")?).filter(|d| d.len() == MAX_DESKTOPS)?;
    let global = boolean(p.get("global")?)?;
    let stereo = boolean(p.get("stereo")?)?;
    let tail = boolean(p.get("tail")?)?;
    let mut desktops = [false; 4];
    for (i, v) in d.iter().enumerate() {
        desktops[i] = boolean(v)?;
    }
    let hover = int_in(p.get("hover")?, -1, MAX_DESKTOPS as i128 - 1)?;
    let w = p.get("params")?;
    let size = object(w)?.len();
    let strengths = match w.get("strengths") {
        None if size != 6 => return None,
        None => None,
        Some(_) if size != 7 => return None,
        Some(a) => {
            let a = array(a).filter(|a| a.len() == MAX_DESKTOPS)?;
            let mut s = [0; 4];
            for (i, v) in a.iter().enumerate() {
                s[i] = int_in(v, 0, 200)?;
            }
            Some(s)
        }
    };
    let field = |key, max| int_in(w.get(key)?, 0, max);
    let params = ImmersiveParams {
        strength: field("strength", 200)?,
        convergence: field("convergence", 100)?,
        near_arcmin: field("near", 90)?,
        far_arcmin: field("far", 60)?,
        text: field("text", 100)?,
        range_ms: field("range_ms", 10_000)?,
        strengths,
    };
    (!(tail && hover < 0)).then_some(Immersive {
        desktops,
        global,
        stereo,
        hover,
        tail,
        params,
    })
}

fn environment(p: &Value) -> Option<Environment> {
    if len(p) != 2 {
        return None;
    }
    Some(Environment {
        background: background_name(p.get("background")?)?,
        brightness: brightness_percent(p.get("brightness")?)?,
    })
}

fn video_format(p: &Value) -> Option<VideoFormat> {
    let projection = match p.get("projection")?.as_str()? {
        "360" => Projection::Equirect360,
        "180" => Projection::Equirect180,
        "fisheye" => Projection::Fisheye,
        _ => return None,
    };
    let stereo = match p.get("stereo")?.as_str()? {
        "mono" => Stereo::Mono,
        "sbs" => Stereo::Sbs,
        "tb" => Stereo::Tb,
        _ => return None,
    };
    Some(VideoFormat {
        projection,
        stereo,
        fov_deg: uint_in(p.get("fov")?, 180, 220)?,
    })
}

fn video(p: &Value) -> Option<Video> {
    let action = p.get("action")?.as_str()?;
    Some(match action {
        "open" if exact(p, &["action", "projection", "stereo", "fov", "title"]) => Video::Open {
            format: video_format(p)?,
            title: ascii(p.get("title")?, 1, MAX_HELP_TEXT)?,
        },
        "format" if exact(p, &["action", "projection", "stereo", "fov"]) => {
            Video::Format(video_format(p)?)
        }
        "close" if exact(p, &["action"]) => Video::Close,
        "pause" if exact(p, &["action"]) => Video::Pause,
        "controls" if exact(p, &["action", "visible"]) => Video::Controls {
            visible: boolean(p.get("visible")?)?,
        },
        "quality" if exact(p, &["action", "filter", "deband"]) => {
            let filter = match p.get("filter")?.as_str()? {
                "catmull-rom" => VideoFilter::CatmullRom,
                "sharp" => VideoFilter::Sharp,
                "bilinear" | "linear" => VideoFilter::Bilinear,
                "nearest" => VideoFilter::Nearest,
                _ => return None,
            };
            Video::Quality {
                filter,
                deband: boolean(p.get("deband")?)?,
            }
        }
        "menu" if exact(p, &["action", "visible", "selected", "rows"]) => {
            let visible = boolean(p.get("visible")?)?;
            let rows = array(p.get("rows")?).filter(|r| !r.is_empty() && r.len() <= 8)?;
            let selected = unsigned(p.get("selected")?).filter(|&s| s < rows.len() as u64)? as u32;
            let rows = rows
                .iter()
                .map(|r| {
                    if !exact(r, &["label", "value"]) {
                        return None;
                    }
                    Some((
                        ascii(r.get("label")?, 1, 24)?,
                        ascii(r.get("value")?, 1, 24)?,
                    ))
                })
                .collect::<Option<_>>()?;
            Video::Menu {
                visible,
                selected,
                rows,
            }
        }
        "seek" if exact(p, &["action", "seconds"]) => Video::Seek {
            seconds: int_in(p.get("seconds")?, -3600, 3600).filter(|&s| s != 0)?,
        },
        _ => return None,
    })
}

fn payload(op: Op, p: &Value, session: &str) -> Option<Payload> {
    Some(match op {
        Op::Status | Op::Start | Op::Stop | Op::Recover | Op::Recenter => {
            return (len(p) == 0).then_some(Payload::Empty);
        }
        Op::Select => return select(p),
        Op::View => Payload::View(view(p)?),
        Op::Style => return style(p),
        Op::Help => Payload::Help(help(p)?),
        Op::Placement => Payload::Placement(placements(p)?),
        Op::PlacementMenu => Payload::PlacementMenu(placement_menu(p)?),
        Op::OptionsMenu => Payload::OptionsMenu(options_menu(p, session)?),
        Op::ResizeHandle => Payload::ResizeHandle(resize_handle(p)?),
        Op::Video => Payload::Video(video(p)?),
        Op::MoveHandle => Payload::MoveHandle(move_handle(p)?),
        Op::OverviewPointer => Payload::OverviewPointer(overview_pointer(p)?),
        Op::Roll => Payload::Roll(roll(p)?),
        Op::Desktops => Payload::Desktops(desktops(p)?),
        Op::Environment => Payload::Environment(environment(p)?),
        Op::Binds => Payload::Binds(binds(p)?),
        Op::EdgePan => return edge_pan(p),
        Op::Immersive => Payload::Immersive(immersive(p)?),
        Op::RestoreBand => return restore_band(p),
    })
}

/// Decode one newline-terminated frame as `decode_control` does, in its order of checks.
pub fn decode(frame: &[u8]) -> Result<Message, Refusal> {
    let body = frame.strip_suffix(b"\n");
    if frame.len() > MAX_OPTIONS_MENU_FRAME || body.is_none_or(|b| b.contains(&b'\n')) {
        return Err(invalid("bad framing"));
    }
    if json::too_deep(frame, MAX_DEPTH) {
        return Err(invalid("nesting limit"));
    }
    let j = json::parse_strict(frame).map_err(|e| {
        if e.0.contains(json::DUPLICATE_KEY) {
            invalid("duplicate key")
        } else if frame.len() > MAX_FRAME {
            invalid("bad framing")
        } else {
            invalid("not JSON")
        }
    })?;
    let envelope = || invalid("bad envelope");
    const FIELDS: [&str; 6] = ["schema", "request_id", "run_id", "session", "op", "payload"];
    if !exact(&j, &FIELDS) {
        return Err(envelope());
    }
    let field = |k| j.get(k).expect("checked above");
    if unsigned(field("schema")) != Some(1) {
        return Err(envelope());
    }
    let request_id = unsigned(field("request_id"))
        .filter(|&n| n != 0)
        .ok_or_else(envelope)?;
    let (run_id, session) = match (token(field("run_id")), token(field("session"))) {
        (Some(r), Some(s)) => (r, s),
        _ => return Err(envelope()),
    };
    let name = field("op").as_str().ok_or_else(envelope)?;
    let raw = field("payload");
    if name != "options_menu" && (frame.len() > MAX_FRAME || object(raw).is_none()) {
        return Err(envelope());
    }
    let op = Op::from_name(name).ok_or_else(|| invalid("unknown op"))?;
    let payload = payload(op, raw, session).ok_or_else(|| op.payload_refusal())?;
    Ok(Message {
        request_id,
        run_id: run_id.to_owned(),
        session: session.to_owned(),
        op,
        payload,
        raw_payload: raw.clone(),
    })
}

/// A refused frame, as `ControlRefusal` reports it to the log.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Refused {
    pub refusal: Refusal,
    /// The frame's `op` if it read as JSON and that is a token.
    pub op: Option<String>,
    /// The frame's `request_id` if it read as JSON and that is unsigned; 0 otherwise.
    pub request_id: u64,
    /// What a log may say of the frame: [`redacted_frame`].
    pub frame: String,
}

/// The most of a frame [`redacted_frame`] keeps (`kMaxRedactedFrame`).
pub const MAX_REDACTED_FRAME: usize = 200;

/// String values a log may show: this protocol's own words, never a person's text.
fn harmless_string(key: &str, s: &str) -> bool {
    const KEYS: [&str; 11] = [
        "action",
        "background",
        "corner",
        "field",
        "look",
        "mode",
        "op",
        "projection",
        "side",
        "state",
        "stereo",
    ];
    KEYS.contains(&key) && is_token(s) && s.len() <= 32
}

fn redact(value: serde_json::Value, key: &str, depth: usize) -> serde_json::Value {
    use serde_json::Value as J;
    if depth > 8 {
        return J::String("***".into()); // deeper than any frame this protocol has
    }
    match value {
        J::Object(o) => {
            // nlohmann's object is a sorted map: the same order whatever the frame's.
            let mut out = std::collections::BTreeMap::new();
            for (name, item) in o {
                let item = redact(item, &name, depth + 1);
                out.insert(if is_token(&name) { name } else { "***".into() }, item);
            }
            J::Object(out.into_iter().collect())
        }
        J::Array(a) => J::Array(a.into_iter().map(|v| redact(v, key, depth + 1)).collect()),
        J::String(s) if !harmless_string(key, &s) => J::String("***".into()),
        other => other,
    }
}

/// `redacted_frame`: a frame's first [`MAX_REDACTED_FRAME`] bytes on one line, with no string it
/// does not know to be harmless. Frames carry video titles and the bind panel's text.
pub fn redacted_frame(frame: &[u8]) -> String {
    if frame.is_empty() {
        return String::new();
    }
    let mut out = match serde_json::from_slice::<serde_json::Value>(frame) {
        Ok(v) => {
            let v = redact(v, "", 0);
            let text = serde_json::to_string(&v).unwrap_or_default();
            // ensure_ascii, as nlohmann's dump writes it.
            text.chars().fold(String::new(), |mut o, c| {
                if c.is_ascii() {
                    o.push(c);
                } else {
                    for u in c.encode_utf16(&mut [0; 2]) {
                        o.push_str(&format!("\\u{u:04x}"));
                    }
                }
                o
            })
        }
        // Not JSON: nothing tells its strings from any other text, so no words at all.
        Err(_) => frame
            .iter()
            .take(MAX_REDACTED_FRAME + 1)
            .map(|&c| match c {
                c if c.is_ascii_alphanumeric() => 'x',
                0x20..0x7f => c as char,
                _ => '.',
            })
            .collect(),
    };
    if out.len() > MAX_REDACTED_FRAME {
        out.truncate(MAX_REDACTED_FRAME);
        out.push_str("...");
    }
    out
}

/// `ControlStream` and its `ControlQueue`: frames for one run and session, request ids strictly
/// increasing. A refused frame costs only itself. Bytes past [`MAX_OPTIONS_MENU_FRAME`] with no
/// newline are refused as `control_overflow` and skipped up to the next newline.
#[derive(Debug)]
pub struct Stream {
    run_id: String,
    session: String,
    last: u64,
    pending: Vec<u8>,
    skipping: bool,
    accepted: u64,
    refused: u64,
}

impl Stream {
    /// `None` unless both are tokens (the C++ throws `invalid identity`).
    pub fn new(run_id: &str, session: &str) -> Option<Stream> {
        (is_token(run_id) && is_token(session)).then(|| Stream {
            run_id: run_id.to_owned(),
            session: session.to_owned(),
            last: 0,
            pending: Vec::new(),
            skipping: false,
            accepted: 0,
            refused: 0,
        })
    }

    pub fn accepted(&self) -> u64 {
        self.accepted
    }

    pub fn refused(&self) -> u64 {
        self.refused
    }

    /// Feed bytes as they arrive; every complete frame comes back in order, taken or refused.
    pub fn feed(&mut self, mut bytes: &[u8]) -> Vec<Result<Message, Refused>> {
        let mut out = Vec::new();
        if self.skipping {
            let Some(end) = bytes.iter().position(|&b| b == b'\n') else {
                return out;
            };
            bytes = &bytes[end + 1..];
            self.skipping = false;
        }
        self.pending.extend_from_slice(bytes);
        while let Some(nl) = self.pending.iter().position(|&b| b == b'\n') {
            let frame: Vec<u8> = self.pending.drain(..=nl).collect();
            out.push(match self.push(&frame) {
                Ok(message) => {
                    self.accepted += 1;
                    Ok(message)
                }
                Err(refusal) => Err(self.refuse(refusal, &frame)),
            });
        }
        if self.pending.len() > MAX_OPTIONS_MENU_FRAME {
            let start: Vec<u8> = self.pending.iter().take(201).copied().collect();
            self.pending.clear();
            self.skipping = true;
            let refusal = Refusal {
                code: "control_overflow",
                detail: format!("no newline in {MAX_OPTIONS_MENU_FRAME} bytes"),
            };
            out.push(Err(self.refuse(refusal, &start)));
        }
        out
    }

    fn push(&mut self, frame: &[u8]) -> Result<Message, Refusal> {
        let message = decode(frame)?;
        if message.run_id != self.run_id || message.session != self.session {
            return Err(Refusal {
                code: "control_wrong_run",
                detail: "frame is for another run or session".into(),
            });
        }
        if message.request_id <= self.last {
            return Err(Refusal {
                code: "control_replay",
                detail: format!(
                    "request id {} is not above {}",
                    message.request_id, self.last
                ),
            });
        }
        self.last = message.request_id;
        Ok(message)
    }

    fn refuse(&mut self, refusal: Refusal, frame: &[u8]) -> Refused {
        self.refused += 1;
        // Read leniently, only for the log: repeated keys keep the last, as nlohmann does.
        let parsed: Option<serde_json::Value> = serde_json::from_slice(frame).ok();
        let lenient = parsed.as_ref().and_then(serde_json::Value::as_object);
        let op = lenient
            .and_then(|o| o.get("op"))
            .and_then(serde_json::Value::as_str)
            .filter(|s| is_token(s))
            .map(str::to_owned);
        let request_id = lenient
            .and_then(|o| o.get("request_id"))
            .and_then(serde_json::Value::as_u64)
            .unwrap_or(0);
        Refused {
            refusal,
            op,
            request_id,
            frame: redacted_frame(frame),
        }
    }
}

/// A frame for `op` as its Python sender writes it, newline included. Every sender writes
/// `ensure_ascii` JSON. `start` (`supervisor._launch`) uses `json.dumps`' default separators;
/// `options_menu` and `restore_band` put the identity before the request id; every other op is
/// compact with the keys in envelope order.
pub fn encode(op: Op, request_id: u64, run_id: &str, session: &str, payload: &Value) -> String {
    let s = |v: &str| Value::String(v.to_owned());
    let id = Value::Number(request_id.into());
    let schema = ("schema".to_owned(), Value::Number(1u64.into()));
    let mut keys = vec![schema];
    let identity = [
        ("run_id".to_owned(), s(run_id)),
        ("session".to_owned(), s(session)),
    ];
    if matches!(op, Op::OptionsMenu | Op::RestoreBand) {
        keys.extend(identity);
        keys.push(("request_id".to_owned(), id));
    } else {
        keys.push(("request_id".to_owned(), id));
        keys.extend(identity);
    }
    keys.push(("op".to_owned(), s(op.name())));
    keys.push(("payload".to_owned(), payload.clone()));
    let style = if op == Op::Start {
        Style::DEFAULT
    } else {
        Style::COMPACT
    };
    let mut out = json::encode(&Value::Object(keys), style);
    out.push('\n');
    out
}

/// [`encode`] for a decoded message: its frame as the sender wrote it.
pub fn encode_message(message: &Message) -> String {
    encode(
        message.op,
        message.request_id,
        &message.run_id,
        &message.session,
        &message.raw_payload,
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    const FRAME: &str = r#"{"schema":1,"request_id":3,"run_id":"run","session":"session","op":"roll","payload":{"roll":[0,0,0,0],"icons":["none","bar","none","none"],"hover":1,"side":"cw","active":true}}"#;

    #[test]
    fn a_frame_decodes_and_encodes_back() {
        let frame = format!("{FRAME}\n");
        let message = decode(frame.as_bytes()).unwrap();
        assert_eq!(message.op, Op::Roll);
        let Payload::Roll(r) = message.payload else {
            panic!()
        };
        assert_eq!(
            (r.hover, r.side, r.bars),
            (1, RollSide::Cw, [false, true, false, false])
        );
        assert_eq!(encode_message(&decode(frame.as_bytes()).unwrap()), frame);
    }

    #[test]
    fn the_stream_splits_lines_and_holds_the_replay_floor() {
        let mut stream = Stream::new("run", "session").unwrap();
        let frame = format!("{FRAME}\n");
        let (head, tail) = frame.split_at(20);
        assert!(stream.feed(head.as_bytes()).is_empty());
        assert!(stream.feed(tail.as_bytes())[0].is_ok());
        let again = stream.feed(frame.as_bytes());
        let Err(refused) = &again[0] else { panic!() };
        assert_eq!(refused.refusal.detail, "request id 3 is not above 3");
        assert_eq!(
            (refused.op.as_deref(), refused.request_id),
            (Some("roll"), 3)
        );
    }

    #[test]
    fn an_endless_line_is_refused_once_and_skipped() {
        let mut stream = Stream::new("run", "session").unwrap();
        let got = stream.feed(&vec![b'x'; MAX_OPTIONS_MENU_FRAME + 1]);
        assert_eq!(got.len(), 1);
        assert_eq!(
            got[0].as_ref().unwrap_err().refusal.code,
            "control_overflow"
        );
        assert!(stream.feed(b"more").is_empty());
        let got = stream.feed(format!("rest\n{FRAME}\n").as_bytes());
        assert_eq!(got.len(), 1);
        assert!(got[0].is_ok());
        assert_eq!((stream.accepted(), stream.refused()), (1, 1));
    }

    #[test]
    fn identities_must_be_tokens() {
        assert!(Stream::new("run", "").is_none());
        assert!(Stream::new("-run", "session").is_none());
        assert!(
            Op::ALL
                .iter()
                .all(|op| Op::from_name(op.name()) == Some(*op))
        );
    }

    #[test]
    fn redacted_frames_keep_only_protocol_words() {
        let frame = br#"{"schema":1,"request_id":4,"run_id":"run","session":"s","op":"video","payload":{"action":"open","title":"Holiday \u00e9 2025","fov":180,"projection":"360"}}
"#;
        assert_eq!(
            redacted_frame(frame),
            r#"{"op":"video","payload":{"action":"open","fov":180,"projection":"360","title":"***"},"request_id":4,"run_id":"***","schema":1,"session":"***"}"#
        );
        assert_eq!(redacted_frame(b"pass word=1\n\x01"), "xxxx xxxx=x..");
        assert_eq!(redacted_frame(b""), "");
        let long = format!("{}\n", "a".repeat(500));
        assert_eq!(
            redacted_frame(long.as_bytes()),
            format!("{}...", "x".repeat(200))
        );
        let deep = format!("{}1{}", "[".repeat(12), "]".repeat(12));
        assert_eq!(
            redacted_frame(deep.as_bytes()),
            format!("{}\"***\"{}", "[".repeat(9), "]".repeat(9))
        );
        let mut stream = Stream::new("run", "s").unwrap();
        let got = stream.feed(b"{\"op\":\"view\",\"note\":\"secret\"}\n");
        assert_eq!(
            got[0].as_ref().unwrap_err().frame,
            r#"{"note":"***","op":"view"}"#
        );
    }

    #[test]
    fn only_display_payloads_are_ignorable() {
        let refusal = |op: Op| op.payload_refusal();
        assert!(refusal(Op::PlacementMenu).ignorable() && refusal(Op::Binds).ignorable());
        assert!(!refusal(Op::EdgePan).ignorable() && !refusal(Op::RestoreBand).ignorable());
        assert!(!refusal(Op::View).ignorable() && !invalid("not JSON").ignorable());
    }
}
