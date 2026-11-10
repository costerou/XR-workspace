//! The pill and its morph as the real compositor draws them (GlCompositor::draw_and_swap on surfaceless EGL, one canvas
//! px per pixel): at every size, curvature and roll, round you, in the last moment of a zoom and all along the pill <->
//! panel morph, the glass sits a small fixed gap clear of the selected desktop's border and never on it. Port of
//! spike/tests/pill_border_render_test.cpp (without its PPM dump).
//!
//! The border alone and the glass alone are drawn on a black desktop, so a lit pixel is that and nothing else (the
//! glass's shadow is black: it draws nothing here), and the two masks are compared. Exit 77 (skipped) without EGL/GLES.

use std::f64::consts::PI;
use std::sync::atomic::{AtomicUsize, Ordering};

use xr_renderer::gl::GlCompositor;
use xr_renderer::glass::{GLASS_PILL_GAP, GlassFrame};
use xr_renderer::pose::IDENTITY;
use xr_renderer::view::{
    BORDER_PX, Canvas, FRect, Placement, PointerLook, Size, blended_layout, overview_layout,
    zoomed_placements,
};

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

const W: u32 = 1536;
const H: u32 = 960;
const DESKTOP_W: u32 = 64;
const DESKTOP_H: u32 = 36;
const ACCENT: [u8; 3] = [0x7a, 0xa2, 0xf7]; // an Omarchy theme accent (Tokyo Night)
// The empty rows between the border and the glass, laid out as GLASS_PILL_GAP - BORDER_PX. Square to the view that is
// what is drawn at every size, less the row the glass's anti-aliased edge takes when the edge lands between pixels.
// Rolled, both edges rasterize as stairs, which takes up to 1.5 px off it.
const ROWS: f32 = GLASS_PILL_GAP - BORDER_PX;
const SQUARE_FEWEST: f32 = ROWS - 1.0;
const ROLLED_FEWEST: f32 = ROWS - 1.5;
const MOST_ROWS: f32 = ROWS + 1.0;
const NO_LIMIT: f32 = f32::MAX;

struct Pose {
    name: &'static str,
    placement: Placement,
    square: bool,
}

#[allow(clippy::approx_constant)] // the C++ test's poses, to the digit
const POSES: [Pose; 8] = [
    Pose {
        name: "flat",
        placement: Placement::new(0.0, 0.0, 1.0, 0.0, 0.0),
        square: true,
    },
    Pose {
        name: "curved",
        placement: Placement::new(0.0, 0.0, 1.0, 1.0, 0.0),
        square: false,
    },
    Pose {
        name: "most-curved",
        placement: Placement::new(0.0, 0.0, 1.0, 2.0944, 0.0),
        square: false,
    },
    Pose {
        name: "rolled",
        placement: Placement::new(0.0, 0.0, 1.0, 0.0, 0.3),
        square: false,
    },
    Pose {
        name: "rolled-back",
        placement: Placement::new(0.0, 0.0, 1.0, 0.0, -0.74),
        square: false,
    },
    Pose {
        name: "on-its-side",
        placement: Placement::new(0.0, 0.0, 1.0, 0.0, 1.5708),
        square: false,
    },
    Pose {
        name: "turned",
        placement: Placement::new(0.15, 0.1, 1.0, 0.0, 0.0),
        square: false,
    },
    Pose {
        name: "everything",
        placement: Placement::new(-0.1, 0.08, 1.0, 0.8, -0.4),
        square: false,
    },
];

#[derive(Clone, Copy)]
struct Scene {
    tile: FRect,
    placement: Placement,
    expand: f32,
}

/// RGB, top-down.
fn draw(
    gl: &mut GlCompositor,
    s: &Scene,
    border: bool,
    glass: bool,
    half_w: f32,
    half_h: f32,
) -> Vec<u8> {
    let mut frame = GlassFrame {
        pills: glass,
        ..GlassFrame::default()
    };
    frame.expand[0] = if glass { s.expand } else { 0.0 };
    frame.top = if glass && s.expand > 0.0 { 0 } else { -1 };
    gl.set_glass(&frame, None, None);
    gl.set_free_pointer(false, 0.0, 0.0, 0.0, false, 0.0, 0.0, PointerLook::Plain);
    check!(gl.draw_and_swap(
        &[s.tile],
        &[s.placement],
        if border { 0 } else { -1 },
        1.0,
        &IDENTITY,
        half_w,
        half_h,
        None
    ));
    let bottom_up = gl.read_rgb();
    check!(gl.gl().get_error() == glow::NO_ERROR);
    let row = (W * 3) as usize;
    bottom_up
        .chunks_exact(row)
        .rev()
        .flatten()
        .copied()
        .collect()
}

