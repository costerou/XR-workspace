//! View state and the renderer frames it makes (`tools/native_spike/view.py`, the parts outside `ViewControl`).
//!
//! `ViewState` is the run's view (maximized or overview, selected desktop, spacing, the placement panel) and the
//! frame encoders turn it into the bytes the Python sends the renderer: `json.dumps(..., separators=(',', ':'))`
//! plus a newline, keys in the Python's order. `load_settings` / `save_settings` read and write `layout.json` as the
//! Python does. The bind-table Lua (`install_binds_lua` and friends), `session_mode` and `ViewControl` are not here.

use super::environment::{self, Environment};
use super::immersive;
use super::python::{Invalid, Result, py_round, py_space};
use super::quality::{self, Quality};
use crate::geometry::{
    self, DEFAULT_PLACEMENT, Desktop, MAX_DESKTOPS, PLACEMENT_BOUNDS, PLACEMENT_KEYS, Placement,
    valid_scales,
};
use crate::pyjson::{self, Json};
use serde::Serialize;
use serde_json::Value;
use std::collections::BTreeMap;
use std::fs;
use std::io::{self, Write};
use std::os::unix::fs::{DirBuilderExt, OpenOptionsExt};
use std::path::{Path, PathBuf};

pub const DEFAULT_SPACING: i64 = 40;
pub const MAX_SPACING: i64 = 400;
pub const SPACING_STEP: i64 = 20;
pub const DEFAULT_STABILIZATION: i64 = 25;

pub const USER_OPS: [&str; 10] = [
    "zoom_in",
    "zoom_out",
    "next",
    "prev",
    "spacing_up",
    "spacing_down",
    "recenter",
    "mode_next",
    "prediction",
    "help",
];

/// The run binds, chord then op, in the order the Python installs them.
pub const BINDS: [(&str, &str); 22] = [
    ("CTRL + ALT + up", "zoom_in"),
    ("CTRL + ALT + down", "zoom_out"),
    ("CTRL + ALT + left", "prev"),
    ("CTRL + ALT + right", "next"),
    ("CTRL + ALT + bracketleft", "spacing_down"),
    ("CTRL + ALT + bracketright", "spacing_up"),
    ("CTRL + ALT + space", "recenter"),
    ("CTRL + ALT + V", "mode_next"),
    ("CTRL + ALT + P", "prediction"),
    ("CTRL + ALT + H", "help"),
    ("CTRL + ALT + T", "typing_toggle"),
    ("CTRL + ALT + L", "menu_toggle"),
    ("CTRL + ALT + R", "layout_reset"),
    ("CTRL + ALT + O", "video_open"),
    ("CTRL + ALT + U", "video_paste"),
    ("CTRL + ALT + Page_Up", "help_prev"),
    ("CTRL + ALT + Page_Down", "help_next"),
    ("CTRL + ALT + D", "privacy_display"),
    ("CTRL + ALT + C", "playback_menu_toggle"),
    ("CTRL + ALT + K", "binds_toggle"),
    ("CTRL + ALT + E", "environment_toggle"),
    ("CTRL + ALT + I", "immersive_toggle"),
];

/// `send.SEND_BINDS`: the send-window binds, which the help card names on its Ctrl+Alt+H row.
pub const SEND_BINDS: [(&str, &str); 6] = [
    ("CTRL + ALT + G", "send_glasses"),
    ("CTRL + ALT + code:10", "send_glasses_1"),
    ("CTRL + ALT + code:11", "send_glasses_2"),
    ("CTRL + ALT + code:12", "send_glasses_3"),
    ("CTRL + ALT + code:13", "send_glasses_4"),
    ("CTRL + ALT + B", "send_back"),
];

const HELP_ACTIONS: [(&str, &str); 22] = [
    ("zoom_in", "Maximize the selected desktop"),
    ("zoom_out", "Overview: any desktop; pills, bar, corners"),
    ("prev", "Previous desktop"),
    ("next", "Next desktop"),
    ("spacing_down", "Less space between desktops"),
    ("spacing_up", "More space between desktops"),
    ("recenter", "Recenter the view"),
    ("layout_reset", "Reset to 3 flat desktops (press twice)"),
    ("mode_next", "Maximized: follow / locked"),
    ("prediction", "Toggle pose prediction"),
    ("help", "Help; Ctrl+Alt+G/B: window to glasses/back"),
    ("typing_toggle", "Typing mode: XR keys off / on"),
    ("menu_toggle", "Placement pill; R in it resets, levels"),
    ("video_open", "Play a 180/360 VR video"),
    ("video_paste", "Play the browser video you copied"),
    ("help_prev", "Previous help page"),
    ("help_next", "Next help page"),
    ("privacy_display", "Privacy: main display off / on"),
    (
        "playback_menu_toggle",
        "VR video: hide / show playback controls",
    ),
    ("binds_toggle", "Key binds: change keys, see conflicts"),
    ("environment_toggle", "Background: none / last used"),
    ("immersive_toggle", "Immersive 3D: all desktops on / off"),
];

const HELP_SECTIONS: [&[&str]; 4] = [
    &["zoom_in", "zoom_out", "prev", "spacing_down", "recenter"],
    &[
        "mode_next",
        "prediction",
        "typing_toggle",
        "privacy_display",
        "environment_toggle",
    ],
    &["menu_toggle", "video_open"],
    &["help", "help_prev"],
];

/// `HELP_PAIRS`: a row's op, the ops that share its row, and the row's text.
const HELP_PAIRS: [(&str, &[&str], &str); 7] = [
    (
        "environment_toggle",
        &["immersive_toggle"],
        "Background / immersive 3D: on or off",
    ),
    ("prev", &["next"], "Previous / next desktop"),
    (
        "spacing_down",
        &["spacing_up"],
        "Less / more space between desktops",
    ),
    (
        "recenter",
        &["layout_reset"],
        "Recenter / reset to 3 flat desktops",
    ),
    (
        "video_open",
        &["video_paste", "playback_menu_toggle"],
        "VR video: file / web link / hide controls",
    ),
    ("help_prev", &["help_next"], "Help: previous / next page"),
    (
        "help",
        &["binds_toggle"],
        "Help/binds; Ctrl+Alt+G/B: window to XR/back",
    ),
];

pub const MENU_OPS: [&str; 14] = [
    "menu_toggle",
    "menu_desktop_next",
    "menu_desktop_prev",
    "menu_field_next",
    "menu_field_prev",
    "menu_inc",
    "menu_dec",
    "menu_inc_big",
    "menu_dec_big",
    "menu_reset",
    "menu_back",
    "menu_ultrawide",
    "menu_add",
    "menu_delete",
];

/// `MENU_DESKTOP_OPS`: the panel's desktop-row keys and the desktop change each asks for.
pub const MENU_DESKTOP_OPS: [(&str, &str); 3] = [
    ("menu_ultrawide", "ultrawide"),
    ("menu_add", "add"),
    ("menu_delete", "remove"),
];

/// `MENU_STEPS`: the small and the big (Shift) step of each placement key.
pub const MENU_STEPS: [(&str, i64, i64); 4] = [
    ("x", 2, 150),
    ("y", 2, 100),
    ("distance", 5, 25),
    ("curvature", 2, 20),
];

pub const GLIDE_MS: i64 = 240;
pub const MAX_GLIDE_MS: i64 = 1000;
pub const SCALE_PERCENTS: [i64; 7] = [100, 125, 150, 175, 200, 250, 300];
pub const DEFAULT_RESET_PLACEMENT: Placement = DEFAULT_PLACEMENT;
pub const RESET_LEAVING_PLACEMENT: Placement = Placement {
    x: 0,
    y: -900,
    distance: 200,
    curvature: 0,
};

