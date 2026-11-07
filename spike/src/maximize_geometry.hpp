#pragma once
#include "xr/options_geometry.hpp"
#include <nlohmann/json.hpp>
namespace xr {
// What the supervisor needs of the Step-Back Band, always as it stands fully stepped back, whatever the ease has reached: `placed` is
// the desktop's placement before the step. Same serializer for the presentation status emitter and the native tests.
inline std::string maximize_geometry_json(const std::string& monitor,const Canvas& canvas,const FRect& tile,const Placement& placed,const std::array<float,9>& view){
 const float scale=options_step_scale(tile);const Placement on=options_stepped(placed,scale,1);
 const FRect r=options_restore_rect(tile,scale);nlohmann::json mesh=nlohmann::json::array();
 for(const auto& v:tile_mesh(canvas,tile,on,options_restore_hit(r,scale)))mesh.push_back({v.world.x,v.world.y,v.world.z});
 return nlohmann::json{{"maximize_geometry",{{"monitor",monitor},{"maximized",true},{"canvas",{canvas.view_w,canvas.view_h,canvas.half_w,canvas.half_h}},{"tile",{tile.x,tile.y,tile.width,tile.height}},{"placement",{on.x,on.y,on.distance,on.curvature,on.roll}},{"rect",{r.x,r.y,r.width,r.height}},{"picture",options_picture_box(canvas,tile,on,view)},{"hit_mesh",mesh},{"view",view}}}}.dump();
}
}
