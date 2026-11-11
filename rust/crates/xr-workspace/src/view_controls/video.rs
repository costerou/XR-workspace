//! VR video in a run: `video.VideoControl` (tools/native_spike/video.py), as `managed.view_controls` builds it
//! (`VideoControl(control, binds=video_binds, notify=notify_send)`, `control.options_video = video`; `video_binds`:
//! `client.install_video_binds` / `remove_video_binds` while a video plays) and closes it (`video.close()`, after the
//! desktops, before the sender), with the Supervisor's `attach_supervisor` (the measured status it reads).
//!
//! `VideoControl` wraps the view: its `on_message` takes the video ops and hands the rest to the view
//! ([`super::ViewControls::on_message`] calls [`Video::on_message`] first, and [`Video::after_view`] after the view
//! took one). A video's file travels with its frame (`video.FdFrame`, SCM_RIGHTS on the renderer's socket:
//! [`super::Frames::fds`]); the descriptors of results never sent are closed here.
//!
//! The requests, payloads, file checks, probe and web video are `crate::supervisor::{video, web_video, quality}`.
//! This is the control around them: [`Control`] is `VideoControl`'s state machine, and what it reads and changes on
//! the view comes through [`Host`]. A run without a view (the view is not ported yet) gives the video a [`Detached`]
//! host: the run's ids and frame numbers, no typing mode, cards or panels, and the default quality and background.
//!
//! As in Python:
//! - A file or web video is opened, probed and checked on a worker thread; the result is taken on the loop's poll.
//! - The video keys go in and out off the loop, in order, on a worker with its own compositor client; at close the
//!   work out gets 2.5 s, then the keys are removed if they are up and no change follows.
//! - The deferred owner path (the options menu's `done`: [`Done`]) completes each request once, from what the renderer
//!   reads back (`owner_readback`'s `video`), and registers each frame it sends with the owner first.
//!
//! Not ported here: the options menu's own calls (`control.options_video`) are the view's, as is the config reload
//! that reinstalls the keys ([`Video::config_reloaded`], which the view's event stream calls).

use std::os::fd::{OwnedFd, RawFd};
use std::rc::Rc;
use std::sync::mpsc::{self, Receiver, Sender};
use std::sync::{Arc, Mutex};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

use serde_json::{Map, Value};
use xr_wire::run_control::Frame;

use super::view::ViewControl;
use super::{Frames, Run};
use crate::hyprland::Client;
use crate::lua::BindCommand;
use crate::supervisor::environment::{self, Environment};
use crate::supervisor::error::{Error, Result};
use crate::supervisor::keybinds;
use crate::supervisor::quality::{self, MenuRow, Quality};
use crate::supervisor::video::{
    self as v, Format, KEY_OPS, Payload, ProbeInfo, Request, VIDEO_OPS, VIDEO_STATES, seek_seconds,
};
use crate::supervisor::web_video::{self as wv, RelayFactory, WebStream};

/// `video.ERROR_EXIT_S`: a renderer playback error stays on the card this long, then the desktops come back.
pub const ERROR_EXIT_S: f64 = 4.0;
/// `video.START_GRACE_S`: no video state from the renderer this long after open: it cannot play video.
pub const START_GRACE_S: f64 = 5.0;
/// `view.OVERLAY_IDLE_CLOSE_S`.
pub const OVERLAY_IDLE_CLOSE_S: f64 = 30.0;
/// `video.QUALITY_OPS`.
pub const QUALITY_OPS: [&str; 3] = ["video_quality", "video_up", "video_down"];
/// `view.PLAYBACK_MENU_OP` (Ctrl+Alt+C) and its toasts (`view.PLAYBACK_MENU_TOASTS`, hidden first).
pub const PLAYBACK_MENU_OP: &str = "playback_menu_toggle";
pub const PLAYBACK_MENU_TOASTS: [&str; 2] = [
    "VR video menu hidden: it no longer pops up on play or seek. Ctrl+Alt+C shows it again.",
    "VR video menu shown: it pops up on play and seek. Ctrl+Alt+C hides it.",
];
/// `close(timeout=2.5)`.
const CLOSE_TIMEOUT: Duration = Duration::from_millis(2500);
/// The loop's next look while an open is out (its result is taken on a tick) and while a web video plays.
const WORK_TICK: Duration = Duration::from_millis(20);
const STREAM_TICK: Duration = Duration::from_millis(100);
/// `len(self._owner_pending) >= 32`.
const MAX_PENDING: usize = 32;

/// `video.IGNORED_WHILE_PLAYING`: the desktop view keys, the pills' keys, resize, Super drags and window sending.
pub fn ignored_while_playing(op: &str) -> bool {
    [
        "zoom_in",
        "zoom_out",
        "next",
        "prev",
        "spacing_up",
        "spacing_down",
        "mode_next",
    ]
    .contains(&op)
        || crate::supervisor::view::MENU_OPS.contains(&op)
        || crate::lua::resize::RESIZE_OPS.contains(&op)
        || crate::lua::view::SUPER_OPS.contains(&op)
        || crate::lua::send::SEND_OPS.contains(&op)
}

/// What `VideoControl` reads and changes on the view control (`self.view`), besides what the key bind panel does
/// ([`keybinds::Host`]: the clock, typing mode, the help card and placement panel it closes, the frame ids).
pub trait Host: keybinds::Host {
    /// `quality.video_quality(view.state)`.
    fn video_quality(&self) -> Quality;
    /// `view.state.video_quality = settings`.
    fn set_video_quality(&mut self, quality: Quality);
    /// `view.settings.save(view.state.settings())`: the error's text on a failure.
    fn save_settings(&mut self) -> std::result::Result<(), String>;
    /// `view.state.environment` (environment.py's setting).
    fn environment(&self) -> Value;
    /// `view.set_environment(env)`: applied, saved, and its frames.
    fn set_environment(&mut self, env: Environment) -> Vec<u8>;
    /// `view.video_active`: the free pointer is off while a video plays.
    fn set_video_active(&mut self, on: bool);
    /// `view.keys_open`, and `view.keys.close()`'s frames.
    fn keys_open(&self) -> bool;
    fn close_keys(&mut self) -> Vec<u8>;
    /// `view._tell(text)`: a toast (`control.notify`), off the loop.
    fn tell(&mut self, text: &str);
    /// `view.owner_readback`: which readback it is (a new number for each one the renderer sent; 0: none yet) and its
    /// `video` entry.
    fn owner_readback(&self) -> (u64, Option<Value>);
}

/// The deferred owner's callback (`options_actions.owner_callback`'s `done`).
pub trait Done {
    /// `done(success, detail)`.
    fn complete(&self, success: bool, detail: &str);
    /// `done.retain_frames(frames)`: the frames are registered with the owner (true), or the request is stale.
    /// `None`: a caller without the registrar (every frame is fine).
    fn retain_frames(&self, _frames: &[u8]) -> Option<bool> {
        None
    }
}

