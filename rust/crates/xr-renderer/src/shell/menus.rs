//! The options panel and the placement panel, both opening from the overview's glass (wayland.cpp set_options_menu,
//! set_placement_menu and the render path's glass block; options_overlay.hpp and options_evidence.hpp are ported here;
//! `glass.rs`, `glass_raster.rs` and `options_geometry.rs` hold the rest of the logic and the rasters).

use std::rc::Rc;

use xr_renderer::glass::GlassPills;
use xr_renderer::glass_raster::{render_glass_panel, render_options_panel};
use xr_renderer::glass_theme::GLASS_THEME_DEFAULT;
use xr_renderer::hud::{
    CardFade, HUD_FONT_FAMILY, HudImage, HudTextMetrics, MENU_FADE_IN_MS, MENU_FADE_OUT_MS, MenuPlacement,
    PLACEMENT_MENU_BRIGHTNESS, PLACEMENT_MENU_DESKTOP_COUNT, PlacementMenuState,
};
use xr_renderer::hud_font::HudFont;
use xr_renderer::material::MdRgb;
use xr_renderer::options_geometry::json_string;
use xr_renderer::view::Placement;
use xr_wire::control::{MenuField, OptionsMenu, PlacementMenu};

use super::scene::Scene;
use super::{Presentation, monotonic_ms};

/// The C++ OptionsMenuState's defaults (ipc.hpp): closed, no monitor, nothing focused.
fn closed_options() -> OptionsMenu {
    OptionsMenu {
        open: false,
        namespace: String::new(),
        monitor: String::new(),
        scope: String::new(),
        category: String::new(),
        focus: -1,
        scroll: 0,
        revision: 0,
        status: String::new(),
        message: String::new(),
        rows: Vec::new(),
    }
}

/// options_overlay.hpp OptionsOverlay: the options panel's fade and the last visible snapshot, which survives a close
/// for the fade (the supervisor's logical state stops taking input at once). The image is rasterized lazily, once per
/// change, on the next frame that draws it.
struct OptionsOverlay {
    fade: CardFade,
    state: OptionsMenu,
    accent: MdRgb,
    hidden_ms: f64,
    generation: u64,
    renders: u64,
    dirty: bool,
    image: Option<Rc<HudImage>>,
}

impl Default for OptionsOverlay {
    fn default() -> Self {
        Self {
            fade: CardFade::new(MENU_FADE_IN_MS, MENU_FADE_OUT_MS),
            state: closed_options(),
            accent: GLASS_THEME_DEFAULT,
            hidden_ms: -1.0,
            generation: 0,
            renders: 0,
            dirty: false,
            image: None,
        }
    }
}

impl OptionsOverlay {
    fn set(&mut self, state: &OptionsMenu, now_ms: f64) -> bool {
        let mut changed = self.fade.set(state.open, now_ms);
        if state.open && (self.generation == 0 || *state != self.state) {
            self.state = state.clone();
            self.dirty = true;
            self.generation += 1;
            changed = true;
        }
        changed
    }

    fn set_accent(&mut self, accent: MdRgb) -> bool {
        if accent == self.accent {
            return false;
        }
        self.accent = accent;
        if self.generation == 0 {
            return false;
        }
        self.dirty = true;
        self.generation += 1;
        true
    }

    fn frame(&mut self, overview: bool, now_ms: f64) -> f32 {
        if !overview {
            if self.hidden_ms >= 0.0 && self.fade.visible() {
                self.fade.hold(self.hidden_ms, now_ms);
            }
            self.hidden_ms = now_ms;
            return 0.0;
        }
        if self.hidden_ms >= 0.0 && self.fade.visible() {
            self.fade.hold(self.hidden_ms, now_ms);
        }
        self.hidden_ms = -1.0;
        if self.generation != 0 { self.fade.alpha(now_ms) } else { 0.0 }
    }

    fn needs_frame(&self, overview: bool, was_drawn: bool, now_ms: f64) -> bool {
        (overview && self.fade.animating(now_ms)) || (was_drawn && (!overview || !self.fade.visible()))
    }

    fn image(&mut self, font: Option<&dyn HudTextMetrics>) -> Rc<HudImage> {
        if self.dirty || self.image.is_none() {
            self.image = Some(Rc::new(render_options_panel(&self.state, font, self.accent)));
            self.dirty = false;
            self.renders += 1;
        }
        self.image.clone().expect("rendered")
    }

