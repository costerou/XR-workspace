//! The key bind panel (Ctrl+Alt+K by default): every run bind, rebindable live in the glasses, with conflicts. The
//! port of `tools/native_spike/keybinds.py`; its module docstring explains the panel, how its keys reach the run and
//! how conflicts are found.
//!
//! The Python panel runs its compositor work (the panel's key subscription, the run binds reinstalled with new keys,
//! reading `hyprctl binds`) on threads. Here the loop side (`BindControl`) only queues that work as `Job`s, each a
//! snapshot of what it needs; whoever owns the compositor client runs them in order with a `BindWorker` and hands the
//! results back with `BindControl::deliver`. Toasts are queued the same way (`take_toasts`).
//!
//! The compositor Lua (the panel's submap and key subscription) belongs to the Lua generators' port.

use std::collections::{BTreeMap, BTreeSet, VecDeque};
use std::io::{self, Write};
use std::os::unix::fs::{DirBuilderExt, OpenOptionsExt};
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};

use serde_json::Value;

use super::bind_check::{Normal, normalize};
use super::{json_str, py_strip};

pub const PANEL_OP: &str = "binds_toggle";
/// The subscription's key presses: payload {"mods": modmask, "code": xkb keycode}.
pub const WIRE_OP: &str = "binds_key";
pub const OPS: [&str; 2] = [PANEL_OP, WIRE_OP];
pub const TYPING_OP: &str = "typing_toggle";
pub const SCHEMA: i64 = 1;
/// A change with no keys pressed is cancelled after this long.
pub const CAPTURE_TIMEOUT_S: f64 = 15.0;
/// Shift+R resets every bind only when pressed twice within this.
pub const RESET_ARM_S: f64 = 4.0;
/// The panel closes by itself after this long without a key (view.OVERLAY_IDLE_CLOSE_S).
pub const IDLE_CLOSE_S: f64 = 30.0;
/// PgUp / PgDn move this many rows.
pub const PAGE: i64 = 8;
/// spike/include/xr/binds_panel.hpp kMaxBindText (notes, hint, status).
pub const MAX_TEXT: usize = 96;
/// Its kMaxBindLabel (action, keys, title).
pub const MAX_LABEL: usize = 48;

/// Hyprland modmask bits (src/devices/IKeyboard.hpp HL_MODIFIER_*), for `hyprctl binds` and the key subscription.
pub const MODMASK: [(i64, &str); 8] = [
    (1, "SHIFT"),
    (2, "CAPS"),
    (4, "CTRL"),
    (8, "ALT"),
    (16, "MOD2"),
    (32, "MOD3"),
    (64, "SUPER"),
    (128, "MOD5"),
];
/// How a chord is written (the defaults: 'CTRL + ALT + K').
pub const MOD_ORDER: [&str; 4] = ["CTRL", "ALT", "SHIFT", "SUPER"];
/// Enter and the keypad's Enter: change the picked bind.
pub const ENTER_CODES: [i64; 2] = [36, 104];

/// xkb keycode (evdev + 8) -> Hyprland key name, US layout. Digits stay keycodes (layout-independent, as Omarchy binds
/// them).
pub const KEYCODES: &[(i64, &str)] = &[
    (9, "Escape"),
    (10, "code:10"),
    (11, "code:11"),
    (12, "code:12"),
    (13, "code:13"),
    (14, "code:14"),
    (15, "code:15"),
    (16, "code:16"),
    (17, "code:17"),
    (18, "code:18"),
    (19, "code:19"),
    (20, "minus"),
    (21, "equal"),
    (22, "BackSpace"),
    (23, "Tab"),
    (24, "Q"),
    (25, "W"),
    (26, "E"),
    (27, "R"),
    (28, "T"),
    (29, "Y"),
    (30, "U"),
    (31, "I"),
    (32, "O"),
    (33, "P"),
    (34, "bracketleft"),
    (35, "bracketright"),
    (36, "Return"),
    (38, "A"),
    (39, "S"),
    (40, "D"),
    (41, "F"),
    (42, "G"),
    (43, "H"),
    (44, "J"),
    (45, "K"),
    (46, "L"),
    (47, "semicolon"),
    (48, "apostrophe"),
    (49, "grave"),
    (51, "backslash"),
    (52, "Z"),
    (53, "X"),
    (54, "C"),
    (55, "V"),
    (56, "B"),
    (57, "N"),
    (58, "M"),
    (59, "comma"),
    (60, "period"),
    (61, "slash"),
    (65, "space"),
    (67, "F1"),
    (68, "F2"),
    (69, "F3"),
    (70, "F4"),
    (71, "F5"),
    (72, "F6"),
    (73, "F7"),
    (74, "F8"),
    (75, "F9"),
    (76, "F10"),
    (95, "F11"),
    (96, "F12"),
    (110, "Home"),
    (111, "up"),
    (112, "Page_Up"),
    (113, "left"),
    (114, "right"),
    (115, "End"),
    (116, "down"),
    (117, "Page_Down"),
    (118, "Insert"),
    (119, "Delete"),
];

/// How a chord's parts are shown (view.KEY_NAMES); other parts show as written, a single character upper-cased.
pub const KEY_NAMES: &[(&str, &str)] = &[
    ("CTRL", "Ctrl"),
    ("ALT", "Alt"),
    ("SHIFT", "Shift"),
    ("SUPER", "Super"),
    ("up", "Up"),
    ("down", "Down"),
    ("left", "Left"),
    ("right", "Right"),
    ("bracketleft", "["),
    ("bracketright", "]"),
    ("space", "Space"),
    ("Tab", "Tab"),
    ("Escape", "Esc"),
    ("Page_Up", "PgUp"),
    ("Page_Down", "PgDn"),
    ("Return", "Enter"),
    ("BackSpace", "Backspace"),
    ("Delete", "Del"),
    ("Insert", "Ins"),
    ("comma", ","),
    ("period", "."),
    ("slash", "/"),
    ("semicolon", ";"),
    ("apostrophe", "'"),
    ("minus", "-"),
    ("equal", "="),
    ("grave", "`"),
    ("backslash", "\\"),
    ("code:10", "1"),
    ("code:11", "2"),
    ("code:12", "3"),
    ("code:13", "4"),
    ("code:14", "5"),
    ("code:15", "6"),
    ("code:16", "7"),
    ("code:17", "8"),
    ("code:18", "9"),
    ("code:19", "0"),
];

/// (op, default chord, panel label) for every bind the panel lists, in its order: the help card's sections, the other
/// run binds, then the send-window binds.
pub const CATALOG: [(&str, &str, &str); 28] = [
    (
        "zoom_in",
        "CTRL + ALT + up",
        "Maximize the selected desktop",
    ),
    ("zoom_out", "CTRL + ALT + down", "Overview"),
    ("prev", "CTRL + ALT + left", "Previous desktop"),
    ("next", "CTRL + ALT + right", "Next desktop"),
    (
        "spacing_down",
        "CTRL + ALT + bracketleft",
        "Less space between desktops",
    ),
    (
        "spacing_up",
        "CTRL + ALT + bracketright",
        "More space between desktops",
    ),
    ("recenter", "CTRL + ALT + space", "Recenter the view"),
    (
        "layout_reset",
        "CTRL + ALT + R",
        "Reset to 3 flat desktops (press twice)",
    ),
    ("mode_next", "CTRL + ALT + V", "Maximized: follow / locked"),
    ("prediction", "CTRL + ALT + P", "Toggle pose prediction"),
    (
        "typing_toggle",
        "CTRL + ALT + T",
        "Typing mode: XR keys off / on",
    ),
    (
        "privacy_display",
        "CTRL + ALT + D",
        "Privacy: main display off / on",
    ),
    (
        "environment_toggle",
        "CTRL + ALT + E",
        "Background: none / last used",
    ),
    (
        "immersive_toggle",
        "CTRL + ALT + I",
        "Immersive 3D: all desktops on / off",
    ),
    (
        "menu_toggle",
        "CTRL + ALT + L",
        "Placement pill of the selected desktop",
    ),
    ("video_open", "CTRL + ALT + O", "Play a 180/360 VR video"),
    (
        "video_paste",
        "CTRL + ALT + U",
        "Play the browser video you copied",
    ),
    (
        "playback_menu_toggle",
        "CTRL + ALT + C",
        "VR video: hide / show playback controls",
    ),
    ("help", "CTRL + ALT + H", "Help card (every key)"),
    ("binds_toggle", "CTRL + ALT + K", "Key binds (this panel)"),
    ("help_prev", "CTRL + ALT + Page_Up", "Previous help page"),
    ("help_next", "CTRL + ALT + Page_Down", "Next help page"),
    (
        "send_glasses",
        "CTRL + ALT + G",
        "Send window to the selected desktop",
    ),
    (
        "send_glasses_1",
        "CTRL + ALT + code:10",
        "Send window to XR-1",
    ),
    (
        "send_glasses_2",
        "CTRL + ALT + code:11",
        "Send window to XR-2",
    ),
    (
        "send_glasses_3",
        "CTRL + ALT + code:12",
        "Send window to XR-3",
    ),
    (
        "send_glasses_4",
        "CTRL + ALT + code:13",
        "Send window to XR-4",
    ),
    (
        "send_back",
        "CTRL + ALT + B",
        "Send window back to the main display",
    ),
];
/// The run binds, in view.BINDS order (the order the bind Lua installs them).
pub const RUN_OPS: [&str; 22] = [
    "zoom_in",
    "zoom_out",
    "prev",
    "next",
    "spacing_down",
    "spacing_up",
    "recenter",
    "mode_next",
    "prediction",
    "help",
    "typing_toggle",
    "menu_toggle",
    "layout_reset",
    "video_open",
    "video_paste",
    "help_prev",
    "help_next",
    "privacy_display",
    "playback_menu_toggle",
    "binds_toggle",
    "environment_toggle",
    "immersive_toggle",
];
/// The send-window binds, in send.SEND_BINDS order.
pub const SEND_OPS: [&str; 6] = [
    "send_glasses",
    "send_glasses_1",
    "send_glasses_2",
    "send_glasses_3",
    "send_glasses_4",
    "send_back",
];

