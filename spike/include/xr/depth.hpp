#pragma once
// The AI depth engine's contract (docs/depth-engine.md). The engine is a separate process (tools/depth_engine), so a
// crash there can never take the renderer with it: if engine_alive() goes false the renderer draws the desktop flat.
//
// One shared-memory file carries a channel per XR desktop. Each channel has two directions:
//   input   renderer -> engine   a desktop, already shrunk by the GPU (src/depth_feed.hpp), RGBA8 or BGRA8
//   output  engine -> renderer   a depth map, R16F or R32F: normalised disparity, 0 = far, 1 = near, temporally smoothed
// Everything is plain data with no pointers, and tools/depth_engine/shm.py mirrors this layout byte for byte
// (tests/depth_engine/test_contract.py compares the two).
//
// Each direction is one writer, one reader and kDepthSlots slots behind a seqlock. The writer never waits for the
// reader, so a slow engine cannot stall a 120 Hz renderer. The reader copies a slot and rereads its sequence number; a
// slot the writer came back to reads as "nothing new" and the next fetch gets a newer frame.
#include <atomic>
#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <optional>
#include <span>
#include <utility>
#include <vector>

namespace xr {
enum class DepthFormat : std::uint32_t { None = 0, Rgba8 = 1, Bgra8 = 2, R16F = 3, R32F = 4 };
constexpr std::size_t depth_bytes_per_pixel(DepthFormat f) {
    switch (f) {
    case DepthFormat::Rgba8: case DepthFormat::Bgra8: case DepthFormat::R32F: return 4;
    case DepthFormat::R16F: return 2;
    case DepthFormat::None: break;
    }
    return 0;
}

constexpr std::uint32_t kDepthSceneCut = 1;  // Output: the smoothing was reset, so a renderer may snap instead of ease.
constexpr std::uint32_t kDepthFlat = 2;      // Output: nothing to separate (a blank frame); every pixel is 0.5.

// What the renderer samples as a GL texture (R16F: GL_RED / GL_HALF_FLOAT, tightly packed, row 0 is the frame's top).
struct DepthFrame {
    int w = 0, h = 0;
    std::uint64_t source_seq = 0;  // The capture frame this came from; starts at 1 (0 means "none" to fetch).
    std::uint64_t source_ns = 0;   // CLOCK_MONOTONIC when that frame was captured; done_ns - source_ns is the lag.
    std::uint64_t done_ns = 0;     // CLOCK_MONOTONIC when the map was finished.
    DepthFormat format = DepthFormat::R16F;
    std::uint32_t flags = 0;
    std::span<const std::byte> pixels{};
};

// The model takes multiples of its 14 px patch.
constexpr int kDepthPatch = 14;
// Design contract: a depth map is at least 256 px tall; 266 is the first multiple of 14 above that.
constexpr int kDepthMinTall = 266;
constexpr int kDepthLongHigh = 518;      // One desktop: 518x322 for 16:10.
constexpr int kDepthLongBalanced = 420;  // Three or four desktops: 420x266 for 16:10.
struct DepthSize { int w = 0, h = 0; };
inline int depth_round_patch(double v) { return std::max(1, static_cast<int>(std::lround(v / kDepthPatch))) * kDepthPatch; }
// The input size for a desktop: keeps its shape, long side near long_side, never under kDepthMinTall. Square input
// would spend 60% more model time on a 16:10 desktop for the same detail.
inline DepthSize depth_input_size(int src_w, int src_h, int long_side) {
    if (src_w <= 0 || src_h <= 0 || long_side <= 0) return {};
    const double aspect = static_cast<double>(src_w) / src_h;
    int w = 0, h = 0;
    if (aspect >= 1.0) { w = depth_round_patch(long_side); h = depth_round_patch(long_side / aspect); }
    else { h = depth_round_patch(long_side); w = depth_round_patch(long_side * aspect); }
    if (h < kDepthMinTall) { h = kDepthMinTall; w = depth_round_patch(h * aspect); }
    return {w, h};
}

// What the renderer's stereo pass reads (docs/stereo-render.md), whichever way the map arrived. Values are "nearness": larger
// is nearer. `lo` / `hi` are the farthest and nearest values of this frame, so a model's arbitrary scale needs no conversion
// (the engine's maps are 0..1 already, the defaults). NaN and infinity are allowed and are drawn flat. One of `pixels` and
// `texture` is set:
//   pixels   `h` rows of `w` floats (R32F), top row first, `stride` bytes apart (0: tightly packed). Read only during the call.
//   texture  a GL texture name in the renderer's context, R16F or R32F, row 0 the top row.
struct DepthInput {
    int w = 0, h = 0;
    std::uint64_t source_seq = 0;  // The capture frame this was estimated from.
    const float* pixels = nullptr;
    std::size_t stride = 0;
    unsigned texture = 0;
    float lo = 0.f, hi = 1.f;
};
inline constexpr int kDepthMinHeight = 256;
inline bool depth_input_valid(const DepthInput& f) {
    if (f.w <= 0 || f.h < kDepthMinHeight || (!f.pixels && !f.texture)) return false;
    return !f.pixels || !f.stride || f.stride >= std::size_t(f.w) * sizeof(float);
}

// The shared-memory layout.
constexpr std::uint32_t kDepthMagic = 0x31445258;  // 'XRD1'
constexpr std::uint32_t kDepthVersion = 1;
constexpr std::uint32_t kDepthSlots = 3;
constexpr std::uint32_t kDepthMaxChannels = 16;
constexpr std::size_t kDepthHeaderBytes = 4096;
constexpr std::size_t kDepthChannelHeaderBytes = 320;
constexpr std::uint32_t kDepthEnabled = 1, kDepthFocused = 2;  // DepthChannelHeader::control, set by the renderer.

struct DepthShmHeader {
    std::uint32_t magic, version, channels, slots;
    std::uint32_t in_w, in_h, out_w, out_h;  // Largest frame a slot holds.
    std::uint64_t channel_stride, total_bytes;
    std::uint64_t engine_ns;                 // CLOCK_MONOTONIC heartbeat, written by the engine.
    std::uint32_t engine_pid, reserved;
};
struct DepthSlotMeta {
    std::uint32_t seq, w, h, format;  // seq: odd while being written.
    std::uint64_t source_seq, source_ns, done_ns;
    std::uint32_t flags, reserved;
};
struct DepthChannelHeader {
    std::uint32_t latest_in, latest_out, control, reserved;
    DepthSlotMeta in[kDepthSlots], out[kDepthSlots];
    std::byte pad[16];
};
static_assert(sizeof(DepthShmHeader) == 64 && sizeof(DepthSlotMeta) == 48 && sizeof(DepthChannelHeader) == kDepthChannelHeaderBytes);

struct DepthShmConfig {
    std::uint32_t channels = 4;
    int in_w = kDepthLongHigh, in_h = kDepthLongHigh, out_w = kDepthLongHigh, out_h = kDepthLongHigh;
};
constexpr std::size_t depth_align_page(std::size_t n) { return (n + 4095) / 4096 * 4096; }
constexpr std::size_t depth_channel_stride(const DepthShmConfig& c) {
    return depth_align_page(kDepthChannelHeaderBytes + kDepthSlots * static_cast<std::size_t>(c.in_w) * c.in_h * 4
                            + kDepthSlots * static_cast<std::size_t>(c.out_w) * c.out_h * 4);
}
constexpr std::size_t depth_shm_bytes(const DepthShmConfig& c) { return kDepthHeaderBytes + depth_channel_stride(c) * c.channels; }

// The shared file, mapped by the caller at `base`. A view holds no state of its own beyond the mapping.
class DepthShm {
public:
    enum class Side { Input, Output };
    // The renderer makes the file: `bytes` must be depth_shm_bytes(cfg) and zeroed.
    static std::optional<DepthShm> create(void* base, std::size_t bytes, const DepthShmConfig& cfg) {
        if (!base || cfg.channels == 0 || cfg.channels > kDepthMaxChannels || cfg.in_w <= 0 || cfg.in_h <= 0 || cfg.out_w <= 0 || cfg.out_h <= 0
            || bytes < depth_shm_bytes(cfg)) return std::nullopt;
        auto* h = static_cast<DepthShmHeader*>(base);
        h->version = kDepthVersion; h->channels = cfg.channels; h->slots = kDepthSlots;
        h->in_w = static_cast<std::uint32_t>(cfg.in_w); h->in_h = static_cast<std::uint32_t>(cfg.in_h);
        h->out_w = static_cast<std::uint32_t>(cfg.out_w); h->out_h = static_cast<std::uint32_t>(cfg.out_h);
        h->channel_stride = depth_channel_stride(cfg); h->total_bytes = depth_shm_bytes(cfg);
        std::atomic_ref<std::uint32_t>(h->magic).store(kDepthMagic, std::memory_order_release);  // Last: the file is ready.
        return DepthShm(static_cast<std::byte*>(base), bytes);
    }
    // Refuses anything that is not a whole file of this version (a stale file, a half-made one, a short mapping).
    static std::optional<DepthShm> attach(void* base, std::size_t bytes) {
        if (!base || bytes < sizeof(DepthShmHeader)) return std::nullopt;
        auto* h = static_cast<DepthShmHeader*>(base);
        if (std::atomic_ref<std::uint32_t>(h->magic).load(std::memory_order_acquire) != kDepthMagic) return std::nullopt;
        if (h->version != kDepthVersion || h->slots != kDepthSlots || h->channels == 0 || h->channels > kDepthMaxChannels) return std::nullopt;
        const DepthShmConfig cfg{h->channels, static_cast<int>(h->in_w), static_cast<int>(h->in_h), static_cast<int>(h->out_w), static_cast<int>(h->out_h)};
        if (h->total_bytes != depth_shm_bytes(cfg) || h->channel_stride != depth_channel_stride(cfg) || bytes < h->total_bytes) return std::nullopt;
        return DepthShm(static_cast<std::byte*>(base), bytes);
    }
    std::uint32_t channels() const { return header()->channels; }