/// `MANAGED`: XR-1..XR-4, the only outputs a run makes.
pub fn managed(name: &str) -> bool {
    matches!(name.as_bytes(), [b'X', b'R', b'-', b'1'..=b'4'])
}

/// The viewing mode: the overview is anchor, a maximized desktop follow or locked.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[cfg_attr(test, derive(serde::Deserialize))]
#[serde(rename_all = "lowercase")]
pub enum Mode {
    Anchor,
    Follow,
    Locked,
}

impl Mode {
    pub fn parse(name: &str) -> Option<Mode> {
        match name {
            "anchor" => Some(Mode::Anchor),
            "follow" => Some(Mode::Follow),
            "locked" => Some(Mode::Locked),
            _ => None,
        }
    }
}

/// The placement panel's setting: a placement key, or the background's brightness.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[cfg_attr(test, derive(serde::Deserialize))]
#[serde(rename_all = "lowercase")]
pub enum Field {
    X,
    Y,
    Distance,
    Curvature,
    Brightness,
}

impl Field {
    /// `PLACEMENT_KEYS + (BRIGHTNESS_FIELD,)`, the order Up / Down go through.
    pub const ALL: [Field; 5] = [
        Field::X,
        Field::Y,
        Field::Distance,
        Field::Curvature,
        Field::Brightness,
    ];

    pub fn name(self) -> &'static str {
        match self {
            Field::X => "x",
            Field::Y => "y",
            Field::Distance => "distance",
            Field::Curvature => "curvature",
            Field::Brightness => environment::BRIGHTNESS_FIELD,
        }
    }

    pub fn parse(name: &str) -> Option<Field> {
        Field::ALL.into_iter().find(|f| f.name() == name)
    }
}

fn key_name(part: &str) -> Option<&'static str> {
    Some(match part {
        "CTRL" => "Ctrl",
        "ALT" => "Alt",
        "SHIFT" => "Shift",
        "SUPER" => "Super",
        "up" => "Up",
        "down" => "Down",
        "left" => "Left",
        "right" => "Right",
        "bracketleft" => "[",
        "bracketright" => "]",
        "space" => "Space",
        "Tab" => "Tab",
        "Escape" => "Esc",
        "Page_Up" => "PgUp",
        "Page_Down" => "PgDn",
        "Return" => "Enter",
        "BackSpace" => "Backspace",
        "Delete" => "Del",
        "Insert" => "Ins",
        "comma" => ",",
        "period" => ".",
        "slash" => "/",
        "semicolon" => ";",
        "apostrophe" => "'",
        "minus" => "-",
        "equal" => "=",
        "grave" => "`",
        "backslash" => "\\",
        "code:10" => "1",
        "code:11" => "2",
        "code:12" => "3",
        "code:13" => "4",
        "code:14" => "5",
        "code:15" => "6",
        "code:16" => "7",
        "code:17" => "8",
        "code:18" => "9",
        "code:19" => "0",
        _ => return None,
    })
}

/// `chord_label`: "CTRL + ALT + bracketleft" is "Ctrl+Alt+[", a single letter is upper-cased.
pub fn chord_label(chord: &str) -> String {
    let parts: Vec<String> = chord
        .split(" + ")
        .map(|part| match key_name(part) {
            Some(name) => name.to_string(),
            None if part.chars().count() == 1 => part.to_uppercase(),
            None => part.to_string(),
        })
        .collect();
    parts.join("+")
}

/// A dict built from (chord, op) pairs keyed by op: the last chord for an op wins.
fn chord_of<'a>(binds: &[(&'a str, &'a str)], op: &str) -> Option<&'a str> {
    binds.iter().rev().find(|(_, o)| *o == op).map(|(c, _)| *c)
}

/// The text up to and including the last '+' (all of a label's modifiers), or "" when there is none.
fn prefix(label: &str) -> &str {
    label.rfind('+').map_or("", |i| &label[..=i])
}

fn short_pair(a: &str, b: &str) -> String {
    match b.strip_prefix(prefix(a)) {
        Some(rest) => format!("{a}/{rest}"),
        None => format!("{a}/{b}"),
    }
}

/// `_help_action`: the Ctrl+Alt+H row names the send-window keys, when they are not the default ones.
fn help_action(send_binds: Option<&[(&str, &str)]>) -> String {
    let default = HELP_PAIRS
        .iter()
        .find(|(op, _, _)| *op == "help")
        .expect("help has a pair")
        .2;
    let Some(send_binds) = send_binds.filter(|b| *b != SEND_BINDS) else {
        return default.to_string();
    };
    let (Some(glasses), Some(back)) = (
        chord_of(send_binds, "send_glasses"),
        chord_of(send_binds, "send_back"),
    ) else {
        return default.to_string();
    };
    let text = format!(
        "Help/binds; {}: window to XR/back",
        short_pair(&chord_label(glasses), &chord_label(back))
    );
    if text.chars().count() <= 48 {
        text
    } else {
        "Help/binds (window keys: see key binds)".to_string()
    }
}

pub type HelpRow = [String; 2];

/// `_help_sections`: the help card's rows, grouped. `binds` is the run's bind table (None: `BINDS`); every op in
/// it must be one of `BINDS`'s (`_checked_binds` makes sure of that before a table gets here).
fn help_sections_rows(
    binds: Option<&[(&str, &str)]>,
    send_binds: Option<&[(&str, &str)]>,
) -> Result<Vec<Vec<HelpRow>>> {
    let binds = binds.unwrap_or(&BINDS);
    let seconds: Vec<&str> = HELP_PAIRS
        .iter()
        .flat_map(|(_, s, _)| s.iter().copied())
        .collect();
    let row = |op: &str| -> Result<HelpRow> {
        let chord = chord_of(binds, op).expect("rows are made only for ops in the table");
        if let Some((_, pair, text)) = HELP_PAIRS.iter().find(|(o, _, _)| *o == op) {
            let mut label = chord_label(chord);
            let lead = prefix(&label).to_string();
            for second in pair.iter().filter_map(|s| chord_of(binds, s)) {
                let b = chord_label(second);
                label += &match b.strip_prefix(&lead) {
                    Some(rest) => format!(" / {rest}"),
                    None => format!(" / {b}"),
                };
            }
            let action = if op == "help" {
                help_action(send_binds)
            } else {
                text.to_string()
            };
            return Ok([label, action]);
        }
        match HELP_ACTIONS.iter().find(|(o, _)| *o == op) {
            Some((_, action)) => Ok([chord_label(chord), action.to_string()]),
            None => Err(Invalid(format!("no help text for {op}"))),
        }
    };
    let placed = |op: &str| HELP_SECTIONS.iter().any(|s| s.contains(&op));
    let mut sections = HELP_SECTIONS
        .iter()
        .map(|section| {
            section
                .iter()
                .filter(|op| chord_of(binds, op).is_some())
                .map(|op| row(op))
                .collect()
        })
        .collect::<Result<Vec<Vec<HelpRow>>>>()?;
    let rest = binds
        .iter()
        .filter(|(_, op)| !placed(op) && !seconds.contains(op))
        .map(|(_, op)| row(op))
        .collect::<Result<Vec<HelpRow>>>()?;
    sections
        .last_mut()
        .expect("there are sections")
        .extend(rest);
    sections.retain(|s| !s.is_empty());
    Ok(sections)
}

/// `help_rows`: every row of the help card, in order.
pub fn help_rows(
    binds: Option<&[(&str, &str)]>,
    send_binds: Option<&[(&str, &str)]>,
) -> Result<Vec<HelpRow>> {
    Ok(help_sections_rows(binds, send_binds)?
        .into_iter()
        .flatten()
        .collect())
}

