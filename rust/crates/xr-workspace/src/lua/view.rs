//! view.py: the run's Ctrl+Alt binds, the modal menu binds, the Super drag binds, the cursor warp and the session mode.

use super::{BindCommand, Refused, Result, connector, keybinds, py_g, resize, send};

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
pub const TYPING_OP: &str = "typing_toggle";
pub const SUPER_OPS: [&str; 3] = ["super_left_press", "super_right_press", "super_release"];
pub const SUPER_BINDS: [(&str, &str); 2] = [
    ("SUPER + mouse:272", "super_left_press"),
    ("SUPER + mouse:273", "super_right_press"),
];
pub const SUPER_BUTTONS: [&str; 2] = ["mouse:272", "mouse:273"];
pub const DRAG_TABLE: &str = "xr_workspace_drag_binds";
/// The bind helper's command line for the running run; unset, the Super drag binds are inert.
pub const DRAG_RUN: &str = "xr_workspace_drag_run";
pub const MENU_BINDS: [(&str, &str); 13] = [
    ("up", "menu_field_prev"),
    ("down", "menu_field_next"),
    ("left", "menu_dec"),
    ("right", "menu_inc"),
    ("SHIFT + left", "menu_dec_big"),
    ("SHIFT + right", "menu_inc_big"),
    ("Tab", "menu_desktop_next"),
    ("SHIFT + Tab", "menu_desktop_prev"),
    ("R", "menu_reset"),
    ("Escape", "menu_back"),
    ("U", "menu_ultrawide"),
    ("N", "menu_add"),
    ("SHIFT + Delete", "menu_delete"),
];
pub const OPTIONS_BINDS: [(&str, &str); 12] = [
    ("up", "binds_key 0 111"),
    ("down", "binds_key 0 116"),
    ("left", "binds_key 0 113"),
    ("right", "binds_key 0 114"),
    ("Tab", "binds_key 0 23"),
    ("SHIFT + Tab", "binds_key 1 23"),
    ("Escape", "binds_key 0 9"),
    ("Return", "binds_key 0 36"),
    ("space", "binds_key 0 65"),
    ("BackSpace", "binds_key 0 22"),
    ("Prior", "binds_key 0 112"),
    ("Next", "binds_key 0 117"),
];
/// User grant: the session-only Luma/Beast mode on the approved output.
pub const SESSION_MODE: (u32, u32, u32) = (1920, 1200, 120);

pub const LEGACY_MOUSE_CLEANUP_LUA: &str = concat!(
    r#"for _, b in ipairs(xr_workspace_mouse_binds or {}) do if tostring(b) ~= "#,
    r#""HL.Keybind(expired)" then b:remove() end end xr_workspace_mouse_binds = nil do local s "#,
    r#"= xr_workspace_mouse xr_workspace_mouse = nil if s then pcall(hl.config, { cursor = { "#,
    r#"invisible = s.invisible } }) if s.home then pcall(function() "#,
    r#"hl.dispatch(hl.dsp.cursor.move({ x = s.home.x, y = s.home.y })) end) end end end"#,
);

/// The cursor at layout pixel (x, y): the overview free pointer parks its hidden cursor and hands it back.
pub fn warp_cursor_lua(x: i64, y: i64) -> Result<String> {
    if ![x, y].iter().all(|v| (-1_000_000..=1_000_000).contains(v)) {
        return Err(Refused("invalid cursor position"));
    }
    Ok(format!(
        "pcall(function() hl.dispatch(hl.dsp.cursor.move({{ x = {x}, y = {y} }})) end)"
    ))
}

/// The approved output's placement in the granted session mode (view.session_mode's result).
#[derive(Clone, Debug)]
pub struct SessionMode<'a> {
    pub name: &'a str,
    pub x: i64,
    pub y: i64,
    pub scale: f64,
}

/// Re-apply the granted session mode after a config reload reset it.
pub fn restore_mode_lua(mode: &SessionMode) -> Result<String> {
    if !connector(mode.name, &["DP", "HDMI-A", "eDP"], 1, usize::MAX)
        || !(0.25..=4.0).contains(&mode.scale)
    {
        return Err(Refused("invalid session mode"));
    }
    let (w, h, r) = SESSION_MODE;
    Ok(format!(
        "hl.monitor({{ output = \"{}\", mode = \"{w}x{h}@{r}\", position = \"{}x{}\", scale = {} }})",
        mode.name,
        mode.x,
        mode.y,
        py_g(mode.scale)
    ))
}

/// The run's Ctrl+Alt binds; in typing mode only the Ctrl+Alt+T toggle, in the same table. `binds`: the key bind
/// panel's (chord, op) pairs instead of BINDS.
pub fn install_binds_lua(
    cmd: &BindCommand,
    typing: bool,
    binds: Option<&[(&str, &str)]>,
) -> Result<String> {
    let base = cmd.base()?;
    let ops: Vec<&str> = BINDS.iter().map(|b| b.1).collect();
    let binds = match binds {
        None => BINDS.to_vec(),
        Some(b) => checked_binds(b, &ops)?,
    };
    let binds: Vec<_> = if typing {
        binds.into_iter().filter(|b| b.1 == TYPING_OP).collect()
    } else {
        binds
    };
    Ok(binds_lua("xr_workspace_binds", &binds, &base))
}

/// Key bind panel pairs re-checked before they go into Lua: known ops, each at most once, chords parse_chord writes back
/// unchanged.
fn checked_binds<'a>(
    binds: &[(&'a str, &'a str)],
    ops: &[&str],
) -> Result<Vec<(&'a str, &'a str)>> {
    let unique = binds
        .iter()
        .enumerate()
        .all(|(i, b)| binds[..i].iter().all(|p| p.1 != b.1));
    if !unique || binds.iter().any(|b| !ops.contains(&b.1)) {
        return Err(Refused("invalid binds"));
    }
    for (chord, _) in binds {
        if keybinds::parse_chord(chord)? != *chord {
            return Err(Refused("invalid binds"));
        }
    }
    Ok(binds.to_vec())
}