    fn generation(&self) -> u64 {
        1_000_000 + self.generation
    }
}

/// options_evidence.hpp OptionsEvidence: what the exit report says about the options panel, without the rows' labels,
/// values or the message. `accepted` counts setter calls; `drawn` counts frames that put the panel on screen.
#[derive(Default)]
struct OptionsEvidence {
    updates: u64,
    frames: u64,
    /// The metadata of the last drawn frame's snapshot (the JSON object), None: never drawn.
    last_drawn: Option<String>,
}

impl OptionsEvidence {
    /// nlohmann's `dump` of the metadata object: its keys sorted, `extra` merged in at their places.
    fn metadata(s: &OptionsMenu, extra: Option<(&OptionsEvidence,)>) -> String {
        let mut out = String::from("{\"category\":");
        json_string(&mut out, &s.category);
        if let Some((e,)) = extra {
            out.push_str(&format!(",\"drawn_frames\":{}", e.frames));
        }
        out.push_str(&format!(",\"focus\":{}", s.focus));
        if let Some((e,)) = extra {
            out.push_str(",\"last_drawn\":");
            out.push_str(e.last_drawn.as_deref().unwrap_or("null"));
        }
        out.push_str(",\"monitor\":");
        json_string(&mut out, &s.monitor);
        out.push_str(&format!(
            ",\"open\":{},\"revision\":{},\"row_count\":{},\"scope\":",
            s.open,
            s.revision,
            s.rows.len()
        ));
        json_string(&mut out, &s.scope);
        out.push_str(&format!(",\"scroll\":{},\"status\":", s.scroll));
        json_string(&mut out, &s.status);
        if let Some((e,)) = extra {
            out.push_str(&format!(",\"updates\":{}", e.updates));
        }
        out.push('}');
        out
    }

    fn accepted(&mut self) {
        self.updates += 1;
    }

    fn drawn(&mut self, s: &OptionsMenu) {
        self.frames += 1;
        self.last_drawn = Some(Self::metadata(s, None));
    }

    fn report(&self, state: &OptionsMenu) -> String {
        Self::metadata(state, Some((self,)))
    }
}

/// main.cpp's split of the wire `placement_menu` payload: the panel's `PlacementMenuState` and the held slider's
/// `surface` (both in wire units).
fn placement_menu_state(menu: &PlacementMenu) -> (PlacementMenuState, Option<MenuPlacement>) {
    let mut state = PlacementMenuState {
        open: menu.open,
        desktop: menu.desktop,
        field: match menu.field {
            MenuField::X => 0,
            MenuField::Y => 1,
            MenuField::Distance => 2,
            MenuField::Curvature => 3,
            MenuField::Brightness => PLACEMENT_MENU_BRIGHTNESS as u32,
        },
        // ipc.hpp WirePlacement's defaults for the entries past `count`.
        placements: [[0, 0, 100, 0]; PLACEMENT_MENU_DESKTOP_COUNT],
        count: menu.placements.len() as u32,
        numbers: [1, 2, 3, 4],
        wide: menu.wide,
        armed: menu.armed,
        busy: menu.busy,
        scale: menu.scale.unwrap_or(0),
        scales: menu.scales,
        picker: menu.picker,
        resolution_w: menu.resolution.map_or(0, |r| r.0),
        resolution_h: menu.resolution.map_or(0, |r| r.1),
        resolution_picker: menu.resolution_picker,
        background: menu.background.unwrap_or(0),
        brightness: menu.brightness.unwrap_or(100),
        background_picker: menu.background_picker,
    };
    for (to, w) in state.placements.iter_mut().zip(&menu.placements) {
        *to = [w.x, w.y, w.distance, w.curvature];
    }
    for (to, &n) in state.numbers.iter_mut().zip(&menu.numbers) {
        *to = n;
    }
    let surface = menu.surface.map(|w| [w.x, w.y, w.distance, w.curvature]);
    (state, surface)
}

