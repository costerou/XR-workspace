//! VR video playback timing harness (docs/vr-video.md#performance; port of spike/tests/video_bench.cpp). Not a test:
//! it is slow, needs a GPU and its numbers depend on machine load, so `cargo test` never runs it. Run by hand:
//!   cargo run --release -p xr-renderer --bin video_bench -- [--gpu /dev/dri/renderD128] [--seconds 6] [--hz 120]
//!       [--dir DIR] [--case NAME]... [--restarts N] [--json]
//! It drives the real Player and Sphere the way the renderer's frame loop does (update, project into a 1920x1200
//! target, swap report) on a surfaceless EGL context, at a simulated display rate, and reports per clip the same
//! fields as the C++ bench: hwdec, first frame, update CPU and GPU time, busy time and frames over budget, frames
//! shown and dropped (read from a frame-number barcode burned into each generated clip), judder, mpv's lead and drops.
//! --restarts N: a web video's seek restarts the relay and reopens the player; measures the stall of each close + open
//! and the time to the next first frame. Clips are generated with ffmpeg (VA-API encoder when it has one), never
//! downloaded, and kept in --dir between runs.

use std::collections::BTreeMap;
use std::ffi::c_void;
use std::fs::File;
use std::os::fd::OwnedFd;
use std::path::Path;
use std::process::Command;
use std::rc::Rc;

use glow::{HasContext, PixelPackData};
use serde_json::{Value, json};
use xr_renderer::egl::{self, Headless};
use xr_renderer::video::math::{Format, IDENTITY, State, half_fov_tan};
use xr_renderer::video::player::{Options as PlayerOptions, Player};
use xr_renderer::video::sphere::Sphere;

const GL_TIMESTAMP_EXT: u32 = 0x8E28;
const GL_QUERY_RESULT_EXT: u32 = 0x8866;
const OPTIONS: PlayerOptions = PlayerOptions {
    audio: false,
    hwdec: true,
    display: None,
};

fn now_ns() -> u64 {
    let mut ts = libc::timespec {
        tv_sec: 0,
        tv_nsec: 0,
    };
    // SAFETY: ts is a valid out pointer.
    unsafe { libc::clock_gettime(libc::CLOCK_MONOTONIC, &mut ts) };
    ts.tv_sec as u64 * 1_000_000_000 + ts.tv_nsec as u64
}

fn ms(ns: u64) -> f64 {
    ns as f64 / 1e6
}

type GenQueries = unsafe extern "system" fn(i32, *mut u32);
type QueryCounter = unsafe extern "system" fn(u32, u32);
type QueryU64 = unsafe extern "system" fn(u32, u32, *mut u64);

/// GL_EXT_disjoint_timer_query, if the context has it.
struct Timer {
    gen_queries: GenQueries,
    counter: QueryCounter,
    get: QueryU64,
}

impl Timer {
    fn load(gl: &glow::Context) -> Option<Self> {
        if !gl
            .supported_extensions()
            .contains("GL_EXT_disjoint_timer_query")
        {
            return None;
        }
        let p = |n: &std::ffi::CStr| {
            let f = egl::proc_address(n);
            (!f.is_null()).then_some(f)
        };
        let (g, c, q) = (
            p(c"glGenQueriesEXT")?,
            p(c"glQueryCounterEXT")?,
            p(c"glGetQueryObjectui64vEXT")?,
        );
        // SAFETY: each pointer is the named GL entry point, whose C signature the type spells.
        unsafe {
            Some(Self {
                gen_queries: std::mem::transmute::<*mut c_void, GenQueries>(g),
                counter: std::mem::transmute::<*mut c_void, QueryCounter>(c),
                get: std::mem::transmute::<*mut c_void, QueryU64>(q),
            })
        }
    }
}

struct Clip {
    name: &'static str,
    codec: &'static str,
    w: u32,
    h: u32,
    fps: u32,
    pix: &'static str,
    mbit: u32,
}

