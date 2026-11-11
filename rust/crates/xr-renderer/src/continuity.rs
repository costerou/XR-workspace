//! Port of spike/include/xr/continuity.hpp (`xr::ViewContinuity`) and the file-descriptor halves of
//! `Presentation::save_continuity` / `restore_continuity` (spike/src/wayland.cpp).
//!
//! A private, same-build renderer handover: never persisted, never accepted from IPC. The blob is the C++ one byte for
//! byte: `XRCONT01`, the FNV-1a 64 hash of the payload, then the payload, where each value is the C++ object's bytes as
//! x86-64 GCC lays it out (`memcpy` of a trivially copyable type, padding included) and a vector is a `u32` count
//! (at most 64) then its items. So a blob the C++ renderer writes restores here and the reverse. Padding is written as
//! zeros here and ignored on read; the C++ writes whatever its padding holds, which the hash covers either way.

use std::os::fd::BorrowedFd;

use crate::desktops::MAX_DESKTOPS;
use crate::pose::{Mat3, ModeController, PoseState, Quat, Tuning, ViewMode, IDENTITY};
use crate::stabilization::Stabilizer;
use crate::view::{EdgePan, LandedTurn, Landing, Switch, ViewAnimator};

const MAGIC: &[u8; 8] = b"XRCONT01";
/// The largest blob either side accepts (and the most `restore` reads).
pub const MAX_BYTES: usize = 65536;
/// The most items in a vector (`continuity vector limit`).
const MAX_ITEMS: u32 = 64;

/// A value with a fixed C++ byte layout of `SIZE` bytes.
pub(crate) trait Field: Sized {
    const SIZE: usize;
    /// Into `out` (exactly `SIZE` bytes, prefilled with the padding byte): every byte but padding.
    fn write(&self, out: &mut [u8]);
    /// From exactly `SIZE` bytes; `None` for a value the C++ type cannot hold (a bool or enum out of range).
    fn read(b: &[u8]) -> Option<Self>;
}

fn write_at<T: Field>(out: &mut [u8], off: usize, v: &T) {
    v.write(&mut out[off..off + T::SIZE]);
}

fn read_at<T: Field>(b: &[u8], off: usize) -> Option<T> {
    T::read(&b[off..off + T::SIZE])
}

macro_rules! number {
    ($($t:ty),*) => {$(
        impl Field for $t {
            const SIZE: usize = std::mem::size_of::<$t>();
            fn write(&self, out: &mut [u8]) {
                out.copy_from_slice(&self.to_ne_bytes());
            }
            fn read(b: &[u8]) -> Option<Self> {
                Some(<$t>::from_ne_bytes(b.try_into().ok()?))
            }
        }
    )*};
}
number!(u8, i32, u32, u64, f32, f64);

impl Field for bool {
    const SIZE: usize = 1;
    fn write(&self, out: &mut [u8]) {
        out[0] = u8::from(*self);
    }
    fn read(b: &[u8]) -> Option<Self> {
        match b[0] {
            0 => Some(false),
            1 => Some(true),
            _ => None,
        }
    }
}

impl Field for ViewMode {
    const SIZE: usize = 1;
    fn write(&self, out: &mut [u8]) {
        out[0] = *self as u8;
    }
    fn read(b: &[u8]) -> Option<Self> {
        match b[0] {
            0 => Some(ViewMode::Anchor),
            1 => Some(ViewMode::Locked),
            2 => Some(ViewMode::Follow),
            _ => None,
        }
    }
}

impl<T: Field + Copy + Default, const N: usize> Field for [T; N] {
    const SIZE: usize = T::SIZE * N;
    fn write(&self, out: &mut [u8]) {
        for (i, v) in self.iter().enumerate() {
            write_at(out, i * T::SIZE, v);
        }
    }
    fn read(b: &[u8]) -> Option<Self> {
        let mut a = [T::default(); N];
        for (i, v) in a.iter_mut().enumerate() {
            *v = read_at(b, i * T::SIZE)?;
        }
        Some(a)
    }
}