pub(super) struct Menus {
    /// The placement panel (C++ `glass`) and its rendered content.
    glass: GlassPills,
    glass_image: Option<Rc<HudImage>>,
    /// The supervisor's options state as accepted (C++ `options`), the panel's surface while the pointer holds it.
    options: OptionsMenu,
    options_surface: Option<Placement>,
    overlay: OptionsOverlay,
    evidence: OptionsEvidence,
    /// The capture order index of the overlay snapshot's monitor (None: not one of the run's desktops).
    options_desktop: Option<usize>,
    /// The run's desktops, capture order (set_options_menu checks the monitor against them).
    desktops: Vec<String>,
    /// The overview free pointer is held (set_overview_pointer's `held`).
    pointer_held: bool,
    /// The panels' font, loaded once on first use (None: the built-in pixel font).
    font: Option<HudFont>,
    font_tried: bool,
    /// The last frame: an options panel in it, a placement panel open or closing in it, and its view.
    options_in_frame: bool,
    glass_drawn: bool,
    zoom: f32,
    overview: bool,
}

impl Default for Menus {
    fn default() -> Self {
        Self {
            glass: GlassPills::default(),
            glass_image: None,
            options: closed_options(),
            options_surface: None,
            overlay: OptionsOverlay::default(),
            evidence: OptionsEvidence::default(),
            options_desktop: None,
            desktops: Vec::new(),
            pointer_held: false,
            font: None,
            font_tried: false,
            options_in_frame: false,
            glass_drawn: false,
            zoom: 0.0,
            overview: true,
        }
    }
}

impl Menus {
    fn font(&mut self) -> Option<&dyn HudTextMetrics> {
        if !self.font_tried {
            self.font_tried = true;
            self.font = HudFont::load(HUD_FONT_FAMILY).ok();
        }
        self.font.as_ref().map(|f| f as &dyn HudTextMetrics)
    }

    fn resolve_options_desktop(&mut self) {
        let monitor = &self.overlay.state.monitor;
        self.options_desktop = self.desktops.iter().position(|d| d == monitor);
    }

    /// The run's desktops (capture order): wayland.cpp set_desktops closes an options panel whose monitor is gone.
    /// True if what is drawn changed.
    pub(super) fn desktops_changed(&mut self, names: &[String], now_ms: f64) -> bool {
        let mut changed = false;
        if self.options.open && !names.contains(&self.options.monitor) {
            self.options.open = false;
            self.options_surface = None;
            changed = self.overlay.set(&self.options, now_ms);
        }
        self.desktops = names.to_vec();
        self.resolve_options_desktop();
        changed
    }

    /// The theme seed (wayland.cpp set_accent): an open placement panel and the options panel are recoloured.
    pub(super) fn set_accent(&mut self, r: u8, g: u8, b: u8) {
        let seed = [f32::from(r) / 255.0, f32::from(g) / 255.0, f32::from(b) / 255.0];
        self.glass.set_accent(seed);
        self.overlay.set_accent(seed);
    }

    /// The overview free pointer's `held` (set_overview_pointer): a held slider keeps the options panel on the
    /// placement it had at the press.
    pub(super) fn set_pointer_held(&mut self, held: bool) {
        self.pointer_held = held;
    }

    /// `scene.glass()`: expand, content, top, surface, options fields and the panels' images (the render path's glass
    /// block, 1858-1895). The desktops' placements are `scene.targets` (scene_layout's).
    pub(super) fn contribute(&mut self, scene: &mut Scene) {
        let now = scene.now_ms;
        self.zoom = scene.zoom;
        self.overview = scene.overview;
        self.options_in_frame = false;
        let frame = self.glass.frame(scene.overview, now);
        if frame.content_dirty {
            let state = self.glass.rendered().clone();
            let accent = self.glass.accent();
            self.glass_image = Some(Rc::new(render_glass_panel(&state, self.font(), accent)));
        }
        // wayland.cpp target_placement: the latest placement and roll, not the eased one.
        let own = |i: usize| scene.targets.get(i).copied().unwrap_or_default();
        let mut surface = frame.surface;
        if let Some(s) = &mut surface
            && let Ok(d) = usize::try_from(frame.surface_desktop)
        {
            s.roll = own(d).roll; // a held slider's panel stays on its desktop's levelling roll
        }
        let options_alpha = self.overlay.frame(scene.overview, now);
        let mut options = None;
        if options_alpha > 0.0
            && let Some(i) = self.options_desktop
        {
            if self.options.open && self.pointer_held && self.options_surface.is_none() {
                self.options_surface = Some(own(i));
            }
            if !self.pointer_held {
                self.options_surface = None;
            }
            self.options_in_frame = true;
            let image = self.overlay.image(self.font.as_ref().map(|f| f as &dyn HudTextMetrics));
            options = Some((i as i32, image));
        }
        let glass_image = self.glass_image.clone();
        let g = scene.glass();
        g.frame.expand = frame.expand;
        g.frame.content = frame.content;
        g.frame.generation = frame.generation;
        g.frame.content_alpha = frame.content_alpha;
        g.frame.content_dirty = frame.content_dirty;
        g.frame.top = frame.top;
        g.frame.surface_desktop = frame.surface_desktop;
        g.frame.surface = surface;
        g.image = glass_image;
        if let Some((i, image)) = options {
            g.frame.surface = self.options_surface;
            g.frame.surface_desktop = i;
            g.frame.options = true;
            g.frame.top = i;
            g.frame.options_desktop = i;
            g.frame.options_expand = options_alpha;
            g.frame.options_generation = self.overlay.generation();
            g.options_image = Some(image);
        }
        self.glass_drawn = !g.frame.options && g.frame.expand.iter().any(|&e| e > 0.0);
    }

