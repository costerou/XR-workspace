// Writes golden/hud_font.txt from the C++ HudFont (spike/src/hud_font.cpp) and the installed kHudFontFamily: its
// description, its metrics, and what it measures and draws for the hud_font ctest's queries and for single glyphs
// (ASCII and the non-ASCII the cards use) at several sizes in both faces. A drawing is "<plots> <FNV-1a>" over each
// plot's x and y (i32) and coverage (f32 bits), little-endian, in plot order. Then the load failures: a family that is
// not installed, and kHudFontFamily with fontconfig given an empty configuration (no font at all), with the pixel-font
// card the pop-ups fall back to. tests/hud_font.rs replays the same cases and must print the same lines; its checks
// that need the font are skipped when the first line is not "load ...".
// From the repository root, after `cmake -S spike -B $B && cmake --build $B --target xr_hud`:
//   g++ -std=c++20 -O2 -Ispike/include rust/crates/xr-renderer/golden/hud_font.cpp $B/libxr_hud.a $B/libxr_view.a \
//     $B/libxr_pose.a $B/libxr_luma_rt.a $(pkg-config --libs freetype2 fontconfig) -o /tmp/hud-font-golden \
//     && /tmp/hud-font-golden > rust/crates/xr-renderer/golden/hud_font.txt
#include "xr/hud.hpp"
#include "xr/material.hpp"
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <unistd.h>
using namespace xr;
namespace {
struct Fnv {
    std::uint64_t h = 0xcbf29ce484222325ull;
    void bytes(const void* p, std::size_t n) {
        for (std::size_t i = 0; i < n; ++i) { h ^= static_cast<const std::uint8_t*>(p)[i]; h *= 0x100000001b3ull; }
    }
    void u32(std::uint32_t v) { const std::uint8_t le[4] = {std::uint8_t(v), std::uint8_t(v >> 8), std::uint8_t(v >> 16), std::uint8_t(v >> 24)}; bytes(le, 4); }
};
std::string hex(std::string_view s) {
    std::string out;
    char b[3];
    for (unsigned char c : s) { std::snprintf(b, sizeof b, "%02x", c); out += b; }
    return out.empty() ? "-" : out;
}
std::string utf8(char32_t cp) {
    std::string out;
    if (cp < 0x80) out += char(cp);
    else if (cp < 0x800) { out += char(0xC0 | (cp >> 6)); out += char(0x80 | (cp & 0x3F)); }
    else if (cp < 0x10000) { out += char(0xE0 | (cp >> 12)); out += char(0x80 | ((cp >> 6) & 0x3F)); out += char(0x80 | (cp & 0x3F)); }
    else { out += char(0xF0 | (cp >> 18)); out += char(0x80 | ((cp >> 12) & 0x3F)); out += char(0x80 | ((cp >> 6) & 0x3F)); out += char(0x80 | (cp & 0x3F)); }
    return out;
}
// "<plots> <hash>" of drawing `text` with its pen at `left` and its baseline at `baseline`.
std::string drawing(const HudFont& font, std::string_view text, std::uint32_t px, bool bold, int left, int baseline) {
    Fnv f;
    std::size_t n = 0;
    font.draw(text, px, bold, left, baseline, [&](int x, int y, float c) {
        std::uint32_t bits; std::memcpy(&bits, &c, 4);
        f.u32(std::uint32_t(x)); f.u32(std::uint32_t(y)); f.u32(bits); ++n;
    });
    return std::to_string(n) + " " + std::to_string(f.h);
}
}  // namespace

int main() {
    std::string why;
    if (auto font = HudFont::load(kHudFontFamily, why)) {
        std::printf("load %s\n", font->description().c_str());
        for (std::uint32_t px : {8u, 12u, 18u, 20u, 24u, 30u, 48u, 160u})
            for (bool bold : {false, true}) {
                const auto m = font->metrics(px, bold);
                std::printf("metrics %u %d %u %u\n", px, int(bold), m.ascent, m.descent);
            }
        // The hud_font ctest's queries, drawn as it draws them (pen at 5, baseline at 50).
        const struct { const char* text; std::uint32_t px; bool bold; } queries[] = {
            {"XR-2", 24, true}, {"Placement", 24, false}, {"Curvature  +12\xc2\xb0", 18, false},
            {"\xe2\x86\x91\xe2\x86\x93 field  \xe2\x86\x90\xe2\x86\x92 adjust", 18, false}, {"\xef\x80\x81 nerd \xe4\xb8\xad", 30, true},
            {"bad \xff\xfe trunc \xe2\x86", 24, false}, {"over \xc0\xaf \xf7\xbf\xbf\xbf \xed\xa0\x80", 12, true}, {"", 24, false},
            {"Mm", 160, false}, {"XR-2", 24, false}, {"Placement", 24, true}, {"Mm", 160, true}};
        for (const auto& q : queries)
            std::printf("text %s %u %d %u %s\n", hex(q.text).c_str(), q.px, int(q.bold), font->measure(q.text, q.px, q.bold),
                        drawing(*font, q.text, q.px, q.bold, 5, 50).c_str());
        // Single glyphs: printable ASCII, then degree, arrows, a Nerd Font icon and powerline glyph, a CJK ideograph,
        // the replacement character and an emoji (not in the font: its missing-glyph box).
        std::vector<char32_t> cps;
        for (char32_t c = 0x20; c < 0x7f; ++c) cps.push_back(c);
        for (char32_t c : {0xB0, 0xB7, 0x2190, 0x2191, 0x2192, 0x2193, 0x2026, 0xF001, 0xE0B0, 0x4E2D, 0xFFFD, 0x1F600}) cps.push_back(c);
        for (std::uint32_t px : {12u, 18u, 24u, 30u, 160u})
            for (bool bold : {false, true})
                for (char32_t cp : cps) {
                    const auto s = utf8(cp);
                    std::printf("glyph %u %d %x %u %s\n", px, int(bold), unsigned(cp), font->measure(s, px, bold),
                                drawing(*font, s, px, bold, 3, int(px)).c_str());
                }
    } else {
        std::printf("skip %s\n", why.c_str());
    }
    HudFont::load("XR Workspace No Such Font Family", why);
    std::printf("missing %s\n", why.c_str());
    // No font at all: fontconfig reads an empty configuration (no font directories), so it matches nothing. The pop-ups
    // then draw with the built-in pixel font.
    char dir[] = "/tmp/hud-font-golden-XXXXXX";
    if (!mkdtemp(dir)) return 1;
    const std::string conf = std::string(dir) + "/fonts.conf";
    if (FILE* f = std::fopen(conf.c_str(), "w")) { std::fputs("<?xml version=\"1.0\"?>\n<fontconfig></fontconfig>\n", f); std::fclose(f); }
    setenv("FONTCONFIG_FILE", conf.c_str(), 1);
    const auto none = HudFont::load(kHudFontFamily, why);
    std::printf("empty_fontconfig %s %s\n", none ? "loaded" : "failed", why.c_str());
    for (std::uint8_t mode : {0, 1, 2}) {
        const auto card = render_hud_card(view_mode_lines(mode), kMdDefaultSeed, none.get());
        Fnv f; f.bytes(card.rgba.data(), card.rgba.size());
        std::printf("fallback_card %u %u %u %llu\n", unsigned(mode), card.width, card.height, (unsigned long long)f.h);
    }
    unlink(conf.c_str()); rmdir(dir);
}
