//! A run outlives a USB drop of the glasses (`tools/native_spike/survive.py`, docs/usb-drops.md).
//!
//! The hub in the glasses can go at any time, and the second drop of a pair takes the display with
//! it. A drop ends the renderer, not the run: the session waits for the glasses, puts their mode
//! back and starts a new renderer. A wait that runs out ends the run the ordinary way.

use std::collections::VecDeque;
use std::fs;
use std::path::Path;
use std::time::Duration;

use super::tracking::Tracking;

/// The Luma Ultra's vendor interface: the one the driver opens.
pub const GLASSES_USB: (&str, &str) = ("35ca", "1104");
pub const PRO2_USB: (&str, &str) = ("35ca", "1301");
pub const PRO2_PRODUCT: &str = "VITURE PRO 2 XR Glasses";
/// Longer than any drop on record that was not a person unplugging them.
pub const HOLD_LIMIT_S: f64 = 120.0;
/// Up this long without a flicker: the hub drops in pairs up to 28 s apart, and a pair caught here
/// costs a new hold, not the session.
pub const SETTLE_S: f64 = 3.0;
pub const POLL_S: f64 = 0.25;
/// In `HOLD_WINDOW_S`: past this it is a bad cable, and the desktops belong on a monitor.
pub const MAX_HOLDS: usize = 8;
pub const HOLD_WINDOW_S: f64 = 600.0;
/// Ends that are final whatever the glasses did. A tracking timeout is the supervisor giving up on
/// an outage after the limit, and the USB number has moved by then, so it would read as a drop.
pub const NOT_A_DROP: [&str; 2] = ["interrupted", "tracking-timeout"];

/// The glasses model whose USB identity a run follows.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Model {
    LumaUltra,
    Pro2,
}

/// (bus, device number) of the glasses, or None. The number rises at every enumeration, so a
/// change is a drop, however quick. Port names are not used: they depend on where the cable is.
/// A Pro 2 also needs its exact product name and to be the only VITURE device, as the bridge's
/// identity guard does; a Luma Ultra is the first one found.
pub fn usb_identity(root: &Path, model: Model) -> Option<(i64, i64)> {
    let ids = match model {
        Model::LumaUltra => GLASSES_USB,
        Model::Pro2 => PRO2_USB,
    };
    let pro2 = model == Model::Pro2;
    let mut found = Vec::new();
    let entries = fs::read_dir(root).ok()?;
    for device in entries.flatten().map(|e| e.path()) {
        let read = |name: &str| fs::read_to_string(device.join(name)).map(|s| s.trim().to_string());
        match read("idVendor") {
            Ok(v) if v == ids.0 => {}
            _ => continue,
        }
        let identity = (|| {
            if read("idProduct").ok()? != ids.1 {
                return Some(None); // another VITURE device
            }
            if pro2 && read("product").ok()? != PRO2_PRODUCT {
                return None;
            }
            let num = |name| read(name).ok()?.parse::<i64>().ok();
            Some(Some((num("busnum")?, num("devnum")?)))
        })();
        match identity {
            Some(Some(id)) if !pro2 => return Some(id),
            Some(Some(id)) if id.0.min(id.1) <= 0 => return None,
            Some(Some(id)) => found.push(id),
            Some(None) if pro2 => return None,
            Some(None) => {}
            None if pro2 => return None,
            None => {}
        }
    }
    if found.len() == 1 { found.pop() } else { None }
}

/// Was this renderer's end a drop of the glasses? Only with evidence: a crash with the glasses
/// fine is still a crash. `expected`: the glasses are changing mode, which can take the output for
/// a moment and be over by the time this looks, so it counts as evidence by itself.
pub fn should_hold(
    returncode: i32,
    stop_reason: Option<&str>,
    output_ok: bool,
    usb_before: Option<(i64, i64)>,
    usb_now: Option<(i64, i64)>,
    expected: bool,
) -> bool {
    if stop_reason.is_some_and(|r| NOT_A_DROP.contains(&r)) {
        return false;
    }
    if returncode == 0 && stop_reason.is_none() {
        return false;
    }
    expected || !output_ok || (usb_before.is_some() && usb_before != usb_now)
}

/// One finished hold, as the run's report records it.
#[derive(Debug, Clone, PartialEq)]
pub struct Hold {
    /// Rounded to 0.1 s.
    pub seconds: f64,
    pub resumed: bool,
}

