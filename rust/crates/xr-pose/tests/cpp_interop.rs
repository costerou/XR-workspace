//! Rust against the C++ driver code itself. `tests/cpp/pose_interop.cpp` is built with `c++` from
//! the driver's real `rt.cpp` and `predict.cpp`, then:
//! - its `sizeof`/`offsetof` must equal the layout the crate asserts at compile time;
//! - each side reads what the other writes, for the seqlock object and the PredictionWire bytes;
//! - torn-read stress runs across processes and languages, both ways, including a C++ writer
//!   killed mid-stream and a Rust writer taking over.
//!
//! `record(i)` and `state(i)` must match the generators in the C++ helper.

use std::io::{BufRead, BufReader};
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::sync::Arc;
use std::sync::OnceLock;
use std::sync::atomic::{AtomicBool, Ordering};
use xr_pose::{
    MotionState, POSE_CALIBRATED, POSE_SHM_NAME, PREDICTION_IPC_NAME, PREDICTION_MAGIC, PoseReader,
    PoseRecord, PoseWriter, PredictionWire,
};

fn helper() -> &'static PathBuf {
    static BIN: OnceLock<PathBuf> = OnceLock::new();
    BIN.get_or_init(|| {
        let manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
        let driver = manifest.join("../../../spike/driver");
        let out = PathBuf::from(env!("CARGO_TARGET_TMPDIR")).join("pose_interop");
        let status = Command::new(std::env::var("CXX").unwrap_or_else(|_| "c++".into()))
            .args([
                "-std=c++20",
                "-O2",
                "-Wall",
                "-Wextra",
                "-Werror",
                "-Wno-class-memaccess",
                "-pthread",
                "-I",
            ])
            .arg(driver.join("include"))
            .arg(manifest.join("tests/cpp/pose_interop.cpp"))
            .arg(driver.join("src/rt.cpp"))
            .arg(driver.join("src/predict.cpp"))
            .arg("-o")
            .arg(&out)
            .status()
            .expect("run the C++ compiler (set CXX)");
        assert!(status.success(), "building the C++ helper failed");
        out
    })
}

fn run(args: &[&str]) -> String {
    let out = Command::new(helper()).args(args).output().unwrap();
    assert!(
        out.status.success(),
        "helper {args:?}: {}",
        String::from_utf8_lossy(&out.stderr)
    );
    String::from_utf8(out.stdout).unwrap()
}

struct Name(String);
impl Name {
    fn new(tag: &str) -> Self {
        Self(format!("/xr-pose-interop-{}-{tag}", std::process::id()))
    }
}
impl Drop for Name {
    fn drop(&mut self) {
        let _ = std::fs::remove_file(format!("/dev/shm{}", self.0));
    }
}

fn fbits(v: u32) -> f32 {
    f32::from_bits(v & 0x3fff_ffff)
}

fn record(i: u64) -> PoseRecord {
    let lo = i as u32;
    PoseRecord {
        w: fbits(lo),
        x: fbits(lo.wrapping_add(1)),
        y: fbits(lo.wrapping_add(2)),
        z: fbits(lo.wrapping_add(3)),
        t_raw_ns: i,
        raw_to_mono_ns: -(i as i64),
        seq: lo,
        flags: lo ^ 0x5a5a_5a5a,
    }
}

fn state(i: u32) -> MotionState {
    let f = |k: u32| fbits(i.wrapping_mul(31).wrapping_add(k));
    MotionState {
        q: [f(0), f(1), f(2), f(3)],
        host_ns: u64::from(i) << 32 | u64::from(i),
        seq: i,
        omega_rad_s: [f(4), f(5), f(6)],
        alpha_rad_s2: [f(7), f(8), f(9)],
        valid: true,
        calibrated: i % 2 == 0,
    }
}

fn pose_line(r: &PoseRecord) -> String {
    format!(
        "{:08x} {:08x} {:08x} {:08x} {} {} {} {}\n",
        r.w.to_bits(),
        r.x.to_bits(),
        r.y.to_bits(),
        r.z.to_bits(),
        r.t_raw_ns,
        r.raw_to_mono_ns,
        r.seq,
        r.flags
    )
}

