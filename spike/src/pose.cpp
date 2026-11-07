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
// PoseState::up_: how far it can be from a worn head, and the filter, in radians and seconds.
constexpr float kDegToRad = 3.14159265f / 180.f;
constexpr float kUpReachDeg = 45.f;  // a recentre: up is no further than this from the head's up
constexpr float kUpWornDeg = 60.f;   // further than this from up, the glasses are not a head looking about
constexpr float kUpFeedDeg = 8.f;    // how far off the feed's z is taken to be, when in reach (1 sigma): usually exact
constexpr float kUpHeadDeg = 20.f;   // ... and the head's own up: the head's pitch is in it
constexpr float kUpNoise = 0.0167f;  // head roll as measurement noise, rad^2 s: about 3 degrees, for about 3 s at a time
constexpr float kUpDrift = 5e-6f;    // rad^2 / s, about a degree a minute: a held tilt is taken for how you sit in about that long
constexpr double kUpMaxStepMs = 100; // a stall in the feed is no more evidence than one frame
constexpr double kUpMinStepMs = 1;   // two samples in one tick are not free evidence
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
    float* look_ahead = nullptr;  // optional
    std::optional<FeedSample> last;  // newest valid sample read: served again while the driver holds the lock
    // After two 2 ms waits in a row time out (a stopped driver, not a busy one), frames only try the
    // lock until retry_ns, then wait once more; while the holder never lets go the gap doubles, 50 ms
    // up to 1 s.
    std::uint64_t retry_ns = 0, backoff_ns = 0;
    unsigned timeouts = 0;
    ~Impl() { for (void* p : {static_cast<void*>(pose), static_cast<void*>(mutex), static_cast<void*>(disabled), static_cast<void*>(fov), static_cast<void*>(look_ahead)}) if (p) shmdt(p); }
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
    s.look_ahead = static_cast<float*>(attach(prefix + "look_ahead_cfg", sizeof(float) * 4, false, ignored));
    return feed;
}
PoseFeed::~PoseFeed() = default;
std::optional<float> PoseFeed::display_fov() const {
    if (!impl_->fov) return std::nullopt;
    const float v = *impl_->fov;
    if (!std::isfinite(v) || v < 10.f || v > 120.f) return std::nullopt;
    return v;
}
std::optional<std::array<float, 4>> PoseFeed::look_ahead_cfg() const {
    if (!impl_->look_ahead) return std::nullopt;
    return std::array<float, 4>{impl_->look_ahead[0], impl_->look_ahead[1], impl_->look_ahead[2], impl_->look_ahead[3]};
}
namespace {
std::uint64_t monotonic_ns() {
    timespec ts{};
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return std::uint64_t(ts.tv_sec) * 1'000'000'000ull + std::uint64_t(ts.tv_nsec);
}
}
std::optional<FeedSample> PoseFeed::read() {
    auto& s = *impl_;
    if (*s.disabled) { s.last.reset(); return std::nullopt; }
    // The driver holds its lock only to copy 16 floats (XRLinuxDriver src/outputs.c), so it is nearly
    // always free. First try with an already-passed deadline: an atomic and no system call when the lock
    // is free. (Not pthread_mutex_trylock: on a not-recoverable robust mutex glibc's trylock leaves our
    // thread id in the lock word, which would hang the driver's next lock forever; clocklock releases it.)
    // If the lock is held, wait for it (futex) for up to 2 ms on the monotonic clock, which wall-clock
    // steps cannot stretch. If it is still held (a stopped driver), serve the newest sample again: its
    // device timestamp is unchanged, so it ages and is predicted like any other read (no sample at all
    // would leave the frame unpredicted). After two such timeouts in a row, frames only try the lock
    // until Impl::retry_ns, so a stopped driver costs a 2 ms wait about once a second instead of on
    // every frame. Outside that back-off a live driver is waited for exactly as before.
    constexpr std::uint64_t kWaitNs = 2'000'000;
    constexpr std::uint64_t kFirstBackoffNs = 50'000'000, kMaxBackoffNs = 1'000'000'000;
    static constexpr timespec kNoWait{0, 0};
    int rc = pthread_mutex_clocklock(s.mutex, CLOCK_MONOTONIC, &kNoWait);
    if (rc == ETIMEDOUT) {
        const std::uint64_t now = monotonic_ns();
        if (s.retry_ns && now < s.retry_ns) return s.last;  // backing off: no waiting
        const std::uint64_t until = now + kWaitNs;
        const timespec deadline{time_t(until / 1'000'000'000ull), long(until % 1'000'000'000ull)};
        rc = pthread_mutex_clocklock(s.mutex, CLOCK_MONOTONIC, &deadline);
        if (rc == ETIMEDOUT) {
            if (++s.timeouts >= 2) {
                s.backoff_ns = s.retry_ns ? std::min(2 * s.backoff_ns, kMaxBackoffNs) : kFirstBackoffNs;
                s.retry_ns = monotonic_ns() + s.backoff_ns;
            }
            return s.last;
        }
    }
    if (rc == EOWNERDEAD) {
        // The driver died mid-write (its mutex is robust): we now own the lock. Make it consistent and
        // release it, or every later read (and a restarted driver) would wait on a lock nobody frees.
        // This frame gets the last good sample, not the possibly half-written one.
        pthread_mutex_consistent(s.mutex);
        pthread_mutex_unlock(s.mutex);
        s.retry_ns = 0; s.timeouts = 0;
        return s.last;
    }
    if (rc != 0) return std::nullopt;  // ENOTRECOVERABLE (lock word released by glibc) or not a mutex: no sample
    s.retry_ns = 0; s.timeouts = 0;
    float v[16];
    for (int i = 0; i < 16; ++i) v[i] = s.pose[i];
    pthread_mutex_unlock(s.mutex);
    auto q = quat_normalized({v[0], v[1], v[2], v[3]});
    if (!q || !std::isfinite(v[12])) return std::nullopt;
    s.last = FeedSample{*q, v[12], {Quat{v[4], v[5], v[6], v[7]}, Quat{v[8], v[9], v[10], v[11]}}, {v[13], v[14]}};
    return s.last;
}

