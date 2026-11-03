#pragma once
// The TRON grid: an optional 360° environment drawn behind the XR desktops (Ctrl+Alt+E on / off, Ctrl+Alt+Shift+E its
// colour; tools/native_spike/environment.py owns the setting and saves it with the view settings). A near-black sky, an
// endless neon grid floor fading into the distance, a glowing horizon, a ring of distant mountain outlines, and light
// pulses running along the grid lines. One procedural fullscreen pass (spike/src/environment_gl.hpp), no textures.
//
// Always world-locked: it turns with the head pose in every viewing mode (anchor, follow, locked) and while maximized, so
// you can look round it whatever the desktops do. Its frame is the recentre reference turned to level: the reference's
// heading about gravity (the pose feed's z axis), so the floor stays flat even if you recentred with your head tipped.
//
// The glasses. Black is see-through on their micro-OLED panels: the sky and the floor between the lines stay black, and
// nothing passes the 191/255 cap (hud.hpp kCardMaxLevel, material.hpp kMdOledLevel). The desktops, pills, cursor dot and
// cards are drawn over it unchanged. Off (and once its fade-out has finished) the pass is skipped and every frame is exactly
// what it was without it.
//
// Frames. It asks for frames only while it fades: a head-tracked run draws every frame anyway, so its pulses run smoothly.
// Without head tracking a settled grid is redrawn only when something else is (a pulse then moves in steps), because a renderer
// that draws on every callback shows a new desktop frame a refresh later (capture_e2e_bench: 5 -> 13 ms capture to present).
//
// Pure and offline-testable; the GL pass and the renderer's frame loop use these.
#include "xr/material.hpp"
#include "xr/pose.hpp"
#include <array>
#include <cstdint>
#include <optional>

namespace xr {
enum class EnvironmentColor : std::uint8_t { cyan = 0, theme = 1 };  // wire "cyan" | "theme" (ipc.hpp EnvironmentPayload)

inline constexpr float kEnvironmentCap = 191.f / 255.f;       // the OLED cap: no channel of the pass is brighter
inline constexpr double kEnvironmentFadeMs = 600;            // on / off fade
inline constexpr double kEnvironmentColorMs = 400;           // cyan <-> theme crossfade
// The animation clock wraps every kEnvironmentLoopS seconds: every pulse's period divides it exactly (environment_gl.cpp
// pulse speeds 8 / 12 / 16 m/s along a 128 m period), so the wrap is seamless.
inline constexpr double kEnvironmentLoopS = 32;
// Classic TRON cyan, scaled to the cap (its brightest channel sits exactly on it).
inline constexpr MdRgb kEnvironmentCyan{0.f, 0.88f * kEnvironmentCap, kEnvironmentCap};

// The grid's line colour for `color` (already under the cap, brightest channel on it unless the colour is darker):
// cyan, or the Omarchy theme accent (the `style` op's border colour, GlCompositor::set_accent) at its own hue and a vivid
// chroma, lightened to a neon tone. A grey theme gives a grey grid.
MdRgb environment_line_color(EnvironmentColor color, MdRgb accent);

// The environment's view rotation (world -> eye, row-major, like pose.hpp view_from) for the head's orientation
// `head_relative` (driver frame, relative to the recentre reference, prediction included) and the recentre `reference`
// itself (PoseState::reference; nullopt before the first sample: the reference is taken as level).
Mat3 environment_view(Quat head_relative, const std::optional<Quat>& reference);
// The reference's heading only: its turn about the driver's z (gravity) axis.
Quat heading(Quat q);

// The on / off fade and the colour crossfade, on the renderer's monotonic clock (ms).
class EnvironmentFade {
public:
    // `on`: fade in (or out) from wherever the fade is now; `color`: the line colour to crossfade to. Returns true if
    // anything changed (the renderer then keeps drawing until the fade settles).
    bool set(bool on, MdRgb color, double now_ms);
    float level(double now_ms) const;    // 0 (off) .. 1 (on), smoothstep-eased
    MdRgb color(double now_ms) const;    // the line colour now
    bool on() const { return on_; }
    bool animating(double now_ms) const;  // a fade or crossfade is running
private:
    bool on_ = false;
    float from_level_ = 0;               // the level when the last on / off change started
    double level_ms_ = -1e9;             // when it started
    MdRgb from_color_ = kEnvironmentCyan, to_color_ = kEnvironmentCyan;
    double color_ms_ = -1e9;
    bool seen_color_ = false;            // the first colour is taken as is (no crossfade from cyan)
};
}
