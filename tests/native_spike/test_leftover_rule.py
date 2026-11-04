"""Incident 20261104T022411Z (run 4468c283): Omarchy's scale control, used with XR-1 focused, wrote a monitor rule naming XR-1.

The broker's cleanup refused a same-name rule that was not its own, so XR-1 could never be removed, and recovery exited 2
saying nothing. These tests drive the real journal, broker client and recover_managed against a compositor that behaves
like the plugin that was loaded: every run of it evacuates, then blocks while a rule named XR-1 is not the broker's.
Offline: every file is in a scratch directory, and `monitors_config` is what points recovery at one.
"""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.native_spike import managed, monitor_rules
from tools.native_spike.journal import Journal
from tools.native_spike.ownership import OwnershipBrokerClient

XR_1 = '838d1fa2-f20f-416f-984f-50985cb0e9b9'
XR_2 = '4e630955-a14c-4260-af30-3d408edaaa47'
INSTANCE = 'a' * 40 + '_123_456'
RUN = 'run'
OMARCHY_LINE = 'hl.monitor({ output = "XR-1", mode = "preferred", position = "auto", scale = 1.25 })\n'
CONFIG = ('-- See https://wiki.hypr.land/Configuring/Basics/Monitors/\n'
          'hl.monitor({ output = "", mode = "preferred", position = "auto", scale = omarchy_monitor_scale })\n'
          + OMARCHY_LINE +
          '\n-- Ultrawide at its full 240Hz refresh rate.\n'
          'hl.monitor({ output = "DP-1", mode = "5120x1440@240", position = "0x0", scale = 1.25 })\n'
          '-- hl.monitor({ output = "DP-2", mode = "2560x1440@144", position = "0x0", scale = 1 })\n')


class Compositor:
    """The loaded plugin and the Hyprland behind it, as far as recovery can tell."""
    epoch = 'epoch-one'
    session = 'session'

    def __init__(self, config=None, *, new_plugin=False):
        self.instance = INSTANCE
        self.config, self.new_plugin = config, new_plugin
        self.monitors_config = config
        self.outputs = {XR_1: 'XR-1'}
        self.foreign_rule = self._named() if config else False  # Hyprland's rules: what the config said at the last reload
        self.workspaces = {'XR-1': [5, 61]}
        self.windows = []  # (class, workspace, pinned) still on XR-1
        self.events, self.reloads = [], 0
        self.request_id = 0
        self.fallback_usable = True

    def _named(self):
        return bool(monitor_rules.leftovers(['XR-1'], self.config))

    # transport
    runtime_dir = None
    def verified_session(self): return {'id': self.session}
    def reload_config(self):
        self.reloads += 1
        self.foreign_rule = self._named()
    def plugins_loaded(self): return ['xr-ownership']
    def query(self, kind, *a, **k):
        if kind == 'monitors':
            return [{'name': 'DP-1', 'id': 1}, {'name': 'DP-2', 'id': 0}] + ([{'name': 'XR-1', 'id': 2}] if XR_1 in self.outputs else [])
        if kind == 'clients':
            return [{'class': c, 'workspace': {'id': w}, 'pinned': p, 'monitor': 2, 'mapped': True} for c, w, p in self.windows]
        if kind == 'workspaces': return [{'id': w, 'monitor': 'XR-1'} for w in self.workspaces.get('XR-1', [])]
        return []
    def _reply(self, envelope, result):
        return json.dumps({k: envelope[k] for k in ('schema', 'session', 'run_id', 'request_id')} | {'epoch': self.epoch, 'result': result}).encode()
    def _state(self, oid, state):
        live = oid in self.outputs
        observation = {'workspaces': [{'id': w, 'name': str(w), 'special': False} for w in self.workspaces.get('XR-1', [])],
                       'windows': [{'workspace': w, 'pinned': p} for _, w, p in self.windows]} if live else None
        return dict(id=oid, name='XR-1' if oid == XR_1 else 'XR-2', state=state, physical=False, observation=observation,
                    fallback_usable=self.fallback_usable)
    def _request(self, wire, timeout=None):
        envelope = json.loads(wire[len('j/xrspike '):])
        op, oid = envelope['op'], envelope['payload'].get('id')
        self.events.append(op)
        if op == 'inspect':
            return self._reply(envelope, self._state(oid, 'invalid' if oid in self.outputs else 'dead'))
        assert op == 'evacuate_remove'
        if oid not in self.outputs: return self._reply(envelope, self._state(oid, 'dead'))
        if any(p for _, _, p in self.windows) or not self.fallback_usable:
            return self._reply(envelope, self._blocked(oid, 'evacuate'))
        self.workspaces['XR-1'] = [2]
        self.windows = []
        if self.foreign_rule and not self.new_plugin: return self._reply(envelope, self._blocked(oid, 'rule'))
        del self.outputs[oid]
        self.workspaces.pop('XR-1')
        return self._reply(envelope, self._state(oid, 'dead'))
    def _blocked(self, oid, step):
        reply = self._state(oid, 'blocked')
        return reply | {'blocked': step} if self.new_plugin else reply


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        root = self.root = Path(self.tmp.name)
        self.config = root / 'hypr' / 'monitors.lua'
        self.config.parent.mkdir()
        self.config.write_text(CONFIG); self.config.chmod(0o644)
        self.path = root / 'xr-workspace-spike' / RUN / 'journal.json'
        self.path.parent.mkdir(parents=True, mode=0o700); self.path.parent.parent.chmod(0o700)
        patcher = patch('tools.native_spike.send.notify', lambda text: self.notes.append(text))
        patcher.start(); self.addCleanup(patcher.stop)
        for name in ('tools.native_spike.privacy.wake', 'tools.native_spike.managed.validate_physical_pair'):
            patcher = patch(name, lambda *a, **k: None); patcher.start(); self.addCleanup(patcher.stop)
        patcher = patch('tools.native_spike.restore.load', lambda *a, **k: None); patcher.start(); self.addCleanup(patcher.stop)
        patcher = patch('tools.native_spike.restore.discard', lambda *a, **k: None); patcher.start(); self.addCleanup(patcher.stop)
        self.notes = []
        self.compositor = None

    def journal(self):
        """What run 4468c283 left: XR-2 removed, XR-1 confirmed, the broker's scope saved."""
        journal = Journal.open(self.path, RUN, 'session')
        journal.bind_broker(dict(instance=INSTANCE, output='DP-2', recovery_output='DP-1', epoch='epoch-one', fallback='fallback-one'))
        ids = [journal.prepare(dict(kind='output', id=i, name=n, index=x, before=None)) for x, (i, n) in enumerate(((XR_1, 'XR-1'), (XR_2, 'XR-2')))]
        for oid, identity, name in zip(ids, (XR_1, XR_2), ('XR-1', 'XR-2')):
            journal.confirm(oid, dict(id=identity, name=name, physical=False))
        journal.output_removed(XR_2)
        return journal

    def recover(self, compositor, **agreed):
        agreed = agreed or dict(clear_rules=['XR-1'])  # the panel's Recover, its confirm box listing XR-1, clicked
        self.compositor = compositor
        compositor.runtime_dir = self.root
        self.journal() if not self.path.exists() else None
        return managed.recover_managed(compositor, dict(instance=INSTANCE, run_id=RUN, output='DP-2', recovery_output='DP-1'), self.path, **agreed)

    def states(self):
        return {r['operation']['name']: r['state'] for r in Journal.open(self.path, RUN, 'session').operations}

    def backups(self):
        return sorted(p for p in self.config.parent.iterdir() if p.name != 'monitors.lua')


