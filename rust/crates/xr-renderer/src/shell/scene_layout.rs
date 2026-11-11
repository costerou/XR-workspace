//! The overview scene's layout: per-desktop placements and their glide, the theme accent, the edge pan, the run's
//! desktops changing and the tile ease (wayland.cpp set_placements, set_accent, set_edge_pan, set_desktops,
//! ease_tiles, ease_placements, target_placement and pan_dot; `view.rs` holds the ported logic).

use xr_renderer::capture::parse_capture_list;
use xr_renderer::desktops::MAX_DESKTOPS;
use xr_renderer::glass::placement_from_wire;
use xr_renderer::view::{
    Canvas, EdgePan, FREE_POINTER_LEVEL, FRect, Placement, PlacementGlide, PointerLook, Size, TileEase, Vec3,
    ViewAnimator, edge_pan_dot, edge_pan_path, edge_point, overview_layout, place_point, same_placement, start_glide,
    step_placements,
};
use xr_wire::control::{EdgePanMove, PanSide};

use super::roll::RollState;
use super::scene::{PointerInput, Scene};
use super::{Presentation, monotonic_ms_f64};

/// The theme border colour before any `style` op (wayland.cpp Impl::accent).
const DEFAULT_ACCENT: [u8; 3] = [0x8d, 0x8d, 0x8d];

pub(super) struct SceneLayout {
    /// Per-desktop placement in wire units ([x, y, distance, curvature]), capture order; absent entries are flat.
    placements: Vec<[i32; 4]>,
    placement_updates: u64,
    /// The drawn placements, easing toward the latest ones (or gliding: `glide`).
    eased: Vec<Placement>,
    eased_ms: f64,
    glide: PlacementGlide,
    accent: [u8; 3],
    accent_updates: u64,
    /// The tiles on their way to the layout through a desktop change (Ctrl+Alt+R), and the layout they head for.
    tile_ease: TileEase,
    draw_tiles: Vec<FRect>,
}

impl Default for SceneLayout {
    fn default() -> Self {
        Self {
            placements: Vec::new(),
            placement_updates: 0,
            eased: Vec::new(),
            eased_ms: -1.0,
            glide: PlacementGlide::default(),
            accent: DEFAULT_ACCENT,
            accent_updates: 0,
            tile_ease: TileEase::new(),
            draw_tiles: Vec::new(),
        }
    }
}

/// Wire edge pan (ten-thousandths; `turn` of half a turn, `way` of a whole one) to the view's (ipc.cpp edge_pan).
fn edge_pan_from_wire(m: &EdgePanMove) -> EdgePan {
    let f = |v: u16| f32::from(v) / 10000.0;
    let pi = std::f32::consts::PI;
    EdgePan {
        active: true,
        from: m.from,
        to: m.to,
        side: match m.side {
            PanSide::Right => 0,
            PanSide::Left => 1,
            PanSide::Up => 2,
            PanSide::Down => 3,
        },
        exit: f(m.exit),
        entry: f(m.entry),
        peek: f(m.peek),
        cross: f(m.cross),
        turn: f(m.turn) * pi,
        way: f(m.way) * (2.0 * pi),
        zoom: f(m.zoom),
    }
}

impl SceneLayout {
    /// Desktop `i`'s latest placement with its levelling roll (wayland.cpp target_placement): the roll is part of
    /// the target, so it eases with the rest of the placement.
    pub(super) fn target_placement(&self, i: usize, roll: &RollState) -> Placement {
        let mut p = self.placements.get(i).map_or_else(Placement::default, placement_from_wire);
        if i < MAX_DESKTOPS {
            p.roll = roll.target_roll(i);
        }
        p
    }

    /// One step of the drawn placements toward the latest ones, `n` desktops (wayland.cpp ease_placements).
    fn ease_placements(&mut self, targets: &[Placement], now_ms: f64) {
        let dt = if self.eased_ms < 0.0 { 1e9 } else { (now_ms - self.eased_ms) as f32 };
        self.eased_ms = now_ms;
        self.eased.resize(targets.len(), Placement::default()); // eased in from flat
        step_placements(&mut self.eased, targets, dt, now_ms, &mut self.glide);
    }

