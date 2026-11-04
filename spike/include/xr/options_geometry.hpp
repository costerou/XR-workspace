#pragma once
#include "xr/glass.hpp"
#include <algorithm>
namespace xr {
constexpr float kOptionsW=600, kOptionsH=440, kOptionsHeaderH=44, kOptionsTabsTop=44, kOptionsTabsH=36, kOptionsBodyTop=88, kOptionsBodyBottom=392, kOptionsRowH=38, kOptionsPad=16, kOptionsFooterTop=400;
inline FRect options_panel(const FRect& tile){auto p=glass_pill(tile);return {tile.x+(tile.width-kOptionsW)/2,p.y,kOptionsW,kOptionsH};}
inline FRect options_gear(const FRect& tile){auto p=glass_pill(tile);return {p.x+p.width+12,p.y,28,14};}
inline FRect options_maximize(const FRect& tile){auto p=level_bar_rect(tile);float s=level_bar_scale(tile);return {p.x-38*s,p.y,26*s,26*s};}
inline FRect options_gear_hit(const FRect& tile){auto r=options_gear(tile);return {r.x-4,r.y-4,r.width+8,r.height+8};}
inline FRect options_pill_hit(const FRect& tile){auto r=glass_pill(tile);auto g=options_gear(tile);float left=r.x-4,right=std::min(r.x+r.width+4,(r.x+r.width+g.x)/2);return {left,r.y-4,right-left,r.height+8};}
inline FRect options_maximize_hit(const FRect& tile){auto r=options_maximize(tile);float p=4*level_bar_scale(tile);return {r.x-p,r.y-p,r.width+2*p,r.height+2*p};}
inline int options_row_at(float x,float y,int scroll,std::size_t count){if(x<16||x>=584||y<88||y>=392)return -1;int i=int((y-88+scroll)/38);return i>=0&&std::size_t(i)<count?i:-1;}
}
