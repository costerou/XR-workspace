#pragma once
// The background behind the desktops and behind a VR video: an optional 360° environment, chosen in the placement panel's
// Background dropdown or the VR video's quality menu, Ctrl+Alt+E turning it off and back to the last one used
// (tools/native_spike/environment.py owns the setting and saves it with the view settings). One procedural fullscreen pass
// (spike/src/environment_gl.hpp), no textures.
//
// Backgrounds. kBackgrounds lists them; None draws nothing. Adding one: a row here (name on the wire, label in the menus),
// the same row in environment.py BACKGROUNDS (a test compares them) and a `scene()` in environment_gl.cpp.
//   Grid: the TRON grid. A near-black sky, an endless neon grid floor fading into the distance, a glowing horizon, a ring of
//   distant mountain outlines, and light trails running along the grid lines.
//   Smoke: slow wisps of fog drifting round you, thickest in a band about the horizon. Its density ends at exactly zero, so
//   the clear air between the wisps is black.
//
// Colour: the Omarchy theme's, the very accent the highlight border and glow use (GlCompositor::set_accent, Hyprland
// general:col.active_border), so a theme switch recolours it with them, in the same frame. environment_line_color only keeps
// that colour under the glasses' cap.
//
// Always world-locked: it turns with the head pose in every viewing mode (anchor, follow, locked) and while maximized, so
// you can look round it whatever the desktops do. Its frame is the recentre reference turned to level: the reference's
// heading about gravity (the pose feed's z axis), so the floor stays flat even if you recentred with your head tipped.
//
// The glasses. Black is see-through on their micro-OLED panels: the sky and the floor between the lines stay black at any
// brightness (it multiplies), and nothing passes the 191/255 cap (hud.hpp kCardMaxLevel, material.hpp kMdOledLevel). The
// desktops, pills, cursor dot and cards are drawn over it unchanged, and a VR video over it (a 360° video covers all of it,
// so the pass is skipped; a 180° or fisheye one leaves the rest of the sphere to it). With None (once its fade-out has
// finished) the pass is skipped and every frame is exactly what it was without it.
//
// Frames. It asks for frames only while it fades: a head-tracked run draws every frame anyway, so it animates smoothly.
// Without head tracking a settled background is redrawn only when something else is (it then moves in steps), because a renderer
// that draws on every callback shows a new desktop frame a refresh later (capture_e2e_bench: 5 -> 13 ms capture to present).
//
// Pure and offline-testable; the GL pass and the renderer's frame loop use these.
#include "xr/material.hpp"
#include "xr/pose.hpp"
#include <array>
#include <cstdint>
#include <optional>
#include <string_view>

namespace xr {
enum class Background : std::uint8_t { none = 0, grid = 1, smoke = 2 };
struct BackgroundInfo { std::string_view name, label; };  // wire name (ipc.hpp EnvironmentPayload), menu label
inline constexpr std::array<BackgroundInfo, 3> kBackgrounds{BackgroundInfo{"none", "None"}, BackgroundInfo{"grid", "Grid"},
                                                            BackgroundInfo{"smoke", "Smoke"}};
inline constexpr std::size_t kBackgroundCount = kBackgrounds.size();
// Index into kBackgrounds of wire name `name`, kBackgroundCount if there is no such background.
constexpr std::size_t background_index(std::string_view name) {
    std::size_t i = 0;
    while (i < kBackgroundCount && kBackgrounds[i].name != name) ++i;
    return i;
}

// Brightness in percent: it scales the background before the cap, so 100 is the scene as designed, below it dims evenly,
// and above it lifts the dim parts (the far lines, the haze) while the brightest stay on the cap. Black stays black.
// Mirrors environment.py BRIGHTNESS_BOUNDS and DEFAULT_BRIGHTNESS.
inline constexpr std::uint16_t kBrightnessMin = 10, kBrightnessMax = 200, kBrightnessDefault = 100;

inline constexpr float kEnvironmentCap = 191.f / 255.f;       // the OLED cap: no channel of the pass is brighter
inline constexpr float kEnvironmentFloor = 72.f / 255.f;      // the brightest channel of a near-black theme colour is lifted to this
inline constexpr double kEnvironmentFadeMs = 600;             // on / off fade
// The animation clock wraps every kEnvironmentLoopS seconds: every Grid pulse's period divides it exactly (environment_gl.cpp
// pulse speeds 8 / 12 / 16 m/s along a 128 m period) and every Smoke motion is a whole number of cycles of it, so the wrap
// is seamless. Long, so Smoke's drift never shows a repeat.
inline constexpr double kEnvironmentLoopS = 512;

// The background's line colour for theme colour `accent` (the highlight border's): that colour exactly, unless its brightest
// channel is over the OLED cap, when all three scale down together so its hue and saturation stay; a near-black
// theme colour (which would not show on the see-through panels) is lifted the same way to kEnvironmentFloor.
MdRgb environment_line_color(MdRgb accent);

// The environment's view rotation (world -> eye, row-major, like pose.hpp view_from) for the head's orientation
// `head_relative` (driver frame, relative to the recentre reference, prediction included) and the recentre `reference`
// itself (PoseState::reference; nullopt before the first sample: the reference is taken as level).
Mat3 environment_view(Quat head_relative, const std::optional<Quat>& reference);
// The reference's heading only: its turn about the driver's z (gravity) axis.
Quat heading(Quat q);

// The fade between None and a background, on the renderer's monotonic clock (ms).
class EnvironmentFade {
public:
    // `on`: fade in (or out) from wherever the fade is now. True if it changed.
    bool set(bool on, double now_ms);
    float level(double now_ms) const;    // 0 (off) .. 1 (on), smoothstep-eased
    bool on() const { return on_; }
    bool animating(double now_ms) const;  // the fade is running
private:
    bool on_ = false;
    float from_level_ = 0;               // the level when the last change started
    double level_ms_ = -1e9;             // when it started
};
}
