//! free_pointer.py: the overview's free mode, with the real cursor hidden and parked.

use super::{BindCommand, Result, resize, view};

pub const HIDE_LUA: &str = concat!(
    r#"if xr_workspace_grab == nil then local ok, hidden = pcall(hl.get_config, "#,
    r#""cursor.invisible") local p = hl.get_cursor_pos() xr_workspace_grab = { invisible = ok "#,
    r#"and hidden == true, home = p and { x = p.x, y = p.y } } end pcall(hl.config, { cursor = "#,
    r#"{ invisible = true } })"#,
);

pub const SHOW_LUA: &str = concat!(
    r#"do local s = xr_workspace_grab xr_workspace_grab = nil if s then pcall(hl.config, { "#,
    r#"cursor = { invisible = s.invisible } }) end end"#,
);

/// Enter free mode: the left-button binds, the cursor hidden and parked at layout pixel (x, y). One eval.
pub fn grab_pointer_lua(cmd: &BindCommand, x: i64, y: i64) -> Result<String> {
    let binds = resize::install_resize_binds_lua(cmd)?;
    Ok(format!(
        "{binds} {HIDE_LUA} {}",
        view::warp_cursor_lua(x, y)?
    ))
}

/// Leave free mode for desktop mode: binds removed, cursor shown and (if `x` is given) put at layout pixel (x, y).
pub fn release_pointer_lua(x: Option<i64>, y: Option<i64>) -> Result<String> {
    let mut out = format!("{} {SHOW_LUA}", view::remove_lua(resize::BIND_TABLE));
    if let Some(x) = x {
        // Python warps with y as given: a missing y is refused there as a non-int position.
        let y = y.ok_or(super::Refused("invalid cursor position"))?;
        out.push(' ');
        out.push_str(&view::warp_cursor_lua(x, y)?);
    }
    Ok(out)
}