#[test]
fn layout_matches_the_cpp_compiler() {
    use std::mem::{align_of, offset_of, size_of};
    let want = [
        ("pose_size", size_of::<PoseRecord>().to_string()),
        ("pose_align", align_of::<PoseRecord>().to_string()),
        ("pose_w", offset_of!(PoseRecord, w).to_string()),
        ("pose_x", offset_of!(PoseRecord, x).to_string()),
        ("pose_y", offset_of!(PoseRecord, y).to_string()),
        ("pose_z", offset_of!(PoseRecord, z).to_string()),
        (
            "pose_t_raw_ns",
            offset_of!(PoseRecord, t_raw_ns).to_string(),
        ),
        (
            "pose_raw_to_mono_ns",
            offset_of!(PoseRecord, raw_to_mono_ns).to_string(),
        ),
        ("pose_seq", offset_of!(PoseRecord, seq).to_string()),
        ("pose_flags", offset_of!(PoseRecord, flags).to_string()),
        ("pose_calibrated", POSE_CALIBRATED.to_string()),
        ("pose_shm_name", POSE_SHM_NAME.to_string()),
        // The slot and object sizes the crate asserts privately (seqlock.rs).
        ("slot_size", "64".into()),
        ("slot_words", "8".into()),
        ("slot_offset", "64".into()),
        ("shm_total", "128".into()),
        ("wire_size", size_of::<PredictionWire>().to_string()),
        ("wire_align", align_of::<PredictionWire>().to_string()),
        ("wire_seq", "0".into()),
        ("wire_magic", "4".into()),
        ("wire_words", "8".into()),
        ("wire_magic_value", PREDICTION_MAGIC.to_string()),
        ("wire_ipc_name", PREDICTION_IPC_NAME.to_string()),
    ];
    let got = run(&["layout"]);
    let want: String = want.iter().map(|(k, v)| format!("{k} {v}\n")).collect();
    assert_eq!(got, want);
}

#[test]
fn rust_writer_object_has_the_cpp_size() {
    let name = Name::new("size");
    let _w = PoseWriter::create(&name.0).unwrap();
    assert_eq!(
        std::fs::metadata(format!("/dev/shm{}", name.0))
            .unwrap()
            .len(),
        128
    );
}

#[test]
fn cpp_reads_what_rust_writes() {
    let name = Name::new("r2c");
    let w = PoseWriter::create(&name.0).unwrap();
    w.publish(&record(0x1234_5678_9abc));
    assert_eq!(
        run(&["read-pose", &name.0]),
        pose_line(&record(0x1234_5678_9abc))
    );
}

#[test]
fn rust_reads_what_cpp_writes() {
    let name = Name::new("c2r");
    run(&["write-pose", &name.0, "77"]);
    assert_eq!(PoseReader::open(&name.0).unwrap().read(), Some(record(77)));
    // And a Rust writer reuses the C++ object instead of replacing it.
    let w = PoseWriter::create(&name.0).unwrap();
    let r = PoseReader::open(&name.0).unwrap();
    assert_eq!(r.read(), Some(record(77)), "same object, last value kept");
    w.publish(&record(78));
    assert_eq!(r.read(), Some(record(78)));
}

#[test]
fn cpp_refuses_while_rust_writes() {
    let name = Name::new("lock");
    let _w = PoseWriter::create(&name.0).unwrap();
    let out = Command::new(helper())
        .args(["write-pose", &name.0, "1"])
        .output()
        .unwrap();
    assert!(!out.status.success());
    assert!(String::from_utf8_lossy(&out.stderr).contains("already has a writer"));
}

#[repr(C, align(64))]
struct Line([u8; 64]);

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

