//! The two process-wide pieces of `spike/driver/src/main.cpp` the daemon leans on: one driver per feed, and how a log
//! line reaches stderr.

use std::fs::{File, OpenOptions};
use std::io::{self, Write};
use std::os::fd::AsRawFd;
use std::os::unix::fs::OpenOptionsExt;
use std::time::{SystemTime, UNIX_EPOCH};

/// `strerror`'s text, as the C++ driver prints it: Rust's own adds " (os error N)".
fn reason(error: &io::Error) -> String {
    let text = error.to_string();
    match text.rfind(" (os error ") {
        Some(cut) => text[..cut].to_owned(),
        None => text,
    }
}

/// Two drivers on one feed would fight over its segments. The kernel drops the lock with the process, however it ends,
/// so the caller keeps the file open for the life of the process. `Err` is the `luma: error reason=` text:
/// `already-running`, or `lock <path> <why>`.
pub fn take_instance_lock(prefix: &str) -> Result<File, String> {
    let path = format!("{prefix}luma-driver.lock");
    // O_CLOEXEC is Rust's default; O_NOFOLLOW so a planted symlink cannot point the lock at someone else's file.
    let file = OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .mode(0o600)
        .custom_flags(libc::O_NOFOLLOW)
        .open(&path)
        .map_err(|e| format!("lock {path} {}", reason(&e)))?;
    // SAFETY: flock only reads the descriptor number, which `file` keeps open for the whole call.
    if unsafe { libc::flock(file.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } != 0 {
        let e = io::Error::last_os_error();
        return Err(if e.raw_os_error() == Some(libc::EWOULDBLOCK) {
            "already-running".to_owned()
        } else {
            format!("lock {path} {}", reason(&e))
        });
    }
    Ok(file)
}

/// `line` with the wall time the C++ driver adds: ` at=<epoch s>.<ms, 3 digits>` and a newline.
pub fn stamped(line: &str, at: SystemTime) -> String {
    let since = at.duration_since(UNIX_EPOCH).unwrap_or_default();
    format!(
        "{line} at={}.{:03}\n",
        since.as_secs(),
        since.subsec_millis()
    )
}

/// One write per line, so lines from several threads never interleave. A reader gone away is not an error worth
/// stopping for: tracking goes on.
pub fn log_line(line: &str) {
    let _ = io::stderr()
        .lock()
        .write_all(stamped(line, SystemTime::now()).as_bytes());
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::Duration;

    #[test]
    fn stamp_matches_the_cpp_format() {
        let at = UNIX_EPOCH + Duration::from_millis(1_793_700_000_007);
        assert_eq!(
            stamped("luma: connected", at),
            "luma: connected at=1793700000.007\n"
        );
    }

    #[test]
    fn reason_drops_the_os_error_number() {
        assert_eq!(
            reason(&io::Error::from_raw_os_error(libc::ENOENT)),
            "No such file or directory"
        );
    }
}
