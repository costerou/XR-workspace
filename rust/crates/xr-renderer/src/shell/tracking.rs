//! Head tracking: the pose feed, recentre, the viewing mode, prediction and stabilization, and the camera they give
//! the frame (wayland.cpp's tracked render path; `pose.rs`, `predict.rs`, `stabilization.rs`, `motion_smooth.rs`,
//! `motion_feed.rs` hold the ported logic).
//!
//! A frame reaches this module twice. [`Tracking::contribute`] (first of the features) reads the pose late, just before
//! drawing, stabilizes, predicts and smooths it, raises the reconnecting hint and settles the FOV. [`Tracking::camera`]
//! runs after every feature has contributed, once `scene.own` holds the layout's placements: it lands the switches'
//! turns, aims the follow and locked modes at the focused desktop and writes the camera (`scene.head`, `world`,
//! `environment_world`). The software latency tracker is fed from `shell.rs` (the read time per feedback ticket, and
//! the presented time).

use std::rc::Rc;

use xr_renderer::continuity::ViewContinuity;
use xr_renderer::environment::environment_view;
use xr_renderer::hud::{HUD_FONT_FAMILY, HudImage, HudTextMetrics, TrackingHint, render_hud_card, tracking_hint_lines};
use xr_renderer::hud_font::HudFont;
use xr_renderer::ipc::StatusExtras;
use xr_renderer::motion_feed::{MotionFeed, motion_age_ms, motion_delta};
use xr_renderer::motion_smooth::{MotionSmoother, motion_smooth_params};
use xr_renderer::pose::{
    Change, IDENTITY, Mat3, ModeController, PoseFeed, PoseState, Quat, ViewMode, aim_along, aim_rotation, canvas_up,
    mat3_to_quat,
    monotonic_ns, mul, quat_angle, quat_conj, quat_mul, quat_slerp, quat_to_mat3, view_from,
};
use xr_renderer::predict::{
    LatencySummary, LatencyTracker, PoseAge, VsyncMeter, angular_velocity, history_of, look_ahead_from,
    prediction_delta, prediction_horizon,
};
use xr_renderer::stabilization::Stabilizer;
use xr_renderer::view::{
    Canvas, FRect, ViewAnimator, edge_pan_path, focus_direction, focus_weight, look_turn, look_turn_from,
    overview_layout, view_centre,
};
use xr_wire::control::{View, ViewMode as WireMode};

use super::scene::{CardInput, Scene};
use super::{Presentation, monotonic_ms};

/// Whether this build takes `--pose-feed xrld`.
pub fn pose_feed_supported() -> bool {
    true
}

/// The XRLinuxDriver feed's fixed prefix (spike/src/main.cpp).
pub const POSE_PREFIX: &str = "/tmp/shader_runtime_";

/// The diagonal FOV when neither the supervisor nor the driver gives one (reported as `fallback-46`, never silent).
const FALLBACK_FOV_DEG: f32 = 46.0;
/// Prediction never turns the view further than this (Impl::kMaxPredictionDeg).
const MAX_PREDICTION_DEG: f32 = 15.0;
/// A feed (and the motion wire) that is not there is looked for again this often.
const RETRY_MS: u64 = 1000;

/// A centred switch's starting direction, per switch serial (wayland.cpp `Centre`).
#[derive(Default)]
struct Centre {
    valid: bool,
    serial: u32,
    dir: [f32; 2],
}

pub(super) struct Tracking {
    requested: bool,
    prefix: String,
    /// The driver's lock-free channel, preferred when it is written (production prefix only).
    seqlock: String,
    supervised_projection: f32,
    pose_feed: Option<PoseFeed>,
    /// The Luma driver's PredictionWire; absent on XRLinuxDriver.
    motion_feed: Option<MotionFeed>,
    motion_retry_ms: u64,
    /// The last frame predicted from a fresh wire sample.
    motion_live: bool,
    stabilization: Stabilizer,
    pose_why: String,
    pose_retry_ms: u64,
    pose: PoseState,
    modes: ModeController,
    /// The supervisor's last view was maximized (zoomed in, or zooming in).
    maximized: bool,
    /// Software latency (pose read -> compositor presented, same CLOCK_MONOTONIC). Not motion-to-photon latency.
    latency: LatencyTracker,
    pose_age: PoseAge,
    /// Only fed while our driver publishes pose_vsync (--read-events).
    vsync_meter: VsyncMeter,
    prediction: bool,
    predicted_frames: u64,
    unpredicted_frames: u64,
    wire_frames: u64,
    last_horizon_ms: f32,
    last_predicted_deg: f32,
    /// Only with smooth_ms > 0: the Pro 2's slow, uneven feed.
    smoother: MotionSmoother,
    smooth_ms: f32,
    smoothed_frames: u64,
    /// "Reconnecting tracking..." through a USB drop, in the pop-ups' place.
    hint: TrackingHint,
    hints: u64,
    hint_font: Option<HudFont>,
    hint_image: Option<(u64, Rc<HudImage>)>,
    half_w: f32,
    half_h: f32,
    fov_deg: f32,
    fov_from_driver: bool,
    /// The canvas the FOV was split for: a new aspect splits it again.
    fov_size: (u32, u32),
    /// This frame's head pose against the recentre reference (predicted / smoothed), for [`Tracking::camera`].
    relative: Quat,
    now_ms: f64,
    /// This frame's read time and the device time of the sample it draws (-1: none), taken by the next commit.
    read: Option<(u64, f32)>,
    /// The newest presented frame's refresh prediction (wp_presentation), as Metrics keeps it.
    refresh_ns: u32,
    /// The view the desktops were last drawn with (turned), and the head before the switch's turn.
    last_drawn: Mat3,
    last_head: Mat3,
    centre: Centre,
}

