//! The renderer's end of the AI depth engine (docs/immersive-3d.md, spike/src/depth_link.cpp): each 3D desktop's
//! picture out to the engine, its depth map back to the compositor. The engine is another process behind shared memory
//! (XRD1, crate::depth), so nothing here can wait for it and nothing it does can reach the renderer: when its heartbeat
//! stops, this simply stops feeding and stops vouching for the last maps, and the compositor eases those desktops flat
//! by itself.
//!
//! The GPU budget is per frame, not per desktop: at most DEPTH_PER_TICK shrinks and map uploads each tick, whatever the
//! number of desktops, so 3D's cost on the render thread is bounded and head tracking is not paid for it.
//! No link exists while nothing is 3D, so a 2D run does no work here at all.
//!
//! Not wired into main yet: the Rust renderer still refuses --depth-fd, and the live system stays on the C++ one.
//! Everything needs the compositor's GL context current; drop the link before the compositor.

use std::os::fd::BorrowedFd;

use rustix::mm::{MapFlags, ProtFlags, mmap, munmap};

use crate::depth::{
    DEPTH_ENABLED, DEPTH_ENGINE_TIMEOUT_NS, DEPTH_FLAT, DEPTH_FOCUSED, DEPTH_LINK_SHM, DEPTH_MIN_HEIGHT,
    DEPTH_SCENE_CUT, DepthData, DepthFormat, DepthFrame, DepthInput, DepthShm, SHM_HEADER_STRUCT_BYTES, Side,
    depth_anchor, depth_shm_bytes,
};
use crate::depth_feed::DepthFeed;
use crate::desktops::MAX_DESKTOPS;
use crate::gl::GlCompositor;

const _: () = assert!(DEPTH_LINK_SHM.channels as usize == MAX_DESKTOPS);
pub const DEPTH_PER_TICK: usize = 2;
pub const DEPTH_FEED_FOCUSED_NS: u64 = 1_000_000_000 / 60;
pub const DEPTH_FEED_OTHER_NS: u64 = 1_000_000_000 / 30;

/// One frame's wishes.
#[derive(Clone, Copy, Debug)]
pub struct Tick<'a> {
    /// Per desktop: 3D is asked for.
    pub wanted: &'a [bool],
    /// Per desktop: its capture's frame count, rising.
    pub frames: &'a [u64],
    /// The desktop being looked at: fed at the higher rate. -1: none.
    pub focused: i32,
    /// The compositor is drawing 3D: maps are wanted, not just the engine warmed up.
    pub drawing: bool,
    /// The screen plane, as a percentile of each map's nearness.
    pub convergence: f32,
    /// How slowly that plane moves.
    pub range_ms: f32,
    /// CLOCK_MONOTONIC.
    pub now_ns: u64,
}

impl Default for Tick<'_> {
    fn default() -> Self {
        Self {
            wanted: &[],
            frames: &[],
            focused: -1,
            drawing: false,
            convergence: 92.0,
            range_ms: 3000.0,
            now_ns: 0,
        }
    }
}

#[derive(Default)]
struct Channel {
    feed: Option<DepthFeed>,
    fed_frames: u64,
    seq: u64,
    next_ns: u64,
    seen: u64,
    source_ns: u64,
    map_ns: u64,
    bgr: bool,
    on: bool,
    have_anchor: bool,
    anchor: f32,
    rgba: Vec<u8>,
}

pub struct DepthLink {
    base: *mut u8,
    bytes: usize,
    shm: DepthShm,
    channels: [Channel; MAX_DESKTOPS],
    map: Vec<u8>,
    half: Vec<u16>,
    next_feed: usize,
    next_map: usize,
    alive: bool,
    feed_failed: bool,
    feeds: u64,
    maps: u64,
    lag_us: u64,
    /// How old each map's picture was when it reached the compositor, in 1 ms bins.
    age_ms: [u32; 256],
}

