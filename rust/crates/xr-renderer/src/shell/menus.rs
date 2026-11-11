//! The options panel and the placement panel, both opening from the overview's glass (wayland.cpp set_options_menu,
//! set_placement_menu; `glass.rs`, `glass_raster.rs`, `options_geometry.rs` hold the ported logic). Not drawn in this
//! build.

use xr_wire::control::{Op, OptionsMenu, PlacementMenu};

use super::Presentation;
use super::scene::Scene;

#[derive(Default)]
pub(super) struct Menus {}

impl Menus {
    /// `scene.glass()`: expand, content, top, surface, options fields and the panels' images.
    pub(super) fn contribute(&mut self, _scene: &mut Scene) {}

    pub(super) fn animating(&self, _now_ms: f64) -> bool {
        false
    }

    /// The exit report's `placement_menu` and `options_menu` sections.
    pub(super) fn report(&self, _out: &mut String) {}
}

impl Presentation {
    /// The options panel (the wire `options_menu` payload is the C++ OptionsMenuState).
    pub fn set_options_menu(&mut self, _state: &OptionsMenu) {
        self.not_drawn(Op::OptionsMenu);
    }

    /// The placement panel. The wire payload carries what spike/src/main.cpp splits into PlacementMenuState and the
    /// held slider's `surface`; the conversion to `hud::PlacementMenuState` belongs here.
    pub fn set_placement_menu(&mut self, _menu: &PlacementMenu) {
        self.not_drawn(Op::PlacementMenu);
    }
}
