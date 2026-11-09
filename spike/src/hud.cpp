#include "xr/hud.hpp"
#include "xr/glass_theme.hpp"    // glass_theme_scheme: the cards share the pills' M3 scheme
#include "xr/material_draw.hpp"
#include "xr/video.hpp"  // render_video_card: built from the help card parts below
#include "xr/pose.hpp"   // help_geometry_json: the card mapped onto the overview canvas
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

// Lines without a role (HudLine::role) keep their own colours; these are the roles' look-alikes for them, under the cap:
// text at ~70% and dim text at ~46% read on the near-black card.
constexpr std::array<float, 3> kCardText{0.70f, 0.70f, 0.70f}, kCardDim{0.46f, 0.46f, 0.46f};
// Material Design 3 card chrome, in output pixels (the card is drawn 1:1 in the glasses): an extra-large rounded surface
// container (high) at kBackgroundAlpha (a hint of the desktop behind), with a 1 px outline-variant edge.
constexpr float kBackgroundAlpha = 0.9f;
constexpr float kPad = 22, kLineGap = 8, kRadius = kMdShapeExtraLarge, kBorder = 1;
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
// Installed-font em size and pixel-font zoom of a line: from `px` when set, else from its scale step.
std::uint32_t em_px(const HudLine& line) { return line.px ? std::clamp<std::uint32_t>(line.px, 6, 160) : std::clamp<std::uint32_t>(line.scale, 1, 8) * kEmPerScale; }
std::uint32_t pixel_zoom(const HudLine& line) {
    if (!line.px) return std::clamp<std::uint32_t>(line.scale, 1, 8) * kPixelFontZoom;
    return std::max<std::uint32_t>(1, (em_px(line) * kPixelFontZoom + kEmPerScale / 2) / kEmPerScale);  // nearest; capitals match
}
LineBox box(const HudLine& line, const HudFont* font) {
    if (!font) { const auto z = pixel_zoom(line); return {hud_text_width(line.text, z), kGlyphH * z, 0}; }
    const auto m = font->metrics(em_px(line), line.bold);
    return {font->measure(line.text, em_px(line), line.bold), m.ascent, m.descent};
}
// Size `im` and paint the rounded translucent card (M3 surface container high) with its outline-variant edge.
void allocate_card(HudImage& im, std::uint32_t width, std::uint32_t height, const MaterialScheme& m) {
    const auto& back = m.surface_container_high;
    const auto& rim = m.outline_variant;
    im.width = width; im.height = height;
    im.rgba.assign(std::size_t(im.width) * im.height * 4, 0);
    const float w = float(im.width), h = float(im.height);
    // Most of the card is plain translucent background. Outside the corner squares the signed distance is minus the
    // distance to the nearest edge, so a pixel at least kBorder + 0.5 inside is exactly the background: fill it
    // directly instead of evaluating the rounded rectangle (the same bytes; ~20x faster on a menu-sized card).
    std::array<std::uint8_t, 4> fill{};
    for (int i = 0; i < 3; ++i) fill[std::size_t(i)] = std::uint8_t(std::lround(back[std::size_t(i)] * kBackgroundAlpha * 255));
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
            over(im, int(x), int(y), rim, outer);
            auto* p = &im.rgba[(std::size_t(y) * im.width + x) * 4];
            if (inner > 0) {  // replace the edge colour with the translucent background inside the border
                for (int i = 0; i < 3; ++i) p[i] = std::uint8_t(std::lround((back[std::size_t(i)] * kBackgroundAlpha * inner + rim[std::size_t(i)] * outer * (1 - inner)) * 255));
                p[3] = std::uint8_t(std::lround((kBackgroundAlpha * inner + outer * (1 - inner)) * 255));
            }
        }
}
// Draw one line with its top edge at `top` and left edge at `left`, in its own colour (cells and lines are painted with
// their role's colour before they get here: painted / cell).
void draw_line(HudImage& im, const HudLine& line, const LineBox& b, int left, std::uint32_t top, const HudFont* font) {
    if (font) {
        font->draw(line.text, em_px(line), line.bold, left, int(top + b.ascent),
                   [&](int x, int y, float coverage) { over(im, x, y, line.color, coverage); });
        return;
    }
    const auto z = pixel_zoom(line);
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
// `line` in its role's colour from `m` (a line without a role keeps its own).
HudLine painted(HudLine line, const MaterialScheme& m) { line.color = hud_line_color(line, m); return line; }
}

std::array<float, 3> hud_line_color(const HudLine& line, const MaterialScheme& m) {
    switch (line.role) {
    case HudRole::on_surface: return m.on_surface;
    case HudRole::on_surface_variant: return m.on_surface_variant;
    case HudRole::outline: return m.outline;
    case HudRole::primary: return m.primary;
    case HudRole::on_secondary_container: return m.on_secondary_container;
    case HudRole::none: break;
    }
    return line.color;
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
    const auto& m = glass_theme_scheme(accent);
    HudImage im;
    std::vector<LineBox> boxes;
    std::uint32_t content_w = 0, content_h = 0;
    for (std::size_t i = 0; i < lines.size(); ++i) {
        boxes.push_back(box(lines[i], font));
        content_w = std::max(content_w, boxes.back().width);
        content_h += boxes.back().ascent + boxes.back().descent + (i ? std::uint32_t(kLineGap) : 0);
    }
    allocate_card(im, content_w + 2 * std::uint32_t(kPad), content_h + 2 * std::uint32_t(kPad), m);
    std::uint32_t top = std::uint32_t(kPad);
    for (std::size_t i = 0; i < lines.size(); ++i) {
        const auto& b = boxes[i];
        draw_line(im, painted(lines[i], m), b, int((im.width - b.width) / 2), top, font);
        top += b.ascent + b.descent + std::uint32_t(kLineGap);
    }
    return im;
}