impl DepthLink {
    /// `fd`: the shared memory, a memfd the supervisor made (it outlives any renderer, so a renderer started after a drop
    /// finds the running engine). A blank one gets the layout; one a renderer before this one laid out is taken over.
    /// Err (the C++ `why`) if it cannot be mapped or is the wrong size. Does not take the fd.
    pub fn open(fd: BorrowedFd) -> Result<Self, String> {
        let need = depth_shm_bytes(&DEPTH_LINK_SHM);
        match rustix::fs::fstat(fd) {
            Ok(st) if st.st_size as usize == need => {}
            _ => return Err("depth memory has the wrong size".into()),
        }
        // SAFETY: a fresh shared mapping of `need` bytes of the file, unmapped in Drop (or below on failure).
        let base = unsafe {
            mmap(std::ptr::null_mut(), need, ProtFlags::READ | ProtFlags::WRITE, MapFlags::SHARED, fd, 0)
        }
        .map_err(|e| format!("depth memory: {e}"))?
        .cast::<u8>();
        // SAFETY: base is page aligned and `need` bytes stay mapped for the link's life; only the engine writes them
        // meanwhile. A renderer before this one in the same run left a layout to take over; else it is laid out here.
        let shm = unsafe {
            DepthShm::attach(base, need).or_else(|| {
                std::ptr::write_bytes(base, 0, SHM_HEADER_STRUCT_BYTES);
                DepthShm::create(base, need, &DEPTH_LINK_SHM)
            })
        };
        let Some(shm) = shm else {
            // SAFETY: mapped above, not used after this.
            let _ = unsafe { munmap(base.cast(), need) };
            return Err("depth memory layout refused".into());
        };
        Ok(Self {
            base,
            bytes: need,
            shm,
            channels: Default::default(),
            map: Vec::with_capacity((DEPTH_LINK_SHM.out_w * DEPTH_LINK_SHM.out_h * 4) as usize),
            half: Vec::new(),
            next_feed: 0,
            next_map: 0,
            alive: false,
            feed_failed: false,
            feeds: 0,
            maps: 0,
            lag_us: 0,
            age_ms: [0; 256],
        })
    }

    pub fn engine_alive(&self) -> bool {
        self.alive
    }
    pub fn feeds(&self) -> u64 {
        self.feeds
    }
    pub fn maps(&self) -> u64 {
        self.maps
    }

    /// Desktops were added or removed: every channel now means another desktop.
    pub fn restart(&mut self) {
        for c in &mut self.channels {
            c.fed_frames = 0;
            c.seen = 0;
            c.next_ns = 0;
            c.have_anchor = false;
        }
    }

    /// Every channel off, for the engine to go idle; the link is about to go.
    pub fn stop(&self) {
        for i in 0..MAX_DESKTOPS as u32 {
            self.shm.set_control(i, 0);
        }
    }

