//! The HUD's design tokens (docs/design-system/tokens.json): the one definition of each UI number. Lengths are canvas
//! px, opacities 0..1, levels 0..255 channel values, durations ms unless named otherwise.

// Spacing.
pub const SPACE_1: f32 = 4.0;
pub const SPACE_2: f32 = 8.0;
pub const SPACE_3: f32 = 12.0;
pub const SPACE_4: f32 = 16.0;
pub const SPACE_5: f32 = 24.0;
pub const SPACE_6: f32 = 32.0;

// Corner radii.
pub const RADIUS_XS: f32 = 4.0;
pub const RADIUS_SM: f32 = 8.0;
pub const RADIUS_MD: f32 = 12.0;
pub const RADIUS_LG: f32 = 16.0;
pub const RADIUS_XL: f32 = 28.0;

// Sizes.
pub const DOCK_REST_H: f32 = 14.0;
pub const DOCK_H: f32 = 32.0;
pub const CONTROL_H: f32 = 28.0;
pub const ROW_H: f32 = 40.0;
pub const SHEET_W: f32 = 640.0;
pub const SHEET_H: f32 = 448.0;
pub const SHEET_HEADER_H: f32 = 48.0;
pub const SHEET_RAIL_W: f32 = 152.0;
pub const SHEET_FOOTER_H: f32 = 40.0;
pub const TOAST_H: f32 = 56.0;
pub const ICON: f32 = 20.0;
pub const HIT_PAD: f32 = 4.0;
pub const FOCUS_FRAME: f32 = 6.0;
pub const TRACK_H: f32 = 4.0;
pub const KNOB: f32 = 14.0;

// Opacities.
pub const OPACITY_STATE_HOVER: f32 = 0.08;
pub const OPACITY_STATE_FOCUS: f32 = 0.10;
pub const OPACITY_STATE_DRAG: f32 = 0.16;
pub const OPACITY_RIM_HOVER: f32 = 0.55;
pub const OPACITY_RIM_DRAG: f32 = 0.70;
pub const OPACITY_SHEEN: f32 = 0.34;
pub const OPACITY_TRACK_REST: f32 = 0.24;
pub const OPACITY_DISABLED_CONTENT: f32 = 0.38;
pub const OPACITY_DISABLED_CONTAINER: f32 = 0.12;
pub const OPACITY_SHEET_FILL: f32 = 0.94;
pub const OPACITY_CARD_FILL: f32 = 0.90;

// Levels (0..255).
/// Brightest any channel may be in the glasses (micro-OLED image retention). Every other cap derives from this.
pub const LEVEL_CAP: u8 = 191;
/// [`LEVEL_CAP`] as a 0..1 channel value.
pub const LEVEL_CAP_UNIT: f32 = LEVEL_CAP as f32 / 255.0;
pub const LEVEL_FLOOR: u8 = 72;
pub const LEVEL_POINTER: u8 = 170;
pub const LEVEL_POINTER_HELD: u8 = 184;
pub const LEVEL_GHOST: u8 = 150;

// Motion.
pub const MOTION_HOVER_MS: f64 = 140.0;
pub const MOTION_ENTER_MS: f64 = 180.0;
pub const MOTION_EXIT_MS: f64 = 120.0;
pub const MOTION_TOAST_IN_MS: f64 = 120.0;
pub const MOTION_TOAST_HOLD_MS: f64 = 1500.0;
pub const MOTION_TOAST_OUT_MS: f64 = 380.0;
pub const MOTION_RESTORE_STEP_MS: f64 = 220.0;
pub const MOTION_IDLE_CLOSE_S: u32 = 30;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn caps_derive_from_level_cap() {
        assert_eq!(crate::hud::CARD_MAX_LEVEL, LEVEL_CAP);
        for cap in [crate::material::MD_OLED_LEVEL, crate::glass::GLASS_CAP, crate::environment::ENVIRONMENT_CAP] {
            assert_eq!(cap, LEVEL_CAP_UNIT);
        }
    }

    #[test]
    fn glass_shader_cap_is_level_cap() {
        let src = crate::glass::glass_fragment_shader();
        let lit = src.split("const float kCap = ").nth(1).and_then(|s| s.split(';').next()).expect("kCap in shader");
        assert_eq!(lit, format!("{:.8}", f64::from(LEVEL_CAP) / 255.0));
    }
}
