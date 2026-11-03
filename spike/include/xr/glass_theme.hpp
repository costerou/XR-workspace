#pragma once
// The glass pills' theme colour (glass.hpp): the same colour as the highlight border and glow, Hyprland
// general:col.active_border (the Omarchy theme's accent, or the border the theme sets), which the supervisor reads at the
// start of a run and again after every config reload (an Omarchy theme switch reloads it) and sends as the `style` op
// (ipc.hpp StylePayload). Presentation::set_accent hands it to the compositor, which tints each pill's glass every frame
// (glass_look), and to GlassPills, which re-renders an open panel's highlights. So a theme switch recolours the pills,
// the open panel and the glow together.
//
// The collapsed pill is the theme colour itself, as the border and glow are: the accent's own hue, saturation and
// lightness, scaled by one factor so the brightest possible accent (white) still keeps the body under the 191/255 OLED
// cap (kCardMaxLevel) while dragged. So a pale theme gives a pale pill and a dark one a dark pill, greys of different
// lightness give different greys (vantablack, white and lumon no longer all give the same slate), and only an accent
// too dark to see on the see-through OLED is lifted (its hue kept) to a visible floor. Its hairline is the theme colour
// too, at the hairline's brightness. The open panel stays calm behind its text: the accent's hue at one luma, mostly
// mixed with neutral slate, as before. A grey theme gives grey glass; black gives grey too.
#include <array>

namespace xr {
// GlCompositor's accent before the supervisor's first `style` op (#8d8d8d), so the pills and the panel start in step.
inline constexpr std::array<float, 3> kGlassThemeDefault{0x8d / 255.f, 0x8d / 255.f, 0x8d / 255.f};
inline constexpr std::array<float, 3> kGlassSlate{0.19f, 0.20f, 0.23f};  // the neutral glass the theme hue is mixed into
// The open panel's theme hue under its glass, before glass_look's brightness ramp: its luma, its brightest channel at
// most, and how much of the panel is that hue (the rest slate; calm behind the text).
constexpr float kGlassTintLuma = 0.30f, kGlassTintPeak = 0.42f, kGlassTintPanel = 0.55f;
// The pill: the accent times kGlassPillScale (a white accent's channels land on kGlassTintPeak), lifted to a luma of
// kGlassPillMinLuma (or until a channel reaches kGlassPillScale) so a near-black accent still shows.
constexpr float kGlassPillScale = kGlassTintPeak, kGlassPillMinLuma = 0.10f;
// The open panel's highlights: the accent with its brightest channel here, the level of hud.cpp's dimmed kCardAccent.
constexpr float kGlassHighlightPeak = 0.97f * 0.75f;

// Rec. 709 luma of an RGB colour (0..1 each).
float glass_luma(std::array<float, 3> c);
// The hue and saturation of `accent` (each channel clamped to 0..1) at luma `luma`, its brightest channel at most `peak`
// (whichever is reached first). Greys stay grey; black, or anything darker than 1/255, counts as grey.
std::array<float, 3> glass_theme_hue(std::array<float, 3> accent, float luma, float peak);
// The collapsed pill's base tint: the accent itself (clamped to 0..1), scaled by kGlassPillScale, at least
// kGlassPillMinLuma of luma.
std::array<float, 3> glass_theme_pill(std::array<float, 3> accent);
// The glass's base tint `t` (eased 0..1) of the way from pill (glass_theme_pill) to the open panel (the hue at
// kGlassTintLuma, kGlassTintPanel of it over slate); glass_look multiplies it by its brightness ramp.
std::array<float, 3> glass_theme_tint(std::array<float, 3> accent, float t);
// The hairline round a pill or panel: the accent's hue with its brightest channel at `level` (glass.hpp kHairLevel),
// half of it white-grey so the edge stays brighter than the body for deep themes too. Greys give kHairLevel grey.
std::array<float, 3> glass_theme_hair(std::array<float, 3> accent, float level);
// The open panel's highlights (the selected row's band and each slider's fill) for `accent`.
std::array<float, 3> glass_theme_highlight(std::array<float, 3> accent);
}
