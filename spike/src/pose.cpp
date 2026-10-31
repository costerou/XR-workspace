#include "xr/pose.hpp"
#include <cerrno>
#include <cmath>
#include <ctime>
#include <pthread.h>
#include <sys/ipc.h>
#include <sys/shm.h>
#include <sys/stat.h>

namespace xr {
Quat quat_mul(Quat a, Quat b) {
    return {a.w * b.x + a.x * b.w + a.y * b.z - a.z * b.y, a.w * b.y - a.x * b.z + a.y * b.w + a.z * b.x,
            a.w * b.z + a.x * b.y - a.y * b.x + a.z * b.w, a.w * b.w - a.x * b.x - a.y * b.y - a.z * b.z};
}
Quat quat_conj(Quat q) { return {-q.x, -q.y, -q.z, q.w}; }
std::optional<Quat> quat_normalized(Quat q) {
    const float n = std::sqrt(q.x * q.x + q.y * q.y + q.z * q.z + q.w * q.w);
    if (!std::isfinite(n) || n < 1e-6f) return std::nullopt;
    return Quat{q.x / n, q.y / n, q.z / n, q.w / n};
}
Mat3 quat_to_mat3(Quat q) {
    const float x = q.x, y = q.y, z = q.z, w = q.w;
    return {1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y),
            2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x),
            2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)};
}
Mat3 transpose(const Mat3& m) { return {m[0], m[3], m[6], m[1], m[4], m[7], m[2], m[5], m[8]}; }
namespace {
Mat3 mul(const Mat3& a, const Mat3& b) {
    Mat3 r{};
    for (int i = 0; i < 3; ++i) for (int j = 0; j < 3; ++j)
        for (int k = 0; k < 3; ++k) r[i * 3 + j] += a[i * 3 + k] * b[k * 3 + j];
    return r;
}
// gl = M * driver: right = -left, up = up, back = -forward. A proper rotation (det +1).
const Mat3 kDriverToGl{0, -1, 0, 0, 0, 1, -1, 0, 0};
}
Mat3 driver_to_gl(const Mat3& driver) { return mul(mul(kDriverToGl, driver), transpose(kDriverToGl)); }
std::array<float, 3> apply(const Mat3& m, std::array<float, 3> v) {
    return {m[0] * v[0] + m[1] * v[1] + m[2] * v[2], m[3] * v[0] + m[4] * v[1] + m[5] * v[2], m[6] * v[0] + m[7] * v[1] + m[8] * v[2]};
}
std::optional<std::array<float, 2>> project(const Mat3& view, float ndc_x, float ndc_y, float hw, float hh) {
    const auto v = apply(view, {ndc_x * hw, ndc_y * hh, -1.f});
    if (v[2] > -1e-4f || hw <= 0 || hh <= 0) return std::nullopt;
    return std::array<float, 2>{v[0] / -v[2] / hw, v[1] / -v[2] / hh};
}

struct PoseFeed::Impl {
    float* pose = nullptr;
    pthread_mutex_t* mutex = nullptr;
    bool* disabled = nullptr;
    float* fov = nullptr;  // optional
    ~Impl() { for (void* p : {static_cast<void*>(pose), static_cast<void*>(mutex), static_cast<void*>(disabled), static_cast<void*>(fov)}) if (p) shmdt(p); }
};
namespace {
void* attach(const std::string& path, std::size_t min_size, bool writable, std::string& why) {
    struct stat st{};
    if (stat(path.c_str(), &st) != 0) { why = "feed absent: " + path; return nullptr; }
    const key_t key = ftok(path.c_str(), 0);
    const int id = key == -1 ? -1 : shmget(key, 0, 0);
    shmid_ds info{};
    if (id < 0 || shmctl(id, IPC_STAT, &info) != 0 || info.shm_segsz < min_size) { why = "feed segment invalid: " + path; return nullptr; }
    void* addr = shmat(id, nullptr, writable ? 0 : SHM_RDONLY);
    if (addr == reinterpret_cast<void*>(-1)) { why = "feed attach failed: " + path; return nullptr; }
    return addr;
}
}
std::unique_ptr<PoseFeed> PoseFeed::open(const std::string& prefix, std::string& why) {
    std::unique_ptr<PoseFeed> feed(new PoseFeed());
    feed->impl_ = std::make_unique<Impl>();
    auto& s = *feed->impl_;
    s.pose = static_cast<float*>(attach(prefix + "pose_orientation", sizeof(float) * 16, false, why));
    if (!s.pose) return nullptr;
    // A process-shared mutex must be writable to lock; we never write the pose itself.
    s.mutex = static_cast<pthread_mutex_t*>(attach(prefix + "pose_orientation_mutex", sizeof(pthread_mutex_t), true, why));
    if (!s.mutex) return nullptr;
    s.disabled = static_cast<bool*>(attach(prefix + "disabled", sizeof(bool), false, why));
    if (!s.disabled) return nullptr;
    std::string ignored;
    s.fov = static_cast<float*>(attach(prefix + "display_fov", sizeof(float), false, ignored));
    return feed;
}
PoseFeed::~PoseFeed() = default;
std::optional<float> PoseFeed::display_fov() const {
    if (!impl_->fov) return std::nullopt;
    const float v = *impl_->fov;
    if (!std::isfinite(v) || v < 10.f || v > 120.f) return std::nullopt;
    return v;
}
std::optional<FeedSample> PoseFeed::read() {
    auto& s = *impl_;
    if (*s.disabled) return std::nullopt;
    timespec deadline{};
    clock_gettime(CLOCK_REALTIME, &deadline);
    deadline.tv_nsec += 2'000'000;  // a driver that died holding the lock must not stall rendering
    if (deadline.tv_nsec >= 1'000'000'000) { deadline.tv_sec += 1; deadline.tv_nsec -= 1'000'000'000; }
    if (pthread_mutex_timedlock(s.mutex, &deadline) != 0) return std::nullopt;
    float v[16];
    for (int i = 0; i < 16; ++i) v[i] = s.pose[i];
    pthread_mutex_unlock(s.mutex);
    auto q = quat_normalized({v[0], v[1], v[2], v[3]});
    if (!q || !std::isfinite(v[12])) return std::nullopt;
    return FeedSample{*q, v[12]};
}

void PoseState::update(const std::optional<FeedSample>& sample, double now_ms) {
    if (sample) {
        if (auto q = quat_normalized(sample->orientation)) {
            if (sample->device_ms != last_device_ms_) {
                last_device_ms_ = sample->device_ms; last_change_ms_ = now_ms; ++samples_;
                if (recenter_pending_ || !center_) { center_ = *q; recenter_pending_ = false; }
                last_ = *q;
                const Quat relative = quat_mul(quat_conj(*center_), *q);
                view_ = transpose(driver_to_gl(quat_to_mat3(relative)));
                tracking_ = true;
            }
        }
    }
    if (tracking_ && now_ms - last_change_ms_ > stale_ms_) { tracking_ = false; ++lost_events_; }  // hold last pose
}
}
