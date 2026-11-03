"""Window memory: every window goes back to the XR desktop, workspace and place it had (window_memory.py).

Offline: fixtures shaped like Hyprland's j/clients, j/workspaces and j/monitors, temporary files, a real Lua interpreter for
the generated evals (test_window_memory_lua.py) and nothing else. No test here is a glasses pass."""
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock

from tools.native_spike import window_memory as wm

MODEL = 'luma-ultra'
XR = ['XR-1', 'XR-2', 'XR-3']


def mon(name, x=0, y=0, width=1920, height=1200, scale=1.0, active=1, reserved=(0, 26, 0, 0)):
    return {'name': name, 'x': x, 'y': y, 'width': width, 'height': height, 'scale': scale, 'reserved': list(reserved),
            'activeWorkspace': {'id': active, 'name': str(active)}, 'focused': False}


def ws(i, monitor, layout='master', windows=1):
    return {'id': i, 'name': str(i), 'monitor': monitor, 'tiledLayout': layout, 'windows': windows}


def win(address, workspace, at=(0, 0), size=(800, 600), cls='kitty', title='term', initial=None, pid=100, floating=False,
        fullscreen=0, client=None, pinned=False, grouped=(), mapped=True):
    return {'address': address, 'workspace': {'id': workspace, 'name': str(workspace)}, 'at': list(at), 'size': list(size),
            'class': cls, 'title': title, 'initialTitle': title if initial is None else initial, 'pid': pid, 'floating': floating,
            'fullscreen': fullscreen, 'fullscreenClient': fullscreen if client is None else client, 'pinned': pinned,
            'grouped': list(grouped), 'mapped': mapped, 'hidden': False}


MONITORS = [mon('DP-1', active=1), mon('XR-1', x=4096, active=61), mon('XR-2', x=6016, active=71), mon('XR-3', x=7936, active=81)]


def record(address='0xa', output='XR-1', index=0, workspace=61, cls='kitty', title='term', initial='term', pid=100, layout='master',
           floating=False, at=(0, 26), size=(800, 600), fullscreen=0, client=0, order=0, column=0, parked=False, landing=None):
    return {'address': address, 'class': cls, 'title': title, 'initial_title': initial, 'pid': pid, 'output': output, 'index': index,
            'workspace': workspace, 'layout': layout, 'floating': floating, 'at': list(at), 'size': list(size),
            'fullscreen': fullscreen, 'client': client, 'order': order, 'column': column, 'parked': parked, 'landing': landing}


def memory(*windows, instance='inst', desktops=None):
    return {'instance': instance, 'windows': list(windows), 'desktops': desktops if desktops is not None else {
        n: {'width': 1920, 'height': 1200, 'scale': 1.0, 'active': None, 'placement': None, 'roll': None} for n in XR}}


class CaptureTests(unittest.TestCase):
    def capture(self, clients, spaces, **kw):
        return wm.capture(clients, spaces, MONITORS, XR, instance='inst', **kw)

    def test_records_where_each_window_is(self):
        m = self.capture([win('0xa', 61, at=(4106, 36), size=(900, 1150), cls='foot', title='vim', initial='foot', pid=7)],
                         [ws(61, 'XR-1', 'scrolling')])
        self.assertEqual(m['instance'], 'inst')
        self.assertEqual(len(m['windows']), 1)
        w = m['windows'][0]
        self.assertEqual((w['address'], w['class'], w['title'], w['initial_title'], w['pid']), ('0xa', 'foot', 'vim', 'foot', 7))
        self.assertEqual((w['output'], w['index'], w['workspace'], w['layout']), ('XR-1', 0, 61, 'scrolling'))
        self.assertEqual(w['at'], [10, 36])  # relative to its desktop: the desktops' origin can change between runs
        self.assertEqual(w['size'], [900, 1150])
        self.assertFalse(w['floating'])
        self.assertFalse(w['parked'])

    def test_only_windows_on_the_runs_desktops(self):
        m = self.capture([win('0xa', 61), win('0xb', 1), win('0xc', -98)],
                         [ws(61, 'XR-1'), ws(1, 'DP-1'), {'id': -98, 'name': 'special:s', 'monitor': 'XR-1'}])
        self.assertEqual([w['address'] for w in m['windows']], ['0xa'])

    def test_pinned_panel_and_unmapped_windows_are_not_remembered(self):
        m = self.capture([win('0xa', 61, pinned=True), win('0xb', 61, cls='org.quickshell', title='XR Workspace'),
                          win('0xc', 61, mapped=False), win('0xd', 61)], [ws(61, 'XR-1')])
        self.assertEqual([w['address'] for w in m['windows']], ['0xd'])

    def test_a_malformed_window_is_skipped(self):
        m = self.capture([win('nope', 61), {'address': '0xb'}, 'junk', win('0xc', 61)], [ws(61, 'XR-1')])
        self.assertEqual([w['address'] for w in m['windows']], ['0xc'])

    def test_tiled_order_is_left_to_right_then_top_to_bottom(self):
        m = self.capture([win('0xa', 61, at=(5000, 26)), win('0xb', 61, at=(4100, 500)), win('0xc', 61, at=(4100, 26))],
                         [ws(61, 'XR-1')])
        self.assertEqual([(w['address'], w['order']) for w in m['windows']], [('0xc', 0), ('0xb', 1), ('0xa', 2)])

    def test_scrolling_columns_are_windows_sharing_an_x(self):
        m = self.capture([win('0xa', 61, at=(4100, 26)), win('0xb', 61, at=(4100, 500)), win('0xc', 61, at=(5000, 26)),
                          win('0xd', 61, at=(5900, 26))], [ws(61, 'XR-1', 'scrolling')])
        self.assertEqual([w['column'] for w in m['windows']], [0, 0, 1, 2])

    def test_a_column_scrolled_off_the_desktop_keeps_its_place(self):
        m = self.capture([win('0xa', 61, at=(2000, 26)), win('0xb', 61, at=(4100, 26)), win('0xc', 61, at=(9000, 26))],
                         [ws(61, 'XR-1', 'scrolling')])
        self.assertEqual([(w['address'], w['column']) for w in m['windows']], [('0xa', 0), ('0xb', 1), ('0xc', 2)])

    def test_floating_and_fullscreen_windows_have_no_tile_order(self):
        m = self.capture([win('0xa', 61, floating=True), win('0xb', 61, fullscreen=2, client=1), win('0xc', 61, at=(4100, 26))],
                         [ws(61, 'XR-1')])
        by = {w['address']: w for w in m['windows']}
        self.assertIsNone(by['0xa']['order'])
        self.assertTrue(by['0xa']['floating'])
        self.assertIsNone(by['0xb']['order'])
        self.assertEqual((by['0xb']['fullscreen'], by['0xb']['client']), (2, 1))
        self.assertEqual(by['0xc']['order'], 0)

    def test_grouped_windows_stay_out_of_the_tile_order(self):
        m = self.capture([win('0xa', 61, grouped=('0xa', '0xb')), win('0xb', 61, grouped=('0xa', '0xb'))], [ws(61, 'XR-1')])
        self.assertEqual([w['order'] for w in m['windows']], [None, None])

    def test_each_desktop_keeps_its_context(self):
        placed = {'XR-2': {'placement': {'x': 300, 'y': -120, 'distance': 90, 'curvature': 40}, 'roll': 450}}
        m = wm.capture([win('0xa', 71, at=(6016, 26))], [ws(71, 'XR-2')],
                       [mon('XR-2', x=6016, width=2560, height=1440, scale=1.25, active=73)], ['XR-2'], context=placed)
        self.assertEqual(m['desktops']['XR-2'], {'width': 2560, 'height': 1440, 'scale': 1.25, 'active': 73,
                                                 'placement': placed['XR-2']['placement'], 'roll': 450})

    def test_index_is_the_position_among_the_runs_desktops(self):
        # XR-2 was removed: the next start names XR-3's desktop XR-2 (hyprland.plan_outputs numbers by position).
        m = wm.capture([win('0xa', 81, at=(7936, 26))], [ws(81, 'XR-3')], MONITORS, ['XR-1', 'XR-3'])
        self.assertEqual((m['windows'][0]['output'], m['windows'][0]['index']), ('XR-3', 1))

    def test_titles_are_cut_to_a_bounded_length(self):
        m = self.capture([win('0xa', 61, title='x' * 5000, initial='y' * 5000, cls='z' * 5000)], [ws(61, 'XR-1')])
        w = m['windows'][0]
        self.assertTrue(all(len(w[k]) == wm.TEXT_LIMIT for k in ('title', 'initial_title', 'class')))

    def test_windows_are_bounded(self):
        clients = [win(f'0x{i:x}', 61) for i in range(1, wm.MAX_WINDOWS + 20)]
        self.assertEqual(len(self.capture(clients, [ws(61, 'XR-1')])['windows']), wm.MAX_WINDOWS)

    def test_what_it_makes_validates(self):
        m = self.capture([win('0xa', 61, floating=True), win('0xb', 71, at=(6016, 26))], [ws(61, 'XR-1'), ws(71, 'XR-2')])
        self.assertEqual(wm.validate(m), m)

    def test_garbage_is_refused(self):
        with self.assertRaises(ValueError): wm.capture('no', [], [], XR)


class StoreTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.path = self.dir / 'window-memory.json'
        self.store = wm.Store(self.path, MODEL)

    def test_sits_beside_the_profiles(self):
        self.assertEqual(wm.memory_path(self.dir / 'profiles.json'), self.path)

    def test_round_trip(self):
        m = memory(record())
        self.store.save(m)
        self.assertEqual(self.store.load(), m)

    def test_private_and_atomic(self):
        self.store.save(memory(record()))
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual([p.name for p in self.dir.iterdir()], ['window-memory.json'])

    def test_a_failed_write_keeps_the_old_file_and_leaves_no_temporary(self):
        self.store.save(memory(record()))
        before = self.path.read_bytes()
        with mock.patch.object(wm.os, 'replace', side_effect=OSError('disk')), self.assertRaises(OSError):
            self.store.save(memory(record(address='0xb')))
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual([p.name for p in self.dir.iterdir()], ['window-memory.json'])

    def test_each_model_keeps_its_own(self):
        other = wm.Store(self.path, 'beast')
        other.save(memory(record(address='0xb')))
        self.store.save(memory(record(address='0xa')))
        self.assertEqual(other.load()['windows'][0]['address'], '0xb')
        self.assertEqual(self.store.load()['windows'][0]['address'], '0xa')

    def test_unknown_model_is_refused(self):
        with self.assertRaises(ValueError): wm.Store(self.path, 'nope')

    def test_nothing_saved(self):
        self.assertIsNone(self.store.load())

    def test_a_corrupt_file_reads_as_nothing(self):
        for text in ('{not json', '[]', '{"schema": 99, "models": {}}', '{"schema": 1, "models": 3}',
                     json.dumps({'schema': wm.SCHEMA, 'models': {MODEL: {'instance': 'x'}}})):
            with self.subTest(text=text):
                self.path.write_text(text)
                self.assertIsNone(self.store.load())

    def test_invalid_windows_read_as_nothing(self):
        bad = [{**record(), 'address': 'rm -rf'}, {**record(), 'output': 'DP-1'}, {**record(), 'workspace': 0},
               {**record(), 'at': [1]}, {**record(), 'size': ['a', 1]}, {**record(), 'fullscreen': 9}, {**record(), 'extra': 1},
               {**record(), 'layout': 'a"b'}, {**record(), 'order': -1}, {**record(), 'parked': 'yes'}]
        for w in bad:
            with self.subTest(w=w):
                self.path.write_text(json.dumps({'schema': wm.SCHEMA, 'models': {MODEL: memory(w)}}))
                self.assertIsNone(self.store.load())

    def test_a_save_over_a_corrupt_file_works(self):
        self.path.write_text('{not json')
        self.store.save(memory(record()))
        self.assertEqual(len(self.store.load()['windows']), 1)

    def test_an_oversized_file_is_not_read(self):
        self.path.write_text(' ' * (wm.FILE_LIMIT + 1))
        self.assertIsNone(self.store.load())


