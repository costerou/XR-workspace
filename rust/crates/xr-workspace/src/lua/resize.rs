//! resize.py: the overview's left-button press and release binds, and Super to cancel a drag.

use super::{BindCommand, Result, view};

pub const RESIZE_OPS: [&str; 2] = ["resize_press", "resize_release"];
pub const RESIZE_BUTTON: &str = "mouse:272";
/// Super went down (OverviewPointer.super_down).
pub const CANCEL_OP: &str = "resize_cancel";
pub const SUPER_KEYS: [&str; 2] = ["Super_L", "Super_R"];
pub const GRAB_TABLE: &str = "xr_workspace_grab";
pub const BIND_TABLE: &str = "xr_workspace_resize_binds";

pub const GRAB_LEAVE_LUA: &str = concat!(
    r#"do local s = xr_workspace_grab xr_workspace_grab = nil if s then pcall(hl.config, { "#,
    r#"cursor = { invisible = s.invisible } }) if s.home then pcall(function() "#,
    r#"hl.dispatch(hl.dsp.cursor.move({ x = s.home.x, y = s.home.y })) end) end end end"#,
);

/// The modal left-button press and release binds, replacing any surviving set (so never doubled).
pub fn install_resize_binds_lua(cmd: &BindCommand) -> Result<String> {
    let base = cmd.base()?;
    let keys = SUPER_KEYS
        .iter()
        .map(|key| {
            format!(
                "do local ok, b = pcall(hl.bind, \"{key}\", hl.dsp.exec_cmd(c .. \"{CANCEL_OP}\"), \
                 {{ ignore_mods = true, non_consuming = true }}) if ok and b then t[#t + 1] = b end end"
            )
        })
        .collect::<Vec<_>>()
        .join(" ");
    Ok(format!(
        "{} local c = \"{base} \" {BIND_TABLE} = {{ hl.bind(\"{RESIZE_BUTTON}\", hl.dsp.exec_cmd(c .. \"resize_press\")), \
         hl.bind(\"{RESIZE_BUTTON}\", hl.dsp.exec_cmd(c .. \"resize_release\"), {{ release = true, ignore_mods = true }}) }} \
         local t = {BIND_TABLE} {keys}",
        remove_resize_binds_lua()
    ))
}

/// Also shows the cursor again and puts it back if free mode hid it (no-op otherwise).
pub fn remove_resize_binds_lua() -> String {
    format!("{} {GRAB_LEAVE_LUA}", view::remove_lua(BIND_TABLE))
}
