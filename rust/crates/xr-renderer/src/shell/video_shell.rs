//! VR video: the player, its controls card and quality menu, drawn instead of the desktops while a video is open
//! (wayland.cpp video_*, the render path's video branch, status_json's and capture_report's `video`, the owner
//! readback's `video`; video_mode.cpp's card half and video.cpp VideoControlsOverlay). `video/` holds the ported
//! player, sphere and card raster; `video::mode::Mode` the decoder and projection pass.

use std::cell::RefCell;
use std::os::fd::OwnedFd;
use std::rc::Rc;

use serde_json::{Map, Value};
use xr_renderer::gl::GlCompositor;
use xr_renderer::hud::{
    CardFade, HELP_FADE_IN_MS, HELP_FADE_OUT_MS, HUD_FONT_FAMILY, HudImage, HudTextMetrics,
};
use xr_renderer::hud_font::HudFont;
use xr_renderer::ipc::StatusExtras;
use xr_renderer::material::MD_DEFAULT_SEED;
use xr_renderer::video::card::{MenuRow, VideoCardState, render_video_card};
use xr_renderer::video::math::{Filter, Format, Projection, Quality, State, Stereo};
use xr_renderer::video::mode::Mode;
use xr_wire::control::{self as wire, VideoFilter, VideoFormat};
use xr_wire::status::Video;

use super::scene::Scene;
use super::{Presentation, monotonic_ms_f64};

/// The theme colour before the first `style` (wayland.cpp Impl::accent).
const ACCENT_DEFAULT: [u8; 3] = xr_renderer::material::MD_DEFAULT_SEED_U8;
/// The diagonal FOV when none is known (wayland.cpp's video branch: `s.fov_deg > 0 ? s.fov_deg : 46`).
const FOV_FALLBACK_DEG: f32 = 46.0;
/// The card's error line, bytes (video_mode.cpp prepare).
const DETAIL_MAX: usize = 64;

/// The controls card (video.cpp VideoControlsOverlay): rendered when shown and changed, faded as the help card.
struct ControlsOverlay {
    fade: CardFade,
    state: VideoCardState,
    dirty: bool,
    generation: u64,
    shown: u64,
    image: Rc<HudImage>,
    accent: [f32; 3],
    font: Option<HudFont>,
}

impl Default for ControlsOverlay {
    fn default() -> Self {
        Self {
            fade: CardFade::new(HELP_FADE_IN_MS, HELP_FADE_OUT_MS),
            state: VideoCardState::default(),
            dirty: true,
            generation: 0,
            shown: 0,
            image: Rc::default(),
            accent: MD_DEFAULT_SEED,
            font: None,
        }
    }
}

impl ControlsOverlay {
    /// True if visibility changed.
    fn set_visible(&mut self, visible: bool, now_ms: f64) -> bool {
        let changed = self.fade.set(visible, now_ms);
        if changed && visible {
            self.shown += 1;
            if self.dirty || self.generation == 0 {
                self.render();
            }
        }
        changed
    }

    /// Re-renders when visible and changed; true if redrawn.
    fn update(&mut self, state: VideoCardState) -> bool {
        if state != self.state {
            self.state = state;
            self.dirty = true;
        }
        if !self.dirty || !self.fade.visible() {
            return false;
        }
        self.render();
        true
    }

    fn render(&mut self) {
        let font = self.font.as_ref().map(|f| f as &dyn HudTextMetrics);
        self.image = Rc::new(render_video_card(&self.state, self.accent, font));
        self.dirty = false;
        self.generation += 1;
    }

    fn set_font(&mut self, font: Option<HudFont>) {
        self.font = font;
        self.dirty = true;
    }

    /// A change redraws the card on its next update. True if it changed.
    fn set_accent(&mut self, accent: [f32; 3]) -> bool {
        if accent == self.accent {
            return false;
        }
        self.accent = accent;
        self.dirty = true;
        true
    }

    fn alpha(&self, now_ms: f64) -> f32 {
        if self.generation != 0 {
            self.fade.alpha(now_ms)
        } else {
            0.0
        }
    }