impl Default for Tracking {
    fn default() -> Self {
        Self {
            requested: false,
            prefix: String::new(),
            seqlock: String::new(),
            supervised_projection: 0.0,
            pose_feed: None,
            motion_feed: None,
            motion_retry_ms: 0,
            motion_live: false,
            stabilization: Stabilizer::new(),
            pose_why: String::new(),
            pose_retry_ms: 0,
            pose: PoseState::default(),
            modes: ModeController::default(),
            maximized: false,
            latency: LatencyTracker::new(),
            pose_age: PoseAge::new(),
            vsync_meter: VsyncMeter::new(),
            prediction: false,
            predicted_frames: 0,
            unpredicted_frames: 0,
            wire_frames: 0,
            last_horizon_ms: 0.0,
            last_predicted_deg: 0.0,
            smoother: MotionSmoother::default(),
            smooth_ms: 0.0,
            smoothed_frames: 0,
            hint: TrackingHint::new(),
            hints: 0,
            hint_font: None,
            hint_image: None,
            half_w: 0.0,
            half_h: 0.0,
            fov_deg: 0.0,
            fov_from_driver: false,
            fov_size: (0, 0),
            relative: Quat::default(),
            now_ms: 0.0,
            read: None,
            refresh_ns: 0,
            last_drawn: IDENTITY,
            last_head: IDENTITY,
            centre: Centre::default(),
        }
    }
}

/// tan of the half FOV, horizontal and vertical, filling a `diag_deg` diagonal FOV on a `width` x `height` canvas
/// (wayland.cpp update_fov, in its double maths).
pub(super) fn split_fov(diag_deg: f32, width: u32, height: u32) -> (f32, f32) {
    let d = (f64::from(diag_deg) * std::f64::consts::PI / 360.0).tan();
    let a = if height != 0 { f64::from(width) / f64::from(height) } else { 16.0 / 10.0 };
    let n = (1.0 + a * a).sqrt();
    ((d * a / n) as f32, (d / n) as f32)
}

/// C++ `std::to_string` of a float or double (`%f`).
fn f6(x: impl Into<f64>) -> String {
    format!("{:.6}", x.into())
}

fn stats_json(l: &LatencySummary) -> String {
    format!(
        "{{\"count\":{},\"dropped\":{},\"mean_ms\":{},\"p50_ms\":{},\"p95_ms\":{},\"max_ms\":{}}}",
        l.count,
        l.dropped,
        f6(l.mean_ms),
        f6(l.p50_ms),
        f6(l.p95_ms),
        f6(l.max_ms)
    )
}

impl Tracking {
    /// The FOV at the centre pose for a `width` x `height` canvas: the supervisor's projection, else the driver's
    /// diagonal, else 46 degrees. Split again only when it was invalidated (feed opened, projection given) or the
    /// canvas changed, as the C++ resets half_w.
    pub(super) fn fov(&mut self, width: u32, height: u32) -> (f32, f32) {
        if self.half_w == 0.0 || self.fov_size != (width, height) {
            let published = self.pose_feed.as_ref().and_then(PoseFeed::display_fov);
            self.fov_from_driver = published.is_some();
            let diag = if self.supervised_projection > 0.0 {
                self.supervised_projection
            } else {
                published.unwrap_or(FALLBACK_FOV_DEG)
            };
            self.fov_deg = diag;
            (self.half_w, self.half_h) = split_fov(diag, width, height);
            self.fov_size = (width, height);
        }
        (self.half_w, self.half_h)
    }