class TheIncident(Base):
    def test_recovery_finishes_with_the_plugin_that_was_loaded(self):
        """The red: complete False, XR-1 left, nothing said. The plugin in the compositor cannot change, so recovery must."""
        compositor = Compositor(self.config)
        result = self.recover(compositor)
        self.assertTrue(result['complete'], result)
        self.assertEqual(result['remaining'], [])
        self.assertNotIn(XR_1, compositor.outputs)
        self.assertEqual(self.states(), {'XR-1': 'done', 'XR-2': 'done'})
        self.assertEqual(compositor.reloads, 1)

    def test_only_the_stray_line_goes_and_a_backup_keeps_the_rest(self):
        self.recover(Compositor(self.config))
        self.assertEqual(self.config.read_text(), CONFIG.replace(OMARCHY_LINE, ''))
        self.assertEqual(oct(self.config.stat().st_mode & 0o777), '0o644')
        [backup] = self.backups()
        self.assertEqual(backup.read_text(), CONFIG)
        self.assertTrue(backup.name.startswith('monitors.lua.bak.xr-rule-'), backup.name)

    def test_cody_is_told_what_changed(self):
        self.recover(Compositor(self.config))
        self.assertEqual(len(self.notes), 1)
        self.assertIn('XR-1', self.notes[0]); self.assertIn('monitors.lua', self.notes[0])

    def test_the_line_goes_before_the_broker_is_asked(self):
        compositor = Compositor(self.config)
        reloaded = []
        real = compositor.reload_config
        compositor.reload_config = lambda: (reloaded.append(list(compositor.events)), real())
        self.recover(compositor)
        self.assertEqual(reloaded, [[]])
        self.assertEqual(compositor.events.count('evacuate_remove'), 1)

    def test_a_recovery_with_nothing_in_the_config_leaves_it_alone(self):
        self.config.write_text(CONFIG.replace(OMARCHY_LINE, ''))
        before = self.config.stat().st_mtime_ns
        compositor = Compositor(self.config)
        self.assertTrue(self.recover(compositor)['complete'])
        self.assertEqual(compositor.reloads, 0)
        self.assertEqual(self.config.stat().st_mtime_ns, before)
        self.assertEqual(self.backups(), [])
        self.assertEqual(self.notes, [])

    def test_a_fixed_plugin_does_not_need_the_reload_to_finish(self):
        """The rule is the user's; the broker leaves it and removes the output. The line still goes: the next start needs that."""
        compositor = Compositor(self.config, new_plugin=True)
        compositor.reload_config = lambda: None  # the rule stays in Hyprland
        self.assertTrue(self.recover(compositor)['complete'])
        self.assertNotIn('XR-1', self.config.read_text())

    def test_a_second_attempt_is_a_no_op(self):
        self.recover(Compositor(self.config))
        compositor = Compositor(self.config)
        compositor.outputs = {}
        self.assertTrue(self.recover(compositor)['complete'])
        self.assertEqual(compositor.events, [])
        self.assertEqual(len(self.backups()), 1)