namespace {
// Help-card building blocks. The help card and the VR video card are both drawn from these, so
// type, colours, sizes and spacing cannot drift apart.
// M3 dividers (outline variant) under the card title and between sections, a little translucent so they never count as
// ink; the video's progress is M3's linear indicator (kProgressH, fully rounded: primary on a secondary-container track).
constexpr float kDividerAlpha = 0.8f;
constexpr std::uint32_t kProgressH = 4;
HudLine card_title(std::string text) { return {std::move(text), 2, kCardText, true, 0, HudRole::on_surface}; }
HudLine card_key(std::string text) { return {std::move(text), 2, kCardText, true, 0, HudRole::on_surface}; }   // left column: bold
HudLine card_detail(std::string text) { return {std::move(text), 2, kCardDim, false, 0, HudRole::on_surface_variant}; }  // descriptions
HudLine card_footer(std::string text) { return {std::move(text), 1, kCardDim, false, 0, HudRole::outline}; }  // key hints: small
std::uint32_t height(const LineBox& b) { return b.ascent + b.descent; }
struct Cell { HudLine line; LineBox box; };
Cell cell(HudLine line, const HudFont* font, const MaterialScheme& m) { line = painted(std::move(line), m); auto b = box(line, font); return {std::move(line), b}; }
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
void draw_divider(HudImage& im, std::uint32_t section_top, const MaterialScheme& m) {
    for (std::uint32_t x = std::uint32_t(kHelpPad); x + std::uint32_t(kHelpPad) < im.width; ++x)
        over(im, int(x), int(section_top - std::uint32_t(kSectionGap) / 2), m.outline_variant, kDividerAlpha);
}
std::uint32_t card_size(std::uint32_t content) { return content + 2 * std::uint32_t(kHelpPad); }
}

