"""The window memory's generated evals, run for real in a Lua interpreter against a stand-in for Hyprland's API.

The stand-in follows Hyprland 0.56.2's source where it matters here, read at efb5099: a workspace's layout is its own and
holds columns of windows (master and dwindle: one window a column, a window's position is its slot); a new window gets a
column of its own; window.swap exchanges two windows in their slots, so a column keeps its width and its height; `expel`,
`consume` and `colresize` act on the focused window and refuse unless its workspace is the one the focused monitor shows;
window.resize shrinks a scrolling column off the view, as CScrollingAlgorithm::resizeTarget clamps it; a fullscreen window
cannot be swapped, moved or resized. Chunks run in turn on one state, and the result is the state as Hyprland's j/ replies
report it, so a capture can be taken from it. No test here is a glasses pass."""
import json
import shutil
import subprocess
import unittest

from tools.native_spike import adopt, restore, window_memory as wm

HARNESS = r'''
local state = STATE
local ARR = {}
local function arr(t) return setmetatable(t, ARR) end
local mons, order, spaces, wins, log = {}, {}, {}, {}, {}
local focus = { monitor = state.focus_monitor, window = state.focus }
local cursor = { x = 11, y = 22 }
local DEFAULT = state.default_layout or "master"
for _, m in ipairs(state.monitors) do
  mons[m.name] = { name = m.name, active = m.active, block = m.block, x = m.x, y = m.y or 0, width = m.width or 1920, height = m.height or 1200,
                   scale = m.scale or 1 }
  order[#order + 1] = m.name
end
local function create(id, mon, layout)
  spaces[id] = { mon = mon, cols = {}, layout = layout or (state.rules and state.rules[tostring(id)]) or DEFAULT }
  log[#log + 1] = "create " .. id .. " " .. mon
end
for _, s in ipairs(state.workspaces) do
  spaces[s.id] = { mon = s.mon, cols = {}, layout = s.layout or DEFAULT }
  for _, c in ipairs(s.cols or {}) do
    local col = { w = c.w or 0.5, list = {} }
    for _, a in ipairs(c.list) do col.list[#col.list + 1] = a end
    spaces[s.id].cols[#spaces[s.id].cols + 1] = col
  end
end
for _, w in ipairs(state.windows) do
  wins[w.address] = { ws = w.ws, floating = w.floating or false, x = w.x or 0, y = w.y or 0, w = w.w or 100, h = w.h or 100,
                      fs = w.fs or 0, fc = w.fc or w.fs or 0, pinned = w.pinned or false, class = w.class or "kitty",
                      title = w.title or "term", initial = w.initial or w.title or "term", pid = w.pid or 100 }
end
local function locate(a)
  local w = wins[a]; local s = spaces[w.ws]
  for i, c in ipairs(s.cols) do for j, x in ipairs(c.list) do if x == a then return i, j, c end end end
end
local function count(id) local n = 0; for _, w in pairs(wins) do if w.ws == id then n = n + 1 end end; return n end
local function untile(a)
  local i, j, c = locate(a); if not i then return end
  table.remove(c.list, j); if #c.list == 0 then table.remove(spaces[wins[a].ws].cols, i) end
end
local function tile(a)
  local s = spaces[wins[a].ws]
  s.cols[#s.cols + 1] = { w = s.layout == "scrolling" and 0.5 or 1, list = { a } }
end
local function pos(a)
  local w = wins[a]; local m = mons[spaces[w.ws].mon]
  if w.fs ~= 0 then return m.x, m.y, m.width, m.height end
  if w.floating then return w.x, w.y, w.w, w.h end
  local s = spaces[w.ws]; local i, j, c = locate(a); local x = m.x
  if s.layout == "scrolling" then
    for k = 1, i - 1 do x = x + math.floor(s.cols[k].w * m.width) end
    return x, m.y + 26 + 100 * (j - 1), math.floor(c.w * m.width), math.floor((m.height - 26) / #c.list)
  end
  if w.tw then return m.x + 100 * (i - 1), m.y + 26, w.tw, w.th end
  return m.x + 100 * (i - 1), m.y + 26, 100, 100
end
local wsobj, monobj, winobj
function monobj(name)
  local m = mons[name]; if not m then return nil end
  return { name = m.name, active_workspace = m.active and spaces[m.active] and wsobj(m.active, true) or nil,
           set_workspace = function(self, a) m.active = tonumber(a.workspace); log[#log + 1] = "show " .. a.workspace .. " " .. name end }
end
function wsobj(id, shallow)
  local s = spaces[id]; if not s then return nil end
  local m = mons[s.mon]
  return { id = id, name = tostring(id), windows = count(id), special = false, tiled_layout = s.layout,
           monitor = m and (shallow and { name = m.name, active_workspace = { id = m.active } } or monobj(m.name)) or nil }
end
function winobj(a)
  local w = wins[a]; if not w then return nil end
  local x, y, sw, sh = pos(a)
  local o = { address = a, workspace = wsobj(w.ws, true), floating = w.floating, at = { x = x, y = y }, size = { x = sw, y = sh },
              fullscreen = w.fs, fullscreen_client = w.fc, pinned = w.pinned, group = nil }
  local s = spaces[w.ws]
  if s.layout == "scrolling" and not w.floating then
    local i, j, c = locate(a)
    if i then
      local list = {}; for _, x2 in ipairs(c.list) do list[#list + 1] = { address = x2 } end
      o.layout = { name = "scrolling", column = { index = i - 1, width = c.w, windows = list }, index_in_column = j - 1 }
    end
  elseif not w.floating then o.layout = { name = s.layout } end
  return o
end
local function addr(sel) assert(type(sel) == "string" and sel:sub(1, 8) == "address:", "bad window selector"); return sel:sub(9) end
local function need(a) local w = wins[a]; if not w then error("window not found") end; return w end
hl = { dsp = { workspace = {}, window = {}, cursor = {} } }
function hl.get_workspace(id) return wsobj(tonumber(id)) end
function hl.get_workspaces() local r = {}; for id in pairs(spaces) do r[#r + 1] = wsobj(id, true) end; return r end
function hl.get_monitor(name) return monobj(name) end
function hl.get_window(sel) return winobj(addr(sel)) end
function hl.get_windows() local r = {}; for a in pairs(wins) do r[#r + 1] = winobj(a) end; return r end
function hl.get_cursor_pos() return { x = cursor.x, y = cursor.y } end
function hl.get_config(key) if key == "scrolling.direction" then return state.direction end end
function hl.dispatch(f) f() end
function hl.workspace_rule(a)
  local s = spaces[tonumber(a.workspace)]
  if s then s.layout = a.layout; log[#log + 1] = "rule " .. a.workspace .. " " .. a.layout end
end
function hl.dsp.cursor.move(a) return function() cursor.x, cursor.y = a.x, a.y end end
function hl.dsp.focus(a) return function()
  if a.monitor then assert(mons[a.monitor], "no monitor"); focus.monitor = a.monitor; focus.window = nil
  elseif a.window then
    local w = need(addr(a.window)); focus.window = addr(a.window); focus.monitor = spaces[w.ws].mon; mons[focus.monitor].active = w.ws
    cursor.x, cursor.y = 9999, 9999; log[#log + 1] = "focus " .. addr(a.window)
  elseif a.workspace then
    local id = tonumber(a.workspace); if not spaces[id] then create(id, focus.monitor) end
    focus.monitor = spaces[id].mon; mons[focus.monitor].active = id; focus.window = nil end end end
function hl.dsp.workspace.change_id(a) return function()
  local id = tonumber(a.workspace); assert(spaces[id], "no workspace " .. id); assert(not spaces[a.id], "id taken " .. a.id)
  spaces[a.id] = spaces[id]; spaces[id] = nil
  for _, w in pairs(wins) do if w.ws == id then w.ws = a.id end end
  for _, m in pairs(mons) do if m.active == id then m.active = a.id end end
  log[#log + 1] = "renumber " .. id .. " " .. a.id end end
function hl.dsp.workspace.move(a) return function()
  local id = tonumber(a.workspace); local s = spaces[id]; assert(s, "no workspace"); assert(mons[a.monitor], "no monitor")
  local old = mons[s.mon]; s.mon = a.monitor; mons[a.monitor].active = id
  if old.active == id and old.name ~= a.monitor then
    old.active = nil
    for k, v in pairs(spaces) do if v.mon == old.name and (not old.active or k < old.active) then old.active = k end end
    if not old.active then local n = 1; while spaces[n] do n = n + 1 end; create(n, old.name); old.active = n end
  end
  log[#log + 1] = "move " .. id .. " " .. a.monitor end end
function hl.dsp.window.move(a) return function()
  local w = need(addr(a.window))
  if a.workspace then
    assert(a.follow == false, "a restore never follows")
    local id = tonumber(a.workspace); if not spaces[id] then create(id, mons[spaces[w.ws].mon].name) end
    if w.ws == id then return end
    if not w.floating then untile(addr(a.window)) end
    w.ws = id; if not w.floating then tile(addr(a.window)) end
    log[#log + 1] = "window " .. addr(a.window) .. " " .. id
  else
    if w.fs ~= 0 then error("Window is fullscreen") end
    assert(w.floating, "moving a tiled window"); w.x, w.y = a.x, a.y
  end end end
function hl.dsp.window.resize(a) return function()
  local w = need(addr(a.window)); if w.fs ~= 0 then error("Window is fullscreen") end
  if w.floating then w.w, w.h = a.x, a.y; return end
  local s = spaces[w.ws]
  log[#log + 1] = "resize " .. addr(a.window) .. " " .. a.x .. " " .. a.y
  if s.layout == "scrolling" then
    local _, _, c = locate(addr(a.window)); c.w = 0.05; return   -- CScrollingAlgorithm: a column off the view shrinks
  end
  w.tw, w.th = a.x, a.y end end
function hl.dsp.window.swap(a) return function()
  local p, q = addr(a.window), addr(a.target); local wp, wq = need(p), need(q)
  if wp.fs ~= 0 or wq.fs ~= 0 then error("Can't swap fullscreen window") end
  assert(wp.ws == wq.ws, "swap across workspaces")
  local i, j, c = locate(p); local k, l, d = locate(q); c.list[j], d.list[l] = q, p
  cursor.x, cursor.y = 9999, 9999
  log[#log + 1] = "swap " .. p .. " " .. q end end
function hl.dsp.window.float(a) return function()
  local p = addr(a.window); local w = need(p); local on = a.action == "on"
  if w.floating == on then return end
  if on then untile(p); w.floating = true else w.floating = false; tile(p) end end end
function hl.dsp.window.fullscreen_state(a) return function()
  local w = need(addr(a.window)); assert(a.action == "set"); w.fs, w.fc = a.internal, a.client end end
function hl.dsp.layout(msg) return function()
  local m = mons[focus.monitor]; local s = spaces[m.active]; local a = focus.window
  assert(a and wins[a] and wins[a].ws == m.active, "a layout message goes to the focused monitor's shown workspace")
  local i, j, c = locate(a); assert(i, "no target")
  local cmd, arg = msg:match("^(%S+)%s*(%S*)$")
  log[#log + 1] = "layout " .. msg
  if cmd == "expel" then
    assert(#c.list > 1, "column has only one window")
    local last = table.remove(c.list); table.insert(s.cols, i + 1, { w = 0.5, list = { last } })
  elseif cmd == "consume" then
    local nxt = s.cols[i + 1]; assert(nxt, "no next column")
    c.list[#c.list + 1] = table.remove(nxt.list, 1); if #nxt.list == 0 then table.remove(s.cols, i + 1) end
  elseif cmd == "colresize" then
    local f = tonumber(arg); assert(f and f >= 0.05 and f <= 1, "bad width " .. tostring(arg)); c.w = f
  else error("unknown layout message " .. msg) end end end
if state.omarchy then
  o = { workspaces = { workspace_id = function(m, slot) return mons[m.name].block * 10 + slot end } }
end
CHUNKS
local function dump()
  local r = { clients = arr({}), workspaces = arr({}), monitors = arr({}), activewindow = {}, columns = {}, log = arr(log),
              cursor = arr({ cursor.x, cursor.y }) }
  local addresses = {}; for a in pairs(wins) do addresses[#addresses + 1] = a end; table.sort(addresses)
  for _, a in ipairs(addresses) do
    local w = wins[a]; local x, y, sw, sh = pos(a)
    r.clients[#r.clients + 1] = { address = a, workspace = { id = w.ws, name = tostring(w.ws) }, at = arr({ x, y }), size = arr({ sw, sh }),
      floating = w.floating, pinned = w.pinned, fullscreen = w.fs, fullscreenClient = w.fc, class = w.class, title = w.title,
      initialTitle = w.initial, pid = w.pid, mapped = true, hidden = false, grouped = arr({}) }
  end
  local ids = {}; for id in pairs(spaces) do ids[#ids + 1] = id end; table.sort(ids)
  for _, id in ipairs(ids) do
    r.workspaces[#r.workspaces + 1] = { id = id, name = tostring(id), monitor = spaces[id].mon, windows = count(id), tiledLayout = spaces[id].layout }
    local cols = arr({})
    for _, c in ipairs(spaces[id].cols) do cols[#cols + 1] = { w = c.w, list = arr({ table.unpack(c.list) }) } end
    r.columns[tostring(id)] = cols
  end
  for _, name in ipairs(order) do
    local m = mons[name]
    r.monitors[#r.monitors + 1] = { name = name, id = 0, x = m.x, y = m.y, width = m.width, height = m.height, scale = m.scale,
      reserved = arr({ 0, 26, 0, 0 }), activeWorkspace = { id = m.active or -1, name = tostring(m.active) }, focused = focus.monitor == name }
  end
  if focus.window and wins[focus.window] then r.activewindow = { address = focus.window } end
  return r
end
local function enc(v)
  if type(v) == "table" then
    local parts = {}
    if getmetatable(v) == ARR then for _, x in ipairs(v) do parts[#parts + 1] = enc(x) end; return "[" .. table.concat(parts, ",") .. "]" end
    for k, x in pairs(v) do parts[#parts + 1] = string.format("%q", tostring(k)) .. ":" .. enc(x) end
    return "{" .. table.concat(parts, ",") .. "}"
  elseif type(v) == "string" then return string.format("%q", v) else return tostring(v) end
end
print(enc(dump()))
'''


