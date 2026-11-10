#pragma once
// A pretend pair of glasses: a thread sends one packet per period through a real kernel object, so the reader pays what it pays on
// hidraw: a blocking poll(), a scheduler wake-up and a read(). A packet-mode pipe with 64 slots stands in for hidraw's per-reader
// queue, which holds 64 reports (HIDRAW_BUFFER_SIZE in the kernel's drivers/hid/hidraw.c). Same read(span, ms) shape as the contract's
// luma::Transport. The packet format is invented; only its size and the need to check a CRC stand for the real one.
#include "luma/rt.hpp"
#include "luma/types.hpp"
#include <array>
#include <atomic>
#include <chrono>
#include <cstring>
#include <fcntl.h>
#include <optional>
#include <poll.h>
#include <span>
#include <string>
#include <thread>
#include <unistd.h>

namespace luma::bench {
struct FakePacket {
    std::uint32_t seq;
    std::uint32_t reserved;
    std::uint64_t t_sent_ns;  // stamped by the sender just before the write: what the bench measures from
    float gyro[3], accel[3];
    std::uint8_t padding[22];
    std::uint16_t crc;
};
static_assert(sizeof(FakePacket) == 64);

inline constexpr std::array<std::uint16_t, 256> kCrcTable = [] {
    std::array<std::uint16_t, 256> table{};
    for (unsigned i = 0; i < 256; ++i) {
        std::uint16_t c = std::uint16_t(i << 8);
        for (int b = 0; b < 8; ++b) c = std::uint16_t((c & 0x8000) ? (c << 1) ^ 0x1021 : c << 1);
        table[i] = c;
    }
    return table;
}();
inline std::uint16_t crc16(std::span<const std::uint8_t> bytes) noexcept {
    std::uint16_t crc = 0xFFFF;
    for (std::uint8_t b : bytes) crc = std::uint16_t((crc << 8) ^ kCrcTable[((crc >> 8) ^ b) & 0xFF]);
    return crc;
}
inline std::optional<FakePacket> parse_fake(std::span<const std::uint8_t> bytes) noexcept {
    FakePacket p;
    if (bytes.size() != sizeof p) return std::nullopt;
    std::memcpy(&p, bytes.data(), sizeof p);
    if (crc16(bytes.first(sizeof p - 2)) != p.crc) return std::nullopt;
    return p;
}

class FakeTransport {
public:
    // realtime: the sender asks for SCHED_FIFO like the reader. Real packets arrive from the USB interrupt, which no busy thread
    // delays; a normal-class sender preempted between its timestamp and its write() would book its own stall as driver latency.
    FakeTransport(int rate_hz, std::uint32_t packets, int sender_cpu, bool realtime = false)
        : period_ns_(1'000'000'000ull / std::uint64_t(rate_hz)), packets_(packets), cpu_(sender_cpu), realtime_(realtime) {}
    ~FakeTransport() { stop(); if (rd_ >= 0) close(rd_); if (wr_ >= 0) close(wr_); }
    bool open(std::string& why) {  // before the reader's first read()
        int fds[2];
        if (pipe2(fds, O_DIRECT | O_NONBLOCK | O_CLOEXEC) != 0) { why = "pipe2"; return false; }
        rd_ = fds[0]; wr_ = fds[1];
        if (fcntl(wr_, F_SETPIPE_SZ, 64 * 4096) < 0) { why = "pipe size"; return false; }  // one packet takes one 4 KiB slot
        return true;
    }
    void start() { sender_ = std::thread([this] { send_loop(); }); }
    void stop() {
        stop_.store(true);
        if (sender_.joinable()) sender_.join();
    }
    // Bytes read, 0 on timeout, negative when the sender has finished and the queue is empty.
    int read(std::span<std::uint8_t> out, std::chrono::milliseconds timeout) noexcept {
        pollfd p{rd_, POLLIN, 0};
        const int ready = poll(&p, 1, int(timeout.count()));
        if (ready <= 0) return 0;
        const ssize_t n = ::read(rd_, out.data(), out.size());
        if (n > 0) return int(n);
        return n == 0 ? -1 : 0;
    }
    std::uint64_t sent() const noexcept { return sent_.load(); }
    std::uint64_t queue_overflows() const noexcept { return overflows_.load(); }  // what hidraw would have discarded
    std::uint64_t late_sends() const noexcept { return late_.load(); }            // sender woke over 1 period late: our rig, not the driver
    bool finished() const noexcept { return finished_.load(); }
    bool sender_fifo() const noexcept { return sender_sched_.load(); }  // once the first packet is out
private:
    void send_loop() {
        rt::ThreadPolicy policy;
        policy.realtime = realtime_;
        policy.cpu = cpu_;
        sender_sched_.store(rt::apply_thread_policy(policy).sched == rt::SchedClass::fifo);
        std::uint64_t target = rt::monotonic_raw_ns() + period_ns_;
        FakePacket p{};
        constexpr std::uint64_t kSpinNs = 50'000;  // sleep most of the period, spin the rest: an exact cadence, whatever the scheduler does
        for (std::uint32_t seq = 0; seq < packets_ && !stop_.load(std::memory_order_relaxed); ++seq) {
            const std::uint64_t now = rt::monotonic_raw_ns();
            if (now + kSpinNs < target) {
                const std::uint64_t nap = target - now - kSpinNs;
                const timespec ts{0, long(nap)};
                clock_nanosleep(CLOCK_MONOTONIC, 0, &ts, nullptr);
            }
            while (rt::monotonic_raw_ns() < target) __builtin_ia32_pause();
            p.seq = seq;
            const float wobble = float(seq % 1000) * 1e-3f;
            p.gyro[0] = wobble; p.gyro[1] = -wobble; p.gyro[2] = 0.1f;
            p.accel[0] = 0.1f; p.accel[1] = 0.2f; p.accel[2] = 9.8f;
            const std::uint64_t t = rt::monotonic_raw_ns();
            if (t > target + period_ns_) late_.fetch_add(1, std::memory_order_relaxed);
            p.t_sent_ns = t;
            p.crc = crc16(std::span(reinterpret_cast<const std::uint8_t*>(&p), sizeof p - 2));
            sent_.fetch_add(1, std::memory_order_relaxed);
            if (::write(wr_, &p, sizeof p) != ssize_t(sizeof p)) overflows_.fetch_add(1, std::memory_order_relaxed);
            target += period_ns_;
        }
        finished_.store(true);
    }
    std::uint64_t period_ns_;
    std::uint32_t packets_;
    int cpu_;
    bool realtime_;
    std::atomic<bool> sender_sched_{false};
    int rd_ = -1, wr_ = -1;
    std::thread sender_;
    std::atomic<bool> stop_{false}, finished_{false};
    std::atomic<std::uint64_t> sent_{0}, overflows_{0}, late_{0};
};
}  // namespace luma::bench