const CLIPS: [Clip; 5] = [
    Clip {
        name: "4k60-h264",
        codec: "h264",
        w: 3840,
        h: 1920,
        fps: 60,
        pix: "nv12",
        mbit: 40,
    },
    Clip {
        name: "5.7k60-hevc",
        codec: "hevc",
        w: 5760,
        h: 2880,
        fps: 60,
        pix: "nv12",
        mbit: 60,
    },
    Clip {
        name: "8k30-hevc",
        codec: "hevc",
        w: 8192,
        h: 4096,
        fps: 30,
        pix: "nv12",
        mbit: 60,
    },
    Clip {
        name: "8k60-hevc10",
        codec: "hevc",
        w: 8192,
        h: 4096,
        fps: 60,
        pix: "p010",
        mbit: 100,
    },
    Clip {
        name: "8k30-av1",
        codec: "av1",
        w: 8192,
        h: 4096,
        fps: 30,
        pix: "nv12",
        mbit: 60,
    },
];

// Frame-number barcode: 12 blocks of 32x32 at the picture's top-left, white = bit set (frame number mod 4096).
const BITS: i32 = 12;
const BLOCK: i32 = 32;

/// Empty on success, else what the command printed.
fn run_quiet(cmd: &str) -> String {
    match Command::new("sh")
        .arg("-c")
        .arg(format!("{cmd} 2>&1"))
        .output()
    {
        Ok(o) if o.status.success() => String::new(),
        Ok(o) if !o.stdout.is_empty() => {
            String::from_utf8_lossy(&o.stdout[..o.stdout.len().min(4000)]).into_owned()
        }
        _ => "failed".into(),
    }
}

fn generate(ffmpeg: &str, gpu: &str, c: &Clip, seconds: f64, path: &str) -> bool {
    if std::fs::metadata(path).is_ok_and(|m| m.len() > 100_000) {
        return true;
    }
    let tmp = format!("{path}.part.mp4");
    let (fps, dur) = (c.fps, seconds.ceil() as i64);
    let graph = format!(
        "testsrc2=s={}x{}:r={fps}:d={dur}[bg];nullsrc=s={}x{BLOCK}:r={fps}:d={dur},format=yuv420p,\
         geq=lum='if(bitand(N\\,pow(2\\,floor(X/{BLOCK})))\\,235\\,16)':cb=128:cr=128[code];[bg][code]overlay=0:0:shortest=1",
        c.w,
        c.h,
        BITS * BLOCK
    );
    let common = format!("'{ffmpeg}' -nostdin -hide_banner -loglevel error -y ");
    let audio =
        format!(" -f lavfi -i sine=frequency=440:duration={dur} -c:a aac -b:a 128k -shortest ");
    let node = if gpu.is_empty() {
        "/dev/dri/renderD128"
    } else {
        gpu
    };
    let profile = if c.pix == "p010" {
        " -profile:v main10"
    } else {
        ""
    };
    let hw = format!(
        "{common}-vaapi_device {node} -filter_complex \"{graph},format={},hwupload[v]\"{audio}-map '[v]' -map 0:a -c:v {}_vaapi{profile} \
         -b:v {}M -g {fps} '{tmp}'",
        c.pix, c.codec, c.mbit
    );
    if !run_quiet(&hw).is_empty() {
        // software fallback
        let enc = match c.codec {
            "h264" => "libx264 -preset ultrafast",
            "hevc" => "libx265 -preset ultrafast -x265-params log-level=error",
            _ => "libsvtav1 -preset 12",
        };
        let pix = if c.pix == "p010" {
            "yuv420p10le"
        } else {
            "yuv420p"
        };
        let sw = format!(
            "{common}-filter_complex \"{graph},format={pix}[v]\"{audio}-map '[v]' -map 0:a -c:v {enc} -b:v {}M -g {fps} '{tmp}'",
            c.mbit
        );
        let err = run_quiet(&sw);
        if !err.is_empty() {
            eprintln!("  {}: cannot generate: {err}", c.name);
            return false;
        }
    }
    std::fs::rename(&tmp, path).is_ok()
}

struct Target {
    gl: Rc<glow::Context>,
    fb: glow::Framebuffer,
    rb: glow::Renderbuffer,
}

