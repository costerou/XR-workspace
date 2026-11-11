//! One frame's scene: what the GPU composition hands `GlCompositor` (the set_* calls of wayland.cpp's render path, then
//! `draw_and_swap`), assembled from one `contribute` per feature module, and the hooks the status datagram, the exit
//! report and the control ops reach the feature modules through.
//!
//! The core (`captures.rs` `draw_gpu`) fills the frame's context and defaults (identity camera, default placements, the
//! view's blended tiles), calls [`Features::contribute`], which calls each feature's `contribute` in the C++ order, and
//! then applies the scene with [`Scene::apply`]. A feature puts its state on screen by changing its own module only;
//! see `SCENE_WIRING.md` next to `Cargo.toml` for which module feeds what.

use std::collections::HashSet;
use std::rc::Rc;

use xr_renderer::gl::{GlCompositor, StereoSettings};
use xr_renderer::glass::GlassFrame;
use xr_renderer::hud::HudImage;
use xr_renderer::ipc::StatusExtras;
use xr_renderer::pose::{IDENTITY, Mat3};
use xr_renderer::view::{FRect, Placement, PointerLook, Size, ViewAnimator};
use xr_wire::control::Op;

use super::{
    Presentation, cards, continuity_shell, handles, immersive, menus, pointer, roll, scene_layout, tracking,
    video_shell,
};

/// A head-locked card (`GlCompositor::set_popup` / `set_help` / `set_binds`): its image, re-uploaded when `generation`
/// changes, its fade and how far it is dropped (the pop-up has no drop).
#[derive(Clone, Default)]
pub(super) struct CardInput {
    pub image: Option<Rc<HudImage>>,
    pub generation: u64,
    pub alpha: f32,
    pub drop_px: f32,
}

/// `GlCompositor::set_glass`: the glass frame and the open panels' content.
#[derive(Clone, Default)]
pub(super) struct GlassInput {
    pub frame: GlassFrame,
    pub image: Option<Rc<HudImage>>,
    pub options_image: Option<Rc<HudImage>>,
}

/// `GlCompositor::set_free_pointer` (canvas px, or degrees when `directed`).
#[derive(Clone, Copy, Default)]
pub(super) struct PointerInput {
    pub visible: bool,
    pub x: f32,
    pub y: f32,
    pub level: f32,
    pub directed: bool,
    pub az: f32,
    pub el: f32,
    pub look: PointerLook,
}

/// `GlCompositor::set_window_drag`: the ghost's canvas size and the desktop it would land on (-1: none).
#[derive(Clone, Copy)]
pub(super) struct DragInput {
    pub w: f32,
    pub h: f32,
    pub target: i32,
}

/// One GPU frame. The context fields are the core's and read only for the features; every `Option` left `None` leaves
/// that `GlCompositor` input as it was, so a feature that sets one sets it every frame (as the C++ does).
#[allow(dead_code)] // context the features read once they are wired
pub(super) struct Scene {
    // ---- Context (the core).
    pub now_ms: f64,
    /// The canvas, logical px.
    pub width: u32,
    pub height: u32,
    /// tan of the half FOV at the centre pose.
    pub half_w: f32,
    pub half_h: f32,
    /// The overview, settled (zoom < 0.01 and not switching): pills, caps, pointer and panels show only here.
    pub overview: bool,
    /// The view's zoom (0 overview, 1 zoomed in) and its focus (the desktop the zoom is on).
    pub zoom: f32,
    pub focus: usize,
    /// The view turns from one desktop to another (`View::switching`).
    pub switching: bool,
    /// Each captured desktop's eased size, capture order.
    pub sizes: Vec<Size>,

    // ---- Camera (tracking): world -> eye, row-major. Identity: head-locked, the untracked view.
    /// The desktops' view (`draw_and_swap`'s `view`).
    pub head: Mat3,
    /// World-locked to the recentre reference (VR video).
    pub world: Mat3,
    /// World-locked and level (the background, `set_environment`'s `world`).
    pub environment_world: Mat3,

    // ---- Layout (scene_layout, roll).
    /// The desktops' rects on the canvas (the core's blended layout; a tile ease replaces them).
    pub tiles: Vec<FRect>,
    /// Each desktop's own placement (default: straight ahead), before the zoom grows the focused one.
    pub own: Vec<Placement>,
    /// `set_accent`, when the theme changed.
    pub accent: Option<[u8; 3]>,

    // ---- Head-locked cards (cards).
    pub popup: Option<CardInput>,
    pub help: Option<CardInput>,
    pub binds: Option<CardInput>,

    // ---- Glass (scene_layout's pills, roll's bars, handles' caps and band, menus' panels, immersive's buttons).
    pub glass: Option<GlassInput>,

    // ---- Overview pointer (pointer).
    pub pointer: Option<PointerInput>,
    pub drag: Option<DragInput>,

    // ---- Immersive 3D (immersive): `set_stereo` when it changed.
    pub stereo: Option<StereoSettings>,
}

impl Scene {
    /// A frame with the core's context and every feature input at its default.
    pub(super) fn new(
        now_ms: f64,
        (width, height): (u32, u32),
        (half_w, half_h): (f32, f32),
        tiles: Vec<FRect>,
    ) -> Self {
        let own = vec![Placement::default(); tiles.len()];
        Self {
            now_ms,
            width,
            height,
            half_w,
            half_h,
            overview: true,
            zoom: 0.0,
            focus: 0,
            switching: false,
            sizes: Vec::new(),
            head: IDENTITY,
            world: IDENTITY,
            environment_world: IDENTITY,
            tiles,
            own,
            accent: None,
            popup: None,
            help: None,
            binds: None,
            glass: None,
            pointer: None,
            drag: None,
            stereo: None,
        }
    }

