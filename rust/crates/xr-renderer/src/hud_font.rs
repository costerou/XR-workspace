//! System font for the HUD cards and panels (the C++ `HudFont`, `spike/src/hud_font.cpp`): fontconfig finds the
//! installed file, FreeType rasterises. Reads local font files only; nothing is downloaded. Any failure is an `Err` with
//! the C++ reason, and the caller keeps the built-in pixel font.
//!
//! Both libraries go through the thin hand-written FFI below (like `video/mpv.rs`). Only the leading fields of the
//! FreeType records that are read are declared; the records are only ever reached through FreeType's own pointers.

use std::cell::RefCell;
use std::collections::HashMap;
use std::ffi::{CStr, CString, c_char, c_int, c_long, c_uint, c_ulong, c_void};
use std::ptr;

use crate::hud::{FontMetrics, HudTextMetrics};

// --- FreeType --------------------------------------------------------------------------------------------------------

type FtError = c_int;
type FtPos = c_long;
type FtLibrary = *mut c_void;
type FtFace = *mut FtFaceRec;

#[repr(C)]
struct FtGeneric {
    data: *mut c_void,
    finalizer: *mut c_void,
}

#[repr(C)]
struct FtBBox {
    x_min: FtPos,
    y_min: FtPos,
    x_max: FtPos,
    y_max: FtPos,
}

/// The public head of `FT_FaceRec` (freetype.h); private fields follow it.
#[repr(C)]
struct FtFaceRec {
    num_faces: c_long,
    face_index: c_long,
    face_flags: c_long,
    style_flags: c_long,
    num_glyphs: c_long,
    family_name: *mut c_char,
    style_name: *mut c_char,
    num_fixed_sizes: c_int,
    available_sizes: *mut c_void,
    num_charmaps: c_int,
    charmaps: *mut c_void,
    generic: FtGeneric,
    bbox: FtBBox,
    units_per_em: u16,
    ascender: i16,
    descender: i16,
    height: i16,
    max_advance_width: i16,
    max_advance_height: i16,
    underline_position: i16,
    underline_thickness: i16,
    glyph: *mut FtGlyphSlotRec,
    size: *mut FtSizeRec,
    charmap: *mut c_void,
}

#[repr(C)]
struct FtSizeMetrics {
    x_ppem: u16,
    y_ppem: u16,
    x_scale: c_long,
    y_scale: c_long,
    ascender: FtPos,
    descender: FtPos,
    height: FtPos,
    max_advance: FtPos,
}

#[repr(C)]
struct FtSizeRec {
    face: FtFace,
    generic: FtGeneric,
    metrics: FtSizeMetrics,
    internal: *mut c_void,
}

#[repr(C)]
struct FtVector {
    x: FtPos,
    y: FtPos,
}

#[repr(C)]
struct FtGlyphMetrics {
    width: FtPos,
    height: FtPos,
    hori_bearing_x: FtPos,
    hori_bearing_y: FtPos,
    hori_advance: FtPos,
    vert_bearing_x: FtPos,
    vert_bearing_y: FtPos,
    vert_advance: FtPos,
}

#[repr(C)]
struct FtBitmap {
    rows: c_uint,
    width: c_uint,
    pitch: c_int,
    buffer: *mut u8,
    num_grays: u16,
    pixel_mode: u8,
    palette_mode: u8,
    palette: *mut c_void,
}

/// The public head of `FT_GlyphSlotRec`, up to the bitmap position.
#[repr(C)]
struct FtGlyphSlotRec {
    library: FtLibrary,
    face: FtFace,
    next: *mut c_void,
    glyph_index: c_uint,
    generic: FtGeneric,
    metrics: FtGlyphMetrics,
    linear_hori_advance: c_long,
    linear_vert_advance: c_long,
    advance: FtVector,
    format: c_uint,
    bitmap: FtBitmap,
    bitmap_left: c_int,
    bitmap_top: c_int,
}

