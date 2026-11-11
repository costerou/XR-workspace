//! Port of the bench mode of spike/tests/gl_bench.cpp (`gl_bench --bench`) for the Rust GlCompositor: the same
//! scenarios (tests/support/gl_scenarios.rs: every one drawn without a background or video), the same surfaceless EGL
//! offscreen compositor, 30 warm-up frames then N timed frames each, and the same table, column for column, so the C++
//! and Rust numbers can be read side by side.
//!
//!     cargo bench -p xr-renderer --bench gl_bench -- [--frames N] [--size WxH]
//!
//! cpu: wall time of the draw call (draw_and_swap, uploads excluded); gl = cpu minus the time inside its glFlush
//! (command submission); thr = thread CPU time of the call; gpu = GPU time of the frame (GL_EXT_disjoint_timer_query
//! GL_TIME_ELAPSED_EXT); frm = call to glFinish; >8.3 = frames over one 120 Hz refresh. The counts are the last frame's
//! (`xr_renderer::gl::Counts`, the categories of spike/tests/gl_call_counts.hpp). Not ported: the C++ `report:` line
//! and the card and font CPU timings (the font is not in Rust yet). Exit 77 (skipped) without EGL/GLES, 2 on bad
//! arguments.

#[path = "../tests/support/gl_scenarios.rs"]
mod gl_scenarios;

use std::ffi::c_void;
use std::time::Instant;

use gl_scenarios::*;
use glow::HasContext as _;
use xr_renderer::egl;

const TIME_ELAPSED_EXT: u32 = 0x88BF;
const QUERY_RESULT_EXT: u32 = 0x8866;

type GenQueries = unsafe extern "system" fn(i32, *mut u32);
type Query = unsafe extern "system" fn(u32, u32);
type EndQuery = unsafe extern "system" fn(u32);
type QueryU64 = unsafe extern "system" fn(u32, u32, *mut u64);

/// GL_EXT_disjoint_timer_query's elapsed-time query, if the context has it.
struct Timer {
    begin: Query,
    end: EndQuery,
    result: QueryU64,
    query: u32,
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
        let (g, b, e, r) = (
            p(c"glGenQueriesEXT")?,
            p(c"glBeginQueryEXT")?,
            p(c"glEndQueryEXT")?,
            p(c"glGetQueryObjectui64vEXT")?,
        );
        // SAFETY: each pointer is the named GL entry point, whose C signature the type spells; the context that
        // gave them is current on this thread, and `query` is a valid out pointer.
        unsafe {
            let gen_queries = std::mem::transmute::<*mut c_void, GenQueries>(g);
            let mut query = 0;
            gen_queries(1, &mut query);
            Some(Self {
                begin: std::mem::transmute::<*mut c_void, Query>(b),
                end: std::mem::transmute::<*mut c_void, EndQuery>(e),
                result: std::mem::transmute::<*mut c_void, QueryU64>(r),
                query,
            })
        }
    }
    fn begin(&self) {
        // SAFETY: the query made in load, on the context still current on this thread.
        unsafe { (self.begin)(TIME_ELAPSED_EXT, self.query) }
    }
    fn end(&self) {
        // SAFETY: as begin.
        unsafe { (self.end)(TIME_ELAPSED_EXT) }
    }
    fn ns(&self) -> u64 {
        let mut ns = 0;
        // SAFETY: as begin; `ns` is a valid out pointer.
        unsafe { (self.result)(self.query, QUERY_RESULT_EXT, &mut ns) };
        ns
    }
}

fn thread_us() -> f64 {
    let mut t = libc::timespec {
        tv_sec: 0,
        tv_nsec: 0,
    };
    // SAFETY: t is a valid out pointer.
    unsafe { libc::clock_gettime(libc::CLOCK_THREAD_CPUTIME_ID, &mut t) };
    t.tv_sec as f64 * 1e6 + t.tv_nsec as f64 / 1e3
}

#[derive(Default)]
struct Pct {
    mean: f64,
    p50: f64,
    p99: f64,
    max: f64,
}

/// gl_bench.cpp pct: the mean, and the sorted values at n/2, floor(n*0.99) and the end.
fn pct(mut v: Vec<f64>) -> Pct {
    if v.is_empty() {
        return Pct::default();
    }
    v.sort_by(f64::total_cmp);
    let n = v.len();
    Pct {
        mean: v.iter().sum::<f64>() / n as f64,
        p50: v[n / 2],
        p99: v[(n - 1).min((n as f64 * 0.99) as usize)],
        max: v[n - 1],
    }
}

fn usage() -> ! {
    eprintln!("usage: gl_bench [--frames N] [--size WxH]");
    std::process::exit(2);
}

fn skip(why: &str) -> ! {
    println!("SKIP: {why}");
    std::process::exit(77);
}