def lua_table(value):
    if value is None: return 'nil'
    if isinstance(value, bool): return 'true' if value else 'false'
    if isinstance(value, (int, float)): return repr(value)
    if isinstance(value, str): return json.dumps(value)
    if isinstance(value, list): return '{' + ','.join(lua_table(v) for v in value) + '}'
    return '{' + ','.join(f'[{json.dumps(k)}]={lua_table(v)}' for k, v in value.items()) + '}'


LUA = shutil.which('lua5.4') or shutil.which('lua')
XR3 = ['XR-1', 'XR-2', 'XR-3']


def run(state, *chunks):
    """Run `chunks` in turn on `state`: what Hyprland's j/ replies would say afterwards. An error in a chunk fails the test,
    unless it is inside a pcall, as in the compositor."""
    code = '\n'.join(f'do\n{c}\nend' for c in chunks)
    script = HARNESS.replace('STATE', lua_table(state), 1).replace('CHUNKS', code, 1)
    done = subprocess.run([LUA, '-'], input=script, capture_output=True, text=True, timeout=10)
    if done.returncode != 0: raise AssertionError(done.stderr[-2000:])
    return json.loads(done.stdout)


def world(omarchy=True, focus='0xa', windows=(), workspaces=(), rules=None, default_layout='master', xr=3):
    state = {'omarchy': omarchy, 'focus': focus, 'focus_monitor': 'DP-1', 'default_layout': default_layout,
             'monitors': [{'name': 'DP-1', 'active': 1, 'block': 0, 'x': 0}] +
                         [{'name': f'XR-{n}', 'active': 50 + 10 * n + 1, 'block': 5 + n, 'x': 2000 * n} for n in range(1, xr + 1)],
             'workspaces': [{'id': 50 + 10 * n + 1, 'mon': f'XR-{n}'} for n in range(1, xr + 1)] + list(workspaces),
             'windows': list(windows)}
    if rules: state['rules'] = rules
    return state


