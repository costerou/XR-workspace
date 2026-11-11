//! The overview's corner-resize caps, glass pills (move handles) and the Step-Back Band (wayland.cpp
//! set_resize_handle, set_move_handle, set_restore_band; `glass.rs` holds the ported logic). Not drawn in this build.

use xr_wire::control::Op;

use super::Presentation;
use super::scene::Scene;

#[derive(Default)]
pub(super) struct Handles {}

impl Handles {
    /// `scene.glass()`: cap hover / corner / active, pill and gear levels, `restore_alpha`.
    pub(super) fn contribute(&mut self, _scene: &mut Scene) {}

    pub(super) fn animating(&self, _now_ms: f64) -> bool {
        false
    }

    /// The exit report's `restore_band` section.
    pub(super) fn report_restore_band(&self, _out: &mut String) {}

    /// The exit report's `resize_handle` and `move_handle` sections.
    pub(super) fn report_handles(&self, _out: &mut String) {}
}

impl Presentation {
    /// The cap on `corner` (0 tl, 1 tr, 2 bl, 3 br) of `desktop` lights while `visible`, brightest while `active`.
    pub fn set_resize_handle(&mut self, _visible: bool, _desktop: u32, _corner: u32, _active: bool) {
        self.not_drawn(Op::ResizeHandle);
    }

    /// The glass pill under `desktop`: brighter on `hover`, brightest when `active`; `gear`: its gear is under the dot.
    pub fn set_move_handle(&mut self, _visible: bool, _desktop: u32, _hover: bool, _active: bool, _gear: bool) {
        self.not_drawn(Op::MoveHandle);
    }

    /// The Step-Back Band, open or closed.
    pub fn set_restore_band(&mut self, _open: bool) {
        self.not_drawn(Op::RestoreBand);
    }
}