    fn animating(&self, now_ms: f64) -> bool {
        self.generation != 0 && self.fade.animating(now_ms)
    }
}

/// The open video (video_mode.cpp VideoMode: the shell's half is the card, the title and the quality menu) and the
/// quality kept for the next one (VideoDefaults).
pub(super) struct VideoShell {
    /// Made on the first open, with the compositor's GL context.
    mode: Option<Mode>,
    card: ControlsOverlay,
    title: String,
    menu: bool,
    menu_rows: Vec<MenuRow>,
    menu_selected: u32,
    /// The last `quality` frame, for a mode made after it.
    defaults: Quality,
    /// The theme colour so far (`scene.accent`).
    accent: [u8; 3],
}

impl Default for VideoShell {
    fn default() -> Self {
        Self {
            mode: None,
            card: ControlsOverlay::default(),
            title: String::new(),
            menu: false,
            menu_rows: Vec::new(),
            menu_selected: 0,
            defaults: Quality::default(),
            accent: ACCENT_DEFAULT,
        }
    }
}

/// The M3 seed of a theme colour (wayland.cpp set_accent).
fn seed([r, g, b]: [u8; 3]) -> [f32; 3] {
    [
        f32::from(r) / 255.0,
        f32::from(g) / 255.0,
        f32::from(b) / 255.0,
    ]
}

fn format_of(f: &VideoFormat) -> Format {
    let projection = match f.projection {
        wire::Projection::Equirect360 => Projection::Equirect360,
        wire::Projection::Equirect180 => Projection::Equirect180,
        wire::Projection::Fisheye => Projection::Fisheye,
    };
    let stereo = match f.stereo {
        wire::Stereo::Mono => Stereo::Mono,
        wire::Stereo::Sbs => Stereo::Sbs,
        wire::Stereo::Tb => Stereo::Tb,
    };
    Format::new(projection, stereo, f.fov_deg)
}

fn filter_of(f: VideoFilter) -> Filter {
    match f {
        VideoFilter::CatmullRom => Filter::CatmullRom,
        VideoFilter::Sharp => Filter::Sharp,
        VideoFilter::Bilinear => Filter::Bilinear,
        VideoFilter::Nearest => Filter::Nearest,
    }
}

fn status_of(s: State) -> Video {
    match s {
        State::Off => Video::Off,
        State::Loading => Video::Loading,
        State::Playing => Video::Playing,
        State::Paused => Video::Paused,
        State::Ended => Video::Ended,
        State::Error => Video::Error,
    }
}


/// At most `max` bytes of `s`, cut at a character.
fn clip(s: &str, max: usize) -> String {
    let mut end = s.len().min(max);
    while !s.is_char_boundary(end) {
        end -= 1;
    }
    s[..end].to_owned()
}

/// `v` with every object's keys sorted, as nlohmann::json holds them (serde_json may preserve insertion order).
fn sorted(v: Value) -> Value {
    match v {
        Value::Object(o) => {
            let mut entries: Vec<(String, Value)> = o.into_iter().collect();
            entries.sort_by(|a, b| a.0.cmp(&b.0));
            Value::Object(
                entries
                    .into_iter()
                    .map(|(k, v)| (k, sorted(v)))
                    .collect::<Map<_, _>>(),
            )
        }
        Value::Array(a) => Value::Array(a.into_iter().map(sorted).collect()),
        v => v,
    }
}

impl VideoShell {
    /// A VR video is open: it draws in place of the desktops.
    pub(super) fn active(&self) -> bool {
        self.mode.as_ref().is_some_and(Mode::active)
    }

