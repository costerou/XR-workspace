//! What the incident monitor reads from the host: `HostSources` in `tools/xr_controller/incident_monitor.py`.
//!
//! All of it is read-only and bounded by a timeout. A source that cannot answer gives an empty answer; one that fails
//! outright gives an `Err`, which the monitor logs once and treats as Python's `_safe` default.

use std::collections::BTreeSet;
use std::fs::File;
use std::io::{self, Read, Seek, SeekFrom};
use std::os::unix::fs::OpenOptionsExt;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::LazyLock;
use std::time::{Duration, Instant};

use regex::Regex;
use serde_json::{Value, json};

use super::detect::signal_name;
use super::py::{as_int, as_str, decode, get, splitlines};
use crate::private_file::{DEFAULT_LIMIT, read_private_json};

pub type Answer<T> = Result<T, String>;

/// The host, as the monitor sees it. `HostSources` reads the real one; tests and the parity corpus fake it.
pub trait Sources {
    fn boot_id(&mut self) -> Answer<Option<String>>;
    /// The run's consent file; an object, or an error the monitor turns into "no consent".
    fn consent(&mut self, run_id: &str) -> Answer<Value>;
    /// `{run_id}.result.json` when it is this run's, else null.
    fn result(&mut self, run_id: &str) -> Answer<Value>;
    /// What the last Recover left: `{remaining, reasons}`, or null.
    fn recovery(&mut self, run_id: &str) -> Answer<Value>;
    fn kernel_start(&mut self) -> Answer<Option<String>>;
    fn kernel_read(&mut self, cursor: &str) -> Answer<(Vec<String>, String)>;
    fn kernel_around(&mut self, first: f64, last: f64) -> Answer<Vec<String>>;
    fn journal(&mut self, unit: &str, lines: usize) -> Answer<Vec<String>>;
    fn driver_start(&mut self) -> Answer<u64>;
    fn driver_read(&mut self, offset: u64) -> Answer<(String, u64)>;
    fn driver_tail(&mut self, lines: usize) -> Answer<Vec<String>>;
    fn drm(&mut self, output: &str) -> Answer<Option<String>>;
    fn usb_ports(&mut self) -> Answer<Option<BTreeSet<String>>>;
    fn hyprland_pid(&mut self, instance: Option<&str>) -> Answer<Option<i64>>;
    fn alive(&mut self, pid: Option<i64>) -> Answer<Option<bool>>;
    fn driver_alive(&mut self) -> Answer<bool>;
    fn tracking_driver(&mut self) -> Answer<Value>;
    fn core_dumps(&mut self, since: f64, exes: &[String], pids: &[i64]) -> Answer<Vec<Value>>;
    fn source(&mut self) -> Answer<Value>;
}

static NOISE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"split lock|bus_lock|\bUFW\b").unwrap());
static WANTED: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)\bxhci|\[drm\]|\bdrm\b|\bamdgpu\b|\b35ca\b|\bPM: suspend").unwrap()
});
static PORT: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\b(\d+-\d+(?:\.\d+)*)[:\s]").unwrap());

/// The lines about the glasses' USB tree and the GPU. Not other USB devices (their names are private), not the firewall.
pub fn relevant_kernel(lines: &[String], ports: &BTreeSet<String>) -> Vec<String> {
    let roots: BTreeSet<&str> = ports
        .iter()
        .map(|p| p.split('.').next().unwrap_or(""))
        .collect();
    lines
        .iter()
        .filter(|line| !NOISE.is_match(line))
        .filter(|line| {
            let tail = line
                .split_once(" kernel: ")
                .map_or(line.as_str(), |(_, t)| t);
            let ours = PORT
                .captures(tail)
                .is_some_and(|m| roots.contains(m[1].split('.').next().unwrap_or("")));
            ours || WANTED.is_match(line)
        })
        .cloned()
        .collect()
}

/// The crashing thread's frames from `coredumpctl info`. The rest of that output (command line, paths) stays out.
pub fn stack_frames(info: &str) -> Vec<String> {
    let (mut frames, mut inside) = (Vec::new(), false);
    for line in splitlines(info) {
        let text = line.trim();
        if text.starts_with("Stack trace of thread") {
            if inside {
                break;
            }
            inside = true;
        } else if inside && text.starts_with('#') {
            frames.push(text.to_owned());
        } else if inside && !frames.is_empty() {
            break;
        }
    }
    frames
}

/// A finished command: its exit code (None when a signal ended it) and its output, decoded as Python's `errors='replace'`.
pub struct Done {
    pub code: Option<i32>,
    pub stdout: String,
}