    fn placements_easing(&self, roll: &RollState) -> bool {
        self.eased.iter().enumerate().any(|(i, e)| !same_placement(e, &self.target_placement(i, roll)))
    }

    /// A change of roll ends a glide (wayland.cpp set_roll): a glide aims at the placements it began with, so it eases
    /// on from where it got to.
    pub(super) fn end_glide(&mut self) {
        self.glide.ms = 0.0;
    }

    /// `scene.own` (each desktop's eased placement, its roll included), `scene.targets`, `scene.tiles` (on their way
    /// through a tile ease) and `scene.accent`.
    pub(super) fn contribute(&mut self, scene: &mut Scene, roll: &RollState) {
        let n = scene.sizes.len();
        scene.targets = (0..n).map(|i| self.target_placement(i, roll)).collect();
        self.ease_placements(&scene.targets, scene.now_ms);
        scene.own = self.eased.clone();
        self.draw_tiles.clone_from(&scene.tiles);
        let tiles = self.tile_ease.tiles(&scene.sizes, &self.draw_tiles, scene.overview, scene.now_ms);
        if !tiles.is_target() {
            scene.tiles = tiles.to_vec();
        }
        scene.accent = Some(self.accent);
    }

    pub(super) fn animating(&self, _now_ms: f64, roll: &RollState) -> bool {
        self.placements_easing(roll) || self.tile_ease.active()
    }

    /// The dot of an edge pan as the free pointer, directed {azimuth, elevation} in degrees: on the desktop's edge as
    /// it is drawn until it leaves it, then on its way across. None when there is none to draw (landing: the real
    /// cursor is on the target). `tiles` and `drawn` are the frame's tiles and zoomed placements (wayland.cpp pan_dot
    /// and the render path's set_free_pointer).
    pub(super) fn pan_dot(
        &self,
        c: &Canvas,
        sizes: &[Size],
        view: &ViewAnimator,
        tiles: &[FRect],
        drawn: &[Placement],
        roll: &RollState,
    ) -> Option<PointerInput> {
        if !view.pan_active() || view.pan_landing() || c.half_w == 0.0 {
            return None;
        }
        let overview = overview_layout(sizes, c.view_w, c.view_h, view.spacing());
        let n = sizes.len().min(MAX_DESKTOPS);
        let placed: Vec<Placement> =
            (0..n).map(|i| self.eased.get(i).copied().unwrap_or_else(|| self.target_placement(i, roll))).collect();
        let path = edge_pan_path(c, sizes, view.spacing(), &overview, &placed, view.pan());
        let p = view.pan();
        let from = p.from as usize;
        let path = path.filter(|_| from < tiles.len() && from < drawn.len())?;
        let mut start = path.from;
        if view.zoom() > p.zoom {
            let e = edge_point(&tiles[from], p.side, p.exit);
            start = place_point(c, &tiles[from], &drawn[from], e[0], e[1]);
            let len = (start.x * start.x + start.y * start.y + start.z * start.z).sqrt();
            if !(len > 0.0) {
                return None;
            }
            start = Vec3 { x: start.x / len, y: start.y / len, z: start.z / len };
        }
        let d = edge_pan_dot(&path, &start, view.pan_cross());
        let (az, el) = (d.x.atan2(-d.z), d.y.atan2((d.x * d.x + d.z * d.z).sqrt()));
        let deg = (180.0 / std::f64::consts::PI) as f32;
        Some(PointerInput {
            visible: true,
            x: c.view_w / 2.0,
            y: c.view_h / 2.0,
            level: FREE_POINTER_LEVEL,
            directed: true,
            az: az * deg,
            el: el * deg,
            look: PointerLook::Plain,
        })
    }

    /// Desktop `i` was dropped (set_desktops): its placement goes with it.
    pub(super) fn desktop_removed(&mut self, i: usize) {
        if i < self.placements.len() {
            self.placements.remove(i);
        }
        if i < self.eased.len() {
            self.eased.remove(i);
        }
    }

