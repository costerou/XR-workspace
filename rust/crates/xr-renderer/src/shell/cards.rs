//! The head-locked cards: the keyboard help card and its geometry line, the key bind panel, and the mode pop-ups of
//! the `view` op (wayland.cpp set_help, take_help_geometry, resend_help_geometry, set_binds and set_view's pop-ups;
//! `hud.rs`, `hud_font.rs`, `binds_panel.rs` hold the ported rasters).
//!
//! The overlays keep the C++ state (fade, page, scroll, generation); the C++ also rasterises each card when its
//! generation bumps. Here the card is rasterised the first time a frame (or the help geometry) needs that generation,
//! from the same state, so the pixels are the C++ ones.

use std::rc::Rc;

use xr_renderer::binds_panel::{BindPanelOverlay, BindPanelState, BindRow, BindRowState, render_bind_panel};
use xr_renderer::hud::{
    HUD_FONT_FAMILY, HelpOverlay, HelpRow, HudImage, HudTextMetrics, StatusPopups, centered_card_origin,
    help_geometry_json, render_help_card, render_hud_card,
};
use xr_renderer::hud_font::HudFont;
use xr_renderer::pose::{IDENTITY, Mat3};
use xr_wire::control::{BindState, Binds, View, ViewMode};

use super::scene::{CardInput, Scene};
use super::{Presentation, monotonic_ms_f64};

/// A moving head re-sends the help geometry at most this often (Presentation::kHelpGeometryMs).
pub const HELP_GEOMETRY_MS: f64 = 40.0;

const NULL_LINE: &str = "{\"help_geometry\":null}";
const CARD_PREFIX: &str = "{\"help_geometry\":{";

/// One card's raster, kept for the generation it was drawn for.
#[derive(Default)]
struct Raster {
    generation: u64,
    image: Option<Rc<HudImage>>,
}

impl Raster {
    fn get(&mut self, generation: u64, draw: impl FnOnce() -> HudImage) -> Rc<HudImage> {
        match &self.image {
            Some(image) if self.generation == generation => image.clone(),
            _ => {
                let image = Rc::new(draw());
                self.generation = generation;
                self.image = Some(image.clone());
                image
            }
        }
    }
}

/// The help geometry line's send state (help_geometry_sent, help_geometry_ms).
struct GeometrySent {
    line: String,
    ms: f64,
}

impl Default for GeometrySent {
    fn default() -> Self {
        Self { line: String::new(), ms: -1e9 }
    }
}

impl GeometrySent {
    /// Presentation::take_help_geometry's sending rule: `up` is whether the card is on the overview, `build` the card's
    /// line (empty: the canvas is unusable).
    fn take(&mut self, up: bool, now_ms: f64, build: impl FnOnce() -> String) -> Option<String> {
        // The two common cases return what the full path below would, without building a line: the card is down and
        // "null" was already sent, or it stays up inside the rate limit (equal line or not, no send).
        if !up && self.line == NULL_LINE {
            return None;
        }
        let still_up = up && self.line.starts_with(CARD_PREFIX);
        if still_up && now_ms - self.ms < HELP_GEOMETRY_MS {
            return None;
        }
        let mut line = String::from(NULL_LINE);
        if up {
            line = build();
            if line.is_empty() {
                line = String::from(NULL_LINE);
            }
        }
        if line == self.line {
            return None;
        }
        // While the card stays up, changes are rate-limited (head motion, mostly; a new page size is at most
        // HELP_GEOMETRY_MS late, the main loop retries every pass). A show or hide goes out at once.
        if still_up && now_ms - self.ms < HELP_GEOMETRY_MS {
            return None;
        }
        self.line.clone_from(&line);
        self.ms = now_ms;
        Some(line)
    }
}

pub(super) struct Cards {
    /// Viewing mode / cursor follow / prediction / typing pop-ups, and their font (shared with the tracking hint).
    popups: StatusPopups,
    popup_font: Option<HudFont>,
    /// Reported: the loaded family, or why the pixel font is used ("none": no GPU composition).
    popup_font_report: String,
    help: HelpOverlay,
    binds: BindPanelOverlay,
    popup_raster: Raster,
    help_raster: Raster,
    binds_raster: Raster,
    /// The output surface the help card and the bind panel fit (set_viewport).
    viewport: (u32, u32),
    /// The last frame showed the card: draw once more so it clears.
    popup_drawn: bool,
    help_drawn: bool,
    binds_drawn: bool,
    /// The `view` op's last zoomed (Impl::maximized): a maximize or un-maximize changes the mode without a pop-up.
    maximized: bool,
    /// The head rotation of the last desktop frame and its FOV split (0: not yet worked out since the configure).
    last_head: Mat3,
    half_w: f32,
    half_h: f32,
    geometry: GeometrySent,
}

