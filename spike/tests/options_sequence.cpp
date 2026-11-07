// Offline production-raster composition of real supervisor options wire snapshots.
// The empty monitor rectangle is synthetic content; no display/device connection.
#include "xr/glass.hpp"
#include "xr/ipc.hpp"
#include "xr/options_geometry.hpp"
#include <algorithm>
#include <cmath>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <sstream>
using namespace xr;
int main(int argc,char**argv){
 if(argc!=2)return 2;
 std::string why;auto font=HudFont::load(kHudFontFamily,why);
 auto atlas=render_handle_atlas({.45f,.65f,.8f},font.get());
 FRect tile{150,100,500,281};int index=0;std::string line;
 while(std::getline(std::cin,line)){
  auto decoded=decode_control(line+"\n");auto* message=std::get_if<ControlMessage>(&decoded);
  if(!message || message->op!=ControlOp::options_menu)return 3;
  auto state=std::get<OptionsMenuState>(message->payload);
  HudImage frame{1000,900,std::vector<std::uint8_t>(1000*900*4,0)};
  auto put=[&](int x,int y,const std::uint8_t* pixel){if(x<0||y<0||x>=1000||y>=900)return;for(int c=0;c<4;++c)frame.rgba[(y*1000+x)*4+c]=pixel[c];};
  std::uint8_t bg[]{20,24,28,255},edge[]{95,140,170,255};
  for(int y=94;y<387;++y)for(int x=144;x<656;++x)put(x,y,(x<150||x>=650||y<100||y>=381)?edge:bg);
  auto icon=[&](FRect r,AtlasBox box){for(int y=0;y<int(r.height);++y)for(int x=0;x<int(r.width);++x){auto sx=std::min(atlas.width-1,unsigned((box.u0+(box.u1-box.u0)*(x+.5f)/r.width)*atlas.width));auto sy=std::min(atlas.height-1,unsigned((box.v0+(box.v1-box.v0)*(y+.5f)/r.height)*atlas.height));put(int(r.x)+x,int(r.y)+y,&atlas.rgba[(sy*atlas.width+sx)*4]);}};
  icon(level_bar_rect(tile),level_bar_box(LevelSegment::none,false));
  icon(immersive_main_rect(tile),immersive_main_box(false,false));
  icon(immersive_tail_rect(tile),immersive_tail_box(false,false));
  icon(options_maximize(tile),options_icon_box());
  auto pill=glass_pill(tile);auto look=glass_look({.45f,.65f,.8f},0,0);
  for(int y=int(pill.y);y<int(pill.y+pill.height);++y)for(int x=int(pill.x);x<int(pill.x+pill.width);++x){auto color=glass_shade(look,pill,pill.height/2,x+.5f,y+.5f,1);std::uint8_t rgba[4];for(int c=0;c<4;++c)rgba[c]=std::uint8_t(std::clamp(color[c],0.f,1.f)*255);put(x,y,rgba);}
  icon(options_gear(tile),options_gear_box());
  if(state.open){auto panel=render_options_panel(state,font.get());auto r=options_panel(tile);for(unsigned y=0;y<panel.height;++y)for(unsigned x=0;x<panel.width;++x)put(int(r.x)+int(x),int(r.y)+int(y),&panel.rgba[(y*panel.width+x)*4]);}
  std::ostringstream path;path<<argv[1]<<"/navigation-"<<std::setw(3)<<std::setfill('0')<<index++<<".ppm";
  std::ofstream out(path.str(),std::ios::binary);out<<"P6\n1000 900\n255\n";for(std::size_t p=0;p<frame.rgba.size();p+=4)out.write(reinterpret_cast<char*>(&frame.rgba[p]),3);
 }
 return index?0:4;
}
