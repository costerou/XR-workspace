"""Incident bundles, offline: what they hold, what they must never hold, their size and how they are written and handed on."""
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import time
import unittest

from tools.xr_controller import incident_report as r
from tools.xr_controller.incident_detect import Episode, Signal
from tools.xr_controller.incident_report import RateLimit, Reports, Scrubber, build_bundle, fit, guess_cause, markdown, notify_text, run_hook, summary_line

RUN = '2298c7a18df736b9b6c4bde5b5877031'
HOME = '/home/testuser'


def scrubber(*keep): return Scrubber(home=HOME, user='testuser', host='testhost', keep=keep)


def episode(*kinds, ended=None, run=RUN):
    e = Episode(run, 1000.0)
    for i, kind in enumerate(kinds): e.add(Signal(1000.0 + i, kind, 'kernel' if kind == 'usb-drop' else 'renderer', f'{kind} happened'))
    e.ended = ended
    return e


def evidence(**over):
    base = dict(consent=dict(model='luma-ultra', output='DP-2', recovery_output='DP-1', tracking='xrlinuxdriver',
                             renderer=dict(path=f'{HOME}/.local/share/viture-workspace/bin/f0de154899b9/xr-native-spike', sha256='a' * 64),
                             plugin=dict(path=f'{HOME}/.local/share/viture-workspace/bin/f0de154899b9/libxr-ownership.so', sha256='b' * 64)),
                result=dict(run_id=RUN, renderer_returncode=1, complete=True, phase='done', error=None), unit=dict(code='exited', status='2/INVALIDARGUMENT'),
                source=dict(sha='13c8d5e' + '0' * 33, dirty=False), cores=[], status=dict(presented_hz=119.9, missed=0), mode='1920x1200@120',
                expected_mode='1920x1200@120', drm='connected', metrics={}, logs=dict(run=[], controller=[], driver=[], kernel=[]), suppressed={})
    return dict(base, **over)