    /// After the frame's draw and swap. `desktops`: the desktops were drawn (not a VR video in their place).
    pub(super) fn drawn(&mut self, desktops: bool) {
        if !desktops {
            self.glass_drawn = false;
            self.options_in_frame = false;
        } else if self.options_in_frame {
            self.evidence.drawn(&self.overlay.state);
        }
    }

    /// wayland.cpp's redraw test for the panels (the pills' own levels are scene_layout's and handles').
    pub(super) fn animating(&self, now_ms: f64) -> bool {
        (self.glass.animating(now_ms) && self.zoom < 0.01)
            || (self.glass_drawn && !self.glass.any_expanded(now_ms))
            || self.overlay.needs_frame(self.overview, self.options_in_frame, now_ms)
    }

    /// The exit report's `placement_menu` and `options_menu` sections.
    pub(super) fn report(&self, out: &mut String) {
        out.push_str(&format!(
            ",\"placement_menu\":{{\"open\":{},\"desktop\":{},\"shown\":{},\"hidden\":{},\"renders\":{}}}",
            self.glass.open(),
            self.glass.content_desktop(),
            self.glass.opened(),
            self.glass.closed(),
            self.glass.renders()
        ));
        out.push_str(",\"options_menu\":");
        out.push_str(&self.evidence.report(&self.options));
    }
}

impl Presentation {
    /// The options panel (the wire `options_menu` payload is the C++ OptionsMenuState).
    pub fn set_options_menu(&mut self, state: &OptionsMenu) {
        let names: Vec<String> = self.state.capture.capture_names().map(str::to_owned).collect();
        let m = &mut self.state.features.menus;
        m.desktops = names;
        if !state.open || state.monitor != m.options.monitor {
            m.options_surface = None;
        }
        m.options = state.clone();
        m.evidence.accepted();
        if state.open && !m.desktops.contains(&state.monitor) {
            m.options.open = false;
        }
        let changed = m.overlay.set(&m.options, monotonic_ms() as f64);
        m.resolve_options_desktop();
        if changed {
            self.state.capture.invalidate();
        }
    }