class WhyItDidNotFinish(Base):
    def assert_reason(self, result, *words):
        self.assertFalse(result['complete'])
        text = ' '.join(result['reasons'])
        for word in words: self.assertIn(word, text)
        self.assertEqual(self.states()['XR-1'], 'confirmed')

    def test_a_rule_recovery_will_not_touch_is_named_by_output_file_and_line(self):
        config = CONFIG.replace(OMARCHY_LINE, 'hl.monitor({\n  output = "XR-1",\n  scale = 1.25,\n})\n')
        self.config.write_text(config)
        result = self.recover(Compositor(self.config))
        self.assert_reason(result, 'XR-1', 'monitors.lua', 'line 4')
        self.assertEqual(self.config.read_text(), config)  # hand-written: never edited
        self.assertEqual(self.backups(), [])

    def test_a_config_that_cannot_be_rewritten_is_a_reason_and_recovery_still_runs(self):
        compositor = Compositor(self.config)
        with patch.object(monitor_rules, 'remove', side_effect=OSError('read-only file system')):
            result = self.recover(compositor)
        self.assert_reason(result, 'XR-1', 'read-only file system')
        self.assertIn('evacuate_remove', compositor.events)

    def test_a_pinned_window_says_which(self):
        self.config.write_text(CONFIG.replace(OMARCHY_LINE, ''))
        compositor = Compositor(self.config, new_plugin=True)
        compositor.windows = [('kitty', 5, True)]
        self.assert_reason(self.recover(compositor), 'XR-1', 'kitty', 'pinned', 'workspace 5')

    def test_the_broker_names_its_step(self):
        self.config.write_text(CONFIG.replace(OMARCHY_LINE, ''))
        compositor = Compositor(self.config, new_plugin=True)
        compositor.fallback_usable = False
        self.assert_reason(self.recover(compositor), 'XR-1', 'DP-1')

    def test_a_plugin_too_old_to_say_is_still_a_reason(self):
        """The rule is in Hyprland (a line removed but not reloaded), and the loaded plugin has no step to give."""
        self.config.write_text(CONFIG.replace(OMARCHY_LINE, ''))
        compositor = Compositor(self.config)
        compositor.foreign_rule = True
        self.assert_reason(self.recover(compositor), 'XR-1', 'refused')

    def test_an_exception_in_the_undo_is_a_reason_too(self):
        compositor = Compositor(self.config)
        real = compositor._request
        def lost(wire, timeout=None):
            if 'evacuate_remove' in wire: raise OSError('compositor socket closed')
            return real(wire)
        compositor._request = lost
        self.assert_reason(self.recover(compositor), 'XR-1', 'compositor socket closed')


