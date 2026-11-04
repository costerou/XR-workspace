// The one file that knows the real Transport, parse_imu, Fusion and Publisher. None of them is on master yet, so these are
// stand-ins, enough to run the whole binary against a capture; a replay through them shows the daemon works, not that a
// pose is right. As each piece lands, replace its function here and take its name out of kStandIn:
//   live_link         a Link over luma::Transport
//   parser            return &luma::parse_imu (the signatures already match)
//   start/stop_command   luma::Command's IMU stream start and stop
//   estimator         an Estimator over luma::Fusion (update already matches)
//   feed_sink         a Sink over luma::Publisher (publish, and set_stale onto its `disabled` flag)
#include "luma/wiring.hpp"
#include <cmath>
#include <cstring>

namespace luma::wiring {
const char* const kStandIn = "transport parser commands estimator feed";

std::unique_ptr<Link> live_link(std::string& why) {
    why = "transport not built in yet";
    return nullptr;
}

namespace {
class NullSink final : public Sink {
public:
    void publish(const PoseOut&) override {}
    void set_stale(bool) override {}
};
// Integrates the gyro, nothing more. Calls itself calibrated after 100 samples so the states can be exercised.
class GyroOnly final : public Estimator {
public:
    PoseOut update(const ImuSample& s) override {
        const float dt = have_t_ ? float(s.t_ns - t_) * 1e-9f : 0.f;
        have_t_ = true; t_ = s.t_ns;
        const Quatf d{1, 0.5f * s.gyro_rad_s[0] * dt, 0.5f * s.gyro_rad_s[1] * dt, 0.5f * s.gyro_rad_s[2] * dt};
        const Quatf a = q_;
        q_ = {a.w * d.w - a.x * d.x - a.y * d.y - a.z * d.z, a.w * d.x + a.x * d.w + a.y * d.z - a.z * d.y,
              a.w * d.y - a.x * d.z + a.y * d.w + a.z * d.x, a.w * d.z + a.x * d.y - a.y * d.x + a.z * d.w};
        const float n = std::sqrt(q_.w * q_.w + q_.x * q_.x + q_.y * q_.y + q_.z * q_.z);
        q_ = {q_.w / n, q_.x / n, q_.y / n, q_.z / n};
        return {q_, s.t_ns, s.seq, ++count_ >= 100};
    }
private:
    Quatf q_{};
    std::uint64_t t_ = 0, count_ = 0;
    bool have_t_ = false;
};
struct Packed { std::uint64_t t_ns; std::uint32_t seq; float gyro[3], accel[3]; };
std::optional<ImuSample> stand_in_parse(std::span<const std::uint8_t> bytes) {
    Packed p;
    if (bytes.size() != sizeof p) return std::nullopt;
    std::memcpy(&p, bytes.data(), sizeof p);
    return ImuSample{p.t_ns, {p.gyro[0], p.gyro[1], p.gyro[2]}, {p.accel[0], p.accel[1], p.accel[2]}, std::nullopt, p.seq};
}
}

std::unique_ptr<Sink> feed_sink(const std::string&, std::string&) { return std::make_unique<NullSink>(); }
std::unique_ptr<Estimator> estimator() { return std::make_unique<GyroOnly>(); }
Parser parser() { return &stand_in_parse; }
std::vector<std::uint8_t> start_command() { return {0x01}; }
std::vector<std::uint8_t> stop_command() { return {0x00}; }
std::vector<std::uint8_t> stand_in_packet(const ImuSample& s) {
    const Packed p{s.t_ns, s.seq, {s.gyro_rad_s[0], s.gyro_rad_s[1], s.gyro_rad_s[2]}, {s.accel_m_s2[0], s.accel_m_s2[1], s.accel_m_s2[2]}};
    std::vector<std::uint8_t> bytes(sizeof p);
    std::memcpy(bytes.data(), &p, sizeof p);
    return bytes;
}
}
