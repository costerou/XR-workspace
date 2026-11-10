//! CPU side of VR video: formats, the projection maths the shader mirrors, output eyes and frame pacing.
//! Port of spike/include/xr/video.hpp and spike/src/video.cpp (the controls card stays with the HUD).

use std::f32::consts::PI;

/// Row-major 3x3 rotation, world -> eye (the desktops' convention, spike/include/xr/pose.hpp).
pub type Mat3 = [f32; 9];

pub const IDENTITY: Mat3 = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0];

pub fn transpose(m: &Mat3) -> Mat3 {
    [m[0], m[3], m[6], m[1], m[4], m[7], m[2], m[5], m[8]]
}

pub fn apply(m: &Mat3, v: [f32; 3]) -> [f32; 3] {
    [
        m[0] * v[0] + m[1] * v[1] + m[2] * v[2],
        m[3] * v[0] + m[4] * v[1] + m[5] * v[2],
        m[6] * v[0] + m[7] * v[1] + m[8] * v[2],
    ]
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Default)]
pub enum Projection {
    #[default]
    Equirect360 = 0,
    Equirect180 = 1,
    Fisheye = 2,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Default)]
pub enum Stereo {
    #[default]
    Mono = 0,
    Sbs = 1,
    Tb = 2,
}

pub const FISHEYE_FOV_MIN_DEG: u32 = 180;
pub const FISHEYE_FOV_MAX_DEG: u32 = 220;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Format {
    pub projection: Projection,
    pub stereo: Stereo,
    pub fov_deg: u32,
}

impl Default for Format {
    fn default() -> Self {
        Self {
            projection: Projection::Equirect360,
            stereo: Stereo::Mono,
            fov_deg: 180,
        }
    }
}

impl Format {
    pub const fn new(projection: Projection, stereo: Stereo, fov_deg: u32) -> Self {
        Self {
            projection,
            stereo,
            fov_deg,
        }
    }

    /// The fisheye lens angle the shader uses.
    pub fn clamped_fov_deg(&self) -> u32 {
        self.fov_deg.clamp(FISHEYE_FOV_MIN_DEG, FISHEYE_FOV_MAX_DEG)
    }

    pub fn label(&self, ascii: bool) -> String {
        let deg = if ascii { " deg" } else { "°" };
        let mut out = match self.projection {
            Projection::Equirect360 => format!("360{deg}"),
            Projection::Equirect180 => format!("180{deg}"),
            Projection::Fisheye => format!("Fisheye {}{deg}", self.fov_deg),
        };
        out += match self.stereo {
            Stereo::Mono => " mono",
            Stereo::Sbs => " side-by-side 3D",
            Stereo::Tb => " top-bottom 3D",
        };
        out
    }
}

/// The part of the frame one eye sees, in texture coordinates.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct UvRect {
    pub x: f32,
    pub y: f32,
    pub w: f32,
    pub h: f32,
}

pub fn eye_rect(stereo: Stereo, eye: usize) -> UvRect {
    let e = if eye != 0 { 1.0 } else { 0.0 };
    match stereo {
        Stereo::Sbs => UvRect {
            x: 0.5 * e,
            y: 0.0,
            w: 0.5,
            h: 1.0,
        },
        Stereo::Tb => UvRect {
            x: 0.0,
            y: 0.5 * e,
            w: 1.0,
            h: 0.5,
        },
        Stereo::Mono => UvRect {
            x: 0.0,
            y: 0.0,
            w: 1.0,
            h: 1.0,
        },
    }
}

