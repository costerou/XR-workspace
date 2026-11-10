//! The free pointer's sampler: the background cursor reads and every compositor call the overview pointer makes (the
//! left-button binds, hiding, showing and parking the cursor, handing it back), plus, for the unified cursor, the
//! confinement to home and the push past its edge that switches desktops.
//!
//! A port of `free_pointer.PointerSampler` ([`Variant::Plain`]), `input_lock.LockedSampler` ([`Variant::Locked`]) and
//! `unified_cursor.UnifiedSampler` ([`Variant::Unified`]), with `unified_cursor.EdgePush` and its helpers in [`push`].
//! The decisions ([`Sampler`]) are kept apart from the thread ([`run`]): one [`Sampler::step`] is one iteration of the
//! Python's `_run` loop and says how long to wait after it, read from a [`Clock`], so the tests drive it on a fake clock.
//! `rust/corpus/pointer/sampler.json` holds the Python's answers and `sampler_corpus_tests.rs` replays them.
//!
//! # The loop is event-driven
//!
//! The Python wakes every `IDLE_S` (0.1 s) outside the overview to look whether it opened. Here the thread blocks on a
//! condition variable instead whenever a step says nothing can change on its own ([`Idle::Block`] or [`Idle::Until`]),
//! and [`run::Handle::wake`] ends that wait at once: the pointer's owner calls it when the overview opens or closes,
//! when restore sampling starts or stops, and [`run::Handle::set_names`] wakes it too. While sampling it runs at the
//! Python's cadence: `period` after each step (`SAMPLE_S`), `IDLE_S` where the Python polls.
//!
//! What still polls, as the Python does, because the cursor itself is what it watches and Hyprland sends no event for
//! it: the unified cursor outside the overview while the run has XR monitors (the confinement to home and the edge
//! push: `period` while the cursor is on an XR monitor, `IDLE_S` off them), and any step whose compositor call failed
//! (it is retried). The plain and locked samplers block outside the overview once the binds are gone and the cursor
//! shows; the unified one blocks until its next monitors read while the run has no XR monitor.
//!
//! # One sample's cost
//!
//! Each sample is still one `cursorpos` request on a fresh connection to Hyprland's one-shot request socket (Hyprland
//! answers one request per connection). The real client ([`live`]) sends it on a [`crate::hyprland::Lane`]: the
//! socket directory checks, the peer's credentials and its start time are verified once, and the open directory, the
//! socket address, the verified identity and a pidfd of that compositor are kept. Each sample then costs one `fstatat`
//! of the socket, `socket` + `connect`, an `SO_PEERCRED` check against the kept identity, a `poll` of the pidfd (the
//! compositor has not exited, so its PID was not reused) in place of reading `/proc/<pid>/stat`, the write, and a
//! `poll` + `read` into a buffer kept across samples. The reply is parsed strictly as every query is, which allocates
//! the few small nodes of `{"x": .., "y": ..}`; nothing else is allocated per sample.

pub mod live;
pub mod push;
pub mod run;
mod step;

pub use push::EdgePush;
pub use step::{Idle, Sampler, ShapeReader, Variant, Wait};

use std::time::Duration;

use serde_json::Value;

use super::columns::ColumnsClient;
use super::overview_cursor::CursorClient;
use crate::resize::OutputRect;

/// free_pointer.SAMPLE_S: one cursor sample per this long in the overview.
pub const SAMPLE_S: f64 = 1.0 / 250.0;
/// free_pointer.IDLE_S: the Python's poll outside the overview.
pub const IDLE_S: f64 = 0.1;
/// free_pointer.OUTPUTS_REFRESH: the XR monitors are read again this often.
pub const OUTPUTS_REFRESH: f64 = 2.0;
/// free_pointer.RECENTRE_PX: free mode puts a hidden cursor this far from its parking point back.
pub const RECENTRE_PX: f64 = 200.0;
/// free_pointer.JUMP_PX: a move further than this is a warp, never the hand.
pub const JUMP_PX: f64 = 400.0;
/// free_pointer.SAMPLE_JUMP_PX: a sample this far from the last on either axis counts no motion.
pub const SAMPLE_JUMP_PX: i64 = 1500;
/// free_pointer.INSET_PX: the confinement keeps the cursor this far inside home.
pub const INSET_PX: f64 = 2.0;
/// free_pointer shape reads: every SHAPE_S, every SHAPE_CHECK_S while the plugin pushes, SHAPE_SETTLE_S after a warp,
/// SHAPE_RETRY_S after a failure.
pub const SHAPE_S: f64 = 1.0 / 120.0;
pub const SHAPE_CHECK_S: f64 = 0.25;
pub const SHAPE_SETTLE_S: f64 = 1.0 / 30.0;
pub const SHAPE_RETRY_S: f64 = 1.0;
/// A config reload that dropped the binds is answered by a regrab at most once per this long.
pub const REGRAB_S: f64 = 2.0;
/// input_lock.PARK_MARGIN_PX and LEASH_PX.
pub const PARK_MARGIN_PX: f64 = 100.0;
pub const LEASH_PX: f64 = 32.0;
/// unified_cursor.CARRY_LEASH_PX and PENDING_S.
pub const CARRY_LEASH_PX: f64 = 6.0;
pub const PENDING_S: f64 = 0.5;
/// The reads' timeouts.
pub const MONITORS_TIMEOUT: Duration = Duration::from_millis(500);
pub const CURSOR_TIMEOUT: Duration = Duration::from_millis(250);

