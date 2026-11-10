//! Text typed into the options menu's editor through the compositor's own keyboard layout: the port of
//! `tools/native_spike/options_text.py` (`TextInput`), on libxkbcommon.
//!
//! No keyboard subscription or ownership here. The supervisor forwards xkb keycodes (evdev + 8) and the compositor's
//! modifier masks (`modifiers`, or Hyprland's modmask through `hypr_modifiers`); the editor drops the `TextInput`
//! when it loses focus. `RuntimeText` (which reads the keyboard's names from the compositor) waits for the loop's
//! port.

use std::ffi::OsStr;

use xkbcommon::xkb::{self, compose};

/// The largest keyboard name (rules, model, layout, variant, options) taken, in bytes.
pub const MAX_NAME: usize = 256;
/// The largest keymap text taken, in bytes.
pub const MAX_KEYMAP: usize = 1024 * 1024;
const KEY_ESCAPE: u32 = 0xff1b;
const KEY_RETURN: u32 = 0xff0d;
const KEY_KP_ENTER: u32 = 0xff8d;
const KEY_BACKSPACE: u32 = 0xff08;
/// Hyprland's modmask bits and the xkb modifier each one is.
const HYPR_MODS: [(i64, &str); 8] = [
    (1, "Shift"),
    (2, "Lock"),
    (4, "Control"),
    (8, "Mod1"),
    (16, "Mod2"),
    (32, "Mod3"),
    (64, "Mod4"),
    (128, "Mod5"),
];

/// The compositor keyboard's xkb names (`hyprctl devices`).
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Names {
    pub rules: String,
    pub model: String,
    pub layout: String,
    pub variant: String,
    pub options: String,
}

/// What a key press does in the editor.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Action {
    Cancel,
    Submit,
    Backspace,
    /// Text to insert (empty: nothing, as for a dead key or a key with no character).
    Text(String),
}

impl Action {
    /// The Python's (action, text) pair.
    pub fn pair(&self) -> (&'static str, &str) {
        match self {
            Action::Cancel => ("cancel", ""),
            Action::Submit => ("submit", ""),
            Action::Backspace => ("backspace", ""),
            Action::Text(text) => ("text", text),
        }
    }
}

/// C strings stop at the first NUL, as the Python's ctypes arguments do.
fn c_text(s: &str) -> &str {
    s.split('\0').next().unwrap_or_default()
}

/// Python's `str.isprintable` for one character: not a control, format, surrogate, private-use or separator
/// character, except the plain space.
fn printable(c: char) -> bool {
    if c == ' ' {
        return true;
    }
    let u = c as u32;
    !(c.is_control()
        || c.is_whitespace()
        || matches!(u, 0xad | 0x600..=0x605 | 0x61c | 0x6dd | 0x70f | 0x180e | 0x200b..=0x200f | 0x202a..=0x202e
            | 0x2060..=0x2064 | 0x2066..=0x206f | 0xfeff | 0xfff9..=0xfffb | 0x110bd | 0x1d173..=0x1d17a
            | 0xe0001 | 0xe0020..=0xe007f)
        || (0xe000..=0xf8ff).contains(&u)
        || u >= 0xf0000)
}

/// Key presses to text through one compiled keymap, with the C.UTF-8 compose table (dead keys, the Compose key).
pub struct TextInput {
    keymap: xkb::Keymap,
    state: Option<xkb::State>,
    compose: Option<compose::State>,
}

impl TextInput {
    /// Compiles the compositor keyboard's reported names (never assumes US). Empty names take libxkbcommon's
    /// defaults, as NULL ones do.
    pub fn from_names(names: &Names) -> Result<TextInput, String> {
        if names.layout.is_empty() {
            return Err("Compositor keyboard layout unavailable".into());
        }
        let fields = [
            &names.rules,
            &names.model,
            &names.layout,
            &names.variant,
            &names.options,
        ];
        if fields.iter().any(|f| f.len() > MAX_NAME) {
            return Err("Invalid compositor keyboard names".into());
        }
        let context = xkb::Context::new(xkb::CONTEXT_NO_FLAGS);
        let options = Some(c_text(&names.options).to_string()).filter(|o| !o.is_empty());
        let keymap = xkb::Keymap::new_from_names(
            &context,
            c_text(&names.rules),
            c_text(&names.model),
            c_text(&names.layout),
            c_text(&names.variant),
            options,
            xkb::KEYMAP_COMPILE_NO_FLAGS,
        )
        .ok_or("Cannot compile compositor keyboard layout")?;
        TextInput::new(&keymap.get_as_string(xkb::KEYMAP_FORMAT_TEXT_V1))
    }

