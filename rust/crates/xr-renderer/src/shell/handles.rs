//! The overview's corner-resize caps, glass pills (move handles) and the Step-Back Band (wayland.cpp
//! set_resize_handle, set_move_handle, set_restore_band and their lines of the render path; `glass.rs` holds the ported
//! logic).

use std::fmt::Write as _;

use xr_renderer::glass::{GLASS_ACTIVE, GLASS_HOVER, GLASS_IDLE, GlassLevels, RestoreStep};

use super::scene::Scene;
use super::{Presentation, monotonic_ms_f64};

/// The corner under the dot or held (wayland.cpp `Impl::handle`).
#[derive(Default)]
struct CornerHandle {
    visible: bool,
    desktop: u32,
    corner: u32,
    active: bool,
    shown: u64,
}

/// The pill under the dot or dragged, and its gear (wayland.cpp `Impl::pills`).
#[derive(Default)]
struct MoveHandle {
    visible: bool,
    desktop: u32,
    hover: bool,
    active: bool,
    gear: bool,
    shown: u64,
}

#[derive(Default)]
pub(super) struct Handles {
    handle: CornerHandle,
    pills: MoveHandle,
    pill_levels: GlassLevels,
    gear_levels: GlassLevels,
    restore_step: RestoreStep,
    restore_updates: u64,
    restore_full_frames: u64,
    /// The last frame's zoom was the overview's (< 0.01): the levels' ease draws frames only there (wayland.cpp
    /// frame_done). The zoom moves only while the view animates, which draws frames by itself.
    zoomed_out: bool,
}

impl Handles {
    /// `scene.glass()`: cap hover / corner / active, pills, pill and gear levels, `restore_alpha`.
    pub(super) fn contribute(&mut self, scene: &mut Scene) {
        let now = scene.now_ms;
        let overview = scene.overview;
        let restore_alpha = if scene.zoom >= 0.99 && !scene.switching {
            self.restore_step.value(now)
        } else {
            0.0
        };
        self.zoomed_out = scene.zoom < 0.01;
        let glass = &mut scene.glass().frame;
        glass.restore_alpha = restore_alpha;
        if glass.restore_alpha >= 1.0 {
            self.restore_full_frames += 1;
        }
        let p = &self.pills;
        glass.pills = p.visible && overview;
        // The pill under the dot or dragged, and the gear under the dot (the supervisor's move_handle gear flag), ease
        // to their state; every other one eases back to idle.
        for i in 0..glass.pill_levels.len() {
            let mine = overview && p.visible && p.desktop as usize == i;
            let pill = if mine && p.active {
                GLASS_ACTIVE
            } else if mine && p.hover {
                GLASS_HOVER
            } else {
                GLASS_IDLE
            };
            self.pill_levels.set(i, pill, now);
            self.gear_levels
                .set(i, if mine && p.gear { GLASS_HOVER } else { GLASS_IDLE }, now);
            glass.pill_levels[i] = self.pill_levels.level(i, now);
            glass.gear_levels[i] = self.gear_levels.level(i, now);
        }
        // The corner under the dot or held: overview only (any zoom hides the caps, so it never lands on a zooming
        // desktop).
        let h = &self.handle;
        if h.visible && overview {
            glass.cap_hover = h.desktop as i32;
            glass.cap_corner = h.corner;
            glass.cap_active = h.active;
        }
    }

    pub(super) fn animating(&self, now_ms: f64) -> bool {
        ((self.pill_levels.animating(now_ms) || self.gear_levels.animating(now_ms)) && self.zoomed_out)
            || self.restore_step.animating(now_ms)
    }

    /// The Step-Back Band's ease, for the maximized desktop's step back (wayland.cpp resolved_placements).
    #[allow(dead_code)] // read by the layout's resolved placements once they are wired
    pub(super) fn restore_step(&self) -> &RestoreStep {
        &self.restore_step
    }

    /// The exit report's `restore_band` section.
    pub(super) fn report_restore_band(&self, out: &mut String) {
        let value_milli = (self.restore_step.value(monotonic_ms_f64()) * 1000.0).round() as i32;
        let _ = write!(
            out,
            ",\"restore_band\":{{\"updates\":{},\"full_frames\":{},\"value_milli\":{value_milli}}}",
            self.restore_updates, self.restore_full_frames
        );
    }

    /// The exit report's `resize_handle` and `move_handle` sections.
    pub(super) fn report_handles(&self, out: &mut String) {
        let _ = write!(
            out,
            ",\"resize_handle\":{{\"shown\":{}}},\"move_handle\":{{\"shown\":{}}}",
            self.handle.shown, self.pills.shown
        );
    }
}

impl Presentation {
    /// The cap on `corner` (0 tl, 1 tr, 2 bl, 3 br) of `desktop` lights while `visible`, brightest while `active`.
    pub fn set_resize_handle(&mut self, visible: bool, desktop: u32, corner: u32, active: bool) {
        let h = &mut self.state.features.handles.handle;
        if visible && !h.visible {
            h.shown += 1;
        }
        (h.visible, h.desktop, h.corner, h.active) = (visible, desktop, corner, active);
        self.state.capture.invalidate();
    }

    /// The glass pill under `desktop`: brighter on `hover`, brightest when `active`; `gear`: its gear is under the dot.
    pub fn set_move_handle(&mut self, visible: bool, desktop: u32, hover: bool, active: bool, gear: bool) {
        let p = &mut self.state.features.handles.pills;
        if visible && !p.visible {
            p.shown += 1;
        }
        (p.visible, p.desktop, p.hover, p.active, p.gear) = (visible, desktop, hover, active, gear);
        self.state.capture.invalidate();
    }

    /// The Step-Back Band, open or closed.
    pub fn set_restore_band(&mut self, open: bool) {
        let handles = &mut self.state.features.handles;
        handles.restore_step.set(open, monotonic_ms_f64());
        handles.restore_updates += 1;
        self.state.capture.invalidate();
    }
}
