//! The overview free pointer and a window drag's ghost (wayland.cpp set_overview_pointer and the render path's
//! set_free_pointer / set_window_drag; `view.rs` PointerLook and the pointer constants). Not drawn in this build.

use xr_renderer::view::PointerLook;
use xr_wire::control::Op;

use super::Presentation;
use super::scene::Scene;

#[derive(Default)]
pub(super) struct Pointer {}

impl Pointer {
    /// `scene.pointer` and `scene.drag`.
    pub(super) fn contribute(&mut self, _scene: &mut Scene) {}

    pub(super) fn animating(&self, _now_ms: f64) -> bool {
        false
    }

    /// The exit report's `overview_pointer` section.
    pub(super) fn report(&self, _out: &mut String) {}
}

impl Presentation {
    /// A dot at canvas (x, y), brighter while `held`; `directed`: at (az, el) in 1/100 degree instead. `ghost_w` /
    /// `ghost_h` > 0: a window drag's ghost, `target` >= 0 the desktop it would land on. `look`: the marks round it.
    #[allow(clippy::too_many_arguments)]
    pub fn set_overview_pointer(
        &mut self,
        _visible: bool,
        _x: i32,
        _y: i32,
        _held: bool,
        _directed: bool,
        _az: i32,
        _el: i32,
        _ghost_w: i32,
        _ghost_h: i32,
        _target: i32,
        _look: PointerLook,
    ) {
        self.not_drawn(Op::OverviewPointer);
    }
}
