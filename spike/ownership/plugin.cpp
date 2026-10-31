// Opt-in lifecycle-only module. No renderer, capture, input or config-file access.
#include <hyprland/src/plugins/PluginAPI.hpp>
#include <hyprland/src/Compositor.hpp>
#include <hyprland/src/debug/HyprCtl.hpp>
#include <hyprland/src/state/MonitorState.hpp>
#include <hyprland/src/state/WorkspaceState.hpp>
#include <hyprland/src/state/WorkspacePlacementController.hpp>
#include <hyprland/src/output/Monitor.hpp>
#include <hyprland/src/desktop/Workspace.hpp>
#include <hyprland/src/desktop/state/WindowState.hpp>
#include <hyprland/src/config/shared/monitor/MonitorRuleManager.hpp>
#include <aquamarine/backend/Headless.hpp>
#include <sys/random.h>
#include <thread>
#include "registry.hpp"
using namespace xr::ownership;
namespace {
std::string randomToken() {
    unsigned char bytes[24];
    if(getrandom(bytes,sizeof(bytes),GRND_NONBLOCK)!=sizeof(bytes)) throw std::runtime_error("entropy unavailable");
    std::string s; for(auto b:bytes) { s+="0123456789abcdef"[b>>4]; s+="0123456789abcdef"[b&15]; } return s;
}
struct NativeObject : Object {
    SP<Aquamarine::IOutput> output;
    PHLMONITOR monitor;
    PHLWORKSPACE workspace;
    CHyprSignalListener destroyListener;
    std::optional<Config::CMonitorRule> rule;
    bool ruleLost=false;
};
auto native(const Handle& h) { return std::static_pointer_cast<NativeObject>(h); }
bool eraseRule(NativeObject& o) {
    if(!o.rule) return true;
    auto& rules=Config::monitorRuleMgr()->allMut();
    auto found=rules.end();
    for(auto it=rules.begin();it!=rules.end();++it) if(it->m_name==o.rule->m_name) {
        if(found!=rules.end() || it->compare(*o.rule)!=Config::COMPARISON_FULL_MATCH) { o.ruleLost=true; return false; }
        found=it;
    }
    if(found==rules.end()) { o.ruleLost=true; return false; }
    rules.erase(found); o.rule.reset(); return true;
}
class NativeBackend final : public Backend {
public:
    SP<Aquamarine::IBackendImplementation> headless;
    std::vector<std::pair<std::weak_ptr<NativeObject>,Json>> configured;
    CHyprSignalListener reloadListener;
    NativeBackend() {
        for(auto& impl:g_pCompositor->m_aqBackend->getImplementations()) if(impl->type()==Aquamarine::AQ_BACKEND_HEADLESS) {
            if(headless) throw std::runtime_error("ambiguous headless backend");
            headless=impl;
        }
        if(!headless) throw std::runtime_error("headless backend unavailable");
        reloadListener=Config::monitorRuleMgr()->m_events.stateReloaded.listen([this] {
            // Deferred reload is not permission to reapply. Latch any mismatch.
            for(auto& [weak,spec]:configured) if(auto o=weak.lock(); o && o->live) {
                try { if(!verified(o,spec)) o->ruleLost=true; }
                catch(...) { o->ruleLost=true; }
            }
        });
    }
    Handle wrap(SP<Aquamarine::IOutput> output,bool physical=false) {
        auto o=std::make_shared<NativeObject>(); o->output=output; o->physical=physical;
        std::weak_ptr<NativeObject> weak=o;
        o->destroyListener=output->events.destroy.listen([weak] {
            if(auto o=weak.lock()) {
                if(!o->live) return;
                o->live=false;
                try { eraseRule(*o); } catch(...) { o->ruleLost=true; }
            }
        });
        return o;
    }
    PHLMONITOR resolve(const Handle& h) {
        if(!h || !h->live) return nullptr;
        auto o=native(h);
        for(auto& m:State::monitorState()->allMonitors()) if(m->m_output==o->output) {
            if(o->monitor && o->monitor!=m) return nullptr;
            o->monitor=m; return m;
        }
        return nullptr;
    }
    bool collision(const Json& p) override {
        for(auto& m:State::monitorState()->allMonitors()) if(m->m_name==p["name"].get<std::string>()) return true;
        for(auto& rule:Config::monitorRuleMgr()->all()) if(rule.m_name==p["name"].get<std::string>()) return true;
        for(auto& w:State::workspaceState()->workspacesCopy()) if(w->m_id==p["workspace"] || w->m_name==p["workspace_name"].get<std::string>()) return true;
        return false;
    }
    bool create(const Json& p,const Capture& capture) override {
        auto listener=g_pCompositor->m_aqBackend->events.newOutput.listen([&](const SP<Aquamarine::IOutput>& output) {
            // Bind at emission, before createOutput returns. No name-based adoption.
            try { capture(wrap(output), output->getBackend()==headless && output->name==p["name"].get<std::string>()); }
            catch(...) { capture({},false); }
        });
        return headless->createOutput(p["name"].get<std::string>());
    }
    bool configure(const Handle& h,const Json& p) override {
        auto m=resolve(h); if(!m) return false; auto o=native(h);
        // Recheck workspace/rule collisions after synchronous creation callbacks.
        for(auto& w:State::workspaceState()->workspacesCopy()) if(w->m_id==p["workspace"] || w->m_name==p["workspace_name"].get<std::string>()) return false;
        for(auto& rule:Config::monitorRuleMgr()->all()) if(rule.m_name==p["name"].get<std::string>()) return false;
        Config::CMonitorRule rule;
        rule.m_name=p["name"]; rule.m_resolution={1920,1080}; rule.m_refreshRate=120;
        rule.m_scale=1; rule.m_offset={p["x"].get<double>(),p["y"].get<double>()};
        rule.m_transform=WL_OUTPUT_TRANSFORM_NORMAL; rule.m_disabled=false; rule.m_mirrorOf="";
        o->rule=rule; Config::monitorRuleMgr()->add(Config::CMonitorRule(rule));
        if(!h->live || !m->applyMonitorRule(std::move(rule)) || !resolve(h)) return false;
        o->workspace=State::workspaceState()->create(p["workspace"].get<int64_t>(),m->m_id,p["workspace_name"],true);
        if(!o->workspace || !resolve(h)) return false;
        m->changeWorkspace(o->workspace,true,true,true);
        configured.emplace_back(o,p);
        return h->live;
    }
    bool verified(const Handle& h,const Json& p) override {
        auto m=resolve(h); if(!m) return false; auto o=native(h);
        if(o->ruleLost || !o->rule) return false;
        unsigned matched=0;
        for(auto& rule:Config::monitorRuleMgr()->all()) if(rule.m_name==o->rule->m_name) {
            if(rule.compare(*o->rule)!=Config::COMPARISON_FULL_MATCH) { o->ruleLost=true; return false; } ++matched;
        }
        if(matched!=1) { o->ruleLost=true; return false; }
        auto w=o->workspace;
        const auto workspaces=State::workspaceState()->workspacesCopy();
        if(std::find(workspaces.begin(),workspaces.end(),w)==workspaces.end()) return false;
        return m->m_currentMode && m->m_currentMode->pixelSize==Vector2D(1920,1080) && m->m_currentMode->refreshRate==120000 &&
            m->enabled() && m->m_dpmsStatus && !m->m_mirrorOf && m->m_pixelSize==Vector2D(1920,1080) &&
            m->m_scale==1 && m->m_refreshRate==120 && m->m_position==Vector2D(p["x"].get<double>(),p["y"].get<double>()) &&
            m->m_transform==WL_OUTPUT_TRANSFORM_NORMAL && w && !w->inert() && w->m_id==p["workspace"] &&
            w->m_name==p["workspace_name"].get<std::string>() && w->m_monitor==m && m->m_activeWorkspace==w;
    }
    bool usable(const Handle& h) override {
        auto m=resolve(h);
        return m && h->physical && m->m_output->getBackend()->type()==Aquamarine::AQ_BACKEND_DRM && m->enabled() && m->m_dpmsStatus && !m->m_mirrorOf;
    }
    bool evacuate(const Handle& h,const Handle& fallback) override {
        auto source=resolve(h), target=resolve(fallback); if(!source || !usable(fallback)) return false;
        auto workspaces=State::workspaceState()->workspacesCopy();
        for(auto& w:workspaces) if(w->m_monitor==source && (w->inert() || w->m_isSpecialWorkspace || w->m_id<=0)) return false;
        for(auto& w:Desktop::windowState()->windows()) if(w->m_isMapped && (w->m_monitor==source || (w->m_workspace && w->m_workspace->m_monitor==source)))
            if(w->m_pinned || !w->m_workspace || w->m_workspace->m_isSpecialWorkspace) return false;
        for(auto& w:workspaces) if(w->m_monitor==source) {
            if(!resolve(h) || !usable(fallback) || w->inert()) return false;
            State::workspacePlacementController()->moveWorkspaceToMonitor(w,target,true);
            if(!resolve(h) || !usable(fallback) || w->inert() || w->m_monitor!=target) return false;
        }
        return true;
    }
    bool reachable(const Handle& h,const Handle& fallback) override {
        if(!usable(fallback)) return false;
        auto source=native(h)->monitor;
        if(!h->live) for(const auto& m:State::monitorState()->allMonitors())
            if(m->m_output==native(h)->output) return false;
        const auto workspaces=State::workspaceState()->workspacesCopy();
        const auto& monitors=State::monitorState()->monitors();
        for(auto& w:workspaces) if(w->m_monitor==source) return false;
        for(auto& w:Desktop::windowState()->windows()) if(w->m_isMapped) {
            if(std::find(workspaces.begin(),workspaces.end(),w->m_workspace)==workspaces.end() ||
               std::none_of(monitors.begin(),monitors.end(),[&](const auto& m){return w->m_monitor==m;})) return false;
            if(!w->m_workspace || w->m_workspace->inert() || !w->m_monitor || w->m_monitor==source ||
               w->m_workspace->m_monitor!=w->m_monitor || !w->m_monitor->enabled() || !w->m_monitor->m_dpmsStatus || w->m_monitor->m_mirrorOf) return false;
        }
        return true;
    }
    bool cleanup(const Handle& h,const Json&) override { return resolve(h) && eraseRule(*native(h)); }
    bool destroy(const Handle& h) override { return resolve(h) && native(h)->output->destroy(); }
    Json observation(const Handle& h) {
        auto m=resolve(h); if(!m) return nullptr;
        Json workspaces=Json::array(), windows=Json::array();
        for(auto& w:State::workspaceState()->workspacesCopy()) if(w->m_monitor==m) workspaces.push_back({{"id",w->m_id},{"name",w->m_name},{"special",w->m_isSpecialWorkspace}});
        for(auto& w:Desktop::windowState()->windows()) if(w->m_isMapped && w->m_monitor==m)
            windows.push_back({{"workspace",w->m_workspace ? w->m_workspace->m_id : -1},{"pinned",w->m_pinned}});
        return {{"width",m->m_pixelSize.x},{"height",m->m_pixelSize.y},{"refresh_millihz",m->m_currentMode ? m->m_currentMode->refreshRate : 0},
            {"scale",m->m_scale},{"x",m->m_position.x},{"y",m->m_position.y},{"enabled",m->enabled()},
            {"dpms",m->m_dpmsStatus},{"mirrored",bool(m->m_mirrorOf)},{"transform",int(m->m_transform)},
            {"workspace",m->m_activeWorkspace ? m->m_activeWorkspace->m_id : -1},{"workspaces",workspaces},{"windows",windows}};
    }
};
struct Run { std::string session,name,token; Handle fallback; };
struct Broker {
    NativeBackend backend; Registry registry{backend}; std::string epoch=randomToken();
    std::thread::id thread=std::this_thread::get_id(); bool busy=false;
    std::map<std::string,Run> runs;
    std::string handle(std::string wire) {
        if(busy || std::this_thread::get_id()!=thread) return "{\"error\":\"thread/reentrancy\"}";
        busy=true; struct Reset { bool& b; ~Reset(){b=false;} } reset{busy};
        try {
            if(State::monitorState()->allMonitors().size()>128 || State::workspaceState()->workspaceRefs().size()>4096 ||
               Desktop::windowState()->windows().size()>4096) throw std::runtime_error("desktop inspection capacity");
            auto j=parse_request(wire); auto op=j["op"].get<std::string>(); auto& p=j["payload"];
            auto run=j["run_id"].get<std::string>(), session=j["session"].get<std::string>();
            Json result;
            if(op=="hello") {
                if(j["epoch"]!="unknown" && j["epoch"]!=epoch) throw std::runtime_error("epoch lost");
                if(!runs.contains(run)) {
                    if(runs.size()>=32) throw std::runtime_error("run capacity");
                    Handle fallback;
                    for(auto& m:State::monitorState()->allMonitors()) if(m->m_name==p["fallback_name"].get<std::string>()) {
                        if(fallback) throw std::runtime_error("ambiguous fallback");
                        fallback=backend.wrap(m->m_output,true);
                    }
                    if(!backend.usable(fallback)) throw std::runtime_error("unusable fallback");
                    runs.emplace(run,Run{session,p["fallback_name"],randomToken(),fallback});
                }
                auto& r=runs.at(run);
                if(r.session!=session || r.name!=p["fallback_name"].get<std::string>() || !backend.usable(r.fallback)) throw std::runtime_error("run/fallback changed");
                result={{"fallback",r.token},{"qualified",true}};
            } else {
                if(j["epoch"]!=epoch || !runs.contains(run) || runs.at(run).session!=session) throw std::runtime_error("epoch/run/session lost");
                auto& r=runs.at(run); auto id=p["id"].get<std::string>(); std::string state;
                if(op=="create") {
                    if(!backend.usable(r.fallback)) throw std::runtime_error("fallback unavailable before creation");
                    state=registry.create(session,run,p);
                }
                else if(op=="inspect") state=registry.inspect(session,run,id);
                else {
                    if(p["fallback"]!=r.token || registry.entry(id).spec["name"]!=p["name"]) throw std::runtime_error("handle mismatch");
                    state=registry.remove(session,run,id,r.fallback);
                }
                auto& e=registry.entry(id);
                result={{"id",id},{"name",e.spec["name"]},{"state",state},{"physical",false},
                    {"observation",e.object ? backend.observation(e.object):Json(nullptr)},
                    {"fallback_usable",backend.usable(r.fallback)}};
            }
            auto reply=Json{{"schema",1},{"session",session},{"run_id",run},{"request_id",j["request_id"]},{"epoch",epoch},{"result",result}}.dump();
            if(reply.size()>1048576) throw std::runtime_error("response limit");
            return reply;
        } catch(const std::exception&) { return "{\"error\":\"ownership blocked\"}"; }
        catch(...) { return "{\"error\":\"ownership failure\"}"; }
    }
};
std::unique_ptr<Broker> broker;
SP<SHyprCtlCommand> command;
HANDLE pluginHandle;
}
APICALL EXPORT std::string PLUGIN_API_VERSION() { return HYPRLAND_API_VERSION; }
APICALL EXPORT PLUGIN_DESCRIPTION_INFO PLUGIN_INIT(HANDLE handle) {
    if(std::string(__hyprland_api_get_hash())!=__hyprland_api_get_client_hash() ||
       std::string(GIT_COMMIT_HASH)!="efb50993780079460b0cbed1363e2166a2de1d9f") throw std::runtime_error("XR broker exact ABI/hash mismatch");
    pluginHandle=handle;
    try {
        broker=std::make_unique<Broker>();
        command=HyprlandAPI::registerHyprCtlCommand(handle,{"xrspike ",false,[](eHyprCtlOutputFormat,std::string request){return broker->handle(std::move(request));}});
        if(!command) throw std::runtime_error("broker command registration failed");
    } catch(...) {
        command.reset(); broker.reset();
        throw;
    }
    return {"xr-ownership","Explicit opt-in output lifecycle broker","Hermes Agent","1"};
}
APICALL EXPORT void PLUGIN_EXIT() {
    if(command) HyprlandAPI::unregisterHyprCtlCommand(pluginHandle,command);
    command.reset(); broker.reset(); // Detach listeners; never delete unresolved outputs on unload.
}
