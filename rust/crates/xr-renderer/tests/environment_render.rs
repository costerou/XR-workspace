//! The backgrounds drawn by the real compositor (GlCompositor::draw_and_swap on surfaceless EGL, the glasses' 1920x1200
//! buffer under scale 1.25) behind three synthetic desktops, the glass pills, the cursor dot and the highlight:
//!   * None (or level 0) the frame is bit for bit the frame of a compositor that never heard of it;
//!   * on, every desktop's pixels are exactly as they are with None, and the highlight's glow adds to the grid instead of
//!     blacking out a halo round the selected desktop;
//!   * the grid alone, looking every way round: nothing passes the 191/255 OLED cap, most of the view stays black
//!     (see-through), something is drawn (the horizon ahead, the floor below), and the sky straight up is black;
//!   * world-locked: the horizon is where the head pitch puts it, a whole turn draws the same frame, and in locked mode
//!     (desktops head-locked) the grid still turns with the head while the desktops do not;
//!   * its colour is the theme's, the accent the highlight border is drawn in: every lit pixel has that hue, and a theme
//!     switch recolours the very next frame; the fade and the brightness scale it, black stays black at any brightness and
//!     nothing passes the cap at the brightest; the pulses move and loop seamlessly (ENVIRONMENT_LOOP_S);
//!   * Smoke: the desktops untouched over it, under the cap every way round with clear air exactly black at any
//!     brightness, the theme's hue, no seam anywhere on the sphere, world-locked, and motion that is slow and loops;
//!   * level bars and corner caps over the background leave the marks they leave over black.
//!
//! Port of spike/tests/environment_render_test.cpp, with every check and tolerance, except:
//!   * its section 6, behind a 180 degree video (VideoSphere over draw_environment, in a draw_scene_and_swap): the Rust
//!     GlCompositor has no VR video scene yet, so that check is not ported;
//!   * the preview PNGs (a directory argument, `--smoke-frames`, zlib) are not written, and with them the placement panel
//!     (render_glass_panel) only the previews drew;
//!   * the desktops' text is drawn as blocks, as the C++ does without a font (the font is not ported yet);
//!   * the level bars, caps and 3D buttons sample the handle atlas (render_handle_atlas, which needs the font, is not
//!     ported): with XR_GL_CARDS set to a `gl_bench --dump DIR` directory their section draws the C++ atlas, else it is
//!     skipped (and said so). A synthetic atlas cannot stand in: its cells are neither under the cap nor lit/unlit pairs.
//!
//! Exit 77 (skipped) without EGL/GLES.

#[path = "support/gl_scenarios.rs"]
mod gl_scenarios;

use std::f32::consts::PI as PI_F32;
use std::f64::consts::PI;
use std::sync::atomic::{AtomicUsize, Ordering};

use xr_renderer::environment::{ENVIRONMENT_LOOP_S, environment_view};
use xr_renderer::gl::GlCompositor;
use xr_renderer::glass::GlassFrame;
use xr_renderer::hud::Background;
use xr_renderer::pose::{IDENTITY, Mat3, Quat, quat_mul};
use xr_renderer::view::{FREE_POINTER_LEVEL, FRect, Placement, PointerLook, Size, blended_layout};

static FAILURES: AtomicUsize = AtomicUsize::new(0);

macro_rules! check {
    ($c:expr) => {{
        let ok: bool = $c;
        if !ok {
            eprintln!("{}:{}: check!({})", file!(), line!(), stringify!($c));
            FAILURES.fetch_add(1, Ordering::Relaxed);
        }
    }};
}

const CANVAS_W: u32 = 1536;
const CANVAS_H: u32 = 960;
const PX_W: u32 = 1920; // the glasses at scale 1.25
const PX_H: u32 = 1200;
const PIXELS: usize = PX_W as usize * PX_H as usize;
const DESKTOP_W: u32 = 1920;
const DESKTOP_H: u32 = 1080;
const TOKYO: [u8; 3] = [0x7a, 0xa2, 0xf7]; // an Omarchy theme accent (Tokyo Night)
const ROSE: [u8; 3] = [0xf7, 0x76, 0x8e];
const GREEN: [u8; 3] = [0x9e, 0xce, 0x6a];
const CAP: u8 = 191;

/// RGB, top-down.
type Image = Vec<u8>;

// --- synthetic desktops: a wallpaper and a window of text, as B,G,R,X (wl_shm XRGB) --------------------------------
struct Desktop {
    px: Vec<u8>,
}