    /// The tracked half of the frame: the late pose read and what follows it, the reconnecting hint and the FOV.
    /// Every GPU frame records its read time for the latency tracker, tracked or not (as the C++).
    pub(super) fn contribute(&mut self, scene: &mut Scene) {
        let read_ns = monotonic_ns(); // the wp_presentation clock domain (checked in LatencyTracker)
        let mut drawn_ms = -1.0f32;
        self.relative = Quat::default();
        if self.requested {
            let now = monotonic_ms();
            self.now_ms = now as f64;
            self.read_pose(now, read_ns, &mut drawn_ms);
        }
        self.read = Some((read_ns, drawn_ms));
        (scene.half_w, scene.half_h) = self.fov(scene.width, scene.height);
        scene.fov_deg = self.fov_deg;
        // The hint takes the pop-ups' place; a mode pop-up (cards) goes first and replaces it there.
        if self.requested {
            let alpha = self.hint.alpha(monotonic_ms() as f64);
            scene.popup = Some(if alpha > 0.0 {
                let (generation, image) = self.hint_card();
                CardInput { image: Some(image), generation, alpha, drop_px: 0.0 }
            } else {
                CardInput::default()
            });
        }
    }

    fn read_pose(&mut self, now: u64, read_ns: u64, drawn_ms: &mut f32) {
        if self.pose_feed.is_none() && now >= self.pose_retry_ms {
            match PoseFeed::open(&self.prefix, &self.seqlock) {
                Ok(feed) => {
                    self.pose_feed = Some(feed);
                    self.half_w = 0.0; // pick up the driver's FOV
                }
                Err(why) => self.pose_why = why,
            }
            self.pose_retry_ms = now + RETRY_MS;
        }
        // Opened apart from the feed: the driver may create it later, and a restarted driver makes a new segment, which
        // leaves the old one stale (no fresh motion below) until it is reopened here.
        if self.pose_feed.is_some() && !self.motion_live && now >= self.motion_retry_ms {
            self.motion_feed = MotionFeed::open(&self.prefix);
            self.motion_retry_ms = now + RETRY_MS;
        }
        let sample = self.pose_feed.as_mut().and_then(PoseFeed::read);
        if let Some(s) = &sample {
            *drawn_ms = s.device_ms;
        }
        let mut stabilized = sample;
        match &mut stabilized {
            Some(s) => s.orientation = self.stabilization.update(s.orientation, f64::from(s.device_ms)),
            None => self.stabilization.reset(),
        }
        let now_f = now as f64;
        self.pose.update(stabilized, now_f);
        let lost = self.pose.reconnecting(now_f);
        if self.hint.update(lost, now_f) && lost {
            self.hints += 1;
        }
        let mut relative = self.pose.relative();
        let age = sample.and_then(|s| self.pose_age.observe(read_ns as f64 / 1e6, s.device_ms));
        if self.prediction && self.pose.tracking() {
            // never extrapolate a held (stale) pose
            // D3: the Luma driver's gyro motion when it publishes one, timed from that sample's real arrival; otherwise
            // (XRLinuxDriver, or a wire with no fresh sample) the velocity between the feed's two newest samples.
            let motion = if sample.is_some() { self.motion_feed.as_ref().and_then(MotionFeed::read) } else { None };
            let motion_age = motion.as_ref().and_then(|m| motion_age_ms(m, read_ns));
            self.motion_live = motion_age.is_some();
            let omega = match (&sample, motion_age) {
                (Some(s), None) => angular_velocity(&history_of(s)),
                _ => None,
            };
            let frametime = if self.refresh_ns != 0 { f64::from(self.refresh_ns) / 1e6 } else { 1000.0 / 120.0 };
            let cfg = self
                .pose_feed
                .as_ref()
                .and_then(|f| look_ahead_from(&f.look_ahead_cfg().unwrap_or([0.0; 4])));
            let horizon = prediction_horizon(
                cfg.as_ref(),
                self.latency.expected_ms(),
                motion_age.unwrap_or(age.unwrap_or(0.0)),
                frametime,
            );
            match (horizon, &sample) {
                (Some(h), Some(s)) if self.smooth_ms > 0.0 => {
                    // Horizon from the feed's bound; the pose is the stabilized sample, so a still head stays still. A
                    // live wire (the Rust bridge's, D12) gives the velocity: estimated from every sample, braked and
                    // still-gated there.
                    let mut measured = None;
                    if let (Some(m), Some(_)) = (&motion, motion_age) {
                        let w = m.omega_rad_s;
                        measured = Some([w[0] / 1000.0, w[1] / 1000.0, w[2] / 1000.0]);
                        self.wire_frames += 1;
                    }
                    relative = self.smoother.update(relative, s.device_ms, h, read_ns as f64 / 1e6, measured);
                    self.last_horizon_ms = self.smoother.last_horizon_ms();
                    self.last_predicted_deg = self.smoother.last_predicted_deg();
                    self.predicted_frames += 1;
                    self.smoothed_frames += 1;
                }
                (Some(h), _) if motion_age.is_some() || omega.is_some() => {
                    // The stabilizer's rest gate shortens the horizon to hide the finite difference's noise. The wire
                    // has its own gate (luma::PredictConfig::still_gate, on the gyro), and the stabilizer's would
                    // triple its error in slow turns.
                    let delta = match (&motion, motion_age, &omega) {
                        (Some(m), Some(_), _) => {
                            self.wire_frames += 1;
                            motion_delta(m, h, MAX_PREDICTION_DEG)
                        }
                        (_, _, Some(w)) => {
                            prediction_delta(w, h * self.stabilization.prediction_gain(), MAX_PREDICTION_DEG)
                        }
                        _ => unreachable!("motion or omega"),
                    };
                    relative = quat_mul(relative, delta); // body frame: newest sample * delta
                    self.last_horizon_ms = h;
                    self.last_predicted_deg = quat_angle(Quat::default(), delta) * (180.0 / std::f64::consts::PI) as f32;
                    self.predicted_frames += 1;
                }
                _ => self.unpredicted_frames += 1,
            }
        } else if self.smooth_ms > 0.0 {
            self.smoother.reset(); // off (Ctrl+Alt+P) or lost: the raw pose, and a fresh start after
        }
        self.relative = relative;
    }

