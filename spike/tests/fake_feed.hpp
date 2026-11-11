#pragma once
// Private SysV feed laid out like XRLinuxDriver's (src/ipc.c): 16 pose floats under a process-shared
// ROBUST mutex, the disabled flag, display FOV and look-ahead config. Segments are created with
// IPC_EXCL: ftok keys use only 16 bits of the file's inode, so a key can collide with another segment
// (a live driver's, say). On a collision the file is moved aside and recreated with a new inode, so a
// test never attaches to, re-initialises or removes a segment it did not create.
#include "xr/pose.hpp"
#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <pthread.h>
#include <string>
#include <sys/ipc.h>
#include <sys/shm.h>
#include <vector>

namespace xr::test {
class FakeFeed {
public:
    float* pose = nullptr;
    pthread_mutex_t* mutex = nullptr;
    bool* disabled = nullptr;
    float* fov = nullptr;
    float* look_ahead = nullptr;
    explicit FakeFeed(std::string prefix, float fov_deg = 52.f, std::array<float, 4> cfg = {10, 0.6f, 10, 40}) : prefix_(std::move(prefix)) {
        pose = static_cast<float*>(make("pose_orientation", sizeof(float) * 16));
        mutex = static_cast<pthread_mutex_t*>(make("pose_orientation_mutex", sizeof(pthread_mutex_t)));
        disabled = static_cast<bool*>(make("disabled", sizeof(bool)));
        fov = static_cast<float*>(make("display_fov", sizeof(float)));
        look_ahead = static_cast<float*>(make("look_ahead_cfg", sizeof(float) * 4));
        *fov = fov_deg;
        std::memcpy(look_ahead, cfg.data(), sizeof(float) * 4);
        pthread_mutexattr_t attr;
        pthread_mutexattr_init(&attr);
        pthread_mutexattr_setpshared(&attr, PTHREAD_PROCESS_SHARED);
        pthread_mutexattr_setrobust(&attr, PTHREAD_MUTEX_ROBUST);
        pthread_mutex_init(mutex, &attr);
        pthread_mutexattr_destroy(&attr);
        *disabled = false;
    }
    FakeFeed(const FakeFeed&) = delete;
    FakeFeed& operator=(const FakeFeed&) = delete;
    // The driver's publish: all 16 floats under the lock (recovering it if its holder died).
    void write(const float (&v)[16]) {
        if (pthread_mutex_lock(mutex) == EOWNERDEAD) pthread_mutex_consistent(mutex);
        std::memcpy(pose, v, sizeof v);
        pthread_mutex_unlock(mutex);
    }
    // Newest q at ms; the older two the same orientation at ms - 16 and ms - 32.
    void write(Quat q, float ms) {
        const float v[16] = {q.x, q.y, q.z, q.w, q.x, q.y, q.z, q.w, q.x, q.y, q.z, q.w, ms, ms - 16, ms - 32, 0};
        write(v);
    }
    ~FakeFeed() { cleanup(); }
    // A segment beyond XRLinuxDriver's, made the same way and removed with the rest: one a shared (not
    // private_segments) Luma publisher would otherwise create itself and, by design, leave behind.
    void* also(const char* name, std::size_t size) { return make(name, size); }
private:
    void* make(const char* name, std::size_t size) {
        const std::string path = prefix_ + name;
        // Each retry moves to the next inode (tmpfs numbers them in sequence), and so the next key; a live
        // driver holds a run of ~28 consecutive keys, so allow far more attempts than that.
        for (int attempt = 0; attempt < 1024; ++attempt) {
            if (FILE* f = std::fopen(path.c_str(), "w")) std::fclose(f);
            if (attempt == 0) files_.push_back(path);
            const int id = shmget(ftok(path.c_str(), 0), size, 0600 | IPC_CREAT | IPC_EXCL);
            if (id >= 0) {
                ids_.push_back(id);
                void* addr = shmat(id, nullptr, 0);
                if (addr == reinterpret_cast<void*>(-1)) fail("shmat", path);
                return addr;
            }
            if (errno != EEXIST) fail("shmget", path);
            // Key taken by someone else's segment: keep this inode alive under another name, try a new file.
            const std::string aside = path + ".collision" + std::to_string(attempt);
            std::rename(path.c_str(), aside.c_str());
            files_.push_back(aside);
        }
        fail("no free SysV key", path);
    }
    [[noreturn]] void fail(const char* what, const std::string& path) {
        std::fprintf(stderr, "%s: %s (%s)\n", what, path.c_str(), std::strerror(errno));
        cleanup();  // remove only what this fixture created
        std::exit(2);
    }
    void cleanup() {
        for (int id : ids_) shmctl(id, IPC_RMID, nullptr);
        ids_.clear();
        for (const auto& path : files_) std::remove(path.c_str());
        files_.clear();
    }
    std::string prefix_;
    std::vector<int> ids_;
    std::vector<std::string> files_;
};
}
