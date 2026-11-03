"""Window memory: every window goes back to the XR desktop, workspace and place it had when XR last stopped.

The monitor setup comes back on its own (profiles.load_setup). This does the same for the windows on it: Cody, 2026-11-03,
"Remember where windows used to be and put them back on the correct monitors in the correct orientations."

What is remembered (capture). For every window on one of the run's XR desktops: who it is (address, class, title, initial
title, pid), where it is (output, its position among the run's desktops, workspace id and the workspace's tiled layout), and
how (floating, position relative to the desktop, size, fullscreen, tile order, scrolling column). Each desktop keeps its
context too: size and scale, the workspace it showed, and its placement and levelling roll round you. Only the desktops'
own regular workspaces count: never a special workspace, a pinned window or the XR Workspace panel (adopt.stays), the same
rule adoption and recovery keep.

Where it lives. window-memory.json beside profiles.json, one entry per glasses model, 0600, always replaced atomically (a
temporary file in the same directory, fsync, rename). It is its own file so that nothing that rewrites a profile can touch
it: Ctrl+Alt+R and a discarded setup reset the monitors and leave the windows' memory alone. It holds window titles, as
Hyprland reports them, cut to TEXT_LIMIT. An empty capture never replaces a memory: a run that ended with nothing on the
glasses says nothing about where windows belong.

Keeping it current (WindowTracker). The run marks the memory dirty on Hyprland's window events (a Super drag across
desktops, a send key, opening, closing, floating, fullscreen, a workspace change), when a Super drag ends (a drag inside a
workspace sends no event), and when the desktops change. CAPTURE_DELAY_S later a worker, never the supervisor loop, reads
Hyprland and saves if anything changed; the run's end reads it once more, before recovery takes the outputs away. So a crash
loses only the last moment.

Putting them back (place, on run start, after the remembered desktops exist and before the renderer starts):

  1. Which windows. The ones adoption would take: on the physical fallback's regular workspaces, not staying (adopt.occupied).
     A window on another physical monitor is left alone.
  2. Which window is which (match), each pass over what the last left, so nothing is assigned twice: same address (only
     within one Hyprland session, and only with the same class: an address is a pointer, so a new window can reuse it), then
     class + title, class + initial title + pid, class + initial title, class + pid, and last class alone.
  3. Where. The desktop at the same position in the run: profiles keep no names, so a start numbers its desktops from XR-1
     (hyprland.plan_outputs), and after removing XR-2 the old XR-3 is the new XR-2. Past the last one, the last one: the same
     rule as adoption and Ctrl+Alt+R (XR-4 to XR-3), each workspace still its own workspace there.
  4. How. A physical workspace holding exactly what one remembered workspace held moves whole and takes its id (Stop moved it
     back whole, restore.py, so this is the normal case): its tile tree, column widths and split ratios come with it, nothing
     is rebuilt and focus never moves. Anything else moves window by window. Ids are kept because they mean something:
     Omarchy gives each monitor a block of ten by its identity (monitor-workspaces.lua: XR-1 is 61..70 for as long as the
     state file says so) and keys its saved layouts by id. A workspace whose id is taken gets the same slot if free, or the
     first free one.
  5. Then adoption runs as it always has, on whatever is left. Unmatched windows behave exactly as without this.
  6. Then the settling evals: the workspace's layout if it differs, floating state and geometry (inside the desktop, which
     may have changed size: fit_windows), tile order, fullscreen, the workspace each desktop showed, focus and the cursor.

Order and layouts. Hyprland's swap exchanges two windows in their slots whatever the layout, so sorting the slots and the
remembered windows the same way and swapping restores the order in master, dwindle and scrolling alike. A scrolling
workspace rebuilt window by window has one column per window, so if the remembered columns differ from the live ones the
stacks are expelled, the order set, and the stacks consumed again. These messages act on the focused window of the focused
monitor's shown workspace, which is why this step, and only this step, moves focus. Column widths come back by `colresize`:
Hyprland's resize clamps a column to the view and would shrink one scrolled off it. Tiled sizes in other layouts come back by
resize, only on a desktop of the same logical size. What a window-by-window rebuild cannot bring back: the heights inside a
stacked column, a dwindle tree's exact shape, and tab groups (their members move on their own). A workspace that moves whole
keeps all three.

Desktops that go and come back. A desktop removed during a run (the pill's Remove, Ctrl+Alt+R) parks the memory of its
windows: they stay remembered for that desktop while they sit where the removal put them (XR-3, or the physical monitor). If
the same desktop is added again in the run they go straight back. A parked window the user moves elsewhere is simply
remembered there from then on. At a later start a parked window whose desktop is still missing follows step 3.

Everything here is best effort and fails open: a memory that cannot be read, a plan that cannot be made or a move Hyprland
refuses leaves the windows to adoption, and the run goes on. Nothing here widens what a run may do: the windows it moves are
the ones adoption would move, onto the run's own outputs, by generated Lua from validated addresses, output names and whole
numbers (every eval under the client's 4096-byte limit).
"""
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import time

from .adopt import ADDRESS, EVAL_LIMIT, OUTPUT, _LUA_HEAD, _regular, occupied, stays
from .fit_windows import free_area
from .geometry import MAX_DESKTOPS, MAX_ROLL, logical_height, logical_width, validate_desktop, validate_placement
from .profiles import MODELS, ProfileStore, log
from .restore import MONITOR