namespace {
// Help card text and gaps, roomiest first. The card's chrome (padding, border, radius, colours) is the menu's; its text
// is smaller and its gaps tighter so every bind fits at 1920x1200 @1.25 with room for a few more rows. The layout takes
// the first step that fits every row on one page; when none does, the smallest step split into pages (hud.hpp). Row
// text stays at least kHelpRowPxMin (capitals ~13 px) on the glasses.
struct HelpStyle { std::uint32_t title_px, row_px, footer_px, row_gap, section_gap, column_gap; };
constexpr HelpStyle kHelpStyles[] = {
    {32, 30, 20, 8, 16, 36}, {30, 28, 19, 7, 15, 34}, {28, 26, 18, 6, 14, 32}, {26, 24, 17, 6, 12, 30},
    {24, 22, 16, 5, 11, 28}, {22, 20, 15, 4, 10, 26}, {20, 18, 14, 4, 9, 24},
};
static_assert(kHelpStyles[0].row_px == kHelpRowPxMax && kHelpStyles[std::size(kHelpStyles) - 1].row_px == kHelpRowPxMin);
constexpr float kSectionRuleAlpha = 0.5f;  // a fainter divider between sections than under the title

struct HelpLayout {
    HelpStyle style{};
    Cell title, footer, hint;
    std::vector<std::pair<Cell, Cell>> table;
    std::vector<bool> starts;                               // row i starts a section (never row 0)
    std::vector<std::pair<std::size_t, std::size_t>> pages;  // [begin, end) rows per page
    std::uint32_t keys_w = 0, table_w = 0, content_w = 0, content_h = 0;
    std::uint32_t nav_h = 0, arrow = 0, nav_gap = 0;        // paged footer: indicator line height, arrow size, spacing
};
std::uint32_t row_height(const HelpLayout& L, std::size_t i) { return row_ascent(L.table[i].first, L.table[i].second) + row_descent(L.table[i].first, L.table[i].second); }
std::uint32_t gap_before(const HelpLayout& L, std::size_t i) { return L.starts[i] ? L.style.section_gap : L.style.row_gap; }
std::uint32_t rows_height(const HelpLayout& L, std::size_t b, std::size_t e) {
    std::uint32_t h = 0;
    for (std::size_t i = b; i < e; ++i) h += row_height(L, i) + (i > b ? gap_before(L, i) : 0);
    return h;
}
Cell page_indicator(const HelpLayout& L, std::size_t page, const HudFont* font, const MaterialScheme& m) {
    return cell({std::to_string(page + 1) + " / " + std::to_string(L.pages.size()), 1, kCardText, true, L.style.footer_px, HudRole::on_surface},
                font, m);
}
// Rows below the title and above the footer: one page if `avail` (0 = unbounded) holds them all, else greedy pages that
// keep a section together when it fits a page; a section taller than a page is split row by row. Never an empty page.
void paginate(HelpLayout& L, std::uint32_t avail) {
    const std::size_t n = L.table.size();
    L.pages.clear();
    if (!avail || rows_height(L, 0, n) <= avail) { L.pages.push_back({0, n}); return; }
    std::size_t i = 0;
    while (i < n) {
        const std::size_t b = i;
        std::uint32_t used = 0;
        while (i < n) {
            std::size_t e = i + 1;
            while (e < n && !L.starts[e]) ++e;  // the section (or its rest) from row i
            const std::uint32_t add = (i > b ? gap_before(L, i) : 0) + rows_height(L, i, e);
            if (used + add <= avail) { used += add; i = e; continue; }
            if (i > b) break;  // the next page takes this section
            while (i < n && (i == b || used + gap_before(L, i) + row_height(L, i) <= avail)) {  // too tall for any page
                used += (i > b ? gap_before(L, i) : 0) + row_height(L, i);
                ++i;
            }
            break;
        }
        L.pages.push_back({b, i});
    }
}
// Fixed parts around the rows: title + gap above, gap + footer below (a paged card adds the indicator line and the hint).
std::uint32_t head_height(const HelpLayout& L) { return height(L.title.box) + L.style.section_gap; }
std::uint32_t foot_height(const HelpLayout& L, bool paged) {
    std::uint32_t h = L.style.section_gap + height(L.footer.box);
    if (paged) h += L.nav_h + L.style.row_gap + height(L.hint.box) + L.style.row_gap / 2;
    return h;
}
HelpLayout layout_help(const std::vector<HelpRow>& rows, const HudFont* font, const HelpStyle& st, std::uint32_t max_h,
                       const MaterialScheme& m) {
    HelpLayout L;
    L.style = st;
    L.title = cell({"Keyboard shortcuts", 1, kCardText, true, st.title_px, HudRole::on_surface}, font, m);
    L.footer = cell({"Ctrl+Alt+H to close. Help and menu close after " + std::to_string(kOverlayIdleCloseS) + " s idle.", 1, kCardDim,
                     false, st.footer_px, HudRole::outline}, font, m);
    L.hint = cell({kHelpPageHint, 1, kCardDim, false, st.footer_px, HudRole::outline}, font, m);
    std::uint32_t actions_w = 0;
    for (std::size_t i = 0; i < rows.size(); ++i) {
        const auto& r = rows[i];
        L.table.push_back({cell({r.keys, 1, kCardText, true, st.row_px, HudRole::on_surface}, font, m),
                           cell({r.action, 1, kCardDim, false, st.row_px, HudRole::on_surface_variant}, font, m)});
        L.starts.push_back(i > 0 && r.section);
        const auto& [k, a] = L.table.back();
        L.keys_w = std::max(L.keys_w, k.box.width); actions_w = std::max(actions_w, a.box.width);
    }
    L.table_w = L.table.empty() ? 0 : L.keys_w + st.column_gap + actions_w;
    // Paged footer line: "<  12 / 34  >" with drawn arrows sized to the indicator's capitals.
    const Cell probe = cell({"0 / 0", 1, kCardText, true, st.footer_px}, font, m);
    L.arrow = std::max<std::uint32_t>(10, probe.box.ascent * 4 / 5);
    L.nav_h = std::max(height(probe.box), L.arrow);
    L.nav_gap = st.column_gap;
    const std::uint32_t chrome = 2 * kHudFitMargin + 2 * std::uint32_t(kHelpPad);
    const std::uint32_t single = head_height(L) + rows_height(L, 0, L.table.size()) + foot_height(L, false);
    const bool one_page = !max_h || single + chrome <= max_h;
    const std::uint32_t fixed = head_height(L) + foot_height(L, true) + chrome;
    paginate(L, one_page ? 0 : (max_h > fixed ? max_h - fixed : 1));
    const bool paged = L.pages.size() > 1;
    std::uint32_t tallest = 0;
    for (const auto& [b, e] : L.pages) tallest = std::max(tallest, rows_height(L, b, e));
    L.content_w = std::max({L.title.box.width, L.footer.box.width, L.table_w});
    if (paged) {
        const std::uint32_t nav_w = 2 * L.arrow + 2 * L.nav_gap + page_indicator(L, L.pages.size() - 1, font, m).box.width;
        L.content_w = std::max({L.content_w, L.hint.box.width, nav_w});
    }
    L.content_h = head_height(L) + tallest + foot_height(L, paged);
    return L;
}
bool help_fits(const HelpLayout& L, std::uint32_t max_w, std::uint32_t max_h) {
    return (!max_w || card_size(L.content_w) + 2 * kHudFitMargin <= max_w) && (!max_h || card_size(L.content_h) + 2 * kHudFitMargin <= max_h);
}
HelpLayout choose_help_layout(const std::vector<HelpRow>& rows, const HudFont* font, std::uint32_t max_w, std::uint32_t max_h,
                              const MaterialScheme& m) {
    HelpLayout L;
    for (const auto& st : kHelpStyles) {
        L = layout_help(rows, font, st, max_h, m);
        if (L.pages.size() == 1 && help_fits(L, max_w, max_h)) return L;
    }
    return L;  // the smallest text, in pages (or, if a row alone is too big, centred and clipped: fits=false)
}
// Arrow pointing left (dir -1) or right (+1) in the square at (x, y) of side `size`, anti-aliased (4x4 samples).
void draw_arrow(HudImage& im, int x, int y, std::uint32_t size, int dir, std::array<float, 3> color) {
    const float s = float(size), half = s / 2, tip = dir < 0 ? 0.f : s, base = dir < 0 ? s * 0.8f : s * 0.2f;
    for (std::uint32_t py = 0; py < size; ++py)
        for (std::uint32_t px = 0; px < size; ++px) {
            int inside = 0;
            for (int sy = 0; sy < 4; ++sy)
                for (int sx = 0; sx < 4; ++sx) {
                    const float fx = float(px) + (float(sx) + 0.5f) / 4, fy = float(py) + (float(sy) + 0.5f) / 4;
                    const float t = (fx - base) / (tip - base);  // 0 at the base, 1 at the tip
                    if (t >= 0 && t <= 1 && std::fabs(fy - half) <= half * (1 - t)) ++inside;
                }
            if (inside) over(im, x + int(px), y + int(py), color, float(inside) / 16.f);
        }
}
}

