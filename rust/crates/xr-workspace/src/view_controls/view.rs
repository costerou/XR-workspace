//! `view.ViewControl` (tools/native_spike/view.py from `class ViewControl`): the run's view state on the loop, the
//! dispatcher of every user op, the renderer frames, the status reply, the timed work (`poll`), the Hyprland events,
//! the options menu, and the setup saved for the next start. With it from tools/native_spike/managed.py: the saved
//! layout's load (`store.load()`), `_restored`, `_initial_placements` and the event stream (`client.event_stream`);
//! from tools/native_spike/supervisor.py the status datagrams it takes (`Supervisor.status_bytes`: `control_applied`,
//! `control_refused`, `owner_readback`, `maximize_geometry`, `overview_geometry`, `roll_geometry`, `help_geometry`,
//! and the measured status for `status_reply`) and `_events_ready`; and `options_text.RuntimeText`.
//!
//! The pure parts are ported: `crate::supervisor::view` (`ViewState`, the frame encoders, `layout.json`), `environment`,
//! `options_geometry`, `options_text`. [`ViewControl::on_message`] routes the other features' ops already, in the
//! Python's order; the view's own arms are marked where they go.
//!
//! Not ported: [`ViewControl::open`] gives `None`; no op is taken and no frame sent.

use std::os::fd::RawFd;
use std::time::Instant;

use xr_wire::run_control::Frame;

use super::overview_pointer::Pointer;
use super::{Features, Frames, Run};

/// `view.ViewControl`.
pub struct ViewControl {
    /// `state.names` and `state.selected`: the desktop the send keys act on.
    names: Vec<String>,
    selected: usize,
    /// `state.typing`: typing mode ignores `IGNORED_WHILE_TYPING`.
    typing: bool,
    /// `state.immersive`, `next_id` and `last_input`: what the 3D methods take ([`super::immersive::Shown`]).
    immersive: crate::supervisor::immersive::Setting,
    next_id: i64,
    last_input: f64,
}

pub use crate::lua::resize::{CANCEL_OP as RESIZE_CANCEL_OP, RESIZE_OPS};
pub use crate::supervisor::privacy::PRIVACY_OP;
/// `view.IMMERSIVE_OP` and `IMMERSIVE_SET_OP`.
pub const IMMERSIVE_OP: &str = "immersive_toggle";
pub const IMMERSIVE_SET_OP: &str = "immersive";
/// `view.BINDS_PANEL_OP` and the key bind panel's key (`keybinds.OPS`).
pub const BINDS_PANEL_OP: &str = "binds_toggle";
pub const BINDS_KEY_OP: &str = "binds_key";
/// `view.RESET_OP`: Ctrl+Alt+R.
pub const RESET_OP: &str = "layout_reset";
/// `view.ENVIRONMENT_OP` and `view.PLAYBACK_MENU_OP`.
pub const ENVIRONMENT_OP: &str = "environment_toggle";
pub const PLAYBACK_MENU_OP: &str = "playback_menu_toggle";

/// `view.IGNORED_WHILE_TYPING`: MENU_OPS + SEND_OPS + privacy, the playback menu, the key bind panel, the background
/// and 3D toggles.
pub fn ignored_while_typing(op: &str) -> bool {
    crate::supervisor::view::MENU_OPS.contains(&op)
        || crate::lua::send::SEND_OPS.contains(&op)
        || [
            PRIVACY_OP,
            PLAYBACK_MENU_OP,
            BINDS_PANEL_OP,
            BINDS_KEY_OP,
            ENVIRONMENT_OP,
            IMMERSIVE_OP,
        ]
        .contains(&op)
}

impl ViewControl {
    /// `view.ViewControl(view.ViewState(capture, ...saved...), ...)`, the saved placements and rolls first
    /// (`_restored`, `_initial_placements`).
    pub fn open(_run: &Run<'_>) -> Option<ViewControl> {
        None
    }

    /// A view on `names` with the first selected, for the routing tests (and the port's start).
    pub fn with_names(names: Vec<String>) -> ViewControl {
        ViewControl {
            names,
            selected: 0,
            typing: false,
            immersive: Default::default(),
            next_id: 1,
            last_input: 0.0,
        }
    }