    /// The placement panel. The wire payload carries what spike/src/main.cpp splits into PlacementMenuState and the
    /// held slider's `surface`.
    pub fn set_placement_menu(&mut self, menu: &PlacementMenu) {
        let (state, surface) = placement_menu_state(menu);
        if self.state.features.menus.glass.set(&state, monotonic_ms() as f64, surface) {
            self.state.capture.invalidate();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use xr_wire::control::{Payload, decode};

    fn frame(payload: &serde_json::Value, id: u64) -> Vec<u8> {
        let j = serde_json::json!({"schema":1,"run_id":"run","session":"session","request_id":id,"op":"options_menu",
                                   "payload":payload});
        let mut s = j.to_string();
        s.push('\n');
        s.into_bytes()
    }

    /// spike/tests/options_evidence_test.cpp: the real decoder, the receipt apart from the draw, no user text reported.
    #[test]
    fn options_evidence() {
        let mut evidence = OptionsEvidence::default();
        let mut native = closed_options();
        let feed = |f: &[u8], native: &mut OptionsMenu, evidence: &mut OptionsEvidence| match decode(f) {
            Ok(m) => {
                if let Payload::OptionsMenu(menu) = m.payload {
                    *native = menu;
                    evidence.accepted();
                }
                true
            }
            Err(_) => false,
        };
        let mut p = serde_json::json!({"open":true,"namespace":"session","monitor":"XR-2","scope":"monitor",
            "category":"3d","focus":0,"scroll":38,"revision":9,"status":"pending",
            "message":"private value must not be logged",
            "rows":[{"id":"editor","label":"private label","scope":"monitor","kind":"text",
                     "value":"private typed value","enabled":true}]});
        assert!(feed(&frame(&p, 1), &mut native, &mut evidence));
        let report: serde_json::Value = serde_json::from_str(&evidence.report(&native)).unwrap();
        assert_eq!(report["updates"], 1);
        assert_eq!(report["drawn_frames"], 0);
        assert!(report["last_drawn"].is_null());
        // A malformed display frame is refused without replacing the accepted snapshot.
        let mut invalid = p.clone();
        invalid["unknown"] = serde_json::json!(true);
        assert!(!feed(&frame(&invalid, 2), &mut native, &mut evidence));
        let report: serde_json::Value = serde_json::from_str(&evidence.report(&native)).unwrap();
        assert_eq!(report["updates"], 1);
        assert_eq!(native.revision, 9);
        evidence.drawn(&native);
        p["open"] = serde_json::json!(false);
        p["revision"] = serde_json::json!(10);
        assert!(feed(&frame(&p, 3), &mut native, &mut evidence));
        let dump = evidence.report(&native);
        let report: serde_json::Value = serde_json::from_str(&dump).unwrap();
        assert_eq!(report["updates"], 2);
        assert_eq!(report["open"], false);
        assert_eq!(report["last_drawn"]["revision"], 9);
        assert_eq!(report["drawn_frames"], 1);
        assert!(!dump.contains("private"));
    }

    /// The report as nlohmann dumps it: keys sorted, `last_drawn` null until a frame is drawn.
    #[test]
    fn options_report_is_the_cpp_dump() {
        let e = OptionsEvidence::default();
        assert_eq!(
            e.report(&closed_options()),
            "{\"category\":\"\",\"drawn_frames\":0,\"focus\":-1,\"last_drawn\":null,\"monitor\":\"\",\"open\":false,\
             \"revision\":0,\"row_count\":0,\"scope\":\"\",\"scroll\":0,\"status\":\"\",\"updates\":0}"
        );
    }

    #[test]
    fn placement_menu_splits_as_main_cpp() {
        use xr_wire::control::Placement as Wire;
        let menu = PlacementMenu {
            open: true,
            desktop: 1,
            field: MenuField::Brightness,
            placements: vec![
                Wire { x: 1, y: 2, distance: 90, curvature: 4 },
                Wire { x: -5, y: 6, distance: 80, curvature: 0 },
            ],
            numbers: vec![1, 3],
            wide: true,
            armed: false,
            busy: true,
            picker: false,
            resolution_picker: true,
            background_picker: false,
            resolution: Some((1920, 1080)),
            background: None,
            brightness: None,
            scale: Some(150),
            scales: 0b101,
            surface: Some(Wire { x: 7, y: 8, distance: 110, curvature: 9 }),
        };
        let (s, surface) = placement_menu_state(&menu);
        assert_eq!(s.field, PLACEMENT_MENU_BRIGHTNESS as u32);
        assert_eq!(s.count, 2);
        assert_eq!(s.placements, [[1, 2, 90, 4], [-5, 6, 80, 0], [0, 0, 100, 0], [0, 0, 100, 0]]);
        assert_eq!(s.numbers, [1, 3, 3, 4]);
        assert_eq!((s.resolution_w, s.resolution_h, s.background, s.brightness, s.scale), (1920, 1080, 0, 100, 150));
        assert!(s.wide && s.busy && s.resolution_picker && !s.armed);
        assert_eq!(surface, Some([7, 8, 110, 9]));
    }

    /// The overlay keeps the last open snapshot for the fade and re-renders only on a change.
    #[test]
    fn options_overlay_keeps_the_last_open_snapshot() {
        let mut o = OptionsOverlay::default();
        let mut s = closed_options();
        assert!(!o.set(&s, 0.0));
        assert_eq!(o.frame(true, 0.0), 0.0);
        s.open = true;
        s.revision = 4;
        assert!(o.set(&s, 0.0));
        assert_eq!(o.generation(), 1_000_001);
        assert!(!o.set(&s, 10.0));
        let closed = OptionsMenu { open: false, revision: 5, ..s.clone() };
        assert!(o.set(&closed, 1000.0));
        assert_eq!(o.state.revision, 4);
        assert!(o.frame(true, 1010.0) > 0.0);
        assert_eq!(o.frame(true, 2000.0), 0.0);
    }
}
