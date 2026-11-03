"""Session start: the physical monitor's windows move onto the XR desktops, once, by workspace.

A capture run (a panel session) takes every window on the run's physical fallback monitor (the output Stop and Recover
return windows to) onto its XR desktops, right after the desktops are created and before the renderer starts:

  * Workspace by workspace. The occupied regular workspaces of the fallback, in id order, go to XR-1, XR-2, ... in
    turn: the first occupied one to XR-1, the second to XR-2, and so on (workspace 1 -> XR-1, 2 -> XR-2 when they are
    1, 2, ...). A gap in the numbers does not skip a desktop, so nothing waits on the last desktop while one is empty.
  * More occupied workspaces than desktops: the run starts with more desktops (desktop_count), up to MAX_DESKTOPS, each
    the size of the last one (16:9 if that one is ultrawide, as a pill's Add). Past MAX_DESKTOPS the remaining
    workspaces go to the last desktop, still each its own workspace there.
  * A workspace moves whole (Hyprland's workspace move), so its windows stay together in their layout. Omarchy gives
    each monitor its own block of ten workspace numbers (default/hypr/monitor-workspaces.lua); a moved workspace joins
    its desktop's block under the same slot, as Omarchy's own move-workspace-to-monitor does, so Omarchy's reconcile
    (a pill's Add, a theme switch) leaves it where it was put instead of sending it back to the physical monitor.
  * What stays: special (scratchpad) workspaces, pinned windows and the VITURE Workspace panel itself (a window of the
    Omarchy shell, `org.quickshell` titled "VITURE Workspace"). A workspace that holds one of those stays on the
    physical monitor with it; its other windows move one by one, in layout order (top to bottom, left to right), onto
    the workspace their desktop shows. Recovery refuses to evacuate an XR output with a pinned window or a special
    workspace on it, so neither is ever put there.
  * One Hyprland eval (generated Lua, fixed shape, every value validated) makes all the moves, in one compositor tick:
    no intermediate layout is drawn. A plan too large for one eval is split; each part is still one tick.

Stop and Recover are unchanged: recovery evacuates every workspace of every XR output back to the fallback (the
ownership broker's evacuate_remove), which brings the adopted workspaces, and the windows moved one by one, back to the
physical monitor. Adoption is best-effort: if it fails, the run goes on with the windows where they were.
"""
import re

from .geometry import MAX_DESKTOPS, is_ultrawide, toggle_ultrawide, validate_desktop

PANEL_CLASS = 'org.quickshell'
PANEL_TITLE = 'VITURE Workspace'   # packaging/viture-workspace.desktop StartupWMClass / Panel.qml title
ADDRESS = re.compile(r'0x[0-9a-f]{1,16}\Z')
OUTPUT = re.compile(r'XR-[1-9]\Z')
EVAL_LIMIT = 4096                  # hyprland.HyprlandClient._eval


def _regular(workspace):
    return (isinstance(workspace, dict) and type(workspace.get('id')) is int and 0 < workspace['id'] <= 2147483647
            and isinstance(workspace.get('name'), str) and not workspace['name'].startswith('special'))


def stays(window):
    """A window that never leaves the physical monitor: pinned, or the VITURE Workspace panel."""
    return window.get('pinned') is not False or (window.get('class') == PANEL_CLASS and window.get('title') == PANEL_TITLE)


def occupied(workspaces, clients, monitor):
    """The fallback's workspaces with windows to move, in id order: [(id, whole, [addresses in layout order])].
    `whole`: the workspace moves as one (nothing on it stays); otherwise only the listed windows move."""
    if not isinstance(workspaces, list) or not isinstance(clients, list): raise ValueError('invalid snapshot')
    ids = sorted({w['id'] for w in workspaces if _regular(w) and w.get('monitor') == monitor})
    out = []
    for wid in ids:
        on = [c for c in clients if isinstance(c, dict) and isinstance(c.get('workspace'), dict)
              and c['workspace'].get('id') == wid]
        if any(not isinstance(c.get('address'), str) or not ADDRESS.match(c['address']) for c in on):
            raise ValueError('invalid window address')
        movable = [c for c in on if not stays(c)]
        if not movable: continue
        def order(c):
            at = c.get('at')
            return (at[1], at[0]) if isinstance(at, list) and len(at) == 2 and all(type(v) is int for v in at) else (0, 0)
        out.append((wid, len(movable) == len(on), [c['address'] for c in sorted(movable, key=order)]))
    return out


def desktop_count(current, wanted):
    """How many desktops a run starts with: its own, or more for `wanted` occupied workspaces, up to MAX_DESKTOPS."""
    return max(current, min(wanted, MAX_DESKTOPS))


def extend_desktops(desktops, count):
    """`desktops` plus copies of the last one (16:9 if it is ultrawide, like a pill's Add) up to `count`."""
    desktops = [validate_desktop(dict(d)) for d in desktops]
    if not desktops: raise ValueError('at least one desktop required')
    model = desktops[-1]
    extra = toggle_ultrawide(model) if is_ultrawide(model) else model
    return desktops + [dict(extra) for _ in range(count - len(desktops))]