class MatchTests(unittest.TestCase):
    def pairs(self, records, clients, same=True, exact=False):
        return [(r['address'], c['address']) for r, c in wm.match(records, clients, same, exact=exact)]

    def test_the_same_address_in_the_same_session(self):
        self.assertEqual(self.pairs([record('0xa', title='old')], [win('0xb', 1, title='old'), win('0xa', 1, title='new')]),
                         [('0xa', '0xa')])

    def test_an_address_from_another_compositor_session_means_nothing(self):
        self.assertEqual(self.pairs([record('0xa', title='old')], [win('0xb', 1, title='old'), win('0xa', 1, title='new')], same=False),
                         [('0xa', '0xb')])

    def test_a_reused_address_with_another_class_is_not_the_same_window(self):
        self.assertEqual(self.pairs([record('0xa', cls='foot')], [win('0xa', 1, cls='firefox')]), [])

    def test_class_and_title(self):
        self.assertEqual(self.pairs([record('0xa', cls='foot', title='vim')],
                                    [win('0xb', 1, cls='foot', title='zsh'), win('0xc', 1, cls='foot', title='vim')]),
                         [('0xa', '0xc')])

    def test_class_and_initial_title_when_the_title_changed(self):
        self.assertEqual(self.pairs([record('0xa', cls='foot', title='vim', initial='foot-a', pid=5)],
                                    [win('0xb', 1, cls='foot', title='x', initial='foot-b', pid=6),
                                     win('0xc', 1, cls='foot', title='y', initial='foot-a', pid=6)]), [('0xa', '0xc')])

    def test_class_initial_title_and_pid_beat_class_and_initial_title(self):
        self.assertEqual(self.pairs([record('0xa', cls='foot', title='a', initial='foot', pid=5)],
                                    [win('0xb', 1, cls='foot', title='b', initial='foot', pid=6),
                                     win('0xc', 1, cls='foot', title='c', initial='foot', pid=5)]), [('0xa', '0xc')])

    def test_class_and_pid(self):
        self.assertEqual(self.pairs([record('0xa', cls='foot', title='a', initial='x', pid=5)],
                                    [win('0xb', 1, cls='foot', title='b', initial='y', pid=6),
                                     win('0xc', 1, cls='foot', title='c', initial='z', pid=5)]), [('0xa', '0xc')])

    def test_class_alone_is_the_last_resort(self):
        self.assertEqual(self.pairs([record('0xa', cls='foot', title='a', initial='x', pid=5)],
                                    [win('0xb', 1, cls='bar', title='a'), win('0xc', 1, cls='foot', title='c', initial='z', pid=6)]),
                         [('0xa', '0xc')])

    def test_nothing_alike_stays_unmatched(self):
        self.assertEqual(self.pairs([record('0xa', cls='foot')], [win('0xb', 1, cls='bar')]), [])

    def test_a_better_match_goes_first_whatever_the_order(self):
        # The first record only fits by class; if it took the window first, the second would lose its exact title.
        records = [record('0xa', cls='foot', title='other', initial='i', pid=1), record('0xb', cls='foot', title='vim', initial='j', pid=2)]
        self.assertEqual(sorted(self.pairs(records, [win('0xc', 1, cls='foot', title='vim', initial='k', pid=3),
                                                     win('0xd', 1, cls='foot', title='zsh', initial='l', pid=4)])),
                         [('0xa', '0xd'), ('0xb', '0xc')])

    def test_no_window_is_assigned_twice(self):
        records = [record(f'0x{i}', cls='foot', title='same', order=i) for i in range(1, 4)]
        found = self.pairs(records, [win('0xa', 1, cls='foot', title='same'), win('0xb', 1, cls='foot', title='same')])
        self.assertEqual(len(found), 2)
        self.assertEqual(len({c for _, c in found}), 2)
        self.assertEqual(len({r for r, _ in found}), 2)

    def test_no_record_is_assigned_twice(self):
        found = self.pairs([record('0xa', cls='foot', title='same')],
                           [win('0xb', 1, cls='foot', title='same'), win('0xc', 1, cls='foot', title='same')])
        self.assertEqual(len(found), 1)

    def test_identical_windows_keep_their_order(self):
        records = [record(f'0x{i}', cls='foot', title='same', order=i) for i in (1, 2, 3)]
        clients = [win('0xc', 5, at=(300, 0), cls='foot', title='same'), win('0xa', 5, at=(100, 0), cls='foot', title='same'),
                   win('0xb', 5, at=(200, 0), cls='foot', title='same')]
        self.assertEqual(self.pairs(records, clients, same=False), [('0x1', '0xa'), ('0x2', '0xb'), ('0x3', '0xc')])

    def test_windows_without_a_class_only_match_by_address(self):
        self.assertEqual(self.pairs([record('0xa', cls='', title='t')], [win('0xb', 1, cls='', title='t')]), [])
        self.assertEqual(self.pairs([record('0xa', cls='', title='t')], [win('0xa', 1, cls='', title='t')]), [('0xa', '0xa')])

    def test_exact_takes_the_address_only(self):
        self.assertEqual(self.pairs([record('0xa', cls='foot', title='same')], [win('0xb', 1, cls='foot', title='same')], exact=True), [])