def physical(*spaces, layout='master'):
    """DP-1's workspaces: (id, [addresses]). Master, one column each, in that order."""
    return [{'id': i, 'mon': 'DP-1', 'layout': layout, 'cols': [{'w': 1, 'list': [a]} for a in addresses]} for i, addresses in spaces]


def wins(*addresses, **common):
    return [{'address': a, 'ws': w, **common} for w, a in addresses]


def on(result, address):
    """The workspace id a window is on."""
    return next(c['workspace']['id'] for c in result['clients'] if c['address'] == address)


def monitor_of(result, workspace):
    return next(w['monitor'] for w in result['workspaces'] if w['id'] == workspace)


def tiled(result, workspace):
    return [c['list'] for c in result['columns'][str(workspace)]]


def client(result, address):
    return next(c for c in result['clients'] if c['address'] == address)


def apply(state, mem, source='DP-1', names=XR3, active=None, exact=False):
    """A start's plan on `state`, and its two steps of evals."""
    before = run(state)
    p = wm.plan(mem, before['clients'], before['workspaces'], before['monitors'], names, source, instance='inst', active=active, exact=exact)
    return p, wm.moves_lua(p), wm.settle_lua(p)


def from_records(*records, desktops=None):
    return {'instance': 'inst', 'windows': list(records), 'desktops': desktops if desktops is not None else {
        n: {'width': 1920, 'height': 1200, 'scale': 1.0, 'active': None, 'placement': None, 'roll': None} for n in XR3 + ['XR-4']}}


def rec(address, output='XR-1', workspace=61, order=0, column=None, layout='master', floating=False, at=(0, 26), size=(100, 100), fullscreen=0,
        client=0, parked=False, landing=None, index=None, cls='kitty', title='term'):
    index = int(output[3:]) - 1 if index is None and not parked else index
    return {'address': address, 'class': cls, 'title': title, 'initial_title': title, 'pid': 100, 'output': output, 'index': index,
            'workspace': workspace, 'layout': layout, 'floating': floating, 'at': list(at), 'size': list(size), 'fullscreen': fullscreen,
            'client': client, 'order': None if floating or fullscreen else order, 'column': (order if column is None else column) if not floating and not fullscreen else None,
            'parked': parked, 'landing': landing}


