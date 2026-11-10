//! input_lock.py: the pointer captured for the options menu; the other buttons and the wheel go to the run.

use super::{BindCommand, Result, free_pointer, resize};

pub const OTHER_BUTTONS: [&str; 7] = [
    "mouse:273",
    "mouse:274",
    "mouse:275",
    "mouse:276",
    "mouse:277",
    "mouse:278",
    "mouse:279",
];
pub const OTHER_BUTTON_OP: &str = "resize_release";
pub const WHEEL: [&str; 4] = ["mouse_up", "mouse_down", "mouse_left", "mouse_right"];
pub const SCROLL_DELAY: &str = "binds.scroll_event_delay";

pub const PIN_HOME_LUA: &str = concat!(
    r#"if xr_workspace_grab then local p = hl.get_cursor_pos() if p then xr_workspace_grab.home "#,
    r#"= { x = p.x, y = p.y } end end"#,
);

fn quoted(items: &[&str]) -> String {
    items
        .iter()
        .map(|i| format!("\"{i}\""))
        .collect::<Vec<_>>()
        .join(", ")
}

/// The lock's binds, added to the free pointer's bind table: the other buttons to the helper's resize_release, the
/// wheel to nothing (up and down scroll the options), and Hyprland's scroll event delay at 0 until the table goes.
pub fn lock_binds_lua(cmd: &BindCommand) -> Result<String> {
    let base = cmd.base()?;
    let t = resize::BIND_TABLE;
    Ok(format!(
        "do local t = {t} or {{}} {t} = t local ok, d = pcall(hl.get_config, \"{SCROLL_DELAY}\") \
         if ok and type(d) == \"number\" and d ~= 0 and pcall(hl.config, {{ binds = {{ scroll_event_delay = 0 }} }}) then \
         t[#t + 1] = {{ remove = function() pcall(hl.config, {{ binds = {{ scroll_event_delay = d }} }}) end }} end \
         local c = \"{base} {OTHER_BUTTON_OP}\" for _, k in ipairs({{ {buttons} }}) do local ok, b = pcall(hl.bind, k, hl.dsp.exec_cmd(c)) \
         if ok and b then t[#t + 1] = b end end local wc = \"{base} options_scroll \" \
         for _, k in ipairs({{ {wheel} }}) do local action = function() end \
         if k == \"mouse_up\" then action = hl.dsp.exec_cmd(wc .. \"-38\") \
         elseif k == \"mouse_down\" then action = hl.dsp.exec_cmd(wc .. \"38\") end \
         local ok, b = pcall(hl.bind, k, action) if ok and b then t[#t + 1] = b end end end",
        buttons = quoted(&OTHER_BUTTONS),
        wheel = quoted(&WHEEL),
    ))
}

/// Capture or re-park: the free pointer's grab at layout pixel (x, y) plus the lock's binds, in one eval.
pub fn lock_pointer_lua(cmd: &BindCommand, x: i64, y: i64) -> Result<String> {
    Ok(format!(
        "{PIN_HOME_LUA} {} {}",
        free_pointer::grab_pointer_lua(cmd, x, y)?,
        lock_binds_lua(cmd)?
    ))
}
