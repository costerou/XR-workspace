#pragma once
// USB latency of the real glasses (rust-rebuild-plan D1): what `xr-luma-driver --latency-log FILE` writes, and how
// luma_latency_report reads it.
//
// Every IMU packet carries the time the glasses stamped it (device time, 100 us ticks). The transport stamps the time it
// reaped the finished URB (host time, CLOCK_MONOTONIC). The two clocks share no zero and run at slightly different rates, so
// absolute latency is not measurable here. What is measurable is how much later than the fastest packet each packet arrives:
//
//   d = host_ns - device_ns, per packet.
//   Within a window, the line L below every (device_ns, d) point that is tightest on average is the lower convex hull edge that
//   spans the mean device time. Its slope is the clock drift; its points are the packets that came through fastest.
//   excess = d - L(device_ns) >= 0: the delay a packet saw beyond the best the link did in that window.
//
// The excess includes the device's own 100 us stamp rounding, so its floor is about 100 us of noise, not zero.
//
// File: 16-byte header ("LUMALAT1", u32 record size, u32 0), then 24-byte little-endian records. A record with the opened flag
// marks a new link: the packet after it may be one the previous reader left in the device, so the report drops it.
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <optional>
#include <span>
#include <string>
#include <vector>

namespace luma::latency {

inline constexpr std::array<char, 8> kMagic{'L', 'U', 'M', 'A', 'L', 'A', 'T', '1'};
inline constexpr std::uint32_t kOpened = 1;  // flags: the link was just opened; host_ns and device_ns are 0

struct Record {
    std::uint64_t host_ns = 0;    // CLOCK_MONOTONIC when the URB completed (a replay: the capture's time)
    std::uint64_t device_ns = 0;  // the glasses' own stamp, in ns
    std::uint32_t seq = 0;        // the 16-bit wire sequence number
    std::uint32_t flags = 0;
};
static_assert(sizeof(Record) == 24);

struct Header {
    std::array<char, 8> magic = kMagic;
    std::uint32_t record_size = sizeof(Record);
    std::uint32_t reserved = 0;
};
static_assert(sizeof(Header) == 16);

inline std::optional<std::vector<Record>> load(const std::string& path, std::string& why) {
    std::ifstream in(path, std::ios::binary);
    if (!in) { why = "cannot read " + path; return std::nullopt; }
    Header h{};
    if (!in.read(reinterpret_cast<char*>(&h), sizeof h) || h.magic != kMagic || h.record_size != sizeof(Record)) {
        why = path + " is not a latency log";
        return std::nullopt;
    }
    std::vector<Record> records;
    Record r{};
    while (in.read(reinterpret_cast<char*>(&r), sizeof r)) records.push_back(r);
    if (in.gcount() != 0) { why = path + " ends inside a record"; return std::nullopt; }
    return records;
}

inline bool save(const std::string& path, std::span<const Record> records) {  // for tests; the driver streams its own
    std::ofstream out(path, std::ios::binary | std::ios::trunc);
    const Header h{};
    out.write(reinterpret_cast<const char*>(&h), sizeof h);
    out.write(reinterpret_cast<const char*>(records.data()), std::streamsize(records.size_bytes()));
    return bool(out.flush());
}

struct Spread {  // ns
    double p50 = 0, p99 = 0, p999 = 0, max = 0;
};

struct Report {
    std::uint64_t records = 0;    // data records in the file
    std::uint64_t packets = 0;    // kept: what the percentiles are over
    std::uint64_t opens = 0;
    std::uint64_t leftovers = 0;  // first packet after an open, dropped
    std::uint64_t repeats = 0;    // device time not ahead of the last packet (the Ultra resends its first ~60), dropped
    std::uint64_t lost = 0;       // sequence numbers skipped
    std::uint64_t segments = 0;   // runs with no open, restart or gap over 1 s inside
    std::uint64_t windows = 0;
    double duration_s = 0;        // host time covered by the segments
    double rate_hz = 0;
    double drift_ppm = 0;         // median over windows: host clock rate minus device clock rate
    Spread excess;                // beyond the fastest packet in its window
    Spread interarrival;          // host time between kept packets
    std::uint64_t over_1ms = 0, over_2ms = 0, over_8ms = 0;  // excess counts
};

namespace detail {
inline double percentile(std::vector<double>& sorted, double p) {  // nearest rank; sorted ascending
    if (sorted.empty()) return 0;
    const std::size_t rank = std::size_t(std::ceil(p * double(sorted.size())));
    return sorted[std::min(sorted.size() - 1, rank ? rank - 1 : 0)];
}
inline Spread spread(std::vector<double> v) {
    std::sort(v.begin(), v.end());
    Spread s;
    s.p50 = percentile(v, 0.5);
    s.p99 = percentile(v, 0.99);
    s.p999 = percentile(v, 0.999);
    s.max = v.empty() ? 0 : v.back();
    return s;
}
__extension__ typedef __int128 Wide;  // exact cross products: x and y spans multiply past 64 bits
struct Point { std::int64_t x, y; };  // x: device ns from the window's first packet; y: host - device, from the first packet's

// Excess of every point over the tightest line below them all, appended to out. Points have increasing x. Returns the slope.
inline double window_excess(const std::vector<Point>& p, std::vector<double>& out) {
    if (p.size() < 2) { out.insert(out.end(), p.size(), 0.0); return 0; }
    const auto cross = [](const Point& o, const Point& a, const Point& b) {
        return Wide(a.x - o.x) * (b.y - o.y) - Wide(a.y - o.y) * (b.x - o.x);
    };
    std::vector<Point> hull;  // lower hull, monotone chain
    for (const Point& q : p) {
        while (hull.size() >= 2 && cross(hull[hull.size() - 2], hull.back(), q) <= 0) hull.pop_back();
        hull.push_back(q);
    }
    long double mean = 0;
    for (const Point& q : p) mean += q.x;
    mean /= p.size();
    std::size_t edge = 0;  // the edge whose x range holds the mean: no line below all points has a higher mean
    while (edge + 2 < hull.size() && hull[edge + 1].x < mean) ++edge;
    const Point a = hull[edge], b = hull[edge + 1];
    const long double slope = (long double)(b.y - a.y) / (long double)(b.x - a.x);
    for (const Point& q : p) out.push_back(std::max(0.0, double((long double)(q.y - a.y) - slope * (q.x - a.x))));
    return double(slope);
}
}  // namespace detail

inline Report analyze(std::span<const Record> records, double window_s = 10.0) {
    constexpr std::uint64_t kGapNs = 1'000'000'000;
    Report rep;
    std::vector<double> excess, gaps, drifts;
    std::vector<Record> seg;
    const auto close_segment = [&] {
        if (seg.empty()) return;
        ++rep.segments;
        rep.duration_s += double(seg.back().host_ns - seg.front().host_ns) / 1e9;
        const std::uint64_t window_ns = std::uint64_t(std::max(window_s, 0.001) * 1e9);
        std::size_t begin = 0;
        while (begin < seg.size()) {
            std::size_t end = begin;
            while (end < seg.size() && seg[end].host_ns - seg[begin].host_ns < window_ns) ++end;
            // A short tail fits its line poorly: give it to this window instead.
            if (end < seg.size() && seg.back().host_ns - seg[end].host_ns < window_ns / 2) end = seg.size();
            std::vector<detail::Point> pts;
            pts.reserve(end - begin);
            const Record& o = seg[begin];
            const std::int64_t d0 = std::int64_t(o.host_ns - o.device_ns);
            for (std::size_t i = begin; i < end; ++i)
                pts.push_back({std::int64_t(seg[i].device_ns - o.device_ns), std::int64_t(seg[i].host_ns - seg[i].device_ns) - d0});
            const double slope = detail::window_excess(pts, excess);
            if (pts.size() >= 2) { drifts.push_back(slope * 1e6); ++rep.windows; }
            begin = end;
        }
        seg.clear();
    };
    bool leftover = false;
    for (const Record& r : records) {
        if (r.flags & kOpened) { close_segment(); ++rep.opens; leftover = true; continue; }
        ++rep.records;
        if (leftover) { leftover = false; ++rep.leftovers; continue; }
        if (!seg.empty()) {
            const Record& last = seg.back();
            if (r.device_ns <= last.device_ns && last.device_ns - r.device_ns < kGapNs) { ++rep.repeats; continue; }
            if (r.device_ns <= last.device_ns || r.device_ns - last.device_ns > kGapNs || r.host_ns < last.host_ns ||
                r.host_ns - last.host_ns > kGapNs) {
                close_segment();  // the device restarted, or the link was silent: the old line says nothing here
            } else {
                rep.lost += (std::uint16_t(r.seq - last.seq) + 0xFFFFu) & 0xFFFFu;  // a step of 1 adds 0
                gaps.push_back(double(r.host_ns - last.host_ns));
            }
        }
        seg.push_back(r);
        ++rep.packets;
    }
    close_segment();
    for (double e : excess) {
        rep.over_1ms += e > 1e6;
        rep.over_2ms += e > 2e6;
        rep.over_8ms += e > 8e6;
    }
    rep.excess = detail::spread(std::move(excess));
    rep.interarrival = detail::spread(std::move(gaps));
    if (!drifts.empty()) {
        std::sort(drifts.begin(), drifts.end());
        rep.drift_ppm = drifts[drifts.size() / 2];
    }
    rep.rate_hz = rep.duration_s > 0 ? double(rep.packets) / rep.duration_s : 0;
    return rep;
}

// The tracking_bench layout: one metric per line, then one json line for scripts.
inline std::string format(const Report& r, const std::string& label) {
    std::string out;
    char line[512];
    const auto row = [&](const char* name, const Spread& s) {
        std::snprintf(line, sizeof line, "usb   %-34s p50 %8.0f ns  p99 %8.0f ns  p99.9 %8.0f ns  max %9.0f ns\n", name, s.p50, s.p99,
                      s.p999, s.max);
        out += line;
    };
    std::snprintf(line, sizeof line, "usb   %-34s %s\n", "label", label.c_str());
    out += line;
    row("excess over fastest packet", r.excess);
    row("host inter-arrival", r.interarrival);
    std::snprintf(line, sizeof line,
                  "usb   %-34s packets %llu  %.1f s  %.1f Hz  lost %llu  repeats %llu  leftovers %llu  opens %llu  segments %llu\n",
                  "stream", (unsigned long long)r.packets, r.duration_s, r.rate_hz, (unsigned long long)r.lost,
                  (unsigned long long)r.repeats, (unsigned long long)r.leftovers, (unsigned long long)r.opens,
                  (unsigned long long)r.segments);
    out += line;
    std::snprintf(line, sizeof line, "usb   %-34s >1ms %llu  >2ms %llu  >8ms %llu  drift %.1f ppm over %llu windows\n", "excess tail",
                  (unsigned long long)r.over_1ms, (unsigned long long)r.over_2ms, (unsigned long long)r.over_8ms, r.drift_ppm,
                  (unsigned long long)r.windows);
    out += line;
    std::string safe;
    for (char c : label) if (c != '"' && c != '\\' && (unsigned char)c >= 0x20) safe += c;
    std::snprintf(line, sizeof line,
                  "json {\"label\":\"%s\",\"packets\":%llu,\"duration_s\":%.3f,\"rate_hz\":%.2f,\"lost\":%llu,\"repeats\":%llu,"
                  "\"leftovers\":%llu,\"opens\":%llu,\"segments\":%llu,\"excess_p50_us\":%.1f,\"excess_p99_us\":%.1f,"
                  "\"excess_p999_us\":%.1f,\"excess_max_us\":%.1f,\"gap_p50_us\":%.1f,\"gap_p99_us\":%.1f,\"gap_p999_us\":%.1f,"
                  "\"gap_max_us\":%.1f,\"over_1ms\":%llu,\"over_2ms\":%llu,\"over_8ms\":%llu,\"drift_ppm\":%.2f}\n",
                  safe.c_str(), (unsigned long long)r.packets, r.duration_s, r.rate_hz, (unsigned long long)r.lost,
                  (unsigned long long)r.repeats, (unsigned long long)r.leftovers, (unsigned long long)r.opens,
                  (unsigned long long)r.segments, r.excess.p50 / 1e3, r.excess.p99 / 1e3, r.excess.p999 / 1e3, r.excess.max / 1e3,
                  r.interarrival.p50 / 1e3, r.interarrival.p99 / 1e3, r.interarrival.p999 / 1e3, r.interarrival.max / 1e3,
                  (unsigned long long)r.over_1ms, (unsigned long long)r.over_2ms, (unsigned long long)r.over_8ms, r.drift_ppm);
    out += line;
    return out;
}

}  // namespace luma::latency