    /// The hint card, drawn again when its generation moves (accent, font).
    fn hint_card(&mut self) -> (u64, Rc<HudImage>) {
        let generation = self.hint.generation();
        if let Some((g, image)) = &self.hint_image {
            if *g == generation {
                return (generation, image.clone());
            }
        }
        let font = self.hint_font.as_ref().map(|f| f as &dyn HudTextMetrics);
        let image = Rc::new(render_hud_card(&tracking_hint_lines(), self.hint.accent(), font));
        self.hint_image = Some((generation, image.clone()));
        (generation, image)
    }

    /// The camera, once the layout is in `scene.own`: the landed switches' turns, the follow / locked aim, the
    /// viewing mode's anchor, and the switch's view turn. Sets `scene.head` (the desktops' view, as drawn),
    /// `scene.world` (VR video) and `scene.environment_world` (the background). Every frame, tracked or not.
    pub(super) fn camera(&mut self, scene: &mut Scene, view: &mut ViewAnimator) {
        self.land_turns(scene, view);
        self.capture_centre(view);
        let mut head = IDENTITY;
        // Untracked, anchor mode's room is still turned by landed switches (ModeController::turn_anchor).
        if !self.requested && self.modes.mode() == ViewMode::Anchor {
            head = view_from(Quat::default(), self.modes.update(Quat::default(), monotonic_ms() as f64));
        }
        let mut world = head;
        let mut environment_world = IDENTITY;
        if self.requested {
            let (now, relative) = (self.now_ms, self.relative);
            let aim = self.focus_aim(scene, view);
            self.modes.set_aim(aim);
            // A maximize holds the canvas in the room while the desktop zooms in; follow / locked take over once it
            // lands.
            if self.modes.holding() && !(view.zoom() < 1.0) {
                self.modes.land_maximize(now);
            }
            head = view_from(relative, self.modes.update(relative, now));
            world = view_from(relative, Quat::default());
            environment_world = environment_view(relative, self.pose.reference());
        }
        self.last_head = head;
        self.last_drawn = self.turned(&head, scene, view);
        scene.head = self.last_drawn;
        scene.world = world;
        scene.environment_world = environment_world;
    }

    fn canvas(&self, scene: &Scene) -> Canvas {
        Canvas::new(scene.width as f32, scene.height as f32, self.half_w, self.half_h)
    }

    fn overview(scene: &Scene, view: &ViewAnimator) -> Vec<FRect> {
        overview_layout(&scene.sizes, scene.width as f32, scene.height as f32, view.spacing())
    }

    /// Follow and locked modes centre the view on the focused desktop wherever it was dragged: the canvas turns, never
    /// a placement. Identity in the overview. An edge pan between maximized desktops aims by itself.
    fn focus_aim(&self, scene: &Scene, view: &ViewAnimator) -> Quat {
        let w = focus_weight(view);
        let maximized_pan = view.pan_active() && !view.pan_overview(); // the overview's turns the room instead (turned)
        if (!(w > 0.0) && !maximized_pan) || self.half_w == 0.0 {
            return Quat::default();
        }
        let overview = Self::overview(scene, view);
        let c = self.canvas(scene);
        let ps = &scene.own;
        if maximized_pan {
            let pan = view.pan();
            let from = focus_direction(&c, &overview, ps, pan.from as usize);
            let to = focus_direction(&c, &overview, ps, pan.to as usize);
            return aim_along(aim_rotation(from[0], from[1]), aim_rotation(to[0], to[1]), pan.way, view.pan_turn(), pan.side);
        }
        let d = focus_direction(&c, &overview, ps, view.focus() as usize);
        aim_rotation(w * d[0], w * d[1])
    }

    /// A centred switch turns from the direction at the centre of the view when it began.
    fn capture_centre(&mut self, view: &ViewAnimator) {
        if !view.turn_centred() || (self.centre.valid && self.centre.serial == view.serial()) {
            return;
        }
        // the turn holds 0 until phase 2: nothing drawn turned yet
        self.centre = Centre { valid: true, serial: view.serial(), dir: view_centre(&self.last_drawn) };
    }