class RedactionTests(unittest.TestCase):
    def assertGone(self, text, *secrets, **over):
        out = Scrubber(**dict(dict(home=HOME, user='testuser', host='testhost'), **over)).text(text)
        for secret in secrets: self.assertNotIn(secret, out)
        return out

    def test_video_urls_never_survive(self):
        for text, secret in (('open https://www.youtube.com/watch?v=dQw4w9WgXcQ now', 'dQw4w9WgXcQ'), ('stream http://cdn.example.net/a/b.m3u8?sig=abc', 'cdn.example.net'),
                             ('youtu.be/dQw4w9WgXcQ', 'dQw4w9WgXcQ'), ('www.example.com/private', 'example.com'),
                             ('example.org/watch?v=1 failed', 'example.org'), ('rtmp://live.example.tv/key', 'live.example'), ('file:///tmp/a.mp4', 'a.mp4')):
            self.assertIn('<url>', self.assertGone(text, secret))

    def test_local_video_files_never_survive(self):
        self.assertGone(f'opening {HOME}/Videos/holiday.mp4', 'holiday')
        self.assertGone('opening /mnt/media/My Film/holiday.mkv', 'holiday.mkv')
        self.assertGone('opening /srv/clips/holiday.webm', 'holiday')

    def test_things_that_look_like_tokens_never_survive(self):
        for secret in ('ghp_' + 'a1B2' * 9, 'github_pat_' + 'a1B2' * 9, 'sk-ant-' + 'a1B2' * 9, 'xoxb-1234567890-abcdefghij', 'AKIA' + 'ABCDEFGHIJ012345',
                       'AIza' + 'a1B2' * 9, 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9P',
                       'deadbeef' * 5, 'c2VjcmV0IHZhbHVlIHRoYXQgaXMgbG9uZyBlbm91Z2g9PQ'):
            self.assertIn('<', self.assertGone(f'value {secret} here', secret))

    def test_secrets_by_name_never_survive(self):
        for text in ('password=hunter2hunter2', 'api_key: hunter2hunter2', 'AUTH_TOKEN="hunter2hunter2"', 'client_secret = hunter2hunter2',
                     'cookie: hunter2hunter2', 'Authorization: Bearer hunter2hunter2', 'Bearer hunter2hunter2'):
            self.assertGone(text, 'hunter2hunter2')

    def test_a_private_key_block_never_survives(self):
        self.assertGone('-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXk\n-----END OPENSSH PRIVATE KEY-----', 'b3BlbnNzaC1rZXk')

    def test_home_directory_secrets_never_survive(self):
        for path in ('.ssh/id_ed25519', '.gnupg/secring.gpg', '.aws/credentials', '.config/gh/hosts.yml', '.netrc', '.env'):
            out = self.assertGone(f'cannot read {HOME}/{path}: denied', path)
            self.assertIn('~/<private-path>', out)
            self.assertIn('denied', out)

    def test_a_home_path_is_kept_only_when_it_is_this_apps_own(self):
        self.assertIn('~/.local/state/xr_driver/driver.log', self.assertGone(f'{HOME}/.local/state/xr_driver/driver.log', HOME))
        self.assertIn('~/.t3/worktrees/viture-workspace/x/tools/runner.py', self.assertGone(f'File "{HOME}/.t3/worktrees/viture-workspace/x/tools/runner.py", line 3', HOME))
        self.assertGone(f'{HOME}/Documents/taxes-2026.pdf', 'taxes')
        self.assertGone(f'{HOME}/.config/hypr/hyprland.lua', 'hyprland.lua')

    def test_another_users_home(self):
        self.assertGone('/home/someone/notes.txt', 'someone', 'notes.txt')

    def test_a_tilde_path_is_the_same_path(self):
        self.assertGone('~/.ssh/id_rsa', 'id_rsa')

    def test_window_titles_and_clipboards_take_the_whole_line(self):
        for line in ('{"address":"0x1","title":"Online banking - Mail"}', 'title: Online banking - Mail', 'initialTitle=Online banking - Mail',
                     'window title Online banking - Mail', 'clipboard: Online banking - Mail', 'wl-paste said Online banking - Mail'):
            out = self.assertGone(line, 'Online banking', 'Mail')
            self.assertEqual(out, r.REMOVED_LINE)

    def test_addresses_serials_and_names(self):
        out = self.assertGone('testuser on testhost from a@example.com 10.0.0.15 28:94:01:b8:9b:e3 SerialNumber: 1234333333',
                              'testuser', 'testhost', 'a@example.com', '10.0.0.15', '28:94:01:b8:9b:e3', '1234333333')
        self.assertIn('<serial>', out)

    def test_what_a_diagnosis_needs_survives(self):
        for line in ('usb 5-2.1: USB disconnect, device number 45', 'dispatch: closed, removed or disconnected',
                     '#0  0x00007fa89ea9a11c n/a (libc.so.6 + 0x9a11c)', '#5  0x00007fa89ee9a5ed _ZSt9terminatev (libstdc++.so.6 + 0x9a5ed)',
                     '#7  0x000055722103b912 _ZN2xr12Presentation17draw_test_patternEv (xr-native-spike + 0x8912)',
                     'ValueError: recovery plugin pin mismatch', '[E][libglasses] Failed to send USB command - MsgID: 0x0007, error code: -1',
                     "viture-xr-unit.service: Failed with result 'exit-code'.", 'libc.so.6', 'tools.xr_controller.runner',
                     'amdgpu 0000:03:00.0: [drm] REG_WAIT timeout 1us * 100 tries - dcn32_program_compbuf_size line:149'):
            self.assertEqual(scrubber().text(line), line)

    def test_the_run_id_stays_but_other_hex_does_not(self):
        out = scrubber(RUN).text(f'viture-xr-{RUN}.service and 022b29d10a4d93024b7b7a611f0d5acc')
        self.assertIn(RUN, out)
        self.assertNotIn('022b29d1', out)

    def test_every_string_in_a_tree_is_cleaned(self):
        out = scrubber().tree(dict(a=['https://x.example/y', dict(b=f'{HOME}/.ssh/id_rsa')], n=3, ok=True, none=None))
        self.assertEqual(out['a'][0], '<url>')
        self.assertEqual(out['n'], 3)
        self.assertIs(out['ok'], True)
        self.assertIsNone(out['none'])
        self.assertNotIn('id_rsa', json.dumps(out))

    def test_lines_stay_single_and_short(self):
        out = scrubber().text('a\x1b[31mb\nc\td\x00' + 'x y ' * 500)
        self.assertNotIn('\n', out)
        self.assertNotIn('\x1b', out)
        self.assertLessEqual(len(out), r.LINE_MAX + 1)


