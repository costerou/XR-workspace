//! Test folders under the temporary directory, removed when dropped.

use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
use std::path::{Path, PathBuf};

pub struct TestDir(PathBuf);

impl TestDir {
    pub fn new(tag: &str) -> TestDir {
        use std::sync::atomic::{AtomicU32, Ordering};
        static NEXT: AtomicU32 = AtomicU32::new(0);
        let n = NEXT.fetch_add(1, Ordering::Relaxed);
        let path =
            std::env::temp_dir().join(format!("xr-workspace-{tag}-{}-{n}", std::process::id()));
        let _ = std::fs::remove_dir_all(&path);
        std::fs::create_dir_all(&path).unwrap();
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o700)).unwrap();
        TestDir(path)
    }
    pub fn path(&self) -> &Path {
        &self.0
    }
}

impl Drop for TestDir {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

/// `value` written as a private JSON file (0600), replacing any file there.
pub fn write_private(path: &Path, value: &serde_json::Value) {
    let tmp = path.with_extension("tmp");
    let mut file = std::fs::File::options()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(&tmp)
        .unwrap();
    std::io::Write::write_all(&mut file, value.to_string().as_bytes()).unwrap();
    std::fs::rename(&tmp, path).unwrap();
}

/// An executable script (0700) at `path`, replacing any file there. Written by a child process: a
/// writable descriptor held here could leak into another test's fork and make the exec fail with
/// ETXTBSY.
pub fn write_script(path: &Path, body: &str) {
    let status = std::process::Command::new("/bin/sh")
        .args([
            "-c",
            "printf '%s' \"$1\" > \"$2\" && chmod 700 \"$2\"",
            "sh",
            body,
        ])
        .arg(path)
        .status()
        .unwrap();
    assert!(status.success());
}
