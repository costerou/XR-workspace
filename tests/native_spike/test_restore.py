"""Session end: the adopted windows back to their pre-XR layout (restore.py), and its wiring into Stop and Recover."""
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tools.native_spike import adopt, restore


# A stand-in for the parts of Hyprland's Lua API the adopt and restore evals use, with a tiled order per workspace (a
# tiled window's position is its slot: x = monitor x + 100 * index), floating geometry, fullscreen, focus, Omarchy's
# per-monitor blocks, and Hyprland's habit of giving a monitor whose last workspace left the lowest free number.
# Chunks run in turn on one state; the result is the state as Hyprland's j/ replies would report it.
HARNESS = r'''
local state = STATE
local mons, order, spaces, wins, log = {}, {}, {}, {}, {}
local focus = { monitor = state.focus_monitor, window = state.focus }
local ARR = {}
local function arr(t) return setmetatable(t, ARR) end
for _, m in ipairs(state.monitors) do mons[m.name] = { name = m.name, active = m.active, block = m.block, x = m.x }; order[#order + 1] = m.name end
for _, s in ipairs(state.workspaces) do spaces[s.id] = { mon = s.mon, tiled = {} } end
for _, w in ipairs(state.windows) do
  wins[w.address] = { ws = w.ws, floating = w.floating or false, x = w.x or 0, y = w.y or 0, w = w.w or 100, h = w.h or 100,
                      fs = w.fs or 0, fc = w.fc or w.fs or 0, pinned = w.pinned or false, class = w.class or "kitty",
                      title = w.title or "term" }
  if not wins[w.address].floating then local t = spaces[w.ws].tiled; t[#t + 1] = w.address end
end
local function count(id) local n = 0; for _, w in pairs(wins) do if w.ws == id then n = n + 1 end end; return n end
local function index(id, a) for i, x in ipairs(spaces[id].tiled) do if x == a then return i end end end
local function untile(a) local w = wins[a]; local i = spaces[w.ws] and index(w.ws, a); if i then table.remove(spaces[w.ws].tiled, i) end end
local function tile(a) local t = spaces[wins[a].ws].tiled; t[#t + 1] = a end
local function pos(a)
  local w = wins[a]; local m = mons[spaces[w.ws].mon]
  if w.fs ~= 0 then return m.x, 0 end
  if w.floating then return w.x, w.y end
  return m.x + 100 * (index(w.ws, a) - 1), 0
end
local function create(id, mon) spaces[id] = { mon = mon, tiled = {} }; log[#log + 1] = "create " .. id .. " " .. mon end
local wsobj, monobj
function monobj(name)
  local m = mons[name]; if not m then return nil end
  return { name = m.name, active_workspace = m.active and spaces[m.active] and wsobj(m.active, true) or nil,
           set_workspace = function(self, a) m.active = tonumber(a.workspace); log[#log + 1] = "show " .. a.workspace .. " " .. name end }
end
function wsobj(id, shallow)
  local s = spaces[id]; if not s then return nil end
  local m = mons[s.mon]
  return { id = id, name = tostring(id), windows = count(id), special = false,
           monitor = m and (shallow and { name = m.name, active_workspace = { id = m.active } } or monobj(m.name)) or nil }
end
local function winobj(a)
  local w = wins[a]; if not w then return nil end
  local x, y = pos(a)
  return { address = a, workspace = wsobj(w.ws, true), floating = w.floating, at = { x = x, y = y }, fullscreen = w.fs,
           fullscreen_client = w.fc, pinned = w.pinned, group = nil }
end
local function addr(sel) assert(type(sel) == "string" and sel:sub(1, 8) == "address:", "bad window selector"); return sel:sub(9) end
local function need(a) local w = wins[a]; if not w then error("window not found") end; return w end
hl = { dsp = { workspace = {}, window = {} } }
function hl.get_workspace(id) return wsobj(tonumber(id)) end
function hl.get_workspaces() local r = {}; for id in pairs(spaces) do r[#r + 1] = wsobj(id, true) end; return r end
function hl.get_monitor(name) return monobj(name) end
function hl.get_window(sel) return winobj(addr(sel)) end
function hl.get_windows() local r = {}; for a in pairs(wins) do r[#r + 1] = winobj(a) end; return r end
function hl.dispatch(f) f() end
function hl.dsp.focus(a) return function()
  if a.monitor then assert(mons[a.monitor], "no monitor"); focus.monitor = a.monitor; focus.window = nil
  elseif a.window then local w = need(addr(a.window)); focus.window = addr(a.window); focus.monitor = spaces[w.ws].mon; mons[focus.monitor].active = w.ws
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
    assert(a.follow == false)
    local id = tonumber(a.workspace); if not spaces[id] then create(id, focus.monitor) end
    untile(addr(a.window)); w.ws = id; if not w.floating then tile(addr(a.window)) end
    log[#log + 1] = "window " .. addr(a.window) .. " " .. id
  else
    if w.fs ~= 0 then error("Window is fullscreen") end
    assert(w.floating, "moving a tiled window"); w.x, w.y = a.x, a.y
  end end end
function hl.dsp.window.resize(a) return function()
  local w = need(addr(a.window)); if w.fs ~= 0 then error("Window is fullscreen") end; w.w, w.h = a.x, a.y end end
function hl.dsp.window.swap(a) return function()
  local p, q = addr(a.window), addr(a.target); local wp, wq = need(p), need(q)
  if wp.fs ~= 0 or wq.fs ~= 0 then error("Can't swap fullscreen window") end
  assert(wp.ws == wq.ws, "swap across workspaces")
  local i, j = index(wp.ws, p), index(wq.ws, q); local t = spaces[wp.ws].tiled; t[i], t[j] = q, p
  log[#log + 1] = "swap " .. p .. " " .. q end end
function hl.dsp.window.float(a) return function()
  local w = need(addr(a.window)); local on = a.action == "on"
  if w.floating == on then return end
  if on then untile(addr(a.window)); w.floating = true else w.floating = false; tile(addr(a.window)) end end end
function hl.dsp.window.fullscreen_state(a) return function()
  local w = need(addr(a.window)); assert(a.action == "set"); w.fs, w.fc = a.internal, a.client end end
if state.omarchy then
  o = { workspaces = { workspace_id = function(m, slot) return mons[m.name].block * 10 + slot end } }
end
CHUNKS
local function dump()
  local r = { clients = arr({}), workspaces = arr({}), monitors = arr({}), activewindow = {}, tiled = {}, log = arr(log) }
  local addresses = {}; for a in pairs(wins) do addresses[#addresses + 1] = a end; table.sort(addresses)
  for _, a in ipairs(addresses) do
    local w = wins[a]; local x, y = pos(a)
    r.clients[#r.clients + 1] = { address = a, workspace = { id = w.ws, name = tostring(w.ws) }, at = arr({ x, y }),
      size = arr({ w.w, w.h }), floating = w.floating, pinned = w.pinned, fullscreen = w.fs, fullscreenClient = w.fc,
      class = w.class, title = w.title }
  end
  local ids = {}; for id in pairs(spaces) do ids[#ids + 1] = id end; table.sort(ids)
  for _, id in ipairs(ids) do
    r.workspaces[#r.workspaces + 1] = { id = id, name = tostring(id), monitor = spaces[id].mon, windows = count(id) }
    r.tiled[tostring(id)] = arr({ table.unpack(spaces[id].tiled) })
  end
  for _, name in ipairs(order) do
    local m = mons[name]
    r.monitors[#r.monitors + 1] = { name = name, activeWorkspace = { id = m.active or -1, name = tostring(m.active) },
      focused = focus.monitor == name }
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
    return '{' + ','.join(f'{k}={lua_table(v)}' for k, v in value.items()) + '}'


LUA = shutil.which('lua5.4') or shutil.which('lua')
PANEL = dict(cls=adopt.PANEL_CLASS, title=adopt.PANEL_TITLE)


def world(omarchy=True):
    """DP-1 (block 0) with three workspaces, each XR desktop (blocks 3..5) on its own empty first slot:
    1: tiled a, b, c in that order, floating f; 2: tiled d, e with e fullscreen (shown, focused); 3: the panel and 9."""
    return {'omarchy': omarchy, 'focus': '0xe', 'focus_monitor': 'DP-1',
            'monitors': [{'name': 'DP-1', 'active': 2, 'block': 0, 'x': 0}, {'name': 'XR-1', 'active': 31, 'block': 3, 'x': 10000},
                         {'name': 'XR-2', 'active': 41, 'block': 4, 'x': 20000}, {'name': 'XR-3', 'active': 51, 'block': 5, 'x': 30000}],
            'workspaces': [{'id': i, 'mon': 'DP-1'} for i in (1, 2, 3)] + [{'id': 31, 'mon': 'XR-1'}, {'id': 41, 'mon': 'XR-2'},
                                                                          {'id': 51, 'mon': 'XR-3'}],
            'windows': [{'address': '0xa', 'ws': 1}, {'address': '0xb', 'ws': 1}, {'address': '0xc', 'ws': 1},
                        {'address': '0xf', 'ws': 1, 'floating': True, 'x': 500, 'y': 300, 'w': 400, 'h': 300},
                        {'address': '0xd', 'ws': 2}, {'address': '0xe', 'ws': 2, 'fs': 2},
                        {'address': '0xf0', 'ws': 3, 'floating': True, 'x': 10, 'y': 10, 'class': adopt.PANEL_CLASS,
                         'title': adopt.PANEL_TITLE},
                        {'address': '0x9', 'ws': 3}]}


@unittest.skipUnless(LUA, 'needs a Lua 5.4 interpreter')
class RoundTripTests(unittest.TestCase):
    """Adoption at start, changes during the run, the restore at Stop: the windows end up as they were."""

    def run_lua(self, state, *chunks):
        body = '\n'.join(f';(function() {c} end)()' for c in chunks)
        script = HARNESS.replace('STATE', lua_table(state)).replace('CHUNKS', body)
        done = subprocess.run([LUA, '-'], input=script, capture_output=True, text=True, timeout=10)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    def start(self, state):
        """The run's start: the snapshot and the adoption eval, from the replies Hyprland would give."""
        before = self.run_lua(state)
        snapshot = restore.capture(before['workspaces'], before['clients'], before['monitors'], before['activewindow'],
                                   'DP-1', 'run')
        moves = adopt.plan(before['workspaces'], before['clients'], 'DP-1', ['XR-1', 'XR-2', 'XR-3'])
        return before, snapshot, adopt.lua(moves)

    def where(self, r):
        spaces = {w['id']: w['monitor'] for w in r['workspaces']}
        return {c['address']: (c['workspace']['id'], spaces[c['workspace']['id']]) for c in r['clients']}

    def assertRestored(self, before, after, ignore=()):
        self.assertEqual({a: v for a, v in self.where(after).items() if a not in ignore},
                         {a: v for a, v in self.where(before).items() if a not in ignore})
        for wid in ('1', '2', '3'):
            self.assertEqual([a for a in after['tiled'][wid] if a not in ignore], before['tiled'][wid], wid)
        strip = lambda r: {c['address']: c for c in r['clients'] if c['address'] not in ignore}
        for address, client in strip(before).items(): self.assertEqual(strip(after)[address], client, address)
        self.assertEqual(after['monitors'][0], before['monitors'][0])  # DP-1 shows 2 again, and has focus
        self.assertEqual(after['activewindow'], before['activewindow'])

    def test_adopted_windows_go_back_exactly(self):
        state = world()
        before, snapshot, adopting = self.start(state)
        mid = self.run_lua(state, *adopting)
        self.assertEqual({w['monitor'] for w in mid['workspaces'] if w['windows'] and w['id'] != 3}, {'XR-1', 'XR-2', 'XR-3'})
        self.assertNotIn('1', mid['tiled'])  # adoption renumbered it into XR-1's block
        after = self.run_lua(state, *adopting, restore.lua(snapshot))
        self.assertRestored(before, after)

    def test_changes_during_the_run_are_undone_and_new_windows_keep_their_place(self):
        state = world()
        state['windows'] += [{'address': '0x7', 'ws': 99}, {'address': '0x8', 'ws': 98}]
        state['workspaces'] += [{'id': 99, 'mon': 'XR-3'}, {'id': 98, 'mon': 'XR-3'}]
        # The two windows opened during the run are parked on 99/98 until the adoption has run, then put in place.
        before, snapshot, adopting = self.start(state)
        user = ('local function D(f,a) hl.dispatch(f(a)) end '
                'local one=hl.get_window("address:0xa").workspace.id '
                'D(hl.dsp.window.swap,{window="address:0xa",target="address:0xc"}) '           # reorder
                'D(hl.dsp.window.float,{action="on",window="address:0xb"}) '                   # float a tiled one
                'D(hl.dsp.window.float,{action="off",window="address:0xf"}) '                  # tile a floating one
                'D(hl.dsp.window.fullscreen_state,{internal=0,client=0,action="set",window="address:0xe"}) '
                'D(hl.dsp.window.move,{workspace="51",follow=false,window="address:0xc"}) '    # onto another desktop
                'D(hl.dsp.window.move,{workspace="51",follow=false,window="address:0xd"}) '
                'D(hl.dsp.window.move,{workspace=tostring(one),follow=false,window="address:0x7"}) '  # new, on desktop 1
                'D(hl.dsp.workspace.change_id,{workspace="98",id=33}) '                        # new workspace on XR-1
                'D(hl.dsp.workspace.move,{workspace="33",monitor="XR-1"}) '
                'D(hl.dsp.focus,{window="address:0xa"})')
        after = self.run_lua(state, *adopting, user, restore.lua(snapshot))
        self.assertRestored(before, after, ignore=('0x7', '0x8'))
        where = self.where(after)
        self.assertEqual(where['0x7'], (1, 'DP-1'))        # with the workspace it was opened on
        self.assertEqual(after['tiled']['1'], ['0xa', '0xb', '0xc', '0x7'])  # after the restored ones
        self.assertEqual(where['0x8'], (4, 'DP-1'))        # 33 is slot 3; 3 is a restored one: the first free slot

    def test_without_omarchy_ids_are_kept(self):
        state = world(omarchy=False)
        before, snapshot, adopting = self.start(state)
        after = self.run_lua(state, *adopting, restore.lua(snapshot))
        self.assertRestored(before, after)
        self.assertFalse(any(e.startswith('renumber') for e in after['log']))

    def test_renumbered_again_during_the_run_and_an_id_taken_by_an_empty_workspace(self):
        # A pill's Add / theme switch (Omarchy reconcile) renumbered desktop 1's workspace again, and an XR desktop
        # was given number 1 for an empty workspace: the windows still find their workspace, and 1 is freed for it.
        state = world()
        before, snapshot, adopting = self.start(state)
        user = ('local id=hl.get_window("address:0xa").workspace.id '
                'hl.dispatch(hl.dsp.workspace.change_id({workspace=tostring(id),id=35})) '
                'hl.dispatch(hl.dsp.focus({monitor="XR-2"})) hl.dispatch(hl.dsp.focus({workspace="1"}))')
        mid = self.run_lua(state, *adopting, user)
        self.assertEqual(next(w for w in mid['workspaces'] if w['id'] == 1)['monitor'], 'XR-2')
        after = self.run_lua(state, *adopting, user, restore.lua(snapshot))
        self.assertRestored(before, after)
        self.assertNotEqual(next(w for w in after['workspaces'] if w['id'] == 1)['monitor'], 'XR-2')

    def test_closed_windows_and_a_failing_step_leave_the_rest(self):
        state = world()
        before, snapshot, adopting = self.start(state)
        snapshot['windows'].append({'address': '0xdead', 'workspace': 1, 'floating': True, 'at': [0, 0], 'size': [5, 5],
                                    'fullscreen': 0, 'client': 0})
        user = 'hl.dispatch(function() end)'
        after = self.run_lua(state, *adopting, user, restore.lua(snapshot))
        self.assertRestored(before, after)

    def test_restore_is_idempotent(self):
        state = world()
        before, snapshot, adopting = self.start(state)
        after = self.run_lua(state, *adopting, restore.lua(snapshot), restore.lua(snapshot))
        self.assertRestored(before, after)

    def test_a_crashed_run_restores_after_recovery_already_evacuated(self):
        # Recovery's evacuation (without the restore) left everything on DP-1 under the XR blocks' numbers.
        state = world()
        before, snapshot, adopting = self.start(state)
        evacuate = ('for _,n in ipairs({"XR-1","XR-2","XR-3"}) do for _,x in ipairs(hl.get_workspaces()) do '
                    'if x.monitor and x.monitor.name==n then hl.dispatch(hl.dsp.workspace.move({workspace=tostring(x.id),monitor="DP-1"})) end end end')
        after = self.run_lua(state, *adopting, evacuate, restore.lua(snapshot))
        self.assertRestored(before, after)


class SnapshotTests(unittest.TestCase):
    def snapshot(self):
        spaces = [{'id': 1, 'name': '1', 'monitor': 'DP-1'}, {'id': 2, 'name': '2', 'monitor': 'DP-1'},
                  {'id': 7, 'name': '7', 'monitor': 'HDMI-A-1'}, {'id': -98, 'name': 'special:scratch', 'monitor': 'DP-1'}]
        clients = [{'address': '0x1', 'workspace': {'id': 1, 'name': '1'}, 'at': [0, 0], 'size': [960, 1080], 'floating': False,
                    'pinned': False, 'fullscreen': 0, 'fullscreenClient': 0, 'class': 'kitty', 'title': 't'},
                   {'address': '0x2', 'workspace': {'id': 1, 'name': '1'}, 'at': [-5, 40], 'size': [300, 200], 'floating': True,
                    'pinned': False, 'fullscreen': 1, 'fullscreenClient': 0, 'class': 'kitty', 'title': 't'},
                   {'address': '0x3', 'workspace': {'id': 2, 'name': '2'}, 'at': [0, 0], 'size': [10, 10], 'floating': True,
                    'pinned': True, 'class': 'kitty', 'title': 't'},
                   {'address': '0x4', 'workspace': {'id': 2, 'name': '2'}, 'at': [0, 0], 'size': [10, 10], 'floating': False,
                    'pinned': False, 'class': 'kitty', 'title': 't'},
                   {'address': '0x5', 'workspace': {'id': 7, 'name': '7'}, 'at': [0, 0], 'size': [10, 10], 'floating': False,
                    'pinned': False, 'class': 'kitty', 'title': 't'},
                   {'address': '0x6', 'workspace': {'id': -98, 'name': 'special:scratch'}, 'at': [0, 0], 'size': [10, 10],
                    'floating': False, 'pinned': False, 'class': 'kitty', 'title': 't'}]
        monitors = [{'name': 'DP-1', 'activeWorkspace': {'id': 2}, 'focused': False},
                    {'name': 'HDMI-A-1', 'activeWorkspace': {'id': 7}, 'focused': True}]
        return restore.capture(spaces, clients, monitors, {'address': '0x5'}, 'DP-1', 'run-1')

    def test_capture_takes_what_adoption_moves_and_where_the_fallback_was(self):
        s = self.snapshot()
        self.assertEqual(s['workspaces'], [{'id': 1, 'whole': True}, {'id': 2, 'whole': False}])
        self.assertEqual([w['address'] for w in s['windows']], ['0x1', '0x2', '0x4'])  # not pinned, special, other monitor
        self.assertEqual(s['windows'][1], {'address': '0x2', 'workspace': 1, 'floating': True, 'at': [-5, 40],
                                           'size': [300, 200], 'fullscreen': 1, 'client': 0})
        self.assertEqual((s['active'], s['focus'], s['focus_monitor']), (2, '0x5', 'HDMI-A-1'))

    def test_saved_privately_and_loaded_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {'XDG_STATE_HOME': tmp}
            directory = restore.layout_dir(env)
            self.assertEqual(directory, Path(tmp) / 'viture-workspace' / 'layouts')
            path = restore.save(self.snapshot(), directory)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
            self.assertEqual(restore.load('run-1', directory), self.snapshot())
            self.assertIsNone(restore.load('run-2', directory))
            restore.discard('run-1', directory); restore.discard('run-1', directory)
            self.assertIsNone(restore.load('run-1', directory))
            self.assertIsNone(restore.load('run-1', Path(tmp) / 'missing'))

    def test_a_tampered_file_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = restore.save(self.snapshot(), tmp)
            data = json.loads(path.read_text())
            for change in ({'run_id': 'run-2'}, {'monitor': 'XR-1'}, {'focus': '0x1"..os.exit()--'},
                           {'windows': [dict(data['windows'][0], address='0x1") os.exit() --')]},
                           {'windows': [dict(data['windows'][0], workspace=9)]},
                           {'windows': [dict(data['windows'][0], at=[0.5, 0])]}, {'extra': 1}):
                with self.subTest(change=change):
                    path.write_text(json.dumps(dict(data, **change)))
                    with self.assertRaises(ValueError): restore.load('run-1', tmp)

    def test_lua_is_one_eval_of_validated_values(self):
        code = restore.lua(self.snapshot())
        self.assertTrue(code.startswith('local F="DP-1" local A=2 local P="0x5" local PM="HDMI-A-1" '
                                        'local S={{1,1},{2,0}} local W={{"0x1",1,0,0,0,960,1080,0,0},'))
        self.assertLessEqual(len(code), restore.EVAL_LIMIT)
        many = dict(self.snapshot(), windows=[{'address': f'0x{i:012x}', 'workspace': 1, 'floating': False, 'at': [i, 0],
                                               'size': [1, 1], 'fullscreen': 0, 'client': 0} for i in range(400)])
        with self.assertRaises(ValueError): restore.lua(many)

    def test_hyprland_client_evals_only_the_generated_lua(self):
        from tools.native_spike.hyprland import HyprlandClient
        client = HyprlandClient.__new__(HyprlandClient)
        sent = []
        with patch.object(HyprlandClient, '_request', lambda self, request, timeout=None: sent.append(request) or b'ok'):
            client.restore_layout(self.snapshot())
            with self.assertRaises(ValueError): client.restore_layout(dict(self.snapshot(), monitor='DP-1"'))
        self.assertEqual(sent, ['/eval ' + restore.lua(self.snapshot())])
        self.assertGreater(len(sent[0]), 4096)  # the bind limit does not apply to the restore's own bound

    def test_restore_never_raises(self):
        said = []
        class Broken:
            def restore_layout(self, snapshot): raise OSError('socket gone')
        self.assertFalse(restore.restore(Broken(), self.snapshot(), notify=said.append))
        self.assertEqual(len(said), 1)
        self.assertFalse(restore.restore(object(), self.snapshot()))
        self.assertFalse(restore.restore(Broken(), None))


class Transport:
    def __init__(self, log, fail=False):
        self.log, self.fail = log, fail
    def query(self, kind, timeout=None):
        return {'workspaces': [{'id': 1, 'name': '1', 'monitor': 'DP-1'}],
                'clients': [{'address': '0x1', 'workspace': {'id': 1, 'name': '1'}, 'at': [0, 0], 'size': [9, 9],
                             'floating': False, 'pinned': False, 'class': 'kitty', 'title': 't'}],
                'monitors': [{'name': 'DP-1', 'activeWorkspace': {'id': 1}, 'focused': True}], 'activewindow': {}}[kind]
    def restore_layout(self, snapshot):
        self.log.append(('restore', snapshot['run_id']))
        if self.fail: raise ValueError('compositor rejected bind change')


class RecoveryWiringTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.layouts = Path(self.tmp.name) / 'layouts'

    def recovered(self, complete, fail=False, snapshot=None):
        from tools.native_spike import managed
        from tools.native_spike.journal import RecoveryResult
        log = []
        journal = type('J', (), {})()
        def recover(j, broker):
            log.append(('recover',)); return RecoveryResult(complete, [] if complete else ['x'])
        with patch.object(managed, 'recover', recover), patch('tools.native_spike.send.notify', lambda m: log.append(('notify',))):
            managed.recover_restoring(Transport(log, fail), journal, None, 'run-1', snapshot, self.layouts)
        return log

    def test_saved_snapshot_restores_before_recovery_and_goes_once_complete(self):
        restore.save(restore.take(Transport([]), 'DP-1', 'run-1', self.layouts), self.layouts)
        self.assertEqual(self.recovered(complete=False), [('restore', 'run-1'), ('recover',)])
        self.assertIsNotNone(restore.load('run-1', self.layouts))  # kept for the next attempt
        self.assertEqual(self.recovered(complete=True), [('restore', 'run-1'), ('recover',)])
        self.assertIsNone(restore.load('run-1', self.layouts))
        self.assertEqual(self.recovered(complete=True), [('recover',)])  # nothing saved: recovery as before

    def test_a_failed_restore_still_recovers(self):
        restore.take(Transport([]), 'DP-1', 'run-1', self.layouts)
        self.assertEqual(self.recovered(complete=True, fail=True), [('restore', 'run-1'), ('notify',), ('recover',)])

    def test_an_unreadable_snapshot_still_recovers(self):
        self.layouts.mkdir(mode=0o700)
        (self.layouts / 'run-1.json').write_text('{}'); (self.layouts / 'run-1.json').chmod(0o600)
        self.assertEqual(self.recovered(complete=True), [('recover',)])


class ManagedRestoreTests(unittest.TestCase):
    """_run_owned: the snapshot just before adoption, the restore after the renderer and before recovery."""
    def setUp(self):
        from test_managed import ManagedTransport
        from tools.native_spike.journal import Journal
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'xr-workspace-spike' / 'run' / 'journal.json'
        self.path.parent.mkdir(parents=True, mode=0o700); self.path.parent.parent.chmod(0o700)
        self.journal = Journal.open(self.path, 'run', 'session')
        self.layouts = Path(self.tmp.name) / 'state' / 'layouts'
        order = self.order = []
        class T(ManagedTransport):
            def query(self, kind, timeout=None):
                if kind in ('workspaces', 'clients', 'activewindow') or (kind == 'monitors' and order and order[-1] == 'q'):
                    return Transport([]).query(kind)
                return super().query(kind)
            def adopt_windows(self, moves): order.append('adopt')
            def restore_layout(self, snapshot): order.append(('restore', snapshot['windows'][0]['address']))
            def connect_wayland(self, session): return open(os.devnull, 'rb')
        self.T = T

    def run_owned(self, adopt_on):
        from tools.native_spike import managed
        from tools.native_spike.ownership import OwnershipBrokerClient
        from tools.native_spike.hyprland import plan_outputs
        from tools.native_spike.journal import RecoveryResult
        transport = self.T(self.path)
        broker = OwnershipBrokerClient(transport, 'run', 'session', 'DP-1'); broker.require_output_ownership()
        order, saved = self.order, []
        class Stop(Exception): pass
        def controls(*a, **k):
            saved.append(restore.load('run', self.layouts)); order.append('controls'); raise Stop
        def recover(journal, b): order.append('recover'); return RecoveryResult(True, [])
        real = restore.take
        def take(client, fallback, run_id, directory):
            order.append('q')
            return real(client, fallback, run_id, directory)
        with patch.object(managed, 'view_controls', controls), patch.object(managed, 'recover', recover), \
                patch.object(restore, 'take', take), patch('tools.native_spike.send.notify', lambda m: None), \
                self.assertRaises(Stop):
            managed._run_owned(transport, self.journal, broker, 'DP-2', 'DP-1', plan_outputs([], [], 'run'),
                               capture=['XR-1', 'XR-2', 'XR-3'], adopt=adopt_on, layouts=self.layouts)
        return saved

    def test_snapshot_before_adoption_and_restore_before_recovery(self):
        saved = self.run_owned(True)
        self.assertEqual(self.order, ['q', 'adopt', 'controls', ('restore', '0x1'), 'recover'])
        self.assertEqual(saved[0]['windows'][0]['address'], '0x1')  # on disk while the run is up
        self.assertIsNone(restore.load('run', self.layouts))       # gone once recovery completed

    def test_no_adoption_no_snapshot_no_restore(self):
        self.run_owned(False)
        self.assertEqual(self.order, ['controls', 'recover'])


if __name__ == '__main__':
    unittest.main()
