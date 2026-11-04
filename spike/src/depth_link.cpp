#include "depth_link.hpp"
#include <algorithm>
#include <cerrno>
#include <cmath>
#include <cstring>
#include <sys/mman.h>
#include <sys/stat.h>

namespace xr {
float depth_half_to_float(std::uint16_t half) {
    const std::uint32_t sign = std::uint32_t(half & 0x8000u) << 16, exponent = (half >> 10) & 0x1fu, mantissa = half & 0x3ffu;
    std::uint32_t bits = sign;
    if (exponent == 0x1fu) bits |= 0x7f800000u | (mantissa << 13);
    else if (exponent) bits |= ((exponent + 112u) << 23) | (mantissa << 13);
    else if (mantissa) {  // subnormal: normalise it
        std::uint32_t e = 113u, m = mantissa;
        while (!(m & 0x400u)) { m <<= 1; --e; }
        bits |= (e << 23) | ((m & 0x3ffu) << 13);
    }
    float out;
    std::memcpy(&out, &bits, sizeof out);
    return out;
}

float depth_anchor(std::span<const std::uint16_t> half, int w, int h, float percentile) {
    if (w <= 0 || h <= 0 || half.size() < std::size_t(w) * std::size_t(h)) return 0.5f;
    thread_local std::vector<std::uint16_t> sample;  // the render thread's; no allocation once it has grown
    sample.clear();
    for (int y = 0; y < h; y += 4) for (int x = 0; x < w; x += 4) {
        const std::uint16_t v = half[std::size_t(y) * std::size_t(w) + std::size_t(x)];
        sample.push_back(v & 0x8000u ? std::uint16_t(0) : v);  // a map holds 0..1; a stray negative is the far end
    }
    if (sample.empty()) return 0.5f;
    const auto rank = std::size_t(std::clamp(percentile, 0.f, 100.f) / 100.f * float(sample.size() - 1));
    std::nth_element(sample.begin(), sample.begin() + std::ptrdiff_t(rank), sample.end());
    const float v = depth_half_to_float(sample[rank]);
    return std::isfinite(v) ? std::clamp(v, 0.f, 1.f) : 0.5f;
}

std::unique_ptr<DepthLink> DepthLink::open(int fd, std::string& why) {
    struct stat st{};
    const std::size_t need = depth_shm_bytes(kDepthLinkShm);
    if (fd < 0 || fstat(fd, &st) != 0 || std::size_t(st.st_size) != need) { why = "depth memory has the wrong size"; return nullptr; }
    void* base = mmap(nullptr, need, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    if (base == MAP_FAILED) { why = std::string("depth memory: ") + std::strerror(errno); return nullptr; }
    auto shm = DepthShm::attach(base, need);  // a renderer before this one in the same run
    if (!shm) {
        std::memset(base, 0, sizeof(DepthShmHeader));
        shm = DepthShm::create(base, need, kDepthLinkShm);
    }
    if (!shm) { munmap(base, need); why = "depth memory layout refused"; return nullptr; }
    std::unique_ptr<DepthLink> out(new DepthLink());
    out->base_ = base; out->bytes_ = need; out->shm_ = shm;
    out->map_.reserve(std::size_t(kDepthLinkShm.out_w) * std::size_t(kDepthLinkShm.out_h) * 4);
    return out;
}

DepthLink::~DepthLink() { if (base_) munmap(base_, bytes_); }

void DepthLink::restart() {
    for (auto& c : channels_) { c.fed_frames = c.seen = c.next_ns = 0; c.have_anchor = false; }
}

void DepthLink::stop() {
    for (std::uint32_t i = 0; i < kMaxDesktops; ++i) shm_->set_control(i, 0);
}

void DepthLink::tick(GlCompositor& gl, const Tick& t) {
    const std::size_t n = std::min({t.wanted.size(), t.frames.size(), std::size_t(kMaxDesktops)});
    for (std::size_t i = 0; i < kMaxDesktops; ++i) {
        const bool on = i < n && t.wanted[i];
        if (on != channels_[i].on) { channels_[i].on = on; channels_[i].seen = 0; channels_[i].have_anchor = false; }
        shm_->set_control(std::uint32_t(i), on ? kDepthEnabled | (int(i) == t.focused ? kDepthFocused : 0u) : 0u);
    }
    alive_ = shm_->engine_alive(t.now_ns);
    if (!alive_) return;  // no heartbeat: nothing is fed, nothing is vouched for, and those desktops ease flat
    // Finished shrinks to the engine: a copy of half a megabyte, no wait.
    for (std::size_t i = 0; i < n; ++i) {
        auto& c = channels_[i];
        if (!c.feed || !c.feed->busy() || !c.feed->collect(c.rgba)) continue;
        DepthFrame f{kDepthLinkShm.in_w, kDepthLinkShm.in_h, ++c.seq, c.source_ns, 0, c.bgr ? DepthFormat::Bgra8 : DepthFormat::Rgba8, 0, c.rgba};
        if (c.on) shm_->publish(DepthShm::Side::Input, std::uint32_t(i), f);
    }
    // New shrinks: the next desktops round from the last one with a new picture and their turn due.
    int shrinks = 0;
    if (!feed_failed_) for (std::size_t k = 0, from = next_feed_; k < n && shrinks < kDepthPerTick; ++k) {
        const std::size_t i = (from + k) % n;
        auto& c = channels_[i];
        if (!c.on || t.frames[i] == c.fed_frames || t.now_ns < c.next_ns || (c.feed && c.feed->busy())) continue;
        const auto texture = gl.source_texture(i);
        if (!texture.id) continue;
        if (!c.feed) {
            c.feed = std::make_unique<DepthFeed>();
            if (!c.feed->init(kDepthLinkShm.in_w, kDepthLinkShm.in_h)) { c.feed.reset(); feed_failed_ = true; break; }
        }
        if (!c.feed->begin_texture(texture.id)) continue;
        c.bgr = texture.bgr; c.fed_frames = t.frames[i]; c.source_ns = t.now_ns;
        c.next_ns = t.now_ns + (int(i) == t.focused ? kDepthFeedFocusedNs : kDepthFeedOtherNs);
        next_feed_ = (i + 1) % n;
        ++feeds_; ++shrinks;
    }
    if (!t.drawing) return;
    // New maps, and the rest vouched for: the engine is alive, so a desktop with nothing new has simply not changed.
    int taken = 0;
    for (std::size_t k = 0, from = next_map_; k < n; ++k) {
        const std::size_t i = (from + k) % n;
        auto& c = channels_[i];
        if (!c.on) continue;
        if (taken < kDepthPerTick) if (const auto f = shm_->fetch(DepthShm::Side::Output, std::uint32_t(i), map_, c.seen);
                        f && f->format == DepthFormat::R16F && f->w >= 1 && f->h >= kDepthMinHeight) {
            const std::span<const std::uint16_t> half(reinterpret_cast<const std::uint16_t*>(f->pixels.data()), f->pixels.size() / 2);
            const float now = f->flags & kDepthFlat ? 0.5f : depth_anchor(half, f->w, f->h, t.convergence);
            const double dt_ms = c.map_ns ? double(t.now_ns - c.map_ns) / 1e6 : 0.0;
            const bool snap = !c.have_anchor || (f->flags & kDepthSceneCut) || t.range_ms <= 0.f;
            c.anchor = snap ? now : c.anchor + (now - c.anchor) * float(1.0 - std::exp(-dt_ms / double(t.range_ms)));
            c.have_anchor = true; c.map_ns = t.now_ns;
            DepthInput in; in.w = f->w; in.h = f->h; in.source_seq = f->source_seq; in.half = half.data(); in.convergence = c.anchor;
            if (gl.set_depth(i, in)) {
                c.seen = f->source_seq; ++maps_;
                if (f->done_ns >= f->source_ns) lag_us_ = (f->done_ns - f->source_ns) / 1000;
                if (t.now_ns >= f->source_ns) ++age_ms_[std::min<std::uint64_t>((t.now_ns - f->source_ns) / 1'000'000, age_ms_.size() - 1)];
            }
            ++taken;
            next_map_ = (i + 1) % n;
        }
        gl.keep_depth(i);
    }
}

double DepthLink::age_percentile(double q) const {
    std::uint64_t total = 0, seen = 0;
    for (const auto n : age_ms_) total += n;
    for (std::size_t i = 0; i < age_ms_.size(); ++i) {
        seen += age_ms_[i];
        if (seen && double(seen) >= q * double(total)) return double(i);
    }
    return 0;
}

std::string DepthLink::report() const {
    return "{\"alive\":" + std::string(alive_ ? "true" : "false") + ",\"feeds\":" + std::to_string(feeds_) + ",\"maps\":" + std::to_string(maps_) +
           ",\"lag_us\":" + std::to_string(lag_us_) + ",\"age_p50_ms\":" + std::to_string(int(age_percentile(0.5))) +
           ",\"age_p99_ms\":" + std::to_string(int(age_percentile(0.99))) + ",\"feed_failed\":" + (feed_failed_ ? "true" : "false") + "}";
}
}
