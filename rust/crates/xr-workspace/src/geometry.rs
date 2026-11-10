//! Desktop sizes, scales and placements: tools/native_spike/geometry.py.

use crate::lua::{Refused, Result};
use crate::py;
use serde_json::{Value, json};

/// Hyprland output scales a desktop may use.
pub const SCALES: [f64; 7] = [1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0];
/// What the broker accepts for a custom desktop, in pixels (both even).
pub const WIDTH_BOUNDS: (i64, i64) = (640, 7680);
pub const HEIGHT_BOUNDS: (i64, i64) = (480, 4320);
pub const MAX_DESKTOPS: usize = 4;
pub const MAX_ROLL: i64 = 9000;

/// The Ultrawide button's widths for the common heights, then the 16:9 ones it goes back to.
const ULTRAWIDE_WIDTHS: [(i64, i64); 3] = [(1080, 2560), (1440, 3440), (2160, 5120)];
const STANDARD_WIDTHS: [(i64, i64); 3] = [(1080, 1920), (1440, 2560), (2160, 3840)];

/// A valid desktop: pixel size and Hyprland output scale (validate_desktop's result).
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Desktop {
    pub width: i64,
    pub height: i64,
    pub scale: f64,
}

pub const DEFAULT_DESKTOP: Desktop = Desktop {
    width: 1920,
    height: 1080,
    scale: 1.0,
};

/// Every scale in SCALES is a multiple of 1/4, so scale*4 is an integer and the check is exact.
pub fn exact_scale(width: i64, height: i64, scale: f64) -> bool {
    let n = py::round(scale * 4.0);
    n > 0.0 && n / 4.0 == scale && (width * 4) % (n as i64) == 0 && (height * 4) % (n as i64) == 0
}

pub fn valid_scales(width: i64, height: i64) -> Vec<f64> {
    SCALES
        .into_iter()
        .filter(|&s| exact_scale(width, height, s))
        .collect()
}

/// The step of valid_scales nearest `scale`; a tie goes to the larger step. 1.0 divides every even size.
pub fn nearest_scale(width: i64, height: i64, scale: f64) -> f64 {
    let mut best: Option<f64> = None;
    for s in valid_scales(width, height) {
        let better = match best {
            None => true,
            Some(b) => ((s - scale).abs(), -s) < ((b - scale).abs(), -b),
        };
        if better {
            best = Some(s);
        }
    }
    best.unwrap_or(1.0)
}

impl Desktop {
    /// validate_desktop on a size and scale already of the right types.
    pub fn new(width: i64, height: i64, scale: f64) -> Result<Desktop> {
        if !SCALES.contains(&scale) {
            return Err(Refused("unsupported scale"));
        }
        for (v, key, (lo, hi)) in [
            (width, "width", WIDTH_BOUNDS),
            (height, "height", HEIGHT_BOUNDS),
        ] {
            if !(lo..=hi).contains(&v) || v % 2 != 0 {
                return Err(Refused(match key {
                    "width" => "width out of bounds or odd",
                    _ => "height out of bounds or odd",
                }));
            }
        }
        if !exact_scale(width, height, scale) {
            return Err(Refused("scale does not divide resolution exactly"));
        }
        Ok(Desktop {
            width,
            height,
            scale,
        })
    }

    /// validate_desktop: exactly width, height, scale, as JSON.
    pub fn from_value(d: &Value) -> Result<Desktop> {
        if !py::has_exactly(d, &["width", "height", "scale"]) {
            return Err(Refused("desktop must have exactly width, height, scale"));
        }
        Desktop::from_parts(d.get("width"), d.get("height"), d.get("scale"))
    }

    /// validate_desktop on three JSON values (a spec or a Hyprland monitor's own keys).
    pub fn from_parts(
        width: Option<&Value>,
        height: Option<&Value>,
        scale: Option<&Value>,
    ) -> Result<Desktop> {
        let (Some(w), Some(h)) = (py::int(width), py::int(height)) else {
            return Err(Refused("width and height must be integers"));
        };
        let Some(s) = py::number(scale) else {
            return Err(Refused("unsupported scale"));
        };
        Desktop::new(w, h, s)
    }

    pub fn to_value(self) -> Value {
        json!({"width": self.width, "height": self.height, "scale": self.scale})
    }

    pub fn logical_width(self) -> i64 {
        (self.width as f64 / self.scale) as i64
    }

    pub fn logical_height(self) -> i64 {
        (self.height as f64 / self.scale) as i64
    }

    /// Wider than 2:1 (21:9 and 32:9 count).
    pub fn is_ultrawide(self) -> bool {
        self.width > 2 * self.height
    }

