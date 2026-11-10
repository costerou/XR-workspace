//! Overview corner resize and pill moves, the shared pieces: tools/native_spike/resize.py. The binds are lua::resize.
//!
//! The modal left-button binds every free-pointer click goes through (resize_press / resize_release), the two Super keys
//! that cancel a monitor drag, and the run's managed outputs as logical rectangles. The press bind has no modifier, so
//! with Super held it never reaches the run: Super + drag stays Hyprland's own window move and resize.

use crate::py;
use serde_json::Value;

pub const CORNERS: [&str; 4] = ["tl", "tr", "bl", "br"];

/// One managed output's logical rectangle, `index` its place in the run's names.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct OutputRect {
    pub index: usize,
    pub x: i64,
    pub y: i64,
    pub width: f64,
    pub height: f64,
}

/// Logical rectangles of the run's managed outputs `names`, in `names` order, from Hyprland's `monitors`; a missing or
/// malformed monitor is left out.
pub fn outputs_from_monitors(raw: &Value, names: &[&str]) -> Vec<OutputRect> {
    let Some(raw) = raw.as_array() else {
        return Vec::new();
    };
    let mut out = Vec::new();
    for (index, name) in names.iter().enumerate() {
        let Some(m) = raw
            .iter()
            .find(|m| m.is_object() && py::text(m.get("name")) == Some(name))
        else {
            continue;
        };
        let [w, h, x, y] = ["width", "height", "x", "y"].map(|k| py::int(m.get(k)));
        let (Some(w), Some(h), Some(x), Some(y), Some(scale)) =
            (w, h, x, y, py::number(m.get("scale")))
        else {
            continue;
        };
        if !(0.25..=4.0).contains(&scale) || w <= 0 || h <= 0 {
            continue;
        }
        out.push(OutputRect {
            index,
            x,
            y,
            width: w as f64 / scale,
            height: h as f64 / scale,
        });
    }
    out
}
