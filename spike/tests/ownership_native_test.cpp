#include "lifecycle.hpp"
#include "registry.hpp"
#include <memory>
#include <vector>
#include <optional>
#include <iostream>
#include <stdexcept>
#include <xf86drmMode.h>
using namespace xr::ownership;
#define CHECK(x) do { if(!(x)) throw std::runtime_error(#x); } while(0)
struct Monitor;
struct Workspace {
 int m_id=1; std::string m_name="1"; bool m_wasCreatedEmpty=true;
 bool m_isSpecialWorkspace=false, dead=false, persistent=false; int windows=0;
 std::shared_ptr<Monitor> m_monitor;
 bool inert() const { return dead; } bool isPersistent() const {return persistent;}
};
using W=std::shared_ptr<Workspace>;
struct Monitor { W m_activeWorkspace; };
struct Rule {
 int m_autoDir=0;
 int m_name=0;
 int m_resolution=0;
 int m_offset=0;
 int m_scale=0;
 int m_refreshRate=0;
 int m_disabled=0;
 int m_transform=0;
 int m_mirrorOf=0;
 int m_enable10bit=0;
 int m_cmType=0;
 int m_sdrEotf=0;
 int m_sdrSaturation=0;
 int m_sdrBrightness=0;
 int m_reservedArea=0;
 int m_iccFile=0;
 int m_supportsWideColor=0;
 int m_supportsHDR=0;
 int m_sdrMinLuminance=0;
 int m_sdrMaxLuminance=0;
 int m_minLuminance=0;
 int m_maxLuminance=0;
 int m_maxAvgLuminance=0;
 drmModeModeInfo m_drmMode{}; std::optional<int> m_vrr;
 // Pinned compare omits VRR and EOTF.
 int compare(const Rule&) const { return 0; }
};
// Offline API model: creation synchronously allocates the lowest unused ID;
// moving the last active workspace creates a new source gap; disconnect moves
// inactive workspaces and drops the source active ref. Registry is production.
struct NativeModel : Backend {
 Handle output=std::make_shared<Object>(), fallback=std::make_shared<Object>();
 std::shared_ptr<Monitor> source=std::make_shared<Monitor>(), target=std::make_shared<Monitor>();
 std::vector<W> workspaces, beforeCreate, beforeEvacuation;
 W allocated, owned, gap; Rule expected, current; bool erased=false; int destroyed=0;
 bool strand=false, replaceGap=false;
 NativeModel(){fallback->physical=true;}
 W allocate() {
   auto w=std::make_shared<Workspace>();
   while(std::any_of(workspaces.begin(),workspaces.end(),[&](auto& x){return x->m_id==w->m_id;})) ++w->m_id;
   w->m_name=std::to_string(w->m_id);
   w->m_monitor=source; workspaces.push_back(w); source->m_activeWorkspace=w; return w;
 }
 bool collision(const Json&) override {return false;}
 bool create(const Json&,const Capture& capture) override {
   beforeCreate=workspaces;
   allocated=allocate(); // MonitorState::add -> onConnect -> setupDefaultWS
   capture(output,true); return true;
 }
 // Omarchy model (design 0725797): the broker never adopts/pins workspaces; `owned` is simply
 // the workspace Hyprland/Omarchy showed on the new output, tracked here for assertions only.
 bool configure(const Handle&,const Json&) override { owned=allocated; return true; }
 bool verified(const Handle&,const Json&) override {return sameRule(expected,current);}
 bool usable(const Handle& h) override {return h==fallback && h->live;}
 bool evacuate(const Handle&,const Handle&) override {
   beforeEvacuation=workspaces;
   for(auto& w:beforeEvacuation) if(w->m_monitor==source) {
     if(source->m_activeWorkspace==w) allocate();
     if(!strand) w->m_monitor=target;
   }
   gap=source->m_activeWorkspace;
   if(replaceGap) { auto other=std::make_shared<Workspace>(*gap); workspaces.back()=other; source->m_activeWorkspace=other; }
   return true;
 }
 bool reachable(const Handle& h,const Handle&) override {
   for(auto& w:workspaces) if(w->m_monitor==source)
     if(w!=gap || !retirementGap(w,beforeEvacuation,workspaces,source,h->live,[](auto& x){return x->windows==0;})) return false;
   // No mapped-window exemption, including windows on a purported gap.
   for(auto& w:workspaces) if(w->windows && w->m_monitor!=target) return false;
   return true;
 }
 // The plugin's cleanup (ownership_offset_test runs the real one): only our own rule goes, a changed one is the user's.
 bool cleanup(const Handle&,const Json&) override {
   if(sameRule(expected,current)) erased=true;
   return true;
 }
 bool destroy(const Handle& h) override {
   return disconnectRetiring(source,gap,[&]{return h->live;},[&]{
     ++destroyed;
     // Pinned onDisconnect migrates remaining workspaces. Null active means
     // moveWorkspaceToMonitor must not create another replacement.
     auto snapshot=workspaces;
     for(auto& w:snapshot) if(w->m_monitor==source) {
       if(source->m_activeWorkspace==w) allocate();
       w->m_monitor=target;
     }
     source->m_activeWorkspace.reset(); h->live=false; return true;
   });
 }
 int released=0;
 void release(const Handle&) override { ++released; owned.reset(); allocated.reset(); gap.reset(); beforeCreate.clear(); beforeEvacuation.clear(); }
};
void transaction(const std::string& test) {
 NativeModel b; Registry registry(b);
 Json spec={{"id","id"},{"name","XR-1"}};
 CHECK(registry.create("s","r",spec)=="live"); CHECK(b.owned==b.allocated);
 b.owned->windows=2;
 if(test=="vrr") b.current.m_vrr=1;
 if(test=="eotf") b.current.m_sdrEotf=1;
 if(test=="vrr" || test=="eotf") {
   CHECK(registry.inspect("s","r","id")=="invalid");
   // The rule is no longer ours, so it stays; the output is, so it goes. Reachability still decides that (below).
   CHECK(registry.remove("s","r","id",b.fallback)=="dead"); CHECK(!b.erased && b.destroyed==1);
 } else {
   auto owned=b.owned;
   CHECK(registry.remove("s","r","id",b.fallback)=="dead");
   CHECK(owned->windows==2 && owned->m_monitor==b.target);
   // Production release drops the gap/owned refs; the repeat dead check must not need them.
   CHECK(b.released==1 && !b.owned && !b.gap && b.beforeEvacuation.empty());
   CHECK(registry.remove("s","r","id",b.fallback)=="dead"); CHECK(b.destroyed==1);
   for(int fault=0;fault<2;++fault) {
     NativeModel bad; Registry r(bad); CHECK(r.create("s","r",spec)=="live");
     bad.owned->windows=1; bad.strand=fault==0; bad.replaceGap=fault==1;
     CHECK(r.remove("s","r","id",bad.fallback)=="blocked"); CHECK(bad.destroyed==0);
   }
 }
}
int main(int argc,char** argv) {
 try {
 const std::string test=argc>1?argv[1]:"";
 auto source=std::make_shared<Monitor>(), target=std::make_shared<Monitor>();
 std::vector<W> before, after;
 auto empty=[](const W& w){return w->windows==0;};
 if(test=="default") {
   // setupDefaultWS runs synchronously inside createOutput, allocating planner ID 1.
   auto allocated=std::make_shared<Workspace>(); allocated->m_monitor=source;
   source->m_activeWorkspace=allocated; after.push_back(allocated);
   CHECK(freshDefault(allocated,before,after,source));
   before=after; CHECK(!freshDefault(allocated,before,after,source));
   before.clear(); auto replacement=std::make_shared<Workspace>(*allocated);
   after={replacement}; CHECK(!freshDefault(allocated,before,after,source));
   after={allocated}; allocated->m_monitor=target;
   CHECK(!freshDefault(allocated,before,after,source));
 } else if(test=="retirement") {
   auto moved=std::make_shared<Workspace>(); moved->m_monitor=source;
   source->m_activeWorkspace=moved; before={moved};
   // Pinned last-active move allocates a replacement before moving the original.
   auto gap=std::make_shared<Workspace>(); gap->m_id=2; gap->m_name="2"; gap->m_monitor=source;
   source->m_activeWorkspace=gap; moved->m_monitor=target; after={moved,gap};
   CHECK(retirementGap(gap,before,after,source,true,empty));
   gap->m_name="user-renamed"; CHECK(!retirementGap(gap,before,after,source,true,empty)); gap->m_name="2";
   gap->m_wasCreatedEmpty=false; CHECK(!retirementGap(gap,before,after,source,true,empty)); gap->m_wasCreatedEmpty=true;
   bool live=true;
   // Disconnect must see no active source workspace, avoiding another gap.
   CHECK(disconnectRetiring(source,gap,[&]{return live;},[&]{
       CHECK(!source->m_activeWorkspace); gap->m_monitor=target; live=false; return true;
   }));
   CHECK(!source->m_activeWorkspace);
   gap->m_monitor=source; source->m_activeWorkspace=gap; live=true;
   CHECK(!disconnectRetiring(source,gap,[&]{return live;},[]{return false;}));
   CHECK(source->m_activeWorkspace==gap);
   try { disconnectRetiring(source,gap,[&]{return live;},[]()->bool{throw std::runtime_error("destroy");}); }
   catch(const std::runtime_error&) {}
   CHECK(source->m_activeWorkspace==gap);
   CHECK(!retirementGap(gap,before,after,source,false,empty));
   gap->windows=1; CHECK(!retirementGap(gap,before,after,source,true,empty)); gap->windows=0;
   gap->persistent=true; CHECK(!retirementGap(gap,before,after,source,true,empty)); gap->persistent=false;
   auto replacement=std::make_shared<Workspace>(*gap); after={moved,replacement};
   CHECK(!retirementGap(gap,before,after,source,true,empty));
   after={moved,gap}; before.push_back(gap);
   CHECK(!retirementGap(gap,before,after,source,true,empty));
 } else {
   Rule original, edited; CHECK(sameRule(original,edited));
   if(test=="vrr") edited.m_vrr=1; else edited.m_sdrEotf=1;
   CHECK(!sameRule(original,edited));
 }
 transaction(test);
 std::cout<<test<<" passed\n";
 } catch(const std::exception& e) { std::cerr<<e.what()<<"\n"; return 1; }
}
