//! The glass pills' theme colour (`spike/include/xr/glass_theme.hpp`).
//!
//! The colours are a Material Design 3 dark scheme seeded with Hyprland's active border colour (the Omarchy theme's
//! accent), so a theme switch recolours the pills, the open panel, the cards and the glow together.

use std::cell::RefCell;

use crate::material::{MD_DEFAULT_SEED, MaterialScheme, MdRgb, material_scheme};

/// The compositor's accent before the supervisor's first `style` op (#8d8d8d), so the pills and the panel start in step.
pub const GLASS_THEME_DEFAULT: MdRgb = MD_DEFAULT_SEED;

/// How many accents [`glass_theme_scheme`] remembers per thread: the compositor asks every frame, and it changes only
/// with the theme.
pub const GLASS_SCHEME_CACHE: usize = 4;

/// Rec. 709 luma of an RGB colour (0..1 each).
pub fn glass_luma(c: [f32; 3]) -> f32 {
    0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
}

struct Cache {
    entries: [Option<(MdRgb, MaterialScheme)>; GLASS_SCHEME_CACHE],
    next: usize,
}

thread_local! {
    static CACHE: RefCell<Cache> = const { RefCell::new(Cache { entries: [None, None, None, None], next: 0 }) };
}

/// The M3 scheme for theme colour `accent` ([`material_scheme`]), remembered for the last few accents asked for.
pub fn glass_theme_scheme(accent: MdRgb) -> MaterialScheme {
    CACHE.with_borrow_mut(|cache| {
        if let Some((_, scheme)) = cache.entries.iter().flatten().find(|(a, _)| *a == accent) {
            return *scheme;
        }
        let scheme = material_scheme(accent);
        let slot = cache.next;
        cache.entries[slot] = Some((accent, scheme));
        cache.next = (slot + 1) % GLASS_SCHEME_CACHE;
        scheme
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cached_scheme_equals_a_fresh_one() {
        let accent = [0.2, 0.5, 0.8];
        assert_eq!(glass_theme_scheme(accent), material_scheme(accent));
        for i in 0..6 {
            let a = [i as f32 / 10.0, 0.3, 0.3];
            assert_eq!(glass_theme_scheme(a), material_scheme(a));
        }
        assert_eq!(glass_theme_scheme(accent), material_scheme(accent));
        assert!((glass_luma([1.0, 1.0, 1.0]) - 1.0).abs() < 1e-6);
    }
}
