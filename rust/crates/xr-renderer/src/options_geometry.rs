//! The options panel's geometry: its header controls and tab cells, the gear and Maximize pills under and above a
//! desktop, and the Step-Back Band's Restore pill over a maximized one (from `spike/include/xr/options_geometry.hpp`),
//! plus the supervisor's `maximize_geometry` line (`spike/src/maximize_geometry.hpp`).
//!
//! render_options_panel draws the header and tabs, [`options_header_at`] / [`options_tab_at`] are their hit regions, and
//! tools/native_spike/options_geometry.py mirrors both (`options_geometry_dump` dumps them, tests compare). All maths is
//! `f32`, in the same order as the C++, so the dumps match it bit for bit.
//!
//! The JSON writers at the end print numbers and strings exactly as nlohmann::json's `dump()` does, so the Rust
//! dumps are byte for byte the C++ ones.

use crate::glass::{LEVEL_BAR_H, glass_pill, level_bar_rect, level_bar_scale};
use crate::view::{Canvas, FRect, MeshVertex, Placement, Vec3, cmin, place_point, tile_mesh};

pub const OPTIONS_TRACK_X0: f32 = 300.0;
pub const OPTIONS_TRACK_X1: f32 = 450.0;
// The header's controls (x ranges) and the tab cells.
pub const OPTIONS_SCOPE_X0: f32 = 346.0;
pub const OPTIONS_NEXT_X0: f32 = 440.0;
pub const OPTIONS_CLOSE_X0: f32 = 530.0;
pub const OPTIONS_CLOSE_X1: f32 = 594.0;
pub const OPTIONS_TABS_X: f32 = 16.0;
pub const OPTIONS_TAB_W: f32 = 136.0;
pub const OPTIONS_TAB_PITCH: f32 = 142.0;
pub const OPTIONS_TABS_PER_PAGE: i32 = 4;

/// The header control at a panel point.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum OptionsHeader {
    None,
    Scope,
    Next,
    Close,
}

/// The header control at panel point (x, y): the header is the panel's top 44 px.
pub fn options_header_at(x: f32, y: f32) -> OptionsHeader {
    if y < 0.0 || y >= 44.0 {
        return OptionsHeader::None;
    }
    if (OPTIONS_SCOPE_X0..OPTIONS_NEXT_X0).contains(&x) {
        OptionsHeader::Scope
    } else if (OPTIONS_NEXT_X0..OPTIONS_CLOSE_X0).contains(&x) {
        OptionsHeader::Next
    } else if (OPTIONS_CLOSE_X0..OPTIONS_CLOSE_X1).contains(&x) {
        OptionsHeader::Close
    } else {
        OptionsHeader::None
    }
}

/// The tab (category index) at panel point (x, y), on the page that holds `category`, of `count` tabs; -1 for none.
/// Only the drawn cell counts, not the gap after it, and the next page's tabs are not under the page you see.
pub fn options_tab_at(x: f32, y: f32, category: i32, count: i32) -> i32 {
    if y < 44.0 || y >= 80.0 || x < OPTIONS_TABS_X {
        return -1;
    }
    let cell = ((x - OPTIONS_TABS_X) / OPTIONS_TAB_PITCH) as i32;
    // Wrapping, as the C++ int maths is on any real panel point (no overflow there).
    let index = (category / OPTIONS_TABS_PER_PAGE * OPTIONS_TABS_PER_PAGE).wrapping_add(cell);
    if cell < OPTIONS_TABS_PER_PAGE
        && x - OPTIONS_TABS_X - cell as f32 * OPTIONS_TAB_PITCH < OPTIONS_TAB_W
        && index < count
    {
        index
    } else {
        -1
    }
}

pub const OPTIONS_W: f32 = 600.0;
pub const OPTIONS_H: f32 = 440.0;
pub const OPTIONS_HEADER_H: f32 = 44.0;
pub const OPTIONS_TABS_TOP: f32 = 44.0;
pub const OPTIONS_TABS_H: f32 = 36.0;
pub const OPTIONS_BODY_TOP: f32 = 88.0;
pub const OPTIONS_BODY_BOTTOM: f32 = 392.0;
pub const OPTIONS_ROW_H: f32 = 38.0;
pub const OPTIONS_PAD: f32 = 16.0;
pub const OPTIONS_FOOTER_TOP: f32 = 400.0;