impl Target {
    fn new(gl: &Rc<glow::Context>, w: i32, h: i32) -> Self {
        // SAFETY (all GL in this file): plain GL calls on the headless context, current on this thread.
        unsafe {
            let fb = gl.create_framebuffer().unwrap();
            gl.bind_framebuffer(glow::FRAMEBUFFER, Some(fb));
            let rb = gl.create_renderbuffer().unwrap();
            gl.bind_renderbuffer(glow::RENDERBUFFER, Some(rb));
            gl.renderbuffer_storage(glow::RENDERBUFFER, glow::RGBA8, w, h);
            gl.framebuffer_renderbuffer(
                glow::FRAMEBUFFER,
                glow::COLOR_ATTACHMENT0,
                glow::RENDERBUFFER,
                Some(rb),
            );
            Self {
                gl: gl.clone(),
                fb,
                rb,
            }
        }
    }
}

impl Drop for Target {
    fn drop(&mut self) {
        unsafe {
            self.gl.delete_framebuffer(self.fb);
            self.gl.delete_renderbuffer(self.rb);
        }
    }
}

fn read_code(
    gl: &glow::Context,
    texture: glow::Texture,
    read_fb: glow::Framebuffer,
) -> Option<i32> {
    let mut px = [0u8; (BITS * BLOCK * 4) as usize];
    unsafe {
        gl.bind_framebuffer(glow::FRAMEBUFFER, Some(read_fb));
        gl.framebuffer_texture_2d(
            glow::FRAMEBUFFER,
            glow::COLOR_ATTACHMENT0,
            glow::TEXTURE_2D,
            Some(texture),
            0,
        );
        // row 0 = top of the picture
        gl.read_pixels(
            0,
            BLOCK / 2,
            BITS * BLOCK,
            1,
            glow::RGBA,
            glow::UNSIGNED_BYTE,
            PixelPackData::Slice(Some(&mut px)),
        );
    }
    let mut code = 0;
    for b in 0..BITS {
        let v = px[((b * BLOCK + BLOCK / 2) * 4) as usize];
        if v > 96 && v < 160 {
            return None; // blurred: not a clean frame
        }
        if v >= 160 {
            code |= 1 << b;
        }
    }
    Some(code)
}

fn stat(mut v: Vec<f64>) -> Value {
    let n = v.len();
    if v.is_empty() {
        return json!({"mean": 0.0, "p50": 0.0, "p99": 0.0, "max": 0.0, "n": 0});
    }
    v.sort_by(f64::total_cmp);
    let mean = v.iter().sum::<f64>() / n as f64;
    let p99 = v[(n - 1).min((n as f64 * 0.99) as usize)];
    json!({"mean": mean, "p50": v[n / 2], "p99": p99, "max": v[n - 1], "n": n})
}

fn player_report(p: &Player) -> Value {
    serde_json::from_str(&p.report()).unwrap_or_else(|_| json!({}))
}

struct Options {
    gpu: String,
    dir: String,
    ffmpeg: String,
    seconds: f64,
    hz: i32,
    restarts: i32,
    cases: Vec<String>,
    json: bool,
}

fn open(path: &str) -> Option<OwnedFd> {
    File::open(path).ok().map(Into::into)
}

#[derive(Default, Clone)]
struct Tick {
    at: u64,
    update_ms: f64,
    gpu_ms: f64,
    busy_ms: f64,
    fresh: bool,
    code: Option<i32>,
}

