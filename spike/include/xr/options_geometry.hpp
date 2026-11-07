#pragma once
#include "xr/glass.hpp"
#include <algorithm>
#include <array>
#include <optional>
#include <vector>
namespace xr {
// The options menu is the placement menu with more rows, so every size here is the placement panel's own constant, or derived from
// one: change glass.hpp and both follow. It is kOptionsExtraW wider only because its labels are longer, and the same height.
// render_options_panel draws these, the *_at functions are their hit regions, and tools/native_spike/options_geometry.py mirrors both
// (spike/tests/options_geometry_test.cpp dumps them, tests compare).
constexpr float kOptionsExtraW=80, kOptionsW=kPanelW+kOptionsExtraW, kOptionsH=kPanelH, kOptionsPad=kPanelPad, kOptionsHeaderH=kPanelHeaderH, kOptionsRowH=kPanelRowH;
constexpr float kOptionsSectionTop=kPanelRowsTop, kOptionsScopeTop=kOptionsSectionTop+kPanelRowH;  // the two rows under the header, on the placement panel's rows
constexpr float kOptionsBoxX0=kPanelTrackX0, kOptionsBoxX1=kOptionsW-kPanelPad, kOptionsBoxH=kScaleBoxH;
constexpr float kOptionsDividerY=kOptionsScopeTop+kPanelRowH+3, kOptionsBodyTop=kOptionsDividerY+5, kOptionsFooterTop=kPanelFooterTop;
constexpr int kOptionsVisibleRows=7;
constexpr float kOptionsBodyH=float(kOptionsVisibleRows)*kOptionsRowH, kOptionsBodyBottom=kOptionsBodyTop+kOptionsBodyH;
static_assert(kOptionsBodyBottom<=kOptionsFooterTop,"the rows end above the placement panel's footer");
constexpr float kOptionsValueW=kPanelW-kPanelPad-kPanelTrackX1;  // the placement panel's value column
constexpr float kOptionsTrackX1=kOptionsW-kPanelPad-kOptionsValueW, kOptionsTrackX0=kOptionsTrackX1-122, kOptionsKnobLo=kOptionsTrackX0+kPanelKnobR, kOptionsKnobHi=kOptionsTrackX1-kPanelKnobR;
constexpr float kOptionsCloseW=74, kOptionsCloseH=kScaleBoxH, kOptionsCloseX1=kOptionsW-kPanelPad, kOptionsCloseX0=kOptionsCloseX1-kOptionsCloseW, kOptionsCloseY0=(kGrabberTop+kGrabberH+kPanelHeaderH-kOptionsCloseH-1)/2;  // on the header's text line
// The sections: an exposed dropdown like Resolution, whose list is two columns of its 23 px items, under the box.
struct OptionsSection{const char* id;const char* label;};
inline constexpr std::array<OptionsSection,14> kOptionsSections{{{"placement","Placement"},{"output","Output"},{"3d","3D"},{"stabilization","Stabilization"},{"layout","Layout"},{"environment","Environment"},{"view","View"},{"video","Video"},{"input","Input"},{"keybinds","Key binds"},{"windows","Windows"},{"privacy","Privacy"},{"help","Help"},{"session","Session"}}};
constexpr int kOptionsListRows=int(kOptionsSections.size()+1)/2;
constexpr float kOptionsListTop=kOptionsSectionTop+(kPanelRowH+kOptionsBoxH)/2+kResListGap, kOptionsListItemW=(kOptionsBoxX1-kOptionsBoxX0)/2;
static_assert(kOptionsListTop+float(kOptionsListRows)*kResItemH<=kOptionsH,"the open list stays on the panel");
// OptionsMenuState::focus: a row, or one of these header controls.
constexpr int kOptionsFocusScope=-4,kOptionsFocusSection=-3,kOptionsFocusList=-2,kOptionsFocusClose=-1;
enum class OptionsHeader{none,section,scope_monitor,scope_workspace,close};
constexpr FRect options_section_box(){return {kOptionsBoxX0,kOptionsSectionTop+(kPanelRowH-kOptionsBoxH)/2,kOptionsBoxX1-kOptionsBoxX0,kOptionsBoxH};}
constexpr FRect options_scope_box(){return {kOptionsBoxX0,kOptionsScopeTop+(kPanelRowH-kOptionsBoxH)/2,kOptionsBoxX1-kOptionsBoxX0,kOptionsBoxH};}
constexpr FRect options_close_box(){return {kOptionsCloseX0,kOptionsCloseY0,kOptionsCloseW,kOptionsCloseH};}
constexpr FRect options_section_item(std::size_t i){return {kOptionsBoxX0+float(i/std::size_t(kOptionsListRows))*kOptionsListItemW,kOptionsListTop+float(i%std::size_t(kOptionsListRows))*kResItemH,kOptionsListItemW,kResItemH};}
constexpr bool options_in(const FRect& r,float x,float y){return x>=r.x&&x<r.x+r.width&&y>=r.y&&y<r.y+r.height;}
constexpr OptionsHeader options_header_at(float x,float y){
 if(options_in(options_close_box(),x,y))return OptionsHeader::close;
 if(options_in(options_section_box(),x,y))return OptionsHeader::section;
 if(options_in(options_scope_box(),x,y))return x<kOptionsBoxX0+(kOptionsBoxX1-kOptionsBoxX0)/2?OptionsHeader::scope_monitor:OptionsHeader::scope_workspace;
 return OptionsHeader::none;
}
constexpr int options_section_at(float x,float y){for(std::size_t i=0;i<kOptionsSections.size();++i)if(options_in(options_section_item(i),x,y))return int(i);return -1;}
// A choice row's list: the Scale list's items under the placement panel's rule (over the rows, above its box), as wide as the box;
// two columns past kOptionsChoiceRows items, as Resolution. Below the box when it does not fit above, and always on the panel. `row_y`: the row's top.
constexpr int kOptionsChoiceRows=6, kMaxOptionsChoices=32;  // the wire allows 32
constexpr float kOptionsChoiceItemW=kOptionsBoxX1-kOptionsTrackX0;
constexpr int options_choice_columns(std::size_t count){return count>std::size_t(kOptionsChoiceRows)?2:1;}
constexpr int options_choice_rows(std::size_t count){return int((count+std::size_t(options_choice_columns(count))-1)/std::size_t(options_choice_columns(count)));}
constexpr float options_choice_top(float row_y,std::size_t count){
 const float box=row_y+(kOptionsRowH-kScaleBoxH)/2,h=float(options_choice_rows(count))*kScaleItemH,above=box-kScaleListGap-h,below=box+kScaleBoxH+kScaleListGap;
 return above>=kOptionsHeaderH?above:below+h<=kOptionsH-kScaleListGap?below:kOptionsH-kScaleListGap-h;
}
constexpr FRect options_choice_item(float row_y,std::size_t count,std::size_t i){
 const int rows=options_choice_rows(count),columns=options_choice_columns(count);
 return {kOptionsBoxX1-float(columns-int(i/std::size_t(rows)))*kOptionsChoiceItemW,options_choice_top(row_y,count)+float(i%std::size_t(rows))*kScaleItemH,kOptionsChoiceItemW,kScaleItemH};
}
constexpr int options_choice_at(float x,float y,float row_y,std::size_t count){for(std::size_t i=0;i<count;++i)if(options_in(options_choice_item(row_y,count,i),x,y))return int(i);return -1;}
static_assert(float(kMaxOptionsChoices+1)/2*kScaleItemH<=kOptionsH-kOptionsHeaderH,"the longest list stays on the panel");
// The Options pill is the drag pill's twin: the same size, kOptionsPillGap to its right.
constexpr float kOptionsPillGap=12;
// Maximize and Restore: a capsule of the level bar's height and fill.
constexpr float kMaximizeW=38, kMaximizeH=kLevelBarH, kMaximizeGap=12;
inline FRect options_panel(const FRect& tile){auto p=glass_pill(tile);return {tile.x+(tile.width-kOptionsW)/2,p.y,kOptionsW,kOptionsH};}
inline FRect options_gear(const FRect& tile){auto p=glass_pill(tile);return {p.x+p.width+kOptionsPillGap,p.y,p.width,p.height};}
inline FRect options_maximize(const FRect& tile){auto p=level_bar_rect(tile);float s=level_bar_scale(tile);return {p.x-(kMaximizeGap+kMaximizeW)*s,p.y,kMaximizeW*s,kMaximizeH*s};}
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
inline int options_row_at(float x,float y,int scroll,std::size_t count){if(x<kOptionsPad||x>=kOptionsW-kOptionsPad||y<kOptionsBodyTop||y>=kOptionsBodyBottom)return -1;int i=int((y-kOptionsBodyTop+float(scroll))/kOptionsRowH);return i>=0&&std::size_t(i)<count?i:-1;}
}
namespace xr {
// Ray against the same triangle strip used to draw the transformed target.
inline std::optional<float> options_mesh_ray(const std::vector<MeshVertex>& mesh,const Vec3& ray){
 auto sub=[](Vec3 a,Vec3 b){return Vec3{a.x-b.x,a.y-b.y,a.z-b.z};};
 auto cross=[](Vec3 a,Vec3 b){return Vec3{a.y*b.z-a.z*b.y,a.z*b.x-a.x*b.z,a.x*b.y-a.y*b.x};};
 auto dot=[](Vec3 a,Vec3 b){return a.x*b.x+a.y*b.y+a.z*b.z;};
 std::optional<float> best;
 for(std::size_t i=0;i+2<mesh.size();++i){auto a=mesh[i].world,e1=sub(mesh[i+1].world,a),e2=sub(mesh[i+2].world,a),n=cross(ray,e2);float d=dot(e1,n);if(std::abs(d)<1e-12f)continue;Vec3 o{-a.x,-a.y,-a.z};float u=dot(o,n)/d;if(u<-.0001f||u>1.0001f)continue;auto q=cross(o,e1);float v=dot(ray,q)/d,t=dot(e2,q)/d;if(v<-.0001f||u+v>1.0001f||t<=0)continue;if(!best||t<*best)best=t;}
 return best;
}
inline std::optional<float> options_maximize_ray(const Canvas& canvas,const FRect& tile,const Placement& on,const Vec3& ray){return options_mesh_ray(tile_mesh(canvas,tile,on,options_maximize_hit(tile)),ray);}
// The Options pill under the dot: the renderer lights it as it lights Maximize, on the hit region the supervisor presses.
inline bool options_gear_ray(const Canvas& canvas,const FRect& tile,const Placement& on,const Vec3& ray){return bool(options_mesh_ray(glass_mesh(canvas,tile,on,options_gear_hit(tile)),ray));}
}