class TheWholeStory(Base):
    """A clean stop, recovery short of a desktop, Recover saying why, the person fixing it, Recover finishing. Every piece
    is the real one but the compositor and the renderer."""
    HAND_WRITTEN = 'hl.monitor({\n  output = "XR-1",\n  scale = 1.25,\n})\n'

    def stop(self, compositor):
        """_run_owned, from the outputs existing to the renderer exiting 0."""
        self.compositor = compositor
        compositor.runtime_dir = self.root
        compositor.connect_wayland = lambda session: open(os.devnull, 'rb')
        journal = Journal.open(self.path, RUN, 'session')
        broker = OwnershipBrokerClient(compositor, RUN, 'session', 'DP-1')
        broker.epoch, broker.fallback = 'epoch-one', 'fallback-one'
        def create(specs, journal, broker):
            for index, (identity, name) in enumerate(((XR_1, 'XR-1'), (XR_2, 'XR-2'))):
                oid = journal.prepare(dict(kind='output', id=identity, name=name, index=index, before=None))
                journal.confirm(oid, dict(id=identity, name=name, physical=False))
            journal.output_removed(XR_2)  # XR-2 was the easy one, as it was on 2026-11-03
        with patch.object(managed, 'create_outputs', create), patch.object(managed, '_launch', lambda *a, **k: 0):
            return managed._run_owned(compositor, journal, broker, 'DP-2', 'DP-1', [], context=dict(
                instance=INSTANCE, output='DP-2', recovery_output='DP-1', epoch='epoch-one', fallback='fallback-one'), guard=lambda: None)

    def recover_run(self, *flags):
        """`recover-run`, as the panel's Recover spawns it, with the real recovery behind it."""
        import contextlib
        import io
        from tools.xr_controller import __main__ as controller_main
        state = self.root / 'state'
        state.mkdir(mode=0o700, exist_ok=True)
        consent_path = state / f'{RUN}.json'
        consent_path.write_text('{}'); consent_path.chmod(0o600)
        plugin = dict(path='/p/libxr-ownership.so', sha256='b' * 64)
        consent = dict(instance=INSTANCE, run_id=RUN, output='DP-2', recovery_output='DP-1', plugin=plugin)
        compositor = self.compositor
        compositor.plugin_unload = lambda *a, **k: setattr(compositor, 'plugins_loaded', lambda: [])
        err = io.StringIO()
        with patch.object(controller_main, 'read_private_json', lambda path, *a, **k: dict(instance=INSTANCE, output='DP-2')), \
             patch('tools.native_spike.panel_consent.load_panel_consent', lambda *a, **k: consent), \
             patch('tools.xr_controller.install.verify_recovery_pin', lambda *a, **k: None), \
             patch.object(controller_main, 'HyprlandClient', lambda *a, **k: compositor), \
             patch.dict(os.environ, XDG_RUNTIME_DIR=str(self.root), XDG_DATA_HOME=str(self.root)), contextlib.redirect_stderr(err):
            code = controller_main.main(['recover-run', '--consent', str(consent_path), '--journal', str(self.path), *flags])
        return code, err.getvalue(), state / f'{RUN}.recovery.json'

    def test_a_rule_recovery_may_not_touch_stops_it_until_the_person_acts(self):
        config = CONFIG.replace(OMARCHY_LINE, self.HAND_WRITTEN)
        self.config.write_text(config)
        compositor = Compositor(self.config)
        result = self.stop(compositor)
        self.assertEqual((result['renderer_returncode'], result['complete']), (0, False))
        self.assertEqual(len(result['remaining']), 1)
        self.assertIn('XR-1', ' '.join(result['reasons']))
        self.assertIn(XR_1, compositor.outputs)

        code, said, record = self.recover_run()
        self.assertEqual(code, 2)
        self.assertIn('monitors.lua line 4 names XR-1', said)
        self.assertTrue(record.exists())
        self.assertIn(XR_1, compositor.outputs)
        self.assertEqual(self.config.read_text(), config)

        self.config.write_text(config.replace(self.HAND_WRITTEN, ''))  # the person takes it out, and Hyprland reloads
        compositor.reload_config()
        code, said, record = self.recover_run()
        self.assertEqual((code, said), (0, ''))
        self.assertFalse(record.exists())
        self.assertNotIn(XR_1, compositor.outputs)
        self.assertEqual(compositor.plugins_loaded(), [])
        self.assertEqual(self.states(), {'XR-1': 'done', 'XR-2': 'done'})

    def test_the_rule_omarchy_writes_stops_a_stop_and_the_confirmed_recover_finishes_it(self):
        """Run 4468c283, start to end, on the plugin that was loaded."""
        compositor = Compositor(self.config)
        result = self.stop(compositor)
        self.assertEqual((result['renderer_returncode'], result['complete']), (0, False))
        self.assertIn('monitors.lua line 3 names XR-1', ' '.join(result['reasons']))
        self.assertEqual((self.config.read_text(), compositor.reloads), (CONFIG, 0))  # Stop has no consent for this
        code, said, record = self.recover_run('--clear-rule', 'XR-1')
        self.assertEqual((code, said), (0, ''))
        self.assertFalse(record.exists())
        self.assertNotIn(XR_1, compositor.outputs)
        self.assertNotIn('XR-1', self.config.read_text())
        self.assertEqual(compositor.plugins_loaded(), [])
        self.assertEqual(self.states(), {'XR-1': 'done', 'XR-2': 'done'})

    def test_a_fixed_plugin_finishes_the_stop_and_leaves_the_person_the_line(self):
        compositor = Compositor(self.config, new_plugin=True)
        result = self.stop(compositor)
        self.assertEqual((result['renderer_returncode'], result['complete'], result['reasons']), (0, True, []))
        self.assertNotIn(XR_1, compositor.outputs)
        self.assertEqual(self.config.read_text(), CONFIG)
        self.assertEqual(len(self.notes), 1)
        self.assertIn('monitors.lua line 3 names XR-1', self.notes[0])


class NoClickNoEdit(Base):
    """Saved monitor configuration is never edited (docs/technical.md, Safety boundaries), but for a Recover someone confirmed."""
    def assert_untouched(self, compositor):
        self.assertEqual(self.config.read_text(), CONFIG)
        self.assertEqual((compositor.reloads, self.backups()), (0, []))

    def test_the_cli_recover_only_says_so(self):
        compositor = Compositor(self.config)
        result = self.recover(compositor, clear_rules=())
        self.assertFalse(result['complete'])
        self.assertIn('monitors.lua line 3 names XR-1', ' '.join(result['reasons']))
        self.assert_untouched(compositor)

    def test_a_default_recovery_is_one_that_does_not_edit(self):
        compositor = Compositor(self.config)
        compositor.runtime_dir = self.root
        self.journal()
        managed.recover_managed(compositor, dict(instance=INSTANCE, run_id=RUN, output='DP-2', recovery_output='DP-1'), self.path)
        self.assert_untouched(compositor)

    def test_a_line_found_at_a_stop_is_only_named(self):
        compositor = Compositor(self.config)
        compositor.runtime_dir = self.root
        journal = self.journal()
        result = managed.recover_restoring(compositor, journal, OwnershipBrokerClient.from_journal(compositor, journal), RUN)
        self.assertFalse(result.complete)
        self.assertIn('monitors.lua line 3 names XR-1', ' '.join(result.reasons))
        self.assert_untouched(compositor)


    def test_only_the_rules_the_box_listed_go(self):
        """A rule that appeared after the box was shown was never agreed to."""
        two = CONFIG + OMARCHY_LINE.replace('XR-1', 'XR-2')
        self.config.write_text(two)
        compositor = Compositor(self.config, new_plugin=True)
        result = self.recover(compositor, clear_rules=['XR-2'])
        self.assertTrue(result['complete'])
        self.assertEqual(self.config.read_text(), CONFIG)
        self.assertEqual(len(self.backups()), 1)
        self.assertTrue(any('line 3 names XR-1' in note for note in self.notes), self.notes)

    def test_recover_run_without_the_box_edits_nothing(self):
        """An old controller still running after an install shows no box and sends no flag, while new code runs recover-run."""
        story = TheWholeStory('recover_run')
        story.root, story.path, story.compositor = self.root, self.path, Compositor(self.config)
        story.compositor.runtime_dir = self.root
        self.journal()
        code, said, record = story.recover_run()
        self.assertEqual(code, 2)
        self.assertIn('monitors.lua line 3 names XR-1', said)
        self.assertEqual(self.config.read_text(), CONFIG)
        self.assertEqual(self.backups(), [])


