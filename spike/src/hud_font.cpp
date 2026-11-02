// System font for the status pop-ups: fontconfig finds the installed file, FreeType rasterises.
// Reads local font files only; nothing is downloaded. Any failure -> nullptr and the caller
// keeps the built-in pixel font (the reason is reported).
#include "xr/hud.hpp"
#include <fontconfig/fontconfig.h>
#include <ft2build.h>
#include FT_FREETYPE_H
#include <algorithm>
#include <map>
#include <tuple>

namespace xr {
namespace {
// Minimal UTF-8 decoder (invalid bytes -> U+FFFD); pop-up strings are short and trusted.
std::vector<char32_t> decode(std::string_view s) {
    std::vector<char32_t> out;
    for (std::size_t i = 0; i < s.size();) {
        const auto c = static_cast<unsigned char>(s[i]);
        int n = c < 0x80 ? 0 : (c >> 5) == 6 ? 1 : (c >> 4) == 14 ? 2 : (c >> 3) == 30 ? 3 : -1;
        if (n < 0 || i + std::size_t(n) >= s.size() + (n ? 0 : 1)) { out.push_back(0xFFFD); ++i; continue; }
        char32_t cp = n == 0 ? c : n == 1 ? (c & 0x1F) : n == 2 ? (c & 0x0F) : (c & 0x07);
        bool ok = true;
        for (int k = 1; k <= n; ++k) {
            const auto d = static_cast<unsigned char>(s[i + std::size_t(k)]);
            if ((d >> 6) != 2) { ok = false; break; }
            cp = (cp << 6) | (d & 0x3F);
        }
        out.push_back(ok ? cp : 0xFFFD); i += ok ? std::size_t(n) + 1 : 1;
    }
    return out;
}
std::string match(FcConfig* config, const std::string& family, bool bold, std::string& found) {
    FcPattern* pattern = FcPatternCreate();
    FcPatternAddString(pattern, FC_FAMILY, reinterpret_cast<const FcChar8*>(family.c_str()));
    FcPatternAddInteger(pattern, FC_WEIGHT, bold ? FC_WEIGHT_BOLD : FC_WEIGHT_REGULAR);
    FcPatternAddBool(pattern, FC_SCALABLE, FcTrue);
    FcConfigSubstitute(config, pattern, FcMatchPattern); FcDefaultSubstitute(pattern);
    FcResult result = FcResultNoMatch;
    FcPattern* best = FcFontMatch(config, pattern, &result);
    FcPatternDestroy(pattern);
    std::string path;
    FcChar8 *file = nullptr, *name = nullptr;
    if (best && FcPatternGetString(best, FC_FILE, 0, &file) == FcResultMatch && file) path = reinterpret_cast<const char*>(file);
    if (best && FcPatternGetString(best, FC_FAMILY, 0, &name) == FcResultMatch && name) found = reinterpret_cast<const char*>(name);
    if (best) FcPatternDestroy(best);
    return path;
}
}

struct HudFont::Impl {
    FT_Library library = nullptr;
    FT_Face regular = nullptr, bold = nullptr;
    std::string description;
    ~Impl() {
        if (bold && bold != regular) FT_Done_Face(bold);
        if (regular) FT_Done_Face(regular);
        if (library) FT_Done_FreeType(library);
    }
    FT_Face face(bool b) const { return b && bold ? bold : regular; }
    // Rasterised glyphs and advances, per (code point, pixel size, bold). Cards are re-rendered whenever a value
    // changes (a placement-panel slider drag: up to once per frame), and FreeType hinting and rasterising is most of that cost.
    // Exactly what FreeType returned is kept, so cached text draws byte for byte the same. Bounded: cleared when full.
    struct Glyph { bool ok = false; std::vector<unsigned char> coverage; unsigned width = 0, rows = 0; int left = 0, top = 0; FT_Pos advance = 0; };
    using Key = std::tuple<char32_t, std::uint32_t, bool>;
    std::map<Key, Glyph> glyphs;
    std::map<Key, std::pair<bool, FT_Pos>> advances;
    static constexpr std::size_t kMaxCached = 4096;
};

std::unique_ptr<HudFont> HudFont::load(const std::string& family, std::string& why) {
    std::unique_ptr<HudFont> out(new HudFont());
    out->impl_ = std::make_unique<Impl>();
    auto& s = *out->impl_;
    FcConfig* config = FcInitLoadConfigAndFonts();
    if (!config) { why = "fontconfig unavailable"; return nullptr; }
    std::string found, found_bold;
    const auto path = match(config, family, false, found), bold_path = match(config, family, true, found_bold);
    FcConfigDestroy(config);
    // fontconfig always proposes something; only accept the family that was asked for.
    if (path.empty() || found != family) { why = "font not installed: " + family; return nullptr; }
    if (FT_Init_FreeType(&s.library)) { why = "freetype init failed"; return nullptr; }
    if (FT_New_Face(s.library, path.c_str(), 0, &s.regular) || !FT_IS_SCALABLE(s.regular)) { why = "font load failed: " + path; return nullptr; }
    if (!bold_path.empty() && found_bold == family && bold_path != path && FT_New_Face(s.library, bold_path.c_str(), 0, &s.bold)) s.bold = nullptr;
    s.description = family + (s.bold ? " (regular + bold)" : " (regular)");
    return out;
}

HudFont::~HudFont() = default;
const std::string& HudFont::description() const { return impl_->description; }

HudFont::Metrics HudFont::metrics(std::uint32_t px, bool bold) const {
    FT_Face f = impl_->face(bold);
    if (FT_Set_Pixel_Sizes(f, 0, px)) return {};
    const auto asc = std::uint32_t(std::max<FT_Pos>(f->size->metrics.ascender, 0) >> 6);
    const auto desc = std::uint32_t(std::max<FT_Pos>(-f->size->metrics.descender, 0) >> 6);
    return {asc, desc};
}

std::uint32_t HudFont::measure(std::string_view text, std::uint32_t px, bool bold) const {
    FT_Face f = impl_->face(bold);
    auto& cache = impl_->advances;
    if (cache.size() > Impl::kMaxCached) cache.clear();
    bool sized = false;
    FT_Pos x = 0;
    for (char32_t cp : decode(text)) {
        auto [it, fresh] = cache.try_emplace({cp, px, bold});
        if (fresh) {
            if (!sized) { if (FT_Set_Pixel_Sizes(f, 0, px)) { cache.erase(it); return 0; } sized = true; }
            it->second = {!FT_Load_Char(f, cp, FT_LOAD_DEFAULT | FT_LOAD_TARGET_LIGHT), f->glyph->advance.x};
        }
        if (it->second.first) x += it->second.second;
    }
    return std::uint32_t((x + 63) >> 6);
}

void HudFont::draw(std::string_view text, std::uint32_t px, bool bold, int left, int baseline, const Plot& plot) const {
    FT_Face f = impl_->face(bold);
    auto& cache = impl_->glyphs;
    if (cache.size() > Impl::kMaxCached) cache.clear();
    bool sized = false;
    FT_Pos pen = FT_Pos(left) << 6;
    for (char32_t cp : decode(text)) {
        auto [it, fresh] = cache.try_emplace({cp, px, bold});
        auto& g = it->second;
        if (fresh) {
            if (!sized) { if (FT_Set_Pixel_Sizes(f, 0, px)) { cache.erase(it); return; } sized = true; }
            if (!FT_Load_Char(f, cp, FT_LOAD_RENDER | FT_LOAD_TARGET_LIGHT)) {
                const FT_Bitmap& bm = f->glyph->bitmap;
                g.ok = true; g.advance = f->glyph->advance.x; g.left = f->glyph->bitmap_left; g.top = f->glyph->bitmap_top;
                if (bm.pixel_mode == FT_PIXEL_MODE_GRAY) {
                    g.width = bm.width; g.rows = bm.rows;
                    g.coverage.resize(std::size_t(bm.width) * bm.rows);
                    for (unsigned row = 0; row < bm.rows; ++row)
                        for (unsigned col = 0; col < bm.width; ++col) g.coverage[std::size_t(row) * bm.width + col] = bm.buffer[std::ptrdiff_t(row) * bm.pitch + col];
                }
            }
        }
        if (!g.ok) continue;
        const int x0 = int(pen >> 6) + g.left, y0 = baseline - g.top;
        for (unsigned row = 0; row < g.rows; ++row)
            for (unsigned col = 0; col < g.width; ++col) {
                const unsigned char cov = g.coverage[std::size_t(row) * g.width + col];
                if (cov) plot(x0 + int(col), y0 + int(row), float(cov) / 255.f);
            }
        pen += g.advance;
    }
}
}