    fn centre_of(&self, serial: u32) -> [f32; 2] {
        if self.centre.valid && self.centre.serial == serial { self.centre.dir } else { view_centre(&self.last_drawn) }
    }

    /// A landed desktop switch's turn becomes the room's in anchor mode, so the view stays on the target.
    fn land_turns(&mut self, scene: &Scene, view: &mut ViewAnimator) {
        let landed = view.take_landed_turns();
        if landed.is_empty() || self.modes.mode() != ViewMode::Anchor || self.half_w == 0.0 {
            return;
        }
        let overview = Self::overview(scene, view);
        let c = self.canvas(scene);
        let ps = &scene.own;
        for l in &landed {
            // in order: turns do not commute
            let a = if l.centred { self.centre_of(l.serial) } else { focus_direction(&c, &overview, ps, l.from as usize) };
            let b = focus_direction(&c, &overview, ps, l.to as usize);
            self.modes.turn_anchor(quat_mul(quat_conj(aim_rotation(a[0], a[1])), aim_rotation(b[0], b[1])));
        }
    }

    /// The overview's edge pan turns the room about the dot's path's axis by the hand's turn, and landing, on to the
    /// centred turn that puts the target where the centre of the view was.
    fn overview_pan_turn(&self, scene: &Scene, view: &ViewAnimator) -> Mat3 {
        let overview = Self::overview(scene, view);
        let c = self.canvas(scene);
        let mut q = Quat::default();
        if let Some(path) = edge_pan_path(&c, &scene.sizes, view.spacing(), &overview, &scene.own, view.pan()) {
            let s = (-view.pan_turn() / 2.0).sin();
            q = Quat { x: path.axis.x * s, y: path.axis.y * s, z: path.axis.z * s, w: (-view.pan_turn() / 2.0).cos() };
        }
        if view.pan_landing() {
            let to = look_turn_from(&c, &overview, &scene.own, self.centre_of(view.serial()), view.pan().to as usize, 1.0);
            q = quat_slerp(q, mat3_to_quat(&to), view.pan_land());
        }
        quat_to_mat3(q)
    }

    /// A desktop switch's view turn applied to this frame's head rotation (anchor mode only: follow and locked already
    /// turn to the target through the aim). Identity turn at rest.
    fn turned(&self, head: &Mat3, scene: &Scene, view: &ViewAnimator) -> Mat3 {
        let anchor = self.modes.mode() == ViewMode::Anchor;
        let overview_pan = view.pan_overview() && anchor && self.half_w > 0.0;
        if (!(view.turn() > 0.0) && !overview_pan) || !anchor {
            return *head;
        }
        let overview = Self::overview(scene, view);
        let c = self.canvas(scene);
        let m = if overview_pan {
            self.overview_pan_turn(scene, view)
        } else if view.turn_centred() {
            look_turn_from(&c, &overview, &scene.own, self.centre_of(view.serial()), view.look_to() as usize, view.turn())
        } else {
            look_turn(&c, &overview, &scene.own, view.look_from() as usize, view.look_to() as usize, view.turn())
        };
        mul(head, &m)
    }

    /// The tracking fields of a continuity snapshot (wayland.cpp save_continuity): the pose, stabilization, viewing
    /// mode, the last maximize, the view last drawn and a centred switch's start.
    pub(super) fn save_continuity(&self, c: &mut ViewContinuity) {
        c.pose = self.pose.clone();
        c.stabilization = self.stabilization.clone();
        c.modes = self.modes.clone();
        c.maximized = self.maximized;
        c.last_drawn = self.last_drawn;
        c.centre_valid = self.centre.valid;
        c.centre_serial = self.centre.serial;
        c.centre_dir = self.centre.dir;
    }

    /// The same fields back from a restored snapshot (wayland.cpp restore_continuity).
    pub(super) fn restore_continuity(&mut self, c: &ViewContinuity) {
        self.pose = c.pose.clone();
        self.stabilization = c.stabilization.clone();
        self.modes = c.modes.clone();
        self.maximized = c.maximized;
        self.last_drawn = c.last_drawn;
        self.centre = Centre { valid: c.centre_valid, serial: c.centre_serial, dir: c.centre_dir };
    }

    /// The theme colour of the reconnecting hint (wayland.cpp set_accent).
    pub(super) fn set_accent(&mut self, seed: [f32; 3]) {
        self.hint.set_accent(seed);
    }

    /// The world's up in the canvas frame when a desktop's tilt is measured (wayland.cpp take_roll_geometry's
    /// `measured`, less its video test): tracking with a level reference, anchor mode settled.
    pub(super) fn roll_up(&self) -> Option<[f32; 3]> {
        let anchored = self.pose.tracking() && self.modes.mode() == ViewMode::Anchor && self.modes.settled();
        self.pose.reference().filter(|_| anchored).map(|r| canvas_up(r, self.modes.anchor()))
    }