fn bench_clip(
    o: &Options,
    gl: &Rc<glow::Context>,
    timer: Option<&Timer>,
    c: &Clip,
    path: &str,
    sphere: &Sphere,
) -> Value {
    let mut out = json!({"clip": c.name});
    let period_ns = 1e9 / o.hz as f64;
    let Some(fd) = open(path) else {
        out["error"] = "cannot open clip".into();
        return out;
    };
    let t_open = now_ns();
    let player = Player::open(gl.clone(), fd, OPTIONS);
    let open_ns = now_ns() - t_open;
    let mut player = match player {
        Ok(p) => p,
        Err(e) => {
            out["error"] = e.into();
            return out;
        }
    };
    let target = Target::new(gl, 1920, 1200);
    let read_fb = unsafe { gl.create_framebuffer().unwrap() };
    let mut queries = [0u32; 2];
    if let Some(t) = timer {
        unsafe { (t.gen_queries)(2, queries.as_mut_ptr()) };
    }
    let format = Format::default();
    let [hw, hh] = half_fov_tan(46.0, 1920, 1200);

    let mut ticks: Vec<Tick> = Vec::with_capacity(((o.seconds + 30.0) * o.hz as f64) as usize);
    let (mut first_frame_ns, mut measure_from, mut measure_to) = (0u64, 0u64, 0u64);
    let (mut last_frames, mut max_update_before_first) = (0u64, 0u64);
    let mut deadline = now_ns();
    let give_up = deadline + 30_000_000_000;
    let mut late_wakeups = 0;
    loop {
        let t0 = now_ns();
        if let Some(t) = timer {
            unsafe { (t.counter)(queries[0], GL_TIMESTAMP_EXT) };
        }
        player.update();
        if let Some(t) = timer {
            unsafe { (t.counter)(queries[1], GL_TIMESTAMP_EXT) };
        }
        let t1 = now_ns();
        unsafe {
            gl.bind_framebuffer(glow::FRAMEBUFFER, Some(target.fb));
            gl.viewport(0, 0, 1920, 1200);
            gl.clear_color(0.0, 0.0, 0.0, 1.0);
            gl.clear(glow::COLOR_BUFFER_BIT);
        }
        if let Some(tex) = player.texture() {
            sphere.draw(tex, player.flip_y(), &format, 0, &IDENTITY, hw, hh, false);
        }
        unsafe { gl.finish() }; // the GPU work of this display frame is done (a swap would hand it to the compositor)
        let t2 = now_ns();
        player.report_swap();
        let mut tick = Tick {
            at: t0,
            update_ms: ms(t1 - t0),
            gpu_ms: -1.0,
            busy_ms: ms(t2 - t0),
            ..Tick::default()
        };
        let r = player_report(&player);
        let frames = r["frames"].as_u64().unwrap_or(0);
        tick.fresh = frames != last_frames;
        last_frames = frames;
        if let (true, Some(t)) = (tick.fresh, timer) {
            let (mut a, mut b) = (0u64, 0u64);
            unsafe {
                (t.get)(queries[0], GL_QUERY_RESULT_EXT, &mut a);
                (t.get)(queries[1], GL_QUERY_RESULT_EXT, &mut b);
            }
            tick.gpu_ms = if b > a { ms(b - a) } else { 0.0 };
        }
        if let (true, Some(tex)) = (tick.fresh, player.texture()) {
            tick.code = read_code(gl, tex, read_fb);
        }
        if first_frame_ns == 0 {
            max_update_before_first = max_update_before_first.max(t1 - t0);
            if player.texture().is_some() {
                first_frame_ns = t0 - t_open;
                measure_from = t0 + 1_000_000_000; // skip the start-up second
                measure_to = measure_from + (o.seconds * 1e9) as u64;
            } else if player.state() == State::Error || t0 > give_up {
                break;
            }
        }
        if measure_from != 0 && t0 >= measure_from && t0 < measure_to {
            ticks.push(tick);
        }
        if measure_to != 0 && t0 >= measure_to {
            break;
        }
        deadline += period_ns as u64;
        let after = now_ns();
        if after > deadline {
            // missed: the next vsync is the next multiple of the period
            late_wakeups += 1;
            while deadline < after {
                deadline += period_ns as u64;
            }
        }
        let ts = libc::timespec {
            tv_sec: (deadline / 1_000_000_000) as _,
            tv_nsec: (deadline % 1_000_000_000) as _,
        };
        // SAFETY: ts is valid; a null remainder is allowed with TIMER_ABSTIME.
        unsafe {
            libc::clock_nanosleep(
                libc::CLOCK_MONOTONIC,
                libc::TIMER_ABSTIME,
                &ts,
                std::ptr::null_mut(),
            )
        };
    }
    let final_report = player_report(&player);
    out["hwdec"] = final_report
        .get("hwdec")
        .cloned()
        .unwrap_or_else(|| "?".into());
    out["open_ms"] = ms(open_ns).into();
    out["first_frame_ms"] = if first_frame_ns != 0 {
        ms(first_frame_ns)
    } else {
        -1.0
    }
    .into();
    out["max_update_before_first_ms"] = ms(max_update_before_first).into();
    if first_frame_ns == 0 {
        out["error"] = if player.error().is_empty() {
            "no frame"
        } else {
            player.error()
        }
        .into();
    }
    // Per-tick statistics over the measurement window.
    let upd: Vec<f64> = ticks.iter().map(|t| t.update_ms).collect();
    let busy: Vec<f64> = ticks.iter().map(|t| t.busy_ms).collect();
    let gpu: Vec<f64> = ticks
        .iter()
        .filter(|t| t.gpu_ms >= 0.0)
        .map(|t| t.gpu_ms)
        .collect();
    let over_budget = ticks.iter().filter(|t| t.busy_ms * 1e6 > period_ns).count();
    // Frames shown: the barcode sequence (unwrapped mod 4096).
    let mut shown: Vec<(usize, i32)> = Vec::new();
    let mut unreadable = 0;
    for (i, t) in ticks.iter().enumerate() {
        if !t.fresh {
            continue;
        }
        let Some(mut n) = t.code else {
            unreadable += 1;
            continue;
        };
        if let Some(&(_, prev)) = shown.last() {
            while n < prev - 2048 {
                n += 4096;
            }
        }
        shown.push((i, n));
    }
    let (mut dropped, mut repeated, mut backwards, mut judder) = (0, 0, 0, 0);
    let mut intervals: BTreeMap<String, i32> = BTreeMap::new();
    let ideal = o.hz as f64 / c.fps as f64;
    for k in shown.windows(2) {
        let dn = k[1].1 - k[0].1;
        if dn > 1 {
            dropped += dn - 1;
        }
        if dn == 0 {
            repeated += 1;
        }
        if dn < 0 {
            backwards += 1;
        }
        if dn == 1 {
            let dt = (k[1].0 - k[0].0) as i32;
            *intervals.entry(dt.to_string()).or_default() += 1;
            if (dt as f64 - ideal).abs() > 0.5 {
                judder += 1;
            }
        }
    }
    let window_s = match (ticks.first(), ticks.last()) {
        (Some(a), Some(b)) => (b.at - a.at) as f64 / 1e9,
        _ => 0.0,
    };
    out["window_s"] = window_s.into();
    out["ticks"] = ticks.len().into();
    out["update_ms"] = stat(upd);
    out["gpu_new_frame_ms"] = stat(gpu);
    out["busy_ms"] = stat(busy);
    out["over_budget"] = over_budget.into();
    out["late_wakeups"] = late_wakeups.into();
    out["expected_frames"] = ((window_s * c.fps as f64).round() as i64).into();
    out["shown"] = shown.len().into();
    out["dropped"] = dropped.into();
    out["repeated"] = repeated.into();
    out["backwards"] = backwards.into();
    out["unreadable"] = unreadable.into();
    out["judder"] = judder.into();
    out["intervals"] = json!(intervals);
    for k in [
        "lead_ms",
        "drops",
        "early",
        "late",
        "held",
        "render_ms",
        "va_device",
        "interval_ms",
    ] {
        if let Some(v) = final_report.get(k) {
            out[k] = v.clone();
        }
    }
    // Close: the renderer-thread stall of tearing the player down (Mode::close / a web seek).
    let t_close = now_ns();
    drop(player);
    out["close_ms"] = ms(now_ns() - t_close).into();
    unsafe { gl.delete_framebuffer(read_fb) };
    drop(target);
    out
}