void PoseState::update(const std::optional<FeedSample>& sample, double now_ms) {
    if (sample) {
        if (auto q = quat_normalized(sample->orientation)) {
            if (sample->device_ms != last_device_ms_) {
                if (level_reference_) {
                    if (recenter_pending_ || !center_) {
                        const Mat3 m = quat_to_mat3(*q);  // the left axis holds its heading through any pitch of the head
                        const float half = std::atan2(-m[1], m[4]) / 2;
                        const Quat turn = quat_mul(quat_conj(*q), Quat{0, 0, std::sin(half), std::cos(half)});
                        level_turn_ = turn;
                        levelled_deg_ = quat_angle(Quat{}, turn) * 180.f / 3.14159265f;
                    }
                    q = quat_normalized(quat_mul(*q, level_turn_));
                }
                const double since_ms = now_ms - last_change_ms_;
                last_device_ms_ = sample->device_ms; last_change_ms_ = now_ms; ++samples_;
                if (center_ && !tracking_ && !dropped_ && since_ms > kReanchorAfterMs) dropped_ = true;
                if (dropped_) {
                    if (settling_from_ms_ < 0 || since_ms > stale_ms_) settling_from_ms_ = now_ms;
                    if (now_ms - settling_from_ms_ < kResumeSettleMs) return;  // the view stays held
                    reanchor(*q);
                    dropped_ = false; settling_from_ms_ = -1; ++reanchors_;
                }
                if (recenter_pending_ || !center_) { center_ = *q; recentre_up(*q); recenter_pending_ = false; }
                else learn_up(*q, since_ms);
                last_ = *q;
                const Quat relative = quat_mul(quat_conj(*center_), *q);
                relative_ = relative;
                view_ = transpose(driver_to_gl(quat_to_mat3(relative)));
                tracking_ = true;
            }
        }
    }
    if (tracking_ && now_ms - last_change_ms_ > stale_ms_) { tracking_ = false; lost_at_ms_ = now_ms; ++lost_events_; }  // hold last pose
}

