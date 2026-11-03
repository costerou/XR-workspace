// Config reloads during a run keep the run's monitor rules, so a reload modesets nothing.
// The plugin's actual keptThroughReload body (extracted from ownership/plugin.cpp) runs against offline doubles of
// the pinned efb5099 rule manager: reload() = clear() -> parse the config -> ensureMonitorStatus(), which modesets
// each output whose rule (the last naming it, else the first catch-all "") does not compare() to the applied one.
// Omarchy's workspace layout toggle (master / scrolling / floating) is such a reload: it rewrites a layout file the
// config requires. Before the fix every switch took XR-n through the catch-all and the glasses out of their session
// mode, each and back (two modesets per output per switch); the unhooked cases below replay that.
#include "registry.hpp"
#include "lifecycle.hpp"
#include <algorithm>
#include <functional>
#include <iostream>
#include <map>
#include <memory>
#include <optional>
#include <ranges>
#include <vector>
using namespace xr::ownership;
#define CHECK(x) do { if(!(x)) throw std::runtime_error(#x); } while(0)
struct Vector2D {
 double x=0,y=0;
 bool operator==(const Vector2D&) const=default;
};
namespace Config {
enum eMonitorRuleComparisonResult { COMPARISON_FULL_MATCH, COMPARISON_SOFT_MISMATCH, COMPARISON_NO_MATCH };
struct CMonitorRule {
 std::string m_name; Vector2D m_resolution,m_offset; double m_scale=1,m_refreshRate=60;
 // Pinned compare(): mode, refresh and scale are hard (a modeset); position is soft.
 eMonitorRuleComparisonResult compare(const CMonitorRule& o) const {
   if(!(m_resolution==o.m_resolution) || m_refreshRate!=o.m_refreshRate || m_scale!=o.m_scale) return COMPARISON_NO_MATCH;
   return m_offset==o.m_offset ? COMPARISON_FULL_MATCH : COMPARISON_SOFT_MISMATCH;
 }
};
}
struct Monitor {
 std::string m_name, m_description;
 bool m_output=true, m_isUnsafeFallback=false;
 Config::CMonitorRule m_activeMonitorRule;
 int modesets=0, soft=0;
 bool matchesStaticSelector(std::string_view s) const {
   return s.starts_with("desc:") ? m_description.starts_with(s.substr(5)) : m_name==s;
 }
};
using PHLMONITOR=std::shared_ptr<Monitor>;
namespace State {
struct MonitorState { std::vector<PHLMONITOR> monitors; const auto& allMonitors() { return monitors; } };
MonitorState monitors;
MonitorState* monitorState() { return &monitors; }
}
namespace Config {
struct RuleManager {
 std::vector<CMonitorRule> rules;
 void clear() { rules.clear(); }
 void add(CMonitorRule r) { std::erase_if(rules,[&](const auto& e){return e.m_name==r.m_name;}); rules.push_back(r); }
 const std::vector<CMonitorRule>& all() { return rules; }
 CMonitorRule get(const PHLMONITOR& m) {
   for(const auto& r:rules | std::views::reverse) if(m->matchesStaticSelector(r.m_name)) return r;
   for(const auto& r:rules) if(r.m_name.empty()) return r;
   return {};
 }
 void ensureMonitorStatus() {
   for(const auto& m:State::monitors.monitors) {
     if(!m->m_output || m->m_isUnsafeFallback) continue;
     auto rule=get(m);
     auto cmp=rule.compare(m->m_activeMonitorRule);
     if(cmp==COMPARISON_FULL_MATCH) continue;
     if(cmp==COMPARISON_NO_MATCH) ++m->modesets; else ++m->soft;
     m->m_activeMonitorRule=rule;
   }
 }
};
RuleManager manager;
RuleManager* monitorRuleMgr() { return &manager; }
}
struct NativeObject : Object { std::optional<Config::CMonitorRule> rule; bool ruleLost=false; };
struct NativeBackend {
 std::vector<std::pair<std::weak_ptr<NativeObject>,Json>> configured;
#include "ownership_reload_methods.inc"
};

using Config::CMonitorRule;
// The user's monitors.lua: a catch-all at the user's scale, and the main display by name.
const CMonitorRule catchAll{"",{0,0},{0,0},1.25,0};
const CMonitorRule mainDisplay{"DP-1",{5120,1440},{0,0},1.25,240};
const CMonitorRule glassesSession{"DP-2",{1920,1200},{4096,0},1,120};  // the controller's runtime session mode
const CMonitorRule xrRule{"XR-1",{2140,1200},{8000,0},1,120};          // the plugin's configure()
using Config_=std::function<void(Config::RuleManager&)>;
const Config_ userConfig=[](Config::RuleManager& r){ r.add(catchAll); r.add(mainDisplay); };

