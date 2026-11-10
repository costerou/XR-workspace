//! Global immersive 3D on a surfaceless EGL context through the real GlCompositor, a port of the spike's
//! tests/stereo_render_test.cpp (with tests/fake_depth.hpp):
//!  1. golden images of the side-by-side frame (spike/tests/golden/stereo_{box,ramp,text}.ppm, the C++ renderer's), with
//!     the C++ tolerance for different GPUs: at most 1% of the pixels off by more than 16;
//!  2. disparity measured from the pixels by block matching the two eyes: exact on flat depth, inside the comfort limits
//!     at the harshest settings, no pixel brighter than the source;
//!  3. live strength: strength 0 and a per-desktop 0 are flat at once, without a fresh depth frame;
//!  4. frame time at 3840 x 1200 with depth arriving at 30 Hz on three desktops, five rounds of 240 frames. Printed on
//!     any GPU, enforced on the RX 7800 XT on the quietest round (a shared GPU adds noise): median <= 0.41 ms (the Rust rebuild's gate) and p99 < 8.33 ms (120 Hz, the C++ check).
//!
//! Not ported: the CPU mirror checks of the guide and warp passes (the CPU mirrors of xr/stereo.hpp are not in Rust)
//! and the whole-view checks (pills, pointer, environment).
//! Exit 77 (skipped) without EGL/GLES or without the stereo pass. `--write-goldens DIR` writes this renderer's frames.

use std::time::{Duration, Instant};

use glow::HasContext;
use xr_renderer::depth::{DepthData, DepthInput};
use xr_renderer::gl::{GlCompositor, StereoSettings};
use xr_renderer::pose::IDENTITY;
use xr_renderer::stereo::{
    STEREO_FAR_DEG_MAX, STEREO_NEAR_DEG_MAX, STEREO_STRENGTH_MAX, StereoParams, stereo_budget,
    stereo_half_disparity, stereo_limits, stereo_v,
};
use xr_renderer::view::{FRect, Placement};

const HALF_W: f32 = 0.44;
const HALF_H: f32 = 0.275;

static mut FAILURES: u32 = 0;

macro_rules! check {
    ($c:expr) => {
        if !($c) {
            eprintln!("{}:{}: CHECK({})", file!(), line!(), stringify!($c));
            // SAFETY: single-threaded test binary.
            unsafe { FAILURES += 1 };
        }
    };
}

// ---- fake_depth.hpp ------------------------------------------------------------------------------------------------

struct Plane {
    w: i32,
    h: i32,
    px: Vec<f32>,
}

struct Rgb {
    w: i32,
    px: Vec<f32>,
}

/// Far wall (0) with a near box (1) in the middle third.
fn box_depth(w: i32, h: i32) -> Plane {
    let mut px = vec![0.0; (w * h) as usize];
    for y in h / 3..2 * h / 3 {
        for x in w / 3..2 * w / 3 {
            px[(y * w + x) as usize] = 1.0;
        }
    }
    Plane { w, h, px }
}

/// Far at the left, near at the right.
fn ramp_depth(w: i32, h: i32) -> Plane {
    let px = (0..h).flat_map(|_| (0..w).map(move |x| (x as f32 + 0.5) / w as f32)).collect();
    Plane { w, h, px }
}

fn flat_depth(w: i32, h: i32, value: f32) -> Plane {
    Plane { w, h, px: vec![value; (w * h) as usize] }
}

fn bumps(x: f32, y: f32, seed: u32) -> f32 {
    let (mut v, mut k) = (0.0f32, 1.0f32);
    for i in 0..4u32 {
        let a = seed.wrapping_mul(13).wrapping_add(i * 7) as f32 * 0.37;
        let fi = i as f32;
        v += k * (x * (3.0 + fi * 4.0) + a).sin() * (y * (2.0 + fi * 5.0) - a).cos();
        k *= 0.5;
    }
    0.5 + 0.27 * v
}

fn bump_depth(w: i32, h: i32, seed: u32) -> Plane {
    let mut px = Vec::with_capacity((w * h) as usize);
    for y in 0..h {
        for x in 0..w {
            px.push(bumps(x as f32 / w as f32 * 6.0, y as f32 / h as f32 * 6.0, seed).clamp(0.0, 1.0));
        }
    }
    Plane { w, h, px }
}

