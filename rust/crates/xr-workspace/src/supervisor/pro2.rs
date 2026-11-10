//! The Pro 2 tracking wrapper: a supervised run's scope (`tools/pro2_runtime/scope.py` `TestScope`).
//!
//! The Pro 2 has no driver process to start. The supervised bridge publishes poses itself and
//! writes a health file 50 times a second; the run guard checks it, and the renderer gets the
//! scope's pose prefix and projection on its command line.

use std::fs::File;
use std::io::{self, Read};
use std::os::unix::fs::{MetadataExt, OpenOptionsExt};
use std::path::PathBuf;

use serde_json::Value;

use crate::private_file::strict_json;

/// Seconds. The bridge itself stops on 100 ms of silence, so this only asks that its monitor is alive.
pub const HEALTH_MAX_AGE: f64 = 2.0;
/// motion_smooth.hpp tau, checked by tools/pro2_runtime/smooth_acceptance.py.
pub const SMOOTH_MS: f64 = 35.0;
const HEALTH_MAX_BYTES: u64 = 4096;

#[derive(Debug)]
pub enum Error {
    /// The scope or the health file says no.
    Refused(String),
    /// The health file is not ours to trust.
    Permission(String),
    Os(io::Error),
}

impl std::fmt::Display for Error {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Error::Refused(m) | Error::Permission(m) => f.write_str(m),
            Error::Os(e) => e.fmt(f),
        }
    }
}

impl std::error::Error for Error {}

/// An explicit, approximate diagonal in degrees, as `launch.py` `validate_projection`.
pub fn validate_projection(value: f64) -> Result<f64, Error> {
    if value.is_finite() && (44.5..=50.0).contains(&value) {
        Ok(value)
    } else {
        Err(Error::Refused("explicit approximate supervised diagonal44.5..50 required".into()))
    }
}

/// The health file, which is replaced 50 times a second. An open can land just before a replace,
/// and the file it holds is then a whole, valid, unlinked one: nlink 0 is the old copy, not an
/// attack, so only more than one link is refused.
pub fn read_health(path: &std::path::Path) -> Result<Value, Error> {
    let mut file = File::options()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK)
        .open(path)
        .map_err(Error::Os)?;
    let st = file.metadata().map_err(Error::Os)?;
    // SAFETY: getuid has no preconditions and cannot fail.
    let uid = unsafe { libc::getuid() };
    if !st.file_type().is_file() || st.uid() != uid || st.mode() & 0o7777 != 0o600 || st.nlink() > 1 {
        return Err(Error::Permission("health must be owned regular single-link mode 0600".into()));
    }
    let mut raw = Vec::new();
    (&mut file).take(HEALTH_MAX_BYTES).read_to_end(&mut raw).map_err(Error::Os)?;
    strict_json(&raw).map_err(|e| Error::Refused(e.to_string()))
}

/// CLOCK_MONOTONIC in seconds, the clock Python's `time.monotonic` reads and the bridge stamps.
pub fn monotonic() -> f64 {
    let mut ts = libc::timespec { tv_sec: 0, tv_nsec: 0 };
    // SAFETY: ts is a valid timespec to write; CLOCK_MONOTONIC is always available on Linux.
    unsafe { libc::clock_gettime(libc::CLOCK_MONOTONIC, &mut ts) };
    ts.tv_sec as f64 + ts.tv_nsec as f64 * 1e-9
}

/// A float as Python's `str(float)` spells it, which the renderer's argument parser was fed.
fn py_float(x: f64) -> String {
    format!("{x:?}")
}

/// A supervised Pro 2 run's scope.
#[derive(Debug, Clone)]
pub struct Scope {
    pub prefix: String,
    pub projection: f64,
    pub health_path: PathBuf,
    pub nonce: String,
    pub smooth_ms: f64,
    /// Level to the SDK's gravity at each recentre, in place of the mount pitch alone.
    pub level_gravity: bool,
}

impl Scope {
    pub fn new(prefix: String, projection: f64, health_path: PathBuf, nonce: String) -> Self {
        Scope { prefix, projection, health_path, nonce, smooth_ms: SMOOTH_MS, level_gravity: false }
    }

    /// Ok while the bridge is live, ready and this run's: the run guard's check.
    pub fn healthy(&self) -> Result<(), Error> {
        self.healthy_at(monotonic())
    }

    pub fn healthy_at(&self, now: f64) -> Result<(), Error> {
        validate_projection(self.projection)?;
        let data = read_health(&self.health_path)?;
        let stamp = data
            .get("monotonic")
            .and_then(Value::as_f64)
            .ok_or_else(|| Error::Refused("Pro2 supervised provider health has no monotonic stamp".into()))?;
        let age = now - stamp;
        let nonce_ok = data.get("nonce").and_then(Value::as_str) == Some(self.nonce.as_str());
        let ready = data.get("ready").and_then(Value::as_bool) == Some(true);
        if nonce_ok && ready && (0.0..=HEALTH_MAX_AGE).contains(&age) {
            return Ok(());
        }
        // Python's `data.get('reason') or 'no reason given'`: anything falsy has no reason. A reason
        // that is not a string is shown as JSON, where Python would show its repr.
        let reason = match data.get("reason") {
            Some(Value::String(s)) if !s.is_empty() => s.clone(),
            None | Some(Value::Null | Value::Bool(false) | Value::String(_)) => "no reason given".into(),
            Some(Value::Number(n)) if n.as_f64() == Some(0.0) => "no reason given".into(),
            Some(Value::Array(a)) if a.is_empty() => "no reason given".into(),
            Some(Value::Object(o)) if o.is_empty() => "no reason given".into(),
            Some(other) => other.to_string(),
        };
        Err(Error::Refused(format!("Pro2 supervised provider stale/unready: age {age:.3} s, {reason}")))
    }

