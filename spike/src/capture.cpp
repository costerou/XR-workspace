#include "xr/capture.hpp"
#include <algorithm>
#include <regex>

namespace xr {
std::optional<std::vector<std::string>> parse_capture_list(const std::string& csv, const std::string& own_output) {
    static const std::regex name("XR-[1-3]");
    std::vector<std::string> names;
    std::size_t start = 0;
    while (start <= csv.size()) {
        const auto end = csv.find(',', start);
        const auto item = csv.substr(start, end == std::string::npos ? std::string::npos : end - start);
        if (!std::regex_match(item, name) || item == own_output ||
            std::find(names.begin(), names.end(), item) != names.end()) return std::nullopt;
        names.push_back(item);
        if (end == std::string::npos) break;
        start = end + 1;
    }
    if (names.empty() || names.size() > 3) return std::nullopt;
    std::sort(names.begin(), names.end());
    return names;
}

std::optional<ShmPlan> plan_capture_buffer(std::uint32_t width, std::uint32_t height, const std::vector<std::uint32_t>& formats) {
    if (!width || !height || width > 8192 || height > 8192) return std::nullopt;
    for (auto format : {kShmXrgb8888, kShmArgb8888})
        if (std::find(formats.begin(), formats.end(), format) != formats.end())
            return ShmPlan{width, height, width * 4, format, std::size_t(width) * 4 * height};
    return std::nullopt;
}

std::vector<Rect> side_by_side(std::size_t count, std::uint32_t dst_width, std::uint32_t dst_height,
                               std::uint32_t src_width, std::uint32_t src_height) {
    if (!count || !dst_width || !dst_height || !src_width || !src_height) return {};
    const std::uint32_t slot = dst_width / static_cast<std::uint32_t>(count);
    std::uint32_t w = slot, h = static_cast<std::uint32_t>(std::uint64_t(slot) * src_height / src_width);
    if (h > dst_height) { h = dst_height; w = static_cast<std::uint32_t>(std::uint64_t(dst_height) * src_width / src_height); }
    std::vector<Rect> tiles;
    for (std::size_t i = 0; i < count; ++i)
        tiles.push_back({static_cast<std::uint32_t>(i) * slot + (slot - w) / 2, (dst_height - h) / 2, w, h});
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
