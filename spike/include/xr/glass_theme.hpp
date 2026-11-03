#pragma once
// The glass pills' theme colour (glass.hpp): the same colour as the highlight border and glow, Hyprland
// general:col.active_border (the Omarchy theme's accent, or the border the theme sets), which the supervisor reads at the
// start of a run and again after every config reload (an Omarchy theme switch reloads it) and sends as the `style` op
// (ipc.hpp StylePayload). Presentation::set_accent hands it to the compositor, which styles each pill every frame
// (glass_look), to GlassPills, which re-renders an open panel, and to the pop-ups and the help and video cards. So a theme
// switch recolours the pills, the open panel, the cards and the glow together.
//
// The colours are a Material Design 3 dark scheme with that accent as its seed (material.hpp material_scheme: an M3
// fidelity scheme, every role under the 191/255 OLED cap). The collapsed pill is the scheme's primary container: the
// accent's own hue and chroma at its own tone, pressed into a range the see-through OLED shows (a pale theme gives a pale
// pill, a dark one a darker pill, and the grey themes each their own grey). The open panel is an M3 surface container
// (high) with an outline-variant edge, calm behind its text; its selected row is the secondary container, its sliders,
// handles and "on" button the primary. A grey theme gives grey glass; black gives grey too.
#include "xr/material.hpp"
#include <array>
#include <cstddef>

namespace xr {
// GlCompositor's accent before the supervisor's first `style` op (#8d8d8d), so the pills and the panel start in step.
inline constexpr std::array<float, 3> kGlassThemeDefault = kMdDefaultSeed;

// Rec. 709 luma of an RGB colour (0..1 each).
float glass_luma(std::array<float, 3> c);
// The M3 scheme for theme colour `accent` (material_scheme), remembered for the last few accents asked for: the compositor
// asks for it every frame, and it changes only with the theme. Thread-local; the reference stays valid until that thread
// asks for kGlassSchemeCache further accents.
inline constexpr std::size_t kGlassSchemeCache = 4;
const MaterialScheme& glass_theme_scheme(std::array<float, 3> accent);
}