/// Why a compositor call failed. `Missing`: a client without that call (the Python's `getattr(.., None)` or an
/// `AttributeError`). `Failed`: the call raised (`OSError`, `ValueError`, ..), which every caller here catches.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum CallError {
    Failed,
    Missing,
}

impl From<crate::hyprland::Error> for CallError {
    fn from(_: crate::hyprland::Error) -> Self {
        CallError::Failed
    }
}

/// Monotonic seconds (the Python's `time.monotonic`).
pub trait Clock {
    fn now(&mut self) -> f64;
}

/// `CLOCK_MONOTONIC`, as `time.monotonic` reads it.
#[derive(Clone, Copy, Debug, Default)]
pub struct Monotonic;

impl Clock for Monotonic {
    fn now(&mut self) -> f64 {
        let mut ts = libc::timespec {
            tv_sec: 0,
            tv_nsec: 0,
        };
        // SAFETY: `ts` is a valid timespec to write; CLOCK_MONOTONIC always exists on Linux.
        unsafe { libc::clock_gettime(libc::CLOCK_MONOTONIC, &mut ts) };
        ts.tv_sec as f64 + ts.tv_nsec as f64 * 1e-9
    }
}

/// An edge of a desktop ('left', 'right', 'up', 'down').
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Side {
    Left,
    Right,
    Up,
    Down,
}

impl Side {
    pub fn as_str(self) -> &'static str {
        match self {
            Side::Left => "left",
            Side::Right => "right",
            Side::Up => "up",
            Side::Down => "down",
        }
    }

    pub fn parse(s: &str) -> Option<Side> {
        Some(match s {
            "left" => Side::Left,
            "right" => Side::Right,
            "up" => Side::Up,
            "down" => Side::Down,
            _ => return None,
        })
    }

    /// Left or right (columns.SIDES).
    pub fn horizontal(self) -> bool {
        matches!(self, Side::Left | Side::Right)
    }
}

/// The overview pointer's mode, and the kind of what it wants of the sampler (`OverviewPointer.mode`, `want[1]`).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Mode {
    Off,
    Away,
    Desktop,
    Free,
    Carry,
    Restore,
    /// Any other name.
    Other,
}

impl Mode {
    pub fn as_str(self) -> &'static str {
        match self {
            Mode::Off => "off",
            Mode::Away => "away",
            Mode::Desktop => "desktop",
            Mode::Free => "free",
            Mode::Carry => "carry",
            Mode::Restore => "restore",
            Mode::Other => "other",
        }
    }

    pub fn parse(s: &str) -> Mode {
        match s {
            "off" => Mode::Off,
            "away" => Mode::Away,
            "desktop" => Mode::Desktop,
            "free" => Mode::Free,
            "carry" => Mode::Carry,
            "restore" => Mode::Restore,
            _ => Mode::Other,
        }
    }

    /// overview_cursor.DOT_MODES: the dot drawn and the real cursor hidden.
    pub fn dot(self) -> bool {
        matches!(self, Mode::Desktop | Mode::Free | Mode::Carry)
    }
}

/// `OverviewPointer.want`: (serial, kind, layout point or none).
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Want {
    pub serial: i64,
    pub kind: Mode,
    pub point: Option<(i64, i64)>,
}

