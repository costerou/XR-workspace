//! The renderer trial flags (`supervisor._renderer_flags`, docs/technical.md "Renderer trial flags") on both sides.
//!
//! The file reaches only a renderer with a pose feed, a head-tracked run, which this compositor's runs are not: a
//! tracked start waits for a calibrated driver of the real machine's glasses. So the runs here show the other half,
//! that without a pose feed neither supervisor reads the file, good or bad: the renderer is launched with the same
//! arguments, none of them a trial flag, and the run ends as a plain Stop. What the file says when it is read is held
//! to the Python reader directly: one corpus of files through `_renderer_flags` and `managed::real::renderer_flags_at`,
//! which `RealHost::launch` hands to the renderer after `--pose-feed xrld` (`tests/supervisor_faults.rs` checks that
//! place).

use std::fs;
use std::os::unix::fs::PermissionsExt;
use std::process::{Command, Stdio};

use serde_json::{Value, json};
use xr_workspace::managed::real::renderer_flags_at;

use super::{Start, parity_from, repo};

const FLAGS: &str = "config/viture-workspace/renderer-flags";

fn stopped_with(file: &str) -> super::Observed {
    let start = Start {
        files: vec![(FLAGS, file.to_owned())],
        ..Start::default()
    };
    let (run, _) = parity_from("stop", &start);
    assert_eq!(run.result["stop_reason"], "interrupted", "{:?}", run.result);
    assert_eq!(run.result["complete"], true);
    assert!(!run.renderers.is_empty());
    for argv in &run.renderers {
        assert!(!argv.contains("--pose-feed"), "{argv}");
        assert!(!argv.contains("--late-latch-guard-ms"), "{argv}");
    }
    assert_eq!(
        run.files,
        vec![Some(file.to_owned())],
        "the file is left as it was"
    );
    run
}

#[test]
fn a_flags_file_reaches_no_renderer_without_head_tracking() {
    stopped_with("--late-latch-guard-ms 2\n");
}

#[test]
fn a_bad_flags_file_refuses_no_run_without_head_tracking() {
    stopped_with("--late-latch-guard-ms 9 --bogus\n");
}

/// What each file gives: `{"flags": [...]}` or `{"error": str(exc)}`.
fn python(paths: &[String]) -> Vec<Value> {
    let script = "import json, sys\n\
                  sys.path.insert(0, sys.argv[1])\n\
                  from pathlib import Path\n\
                  from tools.native_spike import supervisor\n\
                  out = []\n\
                  for p in sys.argv[2:]:\n\
                  \x20   supervisor.RENDERER_FLAGS = Path(p)\n\
                  \x20   try: out.append({'flags': supervisor._renderer_flags()})\n\
                  \x20   except (OSError, ValueError) as exc: out.append({'error': str(exc)})\n\
                  print(json.dumps(out))\n";
    let out = Command::new("/usr/bin/python3")
        .arg("-I")
        .arg("-c")
        .arg(script)
        .arg(repo())
        .args(paths)
        .env("TMPDIR", "/tmp")
        .stdin(Stdio::null())
        .output()
        .unwrap();
    assert!(
        out.status.success(),
        "{}",
        String::from_utf8_lossy(&out.stderr)
    );
    serde_json::from_slice(&out.stdout).unwrap()
}

fn rust(path: &str) -> Value {
    match renderer_flags_at(path.as_ref()) {
        Ok(flags) => json!({"flags": flags}),
        Err(e) => json!({"error": e.to_string()}),
    }
}