/// The options panel under `tile`: centred on it, its top at the pill's.
pub fn options_panel(tile: &FRect) -> FRect {
    let p = glass_pill(tile);
    FRect::new(tile.x + (tile.width - OPTIONS_W) / 2.0, p.y, OPTIONS_W, OPTIONS_H)
}

/// The gear, 12 px right of the pill.
pub fn options_gear(tile: &FRect) -> FRect {
    let p = glass_pill(tile);
    FRect::new(p.x + p.width + 12.0, p.y, 28.0, 14.0)
}

/// The Maximize pill, left of the level bar, scaled with it.
pub fn options_maximize(tile: &FRect) -> FRect {
    let p = level_bar_rect(tile);
    let s = level_bar_scale(tile);
    FRect::new(p.x - 38.0 * s, p.y, 26.0 * s, 26.0 * s)
}

/// The gear padded by 4 px all round.
pub fn options_gear_hit(tile: &FRect) -> FRect {
    let r = options_gear(tile);
    FRect::new(r.x - 4.0, r.y - 4.0, r.width + 8.0, r.height + 8.0)
}

/// The pill padded by 4 px, but on the right only up to halfway to the gear.
pub fn options_pill_hit(tile: &FRect) -> FRect {
    let r = glass_pill(tile);
    let g = options_gear(tile);
    let left = r.x - 4.0;
    let right = cmin(r.x + r.width + 4.0, (r.x + r.width + g.x) / 2.0);
    FRect::new(left, r.y - 4.0, right - left, r.height + 8.0)
}

/// The Maximize pill padded by 4 px times the level bar's scale.
pub fn options_maximize_hit(tile: &FRect) -> FRect {
    let r = options_maximize(tile);
    let p = 4.0 * level_bar_scale(tile);
    FRect::new(r.x - p, r.y - p, r.width + 2.0 * p, r.height + 2.0 * p)
}

// Restore, the Step-Back Band (Cody's pick, R3): nothing over a maximized picture. Resting the pointer on the top edge
// eases the picture back, about the view's centre, so a band opens above it, and the Restore pill is in that band,
// RESTORE_BAND_GAP above the picture. A picture that already leaves RESTORE_BAND_MIN (a 16:9 or ultrawide one in the
// 16:10 view) stays its size: only the pill comes. Tile px on the desktop's surface, like the bar, at its stepped
// distance, so a curved desktop curves it. Mirrored in restore_band.py.
pub const RESTORE_STEP_SCALE: f32 = 0.84;
pub const RESTORE_STEP_MS: f32 = 220.0;
pub const RESTORE_BAND_MIN: f32 = 46.0;
pub const RESTORE_BAND_GAP: f32 = 12.0;
pub const RESTORE_PILL_W: f32 = 122.0;
pub const RESTORE_PILL_H: f32 = LEVEL_BAR_H;
pub const RESTORE_PAD: f32 = 4.0;
const _: () = assert!(RESTORE_BAND_GAP + RESTORE_PILL_H + 8.0 <= RESTORE_BAND_MIN, "the pill fits the smallest band");

/// How far the picture steps back: not at all when it already leaves the band, else RESTORE_STEP_SCALE.
pub fn options_step_scale(tile: &FRect) -> f32 {
    if tile.y >= RESTORE_BAND_MIN { 1.0 } else { RESTORE_STEP_SCALE }
}

/// `on` `e` (eased 0..1) of the way back: the distance grows, every length scales about the eye.
pub fn options_stepped(mut on: Placement, scale: f32, e: f32) -> Placement {
    on.distance /= 1.0 + (scale - 1.0) * e;
    on
}

/// The Restore pill, tile px at the stepped distance: centred, RESTORE_BAND_GAP above the picture as you see it.
pub fn options_restore_rect(tile: &FRect, scale: f32) -> FRect {
    let w = RESTORE_PILL_W / scale;
    let h = RESTORE_PILL_H / scale;
    FRect::new(tile.x + (tile.width - w) / 2.0, tile.y - (RESTORE_BAND_GAP + RESTORE_PILL_H) / scale, w, h)
}

/// The Restore pill padded by RESTORE_PAD as you see it.
pub fn options_restore_hit(rect: &FRect, scale: f32) -> FRect {
    let p = RESTORE_PAD / scale;
    FRect::new(rect.x - p, rect.y - p, rect.width + 2.0 * p, rect.height + 2.0 * p)
}