    /// A desktop went in at `i` (set_desktops): flat, straight ahead.
    pub(super) fn desktop_inserted(&mut self, i: usize) {
        if i < self.placements.len() {
            self.placements.insert(i, [0, 0, 100, 0]);
        }
        if i < self.eased.len() {
            self.eased.insert(i, Placement::default());
        }
    }

    /// The exit report's `theme` section.
    pub(super) fn report_theme(&self, out: &mut String) {
        let [r, g, b] = self.accent;
        out.push_str(&format!(",\"theme\":{{\"accent\":[{r},{g},{b}],\"updates\":{}}}", self.accent_updates));
    }

    /// The exit report's `view` section (its `roll_updates` from `roll`).
    pub(super) fn report_view(&self, out: &mut String, roll: &RollState, view: &ViewAnimator) {
        out.push_str(&format!(
            ",\"view\":{{\"selected\":{},\"zoom\":{:.6},\"spacing\":{:.6},\"placement_updates\":{},\"roll_updates\":{},\
             \"placements\":[",
            view.selected(),
            f64::from(view.zoom()),
            f64::from(view.spacing()),
            self.placement_updates,
            roll.updates(),
        ));
        for (i, p) in self.placements.iter().enumerate() {
            if i > 0 {
                out.push(',');
            }
            out.push_str(&format!("[{},{},{},{}]", p[0], p[1], p[2], p[3]));
        }
        out.push_str("]}");
    }
}

impl Presentation {
    /// Per-desktop placement in capture order, each [x, y, distance, curvature] in wire units; `glide_ms` > 0 glides.
    pub fn set_placements(&mut self, placements: &[[i32; 4]], glide_ms: u32) {
        let n = self.state.capture.capture_sizes().len();
        let l = &mut self.state.features.layout;
        // (0: a glide under way ends; eased on from where it is)
        start_glide(&mut l.glide, &l.eased, monotonic_ms_f64(), glide_ms);
        l.placements = placements[..placements.len().min(n)].to_vec();
        l.placement_updates += 1;
        self.state.capture.invalidate();
    }

    /// The theme border colour (`style`): the highlight border, glow, the pills' glass, the level bars and corner caps.
    pub fn set_accent(&mut self, r: u8, g: u8, b: u8) {
        if !self.state.capture.gpu() {
            return;
        }
        // The seed of the M3 scheme (material.hpp) of the parts drawn with it: the cards and pop-ups, redrawn if
        // showing, the tracking hint, the open panels (recoloured live, a closing one included). The GL accent and the
        // video card take `scene.accent` on the next frame.
        let seed = [f32::from(r) / 255.0, f32::from(g) / 255.0, f32::from(b) / 255.0];
        let f = &mut self.state.features;
        f.cards.set_accent(seed, monotonic_ms_f64());
        f.tracking.set_accent(seed);
        f.menus.set_accent(r, g, b);
        let l = &mut f.layout;
        if l.accent != [r, g, b] {
            l.accent = [r, g, b];
            l.accent_updates += 1;
        }
        self.state.capture.invalidate();
    }

    /// The hand's pan past a maximized desktop's edge; `None`: no pan.
    pub fn set_edge_pan(&mut self, pan: Option<&EdgePanMove>) {
        let pan = pan.map_or_else(EdgePan::default, edge_pan_from_wire);
        self.state.capture.view.set_pan(&pan, monotonic_ms_f64());
        self.state.capture.invalidate();
    }

    /// The run's managed desktops changed: captures not in `outputs` stop, new ones start, `renew` restart.
    pub fn set_desktops(&mut self, outputs: &[String], renew: &[String]) {
        self.state.features.layout.glide.ms = 0.0; // a glide is per desktop index: eased on from where it got to
        self.change_desktops(outputs, renew);
        self.state.features.immersive.desktops_changed(); // so is a depth channel
    }