    /// `control.events = client.event_stream()`; none on an error.
    pub fn open_events(&mut self, _run: &mut Run<'_>) {}

    /// The event stream, for the loop's poll.
    pub fn events_fd(&self) -> Option<RawFd> {
        None
    }

    /// `Supervisor._events_ready` and `ViewControl.on_events`: false at the stream's end.
    pub fn events_ready(&mut self, _features: &mut Features<'_>, _out: &mut Frames) -> bool {
        true
    }

    /// `events.close()`.
    pub fn close_events(&mut self) {}

    /// `control.options_key_text = RuntimeText(control, client)` and `control.options_binds`.
    pub fn open_options(&mut self, _run: &mut Run<'_>) {}

    /// The `finally`'s first block: the options menu closed (`stop`), its binds removed, the maximize geometry
    /// cleared on the pointer.
    pub fn close_options(&mut self, _run: &mut Run<'_>, _pointer: Option<&mut Pointer>) {}

    /// `ViewControl.on_message`, in its order. Each arm that is another feature's calls that feature.
    pub fn on_message(&mut self, message: &Frame, f: &mut Features<'_>, out: &mut Frames) {
        let op = message.op.as_str();
        // View: options_scroll, options_*, binds_key while the options menu owns the keyboard, menu_back,
        // last_input, stabilization, placement.
        if op == RESIZE_CANCEL_OP {
            if let Some(pointer) = f.pointer.as_deref_mut() {
                pointer.super_down(out);
            }
            return;
        }
        // View: TYPING_OP.
        if self.typing && ignored_while_typing(op) {
            return;
        }
        // View: MENU_OPS.
        if op == RESET_OP {
            if let Some(desktops) = f.desktops.as_deref_mut() {
                desktops.layout_reset(out);
            }
            return;
        }
        if RESIZE_OPS.contains(&op) {
            if let Some(pointer) = f.pointer.as_deref_mut() {
                pointer.on_op(op, out);
            }
            return;
        }
        if crate::lua::view::SUPER_OPS.contains(&op) {
            if op == crate::lua::view::SUPER_OPS[2]
                && let Some(windows) = f.windows.as_deref_mut()
            {
                windows.mark(); // a drag inside a workspace sends Hyprland no event
            }
            if let Some(pointer) = f.pointer.as_deref_mut() {
                pointer.on_super(op, out);
            }
            return;
        }
        if crate::lua::send::SEND_OPS.contains(&op) {
            if let (Some(sender), Some(name)) =
                (f.send.as_deref_mut(), self.names.get(self.selected))
            {
                sender.request(op, name);
            }
            return;
        }
        // View: ENVIRONMENT_OP, ENVIRONMENT_SET_OP.
        if op == IMMERSIVE_OP || op == IMMERSIVE_SET_OP {
            if let Some(immersive) = f.immersive.as_deref_mut() {
                let mut shown = super::immersive::Shown {
                    setting: &mut self.immersive,
                    names: &self.names,
                    video_active: false,
                    zoomed: false,
                    typing: self.typing,
                    next_id: &mut self.next_id,
                    now: 0.0,
                    last_input: &mut self.last_input,
                    changed: false,
                };
                immersive.on_message(message, &mut shown, out);
            }
            return;
        }
        if op == PRIVACY_OP {
            if let Some(privacy) = f.privacy.as_deref_mut() {
                privacy.request();
            }
            return;
        }
        // View: HELP_PAGE_OPS.
        if op == BINDS_PANEL_OP || op == BINDS_KEY_OP {
            if let Some(keys) = f.keys.as_deref_mut() {
                keys.on_message(message, self, out);
            }
            return;
        }
        // View: USER_OPS (help, recenter, zoom, next / prev, spacing, prediction, mode_next).
    }

    /// `ViewControl.initial_frame()`: the saved spacing, the overview.
    pub fn initial_frame(&mut self) -> Vec<u8> {
        Vec::new()
    }

    /// `ViewControl.status_reply(status)`: the `status` answer's body (`StatusReply`). `held`: asked during a hold
    /// (`serve_without_renderer`), whose `status` is `{}` rather than the renderer's last measured one.
    pub fn status_reply(&self, _held: bool) -> Vec<(String, xr_wire::json::Value)> {
        Vec::new()
    }

    /// The view's side of `Supervisor.status_bytes`: receipts, refusals, readbacks, geometry, the measured status.
    pub fn status(&mut self, _datagram: &[u8], _features: &mut Features<'_>) {}

    /// When [`ViewControl::poll`] is next due (the pointer's timers, the save delay, the idle close, the reset).
    pub fn deadline(&mut self, _pointer: Option<&mut Pointer>) -> Option<Instant> {
        None
    }

    /// `ViewControl.poll(now)`.
    pub fn poll(&mut self, _now: Instant, _features: &mut Features<'_>, _out: &mut Frames) {}

    /// `ViewControl.renderer_restarted()`: the renderer after a hold gets the view again.
    pub fn renderer_restarted(&mut self) {}

    /// `control.flush()`: a change still inside its save delay.
    pub fn flush(&mut self) {}

    /// `control.save_setup()`: the monitor setup for the next start (profiles.py).
    pub fn save_setup(&mut self) {}
}

/// What the key bind panel needs from the view (`keybinds.BindControl`'s `control`). A stub until the view is ported:
/// no typing mode, card or menu, and no frame ids of its own.
impl crate::supervisor::keybinds::Host for ViewControl {
    fn now(&self) -> f64 {
        0.0
    }
    fn typing(&self) -> bool {
        self.typing
    }
    fn video_active(&self) -> bool {
        false
    }
    fn help_visible(&self) -> bool {
        false
    }
    fn close_help(&mut self) -> Vec<u8> {
        Vec::new()
    }
    fn menu_open(&self) -> bool {
        false
    }
    fn close_menu(&mut self) -> Vec<u8> {
        Vec::new()
    }
    fn keyboard_owned(&self) -> bool {
        false
    }
    fn run_id(&self) -> &str {
        ""
    }
    fn session(&self) -> &str {
        ""
    }
    fn next_request_id(&mut self) -> u64 {
        0
    }
    fn set_last_input(&mut self, _now: f64) {}
    fn binds_reinstalled(&mut self, _typing: bool, _menu_binds: bool, _options_binds: bool) {}
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::view_controls::send::Sender;

    fn frame(op: &str) -> Frame {
        Frame {
            request_id: 2,
            run_id: "r1".into(),
            session: "s".into(),
            op: op.into(),
            payload: xr_wire::json::Value::Object(Vec::new()),
        }
    }

    fn none<'a>() -> Features<'a> {
        Features {
            pointer: None,
            send: None,
            privacy: None,
            desktops: None,
            immersive: None,
            windows: None,
            keys: None,
        }
    }

    #[test]
    fn a_feature_that_is_not_there_takes_nothing_and_sends_nothing() {
        let mut view = ViewControl::with_names(vec!["XR-1".into()]);
        let mut out = Frames::default();
        for op in [
            "send_glasses",
            PRIVACY_OP,
            IMMERSIVE_OP,
            BINDS_PANEL_OP,
            RESET_OP,
            "resize_press",
            "zoom_in",
        ] {
            view.on_message(&frame(op), &mut none(), &mut out);
        }
        assert!(out.bytes.is_empty() && out.fds.is_empty());
    }

    #[test]
    fn the_send_keys_reach_the_sender_with_the_selected_desktop() {
        let mut view = ViewControl::with_names(vec!["XR-1".into(), "XR-2".into()]);
        view.selected = 1;
        let mut sender = Sender::recording();
        let mut out = Frames::default();
        let mut features = Features {
            send: Some(&mut sender),
            ..none()
        };
        view.on_message(
            &frame(crate::lua::send::SEND_OPS[0]),
            &mut features,
            &mut out,
        );
        view.typing = true; // typing mode ignores them
        view.on_message(
            &frame(crate::lua::send::SEND_OPS[0]),
            &mut features,
            &mut out,
        );
        assert_eq!(
            sender.requests(),
            [(crate::lua::send::SEND_OPS[0].to_owned(), "XR-2".to_owned())]
        );
        assert!(out.bytes.is_empty());
    }
}
