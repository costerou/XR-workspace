//! The feed across languages: the C++ `pose_test` (spike/tests/pose_test.cpp, `--feed-read` and
//! `--feed-write`) reads what the Rust writer writes, and the Rust reader reads what the C++
//! writer writes, including a lock whose holder died on the other side (EOWNERDEAD).
//!
//! `pose_test` is built with `c++` (or `$CXX`) from pose_test.cpp and the renderer's real
//! `pose.cpp`, as tests/cpp_interop.rs builds its helper.
mod common;

use common::*;
use std::io::{BufRead, BufReader, Write};
use std::path::PathBuf;
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};
use std::sync::OnceLock;
use xr_pose::feed::{FeedReader, FeedSample};

fn pose_test() -> &'static PathBuf {
    static BIN: OnceLock<PathBuf> = OnceLock::new();
    BIN.get_or_init(|| {
        let spike = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../../spike");
        let out = PathBuf::from(env!("CARGO_TARGET_TMPDIR")).join("pose_test");
        let status = Command::new(std::env::var("CXX").unwrap_or_else(|_| "c++".into()))
            .args(["-std=c++20", "-O2", "-pthread", "-I"])
            .arg(spike.join("include"))
            .arg("-I")
            .arg(spike.join("tests"))
            .arg(spike.join("tests/pose_test.cpp"))
            .arg(spike.join("src/pose.cpp"))
            .arg("-o")
            .arg(&out)
            .status()
            .expect("run the C++ compiler (set CXX)");
        assert!(status.success(), "building pose_test failed");
        out
    })
}

fn floats(line: &str, tag: &str) -> Vec<f32> {
    let rest = line.strip_prefix(tag).unwrap_or_else(|| panic!("expected {tag:?}, got {line:?}"));
    rest.split_whitespace().map(|f| f.parse().unwrap()).collect()
}

/// `pose_test --feed-read`: the lines it printed.
fn cpp_read(bin: &PathBuf, prefix: &str, reads: u32) -> Vec<String> {
    let out = Command::new(bin).args(["--feed-read", prefix, &reads.to_string()]).output().unwrap();
    assert!(out.status.success(), "{out:?}");
    String::from_utf8(out.stdout).unwrap().lines().map(str::to_owned).collect()
}

fn as_floats(s: &FeedSample) -> [f32; 16] {
    let [a, b] = s.older;
    let o = s.orientation;
    [o[0], o[1], o[2], o[3], a[0], a[1], a[2], a[3], b[0], b[1], b[2], b[3], s.device_ms, s.older_ms[0], s.older_ms[1], 0.0]
}

/// q0 is normalised by the reader (a unit quaternion may move by an ulp); the rest is exact.
fn assert_same_pose(got: &[f32], want: &[f32; 16]) {
    assert_eq!(got.len(), 16, "{got:?}");
    for i in 0..4 {
        assert!((got[i] - want[i]).abs() <= 1e-6, "q0[{i}]: {} vs {}", got[i], want[i]);
    }
    assert_eq!(&got[4..], &want[4..]);
}

#[test]
fn cpp_reads_the_rust_feed() {
    let bin = pose_test();
    let scratch = Scratch::new();
    let mut options = private(&scratch.prefix());
    options.display_fov_deg = 46.5;
    options.look_ahead_cfg = [10.0, 0.6, 10.0, 40.0];
    let mut writer = xr_pose::feed::FeedWriter::open(&options).unwrap();
    let prefix = scratch.prefix();

    assert_eq!(cpp_read(bin, &prefix, 1), ["fov 46.5", "look_ahead 10 0.600000024 10 40", "none"], "disabled at open");
    let v = pose(30.0, 1234.5);
    assert!(writer.write(&v));
    writer.set_disabled(false);
    let lines = cpp_read(bin, &prefix, 1);
    assert_same_pose(&floats(&lines[2], "sample"), &v);
    writer.set_disabled(true);
    assert_eq!(cpp_read(bin, &prefix, 1)[2], "none");
    writer.set_disabled(false);

    // EOWNERDEAD: a process died holding the Rust-initialised lock. The C++ reader recovers it and
    // releases it (a fresh reader has no earlier sample to serve, so "none", then a sample).
    let mutex = mutex_of(&prefix);
    die_holding(mutex);
    let lines = cpp_read(bin, &prefix, 2);
    assert_eq!(lines[2], "none");
    assert_same_pose(&floats(&lines[3], "sample"), &v);
    let w = pose(31.0, 1235.5);
    assert!(writer.write(&w), "the lock is free again for the Rust writer");
    assert_same_pose(&floats(&cpp_read(bin, &prefix, 1)[2], "sample"), &w);
}

