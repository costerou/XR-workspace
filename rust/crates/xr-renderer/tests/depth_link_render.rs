//! The renderer's end of the depth engine (src/depth_link.rs), on a real GPU off screen, a port of the spike's
//! tests/depth_link_render_test.cpp: the real GlCompositor and DepthLink against a stand-in engine in this process,
//! through the real XRD1 shared memory in a real memfd. What the feature rests on:
//!   * a blank memfd gets the layout, and the next renderer of a run takes it over with the engine still attached;
//!   * nothing is fed or drawn in 3D before the engine's heartbeat, and a desktop gets depth only while asked for;
//!   * the picture reaches the engine the right way up and in the right colours;
//!   * maps reach the compositor: the eyes differ; a still desktop stays 3D though no new map comes; a crash goes flat,
//!     by itself, with every frame still drawn; a restarted engine brings 3D back;
//!   * the budget: never more than DEPTH_PER_TICK shrinks a frame, however many desktops.
//!
//! Exit 77 (skipped) without EGL/GLES or without the stereo pass.

use std::os::fd::{AsFd, OwnedFd};
use std::time::{Duration, Instant};

use glow::HasContext;
use rustix::fs::{MemfdFlags, ftruncate, memfd_create};
use rustix::mm::{MapFlags, ProtFlags, mmap, munmap};
use xr_renderer::depth::{
    DEPTH_ENABLED, DEPTH_ENGINE_TIMEOUT_NS, DEPTH_FOCUSED, DEPTH_LINK_SHM, DepthFormat, DepthFrame, DepthShm, Side,
    depth_shm_bytes,
};
use xr_renderer::depth_feed::DepthFeed;
use xr_renderer::depth_link::{DEPTH_PER_TICK, DepthLink, Tick};
use xr_renderer::gl::{GlCompositor, StereoSettings};
use xr_renderer::pose::IDENTITY;
use xr_renderer::stereo::StereoParams;
use xr_renderer::view::{FRect, Placement};

const W: i32 = 640;
const H: i32 = 400;

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

fn now_ns() -> u64 {
    let t = rustix::time::clock_gettime(rustix::time::ClockId::Monotonic);
    t.tv_sec as u64 * 1_000_000_000 + t.tv_nsec as u64
}

fn sleep_ms(ms: u64) {
    std::thread::sleep(Duration::from_millis(ms));
}

/// Top half red, bottom half blue on a green stripe texture, as XRGB bytes, so the way up and the colours can be read.
fn picture() -> Vec<u8> {
    let mut px = vec![0u8; (W * H * 4) as usize];
    for y in 0..H {
        for x in 0..W {
            let (top, line) = (y < H / 2, (x / 9 + y / 7) % 2 == 0);
            let i = ((y * W + x) * 4) as usize;
            px[i..i + 4].copy_from_slice(&[
                if top { 20 } else { 220 },
                if line { 150 } else { 40 },
                if top { 220 } else { 20 },
                255,
            ]);
        }
    }
    px
}

/// The engine's half of the wire, in a few lines: a heartbeat, and a map of a near middle.
struct FakeEngine {
    shm: DepthShm,
    seq: u64,
    map: Vec<u8>,
}

impl FakeEngine {
    fn new(shm: DepthShm) -> Self {
        let (w, h) = (DEPTH_LINK_SHM.out_w, DEPTH_LINK_SHM.out_h);
        let mut map = vec![0u16; (w * h) as usize]; // 0.0: far
        for y in h / 4..3 * h / 4 {
            for x in w / 4..3 * w / 4 {
                map[(y * w + x) as usize] = 0x3c00; // 1.0: near
            }
        }
        Self { shm, seq: 0, map: map.iter().flat_map(|v| v.to_ne_bytes()).collect() }
    }
    fn beat(&self) {
        self.shm.heartbeat(now_ns(), 4242);
    }
    fn publish(&mut self, channel: u32) {
        self.seq += 1;
        let f = DepthFrame {
            w: DEPTH_LINK_SHM.out_w,
            h: DEPTH_LINK_SHM.out_h,
            source_seq: self.seq,
            source_ns: now_ns(),
            done_ns: now_ns(),
            format: DepthFormat::R16F,
            flags: 0,
            pixels: &self.map,
        };
        check!(self.shm.publish(Side::Output, channel, &f));
    }
}

