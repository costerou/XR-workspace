//! The gyro bias kept between runs (acceptance C5, C6): `luma-gyro-bias` in the state directory.
//! A port of `luma::wiring::BiasStore` and the bias half of `FusionEstimator` (spike/driver/src/wiring.cpp).
//!
//! The file is read at start, and written every five minutes and at exit, off the sample path: the
//! driver outlives its XR runs, and a crash should not lose what it learned. The estimator notes its
//! bias here once a minute, and only when it is `Good`. A missing or unreadable file means a normal
//! cold start.
//!
//! Format, shared with the C++ driver so either can read what the other wrote: one line, three rad/s
//! values (x y z, driver frame) separated by single spaces, each written as C's `%.9g` of the float,
//! then `\n`. Reading takes the first three whitespace-separated numbers; any value that is not
//! finite or is above `MAX_BIAS` refuses the whole file.

use std::cell::RefCell;
use std::path::{Path, PathBuf};

use crate::daemon::Estimator;
use crate::fusion::{Fusion, FusionConfig, FusionQuality, FusionStatus};
use crate::types::{ImuSample, PoseOut};

/// The file's name inside the state directory.
pub const FILE_NAME: &str = "luma-gyro-bias";
/// rad/s. Beyond anything a gyro could need: such a file is refused, not clamped. `FusionConfig::max_bias`.
pub const MAX_BIAS: f32 = 0.35;
/// The store is written this often while the driver runs (`next_save` in spike/driver/src/main.cpp).
pub const SAVE_EVERY_NS: u64 = 300_000_000_000;
/// The estimator notes its bias every this many samples: a minute at 1 kHz.
pub const NOTE_EVERY: u64 = 60_000;

pub struct BiasStore {
    path: PathBuf,
    bias: Option<[f32; 3]>,
    next_save_ns: u64,
}

impl BiasStore {
    pub fn new(path: impl Into<PathBuf>) -> Self {
        Self { path: path.into(), bias: None, next_save_ns: 0 }
    }

    /// The store in `state_dir`, or in `default_state_dir()` when that is empty, already loaded.
    /// None when there is no directory to use: then the driver runs without one.
    pub fn open(state_dir: &str) -> Option<Self> {
        let dir = if state_dir.is_empty() { default_state_dir()? } else { PathBuf::from(state_dir) };
        let mut store = Self::new(dir.join(FILE_NAME));
        store.load();
        Some(store)
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    pub fn bias(&self) -> Option<[f32; 3]> {
        self.bias
    }

    pub fn remember(&mut self, bias: [f32; 3]) {
        self.bias = Some(bias);
    }

    /// Reads the file. Leaves the store as it was when the file is missing, unreadable or refused.
    pub fn load(&mut self) {
        let Ok(text) = std::fs::read_to_string(&self.path) else { return };
        if let Some(bias) = parse(&text) {
            self.bias = Some(bias);
        }
    }

    /// Writes the bias through a `.tmp` file and a rename, so a reader never sees half a file.
    /// False when there is nothing to write or the write failed.
    pub fn save(&self) -> bool {
        let Some(bias) = self.bias else { return false };
        if let Some(parent) = self.path.parent() {
            let _ = std::fs::create_dir_all(parent);
        }
        let mut temporary = self.path.clone().into_os_string();
        temporary.push(".tmp");
        std::fs::write(&temporary, format_line(bias)).is_ok() && std::fs::rename(&temporary, &self.path).is_ok()
    }

    /// The five-minute rewrite, for the daemon's tick. The first call only starts the clock: the
    /// file was just read, so there is nothing new to write yet.
    pub fn tick(&mut self, now_ns: u64) {
        if now_ns < self.next_save_ns {
            return;
        }
        if self.next_save_ns != 0 {
            self.save();
        }
        self.next_save_ns = now_ns + SAVE_EVERY_NS;
    }
}

/// `$XDG_STATE_HOME/viture-workspace`, else `$HOME/.local/state/viture-workspace`, else None.
pub fn default_state_dir() -> Option<PathBuf> {
    let set = |name| std::env::var_os(name).filter(|v| !v.is_empty()).map(PathBuf::from);
    if let Some(state) = set("XDG_STATE_HOME") {
        return Some(state.join("viture-workspace"));
    }
    set("HOME").map(|home| home.join(".local/state/viture-workspace"))
}

fn parse(text: &str) -> Option<[f32; 3]> {
    let mut words = text.split_ascii_whitespace();
    let mut bias = [0f32; 3];
    for v in &mut bias {
        *v = words.next()?.parse().ok()?;
        if !v.is_finite() || v.abs() > MAX_BIAS {
            return None;
        }
    }
    Some(bias)
}

fn format_line(bias: [f32; 3]) -> String {
    format!("{} {} {}\n", format_g9(bias[0]), format_g9(bias[1]), format_g9(bias[2]))
}

/// C's `%.9g` of a float, as the C++ driver's `ostream` with `precision(9)` writes it.
fn format_g9(v: f32) -> String {
    const P: i32 = 9;
    let v = f64::from(v);
    let sci = format!("{:.*e}", (P - 1) as usize, v);
    let (mantissa, exp) = sci.split_once('e').expect("{:e} always has an exponent");
    let exp: i32 = exp.parse().expect("{:e} exponent is an integer");
    if (-4..P).contains(&exp) {
        trim_zeros(format!("{:.*}", (P - 1 - exp) as usize, v))
    } else {
        let sign = if exp < 0 { '-' } else { '+' };
        format!("{}e{sign}{:02}", trim_zeros(mantissa.to_owned()), exp.abs())
    }
}

fn trim_zeros(mut s: String) -> String {
    if s.contains('.') {
        s.truncate(s.trim_end_matches('0').trim_end_matches('.').len());
    }
    s
}

/// Fusion, warm-started from the store, that hands its bias back to it: once a minute while it
/// runs and once more when it goes. Only a `Good` bias is kept; a `Coarse` guess must not become
/// the next run's truth.
pub struct FusionEstimator<'a> {
    fusion: Fusion,
    store: Option<&'a RefCell<BiasStore>>,
    samples: u64,
}