const FT_FACE_FLAG_SCALABLE: c_long = 1;
const FT_LOAD_DEFAULT: i32 = 0;
const FT_LOAD_RENDER: i32 = 1 << 2;
const FT_LOAD_TARGET_LIGHT: i32 = 1 << 16; // FT_LOAD_TARGET_(FT_RENDER_MODE_LIGHT)
const FT_PIXEL_MODE_GRAY: u8 = 2;

#[link(name = "freetype")]
unsafe extern "C" {
    fn FT_Init_FreeType(alibrary: *mut FtLibrary) -> FtError;
    fn FT_Done_FreeType(library: FtLibrary) -> FtError;
    fn FT_New_Face(library: FtLibrary, filepathname: *const c_char, face_index: c_long, aface: *mut FtFace) -> FtError;
    fn FT_Done_Face(face: FtFace) -> FtError;
    fn FT_Set_Pixel_Sizes(face: FtFace, pixel_width: c_uint, pixel_height: c_uint) -> FtError;
    fn FT_Load_Char(face: FtFace, char_code: c_ulong, load_flags: i32) -> FtError;
}

// --- fontconfig ------------------------------------------------------------------------------------------------------

#[repr(C)]
struct FcConfig {
    _opaque: [u8; 0],
}
#[repr(C)]
struct FcPattern {
    _opaque: [u8; 0],
}

const FC_MATCH_PATTERN: c_int = 0;
const FC_RESULT_MATCH: c_int = 0;
const FC_RESULT_NO_MATCH: c_int = 1;
const FC_WEIGHT_REGULAR: c_int = 80;
const FC_WEIGHT_BOLD: c_int = 200;

#[link(name = "fontconfig")]
unsafe extern "C" {
    fn FcInitLoadConfigAndFonts() -> *mut FcConfig;
    fn FcConfigDestroy(config: *mut FcConfig);
    fn FcPatternCreate() -> *mut FcPattern;
    fn FcPatternDestroy(p: *mut FcPattern);
    fn FcPatternAddString(p: *mut FcPattern, object: *const c_char, s: *const u8) -> c_int;
    fn FcPatternAddInteger(p: *mut FcPattern, object: *const c_char, i: c_int) -> c_int;
    fn FcPatternAddBool(p: *mut FcPattern, object: *const c_char, b: c_int) -> c_int;
    fn FcConfigSubstitute(config: *mut FcConfig, p: *mut FcPattern, kind: c_int) -> c_int;
    fn FcDefaultSubstitute(p: *mut FcPattern);
    fn FcFontMatch(config: *mut FcConfig, p: *mut FcPattern, result: *mut c_int) -> *mut FcPattern;
    fn FcPatternGetString(p: *const FcPattern, object: *const c_char, n: c_int, s: *mut *mut u8) -> c_int;
}

/// The best scalable match for `family` at a weight: (file, family name), either empty when fontconfig has none.
/// # Safety
/// `config` is a live fontconfig configuration.
unsafe fn match_font(config: *mut FcConfig, family: &CStr, bold: bool) -> (Option<CString>, Vec<u8>) {
    // SAFETY: every pointer passed is live: `config` (the caller's), the pattern created here, and NUL-terminated
    // strings; the strings fontconfig returns belong to `best` and are copied before it is destroyed.
    unsafe {
        let pattern = FcPatternCreate();
        if pattern.is_null() {
            return (None, Vec::new());
        }
        FcPatternAddString(pattern, c"family".as_ptr(), family.as_ptr().cast());
        FcPatternAddInteger(pattern, c"weight".as_ptr(), if bold { FC_WEIGHT_BOLD } else { FC_WEIGHT_REGULAR });
        FcPatternAddBool(pattern, c"scalable".as_ptr(), 1);
        FcConfigSubstitute(config, pattern, FC_MATCH_PATTERN);
        FcDefaultSubstitute(pattern);
        let mut result = FC_RESULT_NO_MATCH;
        let best = FcFontMatch(config, pattern, &mut result);
        FcPatternDestroy(pattern);
        let (mut path, mut found) = (None, Vec::new());
        if !best.is_null() {
            let mut s: *mut u8 = ptr::null_mut();
            if FcPatternGetString(best, c"file".as_ptr(), 0, &mut s) == FC_RESULT_MATCH && !s.is_null() {
                path = Some(CStr::from_ptr(s.cast()).to_owned());
            }
            let mut s: *mut u8 = ptr::null_mut();
            if FcPatternGetString(best, c"family".as_ptr(), 0, &mut s) == FC_RESULT_MATCH && !s.is_null() {
                found = CStr::from_ptr(s.cast()).to_bytes().to_vec();
            }
            FcPatternDestroy(best);
        }
        (path.filter(|p| !p.is_empty()), found)
    }
}