/// `help_sections`: how many rows each group has.
pub fn help_sections(
    binds: Option<&[(&str, &str)]>,
    send_binds: Option<&[(&str, &str)]>,
) -> Result<Vec<usize>> {
    Ok(help_sections_rows(binds, send_binds)?
        .iter()
        .map(Vec::len)
        .collect())
}

#[derive(Serialize)]
struct Frame<'a, P> {
    schema: u8,
    request_id: u64,
    run_id: &'a str,
    session: &'a str,
    op: &'a str,
    payload: P,
}

/// One control frame line, as every Python sender writes it.
pub fn frame<P: Serialize>(
    run_id: &str,
    session: &str,
    request_id: u64,
    op: &str,
    payload: P,
) -> String {
    let mut out = pyjson::compact(&Frame {
        schema: 1,
        request_id,
        run_id,
        session,
        op,
        payload,
    });
    out.push('\n');
    out
}

/// `help_frame`: show or hide the help card; `turn` -1 / 1 turns its page.
pub fn help_frame(
    run_id: &str,
    session: &str,
    request_id: u64,
    visible: bool,
    turn: i64,
    binds: Option<&[(&str, &str)]>,
    send_binds: Option<&[(&str, &str)]>,
) -> Result<String> {
    #[derive(Serialize)]
    struct Help {
        visible: bool,
        rows: Vec<HelpRow>,
        sections: Vec<usize>,
        #[serde(skip_serializing_if = "is_zero")]
        turn: i64,
    }
    fn is_zero(v: &i64) -> bool {
        *v == 0
    }
    if !(-1..=1).contains(&turn) {
        return Err(Invalid::new("invalid help turn"));
    }
    let sections = help_sections_rows(binds, send_binds)?;
    let payload = Help {
        visible,
        sections: sections.iter().map(Vec::len).collect(),
        rows: sections.into_iter().flatten().collect(),
        turn,
    };
    Ok(frame(run_id, session, request_id, "help", payload))
}

/// `COLOR`: 8 hex digits (AARRGGBB), optionally after "0x".
fn colour(token: &str) -> Option<u32> {
    let hex = |s: &str| s.len() == 8 && s.bytes().all(|b| b.is_ascii_hexdigit());
    let digits = if hex(token) {
        token
    } else {
        token.strip_prefix("0x").filter(|s| hex(s))?
    };
    u32::from_str_radix(digits, 16).ok()
}

/// `parse_border`: Hyprland's `general:col.active_border` reply (`getoption`) as RGB, from the first gradient
/// colour or the plain int; None for anything else.
pub fn parse_border(reply: &Json) -> Option<[u8; 3]> {
    reply.as_object()?;
    let value = match reply.get("gradient") {
        Some(Json::Str(gradient)) => colour(gradient.split(py_space).find(|t| !t.is_empty())?)?,
        _ => u32::try_from(reply.get("int")?.as_int()?).ok()?,
    };
    Some([(value >> 16) as u8, (value >> 8) as u8, value as u8])
}

/// `style_frame`: the theme's accent colour.
pub fn style_frame(run_id: &str, session: &str, request_id: u64, rgb: [u8; 3]) -> String {
    #[derive(Serialize)]
    struct Style {
        border: [u8; 3],
    }
    frame(run_id, session, request_id, "style", Style { border: rgb })
}

/// `desktops_frame`: the run's desktops (`renew`: outputs Hyprland made again, `ease`: glide to the new layout).
pub fn desktops_frame(
    run_id: &str,
    session: &str,
    request_id: u64,
    names: &[&str],
    renew: &[&str],
    ease: bool,
) -> Result<String> {
    #[derive(Serialize)]
    struct Desktops<'a> {
        outputs: Vec<&'a str>,
        #[serde(skip_serializing_if = "Vec::is_empty")]
        renew: Vec<&'a str>,
        #[serde(skip_serializing_if = "std::ops::Not::not")]
        ease: bool,
    }
    let unique = |v: &[&str]| v.iter().enumerate().all(|(i, n)| !v[..i].contains(n));
    if !(1..=MAX_DESKTOPS).contains(&names.len())
        || !unique(names)
        || !names.iter().all(|n| managed(n))
        || !unique(renew)
        || !renew.iter().all(|n| names.contains(n))
    {
        return Err(Invalid::new("invalid desktops"));
    }
    let (mut outputs, mut renew) = (names.to_vec(), renew.to_vec());
    outputs.sort();
    renew.sort();
    Ok(frame(
        run_id,
        session,
        request_id,
        "desktops",
        Desktops {
            outputs,
            renew,
            ease,
        },
    ))
}

/// `placement_frame`: every desktop's placement, applied at once or over `glide` ms.
pub fn placement_frame(
    run_id: &str,
    session: &str,
    request_id: u64,
    placements: &[Placement],
    glide: Option<i64>,
) -> Result<String> {
    #[derive(Serialize)]
    struct Placements {
        desktops: Vec<Placement>,
        #[serde(skip_serializing_if = "Option::is_none")]
        glide: Option<i64>,
    }
    if !(1..=MAX_DESKTOPS).contains(&placements.len()) {
        return Err(Invalid::new("invalid placements"));
    }
    let desktops = placements
        .iter()
        .map(validate_placement)
        .collect::<Result<_>>()?;
    if glide.is_some_and(|g| !(1..=MAX_GLIDE_MS).contains(&g)) {
        return Err(Invalid::new("invalid glide"));
    }
    Ok(frame(
        run_id,
        session,
        request_id,
        "placement",
        Placements { desktops, glide },
    ))
}

/// `geometry.PLACEMENT_BOUNDS` by key: (key, low, high).
fn placement_bounds() -> impl Iterator<Item = (&'static str, i64, i64)> {
    PLACEMENT_KEYS
        .into_iter()
        .zip(PLACEMENT_BOUNDS)
        .map(|(k, (lo, hi))| (k, lo, hi))
}

/// `validate_placement`, `validate_placements` and `validate_desktop` on typed values, refusing as [`Invalid`].
fn validate_placement(p: &Placement) -> Result<Placement> {
    Ok(p.validate()?)
}

fn validate_placements(value: &[Placement]) -> Result<Vec<Placement>> {
    Ok(geometry::validate_placements(value)?)
}

fn validate_desktop(d: &Desktop) -> Result<Desktop> {
    Ok(Desktop::new(d.width, d.height, d.scale)?)
}

/// `smootherstep`, clamped to 0..1 as the Python clamps (a NaN stays NaN).
pub fn smootherstep(t: f64) -> f64 {
    let t = py_min(py_max(t, 0.0), 1.0);
    t * t * t * (t * (6.0 * t - 15.0) + 10.0)
}

/// Python's `max(a, b)` and `min(a, b)`: the first argument unless the second compares past it.
fn py_max(a: f64, b: f64) -> f64 {
    if b > a { b } else { a }
}

fn py_min(a: f64, b: f64) -> f64 {
    if b < a { b } else { a }
}

/// `eased_to`: `e` of the way from `p` to `to`; x goes the short way round.
pub fn eased_to(p: &Placement, to: &Placement, e: f64) -> Placement {
    let mut out = *p;
    for key in PLACEMENT_KEYS {
        let (a, b) = (
            p.get(key).expect("placement key"),
            to.get(key).expect("placement key"),
        );
        let value = if key == "x" {
            let turn = (b - a + 1800).rem_euclid(3600) - 1800;
            let v = py_round(a as f64 + turn as f64 * e);
            if (-1800..=1800).contains(&v) {
                v
            } else {
                (v + 1800).rem_euclid(3600) - 1800
            }
        } else {
            py_round(a as f64 + (b - a) as f64 * e)
        };
        out = out.with(key, value);
    }
    out
}