class SummaryTests(unittest.TestCase):
    def test_a_plain_line_for_every_kind(self):
        from tools.xr_controller.incident_detect import KINDS
        for kind in KINDS:
            for ended in (None, 'ended', 'stopped'):
                line = summary_line(kind, ended)
                self.assertTrue(line[0].islower() or line.split()[0] in ('Hyprland', 'Recover'), line)  # it follows "hit a problem:"
                self.assertNotIn(kind, line)
                self.assertLess(len(line), 110)

    def test_it_says_what_became_of_the_session(self):
        self.assertEqual(summary_line('usb-drop', 'ended'), "the glasses' USB connection dropped and the session ended")
        self.assertEqual(summary_line('usb-drop', None), "the glasses' USB connection dropped but the session kept running")
        self.assertEqual(summary_line('usb-drop', 'stopped'), "the glasses' USB connection dropped and the session was stopped")

    def test_the_notification_is_the_agreed_sentence(self):
        self.assertEqual(notify_text('the XR renderer crashed'), 'XR Workspace hit a problem: the XR renderer crashed. A report was saved.')


class GuessTests(unittest.TestCase):
    def test_a_usb_drop_seen_by_the_kernel(self):
        guess = guess_cause(episode('usb-drop', 'renderer-exit', ended='ended'), evidence())
        self.assertEqual(guess['confidence'], 'high')
        self.assertIn('USB', guess['text'])
        self.assertIn('cable', guess['text'])

    def test_a_usb_drop_only_the_driver_saw_is_less_sure(self):
        e = Episode(RUN, 1000.0)
        e.add(Signal(1000.0, 'usb-drop', 'driver-log', 'x'))
        self.assertEqual(guess_cause(e, evidence())['confidence'], 'medium')

    def test_a_core_dump_names_where_it_crashed(self):
        core = dict(exe='xr-native-spike', signal='SIGSEGV', frames=['#0  0x1 _ZN2xr12Presentation17draw_test_patternEv (xr-native-spike + 0x8912)'])
        guess = guess_cause(episode('renderer-crash', ended='ended'), evidence(cores=[core]))
        self.assertEqual(guess['confidence'], 'high')
        self.assertIn('SIGSEGV', guess['text'])
        self.assertIn('draw_test_pattern', guess['text'])

    def test_a_crash_during_a_usb_drop_says_both(self):
        core = dict(exe='xr-native-spike', signal='SIGSEGV', frames=[])
        self.assertIn('USB', guess_cause(episode('renderer-crash', 'usb-drop', ended='ended'), evidence(cores=[core]))['text'])

    def test_a_gpu_reset(self):
        kernel = ['2026-11-03T20:00:00-05:00 kernel: amdgpu 0000:03:00.0: amdgpu: GPU reset begin!']
        guess = guess_cause(episode('present-stall'), evidence(logs=dict(run=[], controller=[], driver=[], kernel=kernel)))
        self.assertIn('graphics', guess['text'])

    def test_out_of_memory(self):
        self.assertIn('memory', guess_cause(episode('run-killed', ended='ended'), evidence(unit=dict(result='oom-kill')))['text'])

    def test_hyprland_gone_without_a_crash_record(self):
        guess = guess_cause(episode('hyprland-crash', ended='ended'), evidence())
        self.assertIn('logout', guess['text'])

    def test_video_lost_without_usb_trouble(self):
        self.assertIn('display', guess_cause(episode('video-lost'), evidence())['text'])

    def test_the_renderers_own_words(self):
        run = ['2026-11-03T19:18:40-05:00 python3[1]: dispatch: closed, removed or disconnected']
        guess = guess_cause(episode('renderer-exit', ended='ended'), evidence(logs=dict(run=run, controller=[], driver=[], kernel=[])))
        self.assertIn('Hyprland closed', guess['text'])

    def test_a_frozen_renderer(self):
        self.assertIn('froze', guess_cause(episode('watchdog', ended='ended'), evidence(result=dict(stop_reason='heartbeat-timeout')))['text'])

    def test_when_it_cannot_tell_it_says_so(self):
        guess = guess_cause(episode('tracking-lost'), evidence())
        self.assertEqual(guess['confidence'], 'low')


