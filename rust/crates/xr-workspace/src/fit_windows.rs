//! A desktop that shrinks keeps its floating windows inside it: tools/native_spike/fit_windows.py.
//!
//! Once a change that shrinks a desktop's logical size is through, every floating window on that output's regular
//! workspaces that sticks out of it moves the least it takes to be inside, into the part the bar leaves free; one larger
//! than that also shrinks to fit. Windows inside already, fullscreen ones, hidden group members and anything on another
//! output or a special workspace are left alone.

use crate::geometry::Desktop;
use crate::lua::{self, Refused, Result};
use crate::py;
use serde_json::Value;

/// An output's place in Hyprland's layout: the desktop it shows, from its top-left corner (a broker spec).
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Output<'a> {
    pub name: &'a str,
    pub x: i64,
    pub y: i64,
    pub desktop: Desktop,
}

/// (x, y, w, h) in logical layout pixels.
pub type Rect = (i64, i64, i64, i64);

/// The output as a rectangle, and the part of it Hyprland leaves free of the bar and other reserved edges (`monitors`
/// `reserved`: left, top, right, bottom); the whole output if that says nothing usable.
pub fn free_area(output: Output<'_>, monitors: &[Value]) -> (Rect, Rect) {
    let whole = (
        output.x,
        output.y,
        output.desktop.logical_width(),
        output.desktop.logical_height(),
    );
    let (x, y, w, h) = whole;
    let found: Vec<&Value> = monitors
        .iter()
        .filter(|m| m.is_object() && py::text(m.get("name")) == Some(output.name))
        .collect();
    if let [m] = found.as_slice() {
        if let Some([l, t, r, b]) = py::list(m.get("reserved")).map(Vec::as_slice) {
            let edges: Vec<i64> = [l, t, r, b]
                .iter()
                .filter_map(|v| py::int(Some(v)))
                .filter(|v| *v >= 0)
                .collect();
            if let [left, top, right, bottom] = edges[..] {
                if w - left - right > 0 && h - top - bottom > 0 {
                    return (
                        whole,
                        (x + left, y + top, w - left - right, h - top - bottom),
                    );
                }
            }
        }
    }
    (whole, whole)
}

/// A pair of numbers, as Hyprland gives `at` and `size`.
fn pair(v: Option<&Value>) -> Option<(f64, f64)> {
    match py::list(v).map(Vec::as_slice) {
        Some([a, b]) => Some((py::number(Some(a))?, py::number(Some(b))?)),
        _ => None,
    }
}

/// One window's move: its new top-left in layout px, and its new size or 0, 0 to keep it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Move {
    pub address: String,
    pub x: i64,
    pub y: i64,
    pub w: i64,
    pub h: i64,
}

/// The evals that make `moves` (lua::fit_windows::lua).
pub fn lua(moves: &[Move]) -> Result<Vec<String>> {
    let moves: Vec<lua::fit_windows::Move> = moves
        .iter()
        .map(|m| lua::fit_windows::Move {
            address: &m.address,
            x: m.x,
            y: m.y,
            w: m.w,
            h: m.h,
        })
        .collect();
    lua::fit_windows::lua(&moves)
}

/// The moves that bring `output`'s floating windows inside it, as it is now.
pub fn plan(
    clients: &Value,
    workspaces: &Value,
    monitors: &Value,
    output: Output<'_>,
) -> Result<Vec<Move>> {
    let (Some(clients), Some(workspaces), Some(monitors)) = (
        clients.as_array(),
        workspaces.as_array(),
        monitors.as_array(),
    ) else {
        return Err(Refused("invalid compositor state"));
    };
    let ((dx, dy, dw, dh), (ux, uy, uw, uh)) = free_area(output, monitors);
    let ids: Vec<i64> = workspaces
        .iter()
        .filter(|w| w.is_object() && py::text(w.get("monitor")) == Some(output.name))
        .filter_map(|w| py::int(w.get("id")))
        .filter(|id| *id > 0)
        .collect();
    let mut moves = Vec::new();
    for c in clients {
        if !c.is_object()
            || !py::is_true(c.get("floating"))
            || py::is_false(c.get("mapped"))
            || py::is_true(c.get("hidden"))
        {
            continue;
        }
        if !py::off(c.get("fullscreen")) {
            continue;
        }
        let Some(workspace) = c.get("workspace").filter(|w| w.is_object()) else {
            continue;
        };
        if !ids.iter().any(|id| py::eq_int(workspace.get("id"), *id)) {
            continue;
        }
        let Some(address) = py::text(c.get("address")).filter(|a| py::is_address(a)) else {
            continue;
        };
        let (Some(at), Some(size)) = (pair(c.get("at")), pair(c.get("size"))) else {
            continue;
        };
        if size.0 <= 0.0 || size.1 <= 0.0 {
            continue;
        }
        let [x, y, w, h] = [at.0, at.1, size.0, size.1].map(|v| py::round(v) as i64);
        if dx <= x && dy <= y && x + w <= dx + dw && y + h <= dy + dh {
            continue; // inside already
        }
        let (nw, nh) = (w.min(uw), h.min(uh));
        let resized = (nw, nh) != (w, h);
        moves.push(Move {
            address: address.to_owned(),
            x: ux.max(x.min(ux + uw - nw)),
            y: uy.max(y.min(uy + uh - nh)),
            w: if resized { nw } else { 0 },
            h: if resized { nh } else { 0 },
        });
    }
    Ok(moves)
}
