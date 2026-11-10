//! Material Design 3 for the renderer's in-XR chrome (`spike/include/xr/material.hpp`, `spike/src/material.cpp`): one
//! tonal palette from the Omarchy theme colour, plus M3's state layer, shape and elevation tokens, sized for the glasses.
//! Pure and offline-testable.
//!
//! The palette. The seed is the theme colour the highlight border and glow use (Hyprland `general:col.active_border`),
//! so a theme switch recolours everything live. From it comes an M3 *fidelity* dark scheme: the primary palette keeps
//! the seed's own hue and chroma (a grey theme stays grey, a pale one stays pale), the primary container is the seed
//! itself at its own tone (pressed into [`MD_CONTAINER_TONE_LO`]..[`MD_CONTAINER_TONE_HI`] so a near-black accent still
//! shows on the see-through OLED and a white one does not glare), and the secondary, neutral and neutral-variant
//! palettes are the seed's hue at less chroma. Roles take M3's dark-scheme tones. Tone is CIE L* (M3's HCT tone); hue
//! and chroma are OKLCH's, so a blue stays blue as it is lightened (CIELAB's hue drifts toward purple there), and a role
//! out of the sRGB gamut keeps its hue and tone and gives up chroma.
//!
//! The glasses. Black is see-through on their micro-OLED panels, and nothing drawn here may pass the cards' 191/255
//! image-retention cap (`hud::CARD_MAX_LEVEL`). Every role is scaled by [`MD_OLED_LEVEL`] in sRGB: the brightest
//! possible role (white) lands exactly on the cap, and every role keeps its place against the others.
//!
//! The maths is `f64` and `f32` in the C++ order, with the same libm calls, so the roles match the C++ bit for bit.

use std::f64::consts::PI;

/// sRGB, 0..1.
pub type MdRgb = [f32; 3];

/// Before the supervisor's first `style` op: the compositor's default border colour (#8d8d8d), a neutral grey scheme.
pub const MD_DEFAULT_SEED: MdRgb = [0x8d as f32 / 255.0, 0x8d as f32 / 255.0, 0x8d as f32 / 255.0];
/// `hud::CARD_MAX_LEVEL / 255`: every role is scaled by this.
pub const MD_OLED_LEVEL: f32 = 191.0 / 255.0;
/// The primary container (the collapsed pill) is the seed at its own tone, pressed into this range (linearly, so a
/// lighter theme colour still gives a lighter pill).
pub const MD_CONTAINER_TONE_LO: f32 = 28.0;
pub const MD_CONTAINER_TONE_HI: f32 = 82.0;

/// The dark scheme's colour roles, already scaled by [`MD_OLED_LEVEL`].
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct MaterialScheme {
    pub primary: MdRgb,
    pub on_primary: MdRgb,
    pub primary_container: MdRgb,
    pub on_primary_container: MdRgb,
    /// The primary palette at tone 98: a hovered or dragged icon (no container to put a state layer on).
    pub primary_bright: MdRgb,
    pub secondary_container: MdRgb,
    pub on_secondary_container: MdRgb,
    pub error_container: MdRgb,
    pub on_error_container: MdRgb,
    pub surface: MdRgb,
    pub surface_container_low: MdRgb,
    pub surface_container: MdRgb,
    pub surface_container_high: MdRgb,
    pub surface_container_highest: MdRgb,
    pub on_surface: MdRgb,
    pub on_surface_variant: MdRgb,
    pub outline: MdRgb,
    pub outline_variant: MdRgb,
    /// The seed's L*.
    pub seed_tone: f32,
    /// The primary container's L*.
    pub container_tone: f32,
}

/// OKLCH hue (degrees) and chroma, CIE L* tone (0..100).
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct MdHct {
    pub hue: f32,
    pub chroma: f32,
    pub tone: f32,
}

// --- State layers, shape, elevation ------------------------------------------------------------------------------

