#pragma once
// The glass pills' theme colour (glass.hpp): the same colour as the highlight border and glow, Hyprland
// general:col.active_border (the Omarchy theme's accent, or the border the theme sets), which the supervisor reads at the
// start of a run and again after every config reload (an Omarchy theme switch reloads it) and sends as the `style` op
// (ipc.hpp StylePayload). Presentation::set_accent hands it to the compositor, which tints each pill's glass every frame
// (glass_look), and to GlassPills, which re-renders an open panel's highlights. So a theme switch recolours the pills,
// the open panel and the glow together.
//
// Every theme gets the same glass in its own hue. The accent keeps its hue and saturation but is scaled to one fixed
// luma (and no channel past a peak), so a pale theme and a deep one give equally bright glass: the gloss, the hairline,
// the text contrast and the 191/255 OLED cap (kCardMaxLevel) hold for any theme, even while a pill is dragged. A little
// neutral slate stays in the mix, so it still reads as glass. A grey theme gives grey glass; black gives grey too.
#include <array>

namespace xr {
// GlCompositor's accent before the supervisor's first `style` op (#8d8d8d), so the pills and the panel start in step.
inline constexpr std::array<float, 3> kGlassThemeDefault{0x8d / 255.f, 0x8d / 255.f, 0x8d / 255.f};
inline constexpr std::array<float, 3> kGlassSlate{0.19f, 0.20f, 0.23f};  // the neutral glass the theme hue is mixed into
// The theme hue under the glass, before glass_look's brightness ramp: its luma, and its brightest channel at most.
constexpr float kGlassTintLuma = 0.30f, kGlassTintPeak = 0.42f;
// How much of the glass is the theme hue (the rest slate): the pill, and the open panel (calmer behind its text).
constexpr float kGlassTintPill = 0.85f, kGlassTintPanel = 0.55f;
// The open panel's highlights: the accent with its brightest channel here, the level of hud.cpp's dimmed kCardAccent.
constexpr float kGlassHighlightPeak = 0.97f * 0.75f;

// Rec. 709 luma of an RGB colour (0..1 each).
float glass_luma(std::array<float, 3> c);
// The hue and saturation of `accent` (each channel clamped to 0..1) at luma `luma`, its brightest channel at most `peak`
// (whichever is reached first). Greys stay grey; black, or anything darker than 1/255, counts as grey.
std::array<float, 3> glass_theme_hue(std::array<float, 3> accent, float luma, float peak);
// The glass's base tint `t` (eased 0..1) of the way from pill to panel; glass_look multiplies it by its brightness ramp.
std::array<float, 3> glass_theme_tint(std::array<float, 3> accent, float t);
// The open panel's highlights (the selected row's band and each slider's fill) for `accent`.
std::array<float, 3> glass_theme_highlight(std::array<float, 3> accent);
}
