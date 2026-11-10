//! The Rust GlCompositor against the C++ one, frame for frame: XR_GL_CPP_BENCH names a built spike gl_bench. Its
//! `--dump` gives the CPU-rendered cards both draw with, `--hashes` the FNV-1a of every scenario's six frames, and
//! `--bench --frames 3` the GL calls of each scenario's steady-state frame. Every scenario the Rust compositor draws
//! (all but the video and background ones) must hash the same (pixel-identical frames) and issue at most as many GL
//! calls. Skipped (exit 0) without XR_GL_CPP_BENCH; exit 77 without EGL/GLES.

#[path = "support/gl_scenarios.rs"]
mod gl_scenarios;

use std::collections::HashMap;
use std::path::PathBuf;
use std::process::Command;

use gl_scenarios::*;
use xr_renderer::gl::Counts;

fn run(bench: &str, args: &[&str]) -> String {
    let out = Command::new(bench)
        .args(args)
        .output()
        .unwrap_or_else(|e| panic!("{bench}: {e}"));
    let text = String::from_utf8_lossy(&out.stdout).into_owned();
    if out.status.code() == Some(77) {
        println!("SKIP: C++ gl_bench: {}", text.trim());
        std::process::exit(77);
    }
    assert!(
        out.status.success(),
        "{bench} {args:?}: {}\n{}",
        out.status,
        String::from_utf8_lossy(&out.stderr)
    );
    text
}

fn main() {
    let Some(bench) = std::env::var_os("XR_GL_CPP_BENCH") else {
        // Opt-in, not a missing platform: a plain `cargo test` passes without it.
        println!("SKIP: XR_GL_CPP_BENCH (the spike's gl_bench) not set");
        return;
    };
    let bench = bench.to_string_lossy().into_owned();
    let cards = PathBuf::from(env!("CARGO_TARGET_TMPDIR")).join("gl_parity_cards");
    std::fs::create_dir_all(&cards).unwrap();
    run(&bench, &["--dump", cards.to_str().unwrap()]);
    let cpp_hashes: HashMap<String, String> = run(&bench, &["--hashes"])
        .lines()
        .filter_map(|l| l.strip_prefix("frame "))
        .filter_map(|l| {
            let mut it = l.split_whitespace();
            Some((it.next()?.to_owned(), it.next()?.to_owned()))
        })
        .collect();
    // calls draws progs unifs texs attrs bufs tgls upKiB: the last '|' column group of a scenario row.
    let cpp_counts: HashMap<String, Vec<u64>> = run(&bench, &["--bench", "--frames", "3"])
        .lines()
        .filter(|l| l.matches('|').count() == 3)
        .filter_map(|l| {
            let name = l.split_whitespace().next()?.to_owned();
            let cols: Vec<u64> = l
                .rsplit('|')
                .next()?
                .split_whitespace()
                .filter_map(|v| v.parse().ok())
                .collect();
            (cols.len() == 9).then_some((name, cols))
        })
        .collect();

    let (w, h) = (DEFAULT_W, DEFAULT_H);
    let r = load_resources_from(Some(&cards));
    let list = scenarios();
    let mut failures = 0;

    // gl_bench.cpp hashes(): one compositor, six frames of each scenario in order.
    let mut gl = match make(w, h, &r) {
        Ok(gl) => gl,
        Err(why) => {
            println!("SKIP: {why}");
            std::process::exit(77);
        }
    };
    let (renderer, version, _) = gl.strings();
    println!("renderer: {renderer} | {version}");
    for s in &list {
        let mut hash = FNV_OFFSET;
        for f in 0..6 {
            assert!(draw_frame(
                &mut gl,
                &r,
                s,
                f,
                w,
                h,
                s.upload_each_frame || f == 0
            ));
            hash = fnv(&gl.read_rgb(), hash);
        }
        let ours = format!("{hash:016x}");
        let theirs = cpp_hashes.get(s.name).map_or("missing", String::as_str);
        let same = ours == theirs;
        failures += usize::from(!same);
        println!(
            "frame {:<22} rust {ours} c++ {theirs} {}",
            s.name,
            if same { "same" } else { "DIFFERENT" }
        );
    }
    drop(gl);

    // gl_bench.cpp bench(): one compositor; per scenario 30 warm-up frames then 3, the last frame's counts.
    let mut gl = make(w, h, &r).expect("compositor");
    println!(
        "{:<22} {:>11} {:>11} {:>11} {:>11} {:>11} {:>11} {:>11} {:>11}",
        "scenario (rust/c++)", "calls", "draws", "progs", "unifs", "texs", "attrs", "bufs", "tgls"
    );
    for s in &list {
        let mut per = Counts::default();
        for f in -30..3 {
            let fi = if f < 0 { f + 30 } else { f };
            gl.gl().reset_counts();
            let frame = if s.panel_drag { fi % 81 } else { fi };
            assert!(draw_frame(&mut gl, &r, s, frame, w, h, s.upload_each_frame));
            if f >= 0 {
                per = gl.gl().counts();
            }
        }
        if s.upload_each_frame {
            // The C++ bench reports only upload times for it; the uploads are 3 full sub-images per frame.
            println!(
                "{:<22} {} calls, {} uploads ({} KiB) per frame; no C++ count to compare",
                s.name,
                per.calls(),
                per.uploads,
                per.upload_bytes >> 10
            );
            continue;
        }
        let ours = [
            per.calls(),
            per.draws,
            per.programs,
            per.uniforms,
            per.textures,
            per.attribs,
            per.buffers,
            per.toggles,
        ];
        let Some(theirs) = cpp_counts.get(s.name) else {
            println!("{:<22} missing from the C++ bench", s.name);
            failures += 1;
            continue;
        };
        let cols: Vec<String> = ours
            .iter()
            .zip(theirs)
            .map(|(a, b)| format!("{a}/{b}"))
            .collect();
        let fewer = ours[0] <= theirs[0];
        failures += usize::from(!fewer);
        println!(
            "{:<22} {:>11} {:>11} {:>11} {:>11} {:>11} {:>11} {:>11} {:>11} {}",
            s.name,
            cols[0],
            cols[1],
            cols[2],
            cols[3],
            cols[4],
            cols[5],
            cols[6],
            cols[7],
            if fewer { "ok" } else { "MORE CALLS" }
        );
    }
    if failures > 0 {
        eprintln!("{failures} failures");
        std::process::exit(1);
    }
    println!(
        "gl parity passed: {} scenarios pixel-identical to the C++ compositor, GL calls equal or fewer",
        list.len()
    );
}
