#include "luma/publish.hpp"
#include <algorithm>
#include <atomic>
#include <cerrno>
#include <cmath>
#include <cstring>
#include <ctime>
#include <fcntl.h>
#include <pthread.h>
#include <sys/ipc.h>
#include <sys/shm.h>
#include <sys/stat.h>
#include <unistd.h>
#include <vector>

namespace luma {
namespace {
constexpr std::uint32_t kMaxHistoryStep = 1024;
constexpr std::uint64_t kNsPerMs = 1'000'000;
constexpr std::uint64_t kLockWaitNs = 2'000'000;
// src/devices/viture.c VITURE_FLOAT_EXACT_MS_LIMIT - VITURE_FLOAT_RESET_MARGIN_MS.
constexpr std::uint64_t kRestartMs = (1ull << 24) - 1000;
// src/imu.c pose_orientation_reset_data: four identities, so t reads 0, 0, 0 and the last slot 1 (publish writes 0 there).
constexpr float kResetPose[16] = {0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1};

struct Entry { float q[4]; float ms; };  // q in the feed's order, xyzw

bool usable(const Quatf& q) {
    const float n = std::sqrt(q.x * q.x + q.y * q.y + q.z * q.z + q.w * q.w);
    return std::isfinite(n) && n >= 1e-6f;  // what pose.cpp quat_normalized accepts
}
}

struct Publisher::Impl {
    PublisherOptions options;
    std::vector<int> ids;               // private_segments: what to remove
    std::vector<std::string> files;     // private_segments: what to remove
    std::vector<void*> attached;
    float* pose = nullptr;
    pthread_mutex_t* mutex = nullptr;
    bool* disabled = nullptr;

    std::vector<Entry> ring;  // 2 * history_step + 1 samples, ring[head] the oldest once full
    std::size_t head = 0, count = 0;
    std::uint64_t epoch_ns = 0, last_ns = 0;
    bool clock_started = false, live = false;
    std::uint64_t written = 0, dropped = 0;

    ~Impl() {
        if (disabled) set_disabled(true);
        for (void* addr : attached) shmdt(addr);
        for (int id : ids) shmctl(id, IPC_RMID, nullptr);
        for (const auto& path : files) unlink(path.c_str());
    }
    void set_disabled(bool value) {
        std::atomic_ref<bool>(*disabled).store(value, std::memory_order_release);
    }

    void* attach(const char* name, std::size_t size, std::string& why) {
        const std::string path = options.prefix + name;
        const bool own = options.private_segments;
        int fd = -1;
        if (own) {
            fd = ::open(path.c_str(), O_CREAT | O_EXCL | O_RDONLY | O_CLOEXEC, 0600);
            if (fd >= 0) files.push_back(path);
        }
        if (fd < 0) fd = ::open(path.c_str(), O_CREAT | O_RDONLY | O_CLOEXEC, own ? 0600 : 0666);
        if (fd < 0) { why = "cannot create " + path + ": " + std::strerror(errno); return nullptr; }
        if (!own) fchmod(fd, 0666);  // src/ipc.c sets umask 0 for this; umask is per process, and it may be someone else's file
        close(fd);
        const key_t key = ftok(path.c_str(), 0);
        if (key == -1) { why = "ftok failed: " + path; return nullptr; }
        int id;
        if (own) {
            id = shmget(key, size, 0600 | IPC_CREAT | IPC_EXCL);
            if (id < 0 && errno == EEXIST) { why = "key in use: " + path; return nullptr; }
            if (id >= 0) ids.push_back(id);
        } else {
            id = shmget(key, size, 0666 | IPC_CREAT);
            if (id < 0 && errno == EINVAL) {  // left over at another size (src/ipc.c setup_ipc_value)
                const int stale = shmget(key, 0, 0);
                if (stale >= 0) shmctl(stale, IPC_RMID, nullptr);
                id = shmget(key, size, 0666 | IPC_CREAT);
            }
        }
        if (id < 0) { why = "shmget failed: " + path + ": " + std::strerror(errno); return nullptr; }
        void* addr = shmat(id, nullptr, 0);
        if (addr == reinterpret_cast<void*>(-1)) { why = "shmat failed: " + path + ": " + std::strerror(errno); return nullptr; }
        attached.push_back(addr);
        return addr;
    }

    // Over whatever is there, as src/ipc.c does, whether or not pthread_mutex_destroy liked it: the bytes may be zeroes, or
    // a lock a dead driver held. At open readers are kept off by disabled; after ENOTRECOVERABLE they try again next frame.
    bool init_mutex() {
        pthread_mutex_destroy(mutex);
        pthread_mutexattr_t attr;
        if (pthread_mutexattr_init(&attr) != 0) return false;
        const bool ok = pthread_mutexattr_setpshared(&attr, PTHREAD_PROCESS_SHARED) == 0 &&
                        pthread_mutexattr_setrobust(&attr, PTHREAD_MUTEX_ROBUST) == 0 && pthread_mutex_init(mutex, &attr) == 0;
        pthread_mutexattr_destroy(&attr);
        return ok;
    }

