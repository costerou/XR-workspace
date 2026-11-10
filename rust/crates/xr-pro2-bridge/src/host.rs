//! The duties `tools/pro2_runtime/launch.py` does around the bridge, done by the host itself when
//! asked, so a parent that is not the Python launcher can run it: its own user and network
//! namespace (`--unshare`, as `unshare -Urn`), the same-user competing-provider scan
//! (`--scan-competitors`, as `launch.competing`) and the heartbeat file
//! (`--health <path> --nonce <n>`, read by `tools/pro2_runtime/scope.py`'s `TestScope.healthy`).
//! Each is opt-in: the Python launcher still does them itself and passes none of these flags.

use std::ffi::{CString, c_int};
use std::io::Write as _;
use std::os::unix::ffi::OsStrExt;
use std::path::Path;
use std::time::{Duration, Instant};

/// A new user namespace with this user mapped to root, and a new network namespace with only
/// `lo`, as `unshare -Urn` gives the launcher's child. Must run while the process has one thread.
pub fn unshare_user_net() -> Result<(), &'static str> {
    // SAFETY: plain system calls; getuid and getgid cannot fail.
    let (uid, gid) = unsafe { (libc::getuid(), libc::getgid()) };
    if unsafe { libc::unshare(libc::CLONE_NEWUSER | libc::CLONE_NEWNET) } != 0 {
        return Err("unshare");
    }
    let write = |path: &str, text: String| {
        std::fs::OpenOptions::new()
            .write(true)
            .open(path)
            .and_then(|mut f| f.write_all(text.as_bytes()))
            .map_err(|_| "unshare-map")
    };
    write("/proc/self/setgroups", "deny".into())?;
    write("/proc/self/uid_map", format!("0 {uid} 1"))?;
    write("/proc/self/gid_map", format!("0 {gid} 1"))
}

/// Process names of the known head-tracking providers. The C++ bundle's bridge and this one are
/// both here: a second bridge is a competitor too.
const PROVIDERS: [&[u8]; 4] = [
    b"xrDriver",
    b"xr-luma-driver",
    b"pro2-sdk-bridge",
    b"xr-pro2-bridge",
];

/// A same-user process outside `exclude` that is a known provider by name or has the VITURE SDK
/// mapped, as `launch.competing`. Unreadable processes are skipped: this is not a claim to see
/// every process, only this user's. Run it in the user namespace the providers live in: from
/// inside a new one the kernel refuses every other process's `maps` (the caller's capabilities
/// there are not a subset of the target's), and the SDK check would see nothing.
pub fn competing(exclude: &[u32]) -> Result<bool, &'static str> {
    // SAFETY: getuid cannot fail.
    let uid = unsafe { libc::getuid() };
    for entry in std::fs::read_dir("/proc").map_err(|_| "provider-scan")? {
        let Ok(entry) = entry else { continue };
        let name = entry.file_name();
        let Some(pid) = std::str::from_utf8(name.as_bytes())
            .ok()
            .and_then(|n| n.parse::<u32>().ok())
        else {
            continue;
        };
        if exclude.contains(&pid) {
            continue;
        }
        let dir = entry.path();
        let Ok(meta) = std::fs::metadata(&dir) else {
            continue;
        };
        if std::os::unix::fs::MetadataExt::uid(&meta) != uid {
            continue;
        }
        if let Ok(comm) = std::fs::read(dir.join("comm"))
            && PROVIDERS.contains(&comm.trim_ascii_end())
        {
            return Ok(true);
        }
        if let Ok(maps) = std::fs::read(dir.join("maps"))
            && maps.windows(13).any(|w| w == b"libglasses.so")
        {
            return Ok(true);
        }
    }
    Ok(false)
}

/// How often the scanner looks again, as `launch.COMPETING_EVERY_S`.
const SCAN_EVERY: Duration = Duration::from_secs(1);

/// The rescan for the rest of the run: a child process forked before `--unshare`, so it stays in
/// the namespace the providers are in, as the launcher's scanner thread does. A process, not a
/// thread, for that reason; and one scan reads every process's maps (each read holds that
/// process's mmap lock), which must not delay the pose loop or the heartbeat either. It says one
/// byte and exits: `c` a provider, `f` the scan failed. Silence is all clear; end of file without a
/// byte means it died, and the run cannot go on unwatched.
pub struct Scanner {
    pid: libc::pid_t,
    read: c_int,
}

