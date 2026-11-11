//! `--continuity-fd` end to end (tests/native_spike/test_continuity.py's real-renderer case, both ways): the Rust
//! renderer, launched by the C++ loopback `capture_e2e_bench`, restores a blob the C++ wrote (golden/continuity_end.bin)
//! and saves one; the C++ renderer then restores that one.
//!
//! Needs a CMake build of spike/: `XR_CPP_BUILD=<dir with capture_e2e_bench and xr-native-spike>`. Without it the test
//! says so and passes, as the Python test skips.

use std::os::fd::{AsFd, AsRawFd, OwnedFd};
use std::os::unix::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::Command;

use xr_renderer::continuity::ViewContinuity;

fn golden(name: &str) -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("golden").join(name)
}

/// A wrapper renderer that runs `binary` with the bench's arguments plus `--continuity-fd <fd>`. Written by a child
/// process, so no handle of ours is open on it when it is executed.
fn wrapper(dir: &Path, name: &str, binary: &Path, fd: i32) -> PathBuf {
    let path = dir.join(name);
    let script = format!("#!/bin/sh\nexec '{}' \"$@\" --continuity-fd {fd}\n", binary.display());
    let ok = Command::new("sh")
        .args(["-c", "printf '%s' \"$1\" > \"$2\" && chmod 700 \"$2\"", "sh", &script])
        .arg(&path)
        .status()
        .unwrap()
        .success();
    assert!(ok);
    path
}

/// One bench run (as the Python test: cpu mode, 0.15 s) with `renderer`; the renderer's stderr lines.
fn run(bench: &Path, renderer: &Path, fd: &OwnedFd) -> Vec<String> {
    let raw = fd.as_raw_fd();
    let mut cmd = Command::new(bench);
    cmd.args(["--renderer"]).arg(renderer).args(["--mode", "cpu", "--seconds", "0.15", "--warmup", "0.1"]);
    // SAFETY: only fcntl between fork and exec. The file is inherited by this child alone.
    unsafe {
        cmd.pre_exec(move || {
            let fd = rustix::fd::BorrowedFd::borrow_raw(raw);
            rustix::io::fcntl_setfd(fd, rustix::io::FdFlags::empty()).map_err(std::io::Error::from)
        });
    }
    let out = cmd.output().unwrap();
    assert!(out.status.success(), "bench: {}", String::from_utf8_lossy(&out.stderr));
    let report: serde_json::Value = serde_json::from_slice(&out.stdout).unwrap();
    report["renderer_stderr"]
        .as_array()
        .map(|a| a.iter().filter_map(|l| l.as_str().map(String::from)).collect())
        .unwrap_or_default()
}

fn monotonic_ms() -> u64 {
    let t = rustix::time::clock_gettime(rustix::time::ClockId::Monotonic);
    t.tv_sec as u64 * 1000 + t.tv_nsec as u64 / 1_000_000
}

fn contents(fd: &OwnedFd) -> Vec<u8> {
    let size = rustix::fs::fstat(fd).unwrap().st_size as usize;
    let mut bytes = vec![0u8; size];
    assert_eq!(rustix::io::pread(fd, &mut bytes, 0).unwrap(), size);
    bytes
}

#[test]
fn rust_renderer_restores_a_cpp_blob_and_saves_one_the_cpp_restores() {
    let Some(build) = std::env::var_os("XR_CPP_BUILD").map(PathBuf::from) else {
        eprintln!("skipped: set XR_CPP_BUILD to a spike/ build with capture_e2e_bench and xr-native-spike");
        return;
    };
    let (bench, cpp) = (build.join("capture_e2e_bench"), build.join("xr-native-spike"));
    assert!(bench.is_file() && cpp.is_file(), "{} lacks the bench or the renderer", build.display());
    let memfd = rustix::fs::memfd_create("continuity-launch", rustix::fs::MemfdFlags::CLOEXEC).unwrap();
    // Above the bench's own descriptors, as the Python test.
    let fd = rustix::io::fcntl_dupfd_cloexec(&memfd, 50).unwrap();
    drop(memfd);
    let dir = std::env::temp_dir().join(format!("xr-continuity-launch-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let rust = wrapper(&dir, "rust", Path::new(env!("CARGO_BIN_EXE_xr-renderer")), fd.as_raw_fd());
    let cpp = wrapper(&dir, "cpp", &cpp, fd.as_raw_fd());

    // The C++ blob, restored by the Rust renderer and saved back.
    let blob = std::fs::read(golden("continuity_end.bin")).unwrap();
    rustix::io::pwrite(fd.as_fd(), &blob, 0).unwrap();
    let launched = monotonic_ms();
    let stderr = run(&bench, &rust, &fd);
    assert!(stderr.iter().any(|l| l == "continuity: restored"), "{stderr:?}");
    let saved_bytes = contents(&fd);
    assert_eq!(&saved_bytes[..8], b"XRCONT01");
    let mut saved = ViewContinuity::default();
    assert!(saved.decode(&saved_bytes));

    // What was saved is the restored state: the C++ blob resumed at the restore (every clock moved on by the gap
    // since it was saved), saved later. The restore was at a whole millisecond between the launch and the save. The
    // view is live (the bench's `view` op retargets it), so only its edge pan is compared: restored, then untouched.
    // The same op sets the stabilization strength and the maximized flag (wayland.cpp set_view), so those are live too.
    let mut cpp_state = ViewContinuity::default();
    assert!(cpp_state.decode(&blob));
    let resumed_at = |t: u64| {
        let mut c = cpp_state.clone();
        c.resume(t as f64);
        c.saved_ms = saved.saved_ms;
        let pan = |v: &ViewContinuity| {
            let d = format!("{:?}", v.view);
            let at = d.find("pan: EdgePan {").unwrap();
            d[at..at + d[at..].find('}').unwrap()].to_string()
        };
        let same_pan = pan(&c) == pan(&saved);
        c.view = saved.view.clone();
        c.stabilization = saved.stabilization.clone();
        c.maximized = saved.maximized;
        same_pan && c.encode() == saved.encode()
    };
    assert!(
        (launched..=saved.saved_ms as u64).any(resumed_at),
        "saved state is not the restored one"
    );

    // The Rust blob, restored by the C++ renderer.
    let stderr = run(&bench, &cpp, &fd);
    assert!(stderr.iter().any(|l| l == "continuity: restored"), "{stderr:?}");
    assert_eq!(&contents(&fd)[..8], b"XRCONT01");

    // A fresh file restores nothing.
    rustix::fs::ftruncate(&fd, 0).unwrap();
    let stderr = run(&bench, &rust, &fd);
    assert!(!stderr.iter().any(|l| l == "continuity: restored"), "{stderr:?}");
    assert_eq!(&contents(&fd)[..8], b"XRCONT01");
    let _ = std::fs::remove_dir_all(&dir);
}
