//! The AI depth engine's contract (spike/include/xr/depth.hpp; docs/depth-engine.md). The engine is a separate process
//! (tools/depth_engine), so a crash there can never take the renderer with it: if [`DepthShm::engine_alive`] goes false
//! the renderer draws the desktop flat.
//!
//! One shared-memory file ("XRD1") carries a channel per XR desktop. Each channel has two directions:
//!   input   renderer -> engine   a desktop, already shrunk by the GPU ([`crate::depth_feed`]), RGBA8 or BGRA8
//!   output  engine -> renderer   a depth map, R16F or R32F: normalised disparity, 0 = far, 1 = near
//! The layout is the C++ header's byte for byte, and tools/depth_engine/shm.py mirrors it too.
//!
//! Each direction is one writer, one reader and [`DEPTH_SLOTS`] slots behind a seqlock. The writer never waits for the
//! reader; the reader copies a slot and rereads its sequence number. Every header field is accessed atomically (relaxed
//! inside the seqlock), so a racing peer can only make a fetch retry, never tear a field.

use std::sync::atomic::{AtomicU32, AtomicU64, Ordering, fence};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
#[repr(u32)]
pub enum DepthFormat {
    None = 0,
    Rgba8 = 1,
    Bgra8 = 2,
    R16F = 3,
    R32F = 4,
}

impl DepthFormat {
    pub fn from_u32(v: u32) -> Self {
        match v {
            1 => Self::Rgba8,
            2 => Self::Bgra8,
            3 => Self::R16F,
            4 => Self::R32F,
            _ => Self::None,
        }
    }
    pub fn bytes_per_pixel(self) -> usize {
        match self {
            Self::Rgba8 | Self::Bgra8 | Self::R32F => 4,
            Self::R16F => 2,
            Self::None => 0,
        }
    }
}

/// Output flag: the smoothing was reset, so a renderer may snap instead of ease.
pub const DEPTH_SCENE_CUT: u32 = 1;
/// Output flag: nothing to separate (a blank frame); every pixel is 0.5.
pub const DEPTH_FLAT: u32 = 2;

/// The model takes multiples of its 14 px patch.
pub const DEPTH_PATCH: i32 = 14;
/// A depth map is at least 256 px tall; 266 is the first multiple of 14 above that.
pub const DEPTH_MIN_TALL: i32 = 266;
pub const DEPTH_LONG_HIGH: i32 = 518;
pub const DEPTH_LONG_BALANCED: i32 = 420;
/// The smallest depth map the stereo pass takes.
pub const DEPTH_MIN_HEIGHT: i32 = 256;

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct DepthSize {
    pub w: i32,
    pub h: i32,
}

fn round_patch(v: f64) -> i32 {
    ((v / f64::from(DEPTH_PATCH)).round() as i32).max(1) * DEPTH_PATCH
}

/// The input size for a desktop: keeps its shape, long side near long_side, never under DEPTH_MIN_TALL.
pub fn depth_input_size(src_w: i32, src_h: i32, long_side: i32) -> DepthSize {
    if src_w <= 0 || src_h <= 0 || long_side <= 0 {
        return DepthSize::default();
    }
    let aspect = f64::from(src_w) / f64::from(src_h);
    let long = f64::from(long_side);
    let (mut w, mut h) = if aspect >= 1.0 {
        (round_patch(long), round_patch(long / aspect))
    } else {
        (round_patch(long * aspect), round_patch(long))
    };
    if h < DEPTH_MIN_TALL {
        h = DEPTH_MIN_TALL;
        w = round_patch(f64::from(h) * aspect);
    }
    DepthSize { w, h }
}

/// Where a depth map's values are. Row 0 is the frame's top row.
#[derive(Clone, Copy, Debug)]
pub enum DepthData<'a> {
    /// R32F rows, `stride` bytes apart. Read only during the call.
    Float(&'a [f32]),
    /// R16F rows (what the engine sends), uploaded as they are.
    Half(&'a [u16]),
    /// A GL texture in the renderer's context, R16F or R32F.
    Texture(glow::Texture),
}

/// What the stereo pass reads (docs/stereo-render.md). Values are "nearness": larger is nearer. `lo` / `hi` are the
/// farthest and nearest values of this frame, so a model's arbitrary scale needs no conversion. NaN and infinity are
/// drawn flat.
#[derive(Clone, Copy, Debug)]
pub struct DepthInput<'a> {
    pub w: i32,
    pub h: i32,
    /// The capture frame this was estimated from.
    pub source_seq: u64,
    pub data: DepthData<'a>,
    /// Bytes between rows of `data` (0: tightly packed).
    pub stride: usize,
    pub lo: f32,
    pub hi: f32,
    pub reset_history: bool,
    /// This frame's screen plane (nearness, 0..1). Negative: StereoParams::convergence.
    pub convergence: f32,
}