/// One pan past an edge (`edge_pan.Plan`): what [`EdgePush`] reads of it.
pub trait PanPlan {
    /// The `edge_pan` payload [`PanPlan::frame`] gives.
    type Frame: Clone;
    fn home(&self) -> i64;
    fn target(&self) -> i64;
    fn side(&self) -> Side;
    /// Fractions along the two edges.
    fn exit(&self) -> f64;
    fn entry(&self) -> f64;
    /// The hand's travel past `edge_pan.START_PX` that switches.
    fn total_px(&self) -> f64;
    /// The payload at `travel` px past `edge_pan.START_PX`.
    fn frame(&self, travel: f64) -> Self::Frame;
}

/// The overview pointer as the sampler thread sees it (`free_pointer.OverviewPointer` and its lock / unified
/// subclasses). Methods are the Python's attribute reads and calls, in the order the Python makes them; an
/// implementation shared with the run's loop takes its lock per call.
pub trait SampledPointer {
    type Plan: PanPlan;
    /// In the overview (`overview()`).
    fn overview(&mut self) -> bool;
    /// UI ownership wants the cursor sampled outside the overview (`restore_sampling()`).
    fn restore_sampling(&mut self) -> bool;
    /// `_restore_release()`.
    fn restore_release(&mut self);
    /// `want`.
    fn want(&mut self) -> Want;
    /// `binds_dropped`, and setting it back to false.
    fn binds_dropped(&mut self) -> bool;
    fn clear_binds_dropped(&mut self);
    /// `mode`.
    fn mode(&mut self) -> Mode;
    /// `feed(cursor, motion, outputs, applied)`.
    fn feed(
        &mut self,
        cursor: Option<(i64, i64)>,
        motion: (f64, f64),
        outputs: &[OutputRect],
        applied: i64,
    );
    /// The shape reads: `_handles_on()`, `_sync`, `shape_gated`, `shape_pushed`, `shape_serial()`, `feed_shape`, and
    /// `shape_wired` (set once, at construction).
    fn handles_on(&mut self) -> bool;
    fn sync(&mut self) -> i64;
    fn shape_gated(&mut self) -> bool;
    fn shape_pushed(&mut self) -> bool;
    fn shape_serial(&mut self) -> i64;
    fn feed_shape(&mut self, name: Option<&str>, serial: i64);
    fn set_shape_wired(&mut self, wired: bool);
    /// The control's config reload count (`control.reloads`) and selected desktop (`control.state.selected`).
    fn reloads(&mut self) -> i64;
    fn selected(&mut self) -> i64;
    /// The unified pointer only: `set_pan`, `request_switch`, `pan_plan`, `neighbour`, `can_switch`, `can_scroll`.
    fn set_pan(&mut self, frame: Option<&<Self::Plan as PanPlan>::Frame>);
    fn request_switch(&mut self, home: i64, target: i64, panned: bool);
    fn pan_plan(
        &mut self,
        target: i64,
        side: Side,
        along: f64,
        output: &OutputRect,
    ) -> Option<Self::Plan>;
    fn neighbour(&mut self, side: Side, along: f64) -> Option<i64>;
    fn can_switch(&mut self) -> bool;
    fn can_scroll(&mut self) -> bool;
}

/// The sampler's compositor calls (`hyprland.HyprlandClient`'s). `bind_args` are the bind command's five strings
/// (python, client, control socket, run id, session); `Missing` is a client without the call.
pub trait SamplerClient: CursorClient + ColumnsClient {
    /// `query('cursorpos', timeout=..)`: `Ok(None)` is a reply without finite numbers `x` and `y`.
    fn cursor_pos(&mut self, timeout: Duration) -> Result<Option<(f64, f64)>, CallError>;
    fn grab_pointer(&mut self, bind_args: &[String], x: i64, y: i64) -> Result<(), CallError>;
    fn lock_pointer(&mut self, bind_args: &[String], x: i64, y: i64) -> Result<(), CallError>;
    fn release_pointer(&mut self, x: Option<i64>, y: Option<i64>) -> Result<(), CallError>;
    fn warp_cursor(&mut self, x: i64, y: i64) -> Result<(), CallError>;
    fn remove_resize_binds(&mut self) -> Result<(), CallError>;
    fn enter_desktop(
        &mut self,
        bind_args: &[String],
        x: Option<i64>,
        y: Option<i64>,
        monitor: Option<&str>,
    ) -> Result<(), CallError>;
    fn arm_click_binds(&mut self, bind_args: &[String]) -> Result<(), CallError>;
    fn confine_cursor(&mut self, x: i64, y: i64, name: &str) -> Result<(), CallError>;
    /// The client can scroll a scrolling layout's columns (`hasattr(client, 'scroll_to_window')`).
    fn scrolls(&self) -> bool;
}

