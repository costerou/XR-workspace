#include "xr/pose.hpp"
#include <algorithm>
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
                relative_ = relative;
                view_ = transpose(driver_to_gl(quat_to_mat3(relative)));
                tracking_ = true;
            }
        }
    }
    if (tracking_ && now_ms - last_change_ms_ > stale_ms_) { tracking_ = false; ++lost_events_; }  // hold last pose
}

Quat quat_slerp(Quat a, Quat b, float t) {
    float d = a.x * b.x + a.y * b.y + a.z * b.z + a.w * b.w;
    if (d < 0) { b = {-b.x, -b.y, -b.z, -b.w}; d = -d; }
    float wa = 1 - t, wb = t;
    if (d < 0.9995f) {
        const float th = std::acos(std::min(d, 1.f)), s = std::sin(th);
        wa = std::sin((1 - t) * th) / s; wb = std::sin(t * th) / s;
    }
    return quat_normalized({wa * a.x + wb * b.x, wa * a.y + wb * b.y, wa * a.z + wb * b.z, wa * a.w + wb * b.w}).value_or(b);
}
float quat_angle(Quat a, Quat b) {  // atan2 form: accurate near zero, where acos is not
    const Quat d = quat_mul(quat_conj(a), b);
    return 2 * std::atan2(std::sqrt(d.x * d.x + d.y * d.y + d.z * d.z), std::fabs(d.w));
}
Quat level(Quat q) {
    const auto f = apply(quat_to_mat3(q), {1, 0, 0});  // forward (x) in the reference frame; z up
    const float yaw = std::atan2(f[1], f[0]), pitch = std::asin(std::clamp(f[2], -1.f, 1.f));
    const Quat qy{0, 0, std::sin(yaw / 2), std::cos(yaw / 2)};
    const Quat qp{0, std::sin(-pitch / 2), 0, std::cos(-pitch / 2)};  // +pitch tips forward toward +z
    return quat_mul(qy, qp);
}
Mat3 view_from(Quat head, Quat anchor) {
    return transpose(driver_to_gl(quat_to_mat3(quat_mul(quat_conj(anchor), head))));
}

void ModeController::set_mode(ViewMode mode, double now_ms) {
    if (mode == mode_) return;
    from_ = anchor_; started_ms_ = now_ms; transitioning_ = true;
    if (mode == ViewMode::follow) { follow_ = anchor_; velocity_ = {}; }
    mode_ = mode;
}
void ModeController::recenter() { anchor_ = from_ = follow_ = Quat{}; transitioning_ = false; velocity_ = {}; }
Quat ModeController::target(Quat head) const {
    switch (mode_) {
    case ViewMode::locked: return head;
    case ViewMode::follow: return follow_;
    case ViewMode::anchor: break;
    }
    return Quat{};
}
Quat ModeController::update(Quat head, double now_ms) {
    const double dt = last_ms_ < 0 ? 0 : std::clamp(now_ms - last_ms_, 0.0, 100.0);
    last_ms_ = now_ms;
    if (mode_ == ViewMode::follow && dt > 0) {
        // Critically damped spring on the rotation error (axis-angle in the canvas frame):
        // a = w^2 e - 2 w v. Semi-implicit Euler in <=4 ms substeps (stable for w*h << 1).
        const float w = tuning_.follow_omega;
        double left = dt;
        while (left > 0) {
            const float h = float(std::min(left, 4.0) / 1000.0); left -= 4.0;
            Quat e = quat_mul(quat_conj(follow_), head);
            if (e.w < 0) e = {-e.x, -e.y, -e.z, -e.w};  // shortest way round
            const float sn = std::sqrt(e.x * e.x + e.y * e.y + e.z * e.z);
            const float angle = 2 * std::atan2(sn, e.w), k = sn > 1e-7f ? angle / sn : 2.f;
            const std::array<float, 3> err{e.x * k, e.y * k, e.z * k};
            for (int a = 0; a < 3; ++a) velocity_[a] += (w * w * err[a] - 2 * w * velocity_[a]) * h;
            const float vx = velocity_[0] * h, vy = velocity_[1] * h, vz = velocity_[2] * h;
            const float th = std::sqrt(vx * vx + vy * vy + vz * vz);
            if (th > 1e-9f) {
                const float c = std::sin(th / 2) / th;
                follow_ = quat_normalized(quat_mul(follow_, Quat{vx * c, vy * c, vz * c, std::cos(th / 2)})).value_or(follow_);
            }
        }
    }
    const Quat goal = target(head);
    if (transitioning_) {
        const float t = float(std::clamp((now_ms - started_ms_) / tuning_.transition_ms, 0.0, 1.0));
        const float s = t * t * (3 - 2 * t);
        anchor_ = quat_slerp(from_, goal, s);
        if (t >= 1) transitioning_ = false;
    } else anchor_ = goal;
    return anchor_;
}
bool ModeController::settled() const {
    const float v = std::sqrt(velocity_[0] * velocity_[0] + velocity_[1] * velocity_[1] + velocity_[2] * velocity_[2]);
    return !transitioning_ && (mode_ != ViewMode::follow || v < 0.01f);  // < 0.6 deg/s
}
}
