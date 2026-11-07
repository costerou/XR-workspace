#include "xr/options_geometry.hpp"
#include <array>
#include <iostream>
#include <iterator>
#include <nlohmann/json.hpp>
#include <string>
#include "xr/ipc.hpp"
#include "maximize_geometry.hpp"
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
   {"panel_mid",glass(panel.x+300,panel.y+220)},{"gear_mid",glass(gear.x+gear.width/2,gear.y+gear.height/2)},{"maximize_mid",world(mx.x+mx.width/2,mx.y+mx.height/2)}});
 }
 std::cout<<out.dump()<<"\n";return 0;
}
// --band: for each {"monitor","source","canvas","placement"} on stdin, the production maximize_geometry line of that desktop maximized.
static int band(){
 std::string text{std::istreambuf_iterator<char>(std::cin),std::istreambuf_iterator<char>()};
 for(const auto& e:nlohmann::json::parse(text)){
  xr::Canvas c{e["canvas"][0],e["canvas"][1],e["canvas"][2],e["canvas"][3]};const std::array<xr::Size,1> sources{{{e["source"][0],e["source"][1]}}};
  const std::array<xr::Placement,1> own{{{e["placement"][0],e["placement"][1],e["placement"][2],e["placement"][3],e["placement"][4]}}};
  auto overview=xr::overview_layout(sources,c.view_w,c.view_h,20);auto tiles=xr::blended_layout(sources,c.view_w,c.view_h,20,1,0);auto placed=xr::zoomed_placements(c,overview,tiles,own,1,0)[0];
  auto aim=xr::focus_direction(c,overview,own,0);auto view=xr::view_from(xr::Quat{},xr::aim_rotation(aim[0],aim[1]));
  std::cout<<xr::maximize_geometry_json(e["monitor"],c,tiles[0],placed,view)<<"\n";
 }
 return 0;
}
int main(int argc,char**argv){
 if(argc==2&&std::string(argv[1])=="--layout")return layout();
 if(argc==2&&std::string(argv[1])=="--band")return band();
 if(argc==3&&std::string(argv[1])=="--section"){auto r=xr::options_section_item(std::size_t(std::stoi(argv[2])));std::cout<<nlohmann::json::array({r.x,r.y,r.width,r.height}).dump()<<"\n";return 0;}
 for(float w:{80.f,240.f,640.f,1800.f})for(float x:{-800.f,0.f,1200.f}){
 xr::FRect t{x,100,w,360};auto p=xr::glass_pill(t),g=xr::options_gear(t),m=xr::options_maximize(t),b=xr::level_bar_rect(t);float s=xr::level_bar_scale(t);
 if(g.x-(p.x+p.width)!=xr::kOptionsPillGap||g.y!=p.y||g.width!=p.width||g.height!=p.height||m.width!=xr::kMaximizeW*s||m.height!=b.height||std::abs(b.x-(m.x+m.width)-xr::kMaximizeGap*s)>.001)return 1;
 auto panel=xr::options_panel(t);if(panel.width!=xr::kOptionsW||panel.height!=xr::kPanelH||panel.y!=p.y)return 2;
 }
 for(float curve:{0.f,1.f,2.094f})for(float roll:{-.5f,0.f,.5f})for(float distance:{.1f,1.f,2.f}){
 xr::Canvas c{1920,1080,1,.5625};xr::FRect t{300,200,640,360};xr::Placement on{.1f,.2f,distance,curve,roll};auto r=xr::options_maximize(t);auto q=xr::place_point(c,t,on,r.x+r.width/2,r.y+r.height/2);float len=std::sqrt(q.x*q.x+q.y*q.y+q.z*q.z);xr::Vec3 ray{q.x/len,q.y/len,q.z/len};if(!xr::options_maximize_ray(c,t,on,ray)){std::cerr<<curve<<" "<<roll<<" "<<distance<<"\n";return 4;}
 }
 if(xr::options_row_at(30,xr::kOptionsBodyTop,int(xr::kOptionsRowH),10)!=1||xr::options_row_at(30,xr::kOptionsBodyBottom,0,64)!=-1)return 3;
 // Drawn boxes and hit regions agree, and no two overlap.
 {auto hit=[](const xr::FRect& r){return xr::options_header_at(r.x+r.width/2,r.y+r.height/2);};
 if(hit(xr::options_section_box())!=xr::OptionsHeader::section||hit(xr::options_close_box())!=xr::OptionsHeader::close||xr::options_header_at(10,10)!=xr::OptionsHeader::none)return 4;
 auto scope=xr::options_scope_box();if(xr::options_header_at(scope.x+1,scope.y+1)!=xr::OptionsHeader::scope_monitor||xr::options_header_at(scope.x+scope.width-1,scope.y+1)!=xr::OptionsHeader::scope_workspace)return 4;
 for(std::size_t i=0;i<xr::kOptionsSections.size();++i){auto r=xr::options_section_item(i);if(xr::options_section_at(r.x+r.width/2,r.y+r.height/2)!=int(i))return 4;}
 if(xr::options_section_at(10,10)!=-1)return 4;}
 const std::array<float,3> blue{.45f,.65f,.8f},red{.8f,.3f,.3f};
 auto pixel=[](const xr::HudImage& im,float x,float y){return &im.rgba[(std::size_t(y)*im.width+std::size_t(x))*4];};
 auto menu=[](std::vector<xr::OptionsRow> rows,int focus=-1){xr::OptionsMenuState s;s.open=true;s.monitor="XR-1";s.scope="monitor";s.category="output";s.status="idle";s.focus=focus;s.rows=std::move(rows);return s;};
 const xr::OptionsRow range{"a","Distance","monitor","range","100","%",10,200,1,true,{}};
 // The glass under the menu is the placement panel's: nothing here paints over it.
 {auto a=xr::render_options_panel(menu({range}),nullptr,blue);if(a.width!=xr::kOptionsW||a.height!=xr::kPanelH||pixel(a,3,200)[3]!=0||pixel(a,300,xr::kOptionsBodyBottom-2)[3]!=0){std::cerr<<"the menu paints its own background\n";return 7;}
 // The theme is followed: the same state in another accent changes, and an accent is not ignored.
 auto b=xr::render_options_panel(menu({range},0),nullptr,blue),c=xr::render_options_panel(menu({range},0),nullptr,red);if(b.rgba==c.rgba){std::cerr<<"the menu ignores the theme\n";return 7;}
 // Nothing passes the OLED cap.
 for(std::size_t i=0;i<b.rgba.size();i+=4)for(int k=0;k<3;++k)if(b.rgba[i+std::size_t(k)]>191){std::cerr<<"past the OLED cap\n";return 7;}}
 // The selected row is the placement panel's band: the same colour, in the same inset.
 {xr::PlacementMenuState p;p.open=true;p.field=0;auto a=xr::render_glass_panel(p,nullptr,blue);auto b=xr::render_options_panel(menu({range},0),nullptr,blue);
 const auto* want=pixel(a,12,xr::kPanelRowsTop+xr::kPanelRowH/2);const auto* got=pixel(b,12,xr::kOptionsBodyTop+xr::kPanelRowH/2);
 if(!std::equal(want,want+4,got)||pixel(b,5,xr::kOptionsBodyTop+xr::kPanelRowH/2)[3]!=0){std::cerr<<"the selected row is not the placement band\n";return 7;}}
 // A long value is cut, never drawn past its column: every kind, in the pixel font and the real one.
 {const std::string many(300,'x');xr::OptionsRow ro{"a",many,"monitor","readonly",many,"",0,0,0,true,{}},ch=ro,tx=ro,ac=ro;ch.kind="choice";tx.kind="text";tx.label="Path";ac.kind="action";std::string why;auto font=xr::HudFont::load(xr::kHudFontFamily,why);
 for(const xr::HudFont* f:std::array<const xr::HudFont*,2>{nullptr,font.get()})for(const auto& r:{ro,ch,tx,ac}){auto im=xr::render_options_panel(menu({r}),f,blue);for(unsigned y=std::uint32_t(xr::kOptionsBodyTop);y<std::uint32_t(xr::kOptionsBodyTop+xr::kOptionsRowH);++y)for(unsigned x=std::uint32_t(xr::kOptionsW-xr::kPanelPad+1);x<std::uint32_t(xr::kOptionsW);++x)if(pixel(im,float(x),float(y))[3]){std::cerr<<r.kind<<" runs past its column\n";return 7;}}}
 // An open list: the row's value and the drawn list agree with the hit regions, and a long one stays on the panel.
 for(std::size_t n:{2u,12u,16u,17u,32u})for(float row:{122.f,190.f,326.f}){const float h=float(xr::options_choice_rows(n))*xr::kScaleItemH;const auto top=xr::options_choice_top(row,n);if(top<xr::kOptionsHeaderH-1||top+h>xr::kOptionsH){std::cerr<<"a list leaves the panel\n";return 8;}
  for(std::size_t i=0;i<n;++i){auto r=xr::options_choice_item(row,n,i);if(r.x<0||r.x+r.width>xr::kOptionsW||xr::options_choice_at(r.x+r.width/2,r.y+r.height/2,row,n)!=int(i)){std::cerr<<"item "<<i<<" of "<<n<<"\n";return 8;}}}
 // The list is really drawn: over the rows above its box, opaque, with the highlighted item on the secondary container.
 {xr::OptionsRow c{"resolution","Resolution","monitor","choice","b","",0,0,0,true,{"a","b","c"}},above{"x","Row","monitor","action","","",0,0,0,true,{}};auto s=menu({above,above,c},2);auto shut=xr::render_options_panel(s,nullptr,blue);s.list=1;auto open=xr::render_options_panel(s,nullptr,blue);
 auto item=xr::options_choice_item(xr::kOptionsBodyTop+2*xr::kOptionsRowH,3,1);const auto* p=pixel(open,item.x+item.width-6,item.y+item.height/2);const auto& m=xr::glass_theme_scheme(blue);
 if(shut.rgba==open.rgba||p[3]!=255||std::abs(int(p[0])-int(std::lround(m.secondary_container[0]*255)))>1){std::cerr<<"the open list is not drawn\n";return 8;}}
 // On and off are visibly different: the switch's own colours, with a handle at either end.
 {xr::OptionsRow t{"a","Toggle","monitor","toggle","true","",0,0,0,true,{}},f=t;f.value="false";auto on=xr::render_options_panel(menu({t}),nullptr,blue),off=xr::render_options_panel(menu({f}),nullptr,blue);
 const float x=xr::kOptionsW-xr::kPanelPad-13,y=xr::kOptionsBodyTop+xr::kOptionsRowH/2;if(on.rgba==off.rgba||std::equal(pixel(on,x,y),pixel(on,x,y)+4,pixel(off,x,y))){std::cerr<<"a toggle reads the same on and off\n";return 8;}}
 // A status has the whole footer: its words are not squeezed out by the key hint.
 {std::string why;auto font=xr::HudFont::load(xr::kHudFontFamily,why);if(font){auto s=menu({range});auto idle=xr::render_options_panel(s,font.get(),blue);s.status="error";s.message=std::string(40,'x');auto bad=xr::render_options_panel(s,font.get(),blue);
 std::uint32_t far=0;for(std::uint32_t x=30;x<300;++x)for(std::uint32_t y=std::uint32_t(xr::kOptionsFooterTop);y<std::uint32_t(xr::kOptionsFooterTop)+xr::kPanelHintPx;++y)if(pixel(bad,float(x),float(y))[3]){++far;break;}if(far<100||idle.rgba==bad.rgba){std::cerr<<"a status is squeezed out of the footer\n";return 7;}}}
 // A scrolled row is cut at the strip's edge, not drawn over the Scope row or the footer.
 {std::vector<xr::OptionsRow> rows(20,xr::OptionsRow{"a","Row","monitor","action","","",0,0,0,true,{}});auto s=menu(rows);s.scroll=17;auto im=xr::render_options_panel(s,nullptr,blue);for(std::uint32_t x=30;x<200;++x)if(pixel(im,float(x),xr::kOptionsBodyBottom+1)[3]){std::cerr<<"a row leaves its strip\n";return 7;}}
 // A choice's dropdown arrow is not printed over: the arrow's column is the same whatever the value.
 auto choice=[](const std::string& value){xr::OptionsMenuState s;s.open=true;s.monitor="XR-1";s.scope="monitor";s.category="output";s.status="idle";s.rows={{"a","Resolution","monitor","choice",value,"",0,0,0,true,{}}};return xr::render_options_panel(s);};
 {auto a=choice("x"),b=choice(std::string(200,'5'));const unsigned W=unsigned(xr::kOptionsW);for(unsigned y=std::uint32_t(xr::kOptionsBodyTop);y<std::uint32_t(xr::kOptionsBodyTop+xr::kOptionsRowH);++y)for(unsigned x=W-unsigned(xr::kPanelPad)-22;x<W-unsigned(xr::kPanelPad);++x)for(int k=0;k<3;++k)if(a.rgba[(y*W+x)*4+std::size_t(k)]!=b.rgba[(y*W+x)*4+std::size_t(k)]){std::cerr<<"value over the arrow at "<<x<<","<<y<<"\n";return 5;}}
 // The Maximize and Restore glyphs are centred in their pill: their ink's bounding box, in atlas pixels, around the cell's middle.
 const auto atlas=xr::render_handle_atlas({.45f,.65f,.8f},nullptr);
 for(bool hot:{false,true}){
  const bool restore=false;
  auto box=xr::options_icon_box(hot);
  const int x0=int(std::lround(box.u0*atlas.width)),y0=int(std::lround(box.v0*atlas.height)),x1=int(std::lround(box.u1*atlas.width)),y1=int(std::lround(box.v1*atlas.height));
  auto at=[&](int x,int y){return &atlas.rgba[(std::size_t(y)*atlas.width+std::size_t(x))*4];};
  const auto* fill=at((x0+x1)/2,y0+6);int lo_x=1<<20,hi_x=-1,lo_y=1<<20,hi_y=-1;
  for(int y=y0+8;y<y1-8;++y)for(int x=x0+8;x<x1-8;++x){const auto* p=at(x,y);if(std::abs(int(p[0])-fill[0])+std::abs(int(p[1])-fill[1])+std::abs(int(p[2])-fill[2])<40)continue;lo_x=std::min(lo_x,x);hi_x=std::max(hi_x,x+1);lo_y=std::min(lo_y,y);hi_y=std::max(hi_y,y+1);}
  if(hi_x<0||std::abs((lo_x+hi_x)/2.f-(x0+x1)/2.f)>.5f||std::abs((lo_y+hi_y)/2.f-(y0+y1)/2.f)>.5f){std::cerr<<"icon off centre: restore="<<restore<<" ink "<<lo_x-x0<<".."<<hi_x-x0<<" x "<<lo_y-y0<<".."<<hi_y-y0<<" of "<<x1-x0<<"x"<<y1-y0<<"\n";return 5;}
 }
 nlohmann::json out={{"w",xr::kOptionsW},{"h",xr::kOptionsH},{"pad",xr::kOptionsPad},{"header_h",xr::kOptionsHeaderH},{"row_h",xr::kOptionsRowH},{"section_top",xr::kOptionsSectionTop},{"scope_top",xr::kOptionsScopeTop},
  {"box_x0",xr::kOptionsBoxX0},{"box_x1",xr::kOptionsBoxX1},{"box_h",xr::kOptionsBoxH},{"divider_y",xr::kOptionsDividerY},{"body_top",xr::kOptionsBodyTop},{"body_h",xr::kOptionsBodyH},{"body_bottom",xr::kOptionsBodyBottom},{"footer_top",xr::kOptionsFooterTop},
  {"track_x0",xr::kOptionsTrackX0},{"track_x1",xr::kOptionsTrackX1},{"knob_lo",xr::kOptionsKnobLo},{"knob_hi",xr::kOptionsKnobHi},{"close_x0",xr::kOptionsCloseX0},{"close_x1",xr::kOptionsCloseX1},{"close_y0",xr::kOptionsCloseY0},{"close_w",xr::kOptionsCloseW},{"close_h",xr::kOptionsCloseH},
  {"gear_gap",xr::kOptionsPillGap},{"list_top",xr::kOptionsListTop},{"list_item_w",xr::kOptionsListItemW},{"list_rows",xr::kOptionsListRows},{"item_h",xr::kResItemH},{"sections",xr::kOptionsSections.size()},
  {"step_scale",xr::kRestoreStepScale},{"step_ms",xr::kRestoreStepMs},{"band_min",xr::kRestoreBandMin},{"band_gap",xr::kRestoreBandGap},{"focus_scope",xr::kOptionsFocusScope},{"focus_section",xr::kOptionsFocusSection},{"focus_list",xr::kOptionsFocusList},{"focus_close",xr::kOptionsFocusClose}};
 std::cout<<out.dump()<<"\n";
}