/// `send.complete`: the detail at most 256 bytes, cut on a character.
fn complete(done: &dyn Done, success: bool, detail: &str) {
    let mut end = detail.len().min(256);
    while !detail.is_char_boundary(end) {
        end -= 1;
    }
    done.complete(success, &detail[..end]);
}

/// The host of a run without a view: the run's ids and frame numbers (`first_request_id=2`), its clock, and none of
/// the view's cards, panels or typing mode. The quality and the background are the defaults and are not saved.
pub struct Detached {
    run_id: String,
    session: String,
    next_id: u64,
    quality: Quality,
    environment: Environment,
    notify: Notify,
}

impl Detached {
    pub fn new(run_id: &str, session: &str, notify: Notify) -> Detached {
        Detached {
            run_id: run_id.into(),
            session: session.into(),
            next_id: 2,
            quality: Quality::default(),
            environment: environment::DEFAULTS,
            notify,
        }
    }
}

impl keybinds::Host for Detached {
    fn now(&self) -> f64 {
        monotonic()
    }
    fn typing(&self) -> bool {
        false
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
        &self.run_id
    }
    fn session(&self) -> &str {
        &self.session
    }
    fn next_request_id(&mut self) -> u64 {
        self.next_id += 1;
        self.next_id - 1
    }
    fn set_last_input(&mut self, _now: f64) {}
    fn binds_reinstalled(&mut self, _typing: bool, _menu_binds: bool, _options_binds: bool) {}
}

impl Host for Detached {
    fn video_quality(&self) -> Quality {
        self.quality
    }
    fn set_video_quality(&mut self, quality: Quality) {
        self.quality = quality;
    }
    fn save_settings(&mut self) -> std::result::Result<(), String> {
        Ok(())
    }
    fn environment(&self) -> Value {
        self.environment.to_json()
    }
    fn set_environment(&mut self, env: Environment) -> Vec<u8> {
        self.environment = env;
        let id = keybinds::Host::next_request_id(self);
        environment::renderer_frame(&self.run_id, &self.session, id, &env.to_json())
            .unwrap_or_default()
    }
    fn set_video_active(&mut self, _on: bool) {}
    fn keys_open(&self) -> bool {
        false
    }
    fn close_keys(&mut self) -> Vec<u8> {
        Vec::new()
    }
    fn tell(&mut self, text: &str) {
        let notify = self.notify.clone();
        let text = text.to_owned();
        let _ = thread::Builder::new()
            .name("xr-video-toast".into())
            .spawn(move || notify(&text));
    }
    fn owner_readback(&self) -> (u64, Option<Value>) {
        (0, None)
    }
}

/// `time.monotonic()`.
fn monotonic() -> f64 {
    let mut ts = libc::timespec {
        tv_sec: 0,
        tv_nsec: 0,
    };
    // SAFETY: a valid clock id and a valid out pointer.
    unsafe { libc::clock_gettime(libc::CLOCK_MONOTONIC, &mut ts) };
    ts.tv_sec as f64 + ts.tv_nsec as f64 / 1e9
}

/// A toast: `notify_send` for the video's own, `send.notify` for the view's.
pub type Notify = Arc<dyn Fn(&str) + Send + Sync>;
/// `probe`: (descriptor) -> what ffprobe says (`probe_fd`).
pub type Probe = Arc<dyn Fn(RawFd) -> Result<Option<ProbeInfo>> + Send + Sync>;
/// `web_probe`: (link, headers, user agent) -> what ffprobe says (`web_video.probe_url`).
pub type WebProbe =
    Arc<dyn Fn(&str, &[(String, String)], Option<&str>) -> Result<Option<ProbeInfo>> + Send + Sync>;
/// `web_resolve`: (page link) -> yt-dlp's info (`web_video.resolve_page`).
pub type Resolve = Arc<dyn Fn(&str) -> Result<Value> + Send + Sync>;
/// `relay_factory`, one for each web video.
pub type Relays = Arc<dyn Fn() -> RelayFactory + Send + Sync>;
/// `binds(on)`: the video keys in or out (a compositor call).
pub type BindFn = Box<dyn FnMut(bool) -> std::result::Result<(), String> + Send>;

/// `video.notify_send`: a desktop notification titled VR video (mako on Omarchy); best effort, never fails.
pub fn notify_send(text: &str) {
    let exe = std::path::Path::new("/usr/bin/notify-send");
    if !crate::supervisor::process::executable(exe) {
        return;
    }
    let text: String = text.chars().take(300).collect();
    let child = std::process::Command::new(exe)
        .args([
            "--app-name=XR Workspace",
            "--expire-time=8000",
            "VR video",
            &text,
        ])
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .spawn();
    let Ok(mut child) = child else { return };
    let end = Instant::now() + Duration::from_secs(5);
    while matches!(child.try_wait(), Ok(None)) {
        if Instant::now() >= end {
            let _ = child.kill();
            let _ = child.wait();
            return;
        }
        thread::sleep(Duration::from_millis(10));
    }
}

/// What the file checks need (`VideoControl(probe=..., web_probe=..., web_resolve=..., relay_factory=...,
/// allow_local=...)`), and whether they run on a thread (`threads`).
#[derive(Clone)]
pub struct Opener {
    pub threads: bool,
    pub probe: Probe,
    pub web_probe: WebProbe,
    pub resolve: Resolve,
    pub relays: Relays,
    pub allow_local: bool,
}

impl Opener {
    /// The run's: ffprobe, yt-dlp and the ffmpeg relay, on a worker thread.
    pub fn real() -> Opener {
        use crate::supervisor::process;
        Opener {
            threads: true,
            probe: Arc::new(|fd| v::probe_fd(fd, None, &process::run)),
            web_probe: Arc::new(|url, headers, ua| {
                wv::probe_url(url, headers, ua, &process::run, None)
            }),
            resolve: Arc::new(|url| wv::resolve_page(url, &process::run, None)),
            relays: Arc::new(wv::stream_relay_factory),
            allow_local: false,
        }
    }
}

/// A worker's result (`self._results`).
enum Outcome {
    Open { opened: Opened, owner: Option<u64> },
    Refused { text: String, owner: Option<u64> },
}

struct Opened {
    fd: OwnedFd,
    title: String,
    format: Format,
    source: &'static str,
    warnings: Vec<String>,
    stream: Option<WebStream>,
}

/// What to open (`_prepare` / `_prepare_web`'s arguments).
enum Target {
    File(String),
    Web(Map<String, Value>),
}