/// Where the world direction `d` lands in the frame for `eye`; None outside the picture. The fragment shader mirrors it.
pub fn video_uv(f: &Format, d: [f32; 3], eye: usize) -> Option<[f32; 2]> {
    let n = (d[0] * d[0] + d[1] * d[1] + d[2] * d[2]).sqrt();
    if !n.is_finite() || n <= 1e-9 {
        return None;
    }
    let d = [d[0] / n, d[1] / n, d[2] / n];
    let (u, v);
    if f.projection == Projection::Fisheye {
        let fov = f.clamped_fov_deg() as f32 * PI / 180.0;
        let theta = (-d[2]).clamp(-1.0, 1.0).acos(); // angle from straight ahead
        let r = theta / (0.5 * fov);
        if r > 1.0 {
            return None;
        }
        let l = d[0].hypot(d[1]);
        let (cx, cy) = if l > 1e-7 {
            (d[0] / l, d[1] / l)
        } else {
            (0.0, 0.0)
        };
        u = 0.5 + 0.5 * r * cx;
        v = 0.5 - 0.5 * r * cy;
    } else {
        let lon = d[0].atan2(-d[2]);
        let lat = d[1].clamp(-1.0, 1.0).asin();
        v = 0.5 - lat / PI;
        if f.projection == Projection::Equirect180 {
            if lon.abs() > 0.5 * PI + 1e-6 {
                return None;
            }
            u = 0.5 + lon / PI;
        } else {
            let w = 0.5 + lon / (2.0 * PI);
            u = w - w.floor(); // wrap (the shader uses fract)
        }
    }
    let r = eye_rect(f.stereo, eye);
    Some([r.x + u.clamp(0.0, 1.0) * r.w, r.y + v.clamp(0.0, 1.0) * r.h])
}

/// The world direction through NDC (`x`, `y`) of a view with tan(half FOV) `hw`/`hh`.
pub fn view_ray(view: &Mat3, x: f32, y: f32, hw: f32, hh: f32) -> [f32; 3] {
    apply(&transpose(view), [x * hw, y * hh, -1.0])
}

/// tan(half FOV) across and up for a `width` x `height` viewport with diagonal FOV `diagonal_deg`.
pub fn half_fov_tan(diagonal_deg: f32, width: u32, height: u32) -> [f32; 2] {
    let d = (f64::from(diagonal_deg) * std::f64::consts::PI / 360.0).tan();
    let a = if height != 0 {
        f64::from(width) / f64::from(height)
    } else {
        16.0 / 10.0
    };
    let s = (1.0 + a * a).sqrt();
    [(d * a / s) as f32, (d / s) as f32]
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct EyeViewport {
    pub x: u32,
    pub y: u32,
    pub width: u32,
    pub height: u32,
    pub eye: usize,
}

/// One viewport per output eye: a 3:1 or wider output is the glasses' side-by-side mode.
pub fn eye_viewports(width: u32, height: u32) -> Vec<EyeViewport> {
    if width == 0 || height == 0 {
        return Vec::new();
    }
    if width >= 3 * height {
        let half = width / 2;
        return vec![
            EyeViewport {
                x: 0,
                y: 0,
                width: half,
                height,
                eye: 0,
            },
            EyeViewport {
                x: half,
                y: 0,
                width: width - half,
                height,
                eye: 1,
            },
        ];
    }
    vec![EyeViewport {
        x: 0,
        y: 0,
        width,
        height,
        eye: 0,
    }]
}

pub const VIDEO_MAX_TEXTURE_SIDE: u32 = 8192;

/// The texture for a `w` x `h` frame: the frame's size, scaled down (aspect kept) to fit `max_side`.
pub fn video_texture_size(w: u32, h: u32, max_side: u32) -> [u32; 2] {
    if w == 0 || h == 0 || max_side == 0 {
        return [0, 0];
    }
    if w <= max_side && h <= max_side {
        return [w, h];
    }
    let s = f64::from(max_side) / f64::from(w.max(h));
    [
        ((f64::from(w) * s).round() as u32).max(1),
        ((f64::from(h) * s).round() as u32).max(1),
    ]
}

/// Holds each decoded frame until its display frame (mpv hands frames over up to 50 ms early).
#[derive(Clone, Debug)]
pub struct FramePacer {
    samples: [i64; PACER_SAMPLES],
    count: usize,
    next: usize,
    last_tick: i64,
    interval: i64,
    pending_since: i64,
    last_target: i64,
    last_drawn: i64,
    held: u64,
    timed: u64,
    early: u64,
    late: u64,
    lead_sum: f64,
    lead_min: f64,
    lead_max: f64,
}

const PACER_SAMPLES: usize = 15;

#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct PacerStats {
    pub held: u64,
    pub timed: u64,
    /// Early / late: by more than one display interval.
    pub early: u64,
    pub late: u64,
    /// Target - on screen; > 0: on screen before its time.
    pub lead_min_ms: f64,
    pub lead_mean_ms: f64,
    pub lead_max_ms: f64,
}

