//! unified_cursor.py: one cursor across the physical monitor and the desktops.

use super::{BindCommand, Refused, Result, free_pointer, managed, resize, view};

pub const CLICK_OPTS: [&str; 2] = [
    "{ non_consuming = true }",
    "{ release = true, non_consuming = true }",
];

/// Over home: the left button's press and release, non-consuming, to the helper's resize_press / resize_release,
/// added to the free pointer's bind table.
pub fn click_binds_lua(cmd: &BindCommand) -> Result<String> {
    let base = cmd.base()?;
    let binds = resize::RESIZE_OPS
        .iter()
        .zip(CLICK_OPTS)
        .enumerate()
        .map(|(i, (op, opts))| {
            format!(
                "local ok{i}, b{i} = pcall(hl.bind, \"{}\", hl.dsp.exec_cmd(c .. \"{op}\"), {opts}) \
                 if ok{i} and b{i} then t[#t + 1] = b{i} end",
                resize::RESIZE_BUTTON
            )
        })
        .collect::<Vec<_>>()
        .join(" ");
    let t = resize::BIND_TABLE;
    Ok(format!(
        "do local t = {t} or {{}} {t} = t local c = \"{base} \" {binds} end"
    ))
}

/// Into desktop mode: any free-mode grab removed, the cursor warped to layout pixel (x, y) if given, then the click
/// binds. With `monitor` (and a point) that output gets Hyprland's monitor focus first.
pub fn enter_desktop_lua(
    cmd: &BindCommand,
    x: Option<i64>,
    y: Option<i64>,
    monitor: Option<&str>,
) -> Result<String> {
    let mut focus = String::new();
    if let (Some(m), Some(_)) = (monitor, x) {
        if !managed(m) {
            return Err(Refused("focus is limited to managed outputs"));
        }
        focus =
            format!("pcall(function() hl.dispatch(hl.dsp.focus({{ monitor = \"{m}\" }})) end) ");
    }
    Ok(format!(
        "{focus}{} {}",
        free_pointer::release_pointer_lua(x, y)?,
        click_binds_lua(cmd)?
    ))
}

/// Zoomed in: the cursor back at layout pixel (x, y) on home, and home's monitor focused again.
pub fn confine_lua(x: i64, y: i64, name: &str) -> Result<String> {
    if !managed(name) {
        return Err(Refused("confinement is limited to managed outputs"));
    }
    Ok(format!(
        "{} pcall(function() hl.dispatch(hl.dsp.focus({{ monitor = \"{name}\" }})) end)",
        view::warp_cursor_lua(x, y)?
    ))
}
