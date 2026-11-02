"""Web VR video (tools/native_spike/web_video.py, docs/delightxr.md): the bookmarklet's object, DelightXR formats, source
choice, the clipboard helper, the ffmpeg relay end to end against a local HTTP server (generated clips, never
downloaded), VideoControl with web streams (open, seek by relay restart, resume after a dropped link, close), and the
bookmarklet itself in headless Chromium with a throwaway profile and every host unresolvable.

Offline: the only network is 127.0.0.1 inside this test (allow_local=True, which the run never sets)."""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import unittest

from tools.delightxr import build
from tools.native_spike import video, view, web_video as wv, xr_control
from tools.native_spike.ipc import decode_control
from tools.native_spike.profiles import LayoutStore

NAMES = ['XR-1', 'XR-2', 'XR-3']
FFMPEG, FFPROBE, CHROMIUM = shutil.which('ffmpeg'), shutil.which('ffprobe'), shutil.which('chromium')


def web(*sources, **kw):
    out = {'viture_xr_video': 1, 'page': 'https://vr.example.com/watch/42', 'title': 'Sunset beach', 'format': 'STEREO_180_LR',
           'fov': None, 'start': 0, 'ua': 'Mozilla/5.0 test', 'sources': list(sources) or [{'src': 'https://cdn.example.com/a.mp4', 'via': 'dl8'}]}
    out.update(kw)
    return out


def frames(data):
    return [json.loads(line) for line in (data or b'').decode().splitlines()]


class Clock:
    def __init__(self): self.now = 100.0
    def __call__(self): return self.now


class ValidationTests(unittest.TestCase):
    def test_urls(self):
        self.assertEqual(wv.validate_url('https://cdn.example.com/v/a.mp4?sig=x&e=1'), 'https://cdn.example.com/v/a.mp4?sig=x&e=1')
        for bad in ('', 'ftp://x/a.mp4', 'file:///etc/passwd', 'data:video/mp4;base64,AA', 'javascript:alert(1)',
                    'https://user:pw@cdn.example.com/a.mp4', 'https://cdn.example.com/a b.mp4', 'https://cdn.example.com/a\n.mp4',
                    'https://localhost/a.mp4', 'http://127.0.0.1:8080/a.mp4', 'http://[::1]/a.mp4', 'http://192.168.1.4/a.mp4',
                    'http://10.0.0.1/a.m3u8', 'http://169.254.169.254/latest', 'https://cdn.example.com:99999/a.mp4',
                    'https://x.example.com/' + 'a' * 4096, None, 7):
            with self.subTest(bad=bad), self.assertRaises(video.VideoRejected): wv.validate_url(bad)
        with self.assertRaises(video.VideoRejected) as cm: wv.validate_url('blob:https://vr.example.com/1234')
        self.assertIn('blob', str(cm.exception))
        self.assertEqual(wv.validate_url('http://127.0.0.1:8080/a.mp4', allow_local=True), 'http://127.0.0.1:8080/a.mp4')

    def test_bookmarklet_object(self):
        w = wv.validate_web(web())
        self.assertEqual(w['format'], 'STEREO_180_LR')
        self.assertEqual(w['sources'], [{'src': 'https://cdn.example.com/a.mp4', 'type': '', 'quality': '', 'format': None, 'via': 'dl8'}])
        # A page that is not http(s), or an odd start, is simply dropped; unknown keys or a missing marker are refused.
        self.assertIsNone(wv.validate_web(web(page='file:///home/x.html'))['page'])
        self.assertEqual(wv.validate_web(web(start=-3))['start'], 0)
        self.assertEqual(wv.validate_web(web(start=float('nan')))['start'], 0)
        self.assertIsNone(wv.validate_web(web(ua='Mozilla\r\nX-Evil: 1'))['ua'])  # never becomes a header line
        for bad in (web(extra=1), {**web(), 'viture_xr_video': 2}, web(sources=[]), web(sources='x'), web(format='STEREO 180'),
                    web(fov=1000), web({'src': 'https://cdn.example.com/a.mp4', 'cookie': 'x'}),
                    web({'src': 'https://cdn.example.com/a.mp4', 'via': 'elsewhere'}), web({'src': 'file:///etc/passwd'}), [], None):
            with self.subTest(bad=bad), self.assertRaises(video.VideoRejected): wv.validate_web(bad)
        # Refused sources are skipped while one is left.
        self.assertEqual(len(wv.validate_web(web({'src': 'blob:https://x/1'}, {'src': 'https://cdn.example.com/b.mp4'}))['sources']), 1)

    def test_control_socket_shape(self):
        def frame(payload):
            return (json.dumps({'schema': 1, 'request_id': 9, 'run_id': 'run', 'session': 's', 'op': 'video_open', 'payload': payload}) + '\n').encode()
        self.assertEqual(decode_control(frame({'url': 'https://cdn.example.com/a_360.mp4'}))['payload'], {'url': 'https://cdn.example.com/a_360.mp4'})
        self.assertIn('web', decode_control(frame({'web': web()}))['payload'])
        chosen = {'projection': '180', 'stereo': 'tb', 'fov': 180}
        self.assertEqual(decode_control(frame({'web': web(), 'format': chosen}))['payload']['format'], chosen)
        for bad in ({'url': 'https://a.example/x.mp4', 'path': '/x.mp4'}, {'url': 'ftp://a/x'}, {'web': {'sources': []}},
                    {'url': 'https://a.example/x.mp4', 'web': web()}):
            with self.subTest(bad=bad), self.assertRaises(ValueError): decode_control(frame(bad))
        # The shape check accepts local hosts (tests); the run refuses them when it opens (VideoControl.allow_local).
        self.assertEqual(decode_control(frame({'url': 'http://127.0.0.1/a.mp4'}))['payload'], {'url': 'http://127.0.0.1/a.mp4'})