    /// Compiles a keymap's text (as the compositor sends it).
    pub fn new(keymap: &str) -> Result<TextInput, String> {
        if keymap.len() > MAX_KEYMAP {
            return Err("invalid keymap".into());
        }
        let context = xkb::Context::new(xkb::CONTEXT_NO_FLAGS);
        let keymap = xkb::Keymap::new_from_string(
            &context,
            c_text(keymap).to_string(),
            xkb::KEYMAP_FORMAT_TEXT_V1,
            xkb::KEYMAP_COMPILE_NO_FLAGS,
        )
        .ok_or("cannot compile compositor keymap")?;
        let state = xkb::State::new(&keymap);
        let compose = compose::Table::new_from_locale(
            &context,
            OsStr::new("C.UTF-8"),
            compose::COMPILE_NO_FLAGS,
        )
        .ok()
        .map(|table| compose::State::new(&table, compose::STATE_NO_FLAGS));
        Ok(TextInput {
            keymap,
            state: Some(state),
            compose,
        })
    }

    /// Hyprland's modmask (`HYPR_MODS`) as the xkb modifiers held down.
    pub fn hypr_modifiers(&mut self, mods: i64, group: i64) -> Result<(), String> {
        let mut mask: i64 = 0;
        for (bit, name) in HYPR_MODS {
            let index = self.keymap.mod_get_index(name);
            if mods & bit != 0 && index < 32 {
                mask |= 1 << index;
            }
        }
        self.modifiers(mask, 0, 0, group)
    }

    /// The xkb state's depressed, latched and locked modifier masks and its effective group, each 0..=0xffffffff.
    pub fn modifiers(
        &mut self,
        depressed: i64,
        latched: i64,
        locked: i64,
        group: i64,
    ) -> Result<(), String> {
        let mask = |v: i64| u32::try_from(v).map_err(|_| "invalid modifiers".to_string());
        let (depressed, latched, locked, group) = (
            mask(depressed)?,
            mask(latched)?,
            mask(locked)?,
            mask(group)?,
        );
        if let Some(state) = &mut self.state {
            state.update_mask(depressed, latched, locked, 0, 0, group);
        }
        Ok(())
    }

    /// One key pressed (xkb keycode 8..=65535): cancel, submit, backspace or text.
    pub fn key(&mut self, code: i64) -> Result<Action, String> {
        let (Some(state), Ok(code @ 8..=65535)) = (&self.state, u32::try_from(code)) else {
            return Err("invalid key".into());
        };
        let code = xkb::Keycode::new(code);
        let sym = state.key_get_one_sym(code);
        match sym.raw() {
            KEY_ESCAPE => return Ok(Action::Cancel),
            KEY_RETURN | KEY_KP_ENTER => return Ok(Action::Submit),
            KEY_BACKSPACE => return Ok(Action::Backspace),
            _ => {}
        }
        if let Some(compose) = &mut self.compose {
            compose.feed(sym);
            match compose.status() {
                compose::Status::Composing => return Ok(Action::Text(String::new())),
                compose::Status::Composed => {
                    let text = compose.utf8().unwrap_or_default();
                    compose.reset();
                    return Ok(Action::Text(text));
                }
                compose::Status::Cancelled => {
                    compose.reset();
                    return Ok(Action::Text(String::new()));
                }
                compose::Status::Nothing => {}
            }
        }
        let text = state.key_get_utf8(code);
        let text = text.split('\0').next().unwrap_or_default();
        Ok(Action::Text(
            text.chars().filter(|c| printable(*c)).collect(),
        ))
    }