HelpCardFit help_card_fit(const std::vector<HelpRow>& rows, const HudFont* font, std::uint32_t max_width, std::uint32_t max_height) {
    const auto L = choose_help_layout(rows, font, max_width, max_height, glass_theme_scheme(kMdDefaultSeed));  // colours do not change the fit
    HelpCardFit fit{card_size(L.content_w), card_size(L.content_h), L.style.row_px, help_fits(L, max_width, max_height), {}};
    for (const auto& [b, e] : L.pages) fit.page_rows.push_back(std::uint32_t(e - b));
    return fit;
}

HudImage render_help_card(const std::vector<HelpRow>& rows, std::array<float, 3> accent, const HudFont* font, std::uint32_t max_width,
                          std::uint32_t max_height, std::uint32_t page, HelpCardControls* controls) {
    const auto& m = glass_theme_scheme(accent);
    const auto L = choose_help_layout(rows, font, max_width, max_height, m);
    const auto& st = L.style;
    const std::size_t pages = L.pages.size(), shown = page % pages;
    const bool paged = pages > 1;
    if (controls) *controls = {};
    HudImage im;
    allocate_card(im, card_size(L.content_w), card_size(L.content_h), m);
    std::uint32_t top = std::uint32_t(kHelpPad);
    draw_centered(im, L.title, top, font);
    top += height(L.title.box) + st.section_gap;
    for (std::uint32_t x = std::uint32_t(kHelpPad); x + std::uint32_t(kHelpPad) < im.width; ++x)  // divider in the gap's middle
        over(im, int(x), int(top - st.section_gap / 2), m.outline_variant, kDividerAlpha);
    const int left = int((im.width - L.table_w) / 2);
    const auto [b, e] = L.pages[shown];
    for (std::size_t i = b; i < e; ++i) {
        const auto& [k, a] = L.table[i];
        if (i > b) {
            top += gap_before(L, i);
            if (L.starts[i])  // a fainter divider between sections, across the table
                for (std::uint32_t x = 0; x < L.table_w; ++x) over(im, left + int(x), int(top - st.section_gap / 2), m.outline_variant, kSectionRuleAlpha);
        }
        const std::uint32_t ascent = row_ascent(k, a);
        draw_cell(im, k, left, top, ascent, font);
        draw_cell(im, a, left + int(L.keys_w + st.column_gap), top, ascent, font);
        top += ascent + row_descent(k, a);
    }
    // The footer sits at the bottom: on a short last page the rows end higher, the card keeps its size.
    top = im.height - std::uint32_t(kHelpPad) - foot_height(L, paged) + st.section_gap;
    if (paged) {
        const Cell indicator = page_indicator(L, shown, font, m);
        const std::uint32_t nav_w = 2 * L.arrow + 2 * L.nav_gap + indicator.box.width;
        const int nav_left = int((im.width - nav_w) / 2);
        const int arrow_top = int(top + (L.nav_h - L.arrow) / 2);
        const int text_left = nav_left + int(L.arrow + L.nav_gap), next_left = text_left + int(indicator.box.width + L.nav_gap);
        draw_arrow(im, nav_left, arrow_top, L.arrow, -1, m.on_surface_variant);  // M3 icon buttons (arrow_left / arrow_right)
        draw_cell(im, indicator, text_left, top + (L.nav_h - height(indicator.box)) / 2, indicator.box.ascent, font);
        draw_arrow(im, next_left, arrow_top, L.arrow, 1, m.on_surface_variant);
        if (controls) {  // generous click targets: each half of the gap, and the line's height plus half a gap either side
            const int pad = int(L.nav_gap / 2), y = int(top) - int(st.row_gap), h = int(L.nav_h + 2 * st.row_gap);
            controls->prev = {nav_left - pad, y, int(L.arrow) + 2 * pad, h};
            controls->indicator = {text_left - pad / 2, y, int(indicator.box.width) + pad, h};
            controls->next = {next_left - pad, y, int(L.arrow) + 2 * pad, h};
        }
        top += L.nav_h + st.row_gap;
        draw_centered(im, L.hint, top, font);
        top += height(L.hint.box) + st.row_gap / 2;
    }
    draw_centered(im, L.footer, top, font);
    return im;
}