@unittest.skipUnless(LUA, 'no Lua interpreter')
class MoveTests(unittest.TestCase):
    def test_a_whole_workspace_takes_its_remembered_id_on_its_desktop(self):
        state = world(windows=wins((1, '0xa'), (1, '0xb')), workspaces=physical((1, ['0xa', '0xb'])))
        p, moves, settle = apply(state, from_records(rec('0xa', 'XR-2', 73), rec('0xb', 'XR-2', 73, order=1)))
        r = run(state, *moves, *settle)
        self.assertEqual(on(r, '0xa'), 73)
        self.assertEqual(monitor_of(r, 73), 'XR-2')
        self.assertEqual(tiled(r, 73), [['0xa'], ['0xb']])

    def test_a_whole_workspace_needs_no_focus_and_no_swaps(self):
        state = world(windows=wins((1, '0xa'), (1, '0xb')), workspaces=physical((1, ['0xa', '0xb'])))
        p, moves, settle = apply(state, from_records(rec('0xa', 'XR-2', 73), rec('0xb', 'XR-2', 73, order=1)))
        r = run(state, *moves, *settle)
        self.assertFalse([e for e in r['log'] if e.startswith(('focus', 'swap', 'layout', 'resize'))], r['log'])

    def test_an_empty_workspace_on_that_id_steps_aside(self):
        state = world(windows=wins((1, '0xa')), workspaces=physical((1, ['0xa'])))
        p, moves, settle = apply(state, from_records(rec('0xa', 'XR-1', 61)))
        r = run(state, *moves, *settle)
        self.assertEqual(on(r, '0xa'), 61)
        self.assertEqual(monitor_of(r, 61), 'XR-1')

    def test_a_loose_window_lands_on_a_workspace_made_on_its_desktop(self):
        windows = wins((1, '0xa'), (1, '0xb')); windows[1]['class'] = 'other'  # 0xb is unmatched
        state = world(windows=windows, workspaces=physical((1, ['0xa', '0xb'])))
        p, moves, settle = apply(state, from_records(rec('0xa', 'XR-3', 84)))
        r = run(state, *moves, *settle)
        self.assertEqual(on(r, '0xa'), 84)
        self.assertEqual(monitor_of(r, 84), 'XR-3')
        self.assertEqual(on(r, '0xb'), 1)  # left for adoption

    def test_two_windows_for_one_new_workspace_share_it(self):
        windows = wins((1, '0xa'), (1, '0xb'), (1, '0xc')); windows[2]['class'] = 'other'
        state = world(windows=windows, workspaces=physical((1, ['0xa', '0xb', '0xc'])))
        p, moves, settle = apply(state, from_records(rec('0xa', 'XR-3', 84), rec('0xb', 'XR-3', 84, order=1)))
        r = run(state, *moves, *settle)
        self.assertEqual({on(r, '0xa'), on(r, '0xb')}, {84})

    def test_a_workspace_on_another_monitor_is_not_stolen(self):
        # No Omarchy: XR-2's remembered id 2 is the physical workspace still holding a window that stays.
        state = world(omarchy=False, windows=wins((2, '0xa'), (2, '0xb')) + [{'address': '0xc', 'ws': 2, 'pinned': True}],
                      workspaces=physical((2, ['0xa', '0xb', '0xc'])))
        p, moves, settle = apply(state, from_records(rec('0xa', 'XR-2', 2)))
        r = run(state, *moves, *settle)
        self.assertEqual(monitor_of(r, 2), 'DP-1')
        self.assertEqual(monitor_of(r, on(r, '0xa')), 'XR-2')

    def test_without_omarchy_a_free_id_is_kept(self):
        windows = wins((1, '0xa'), (1, '0xb')); windows[1]['class'] = 'other'
        state = world(omarchy=False, windows=windows, workspaces=physical((1, ['0xa', '0xb'])))
        p, moves, settle = apply(state, from_records(rec('0xa', 'XR-2', 7)))
        r = run(state, *moves, *settle)
        self.assertEqual(on(r, '0xa'), 7)
        self.assertEqual(monitor_of(r, 7), 'XR-2')

    def test_a_missing_desktops_workspace_keeps_its_slot_on_the_last_one(self):
        state = world(windows=wins((1, '0xa')), workspaces=physical((1, ['0xa'])))
        p, moves, settle = apply(state, from_records(rec('0xa', 'XR-4', 92, index=3)))
        r = run(state, *moves, *settle)
        self.assertEqual(on(r, '0xa'), 82)  # XR-3's block, the same slot
        self.assertEqual(monitor_of(r, 82), 'XR-3')

    def test_a_missing_desktop_never_merges_into_a_workspace_with_windows(self):
        state = world(windows=wins((1, '0xa'), (2, '0xb')), workspaces=physical((1, ['0xa']), (2, ['0xb'])))
        p, moves, settle = apply(state, from_records(rec('0xa', 'XR-3', 82), rec('0xb', 'XR-4', 92, index=3)))
        r = run(state, *moves, *settle)
        self.assertEqual(on(r, '0xa'), 82)
        self.assertEqual(monitor_of(r, on(r, '0xb')), 'XR-3')
        self.assertNotEqual(on(r, '0xb'), 82)

    def test_loose_windows_from_a_missing_desktop_stay_together_and_apart(self):
        windows = wins((1, '0xa'), (2, '0xb'), (2, '0xc'), (2, '0xd')); windows[3]['class'] = 'other'
        state = world(windows=windows, workspaces=physical((1, ['0xa']), (2, ['0xb', '0xc', '0xd'])))
        p, moves, settle = apply(state, from_records(rec('0xa', 'XR-3', 82), rec('0xb', 'XR-4', 92, index=3), rec('0xc', 'XR-4', 92, order=1, index=3)))
        r = run(state, *moves, *settle)
        self.assertEqual(on(r, '0xb'), on(r, '0xc'))
        self.assertNotEqual(on(r, '0xb'), on(r, '0xa'))
        self.assertEqual(monitor_of(r, on(r, '0xb')), 'XR-3')

    def test_a_refused_move_leaves_the_rest_to_run(self):
        state = world(windows=wins((1, '0xb')), workspaces=physical((1, ['0xb'])))
        p = wm.plan(from_records(rec('0xb', 'XR-1', 61)), run(state)['clients'], run(state)['workspaces'], run(state)['monitors'], XR3, 'DP-1')
        p['windows'] = [('0xdead', 'XR-2', 71, 0)] + p['windows']
        r = run(state, *wm.moves_lua(p))
        self.assertEqual(on(r, '0xb'), 61)


