"""Every XR start restores the last session's monitor setup (remember-layout-reset-bind, 2026-11-03; it undid the fresh
three-flat start of xr-default-three-monitors, 17b6238): the desktop count, each desktop's resolution and scale, its
placement (left/right, down/up, distance, curvature) and its levelling roll. Nothing saved, or a save that is unusable
(corrupt, invalid, outputs the broker refuses), gives the default three flat desktops, with the reason logged. The
maximized follow / locked choice still starts at follow every time (maximize-follow), and the saved mode is not read.

Offline: temporary profiles, a mock compositor client, test_desktops' fake broker and the real journal."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from tools.native_spike import free_pointer as fp, geometry as g, managed, profiles, view
from tools.native_spike.hyprland import OwnershipBlocked, create_outputs, plan_outputs
from tools.native_spike.journal import Journal
from tools.native_spike.ownership import OwnershipBrokerClient
from test_desktops import BrokerTransport, Run

MODEL = 'luma-ultra'
D169 = dict(g.DEFAULT_DESKTOP)
WIDE = {'width': 2560, 'height': 1080, 'scale': 1.0}
SHARP = {'width': 2560, 'height': 1440, 'scale': 1.25}
BIG = {'width': 3840, 'height': 2160, 'scale': 2.0}
FOUR = [WIDE, SHARP, D169, BIG]
FLAT = dict(g.DEFAULT_PLACEMENT)
PLACED = [{'x': -450, 'y': 30, 'distance': 80, 'curvature': 40}, {'x': 0, 'y': -120, 'distance': 140, 'curvature': 0},
          {'x': 1800, 'y': 0, 'distance': 100, 'curvature': 120}, {'x': 600, 'y': 900, 'distance': 10, 'curvature': 7}]
ROLLS = [250, -1200, 0, 9000]
SETUP = {'desktops': FOUR, 'placements': PLACED, 'rolls': ROLLS}


def quiet():
    """Capture the log lines (profiles.log writes to stderr)."""
    return contextlib.redirect_stderr(io.StringIO())


class Profile:
    def __init__(self, test):
        tmp = tempfile.TemporaryDirectory(); test.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.path = self.dir / 'profiles.json'

    def raw(self, entry, schema=profiles.SCHEMA):
        self.path.write_text(json.dumps({'schema': schema, 'profiles': {MODEL: entry}}))

    def setup(self):
        return profiles.load_setup(self.path, MODEL)


class LoadSetupTests(unittest.TestCase):
    def setUp(self):
        self.p = Profile(self)

    def test_the_whole_setup_round_trips(self):
        profiles.save_profile(self.p.path, MODEL, {**profiles.DEFAULT_PROFILE, 'spacing': 80, 'tracking': False})
        profiles.PlacementStore(self.p.path, MODEL).save_desktops(FOUR, PLACED, ROLLS)
        self.assertEqual(self.p.setup(), (SETUP, None))
        saved = profiles.load_profile(self.p.path, MODEL)
        self.assertEqual((saved['spacing'], saved['tracking']), (80, False))  # every other field kept
        self.assertEqual(oct(self.p.path.stat().st_mode & 0o777), '0o600')
        self.assertEqual(sorted(f.name for f in self.p.dir.iterdir()), ['profiles.json'])  # no temp file left behind

    def test_nothing_saved_yet_is_the_default_three_flat_desktops(self):
        default = {'desktops': [D169] * 3, 'placements': [FLAT] * 3, 'rolls': [0] * 3}
        self.assertEqual(profiles.default_setup(), default)
        self.assertEqual(self.p.setup(), (default, profiles.NOTHING_SAVED))  # no file
        profiles.save_profile(self.p.path, 'beast', profiles.DEFAULT_PROFILE)
        self.assertEqual(self.p.setup(), (default, profiles.NOTHING_SAVED))  # another model's only
        self.p.raw({'spacing': 60})  # a profile without desktops
        self.assertEqual(self.p.setup(), (default, profiles.NOTHING_SAVED))

    def test_an_unusable_save_falls_back_whole_with_the_reason(self):
        good = {**profiles.DEFAULT_PROFILE, **SETUP}
        cases = {
            'not JSON': ('{"schema": 2, "profiles"', 'corrupt (not JSON)'),
            'old schema': (json.dumps({'schema': 1, 'profiles': {MODEL: good}}), 'corrupt (no schema 2 profiles)'),
            'not an object': ([1, 2], 'not an object'),
            'odd width': ({**good, 'desktops': [{**WIDE, 'width': 2561}] + FOUR[1:]}, 'saved desktops are invalid'),
            'bad scale': ({**good, 'desktops': [{**WIDE, 'scale': 1.3}] + FOUR[1:]}, 'saved desktops are invalid'),
            'five desktops': ({**good, 'desktops': FOUR + [D169], 'placements': PLACED + [FLAT], 'rolls': ROLLS + [0]},
                              'saved desktops are invalid'),
            'no desktops': ({**good, 'desktops': []}, 'saved desktops are invalid'),
            'placement count': ({**good, 'placements': PLACED[:3]}, 'saved placements are invalid'),
            'placement bounds': ({**good, 'placements': [{**PLACED[0], 'distance': 5}] + PLACED[1:]}, 'saved placements are invalid'),
            'placements not a list': ({**good, 'placements': 'flat'}, 'saved placements are invalid'),
            'roll count': ({**good, 'rolls': ROLLS[:2]}, 'saved rolls are invalid'),
            'roll bound': ({**good, 'rolls': [9001, 0, 0, 0]}, 'saved rolls are invalid'),
            'roll type': ({**good, 'rolls': [1.5, 0, 0, 0]}, 'saved rolls are invalid'),
        }
        for name, (entry, why) in cases.items():
            with self.subTest(name):
                if isinstance(entry, str): self.p.path.write_text(entry)
                else: self.p.raw(entry)
                setup, reason = self.p.setup()
                self.assertEqual(setup, profiles.default_setup())  # never a mix of saved and default
                self.assertIn(why, reason)
        self.p.path.write_text('{}'); self.p.path.chmod(0)
        if not os.access(self.p.path, os.R_OK):  # (root reads anyway)
            self.assertIn('could not be read', self.p.setup()[1])

    def test_older_saves_still_restore(self):
        legacy = [{**p, 'yaw': 10, 'pitch': 0, 'roll': 5} for p in PLACED]  # saved before 2026-11-02's facing removal
        self.p.raw({'desktops': FOUR, 'placements': legacy})  # and before rolls were kept
        self.assertEqual(self.p.setup(), ({'desktops': FOUR, 'placements': PLACED, 'rolls': [0] * 4}, None))
        self.p.raw({'desktops': FOUR[:2]})  # before placements existed
        self.assertEqual(self.p.setup(), ({'desktops': FOUR[:2], 'placements': [FLAT] * 2, 'rolls': [0, 0]}, None))

    def test_the_panel_still_reads_a_profile_the_start_refuses(self):
        # load_profile cleans field by field (the panel and the view settings); load_setup takes the setup whole or not.
        self.p.raw({**profiles.DEFAULT_PROFILE, **SETUP, 'rolls': [1, 2]})
        self.assertEqual(profiles.load_profile(self.p.path, MODEL)['rolls'], [0] * 4)
        self.assertEqual(profiles.load_profile(self.p.path, MODEL)['placements'], PLACED)
        self.assertEqual(self.p.setup()[0], profiles.default_setup())

    def test_saves_are_atomic(self):
        store = profiles.PlacementStore(self.p.path, MODEL)
        store.save_desktops(FOUR, PLACED, ROLLS)
        before = self.p.path.read_bytes()
        with mock.patch('tools.native_spike.profiles.os.replace', side_effect=OSError('disk full')):
            with self.assertRaises(OSError): store.save_desktops([D169], [FLAT], [0])
        self.assertEqual(self.p.path.read_bytes(), before)  # the old file, whole
        self.assertEqual(sorted(f.name for f in self.p.dir.iterdir()), ['profiles.json'])  # its temp file removed

    def test_placement_and_roll_saves_keep_the_rest_of_the_setup(self):
        store = profiles.PlacementStore(self.p.path, MODEL)
        store.save_desktops(FOUR, PLACED, ROLLS)
        store.save_layout([FLAT, FLAT], [7, 8])  # fewer: the tail stays as saved
        self.assertEqual(self.p.setup()[0], {'desktops': FOUR, 'placements': [FLAT, FLAT] + PLACED[2:], 'rolls': [7, 8] + ROLLS[2:]})
        store.save([PLACED[0]] * 4)  # placements alone: the rolls stay
        self.assertEqual(self.p.setup()[0]['rolls'], [7, 8] + ROLLS[2:])
        store.save_desktops(FOUR[:2], PLACED[:2])  # no rolls known: level
        self.assertEqual(self.p.setup()[0], {'desktops': FOUR[:2], 'placements': PLACED[:2], 'rolls': [0, 0]})
        store.reset()
        self.assertEqual(self.p.setup(), (profiles.default_setup(), None))

    def test_a_profile_saved_with_another_count_gets_level_rolls(self):
        # A caller that changes the count without knowing rolls (the panel's desktop editor keeps the count anyway).
        base = profiles.load_profile(self.p.path, MODEL)
        profiles.save_profile(self.p.path, MODEL, {**base, 'desktops': FOUR, 'placements': PLACED})
        self.assertEqual(profiles.load_profile(self.p.path, MODEL)['rolls'], [0] * 4)
        with self.assertRaises(ValueError): profiles.save_profile(self.p.path, MODEL, {**base, 'rolls': [0, 0, 9001]})
        with self.assertRaises(ValueError): profiles.save_profile(self.p.path, MODEL, {**base, 'rolls': None})

    def test_the_backup_is_kept_validated_and_never_restored(self):
        store = profiles.PlacementStore(self.p.path, MODEL)
        store.save_desktops(FOUR, PLACED, ROLLS)
        self.assertIsNone(profiles.load_profile(self.p.path, MODEL)['previous_setup'])
        store.reset({'desktops': FOUR, 'placements': PLACED, 'rolls': ROLLS, 'spacing': 80})
        self.assertEqual(profiles.load_profile(self.p.path, MODEL)['previous_setup'], {**SETUP, 'spacing': 80})
        self.assertEqual(self.p.setup(), (profiles.default_setup(), None))  # a start restores the default, not the backup
        store.save_desktops([D169], [FLAT], [0])  # later saves keep it
        store.save([PLACED[0]])
        self.assertEqual(profiles.load_profile(self.p.path, MODEL)['previous_setup'], {**SETUP, 'spacing': 80})
        store.reset()  # (a setup the broker refused): the setup alone, the backup and spacing stay
        self.assertEqual(profiles.load_profile(self.p.path, MODEL)['previous_setup'], {**SETUP, 'spacing': 80})
        profile = profiles.load_profile(self.p.path, MODEL)
        for bad in ({**SETUP}, {**SETUP, 'spacing': 9999}, {**SETUP, 'spacing': 80, 'rolls': [1]}, 'flat'):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError): profiles.save_profile(self.p.path, MODEL, {**profile, 'previous_setup': bad})
                self.p.raw({**profile, 'previous_setup': bad})  # a damaged backup costs only the backup
                self.assertIsNone(profiles.load_profile(self.p.path, MODEL)['previous_setup'])
                self.assertIsNone(self.p.setup()[1])

    def test_a_reset_without_live_desktops_backs_up_the_profiles(self):
        store = profiles.PlacementStore(self.p.path, MODEL)
        store.save_desktops(FOUR, PLACED, ROLLS)
        store.reset({'desktops': None, 'placements': PLACED, 'rolls': ROLLS, 'spacing': 60})
        self.assertEqual(profiles.load_profile(self.p.path, MODEL)['previous_setup']['desktops'], FOUR)
        self.assertEqual(profiles.load_profile(self.p.path, MODEL)['spacing'], view.DEFAULT_SPACING)

    def test_the_roll_bound_is_one_number_everywhere(self):
        root = Path(__file__).resolve().parents[2]
        self.assertEqual(g.MAX_ROLL, fp.MAX_ROLL)
        self.assertIn(f'kMaxRoll = {g.MAX_ROLL};', (root / 'spike/include/xr/ipc.hpp').read_text())
        self.assertEqual(profiles.DEFAULT_SETUP_DESKTOPS, 3)
        self.assertEqual(profiles.SETUP_KEYS, ('desktops', 'placements', 'rolls'))
        self.assertEqual(view.DEFAULT_RESET_PLACEMENT, g.DEFAULT_PLACEMENT)


def mock_client(runtime):
    client = mock.Mock(runtime_dir=runtime, event_stream=None)
    client.query.return_value = []
    for name in ('lock_pointer', 'enter_desktop'):  # no unified-cursor sampler thread in these tests
        delattr(client, name)
    return client


class RunRestoreTests(unittest.TestCase):
    def setUp(self):
        self.p = Profile(self)
        self.runtime = self.p.dir / 'run'; self.runtime.mkdir(mode=0o700)
        self.client = mock_client(self.runtime)
        self.store = profiles.ProfileStore(self.p.path, MODEL)

    def run_with(self, desktops):
        """A panel run's view controls over a broker-made set of `desktops` (managed.view_controls)."""
        run = Run(self, desktops)
        return run, managed.view_controls(self.client, 'run', 'session', run.manager.names(), store=self.store,
                                          desktops=run.manager)

    def test_a_run_restores_placements_and_rolls_and_saves_its_setup_at_the_end(self):
        profiles.save_profile(self.p.path, MODEL, {**profiles.DEFAULT_PROFILE, 'mode': 'locked', **SETUP})
        run, controls = self.run_with(FOUR)
        with quiet() as log, controls as control:
            s = control.state
            self.assertEqual(s.names, ['XR-1', 'XR-2', 'XR-3', 'XR-4'])
            self.assertEqual(s.placements, PLACED)
            self.assertEqual(control.overview_pointer.rolls, ROLLS)
            self.assertEqual(s.sizes, FOUR)  # the Scale dropdown's view of them
            placed = [json.loads(f) for f in control.initial_frame().splitlines()]
            self.assertEqual([f['payload']['desktops'] for f in placed if f['op'] == 'placement'], [PLACED])
            frames = [json.loads(f) for f in control.poll(0.0).splitlines()]
            self.assertEqual([f['payload']['roll'] for f in frames if f['op'] == 'roll'], [ROLLS])  # to the renderer
            # The maximized follow / locked choice is not part of the setup: every start maximizes in follow.
            self.assertEqual((s.zoomed, s.mode, s.maximized_mode), (False, 'anchor', 'follow'))
            # Changed during the run, without a save yet (no panel collapse, no drag release, no debounce elapsed).
            s.placements = [FLAT] + PLACED[1:]
            control.overview_pointer.rolls[3] = -300
        self.assertEqual(log.getvalue(), '')  # restored: nothing to report
        self.assertEqual(self.p.setup(), ({'desktops': FOUR, 'placements': [FLAT] + PLACED[1:],
                                           'rolls': ROLLS[:3] + [-300]}, None))  # the run's end saved it

    def test_a_save_for_other_desktops_starts_flat_and_says_why(self):
        profiles.PlacementStore(self.p.path, MODEL).save_desktops(FOUR, PLACED, ROLLS)
        run, controls = self.run_with(None)  # the default three (a CLI run, or the profile changed since begin_start)
        with quiet() as log, controls as control:
            self.assertEqual(control.state.placements, [FLAT] * 3)
            self.assertEqual(control.overview_pointer.rolls, [0] * 4)
        self.assertIn('the saved placements are for other desktops (4 desktops: 2560x1080@1', log.getvalue())
        self.assertEqual(self.p.setup()[0], profiles.default_setup())  # the run's own setup, saved at its end

    def test_an_unusable_save_starts_flat_and_logs_the_reason(self):
        self.p.raw({**profiles.DEFAULT_PROFILE, **SETUP, 'placements': PLACED[:2]})
        run, controls = self.run_with(FOUR)  # (begin_start would not have made these; the run checks on its own too)
        with quiet() as log, controls as control:
            self.assertEqual(control.state.placements, [FLAT] * 4)
            self.assertEqual(control.overview_pointer.rolls, [0] * 4)
        self.assertIn('run: saved monitor setup not used: the saved placements are invalid', log.getvalue())

    def test_nothing_saved_starts_flat_quietly(self):
        run, controls = self.run_with(None)
        with quiet() as log, controls as control:
            self.assertEqual(control.state.placements, [FLAT] * 3)
        self.assertEqual(log.getvalue(), '')
        self.assertEqual(self.p.setup(), (profiles.default_setup(), None))  # saved at the end: next time it restores

    def test_a_roll_change_is_saved_like_a_placement(self):
        profiles.PlacementStore(self.p.path, MODEL).save_desktops(FOUR, PLACED, ROLLS)
        run, controls = self.run_with(FOUR)
        with quiet(), controls as control:
            c = control.view
            now = [100.0]
            c.clock = lambda: now[0]
            c.poll(now[0])  # takes in the restored rolls
            c.overview_pointer.rolls[0] = 0  # a level bar's click (free_pointer.roll_click)
            c.poll(now[0])
            self.assertEqual(self.p.setup()[0]['rolls'], ROLLS)  # not yet: debounced like a placement change
            now[0] += view.MENU_SAVE_DELAY + 0.01
            c.poll(now[0])
            self.assertEqual(self.p.setup()[0]['rolls'], [0] + ROLLS[1:])
            c.state.placements = [FLAT] * 4
            c.save_due = now[0]; c.poll(now[0])  # a placement save carries the rolls too
            self.assertEqual(self.p.setup()[0], {'desktops': FOUR, 'placements': [FLAT] * 4, 'rolls': [0] + ROLLS[1:]})

    def test_a_desktop_change_saves_the_rolls_by_desktop(self):
        profiles.PlacementStore(self.p.path, MODEL).save_desktops(FOUR, PLACED, ROLLS)
        run, controls = self.run_with(FOUR)
        with quiet(), controls as control:
            c = control.view
            c.attach_supervisor(mock.Mock())
            c.state.open_menu(1)
            c.desktop_action(1, 'remove'); c.desktop_action(1, 'remove')  # Remove asks twice
            c.poll(0.0)
            self.assertEqual(c.state.names, ['XR-1', 'XR-3', 'XR-4'])
            self.assertEqual(self.p.setup()[0], {'desktops': [WIDE, D169, BIG], 'placements': [PLACED[0]] + PLACED[2:],
                                                 'rolls': [ROLLS[0]] + ROLLS[2:]})


