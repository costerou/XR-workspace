//! The generated test pattern, its cadence and the surface-size rules.
//!
//! A port of the presentation-only parts of `spike/src/presentation.cpp` and `PatternCadence` in
//! `spike/include/xr/presentation.hpp`. No capture, pose or external pixels.

/// `--animate-pattern-ms`: 1..=60000, decimal, no sign or leading zero.
pub fn parse_pattern_duration(text: &str) -> Option<u32> {
    let first = *text.as_bytes().first()?;
    if !(b'1'..=b'9').contains(&first)
        || text.len() > 5
        || !text.bytes().all(|c| c.is_ascii_digit())
    {
        return None;
    }
    text.parse::<u32>().ok().filter(|&n| n <= 60_000)
}

pub fn valid_surface_size(width: u32, height: u32) -> bool {
    width > 0
        && height > 0
        && width <= 8192
        && height <= 8192
        && u64::from(width) * u64::from(height) * 4 <= 64 << 20
}

/// The canvas: half the surface when the output is side by side (three times as wide as high or more).
pub fn canvas_width(surface_width: u32, height: u32) -> u32 {
    if height != 0 && surface_width / 3 >= height {
        surface_width / 2
    } else {
        surface_width
    }
}

/// The layer-shell version to bind: the pinned v4, never above what the compositor offers.
pub fn layer_version(advertised: u32) -> u32 {
    advertised.min(4)
}

pub fn pattern_pixel(x: u32, y: u32, width: u32, height: u32) -> u32 {
    if !valid_surface_size(width, height) || x >= width || y >= height {
        return 0xff00_0000;
    }
    const COLORS: [u32; 3] = [0xffff_3030, 0xff30_ff30, 0xff30_30ff];
    let band = (u64::from(x) * 3 / u64::from(width)).min(2) as usize;
    // Spatial grid distinguishes orientation and resize.
    if y > 0 && (y % 64 == 0 || x % 64 == 0) {
        0xffee_eeee
    } else {
        COLORS[band]
    }
}

pub fn animated_pattern_pixel(x: u32, y: u32, width: u32, height: u32, phase: u64) -> u32 {
    let base = pattern_pixel(x, y, width, height);
    // A moving bright vertical stripe.
    let w = u64::from(width);
    if phase != 0
        && width != 0
        && x < width
        && y < height
        && (u64::from(x) + (phase % w) * 8) % w < 32
    {
        return 0xffff_ffff;
    }
    base
}

/// The whole frame of [`animated_pattern_pixel`] into rows `stride` pixels apart. For a valid size a
/// pixel depends on y only through y == 0, y % 64 == 0 or neither, so rows 0, 1 and 64 are computed
/// and copied down.
pub fn render_animated_pattern(
    pixels: &mut [u32],
    width: u32,
    height: u32,
    stride: usize,
    phase: u64,
) {
    let (w, h) = (width as usize, height as usize);
    if w == 0 || h == 0 || stride < w || pixels.len() < (h - 1) * stride + w {
        return;
    }
    let compute = |pixels: &mut [u32], y: usize| {
        for (x, out) in pixels[y * stride..y * stride + w].iter_mut().enumerate() {
            *out = animated_pattern_pixel(x as u32, y as u32, width, height, phase);
        }
    };
    if !valid_surface_size(width, height) {
        (0..h).for_each(|y| compute(pixels, y));
        return;
    }
    compute(pixels, 0);
    if h > 1 {
        compute(pixels, 1);
    }
    if h > 64 {
        compute(pixels, 64);
    }
    for y in 2..h {
        if y != 64 {
            let from = if y % 64 == 0 { 64 } else { 1 };
            pixels.copy_within(from * stride..from * stride + w, y * stride);
        }
    }
}

/// When the next pattern frame may be drawn: one in flight at a time, paced by frame callbacks.
#[derive(Clone, Debug)]
pub struct PatternCadence {
    duration_ms: u32,
    start_ms: u64,
    phase: u64,
    dirty: bool,
    waiting: bool,
    stopped: bool,
}

impl Default for PatternCadence {
    fn default() -> Self {
        Self::new(0, 0)
    }
}