fn prepare(opener: &Opener, target: Target, chosen: Option<Format>, owner: Option<u64>) -> Outcome {
    match target {
        Target::File(path) => match v::open_file(&path, chosen, &|fd| (opener.probe)(fd)) {
            Ok(o) => Outcome::Open {
                opened: Opened {
                    fd: o.fd,
                    title: o.title,
                    format: o.format,
                    source: o.source,
                    warnings: o.warnings,
                    stream: None,
                },
                owner,
            },
            Err(e) => Outcome::Refused {
                text: refusal(&e, "cannot open the video"),
                owner,
            },
        },
        Target::Web(payload) => {
            let probe =
                |u: &str, h: &[(String, String)], ua: Option<&str>| (opener.web_probe)(u, h, ua);
            let resolve = |u: &str| (opener.resolve)(u);
            match wv::open_web(
                &payload,
                chosen,
                opener.allow_local,
                &probe,
                &resolve,
                (opener.relays)(),
            ) {
                Ok(o) => Outcome::Open {
                    opened: Opened {
                        fd: o.fd,
                        title: o.title,
                        format: o.format,
                        source: o.source,
                        warnings: o.warnings,
                        stream: Some(o.stream),
                    },
                    owner,
                },
                Err(e) => Outcome::Refused {
                    text: refusal(&e, "cannot open the web video"),
                    owner,
                },
            }
        }
    }
}

/// `str(exc) or default`.
fn refusal(e: &Error, default: &str) -> String {
    let m = e.message();
    if m.is_empty() {
        default.into()
    } else {
        m.into()
    }
}

/// One deferred owner request (`self._owner_pending`'s dicts).
struct Pending {
    id: u64,
    op: String,
    done: Rc<dyn Done>,
    before: (u64, Option<Value>),
    error: Option<String>,
    format: Option<Format>,
    quality: Quality,
    inactive: bool,
}

/// `_bind_lock`'s state: the binds callback and whether the keys are up.
struct BindState {
    binds: Option<BindFn>,
    installed: bool,
}

impl BindState {
    /// `_bind_work`.
    fn work(&mut self, want: bool, force: bool) {
        let Some(binds) = self.binds.as_mut() else {
            return;
        };
        if want == self.installed && !(force && want) {
            return;
        }
        if binds(want).is_ok() {
            self.installed = want;
        }
    }
}

/// `_sync_binds`' threads: in order on a worker, or here (`threads=False`).
struct Binds {
    state: Arc<Mutex<BindState>>,
    jobs: Option<Sender<(bool, bool)>>,
    worker: Option<JoinHandle<()>>,
}

impl Binds {
    fn new(binds: Option<BindFn>, threads: bool) -> Binds {
        let present = binds.is_some();
        let state = Arc::new(Mutex::new(BindState {
            binds,
            installed: false,
        }));
        let mut out = Binds {
            state,
            jobs: None,
            worker: None,
        };
        if threads && present {
            let (jobs, inbox) = mpsc::channel::<(bool, bool)>();
            let state = out.state.clone();
            if let Ok(handle) =
                thread::Builder::new()
                    .name("xr-video-binds".into())
                    .spawn(move || {
                        for (want, force) in inbox {
                            if let Ok(mut s) = state.lock() {
                                s.work(want, force);
                            }
                        }
                    })
            {
                out.jobs = Some(jobs);
                out.worker = Some(handle);
            }
        }
        out
    }

    fn present(&self) -> bool {
        self.state.lock().is_ok_and(|s| s.binds.is_some())
    }

    fn sync(&mut self, want: bool, force: bool) {
        if !self.present() {
            return;
        }
        match &self.jobs {
            Some(jobs) => {
                let _ = jobs.send((want, force));
            }
            None => {
                if let Ok(mut s) = self.state.lock() {
                    s.work(want, force);
                }
            }
        }
    }

    fn installed(&self) -> bool {
        self.state.lock().is_ok_and(|s| s.installed)
    }

    /// `close`'s part: the work out gets until `deadline`, then no change follows but the keys' removal.
    fn close(&mut self, deadline: Instant) {
        self.jobs = None;
        if let Some(worker) = self.worker.take() {
            while !worker.is_finished() && Instant::now() < deadline {
                thread::sleep(Duration::from_millis(1));
            }
            if worker.is_finished() {
                let _ = worker.join();
            }
        }
        if let Ok(mut s) = self.state.lock()
            && let Some(mut binds) = s.binds.take()
            && s.installed
        {
            let _ = binds(false);
            s.installed = false;
        }
    }
}

/// `video.VideoControl`'s state, driven with the view as its [`Host`].
pub struct Control {
    opener: Opener,
    notify: Option<Notify>,
    stream: Option<WebStream>,
    origin: Option<&'static str>,
    opening: bool,
    last_poll: Option<f64>,
    stream_retries: u32,
    active: bool,
    title: Option<String>,
    format: Option<Format>,
    source: Option<&'static str>,
    warnings: Vec<String>,
    error: Option<String>,
    controls: bool,
    menu_hidden: bool,
    menu: bool,
    menu_row: i64,
    last_input: f64,
    opened_at: f64,
    error_seen_at: Option<f64>,
    /// The renderer's measured status's `video` (`supervisor.status`), when valid.
    renderer_video: Option<String>,
    opens: u64,
    pending: Vec<Pending>,
    next_owner: u64,
    stream_seek_owner: Option<u64>,
    stream_open_owner: Option<u64>,
    opening_owner: Option<u64>,
    results: (Sender<Outcome>, Receiver<Outcome>),
    worker: Option<JoinHandle<()>>,
    binds: Binds,
    toasts: Vec<JoinHandle<()>>,
    closed: bool,
}

impl Control {
    /// `VideoControl(view, binds=..., notify=..., probe=..., threads=...)`.
    pub fn new(opener: Opener, binds: Option<BindFn>, notify: Option<Notify>) -> Control {
        let threads = opener.threads;
        Control {
            opener,
            notify,
            stream: None,
            origin: None,
            opening: false,
            last_poll: None,
            stream_retries: 0,
            active: false,
            title: None,
            format: None,
            source: None,
            warnings: Vec::new(),
            error: None,
            controls: false,
            menu_hidden: false,
            menu: false,
            menu_row: 0,
            last_input: 0.0,
            opened_at: 0.0,
            error_seen_at: None,
            renderer_video: None,
            opens: 0,
            pending: Vec::new(),
            next_owner: 0,
            stream_seek_owner: None,
            stream_open_owner: None,
            opening_owner: None,
            results: mpsc::channel(),
            worker: None,
            binds: Binds::new(binds, threads),
            toasts: Vec::new(),
            closed: false,
        }
    }

    pub fn active(&self) -> bool {
        self.active
    }

    // --- frames

    fn frame(&mut self, host: &mut dyn Host, payload: &Payload) -> Vec<u8> {
        let id = host.next_request_id();
        v::video_frame(host.run_id(), host.session(), id as i64, payload).unwrap_or_default()
    }

    fn controls_frame(&mut self, host: &mut dyn Host, visible: bool) -> Vec<u8> {
        if visible == self.controls {
            return Vec::new();
        }
        let mut out = if visible {
            Vec::new()
        } else {
            self.menu_frame(host, false)
        };
        self.controls = visible;
        out.extend(self.frame(host, &Payload::Controls(visible)));
        out
    }

    fn menu_extra(host: &dyn Host) -> Vec<MenuRow> {
        let (label, value) = environment::menu_row(&host.environment());
        vec![MenuRow {
            label: label.into(),
            value: value.into(),
        }]
    }