/// This run's holds, and the limit on them. Times are seconds on one monotonic clock.
#[derive(Debug, Default)]
pub struct Holds {
    pub record: Vec<Hold>,
    recent: VecDeque<f64>,
    since: Option<f64>,
}

impl Holds {
    /// May a hold begin at `now`? False once `MAX_HOLDS` began in the last `HOLD_WINDOW_S`; a hold
    /// exactly that old still counts.
    pub fn begin(&mut self, now: f64) -> bool {
        while self.recent.front().is_some_and(|&t| now - t > HOLD_WINDOW_S) {
            self.recent.pop_front();
        }
        if self.recent.len() >= MAX_HOLDS {
            return false;
        }
        self.recent.push_back(now);
        self.since = Some(now);
        true
    }

    pub fn finish(&mut self, now: f64, resumed: bool) {
        let seconds = now - self.since.unwrap_or(now);
        self.record.push(Hold { seconds: (seconds * 10.0).round() / 10.0, resumed });
    }

    pub fn survived(&self) -> usize {
        self.record.iter().filter(|h| h.resumed).count()
    }
}

/// Until `ready()` has held for `settle` seconds and `restore()` took: true. False when `limit`
/// runs out. `serve(seconds)` is the wait, and answers the panel meanwhile. A restore that fails
/// (the mode not read back yet) is another try, not the end.
pub fn wait_for_glasses<E>(
    mut ready: impl FnMut() -> bool,
    mut restore: impl FnMut() -> Result<(), E>,
    mut serve: impl FnMut(Duration),
    mut clock: impl FnMut() -> f64,
    limit: f64,
    settle: f64,
) -> bool {
    let end = clock() + limit;
    let mut since: Option<f64> = None;
    while clock() < end {
        if !ready() {
            since = None;
        } else {
            let now = clock();
            let from = *since.get_or_insert(now);
            if now - from >= settle {
                if restore().is_ok() {
                    return true;
                }
                since = None;
            }
        }
        serve(Duration::from_secs_f64(POLL_S));
    }
    false
}

