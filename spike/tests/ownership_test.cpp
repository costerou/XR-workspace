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
    int reshapes=0; bool reshapeOk=true; Json reshapedTo;
    bool reshape(const Handle& o,const Json&,const Json& after) override { ++reshapes; reshapedTo=after; return reshapeOk && o->live; }
};
// A backend that never implemented reshape (the boundary doubles): refused, the entry is no longer trusted.
struct NoReshape : Fake {
    bool reshape(const Handle& o,const Json& before,const Json& after) override { return Backend::reshape(o,before,after); }
};
Json spec(int index=0) { return {{"id", "uuid"+std::to_string(index)}, {"name","output"+std::to_string(index)}}; }
// Which step a refused removal stopped at: what recovery tells a person (tools/native_spike/ownership.py REMOVAL_STEPS).
std::string refusedAt(const std::function<void(Fake&)>& arrange) {
    Fake f; Registry r(f); CHECK(r.create("s","r",spec())=="live");
    arrange(f);
    CHECK(r.remove("s","r","uuid0",f.fallback)=="blocked");
    return r.entry("uuid0").blocked;
}
int main(int argc,char** argv) {
    if(argc==2 && std::string(argv[1])=="--validate") {
        std::string wire; char c;
        while(wire.size()<=8192 && std::cin.get(c)) wire+=c;
        try { parse_request(wire); return 0; } catch(...) { return 2; }
    }
    if(argc==2 && std::string(argv[1])=="--cursor-name") {  // the contract test: every fixture name, one per stdin read
        std::string name; char c;
        while(name.size()<=8192 && std::cin.get(c)) name+=c;
        std::cout<<cursorShapeName(name);
        return 0;
    }
    CHECK(refusedAt([](Fake& f){f.fallback->live=false;})=="unavailable");
    CHECK(refusedAt([](Fake& f){f.pinned=true;})=="evacuate");
    CHECK(refusedAt([](Fake& f){f.special=true;})=="evacuate");
    CHECK(refusedAt([](Fake& f){f.dropFallback=true;})=="unavailable");
    CHECK(refusedAt([](Fake& f){f.reach=false;})=="unreachable");
    CHECK(refusedAt([](Fake& f){f.rule=false;})=="rule");
    CHECK(refusedAt([](Fake& f){f.reachAfterDestroy=false;})=="unreachable");
    // A requested cursor's name, as the broker may report it: a plain word or nothing, never compositor-supplied text.
    for(const char* good:{"text","vertical-text","xterm","ibeam","left_ptr","default","nwse-resize","Text","e29285e634086352946a0e7090d73106","a"})
        CHECK(cursorShapeName(good)==good);
    CHECK(cursorShapeName(std::string(64,'a'))==std::string(64,'a'));
    for(const std::string& bad:{std::string(),std::string(65,'a'),std::string("te xt"),std::string("-text"),std::string("_text"),std::string(".text"),
                                std::string("te\"xt"),std::string("te\nxt"),std::string("te/xt"),std::string("te\\xt"),std::string("t\xc3\xa9xt"),
                                std::string("te\0xt",5),std::string("text "),std::string(" text")})
        CHECK(cursorShapeName(bad).empty());
    // Which changes go out on socket2: each change once, and never more than a bounded number in a second.
    {
        CursorNotice notice;
        CHECK(!notice.announce("",1000));                   // nothing was said yet, and nothing is the start
        CHECK(notice.announce("text",1000) && !notice.announce("text",1001) && !notice.announce("text",9000));
        CHECK(notice.announce("left_ptr",9001) && notice.announce("",9002) && !notice.announce("",9003));
        CursorNotice flood;
        unsigned said=0;
        for(unsigned i=0;i<1000;++i) said+=flood.announce(i%2 ? "text":"left_ptr",5000+i/10);  // 1000 flips in 100 ms
        CHECK(said==CursorNotice::kPerSecond);
        CHECK(!flood.announce("other",5999));               // still that second
        CHECK(flood.announce("other",6000));                // the next one: what was held back is not lost for good
        CHECK(!flood.announce("other",6001));
        CursorNotice backwards;
        CHECK(backwards.announce("text",5000) && backwards.announce("left_ptr",100));  // a clock that steps back starts anew
    }
    // The op's wire: schema 1, an empty payload, nothing else in it.
    {
        const Json read={{"schema",1},{"session","session"},{"run_id","run"},{"request_id",1},{"epoch","epoch"},{"op","cursor_shape"},{"payload",Json::object()}};
        CHECK(parse_request("xrspike "+read.dump())["op"]=="cursor_shape");
        auto refused=[&](Json j) { try { parse_request("xrspike "+j.dump()); return false; } catch(...) { return true; } };
        for(auto payload:{Json{{"id","x"}},Json{{"shape","text"}},Json{{"mode","isolated-vm"}}}) { Json j=read; j["payload"]=payload; CHECK(refused(j)); }
        { Json j=read; j["schema"]=2; CHECK(refused(j)); }
        { Json j=read; j["schema"]=2; j["payload"]={{"mode","isolated-vm"}}; CHECK(refused(j)); }
        { Json j=read; j["payload"]=Json::array(); CHECK(refused(j)); }
        { Json j=read; j["extra"]=1; CHECK(refused(j)); }
    }
    // What it may read: this run's live outputs, no one else's, nothing failed, invalid or removed.
    {
        Fake b; Registry r(b);
        CHECK(r.liveOutputs("session","run").empty());
        CHECK(r.create("session","run",spec(0))=="live");
        CHECK(r.create("session","run",spec(1))=="live");
        CHECK(r.create("session","other",spec(2))=="live");
        CHECK(r.liveOutputs("session","run")==(std::vector<Handle>{b.objects[0],b.objects[1]}));
        CHECK(r.liveOutputs("session","other")==(std::vector<Handle>{b.objects[2]}));
        CHECK(r.liveOutputs("elsewhere","run").empty());
        CHECK(r.liveOutputs("session","nobody").empty());
        b.objects[1]->live=false;                                     // gone from the compositor, not yet noticed
        CHECK(r.liveOutputs("session","run")==(std::vector<Handle>{b.objects[0]}));
        b.rule=false; CHECK(r.inspect("session","run","uuid0")=="invalid"); b.rule=true;  // no longer trusted
        CHECK(r.liveOutputs("session","run").empty());
        b.failAt=4; CHECK(r.create("session","run",spec(3))=="failed");
        CHECK(r.liveOutputs("session","run").empty());
        const int creates=b.creates,destroys=b.destroys,configured=b.configured;
        (void)r.liveOutputs("session","other");                       // and reading changes nothing
        CHECK(b.creates==creates && b.destroys==destroys && b.configured==configured);
    }
    // Output scales (geometry.SCALES, the pill's Scale dropdown): quarter steps 1 .. 2, then 2.5 and 3; nothing between.
    for(double sc : {1.0,1.25,1.5,2.0,2.5,3.0}) CHECK(validDesktop(1920,1080,sc));
    CHECK(validDesktop(2800,1400,1.75) && !validDesktop(1920,1080,1.75) && !validDesktop(2560,1440,3.0));
    for(double sc : {0.75,2.25,2.75,3.25,3.5,4.0,1.1}) CHECK(!validDesktop(1920,1080,sc));
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
    CHECK(!r.entry("uuid0").blocked.empty());  // a refusal always says where it stopped
    b.reach=true;
    // A removed slot is created again under a new id (a pill's Add after a Remove: the lowest free XR-n, desktops.py).
    // Regression (ultrawide-add-monitor, 2026-11-03): one ultrawide XR-1 left after removing XR-2 and XR-3, Add new
    // asked for XR-2 again and the broker refused it ("run slot already used"), so nothing was added.
    auto reused=spec(); reused["id"]="new-uuid";
    CHECK(r.create("session","run",reused)=="live"); CHECK(b.creates==4);
    // ... but never while the slot is held: a live output, or one whose creation failed (its output may still exist).
    auto twice=spec(); twice["id"]="third-uuid";
    bool nameReuse=false; try { r.create("session","run",twice); } catch(...) {nameReuse=true;} CHECK(nameReuse && b.creates==4);
    {
        Fake f; f.failAt=1; Registry registry(f);
        CHECK(registry.create("s","r",spec())=="failed");
        auto again=spec(); again["id"]="again-uuid";
        bool held=false; try { registry.create("s","r",again); } catch(...) {held=true;} CHECK(held && f.creates==1);
    }
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
    // Per-desktop geometry: same rules as tools/native_spike/geometry.validate_desktop.
    for(auto [w,h,sc]:std::vector<std::tuple<int,int,double>>{{3840,1080,1},{3440,1440,1.25},{2560,1440,2},{640,480,1}}) {
        auto good=valid; good["payload"]["width"]=w; good["payload"]["height"]=h; good["payload"]["scale"]=sc;
        CHECK(parse_request("xrspike "+good.dump())==good);
    }
    for(int fault=0;fault<5;++fault) {
        auto bad=valid; auto& q=bad["payload"];
        if(fault==0) { q["width"]=3840; q["height"]=1080; q["scale"]=1.75; }
        if(fault==1) q["width"]=1921;
        if(fault==2) q["width"]=7682;
        if(fault==3) q["scale"]=1.1;
        if(fault==4) q["refresh_millihz"]=60000;
        bool rejected=false; try { parse_request("xrspike "+bad.dump()); } catch(const std::exception& e) { rejected=std::string(e.what())=="geometry"; } CHECK(rejected);
    }
    // Stable Omarchy-monitor names; workspaces are never planned by the broker.
    for(int fault=0;fault<4;++fault) {
        auto bad=valid;
        if(fault==0) bad["payload"]["name"]="XR-SPIKE-run-0";
        if(fault==1) bad["payload"]["name"]="XR-2";
        if(fault==2) bad["payload"]["workspace"]=1;
        if(fault==3) bad["payload"]["name"]="XR-01";
        bool rejected=false; try { parse_request("xrspike "+bad.dump()); } catch(...) {rejected=true;} CHECK(rejected);
    }
    {   // XR-4: a fourth desktop added during a run (index 3); never a fifth.
        auto fourth=valid; fourth["payload"]["name"]="XR-4"; fourth["payload"]["index"]=3;
        CHECK(parse_request("xrspike "+fourth.dump())==fourth);
        auto fifth=valid; fifth["payload"]["name"]="XR-5"; fifth["payload"]["index"]=4;
        bool rejected=false; try { parse_request("xrspike "+fifth.dump()); } catch(...) {rejected=true;} CHECK(rejected);
    }
    {   // reshape: the create payload's exact shape and rules (an Ultrawide toggle, or a neighbour sliding along the row).
        auto wide=valid; wide["op"]="reshape"; wide["payload"]["width"]=2560; wide["payload"]["x"]=1920;
        CHECK(parse_request("xrspike "+wide.dump())==wide);
        for(int fault=0;fault<4;++fault) {
            auto bad=wide; auto& q=bad["payload"];
            if(fault==0) q["width"]=2561;
            if(fault==1) q.erase("index");
            if(fault==2) q["name"]="XR-2";
            if(fault==3) q["extra"]=1;
            bool rejected=false; try { parse_request("xrspike "+bad.dump()); } catch(...) {rejected=true;} CHECK(rejected);
        }
        Fake f; Registry registry(f);
        Json one={{"id","uuid-r"},{"name","XR-1"},{"index",0},{"width",1920},{"height",1080},{"refresh_millihz",120000},{"scale",1.0},{"x",0},{"y",0}};
        CHECK(registry.create("s","run",one)=="live");
        auto to=one; to["width"]=2560; to["x"]=40;
        CHECK(registry.reshape("s","run",to)=="live" && f.reshapes==1 && f.reshapedTo==to);
        CHECK(registry.entry("uuid-r").spec==to);
        CHECK(registry.reshape("s","run",to)=="live" && f.reshapes==1);  // nothing to change: no compositor call
        // The id is known and its spec moved on: replaying the old create collides instead of creating anything.
        bool collided=false; try { registry.create("s","run",one); } catch(...) {collided=true;} CHECK(collided);
        for(auto key:{"name","index","refresh_millihz"}) {  // never another output's identity
            auto other=to; other[key]= std::string(key)=="name" ? Json("XR-2") : Json(std::string(key)=="index" ? 1 : 60000);
            bool refused=false; try { registry.reshape("s","run",other); } catch(...) {refused=true;} CHECK(refused);
        }
        bool foreign=false; try { registry.reshape("s","other-run",to); } catch(...) {foreign=true;} CHECK(foreign);
        f.reshapeOk=false; auto back=to; back["width"]=1920;
        CHECK(registry.reshape("s","run",back)=="invalid");  // a failed reshape is not trusted again ...
        CHECK(registry.reshape("s","run",to)=="invalid" && f.reshapes==2);
        CHECK(registry.remove("s","run","uuid-r",f.fallback)=="dead");  // ... but recovery still removes it
        NoReshape n; Registry plain(n);
        CHECK(plain.create("s","run",one)=="live");
        CHECK(plain.reshape("s","run",to)=="invalid");
    }
    valid["payload"]["x"]=18446744073709551615ULL;
    bool overflow=false; try { parse_request("xrspike "+valid.dump()); } catch(...) {overflow=true;} CHECK(overflow);
    std::cout << "ownership lifecycle passed\n";
}
