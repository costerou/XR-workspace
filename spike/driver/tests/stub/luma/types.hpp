#pragma once
// Local stand-in for spike/driver/include/luma/types.hpp, word for word from the design contract, used
// only while that header does not exist (driver/predict.cmake checks). The real one wins once merged.
#include <array>
#include <cstdint>
#include <optional>

namespace luma {
struct ImuSample { std::uint64_t t_ns; std::array<float, 3> gyro_rad_s; std::array<float, 3> accel_m_s2; std::optional<std::array<float, 3>> mag; std::uint32_t seq; };
struct Quatf { float w = 1, x = 0, y = 0, z = 0; };
struct PoseOut { Quatf q; std::uint64_t t_ns; std::uint32_t seq; bool calibrated; };
}