/// Where the stepped picture lands on the canvas: its left and right as drawn, its top and bottom at the middle column
/// (where the pill is: a curved desktop's corners stand further out), as the supervisor measures the band and the way
/// back. [left, top, right, bottom] in canvas px through `view`.
pub fn options_picture_box(c: &Canvas, tile: &FRect, on: &Placement, view: &[f32; 9]) -> [f32; 4] {
    let canvas_of = |px: f32, py: f32| -> [f32; 2] {
        let q = place_point(c, tile, on, px, py);
        let x = view[0] * q.x + view[1] * q.y + view[2] * q.z;
        let y = view[3] * q.x + view[4] * q.y + view[5] * q.z;
        let z = view[6] * q.x + view[7] * q.y + view[8] * q.z;
        [(x / -z / c.half_w + 1.0) * c.view_w / 2.0, (1.0 - y / -z / c.half_h) * c.view_h / 2.0]
    };
    let mid = tile.x + tile.width / 2.0;
    [
        canvas_of(tile.x, tile.y + tile.height / 2.0)[0],
        canvas_of(mid, tile.y)[1],
        canvas_of(tile.x + tile.width, tile.y + tile.height / 2.0)[0],
        canvas_of(mid, tile.y + tile.height)[1],
    ]
}

/// The body row at panel point (x, y), scrolled by `scroll` px, of `count` rows; -1 for none.
pub fn options_row_at(x: f32, y: f32, scroll: i32, count: usize) -> i32 {
    if x < 16.0 || x >= 584.0 || y < 88.0 || y >= 392.0 {
        return -1;
    }
    let i = ((y - 88.0 + scroll as f32) / 38.0) as i32;
    if i >= 0 && (i as usize) < count { i } else { -1 }
}

/// Ray (from the eye, direction `ray`) against the same triangle strip used to draw the transformed target
/// (Möller–Trumbore, a little slack at the edges): the nearest hit's distance along the ray, None for a miss.
pub fn options_mesh_ray(mesh: &[MeshVertex], ray: &Vec3) -> Option<f32> {
    let sub = |a: Vec3, b: Vec3| Vec3::new(a.x - b.x, a.y - b.y, a.z - b.z);
    let cross = |a: Vec3, b: Vec3| Vec3::new(a.y * b.z - a.z * b.y, a.z * b.x - a.x * b.z, a.x * b.y - a.y * b.x);
    let dot = |a: Vec3, b: Vec3| a.x * b.x + a.y * b.y + a.z * b.z;
    let ray = *ray;
    let mut best: Option<f32> = None;
    for w in mesh.windows(3) {
        let a = w[0].world;
        let e1 = sub(w[1].world, a);
        let e2 = sub(w[2].world, a);
        let n = cross(ray, e2);
        let d = dot(e1, n);
        if d.abs() < 1e-12 {
            continue;
        }
        let o = Vec3::new(-a.x, -a.y, -a.z);
        let u = dot(o, n) / d;
        if u < -0.0001 || u > 1.0001 {
            continue;
        }
        let q = cross(o, e1);
        let v = dot(ray, q) / d;
        let t = dot(e2, q) / d;
        if v < -0.0001 || u + v > 1.0001 || t <= 0.0 {
            continue;
        }
        if best.is_none_or(|b| t < b) {
            best = Some(t);
        }
    }
    best
}

/// The ray against the Maximize pill's hit rect on the desktop's surface.
pub fn options_maximize_ray(canvas: &Canvas, tile: &FRect, on: &Placement, ray: &Vec3) -> Option<f32> {
    options_mesh_ray(&tile_mesh(canvas, tile, on, &options_maximize_hit(tile)), ray)
}