fn main() {
    let (mut w, mut h, mut frames) = (DEFAULT_W, DEFAULT_H, 1000);
    let mut args = std::env::args().skip(1);
    while let Some(a) = args.next() {
        match a.as_str() {
            "--bench" => {} // what `cargo bench` passes a harness-less bench
            "--frames" => {
                frames = args
                    .next()
                    .and_then(|v| v.parse().ok())
                    .filter(|&n| n > 0)
                    .unwrap_or_else(|| usage())
            }
            "--size" => {
                let v = args.next().unwrap_or_else(|| usage());
                let (a, b) = v.split_once('x').unwrap_or_else(|| usage());
                match (a.parse(), b.parse()) {
                    (Ok(a), Ok(b)) if a > 0 && b > 0 => (w, h) = (a, b),
                    _ => usage(),
                }
            }
            _ => usage(),
        }
    }
    let r = load_resources();
    let list = scenarios();
    {
        // First frames on a fresh compositor, scenarios in order: one-time costs (lazy creation, first use of a
        // program or texture) land on the first scenario that needs them. CPU call + glFinish.
        let t0 = Instant::now();
        let mut fresh = make(w, h, &r).unwrap_or_else(|why| skip(&why));
        // SAFETY: the compositor's context, current on this thread.
        unsafe { fresh.gl().raw().finish() };
        print!(
            "first frames (fresh compositor; create + 3 uploads {:.0} us):",
            t0.elapsed().as_secs_f64() * 1e6
        );
        let mut worst = 0f64;
        for s in &list {
            let t = Instant::now();
            draw_frame(&mut fresh, &r, s, 0, w, h, s.upload_each_frame);
            // SAFETY: as above.
            unsafe { fresh.gl().raw().finish() };
            let us = t.elapsed().as_secs_f64() * 1e6;
            worst = worst.max(us);
            print!(" {} {us:.0}", s.name);
        }
        println!(" | worst {worst:.0} us");
    }
    let mut gl = make(w, h, &r).unwrap_or_else(|why| skip(&why));
    let timer = Timer::load(gl.gl().raw());
    // SAFETY: the compositor's context, current on this thread.
    let renderer = unsafe { gl.gl().raw().get_parameter_string(glow::RENDERER) };
    println!(
        "renderer: {renderer} | {w}x{h}, {frames} frames per scenario, GPU timer {}",
        if timer.is_some() {
            "on"
        } else {
            "off (frame time only)"
        }
    );
    println!(
        "{:<22} {:>7} {:>7} {:>7} {:>7} {:>7} {:>7} | {:>7} {:>7} | {:>7} {:>7} {:>4} | {:>5} {:>5} {:>5} {:>5} {:>5} {:>5} {:>5} {:>5} {:>7}",
        "scenario",
        "cpu_avg",
        "cpu_p50",
        "cpu_p99",
        "gl_p50",
        "thr_p50",
        "fls_p50",
        "gpu_avg",
        "gpu_p99",
        "frm_avg",
        "frm_p99",
        ">8.3",
        "calls",
        "draws",
        "progs",
        "unifs",
        "texs",
        "attrs",
        "bufs",
        "tgls",
        "upKiB"
    );
    for s in &list {
        let (mut cpu, mut gl_only, mut thr, mut flush, mut gpu, mut frame, mut up) =
            (vec![], vec![], vec![], vec![], vec![], vec![], vec![]);
        let mut per = xr_renderer::gl::Counts::default();
        for f in -30..frames {
            let fi = if f < 0 { f + 30 } else { f };
            let t0 = Instant::now();
            gl.gl().reset_counts();
            if let Some(t) = &timer {
                t.begin();
            }
            // The uploads, timed apart (a frame per upload set), or the draw call alone.
            let mut upload_us = 0.0;
            if s.upload_each_frame {
                upload_us = draw_frame_timed(&mut gl, &r, s, fi, w, h, true).upload_us;
            }
            let uploads = gl.gl().counts();
            let c0 = thread_us();
            let c = if s.upload_each_frame {
                0.0
            } else {
                let fr = if s.panel_drag { fi % 81 } else { fi };
                draw_frame_timed(&mut gl, &r, s, fr, w, h, false).draw_us
            };
            let thread = thread_us() - c0;
            if let Some(t) = &timer {
                t.end();
            }
            let counts = gl.gl().counts();
            let flush_ns = gl.gl().flush_ns();
            // SAFETY: the compositor's context, current on this thread.
            unsafe { gl.gl().raw().finish() };
            let t1 = t0.elapsed().as_secs_f64() * 1e6;
            let ns = timer.as_ref().map_or(0, Timer::ns);
            if f < 0 {
                continue;
            }
            if s.upload_each_frame {
                up.push(upload_us);
                per = uploads;
                continue;
            }
            let fl = flush_ns as f64 / 1e3;
            cpu.push(c);
            gl_only.push(c - fl);
            thr.push(thread);
            flush.push(fl);
            frame.push(t1);
            gpu.push(ns as f64 / 1e3);
            per = counts; // steady state: the last frame's counts (every frame of a scenario issues the same calls)
        }
        if s.upload_each_frame {
            let pu = pct(up);
            println!(
                "{:<22} 3 SHM uploads/frame: mean {:.1} us, p50 {:.1}, p99 {:.1}, max {:.1} ({} KiB)",
                s.name,
                pu.mean,
                pu.p50,
                pu.p99,
                pu.max,
                per.upload_bytes >> 10
            );
            continue;
        }
        let over = frame.iter().filter(|&&v| v > 1e6 / 120.0).count();
        let (pc, pl, pt, ps, pg, pf) = (
            pct(cpu),
            pct(gl_only),
            pct(thr),
            pct(flush),
            pct(gpu),
            pct(frame),
        );
        println!(
            "{:<22} {:>7.1} {:>7.1} {:>7.1} {:>7.1} {:>7.1} {:>7.1} | {:>7.1} {:>7.1} | {:>7.1} {:>7.1} {:>4} | {:>5} {:>5} {:>5} {:>5} {:>5} {:>5} {:>5} {:>5} {:>7}",
            s.name,
            pc.mean,
            pc.p50,
            pc.p99,
            pl.p50,
            pt.p50,
            ps.p50,
            pg.mean,
            pg.p99,
            pf.mean,
            pf.p99,
            over,
            per.calls(),
            per.draws,
            per.programs,
            per.uniforms,
            per.textures,
            per.attribs,
            per.buffers,
            per.toggles,
            per.upload_bytes >> 10
        );
    }
}