impl<'a> DepthInput<'a> {
    pub fn new(w: i32, h: i32, source_seq: u64, data: DepthData<'a>) -> Self {
        Self {
            w,
            h,
            source_seq,
            data,
            stride: 0,
            lo: 0.0,
            hi: 1.0,
            reset_history: false,
            convergence: -1.0,
        }
    }
}

/// The C++ depth_input_valid, plus the check C++ leaves to its caller: a slice holds every row it claims.
pub fn depth_input_valid(f: &DepthInput) -> bool {
    if f.w <= 0 || f.h < DEPTH_MIN_HEIGHT {
        return false;
    }
    let (len, size) = match f.data {
        DepthData::Texture(_) => return true,
        DepthData::Float(p) => (p.len(), 4),
        DepthData::Half(p) => (p.len(), 2),
    };
    let w = f.w as usize;
    if f.stride != 0 && (f.stride < w * size || f.stride % size != 0) {
        return false;
    }
    let row = if f.stride == 0 { w } else { f.stride / size };
    len >= (f.h as usize - 1) * row + w
}

// ---------------------------------------------------------------------------------------------------------------------
// The shared-memory layout

/// 'XRD1'
pub const DEPTH_MAGIC: u32 = 0x3144_5258;
pub const DEPTH_VERSION: u32 = 1;
pub const DEPTH_SLOTS: u32 = 3;
pub const DEPTH_MAX_CHANNELS: u32 = 16;
pub const DEPTH_HEADER_BYTES: usize = 4096;
pub const DEPTH_CHANNEL_HEADER_BYTES: usize = 320;
/// DepthChannelHeader::control bits, set by the renderer.
pub const DEPTH_ENABLED: u32 = 1;
pub const DEPTH_FOCUSED: u32 = 2;

// struct DepthShmHeader (64 bytes)
pub const OFF_MAGIC: usize = 0;
pub const OFF_VERSION: usize = 4;
pub const OFF_CHANNELS: usize = 8;
pub const OFF_SLOTS: usize = 12;
pub const OFF_IN_W: usize = 16;
pub const OFF_IN_H: usize = 20;
pub const OFF_OUT_W: usize = 24;
pub const OFF_OUT_H: usize = 28;
pub const OFF_CHANNEL_STRIDE: usize = 32;
pub const OFF_TOTAL_BYTES: usize = 40;
pub const OFF_ENGINE_NS: usize = 48;
pub const OFF_ENGINE_PID: usize = 56;
pub const SHM_HEADER_STRUCT_BYTES: usize = 64;
// struct DepthSlotMeta (48 bytes)
pub const META_BYTES: usize = 48;
const META_SEQ: usize = 0;
const META_W: usize = 4;
const META_H: usize = 8;
const META_FORMAT: usize = 12;
pub const META_SOURCE_SEQ: usize = 16;
const META_SOURCE_NS: usize = 24;
const META_DONE_NS: usize = 32;
pub const META_FLAGS: usize = 40;
// struct DepthChannelHeader (320 bytes)
pub const CHAN_LATEST_IN: usize = 0;
pub const CHAN_LATEST_OUT: usize = 4;
pub const CHAN_CONTROL: usize = 8;
pub const CHAN_META_IN: usize = 16;
pub const CHAN_META_OUT: usize = CHAN_META_IN + DEPTH_SLOTS as usize * META_BYTES;
const _: () = assert!(CHAN_META_OUT + DEPTH_SLOTS as usize * META_BYTES + 16 == DEPTH_CHANNEL_HEADER_BYTES);

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct DepthShmConfig {
    pub channels: u32,
    pub in_w: i32,
    pub in_h: i32,
    pub out_w: i32,
    pub out_h: i32,
}

