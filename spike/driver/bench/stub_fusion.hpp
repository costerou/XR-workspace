#pragma once
// A stand-in for fusion.hpp's luma::Fusion with the same update() shape: a plain Mahony filter, so the benchmark carries about
// the arithmetic the real thing will. CMakeLists.txt switches to the real one as soon as it is merged.
#include "luma/types.hpp"
#include <cmath>

namespace luma::bench {
class StubFusion {
public:
    PoseOut update(const ImuSample& s) noexcept {
        const float dt = last_ns_ ? float(s.t_ns - last_ns_) * 1e-9f : 1e-3f;
        last_ns_ = s.t_ns;
        float gx = s.gyro_rad_s[0], gy = s.gyro_rad_s[1], gz = s.gyro_rad_s[2];
        float ax = s.accel_m_s2[0], ay = s.accel_m_s2[1], az = s.accel_m_s2[2];
        auto& q = pose_;
        const float n = std::sqrt(ax * ax + ay * ay + az * az);
        if (n > 1e-3f) {
            ax /= n; ay /= n; az /= n;
            const float vx = 2 * (q.x * q.z - q.w * q.y), vy = 2 * (q.w * q.x + q.y * q.z), vz = q.w * q.w - q.x * q.x - q.y * q.y + q.z * q.z;
            const float ex = ay * vz - az * vy, ey = az * vx - ax * vz, ez = ax * vy - ay * vx;
            ix_ += kKi * ex * dt; iy_ += kKi * ey * dt; iz_ += kKi * ez * dt;
            gx += kKp * ex + ix_; gy += kKp * ey + iy_; gz += kKp * ez + iz_;
        }
        const float hx = gx * dt / 2, hy = gy * dt / 2, hz = gz * dt / 2;
        const Quatf p = q;
        q.w += -p.x * hx - p.y * hy - p.z * hz;
        q.x += p.w * hx + p.y * hz - p.z * hy;
        q.y += p.w * hy - p.x * hz + p.z * hx;
        q.z += p.w * hz + p.x * hy - p.y * hx;
        const float len = std::sqrt(q.w * q.w + q.x * q.x + q.y * q.y + q.z * q.z);
        q.w /= len; q.x /= len; q.y /= len; q.z /= len;
        return {q, s.t_ns, s.seq, true};
    }
private:
    static constexpr float kKp = 2.0f, kKi = 0.01f;
    Quatf pose_;
    std::uint64_t last_ns_ = 0;
    float ix_ = 0, iy_ = 0, iz_ = 0;
};
}  // namespace luma::bench