    fn menu_frame(&mut self, host: &mut dyn Host, visible: bool) -> Vec<u8> {
        if !visible && !self.menu {
            return Vec::new();
        }
        self.menu = visible;
        let text = quality::menu_payload(
            host.video_quality(),
            visible,
            self.menu_row,
            &Self::menu_extra(host),
        );
        let payload = serde_json::from_str(&text)
            .ok()
            .and_then(|p| v::validate_renderer_payload(&p).ok());
        match payload {
            Some(p) => self.frame(host, &p),
            None => Vec::new(),
        }
    }

    fn quality_frame(&mut self, host: &mut dyn Host) -> Vec<u8> {
        let q = host.video_quality();
        self.frame(host, &Payload::Quality(q))
    }

    /// `_set_quality(settings, done)`: applied live (the renderer) and saved with the view settings.
    pub fn set_quality(
        &mut self,
        host: &mut dyn Host,
        settings: &Value,
        done: Option<Rc<dyn Done>>,
    ) -> Vec<u8> {
        let Ok(settings) = quality::validate(settings) else {
            return Vec::new();
        };
        let mut owner = None;
        if let Some(done) = done {
            if self.pending.len() >= MAX_PENDING {
                complete(&*done, false, "Too many pending video operations");
                return Vec::new();
            }
            let id = self.owner_id();
            self.pending.push(Pending {
                id,
                op: "quality".into(),
                done,
                before: host.owner_readback(),
                error: self.error.clone(),
                format: None,
                quality: settings,
                inactive: !self.active,
            });
            owner = Some(id);
        }
        if settings == host.video_quality() {
            return Vec::new();
        }
        host.set_video_quality(settings);
        if let Err(e) = host.save_settings()
            && let Some(id) = owner
            && let Some(i) = self.pending.iter().position(|p| p.id == id)
        {
            let p = self.pending.remove(i);
            complete(
                &*p.done,
                false,
                &format!("Could not save video quality: {e}"),
            );
        }
        self.quality_frame(host)
    }

    /// `_on_menu_key`: a video key while the quality menu is open; `None` if it is not a menu key.
    fn on_menu_key(&mut self, host: &mut dyn Host, op: &str) -> Option<Vec<u8>> {
        let rows = (quality::KEYS.len() + Self::menu_extra(host).len()) as i64;
        if op == "video_quality" || op == "video_exit" {
            return Some(self.menu_frame(host, false));
        }
        if ["video_up", "video_down", "video_format"].contains(&op) {
            self.menu_row =
                (self.menu_row + if op == "video_up" { -1 } else { 1 }).rem_euclid(rows);
            return Some(self.menu_frame(host, true));
        }
        let seconds = seek_seconds(op)?;
        let delta = if seconds < 0 { -1 } else { 1 };
        let mut out = if (self.menu_row as usize) < quality::KEYS.len() {
            let stepped = quality::step(host.video_quality(), self.menu_row, delta);
            self.set_quality(host, &quality::to_value(stepped), None)
        } else {
            let env = environment::stepped(&host.environment(), delta);
            host.set_environment(env)
        };
        out.extend(self.menu_frame(host, true));
        Some(out)
    }

    fn hide_if_hidden(&mut self, host: &mut dyn Host) -> Vec<u8> {
        if self.menu_hidden {
            self.controls_frame(host, false)
        } else {
            Vec::new()
        }
    }

    /// `_menu_toggle`: Ctrl+Alt+C. `None`: nothing to send.
    fn menu_toggle(&mut self, host: &mut dyn Host) -> Vec<u8> {
        if host.typing() {
            return Vec::new(); // its bind is gone in typing mode: a stale key
        }
        self.menu_hidden = !self.menu_hidden;
        host.tell(PLAYBACK_MENU_TOASTS[usize::from(!self.menu_hidden)]);
        if !self.active {
            return Vec::new();
        }
        self.last_input = host.now();
        let visible = !self.menu_hidden;
        self.controls_frame(host, visible)
    }

    fn owner_id(&mut self) -> u64 {
        self.next_owner += 1;
        self.next_owner
    }

    // --- the control socket

    /// `VideoControl.on_message(message, done)`: `Some(frames)` when the op was the video's (taken here; frames may be
    /// empty), `None` to hand it to the view.
    pub fn on_message(
        &mut self,
        host: &mut dyn Host,
        op: &str,
        payload: &Value,
        done: Option<Rc<dyn Done>>,
    ) -> Option<Vec<u8>> {
        let Some(done) = done else {
            return self.on_message_owned(host, op, payload, None);
        };
        if self.pending.len() >= MAX_PENDING {
            complete(&*done, false, "Too many pending video operations");
            return Some(Vec::new());
        }
        let before = host.owner_readback();
        if seek_seconds(op).is_some() && !position_readback(before.1.as_ref()) {
            complete(&*done, false, "Native video position readback unavailable");
            return Some(Vec::new());
        }
        if op != "video_open" && !self.active {
            complete(&*done, false, "No active video");
            return Some(Vec::new());
        }
        let id = self.owner_id();
        self.pending.push(Pending {
            id,
            op: op.into(),
            done,
            before,
            error: self.error.clone(),
            format: None,
            quality: Quality::default(),
            inactive: false,
        });
        let out = self.on_message_owned(host, op, payload, Some(id));
        let (format, quality) = (self.format, host.video_quality());
        if let Some(p) = self.pending.iter_mut().find(|p| p.id == id) {
            (p.format, p.quality) = (format, quality);
        }
        out
    }

    fn on_message_owned(
        &mut self,
        host: &mut dyn Host,
        op: &str,
        payload: &Value,
        owner: Option<u64>,
    ) -> Option<Vec<u8>> {
        if op == PLAYBACK_MENU_OP {
            return Some(self.menu_toggle(host));
        }
        if VIDEO_OPS.contains(&op) {
            let now = host.now();
            host.set_last_input(now);
            self.last_input = now;
            // The wire's payload check is this module's (view_controls::socket): a bad one is dropped.
            let Ok(request) = v::validate_request(op, payload) else {
                return Some(Vec::new());
            };
            return Some(self.on_video(host, op, request, owner));
        }
        if self.active {
            if op == "help" {
                // Ctrl+Alt+H: the video controls instead of the desktop help card.
                self.last_input = host.now();
                let visible = !self.controls;
                return Some(self.controls_frame(host, visible));
            }
            if ignored_while_playing(op) {
                return Some(Vec::new());
            }
        }
        None
    }

    /// After the view took `op`: typing mode releases the video's bare keys too, and gives them back.
    pub fn after_view(&mut self, host: &mut dyn Host, op: &str) {
        if op == crate::lua::view::TYPING_OP {
            self.sync_binds(host, false);
        }
    }