/// A state layer: the content colour over its container at an M3 opacity (hover 8%, focus and pressed 10%, dragged 16%).
pub const MD_STATE_HOVER: f32 = 0.08;
pub const MD_STATE_FOCUS: f32 = 0.10;
pub const MD_STATE_PRESSED: f32 = 0.10;
pub const MD_STATE_DRAGGED: f32 = 0.16;
/// Disabled: the container is on-surface at 12%, the content on-surface at 38%.
pub const MD_DISABLED_CONTAINER: f32 = 0.12;
pub const MD_DISABLED_CONTENT: f32 = 0.38;
/// Shape scale: corner radii (canvas / output px). Fully rounded shapes use half their height.
pub const MD_SHAPE_EXTRA_SMALL: f32 = 4.0;
pub const MD_SHAPE_SMALL: f32 = 8.0;
pub const MD_SHAPE_MEDIUM: f32 = 12.0;
pub const MD_SHAPE_LARGE: f32 = 16.0;
pub const MD_SHAPE_EXTRA_LARGE: f32 = 28.0;
pub const MD_ELEVATION_PILL: f32 = 1.0;
pub const MD_ELEVATION_HOVER: f32 = 2.0;
pub const MD_ELEVATION_DIALOG: f32 = 3.0;
pub const MD_ELEVATION_DRAGGED: f32 = 4.0;

/// One soft drop shadow: its opacity, blur (Gaussian sigma) and downward offset, px.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct MdShadow {
    pub alpha: f32,
    pub sigma: f32,
    pub dy: f32,
}

impl Default for MdShadow {
    fn default() -> Self {
        Self { alpha: 0.0, sigma: 1.0, dy: 0.0 }
    }
}

// `std::clamp(v, lo, hi)`: `v < lo ? lo : hi < v ? hi : v`, so a NaN comes back unchanged.
fn clampf(v: f32, lo: f32, hi: f32) -> f32 {
    if v < lo {
        lo
    } else if hi < v {
        hi
    } else {
        v
    }
}

fn clampd(v: f64, lo: f64, hi: f64) -> f64 {
    if v < lo {
        lo
    } else if hi < v {
        hi
    } else {
        v
    }
}

fn unit(v: f32) -> f32 {
    if v.is_finite() { clampf(v, 0.0, 1.0) } else { 0.0 }
}

fn to_linear(c: f64) -> f64 {
    if c <= 0.04045 { c / 12.92 } else { ((c + 0.055) / 1.055).powf(2.4) }
}

fn to_srgb(c: f64) -> f64 {
    if c <= 0.0031308 { 12.92 * c } else { 1.055 * c.powf(1.0 / 2.4) - 0.055 }
}

#[derive(Clone, Copy, Default)]
struct Lin {
    r: f64,
    g: f64,
    b: f64,
}

#[derive(Clone, Copy, Default)]
struct Lab {
    l: f64,
    a: f64,
    b: f64,
}

fn linear(c: MdRgb) -> Lin {
    Lin { r: to_linear(f64::from(unit(c[0]))), g: to_linear(f64::from(unit(c[1]))), b: to_linear(f64::from(unit(c[2]))) }
}

fn luminance(c: &Lin) -> f64 {
    0.2126 * c.r + 0.7152 * c.g + 0.0722 * c.b
}

// OKLab (Björn Ottosson, 2020), from and to linear sRGB.
#[allow(clippy::excessive_precision)]
fn oklab(c: &Lin) -> Lab {
    let l = (0.4122214708 * c.r + 0.5363325363 * c.g + 0.0514459929 * c.b).cbrt();
    let m = (0.2119034982 * c.r + 0.6806995451 * c.g + 0.1073969566 * c.b).cbrt();
    let s = (0.0883024619 * c.r + 0.2817188376 * c.g + 0.6299787005 * c.b).cbrt();
    Lab {
        l: 0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
        a: 1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
        b: 0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s,
    }
}

#[allow(clippy::excessive_precision)]
fn from_oklab(c: &Lab) -> Lin {
    let l = c.l + 0.3963377774 * c.a + 0.2158037573 * c.b;
    let m = c.l - 0.1055613458 * c.a - 0.0638541728 * c.b;
    let s = c.l - 0.0894841775 * c.a - 1.2914855480 * c.b;
    let (l3, m3, s3) = (l * l * l, m * m * m, s * s * s);
    Lin {
        r: 4.0767416621 * l3 - 3.3077115913 * m3 + 0.2309699292 * s3,
        g: -1.2684380046 * l3 + 2.6097574011 * m3 - 0.3413193965 * s3,
        b: -0.0041960863 * l3 - 0.7034186147 * m3 + 1.7076147010 * s3,
    }
}

// CIE L* <-> relative luminance Y.
const EPSILON: f64 = 216.0 / 24389.0;
const KAPPA: f64 = 24389.0 / 27.0;

