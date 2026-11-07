#include "xr/options_geometry.hpp"
#include <iostream>
#include <iterator>
#include <nlohmann/json.hpp>
#include <string>
#include "xr/ipc.hpp"
#include <algorithm>
#include <cmath>
#include <cstdlib>
// --layout: for each {"canvas":[w,h,hw,hh],"tile":[x,y,w,h],"placement":[x,y,d,c,r]} (radians) on stdin, the rects as drawn and where
// glass and tile points land in the world, for tests/xr_options_integration/test_options_live_routes.py.
static int layout(){
 std::string text{std::istreambuf_iterator<char>(std::cin),std::istreambuf_iterator<char>()};auto all=nlohmann::json::parse(text),out=nlohmann::json::array();
 auto rect=[](const xr::FRect& r){return nlohmann::json::array({r.x,r.y,r.width,r.height});};
 for(const auto& e:all){
  xr::Canvas c{e["canvas"][0],e["canvas"][1],e["canvas"][2],e["canvas"][3]};xr::FRect t{e["tile"][0],e["tile"][1],e["tile"][2],e["tile"][3]};xr::Placement on{e["placement"][0],e["placement"][1],e["placement"][2],e["placement"][3],e["placement"][4]};
  auto world=[&](float x,float y){auto v=xr::place_point(c,t,on,x,y);return nlohmann::json::array({v.x,v.y,v.z});};
  auto glass=[&](float x,float y){auto q=xr::glass_to_desktop(t,on,x,y);return world(q[0],q[1]);};
  auto panel=xr::options_panel(t),gear=xr::options_gear(t),mx=xr::options_maximize(t);
  out.push_back({{"pill",rect(xr::glass_pill(t))},{"panel",rect(panel)},{"gear",rect(gear)},{"gear_hit",rect(xr::options_gear_hit(t))},{"maximize",rect(mx)},{"maximize_hit",rect(xr::options_maximize_hit(t))},
   {"panel_mid",glass(panel.x+300,panel.y+220)},{"gear_mid",glass(gear.x+14,gear.y+7)},{"maximize_mid",world(mx.x+mx.width/2,mx.y+mx.height/2)}});
 }
 std::cout<<out.dump()<<"\n";return 0;
}
int main(int argc,char**argv){
 if(argc==2&&std::string(argv[1])=="--layout")return layout();
 for(float w:{80.f,240.f,640.f,1800.f})for(float x:{-800.f,0.f,1200.f}){
 xr::FRect t{x,100,w,360};auto p=xr::glass_pill(t),g=xr::options_gear(t),m=xr::options_maximize(t),b=xr::level_bar_rect(t);float s=xr::level_bar_scale(t);
 if(g.x-(p.x+p.width)!=12||g.y!=p.y||m.width!=26*s||std::abs(b.x-(m.x+m.width)-12*s)>.001)return 1;
 auto panel=xr::options_panel(t);if(panel.width!=600||panel.height!=440||panel.y!=p.y)return 2;
 }
 for(float curve:{0.f,1.f,2.094f})for(float roll:{-.5f,0.f,.5f})for(float distance:{.1f,1.f,2.f}){
 xr::Canvas c{1920,1080,1,.5625};xr::FRect t{300,200,640,360};xr::Placement on{.1f,.2f,distance,curve,roll};auto r=xr::options_maximize(t);auto q=xr::place_point(c,t,on,r.x+r.width/2,r.y+r.height/2);float len=std::sqrt(q.x*q.x+q.y*q.y+q.z*q.z);xr::Vec3 ray{q.x/len,q.y/len,q.z/len};if(!xr::options_maximize_ray(c,t,on,ray)){std::cerr<<curve<<" "<<roll<<" "<<distance<<"\n";return 4;}
 }
 if(xr::options_row_at(20,88,38,10)!=1||xr::options_row_at(20,392,0,64)!=-1)return 3;
 // Drawn cells and hit regions agree; the next page's tabs are not under the page you see.
 if(xr::options_tab_at(16,62,0,14)!=0||xr::options_tab_at(151.9f,62,0,14)!=0||xr::options_tab_at(153,62,0,14)!=-1||xr::options_tab_at(158,62,0,14)!=1)return 4;
 if(xr::options_tab_at(583,62,0,14)!=-1||xr::options_tab_at(590,62,0,14)!=-1||xr::options_tab_at(16+142*2,62,12,14)!=-1)return 4;
 if(xr::options_header_at(346,20)!=xr::OptionsHeader::scope||xr::options_header_at(440,20)!=xr::OptionsHeader::next||xr::options_header_at(593,20)!=xr::OptionsHeader::close||xr::options_header_at(594,20)!=xr::OptionsHeader::none||xr::options_header_at(300,20)!=xr::OptionsHeader::none)return 4;
 // A choice's dropdown arrow is not printed over: the arrow's column is the same whatever the value.
 auto choice=[](const std::string& value){xr::OptionsMenuState s;s.open=true;s.monitor="XR-1";s.scope="monitor";s.category="output";s.status="idle";s.rows={{"a","Resolution","monitor","choice",value,"",0,0,0,true,{}}};return xr::render_options_panel(s,nullptr);};
 {auto a=choice(""),b=choice("5120x1440");for(unsigned y=88;y<126;++y)for(unsigned x=560;x<584;++x)for(int k=0;k<3;++k)if(a.rgba[(y*600+x)*4+k]!=b.rgba[(y*600+x)*4+k]){std::cerr<<"value over the arrow at "<<x<<","<<y<<"\n";return 6;}}
 // The Maximize and Restore glyphs are centred in their pill: their ink's bounding box, in atlas pixels, around the cell's middle.
 const auto atlas=xr::render_handle_atlas({.45f,.65f,.8f},nullptr);
 for(bool restore:{false,true})for(bool hot:{false,true}){
  auto box=xr::options_icon_box(restore,hot);
  const int x0=int(std::lround(box.u0*atlas.width)),y0=int(std::lround(box.v0*atlas.height)),x1=int(std::lround(box.u1*atlas.width)),y1=int(std::lround(box.v1*atlas.height));
  auto at=[&](int x,int y){return &atlas.rgba[(std::size_t(y)*atlas.width+std::size_t(x))*4];};
  const auto* fill=at((x0+x1)/2,y0+6);int lo_x=1<<20,hi_x=-1,lo_y=1<<20,hi_y=-1;
  for(int y=y0+8;y<y1-8;++y)for(int x=x0+8;x<x1-8;++x){const auto* p=at(x,y);if(std::abs(int(p[0])-fill[0])+std::abs(int(p[1])-fill[1])+std::abs(int(p[2])-fill[2])<40)continue;lo_x=std::min(lo_x,x);hi_x=std::max(hi_x,x+1);lo_y=std::min(lo_y,y);hi_y=std::max(hi_y,y+1);}
  if(hi_x<0||std::abs((lo_x+hi_x)/2.f-(x0+x1)/2.f)>.5f||std::abs((lo_y+hi_y)/2.f-(y0+y1)/2.f)>.5f){std::cerr<<"icon off centre: restore="<<restore<<" ink "<<lo_x-x0<<".."<<hi_x-x0<<" x "<<lo_y-y0<<".."<<hi_y-y0<<" of "<<x1-x0<<"x"<<y1-y0<<"\n";return 5;}
 }
 std::cout<<"{\"width\":600,\"height\":440,\"header\":44,\"tabs_top\":44,\"tabs_height\":36,\"body_top\":88,\"body_bottom\":392,\"row_height\":38,\"pad\":16,\"footer_top\":400,\"gear_width\":28,\"gear_height\":14,\"gear_gap\":12,\"hit_pad\":4,\"maximize_width\":26,\"maximize_gap\":12,\"scope_x0\":346,\"next_x0\":440,\"close_x0\":530,\"close_x1\":594,\"tabs_x\":16,\"tab_w\":136,\"tab_pitch\":142,\"tabs_per_page\":4}\n";
}