/// Runs a command: argv, timeout, extra environment. None where Python catches OSError or SubprocessError.
pub type Runner = Box<dyn FnMut(&[String], Duration, &[(&str, &str)]) -> Option<Done> + Send>;

/// `subprocess.run(argv, stdin=DEVNULL, stdout=PIPE, stderr=DEVNULL, timeout=...)`.
pub fn run_bounded(argv: &[String], timeout: Duration, env: &[(&str, &str)]) -> Option<Done> {
    let (program, args) = argv.split_first()?;
    let mut child = Command::new(program)
        .args(args)
        .envs(env.iter().copied())
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .ok()?;
    let mut stdout = child.stdout.take()?;
    // The pipe is read beside the wait, or a big output would block the child before it can exit.
    let reader = std::thread::spawn(move || {
        let mut data = Vec::new();
        stdout.read_to_end(&mut data).map(|_| data)
    });
    let deadline = Instant::now() + timeout;
    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break status,
            Ok(None) if Instant::now() < deadline => std::thread::sleep(Duration::from_millis(10)),
            _ => {
                let _ = child.kill();
                let _ = child.wait();
                return None;
            }
        }
    };
    let data = reader.join().ok()?.ok()?;
    Some(Done {
        code: status.code(),
        stdout: decode(&data),
    })
}

fn argv(parts: &[&str]) -> Vec<String> {
    parts.iter().map(|p| (*p).to_owned()).collect()
}

fn not_marker(lines: Vec<String>) -> Vec<String> {
    lines
        .into_iter()
        .filter(|line| !line.starts_with("-- "))
        .collect()
}

fn cursor(lines: &[String]) -> Option<String> {
    lines
        .iter()
        .rev()
        .find_map(|line| line.strip_prefix("-- cursor: ").map(str::to_owned))
}

fn missing(error: &io::Error) -> bool {
    error.kind() == io::ErrorKind::NotFound
}

/// The kernel's state field and start tick from `/proc/<pid>/stat`, past the command name.
fn stat_fields(proc_root: &Path, pid: i64) -> Option<Vec<String>> {
    let text = std::fs::read_to_string(proc_root.join(pid.to_string()).join("stat")).ok()?;
    let (_, rest) = text.rsplit_once(')')?;
    Some(rest.split_whitespace().map(str::to_owned).collect())
}

pub struct HostSources {
    pub runtime: PathBuf,
    pub state_dir: PathBuf,
    pub repo: PathBuf,
    pub sys_root: PathBuf,
    pub proc_root: PathBuf,
    /// A fixed driver log; else whichever driver tracking.json names.
    pub driver_log: Option<PathBuf>,
    /// `tracking.LUMA_LOG` and `tracking.DRIVER_LOG`.
    pub luma_log: PathBuf,
    pub xrdriver_log: PathBuf,
    reading: Option<PathBuf>,
    run: Runner,
}

impl HostSources {
    pub fn new(runtime: &Path, state_dir: &Path) -> HostSources {
        let home = PathBuf::from(std::env::var_os("HOME").unwrap_or_default());
        let state_home = std::env::var_os("XDG_STATE_HOME")
            .filter(|v| !v.is_empty())
            .map_or_else(|| home.join(".local/state"), PathBuf::from);
        HostSources {
            runtime: runtime.into(),
            state_dir: state_dir.into(),
            // The checkout this binary was built from, as Python names the checkout it runs from.
            repo: Path::new(env!("CARGO_MANIFEST_DIR")).join("../../.."),
            sys_root: "/sys".into(),
            proc_root: "/proc".into(),
            driver_log: None,
            luma_log: state_home.join("viture-workspace/luma-driver.log"),
            xrdriver_log: home.join(".local/state/xr_driver/driver.log"),
            reading: None,
            run: Box::new(run_bounded),
        }
    }

    pub fn with_runner(mut self, run: Runner) -> HostSources {
        self.run = run;
        self
    }

    fn exec(&mut self, parts: &[&str], timeout: u64, env: &[(&str, &str)]) -> Option<Done> {
        (self.run)(&argv(parts), Duration::from_secs(timeout), env)
    }

    fn lines(&mut self, parts: &[&str]) -> Vec<String> {
        self.exec(parts, 5, &[])
            .map_or_else(Vec::new, |done| not_marker(splitlines(&done.stdout)))
    }

