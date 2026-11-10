//! Shared helpers for the feed tests. Every feed lives under a private prefix in its own scratch
//! directory, and every segment a test touches was created by the test with IPC_EXCL, so a test
//! never attaches to, re-initialises or removes a live driver's segment.
#![allow(dead_code)]

use std::ffi::{CString, c_void};
use std::path::PathBuf;
use std::sync::atomic::{AtomicU32, Ordering};
use xr_pose::feed::{FeedWriter, WriterOptions};

pub struct Scratch {
    pub dir: PathBuf,
}

impl Scratch {
    pub fn new() -> Self {
        static N: AtomicU32 = AtomicU32::new(0);
        let dir = std::env::temp_dir().join(format!(
            "xr-pose-feed-test-{}-{}",
            std::process::id(),
            N.fetch_add(1, Ordering::Relaxed)
        ));
        std::fs::create_dir_all(&dir).unwrap();
        Scratch { dir }
    }
    pub fn prefix(&self) -> String {
        format!("{}/feed_", self.dir.display())
    }
}

impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.dir);
    }
}

pub fn private(prefix: &str) -> WriterOptions {
    WriterOptions { prefix: prefix.to_owned(), private_segments: true, ..WriterOptions::default() }
}

/// A private writer, its scratch directory kept alive alongside.
pub fn private_writer() -> (Scratch, FeedWriter) {
    let scratch = Scratch::new();
    let writer = FeedWriter::open(&private(&scratch.prefix())).expect("private feed");
    (scratch, writer)
}

/// Attaches to an existing segment by file, read-write, outside the feed types: what another
/// process holding the lock does.
pub fn attach_raw(path: &str) -> *mut c_void {
    let c = CString::new(path).unwrap();
    unsafe {
        let id = libc::shmget(libc::ftok(c.as_ptr(), 0), 0, 0);
        assert!(id >= 0, "shmget {path}");
        let addr = libc::shmat(id, std::ptr::null(), 0);
        assert!(addr as isize != -1, "shmat {path}");
        addr
    }
}

pub fn mutex_of(prefix: &str) -> *mut libc::pthread_mutex_t {
    attach_raw(&format!("{prefix}pose_orientation_mutex")).cast()
}

/// Held by every test that forks or claims a shared feed. A writer.lock is a flock, which a fork
/// shares: while any test's child lives, another test's released lock still reads as held.
static FORKS: std::sync::Mutex<()> = std::sync::Mutex::new(());

pub fn forks() -> std::sync::MutexGuard<'static, ()> {
    FORKS.lock().unwrap_or_else(|e| e.into_inner())
}

/// A child process locks the mutex and exits holding it: the next locker gets EOWNERDEAD.
pub fn die_holding(mutex: *mut libc::pthread_mutex_t) {
    let _forks = forks();
    unsafe {
        let child = libc::fork();
        assert!(child >= 0);
        if child == 0 {
            libc::pthread_mutex_lock(mutex);
            libc::_exit(0);
        }
        let mut status = 0;
        assert_eq!(libc::waitpid(child, &mut status, 0), child);
        assert!(libc::WIFEXITED(status) && libc::WEXITSTATUS(status) == 0);
    }
}

/// A full-layout pose: q0 a turn of `deg` about z, q1 and q2 distinct, then t.
pub fn pose(deg: f32, ms: f32) -> [f32; 16] {
    let h = deg.to_radians() / 2.0;
    [0., 0., h.sin(), h.cos(), 0.1, 0.2, 0.3, 0.9, -0.1, -0.2, -0.3, 0.8, ms, ms - 16., ms - 32., 0.]
}

/// Segments laid out like the driver's, created with IPC_EXCL ahead of a shared-mode writer, which
/// then reuses them. ftok keys hold 16 bits of the inode, so on a collision with someone else's
/// segment the file is moved aside and recreated with a new inode (as spike/tests/fake_feed.hpp).
pub struct Precreated {
    ids: Vec<i32>,
}

impl Precreated {
    pub fn new(prefix: &str) -> Self {
        let mut ids = Vec::new();
        for (name, size) in [
            ("disabled", 1usize),
            ("pose_orientation", 64),
            ("pose_orientation_mutex", size_of::<libc::pthread_mutex_t>()),
            ("display_fov", 4),
            ("look_ahead_cfg", 16),
            ("pose_prediction", 64),
        ] {
            let path = format!("{prefix}{name}");
            let mut made = false;
            for attempt in 0..1024 {
                std::fs::write(&path, b"").unwrap();
                let c = CString::new(path.as_str()).unwrap();
                let id = unsafe { libc::shmget(libc::ftok(c.as_ptr(), 0), size, 0o600 | libc::IPC_CREAT | libc::IPC_EXCL) };
                if id >= 0 {
                    ids.push(id);
                    made = true;
                    break;
                }
                assert_eq!(std::io::Error::last_os_error().raw_os_error(), Some(libc::EEXIST));
                std::fs::rename(&path, format!("{path}.collision{attempt}")).unwrap();
            }
            assert!(made, "no free SysV key for {path}");
        }
        Precreated { ids }
    }
}

impl Drop for Precreated {
    fn drop(&mut self) {
        for &id in &self.ids {
            unsafe { libc::shmctl(id, libc::IPC_RMID, std::ptr::null_mut()) };
        }
    }
}