std::string help_geometry_json(const std::array<float, 9>& view, std::uint32_t width, std::uint32_t height, float half_w,
                               float half_h, CardOrigin origin, std::uint32_t card_w, std::uint32_t card_h,
                               const HelpCardControls& controls) {
    if (!width || !height || half_w <= 0 || half_h <= 0) return {};
    const Mat3 inverse = transpose(view);  // rotations: the inverse is the transpose
    const float W = float(width), H = float(height);
    std::string out = "{\"help_geometry\":{";
    bool first = true;
    auto rect = [&](const char* name, float x, float y, float w, float h) {
        float lo_x = 1e9f, lo_y = 1e9f, hi_x = -1e9f, hi_y = -1e9f;
        for (const auto& [cx, cy] : {std::pair{x, y}, std::pair{x + w, y}, std::pair{x, y + h}, std::pair{x + w, y + h}}) {
            // Output px -> head-locked NDC -> the canvas plane (world, z = -1) -> canvas px.
            const auto c = project(inverse, 2 * cx / W - 1, 1 - 2 * cy / H, half_w, half_h);
            if (!c) return false;
            const float px = ((*c)[0] + 1) / 2 * W, py = (1 - (*c)[1]) / 2 * H;
            lo_x = std::min(lo_x, px); lo_y = std::min(lo_y, py); hi_x = std::max(hi_x, px); hi_y = std::max(hi_y, py);
        }
        const auto clamp = [](float v) { return long(std::lround(std::clamp(v, -8192.f, 8192.f))); };
        const long l = clamp(lo_x), t = clamp(lo_y), r = clamp(hi_x), bo = clamp(hi_y);
        out += std::string(first ? "" : ",") + "\"" + name + "\":[" + std::to_string(l) + "," + std::to_string(t) + "," +
               std::to_string(std::max(1L, r - l)) + "," + std::to_string(std::max(1L, bo - t)) + "]";
        first = false;
        return true;
    };
    if (!rect("card", origin.x, origin.y, float(card_w), float(card_h))) return {};
    for (const auto& [name, r] : {std::pair{"prev", controls.prev}, std::pair{"indicator", controls.indicator}, std::pair{"next", controls.next}})
        if (!r.empty() && !rect(name, origin.x + float(r.x), origin.y + float(r.y), float(r.w), float(r.h))) return {};
    return out + "}}";
}

namespace {
// Placement menu: field order matches ipc.hpp WirePlacement / geometry.py PLACEMENT_KEYS.
// lo/hi: the slider's ends (ipc.cpp kPlacementBounds); flat: the flat-default value the slider fills from. tenths: the
// value is in tenths of a degree (the orbit round you, tools/native_spike/orbit.py), shown as whole degrees.
struct MenuField { const char* label; const char* hint; bool percent, is_signed, tenths; std::int32_t lo, hi, flat; };
constexpr MenuField kMenuFields[kPlacementMenuFields] = {
    {"Left / right", "", false, true, true, -1800, 1800, 0}, {"Down / up", "", false, true, true, -900, 900, 0},
    {"Distance", "", true, false, false, 10, 200, 100}, {"Curvature", "", false, false, false, 0, 120, 0}};
constexpr const char* kHintGap = "     ";      // between hints on one footer line (video card)
}

std::array<std::pair<std::int32_t, std::int32_t>, kPlacementMenuFields> placement_menu_bounds() {
    std::array<std::pair<std::int32_t, std::int32_t>, kPlacementMenuFields> out{};
    for (std::size_t i = 0; i < kPlacementMenuFields; ++i) out[i] = {kMenuFields[i].lo, kMenuFields[i].hi};
    return out;
}

MenuPlacement placement_menu_flat() {
    MenuPlacement out{};
    for (std::size_t i = 0; i < kPlacementMenuFields; ++i) out[i] = kMenuFields[i].flat;
    return out;
}

