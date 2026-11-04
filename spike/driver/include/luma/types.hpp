// Shared value types of the Luma driver (design contract, identical in every piece).
#pragma once
#include <array>
#include <cstdint>
#include <optional>

namespace luma {

// One inertial sample, in SI units and in the sensor's own axes. Axes are not
// remapped here: that belongs to whoever consumes the sample.
struct ImuSample {
    std::uint64_t t_ns;
    std::array<float, 3> gyro_rad_s;
    std::array<float, 3> accel_m_s2;
    std::optional<std::array<float, 3>> mag;
    std::uint32_t seq;
};

struct Quatf {
    float w = 1, x = 0, y = 0, z = 0;
};

struct PoseOut {
    Quatf q;
    std::uint64_t t_ns;
    std::uint32_t seq;
    bool calibrated;
};

}  // namespace luma