impl Desktop {
    fn new() -> Self {
        Self {
            px: vec![0; DESKTOP_W as usize * DESKTOP_H as usize * 4],
        }
    }
    fn fill(&mut self, x0: i32, y0: i32, x1: i32, y1: i32, rgb: [i32; 3]) {
        for y in y0.max(0)..y1.min(DESKTOP_H as i32) {
            for x in x0.max(0)..x1.min(DESKTOP_W as i32) {
                let k = (y as usize * DESKTOP_W as usize + x as usize) * 4;
                self.px[k..k + 4].copy_from_slice(&[rgb[2] as u8, rgb[1] as u8, rgb[0] as u8, 255]);
            }
        }
    }
    /// Text as the C++ draws it without a font: a block a third as bright.
    fn text(&mut self, s: &str, x: i32, baseline: i32, size: u32, rgb: [i32; 3]) {
        let len = s.len() as u32;
        self.fill(
            x,
            baseline - size as i32 * 2 / 3,
            x + (len * size * 6 / 10) as i32,
            baseline,
            [rgb[0] / 3, rgb[1] / 3, rgb[2] / 3],
        );
    }
}

fn desktops() -> [Desktop; 3] {
    let mut d = [Desktop::new(), Desktop::new(), Desktop::new()];
    let wall = [[16, 18, 30], [20, 16, 28], [14, 20, 26]];
    let accent = TOKYO.map(i32::from);
    for (i, k) in d.iter_mut().enumerate() {
        for y in 0..DESKTOP_H {
            // a soft vertical wallpaper gradient
            let t = y as f32 / DESKTOP_H as f32;
            let f = 1.4 - t * 0.6;
            let c = wall[i].map(|v: i32| (v as f32 * f) as i32);
            k.fill(0, y as i32, DESKTOP_W as i32, y as i32 + 1, c);
        }
        k.fill(0, 0, DESKTOP_W as i32, 34, [22, 22, 30]); // the bar
        let spaces = match i {
            0 => "1  2  3",
            1 => "1  2",
            _ => "1",
        };
        k.text(spaces, 20, 24, 18, [150, 160, 190]);
        k.text(
            &format!("XR-{}", i + 1),
            DESKTOP_W as i32 / 2 - 30,
            24,
            18,
            [200, 205, 220],
        );
        k.text("15:19", DESKTOP_W as i32 - 90, 24, 18, [150, 160, 190]);
        k.fill(60, 80, DESKTOP_W as i32 - 60, DESKTOP_H as i32 - 50, accent); // the window border
        k.fill(
            62,
            82,
            DESKTOP_W as i32 - 62,
            DESKTOP_H as i32 - 52,
            [26, 27, 38],
        );
    }
    let shell = [
        "cody@omarchy ~/Projects/viture-workspace $ git log --oneline -4",
        "e8c44e5 Merge origin/placement-scale-dropdown",
        "0f89db5 Merge origin/pill-material-design",
        "4ff0a84 Merge origin/prestart-simple-ui",
        "1f4a608 Merge origin/layout-switch-seamless",
        "cody@omarchy ~/Projects/viture-workspace $ ctest --test-dir build/release",
        "100% tests passed, 0 tests failed out of 71",
        "cody@omarchy ~/Projects/viture-workspace $ _",
    ];
    for (l, s) in shell.iter().enumerate() {
        let c = if l == 0 || l >= 5 {
            [158, 206, 106]
        } else {
            [192, 202, 245]
        };
        d[0].text(s, 100, 150 + l as i32 * 44, 28, c);
    }
    let code = [
        "#include \"xr/environment.hpp\"",
        "",
        "namespace xr {",
        "Mat3 environment_view(Quat head, const std::optional<Quat>& ref) {",
        "    // world-locked, levelled to gravity",
        "    const Quat a = ref ? anchor(*ref) : Quat{};",
        "    return view_from(head, a);",
        "}",
        "}  // namespace xr",
    ];
    for (l, s) in code.iter().enumerate() {
        let c = if l == 4 {
            [86, 95, 137]
        } else {
            [187, 154, 247]
        };
        d[1].text(s, 100, 150 + l as i32 * 44, 28, c);
    }
    d[2].text("XR Workspace", 100, 190, 64, [200, 210, 235]);
    let page = [
        "Choose a background behind your desktops.",
        "Ctrl+Alt+E     background on / off",
        "Its colour is your theme's.",
        "Look round: it stays put in the room.",
    ];
    for (l, s) in page.iter().enumerate() {
        d[2].text(s, 100, 300 + l as i32 * 56, 34, [170, 180, 210]);
    }
    d
}

// --- the compositor ------------------------------------------------------------------------------------------------
struct Env {
    gl: GlCompositor,
    half_w: f32,
    half_h: f32,
}