// `head` in the feed's new frame is last_ in the old one, so `frame` turns the old frame's coordinates into the new one's. The
// centre and up go through it: relative_ comes out the same, and so does the level. Up is only as good as "the head did not
// move", so it is held loosely until learn_up has seen the head again.
void PoseState::reanchor(Quat head) {
    const Quat frame = quat_mul(head, quat_conj(last_));
    center_ = quat_mul(frame, *center_);
    up_ = xr::apply(quat_to_mat3(frame), up_);
    const float loose = kUpHeadDeg * kDegToRad;
    for (int i = 0; i < 3; ++i) up_cov_[i * 4] = std::max(up_cov_[i * 4], loose * loose);
    last_ = head;
}

void PoseState::recentre_up(Quat head) {
    const Mat3 m = quat_to_mat3(head);  // columns: the head's forward, left and up in the feed's frame
    const float reach = std::cos(kUpReachDeg * kDegToRad);
    const bool started = up_cov_[0] > 0;
    if (started && m[2] * up_[0] + m[5] * up_[1] + m[8] * up_[2] >= reach) return;
    const bool feed = m[8] >= reach;
    if (feed) up_ = {0, 0, 1};
    else up_ = {m[2], m[5], m[8]};
    const float start = (feed ? kUpFeedDeg : kUpHeadDeg) * kDegToRad;
    up_cov_ = {start * start, 0, 0, 0, start * start, 0, 0, 0, start * start};
}
void PoseState::learn_up(Quat head, double ms) {
    const Mat3 m = quat_to_mat3(head);
    if (m[2] * up_[0] + m[5] * up_[1] + m[8] * up_[2] < std::cos(kUpWornDeg * kDegToRad)) return;
    // One measurement: the head's left axis is level, left . up = 0, with the head's roll as its noise.
    const std::array<float, 3> left{m[1], m[4], m[7]};
    const float seconds = float(std::clamp(ms, kUpMinStepMs, kUpMaxStepMs)) / 1000.f;
    const std::array<float, 3> pl = xr::apply(up_cov_, left);  // (std::apply is a closer match on a non-const array)
    const float s = left[0] * pl[0] + left[1] * pl[1] + left[2] * pl[2] + kUpNoise / seconds;
    const float off = (left[0] * up_[0] + left[1] * up_[1] + left[2] * up_[2]) / s;
    const std::array<float, 3> v{up_[0] - off * pl[0], up_[1] - off * pl[1], up_[2] - off * pl[2]};
    const float n = std::sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]);
    if (n > 1e-6f) up_ = {v[0] / n, v[1] / n, v[2] / n};
    for (int i = 0; i < 3; ++i)
        for (int j = 0; j < 3; ++j) up_cov_[i * 3 + j] -= pl[i] * pl[j] / s - (i == j ? kUpDrift * seconds : 0.f);
}
std::optional<Quat> PoseState::reference() const {
    if (!center_) return std::nullopt;
    // The shortest turn taking up_ to z, half a turn about x when it is exactly opposite. Its heading is arbitrary there, and
    // neither user minds: canvas_up undoes the turn, and environment_view takes the heading of the result.
    const Quat turn = quat_normalized({up_[1], -up_[0], 0, 1 + up_[2]}).value_or(Quat{1, 0, 0, 0});
    return quat_mul(turn, *center_);
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
namespace { constexpr float kHalfTurnSlack = 1e-2f; }  // radians: this near half a turn, which way round is the push's to say
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

std::array<float, 3> canvas_up(Quat reference, Quat anchor) {
    const auto v = apply(transpose(quat_to_mat3(quat_mul(reference, anchor))), {0, 0, 1});  // up, in the canvas's driver frame
    return apply(kDriverToGl, v);
}

void ModeController::set_mode(ViewMode mode, double now_ms, Change how) {
    if (mode == mode_) return;
    holding_ = false;  // a maximize still held when the mode changes again: that change starts from where it is held
    if (how == Change::maximize && mode_ == ViewMode::anchor) {
        // The room (pinned_) is untouched and the canvas stays where it is: the zoom-in grows the desktop in place.
        held_ = anchor_; holding_ = true; transitioning_ = false; velocity_ = {};
        mode_ = mode;
        return;
    }
    from_ = anchor_; started_ms_ = now_ms; transitioning_ = true;
    if (mode == ViewMode::follow) { follow_ = quat_mul(anchor_, quat_conj(aim_)); velocity_ = {}; }  // follow_ * aim = here
    if (mode == ViewMode::anchor && how != Change::restore) pinned_ = anchor_;  // anchor where the canvas is right now
    mode_ = mode;  // restore: the blend heads back to the room as it was left
}
void ModeController::land_maximize(double now_ms) {
    if (!holding_) return;
    holding_ = false;
    from_ = anchor_; started_ms_ = now_ms; transitioning_ = true;  // follow: from == its target, so nothing moves
    if (mode_ == ViewMode::follow) { follow_ = quat_mul(anchor_, quat_conj(aim_)); velocity_ = {}; }  // the aim has landed
}
void ModeController::turn_anchor(Quat turn) {
    pinned_ = quat_normalized(quat_mul(pinned_, turn)).value_or(pinned_);
    if (mode_ == ViewMode::anchor && !transitioning_) anchor_ = pinned_;
}
void ModeController::recenter() { follow_ = Quat{}; anchor_ = from_ = pinned_ = held_ = aim_; transitioning_ = false; velocity_ = {}; }
Quat aim_rotation(float az, float el) {
    // GL: the view must apply R^T = El(el)^T . Az(az)^T (place_point's R = Az(az) . El(el)); in the driver frame (x forward,
    // y left, z up) that is a pitch of el about +y after a yaw of az about +z.
    return quat_mul(Quat{0, std::sin(el / 2), 0, std::cos(el / 2)}, Quat{0, 0, std::sin(az / 2), std::cos(az / 2)});
}
Quat aim_along(Quat from, Quat to, float way, float turned, std::uint8_t side) {
    const float s = way > 0 ? std::clamp(turned / way, 0.f, 1.f) : 1.f;
    if (way < float(M_PI) - kHalfTurnSlack) return quat_slerp(from, to, s);
    Quat d = quat_mul(quat_conj(from), to);
    if (d.w < 0) d = {-d.x, -d.y, -d.z, -d.w};
    const float sn = std::sqrt(d.x * d.x + d.y * d.y + d.z * d.z);
    if (!(sn > 1e-6f)) return s < 1 ? from : to;
    // The push as a turn of the canvas: right about -y, left about +y, up about +x, down about -x (GL frame, one quarter of `from`'s view).
    static constexpr std::array<std::array<float, 3>, 4> kPush{{{0, -1, 0}, {0, 1, 0}, {1, 0, 0}, {-1, 0, 0}}};
    const auto push = xr::apply(transpose(kDriverToGl), kPush[std::min<std::size_t>(side, 3)]);
    float axis[3] = {d.x / sn, d.y / sn, d.z / sn}, angle = 2 * std::atan2(sn, d.w);
    if (axis[0] * push[0] + axis[1] * push[1] + axis[2] * push[2] > 0) {  // a turn about +a of the aim is one about -a of the canvas
        for (float& v : axis) v = -v;
        angle = 2 * float(M_PI) - angle;
    }
    const float half = s * angle / 2, k = std::sin(half);
    return quat_normalized(quat_mul(from, Quat{axis[0] * k, axis[1] * k, axis[2] * k, std::cos(half)})).value_or(to);
}
Quat ModeController::target(Quat head) const {
    if (holding_) return held_;
    switch (mode_) {
    case ViewMode::locked: return quat_mul(head, aim_);
    case ViewMode::follow: return quat_mul(follow_, aim_);
    case ViewMode::anchor: break;
    }
    return pinned_;
}
Quat ModeController::update(Quat head, double now_ms) {
    const double dt = last_ms_ < 0 ? 0 : std::clamp(now_ms - last_ms_, 0.0, 100.0);
    last_ms_ = now_ms;
    if (mode_ == ViewMode::follow && !holding_ && dt > 0) {
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
    return !transitioning_ && !holding_ && (mode_ != ViewMode::follow || v < 0.01f);  // < 0.6 deg/s
}
}