    fn on_video(
        &mut self,
        host: &mut dyn Host,
        op: &str,
        request: Request,
        owner: Option<u64>,
    ) -> Vec<u8> {
        if op == "video_open" {
            if self.worker.as_ref().is_some_and(|w| !w.is_finished()) {
                self.error = Some("still opening the previous video".into());
                return Vec::new();
            }
            let (target, chosen) = match request {
                Request::Open { path, format } => (Target::File(path), format),
                Request::OpenWeb { url, web, format } => {
                    let mut map = Map::new();
                    if let Some(u) = url {
                        map.insert("url".into(), u);
                    }
                    if let Some(w) = web {
                        map.insert("web".into(), w);
                    }
                    (Target::Web(map), format)
                }
                Request::Key => return Vec::new(),
            };
            (self.opening, self.error) = (true, None);
            self.opening_owner = owner;
            if self.opener.threads {
                let opener = self.opener.clone();
                let results = self.results.0.clone();
                self.worker = thread::Builder::new()
                    .name("xr-video-open".into())
                    .spawn(move || {
                        let _ = results.send(prepare(&opener, target, chosen, owner));
                    })
                    .ok();
            } else {
                let _ = self
                    .results
                    .0
                    .send(prepare(&self.opener, target, chosen, owner));
            }
            return Vec::new(); // poll() sends the open frame with the descriptor
        }
        if !self.active {
            return Vec::new();
        }
        if self.menu
            && let Some(out) = self.on_menu_key(host, op)
        {
            return out;
        }
        match op {
            "video_quality" => {
                let mut out = self.controls_frame(host, true);
                out.extend(self.menu_frame(host, true));
                return out;
            }
            "video_exit" => return self.close_video(host, None),
            "video_controls" | "video_up" | "video_down" => {
                let visible = !self.controls;
                return self.controls_frame(host, visible);
            }
            _ => {}
        }
        let mut out = if self.menu_hidden {
            Vec::new()
        } else {
            self.controls_frame(host, true)
        };
        if op == "video_pause" {
            out.extend(self.frame(host, &Payload::Pause));
        } else if let Some(seconds) = seek_seconds(op) {
            if self.stream.is_some() {
                // A pipe cannot seek: poll() restarts the relay once the keys settle.
                let previous = self.stream_seek_owner;
                if let Some(p) = previous
                    && Some(p) != owner
                {
                    self.discard_deferred(Some(p), "Video seek superseded");
                }
                self.stream_seek_owner = owner;
                let now = host.now();
                if let Some(stream) = self.stream.as_mut()
                    && !stream.request_seek(seconds as f64, now)
                {
                    self.error = Some("a live stream cannot seek".into());
                }
            } else {
                out.extend(self.frame(host, &Payload::Seek(seconds)));
            }
        } else if op == "video_format"
            && let Some(format) = self.format
        {
            let next = v::next_format(&format);
            (self.format, self.source) = (Some(next), Some("chosen"));
            out.extend(self.frame(host, &Payload::Format(next)));
        }
        out
    }

    /// `_open`: the video's first frames (its descriptor goes with them).
    fn open(&mut self, host: &mut dyn Host, opened: Opened) -> (Vec<u8>, OwnedFd) {
        let mut out = Vec::new();
        if host.menu_open() {
            out.extend(host.close_menu()); // collapses: saves, releases the keys
        }
        if host.help_visible() {
            out.extend(host.close_help());
        }
        if host.keys_open() {
            out.extend(host.close_keys()); // the key bind panel's keys go too
        }
        let Opened {
            fd,
            title,
            format,
            source,
            warnings,
            stream,
        } = opened;
        if let Some(mut old) = self.stream.take() {
            old.stop();
        }
        self.origin = Some(if stream.is_some() { "web" } else { "file" });
        (self.stream, self.opening, self.stream_retries) = (stream, false, 0);
        self.title = Some(title.clone());
        out.extend(self.frame(host, &Payload::Open { format, title }));
        out.extend(self.quality_frame(host)); // the renderer applies it to this and later videos
        self.menu = false;
        let now = host.now();
        (
            self.active,
            self.format,
            self.source,
            self.warnings,
            self.error,
        ) = (true, Some(format), Some(source), warnings, None);
        host.set_video_active(true); // free pointer off
        self.controls = true; // the renderer shows the card on open
        out.extend(self.hide_if_hidden(host));
        (self.last_input, self.opened_at) = (now, now);
        self.error_seen_at = None;
        self.opens += 1;
        self.sync_binds(host, false);
        (out, fd)
    }

    /// `_restart_stream`: a seek or a resume of a web video, as a fresh open from `at` (same format).
    fn restart_stream(
        &mut self,
        host: &mut dyn Host,
        at: f64,
        now: f64,
        owner: Option<u64>,
    ) -> (Vec<u8>, Option<OwnedFd>) {
        if !self.retain_deferred(owner, b"") {
            return (Vec::new(), None);
        }
        let started = match self.stream.as_mut() {
            Some(stream) => stream.start(at),
            None => return (Vec::new(), None),
        };
        let fd = match started {
            Ok(fd) => fd,
            Err(e) => {
                let why = refusal(&e, "the stream could not be restarted");
                return (self.close_video(host, Some(why)), None);
            }
        };
        (self.controls, self.menu) = (true, false);
        (self.opened_at, self.error_seen_at, self.last_input) = (now, None, now);
        let (Some(format), Some(title)) = (self.format, self.title.clone()) else {
            return (Vec::new(), None);
        };
        let mut out = self.frame(host, &Payload::Open { format, title });
        out.extend(self.hide_if_hidden(host));
        if !self.retain_deferred(owner, &out) {
            return (Vec::new(), None); // the descriptor goes with `fd`
        }
        (out, Some(fd))
    }

    /// `_close(error)`.
    fn close_video(&mut self, host: &mut dyn Host, error: Option<String>) -> Vec<u8> {
        if let Some(mut stream) = self.stream.take() {
            stream.stop();
        }
        if !self.active {
            return Vec::new();
        }
        (self.active, self.controls, self.menu) = (false, false, false);
        host.set_video_active(false);
        if let Some(e) = error.filter(|e| !e.is_empty()) {
            self.error = Some(e);
        }
        self.sync_binds(host, false);
        self.frame(host, &Payload::Close)
    }

    /// `renderer_restarted()`: the video went with the old renderer, and its keys go too.
    pub fn renderer_restarted(&mut self, host: &mut dyn Host) {
        self.finish_video(false, "Renderer restarted");
        self.close_video(host, None);
    }

