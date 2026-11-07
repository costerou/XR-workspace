#pragma once
#include "xr/options_geometry.hpp"
#include <nlohmann/json.hpp>
namespace xr {
// Same serializer used by the presentation status emitter and app-bound regression.
inline std::string maximize_geometry_json(const std::string& monitor,const Canvas& canvas,const FRect& tile,const Placement& placed,const std::array<float,9>& view){
 auto r=options_restore_rect(canvas,tile,placed,view);if(r.width<=0)return "{\"maximize_geometry\":null}";nlohmann::json mesh=nlohmann::json::array();
 for(const auto& v:tile_mesh(canvas,tile,placed,options_restore_hit(r,tile)))mesh.push_back({v.world.x,v.world.y,v.world.z});
 return nlohmann::json{{"maximize_geometry",{{"monitor",monitor},{"maximized",true},{"canvas",{canvas.view_w,canvas.view_h,canvas.half_w,canvas.half_h}},{"tile",{tile.x,tile.y,tile.width,tile.height}},{"placement",{placed.x,placed.y,placed.distance,placed.curvature,placed.roll}},{"rect",{r.x,r.y,r.width,r.height}},{"hit_mesh",mesh},{"view",view}}}}.dump();
}
}
