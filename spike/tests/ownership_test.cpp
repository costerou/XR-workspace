#include "registry.hpp"
#include <iostream>
#include <stdexcept>
using namespace xr::ownership;
#define CHECK(x) do { if (!(x)) throw std::runtime_error(#x); } while (0)
struct Fake : Backend {
    Handle source, fallback = std::make_shared<Object>();
    int creates=0, destroys=0, configured=0, failAt=0;
    bool emit=true, wrong=false, die=false, migration=true, reach=true, rule=true, special=false, pinned=false, dropFallback=false;
    std::function<void()> callback;
    std::vector<Handle> objects;
    std::vector<Handle> removed;
    std::vector<Handle> configuredObjects;
    std::map<Handle,std::shared_ptr<int>> workspace, expectedWorkspace;
    Fake() { fallback->physical=true; }
    bool collision(const Json&) override { return false; }
    bool create(const Json&, const Capture& capture) override {
        ++creates; source=std::make_shared<Object>(); objects.push_back(source);
        if(callback) callback();
        if (emit) capture(source, !wrong);
        if (die) source->live=false;
        return creates!=failAt;
    }
    bool configure(const Handle& o,const Json&) override {
        ++configured; configuredObjects.push_back(o);
        workspace[o]=std::make_shared<int>(configured);
        expectedWorkspace[o]=workspace[o];
        return o->live;
    }
    bool verified(const Handle& o,const Json&) override { return o->live && rule && workspace[o]==expectedWorkspace[o]; }
    bool usable(const Handle& o) override { return o && o->live && o->physical; }
    bool evacuate(const Handle&,const Handle&) override { if(dropFallback) fallback->live=false; return migration && !special && !pinned; }
    bool reachAfterDestroy=true;
    bool reachable(const Handle&,const Handle&) override { return reach && (reachAfterDestroy || destroys==0); }
    bool destroy(const Handle& o) override { ++destroys; removed.push_back(o); o->live=false; o->live=false; return true; }
    bool cleanup(const Handle&,const Json&) override { return rule; }
    std::vector<Handle> released;
    void release(const Handle& o) override { released.push_back(o); }
};
Json spec(int index=0) { return {{"id", "uuid"+std::to_string(index)}, {"name","output"+std::to_string(index)}}; }
int main(int argc,char** argv) {
    if(argc==2 && std::string(argv[1])=="--validate") {
        std::string wire; char c;
        while(wire.size()<=8192 && std::cin.get(c)) wire+=c;
        try { parse_request(wire); return 0; } catch(...) { return 2; }
    }
    Fake b; Registry r(b);
    for(int i=0;i<3;++i) CHECK(r.create("session","run",spec(i))=="live");
    CHECK(b.creates==3 && b.workspace.size()==3);
    CHECK(b.workspace[b.objects[0]]!=b.workspace[b.objects[1]] && b.workspace[b.objects[1]]!=b.workspace[b.objects[2]]);
    CHECK(*b.workspace[b.objects[0]]==1 && *b.workspace[b.objects[1]]==2 && *b.workspace[b.objects[2]]==3);
    CHECK(r.create("session","run",spec())=="live"); CHECK(b.creates==3);
    CHECK(b.released.empty());
    CHECK(r.remove("session","run","uuid0",b.fallback)=="dead");
    // Workspace/snapshot references are dropped once dead (lingering empty workspaces).
    CHECK(b.released.size()==1 && b.released[0]==b.objects[0]);
    CHECK(r.remove("session","run","uuid0",b.fallback)=="dead"); CHECK(b.destroys==1 && b.released.size()==1);
    CHECK(r.create("session","run",spec())=="dead"); CHECK(b.creates==3);
    b.reach=false;
    CHECK(r.remove("session","run","uuid0",b.fallback)=="blocked");
    b.reach=true;
    auto reused=spec(); reused["id"]="new-uuid";
    bool nameReuse=false; try { r.create("session","run",reused); } catch(...) {nameReuse=true;} CHECK(nameReuse);
    b.rule=false;
    CHECK(r.create("session","run",spec(1))=="invalid");
    bool rejected=false;
    try { parse_request("xrspike {\"schema\":1,\"schema\":1}"); } catch(...) { rejected=true; }
    CHECK(rejected);
    for(int failure: {2,3}) {
        Fake f; f.failAt=failure; Registry registry(f);
        for(int i=0;i<failure;++i) CHECK(registry.create("s","r",spec(i))==(i+1==failure ? "failed":"live"));
        for(int i=failure-1;i>=0;--i) CHECK(registry.remove("s","r","uuid"+std::to_string(i),f.fallback)=="dead");
        CHECK(f.destroys==failure);
    }
    for(int fault=0;fault<3;++fault) {
        Fake f; Registry registry(f); f.emit=fault!=0; f.wrong=fault==1; f.die=fault==2;
        CHECK(registry.create("s","r",spec())=="failed"); CHECK(f.configured==0);
        CHECK(registry.create("s","r",spec())=="failed"); CHECK(f.creates==1);
    }
    for(int fault=0;fault<5;++fault) {
        Fake f; Registry registry(f); CHECK(registry.create("s","r",spec())=="live");
        f.reach=fault!=0; f.migration=fault!=1; f.special=fault==2; f.pinned=fault==3; f.dropFallback=fault==4;
        CHECK(registry.remove("s","r","uuid0",f.fallback)=="blocked"); CHECK(f.destroys==0 && f.released.empty());
    }
    { // Stable names: run B may reuse XR-1 after run A's is dead; neither can address the other's UUID.
        Fake f; Registry registry(f);
        Json a={{"id","uuid-a"},{"name","XR-1"}}, b={{"id","uuid-b"},{"name","XR-1"}};
        CHECK(registry.create("s","runA",a)=="live"); auto objectA=f.source;
        CHECK(registry.remove("s","runA","uuid-a",f.fallback)=="dead");
        CHECK(registry.create("s","runB",b)=="live"); auto objectB=f.source;
        CHECK(objectA!=objectB && objectB->live);
        CHECK(registry.remove("s","runA","uuid-a",f.fallback)=="dead"); CHECK(objectB->live && f.destroys==1);
        CHECK(registry.inspect("s","runA","uuid-a")=="dead");
        for(auto [run,id]: std::vector<std::pair<std::string,std::string>>{{"runB","uuid-a"},{"runA","uuid-b"}}) {
            bool unproven=false; try { registry.remove("s",run,id,f.fallback); } catch(...) {unproven=true;}
            CHECK(unproven && objectB->live);
        }
        CHECK(registry.remove("s","runB","uuid-b",f.fallback)=="dead"); CHECK(!objectB->live && f.destroys==2);
    }
    { // Destroyed but not proven reachable: stays blocked, yet compositor references are released.
        Fake f; Registry registry(f); CHECK(registry.create("s","r",spec())=="live"); f.reachAfterDestroy=false;
        CHECK(registry.remove("s","r","uuid0",f.fallback)=="blocked"); CHECK(f.destroys==1 && f.released.size()==1);
        CHECK(registry.remove("s","r","uuid0",f.fallback)=="blocked"); CHECK(f.destroys==1);
    }
    { // A replacement with the same lookup metadata is never a capability.
        Fake f; Registry registry(f); registry.create("s","r",spec()); auto original=f.source;
        original->live=false; f.source=std::make_shared<Object>();
        CHECK(registry.inspect("s","r","uuid0")=="invalid");
        CHECK(registry.remove("s","r","uuid0",f.fallback)=="blocked");
        CHECK(f.destroys==0 && f.configuredObjects.size()==1 && f.configuredObjects[0]==original);
        Registry restarted(f); bool unproven=false;
        try { restarted.inspect("s","r","uuid0"); } catch(...) { unproven=true; } CHECK(unproven);
    }
    { // Same numeric workspace ID, different object: reject, never reconfigure.
        Fake f; Registry registry(f); registry.create("s","r",spec());
        f.workspace[f.source]=std::make_shared<int>(*f.expectedWorkspace[f.source]);
        CHECK(registry.inspect("s","r","uuid0")=="invalid"); CHECK(f.configured==1);
    }
    { // Deferred reload mismatch invalidates rather than reapplies the user's rule.
        Fake f; Registry registry(f); registry.create("s","r",spec()); f.rule=false;
        CHECK(registry.inspect("s","r","uuid0")=="invalid");
        CHECK(registry.remove("s","r","uuid0",f.fallback)=="blocked"); CHECK(f.configured==1 && f.destroys==0);
    }
    { Fake f; Registry registry(f); bool guarded=false;
      f.callback=[&] { try { registry.create("s","r",spec(1)); } catch(...) { guarded=true; } };
      CHECK(registry.create("s","r",spec())=="live"); CHECK(guarded && f.creates==1);
      auto conflict=spec(); conflict["name"]="replacement"; bool collision=false;
      try { registry.create("s","r",conflict); } catch(...) { collision=true; } CHECK(collision && f.creates==1);
    }
    Json valid={{"schema",1},{"session","session"},{"run_id","run"},{"request_id",1},{"epoch","unknown"},{"op","hello"},{"payload",{{"fallback_name","DP-1"}}}};
    CHECK(parse_request("xrspike "+valid.dump())==valid);
    for(auto wire: {std::string("xrspike ")+std::string(8193,' '), std::string("wrong ")+valid.dump(),
                    std::string("xrspike ")+valid.dump().insert(1,"\"schema\":1,")}) {
        bool bad=false; try { parse_request(wire); } catch(...) {bad=true;} CHECK(bad);
    }
    valid["op"]="create";
    valid["payload"]={{"id","12345678-1234-4234-8234-123456789abc"},{"name","XR-1"},{"index",0},{"width",1920},{"height",1080},
        {"refresh_millihz",120000},{"scale",1.0},{"x",0},{"y",0}};
    CHECK(parse_request("xrspike "+valid.dump())==valid);
    // Stable Omarchy-monitor names; workspaces are never planned by the broker.
    for(int fault=0;fault<4;++fault) {
        auto bad=valid;
        if(fault==0) bad["payload"]["name"]="XR-SPIKE-run-0";
        if(fault==1) bad["payload"]["name"]="XR-2";
        if(fault==2) bad["payload"]["workspace"]=1;
        if(fault==3) bad["payload"]["name"]="XR-01";
        bool rejected=false; try { parse_request("xrspike "+bad.dump()); } catch(...) {rejected=true;} CHECK(rejected);
    }
    valid["payload"]["x"]=18446744073709551615ULL;
    bool overflow=false; try { parse_request("xrspike "+valid.dump()); } catch(...) {overflow=true;} CHECK(overflow);
    std::cout << "ownership lifecycle passed\n";
}