fn make(d: &[Desktop; 3]) -> Result<Env, String> {
    let mut gl = GlCompositor::create_offscreen(CANVAS_W, CANVAS_H, 3)?;
    gl.resize(CANVAS_W, CANVAS_H, PX_W, PX_H);
    gl.set_accent(TOKYO[0], TOKYO[1], TOKYO[2]);
    if std::env::var_os("XR_GL_CARDS").is_some() {
        gl.set_handle_atlas(gl_scenarios::load_resources().handles);
    }
    for (i, k) in d.iter().enumerate() {
        check!(gl.upload(i, &k.px, DESKTOP_W, DESKTOP_H, DESKTOP_W * 4));
    }
    let t = (46.0 * PI / 360.0).tan();
    let a = f64::from(CANVAS_W) / f64::from(CANVAS_H); // the renderer's FOV fallback
    Ok(Env {
        gl,
        half_w: (t * a / (1.0 + a * a).sqrt()) as f32,
        half_h: (t / (1.0 + a * a).sqrt()) as f32,
    })
}

/// Head orientation (driver frame, relative to a level recentre reference): turned `yaw` degrees left, `pitch` up.
fn look(yaw: f32, pitch: f32) -> Quat {
    let y = yaw * PI_F32 / 360.0;
    let p = -pitch * PI_F32 / 360.0;
    quat_mul(
        Quat {
            x: 0.0,
            y: 0.0,
            z: y.sin(),
            w: y.cos(),
        },
        Quat {
            x: 0.0,
            y: p.sin(),
            z: 0.0,
            w: p.cos(),
        },
    )
}

fn world(yaw: f32, pitch: f32) -> Mat3 {
    environment_view(look(yaw, pitch), Some(Quat::default()))
}

#[derive(Clone, Copy)]
struct Frame {
    desktops: bool,
    pills: bool,
    pointer: bool,
    /// Every desktop's level bar and corner caps (drawn with the pills: overview only).
    bars: bool,
    /// And their 3D segments lit.
    immersive: bool,
    /// And their tails.
    global: bool,
    /// 0 overview, 1 maximized (desktop 1).
    zoom: f32,
    highlight: i32,
    /// The desktops' rotation (anchor: the head; locked / follow: identity here).
    view: Mat3,
    environment: bool,
    /// False: never call set_environment (a compositor without it).
    touch: bool,
    scene: Background,
    level: f32,
    brightness: f32,
    seconds: f32,
    world: Mat3,
}

impl Default for Frame {
    fn default() -> Self {
        Self {
            desktops: true,
            pills: false,
            pointer: false,
            bars: false,
            immersive: false,
            global: false,
            zoom: 0.0,
            highlight: 1,
            view: IDENTITY,
            environment: true,
            touch: true,
            scene: Background::Grid,
            level: 1.0,
            brightness: 100.0,
            seconds: 3.25,
            world: IDENTITY,
        }
    }
}

fn sizes() -> Vec<Size> {
    vec![Size::new(DESKTOP_W as f32, DESKTOP_H as f32); 3]
}

fn draw(e: &mut Env, f: &Frame) -> Image {
    let tiles: Vec<FRect> = if f.desktops {
        blended_layout(
            &sizes(),
            CANVAS_W as f32,
            CANVAS_H as f32,
            40.0,
            f.zoom,
            1.0,
        )
    } else {
        Vec::new()
    };
    let mut glass = GlassFrame {
        pills: f.pills && f.zoom < 0.01,
        immersive_global: f.global,
        ..GlassFrame::default()
    };
    if f.bars {
        glass.bars.fill(true);
    }
    if f.immersive {
        glass.immersive.fill(true);
    }
    let gl = &mut e.gl;
    gl.set_glass(&glass, None, None);
    gl.set_free_pointer(
        f.pointer && f.zoom < 0.01,
        CANVAS_W as f32 * 0.58,
        CANVAS_H as f32 * 0.47,
        FREE_POINTER_LEVEL,
        false,
        0.0,
        0.0,
        PointerLook::Plain,
    );
    if f.touch {
        gl.set_environment(
            f.level,
            if f.environment {
                f.scene
            } else {
                Background::None
            },
            f.brightness,
            f.seconds,
            &f.world,
        );
    }
    check!(gl.draw_and_swap(
        &tiles,
        &vec![Placement::default(); tiles.len()],
        if f.desktops { f.highlight } else { -1 },
        1.0 - f.zoom,
        &f.view,
        e.half_w,
        e.half_h,
        None
    ));
    gl.finish();
    let bottom_up = gl.read_rgb();
    check!(gl.gl().get_error() == glow::NO_ERROR);
    let row = PX_W as usize * 3;
    bottom_up
        .chunks_exact(row)
        .rev()
        .flatten()
        .copied()
        .collect()
}

fn channel_max(im: &[u8], i: usize) -> u8 {
    im[i * 3].max(im[i * 3 + 1]).max(im[i * 3 + 2])
}

fn sum(im: &[u8]) -> f64 {
    im.iter().map(|&v| f64::from(v)).sum()
}

/// Bytes apart by more than 2 (float rounding only below that).
fn off_by_more(a: &[u8], b: &[u8]) -> usize {
    a.iter()
        .zip(b)
        .filter(|(p, q)| (i32::from(**p) - i32::from(**q)).abs() > 2)
        .count()
}

