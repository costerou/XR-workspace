#pragma once
#include "xr/glass.hpp"
#include <algorithm>
namespace xr {
constexpr float kOptionsTrackX0=300, kOptionsTrackX1=450;
constexpr float kOptionsW=600, kOptionsH=440, kOptionsHeaderH=44, kOptionsTabsTop=44, kOptionsTabsH=36, kOptionsBodyTop=88, kOptionsBodyBottom=392, kOptionsRowH=38, kOptionsPad=16, kOptionsFooterTop=400;
inline FRect options_panel(const FRect& tile){auto p=glass_pill(tile);return {tile.x+(tile.width-kOptionsW)/2,p.y,kOptionsW,kOptionsH};}
inline FRect options_gear(const FRect& tile){auto p=glass_pill(tile);return {p.x+p.width+12,p.y,28,14};}
inline FRect options_maximize(const FRect& tile,bool restore=false){auto p=level_bar_rect(tile);float s=level_bar_scale(tile);if(restore)return {std::clamp(p.x-38*s,tile.x+16*s,tile.x+tile.width-42*s),tile.y+16*s,26*s,26*s};return {p.x-38*s,p.y,26*s,26*s};}
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
inline int options_row_at(float x,float y,int scroll,std::size_t count){if(x<16||x>=584||y<88||y>=392)return -1;int i=int((y-88+scroll)/38);return i>=0&&std::size_t(i)<count?i:-1;}
}
namespace xr {
// Ray against the same triangle strip used to draw the transformed target.
inline std::optional<float> options_maximize_ray(const Canvas& canvas,const FRect& tile,const Placement& on,const Vec3& ray,bool restore=false,const FRect* resolved=nullptr){
 auto mesh=tile_mesh(canvas,tile,on,resolved?options_restore_hit(*resolved,tile):options_maximize_hit(tile,restore));
 auto sub=[](Vec3 a,Vec3 b){return Vec3{a.x-b.x,a.y-b.y,a.z-b.z};};
 auto cross=[](Vec3 a,Vec3 b){return Vec3{a.y*b.z-a.z*b.y,a.z*b.x-a.x*b.z,a.x*b.y-a.y*b.x};};
 auto dot=[](Vec3 a,Vec3 b){return a.x*b.x+a.y*b.y+a.z*b.z;};
 std::optional<float> best;
 for(std::size_t i=0;i+2<mesh.size();++i){auto a=mesh[i].world,e1=sub(mesh[i+1].world,a),e2=sub(mesh[i+2].world,a),n=cross(ray,e2);float d=dot(e1,n);if(std::abs(d)<1e-12f)continue;Vec3 o{-a.x,-a.y,-a.z};float u=dot(o,n)/d;if(u<-.0001f||u>1.0001f)continue;auto q=cross(o,e1);float v=dot(ray,q)/d,t=dot(e2,q)/d;if(v<-.0001f||u+v>1.0001f||t<=0)continue;if(!best||t<*best)best=t;}
 return best;
}
}