SCHEMA = 1
FILE_NAME = 'window-memory.json'
FILE_LIMIT = 4 << 20       # a file past this is not read: MAX_WINDOWS at TEXT_LIMIT, for every model, is far less
TEXT_LIMIT = 256           # class and titles are cut to this, when saved and when compared
MAX_WINDOWS = 256          # windows remembered per glasses model; any more behave as if unmatched
COORD = 1 << 20
CAPTURE_DELAY_S = 1.0      # a window change is read this long after it: a burst of moves is one read
QUERY_TIMEOUT_S = 0.5      # each read while the run is live or ending: recovery never waits long on this
FAILURES = (OSError, ValueError, TypeError, KeyError, AttributeError, IndexError)  # what a compositor or file step may raise
LAYOUT = re.compile(r'[a-z]{1,32}\Z')
INSTANCE_LIMIT = 128
COLUMN_TOLERANCE_PX = 1    # tiled windows this close in x share a scrolling column
WINDOW_KEYS = {'address', 'class', 'title', 'initial_title', 'pid', 'output', 'index', 'workspace', 'layout', 'floating', 'at',
               'size', 'fullscreen', 'client', 'order', 'column', 'parked', 'landing'}
DESKTOP_KEYS = {'width', 'height', 'scale', 'active', 'placement', 'roll'}
PLAN_KEYS = {'whole', 'windows', 'layouts', 'state', 'order', 'fullscreen', 'shown', 'placed', 'focus', 'monitor'}
# match(): each pass the fields that must agree, after the address.
PASSES = (('class', 'title'), ('class', 'initial_title', 'pid'), ('class', 'initial_title'), ('class', 'pid'), ('class',))


def _number(name):
    return int(name.split('-', 1)[1])


def _int(value, low, high):
    return type(value) is int and low <= value <= high


def _pair(value):
    return isinstance(value, list) and len(value) == 2 and all(_int(v, -COORD, COORD) for v in value)


def _text(value):
    return value[:TEXT_LIMIT] if isinstance(value, str) else ''


def _whole(value):
    """A coordinate as Hyprland's JSON gives it, as an int, or None."""
    if type(value) is int: return value
    return int(round(value)) if type(value) is float and abs(value) < COORD else None


def _coords(value):
    if not isinstance(value, list) or len(value) != 2: return None
    out = [_whole(v) for v in value]
    return None if None in out or not all(-COORD <= v <= COORD for v in out) else out


def _names(names):
    if not isinstance(names, (list, tuple)) or not names or any(not isinstance(n, str) or not OUTPUT.match(n) for n in names):
        raise ValueError('invalid outputs')
    return sorted(names, key=_number)


def _desktop(monitor, shown, context):
    size = {k: monitor[k] for k in ('width', 'height', 'scale')}
    validate_desktop({**size, 'scale': float(size['scale'])})
    return {**size, 'active': shown if _int(shown, 1, 2147483647) else None,
            'placement': context.get('placement'), 'roll': context.get('roll')}


def capture(clients, workspaces, monitors, names, instance='', context=None):
    """The memory (module docstring) of the desktops `names`, from Hyprland's j/clients, j/workspaces and j/monitors replies.
    `context`: optional {name: {'placement', 'roll'}}, as the view has them now. A malformed window is skipped."""
    if not all(isinstance(v, list) for v in (clients, workspaces, monitors)): raise ValueError('invalid compositor state')
    names = _names(names)
    index = {n: i for i, n in enumerate(names)}
    found = {m['name']: m for m in monitors if isinstance(m, dict) and m.get('name') in index}
    desktops = {}
    for name, m in found.items():
        shown = m.get('activeWorkspace')
        desktops[name] = _desktop(m, shown.get('id') if isinstance(shown, dict) else None, (context or {}).get(name) or {})
    spaces = {w['id']: w for w in workspaces if _regular(w) and w.get('monitor') in found}
    on = {}
    for c in clients:
        if not isinstance(c, dict) or not isinstance(c.get('workspace'), dict) or c['workspace'].get('id') not in spaces: continue
        if not isinstance(c.get('address'), str) or not ADDRESS.match(c['address']): continue
        if stays(c) or c.get('mapped') is False: continue
        at, size = _coords(c.get('at')), _coords(c.get('size'))
        if at is None or size is None or min(size) < 0: continue
        on.setdefault(c['workspace']['id'], []).append((c, at, size))
    windows = []
    for wid in sorted(on, key=lambda i: (index[spaces[i]['monitor']], i)):
        output, layout = spaces[wid]['monitor'], spaces[wid].get('tiledLayout')
        layout = layout if isinstance(layout, str) and LAYOUT.match(layout) else ''
        def tiled(c): return (c.get('floating') is not True and not c.get('grouped') and c.get('fullscreen') in (None, 0, False)
                              and c.get('fullscreenClient') in (None, 0, False))
        rows = sorted((r for r in on[wid] if tiled(r[0])), key=lambda r: (r[1][0], r[1][1], r[0]['address']))
        order, column, last = {}, -1, None
        for k, (c, at, _) in enumerate(rows):
            if last is None or abs(at[0] - last) > COLUMN_TOLERANCE_PX: column += 1
            last = at[0]
            order[c['address']] = (k, column if layout == 'scrolling' else k)
        origin = found[output]
        placed = rows + [r for r in on[wid] if not tiled(r[0])]
        for c, at, size in placed:
            at = [at[0] - origin['x'], at[1] - origin['y']]
            if not _pair(at): continue
            fullscreen, client = c.get('fullscreen', 0), c.get('fullscreenClient', 0)
            windows.append({'address': c['address'], 'class': _text(c.get('class')), 'title': _text(c.get('title')),
                            'initial_title': _text(c.get('initialTitle')), 'pid': c['pid'] if _int(c.get('pid'), 0, 1 << 31) else 0,
                            'output': output, 'index': index[output], 'workspace': wid, 'layout': layout,
                            'floating': c.get('floating') is True, 'at': at, 'size': size,
                            'fullscreen': fullscreen if _int(fullscreen, 0, 3) else 0, 'client': client if _int(client, 0, 3) else 0,
                            'order': order.get(c['address'], (None, None))[0], 'column': order.get(c['address'], (None, None))[1],
                            'parked': False, 'landing': None})
    return validate({'instance': instance if isinstance(instance, str) else '', 'desktops': desktops, 'windows': windows[:MAX_WINDOWS]})


