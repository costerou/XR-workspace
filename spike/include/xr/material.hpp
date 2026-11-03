#pragma once
// Material Design 3 for the renderer's in-XR chrome: the glass pills and the placement panel (glass.hpp), the roll and
// resize affordances, the status pop-ups and the help and video cards (hud.hpp, video.hpp). One tonal palette from the
// Omarchy theme colour, plus M3's state layer, shape and elevation tokens, sized for the glasses. Pure and offline-testable.
//
// The palette. The seed is the theme colour the highlight border and glow use (Hyprland general:col.active_border, sent
// as the `style` op at the start of a run and after every config reload: glass_theme.hpp), so a theme switch recolours
// everything live. From it comes an M3 *fidelity* dark scheme: the primary palette keeps the seed's own hue and chroma
// (a grey theme stays grey, a pale one stays pale), the primary container is the seed itself at its own tone (pressed
// into kMdContainerToneLo..Hi so a near-black accent still shows on the see-through OLED and a white one does not glare),
// and the secondary, neutral and neutral-variant palettes are the seed's hue at less chroma. Roles take M3's dark-scheme
// tones (primary 80, on primary 20, secondary container 30, surface containers 4..22, on surface 90, outline 60, ...).
// Tone is CIE L* (M3's HCT tone); hue and chroma are OKLCH's, so a blue stays blue as it is lightened (CIELAB's hue
// drifts toward purple there), and a role out of the sRGB gamut keeps its hue and tone and gives up chroma.
//
// The glasses. Black is see-through on their micro-OLED panels, and nothing drawn here may pass the cards' 191/255
// image-retention cap (hud.hpp kCardMaxLevel). Every role is scaled by kMdOledLevel = 191/255 in sRGB: the brightest
// possible role (white) lands exactly on the cap, and every role keeps its place against the others.
#include <array>
#include <cstdint>

namespace xr {
using MdRgb = std::array<float, 3>;  // sRGB, 0..1

// Before the supervisor's first `style` op: the compositor's default border colour (#8d8d8d), a neutral grey scheme.
inline constexpr MdRgb kMdDefaultSeed{0x8d / 255.f, 0x8d / 255.f, 0x8d / 255.f};
inline constexpr float kMdOledLevel = 191.f / 255.f;  // = hud.hpp kCardMaxLevel / 255: every role is scaled by this
// The primary container (the collapsed pill) is the seed at its own tone, pressed into this range (linearly, so a
// lighter theme colour still gives a lighter pill).
inline constexpr float kMdContainerToneLo = 28.f, kMdContainerToneHi = 82.f;

// The dark scheme's colour roles, already scaled by kMdOledLevel.
struct MaterialScheme {
    MdRgb primary{}, on_primary{}, primary_container{}, on_primary_container{};
    MdRgb primary_bright{};  // the primary palette at tone 98: a hovered or dragged icon (no container to put a state layer on)
    MdRgb secondary_container{}, on_secondary_container{};
    MdRgb error_container{}, on_error_container{};
    MdRgb surface{}, surface_container_low{}, surface_container{}, surface_container_high{}, surface_container_highest{};
    MdRgb on_surface{}, on_surface_variant{}, outline{}, outline_variant{};
    float seed_tone = 0, container_tone = 0;  // the seed's L* and the primary container's
    bool operator==(const MaterialScheme&) const = default;
};
// The scheme for theme colour `seed` (channels clamped to 0..1; non-finite ones count as 0).
MaterialScheme material_scheme(MdRgb seed);

// --- Colour science (unscaled sRGB) -------------------------------------------------------------------------------
struct MdHct { float hue = 0, chroma = 0, tone = 0; };  // OKLCH hue (degrees) and chroma, CIE L* tone (0..100)
MdHct md_hct(MdRgb srgb);
// The colour of `hue` / `chroma` at `tone`, in sRGB: exactly that tone, and that hue unless the chroma is 0; when the
// chroma does not fit the gamut there, the most that does.
MdRgb md_color(float hue, float chroma, float tone);
float md_tone(MdRgb srgb);  // CIE L*
// WCAG 2 contrast ratio between two sRGB colours (1..21).
float md_contrast(MdRgb a, MdRgb b);

// --- State layers, shape, elevation --------------------------------------------------------------------------------
// A state layer: the content colour over its container at an M3 opacity (hover 8%, focus and pressed 10%, dragged 16%).
inline constexpr float kMdStateHover = 0.08f, kMdStateFocus = 0.10f, kMdStatePressed = 0.10f, kMdStateDragged = 0.16f;
// Disabled: the container is on-surface at 12%, the content on-surface at 38%.
inline constexpr float kMdDisabledContainer = 0.12f, kMdDisabledContent = 0.38f;
MdRgb md_layer(MdRgb container, MdRgb content, float opacity);  // container * (1 - o) + content * o
// Shape scale: corner radii (canvas / output px). Fully rounded shapes use half their height.
inline constexpr float kMdShapeExtraSmall = 4.f, kMdShapeSmall = 8.f, kMdShapeMedium = 12.f, kMdShapeLarge = 16.f,
                       kMdShapeExtraLarge = 28.f;
// Elevation level 0..5 (M3: 0, 1, 3, 6, 8, 12 dp, interpolated in between) as one soft drop shadow: its opacity, blur
// (Gaussian sigma) and downward offset, px. Level 0 casts none.
struct MdShadow { float alpha = 0, sigma = 1, dy = 0; };
MdShadow md_elevation(float level);
inline constexpr float kMdElevationPill = 1.f, kMdElevationHover = 2.f, kMdElevationDialog = 3.f, kMdElevationDragged = 4.f;
}