fn y_from_tone(t: f64) -> f64 {
    if t > KAPPA * EPSILON { ((t + 16.0) / 116.0).powf(3.0) } else { t / KAPPA }
}

fn tone_from_y(y: f64) -> f64 {
    if y > EPSILON { 116.0 * y.cbrt() - 16.0 } else { y * KAPPA }
}

fn in_gamut(c: &Lin) -> bool {
    const E: f64 = 1e-6;
    c.r >= -E && c.r <= 1.0 + E && c.g >= -E && c.g <= 1.0 + E && c.b >= -E && c.b <= 1.0 + E
}

// OKLab lightness that gives luminance `y` at chroma (a, b): luminance grows with L (bisection).
fn at_luminance(y: f64, a: f64, b: f64) -> Lin {
    let (mut lo, mut hi) = (0.0_f64, 1.05_f64);
    for _ in 0..30 {
        let mid = (lo + hi) / 2.0;
        if luminance(&from_oklab(&Lab { l: mid, a, b })) < y {
            lo = mid;
        } else {
            hi = mid;
        }
    }
    from_oklab(&Lab { l: (lo + hi) / 2.0, a, b })
}

fn encode(c: &Lin) -> MdRgb {
    let e = |v: f64| clampd(to_srgb(clampd(v, 0.0, 1.0)), 0.0, 1.0) as f32;
    [e(c.r), e(c.g), e(c.b)]
}

fn dim(c: MdRgb) -> MdRgb {
    [c[0] * MD_OLED_LEVEL, c[1] * MD_OLED_LEVEL, c[2] * MD_OLED_LEVEL]
}

/// Hue, chroma and tone of an sRGB colour (unscaled).
pub fn md_hct(srgb: MdRgb) -> MdHct {
    let c = linear(srgb);
    let lab = oklab(&c);
    let mut hue = lab.b.atan2(lab.a) * 180.0 / PI;
    if hue < 0.0 {
        hue += 360.0;
    }
    MdHct { hue: hue as f32, chroma: lab.a.hypot(lab.b) as f32, tone: tone_from_y(luminance(&c)) as f32 }
}

/// CIE L* of an sRGB colour.
pub fn md_tone(srgb: MdRgb) -> f32 {
    tone_from_y(luminance(&linear(srgb))) as f32
}

/// The colour of `hue` / `chroma` at `tone`, in sRGB: exactly that tone, and that hue unless the chroma is 0; when the
/// chroma does not fit the gamut there, the most that does.
pub fn md_color(hue: f32, chroma: f32, tone: f32) -> MdRgb {
    let t = if tone.is_finite() { clampd(f64::from(tone), 0.0, 100.0) } else { 0.0 };
    if t <= 0.0 {
        return [0.0, 0.0, 0.0];
    }
    if t >= 100.0 {
        return [1.0, 1.0, 1.0];
    }
    let y = y_from_tone(t);
    let h = f64::from(if hue.is_finite() { hue } else { 0.0 }) * PI / 180.0;
    let (ca, sa) = (h.cos(), h.sin());
    // std::max(0.0, chroma): the chroma unless it is not above 0.
    let c = if chroma.is_finite() {
        let c = f64::from(chroma);
        if 0.0 < c { c } else { 0.0 }
    } else {
        0.0
    };
    let mut best = at_luminance(y, c * ca, c * sa);
    if in_gamut(&best) {
        return encode(&best);
    }
    // Out of gamut: the most chroma that fits at this hue and tone (chroma 0, a grey, always does).
    let (mut lo, mut hi) = (0.0_f64, c);
    best = at_luminance(y, 0.0, 0.0);
    for _ in 0..18 {
        let mid = (lo + hi) / 2.0;
        let m = at_luminance(y, mid * ca, mid * sa);
        if in_gamut(&m) {
            lo = mid;
            best = m;
        } else {
            hi = mid;
        }
    }
    encode(&best)
}

/// WCAG 2 contrast ratio between two sRGB colours (1..21).
pub fn md_contrast(a: MdRgb, b: MdRgb) -> f32 {
    let la = luminance(&linear(a));
    let lb = luminance(&linear(b));
    // std::max(la, lb) and std::min(la, lb).
    let hi = if la < lb { lb } else { la };
    let lo = if lb < la { lb } else { la };
    ((hi + 0.05) / (lo + 0.05)) as f32
}

