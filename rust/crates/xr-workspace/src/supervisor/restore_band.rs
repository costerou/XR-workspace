//! The Step-Back Band's rest (R3, spike/include/xr/options_geometry.hpp has the picture and the pill). A port of
//! `tools/native_spike/restore_band.py`.
//!
//! The pointer asks for the band by resting pressed against the output's top edge. Near the edge is not enough: tabs
//! and title bars live there, and only the edge stops a hand. Hyprland clamps the cursor to the output, so the
//! outermost row is where a hand pressing up ends.

use super::python::py_hypot;
use crate::resize::OutputRect;

/// A rest this long (seconds) opens the band.
pub const DWELL_S: f64 = 0.35;
/// Logical px: this close to the top row is touching the edge, as free_pointer.EDGE_PX.
pub const TOUCH_PX: f64 = 0.5;
/// Logical px: the rest survives this far down, past EdgePush's NUDGE_PX (12).
pub const ZONE_PX: i64 = 14;
/// Logical px: a hand this steady is resting; more restarts the rest, so sliding along the edge never opens the band.
pub const STILL_PX: f64 = 2.0;
/// Logical px: where the app gets the cursor back, under the zone, so coming back does not open it again.
pub const LAND_PX: i64 = 24;
/// Canvas px: this far into the picture is back on the picture.
pub const RETURN_PX: i64 = 8;
/// The band shuts by itself after this long (seconds) with no hand motion.
pub const IDLE_S: i64 = 10;
/// A press with no release after this long (seconds) was lost.
pub const HELD_MAX_S: i64 = 30;
// Mirrors spike/include/xr/options_geometry.hpp; test_restore_band compares them.
pub const STEP_SCALE: f64 = 0.84;
pub const STEP_MS: i64 = 220;
pub const BAND_MIN: i64 = 46;
pub const BAND_GAP: i64 = 12;

/// `Dwell`: pressed against the top edge and resting. [`Dwell::step`] takes each cursor sample and says when the rest
/// is up.
#[derive(Clone, Debug, PartialEq)]
pub struct Dwell {
    pub touched: bool,
    pub anchor: Option<(i64, i64)>,
    pub since: Option<f64>,
    pub armed: bool,
}

impl Default for Dwell {
    fn default() -> Dwell {
        Dwell::new()
    }
}

impl Dwell {
    pub fn new() -> Dwell {
        Dwell { touched: false, anchor: None, since: None, armed: true }
    }

    /// Nothing has been touched.
    pub fn reset(&mut self) {
        (self.touched, self.anchor, self.since) = (false, None, None);
    }

    /// The rest starts over, as after a click: the touch stands.
    pub fn restart(&mut self) {
        (self.anchor, self.since) = (None, None);
    }

    /// Not again until the pointer has left the zone.
    pub fn disarm(&mut self) {
        self.reset();
        self.armed = false;
    }

    /// One cursor sample (logical px) on `output` at time `now` (seconds); `blocked`: a click or a press, which starts
    /// the rest over. True when the rest is up.
    pub fn step(&mut self, cursor: Option<(i64, i64)>, output: Option<&OutputRect>, blocked: bool, now: f64) -> bool {
        let (Some(c), Some(o)) = (cursor, output) else {
            self.reset();
            return false;
        };
        let (x, y) = (c.0 as f64, c.1 as f64);
        let inside = o.x as f64 <= x && x < o.x as f64 + o.width && o.y as f64 <= y && y < o.y as f64 + o.height;
        if !inside {
            self.reset();
            return false;
        }
        let down = c.1 - o.y;
        if down > ZONE_PX {
            self.reset();
            self.armed = true;
            return false;
        }
        if !self.armed {
            return false;
        }
        if down as f64 <= TOUCH_PX {
            self.touched = true;
        }
        if !self.touched {
            return false;
        }
        if blocked {
            self.restart();
            return false;
        }
        let (Some(anchor), Some(since)) = (self.anchor, self.since) else {
            (self.anchor, self.since) = (Some(c), Some(now));
            return false;
        };
        if py_hypot((c.0 - anchor.0) as f64, (c.1 - anchor.1) as f64) > STILL_PX {
            (self.anchor, self.since) = (Some(c), Some(now));
            return false;
        }
        now - since >= DWELL_S - 1e-9
    }
}