// A web video seek: the next stream into the playing player, as Mode::open does on every relay restart (reopen(),
// else a new player), then a full close as on Esc.
fn bench_restarts(o: &Options, gl: &Rc<glow::Context>, path: &str, sphere: &Sphere) -> Value {
    let mut out = json!({"restarts": o.restarts});
    let (mut close_ms, mut open_ms, mut first_ms, mut worst_tick_ms) =
        (vec![], vec![], vec![], vec![]);
    let mut reused = 0;
    let mut player: Option<Player> = None;
    let target = Target::new(gl, 1920, 1200);
    let [hw, hh] = half_fov_tan(46.0, 1920, 1200);
    let pause = std::time::Duration::from_micros(1_000_000 / o.hz as u64);
    for i in 0..=o.restarts {
        let Some(fd) = open(path) else {
            out["error"] = "cannot open clip".into();
            break;
        };
        let t0 = now_ns();
        let fd = match player.as_mut() {
            Some(p) => p.reopen(fd, OPTIONS).err(),
            None => Some(fd),
        };
        let reuse = fd.is_none();
        reused += reuse as i32;
        if !reuse {
            player = None;
        }
        let t1 = now_ns();
        if let Some(fd) = fd {
            match Player::open(gl.clone(), fd, OPTIONS) {
                Ok(p) => player = Some(p),
                Err(e) => {
                    out["error"] = e.into();
                    break;
                }
            }
        }
        let t2 = now_ns();
        let p = player.as_mut().unwrap();
        let mut worst: f64 = 0.0;
        let mut first = 0u64;
        while first == 0 && now_ns() - t2 < 20_000_000_000 {
            let a = now_ns();
            p.update();
            unsafe {
                gl.bind_framebuffer(glow::FRAMEBUFFER, Some(target.fb));
                gl.viewport(0, 0, 1920, 1200);
                gl.clear(glow::COLOR_BUFFER_BIT);
            }
            if let Some(tex) = p.texture() {
                sphere.draw(
                    tex,
                    p.flip_y(),
                    &Format::default(),
                    0,
                    &IDENTITY,
                    hw,
                    hh,
                    false,
                );
            }
            unsafe { gl.finish() };
            p.report_swap();
            worst = worst.max(ms(now_ns() - a));
            if p.texture().is_some() {
                first = now_ns() - t2;
            } else {
                std::thread::sleep(pause);
            }
        }
        if i > 0 {
            // the first open is not a restart
            close_ms.push(ms(t1 - t0));
            open_ms.push(ms(t2 - t1));
            first_ms.push(ms(first));
            worst_tick_ms.push(worst);
        }
        // Play a little, as between seek key presses.
        for _ in 0..o.hz / 2 {
            p.update();
            unsafe { gl.finish() };
            p.report_swap();
            std::thread::sleep(pause);
        }
    }
    let t_close = now_ns();
    drop(player);
    out["final_close_ms"] = ms(now_ns() - t_close).into();
    out["reused"] = reused.into();
    out["close_ms"] = stat(close_ms);
    out["open_ms"] = stat(open_ms);
    out["first_frame_ms"] = stat(first_ms);
    out["worst_tick_until_first_ms"] = stat(worst_tick_ms);
    out
}

