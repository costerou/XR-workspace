//! Immersive 3D: the 3D buttons, the stereo pass and the depth engine's shared memory (wayland.cpp set_immersive,
//! set_depth_fd, depth_tick; `stereo.rs`, `stereo_gl.rs`, `depth.rs`, `depth_feed.rs`, `depth_link.rs` hold the
//! ported logic). Not drawn in this build: `--depth-fd` is refused at launch.

use std::os::fd::OwnedFd;

use xr_renderer::gl::GlCompositor;
use xr_wire::control::{ImmersiveParams, Op};

use super::Presentation;
use super::scene::Scene;

/// Whether this build takes `--depth-fd`. False: the launch exits 2, as before the feature was wired.
pub fn depth_fd_supported() -> bool {
    false
}

#[derive(Default)]
pub(super) struct ImmersiveState {}

impl ImmersiveState {
    /// The 3D buttons of `scene.glass()` and `scene.stereo`.
    pub(super) fn contribute(&mut self, _scene: &mut Scene) {}

    /// Before the pose is read (C++ depth_tick): the depth frames into `GlCompositor::set_depth` / `keep_depth`.
    pub(super) fn before_draw(&mut self, _gl: &mut GlCompositor) {}

    pub(super) fn animating(&self, _now_ms: f64) -> bool {
        false
    }

    /// The exit report's `depth` section (after `composition`).
    pub(super) fn report(&self, _out: &mut String) {}
}

impl Presentation {
    pub fn set_immersive(
        &mut self,
        _desktops: &[bool; 4],
        _global: bool,
        _stereo: bool,
        _hover: i32,
        _tail: bool,
        _params: &ImmersiveParams,
    ) {
        self.not_drawn(Op::Immersive);
    }

    /// The depth engine's shared memory (a memfd the supervisor made); it stays open for the run.
    pub fn set_depth_fd(&mut self, fd: OwnedFd) {
        drop(fd);
        eprintln!("depth: not drawn in this build");
    }
}
