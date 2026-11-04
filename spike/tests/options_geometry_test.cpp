#include "xr/options_geometry.hpp"
#include <iostream>
#include <cmath>
int main(){
 for(float w:{80.f,240.f,640.f,1800.f})for(float x:{-800.f,0.f,1200.f}){
 xr::FRect t{x,100,w,360};auto p=xr::glass_pill(t),g=xr::options_gear(t),m=xr::options_maximize(t),b=xr::level_bar_rect(t);float s=xr::level_bar_scale(t);
 if(g.x-(p.x+p.width)!=12||g.y!=p.y||m.width!=26*s||std::abs(b.x-(m.x+m.width)-12*s)>.001)return 1;
 auto panel=xr::options_panel(t);if(panel.width!=600||panel.height!=440||panel.y!=p.y)return 2;
 }
 for(float curve:{0.f,1.f,2.094f})for(float roll:{-.5f,0.f,.5f})for(float distance:{.1f,1.f,2.f}){
 xr::Canvas c{1920,1080,1,.5625};xr::FRect t{300,200,640,360};xr::Placement on{.1f,.2f,distance,curve,roll};auto r=xr::options_maximize(t);auto q=xr::place_point(c,t,on,r.x+r.width/2,r.y+r.height/2);float len=std::sqrt(q.x*q.x+q.y*q.y+q.z*q.z);xr::Vec3 ray{q.x/len,q.y/len,q.z/len};if(!xr::options_maximize_ray(c,t,on,ray))return 4;
 }
 if(xr::options_row_at(20,88,38,10)!=1||xr::options_row_at(20,392,0,64)!=-1)return 3;
 std::cout<<"{\"width\":600,\"height\":440,\"header\":44,\"tabs_top\":44,\"tabs_height\":36,\"body_top\":88,\"body_bottom\":392,\"row_height\":38,\"pad\":16,\"footer_top\":400,\"gear_width\":28,\"gear_height\":14,\"gear_gap\":12,\"hit_pad\":4,\"maximize_width\":26,\"maximize_gap\":12}\n";
}
