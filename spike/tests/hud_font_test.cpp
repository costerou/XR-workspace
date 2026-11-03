// HudFont (src/hud_font.cpp) caches what FreeType returns; the cache must never change what is drawn or measured.
//  - Cold vs warm: each query drawn first by a freshly loaded font equals the same query replayed from a long-lived
//    font's caches, including after a flood of distinct glyphs past the cache bound and with metrics() calls (which
//    used to set the face size) interleaved at other sizes.
//  - UTF-8: any byte string draws and measures exactly like its decoding by the reference decoder below (the original
//    implementation: invalid, truncated or non-continuation bytes -> U+FFFD, one byte at a time; overlong forms,
//    surrogates and values up to 0x1FFFFF are passed through), re-encoded as plain UTF-8.
// Exit 77 (skipped) if the system font is not installed.
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <random>
#include <string>
#include <string_view>
#include <vector>
#include "xr/hud.hpp"

using namespace xr;
namespace {
int failures = 0;
#define CHECK(c) do { if (!(c)) { std::fprintf(stderr, "%s:%d: CHECK(%s)\n", __FILE__, __LINE__, #c); ++failures; } } while (0)

struct Plotted { int x, y; float coverage; bool operator==(const Plotted&) const = default; };
struct Result {
    std::vector<Plotted> plots; std::uint32_t width = 0; HudFont::Metrics metrics;
    bool operator==(const Result& o) const { return plots == o.plots && width == o.width && metrics.ascent == o.metrics.ascent && metrics.descent == o.metrics.descent; }
};
Result query(const HudFont& font, std::string_view text, std::uint32_t px, bool bold) {
    Result r;
    r.metrics = font.metrics(px, bold);
    r.width = font.measure(text, px, bold);
    font.draw(text, px, bold, 5, 50, [&](int x, int y, float c) { r.plots.push_back({x, y, c}); });
    return r;
}

std::vector<char32_t> reference_decode(std::string_view s) {
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
std::string encode(const std::vector<char32_t>& cps) {  // shortest form; the decoder accepts every value it produced
    std::string out;
    for (char32_t cp : cps) {
        if (cp < 0x80) out += char(cp);
        else if (cp < 0x800) { out += char(0xC0 | (cp >> 6)); out += char(0x80 | (cp & 0x3F)); }
        else if (cp < 0x10000) { out += char(0xE0 | (cp >> 12)); out += char(0x80 | ((cp >> 6) & 0x3F)); out += char(0x80 | (cp & 0x3F)); }
        else { out += char(0xF0 | (cp >> 18)); out += char(0x80 | ((cp >> 12) & 0x3F)); out += char(0x80 | ((cp >> 6) & 0x3F)); out += char(0x80 | (cp & 0x3F)); }
    }
    return out;
}
}  // namespace

int main() {
    std::string why;
    auto font = HudFont::load(kHudFontFamily, why);
    if (!font) { std::printf("SKIP: %s\n", why.c_str()); return 77; }
    struct Query { std::string text; std::uint32_t px; bool bold; };
    const std::vector<Query> queries{
        {"XR-2", 24, true}, {"Placement", 24, false}, {"Curvature  +12\xc2\xb0", 18, false},
        {"\xe2\x86\x91\xe2\x86\x93 field  \xe2\x86\x90\xe2\x86\x92 adjust", 18, false}, {"\xef\x80\x81 nerd \xe4\xb8\xad", 30, true},
        {"bad \xff\xfe trunc \xe2\x86", 24, false}, {"over \xc0\xaf \xf7\xbf\xbf\xbf \xed\xa0\x80", 12, true}, {"", 24, false}, {"Mm", 160, false},
        {"XR-2", 24, false}, {"Placement", 24, true}, {"Mm", 160, true}};  // the same glyphs and sizes in the other face
    // Cold: a fresh font per query (nothing cached when it is drawn).
    std::vector<Result> cold;
    for (const auto& q : queries) {
        auto fresh = HudFont::load(kHudFontFamily, why);
        CHECK(fresh != nullptr);
        if (!fresh) return 1;
        cold.push_back(query(*fresh, q.text, q.px, q.bold));
    }
    CHECK(!cold[0].plots.empty() && cold[0].width > 0);
    // Coverage is FreeType's 8-bit grey level over 255, and only covered pixels are plotted.
    for (const auto& r : cold)
        for (const auto& p : r.plots) {
            const long k = std::lround(p.coverage * 255.f);
            if (!(k >= 1 && k <= 255 && p.coverage == float(k) / 255.f)) { std::fprintf(stderr, "coverage %.9g is not k/255\n", double(p.coverage)); ++failures; break; }
        }
    // Warm: one font, queries repeated with other sizes in between and a flood of glyphs past the cache bound.
    for (int round = 0; round < 3; ++round) {
        for (std::size_t i = 0; i < queries.size(); ++i) {
            font->metrics(queries[i].px + 7, !queries[i].bold);  // a different size set on the face just before
            const auto warm = query(*font, queries[i].text, queries[i].px, queries[i].bold);
            if (!(warm == cold[i])) { std::fprintf(stderr, "round %d query %zu (\"%s\" %u%s) differs from a cold font\n", round, i,
                                                   queries[i].text.c_str(), queries[i].px, queries[i].bold ? " bold" : ""); ++failures; }
        }
        std::string all; for (char c = 0x21; c < 0x7f; ++c) all += c;
        for (std::uint32_t px = 8; px < 60; ++px) { font->measure(all, px, px & 1); font->draw(all, px, px & 1, 0, 0, [](int, int, float) {}); font->metrics(px, false); }
    }
    // UTF-8: random byte strings, biased toward lead and continuation bytes.
    std::mt19937 rng(20261102);
    const unsigned char pool[] = {'a', 'Z', ' ', '%', 0x7f, 0x80, 0x8f, 0xbf, 0xc0, 0xc2, 0xdf, 0xe0, 0xe2, 0xed, 0xef, 0xf0, 0xf4, 0xf7, 0xf8, 0xfe, 0xff};
    for (int t = 0; t < 400; ++t) {
        std::string raw;
        const int n = int(rng() % 12);
        for (int k = 0; k < n; ++k) raw += char(pool[rng() % sizeof pool]);
        const std::string clean = encode(reference_decode(raw));
        const auto a = query(*font, raw, 20, t & 1), b = query(*font, clean, 20, t & 1);
        if (!(a == b)) { std::fprintf(stderr, "utf-8 case %d (%zu bytes) decodes differently from the reference\n", t, raw.size()); ++failures; }
    }
    if (failures) { std::fprintf(stderr, "%d failures\n", failures); return 1; }
    std::printf("hud font test passed: %zu queries cold vs warm x3, 400 UTF-8 cases\n", queries.size());
    return 0;
}