def _valid_window(w):
    return (isinstance(w, dict) and set(w) == WINDOW_KEYS and isinstance(w['address'], str) and ADDRESS.match(w['address'])
            and all(isinstance(w[k], str) and len(w[k]) <= TEXT_LIMIT for k in ('class', 'title', 'initial_title'))
            and _int(w['pid'], 0, 1 << 31) and isinstance(w['output'], str) and OUTPUT.match(w['output'])
            and (w['index'] is None or _int(w['index'], 0, MAX_DESKTOPS - 1)) and _int(w['workspace'], 1, 2147483647)
            and isinstance(w['layout'], str) and (w['layout'] == '' or LAYOUT.match(w['layout']))
            and type(w['floating']) is bool and _pair(w['at']) and _pair(w['size']) and min(w['size']) >= 0
            and _int(w['fullscreen'], 0, 3) and _int(w['client'], 0, 3)
            and (w['order'] is None or _int(w['order'], 0, MAX_WINDOWS)) and (w['column'] is None or _int(w['column'], 0, MAX_WINDOWS))
            and type(w['parked']) is bool
            and (w['landing'] is None or (isinstance(w['landing'], str) and MONITOR.match(w['landing']))))


def _valid_desktop(d):
    if not isinstance(d, dict) or set(d) != DESKTOP_KEYS: return False
    try:
        validate_desktop({k: d[k] for k in ('width', 'height', 'scale')})
        if d['placement'] is not None: validate_placement(d['placement'])
    except ValueError:
        return False
    return ((d['active'] is None or _int(d['active'], 1, 2147483647))
            and (d['roll'] is None or _int(d['roll'], -MAX_ROLL, MAX_ROLL)))


def validate(memory):
    """The memory if it has exactly the shape capture() makes, else ValueError. Loaded ones are checked again."""
    if not isinstance(memory, dict) or set(memory) != {'instance', 'desktops', 'windows'}: raise ValueError('invalid window memory')
    if not isinstance(memory['instance'], str) or len(memory['instance']) > INSTANCE_LIMIT: raise ValueError('invalid window memory')
    desktops, windows = memory['desktops'], memory['windows']
    if not isinstance(desktops, dict) or any(not isinstance(n, str) or not OUTPUT.match(n) or not _valid_desktop(d) for n, d in desktops.items()):
        raise ValueError('invalid desktops in the window memory')
    if not isinstance(windows, list) or len(windows) > MAX_WINDOWS or not all(_valid_window(w) for w in windows):
        raise ValueError('invalid windows in the window memory')
    if len({w['address'] for w in windows}) != len(windows): raise ValueError('a window remembered twice')
    return memory


def memory_path(profiles):
    """The memory file beside the profiles file."""
    return Path(profiles).with_name(FILE_NAME)


