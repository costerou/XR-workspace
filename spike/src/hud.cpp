#include "xr/hud.hpp"
#include <algorithm>
#include <cmath>

namespace xr {
namespace {
using Glyph = std::array<std::uint8_t, kGlyphH>;
constexpr Glyph kLetters[26] = {
    {0x0E, 0x11, 0x11, 0x1F, 0x11, 0x11, 0x11}, {0x1E, 0x11, 0x11, 0x1E, 0x11, 0x11, 0x1E},  // A B
    {0x0E, 0x11, 0x10, 0x10, 0x10, 0x11, 0x0E}, {0x1C, 0x12, 0x11, 0x11, 0x11, 0x12, 0x1C},  // C D
    {0x1F, 0x10, 0x10, 0x1E, 0x10, 0x10, 0x1F}, {0x1F, 0x10, 0x10, 0x1E, 0x10, 0x10, 0x10},  // E F
    {0x0E, 0x11, 0x10, 0x17, 0x11, 0x11, 0x0F}, {0x11, 0x11, 0x11, 0x1F, 0x11, 0x11, 0x11},  // G H
    {0x0E, 0x04, 0x04, 0x04, 0x04, 0x04, 0x0E}, {0x07, 0x02, 0x02, 0x02, 0x02, 0x12, 0x0C},  // I J
    {0x11, 0x12, 0x14, 0x18, 0x14, 0x12, 0x11}, {0x10, 0x10, 0x10, 0x10, 0x10, 0x10, 0x1F},  // K L
    {0x11, 0x1B, 0x15, 0x15, 0x11, 0x11, 0x11}, {0x11, 0x11, 0x19, 0x15, 0x13, 0x11, 0x11},  // M N
    {0x0E, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0E}, {0x1E, 0x11, 0x11, 0x1E, 0x10, 0x10, 0x10},  // O P
    {0x0E, 0x11, 0x11, 0x11, 0x15, 0x12, 0x0D}, {0x1E, 0x11, 0x11, 0x1E, 0x14, 0x12, 0x11},  // Q R
    {0x0F, 0x10, 0x10, 0x0E, 0x01, 0x01, 0x1E}, {0x1F, 0x04, 0x04, 0x04, 0x04, 0x04, 0x04},  // S T
    {0x11, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0E}, {0x11, 0x11, 0x11, 0x11, 0x11, 0x0A, 0x04},  // U V
    {0x11, 0x11, 0x11, 0x15, 0x15, 0x15, 0x0A}, {0x11, 0x11, 0x0A, 0x04, 0x0A, 0x11, 0x11},  // W X
    {0x11, 0x11, 0x0A, 0x04, 0x04, 0x04, 0x04}, {0x1F, 0x01, 0x02, 0x04, 0x08, 0x10, 0x1F},  // Y Z
};
constexpr Glyph kDigits[10] = {
    {0x0E, 0x11, 0x13, 0x15, 0x19, 0x11, 0x0E}, {0x04, 0x0C, 0x04, 0x04, 0x04, 0x04, 0x0E},
    {0x0E, 0x11, 0x01, 0x02, 0x04, 0x08, 0x1F}, {0x1F, 0x02, 0x04, 0x02, 0x01, 0x11, 0x0E},
    {0x02, 0x06, 0x0A, 0x12, 0x1F, 0x02, 0x02}, {0x1F, 0x10, 0x1E, 0x01, 0x01, 0x11, 0x0E},
    {0x06, 0x08, 0x10, 0x1E, 0x11, 0x11, 0x0E}, {0x1F, 0x01, 0x02, 0x04, 0x08, 0x08, 0x08},
    {0x0E, 0x11, 0x11, 0x0E, 0x11, 0x11, 0x0E}, {0x0E, 0x11, 0x11, 0x0F, 0x01, 0x02, 0x0C},
};
constexpr Glyph kSpace{}, kColon{0x00, 0x0C, 0x0C, 0x00, 0x0C, 0x0C, 0x00}, kDash{0x00, 0x00, 0x00, 0x1F, 0x00, 0x00, 0x00},
    kPlus{0x00, 0x04, 0x04, 0x1F, 0x04, 0x04, 0x00}, kDot{0x00, 0x00, 0x00, 0x00, 0x00, 0x0C, 0x0C},
    kSlash{0x00, 0x01, 0x02, 0x04, 0x08, 0x10, 0x00}, kUnknown{0x0E, 0x11, 0x01, 0x02, 0x04, 0x00, 0x04};

constexpr std::array<float, 3> kWhite{0.96f, 0.96f, 0.96f}, kDim{0.62f, 0.62f, 0.62f};
constexpr std::array<float, 3> kBackground{0.06f, 0.06f, 0.07f};
constexpr float kBackgroundAlpha = 0.88f;
// Card chrome in output pixels (the card is drawn 1:1 in the glasses).
constexpr float kPad = 22, kLineGap = 8, kRadius = 14, kBorder = 2;
constexpr std::uint32_t kPixelFontZoom = 2;  // built-in font: each font texel is 2x2 pixels
// Real font: em size per HudLine::scale step, chosen so capitals match the pixel font's height.
constexpr std::uint32_t kEmPerScale = 19;

// Premultiplied "over": paint colour `c` with coverage `a` on top of what is there.
void over(HudImage& im, int x, int y, std::array<float, 3> c, float a) {
    if (x < 0 || y < 0 || std::uint32_t(x) >= im.width || std::uint32_t(y) >= im.height || a <= 0) return;
    a = std::min(a, 1.f);
    auto* p = &im.rgba[(std::size_t(y) * im.width + std::size_t(x)) * 4];
    for (int i = 0; i < 3; ++i) p[i] = std::uint8_t(std::lround(std::clamp(c[std::size_t(i)] * a * 255 + p[i] * (1 - a), 0.f, 255.f)));
    p[3] = std::uint8_t(std::lround(std::clamp(a * 255 + p[3] * (1 - a), 0.f, 255.f)));
}
// Signed distance (pixels, negative inside) from pixel centre to a rounded rectangle.
float rounded_distance(float px, float py, float w, float h, float r) {
    const float qx = std::fabs(px - w / 2) - (w / 2 - r), qy = std::fabs(py - h / 2) - (h / 2 - r);
    const float ox = std::max(qx, 0.f), oy = std::max(qy, 0.f);
    return std::sqrt(ox * ox + oy * oy) + std::min(std::max(qx, qy), 0.f) - r;
}
struct LineBox { std::uint32_t width = 0, ascent = 0, descent = 0; };
LineBox box(const HudLine& line, const HudFont* font) {
    const auto scale = std::clamp<std::uint32_t>(line.scale, 1, 8);
    if (!font) return {hud_text_width(line.text, scale * kPixelFontZoom), kGlyphH * scale * kPixelFontZoom, 0};
    const auto m = font->metrics(scale * kEmPerScale, line.bold);
    return {font->measure(line.text, scale * kEmPerScale, line.bold), m.ascent, m.descent};
}
}

const std::array<std::uint8_t, kGlyphH>& hud_glyph(char c) {
    if (c >= 'a' && c <= 'z') c = char(c - 'a' + 'A');
    if (c >= 'A' && c <= 'Z') return kLetters[c - 'A'];
    if (c >= '0' && c <= '9') return kDigits[c - '0'];
    switch (c) {
    case ' ': return kSpace; case ':': return kColon; case '-': return kDash; case '+': return kPlus;
    case '.': return kDot; case '/': return kSlash; default: return kUnknown;
    }
}

std::uint32_t hud_text_width(std::string_view text, std::uint32_t scale) {
    if (text.empty()) return 0;
    return std::uint32_t(text.size() * (kGlyphW + 1) - 1) * scale;
}

HudImage render_hud_card(const std::vector<HudLine>& lines, std::array<float, 3> accent, const HudFont* font) {
    HudImage im;
    std::vector<LineBox> boxes;
    std::uint32_t content_w = 0, content_h = 0;
    for (std::size_t i = 0; i < lines.size(); ++i) {
        boxes.push_back(box(lines[i], font));
        content_w = std::max(content_w, boxes.back().width);
        content_h += boxes.back().ascent + boxes.back().descent + (i ? std::uint32_t(kLineGap) : 0);
    }
    im.width = content_w + 2 * std::uint32_t(kPad); im.height = content_h + 2 * std::uint32_t(kPad);
    im.rgba.assign(std::size_t(im.width) * im.height * 4, 0);
    const float w = float(im.width), h = float(im.height);
    for (std::uint32_t y = 0; y < im.height; ++y)
        for (std::uint32_t x = 0; x < im.width; ++x) {
            const float d = rounded_distance(float(x) + 0.5f, float(y) + 0.5f, w, h, kRadius);
            const float outer = std::clamp(0.5f - d, 0.f, 1.f);          // anti-aliased card edge
            const float inner = std::clamp(0.5f - (d + kBorder), 0.f, 1.f);  // inside the border
            if (outer <= 0) continue;
            over(im, int(x), int(y), accent, outer);
            auto* p = &im.rgba[(std::size_t(y) * im.width + x) * 4];
            if (inner > 0) {  // replace the accent with the translucent background inside the border
                for (int i = 0; i < 3; ++i) p[i] = std::uint8_t(std::lround((kBackground[std::size_t(i)] * kBackgroundAlpha * inner + accent[std::size_t(i)] * outer * (1 - inner)) * 255));
                p[3] = std::uint8_t(std::lround((kBackgroundAlpha * inner + outer * (1 - inner)) * 255));
            }
        }
    std::uint32_t top = std::uint32_t(kPad);
    for (std::size_t i = 0; i < lines.size(); ++i) {
        const auto& line = lines[i]; const auto& b = boxes[i];
        const int left = int((im.width - b.width) / 2);
        const auto scale = std::clamp<std::uint32_t>(line.scale, 1, 8);
        if (font) {
            font->draw(line.text, scale * kEmPerScale, line.bold, left, int(top + b.ascent),
                       [&](int x, int y, float coverage) { over(im, x, y, line.color, coverage); });
        } else {
            const auto z = scale * kPixelFontZoom;
            int x = left;
            for (char ch : line.text) {
                const auto& g = hud_glyph(ch);
                for (std::uint32_t row = 0; row < kGlyphH; ++row)
                    for (std::uint32_t col = 0; col < kGlyphW; ++col)
                        if (g[row] & (0x10u >> col))
                            for (std::uint32_t sy = 0; sy < z; ++sy)
                                for (std::uint32_t sx = 0; sx < z; ++sx)
                                    over(im, x + int(col * z + sx), int(top + row * z + sy), line.color, 1.f);
                x += int((kGlyphW + 1) * z);
            }
        }
        top += b.ascent + b.descent + std::uint32_t(kLineGap);
    }
    return im;
}

float popup_alpha(double t) {
    if (t < 0 || t >= kPopupTotalMs) return 0.f;
    if (t < kPopupFadeInMs) return float(t / kPopupFadeInMs);
    if (t < kPopupFadeInMs + kPopupHoldMs) return 1.f;
    const double out = (t - kPopupFadeInMs - kPopupHoldMs) / kPopupFadeOutMs;
    return float(1 - out * out * (3 - 2 * out));  // smooth fade out
}

std::vector<HudLine> view_mode_lines(std::uint8_t mode) {
    const char* name = mode == 1 ? "Locked" : mode == 2 ? "Follow" : "Anchor";
    const char* hint = mode == 1 ? "Screens move with your head" : mode == 2 ? "Screens ease back in front of you"
                                                                             : "Screens stay fixed in the room";
    return {{"View mode", 2, kDim}, {name, 4, kWhite, true}, {hint, 2, kDim}, {"Ctrl+Alt+V to change", 1, kDim}};
}

std::vector<HudLine> cursor_follow_lines(bool on) {
    return {{"Cursor follow", 2, kDim}, {on ? "On" : "Off", 4, kWhite, true},
            {on ? "View follows the pointer between screens" : "View stays on the selected screen", 2, kDim},
            {"Ctrl+Alt+F to change", 1, kDim}};
}

std::vector<HudLine> prediction_lines(bool on) {
    return {{"Prediction", 2, kDim}, {on ? "On" : "Off", 4, kWhite, true},
            {on ? "Screens lead your head motion slightly" : "Screens use the latest sensor pose", 2, kDim},
            {"Ctrl+Alt+P to change", 1, kDim}};
}

bool StatusPopups::update(std::uint8_t mode, bool cursor_follow, bool prediction, double now_ms) {
    if (mode > 2) mode = 0;
    const bool first = !seen_, mode_changed = first || mode != mode_, cursor_changed = !first && cursor_follow != cursor_follow_;
    const bool prediction_changed = !first && prediction != prediction_;
    seen_ = true; mode_ = mode; cursor_follow_ = cursor_follow; prediction_ = prediction;
    if (!mode_changed && !cursor_changed && !prediction_changed) return false;
    std::vector<HudLine> lines;
    if (mode_changed) lines = view_mode_lines(mode);
    if (cursor_changed) {
        if (!lines.empty()) lines.push_back({"", 1, kDim});
        for (auto& l : cursor_follow_lines(cursor_follow)) lines.push_back(std::move(l));
    }
    if (prediction_changed) {
        if (!lines.empty()) lines.push_back({"", 1, kDim});
        for (auto& l : prediction_lines(prediction)) lines.push_back(std::move(l));
    }
    image_ = render_hud_card(lines, {0.48f, 0.64f, 0.97f}, font_.get());  // same accent as the selection highlight
    shown_ms_ = now_ms; ++generation_;
    return true;
}

float StatusPopups::alpha(double now_ms) const { return generation_ ? popup_alpha(now_ms - shown_ms_) : 0.f; }
}

namespace xr {
void StatusPopups::set_font(std::unique_ptr<HudFont> font) { font_ = std::move(font); }
}