/// A `cursorpos` reply's `x` and `y`: numbers (never booleans), both finite; `None` otherwise.
pub fn cursor_reply(pos: &Value) -> Option<(f64, f64)> {
    let x = pos.as_object()?.get("x")?;
    let y = pos.as_object()?.get("y")?;
    let (x, y) = (x.as_f64()?, y.as_f64()?);
    (x.is_finite() && y.is_finite()).then_some((x, y))
}

/// `names[i]` as Python indexes a list: a negative `i` counts from the end; `None` is the `IndexError`.
pub fn py_index(names: &[String], i: i64) -> Option<&str> {
    let n = names.len() as i64;
    let i = if i < 0 { i + n } else { i };
    (0..n).contains(&i).then(|| names[i as usize].as_str())
}

/// input_lock.park_point: the integer layout pixel where the capture holds the cursor on `output`: `cursor` kept
/// PARK_MARGIN_PX inside each edge (the middle, on an output too small for that), or the middle if none is given.
pub fn park_point(output: &OutputRect, cursor: Option<(f64, f64)>) -> (i64, i64) {
    fn axis(lo: i64, size: f64, v: Option<f64>) -> i64 {
        let lo = lo as f64;
        let margin = PARK_MARGIN_PX.min((size - 1.0) / 2.0);
        match v {
            None => (lo + size / 2.0).trunc() as i64,
            Some(v) => v.max(lo + margin).min(lo + size - 1.0 - margin).trunc() as i64,
        }
    }
    (
        axis(output.x, output.width, cursor.map(|c| c.0)),
        axis(output.y, output.height, cursor.map(|c| c.1)),
    )
}

/// A float in a corpus state snapshot: JSON has no infinities, so they are `{"$f": "inf"}` / `{"$f": "-inf"}`.
#[cfg(test)]
pub(crate) fn test_float(v: f64) -> Value {
    if v.is_infinite() {
        serde_json::json!({"$f": if v > 0.0 { "inf" } else { "-inf" }})
    } else {
        serde_json::json!(v)
    }
}

/// A plan or frame as the corpus shows it.
#[cfg(test)]
pub trait Snapshot {
    fn snapshot(&self) -> Value;
}

#[cfg(test)]
impl Snapshot for Value {
    fn snapshot(&self) -> Value {
        self.clone()
    }
}

/// An output as the Python holds it (`resize.outputs_from_monitors`).
#[cfg(test)]
pub(crate) fn test_output(o: &OutputRect) -> Value {
    serde_json::json!({"index": o.index, "x": o.x, "y": o.y, "width": o.width, "height": o.height})
}

#[cfg(test)]
mod tests {
    use super::*;

    fn rect(x: i64, y: i64, w: f64, h: f64) -> OutputRect {
        OutputRect {
            index: 0,
            x,
            y,
            width: w,
            height: h,
        }
    }

    #[test]
    fn park_points() {
        let o = rect(1920, 0, 1920.0, 1200.0);
        assert_eq!(park_point(&o, None), (2880, 600));
        assert_eq!(park_point(&o, Some((1930.0, 5.0))), (2020, 100));
        assert_eq!(park_point(&o, Some((5000.0, 1199.0))), (3739, 1099));
        let tiny = rect(0, 0, 51.0, 9.0);
        assert_eq!(park_point(&tiny, Some((0.0, 0.0))), (25, 4));
    }

    #[test]
    fn cursor_replies() {
        use serde_json::json;
        assert_eq!(cursor_reply(&json!({"x": 3, "y": 4.5})), Some((3.0, 4.5)));
        assert_eq!(cursor_reply(&json!({"x": true, "y": 4})), None);
        assert_eq!(cursor_reply(&json!({"x": 3})), None);
        assert_eq!(cursor_reply(&json!([3, 4])), None);
        assert_eq!(cursor_reply(&json!({"x": "3", "y": 4})), None);
    }

    #[test]
    fn python_indexes() {
        let names = vec!["a".to_string(), "b".to_string()];
        assert_eq!(py_index(&names, 1), Some("b"));
        assert_eq!(py_index(&names, -1), Some("b"));
        assert_eq!(py_index(&names, -3), None);
        assert_eq!(py_index(&names, 2), None);
    }
}
