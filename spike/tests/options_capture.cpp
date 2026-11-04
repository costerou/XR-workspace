// CPU-only offline native production raster composition. The captured picture is
// a labelled placeholder; no compositor, context, media, device or service opens.
#include "xr/glass.hpp"
#include "xr/ipc.hpp"
#include "xr/options_geometry.hpp"
#include <cmath>
#include <algorithm>
#include <fstream>
#include <iostream>
using namespace xr;
int main(int argc,char**argv){
 if(argc!=2)return 2;
 std::string why;auto font=HudFont::load(kHudFontFamily,why);
 auto atlas=render_handle_atlas({.45f,.65f,.8f},font.get());
 OptionsMenuState menu;menu.open=true;menu.monitor="XR-2";menu.scope="monitor";menu.category="placement";menu.status="idle";menu.focus=0;
 menu.rows={{"placement","Open native placement editor","monitor","action","Open","",0,0,0,true,{}}};
 for(int state=0;state<4;++state){
  bool restore=state>=2,hover=state==3;
  FRect tile=restore?FRect{32,32,936,526}:FRect{180,100,640,360};
  HudImage frame{1000,1000,std::vector<std::uint8_t>(1000*1000*4,0)};
  auto put=[&](int x,int y,const std::uint8_t* p){if(x<0||y<0||x>=1000||y>=1000)return;for(int c=0;c<4;++c)frame.rgba[(y*1000+x)*4+c]=std::uint8_t(std::min(255,int(p[c])+int(frame.rgba[(y*1000+x)*4+c])*(255-int(p[3]))/255));};
  std::uint8_t background[]{24,28,32,255},border[]{95,140,170,255};
  for(int y=int(tile.y)-6;y<int(tile.y+tile.height)+6;++y)for(int x=int(tile.x)-6;x<int(tile.x+tile.width)+6;++x)put(x,y,x<tile.x||x>=tile.x+tile.width||y<tile.y||y>=tile.y+tile.height?border:background);
  auto blit=[&](const HudImage& im,int x,int y){for(unsigned sy=0;sy<im.height;++sy)for(unsigned sx=0;sx<im.width;++sx)put(x+int(sx),y+int(sy),&im.rgba[(sy*im.width+sx)*4]);};
  auto icon=[&](FRect r,AtlasBox box){for(int y=0;y<int(r.height);++y)for(int x=0;x<int(r.width);++x){auto sx=std::min(atlas.width-1,unsigned((box.u0+(box.u1-box.u0)*(x+.5f)/r.width)*atlas.width));auto sy=std::min(atlas.height-1,unsigned((box.v0+(box.v1-box.v0)*(y+.5f)/r.height)*atlas.height));put(int(r.x)+x,int(r.y)+y,&atlas.rgba[(sy*atlas.width+sx)*4]);}};
  auto label=render_hud_card({HudLine{restore?"Maximized captured-picture placeholder":"Overview captured-picture placeholder"}}, {.45f,.65f,.8f},font.get());
  blit(label,int(tile.x)+60,int(tile.y)+90);
  if(!restore){
   icon(level_bar_rect(tile),level_bar_box(LevelSegment::none,false));
   icon(immersive_main_rect(tile),immersive_main_box(false,false));
   icon(immersive_tail_rect(tile),immersive_tail_box(false,false));
   auto pill=glass_pill(tile);auto look=glass_look({.45f,.65f,.8f},0,0);
   for(int y=int(pill.y);y<int(pill.y+pill.height);++y)for(int x=int(pill.x);x<int(pill.x+pill.width);++x){auto c=glass_shade(look,pill,pill.height/2,x+.5f,y+.5f,1);std::uint8_t p[4];for(int k=0;k<4;++k)p[k]=std::uint8_t(std::clamp(c[k],0.f,1.f)*255);put(x,y,p);}
   icon(options_gear(tile),options_gear_box());
   if(state==1){auto panel=render_options_panel(menu,font.get());auto r=options_panel(tile);blit(panel,int(r.x),int(r.y));}
  }
  auto r=options_maximize(tile,restore);icon(r,options_icon_box(restore,hover));
  if(hover){float scale=level_bar_scale(tile);icon({r.x,r.y+32*scale,84*scale,20*scale},options_hint_box(true));}
  std::ofstream out(std::string(argv[1])+"/native-options-"+std::to_string(state)+".ppm",std::ios::binary);out<<"P6\n1000 1000\n255\n";for(std::size_t p=0;p<frame.rgba.size();p+=4)out.write(reinterpret_cast<char*>(&frame.rgba[p]),3);
 }
 std::cout<<"CPU production native raster: overview chrome, C1 panel, app-inset Restore normal/hover. Captured picture placeholder; no projected GPU/session evidence.\n";
}