fn print_human(r: &Value) {
    let f = |v: &Value| v.as_f64().unwrap_or(0.0);
    if r.get("restarts").is_some() {
        println!(
            "restarts x{} (reused {}): close {:.1} ms (max {:.1}), open {:.1} ms (max {:.1}), first frame {:.0} ms (max {:.0}), \
             worst tick {:.1} ms (max {:.1}), final close {:.1} ms",
            r["restarts"],
            r.get("reused").cloned().unwrap_or(json!(0)),
            f(&r["close_ms"]["mean"]),
            f(&r["close_ms"]["max"]),
            f(&r["open_ms"]["mean"]),
            f(&r["open_ms"]["max"]),
            f(&r["first_frame_ms"]["mean"]),
            f(&r["first_frame_ms"]["max"]),
            f(&r["worst_tick_until_first_ms"]["mean"]),
            f(&r["worst_tick_until_first_ms"]["max"]),
            r.get("final_close_ms").map_or(-1.0, f),
        );
        return;
    }
    let s = |v: &Value| v.as_str().unwrap_or("").to_owned();
    if let Some(e) = r.get("error") {
        println!("{:<12} error: {}", s(&r["clip"]), s(e));
        return;
    }
    print!(
        "{:<12} {:<10} first {:5.0} ms | update {:.2}/{:.2}/{:.2} ms | gpu/new frame {:.2} (p99 {:.2}) ms | busy p99 {:.2} max {:.2} ms, \
         over {}/{} | shown {}/{} dropped {} repeated {} judder {} | close {:.1} ms",
        s(&r["clip"]),
        s(&r["hwdec"]),
        f(&r["first_frame_ms"]),
        f(&r["update_ms"]["mean"]),
        f(&r["update_ms"]["p99"]),
        f(&r["update_ms"]["max"]),
        f(&r["gpu_new_frame_ms"]["mean"]),
        f(&r["gpu_new_frame_ms"]["p99"]),
        f(&r["busy_ms"]["p99"]),
        f(&r["busy_ms"]["max"]),
        r["over_budget"],
        r["ticks"],
        r["shown"],
        r["expected_frames"],
        r["dropped"],
        r["repeated"],
        r["judder"],
        f(&r["close_ms"]),
    );
    if let Some(lead) = r.get("lead_ms") {
        let g = |k: &str| r.get(k).cloned().unwrap_or(json!(0));
        print!(
            " | lead min/mean/max {lead} ms, early {} late {} held {}, mpv drops {}, render cpu {} ms",
            g("early"),
            g("late"),
            g("held"),
            g("drops"),
            g("render_ms")
        );
    }
    println!();
}