struct World {
 NativeBackend backend;
 std::shared_ptr<NativeObject> object=std::make_shared<NativeObject>();
 PHLMONITOR dp1, dp2, xr;
 World() {
   State::monitors.monitors.clear(); Config::manager.rules.clear();
   auto monitor=[](std::string name,std::string description) {
     auto m=std::make_shared<Monitor>(); m->m_name=name; m->m_description=description; return m;
   };
   dp1=monitor("DP-1","Samsung Odyssey"); dp2=monitor("DP-2","VITURE Luma Ultra"); xr=monitor("XR-1","Headless");
   State::monitors.monitors={dp1,dp2,xr};
   userConfig(Config::manager); Config::manager.ensureMonitorStatus();
   Config::manager.add(glassesSession); Config::manager.ensureMonitorStatus();
   Config::manager.add(xrRule); Config::manager.ensureMonitorStatus();
   object->rule=xrRule; backend.configured.emplace_back(object,Json::object());
   for(auto& m:State::monitors.monitors) m->modesets=m->soft=0;
 }
 // Pinned reload(); `hooked`: the plugin's clear() hook (hookedClear) is installed.
 void reload(bool hooked,const Config_& config=userConfig) {
   std::vector<CMonitorRule> kept;
   if(hooked) kept=backend.keptThroughReload();
   Config::manager.clear();
   for(auto& r:kept) Config::manager.add(r);
   config(Config::manager);
   Config::manager.ensureMonitorStatus();
 }
 int modesets() const { return dp1->modesets+dp2->modesets+xr->modesets; }
};

int main() {
 try {
   { // Before the fix: a layout switch's reload modesets XR-1 (to the catch-all) and the glasses (to their preferred mode).
     World w; w.reload(false);
     CHECK(w.xr->modesets==1 && w.dp2->modesets==1 && w.dp1->modesets==0);
     CHECK(w.xr->m_activeMonitorRule.m_scale==1.25);  // every window on the XR desktop re-laid out at another scale
   }
   { // Hooked: master -> scrolling -> floating -> master, each a reload: nothing is modeset, the rules are still there.
     World w;
     for(int i=0;i<3;++i) w.reload(true);
     CHECK(w.modesets()==0 && w.xr->soft==0 && w.dp2->soft==0);
     CHECK(w.xr->m_activeMonitorRule.m_resolution==xrRule.m_resolution && w.dp2->m_activeMonitorRule.m_refreshRate==120);
     const auto& rules=Config::manager.all();
     CHECK(std::ranges::count_if(rules,[](const auto& r){return r.m_name=="XR-1";})==1);
     CHECK(std::ranges::count_if(rules,[](const auto& r){return r.m_name=="DP-2";})==1);
     CHECK(std::ranges::count_if(rules,[](const auto& r){return r.m_name.empty();})==1);  // the config's own, not doubled
   }
   { // The config wins wherever it names an output: a changed main display, the glasses by name or by description.
     World w;
     w.reload(true,[](Config::RuleManager& r){ r.add(catchAll); r.add({"DP-1",{3840,1080},{0,0},1.25,240}); });
     CHECK(w.dp1->modesets==1 && w.dp2->modesets==0 && w.xr->modesets==0);
     World by_name;
     by_name.reload(true,[](Config::RuleManager& r){ userConfig(r); r.add({"DP-2",{1920,1080},{4096,0},1,60}); });
     CHECK(by_name.dp2->modesets==1 && by_name.xr->modesets==0);
     World by_desc;
     by_desc.reload(true,[](Config::RuleManager& r){ userConfig(r); r.add({"desc:VITURE",{1920,1080},{4096,0},1,60}); });
     CHECK(by_desc.dp2->modesets==1 && by_desc.dp2->m_activeMonitorRule.m_refreshRate==60);
     World xr_named;  // a same-name rule from the user's config wins (the stateReloaded listener then latches ruleLost)
     xr_named.reload(true,[](Config::RuleManager& r){ userConfig(r); r.add({"XR-1",{1920,1080},{8000,0},1,60}); });
     CHECK(xr_named.xr->modesets==1 && xr_named.dp2->modesets==0);
   }
   { // No live run (stopped, or the rule was lost): a reload is entirely the user's, as before.
     World stopped; stopped.object->live=false; stopped.reload(true);
     CHECK(stopped.xr->modesets==1 && stopped.dp2->modesets==1);
     World lost; lost.object->ruleLost=true; lost.reload(true);
     CHECK(lost.xr->modesets==1 && lost.dp2->modesets==1);
   }
   { // Only a rule in effect is kept: one not applied yet (here: never) is the config's to decide.
     World w; Config::manager.add({"DP-2",{1920,1080},{4096,0},1,60});  // added, not applied (no ensureMonitorStatus)
     w.reload(true);
     CHECK(w.dp2->modesets==1 && w.dp2->m_activeMonitorRule.m_scale==1.25 && w.xr->modesets==0);
   }
   std::cout<<"config reloads keep the run's monitor rules: no modeset\n";
 } catch(const std::exception& e) { std::cerr<<e.what()<<"\n"; return 1; }
}