fn same_px(a: &[u8], b: &[u8], k: usize) -> bool {
    a[k..k + 3] == b[k..k + 3]
}

/// Desktop `i`'s rect in buffer px (overview, head straight ahead: the canvas maps 1:1 onto the view), `inset` px inside.
#[derive(Clone, Copy)]
struct PxRect {
    x0: i32,
    y0: i32,
    x1: i32,
    y1: i32,
}

impl PxRect {
    fn contains(&self, x: i32, y: i32) -> bool {
        x >= self.x0 && x < self.x1 && y >= self.y0 && y < self.y1
    }
}

fn tile_px(i: usize, zoom: f32, inset: i32) -> PxRect {
    let t = blended_layout(&sizes(), CANVAS_W as f32, CANVAS_H as f32, 40.0, zoom, 1.0)[i];
    let s = PX_W as f32 / CANVAS_W as f32;
    PxRect {
        x0: (t.x * s).ceil() as i32 + inset,
        y0: (t.y * s).ceil() as i32 + inset,
        x1: ((t.x + t.width) * s).floor() as i32 - inset,
        y1: ((t.y + t.height) * s).floor() as i32 - inset,
    }
}

/// Desktop pixels (every desktop's rect, the middle one 3 px in, the side ones, which turn to face you, 24) that differ.
fn desktop_differ(a: &[u8], b: &[u8], show: bool) -> (usize, usize) {
    let (mut differ, mut checked, mut shown) = (0usize, 0usize, 0);
    let mut detail = String::new();
    for i in 0..3 {
        let r = tile_px(i, 0.0, if i == 1 { 3 } else { 24 });
        for y in r.y0..r.y1 {
            for x in r.x0..r.x1 {
                let k = (y as usize * PX_W as usize + x as usize) * 3;
                checked += 1;
                if !same_px(a, b, k) {
                    differ += 1;
                    if shown < 8 {
                        shown += 1;
                        detail += &format!(
                            " ({x},{y} {}/{}/{} vs {}/{}/{})",
                            a[k],
                            a[k + 1],
                            a[k + 2],
                            b[k],
                            b[k + 1],
                            b[k + 2]
                        );
                    }
                }
            }
        }
    }
    if show && differ > 0 {
        eprintln!("{differ} of {checked} desktop pixels changed under the background:{detail}");
    }
    (differ, checked)
}

/// Every lit pixel (channel max >= `min`) in `accent`'s proportions within `tolerance`: (lit, channels off).
fn hue_check(im: &[u8], accent: [u8; 3], min: u8, tolerance: f32) -> (usize, usize) {
    let top = f32::from(accent[0].max(accent[1]).max(accent[2]));
    let (mut lit, mut off) = (0, 0);
    for i in 0..PIXELS {
        let m = channel_max(im, i);
        if m < min {
            continue;
        }
        lit += 1;
        for c in 0..3 {
            off += usize::from(
                (f32::from(im[i * 3 + c]) / f32::from(m) - f32::from(accent[c]) / top).abs()
                    > tolerance,
            );
        }
    }
    (lit, off)
}

fn mean_change(p: &[u8], q: &[u8]) -> f64 {
    let t: f64 = p
        .iter()
        .zip(q)
        .map(|(a, b)| f64::from((i32::from(*a) - i32::from(*b)).abs()))
        .sum();
    t / p.len() as f64
}

/// Pixels apart by more than rounding.
fn changed(a: &[u8], b: &[u8]) -> usize {
    a.chunks_exact(3)
        .zip(b.chunks_exact(3))
        .filter(|(p, q)| {
            (0..3)
                .map(|c| (i32::from(p[c]) - i32::from(q[c])).abs())
                .sum::<i32>()
                > 6
        })
        .count()
}

struct Dir {
    name: &'static str,
    yaw: f32,
    pitch: f32,
}

const fn dir(name: &'static str, yaw: f32, pitch: f32) -> Dir {
    Dir { name, yaw, pitch }
}

