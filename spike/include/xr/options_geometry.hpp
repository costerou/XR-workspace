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
inline FRect options_maximize(const FRect& tile,bool restore=false){auto p=level_bar_rect(tile);float s=level_bar_scale(tile),x=p.x-(kMaximizeGap+kMaximizeW)*s;if(restore)return {std::clamp(x,tile.x+16*s,tile.x+tile.width-(16+kMaximizeW)*s),tile.y+16*s,kMaximizeW*s,kMaximizeH*s};return {x,p.y,kMaximizeW*s,kMaximizeH*s};}
inline FRect options_gear_hit(const FRect& tile){auto r=options_gear(tile);return {r.x-4,r.y-4,r.width+8,r.height+8};}
inline FRect options_pill_hit(const FRect& tile){auto r=glass_pill(tile);auto g=options_gear(tile);float left=r.x-4,right=std::min(r.x+r.width+4,(r.x+r.width+g.x)/2);return {left,r.y-4,right-left,r.height+8};}
inline FRect options_maximize_hit(const FRect& tile,bool restore=false){auto r=options_maximize(tile,restore);float p=4*level_bar_scale(tile);return {r.x-p,r.y-p,r.width+2*p,r.height+2*p};}
// Resolve Restore inside BOTH the captured picture and actual camera frustum.
// At fixed x, place_point is affine in y even on curved/rolled surfaces. Solve
// the five clip-plane inequalities for every padded mesh vertex, preserving x
// first; only search inward horizontally if that column has no visible region.
inline FRect options_restore_rect(const Canvas& canvas,const FRect& tile,const Placement& on,const std::array<float,9>& view){
 auto initial=options_maximize(tile,true);float pad=4*level_bar_scale(tile);
 if(tile.width<initial.width+2*pad||tile.height<initial.height+2*pad)return {};
 auto solve=[&](float x)->std::optional<FRect>{
  FRect r{x,initial.y,initial.width,initial.height};
  float lo=tile.y+pad-r.y,hi=tile.y+tile.height-pad-r.height-r.y;
  auto eye=[&](Vec3 p){return std::array<float,3>{view[0]*p.x+view[1]*p.y+view[2]*p.z,view[3]*p.x+view[4]*p.y+view[5]*p.z,view[6]*p.x+view[7]*p.y+view[8]*p.z};};
  auto planes=[&](const std::array<float,3>& p){return std::array<float,5>{-p[2]-1e-5f,-canvas.half_w*p[2]+p[0],-canvas.half_w*p[2]-p[0],-canvas.half_h*p[2]+p[1],-canvas.half_h*p[2]-p[1]};};
  for(const auto& v:tile_mesh(canvas,tile,on,{r.x-pad,r.y-pad,r.width+2*pad,r.height+2*pad})){
   auto a=planes(eye(v.world)),b=planes(eye(place_point(canvas,tile,on,v.px,v.py+16)));
   for(std::size_t k=0;k<a.size();++k){float slope=(b[k]-a[k])/16;
    if(std::abs(slope)<1e-9f){if(a[k]<0)return std::nullopt;continue;}
    float bound=-a[k]/slope;if(slope>0)lo=std::max(lo,bound);else hi=std::min(hi,bound);
   }
  }
  // Tiny inward clearance keeps exported float coordinates inside clip planes.
  lo+=.02f;hi-=.02f;if(lo>hi)return std::nullopt;r.y+=std::clamp(0.f,lo,hi);return r;
 };
 if(auto r=solve(initial.x))return *r;
 std::optional<FRect> best;float left=tile.x+pad,right=tile.x+tile.width-initial.width-pad;
 for(int i=0;i<=32;++i){float x=left+(right-left)*float(i)/32;if(auto r=solve(x))if(!best||std::abs(x-initial.x)<std::abs(best->x-initial.x))best=r;}
 if(!best)return {};
 float good=best->x,bad=initial.x;for(int i=0;i<14;++i){float x=(good+bad)/2;if(auto r=solve(x)){good=x;best=r;}else bad=x;}
 return *best;
}
inline FRect options_restore_hit(const FRect& rect,const FRect& tile){if(rect.width<=0)return {};float p=4*level_bar_scale(tile);return {rect.x-p,rect.y-p,rect.width+2*p,rect.height+2*p};}
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
inline std::optional<float> options_maximize_ray(const Canvas& canvas,const FRect& tile,const Placement& on,const Vec3& ray,bool restore=false,const FRect* resolved=nullptr){
 return options_mesh_ray(tile_mesh(canvas,tile,on,resolved?options_restore_hit(*resolved,tile):options_maximize_hit(tile,restore)),ray);
}
// The Options pill under the dot: the renderer lights it as it lights Maximize, on the hit region the supervisor presses.
inline bool options_gear_ray(const Canvas& canvas,const FRect& tile,const Placement& on,const Vec3& ray){return bool(options_mesh_ray(glass_mesh(canvas,tile,on,options_gear_hit(tile)),ray));}
}