fn usage(code: i32) -> ! {
    println!(
        "usage: video_bench [--gpu /dev/dri/renderDN] [--seconds S] [--hz HZ] [--dir DIR] [--ffmpeg PATH] [--case NAME]... \
         [--restarts N] [--json]\ncases: {}",
        CLIPS.iter().map(|c| c.name).collect::<Vec<_>>().join(" ")
    );
    std::process::exit(code)
}

fn main() {
    let mut o = Options {
        gpu: String::new(),
        dir: "video-bench-clips".into(),
        ffmpeg: "/usr/bin/ffmpeg".into(),
        seconds: 6.0,
        hz: 120,
        restarts: 0,
        cases: vec![],
        json: false,
    };
    let mut args = std::env::args().skip(1);
    while let Some(a) = args.next() {
        let mut next = || args.next().unwrap_or_default();
        match a.as_str() {
            "--gpu" => o.gpu = next(),
            "--seconds" => o.seconds = next().parse().unwrap_or(0.0),
            "--hz" => o.hz = next().parse().unwrap_or(0),
            "--dir" => o.dir = next(),
            "--ffmpeg" => o.ffmpeg = next(),
            "--case" => o.cases.push(next()),
            "--restarts" => o.restarts = next().parse().unwrap_or(0),
            "--json" => o.json = true,
            "--help" => usage(0),
            _ => usage(2),
        }
    }
    if !(10..=500).contains(&o.hz) || o.seconds <= 0.0 {
        eprintln!("bad --hz or --seconds");
        std::process::exit(2);
    }
    let headless = match Headless::new((!o.gpu.is_empty()).then_some(o.gpu.as_str())) {
        Ok(h) => h,
        Err(e) => {
            eprintln!("EGL: {e}");
            std::process::exit(77);
        }
    };
    let gl = headless.gl.clone();
    let timer = Timer::load(&gl);
    unsafe {
        println!(
            "GL: {} / {}; {} Hz, {:.1} s per clip",
            gl.get_parameter_string(glow::RENDERER),
            gl.get_parameter_string(glow::VERSION),
            o.hz,
            o.seconds
        );
    }
    let _ = std::fs::create_dir_all(&o.dir);
    let sphere = match Sphere::new(gl.clone()) {
        Ok(s) => s,
        Err(e) => {
            eprintln!("sphere: {e}");
            std::process::exit(1);
        }
    };
    let emit = |r: &Value| {
        if o.json {
            println!("{r}");
        } else {
            print_human(r);
        }
    };
    for c in &CLIPS {
        if !o.cases.is_empty() && !o.cases.iter().any(|n| n == c.name) {
            continue;
        }
        let path = format!("{}/{}.mp4", o.dir, c.name);
        if !Path::new(&o.ffmpeg).exists() || !generate(&o.ffmpeg, &o.gpu, c, o.seconds + 6.0, &path)
        {
            continue;
        }
        emit(&bench_clip(&o, &gl, timer.as_ref(), c, &path, &sphere));
    }
    if o.restarts > 0 {
        let c = &CLIPS[0];
        let path = format!("{}/{}.mp4", o.dir, c.name);
        if generate(&o.ffmpeg, &o.gpu, c, o.seconds + 6.0, &path) {
            emit(&bench_restarts(&o, &gl, &path, &sphere));
        }
    }
    drop(sphere);
    drop(gl);
    drop(headless);
}