    /// `tracking._read_state`, without the folder it creates: the monitor only reads.
    fn tracking_state(&self) -> Option<Value> {
        let raw = std::fs::read(self.runtime.join("viture-workspace/tracking.json")).ok()?;
        let data: Value = serde_json::from_slice(&raw).ok()?;
        (as_int(get(&data, "pid")).is_some() && get(&data, "start_tick").is_string())
            .then_some(data)
    }

    /// `tracking._ours`: the recorded process is still the driver we started, and not a corpse.
    fn ours(&self, data: &Value) -> bool {
        let Some(pid) = as_int(get(data, "pid")) else {
            return false;
        };
        let Some(fields) = stat_fields(&self.proc_root, pid) else {
            return false;
        };
        fields.get(19).map(String::as_str) == as_str(get(data, "start_tick"))
            && fields.first().map(String::as_str) != Some("Z")
    }

    fn luma(data: &Value) -> bool {
        as_str(get(data, "driver")) == Some("luma")
    }

    fn driver_path(&self) -> PathBuf {
        if let Some(path) = &self.driver_log {
            return path.clone();
        }
        match self.tracking_state() {
            Some(data) if Self::luma(&data) => self.luma_log.clone(),
            _ => self.xrdriver_log.clone(),
        }
    }

    fn driver_file(&self) -> Option<File> {
        File::options()
            .read(true)
            .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC)
            .open(self.driver_path())
            .ok()
    }

    fn private(&self, name: String) -> io::Result<Option<Value>> {
        match read_private_json(&self.state_dir.join(name), DEFAULT_LIMIT) {
            Err(e) if missing(&e) => Ok(None),
            other => other.map(Some),
        }
    }
}

fn text(error: io::Error) -> String {
    error.to_string()
}

impl Sources for HostSources {
    fn boot_id(&mut self) -> Answer<Option<String>> {
        Ok(
            std::fs::read_to_string(self.proc_root.join("sys/kernel/random/boot_id"))
                .ok()
                .map(|t| t.trim().to_owned()),
        )
    }

    fn consent(&mut self, run_id: &str) -> Answer<Value> {
        read_private_json(
            &self.state_dir.join(format!("{run_id}.json")),
            DEFAULT_LIMIT,
        )
        .map_err(text)
    }

    fn result(&mut self, run_id: &str) -> Answer<Value> {
        let result = self
            .private(format!("{run_id}.result.json"))
            .map_err(text)?
            .unwrap_or_default();
        Ok(
            if as_str(get(&result, "run_id")) == Some(run_id) && result.is_object() {
                result
            } else {
                Value::Null
            },
        )
    }

    fn recovery(&mut self, run_id: &str) -> Answer<Value> {
        let Some(record) = self
            .private(format!("{run_id}.recovery.json"))
            .map_err(text)?
        else {
            return Ok(Value::Null);
        };
        if !record.is_object() || as_str(get(&record, "run_id")) != Some(run_id) {
            return Ok(Value::Null);
        }
        let texts = |key: &str| -> Vec<Value> {
            get(&record, key).as_array().map_or_else(Vec::new, |items| {
                items.iter().map(|v| super::py::py_str(v).into()).collect()
            })
        };
        Ok(json!({"remaining": texts("remaining"), "reasons": texts("reasons")}))
    }

    fn kernel_start(&mut self) -> Answer<Option<String>> {
        Ok(self
            .exec(
                &["journalctl", "-k", "-n", "0", "--show-cursor", "--no-pager"],
                5,
                &[],
            )
            .and_then(|d| cursor(&splitlines(&d.stdout))))
    }

    fn kernel_read(&mut self, after: &str) -> Answer<(Vec<String>, String)> {
        let flag = format!("--after-cursor={after}");
        let parts = [
            "journalctl",
            "-k",
            &flag,
            "--show-cursor",
            "--no-pager",
            "-o",
            "short-iso",
            "-n",
            "2000",
        ];
        let Some(done) = self.exec(&parts, 5, &[]) else {
            return Ok((Vec::new(), after.to_owned()));
        };
        let lines = splitlines(&done.stdout);
        let next = cursor(&lines).unwrap_or_else(|| after.to_owned());
        Ok((not_marker(lines), next))
    }

    fn kernel_around(&mut self, first: f64, last: f64) -> Answer<Vec<String>> {
        let (since, until) = (
            format!("--since=@{}", first.trunc() as i64),
            format!("--until=@{}", last.ceil() as i64),
        );
        Ok(self.lines(&[
            "journalctl",
            "-k",
            &since,
            &until,
            "--no-pager",
            "-o",
            "short-iso",
            "-n",
            "4000",
        ]))
    }