/// What the supervisor needs of the Step-Back Band, always as it stands fully stepped back, whatever the ease has
/// reached: `placed` is the desktop's placement before the step. Same serializer for the presentation status emitter
/// and the native tests. One JSON object, nlohmann's `dump()` of it (keys sorted, as its std::map holds them).
pub fn maximize_geometry_json(
    monitor: &str,
    canvas: &Canvas,
    tile: &FRect,
    placed: &Placement,
    view: &[f32; 9],
) -> String {
    let scale = options_step_scale(tile);
    let on = options_stepped(*placed, scale, 1.0);
    let r = options_restore_rect(tile, scale);
    let mesh = tile_mesh(canvas, tile, &on, &options_restore_hit(&r, scale));
    let mut out = String::from("{\"maximize_geometry\":{\"canvas\":");
    json_floats(&mut out, &[canvas.view_w, canvas.view_h, canvas.half_w, canvas.half_h]);
    out.push_str(",\"hit_mesh\":[");
    for (i, v) in mesh.iter().enumerate() {
        if i > 0 {
            out.push(',');
        }
        json_floats(&mut out, &[v.world.x, v.world.y, v.world.z]);
    }
    out.push_str("],\"maximized\":true,\"monitor\":");
    json_string(&mut out, monitor);
    out.push_str(",\"picture\":");
    json_floats(&mut out, &options_picture_box(canvas, tile, &on, view));
    out.push_str(",\"placement\":");
    json_floats(&mut out, &[on.x, on.y, on.distance, on.curvature, on.roll]);
    out.push_str(",\"rect\":");
    json_floats(&mut out, &[r.x, r.y, r.width, r.height]);
    out.push_str(",\"tile\":");
    json_floats(&mut out, &[tile.x, tile.y, tile.width, tile.height]);
    out.push_str(",\"view\":");
    json_floats(&mut out, view);
    out.push_str("}}");
    out
}

// --- nlohmann::json dump() ------------------------------------------------------------------------------------------

/// An array of floats as nlohmann dumps it: each stored as a double, no spaces.
pub fn json_floats(out: &mut String, values: &[f32]) {
    out.push('[');
    for (i, &v) in values.iter().enumerate() {
        if i > 0 {
            out.push(',');
        }
        json_number(out, f64::from(v));
    }
    out.push(']');
}

/// A string as nlohmann dumps it (ensure_ascii off): `"` and `\` escaped, the short escapes for \b \t \n \f \r, other
/// control characters as \u00xx (lower-case hex), everything else, UTF-8 included, as it is.
pub fn json_string(out: &mut String, s: &str) {
    out.push('"');
    for ch in s.chars() {
        match ch {
            '\u{8}' => out.push_str("\\b"),
            '\t' => out.push_str("\\t"),
            '\n' => out.push_str("\\n"),
            '\u{c}' => out.push_str("\\f"),
            '\r' => out.push_str("\\r"),
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            c if u32::from(c) <= 0x1f => out.push_str(&format!("\\u{:04x}", u32::from(c))),
            c => out.push(c),
        }
    }
    out.push('"');
}

/// A double as nlohmann dumps it: NaN and the infinities as `null`; else its Grisu2 digits (round-trip, but not always
/// the shortest: Rust's own `{}` can differ), fixed point with a `.0` on integers for decimal exponents -4..15,
/// otherwise `d.ddde+XX` with at least two exponent digits.
pub fn json_number(out: &mut String, v: f64) {
    if !v.is_finite() {
        out.push_str("null");
        return;
    }
    let mut v = v;
    if v.is_sign_negative() {
        v = -v;
        out.push('-');
    }
    if v == 0.0 {
        out.push_str("0.0");
        return;
    }
    let (digits, exponent) = grisu2::grisu2(v);
    format_buffer(out, &digits, exponent, -4, 15);
}

/// v = digits * 10^decimal_exponent, printed fixed point if it is in [10^min_exp, 10^max_exp), else exponential.
fn format_buffer(out: &mut String, digits: &[u8], decimal_exponent: i32, min_exp: i32, max_exp: i32) {
    let k = digits.len() as i32;
    // The position of the decimal point relative to the start of the digits.
    let n = k + decimal_exponent;
    let text = |d: &[u8]| -> String { d.iter().map(|&b| char::from(b)).collect() };
    if k <= n && n <= max_exp {
        // digits[000].0
        out.push_str(&text(digits));
        out.extend(std::iter::repeat_n('0', (n - k) as usize));
        out.push_str(".0");
        return;
    }
    if 0 < n && n <= max_exp {
        // dig.its
        out.push_str(&text(&digits[..n as usize]));
        out.push('.');
        out.push_str(&text(&digits[n as usize..]));
        return;
    }
    if min_exp < n && n <= 0 {
        // 0.[000]digits
        out.push_str("0.");
        out.extend(std::iter::repeat_n('0', (-n) as usize));
        out.push_str(&text(digits));
        return;
    }
    // d.igitse+123 (dE+123 for one digit)
    out.push(char::from(digits[0]));
    if k > 1 {
        out.push('.');
        out.push_str(&text(&digits[1..]));
    }
    out.push('e');
    let e = n - 1;
    out.push(if e < 0 { '-' } else { '+' });
    let e = e.unsigned_abs();
    if e < 10 {
        out.push('0');
    }
    out.push_str(&e.to_string());
}

