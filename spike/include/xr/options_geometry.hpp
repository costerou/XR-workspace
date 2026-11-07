#pragma once
#include "xr/glass.hpp"
#include <algorithm>
namespace xr {
constexpr float kOptionsTrackX0=300, kOptionsTrackX1=450;
// The header's controls (x ranges) and the tab cells: render_options_panel draws them, options_header_at / options_tab_at are their
// hit regions, and tools/native_spike/options_geometry.py mirrors both (spike/tests/options_geometry_test.cpp dumps them, tests compare).
constexpr float kOptionsScopeX0=346, kOptionsNextX0=440, kOptionsCloseX0=530, kOptionsCloseX1=594, kOptionsTabsX=16, kOptionsTabW=136, kOptionsTabPitch=142;
constexpr int kOptionsTabsPerPage=4;
enum class OptionsHeader{none,scope,next,close};
constexpr OptionsHeader options_header_at(float x,float y){if(y<0||y>=44)return OptionsHeader::none;return x>=kOptionsScopeX0&&x<kOptionsNextX0?OptionsHeader::scope:x>=kOptionsNextX0&&x<kOptionsCloseX0?OptionsHeader::next:x>=kOptionsCloseX0&&x<kOptionsCloseX1?OptionsHeader::close:OptionsHeader::none;}
constexpr int options_tab_at(float x,float y,int category,int count){if(y<44||y>=80||x<kOptionsTabsX)return -1;const int cell=int((x-kOptionsTabsX)/kOptionsTabPitch);const int index=category/kOptionsTabsPerPage*kOptionsTabsPerPage+cell;return cell<kOptionsTabsPerPage&&x-kOptionsTabsX-float(cell)*kOptionsTabPitch<kOptionsTabW&&index<count?index:-1;}
constexpr float kOptionsW=600, kOptionsH=440, kOptionsHeaderH=44, kOptionsTabsTop=44, kOptionsTabsH=36, kOptionsBodyTop=88, kOptionsBodyBottom=392, kOptionsRowH=38, kOptionsPad=16, kOptionsFooterTop=400;
inline FRect options_panel(const FRect& tile){auto p=glass_pill(tile);return {tile.x+(tile.width-kOptionsW)/2,p.y,kOptionsW,kOptionsH};}
inline FRect options_gear(const FRect& tile){auto p=glass_pill(tile);return {p.x+p.width+12,p.y,28,14};}
inline FRect options_maximize(const FRect& tile){auto p=level_bar_rect(tile);float s=level_bar_scale(tile);return {p.x-38*s,p.y,26*s,26*s};}
inline FRect options_gear_hit(const FRect& tile){auto r=options_gear(tile);return {r.x-4,r.y-4,r.width+8,r.height+8};}
inline FRect options_pill_hit(const FRect& tile){auto r=glass_pill(tile);auto g=options_gear(tile);float left=r.x-4,right=std::min(r.x+r.width+4,(r.x+r.width+g.x)/2);return {left,r.y-4,right-left,r.height+8};}
inline FRect options_maximize_hit(const FRect& tile){auto r=options_maximize(tile);float p=4*level_bar_scale(tile);return {r.x-p,r.y-p,r.width+2*p,r.height+2*p};}
// Restore, the Step-Back Band (Cody's pick, R3): nothing over a maximized picture. Resting the pointer on the top edge eases the picture
// back, about the view's centre, so a band opens above it, and the Restore pill is in that band, kRestoreBandGap above the picture. A
// picture that already leaves kRestoreBandMin (a 16:9 or ultrawide one in the 16:10 view) stays its size: only the pill comes. Tile px
// on the desktop's surface, like the bar, at its stepped distance, so a curved desktop curves it. Mirrored in restore_band.py.
constexpr float kRestoreStepScale=.84f, kRestoreStepMs=220, kRestoreBandMin=46, kRestoreBandGap=12, kRestorePillW=122, kRestorePillH=kLevelBarH, kRestorePad=4;
static_assert(kRestoreBandGap+kRestorePillH+8<=kRestoreBandMin,"the pill fits the smallest band");
inline float options_step_scale(const FRect& tile){return tile.y>=kRestoreBandMin?1.f:kRestoreStepScale;}
// `on` `e` (eased 0..1) of the way back: the distance grows, every length scales about the eye.
inline Placement options_stepped(Placement on,float scale,float e){on.distance/=1+(scale-1)*e;return on;}
inline FRect options_restore_rect(const FRect& tile,float scale){const float w=kRestorePillW/scale,h=kRestorePillH/scale;return {tile.x+(tile.width-w)/2,tile.y-(kRestoreBandGap+kRestorePillH)/scale,w,h};}
inline FRect options_restore_hit(const FRect& rect,float scale){const float p=kRestorePad/scale;return {rect.x-p,rect.y-p,rect.width+2*p,rect.height+2*p};}
// Where the stepped picture lands on the canvas: its left and right as drawn, its top and bottom at the middle column (where the pill
// is: a curved desktop's corners stand further out), as the supervisor measures the band and the way back.
inline std::array<float,4> options_picture_box(const Canvas& c,const FRect& tile,const Placement& on,const std::array<float,9>& view){
 auto canvas_of=[&](float px,float py){auto q=place_point(c,tile,on,px,py);const float x=view[0]*q.x+view[1]*q.y+view[2]*q.z,y=view[3]*q.x+view[4]*q.y+view[5]*q.z,z=view[6]*q.x+view[7]*q.y+view[8]*q.z;
  return std::array<float,2>{(x/-z/c.half_w+1)*c.view_w/2,(1-y/-z/c.half_h)*c.view_h/2};};
 const float mid=tile.x+tile.width/2;
 return {canvas_of(tile.x,tile.y+tile.height/2)[0],canvas_of(mid,tile.y)[1],canvas_of(tile.x+tile.width,tile.y+tile.height/2)[0],canvas_of(mid,tile.y+tile.height)[1]};
}
inline int options_row_at(float x,float y,int scroll,std::size_t count){if(x<16||x>=584||y<88||y>=392)return -1;int i=int((y-88+scroll)/38);return i>=0&&std::size_t(i)<count?i:-1;}
}
namespace xr {
// Ray against the same triangle strip used to draw the transformed target.
inline std::optional<float> options_maximize_ray(const Canvas& canvas,const FRect& tile,const Placement& on,const Vec3& ray){
 auto mesh=tile_mesh(canvas,tile,on,options_maximize_hit(tile));
 auto sub=[](Vec3 a,Vec3 b){return Vec3{a.x-b.x,a.y-b.y,a.z-b.z};};
 auto cross=[](Vec3 a,Vec3 b){return Vec3{a.y*b.z-a.z*b.y,a.z*b.x-a.x*b.z,a.x*b.y-a.y*b.x};};
 auto dot=[](Vec3 a,Vec3 b){return a.x*b.x+a.y*b.y+a.z*b.z;};
 std::optional<float> best;
 for(std::size_t i=0;i+2<mesh.size();++i){auto a=mesh[i].world,e1=sub(mesh[i+1].world,a),e2=sub(mesh[i+2].world,a),n=cross(ray,e2);float d=dot(e1,n);if(std::abs(d)<1e-12f)continue;Vec3 o{-a.x,-a.y,-a.z};float u=dot(o,n)/d;if(u<-.0001f||u>1.0001f)continue;auto q=cross(o,e1);float v=dot(ray,q)/d,t=dot(e2,q)/d;if(v<-.0001f||u+v>1.0001f||t<=0)continue;if(!best||t<*best)best=t;}
 return best;
}
}