    /// Start `fd` on `mode` (video_mode.cpp open): the card fades in again, the menu closes.
    fn open(
        &mut self,
        gl: &Rc<glow::Context>,
        fd: OwnedFd,
        format: Format,
        title: &str,
        now_ms: f64,
    ) {
        if self.mode.is_none() {
            let mut mode = Mode::new(gl.clone());
            mode.set_quality(self.defaults);
            self.mode = Some(mode);
            self.card.set_font(HudFont::load(HUD_FONT_FAMILY).ok());
            self.card.set_accent(seed(self.accent)); // the theme colour so far
        }
        if self.active() {
            self.card.set_visible(false, 0.0); // as close(): the open fades the card in again
        }
        self.title = title.to_owned();
        if let Some(mode) = &mut self.mode {
            mode.open(fd, format, true);
        }
        self.menu = false;
        self.card.set_visible(true, now_ms);
        self.refresh_card(); // Mode::open decoded already (video_mode.cpp open's prepare)
    }

    fn close(&mut self) {
        if self.active() {
            self.card.set_visible(false, 0.0); // the next open fades it in again
        }
        if let Some(mode) = &mut self.mode {
            mode.close();
        }
        self.menu = false;
    }

    fn set_menu(&mut self, visible: bool, rows: &[(String, String)], selected: u32) {
        self.menu = visible && !rows.is_empty();
        self.menu_rows = rows
            .iter()
            .map(|(label, value)| MenuRow {
                label: label.clone(),
                value: value.clone(),
            })
            .collect();
        self.menu_selected = if self.menu_rows.is_empty() {
            0
        } else {
            selected.min(self.menu_rows.len() as u32 - 1)
        };
    }

    /// Before drawing a frame: decode and update, and refresh the card (video_mode.cpp prepare).
    fn prepare(&mut self) {
        if let Some(mode) = self.mode.as_mut().filter(|m| m.active()) {
            mode.prepare();
            self.refresh_card();
        }
    }

    /// The card shows the open video as it is now (re-rendered only on a change: VideoControlsOverlay::update).
    fn refresh_card(&mut self) {
        let Some(mode) = self.mode.as_ref().filter(|m| m.active()) else {
            return;
        };
        let state = mode.state();
        let mut c = VideoCardState {
            title: self.title.clone(),
            format: mode.format(),
            state,
            ..VideoCardState::default()
        };
        if self.menu {
            c.menu = true;
            c.menu_rows.clone_from(&self.menu_rows);
            c.menu_selected = self.menu_selected;
        }
        match mode.player() {
            Some(p) => {
                c.position_s = p.position_s().max(0.0) as u32;
                c.duration_s = p.duration_s().max(0.0) as u32;
                if state == State::Error {
                    c.detail = clip(p.error(), DETAIL_MAX);
                }
            }
            None => c.detail = clip(mode.error(), DETAIL_MAX),
        }
        self.card.update(c);
    }

    /// While a video is open: draw it (and its card) in place of the desktops and swap, Some(drawn ok). None: no
    /// video this frame, the desktops are drawn.
    pub(super) fn draw(&mut self, gl: &mut GlCompositor, scene: &Scene) -> Option<bool> {
        if let Some(accent) = scene.accent {
            self.accent = accent;
            if self.mode.is_some() {
                self.card.set_accent(seed(accent));
            }
        }
        if !self.active() {
            return None;
        }
        let now = monotonic_ms_f64();
        self.prepare();
        let alpha = self.card.alpha(now);
        let card = (alpha > 0.0).then(|| self.card.image.clone());
        let generation = self.card.generation;
        let fov = if scene.fov_deg > 0.0 { scene.fov_deg } else { FOV_FALLBACK_DEG };
        let world = scene.world;
        let mode = self.mode.as_mut()?;
        let ok = gl.draw_scene_and_swap(
            |gl, w, h| {
                // The background behind the video, in each eye's viewport: what a 180° or fisheye picture leaves of
                // the sphere.
                let gl = RefCell::new(gl);
                let behind =
                    |half_w: f32, half_h: f32| gl.borrow_mut().draw_environment(half_w, half_h);
                mode.draw(&world, fov, w, h, Some(&behind));
            },
            card.as_deref(),
            generation,
            alpha,
        );
        mode.after_swap();
        Some(ok)
    }