impl Default for FramePacer {
    fn default() -> Self {
        Self {
            samples: [0; PACER_SAMPLES],
            count: 0,
            next: 0,
            last_tick: 0,
            interval: Self::DEFAULT_INTERVAL_NS,
            pending_since: 0,
            last_target: 0,
            last_drawn: 0,
            held: 0,
            timed: 0,
            early: 0,
            late: 0,
            lead_sum: 0.0,
            lead_min: 0.0,
            lead_max: 0.0,
        }
    }
}

impl FramePacer {
    pub const DEFAULT_INTERVAL_NS: i64 = 1_000_000_000 / 120;
    /// Never hold a frame longer (mpv hands them over <= 50 ms early).
    pub const MAX_HOLD_NS: i64 = 100_000_000;

    /// Once per display frame, before `due`.
    pub fn tick(&mut self, now: i64) {
        const MIN: i64 = 2_000_000; // 500 Hz .. 20 Hz; anything else is not a display frame
        const MAX: i64 = 50_000_000;
        let dt = if self.last_tick != 0 {
            now - self.last_tick
        } else {
            0
        };
        self.last_tick = now;
        if !(MIN..=MAX).contains(&dt) {
            return;
        }
        self.samples[self.next] = dt;
        self.next = (self.next + 1) % PACER_SAMPLES;
        self.count = (self.count + 1).min(PACER_SAMPLES);
        if self.count < 5 {
            return; // too few gaps for a median: keep the default
        }
        let mut sorted = self.samples;
        let mid = self.count / 2;
        let (_, median, _) = sorted[..self.count].select_nth_unstable(mid);
        self.interval = *median;
    }

    /// Draw the pending frame (due at `target`) on this display frame?
    pub fn due(&mut self, target: i64, now: i64) -> bool {
        if self.pending_since == 0 {
            self.pending_since = now;
        }
        let t = self.interval;
        let early_by = target - (now + t); // > 0: it would be on screen before its time
        if early_by <= t / 2 || now - self.pending_since >= Self::MAX_HOLD_NS {
            return true; // the vblank nearest the target, or late
        }
        let spacing = target - self.last_target;
        if self.last_target != 0
            && spacing > 0
            && spacing < 1_000_000_000
            && early_by <= t
            && now - self.last_drawn >= spacing - t / 2
        {
            return true;
        }
        self.held += 1;
        false
    }

    /// It was drawn now: the next frame's cadence starts here.
    pub fn drawn(&mut self, target: i64, now: i64) {
        let lead = (target - (now + self.interval)) as f64 / 1e6;
        let interval = self.interval as f64 / 1e6;
        if self.timed == 0 {
            self.lead_min = lead;
            self.lead_max = lead;
        }
        self.lead_min = self.lead_min.min(lead);
        self.lead_max = self.lead_max.max(lead);
        self.lead_sum += lead;
        self.timed += 1;
        if lead > interval {
            self.early += 1;
        } else if lead < -interval {
            self.late += 1;
        }
        self.last_target = target;
        self.last_drawn = now;
        self.pending_since = 0;
    }

    /// A frame without a target (a redraw) was drawn: no cadence to keep.
    pub fn untimed(&mut self) {
        self.last_target = 0;
        self.pending_since = 0;
    }

