//! The send keys: one window onto a desktop of the glasses, or back: tools/native_spike/send.py.
//!
//! The pure part: which window is under a point, where a dropped floating window lands, which output an op sends to and
//! the request command that moves it (lua::send has the eval and the binds). The worker that runs one op at a time is the run's.

use crate::lua::{Refused, Result, send::SEND_OPS};
use crate::py;
use serde_json::{Value, json};

/// The notification's app name and title.
pub const APP: &str = "XR Workspace";

fn checked(address: &str, workspace: i64) -> Result<()> {
    if !py::is_address(address) {
        return Err(Refused("invalid window address"));
    }
    if !(1..=2147483647).contains(&workspace) {
        return Err(Refused("invalid workspace"));
    }
    Ok(())
}

/// The request-socket command that moves window `address` onto workspace `workspace`, following it or not.
pub fn move_command(address: &str, workspace: i64, follow: bool) -> Result<String> {
    checked(address, workspace)?;
    Ok(format!(
        "dispatch hl.dsp.window.move({{ workspace = \"{workspace}\", follow = {follow}, window = \"address:{address}\" }})"
    ))
}

/// Where a floating window (Hyprland `activewindow`: at, size) dropped by a Super drag lands on `monitor`: as near as it
/// can be to where the drag left it, but whole on that monitor; wider or taller than it, from its left or top edge.
pub fn landing(window: &Value, monitor: &Value) -> Result<(i64, i64)> {
    let bad = || Refused("invalid window or monitor geometry");
    let two = |v: Option<&Value>| match py::list(v).map(Vec::as_slice) {
        Some([a, b]) => Some((py::number(Some(a))?, py::number(Some(b))?)),
        _ => None,
    };
    let (at, size) = (
        two(window.get("at")).ok_or_else(bad)?,
        two(window.get("size")).ok_or_else(bad)?,
    );
    let get = |k: &str| py::number(monitor.get(k)).ok_or_else(bad);
    let (w, h, x, y, scale) = (
        get("width")?,
        get("height")?,
        get("x")?,
        get("y")?,
        get("scale")?,
    );
    if scale <= 0.0 {
        return Err(bad());
    }
    let axis = |v: f64, length: f64, lo: f64, span: f64| {
        py::round(lo.max(v.min(lo + span - length))) as i64
    };
    Ok((
        axis(at.0, size.0, x, w / scale),
        axis(at.1, size.1, y, h / scale),
    ))
}

/// The monitor named `name` (exactly one) and its active regular workspace id.
pub(crate) fn monitor<'a>(monitors: &'a Value, name: &str) -> Result<Option<(&'a Value, Option<i64>)>> {
    let monitors = monitors.as_array().ok_or(Refused("invalid monitors"))?;
    let found: Vec<&Value> = monitors
        .iter()
        .filter(|m| m.is_object() && py::text(m.get("name")) == Some(name))
        .collect();
    let [m] = found.as_slice() else {
        return Ok(None);
    };
    let active = m
        .get("activeWorkspace")
        .filter(|a| a.is_object())
        .and_then(|a| py::int(a.get("id")))
        .filter(|id| *id > 0);
    Ok(Some((m, active)))
}

/// The window shown at layout point (x, y) on output `name`, as `{"address", "w", "h", "pinned"}`, or None. A
/// fullscreen window on the output's active workspace covers the point; otherwise floating windows (and pinned ones, on
/// every workspace of their monitor) are above tiled ones, the most recently focused first.
pub fn window_at(
    clients: &Value,
    monitors: &Value,
    name: &str,
    x: f64,
    y: f64,
) -> Result<Option<Value>> {
    let Some((monitor, Some(workspace))) = monitor(monitors, name)? else {
        return Ok(None);
    };
    let Some(clients) = clients.as_array() else {
        return Ok(None);
    };
    let mut best: Option<((u8, i64), &Value)> = None;
    for c in clients {
        if !c.is_object() || !py::text(c.get("address")).is_some_and(py::is_address) {
            continue;
        }
        if py::is_false(c.get("mapped")) || py::is_true(c.get("hidden")) {
            continue;
        }
        let pinned = py::is_true(c.get("pinned"));
        let on = c
            .get("workspace")
            .is_some_and(|w| w.is_object() && py::eq_int(w.get("id"), workspace))
            || (pinned && py::eq(c.get("monitor"), monitor.get("id")));
        let (at, size) = (py::list(c.get("at")), py::list(c.get("size")));
        let (Some([ax, ay]), Some([sw, sh])) = (at.map(Vec::as_slice), size.map(Vec::as_slice))
        else {
            continue;
        };
        let nums: Vec<f64> = [ax, ay, sw, sh]
            .iter()
            .filter_map(|v| py::number(Some(v)))
            .collect();
        let [ax, ay, sw, sh] = nums[..] else { continue };
        if !on || sw <= 0.0 || sh <= 0.0 {
            continue;
        }
        if !(ax <= x && x < ax + sw && ay <= y && y < ay + sh) {
            continue;
        }
        let rank = if !py::off(c.get("fullscreen")) {
            0
        } else if py::is_true(c.get("floating")) || pinned {
            1
        } else {
            2
        };
        let history = py::int(c.get("focusHistoryID"))
            .filter(|h| *h >= 0)
            .unwrap_or(1 << 30);
        if best.is_none_or(|(key, _)| (rank, history) < key) {
            best = Some(((rank, history), c));
        }
    }
    Ok(best.map(|(_, c)| json!({"address": c["address"], "w": c["size"][0], "h": c["size"][1], "pinned": py::is_true(c.get("pinned"))})))
}

/// The output an op sends to: `selected` for send_glasses, XR-n for send_glasses_n, `fallback` for send_back.
pub fn target_for(
    op: &str,
    selected: Option<&str>,
    fallback: Option<&str>,
) -> Result<Option<String>> {
    if !SEND_OPS.contains(&op) {
        return Err(Refused("unsupported send operation"));
    }
    Ok(match op {
        "send_back" => fallback.map(str::to_owned),
        "send_glasses" => selected.map(str::to_owned),
        _ => Some(format!("XR-{}", &op[op.len() - 1..])),
    })
}