#[test]
fn the_reader_reads_every_file_as_the_python() {
    let dir = std::env::temp_dir().join(format!("xr-renderer-flags-{}", std::process::id()));
    let _ = fs::remove_dir_all(&dir);
    fs::create_dir_all(&dir).unwrap();
    let files: Vec<&[u8]> = vec![
        b"",
        b" \n\t \n",
        b"--late-latch-guard-ms 2\n",
        b"  --late-latch-guard-ms\n\n0  ",
        b"--late-latch-guard-ms 8",
        b"--late-latch-guard-ms 8.0",
        b"--late-latch-guard-ms 8.000",
        b"--late-latch-guard-ms 08",
        b"--late-latch-guard-ms 00.001",
        b"--late-latch-guard-ms 7.999",
        // Python's str.split() also splits on U+001C..U+001F and U+00A0; U+200B is no whitespace to either.
        b"--late-latch-guard-ms\x1c3",
        b"--late-latch-guard-ms\x1f3",
        "--late-latch-guard-ms\u{a0}3".as_bytes(),
        "--late-latch-guard-ms\u{3000}3".as_bytes(),
        "--late-latch-guard-ms\u{200b}3".as_bytes(),
        // Refused.
        b"--late-latch-guard-ms 8.001",
        b"--late-latch-guard-ms 9",
        b"--late-latch-guard-ms 10",
        b"--late-latch-guard-ms 99.999",
        b"--late-latch-guard-ms 008",
        b"--late-latch-guard-ms 1.2345",
        b"--late-latch-guard-ms .5",
        b"--late-latch-guard-ms 5.",
        b"--late-latch-guard-ms -1",
        b"--late-latch-guard-ms +1",
        b"--late-latch-guard-ms 1e0",
        b"--late-latch-guard-ms nan",
        b"--late-latch-guard-ms inf",
        b"--late-latch-guard-ms 1_0",
        b"--late-latch-guard-ms 1..2",
        b"--late-latch-guard-ms",
        b"--late-latch-guard-ms=2",
        b"--late-latch-guard-ms 1 --late-latch-guard-ms 2",
        b"--late-latch-guard-ms 3 extra",
        b"--bogus 1",
        b"2",
        b"\xff",
        b"--late-latch-guard-ms 3\xc3",
        b"--late-latch-guard-ms \xe2\x82(",
        b"--late-latch-guard-ms \xf0\x9f\x98(",
        b"--late-latch-guard-ms \xe2(",
        b"\xed\xa0\x80",
        b"\xc0\xaf",
    ];
    let mut paths: Vec<String> = Vec::new();
    for (i, body) in files.iter().enumerate() {
        let path = dir.join(format!("flags-{i}"));
        fs::write(&path, body).unwrap();
        paths.push(path.display().to_string());
    }
    paths.push(dir.join("missing").display().to_string());
    paths.push(dir.join("missing-dir/renderer-flags").display().to_string());
    fs::create_dir(dir.join("a-directory")).unwrap();
    paths.push(dir.join("a-directory").display().to_string());
    fs::write(dir.join("not-a-dir"), "").unwrap();
    paths.push(dir.join("not-a-dir/renderer-flags").display().to_string());
    let locked = dir.join("locked");
    fs::write(&locked, "--late-latch-guard-ms 1").unwrap();
    fs::set_permissions(&locked, fs::Permissions::from_mode(0o000)).unwrap();
    paths.push(locked.display().to_string());

    let py = python(&paths);
    let rs: Vec<Value> = paths.iter().map(|p| rust(p)).collect();
    let _ = fs::remove_dir_all(&dir);
    assert_eq!(py.len(), rs.len());
    for (i, (p, r)) in py.iter().zip(&rs).enumerate() {
        assert_eq!(
            p,
            r,
            "file {i}: {:?}",
            files.get(i).map(|b| String::from_utf8_lossy(b))
        );
    }
    assert_eq!(rs[2], json!({"flags": ["--late-latch-guard-ms", "2"]}));
    assert!(rs.iter().any(|r| r.get("error").is_some()));
}

/// The one difference, by choice: Python's `\d` takes any script's decimal digits (`float` reads them), so it hands
/// `٣` to the renderer, which cannot parse it; the Rust reader refuses it at the supervisor, as an ASCII value out of
/// range is refused.
#[test]
fn other_scripts_digits_are_refused_here_where_the_python_passes_them_on() {
    let dir = std::env::temp_dir().join(format!("xr-renderer-flags-digits-{}", std::process::id()));
    fs::create_dir_all(&dir).unwrap();
    let path = dir.join("renderer-flags");
    fs::write(&path, "--late-latch-guard-ms \u{663}").unwrap();
    let path = path.display().to_string();
    let py = python(std::slice::from_ref(&path));
    let rs = rust(&path);
    let _ = fs::remove_dir_all(&dir);
    assert_eq!(
        py[0],
        json!({"flags": ["--late-latch-guard-ms", "\u{663}"]})
    );
    assert_eq!(
        rs,
        json!({"error": format!("{path}: --late-latch-guard-ms needs a decimal 0..8")})
    );
}
