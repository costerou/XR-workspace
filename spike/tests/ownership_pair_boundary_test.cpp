#include "registry.hpp"
#include <sys/stat.h>
#include <sys/sysmacros.h>
#include <iostream>
#include <optional>
#include <thread>
using namespace xr::ownership;
#define CHECK(x) do { if(!(x)) throw std::runtime_error(#x); } while(0)
template<class T> using SP=std::shared_ptr<T>;
namespace Aquamarine {
enum {AQ_BACKEND_DRM=1};
struct IBackendImplementation { int kind=1,fd=10; int type(){return kind;} int drmFD(){return fd;} };
struct IOutput {
    virtual ~IOutput()=default;
    std::string name;
    SP<IBackendImplementation> backend=std::make_shared<IBackendImplementation>();
    auto getBackend(){return backend;}
};
struct CDRMOutput : IOutput { int connector=31; int getConnectorID(){return connector;} };
}
struct Monitor {
    SP<Aquamarine::IOutput> m_output;
    std::string m_name;
    bool on=true,m_dpmsStatus=true,m_mirrorOf=false;
    std::vector<int> m_mirrors;
    bool enabled(){return on;}
};
using PHLMONITOR=SP<Monitor>;
// Mirrors Hyprutils CWeakPointer semantics used by production (expired != live).
struct PHLMONITORREF {
    std::weak_ptr<Monitor> w;
    PHLMONITORREF& operator=(const PHLMONITOR& s) { w=s; return *this; }
    bool operator!=(const PHLMONITOR& s) const { return w.lock()!=s; }
};
struct NativeObject : Object {
    SP<Aquamarine::IOutput> output;
    SP<Aquamarine::IBackendImplementation> pairBackend;
    std::optional<PairObservation> pairProof;
    bool legacyDrmEligible=false;
    PHLMONITORREF monitor;
    bool monitorBound=false;
};
auto native(const Handle& h){return std::static_pointer_cast<NativeObject>(h);}
namespace State {
struct Monitors {std::vector<PHLMONITOR> ms; auto& allMonitors(){return ms;}} monitors;
auto monitorState(){return &monitors;}
struct Workspaces {std::vector<int> workspaceRefs(){return {};}} workspaces;
auto workspaceState(){return &workspaces;}
}
bool statFail=false,character=true;
int pairFstat(int fd,struct stat* st) {
    if(statFail || fd<0) return -1;
    st->st_mode=character ? S_IFCHR:S_IFREG; st->st_rdev=makedev(226,fd-10); return 0;
}
#define fstat pairFstat
namespace Desktop {
struct Windows {std::vector<int> windows(){return {};}} window;
auto windowState(){return &window;}
}
struct Boundary : PairBackend, Backend {
    int selections=0;
    Handle wrap(SP<Aquamarine::IOutput> o,bool legacy=false) {++selections; auto h=std::make_shared<NativeObject>(); h->output=o; h->legacyDrmEligible=legacy; return h;}
    int mutations=0;
    bool collision(const Json&) override {return false;}
    // Lifecycle doubles succeed only through the production fallback eligibility check.
    bool create(const Json&,const Capture& capture) override {++mutations; capture(std::make_shared<Object>(),true); return true;}
    bool configure(const Handle&,const Json&) override {return true;}
    bool verified(const Handle& h,const Json&) override {return h->live;}
    bool evacuate(const Handle&,const Handle& fallback) override {++mutations; return usable(fallback);}
    bool reachable(const Handle&,const Handle& fallback) override {return usable(fallback);}
    bool destroy(const Handle& h) override {++mutations; h->live=false; return true;}
    bool cleanup(const Handle&,const Json&) override {++mutations; return true;}
    Json observation(const Handle&) {return nullptr;}
#include "ownership_pair_methods.inc"
};
#undef fstat
using NativeBackend=Boundary;
struct Run {std::string session,name,token; Handle fallback; bool isolated=false;};
std::string randomToken(){static unsigned n=0; auto s=std::to_string(++n); return std::string(48-s.size(),'a')+s;}
#include "ownership_pair_broker.inc"
void twoOutputs() {
    State::monitors.ms.clear(); statFail=false; character=true;
    for(int i=0;i<2;++i) {
        auto m=std::make_shared<Monitor>(); auto o=std::make_shared<Aquamarine::CDRMOutput>();
        o->name="Virtual-"+std::to_string(i+1); o->connector=31+i;
        m->m_name=o->name; m->m_output=o; State::monitors.ms.push_back(m);
    }
}
Json envelope(int schema,const std::string& epoch,const std::string& op,Json payload) {
    return {{"schema",schema},{"session","guest"},{"run_id","run"},{"request_id",1},{"epoch",epoch},
        {"op",op},{"payload",std::move(payload)}};
}
Json send(Broker& b,const Json& j) {return Json::parse(b.handle("xrspike "+j.dump()));}
const std::string uuid="12345678-1234-1234-1234-123456789abc";
Json spec(bool isolated) {
    Json s={{"id",uuid},{"name","XR-SPIKE-run-0"},{"index",0},{"width",1920},{"height",1080},
        {"refresh_millihz",120000},{"scale",1},{"x",1280},{"y",0},{"workspace",11},{"workspace_name","XR-SPIKE-run-workspace-0"}};
    if(isolated) s["mode"]="isolated-vm";
    return s;
}
// Isolated lifecycle is bound to the pair's own retained fallback; never name adoption.
void isolatedLifecycle() {
    const Json hello=envelope(2,"unknown","pair_hello",{{"mode","isolated-vm"},{"target_name","Virtual-1"},{"fallback_name","Virtual-2"}});
    for(int fault=0;fault<8;++fault) {
        twoOutputs(); Broker broker;
        auto reply=send(broker,hello); CHECK(reply.contains("result"));
        const std::string epoch=reply["epoch"], fallback=reply["result"]["fallback"]["handle"];
        CHECK(broker.backend.selections==2);
        // Schema-1 lifecycle and schema-1 hello never reach a pair-bound run.
        CHECK(send(broker,envelope(1,epoch,"create",spec(false))).contains("error"));
        CHECK(send(broker,envelope(1,epoch,"inspect",{{"id",uuid}})).contains("error"));
        CHECK(send(broker,envelope(1,"unknown","hello",{{"fallback_name","Virtual-2"}})).contains("error"));
        CHECK(broker.backend.mutations==0 && broker.backend.selections==2);
        auto m=State::monitors.ms[fault%2]; auto o=std::dynamic_pointer_cast<Aquamarine::CDRMOutput>(m->m_output);
        bool target=fault%2==0, blocked=true;
        switch(fault/2) {
        case 0: blocked=false; break;
        case 1: m->m_output=std::make_shared<Aquamarine::CDRMOutput>(*o); break; // same name/connector, new object
        case 2: ++o->connector; break;
        case 3: m->m_dpmsStatus=false; break;
        }
        auto created=send(broker,envelope(2,epoch,"create",spec(true)));
        if(blocked) {
            CHECK(created.contains("error") && broker.backend.mutations==0);
            continue;
        }
        CHECK(created["schema"]==2 && created["result"]["state"]=="live" && broker.backend.mutations==1);
        CHECK(created["result"]["fallback_usable"]==true && created["result"]["physical"]==false);
        auto inspected=send(broker,envelope(2,epoch,"inspect",{{"id",uuid},{"mode","isolated-vm"}}));
        CHECK(inspected["result"]["state"]=="live");
        // In-place fallback DRM identity change (same object) is unusable for recovery.
        auto fallbackOutput=std::dynamic_pointer_cast<Aquamarine::CDRMOutput>(State::monitors.ms[1]->m_output);
        for(int change=0;change<2;++change) {
            if(change==0) ++fallbackOutput->connector; else ++fallbackOutput->backend->fd;
            CHECK(send(broker,envelope(2,epoch,"inspect",{{"id",uuid},{"mode","isolated-vm"}}))["result"]["fallback_usable"]==false);
            if(change==0) --fallbackOutput->connector; else --fallbackOutput->backend->fd;
        }
        CHECK(send(broker,envelope(2,epoch,"inspect",{{"id",uuid},{"mode","isolated-vm"}}))["result"]["fallback_usable"]==true);
        // Wrong fallback token, then the pair's own token.
        Json remove={{"id",uuid},{"name","XR-SPIKE-run-0"},{"fallback",std::string(48,'f')},{"mode","isolated-vm"}};
        CHECK(send(broker,envelope(2,epoch,"evacuate_remove",remove)).contains("error"));
        remove["fallback"]=fallback;
        // Fallback substitution after creation blocks recovery; target loss does not.
        auto swap=State::monitors.ms[target ? 1:0];
        swap->m_output=std::make_shared<Aquamarine::CDRMOutput>(*std::dynamic_pointer_cast<Aquamarine::CDRMOutput>(swap->m_output));
        CHECK(send(broker,envelope(2,epoch,"inspect",{{"id",uuid},{"mode","isolated-vm"}}))["result"]["fallback_usable"]==!target);
        auto removed=send(broker,envelope(2,epoch,"evacuate_remove",remove))["result"];
        CHECK(removed["state"]==(target ? "blocked":"dead") && removed["fallback_usable"]==!target);
        CHECK(broker.backend.mutations==(target ? 1:4)); // blocked before evacuation
        CHECK(send(broker,envelope(2,epoch,"create",spec(true))).contains("error"));
        // A restarted broker has no pair authority for saved epoch/handles.
        Broker restarted; restarted.epoch=epoch;
        CHECK(send(restarted,envelope(2,epoch,"inspect",{{"id",uuid},{"mode","isolated-vm"}})).contains("error"));
        CHECK(restarted.backend.selections==0 && restarted.backend.mutations==0);
    }
    // A legacy run cannot be driven through schema-2 lifecycle.
    twoOutputs(); Broker legacy;
    auto r=send(legacy,envelope(1,"unknown","hello",{{"fallback_name","Virtual-2"}}));
    CHECK(r.contains("result"));
    CHECK(send(legacy,envelope(2,r["epoch"],"create",spec(true))).contains("error"));
    CHECK(send(legacy,envelope(2,r["epoch"],"inspect",{{"id",uuid},{"mode","isolated-vm"}})).contains("error"));
    CHECK(legacy.backend.mutations==0);
}
// Regression (physical phys1 + VM xr-isolated-bindrace): a strong broker reference kept a
// destroyed CMonitor alive, so Hyprland's late wl_output bind dereferenced its torn-down
// output instead of seeing an expired monitor. The broker must hold monitors weakly.
void monitorNotRetained() {
    twoOutputs(); Boundary b;
    auto handle=b.selectPairOutput("Virtual-1");
    CHECK(b.resolve(handle));
    std::weak_ptr<Monitor> probe=State::monitors.ms[0];
    State::monitors.ms.erase(State::monitors.ms.begin());
    CHECK(probe.expired());
    CHECK(!b.resolve(handle));
    // A new monitor for the same retained output is a replacement, never re-adopted.
    auto replacement=std::make_shared<Monitor>(); replacement->m_name="Virtual-1";
    replacement->m_output=native(handle)->output; State::monitors.ms.push_back(replacement);
    CHECK(!b.resolve(handle));
}
int main() {
    monitorNotRetained();
    isolatedLifecycle();
    for(int side=0;side<2;++side) for(int fault=0;fault<20;++fault) {
        State::monitors.ms.clear(); statFail=false; character=true;
        for(int i=0;i<2;++i) {
            auto m=std::make_shared<Monitor>(); auto o=std::make_shared<Aquamarine::CDRMOutput>();
            o->name="Virtual-"+std::to_string(i+1); o->connector=31+i;
            m->m_name=o->name; m->m_output=o; State::monitors.ms.push_back(m);
        }
        Boundary b; unsigned token=0;
        PairRegistry r(b,[&]{return std::string(47,'a')+std::to_string(++token);});
        Json j={{"schema",2},{"session","guest"},{"run_id","run"},{"request_id",1},{"epoch","unknown"},
            {"op","pair_hello"},{"payload",{{"mode","isolated-vm"},{"target_name","Virtual-1"},{"fallback_name","Virtual-2"}}}};
        auto call=[&] {return r.handle(parse_request("xrspike "+j.dump()),"epoch");};
        if(side==0 && fault==0) {
            auto second=std::dynamic_pointer_cast<Aquamarine::CDRMOutput>(State::monitors.ms[1]->m_output);
            second->connector=31;
            Broker alias;
            CHECK(Json::parse(alias.handle("xrspike "+j.dump())).contains("error"));
            CHECK(alias.backend.mutations==0);
            second->connector=32;
            State::monitors.ms.push_back(std::make_shared<Monitor>(*State::monitors.ms[0]));
            Broker ambiguous;
            CHECK(Json::parse(ambiguous.handle("xrspike "+j.dump())).contains("error"));
            State::monitors.ms.pop_back();
        }
        Broker broker;
        auto reply=Json::parse(broker.handle("xrspike "+j.dump()));
        CHECK(reply.contains("result") && reply["schema"]==2);
        auto legacy=j; legacy["schema"]=1; legacy["op"]="hello";
        legacy["payload"]={{"fallback_name","Virtual-2"}};
        CHECK(Json::parse(broker.handle("xrspike "+legacy.dump())).contains("error"));
        CHECK(broker.backend.mutations==0);
        Broker legacyFirst;
        CHECK(Json::parse(legacyFirst.handle("xrspike "+legacy.dump())).contains("result"));
        CHECK(Json::parse(legacyFirst.handle("xrspike "+j.dump())).contains("error"));
        auto saved=j; saved["op"]="pair_observe"; saved["epoch"]=reply["epoch"];
        saved["payload"]={{"mode","isolated-vm"},{"target",reply["result"]["target"]["handle"]},
            {"fallback",reply["result"]["fallback"]["handle"]}};
        CHECK(Json::parse(broker.handle("xrspike "+saved.dump()))["result"]==reply["result"]);
        Broker restarted; restarted.epoch=broker.epoch;
        CHECK(Json::parse(restarted.handle("xrspike "+saved.dump())).contains("error"));
        CHECK(restarted.backend.selections==0 && restarted.backend.mutations==0);
        auto proof=call(); CHECK(proof["target"]["device_major"]==226);
        CHECK(proof["fallback"]["connector_id"]==32);
        j["op"]="pair_observe"; j["epoch"]="epoch";
        j["payload"]={{"mode","isolated-vm"},{"target",proof["target"]["handle"]},{"fallback",proof["fallback"]["handle"]}};
        CHECK(call()==proof);
        auto m=State::monitors.ms[side]; auto o=std::dynamic_pointer_cast<Aquamarine::CDRMOutput>(m->m_output);
        switch(fault) {
        case 0: m->on=false; break;
        case 1: m->m_dpmsStatus=false; break;
        case 2: m->m_mirrorOf=true; break;
        case 3: m->m_mirrors.push_back(1); break;
        case 4: State::monitors.ms.erase(State::monitors.ms.begin()+side); break;
        case 5: m->m_output=std::make_shared<Aquamarine::CDRMOutput>(*o); break; // same name, device, connector
        case 6: State::monitors.ms[side]=std::make_shared<Monitor>(*m); break; // same output, new monitor
        case 7: o->backend->kind=2; break;
        case 8: o->backend=std::make_shared<Aquamarine::IBackendImplementation>(*o->backend); break;
        case 9: ++o->backend->fd; break; // same name/connector, wrong device
        case 10: ++o->connector; break;
        case 11: statFail=true; break;
        case 12: character=false; break;
        case 13: m->m_name="renamed"; break;
        case 14: o->connector=0; break;
        case 15: o->backend->fd=-1; break;
        case 16: m->m_output=std::make_shared<Aquamarine::IOutput>(*o); break;
        case 17: o->backend.reset(); break;
        case 18: {
            auto duplicate=std::make_shared<Monitor>(*m);
            duplicate->m_output=std::make_shared<Aquamarine::CDRMOutput>(*o);
            State::monitors.ms.push_back(duplicate); break;
        }
        case 19: State::monitors.ms.push_back(std::make_shared<Monitor>(*m)); break;
        }
        bool rejected=false; try {call();} catch(...) {rejected=true;} CHECK(rejected);
        CHECK(Json::parse(broker.handle("xrspike "+saved.dump())).contains("error"));
        CHECK(broker.backend.selections==2 && broker.backend.mutations==0);
        if(fault==0 || fault==1 || fault==2 || fault==3 || fault==7 || fault==11 || fault==12 || fault>=14) {
            bool initialBlocked=false;
            try {b.observePairOutput(b.selectPairOutput(m->m_name));} catch(...) {initialBlocked=true;}
            CHECK(initialBlocked);
        }
    }
    std::cout << "production pair boundary passed\n";
}