    /// The renderer's arguments for this scope (`supervisor.py`).
    pub fn renderer_args(&self) -> Vec<String> {
        let mut args = vec![
            "--pro2-test-pose-prefix".into(),
            self.prefix.clone(),
            "--pro2-test-projection".into(),
            py_float(self.projection),
            "--pro2-test-smooth-ms".into(),
            py_float(self.smooth_ms),
        ];
        if self.level_gravity {
            args.extend(["--pro2-test-level-gravity".into(), "1".into()]);
        }
        args
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::os::unix::fs::PermissionsExt;

    fn health(dir: &std::path::Path, body: &str) -> PathBuf {
        let p = dir.join("health.json");
        let _ = fs::remove_file(&p);
        fs::write(&p, body).unwrap();
        fs::set_permissions(&p, fs::Permissions::from_mode(0o600)).unwrap();
        p
    }

    #[test]
    fn projection_must_be_a_plausible_diagonal() {
        for ok in [44.5, 47.0, 50.0] {
            assert_eq!(validate_projection(ok).unwrap(), ok);
        }
        for bad in [44.4, 50.01, f64::NAN, f64::INFINITY, 0.0] {
            assert!(validate_projection(bad).is_err(), "{bad}");
        }
    }

    #[test]
    fn a_live_ready_bridge_is_healthy_and_anything_else_says_why() {
        let tmp = crate::testdir::TestDir::new("pro2-health");
        let dir = tmp.path();
        let scope = |path| Scope::new("/xr-pro2".into(), 47.0, path, "abc".into());
        let s = scope(health(dir, r#"{"monotonic": 100.0, "nonce": "abc", "ready": true}"#));
        assert!(s.healthy_at(101.5).is_ok());
        assert!(s.healthy_at(100.0).is_ok());
        let e = s.healthy_at(102.5).unwrap_err().to_string();
        assert_eq!(e, "Pro2 supervised provider stale/unready: age 2.500 s, no reason given");
        assert!(s.healthy_at(99.0).is_err()); // from the future

        let s = scope(health(dir, r#"{"monotonic": 100, "nonce": "abc", "ready": false, "reason": "no glasses"}"#));
        assert_eq!(
            s.healthy_at(100.25).unwrap_err().to_string(),
            "Pro2 supervised provider stale/unready: age 0.250 s, no glasses"
        );
        let s = scope(health(dir, r#"{"monotonic": 100, "nonce": "other", "ready": true}"#));
        assert!(s.healthy_at(100.0).is_err());
        let s = scope(health(dir, r#"{"monotonic": 100, "ready": 1, "nonce": "abc"}"#));
        assert!(s.healthy_at(100.0).is_err()); // `ready is True`, not truthy
        let s = scope(health(dir, r#"{"monotonic": 100, "nonce": "abc", "ready": true, "ready": true}"#));
        assert!(s.healthy_at(100.0).is_err()); // strict JSON

        let mut s = scope(health(dir, r#"{"monotonic": 100.0, "nonce": "abc", "ready": true}"#));
        s.projection = 60.0;
        assert!(s.healthy_at(100.0).is_err());
    }

    #[test]
    fn only_a_private_single_link_file_is_read() {
        let tmp = crate::testdir::TestDir::new("pro2-perm");
        let dir = tmp.path();
        let p = health(dir, r#"{"monotonic": 1, "nonce": "", "ready": true}"#);
        assert!(read_health(&p).is_ok());
        fs::set_permissions(&p, fs::Permissions::from_mode(0o644)).unwrap();
        assert!(matches!(read_health(&p), Err(Error::Permission(_))));
        fs::set_permissions(&p, fs::Permissions::from_mode(0o600)).unwrap();
        fs::hard_link(&p, dir.join("second")).unwrap();
        assert!(matches!(read_health(&p), Err(Error::Permission(_))));
        fs::remove_file(dir.join("second")).unwrap();
        std::os::unix::fs::symlink(&p, dir.join("link")).unwrap();
        assert!(matches!(read_health(&dir.join("link")), Err(Error::Os(_))));
    }

    #[test]
    fn renderer_args_match_the_python_supervisor() {
        let mut s = Scope::new("/xr-pro2-1".into(), 47.0, "/h".into(), "n".into());
        assert_eq!(
            s.renderer_args(),
            ["--pro2-test-pose-prefix", "/xr-pro2-1", "--pro2-test-projection", "47.0", "--pro2-test-smooth-ms", "35.0"]
        );
        s.level_gravity = true;
        s.projection = 46.25;
        assert_eq!(s.renderer_args()[3], "46.25");
        assert_eq!(s.renderer_args()[6..], ["--pro2-test-level-gravity", "1"]);
    }
}