    fn journal(&mut self, unit: &str, lines: usize) -> Answer<Vec<String>> {
        let n = lines.to_string();
        Ok(self.lines(&[
            "journalctl",
            "--user",
            "-u",
            unit,
            "-n",
            &n,
            "--no-pager",
            "-o",
            "short-iso",
        ]))
    }

    fn driver_start(&mut self) -> Answer<u64> {
        self.reading = Some(self.driver_path());
        Ok(self
            .driver_file()
            .and_then(|f| f.metadata().ok())
            .map_or(0, |m| m.len()))
    }

    fn driver_read(&mut self, offset: u64) -> Answer<(String, u64)> {
        let Some(mut stream) = self.driver_file() else {
            return Ok((String::new(), 0));
        };
        let size = stream.metadata().map_err(text)?.len();
        let path = self.driver_path();
        let mut offset = offset;
        if self
            .reading
            .as_ref()
            .is_some_and(|reading| *reading != path)
        {
            offset = size; // the start changed driver: its log, from here
        }
        self.reading = Some(path);
        if size < offset {
            offset = 0; // the driver started a new log
        }
        stream.seek(SeekFrom::Start(offset)).map_err(text)?;
        let mut data = Vec::new();
        stream.take(1 << 20).read_to_end(&mut data).map_err(text)?;
        Ok((decode(&data), offset + data.len() as u64))
    }

    fn driver_tail(&mut self, lines: usize) -> Answer<Vec<String>> {
        let Some(mut stream) = self.driver_file() else {
            return Ok(Vec::new());
        };
        let size = stream.metadata().map_err(text)?.len();
        stream
            .seek(SeekFrom::Start(size.saturating_sub(1 << 17)))
            .map_err(text)?;
        let mut data = Vec::new();
        stream.read_to_end(&mut data).map_err(text)?;
        let all = splitlines(&decode(&data));
        Ok(all[all.len().saturating_sub(lines)..].to_vec())
    }

    fn drm(&mut self, output: &str) -> Answer<Option<String>> {
        let Ok(entries) = std::fs::read_dir(self.sys_root.join("class/drm")) else {
            return Ok(None);
        };
        let suffix = format!("-{output}");
        let mut states = Vec::new();
        for entry in entries.flatten() {
            let name = entry.file_name().to_string_lossy().into_owned();
            if name.starts_with("card") && name.ends_with(&suffix) && name.len() > 4 + suffix.len()
            {
                if let Ok(state) = std::fs::read_to_string(entry.path().join("status")) {
                    states.push(state.trim().to_owned());
                }
            }
        }
        if states.is_empty() {
            return Ok(None);
        }
        Ok(Some(
            if states.iter().any(|s| s == "connected") {
                "connected"
            } else {
                "disconnected"
            }
            .into(),
        ))
    }

    fn usb_ports(&mut self) -> Answer<Option<BTreeSet<String>>> {
        let Ok(entries) = std::fs::read_dir(self.sys_root.join("bus/usb/devices")) else {
            return Ok(None);
        };
        let mut ports = BTreeSet::new();
        for entry in entries {
            let Ok(entry) = entry else { return Ok(None) };
            let name = entry.file_name().to_string_lossy().into_owned();
            if name.contains(':') {
                continue; // an interface, not a device
            }
            if std::fs::read_to_string(entry.path().join("idVendor"))
                .is_ok_and(|v| v.trim().to_lowercase() == "35ca")
            {
                ports.insert(name);
            }
        }
        Ok(Some(ports))
    }

    fn hyprland_pid(&mut self, instance: Option<&str>) -> Answer<Option<i64>> {
        let Some(instance) = instance.filter(|i| !i.is_empty()) else {
            return Ok(None);
        };
        let lock = std::fs::read_to_string(
            self.runtime
                .join("hypr")
                .join(instance)
                .join("hyprland.lock"),
        );
        Ok(lock.ok().and_then(|text| {
            text.split_whitespace()
                .next()
                .and_then(|pid| pid.parse().ok())
        }))
    }

    fn alive(&mut self, pid: Option<i64>) -> Answer<Option<bool>> {
        let Some(pid) = pid else { return Ok(None) };
        let comm = std::fs::read_to_string(self.proc_root.join(pid.to_string()).join("comm"));
        Ok(Some(comm.is_ok_and(|c| c.trim() == "Hyprland")))
    }