@unittest.skipUnless(LUA, 'no Lua interpreter')
class SettleTests(unittest.TestCase):
    def test_a_floating_window_gets_its_place_and_size(self):
        state = world(windows=wins((1, '0xa')), workspaces=physical((1, ['0xa'])))
        r = run(state)
        mem = from_records(rec('0xa', 'XR-2', 72, floating=True, at=(40, 60), size=(500, 400)))
        p = wm.plan(mem, r['clients'], r['workspaces'], r['monitors'], XR3, 'DP-1')
        c = client(run(state, *wm.moves_lua(p), *wm.settle_lua(p)), '0xa')
        self.assertTrue(c['floating'])
        self.assertEqual((c['at'], c['size']), ([4040, 60], [500, 400]))

    def test_a_window_floated_since_goes_back_into_the_tiles(self):
        state = world(windows=wins((1, '0xa'), (1, '0xb')) , workspaces=physical((1, ['0xb'])))
        state['windows'][0].update(floating=True)
        r = run(state)
        mem = from_records(rec('0xa', 'XR-1', 61, order=0), rec('0xb', 'XR-1', 61, order=1))
        p = wm.plan(mem, r['clients'], r['workspaces'], r['monitors'], XR3, 'DP-1')
        r = run(state, *wm.moves_lua(p), *wm.settle_lua(p))
        self.assertFalse(client(r, '0xa')['floating'])
        self.assertEqual(tiled(r, 61), [['0xa'], ['0xb']])

    def test_fullscreen_comes_back_after_the_order(self):
        state = world(windows=wins((1, '0xa'), (1, '0xb'), (1, '0xc')), workspaces=physical((1, ['0xa', '0xb', '0xc'])))
        r = run(state)
        mem = from_records(rec('0xb', 'XR-1', 61, order=0), rec('0xc', 'XR-1', 61, order=1), rec('0xa', 'XR-1', 61, fullscreen=2, client=2))
        p = wm.plan(mem, r['clients'], r['workspaces'], r['monitors'], XR3, 'DP-1', instance='inst')
        r = run(state, *wm.moves_lua(p), *wm.settle_lua(p))
        self.assertEqual((client(r, '0xa')['fullscreen'], client(r, '0xa')['fullscreenClient']), (2, 2))

    def test_fullscreen_goes_off_for_a_window_that_was_not(self):
        state = world(windows=[{'address': '0xa', 'ws': 1, 'fs': 1}], workspaces=physical((1, ['0xa'])))
        r = run(state)
        p = wm.plan(from_records(rec('0xa', 'XR-1', 61)), r['clients'], r['workspaces'], r['monitors'], XR3, 'DP-1')
        self.assertEqual(client(run(state, *wm.moves_lua(p), *wm.settle_lua(p)), '0xa')['fullscreen'], 0)

    def test_master_order_comes_back_by_swaps(self):
        state = world(windows=wins((1, '0xa'), (1, '0xb'), (1, '0xc')), workspaces=physical((1, ['0xa', '0xb', '0xc'])))
        mem = from_records(rec('0xc', 'XR-1', 61, order=0), rec('0xa', 'XR-1', 61, order=1), rec('0xb', 'XR-1', 61, order=2))
        p, moves, settle = apply(state, mem)
        r = run(state, *moves, *settle)
        self.assertEqual(tiled(r, 61), [['0xc'], ['0xa'], ['0xb']])

    def test_a_window_the_memory_does_not_know_keeps_after_the_known_ones(self):
        state = world(windows=wins((1, '0xa'), (1, '0xb'), (2, '0xc')), workspaces=physical((1, ['0xa', '0xb']), (2, ['0xc'])))
        state['windows'][2]['class'] = 'other'
        mem = from_records(rec('0xb', 'XR-1', 61, order=0), rec('0xa', 'XR-1', 61, order=1))
        p, moves, settle = apply(state, mem)
        extra = 'hl.dispatch(hl.dsp.window.move({workspace="61",follow=false,window="address:0xc"}))'
        r = run(state, *moves, extra, *settle)
        self.assertEqual(tiled(r, 61), [['0xb'], ['0xa'], ['0xc']])

    def test_scrolling_columns_are_rebuilt(self):
        windows = wins((1, '0xa'), (2, '0xb'), (3, '0xc'), (4, '0xd'))
        state = world(windows=windows, workspaces=physical((1, ['0xa']), (2, ['0xb']), (3, ['0xc']), (4, ['0xd'])), default_layout='scrolling')
        mem = from_records(rec('0xc', 'XR-1', 61, order=0, column=0, layout='scrolling'), rec('0xa', 'XR-1', 61, order=1, column=0, layout='scrolling'),
                           rec('0xd', 'XR-1', 61, order=2, column=1, layout='scrolling'), rec('0xb', 'XR-1', 61, order=3, column=2, layout='scrolling'))
        p, moves, settle = apply(state, mem)
        r = run(state, *moves, *settle)
        self.assertEqual(tiled(r, 61), [['0xc', '0xa'], ['0xd'], ['0xb']])

    def stacked(self, direction):
        windows = wins((1, '0xa'), (2, '0xb'), (3, '0xc'))
        state = world(windows=windows, workspaces=physical((1, ['0xa']), (2, ['0xb']), (3, ['0xc'])), default_layout='scrolling')
        state['direction'] = direction
        mem = from_records(rec('0xc', 'XR-1', 61, order=0, column=0, layout='scrolling'), rec('0xa', 'XR-1', 61, order=1, column=0, layout='scrolling'),
                           rec('0xb', 'XR-1', 61, order=2, column=1, layout='scrolling'))
        p, moves, settle = apply(state, mem)
        return run(state, *moves, *settle)

    def test_any_direction_but_left_up_or_down_counts_as_right_as_in_hyprland(self):
        for direction in ('right', 'auto', 1, None):
            with self.subTest(direction=direction):
                self.assertEqual(tiled(self.stacked(direction), 61), [['0xc', '0xa'], ['0xb']])

    def test_columns_read_left_to_right_are_not_forced_on_another_direction(self):
        for direction in ('left', 'up', 'down'):
            with self.subTest(direction=direction):
                r = self.stacked(direction)
                self.assertEqual(tiled(r, 61), [['0xc'], ['0xa'], ['0xb']])  # the order stays
                self.assertFalse([e for e in r['log'] if e.startswith('layout')], r['log'])

    def test_a_stack_that_should_not_be_one_comes_apart(self):
        windows = wins((1, '0xa'), (1, '0xb'), (1, '0xc'))
        state = world(windows=windows, workspaces=[{'id': 1, 'mon': 'DP-1', 'layout': 'scrolling', 'cols': [{'w': 0.5, 'list': ['0xa', '0xb', '0xc']}]}])
        mem = from_records(rec('0xb', 'XR-1', 61, order=0, column=0, layout='scrolling'), rec('0xc', 'XR-1', 61, order=1, column=1, layout='scrolling'),
                           rec('0xa', 'XR-1', 61, order=2, column=2, layout='scrolling'))
        p, moves, settle = apply(state, mem)
        r = run(state, *moves, *settle)
        self.assertEqual(tiled(r, 61), [['0xb'], ['0xc'], ['0xa']])

    def test_a_scrolling_workspace_that_already_fits_is_left_alone(self):
        windows = wins((1, '0xa'), (1, '0xb'), (1, '0xc'))
        state = world(windows=windows, workspaces=[{'id': 1, 'mon': 'DP-1', 'layout': 'scrolling',
                                                    'cols': [{'w': 0.25, 'list': ['0xa', '0xb']}, {'w': 0.6, 'list': ['0xc']}]}])
        mem = from_records(rec('0xa', 'XR-1', 61, order=0, column=0, layout='scrolling', size=(480, 587)), rec('0xb', 'XR-1', 61, order=1, column=0, layout='scrolling', size=(480, 587)),
                           rec('0xc', 'XR-1', 61, order=2, column=1, layout='scrolling', size=(1152, 1174)))
        p, moves, settle = apply(state, mem)
        r = run(state, *moves, *settle)
        self.assertFalse([e for e in r['log'] if e.startswith(('focus', 'swap', 'layout', 'resize'))], r['log'])
        self.assertEqual(r['columns']['61'][0]['w'], 0.25)

    def test_a_columns_width_comes_back(self):
        windows = wins((1, '0xa'), (2, '0xb'))
        state = world(windows=windows, workspaces=physical((1, ['0xa']), (2, ['0xb'])), default_layout='scrolling')
        mem = from_records(rec('0xa', 'XR-1', 61, order=0, column=0, layout='scrolling', size=(1440, 1174)), rec('0xb', 'XR-1', 61, order=1, column=1, layout='scrolling', size=(480, 1174)))
        p, moves, settle = apply(state, mem)
        r = run(state, *moves, *settle)
        widths = {c['list'][0]: c['w'] for c in r['columns']['61']}
        self.assertAlmostEqual(widths['0xa'], 0.75, places=2)
        self.assertAlmostEqual(widths['0xb'], 0.25, places=2)

    def test_a_scrolling_column_is_never_resized_with_the_clamping_dispatcher(self):
        windows = wins((1, '0xa'), (2, '0xb'))
        state = world(windows=windows, workspaces=physical((1, ['0xa']), (2, ['0xb'])), default_layout='scrolling')
        mem = from_records(rec('0xa', 'XR-1', 61, order=0, column=0, layout='scrolling', size=(1440, 1174)), rec('0xb', 'XR-1', 61, order=1, column=1, layout='scrolling', size=(480, 1174)))
        p, moves, settle = apply(state, mem)
        self.assertFalse([e for e in run(state, *moves, *settle)['log'] if e.startswith('resize')])

    def test_master_tiles_get_their_size(self):
        state = world(windows=wins((1, '0xa'), (2, '0xb')), workspaces=physical((1, ['0xa']), (2, ['0xb'])))
        mem = from_records(rec('0xa', 'XR-1', 61, order=0, size=(1200, 1100)), rec('0xb', 'XR-1', 61, order=1, size=(700, 1100)))
        p, moves, settle = apply(state, mem)
        r = run(state, *moves, *settle)
        self.assertEqual(client(r, '0xa')['size'], [1200, 1100])

    def test_a_size_that_fits_is_not_dispatched(self):
        state = world(windows=wins((1, '0xa')), workspaces=physical((1, ['0xa'])))
        p, moves, settle = apply(state, from_records(rec('0xa', 'XR-1', 61, size=(101, 99))))
        self.assertFalse([e for e in run(state, *moves, *settle)['log'] if e.startswith('resize')])

    def test_a_layout_that_differs_is_set_before_the_order(self):
        windows = wins((1, '0xa'), (2, '0xb'), (3, '0xc'))
        state = world(windows=windows, workspaces=physical((1, ['0xa']), (2, ['0xb']), (3, ['0xc'])))
        mem = from_records(rec('0xc', 'XR-1', 64, order=0, column=0, layout='scrolling'), rec('0xa', 'XR-1', 64, order=1, column=0, layout='scrolling'),
                           rec('0xb', 'XR-1', 64, order=2, column=1, layout='scrolling'))
        p, moves, settle = apply(state, mem)
        r = run(state, *moves, *settle)
        self.assertEqual(next(w for w in r['workspaces'] if w['id'] == 64)['tiledLayout'], 'scrolling')

    def test_each_desktop_shows_what_it_showed(self):
        state = world(windows=wins((1, '0xa'), (2, '0xb')), workspaces=physical((1, ['0xa']), (2, ['0xb'])))
        d = {n: {'width': 1920, 'height': 1200, 'scale': 1.0, 'active': 61 if n == 'XR-1' else None, 'placement': None, 'roll': None} for n in XR3}
        p, moves, settle = apply(state, from_records(rec('0xa', 'XR-1', 61), rec('0xb', 'XR-1', 62), desktops=d))
        r = run(state, *moves, *settle)
        self.assertEqual(next(m for m in r['monitors'] if m['name'] == 'XR-1')['activeWorkspace']['id'], 61)

    def test_focus_and_the_cursor_end_where_they_were(self):
        windows = wins((1, '0xa'), (2, '0xb'), (3, '0xc'))
        state = world(windows=windows, workspaces=physical((1, ['0xa']), (2, ['0xb']), (3, ['0xc'])), default_layout='scrolling', focus='0xb')
        mem = from_records(rec('0xc', 'XR-1', 61, order=0, column=0, layout='scrolling'), rec('0xa', 'XR-1', 61, order=1, column=0, layout='scrolling'),
                           rec('0xb', 'XR-1', 61, order=2, column=1, layout='scrolling'))
        p, moves, settle = apply(state, mem, active={'address': '0xb', 'monitor': 'DP-1'})
        r = run(state, *moves, *settle)
        self.assertEqual(r['activewindow'], {'address': '0xb'})
        self.assertEqual(r['cursor'], [11, 22])

    def test_a_closed_window_is_skipped(self):
        state = world(windows=wins((1, '0xa'), (1, '0xb')), workspaces=physical((1, ['0xa', '0xb'])))
        r = run(state)
        mem = from_records(rec('0xb', 'XR-1', 61, order=0, floating=True), rec('0xa', 'XR-1', 61, order=1))
        p = wm.plan(mem, r['clients'], r['workspaces'], r['monitors'], XR3, 'DP-1')
        p['state'] = [('0xdead', 1, 0, 0, 10, 10)] + p['state']
        p['order'] = [[('0xdead', 0, 0, 0)] + rows for rows in p['order']]
        p['fullscreen'] = [('0xdead', 1, 1)]
        p['shown'] = [('XR-1', '0xdead')]
        r = run(state, *wm.moves_lua(p), *wm.settle_lua(p))
        self.assertEqual(on(r, '0xa'), 61)