impl<'a> FusionEstimator<'a> {
    /// A cold start that keeps nothing.
    pub fn new(config: FusionConfig) -> Self {
        Self { fusion: Fusion::new(config), store: None, samples: 0 }
    }

    /// `wiring::estimator_with`: starts from the stored bias, if any, and notes what it learns.
    pub fn with_store(store: &'a RefCell<BiasStore>) -> Self {
        let config = FusionConfig { warm_start_bias: store.borrow().bias(), ..FusionConfig::default() };
        Self { fusion: Fusion::new(config), store: Some(store), samples: 0 }
    }

    pub fn status(&self) -> FusionStatus {
        self.fusion.status()
    }

    fn note_bias(&self) {
        let Some(store) = self.store else { return };
        let status = self.fusion.status();
        if status.quality == FusionQuality::Good {
            store.borrow_mut().remember(status.gyro_bias_rad_s);
        }
    }
}

impl Estimator for FusionEstimator<'_> {
    fn update(&mut self, sample: &ImuSample) -> PoseOut {
        let pose = self.fusion.update(sample);
        self.samples += 1;
        if self.samples % NOTE_EVERY == 0 {
            self.note_bias();
        }
        pose
    }
}

impl Drop for FusionEstimator<'_> {
    fn drop(&mut self) {
        self.note_bias();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn writes_what_the_cpp_ostream_writes() {
        // Each right-hand side is what `std::ostream` with precision(9) printed for the float.
        let cases: [(f32, &str); 15] = [
            (0.01, "0.00999999978"),
            (-0.02, "-0.0199999996"),
            (0.03, "0.0299999993"),
            (0.004, "0.00400000019"),
            (-0.0025, "-0.00249999994"),
            (0.0015, "0.00150000001"),
            (0.0, "0"),
            (-0.0, "-0"),
            (1.5e-5, "1.49999996e-05"),
            (-0.000123456, "-0.000123456004"),
            (0.35, "0.349999994"),
            (0.1, "0.100000001"),
            (1e-7, "1.00000001e-07"),
            (0.0001, "9.99999975e-05"),
            (0.00009999, "9.99900003e-05"),
        ];
        for (v, want) in cases {
            assert_eq!(format_g9(v), want, "{v:e}");
            assert_eq!(want.parse::<f32>().unwrap().to_bits(), v.to_bits(), "{want} reads back exactly");
        }
    }

    #[test]
    fn reads_the_first_three_numbers_and_refuses_the_rest() {
        assert_eq!(parse("0.01 -0.02 0.03\n"), Some([0.01, -0.02, 0.03]));
        assert_eq!(parse("  0.01\n-0.02\t0.03 trailing"), Some([0.01, -0.02, 0.03]));
        assert_eq!(parse("garbage"), None);
        assert_eq!(parse("0.01 0.02"), None);
        assert_eq!(parse("0 0 9"), None);
        assert_eq!(parse("0 0 nan"), None);
        assert_eq!(parse("0 inf 0"), None);
        assert_eq!(parse(""), None);
    }
}