    fn driver_alive(&mut self) -> Answer<bool> {
        Ok(self.tracking_state().is_some_and(|data| self.ours(&data)))
    }

    fn tracking_driver(&mut self) -> Answer<Value> {
        Ok(match self.tracking_state() {
            Some(data) if self.ours(&data) => (if Self::luma(&data) {
                "luma"
            } else {
                "xrdriver"
            })
            .into(),
            _ => Value::Null,
        })
    }

    fn core_dumps(&mut self, since: f64, exes: &[String], pids: &[i64]) -> Answer<Vec<Value>> {
        let flag = format!("--since=@{}", since.trunc() as i64);
        let Some(done) = self.exec(
            &["coredumpctl", "--no-pager", "--json=short", &flag, "list"],
            10,
            &[],
        ) else {
            return Ok(Vec::new());
        };
        if done.code != Some(0) {
            return Ok(Vec::new());
        }
        let Ok(entries) = serde_json::from_str::<Value>(&done.stdout) else {
            return Ok(Vec::new());
        };
        let mut out = Vec::new();
        for entry in entries
            .as_array()
            .into_iter()
            .flatten()
            .filter(|e| e.is_object())
        {
            let (exe, pid, sig) = (get(entry, "exe"), get(entry, "pid"), get(entry, "sig"));
            let ours = as_str(exe).is_some_and(|e| exes.iter().any(|x| x == e))
                || as_int(pid).is_some_and(|p| pids.contains(&p));
            if !ours {
                continue;
            }
            let pid_text = super::py::py_str(pid);
            let info = self.exec(&["coredumpctl", "--no-pager", "info", &pid_text], 10, &[]);
            let signal = as_int(sig)
                .filter(|n| *n != 0)
                .map_or(Value::Null, |n| signal_name(n).into());
            out.push(json!({
                "exe": super::report::file_name(&super::py::py_str(exe)),
                "pid": pid,
                "signal": signal,
                "frames": info.map_or_else(Vec::new, |i| stack_frames(&i.stdout)),
            }));
        }
        Ok(out)
    }

    fn source(&mut self) -> Answer<Value> {
        let repo = self.repo.to_string_lossy().into_owned();
        let env = [("GIT_OPTIONAL_LOCKS", "0")]; // never touch the index of a checkout someone may be working in
        let sha = self.exec(&["git", "-C", &repo, "rev-parse", "HEAD"], 5, &env);
        let dirty = self.exec(&["git", "-C", &repo, "status", "--porcelain"], 5, &env);
        let sha = sha.map_or(Value::Null, |d| {
            let s = d.stdout.trim();
            if s.is_empty() { Value::Null } else { s.into() }
        });
        Ok(json!({"sha": sha, "dirty": dirty.map(|d| !d.stdout.trim().is_empty())}))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testdir::TestDir;
    use std::sync::{Arc, Mutex};

    #[test]
    fn kernel_lines_about_the_glasses_and_the_gpu_only() {
        let lines: Vec<String> = [
            "t h kernel: usb 5-2.1: USB disconnect, device number 5",
            "t h kernel: usb 1-4: USB disconnect, device number 3",
            "t h kernel: amdgpu 0000:03:00.0: ring gfx timeout",
            "t h kernel: x86/split lock detection: #AC: xhci",
            "t h kernel: [UFW BLOCK] IN=wlan0",
        ]
        .map(String::from)
        .to_vec();
        let ports = BTreeSet::from(["5-2.3".to_owned()]);
        assert_eq!(
            relevant_kernel(&lines, &ports),
            lines[..1]
                .iter()
                .chain(&lines[2..3])
                .cloned()
                .collect::<Vec<_>>()
        );
    }

    #[test]
    fn frames_of_the_crashing_thread() {
        let info = "PID: 7\n  Command Line: secret\n  Stack trace of thread 7:\n  #0  0x00007f libc.so (raise + 0x1)\n  #1  0x00007f xr-renderer (main + 0x2)\n\n  Stack trace of thread 8:\n  #0 0x1 other\n";
        assert_eq!(
            stack_frames(info),
            [
                "#0  0x00007f libc.so (raise + 0x1)",
                "#1  0x00007f xr-renderer (main + 0x2)"
            ]
        );
    }

    fn write(path: &Path, text: &str) {
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, text).unwrap();
    }