    /// `poll(now)`, after the view's: the worker's results, a web video's relay, the card's idle close, the
    /// renderer's errors, and the deferred owners.
    pub fn poll(&mut self, host: &mut dyn Host, now: f64, out: &mut Frames) {
        while let Ok(result) = self.results.1.try_recv() {
            let owner = match &result {
                Outcome::Open { owner, .. } | Outcome::Refused { owner, .. } => *owner,
            };
            if !self.retain_deferred(owner, b"") {
                if self.opening_owner == owner {
                    self.opening = false;
                }
                continue; // an open's descriptor and stream go with it
            }
            match result {
                Outcome::Open { opened, .. } => {
                    let (frame, fd) = self.open(host, opened);
                    if !self.retain_deferred(owner, &frame) {
                        drop(fd);
                        self.close_video(host, Some("Deferred video request became stale".into()));
                        continue;
                    }
                    self.stream_open_owner = owner;
                    out.bytes.extend(frame);
                    out.fds.push(fd);
                }
                Outcome::Refused { text, .. } => {
                    (self.opening, self.error) = (false, Some(text.clone()));
                    self.tell(&text);
                    if owner.is_some() {
                        self.discard_deferred(owner, &text);
                    } else {
                        self.finish_video(false, &text);
                    }
                }
            }
        }
        let dt = self.last_poll.map_or(0.0, |last| now - last);
        self.last_poll = Some(now);
        if self.active && self.stream.is_some() {
            let state = self.renderer_state();
            let (target, failure, position) = {
                let stream = self.stream.as_mut().expect("checked");
                stream.advance(dt, state.unwrap_or(""));
                (stream.due_seek(now), stream.failure(), stream.position())
            };
            let mut frame = (Vec::new(), None);
            if let Some(target) = target {
                let owner = self.stream_seek_owner.take();
                frame = self.restart_stream(host, target, now, owner);
            } else if let Some(failure) =
                failure.filter(|_| matches!(state, Some("ended" | "error")))
            {
                // The connection broke for good (ffmpeg's own reconnects failed): resume once or twice, then give up.
                if self.stream_retries < 2 && position > 0.0 {
                    self.stream_retries += 1;
                    let owner = self
                        .stream_open_owner
                        .filter(|id| self.pending.iter().any(|p| p.id == *id));
                    frame = self.restart_stream(host, position, now, owner);
                } else {
                    self.error = Some(failure.clone());
                    self.tell(&failure);
                    let closed = self.close_video(host, Some(failure));
                    out.bytes.extend(closed);
                }
            }
            out.bytes.extend(frame.0);
            out.fds.extend(frame.1);
        }
        if self.active {
            if self.controls && now - self.last_input >= OVERLAY_IDLE_CLOSE_S {
                let hidden = self.controls_frame(host, false);
                out.bytes.extend(hidden);
            }
            let state = self.renderer_state();
            if state == Some("error") {
                match self.error_seen_at {
                    None => {
                        self.error_seen_at = Some(now);
                        self.error = Some("the renderer could not play this video".into());
                        let shown = self.controls_frame(host, true); // the card says why
                        out.bytes.extend(shown);
                    }
                    Some(seen) if now - seen >= ERROR_EXIT_S => {
                        let closed = self.close_video(host, None);
                        out.bytes.extend(closed);
                    }
                    Some(_) => {}
                }
            } else {
                self.error_seen_at = None;
                if matches!(state, None | Some("off")) && now - self.opened_at >= START_GRACE_S {
                    let why = "video playback is not available in this session (renderer without video support)";
                    let closed = self.close_video(host, Some(why.into()));
                    out.bytes.extend(closed);
                }
            }
        }
        self.poll_owner_readback(host);
    }

    /// When [`Control::poll`] is next due: soon while an open is out or a web video plays, else at the card's idle
    /// close, the start grace or the error's exit.
    pub fn deadline(&self, host: &dyn Host, now: Instant) -> Option<Instant> {
        if self.opening || self.worker.as_ref().is_some_and(|w| !w.is_finished()) {
            return Some(now + WORK_TICK);
        }
        if !self.active {
            return None;
        }
        if self.stream.is_some() || !self.pending.is_empty() {
            return Some(now + STREAM_TICK);
        }
        let t = host.now();
        let mut due = vec![self.opened_at + START_GRACE_S];
        if self.controls {
            due.push(self.last_input + OVERLAY_IDLE_CLOSE_S);
        }
        if let Some(seen) = self.error_seen_at {
            due.push(seen + ERROR_EXIT_S);
        }
        if self.renderer_state() == Some("error") && self.error_seen_at.is_none() {
            due.push(t);
        }
        let at = due.into_iter().fold(f64::INFINITY, f64::min);
        Some(now + Duration::from_secs_f64((at - t).clamp(0.0, 3600.0)))
    }

    fn discard_deferred(&mut self, owner: Option<u64>, detail: &str) {
        let Some(id) = owner else { return };
        if let Some(i) = self.pending.iter().position(|p| p.id == id) {
            let p = self.pending.remove(i);
            complete(&*p.done, false, detail);
        }
    }

    /// `_retain_deferred`: an owner's request is still wanted, and the frames are registered with it.
    fn retain_deferred(&mut self, owner: Option<u64>, frames: &[u8]) -> bool {
        let Some(id) = owner else { return true };
        let Some(p) = self.pending.iter().find(|p| p.id == id) else {
            return false;
        };
        match p.done.retain_frames(frames) {
            None | Some(true) => true,
            Some(false) => {
                self.discard_deferred(owner, "Deferred video request superseded or stale");
                false
            }
        }
    }

    fn finish_video(&mut self, success: bool, detail: &str) {
        for p in std::mem::take(&mut self.pending) {
            complete(&*p.done, success, detail);
        }
    }

