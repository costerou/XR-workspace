//! Running ffprobe and yt-dlp: one call with a clean environment, a time limit and bounded output, as Python's
//! `subprocess.run(..., capture_output=True, timeout=...)` does. Callers take a `Runner`, so tests pass a stand-in.

use std::io::{self, Read};
use std::os::fd::RawFd;
use std::os::unix::process::{CommandExt, ExitStatusExt};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::mpsc;
use std::time::{Duration, Instant};

/// stderr kept per call: the reasons are read from its end and from words anywhere in it.
const STDERR_KEEP: usize = 256 << 10;

/// One program run.
#[derive(Debug, Clone, PartialEq)]
pub struct Invocation {
    pub argv: Vec<String>,
    /// The whole environment of the child (nothing is inherited).
    pub env: Vec<(String, String)>,
    /// A descriptor the child inherits under the same number (Python `pass_fds`).
    pub pass_fd: Option<RawFd>,
    pub timeout: Duration,
    /// stdout beyond this is not kept (`Output::stdout_over` is set); the program is still read to its end.
    pub max_output: usize,
}

#[derive(Debug, Clone, PartialEq, Default)]
pub struct Output {
    /// The exit code, or minus the signal number (Python's returncode).
    pub returncode: i32,
    pub stdout: Vec<u8>,
    pub stderr: Vec<u8>,
    /// stdout was longer than `max_output`.
    pub stdout_over: bool,
}

#[derive(Debug)]
pub enum RunError {
    /// It did not finish in time (and was killed).
    Timeout,
    /// It could not be started (Python OSError).
    Spawn(io::Error),
}

/// `run` or a test stand-in.
pub type Runner<'a> = &'a dyn Fn(&Invocation) -> Result<Output, RunError>;

/// The fixed program environment of ffprobe and ffmpeg.
pub fn tool_env() -> Vec<(String, String)> {
    vec![("PATH".into(), "/usr/bin:/bin".into()), ("LC_ALL".into(), "C".into())]
}

/// `/usr/bin/<name>` if executable, else the first executable `<name>` on PATH (Python `shutil.which`).
pub fn find_tool(name: &str) -> Option<PathBuf> {
    let fixed = Path::new("/usr/bin").join(name);
    if executable(&fixed) {
        return Some(fixed);
    }
    let path = std::env::var_os("PATH")?;
    std::env::split_paths(&path).map(|dir| dir.join(name)).find(|p| executable(p))
}

pub(crate) fn executable(path: &Path) -> bool {
    use std::os::unix::ffi::OsStrExt;
    let Ok(c) = std::ffi::CString::new(path.as_os_str().as_bytes()) else { return false };
    // SAFETY: `c` is a valid NUL-terminated path.
    let ok = unsafe { libc::access(c.as_ptr(), libc::X_OK) } == 0;
    ok && path.is_file()
}

/// Run the program: stdin is /dev/null, stdout and stderr are captured.
pub fn run(inv: &Invocation) -> Result<Output, RunError> {
    let Some((exe, args)) = inv.argv.split_first() else {
        return Err(RunError::Spawn(io::Error::from(io::ErrorKind::InvalidInput)));
    };
    let mut cmd = Command::new(exe);
    cmd.args(args).env_clear().envs(inv.env.iter().cloned());
    cmd.stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::piped());
    if let Some(fd) = inv.pass_fd {
        let hook = move || {
            // SAFETY: fcntl on a descriptor number has no memory effects.
            let flags = unsafe { libc::fcntl(fd, libc::F_GETFD) };
            if flags < 0 || unsafe { libc::fcntl(fd, libc::F_SETFD, flags & !libc::FD_CLOEXEC) } < 0 {
                return Err(io::Error::last_os_error());
            }
            Ok(())
        };
        // SAFETY: the hook only calls fcntl, which is async-signal-safe.
        unsafe { cmd.pre_exec(hook) };
    }
    let deadline = Instant::now() + inv.timeout;
    let mut child = cmd.spawn().map_err(RunError::Spawn)?;
    let (tx, rx) = mpsc::channel();
    let mut out = child.stdout.take().expect("piped stdout");
    let mut err = child.stderr.take().expect("piped stderr");
    let max = inv.max_output;
    let tx_out = tx.clone();
    std::thread::spawn(move || {
        let (kept, over) = read_head(&mut out, max);
        let _ = tx_out.send(Stream::Out(kept, over));
    });
    std::thread::spawn(move || {
        let _ = tx.send(Stream::Err(read_tail(&mut err, STDERR_KEEP)));
    });
    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break status,
            Ok(None) if Instant::now() < deadline => std::thread::sleep(Duration::from_millis(5)),
            _ => {
                let _ = child.kill();
                let _ = child.wait();
                return Err(RunError::Timeout);
            }
        }
    };
    let mut output = Output {
        returncode: status.code().unwrap_or_else(|| -status.signal().unwrap_or(0)),
        ..Output::default()
    };
    // Both pipes reach their end when the program (and anything it started) is gone.
    for _ in 0..2 {
        let left = deadline.saturating_duration_since(Instant::now());
        match rx.recv_timeout(left) {
            Ok(Stream::Out(data, over)) => (output.stdout, output.stdout_over) = (data, over),
            Ok(Stream::Err(data)) => output.stderr = data,
            Err(_) => return Err(RunError::Timeout),
        }
    }
    Ok(output)
}