std::vector<PlacementMenuRow> placement_menu_rows(const PlacementMenuState& state, bool ascii_units) {
    const auto& p = state.placements[std::min<std::size_t>(state.desktop, kPlacementMenuDesktopCount - 1)];
    std::vector<PlacementMenuRow> rows;
    for (std::size_t i = 0; i < kPlacementMenuFields; ++i) {
        const auto& f = kMenuFields[i];
        const std::int32_t v = p[i];
        std::string value = (f.is_signed && v > 0 ? "+" : "") + std::to_string(v);
        if (f.tenths) {
            // Whole degrees, rounded half away from zero ("+13°", "-180°"): "-179.9°" would not fit the value column
            // (kPanelW - kPanelPad - kPanelTrackX1) with the system font, nor "+180 deg" with the pixel font.
            const std::int32_t whole = ((v < 0 ? -v : v) + 5) / 10;
            const char* sign = whole == 0 ? "" : v < 0 ? "-" : f.is_signed ? "+" : "";
            value = sign + std::to_string(whole) + (ascii_units ? "deg" : "\xc2\xb0");
        } else value += f.percent ? "%" : ascii_units ? " deg" : "\xc2\xb0";  // U+00B0 DEGREE SIGN
        rows.push_back({f.label, f.hint, std::move(value), state.field == i});
    }
    return rows;
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

namespace {
// A pop-up section's lines (hud.hpp view_mode_lines): label, value, hint, key.
HudLine popup_label(std::string text) { return {std::move(text), 2, kCardDim, false, 0, HudRole::on_surface_variant}; }
HudLine popup_value(std::string text) { return {std::move(text), 4, kCardText, true, 0, HudRole::primary}; }
HudLine popup_hint(std::string text) { return {std::move(text), 2, kCardDim, false, 0, HudRole::on_surface_variant}; }
HudLine popup_key(std::string text) { return {std::move(text), 1, kCardDim, false, 0, HudRole::outline}; }
}

std::vector<HudLine> tracking_hint_lines() { return {popup_hint("Reconnecting tracking...")}; }

void TrackingHint::set_font(const HudFont* font) { font_ = font; dirty_ = true; }
bool TrackingHint::set_accent(std::array<float, 3> accent) {
    if (accent == accent_) return false;
    accent_ = accent; dirty_ = true;
    return true;
}
void TrackingHint::ensure() const {
    if (!dirty_) return;
    image_ = render_hud_card(tracking_hint_lines(), accent_, font_);
    dirty_ = false; ++generation_;
}

std::vector<HudLine> view_mode_lines(std::uint8_t mode) {
    // Maximize-follow (2026-11-03): the overview is always anchor; a maximized screen follows you, or is locked by choice
    // (Ctrl+Alt+V there, kept for the session). Only the start (anchor; a fresh start maximizes in follow) and Ctrl+Alt+V
    // show this card: a maximize changes the mode quietly.
    const char* name = mode == 1 ? "Locked" : mode == 2 ? "Follow" : "Anchor";
    const char* hint = mode == 1 ? "Maximized screen moves with your head" : mode == 2 ? "Maximized screen eases in front of you"
                                                                                      : "Screens stay fixed in the room";
    const char* how = mode == 1 ? "Ctrl+Alt+V for follow" : mode == 2 ? "Ctrl+Alt+V for locked" : "A maximized screen follows you";
    return {popup_label("View mode"), popup_value(name), popup_hint(hint), popup_key(how)};
}

std::vector<HudLine> cursor_follow_lines(bool on) {
    return {popup_label("Cursor follow"), popup_value(on ? "On" : "Off"),
            popup_hint(on ? "View follows the pointer between screens" : "View stays on the selected screen"),
            popup_key("Ctrl+Alt+F to change")};
}

std::vector<HudLine> prediction_lines(bool on) {
    return {popup_label("Prediction"), popup_value(on ? "On" : "Off"),
            popup_hint(on ? "Screens lead your head motion slightly" : "Screens use the latest sensor pose"),
            popup_key("Ctrl+Alt+P to change")};
}

std::vector<HudLine> typing_lines(bool on) {
    return {popup_label("Keys"), popup_value(on ? "Typing" : "XR control"),
            popup_hint(on ? "XR keys off: arrows / Space / Tab / Esc work as usual" : "XR keys back on"),
            popup_key(on ? "Ctrl+Alt+T for XR control" : "Ctrl+Alt+T for typing")};
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
        if (!lines.empty()) lines.push_back({"", 1, kCardDim});
        for (auto& l : cursor_follow_lines(cursor_follow)) lines.push_back(std::move(l));
    }
    if (prediction_changed) {
        if (!lines.empty()) lines.push_back({"", 1, kCardDim});
        for (auto& l : prediction_lines(prediction)) lines.push_back(std::move(l));
    }
    if (typing_changed) {
        if (!lines.empty()) lines.push_back({"", 1, kCardDim});
        for (auto& l : typing_lines(typing)) lines.push_back(std::move(l));
    }
    lines_ = std::move(lines);
    image_ = render_hud_card(lines_, accent_, font_.get());
    shown_ms_ = now_ms; ++generation_;
    return true;
}

bool StatusPopups::set_accent(std::array<float, 3> accent, double now_ms) {
    if (accent == accent_) return false;
    accent_ = accent;
    if (!active(now_ms)) return false;  // the next card uses it
    image_ = render_hud_card(lines_, accent_, font_.get());
    ++generation_;
    return true;
}

float StatusPopups::alpha(double now_ms) const { return generation_ ? popup_alpha(now_ms - shown_ms_) : 0.f; }
}