class Store:
    """One glasses model's memory in the shared file; the other models' are preserved."""
    def __init__(self, path, model):
        if model not in MODELS: raise ValueError('unknown model')
        self.path, self.model = Path(path), model

    def _models(self):
        try:
            if self.path.stat().st_size > FILE_LIMIT: return {}
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict) or data.get('schema') != SCHEMA or not isinstance(data.get('models'), dict): return {}
        return data['models']

    def load(self):
        """The saved memory, or None if there is none or it is unusable (which reads as nothing remembered)."""
        raw = self._models().get(self.model)
        if raw is None: return None
        try: return validate(raw)
        except ValueError: return None

    def save(self, memory):
        models = {k: v for k, v in self._models().items() if k in MODELS and k != self.model}
        models[self.model] = validate(memory)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix='.window-memory.', suffix='.tmp')
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, 'w') as f:
                json.dump({'schema': SCHEMA, 'models': models}, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            try: os.unlink(tmp)
            except OSError: pass
            raise


def _identity(client):
    return {'address': client.get('address'), 'class': _text(client.get('class')), 'title': _text(client.get('title')),
            'initial_title': _text(client.get('initialTitle')), 'pid': client.get('pid') if _int(client.get('pid'), 1, 1 << 31) else 0}


def _slot_key(client):
    workspace = client.get('workspace')
    at = client.get('at')
    return (workspace.get('id', 0) if isinstance(workspace, dict) else 0, at if isinstance(at, list) else [], str(client.get('address')))


def match(records, clients, same_instance, exact=False):
    """Which window is which (module docstring, step 2): [(record, client)], each used once. `same_instance`: the memory was
    made in this Hyprland session, so an address can mean something. `exact`: by address only (a parked window coming home:
    only that very window may move)."""
    left = sorted((c for c in clients if isinstance(c, dict)), key=_slot_key)
    who = {id(c): _identity(c) for c in left}
    todo, pairs = list(records), []
    def take(record, client):
        pairs.append((record, client))
        todo.remove(record)
        left.remove(client)
    if same_instance:
        by_address = {who[id(c)]['address']: c for c in left}
        for r in list(todo):
            c = by_address.get(r['address'])
            if c is not None and who[id(c)]['class'] == r['class']: take(r, c)
    for fields in () if exact else PASSES:
        buckets = {}
        for c in left:
            key = tuple(who[id(c)][f] for f in fields)
            if all(key): buckets.setdefault(key, []).append(c)
        for r in list(todo):
            key = tuple(r[f] for f in fields)
            if all(key) and buckets.get(key): take(r, buckets[key].pop(0))
    return pairs


def _target(record, names):
    """(output, moved): the desktop a record goes to in a run with `names`, and whether that is not its own."""
    if record['parked'] or record['index'] is None:
        return (record['output'], False) if record['output'] in names else (names[-1], True)
    return (names[record['index']], False) if record['index'] < len(names) else (names[-1], True)


def _place_key(record):
    return (_number(record['output']), record['workspace'], record['order'] is None,
            record['order'] if record['order'] is not None else 0, record['address'])


def _floating(record, monitor, monitors):
    """A floating window's (x, y, w, h) on `monitor`: as remembered, or the least move that puts it inside (fit_windows)."""
    (dx, dy, dw, dh), (ux, uy, uw, uh) = free_area({**monitor, 'scale': float(monitor['scale'])}, monitors)
    x, y = dx + record['at'][0], dy + record['at'][1]
    w, h = record['size']
    if w <= 0 or h <= 0: return x, y, 0, 0
    if dx <= x and dy <= y and x + w <= dx + dw and y + h <= dy + dh: return x, y, w, h
    w, h = min(w, uw), min(h, uh)
    return max(ux, min(x, ux + uw - w)), max(uy, min(y, uy + uh - h)), w, h


def _logical(d):
    return logical_width(d), logical_height(d)


def _tile_size(record, old, new, moved):
    """(w, h) a tiled window should end up: 0 for what is left to the layout. A scrolling column keeps its share of the
    desktop's width; a fixed tile size only means something on a desktop of the same logical size."""
    if old is None: return 0, 0
    w, h = record['size']
    same = not moved and _logical(old) == _logical(new)
    if record['layout'] == 'scrolling': return int(round(w * logical_width(new) / logical_width(old))), h if same else 0
    return (w, h) if same else (0, 0)


def plan(memory, clients, workspaces, monitors, names, source, instance='', active=None, exact=False):
    """Pure: what putting the windows back takes (module docstring), for the windows on output `source`. `active`: {'address',
    'monitor'} to hand focus back to. A dict of lists in the order things happen; `placed` says which record went where."""
    names = _names(names)
    groups = occupied(workspaces, clients, source)
    movable = {a for _, _, addresses in groups for a in addresses}
    candidates = [c for c in clients if isinstance(c, dict) and c.get('address') in movable]
    records = sorted(memory['windows'], key=_place_key)
    pairs = match(records, candidates, bool(instance) and memory['instance'] == instance, exact=exact)
    pairs.sort(key=lambda p: _place_key(p[0]))
    monitor_of = {m['name']: m for m in monitors if isinstance(m, dict) and m.get('name') in names}
    placed = []
    for record, client in pairs:
        output, moved = _target(record, names)
        if output in monitor_of:
            placed.append({'record': record, 'client': client, 'address': client['address'], 'output': output, 'moved': moved,
                           'group': (output, record['workspace'], moved)})
    group_of = {p['address']: p['group'] for p in placed}

    # A workspace all of whose windows go to one remembered workspace moves whole; two for one target: the larger one.
    claims = {}
    for wid, whole, addresses in groups:
        keys = {group_of.get(a) for a in addresses}
        if whole and len(keys) == 1 and None not in keys: claims.setdefault(keys.pop(), []).append((len(addresses), -wid))
    chosen = {-max(options)[1]: key for key, options in claims.items()}
    order_key = lambda group: (group[2], _number(group[0]), group[1])
    gone = {a for wid, whole, addresses in groups if wid in chosen for a in addresses}
    out = {'whole': [(wid, key[0], key[1]) for wid, key in sorted(chosen.items(), key=lambda kv: order_key(kv[1]))],
           'windows': [(p['address'], p['group'][0], p['group'][1], 1 if p['moved'] else 0)
                       for p in sorted(placed, key=lambda p: order_key(p['group'])) if p['address'] not in gone],
           'layouts': [], 'state': [], 'order': [], 'fullscreen': [], 'shown': [], 'placed': placed,
           'focus': None, 'monitor': None}

    rows, heads, shown = {}, {}, {}
    for p in placed:
        record, client, group = p['record'], p['client'], p['group']
        monitor = monitor_of[p['output']]
        new = {'width': monitor['width'], 'height': monitor['height'], 'scale': float(monitor['scale'])}
        if record['layout'] and group not in heads:
            heads[group] = p['address']
            out['layouts'].append((p['address'], record['layout']))
        if record['floating']:
            out['state'].append((p['address'], 1, *(int(round(v)) for v in _floating(record, monitor, monitors))))
        elif client.get('floating') is True or client.get('fullscreen') not in (None, 0, False):
            out['state'].append((p['address'], 0, 0, 0, 0, 0))
        if record['fullscreen'] or record['client']: out['fullscreen'].append((p['address'], record['fullscreen'], record['client']))
        if record['order'] is not None and not record['floating']:
            size = _tile_size(record, memory['desktops'].get(record['output']), new, p['moved'])
            rows.setdefault(group, []).append((p['address'], record['column'] or 0, *size))
        old = memory['desktops'].get(record['output'])
        if not p['moved'] and old is not None and old['active'] == record['workspace']: shown.setdefault(p['output'], p['address'])
    out['order'] = [rows[group] for group in sorted(rows, key=order_key)]
    out['shown'] = sorted(shown.items(), key=lambda kv: _number(kv[0]))
    if isinstance(active, dict):
        address, monitor = active.get('address'), active.get('monitor')
        out['focus'] = address if isinstance(address, str) and ADDRESS.match(address) else None
        out['monitor'] = monitor if isinstance(monitor, str) and MONITOR.match(monitor) else None
    return out


def empty(placement):
    """Nothing to move."""
    return not placement['whole'] and not placement['windows']


# The generated evals. Every value in them is validated (_validated): addresses, XR output names, layout names of [a-z] and
# whole numbers. Each step runs in pcall, so one refused move leaves the others to run. A window that has closed is skipped.

# pick: the id a group of loose windows takes on monitor m: the remembered one, or its slot in m's block (Omarchy); `own`: it
# comes from a desktop that is gone, so it must not join a workspace holding windows. Nil: no id of its own, it joins what m
# shows. G keeps each group on the id its first window got.
_LUA_PICK = (
    'local G={} '
    'local function pick(m,id,own) local b=ow and type(ow.workspace_id)=="function" and ow.workspace_id(m,1)-1 or nil '
    'if b and not (id>b and id<=b+10) then id=b+(id-1)%10+1 end local x=hl.get_workspace(id) '
    'if x and ((x.monitor and x.monitor.name~=m.name) or (own and (x.windows or 0)>0)) then id=b and free(b) or nil end return id end ')
_LUA_WHOLE = (
    'for _,e in ipairs(W) do pcall(function() hl.dispatch(hl.dsp.workspace.move({workspace=tostring(e[1]),monitor=e[2]})) '
    'home(e[1],e[2],e[3]) end) end ')
_LUA_LOOSE = (
    'for _,e in ipairs(C) do pcall(function() local m=hl.get_monitor(e[2]) if not m then return end local k=e[2]..":"..e[3] '
    'local id=G[k] or pick(m,e[3],e[4]==1) or (m.active_workspace and m.active_workspace.id) if not id or id<=0 then return end G[k]=id '
    'hl.dispatch(hl.dsp.window.move({workspace=tostring(id),follow=false,window="address:"..e[1]})) '
    'local x=hl.get_workspace(id) if x and x.monitor and x.monitor.name~=e[2] then '
    'hl.dispatch(hl.dsp.workspace.move({workspace=tostring(id),monitor=e[2]})) end end) end')
_LUA_D = ('local function D(f,a) pcall(function() hl.dispatch(f(a)) end) end '
          'local function win(a) return hl.get_window("address:"..a) end ')
_LUA_CURSOR = 'if p then D(hl.dsp.cursor.move,{x=p.x,y=p.y}) end'
# The workspace's layout where it differs (a fresh workspace has the default one), then floating state and geometry. Resize
# first: Hyprland resizes a floating window about its centre.
_LUA_STATE = (
    'for _,e in ipairs(L) do local w=win(e[1]) local x=w and w.workspace if x and x.tiled_layout and x.tiled_layout~=e[2] then '
    'pcall(hl.workspace_rule,{workspace=tostring(x.id),layout=e[2]}) end end '
    'for _,e in ipairs(S) do local w=win(e[1]) if w then local s="address:"..e[1] '
    'if (w.fullscreen or 0)~=0 or (w.fullscreen_client or 0)~=0 then D(hl.dsp.window.fullscreen_state,{internal=0,client=0,action="set",window=s}) end '
    'if w.floating~=(e[2]==1) then D(hl.dsp.window.float,{action=e[2]==1 and "on" or "off",window=s}) end '
    'if e[2]==1 then if e[5]>0 and e[6]>0 then D(hl.dsp.window.resize,{x=e[5],y=e[6],window=s}) end D(hl.dsp.window.move,{x=e[3],y=e[4],window=s}) end '
    'end end')
# One workspace's tiled order (module docstring: Order and layouts). G: {address, column, w, h} in remembered order. slots
# swaps them into the k-th slots by position; windows the memory does not know keep after them. Only if the columns then
# differ: every stack comes apart (expel takes a column's last window), slots again, the stacks form (consume takes the next
# column's first). Widths by colresize, as a share scaled from the live one. `h`: columns were read left to right, so left, up
# and down keep the order and nothing else; anything else is right, as CScrollingAlgorithm::getDynamicDirection has it.
# Nothing that fits is dispatched.
_LUA_ORDER = (
    'local p=hl.get_cursor_pos() '
    'local function tl(w) return w and not w.floating and not w.group and (w.fullscreen or 0)==0 end '
    'local function xy(a,b) if a.x~=b.x then return a.x<b.x end if a.y~=b.y then return a.y<b.y end return a.a<b.a end '
    'local id for _,r in ipairs(G) do local w=win(r[1]) if w and w.workspace then id=w.workspace.id break end end '
    'local R={} for _,r in ipairs(G) do local w=win(r[1]) if tl(w) and w.workspace.id==id then R[#R+1]=r end end '
    'local function slots() local cur,inq,g={}, {}, {} for _,r in ipairs(R) do inq[r[1]]=true g[#g+1]=r[1] end '
    'for _,w in ipairs(hl.get_windows()) do if tl(w) and w.workspace and w.workspace.id==id then cur[#cur+1]={a=w.address,x=w.at.x,y=w.at.y} end end '
    'table.sort(cur,xy) for _,c in ipairs(cur) do if not inq[c.a] then g[#g+1]=c.a end end '
    'local pos={} for i,c in ipairs(cur) do pos[c.a]=i end for k,a in ipairs(g) do local b=cur[k] and cur[k].a '
    'if b and pos[a] and b~=a then D(hl.dsp.window.swap,{window="address:"..a,target="address:"..b}) '
    'local i=pos[a] cur[i],cur[k]=cur[k],cur[i] pos[b],pos[a]=i,k end end end '
    'local function col(a) local w=win(a) return w and w.layout and w.layout.column end '
    'local function fo(a) D(hl.dsp.focus,{window="address:"..a}) end local function M(m) D(hl.dsp.layout,m) end '
    'if id then slots() local ok,d=pcall(hl.get_config,"scrolling.direction") local h=not (ok and (d=="left" or d=="up" or d=="down")) '
    'if R[1] and col(R[1][1]) then if h then local bad=false for i=2,#R do local a,b=col(R[i-1][1]),col(R[i][1]) '
    'if (R[i][2]==R[i-1][2])~=((a and b and a.index==b.index) or false) then bad=true end end '
    'if bad then for _,r in ipairs(R) do local c=col(r[1]) local n=c and #c.windows or 1 if n>1 then fo(r[1]) for _=2,n do M("expel") end end end '
    'slots() local i=1 while i<=#R do local j=i while R[j+1] and R[j+1][2]==R[i][2] do j=j+1 end '
    'if j>i then fo(R[i][1]) for _=i+1,j do M("consume") end end i=j+1 end end '
    'local last for _,r in ipairs(R) do if r[2]~=last then last=r[2] local w,c=win(r[1]),col(r[1]) '
    'if w and c and r[3]>0 and w.size.x>0 and math.abs(w.size.x-r[3])>8 then fo(r[1]) '
    'M(string.format("colresize %.4f",math.max(0.05,math.min(1,c.width*r[3]/w.size.x)))) end end end end '
    'else for _,r in ipairs(R) do local w=win(r[1]) if w and r[3]>0 and r[4]>0 and (math.abs(w.size.x-r[3])>2 or math.abs(w.size.y-r[4])>2) then '
    'D(hl.dsp.window.resize,{x=r[3],y=r[4],window="address:"..r[1]}) end end end end ')
# Fullscreen, what each desktop shows, then focus and the cursor, which the steps above may have moved.
_LUA_END = (
    'local p=hl.get_cursor_pos() '
    'for _,e in ipairs(F) do D(hl.dsp.window.fullscreen_state,{internal=e[2],client=e[3],action="set",window="address:"..e[1]}) end '
    'for _,e in ipairs(A) do local w=win(e[2]) local x=w and w.workspace local m=hl.get_monitor(e[1]) '
    'if x and m and x.monitor and x.monitor.name==e[1] then pcall(function() m:set_workspace({workspace=x.name}) end) end end '
    'if P~="" and win(P) then D(hl.dsp.focus,{window="address:"..P}) elseif PM~="" and hl.get_monitor(PM) then D(hl.dsp.focus,{monitor=PM}) end ')


def _check(condition):
    if not condition: raise ValueError('invalid window placement')


def _address(value):
    return isinstance(value, str) and ADDRESS.match(value) is not None


def _output(value):
    return isinstance(value, str) and OUTPUT.match(value) is not None


def _validated(placement):
    _check(isinstance(placement, dict) and set(placement) == PLAN_KEYS)
    for wid, output, want in placement['whole']: _check(_int(wid, 1, 2147483647) and _output(output) and _int(want, 1, 2147483647))
    for address, output, want, moved in placement['windows']:
        _check(_address(address) and _output(output) and _int(want, 1, 2147483647) and moved in (0, 1) and type(moved) is int)
    for address, layout in placement['layouts']: _check(_address(address) and isinstance(layout, str) and LAYOUT.match(layout))
    for address, floating, x, y, w, h in placement['state']:
        _check(_address(address) and floating in (0, 1) and type(floating) is int and _int(x, -COORD, COORD) and _int(y, -COORD, COORD)
               and _int(w, 0, COORD) and _int(h, 0, COORD))
    for rows in placement['order']:
        for address, column, w, h in rows:
            _check(_address(address) and _int(column, 0, MAX_WINDOWS) and _int(w, 0, COORD) and _int(h, 0, COORD))
    for address, internal, client in placement['fullscreen']: _check(_address(address) and _int(internal, 0, 3) and _int(client, 0, 3))
    for output, address in placement['shown']: _check(_output(output) and _address(address))
    _check(placement['focus'] is None or _address(placement['focus']))
    _check(placement['monitor'] is None or (isinstance(placement['monitor'], str) and MONITOR.match(placement['monitor']) is not None))
    return placement


def _rows(rows):
    """Validated rows as a Lua table's contents."""
    return ','.join('{' + ','.join(f'"{v}"' if isinstance(v, str) else str(v) for v in row) + '}' for row in rows)


def _batches(groups, make):
    """Evals for `groups` (lists of rows that belong together), each under EVAL_LIMIT: a group goes whole into an eval while
    it fits one, and only one larger than that is cut."""
    out, part = [], []
    def fits(rows): return len(make(rows)) < EVAL_LIMIT
    for group in groups:
        if part and not fits(part + group):
            out.append(make(part)); part = []
        for row in group:
            if part and not fits(part + [row]):
                out.append(make(part)); part = []
            part.append(row)
    if part: out.append(make(part))
    return out


def _moves_eval(rows):
    whole = [row for kind, row in rows if kind == 'W']
    loose = [row for kind, row in rows if kind == 'C']
    return f'local C={{{_rows(loose)}}} local W={{{_rows(whole)}}} ' + _LUA_HEAD + _LUA_PICK + _LUA_WHOLE + _LUA_LOOSE


def moves_lua(placement):
    """The evals that put the windows on their workspaces, each under EVAL_LIMIT: workspaces first, so that loose windows join
    them. [] when nothing moves."""
    p = _validated(placement)
    groups, index = [[('W', row)] for row in p['whole']], {}
    for row in p['windows']:
        key = row[1:3]
        if key not in index:
            index[key] = len(groups)
            groups.append([])
        groups[index[key]].append(('C', row))
    return _batches(groups, _moves_eval)


def _state_eval(rows):
    layouts = [row for kind, row in rows if kind == 'L']
    state = [row for kind, row in rows if kind == 'S']
    return f'local L={{{_rows(layouts)}}} local S={{{_rows(state)}}} ' + _LUA_D + _LUA_STATE


def _order_eval(rows):
    return f'local G={{{_rows(rows)}}} ' + _LUA_D + _LUA_ORDER + _LUA_CURSOR


def _end_eval(rows, placement):
    fullscreen = [row for kind, row in rows if kind == 'F']
    last = any(kind == 'E' for kind, _ in rows)
    shown = _rows(placement['shown']) if last else ''
    focus, monitor = (placement['focus'] or '', placement['monitor'] or '') if last else ('', '')
    return (f'local F={{{_rows(fullscreen)}}} local A={{{shown}}} local P="{focus}" local PM="{monitor}" ' + _LUA_D + _LUA_END + _LUA_CURSOR)


def settle_lua(placement):
    """The evals after the moves: state, then one per workspace's tile order, then fullscreen, what each desktop shows and
    focus, in that order. Each under EVAL_LIMIT. A workspace with more tiled windows than one eval holds gets the first ones in
    order; the rest stay after them as Hyprland placed them. [] when nothing is left to do."""
    p = _validated(placement)
    out = _batches([[('L', row)] for row in p['layouts']] + [[('S', row)] for row in p['state']], _state_eval) if p['state'] or p['layouts'] else []
    for rows in p['order']:
        rows = list(rows)
        while rows and len(_order_eval(rows)) >= EVAL_LIMIT: rows.pop()
        if rows: out.append(_order_eval(rows))
    tail = [[('F', row)] for row in p['fullscreen']]
    if p['shown'] or p['focus'] or p['monitor']: tail.append([('E', None)])
    if tail: out += _batches(tail, lambda rows: _end_eval(rows, p))
    return out


class WindowTracker:
    """A run's window memory (module docstring): puts the windows back at its start, keeps the memory current, parks and
    restores the windows of a desktop removed and added again, and takes the last capture at its end.

    `client`: hyprland.HyprlandClient (query, place_windows, instance); `store`: Store; `fallback`: the run's physical monitor,
    where a Remove sends windows; `names`: the run's desktops. mark, poll and set_names run on the supervisor loop and never
    touch the compositor: its work is on one worker at a time. `threads=False` runs that work inline (tests)."""
    def __init__(self, client, store, fallback, names, *, clock=time.monotonic, threads=True):
        self.client, self.store, self.fallback, self.names = client, store, fallback, list(names)
        self.clock, self.threads = clock, threads
        self.instance = getattr(client, 'instance', '') or ''
        self.delay = CAPTURE_DELAY_S
        self.context = None      # optional () -> {name: {'placement', 'roll'}} (view.ViewControl.window_context)
        self.saved = None        # the memory as it is on disk
        self.parked = []         # records of windows waiting for their desktop (module docstring)
        self.parked_desktops = {}  # those desktops' context
        self.due = None          # monotonic time of the next capture
        self.home_due = False    # a desktop with parked windows is back
        self.closed = False
        self._lock = threading.Lock()  # one reader of the compositor and writer of the file at a time
        self._worker = None
        self._placement = None   # place()'s plan, for finish()
        self.captures = self.writes = self.failures = self.restored = 0  # diagnostics

    # --- the supervisor loop: no compositor calls ----------------------------------------------------------------------
    def mark(self, delay=None):
        """Something moved: capture soon. Marks inside the delay are one capture."""
        if self.due is None: self.due = self.clock() + (self.delay if delay is None else delay)

    def poll(self, now):
        if self.closed or self.due is None or now < self.due: return
        if self._worker is not None and self._worker.is_alive(): return  # still due: it goes next
        self.due = None
        if not self.threads:
            self._work()
            return
        self._worker = threading.Thread(target=self._work, daemon=True)
        self._worker.start()

    def set_names(self, names):
        """The run's desktops changed (view.ViewControl.desktop_listeners). One that came back brings its windows home."""
        new = set(names) - set(self.names)
        self.names = list(names)
        if any(r['output'] in new for r in self.parked):
            self.home_due = True
            self.mark(0)
        else: self.mark()

    def join(self, timeout=None):
        worker = self._worker
        if worker is not None: worker.join(timeout)

    # --- the run's start, before the renderer ----------------------------------------------------------------------------
    def _read(self, timeout=None):
        kw = {} if timeout is None else {'timeout': timeout}
        return tuple(self.client.query(kind, **kw) for kind in ('clients', 'workspaces', 'monitors'))

    def _active(self, monitors, timeout=None):
        window = self.client.query('activewindow', **({} if timeout is None else {'timeout': timeout}))
        focused = next((m.get('name') for m in monitors if isinstance(m, dict) and m.get('focused') is True), None)
        return {'address': window.get('address') if isinstance(window, dict) else None, 'monitor': focused}

    def place(self, names):
        """Run start, the desktops created, before adoption: the remembered windows onto their desktops (module docstring,
        steps 1 to 4). Never raises for compositor or file errors: adoption then takes them as it always has."""
        self.names = list(names)
        try:
            memory = self.saved = self.store.load()
            if memory is None: return
            clients, workspaces, monitors = self._read()
            placement = plan(memory, clients, workspaces, monitors, names, self.fallback, instance=self.instance,
                             active=self._active(monitors))
            self.parked_desktops = dict(memory['desktops'])
            if empty(placement): return
            self.client.place_windows(placement, 'moves')
            self._placement = placement
        except FAILURES as exc:
            log(f'run: could not put the remembered windows back: {exc}')

    def finish(self):
        """After adoption: everything that needs every window in place (module docstring, step 6), then the first capture."""
        placement, self._placement = self._placement, None
        if placement is not None:
            self.parked = [{**p['record'], 'address': p['address'], 'parked': True, 'landing': p['output']} for p in placement['placed'] if p['moved']]
            self.restored += len(placement['placed'])
            try:
                self.client.place_windows(placement, 'settle')
                log(f"run: {len(placement['placed'])} remembered window{'s' if len(placement['placed']) != 1 else ''} back on their desktops")
            except FAILURES as exc:
                log(f'run: could not settle the remembered windows: {exc}')
        self.mark()

    # --- the worker ------------------------------------------------------------------------------------------------------
    def _work(self):
        with self._lock:
            if self.closed: return
            home, self.home_due = self.home_due, False
            if home: self._come_home()
            self._capture()

    def _view(self):
        """The view's context for each desktop; a failure only loses the context."""
        if self.context is None: return None
        try: return self.context()
        except Exception: return None

    def _capture(self, names=None, timeout=QUERY_TIMEOUT_S):
        """Read Hyprland and save the memory if it changed. The memory read, or None if it could not be read."""
        self.captures += 1
        try:
            clients, workspaces, monitors = self._read(timeout)
            fresh = capture(clients, workspaces, monitors, names or self.names, self.instance, self._view())
            monitor_of = {w.get('id'): w.get('monitor') for w in workspaces if isinstance(w, dict)}
            where = {c.get('address'): monitor_of.get(c['workspace'].get('id')) for c in clients
                     if isinstance(c, dict) and isinstance(c.get('workspace'), dict)}
            # A parked window stays parked only where the removal put it: anywhere else it was moved on purpose.
            self.parked = [r for r in self.parked if where.get(r['address']) == r['landing']]
            waiting = {r['address'] for r in self.parked}
            windows = [w for w in fresh['windows'] if w['address'] not in waiting] + self.parked
            desktops = {**{r['output']: self.parked_desktops[r['output']] for r in self.parked if r['output'] in self.parked_desktops}, **fresh['desktops']}
            memory = validate({**fresh, 'desktops': desktops, 'windows': windows[:MAX_WINDOWS]})
        except FAILURES as exc:
            self.failures += 1
            log(f'run: could not read the windows for the window memory: {exc}')
            return None
        if memory['windows'] and memory != self.saved:  # an empty capture never replaces a memory (module docstring)
            try:
                self.store.save(memory)
                self.saved = memory
                self.writes += 1
            except FAILURES as exc:
                self.failures += 1
                log(f'run: could not save the window memory: {exc}')
        return memory

    def _come_home(self):
        """A desktop with parked windows exists again: those very windows go back (module docstring). `exact`: by address, so a
        window that has closed can never pull in another."""
        for landing in sorted({r['landing'] for r in self.parked if r['output'] in self.names}):
            memory = {'instance': self.instance, 'desktops': self.parked_desktops,
                      'windows': [r for r in self.parked if r['output'] in self.names and r['landing'] == landing]}
            try:
                clients, workspaces, monitors = self._read(QUERY_TIMEOUT_S)
                placement = plan(memory, clients, workspaces, monitors, self.names, landing, instance=self.instance,
                                 active=self._active(monitors, QUERY_TIMEOUT_S), exact=True)
                if empty(placement): continue
                self.client.place_windows(placement, 'moves')
                self.client.place_windows(placement, 'settle')
                self.restored += len(placement['placed'])
            except FAILURES as exc:
                self.failures += 1
                log(f'run: could not bring the windows back to their desktop: {exc}')

    def leaving(self, name, target):
        """A desktop is about to go (desktops.DesktopManager, on its worker): its windows' memory is parked. `target`: where
        they go, or None for the fallback. Never raises: the removal goes ahead whatever happens here."""
        landing = target or self.fallback
        try:
            with self._lock:
                memory = self._capture(sorted(set(self.names) | {name}, key=_number))
                source = memory if memory is not None else self.saved
                if source is None: return
                waiting = {r['address'] for r in self.parked}
                self.parked += [{**r, 'index': None, 'parked': True, 'landing': landing} for r in source['windows']
                                if r['output'] == name and not r['parked'] and r['address'] not in waiting]
                if name in source['desktops']: self.parked_desktops[name] = source['desktops'][name]
        except FAILURES as exc:
            self.failures += 1
            log(f'run: could not keep the memory of {name}: {exc}')

    # --- the run's end ---------------------------------------------------------------------------------------------------
    def close(self, timeout=2.0):
        """Run end, before recovery: the last capture, while the XR outputs still hold the windows. Nothing runs after it."""
        if self.closed: return
        self.join(timeout)
        with self._lock:
            self.closed = True
            self._capture()


def verify_lua():
    """For bind_check: every generated eval on a sample plan, and each dispatcher they call with the arguments they give it, so
    that Hyprland's own loader and argument checks see them (`--verify-config`; nothing is dispatched there)."""
    a, b = '0x1', '0x2'
    sample = {'whole': [(1, 'XR-1', 61)], 'windows': [(a, 'XR-2', 71, 0), (b, 'XR-3', 81, 1)], 'layouts': [(a, 'scrolling')],
              'state': [(a, 1, 10, 20, 300, 400), (b, 0, 0, 0, 0, 0)], 'order': [[(a, 0, 300, 400), (b, 1, 300, 0)]],
              'fullscreen': [(a, 2, 2)], 'shown': [('XR-1', a)], 'placed': [], 'focus': a, 'monitor': 'DP-1'}
    w = f'window="address:{a}"'
    calls = ('hl.dsp.layout("consume")', 'hl.dsp.layout("expel")', 'hl.dsp.layout("colresize 0.5000")', f'hl.dsp.window.swap({{{w},target="address:{b}"}})',
             f'hl.dsp.window.float({{action="on",{w}}})', f'hl.dsp.window.resize({{x=300,y=400,{w}}})', f'hl.dsp.window.move({{x=10,y=20,{w}}})',
             f'hl.dsp.window.move({{workspace="61",follow=false,{w}}})', f'hl.dsp.window.fullscreen_state({{internal=2,client=2,action="set",{w}}})',
             'hl.dsp.workspace.move({workspace="61",monitor="XR-1"})', 'hl.dsp.workspace.change_id({workspace="61",id=62})',
             f'hl.dsp.focus({{{w}}})', 'hl.dsp.focus({monitor="XR-1"})', 'hl.dsp.cursor.move({x=1,y=2})')
    return moves_lua(sample) + settle_lua(sample) + [' '.join(f'assert({c})' for c in calls)]


def tracker_for(client, store, fallback, names):
    """The run's WindowTracker, or None for a run with no glasses profile (the CLI's layout.json) or no way to move windows."""
    if not isinstance(store, ProfileStore) or not hasattr(client, 'place_windows'): return None
    return WindowTracker(client, Store(memory_path(store.path), store.model), fallback, names)
