//! The SysV feed, Rust writer against Rust reader. tests/cpp_interop.rs crosses to C++.
mod common;

use common::*;
use std::time::{Duration, Instant};
use xr_pose::feed::{FeedReader, FeedWriter, WriterOptions};

fn timedlock_1s(mutex: *mut libc::pthread_mutex_t) -> i32 {
    unsafe {
        let mut deadline = libc::timespec { tv_sec: 0, tv_nsec: 0 };
        libc::clock_gettime(libc::CLOCK_REALTIME, &mut deadline);
        deadline.tv_sec += 1;
        let rc = libc::pthread_mutex_timedlock(mutex, &deadline);
        if rc == 0 {
            libc::pthread_mutex_unlock(mutex);
        }
        rc
    }
}

#[test]
fn absent_feed_is_refused() {
    let scratch = Scratch::new();
    let err = FeedReader::open(&scratch.prefix()).err().unwrap();
    assert!(err.0.starts_with("feed absent: "), "{err}");
}

#[test]
fn round_trip_disabled_fov_and_look_ahead() {
    let scratch = Scratch::new();
    let mut options = private(&scratch.prefix());
    options.display_fov_deg = 46.0;
    options.look_ahead_cfg = [1.0, 2.0, 3.0, 4.0];
    let mut writer = FeedWriter::open(&options).unwrap();
    let mut reader = FeedReader::open(&scratch.prefix()).unwrap();
    assert_eq!(reader.display_fov(), Some(46.0));
    assert_eq!(reader.look_ahead_cfg(), Some([1.0, 2.0, 3.0, 4.0]));
    assert_eq!(reader.read(), None, "open leaves the feed disabled");

    let v = pose(30.0, 1234.5);
    assert!(writer.write(&v));
    assert_eq!(reader.read(), None, "still disabled until the writer says so");
    writer.set_disabled(false);
    let s = reader.read().unwrap();
    assert_eq!(s.device_ms, 1234.5);
    assert!((s.orientation[2] - 15f32.to_radians().sin()).abs() < 1e-6);
    assert_eq!(s.older, [[0.1, 0.2, 0.3, 0.9], [-0.1, -0.2, -0.3, 0.8]]);
    assert_eq!(s.older_ms, [1234.5 - 16.0, 1234.5 - 32.0]);

    // An unusable quaternion or time gives no sample.
    let mut bad = v;
    bad[0] = f32::NAN;
    assert!(writer.write(&bad));
    assert_eq!(reader.read(), None);
    let mut zero = v;
    zero[..4].fill(0.0);
    assert!(writer.write(&zero));
    assert_eq!(reader.read(), None);
    let mut no_time = v;
    no_time[12] = f32::INFINITY;
    assert!(writer.write(&no_time));
    assert_eq!(reader.read(), None);

    writer.set_disabled(true);
    assert_eq!(reader.read(), None);
    writer.set_disabled(false);
    drop(writer);
    assert_eq!(reader.read(), None, "closing the writer disables the feed");
}

#[test]
fn insane_fov_is_never_used() {
    let scratch = Scratch::new();
    for fov in [f32::NAN, 500.0, 5.0] {
        let mut options = private(&scratch.prefix());
        options.display_fov_deg = fov;
        let _writer = FeedWriter::open(&options).unwrap();
        assert_eq!(FeedReader::open(&scratch.prefix()).unwrap().display_fov(), None);
    }
}

#[test]
fn private_feed_cleans_up_its_files() {
    let scratch = Scratch::new();
    let writer = FeedWriter::open(&private(&scratch.prefix())).unwrap();
    assert!(std::path::Path::new(&format!("{}pose_orientation", scratch.prefix())).exists());
    drop(writer);
    assert_eq!(std::fs::read_dir(&scratch.dir).unwrap().count(), 0);
    assert!(FeedReader::open(&scratch.prefix()).is_err());
}