impl Default for Cards {
    fn default() -> Self {
        Self {
            popups: StatusPopups::new(),
            popup_font: None,
            popup_font_report: String::from("none"),
            help: HelpOverlay::new(),
            binds: BindPanelOverlay::new(),
            popup_raster: Raster::default(),
            help_raster: Raster::default(),
            binds_raster: Raster::default(),
            viewport: (0, 0),
            popup_drawn: false,
            help_drawn: false,
            binds_drawn: false,
            maximized: false,
            last_head: IDENTITY,
            half_w: 0.0,
            half_h: 0.0,
            geometry: GeometrySent::default(),
        }
    }
}

impl Cards {
    /// The GPU composition is up: the cards' fonts, each its own instance (the C++ open, after the first allocation).
    pub(super) fn load_fonts(&mut self) {
        match HudFont::load(HUD_FONT_FAMILY) {
            Ok(font) => {
                self.popup_font_report = font.description().to_owned();
                self.popup_font = Some(font);
            }
            Err(why) => {
                self.popup_font_report = format!("pixel-fallback: {why}");
                self.popup_font = None;
            }
        }
        self.help.set_font(HudFont::load(HUD_FONT_FAMILY).ok().map(|f| Box::new(f) as Box<dyn HudTextMetrics>));
        self.binds.set_font(HudFont::load(HUD_FONT_FAMILY).ok().map(|f| Box::new(f) as Box<dyn HudTextMetrics>));
    }

    /// The layer surface was configured (the canvas width and the output height): the help card's text size comes from
    /// the output height, and the FOV split is worked out again by the next frame.
    pub(super) fn configured(&mut self, width: u32, height: u32) {
        self.viewport = (width, height);
        self.help.set_viewport(width, height);
        self.binds.set_viewport(width, height);
        self.half_w = 0.0;
    }

    /// The pop-ups' font, for the tracking hint (None: the pixel font).
    #[allow(dead_code)] // the tracking hint's card is tracking.rs's
    pub(super) fn popup_font(&self) -> Option<&dyn HudTextMetrics> {
        self.popup_font.as_ref().map(|f| f as &dyn HudTextMetrics)
    }

    /// The theme colour (Presentation::set_accent's share): the pop-ups and the cards, redrawn if showing.
    pub(super) fn set_accent(&mut self, seed: [f32; 3], now_ms: f64) -> bool {
        let popup = self.popups.set_accent(seed, now_ms);
        let help = self.help.set_accent(seed);
        let binds = self.binds.set_accent(seed);
        popup || help || binds
    }

    /// set_view's pop-ups; true when a pop-up (re)started.
    fn view(&mut self, v: &View, now_ms: f64) -> bool {
        let mode = match v.mode {
            ViewMode::Anchor => 0,
            ViewMode::Locked => 1,
            ViewMode::Follow => 2,
        };
        if v.zoomed != self.maximized {
            self.popups.track_mode(mode);
        }
        self.maximized = v.zoomed;
        self.popups.update_all(mode, v.cursor_follow, v.prediction, v.typing, now_ms)
    }

    fn help_image(&mut self) -> Rc<HudImage> {
        let (help, (w, h)) = (&self.help, self.viewport);
        self.help_raster.get(help.generation(), || {
            render_help_card(help.rows(), help.accent(), help.font(), w, h, help.page()).0
        })
    }

    /// `scene.popup`, `scene.help`, `scene.binds`. The tracking hint, when it shows, is already on `scene.popup`
    /// (tracking contributes first); a mode pop-up goes first, and is brief.
    pub(super) fn contribute(&mut self, scene: &mut Scene) {
        let now = scene.now_ms;
        let popup = self.popups.alpha(now);
        let hint = scene.popup.as_ref().map_or(0.0, |c| c.alpha);
        if popup > 0.0 || hint <= 0.0 {
            let image = (popup > 0.0).then(|| {
                let (popups, font) = (&self.popups, self.popup_font.as_ref().map(|f| f as &dyn HudTextMetrics));
                self.popup_raster.get(popups.generation(), || render_hud_card(popups.lines(), popups.accent(), font))
            });
            scene.popup = Some(CardInput { image, generation: self.popups.generation(), alpha: popup, drop_px: 0.0 });
        }
        self.popup_drawn = popup > 0.0 || hint > 0.0;
        // The FOV split as this frame has it (update_fov runs before the render path).
        self.half_w = scene.half_w;
        self.half_h = scene.half_h;
        let help = self.help.alpha(now);
        let image = (help > 0.0).then(|| self.help_image());
        let (generation, drop_px) = (self.help.generation(), self.help.offset_px(now));
        scene.help = Some(CardInput { image, generation, alpha: help, drop_px });
        self.help_drawn = help > 0.0;
        let binds = self.binds.alpha(now);
        let image = (binds > 0.0).then(|| {
            let (b, (w, h)) = (&self.binds, self.viewport);
            self.binds_raster.get(b.generation(), || render_bind_panel(b.state(), b.accent(), b.font(), w, h, b.first()))
        });
        let (generation, drop_px) = (self.binds.generation(), self.binds.offset_px(now));
        scene.binds = Some(CardInput { image, generation, alpha: binds, drop_px });
        self.binds_drawn = binds > 0.0;
        // The help card's hit box is worked out on the canvas as drawn.
        self.last_head = scene.head;
    }

