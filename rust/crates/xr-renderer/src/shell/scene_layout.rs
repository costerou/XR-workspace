//! The overview scene's layout: per-desktop placements and their glide, the theme accent, the edge pan, the run's
//! desktops changing and the tile ease (wayland.cpp set_placements, set_accent, set_edge_pan, set_desktops,
//! ease_tiles; `view.rs` holds the ported logic). Not drawn in this build.

use xr_wire::control::{EdgePanMove, Op};

use super::Presentation;
use super::roll::RollState;
use super::scene::Scene;

#[derive(Default)]
pub(super) struct SceneLayout {}

impl SceneLayout {
    /// `scene.own` (placements, eased), `scene.tiles` (a tile ease), `scene.accent`, the pills of `scene.glass()`.
    pub(super) fn contribute(&mut self, _scene: &mut Scene) {}

    pub(super) fn animating(&self, _now_ms: f64) -> bool {
        false
    }

    /// The exit report's `theme` section.
    pub(super) fn report_theme(&self, _out: &mut String) {}

    /// The exit report's `view` section (its `roll_updates` from `roll`).
    pub(super) fn report_view(&self, _out: &mut String, _roll: &RollState) {}
}

impl Presentation {
    /// Per-desktop placement in capture order, each [x, y, distance, curvature] in wire units; `glide_ms` > 0 glides.
    pub fn set_placements(&mut self, _placements: &[[i32; 4]], _glide_ms: u32) {
        self.not_drawn(Op::Placement);
    }

    /// The theme border colour (`style`).
    pub fn set_accent(&mut self, _r: u8, _g: u8, _b: u8) {
        self.not_drawn(Op::Style);
    }

    /// The hand's pan past a maximized desktop's edge; `None`: no pan.
    pub fn set_edge_pan(&mut self, _pan: Option<&EdgePanMove>) {
        self.not_drawn(Op::EdgePan);
    }

    /// The run's managed desktops changed: captures not in `outputs` stop, new ones start, `renew` restart.
    pub fn set_desktops(&mut self, _outputs: &[String], _renew: &[String]) {
        self.not_drawn(Op::Desktops);
    }

    /// The tiles ease to the layout the next set_desktops leads to: call it first.
    pub fn ease_tiles(&mut self) {
        self.not_drawn(Op::Desktops);
    }
}