enum Stream {
    Out(Vec<u8>, bool),
    Err(Vec<u8>),
}

fn read_head(r: &mut impl Read, max: usize) -> (Vec<u8>, bool) {
    let mut kept = Vec::new();
    let mut over = false;
    let mut buf = vec![0u8; 64 << 10];
    loop {
        match r.read(&mut buf) {
            Ok(0) => break,
            Ok(n) => {
                let room = max.saturating_sub(kept.len());
                kept.extend_from_slice(&buf[..n.min(room)]);
                over |= n > room;
            }
            Err(e) if e.kind() == io::ErrorKind::Interrupted => {}
            Err(_) => break,
        }
    }
    (kept, over)
}

fn read_tail(r: &mut impl Read, keep: usize) -> Vec<u8> {
    let mut kept = Vec::new();
    let mut buf = vec![0u8; 64 << 10];
    loop {
        match r.read(&mut buf) {
            Ok(0) => break,
            Ok(n) => {
                kept.extend_from_slice(&buf[..n]);
                if kept.len() > keep * 2 {
                    kept.drain(..kept.len() - keep);
                }
            }
            Err(e) if e.kind() == io::ErrorKind::Interrupted => {}
            Err(_) => break,
        }
    }
    if kept.len() > keep {
        kept.drain(..kept.len() - keep);
    }
    kept
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sh(script: &str, timeout_ms: u64, max: usize) -> Result<Output, RunError> {
        run(&Invocation {
            argv: vec!["/bin/sh".into(), "-c".into(), script.into()],
            env: tool_env(),
            pass_fd: None,
            timeout: Duration::from_millis(timeout_ms),
            max_output: max,
        })
    }

    #[test]
    fn captures_output_and_the_exit_code() {
        let out = sh("printf hello; printf oops >&2; exit 3", 5000, 100).unwrap();
        assert_eq!((out.returncode, out.stdout.as_slice(), out.stderr.as_slice(), out.stdout_over), (3, &b"hello"[..], &b"oops"[..], false));
        let out = sh("printf 0123456789", 5000, 4).unwrap();
        assert_eq!((out.stdout.as_slice(), out.stdout_over), (&b"0123"[..], true));
        let out = sh("echo \"$PATH $LC_ALL $HOME\"", 5000, 100).unwrap();
        assert_eq!(out.stdout, b"/usr/bin:/bin C \n");
    }

    #[test]
    fn a_slow_program_times_out_and_a_missing_one_does_not_start() {
        let start = Instant::now();
        assert!(matches!(sh("sleep 5", 200, 100), Err(RunError::Timeout)));
        assert!(start.elapsed() < Duration::from_secs(3));
        let missing = Invocation {
            argv: vec!["/nonexistent/ffprobe".into()],
            env: vec![],
            pass_fd: None,
            timeout: Duration::from_secs(1),
            max_output: 1,
        };
        assert!(matches!(run(&missing), Err(RunError::Spawn(_))));
    }

    #[test]
    fn the_passed_descriptor_is_inherited_under_its_number() {
        let file = std::fs::File::open("/etc/hostname").or_else(|_| std::fs::File::open("/etc/passwd")).unwrap();
        let fd = std::os::fd::AsRawFd::as_raw_fd(&file);
        let out = run(&Invocation {
            argv: vec!["/bin/sh".into(), "-c".into(), format!("test -r /proc/self/fd/{fd} && echo yes")],
            env: tool_env(),
            pass_fd: Some(fd),
            timeout: Duration::from_secs(5),
            max_output: 100,
        })
        .unwrap();
        assert_eq!(out.stdout, b"yes\n");
    }
}