class CreateFailureTests(unittest.TestCase):
    """A saved setup whose outputs the broker refuses: the next start falls back (managed._setup_failed)."""
    def setUp(self):
        self.p = Profile(self)
        profiles.PlacementStore(self.p.path, MODEL).save_desktops(FOUR, PLACED, ROLLS)
        self.store = profiles.ProfileStore(self.p.path, MODEL)
        self.journal = Journal.open(self.p.dir / 'journal.json', 'run', 'session')
        self.transport = BrokerTransport(self.journal.path)
        self.broker = OwnershipBrokerClient(self.transport, 'run', 'session', 'DP-1')
        self.specs = plan_outputs([], [], 'run', FOUR)

    def failure(self):
        with self.assertRaises(Exception) as caught: create_outputs(self.specs, self.journal, self.broker)
        return caught.exception

    def test_refused_outputs_reset_the_saved_setup_and_log_it(self):
        self.transport.fail_index = 3  # the broker could not make XR-4 live
        exc = self.failure()
        self.assertEqual(str(exc), 'creation incomplete')
        with quiet() as log: managed._setup_failed(self.store, FOUR)(exc)
        self.assertEqual(self.p.setup(), (profiles.default_setup(), None))  # the next start: three flat desktops
        self.assertIn('the saved monitor setup could not be created (creation incomplete)', log.getvalue())
        self.assertIn(json.dumps(SETUP, separators=(',', ':')), log.getvalue())  # what it replaced, to recover by hand
        for why in managed.UNCREATABLE: self.assertIn(why, Path(managed.__file__.replace('managed', 'ownership')).read_text())

    def test_other_failures_keep_the_saved_setup(self):
        self.transport.lost_reply = True  # a transport error says nothing about the setup
        exc = self.failure()
        self.assertIsInstance(exc, OSError)
        failed = managed._setup_failed(self.store, FOUR)
        for e in (exc, OwnershipBlocked('broker epoch lost; preserve journal'), OwnershipBlocked('compositor session changed'),
                  RuntimeError('creation incomplete')):
            with quiet(): failed(e)
            self.assertEqual(self.p.setup(), (SETUP, None))
        with quiet(): managed._setup_failed(self.store, [D169, WIDE])(OwnershipBlocked('creation incomplete'))
        self.assertEqual(self.p.setup(), (SETUP, None))  # the profile holds other desktops by now: not this one's to drop

    def test_only_a_restored_setup_from_a_profile_is_discarded(self):
        self.assertIsNone(managed._setup_failed(self.store, [D169] * 3))  # the default: nothing to fall back to
        self.assertIsNone(managed._setup_failed(profiles.LayoutStore(self.p.dir / 'layout.json'), FOUR))  # the CLI's store
        self.assertIsNone(managed._setup_failed(self.store, None))

    def test_the_run_calls_it_when_creating_its_outputs_fails(self):
        seen = []
        client = mock.Mock(instance='0' * 40 + '_1_2')  # hyprland.INSTANCE
        self.broker.require_output_ownership()
        refused = OwnershipBlocked('actual geometry/workspace mismatch')
        with mock.patch.object(managed, 'create_outputs', side_effect=refused), \
                mock.patch.object(managed, 'recover_restoring', return_value=mock.Mock(complete=True, remaining=[])) as recovered:
            with self.assertRaises(OwnershipBlocked):
                managed._run_owned(client, self.journal, self.broker, 'DP-2', 'DP-1', self.specs, guard=lambda: None,
                                   create_failed=seen.append)
        self.assertEqual(seen, [refused])
        recovered.assert_called_once()  # recovery still runs, as before

    def test_a_failing_hook_never_hides_the_create_failure(self):
        client = mock.Mock(instance='0' * 40 + '_1_2')
        self.broker.require_output_ownership()
        refused = OwnershipBlocked('creation incomplete')
        with mock.patch.object(managed, 'create_outputs', side_effect=refused), \
                mock.patch.object(managed, 'recover_restoring', return_value=mock.Mock(complete=True, remaining=[])):
            with self.assertRaises(OwnershipBlocked) as caught:
                managed._run_owned(client, self.journal, self.broker, 'DP-2', 'DP-1', self.specs, guard=lambda: None,
                                   create_failed=mock.Mock(side_effect=RuntimeError('profile gone')))
        self.assertIs(caught.exception, refused)


if __name__ == '__main__':
    unittest.main()
