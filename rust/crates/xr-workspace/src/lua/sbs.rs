//! sbs.py: the glasses' wide (3D side-by-side) mode, and Pro 2's 2D timings put back.

use super::{Refused, Result, connector, py_g};

/// The wide mode and its rates, best first, for a glasses model.
fn mode(model: &str) -> Result<((u32, u32), &'static [f64])> {
    match model {
        "pro2" => Ok(((3840, 1080), &[60.0])),
        "luma-ultra" => Ok(((3840, 1200), &[90.0, 60.0])),
        _ => Err(Refused("unsupported 3D glasses model")),
    }
}

/// The rule for a wide mode, from values checked here. `model` is `luma-ultra` unless the glasses are a Pro 2.
pub fn wide_mode_lua(
    name: &str,
    x: i64,
    y: i64,
    scale: f64,
    rate: f64,
    model: &str,
) -> Result<String> {
    let ((w, h), rates) = mode(model)?;
    if !rates.contains(&rate) {
        return Err(Refused("invalid wide mode"));
    }
    mode_lua(name, x, y, scale, w, h, rate)
}

fn mode_lua(
    name: &str,
    x: i64,
    y: i64,
    scale: f64,
    width: u32,
    height: u32,
    rate: f64,
) -> Result<String> {
    if !connector(name, &["DP", "HDMI-A"], 1, usize::MAX) || !(0.25..=4.0).contains(&scale) {
        return Err(Refused("invalid wide mode"));
    }
    Ok(format!(
        "hl.monitor({{ output = \"{name}\", mode = \"{width}x{height}@{}\", position = \"{x}x{y}\", scale = {} }})",
        py_g(rate),
        py_g(scale)
    ))
}

/// Only Pro 2's 1080p 2D timings (60, 90 or 120 Hz, within half a hertz); callers also require the timing in the
/// current EDID.
pub fn pro2_mode_lua(name: &str, x: i64, y: i64, scale: f64, rate: f64) -> Result<String> {
    if !rate.is_finite()
        || ![60.0, 90.0, 120.0]
            .iter()
            .any(|hz: &f64| (rate - hz).abs() < 0.5)
    {
        return Err(Refused("invalid Pro 2 2D rate"));
    }
    mode_lua(name, x, y, scale, 1920, 1080, rate)
}
