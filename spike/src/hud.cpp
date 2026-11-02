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
    kSlash{0x00, 0x01, 0x02, 0x04, 0x08, 0x10, 0x00}, kPercent{0x18, 0x19, 0x02, 0x04, 0x08, 0x13, 0x03},
    kOpen{0x0E, 0x08, 0x08, 0x08, 0x08, 0x08, 0x0E}, kClose{0x0E, 0x02, 0x02, 0x02, 0x02, 0x02, 0x0E}, kUnknown{0x0E, 0x11, 0x01, 0x02, 0x04, 0x00, 0x04};

constexpr std::array<float, 3> kWhite{0.96f, 0.96f, 0.96f}, kDim{0.62f, 0.62f, 0.62f};
constexpr std::array<float, 3> kBackground{0.06f, 0.06f, 0.07f};
constexpr float kBackgroundAlpha = 0.88f;
// Card chrome in output pixels (the card is drawn 1:1 in the glasses).
constexpr float kPad = 22, kLineGap = 8, kRadius = 14, kBorder = 2;
constexpr std::uint32_t kPixelFontZoom = 2;  // built-in font: each font texel is 2x2 pixels
// Real font: em size per HudLine::scale step, chosen so capitals match the pixel font's height.
constexpr std::uint32_t kEmPerScale = 19;
// Help card: roomier padding, gaps between title / rows / footer, between rows and columns.
constexpr float kHelpPad = 34, kSectionGap = 22, kRowGap = 12, kColumnGap = 48;

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
// Size `im` and paint the rounded translucent card with its accent border.
void allocate_card(HudImage& im, std::uint32_t width, std::uint32_t height, std::array<float, 3> accent) {
    im.width = width; im.height = height;
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
}
// Draw one line with its top edge at `top` and left edge at `left`.
void draw_line(HudImage& im, const HudLine& line, const LineBox& b, int left, std::uint32_t top, const HudFont* font) {
    const auto scale = std::clamp<std::uint32_t>(line.scale, 1, 8);
    if (font) {
        font->draw(line.text, scale * kEmPerScale, line.bold, left, int(top + b.ascent),
                   [&](int x, int y, float coverage) { over(im, x, y, line.color, coverage); });
        return;
    }
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
float smoothstep(float t) { t = std::clamp(t, 0.f, 1.f); return t * t * (3 - 2 * t); }
}

