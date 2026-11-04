// Actual compositor app pixel -> native captured surface -> rendered Restore hit mesh.
// No injected mesh, pointer excursion, compositor, device or GPU.
#include "xr/options_geometry.hpp"
#include "maximize_geometry.hpp"
#include "xr/pose.hpp"
#include <cmath>
#include <iostream>
int main(int argc,char**){
 for(float width:{320.f,640.f,1440.f,2400.f})for(float height:{240.f,600.f,1080.f})
 for(bool keep_size:{false,true})for(float distance:{.25f,1.5f})
 for(float curve:{0.f,1.f,2.094395f})for(float roll:{-.5f,0.f,.5f}) {
  xr::Canvas canvas{2560,1440,1,.5625};
  const std::array<xr::Size,1> sources{{{width,height}}};
  auto overview=xr::overview_layout(sources,2560,1440,40);
  auto tiles=xr::blended_layout(sources,2560,1440,40,1,0);
  const std::array<xr::Placement,1> originals{{{.2f,-.1f,distance,curve,roll}}};
  auto placed=xr::zoomed_placements(canvas,overview,tiles,originals,1,0,keep_size)[0];auto tile=tiles[0];
  if(placed.roll!=0||originals[0].roll!=roll)return 7;
  auto aim=xr::focus_direction(canvas,overview,originals,0);
  auto view=xr::view_from(xr::Quat{},xr::aim_rotation(aim[0],aim[1]));
  auto wire=xr::maximize_geometry_json("XR-3",canvas,tile,placed,view);
  auto emitted=nlohmann::json::parse(wire)["maximize_geometry"];
  if(emitted.is_null()){std::cerr<<"no reachable Restore in actual selected view\n";return 10;}
  if(argc>1)std::cout<<wire<<"\n";
  auto rect=xr::options_restore_rect(canvas,tile,placed,view);
  if(emitted["rect"]!=nlohmann::json{rect.x,rect.y,rect.width,rect.height})return 4;
  auto drawn_mesh=xr::tile_mesh(canvas,tile,placed,xr::options_restore_hit(rect,tile));
  if(emitted["hit_mesh"].size()!=drawn_mesh.size())return 5;
  for(std::size_t i=0;i<drawn_mesh.size();++i){auto v=drawn_mesh[i].world;if(emitted["hit_mesh"][i]!=nlohmann::json{v.x,v.y,v.z})return 6;
   auto e=xr::apply(view,{v.x,v.y,v.z});if(e[2]>=0||std::abs(e[0]/e[2])>canvas.half_w+1e-5f||std::abs(e[1]/e[2])>canvas.half_h+1e-5f){std::cerr<<"padded native mesh clipped by actual view\n";return 11;}
  }
  auto original_rect=xr::options_maximize(tile,false);float scale=xr::level_bar_scale(tile);
  if(rect.width!=26*scale)return 9;
  (void)original_rect;
  auto hit=xr::options_restore_hit(rect,tile);
  if(hit.x<tile.x||hit.y<tile.y||hit.x+hit.width>tile.x+tile.width||hit.y+hit.height>tile.y+tile.height){std::cerr<<"Restore padded mesh is outside actual captured picture\n";return 1;}
  // Compositor output has a nonzero global origin; only real integer app pixels are admissible.
  const int output_x=1920,output_y=80,output_w=1920,output_h=1080;
  int cursor_x=output_x+int((rect.x+rect.width/2-tile.x)/tile.width*output_w);
  int cursor_y=output_y+int((rect.y+rect.height/2-tile.y)/tile.height*output_h);
  if(cursor_x<output_x||cursor_x>=output_x+output_w||cursor_y<output_y||cursor_y>=output_y+output_h)return 2;
  float px=tile.x+float(cursor_x-output_x)/output_w*tile.width;
  float py=tile.y+float(cursor_y-output_y)/output_h*tile.height;
  auto point=xr::place_point(canvas,tile,placed,px,py);
  auto eye=xr::apply(view,{point.x,point.y,point.z});
  if(eye[2]>=0||std::abs(eye[0]/eye[2])>canvas.half_w||std::abs(eye[1]/eye[2])>canvas.half_h){std::cerr<<"Restore not visible in actual selected view\n";return 8;}
  if(!xr::options_maximize_ray(canvas,tile,placed,point,true,&rect)){std::cerr<<"actual app cursor does not intersect rendered mesh\n";return 3;}
 }
 std::cerr<<"432 actual app-bound native Restore geometry cases passed\n";
}