/// A colourful texture, different everywhere, so a shift can be measured.
fn textured(w: i32, h: i32) -> Rgb {
    let mut px = Vec::with_capacity((w * h * 3) as usize);
    for y in 0..h {
        for x in 0..w {
            let (u, v) = (x as f32 / w as f32, y as f32 / h as f32);
            px.push(0.5 + 0.4 * (u * 31.0 + v * 7.0).sin());
            px.push(0.5 + 0.4 * (u * 13.0 - v * 19.0 + 1.0).sin());
            px.push(0.5 + 0.4 * (u * 47.0 + v * 3.0).cos());
        }
    }
    Rgb { w, px }
}

/// A red box (matching box_depth at this size) on a green wall.
fn box_picture(w: i32, h: i32) -> Rgb {
    let mut px = Vec::with_capacity((w * h * 3) as usize);
    for y in 0..h {
        for x in 0..w {
            let inside = y >= h / 3 && y < 2 * h / 3 && x >= w / 3 && x < 2 * w / 3;
            px.push(if inside { 1.0 } else { 0.05 });
            px.push(if inside {
                0.05
            } else {
                0.6 + 0.2 * (x as f32 * 0.31).sin() * (y as f32 * 0.23).sin()
            });
            px.push(0.05);
        }
    }
    Rgb { w, px }
}

/// Rows of small "letters" (1-2 px strokes on a dark ground) inside x0..x1, y0..y1.
fn stamp_text(c: &mut Rgb, x0: i32, y0: i32, x1: i32, y1: i32) {
    for y in y0..y1 {
        for x in x0..x1 {
            let (line, col, letter) = ((y - y0) % 14, (x - x0) % 8, (x - x0) / 8);
            let mut ink = false;
            if (2..11).contains(&line) {
                let shape =
                    (letter as u32).wrapping_mul(2654435761).wrapping_add((((y - y0) / 14) as u32).wrapping_mul(40503))
                        >> 13;
                ink = col == 1 + (shape & 1) as i32
                    || col == 5
                    || (line == 2 + ((shape >> 3) & 3) as i32 * 2 && col < 6);
            }
            let i = ((y * c.w + x) * 3) as usize;
            let v = if ink { 0.9 } else { 0.08 };
            c.px[i..i + 3].fill(v);
        }
    }
}

/// upload() takes XRGB bytes: B, G, R, X.
fn bgrx(c: &Rgb) -> Vec<u8> {
    let byte = |v: f32| (v.clamp(0.0, 1.0) * 255.0).round() as u8;
    c.px.chunks_exact(3).flat_map(|p| [byte(p[2]), byte(p[1]), byte(p[0]), 255]).collect()
}

// ---- helpers ---------------------------------------------------------------------------------------------------------

/// RGB, top row first, 3 bytes per pixel.
struct Image {
    w: i32,
    h: i32,
    rgb: Vec<u8>,
}

impl Image {
    fn at(&self, x: i32, y: i32) -> &[u8] {
        let i = ((y * self.w + x) * 3) as usize;
        &self.rgb[i..i + 3]
    }

    fn half(&self, eye: i32) -> Image {
        let w = self.w / 2;
        let mut rgb = Vec::with_capacity((w * self.h * 3) as usize);
        for y in 0..self.h {
            let i = ((y * self.w + eye * w) * 3) as usize;
            rgb.extend_from_slice(&self.rgb[i..i + (w * 3) as usize]);
        }
        Image { w, h: self.h, rgb }
    }
}

fn read(gl: &GlCompositor, w: i32, h: i32) -> Image {
    let bottom_first = gl.read_rgb();
    let row = (w * 3) as usize;
    let rgb = bottom_first.chunks_exact(row).rev().flatten().copied().collect();
    Image { w, h, rgb }
}

fn finish(gl: &GlCompositor) {
    // SAFETY: the compositor's context is current on this thread.
    unsafe { gl.gl().raw().finish() };
}

fn draw(gl: &mut GlCompositor, tiles: &[FRect], at: &[Placement]) {
    gl.draw_and_swap(tiles, at, -1, 0.0, &IDENTITY, HALF_W, HALF_H, None);
}

fn set_depth(gl: &mut GlCompositor, i: usize, d: &Plane, seq: u64) {
    gl.set_depth(i, &DepthInput::new(d.w, d.h, seq, DepthData::Float(&d.px)));
}

/// Draws until the fades are over, depth arriving all the while, and leaves the settled frame in the buffer.
fn settle(gl: &mut GlCompositor, tiles: &[FRect], at: &[Placement], depth: &[&Plane], ms: u64) {
    let end = Instant::now() + Duration::from_millis(ms);
    loop {
        for (i, d) in depth.iter().enumerate() {
            set_depth(gl, i, d, 1);
        }
        draw(gl, tiles, at);
        std::thread::sleep(Duration::from_millis(25));
        if Instant::now() >= end {
            break;
        }
    }
    finish(gl);
}