class BatchTests(unittest.TestCase):
    LONG = '0x' + 'f' * 16

    def big(self, n=256):
        a = [f'0x{0xffffffffff0000 + i:016x}' for i in range(n)]
        return {'whole': [(2147483647 - i, 'XR-4', 2147483000 - i) for i in range(40)], 'windows': [(x, 'XR-4', 2147483647, 1) for x in a],
                'layouts': [(x, 'scrolling') for x in a], 'state': [(x, 1, -1048576, -1048576, 1048576, 1048576) for x in a],
                'order': [[(x, 255, 1048576, 1048576) for x in a]], 'fullscreen': [(x, 3, 3) for x in a], 'shown': [(f'XR-{n}', a[n]) for n in range(1, 5)],
                'placed': [], 'focus': a[0], 'monitor': 'DP-1'}

    def test_every_batch_fits_the_socket_limit(self):
        p = self.big()
        for code in wm.moves_lua(p) + wm.settle_lua(p):
            self.assertLess(len(code), 4096)

    def test_no_window_is_lost_to_batching(self):
        p = self.big()
        moves, settle = '\n'.join(wm.moves_lua(p)), '\n'.join(wm.settle_lua(p))
        for row in p['windows']: self.assertIn(row[0], moves)
        for row in p['state']: self.assertIn(row[0], settle)
        for row in p['fullscreen']: self.assertIn(row[0], settle)

    def test_a_small_plan_is_one_eval_each_step(self):
        p = dict(self.big(), whole=[(1, 'XR-1', 61)], windows=[], layouts=[], state=[('0xa', 1, 0, 0, 10, 10)], order=[], fullscreen=[], shown=[], focus=None, monitor=None)
        self.assertEqual(len(wm.moves_lua(p)), 1)
        self.assertEqual(len(wm.settle_lua(p)), 1)

    def test_nothing_to_do_is_no_eval(self):
        p = dict(self.big(), whole=[], windows=[], layouts=[], state=[], order=[], fullscreen=[], shown=[], focus=None, monitor=None)
        self.assertEqual((wm.moves_lua(p), wm.settle_lua(p)), ([], []))

    def test_a_group_is_not_split_while_it_fits(self):
        rows = [(f'0x{i:x}', 'XR-1', 61, 0) for i in range(30)] + [(f'0x{i + 100:x}', 'XR-2', 71, 0) for i in range(40)] + \
               [(f'0x{i + 200:x}', 'XR-3', 81, 1) for i in range(40)]
        p = dict(self.big(), whole=[], windows=rows)
        batches = wm.moves_lua(p)
        self.assertGreater(len(batches), 1)
        for group in ('XR-3', 'XR-2'):
            self.assertEqual(sum(group in b for b in batches), 1)

    def test_hostile_values_never_reach_lua(self):
        base = dict(self.big(), whole=[], windows=[], layouts=[], state=[], order=[], fullscreen=[], shown=[], focus=None, monitor=None)
        bad = [dict(base, whole=[(1, 'XR-1"..os.exit()', 61)]), dict(base, whole=[(0, 'XR-1', 61)]), dict(base, whole=[(1, 'XR-1', 'x')]),
               dict(base, windows=[('0xa"', 'XR-1', 61, 0)]), dict(base, windows=[('0xa', 'DP-1', 61, 0)]), dict(base, windows=[('0xa', 'XR-1', 61, 2)]),
               dict(base, layouts=[('0xa', 'a"b')]), dict(base, state=[('0xa', 1, 'x', 0, 0, 0)]), dict(base, state=[('0xa', 2, 0, 0, 0, 0)]),
               dict(base, order=[[('0xa', 'x', 0, 0)]]), dict(base, fullscreen=[('0xa', 9, 0)]), dict(base, shown=[('DP-1', '0xa')]),
               dict(base, focus='x'), dict(base, monitor='a"b')]
        for p in bad:
            with self.subTest(p=p), self.assertRaises(ValueError):
                wm.moves_lua(p); wm.settle_lua(p)

    def test_hyprland_client_evals_only_the_generated_lua(self):
        from tools.native_spike.hyprland import HyprlandClient
        client = HyprlandClient('0' * 40 + '_1_1', __import__('pathlib').Path('/run/user/1000'))
        sent = []
        client._request = lambda request, timeout=2.0: (sent.append(request), b'ok')[1]
        p = dict(self.big(), whole=[(1, 'XR-1', 61)], windows=[], layouts=[], state=[('0xa', 1, 0, 0, 10, 10)], order=[], fullscreen=[], shown=[], focus=None, monitor=None)
        client.place_windows(p, 'moves')
        client.place_windows(p, 'settle')
        self.assertEqual(sent, ['/eval ' + c for c in wm.moves_lua(p) + wm.settle_lua(p)])
        with self.assertRaises(ValueError): client.place_windows(p, 'other')