    /// `_poll_owner_readback`: each owner's request completed from what the renderer read back.
    fn poll_owner_readback(&mut self, host: &dyn Host) {
        if self.pending.is_empty() {
            return;
        }
        let (seq, report) = host.owner_readback();
        let empty = Map::new();
        let playback = report
            .as_ref()
            .and_then(|r| r.get("playback"))
            .and_then(Value::as_object)
            .unwrap_or(&empty);
        let num = |m: &Map<String, Value>, k: &str| m.get(k).and_then(Value::as_f64).unwrap_or(0.0);
        let text =
            |m: &Map<String, Value>, k: &str| m.get(k).and_then(Value::as_str).map(str::to_owned);
        let truthy = |m: &Map<String, Value>, k: &str| m.get(k).is_some_and(truthy);
        let opened = |r: Option<&Value>| {
            r.and_then(|r| r.get("opened"))
                .and_then(Value::as_f64)
                .unwrap_or(0.0)
        };
        let mut i = 0;
        while i < self.pending.len() {
            let item = &self.pending[i];
            let mut finish = None;
            if let Some(e) = self.error.as_ref()
                && Some(e) != item.error.as_ref()
            {
                finish = Some((false, e.clone()));
            }
            let before = item.before.1.as_ref();
            let old = before
                .and_then(|b| b.get("playback"))
                .and_then(Value::as_object)
                .unwrap_or(&empty);
            if finish.is_none() {
                let erring = playback.get("state").and_then(Value::as_str) == Some("error")
                    || truthy(playback, "error");
                let new_error = erring
                    && (num(playback, "revision") > num(old, "revision")
                        || playback.get("error") != old.get("error")
                        || (report.is_some() && opened(report.as_ref()) > opened(before)));
                if new_error {
                    let why = text(playback, "error").filter(|e| !e.is_empty());
                    finish = Some((
                        false,
                        why.unwrap_or_else(|| "Native video playback failed".into()),
                    ));
                } else if num(playback, "command_errors") > num(old, "command_errors") {
                    let why = text(playback, "command_error").filter(|e| !e.is_empty());
                    finish = Some((
                        false,
                        why.unwrap_or_else(|| "Native video command failed".into()),
                    ));
                }
            }
            if finish.is_none() {
                let same = (before.is_none() && report.is_none())
                    || (report.is_some() && seq == item.before.0);
                if !(same && item.op != "quality" && item.op != "video_format") {
                    let state = playback.get("state").and_then(Value::as_str);
                    let fresh = num(playback, "revision") > num(old, "revision");
                    let ok = match item.op.as_str() {
                        "video_exit" => match report.as_ref() {
                            None => true,
                            Some(r) => r.get("state").and_then(Value::as_str) == Some("off"),
                        },
                        _ if report.is_none() || !report.as_ref().is_some_and(Value::is_object) => {
                            false
                        }
                        "video_open" => {
                            matches!(state, Some("playing" | "paused"))
                                && opened(report.as_ref()) > opened(before)
                        }
                        "video_pause" => match (old.get("paused"), playback.get("paused")) {
                            (Some(Value::Bool(a)), Some(Value::Bool(b))) => fresh && a != b,
                            _ => false,
                        },
                        "quality" => {
                            let actual = report.as_ref().and_then(|r| r.get("quality_readback"));
                            let (fk, dk) = if item.inactive {
                                ("filter_requested", "deband_requested")
                            } else {
                                ("filter_applied", "deband_applied")
                            };
                            let filter = actual.and_then(|a| a.get(fk)).and_then(Value::as_str);
                            let deband = actual.and_then(|a| a.get(dk)).and_then(Value::as_bool);
                            filter == Some(item.quality.filter_name())
                                && deband == Some(item.quality.deband)
                        }
                        "video_format" => {
                            let applied = report.as_ref().and_then(|r| r.get("format_applied"));
                            applied.and_then(|a| v::validate_format(a).ok()).is_some()
                                && applied.and_then(|a| v::validate_format(a).ok()) == item.format
                        }
                        op => match seek_seconds(op) {
                            Some(seconds) => {
                                let pos = playback
                                    .get("position_s")
                                    .filter(|p| p.is_number())
                                    .and_then(Value::as_f64);
                                let start =
                                    old.get("position_s").and_then(Value::as_f64).unwrap_or(0.0);
                                let mut target = (start + seconds as f64).max(0.0);
                                if let Some(d) = old
                                    .get("duration_s")
                                    .and_then(Value::as_f64)
                                    .filter(|d| *d > 0.0)
                                {
                                    target = target.min(d);
                                }
                                fresh
                                    && pos.is_some_and(|p| (p - target).abs() <= 2.0)
                                    && num(playback, "command_replies")
                                        > num(old, "command_replies")
                            }
                            None => false,
                        },
                    };
                    if ok {
                        let detail = if item.inactive {
                            "Saved for next video"
                        } else {
                            "Native video state verified"
                        };
                        finish = Some((true, detail.into()));
                    }
                }
            }
            match finish {
                Some((success, detail)) => {
                    let p = self.pending.remove(i);
                    complete(&*p.done, success, &detail);
                }
                None => i += 1,
            }
        }
    }

    /// `renderer_state()`: the measured status's video state, when it is one.
    fn renderer_state(&self) -> Option<&'static str> {
        VIDEO_STATES
            .iter()
            .copied()
            .find(|s| Some(*s) == self.renderer_video.as_deref())
    }

    /// The supervisor's measured status changed (`Supervisor.status = value`): its `video`, if any.
    pub fn set_measured(&mut self, video: Option<&str>) {
        self.renderer_video = video.map(str::to_owned);
    }

    /// `on_events`' config reload while a video plays: a reload drops runtime binds, so the keys go back in.
    pub fn config_reloaded(&mut self, host: &mut dyn Host) {
        if self.active {
            if let Ok(mut s) = self.binds.state.lock() {
                s.installed = false;
            }
            self.sync_binds(host, true);
        }
    }

    fn tell(&mut self, text: &str) {
        let Some(notify) = self.notify.clone() else {
            return;
        };
        if !self.opener.threads {
            notify(text);
            return;
        }
        let text = text.to_owned();
        self.toasts.retain(|t| !t.is_finished());
        if let Ok(t) = thread::Builder::new()
            .name("xr-video-toast".into())
            .spawn(move || notify(&text))
        {
            self.toasts.push(t);
        }
    }

    /// `snapshot()`: the panel's `video` entry of the status reply, in the Python's key order.
    pub fn snapshot(&self, host: &dyn Host) -> xr_wire::json::Value {
        use xr_wire::json::Value as J;
        let text = |s: &str| J::String(s.into());
        let opt = |s: Option<&str>| s.map_or(J::Null, text);
        let format = |f: &Format| {
            J::Object(vec![
                ("projection".into(), text(f.projection.as_str())),
                ("stereo".into(), text(f.stereo.as_str())),
                ("fov".into(), J::Number(f.fov.into())),
            ])
        };
        let state = if self.active {
            self.renderer_state()
        } else {
            Some("off")
        };
        let state = state.unwrap_or(if self.active { "loading" } else { "off" });
        let q = host.video_quality();
        let stream = self
            .stream
            .as_ref()
            .filter(|_| self.active)
            .map(WebStream::snapshot);
        let stream = stream
            .and_then(|s| xr_wire::json::parse_strict(s.as_bytes()).ok())
            .unwrap_or(J::Null);
        let entries: Vec<(&str, J)> = vec![
            ("active", J::Bool(self.active)),
            ("title", opt(self.title.as_deref())),
            ("format", self.format.as_ref().map_or(J::Null, format)),
            (
                "label",
                self.format
                    .as_ref()
                    .map_or(J::Null, |f| J::String(v::format_label(f))),
            ),
            ("source", opt(self.source)),
            ("state", text(state)),
            ("controls", J::Bool(self.controls)),
            ("menu_hidden", J::Bool(self.menu_hidden)),
            (
                "warnings",
                J::Array(self.warnings.iter().map(|w| text(w)).collect()),
            ),
            ("error", opt(self.error.as_deref())),
            ("ops", J::Array(KEY_OPS.iter().map(|o| text(o)).collect())),
            ("opening", J::Bool(self.opening)),
            ("origin", opt(self.origin.filter(|_| self.active))),
            (
                "quality",
                J::Object(vec![
                    ("filter".into(), text(q.filter_name())),
                    ("deband".into(), J::Bool(q.deband)),
                ]),
            ),
            ("quality_menu", J::Bool(self.menu && self.active)),
            ("stream", stream),
        ];
        J::Object(
            entries
                .into_iter()
                .map(|(k, v)| (k.to_owned(), v))
                .collect(),
        )
    }

    // --- binds

    fn sync_binds(&mut self, host: &dyn Host, force: bool) {
        if self.closed {
            return;
        }
        let want = self.active && !host.typing();
        self.binds.sync(want, force);
    }

    /// The keys are up (tests).
    pub fn binds_installed(&self) -> bool {
        self.binds.installed()
    }

    /// `close()`: every owner completed, no later bind change, the video keys removed if they are up, and the
    /// descriptors never sent closed.
    pub fn close(&mut self) {
        self.finish_video(false, "Video owner stopped");
        let deadline = Instant::now() + CLOSE_TIMEOUT;
        self.closed = true;
        self.binds.close(deadline);
        if let Some(worker) = self.worker.take() {
            while !worker.is_finished() && Instant::now() < deadline {
                thread::sleep(Duration::from_millis(1));
            }
            if worker.is_finished() {
                let _ = worker.join();
            }
        }
        if let Some(mut stream) = self.stream.take() {
            stream.stop();
        }
        while self.results.1.try_recv().is_ok() {} // each open's descriptor and stream go with it
        for t in self.toasts.drain(..) {
            while !t.is_finished() && Instant::now() < deadline {
                thread::sleep(Duration::from_millis(1));
            }
        }
    }
}