/// The driver reconnects by itself, so a live one is left alone, even mid-calibration; only a dead
/// one is started again. False: no head tracking for now, which is a still picture and not a
/// reason to lose the session.
pub fn revive_tracking(tracking: &Tracking) -> bool {
    tracking.status().running || tracking.restart_same().is_ok()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::Cell;

    #[test]
    fn eight_holds_in_ten_minutes_then_no_more() {
        let mut h = Holds::default();
        for i in 0..MAX_HOLDS {
            assert!(h.begin(i as f64 * 10.0), "hold {i}");
            h.finish(i as f64 * 10.0 + 1.0, true);
        }
        assert!(!h.begin(100.0));
        assert!(!h.begin(600.0)); // the first, at 0, is exactly 600 s old: it still counts
        assert!(h.begin(600.1)); // now it has left the window
        assert!(!h.begin(600.2));
        assert_eq!(h.survived(), MAX_HOLDS);
    }

    #[test]
    fn a_refused_hold_does_not_count_against_later_ones() {
        let mut h = Holds::default();
        for _ in 0..MAX_HOLDS {
            assert!(h.begin(0.0));
        }
        for t in [1.0, 2.0, 3.0] {
            assert!(!h.begin(t));
        }
        assert!(h.begin(600.5));
    }

    #[test]
    fn a_hold_records_its_length_and_outcome() {
        let mut h = Holds::default();
        h.begin(10.0);
        h.finish(14.26, true);
        h.begin(20.0);
        h.finish(140.0, false);
        assert_eq!(h.record, [Hold { seconds: 4.3, resumed: true }, Hold { seconds: 120.0, resumed: false }]);
        assert_eq!(h.survived(), 1);
    }

    /// A fake clock that `serve` moves on.
    fn wait(ready: impl Fn(f64) -> bool, restores_fail: usize, limit: f64, settle: f64) -> (bool, f64, usize) {
        let now = Cell::new(0.0);
        let tries = Cell::new(0);
        let back = wait_for_glasses(
            || ready(now.get()),
            || {
                tries.set(tries.get() + 1);
                if tries.get() > restores_fail { Ok(()) } else { Err(()) }
            },
            |d| now.set(now.get() + d.as_secs_f64()),
            || now.get(),
            limit,
            settle,
        );
        (back, now.get(), tries.get())
    }

    #[test]
    fn the_hold_gives_up_at_120_seconds() {
        let (back, at, tries) = wait(|_| false, 0, HOLD_LIMIT_S, SETTLE_S);
        assert!(!back);
        assert!((HOLD_LIMIT_S..HOLD_LIMIT_S + POLL_S + 1e-9).contains(&at), "{at}");
        assert_eq!(tries, 0);
    }

    #[test]
    fn glasses_back_resume_after_they_settle() {
        let (back, at, tries) = wait(|t| t >= 5.0, 0, HOLD_LIMIT_S, SETTLE_S);
        assert!(back);
        assert!((8.0..=8.25).contains(&at), "{at}");
        assert_eq!(tries, 1);
    }

    #[test]
    fn a_flicker_restarts_the_settle() {
        let (back, at, _) = wait(|t| (5.0..6.0).contains(&t) || t >= 7.0, 0, HOLD_LIMIT_S, SETTLE_S);
        assert!(back);
        assert!(at >= 10.0, "{at}");
    }

    #[test]
    fn a_failed_restore_tries_again_and_glasses_that_return_too_late_lose() {
        let (back, _, tries) = wait(|_| true, 2, HOLD_LIMIT_S, SETTLE_S);
        assert!(back);
        assert_eq!(tries, 3);
        let (back, _, tries) = wait(|t| t >= 118.0, 0, HOLD_LIMIT_S, SETTLE_S);
        assert!(!back);
        assert_eq!(tries, 0);
    }

    #[test]
    fn what_counts_as_a_drop() {
        let a = Some((3, 7));
        let b = Some((3, 9));
        assert!(!should_hold(0, None, false, a, b, true)); // a clean exit is never a drop
        assert!(!should_hold(1, Some("interrupted"), false, a, b, true));
        assert!(!should_hold(1, Some("tracking-timeout"), false, a, b, true));
        assert!(!should_hold(-11, None, true, a, a, false)); // a crash with the glasses fine
        assert!(should_hold(-11, None, false, a, a, false)); // the output went
        assert!(should_hold(-11, None, true, a, b, false)); // re-enumerated
        assert!(should_hold(-11, None, true, a, None, false)); // gone from the bus
        assert!(!should_hold(-11, None, true, None, b, false)); // never seen before: no evidence
        assert!(should_hold(0, Some("output-lost"), true, a, a, true)); // a mode change
    }

    fn device(root: &Path, name: &str, files: &[(&str, &str)]) {
        let d = root.join(name);
        fs::create_dir_all(&d).unwrap();
        for (k, v) in files {
            fs::write(d.join(k), format!("{v}\n")).unwrap();
        }
    }

    #[test]
    fn usb_identity_follows_the_device_number() {
        let tmp = crate::testdir::TestDir::new("survive-usb");
        let root = tmp.path();
        assert_eq!(usb_identity(root, Model::LumaUltra), None);
        device(root, "1-1", &[("idVendor", "1d6b"), ("idProduct", "0002")]);
        device(root, "1-2", &[("idVendor", "35ca"), ("idProduct", "1104"), ("busnum", "1"), ("devnum", "12")]);
        device(root, "1-2:1.0", &[]);
        assert_eq!(usb_identity(root, Model::LumaUltra), Some((1, 12)));
        assert_eq!(usb_identity(root, Model::Pro2), None); // a Luma Ultra is another VITURE device
    }

    #[test]
    fn a_pro2_needs_its_name_and_to_be_alone() {
        let tmp = crate::testdir::TestDir::new("survive-pro2");
        let root = tmp.path();
        let pro2 = [("idVendor", "35ca"), ("idProduct", "1301"), ("product", PRO2_PRODUCT), ("busnum", "3"), ("devnum", "4")];
        device(root, "3-1", &pro2);
        assert_eq!(usb_identity(root, Model::Pro2), Some((3, 4)));
        assert_eq!(usb_identity(root, Model::LumaUltra), None);
        device(root, "3-2", &pro2);
        assert_eq!(usb_identity(root, Model::Pro2), None); // two of them
        fs::remove_dir_all(root.join("3-2")).unwrap();
        fs::write(root.join("3-1/product"), "Something else\n").unwrap();
        assert_eq!(usb_identity(root, Model::Pro2), None);
        fs::write(root.join("3-1/product"), format!("{PRO2_PRODUCT}\n")).unwrap();
        fs::write(root.join("3-1/devnum"), "0\n").unwrap();
        assert_eq!(usb_identity(root, Model::Pro2), None);
    }
}