struct CppWriter {
    child: Child,
    stdin: ChildStdin,
    stdout: BufReader<ChildStdout>,
}

impl CppWriter {
    fn start(bin: &PathBuf, prefix: &str) -> Self {
        let mut child = Command::new(bin)
            .args(["--feed-write", prefix])
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .spawn()
            .unwrap();
        let stdin = child.stdin.take().unwrap();
        let stdout = BufReader::new(child.stdout.take().unwrap());
        let mut w = CppWriter { child, stdin, stdout };
        assert_eq!(w.line(), "ready");
        w
    }
    fn line(&mut self) -> String {
        let mut line = String::new();
        self.stdout.read_line(&mut line).unwrap();
        line.trim_end().to_owned()
    }
    fn send(&mut self, cmd: &str) -> String {
        writeln!(self.stdin, "{cmd}").unwrap();
        self.stdin.flush().unwrap();
        self.line()
    }
    fn write(&mut self, v: &[f32; 16]) {
        let text: Vec<String> = v.iter().map(|f| format!("{f:e}")).collect();
        assert_eq!(self.send(&format!("write {}", text.join(" "))), "ok");
    }
}

impl Drop for CppWriter {
    fn drop(&mut self) {
        let _ = writeln!(self.stdin, "quit");
        let _ = self.child.wait();
    }
}

#[test]
fn rust_reads_the_cpp_feed() {
    let bin = pose_test();
    let scratch = Scratch::new();
    let prefix = scratch.prefix();
    let mut cpp = CppWriter::start(bin, &prefix);
    let mut reader = FeedReader::open(&prefix).unwrap();
    assert_eq!(reader.display_fov(), Some(52.0));
    assert_eq!(reader.look_ahead_cfg(), Some([10.0, 0.6, 10.0, 40.0]));

    let v = pose(20.0, 500.25);
    cpp.write(&v);
    assert_same_pose(&as_floats(&reader.read().unwrap()), &v);
    assert_eq!(cpp.send("disabled 1"), "ok");
    assert_eq!(reader.read(), None);
    assert_eq!(cpp.send("disabled 0"), "ok");
    assert_same_pose(&as_floats(&reader.read().unwrap()), &v);

    // EOWNERDEAD: a C++ process died holding the lock. The Rust reader serves the last good
    // sample, releases the lock (the C++ side takes it at once), then reads on.
    assert_eq!(cpp.send("die-holding"), "ok");
    assert_same_pose(&as_floats(&reader.read().unwrap()), &v);
    assert_eq!(cpp.send("probe"), "rc 0");
    let w = pose(21.0, 501.25);
    cpp.write(&w);
    assert_same_pose(&as_floats(&reader.read().unwrap()), &w);

    // And the other way: a Rust-side process died holding the C++-initialised lock. The C++ writer
    // recovers it.
    die_holding(mutex_of(&prefix));
    let x = pose(22.0, 502.25);
    cpp.write(&x);
    assert_eq!(cpp.send("probe"), "rc 0");
    assert_same_pose(&as_floats(&reader.read().unwrap()), &x);
}