    pub(super) fn animating(&self, now_ms: f64) -> bool {
        self.popup_drawn
            || self.popups.active(now_ms)
            || self.help.animating(now_ms)
            || (self.help_drawn && !self.help.visible())
            || self.binds.animating(now_ms)
            || (self.binds_drawn && !self.binds.visible())
    }

    /// The exit report's `help` and `binds` sections.
    pub(super) fn report_help_binds(&self, out: &mut String) {
        let (h, b) = (&self.help, &self.binds);
        out.push_str(&format!(
            ",\"help\":{{\"visible\":{},\"shown\":{},\"hidden\":{},\"page\":{},\"pages\":{},\"turns\":{}}}",
            h.visible(),
            h.shown_count(),
            h.hidden_count(),
            h.page(),
            h.pages(),
            h.turns()
        ));
        out.push_str(&format!(
            ",\"binds\":{{\"visible\":{},\"shown\":{},\"hidden\":{},\"first\":{},\"slots\":{}}}",
            b.visible(),
            b.shown_count(),
            b.hidden_count(),
            b.first(),
            b.slots()
        ));
    }

    /// The exit report's `popup_font`: bounded, JSON-escaped (font paths come from fontconfig).
    pub(super) fn report_popup_font(&self, out: &mut String) {
        out.push_str(",\"popup_font\":\"");
        out.push_str(&popup_font_json(&self.popup_font_report));
        out.push('"');
    }
}

/// The C++ escape of the reported font: its first 256 bytes, `"` and `\` escaped, control bytes as `?`.
fn popup_font_json(font: &str) -> String {
    let bytes = &font.as_bytes()[..font.len().min(256)];
    let mut escaped = Vec::with_capacity(bytes.len());
    for &c in bytes {
        if c == b'"' || c == b'\\' {
            escaped.push(b'\\');
        }
        escaped.push(if c < 0x20 { b'?' } else { c });
    }
    String::from_utf8_lossy(&escaped).into_owned()
}

/// The wire `binds` payload as the C++ BindPanelState.
fn bind_panel_state(b: &Binds) -> BindPanelState {
    BindPanelState {
        visible: b.visible,
        title: b.title.clone(),
        hint: b.hint.clone(),
        status: b.status.clone(),
        rows: b
            .rows
            .iter()
            .map(|r| {
                let state = match r.state {
                    BindState::Normal => BindRowState::Normal,
                    BindState::Custom => BindRowState::Custom,
                    BindState::Conflict => BindRowState::Conflict,
                    BindState::Capture => BindRowState::Capture,
                };
                BindRow::new(r.action.clone(), r.keys.clone(), r.note.clone(), state)
            })
            .collect(),
        selected: b.selected,
        alert: b.alert,
    }
}

/// The help rows with each section's first row marked (`sections` add up to the rows; ipc checked).
fn help_rows(rows: &[(String, String)], sections: &[u32]) -> Vec<HelpRow> {
    let mut help: Vec<HelpRow> = rows.iter().map(|(keys, action)| HelpRow::new(keys.clone(), action.clone())).collect();
    let mut start = 0usize;
    for &n in sections {
        if start < help.len() {
            help[start].section = true;
        }
        start = start.wrapping_add(n as usize);
    }
    help
}

impl Presentation {
    /// Rows are (keys, action), `sections` their section sizes (empty: one), `turn` -1 / +1 flips a shown card's page.
    pub fn set_help(&mut self, visible: bool, rows: &[(String, String)], sections: &[u32], turn: i32) {
        let help = help_rows(rows, sections);
        if self.state.features.cards.help.set(visible, &help, monotonic_ms_f64(), turn) {
            self.state.capture.invalidate();
        }
    }