    /// The glass frame, started from its default the first time a feature touches it this frame.
    #[allow(dead_code)] // the glass features' first user wires it
    pub(super) fn glass(&mut self) -> &mut GlassInput {
        self.glass.get_or_insert_with(GlassInput::default)
    }

    /// Hands the feature inputs to the compositor, in wayland.cpp's order (pop-up, help, binds, glass, pointer, drag).
    /// The background, the tiles, placements and camera go to `set_environment` / `draw_and_swap` in the core.
    pub(super) fn apply(&self, gl: &mut GlCompositor) {
        if let Some([r, g, b]) = self.accent {
            gl.set_accent(r, g, b);
        }
        if let Some(s) = &self.stereo {
            gl.set_stereo(s);
        }
        if let Some(c) = &self.popup {
            gl.set_popup(c.image.clone(), c.generation, c.alpha);
        }
        if let Some(c) = &self.help {
            gl.set_help(c.image.clone(), c.generation, c.alpha, c.drop_px);
        }
        if let Some(c) = &self.binds {
            gl.set_binds(c.image.clone(), c.generation, c.alpha, c.drop_px);
        }
        if let Some(g) = &self.glass {
            gl.set_glass(&g.frame, g.image.clone(), g.options_image.clone());
        }
        if let Some(p) = self.pointer {
            gl.set_free_pointer(p.visible, p.x, p.y, p.level, p.directed, p.az, p.el, p.look);
        }
        if let Some(d) = self.drag {
            gl.set_window_drag(d.w, d.h, d.target);
        }
    }
}

/// Every feature's state, one struct per module (each defined in its module, so a feature adds its fields there).
#[derive(Default)]
pub(super) struct Features {
    pub tracking: tracking::Tracking,
    pub layout: scene_layout::SceneLayout,
    pub roll: roll::RollState,
    pub handles: handles::Handles,
    pub pointer: pointer::Pointer,
    pub cards: cards::Cards,
    pub menus: menus::Menus,
    pub video: video_shell::VideoShell,
    pub immersive: immersive::ImmersiveState,
    #[allow(dead_code)] // read once continuity is wired
    pub continuity: continuity_shell::ContinuityShell,
    /// Ops already logged as not drawn (each once).
    logged: HashSet<Op>,
}

impl Features {
    /// Each feature's share of the frame, in the order wayland.cpp sets them: the camera first (tracking), then the
    /// layout and its roll, the cards, the glass (handles, menus, immersive) and the pointer.
    pub(super) fn contribute(&mut self, scene: &mut Scene) {
        self.tracking.contribute(scene);
        self.layout.contribute(scene);
        self.roll.contribute(scene);
        self.cards.contribute(scene);
        self.handles.contribute(scene);
        self.menus.contribute(scene);
        self.immersive.contribute(scene);
        self.pointer.contribute(scene);
    }

    /// Whether any feature still moves (a fade, an ease): the frame is drawn again even when nothing else changed.
    pub(super) fn animating(&self, now_ms: f64) -> bool {
        self.tracking.animating(now_ms)
            || self.layout.animating(now_ms)
            || self.roll.animating(now_ms)
            || self.cards.animating(now_ms)
            || self.handles.animating(now_ms)
            || self.menus.animating(now_ms)
            || self.immersive.animating(now_ms)
            || self.pointer.animating(now_ms)
            || self.video.animating(now_ms)
    }

    /// The status datagram's feature fields (`video`, `tracking`, `pose_age_ms`).
    pub(super) fn status(&self, extras: &mut StatusExtras) {
        self.video.status(extras);
        self.tracking.status(extras);
    }

    /// The exit report's feature sections between the outputs and `surface`, in wayland.cpp's order:
    /// help, binds, restore_band, placement_menu, options_menu, resize_handle, move_handle, overview_pointer, theme,
    /// tracking. Each writes `,"key":{...}` or nothing.
    pub(super) fn report_before_surface(&self, out: &mut String) {
        self.cards.report_help_binds(out);
        self.handles.report_restore_band(out);
        self.menus.report(out);
        self.handles.report_handles(out);
        self.pointer.report(out);
        self.layout.report_theme(out);
        self.tracking.report_tracking(out);
    }

    /// The exit report's feature sections between `surface` and `composition`: latency (tracking), view (layout and
    /// roll), popup_font (cards), video.
    pub(super) fn report_before_composition(&self, out: &mut String, view: &ViewAnimator) {
        self.tracking.report_latency(out);
        self.layout.report_view(out, &self.roll, view);
        self.cards.report_popup_font(out);
        self.video.report(out);
    }

    /// The exit report's sections after `composition` (depth).
    pub(super) fn report_after_composition(&self, out: &mut String) {
        self.immersive.report(out);
    }
}

impl Presentation {
    /// The first time `op` reaches a part that is not drawn yet: one journal line, as the op routing logged before.
    pub(super) fn not_drawn(&mut self, op: Op) {
        if self.state.features.logged.insert(op) {
            eprintln!("control: {} received; not drawn in this build", op.name());
        }
    }

    /// `select`, `recover` and `status`: receipted only, as spike/src/main.cpp (no Presentation call).
    pub fn receipted_only(&mut self, op: Op) {
        self.not_drawn(op);
    }

    /// What the presentation adds to a status sample: the capture fields and each feature's.
    pub fn status_extras(&self) -> StatusExtras {
        let (capture_failure_run, capture_path) = self.capture_status();
        let mut extras = StatusExtras {
            capture_failure_run,
            capture_path,
            ..StatusExtras::default()
        };
        self.state.features.status(&mut extras);
        extras
    }
}