impl Default for DepthShmConfig {
    fn default() -> Self {
        Self {
            channels: 4,
            in_w: DEPTH_LONG_HIGH,
            in_h: DEPTH_LONG_HIGH,
            out_w: DEPTH_LONG_HIGH,
            out_h: DEPTH_LONG_HIGH,
        }
    }
}

/// A run's memory (tools/native_spike/immersive.py DepthMemory makes it this size): one size for every desktop.
pub const DEPTH_LINK_SHM: DepthShmConfig = DepthShmConfig {
    channels: 4,
    in_w: DEPTH_LONG_BALANCED,
    in_h: DEPTH_MIN_TALL,
    out_w: DEPTH_LONG_BALANCED,
    out_h: DEPTH_MIN_TALL,
};

const fn align_page(n: usize) -> usize {
    n.div_ceil(4096) * 4096
}
pub const fn depth_channel_stride(c: &DepthShmConfig) -> usize {
    let slots = DEPTH_SLOTS as usize;
    align_page(
        DEPTH_CHANNEL_HEADER_BYTES
            + slots * c.in_w as usize * c.in_h as usize * 4
            + slots * c.out_w as usize * c.out_h as usize * 4,
    )
}
pub const fn depth_shm_bytes(c: &DepthShmConfig) -> usize {
    DEPTH_HEADER_BYTES + depth_channel_stride(c) * c.channels as usize
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Side {
    Input,
    Output,
}

/// A fetched or published frame. `pixels` are `w * h` tightly packed pixels of `format`, row 0 the frame's top.
#[derive(Clone, Copy, Debug)]
pub struct DepthFrame<'a> {
    pub w: i32,
    pub h: i32,
    /// The capture frame this came from; starts at 1 (0 means "none" to fetch).
    pub source_seq: u64,
    /// CLOCK_MONOTONIC when that frame was captured.
    pub source_ns: u64,
    /// CLOCK_MONOTONIC when the map was finished.
    pub done_ns: u64,
    pub format: DepthFormat,
    pub flags: u32,
    pub pixels: &'a [u8],
}

/// A view of the shared file, mapped by the caller. It holds no state of its own beyond the mapping.
#[derive(Debug)]
pub struct DepthShm {
    base: *mut u8,
    bytes: usize,
}

impl DepthShm {
    /// The renderer makes the file: `bytes` must be at least depth_shm_bytes(cfg) and zeroed.
    ///
    /// # Safety
    /// `base` points to `bytes` writable bytes, 8-byte aligned, that stay mapped for as long as the returned view is
    /// used; only this view's peers (another DepthShm, the engine) write them meanwhile.
    pub unsafe fn create(base: *mut u8, bytes: usize, cfg: &DepthShmConfig) -> Option<Self> {
        if base.is_null()
            || base as usize % 8 != 0
            || cfg.channels == 0
            || cfg.channels > DEPTH_MAX_CHANNELS
            || cfg.in_w <= 0
            || cfg.in_h <= 0
            || cfg.out_w <= 0
            || cfg.out_h <= 0
            || bytes < depth_shm_bytes(cfg)
        {
            return None;
        }
        let s = Self { base, bytes };
        let r = Ordering::Relaxed;
        s.u32(OFF_VERSION).store(DEPTH_VERSION, r);
        s.u32(OFF_CHANNELS).store(cfg.channels, r);
        s.u32(OFF_SLOTS).store(DEPTH_SLOTS, r);
        s.u32(OFF_IN_W).store(cfg.in_w as u32, r);
        s.u32(OFF_IN_H).store(cfg.in_h as u32, r);
        s.u32(OFF_OUT_W).store(cfg.out_w as u32, r);
        s.u32(OFF_OUT_H).store(cfg.out_h as u32, r);
        s.u64(OFF_CHANNEL_STRIDE)
            .store(depth_channel_stride(cfg) as u64, r);
        s.u64(OFF_TOTAL_BYTES).store(depth_shm_bytes(cfg) as u64, r);
        s.u32(OFF_MAGIC).store(DEPTH_MAGIC, Ordering::Release); // last: the file is ready
        Some(s)
    }

