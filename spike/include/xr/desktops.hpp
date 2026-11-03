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
// A desktop's pixel size bounds, both even (geometry.py CUSTOM_BOUNDS; the broker's validDesktop).
constexpr std::uint16_t kDesktopMinW = 640, kDesktopMaxW = 7680, kDesktopMinH = 480, kDesktopMaxH = 4320;
// The sizes of a glass pill's Resolution dropdown, by shape: kResolutionGroups names each group (one column of the
// dropdown's list) and how many of kResolutions, in order, are in it. Mirrors geometry.py RESOLUTION_GROUPS /
// RESOLUTIONS (the glass_contract ctest compares them); every one is a desktop the broker accepts at 100%.
struct Resolution { std::uint16_t width = 0, height = 0; bool operator==(const Resolution&) const = default; };
struct ResolutionGroup { const char* name; std::size_t count; };
constexpr std::size_t kResolutionCount = 12, kResolutionGroupCount = 3, kResolutionRows = 5;  // rows: the largest group
constexpr std::array<Resolution, kResolutionCount> kResolutions{{{1280, 720}, {1600, 900}, {1920, 1080}, {2560, 1440}, {3840, 2160},
                                                                 {1680, 1050}, {1920, 1200}, {2560, 1600},
                                                                 {2560, 1080}, {3440, 1440}, {3840, 1080}, {5120, 1440}}};
constexpr std::array<ResolutionGroup, kResolutionGroupCount> kResolutionGroups{{{"16:9", 5}, {"16:10", 3}, {"Ultrawide", 4}}};
static_assert(kResolutionGroups[0].count + kResolutionGroups[1].count + kResolutionGroups[2].count == kResolutionCount);
static_assert(kResolutionGroups[0].count == kResolutionRows && kResolutionGroups[1].count <= kResolutionRows &&
              kResolutionGroups[2].count <= kResolutionRows);
}
