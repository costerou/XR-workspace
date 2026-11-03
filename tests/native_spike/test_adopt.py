"""Session start: the physical monitor's windows onto the XR desktops, by workspace (adopt.py)."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tools.native_spike import adopt
from tools.native_spike.geometry import MAX_DESKTOPS


def ws(i, monitor='DP-1', name=None):
    return {'id': i, 'name': str(i) if name is None else name, 'monitor': monitor}


def win(address, workspace, at=(0, 0), pinned=False, cls='kitty', title='term'):
    return {'address': address, 'workspace': {'id': workspace, 'name': str(workspace)}, 'at': list(at),
            'pinned': pinned, 'class': cls, 'title': title}


PANEL = dict(cls=adopt.PANEL_CLASS, title=adopt.PANEL_TITLE)
XR3 = ['XR-1', 'XR-2', 'XR-3']


class PlanTests(unittest.TestCase):
    def test_each_workspace_goes_to_its_own_desktop(self):
        moves = adopt.plan([ws(1), ws(2), ws(3)], [win('0x1', 1), win('0x2', 2), win('0x3', 3), win('0x4', 1)], 'DP-1', XR3)
        self.assertEqual(moves, {'workspaces': [(1, 'XR-1'), (2, 'XR-2'), (3, 'XR-3')], 'windows': []})

    def test_never_piles_onto_the_last_desktop(self):
        # Regression: the move used to put everything on the last desktop. A gap in the numbers skips no desktop.
        moves = adopt.plan([ws(1), ws(4), ws(7)], [win('0x1', 1), win('0x2', 4), win('0x3', 7)], 'DP-1', XR3)
        self.assertEqual(moves['workspaces'], [(1, 'XR-1'), (4, 'XR-2'), (7, 'XR-3')])
        moves = adopt.plan([ws(1), ws(2)], [win('0x1', 1), win('0x2', 2)], 'DP-1', XR3)
        self.assertEqual(moves['workspaces'], [(1, 'XR-1'), (2, 'XR-2')])

    def test_overflow_past_the_maximum_goes_to_the_last_desktop_only(self):
        names = [f'XR-{n}' for n in range(1, MAX_DESKTOPS + 1)]
        spaces = list(range(1, 8))
        moves = adopt.plan([ws(i) for i in spaces], [win(f'0x{i}', i) for i in spaces], 'DP-1', names)
        self.assertEqual(moves['workspaces'], [(1, 'XR-1'), (2, 'XR-2'), (3, 'XR-3'), (4, 'XR-4'), (5, 'XR-4'),
                                               (6, 'XR-4'), (7, 'XR-4')])

    def test_desktop_order_is_by_number_not_list_order(self):
        moves = adopt.plan([ws(1), ws(2)], [win('0x1', 1), win('0x2', 2)], 'DP-1', ['XR-2', 'XR-1'])
        self.assertEqual(moves['workspaces'], [(1, 'XR-1'), (2, 'XR-2')])

    def test_special_empty_and_other_monitor_workspaces_stay(self):
        spaces = [ws(1), ws(2), ws(-98, name='special:scratchpad'), ws(11, monitor='HDMI-A-1'), ws(3)]
        clients = [win('0x1', 1), win('0x2', -98), win('0x3', 11), win('0x4', 3)]
        moves = adopt.plan(spaces, clients, 'DP-1', XR3)
        self.assertEqual(moves, {'workspaces': [(1, 'XR-1'), (3, 'XR-2')], 'windows': []})  # 2 is empty

    def test_panel_and_pinned_windows_stay_and_the_rest_move_in_layout_order(self):
        clients = [win('0xa', 1, at=(960, 0)), win('0xf0', 1, at=(100, 100), **PANEL), win('0xb', 1, at=(0, 0)),
                   win('0xc', 1, at=(0, 540)), win('0xd', 2), win('0xe', 2, pinned=True)]
        moves = adopt.plan([ws(1), ws(2)], clients, 'DP-1', XR3)
        self.assertEqual(moves['workspaces'], [])
        self.assertEqual(moves['windows'], [('0xb', 'XR-1'), ('0xa', 'XR-1'), ('0xc', 'XR-1'), ('0xd', 'XR-2')])

    def test_a_workspace_with_only_the_panel_uses_no_desktop(self):
        moves = adopt.plan([ws(1), ws(2)], [win('0xf0', 1, **PANEL), win('0x2', 2)], 'DP-1', XR3)
        self.assertEqual(moves, {'workspaces': [(2, 'XR-1')], 'windows': []})
        self.assertEqual(adopt.plan([ws(1)], [win('0xf0', 1, **PANEL)], 'DP-1', XR3), {'workspaces': [], 'windows': []})

    def test_another_shell_window_is_not_the_panel(self):
        moves = adopt.plan([ws(1)], [win('0x1', 1, cls=adopt.PANEL_CLASS, title='Settings')], 'DP-1', XR3)
        self.assertEqual(moves['workspaces'], [(1, 'XR-1')])

    def test_the_panel_is_titled_xr_workspace_and_its_old_title_still_stays(self):
        # Renamed from "VITURE Workspace" (2026-11-03). A shell still running the old panel QML (rescanPlugins does not
        # reload it) shows the old title; that window must stay on the physical monitor too.
        self.assertEqual(adopt.PANEL_TITLE, 'XR Workspace')
        for title in ('XR Workspace', 'VITURE Workspace'):
            self.assertTrue(adopt.stays(win('0xf0', 1, cls=adopt.PANEL_CLASS, title=title)), title)
            self.assertEqual(adopt.plan([ws(1)], [win('0xf0', 1, cls=adopt.PANEL_CLASS, title=title)], 'DP-1', XR3),
                             {'workspaces': [], 'windows': []}, title)
        # Only the shell's own window: an app that happens to use either title still moves.
        for title in ('XR Workspace', 'VITURE Workspace'):
            self.assertFalse(adopt.stays(win('0x1', 1, cls='kitty', title=title)), title)
        self.assertFalse(adopt.stays(win('0x1', 1, cls=adopt.PANEL_CLASS, title='XR Workspace (2)')))

    def test_unknown_pinned_state_is_treated_as_pinned(self):
        bad = win('0x1', 1); del bad['pinned']
        self.assertEqual(adopt.plan([ws(1)], [bad, win('0x2', 1)], 'DP-1', XR3)['windows'], [('0x2', 'XR-1')])

    def test_invalid_input_is_rejected(self):
        for names in ([], ['DP-2'], ['XR-0'], ['XR-1; os.exit()']):
            with self.subTest(names=names), self.assertRaises(ValueError): adopt.plan([ws(1)], [win('0x1', 1)], 'DP-1', names)
        with self.assertRaises(ValueError): adopt.plan([ws(1)], [win('"); os.exit("', 1)], 'DP-1', XR3)
        with self.assertRaises(ValueError): adopt.plan(None, [], 'DP-1', XR3)


class DesktopCountTests(unittest.TestCase):
    def test_count_grows_for_workspaces_up_to_the_maximum(self):
        self.assertEqual(adopt.desktop_count(3, 2), 3)
        self.assertEqual(adopt.desktop_count(2, 3), 3)
        self.assertEqual(adopt.desktop_count(1, 9), MAX_DESKTOPS)
        self.assertEqual(adopt.desktop_count(4, 0), 4)

    def test_extra_desktops_copy_the_last_one_at_16_9(self):
        last = {'width': 2560, 'height': 1080, 'scale': 1.0}  # ultrawide
        out = adopt.extend_desktops([{'width': 1920, 'height': 1200, 'scale': 1.25}, last], 4)
        self.assertEqual(len(out), 4)
        self.assertEqual(out[2], out[3]); self.assertEqual(out[2]['height'], 1080); self.assertLess(out[2]['width'], 2560)
        same = {'width': 1920, 'height': 1080, 'scale': 1.0}
        self.assertEqual(adopt.extend_desktops([same], 2), [same, same])


class LuaShapeTests(unittest.TestCase):
    def test_one_eval_for_a_normal_plan(self):
        code = adopt.lua({'workspaces': [(1, 'XR-1'), (2, 'XR-2')], 'windows': [('0xab', 'XR-3')]})
        self.assertEqual(len(code), 1)
        self.assertIn('local C={{"0xab","XR-3"}} local W={{1,"XR-1"},{2,"XR-2"}}', code[0])
        self.assertLessEqual(len(code[0]), adopt.EVAL_LIMIT)
        self.assertEqual(adopt.lua({'workspaces': [], 'windows': []}), [])

    def test_a_large_plan_splits_under_the_eval_limit_and_keeps_every_move(self):
        windows = [(f'0x{i:012x}', 'XR-1') for i in range(300)]
        code = adopt.lua({'workspaces': [(5, 'XR-2')], 'windows': windows})
        self.assertGreater(len(code), 1)
        self.assertTrue(all(len(c) <= adopt.EVAL_LIMIT for c in code))
        joined = ''.join(code)
        self.assertTrue(all(f'"{a}"' in joined for a, _ in windows)); self.assertIn('{5,"XR-2"}', code[-1])

    def test_unvalidated_plans_never_reach_lua(self):
        for bad in ({'workspaces': [(0, 'XR-1')], 'windows': []}, {'workspaces': [('1', 'XR-1')], 'windows': []},
                    {'workspaces': [(1, 'DP-1')], 'windows': []}, {'workspaces': [], 'windows': [('x', 'XR-1')]},
                    {'workspaces': []}):
            with self.subTest(bad=bad), self.assertRaises(ValueError): adopt.lua(bad)


# A small stand-in for Hyprland's Lua API (the calls default/hypr/monitor-workspaces.lua makes) and Omarchy's
# per-monitor workspace blocks, to run the generated eval for real.
HARNESS = r'''
local state = STATE
local mons, spaces, wins, log = {}, {}, {}, {}
local function wsobj(id)
  local s = spaces[id]; if not s then return nil end
  local n = 0; for _, w in pairs(wins) do if w.ws == id then n = n + 1 end end
  local m = mons[s.mon]
  return { id = id, name = tostring(id), windows = n, monitor = m and {
    name = m.name, active_workspace = { id = m.active } } or nil }
end
for _, m in ipairs(state.monitors) do mons[m.name] = { name = m.name, active = m.active, block = m.block } end
for _, s in ipairs(state.workspaces) do spaces[s.id] = { mon = s.mon } end
for _, w in ipairs(state.windows) do wins[w.address] = { ws = w.ws } end
hl = { dsp = { workspace = {}, window = {} } }
function hl.get_workspace(id) return wsobj(id) end
function hl.get_monitor(name) local m = mons[name]; if m then return { name = m.name, active_workspace = wsobj(m.active) } end end
function hl.dsp.workspace.move(a) return function()
  local id = tonumber(a.workspace); local s = spaces[id]; local old = mons[s.mon]
  s.mon = a.monitor; mons[a.monitor].active = id
  if old.active == id then
    old.active = nil
    for k, v in pairs(spaces) do if v.mon == old.name and (not old.active or k < old.active) then old.active = k end end
    if not old.active then local n = 1000; while spaces[n] do n = n + 1 end; spaces[n] = { mon = old.name }; old.active = n end
  end
  log[#log + 1] = "move " .. id .. " " .. a.monitor end end
function hl.dsp.workspace.change_id(a) return function()
  local id = tonumber(a.workspace); assert(not spaces[a.id], "id taken")
  spaces[a.id] = spaces[id]; spaces[id] = nil
  for _, w in pairs(wins) do if w.ws == id then w.ws = a.id end end
  for _, m in pairs(mons) do if m.active == id then m.active = a.id end end
  log[#log + 1] = "renumber " .. id .. " " .. a.id end end
function hl.dsp.window.move(a) return function()
  assert(a.follow == false)
  wins[a.window:sub(9)].ws = tonumber(a.workspace)
  log[#log + 1] = "window " .. a.window:sub(9) .. " " .. a.workspace end end
function hl.dispatch(f) f() end
if state.omarchy then
  o = { workspaces = { workspace_id = function(m, slot) return mons[m.name].block * 10 + slot end } }
end
CODE
local function out()
  local r = { log = log, windows = {}, active = {}, spaces = {} }
  for a, w in pairs(wins) do r.windows[a] = w.ws end
  for n, m in pairs(mons) do r.active[n] = m.active end
  for id, s in pairs(spaces) do r.spaces[tostring(id)] = s.mon end
  return r
end
local function enc(v)
  if type(v) == "table" then
    local parts, arr = {}, #v > 0
    for k, x in pairs(v) do parts[#parts + 1] = arr and enc(x) or string.format("%q", tostring(k)) .. ":" .. enc(x) end
    if arr then return "[" .. table.concat(parts, ",") .. "]" end
    return "{" .. table.concat(parts, ",") .. "}"
  elseif type(v) == "string" then return string.format("%q", v) else return tostring(v) end
end
print(enc(out()))
'''


def lua_table(value):
    if isinstance(value, bool): return 'true' if value else 'false'
    if isinstance(value, (int, float)): return repr(value)
    if isinstance(value, str): return json.dumps(value)
    if isinstance(value, list): return '{' + ','.join(lua_table(v) for v in value) + '}'
    return '{' + ','.join(f'{k}={lua_table(v)}' for k, v in value.items()) + '}'


LUA = shutil.which('lua5.4') or shutil.which('lua')


@unittest.skipUnless(LUA, 'needs a Lua 5.4 interpreter')
class LuaRunTests(unittest.TestCase):
    def run_lua(self, state, moves):
        result = None
        for code in adopt.lua(moves):
            script = HARNESS.replace('STATE', lua_table(state)).replace('CODE', code)
            done = subprocess.run([LUA, '-'], input=script, capture_output=True, text=True, timeout=10)
            self.assertEqual(done.returncode, 0, done.stderr)
            result = json.loads(done.stdout)
        return result

    def state(self, omarchy=True):
        # DP-1 owns block 0 (1-10), XR-1..3 blocks 3..5; each XR desktop shows its own first slot, as Omarchy puts it.
        return {'omarchy': omarchy,
                'monitors': [{'name': 'DP-1', 'active': 1, 'block': 0}, {'name': 'XR-1', 'active': 31, 'block': 3},
                             {'name': 'XR-2', 'active': 41, 'block': 4}, {'name': 'XR-3', 'active': 51, 'block': 5}],
                'workspaces': [{'id': 1, 'mon': 'DP-1'}, {'id': 2, 'mon': 'DP-1'}, {'id': 3, 'mon': 'DP-1'},
                               {'id': 31, 'mon': 'XR-1'}, {'id': 41, 'mon': 'XR-2'}, {'id': 51, 'mon': 'XR-3'}],
                'windows': [{'address': '0xa', 'ws': 1}, {'address': '0xb', 'ws': 1}, {'address': '0xc', 'ws': 2},
                            {'address': '0xd', 'ws': 3}, {'address': '0xf0', 'ws': 3}]}

    def test_workspaces_move_whole_and_join_their_desktops_block_under_the_same_slot(self):
        r = self.run_lua(self.state(), {'workspaces': [(1, 'XR-1'), (2, 'XR-2')], 'windows': [('0xd', 'XR-3')]})
        self.assertEqual(r['windows'], {'0xa': 31, '0xb': 31, '0xc': 42, '0xd': 51, '0xf0': 3})
        self.assertEqual(r['active']['XR-1'], 31); self.assertEqual(r['active']['XR-2'], 42)
        self.assertEqual(r['spaces']['31'], 'XR-1'); self.assertEqual(r['spaces']['42'], 'XR-2')
        self.assertEqual(r['spaces']['3'], 'DP-1')   # the panel's workspace stays on the physical monitor
        self.assertEqual(r['active']['DP-1'], 3)
        self.assertEqual(r['log'][0], 'window 0xd 51')  # windows first: onto the desktop's own workspace

    def test_overflow_workspaces_stay_separate_on_the_last_desktop(self):
        r = self.run_lua(self.state(), {'workspaces': [(1, 'XR-1'), (2, 'XR-1'), (3, 'XR-1')], 'windows': []})
        self.assertEqual({r['windows'][a] for a in ('0xa', '0xb')}, {31})
        self.assertEqual(len({r['windows']['0xa'], r['windows']['0xc'], r['windows']['0xd']}), 3)
        self.assertTrue(all(30 < r['windows'][a] <= 40 for a in ('0xa', '0xc', '0xd')))

    def test_without_omarchy_workspaces_keep_their_numbers(self):
        r = self.run_lua(self.state(omarchy=False), {'workspaces': [(1, 'XR-1'), (2, 'XR-2')], 'windows': []})
        self.assertEqual(r['windows']['0xa'], 1); self.assertEqual(r['spaces']['1'], 'XR-1')
        self.assertEqual(r['spaces']['2'], 'XR-2')
        self.assertFalse(any(e.startswith('renumber') for e in r['log']))

    def test_a_failed_step_leaves_the_others_to_run(self):
        state = self.state(); state['windows'].append({'address': '0xz', 'ws': 2})
        moves = {'workspaces': [(9, 'XR-1'), (2, 'XR-2')], 'windows': [('0xdead', 'XR-3')]}  # 9 and 0xdead do not exist
        r = self.run_lua(state, moves)
        self.assertEqual(r['spaces']['42'], 'XR-2')


class Client:
    def __init__(self, workspaces, clients, fail=False):
        self.workspaces, self.clients, self.fail, self.adopted = workspaces, clients, fail, []
    def query(self, kind, timeout=None):
        return {'workspaces': self.workspaces, 'clients': self.clients}[kind]
    def adopt_windows(self, moves):
        if self.fail: raise ValueError('compositor rejected bind change')
        self.adopted.append(moves)


class AdoptTests(unittest.TestCase):
    def test_adopt_makes_one_request_with_the_plan(self):
        client = Client([ws(1), ws(2)], [win('0x1', 1), win('0x2', 2)])
        moves = adopt.adopt(client, 'DP-1', ['XR-1', 'XR-2'])
        self.assertEqual(client.adopted, [moves]); self.assertEqual(moves['workspaces'], [(1, 'XR-1'), (2, 'XR-2')])

    def test_nothing_to_move_makes_no_request(self):
        client = Client([ws(1)], [])
        adopt.adopt(client, 'DP-1', ['XR-1']); self.assertEqual(client.adopted, [])

    def test_a_failure_says_so_and_never_raises(self):
        said = []
        self.assertIsNone(adopt.adopt(Client([ws(1)], [win('0x1', 1)], fail=True), 'DP-1', ['XR-1'], notify=said.append))
        self.assertEqual(len(said), 1)

    def test_hyprland_client_evals_only_the_generated_lua(self):
        from tools.native_spike.hyprland import HyprlandClient
        client = HyprlandClient.__new__(HyprlandClient)
        sent = []
        with patch.object(HyprlandClient, '_eval', lambda self, code: sent.append(code)):
            client.adopt_windows({'workspaces': [(1, 'XR-1')], 'windows': []})
            with self.assertRaises(ValueError): client.adopt_windows({'workspaces': [(1, 'XR-1"}) os.exit()--')], 'windows': []})
        self.assertEqual(sent, adopt.lua({'workspaces': [(1, 'XR-1')], 'windows': []}))


class ManagedAdoptionTests(unittest.TestCase):
    """run_managed grows the desktops for the workspaces; _run_owned moves the windows once, before the renderer."""
    def setUp(self):
        from test_managed import ManagedTransport
        from tools.native_spike.journal import Journal
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'xr-workspace-spike' / 'run' / 'journal.json'
        self.path.parent.mkdir(parents=True, mode=0o700); self.path.parent.parent.chmod(0o700)
        self.journal = Journal.open(self.path, 'run', 'session')
        env = patch.dict(os.environ, {'XDG_STATE_HOME': str(Path(self.tmp.name) / 'state')})  # the layout snapshot
        env.start(); self.addCleanup(env.stop)
        spaces, clients = [ws(i, monitor='DP-1') for i in range(1, 7)], [win(f'0x{i}', i) for i in range(1, 7)]
        class Transport(ManagedTransport):
            def query(self, kind, timeout=None):
                if kind == 'workspaces': return spaces
                if kind == 'clients': return clients
                return super().query(kind)
        self.Transport = Transport

    def consent(self, **extra):
        from test_managed import INSTANCE
        return dict(instance=INSTANCE, run_id='new-run', output='DP-2', recovery_output='DP-1', **extra)

    def specs_for(self, consent, desktops):
        from tools.native_spike import managed
        seen = {}
        def owned(client, journal, broker, output, fallback, specs, **kw): seen.update(kw, specs=specs); return 'ok'
        with patch.object(managed, '_run_owned', owned):
            managed.run_managed(self.Transport(self.path), consent, Path(self.tmp.name), desktops=desktops)
        return seen

    def test_capture_run_keeps_its_three_desktops_whatever_the_workspace_count(self):
        # Six occupied workspaces: still the run's own three (its restored setup, profiles.load_setup); the rest go to XR-3.
        from tools.native_spike.supervisor import CAPTURE_SCOPE
        three = [{'width': 1920, 'height': 1080, 'scale': 1.0}] * 2 + [{'width': 2560, 'height': 1440, 'scale': 1.25}]
        seen = self.specs_for(self.consent(capture=CAPTURE_SCOPE), three)
        self.assertEqual([s['name'] for s in seen['specs']], ['XR-1', 'XR-2', 'XR-3'])
        self.assertEqual([(s['width'], s['scale']) for s in seen['specs']], [(1920, 1.0)] * 2 + [(2560, 1.25)])
        self.assertTrue(seen['adopt'])

    def test_test_pattern_run_is_unchanged(self):
        seen = self.specs_for(self.consent(), None)
        self.assertEqual(len(seen['specs']), 3); self.assertFalse(seen['adopt'])

    def test_windows_move_once_after_creation_and_before_the_renderer(self):
        from tools.native_spike import managed
        from tools.native_spike.ownership import OwnershipBrokerClient
        from tools.native_spike.hyprland import plan_outputs
        transport = self.Transport(self.path)
        broker = OwnershipBrokerClient(transport, 'run', 'session', 'DP-1'); broker.require_output_ownership()
        order = []
        transport.adopt_windows = lambda moves: order.append(('adopt', moves))
        transport.connect_wayland = lambda session: open(os.devnull, 'rb')
        class Stop(Exception): pass
        def controls(*a, **k): order.append(('controls',)); raise Stop
        specs = plan_outputs([], [], 'run')
        with patch.object(managed, 'view_controls', controls), patch.object(managed, 'recover'), \
                patch('tools.native_spike.send.notify', lambda m: order.append(('notify', m))), self.assertRaises(Stop):
            managed._run_owned(transport, self.journal, broker, 'DP-2', 'DP-1', specs, capture=['XR-1', 'XR-2', 'XR-3'],
                               adopt=True)
        self.assertEqual([o[0] for o in order], ['adopt', 'controls'])
        self.assertEqual(order[0][1]['workspaces'], [(1, 'XR-1'), (2, 'XR-2'), (3, 'XR-3'), (4, 'XR-3'), (5, 'XR-3'),
                                                     (6, 'XR-3')])

    def test_adoption_failure_does_not_stop_the_run(self):
        from tools.native_spike import managed
        from tools.native_spike.ownership import OwnershipBrokerClient
        from tools.native_spike.hyprland import plan_outputs
        transport = self.Transport(self.path)
        broker = OwnershipBrokerClient(transport, 'run', 'session', 'DP-1'); broker.require_output_ownership()
        def boom(moves): raise OSError('socket gone')
        transport.adopt_windows = boom
        transport.connect_wayland = lambda session: open(os.devnull, 'rb')
        reached, said = [], []
        class Stop(Exception): pass
        def controls(*a, **k): reached.append(True); raise Stop
        with patch.object(managed, 'view_controls', controls), patch.object(managed, 'recover'), \
                patch('tools.native_spike.send.notify', said.append), self.assertRaises(Stop):
            managed._run_owned(transport, self.journal, broker, 'DP-2', 'DP-1', plan_outputs([], [], 'run'),
                               capture=['XR-1'], adopt=True)
        self.assertEqual(reached, [True]); self.assertEqual(len(said), 1)


if __name__ == '__main__':
    unittest.main()