@unittest.skipUnless(LUA, 'no Lua interpreter')
class RoundTripTests(unittest.TestCase):
    """Capture from one stopped session, Stop's restore, the next start: the windows are where they were."""
    def session(self):
        windows = [{'address': '0xa', 'ws': 1, 'class': 'foot', 'title': 'vim'}, {'address': '0xb', 'ws': 1, 'class': 'foot', 'title': 'zsh'},
                   {'address': '0xc', 'ws': 2, 'class': 'firefox', 'title': 'docs'}, {'address': '0xd', 'ws': 3, 'class': 'mpv', 'title': 'film', 'floating': True, 'x': 300, 'y': 200, 'w': 640, 'h': 360}]
        return world(windows=windows, workspaces=physical((1, ['0xa', '0xb']), (2, ['0xc']), (3, [])))

    def adopted(self, state):
        before = run(state)
        moves = adopt.plan(before['workspaces'], before['clients'], 'DP-1', XR3)
        snapshot = restore.capture(before['workspaces'], before['clients'], before['monitors'], before['activewindow'], 'DP-1', 'r1')
        return snapshot, adopt.lua(moves)

    def test_a_session_moved_by_hand_comes_back(self):
        state = self.session()
        snapshot, adopting = self.adopted(state)
        # In the run: the terminal 0xb goes to XR-2's other workspace, and 0xd to XR-1.
        user = ('hl.dispatch(hl.dsp.window.move({workspace="74",follow=false,window="address:0xb"})) '
                'hl.dispatch(hl.dsp.workspace.move({workspace="74",monitor="XR-2"})) '
                'hl.dispatch(hl.dsp.window.move({workspace="61",follow=false,window="address:0xd"})) ')
        live = run(state, *adopting, user)
        mem = wm.capture(live['clients'], live['workspaces'], live['monitors'], XR3, instance='inst')
        stopped = run(state, *adopting, user, restore.lua(snapshot))
        self.assertEqual({on(stopped, a) for a in ('0xa', '0xb')}, {1})
        p = wm.plan(mem, stopped['clients'], stopped['workspaces'], stopped['monitors'], XR3, 'DP-1', instance='inst')
        back = run(state, *adopting, user, restore.lua(snapshot), *wm.moves_lua(p), *wm.settle_lua(p))
        for address in ('0xa', '0xb', '0xc', '0xd'):
            self.assertEqual(on(back, address), on(live, address), address)
            self.assertEqual(client(back, address)['floating'], client(live, address)['floating'], address)
        self.assertEqual(monitor_of(back, on(back, '0xb')), 'XR-2')
        self.assertEqual(client(back, '0xd')['size'], client(live, '0xd')['size'])

    def test_stop_still_restores_after_a_remembered_start(self):
        state = self.session()
        snapshot, adopting = self.adopted(state)
        before = run(state)
        mem = from_records(rec('0xa', 'XR-3', 81, cls='foot', title='vim'), rec('0xb', 'XR-1', 61, cls='foot', title='zsh'),
                           rec('0xc', 'XR-2', 71, cls='firefox', title='docs'))
        p = wm.plan(mem, before['clients'], before['workspaces'], before['monitors'], XR3, 'DP-1', instance='inst')
        after = run(state, *wm.moves_lua(p), *wm.settle_lua(p), restore.lua(snapshot))
        self.assertEqual((on(after, '0xa'), on(after, '0xb'), on(after, '0xc')), (1, 1, 2))
        self.assertEqual(tiled(after, 1), [['0xa'], ['0xb']])
        self.assertEqual({monitor_of(after, 1), monitor_of(after, 2)}, {'DP-1'})

    def test_a_scrolling_session_keeps_its_columns_and_widths(self):
        windows = wins((5, '0xa'), (5, '0xb'), (5, '0xc'))
        state = world(windows=windows, workspaces=[{'id': 5, 'mon': 'DP-1', 'layout': 'scrolling',
                                                    'cols': [{'w': 0.3, 'list': ['0xb']}, {'w': 0.5, 'list': ['0xa', '0xc']}]}])
        before = run(state)
        adopting = adopt.lua(adopt.plan(before['workspaces'], before['clients'], 'DP-1', XR3))
        live = run(state, *adopting)
        mem = wm.capture(live['clients'], live['workspaces'], live['monitors'], XR3, instance='inst')
        snapshot = restore.capture(before['workspaces'], before['clients'], before['monitors'], before['activewindow'], 'DP-1', 'r1')
        stopped = run(state, *adopting, restore.lua(snapshot))
        p = wm.plan(mem, stopped['clients'], stopped['workspaces'], stopped['monitors'], XR3, 'DP-1', instance='inst')
        back = run(state, *adopting, restore.lua(snapshot), *wm.moves_lua(p), *wm.settle_lua(p))
        wid = on(live, '0xa')
        self.assertEqual(on(back, '0xb'), wid)
        self.assertEqual(back['columns'][str(wid)], live['columns'][str(wid)])


if __name__ == '__main__':
    unittest.main()
