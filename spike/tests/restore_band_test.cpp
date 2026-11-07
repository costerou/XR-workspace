// The Step-Back Band, every shape: the serialized geometry is the drawn pill and the stepped picture. No compositor, device or GPU.
#include "xr/options_geometry.hpp"
#include "maximize_geometry.hpp"
#include "xr/pose.hpp"
#include <cmath>
#include <iostream>
int main(int argc,char**){
 for(float width:{320.f,640.f,1440.f,2400.f})for(float height:{240.f,600.f,1080.f})
 for(float distance:{.25f,1.5f})
 for(float curve:{0.f,1.f,2.094395f})for(float roll:{-.5f,0.f,.5f}) {
  xr::Canvas canvas{2560,1440,1,.5625};
  const std::array<xr::Size,1> sources{{{width,height}}};
  auto overview=xr::overview_layout(sources,2560,1440,40);
  auto tiles=xr::blended_layout(sources,2560,1440,40,1,0);
  const std::array<xr::Placement,1> originals{{{.2f,-.1f,distance,curve,roll}}};
  auto placed=xr::zoomed_placements(canvas,overview,tiles,originals,1,0)[0];auto tile=tiles[0];  // keep_size is never on maximized (wayland.cpp)
  if(placed.roll!=0||originals[0].roll!=roll)return 7;
  auto aim=xr::focus_direction(canvas,overview,originals,0);
  auto view=xr::view_from(xr::Quat{},xr::aim_rotation(aim[0],aim[1]));
  auto wire=xr::maximize_geometry_json("XR-3",canvas,tile,placed,view);
  auto emitted=nlohmann::json::parse(wire)["maximize_geometry"];
  if(argc>1)std::cout<<wire<<"\n";
  const float scale=xr::options_step_scale(tile);const auto on=xr::options_stepped(placed,scale,1);
  auto rect=xr::options_restore_rect(tile,scale);
  if(emitted["rect"]!=nlohmann::json{rect.x,rect.y,rect.width,rect.height})return 4;
  auto drawn_mesh=xr::tile_mesh(canvas,tile,on,xr::options_restore_hit(rect,scale));
  if(emitted["hit_mesh"].size()!=drawn_mesh.size())return 5;
  for(std::size_t i=0;i<drawn_mesh.size();++i){auto v=drawn_mesh[i].world;if(emitted["hit_mesh"][i]!=nlohmann::json{v.x,v.y,v.z})return 6;
   auto e=xr::apply(view,{v.x,v.y,v.z});if(e[2]>=0||std::abs(e[0]/e[2])>canvas.half_w+1e-5f||std::abs(e[1]/e[2])>canvas.half_h+1e-5f){std::cerr<<"the pill is cut by the view: "<<width<<"x"<<height<<" d "<<distance<<" curve "<<curve<<" roll "<<roll<<" e "<<e[0]/e[2]<<" "<<e[1]/e[2]<<"\n";return 11;}
  }
  // The band: the pill above the picture at the middle column, kRestoreBandGap clear, whatever the shape.
  auto box=xr::options_picture_box(canvas,tile,on,view);
  auto point=xr::place_point(canvas,tile,on,rect.x+rect.width/2,rect.y+rect.height);auto eye=xr::apply(view,{point.x,point.y,point.z});
  const float bottom=(1+eye[1]/eye[2]/canvas.half_h)*canvas.view_h/2;
  if(!(bottom<box[1])||box[1]-bottom>xr::kRestoreBandGap*2||bottom<0){std::cerr<<"pill bottom "<<bottom<<" against the picture's top "<<box[1]<<"\n";return 7;}
  // A picture filling the view steps back to the scale; one already leaving a band keeps its size.
  auto full=xr::options_picture_box(canvas,tile,placed,view);
  const float grown=(full[3]-full[1])/(box[3]-box[1]);
  if(std::abs(grown*scale-1)>.03f){std::cerr<<"the picture stepped by "<<1/grown<<" not "<<scale<<"\n";return 9;}
  if(xr::options_stepped(placed,scale,0).distance!=placed.distance)return 12;  // at rest the picture is untouched
 }
 // The band's ease: smooth both ways, and a change of mind from where it is.
 {xr::RestoreStep s;if(s.value(0)!=0)return 13;s.set(true,1000);if(s.value(1000)!=0||!s.animating(1100)||std::abs(s.value(1000+xr::kRestoreStepMs/2)-.5f)>.001f||s.value(1000+xr::kRestoreStepMs)!=1||s.animating(1000+xr::kRestoreStepMs))return 13;
 s.set(false,2000);if(s.value(2000)!=1||s.value(2000+xr::kRestoreStepMs)!=0)return 13;s.set(true,3000);const float mid=s.value(3000+xr::kRestoreStepMs/2);s.set(false,3000+xr::kRestoreStepMs/2);if(std::abs(s.value(3000+xr::kRestoreStepMs/2)-mid)>.001f)return 13;}
 // Cody's sizes in the 16:10 view: the Pro 2's already leave a band, the Luma's step back.
 for(const auto& [w,h,want]:std::array<std::array<float,3>,4>{{{5120,1440,1},{2560,1440,1},{2560,1600,xr::kRestoreStepScale},{1920,1200,xr::kRestoreStepScale}}}){const std::array<xr::Size,1> one{{{w,h}}};auto tiles=xr::blended_layout(one,1920,1200,20,1,0);if(xr::options_step_scale(tiles[0])!=want){std::cerr<<w<<"x"<<h<<" scale\n";return 14;}}
 std::cerr<<"216 native Step-Back Band geometry cases passed\n";
}