class LiveRecoveryReports(Base):
    """What the run's own recovery at Stop gives back, for the runner's result."""
    def test_the_result_of_a_stop_carries_the_reasons(self):
        self.config.write_text(CONFIG.replace(OMARCHY_LINE, ''))
        compositor = Compositor(self.config)
        compositor.foreign_rule = True
        compositor.runtime_dir = self.root
        journal = self.journal()
        broker = OwnershipBrokerClient.from_journal(compositor, journal)
        result = managed.recover_restoring(compositor, journal, broker, RUN)
        self.assertFalse(result.complete)
        self.assertEqual(len(result.remaining), 1)
        self.assertIn('XR-1', ' '.join(result.reasons))


class CodeChangedUnderTheSession(unittest.TestCase):
    """The code in the worktree and installed.json move on while a session's plugin stays pinned and loaded. Not what happened
    in run 4468c283 (its Stop came first, the install at 21:24:05), but what an install during a session or a pending recovery
    does. The consent, the pins, the journal bytes, the client's own checks and recover-run are all the real ones here."""
    INSTANCE = 'efb50993780079460b0cbed1363e2166a2de1d9f_1793506481_1554975112'
    RUN = '4468c2831588ef1bfa8425d5359db70a'
    SESSION = '1773a47b7c3d178bf7ce8e28aa9de204a7b4be19afcea1988b7dc6ca21b32e4b'
    EPOCH = '89e94de3b58dd0ea61e7f69e8e194c4c1f11547e830c8992'

    def setUp(self):
        from tools.native_spike.ipc import private_dir
        from tools.native_spike.panel_consent import build_consent, write_consent
        from tools.xr_controller.install import install
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        root = self.root = Path(self.tmp.name)
        self.data, self.state, self.config = root / 'data', root / 'state', root / 'hypr' / 'monitors.lua'
        self.config.parent.mkdir(); self.config.write_text(CONFIG)
        for name, body in (('old', b'old'), ('new', b'new')):
            build = root / name; build.mkdir()
            (build / 'xr-native-spike').write_bytes(body + b'-renderer'); (build / 'libxr-ownership.so').write_bytes(body + b'-plugin')
        self.old = install(root / 'old', self.data)  # the session's pins
        self.new = install(root / 'new', self.data)  # what the install left: installed.json no longer names the session's
        scope = dict(run_id=self.RUN, instance=self.INSTANCE, output='DP-2', recovery_output='DP-1', model='luma-ultra', tracking=False, mode_switch=False,
                     desktops=[dict(width=1920, height=1200, scale=1.0), dict(width=1920, height=1200, scale=1.25)], **self.old)
        fd = private_dir(self.state, create=True); self.addCleanup(os.close, fd)
        write_consent(fd, build_consent(scope))
        self.consent = self.state / f'{self.RUN}.json'
        self.journal = root / 'xr-workspace-spike' / self.RUN / 'journal.json'
        self.journal.parent.mkdir(parents=True, mode=0o700); self.journal.parent.parent.chmod(0o700)
        # The journal as run 4468c283 left it, to the byte.
        self.journal.write_text(json.dumps(dict(schema=1, run_id=self.RUN, session=self.SESSION, operations=[
            dict(id='790c77a9-bb02-4d53-9c96-3c70f62bb571', operation=dict(kind='output', id=XR_1, name='XR-1', index=0, before=None), state='confirmed'),
            dict(id='4c9c3b54-548e-4356-8456-03f25c97d105', operation=dict(kind='output', id=XR_2, name='XR-2', index=1, before=None), state='done')],
            broker=dict(instance=self.INSTANCE, output='DP-2', recovery_output='DP-1', epoch=self.EPOCH, fallback='6fa09398ea1bda277bdac44f515a7719bc5e4fe2b9a5036c'))))
        self.journal.chmod(0o600)
        for name in ('tools.native_spike.privacy.wake', 'tools.native_spike.managed.validate_physical_pair', 'tools.native_spike.send.notify'):
            patcher = patch(name, lambda *a, **k: None); patcher.start(); self.addCleanup(patcher.stop)
        env = patch.dict(os.environ, XDG_RUNTIME_DIR=str(root), XDG_DATA_HOME=str(self.data), XDG_STATE_HOME=str(root / 'xdg-state'))
        env.start(); self.addCleanup(env.stop)

    def compositor(self):
        from tools.native_spike.hyprland import HyprlandClient
        class Pinned(Compositor):
            """The plugin from before the install, loaded; the real client's checks on what may be unloaded."""
            plugin_unload, _plugin_command = HyprlandClient.plugin_unload, HyprlandClient._plugin_command
            epoch, session = self.EPOCH, self.SESSION
            def __init__(self, config):
                super().__init__(config)
                self.loaded, self.unloaded = ['hyprbars', 'xr-ownership'], []
            def plugins_loaded(self): return list(self.loaded)
            def _request(self, wire, timeout=None):
                if not wire.startswith('/plugin unload '): return super()._request(wire, timeout)
                self.unloaded.append(wire.split(' ', 2)[2]); self.loaded.remove('xr-ownership')
                return b'ok'
        compositor = Pinned(self.config)
        compositor.instance, compositor.runtime_dir = self.INSTANCE, self.root
        return compositor

    def recover_run(self, compositor, *flags):
        import contextlib
        import io
        from tools.xr_controller import __main__ as controller_main
        err = io.StringIO()
        with patch.object(controller_main, 'HyprlandClient', lambda *a, **k: compositor), contextlib.redirect_stderr(err):
            code = controller_main.main(['recover-run', '--consent', str(self.consent), '--journal', str(self.journal), *flags])
        return code, err.getvalue()

    def test_the_new_code_finishes_the_old_session_with_its_own_plugin(self):
        compositor = self.compositor()
        code, said = self.recover_run(compositor, '--clear-rule', 'XR-1')
        self.assertEqual((code, said), (0, ''))
        self.assertEqual(compositor.unloaded, [self.old['plugin']['path']])
        self.assertNotEqual(compositor.unloaded, [self.new['plugin']['path']])
        self.assertEqual(compositor.loaded, ['hyprbars'])  # nothing of the new install was loaded; Omarchy's stays
        self.assertNotIn(XR_1, compositor.outputs)
        saved = json.loads(self.journal.read_text())
        self.assertEqual([r['state'] for r in saved['operations']], ['done', 'done'])
        self.assertEqual(saved['broker']['epoch'], self.EPOCH)
        self.assertEqual(json.loads((self.data / 'viture-workspace/installed.json').read_text()), self.new)  # untouched

    def test_a_short_recovery_keeps_the_old_plugin_and_says_which_step_is_left(self):
        compositor = self.compositor()
        code, said = self.recover_run(compositor)  # no box: the line stays, so the old plugin still blocks
        self.assertEqual(code, 2)
        self.assertEqual(compositor.unloaded, [])
        self.assertEqual(compositor.loaded, ['hyprbars', 'xr-ownership'])
        self.assertIn('790c77a9-bb02-4d53-9c96-3c70f62bb571', said)
        self.assertIn('XR-1', said)

    def test_a_pin_that_changed_is_refused_before_the_compositor_hears_of_it(self):
        Path(self.old['plugin']['path']).chmod(0o700)
        Path(self.old['plugin']['path']).write_bytes(b'something else')
        compositor = self.compositor()
        code, said = self.recover_run(compositor, '--clear-rule', 'XR-1')
        self.assertEqual(code, 2)
        self.assertIn('recovery plugin pin unavailable', said)
        self.assertEqual((compositor.events, compositor.unloaded), ([], []))
        self.assertEqual(self.config.read_text(), CONFIG)

    def test_stop_reads_nothing_more_from_a_tree_that_has_moved_on(self):
        """A run imports lazily from the worktree, so anything first imported at Stop would be the new code in the old process
        (2026-11-03 13:16: an ImportError that way left a plugin behind). Everything recovery needs is in by the run's start."""
        import subprocess
        import sys
        repo = Path(__file__).resolve().parents[2]
        script = f'''
import sys
sys.path.insert(0, {str(repo)!r})
import tools.xr_controller.runner  # what the run's unit has loaded by the time the session is up
from tools.native_spike import managed
class Moved:
    def find_spec(self, name, path=None, target=None):
        if name.startswith('tools.'): raise ImportError('read from the moved tree at Stop: ' + name)
sys.meta_path.insert(0, Moved())
class Journal:
    broker, operations = dict(recovery_output='DP-1'), []
    def output_names(self): return ['XR-1']
class Client:
    def query(self, kind, *a, **k): return []
    def restore_layout(self, snapshot): pass
from tools.native_spike.journal import RecoveryResult
managed.recover = lambda journal, broker: RecoveryResult(False, ['x'], ['XR-1: refused'])
snapshot = dict(schema=1, run_id='run', monitor='DP-1', active=None, focus=None, focus_monitor=None, workspaces=[], windows=[])
result = managed.recover_restoring(Client(), Journal(), None, 'run', snapshot)
print('recovered', result.reasons)
'''
        done = subprocess.run([sys.executable, '-I', '-c', script], capture_output=True, text=True, timeout=30,
                              env=dict(os.environ, XDG_CONFIG_HOME=str(self.root), XDG_STATE_HOME=str(self.root)))
        self.assertEqual(done.returncode, 0, done.stderr[-600:])
        self.assertIn("recovered ['XR-1: refused']", done.stdout)