    /// A new file or stream: no cadence, nothing pending.
    pub fn restart(&mut self) {
        self.untimed();
    }

    pub fn interval_ns(&self) -> i64 {
        self.interval
    }

    pub fn stats(&self) -> PacerStats {
        PacerStats {
            held: self.held,
            timed: self.timed,
            early: self.early,
            late: self.late,
            lead_min_ms: self.lead_min,
            lead_mean_ms: if self.timed != 0 {
                self.lead_sum / self.timed as f64
            } else {
                0.0
            },
            lead_max_ms: self.lead_max,
        }
    }
}

/// How the projection pass reconstructs the frame where it is magnified. Minified areas always use trilinear +
/// anisotropic mipmaps.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Default)]
pub enum Filter {
    #[default]
    CatmullRom,
    Sharp,
    Bilinear,
    Nearest,
}

impl Filter {
    /// None for an unknown name; "linear" is bilinear.
    pub fn parse(name: &str) -> Option<Self> {
        match name {
            "catmull-rom" => Some(Self::CatmullRom),
            "sharp" => Some(Self::Sharp),
            "bilinear" | "linear" => Some(Self::Bilinear),
            "nearest" => Some(Self::Nearest),
            _ => None,
        }
    }

    /// Unknown or missing: Catmull-Rom.
    pub fn from_name(name: Option<&str>) -> Self {
        name.and_then(Self::parse).unwrap_or_default()
    }

    pub fn name(self) -> &'static str {
        match self {
            Self::CatmullRom => "catmull-rom",
            Self::Sharp => "sharp",
            Self::Bilinear => "bilinear",
            Self::Nearest => "nearest",
        }
    }

    /// Keys cubic parameter: 0 = bilinear/nearest, -0.5 Catmull-Rom, -0.75 sharper.
    pub fn cubic_a(self) -> f32 {
        match self {
            Self::CatmullRom => -0.5,
            Self::Sharp => -0.75,
            Self::Bilinear | Self::Nearest => 0.0,
        }
    }
}

/// Quality settings: the texture filter of the projection pass and mpv's deband.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Default)]
pub struct Quality {
    pub filter: Filter,
    pub deband: bool,
}