    #[test]
    fn reads_a_fake_host() {
        let dir = TestDir::new("incident-sources");
        let root = dir.path();
        let (sys, proc_root, runtime) = (root.join("sys"), root.join("proc"), root.join("run"));
        write(&sys.join("bus/usb/devices/5-2.1/idVendor"), "35CA\n");
        write(&sys.join("bus/usb/devices/5-2.1:1.0/idVendor"), "35ca\n");
        write(&sys.join("bus/usb/devices/1-4/idVendor"), "046d\n");
        write(&sys.join("class/drm/card1-DP-1/status"), "disconnected\n");
        write(&sys.join("class/drm/card0-DP-1/status"), "connected\n");
        write(
            &runtime.join("hypr/inst/hyprland.lock"),
            "1690\nwayland-1\n",
        );
        write(&proc_root.join("1690/comm"), "Hyprland\n");
        write(&proc_root.join("sys/kernel/random/boot_id"), "boot-1\n");
        write(
            &proc_root.join("77/stat"),
            "77 (xr luma) S 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 4242 20\n",
        );
        write(
            &runtime.join("viture-workspace/tracking.json"),
            r#"{"pid": 77, "start_tick": "4242", "driver": "luma"}"#,
        );
        let luma = root.join("luma.log");
        write(&luma, "luma: one\n");
        let calls = Arc::new(Mutex::new(Vec::new()));
        let seen = calls.clone();
        let mut host = HostSources::new(&runtime, root).with_runner(Box::new(move |argv, _, _| {
            seen.lock().unwrap().push(argv.join(" "));
            let stdout = match argv[0].as_str() {
                "coredumpctl" if argv.last().unwrap() == "list" => {
                    r#"[{"exe": "/opt/x/bin/xr-renderer", "pid": 9, "sig": 11}, {"exe": "/usr/bin/other", "pid": 3, "sig": 6}]"#.into()
                }
                "coredumpctl" => "Stack trace of thread 9:\n#0  0x1 xr-renderer (f)\n".into(),
                _ => "-- cursor: s=abc\n".into(),
            };
            Some(Done { code: Some(0), stdout })
        }));
        host.sys_root = sys;
        host.proc_root = proc_root;
        host.luma_log = luma.clone();
        assert_eq!(
            host.usb_ports().unwrap(),
            Some(BTreeSet::from(["5-2.1".to_owned()]))
        );
        assert_eq!(host.drm("DP-1").unwrap().as_deref(), Some("connected"));
        assert_eq!(host.drm("HDMI-A-1").unwrap(), None);
        assert_eq!(host.hyprland_pid(Some("inst")).unwrap(), Some(1690));
        assert_eq!(host.alive(Some(1690)).unwrap(), Some(true));
        assert_eq!(host.alive(Some(1691)).unwrap(), Some(false));
        assert_eq!(host.boot_id().unwrap().as_deref(), Some("boot-1"));
        assert!(host.driver_alive().unwrap());
        assert_eq!(host.tracking_driver().unwrap(), "luma");
        assert_eq!(host.kernel_start().unwrap().as_deref(), Some("s=abc"));
        let start = host.driver_start().unwrap();
        assert_eq!(start, 10);
        std::fs::write(&luma, "luma: one\nluma: two\n").unwrap();
        assert_eq!(
            host.driver_read(start).unwrap(),
            ("luma: two\n".to_owned(), 20)
        );
        std::fs::write(&luma, "new\n").unwrap();
        assert_eq!(host.driver_read(20).unwrap(), ("new\n".to_owned(), 4));
        assert_eq!(host.driver_tail(1).unwrap(), ["new"]);
        let cores = host
            .core_dumps(100.9, &["/opt/x/bin/xr-renderer".into()], &[1690])
            .unwrap();
        assert_eq!(
            cores,
            [
                json!({"exe": "xr-renderer", "pid": 9, "signal": "SIGSEGV", "frames": ["#0  0x1 xr-renderer (f)"]})
            ]
        );
        assert!(
            calls
                .lock()
                .unwrap()
                .contains(&"coredumpctl --no-pager --json=short --since=@100 list".to_owned())
        );
        assert_eq!(host.result("r1").unwrap(), Value::Null);
    }

    #[test]
    fn a_command_that_overruns_is_no_answer() {
        let started = Instant::now();
        assert!(run_bounded(&argv(&["sleep", "5"]), Duration::from_millis(100), &[]).is_none());
        assert!(started.elapsed() < Duration::from_secs(2));
        let done = run_bounded(
            &argv(&["sh", "-c", "echo hi; exit 2"]),
            Duration::from_secs(5),
            &[],
        )
        .unwrap();
        assert_eq!((done.code, done.stdout.as_str()), (Some(2), "hi\n"));
    }
}