class BundleTests(unittest.TestCase):
    def bundle(self, e=None, **over):
        e = e or episode('usb-drop', 'renderer-exit', ended='ended')
        return build_bundle(e, evidence(**over), 1010.0, scrubber(RUN))

    def test_everything_a_diagnosis_asks_for_is_there(self):
        b = self.bundle(status=dict(presented_hz=119.9, missed=2), metrics=dict(tracking=dict(lost_events=1)))
        for key in ('schema', 'id', 'kind', 'summary', 'created_at', 'first_signal_at', 'session', 'run_id', 'timeline', 'cause', 'versions', 'glasses',
                    'display', 'renderer', 'recovery', 'unit', 'metrics', 'logs', 'core_dumps', 'suppressed_since_last', 'truncated'):
            self.assertIn(key, b)
        self.assertEqual(b['kind'], 'usb-drop')
        self.assertEqual(b['run_id'], RUN)
        self.assertEqual(b['session'], 'ended')
        self.assertEqual(b['versions']['source_sha'], '13c8d5e' + '0' * 33)
        self.assertEqual(b['versions']['bin'], 'f0de154899b9')
        self.assertEqual(b['versions']['renderer_sha256'], 'a' * 64)
        self.assertEqual(b['glasses'], dict(model='luma-ultra', output='DP-2', recovery_output='DP-1', tracking=True, tracking_driver=None))
        self.assertEqual(b['display'], dict(mode='1920x1200@120', expected_mode='1920x1200@120', drm='connected'))
        self.assertEqual(b['renderer']['returncode'], 1)
        self.assertEqual(b['metrics']['last'], dict(presented_hz=119.9, missed=2))
        self.assertEqual(b['metrics']['exit_report'], dict(tracking=dict(lost_events=1)))
        self.assertEqual([t['kind'] for t in b['timeline']], ['usb-drop', 'renderer-exit'])
        self.assertTrue(b['timeline'][0]['at'].endswith('Z'))

    def test_the_id_is_the_utc_time_and_the_kind(self):
        self.assertEqual(self.bundle()['id'], time.strftime('%Y%m%dT%H%M%SZ', time.gmtime(1010.0)) + '-usb-drop')

    def test_the_id_of_every_kind_survives_redaction(self):
        # A long kind makes an id look like a token; the id is the file's name.
        from tools.xr_controller.incident_detect import KINDS
        for kind in KINDS:
            self.assertEqual(build_bundle(episode(kind), evidence(), 1010.0, scrubber(RUN))['id'], time.strftime('%Y%m%dT%H%M%SZ', time.gmtime(1010.0)) + f'-{kind}')

    def test_a_renderer_killed_by_a_signal_is_named(self):
        b = self.bundle(result=dict(renderer_returncode=-11))
        self.assertEqual(b['renderer']['signal'], 'SIGSEGV')

    def test_exit_reports_are_left_out_of_the_logs(self):
        report = json.dumps(dict(kind='managed_output_capture', outputs=[], theme=dict(accent=[1, 2, 3])))
        b = self.bundle(logs=dict(run=[f'2026-11-03T19:18:40-05:00 python3[1]: {report}', 'kept'], controller=[], driver=[], kernel=[]))
        self.assertEqual(b['logs']['run'], ['kept'])

    def test_nothing_private_gets_through_any_field(self):
        dirty = f'see https://youtu.be/PRIVATEVIDEO {HOME}/.ssh/id_rsa password=hunter2hunter2 ' + 'f00dcafe' * 6
        title = '{"title":"Online banking - Mail"}'
        b = self.bundle(result=dict(renderer_returncode=1, error=dirty), logs=dict(run=[dirty, title], controller=[dirty], driver=[dirty], kernel=[dirty]),
                        status=dict(capture_path=dirty), cores=[dict(exe=dirty, signal='SIGSEGV', frames=[dirty])], unit=dict(status=dirty))
        text = json.dumps(b) + markdown(b)
        for secret in ('PRIVATEVIDEO', 'id_rsa', 'hunter2', 'f00dcafe', 'Online banking', 'testuser'): self.assertNotIn(secret, text)

    def test_counted_repeats_reach_the_report(self):
        self.assertEqual(self.bundle(suppressed={'usb-drop': 3})['suppressed_since_last'], {'usb-drop': 3})