// --- The font --------------------------------------------------------------------------------------------------------

/// Minimal UTF-8 decoder (invalid bytes give U+FFFD); returns the code point at `*i` and moves `*i` past it, without
/// allocating.
fn next_code_point(s: &[u8], i: &mut usize) -> u32 {
    let c = s[*i];
    let n: usize = if c < 0x80 {
        0
    } else if c >> 5 == 6 {
        1
    } else if c >> 4 == 14 {
        2
    } else if c >> 3 == 30 {
        3
    } else {
        *i += 1;
        return 0xFFFD;
    };
    if *i + n >= s.len() + usize::from(n == 0) {
        *i += 1;
        return 0xFFFD;
    }
    let mut cp = u32::from(match n {
        0 => c,
        1 => c & 0x1F,
        2 => c & 0x0F,
        _ => c & 0x07,
    });
    for k in 1..=n {
        let d = s[*i + k];
        if d >> 6 != 2 {
            *i += 1;
            return 0xFFFD;
        }
        cp = (cp << 6) | u32::from(d & 0x3F);
    }
    *i += n + 1;
    cp
}

/// Cache key: code point (< 2^21), pixel size, bold.
fn glyph_key(cp: u32, px: u32, bold: bool) -> u64 {
    u64::from(cp) | u64::from(px) << 21 | u64::from(bold) << 63
}

/// A glyph keeps only its covered pixels, row by row (the order they are plotted in), with the coverage already
/// divided by 255.
#[derive(Default)]
struct Glyph {
    ok: bool,
    pixels: Vec<(i32, i32, f32)>,
    left: i32,
    top: i32,
    advance: FtPos,
}

/// Bounded caches: cleared when full.
const MAX_CACHED: usize = 4096;

/// An installed scalable font, regular and (when the family has one) bold.
///
/// Rasterised glyphs, advances and size metrics are cached per (code point, pixel size, bold): cards are re-rendered
/// whenever a value changes, and FreeType hinting and rasterising is most of that cost. Exactly what FreeType returned
/// is kept, so cached text draws byte for byte the same.
pub struct HudFont {
    library: FtLibrary,
    regular: FtFace,
    bold: FtFace,
    description: String,
    glyphs: RefCell<HashMap<u64, Glyph>>,
    advances: RefCell<HashMap<u64, (bool, FtPos)>>,
    /// Per (size, bold): what `FT_Set_Pixel_Sizes` gave (or zeros if it failed).
    metrics: RefCell<HashMap<u64, FontMetrics>>,
}

impl Drop for HudFont {
    fn drop(&mut self) {
        // SAFETY: the faces and the library were created by FreeType here and are freed once, faces first.
        unsafe {
            if !self.bold.is_null() && self.bold != self.regular {
                FT_Done_Face(self.bold);
            }
            if !self.regular.is_null() {
                FT_Done_Face(self.regular);
            }
            if !self.library.is_null() {
                FT_Done_FreeType(self.library);
            }
        }
    }
}