pub const HINT: &str = "Up/Down pick   Enter change   R reset   Shift+R reset all   Esc close";
pub const CAPTURE_HINT: &str = "Press the new keys (Ctrl, Alt or Super with a key)   Esc cancel";

/// A catalog op as the `'static` name the tables hold, or None if it is not one.
pub fn catalog_op(op: &str) -> Option<&'static str> {
    CATALOG
        .iter()
        .find(|(o, _, _)| *o == op)
        .map(|(o, _, _)| *o)
}

/// The op's default chord. Panics on an op outside CATALOG.
pub fn default_chord(op: &str) -> &'static str {
    CATALOG
        .iter()
        .find(|(o, _, _)| *o == op)
        .map(|(_, c, _)| *c)
        .unwrap_or_else(|| panic!("unknown bind {op}"))
}

/// The op's label in the panel. Panics on an op outside CATALOG.
pub fn label(op: &str) -> &'static str {
    CATALOG
        .iter()
        .find(|(o, _, _)| *o == op)
        .map(|(_, _, l)| *l)
        .unwrap_or_else(|| panic!("unknown bind {op}"))
}

pub fn keycode_name(code: i64) -> Option<&'static str> {
    KEYCODES.iter().find(|(c, _)| *c == code).map(|(_, n)| *n)
}

/// A key name a bind may use (every KEYCODES name but Escape), from its lower-cased spelling.
fn lookup_key(lower: &str) -> Option<&'static str> {
    match lower {
        "prior" => return Some("Page_Up"),
        "next" => return Some("Page_Down"),
        "enter" => return Some("Return"),
        _ => {}
    }
    if let [d @ b'0'..=b'9'] = lower.as_bytes() {
        return keycode_name(10 + (i64::from(d - b'0') + 9) % 10);
    }
    KEYCODES
        .iter()
        .map(|(_, n)| *n)
        .find(|n| *n != "Escape" && n.to_lowercase() == lower)
}

fn mod_name(upper: &str) -> Option<&'static str> {
    Some(match upper {
        "CTRL" | "CONTROL" => "CTRL",
        "ALT" | "MOD1" => "ALT",
        "SHIFT" => "SHIFT",
        "SUPER" | "WIN" | "LOGO" | "MOD4" | "META" => "SUPER",
        _ => return None,
    })
}

/// A bind's keys from 'CTRL + ALT + K', 'ctrl+alt+k' or the helper's 'CTRL+ALT+K', as written in Hyprland Lua
/// ('CTRL + ALT + K'): modifiers in MOD_ORDER, then the key. An error unless it has Ctrl, Alt or Super (Shift may join
/// them) and a bindable key.
pub fn parse_chord(text: &str) -> Result<String, String> {
    let invalid = || Err("invalid keys".to_string());
    if !(1..=64).contains(&text.chars().count()) {
        return invalid();
    }
    let parts: Vec<&str> = text.split('+').map(py_strip).collect();
    if parts.len() < 2 || parts.iter().any(|p| p.is_empty()) {
        return invalid();
    }
    let mut mods = BTreeSet::new();
    for p in &parts[..parts.len() - 1] {
        match mod_name(&p.to_uppercase()) {
            Some(name) if mods.insert(name) => {}
            _ => return invalid(),
        }
    }
    let Some(key) = lookup_key(&parts[parts.len() - 1].to_lowercase()) else {
        return Err("that key cannot be bound".into());
    };
    if mods.iter().all(|m| *m == "SHIFT") {
        return Err("a bind needs Ctrl, Alt or Super".into());
    }
    let mut out: Vec<&str> = MOD_ORDER
        .iter()
        .copied()
        .filter(|m| mods.contains(m))
        .collect();
    out.push(key);
    Ok(out.join(" + "))
}

/// The subscription's press (modmask, keycode) as a chord string, or None if that is no possible bind.
pub fn key_chord(mods: i64, code: i64) -> Option<String> {
    let key = keycode_name(code)?;
    if mods < 0 {
        return None;
    }
    let mut names: Vec<&str> = MODMASK
        .iter()
        .filter(|(bit, name)| mods & bit != 0 && MOD_ORDER.contains(name))
        .map(|(_, n)| *n)
        .collect();
    names.push(key);
    parse_chord(&names.join(" + ")).ok()
}

/// 'CTRL + ALT + bracketleft' -> 'Ctrl+Alt+['.
pub fn chord_label(chord: &str) -> String {
    let parts: Vec<String> = chord
        .split(" + ")
        .map(|part| match KEY_NAMES.iter().find(|(k, _)| *k == part) {
            Some((_, name)) => name.to_string(),
            None if part.chars().count() == 1 => part.to_uppercase(),
            None => part.to_string(),
        })
        .collect();
    parts.join("+")
}

/// `~/.config/viture-workspace/keybinds.json` (or under $XDG_CONFIG_HOME).
pub fn config_path() -> PathBuf {
    let base = std::env::var_os("XDG_CONFIG_HOME")
        .filter(|v| !v.is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| {
            PathBuf::from(std::env::var_os("HOME").unwrap_or_default()).join(".config")
        });
    base.join("viture-workspace").join("keybinds.json")
}

/// The effective binds: every CATALOG op's default, or its saved override.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct KeyMap {
    /// Only the binds that differ from their default, each a parsed chord.
    pub overrides: BTreeMap<&'static str, String>,
}

impl KeyMap {
    /// Unknown ops, unreadable chords and chords equal to the default are dropped.
    pub fn new<'a>(overrides: impl IntoIterator<Item = (&'a str, &'a str)>) -> KeyMap {
        let mut map = KeyMap::default();
        for (op, chord) in overrides {
            let Some(op) = catalog_op(op) else { continue };
            let Ok(chord) = parse_chord(chord) else {
                continue;
            };
            if chord != default_chord(op) {
                map.overrides.insert(op, chord);
            }
        }
        map
    }

    pub fn chord(&self, op: &str) -> &str {
        self.overrides
            .get(op)
            .map_or_else(|| default_chord(op), String::as_str)
    }

    /// (op, chord) for every CATALOG op, in its order.
    pub fn items(&self) -> Vec<(&'static str, &str)> {
        CATALOG
            .iter()
            .map(|(op, _, _)| (*op, self.chord(op)))
            .collect()
    }

    pub fn custom(&self, op: &str) -> bool {
        self.overrides.contains_key(op)
    }

    pub fn set(&mut self, op: &str, chord: &str) -> Result<(), String> {
        let op = catalog_op(op).ok_or("unknown bind")?;
        let chord = parse_chord(chord)?;
        if chord == default_chord(op) {
            self.overrides.remove(op);
        } else {
            self.overrides.insert(op, chord);
        }
        Ok(())
    }

    /// One op back to its default, or every op (None).
    pub fn reset(&mut self, op: Option<&str>) {
        match op {
            Some(op) => {
                self.overrides.remove(op);
            }
            None => self.overrides.clear(),
        }
    }
}

/// The saved KeyMap at `path` (defaults if it is missing or unreadable; invalid entries are dropped).
pub fn load(path: &Path) -> KeyMap {
    let Ok(text) = std::fs::read_to_string(path) else {
        return KeyMap::default();
    };
    let Ok(Value::Object(data)) = serde_json::from_str::<Value>(&text) else {
        return KeyMap::default();
    };
    // Python compares the schema with ==, so 1.0 and true are 1 too.
    let schema_ok = match data.get("schema") {
        Some(Value::Bool(b)) => *b,
        Some(Value::Number(n)) => n.as_i64() == Some(SCHEMA) || n.as_f64() == Some(SCHEMA as f64),
        _ => false,
    };
    let Some(Value::Object(binds)) = data.get("binds").filter(|_| schema_ok) else {
        return KeyMap::default();
    };
    KeyMap::new(
        binds
            .iter()
            .filter_map(|(op, chord)| Some((op.as_str(), chord.as_str()?))),
    )
}

