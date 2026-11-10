//! Run units: each run is a transient user unit `viture-xr-<run_id>`, as `tools/xr_controller/__main__.py`
//! (`Units`, `Host.spawn`) drives them through `systemctl` and `systemd-run`. Same unit names, same arguments,
//! same timeouts, so either controller can see, stop and adopt a run the other started.

use std::ffi::{OsStr, OsString};
use std::io::{self, Read};
use std::os::fd::AsRawFd;
use std::os::unix::net::UnixStream;
use std::path::{Path, PathBuf};
use std::process::{Command, ExitStatus, Stdio};
use std::time::{Duration, Instant};

use crate::private_file::{private_dir, token};

/// What `systemctl is-active` prints for a unit that is alive; it exits 0 when any unit asked about is one of these.
pub const ACTIVE_STATES: [&str; 3] = ["active", "reloading", "refreshing"];
const OTHER_STATES: [&str; 5] = [
    "inactive",
    "failed",
    "activating",
    "deactivating",
    "maintenance",
];

/// `systemctl` calls get 2 seconds, `systemd-run` 5, as in the Python controller.
const SYSTEMCTL_TIMEOUT: Duration = Duration::from_secs(2);
const SPAWN_TIMEOUT: Duration = Duration::from_secs(5);
/// The control-socket check reads and writes nothing, and gives up as quickly as the Python one.
const CONTROL_TIMEOUT: Duration = Duration::from_millis(250);

/// Environment a run inherits from the controller when the controller has it.
const PASSED_ENV: [&str; 3] = [
    "XDG_CONFIG_HOME",
    "XDG_STATE_HOME",
    "HYPRLAND_INSTANCE_SIGNATURE",
];

pub fn unit_name(run_id: &str) -> io::Result<String> {
    Ok(format!("viture-xr-{}", token(run_id)?))
}

/// The run unit's command for the Rust supervisor (`xr-workspace supervisor`, runner.rs): the same arguments as the
/// Python runner's `isolated('tools.xr_controller.runner', '--consent', C, '--state-dir', S)`, for [`Units::spawn`].
/// Not the default: the controller starts the Python runner until a run is switched to this one.
pub fn supervisor_argv(exe: &Path, consent: &Path, state_dir: &Path) -> Vec<OsString> {
    vec![
        exe.into(),
        "supervisor".into(),
        "--consent".into(),
        consent.into(),
        "--state-dir".into(),
        state_dir.into(),
    ]
}

/// The user's systemd, through its two command-line tools.
pub struct Units {
    pub systemctl: PathBuf,
    pub systemd_run: PathBuf,
}

impl Default for Units {
    fn default() -> Self {
        Units {
            systemctl: "systemctl".into(),
            systemd_run: "systemd-run".into(),
        }
    }
}

impl Units {
    /// Is the run's unit alive (`systemctl --user is-active --quiet`)?
    pub fn alive(&self, run_id: &str) -> io::Result<bool> {
        let unit = unit_name(run_id)?;
        let done = run(
            Command::new(&self.systemctl).args(["--user", "is-active", "--quiet", &unit]),
            SYSTEMCTL_TIMEOUT,
        )?;
        Ok(done.status.success())
    }

    /// `alive` for each run, with one `systemctl` call for all of them (it prints one state per line). Any answer
    /// that is not exactly one known state per unit, in agreement with the exit status, falls back to one call each.
    pub fn alive_many(&self, run_ids: &[String]) -> io::Result<Vec<bool>> {
        if run_ids.len() < 2 {
            return run_ids.iter().map(|r| self.alive(r)).collect();
        }
        let units = run_ids
            .iter()
            .map(|r| unit_name(r))
            .collect::<io::Result<Vec<_>>>()?;
        let done = run(
            Command::new(&self.systemctl)
                .args(["--user", "is-active"])
                .args(&units),
            SYSTEMCTL_TIMEOUT,
        )?;
        let text = String::from_utf8_lossy(&done.stdout);
        let states: Vec<&str> = text.lines().collect();
        let known = states
            .iter()
            .all(|s| ACTIVE_STATES.contains(s) || OTHER_STATES.contains(s));
        let any_active = states.iter().any(|s| ACTIVE_STATES.contains(s));
        if states.len() != units.len() || !known || done.status.success() != any_active {
            return run_ids.iter().map(|r| self.alive(r)).collect();
        }
        Ok(states.iter().map(|s| ACTIVE_STATES.contains(s)).collect())
    }

    /// Ask the run to end. SIGINT goes to the main process only: the runner's teardown, not its renderer child.
    pub fn stop(&self, run_id: &str) -> io::Result<()> {
        let unit = unit_name(run_id)?;
        let args = [
            "--user",
            "kill",
            "--kill-whom=main",
            "--signal=SIGINT",
            &unit,
        ];
        checked(
            run(Command::new(&self.systemctl).args(args), SYSTEMCTL_TIMEOUT)?,
            "systemctl kill",
        )
    }