fn main() {
    let content = desktops();

    // 1. Off is exactly the frame without it: a compositor that never had set_environment called, then (only one
    // compositor at a time: they would share the EGL display) the same frame after the grid has been on and gone off.
    let base = Frame {
        pills: true,
        pointer: true,
        ..Frame::default()
    };
    let never = Frame {
        touch: false,
        ..base
    };
    let off = Frame {
        environment: false,
        ..base
    };
    let plain = {
        let mut fresh = match make(&content) {
            Ok(e) => e,
            Err(why) => {
                println!("SKIP: {why}");
                std::process::exit(77);
            }
        };
        draw(&mut fresh, &never)
    };
    let mut env = match make(&content) {
        Ok(e) => e,
        Err(why) => {
            println!("SKIP: {why}");
            std::process::exit(77);
        }
    };
    let e = &mut env;
    check!(e.gl.set_environment(0.0, Background::None, 100.0, 0.0, &IDENTITY)); // the pass compiled
    let _ = draw(e, &base);
    {
        let b = draw(e, &off);
        check!(plain == b);
        if plain != b {
            eprintln!("off frame differs from a compositor without the grid");
        }
    }

    // 2. On: the desktops' own pixels are untouched; the highlight's glow adds to the grid (no black halo).
    let on = Frame {
        world: world(0.0, -6.0),
        view: IDENTITY,
        ..base
    };
    let with = draw(e, &on);
    let without = draw(e, &off);
    let grid_only = Frame {
        desktops: false,
        pills: false,
        pointer: false,
        ..on
    };
    let alone = draw(e, &grid_only);
    {
        let (differ, checked) = desktop_differ(&with, &without, true);
        check!(checked > 400_000 && differ == 0);
        // Round the highlighted desktop (outside its border, inside the glow; not below it, where its pill is): at
        // least the grid, and at least the glow.
        let outer = tile_px(1, 0.0, -40);
        let inner = tile_px(1, 0.0, -10);
        let (side0, side2) = (tile_px(0, 0.0, -12), tile_px(2, 0.0, -12));
        let (mut darker, mut band) = (0usize, 0usize);
        for y in outer.y0.max(0)..inner.y1.min(PX_H as i32) {
            // above its bottom edge: not its pill
            for x in outer.x0.max(0)..outer.x1.min(PX_W as i32) {
                if inner.contains(x, y) || side0.contains(x, y) || side2.contains(x, y) {
                    continue;
                }
                let k = (y as usize * PX_W as usize + x as usize) * 3;
                band += 1;
                if (0..3).any(|c| {
                    i32::from(with[k + c]) + 1 < i32::from(alone[k + c])
                        || i32::from(with[k + c]) + 1 < i32::from(without[k + c])
                }) {
                    darker += 1;
                }
            }
        }
        if darker > 0 {
            eprintln!("glow band: {darker} of {band} px darker than the grid or the glow alone");
        }
        check!(band > 10000 && darker == 0);
    }

    // 3. The grid alone, every way round: under the cap, mostly black, something drawn; the sky straight up is black.
    let n = PIXELS as f64;
    for d in [
        dir("ahead", 0.0, 0.0),
        dir("down", 0.0, -50.0),
        dir("left", 80.0, -5.0),
        dir("behind", 180.0, 0.0),
        dir("right-down", -120.0, -25.0),
        dir("up", 0.0, 60.0),
        dir("tilted", 30.0, -15.0),
    ] {
        let g = Frame {
            world: world(d.yaw, d.pitch),
            ..grid_only
        };
        let im = draw(e, &g);
        let (mut dark, mut lit, mut over) = (0usize, 0usize, 0usize);
        for i in 0..PIXELS {
            let m = channel_max(&im, i);
            dark += usize::from(m <= 8);
            lit += usize::from(m >= 24);
            over += usize::from(m > CAP);
        }
        println!(
            "grid {:<10} dark {:.1}%  lit {:.2}%  over cap {over}",
            d.name,
            100.0 * dark as f64 / n,
            100.0 * lit as f64 / n
        );
        check!(over == 0);
        check!(dark as f64 / n >= 0.6);
        if d.pitch < 40.0 {
            check!(lit > 2000);
        } else {
            check!(lit == 0); // straight up: black sky
        }
    }
    // The floor seen from above is mostly black between its lines too.
    {
        let g = Frame {
            world: world(0.0, -89.0),
            ..grid_only
        };
        let im = draw(e, &g);
        let dark = (0..PIXELS).filter(|&i| channel_max(&im, i) <= 8).count();
        check!(dark as f64 / n >= 0.6);
    }

    // 4. World-locked. The horizon is where the pitch puts it.
    let horizon_row = |im: &[u8]| -> u32 {
        // from the top: the sky is dark, the horizon as bright as anything below it
        let w = PX_W as usize;
        let rows: Vec<f64> = (0..PX_H as usize)
            .map(|y| (0..w).map(|x| f64::from(im[(y * w + x) * 3 + 2])).sum())
            .collect();
        let most = rows.iter().copied().fold(0.0, f64::max);
        let mut y = 0u32;
        while y + 1 < PX_H && rows[y as usize] < 0.8 * most {
            y += 1;
        }
        y
    };
    for pitch in [0.0f32, 5.0, -7.0] {
        let g = Frame {
            world: world(0.0, pitch),
            ..grid_only
        };
        let row = horizon_row(&draw(e, &g));
        let expect = PX_H as f32 / 2.0 * (1.0 + (pitch * PI_F32 / 180.0).tan() / e.half_h);
        if (row as f32 - expect).abs() > 4.0 {
            eprintln!("pitch {pitch:.0}: horizon row {row}, expected {expect:.1}");
        }
        check!((row as f32 - expect).abs() <= 4.0);
    }
    {
        let a = Frame {
            world: world(37.0, -12.0),
            ..grid_only
        };
        let b = Frame {
            world: world(37.0 + 360.0, -12.0),
            ..grid_only
        };
        let ia = draw(e, &a);
        let ib = draw(e, &b);
        check!(off_by_more(&ia, &ib) < ia.len() / 2000); // float rounding of the turn only
        let c = Frame {
            world: world(47.0, -12.0),
            ..grid_only
        };
        check!(draw(e, &c) != ia); // turning the head turns the grid
    }
    {
        // Locked (desktops head-locked: view identity): the grid follows the head, the desktops do not.
        let l1 = Frame {
            zoom: 1.0,
            view: IDENTITY,
            world: world(0.0, -5.0),
            ..base
        };
        let l2 = Frame {
            world: world(-40.0, -5.0),
            ..l1
        };
        let a = draw(e, &l1);
        let b = draw(e, &l2);
        let r = tile_px(1, 1.0, 3);
        let (mut same, mut total, mut changed_outside) = (0usize, 0usize, 0usize);
        for y in 0..PX_H as i32 {
            for x in 0..PX_W as i32 {
                let k = (y as usize * PX_W as usize + x as usize) * 3;
                if r.contains(x, y) {
                    total += 1;
                    same += usize::from(same_px(&a, &b, k));
                } else {
                    changed_outside += usize::from(!same_px(&a, &b, k));
                }
            }
        }
        check!(total > 0 && same == total);
        check!(changed_outside > 1000);
    }

    // 5. Its colour is the theme's; the fade and the brightness scale it; the pulses move and loop seamlessly.
    {
        let full = Frame {
            world: world(0.0, -30.0),
            ..grid_only
        };
        let half = Frame { level: 0.5, ..full };
        let a = draw(e, &full);
        let b = draw(e, &half);
        let sa = sum(&a);
        check!(sa > 0.0 && (sum(&b) / sa - 0.5).abs() < 0.03);
        // Every lit pixel of the grid has the highlight border's own colour: the same channels in the same proportion,
        // under the cap, for a blue, a rose and a green theme, and the very next frame after a theme switch.
        for accent in [TOKYO, ROSE, GREEN] {
            e.gl.set_accent(accent[0], accent[1], accent[2]);
            let wide = Frame {
                world: world(30.0, -14.0),
                ..grid_only
            };
            let im = draw(e, &wide);
            let (lit, off) = hue_check(&im, accent, 100, 0.03);
            if off > 0 {
                eprintln!(
                    "theme {:02x}{:02x}{:02x}: {off} channels of {lit} lit pixels off its colour",
                    accent[0], accent[1], accent[2]
                );
            }
            check!(lit > 500 && off == 0);
        }
        e.gl.set_accent(TOKYO[0], TOKYO[1], TOKYO[2]);
        // ... and the border itself is that colour, uncapped: the same frame, on the highlighted desktop's edge.
        {
            let t = tile_px(1, 0.0, 0);
            let k = ((t.y0 + (t.y1 - t.y0) / 2) as usize * PX_W as usize + (t.x0 - 4) as usize) * 3;
            check!(with[k..k + 3] == TOKYO);
        }
        // Brightness: it scales the scene, black stays exactly black at both ends, and the brightest stays under the cap.
        let dim = Frame {
            brightness: 50.0,
            ..full
        };
        let most = Frame {
            brightness: 200.0,
            ..full
        };
        let least = Frame {
            brightness: 10.0,
            ..full
        };
        let id = draw(e, &dim);
        let im = draw(e, &most);
        let il = draw(e, &least);
        check!((sum(&id) / sa - 0.5).abs() < 0.04);
        check!(sum(&il) < 0.16 * sa && sum(&il) > 0.0);
        check!(sum(&im) > 1.3 * sa);
        let (mut over, mut black, mut black_most) = (0usize, 0usize, 0usize);
        for i in 0..PIXELS {
            over += usize::from(channel_max(&im, i) > CAP);
            black += usize::from(channel_max(&a, i) == 0);
            black_most += usize::from(channel_max(&im, i) == 0);
        }
        check!(over == 0);
        // (Only the far floor's faint average of its lines, under half a level at 100%, comes up to one.)
        if black_most as f64 <= 0.93 * black as f64 {
            eprintln!("black pixels: {black} at 100%, {black_most} at 200%");
        }
        check!(black > PIXELS / 2 && black_most as f64 > 0.93 * black as f64);
        for percent in [10.0f32, 100.0, 200.0] {
            // the sky, at any brightness
            let sky = Frame {
                world: world(0.0, 60.0),
                brightness: percent,
                ..grid_only
            };
            let is = draw(e, &sky);
            check!(is.iter().all(|&v| v == 0));
        }
        // Over the floor out to the horizon something is always running somewhere: the frame changes with the clock...
        let wide = Frame {
            world: world(20.0, -12.0),
            ..grid_only
        };
        let w0 = draw(e, &wide);
        let mut moved = 0;
        for dt in [1.0f32, 2.5, 7.0] {
            let l = Frame {
                seconds: wide.seconds + dt,
                ..wide
            };
            moved += usize::from(draw(e, &l) != w0);
        }
        check!(moved == 3);
        // ... and comes back to the same frame after a whole loop (only float rounding of the clock).
        let looped = Frame {
            seconds: wide.seconds + ENVIRONMENT_LOOP_S as f32,
            ..wide
        };
        let lo = draw(e, &looped);
        check!(off_by_more(&w0, &lo) < w0.len() / 2000);
    }

    // 5b. Smoke. The desktops' pixels are as they are with None.
    let smoke = Frame {
        scene: Background::Smoke,
        ..grid_only
    };
    {
        let over = Frame {
            scene: Background::Smoke,
            ..on
        };
        let im = draw(e, &over);
        let (differ, checked) = desktop_differ(&im, &without, true);
        check!(checked > 400_000 && differ == 0);
    }
    // Every way round: under the cap, smoke somewhere, clear air exactly black, and no seam or pole: neighbouring pixels
    // are close everywhere, ahead, behind (where atan-based mapping would tear) and straight up and down.
    for d in [
        dir("ahead", 0.0, 0.0),
        dir("left", 80.0, -5.0),
        dir("behind", 180.0, 0.0),
        dir("right-down", -120.0, -25.0),
        dir("up", 0.0, 60.0),
        dir("zenith", 0.0, 89.5),
        dir("nadir", 0.0, -89.5),
    ] {
        let g = Frame {
            world: world(d.yaw, d.pitch),
            ..smoke
        };
        let im = draw(e, &g);
        let (mut lit, mut black, mut over) = (0usize, 0usize, 0usize);
        let mut jump = 0i32;
        let w = PX_W as usize;
        for y in 0..PX_H as usize {
            for x in 0..w {
                let i = y * w + x;
                let m = channel_max(&im, i);
                lit += usize::from(m >= 24);
                black += usize::from(m == 0);
                over += usize::from(m > CAP);
                if x + 1 < w {
                    jump = jump.max((i32::from(m) - i32::from(channel_max(&im, i + 1))).abs());
                }
                if y + 1 < PX_H as usize {
                    jump = jump.max((i32::from(m) - i32::from(channel_max(&im, i + w))).abs());
                }
            }
        }
        println!(
            "smoke {:<10} black {:.1}%  lit {:.1}%  over cap {over}  biggest step {jump}",
            d.name,
            100.0 * black as f64 / n,
            100.0 * lit as f64 / n
        );
        check!(over == 0 && jump <= 12);
        if d.pitch.abs() < 70.0 {
            check!(lit as f64 / n > 0.02); // (it thins out toward straight up)
        }
    }
    {
        let ahead_smoke = Frame {
            world: world(0.0, -6.0),
            ..smoke
        };
        let a = draw(e, &ahead_smoke);
        let sa = sum(&a);
        // The theme's colour, every lit pixel, and the very next frame after a switch.
        for accent in [TOKYO, ROSE, GREEN] {
            e.gl.set_accent(accent[0], accent[1], accent[2]);
            let im = draw(e, &ahead_smoke);
            let (lit, off) = hue_check(&im, accent, 60, 0.04);
            if off > 0 {
                eprintln!(
                    "smoke, theme {:02x}{:02x}{:02x}: {off} channels of {lit} lit pixels off its colour",
                    accent[0], accent[1], accent[2]
                );
            }
            check!(lit > 500 && off == 0);
        }
        e.gl.set_accent(TOKYO[0], TOKYO[1], TOKYO[2]);
        // The fade and the brightness scale it; clear air stays exactly black at both ends; the brightest stays under
        // the cap.
        let half = Frame {
            level: 0.5,
            ..ahead_smoke
        };
        check!((sum(&draw(e, &half)) / sa - 0.5).abs() < 0.03);
        let dim = Frame {
            brightness: 50.0,
            ..ahead_smoke
        };
        check!((sum(&draw(e, &dim)) / sa - 0.5).abs() < 0.04);
        let most = Frame {
            brightness: 200.0,
            ..ahead_smoke
        };
        let m2 = draw(e, &most);
        check!(sum(&m2) > 1.5 * sa);
        let (mut over, mut black, mut raised) = (0usize, 0usize, 0usize);
        for i in 0..PIXELS {
            over += usize::from(channel_max(&m2, i) > CAP);
            black += usize::from(channel_max(&m2, i) == 0);
            raised += usize::from(channel_max(&m2, i) == 0 && channel_max(&a, i) != 0); // black at 200%, lit at 100%: impossible
        }
        println!("smoke black at 200%: {:.1}%", 100.0 * black as f64 / n);
        check!(over == 0 && raised == 0);
        check!(black as f64 / n > 0.03);
        // World-locked: a whole turn is the same frame, a small turn is not.
        let t0 = Frame {
            world: world(37.0, -12.0),
            ..smoke
        };
        let t1 = Frame {
            world: world(37.0 + 360.0, -12.0),
            ..smoke
        };
        let t2 = Frame {
            world: world(47.0, -12.0),
            ..smoke
        };
        let i0 = draw(e, &t0);
        let i1 = draw(e, &t1);
        check!(off_by_more(&i0, &i1) < i0.len() / 2000);
        check!(draw(e, &t2) != i0);
        // Slow: a frame at 120 Hz changes nothing you could see; ten seconds is another shape, and a whole loop the same.
        let later = Frame {
            seconds: ahead_smoke.seconds + 1.0 / 120.0,
            ..ahead_smoke
        };
        let ten = Frame {
            seconds: ahead_smoke.seconds + 10.0,
            ..ahead_smoke
        };
        let sixty = Frame {
            seconds: ahead_smoke.seconds + 60.0,
            ..ahead_smoke
        };
        let step = mean_change(&a, &draw(e, &later));
        let far = mean_change(&a, &draw(e, &ten));
        let farther = mean_change(&a, &draw(e, &sixty));
        println!(
            "smoke mean change: {step:.3} a frame, {far:.2} in 10 s, {farther:.2} in 60 s (levels)"
        );
        check!(step < 0.6 && far > 1.5 && farther > far);
        let looped = Frame {
            seconds: ahead_smoke.seconds + ENVIRONMENT_LOOP_S as f32,
            ..ahead_smoke
        };
        let edge0 = Frame {
            seconds: 0.0,
            ..ahead_smoke
        };
        let edge1 = Frame {
            seconds: ENVIRONMENT_LOOP_S as f32 - 1.0 / 120.0,
            ..ahead_smoke
        };
        check!(mean_change(&a, &draw(e, &looped)) < 0.6);
        let (i_edge0, i_edge1) = (draw(e, &edge0), draw(e, &edge1));
        check!(mean_change(&i_edge0, &i_edge1) < 0.6); // the clock's wrap is one more frame
    }

    // Level bars and corner caps over the background (blended, premultiplied, after it and before the desktops): the
    // marks they leave over black they leave over every scene, and a maximized frame has none.
    let have_atlas = std::env::var_os("XR_GL_CARDS").is_some();
    if !have_atlas {
        println!(
            "note: XR_GL_CARDS not set, the level bar / 3D button checks were skipped (no handle atlas in Rust yet)"
        );
    }
    if have_atlas {
        let black = Frame {
            pills: true,
            environment: false,
            ..Frame::default()
        };
        let black_bars = Frame {
            bars: true,
            ..black
        };
        let shown = changed(&draw(e, &black_bars), &draw(e, &black));
        println!("bars over black: {shown} px");
        check!(shown > 500);
        // The 3D buttons come with the bars, and a lit one looks different, on the bars' own rules: nothing maximized.
        let lit = Frame {
            immersive: true,
            ..black_bars
        };
        let lit_image = draw(e, &lit);
        check!(changed(&lit_image, &draw(e, &black_bars)) > 200);
        let every = Frame {
            global: true,
            ..lit
        };
        check!(changed(&draw(e, &every), &lit_image) > 100); // the tails light on their own
        check!(changed(&draw(e, &lit), &draw(e, &black)) > shown);
        let unlit_image = draw(e, &black_bars);
        for (l, u) in lit_image.iter().zip(&unlit_image) {
            // what the lit button drew is under the OLED cap, with GL's rounding
            if l != u {
                check!(*l <= 191 + 1);
            }
        }
        let lit_zoomed = Frame { zoom: 1.0, ..lit };
        let black_zoomed = Frame { zoom: 1.0, ..black };
        check!(changed(&draw(e, &lit_zoomed), &draw(e, &black_zoomed)) == 0);
        for scene in [Background::Grid, Background::Smoke] {
            let bare = Frame {
                environment: true,
                scene,
                ..black
            };
            let barred = Frame { bars: true, ..bare };
            let over = changed(&draw(e, &barred), &draw(e, &bare));
            println!("bars over {scene:?}: {over} px");
            check!(over * 10 > shown * 8 && over * 10 < shown * 12);
            let bare_zoomed = Frame { zoom: 1.0, ..bare };
            let barred_zoomed = Frame {
                zoom: 1.0,
                ..barred
            };
            check!(changed(&draw(e, &barred_zoomed), &draw(e, &bare_zoomed)) == 0);
        }
    }

    // (Section 6 of the C++, behind a 180 degree video, is not ported: see the top of the file.)

    let failures = FAILURES.load(Ordering::Relaxed);
    if failures > 0 {
        eprintln!("{failures} failures");
        println!("environment render test FAILED");
        std::process::exit(1);
    }
    println!("environment render test passed");
}