    // Not pthread_mutex_trylock: see the note in pose.cpp PoseFeed::read on a not-recoverable robust mutex.
    bool lock() {
        timespec now{};
        clock_gettime(CLOCK_MONOTONIC, &now);
        const std::uint64_t until = std::uint64_t(now.tv_sec) * 1'000'000'000ull + std::uint64_t(now.tv_nsec) + kLockWaitNs;
        const timespec deadline{time_t(until / 1'000'000'000ull), long(until % 1'000'000'000ull)};
        int rc = pthread_mutex_clocklock(mutex, CLOCK_MONOTONIC, &deadline);
        if (rc == ENOTRECOVERABLE && init_mutex()) rc = pthread_mutex_clocklock(mutex, CLOCK_MONOTONIC, &deadline);
        if (rc == EOWNERDEAD) { pthread_mutex_consistent(mutex); rc = 0; }  // a reader only copies: nothing to repair
        return rc == 0;
    }

    float device_ms(std::uint64_t t_ns) {
        if (!clock_started || t_ns < last_ns) { epoch_ns = t_ns; clock_started = true; }
        last_ns = t_ns;
        std::uint64_t ms = (t_ns - epoch_ns) / kNsPerMs;  // truncated, like the driver's (uint32_t) cast
        if (ms >= kRestartMs) { epoch_ns = t_ns; ms = 0; }
        return float(ms);
    }
    void pause() {
        set_disabled(true);
        live = false;
        head = count = 0;
    }
};

Publisher::Publisher() : impl_(std::make_unique<Impl>()) {}
Publisher::~Publisher() = default;

std::unique_ptr<Publisher> Publisher::open(const PublisherOptions& options, std::string& why) {
    if (options.history_step < 1 || options.history_step > kMaxHistoryStep) {
        why = "history_step must be 1.." + std::to_string(kMaxHistoryStep);
        return nullptr;
    }
    std::unique_ptr<Publisher> publisher(new Publisher());
    auto& s = *publisher->impl_;
    s.options = options;
    s.ring.resize(2 * std::size_t(options.history_step) + 1);
    // disabled first: a reader still attached from before stops looking at the rest while it changes.
    s.disabled = static_cast<bool*>(s.attach("disabled", sizeof(bool), why));
    if (!s.disabled) return nullptr;
    s.set_disabled(true);
    s.pose = static_cast<float*>(s.attach("pose_orientation", sizeof(float) * 16, why));
    if (!s.pose) return nullptr;
    s.mutex = static_cast<pthread_mutex_t*>(s.attach("pose_orientation_mutex", sizeof(pthread_mutex_t), why));
    if (!s.mutex) return nullptr;
    auto* fov = static_cast<float*>(s.attach("display_fov", sizeof(float), why));
    if (!fov) return nullptr;
    auto* look_ahead = static_cast<float*>(s.attach("look_ahead_cfg", sizeof(float) * 4, why));
    if (!look_ahead) return nullptr;
    if (!s.init_mutex()) { why = "cannot initialise the pose mutex"; return nullptr; }
    std::memcpy(s.pose, kResetPose, sizeof kResetPose);
    *fov = options.display_fov_deg;
    std::memcpy(look_ahead, options.look_ahead_cfg.data(), sizeof(float) * 4);
    return publisher;
}

bool Publisher::publish(const PoseOut& pose) {
    auto& s = *impl_;
    if (!pose.calibrated) {
        if (s.live || s.count) s.pause();
        return false;
    }
    if (!usable(pose.q)) { ++s.dropped; return false; }

    const std::size_t size = s.ring.size();
    const std::size_t slot = (s.head + s.count) % size;
    s.ring[slot] = {{pose.q.x, pose.q.y, pose.q.z, pose.q.w}, s.device_ms(pose.t_ns)};
    if (s.count < size) ++s.count;
    else s.head = (s.head + 1) % size;
    if (s.count < size) return false;

    // Newest, history_step back, twice that: the ring is exactly as long as the span from q0 to q2.
    const std::size_t step = s.options.history_step;
    const Entry& q0 = s.ring[slot];
    const Entry& q1 = s.ring[(slot + size - step) % size];
    const Entry& q2 = s.ring[s.head];
    float v[16];
    std::memcpy(v, q0.q, sizeof q0.q);
    std::memcpy(v + 4, q1.q, sizeof q1.q);
    std::memcpy(v + 8, q2.q, sizeof q2.q);
    v[12] = q0.ms; v[13] = q1.ms; v[14] = q2.ms; v[15] = 0;

    if (!s.lock()) { ++s.dropped; return false; }
    std::memcpy(s.pose, v, sizeof v);
    pthread_mutex_unlock(s.mutex);
    if (!s.live) { s.set_disabled(false); s.live = true; }  // after the write: a reader's first sample is a real one
    ++s.written;
    return true;
}

void Publisher::pause() { impl_->pause(); }
std::uint64_t Publisher::written() const { return impl_->written; }
std::uint64_t Publisher::dropped() const { return impl_->dropped; }
}