class Finding(unittest.TestCase):
    def test_what_omarchy_writes_is_removable(self):
        self.assertEqual(monitor_rules.find(OMARCHY_LINE), [dict(name='XR-1', line=1, removable=True)])

    def test_a_commented_out_rule_is_no_rule(self):
        self.assertEqual(monitor_rules.find('-- ' + OMARCHY_LINE), [])
        self.assertEqual(monitor_rules.find('hl.monitor({ output = "DP-1" }) -- not XR: output = "XR-1"\n'), [])

    def test_other_outputs_are_nothing_to_do_with_us(self):
        for line in ('hl.monitor({ output = "DP-1", scale = 1 })', 'hl.monitor({ output = "", scale = 1 })',
                     'hl.monitor({ output = "XR-0", scale = 1 })', 'hl.monitor({ output = "XR-SPIKE-1", scale = 1 })',
                     'hl.monitor({ output = "XR-1x", scale = 1 })'):
            self.assertEqual(monitor_rules.find(line), [], line)

    def test_spacing_quotes_and_a_trailing_comment_are_still_that_rule(self):
        for line in ("hl.monitor({output='XR-2'})", 'hl.monitor{ output = "XR-2" }', '  hl.monitor( { output="XR-2", scale=1 } )  -- mine'):
            found = monitor_rules.find(line)
            self.assertEqual([r['name'] for r in found], ['XR-2'], line)
        self.assertTrue(monitor_rules.find("hl.monitor({output='XR-2'})")[0]['removable'])
        self.assertFalse(monitor_rules.find('hl.monitor{ output = "XR-2" }')[0]['removable'])
        self.assertTrue(monitor_rules.find('  hl.monitor( { output="XR-2", scale=1 } )  -- mine')[0]['removable'])

    def test_anything_else_naming_a_desktop_is_found_but_never_removable(self):
        for text in ('hl.monitor({\n  output = "XR-1",\n})\n', 'local t = { output = "XR-1" }\n',
                     'if x then hl.monitor({ output = "XR-1" }) end\n', 'hl.monitor({ output = "XR-1" }) hl.monitor({ output = "DP-1" })\n',
                     'hl.monitor({ output = "XR-1", reserved = { top = 1 } })\n'):
            found = monitor_rules.find(text)
            self.assertEqual([r['removable'] for r in found], [False], text)

    def test_only_the_desktops_asked_about(self):
        text = OMARCHY_LINE + OMARCHY_LINE.replace('XR-1', 'XR-2')
        self.assertEqual([r['name'] for r in monitor_rules.find(text, ['XR-2'])], ['XR-2'])
        self.assertEqual([r['line'] for r in monitor_rules.find(text)], [1, 2])