namespace xr {
void StatusPopups::set_font(std::unique_ptr<HudFont> font) { font_ = std::move(font); }

bool CardFade::set(bool visible, double now_ms) {
    if (visible == visible_) return false;
    from_alpha_ = alpha(now_ms);
    from_ = level(now_ms);  // reverse from wherever the fade is
    visible_ = visible; changed_ms_ = now_ms;
    return true;
}
float CardFade::level(double now_ms) const {
    const double dt = std::max(0.0, now_ms - changed_ms_);
    if ((visible_ ? in_ms_ : out_ms_) <= 0) return visible_ ? 1.f : 0.f;
    if (visible_) return std::min(1.f, from_ + float(dt / in_ms_));
    return std::max(0.f, from_ - float(dt / out_ms_));
}
float CardFade::alpha(double now_ms) const {
    const float target = visible_ ? 1.f : 0.f;
    const double duration = (visible_ ? in_ms_ : out_ms_) * std::abs(target - from_);
    if (duration <= 0) return target;
    const float t = float(std::clamp((now_ms - changed_ms_) / duration, 0.0, 1.0));
    const float remaining = 1.f - t;
    return from_alpha_ + (target - from_alpha_) * (1.f - remaining * remaining * remaining);
}
bool CardFade::animating(double now_ms) const {
    const float l = level(now_ms);
    return visible_ ? l < 1.f : l > 0.f;
}
void CardFade::hold(double since_ms, double now_ms) {
    if (now_ms <= since_ms) return;
    // Move the clock, preserving both the curve and its progress. A toggle after since_ms starts at its own time.
    changed_ms_ += now_ms - std::max(since_ms, changed_ms_);
}

bool HelpOverlay::set(bool visible, const std::vector<HelpRow>& rows, double now_ms, int turn) {
    const bool showing = visible && !fade_.visible();
    const bool new_rows = !generation_ || rows.size() != rows_.size() ||
        !std::equal(rows.begin(), rows.end(), rows_.begin(),
                    [](const HelpRow& a, const HelpRow& b) { return a.keys == b.keys && a.action == b.action && a.section == b.section; });
    std::uint32_t page = page_;
    if (showing) page = 0;  // reopening starts on page 1
    else if (visible && turn && pages_ > 1) page = std::uint32_t((std::int64_t(page_) + (turn < 0 ? -1 : 1) + pages_) % pages_);
    const bool turned = page != page_, redraw = visible && (new_rows || turned);
    if (redraw) {
        if (new_rows) rows_ = rows;
        page_ = page;
        render();
        if (turned && !showing) ++turns_;
    }
    if (!fade_.set(visible, now_ms)) return redraw;  // a new page or new rows on a shown card: draw it
    if (visible) ++shown_; else ++hidden_;
    return true;
}
bool HelpOverlay::set_viewport(std::uint32_t width, std::uint32_t height) {
    if (width == max_w_ && height == max_h_) return false;
    max_w_ = width; max_h_ = height;
    if (!generation_) return false;
    render();
    return true;
}
void HelpOverlay::render() {
    pages_ = std::max<std::uint32_t>(1, help_card_fit(rows_, font_.get(), max_w_, max_h_).pages());
    page_ %= pages_;  // a new viewport or new rows can have fewer pages
    image_ = render_help_card(rows_, accent_, font_.get(), max_w_, max_h_, page_, &controls_);
    ++generation_;
}
void HelpOverlay::set_font(std::unique_ptr<HudFont> font) { font_ = std::move(font); }
bool HelpOverlay::set_accent(std::array<float, 3> accent) {
    if (accent == accent_) return false;
    accent_ = accent;
    if (!generation_) return false;  // the first render uses it
    render();
    return true;
}
float HelpOverlay::alpha(double now_ms) const { return generation_ ? fade_.alpha(now_ms) : 0.f; }
float HelpOverlay::offset_px(double now_ms) const { return (1 - alpha(now_ms)) * kHelpRisePx; }
bool HelpOverlay::animating(double now_ms) const { return generation_ && fade_.animating(now_ms); }

// VR video controls card (video.hpp): the help card's parts, so the OLED dimming cannot drift from it. Title, state and
// time, M3's linear progress indicator, the format, an optional error line, then the key hints and the idle note.
HudImage render_video_card(const VideoCardState& v, std::array<float, 3> accent, const HudFont* font) {
    if (accent == std::array<float, 3>{}) accent = kMdDefaultSeed;  // no theme colour given
    const auto& scheme = glass_theme_scheme(accent);
    const bool ascii = font == nullptr;
    const char* state = v.state == VideoState::playing ? "Playing" : v.state == VideoState::paused ? "Paused"
                      : v.state == VideoState::ended ? "Ended" : v.state == VideoState::error ? "Cannot play this video" : "Loading";
    const std::string time = format_clock(v.position_s) + " / " + (v.duration_s ? format_clock(v.duration_s) : std::string("--:--"));
    const Cell title = cell(card_title(v.title.empty() ? std::string("VR video") : v.title), font, scheme);
    const Cell status = cell(card_key(state), font, scheme), clock = cell(card_detail(time), font, scheme);
    const Cell format = cell(card_detail(video_format_label(v.format, ascii)), font, scheme);
    const Cell detail = cell(card_detail(v.detail), font, scheme);
    std::vector<Cell> hints;
    // The quality menu (Q) replaces the format line and the key hints: one row per setting, the selected one on M3's active
    // indicator (a fully rounded secondary-container band, its text on-secondary-container) with its value between arrows.
    struct MenuCells { Cell label, value; };
    std::vector<MenuCells> menu;
    if (v.menu)
        for (std::size_t i = 0; i < v.menu_rows.size(); ++i) {
            const bool sel = i == v.menu_selected;
            const auto& r = v.menu_rows[i];
            auto on_band = [](HudLine l) { l.role = HudRole::on_secondary_container; return l; };
            menu.push_back({cell(sel ? on_band(card_key(r.label)) : card_detail(r.label), font, scheme),
                            cell(sel ? on_band(card_key("< " + r.value + " >")) : card_detail(r.value), font, scheme)});
        }
    if (v.menu) {
        const auto all = video_menu_hints(ascii);  // keys two per line, then the closing note
        for (std::size_t i = 0; i + 2 < all.size(); i += 2) hints.push_back(cell(card_footer(all[i] + kHintGap + all[i + 1]), font, scheme));
        hints.push_back(cell(card_footer(all.back()), font, scheme));
    } else {
        const auto all = video_card_hints(ascii);
        std::string line;
        for (std::size_t i = 0; i < all.size(); ++i) {
            line += (line.empty() ? "" : kHintGap) + all[i];
            if (i % 3 == 2 || i + 1 == all.size()) { hints.push_back(cell(card_footer(line), font, scheme)); line.clear(); }
        }
        hints.push_back(cell(card_footer("Closes after " + std::to_string(kOverlayIdleCloseS) + " s idle. Any video key shows it."), font, scheme));
    }
    const std::uint32_t bar_h = kProgressH, status_w = status.box.width + std::uint32_t(kColumnGap) + clock.box.width;
    std::uint32_t content_w = std::max({title.box.width, status_w, format.box.width, detail.box.width, 560u});
    for (const auto& h : hints) content_w = std::max(content_w, h.box.width);
    for (const auto& m : menu) content_w = std::max(content_w, m.label.box.width + std::uint32_t(kColumnGap) + m.value.box.width);
    const std::uint32_t status_h = row_ascent(status, clock) + row_descent(status, clock);
    const auto menu_row_h = [](const MenuCells& m) { return row_ascent(m.label, m.value) + row_descent(m.label, m.value) + std::uint32_t(kRowGap); };
    std::uint32_t body_h = height(format.box);
    if (v.menu) { body_h = 0; for (const auto& m : menu) body_h += menu_row_h(m); }
    std::uint32_t content_h = height(title.box) + std::uint32_t(kSectionGap) + status_h + std::uint32_t(kRowGap) + bar_h +
                              std::uint32_t(kRowGap) + body_h + std::uint32_t(kSectionGap);
    if (!v.detail.empty()) content_h += height(detail.box) + std::uint32_t(kRowGap);
    for (std::size_t i = 0; i < hints.size(); ++i) content_h += height(hints[i].box) + (i + 1 < hints.size() ? std::uint32_t(kRowGap) / 2 : 0);
    HudImage im;
    allocate_card(im, card_size(content_w), card_size(content_h), scheme);
    const int left = int(kHelpPad);
    std::uint32_t top = std::uint32_t(kHelpPad);
    draw_centered(im, title, top, font);
    top += height(title.box) + std::uint32_t(kSectionGap);
    draw_divider(im, top, scheme);
    const std::uint32_t ascent = row_ascent(status, clock);
    draw_cell(im, status, left, top, ascent, font);
    draw_cell(im, clock, left + int(content_w - clock.box.width), top, ascent, font);
    top += status_h + std::uint32_t(kRowGap);
    // Progress: M3's linear indicator, the primary up to the position over a secondary-container track, fully rounded.
    const float progress = v.duration_s ? std::clamp(float(v.position_s) / float(v.duration_s), 0.f, 1.f) : 0.f;
    const auto filled = std::uint32_t(std::lround(progress * float(content_w)));
    const float bar_top = float(top), bar_bottom = float(top + bar_h), r = float(bar_h) / 2;
    md_fill_rounded(im, float(left), bar_top, float(left) + float(content_w), bar_bottom, r, scheme.secondary_container, 1.f);
    if (filled) md_fill_rounded(im, float(left), bar_top, float(left) + float(std::max<std::uint32_t>(filled, bar_h)), bar_bottom, r, scheme.primary, 1.f);
    top += bar_h + std::uint32_t(kRowGap);
    if (v.menu) {
        for (std::size_t i = 0; i < menu.size(); ++i) {
            const auto& m = menu[i];
            const std::uint32_t h = menu_row_h(m), a = row_ascent(m.label, m.value);
            if (i == v.menu_selected) {
                const float x0 = float(left) - float(kRowGap) / 2, y0 = float(top) - float(kRowGap) / 2;
                md_fill_rounded(im, x0, y0, x0 + float(content_w) + float(kRowGap), y0 + float(h), float(h) / 2, scheme.secondary_container, 1.f);
            }
            draw_cell(im, m.label, left, top, a, font);
            draw_cell(im, m.value, left + int(content_w - m.value.box.width), top, a, font);
            top += h;
        }
    } else {
        draw_cell(im, format, left, top, format.box.ascent, font);
        top += height(format.box);
    }
    if (!v.detail.empty()) { top += std::uint32_t(kRowGap); draw_cell(im, detail, left, top, detail.box.ascent, font); top += height(detail.box); }
    top += std::uint32_t(kSectionGap);
    draw_divider(im, top, scheme);
    for (const auto& h : hints) { draw_centered(im, h, top, font); top += height(h.box) + std::uint32_t(kRowGap) / 2; }
    return im;
}
}