    /// A playing video draws every frame; the card fades.
    pub(super) fn animating(&self, now_ms: f64) -> bool {
        self.mode.is_some() && (self.active() || self.card.animating(now_ms))
    }

    /// `video` (left out while off).
    pub(super) fn status(&self, extras: &mut StatusExtras) {
        if let Some(mode) = &self.mode {
            extras.video = Some(status_of(mode.state())).filter(|v| *v != Video::Off);
        }
    }

    fn mode_report(&self) -> Option<String> {
        self.mode
            .as_ref()
            .map(|m| m.report_with_card(self.card.shown))
    }

    /// The exit report's `video` section.
    pub(super) fn report(&self, out: &mut String) {
        out.push_str(",\"video\":");
        out.push_str(self.mode_report().as_deref().unwrap_or("null"));
    }

    /// The owner readback's `video` value: the open video's report, or the next video's defaults, keys sorted.
    pub(super) fn owner_readback(&self, line: &mut String) {
        let report = self
            .mode_report()
            .unwrap_or_else(|| Mode::closed_report(self.defaults));
        let value = serde_json::from_str(&report).map_or(Value::Null, sorted);
        line.push_str(&value.to_string());
    }
}

impl Presentation {
    /// `fd` is the file the supervisor opened; ownership is taken, also when video is unavailable (no GPU
    /// composition: the descriptor is closed).
    pub fn video_open(&mut self, fd: OwnedFd, format: &VideoFormat, title: &str) {
        let Some(gl) = self.state.capture.gl_mut() else {
            return;
        }; // needs GPU composition
        let context = gl.gl().context().clone();
        self.state
            .features
            .video
            .open(&context, fd, format_of(format), title, monotonic_ms_f64());
        self.state.capture.invalidate();
    }

    pub fn video_close(&mut self) {
        let video = &mut self.state.features.video;
        if video.mode.is_some() {
            video.close();
            self.state.capture.invalidate();
        }
    }

    pub fn video_pause(&mut self) {
        if let Some(mode) = &mut self.state.features.video.mode {
            mode.toggle_pause();
        }
    }

    pub fn video_seek(&mut self, seconds: i32) {
        if let Some(mode) = &mut self.state.features.video.mode {
            mode.seek(f64::from(seconds));
        }
    }

    pub fn video_format(&mut self, format: &VideoFormat) {
        if let Some(mode) = &mut self.state.features.video.mode {
            mode.set_format(format_of(format));
            self.state.capture.invalidate();
        }
    }

    pub fn video_controls(&mut self, visible: bool) {
        let video = &mut self.state.features.video;
        if video.mode.is_some() {
            video.card.set_visible(visible, monotonic_ms_f64());
            self.state.capture.invalidate();
        }
    }

    /// Quality settings, applied live and kept for later videos (the C++ VideoQuality).
    pub fn video_quality(&mut self, filter: VideoFilter, deband: bool) {
        let video = &mut self.state.features.video;
        let quality = Quality {
            filter: filter_of(filter),
            deband,
        };
        video.defaults = quality; // kept for a mode made later (the first open)
        if let Some(mode) = &mut video.mode {
            mode.set_quality(quality);
            self.state.capture.invalidate();
        }
    }