impl HudFont {
    /// Loads `family` (regular, plus bold when installed as its own file). Fails with the C++ reasons:
    /// "fontconfig unavailable", "font not installed: <family>", "freetype init failed", "font load failed: <path>".
    pub fn load(family: &str) -> Result<HudFont, String> {
        // SAFETY: fontconfig's configuration lives until destroyed here; FreeType writes its handles into the fields
        // of `font`, whose Drop frees whatever was created when a later step fails.
        unsafe {
            let config = FcInitLoadConfigAndFonts();
            if config.is_null() {
                return Err("fontconfig unavailable".into());
            }
            // The C++ passes c_str(): a family with a NUL is looked up up to it (and then never matches).
            let query = CString::new(family.split('\0').next().unwrap_or_default()).unwrap_or_default();
            let (path, found) = match_font(config, &query, false);
            let (bold_path, found_bold) = match_font(config, &query, true);
            FcConfigDestroy(config);
            // fontconfig always proposes something; only accept the family that was asked for.
            let Some(path) = path.filter(|_| found == family.as_bytes()) else {
                return Err(format!("font not installed: {family}"));
            };
            let mut font = HudFont {
                library: ptr::null_mut(),
                regular: ptr::null_mut(),
                bold: ptr::null_mut(),
                description: String::new(),
                glyphs: RefCell::default(),
                advances: RefCell::default(),
                metrics: RefCell::default(),
            };
            if FT_Init_FreeType(&mut font.library) != 0 {
                font.library = ptr::null_mut();
                return Err("freetype init failed".into());
            }
            if FT_New_Face(font.library, path.as_ptr(), 0, &mut font.regular) != 0 {
                font.regular = ptr::null_mut();
                return Err(format!("font load failed: {}", path.to_string_lossy()));
            }
            if (*font.regular).face_flags & FT_FACE_FLAG_SCALABLE == 0 {
                return Err(format!("font load failed: {}", path.to_string_lossy()));
            }
            if let Some(bold_path) = bold_path.filter(|b| found_bold == family.as_bytes() && *b != path) {
                if FT_New_Face(font.library, bold_path.as_ptr(), 0, &mut font.bold) != 0 {
                    font.bold = ptr::null_mut();
                }
            }
            font.description = format!("{family}{}", if font.bold.is_null() { " (regular)" } else { " (regular + bold)" });
            // FreeType sets up a face's hinting on its first hinted glyph load, about a millisecond per face. Do that
            // here instead of inside the frame that renders the first card. Every later load sets its own size first.
            for f in [font.regular, font.bold] {
                if !f.is_null() && FT_Set_Pixel_Sizes(f, 0, 16) == 0 {
                    FT_Load_Char(f, c_ulong::from(b'o'), FT_LOAD_DEFAULT | FT_LOAD_TARGET_LIGHT);
                }
            }
            Ok(font)
        }
    }

    pub fn description(&self) -> &str {
        &self.description
    }

    fn face(&self, bold: bool) -> FtFace {
        if bold && !self.bold.is_null() { self.bold } else { self.regular }
    }

    /// Ascent and descent at `px` (whole pixels, floored, never negative).
    pub fn metrics(&self, px: u32, bold: bool) -> FontMetrics {
        let mut cache = self.metrics.borrow_mut();
        if cache.len() > MAX_CACHED {
            cache.clear();
        }
        *cache.entry(glyph_key(0, px, bold)).or_insert_with(|| {
            let f = self.face(bold);
            // SAFETY: `f` is a live face; its size record is FreeType's.
            unsafe {
                if FT_Set_Pixel_Sizes(f, 0, px) != 0 {
                    return FontMetrics::default();
                }
                let m = &(*(*f).size).metrics;
                FontMetrics { ascent: (m.ascender.max(0) >> 6) as u32, descent: ((-m.descender).max(0) >> 6) as u32 }
            }
        })
    }

    /// Advance width of `text` in pixels (rounded up).
    pub fn measure(&self, text: &str, px: u32, bold: bool) -> u32 {
        self.measure_bytes(text.as_bytes(), px, bold)
    }

