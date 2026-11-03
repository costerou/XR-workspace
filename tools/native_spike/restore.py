"""Session end: every window the run adopted (adopt.py) goes back where it was before XR, in one compositor tick.

Snapshot, at run start, just before adoption: the physical fallback monitor's regular workspaces (by id, and whether
each moves whole), every window adoption moves (its workspace id, floating state, position and size, fullscreen
state), the workspace the fallback shows, and the focused window and monitor. It is kept in memory and in the state
directory ($XDG_STATE_HOME/viture-workspace/layouts/<run_id>.json, 0600), so a crash recovery (the CLI `recover` or the
panel's Recover) restores it too. It is removed once recovery completes; an incomplete recovery keeps it for the next.

Restore, at Stop and at Recover, before recovery evacuates and removes the XR outputs: one Hyprland eval of generated
Lua (fixed shape, every value validated), so no intermediate layout is drawn. Each step runs in pcall: one that fails
leaves the others to run, and recovery's own evacuation still brings back anything left on an XR output.

  1. Workspaces that moved whole take their own ids again. Each is found by its windows (the workspace holding most of
     them), wherever adoption, Omarchy's block renumbering or a pill's Add left it, and renamed back: its tiling layout
     (order, split orientation and ratios: the layout tree belongs to the workspace) comes back with it. A workspace
     already using that id (an XR desktop's start number, say) steps aside to a free id in its own monitor's block.
  2. Every adopted window not on its own workspace now (moved one by one at start, or moved during the run) goes back
     there, created on the fallback if it has gone.
  3. Those workspaces return to the fallback monitor. Workspaces opened on an XR desktop during the run, with windows,
     come to the fallback too, under the same slot of its block when that is free (Omarchy), so they don't land on a
     restored number or a stranger's block.
  4. Per window: fullscreen off, floating state as it was, tiled windows in their old order (swapped into the slot they
     had, by position), floating windows at their old size and position, then fullscreen as it was. Windows opened
     during the run stay on their workspace, tiled after the restored ones in their own order. Hyprland reports a
     fullscreen window at the monitor's origin, not its tiled slot, so one that was fullscreen at start keeps the slot
     it has now (among the others' order) and goes fullscreen again.
  5. The fallback shows the workspace it showed, and focus goes back to the window (or monitor) that had it.

Only the XR desktops and the fallback are put back. A window or workspace moved to any other monitor during the run (DP-2, a
second physical monitor) was moved there on purpose and stays, as it is and under the number it has (Cody, 2026-11-03; `off` in
the Lua). Carried back here it would also be on the fallback at the next start, and adoption would take it into XR.
"""
import json
import os
from pathlib import Path
import re
import secrets

from .adopt import ADDRESS, _regular, occupied, stays
from .ipc import private_dir, read_private_json, token

# One eval carries the whole restore. Hyprland reads a request in 1023-byte reads until a short one; a local stream
# socket written in one sendall delivers it whole, so the bound is ours: ~200 windows.
EVAL_LIMIT = 16384
MONITOR = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z')
COORD = 1 << 20
FILE_LIMIT = 262144


def _int(value, low, high):
    return type(value) is int and low <= value <= high


def _pair(value):
    return [value[0], value[1]] if (isinstance(value, list) and len(value) == 2
                                    and all(_int(v, -COORD, COORD) for v in value)) else None


def capture(workspaces, clients, monitors, active, fallback, run_id):
    """The snapshot (module docstring) of the fallback `fallback`, from Hyprland's j/workspaces, j/clients,
    j/monitors and j/activewindow replies."""
    groups = occupied(workspaces, clients, fallback)
    whole = {wid: w for wid, w, _ in groups}
    moving = {a for _, _, addresses in groups for a in addresses}
    ids = sorted({w['id'] for w in workspaces if _regular(w) and w.get('monitor') == fallback})
    windows = []
    for c in clients:
        if not isinstance(c, dict) or c.get('address') not in moving or stays(c): continue
        at, size = _pair(c.get('at')), _pair(c.get('size'))
        fullscreen, client = c.get('fullscreen', 0), c.get('fullscreenClient', 0)
        windows.append({'address': c['address'], 'workspace': c['workspace']['id'], 'floating': c.get('floating') is True,
                        'at': at or [0, 0], 'size': size if size and min(size) > 0 else [0, 0],
                        'fullscreen': fullscreen if _int(fullscreen, 0, 3) else 0,
                        'client': client if _int(client, 0, 3) else 0})
    shown = next((m.get('activeWorkspace') for m in monitors if isinstance(m, dict) and m.get('name') == fallback), None)
    shown = shown.get('id') if isinstance(shown, dict) else None
    focused = next((m.get('name') for m in monitors if isinstance(m, dict) and m.get('focused') is True), None)
    focus = active.get('address') if isinstance(active, dict) else None
    return validate({'schema': 1, 'run_id': run_id, 'monitor': fallback,
                     'active': shown if _int(shown, 1, 2147483647) else None,
                     'focus': focus if isinstance(focus, str) and ADDRESS.match(focus) else None,
                     'focus_monitor': focused if isinstance(focused, str) and MONITOR.match(focused) else None,
                     'workspaces': [{'id': i, 'whole': whole.get(i, False)} for i in ids], 'windows': windows})