const std::array<std::uint8_t, kGlyphH>& hud_glyph(char c) {
    if (c >= 'a' && c <= 'z') c = char(c - 'a' + 'A');
    if (c >= 'A' && c <= 'Z') return kLetters[c - 'A'];
    if (c >= '0' && c <= '9') return kDigits[c - '0'];
    switch (c) {
    case ' ': return kSpace; case ':': return kColon; case '-': return kDash; case '+': return kPlus;
    case '.': return kDot; case '/': return kSlash; case '[': return kOpen; case ']': return kClose; case '%': return kPercent; default: return kUnknown;
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
    allocate_card(im, content_w + 2 * std::uint32_t(kPad), content_h + 2 * std::uint32_t(kPad), accent);
    std::uint32_t top = std::uint32_t(kPad);
    for (std::size_t i = 0; i < lines.size(); ++i) {
        const auto& b = boxes[i];
        draw_line(im, lines[i], b, int((im.width - b.width) / 2), top, font);
        top += b.ascent + b.descent + std::uint32_t(kLineGap);
    }
    return im;
}

HudImage render_help_card(const std::vector<HelpRow>& rows, std::array<float, 3> accent, const HudFont* font) {
    const HudLine title{"Keyboard shortcuts", 2, kWhite, true}, footer{"Ctrl+Alt+H to close", 1, kDim};
    std::vector<std::pair<HudLine, HudLine>> table;
    for (const auto& r : rows) table.push_back({{r.keys, 2, kWhite, true}, {r.action, 2, kDim}});
    const LineBox title_box = box(title, font), footer_box = box(footer, font);
    std::uint32_t keys_w = 0, actions_w = 0, rows_h = 0;
    std::vector<std::pair<LineBox, LineBox>> boxes;
    for (const auto& [k, a] : table) {
        boxes.push_back({box(k, font), box(a, font)});
        keys_w = std::max(keys_w, boxes.back().first.width); actions_w = std::max(actions_w, boxes.back().second.width);
        rows_h += std::max(boxes.back().first.ascent, boxes.back().second.ascent) +
                  std::max(boxes.back().first.descent, boxes.back().second.descent) + std::uint32_t(kRowGap);
    }
    const std::uint32_t table_w = table.empty() ? 0 : keys_w + std::uint32_t(kColumnGap) + actions_w;
    const std::uint32_t content_w = std::max({title_box.width, footer_box.width, table_w});
    const std::uint32_t content_h = title_box.ascent + title_box.descent + std::uint32_t(kSectionGap) + rows_h +
                                    std::uint32_t(kSectionGap) + footer_box.ascent + footer_box.descent;
    HudImage im;
    allocate_card(im, content_w + 2 * std::uint32_t(kHelpPad), content_h + 2 * std::uint32_t(kHelpPad), accent);
    std::uint32_t top = std::uint32_t(kHelpPad);
    draw_line(im, title, title_box, int((im.width - title_box.width) / 2), top, font);
    top += title_box.ascent + title_box.descent + std::uint32_t(kSectionGap);
    // Divider under the title, in the accent colour, faint.
    for (std::uint32_t x = std::uint32_t(kHelpPad); x + std::uint32_t(kHelpPad) < im.width; ++x)
        over(im, int(x), int(top - std::uint32_t(kSectionGap) / 2), accent, 0.45f);
    const int left = int((im.width - table_w) / 2);
    for (std::size_t i = 0; i < table.size(); ++i) {
        const auto& [kb, ab] = boxes[i];
        const std::uint32_t ascent = std::max(kb.ascent, ab.ascent);
        draw_line(im, table[i].first, kb, left, top + ascent - kb.ascent, font);   // shared baseline
        draw_line(im, table[i].second, ab, left + int(keys_w + std::uint32_t(kColumnGap)), top + ascent - ab.ascent, font);
        top += ascent + std::max(kb.descent, ab.descent) + std::uint32_t(kRowGap);
    }
    top += std::uint32_t(kSectionGap);
    draw_line(im, footer, footer_box, int((im.width - footer_box.width) / 2), top, font);
    return im;
}

namespace {
// Placement menu: field order matches ipc.hpp WirePlacement / geometry.py PLACEMENT_KEYS.
struct MenuField { const char* label; const char* hint; bool percent, is_signed; };
constexpr MenuField kMenuFields[kPlacementMenuFields] = {
    {"Left / right", "", true, true}, {"Down / up", "", true, true}, {"Distance", "", true, false},
    {"Yaw", "turn left or right", false, true}, {"Pitch", "tilt up or down", false, true},
    {"Roll", "tilt sideways", false, true}, {"Curvature", "", false, false}};
constexpr float kMenuHintGap = 20, kMenuBandPad = 12, kMenuMarkerW = 4, kMenuTabRule = 3;
}

std::vector<PlacementMenuRow> placement_menu_rows(const PlacementMenuState& state, bool ascii_units) {
    const auto& p = state.placements[std::min<std::size_t>(state.desktop, kPlacementMenuDesktopCount - 1)];
    std::vector<PlacementMenuRow> rows;
    for (std::size_t i = 0; i < kPlacementMenuFields; ++i) {
        const auto& f = kMenuFields[i];
        const std::int32_t v = p[i];
        std::string value = (f.is_signed && v > 0 ? "+" : "") + std::to_string(v);
        value += f.percent ? "%" : ascii_units ? " deg" : "\xc2\xb0";  // U+00B0 DEGREE SIGN
        rows.push_back({f.label, f.hint, std::move(value), state.field == i});
    }
    return rows;
}

const std::vector<std::string>& placement_menu_hints() {
    // Keys must match the supervisor's modal menu binds (tools/native_spike/view.py MENU_BINDS).
    static const std::vector<std::string> hints{"Up / Down  choose a setting     Left / Right  adjust     Shift  bigger steps",
                                                "Tab  next desktop     R  reset to flat     Esc  close"};
    return hints;
}

HudImage render_placement_menu_card(const PlacementMenuState& state, std::array<float, 3> accent, const HudFont* font) {
    const HudLine title{"Placement", 2, kWhite, true};
    std::vector<HudLine> tabs;
    for (std::uint32_t d = 0; d < kPlacementMenuDesktopCount; ++d) {
        const bool on = d == state.desktop;
        tabs.push_back({"XR-" + std::to_string(d + 1), 2, on ? kWhite : kDim, on});
    }
    std::vector<HudLine> footer;
    for (const auto& h : placement_menu_hints()) footer.push_back({h, 1, kDim});
    struct Cells { HudLine label, hint, value; LineBox lb, hb, vb; bool selected; };
    std::vector<Cells> rows;
    std::uint32_t label_w = 0, hint_w = 0, value_w = 0;
    for (auto& r : placement_menu_rows(state, font == nullptr)) {
        Cells c{{r.label, 2, kWhite, r.selected}, {r.hint, 2, kDim}, {r.value, 2, kWhite, true}, {}, {}, {}, r.selected};
        c.lb = box(c.label, font); c.hb = box(c.hint, font); c.vb = box(c.value, font);
        label_w = std::max(label_w, c.lb.width); hint_w = std::max(hint_w, c.hb.width); value_w = std::max(value_w, c.vb.width);
        rows.push_back(std::move(c));
    }
    const LineBox title_box = box(title, font);
    std::vector<LineBox> tab_boxes, footer_boxes;
    std::uint32_t tabs_w = 0, tabs_ascent = 0, tabs_descent = 0, footer_w = 0, footer_h = 0;
    for (std::size_t i = 0; i < tabs.size(); ++i) {
        tab_boxes.push_back(box(tabs[i], font));
        tabs_w += tab_boxes.back().width + (i ? std::uint32_t(kColumnGap) : 0);
        tabs_ascent = std::max(tabs_ascent, tab_boxes.back().ascent); tabs_descent = std::max(tabs_descent, tab_boxes.back().descent);
    }
    for (std::size_t i = 0; i < footer.size(); ++i) {
        footer_boxes.push_back(box(footer[i], font));
        footer_w = std::max(footer_w, footer_boxes.back().width);
        footer_h += footer_boxes.back().ascent + footer_boxes.back().descent + (i ? std::uint32_t(kLineGap) : 0);
    }
    const std::uint32_t hint_col = hint_w ? hint_w + std::uint32_t(kMenuHintGap) : 0;
    const std::uint32_t table_w = label_w + hint_col + std::uint32_t(kColumnGap) + value_w + 2 * std::uint32_t(kMenuBandPad);
    std::uint32_t rows_h = 0;
    for (const auto& c : rows)
        rows_h += std::max({c.lb.ascent, c.hb.ascent, c.vb.ascent}) + std::max({c.lb.descent, c.hb.descent, c.vb.descent}) + std::uint32_t(kRowGap);
    const std::uint32_t content_w = std::max({title_box.width, tabs_w, table_w, footer_w});
    const std::uint32_t tabs_h = tabs_ascent + tabs_descent + std::uint32_t(kMenuTabRule) + std::uint32_t(kLineGap);
    const std::uint32_t content_h = title_box.ascent + title_box.descent + std::uint32_t(kLineGap) + tabs_h + std::uint32_t(kSectionGap) +
                                    rows_h + std::uint32_t(kSectionGap) + footer_h;
    HudImage im;
    allocate_card(im, content_w + 2 * std::uint32_t(kHelpPad), content_h + 2 * std::uint32_t(kHelpPad), accent);
    std::uint32_t top = std::uint32_t(kHelpPad);
    draw_line(im, title, title_box, int((im.width - title_box.width) / 2), top, font);
    top += title_box.ascent + title_box.descent + std::uint32_t(kLineGap);
    // Desktop tabs on a shared baseline; the selected one is underlined in the accent colour.
    int x = int((im.width - tabs_w) / 2);
    for (std::size_t i = 0; i < tabs.size(); ++i) {
        const auto& b = tab_boxes[i];
        draw_line(im, tabs[i], b, x, top + tabs_ascent - b.ascent, font);
        if (tabs[i].bold)
            for (std::uint32_t y = 0; y < std::uint32_t(kMenuTabRule); ++y)
                for (std::uint32_t dx = 0; dx < b.width; ++dx)
                    over(im, x + int(dx), int(top + tabs_ascent + tabs_descent + std::uint32_t(kLineGap) / 2 + y), accent, 1.f);
        x += int(b.width + std::uint32_t(kColumnGap));
    }
    top += tabs_h + std::uint32_t(kSectionGap);
    for (std::uint32_t dx = std::uint32_t(kHelpPad); dx + std::uint32_t(kHelpPad) < im.width; ++dx)  // divider, as on the help card
        over(im, int(dx), int(top - std::uint32_t(kSectionGap) / 2), accent, 0.45f);
    const int left = int((im.width - table_w) / 2);
    for (const auto& c : rows) {
        const std::uint32_t ascent = std::max({c.lb.ascent, c.hb.ascent, c.vb.ascent});
        const std::uint32_t height = ascent + std::max({c.lb.descent, c.hb.descent, c.vb.descent});
        if (c.selected) {  // translucent accent band behind the row, solid marker on its left edge
            const int y0 = int(top) - int(kRowGap) / 2, y1 = int(top + height) + int(kRowGap) / 2;
            for (int y = y0; y < y1; ++y)
                for (int dx = 0; dx < int(table_w); ++dx) over(im, left + dx, y, accent, dx < int(kMenuMarkerW) ? 1.f : 0.28f);
        }
        const int col = left + int(kMenuBandPad);
        draw_line(im, c.label, c.lb, col, top + ascent - c.lb.ascent, font);
        if (!c.hint.text.empty()) draw_line(im, c.hint, c.hb, col + int(label_w + std::uint32_t(kMenuHintGap)), top + ascent - c.hb.ascent, font);
        draw_line(im, c.value, c.vb, left + int(table_w) - int(kMenuBandPad) - int(c.vb.width), top + ascent - c.vb.ascent, font);  // right-aligned
        top += height + std::uint32_t(kRowGap);
    }
    top += std::uint32_t(kSectionGap);
    for (std::size_t i = 0; i < footer.size(); ++i) {
        const auto& b = footer_boxes[i];
        draw_line(im, footer[i], b, int((im.width - b.width) / 2), top, font);
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

bool CardFade::set(bool visible, double now_ms) {
    if (visible == visible_) return false;
    from_ = level(now_ms);  // reverse from wherever the fade is
    visible_ = visible; changed_ms_ = now_ms;
    return true;
}
float CardFade::level(double now_ms) const {
    const double dt = std::max(0.0, now_ms - changed_ms_);
    if (visible_) return std::min(1.f, from_ + float(dt / in_ms_));
    return std::max(0.f, from_ - float(dt / out_ms_));
}
float CardFade::alpha(double now_ms) const { return smoothstep(level(now_ms)); }
bool CardFade::animating(double now_ms) const {
    const float l = level(now_ms);
    return visible_ ? l < 1.f : l > 0.f;
}

bool HelpOverlay::set(bool visible, const std::vector<HelpRow>& rows, double now_ms) {
    if (visible && (!generation_ || rows.size() != rows_.size() ||
                    !std::equal(rows.begin(), rows.end(), rows_.begin(),
                                [](const HelpRow& a, const HelpRow& b) { return a.keys == b.keys && a.action == b.action; }))) {
        rows_ = rows;
        image_ = render_help_card(rows_, {0.48f, 0.64f, 0.97f}, font_.get());  // same accent as the pop-ups
        ++generation_;
    }
    if (!fade_.set(visible, now_ms)) return false;
    if (visible) ++shown_; else ++hidden_;
    return true;
}
void HelpOverlay::set_font(std::unique_ptr<HudFont> font) { font_ = std::move(font); }
float HelpOverlay::alpha(double now_ms) const { return generation_ ? fade_.alpha(now_ms) : 0.f; }
float HelpOverlay::offset_px(double now_ms) const { return (1 - alpha(now_ms)) * kHelpRisePx; }
bool HelpOverlay::animating(double now_ms) const { return generation_ && fade_.animating(now_ms); }

bool PlacementMenuOverlay::set(const PlacementMenuState& state, double now_ms) {
    bool changed = false;
    if (state.open && (!generation_ || !(state == rendered_))) {
        rendered_ = state;
        image_ = render_placement_menu_card(rendered_, {0.48f, 0.64f, 0.97f}, font_.get());  // help card accent
        ++generation_; changed = true;
    }
    if (fade_.set(state.open, now_ms)) {  // closing keeps the last card for the fade-out
        if (state.open) ++shown_; else ++hidden_;
        changed = true;
    }
    return changed;
}
void PlacementMenuOverlay::set_font(std::unique_ptr<HudFont> font) { font_ = std::move(font); }
float PlacementMenuOverlay::alpha(double now_ms) const { return generation_ ? fade_.alpha(now_ms) : 0.f; }
float PlacementMenuOverlay::offset_px(double now_ms) const { return (1 - alpha(now_ms)) * kHelpRisePx; }
bool PlacementMenuOverlay::animating(double now_ms) const { return generation_ && fade_.animating(now_ms); }
}