    /// [`HudFont::measure`] of any bytes: invalid UTF-8 measures as U+FFFD, one byte at a time.
    pub fn measure_bytes(&self, text: &[u8], px: u32, bold: bool) -> u32 {
        let f = self.face(bold);
        let mut cache = self.advances.borrow_mut();
        if cache.len() > MAX_CACHED {
            cache.clear();
        }
        let mut sized = false;
        let mut x: FtPos = 0;
        let mut i = 0;
        while i < text.len() {
            let cp = next_code_point(text, &mut i);
            let key = glyph_key(cp, px, bold);
            let advance = match cache.get(&key) {
                Some(&a) => a,
                None => {
                    // SAFETY: `f` is a live face; its glyph slot is FreeType's.
                    let a = unsafe {
                        if !sized {
                            if FT_Set_Pixel_Sizes(f, 0, px) != 0 {
                                return 0;
                            }
                            sized = true;
                        }
                        let ok = FT_Load_Char(f, c_ulong::from(cp), FT_LOAD_DEFAULT | FT_LOAD_TARGET_LIGHT) == 0;
                        (ok, (*(*f).glyph).advance.x)
                    };
                    cache.insert(key, a);
                    a
                }
            };
            if advance.0 {
                x += advance.1;
            }
        }
        ((x + 63) >> 6) as u32
    }

    /// Rasterises anti-aliased text with its baseline at `baseline`, starting at `left`: `plot(x, y, coverage)` once
    /// per covered pixel, coverage in (0, 1].
    pub fn draw(&self, text: &str, px: u32, bold: bool, left: i32, baseline: i32, plot: &mut dyn FnMut(i32, i32, f32)) {
        self.draw_bytes(text.as_bytes(), px, bold, left, baseline, plot);
    }

    /// [`HudFont::draw`] of any bytes: invalid UTF-8 draws as U+FFFD, one byte at a time. `plot` must not draw with
    /// this font.
    pub fn draw_bytes(&self, text: &[u8], px: u32, bold: bool, left: i32, baseline: i32, plot: &mut dyn FnMut(i32, i32, f32)) {
        let f = self.face(bold);
        let mut cache = self.glyphs.borrow_mut();
        if cache.len() > MAX_CACHED {
            cache.clear();
        }
        let mut sized = false;
        let mut pen = FtPos::from(left) << 6;
        let mut i = 0;
        while i < text.len() {
            let cp = next_code_point(text, &mut i);
            let key = glyph_key(cp, px, bold);
            if !cache.contains_key(&key) {
                let mut g = Glyph::default();
                // SAFETY: `f` is a live face; the glyph slot and its bitmap (rows x pitch bytes) are FreeType's and
                // are copied before the next load.
                unsafe {
                    if !sized {
                        if FT_Set_Pixel_Sizes(f, 0, px) != 0 {
                            return;
                        }
                        sized = true;
                    }
                    if FT_Load_Char(f, c_ulong::from(cp), FT_LOAD_RENDER | FT_LOAD_TARGET_LIGHT) == 0 {
                        let slot = &*(*f).glyph;
                        let bm = &slot.bitmap;
                        g.ok = true;
                        g.advance = slot.advance.x;
                        g.left = slot.bitmap_left;
                        g.top = slot.bitmap_top;
                        if bm.pixel_mode == FT_PIXEL_MODE_GRAY {
                            for row in 0..bm.rows {
                                for col in 0..bm.width {
                                    let at = row as isize * bm.pitch as isize + col as isize;
                                    let cov = *bm.buffer.offset(at);
                                    if cov != 0 {
                                        g.pixels.push((col as i32, row as i32, f32::from(cov) / 255.0));
                                    }
                                }
                            }
                        }
                    }
                }
                cache.insert(key, g);
            }
            let g = &cache[&key];
            if !g.ok {
                continue;
            }
            let x0 = ((pen >> 6) as i32).wrapping_add(g.left);
            let y0 = baseline.wrapping_sub(g.top);
            for &(x, y, c) in &g.pixels {
                plot(x0.wrapping_add(x), y0.wrapping_add(y), c);
            }
            pen += g.advance;
        }
    }
}

impl HudTextMetrics for HudFont {
    fn metrics(&self, px: u32, bold: bool) -> FontMetrics {
        HudFont::metrics(self, px, bold)
    }
    fn measure(&self, utf8: &str, px: u32, bold: bool) -> u32 {
        self.measure_bytes(utf8.as_bytes(), px, bold)
    }
    fn draw(&self, utf8: &str, px: u32, bold: bool, left: i32, baseline: i32, plot: &mut dyn FnMut(i32, i32, f32)) {
        self.draw_bytes(utf8.as_bytes(), px, bold, left, baseline, plot);
    }
}