/// libstdc++'s `std::optional<Quat>`: the payload (padding while disengaged), then the engaged flag and 3 bytes of
/// padding.
impl Field for Option<Quat> {
    const SIZE: usize = 20;
    fn write(&self, out: &mut [u8]) {
        if let Some(q) = self {
            write_at(out, 0, q);
        }
        write_at(out, 16, &self.is_some());
    }
    fn read(b: &[u8]) -> Option<Self> {
        let engaged: bool = read_at(b, 16)?;
        Some(if engaged { Some(read_at(b, 0)?) } else { None })
    }
}

/// A struct's C++ layout: its size, and each member at its offset (x86-64 GCC, checked against `offsetof` by
/// golden/continuity.cpp's dump). Members not listed keep their default.
macro_rules! layout {
    ($t:ty, $size:expr, $($f:ident @ $off:expr),* $(,)?) => {
        impl Field for $t {
            const SIZE: usize = $size;
            fn write(&self, out: &mut [u8]) {
                $(write_at(out, $off, &self.$f);)*
            }
            fn read(b: &[u8]) -> Option<Self> {
                let mut v = <$t>::default();
                $(v.$f = read_at(b, $off)?;)*
                Some(v)
            }
        }
    };
}

layout!(Quat, 16, x @ 0, y @ 4, z @ 8, w @ 12);
layout!(Tuning, 8, transition_ms @ 0, follow_omega @ 4);
layout!(PoseState, 224,
    stale_ms @ 0, center @ 8, up @ 28, up_cov @ 40, last @ 76, last_device_ms @ 92, last_change_ms @ 96,
    lost_at_ms @ 104, settling_from_ms @ 112, level_reference @ 120, level_turn @ 124, levelled_deg @ 140,
    tracking @ 144, recenter_pending @ 145, dropped @ 146, view @ 148, relative @ 184, samples @ 200,
    lost_events @ 208, reanchors @ 216);
layout!(Stabilizer, 96,
    strength @ 0, valid @ 4, ms @ 8, speed @ 16, prediction_gain @ 24, previous @ 28, output @ 44, state @ 64);
layout!(ModeController, 144,
    tuning @ 0, mode @ 8, anchor @ 12, from @ 28, follow @ 44, pinned @ 60, held @ 76, aim @ 92, started_ms @ 112,
    last_ms @ 120, velocity @ 128, transitioning @ 140, holding @ 141);
layout!(EdgePan, 44,
    active @ 0, from @ 4, to @ 8, side @ 12, exit @ 16, entry @ 20, peek @ 24, cross @ 28, turn @ 32, way @ 36,
    zoom @ 40);
layout!(Landing, 56,
    active @ 0, start_ms @ 8, left @ 16, way_from @ 20, way_speed @ 24, way_ms @ 28, left_from @ 32,
    left_speed @ 36, left_now @ 40, until @ 44, border @ 48, border_now @ 52);
layout!(Switch, 56,
    active @ 0, queued @ 1, start_ms @ 8, zoom @ 16, out_ms @ 20, from @ 24, to @ 28, next @ 32, center @ 36,
    next_center @ 37, zoom_now @ 40, look_now @ 44, turn_now @ 48);
layout!(LandedTurn, 16, from @ 0, to @ 4, centred @ 8, serial @ 12);

/// `continuity_detail::Writer` / `Reader`: one visit of the values, either way. `Err` is the C++ `runtime_error`.
pub(crate) trait Archive {
    fn one<T: Field>(&mut self, v: &mut T) -> Result<(), ()>;
    fn vec<T: Field + Default>(&mut self, v: &mut Vec<T>) -> Result<(), ()>;
}

struct Writer {
    bytes: Vec<u8>,
    /// What padding is written as.
    fill: u8,
}

impl Archive for Writer {
    fn one<T: Field>(&mut self, v: &mut T) -> Result<(), ()> {
        let at = self.bytes.len();
        self.bytes.resize(at + T::SIZE, self.fill);
        v.write(&mut self.bytes[at..]);
        Ok(())
    }
    fn vec<T: Field + Default>(&mut self, v: &mut Vec<T>) -> Result<(), ()> {
        let mut n = u32::try_from(v.len()).map_err(|_| ())?;
        if n > MAX_ITEMS {
            return Err(());
        }
        self.one(&mut n)?;
        v.iter_mut().try_for_each(|x| self.one(x))
    }
}

struct Reader<'a> {
    bytes: &'a [u8],
    offset: usize,
}

