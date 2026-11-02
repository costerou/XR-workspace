#include "xr/capture.hpp"
#include <algorithm>
#include <regex>

namespace xr {
std::optional<std::vector<std::string>> parse_capture_list(const std::string& csv, const std::string& own_output) {
    static const std::regex name("XR-[1-9][0-9]?");
    std::vector<std::string> names;
    std::size_t start = 0;
    while (start <= csv.size()) {
        const auto end = csv.find(',', start);
        const auto item = csv.substr(start, end == std::string::npos ? std::string::npos : end - start);
        if (!std::regex_match(item, name) || std::stoul(item.substr(3)) > kMaxDesktops || item == own_output ||
            std::find(names.begin(), names.end(), item) != names.end()) return std::nullopt;
        names.push_back(item);
        if (end == std::string::npos) break;
        start = end + 1;
    }
    if (names.empty() || names.size() > kMaxDesktops) return std::nullopt;
    // Numeric, not lexical, so tile order always follows XR-n order.
    std::sort(names.begin(), names.end(), [](const std::string& a, const std::string& b) { return std::stoul(a.substr(3)) < std::stoul(b.substr(3)); });
    return names;
}

std::optional<ShmPlan> plan_capture_buffer(std::uint32_t width, std::uint32_t height, const std::vector<std::uint32_t>& formats) {
    if (!width || !height || width > 8192 || height > 8192) return std::nullopt;
    for (auto format : {kShmXrgb8888, kShmArgb8888})
        if (std::find(formats.begin(), formats.end(), format) != formats.end())
            return ShmPlan{width, height, width * 4, format, std::size_t(width) * 4 * height};
    return std::nullopt;
}

std::vector<Rect> side_by_side(std::span<const Size> sources, std::uint32_t dst_width, std::uint32_t dst_height) {
    if (sources.empty() || !dst_width || !dst_height ||
        std::any_of(sources.begin(), sources.end(), [](Size z) { return !(z.w > 0 && z.h > 0); })) return {};
    auto width = [](Size z, std::uint32_t h) { return static_cast<std::uint32_t>(double(h) * z.w / z.h); };
    auto total = [&](std::uint32_t h) { std::uint64_t t = 0; for (auto z : sources) t += width(z, h); return t; };
    double aspects = 0; for (auto z : sources) aspects += double(z.w) / z.h;
    // Start just above the exact fit and step down until the floored widths fit dst.
    std::uint32_t h = static_cast<std::uint32_t>(std::min<double>(dst_height, double(dst_width) / aspects + 1));
    while (h && total(h) > dst_width) --h;
    const std::uint32_t n = static_cast<std::uint32_t>(sources.size()), gap = static_cast<std::uint32_t>((dst_width - total(h)) / n);
    std::vector<Rect> tiles;
    std::uint32_t x = gap / 2;
    for (auto z : sources) { const auto w = width(z, h); tiles.push_back({x, (dst_height - h) / 2, w, h}); x += w + gap; }
    return tiles;
}

void blit_nearest(const std::uint32_t* src, std::uint32_t src_width, std::uint32_t src_height, std::uint32_t src_stride_px,
                  std::uint32_t* dst, std::uint32_t dst_width, std::uint32_t dst_height, std::uint32_t dst_stride_px, Rect r) {
    if (!src || !dst || !src_width || !src_height || !r.width || !r.height) return;
    const std::uint32_t x_end = std::min<std::uint64_t>(std::uint64_t(r.x) + r.width, dst_width);
    const std::uint32_t y_end = std::min<std::uint64_t>(std::uint64_t(r.y) + r.height, dst_height);
    for (std::uint32_t y = r.y; y < y_end; ++y) {
        const std::uint32_t sy = static_cast<std::uint32_t>(std::uint64_t(y - r.y) * src_height / r.height);
        const std::uint32_t* row = src + std::size_t(sy) * src_stride_px;
        std::uint32_t* out = dst + std::size_t(y) * dst_stride_px;
        for (std::uint32_t x = r.x; x < x_end; ++x)
            out[x] = row[std::uint64_t(x - r.x) * src_width / r.width] | 0xff000000u;
    }
}
}

namespace xr {
ClipQuad clip_quad(Rect r, std::uint32_t width, std::uint32_t height) {
    if (!width || !height) return {};
    auto x = [&](std::uint64_t px) { return static_cast<float>(2.0 * double(px) / width - 1.0); };
    auto y = [&](std::uint64_t px) { return static_cast<float>(1.0 - 2.0 * double(px) / height); };
    return {x(r.x), y(r.y), x(std::uint64_t(r.x) + r.width), y(std::uint64_t(r.y) + r.height)};
}
}