def validate(snapshot):
    """The snapshot if it has exactly the shape capture() makes, else ValueError. Loaded ones are checked again."""
    keys = {'schema', 'run_id', 'monitor', 'active', 'focus', 'focus_monitor', 'workspaces', 'windows'}
    if not isinstance(snapshot, dict) or set(snapshot) != keys or snapshot['schema'] != 1 or type(snapshot['schema']) is not int:
        raise ValueError('invalid layout snapshot')
    token(snapshot['run_id'])
    if not isinstance(snapshot['monitor'], str) or not MONITOR.match(snapshot['monitor']) or snapshot['monitor'].startswith('XR-'):
        raise ValueError('invalid layout monitor')
    if snapshot['active'] is not None and not _int(snapshot['active'], 1, 2147483647): raise ValueError('invalid active workspace')
    if snapshot['focus'] is not None and (not isinstance(snapshot['focus'], str) or not ADDRESS.match(snapshot['focus'])):
        raise ValueError('invalid focus')
    if snapshot['focus_monitor'] is not None and (not isinstance(snapshot['focus_monitor'], str)
                                                  or not MONITOR.match(snapshot['focus_monitor'])):
        raise ValueError('invalid focus monitor')
    spaces, seen = snapshot['workspaces'], set()
    if not isinstance(spaces, list): raise ValueError('invalid workspaces')
    for s in spaces:
        if (not isinstance(s, dict) or set(s) != {'id', 'whole'} or not _int(s['id'], 1, 2147483647)
                or type(s['whole']) is not bool or s['id'] in seen): raise ValueError('invalid workspace')
        seen.add(s['id'])
    if not isinstance(snapshot['windows'], list): raise ValueError('invalid windows')
    addresses = set()
    for w in snapshot['windows']:
        if (not isinstance(w, dict) or set(w) != {'address', 'workspace', 'floating', 'at', 'size', 'fullscreen', 'client'}
                or not isinstance(w['address'], str) or not ADDRESS.match(w['address']) or w['address'] in addresses
                or w['workspace'] not in seen or type(w['workspace']) is not int or type(w['floating']) is not bool
                or _pair(w['at']) is None or _pair(w['size']) is None or min(w['size']) < 0
                or not _int(w['fullscreen'], 0, 3) or not _int(w['client'], 0, 3)):
            raise ValueError('invalid window')
        addresses.add(w['address'])
    return snapshot


def layout_dir(environ=None):
    environ = os.environ if environ is None else environ
    base = Path(environ.get('XDG_STATE_HOME') or Path(environ.get('HOME') or Path.home()) / '.local/state')
    return base / 'viture-workspace' / 'layouts'


def _path(run_id, directory):
    return Path(directory) / (token(run_id) + '.json')


def save(snapshot, directory=None):
    """Atomically, 0600, in a 0700 directory (created): the snapshot for its run. Returns the path."""
    snapshot = validate(snapshot)
    directory = Path(directory) if directory is not None else layout_dir()
    directory.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = private_dir(directory, create=True)
    name = token(snapshot['run_id']) + '.json'
    try:
        tmp = f'.{name}.{secrets.token_hex(8)}.tmp'
        out = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=fd)
        try:
            with os.fdopen(out, 'wb') as stream:
                stream.write(json.dumps(snapshot, sort_keys=True).encode()); stream.flush(); os.fsync(stream.fileno())
            os.replace(tmp, name, src_dir_fd=fd, dst_dir_fd=fd)
        except BaseException:
            try: os.unlink(tmp, dir_fd=fd)
            except OSError: pass
            raise
        os.fsync(fd)
    finally:
        os.close(fd)
    return directory / name


def load(run_id, directory=None):
    """The run's saved snapshot, or None if there is none. A file that is not a valid one for this run raises."""
    directory = Path(directory) if directory is not None else layout_dir()
    try: os.close(private_dir(directory))
    except FileNotFoundError: return None
    try: snapshot = read_private_json(_path(run_id, directory), limit=FILE_LIMIT)
    except FileNotFoundError: return None
    snapshot = validate(snapshot)
    if snapshot['run_id'] != run_id: raise ValueError('layout snapshot belongs to another run')
    return snapshot


def discard(run_id, directory=None):
    directory = Path(directory) if directory is not None else layout_dir()
    try: _path(run_id, directory).unlink()
    except FileNotFoundError: pass