struct Rig {
    gl: GlCompositor,
    tiles: Vec<FRect>,
    placements: Vec<Placement>,
    frames: [u64; 4],
    wanted: [bool; 4],
}

impl Rig {
    fn draw(&mut self) -> bool {
        let ok = self.gl.draw_and_swap(&self.tiles, &self.placements, -1, 0.0, &IDENTITY, 0.44, 0.275, None);
        self.finish();
        ok
    }
    fn finish(&self) {
        // SAFETY: the compositor's context is current on this thread.
        unsafe { self.gl.gl().raw().finish() };
    }
    fn tick(&mut self, link: &mut DepthLink) {
        let (wanted, frames) = (self.wanted, self.frames);
        let t = Tick {
            wanted: &wanted,
            frames: &frames,
            focused: 0,
            drawing: true,
            convergence: 50.0,
            range_ms: 0.0,
            now_ns: now_ns(),
        };
        link.tick(&mut self.gl, &t);
    }
    /// How far apart the eyes are, per pixel, in the middle of the view: 0 is a flat picture.
    fn eyes_apart(&self) -> f32 {
        let px = self.gl.read_rgb(); // bottom first; the rows used are symmetric about the middle
        let (mut sum, mut n) = (0f64, 0usize);
        for y in H / 3..2 * H / 3 {
            for x in W / 3..2 * W / 3 {
                let a = ((y * 2 * W + x) * 3) as usize;
                let b = a + (W * 3) as usize;
                sum += (0..3).map(|k| (px[a + k] as i32 - px[b + k] as i32).abs() as f64).sum::<f64>();
                n += 1;
            }
        }
        (sum / n as f64 / 3.0) as f32
    }
}

/// Runs `body` every `step` ms for `ms` ms.
fn for_ms(ms: u64, step: u64, mut body: impl FnMut()) {
    let end = Instant::now() + Duration::from_millis(ms);
    loop {
        body();
        sleep_ms(step);
        if Instant::now() >= end {
            break;
        }
    }
}

fn memfd(bytes: usize) -> OwnedFd {
    let fd = memfd_create("depth-link-test", MemfdFlags::CLOEXEC).expect("memfd_create");
    ftruncate(&fd, bytes as u64).expect("ftruncate");
    fd
}