impl Archive for Reader<'_> {
    fn one<T: Field>(&mut self, v: &mut T) -> Result<(), ()> {
        let end = self.offset + T::SIZE;
        if end > self.bytes.len() {
            return Err(());
        }
        *v = T::read(&self.bytes[self.offset..end]).ok_or(())?;
        self.offset = end;
        Ok(())
    }
    fn vec<T: Field + Default>(&mut self, v: &mut Vec<T>) -> Result<(), ()> {
        let mut n = 0u32;
        self.one(&mut n)?;
        if n > MAX_ITEMS {
            return Err(());
        }
        v.clear();
        v.resize_with(n as usize, T::default);
        v.iter_mut().try_for_each(|x| self.one(x))
    }
}

/// FNV-1a 64 (`continuity_detail::hash`).
fn hash(bytes: &[u8]) -> u64 {
    let mut h: u64 = 14695981039346656037;
    for &c in bytes {
        h ^= u64::from(c);
        h = h.wrapping_mul(1099511628211);
    }
    h
}

fn view_checkpoint<A: Archive>(v: &mut ViewAnimator, a: &mut A) -> Result<(), ()> {
    a.one(&mut v.duration)?;
    a.one(&mut v.turn_ms)?;
    a.one(&mut v.pan)?;
    a.one(&mut v.pan_on)?;
    a.one(&mut v.pan_landing)?;
    a.one(&mut v.pan_overview)?;
    a.one(&mut v.pan_land_ms)?;
    a.one(&mut v.pan_peek)?;
    a.one(&mut v.pan_cross)?;
    a.one(&mut v.pan_turn)?;
    a.one(&mut v.pan_zoom)?;
    a.one(&mut v.pan_zoom_speed)?;
    a.one(&mut v.pan_ms)?;
    a.one(&mut v.pan_seen_ms)?;
    a.one(&mut v.land)?;
    a.one(&mut v.start_ms)?;
    a.one(&mut v.spacing_start_ms)?;
    a.one(&mut v.look_start_ms)?;
    a.one(&mut v.t)?;
    a.one(&mut v.spacing_t)?;
    a.one(&mut v.look_t)?;
    a.one(&mut v.from_zoom)?;
    a.one(&mut v.to_zoom)?;
    a.one(&mut v.from_spacing)?;
    a.one(&mut v.to_spacing)?;
    a.one(&mut v.from_look)?;
    a.one(&mut v.from_turn)?;
    a.vec(&mut v.landed)?;
    a.one(&mut v.serial)?;
    a.one(&mut v.turn_centred)?;
    a.one(&mut v.to_selected)?;
    a.one(&mut v.shown)?;
    a.one(&mut v.look_from)?;
    a.one(&mut v.look_to)?;
    a.one(&mut v.switch)
}

/// `xr::ViewContinuity`: the view state one renderer hands the next.
#[derive(Clone, Debug)]
pub struct ViewContinuity {
    pub pose: PoseState,
    pub stabilization: Stabilizer,
    pub modes: ModeController,
    pub view: ViewAnimator,
    pub maximized: bool,
    pub rolls: [i32; MAX_DESKTOPS],
    pub last_drawn: Mat3,
    pub centre_valid: bool,
    pub centre_serial: u32,
    pub centre_dir: [f32; 2],
    pub saved_ms: f64,
}

impl Default for ViewContinuity {
    fn default() -> Self {
        Self {
            pose: PoseState::default(),
            stabilization: Stabilizer::default(),
            modes: ModeController::default(),
            view: ViewAnimator::default(),
            maximized: false,
            rolls: [0; MAX_DESKTOPS],
            last_drawn: IDENTITY,
            centre_valid: false,
            centre_serial: 0,
            centre_dir: [0.0; 2],
            saved_ms: 0.0,
        }
    }
}

impl ViewContinuity {
    fn checkpoint<A: Archive>(&mut self, a: &mut A) -> Result<(), ()> {
        a.one(&mut self.pose)?;
        a.one(&mut self.stabilization)?;
        a.one(&mut self.modes)?;
        a.one(&mut self.maximized)?;
        a.one(&mut self.rolls)?;
        a.one(&mut self.last_drawn)?;
        a.one(&mut self.centre_valid)?;
        a.one(&mut self.centre_serial)?;
        a.one(&mut self.centre_dir)?;
        a.one(&mut self.saved_ms)?;
        view_checkpoint(&mut self.view, a)
    }

