#include "xr/capture.hpp"
#include <algorithm>
#include <array>
#include <cstring>
#include <regex>
#include <utility>

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

namespace {
// The dst pixels blit_nearest writes for `r`: the rect clipped to dst, empty when it draws nothing.
Rect blit_coverage(bool drawable, std::uint32_t dst_width, std::uint32_t dst_height, Rect r) {
    if (!drawable || !r.width || !r.height) return {};
    const std::uint32_t x_end = std::min<std::uint64_t>(std::uint64_t(r.x) + r.width, dst_width);
    const std::uint32_t y_end = std::min<std::uint64_t>(std::uint64_t(r.y) + r.height, dst_height);
    if (r.x >= x_end || r.y >= y_end) return {};
    return {r.x, r.y, x_end - r.x, y_end - r.y};
}
}

void blit_nearest(const std::uint32_t* src, std::uint32_t src_width, std::uint32_t src_height, std::uint32_t src_stride_px,
                  std::uint32_t* dst, std::uint32_t dst_width, std::uint32_t dst_height, std::uint32_t dst_stride_px, Rect r) {
    const Rect cover = blit_coverage(src && dst && src_width && src_height, dst_width, dst_height, r);
    if (!cover.width) return;
    const std::uint32_t n = cover.width;
    // Column map, exact floor(i * src_width / r.width) by stepping quotient and remainder (no division per pixel).
    // Grows only, so steady-state frames allocate nothing.
    static thread_local std::vector<std::uint32_t> columns;
    const bool identity = src_width == r.width;
    if (!identity) {
        if (columns.size() < n) columns.resize(n);
        const std::uint32_t step = src_width / r.width, step_rem = src_width % r.width;
        std::uint32_t q = 0;
        std::uint64_t rem = 0;  // i * src_width == q * r.width + rem, rem < r.width
        for (std::uint32_t i = 0; i < n; ++i) {
            columns[i] = q;
            q += step; rem += step_rem;
            if (rem >= r.width) { rem -= r.width; ++q; }
        }
    }
    const std::uint32_t* cols = columns.data();
    std::uint32_t last_sy = 0;
    const std::uint32_t* last_out = nullptr;
    for (std::uint32_t y = r.y; y < r.y + cover.height; ++y) {
        const std::uint32_t sy = static_cast<std::uint32_t>(std::uint64_t(y - r.y) * src_height / r.height);
        std::uint32_t* out = dst + std::size_t(y) * dst_stride_px + r.x;
        if (last_out && sy == last_sy) {  // same source row (vertical upscale): the row we just wrote
            std::memcpy(out, last_out, std::size_t(n) * sizeof(std::uint32_t));
        } else {
            const std::uint32_t* row = src + std::size_t(sy) * src_stride_px;
            if (identity) for (std::uint32_t i = 0; i < n; ++i) out[i] = row[i] | 0xff000000u;
            else for (std::uint32_t i = 0; i < n; ++i) out[i] = row[cols[i]] | 0xff000000u;
        }
        last_sy = sy; last_out = out;
    }
}

void compose_tiles(std::uint32_t* dst, std::uint32_t dst_width, std::uint32_t dst_height, std::uint32_t dst_stride_px,
                   std::span<const TileSource> sources, std::span<const Rect> tiles, std::uint32_t background) {
    if (!dst) return;
    const std::size_t count = std::min(sources.size(), tiles.size());
    constexpr std::size_t kMax = 8;
    if (count > kMax) {  // general case, never hit by the renderer (at most three outputs): fill, then blit
        for (std::uint32_t y = 0; y < dst_height; ++y) std::fill_n(dst + std::size_t(y) * dst_stride_px, dst_width, background);
        for (std::size_t i = 0; i < count; ++i)
            blit_nearest(sources[i].pixels, sources[i].width, sources[i].height, sources[i].stride_px, dst, dst_width, dst_height,
                         dst_stride_px, tiles[i]);
        return;
    }
    std::array<Rect, kMax> cover{};
    std::size_t covered = 0;
    for (std::size_t i = 0; i < count; ++i) {
        const auto& s = sources[i];
        const Rect c = blit_coverage(s.pixels && s.width && s.height, dst_width, dst_height, tiles[i]);
        if (c.width) cover[covered++] = c;
    }
    // Background only where no blit writes: per row, the gaps between the covering spans.
    std::array<std::pair<std::uint32_t, std::uint32_t>, kMax> spans{};
    for (std::uint32_t y = 0; y < dst_height; ++y) {
        std::size_t n = 0;
        for (std::size_t i = 0; i < covered; ++i) {
            if (y < cover[i].y || y - cover[i].y >= cover[i].height) continue;
            std::size_t at = n++;  // insertion by start: at most kMax spans
            for (; at > 0 && spans[at - 1].first > cover[i].x; --at) spans[at] = spans[at - 1];
            spans[at] = {cover[i].x, cover[i].x + cover[i].width};
        }
        std::uint32_t* row = dst + std::size_t(y) * dst_stride_px;
        std::uint32_t x = 0;
        for (std::size_t i = 0; i < n; ++i) {
            if (spans[i].first > x) std::fill(row + x, row + spans[i].first, background);
            x = std::max(x, spans[i].second);
        }
        if (x < dst_width) std::fill(row + x, row + dst_width, background);
    }
    for (std::size_t i = 0; i < count; ++i)
        blit_nearest(sources[i].pixels, sources[i].width, sources[i].height, sources[i].stride_px, dst, dst_width, dst_height,
                     dst_stride_px, tiles[i]);
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