/// The scheme for theme colour `seed` (channels clamped to 0..1; non-finite ones count as 0).
pub fn material_scheme(seed: MdRgb) -> MaterialScheme {
    let s = [unit(seed[0]), unit(seed[1]), unit(seed[2])];
    let hct = md_hct(s);
    let (hue, c) = (hct.hue, hct.chroma);
    // Fidelity: the primary palette is the seed's own chroma; the others are its hue at less (a grey seed: all grey).
    // std::min(x, cap): `cap < x ? cap : x`.
    let min = |x: f32, cap: f32| if cap < x { cap } else { x };
    let (primary, secondary, neutral, variant) = (c, c * 0.45, min(c / 6.0, 0.016), min(c / 4.5, 0.024));
    let p = |t: f32| md_color(hue, primary, t);
    let sec = |t: f32| md_color(hue, secondary, t);
    let n = |t: f32| md_color(hue, neutral, t);
    let v = |t: f32| md_color(hue, variant, t);
    // M3's baseline error colour, #B3261E.
    let error = md_hct([0xb3 as f32 / 255.0, 0x26 as f32 / 255.0, 0x1e as f32 / 255.0]);
    let e = |t: f32| md_color(error.hue, error.chroma, t);
    let mut m = MaterialScheme {
        seed_tone: hct.tone,
        container_tone: MD_CONTAINER_TONE_LO
            + (MD_CONTAINER_TONE_HI - MD_CONTAINER_TONE_LO) * clampf(hct.tone, 0.0, 100.0) / 100.0,
        ..MaterialScheme::default()
    };
    m.primary = p(80.0);
    m.on_primary = p(20.0);
    m.primary_container = p(m.container_tone);
    // On the container: whichever of a light or a dark primary tone stands out more from it.
    let (light, dark) = (p(92.0), p(12.0));
    m.on_primary_container =
        if md_contrast(light, m.primary_container) >= md_contrast(dark, m.primary_container) { light } else { dark };
    m.primary_bright = p(98.0);
    m.secondary_container = sec(30.0);
    m.on_secondary_container = sec(90.0);
    m.error_container = e(30.0);
    m.on_error_container = e(90.0);
    m.surface = n(6.0);
    m.surface_container_low = n(10.0);
    m.surface_container = n(12.0);
    m.surface_container_high = n(17.0);
    m.surface_container_highest = n(22.0);
    m.on_surface = n(90.0);
    m.on_surface_variant = v(80.0);
    m.outline = v(60.0);
    m.outline_variant = v(30.0);
    for r in [
        &mut m.primary,
        &mut m.on_primary,
        &mut m.primary_container,
        &mut m.on_primary_container,
        &mut m.primary_bright,
        &mut m.secondary_container,
        &mut m.on_secondary_container,
        &mut m.error_container,
        &mut m.on_error_container,
        &mut m.surface,
        &mut m.surface_container_low,
        &mut m.surface_container,
        &mut m.surface_container_high,
        &mut m.surface_container_highest,
        &mut m.on_surface,
        &mut m.on_surface_variant,
        &mut m.outline,
        &mut m.outline_variant,
    ] {
        *r = dim(*r);
    }
    m
}

/// `container * (1 - o) + content * o`, `o` clamped to 0..1.
pub fn md_layer(container: MdRgb, content: MdRgb, opacity: f32) -> MdRgb {
    let o = unit(opacity); // exact at both ends: 0 is the container, 1 the content
    [
        container[0] * (1.0 - o) + content[0] * o,
        container[1] * (1.0 - o) + content[1] * o,
        container[2] * (1.0 - o) + content[2] * o,
    ]
}

/// Elevation level 0..5 (M3: 0, 1, 3, 6, 8, 12 dp, interpolated in between) as one soft drop shadow. Level 0 casts none.
pub fn md_elevation(level: f32) -> MdShadow {
    const DP: [f32; 6] = [0.0, 1.0, 3.0, 6.0, 8.0, 12.0];
    let l = if level.is_finite() { clampf(level, 0.0, 5.0) } else { 0.0 };
    let i = (l as usize).min(4);
    let dp = DP[i] + (DP[i + 1] - DP[i]) * (l - i as f32);
    if dp <= 0.0 {
        return MdShadow { alpha: 0.0, sigma: 1.0, dy: 0.0 };
    }
    let a = 0.4 + 0.02 * dp;
    MdShadow { alpha: if a < 0.6 { a } else { 0.6 }, sigma: 1.0 + 0.75 * dp, dy: 0.5 * dp }
}