#[test]
fn prediction_wire_bytes_cross_both_ways() {
    for i in [1u32, 2, 0xfeed_beef] {
        // C++ writes, Rust reads.
        let mut line = Line([0; 64]);
        let text = run(&["write-prediction", &i.to_string()]);
        for (k, b) in line.0.iter_mut().enumerate() {
            *b = u8::from_str_radix(&text[2 * k..2 * k + 2], 16).unwrap();
        }
        // SAFETY: 64 aligned bytes; PredictionWire is repr(C) atomics, any bits valid.
        let wire = unsafe { &*(line.0.as_ptr().cast::<PredictionWire>()) };
        assert_eq!(wire.read(), Some(state(i)));

        // Rust writes, C++ reads, and the bytes are the ones C++ wrote.
        let ours = PredictionWire::default();
        ours.write(&state(i));
        // SAFETY: reading the 64 bytes of a live, quiescent object.
        let bytes = unsafe {
            std::slice::from_raw_parts((&ours as *const PredictionWire).cast::<u8>(), 64)
        };
        assert_eq!(hex(bytes), text.trim(), "identical bytes for state({i})");
        assert_eq!(
            run(&["read-prediction", &hex(bytes)]),
            format!("match {i}\n")
        );
    }
    // Invalid and empty read as nothing on the C++ side.
    let ours = PredictionWire::default();
    assert_eq!(run(&["read-prediction", &hex(&[0; 64])]), "none\n");
    ours.write(&MotionState {
        valid: false,
        ..state(3)
    });
    let bytes =
        unsafe { std::slice::from_raw_parts((&ours as *const PredictionWire).cast::<u8>(), 64) };
    assert_eq!(run(&["read-prediction", &hex(bytes)]), "none\n");
}

// Rust writes at full speed; a C++ reader process checks every record it gets is whole.
#[test]
fn torn_read_stress_rust_writer_cpp_reader() {
    let name = Name::new("stress-r2c");
    let w = PoseWriter::create(&name.0).unwrap();
    w.publish(&record(1));
    let stop = Arc::new(AtomicBool::new(false));
    let writer = {
        let stop = stop.clone();
        std::thread::spawn(move || {
            let mut i = 1u64;
            while !stop.load(Ordering::Relaxed) {
                i += 1;
                w.publish(&record(i));
            }
            i
        })
    };
    let out = run(&["stress-read", &name.0, "5000000"]);
    stop.store(true, Ordering::Relaxed);
    let writes = writer.join().unwrap();
    let mut it = out.split_whitespace().map(|v| v.parse::<u64>().unwrap());
    let (ok, span) = (it.next().unwrap(), it.next().unwrap());
    eprintln!("C++ reader: {ok} whole records over {span} writes; Rust wrote {writes}");
    assert!(
        ok > 0 && span > 1000,
        "reader and writer did not overlap: {out}"
    );
}

// A C++ writer at full speed, a Rust reader checking each record; then the writer is SIGKILLed,
// a Rust writer takes over, and the same Rust reader keeps working without reopening.
#[test]
fn torn_read_stress_cpp_writer_rust_reader_and_takeover() {
    let name = Name::new("stress-c2r");
    let mut child = Command::new(helper())
        .args(["stress-write", &name.0])
        .stdout(Stdio::piped())
        .spawn()
        .unwrap();
    let mut line = String::new();
    BufReader::new(child.stdout.take().unwrap())
        .read_line(&mut line)
        .unwrap();
    assert_eq!(line, "ready\n");
    let r = PoseReader::open(&name.0).unwrap();
    let (mut ok, mut first, mut last) = (0u64, 0u64, 0u64);
    let deadline = std::time::Instant::now() + std::time::Duration::from_millis(1500);
    while std::time::Instant::now() < deadline {
        if let Some(p) = r.read() {
            assert_eq!(p, record(p.t_raw_ns), "torn read");
            assert!(p.t_raw_ns >= last, "went backwards");
            if ok == 0 {
                first = p.t_raw_ns;
            }
            last = p.t_raw_ns;
            ok += 1;
        }
    }
    eprintln!(
        "Rust reader: {ok} whole records over {} C++ writes",
        last - first
    );
    assert!(
        ok > 0 && last - first > 1000,
        "reader and writer did not overlap"
    );
    child.kill().unwrap();
    child.wait().unwrap();
    let _ = r.read(); // whatever the kill left, it returns
    let w = PoseWriter::create(&name.0).unwrap();
    w.publish(&record(0xabcdef));
    assert_eq!(r.read(), Some(record(0xabcdef)));
}