/// The border's pixels: the accent itself (its glow, under it, is a fifth as bright at most).
fn border_mask(f: &[u8]) -> Vec<bool> {
    f.chunks_exact(3)
        .map(|p| (0..3).all(|c| (i32::from(p[c]) - i32::from(ACCENT[c])).abs() <= 8))
        .collect()
}
/// The glass's pixels: anything lit on the black desktop, its faintest anti-aliased edge included.
fn glass_mask(f: &[u8]) -> Vec<bool> {
    f.chunks_exact(3)
        .map(|p| p[0].max(p[1]).max(p[2]) > 8)
        .collect()
}

/// Empty pixels on the shortest way from any glass pixel to the border (a chamfer distance, a hair over the true one on
/// a diagonal). < 0: they overlap.
fn clearance(border: &[bool], glass: &[bool]) -> f32 {
    const FAR: f32 = f32::MAX / 2.0;
    const DIAGONAL: f32 = std::f32::consts::SQRT_2;
    let (w, h) = (W as usize, H as usize);
    let mut d: Vec<f32> = border.iter().map(|&b| if b { 0.0 } else { FAR }).collect();
    for y in 0..h {
        for x in 0..w {
            let mut v = d[y * w + x];
            if x > 0 {
                v = v.min(d[y * w + x - 1] + 1.0);
            }
            if y > 0 {
                v = v.min(d[(y - 1) * w + x] + 1.0);
                if x > 0 {
                    v = v.min(d[(y - 1) * w + x - 1] + DIAGONAL);
                }
                if x + 1 < w {
                    v = v.min(d[(y - 1) * w + x + 1] + DIAGONAL);
                }
            }
            d[y * w + x] = v;
        }
    }
    for y in (0..h).rev() {
        for x in (0..w).rev() {
            let mut v = d[y * w + x];
            if x + 1 < w {
                v = v.min(d[y * w + x + 1] + 1.0);
            }
            if y + 1 < h {
                v = v.min(d[(y + 1) * w + x] + 1.0);
                if x + 1 < w {
                    v = v.min(d[(y + 1) * w + x + 1] + DIAGONAL);
                }
                if x > 0 {
                    v = v.min(d[(y + 1) * w + x - 1] + DIAGONAL);
                }
            }
            d[y * w + x] = v;
        }
    }
    let nearest = d
        .iter()
        .zip(glass)
        .filter(|(_, g)| **g)
        .map(|(v, _)| *v)
        .fold(FAR, f32::min);
    nearest - 1.0 // border next to glass: nothing between them, 0
}

