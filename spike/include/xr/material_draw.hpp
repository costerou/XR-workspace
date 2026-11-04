#pragma once
// Material Design 3 drawing on the renderer's premultiplied RGBA images (hud.hpp HudImage: the placement panel's content,
// the cards): anti-aliased rounded containers, outlines and discs, and the few Material icons the chrome uses, drawn from
// signed distances so they stay crisp at any size. Colours are material.hpp roles (already under the OLED cap), and the
// "over" here never brightens past what it paints, so nothing drawn with roles passes kCardMaxLevel.
#include "xr/hud.hpp"
#include "xr/material.hpp"

namespace xr {
// Premultiplied "over": colour `c` with coverage `a` (clamped to 1) on top of pixel (x, y); outside the image: nothing.
void md_over(HudImage& im, int x, int y, MdRgb c, float a);
// Signed distance (px, negative inside) from (px, py) to a rounded rectangle centred at (cx, cy) with half sizes hw x hh
// and corner radius r (at most the smaller half size).
float md_rounded_distance(float px, float py, float cx, float cy, float hw, float hh, float r);
// A rounded container [x0, x1) x [y0, y1), its edge anti-aliased, `alpha` opaque.
void md_fill_rounded(HudImage& im, float x0, float y0, float x1, float y1, float radius, MdRgb c, float alpha);
// The same shape's outline, `width` px wide just inside its edge (M3 outlined containers: 1 px of outline / outline variant).
void md_stroke_rounded(HudImage& im, float x0, float y0, float x1, float y1, float radius, float width, MdRgb c, float alpha);
void md_disc(HudImage& im, float cx, float cy, float r, MdRgb c, float alpha);
// Signed distance (px, negative inside) from (px, py) to a stroke `width` px wide with round caps along an L: from
// (gap - leg, gap) to the elbow, rounded with radius `bend`, and on to (gap, gap - leg). An L wrapped round the origin's
// outer quadrant, x and y pointing away from what it wraps.
float md_corner_distance(float px, float py, float gap, float leg, float bend, float width);

// Material Symbols-style icons, outlined on M3's 24 px grid with 2 px strokes (heavier below kMdIconPx, as Material
// Symbols' optical size) and drawn `size` px across, centred on (cx, cy): add (a plus), remove (a minus), width (a double
// arrow between two bars: a wider desktop), arrow_drop_down / arrow_drop_up (a dropdown's filled triangle: closed / open),
// rotate_left / rotate_right (a circular arrow, counter-clockwise / clockwise), level (a dot on a broken horizon line).
enum class MdIcon : std::uint8_t { add, remove, width, arrow_drop_down, arrow_drop_up, rotate_left, rotate_right, level, stereo };
inline constexpr float kMdIconPx = 18.f;  // icons in buttons (M3: 18 dp next to a label)
void md_icon(HudImage& im, MdIcon icon, float cx, float cy, float size, MdRgb c, float alpha = 1.f);
// The icon's signed distance (icon px, negative inside) at (x, y) relative to its centre, at `size`: what md_icon fills.
float md_icon_distance(MdIcon icon, float x, float y, float size);
}