    /// Start `argv` as the run's transient unit, collected when it ends. The unit gets the controller's runtime and
    /// data folders and, when set, its config, state and Hyprland instance (as `Host.spawn`).
    pub fn spawn(
        &self,
        run_id: &str,
        workdir: &Path,
        runtime: &Path,
        data_home: &Path,
        argv: &[OsString],
    ) -> io::Result<()> {
        let mut cmd = Command::new(&self.systemd_run);
        cmd.arg("--user")
            .arg(format!("--unit={}", unit_name(run_id)?))
            .arg("--collect");
        cmd.arg(concat("--property=WorkingDirectory=", workdir.as_os_str()));
        cmd.arg(concat("--setenv=XDG_RUNTIME_DIR=", runtime.as_os_str()));
        cmd.arg(concat("--setenv=XDG_DATA_HOME=", data_home.as_os_str()));
        for key in PASSED_ENV {
            if let Some(value) = std::env::var_os(key) {
                cmd.arg(concat(&format!("--setenv={key}="), &value));
            }
        }
        cmd.args(argv);
        checked(run(&mut cmd, SPAWN_TIMEOUT)?, "systemd-run")
    }
}

/// Does the run have a renderer control listener (`Units.control(run_id, session, None)`)? A connection to
/// `<runtime>/xr-workspace-spike/<run_id>/control.sock` in a private folder, from a peer of this user, proves it.
pub fn control_listening(runtime: &Path, run_id: &str) -> bool {
    let check = || -> io::Result<()> {
        let dir = private_dir(&runtime.join("xr-workspace-spike").join(token(run_id)?))?;
        let conn = UnixStream::connect(format!("/proc/self/fd/{}/control.sock", dir.as_raw_fd()))?;
        conn.set_read_timeout(Some(CONTROL_TIMEOUT))?;
        // SAFETY: getuid has no preconditions and cannot fail.
        if peer_uid(&conn)? != unsafe { libc::getuid() } {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                "wrong peer UID",
            ));
        }
        Ok(())
    };
    check().is_ok()
}

fn peer_uid(conn: &UnixStream) -> io::Result<libc::uid_t> {
    let mut cred = libc::ucred {
        pid: 0,
        uid: 0,
        gid: 0,
    };
    let mut len = std::mem::size_of::<libc::ucred>() as libc::socklen_t;
    // SAFETY: `cred` and `len` are valid for writes of the sizes passed, and the descriptor is a live socket.
    let rc = unsafe {
        libc::getsockopt(
            conn.as_raw_fd(),
            libc::SOL_SOCKET,
            libc::SO_PEERCRED,
            (&raw mut cred).cast(),
            &mut len,
        )
    };
    if rc != 0 {
        Err(io::Error::last_os_error())
    } else {
        Ok(cred.uid)
    }
}

fn concat(prefix: &str, value: &OsStr) -> OsString {
    let mut out = OsString::from(prefix);
    out.push(value);
    out
}

struct Done {
    status: ExitStatus,
    stdout: Vec<u8>,
}

fn checked(done: Done, what: &str) -> io::Result<()> {
    if done.status.success() {
        Ok(())
    } else {
        Err(io::Error::other(format!("{what} failed: {}", done.status)))
    }
}