    /// zoomed_placements' keep_size: anchor mode and maximized (wayland.cpp resolved_placements).
    pub(super) fn keep_size(&self) -> bool {
        self.modes.mode() == ViewMode::Anchor && self.maximized
    }

    /// Head tracking redraws on every frame callback.
    pub(super) fn animating(&self, now_ms: f64) -> bool {
        self.requested || self.hint.animating(now_ms)
    }

    /// The head before this frame's switch turn (the help card's hit box is worked out on the canvas as drawn).
    pub(super) fn last_head(&self) -> &Mat3 {
        &self.last_head
    }

    // ---- The latency hooks (shell.rs: the presentation clock, commit and the feedback events).

    pub(super) fn clock(&mut self, id: u32) {
        self.latency.clock(id);
    }

    /// The read time of the frame being committed, once.
    pub(super) fn take_read(&mut self) -> Option<(u64, f32)> {
        self.read.take()
    }

    pub(super) fn read(&mut self, ticket: u64, read_ns: u64) {
        self.latency.read(ticket, read_ns);
    }

    /// `sample_ms`: the device time of the pose that frame drew (-1: none). `refresh_ns`: Metrics' refresh prediction.
    pub(super) fn presented(&mut self, ticket: u64, present_ns: u64, refresh_ns: u32, sample_ms: f32) {
        self.refresh_ns = refresh_ns;
        self.latency.presented(ticket, present_ns);
        if self.latency.clock_ok() {
            // the driver's vsync is on CLOCK_MONOTONIC
            if let Some(feed) = &self.pose_feed {
                self.vsync_meter.presented(feed.vsync().as_ref(), present_ns, sample_ms);
            }
        }
    }

    pub(super) fn discarded(&mut self, ticket: u64) {
        self.latency.discarded(ticket);
    }

    pub(super) fn abandon(&mut self) {
        self.latency.abandon();
    }

    /// `tracking` (only when tracking was asked for), `vsync_error_ms` and `sensor_to_vsync_ms` (only while the driver's
    /// vsync is measured) and `pose_age_ms` (only when the newest sample came from the
    /// seqlock channel, whose time is CLOCK_MONOTONIC; the mutex feed's age is against an unknown clock offset).
    pub(super) fn status(&self, extras: &mut StatusExtras) {
        if self.requested {
            extras.tracking = Some(self.pose.tracking());
        }
        // Our driver's scan-out against wp_presentation over the last presents, only while it publishes pose_vsync
        // (--read-events): the horizon error p95, and the median sample age at scan-out.
        let v = self.vsync_meter.summary();
        if v.count > 0 {
            extras.vsync_error_ms = Some(v.error_p95_ms);
            if v.sensor_count > 0 {
                extras.sensor_to_vsync_ms = Some(v.sensor_p50_ms);
            }
        }
        if let Some(feed) = &self.pose_feed {
            let r = feed.stats();
            if r.seqlock_source {
                extras.pose_age_ns = Some(r.last_age_ns);
            }
        }
    }

    /// The exit report's `tracking` section.
    pub(super) fn report_tracking(&self, out: &mut String) {
        let mode = match self.modes.mode() {
            ViewMode::Locked => "locked",
            ViewMode::Follow => "follow",
            ViewMode::Anchor => "anchor",
        };
        let source = if self.supervised_projection > 0.0 {
            "pro2-supervised-approximate"
        } else if self.fov_from_driver {
            "driver"
        } else {
            "fallback-46"
        };
        out.push_str(&format!(
            ",\"tracking\":{{\"requested\":{},\"mode\":\"{mode}\",\"feed\":{},\"tracking\":{},\"samples\":{},\
             \"lost_events\":{},\"reanchors\":{},\"hints\":{},\"diagonal_fov_deg\":{},\"levelled_deg\":{},\
             \"fov_source\":\"{source}\",\"half_fov_tan\":[{},{}]}}",
            self.requested,
            self.pose_feed.is_some(),
            self.pose.tracking(),
            self.pose.samples(),
            self.pose.lost_events(),
            self.pose.reanchors(),
            self.hints,
            f6(self.fov_deg),
            f6(self.pose.levelled_deg()),
            f6(self.half_w),
            f6(self.half_h),
        ));
    }

