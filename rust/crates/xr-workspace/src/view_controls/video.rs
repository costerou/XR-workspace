//! VR video in a run: `video.VideoControl` (tools/native_spike/video.py), as `managed.view_controls` builds it
//! (`VideoControl(control, binds=video_binds, notify=notify_send)`, `control.options_video = video`; `video_binds`:
//! `client.install_video_binds` / `remove_video_binds` while a video plays) and closes it (`video.close()`, after the
//! desktops, before the sender), with the Supervisor's `attach_supervisor` (the measured status it reads).
//!
//! `VideoControl` wraps the view: its `on_message` takes the video ops and hands the rest to the view
//! ([`super::ViewControls::on_message`] calls [`Video::on_message`] first). A video's file travels with its frame
//! (`video.FdFrame`, SCM_RIGHTS on the renderer's socket: [`super::Frames::fds`]).
//!
//! The requests, payloads, file checks, probe and web video are ported (`crate::supervisor::{video, web_video,
//! quality}`). What is left is the control around them.
//!
//! Not ported: [`Video::open`] gives `None`; the video ops reach the view, which drops them.

use xr_wire::run_control::Frame;

use super::view::ViewControl;
use super::{Frames, Run};

/// `video.VideoControl`.
pub struct Video {
    _private: (),
}

impl Video {
    /// `VideoControl(control, binds=..., notify=notify_send)`.
    pub fn open(_run: &mut Run<'_>) -> Option<Video> {
        None
    }

    /// `VideoControl.on_message`: true when the op was a video op (taken here), false to hand it to the view.
    pub fn on_message(
        &mut self,
        _message: &Frame,
        _view: Option<&mut ViewControl>,
        _out: &mut Frames,
    ) -> bool {
        false
    }

    /// The measured status a playing video reads (`attach_supervisor`: `supervisor.status`).
    pub fn status(&mut self, _datagram: &[u8]) {}

    /// `video.close()`: the video keys removed if a video was playing; none added after this.
    pub fn close(&mut self, _run: &mut Run<'_>) {}
}
