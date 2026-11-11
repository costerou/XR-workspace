//! Capture of the app's own managed outputs only (user grant 2026-10-31: the run's XR-n desktops, in
//! memory, never written anywhere, never the presentation or recovery outputs). Pure, offline-testable
//! helpers, a port of `spike/include/xr/capture.hpp` and `spike/src/capture.cpp`; the Wayland client
//! lives in `shell.rs`, the GBM allocator in `dmabuf.rs`.

use crate::desktops::MAX_DESKTOPS;
use crate::view::Size;

/// "XR-1,XR-2,XR-3" to ordered unique names. Rejects anything not exactly XR-1..XR-MAX_DESKTOPS,
/// duplicates, empty entries, more than MAX_DESKTOPS, or the renderer's own output.
pub fn parse_capture_list(csv: &str, own_output: &str) -> Option<Vec<String>> {
    let mut names: Vec<String> = Vec::new();
    for item in csv.split(',') {
        let b = item.as_bytes();
        let valid = b.len() == 4
            && item.starts_with("XR-")
            && (b'1'..=b'0' + MAX_DESKTOPS as u8).contains(&b[3]);
        if !valid || item == own_output || names.iter().any(|n| n == item) {
            return None;
        }
        names.push(item.to_owned());
    }
    if names.is_empty() || names.len() > MAX_DESKTOPS {
        return None;
    }
    names.sort();
    Some(names)
}

/// wl_shm.format values.
pub const SHM_ARGB8888: u32 = 0;
pub const SHM_XRGB8888: u32 = 1;

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct ShmPlan {
    pub width: u32,
    pub height: u32,
    pub stride: u32,
    pub format: u32,
    pub bytes: usize,
}

/// A 32-bit SHM layout from the session's constraints; nothing else is accepted.
pub fn plan_capture_buffer(width: u32, height: u32, formats: &[u32]) -> Option<ShmPlan> {
    if width == 0 || height == 0 || width > 8192 || height > 8192 {
        return None;
    }
    [SHM_XRGB8888, SHM_ARGB8888].into_iter().find(|f| formats.contains(f)).map(|format| ShmPlan {
        width,
        height,
        stride: width * 4,
        format,
        bytes: width as usize * 4 * height as usize,
    })
}

/// DRM fourccs 'XR24' and 'AR24'.
pub const DRM_XRGB8888: u32 = 0x3432_5258;
pub const DRM_ARGB8888: u32 = 0x3432_5241;
pub const MOD_LINEAR: u64 = 0;
/// The implicit modifier.
pub const MOD_INVALID: u64 = 0x00ff_ffff_ffff_ffff;

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct DmabufFormat {
    pub fourcc: u32,
    pub modifiers: Vec<u64>,
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct DmabufChoice {
    pub fourcc: u32,
    pub modifier: u64,
}

/// Prefer XRGB then ARGB; among offered modifiers EGL can import, prefer LINEAR, then the first.
pub fn choose_dmabuf(offered: &[DmabufFormat], importable: impl Fn(u32, u64) -> bool) -> Option<DmabufChoice> {
    for fourcc in [DRM_XRGB8888, DRM_ARGB8888] {
        for f in offered.iter().filter(|f| f.fourcc == fourcc) {
            let mut first = None;
            for &m in &f.modifiers {
                if importable(fourcc, m) {
                    if m == MOD_LINEAR {
                        return Some(DmabufChoice { fourcc, modifier: m });
                    }
                    first.get_or_insert(m);
                }
            }
            if let Some(modifier) = first {
                return Some(DmabufChoice { fourcc, modifier });
            }
        }
    }
    None
}

/// One complete constraints batch.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Batch {
    pub shm: Option<ShmPlan>,
    pub device: Option<u64>,
    pub dmabuf: Vec<DmabufFormat>,
    pub width: u32,
    pub height: u32,
}

/// Session constraints arrive as any order of shm_format/buffer_size events, then done (Hyprland
/// efb5099 sends formats first). Accumulate; reset only once a batch is complete.
#[derive(Default)]
pub struct SessionConstraints {
    width: u32,
    height: u32,
    formats: Vec<u32>,
    device: Option<u64>,
    dmabuf: Vec<DmabufFormat>,
}