class FormatAndSourceTests(unittest.TestCase):
    def test_delightxr_formats(self):
        self.assertEqual(wv.dl8_format('STEREO_180_LR'), {'projection': '180', 'stereo': 'sbs', 'fov': 180})
        self.assertEqual(wv.dl8_format('stereo_180_tb_spherical'), {'projection': '180', 'stereo': 'tb', 'fov': 180})
        self.assertEqual(wv.dl8_format('MONO_360'), {'projection': '360', 'stereo': 'mono', 'fov': 180})
        self.assertEqual(wv.dl8_format('STEREO_360_LR'), {'projection': '360', 'stereo': 'sbs', 'fov': 180})
        self.assertEqual(wv.dl8_format('STEREO_360_TB'), {'projection': '360', 'stereo': 'tb', 'fov': 180})
        self.assertEqual(wv.dl8_format('MONO_180'), {'projection': '180', 'stereo': 'mono', 'fov': 180})
        self.assertEqual(wv.dl8_format('STEREO_FISHEYE_LR', 190), {'projection': 'fisheye', 'stereo': 'sbs', 'fov': 190})
        self.assertEqual(wv.dl8_format('STEREO_FISHEYE_LR', 240)['fov'], 220)
        self.assertIsNone(wv.dl8_format(None)); self.assertIsNone(wv.dl8_format('SOMETHING_NEW'))
        for flat in wv.DL8_FLAT:
            with self.subTest(flat=flat), self.assertRaises(video.VideoRejected): wv.dl8_format(flat)

    def test_quality_labels(self):
        for label, h in (('8K', 4320), ('5.7k', 3078), ('4k', 2160), ('2160p', 2160), ('1080p', 1080), ('UHD', 2160), ('', 0), ('best', 0)):
            with self.subTest(label=label): self.assertEqual(wv.quality_height(label), h)

    def test_choose_source(self):
        w = wv.validate_web(web(
            {'src': 'https://cdn.example.com/beach_4k.mp4', 'type': 'video/mp4', 'quality': '4K', 'via': 'dl8'},
            {'src': 'https://cdn.example.com/beach_8k.mp4', 'type': 'video/mp4', 'quality': '8K', 'via': 'dl8'},
            {'src': 'https://cdn.example.com/beach/master.m3u8', 'type': 'application/x-mpegurl', 'via': 'dl8'},
            {'src': 'https://cdn.example.com/other.mp4', 'quality': '16K', 'via': 'resource'}))
        self.assertEqual(wv.choose_source(w)['src'], 'https://cdn.example.com/beach_8k.mp4')  # the player's own, highest
        w = wv.validate_web(web({'src': 'https://cdn.example.com/x.mpd', 'via': 'dl8'}, {'src': 'https://cdn.example.com/x.m3u8', 'via': 'dl8'},
                                {'src': 'https://cdn.example.com/x.mp4', 'via': 'dl8'}))
        self.assertEqual(wv.choose_source(w)['src'], 'https://cdn.example.com/x.mp4')  # same quality: progressive seeks best
        self.assertEqual(wv.source_kind({'src': 'https://a/x', 'type': 'application/vnd.apple.mpegurl'}), 'hls')
        self.assertEqual(wv.source_kind({'src': 'https://a/x.mpd?t=1', 'type': ''}), 'dash')

    def test_clipboard(self):
        payload = wv.parse_clipboard(json.dumps(web()))
        self.assertEqual(set(payload), {'web'})
        self.assertEqual(wv.parse_clipboard('  https://cdn.example.com/a_360.mp4\n'), {'url': 'https://cdn.example.com/a_360.mp4'})
        for bad in ('', '   ', 'hello world', '{"x":1}', '{not json', 'https://a.example/x.mp4 https://b', 'x' * 20000):
            with self.subTest(bad=bad[:20]), self.assertRaises(video.VideoRejected): wv.parse_clipboard(bad)
        # The bind helper's stdlib copy agrees on what it forwards.
        self.assertEqual(xr_control.clipboard_payload(json.dumps(web()))[0], payload)
        self.assertEqual(xr_control.clipboard_payload('https://cdn.example.com/a_360.mp4')[0], {'url': 'https://cdn.example.com/a_360.mp4'})
        for bad in ('', 'hello', '{"x":1}', 'x' * 20000):
            with self.subTest(bad=bad[:20]):
                payload, why = xr_control.clipboard_payload(bad)
                self.assertIsNone(payload); self.assertTrue(why)
        self.assertEqual(xr_control.WEB_MARKER, wv.MARKER)
        self.assertEqual(xr_control.MAX_CLIPBOARD, wv.MAX_CLIPBOARD)

    def test_failure_reasons_never_show_the_link(self):
        url = 'https://cdn.example.com/a.mp4?token=SECRET'
        for stderr, part in ((f'[https @ 0x1] HTTP error 403 Forbidden\n{url}: Server returned 403 Forbidden', '403'),
                             ('Server returned 404 Not Found', '404'), ('Protocol \'file\' not on whitelist', 'other than web'),
                             ('Failed to resolve hostname cdn.example.com: Name or service not known', 'offline'),
                             ('[mov] stream 0: cenc encrypted', 'DRM'), ('Connection timed out', 'in time'), ('weird', 'could not be opened')):
            with self.subTest(part=part):
                why = wv.explain_failure(stderr.encode(), 1)
                self.assertIn(part, why); self.assertNotIn('SECRET', why); self.assertNotIn('cdn.example.com', why)

    def test_relay_argv(self):
        argv = wv.relay_argv('https://cdn.example.com/a.m3u8', {'Referer': 'https://vr.example.com/w'}, 'UA/1', 12.5, ffmpeg='/usr/bin/ffmpeg')
        self.assertEqual(argv[argv.index('-protocol_whitelist') + 1], 'https,http,tls,tcp,crypto')  # never file:
        self.assertEqual(argv[argv.index('-headers') + 1], 'Referer: https://vr.example.com/w\r\n')
        self.assertEqual(argv[argv.index('-ss') + 1], '12.500')
        self.assertLess(argv.index('-ss'), argv.index('-i'))  # input seek
        self.assertEqual(argv[argv.index('-c') + 1], 'copy')  # no re-encoding
        self.assertEqual(argv[argv.index('-f') + 1], 'nut')
        self.assertEqual(argv[-1], 'pipe:1')
        self.assertNotIn('-ss', wv.relay_argv('https://a.example/x.mp4', ffmpeg='/usr/bin/ffmpeg'))