    /// The quality menu on the controls card: rows (label, value), `selected` lit.
    pub fn video_menu(&mut self, visible: bool, rows: &[(String, String)], selected: u32) {
        let video = &mut self.state.features.video;
        if video.mode.is_some() {
            video.set_menu(visible, rows, selected);
            self.state.capture.invalidate();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn closed_defaults_read_back_sorted() {
        let mut shell = VideoShell::default();
        let mut line = String::new();
        shell.owner_readback(&mut line);
        let label = Format::default().label(true);
        assert_eq!(
            line,
            format!(
                "{{\"card_shown\":0,\"deband\":false,\"filter\":\"catmull-rom\",\"forced\":false,\"format\":\"{label}\",\
                 \"format_applied\":null,\"format_state\":{{\"fov\":180,\"projection\":\"360\",\"stereo\":\"mono\"}},\
                 \"frames_drawn\":0,\"opened\":0,\"playback\":{{\"command_error\":\"\",\"command_errors\":0,\
                 \"command_replies\":0,\"duration_s\":null,\"error\":\"\",\"paused\":null,\"position_s\":null,\
                 \"revision\":0,\"state\":\"off\"}},\"player\":null,\"quality_readback\":{{\"deband_applied\":null,\
                 \"deband_requested\":false,\"filter_applied\":null,\"filter_requested\":\"catmull-rom\"}},\
                 \"setting\":\"catmull-rom\",\"state\":\"off\"}}"
            )
        );
        // A `quality` before the first open: the next video's defaults.
        shell.defaults = Quality {
            filter: Filter::Nearest,
            deband: true,
        };
        line.clear();
        shell.owner_readback(&mut line);
        let v: Value = serde_json::from_str(&line).unwrap();
        assert_eq!(v["setting"], "nearest");
        assert_eq!(v["deband"], true);
        assert_eq!(v["quality_readback"]["filter_requested"], "nearest");
        assert_eq!(v["quality_readback"]["deband_requested"], true);
    }

    #[test]
    fn no_video_no_status_and_null_report() {
        let shell = VideoShell::default();
        let mut extras = StatusExtras::default();
        shell.status(&mut extras);
        assert_eq!(extras.video, None);
        let mut out = String::new();
        shell.report(&mut out);
        assert_eq!(out, ",\"video\":null");
        assert!(!shell.animating(0.0));
    }

    #[test]
    fn card_fades_and_counts_shows() {
        let mut card = ControlsOverlay::default();
        assert_eq!(card.alpha(0.0), 0.0); // never rendered
        assert!(card.set_visible(true, 100.0));
        assert!(!card.set_visible(true, 120.0));
        assert_eq!((card.shown, card.generation), (1, 1));
        assert!(card.animating(110.0));
        assert_eq!(card.alpha(100.0 + HELP_FADE_IN_MS), 1.0);
        assert!(!card.update(VideoCardState::default())); // unchanged: no render
        let playing = VideoCardState {
            state: State::Playing,
            ..VideoCardState::default()
        };
        assert!(card.update(playing.clone()));
        assert_eq!(card.generation, 2);
        card.set_visible(false, 1000.0);
        let paused = VideoCardState {
            state: State::Paused,
            ..playing
        };
        assert!(!card.update(paused)); // hidden: rendered when shown again
        assert!(card.set_visible(true, 2000.0));
        assert_eq!((card.shown, card.generation), (2, 3));
        assert!(card.set_accent([0.5, 0.25, 0.75]));
        assert!(!card.set_accent([0.5, 0.25, 0.75]));
    }

    #[test]
    fn menu_rows_clamp_and_close_when_empty() {
        let mut shell = VideoShell::default();
        let rows = vec![
            ("Filter".to_owned(), "Sharp".to_owned()),
            ("Deband".to_owned(), "On".to_owned()),
        ];
        shell.set_menu(true, &rows, 7);
        assert!(shell.menu);
        assert_eq!(shell.menu_selected, 1);
        shell.set_menu(true, &[], 3);
        assert!(!shell.menu);
        assert_eq!(shell.menu_selected, 0);
    }

    #[test]
    fn fov_and_wire_types() {
        let f = VideoFormat {
            projection: wire::Projection::Fisheye,
            stereo: wire::Stereo::Tb,
            fov_deg: 200,
        };
        assert_eq!(
            format_of(&f),
            Format::new(Projection::Fisheye, Stereo::Tb, 200)
        );
        assert_eq!(filter_of(VideoFilter::Bilinear), Filter::Bilinear);
        assert_eq!(status_of(State::Ended), Video::Ended);
        assert_eq!(clip("abcdef", 4), "abcd");
        assert_eq!(clip("aé", 2), "a");
        assert_eq!(seed([255, 0, 51]), [1.0, 0.0, 0.2]);
    }

    /// The real path on a surfaceless context: a clip (made here by ffmpeg) opened, drawn in place of the desktops
    /// with its card until it plays, reported, closed and opened again. Skipped without EGL/GLES or ffmpeg.
    #[test]
    fn draws_a_clip_in_place_of_the_desktops() {
        use std::process::Command;
        use std::time::{Duration, Instant};
        use xr_renderer::hud::Background;
        use xr_renderer::pose::IDENTITY;

        let Ok(mut gl) = GlCompositor::create_offscreen(128, 64, 0) else {
            eprintln!("SKIP: no surfaceless EGL / GLES context");
            return;
        };
        let clip = std::env::temp_dir().join(format!("xr-video-shell-{}.mkv", std::process::id()));
        let made = Command::new("ffmpeg")
            .args([
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
            ])
            .arg("testsrc=s=64x32:r=30:d=3")
            .args(["-pix_fmt", "yuv420p", "-c:v", "mpeg4"])
            .arg(&clip)
            .status()
            .is_ok_and(|s| s.success());
        if !made {
            eprintln!("SKIP: no ffmpeg");
            return;
        }
        let open = || -> OwnedFd { std::fs::File::open(&clip).unwrap().into() };
        let context = gl.gl().context().clone();
        let mut shell = VideoShell::default();
        let scene = Scene::new(0.0, (128, 64), (0.3, 0.15), Vec::new());
        gl.set_environment(1.0, Background::Grid, 100.0, 0.0, &IDENTITY);
        assert_eq!(
            shell.draw(&mut gl, &scene),
            None,
            "no video: the desktops are drawn"
        );

        shell.open(
            &context,
            open(),
            Format::default(),
            "Test clip",
            monotonic_ms_f64(),
        );
        assert!(shell.active() && shell.animating(0.0));
        let deadline = Instant::now() + Duration::from_secs(10);
        while shell.mode.as_ref().unwrap().state() != State::Playing && Instant::now() < deadline {
            assert_eq!(shell.draw(&mut gl, &scene), Some(true));
            std::thread::sleep(Duration::from_millis(4));
        }
        for _ in 0..10 {
            assert_eq!(shell.draw(&mut gl, &scene), Some(true));
        }
        let mut extras = StatusExtras::default();
        shell.status(&mut extras);
        assert_eq!(extras.video, Some(Video::Playing));
        assert!(
            gl.read_rgb().iter().any(|&b| b != 0),
            "the picture is on the buffer"
        );
        assert!(shell.card.generation >= 1 && shell.card.fade.visible());
        let mut out = String::new();
        shell.report(&mut out);
        let report: Value = serde_json::from_str(&out[",\"video\":".len()..]).unwrap();
        assert_eq!(report["opened"], 1);
        assert_eq!(report["card_shown"], 1);
        assert!(report["frames_drawn"].as_u64().unwrap() > 0);
        assert_eq!(report["playback"]["state"], "playing");
        let mut line = String::new();
        shell.owner_readback(&mut line);
        let readback: Value = serde_json::from_str(&line).unwrap();
        assert_eq!(
            readback, report,
            "the owner readback is the report, keys sorted"
        );
        let keys: Vec<&String> = readback.as_object().unwrap().keys().collect();
        assert!(keys.windows(2).all(|k| k[0] < k[1]));

        shell.close();
        assert!(!shell.active());
        assert_eq!(
            shell.draw(&mut gl, &scene),
            None,
            "closed: the desktops again"
        );
        let mut extras = StatusExtras::default();
        shell.status(&mut extras);
        assert_eq!(extras.video, None);
        shell.open(
            &context,
            open(),
            Format::default(),
            "Test clip",
            monotonic_ms_f64(),
        );
        assert_eq!(shell.card.shown, 2, "an open fades the card in again");
        assert_eq!(shell.draw(&mut gl, &scene), Some(true));
        shell.close();
        drop(shell); // the player and sphere go while the context is current
        let _ = std::fs::remove_file(&clip);
    }
}