impl SessionConstraints {
    pub fn size(&mut self, width: u32, height: u32) {
        self.width = width;
        self.height = height;
    }
    pub fn format(&mut self, format: u32) {
        self.formats.push(format);
    }
    pub fn dmabuf_device(&mut self, device: u64) {
        self.device = Some(device);
    }
    pub fn dmabuf_format(&mut self, fourcc: u32, modifiers: Vec<u64>) {
        match self.dmabuf.iter_mut().find(|f| f.fourcc == fourcc) {
            Some(f) => f.modifiers.extend(modifiers),
            None => self.dmabuf.push(DmabufFormat { fourcc, modifiers }),
        }
    }
    pub fn done_all(&mut self) -> Batch {
        let b = Batch {
            shm: plan_capture_buffer(self.width, self.height, &self.formats),
            device: self.device,
            dmabuf: std::mem::take(&mut self.dmabuf),
            width: self.width,
            height: self.height,
        };
        *self = Self::default();
        b
    }
}

/// Capture buffer lifecycle of one session (pure; the shell does what it decides). The compositor
/// resends constraints whenever the output's mode is touched, so:
/// - buffers are re-created only for a batch that differs (size or format) from what they were made
///   for, and a batch that matches them again cancels a pending re-creation;
/// - re-creation waits until the events have settled (`settle`, once a dispatch is done) and no frame
///   is in flight, so several batches in one dispatch re-create at most once;
/// - a buffer-constraints failure waits for the next batch with the buffers it has;
/// - `layout_size` is the size of the frame on screen (the last delivered one).
#[derive(Default)]
pub struct CaptureBuffers {
    target: Option<ShmPlan>,
    allocated: Option<ShmPlan>,
    shown: Option<Size>,
    waiting: bool,
    batches: u64,
    reallocations: u64,
}

impl CaptureBuffers {
    pub fn constraints(&mut self, plan: ShmPlan) {
        self.batches += 1;
        self.target = Some(plan);
        self.waiting = false;
    }
    pub fn constraints_failed(&mut self) {
        self.waiting = true;
    }
    /// The session was ended and a new one is starting: its first batch re-creates the buffers
    /// whatever its size; the layout keeps the shown frame's size until a new frame lands.
    pub fn restart(&mut self) {
        self.target = None;
        self.allocated = None;
        self.waiting = false;
    }
    pub fn reallocation_pending(&self) -> bool {
        match (self.target, self.allocated) {
            (Some(t), Some(a)) => !Self::same_buffers(&t, &a),
            (Some(_), None) => true,
            _ => false,
        }
    }
    /// A frame may be requested into the current buffers.
    pub fn may_capture(&self) -> bool {
        self.allocated.is_some() && !self.waiting && !self.reallocation_pending()
    }
    /// True: re-create the buffers for `allocated()` now (they are recorded as made for it).
    pub fn settle(&mut self, frame_in_flight: bool) -> bool {
        if frame_in_flight || !self.reallocation_pending() {
            return false;
        }
        self.allocated = self.target;
        self.reallocations += 1;
        true
    }
    /// A frame landed in the current buffers.
    pub fn delivered(&mut self) {
        if let Some(a) = self.allocated {
            self.shown = Some(Size::new(a.width as f32, a.height as f32));
        }
    }
    pub fn layout_size(&self) -> Option<Size> {
        if self.shown.is_some() {
            return self.shown;
        }
        self.allocated
            .or(self.target)
            .filter(|p| p.width != 0 && p.height != 0)
            .map(|p| Size::new(p.width as f32, p.height as f32))
    }
    pub fn target(&self) -> Option<ShmPlan> {
        self.target
    }
    pub fn allocated(&self) -> Option<ShmPlan> {
        self.allocated
    }
    pub fn batches(&self) -> u64 {
        self.batches
    }
    pub fn reallocations(&self) -> u64 {
        self.reallocations
    }
    pub fn same_buffers(a: &ShmPlan, b: &ShmPlan) -> bool {
        a.width == b.width && a.height == b.height && a.stride == b.stride && a.format == b.format
    }
}

/// A capture whose output was resized live re-creates its buffers this long after the new size was
/// first seen, not at once. Meanwhile the last frame stays on screen at its old size and nothing is
/// captured, so the first frame at the new size has that output's clients done.
pub const RESIZE_SETTLE_MS: u64 = 100;