    // Publish a frame. False (nothing written) for a channel or size this file has no room for, or a frame too short.
    bool publish(Side side, std::uint32_t channel, const DepthFrame& f) {
        const std::size_t bpp = depth_bytes_per_pixel(f.format);
        if (channel >= channels() || f.w <= 0 || f.h <= 0 || !bpp) return false;
        const auto* h = header();
        const auto [max_w, max_h] = side == Side::Input ? std::pair{h->in_w, h->in_h} : std::pair{h->out_w, h->out_h};
        const std::size_t bytes = static_cast<std::size_t>(f.w) * f.h * bpp;
        if (static_cast<std::uint32_t>(f.w) > max_w || static_cast<std::uint32_t>(f.h) > max_h || f.pixels.size() < bytes) return false;
        auto* c = chan(channel);
        auto& latest = side == Side::Input ? c->latest_in : c->latest_out;
        const std::uint32_t slot = (std::atomic_ref<std::uint32_t>(latest).load(std::memory_order_relaxed) + 1) % kDepthSlots;
        DepthSlotMeta& m = (side == Side::Input ? c->in : c->out)[slot];
        std::atomic_ref<std::uint32_t> seq(m.seq);
        const std::uint32_t s = seq.load(std::memory_order_relaxed);
        seq.store(s + 1, std::memory_order_relaxed);
        std::atomic_thread_fence(std::memory_order_release);
        std::memcpy(slot_pixels(channel, side, slot), f.pixels.data(), bytes);
        m.w = static_cast<std::uint32_t>(f.w); m.h = static_cast<std::uint32_t>(f.h); m.format = static_cast<std::uint32_t>(f.format);
        m.source_seq = f.source_seq; m.source_ns = f.source_ns; m.done_ns = f.done_ns; m.flags = f.flags;
        seq.store(s + 2, std::memory_order_release);
        std::atomic_ref<std::uint32_t>(latest).store(slot, std::memory_order_release);
        return true;
    }
    // The newest frame into `storage`, if there is one with a source_seq other than `seen`. Never blocks.
    std::optional<DepthFrame> fetch(Side side, std::uint32_t channel, std::vector<std::byte>& storage, std::uint64_t seen = 0) const {
        if (channel >= channels()) return std::nullopt;
        auto* c = const_cast<DepthShm*>(this)->chan(channel);
        for (int attempt = 0; attempt < 4; ++attempt) {
            const std::uint32_t slot = std::atomic_ref<std::uint32_t>(side == Side::Input ? c->latest_in : c->latest_out).load(std::memory_order_acquire);
            if (slot >= kDepthSlots) return std::nullopt;
            DepthSlotMeta& m = (side == Side::Input ? c->in : c->out)[slot];
            std::atomic_ref<std::uint32_t> seq(m.seq);
            const std::uint32_t before = seq.load(std::memory_order_acquire);
            if (before == 0 || (before & 1)) { if (before == 0) return std::nullopt; continue; }
            DepthFrame f{static_cast<int>(m.w), static_cast<int>(m.h), m.source_seq, m.source_ns, m.done_ns, static_cast<DepthFormat>(m.format), m.flags, {}};
            if (f.source_seq == seen) { if (seq.load(std::memory_order_acquire) == before) return std::nullopt; continue; }
            const std::size_t bpp = depth_bytes_per_pixel(f.format), bytes = static_cast<std::size_t>(f.w) * f.h * bpp;
            if (!bpp || f.w <= 0 || f.h <= 0 || bytes > slot_bytes(side)) continue;  // Read mid-write: the sequence recheck would fail too.
            storage.resize(bytes);
            std::memcpy(storage.data(), const_cast<DepthShm*>(this)->slot_pixels(channel, side, slot), bytes);
            std::atomic_thread_fence(std::memory_order_acquire);
            if (seq.load(std::memory_order_relaxed) != before) continue;
            f.pixels = storage;
            return f;
        }
        return std::nullopt;
    }
    void set_control(std::uint32_t channel, std::uint32_t control) {
        if (channel < channels()) std::atomic_ref<std::uint32_t>(chan(channel)->control).store(control, std::memory_order_release);
    }
    std::uint32_t control(std::uint32_t channel) const {
        return channel < channels() ? std::atomic_ref<std::uint32_t>(const_cast<DepthShm*>(this)->chan(channel)->control).load(std::memory_order_acquire) : 0;
    }
    void heartbeat(std::uint64_t now_ns, std::uint32_t pid) {
        header()->engine_pid = pid;
        std::atomic_ref<std::uint64_t>(header()->engine_ns).store(now_ns, std::memory_order_release);
    }
    // False when the engine never started or has been silent for timeout_ns: draw flat.
    bool engine_alive(std::uint64_t now_ns, std::uint64_t timeout_ns = 500'000'000) const {
        const std::uint64_t beat = std::atomic_ref<std::uint64_t>(const_cast<DepthShm*>(this)->header()->engine_ns).load(std::memory_order_acquire);
        return beat != 0 && (now_ns <= beat || now_ns - beat <= timeout_ns);
    }
private:
    DepthShm(std::byte* base, std::size_t bytes) : base_(base), bytes_(bytes) {}
    DepthShmHeader* header() const { return reinterpret_cast<DepthShmHeader*>(base_); }
    DepthChannelHeader* chan(std::uint32_t i) { return reinterpret_cast<DepthChannelHeader*>(base_ + kDepthHeaderBytes + i * header()->channel_stride); }
    std::size_t slot_bytes(Side side) const {
        const auto* h = header();
        return side == Side::Input ? static_cast<std::size_t>(h->in_w) * h->in_h * 4 : static_cast<std::size_t>(h->out_w) * h->out_h * 4;
    }
    std::byte* slot_pixels(std::uint32_t channel, Side side, std::uint32_t slot) {
        auto* p = reinterpret_cast<std::byte*>(chan(channel)) + kDepthChannelHeaderBytes;
        return side == Side::Input ? p + slot * slot_bytes(Side::Input)
                                   : p + kDepthSlots * slot_bytes(Side::Input) + slot * slot_bytes(Side::Output);
    }
    std::byte* base_ = nullptr;
    std::size_t bytes_ = 0;
};
}