/// keybinds.json, written atomically, mode 0600: the overrides only. Its directory is made (mode 0700) if missing.
pub fn save(path: &Path, keymap: &KeyMap) -> io::Result<()> {
    if let Some(parent) = path.parent().filter(|p| !p.as_os_str().is_empty()) {
        std::fs::DirBuilder::new()
            .recursive(true)
            .mode(0o700)
            .create(parent)?;
    }
    let mut text = format!("{{\n \"schema\": {SCHEMA},\n \"binds\": ");
    if keymap.overrides.is_empty() {
        text.push_str("{}");
    } else {
        let rows: Vec<String> = keymap
            .overrides
            .iter()
            .map(|(op, chord)| format!("  {}: {}", json_str(op), json_str(chord)))
            .collect();
        text.push_str(&format!("{{\n{}\n }}", rows.join(",\n")));
    }
    text.push_str("\n}");
    let mut name = path.file_name().unwrap_or_default().to_os_string();
    name.push(".tmp");
    let tmp = path.with_file_name(name);
    let mut file = std::fs::OpenOptions::new()
        .write(true)
        .create(true)
        .truncate(true)
        .mode(0o600)
        .custom_flags(libc::O_NOFOLLOW)
        .open(&tmp)?;
    file.write_all(text.as_bytes())?;
    drop(file);
    std::fs::rename(&tmp, path)
}

/// An OS error's text without Rust's " (os error N)" suffix, as Python's `strerror` gives it.
fn strerror(e: &io::Error) -> String {
    let text = e.to_string();
    match (e.raw_os_error(), text.rfind(" (os error ")) {
        (Some(_), Some(at)) => text[..at].to_string(),
        _ => text,
    }
}

// --- the desktop's binds (`hyprctl binds`) ------------------------------------------------------------------------

/// Printable ASCII only (anything else '?'), stripped, and cut to `limit` characters with '...'.
pub fn ascii(text: &str, limit: usize) -> String {
    let text: String = text
        .chars()
        .map(|c| if (' '..'\x7f').contains(&c) { c } else { '?' })
        .collect();
    let text = text.trim_matches(' ');
    if text.len() <= limit {
        return text.to_string();
    }
    format!(
        "{}...",
        text[..limit.saturating_sub(3)].trim_end_matches(' ')
    )
}

/// One bind in plain `hyprctl binds` output.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Entry {
    pub modmask: i64,
    pub submap: String,
    pub key: String,
    pub catchall: bool,
    pub description: String,
    pub dispatcher: String,
}

/// Python's `int()` of a stripped field: an optional sign, then digits with single underscores between them.
/// Wraps at 64 bits; only the low modmask bits matter.
fn py_int(text: &str) -> Option<i64> {
    let (negative, digits) = match text.as_bytes().first() {
        Some(b'-') => (true, &text[1..]),
        Some(b'+') => (false, &text[1..]),
        _ => (false, text),
    };
    if digits.is_empty()
        || digits.starts_with('_')
        || digits.ends_with('_')
        || digits.contains("__")
    {
        return None;
    }
    let mut value: i64 = 0;
    for b in digits.bytes() {
        match b {
            b'_' => {}
            b'0'..=b'9' => value = value.wrapping_mul(10).wrapping_add(i64::from(b - b'0')),
            _ => return None,
        }
    }
    Some(if negative {
        value.wrapping_neg()
    } else {
        value
    })
}

/// Every bind in plain `hyprctl binds` output. (The plain form names a keycode bind's key, 'CTRL + ALT + code:10';
/// Hyprland 0.56's JSON leaves it empty.)
pub fn parse_binds(text: &str) -> Vec<Entry> {
    let text = text.replace('\r', "");
    let mut out = Vec::new();
    for block in text.split("\n\n") {
        let lines: Vec<&str> = block
            .split('\n')
            .filter(|l| !py_strip(l).is_empty())
            .collect();
        let Some(first) = lines.first() else { continue };
        if !first.starts_with("bind") {
            continue;
        }
        let mut fields: BTreeMap<&str, &str> = BTreeMap::new();
        for line in &lines[1..] {
            if let Some((name, value)) = py_strip(line).split_once(':') {
                fields.insert(py_strip(name), py_strip(value));
            }
        }
        let field = |name: &str| fields.get(name).copied().unwrap_or_default().to_string();
        let Some(modmask) = py_int(&field("modmask")) else {
            continue;
        };
        out.push(Entry {
            modmask,
            submap: field("submap"),
            key: field("key"),
            catchall: field("catchall") == "true",
            description: field("description"),
            dispatcher: field("dispatcher"),
        });
    }
    out
}

/// An entry's keys, normalized, or None if no run bind could share them.
fn entry_chord(entry: &Entry) -> Option<Normal> {
    if !entry.submap.is_empty() || entry.catchall {
        return None;
    }
    let mut mods: Vec<&str> = MODMASK
        .iter()
        .filter(|(bit, _)| entry.modmask & bit != 0)
        .map(|(_, n)| *n)
        .collect();
    if !mods.iter().any(|m| ["CTRL", "ALT", "SUPER"].contains(m)) {
        return None; // a run bind always has one of these
    }
    let key = match entry.key.rsplit_once('+') {
        Some((_, last)) => py_strip(last),
        None => py_strip(&entry.key),
    };
    if key.is_empty() || key.starts_with("mouse") || key.starts_with("switch:") {
        return None;
    }
    mods.push(key);
    normalize(&mods.join(" + ")).ok()
}

/// What holds a chord on the desktop: the bind's description (or '' with none), and whether it has one (every
/// Omarchy bind does).
pub type Holder = (String, bool);

/// The desktop's keyboard binds, by normalized chord.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct DesktopBinds {
    pub chords: BTreeMap<Normal, Vec<Holder>>,
}

impl DesktopBinds {
    /// `ours`: chords this run had installed when the binds were read (one undescribed entry each is the run's own).
    pub fn new(entries: &[Entry], ours: &[String]) -> DesktopBinds {
        let mut grouped: BTreeMap<Normal, Vec<&Entry>> = BTreeMap::new();
        for entry in entries {
            if let Some(chord) = entry_chord(entry) {
                grouped.entry(chord).or_default().push(entry);
            }
        }
        let mut mine: BTreeMap<Normal, usize> = BTreeMap::new();
        for chord in ours.iter().filter_map(|c| normalize(c).ok()) {
            *mine.entry(chord).or_default() += 1;
        }
        let mut chords = BTreeMap::new();
        for (chord, mut found) in grouped {
            found.sort_by_key(|e| !e.description.is_empty()); // the run's own binds have no description
            let skip = mine.get(&chord).copied().unwrap_or(0);
            let kept: Vec<Holder> = found
                .iter()
                .skip(skip)
                .map(|e| {
                    let has = !e.description.is_empty();
                    (
                        if has {
                            ascii(&e.description, 60)
                        } else {
                            String::new()
                        },
                        has,
                    )
                })
                .collect();
            if !kept.is_empty() {
                chords.insert(chord, kept);
            }
        }
        DesktopBinds { chords }
    }

    pub fn from_text(text: &str, ours: &[String]) -> DesktopBinds {
        DesktopBinds::new(&parse_binds(text), ours)
    }

    /// (what holds `chord`, described) or None.
    pub fn find(&self, chord: &str) -> Option<Holder> {
        let found = self
            .chords
            .get(&normalize(chord).ok()?)
            .filter(|f| !f.is_empty())?;
        Some(match found.iter().find(|(_, has)| *has) {
            Some((d, _)) => (d.clone(), true),
            None => ("a Hyprland bind with no description".into(), false),
        })
    }
}

pub type Conflicts = BTreeMap<&'static str, String>;
pub type Off = BTreeSet<&'static str>;

/// (notes by op, off) for `keymap`: every bind that shares its keys with another run bind or a desktop bind gets a
/// note saying with what; `off` holds the ones not installed (a described desktop bind holds the keys, or an earlier
/// run bind in CATALOG order does). `desktop` None: unknown, only run binds are compared.
pub fn find_conflicts(keymap: &KeyMap, desktop: Option<&DesktopBinds>) -> (Conflicts, Off) {
    let (mut notes, mut off, mut first) = (
        Conflicts::new(),
        Off::new(),
        BTreeMap::<Normal, &'static str>::new(),
    );
    for (op, chord) in keymap.items() {
        let Ok(key) = normalize(chord) else { continue };
        let held = desktop.and_then(|d| d.find(chord));
        if let Some((what, true)) = &held {
            notes.insert(
                op,
                format!("Off: Hyprland binds {} to \"{what}\"", chord_label(chord)),
            );
            off.insert(op);
            continue;
        }
        if let Some(other) = first.get(&key).copied() {
            notes.insert(
                op,
                format!("Off: {} is already {}", chord_label(chord), label(other)),
            );
            notes
                .entry(other)
                .or_insert_with(|| format!("{} has the same keys (off)", label(op)));
            off.insert(op);
            continue;
        }
        first.insert(key, op);
        if let Some((what, _)) = held {
            notes.insert(
                op,
                format!("Hyprland also binds {} ({what})", chord_label(chord)),
            );
        }
    }
    (
        notes
            .into_iter()
            .map(|(op, n)| (op, ascii(&n, MAX_TEXT)))
            .collect(),
        off,
    )
}

