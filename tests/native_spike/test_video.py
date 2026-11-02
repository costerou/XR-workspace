"""VR video, supervisor side (tools/native_spike/video.py): requests, renderer payloads (shared fixture with
spike/tests/video_test.cpp), format detection and limits, the file checks, the control state machine, descriptor passing
and the bind helper. Offline: temporary files and socketpairs only; ffmpeg/ffprobe are used on generated clips if present."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import unittest

from tools.native_spike import video, view, xr_control
from tools.native_spike.ipc import decode_control
from tools.native_spike.profiles import LayoutStore
from tools.native_spike.supervisor import Supervisor, _send_frame

FIXTURE = Path(__file__).with_name('fixtures') / 'video_payloads.json'
NAMES = ['XR-1', 'XR-2', 'XR-3']


def frames(data):
    return [json.loads(line) for line in (data or b'').decode().splitlines()]


class Clock:
    def __init__(self): self.now = 100.0
    def __call__(self): return self.now


def info(w=3840, h=1920, codec='hevc', **kw):
    out = {'width': w, 'height': h, 'codec': codec, 'fps': 30.0, 'duration': 60.0, 'projection': None, 'coverage': None, 'stereo': None}
    out.update(kw)
    return out


class PayloadTests(unittest.TestCase):
    def test_shared_fixture_matches_the_renderer_decoder(self):
        data = json.loads(FIXTURE.read_text())
        for p in data['valid']:
            with self.subTest(p=p):
                video.validate_renderer_payload(p)
                frame = video.video_frame('run1', 's1', 3, p)
                self.assertEqual(json.loads(frame)['payload'], p)
        for p in data['invalid']:
            with self.subTest(p=p), self.assertRaises(ValueError): video.validate_renderer_payload(p)

    def test_requests_on_the_control_socket(self):
        def frame(op, payload):
            return (json.dumps({'schema': 1, 'request_id': 9, 'run_id': 'run', 'session': 's', 'op': op, 'payload': payload}) + '\n').encode()
        msg = decode_control(frame('video_open', {'path': '/home/me/Videos/a_360.mp4'}))
        self.assertEqual(msg['payload'], {'path': '/home/me/Videos/a_360.mp4'})
        chosen = {'projection': '180', 'stereo': 'sbs', 'fov': 180}
        self.assertEqual(decode_control(frame('video_open', {'path': '/v.mkv', 'format': chosen}))['payload']['format'], chosen)
        for op in video.KEY_OPS:
            self.assertEqual(decode_control(frame(op, {}))['op'], op)
        bad = [('video_open', {}), ('video_open', {'path': 'relative.mp4'}), ('video_open', {'path': '/a\0b'}),
               ('video_open', {'path': '/' + 'x' * 4096}), ('video_open', {'path': '/v.mp4', 'extra': 1}),
               ('video_open', {'path': '/v.mp4', 'format': {'projection': 'cubemap', 'stereo': 'mono', 'fov': 180}}),
               ('video_pause', {'x': 1}), ('video_play', {})]
        for op, payload in bad:
            with self.subTest(op=op, payload=payload), self.assertRaises(ValueError): decode_control(frame(op, payload))

    def test_titles_formats_and_cycle(self):
        self.assertEqual(video.ascii_title('/x/Concert VR180_LR.mp4'), 'Concert VR180_LR.mp4')
        self.assertEqual(video.ascii_title('/x/café_360.mp4'), 'caf?_360.mp4')
        long = video.ascii_title('/x/' + 'a' * 80 + '.mp4')
        self.assertEqual(len(long), video.MAX_TITLE); self.assertTrue(long.endswith('...'))
        self.assertTrue(video._title_ok(long))
        self.assertEqual(video.format_label({'projection': 'fisheye', 'stereo': 'sbs', 'fov': 190}), 'Fisheye 190° side-by-side 3D')
        seen, fmt = [], dict(video.FORMAT_CYCLE[0])
        for _ in video.FORMAT_CYCLE:
            fmt = video.next_format(fmt); seen.append(fmt)
        self.assertEqual(seen[-1], video.FORMAT_CYCLE[0])
        self.assertEqual(len({json.dumps(f, sort_keys=True) for f in seen}), len(video.FORMAT_CYCLE))
        for f in video.FORMAT_CYCLE: video.validate_format(f)
        self.assertEqual(video.next_format({'projection': 'fisheye', 'stereo': 'tb', 'fov': 211}), video.FORMAT_CYCLE[0])


class DetectTests(unittest.TestCase):
    def detect(self, name, i=None):
        return video.detect_format(name, i if i is not None else info())

    def test_metadata_wins(self):
        full = info(projection='equirectangular', stereo='tb')
        self.assertEqual(self.detect('trip_180_LR.mp4', full), ({'projection': '360', 'stereo': 'tb', 'fov': 180}, 'metadata'))
        tiled = info(w=5760, h=2880, projection='tiled equirectangular', coverage=180.0, stereo='sbs')
        self.assertEqual(self.detect('x.mp4', tiled)[0], {'projection': '180', 'stereo': 'sbs', 'fov': 180})
        half = info(w=8192, h=4096, projection='half equirectangular', stereo='sbs')
        self.assertEqual(self.detect('x.mp4', half)[0]['projection'], '180')
        fish = info(w=8192, h=4096, projection='fisheye')
        self.assertEqual(self.detect('x.mp4', fish), ({'projection': 'fisheye', 'stereo': 'sbs', 'fov': 180}, 'shape'))
        for unsupported in ('cubemap', 'mesh', 'rectilinear'):
            with self.subTest(p=unsupported), self.assertRaises(video.VideoRejected): self.detect('x.mp4', info(projection=unsupported))

    def test_file_name_conventions(self):
        cases = {'beach_360_TB.mp4': ('360', 'tb', 180), 'beach_360_LR.mp4': ('360', 'sbs', 180), 'show_180_sbs.mkv': ('180', 'sbs', 180),
                 'show_180x180_3dh_LR.mp4': ('180', 'sbs', 180), 'clip_VR180_OU.mp4': ('180', 'tb', 180),
                 'canon_FISHEYE190.mp4': ('fisheye', 'sbs', 190), 'x_MKX200.mp4': ('fisheye', 'sbs', 200),
                 'cam_RF52_LR.mov': ('fisheye', 'sbs', 190), 'x_mono_360.mp4': ('360', 'mono', 180)}
        for name, (p, s, f) in cases.items():
            with self.subTest(name=name):
                fmt, source = self.detect(name, info(w=4096, h=2048))
                self.assertEqual(fmt, {'projection': p, 'stereo': s, 'fov': f})
                self.assertIn(source, ('filename', 'shape'))

    def test_shape_and_default(self):
        self.assertEqual(self.detect('a.mp4', info(w=3840, h=1920)), ({'projection': '360', 'stereo': 'mono', 'fov': 180}, 'shape'))
        self.assertEqual(self.detect('a.mp4', info(w=4096, h=4096)), ({'projection': '360', 'stereo': 'tb', 'fov': 180}, 'shape'))
        self.assertEqual(self.detect('a.mp4', info(w=7680, h=1920))[0]['stereo'], 'sbs')
        self.assertEqual(self.detect('a_180.mp4', info(w=5760, h=2880))[0], {'projection': '180', 'stereo': 'sbs', 'fov': 180})
        self.assertEqual(self.detect('a_180.mp4', info(w=2880, h=2880))[0]['stereo'], 'mono')
        self.assertEqual(self.detect('a_360.mp4', info(w=4096, h=4096))[0]['stereo'], 'tb')
        self.assertEqual(self.detect('a_sbs.mp4', info(w=5760, h=2880))[0]['projection'], '180')  # each eye 1:1
        self.assertEqual(self.detect('a_tb.mp4', info(w=3840, h=3840))[0]['projection'], '360')   # each eye 2:1
        self.assertEqual(self.detect('a.mp4', info(w=1920, h=1080)), ({'projection': '360', 'stereo': 'mono', 'fov': 180}, 'default'))
        self.assertEqual(video.detect_format('a.mp4', None)[1], 'default')

    def test_probe_parsing(self):
        data = {'streams': [{'codec_type': 'audio'}, {'codec_type': 'video', 'disposition': {'attached_pic': 1}, 'width': 300, 'height': 300},
                            {'codec_type': 'video', 'codec_name': 'hevc', 'width': 5760, 'height': 2880, 'avg_frame_rate': '30000/1001',
                             'side_data_list': [{'side_data_type': 'Spherical Mapping', 'projection': 'equirectangular',
                                                 'bound_left': 1073741824, 'bound_right': 1073741824},
                                                {'side_data_type': 'Stereo 3D', 'type': 'side by side', 'inverted': 0}]}],
                'format': {'duration': '12.5'}}
        i = video.parse_probe(data)
        self.assertEqual((i['width'], i['height'], i['codec'], round(i['fps'], 2), i['duration']), (5760, 2880, 'hevc', 29.97, 12.5))
        self.assertEqual((i['projection'], i['coverage'], i['stereo']), ('equirectangular', 180.0, 'sbs'))
        self.assertEqual(video.detect_format('x.mp4', i), ({'projection': '180', 'stereo': 'sbs', 'fov': 180}, 'metadata'))
        mkv = {'streams': [{'codec_type': 'video', 'codec_name': 'vp9', 'width': 4096, 'height': 4096, 'tags': {'stereo_mode': 'top_bottom'}}]}
        self.assertEqual(video.parse_probe(mkv)['stereo'], 'tb')
        for bad in ({}, {'streams': []}, {'streams': [{'codec_type': 'video', 'width': 0, 'height': 10}]}, []):
            with self.subTest(bad=bad), self.assertRaises(video.VideoRejected): video.parse_probe(bad)

    def test_limits(self):
        with self.assertRaises(video.VideoRejected): video.check_limits(info(w=16385, h=8192))
        self.assertEqual(video.check_limits(info(w=7680, h=3840, codec='hevc')), [])
        self.assertEqual(video.check_limits(info(w=3840, h=1920, codec='h264')), [])
        self.assertTrue(any('scaled down' in w for w in video.check_limits(info(w=11520, h=5760, codec='hevc'))))
        self.assertTrue(any('software' in w for w in video.check_limits(info(w=7680, h=3840, codec='h264'))))
        self.assertTrue(any('software' in w for w in video.check_limits(info(codec='prores'))))
        self.assertTrue(any('fps' in w for w in video.check_limits(info(w=7680, h=3840, codec='hevc', fps=90.0))))
        self.assertEqual(video.check_limits(None), ['format not checked (ffprobe unavailable)'])


@unittest.skipUnless(shutil.which('ffmpeg') and video.ffprobe_path(), 'ffmpeg/ffprobe needed to generate and probe a clip')
class RealProbeTests(unittest.TestCase):
    def test_probes_the_opened_descriptor(self):
        with tempfile.TemporaryDirectory() as tmp:
            clip = Path(tmp) / 'holiday.mkv'
            subprocess.run(['ffmpeg', '-nostdin', '-loglevel', 'error', '-f', 'lavfi', '-i', 'testsrc2=s=512x512:r=30:d=1',
                            '-c:v', 'libx264', '-preset', 'ultrafast', '-metadata:s:v:0', 'stereo_mode=top_bottom', str(clip)],
                           check=True, timeout=60)
            fd = video.open_video_file(str(clip))
            try:
                i = video.probe_fd(fd)
            finally:
                os.close(fd)
            self.assertEqual((i['width'], i['height'], i['codec'], i['stereo']), (512, 512, 'h264', 'tb'))
            self.assertEqual(video.detect_format(clip.name, i), ({'projection': '360', 'stereo': 'tb', 'fov': 180}, 'shape'))
            junk = Path(tmp) / 'notes.mp4'; junk.write_text('not a video')
            fd = video.open_video_file(str(junk))
            try:
                with self.assertRaises(video.VideoRejected): video.probe_fd(fd)
            finally:
                os.close(fd)


class FileTests(unittest.TestCase):
    def test_only_readable_regular_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            good = Path(tmp) / 'a_360.mp4'; good.write_bytes(b'x' * 10)
            fd = video.open_video_file(str(good))
            self.assertFalse(os.get_inheritable(fd)); self.assertTrue(os.get_blocking(fd)); os.close(fd)
            empty = Path(tmp) / 'empty.mp4'; empty.write_bytes(b'')
            fifo = Path(tmp) / 'pipe.mp4'; os.mkfifo(fifo)
            for bad in (str(Path(tmp) / 'missing.mp4'), tmp, str(empty), str(fifo), 'relative.mp4', '/dev/null'):
                with self.subTest(bad=bad), self.assertRaises(video.VideoRejected): video.open_video_file(bad)

    def test_probe_uses_the_descriptor_not_the_path(self):
        calls = []
        def run(argv, **kw):
            calls.append((argv, kw))
            return subprocess.CompletedProcess(argv, 0, json.dumps({'streams': [{'codec_type': 'video', 'codec_name': 'av1', 'width': 4096, 'height': 2048}]}).encode(), b'')
        self.assertEqual(video.probe_fd(7, ffprobe='/usr/bin/ffprobe', run=run)['codec'], 'av1')
        argv, kw = calls[0]
        self.assertEqual(argv[-1], 'file:/proc/self/fd/7')
        self.assertEqual(kw['pass_fds'], (7,))
        self.assertIn('-protocol_whitelist', argv)
        self.assertIsNone(video.probe_fd(7, ffprobe=''))
        timeout = lambda argv, **kw: (_ for _ in ()).throw(subprocess.TimeoutExpired(argv, 1))
        with self.assertRaises(video.VideoRejected): video.probe_fd(7, ffprobe='/usr/bin/ffprobe', run=timeout)
        failed = lambda argv, **kw: subprocess.CompletedProcess(argv, 1, b'', b'Invalid data')
        with self.assertRaises(video.VideoRejected): video.probe_fd(7, ffprobe='/usr/bin/ffprobe', run=failed)


class FakeSupervisor:
    def __init__(self): self.status, self.stopping_at = {}, None


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.clip = Path(self.tmp.name) / 'Beach_360_TB.mp4'; self.clip.write_bytes(b'v' * 100)
        self.clock = Clock()
        self.menu_binds, self.video_binds = [], []
        self.view = view.ViewControl(view.ViewState(NAMES), 'run1', 's1', lambda name: None, LayoutStore(Path(self.tmp.name) / 'l.json'),
                                     None, clock=self.clock, menu_binds=self.menu_binds.append)
        self.probed = []
        def probe(fd):
            self.probed.append(os.fstat(fd).st_ino)
            return info(w=4096, h=4096, codec='hevc')
        self.v = video.VideoControl(self.view, binds=self.video_binds.append, probe=probe, threads=False, clock=self.clock)
        self.sup = FakeSupervisor(); self.v.attach_supervisor(self.sup)

    def op(self, op, payload=None):
        return self.v.on_message({'op': op, 'payload': payload or {}})

    def join_binds(self):
        for t in self.v._bind_threads + self.view._menu_threads: t.join(2)

    def open(self, path=None):
        self.assertIsNone(self.op('video_open', {'path': str(path or self.clip)}))
        out = self.v.poll(self.clock())
        self.assertIsInstance(out, video.FdFrame)
        self.assertEqual(len(out.fds), 1)
        self.addCleanup(out.close_fds)
        return out

    def test_open_sends_the_descriptor_with_the_frame(self):
        out = self.open()
        self.assertEqual(os.fstat(out.fds[0]).st_ino, self.clip.stat().st_ino)
        self.assertEqual(self.probed, [self.clip.stat().st_ino])  # probed the very file that is sent
        [f] = frames(out)
        self.assertEqual(f['op'], 'video')
        self.assertEqual(f['payload'], {'action': 'open', 'projection': '360', 'stereo': 'tb', 'fov': 180, 'title': 'Beach_360_TB.mp4'})
        snap = self.v.snapshot()
        self.assertTrue(snap['active']); self.assertEqual(snap['label'], '360° top-bottom 3D'); self.assertTrue(snap['controls'])
        self.assertEqual(snap['state'], 'loading')
        self.join_binds(); self.assertEqual(self.video_binds, [True])

    def test_chosen_format_overrides_detection(self):
        chosen = {'projection': 'fisheye', 'stereo': 'sbs', 'fov': 200}
        self.op('video_open', {'path': str(self.clip), 'format': chosen})
        out = self.v.poll(); self.addCleanup(out.close_fds)
        self.assertEqual({k: frames(out)[0]['payload'][k] for k in chosen}, chosen)
        self.assertEqual(self.v.source, 'chosen')

    def test_open_closes_help_and_menu_first(self):
        self.view.on_message({'op': 'help'}); self.view.on_message({'op': 'menu_toggle'})
        self.assertTrue(self.view.help_visible and self.view.state.menu_open)
        out = self.open()
        ops = [(f['op'], f['payload'].get('visible', f['payload'].get('open'))) for f in frames(out)]
        self.assertEqual(ops[-1][0], 'video')
        self.assertIn(('placement_menu', False), ops); self.assertIn(('help', False), ops)
        self.assertFalse(self.view.help_visible or self.view.state.menu_open)
        self.join_binds(); self.assertEqual(self.menu_binds, [True, False])

    def test_keys_while_playing(self):
        self.open()
        self.v.controls = False  # as if the card had closed
        out = frames(self.op('video_pause'))
        self.assertEqual([f['payload'] for f in out], [{'action': 'controls', 'visible': True}, {'action': 'pause'}])
        seeks = [frames(self.op(op))[-1]['payload']['seconds'] for op in ('video_seek_back', 'video_seek_fwd', 'video_seek_back_big', 'video_seek_fwd_big')]
        self.assertEqual(seeks, [-10, 10, -60, 60])
        fmt = frames(self.op('video_format'))[-1]['payload']
        self.assertEqual(fmt, {'action': 'format', 'projection': '360', 'stereo': 'sbs', 'fov': 180})  # after 360 TB
        self.assertEqual(self.v.snapshot()['source'], 'chosen')
        off = frames(self.op('video_controls'))
        self.assertEqual([f['payload'] for f in off], [{'action': 'controls', 'visible': False}])
        self.assertFalse(self.v.controls)

    def test_desktop_keys_ignored_and_help_shows_the_controls(self):
        self.open()
        before = self.view.state.view()
        for op in ('zoom_in', 'next', 'spacing_up', 'mode_next', 'cursor_follow', 'menu_toggle', 'menu_inc'):
            with self.subTest(op=op): self.assertIsNone(self.op(op))
        self.assertEqual(self.view.state.view(), before)
        self.assertFalse(self.view.state.menu_open)
        self.assertEqual(frames(self.op('help'))[0]['payload'], {'action': 'controls', 'visible': False})
        self.assertFalse(self.view.help_visible)
        self.assertEqual(frames(self.op('recenter'))[0]['op'], 'recenter')  # the existing recenter, passed through
        self.assertEqual(frames(self.op('prediction'))[0]['op'], 'view')

    def test_exit_returns_to_the_desktops(self):
        self.open()
        self.assertEqual(frames(self.op('video_exit'))[0]['payload'], {'action': 'close'})
        self.assertFalse(self.v.active)
        self.assertIsNone(self.op('video_pause'))  # no video: nothing to control
        self.assertEqual(frames(self.op('zoom_in'))[0]['op'], 'view')  # desktop keys work again
        self.join_binds(); self.assertEqual(self.video_binds, [True, False])

    def test_controls_close_after_the_idle_timeout(self):
        self.open()
        self.sup.status = {'video': 'playing'}
        self.clock.now += view.OVERLAY_IDLE_CLOSE_S - 1
        self.assertIsNone(self.v.poll(self.clock()))
        self.clock.now += 1
        self.assertEqual(frames(self.v.poll(self.clock()))[0]['payload'], {'action': 'controls', 'visible': False})
        self.assertIsNone(self.v.poll(self.clock()))
        self.op('video_seek_fwd')  # any video key shows it again and restarts the timer
        self.clock.now += view.OVERLAY_IDLE_CLOSE_S - 1
        self.assertIsNone(self.v.poll(self.clock()))

    def test_renderer_error_and_missing_support_return_to_the_desktops(self):
        self.open()
        self.sup.status = {'presented_hz': 120.0, 'video': 'playing'}
        self.clock.now += 10; self.assertIsNone(self.v.poll(self.clock()))
        self.v.controls = False
        self.sup.status = {'video': 'error'}
        self.assertEqual(frames(self.v.poll(self.clock()))[0]['payload'], {'action': 'controls', 'visible': True})
        self.assertIn('could not play', self.v.snapshot()['error'])
        self.clock.now += video.ERROR_EXIT_S
        self.assertEqual(frames(self.v.poll(self.clock()))[-1]['payload'], {'action': 'close'})
        self.assertFalse(self.v.active)
        # A renderer that never reports video (built without libmpv, or CPU composition).
        self.sup.status = {}
        self.open()
        self.clock.now += video.START_GRACE_S - 1; self.assertIsNone(self.v.poll(self.clock()))
        self.clock.now += 1
        self.assertEqual(frames(self.v.poll(self.clock()))[-1]['payload'], {'action': 'close'})
        self.assertIn('not available', self.v.snapshot()['error'])

    def test_refused_files_send_nothing(self):
        for path in (str(Path(self.tmp.name) / 'missing.mp4'), self.tmp.name):
            self.assertIsNone(self.op('video_open', {'path': path}))
            self.assertIsNone(self.v.poll(self.clock()))
            self.assertFalse(self.v.active); self.assertTrue(self.v.snapshot()['error'])
        huge = video.VideoControl(self.view, probe=lambda fd: info(w=20000, h=10000), threads=False, clock=self.clock)
        huge.on_message({'op': 'video_open', 'payload': {'path': str(self.clip)}})
        self.assertIsNone(huge.poll(self.clock())); self.assertIn('larger than', huge.snapshot()['error'])

    def test_request_ids_keep_rising_across_view_and_video_frames(self):
        ids = [f['request_id'] for f in frames(self.view.initial_frame())]
        ids += [f['request_id'] for f in frames(self.open())]
        for op in ('video_pause', 'recenter', 'video_seek_fwd', 'prediction', 'video_exit', 'zoom_in'):
            ids += [f['request_id'] for f in frames(self.op(op))]
        self.assertEqual(ids, sorted(set(ids)))

    def test_status_reply_and_delegation(self):
        reply = self.v.status_reply({'presented_hz': 119.9})
        self.assertEqual(reply['video']['state'], 'off'); self.assertIn('capabilities', reply)
        self.assertNotIn('video_open', reply['capabilities'])
        self.assertIs(self.v.state, self.view.state)  # attribute delegation
        self.v.flush()

    def test_close_removes_the_keys_and_drops_unsent_files(self):
        self.open()
        self.op('video_open', {'path': str(self.clip)})  # result queued, never sent
        self.join_binds()
        self.v.close()
        self.assertEqual(self.video_binds, [True, False])
        self.assertTrue(self.v._results.empty())
        self.assertIsNone(self.v.binds)

    def test_config_reload_reinstalls_the_video_keys(self):
        self.open(); self.join_binds()
        self.v.on_events(b'configreloaded>>\n')
        self.join_binds()
        self.assertEqual(self.video_binds, [True, True])


class TransportTests(unittest.TestCase):
    def test_descriptor_travels_with_the_frame(self):
        a, b = socket.socketpair()
        self.addCleanup(a.close); self.addCleanup(b.close)
        with tempfile.TemporaryFile() as f:
            f.write(b'video bytes'); f.flush()
            frame = video.FdFrame(b'{"op":"video"}\n', [os.dup(f.fileno())])
            _send_frame(a, frame); frame.close_fds()
            data, fds, _, _ = socket.recv_fds(b, 4096, 4)
            self.assertEqual(data, b'{"op":"video"}\n')
            self.assertEqual(len(fds), 1)
            self.assertEqual(os.pread(fds[0], 11, 0), b'video bytes')
            os.close(fds[0])
        _send_frame(a, b'plain\n')
        self.assertEqual(b.recv(64), b'plain\n')

    def test_supervisor_accepts_the_video_state_and_attaches(self):
        class Child:
            def poll(self): return None
        control = video.VideoControl(view.ViewControl(view.ViewState(NAMES), 'r', 's', lambda n: None, None, None), threads=False)
        sup = Supervisor(Child(), lambda: None, control=control)
        self.assertIs(control.supervisor, sup)
        sup.status_bytes(b'{"presented_hz":120.0,"video":"playing"}\n')
        self.assertEqual(sup.status['video'], 'playing')
        sup.status_bytes(b'{"presented_hz":120.0,"video":"dancing"}\n')
        self.assertEqual(sup.status['video'], 'playing')  # invalid sample ignored

    def test_send_closes_our_copies_even_when_stopping(self):
        class Child:
            def poll(self): return None
        sup = Supervisor(Child(), lambda: None)
        sup.stopping_at = 1.0
        r, w = os.pipe(); os.close(w)
        frame = video.FdFrame(b'x\n', [r])
        sup._send(frame, None)
        with self.assertRaises(OSError): os.fstat(r)


class BindTests(unittest.TestCase):
    def test_ctrl_alt_o_and_the_modal_keys(self):
        self.assertIn(('CTRL + ALT + O', 'video_open'), view.BINDS)
        self.assertIn(['Ctrl+Alt+O', 'Play a 180/360 VR video'], view.help_rows())
        lua = view.install_binds_lua('/usr/bin/python3', '/repo/tools/native_spike/xr_control.py', '/run/user/1000/x/control.sock', 'run1', 's1')
        self.assertLess(len(lua), 4096)
        modal = video.install_video_binds_lua('/usr/bin/python3', '/repo/tools/native_spike/xr_control.py', '/run/user/1000/x/control.sock', 'run1', 's1')
        self.assertLess(len(modal), 4096)
        self.assertTrue(modal.startswith(video.remove_video_binds_lua()))  # never doubled
        for chord, op in video.VIDEO_BINDS: self.assertIn(f'hl.bind("{chord}", hl.dsp.exec_cmd(c .. "{op}"))', modal)
        for bad in (('/usr/bin/python3; rm', 'run1'), ('/usr/bin/python3', 'run 1')):
            with self.assertRaises(ValueError): video.install_video_binds_lua(bad[0], '/c.py', '/s.sock', bad[1], 's1')
        self.assertNotIn('xr_workspace_video_binds', view.remove_binds_lua())

    def test_bind_helper_agrees_with_the_supervisor(self):
        self.assertEqual(xr_control.VIDEO_OPS, video.VIDEO_OPS)
        self.assertEqual(xr_control.REMOVE_VIDEO_BINDS, video.remove_video_binds_lua())
        self.assertEqual(set(video.SEEK_S) | {'video_pause', 'video_format', 'video_controls', 'video_exit'}, set(video.KEY_OPS))
        self.assertEqual({op for _, op in video.VIDEO_BINDS}, set(video.KEY_OPS))

    def test_bind_helper_picks_a_file_and_sends_its_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'control.sock')
            server = socket.socket(socket.AF_UNIX); server.bind(path); server.listen(1)
            self.addCleanup(server.close)
            def received():
                conn, _ = server.accept()
                with conn: return decode_control(conn.recv(65536))
            self.assertEqual(xr_control.main(['x', path, 'run1', 's1', 'video_open'], picker=lambda: '/home/me/Videos/Été_360.mp4'), 0)
            first, second = received(), received()
            self.assertEqual((first['op'], first['payload']), ('video_exit', {}))  # back to the desktops: the picker is visible
            self.assertEqual((second['op'], second['payload']), ('video_open', {'path': '/home/me/Videos/Été_360.mp4'}))
            self.assertLess(first['request_id'], second['request_id'])
            self.assertEqual(xr_control.main(['x', path, 'run1', 's1', 'video_open'], picker=lambda: None), 0)  # cancelled
            self.assertEqual(received()['op'], 'video_exit')
            server.settimeout(0.2)
            with self.assertRaises(OSError): server.accept()
            self.assertEqual(xr_control.main(['x', os.path.join(tmp, 'gone.sock'), 'run1', 's1', 'video_open'],
                                             picker=lambda: self.fail('no picker without a run')), 1)
            self.assertEqual(xr_control.main(['x', path, 'run1', 's1', 'video_seek_fwd']), 0)
            conn, _ = server.accept(); self.assertEqual(decode_control(conn.recv(4096))['op'], 'video_seek_fwd'); conn.close()

    def test_picker_command(self):
        calls = []
        def run(argv, **kw):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, b'/home/me/Videos/a_180_LR.mp4\n', b'')
        if os.access('/usr/bin/zenity', os.X_OK):
            self.assertEqual(xr_control.pick_video(run=run, environ={'HOME': '/nonexistent'}), '/home/me/Videos/a_180_LR.mp4')
            self.assertEqual(calls[0][:2], ['/usr/bin/zenity', '--file-selection'])
            cancel = lambda argv, **kw: subprocess.CompletedProcess(argv, 1, b'', b'')
            self.assertIsNone(xr_control.pick_video(run=cancel, environ={}))
            bad = lambda argv, **kw: subprocess.CompletedProcess(argv, 0, b'relative.mp4\n', b'')
            self.assertIsNone(xr_control.pick_video(run=bad, environ={}))


if __name__ == '__main__':
    unittest.main()