fn main() {
    let mut gl = match GlCompositor::create_offscreen(W, H, 1) {
        Ok(gl) => gl,
        Err(why) => {
            println!("SKIP: no offscreen compositor: {why}");
            std::process::exit(77);
        }
    };
    gl.resize(W, H, W, H);
    gl.set_accent(ACCENT[0], ACCENT[1], ACCENT[2]);
    let black = vec![0u8; (DESKTOP_W * DESKTOP_H * 4) as usize];
    check!(gl.upload(0, &black, DESKTOP_W, DESKTOP_H, DESKTOP_W * 4));
    let t = (46.0 * PI / 360.0).tan();
    let a = f64::from(W) / f64::from(H); // the renderer's FOV fallback
    let (half_w, half_h) = (
        (t * a / (1.0 + a * a).sqrt()) as f32,
        (t / (1.0 + a * a).sqrt()) as f32,
    );

    let (mut scenes, mut overlapping) = (0, 0);
    let (mut least, mut most) = (f32::MAX, -f32::MAX);
    let mut scene =
        |gl: &mut GlCompositor, name: &str, s: Scene, fewest: f32, most_allowed: f32| {
            let border = border_mask(&draw(gl, &s, true, false, half_w, half_h));
            let glass = glass_mask(&draw(gl, &s, false, true, half_w, half_h));
            let lit = glass.iter().filter(|g| **g).count();
            let framed = border.iter().filter(|b| **b).count();
            let on_it = glass
                .iter()
                .zip(&border)
                .filter(|(g, b)| **g && **b)
                .count();
            if lit == 0 || framed == 0 {
                eprintln!("{name}: nothing drawn to compare ({lit} glass, {framed} border)");
                FAILURES.fetch_add(1, Ordering::Relaxed);
                return;
            }
            let rows = if on_it > 0 {
                -1.0
            } else {
                clearance(&border, &glass)
            };
            scenes += 1;
            overlapping += i32::from(on_it > 0);
            least = least.min(rows);
            most = most.max(rows);
            if on_it > 0 {
                eprintln!("{name}: {on_it} of {lit} glass pixels on the border");
            } else if rows < fewest || rows > most_allowed {
                eprintln!("{name}: {rows:.1} rows between the border and the glass");
                FAILURES.fetch_add(1, Ordering::Relaxed);
            }
            check!(on_it == 0);
        };
    let label = |tile: &str, pose: &str, d: f32, e: f32| {
        format!(
            "{tile}-{pose}-d{:03}-e{:03}",
            (d * 100.0).round() as i32,
            (e * 100.0).round() as i32
        )
    };

    // A small desktop, to see it grown ten times, and one the size of a real overview tile, as far as it stays in view.
    let small = FRect {
        x: 718.0,
        y: 452.0,
        width: 100.0,
        height: 56.0,
    };
    let real = FRect {
        x: 475.0,
        y: 315.0,
        width: 586.0,
        height: 330.0,
    };
    // The closed pill: every size, every pose.
    for pose in &POSES {
        let (fewest, most_allowed) = if pose.square {
            (SQUARE_FEWEST, ROWS)
        } else {
            (ROLLED_FEWEST, MOST_ROWS)
        };
        for d in [2.0, 1.5, 1.0, 0.8, 0.61, 0.59, 0.5, 0.35, 0.25, 0.15, 0.1] {
            let p = Placement {
                distance: d,
                ..pose.placement
            };
            scene(
                &mut gl,
                &label("small", pose.name, d, 0.0),
                Scene {
                    tile: small,
                    placement: p,
                    expand: 0.0,
                },
                fewest,
                most_allowed,
            );
        }
        for d in [2.0, 1.0, 0.8, 0.59, 0.5, 0.4] {
            let p = Placement {
                distance: d,
                ..pose.placement
            };
            scene(
                &mut gl,
                &label("real", pose.name, d, 0.0),
                Scene {
                    tile: real,
                    placement: p,
                    expand: 0.0,
                },
                fewest,
                most_allowed,
            );
        }
    }
    // The morph, and the open panel: it shares the pill's top all the way; only how close it comes is bounded.
    for pose in &POSES {
        if pose.placement.curvature > 1.5 {
            continue; // the panel would wrap round the desktop's narrow arc
        }
        for e in [0.25, 0.5, 0.75, 1.0] {
            for d in [1.0, 0.8, 0.59, 0.5, 0.4] {
                let p = Placement {
                    distance: d,
                    ..pose.placement
                };
                scene(
                    &mut gl,
                    &label("real", pose.name, d, e),
                    Scene {
                        tile: real,
                        placement: p,
                        expand: e,
                    },
                    ROLLED_FEWEST,
                    NO_LIMIT,
                );
            }
        }
    }
    // The last moment of a zoom: the pills show below 1%, on the blended layout and placements.
    {
        let canvas = Canvas::new(W as f32, H as f32, half_w, half_h);
        let sizes = [Size {
            w: 1920.0,
            h: 1080.0,
        }]; // kept small enough by its spacing to stay in view, made bigger
        let overview = overview_layout(&sizes, W as f32, H as f32, 400.0);
        let now = blended_layout(&sizes, W as f32, H as f32, 400.0, 0.009, 0.0);
        let resized = [Placement::new(0.0, 0.0, 0.6, 0.0, 0.2)];
        let drawn = zoomed_placements(&canvas, &overview, &now, &resized, 0.009, 0.0);
        scene(
            &mut gl,
            "zooming",
            Scene {
                tile: now[0],
                placement: drawn[0],
                expand: 0.0,
            },
            ROLLED_FEWEST,
            MOST_ROWS,
        );
    }
    println!(
        "pill border render: {scenes} scenes, {overlapping} with glass on the border, {least:.1}..{most:.1} rows clear"
    );
    let failures = FAILURES.load(Ordering::Relaxed);
    println!(
        "{}",
        if failures > 0 {
            "pill border render test FAILED"
        } else {
            "pill border render test passed"
        }
    );
    std::process::exit(i32::from(failures > 0));
}
