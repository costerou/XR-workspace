#pragma once
#include "xr/ipc.hpp"
#include <nlohmann/json.hpp>
namespace xr {
// Presentation diagnostics contain no user row labels, values or message text.
// accepted is called only at the renderer setter; drawn only after a successful
// real compositor draw/swap of an options frame.
class OptionsEvidence {
 std::uint64_t updates_=0,frames_=0;
 nlohmann::json last_drawn_=nullptr;
 static nlohmann::json metadata(const OptionsMenuState& s){return {{"open",s.open},{"monitor",s.monitor},{"scope",s.scope},{"category",s.category},{"focus",s.focus},{"scroll",s.scroll},{"revision",s.revision},{"status",s.status},{"row_count",s.rows.size()}};}
public:
 void accepted(){++updates_;}
 void drawn(const OptionsMenuState& s){++frames_;last_drawn_=metadata(s);}
 nlohmann::json report(const OptionsMenuState& state)const{auto j=metadata(state);j["updates"]=updates_;j["drawn_frames"]=frames_;j["last_drawn"]=last_drawn_;return j;}
};
}
