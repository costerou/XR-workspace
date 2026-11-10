//! overview_cursor.py: the real cursor hidden while the overview draws its own dot.

pub const TABLE: &str = "xr_workspace_overview_cursor";

pub const HIDE_LUA: &str = concat!(
    r#"do local g = xr_workspace_grab if xr_workspace_overview_cursor == nil then local "#,
    r#"invisible if g then invisible = g.invisible == true else local ok, hidden = "#,
    r#"pcall(hl.get_config, "cursor.invisible") invisible = ok and hidden == true end "#,
    r#"xr_workspace_overview_cursor = { invisible = invisible } end if g then g.invisible = "#,
    r#"true end pcall(hl.config, { cursor = { invisible = true } }) end"#,
);

pub const SHOW_LUA: &str = concat!(
    r#"do local s = xr_workspace_overview_cursor xr_workspace_overview_cursor = nil if s then "#,
    r#"if xr_workspace_grab then xr_workspace_grab.invisible = s.invisible else "#,
    r#"pcall(hl.config, { cursor = { invisible = s.invisible } }) end end end"#,
);
