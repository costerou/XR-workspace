// Actual plugin configure/verified bodies, with offline boundary doubles only.
// Pinned Hyprland efb5099: applyMonitorRuleSoft stores active rule; only
// MonitorLayoutController::arrange -> MonitorPositionController::arrange moves.
#include "registry.hpp"
#include "lifecycle.hpp"
#include <iostream>
#include <memory>
#include <optional>
#include <vector>
#include <functional>
using namespace xr::ownership;
#define CHECK(x) do { if(!(x)) throw std::runtime_error(#x); } while(0)
struct Vector2D {
 double x=0,y=0;
 Vector2D()=default; Vector2D(double x_,double y_):x(x_),y(y_){}
 bool operator==(const Vector2D&) const=default;
};
constexpr int WL_OUTPUT_TRANSFORM_NORMAL=0;
namespace Config {
struct CMonitorRule {
 std::string m_name,m_mirrorOf; Vector2D m_resolution,m_offset;
 double m_scale=1,m_refreshRate=60; int m_transform=0; bool m_disabled=false;
 bool operator==(const CMonitorRule&) const=default;
};
// Other fields are covered by ownership_native_test; this double has all fields
// consumed by these two production methods, with exact value equality.
bool sameRule(const CMonitorRule& a,const CMonitorRule& b){return a==b;}
struct RuleManager {
 std::vector<CMonitorRule> rules;
 auto& all(){return rules;}
 void add(CMonitorRule r){rules.push_back(r);}
};
RuleManager rules;
RuleManager* monitorRuleMgr(){return &rules;}
}
struct Monitor;
struct Workspace {
 int64_t m_id=2; std::string m_name="2"; bool m_isSpecialWorkspace=false;
 std::shared_ptr<Monitor> m_monitor;
 bool inert() const{return false;}
 void rename(std::string name){m_name=name;}
};
using W=std::shared_ptr<Workspace>;
struct Mode {Vector2D pixelSize; int refreshRate=0;};
struct Monitor {
 W m_activeWorkspace; int m_id=1;
 std::shared_ptr<Mode> m_currentMode;
 Vector2D m_pixelSize,m_position;
 double m_scale=1,m_refreshRate=60;
 int m_transform=0; bool m_dpmsStatus=true,m_mirrorOf=false,applyOK=true;
 Config::CMonitorRule active;
 bool enabled() const{return true;}
 bool applyMonitorRule(Config::CMonitorRule rule) {
   if(!applyOK) return false;
   active=rule;
   m_pixelSize=rule.m_resolution; m_refreshRate=rule.m_refreshRate;
   m_currentMode=std::make_shared<Mode>(Mode{rule.m_resolution,120000});
   // Deliberately NO position update: pinned production behavior.
   return true;
 }
 void changeWorkspace(W w,bool,bool,bool){m_activeWorkspace=w;}
};
namespace State {
struct WorkspaceState {
 std::vector<W> workspaces;
 auto workspacesCopy(){return workspaces;}
 W create(int64_t,int,std::string,bool){throw std::runtime_error("unexpected allocation");}
};
WorkspaceState ws;
WorkspaceState* workspaceState(){return &ws;}
struct Layout {
 std::shared_ptr<Monitor> source; int calls=0; bool suppress=false;
 std::function<void()> callback;
 void arrange() {
   ++calls;
   if(!suppress) source->m_position=source->active.m_offset;
   if(callback) callback();
 }
};
Layout layout;
Layout* monitorLayoutController(){return &layout;}
}
struct NativeObject: Object {
 W createdDefault,workspace; std::vector<W> beforeCreate;
 std::optional<Config::CMonitorRule> rule; bool ruleLost=false;
};
auto native(const Handle& h){return std::static_pointer_cast<NativeObject>(h);}
struct NativeBackend: Backend {
 std::shared_ptr<Monitor> monitor=std::make_shared<Monitor>();
 std::shared_ptr<NativeObject> object=std::make_shared<NativeObject>();
 std::vector<std::pair<std::weak_ptr<NativeObject>,Json>> configured;
 NativeBackend(){Config::rules.rules.clear(); State::ws.workspaces.clear(); State::layout={}; State::layout.source=monitor;}
 auto resolve(const Handle& h){return h && h->live ? monitor:nullptr;}
 bool collision(const Json&) override{return false;}
 bool create(const Json&,const Capture& capture) override {
   auto w=std::make_shared<Workspace>(); w->m_monitor=monitor;
   monitor->m_activeWorkspace=w; State::ws.workspaces.push_back(w);
   object->createdDefault=w; capture(object,true); return true;
 }
#include "ownership_methods.inc"
 bool usable(const Handle&) override{return true;}
 bool evacuate(const Handle&,const Handle&) override{return false;}
 bool reachable(const Handle&,const Handle&) override{return false;}
 bool cleanup(const Handle&,const Json&) override{return false;}
 bool destroy(const Handle&) override{return false;}
};
Json spec(int x,int y) {
 return {{"id","offset"},{"name","XR-SPIKE-offset-0"},{"workspace",2},
         {"workspace_name","XR-SPIKE-offset-workspace-0"},{"x",x},{"y",y}};
}
int main() {
 try {
   for(auto pos:std::vector<Vector2D>{{1280,0},{3200,0},{-1920,1080}}) {
     NativeBackend b; Registry r(b); auto p=spec(pos.x,pos.y);
     CHECK(r.create("s","r",p)=="live");
     CHECK(b.monitor->m_position==pos); CHECK(State::layout.calls==1);
     CHECK(b.object->workspace==b.object->createdDefault);
     CHECK(b.object->workspace->m_name=="XR-SPIKE-offset-workspace-0");
     // Later deferred reload sees full rule equality, so performs no arrange.
     CHECK(Config::sameRule(Config::rules.rules.at(0),b.monitor->active));
     CHECK(r.inspect("s","r","offset")=="live");
     CHECK(r.create("s","r",p)=="live"); CHECK(State::layout.calls==1);
     b.monitor->m_position.x+=1;
     CHECK(r.inspect("s","r","offset")=="invalid");
   }
   for(int fault=0;fault<4;++fault) {
     NativeBackend b; Registry r(b);
     if(fault==0) b.monitor->applyOK=false;
     if(fault==1) State::layout.suppress=true;
     if(fault==2) State::layout.callback=[&]{b.object->live=false;};
     if(fault==3) State::layout.callback=[]{Config::rules.rules.at(0).m_offset.x+=1;};
     CHECK(r.create("s","r",spec(1280,0))=="failed");
     CHECK(r.inspect("s","r","offset")=="failed");
     if(fault==0) CHECK(State::layout.calls==0);
   }
   std::cout<<"production configure/verify offset lifecycle passed\n";
 } catch(const std::exception& e){std::cerr<<e.what()<<"\n"; return 1;}
}