impl PatternCadence {
    /// `duration_ms` 0: a static pattern, redrawn only when invalidated.
    pub fn new(duration_ms: u32, start_ms: u64) -> Self {
        Self {
            duration_ms,
            start_ms,
            phase: 0,
            dirty: true,
            waiting: false,
            stopped: false,
        }
    }
    pub fn finished(&self, now_ms: u64) -> bool {
        self.stopped
            || (self.duration_ms != 0
                && now_ms >= self.start_ms
                && now_ms - self.start_ms >= u64::from(self.duration_ms))
    }
    pub fn ready(&self, now_ms: u64, buffer_available: bool, feedback_available: bool) -> bool {
        !self.finished(now_ms)
            && self.dirty
            && !self.waiting
            && buffer_available
            && feedback_available
    }
    pub fn committed(&mut self) {
        self.dirty = false;
        self.waiting = true;
        if self.duration_ms != 0 {
            self.phase += 1;
        }
    }
    pub fn frame_done(&mut self) {
        if self.waiting {
            self.waiting = false;
            if self.duration_ms != 0 {
                self.dirty = true;
            }
        }
    }
    pub fn invalidate(&mut self) {
        self.dirty = true;
    }
    pub fn stop(&mut self) {
        self.stopped = true;
    }
    pub fn phase(&self) -> u64 {
        self.phase
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fnv(pixels: &[u32]) -> u64 {
        pixels
            .iter()
            .flat_map(|p| p.to_le_bytes())
            .fold(0xcbf2_9ce4_8422_2325, |h, b| {
                (h ^ u64::from(b)).wrapping_mul(0x0000_0100_0000_01b3)
            })
    }

    #[test]
    fn frames_match_the_cpp_golden_hashes() {
        // golden/pattern.cpp: FNV-1a of render_animated_pattern's frame, little-endian pixels.
        let want: Vec<(u32, u32, u64, u64)> = include_str!("../golden/pattern.txt")
            .lines()
            .map(|l| {
                let f: Vec<u64> = l.split(' ').map(|v| v.parse().unwrap()).collect();
                (f[0] as u32, f[1] as u32, f[2], f[3])
            })
            .collect();
        assert!(want.len() >= 5);
        for (w, h, phase, hash) in want {
            let mut frame = vec![0u32; w as usize * h as usize];
            render_animated_pattern(&mut frame, w, h, w as usize, phase);
            assert_eq!(fnv(&frame), hash, "{w}x{h} phase {phase}");
            // and pixel for pixel the per-pixel function
            for (i, p) in frame.iter().enumerate() {
                let (x, y) = ((i % w as usize) as u32, (i / w as usize) as u32);
                assert_eq!(*p, animated_pattern_pixel(x, y, w, h, phase));
            }
        }
    }

    #[test]
    fn durations() {
        assert_eq!(parse_pattern_duration("1"), Some(1));
        assert_eq!(parse_pattern_duration("60000"), Some(60_000));
        for bad in ["", "0", "01", "60001", "+5", "5 ", "100000", "1e3", "-1"] {
            assert_eq!(parse_pattern_duration(bad), None, "{bad:?}");
        }
    }

    #[test]
    fn sizes() {
        assert!(valid_surface_size(8192, 2048));
        assert!(!valid_surface_size(8192, 2049));
        assert!(!valid_surface_size(0, 10));
        assert_eq!(canvas_width(3840, 1080), 1920);
        assert_eq!(canvas_width(1920, 1080), 1920);
        assert_eq!(canvas_width(100, 0), 100);
        assert_eq!(layer_version(5), 4);
        assert_eq!(layer_version(1), 1);
    }

    #[test]
    fn cadence() {
        let mut c = PatternCadence::new(100, 1000);
        assert!(c.ready(1000, true, true));
        assert!(!c.ready(1000, false, true));
        assert!(!c.ready(1000, true, false));
        c.committed();
        assert_eq!(c.phase(), 1);
        assert!(!c.ready(1001, true, true)); // waiting for the frame callback
        c.frame_done();
        assert!(c.ready(1001, true, true));
        assert!(c.finished(1100));
        assert!(!c.ready(1100, true, true));
        let mut fixed = PatternCadence::default();
        fixed.committed();
        fixed.frame_done();
        assert!(!fixed.ready(0, true, true)); // static: only an invalidate redraws
        fixed.invalidate();
        assert!(fixed.ready(0, true, true));
        assert_eq!(fixed.phase(), 0);
        fixed.stop();
        assert!(fixed.finished(0));
    }
}
