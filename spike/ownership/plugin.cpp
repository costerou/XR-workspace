// Opt-in output lifecycle module. It also reads, and never changes, the name of the cursor the app under the pointer asked
// for: the cursor_shape op, a plain read. No function hook on any cursor or input call, ever: two such hooks took Hyprland down
// on 2026-11-03 (docs/technical.md "No cursor hooks"), and tests/native_spike/test_no_cursor_hooks.py fails if one comes back.
// No other renderer access; no capture, input or config-file access.
#include <hyprland/src/plugins/PluginAPI.hpp>
#include <hyprland/src/Compositor.hpp>
#include <hyprland/src/debug/HyprCtl.hpp>
#include <hyprland/src/state/MonitorState.hpp>
#include <hyprland/src/state/MonitorLayoutController.hpp>
#include <hyprland/src/state/WorkspaceState.hpp>
#include <hyprland/src/state/WorkspacePlacementController.hpp>
#include <hyprland/src/output/Monitor.hpp>
#include <hyprland/src/desktop/Workspace.hpp>
#include <hyprland/src/desktop/state/WindowState.hpp>
#include <hyprland/src/config/shared/monitor/MonitorRuleManager.hpp>
#include <hyprland/src/pointer/PointerManager.hpp>
#include <hyprland/src/render/Renderer.hpp>
#include <aquamarine/backend/Headless.hpp>
#include <aquamarine/backend/DRM.hpp>
#include <sys/stat.h>
#include <sys/sysmacros.h>
#include <sys/random.h>
#include <chrono>
#include <optional>
#include <type_traits>
#include <thread>
#include "registry.hpp"
#include "lifecycle.hpp"
using namespace xr::ownership;
namespace {
std::string randomToken() {
    unsigned char bytes[24];
    if(getrandom(bytes,sizeof(bytes),GRND_NONBLOCK)!=sizeof(bytes)) throw std::runtime_error("entropy unavailable");
    std::string s; for(auto b:bytes) { s+="0123456789abcdef"[b>>4]; s+="0123456789abcdef"[b&15]; } return s;
}
struct NativeObject : Object {
    SP<Aquamarine::IOutput> output;
    // Weak: a strong reference kept destroyed CMonitors alive, turning Hyprland's harmless
    // late wl_output bind (expired monitor) into a use of a torn-down output (phys1 crash).
    PHLMONITORREF monitor;
    bool monitorBound=false;
    SP<Aquamarine::IBackendImplementation> pairBackend;
    std::optional<PairObservation> pairProof; // First DRM identity of a pair-selected object.
    bool legacyDrmEligible=false;
    // Workspaces on managed outputs belong to Omarchy/the user; only teardown state is kept.
    PHLWORKSPACE retirement;
    std::vector<PHLWORKSPACEREF> beforeEvacuation;
    CHyprSignalListener destroyListener;
    std::optional<Config::CMonitorRule> rule;
    bool ruleLost=false;
};
// Regression guard (phys1 crash): never retain a CMonitor strongly; the boundary test's own
// NativeObject double cannot detect a revert of this production field.
static_assert(std::is_same_v<decltype(NativeObject::monitor), PHLMONITORREF>, "broker must hold monitors weakly");
auto native(const Handle& h) { return std::static_pointer_cast<NativeObject>(h); }
bool eraseRule(NativeObject& o) {
    if(!o.rule) return true;
    auto& rules=Config::monitorRuleMgr()->allMut();
    auto found=rules.end();
    for(auto it=rules.begin();it!=rules.end();++it) if(it->m_name==o.rule->m_name) {
        if(found!=rules.end() || !sameRule(*it,*o.rule)) { o.ruleLost=true; return false; }
        found=it;
    }
    // Already gone (e.g. a config reload dropped it): nothing of ours left to erase.
    if(found==rules.end()) { o.rule.reset(); return true; }
    rules.erase(found); o.rule.reset(); return true;
}
// A config reload (`hyprctl reload`, Omarchy theme switches) clears every monitor rule and
// re-parses the user's config, which never names XR-n. Put back exactly our rule if, and only
// if, no rule of that name exists now; a same-name rule from the user's config wins (latched).
bool restoreRule(NativeObject& o) {
    if(!o.rule || o.ruleLost) return false;
    for(const auto& rule:Config::monitorRuleMgr()->all()) if(rule.m_name==o.rule->m_name) return false;
    Config::monitorRuleMgr()->add(Config::CMonitorRule(*o.rule));
    return true;
}
class NativeBackend final : public Backend, public PairBackend {
public:
    SP<Aquamarine::IBackendImplementation> headless;
    std::vector<std::pair<std::weak_ptr<NativeObject>,Json>> configured;
    CHyprSignalListener reloadListener;
    bool reapplying=false;
    unsigned restored=0;  // rules put back after config reloads (diagnostic)
    NativeBackend() {
        for(auto& impl:g_pCompositor->m_aqBackend->getImplementations()) if(impl->type()==Aquamarine::AQ_BACKEND_HEADLESS) {
            if(headless) throw std::runtime_error("ambiguous headless backend");
            headless=impl;
        }
        if(!headless) throw std::runtime_error("headless backend unavailable");
        // Pinned efb5099 reload(): clear rules -> parse config -> ensureMonitorStatus() (applies
        // rules, then emits stateReloaded) -> config.reloaded. So this is the first point where a
        // dropped rule can be seen; re-applying here happens inside the same synchronous reload,
        // before any frame. Anything else that does not verify is latched as before. With the clear()
        // hook (hookedClear) our rule never leaves, so this finds it present and only verifies; it
        // restores only if the hook is missing.
        reloadListener=Config::monitorRuleMgr()->m_events.stateReloaded.listen([this] {
            if(reapplying) return;
            for(auto& [weak,spec]:configured) if(auto o=weak.lock(); o && o->live) {
                try {
                    if(restoreRule(*o)) {
                        auto m=resolve(o);
                        reapplying=true;
                        const bool applied=m && m->applyMonitorRule(Config::CMonitorRule(*o->rule));
                        if(applied && resolve(o)) State::monitorLayoutController()->arrange();
                        reapplying=false;
                        ++restored;
                        if(!applied || !resolve(o)) { o->ruleLost=true; continue; }
                    }
                    if(!verified(o,spec)) o->ruleLost=true;
                }
                catch(...) { reapplying=false; o->ruleLost=true; }
            }
        });
    }
    // What a config reload keeps (lifecycle.hpp keptThroughReload): read just before the reload clears the rules.
    std::vector<Config::CMonitorRule> keptThroughReload() {
        std::vector<Config::CMonitorRule> owned;
        for(auto& [weak,spec]:configured) if(auto o=weak.lock(); o && o->live && o->rule && !o->ruleLost) owned.push_back(*o->rule);
        return xr::ownership::keptThroughReload(owned,Config::monitorRuleMgr()->all(),State::monitorState()->allMonitors(),
            // As ensureMonitorStatus: outputs with a backend output, never the unsafe fallback.
            [](const PHLMONITOR& m,const std::string& name) { return m && m->m_output && !m->m_isUnsafeFallback && m->matchesStaticSelector(name); },
            [](const PHLMONITOR& m,const Config::CMonitorRule& rule) { return rule.compare(m->m_activeMonitorRule)==Config::COMPARISON_FULL_MATCH; });
    }
    Handle wrap(SP<Aquamarine::IOutput> output,bool legacyDrmEligible=false) {
        auto o=std::make_shared<NativeObject>(); o->output=output; o->legacyDrmEligible=legacyDrmEligible;
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
            if(o->monitorBound && o->monitor!=m) return nullptr; // expired or replaced
            o->monitor=m; o->monitorBound=true; return m;
        }
        return nullptr;
    }
    Handle selectPairOutput(const std::string& name) override {
        Handle selected;
        for(auto& m:State::monitorState()->allMonitors()) if(m->m_name==name) {
            if(selected || !m->m_output) throw std::runtime_error("ambiguous/missing pair output");
            selected=wrap(m->m_output);
            native(selected)->monitor=m;
            native(selected)->monitorBound=true;
            native(selected)->pairBackend=m->m_output->getBackend();
        }
        if(!selected) throw std::runtime_error("pair output absent");
        return selected;
    }
    PairObservation observePairOutput(const Handle& h) override {
        auto m=resolve(h);
        if(!m || !m->enabled() || !m->m_dpmsStatus || m->m_mirrorOf || !m->m_mirrors.empty())
            throw std::runtime_error("pair display state");
        auto o=native(h);
        unsigned names=0, outputs=0;
        for(auto& current:State::monitorState()->allMonitors()) {
            names+=current->m_name==m->m_name;
            outputs+=current->m_output==o->output;
        }
        if(names!=1 || outputs!=1) throw std::runtime_error("ambiguous pair identity");
        auto backend=o->output->getBackend();
        if(!backend || backend!=o->pairBackend || backend->type()!=Aquamarine::AQ_BACKEND_DRM)
            throw std::runtime_error("pair DRM backend changed");
        auto* drm=dynamic_cast<Aquamarine::CDRMOutput*>(o->output.get());
        if(!drm) throw std::runtime_error("pair not CDRMOutput");
        const int connector=drm->getConnectorID(), fd=backend->drmFD();
        struct stat st{};
        if(connector<=0 || fd<0 || fstat(fd,&st)!=0 || !S_ISCHR(st.st_mode) || !major(st.st_rdev) ||
           m->m_name!=o->output->name)
            throw std::runtime_error("pair DRM identity unavailable");
        const PairObservation observed{m->m_name,static_cast<uint32_t>(major(st.st_rdev)),
            static_cast<uint32_t>(minor(st.st_rdev)),static_cast<uint32_t>(connector)};
        if(!o->pairProof) o->pairProof=observed;
        else if(*o->pairProof!=observed) throw std::runtime_error("pair DRM identity changed");
        return observed;
    }
    bool collision(const Json& p) override {
        for(auto& m:State::monitorState()->allMonitors()) if(m->m_name==p["name"].get<std::string>()) return true;
        for(auto& rule:Config::monitorRuleMgr()->all()) if(rule.m_name==p["name"].get<std::string>()) return true;
        return false;
    }
    bool create(const Json& p,const Capture& capture) override {
        std::shared_ptr<NativeObject> captured;
        auto listener=g_pCompositor->m_aqBackend->events.newOutput.listen([&](const SP<Aquamarine::IOutput>& output) {
            // Bind at emission, before createOutput returns. No name-based adoption.
            try {
                auto object=wrap(output);
                if(!captured) captured=native(object);
                capture(object, output->getBackend()==headless && output->name==p["name"].get<std::string>());
            }
            catch(...) { capture({},false); }
        });
        return headless->createOutput(p["name"].get<std::string>());
    }
    bool configure(const Handle& h,const Json& p) override {
        auto m=resolve(h); if(!m) return false; auto o=native(h);
        // Recheck rule collisions after synchronous creation callbacks. Workspaces are
        // never created, adopted, renamed or switched: Omarchy gives each monitor its block.
        for(auto& rule:Config::monitorRuleMgr()->all()) if(rule.m_name==p["name"].get<std::string>()) return false;
        Config::CMonitorRule rule;
        rule.m_name=p["name"]; rule.m_resolution={p["width"].get<double>(),p["height"].get<double>()}; rule.m_refreshRate=120;
        rule.m_scale=p["scale"].get<double>(); rule.m_offset={p["x"].get<double>(),p["y"].get<double>()};
        rule.m_transform=WL_OUTPUT_TRANSFORM_NORMAL; rule.m_disabled=false; rule.m_mirrorOf="";
        o->rule=rule; Config::monitorRuleMgr()->add(Config::CMonitorRule(rule));
        if(!h->live || !m->applyMonitorRule(std::move(rule)) || !resolve(h)) return false;
        // Pinned applyMonitorRule stores m_offset but does not move the monitor.
        // Complete the compositor's synchronous layout phase before verification;
        // a deferred reload may skip arrange when the active rule already matches.
        State::monitorLayoutController()->arrange();
        if(!resolve(h)) return false; // layout notifications may destroy the output
        configured.emplace_back(o,p);
        return h->live;
    }
    bool reshape(const Handle& h,const Json&,const Json& after) override {
        auto m=resolve(h); if(!m) return false; auto o=native(h);
        // Our rule goes, then configure() puts the new one in exactly as at creation (collision check, apply, arrange).
        if(o->ruleLost || !eraseRule(*o)) return false;
        std::erase_if(configured,[&](const auto& c) { auto held=c.first.lock(); return !held || held==o; });
        return configure(h,after);
    }
    bool verified(const Handle& h,const Json& p) override {
        auto m=resolve(h); if(!m) return false; auto o=native(h);
        if(o->ruleLost || !o->rule) return false;
        unsigned matched=0;
        for(auto& rule:Config::monitorRuleMgr()->all()) if(rule.m_name==o->rule->m_name) {
            if(!sameRule(rule,*o->rule)) { o->ruleLost=true; return false; } ++matched;
        }
        if(matched!=1) { o->ruleLost=true; return false; }
        // Output identity/geometry only: which workspace is shown is the user's choice.
        return m->m_currentMode && m->m_currentMode->pixelSize==Vector2D(p["width"].get<double>(),p["height"].get<double>()) && m->m_currentMode->refreshRate==120000 &&
            m->enabled() && m->m_dpmsStatus && !m->m_mirrorOf && m->m_pixelSize==Vector2D(p["width"].get<double>(),p["height"].get<double>()) &&
            m->m_scale==static_cast<float>(p["scale"].get<double>()) && m->m_refreshRate==120 && m->m_position==Vector2D(p["x"].get<double>(),p["y"].get<double>()) &&
            m->m_transform==WL_OUTPUT_TRANSFORM_NORMAL;
    }
    // Whether the fallback can hold windows. DPMS is no part of it: a powered-down panel keeps its workspaces and windows
    // (privacy mode, Ctrl+Alt+D, tools/native_spike/privacy.py), and recovery turns the fallback back on before it starts.
    // An isolated pair keeps its full proof, DPMS included (observePairOutput).
    bool usable(const Handle& h) override {
        auto m=resolve(h);
        if(!m || !m->m_output->getBackend() || m->m_output->getBackend()->type()!=Aquamarine::AQ_BACKEND_DRM ||
           !m->enabled() || m->m_mirrorOf) return false;
        auto o=native(h);
        // Legacy schema-1 eligibility and isolated pair proof are never combined.
        if(o->legacyDrmEligible) return !o->pairBackend;
        if(!o->pairBackend || !o->pairProof) return false;
        try { return observePairOutput(h)==*o->pairProof; } catch(...) { return false; }
    }
    bool evacuate(const Handle& h,const Handle& fallback) override {
        auto source=resolve(h), target=resolve(fallback); if(!source || !usable(fallback)) return false;
        auto workspaces=State::workspaceState()->workspacesCopy();
        auto o=native(h); o->retirement.reset(); o->beforeEvacuation.assign(workspaces.begin(),workspaces.end());
        for(auto& w:workspaces) if(w->m_monitor==source && (w->inert() || w->m_isSpecialWorkspace || w->m_id<=0)) return false;
        for(auto& w:Desktop::windowState()->windows()) if(w->m_isMapped && (w->m_monitor==source || (w->m_workspace && w->m_workspace->m_monitor==source)))
            if(w->m_pinned || !w->m_workspace || w->m_workspace->m_isSpecialWorkspace) return false;
        for(auto& w:workspaces) if(w->m_monitor==source) {
            if(!resolve(h) || !usable(fallback) || w->inert()) return false;
            State::workspacePlacementController()->moveWorkspaceToMonitor(w,target,true);
            if(!resolve(h) || !usable(fallback) || w->inert() || w->m_monitor!=target) return false;
        }
        const auto after=State::workspaceState()->workspacesCopy();
        auto gap=source->m_activeWorkspace;
        if(retirementGap(gap,workspaces,after,source,h->live,[&](const auto& w){return empty(w);})) o->retirement=gap;
        return true;
    }
    bool empty(const PHLWORKSPACE& workspace) {
        // Include unmapped windows: a later map must not revive a retired dependency.
        return std::none_of(Desktop::windowState()->windows().begin(),Desktop::windowState()->windows().end(),
            [&](const auto& window){return window->m_workspace==workspace;});
    }
    bool reachable(const Handle& h,const Handle& fallback) override {
        if(!usable(fallback)) return false;
        auto source=native(h)->monitor;
        const auto asleepOk=resolve(fallback); // the only monitor whose windows may be DPMS-off (usable() above)
        if(!h->live) for(const auto& m:State::monitorState()->allMonitors())
            if(m->m_output==native(h)->output) return false;
        const auto workspaces=State::workspaceState()->workspacesCopy();
        const auto& monitors=State::monitorState()->monitors();
        auto o=native(h);
        for(auto& w:workspaces) if(w->m_monitor==source) {
            if(w!=o->retirement || !retirementGap(w,o->beforeEvacuation,workspaces,source,h->live,
                [&](const auto& gap){return empty(gap);})) return false;
        }
        for(auto& w:Desktop::windowState()->windows()) if(w->m_isMapped) {
            if(std::find(workspaces.begin(),workspaces.end(),w->m_workspace)==workspaces.end() ||
               std::none_of(monitors.begin(),monitors.end(),[&](const auto& m){return w->m_monitor==m;})) return false;
            if(!w->m_workspace || w->m_workspace->inert() || !w->m_monitor || w->m_monitor==source ||
               w->m_workspace->m_monitor!=w->m_monitor || !w->m_monitor->enabled() ||
               (!w->m_monitor->m_dpmsStatus && w->m_monitor!=asleepOk) || w->m_monitor->m_mirrorOf) return false;
        }
        return true;
    }
    // The cursor the app under the pointer asked for, by name, while the pointer is on one of `outputs` (the run's own live
    // XR outputs); nothing elsewhere, or for a cursor given as a picture, which has no name. Only reads, and only a string:
    // a cursor surface is never touched, so a null or destroyed one (wl_pointer.set_cursor) cannot reach here. Pinned efb5099:
    // setCursorFromName and setCursorSurface keep m_lastCursorData.name current before they look at m_cursorHidden, so it
    // holds while cursor.invisible hides the cursor; m_cursorSurfaceInfo.name goes stale over borders and empty desktop.
    std::string cursorShape(const std::vector<Handle>& outputs) {
        const auto at=Pointer::mgr()->position();
        for(const auto& h:outputs) if(auto m=resolve(h); m && m->logicalBox().containsPoint(at))
            return cursorShapeName(g_pHyprRenderer->m_lastCursorData.name);
        return {};
    }
    bool cleanup(const Handle& h,const Json&) override {
        if(!resolve(h)) return false;
        auto o=native(h);
        if(o->rule) eraseOwnRule(Config::monitorRuleMgr()->allMut(),*o->rule,[](const auto& a,const auto& b){return sameRule(a,b);});
        o->rule.reset();
        return true;
    }
    void release(const Handle& h) override {
        // Strong PHLWORKSPACE members kept evacuated managed workspaces and retirement
        // gaps listed (empty) for the compositor lifetime after a complete recovery.
        // Deliberately keep `output`: reachable() uses it to prove no monitor still holds that
        // output, and retaining it prevents address reuse from faking that identity.
        auto o=native(h);
        o->retirement.reset(); o->beforeEvacuation.clear();
    }
    bool destroy(const Handle& h) override {
        auto m=resolve(h); if(!m) return false;
        auto o=native(h);
        if(!o->retirement) return o->output->destroy();
        if(!retirementGap(o->retirement,o->beforeEvacuation,State::workspaceState()->workspacesCopy(),m,h->live,
            [&](const auto& gap){return empty(gap);})) return false;
        return disconnectRetiring(m,o->retirement,[&]{return h->live;},[&]{return o->output->destroy();});
    }
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
struct Run { std::string session,name,token; Handle fallback; bool isolated=false; };
struct Broker {
    NativeBackend backend; Registry registry{backend}; std::string epoch=randomToken();
    std::thread::id thread=std::this_thread::get_id(); bool busy=false;
    std::map<std::string,Run> runs;
    PairRegistry pairs{backend,randomToken};
    std::string handle(std::string wire) {
        if(busy || std::this_thread::get_id()!=thread) return "{\"error\":\"thread/reentrancy\"}";
        busy=true; struct Reset { bool& b; ~Reset(){b=false;} } reset{busy};
        try {
            if(State::monitorState()->allMonitors().size()>128 || State::workspaceState()->workspaceRefs().size()>4096 ||
               Desktop::windowState()->windows().size()>4096) throw std::runtime_error("desktop inspection capacity");
            auto j=parse_request(wire); auto op=j["op"].get<std::string>(); auto& p=j["payload"];
            auto run=j["run_id"].get<std::string>(), session=j["session"].get<std::string>();
            Json result;
            const bool isolated=j["schema"]==2;
            if(op=="pair_hello" || op=="pair_observe") {
                if(runs.contains(run) && !runs.at(run).isolated) throw std::runtime_error("cross-mode run");
                result=pairs.handle(j,epoch);
                if(op=="pair_hello" && !runs.contains(run))
                    runs.emplace(run,Run{session,p["fallback_name"],result["fallback"]["handle"],pairs.fallback(run),true});
            } else {
            // Pair runs never accept a name-based schema-1 hello (no fallback adoption).
            if(op=="hello" && pairs.contains(run)) throw std::runtime_error("cross-mode run");
            if(op=="hello") {
                if(j["epoch"]!="unknown" && j["epoch"]!=epoch) throw std::runtime_error("epoch lost");
                if(!runs.contains(run)) {
                    // Isolated pair runs have their own capacity in PairRegistry.
                    if(std::count_if(runs.begin(),runs.end(),[](const auto& r){return !r.second.isolated;})>=32)
                        throw std::runtime_error("run capacity");
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
            } else if(op=="cursor_shape") {
                // Read-only (with no event coming the sampler asks up to 120 times a second in the overview): no lifecycle
                // step, no state kept.
                if(j["epoch"]!=epoch || !runs.contains(run) || runs.at(run).session!=session || runs.at(run).isolated)
                    throw std::runtime_error("epoch/run/session lost");
                result={{"shape",backend.cursorShape(registry.liveOutputs(session,run))}};
            } else {
                if(j["epoch"]!=epoch || !runs.contains(run) || runs.at(run).session!=session) throw std::runtime_error("epoch/run/session lost");
                auto& r=runs.at(run); auto id=p["id"].get<std::string>(); std::string state;
                if(r.isolated!=isolated) throw std::runtime_error("cross-mode run");
                if(op=="create") {
                    if(isolated) pairs.revalidate(run,session); // full target+fallback proof before mutation
                    if(!backend.usable(r.fallback)) throw std::runtime_error("fallback unavailable before creation");
                    state=registry.create(session,run,p);
                }
                else if(op=="reshape") {
                    if(isolated) pairs.revalidate(run,session); // full target+fallback proof before mutation
                    if(!backend.usable(r.fallback)) throw std::runtime_error("fallback unavailable before reshape");
                    state=registry.reshape(session,run,p);
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
                if(state=="blocked") result["blocked"]=e.blocked;
            }
            }
            auto reply=Json{{"schema",j["schema"]},{"session",session},{"run_id",run},{"request_id",j["request_id"]},{"epoch",epoch},{"result",result}}.dump();
            if(reply.size()>1048576) throw std::runtime_error("response limit");
            return reply;
        } catch(const std::exception&) { return "{\"error\":\"ownership blocked\"}"; }
        catch(...) { return "{\"error\":\"ownership failure\"}"; }
    }
};
std::unique_ptr<Broker> broker;
SP<SHyprCtlCommand> command;
HANDLE pluginHandle;
// Config reloads keep the run's monitor rules (keptThroughReload): the pinned reload() calls clear()
// (a real call, not inlined) after the syntax check and before parsing; the rules go back right after it.
CFunctionHook* clearHook=nullptr;
void hookedClear(Config::CMonitorRuleManager* rules) {
    std::vector<Config::CMonitorRule> kept;
    if(broker && rules==Config::monitorRuleMgr().get()) {
        try { kept=broker->backend.keptThroughReload(); } catch(...) { kept.clear(); }
    }
    reinterpret_cast<void(*)(Config::CMonitorRuleManager*)>(clearHook->m_original)(rules);
    for(auto& rule:kept) rules->add(std::move(rule));
}
CFunctionHook* hookSymbol(HANDLE handle,const std::string& symbol,const void* replacement) {
    for(const auto& match:HyprlandAPI::findFunctionsByName(handle,symbol))
        if(match.signature==symbol) {
            auto* hook=HyprlandAPI::createFunctionHook(handle,match.address,replacement);
            if(hook && hook->hook()) return hook;
            if(hook) HyprlandAPI::removeFunctionHook(handle,hook);
            return nullptr;
        }
    return nullptr;
}
void unhook(HANDLE handle,CFunctionHook*& hook) {
    if(hook) { HyprlandAPI::removeFunctionHook(handle,hook); hook=nullptr; }
}
void hookClear(HANDLE handle) {
    // Config::CMonitorRuleManager::clear(). Without the hook a reload still recovers (the stateReloaded listener), only not
    // seamlessly.
    clearHook=hookSymbol(handle,"_ZN6Config19CMonitorRuleManager5clearEv",reinterpret_cast<const void*>(&hookedClear));
}
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
        hookClear(handle);
    } catch(...) {
        unhook(handle,clearHook);
        command.reset(); broker.reset();
        throw;
    }
    return {"xr-ownership","Explicit opt-in output lifecycle broker","Hermes Agent","1"};
}
APICALL EXPORT void PLUGIN_EXIT() {
    // Unhooked first: the reload that follows an unload is the user's own again.
    unhook(pluginHandle,clearHook);
    if(command) HyprlandAPI::unregisterHyprCtlCommand(pluginHandle,command);
    command.reset(); broker.reset(); // Detach listeners; never delete unresolved outputs on unload.
}