    /// Refuses anything that is not a whole file of this version (a stale file, a half-made one, a short mapping).
    ///
    /// # Safety
    /// As for [`DepthShm::create`].
    pub unsafe fn attach(base: *mut u8, bytes: usize) -> Option<Self> {
        if base.is_null() || base as usize % 8 != 0 || bytes < SHM_HEADER_STRUCT_BYTES {
            return None;
        }
        let s = Self { base, bytes };
        if s.u32(OFF_MAGIC).load(Ordering::Acquire) != DEPTH_MAGIC {
            return None;
        }
        let r = Ordering::Relaxed;
        let channels = s.u32(OFF_CHANNELS).load(r);
        if s.u32(OFF_VERSION).load(r) != DEPTH_VERSION
            || s.u32(OFF_SLOTS).load(r) != DEPTH_SLOTS
            || channels == 0
            || channels > DEPTH_MAX_CHANNELS
        {
            return None;
        }
        let dim = |off| s.u32(off).load(r) as i32;
        let cfg = DepthShmConfig {
            channels,
            in_w: dim(OFF_IN_W),
            in_h: dim(OFF_IN_H),
            out_w: dim(OFF_OUT_W),
            out_h: dim(OFF_OUT_H),
        };
        if cfg.in_w <= 0 || cfg.in_h <= 0 || cfg.out_w <= 0 || cfg.out_h <= 0 {
            return None;
        }
        let total = s.u64(OFF_TOTAL_BYTES).load(r);
        if total != depth_shm_bytes(&cfg) as u64
            || s.u64(OFF_CHANNEL_STRIDE).load(r) != depth_channel_stride(&cfg) as u64
            || (bytes as u64) < total
        {
            return None;
        }
        Some(s)
    }

    fn u32(&self, off: usize) -> &AtomicU32 {
        debug_assert!(off + 4 <= self.bytes && off % 4 == 0);
        // SAFETY: in the mapping (create/attach checked its size against every offset used), aligned, and only ever
        // accessed atomically.
        unsafe { AtomicU32::from_ptr(self.base.add(off).cast()) }
    }
    fn u64(&self, off: usize) -> &AtomicU64 {
        debug_assert!(off + 8 <= self.bytes && off % 8 == 0);
        // SAFETY: as for u32.
        unsafe { AtomicU64::from_ptr(self.base.add(off).cast()) }
    }

    pub fn channels(&self) -> u32 {
        self.u32(OFF_CHANNELS).load(Ordering::Relaxed)
    }
    fn dims(&self, side: Side) -> (u32, u32) {
        let r = Ordering::Relaxed;
        match side {
            Side::Input => (self.u32(OFF_IN_W).load(r), self.u32(OFF_IN_H).load(r)),
            Side::Output => (self.u32(OFF_OUT_W).load(r), self.u32(OFF_OUT_H).load(r)),
        }
    }
    fn chan(&self, i: u32) -> usize {
        DEPTH_HEADER_BYTES + i as usize * self.u64(OFF_CHANNEL_STRIDE).load(Ordering::Relaxed) as usize
    }
    fn slot_bytes(&self, side: Side) -> usize {
        let (w, h) = self.dims(side);
        w as usize * h as usize * 4
    }
    fn latest(&self, channel: u32, side: Side) -> usize {
        self.chan(channel)
            + match side {
                Side::Input => CHAN_LATEST_IN,
                Side::Output => CHAN_LATEST_OUT,
            }
    }
    fn meta(&self, channel: u32, side: Side, slot: u32) -> usize {
        self.chan(channel)
            + match side {
                Side::Input => CHAN_META_IN,
                Side::Output => CHAN_META_OUT,
            }
            + slot as usize * META_BYTES
    }
    fn slot_pixels(&self, channel: u32, side: Side, slot: u32) -> *mut u8 {
        let mut off = self.chan(channel) + DEPTH_CHANNEL_HEADER_BYTES;
        off += match side {
            Side::Input => slot as usize * self.slot_bytes(Side::Input),
            Side::Output => {
                DEPTH_SLOTS as usize * self.slot_bytes(Side::Input)
                    + slot as usize * self.slot_bytes(Side::Output)
            }
        };
        // SAFETY: inside the mapping (attach checked total_bytes against the size).
        unsafe { self.base.add(off) }
    }