class PlanTests(unittest.TestCase):
    def plan(self, mem, clients, spaces, names=XR, source='DP-1', monitors=MONITORS, **kw):
        return wm.plan(mem, clients, spaces, monitors, names, source, instance='inst', **kw)

    def test_nothing_remembered_moves_nothing(self):
        p = self.plan(memory(), [win('0xa', 1)], [ws(1, 'DP-1')])
        self.assertEqual((p['whole'], p['windows']), ([], []))
        self.assertTrue(wm.empty(p))

    def test_a_workspace_holding_exactly_a_remembered_one_moves_whole(self):
        p = self.plan(memory(record('0xa', 'XR-2', 1, 73), record('0xb', 'XR-2', 1, 73, order=1)),
                      [win('0xa', 1), win('0xb', 1)], [ws(1, 'DP-1')])
        self.assertEqual(p['whole'], [(1, 'XR-2', 73)])
        self.assertEqual(p['windows'], [])

    def test_windows_split_over_desktops_move_one_by_one(self):
        p = self.plan(memory(record('0xa', 'XR-1', 0, 61), record('0xb', 'XR-3', 2, 82)),
                      [win('0xa', 1), win('0xb', 1)], [ws(1, 'DP-1')])
        self.assertEqual(p['whole'], [])
        self.assertEqual(p['windows'], [('0xa', 'XR-1', 61, 0), ('0xb', 'XR-3', 82, 0)])

    def test_a_whole_workspace_keeps_its_tree_and_others_join_it(self):
        p = self.plan(memory(record('0xa', 'XR-1', 0, 61), record('0xb', 'XR-1', 0, 61, order=1), record('0xc', 'XR-1', 0, 61, order=2)),
                      [win('0xa', 1), win('0xb', 1), win('0xc', 2)], [ws(1, 'DP-1'), ws(2, 'DP-1')])
        self.assertEqual(p['whole'], [(1, 'XR-1', 61)])
        self.assertEqual(p['windows'], [('0xc', 'XR-1', 61, 0)])

    def test_two_workspaces_for_one_target_the_larger_moves_whole(self):
        p = self.plan(memory(record('0xa', 'XR-1', 0, 61), record('0xb', 'XR-1', 0, 61, order=1), record('0xc', 'XR-1', 0, 61, order=2)),
                      [win('0xc', 1), win('0xa', 2), win('0xb', 2)], [ws(1, 'DP-1'), ws(2, 'DP-1')])
        self.assertEqual(p['whole'], [(2, 'XR-1', 61)])
        self.assertEqual(p['windows'], [('0xc', 'XR-1', 61, 0)])

    def test_an_unmatched_window_keeps_its_workspace_for_adoption(self):
        p = self.plan(memory(record('0xa', 'XR-2', 1, 71)), [win('0xa', 1), win('0xb', 1, cls='other')], [ws(1, 'DP-1')])
        self.assertEqual(p['whole'], [])
        self.assertEqual(p['windows'], [('0xa', 'XR-2', 71, 0)])

    def test_windows_that_stay_are_never_taken(self):
        mem = memory(record('0xa', cls='org.quickshell', title='XR Workspace'), record('0xb', title='p', order=1))
        p = self.plan(mem, [win('0xa', 1, cls='org.quickshell', title='XR Workspace'), win('0xb', 1, title='p', pinned=True), win('0xc', 1, cls='x')],
                      [ws(1, 'DP-1')])
        self.assertEqual((p['whole'], p['windows']), ([], []))

    def test_a_workspace_with_a_window_that_stays_is_not_whole(self):
        p = self.plan(memory(record('0xa', 'XR-1', 0, 61)), [win('0xa', 1), win('0xb', 1, pinned=True)], [ws(1, 'DP-1')])
        self.assertEqual(p['whole'], [])
        self.assertEqual(p['windows'], [('0xa', 'XR-1', 61, 0)])

    def test_only_windows_on_the_source_monitor_are_taken(self):
        p = self.plan(memory(record('0xa')), [win('0xa', 21)], [ws(21, 'DP-2')])
        self.assertTrue(wm.empty(p))

    def test_a_missing_desktop_goes_to_the_last_one_as_its_own_workspace(self):
        p = self.plan(memory(record('0xa', 'XR-4', 3, 91)), [win('0xa', 1), win('0xb', 2)], [ws(1, 'DP-1'), ws(2, 'DP-1')])
        self.assertEqual(p['whole'], [(1, 'XR-3', 91)])
        self.assertEqual(p['placed'][0]['moved'], True)

    def test_a_missing_desktops_loose_windows_are_flagged_so_they_do_not_merge(self):
        p = self.plan(memory(record('0xa', 'XR-4', 3, 91)), [win('0xa', 1), win('0xb', 1, cls='other')], [ws(1, 'DP-1')])
        self.assertEqual(p['windows'], [('0xa', 'XR-3', 91, 1)])

    def test_a_position_outlives_a_rename(self):
        # Saved with XR-1 and XR-3: this run calls that second desktop XR-2.
        p = self.plan(memory(record('0xa', 'XR-3', 1, 81)), [win('0xa', 1)], [ws(1, 'DP-1')], names=['XR-1', 'XR-2'])
        self.assertEqual(p['whole'], [(1, 'XR-2', 81)])
        self.assertFalse(p['placed'][0]['moved'])

    def test_a_parked_window_goes_home_when_its_desktop_exists(self):
        p = self.plan(memory(record('0xa', 'XR-3', None, 81, parked=True, landing='XR-2')), [win('0xa', 1)], [ws(1, 'DP-1')])
        self.assertEqual(p['whole'], [(1, 'XR-3', 81)])
        self.assertFalse(p['placed'][0]['moved'])

    def test_a_parked_window_without_its_desktop_goes_to_the_last_one(self):
        p = self.plan(memory(record('0xa', 'XR-4', None, 91, parked=True, landing='XR-3')), [win('0xa', 1)], [ws(1, 'DP-1')])
        self.assertEqual(p['whole'], [(1, 'XR-3', 91)])
        self.assertTrue(p['placed'][0]['moved'])

    def test_own_groups_claim_their_workspaces_before_moved_ones(self):
        p = self.plan(memory(record('0xa', 'XR-4', 3, 91), record('0xb', 'XR-3', 2, 81)),
                      [win('0xa', 1), win('0xb', 2)], [ws(1, 'DP-1'), ws(2, 'DP-1')])
        self.assertEqual(p['whole'], [(2, 'XR-3', 81), (1, 'XR-3', 91)])

    def test_a_floating_window_is_placed_relative_to_its_desktop_now(self):
        mons = [mon('DP-1'), mon('XR-1', x=5000, y=100)]
        p = self.plan(memory(record('0xa', floating=True, at=(40, 60), size=(500, 400), order=None)), [win('0xa', 1, floating=True)], [ws(1, 'DP-1')],
                      names=['XR-1'], monitors=mons)
        self.assertEqual(p['state'], [('0xa', 1, 5040, 160, 500, 400)])

    def test_a_floating_window_is_pulled_inside_a_smaller_desktop(self):
        mons = [mon('DP-1'), mon('XR-1', x=5000, width=1280, height=720)]
        p = self.plan(memory(record('0xa', floating=True, at=(1500, 900), size=(500, 400), order=None)), [win('0xa', 1)], [ws(1, 'DP-1')],
                      names=['XR-1'], monitors=mons)
        self.assertEqual(p['state'], [('0xa', 1, 5000 + 1280 - 500, 720 - 400, 500, 400)])

    def test_a_floating_window_larger_than_the_desktop_shrinks(self):
        mons = [mon('DP-1'), mon('XR-1', x=5000, width=1280, height=720)]
        p = self.plan(memory(record('0xa', floating=True, at=(0, 26), size=(1600, 900), order=None)), [win('0xa', 1)], [ws(1, 'DP-1')],
                      names=['XR-1'], monitors=mons)
        self.assertEqual(p['state'], [('0xa', 1, 5000, 26, 1280, 694)])

    def test_a_window_floated_since_goes_back_to_tiled(self):
        p = self.plan(memory(record('0xa')), [win('0xa', 1, floating=True)], [ws(1, 'DP-1')])
        self.assertEqual(p['state'], [('0xa', 0, 0, 0, 0, 0)])

    def test_a_tiled_window_needs_no_state_step(self):
        p = self.plan(memory(record('0xa')), [win('0xa', 1)], [ws(1, 'DP-1')])
        self.assertEqual(p['state'], [])

    def test_fullscreen_comes_back_last(self):
        p = self.plan(memory(record('0xa', fullscreen=2, client=1, order=None)), [win('0xa', 1)], [ws(1, 'DP-1')])
        self.assertEqual(p['fullscreen'], [('0xa', 2, 1)])
        self.assertEqual(p['order'], [])

    def test_tile_order_is_per_workspace_in_saved_order(self):
        p = self.plan(memory(record('0xb', 'XR-1', 0, 61, order=1, column=0, size=(800, 500)), record('0xa', 'XR-1', 0, 61, order=0, column=0, size=(800, 600)),
                             record('0xc', 'XR-2', 1, 71, order=0, column=0, layout='scrolling', size=(700, 900)), record('0xd', 'XR-2', 1, 71, order=1, column=1, layout='scrolling', size=(900, 900))),
                      [win('0xa', 1), win('0xb', 1), win('0xc', 2), win('0xd', 2)], [ws(1, 'DP-1'), ws(2, 'DP-1')])
        self.assertEqual(p['order'], [[('0xa', 0, 800, 600), ('0xb', 0, 800, 500)], [('0xc', 0, 700, 900), ('0xd', 1, 900, 900)]])

    def test_tiled_sizes_are_not_forced_onto_a_desktop_of_another_size(self):
        mons = [mon('DP-1'), mon('XR-1', x=5000, width=2560, height=1080)]
        p = self.plan(memory(record('0xa', size=(800, 600)), record('0xb', size=(800, 600), order=1, layout='scrolling', column=1)),
                      [win('0xa', 1), win('0xb', 1)], [ws(1, 'DP-1')], names=['XR-1'], monitors=mons)
        self.assertEqual(p['order'], [[('0xa', 0, 0, 0), ('0xb', 1, 1067, 0)]])  # a column keeps its share of the width

    def test_the_layout_of_each_workspace_comes_along(self):
        p = self.plan(memory(record('0xa', layout='scrolling')), [win('0xa', 1)], [ws(1, 'DP-1')])
        self.assertEqual(p['layouts'], [('0xa', 'scrolling')])

    def test_each_desktop_shows_the_workspace_it_showed(self):
        d = {n: {'width': 1920, 'height': 1200, 'scale': 1.0, 'active': 62 if n == 'XR-1' else None, 'placement': None, 'roll': None} for n in XR}
        p = self.plan(memory(record('0xa', workspace=61), record('0xb', workspace=62), desktops=d),
                      [win('0xa', 1), win('0xb', 2)], [ws(1, 'DP-1'), ws(2, 'DP-1')])
        self.assertEqual(p['shown'], [('XR-1', '0xb')])

    def test_focus_goes_back(self):
        p = self.plan(memory(record('0xa')), [win('0xa', 1)], [ws(1, 'DP-1')], active={'address': '0xa', 'monitor': 'DP-1'})
        self.assertEqual((p['focus'], p['monitor']), ('0xa', 'DP-1'))

    def test_names_must_be_outputs(self):
        with self.assertRaises(ValueError): self.plan(memory(), [], [], names=['DP-1'])
        with self.assertRaises(ValueError): self.plan(memory(), [], [], names=[])


