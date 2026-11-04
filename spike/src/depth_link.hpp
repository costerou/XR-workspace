#pragma once
// The renderer's end of the AI depth engine (docs/immersive-3d.md): each 3D desktop's picture out to the engine, its depth map
// back to the compositor. The engine is another process behind shared memory (xr/depth.hpp), so nothing here can wait for it
// and nothing it does can reach the renderer: when its heartbeat stops, this simply stops feeding and stops vouching for the
// last maps, and the compositor eases those desktops flat by itself.
//
// The GPU budget is per frame, not per desktop: at most one shrink and one map upload each tick, whatever the number of desktops,
// so 3D's cost on the render thread is bounded and head tracking is not paid for it. The pose is read after tick(), as ever.
// No link exists while nothing is 3D, so a 2D run does no work here at all.
//
// Everything needs the compositor's GL context current.
#include "gl.hpp"
#include "depth_feed.hpp"
#include "xr/depth.hpp"
#include "xr/desktops.hpp"
#include <array>
#include <cstddef>
#include <cstdint>
#include <memory>
#include <span>
#include <string>
#include <vector>

namespace xr {
static_assert(kDepthLinkShm.channels == kMaxDesktops);
inline constexpr std::uint64_t kDepthFeedFocusedNs = 1'000'000'000ull / 60, kDepthFeedOtherNs = 1'000'000'000ull / 30;

// The nearness at `percentile` (0..100) of a half-float map, read from one pixel in 16. Positive half floats sort as their
// bit patterns, so this needs no conversion but the answer's; a map holds 0..1. Empty: 0.5.
float depth_anchor(std::span<const std::uint16_t> half, int w, int h, float percentile);
float depth_half_to_float(std::uint16_t half);

class DepthLink {
public:
    // `fd`: the shared memory, a memfd the supervisor made (it outlives any renderer, so a renderer started after a drop finds
    // the running engine). A blank one gets the layout; one a renderer before this one laid out is taken over. nullptr, with
    // `why`, if it cannot be mapped or is the wrong size. Does not take the fd.
    static std::unique_ptr<DepthLink> open(int fd, std::string& why);
    ~DepthLink();
    DepthLink(const DepthLink&) = delete;
    DepthLink& operator=(const DepthLink&) = delete;

    struct Tick {
        std::span<const bool> wanted;              // per desktop: 3D is asked for
        std::span<const std::uint64_t> frames;     // per desktop: its capture's frame count, rising
        int focused = -1;                          // the desktop being looked at: fed at the higher rate
        bool drawing = false;                      // the compositor is drawing 3D: maps are wanted, not just the engine warmed up
        float convergence = 92.f;                  // the screen plane, as a percentile of each map's nearness
        float range_ms = 3000.f;                   // how slowly that plane moves
        std::uint64_t now_ns = 0;                  // CLOCK_MONOTONIC
    };
    // Once per frame, after the frame's pictures are presented to `gl`. Never blocks.
    void tick(GlCompositor& gl, const Tick& t);
    // Desktops were added or removed: every channel now means another desktop.
    void restart();
    // Every channel off, for the engine to go idle; the link is about to go.
    void stop();
    bool engine_alive() const { return alive_; }
    std::uint64_t feeds() const { return feeds_; }
    std::uint64_t maps() const { return maps_; }
    std::string report() const;  // bounded JSON
private:
    DepthLink() = default;
    struct Channel {
        std::unique_ptr<DepthFeed> feed;
        std::uint64_t fed_frames = 0, seq = 0, next_ns = 0, seen = 0, source_ns = 0, map_ns = 0;
        bool bgr = false, on = false, have_anchor = false;
        float anchor = 0.5f;
        std::vector<std::byte> rgba;
    };
    void* base_ = nullptr;
    std::size_t bytes_ = 0;
    std::optional<DepthShm> shm_;
    std::array<Channel, kMaxDesktops> channels_;
    std::vector<std::byte> map_;
    std::size_t next_feed_ = 0, next_map_ = 0;
    bool alive_ = false, feed_failed_ = false;
    std::uint64_t feeds_ = 0, maps_ = 0, lag_us_ = 0;
};
}