/// nlohmann's Grisu2 (Loitsch's reference implementation, slightly modified), for a finite positive double.
mod grisu2 {
    /// f * 2^e.
    #[derive(Clone, Copy)]
    struct DiyFp {
        f: u64,
        e: i32,
    }

    impl DiyFp {
        const fn new(f: u64, e: i32) -> Self {
            Self { f, e }
        }

        /// x - y; x.e == y.e and x.f >= y.f.
        fn sub(x: Self, y: Self) -> Self {
            Self::new(x.f.wrapping_sub(y.f), x.e)
        }

        /// x * y, rounded: only the upper 64 bits are kept.
        fn mul(x: Self, y: Self) -> Self {
            let u_lo = x.f & 0xFFFF_FFFF;
            let u_hi = x.f >> 32;
            let v_lo = y.f & 0xFFFF_FFFF;
            let v_hi = y.f >> 32;
            let p0 = u_lo * v_lo;
            let p1 = u_lo * v_hi;
            let p2 = u_hi * v_lo;
            let p3 = u_hi * v_hi;
            let p0_hi = p0 >> 32;
            let p1_lo = p1 & 0xFFFF_FFFF;
            let p1_hi = p1 >> 32;
            let p2_lo = p2 & 0xFFFF_FFFF;
            let p2_hi = p2 >> 32;
            let mut q = p0_hi + p1_lo + p2_lo;
            q += 1u64 << (64 - 32 - 1); // round, ties up
            let h = p3 + p2_hi + p1_hi + (q >> 32);
            Self::new(h, x.e + y.e + 64)
        }

        /// So that the significand is >= 2^63; x.f != 0.
        fn normalize(mut x: Self) -> Self {
            while x.f >> 63 == 0 {
                x.f <<= 1;
                x.e -= 1;
            }
            x
        }

        /// To exponent `target`; the upper x.e - target bits of x.f are zero.
        fn normalize_to(x: Self, target: i32) -> Self {
            Self::new(x.f << (x.e - target), target)
        }
    }

    /// The normalized value and its boundaries m- and m+ (normalized to m+'s exponent).
    fn compute_boundaries(value: f64) -> (DiyFp, DiyFp, DiyFp) {
        const PRECISION: i32 = 53; // includes the hidden bit
        const BIAS: i32 = 1024 - 1 + (PRECISION - 1);
        const MIN_EXP: i32 = 1 - BIAS;
        const HIDDEN_BIT: u64 = 1 << (PRECISION - 1);
        let bits = value.to_bits();
        let e = bits >> (PRECISION - 1);
        let f = bits & (HIDDEN_BIT - 1);
        let v = if e == 0 { DiyFp::new(f, MIN_EXP) } else { DiyFp::new(f + HIDDEN_BIT, e as i32 - BIAS) };
        let lower_boundary_is_closer = f == 0 && e > 1;
        let m_plus = DiyFp::new(2 * v.f + 1, v.e - 1);
        let m_minus =
            if lower_boundary_is_closer { DiyFp::new(4 * v.f - 1, v.e - 2) } else { DiyFp::new(2 * v.f - 1, v.e - 1) };
        let w_plus = DiyFp::normalize(m_plus);
        let w_minus = DiyFp::normalize_to(m_minus, w_plus.e);
        (DiyFp::normalize(v), w_minus, w_plus)
    }

    const ALPHA: i32 = -60;

