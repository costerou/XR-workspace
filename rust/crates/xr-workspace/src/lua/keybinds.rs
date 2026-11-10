//! keybinds.py: the key bind panel's keyboard subscription, and the chords it writes.

use super::{BindCommand, Refused, Result};

pub const SUBMAP: &str = "xr_workspace_keys";
/// The subscription's key presses: payload {"mods": modmask, "code": xkb keycode}.
pub const WIRE_OP: &str = "binds_key";
/// The compositor closes the panel after this long without a key, whatever the run does.
pub const WATCHDOG_S: u32 = 90;
/// How a chord is written (the defaults: `CTRL + ALT + K`).
pub const MOD_ORDER: [&str; 4] = ["CTRL", "ALT", "SHIFT", "SUPER"];
pub const MOD_NAMES: [(&str, &str); 10] = [
    ("CTRL", "CTRL"),
    ("CONTROL", "CTRL"),
    ("ALT", "ALT"),
    ("MOD1", "ALT"),
    ("SHIFT", "SHIFT"),
    ("SUPER", "SUPER"),
    ("WIN", "SUPER"),
    ("LOGO", "SUPER"),
    ("MOD4", "SUPER"),
    ("META", "SUPER"),
];
/// xkb keycode to the key's name in a Hyprland chord.
pub const KEYCODES: [(u32, &str); 74] = [
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
/// Modifier keycodes and their modmask bit.
pub const MODIFIER_KEYS: [(u32, u32); 8] = [
    (37, 4),
    (105, 4),
    (64, 8),
    (108, 8),
    (50, 1),
    (62, 1),
    (133, 64),
    (134, 64),
];
/// Keys the subscription never reports: the modifiers, Caps Lock, Num Lock and the 102nd key's ISO level 3.
pub const SILENT_KEYS: [u32; 11] = [37, 105, 64, 108, 50, 62, 133, 134, 66, 77, 92];

pub const PANEL_OFF_LUA: &str = concat!(
    r#"if xr_workspace_keys_off then xr_workspace_keys_off() end if hl.get_current_submap() == "#,
    r#""xr_workspace_keys" then hl.dispatch(hl.dsp.submap("reset")) end"#,
);

/// The panel on: its submap entered (with its never-removed catch-all) and every key press reported to the run.
/// Replaces any subscription left from before.
pub fn panel_on_lua(cmd: &BindCommand) -> Result<String> {
    let base = cmd.base()?;
    let held = MODIFIER_KEYS
        .iter()
        .map(|(code, bit)| format!("{{ {code}, {bit} }}"))
        .collect::<Vec<_>>()
        .join(", ");
    let silent = SILENT_KEYS
        .iter()
        .map(|code| format!("[{code}] = true"))
        .collect::<Vec<_>>()
        .join(", ");
    Ok(format!(
        "local c = \"{base} {WIRE_OP} \" \
         if xr_workspace_keys_off then xr_workspace_keys_off() end \
         local s = xr_workspace_keys or {{}} xr_workspace_keys = s \
         if not s.catchall or tostring(s.catchall) == \"HL.Keybind(expired)\" then \
         hl.define_submap(\"{SUBMAP}\", function() s.catchall = hl.bind(\"catchall\", hl.dsp.no_op(), {{ ignore_mods = true }}) end) end \
         if not s.catchall or tostring(s.catchall) == \"HL.Keybind(expired)\" then error(\"no catch-all\") end \
         local held, silent = {{ {held} }}, {{ {silent} }} \
         s.seen, s.esc = os.time(), 0 \
         xr_workspace_keys_off = function() xr_workspace_keys_off = nil \
         if s.sub then pcall(function() s.sub:remove() end) end s.sub = nil \
         if s.timer then pcall(function() s.timer:set_enabled(false) end) end \
         if hl.get_current_submap() == \"{SUBMAP}\" then hl.dispatch(hl.dsp.submap(\"reset\")) end end \
         local ok, sub = pcall(hl.on, \"input.keyboard.key\", function(code, _, state) \
         if state ~= 1 or silent[code] then return end \
         s.seen = os.time() \
         local m, bits = 0, {{}} \
         for _, k in ipairs(held) do if not bits[k[2]] and hl.is_key_down(k[1]) then bits[k[2]] = true m = m + k[2] end end \
         if code == 9 and m == 0 then s.esc = s.esc + 1 else s.esc = 0 end \
         if s.esc >= 3 and xr_workspace_keys_off then xr_workspace_keys_off() return end \
         hl.exec_cmd(c .. m .. \" \" .. code) end) \
         if not ok or not sub then xr_workspace_keys_off() error(\"no key subscription\") end s.sub = sub \
         if not s.timer then pcall(function() s.timer = hl.timer(function() \
         if os.time() - s.seen > {WATCHDOG_S} and xr_workspace_keys_off then xr_workspace_keys_off() end end, \
         {{ timeout = 5000, type = \"repeat\" }}) end) end \
         if s.timer then pcall(function() s.timer:set_enabled(true) end) end \
         pcall(hl.dispatch, hl.dsp.submap(\"{SUBMAP}\")) \
         if hl.get_current_submap() ~= \"{SUBMAP}\" then xr_workspace_keys_off() error(\"no key submap\") end"
    ))
}

/// The key a chord names, as Hyprland writes it: any BINDABLE key (every KEYCODES name but Escape) in any case, plus
/// Prior, Next, Enter and the digits.
fn lookup_key(name: &str) -> Option<&'static str> {
    let digit = match name {
        "prior" => return Some("Page_Up"),
        "next" => return Some("Page_Down"),
        "enter" => return Some("Return"),
        "1" => 10,
        "2" => 11,
        "3" => 12,
        "4" => 13,
        "5" => 14,
        "6" => 15,
        "7" => 16,
        "8" => 17,
        "9" => 18,
        "0" => 19,
        _ => 0,
    };
    KEYCODES.iter().find_map(|&(code, key)| {
        let hit = if digit != 0 {
            code == digit
        } else {
            key != "Escape" && key.to_lowercase() == name
        };
        hit.then_some(key)
    })
}