// EOWNERDEAD, reader side: the writer died holding its robust lock. The reader makes it
// consistent and releases it, serving the last good sample for that read, then reads on; it must
// never keep the lock, which would stall every later read and lock out a restarted writer.
#[test]
fn reader_recovers_a_lock_whose_holder_died() {
    let (scratch, mut writer) = private_writer();
    let mut reader = FeedReader::open(&scratch.prefix()).unwrap();
    writer.write(&pose(5.0, 100.0));
    writer.set_disabled(false);
    assert_eq!(reader.read().unwrap().device_ms, 100.0);
    let mutex = mutex_of(&scratch.prefix());
    die_holding(mutex);
    assert_eq!(reader.read().unwrap().device_ms, 100.0, "EOWNERDEAD serves the last good sample");
    for _ in 0..5 {
        assert_eq!(reader.read().unwrap().device_ms, 100.0);
    }
    assert_eq!(timedlock_1s(mutex), 0, "the reader released the recovered lock");
    assert!(writer.write(&pose(6.0, 101.0)));
    assert_eq!(reader.read().unwrap().device_ms, 101.0);
}

// EOWNERDEAD, writer side: a reader died holding the lock. The next write takes it over.
#[test]
fn writer_recovers_a_lock_whose_holder_died() {
    let (scratch, mut writer) = private_writer();
    let mut reader = FeedReader::open(&scratch.prefix()).unwrap();
    writer.set_disabled(false);
    let mutex = mutex_of(&scratch.prefix());
    die_holding(mutex);
    assert!(writer.write(&pose(7.0, 200.0)));
    assert_eq!(timedlock_1s(mutex), 0);
    assert_eq!(reader.read().unwrap().device_ms, 200.0);
}

// Not recoverable: a holder died and someone unlocked without making it consistent (XRLinuxDriver
// ignores EOWNERDEAD). Reads get no sample and leave the lock word free; the writer rebuilds it.
#[test]
fn unrecoverable_lock_gives_no_sample_and_the_writer_rebuilds_it() {
    let (scratch, mut writer) = private_writer();
    let mut reader = FeedReader::open(&scratch.prefix()).unwrap();
    writer.write(&pose(5.0, 300.0));
    writer.set_disabled(false);
    let mutex = mutex_of(&scratch.prefix());
    die_holding(mutex);
    unsafe {
        assert_eq!(libc::pthread_mutex_lock(mutex), libc::EOWNERDEAD);
        libc::pthread_mutex_unlock(mutex);
    }
    for _ in 0..3 {
        assert_eq!(reader.read(), None);
    }
    assert_eq!(timedlock_1s(mutex), libc::ENOTRECOVERABLE, "at once, not held by the reader");
    assert!(writer.write(&pose(5.0, 301.0)));
    assert_eq!(reader.read().unwrap().device_ms, 301.0);
}

// A live holder: the writer never waits, and the reader waits once (bounded), then serves the
// newest sample again without waiting.
#[test]
fn held_lock_never_blocks_the_writer_and_bounds_the_reader() {
    let (scratch, mut writer) = private_writer();
    let mut reader = FeedReader::open(&scratch.prefix()).unwrap();
    writer.write(&pose(5.0, 400.0));
    writer.set_disabled(false);
    assert_eq!(reader.read().unwrap().device_ms, 400.0);
    let mutex = mutex_of(&scratch.prefix()) as usize;
    let (locked_tx, locked_rx) = std::sync::mpsc::channel();
    let (release_tx, release_rx) = std::sync::mpsc::channel::<()>();
    let holder = std::thread::spawn(move || unsafe {
        let m = mutex as *mut libc::pthread_mutex_t;
        libc::pthread_mutex_lock(m);
        locked_tx.send(()).unwrap();
        release_rx.recv().unwrap();
        libc::pthread_mutex_unlock(m);
    });
    locked_rx.recv().unwrap();
    let start = Instant::now();
    for _ in 0..100 {
        assert!(!writer.write(&pose(9.0, 999.0)));
    }
    assert!(start.elapsed() < Duration::from_millis(5), "writer waited: {:?}", start.elapsed());
    let start = Instant::now();
    for _ in 0..10 {
        assert_eq!(reader.read().unwrap().device_ms, 400.0);
    }
    assert!(start.elapsed() < Duration::from_millis(15), "a 2 ms wait per read was 20 ms: {:?}", start.elapsed());
    release_tx.send(()).unwrap();
    holder.join().unwrap();
    assert!(writer.write(&pose(5.0, 401.0)));
    assert_eq!(reader.read().unwrap().device_ms, 401.0);
}

