#pragma once
#include <cstddef>

namespace xr {
// Most managed desktops a run can have (XR-1..XR-4): a glass pill's Add button stops there. Mirrors
// tools/native_spike/geometry.py MAX_DESKTOPS (the glass_contract ctest compares them).
constexpr std::size_t kMaxDesktops = 4;
}
