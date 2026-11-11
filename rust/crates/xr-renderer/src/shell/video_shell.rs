//! VR video: the player, its controls card and quality menu, drawn instead of the desktops while a video is open
//! (wayland.cpp video_*, the render path's video branch; `video/` holds the ported player, sphere and card). Not
//! drawn in this build: an opened file is closed unread.

use std::os::fd::OwnedFd;

use xr_renderer::gl::GlCompositor;
use xr_renderer::ipc::StatusExtras;
use xr_wire::control::{Op, VideoFilter, VideoFormat};

use super::Presentation;
use super::scene::Scene;

#[derive(Default)]
pub(super) struct VideoShell {}

impl VideoShell {
    /// While a video is open: draw it (and its card) in place of the desktops and swap, Some(drawn ok). None: no
    /// video this frame, the desktops are drawn.
    pub(super) fn draw(&mut self, _gl: &mut GlCompositor, _scene: &Scene) -> Option<bool> {
        None
    }

    pub(super) fn animating(&self, _now_ms: f64) -> bool {
        false
    }

    /// `video` (left out while off).
    pub(super) fn status(&self, _extras: &mut StatusExtras) {}

    /// The exit report's `video` section.
    pub(super) fn report(&self, _out: &mut String) {}

    /// The owner readback's `video` value.
    pub(super) fn owner_readback(&self, line: &mut String) {
        line.push_str("null");
    }
}

impl Presentation {
    /// `fd` is the file the supervisor opened; ownership is taken, also when video is unavailable.
    pub fn video_open(&mut self, fd: OwnedFd, _format: &VideoFormat, _title: &str) {
        drop(fd);
        eprintln!("control: video open: no player in this build, file closed");
    }

    pub fn video_close(&mut self) {
        self.not_drawn(Op::Video);
    }

    pub fn video_pause(&mut self) {
        self.not_drawn(Op::Video);
    }

    pub fn video_seek(&mut self, _seconds: i32) {
        self.not_drawn(Op::Video);
    }

    pub fn video_format(&mut self, _format: &VideoFormat) {
        self.not_drawn(Op::Video);
    }

    pub fn video_controls(&mut self, _visible: bool) {
        self.not_drawn(Op::Video);
    }

    /// Quality settings, applied live and kept for later videos (the C++ VideoQuality).
    pub fn video_quality(&mut self, _filter: VideoFilter, _deband: bool) {
        self.not_drawn(Op::Video);
    }

    /// The quality menu on the controls card: rows (label, value), `selected` lit.
    pub fn video_menu(&mut self, _visible: bool, _rows: &[(String, String)], _selected: u32) {
        self.not_drawn(Op::Video);
    }
}