#[test]
fn reader_never_misses_under_a_1khz_writer() {
    let (scratch, mut writer) = private_writer();
    let mut reader = FeedReader::open(&scratch.prefix()).unwrap();
    writer.write(&pose(1.0, 1.0));
    writer.set_disabled(false);
    let stop = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
    let flag = stop.clone();
    let thread = std::thread::spawn(move || {
        let mut ms = 2.0;
        while !flag.load(std::sync::atomic::Ordering::Relaxed) {
            writer.write(&pose(ms * 0.01, ms));
            ms += 1.0;
            std::thread::sleep(Duration::from_micros(900));
        }
    });
    let mut got = 0;
    for _ in 0..300 {
        let s = reader.read();
        // Never torn: q1 and t1 are written with q0 and t0.
        if let Some(s) = s {
            assert_eq!(s.older_ms[0], s.device_ms - 16.0);
            got += 1;
        }
        std::thread::sleep(Duration::from_micros(300));
    }
    stop.store(true, std::sync::atomic::Ordering::Relaxed);
    thread.join().unwrap();
    assert_eq!(got, 300);
}

fn shared(scratch: &Scratch) -> WriterOptions {
    WriterOptions {
        prefix: scratch.prefix(),
        proc_root: scratch.dir.join("proc"),
        ..WriterOptions::default()
    }
}

fn fake_process(scratch: &Scratch, stat: &str) {
    let pid = stat.split(' ').next().unwrap();
    let dir = scratch.dir.join("proc").join(pid);
    std::fs::create_dir_all(&dir).unwrap();
    std::fs::write(dir.join("stat"), format!("{stat}\n")).unwrap();
}

#[test]
fn second_writer_is_refused_until_the_first_is_gone() {
    let scratch = Scratch::new();
    let _segments = Precreated::new(&scratch.prefix());
    let options = shared(&scratch);
    let mut first = FeedWriter::open(&options).unwrap();
    let err = FeedWriter::open(&options).err().unwrap();
    assert!(err.0.contains("another publisher"), "{err}");
    let mut reader = FeedReader::open(&scratch.prefix()).unwrap();
    first.write(&pose(1.0, 2.0));
    first.set_disabled(false);
    assert_eq!(reader.read().unwrap().device_ms, 2.0, "the refused one touched nothing");
    drop(first);
    assert!(scratch.dir.join("feed_pose_orientation").exists(), "shared segments stay");
    FeedWriter::open(&options).unwrap();
    // A crash lets go as well: the lock belongs to the process.
    unsafe {
        let child = libc::fork();
        if child == 0 {
            let held = FeedWriter::open(&options);
            libc::_exit(if held.is_ok() { 0 } else { 1 });
        }
        let mut status = 0;
        assert_eq!(libc::waitpid(child, &mut status, 0), child);
        assert!(libc::WIFEXITED(status) && libc::WEXITSTATUS(status) == 0);
    }
    FeedWriter::open(&options).unwrap();
}

#[test]
fn running_xrdriver_is_refused_and_a_dead_one_is_not() {
    let scratch = Scratch::new();
    let _segments = Precreated::new(&scratch.prefix());
    let options = shared(&scratch);
    fake_process(&scratch, "100 (bash) S 1 100 100 0 -1");
    fake_process(&scratch, "200 ((sd-pam)) S 1 200 200 0 -1");
    fake_process(&scratch, "300 (xrDriver) Z 1 300 300 0 -1");
    let mut writer = FeedWriter::open(&options).unwrap();
    assert!(writer.write(&pose(1.0, 77.0)));
    drop(writer);
    fake_process(&scratch, "400 (xrDriver) S 1 400 400 0 -1");
    let err = FeedWriter::open(&options).err().unwrap();
    assert!(err.0.contains("xrDriver is running (pid 400)"), "{err}");
    // Refused before touching anything: the pose and its mutex are as the last writer left them.
    let raw = attach_raw(&format!("{}pose_orientation", scratch.prefix())) as *const f32;
    assert_eq!(unsafe { raw.add(12).read() }, 77.0);
    assert_eq!(timedlock_1s(mutex_of(&scratch.prefix())), 0);
}

#[test]
fn private_feeds_skip_the_writer_checks() {
    let scratch = Scratch::new();
    fake_process(&scratch, "400 (xrDriver) S 1 400 400 0 -1");
    let mut options = private(&scratch.prefix());
    options.proc_root = scratch.dir.join("proc");
    let _a = FeedWriter::open(&options).unwrap();
    assert!(!scratch.dir.join("feed_writer.lock").exists());
}