class Clearing(unittest.TestCase):
    def test_a_file_changed_under_it_is_nothing_to_report_and_no_reload(self):
        with tempfile.TemporaryDirectory() as root:
            config = Path(root) / 'monitors.lua'; config.write_text(CONFIG)
            calls = []
            client = type('C', (), dict(monitors_config=config, reload_config=lambda self: calls.append('reload')))()
            with patch.object(monitor_rules, 'remove', return_value=None):
                self.assertEqual(monitor_rules.clear(client, ['XR-1'], notify=calls.append), [])
            self.assertEqual(calls, [])

    def test_a_reload_that_fails_is_a_reason_and_recovery_goes_on(self):
        with tempfile.TemporaryDirectory() as root:
            config = Path(root) / 'monitors.lua'; config.write_text(CONFIG)
            def reload_config(self): raise TimeoutError('compositor busy')
            client = type('C', (), dict(monitors_config=config, reload_config=reload_config))()
            [reason] = monitor_rules.clear(client, ['XR-1'])
            self.assertIn('compositor busy', reason)
            self.assertIn('XR-1', reason)


class Removing(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = self.root / 'monitors.lua'
        self.config.write_text(CONFIG); self.config.chmod(0o640)

    def others(self):
        return sorted(p.name for p in self.root.iterdir() if p.name != 'monitors.lua')

    def test_nothing_to_remove_touches_nothing(self):
        before = self.config.stat().st_ino
        self.assertIsNone(monitor_rules.remove(self.config, ['XR-2']))
        self.assertEqual((self.config.stat().st_ino, self.others()), (before, []))

    def test_the_file_keeps_its_mode_and_the_copy_has_the_same(self):
        backup = monitor_rules.remove(self.config, ['XR-1'])
        self.assertEqual(oct(self.config.stat().st_mode & 0o777), '0o640')
        self.assertEqual(oct(backup.stat().st_mode & 0o777), '0o640')

    def test_a_symlink_stays_one_and_its_target_changes(self):
        real = self.root / 'dotfiles.lua'; real.write_text(CONFIG)
        self.config.unlink(); self.config.symlink_to(real)
        backup = monitor_rules.remove(self.config, ['XR-1'])
        self.assertTrue(self.config.is_symlink())
        self.assertEqual(real.read_text(), CONFIG.replace(OMARCHY_LINE, ''))
        self.assertEqual(backup.parent, self.root)
        self.assertTrue(backup.name.startswith('dotfiles.lua.bak.xr-rule-'))

    def test_a_failed_swap_leaves_the_file_whole_and_no_temporary(self):
        with patch('os.replace', side_effect=OSError('disk full')), self.assertRaises(OSError):
            monitor_rules.remove(self.config, ['XR-1'])
        self.assertEqual(self.config.read_text(), CONFIG)
        self.assertEqual([n for n in self.others() if n.endswith('.tmp')], [])

    def test_line_endings_and_a_missing_last_newline_survive(self):
        text = 'a = 1\r\n' + OMARCHY_LINE.rstrip('\n') + '\r\nb = 2'
        self.config.write_bytes(text.encode())
        monitor_rules.remove(self.config, ['XR-1'])
        self.assertEqual(self.config.read_bytes(), b'a = 1\r\nb = 2')

    def test_no_file_is_no_rule(self):
        self.assertEqual(monitor_rules.leftovers(None, self.root / 'absent.lua'), [])

    def test_a_file_that_is_not_text_is_a_refusal_not_an_edit(self):
        self.config.write_bytes(b'\xff\xfe' + OMARCHY_LINE.encode())
        with self.assertRaises(ValueError): monitor_rules.leftovers(['XR-1'], self.config)
        with self.assertRaises(ValueError): monitor_rules.remove(self.config, ['XR-1'])

    def test_a_huge_file_is_refused(self):
        self.config.write_bytes(b' ' * (monitor_rules.LIMIT + 1))
        with self.assertRaises(ValueError): monitor_rules.leftovers(None, self.config)

    def test_the_path_follows_xdg_config_home(self):
        self.assertEqual(monitor_rules.config_path({'XDG_CONFIG_HOME': '/c', 'HOME': '/h'}), Path('/c/hypr/monitors.lua'))
        self.assertEqual(monitor_rules.config_path({'HOME': '/h'}), Path('/h/.config/hypr/monitors.lua'))


class Reach(unittest.TestCase):
    """This module edits a real person's config: only a client that says where it is can get it there."""
    def test_a_double_without_the_attribute_changes_nothing(self):
        self.assertIsNone(monitor_rules.config_of(object()))
        self.assertEqual(monitor_rules.clear(object(), ['XR-1']), [])

    def test_a_mock_answering_everything_changes_nothing(self):
        from unittest.mock import Mock
        client = Mock()
        self.assertIsNone(monitor_rules.config_of(client))
        self.assertEqual(monitor_rules.clear(client, ['XR-1']), [])
        client.reload_config.assert_not_called()
        monitor_rules.refuse_start(client)

    def test_no_run_outputs_changes_nothing(self):
        with tempfile.TemporaryDirectory() as root:
            config = Path(root) / 'monitors.lua'; config.write_text(CONFIG)
            client = type('C', (), dict(monitors_config=config))()
            self.assertEqual(monitor_rules.clear(client, []), [])
            self.assertEqual(config.read_text(), CONFIG)

    def test_the_real_client_says_where(self):
        from tools.native_spike.hyprland import HyprlandClient
        client = HyprlandClient('a' * 40 + '_123_456', Path('/nonexistent'))
        with patch.dict(os.environ, XDG_CONFIG_HOME='/c'):
            self.assertEqual(monitor_rules.config_of(client), Path('/c/hypr/monitors.lua'))

    def test_the_reload_is_a_request_and_a_refusal_is_an_error(self):
        from tools.native_spike.hyprland import HyprlandClient
        client = HyprlandClient('a' * 40 + '_123_456', Path('/nonexistent'))
        sent = []
        client._request = lambda text: (sent.append(text), b'ok')[1]
        client.reload_config()
        self.assertEqual(sent, ['/reload'])
        client._request = lambda text: b'no'
        with self.assertRaises(ValueError): client.reload_config()


class BrokerReplies(unittest.TestCase):
    REPLY = dict(id=XR_1, name='XR-1', state='blocked', physical=False, observation=None, fallback_usable=True)

    def test_a_step_is_understood_and_an_old_reply_has_none(self):
        OwnershipBrokerClient._identity(dict(self.REPLY), XR_1)
        for step in ('unavailable', 'evacuate', 'unreachable', 'rule', 'destroy'):
            OwnershipBrokerClient._identity(dict(self.REPLY, blocked=step), XR_1)

    def test_an_unknown_step_is_a_broken_reply(self):
        from tools.native_spike.hyprland import OwnershipBlocked
        with self.assertRaises(OwnershipBlocked): OwnershipBrokerClient._identity(dict(self.REPLY, blocked='nonsense'), XR_1)
        with self.assertRaises(OwnershipBlocked): OwnershipBrokerClient._identity(dict(self.REPLY, surprise=1), XR_1)


if __name__ == '__main__': unittest.main()