/// Mip levels of a `w` x `h` texture with the full chain.
pub fn video_texture_levels(w: u32, h: u32) -> u32 {
    32 - w.max(h).leading_zeros()
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum State {
    Off,
    Loading,
    Playing,
    Paused,
    Ended,
    Error,
}

impl State {
    pub fn name(self) -> &'static str {
        match self {
            Self::Off => "off",
            Self::Loading => "loading",
            Self::Playing => "playing",
            Self::Paused => "paused",
            Self::Ended => "ended",
            Self::Error => "error",
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn near(a: f32, b: f32) -> bool {
        (a - b).abs() < 1e-3
    }
    // Yaw right / pitch up in degrees -> world direction (the C++ video_test dir()).
    fn dir(yaw: f32, pitch: f32) -> [f32; 3] {
        let (y, p) = (yaw * PI / 180.0, pitch * PI / 180.0);
        [y.sin() * p.cos(), p.sin(), -y.cos() * p.cos()]
    }
    fn uv_is(f: Format, d: [f32; 3], eye: usize, u: f32, v: f32) -> bool {
        video_uv(&f, d, eye).is_some_and(|uv| (uv[0] - u).abs() < 2e-3 && (uv[1] - v).abs() < 2e-3)
    }

    #[test]
    fn projection() {
        let mono360 = Format::default();
        assert!(uv_is(mono360, dir(0.0, 0.0), 0, 0.5, 0.5));
        assert!(uv_is(mono360, dir(90.0, 0.0), 0, 0.75, 0.5));
        assert!(uv_is(mono360, dir(-90.0, 0.0), 0, 0.25, 0.5));
        assert!(uv_is(mono360, dir(0.0, 45.0), 0, 0.5, 0.25));
        assert!(uv_is(mono360, dir(0.0, -89.0), 0, 0.5, 179.0 / 180.0));
        let behind = video_uv(&mono360, dir(179.9, 0.0), 0).unwrap();
        assert!(behind[0] > 0.99 || behind[0] < 0.01);
        assert!(video_uv(&mono360, [0.0, 0.0, 0.0], 0).is_none());
        assert!(video_uv(&mono360, [f32::NAN, 0.0, -1.0], 0).is_none());

        let tb = Format::new(Projection::Equirect360, Stereo::Tb, 180);
        assert!(uv_is(tb, dir(0.0, 0.0), 0, 0.5, 0.25));
        assert!(uv_is(tb, dir(0.0, 0.0), 1, 0.5, 0.75));
        assert!(uv_is(tb, dir(90.0, 0.0), 1, 0.75, 0.75));

        let sbs180 = Format::new(Projection::Equirect180, Stereo::Sbs, 180);
        assert!(uv_is(sbs180, dir(0.0, 0.0), 0, 0.25, 0.5));
        assert!(uv_is(sbs180, dir(0.0, 0.0), 1, 0.75, 0.5));
        assert!(uv_is(sbs180, dir(90.0, 0.0), 0, 0.5, 0.5));
        assert!(uv_is(sbs180, dir(-45.0, 0.0), 1, 0.625, 0.5));
        assert!(video_uv(&sbs180, dir(120.0, 0.0), 0).is_none());
        assert!(video_uv(&sbs180, dir(180.0, 0.0), 1).is_none());

        let fish = Format::new(Projection::Fisheye, Stereo::Sbs, 180);
        assert!(uv_is(fish, dir(0.0, 0.0), 0, 0.25, 0.5));
        assert!(uv_is(fish, dir(90.0, 0.0), 0, 0.5, 0.5));
        assert!(uv_is(fish, dir(0.0, 45.0), 0, 0.25, 0.25));
        assert!(video_uv(&fish, dir(100.0, 0.0), 0).is_none());
        let fish200 = Format::new(Projection::Fisheye, Stereo::Mono, 200);
        assert!(video_uv(&fish200, dir(99.0, 0.0), 0).is_some());
        assert!(uv_is(fish200, dir(100.0, 0.0), 0, 1.0, 0.5));

        let centre = view_ray(&IDENTITY, 0.0, 0.0, 0.5, 0.3);
        assert!(near(centre[0], 0.0) && near(centre[1], 0.0) && near(centre[2], -1.0));
        let corner = view_ray(&IDENTITY, 1.0, 1.0, 0.5, 0.3);
        assert!(near(corner[0], 0.5) && near(corner[1], 0.3));
        let (c, s) = ((PI / 4.0).cos(), (PI / 4.0).sin());
        let right45 = [c, 0.0, s, 0.0, 1.0, 0.0, -s, 0.0, c];
        assert!(uv_is(
            mono360,
            view_ray(&right45, 0.0, 0.0, 0.5, 0.3),
            0,
            0.625,
            0.5
        ));
    }

    #[test]
    fn outputs() {
        let mono = eye_viewports(1920, 1200);
        assert_eq!(
            mono,
            vec![EyeViewport {
                x: 0,
                y: 0,
                width: 1920,
                height: 1200,
                eye: 0
            }]
        );
        assert_eq!(eye_viewports(1536, 960).len(), 1);
        let sbs = eye_viewports(3840, 1200);
        assert_eq!(sbs.len(), 2);
        assert!(
            sbs[0].x == 0
                && sbs[0].width == 1920
                && sbs[1].x == 1920
                && sbs[1].width == 1920
                && sbs[1].eye == 1
        );
        assert_eq!(eye_viewports(3840, 1080).len(), 2);
        assert!(eye_viewports(0, 1080).is_empty());
        let h = half_fov_tan(46.0, 1920, 1200);
        assert!(
            near(
                (h[0] * h[0] + h[1] * h[1]).sqrt(),
                (23.0 * PI / 180.0).tan()
            ) && near(h[0] / h[1], 1.6)
        );

        assert_eq!(
            video_texture_size(7680, 3840, VIDEO_MAX_TEXTURE_SIDE),
            [7680, 3840]
        );
        assert_eq!(
            video_texture_size(11520, 5760, VIDEO_MAX_TEXTURE_SIDE),
            [8192, 4096]
        );
        assert_eq!(
            video_texture_size(8640, 8640, VIDEO_MAX_TEXTURE_SIDE),
            [8192, 8192]
        );
        assert_eq!(video_texture_size(4096, 4096, 2048), [2048, 2048]);
        assert_eq!(video_texture_size(0, 10, VIDEO_MAX_TEXTURE_SIDE), [0, 0]);
    }

    #[test]
    fn filters_and_levels() {
        assert_eq!(Filter::from_name(None), Filter::CatmullRom);
        assert_eq!(Filter::from_name(Some("bogus")), Filter::CatmullRom);
        assert_eq!(Filter::from_name(Some("bilinear")), Filter::Bilinear);
        assert_eq!(Filter::from_name(Some("linear")), Filter::Bilinear);
        assert_eq!(Filter::from_name(Some("sharp")), Filter::Sharp);
        assert_eq!(Filter::Bilinear.cubic_a(), 0.0);
        assert_eq!(Filter::CatmullRom.cubic_a(), -0.5);
        assert_eq!(video_texture_levels(8192, 4096), 14);
        assert_eq!(video_texture_levels(7680, 3840), 13);
        assert_eq!(video_texture_levels(1, 1), 1);
        assert_eq!(
            Format::new(Projection::Fisheye, Stereo::Tb, 205).label(true),
            "Fisheye 205 deg top-bottom 3D"
        );
        assert_eq!(Format::default().label(false), "360° mono");
    }

    #[test]
    fn frame_timing() {
        // Held until its vblank, late frames at once.
        let mut p = FramePacer::default();
        let t = 8_333_333;
        p.tick(0);
        p.tick(t);
        assert_eq!(p.interval_ns(), FramePacer::DEFAULT_INTERVAL_NS); // one gap: no median yet, the default (= t)
        assert!(!p.due(t + 34_000_000, t));
        assert!(!p.due(t + 34_000_000, t + 2 * t));
        assert!(p.due(t + 34_000_000, t + 3 * t));
        p.drawn(t + 34_000_000, t + 3 * t);
        assert!(p.due(t, 5 * t));
        let st = p.stats();
        assert!(st.held == 2 && st.timed == 1 && st.early == 0 && st.late == 0);

        // The cadence anchor, and none after a restart.
        let mut p = FramePacer::default();
        let now = 100 * t;
        let target = now + t + t * 45 / 100;
        p.drawn(target, now);
        let next = now + 4 * t - t * 15 / 100;
        assert!(p.due(target + 4 * t, next));
        p.restart();
        assert!(!p.due(target + 4 * t, next));

        // Never held past MAX_HOLD_NS.
        let mut p = FramePacer::default();
        p.tick(0);
        assert!(!p.due(5_000_000_000, 1));
        assert!(!p.due(5_000_000_000, FramePacer::MAX_HOLD_NS - 1));
        assert!(p.due(5_000_000_000, FramePacer::MAX_HOLD_NS + 1));

        // The interval is the median gap: missed vsyncs and a stall do not move it.
        let mut p = FramePacer::default();
        let mut now = 0;
        for k in 0..60 {
            now += if k % 7 == 3 { 16_666_667 } else { 8_333_333 };
            if k == 30 {
                now += 400_000_000;
            }
            p.tick(now);
        }
        assert!((p.interval_ns() - 8_333_333).abs() < 100_000);
        let mut slow = FramePacer::default();
        now = 0;
        for _ in 0..20 {
            now += 16_666_667;
            slow.tick(now);
        }
        assert!((slow.interval_ns() - 16_666_667).abs() < 100_000);
    }
}
