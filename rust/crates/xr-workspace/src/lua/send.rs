//! send.py: a window sent onto a workspace, keeping the focus and the cursor where they were.

use super::{Refused, Result, address};

pub const SEND_OPS: [&str; 6] = [
    "send_glasses",
    "send_glasses_1",
    "send_glasses_2",
    "send_glasses_3",
    "send_glasses_4",
    "send_back",
];
pub const SEND_BINDS: [(&str, &str); 6] = [
    ("CTRL + ALT + G", "send_glasses"),
    ("CTRL + ALT + code:10", "send_glasses_1"),
    ("CTRL + ALT + code:11", "send_glasses_2"),
    ("CTRL + ALT + code:12", "send_glasses_3"),
    ("CTRL + ALT + code:13", "send_glasses_4"),
    ("CTRL + ALT + B", "send_back"),
];

/// One eval: window `addr` onto regular workspace `workspace`, not following it; then (given both) to layout pixel
/// (x, y), which cancels the shift the workspace move makes. The focus stays on it and the cursor where the hand was.
pub fn land_lua(addr: &str, workspace: i64, x: Option<i64>, y: Option<i64>) -> Result<String> {
    if !address(addr) {
        return Err(Refused("invalid window address"));
    }
    if !(0 < workspace && workspace <= 2147483647) {
        return Err(Refused("invalid workspace"));
    }
    let ok = |v: Option<i64>| v.is_none_or(|v| (-1_000_000..=1_000_000).contains(&v));
    if x.is_some() != y.is_some() || !ok(x) || !ok(y) {
        return Err(Refused("invalid window position"));
    }
    let place = match (x, y) {
        (Some(x), Some(y)) => {
            format!("hl.dispatch(hl.dsp.window.move({{ x = {x}, y = {y}, window = w }})) ")
        }
        _ => String::new(),
    };
    Ok(format!(
        "do local w = \"address:{addr}\" local p = hl.get_cursor_pos() \
         hl.dispatch(hl.dsp.window.move({{ workspace = \"{workspace}\", follow = false, window = w }})) {place}\
         hl.dispatch(hl.dsp.focus({{ window = w }})) \
         if p then hl.dispatch(hl.dsp.cursor.move({{ x = p.x, y = p.y }})) end end"
    ))
}