/// `scale_percent`: a Hyprland scale as the panel's percent.
pub fn scale_percent(scale: f64) -> i64 {
    py_round(scale * 100.0)
}

/// The placement panel, as `placement_menu_frame` sends it. Field order is the frame's key order; `None` is a key
/// the dict does not have.
#[derive(Clone, Debug, PartialEq, Serialize)]
#[cfg_attr(test, derive(serde::Deserialize), serde(deny_unknown_fields))]
pub struct Menu {
    pub open: bool,
    pub desktop: usize,
    pub field: Field,
    pub placements: Vec<Placement>,
    #[serde(skip_serializing_if = "Option::is_none")]
    #[cfg_attr(test, serde(default))]
    pub surface: Option<Placement>,
    #[serde(skip_serializing_if = "Option::is_none")]
    #[cfg_attr(test, serde(default))]
    pub names: Option<Vec<i64>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    #[cfg_attr(test, serde(default))]
    pub wide: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    #[cfg_attr(test, serde(default))]
    pub armed: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    #[cfg_attr(test, serde(default))]
    pub busy: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    #[cfg_attr(test, serde(default))]
    pub picker: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    #[cfg_attr(test, serde(default))]
    pub resolution_picker: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    #[cfg_attr(test, serde(default))]
    pub background_picker: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    #[cfg_attr(test, serde(default))]
    pub resolution: Option<[i64; 2]>,
    #[serde(skip_serializing_if = "Option::is_none")]
    #[cfg_attr(test, serde(default))]
    pub background: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    #[cfg_attr(test, serde(default))]
    pub brightness: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    #[cfg_attr(test, serde(default))]
    pub scale: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    #[cfg_attr(test, serde(default))]
    pub scales: Option<Vec<i64>>,
}

fn strictly_increasing(v: &[i64]) -> bool {
    v.windows(2).all(|w| w[0] < w[1])
}

/// `placement_menu_frame`: the open (or collapsed) placement panel, checked in the Python's order.
pub fn placement_menu_frame(
    run_id: &str,
    session: &str,
    request_id: u64,
    menu: &Menu,
) -> Result<String> {
    let invalid = || Err(Invalid::new("invalid menu"));
    let placements = validate_placements(&menu.placements)?;
    if menu.desktop >= placements.len() || (menu.surface.is_some() && !menu.open) {
        return invalid();
    }
    let surface = menu.surface.as_ref().map(validate_placement).transpose()?;
    if let Some(names) = &menu.names {
        if names.len() != placements.len()
            || !names.iter().all(|n| (1..=MAX_DESKTOPS as i64).contains(n))
            || !strictly_increasing(names)
        {
            return invalid();
        }
    }
    let lists =
        [menu.picker, menu.resolution_picker, menu.background_picker].map(|v| v == Some(true));
    if lists.iter().any(|&l| l) && !menu.open || lists.iter().filter(|&&l| l).count() > 1 {
        return invalid();
    }
    if let Some([width, height]) = menu.resolution {
        if validate_desktop(&Desktop {
            width,
            height,
            scale: 1.0,
        })
        .is_err()
        {
            return invalid();
        }
    }
    let (lo, hi) = environment::BRIGHTNESS_BOUNDS;
    let named = |b: &str| environment::BACKGROUNDS.iter().any(|(n, _)| *n == b);
    if menu.background.as_deref().is_some_and(|b| !named(b))
        || menu.brightness.is_some_and(|b| !(lo..=hi).contains(&b))
        || menu.scale.is_some_and(|s| !SCALE_PERCENTS.contains(&s))
        || menu.scales.as_ref().is_some_and(|s| {
            !s.iter().all(|v| SCALE_PERCENTS.contains(v)) || !strictly_increasing(s)
        })
    {
        return invalid();
    }
    let payload = Menu {
        placements,
        surface,
        ..menu.clone()
    };
    Ok(frame(
        run_id,
        session,
        request_id,
        "placement_menu",
        payload,
    ))
}

/// The `view` payload: what the renderer shows and how it follows the head.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[cfg_attr(test, derive(serde::Deserialize), serde(deny_unknown_fields))]
pub struct ViewPayload {
    pub zoomed: bool,
    pub selected: usize,
    pub spacing: i64,
    pub mode: Mode,
    /// Always false: cursor follow was removed, the key stays for the renderer's schema.
    pub cursor_follow: bool,
    pub prediction: bool,
    pub typing: bool,
    pub stabilization: i64,
}

/// `view_frame`.
pub fn view_frame(run_id: &str, session: &str, request_id: u64, state: &ViewPayload) -> String {
    frame(run_id, session, request_id, "view", state)
}

/// `focus_command`: the hyprctl command that focuses a managed output, and only one.
pub fn focus_command(name: &str) -> Result<String> {
    if !managed(name) {
        return Err(Invalid::new("focus is limited to managed outputs"));
    }
    Ok(format!("dispatch hl.dsp.focus({{monitor=\"{name}\"}})"))
}

/// `layout.json`'s view settings, in the file's key order. `mode` is kept for the file's schema and not read.
#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct Settings {
    pub spacing: i64,
    pub mode: Mode,
    pub prediction: bool,
    pub stabilization: i64,
    pub video_quality: Quality,
    pub environment: Environment,
    pub immersive: immersive::Setting,
}

impl Default for Settings {
    /// `DEFAULT_SETTINGS`.
    fn default() -> Settings {
        Settings {
            spacing: DEFAULT_SPACING,
            mode: Mode::Anchor,
            prediction: true,
            stabilization: DEFAULT_STABILIZATION,
            video_quality: Quality::default(),
            environment: environment::DEFAULTS,
            immersive: immersive::Setting::default(),
        }
    }
}

impl Settings {
    /// `_valid`.
    pub fn is_valid(&self) -> bool {
        (0..=MAX_SPACING).contains(&self.spacing)
            && (0..=100).contains(&self.stabilization)
            && environment::is_valid(&self.environment.to_json())
            && immersive::is_valid(&self.immersive.to_value())
    }
}

/// `settings_path`: `$XDG_CONFIG_HOME/viture-workspace/layout.json`, else under `~/.config`. None without HOME.
pub fn settings_path() -> Option<PathBuf> {
    let base = std::env::var_os("XDG_CONFIG_HOME")
        .filter(|v| !v.is_empty())
        .map(PathBuf::from)
        .or_else(|| std::env::var_os("HOME").map(|h| PathBuf::from(h).join(".config")))?;
    Some(base.join("viture-workspace").join("layout.json"))
}

/// `validate_stabilization`.
pub fn validate_stabilization(value: i64) -> Result<i64> {
    if (0..=100).contains(&value) {
        Ok(value)
    } else {
        Err(Invalid::new("invalid stabilization"))
    }
}

/// `load_settings`: each setting from the file when valid, else its default; any unreadable file gives the
/// defaults. One difference from the Python: `json.loads` takes NaN, Infinity and numbers past a float's range, which
/// only lose the field they are in there; here such a file is not JSON and gives the defaults (a file
/// `save_settings` wrote never has them).
pub fn load_settings(path: &Path) -> Settings {
    fs::read_to_string(path)
        .ok()
        .and_then(|text| Json::parse(&text).ok())
        .map_or_else(Settings::default, |data| settings_from(&data))
}

