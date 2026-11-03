#pragma once
#include <array>
#include <cstddef>
#include <cstdint>

namespace xr {
// Most managed desktops a run can have (XR-1..XR-4): a glass pill's Add button stops there. Mirrors
// tools/native_spike/geometry.py MAX_DESKTOPS (the glass_contract ctest compares them).
constexpr std::size_t kMaxDesktops = 4;
// A desktop's Hyprland output scales in percent, the steps of a glass pill's Scale dropdown: quarter steps 100..200%,
// then 250% and 300%. Mirrors geometry.py SCALES (view.py SCALE_PERCENTS) and the broker's
// (spike/ownership/registry.cpp validDesktop); the glass_contract ctest compares them.
constexpr std::size_t kScaleSteps = 7;
constexpr std::array<std::uint16_t, kScaleSteps> kScalePercents{100, 125, 150, 175, 200, 250, 300};
}