#[derive(Default)]
pub struct ResizeSettle {
    since: Option<u64>,
    settles: u64,
}

impl ResizeSettle {
    /// Whether `buffers`' pending re-creation waits at `now_ms`.
    pub fn hold(&mut self, buffers: &CaptureBuffers, now_ms: u64) -> bool {
        let (Some(target), Some(shown)) = (buffers.target(), buffers.layout_size()) else {
            self.since = None;
            return false;
        };
        if !buffers.reallocation_pending() || (target.width as f32 == shown.w && target.height as f32 == shown.h) {
            self.since = None;
            return false;
        }
        let since = *self.since.get_or_insert_with(|| {
            self.settles += 1;
            now_ms
        });
        now_ms < since + RESIZE_SETTLE_MS
    }
    /// Size changes that waited (diagnostic).
    pub fn settles(&self) -> u64 {
        self.settles
    }
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Rect {
    pub x: u32,
    pub y: u32,
    pub width: u32,
    pub height: u32,
}

/// Tiles side by side at one shared height (the largest that fits), each keeping its source aspect
/// (floored to whole pixels); leftover width is split into equal per-tile gaps. Centred vertically
/// inside dst. Empty if there are no sources, dst is empty, or any source size is non-positive.
pub fn side_by_side(sources: &[Size], dst_width: u32, dst_height: u32) -> Vec<Rect> {
    if sources.is_empty() || dst_width == 0 || dst_height == 0 || sources.iter().any(|z| !(z.w > 0.0 && z.h > 0.0)) {
        return Vec::new();
    }
    let width = |z: &Size, h: u32| (h as f64 * z.w as f64 / z.h as f64) as u32;
    let total = |h: u32| sources.iter().map(|z| width(z, h) as u64).sum::<u64>();
    let aspects: f64 = sources.iter().map(|z| z.w as f64 / z.h as f64).sum();
    // Start just above the exact fit and step down until the floored widths fit dst.
    let mut h = (dst_height as f64).min(dst_width as f64 / aspects + 1.0) as u32;
    while h != 0 && total(h) > dst_width as u64 {
        h -= 1;
    }
    let n = sources.len() as u64;
    let gap = ((dst_width as u64 - total(h)) / n) as u32;
    let mut x = gap / 2;
    sources
        .iter()
        .map(|z| {
            let w = width(z, h);
            let r = Rect { x, y: (dst_height - h) / 2, width: w, height: h };
            x += w + gap;
            r
        })
        .collect()
}

/// The dst pixels `blit_nearest` writes for `r`: the rect clipped to dst, empty when it draws nothing.
fn blit_coverage(drawable: bool, dst_width: u32, dst_height: u32, r: Rect) -> Rect {
    if !drawable || r.width == 0 || r.height == 0 {
        return Rect::default();
    }
    let x_end = (r.x as u64 + r.width as u64).min(dst_width as u64) as u32;
    let y_end = (r.y as u64 + r.height as u64).min(dst_height as u64) as u32;
    if r.x >= x_end || r.y >= y_end {
        return Rect::default();
    }
    Rect { x: r.x, y: r.y, width: x_end - r.x, height: y_end - r.y }
}

/// One source of the CPU composition; `None` pixels (not captured yet) draw nothing.
#[derive(Clone, Copy, Default)]
pub struct TileSource<'a> {
    pub pixels: Option<&'a [u32]>,
    pub width: u32,
    pub height: u32,
    pub stride_px: u32,
}

/// A destination canvas of 32-bit pixels.
pub struct Canvas<'a> {
    pub pixels: &'a mut [u32],
    pub width: u32,
    pub height: u32,
    pub stride_px: u32,
}