    /// c = f * 2^e ~= 10^k.
    const CACHED_POWERS: [(u64, i32, i32); 79] = [
        (0xAB70_FE17_C79A_C6CA, -1060, -300),
        (0xFF77_B1FC_BEBC_DC4F, -1034, -292),
        (0xBE56_91EF_416B_D60C, -1007, -284),
        (0x8DD0_1FAD_907F_FC3C, -980, -276),
        (0xD351_5C28_3155_9A83, -954, -268),
        (0x9D71_AC8F_ADA6_C9B5, -927, -260),
        (0xEA9C_2277_23EE_8BCB, -901, -252),
        (0xAECC_4991_4078_536D, -874, -244),
        (0x823C_1279_5DB6_CE57, -847, -236),
        (0xC210_9436_4DFB_5637, -821, -228),
        (0x9096_EA6F_3848_984F, -794, -220),
        (0xD774_85CB_2582_3AC7, -768, -212),
        (0xA086_CFCD_97BF_97F4, -741, -204),
        (0xEF34_0A98_172A_ACE5, -715, -196),
        (0xB238_67FB_2A35_B28E, -688, -188),
        (0x84C8_D4DF_D2C6_3F3B, -661, -180),
        (0xC5DD_4427_1AD3_CDBA, -635, -172),
        (0x936B_9FCE_BB25_C996, -608, -164),
        (0xDBAC_6C24_7D62_A584, -582, -156),
        (0xA3AB_6658_0D5F_DAF6, -555, -148),
        (0xF3E2_F893_DEC3_F126, -529, -140),
        (0xB5B5_ADA8_AAFF_80B8, -502, -132),
        (0x8762_5F05_6C7C_4A8B, -475, -124),
        (0xC9BC_FF60_34C1_3053, -449, -116),
        (0x964E_858C_91BA_2655, -422, -108),
        (0xDFF9_7724_7029_7EBD, -396, -100),
        (0xA6DF_BD9F_B8E5_B88F, -369, -92),
        (0xF8A9_5FCF_8874_7D94, -343, -84),
        (0xB944_7093_8FA8_9BCF, -316, -76),
        (0x8A08_F0F8_BF0F_156B, -289, -68),
        (0xCDB0_2555_6531_31B6, -263, -60),
        (0x993F_E2C6_D07B_7FAC, -236, -52),
        (0xE45C_10C4_2A2B_3B06, -210, -44),
        (0xAA24_2499_6973_92D3, -183, -36),
        (0xFD87_B5F2_8300_CA0E, -157, -28),
        (0xBCE5_0864_9211_1AEB, -130, -20),
        (0x8CBC_CC09_6F50_88CC, -103, -12),
        (0xD1B7_1758_E219_652C, -77, -4),
        (0x9C40_0000_0000_0000, -50, 4),
        (0xE8D4_A510_0000_0000, -24, 12),
        (0xAD78_EBC5_AC62_0000, 3, 20),
        (0x813F_3978_F894_0984, 30, 28),
        (0xC097_CE7B_C907_15B3, 56, 36),
        (0x8F7E_32CE_7BEA_5C70, 83, 44),
        (0xD5D2_38A4_ABE9_8068, 109, 52),
        (0x9F4F_2726_179A_2245, 136, 60),
        (0xED63_A231_D4C4_FB27, 162, 68),
        (0xB0DE_6538_8CC8_ADA8, 189, 76),
        (0x83C7_088E_1AAB_65DB, 216, 84),
        (0xC45D_1DF9_4271_1D9A, 242, 92),
        (0x924D_692C_A61B_E758, 269, 100),
        (0xDA01_EE64_1A70_8DEA, 295, 108),
        (0xA26D_A399_9AEF_774A, 322, 116),
        (0xF209_787B_B47D_6B85, 348, 124),
        (0xB454_E4A1_79DD_1877, 375, 132),
        (0x865B_8692_5B9B_C5C2, 402, 140),
        (0xC835_53C5_C896_5D3D, 428, 148),
        (0x952A_B45C_FA97_A0B3, 455, 156),
        (0xDE46_9FBD_99A0_5FE3, 481, 164),
        (0xA59B_C234_DB39_8C25, 508, 172),
        (0xF6C6_9A72_A398_9F5C, 534, 180),
        (0xB7DC_BF53_54E9_BECE, 561, 188),
        (0x88FC_F317_F222_41E2, 588, 196),
        (0xCC20_CE9B_D35C_78A5, 614, 204),
        (0x9816_5AF3_7B21_53DF, 641, 212),
        (0xE2A0_B5DC_971F_303A, 667, 220),
        (0xA8D9_D153_5CE3_B396, 694, 228),
        (0xFB9B_7CD9_A4A7_443C, 720, 236),
        (0xBB76_4C4C_A7A4_4410, 747, 244),
        (0x8BAB_8EEF_B640_9C1A, 774, 252),
        (0xD01F_EF10_A657_842C, 800, 260),
        (0x9B10_A4E5_E991_3129, 827, 268),
        (0xE710_9BFB_A19C_0C9D, 853, 276),
        (0xAC28_20D9_623B_F429, 880, 284),
        (0x8044_4B5E_7AA7_CF85, 907, 292),
        (0xBF21_E440_03AC_DD2D, 933, 300),
        (0x8E67_9C2F_5E44_FF8F, 960, 308),
        (0xD433_179D_9C8C_B841, 986, 316),
        (0x9E19_DB92_B4E3_1BA9, 1013, 324),
    ];