class RangeHandler(SimpleHTTPRequestHandler):
    """A static server with byte ranges (CDNs serve MP4 that way; http.server does not)."""
    hits = None

    def log_message(self, *a): pass

    def do_GET(self):
        if type(self).hits is not None: type(self).hits.append(self.path)
        if self.path.startswith('/forbidden'):
            self.send_error(403); return
        path = self.translate_path(self.path.split('?', 1)[0])
        if not os.path.isfile(path): self.send_error(404); return
        size = os.path.getsize(path)
        start, end = 0, size - 1
        rng = self.headers.get('Range')
        if rng and rng.startswith('bytes='):
            a, _, b = rng[6:].partition('-')
            start = int(a) if a else size - int(b)
            end = int(b) if a and b else size - 1
        end = min(end, size - 1)
        self.send_response(206 if rng else 200)
        self.send_header('Content-Type', self.guess_type(path)); self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Content-Length', str(end - start + 1))
        if rng: self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
        self.end_headers()
        with open(path, 'rb') as f:
            f.seek(start); remaining = end - start + 1
            try:
                while remaining > 0:
                    chunk = f.read(min(65536, remaining))
                    if not chunk: break
                    self.wfile.write(chunk); remaining -= len(chunk)
            except (BrokenPipeError, ConnectionResetError): pass


def drain(fd, limit=64 << 20, timeout=30):
    """Read the relay's pipe to its end (what the renderer would get)."""
    data, deadline = bytearray(), time.monotonic() + timeout
    os.set_blocking(fd, True)
    try:
        while time.monotonic() < deadline and len(data) < limit:
            chunk = os.read(fd, 1 << 20)
            if not chunk: break
            data += chunk
    finally:
        os.close(fd)
    return bytes(data)