/// The shared command prefix is a Lua local: one copy per eval keeps the chunk well under the eval limit.
pub fn binds_lua(table: &str, binds: &[(&str, &str)], base: &str) -> String {
    let calls = binds
        .iter()
        .map(|(chord, op)| {
            let release = if op.ends_with("_release") {
                ", { release = true }"
            } else {
                ""
            };
            format!("hl.bind(\"{chord}\", hl.dsp.exec_cmd(c .. \"{op}\"){release})")
        })
        .collect::<Vec<_>>()
        .join(", ");
    format!("local c = \"{base} \" {table} = {{ {calls} }}")
}

/// Hyprland 0.56: remove() on an expired handle (after unbind/reload) segfaults; probe first.
pub fn remove_lua(table: &str) -> String {
    format!(
        "for _, b in ipairs({table} or {{}}) do if tostring(b) ~= \"HL.Keybind(expired)\" then b:remove() end end {table} = nil"
    )
}

/// Run end / reinstall: the view, send-window, Super drag and modal menu binds, and an open key bind panel.
pub fn remove_binds_lua() -> String {
    format!(
        "{} {} {} {} {}",
        remove_lua("xr_workspace_binds"),
        remove_send_binds_lua(),
        remove_drag_binds_lua(),
        remove_menu_binds_lua(),
        keybinds::PANEL_OFF_LUA
    )
}

/// Send-window binds (send.SEND_BINDS, or the key bind panel's) in their own table and eval.
pub fn install_send_binds_lua(cmd: &BindCommand, binds: Option<&[(&str, &str)]>) -> Result<String> {
    let base = cmd.base()?;
    let binds = match binds {
        None => send::SEND_BINDS.to_vec(),
        Some(b) => checked_binds(b, &send::SEND_OPS)?,
    };
    Ok(format!(
        "{} {}",
        remove_send_binds_lua(),
        binds_lua("xr_workspace_send_binds", &binds, &base)
    ))
}

pub fn remove_send_binds_lua() -> String {
    remove_lua("xr_workspace_send_binds")
}

/// The run's view of Super drags: non-consuming binds made once per Hyprland config and armed by DRAG_RUN; never
/// removed, since Keybind:remove() would take the user's own Move window bind with them.
pub fn install_drag_binds_lua(cmd: &BindCommand) -> Result<String> {
    let base = cmd.base()?;
    let presses = SUPER_BINDS
        .iter()
        .map(|(chord, op)| {
            format!("bind(\"{chord}\", press(\"{op}\"), {{ non_consuming = true }})")
        })
        .collect::<Vec<_>>()
        .join(" ");
    let releases = SUPER_BUTTONS
        .iter()
        .map(|b| format!("bind(\"SUPER + {b}\", release, {{ release = true, non_consuming = true, ignore_mods = true }})"))
        .collect::<Vec<_>>()
        .join(" ");
    let grab = resize::GRAB_TABLE;
    let up = SUPER_OPS[2];
    Ok(format!(
        "{DRAG_RUN} = \"{base} \" do local live = false \
         for _, b in ipairs({DRAG_TABLE} or {{}}) do if tostring(b) ~= \"HL.Keybind(expired)\" then live = true end end \
         if not live then local t, held = {{}}, false \
         local function bind(k, f, o) local ok, b = pcall(hl.bind, k, f, o) if ok and b then t[#t + 1] = b end end \
         local function press(op) return function() if not {DRAG_RUN} then return end if {grab} then \
         hl.timer(function() hl.dispatch(hl.dsp.window.drag()) end, {{ timeout = 1, type = \"oneshot\" }}) return end \
         held = true hl.exec_cmd({DRAG_RUN} .. op) end end \
         local function release() if held then held = false if {DRAG_RUN} then hl.exec_cmd({DRAG_RUN} .. \"{up}\") end end end \
         {presses} {releases} {DRAG_TABLE} = t end end"
    ))
}

/// Disarms the Super drag binds; never removes them.
pub fn remove_drag_binds_lua() -> String {
    format!("{DRAG_RUN} = nil")
}

/// Modal placement-panel binds (bare arrows, Tab, R, Escape); replaces any surviving set.
pub fn install_menu_binds_lua(cmd: &BindCommand) -> Result<String> {
    let base = cmd.base()?;
    Ok(format!(
        "{} {}",
        remove_menu_binds_lua(),
        binds_lua("xr_workspace_menu_binds", &MENU_BINDS, &base)
    ))
}

/// The options menu's keys, replacing whatever the shared menu table holds.
pub fn install_options_binds_lua(cmd: &BindCommand) -> Result<String> {
    let base = cmd.base()?;
    Ok(format!(
        "{} {}",
        remove_menu_binds_lua(),
        binds_lua("xr_workspace_menu_binds", &OPTIONS_BINDS, &base)
    ))
}

/// Panel collapsed, run end, or a panel key with no run listening; plus the legacy mouse cleanup.
pub fn remove_menu_binds_lua() -> String {
    format!(
        "{} {LEGACY_MOUSE_CLEANUP_LUA}",
        remove_lua("xr_workspace_menu_binds")
    )
}

/// The `mutate` command that gives managed output `name` Hyprland's monitor focus (the glasses view's focus).
pub fn focus_command(name: &str) -> Result<String> {
    if !super::managed(name) {
        return Err(Refused("focus is limited to managed outputs"));
    }
    Ok(format!("dispatch hl.dsp.focus({{monitor=\"{name}\"}})"))
}