    /// Lets the keymap go: every later key is refused.
    pub fn close(&mut self) {
        (self.state, self.compose) = (None, None);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::supervisor::corpus::{self, s};
    use crate::supervisor::json_int;
    use serde_json::{Value, json};

    /// The Python's argument checks for a names dict: (names, or the error from_names gives first).
    fn names_from(v: &Value) -> Result<Names, String> {
        let Some(map) = v
            .as_object()
            .filter(|m| m.get("layout").is_some_and(|l| l != "" && !l.is_null()))
        else {
            return Err("Compositor keyboard layout unavailable".into());
        };
        let field = |k: &str| match map.get(k) {
            None => Ok(String::new()),
            Some(Value::String(s)) => Ok(s.clone()),
            Some(_) => Err("Invalid compositor keyboard names".to_string()),
        };
        Ok(Names {
            rules: field("rules")?,
            model: field("model")?,
            layout: field("layout")?,
            variant: field("variant")?,
            options: field("options")?,
        })
    }

    fn pair(a: Result<Action, String>) -> Value {
        match a {
            Ok(a) => json!({"value": [a.pair().0, a.pair().1]}),
            Err(e) => json!({"error": e}),
        }
    }

    #[test]
    fn scripted_layouts_match_python() {
        let c = corpus::load("options_text.json");
        let script = c["script"].as_array().unwrap();
        for layout in c["layouts"].as_array().unwrap() {
            let mut t = TextInput::from_names(&names_from(&layout["names"]).unwrap()).unwrap();
            for (step, want) in script.iter().zip(layout["results"].as_array().unwrap()) {
                let got = match s(&step[0]) {
                    "hypr" => {
                        t.hypr_modifiers(step[1].as_i64().unwrap(), 0).unwrap();
                        Value::Null
                    }
                    "mods" => {
                        let m: Vec<i64> = step[1]
                            .as_array()
                            .unwrap()
                            .iter()
                            .map(|v| v.as_i64().unwrap())
                            .collect();
                        t.modifiers(m[0], m[1], m[2], m[3]).unwrap();
                        Value::Null
                    }
                    _ => pair(t.key(step[1].as_i64().unwrap()))["value"].clone(),
                };
                assert_eq!(&got, want, "{} {step}", layout["names"]);
            }
        }
    }

    #[test]
    fn refusals_match_python() {
        let c = corpus::load("options_text.json");
        for case in c["refused_names"].as_array().unwrap() {
            let got =
                names_from(&case["names"]).and_then(|n| TextInput::from_names(&n).map(|_| ()));
            assert_eq!(json!(got.err()), case["error"], "{}", case["names"]);
        }
        let mut t = TextInput::from_names(&names_from(&c["layouts"][0]["names"]).unwrap()).unwrap();
        for case in c["refused_input"].as_array().unwrap() {
            let got = if case[0] == "key" {
                match json_int(&case[1]) {
                    Some(code) => pair(t.key(code)),
                    None => json!({"error": "invalid key"}),
                }
            } else {
                let m: Option<Vec<i64>> =
                    case[1].as_array().unwrap().iter().map(json_int).collect();
                match m.map(|m| t.modifiers(m[0], m[1], m[2], m[3])) {
                    Some(Ok(())) => json!({"value": null}),
                    Some(Err(e)) => json!({"error": e}),
                    None => json!({"error": "invalid modifiers"}),
                }
            };
            assert_eq!(got, case[2], "{case}");
        }
        t.close();
        assert_eq!(t.key(38), Err("invalid key".into()));
        assert_eq!(
            json!(TextInput::new("xkb_keymap { nonsense }").err()),
            c["bad_keymap"]
        );
        assert_eq!(
            json!(TextInput::new(&"x".repeat(MAX_KEYMAP + 1)).err()),
            c["big_keymap"]
        );
    }

    #[test]
    fn printable_matches_python() {
        for c in ['a', ' ', 'é', '€', '´'] {
            assert!(printable(c), "{c:?}");
        }
        for c in [
            '\t', '\n', '\r', '\u{7f}', '\u{85}', '\u{a0}', '\u{200b}', '\u{2028}', '\u{feff}',
            '\u{e000}',
        ] {
            assert!(!printable(c), "{c:?}");
        }
    }
}