class SizeTests(unittest.TestCase):
    def big(self, lines=2000):
        line = 'usb 5-2.1: a line that goes on for a while ' * 8
        return dict(run=[f'{i} {line}' for i in range(lines)], controller=[f'{i} {line}' for i in range(lines)],
                    driver=[f'{i} {line}' for i in range(lines)], kernel=[f'{i} {line}' for i in range(lines)])

    def test_a_bundle_never_passes_the_cap(self):
        b = build_bundle(episode('usb-drop'), evidence(logs=self.big()), 1010.0, scrubber(RUN))
        self.assertLessEqual(len(json.dumps(b).encode()), r.MAX_BUNDLE)
        self.assertEqual(r.MAX_BUNDLE, 256 * 1024)
        self.assertTrue(b['truncated'])

    def test_the_newest_lines_are_the_ones_kept(self):
        b = build_bundle(episode('usb-drop'), evidence(logs=self.big()), 1010.0, scrubber(RUN))
        for section in b['logs'].values(): self.assertTrue(section[-1].startswith('1999 '), section[-1][:20])

    def test_a_small_bundle_is_left_alone(self):
        self.assertFalse(build_bundle(episode('usb-drop'), evidence(), 1010.0, scrubber(RUN))['truncated'])

    def test_it_even_survives_a_huge_core_dump_list(self):
        cores = [dict(exe='x', signal='s', frames=['#0 ' + 'y' * 300] * 64)] * 200
        b = build_bundle(episode('renderer-crash'), evidence(cores=cores, logs=self.big()), 1010.0, scrubber(RUN))
        self.assertLessEqual(len(json.dumps(b).encode()), r.MAX_BUNDLE)

    def test_fit_stops_when_nothing_is_left_to_cut(self):
        b = dict(logs=dict(run=[]), core_dumps=[], timeline=[], blob='x' * 5000, truncated=False)
        self.assertIs(fit(b, 100), b)  # the fields it may not cut stay; the caller's cap holds for everything it can

    def test_the_summary_opens_with_the_one_line_as_a_sentence(self):
        b = build_bundle(episode('renderer-exit', ended='ended'), evidence(), 1010.0, scrubber(RUN))
        self.assertIn('The XR renderer stopped unexpectedly and the session ended.', markdown(b))

    def test_the_summary_stays_short(self):
        b = build_bundle(episode('usb-drop'), evidence(logs=self.big()), 1010.0, scrubber(RUN))
        self.assertLessEqual(len(markdown(b).encode()), r.MAX_SUMMARY)


class ReportsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        os.chmod(self.root, 0o700)
        self.dir = self.root / 'incidents'
        self.reports = Reports(self.dir)

    def write(self, stamp='20261104T010612Z-usb-drop'):
        b = dict(id=stamp, kind='usb-drop', summary='a line', cause=dict(text='t', confidence='low'), timeline=[], run_id=RUN, created_at='x', session=None)
        return self.reports.write(b, '# summary\n')

    def test_the_folder_and_the_files_are_private(self):
        path = self.write()
        self.assertEqual(path, self.dir / '20261104T010612Z-usb-drop.json')
        self.assertEqual(stat.S_IMODE(self.dir.stat().st_mode), 0o700)
        for name in (path, path.with_suffix('.md')): self.assertEqual(stat.S_IMODE(name.stat().st_mode), 0o600)
        self.assertEqual(json.loads(path.read_text())['kind'], 'usb-drop')
        self.assertEqual(path.with_suffix('.md').read_text(), '# summary\n')

    def test_a_folder_someone_loosened_is_refused(self):
        self.write()
        os.chmod(self.dir, 0o755)
        with self.assertRaises(PermissionError): self.write('20261104T010613Z-usb-drop')

    def test_no_temporary_files_are_left(self):
        self.write()
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ['20261104T010612Z-usb-drop.json', '20261104T010612Z-usb-drop.md'])

    def test_the_summary_is_there_before_the_json_appears(self):
        # A watcher picks up the .json: it must find its .md next to it.
        seen = []
        real = os.replace
        def spy(src, dst, **kw):
            seen.append(dst)
            real(src, dst, **kw)
        r.os.replace = spy
        self.addCleanup(setattr, r.os, 'replace', real)
        self.write()
        self.assertEqual(seen, ['20261104T010612Z-usb-drop.md', '20261104T010612Z-usb-drop.json'])

    def test_two_in_the_same_second_keep_both(self):
        first, second = self.write(), self.write()
        self.assertNotEqual(first, second)
        self.assertEqual(second.name, '20261104T010612Z-usb-drop-2.json')
        self.assertTrue(second.with_suffix('.md').exists())

    def test_a_symlink_in_the_folder_is_not_followed(self):
        self.write()
        target = self.root / 'elsewhere'
        target.write_text('safe')
        (self.dir / '20261104T010613Z-usb-drop.md').symlink_to(target)
        self.write('20261104T010613Z-usb-drop')
        self.assertEqual(target.read_text(), 'safe')

    def test_state_lives_outside_what_a_watcher_globs(self):
        self.reports.save_state(dict(a=1))
        self.assertEqual(self.reports.load_state(), dict(a=1))
        self.assertEqual(sorted(self.dir.glob('*.json')), [])

    def test_a_damaged_state_file_starts_clean(self):
        self.reports.save_state(dict(a=1))
        (self.dir / r.STATE).write_text('{not json')
        self.assertEqual(self.reports.load_state(), {})

    def test_no_state_yet(self):
        self.assertEqual(self.reports.load_state(), {})


class RateLimitTests(unittest.TestCase):
    def test_a_repeat_while_the_session_lives_waits_ten_minutes(self):
        limit = RateLimit()
        self.assertTrue(limit.allow('usb-drop', None, 0.0))
        self.assertFalse(limit.allow('usb-drop', None, 599.0))
        self.assertTrue(limit.allow('usb-drop', None, 600.0))

    def test_another_kind_is_not_a_repeat(self):
        limit = RateLimit()
        self.assertTrue(limit.allow('usb-drop', None, 0.0))
        self.assertTrue(limit.allow('tracking-lost', None, 1.0))

    def test_a_session_ending_is_news_even_right_after_a_drop_it_survived(self):
        limit = RateLimit()
        self.assertTrue(limit.allow('usb-drop', None, 0.0))
        self.assertTrue(limit.allow('usb-drop', 'ended', 100.0))

    def test_a_crash_loop_is_held_to_one_a_minute(self):
        limit = RateLimit()
        self.assertTrue(limit.allow('renderer-crash', 'ended', 0.0))
        self.assertFalse(limit.allow('renderer-crash', 'ended', 30.0))
        self.assertTrue(limit.allow('renderer-crash', 'ended', 60.0))

    def test_repeats_are_counted_for_the_next_report(self):
        limit = RateLimit()
        limit.allow('usb-drop', None, 0.0)
        limit.allow('usb-drop', None, 1.0)
        limit.allow('usb-drop', None, 2.0)
        self.assertTrue(limit.allow('usb-drop', None, 700.0))
        self.assertEqual(limit.take_suppressed(), {'usb-drop': 2})
        self.assertEqual(limit.take_suppressed(), {})

    def test_an_hour_holds_a_limited_number_in_all(self):
        limit = RateLimit()
        allowed = sum(limit.allow(f'kind-{i}', 'ended', float(i)) for i in range(100))
        self.assertEqual(allowed, r.MAX_PER_HOUR)
        self.assertTrue(limit.allow('late', 'ended', 3700.0))

    def test_it_survives_a_controller_restart(self):
        limit = RateLimit()
        limit.allow('usb-drop', None, 0.0)
        limit.allow('usb-drop', None, 1.0)
        again = RateLimit.restore(json.loads(json.dumps(limit.save())))
        self.assertFalse(again.allow('usb-drop', None, 2.0))
        self.assertEqual(again.take_suppressed(), {'usb-drop': 2})

    def test_a_damaged_saved_state_is_a_fresh_one(self):
        for bad in (None, 3, dict(last='x'), dict(last={'a': 'b'}, hour=[None], suppressed=[])):
            self.assertTrue(RateLimit.restore(bad).allow('usb-drop', None, 0.0))


class HookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.hook = self.root / 'on-incident'
        self.log = self.root / 'hook.log'
        self.bundle = self.root / 'a report; touch hacked $(touch hacked2).json'
        self.bundle.write_text('{}')

    def script(self, body, mode=0o755):
        self.hook.write_text('#!/bin/sh\n' + body + '\n')
        os.chmod(self.hook, mode)

    def run_it(self, **kw): return run_hook(self.hook, self.bundle, self.log, **kw)

    def test_no_hook_is_the_normal_case_and_runs_nothing(self):
        self.assertIsNone(self.run_it())
        self.assertFalse(self.log.exists())

    def test_it_gets_the_bundle_path_as_one_argument(self):
        self.script(f'printf "%s\\n" "$#" "$1" > "{self.root}/args"')
        self.assertEqual(self.run_it(), 0)
        self.assertEqual((self.root / 'args').read_text().splitlines(), ['1', str(self.bundle)])

    def test_no_shell_ever_reads_the_path(self):
        self.script('exit 0')
        self.run_it()
        self.assertEqual([p.name for p in self.root.glob('hacked*')], [])

    def test_output_and_exit_code_are_logged_privately(self):
        self.script('echo hello from the bot; echo and stderr >&2; exit 3')
        self.assertEqual(self.run_it(), 3)
        text = self.log.read_text()
        for expected in ('hello from the bot', 'and stderr', 'rc=3'): self.assertIn(expected, text)
        self.assertEqual(stat.S_IMODE(self.log.stat().st_mode), 0o600)

    def test_a_hook_that_hangs_is_killed_with_its_children(self):
        self.script(f'sleep 300 &\necho $! > "{self.root}/child"\nwait')
        started = time.monotonic()
        self.assertEqual(self.run_it(timeout=0.5), 'timeout')
        self.assertLess(time.monotonic() - started, 5)
        child = int((self.root / 'child').read_text())
        deadline = time.monotonic() + 3
        while Path(f'/proc/{child}').exists() and Path(f'/proc/{child}/stat').read_text().rsplit(')', 1)[1].split()[0] != 'Z':
            self.assertLess(time.monotonic(), deadline, 'child survived')
            time.sleep(.02)
        self.assertIn('rc=timeout', self.log.read_text())

    def test_a_file_that_is_not_executable_does_not_run(self):
        self.script(f'touch "{self.root}/ran"', mode=0o644)
        self.assertEqual(self.run_it(), 'refused')
        self.assertFalse((self.root / 'ran').exists())
        self.assertIn('not executable', self.log.read_text())

    def test_a_hook_others_could_write_does_not_run(self):
        self.script(f'touch "{self.root}/ran"', mode=0o777)
        self.assertEqual(self.run_it(), 'refused')
        self.assertFalse((self.root / 'ran').exists())

    def test_a_directory_is_not_a_hook(self):
        self.hook.mkdir()
        self.assertEqual(self.run_it(), 'refused')

    def test_a_chatty_hook_is_cut_short(self):
        self.script('yes hello | head -c 2000000')
        self.run_it()
        self.assertLess(self.log.stat().st_size, r.MAX_HOOK_OUTPUT + 1024)

    def test_the_log_does_not_grow_without_bound(self):
        self.log.write_bytes(b'x' * (r.MAX_HOOK_LOG + 1))
        os.chmod(self.log, 0o600)
        self.script('echo fresh')
        self.run_it()
        self.assertLess(self.log.stat().st_size, 2048)
        self.assertIn('fresh', self.log.read_text())
        self.assertEqual(stat.S_IMODE(self.log.with_name('hook.log.1').stat().st_mode), 0o600)

    def test_a_hook_that_cannot_start_is_a_logged_failure(self):
        self.hook.write_text('#!/nonexistent/interpreter\n')
        os.chmod(self.hook, 0o755)
        self.assertEqual(self.run_it(), 'failed')
        self.assertIn('rc=failed', self.log.read_text())

    def test_the_hook_path_follows_the_config_home(self):
        self.assertEqual(r.hook_path('/cfg'), Path('/cfg/viture-workspace/on-incident'))


if __name__ == '__main__': unittest.main()