impl Drop for Control {
    fn drop(&mut self) {
        if !self.closed {
            self.close();
        }
    }
}

/// Python's truthiness of a JSON value.
fn truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().is_some_and(|x| x != 0.0),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// `isinstance(before, dict) and isinstance(before.get('player'), dict) and type(position_s) in (int, float)`.
fn position_readback(before: Option<&Value>) -> bool {
    let Some(b) = before.and_then(Value::as_object) else {
        return false;
    };
    b.get("player").is_some_and(Value::is_object)
        && b.get("playback")
            .and_then(|p| p.get("position_s"))
            .is_some_and(Value::is_number)
}

/// The measured status's `video` from one status datagram's lines (`Supervisor.status_bytes`): `Some(state)` for
/// each line that replaced the measured status (`None` inside: it has no `video`).
fn measured_videos(datagram: &[u8]) -> Vec<Option<String>> {
    datagram
        .split(|b| *b == b'\n')
        .filter(|l| !l.is_empty())
        .filter_map(crate::managed::real::measured_status)
        .map(|status| {
            status
                .get("video")
                .and_then(|v| v.as_str())
                .map(str::to_owned)
        })
        .collect()
}

/// `video.VideoControl` in a run, with its host when the run has no view.
pub struct Video {
    control: Control,
    detached: Detached,
}

/// The bind command `bind_args` names (`binds::Binds::bind_args`).
fn command(bind_args: &[String]) -> Option<BindCommand<'_>> {
    match bind_args {
        [python, client, control_socket, run_id, session] => Some(BindCommand {
            python,
            client,
            control_socket,
            run_id,
            session,
        }),
        _ => None,
    }
}

impl Video {
    /// `VideoControl(control, binds=video_binds, notify=notify_send)`. `bind_args`: the bind helper's command line;
    /// `None`: the run installs no binds, so there are no video keys either (`video_binds = None`). The keys go in
    /// and out on a client of the video's own, pinned to the compositor the run's client verified.
    pub fn open(run: &mut Run<'_>, bind_args: Option<Vec<String>>) -> Option<Video> {
        if run.capture.is_empty() {
            return None;
        }
        let client = run.client.identity().cloned().and_then(|identity| {
            Client::pinned(run.client.instance(), run.runtime, identity, run.session).ok()
        });
        let binds: Option<BindFn> = match (bind_args.filter(|a| command(a).is_some()), client) {
            (Some(args), Some(mut client)) => Some(Box::new(move |on| {
                let result = match (on, command(&args)) {
                    (true, Some(cmd)) => client.install_video_binds(&cmd),
                    (false, _) => client.remove_video_binds(),
                    (true, None) => return Err("invalid bind arguments".into()),
                };
                result.map_err(|e| e.to_string())
            })),
            _ => None,
        };
        let notify: Notify = Arc::new(notify_send);
        Some(Video {
            control: Control::new(Opener::real(), binds, Some(notify)),
            detached: Detached::new(
                run.run_id,
                run.session,
                Arc::new(crate::managed::real::notify),
            ),
        })
    }

    /// The control and its host: the view's, or the detached one.
    fn parts<'a>(
        &'a mut self,
        view: Option<&'a mut ViewControl>,
    ) -> (&'a mut Control, &'a mut dyn Host) {
        let host: &mut dyn Host = match view {
            Some(view) => view,
            None => &mut self.detached,
        };
        (&mut self.control, host)
    }

    /// `VideoControl.on_message`: true when the op was the video's (taken here), false to hand it to the view.
    pub fn on_message(
        &mut self,
        message: &Frame,
        view: Option<&mut ViewControl>,
        out: &mut Frames,
    ) -> bool {
        let payload = super::to_serde(&message.payload);
        let (control, host) = self.parts(view);
        match control.on_message(host, &message.op, &payload, None) {
            Some(frames) => {
                out.bytes.extend(frames);
                true
            }
            None => false,
        }
    }

    /// The view took `message`: a typing toggle syncs the video keys.
    pub fn after_view(&mut self, message: &Frame, view: Option<&mut ViewControl>) {
        let (control, host) = self.parts(view);
        control.after_view(host, &message.op);
    }

    /// `VideoControl.poll`, after the view's.
    pub fn poll(&mut self, view: Option<&mut ViewControl>, out: &mut Frames) {
        let (control, host) = self.parts(view);
        let now = host.now();
        control.poll(host, now, out);
    }

    pub fn deadline(&mut self, view: Option<&mut ViewControl>, now: Instant) -> Option<Instant> {
        let (control, host) = self.parts(view);
        control.deadline(host, now)
    }

    /// The measured status a playing video reads (`attach_supervisor`: `supervisor.status`).
    pub fn status(&mut self, datagram: &[u8]) {
        for video in measured_videos(datagram) {
            self.control.set_measured(video.as_deref());
        }
    }

    /// `status_reply`'s `video`.
    pub fn snapshot(&self, view: Option<&ViewControl>) -> xr_wire::json::Value {
        let host: &dyn Host = match view {
            Some(view) => view,
            None => &self.detached,
        };
        self.control.snapshot(host)
    }

    /// `renderer_restarted()`, before the view's.
    pub fn renderer_restarted(&mut self, view: Option<&mut ViewControl>) {
        let (control, host) = self.parts(view);
        control.renderer_restarted(host);
    }

    /// A config reload (the view's event stream): the video keys back if a video plays.
    pub fn config_reloaded(&mut self, view: Option<&mut ViewControl>) {
        let (control, host) = self.parts(view);
        control.config_reloaded(host);
    }

    /// `video.close()`: the video keys removed if a video was playing; none added after this.
    pub fn close(&mut self, _run: &mut Run<'_>) {
        self.control.close();
    }
}
