#include "xr/hud.hpp"
#include "xr/video.hpp"  // render_video_card: built from the help card parts below
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
constexpr std::array<float, 3> kAccent{0.48f, 0.64f, 0.97f};  // pop-up border
// Help card and placement menu: these stay up for a while, so they are dimmed against micro-OLED image retention.
// Text at ~70% and dim text at ~46% stay readable on the near-black card; the accent keeps its hue at 75%.
constexpr std::array<float, 3> kCardText{0.70f, 0.70f, 0.70f}, kCardDim{0.46f, 0.46f, 0.46f};
constexpr std::array<float, 3> kCardAccent{kAccent[0] * 0.75f, kAccent[1] * 0.75f, kAccent[2] * 0.75f};  // border, rules, highlights
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
    // Most of the card is plain translucent background. Outside the corner squares the signed distance is minus the
    // distance to the nearest edge, so a pixel at least kBorder + 0.5 inside is exactly the background: fill it
    // directly instead of evaluating the rounded rectangle (the same bytes; ~20x faster on a menu-sized card).
    std::array<std::uint8_t, 4> fill{};
    for (int i = 0; i < 3; ++i) fill[std::size_t(i)] = std::uint8_t(std::lround(kBackground[std::size_t(i)] * kBackgroundAlpha * 255));
    fill[3] = std::uint8_t(std::lround(kBackgroundAlpha * 255));
    for (std::uint32_t y = 0; y < im.height; ++y)
        for (std::uint32_t x = 0; x < im.width; ++x) {
            const float cx = float(x) + 0.5f, cy = float(y) + 0.5f;
            const float edge = std::min({cx, cy, w - cx, h - cy});
            const bool band = (cx >= kRadius && cx <= w - kRadius) || (cy >= kRadius && cy <= h - kRadius);
            if (band && edge >= kBorder + 0.5f) {
                std::copy(fill.begin(), fill.end(), &im.rgba[(std::size_t(y) * im.width + x) * 4]);
                continue;
            }
            const float d = rounded_distance(cx, cy, w, h, kRadius);
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

namespace {
// Help-card building blocks. The help card and the placement menu are both drawn from these, so
// type, colours, sizes and spacing cannot drift apart.
constexpr float kDividerAlpha = 0.45f;     // faint accent rule under the card title
constexpr float kHighlightAlpha = 0.28f;   // accent wash behind the selected menu row
static_assert(kHudFitMargin == std::uint32_t(kHelpPad), "fit margin is the card padding");
HudLine card_title(std::string text) { return {std::move(text), 2, kCardText, true}; }
HudLine card_key(std::string text) { return {std::move(text), 2, kCardText, true}; }     // left column: bold
HudLine card_detail(std::string text) { return {std::move(text), 2, kCardDim}; }         // descriptions: dim
HudLine card_footer(std::string text) { return {std::move(text), 1, kCardDim}; }         // key hints: small, dim
std::uint32_t height(const LineBox& b) { return b.ascent + b.descent; }
struct Cell { HudLine line; LineBox box; };
Cell cell(HudLine line, const HudFont* font) { auto b = box(line, font); return {std::move(line), b}; }
// Cells sharing one baseline: tallest ascent above it, deepest descent below it.
template<class... C> std::uint32_t row_ascent(const C&... c) { return std::max({c.box.ascent...}); }
template<class... C> std::uint32_t row_descent(const C&... c) { return std::max({c.box.descent...}); }
void draw_cell(HudImage& im, const Cell& c, int left, std::uint32_t top, std::uint32_t ascent, const HudFont* font) {
    draw_line(im, c.line, c.box, left, top + ascent - c.box.ascent, font);
}
void draw_centered(HudImage& im, const Cell& c, std::uint32_t top, const HudFont* font) {
    draw_line(im, c.line, c.box, int((im.width - c.box.width) / 2), top, font);
}
// Divider across the card's padded width, centred in the section gap that ends at `section_top`.
void draw_divider(HudImage& im, std::uint32_t section_top, std::array<float, 3> accent) {
    for (std::uint32_t x = std::uint32_t(kHelpPad); x + std::uint32_t(kHelpPad) < im.width; ++x)
        over(im, int(x), int(section_top - std::uint32_t(kSectionGap) / 2), accent, kDividerAlpha);
}
std::uint32_t card_size(std::uint32_t content) { return content + 2 * std::uint32_t(kHelpPad); }
}

HudImage render_help_card(const std::vector<HelpRow>& rows, std::array<float, 3> accent, const HudFont* font) {
    const Cell title = cell(card_title("Keyboard shortcuts"), font),
               footer = cell(card_footer("Ctrl+Alt+H to close. Help and menu close after " + std::to_string(kOverlayIdleCloseS) + " s idle."), font);
    std::vector<std::pair<Cell, Cell>> table;
    std::uint32_t keys_w = 0, actions_w = 0, rows_h = 0;
    for (const auto& r : rows) {
        table.push_back({cell(card_key(r.keys), font), cell(card_detail(r.action), font)});
        const auto& [k, a] = table.back();
        keys_w = std::max(keys_w, k.box.width); actions_w = std::max(actions_w, a.box.width);
        rows_h += row_ascent(k, a) + row_descent(k, a) + std::uint32_t(kRowGap);
    }
    const std::uint32_t table_w = table.empty() ? 0 : keys_w + std::uint32_t(kColumnGap) + actions_w;
    const std::uint32_t content_w = std::max({title.box.width, footer.box.width, table_w});
    const std::uint32_t content_h = height(title.box) + std::uint32_t(kSectionGap) + rows_h + std::uint32_t(kSectionGap) + height(footer.box);
    HudImage im;
    allocate_card(im, card_size(content_w), card_size(content_h), accent);
    std::uint32_t top = std::uint32_t(kHelpPad);
    draw_centered(im, title, top, font);
    top += height(title.box) + std::uint32_t(kSectionGap);
    draw_divider(im, top, accent);
    const int left = int((im.width - table_w) / 2);
    for (const auto& [k, a] : table) {
        const std::uint32_t ascent = row_ascent(k, a);
        draw_cell(im, k, left, top, ascent, font);
        draw_cell(im, a, left + int(keys_w + std::uint32_t(kColumnGap)), top, ascent, font);
        top += ascent + row_descent(k, a) + std::uint32_t(kRowGap);
    }
    top += std::uint32_t(kSectionGap);
    draw_centered(im, footer, top, font);
    return im;
}

namespace {
// Placement menu: field order matches ipc.hpp WirePlacement / geometry.py PLACEMENT_KEYS.
// lo/hi: the slider's ends (ipc.cpp kPlacementBounds); flat: the flat-default value the slider fills from.
struct MenuField { const char* label; const char* hint; bool percent, is_signed; std::int32_t lo, hi, flat; };
constexpr MenuField kMenuFields[kPlacementMenuFields] = {
    {"Left / right", "", true, true, -100, 100, 0}, {"Down / up", "", true, true, -100, 100, 0},
    {"Distance", "", true, false, 50, 200, 100}, {"Yaw", "turn left or right", false, true, -60, 60, 0},
    {"Pitch", "tilt up or down", false, true, -45, 45, 0}, {"Roll", "tilt sideways", false, true, -45, 45, 0},
    {"Curvature", "", false, false, 0, 120, 0}};
// Key hints (keys, action); must match the supervisor's modal menu binds (tools/native_spike/view.py MENU_BINDS).
// The toggle chord (Ctrl+Alt+L) is on the help card; inside the menu Esc closes it, so the footer names Esc.
// The short set is the last resort on small outputs; it keeps every key.
// Ctrl+Alt+M (mouse mode: click or drag the sliders) is bound only while the menu is open; Esc leaves mouse mode first.
// The short set leaves it out: with it, 1280x720 outputs lose a row to a second footer line. The help card's menu row
// and the README still name it.
const std::vector<std::pair<std::string, std::string>> kMenuHints{
    {"Up / Down", "choose a setting"}, {"Left / Right", "adjust"}, {"Shift", "bigger steps"},
    {"Tab", "next desktop"}, {"R", "reset to flat"}, {"Ctrl+Alt+M", "mouse"}, {"Esc", "close"}};
const std::vector<std::pair<std::string, std::string>> kMenuHintsShort{
    {"Up/Down", "setting"}, {"Left/Right", "adjust"}, {"Shift", "big step"}, {"Tab", "desktop"}, {"R", "flat"}, {"Esc", "close"}};
constexpr const char* kHintKeyGap = "  ";      // between a key and its action
constexpr const char* kHintGap = "     ";      // between hints on one footer line

// Arrangements, roomiest first; text sizes and spacing are the same in all of them.
// The last two are for small outputs (e.g. scale 2): only as many rows as fit, around the selected one, with
// accent arrows beside the table where rows are hidden; the last also drops the yaw/pitch/roll hint column.
struct MenuArrangement { bool tabs_beside_title, wide_footer, short_hints, windowed = false, hint_column = true; };
constexpr MenuArrangement kArrangements[] = {
    {false, false, false},            // title, then desktop tabs; full hints wrapped to the table width
    {true, false, false},             // tabs beside the title (one header line)
    {true, true, false},              // ... hints may use the output's width (fewer footer lines)
    {true, true, true},               // ... shorter hint wording
    {true, true, true, true},         // ... a window of rows around the selected one
    {true, true, true, true, false},  // ... without the hint column
};
// Scroll cue: a small accent triangle, sized from the card's gaps.
constexpr std::uint32_t kCueH = std::uint32_t(kRowGap) * 3 / 4, kCueW = 2 * kCueH;
// Sliders, in the card's palette: a dim track, the stretch from the flat value to the current one in accent (part
// coverage, so never brighter than the accent), a knob in the text colour. The mouse pointer (render_menu_pointer) is a
// separate small image, a text-colour ring (a disc while a slider is held); the knob's travel stops kPointerR in from
// each end so the ring fits on the slider. Sizes come from the card's spacing.
constexpr float kTrackH = 4, kKnobR = 7, kPointerR = 12, kPointerRing = 2.5f;
constexpr float kFillAlpha = 0.7f;

struct MenuLayout {
    MenuArrangement arrangement{};
    Cell title;
    std::vector<Cell> tabs;
    std::uint32_t tabs_w = 0, tabs_ascent = 0, tabs_descent = 0;
    struct Row { Cell label, hint, value; bool selected; std::uint32_t index; };
    std::vector<Row> rows;                   // the visible rows
    std::uint32_t first_row = 0, total_rows = 0;
    std::uint32_t label_w = 0, hint_w = 0, value_w = 0, slider_w = 0, table_w = 0, rows_h = 0;
    std::vector<Cell> footer;
    std::uint32_t footer_w = 0, footer_h = 0;
    std::uint32_t header_h = 0, content_w = 0, content_h = 0;
};
std::uint32_t tab_rule() { return std::uint32_t(kBorder); }  // selected-tab underline: the border's weight

// Greedy wrap of hint segments into lines no wider than `wrap` (a single segment may exceed it).
std::vector<Cell> footer_lines(const std::vector<std::pair<std::string, std::string>>& hints, std::uint32_t wrap, const HudFont* font) {
    std::vector<Cell> lines;
    std::string line;
    for (const auto& [keys, action] : hints) {
        const std::string segment = keys + kHintKeyGap + action;
        const std::string joined = line.empty() ? segment : line + kHintGap + segment;
        if (!line.empty() && box(card_footer(joined), font).width > wrap) { lines.push_back(cell(card_footer(line), font)); line = segment; }
        else line = joined;
    }
    if (!line.empty()) lines.push_back(cell(card_footer(line), font));
    return lines;
}

MenuLayout layout_menu(const PlacementMenuState& state, const HudFont* font, MenuArrangement a, std::uint32_t avail_content_w,
                       std::uint32_t window = std::uint32_t(kPlacementMenuFields)) {
    MenuLayout L;
    L.arrangement = a;
    L.title = cell(card_title("Placement"), font);
    for (std::uint32_t d = 0; d < kPlacementMenuDesktopCount; ++d) {
        L.tabs.push_back(cell(d == state.desktop ? card_key("XR-" + std::to_string(d + 1)) : card_detail("XR-" + std::to_string(d + 1)), font));
        L.tabs_w += L.tabs.back().box.width + (d ? std::uint32_t(kColumnGap) : 0);
        L.tabs_ascent = std::max(L.tabs_ascent, L.tabs.back().box.ascent); L.tabs_descent = std::max(L.tabs_descent, L.tabs.back().box.descent);
    }
    const auto all = placement_menu_rows(state, font == nullptr);
    L.total_rows = std::uint32_t(all.size());
    window = std::clamp<std::uint32_t>(window, 1, L.total_rows);
    const std::uint32_t selected = state.field < L.total_rows ? state.field : 0;  // keep the selected row in view
    L.first_row = std::min(selected > window / 2 ? selected - window / 2 : 0, L.total_rows - window);
    for (std::uint32_t i = 0; i < L.total_rows; ++i) {  // columns measured over every row: scrolling never resizes the card
        const auto& r = all[i];
        MenuLayout::Row row{cell(card_key(r.label), font), cell(card_detail(a.hint_column ? r.hint : ""), font), cell(card_key(r.value), font),
                            r.selected, i};
        L.label_w = std::max(L.label_w, row.label.box.width); L.hint_w = std::max(L.hint_w, row.hint.box.width);
        L.value_w = std::max(L.value_w, row.value.box.width);
        if (i < L.first_row || i >= L.first_row + window) continue;
        L.rows_h += row_ascent(row.label, row.hint, row.value) + row_descent(row.label, row.hint, row.value) + std::uint32_t(kRowGap);
        L.rows.push_back(std::move(row));
    }
    // The value column always has room for the widest value (sign, three digits, unit), so changing a value never
    // resizes the card or re-wraps the footer.
    for (const char* widest : {"-000%", font ? "-000\xc2\xb0" : "-000 deg"})
        L.value_w = std::max(L.value_w, box(card_key(widest), font).width);
    // Columns: label, hint, slider, value (right-aligned); the selected-row band reaches kRowGap past the text on each
    // side. The slider takes the room the output leaves, between kSliderMinW and kSliderMaxW.
    const bool clipped = L.rows.size() < L.total_rows;
    const std::uint32_t cues = clipped ? 2 * (kCueW + std::uint32_t(kRowGap)) : 0;
    // Half a column gap on each side of the slider: the same spacing the table had before it had sliders.
    const std::uint32_t text_w = L.label_w + (L.hint_w ? std::uint32_t(kColumnGap) / 2 + L.hint_w : 0) + std::uint32_t(kColumnGap) +
                                 L.value_w + 2 * std::uint32_t(kRowGap);
    L.slider_w = kSliderMaxW;
    if (avail_content_w) L.slider_w = std::clamp(avail_content_w > text_w + cues ? avail_content_w - text_w - cues : 0, kSliderMinW, kSliderMaxW);
    L.table_w = text_w + L.slider_w;
    std::uint32_t header_w;
    if (a.tabs_beside_title) {
        header_w = L.title.box.width + std::uint32_t(kColumnGap) + L.tabs_w;
        L.header_h = std::max(L.title.box.ascent, L.tabs_ascent) + std::max(L.title.box.descent, L.tabs_descent) + std::uint32_t(kLineGap) + tab_rule();
    } else {
        header_w = std::max(L.title.box.width, L.tabs_w);
        L.header_h = height(L.title.box) + std::uint32_t(kLineGap) + L.tabs_ascent + L.tabs_descent + std::uint32_t(kLineGap) + tab_rule();
    }
    // Hidden rows: room for the scroll cues on both sides of the table.
    const std::uint32_t body_w = std::max(header_w, L.table_w + cues);
    L.footer = footer_lines(a.short_hints ? kMenuHintsShort : kMenuHints, a.wide_footer ? std::max(body_w, avail_content_w) : body_w, font);
    for (std::size_t i = 0; i < L.footer.size(); ++i) {
        L.footer_w = std::max(L.footer_w, L.footer[i].box.width);
        L.footer_h += height(L.footer[i].box) + (i ? std::uint32_t(kLineGap) : 0);
    }
    L.content_w = std::max(body_w, L.footer_w);
    L.content_h = L.header_h + std::uint32_t(kSectionGap) + L.rows_h + std::uint32_t(kSectionGap) + L.footer_h;
    return L;
}

bool fits(const MenuLayout& L, std::uint32_t max_w, std::uint32_t max_h) {
    return (!max_w || card_size(L.content_w) + 2 * kHudFitMargin <= max_w) && (!max_h || card_size(L.content_h) + 2 * kHudFitMargin <= max_h);
}

MenuLayout choose_layout(const PlacementMenuState& state, const HudFont* font, std::uint32_t max_w, std::uint32_t max_h) {
    const std::uint32_t avail = max_w > 2 * kHudFitMargin + 2 * std::uint32_t(kHelpPad) ? max_w - 2 * kHudFitMargin - 2 * std::uint32_t(kHelpPad) : 0;
    MenuLayout L;
    for (const auto& a : kArrangements) {
        if (a.windowed) continue;
        L = layout_menu(state, font, a, avail);
        if (fits(L, max_w, max_h)) return L;
    }
    // Windowed: the most rows first; at equal rows, keep the hint column if it fits.
    for (std::uint32_t window = std::uint32_t(kPlacementMenuFields) - 1; window >= 1; --window)
        for (const auto& a : kArrangements) {
            if (!a.windowed) continue;
            L = layout_menu(state, font, a, avail, window);
            if (fits(L, max_w, max_h)) return L;
        }
    return L;  // nothing fits: the most compact arrangement, centred (clipped equally on each side)
}

// Desktop tabs on a shared baseline from `left`; the selected one is underlined in the accent colour.
void draw_tabs(HudImage& im, const MenuLayout& L, std::uint32_t desktop, int left, std::uint32_t top, std::uint32_t ascent,
               std::uint32_t rule_top, std::array<float, 3> accent, const HudFont* font) {
    int x = left;
    for (std::uint32_t d = 0; d < L.tabs.size(); ++d) {
        const auto& t = L.tabs[d];
        draw_cell(im, t, x, top, ascent, font);
        if (d == desktop)
            for (std::uint32_t y = 0; y < tab_rule(); ++y)
                for (std::uint32_t dx = 0; dx < t.box.width; ++dx) over(im, x + int(dx), int(rule_top + y), accent, 1.f);
        x += int(t.box.width + std::uint32_t(kColumnGap));
    }
}
}

namespace {
// Disc / ring centred on (cx, cy), hard-edged like the pixel font: every pixel is fully `c` (one of the card's colours)
// or untouched. Anti-aliased edges over the near-opaque track would round to opaque in-between greys.
void draw_disc(HudImage& im, float cx, float cy, float r, std::array<float, 3> c) {
    for (int y = int(std::floor(cy - r - 1)); y <= int(std::ceil(cy + r + 1)); ++y)
        for (int x = int(std::floor(cx - r - 1)); x <= int(std::ceil(cx + r + 1)); ++x)
            if (std::hypot(float(x) + 0.5f - cx, float(y) + 0.5f - cy) <= r) over(im, x, y, c, 1.f);
}
void draw_ring(HudImage& im, float cx, float cy, float r, float width, std::array<float, 3> c) {
    for (int y = int(std::floor(cy - r - width)); y <= int(std::ceil(cy + r + width)); ++y)
        for (int x = int(std::floor(cx - r - width)); x <= int(std::ceil(cx + r + width)); ++x)
            if (std::fabs(std::hypot(float(x) + 0.5f - cx, float(y) + 0.5f - cy) - r) <= width / 2) over(im, x, y, c, 1.f);
}
// Horizontal bar [x0, x1) of kTrackH centred on cy.
void draw_bar(HudImage& im, float x0, float x1, float cy, std::array<float, 3> c, float alpha) {
    if (x1 < x0) std::swap(x0, x1);
    for (int y = int(std::lround(cy - kTrackH / 2)); y < int(std::lround(cy + kTrackH / 2)); ++y)
        for (int x = int(std::lround(x0)); x < int(std::lround(x1)); ++x) over(im, x, y, c, alpha);
}
// Centre line of a scale-2 text row: half a capital (capitals are kGlyphH * zoom * 2 tall in both fonts) above the baseline.
constexpr std::uint32_t kCapHalf = kGlyphH * kPixelFontZoom;
}

std::array<std::pair<std::int32_t, std::int32_t>, kPlacementMenuFields> placement_menu_bounds() {
    std::array<std::pair<std::int32_t, std::int32_t>, kPlacementMenuFields> out{};
    for (std::size_t i = 0; i < kPlacementMenuFields; ++i) out[i] = {kMenuFields[i].lo, kMenuFields[i].hi};
    return out;
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

std::vector<std::string> placement_menu_hints(const HudFont* font, std::uint32_t max_width, std::uint32_t max_height) {
    PlacementMenuState state;  // the footer does not depend on the values
    std::vector<std::string> out;
    for (const auto& line : choose_layout(state, font, max_width, max_height).footer) out.push_back(line.line.text);
    return out;
}

std::vector<std::string> placement_menu_hint_segments(bool short_wording) {
    std::vector<std::string> out;
    for (const auto& [keys, action] : short_wording ? kMenuHintsShort : kMenuHints) out.push_back(keys + kHintKeyGap + action);
    return out;
}

PlacementMenuFit placement_menu_fit(const PlacementMenuState& state, const HudFont* font, std::uint32_t max_width, std::uint32_t max_height) {
    const auto L = choose_layout(state, font, max_width, max_height);
    return {card_size(L.content_w), card_size(L.content_h), L.arrangement.tabs_beside_title, L.arrangement.wide_footer,
            L.arrangement.short_hints, std::uint32_t(L.footer.size()), fits(L, max_width, max_height),
            std::uint32_t(L.rows.size()), L.first_row, L.arrangement.hint_column, L.slider_w};
}

namespace {
// The menu card and, as it is drawn, where its tabs and sliders are (one code path, so they cannot disagree).
HudImage render_menu(const PlacementMenuState& state, std::array<float, 3> accent, const HudFont* font, std::uint32_t max_width,
                     std::uint32_t max_height, MenuGeometry* geometry, bool* fit = nullptr) {
    const auto L = choose_layout(state, font, max_width, max_height);
    if (fit) *fit = fits(L, max_width, max_height);
    HudImage im;
    allocate_card(im, card_size(L.content_w), card_size(L.content_h), accent);
    MenuGeometry g;
    g.width = im.width; g.height = im.height;
    std::uint32_t top = std::uint32_t(kHelpPad);
    auto tabs = [&](int tabs_left, std::uint32_t tabs_top, std::uint32_t ascent, std::uint32_t rule_top) {
        draw_tabs(im, L, state.desktop, tabs_left, tabs_top, ascent, rule_top, accent, font);
        int x = tabs_left;  // each tab's box reaches half the gap to its neighbours, so the selector has no dead space
        for (std::size_t d = 0; d < L.tabs.size() && d < g.tabs.size(); ++d) {
            const int w = int(L.tabs[d].box.width), half = int(kColumnGap) / 2;
            g.tabs[d] = {x - half, int(tabs_top) - int(kLineGap) / 2, x + w + half, int(rule_top + tab_rule()) + int(kLineGap) / 2};
            x += w + int(kColumnGap);
        }
    };
    if (L.arrangement.tabs_beside_title) {
        const std::uint32_t ascent = std::max(L.title.box.ascent, L.tabs_ascent), descent = std::max(L.title.box.descent, L.tabs_descent);
        const int left = int((im.width - (L.title.box.width + std::uint32_t(kColumnGap) + L.tabs_w)) / 2);
        draw_cell(im, L.title, left, top, ascent, font);
        tabs(left + int(L.title.box.width + std::uint32_t(kColumnGap)), top, ascent, top + ascent + descent + std::uint32_t(kLineGap) / 2);
    } else {
        draw_centered(im, L.title, top, font);
        const std::uint32_t tabs_top = top + height(L.title.box) + std::uint32_t(kLineGap);
        tabs(int((im.width - L.tabs_w) / 2), tabs_top, L.tabs_ascent, tabs_top + L.tabs_ascent + L.tabs_descent + std::uint32_t(kLineGap) / 2);
    }
    const auto& placement = state.placements[std::min<std::size_t>(state.desktop, kPlacementMenuDesktopCount - 1)];
    top += L.header_h + std::uint32_t(kSectionGap);
    draw_divider(im, top, accent);
    const int left = int((im.width - L.table_w) / 2);
    // Scroll cues beside the table: an up arrow by the first visible row if rows are hidden above, a down arrow
    // by the last if rows are hidden below.
    auto cue = [&](std::uint32_t row_top, std::uint32_t row_h, bool up) {
        const int cx = left - int(kRowGap) - int(kCueW) / 2, cy = int(row_top + row_h / 2) - int(kCueH) / 2;
        for (std::uint32_t y = 0; y < kCueH; ++y) {
            const std::uint32_t half = (up ? y + 1 : kCueH - y) * kCueW / (2 * kCueH);  // widens downward for up
            for (int dx = -int(half); dx < int(half); ++dx) over(im, cx + dx, cy + int(y), accent, 1.f);
        }
    };
    for (std::size_t i = 0; i < L.rows.size(); ++i) {
        const auto& r = L.rows[i];
        const std::uint32_t ascent = row_ascent(r.label, r.hint, r.value), h = ascent + row_descent(r.label, r.hint, r.value);
        if (r.selected) {  // accent wash behind the row, solid marker (border weight) on its left edge
            const int y0 = int(top) - int(kRowGap) / 2, y1 = int(top + h) + int(kRowGap) / 2;
            for (int y = y0; y < y1; ++y)
                for (int dx = 0; dx < int(L.table_w); ++dx) over(im, left + dx, y, accent, dx < int(kBorder) ? 1.f : kHighlightAlpha);
        }
        const int col = left + int(kRowGap);
        draw_cell(im, r.label, col, top, ascent, font);
        if (!r.hint.line.text.empty()) draw_cell(im, r.hint, col + int(L.label_w + std::uint32_t(kColumnGap) / 2), top, ascent, font);
        draw_cell(im, r.value, left + int(L.table_w) - int(kRowGap) - int(r.value.box.width), top, ascent, font);  // right-aligned
        {  // the slider: its ends are the field's bounds; the knob travels between kPointerR in from each end
            const auto& f = kMenuFields[r.index];
            const float x0 = float(col) + float(L.label_w + (L.hint_w ? std::uint32_t(kColumnGap) / 2 + L.hint_w : 0) + std::uint32_t(kColumnGap) / 2);
            const float lo_x = x0 + kPointerR, span = float(L.slider_w) - 2 * kPointerR, cy = float(top + ascent - std::min(ascent, kCapHalf));
            auto at = [&](std::int32_t v) { return lo_x + span * float(std::clamp(v, f.lo, f.hi) - f.lo) / float(f.hi - f.lo); };
            const float knob = at(placement[r.index]);
            draw_bar(im, x0, x0 + float(L.slider_w), cy, kCardDim, 0.85f);  // part coverage: no opaque blends at the knob edge
            draw_bar(im, at(f.flat), knob, cy, accent, kFillAlpha);
            draw_disc(im, knob, cy, kKnobR, kCardText);
            g.sliders.push_back({r.index, int(top) - int(kRowGap) / 2, int(top + h) + int(kRowGap) / 2, lo_x, lo_x + span});
        }
        if (i == 0 && L.first_row > 0) cue(top, h, true);
        if (i + 1 == L.rows.size() && L.first_row + L.rows.size() < L.total_rows) cue(top, h, false);
        top += h + std::uint32_t(kRowGap);
    }
    top += std::uint32_t(kSectionGap);
    for (const auto& f : L.footer) { draw_centered(im, f, top, font); top += height(f.box) + std::uint32_t(kLineGap); }
    if (geometry) *geometry = std::move(g);
    return im;
}
}

HudImage render_placement_menu_card(const PlacementMenuState& state, std::array<float, 3> accent, const HudFont* font,
                                    std::uint32_t max_width, std::uint32_t max_height) {
    return render_menu(state, accent, font, max_width, max_height, nullptr);
}

MenuGeometry placement_menu_geometry(const PlacementMenuState& state, const HudFont* font, std::uint32_t max_width, std::uint32_t max_height) {
    MenuGeometry g;
    render_menu(state, kCardAccent, font, max_width, max_height, &g);
    return g;
}

std::string menu_geometry_json(const MenuGeometry& g) {
    std::string out = "{\"menu_geometry\":{\"w\":" + std::to_string(g.width) + ",\"h\":" + std::to_string(g.height) + ",\"tabs\":[";
    for (std::size_t i = 0; i < g.tabs.size(); ++i) {
        out += i ? ",[" : "[";
        for (std::size_t k = 0; k < 4; ++k) out += (k ? "," : "") + std::to_string(g.tabs[i][k]);
        out += "]";
    }
    out += "],\"sliders\":[";
    for (std::size_t i = 0; i < g.sliders.size(); ++i) {
        const auto& s = g.sliders[i];
        out += (i ? ",[" : "[") + std::to_string(s.field) + "," + std::to_string(s.top) + "," + std::to_string(s.bottom) + "," +
               std::to_string(std::lround(s.lo_x)) + "," + std::to_string(std::lround(s.hi_x)) + "]";
    }
    return out + "]}}";
}

HudImage render_menu_pointer(bool held) {
    HudImage im;
    const auto side = std::uint32_t(2 * kPointerR + 2);
    im.width = im.height = side;
    im.rgba.assign(std::size_t(side) * side * 4, 0);
    const float c = float(side) / 2;  // hot spot: the image's middle
    if (held) draw_disc(im, c, c, kPointerR - kPointerRing, kCardText);
    draw_ring(im, c, c, kPointerR - kPointerRing / 2, kPointerRing, kCardText);
    return im;
}

CardOrigin centered_card_origin(std::uint32_t output_w, std::uint32_t output_h, std::uint32_t card_w, std::uint32_t card_h, float drop_px) {
    // GlHud works bottom-up: bottom edge at floor((H - h) / 2 - drop). From the top that is H - bottom - h.
    const float bottom = std::floor((float(output_h) - float(card_h)) / 2 - drop_px);
    return {std::floor((float(output_w) - float(card_w)) / 2), float(output_h) - bottom - float(card_h)};
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

std::vector<HudLine> typing_lines(bool on) {
    return {{"Keys", 2, kCardDim}, {on ? "Typing" : "XR control", 4, kCardText, true},
            {on ? "XR keys off: arrows / Space / Tab / Esc work as usual" : "XR keys back on", 2, kCardDim},
            {on ? "Ctrl+Alt+T for XR control" : "Ctrl+Alt+T for typing", 1, kCardDim}};
}

bool StatusPopups::update(std::uint8_t mode, bool cursor_follow, bool prediction, bool typing, double now_ms) {
    if (mode > 2) mode = 0;
    const bool first = !seen_, mode_changed = first || mode != mode_, cursor_changed = !first && cursor_follow != cursor_follow_;
    const bool prediction_changed = !first && prediction != prediction_;
    const bool typing_changed = first ? typing : typing != typing_;  // a first update in typing mode says so
    seen_ = true; mode_ = mode; cursor_follow_ = cursor_follow; prediction_ = prediction; typing_ = typing;
    if (!mode_changed && !cursor_changed && !prediction_changed && !typing_changed) return false;
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
    if (typing_changed) {
        if (!lines.empty()) lines.push_back({"", 1, kCardDim});
        for (auto& l : typing_lines(typing)) lines.push_back(std::move(l));
    }
    const bool typing_only = typing_changed && !mode_changed && !cursor_changed && !prediction_changed;
    image_ = render_hud_card(lines, typing_only ? kCardAccent : kAccent, font_.get());
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
        image_ = render_help_card(rows_, kCardAccent, font_.get());
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
        dirty_ = true; ++generation_; changed = true;  // rasterised on the next image() (once per displayed frame)
    }
    if (fade_.set(state.open, now_ms)) {  // closing keeps the last card for the fade-out
        if (state.open) ++shown_; else ++hidden_;
        changed = true;
    }
    return changed;
}
void PlacementMenuOverlay::ensure() const {
    if (!dirty_) return;
    dirty_ = false;
    MenuGeometry g;
    image_ = render_menu(rendered_, kCardAccent, font_.get(), max_w_, max_h_, &g, &fits_);
    ++renders_;
    if (!(g == geometry_) || !geometry_generation_) { geometry_ = std::move(g); ++geometry_generation_; }
}
bool PlacementMenuOverlay::set_viewport(std::uint32_t width, std::uint32_t height) {
    if (width == max_w_ && height == max_h_) return false;
    max_w_ = width; max_h_ = height;
    if (!generation_) return false;
    dirty_ = true; ++generation_;
    return true;
}
bool PlacementMenuOverlay::set_pointer(bool visible, std::int32_t x, std::int32_t y, bool held) {
    if (visible == pointer_on_ && x == pointer_x_ && y == pointer_y_ && held == pointer_held_) return false;
    pointer_on_ = visible; pointer_x_ = x; pointer_y_ = y; pointer_held_ = held;
    return true;
}
void PlacementMenuOverlay::set_font(std::unique_ptr<HudFont> font) { font_ = std::move(font); }
float PlacementMenuOverlay::alpha(double now_ms) const { return generation_ ? fade_.alpha(now_ms) : 0.f; }
float PlacementMenuOverlay::offset_px(double now_ms) const { return (1 - alpha(now_ms)) * kHelpRisePx; }
bool PlacementMenuOverlay::animating(double now_ms) const { return generation_ && fade_.animating(now_ms); }

// VR video controls card (video.hpp): the help card's parts, so the OLED dimming cannot drift from it. Title, state and
// time, a progress bar, the format, an optional error line, then the key hints and the idle note.
HudImage render_video_card(const VideoCardState& v, std::array<float, 3> accent, const HudFont* font) {
    if (accent == std::array<float, 3>{}) accent = kCardAccent;
    const bool ascii = font == nullptr;
    const char* state = v.state == VideoState::playing ? "Playing" : v.state == VideoState::paused ? "Paused"
                      : v.state == VideoState::ended ? "Ended" : v.state == VideoState::error ? "Cannot play this video" : "Loading";
    const std::string time = format_clock(v.position_s) + " / " + (v.duration_s ? format_clock(v.duration_s) : std::string("--:--"));
    const Cell title = cell(card_title(v.title.empty() ? std::string("VR video") : v.title), font);
    const Cell status = cell(card_key(state), font), clock = cell(card_detail(time), font);
    const Cell format = cell(card_detail(video_format_label(v.format, ascii)), font);
    const Cell detail = cell(card_detail(v.detail), font);
    std::vector<Cell> hints;
    {
        const auto all = video_card_hints(ascii);
        std::string line;
        for (std::size_t i = 0; i < all.size(); ++i) {
            line += (line.empty() ? "" : kHintGap) + all[i];
            if (i % 3 == 2 || i + 1 == all.size()) { hints.push_back(cell(card_footer(line), font)); line.clear(); }
        }
        hints.push_back(cell(card_footer("Closes after " + std::to_string(kOverlayIdleCloseS) + " s idle. Any video key shows it."), font));
    }
    const std::uint32_t bar_h = std::uint32_t(kBorder) * 3, status_w = status.box.width + std::uint32_t(kColumnGap) + clock.box.width;
    std::uint32_t content_w = std::max({title.box.width, status_w, format.box.width, detail.box.width, 560u});
    for (const auto& h : hints) content_w = std::max(content_w, h.box.width);
    const std::uint32_t status_h = row_ascent(status, clock) + row_descent(status, clock);
    std::uint32_t content_h = height(title.box) + std::uint32_t(kSectionGap) + status_h + std::uint32_t(kRowGap) + bar_h +
                              std::uint32_t(kRowGap) + height(format.box) + std::uint32_t(kSectionGap);
    if (!v.detail.empty()) content_h += height(detail.box) + std::uint32_t(kRowGap);
    for (std::size_t i = 0; i < hints.size(); ++i) content_h += height(hints[i].box) + (i + 1 < hints.size() ? std::uint32_t(kRowGap) / 2 : 0);
    HudImage im;
    allocate_card(im, card_size(content_w), card_size(content_h), accent);
    const int left = int(kHelpPad);
    std::uint32_t top = std::uint32_t(kHelpPad);
    draw_centered(im, title, top, font);
    top += height(title.box) + std::uint32_t(kSectionGap);
    draw_divider(im, top, accent);
    const std::uint32_t ascent = row_ascent(status, clock);
    draw_cell(im, status, left, top, ascent, font);
    draw_cell(im, clock, left + int(content_w - clock.box.width), top, ascent, font);
    top += status_h + std::uint32_t(kRowGap);
    // Progress: a faint accent track, filled up to the position. Accent only (dimmed), never white.
    const float progress = v.duration_s ? std::clamp(float(v.position_s) / float(v.duration_s), 0.f, 1.f) : 0.f;
    const auto filled = std::uint32_t(std::lround(progress * float(content_w)));
    for (std::uint32_t y = 0; y < bar_h; ++y)
        for (std::uint32_t x = 0; x < content_w; ++x)
            over(im, left + int(x), int(top + y), accent, x < filled ? 1.f : kHighlightAlpha);
    top += bar_h + std::uint32_t(kRowGap);
    draw_cell(im, format, left, top, format.box.ascent, font);
    top += height(format.box);
    if (!v.detail.empty()) { top += std::uint32_t(kRowGap); draw_cell(im, detail, left, top, detail.box.ascent, font); top += height(detail.box); }
    top += std::uint32_t(kSectionGap);
    draw_divider(im, top, accent);
    for (const auto& h : hints) { draw_centered(im, h, top, font); top += height(h.box) + std::uint32_t(kRowGap) / 2; }
    return im;
}
}