    /// The blob, or `None` where the C++ throws (more than 64 landed turns).
    pub fn encode(&self) -> Option<Vec<u8>> {
        let payload = self.payload(0)?;
        let mut bytes = MAGIC.to_vec();
        bytes.extend_from_slice(&hash(&payload).to_ne_bytes());
        bytes.extend_from_slice(&payload);
        Some(bytes)
    }

    fn payload(&self, fill: u8) -> Option<Vec<u8>> {
        let mut a = Writer { bytes: Vec::new(), fill };
        self.clone().checkpoint(&mut a).ok()?;
        Some(a.bytes)
    }

    /// For each byte of `encode()`, whether it is padding (or a disengaged optional's payload): bytes the C++ writes
    /// as whatever memory held, so a C++ blob matches a Rust one everywhere else. The header (magic and hash) is not.
    #[doc(hidden)]
    pub fn padding(&self) -> Option<Vec<bool>> {
        let (zero, ones) = (self.payload(0)?, self.payload(0xff)?);
        Some([false; 16].into_iter().chain(zero.iter().zip(&ones).map(|(a, b)| a != b)).collect())
    }

    /// Replaces this state with the blob's, all or nothing. False for anything but a whole, intact blob.
    pub fn decode(&mut self, bytes: &[u8]) -> bool {
        if bytes.len() < 16 || bytes.len() > MAX_BYTES || &bytes[..8] != MAGIC {
            return false;
        }
        let payload = &bytes[16..];
        if u64::from_ne_bytes(bytes[8..16].try_into().unwrap()) != hash(payload) {
            return false;
        }
        let mut next = ViewContinuity::default();
        let mut a = Reader { bytes: payload, offset: 0 };
        if next.checkpoint(&mut a).is_err() || a.offset != payload.len() || !next.saved_ms.is_finite() {
            return false;
        }
        *self = next;
        true
    }

    /// Moves every clock on by the time no renderer ran, so animations carry on where they were.
    pub fn resume(&mut self, now_ms: f64) {
        let gap = 0.0f64.max(now_ms - self.saved_ms);
        self.view.pause_clock(gap);
        if self.modes.started_ms >= 0.0 {
            self.modes.started_ms += gap;
        }
        if self.modes.last_ms >= 0.0 {
            self.modes.last_ms += gap;
        }
        // This is a display-only switch with the same driver/reference. Do not interpret time spent with no renderer
        // as a tracking recalibration.
        self.pose.last_change_ms += gap;
        self.pose.lost_at_ms += gap;
        if self.pose.settling_from_ms >= 0.0 {
            self.pose.settling_from_ms += gap;
        }
        self.saved_ms = now_ms;
    }
}

/// `Presentation::save_continuity`: the snapshot into the (sealed, regular) file `fd`. Invalidated first, so a short
/// write never leaves a stale snapshot to restore; any failure leaves the file empty.
pub fn save(fd: BorrowedFd<'_>, c: &ViewContinuity) {
    let Some(bytes) = c.encode() else {
        let _ = rustix::fs::ftruncate(fd, 0);
        return;
    };
    if rustix::fs::ftruncate(fd, 0).is_ok() && rustix::io::pwrite(fd, &bytes, 0).ok() != Some(bytes.len()) {
        let _ = rustix::fs::ftruncate(fd, 0);
    }
}

/// `Presentation::restore_continuity`: the snapshot in `fd`, resumed at `now_ms`, or `None`. Consumed once (the file
/// is emptied before decoding): if this renderer crashes before writing a new snapshot, a later retry must not roll
/// back to an older view.
pub fn restore(fd: BorrowedFd<'_>, now_ms: f64) -> Option<ViewContinuity> {
    let size = rustix::fs::fstat(fd).ok()?.st_size;
    if size <= 0 || size > MAX_BYTES as i64 {
        return None;
    }
    let mut bytes = vec![0u8; size as usize];
    if rustix::io::pread(fd, &mut bytes, 0).ok()? != bytes.len() {
        return None;
    }
    rustix::fs::ftruncate(fd, 0).ok()?;
    let mut c = ViewContinuity::default();
    if !c.decode(&bytes) {
        return None;
    }
    c.resume(now_ms);
    Some(c)
}