    /// The same pixel size at another scale (a pill's Scale dropdown).
    pub fn with_scale(self, scale: f64) -> Result<Desktop> {
        Desktop::new(self.width, self.height, scale)
    }

    /// Another pixel size (a pill's Resolution dropdown): the scale stays if it divides it, else the nearest that does.
    pub fn with_resolution(self, width: i64, height: i64) -> Result<Desktop> {
        Desktop::new(width, height, 1.0)?;
        Desktop::new(width, height, nearest_scale(width, height, self.scale))
    }

    /// What the Ultrawide button makes of the desktop: 21:9 at its height, or back to 16:9. Height and scale stay; if
    /// the new width cannot keep the scale exact, the nearest width that can (within 64 px), else scale 1.
    pub fn toggle_ultrawide(self) -> Desktop {
        let (h, scale) = (self.height, self.scale);
        let (table, ratio) = if self.is_ultrawide() {
            (STANDARD_WIDTHS, 16.0 / 9.0)
        } else {
            (ULTRAWIDE_WIDTHS, 64.0 / 27.0)
        };
        let (lo, hi) = WIDTH_BOUNDS;
        let wanted = table
            .iter()
            .find(|(height, _)| *height == h)
            .map(|(_, w)| *w)
            .unwrap_or_else(|| py::round(h as f64 * ratio / 8.0) as i64 * 8);
        let width = wanted.max(lo).min(hi);
        for step in 0..33 {
            for w in [width - 2 * step, width + 2 * step] {
                if (lo..=hi).contains(&w) && exact_scale(w, h, scale) {
                    if let Ok(d) = Desktop::new(w, h, scale) {
                        return d;
                    }
                }
            }
        }
        Desktop::new(width, h, 1.0).unwrap_or(self)
    }
}

/// A desktop's place round you (geometry.PLACEMENT_BOUNDS): orbit x, y in tenths of a degree, distance in percent,
/// curvature in degrees.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Placement {
    pub x: i64,
    pub y: i64,
    pub distance: i64,
    pub curvature: i64,
}

pub const PLACEMENT_KEYS: [&str; 4] = ["x", "y", "distance", "curvature"];
pub const PLACEMENT_BOUNDS: [(i64, i64); 4] = [(-1800, 1800), (-900, 900), (10, 200), (0, 120)];
pub const DEFAULT_PLACEMENT: Placement = Placement {
    x: 0,
    y: 0,
    distance: 100,
    curvature: 0,
};

impl Placement {
    /// validate_placement.
    pub fn from_value(p: &Value) -> Result<Placement> {
        if !py::has_exactly(p, &PLACEMENT_KEYS) {
            return Err(Refused(
                "placement must have exactly x, y, distance, curvature",
            ));
        }
        let mut v = [0; 4];
        for (i, key) in PLACEMENT_KEYS.iter().enumerate() {
            let (lo, hi) = PLACEMENT_BOUNDS[i];
            match py::int(p.get(*key)) {
                Some(n) if (lo..=hi).contains(&n) => v[i] = n,
                _ => {
                    return Err(Refused(
                        [
                            "placement x must be an integer -1800..1800",
                            "placement y must be an integer -900..900",
                            "placement distance must be an integer 10..200",
                            "placement curvature must be an integer 0..120",
                        ][i],
                    ));
                }
            }
        }
        Ok(Placement {
            x: v[0],
            y: v[1],
            distance: v[2],
            curvature: v[3],
        })
    }

    pub fn to_value(self) -> Value {
        json!({"x": self.x, "y": self.y, "distance": self.distance, "curvature": self.curvature})
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn desktops() {
        assert_eq!(
            Desktop::from_value(&json!({"width": 1920, "height": 1080, "scale": 1}))
                .unwrap()
                .scale,
            1.0
        );
        assert!(
            Desktop::from_value(&json!({"width": 1920, "height": 1080, "scale": true})).is_err()
        );
        assert!(
            Desktop::from_value(&json!({"width": 1921, "height": 1080, "scale": 1.0})).is_err()
        );
        assert!(Desktop::new(1920, 1080, 1.75).is_err());
        let d = Desktop::new(1920, 1080, 1.5).unwrap();
        assert_eq!(d.toggle_ultrawide(), Desktop::new(2562, 1080, 1.5).unwrap());
        assert_eq!(d.toggle_ultrawide().toggle_ultrawide(), d);
        assert_eq!(nearest_scale(2560, 1440, 1.75), 2.0);
        assert_eq!(d.logical_width(), 1280);
    }
}