    /// wayland.cpp Impl::set_desktops.
    fn change_desktops(&mut self, outputs: &[String], renew: &[String]) {
        let own = self.state.selected.map_or("", |i| self.state.outputs[i].name.as_str());
        let Some(names) = parse_capture_list(&outputs.join(","), own) else {
            return;
        };
        let qh = self.qh.clone();
        let Some(was) = self.state.change_captures(&names, renew, &qh) else {
            return;
        };
        // An options panel whose monitor is gone closes (change_captures has the frame drawn again).
        self.state.features.menus.desktops_changed(&names, monotonic_ms_f64());
        let l = &mut self.state.features.layout;
        if l.tile_ease.armed(monotonic_ms_f64()) {
            // the tiles of an eased change follow their desktops by name
            let before: Vec<i32> = names
                .iter()
                .take(MAX_DESKTOPS)
                .map(|name| was.iter().position(|w| w == name).map_or(-1, |at| at as i32))
                .collect();
            l.tile_ease.reindex(&before);
        }
    }

    /// The tiles ease to the layout the next set_desktops leads to: call it first.
    pub fn ease_tiles(&mut self) {
        let sizes = self.state.capture.capture_sizes();
        let l = &mut self.state.features.layout;
        l.tile_ease.arm(&sizes, &l.draw_tiles, monotonic_ms_f64());
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use xr_renderer::view::{
        Leaving, TargetOptions, ZoomOptions, blended_layout_leaving, glide_toward, zoomed_placements_with,
    };

    fn scene(now_ms: f64, sizes: &[Size], tiles: Vec<FRect>) -> Scene {
        let mut s = Scene::new(now_ms, (1920, 1080), (0.36, 0.21), tiles);
        s.sizes = sizes.to_vec();
        s
    }

    #[test]
    fn edge_pan_wire_units() {
        // ipc.cpp edge_pan: ten-thousandths; turn a share of half a turn, way of a whole one.
        let m = EdgePanMove {
            from: 0,
            to: 2,
            side: PanSide::Up,
            turn: 500,
            way: 1000,
            exit: 5000,
            entry: 2500,
            peek: 10000,
            cross: 1,
            zoom: 2000,
        };
        let p = edge_pan_from_wire(&m);
        let pi = std::f32::consts::PI;
        assert!(p.active && p.from == 0 && p.to == 2 && p.side == 2);
        assert_eq!(p.turn, 500.0f32 / 10000.0 * pi);
        assert_eq!(p.way, 1000.0f32 / 10000.0 * (2.0 * pi));
        assert_eq!((p.exit, p.entry, p.peek, p.cross, p.zoom), (0.5, 0.25, 1.0, 0.0001, 0.2));
    }

    #[test]
    fn placements_ease_in_from_flat() {
        let sizes = [Size { w: 1920.0, h: 1080.0 }; 2];
        let tiles = vec![FRect::default(); 2];
        let mut l = SceneLayout::default();
        let mut s = scene(1000.0, &sizes, tiles.clone());
        l.contribute(&mut s, &RollState::default());
        assert_eq!(s.own, vec![Placement::default(); 2]); // nothing set: flat, straight ahead
        assert!(!l.animating(1000.0, &RollState::default()));
        l.placements = vec![[300, 0, 150, 0]];
        let target = placement_from_wire(&[300, 0, 150, 0]);
        let mut s = scene(1000.0 + f64::from(PLACEMENT_EASE), &sizes, tiles.clone());
        l.contribute(&mut s, &RollState::default());
        assert!(s.own[0].x > 0.0 && s.own[0].x < target.x && l.animating(0.0, &RollState::default())); // on its way
        assert_eq!(s.own[1], Placement::default()); // an absent entry is flat
        let mut s = scene(1e6, &sizes, tiles);
        l.contribute(&mut s, &RollState::default());
        assert!(same_placement(&s.own[0], &target) && !l.animating(0.0, &RollState::default())); // lands exactly
    }
    /// The levelling roll is part of the target placement (wayland.cpp target_placement): it eases with the rest.
    #[test]
    fn the_roll_eases_with_the_placement() {
        let sizes = [Size { w: 1920.0, h: 1080.0 }];
        let tiles = vec![FRect::default()];
        let (mut l, mut roll) = (SceneLayout::default(), RollState::default());
        let mut s = scene(1000.0, &sizes, tiles.clone());
        l.contribute(&mut s, &roll);
        roll.set_rolls([9000, 0, 0, 0]);
        let target = std::f32::consts::FRAC_PI_2;
        assert_eq!(l.target_placement(0, &roll).roll, 9000.0 / 100.0 * std::f32::consts::PI / 180.0);
        assert!(l.animating(1000.0, &roll));
        let mut s = scene(1000.0 + f64::from(PLACEMENT_EASE), &sizes, tiles.clone());
        l.contribute(&mut s, &roll);
        assert!(s.own[0].roll > 0.0 && s.own[0].roll < target, "on its way: {}", s.own[0].roll);
        assert_eq!(s.targets[0].roll, l.target_placement(0, &roll).roll);
        let mut s = scene(1e6, &sizes, tiles);
        l.contribute(&mut s, &roll);
        assert!(same_placement(&s.own[0], &s.targets[0]) && !l.animating(0.0, &roll));
    }

    const PLACEMENT_EASE: f32 = xr_renderer::view::PLACEMENT_EASE_MS;

    #[test]
    fn glide_takes_every_desktop_along_one_curve() {
        let sizes = [Size { w: 1920.0, h: 1080.0 }];
        let mut l = SceneLayout::default();
        l.contribute(&mut scene(0.0, &sizes, vec![FRect::default()]), &RollState::default());
        let from = l.eased.clone();
        start_glide(&mut l.glide, &l.eased, 100.0, 200);
        l.placements = vec![[-600, 100, 80, 30]];
        let to = placement_from_wire(&l.placements[0]);
        let mut s = scene(200.0, &sizes, vec![FRect::default()]);
        l.contribute(&mut s, &RollState::default());
        assert_eq!(s.own[0], glide_toward(&from[0], &to, 0.5));
        l.contribute(&mut scene(300.0, &sizes, vec![FRect::default()]), &RollState::default());
        assert!(same_placement(&l.eased[0], &to) && l.glide.ms == 0.0); // landed: the glide ends
    }

    #[test]
    fn desktops_change_keeps_placements_on_their_desktops() {
        let mut l = SceneLayout::default();
        l.placements = vec![[1, 0, 100, 0], [2, 0, 100, 0]];
        l.eased = vec![Placement::new(0.1, 0.0, 1.0, 0.0, 0.0); 3];
        l.desktop_removed(0);
        assert_eq!(l.placements, vec![[2, 0, 100, 0]]);
        assert_eq!(l.eased.len(), 2);
        l.desktop_inserted(0);
        assert_eq!(l.placements, vec![[0, 0, 100, 0], [2, 0, 100, 0]]);
        assert_eq!(l.eased[0], Placement::default());
        l.desktop_inserted(5); // past the end: absent, flat already
        assert_eq!(l.placements.len(), 2);
    }

    #[test]
    fn tile_ease_moves_the_tiles_to_a_new_layout() {
        let one = [Size { w: 1920.0, h: 1080.0 }];
        let two = [Size { w: 1920.0, h: 1080.0 }; 2];
        let before = vec![FRect::new(0.0, 0.0, 100.0, 50.0)];
        let after = vec![FRect::new(0.0, 0.0, 50.0, 25.0), FRect::new(60.0, 0.0, 50.0, 25.0)];
        let mut l = SceneLayout::default();
        l.contribute(&mut scene(0.0, &one, before.clone()), &RollState::default());
        l.tile_ease.arm(&one, &l.draw_tiles, 10.0);
        l.tile_ease.reindex(&[0, -1]); // a desktop went in after the first
        let mut s = scene(20.0, &two, after.clone());
        l.contribute(&mut s, &RollState::default());
        assert_eq!(s.tiles[0], before[0]); // the ease starts from where it was drawn
        assert!(l.animating(20.0, &RollState::default()));
        let mut s = scene(20.0 + f64::from(xr_renderer::view::TILE_EASE_MS), &two, after.clone());
        l.contribute(&mut s, &RollState::default());
        assert_eq!(s.tiles, after);
        assert!(!l.tile_ease.active());
    }

    #[test]
    fn report_sections() {
        let mut l = SceneLayout::default();
        let mut out = String::new();
        l.report_theme(&mut out);
        assert_eq!(out, ",\"theme\":{\"accent\":[141,141,141],\"updates\":0}");
        l.placements = vec![[1, -2, 100, 3], [0, 0, 50, 0]];
        l.placement_updates = 2;
        let mut out = String::new();
        l.report_view(&mut out, &RollState::default(), &ViewAnimator::default());
        assert_eq!(
            out,
            ",\"view\":{\"selected\":0,\"zoom\":0.000000,\"spacing\":40.000000,\"placement_updates\":2,\
             \"roll_updates\":0,\"placements\":[[1,-2,100,3],[0,0,50,0]]}"
        );
    }

    /// wayland.cpp pan_dot, frame by frame against the C++ (spike/tests/edge_pan_trace.cpp, untracked, from the input
    /// below): tests/fixtures/edge_pan_dot.txt holds its `dot`.
    #[test]
    fn pan_dot_matches_the_cpp() {
        let c = Canvas::new(1920.0, 1080.0, 0.36, 0.21);
        let sizes = [
            Size { w: 1920.0, h: 1080.0 },
            Size { w: 1920.0, h: 1080.0 },
            Size { w: 2560.0, h: 1080.0 },
            Size { w: 1920.0, h: 1080.0 },
        ];
        let mut l = SceneLayout::default();
        l.eased = vec![
            Placement::new(0.0, 0.0, 1.0, 0.0, 0.0),
            Placement::new(0.3, 0.05, 1.2, 0.2, 0.0),
            Placement::new(0.6, 0.0, 1.0, 0.0, 0.0),
            Placement::new(-0.5, -0.1, 0.9, 0.0, 0.0),
        ];
        let mut view = ViewAnimator::default();
        view.set_target(false, 0, 40, 0.0);
        view.advance(10000.0);
        view.set_target(true, 0, 40, 10000.0);
        view.advance(20000.0);
        let mut t = 20000.0;
        while t <= 30000.0 {
            view.advance(t);
            view.take_landed_turns();
            t += 8.0;
        }
        let mut frames = Vec::new();
        let mut frame = |view: &mut ViewAnimator, t: f64| {
            view.advance(t + 30000.0);
            view.take_landed_turns();
            let leaving = Leaving { zoom: view.left_zoom(), index: view.left() as usize };
            let (spacing, zoom, position) = (view.spacing(), view.zoom(), view.position());
            let tiles = blended_layout_leaving(&sizes, 1920.0, 1080.0, spacing, zoom, position, leaving);
            let overview = overview_layout(&sizes, 1920.0, 1080.0, view.spacing());
            let options = ZoomOptions { leaving, ..ZoomOptions::default() };
            let drawn = zoomed_placements_with(&c, &overview, &tiles, &l.eased, view.zoom(), view.position(), options);
            let dot = l.pan_dot(&c, &sizes, view, &tiles, &drawn, &RollState::default());
            let rad = (180.0 / std::f64::consts::PI) as f32;
            frames.push(match dot {
                Some(d) => format!("{} {:.6} {:.6}", t, f64::from(d.az / rad), f64::from(d.el / rad)),
                None => format!("{t} -"),
            });
        };
        let mut t = 0.0;
        for k in 0..10 {
            let s: f32 = format!("{}", f64::from(k + 1) / 10.0).parse().expect("a number");
            let p = EdgePan {
                active: true,
                from: 0,
                to: 1,
                side: 0,
                exit: 0.3,
                entry: 0.7,
                peek: s,
                cross: s,
                turn: 0.15707964,
                way: 0.62831855,
                zoom: 0.2,
            };
            view.set_pan(&p, t + 30000.0);
            for _ in 0..3 {
                t += 8.0;
                frame(&mut view, t);
            }
        }
        view.set_target_with(true, 1, 40, t + 30000.0, TargetOptions { pan: true, ..TargetOptions::default() });
        for _ in 0..6 {
            t += 8.0;
            frame(&mut view, t);
        }
        let want: Vec<&str> =
            include_str!("../../tests/fixtures/edge_pan_dot.txt").lines().filter(|l| !l.starts_with('#')).collect();
        assert_eq!(frames.len(), want.len());
        for (got, want) in frames.iter().zip(want) {
            assert_eq!(got, want);
        }
    }
}