    /// `{"help_geometry":...}` when it changed, at most every HELP_GEOMETRY_MS while the head moves.
    pub fn take_help_geometry(&mut self, now_ms: f64) -> Option<String> {
        if !self.state.capture.gpu_composition() {
            return None;
        }
        let (width, height) = (self.state.width, self.state.height);
        let zoom = self.state.capture.zoom();
        let c = &mut self.state.features.cards;
        // The free pointer is overview-only.
        let up = c.help.visible() && c.help.generation() != 0 && zoom < 0.01 && c.half_w > 0.0;
        let mut sent = std::mem::take(&mut c.geometry);
        let line = sent.take(up, now_ms, || {
            let im = c.help_image();
            let origin = centered_card_origin(width, height, im.width, im.height, 0.0);
            help_geometry_json(&c.last_head, width, height, c.half_w, c.half_h, origin, im.width, im.height, c.help.controls())
        });
        c.geometry = sent;
        line
    }

    /// The last take was not delivered.
    pub fn resend_help_geometry(&mut self) {
        self.state.features.cards.geometry.line.clear();
    }

    /// The key bind panel (the wire `binds` payload is the C++ BindPanelState).
    pub fn set_binds(&mut self, state: &Binds) {
        if self.state.features.cards.binds.set(&bind_panel_state(state), monotonic_ms_f64()) {
            self.state.capture.invalidate();
        }
    }

    /// The pop-up half of the `view` op: viewing mode (first call, on change), cursor follow, prediction, typing. A
    /// maximize and an un-maximize change the mode as well, without a pop-up (only Ctrl+Alt+V's gets one).
    pub(super) fn view_popups(&mut self, v: &View) {
        self.state.features.cards.view(v, monotonic_ms_f64());
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const CARD: &str = r#"{"help_geometry":{"card":[1,2,3,4]}}"#;
    const MOVED: &str = r#"{"help_geometry":{"card":[2,2,3,4]}}"#;

    #[test]
    fn geometry_shows_at_once_and_rate_limits_while_up() {
        let mut g = GeometrySent::default();
        assert_eq!(g.take(true, 0.0, || CARD.into()).as_deref(), Some(CARD));
        assert_eq!(g.take(true, 10.0, || MOVED.into()), None, "inside the rate limit");
        assert_eq!(g.take(true, 39.9, || MOVED.into()), None);
        assert_eq!(g.take(true, 40.0, || MOVED.into()).as_deref(), Some(MOVED));
        assert_eq!(g.take(true, 100.0, || MOVED.into()), None, "unchanged");
        // A hide goes out at once, and only once.
        assert_eq!(g.take(false, 101.0, || unreachable!()).as_deref(), Some(NULL_LINE));
        assert_eq!(g.take(false, 102.0, || unreachable!()), None);
        // A show goes out at once too.
        assert_eq!(g.take(true, 103.0, || CARD.into()).as_deref(), Some(CARD));
    }

    #[test]
    fn geometry_first_take_down_sends_null_and_unusable_canvas_is_null() {
        let mut g = GeometrySent::default();
        assert_eq!(g.take(false, 0.0, || unreachable!()).as_deref(), Some(NULL_LINE));
        assert_eq!(g.take(true, 1.0, String::new), None, "an unusable canvas is the null already sent");
        // Resend: the next take goes out again.
        g.line.clear();
        assert_eq!(g.take(false, 2.0, || unreachable!()).as_deref(), Some(NULL_LINE));
    }

    #[test]
    fn popup_font_is_bounded_and_escaped() {
        assert_eq!(popup_font_json("a\"b\\c\nd"), "a\\\"b\\\\c?d");
        assert_eq!(popup_font_json(&"x".repeat(300)).len(), 256);
        assert_eq!(popup_font_json("none"), "none");
    }

    #[test]
    fn sections_mark_their_first_rows() {
        let rows: Vec<(String, String)> = (0..5).map(|i| (format!("k{i}"), format!("a{i}"))).collect();
        let marked: Vec<bool> = help_rows(&rows, &[2, 3]).iter().map(|r| r.section).collect();
        assert_eq!(marked, [true, false, true, false, false]);
        assert!(help_rows(&rows, &[]).iter().all(|r| !r.section));
    }

    #[test]
    fn a_maximize_changes_the_mode_without_a_pop_up() {
        let mut c = Cards::default();
        let view = |zoomed, mode| View {
            zoomed,
            selected: 0,
            spacing: 0,
            mode,
            cursor_follow: false,
            prediction: false,
            typing: false,
            center: false,
            cut: false,
            pan: false,
            stabilization: 25,
        };
        assert!(c.view(&view(false, ViewMode::Anchor), 0.0), "the first view shows the mode");
        assert!(!c.view(&view(true, ViewMode::Follow), 0.0), "maximize-follow: no pop-up");
        assert!(!c.view(&view(false, ViewMode::Anchor), 0.0), "un-maximize: no pop-up");
        assert!(c.view(&view(false, ViewMode::Locked), 0.0), "Ctrl+Alt+V: the mode card");
    }
}