fn stereo(strength: f32, convergence: f32) -> StereoSettings {
    StereoSettings {
        enabled: true,
        params: StereoParams { strength, convergence, ..StereoParams::default() },
        half_baseline: 0.0,
        ..StereoSettings::default()
    }
}

fn full(w: i32, h: i32) -> FRect {
    FRect { x: 0.0, y: 0.0, width: w as f32, height: h as f32 }
}

fn offscreen(w: i32, h: i32, sources: usize) -> GlCompositor {
    GlCompositor::create_offscreen(w as u32, h as u32, sources).expect("offscreen compositor")
}

/// Best horizontal shift of `b` against `a` for a block of `a` at (x, y): positive when the block sits further right in
/// `a` (a near thing's disparity, a = left eye). SAD with a parabola on top.
fn block_match(a: &Image, b: &Image, x: i32, y: i32, half_block: i32, range: i32) -> f32 {
    let mut cost = vec![0f32; (2 * range + 1) as usize];
    let (mut best, mut at) = (1e18f32, 0);
    for s in -range..=range {
        let mut sad = 0f32;
        for j in -half_block..=half_block {
            for i in -half_block..=half_block {
                let (p, q) = (a.at(x + i, y + j), b.at(x + i - s, y + j));
                sad += (0..3).map(|k| (p[k] as i32 - q[k] as i32).abs() as f32).sum::<f32>();
            }
        }
        cost[(s + range) as usize] = sad;
        if sad < best {
            best = sad;
            at = s;
        }
    }
    if at == -range || at == range {
        return at as f32;
    }
    let (lo, hi) = (cost[(at + range - 1) as usize], cost[(at + range + 1) as usize]);
    let d = lo + hi - 2.0 * best;
    if d > 1e-3 { at as f32 + 0.5 * (lo - hi) / d } else { at as f32 }
}

fn read_ppm(path: &std::path::Path) -> Option<Image> {
    let data = std::fs::read(path).ok()?;
    let mut fields = Vec::new();
    let mut pos = 0;
    while fields.len() < 4 {
        while data.get(pos)?.is_ascii_whitespace() {
            pos += 1;
        }
        let start = pos;
        while !data.get(pos)?.is_ascii_whitespace() {
            pos += 1;
        }
        fields.push(std::str::from_utf8(&data[start..pos]).ok()?.to_owned());
    }
    pos += 1;
    let (w, h): (i32, i32) = (fields[1].parse().ok()?, fields[2].parse().ok()?);
    if fields[0] != "P6" || fields[3] != "255" {
        return None;
    }
    let rgb = data.get(pos..pos + (w * h * 3) as usize)?.to_vec();
    Some(Image { w, h, rgb })
}

fn write_ppm(path: &std::path::Path, im: &Image) -> bool {
    let mut out = format!("P6\n{} {}\n255\n", im.w, im.h).into_bytes();
    out.extend_from_slice(&im.rgb);
    std::fs::write(path, out).is_ok()
}

// ---- checks ----------------------------------------------------------------------------------------------------------

fn goldens(dir: &std::path::Path, write: bool) {
    const W: i32 = 240;
    const H: i32 = 150;
    let mut text = textured(W, H);
    stamp_text(&mut text, 20, 12, 120, 70);
    let scenes = [
        ("stereo_box", box_picture(W, H), box_depth(320, 256)),
        ("stereo_ramp", textured(W, H), ramp_depth(320, 256)),
        ("stereo_text", text, bump_depth(320, 256, 2)),
    ];
    for (name, colour, depth) in &scenes {
        let mut gl = offscreen(W, H, 1);
        gl.resize(W as u32, H as u32, 2 * W as u32, H as u32);
        gl.set_stereo(&stereo(1.6, 0.4));
        check!(gl.upload(0, &bgrx(colour), W as u32, H as u32, 4 * W as u32));
        settle(&mut gl, &[full(W, H)], &[Placement::default()], &[depth], 360);
        let got = read(&gl, 2 * W, H);
        let path = dir.join(format!("{name}.ppm"));
        if write {
            check!(write_ppm(&path, &got));
            continue;
        }
        let Some(want) = read_ppm(&path) else {
            check!(false);
            eprintln!("cannot read {}", path.display());
            continue;
        };
        if want.w != got.w || want.h != got.h {
            check!(false);
            continue;
        }
        let (mut off, mut worst) = (0, 0);
        for (g, w) in got.rgb.chunks_exact(3).zip(want.rgb.chunks_exact(3)) {
            let e = (0..3).map(|k| (g[k] as i32 - w[k] as i32).abs()).max().unwrap();
            worst = worst.max(e);
            off += (e > 16) as i32;
        }
        println!("golden {name}: {off} of {} pixels off by more than 16 (worst {worst})", W * 2 * H);
        check!(off <= W * 2 * H / 100);
    }
}