/// `load_settings` on a parsed object (`profiles._view_part`, a profile's view settings): each setting when valid,
/// else its default; anything but an object gives the defaults.
pub fn settings_from(data: &Json) -> Settings {
    let mut out = Settings::default();
    if data.as_object().is_none() {
        return out;
    }
    let int_in = |key, top| {
        data.get(key)
            .and_then(Json::as_int)
            .filter(|v| (0..=top).contains(v))
    };
    if let Some(v) = int_in("spacing", MAX_SPACING) {
        out.spacing = v;
    }
    if let Some(mode) = data
        .get("mode")
        .and_then(Json::as_str)
        .and_then(Mode::parse)
    {
        out.mode = mode;
    }
    if let Some(v) = data.get("prediction").and_then(Json::as_bool) {
        out.prediction = v;
    }
    if let Some(v) = int_in("stabilization", 100) {
        out.stabilization = v;
    }
    out.video_quality = quality::clean(
        &data
            .get("video_quality")
            .map_or(Value::Null, Json::to_value),
    );
    out.environment =
        environment::clean(&data.get("environment").map_or(Value::Null, Json::to_value));
    out.immersive = immersive::clean(&data.get("immersive").map_or(Value::Null, Json::to_value));
    out
}

/// `save_settings`: refuses invalid settings, then writes `json.dump`'s bytes to `layout.json.tmp` (no-follow, mode
/// 0600) and renames it over the file. A missing folder is made, the last one mode 0700.
pub fn save_settings(path: &Path, settings: &Settings) -> io::Result<()> {
    if !settings.is_valid() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "invalid settings",
        ));
    }
    let parent = path
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .unwrap_or(Path::new("."));
    // pathlib's mkdir(parents=True, mode=0o700): the folders above get the default mode, only the last 0700.
    if !parent.is_dir() {
        if let Some(above) = parent.parent().filter(|p| !p.as_os_str().is_empty()) {
            fs::create_dir_all(above)?;
        }
        match fs::DirBuilder::new().mode(0o700).create(parent) {
            Err(e) if !(e.kind() == io::ErrorKind::AlreadyExists && parent.is_dir()) => {
                return Err(e);
            }
            _ => {}
        }
    }
    let mut name = path.file_name().unwrap_or_default().to_os_string();
    name.push(".tmp");
    let tmp = path.with_file_name(name);
    let mut file = fs::File::options()
        .write(true)
        .create(true)
        .truncate(true)
        .custom_flags(libc::O_NOFOLLOW)
        .mode(0o600)
        .open(&tmp)?;
    file.write_all(pyjson::spaced(settings).as_bytes())?;
    drop(file);
    fs::rename(&tmp, path)
}

/// `save_spacing`: the file's settings with this spacing.
pub fn save_spacing(path: &Path, spacing: i64) -> io::Result<()> {
    if !(0..=MAX_SPACING).contains(&spacing) {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "invalid spacing",
        ));
    }
    save_settings(
        path,
        &Settings {
            spacing,
            ..load_settings(path)
        },
    )
}

/// What a `ViewState` starts from (the Python constructor's keyword arguments and their defaults).
#[derive(Clone, Debug)]
pub struct ViewOptions {
    pub spacing: i64,
    pub prediction: bool,
    pub placements: Vec<Placement>,
    pub video_quality: Quality,
    pub environment: Environment,
    /// Taken as given: pass `immersive::clean`'s result, as the Python cleans the raw value once (cleaning again
    /// would drop the empty `strengths` an all-invalid table leaves, which `settings` then refuses).
    pub immersive: immersive::Setting,
    pub stabilization: i64,
}

impl Default for ViewOptions {
    fn default() -> ViewOptions {
        ViewOptions {
            spacing: DEFAULT_SPACING,
            prediction: true,
            placements: Vec::new(),
            video_quality: Quality::default(),
            environment: environment::DEFAULTS,
            immersive: immersive::Setting::default(),
            stabilization: DEFAULT_STABILIZATION,
        }
    }
}

/// `ViewState`: one run's view, its placements and its placement panel.
#[derive(Clone, Debug, PartialEq)]
pub struct ViewState {
    pub names: Vec<String>,
    pub zoomed: bool,
    pub selected: usize,
    /// Placements kept while maximized, applied on the way back to the overview.
    pub held: BTreeMap<String, Placement>,
    pub spacing: i64,
    pub prediction: bool,
    pub stabilization: i64,
    pub maximized_mode: Mode,
    pub video_quality: Quality,
    pub environment: Environment,
    pub immersive: immersive::Setting,
    pub background_picker: bool,
    pub placements: Vec<Placement>,
    pub typing: bool,
    pub menu_open: bool,
    pub menu_desktop: usize,
    pub menu_field: Field,
    pub menu_surface: Option<Placement>,
    pub wide: Vec<bool>,
    pub remove_armed: bool,
    pub desktops_busy: bool,
    pub sizes: Option<Vec<Desktop>>,
    pub scale_picker: bool,
    pub resolution_picker: bool,
}

fn check_names(names: &[&str]) -> Result<()> {
    if names.is_empty() || names.len() > MAX_DESKTOPS || !names.iter().all(|n| managed(n)) {
        return Err(Invalid::new("managed outputs required"));
    }
    Ok(())
}

fn index_of(names: &[String], name: Option<&String>) -> Option<usize> {
    name.and_then(|name| names.iter().position(|n| n == name))
}

impl ViewState {
    pub fn new(names: &[&str], options: ViewOptions) -> Result<ViewState> {
        check_names(names)?;
        let stabilization = validate_stabilization(options.stabilization)?;
        let given = &options.placements[..options.placements.len().min(names.len())];
        let mut placements = given
            .iter()
            .map(validate_placement)
            .collect::<Result<Vec<_>>>()?;
        placements.resize(names.len(), DEFAULT_PLACEMENT);
        Ok(ViewState {
            names: names.iter().map(|n| n.to_string()).collect(),
            zoomed: false,
            selected: 0,
            held: BTreeMap::new(),
            spacing: options.spacing.clamp(0, MAX_SPACING),
            prediction: options.prediction,
            stabilization,
            maximized_mode: Mode::Follow,
            video_quality: options.video_quality,
            environment: environment::clean(&options.environment.to_json()),
            immersive: options.immersive,
            background_picker: false,
            placements,
            typing: false,
            menu_open: false,
            menu_desktop: 0,
            menu_field: Field::X,
            menu_surface: None,
            wide: vec![false; names.len()],
            remove_armed: false,
            desktops_busy: false,
            sizes: None,
            scale_picker: false,
            resolution_picker: false,
        })
    }

    /// The desktops' numbers: XR-2 is 2.
    pub fn numbers(&self) -> Vec<i64> {
        self.names
            .iter()
            .map(|n| i64::from(n.as_bytes()[3] - b'0'))
            .collect()
    }

    /// `set_desktops`: a new desktop set. The selection and an open panel stay on their desktop when it is still
    /// there; `focus` opens the panel on that desktop (not while maximized). Unlike the Python, which has already
    /// taken the new names when a placement is refused, a refused call changes nothing.
    pub fn set_desktops(
        &mut self,
        names: &[&str],
        placements: &[Placement],
        wide: &[bool],
        focus: Option<i64>,
        sizes: Option<&[Desktop]>,
    ) -> Result<()> {
        check_names(names)?;
        if placements.len() != names.len() || wide.len() != names.len() {
            return Err(Invalid::new("one placement per desktop required"));
        }
        if sizes.is_some_and(|s| s.len() != names.len()) {
            return Err(Invalid::new("one size per desktop required"));
        }
        let placements = placements
            .iter()
            .map(validate_placement)
            .collect::<Result<Vec<_>>>()?;
        let names: Vec<String> = names.iter().map(|n| n.to_string()).collect();
        let selected = self.names.get(self.selected);
        let open_on = if self.menu_open {
            self.names.get(self.menu_desktop)
        } else {
            None
        };
        let new_selected = index_of(&names, selected).unwrap_or(self.selected.min(names.len() - 1));
        let open_index = index_of(&names, open_on);
        let focus = focus
            .and_then(|f| usize::try_from(f).ok())
            .filter(|&f| f < names.len() && !self.zoomed);
        if let Some(focus) = focus {
            (self.menu_open, self.menu_desktop) = (true, focus);
        } else if let Some(i) = open_index {
            self.menu_desktop = i;
        } else {
            (self.menu_open, self.menu_desktop) = (false, self.menu_desktop.min(names.len() - 1));
        }
        self.selected = new_selected;
        self.names = names;
        self.placements = placements;
        self.wide = wide.to_vec();
        self.sizes = sizes.map(<[Desktop]>::to_vec);
        self.menu_surface = None;
        self.close_lists();
        self.remove_armed = false;
        Ok(())
    }