    /// Once per frame, after the frame's pictures are uploaded to `gl`. Never blocks.
    pub fn tick(&mut self, gl: &mut GlCompositor, t: &Tick) {
        let n = t.wanted.len().min(t.frames.len()).min(MAX_DESKTOPS);
        for (i, c) in self.channels.iter_mut().enumerate() {
            let on = i < n && t.wanted[i];
            if on != c.on {
                c.on = on;
                c.seen = 0;
                c.have_anchor = false;
            }
            let focused = if i as i32 == t.focused { DEPTH_FOCUSED } else { 0 };
            self.shm.set_control(i as u32, if on { DEPTH_ENABLED | focused } else { 0 });
        }
        self.alive = self.shm.engine_alive(t.now_ns, DEPTH_ENGINE_TIMEOUT_NS);
        if !self.alive {
            return; // No heartbeat: nothing is fed, nothing is vouched for, and those desktops ease flat.
        }
        // Finished shrinks to the engine: a copy of under half a megabyte, no wait.
        for (i, c) in self.channels.iter_mut().enumerate().take(n) {
            let Some(feed) = &mut c.feed else { continue };
            if !feed.busy() || !feed.collect(&mut c.rgba) {
                continue;
            }
            c.seq += 1;
            let f = DepthFrame {
                w: DEPTH_LINK_SHM.in_w,
                h: DEPTH_LINK_SHM.in_h,
                source_seq: c.seq,
                source_ns: c.source_ns,
                done_ns: 0,
                format: if c.bgr { DepthFormat::Bgra8 } else { DepthFormat::Rgba8 },
                flags: 0,
                pixels: &c.rgba,
            };
            if c.on {
                self.shm.publish(Side::Input, i as u32, &f);
            }
        }
        // New shrinks: the next desktops round from the last one with a new picture and their turn due.
        let mut shrinks = 0;
        if !self.feed_failed {
            let from = self.next_feed;
            for k in 0..n {
                if shrinks >= DEPTH_PER_TICK {
                    break;
                }
                let i = (from + k) % n;
                let c = &mut self.channels[i];
                if !c.on || t.frames[i] == c.fed_frames || t.now_ns < c.next_ns || c.feed.as_ref().is_some_and(|f| f.busy())
                {
                    continue;
                }
                let Some((texture, _, _)) = gl.source_texture(i) else { continue };
                if c.feed.is_none() {
                    c.feed = DepthFeed::new(gl.shared_gl(), DEPTH_LINK_SHM.in_w, DEPTH_LINK_SHM.in_h);
                    if c.feed.is_none() {
                        self.feed_failed = true;
                        break;
                    }
                }
                if !c.feed.as_mut().is_some_and(|f| f.begin_texture(texture)) {
                    continue;
                }
                // Every Rust source is an SHM upload: XRGB bytes, so B, G, R in the texture.
                c.bgr = true;
                c.fed_frames = t.frames[i];
                c.source_ns = t.now_ns;
                c.next_ns =
                    t.now_ns + if i as i32 == t.focused { DEPTH_FEED_FOCUSED_NS } else { DEPTH_FEED_OTHER_NS };
                self.next_feed = (i + 1) % n;
                self.feeds += 1;
                shrinks += 1;
            }
        }
        if !t.drawing {
            return;
        }
        // New maps, and the rest vouched for: the engine is alive, so a desktop with nothing new has simply not changed.
        let mut taken = 0;
        let from = self.next_map;
        for k in 0..n {
            let i = (from + k) % n;
            let c = &mut self.channels[i];
            if !c.on {
                continue;
            }
            if taken < DEPTH_PER_TICK
                && let Some(f) = self.shm.fetch(Side::Output, i as u32, &mut self.map, c.seen)
                && f.format == DepthFormat::R16F
                && f.w >= 1
                && f.h >= DEPTH_MIN_HEIGHT
            {
                // The map as half floats; the storage is a byte Vec, so copy only if it is not 2-byte aligned.
                // SAFETY: any bit pattern is a valid u16.
                let (head, aligned, _) = unsafe { f.pixels.align_to::<u16>() };
                let half: &[u16] = if head.is_empty() {
                    aligned
                } else {
                    self.half.clear();
                    self.half
                        .extend(f.pixels.chunks_exact(2).map(|b| u16::from_ne_bytes([b[0], b[1]])));
                    &self.half
                };
                let now = if f.flags & DEPTH_FLAT != 0 { 0.5 } else { depth_anchor(half, f.w, f.h, t.convergence) };
                let dt_ms = if c.map_ns != 0 { t.now_ns.wrapping_sub(c.map_ns) as f64 / 1e6 } else { 0.0 };
                let cut = f.flags & DEPTH_SCENE_CUT != 0;
                let snap = !c.have_anchor || cut || t.range_ms <= 0.0;
                c.anchor = if snap {
                    now
                } else {
                    c.anchor + (now - c.anchor) * (1.0 - (-dt_ms / t.range_ms as f64).exp()) as f32
                };
                c.have_anchor = true;
                c.map_ns = t.now_ns;
                let mut input = DepthInput::new(f.w, f.h, f.source_seq, DepthData::Half(half));
                input.convergence = c.anchor;
                input.reset_history = cut;
                if gl.set_depth(i, &input) {
                    c.seen = f.source_seq;
                    self.maps += 1;
                    if f.done_ns >= f.source_ns {
                        self.lag_us = (f.done_ns - f.source_ns) / 1000;
                    }
                    if t.now_ns >= f.source_ns {
                        let bin = ((t.now_ns - f.source_ns) / 1_000_000).min(self.age_ms.len() as u64 - 1);
                        self.age_ms[bin as usize] += 1;
                    }
                }
                taken += 1;
                self.next_map = (i + 1) % n;
            }
            gl.keep_depth(i);
        }
    }

    fn age_percentile(&self, q: f64) -> f64 {
        let total: u64 = self.age_ms.iter().map(|&n| n as u64).sum();
        let mut seen = 0u64;
        for (i, &n) in self.age_ms.iter().enumerate() {
            seen += n as u64;
            if seen != 0 && seen as f64 >= q * total as f64 {
                return i as f64;
            }
        }
        0.0
    }

    /// Bounded JSON, the C++ keys.
    pub fn report(&self) -> String {
        format!(
            "{{\"alive\":{},\"feeds\":{},\"maps\":{},\"lag_us\":{},\"age_p50_ms\":{},\"age_p99_ms\":{},\"feed_failed\":{}}}",
            self.alive,
            self.feeds,
            self.maps,
            self.lag_us,
            self.age_percentile(0.5) as i32,
            self.age_percentile(0.99) as i32,
            self.feed_failed
        )
    }
}

impl Drop for DepthLink {
    fn drop(&mut self) {
        // SAFETY: mapped in open(); the view (shm) is not used after this.
        let _ = unsafe { munmap(self.base.cast(), self.bytes) };
    }
}