def probe_bytes(data, tmp):
    p = Path(tmp) / 'relayed.nut'; p.write_bytes(data)
    r = subprocess.run([FFPROBE, '-v', 'error', '-print_format', 'json', '-show_streams', '-show_format', '-count_packets', str(p)],
                       capture_output=True, check=True)
    return json.loads(r.stdout)


@unittest.skipUnless(FFMPEG and FFPROBE, 'ffmpeg/ffprobe not installed')
class RelayTests(unittest.TestCase):
    """The real relay: ffprobe and ffmpeg over HTTP (127.0.0.1) into a pipe, as the run does it."""
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = Path(cls.tmp.name)
        src = 'testsrc2=size=1024x512:rate=30:duration=6'
        subprocess.run([FFMPEG, '-v', 'error', '-y', '-f', 'lavfi', '-i', src, '-f', 'lavfi', '-i', 'sine=f=440:duration=6',
                        '-c:v', 'libx264', '-preset', 'ultrafast', '-g', '30', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest',
                        '-movflags', '+faststart', str(d / 'beach_180_LR.mp4')], check=True)
        (d / 'hls').mkdir()
        subprocess.run([FFMPEG, '-v', 'error', '-y', '-i', str(d / 'beach_180_LR.mp4'), '-c', 'copy', '-f', 'hls', '-hls_time', '2',
                        '-hls_playlist_type', 'vod', str(d / 'hls' / 'index.m3u8')], check=True)
        RangeHandler.hits = []
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), partial(RangeHandler, directory=str(d)))
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True); cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.server.server_address[1]}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.tmp.cleanup()

    def test_probe_over_http(self):
        info = wv.probe_url(self.base + '/beach_180_LR.mp4', {'Referer': 'https://vr.example.com/'}, 'UA/1')
        self.assertEqual((info['width'], info['height'], info['codec']), (1024, 512, 'h264'))
        self.assertAlmostEqual(info['duration'], 6, delta=0.2)
        hls = wv.probe_url(self.base + '/hls/index.m3u8')
        self.assertEqual((hls['width'], hls['height']), (1024, 512))
        with self.assertRaises(video.VideoRejected) as cm: wv.probe_url(self.base + '/forbidden.mp4?token=SECRET')
        self.assertIn('403', str(cm.exception)); self.assertNotIn('SECRET', str(cm.exception))
        with self.assertRaises(video.VideoRejected) as cm: wv.probe_url(self.base + '/missing.mp4')
        self.assertIn('404', str(cm.exception))

    def relay(self, url, start=0.0):
        r = wv.StreamRelay(wv.relay_argv(url, {'Referer': 'https://vr.example.com/'}, 'UA/1', start))
        fd = r.start()
        self.addCleanup(r.stop)
        return r, fd

    def test_progressive_and_hls_relay_copy_every_frame(self):
        for url in ('/beach_180_LR.mp4', '/hls/index.m3u8'):
            with self.subTest(url=url):
                r, fd = self.relay(self.base + url)
                data = drain(fd)
                r.stop()
                self.assertEqual(r.returncode, 0); self.assertIsNone(r.failure())
                p = probe_bytes(data, self.tmp.name)
                self.assertEqual(p['format']['format_name'], 'nut')
                v = next(s for s in p['streams'] if s['codec_type'] == 'video')
                self.assertEqual((v['codec_name'], v['width'], v['height']), ('h264', 1024, 512))  # copied, not re-encoded
                self.assertEqual(int(v['nb_read_packets']), 180)  # 6 s at 30 fps: every frame
                self.assertTrue(any(s['codec_type'] == 'audio' for s in p['streams']))

    def test_relay_from_a_position(self):
        # MP4: from the keyframe before 4 s (about the last 2 s). HLS: from the start of the segment holding 4 s.
        for url, most in (('/beach_180_LR.mp4', 100), ('/hls/index.m3u8', 150)):
            with self.subTest(url=url):
                r, fd = self.relay(self.base + url, start=4.0)
                p = probe_bytes(drain(fd), self.tmp.name)
                v = next(s for s in p['streams'] if s['codec_type'] == 'video')
                self.assertGreater(int(v['nb_read_packets']), 30); self.assertLess(int(v['nb_read_packets']), most)

    def test_a_paused_renderer_does_not_block_stop(self):
        r, fd = self.relay(self.base + '/beach_180_LR.mp4')
        time.sleep(0.5)  # nobody reads: ffmpeg and the writer wait on the full pipe
        t0 = time.monotonic(); r.stop(); os.close(fd)
        self.assertLess(time.monotonic() - t0, 3.5)
        self.assertIsNone(r.failure())  # stopped on purpose

    def test_the_run_end_to_end(self):
        """VideoControl with the real probe and relay: the bookmarklet's object in, a NUT pipe for the renderer out."""
        clock = Clock()
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        ctl = view.ViewControl(view.ViewState(NAMES), 'run1', 's1', lambda name: None, LayoutStore(Path(tmp.name) / 'l.json'), None,
                               clock=clock, menu_binds=lambda on: None)
        v = video.VideoControl(ctl, probe=lambda fd: None, threads=False, clock=clock, allow_local=True)
        self.addCleanup(v.close)
        page = web({'src': self.base + '/hls/index.m3u8', 'type': 'application/x-mpegurl'}, {'src': self.base + '/beach_180_LR.mp4', 'quality': '1080p'},
                   format='STEREO_180_LR', start=2.5)
        v.on_message({'op': 'video_open', 'payload': {'web': page}})
        out = v.poll(clock())
        self.assertIsInstance(out, video.FdFrame, v.snapshot())
        [f] = frames(out)
        self.assertEqual(f['payload'], {'action': 'open', 'projection': '180', 'stereo': 'sbs', 'fov': 180, 'title': 'Sunset beach'})
        fd = out.fds.pop()  # the renderer's end
        p = probe_bytes(drain(fd), self.tmp.name)
        vs = next(s for s in p['streams'] if s['codec_type'] == 'video')
        self.assertEqual((p['format']['format_name'], vs['width']), ('nut', 1024))
        self.assertEqual(int(vs['nb_read_packets']), 180)  # from the start: 2.5 s leaves under 5 s of this 6 s clip
        self.assertTrue(any('/beach_180_LR.mp4' in h for h in RangeHandler.hits))  # the labelled progressive file was chosen
        self.assertEqual(v.snapshot()['stream']['duration'], 6.0)
        v.on_message({'op': 'video_exit', 'payload': {}})
        self.assertIsNone(v.stream)

    def test_a_refused_stream_is_a_failure_with_a_reason(self):
        r, fd = self.relay(self.base + '/forbidden.mp4')
        self.assertEqual(drain(fd), b'')
        deadline = time.monotonic() + 5
        while r.returncode is None and time.monotonic() < deadline: time.sleep(0.05)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('403', r.failure())