/// The renderer's `binds` frame (one JSON line). `payload` is the payload's JSON text.
pub fn binds_frame(run_id: &str, session: &str, request_id: u64, payload: &str) -> Vec<u8> {
    format!(
        "{{\"schema\":1,\"request_id\":{request_id},\"run_id\":{},\"session\":{},\"op\":\"binds\",\"payload\":{payload}}}\n",
        json_str(run_id),
        json_str(session)
    )
    .into_bytes()
}

// --- the panel ----------------------------------------------------------------------------------------------------

/// One bind to install: (chord, op).
pub type Bind = (String, &'static str);

/// The view control the panel lives in (ViewControl in the Python): its state, the cards the panel closes, and the
/// frame ids.
pub trait Host {
    fn now(&self) -> f64;
    /// Typing mode: only the typing toggle is bound.
    fn typing(&self) -> bool;
    fn video_active(&self) -> bool;
    fn help_visible(&self) -> bool;
    /// Closes the help card: its frames.
    fn close_help(&mut self) -> Vec<u8>;
    /// A placement panel is open.
    fn menu_open(&self) -> bool;
    /// Closes the placement panel: its frames.
    fn close_menu(&mut self) -> Vec<u8>;
    /// The options menu holds the keyboard.
    fn keyboard_owned(&self) -> bool;
    fn run_id(&self) -> &str;
    fn session(&self) -> &str;
    /// The next frame's request id (each call takes one).
    fn next_request_id(&mut self) -> u64;
    /// A key reached the run (the idle close of the other cards counts from it).
    fn set_last_input(&mut self, now: f64);
    /// The run binds were reinstalled (with typing mode as given); the placement panel's or the options menu's
    /// binds were put back with them (true), or are gone.
    fn binds_reinstalled(&mut self, typing: bool, menu_binds: bool, options_binds: bool);
}

/// The compositor work the panel needs (hyprland.HyprlandClient and the view control's bind helpers). Errors are a
/// text saying what failed.
pub trait KeyClient {
    /// Removes and reinstalls the run binds: the defaults (None), or these (run, send) binds.
    fn reinstall_view_binds(
        &mut self,
        typing: bool,
        binds: Option<(&[Bind], &[Bind])>,
    ) -> Result<(), String>;
    fn open_key_panel(&mut self) -> Result<(), String>;
    fn close_key_panel(&mut self) -> Result<(), String>;
    /// Plain `hyprctl binds` output.
    fn binds_text(&mut self) -> Result<String, String>;
    /// The placement panel's binds on or off.
    fn menu_binds(&mut self, on: bool) -> Result<(), String>;
    /// The options menu's binds on or off.
    fn options_binds(&mut self, on: bool) -> Result<(), String>;
}

/// Compositor work for a `BindWorker`, with the panel's state as it was when the work was asked for.
#[derive(Clone, Debug, PartialEq)]
pub enum Job {
    /// Run binds reinstalled if `version` is newer than the installed one, and the panel's keyboard taken or given
    /// back to match `want_open` (again if `force`).
    Sync {
        version: u64,
        typing: bool,
        binds: (Vec<Bind>, Vec<Bind>),
        /// The binds differ from the defaults (else the bind Lua is exactly what it always was).
        custom: bool,
        want_open: bool,
        force: bool,
        menu_open: bool,
        keyboard_owned: bool,
    },
    /// Read the desktop's binds.
    Read { typing: bool },
    /// The run binds were installed with this keymap (by the run's start, or after a config reload, which also took
    /// the panel's keyboard: `reset_panel`).
    Installed {
        binds: (Vec<Bind>, Vec<Bind>),
        version: u64,
        reset_panel: bool,
    },
}

/// A worker's answer for the panel.
#[derive(Clone, Debug, PartialEq)]
pub enum Reading {
    /// The desktop's binds, read.
    Desktop(DesktopBinds),
    /// They could not be read: the panel keeps what it knew.
    ReadFailed,
    /// The panel could not take the keyboard.
    Failed,
}

/// What one job did: a reading for the panel, and whether the run binds were reinstalled (typing, menu binds back,
/// options binds back).
#[derive(Clone, Debug, Default, PartialEq)]
pub struct WorkResult {
    pub reading: Option<Reading>,
    pub reinstalled: Option<(bool, bool, bool)>,
}

/// Runs the panel's jobs in order, off the supervisor loop: what the compositor has installed.
#[derive(Debug)]
pub struct BindWorker {
    /// The keymap version the run binds were last installed with.
    pub installed_version: u64,
    /// The panel holds the keyboard.
    pub panel_installed: bool,
    /// (run binds, send binds) last installed.
    pub installed: Option<(Vec<Bind>, Vec<Bind>)>,
    stopped: Arc<AtomicBool>,
}

impl BindWorker {
    /// The chords the run's bind tables hold now (typing mode: only the typing toggle).
    pub fn installed_chords(&self, typing: bool) -> Vec<String> {
        let Some((run, send)) = &self.installed else {
            return Vec::new();
        };
        if typing {
            return run
                .iter()
                .filter(|(_, op)| *op == TYPING_OP)
                .map(|(c, _)| c.clone())
                .collect();
        }
        run.iter().chain(send).map(|(c, _)| c.clone()).collect()
    }

    pub fn run(&mut self, job: Job, client: Option<&mut dyn KeyClient>) -> WorkResult {
        let mut result = WorkResult::default();
        match job {
            Job::Installed {
                binds,
                version,
                reset_panel,
            } => {
                self.installed = Some(binds);
                self.installed_version = version;
                if reset_panel {
                    self.panel_installed = false;
                }
            }
            Job::Read { typing } => {
                let Some(client) = client else { return result };
                result.reading = Some(match client.binds_text() {
                    Ok(text) => Reading::Desktop(DesktopBinds::from_text(
                        &text,
                        &self.installed_chords(typing),
                    )),
                    Err(_) => Reading::ReadFailed,
                });
            }
            Job::Sync {
                version,
                typing,
                binds,
                custom,
                want_open,
                force,
                menu_open,
                keyboard_owned,
            } => {
                let Some(client) = client else { return result };
                if self.stopped.load(Ordering::SeqCst) {
                    return result;
                }
                // Best effort: Esc, the idle close and the run's end still give the keyboard back. A panel that
                // could not take the keyboard closes again, and says so (BindControl::poll).
                let done = (|| -> Result<(), String> {
                    if self.installed_version != version {
                        let given = custom.then_some((binds.0.as_slice(), binds.1.as_slice()));
                        client.reinstall_view_binds(typing, given)?;
                        self.installed = Some(binds.clone());
                        self.installed_version = version;
                        self.panel_installed = false; // removing the run binds closed it too
                        let (mut menu, mut options) = (false, false);
                        let mut back = || -> Result<(), String> {
                            if menu_open {
                                client.menu_binds(true)?;
                                menu = true;
                            } else if keyboard_owned {
                                client.options_binds(true)?;
                                options = true;
                            }
                            Ok(())
                        };
                        let restored = back();
                        result.reinstalled = Some((typing, menu, options));
                        restored?;
                    }
                    if want_open != self.panel_installed || (force && want_open) {
                        if want_open {
                            client.open_key_panel()?;
                        } else {
                            client.close_key_panel()?;
                        }
                        self.panel_installed = want_open;
                    }
                    Ok(())
                })();
                if done.is_err() && want_open && !self.panel_installed {
                    result.reading = Some(Reading::Failed);
                }
            }
        }
        result
    }
}

/// The panel, supervisor side (the module docstring). Its compositor work goes out as jobs (`take_jobs`), its
/// toasts as texts (`take_toasts`).
#[derive(Debug)]
pub struct BindControl {
    pub keymap: KeyMap,
    /// Where the keymap is saved (None: not saved).
    path: Option<PathBuf>,
    /// A compositor client runs the jobs (false: none, as in tests of the state alone).
    client: bool,
    /// Toasts are wanted.
    notify: bool,
    /// The desktop's binds, or None (unknown).
    pub desktop: Option<DesktopBinds>,
    pub open: bool,
    pub capturing: bool,
    pub selected: usize,
    pub status: String,
    pub alert: bool,
    /// (op, note): the row a refused change pointed at, while the change still waits.
    refused: Option<(&'static str, String)>,
    capture_at: Option<f64>,
    armed_at: Option<f64>,
    pub last_input: f64,
    /// Bumps on every keymap change (and when a bind turns off or back on).
    pub version: u64,
    /// A read of the desktop's binds is on its way.
    pub reading: bool,
    stopped: Arc<AtomicBool>,
    pub conflicts: Conflicts,
    pub off: Off,
    jobs: Vec<Job>,
    inbox: VecDeque<Reading>,
    toasts: Vec<String>,
}

impl BindControl {
    pub fn new(
        keymap: KeyMap,
        path: Option<PathBuf>,
        client: bool,
        desktop: Option<DesktopBinds>,
        notify: bool,
        now: f64,
    ) -> BindControl {
        let (conflicts, off) = find_conflicts(&keymap, desktop.as_ref());
        BindControl {
            keymap,
            path,
            client,
            notify,
            desktop,
            open: false,
            capturing: false,
            selected: 0,
            status: String::new(),
            alert: false,
            refused: None,
            capture_at: None,
            armed_at: None,
            last_input: now,
            version: 0,
            reading: false,
            stopped: Arc::new(AtomicBool::new(false)),
            conflicts,
            off,
            jobs: Vec::new(),
            inbox: VecDeque::new(),
            toasts: Vec::new(),
        }
    }

    /// The worker for this panel's jobs (it shares the panel's stop).
    pub fn worker(&self) -> BindWorker {
        BindWorker {
            installed_version: 0,
            panel_installed: false,
            installed: None,
            stopped: self.stopped.clone(),
        }
    }

    pub fn take_jobs(&mut self) -> Vec<Job> {
        std::mem::take(&mut self.jobs)
    }

    pub fn take_toasts(&mut self) -> Vec<String> {
        std::mem::take(&mut self.toasts)
    }

    /// A job's result: the reinstall told to the host now, the reading kept for the next `poll`.
    pub fn deliver(&mut self, result: WorkResult, host: &mut dyn Host) {
        if let Some((typing, menu, options)) = result.reinstalled {
            host.binds_reinstalled(typing, menu, options);
        }
        self.inbox.extend(result.reading);
    }

    // --- binds to install -------------------------------------------------------------------------------------

    fn binds_for(&self, ops: &[&'static str]) -> Vec<Bind> {
        ops.iter()
            .filter(|op| !self.off.contains(*op))
            .map(|op| (self.keymap.chord(op).to_string(), *op))
            .collect()
    }

    /// The run binds with this keymap's keys, the ops that are off left out.
    pub fn run_binds(&self) -> Vec<Bind> {
        self.binds_for(&RUN_OPS)
    }

    pub fn send_binds(&self) -> Vec<Bind> {
        self.binds_for(&SEND_OPS)
    }

    /// The binds to install, or None with every default installed (the bind Lua is then exactly what it always was).
    pub fn bind_kwargs(&self) -> Option<(Vec<Bind>, Vec<Bind>)> {
        let defaults = |ops: &[&'static str]| -> Vec<Bind> {
            ops.iter()
                .map(|op| (default_chord(op).to_string(), *op))
                .collect()
        };
        let (run, send) = (self.run_binds(), self.send_binds());
        if run == defaults(&RUN_OPS) && send == defaults(&SEND_OPS) {
            return None;
        }
        Some((run, send))
    }

    /// The run binds were installed with this keymap (at the run's start).
    pub fn installed(&mut self) {
        self.jobs.push(Job::Installed {
            binds: (self.run_binds(), self.send_binds()),
            version: self.version,
            reset_panel: false,
        });
    }

    /// At the start: one toast naming the first conflict (and how many), or None.
    pub fn startup_toast(&mut self) -> Option<String> {
        let op = CATALOG
            .iter()
            .map(|(o, _, _)| *o)
            .find(|o| self.conflicts.contains_key(o))?;
        let more = self.conflicts.len() - 1;
        let toggle = if self.off.contains(PANEL_OP) {
            "the key bind panel".to_string()
        } else {
            chord_label(self.keymap.chord(PANEL_OP))
        };
        let more = if more > 0 {
            format!(" {more} more.")
        } else {
            String::new()
        };
        let text = format!(
            "Key bind conflict: {}: {}.{more} {toggle} shows them all.",
            label(op),
            self.conflicts[op]
        );
        self.tell(&text);
        Some(text)
    }

    fn recompute(&mut self) {
        let (conflicts, off) = find_conflicts(&self.keymap, self.desktop.as_ref());
        if off != self.off {
            self.version += 1; // a bind turned off or back on: reinstall
        }
        self.conflicts = conflicts;
        self.off = off;
    }

    // --- frames -----------------------------------------------------------------------------------------------

    /// The `binds` payload's JSON text.
    pub fn payload(&self) -> String {
        if !self.open {
            return "{\"visible\":false}".into();
        }
        let mut rows = Vec::new();
        for (i, (op, chord)) in self.keymap.items().into_iter().enumerate() {
            let mut note = self.conflicts.get(op).cloned().unwrap_or_default();
            let mut state = if !note.is_empty() {
                "conflict"
            } else if self.keymap.custom(op) {
                "custom"
            } else {
                "normal"
            };
            let mut keys = chord_label(chord);
            if self.capturing && i == self.selected {
                (state, keys) = ("capture", "Press keys...".into());
            }
            if let Some((refused, why)) = &self.refused
                && *refused == op
            {
                (state, note) = ("conflict", why.clone());
            }
            rows.push(format!(
                "{{\"action\":{},\"keys\":{},\"note\":{},\"state\":{}}}",
                json_str(&ascii(label(op), MAX_LABEL)),
                json_str(&ascii(&keys, MAX_LABEL)),
                json_str(&note),
                json_str(state)
            ));
        }
        let status = if self.status.is_empty() {
            self.summary()
        } else {
            self.status.clone()
        };
        let alert = self.alert || (self.status.is_empty() && !self.conflicts.is_empty());
        format!(
            "{{\"visible\":true,\"title\":\"Key binds\",\"rows\":[{}],\"selected\":{},\"hint\":{},\"status\":{},\"alert\":{alert}}}",
            rows.join(","),
            self.selected,
            json_str(if self.capturing { CAPTURE_HINT } else { HINT }),
            json_str(&ascii(&status, MAX_TEXT)),
        )
    }

    fn summary(&self) -> String {
        let n = self.conflicts.len();
        if n > 0 {
            let s = if n > 1 { "s" } else { "" };
            return format!("{n} bind{s} with conflicts (marked). Change them, or R to reset.");
        }
        if self.desktop.is_none() {
            return "Hyprland binds not read yet: conflicts with them unknown.".into();
        }
        "No conflicts with each other or with Hyprland / Omarchy binds.".into()
    }

    fn frame(&self, host: &mut dyn Host) -> Vec<u8> {
        let id = host.next_request_id();
        binds_frame(host.run_id(), host.session(), id, &self.payload())
    }

    // --- ops --------------------------------------------------------------------------------------------------

    /// A control message: the panel toggle or a key press. Frames (empty: nothing to draw).
    pub fn on_message(&mut self, message: &Value, host: &mut dyn Host) -> Vec<u8> {
        match message.get("op").and_then(Value::as_str) {
            Some(PANEL_OP) => self.toggle(host),
            Some(WIRE_OP) => {
                let payload = message.get("payload");
                let int = |name: &str| {
                    payload
                        .and_then(|p| p.get(name))
                        .filter(|v| !v.is_f64())
                        .and_then(Value::as_i64)
                };
                match (int("mods"), int("code")) {
                    (Some(mods), Some(code)) => self.key(mods, code, host),
                    _ => Vec::new(),
                }
            }
            _ => Vec::new(),
        }
    }

    pub fn toggle(&mut self, host: &mut dyn Host) -> Vec<u8> {
        if self.open {
            self.close(host)
        } else {
            self.open_panel(host)
        }
    }

    pub fn open_panel(&mut self, host: &mut dyn Host) -> Vec<u8> {
        if self.open || self.stopped.load(Ordering::SeqCst) || host.typing() || host.video_active()
        {
            return Vec::new();
        }
        let mut out = Vec::new();
        if host.help_visible() {
            out.extend(host.close_help()); // one centred card at a time
        }
        if host.menu_open() {
            out.extend(host.close_menu()); // its keys and the panel's would clash
        }
        (self.open, self.capturing, self.refused, self.armed_at) = (true, false, None, None);
        (self.status, self.alert) = (String::new(), false);
        self.last_input = host.now();
        self.read_desktop(host); // the newest Hyprland binds, for conflicts (results in poll)
        self.sync(false, host);
        out.extend(self.frame(host));
        out
    }

    pub fn close(&mut self, host: &mut dyn Host) -> Vec<u8> {
        if !self.open {
            return Vec::new();
        }
        (self.open, self.capturing) = (false, false);
        (self.refused, self.armed_at, self.capture_at) = (None, None, None);
        (self.status, self.alert) = (String::new(), false);
        self.sync(false, host);
        self.frame(host)
    }

    /// A key pressed while the panel is open (the compositor's subscription): browse or capture. Frames.
    pub fn key(&mut self, mods: i64, code: i64, host: &mut dyn Host) -> Vec<u8> {
        if !self.open {
            // The compositor still holds the keyboard for a closed panel (a failed close): close it again.
            self.sync(false, host);
            return Vec::new();
        }
        let now = host.now();
        self.last_input = now;
        host.set_last_input(now);
        let name = keycode_name(code);
        let plain = mods & !1 == 0; // nothing held but (maybe) Shift
        if self.capturing {
            if name == Some("Escape") && mods == 0 {
                return self.cancel("Change cancelled.", host);
            }
            return match key_chord(mods, code) {
                Some(chord) => self.capture(&chord, host),
                None if plain && name.is_some() => {
                    self.say("Hold Ctrl, Alt or Super too (Esc cancels).", false, host)
                }
                None => self.say(
                    "That key cannot be bound here. Try another (Esc cancels).",
                    false,
                    host,
                ),
            };
        }
        if name == Some("Escape") && mods == 0 {
            return self.close(host);
        }
        if let Some(chord) = key_chord(mods, code)
            && normalize(&chord).ok() == normalize(self.keymap.chord(PANEL_OP)).ok()
        {
            return self.close(host);
        }
        let last = CATALOG.len() as i64 - 1;
        let step = match name {
            Some("up") => Some(-1),
            Some("down") => Some(1),
            Some("Page_Up") => Some(-PAGE),
            Some("Page_Down") => Some(PAGE),
            Some("Home") => Some(-last),
            Some("End") => Some(last),
            _ => None,
        };
        if let (0, Some(step)) = (mods, step) {
            self.selected = (self.selected as i64 + step).clamp(0, last) as usize;
            (self.armed_at, self.status, self.alert) = (None, String::new(), false);
            return self.frame(host);
        }
        if mods == 0 && ENTER_CODES.contains(&code) {
            (self.capturing, self.capture_at, self.refused, self.armed_at) =
                (true, Some(now), None, None);
            let op = CATALOG[self.selected].0;
            self.status = format!(
                "New keys for {} (now {}).",
                label(op),
                chord_label(self.keymap.chord(op))
            );
            self.alert = false;
            return self.frame(host);
        }
        match (name, mods) {
            (Some("R"), 0) => self.reset(CATALOG[self.selected].0, host),
            (Some("R"), 1) => self.reset_all(host),
            _ => self.say(
                "Esc closes the panel; Up / Down and Enter change a bind.",
                false,
                host,
            ),
        }
    }

    fn say(&mut self, text: &str, alert: bool, host: &mut dyn Host) -> Vec<u8> {
        (self.status, self.alert) = (text.to_string(), alert);
        self.frame(host)
    }

    fn cancel(&mut self, text: &str, host: &mut dyn Host) -> Vec<u8> {
        (self.capturing, self.refused, self.capture_at) = (false, None, None);
        self.say(text, false, host)
    }

    fn capture(&mut self, chord: &str, host: &mut dyn Host) -> Vec<u8> {
        let op = CATALOG[self.selected].0;
        let key = normalize(chord).ok();
        if key == normalize(self.keymap.chord(op)).ok() && !self.off.contains(op) {
            return self.cancel(
                &format!("{} stays {}.", label(op), chord_label(chord)),
                host,
            );
        }
        let other = self
            .keymap
            .items()
            .into_iter()
            .find(|(o, c)| *o != op && !self.off.contains(o) && normalize(c).ok() == key)
            .map(|(o, _)| o);
        let held = self.desktop.as_ref().and_then(|d| d.find(chord));
        let text = if let Some(other) = other {
            let note = format!(
                "Has {}: change this one first to use it",
                chord_label(chord)
            );
            self.refused = Some((other, ascii(&note, MAX_TEXT)));
            format!(
                "{} is already {}. Press other keys, or Esc.",
                chord_label(chord),
                label(other)
            )
        } else if let Some((what, _)) = held {
            let note = format!("{} is taken: Hyprland \"{what}\"", chord_label(chord));
            self.refused = Some((op, ascii(&note, MAX_TEXT)));
            format!(
                "Hyprland binds {} to \"{what}\". Press other keys, or Esc.",
                chord_label(chord)
            )
        } else {
            self.keymap
                .set(op, chord)
                .expect("a chord from key_chord parses");
            (self.capturing, self.refused, self.capture_at) = (false, None, None);
            return self.changed(
                format!("{} is now {}.", label(op), chord_label(chord)),
                host,
            );
        };
        self.tell(&text);
        self.say(&ascii(&text, MAX_TEXT), true, host)
    }

    fn reset(&mut self, op: &'static str, host: &mut dyn Host) -> Vec<u8> {
        if !self.keymap.custom(op) {
            let text = format!(
                "{} already has its default keys ({}).",
                label(op),
                chord_label(default_chord(op))
            );
            return self.say(&text, false, host);
        }
        self.keymap.reset(Some(op));
        self.changed(
            format!(
                "{} is back to {}.",
                label(op),
                chord_label(default_chord(op))
            ),
            host,
        )
    }

    fn reset_all(&mut self, host: &mut dyn Host) -> Vec<u8> {
        let now = host.now();
        match self.armed_at {
            Some(at) if now - at < RESET_ARM_S => {}
            _ => {
                self.armed_at = Some(now);
                return self.say(
                    "Press Shift+R again to put every bind back to its default.",
                    false,
                    host,
                );
            }
        }
        self.armed_at = None;
        if self.keymap.overrides.is_empty() {
            return self.say("Every bind already has its default keys.", false, host);
        }
        self.keymap.reset(None);
        self.changed("Every bind is back to its default keys.".into(), host)
    }

    /// The keymap changed: saved, conflicts again, the run binds reinstalled (live), the panel redrawn.
    fn changed(&mut self, mut text: String, host: &mut dyn Host) -> Vec<u8> {
        self.version += 1;
        let mut alert = false;
        if let Some(path) = &self.path
            && let Err(e) = save(path, &self.keymap)
        {
            text = format!("{text} Not saved: {}.", strerror(&e));
            alert = true;
        }
        self.recompute();
        if !self.conflicts.is_empty() && !alert {
            self.tell(&format!(
                "Key binds: {} with conflicts. They are marked in the panel.",
                self.conflicts.len()
            ));
        }
        self.sync(false, host);
        self.say(&ascii(&text, MAX_TEXT), alert, host)
    }

    fn tell(&mut self, text: &str) {
        if self.notify {
            self.toasts.push(text.to_string());
        }
    }

    // --- the supervisor loop ----------------------------------------------------------------------------------

    /// Each loop tick: the readings that came back, then the capture timeout, the reset arm and the idle close.
    pub fn poll(&mut self, now: f64, host: &mut dyn Host) -> Vec<u8> {
        let mut out = Vec::new();
        while let Some(reading) = self.inbox.pop_front() {
            let desktop = match reading {
                Reading::Failed => {
                    if self.open {
                        out.extend(self.close(host));
                        self.tell("The key bind panel could not take the keyboard, so it closed. Your binds are unchanged.");
                    }
                    continue;
                }
                Reading::Desktop(desktop) => Some(desktop),
                Reading::ReadFailed => None,
            };
            self.reading = false;
            if desktop.is_some() {
                self.desktop = desktop;
            }
            let before = (self.conflicts.clone(), self.off.clone());
            self.recompute();
            if self.off != before.1 {
                self.sync(false, host);
            }
            if self.open && (self.conflicts != before.0 || self.off != before.1) {
                out.extend(self.frame(host));
            }
        }
        if !self.open {
            return out;
        }
        if self.capturing
            && self
                .capture_at
                .is_some_and(|at| now - at >= CAPTURE_TIMEOUT_S)
        {
            out.extend(self.cancel("No keys pressed: the bind is unchanged.", host));
        }
        if self.armed_at.is_some_and(|at| now - at >= RESET_ARM_S) {
            self.armed_at = None;
            out.extend(self.say("", false, host));
        }
        if now - self.last_input >= IDLE_CLOSE_S {
            out.extend(self.close(host));
        }
        out
    }

    // --- compositor work --------------------------------------------------------------------------------------

    fn read_desktop(&mut self, host: &dyn Host) {
        if !self.client || self.reading {
            return;
        }
        self.reading = true;
        self.jobs.push(Job::Read {
            typing: host.typing(),
        });
    }

    fn sync(&mut self, force: bool, host: &dyn Host) {
        if !self.client || self.stopped.load(Ordering::SeqCst) {
            return;
        }
        let custom = self.bind_kwargs().is_some();
        self.jobs.push(Job::Sync {
            version: self.version,
            typing: host.typing(),
            binds: (self.run_binds(), self.send_binds()),
            custom,
            want_open: self.open,
            force,
            menu_open: host.menu_open(),
            keyboard_owned: host.keyboard_owned(),
        });
    }

    /// A Hyprland config reload dropped every runtime bind and the Lua state (the run binds were put back with this
    /// keymap): the panel's subscription comes back if it is open.
    pub fn after_reload(&mut self, host: &dyn Host) {
        self.jobs.push(Job::Installed {
            binds: (self.run_binds(), self.send_binds()),
            version: self.version,
            reset_panel: true,
        });
        if self.client {
            self.jobs.push(Job::Sync {
                version: self.version,
                typing: host.typing(),
                binds: (self.run_binds(), self.send_binds()),
                custom: self.bind_kwargs().is_some(),
                want_open: self.open,
                force: true,
                menu_open: host.menu_open(),
                keyboard_owned: host.keyboard_owned(),
            });
        }
        self.read_desktop(host); // the reload may have changed the desktop's binds
    }

    /// Run end: no compositor change after this (removing the run binds closes an open panel).
    pub fn stop(&mut self) {
        self.stopped.store(true, Ordering::SeqCst);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::supervisor::corpus::{self, s};
    use crate::testdir::TestDir;
    use serde_json::json;
    use std::cell::RefCell;
    use std::os::unix::fs::PermissionsExt;
    use std::rc::Rc;

    fn overrides(v: &Value) -> KeyMap {
        KeyMap::new(
            v.as_object()
                .unwrap()
                .iter()
                .filter_map(|(k, v)| Some((k.as_str(), v.as_str()?))),
        )
    }

    fn overrides_json(k: &KeyMap) -> Value {
        json!(k.overrides)
    }

    fn normal_json((mods, key): &Normal) -> Value {
        json!([mods, key])
    }

    #[test]
    fn tables_match_python() {
        let c = corpus::load("keybinds.json");
        let t = &c["tables"];
        let catalog: Vec<Value> = CATALOG.iter().map(|(o, d, l)| json!([o, d, l])).collect();
        assert_eq!(t["catalog"], Value::from(catalog));
        assert_eq!(t["run_ops"], json!(RUN_OPS));
        assert_eq!(t["send_ops"], json!(SEND_OPS));
        let codes: serde_json::Map<String, Value> = KEYCODES
            .iter()
            .map(|(c, n)| (c.to_string(), json!(n)))
            .collect();
        assert_eq!(t["keycodes"], Value::Object(codes));
        let names: serde_json::Map<String, Value> = KEY_NAMES
            .iter()
            .map(|(k, n)| (k.to_string(), json!(n)))
            .collect();
        assert_eq!(t["key_names"], Value::Object(names));
        assert_eq!(t["panel_op"], PANEL_OP);
        assert_eq!(t["wire_op"], WIRE_OP);
        assert_eq!(t["typing_op"], TYPING_OP);
        assert_eq!(t["hint"], HINT);
        assert_eq!(t["capture_hint"], CAPTURE_HINT);
        assert_eq!(t["idle_close_s"].as_f64(), Some(IDLE_CLOSE_S));
        assert_eq!(t["capture_timeout_s"].as_f64(), Some(CAPTURE_TIMEOUT_S));
        assert_eq!(t["reset_arm_s"].as_f64(), Some(RESET_ARM_S));
        assert_eq!(t["page"], PAGE);
        // Every run and send op is in the catalog, once.
        let all: BTreeSet<&str> = RUN_OPS.iter().chain(&SEND_OPS).copied().collect();
        assert_eq!(all.len(), CATALOG.len());
        assert!(all.iter().all(|op| catalog_op(op).is_some()));
    }

    #[test]
    fn chords_labels_and_ascii_match_python() {
        let c = corpus::load("keybinds.json");
        for case in c["parse_chord"].as_array().unwrap() {
            let got = match parse_chord(s(&case["text"])) {
                Ok(v) => json!({"text": case["text"], "value": v}),
                Err(e) => json!({"text": case["text"], "error": e}),
            };
            assert_eq!(&got, case);
        }
        for case in c["key_chord"].as_array().unwrap() {
            let (m, code) = (case[0].as_i64().unwrap(), case[1].as_i64().unwrap());
            assert_eq!(json!(key_chord(m, code)), case[2], "{case}");
        }
        for case in c["chord_label"].as_array().unwrap() {
            assert_eq!(chord_label(s(&case[0])), s(&case[1]));
        }
        for case in c["ascii"].as_array().unwrap() {
            assert_eq!(
                ascii(s(&case[0]), case[1].as_u64().unwrap() as usize),
                s(&case[2]),
                "{case}"
            );
        }
    }

    #[test]
    fn keymaps_match_python() {
        let c = corpus::load("keybinds.json");
        for case in c["keymap"].as_array().unwrap() {
            let k = overrides(&case["overrides"]);
            assert_eq!(overrides_json(&k), case["effective"]);
            let items: Vec<Value> = k.items().iter().map(|(o, c)| json!([o, c])).collect();
            assert_eq!(Value::from(items), case["items"]);
        }
        let mut k = KeyMap::default();
        assert_eq!(k.set("nope", "CTRL + ALT + J"), Err("unknown bind".into()));
        assert_eq!(k.set("next", "J"), Err("invalid keys".into()));
        k.set("next", "ctrl+alt+j").unwrap();
        assert!(k.custom("next"));
        k.set("next", "CTRL + ALT + right").unwrap();
        assert!(!k.custom("next"));
    }

    #[test]
    fn load_and_save_match_python() {
        let c = corpus::load("keybinds.json");
        let dir = TestDir::new("keybinds-load");
        for (i, case) in c["load"].as_array().unwrap().iter().enumerate() {
            let path = dir.path().join(format!("load{i}.json"));
            if case["directory"] == true {
                std::fs::create_dir(&path).unwrap();
            } else if let Some(text) = case["file"].as_str() {
                let bytes: Vec<u8> = if case["bytes"] == true {
                    text.chars()
                        .map(|ch| u8::try_from(ch as u32).unwrap())
                        .collect()
                } else {
                    text.as_bytes().to_vec()
                };
                std::fs::write(&path, bytes).unwrap();
            }
            assert_eq!(
                overrides_json(&load(&path)),
                case["overrides"],
                "{}",
                case["file"]
            );
        }
        for (i, case) in c["save"].as_array().unwrap().iter().enumerate() {
            let path = dir.path().join(format!("save{i}/deeper/keybinds.json"));
            save(&path, &overrides(&case["overrides"])).unwrap();
            assert_eq!(std::fs::read_to_string(&path).unwrap(), s(&case["text"]));
            let mode = std::fs::metadata(&path).unwrap().permissions().mode() & 0o7777;
            assert_eq!(json!(mode), case["mode"]);
            assert_eq!(
                path.with_file_name("keybinds.json.tmp").exists(),
                case["leftover_tmp"] == true
            );
            assert_eq!(load(&path), overrides(&case["overrides"]));
        }
    }

    #[test]
    fn save_refuses_a_link_at_the_temporary_file() {
        let dir = TestDir::new("keybinds-nofollow");
        let path = dir.path().join("keybinds.json");
        let target = dir.path().join("elsewhere");
        std::os::unix::fs::symlink(&target, dir.path().join("keybinds.json.tmp")).unwrap();
        let err = save(&path, &KeyMap::default()).unwrap_err();
        assert_eq!(err.raw_os_error(), Some(libc::ELOOP));
        assert!(!target.exists() && !path.exists());
        assert_eq!(strerror(&err), "Too many levels of symbolic links");
    }

    fn entry_json(e: &Entry) -> Value {
        json!({"modmask": e.modmask, "submap": e.submap, "key": e.key, "catchall": e.catchall,
               "description": e.description, "dispatcher": e.dispatcher})
    }

    fn desktop_from(text: &Value) -> Option<DesktopBinds> {
        text.as_str().map(|t| DesktopBinds::from_text(t, &[]))
    }

    #[test]
    fn desktop_binds_match_python() {
        let c = corpus::load("keybinds.json");
        for case in c["binds"].as_array().unwrap() {
            let text = s(&case["text"]);
            let ours: Vec<String> = case["ours"]
                .as_array()
                .unwrap()
                .iter()
                .map(|v| s(v).to_string())
                .collect();
            let entries: Vec<Value> = parse_binds(text).iter().map(entry_json).collect();
            assert_eq!(Value::from(entries), case["entries"], "{text}");
            let d = DesktopBinds::from_text(text, &ours);
            let chords: Vec<Value> = d
                .chords
                .iter()
                .map(|(n, found)| {
                    let n = normal_json(n);
                    json!([
                        n[0],
                        n[1],
                        found.iter().map(|(d, h)| json!([d, h])).collect::<Vec<_>>()
                    ])
                })
                .collect();
            assert_eq!(Value::from(chords), case["chords"], "{text} {ours:?}");
            for find in case["find"].as_array().unwrap() {
                let got = d.find(s(&find[0])).map(|(d, h)| json!([d, h]));
                assert_eq!(json!(got), find[1], "{text} {}", find[0]);
            }
        }
    }

    #[test]
    fn conflicts_match_python() {
        let c = corpus::load("keybinds.json");
        for case in c["conflicts"].as_array().unwrap() {
            let (notes, off) = find_conflicts(
                &overrides(&case["overrides"]),
                desktop_from(&case["desktop"]).as_ref(),
            );
            assert_eq!(
                json!(notes),
                case["notes"],
                "{} {}",
                case["overrides"],
                case["desktop"]
            );
            assert_eq!(
                json!(off),
                case["off"],
                "{} {}",
                case["overrides"],
                case["desktop"]
            );
        }
        for case in c["frame"].as_array().unwrap() {
            let frame = binds_frame(
                s(&case["run_id"]),
                s(&case["session"]),
                case["request_id"].as_u64().unwrap(),
                &case["payload"].to_string(),
            );
            assert_eq!(String::from_utf8(frame).unwrap(), s(&case["frame"]));
        }
    }

    // --- scripted panel sessions (the corpus's traces) -----------------------------------------------------------

    type Log = Rc<RefCell<Vec<Value>>>;

    /// The ViewControl stand-in the Python traces use (keybinds_corpus._Host).
    struct FakeHost {
        typing: bool,
        menu_open: bool,
        help_visible: bool,
        video_active: bool,
        keyboard_owned: bool,
        now: f64,
        next_id: u64,
        last_input: Option<f64>,
        log: Log,
    }

    impl Host for FakeHost {
        fn now(&self) -> f64 {
            self.now
        }
        fn typing(&self) -> bool {
            self.typing
        }
        fn video_active(&self) -> bool {
            self.video_active
        }
        fn help_visible(&self) -> bool {
            self.help_visible
        }
        fn close_help(&mut self) -> Vec<u8> {
            self.help_visible = false;
            self.log.borrow_mut().push(json!(["help_closed"]));
            b"<help closed>\n".to_vec()
        }
        fn menu_open(&self) -> bool {
            self.menu_open
        }
        fn close_menu(&mut self) -> Vec<u8> {
            self.menu_open = false;
            self.log.borrow_mut().push(json!(["menu_closed"]));
            b"<menu closed>\n".to_vec()
        }
        fn keyboard_owned(&self) -> bool {
            self.keyboard_owned
        }
        fn run_id(&self) -> &str {
            "run"
        }
        fn session(&self) -> &str {
            "session"
        }
        fn next_request_id(&mut self) -> u64 {
            self.next_id += 1;
            self.next_id - 1
        }
        fn set_last_input(&mut self, now: f64) {
            self.last_input = Some(now);
        }
        fn binds_reinstalled(&mut self, _typing: bool, _menu: bool, _options: bool) {}
    }

    /// keybinds_corpus._Client: records each call, fails the ones named in `fail`.
    struct FakeClient {
        text: String,
        fail: BTreeSet<String>,
        calls: Vec<Value>,
        host_log: Log,
    }

    impl FakeClient {
        fn maybe(&self, what: &str) -> Result<(), String> {
            if self.fail.contains(what) {
                Err(format!("{what} failed"))
            } else {
                Ok(())
            }
        }
    }

    fn binds_json(binds: &[Bind]) -> Value {
        Value::from(binds.iter().map(|(c, o)| json!([c, o])).collect::<Vec<_>>())
    }

    impl KeyClient for FakeClient {
        fn reinstall_view_binds(
            &mut self,
            typing: bool,
            binds: Option<(&[Bind], &[Bind])>,
        ) -> Result<(), String> {
            let (run, send) = match binds {
                Some((r, s)) => (binds_json(r), binds_json(s)),
                None => (Value::Null, Value::Null),
            };
            self.calls.push(json!(["reinstall", typing, run, send]));
            self.maybe("reinstall")
        }
        fn open_key_panel(&mut self) -> Result<(), String> {
            self.calls.push(json!(["open"]));
            self.maybe("open")
        }
        fn close_key_panel(&mut self) -> Result<(), String> {
            self.calls.push(json!(["close"]));
            self.maybe("close")
        }
        fn binds_text(&mut self) -> Result<String, String> {
            self.calls.push(json!(["read"]));
            self.maybe("read")?;
            Ok(self.text.clone())
        }
        fn menu_binds(&mut self, on: bool) -> Result<(), String> {
            self.host_log.borrow_mut().push(json!(["menu_binds", on]));
            Ok(())
        }
        fn options_binds(&mut self, on: bool) -> Result<(), String> {
            self.host_log
                .borrow_mut()
                .push(json!(["options_binds", on]));
            Ok(())
        }
    }

    fn drain(
        k: &mut BindControl,
        worker: &mut BindWorker,
        client: &mut Option<FakeClient>,
        host: &mut FakeHost,
    ) {
        loop {
            let jobs = k.take_jobs();
            if jobs.is_empty() {
                return;
            }
            for job in jobs {
                let result = worker.run(job, client.as_mut().map(|c| c as &mut dyn KeyClient));
                k.deliver(result, host);
            }
        }
    }

    fn snapshot(k: &BindControl, worker: &BindWorker, host: &FakeHost) -> Value {
        json!({
            "open": k.open, "capturing": k.capturing, "selected": k.selected, "status": k.status, "alert": k.alert,
            "overrides": k.keymap.overrides, "off": k.off, "conflicts": k.conflicts, "version": k.version,
            "installed_version": worker.installed_version, "panel_installed": worker.panel_installed,
            "desktop_known": k.desktop.is_some(), "reading": k.reading, "last_input": k.last_input,
            "host_last_input": host.last_input,
            "bind_kwargs": k.bind_kwargs().map(|(r, s)| json!([binds_json(&r), binds_json(&s)])),
            "installed_chords": worker.installed_chords(host.typing),
        })
    }

    fn bytes_out(out: Vec<u8>) -> Value {
        if out.is_empty() {
            Value::Null
        } else {
            json!(String::from_utf8(out).unwrap())
        }
    }

    #[test]
    fn traces_match_python() {
        let c = corpus::load("keybinds.json");
        for trace in c["traces"].as_array().unwrap() {
            let name = s(&trace["name"]);
            let dir = TestDir::new("keybinds-trace");
            let file = match trace["path"].as_str() {
                Some("file") => Some(dir.path().join("cfg/keybinds.json")),
                Some("blocked") => {
                    std::fs::write(dir.path().join("blocked"), "").unwrap();
                    Some(dir.path().join("blocked/keybinds.json"))
                }
                _ => None,
            };
            let log: Log = Rc::default();
            let mut host = FakeHost {
                typing: false,
                menu_open: false,
                help_visible: false,
                video_active: false,
                keyboard_owned: false,
                now: 1000.0,
                next_id: 1,
                last_input: None,
                log: log.clone(),
            };
            let mut client = trace["client"].as_object().map(|cl| FakeClient {
                text: cl["text"].as_str().unwrap().to_string(),
                fail: cl
                    .get("fail")
                    .and_then(Value::as_array)
                    .into_iter()
                    .flatten()
                    .map(|v| s(v).to_string())
                    .collect(),
                calls: Vec::new(),
                host_log: log.clone(),
            });
            let mut k = BindControl::new(
                overrides(&trace["overrides"]),
                file.clone(),
                client.is_some(),
                desktop_from(&trace["desktop"]),
                trace["notify"] == true,
                host.now,
            );
            let mut worker = k.worker();
            k.installed();
            drain(&mut k, &mut worker, &mut client, &mut host);
            let mut saved: Option<String> = None;
            for (i, want) in trace["steps"].as_array().unwrap().iter().enumerate() {
                let step = want["step"].as_array().unwrap();
                let calls0 = client.as_ref().map_or(0, |c| c.calls.len());
                let log0 = log.borrow().len();
                let out = match s(&step[0]) {
                    "msg" => bytes_out(k.on_message(&step[1], &mut host)),
                    "key" => {
                        let msg =
                            json!({"op": WIRE_OP, "payload": {"mods": step[1], "code": step[2]}});
                        bytes_out(k.on_message(&msg, &mut host))
                    }
                    "toggle" => {
                        bytes_out(k.on_message(&json!({"op": PANEL_OP, "payload": {}}), &mut host))
                    }
                    "poll" => json!(String::from_utf8(k.poll(host.now, &mut host)).unwrap()),
                    "advance" => {
                        host.now += step[1].as_f64().unwrap();
                        Value::Null
                    }
                    "host" => {
                        let on = step[2].as_bool().unwrap();
                        match s(&step[1]) {
                            "typing" => host.typing = on,
                            "menu_open" => host.menu_open = on,
                            "help_visible" => host.help_visible = on,
                            "video_active" => host.video_active = on,
                            other => panic!("host field {other}"),
                        }
                        Value::Null
                    }
                    "client" => {
                        let cl = client.as_mut().unwrap();
                        match s(&step[1]) {
                            "text" => cl.text = s(&step[2]).to_string(),
                            "fail" => {
                                cl.fail = step[2]
                                    .as_array()
                                    .unwrap()
                                    .iter()
                                    .map(|v| s(v).to_string())
                                    .collect()
                            }
                            other => panic!("client field {other}"),
                        }
                        Value::Null
                    }
                    "startup_toast" => json!(k.startup_toast()),
                    "after_reload" => {
                        k.after_reload(&host);
                        Value::Null
                    }
                    "installed" => {
                        k.installed();
                        Value::Null
                    }
                    "stop" => {
                        k.stop();
                        Value::Null
                    }
                    other => panic!("step {other}"),
                };
                drain(&mut k, &mut worker, &mut client, &mut host);
                let now_saved = file.as_ref().and_then(|f| std::fs::read_to_string(f).ok());
                let calls = client
                    .as_ref()
                    .map_or_else(Vec::new, |c| c.calls[calls0..].to_vec());
                let got = json!({
                    "step": want["step"], "out": out, "calls": calls, "toasts": k.take_toasts(),
                    "host": log.borrow()[log0..].to_vec(),
                    "saved": if now_saved != saved { json!(now_saved) } else { Value::Null },
                    "state": snapshot(&k, &worker, &host),
                });
                saved = now_saved;
                for field in ["out", "calls", "toasts", "host", "saved", "state"] {
                    assert_eq!(
                        got[field], want[field],
                        "trace {name}, step {i} {:?}: {field}",
                        want["step"]
                    );
                }
            }
        }
    }
}
