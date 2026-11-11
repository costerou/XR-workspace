//! The head-locked cards: the keyboard help card and its geometry line, the key bind panel, and the mode pop-ups of
//! the `view` op (wayland.cpp set_help, take_help_geometry, resend_help_geometry, set_binds and set_view's pop-ups;
//! `hud.rs`, `hud_font.rs`, `binds_panel.rs` hold the ported rasters). Not drawn in this build.

use xr_wire::control::{Binds, Op, View};

use super::Presentation;
use super::scene::Scene;

/// A moving head re-sends the help geometry at most this often (Presentation::kHelpGeometryMs).
#[allow(dead_code)] // the cards feature's first user wires it
pub const HELP_GEOMETRY_MS: f64 = 40.0;

#[derive(Default)]
pub(super) struct Cards {}

impl Cards {
    /// `scene.popup`, `scene.help`, `scene.binds`.
    pub(super) fn contribute(&mut self, _scene: &mut Scene) {}

    pub(super) fn animating(&self, _now_ms: f64) -> bool {
        false
    }

    /// The exit report's `help` and `binds` sections.
    pub(super) fn report_help_binds(&self, _out: &mut String) {}

    /// The exit report's `popup_font`.
    pub(super) fn report_popup_font(&self, _out: &mut String) {}
}

impl Presentation {
    /// Rows are (keys, action), `sections` their section sizes (empty: one), `turn` -1 / +1 flips a shown card's page.
    pub fn set_help(&mut self, _visible: bool, _rows: &[(String, String)], _sections: &[u32], _turn: i32) {
        self.not_drawn(Op::Help);
    }

    /// `{"help_geometry":...}` when it changed, at most every HELP_GEOMETRY_MS while the head moves.
    pub fn take_help_geometry(&mut self, _now_ms: f64) -> Option<String> {
        None
    }

    /// The last take was not delivered.
    pub fn resend_help_geometry(&mut self) {}

    /// The key bind panel (the wire `binds` payload is the C++ BindPanelState).
    pub fn set_binds(&mut self, _state: &Binds) {
        self.not_drawn(Op::Binds);
    }

    /// The pop-up half of the `view` op: viewing mode (first call, on change), cursor follow, prediction, typing.
    /// Silent until wired.
    pub(super) fn view_popups(&mut self, _v: &View) {}
}