/// Nearest-neighbour scale of a 32-bit source into the dst rect (clipped to dst); alpha forced
/// opaque. Source column/row for dst pixel i of the rect: floor(i * src_size / rect_size).
pub fn blit_nearest(src: &TileSource, dst: &mut Canvas, r: Rect, columns: &mut Vec<u32>) {
    let Some(pixels) = src.pixels else { return };
    let cover = blit_coverage(src.width != 0 && src.height != 0, dst.width, dst.height, r);
    if cover.width == 0 {
        return;
    }
    let n = cover.width as usize;
    let identity = src.width == r.width;
    if !identity {
        // Exact floor(i * src_width / r.width) by stepping quotient and remainder.
        columns.resize(n.max(columns.len()), 0);
        let (step, step_rem) = (src.width / r.width, src.width % r.width);
        let (mut q, mut rem) = (0u32, 0u64);
        for c in columns.iter_mut().take(n) {
            *c = q;
            q += step;
            rem += step_rem as u64;
            if rem >= r.width as u64 {
                rem -= r.width as u64;
                q += 1;
            }
        }
    }
    let mut last: Option<(u32, usize)> = None;
    for y in r.y..r.y + cover.height {
        let sy = ((y - r.y) as u64 * src.height as u64 / r.height as u64) as u32;
        let out = y as usize * dst.stride_px as usize + r.x as usize;
        match last {
            // Same source row (vertical upscale): the row just written.
            Some((last_sy, last_out)) if last_sy == sy => dst.pixels.copy_within(last_out..last_out + n, out),
            _ => {
                let row = &pixels[sy as usize * src.stride_px as usize..];
                let out_row = &mut dst.pixels[out..out + n];
                if identity {
                    for (o, p) in out_row.iter_mut().zip(row) {
                        *o = p | 0xff00_0000;
                    }
                } else {
                    for (o, &c) in out_row.iter_mut().zip(columns.iter()) {
                        *o = row[c as usize] | 0xff00_0000;
                    }
                }
            }
        }
        last = Some((sy, out));
    }
}

/// CPU composition frame (the GPU-less fallback): `background` in columns [0, dst.width) of every
/// row, then `sources[i]` blitted into `tiles[i]` in order. Pixel-identical to filling and then
/// blitting, but each pixel is written once: only what no blit covers gets the background.
pub fn compose_tiles(dst: &mut Canvas, sources: &[TileSource], tiles: &[Rect], background: u32, columns: &mut Vec<u32>) {
    let count = sources.len().min(tiles.len());
    let cover: Vec<Rect> = (0..count)
        .map(|i| {
            let s = &sources[i];
            blit_coverage(s.pixels.is_some() && s.width != 0 && s.height != 0, dst.width, dst.height, tiles[i])
        })
        .filter(|c| c.width != 0)
        .collect();
    let mut spans: Vec<(u32, u32)> = Vec::with_capacity(cover.len());
    for y in 0..dst.height {
        spans.clear();
        spans.extend(cover.iter().filter(|c| y >= c.y && y - c.y < c.height).map(|c| (c.x, c.x + c.width)));
        spans.sort_unstable();
        let row = &mut dst.pixels[y as usize * dst.stride_px as usize..][..dst.width as usize];
        let mut x = 0u32;
        for &(start, end) in &spans {
            if start > x {
                row[x as usize..start as usize].fill(background);
            }
            x = x.max(end);
        }
        if x < dst.width {
            row[x as usize..].fill(background);
        }
    }
    for i in 0..count {
        blit_nearest(&sources[i], dst, tiles[i], columns);
    }
}

/// Clip-space quad (x0, y0, x1, y1 in [-1, 1], y up) for a pixel rect in a w x h viewport (y down).
pub fn clip_quad(r: Rect, width: u32, height: u32) -> [f32; 4] {
    if width == 0 || height == 0 {
        return [0.0; 4];
    }
    let x = |px: u64| (2.0 * px as f64 / width as f64 - 1.0) as f32;
    let y = |px: u64| (1.0 - 2.0 * px as f64 / height as f64) as f32;
    [x(r.x as u64), y(r.y as u64), x(r.x as u64 + r.width as u64), y(r.y as u64 + r.height as u64)]
}

/// The owner-readback line limiter: send at most every 50 ms, an identical line at most once per
/// second; a short send forgets the last line and when it went, so it is sent again at once (owner_readback.hpp).
#[derive(Default)]
pub struct ReadbackLimiter {
    sent_ms: Option<u64>,
    last: String,
    last_ms: u64,
}