def plan(workspaces, clients, monitor, names):
    """The moves: {'workspaces': [(id, output)], 'windows': [(address, output)]}. The k-th occupied workspace goes to the
    k-th desktop of `names` (in name order); the ones past the last desktop go to the last."""
    names = sorted(names, key=lambda n: int(n.split('-', 1)[1]))
    if not names or any(not isinstance(n, str) or not OUTPUT.match(n) for n in names): raise ValueError('invalid outputs')
    moves = {'workspaces': [], 'windows': []}
    for k, (wid, whole, addresses) in enumerate(occupied(workspaces, clients, monitor)):
        target = names[min(k, len(names) - 1)]
        if whole: moves['workspaces'].append((wid, target))
        else: moves['windows'].extend((a, target) for a in addresses)
    return moves


# Workspace move, then (Omarchy only) renumbering into the desktop's block: the slot it had when free; if an empty
# workspace holds it (the number the desktop started on, which goes away once not shown) that one steps aside; otherwise
# the first free slot. Each step in pcall: one that fails leaves the others to run, and the eval still answers ok.
_LUA_HEAD = (
    'local ow=type(o)=="table" and type(o.workspaces)=="table" and o.workspaces or nil '
    'local function free(b) for s=10,1,-1 do if not hl.get_workspace(b+s) then return b+s end end end '
    'local function home(id,name) '
    'if not ow or type(ow.workspace_id)~="function" then return end '
    'local m=hl.get_monitor(name) local w=hl.get_workspace(id) '
    'if not m or not w or not w.monitor or w.monitor.name~=name then return end '
    'local b=ow.workspace_id(m,1)-1 if id>b and id<=b+10 then return end '
    'local want=b+(id-1)%10+1 local x=hl.get_workspace(want) '
    'if x and (x.windows or 0)==0 and not (x.monitor and x.monitor.active_workspace and x.monitor.active_workspace.id==want) then '
    'local f=free(b) if f then hl.dispatch(hl.dsp.workspace.change_id({workspace=tostring(want),id=f})) end x=hl.get_workspace(want) end '
    'local n=(not x) and want or free(b) '
    'if n then hl.dispatch(hl.dsp.workspace.change_id({workspace=tostring(id),id=n})) end end ')
_LUA_WINDOWS = (
    'for _,e in ipairs(C) do pcall(function() local m=hl.get_monitor(e[2]) local a=m and m.active_workspace '
    'if a and a.id>0 then hl.dispatch(hl.dsp.window.move({workspace=tostring(a.id),follow=false,window="address:"..e[1]})) end end) end ')
_LUA_WORKSPACES = (
    'for _,e in ipairs(W) do pcall(function() hl.dispatch(hl.dsp.workspace.move({workspace=tostring(e[1]),monitor=e[2]})) '
    'local w=hl.get_workspace(e[1]) if w and w.monitor and w.monitor.name==e[2] then home(e[1],e[2]) end end) end')


def _validated(moves):
    if not isinstance(moves, dict) or set(moves) != {'workspaces', 'windows'}: raise ValueError('invalid adoption plan')
    for wid, name in moves['workspaces']:
        if type(wid) is not int or not 0 < wid <= 2147483647 or not OUTPUT.match(name): raise ValueError('invalid adoption plan')
    for address, name in moves['windows']:
        if not isinstance(address, str) or not ADDRESS.match(address) or not OUTPUT.match(name):
            raise ValueError('invalid adoption plan')
    return moves


def _chunk(windows, workspaces):
    c = ','.join(f'{{"{a}","{n}"}}' for a, n in windows)
    w = ','.join(f'{{{i},"{n}"}}' for i, n in workspaces)
    return f'local C={{{c}}} local W={{{w}}} ' + _LUA_HEAD + _LUA_WINDOWS + _LUA_WORKSPACES


def lua(moves):
    """The eval(s) for a plan: one when it fits (windows first, so a window lands on its desktop's own workspace, then
    the workspaces), else several, each under EVAL_LIMIT. [] when there is nothing to move."""
    moves = _validated(moves)
    items = [('C', m) for m in moves['windows']] + [('W', m) for m in moves['workspaces']]
    out, part = [], []
    def code(p): return _chunk([m for k, m in p if k == 'C'], [m for k, m in p if k == 'W'])
    for item in items:
        if part and len(code(part + [item])) > EVAL_LIMIT:
            out.append(code(part)); part = []
        part.append(item)
    if part: out.append(code(part))
    return out


def adopt(client, monitor, names, notify=None):
    """Run start: move the fallback's windows onto the run's desktops (module docstring). Returns the plan made, or None
    if nothing could be done. Never raises for compositor errors: the run goes on."""
    try:
        moves = plan(client.query('workspaces'), client.query('clients'), monitor, names)
        if moves['workspaces'] or moves['windows']: client.adopt_windows(moves)
        return moves
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        if notify is not None: notify('Could not move your windows onto the glasses.')
        return None