/// Python's `str.isspace()`: Unicode White_Space plus the four ASCII separators U+001C..U+001F.
fn py_space(c: char) -> bool {
    c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c)
}

/// A bind's keys from `CTRL + ALT + K`, `ctrl+alt+k` or the helper's `CTRL+ALT+K`, written as Hyprland Lua writes them
/// (`CTRL + ALT + K`): modifiers in MOD_ORDER, then the key. Refused unless it has Ctrl, Alt or Super (Shift may join
/// them) and a bindable key.
pub fn parse_chord(text: &str) -> Result<String> {
    const BAD: Refused = Refused("invalid keys");
    if !(1..=64).contains(&text.chars().count()) {
        return Err(BAD);
    }
    let parts: Vec<&str> = text.split('+').map(|p| p.trim_matches(py_space)).collect();
    if parts.len() < 2 || parts.iter().any(|p| p.is_empty()) {
        return Err(BAD);
    }
    let mut mods: Vec<&str> = Vec::new();
    for p in &parts[..parts.len() - 1] {
        let upper = p.to_uppercase();
        let name = MOD_NAMES
            .iter()
            .find(|(k, _)| *k == upper)
            .map(|(_, v)| *v)
            .ok_or(BAD)?;
        if mods.contains(&name) {
            return Err(BAD);
        }
        mods.push(name);
    }
    let key = lookup_key(&parts[parts.len() - 1].to_lowercase())
        .ok_or(Refused("that key cannot be bound"))?;
    if mods.iter().all(|&m| m == "SHIFT") {
        return Err(Refused("a bind needs Ctrl, Alt or Super"));
    }
    let mut out: Vec<&str> = MOD_ORDER
        .iter()
        .copied()
        .filter(|m| mods.contains(m))
        .collect();
    out.push(key);
    Ok(out.join(" + "))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn chords() {
        assert_eq!(parse_chord("ctrl+alt+k").unwrap(), "CTRL + ALT + K");
        assert_eq!(
            parse_chord(" super + shift + 1 ").unwrap(),
            "SHIFT + SUPER + code:10"
        );
        assert_eq!(
            parse_chord("Control+Meta+prior").unwrap(),
            "CTRL + SUPER + Page_Up"
        );
        assert_eq!(parse_chord("ALT+\u{1f}enter").unwrap(), "ALT + Return");
        assert_eq!(
            parse_chord("ctrl+alt+escape"),
            Err(Refused("that key cannot be bound"))
        );
        assert_eq!(
            parse_chord("shift+k"),
            Err(Refused("a bind needs Ctrl, Alt or Super"))
        );
        assert_eq!(parse_chord("ctrl+ctrl+k"), Err(Refused("invalid keys")));
        assert_eq!(parse_chord("k"), Err(Refused("invalid keys")));
        assert_eq!(parse_chord("ctrl++k"), Err(Refused("invalid keys")));
        assert_eq!(
            parse_chord(&format!("ctrl+{}", "k".repeat(60))),
            Err(Refused("invalid keys"))
        );
        // 64 characters, more bytes: Python counts characters.
        assert_eq!(
            parse_chord(&format!("ctrl+alt+k{}", "\u{a0}".repeat(54))).unwrap(),
            "CTRL + ALT + K"
        );
    }
}