class FakeSupervisor:
    def __init__(self): self.status, self.stopping_at = {}, None


class FakeRelay:
    def __init__(self, argv, log):
        self.argv, self.log, self.stopped, self.returncode, self.why = argv, log, False, None, None
        log.append(self)

    def start(self):
        r, w = os.pipe(); os.close(w)
        return r

    def stop(self): self.stopped = True
    def failure(self): return self.why


class WebControlTests(unittest.TestCase):
    """VideoControl with web streams: probe and relay are stand-ins; frames and descriptors are real."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.clock = Clock()
        self.view = view.ViewControl(view.ViewState(NAMES), 'run1', 's1', lambda name: None, LayoutStore(Path(self.tmp.name) / 'l.json'),
                                     None, clock=self.clock, menu_binds=lambda on: None)
        self.relays, self.probed, self.told = [], [], []
        self.info = {'width': 8192, 'height': 4096, 'codec': 'hevc', 'fps': 30.0, 'duration': 600.0, 'projection': None,
                     'coverage': None, 'stereo': None}
        def probe(url, headers, ua):
            self.probed.append((url, headers, ua))
            if 'forbidden' in url: raise video.VideoRejected('the site refused the stream (HTTP 403)')
            return dict(self.info)
        self.v = video.VideoControl(self.view, probe=lambda fd: None, threads=False, clock=self.clock, web_probe=probe,
                                    relay_factory=lambda argv: FakeRelay(argv, self.relays), notify=self.told.append)
        self.sup = FakeSupervisor(); self.v.attach_supervisor(self.sup)

    def op(self, op, payload=None):
        return self.v.on_message({'op': op, 'payload': payload or {}})

    def poll(self):
        out = self.v.poll(self.clock())
        if isinstance(out, video.FdFrame): self.addCleanup(out.close_fds)
        return out

    def test_opens_with_the_players_format_and_resumes_where_the_page_was(self):
        page = web({'src': 'https://cdn.example.com/beach_4k.mp4', 'quality': '4K'}, {'src': 'https://cdn.example.com/beach_8k.mp4', 'quality': '8K'},
                   start=42.3)
        self.assertIsNone(self.op('video_open', {'web': page}))
        out = self.poll()
        self.assertIsInstance(out, video.FdFrame); self.assertEqual(len(out.fds), 1)
        [f] = frames(out)
        self.assertEqual(f['payload'], {'action': 'open', 'projection': '180', 'stereo': 'sbs', 'fov': 180, 'title': 'Sunset beach'})
        self.assertEqual(self.probed, [('https://cdn.example.com/beach_8k.mp4', {'Referer': 'https://vr.example.com/watch/42'}, 'Mozilla/5.0 test')])
        argv = self.relays[0].argv
        self.assertEqual(argv[argv.index('-ss') + 1], '42.300')
        snap = self.v.snapshot()
        self.assertEqual((snap['origin'], snap['source'], snap['label']), ('web', 'player', '180° side-by-side 3D'))
        self.assertEqual(snap['stream']['site'], 'cdn.example.com')
        self.assertEqual(snap['stream']['position'], 42.3)
        self.assertNotIn('beach_8k', json.dumps(snap))  # the link (maybe signed) is not shown or logged

    def test_a_bare_link_is_detected_from_its_name_and_shape(self):
        self.op('video_open', {'url': 'https://cdn.example.com/clips/Alps_360_TB.mp4'})
        [f] = frames(self.poll())
        self.assertEqual((f['payload']['projection'], f['payload']['stereo'], f['payload']['title']), ('360', 'tb', 'Alps_360_TB.mp4'))
        self.assertEqual(self.v.snapshot()['source'], 'filename')

    def test_refusals_are_told_and_nothing_reaches_the_renderer(self):
        for payload, part in (({'url': 'http://127.0.0.1/a.mp4'}, 'local'), ({'url': 'https://cdn.example.com/forbidden.mp4'}, '403'),
                              ({'web': web(format='MONO_FLAT')}, 'flat')):
            with self.subTest(part=part):
                self.told.clear()
                self.op('video_open', payload)
                self.assertIsNone(self.poll())
                self.assertIn(part, self.v.snapshot()['error'])
                self.assertFalse(self.v.active)
                for t in threading.enumerate():
                    if t is not threading.current_thread() and t.daemon: t.join(0.5)
                self.assertEqual(len(self.told), 1); self.assertIn(part, self.told[0])

    def test_seek_keys_settle_into_one_relay_restart_from_the_estimated_position(self):
        self.op('video_open', {'web': web()}); self.poll()
        self.sup.status = {'video': 'playing'}
        for _ in range(10):  # 10 s of playing at 1 s polls
            self.clock.now += 1; self.poll()
        self.assertAlmostEqual(self.v.stream.position(), 10, delta=0.01)
        out = self.op('video_seek_fwd')  # three quick presses: +60 s +10 s +10 s
        self.assertNotIn('seek', [f['payload'].get('action') for f in frames(out)])  # never a renderer seek on a pipe
        self.op('video_seek_fwd_big'); self.op('video_seek_fwd')
        self.assertEqual(len(self.relays), 1)
        self.clock.now += 0.1; self.poll()
        self.assertEqual(len(self.relays), 1)  # still settling
        self.clock.now += wv.SEEK_SETTLE_S; out = self.poll()
        self.assertIsInstance(out, video.FdFrame)
        self.assertEqual([f['payload']['action'] for f in frames(out)], ['open'])
        self.assertEqual(len(self.relays), 2); self.assertTrue(self.relays[0].stopped)
        self.assertEqual(self.relays[1].argv[self.relays[1].argv.index('-ss') + 1], '90.000')  # 10 + 10 + 60 + 10
        self.sup.status = {'video': 'paused'}
        self.clock.now += 5; self.poll()
        self.assertAlmostEqual(self.v.stream.position(), 90.0, delta=0.01)  # paused time does not count
        # Seeking back past the start or forward past the end stays inside the video.
        for _ in range(3): self.op('video_seek_back_big')
        self.clock.now += 1; self.poll()
        self.assertNotIn('-ss', self.relays[-1].argv)
        for _ in range(12): self.op('video_seek_fwd_big')
        self.clock.now += 1; self.poll()
        self.assertEqual(self.relays[-1].argv[self.relays[-1].argv.index('-ss') + 1], '597.000')

    def test_a_dropped_link_resumes_then_gives_up(self):
        self.op('video_open', {'web': web()}); self.poll()
        self.sup.status = {'video': 'playing'}
        for _ in range(30): self.clock.now += 1; self.poll()
        self.clock.now += 30; self.poll()  # a stalled loop: counted as at most 1 s
        for attempt in range(3):
            self.relays[-1].why = 'the site did not answer in time'
            self.sup.status = {'video': 'ended'}
            self.clock.now += 1; out = self.poll()
            if attempt < 2:
                self.assertEqual([f['payload']['action'] for f in frames(out)], ['open'])
                self.assertEqual(self.relays[-1].argv[self.relays[-1].argv.index('-ss') + 1], '31.000')
                self.sup.status = {'video': 'loading'}
            else:
                self.assertEqual([f['payload']['action'] for f in frames(out)], ['close'])
                self.assertFalse(self.v.active)
                self.assertEqual(self.v.snapshot()['error'], 'the site did not answer in time')
        self.assertTrue(all(r.stopped for r in self.relays))

    def test_a_normal_end_stays_and_left_seeks_back_into_it(self):
        self.op('video_open', {'web': web()}); self.poll()
        self.sup.status = {'video': 'ended'}
        self.clock.now += 1; self.assertNotIsInstance(self.poll(), video.FdFrame)
        self.assertTrue(self.v.active)
        self.op('video_seek_back'); self.clock.now += 1; self.poll()
        self.assertEqual(len(self.relays), 2)

    def test_live_streams_do_not_seek(self):
        self.info['duration'] = None
        self.op('video_open', {'web': web()}); self.poll()
        self.assertIn('live stream: no seeking', self.v.snapshot()['warnings'])
        self.op('video_seek_fwd'); self.clock.now += 1; self.poll()
        self.assertEqual(len(self.relays), 1)
        self.assertIn('live', self.v.snapshot()['error'])

    def test_exit_close_and_replacing_stop_the_relay(self):
        self.op('video_open', {'web': web()}); self.poll()
        self.op('video_open', {'url': 'https://cdn.example.com/b_360.mp4'}); self.poll()
        self.assertTrue(self.relays[0].stopped); self.assertFalse(self.relays[1].stopped)
        self.assertEqual([f['payload']['action'] for f in frames(self.op('video_exit'))], ['close'])
        self.assertTrue(self.relays[1].stopped)
        self.assertIsNone(self.v.snapshot()['stream'])
        self.op('video_open', {'web': web()}); self.poll()
        self.v.close()
        self.assertTrue(self.relays[2].stopped)

    def test_a_local_file_still_plays_as_before(self):
        clip = Path(self.tmp.name) / 'a_360.mp4'; clip.write_bytes(b'v' * 10)
        self.op('video_open', {'path': str(clip)}); out = self.poll()
        self.assertEqual(frames(out)[0]['payload']['title'], 'a_360.mp4')
        self.assertEqual(self.v.snapshot()['origin'], 'file'); self.assertIsNone(self.v.snapshot()['stream'])
        self.sup.status = {'video': 'playing'}
        self.assertEqual([f['payload'] for f in frames(self.op('video_seek_fwd'))][-1], {'action': 'seek', 'seconds': 10})


class WebVideoGatingTests(unittest.TestCase):
    """A DelightXR / web stream is the same VR video mode: the same keys are ignored and the same overlays are off."""
    setUp, op, poll = WebControlTests.setUp, WebControlTests.op, WebControlTests.poll

    def test_web_playback_uses_the_video_gating(self):
        from tools.native_spike import resize
        requests = []
        class Sender:
            def request(self, op, selected): requests.append(op)
        self.view.sender = Sender()
        r = self.view.resize = resize.CornerResize(self.view, clock=self.clock); self.addCleanup(r.close)
        self.view.on_message({'op': 'help'}); self.view.on_message({'op': 'menu_toggle'})
        self.op('video_open', {'url': 'https://cdn.example.com/clips/Alps_360_TB.mp4'})
        self.poll()
        self.assertTrue(self.v.active); self.assertEqual(self.v.snapshot()['origin'], 'web')
        self.assertTrue(self.view.video_active)  # corner resize and drag bars off
        self.assertFalse(self.view.help_visible or self.view.state.menu_open)
        self.assertFalse(r.overview())
        for op in ('menu_toggle', 'menu_mouse_press', 'resize_press', 'send_glasses', 'send_glasses_1', 'zoom_in'):
            with self.subTest(op=op): self.assertIsNone(self.op(op))
        self.assertEqual(requests, []); self.assertFalse(self.view.state.menu_open)
        self.op('video_exit')
        self.assertFalse(self.view.video_active); self.assertTrue(r.overview())


class BindHelperTests(unittest.TestCase):
    def test_ctrl_alt_u(self):
        self.assertIn(('CTRL + ALT + U', 'video_paste'), view.BINDS)
        self.assertIn(['Ctrl+Alt+O / U', 'VR video: file / copied web link'], view.help_rows())

    def test_paste_sends_the_clipboard_as_video_open(self):
        sent, told = [], []
        orig = xr_control.send
        xr_control.send = lambda *a: sent.append(a)
        try:
            argv = ['x', '/run/c.sock', 'run1', 's1', 'video_paste']
            self.assertEqual(xr_control.main(argv, clipboard=lambda: json.dumps(web()), tell=told.append), 0)
            self.assertEqual(sent[-1][3:], ('video_open', {'web': web()}))
            self.assertEqual(xr_control.main(argv, clipboard=lambda: 'shopping list', tell=told.append), 0)
            self.assertEqual(len(sent), 1); self.assertIn('Play in VITURE', told[-1])
            self.assertEqual(xr_control.main(argv, clipboard=lambda: None, tell=told.append), 0)
            self.assertEqual(len(sent), 1)
        finally:
            xr_control.send = orig


class BookmarkletTests(unittest.TestCase):
    def test_build(self):
        url = build.bookmark_url()
        self.assertTrue(url.startswith('javascript:'))
        for ch in ' #%\n':
            self.assertNotIn(ch, url.replace('%', '') if ch == '%' else url)
        self.assertNotIn('/*', build.code())
        self.assertIn('viture_xr_video', build.code())

    @unittest.skipUnless(CHROMIUM, 'chromium not installed')
    def test_in_headless_chromium(self):
        """The built bookmark's code, run on test pages in headless Chromium: a temporary profile, every host
        unresolvable (no network), the clipboard replaced by a test sink."""
        from urllib.parse import unquote
        code = unquote(build.bookmark_url()[len('javascript:'):])
        pages = {
            'dl8': '''<dl8-video format="STEREO_180_LR" title="Sunset beach">
                        <source src="https://cdn.example.com/beach_4k.mp4" type="video/mp4" quality="4K">
                        <source src="/media/beach_8k.mp4" type="video/mp4" quality="8K">
                        <source src="https://cdn.example.com/beach/master.m3u8" type="application/x-mpegurl"></dl8-video>''',
            'custom': '''<meta name="dl8-custom-format" content="name:CANON_R5,base:STEREO_FISHEYE_LR,params:fov:190,magnification:1">
                         <dl8-video format="CANON_R5"><source src="https://cdn.example.com/rf52.mp4"></dl8-video>''',
            'plain': '<video src="https://cdn.example.com/plain_360.mp4" muted></video>',
            'none': '<p>nothing here</p>',
        }
        with tempfile.TemporaryDirectory() as tmp:
            for name, body in pages.items():
                html = ('<!doctype html><html><head><base href="https://vr.example.com/"><title>Page title</title><script>'
                        'window.__vitureXrTestSink = function (t) { window.__out = t; };</script></head><body>' + body +
                        '<pre id="out"></pre><script>' + code.replace('</', '<\\/') + '</script><script>'
                        'document.getElementById("out").textContent = window.__out || "NONE";</script></body></html>')
                (Path(tmp) / f'{name}.html').write_text(html)
            results = {}
            for name in pages:
                profile = Path(tmp) / f'profile-{name}'
                r = subprocess.run([CHROMIUM, '--headless=new', f'--user-data-dir={profile}', '--no-first-run',
                                    '--disable-background-networking', '--disable-component-update', '--disable-sync',
                                    '--host-resolver-rules=MAP * ~NOTFOUND', '--virtual-time-budget=3000', '--dump-dom',
                                    (Path(tmp) / f'{name}.html').as_uri()], capture_output=True, timeout=90, check=False)
                import re
                m = re.search(r'<pre id="out">(.*?)</pre>', r.stdout.decode('utf-8', 'replace'), re.S)
                if not m: self.skipTest('headless chromium produced no DOM: ' + r.stderr.decode('utf-8', 'replace')[-300:])
                import html as h
                results[name] = h.unescape(m.group(1))
        self.assertEqual(results['none'], 'NONE')
        dl8 = json.loads(results['dl8'])
        self.assertEqual(dl8['format'], 'STEREO_180_LR'); self.assertEqual(dl8['title'], 'Sunset beach')
        self.assertEqual([s['src'] for s in dl8['sources']], ['https://cdn.example.com/beach_4k.mp4', 'https://vr.example.com/media/beach_8k.mp4',
                                                              'https://cdn.example.com/beach/master.m3u8'])
        w = wv.validate_web(dl8)  # what the run accepts
        self.assertEqual(wv.choose_source(w)['src'], 'https://vr.example.com/media/beach_8k.mp4')
        custom = json.loads(results['custom'])
        self.assertEqual((custom['format'], custom['fov']), ('STEREO_FISHEYE_LR', 190))
        self.assertEqual(wv.dl8_format(custom['format'], custom['fov']), {'projection': 'fisheye', 'stereo': 'sbs', 'fov': 190})
        plain = json.loads(results['plain'])
        self.assertIsNone(plain['format']); self.assertEqual(plain['title'], 'Page title')
        self.assertEqual(plain['sources'][0]['src'], 'https://cdn.example.com/plain_360.mp4')
        self.assertEqual(wv.parse_clipboard(results['plain']), {'web': plain})


if __name__ == '__main__':
    unittest.main()
