//! Level bars and the levelling roll, and the roll geometry line for the supervisor's roll-level icons (wayland.cpp
//! set_roll, take_roll_geometry, resend_roll_geometry). Not drawn in this build.

use xr_wire::control::Op;

use super::Presentation;
use super::scene::Scene;

/// At most this often a changed roll geometry line goes out (Presentation::kRollGeometryMs).
#[allow(dead_code)] // the roll feature's first user wires it
pub const ROLL_GEOMETRY_MS: f64 = 100.0;

#[derive(Default)]
pub(super) struct RollState {}

impl RollState {
    /// Each desktop's roll on `scene.own[i].roll`; the bars, hover, segment and active of `scene.glass()`.
    pub(super) fn contribute(&mut self, _scene: &mut Scene) {}

    pub(super) fn animating(&self, _now_ms: f64) -> bool {
        false
    }

    /// How many `roll` ops were applied (the exit report's `view.roll_updates`).
    pub(super) fn updates(&self) -> u64 {
        0
    }
}

impl Presentation {
    /// `roll` each desktop's levelling roll (1/100 degree, counter-clockwise); `bars` whose level bars show; `hover` /
    /// `side` the lit segment (-1: none; side -1 none, 0 ccw, 1 level, 2 cw), brightest while `active`.
    pub fn set_roll(&mut self, _roll: &[i32; 4], _bars: &[bool; 4], _hover: i32, _side: i8, _active: bool) {
        self.not_drawn(Op::Roll);
    }

    /// `{"roll_geometry":{"tilt":[...]}}` when it changed, at most every ROLL_GEOMETRY_MS.
    pub fn take_roll_geometry(&mut self, _now_ms: f64) -> Option<String> {
        None
    }

    /// The last take was not delivered.
    pub fn resend_roll_geometry(&mut self) {}
}