/// The command run to completion with no input, its stdout kept (systemctl's few lines fit the pipe) and its
/// stderr left to the controller's journal. Past `timeout` it is killed and this fails, like `subprocess.run`.
fn run(cmd: &mut Command, timeout: Duration) -> io::Result<Done> {
    let mut child = cmd.stdin(Stdio::null()).stdout(Stdio::piped()).spawn()?;
    let deadline = Instant::now() + timeout;
    let status = loop {
        if let Some(status) = child.try_wait()? {
            break status;
        }
        if Instant::now() >= deadline {
            let _ = child.kill();
            let _ = child.wait();
            return Err(io::Error::new(io::ErrorKind::TimedOut, "command timed out"));
        }
        std::thread::sleep(Duration::from_millis(5));
    };
    let mut stdout = Vec::new();
    child
        .stdout
        .take()
        .expect("piped")
        .read_to_end(&mut stdout)?;
    Ok(Done { status, stdout })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testdir::TestDir;
    use std::os::unix::fs::PermissionsExt;

    /// A stand-in `systemctl` that logs its arguments and answers from a script.
    fn fake(dir: &TestDir, body: &str) -> Units {
        let path = dir.path().join("systemctl");
        std::fs::write(
            &path,
            format!(
                "#!/bin/sh\necho \"$@\" >> '{}'\n{body}\n",
                dir.path().join("calls").display()
            ),
        )
        .unwrap();
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o700)).unwrap();
        Units {
            systemctl: path.clone(),
            systemd_run: path,
        }
    }

    fn calls(dir: &TestDir) -> Vec<String> {
        std::fs::read_to_string(dir.path().join("calls"))
            .unwrap_or_default()
            .lines()
            .map(str::to_owned)
            .collect()
    }

    fn ids(v: &[&str]) -> Vec<String> {
        v.iter().map(|s| s.to_string()).collect()
    }

    #[test]
    fn alive_asks_is_active_quietly() {
        let dir = TestDir::new("units");
        let units = fake(&dir, "exit 3");
        assert!(!units.alive("r1").unwrap());
        assert_eq!(calls(&dir), ["--user is-active --quiet viture-xr-r1"]);
        assert!(
            units.alive("../x").is_err(),
            "a run id is a token before it names a unit"
        );
    }

    #[test]
    fn alive_many_reads_one_state_per_unit() {
        let dir = TestDir::new("units");
        let units = fake(&dir, "printf 'active\\ninactive\\nreloading\\n'; exit 0");
        assert_eq!(
            units.alive_many(&ids(&["a", "b", "c"])).unwrap(),
            [true, false, true]
        );
        assert_eq!(calls(&dir).len(), 1, "one call for all of them");
    }

    #[test]
    fn alive_many_falls_back_on_an_answer_it_does_not_trust() {
        // Too few lines, an unknown state, and an exit status that disagrees with the states: each costs one call per run.
        for body in [
            "printf 'active\\n'; exit 0",
            "printf 'active\\nweird\\n'; exit 0",
            "printf 'inactive\\ninactive\\n'; exit 0",
        ] {
            let dir = TestDir::new("units");
            let units = fake(&dir, body);
            units.alive_many(&ids(&["a", "b"])).unwrap();
            let c = calls(&dir);
            assert_eq!(c.len(), 3, "{body}: {c:?}");
            assert_eq!(
                c[1..],
                [
                    "--user is-active --quiet viture-xr-a",
                    "--user is-active --quiet viture-xr-b"
                ]
            );
        }
    }

    #[test]
    fn stop_interrupts_the_main_process_and_reports_failure() {
        let dir = TestDir::new("units");
        fake(&dir, "exit 0").stop("r1").unwrap();
        assert_eq!(
            calls(&dir),
            ["--user kill --kill-whom=main --signal=SIGINT viture-xr-r1"]
        );
        assert!(fake(&dir, "exit 1").stop("r1").is_err());
    }

    #[test]
    fn spawn_runs_the_rust_supervisor_as_the_run_unit() {
        let dir = TestDir::new("units");
        let units = fake(&dir, "exit 0");
        let argv = supervisor_argv(
            Path::new("/data/xr-workspace"),
            Path::new("/state/consents/r1.json"),
            Path::new("/state"),
        );
        units
            .spawn("r1", Path::new("/repo"), Path::new("/run/user/1"), Path::new("/data"), &argv)
            .unwrap();
        let line = &calls(&dir)[0];
        assert!(line.starts_with("--user --unit=viture-xr-r1 --collect "), "{line}");
        assert!(
            line.ends_with(" /data/xr-workspace supervisor --consent /state/consents/r1.json --state-dir /state"),
            "{line}"
        );
    }

    #[test]
    fn spawn_builds_the_python_controllers_command() {
        let dir = TestDir::new("units");
        let units = fake(&dir, "exit 0");
        let argv = [OsString::from("/usr/bin/python3"), OsString::from("-I")];
        units
            .spawn(
                "r1",
                Path::new("/repo"),
                Path::new("/run/user/1"),
                Path::new("/data"),
                &argv,
            )
            .unwrap();
        let line = &calls(&dir)[0];
        assert!(line.starts_with(
            "--user --unit=viture-xr-r1 --collect --property=WorkingDirectory=/repo --setenv=XDG_RUNTIME_DIR=/run/user/1 --setenv=XDG_DATA_HOME=/data "
        ), "{line}");
        // Between them, the passed-through environment the test process happens to have; the run's argv comes last.
        assert!(line.ends_with(" /usr/bin/python3 -I"), "{line}");
        assert!(
            fake(&dir, "exit 1")
                .spawn(
                    "r1",
                    Path::new("/r"),
                    Path::new("/r"),
                    Path::new("/r"),
                    &argv
                )
                .is_err()
        );
    }

    #[test]
    fn a_hung_systemctl_times_out() {
        let dir = TestDir::new("units");
        let start = Instant::now();
        let err = fake(&dir, "exec sleep 10").alive("r1").unwrap_err();
        assert_eq!(err.kind(), io::ErrorKind::TimedOut);
        assert!(start.elapsed() < Duration::from_secs(4));
    }

    #[test]
    fn control_listener_needs_a_private_folder_and_a_listener() {
        let dir = TestDir::new("units");
        let run_dir = dir.path().join("xr-workspace-spike").join("r1");
        std::fs::create_dir_all(&run_dir).unwrap();
        std::fs::set_permissions(&run_dir, std::fs::Permissions::from_mode(0o700)).unwrap();
        assert!(!control_listening(dir.path(), "r1"), "no socket");
        let _listener =
            std::os::unix::net::UnixListener::bind(run_dir.join("control.sock")).unwrap();
        assert!(control_listening(dir.path(), "r1"));
        std::fs::set_permissions(&run_dir, std::fs::Permissions::from_mode(0o750)).unwrap();
        assert!(!control_listening(dir.path(), "r1"), "folder not private");
    }
}