fn measured_disparity() {
    const W: i32 = 960;
    const H: i32 = 600;
    let mut gl = offscreen(W, H, 1);
    gl.resize(W as u32, H as u32, 2 * W as u32, H as u32);
    let colour = textured(W, H);
    check!(gl.upload(0, &bgrx(&colour), W as u32, H as u32, 4 * W as u32));
    let tiles = [full(W, H)];
    let at = [Placement::default()];
    let source_max = colour.px.iter().fold(0f32, |m, &v| m.max(v));
    // Exact on a flat depth: pushed in and out by a known amount.
    for d in [0.75f32, 0.25, 0.5] {
        let set = stereo(1.0, 0.5);
        gl.set_stereo(&set);
        settle(&mut gl, &tiles, &at, &[&flat_depth(320, 256, d)], 360);
        let sbs = read(&gl, 2 * W, H);
        let (left, right) = (sbs.half(0), sbs.half(1));
        let limits = stereo_limits(&set.params, W as f32, HALF_W);
        let v = stereo_v(d, &set.params);
        let want = 2.0 * stereo_half_disparity(v, &stereo_budget(&limits, 0.0, 1.0, 1.0));
        let (mut got, mut n) = (0f32, 0);
        for y in (150..450).step_by(50) {
            for x in (300..660).step_by(60) {
                got += block_match(&left, &right, x, y, 7, 40);
                n += 1;
            }
        }
        got /= n as f32;
        println!("flat depth {d:.2}: disparity {got:.2} px, expected {want:.2}");
        check!((got - want).abs() < 0.6);
    }
    // The harshest settings on rough depth: never beyond the limits, never brighter than the source.
    let (mut most_near, mut most_far, mut brightest) = (0f32, 0f32, 0i32);
    for convergence in [0.0f32, 1.0] {
        let mut set = stereo(STEREO_STRENGTH_MAX, convergence);
        set.params.near_deg = STEREO_NEAR_DEG_MAX;
        set.params.far_deg = STEREO_FAR_DEG_MAX;
        gl.set_stereo(&set);
        settle(&mut gl, &tiles, &at, &[&bump_depth(320, 256, 9)], 340);
        let sbs = read(&gl, 2 * W, H);
        let (left, right) = (sbs.half(0), sbs.half(1));
        for y in (40..H - 40).step_by(16) {
            for x in (90..W - 90).step_by(16) {
                let s = block_match(&left, &right, x, y, 5, 64);
                most_near = most_near.max(s);
                most_far = most_far.min(s);
            }
        }
        brightest = brightest.max(sbs.rgb.iter().copied().max().unwrap_or(0) as i32);
    }
    let top = StereoParams {
        near_deg: STEREO_NEAR_DEG_MAX,
        far_deg: STEREO_FAR_DEG_MAX,
        ..StereoParams::default()
    };
    let limits = stereo_limits(&top, W as f32, HALF_W);
    let source = (source_max * 255.0 + 0.5) as i32;
    println!(
        "harshest settings: measured {most_near:.1} near / {most_far:.1} far px, limits {:.1} / {:.1}; brightest \
         {brightest} (source {source})",
        limits.near_px, limits.far_px
    );
    check!(most_near <= limits.near_px + 1.5);
    check!(-most_far <= limits.far_px + 1.5);
    check!(most_near > limits.near_px * 0.5); // and the test really reached them
    check!(-most_far > limits.far_px * 0.5);
    check!(brightest <= source + 1);
}

fn live_strength() {
    const W: i32 = 640;
    const H: i32 = 400;
    let mut gl = offscreen(2 * W, H, 1);
    gl.resize(W as u32, H as u32, 2 * W as u32, H as u32);
    let mut set = stereo(1.0, 0.5);
    gl.set_stereo(&set);
    check!(gl.upload(0, &bgrx(&box_picture(W, H)), W as u32, H as u32, 4 * W as u32));
    let tiles = [full(W, H)];
    let at = [Placement::default()];
    settle(&mut gl, &tiles, &at, &[&box_depth(320, 256)], 360);
    let difference = |gl: &mut GlCompositor| {
        draw(gl, &tiles, &at);
        finish(gl);
        let sbs = read(gl, 2 * W, H);
        let mut changed = 0;
        for y in 20..H - 20 {
            for x in 20..W - 20 {
                changed += (sbs.at(x, y) != sbs.at(x + W, y)) as i32;
            }
        }
        changed
    };
    check!(difference(&mut gl) > 100);
    set.params.strength = 0.0;
    gl.set_stereo(&set);
    check!(difference(&mut gl) == 0); // No fresh depth frame: the desktop is completely still.
    set.strengths = vec![100];
    gl.set_stereo(&set);
    check!(difference(&mut gl) > 100); // The per-desktop override is live too.
    set.strengths = vec![0];
    gl.set_stereo(&set);
    check!(difference(&mut gl) == 0);
}

