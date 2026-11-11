//! Head tracking: the pose feed, recentre, the viewing mode, prediction and stabilization, and the camera they give
//! the frame (wayland.cpp's tracked render path; `pose.rs`, `predict.rs`, `stabilization.rs`, `motion_smooth.rs`,
//! `motion_feed.rs` hold the ported logic). Not drawn in this build: `--pose-feed` is refused at launch.

use xr_renderer::ipc::StatusExtras;
use xr_wire::control::{Op, View};

use super::Presentation;
use super::scene::Scene;

/// Whether this build takes `--pose-feed xrld`. False: the launch exits 2, as before the feature was wired.
pub fn pose_feed_supported() -> bool {
    false
}

/// The XRLinuxDriver feed's fixed prefix (spike/src/main.cpp).
pub const POSE_PREFIX: &str = "/tmp/shader_runtime_";

#[derive(Default)]
pub(super) struct Tracking {}

impl Tracking {
    /// The camera: `scene.head`, `scene.world`, `scene.environment_world`.
    pub(super) fn contribute(&mut self, _scene: &mut Scene) {}

    pub(super) fn animating(&self, _now_ms: f64) -> bool {
        false
    }

    /// `tracking` (only when tracking was asked for) and `pose_age_ms`.
    pub(super) fn status(&self, _extras: &mut StatusExtras) {}

    /// The exit report's `tracking` section.
    pub(super) fn report_tracking(&self, _out: &mut String) {}

    /// The exit report's `latency` section (`read_to_present`, `pose_age_*`, prediction, late latch, vsync).
    pub(super) fn report_latency(&self, _out: &mut String) {}
}

impl Presentation {
    /// World-anchored 3DoF from the pose feed at `prefix` (GPU composition only). `smooth_ms`: motion_smooth, 0 = off.
    pub fn enable_tracking(
        &mut self,
        _prefix: &str,
        _supervised_projection: f32,
        _smooth_ms: f32,
        _level_gravity: bool,
    ) {
        eprintln!("tracking: not drawn in this build");
    }

    pub fn recenter(&mut self) {
        self.not_drawn(Op::Recenter);
    }

    /// The tracking half of the `view` op: mode, cursor follow, prediction, typing, stabilization. Silent until wired.
    pub(super) fn view_tracking(&mut self, _v: &View) {}
}