    /// A cached power of ten c such that alpha <= e_c + e + 64 <= gamma, for a normalized w = f * 2^e.
    fn cached_power_for_binary_exponent(e: i32) -> (u64, i32, i32) {
        const MIN_DEC_EXP: i32 = -300;
        const DEC_STEP: i32 = 8;
        let f = ALPHA - e - 1;
        let k = (f * 78913) / (1 << 18) + i32::from(f > 0);
        let index = (-MIN_DEC_EXP + k + (DEC_STEP - 1)) / DEC_STEP;
        CACHED_POWERS[index as usize]
    }

    /// For n != 0, k such that pow10 = 10^(k-1) <= n < 10^k; for n == 0, 1 and pow10 = 1.
    fn find_largest_pow10(n: u32) -> (i32, u32) {
        let mut pow10 = 1_000_000_000u32;
        let mut k = 10;
        while k > 1 && n < pow10 {
            pow10 /= 10;
            k -= 1;
        }
        (k, pow10)
    }

    fn round(buf: &mut [u8], dist: u64, delta: u64, mut rest: u64, ten_k: u64) {
        let last = buf.len() - 1;
        while rest < dist
            && delta.wrapping_sub(rest) >= ten_k
            && (rest.wrapping_add(ten_k) < dist
                || dist.wrapping_sub(rest) > rest.wrapping_add(ten_k).wrapping_sub(dist))
        {
            buf[last] -= 1;
            rest = rest.wrapping_add(ten_k);
        }
    }

    /// V = digits * 10^decimal_exponent with M- <= V <= M+ (normalized, one exponent in -60..-32).
    fn digit_gen(m_minus: DiyFp, w: DiyFp, m_plus: DiyFp, decimal_exponent: &mut i32) -> Vec<u8> {
        let mut buf = Vec::with_capacity(17);
        let mut delta = DiyFp::sub(m_plus, m_minus).f;
        let mut dist = DiyFp::sub(m_plus, w).f;
        let shift = -m_plus.e;
        let one_f = 1u64 << shift;
        let mut p1 = (m_plus.f >> shift) as u32;
        let mut p2 = m_plus.f & (one_f - 1);
        let (k, mut pow10) = find_largest_pow10(p1);
        let mut n = k;
        while n > 0 {
            let d = p1 / pow10;
            let r = p1 % pow10;
            buf.push(b'0' + d as u8);
            p1 = r;
            n -= 1;
            let rest = (u64::from(p1) << shift) + p2;
            if rest <= delta {
                *decimal_exponent += n;
                let ten_n = u64::from(pow10) << shift;
                round(&mut buf, dist, delta, rest, ten_n);
                return buf;
            }
            pow10 /= 10;
        }
        let mut m = 0;
        loop {
            p2 = p2.wrapping_mul(10);
            let d = p2 >> shift;
            let r = p2 & (one_f - 1);
            buf.push(b'0' + d as u8);
            p2 = r;
            m += 1;
            delta = delta.wrapping_mul(10);
            dist = dist.wrapping_mul(10);
            if p2 <= delta {
                break;
            }
        }
        *decimal_exponent -= m;
        round(&mut buf, dist, delta, p2, one_f);
        buf
    }

    /// The digits and decimal exponent of a finite positive double: value ~= digits * 10^exponent.
    pub(super) fn grisu2(value: f64) -> (Vec<u8>, i32) {
        let (v, m_minus, m_plus) = compute_boundaries(value);
        let (cf, ce, ck) = cached_power_for_binary_exponent(m_plus.e);
        let c_minus_k = DiyFp::new(cf, ce); // ~= 10^-k
        let w = DiyFp::mul(v, c_minus_k);
        let w_minus = DiyFp::mul(m_minus, c_minus_k);
        let w_plus = DiyFp::mul(m_plus, c_minus_k);
        let lo = DiyFp::new(w_minus.f + 1, w_minus.e);
        let hi = DiyFp::new(w_plus.f - 1, w_plus.e);
        let mut decimal_exponent = -ck;
        let digits = digit_gen(lo, w, hi, &mut decimal_exponent);
        (digits, decimal_exponent)
    }
}