# The restore (module docstring), steps 0-5. F fallback, A shown workspace (0: none), P focused window (""), PM focused
# monitor (""), S {id, whole}, W {address, workspace, floating, x, y, w, h, fullscreen, client}.
_LUA = (
    'local ow=type(o)=="table" and type(o.workspaces)=="table" and o.workspaces or nil '
    'local function D(f,a) pcall(function() hl.dispatch(f(a)) end) end '
    'local function ws(id) return hl.get_workspace(id) end '
    'local function win(a) return hl.get_window("address:"..a) end '
    'local function ren(a,b) D(hl.dsp.workspace.change_id,{workspace=tostring(a),id=b}) end '
    'local T={} for _,s in ipairs(S) do T[s[1]]=s end '
    'if not hl.get_monitor(F) then return end '
    'local function block(m) if not ow or type(ow.workspace_id)~="function" or not m then return nil end '
    'local ok,v=pcall(ow.workspace_id,m,1) if ok and type(v)=="number" then return v-1 end end '
    'local base=block(hl.get_monitor(F)) '
    'local function free(b) if b then for s=1,10 do if not ws(b+s) and not T[b+s] then return b+s end end return nil end '
    'local n=1 for _,x in ipairs(hl.get_workspaces()) do if x.id>=n then n=x.id+1 end end while T[n] do n=n+1 end return n end '
    'local function aside(x) local n=free(block(x.monitor)) or free(nil) ren(x.id,n) end '
    'local function reg(x) return x and not x.special and x.id>0 end '
    'local function off(x) local m=x and x.monitor return m and m.name~=F and not m.name:match("^XR%-") end '
    'D(hl.dsp.focus,{monitor=F}) '
    # 1: whole workspaces back under their own ids
    'local cnt={} for _,e in ipairs(W) do local t=T[e[2]] local w=win(e[1]) local c=w and w.workspace '
    'if t and t[2]==1 and reg(c) and not off(c) and (c.id==e[2] or not T[c.id]) then cnt[e[2]]=cnt[e[2]] or {} '
    'cnt[e[2]][c.id]=(cnt[e[2]][c.id] or 0)+1 end end '
    'local L={} for t,m in pairs(cnt) do for c,k in pairs(m) do L[#L+1]={k,t,c} end end '
    'table.sort(L,function(a,b) if a[1]~=b[1] then return a[1]>b[1] end if a[2]~=b[2] then return a[2]<b[2] end return a[3]<b[3] end) '
    'local got,used,tmp={},{},{} for _,x in ipairs(L) do if not got[x[2]] and not used[x[3]] then got[x[2]]=x[3] used[x[3]]=true end end '
    'for _,s in ipairs(S) do local c=got[s[1]] if c and c~=s[1] then local n=free(nil) ren(c,n) if ws(n) then tmp[#tmp+1]={n,s[1]} end end end '
    'for _,p in ipairs(tmp) do local x=ws(p[2]) if x then aside(x) end if not ws(p[2]) then ren(p[1],p[2]) end end '
    # 2: windows back onto their workspaces
    'for _,e in ipairs(W) do local w=win(e[1]) if w and not w.pinned and w.workspace and w.workspace.id~=e[2] and not off(w.workspace) then '
    'D(hl.dsp.window.move,{workspace=tostring(e[2]),follow=false,window="address:"..e[1]}) end end '
    # 3: onto the fallback; then workspaces opened on an XR desktop during the run
    'for _,s in ipairs(S) do local x=ws(s[1]) if x and x.monitor and x.monitor.name~=F and not off(x) then '
    'D(hl.dsp.workspace.move,{workspace=tostring(s[1]),monitor=F}) end end '
    'local X={} for _,x in ipairs(hl.get_workspaces()) do if reg(x) and not T[x.id] and (x.windows or 0)>0 and x.monitor '
    'and x.monitor.name:match("^XR%-%d+$") then X[#X+1]=x.id end end table.sort(X) '
    'for _,id in ipairs(X) do if base and not (id>base and id<=base+10) then local want=base+(id-1)%10+1 '
    'local n=(not ws(want) and not T[want]) and want or free(base) if n then ren(id,n) if ws(n) then id=n end end end '
    'D(hl.dsp.workspace.move,{workspace=tostring(id),monitor=F}) end '
    # 4: per window: fullscreen off, floating, tiled order, floating geometry, fullscreen
    'local mine={} for _,e in ipairs(W) do local w=win(e[1]) if w and w.workspace and w.workspace.id==e[2] and not off(w.workspace) then mine[#mine+1]=e '
    'local sel="address:"..e[1] if (w.fullscreen or 0)~=0 or (w.fullscreen_client or 0)~=0 then '
    'D(hl.dsp.window.fullscreen_state,{internal=0,client=0,action="set",window=sel}) end '
    'if w.floating~=(e[3]==1) then D(hl.dsp.window.float,{action=e[3]==1 and "on" or "off",window=sel}) end end end '
    'local function tl(w) return w and not w.floating and not w.group and (w.fullscreen or 0)==0 end '
    'local by={} for _,e in ipairs(mine) do if e[3]==0 and e[8]==0 and tl(win(e[1])) then '
    'by[e[2]]=by[e[2]] or {} local g=by[e[2]] g[#g+1]={a=e[1],x=e[4],y=e[5]} end end '
    'local function yx(p,q) if p.y~=q.y then return p.y<q.y end if p.x~=q.x then return p.x<q.x end return p.a<q.a end '
    'for t,g in pairs(by) do local cur,inq={},{} for _,p in ipairs(g) do inq[p.a]=true end '
    'for _,w in ipairs(hl.get_windows()) do if tl(w) and w.workspace and w.workspace.id==t then '
    'cur[#cur+1]={a=w.address,x=w.at.x,y=w.at.y} end end table.sort(g,yx) table.sort(cur,yx) '
    'for _,p in ipairs(cur) do if not inq[p.a] then g[#g+1]=p end end local pos={} for i,p in ipairs(cur) do pos[p.a]=i end '
    'for k,p in ipairs(g) do local b=cur[k] and cur[k].a if b and pos[p.a] and b~=p.a then '
    'D(hl.dsp.window.swap,{window="address:"..p.a,target="address:"..b}) '
    'local i=pos[p.a] cur[i],cur[k]=cur[k],cur[i] pos[b],pos[p.a]=i,k end end end '
    'for _,e in ipairs(mine) do local sel="address:"..e[1] if e[3]==1 then '
    'if e[6]>0 and e[7]>0 then D(hl.dsp.window.resize,{x=e[6],y=e[7],window=sel}) end D(hl.dsp.window.move,{x=e[4],y=e[5],window=sel}) end '
    'if e[8]~=0 or e[9]~=0 then D(hl.dsp.window.fullscreen_state,{internal=e[8],client=e[9],action="set",window=sel}) end end '
    # 5: the shown workspace, then focus
    'if A>0 and not (off(ws(A)) and (ws(A).windows or 0)>0) then local x=ws(A) if x and x.monitor and x.monitor.name~=F then if (x.windows or 0)==0 then aside(x) else '
    'D(hl.dsp.workspace.move,{workspace=tostring(A),monitor=F}) end x=ws(A) end D(hl.dsp.focus,{monitor=F}) '
    'if x then pcall(function() hl.get_monitor(F):set_workspace({workspace=x.name}) end) else D(hl.dsp.focus,{workspace=tostring(A)}) end end '
    'if P~="" and win(P) then D(hl.dsp.focus,{window="address:"..P}) elseif PM~="" and hl.get_monitor(PM) then D(hl.dsp.focus,{monitor=PM}) end')