    fn close_lists(&mut self) {
        (
            self.scale_picker,
            self.resolution_picker,
            self.background_picker,
        ) = (false, false, false);
    }

    /// `size`: the desktop's pixel size and scale, when the run knows every desktop's.
    pub fn size(&self, desktop: usize) -> Option<Desktop> {
        self.sizes
            .as_ref()
            .filter(|s| s.len() == self.names.len())
            .and_then(|s| s.get(desktop).copied())
    }

    /// `menu`: the panel as `placement_menu_frame` sends it.
    pub fn menu(&self) -> Menu {
        let desktop = self.menu_desktop.min(self.names.len() - 1);
        let size = self.size(desktop);
        Menu {
            open: self.menu_open,
            desktop,
            field: self.menu_field,
            placements: self.placements.clone(),
            surface: self.menu_surface.filter(|_| self.menu_open),
            names: Some(self.numbers()),
            wide: Some(self.wide.get(desktop).copied().unwrap_or(false)),
            armed: Some(self.menu_open && self.remove_armed),
            busy: Some(self.desktops_busy),
            picker: size.map(|_| self.menu_open && self.scale_picker),
            resolution_picker: size.map(|_| self.menu_open && self.resolution_picker),
            background_picker: Some(self.menu_open && self.background_picker),
            resolution: size.map(|s| [s.width, s.height]),
            background: Some(self.environment.background.to_string()),
            brightness: Some(self.environment.brightness),
            scale: size.map(|s| scale_percent(s.scale)),
            scales: size.map(|s| {
                valid_scales(s.width, s.height)
                    .into_iter()
                    .map(scale_percent)
                    .collect()
            }),
        }
    }

    /// `open_menu`: expand this desktop's pill.
    pub fn open_menu(&mut self, desktop: i64) -> Result<()> {
        let desktop = usize::try_from(desktop)
            .ok()
            .filter(|&d| d < self.names.len())
            .ok_or_else(|| Invalid::new("invalid desktop"))?;
        (self.menu_open, self.menu_desktop, self.menu_surface) = (true, desktop, None);
        self.remove_armed = false;
        self.close_lists();
        Ok(())
    }

    /// `hold_surface`: the placement the renderer keeps the panel on while a slider is held (None: let go).
    pub fn hold_surface(&mut self, placement: Option<&Placement>) -> Result<()> {
        let placement = placement.map(validate_placement).transpose()?;
        self.menu_surface = placement.filter(|_| self.menu_open);
        Ok(())
    }

    /// `slide`: a slider at `t` (0..1) of its range. True when the placement changed.
    pub fn slide(&mut self, field: &str, t: f64) -> Result<bool> {
        let bounds = placement_bounds().find(|(k, _, _)| *k == field);
        let Some((key, lo, hi)) = bounds.filter(|_| t.is_finite()) else {
            return Err(Invalid::new("invalid slider"));
        };
        if !self.menu_open {
            return Ok(false);
        }
        self.menu_field = Field::parse(key).expect("placement keys are fields");
        let value = lo + ((py_min(py_max(t, 0.0), 1.0) * (hi - lo) as f64 + 0.5).floor() as i64);
        let current = self.placements[self.menu_desktop];
        if current.get(key) == Some(value) {
            return Ok(false);
        }
        self.placements[self.menu_desktop] = current.with(key, value);
        Ok(true)
    }

    /// `apply_menu`: one panel key. True when the open desktop's placement changed.
    pub fn apply_menu(&mut self, op: &str) -> Result<bool> {
        if !MENU_OPS.contains(&op) {
            return Err(Invalid::new("unsupported menu operation"));
        }
        self.close_lists();
        if MENU_DESKTOP_OPS.iter().any(|(o, _)| *o == op) {
            return Ok(false);
        }
        let op = match op {
            "menu_back" if self.menu_open => "menu_toggle",
            "menu_back" => return Ok(false),
            op => op,
        };
        if op == "menu_toggle" {
            if self.menu_open {
                (self.menu_open, self.menu_surface, self.remove_armed) = (false, None, false);
            } else {
                self.open_menu(self.selected as i64)?;
            }
            return Ok(false);
        }
        if !self.menu_open {
            return Ok(false);
        }
        let step = |forward: bool| if forward { 1 } else { -1 };
        match op {
            "menu_desktop_next" | "menu_desktop_prev" => {
                let n = self.names.len() as i64;
                self.menu_desktop = (self.menu_desktop as i64 + step(op == "menu_desktop_next"))
                    .rem_euclid(n) as usize;
                self.menu_surface = None;
                self.remove_armed = false;
                return Ok(false);
            }
            "menu_field_next" | "menu_field_prev" => {
                let i = Field::ALL
                    .iter()
                    .position(|f| *f == self.menu_field)
                    .expect("a field") as i64;
                let n = Field::ALL.len() as i64;
                self.menu_field =
                    Field::ALL[(i + step(op == "menu_field_next")).rem_euclid(n) as usize];
                return Ok(false);
            }
            _ => {}
        }
        let current = self.placements[self.menu_desktop];
        let updated = if op == "menu_reset" {
            DEFAULT_PLACEMENT
        } else if self.menu_field == Field::Brightness {
            return Ok(false);
        } else {
            let key = self.menu_field.name();
            let &(_, small, big) = MENU_STEPS
                .iter()
                .find(|(k, _, _)| *k == key)
                .expect("placement fields have steps");
            let delta =
                if op.ends_with("_big") { big } else { small } * step(op.starts_with("menu_inc"));
            let (_, lo, hi) = placement_bounds()
                .find(|(k, _, _)| *k == key)
                .expect("placement key");
            let mut value = current.get(key).expect("placement key") + delta;
            if key == "x" {
                value = geometry::wrap(value);
            }
            current.with(key, value.clamp(lo, hi))
        };
        if updated == current {
            return Ok(false);
        }
        self.placements[self.menu_desktop] = updated;
        Ok(true)
    }

    fn checked(&self, placements: &[Placement]) -> Result<Vec<Placement>> {
        if placements.len() != self.names.len() {
            return Err(Invalid::new("one placement per desktop required"));
        }
        placements.iter().map(validate_placement).collect()
    }

    /// `set_placements`: every desktop's placement. True when that changed anything.
    pub fn set_placements(&mut self, placements: &[Placement]) -> Result<bool> {
        let placements = self.checked(placements)?;
        if placements == self.placements {
            return Ok(false);
        }
        self.placements = placements;
        Ok(true)
    }

    /// `hold_placements`: placements to apply later (maximized), kept for the desktops they change.
    pub fn hold_placements(&mut self, placements: &[Placement]) -> Result<()> {
        let placements = self.checked(placements)?;
        self.held = self
            .names
            .iter()
            .zip(placements)
            .zip(&self.placements)
            .filter(|((_, p), now)| p != *now)
            .map(|((n, p), _)| (n.clone(), p))
            .collect();
        Ok(())
    }

