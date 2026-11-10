#include "xr/motion_feed.hpp"
#include "luma/predict.hpp"
#include <cmath>
#include <sys/ipc.h>
#include <sys/shm.h>

namespace xr {
std::unique_ptr<MotionFeed> MotionFeed::open(const std::string& prefix) {
    const std::string path = prefix + luma::kPredictionIpcName;
    const key_t key = ftok(path.c_str(), 0);  // fails when the driver made no file for it: no wire
    const int id = key == -1 ? -1 : shmget(key, 0, 0);
    shmid_ds info{};
    if (id < 0 || shmctl(id, IPC_STAT, &info) != 0 || info.shm_segsz < sizeof(luma::PredictionWire)) return nullptr;
    void* addr = shmat(id, nullptr, SHM_RDONLY);
    if (addr == reinterpret_cast<void*>(-1)) return nullptr;
    std::unique_ptr<MotionFeed> feed(new MotionFeed());
    feed->wire_ = addr;
    return feed;
}
MotionFeed::~MotionFeed() { if (wire_) shmdt(wire_); }

std::optional<FeedMotion> MotionFeed::read() const {
    const auto m = luma::read_prediction(*static_cast<const luma::PredictionWire*>(wire_));
    if (!m || !m->calibrated) return std::nullopt;
    const auto q = quat_normalized({m->q.x, m->q.y, m->q.z, m->q.w});
    if (!q) return std::nullopt;
    for (std::size_t i = 0; i < 3; ++i)
        if (!std::isfinite(m->omega_rad_s[i]) || !std::isfinite(m->alpha_rad_s2[i])) return std::nullopt;
    return FeedMotion{*q, m->host_ns, m->omega_rad_s, m->alpha_rad_s2};
}

Quat motion_delta(const FeedMotion& m, float horizon_ms, float max_deg) {
    if (!(horizon_ms > 0)) return {};
    luma::MotionState state;
    state.q = {m.q.w, m.q.x, m.q.y, m.q.z};
    state.host_ns = m.host_ns;
    state.omega_rad_s = m.omega_rad_s;
    state.alpha_rad_s2 = m.alpha_rad_s2;
    state.valid = state.calibrated = true;
    luma::PredictConfig cfg;
    cfg.max_deg = max_deg;
    const auto p = luma::extrapolate(state, horizon_ms, cfg).q;
    return quat_normalized(quat_mul(quat_conj(m.q), Quat{p.x, p.y, p.z, p.w})).value_or(Quat{});
}

std::optional<double> motion_age_ms(const FeedMotion& m, std::uint64_t now_ns) {
    if (now_ns < m.host_ns) return std::nullopt;
    const double age = double(now_ns - m.host_ns) / 1e6;
    if (age > kMaxMotionAgeMs) return std::nullopt;
    return age;
}
}