impl Scanner {
    /// Before any thread exists: the child runs only `competing` and system calls.
    pub fn spawn() -> Result<Self, &'static str> {
        let parent = std::process::id();
        let mut ends = [-1; 2];
        // SAFETY: pipe2 into a two-slot array; fork while single-threaded; the child never
        // returns into the caller's code and leaves through _exit.
        unsafe {
            if libc::pipe2(ends.as_mut_ptr(), libc::O_CLOEXEC | libc::O_NONBLOCK) != 0 {
                return Err("provider-scan");
            }
            let pid = libc::fork();
            if pid < 0 {
                libc::close(ends[0]);
                libc::close(ends[1]);
                return Err("provider-scan");
            }
            if pid == 0 {
                libc::close(ends[0]);
                // Dies with the host, and never outlives a host that died before this line.
                if libc::prctl(libc::PR_SET_PDEATHSIG, libc::SIGKILL) != 0
                    || libc::getppid() as u32 != parent
                {
                    libc::_exit(1);
                }
                let me = std::process::id();
                let word: u8 = loop {
                    std::thread::sleep(SCAN_EVERY);
                    match competing(&[parent, me]) {
                        Ok(false) => {}
                        Ok(true) => break b'c',
                        Err(_) => break b'f',
                    }
                };
                libc::write(ends[1], (&raw const word).cast(), 1);
                libc::_exit(0);
            }
            libc::close(ends[1]);
            Ok(Scanner { pid, read: ends[0] })
        }
    }

    /// What the scanner has said since the last look, without waiting.
    pub fn check(&self) -> Result<(), &'static str> {
        let mut word = 0u8;
        // SAFETY: a one-byte read into a live byte from our own non-blocking pipe.
        let n = unsafe { libc::read(self.read, (&raw mut word).cast(), 1) };
        match n {
            1 if word == b'c' => Err("competing-provider"),
            1 | 0 => Err("provider-scan"),
            _ if std::io::Error::last_os_error().raw_os_error() == Some(libc::EAGAIN) => Ok(()),
            _ => Err("provider-scan"),
        }
    }
}

impl Drop for Scanner {
    fn drop(&mut self) {
        // SAFETY: our own child and descriptor; the wait reaps it so it leaves no zombie.
        unsafe {
            libc::kill(self.pid, libc::SIGKILL);
            libc::waitpid(self.pid, std::ptr::null_mut(), 0);
            libc::close(self.read);
        }
    }
}

/// The heartbeat as `launch.RuntimeSession.write_health` writes it: `{"nonce", "ready",
/// "monotonic"[, "reason"]}`, mode 0600, through `health.tmp` beside it and a rename, so a reader
/// only ever sees a whole file. `monotonic` is CLOCK_MONOTONIC in seconds, the clock Python's
/// `time.monotonic()` reads in the parent.
pub struct Health {
    path: CString,
    temp: CString,
    nonce: String,
    last: Option<Instant>,
}

/// As often as the Python monitor writes it. The reader allows 2 s.
const BEAT: Duration = Duration::from_millis(20);

impl Health {
    /// `path` absolute and not a directory; `nonce` 1..=64 ASCII letters or digits, so it needs
    /// no escaping in JSON.
    pub fn new(path: &str, nonce: &str) -> Option<Self> {
        let p = Path::new(path);
        let parent = p.parent()?;
        let ok_nonce =
            (1..=64).contains(&nonce.len()) && nonce.bytes().all(|b| b.is_ascii_alphanumeric());
        if !p.is_absolute() || p.file_name().is_none() || path.ends_with('/') || !ok_nonce {
            return None;
        }
        let temp = parent.join("health.tmp");
        if temp == p {
            return None;
        }
        Some(Health {
            path: CString::new(path).ok()?,
            temp: CString::new(temp.as_os_str().as_bytes()).ok()?,
            nonce: nonce.into(),
            last: None,
        })
    }

    /// `ready: true`, at most once per beat.
    pub fn beat(&mut self) -> Result<(), &'static str> {
        if self.last.is_some_and(|t| t.elapsed() < BEAT) {
            return Ok(());
        }
        self.write(true, "")?;
        self.last = Some(Instant::now());
        Ok(())
    }

    /// The last word: `ready: false`, with the run's error as the reason when it has one.
    pub fn finish(&mut self, reason: &str) {
        let _ = self.write(false, reason);
    }

    fn write(&self, ready: bool, reason: &str) -> Result<(), &'static str> {
        let mut text = format!(
            "{{\"nonce\": \"{}\", \"ready\": {ready}, \"monotonic\": {:.9}",
            self.nonce,
            crate::monotonic_ns() as f64 / 1e9
        );
        if !reason.is_empty() {
            // Reasons are the bridge's own error tokens: no character needs escaping.
            text.push_str(&format!(", \"reason\": \"{reason}\""));
        }
        text.push('}');
        // SAFETY: plain system calls on paths and a buffer that live across them.
        unsafe {
            let fd = libc::open(
                self.temp.as_ptr(),
                libc::O_CREAT | libc::O_TRUNC | libc::O_WRONLY | libc::O_NOFOLLOW | libc::O_CLOEXEC,
                0o600,
            );
            if fd < 0 {
                return Err("health-write");
            }
            // An older temp file keeps its mode through O_TRUNC; the reader wants exactly 0600.
            let wrote = libc::fchmod(fd, 0o600) == 0
                && libc::write(fd, text.as_ptr().cast(), text.len()) == text.len() as isize;
            libc::close(fd);
            if !wrote || libc::rename(self.temp.as_ptr(), self.path.as_ptr()) != 0 {
                return Err("health-write");
            }
        }
        Ok(())
    }
}