    /// `wanted_placements`: the placements with the held ones in their place.
    pub fn wanted_placements(&self) -> Vec<Placement> {
        self.names
            .iter()
            .zip(&self.placements)
            .map(|(n, p)| *self.held.get(n).unwrap_or(p))
            .collect()
    }

    /// The viewing mode: anchor in the overview, the maximized choice when maximized.
    pub fn mode(&self) -> Mode {
        if self.zoomed {
            self.maximized_mode
        } else {
            Mode::Anchor
        }
    }

    pub fn view(&self) -> ViewPayload {
        ViewPayload {
            zoomed: self.zoomed,
            selected: self.selected,
            spacing: self.spacing,
            mode: self.mode(),
            cursor_follow: false,
            prediction: self.prediction,
            typing: self.typing,
            stabilization: self.stabilization,
        }
    }

    /// `settings`: what `save_settings` writes. Refused when the immersive setting is not valid, as the Python does
    /// (a saved per-desktop strength table whose every entry is out of range leaves an empty one).
    pub fn settings(&self) -> Result<Settings> {
        Ok(Settings {
            spacing: self.spacing,
            mode: Mode::Anchor,
            prediction: self.prediction,
            stabilization: self.stabilization,
            video_quality: self.video_quality,
            environment: self.environment,
            immersive: immersive::validate(&self.immersive.to_value())?,
        })
    }

    /// `apply`: one view key. Some(output) when that output should take Hyprland's focus.
    pub fn apply(&mut self, op: &str) -> Result<Option<String>> {
        match op {
            "recenter" | "help" => {}
            "mode_next" => {
                if self.zoomed {
                    self.maximized_mode = if self.maximized_mode == Mode::Follow {
                        Mode::Locked
                    } else {
                        Mode::Follow
                    };
                }
            }
            "prediction" => self.prediction = !self.prediction,
            "zoom_in" => {
                self.zoomed = true;
                return Ok(Some(self.names[self.selected].clone()));
            }
            "zoom_out" => self.zoomed = false,
            "next" | "prev" => {
                let target = self.selected as i64 + if op == "next" { 1 } else { -1 };
                if (0..self.names.len() as i64).contains(&target) {
                    self.selected = target as usize;
                    return Ok(Some(self.names[self.selected].clone()));
                }
            }
            "spacing_up" | "spacing_down" => {
                let delta = if op == "spacing_up" {
                    SPACING_STEP
                } else {
                    -SPACING_STEP
                };
                self.spacing = (self.spacing + delta).clamp(0, MAX_SPACING);
            }
            _ => return Err(Invalid::new("unsupported view operation")),
        }
        Ok(None)
    }
}

/// `tests/native_spike/test_view.py`, the parts outside `ViewControl` and the bind Lua.
#[cfg(test)]
mod tests {
    use super::*;
    use crate::testdir::TestDir;
    use std::os::unix::fs::PermissionsExt;

    const NAMES: [&str; 3] = ["XR-1", "XR-2", "XR-3"];

    fn state() -> ViewState {
        ViewState::new(&NAMES, ViewOptions::default()).unwrap()
    }

    fn payload(zoomed: bool, selected: usize, mode: Mode) -> ViewPayload {
        ViewPayload {
            zoomed,
            selected,
            spacing: 40,
            mode,
            cursor_follow: false,
            prediction: true,
            typing: false,
            stabilization: 25,
        }
    }

    #[test]
    fn transitions_focus_and_view() {
        let mut v = state();
        assert_eq!(v.view(), payload(false, 0, Mode::Anchor));
        assert_eq!(v.apply("next").unwrap().as_deref(), Some("XR-2"));
        assert_eq!(v.apply("zoom_in").unwrap().as_deref(), Some("XR-2"));
        assert_eq!(v.view(), payload(true, 1, Mode::Follow));
        assert_eq!(v.apply("next").unwrap().as_deref(), Some("XR-3"));
        assert_eq!(v.apply("next").unwrap(), None);
        assert_eq!(v.apply("prev").unwrap().as_deref(), Some("XR-2"));
        assert_eq!(v.apply("zoom_out").unwrap(), None);
        assert_eq!((v.view().zoomed, v.view().mode), (false, Mode::Anchor));
        assert_eq!(v.apply("prev").unwrap().as_deref(), Some("XR-1"));
        assert_eq!(v.apply("prev").unwrap(), None);
        assert_eq!(
            v.apply("explode").unwrap_err().0,
            "unsupported view operation"
        );
    }

    #[test]
    fn maximized_mode_toggles_and_is_never_saved() {
        let mut v = state();
        v.apply("mode_next").unwrap();
        assert_eq!((v.mode(), v.zoomed), (Mode::Anchor, false));
        v.apply("zoom_in").unwrap();
        assert_eq!(v.mode(), Mode::Follow);
        let modes: Vec<Mode> = (0..3)
            .map(|_| (v.apply("mode_next").unwrap(), v.mode()).1)
            .collect();
        assert_eq!(modes, [Mode::Locked, Mode::Follow, Mode::Locked]);
        v.apply("zoom_out").unwrap();
        assert_eq!(v.mode(), Mode::Anchor);
        v.apply("zoom_in").unwrap();
        assert_eq!(v.mode(), Mode::Locked);
        assert_eq!(v.settings().unwrap().mode, Mode::Anchor);
        assert_eq!(state().maximized_mode, Mode::Follow);
    }

    #[test]
    fn spacing_bounded_and_persisted() {
        let dir = TestDir::new("view-spacing");
        let path = dir.path().join("cfg").join("layout.json");
        let mut v = ViewState::new(
            &NAMES,
            ViewOptions {
                spacing: load_settings(&path).spacing,
                ..Default::default()
            },
        )
        .unwrap();
        assert_eq!(v.view().spacing, DEFAULT_SPACING);
        for _ in 0..100 {
            v.apply("spacing_up").unwrap();
        }
        assert_eq!(v.view().spacing, MAX_SPACING);
        save_spacing(&path, v.view().spacing).unwrap();
        assert_eq!(load_settings(&path).spacing, MAX_SPACING);
        assert_eq!(
            fs::metadata(&path).unwrap().permissions().mode() & 0o777,
            0o600
        );
        assert_eq!(
            fs::metadata(path.parent().unwrap())
                .unwrap()
                .permissions()
                .mode()
                & 0o777,
            0o700
        );
        for _ in 0..100 {
            v.apply("spacing_down").unwrap();
        }
        assert_eq!(v.view().spacing, 0);
        for bad in [
            r#"{"spacing": -1}"#,
            r#"{"spacing": 9999}"#,
            r#"{"spacing": true}"#,
            "[1]",
            "not json",
            r#"{"spacing": 1.5}"#,
        ] {
            fs::write(&path, bad).unwrap();
            assert_eq!(load_settings(&path).spacing, DEFAULT_SPACING, "{bad}");
        }
        assert_eq!(
            save_spacing(&path, MAX_SPACING + 1).unwrap_err().kind(),
            io::ErrorKind::InvalidInput
        );
    }

    #[test]
    fn renderer_frame_is_one_view_line() {
        let frame = view_frame("run", "session", 7, &payload(true, 2, Mode::Follow));
        assert!(frame.ends_with('\n') && frame.matches('\n').count() == 1);
        let decoded: serde_json::Value = serde_json::from_str(&frame).unwrap();
        assert_eq!(decoded["op"], "view");
        assert!(USER_OPS.contains(&"prediction"));
    }