    /// The exit report's `latency` section: software read -> present, the pose ages and reader, prediction (and the
    /// smoother), late latch (off: task 6) and the driver's vsync.
    pub(super) fn report_latency(&self, out: &mut String) {
        let feed = self.pose_feed.as_ref();
        let cfg_json = match feed.and_then(PoseFeed::look_ahead_cfg) {
            Some(c) if look_ahead_from(&c).is_some() => format!("[{},{},{},{}]", f6(c[0]), f6(c[1]), f6(c[2]), f6(c[3])),
            _ => "null".to_string(),
        };
        out.push_str(&format!(
            ",\"latency\":{{\"kind\":\"software_read_to_present\",\"clock_monotonic\":{},\"read_to_present\":{},\
             \"pose_age_relative\":{}",
            self.latency.clock_ok(),
            stats_json(&self.latency.summary()),
            stats_json(&self.pose_age.summary())
        ));
        // pose_age_absolute (seqlock reads: read time minus the sample's CLOCK_MONOTONIC time) and pose_reader.
        let r = feed.map(PoseFeed::stats).unwrap_or_default();
        let ms = |ns: u64| f6(ns as f64 / 1e6);
        let source = match feed {
            None => "none",
            Some(_) if r.seqlock_source => "seqlock",
            Some(_) => "mutex",
        };
        out.push_str(&format!(
            ",\"pose_age_absolute\":{{\"count\":{},\"p50_ms\":{},\"p95_ms\":{},\"p99_ms\":{},\"max_ms\":{}}},\
             \"pose_reader\":{{\"source\":\"{source}\",\"seqlock_attached\":{},\"seqlock_reads\":{},\"mutex_reads\":{},\
             \"waits\":{},\"retries\":{},\"switches\":{},\"read_p99_us\":{},\"read_max_us\":{}}}",
            r.age_count,
            ms(r.age_p50_ns),
            ms(r.age_p95_ns),
            ms(r.age_p99_ns),
            ms(r.age_max_ns),
            r.seqlock_attached,
            r.seqlock_reads,
            r.mutex_reads,
            r.waits,
            r.retries,
            r.switches,
            f6(r.read_p99_ns as f64 / 1e3),
            f6(r.read_max_ns as f64 / 1e3),
        ));
        out.push_str(&format!(
            ",\"prediction\":{{\"enabled\":{},\"predicted_frames\":{},\"unpredicted_frames\":{},\"wire_frames\":{},\
             \"last_horizon_ms\":{},\"last_predicted_deg\":{},\"max_deg\":{},\"look_ahead_cfg\":{cfg_json}",
            self.prediction,
            self.predicted_frames,
            self.unpredicted_frames,
            self.wire_frames,
            f6(self.last_horizon_ms),
            f6(self.last_predicted_deg),
            f6(MAX_PREDICTION_DEG),
        ));
        if self.smooth_ms > 0.0 {
            let s = &self.smoother;
            out.push_str(&format!(
                ",\"smoothing\":{{\"tau_ms\":{},\"frames\":{},\"snaps\":{},\"max_predicted_deg\":{},\
                 \"turn_axis_forward_deg\":{},\"turn_axis_left_deg\":{},\"turn_seconds\":{},\"capped_frames\":{},\
                 \"peak_speed_dps\":{},\"last_correction_deg\":{}}}",
                f6(self.smooth_ms),
                self.smoothed_frames,
                s.snaps(),
                f6(s.max_predicted_deg()),
                f6(s.turn_axis_forward_deg()),
                f6(s.turn_axis_left_deg()),
                f6(s.turn_seconds()),
                s.capped_frames(),
                f6(s.peak_speed_dps()),
                f6(s.last_correction_deg()),
            ));
        }
        out.push('}');
        // D4 late latch (plan task 6): off in this build, as the C++ without --late-latch-guard-ms.
        out.push_str(",\"late_latch\":{\"enabled\":false}");
        // The driver's scan-out against wp_presentation: only with our driver and --read-events.
        let v = self.vsync_meter.summary();
        out.push_str(&format!(
            ",\"vsync\":{{\"frames\":{},\"skipped\":{},\"period_ms\":{},\"offset_ms\":{},\"horizon_error\":{{\
             \"p50_ms\":{},\"p95_ms\":{},\"max_ms\":{}}},\"sensor_to_vsync\":{{\"count\":{},\"p50_ms\":{},\
             \"p95_ms\":{},\"max_ms\":{}}}}}}}",
            v.count,
            v.skipped,
            f6(v.period_ms),
            f6(v.offset_ms),
            f6(v.error_p50_ms),
            f6(v.error_p95_ms),
            f6(v.error_max_ms),
            v.sensor_count,
            f6(v.sensor_p50_ms),
            f6(v.sensor_p95_ms),
            f6(v.sensor_max_ms),
        ));
    }
}

impl Presentation {
    /// World-anchored 3DoF from the pose feed at `prefix` (GPU composition only). `smooth_ms`: motion_smooth, 0 = off.
    pub fn enable_tracking(&mut self, prefix: &str, supervised_projection: f32, smooth_ms: f32, level_gravity: bool) {
        if !self.state.capture.gpu_composition() {
            return; // head tracking needs GPU composition
        }
        let t = &mut self.state.features.tracking;
        if let Some(params) = motion_smooth_params(smooth_ms) {
            t.smoother = MotionSmoother::new(params);
            t.smooth_ms = smooth_ms;
        }
        t.pose.level_reference(level_gravity); // a comparison only: the Pro 2's frame is its mount pitch, in the feed
        t.supervised_projection = supervised_projection;
        if supervised_projection > 0.0 {
            t.half_w = 0.0;
            self.state.features.cards.fov_reset();
        }
        // The channel has one fixed name, so only the production feed's renderer reads it; a test prefix gets its own
        // feed alone.
        t.seqlock = if prefix == POSE_PREFIX { PoseFeed::SEQLOCK_POSE_NAME.to_string() } else { String::new() };
        t.requested = true;
        t.prefix = prefix.to_string();
        // The hint's font (the pop-ups' family); the built-in pixel font without it.
        t.hint_font = HudFont::load(HUD_FONT_FAMILY).ok();
        t.hint.font_changed();
        self.state.capture.invalidate();
        self.state.cadence.invalidate();
    }