def lua(snapshot):
    """The restore eval for a snapshot: one, under EVAL_LIMIT, or ValueError."""
    s = validate(snapshot)
    spaces = ','.join(f'{{{w["id"]},{1 if w["whole"] else 0}}}' for w in s['workspaces'])
    windows = ','.join(f'{{"{w["address"]}",{w["workspace"]},{1 if w["floating"] else 0},{w["at"][0]},{w["at"][1]},'
                       f'{w["size"][0]},{w["size"][1]},{w["fullscreen"]},{w["client"]}}}' for w in s['windows'])
    code = (f'local F="{s["monitor"]}" local A={s["active"] or 0} local P="{s["focus"] or ""}" '
            f'local PM="{s["focus_monitor"] or ""}" local S={{{spaces}}} local W={{{windows}}} ' + _LUA)
    if len(code) > EVAL_LIMIT: raise ValueError('layout too large for one restore')
    return code


def take(client, fallback, run_id, directory=None):
    """Run start, before adoption: the snapshot, saved to the state directory. None if it cannot be read; one that
    cannot be saved is still returned (Stop restores from memory; only a crash recovery would miss it)."""
    try:
        snapshot = capture(client.query('workspaces'), client.query('clients'), client.query('monitors'),
                           client.query('activewindow'), fallback, run_id)
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None
    try: save(snapshot, directory)
    except (OSError, ValueError): pass
    return snapshot


def restore(client, snapshot, notify=None):
    """Stop / Recover, before recovery: put the windows back (module docstring). True if the eval ran. Never raises
    for compositor errors: recovery runs regardless."""
    if snapshot is None or not hasattr(client, 'restore_layout'): return False
    try:
        client.restore_layout(snapshot)
        return True
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        if notify is not None: notify('Could not put your windows back where they were.')
        return False