impl ReadbackLimiter {
    pub const MAX_LINE: usize = 16384;
    pub fn due(&self, line: &str, now_ms: u64) -> bool {
        if line.len() + 1 > Self::MAX_LINE {
            return false;
        }
        match self.sent_ms {
            None => true,
            Some(t) if now_ms < t + 50 => false,
            Some(_) => line != self.last || now_ms >= self.last_ms + 1000,
        }
    }
    pub fn sent(&mut self, line: &str, now_ms: u64) {
        self.sent_ms = Some(now_ms);
        if line != self.last {
            self.last = line.to_owned();
        }
        self.last_ms = now_ms;
    }
    pub fn resend(&mut self) {
        self.last.clear();
        self.sent_ms = None;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn capture_list() {
        assert_eq!(parse_capture_list("XR-3,XR-1", "DP-2"), Some(vec!["XR-1".into(), "XR-3".into()]));
        assert_eq!(parse_capture_list("XR-4", "DP-2"), Some(vec!["XR-4".into()]));
        for bad in ["", "XR-5", "XR-0", "XR-1,", "XR-1,XR-1", "XR-12", "xr-1", " XR-1", "XR-1,XR-2,XR-3,XR-4,XR-1"] {
            assert_eq!(parse_capture_list(bad, "DP-2"), None, "{bad}");
        }
        assert_eq!(parse_capture_list("XR-1,XR-2", "XR-2"), None);
    }

    #[test]
    fn shm_plan() {
        assert_eq!(plan_capture_buffer(0, 10, &[1]), None);
        assert_eq!(plan_capture_buffer(8193, 10, &[1]), None);
        assert_eq!(plan_capture_buffer(10, 10, &[5]), None);
        let p = plan_capture_buffer(1920, 1080, &[0, 1]).unwrap();
        assert_eq!((p.format, p.stride, p.bytes), (SHM_XRGB8888, 7680, 7680 * 1080));
        assert_eq!(plan_capture_buffer(4, 4, &[0]).unwrap().format, SHM_ARGB8888);
    }

    #[test]
    fn dmabuf_choice() {
        let offered = vec![
            DmabufFormat { fourcc: DRM_ARGB8888, modifiers: vec![MOD_LINEAR] },
            DmabufFormat { fourcc: DRM_XRGB8888, modifiers: vec![7, MOD_LINEAR, 9] },
        ];
        assert_eq!(choose_dmabuf(&offered, |_, _| true), Some(DmabufChoice { fourcc: DRM_XRGB8888, modifier: 0 }));
        assert_eq!(choose_dmabuf(&offered, |_, m| m == 9), Some(DmabufChoice { fourcc: DRM_XRGB8888, modifier: 9 }));
        assert_eq!(
            choose_dmabuf(&offered, |f, _| f == DRM_ARGB8888),
            Some(DmabufChoice { fourcc: DRM_ARGB8888, modifier: 0 })
        );
        assert_eq!(choose_dmabuf(&offered, |_, _| false), None);
    }

    #[test]
    fn constraints_batch() {
        let mut c = SessionConstraints::default();
        c.format(0);
        c.size(64, 32);
        c.dmabuf_device(0xe280);
        c.dmabuf_format(DRM_XRGB8888, vec![1]);
        c.dmabuf_format(DRM_XRGB8888, vec![2]);
        let b = c.done_all();
        assert_eq!(b.shm.unwrap().format, SHM_ARGB8888);
        assert_eq!(b.device, Some(0xe280));
        assert_eq!(b.dmabuf, vec![DmabufFormat { fourcc: DRM_XRGB8888, modifiers: vec![1, 2] }]);
        assert_eq!(c.done_all(), Batch::default());
    }

    fn plan(w: u32, h: u32) -> ShmPlan {
        plan_capture_buffer(w, h, &[1]).unwrap()
    }

    #[test]
    fn buffers_lifecycle() {
        let mut b = CaptureBuffers::default();
        assert!(!b.may_capture() && b.layout_size().is_none());
        b.constraints(plan(10, 10));
        assert!(b.reallocation_pending() && !b.may_capture());
        assert!(!b.settle(true));
        assert!(b.settle(false));
        assert!(b.may_capture() && !b.settle(false));
        b.delivered();
        // A size that changes and changes back cancels the re-creation.
        b.constraints(plan(20, 10));
        assert!(b.reallocation_pending());
        b.constraints(plan(10, 10));
        assert!(!b.reallocation_pending() && b.may_capture());
        assert_eq!((b.batches(), b.reallocations()), (3, 1));
        b.constraints_failed();
        assert!(!b.may_capture());
        b.restart();
        assert_eq!(b.layout_size(), Some(Size::new(10.0, 10.0)));
        assert!(!b.may_capture());
    }

    #[test]
    fn resize_settle_waits_only_for_a_new_size() {
        let mut b = CaptureBuffers::default();
        let mut r = ResizeSettle::default();
        b.constraints(plan(10, 10));
        assert!(!r.hold(&b, 0)); // first buffers do not wait
        b.settle(false);
        b.delivered();
        b.constraints(plan(20, 10));
        assert!(r.hold(&b, 1000));
        assert!(r.hold(&b, 1099));
        assert!(!r.hold(&b, 1100));
        assert_eq!(r.settles(), 1);
        b.settle(false);
        assert!(!r.hold(&b, 1101));
    }

    #[test]
    fn side_by_side_fits() {
        assert!(side_by_side(&[], 100, 100).is_empty());
        assert!(side_by_side(&[Size::new(0.0, 1.0)], 100, 100).is_empty());
        let s = [Size::new(1920.0, 1080.0); 3];
        let t = side_by_side(&s, 1920, 1200);
        assert_eq!(t.len(), 3);
        assert_eq!(t[0], Rect { x: 0, y: 420, width: 640, height: 360 });
        assert_eq!(t[2].x, 1280);
        let one = side_by_side(&[Size::new(1920.0, 1080.0)], 1920, 1200);
        assert_eq!(one[0], Rect { x: 0, y: 60, width: 1920, height: 1080 });
    }

    #[test]
    fn compose_matches_fill_then_blit() {
        let src: Vec<u32> = (0..12).collect();
        let s = TileSource { pixels: Some(&src), width: 4, height: 3, stride_px: 4 };
        let tiles = [Rect { x: 1, y: 1, width: 8, height: 6 }, Rect { x: 6, y: 0, width: 4, height: 3 }];
        let mut a = vec![0u32; 10 * 8];
        let mut cols = Vec::new();
        compose_tiles(
            &mut Canvas { pixels: &mut a, width: 10, height: 8, stride_px: 10 },
            &[s, s],
            &tiles,
            0xff11_2233,
            &mut cols,
        );
        let mut b = vec![0xff11_2233u32; 10 * 8];
        for t in tiles {
            blit_nearest(&s, &mut Canvas { pixels: &mut b, width: 10, height: 8, stride_px: 10 }, t, &mut cols);
        }
        assert_eq!(a, b);
        assert_eq!(a[10 + 1], 0xff00_0000); // src (0,0)
        assert_eq!(a[2 * 10 + 2], 0xff00_0000); // upscaled 2x: still (0,0)
        assert_eq!(a[3 * 10 + 3], 0xff00_0005); // (1,1)
        assert_eq!(a[0], 0xff11_2233);
        // A source not captured yet draws nothing: background only.
        let none = TileSource { pixels: None, ..s };
        compose_tiles(&mut Canvas { pixels: &mut a, width: 10, height: 8, stride_px: 10 }, &[none], &tiles, 7, &mut cols);
        assert!(a.iter().all(|&p| p == 7));
    }

    #[test]
    fn clip_quad_maps_to_ndc() {
        assert_eq!(clip_quad(Rect { x: 0, y: 0, width: 100, height: 50 }, 100, 50), [-1.0, 1.0, 1.0, -1.0]);
        assert_eq!(clip_quad(Rect::default(), 0, 1), [0.0; 4]);
    }

    #[test]
    fn readback_limiter() {
        let mut l = ReadbackLimiter::default();
        assert!(l.due("a", 0));
        l.sent("a", 0);
        assert!(!l.due("b", 49));
        assert!(l.due("b", 50));
        assert!(!l.due("a", 999));
        assert!(l.due("a", 1000));
        l.sent("a", 1000);
        l.resend();
        assert!(l.due("a", 1001), "a short send is sent again at once");
        assert!(!l.due(&"x".repeat(16384), 5000));
    }
}