    #[test]
    fn help_bind_and_rows_cover_every_bind() {
        assert!(BINDS.contains(&("CTRL + ALT + H", "help")));
        let ops: Vec<&str> = BINDS.iter().map(|(_, op)| *op).collect();
        let mut actions: Vec<&str> = HELP_ACTIONS.iter().map(|(op, _)| *op).collect();
        let mut sorted = ops.clone();
        sorted.sort();
        actions.sort();
        assert_eq!(sorted, actions);
        assert_eq!(chord_label("CTRL + ALT + bracketleft"), "Ctrl+Alt+[");
        assert_eq!(chord_label("CTRL + ALT + V"), "Ctrl+Alt+V");
        let rows = help_rows(None, None).unwrap();
        for text in rows.iter().flatten() {
            // Renderer limits: 1..48 printable ASCII, at most 64 rows (ipc.hpp kMaxHelpRows).
            assert!(
                !text.is_empty()
                    && text.len() <= 48
                    && text.bytes().all(|b| (b' '..=b'~').contains(&b)),
                "{text}"
            );
        }
        assert!(rows.len() <= 64);
    }

    #[test]
    fn ctrl_alt_a_is_gone_and_its_saved_setting_ignored() {
        assert!(
            !BINDS
                .iter()
                .any(|(chord, op)| *chord == "CTRL + ALT + A" || *op == "cursor_anim_toggle")
        );
        let rows = help_rows(None, None).unwrap();
        assert!(
            !rows
                .iter()
                .any(|[keys, _]| keys.contains("Ctrl+Alt+A") || keys.contains("/ A"))
        );
        assert!(rows.contains(&["Ctrl+Alt+V".into(), "Maximized: follow / locked".into()]));
        let dir = TestDir::new("view-anim");
        let path = dir.path().join("layout.json");
        fs::write(&path, r#"{"spacing": 60, "mode": "follow", "prediction": false, "cursor_switch_animate": false}"#).unwrap();
        let loaded = load_settings(&path);
        assert_eq!(
            loaded,
            Settings {
                spacing: 60,
                mode: Mode::Follow,
                prediction: false,
                ..Default::default()
            }
        );
        save_spacing(&path, 80).unwrap();
        assert!(
            !fs::read_to_string(&path)
                .unwrap()
                .contains("cursor_switch_animate")
        );
    }

    #[test]
    fn settings_fields_fall_back_independently() {
        let dir = TestDir::new("view-fallback");
        let path = dir.path().join("layout.json");
        fs::write(&path, r#"{"spacing": 80}"#).unwrap();
        assert_eq!(
            load_settings(&path),
            Settings {
                spacing: 80,
                ..Default::default()
            }
        );
        fs::write(&path, r#"{"spacing": 9999, "mode": "orbit", "cursor_follow": 1, "prediction": "on", "cursor_switch_animate": 0}"#)
            .unwrap();
        assert_eq!(load_settings(&path), Settings::default());
        assert!(Settings::default().is_valid());
        let bad = Settings {
            stabilization: 101,
            ..Default::default()
        };
        assert_eq!(
            save_settings(&path, &bad).unwrap_err().to_string(),
            "invalid settings"
        );
    }

    #[test]
    fn prediction_defaults_on_and_starts_off_when_asked() {
        assert!(Settings::default().prediction);
        let mut v = state();
        v.apply("prediction").unwrap();
        assert_eq!(
            v.view(),
            ViewPayload {
                prediction: false,
                ..payload(false, 0, Mode::Anchor)
            }
        );
        assert!(!v.settings().unwrap().prediction);
        let off = ViewState::new(
            &NAMES,
            ViewOptions {
                prediction: false,
                ..Default::default()
            },
        )
        .unwrap();
        assert!(!off.view().prediction);
    }

    #[test]
    fn help_card_has_every_bind_and_no_turn() {
        let frame = help_frame("run", "session", 3, true, 0, None, None).unwrap();
        let shown: serde_json::Value = serde_json::from_str(&frame).unwrap();
        assert_eq!(shown["op"], "help");
        assert_eq!(shown["payload"]["visible"], true);
        assert!(shown["payload"].get("turn").is_none());
        let rows = help_rows(None, None).unwrap();
        assert_eq!(
            shown["payload"]["rows"],
            serde_json::to_value(&rows).unwrap()
        );
        let sections = help_sections(None, None).unwrap();
        assert_eq!(
            shown["payload"]["sections"],
            serde_json::to_value(&sections).unwrap()
        );
        assert_eq!(sections.iter().sum::<usize>(), rows.len());
        let keys: Vec<&str> = rows.iter().map(|[k, _]| k.as_str()).collect();
        let covered = |label: &str| {
            let cut = label.rfind('+').map_or(0, |i| i + 1);
            let (prefix, key) = label.split_at(cut);
            keys.contains(&label)
                || keys.iter().any(|k| {
                    k.starts_with(prefix) && k[prefix.len()..].split(" / ").any(|part| part == key)
                })
        };
        for (chord, op) in BINDS {
            assert!(covered(&chord_label(chord)), "{op}");
        }
        for (keys, text) in [
            ("Ctrl+Alt+[ / ]", "Less / more space between desktops"),
            (
                "Ctrl+Alt+O / U / C",
                "VR video: file / web link / hide controls",
            ),
            (
                "Ctrl+Alt+H / K",
                "Help/binds; Ctrl+Alt+G/B: window to XR/back",
            ),
        ] {
            assert!(rows.contains(&[keys.into(), text.into()]), "{keys}");
        }
        assert_eq!(
            help_frame("run", "session", 3, true, 2, None, None)
                .unwrap_err()
                .0,
            "invalid help turn"
        );
    }

    #[test]
    fn focus_command_allowlisted() {
        assert_eq!(
            focus_command("XR-2").unwrap(),
            r#"dispatch hl.dsp.focus({monitor="XR-2"})"#
        );
        assert_eq!(
            focus_command("XR-4").unwrap(),
            r#"dispatch hl.dsp.focus({monitor="XR-4"})"#
        );
        for bad in ["DP-1", "XR-5", r#"XR-1"})"#, ""] {
            assert_eq!(
                focus_command(bad).unwrap_err().0,
                "focus is limited to managed outputs",
                "{bad}"
            );
        }
    }

    #[test]
    fn parse_border_reads_the_first_colour() {
        let parse = |text: &str| parse_border(&Json::parse(text).unwrap());
        assert_eq!(
            parse(
                r#"{"option": "general:col.active_border", "gradient": "ff8d8d8d 0deg", "set": true}"#
            ),
            Some([0x8d; 3])
        );
        assert_eq!(
            parse(r#"{"gradient": "ee33ccff ee00ff99 45deg"}"#),
            Some([0x33, 0xcc, 0xff])
        );
        assert_eq!(
            parse(&format!(r#"{{"int": {}}}"#, 0xff112233u32)),
            Some([0x11, 0x22, 0x33])
        );
        for bad in [
            "null",
            "[]",
            "{}",
            r#"{"gradient": ""}"#,
            r#"{"gradient": "zz 0deg"}"#,
            r#"{"gradient": "ff8d8d 0deg"}"#,
            r#"{"int": -1}"#,
            r#"{"int": true}"#,
        ] {
            assert_eq!(parse(bad), None, "{bad}");
        }
    }

    #[test]
    fn view_state_refuses_bad_starts() {
        let start =
            |names: &[&str], options| ViewState::new(names, options).map(|_| ()).unwrap_err().0;
        assert_eq!(
            start(&[], ViewOptions::default()),
            "managed outputs required"
        );
        assert_eq!(
            start(&["XR-1", "HDMI-A-1"], ViewOptions::default()),
            "managed outputs required"
        );
        assert_eq!(
            start(
                &["XR-1"],
                ViewOptions {
                    stabilization: 101,
                    ..Default::default()
                }
            ),
            "invalid stabilization"
        );
    }
}
