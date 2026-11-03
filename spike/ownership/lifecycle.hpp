#pragma once
#include <algorithm>
#include <iterator>
#include <string>
#include <vector>
namespace xr::ownership {
// Object identity, not an ID/name, proves a newly allocated active workspace.
template<class W, class Before, class Range, class M>
bool freshDefault(const W& w, const Before& before, const Range& after, const M& source) {
    return w && source && !w->inert() && !w->m_isSpecialWorkspace && w->m_id>0 &&
        w->m_monitor==source && source->m_activeWorkspace==w &&
        std::find(before.begin(),before.end(),w)==before.end() &&
        std::count(after.begin(),after.end(),w)==1;
}
// Only the fresh, empty last-active replacement may remain until disconnect.
// Recheck on every reachability pass; never exempt any window from reachability.
template<class W, class Before, class Range, class M, class Empty>
bool retirementGap(const W& w, const Before& before, const Range& after, const M& source, bool live, Empty empty) {
    return live && freshDefault(w,before,after,source) && !w->isPersistent() &&
        w->m_wasCreatedEmpty && w->m_name==std::to_string(w->m_id) && empty(w) &&
        std::count_if(after.begin(),after.end(),[&](const auto& other){return other->m_monitor==source;})==1;
}
// Called only after retirementGap and strict mapped-window reachability pass.
// onDisconnect supports a null active workspace. Clearing only this proven empty
// gap immediately before destroy prevents moveWorkspaceToMonitor making another.
template<class M, class W, class Live, class Destroy>
bool disconnectRetiring(const M& source, const W& gap, Live live, Destroy destroy) {
    if(!source || !gap || source->m_activeWorkspace!=gap || gap->m_monitor!=source) return false;
    struct Restore {
        const M& source; const W& gap; Live& live;
        ~Restore() {
            if(live() && !source->m_activeWorkspace && gap->m_monitor==source && !gap->inert()) source->m_activeWorkspace=gap;
        }
    } restore{source,gap,live};
    source->m_activeWorkspace.reset();
    return destroy();
}
// The monitor rules a config reload must keep, decided just before it clears them (plugin.cpp hooks
// CMonitorRuleManager::clear and re-adds these right after, before the config is parsed).
// Pinned efb5099 reload(): clear every rule -> parse the config -> ensureMonitorStatus(), which modesets
// each output whose rule (the last one naming it, else the config's first catch-all "") no longer
// matches the applied one. The config never names XR-n or the glasses' output, so every reload -- and
// Omarchy's workspace layout toggle is one, since it rewrites a layout file the config requires --
// took XR-n through the catch-all (preferred mode, the user's scale) and the glasses from their session
// 1920x1200@120 to the config's mode, each and straight back: a resize of every window on the XR
// desktops, a capture constraint bounce and two physical modesets of the glasses per switch.
// Kept, while a run has live XR outputs (`owned`: their rules, never empty then): our rules, and for
// every other output the named rule in effect (`applied`: compare() is a full match, so the reload
// would otherwise modeset). Re-added before the parse, so a rule of the same name in the config still
// replaces it (add() erases by name): the config wins wherever it names an output. Without a run,
// nothing: a reload is entirely the user's.
template<class R, class Rules, class Monitors, class Matches, class Applied>
std::vector<R> keptThroughReload(const std::vector<R>& owned, const Rules& rules, const Monitors& monitors,
                                 Matches matches, Applied applied) {
    std::vector<R> kept;
    if(owned.empty()) return kept;
    auto named=[&](const auto& name) {
        return std::any_of(kept.begin(),kept.end(),[&](const R& k){return k.m_name==name;});
    };
    for(const auto& rule:owned) if(!named(rule.m_name)) kept.push_back(rule);
    for(const auto& monitor:monitors) {
        // The rule ensureMonitorStatus would apply: the last that names this output.
        auto rule=std::find_if(std::rbegin(rules),std::rend(rules),[&](const R& r){return matches(monitor,r.m_name);});
        if(rule==std::rend(rules) || rule->m_name.empty() || named(rule->m_name) || !applied(monitor,*rule)) continue;
        kept.push_back(*rule);
    }
    return kept;
}
// Explicit pinned CMonitorRule value equality: compare() is modeset policy,
// not ownership equality. Do not compare struct padding in drmModeModeInfo.
template<class R> bool sameRule(const R& a,const R& b) {
    return a.m_autoDir==b.m_autoDir &&
        a.m_name==b.m_name &&
        a.m_resolution==b.m_resolution &&
        a.m_offset==b.m_offset &&
        a.m_scale==b.m_scale &&
        a.m_refreshRate==b.m_refreshRate &&
        a.m_disabled==b.m_disabled &&
        a.m_transform==b.m_transform &&
        a.m_mirrorOf==b.m_mirrorOf &&
        a.m_enable10bit==b.m_enable10bit &&
        a.m_cmType==b.m_cmType &&
        a.m_sdrEotf==b.m_sdrEotf &&
        a.m_sdrSaturation==b.m_sdrSaturation &&
        a.m_sdrBrightness==b.m_sdrBrightness &&
        a.m_reservedArea==b.m_reservedArea &&
        a.m_iccFile==b.m_iccFile &&
        a.m_supportsWideColor==b.m_supportsWideColor &&
        a.m_supportsHDR==b.m_supportsHDR &&
        a.m_sdrMinLuminance==b.m_sdrMinLuminance &&
        a.m_sdrMaxLuminance==b.m_sdrMaxLuminance &&
        a.m_minLuminance==b.m_minLuminance &&
        a.m_maxLuminance==b.m_maxLuminance &&
        a.m_maxAvgLuminance==b.m_maxAvgLuminance &&
        a.m_vrr==b.m_vrr &&
        a.m_drmMode.clock==b.m_drmMode.clock &&
        a.m_drmMode.hdisplay==b.m_drmMode.hdisplay && a.m_drmMode.hsync_start==b.m_drmMode.hsync_start &&
        a.m_drmMode.hsync_end==b.m_drmMode.hsync_end && a.m_drmMode.htotal==b.m_drmMode.htotal &&
        a.m_drmMode.hskew==b.m_drmMode.hskew && a.m_drmMode.vdisplay==b.m_drmMode.vdisplay &&
        a.m_drmMode.vsync_start==b.m_drmMode.vsync_start && a.m_drmMode.vsync_end==b.m_drmMode.vsync_end &&
        a.m_drmMode.vtotal==b.m_drmMode.vtotal && a.m_drmMode.vscan==b.m_drmMode.vscan &&
        a.m_drmMode.vrefresh==b.m_drmMode.vrefresh && a.m_drmMode.flags==b.m_drmMode.flags &&
        a.m_drmMode.type==b.m_drmMode.type &&
        std::equal(std::begin(a.m_drmMode.name),std::end(a.m_drmMode.name),std::begin(b.m_drmMode.name));
}
}