fn frame_time() {
    const W: i32 = 1920;
    const H: i32 = 1200;
    let mut gl = offscreen(W, H, 3);
    gl.resize(W as u32, H as u32, 2 * W as u32, H as u32);
    gl.set_stereo(&StereoSettings { enabled: true, ..StereoSettings::default() });
    let mut colour = textured(W, H);
    stamp_text(&mut colour, 100, 100, 800, 500);
    let bytes = bgrx(&colour);
    for i in 0..3 {
        check!(gl.upload(i, &bytes, W as u32, H as u32, 4 * W as u32));
    }
    let depth = [bump_depth(518, 324, 1), bump_depth(518, 324, 2), bump_depth(518, 324, 3)];
    // Worst case: a maximized desktop, every eye pixel warped; the other two behind it get their depth frames too.
    let tiles = [full(W, H); 3];
    let place = |distance| Placement { distance, ..Placement::default() };
    let at = [place(1.2), place(1.1), Placement::default()];
    settle(&mut gl, &tiles, &at, &[&depth[0], &depth[1], &depth[2]], 340);
    let renderer = gl.strings().1.to_owned();
    // A shared GPU (the desktop, a live session) adds noise of its own: five rounds, the gate on the quietest one.
    let mut rounds = Vec::new();
    for _ in 0..5 {
        let mut frames = Vec::with_capacity(240);
        let (mut depth_ms, mut depth_n) = (0f64, 0);
        for f in 0..240u64 {
            let t0 = Instant::now();
            if f % 4 == 0 {
                // 30 Hz at 120.
                for (i, d) in depth.iter().enumerate() {
                    set_depth(&mut gl, i, d, f);
                }
                finish(&gl);
                depth_ms += t0.elapsed().as_secs_f64() * 1e3;
                depth_n += 1;
            }
            draw(&mut gl, &tiles, &at);
            finish(&gl);
            frames.push(t0.elapsed().as_secs_f64() * 1e3);
        }
        frames.sort_by(f64::total_cmp);
        let (median, p99) = (frames[frames.len() / 2], frames[frames.len() * 99 / 100]);
        println!(
            "3840x1200 on {renderer}: frame median {median:.3} ms, p99 {p99:.3} ms, depth update of 3 desktops {:.3} ms",
            depth_ms / depth_n as f64
        );
        rounds.push((median, p99));
    }
    let (median, p99) = rounds.iter().copied().min_by(|a, b| a.0.total_cmp(&b.0)).unwrap();
    println!("stereo frame time, quietest of {} rounds: median {median:.3} ms, p99 {p99:.3} ms", rounds.len());
    if renderer.contains("7800") {
        check!(median <= 0.41);
        check!(p99 < 1000.0 / 120.0);
    }
}

fn main() {
    let args: Vec<String> = std::env::args().skip(1).filter(|a| !a.starts_with("--") || a == "--write-goldens").collect();
    let write = args.first().is_some_and(|a| a == "--write-goldens");
    let golden = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../../spike/tests/golden");
    let dir = match (write, args.get(1)) {
        (true, Some(d)) => std::path::PathBuf::from(d),
        (true, None) => {
            eprintln!("--write-goldens DIR");
            std::process::exit(2);
        }
        _ => golden,
    };
    match GlCompositor::create_offscreen(64, 64, 1) {
        Err(why) => {
            println!("SKIP: {why}");
            std::process::exit(77);
        }
        Ok(mut probe) => {
            probe.set_stereo(&StereoSettings { enabled: true, ..StereoSettings::default() });
            if !probe.stereo_available() {
                println!("SKIP: stereo not available on this GL");
                std::process::exit(77);
            }
        }
    }
    goldens(&dir, write);
    if !write {
        measured_disparity();
        live_strength();
        frame_time();
    }
    // SAFETY: single-threaded test binary.
    let failures = unsafe { FAILURES };
    if failures > 0 {
        eprintln!("stereo_render: {failures} failure(s)");
        std::process::exit(1);
    }
    println!("stereo_render: ok");
}