    /// Publish a frame. False (nothing written) for a channel or size this file has no room for, or a frame too short.
    pub fn publish(&self, side: Side, channel: u32, f: &DepthFrame) -> bool {
        let bpp = f.format.bytes_per_pixel();
        if channel >= self.channels() || f.w <= 0 || f.h <= 0 || bpp == 0 {
            return false;
        }
        let (max_w, max_h) = self.dims(side);
        let bytes = f.w as usize * f.h as usize * bpp;
        if f.w as u32 > max_w || f.h as u32 > max_h || f.pixels.len() < bytes {
            return false;
        }
        let r = Ordering::Relaxed;
        let latest = self.u32(self.latest(channel, side));
        let slot = (latest.load(r) + 1) % DEPTH_SLOTS;
        let m = self.meta(channel, side, slot);
        let seq = self.u32(m + META_SEQ);
        let s = seq.load(r);
        seq.store(s.wrapping_add(1), r);
        fence(Ordering::Release);
        // SAFETY: `bytes` fits the slot (checked against the file's largest frame); the source is a separate buffer.
        unsafe {
            std::ptr::copy_nonoverlapping(
                f.pixels.as_ptr(),
                self.slot_pixels(channel, side, slot),
                bytes,
            )
        };
        self.u32(m + META_W).store(f.w as u32, r);
        self.u32(m + META_H).store(f.h as u32, r);
        self.u32(m + META_FORMAT).store(f.format as u32, r);
        self.u64(m + META_SOURCE_SEQ).store(f.source_seq, r);
        self.u64(m + META_SOURCE_NS).store(f.source_ns, r);
        self.u64(m + META_DONE_NS).store(f.done_ns, r);
        self.u32(m + META_FLAGS).store(f.flags, r);
        seq.store(s.wrapping_add(2), Ordering::Release);
        latest.store(slot, Ordering::Release);
        true
    }

    /// The newest frame into `storage`, if there is one with a source_seq other than `seen`. Never blocks.
    pub fn fetch<'s>(
        &self,
        side: Side,
        channel: u32,
        storage: &'s mut Vec<u8>,
        seen: u64,
    ) -> Option<DepthFrame<'s>> {
        if channel >= self.channels() {
            return None;
        }
        let r = Ordering::Relaxed;
        for _ in 0..4 {
            let slot = self
                .u32(self.latest(channel, side))
                .load(Ordering::Acquire);
            if slot >= DEPTH_SLOTS {
                return None;
            }
            let m = self.meta(channel, side, slot);
            let seq = self.u32(m + META_SEQ);
            let before = seq.load(Ordering::Acquire);
            if before == 0 {
                return None;
            }
            if before & 1 != 0 {
                continue;
            }
            let (w, h) = (
                self.u32(m + META_W).load(r) as i32,
                self.u32(m + META_H).load(r) as i32,
            );
            let format = DepthFormat::from_u32(self.u32(m + META_FORMAT).load(r));
            let source_seq = self.u64(m + META_SOURCE_SEQ).load(r);
            let (source_ns, done_ns, flags) = (
                self.u64(m + META_SOURCE_NS).load(r),
                self.u64(m + META_DONE_NS).load(r),
                self.u32(m + META_FLAGS).load(r),
            );
            if source_seq == seen {
                if seq.load(Ordering::Acquire) == before {
                    return None;
                }
                continue;
            }
            let bpp = format.bytes_per_pixel();
            if bpp == 0 || w <= 0 || h <= 0 {
                continue; // read mid-write: the sequence recheck would fail too
            }
            let bytes = w as usize * h as usize * bpp;
            if bytes > self.slot_bytes(side) {
                continue;
            }
            storage.resize(bytes, 0);
            // SAFETY: `bytes` fits the slot; storage is ours.
            unsafe {
                std::ptr::copy_nonoverlapping(
                    self.slot_pixels(channel, side, slot),
                    storage.as_mut_ptr(),
                    bytes,
                )
            };
            fence(Ordering::Acquire);
            if seq.load(r) != before {
                continue;
            }
            return Some(DepthFrame {
                w,
                h,
                source_seq,
                source_ns,
                done_ns,
                format,
                flags,
                pixels: &storage[..],
            });
        }
        None
    }

    pub fn set_control(&self, channel: u32, control: u32) {
        if channel < self.channels() {
            self.u32(self.chan(channel) + CHAN_CONTROL)
                .store(control, Ordering::Release);
        }
    }
    pub fn control(&self, channel: u32) -> u32 {
        if channel < self.channels() {
            self.u32(self.chan(channel) + CHAN_CONTROL)
                .load(Ordering::Acquire)
        } else {
            0
        }
    }
    pub fn heartbeat(&self, now_ns: u64, pid: u32) {
        self.u32(OFF_ENGINE_PID).store(pid, Ordering::Relaxed);
        self.u64(OFF_ENGINE_NS).store(now_ns, Ordering::Release);
    }
    /// False when the engine never started or has been silent for timeout_ns: draw flat.
    pub fn engine_alive(&self, now_ns: u64, timeout_ns: u64) -> bool {
        let beat = self.u64(OFF_ENGINE_NS).load(Ordering::Acquire);
        beat != 0 && (now_ns <= beat || now_ns - beat <= timeout_ns)
    }
}