class Client:
    """A compositor that answers from `state` and records what it is told."""
    instance = 'inst'

    def __init__(self, clients=(), spaces=(), monitors=MONITORS, active=None):
        self.state = {'clients': list(clients), 'workspaces': list(spaces), 'monitors': list(monitors), 'activewindow': active or {}}
        self.placed, self.queries, self.fail = [], [], None

    def query(self, kind, timeout=None):
        self.queries.append(kind)
        if self.fail is not None: raise self.fail
        return self.state[kind]

    def place_windows(self, placement, phase):
        self.placed.append((phase, placement))


class Tracked(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.store = wm.Store(self.dir / 'window-memory.json', MODEL)
        self.now = 100.0

    def clock(self):
        return self.now

    def tracker(self, client, names=XR, **kw):
        t = wm.WindowTracker(client, self.store, 'DP-1', names, clock=self.clock, threads=False, **kw)
        t.delay = 0
        return t


class TrackerTests(Tracked):
    def test_a_mark_saves_after_the_delay(self):
        c = Client([win('0xa', 61, at=(4100, 26))], [ws(61, 'XR-1')])
        t = wm.WindowTracker(c, self.store, 'DP-1', XR, clock=self.clock, threads=False)
        t.mark()
        t.poll(self.now + wm.CAPTURE_DELAY_S - 0.01)
        self.assertEqual(c.queries, [])
        t.poll(self.now + wm.CAPTURE_DELAY_S)
        self.assertEqual([w['address'] for w in self.store.load()['windows']], ['0xa'])

    def test_marks_during_the_delay_are_one_capture(self):
        c = Client([win('0xa', 61)], [ws(61, 'XR-1')])
        t = self.tracker(c)
        t.mark(); t.mark(); t.mark()
        t.poll(self.now + 1); t.poll(self.now + 2)
        self.assertEqual(c.queries, ['clients', 'workspaces', 'monitors'])

    def test_nothing_marked_reads_nothing(self):
        c = Client([win('0xa', 61)], [ws(61, 'XR-1')])
        self.tracker(c).poll(self.now + 100)
        self.assertEqual(c.queries, [])

    def test_the_memory_is_only_written_when_it_changed(self):
        c = Client([win('0xa', 61)], [ws(61, 'XR-1')])
        t = self.tracker(c)
        t.mark(); t.poll(self.now)
        with mock.patch.object(self.store, 'save') as save:
            t.mark(); t.poll(self.now)
            save.assert_not_called()
            c.state['clients'][0]['at'] = [4200, 26]
            t.mark(); t.poll(self.now)
            save.assert_called_once()

    def test_the_reads_never_run_on_the_callers_thread(self):
        import threading
        seen = []
        c = Client([win('0xa', 61)], [ws(61, 'XR-1')])
        real = c.query
        c.query = lambda kind, timeout=None: (seen.append(threading.current_thread()), real(kind))[1]
        t = wm.WindowTracker(c, self.store, 'DP-1', XR, clock=self.clock)
        t.delay = 0
        t.mark(); t.poll(self.now); t.join()
        self.assertTrue(seen)
        self.assertNotIn(threading.current_thread(), seen)

    def test_only_one_worker_at_a_time(self):
        c = Client([win('0xa', 61)], [ws(61, 'XR-1')])
        t = wm.WindowTracker(c, self.store, 'DP-1', XR, clock=self.clock)
        t.delay = 0
        import threading
        hold = threading.Event()
        started = []
        real = c.query
        def slow(kind, timeout=None):
            started.append(kind); hold.wait(2); return real(kind)
        c.query = slow
        t.mark(); t.poll(self.now)
        t.mark(); t.poll(self.now)
        hold.set(); t.join()
        self.assertEqual(started.count('clients'), 1)
        t.poll(self.now)  # still marked: it goes next
        t.join()
        self.assertEqual(started.count('clients'), 2)

    def test_a_failed_read_keeps_what_was_saved_and_tries_again(self):
        c = Client([win('0xa', 61)], [ws(61, 'XR-1')])
        t = self.tracker(c)
        t.mark(); t.poll(self.now)
        c.fail = OSError('gone')
        c.state['clients'] = []
        t.mark(); t.poll(self.now)
        self.assertEqual(len(self.store.load()['windows']), 1)

    def test_a_failed_save_does_not_reach_the_run(self):
        c = Client([win('0xa', 61)], [ws(61, 'XR-1')])
        t = self.tracker(c)
        with mock.patch.object(self.store, 'save', side_effect=OSError('disk')):
            t.mark(); t.poll(self.now)
            t.close()

    def test_an_empty_capture_never_replaces_a_memory(self):
        c = Client([win('0xa', 61)], [ws(61, 'XR-1')])
        t = self.tracker(c)
        t.mark(); t.poll(self.now)
        c.state['clients'] = []
        t.mark(); t.poll(self.now)
        t.close()
        self.assertEqual([w['address'] for w in self.store.load()['windows']], ['0xa'])

    def test_the_last_capture_is_at_the_end(self):
        c = Client([win('0xa', 61)], [ws(61, 'XR-1')])
        t = self.tracker(c)
        t.close()
        self.assertEqual(len(self.store.load()['windows']), 1)
        c.queries.clear()
        t.mark(); t.poll(self.now + 10)
        t.close()
        self.assertEqual(c.queries, [])  # nothing runs after the end

    def test_the_end_reads_with_a_short_deadline(self):
        timeouts = []
        c = Client([win('0xa', 61)], [ws(61, 'XR-1')])
        real = c.query
        c.query = lambda kind, timeout=None: (timeouts.append(timeout), real(kind))[1]
        self.tracker(c).close()
        self.assertTrue(all(t is not None and t <= wm.QUERY_TIMEOUT_S for t in timeouts))

    def test_the_context_comes_from_the_view(self):
        placed = {'placement': {'x': 100, 'y': 0, 'distance': 90, 'curvature': 0}, 'roll': -300}
        c = Client([win('0xa', 61)], [ws(61, 'XR-1')])
        t = self.tracker(c)
        t.context = lambda: {'XR-1': placed}
        t.close()
        d = self.store.load()['desktops']['XR-1']
        self.assertEqual((d['placement'], d['roll']), (placed['placement'], -300))

    def test_a_broken_context_only_loses_the_context(self):
        c = Client([win('0xa', 61)], [ws(61, 'XR-1')])
        t = self.tracker(c)
        t.context = lambda: 1 / 0
        t.close()
        self.assertEqual(len(self.store.load()['windows']), 1)


class StartTests(Tracked):
    def saved(self, *windows, instance='inst'):
        self.store.save(memory(*windows, instance=instance))

    def test_nothing_saved_does_nothing(self):
        c = Client([win('0xa', 1)], [ws(1, 'DP-1')])
        t = self.tracker(c)
        t.place(XR); t.finish()
        self.assertEqual(c.placed, [])

    def test_an_unusable_memory_does_nothing(self):
        self.store.path.write_text('{not json')
        c = Client([win('0xa', 1)], [ws(1, 'DP-1')])
        t = self.tracker(c)
        t.place(XR); t.finish()
        self.assertEqual(c.placed, [])

    def test_moves_then_settles_with_one_plan(self):
        self.saved(record('0xa', 'XR-2', 1, 71))
        c = Client([win('0xa', 1)], [ws(1, 'DP-1')])
        t = self.tracker(c)
        t.place(XR)
        self.assertEqual([phase for phase, _ in c.placed], ['moves'])
        t.finish()
        self.assertEqual([phase for phase, _ in c.placed], ['moves', 'settle'])
        self.assertIs(c.placed[0][1], c.placed[1][1])

    def test_focus_is_read_before_anything_moves(self):
        self.saved(record('0xa', 'XR-2', 1, 71))
        monitors = [dict(m) for m in MONITORS]
        monitors[0]['focused'] = True
        c = Client([win('0xa', 1)], [ws(1, 'DP-1')], monitors, active={'address': '0xa'})
        t = self.tracker(c)
        t.place(XR)
        self.assertEqual((c.placed[0][1]['focus'], c.placed[0][1]['monitor']), ('0xa', 'DP-1'))

    def test_a_compositor_failure_leaves_the_windows_to_adoption(self):
        self.saved(record('0xa', 'XR-2', 1, 71))
        c = Client([win('0xa', 1)], [ws(1, 'DP-1')])
        c.fail = OSError('gone')
        t = self.tracker(c)
        t.place(XR); t.finish()
        self.assertEqual(c.placed, [])

    def test_a_refused_move_does_not_stop_the_run(self):
        self.saved(record('0xa', 'XR-2', 1, 71))
        c = Client([win('0xa', 1)], [ws(1, 'DP-1')])
        c.place_windows = mock.Mock(side_effect=ValueError('refused'))
        t = self.tracker(c)
        t.place(XR); t.finish()

    def test_the_address_needs_the_same_session(self):
        self.saved(record('0xa', 'XR-2', 1, 71, cls='foot', title='vim'), instance='older')
        c = Client([win('0xa', 1, cls='firefox', title='x'), win('0xb', 1, cls='foot', title='vim')], [ws(1, 'DP-1')])
        t = self.tracker(c)
        t.place(XR)
        self.assertEqual([p['address'] for p in c.placed[0][1]['placed']], ['0xb'])

    def test_the_first_capture_after_a_start_replaces_the_old_memory(self):
        self.saved(record('0xa', 'XR-2', 1, 71), record('0xb', 'XR-3', 2, 81))
        c = Client([win('0xa', 1)], [ws(1, 'DP-1')])
        t = self.tracker(c)
        t.place(XR); t.finish()
        c.state.update(clients=[win('0xa', 71, at=(6016, 26))], workspaces=[ws(71, 'XR-2')])
        t.close()
        self.assertEqual([w['address'] for w in self.store.load()['windows']], ['0xa'])

    def test_a_window_on_a_missing_desktop_stays_parked_there(self):
        self.saved(record('0xa', 'XR-4', 3, 91))
        c = Client([win('0xa', 1)], [ws(1, 'DP-1')])
        t = self.tracker(c)
        t.place(XR); t.finish()
        c.state.update(clients=[win('0xa', 82, at=(7936, 26))], workspaces=[ws(82, 'XR-3')])
        t.close()
        w = self.store.load()['windows'][0]
        self.assertEqual((w['parked'], w['output'], w['landing'], w['workspace']), (True, 'XR-4', 'XR-3', 91))


class RemovalTests(Tracked):
    """A desktop removed in a run and added again (module docstring: Desktops that go and come back)."""
    def run_(self):
        self.c = Client([win('0xa', 91, at=(9856, 26)), win('0xb', 91, at=(10000, 26)), win('0xc', 81, at=(7936, 26))],
                        [ws(91, 'XR-4'), ws(81, 'XR-3')], MONITORS + [mon('XR-4', x=9856, active=91)])
        return self.tracker(self.c, names=['XR-1', 'XR-2', 'XR-3', 'XR-4'])

    def land(self, workspace=86, monitor='XR-3'):
        """The windows of XR-4 now sit on `workspace` of `monitor` (adopt.adopt moved them), XR-4 is gone."""
        c = self.c
        c.state['monitors'] = [m for m in c.state['monitors'] if m['name'] != 'XR-4']
        c.state['workspaces'] = [ws(workspace, monitor), ws(81, 'XR-3')]
        for w in c.state['clients'][:2]: w['workspace'] = {'id': workspace, 'name': str(workspace)}

    def test_a_removed_desktops_windows_are_parked(self):
        t = self.run_()
        t.leaving('XR-4', 'XR-3')
        self.land()
        t.set_names(XR)
        t.close()
        by = {w['address']: w for w in self.store.load()['windows']}
        self.assertEqual((by['0xa']['output'], by['0xa']['workspace'], by['0xa']['parked'], by['0xa']['landing']), ('XR-4', 91, True, 'XR-3'))
        self.assertFalse(by['0xc']['parked'])

    def test_windows_of_a_removed_desktop_park_on_the_fallback_without_a_target(self):
        t = self.run_()
        t.leaving('XR-4', None)
        self.land(5, 'DP-1')
        t.set_names(XR)
        t.close()
        by = {w['address']: w for w in self.store.load()['windows']}
        self.assertEqual((by['0xa']['parked'], by['0xa']['landing']), (True, 'DP-1'))

    def test_a_parked_window_moved_by_hand_is_remembered_there(self):
        t = self.run_()
        t.leaving('XR-4', 'XR-3')
        self.land()
        t.set_names(XR)
        t.mark(); t.poll(self.now)
        self.c.state['clients'][0]['workspace'] = {'id': 71, 'name': '71'}
        self.c.state['workspaces'].append(ws(71, 'XR-2'))
        t.close()
        by = {w['address']: w for w in self.store.load()['windows']}
        self.assertEqual((by['0xa']['output'], by['0xa']['parked']), ('XR-2', False))
        self.assertTrue(by['0xb']['parked'])

    def test_a_parked_window_that_closed_is_forgotten(self):
        t = self.run_()
        t.leaving('XR-4', 'XR-3')
        self.land()
        t.set_names(XR)
        del self.c.state['clients'][0]
        t.close()
        self.assertEqual(sorted(w['address'] for w in self.store.load()['windows']), ['0xb', '0xc'])

    def test_adding_the_desktop_again_brings_them_back(self):
        t = self.run_()
        t.leaving('XR-4', 'XR-3')
        self.land()
        t.set_names(XR)
        self.c.state['monitors'] = self.c.state['monitors'] + [mon('XR-4', x=9856, active=91)]
        t.set_names(['XR-1', 'XR-2', 'XR-3', 'XR-4'])
        t.poll(self.now)
        phases = [phase for phase, _ in self.c.placed]
        self.assertEqual(phases, ['moves', 'settle'])
        p = self.c.placed[0][1]
        self.assertEqual(p['whole'], [(86, 'XR-4', 91)])
        self.assertEqual(sorted(x['address'] for x in p['placed']), ['0xa', '0xb'])

    def test_only_the_very_windows_come_back(self):
        t = self.run_()
        t.leaving('XR-4', 'XR-3')
        self.land()
        t.set_names(XR)
        del self.c.state['clients'][0:2]
        self.c.state['clients'].append(win('0xd', 86, at=(7936, 26)))  # same class, same title, a new window
        self.c.state['monitors'] = self.c.state['monitors'] + [mon('XR-4', x=9856, active=91)]
        t.set_names(['XR-1', 'XR-2', 'XR-3', 'XR-4'])
        t.poll(self.now)
        self.assertEqual(self.c.placed, [])

    def test_a_different_new_desktop_brings_nothing(self):
        t = self.run_()
        t.leaving('XR-4', 'XR-3')
        self.land()
        t.set_names(XR)
        t.set_names(['XR-1', 'XR-2', 'XR-3'])
        t.poll(self.now)
        self.assertEqual(self.c.placed, [])

    def test_a_second_removal_cannot_park_a_window_twice(self):
        t = self.run_()
        t.mark(); t.poll(self.now)  # on disk, not parked
        self.c.fail = OSError('gone')  # neither removal can read Hyprland: both fall back on the saved memory
        t.leaving('XR-4', 'XR-3')
        t.leaving('XR-4', 'XR-3')
        self.c.fail = None
        self.land()
        t.set_names(XR)
        t.close()
        by = {w['address']: w for w in self.store.load()['windows']}
        self.assertEqual(sorted(by), ['0xa', '0xb', '0xc'])
        self.assertTrue(by['0xa']['parked'])  # saved: a duplicate would fail validation and keep the old file

    def test_a_desktop_change_failing_in_the_tracker_never_fails_the_removal(self):
        t = self.run_()
        self.c.fail = OSError('gone')
        t.leaving('XR-4', 'XR-3')  # no exception


class ViewWiringTests(unittest.TestCase):
    def control(self):
        from tools.native_spike import view
        state = view.ViewState(['XR-1', 'XR-2'])
        c = view.ViewControl(state, 'run', 'session', lambda name: None, None, None)
        c.windows = mock.Mock()
        return c

    def test_window_events_mark_the_memory(self):
        for event in (b'movewindow>>abc,3', b'movewindowv2>>abc,3,3', b'openwindow>>abc,1,foot,t', b'closewindow>>abc', b'changefloatingmode>>abc,1',
                      b'fullscreen>>1', b'moveworkspace>>1,XR-1', b'moveworkspacev2>>1,1,XR-1', b'workspace>>3', b'workspacev2>>3,3', b'renameworkspace>>3,4'):
            with self.subTest(event=event):
                c = self.control()
                c.on_events(event + b'\n')
                c.windows.mark.assert_called_once()

    def test_other_events_do_not(self):
        c = self.control()
        c.on_events(b'activewindow>>foot,t\nwindowtitle>>abc\nwindowtitlev2>>abc,t\nfocusedmon>>XR-1,1\n')
        c.windows.mark.assert_not_called()

    def test_a_chunk_of_events_marks_once(self):
        c = self.control()
        c.on_events(b'movewindow>>a,1\nmovewindow>>b,1\nopenwindow>>c,1,foot,t\n')
        c.windows.mark.assert_called_once()

    def test_an_event_split_across_reads_counts_once_whole(self):
        c = self.control()
        c.on_events(b'movewin')
        c.windows.mark.assert_not_called()
        c.on_events(b'dow>>a,1\n')
        c.windows.mark.assert_called_once()

    def test_events_without_a_memory_are_fine(self):
        c = self.control()
        c.windows = None
        c.on_events(b'movewindow>>a,1\n')

    def test_the_end_of_a_super_drag_marks_the_memory(self):
        c = self.control()
        c.overview_pointer = mock.Mock()
        c.overview_pointer.on_super.return_value = None
        c.on_message({'op': 'super_release'})
        c.windows.mark.assert_called_once()

    def test_the_start_of_a_super_drag_does_not(self):
        c = self.control()
        c.overview_pointer = mock.Mock()
        c.overview_pointer.on_super.return_value = None
        c.on_message({'op': 'super_left_press'})
        c.windows.mark.assert_not_called()

    def test_poll_drives_the_memory(self):
        c = self.control()
        c.poll(5.0)
        c.windows.poll.assert_called_once_with(5.0)

    def test_no_capture_while_desktops_change(self):
        c = self.control()
        c.state.desktops_busy = True
        c.poll(5.0)
        c.windows.poll.assert_not_called()

    def test_events_and_ticks_reach_it_through_the_wrapper_the_supervisor_drives(self):
        from tools.native_spike.video import VideoControl
        c = self.control()
        video = VideoControl(c, binds=None, notify=lambda text: None)
        video.on_events(b'movewindow>>a,1\n')
        c.windows.mark.assert_called_once()
        video.poll(5.0)
        c.windows.poll.assert_called_once_with(5.0)

    def test_the_context_is_the_views(self):
        from tools.native_spike import view
        c = self.control()
        out = c.window_context()
        self.assertEqual(sorted(out), ['XR-1', 'XR-2'])
        self.assertEqual(out['XR-1'], {'placement': c.state.placements[0], 'roll': 0})


if __name__ == '__main__':
    unittest.main()