    pub fn recenter(&mut self) {
        let t = &mut self.state.features.tracking;
        t.stabilization.reset();
        t.smoother.reset();
        t.pose.recenter();
        t.modes.recenter();
        self.state.capture.invalidate();
    }

    /// The tracking half of the `view` op: the viewing mode (maximize-follow: a maximize or un-maximize changes it
    /// too, keeping and restoring the room), prediction and stabilization.
    pub(super) fn view_tracking(&mut self, v: &View) {
        let t = &mut self.state.features.tracking;
        let maximize = v.zoomed && !t.maximized;
        let unmaximize = !v.zoomed && t.maximized;
        let mode = match v.mode {
            WireMode::Locked => ViewMode::Locked,
            WireMode::Follow => ViewMode::Follow,
            WireMode::Anchor => ViewMode::Anchor,
        };
        t.prediction = v.prediction;
        t.stabilization.strength(v.stabilization as i32);
        let how = if maximize && mode != ViewMode::Anchor {
            Change::Maximize
        } else if unmaximize && mode == ViewMode::Anchor {
            Change::Restore
        } else {
            Change::Blend
        };
        t.modes.set_mode(mode, monotonic_ms() as f64, how);
        t.maximized = v.zoomed;
    }
}

#[cfg(test)]
mod tests {
    use super::{Tracking, split_fov};
    use xr_renderer::ipc::{StatusExtras, status_dict};
    use xr_renderer::pose::VsyncSnapshot;
    use xr_renderer::predict::VsyncMeter;

    /// golden/status_vsync.cpp's `run`: n presents at 90 Hz, each a little after its scan-out, drawing a sample `age_ms`
    /// before it (none when `sample` is false).
    fn run(m: &mut VsyncMeter, n: u32, age_ms: f64, sample: bool, feed_ms: f64) {
        let v = VsyncSnapshot {
            anchor_host_ns: 1_000_000_000,
            period_ns: 1e9 / 90.0,
            anchor_feed_ms: feed_ms,
            ..VsyncSnapshot::default()
        };
        let late_us = [2000.0, 2150.0, 1900.0, 2600.0, 2050.0];
        for k in 0..n {
            let at = v.anchor_host_ns + (f64::from(k) * v.period_ns + late_us[k as usize % 5] * 1e3) as u64;
            let drawn = if sample { (500.0 + f64::from(k) * v.period_ns / 1e6 - age_ms) as f32 } else { -1.0 };
            m.presented(Some(&v), at, drawn);
        }
    }

    #[test]
    fn the_vsync_status_fields_match_the_cpp() {
        let status = |m: VsyncMeter| {
            let t = Tracking { vsync_meter: m, ..Tracking::default() };
            let mut extras = StatusExtras::default();
            t.status(&mut extras);
            status_dict("{}", &extras)
        };
        let scenario = |n, age, sample, feed| {
            let mut m = VsyncMeter::new();
            run(&mut m, n, age, sample, feed);
            m
        };
        let mut skipped = VsyncMeter::new();
        skipped.presented(None, 5, 1.0);
        let got = [
            status(VsyncMeter::new()),
            status(skipped),
            status(scenario(20, 0.0, false, 500.0)),
            status(scenario(40, 12.25, true, 500.0)),
            status(scenario(40, -3.5, true, 500.0)),
            status(scenario(10, 7.0, true, f64::NAN)),
            status(scenario(1, 30.0, true, 500.0)),
        ];
        let want: Vec<&str> = include_str!("../../golden/status_vsync.txt").lines().collect();
        assert_eq!(got.iter().map(String::as_str).collect::<Vec<_>>(), want);
    }

    #[test]
    fn the_fov_fills_the_diagonal() {
        let (hw, hh) = split_fov(46.0, 1920, 1080);
        let diag = (hw * hw + hh * hh).sqrt();
        assert!((diag - (23.0f32).to_radians().tan()).abs() < 1e-6);
        assert!((hw / hh - 1920.0 / 1080.0).abs() < 1e-5);
        // No height yet: 16:10, as the C++.
        let (hw, hh) = split_fov(46.0, 0, 0);
        assert!((hw / hh - 1.6).abs() < 1e-5);
    }
}