/// The engine's default silence before 3D draws flat.
pub const DEPTH_ENGINE_TIMEOUT_NS: u64 = 500_000_000;

/// IEEE half -> float, subnormals included.
pub fn depth_half_to_float(h: u16) -> f32 {
    let sign = u32::from(h >> 15) << 31;
    let exp = u32::from((h >> 10) & 0x1f);
    let mant = u32::from(h & 0x3ff);
    let bits = if exp == 0 {
        if mant == 0 {
            sign
        } else {
            // subnormal: normalise
            let mut e = 127 - 15 + 1;
            let mut m = mant;
            while m & 0x400 == 0 {
                m <<= 1;
                e -= 1;
            }
            sign | (e << 23) | ((m & 0x3ff) << 13)
        }
    } else if exp == 31 {
        sign | 0x7f80_0000 | (mant << 13)
    } else {
        sign | ((exp + 127 - 15) << 23) | (mant << 13)
    };
    f32::from_bits(bits)
}

/// The nearness at percentile `pct` of a half-float map (every 4th pixel each way; negatives count as 0): the screen
/// plane the depth link eases towards. 0.5 for an empty or short map.
pub fn depth_anchor(half: &[u16], w: i32, h: i32, pct: f32) -> f32 {
    if w <= 0 || h <= 0 || half.len() < w as usize * h as usize {
        return 0.5;
    }
    let mut samples: Vec<u16> = Vec::with_capacity((w as usize).div_ceil(4) * (h as usize).div_ceil(4));
    for y in (0..h as usize).step_by(4) {
        for x in (0..w as usize).step_by(4) {
            let v = half[y * w as usize + x];
            samples.push(if v & 0x8000 != 0 { 0 } else { v });
        }
    }
    let n = samples.len();
    let p = pct.clamp(0.0, 100.0);
    let rank = (p / 100.0 * (n - 1) as f32) as usize;
    // Non-negative halves order as their bit patterns (NaN above infinity, as nth_element on uint16 sees them).
    let (_, v, _) = samples.select_nth_unstable(rank.min(n - 1));
    let f = depth_half_to_float(*v);
    if f.is_finite() { f.clamp(0.0, 1.0) } else { 0.5 }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sizes_match_the_header() {
        // depth.hpp: depth_input_size and the shared file sizes.
        assert_eq!(depth_input_size(1920, 1200, DEPTH_LONG_HIGH), DepthSize { w: 518, h: 322 });
        assert_eq!(depth_input_size(1920, 1200, DEPTH_LONG_BALANCED), DepthSize { w: 420, h: 266 });
        assert_eq!(depth_input_size(1200, 1920, DEPTH_LONG_HIGH), DepthSize { w: 322, h: 518 });
        assert_eq!(depth_input_size(0, 1, 1), DepthSize::default());
        let stride = depth_channel_stride(&DEPTH_LINK_SHM);
        assert_eq!(stride % 4096, 0);
        assert!(stride >= 320 + 6 * 420 * 266 * 4);
        assert_eq!(depth_shm_bytes(&DEPTH_LINK_SHM), 4096 + 4 * stride);
    }

    #[test]
    fn halves() {
        assert_eq!(depth_half_to_float(0x3c00), 1.0);
        assert_eq!(depth_half_to_float(0x3800), 0.5);
        assert_eq!(depth_half_to_float(0xc000), -2.0);
        assert_eq!(depth_half_to_float(0x0001), 2f32.powi(-24));
        assert_eq!(depth_half_to_float(0x03ff), 1023.0 * 2f32.powi(-24));
        assert!(depth_half_to_float(0x7c00).is_infinite());
        assert!(depth_half_to_float(0x7e00).is_nan());
    }

    #[test]
    fn anchor() {
        let (w, h) = (16, 16);
        let mut map = vec![0x3800u16; w * h]; // 0.5
        assert_eq!(depth_anchor(&map, w as i32, h as i32, 92.0), 0.5);
        map.iter_mut().enumerate().for_each(|(i, v)| {
            *v = if i % w < 8 { 0x0000 } else { 0x3c00 };
        });
        assert_eq!(depth_anchor(&map, w as i32, h as i32, 92.0), 1.0);
        assert_eq!(depth_anchor(&map, w as i32, h as i32, 0.0), 0.0);
        map.fill(0xbc00); // -1: counts as 0
        assert_eq!(depth_anchor(&map, w as i32, h as i32, 50.0), 0.0);
        assert_eq!(depth_anchor(&map[..10], w as i32, h as i32, 50.0), 0.5);
    }

    #[test]
    fn input_validity() {
        let px = vec![0.0f32; 300 * 256];
        let f = DepthInput::new(300, 256, 1, DepthData::Float(&px));
        assert!(depth_input_valid(&f));
        assert!(!depth_input_valid(&DepthInput { h: 255, ..f }));
        assert!(!depth_input_valid(&DepthInput { stride: 1204, ..f })); // rows would run past the slice
        assert!(!depth_input_valid(&DepthInput { stride: 8, ..f }));
        let half = vec![0u16; 300 * 256];
        assert!(depth_input_valid(&DepthInput::new(300, 256, 1, DepthData::Half(&half))));
        assert!(!depth_input_valid(&DepthInput::new(301, 256, 1, DepthData::Half(&half))));
    }

    #[test]
    fn exchange_round_trip() {
        let cfg = DepthShmConfig {
            channels: 2,
            in_w: 8,
            in_h: 4,
            out_w: 8,
            out_h: 4,
        };
        let bytes = depth_shm_bytes(&cfg);
        let mut mem = vec![0u64; bytes / 8];
        let base = mem.as_mut_ptr().cast::<u8>();
        // SAFETY: `mem` is zeroed, aligned, outlives both views, and nothing else touches it.
        let (a, b) = unsafe {
            assert!(DepthShm::attach(base, bytes).is_none()); // no magic yet
            let a = DepthShm::create(base, bytes, &cfg).unwrap();
            assert!(DepthShm::attach(base, bytes - 1).is_none());
            (a, DepthShm::attach(base, bytes).unwrap())
        };
        assert_eq!(b.channels(), 2);
        let mut storage = Vec::new();
        assert!(b.fetch(Side::Input, 0, &mut storage, 0).is_none());
        let px: Vec<u8> = (0..8 * 4 * 4).map(|i| i as u8).collect();
        let frame = DepthFrame {
            w: 8,
            h: 4,
            source_seq: 7,
            source_ns: 11,
            done_ns: 0,
            format: DepthFormat::Bgra8,
            flags: 0,
            pixels: &px,
        };
        assert!(a.publish(Side::Input, 1, &frame));
        assert!(!a.publish(Side::Input, 2, &frame)); // no such channel
        assert!(!a.publish(Side::Input, 1, &DepthFrame { w: 9, ..frame }));
        let got = b.fetch(Side::Input, 1, &mut storage, 0).unwrap();
        assert_eq!((got.w, got.h, got.source_seq, got.source_ns, got.format), (8, 4, 7, 11, DepthFormat::Bgra8));
        assert_eq!(got.pixels, &px[..]);
        assert!(b.fetch(Side::Input, 1, &mut storage, 7).is_none()); // nothing new
        assert!(b.fetch(Side::Output, 1, &mut storage, 0).is_none());
        a.set_control(1, DEPTH_ENABLED | DEPTH_FOCUSED);
        assert_eq!(b.control(1), 3);
        assert!(!b.engine_alive(10, DEPTH_ENGINE_TIMEOUT_NS));
        a.heartbeat(1_000, 42);
        assert!(b.engine_alive(1_000 + DEPTH_ENGINE_TIMEOUT_NS, DEPTH_ENGINE_TIMEOUT_NS));
        assert!(!b.engine_alive(1_001 + DEPTH_ENGINE_TIMEOUT_NS, DEPTH_ENGINE_TIMEOUT_NS));
        drop(mem);
    }
}