fn main() {
    let mut gl = match GlCompositor::create_offscreen(2 * W as u32, H as u32, 4) {
        Ok(gl) => gl,
        Err(why) => {
            println!("SKIP: {why}");
            std::process::exit(77);
        }
    };
    gl.resize(W as u32, H as u32, 2 * W as u32, H as u32);
    let bytes = depth_shm_bytes(&DEPTH_LINK_SHM);
    let fd = memfd(bytes);
    // SAFETY: a fresh shared mapping of the whole file, unmapped at the end after every view of it is gone.
    let base = unsafe {
        mmap(std::ptr::null_mut(), bytes, ProtFlags::READ | ProtFlags::WRITE, MapFlags::SHARED, &fd, 0)
    }
    .expect("mmap")
    .cast::<u8>();

    let px = picture();
    for i in 0..4 {
        check!(gl.upload(i, &px, W as u32, H as u32, 4 * W as u32));
    }
    let tiles = (0..4).map(|i| FRect { x: (i * W) as f32, y: 0.0, width: W as f32, height: H as f32 }).collect();
    let mut set = StereoSettings {
        enabled: true,
        half_baseline: 0.0,
        params: StereoParams {
            near_deg: 1.5,
            far_deg: 1.0,
            strength: 2.0,
            text_keep: 1.0,
            ..StereoParams::default()
        },
        desktops: vec![true, false, false, false],
        ..StereoSettings::default()
    };
    gl.set_stereo(&set);
    if !gl.stereo_available() {
        println!("SKIP: stereo not available on this GL");
        std::process::exit(77);
    }
    let mut r = Rig {
        gl,
        tiles,
        placements: vec![Placement::default(); 4],
        frames: [1; 4],
        wanted: [true, false, false, false],
    };

    // A wrong-sized memory is refused, and no link means nothing happens.
    {
        let small = memfd(4096);
        check!(DepthLink::open(small.as_fd()).is_err_and(|why| !why.is_empty()));
    }
    let mut link = DepthLink::open(fd.as_fd()).expect("DepthLink::open");
    // SAFETY: the engine's side of the same mapping, alive until the munmap at the end.
    let view = unsafe { DepthShm::attach(base, bytes) }.expect("attach");
    // SAFETY: as above, a second view for the fake engine.
    let mut engine = FakeEngine::new(unsafe { DepthShm::attach(base, bytes) }.expect("attach"));
    let mut seen = Vec::new();

    // No heartbeat yet: the wish reaches the engine's side, nothing else moves.
    r.tick(&mut link);
    check!(view.control(0) == (DEPTH_ENABLED | DEPTH_FOCUSED) && view.control(1) == 0);
    check!(link.feeds() == 0 && !link.engine_alive());
    check!(r.draw());
    check!(r.eyes_apart() < 1.0);

    // The engine comes up: the picture reaches it, once for each new frame, right way up and in the right colours.
    engine.beat();
    r.tick(&mut link);
    check!(link.engine_alive() && link.feeds() == 1);
    r.finish();
    let deadline = Instant::now() + Duration::from_secs(2);
    let mut got = None;
    while got.is_none() && Instant::now() < deadline {
        engine.beat();
        r.tick(&mut link);
        got = view.fetch(Side::Input, 0, &mut seen, 0).map(|f| (f.w, f.h, f.format));
    }
    check!(got.is_some());
    if let Some((w, h, format)) = got {
        check!(w == DEPTH_LINK_SHM.in_w && h == DEPTH_LINK_SHM.in_h);
        check!(format == DepthFormat::Bgra8); // an SHM upload is B, G, R in the texture
        let at = |row: i32| ((row * w + w / 2) * 4) as usize;
        let (top, bottom) = (&seen[at(h / 8)..], &seen[at(h * 7 / 8)..]);
        check!(top[0] < 60 && top[2] > 180); // in BGRA: the top is red, B low and R high
        check!(bottom[0] > 180 && bottom[2] < 60); // and the bottom blue
    }
    check!(link.feeds() == 1); // the frame count has not moved, so the same picture does not go twice

    // The budget: however many desktops have new pictures, DEPTH_PER_TICK shrinks a tick.
    sleep_ms(40); // past the first desktop's rate limit
    r.wanted = [true; 4];
    r.frames = [2; 4];
    engine.beat();
    let before = link.feeds();
    r.tick(&mut link);
    check!(link.feeds() == before + DEPTH_PER_TICK as u64);
    r.finish();
    sleep_ms(40);
    engine.beat();
    r.tick(&mut link); // the rest, in their turn
    check!(link.feeds() == before + 4);
    r.finish();
    sleep_ms(40);
    engine.beat();
    r.tick(&mut link);
    check!(link.feeds() == before + 4); // the same frames again: nothing new to send, whatever the turn says
    r.wanted = [true, false, false, false];

    // A map arrives: 3D.
    engine.beat();
    engine.publish(0);
    r.tick(&mut link);
    check!(link.maps() == 1);
    engine.publish(0);
    for_ms(450, 16, || {
        engine.beat();
        r.tick(&mut link);
        check!(r.draw());
    });
    let deep = r.eyes_apart();
    println!("eyes apart with depth: {deep:.2}/255");
    check!(deep > 3.0);

    // A still desktop: no new map for a good while past STEREO_STALE_MS, the engine alive. It stays 3D.
    for_ms(800, 16, || {
        engine.beat();
        r.tick(&mut link);
        check!(r.draw());
    });
    let held = r.eyes_apart();
    println!("still desktop, no new maps for 800 ms: {held:.2}/255");
    check!(held > 0.8 * deep);

    // The engine crashes: no more heartbeats, and no warning. The frames keep coming, and the desktop eases flat.
    let (mut bad, mut frames) = (0, 0);
    for_ms(1500, 8, || {
        r.tick(&mut link);
        bad += !r.draw() as i32;
        frames += 1;
    });
    let flat = r.eyes_apart();
    println!("after a crash, {frames} frames drawn, {bad} failed: {flat:.2}/255");
    check!(!link.engine_alive() && bad == 0 && frames > 40);
    check!(flat < 0.5);
    let feeds_dead = link.feeds();
    r.frames = [3; 4];
    r.tick(&mut link);
    check!(link.feeds() == feeds_dead); // nothing is fed to a dead engine

    // The renderer starts again in the same run, the engine with it: the memory is taken over, not wiped.
    engine.beat();
    link.stop();
    check!(view.control(0) == 0);
    drop(link);
    let mut link = DepthLink::open(fd.as_fd()).expect("DepthLink::open again");
    check!(view.engine_alive(now_ns(), DEPTH_ENGINE_TIMEOUT_NS)); // the heartbeat survived: not laid out again
    for_ms(500, 16, || {
        engine.beat();
        engine.publish(0);
        r.tick(&mut link);
        check!(r.draw());
    });
    let again = r.eyes_apart();
    println!("after a restart: {again:.2}/255");
    check!(again > 0.8 * deep);

    // A desktop not asked for never gets depth, whatever the engine publishes for it, and 3D off goes flat.
    r.wanted = [false; 4];
    engine.publish(0);
    let maps = link.maps();
    r.tick(&mut link);
    check!(link.maps() == maps && view.control(0) == 0);
    set.enabled = false;
    r.gl.set_stereo(&set);
    for_ms(450, 16, || check!(r.draw()));
    check!(r.eyes_apart() < 0.5); // a buffer still side by side: the same picture for each eye
    println!("report: {}", link.report());

    // A plain RGBA GL_TEXTURE_2D, sampled as it is, not swapped; row 0 of the shrink is the source's top.
    {
        let raw = r.gl.gl().raw();
        let mut feed = DepthFeed::new(r.gl.shared_gl(), DEPTH_LINK_SHM.in_w, DEPTH_LINK_SHM.in_h).expect("feed");
        let mut rgba = vec![0u8; 64 * 64 * 4];
        for y in 0..64 {
            for x in 0..64 {
                let i = (y * 64 + x) * 4;
                rgba[i..i + 4].copy_from_slice(&[if y < 32 { 240 } else { 10 }, 30, if y < 32 { 10 } else { 240 }, 255]);
            }
        }
        // SAFETY: plain GL calls on the current context; the texture is deleted below.
        let tex = unsafe {
            let tex = raw.create_texture().expect("texture");
            raw.bind_texture(glow::TEXTURE_2D, Some(tex));
            for (p, v) in [
                (glow::TEXTURE_MIN_FILTER, glow::LINEAR),
                (glow::TEXTURE_MAG_FILTER, glow::LINEAR),
                (glow::TEXTURE_WRAP_S, glow::CLAMP_TO_EDGE),
                (glow::TEXTURE_WRAP_T, glow::CLAMP_TO_EDGE),
            ] {
                raw.tex_parameter_i32(glow::TEXTURE_2D, p, v as i32);
            }
            raw.tex_image_2d(
                glow::TEXTURE_2D,
                0,
                glow::RGBA as i32,
                64,
                64,
                0,
                glow::RGBA,
                glow::UNSIGNED_BYTE,
                glow::PixelUnpackData::Slice(Some(&rgba)),
            );
            tex
        };
        check!(feed.begin_texture(tex));
        // SAFETY: as above.
        check!(unsafe { raw.get_error() } == glow::NO_ERROR);
        let mut out = Vec::new();
        r.finish();
        let done = Instant::now() + Duration::from_secs(2);
        while !feed.collect(&mut out) && Instant::now() < done {
            sleep_ms(1);
        }
        check!(!out.is_empty());
        if !out.is_empty() {
            check!(out[0] > 200 && out[2] < 50); // row 0, the source's top: red
            let q = &out[((DEPTH_LINK_SHM.in_h - 1) * DEPTH_LINK_SHM.in_w * 4) as usize..];
            check!(q[0] < 50 && q[2] > 200);
        }
        drop(feed);
        // SAFETY: made above, no longer used.
        unsafe { raw.delete_texture(tex) };
    }

    drop(link);
    drop((view, engine));
    // SAFETY: mapped above; every view of it is gone.
    let _ = unsafe { munmap(base.cast(), bytes) };
    // SAFETY: single-threaded test binary.
    let failures = unsafe { FAILURES };
    if failures > 0 {
        eprintln!("depth_link_render: {failures} failure(s)");
        std::process::exit(1);
    }
    println!("depth_link_render: ok");
}
