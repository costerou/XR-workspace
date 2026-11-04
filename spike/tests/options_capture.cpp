// Offline evidence from production raster functions. No compositor/device connection.
#include "xr/glass.hpp"
#include "xr/ipc.hpp"
#include "xr/options_geometry.hpp"
#include <cmath>
#include <algorithm>
#include <fstream>
#include <iostream>
void ppm(const xr::HudImage& im,const std::string& file){std::ofstream f(file,std::ios::binary);f<<"P6\n"<<im.width<<" "<<im.height<<"\n255\n";for(std::size_t i=0;i<im.rgba.size();i+=4)for(int c=0;c<3;++c){unsigned char b=im.rgba[i+c];f.write(reinterpret_cast<char*>(&b),1);}}
int main(int argc,char**argv){if(argc!=2)return 2;std::string why;auto font=xr::HudFont::load(xr::kHudFontFamily,why);xr::OptionsMenuState s;s.open=true;s.monitor="XR-2";s.scope="monitor";s.category="placement";s.status="idle";s.focus=1;
 s.rows={{"placement","Open native placement editor","monitor","action","Open","",0,0,0,true,{}},{"distance","Distance","monitor","readonly","1.5","m",.1,2,.1,true,{}},{"roll","Roll","monitor","readonly","-12","deg",-90,90,1,true,{}}};
 auto panel=xr::render_options_panel(s,font.get());auto atlas=xr::render_handle_atlas({.45f,.65f,.8f},font.get());
 for(int state=0;state<2;++state){xr::HudImage frame{800,600,std::vector<std::uint8_t>(800*600*4,0)};
 auto blit=[&](const xr::HudImage& src,int x,int y){for(unsigned sy=0;sy<src.height;++sy)for(unsigned sx=0;sx<src.width;++sx)for(int c=0;c<4;++c)frame.rgba[((y+sy)*800+x+sx)*4+c]=src.rgba[(sy*src.width+sx)*4+c];};
 blit(panel,100,140);
 auto crop=[&](xr::AtlasBox box,int x,int y){unsigned x0=unsigned(std::lround(box.u0*atlas.width)),y0=unsigned(std::lround(box.v0*atlas.height)),w=unsigned(std::lround((box.u1-box.u0)*atlas.width)),h=unsigned(std::lround((box.v1-box.v0)*atlas.height));xr::HudImage part{w,h,std::vector<std::uint8_t>(w*h*4)};for(unsigned r=0;r<h;++r)std::copy_n(atlas.rgba.begin()+((y0+r)*atlas.width+x0)*4,w*4,part.rgba.begin()+r*w*4);blit(part,x,y);};
 crop(xr::options_icon_box(state),100,25);crop(xr::options_gear_box(),220,45);crop(xr::level_bar_box(xr::LevelSegment::none,false),330,25);
 ppm(frame,std::string(argv[1])+"/native-options-"+std::to_string(state)+".ppm");}
 std::cout<<"Production CPU raster evidence; atlas cells shown at 3x; panel at 1x. Not projected GPU/session evidence.\n";
}
