"""Web video relay timing harness (docs/web-video.md, tools/native_spike/web_video.py). Not a test (unittest discovery
skips it): numbers depend on machine load. Run from the repository root:

    python3 tests/native_spike/bench_web_video.py [--json] [--only NAME ...]

Offline: the only network is a local HTTP server on 127.0.0.1 serving clips generated here (never downloaded).

Cases:
  pump      the relay's read-ahead pump alone (`cat` of 256 MiB): throughput, and supervisor CPU per GiB
  paced     a renderer reading 100 Mbit/s (8K VR) in 64 KiB reads for 5 s: supervisor CPU % and pipe reads per second
  stop      StreamRelay.stop() with a paused renderer and a full read-ahead (the seek path calls it on the supervisor loop),
            for `cat` and for the real ffmpeg relay downloading from a 200 Mbit/s server
  seek      WebStream.start(at) while playing: how long the supervisor loop is blocked, and time to the new first byte
  open      prepare_target (ffprobe over HTTP) + relay start: time to the first byte for the renderer (MP4 and HLS)
"""
import argparse
import json
import os
from pathlib import Path
import select
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from tools.native_spike import web_video as wv  # noqa: E402
from test_web_video import RangeHandler  # noqa: E402
from functools import partial  # noqa: E402
from http.server import ThreadingHTTPServer  # noqa: E402

FFMPEG = shutil.which('ffmpeg')


def cpu():
    return time.process_time()  # this process, all threads: the supervisor's share


def drain(fd, *, rate=None, chunk=1 << 20, limit=None, timeout=60):
    """Read the pipe like the renderer (mpv) would: as fast as possible, or paced at `rate` bytes/s."""
    os.set_blocking(fd, True)
    total, reads, t0 = 0, 0, time.monotonic()
    first = None
    try:
        while time.monotonic() - t0 < timeout and (limit is None or total < limit):
            data = os.read(fd, chunk)
            if not data: break
            if first is None: first = time.monotonic() - t0
            total += len(data); reads += 1
            if rate:
                due = t0 + total / rate
                delay = due - time.monotonic()
                if delay > 0: time.sleep(delay)
    finally:
        os.close(fd)
    return total, reads, time.monotonic() - t0, first


def bench_pump(tmp):
    src = Path(tmp) / 'random.bin'
    with open(src, 'wb') as f:
        for _ in range(256): f.write(os.urandom(1 << 20))
    out = []
    for _ in range(3):
        r = wv.StreamRelay(['/usr/bin/cat', str(src)])
        c0 = cpu(); fd = r.start()
        total, reads, dt, _ = drain(fd)
        c1 = cpu(); r.stop()
        out.append({'mib_s': total / dt / (1 << 20), 'cpu_s_per_gib': (c1 - c0) / (total / (1 << 30)), 'reads': reads})
    best = max(out, key=lambda o: o['mib_s'])
    return {'case': 'pump', 'mib_s': round(best['mib_s']), 'cpu_s_per_gib': round(min(o['cpu_s_per_gib'] for o in out), 2),
            'renderer_reads_per_mib': round(best['reads'] / 256, 1)}


def bench_paced(tmp):
    src = Path(tmp) / 'random.bin'
    rate = 100e6 / 8
    r = wv.StreamRelay(['/usr/bin/cat', str(src)])
    fd = r.start()
    time.sleep(0.5)  # let the read-ahead fill as the network would
    c0, t0 = cpu(), time.monotonic()
    total, reads, dt, _ = drain(fd, rate=rate, chunk=64 << 10, limit=int(rate * 5))
    c1 = cpu()
    r.stop()
    return {'case': 'paced', 'mbit_s': round(total * 8 / dt / 1e6, 1), 'supervisor_cpu_pct': round(100 * (c1 - c0) / dt, 2),
            'reads_per_s': round(reads / dt), 'bytes_per_read': round(total / max(1, reads))}


def bench_stop(tmp):
    src = Path(tmp) / 'random.bin'
    out = []
    for _ in range(3):
        r = wv.StreamRelay(['/usr/bin/cat', str(src)])
        fd = r.start()
        time.sleep(1.0)  # renderer paused: nobody reads; the read-ahead fills (128 MiB) and cat blocks on its pipe
        t0 = time.monotonic(); r.stop(); dt = time.monotonic() - t0
        os.close(fd)
        out.append(dt * 1000)
    return {'case': 'stop', 'ms': [round(v) for v in out], 'max_ms': round(max(out))}


class Throttled:
    """A socket writer limited to `rate` bytes/s: a CDN link (ffmpeg is still downloading when a seek arrives)."""
    def __init__(self, raw, rate):
        self.raw, self.rate, self.sent, self.t0 = raw, rate, 0, time.monotonic()

    def write(self, data):
        n = self.raw.write(data); self.sent += len(data)
        delay = self.t0 + self.sent / self.rate - time.monotonic()
        if delay > 0: time.sleep(delay)
        return n

    def __getattr__(self, name): return getattr(self.raw, name)


class ThrottledHandler(RangeHandler):
    rate = 25e6  # 200 Mbit/s

    def setup(self):
        super().setup()
        self.wfile = Throttled(self.wfile, self.rate)


class Server:
    def __init__(self, directory):
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), partial(ThrottledHandler, directory=str(directory)))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f'http://127.0.0.1:{self.server.server_address[1]}'

    def close(self):
        self.server.shutdown(); self.server.server_close()


def make_clips(tmp):
    d = Path(tmp)
    clip = d / 'vr_180_LR.mp4'
    # 4K-wide picture at a high bitrate so the read-ahead takes a while to fill, as with a real 8K stream.
    subprocess.run([FFMPEG, '-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc2=size=3840x1920:rate=30:duration=20', '-f', 'lavfi', '-i',
                    'sine=f=440:duration=20', '-c:v', 'libx264', '-preset', 'ultrafast', '-b:v', '40M', '-g', '30', '-pix_fmt', 'yuv420p',
                    '-c:a', 'aac', '-shortest', '-movflags', '+faststart', str(clip)], check=True)
    long = d / 'vr_180_LR_long.mp4'  # 2 minutes, ~600 MB: still downloading at any stop or seek
    subprocess.run([FFMPEG, '-v', 'error', '-y', '-stream_loop', '5', '-i', str(clip), '-c', 'copy', '-movflags', '+faststart', str(long)], check=True)
    (d / 'hls').mkdir(exist_ok=True)
    subprocess.run([FFMPEG, '-v', 'error', '-y', '-i', str(long), '-c', 'copy', '-f', 'hls', '-hls_time', '2', '-hls_playlist_type', 'vod',
                    str(d / 'hls' / 'index.m3u8')], check=True)
    return long


def bench_stop_ffmpeg(server):
    """stop() of the real ffmpeg relay while it downloads: renderer paused (read-ahead filling), and reading."""
    out = {'case': 'stop_ffmpeg'}
    for name, wait, reading in (('paused', 1.5, False), ('paused_full', 7.0, False), ('reading', 1.5, True)):
        runs = []
        for _ in range(2):
            r = wv.StreamRelay(wv.relay_argv(server.base + '/vr_180_LR_long.mp4'))
            fd = r.start()
            t = threading.Thread(target=drain, args=(fd,), kwargs={'rate': 12.5e6, 'chunk': 64 << 10, 'timeout': wait + 5}, daemon=True) if reading else None
            if t: t.start()
            time.sleep(wait)
            t0 = time.monotonic(); r.stop(); runs.append((time.monotonic() - t0) * 1000)
            if t: t.join(10)
            else: os.close(fd)
        out[name] = [round(v) for v in runs]
    return out


def bench_seek(server):
    url = server.base + '/vr_180_LR_long.mp4'
    info = wv.probe_url(url)
    target = {'url': url, 'audio': None, 'headers': {}, 'ua': None, 'kind': 'progressive', 'site': '127.0.0.1', 'via': 'direct',
              'title': 't', 'hint': 't'}
    blocked, first = [], []
    stream = wv.WebStream(target, info)
    fd = stream.start(0.0)
    time.sleep(1.0)  # playing: the read-ahead is ahead of the (paused-like) renderer
    for at in (40.0, 13.0, 72.0, 25.0, 100.0):
        os.close(fd)  # the renderer closes the old player when the new open frame arrives
        t0 = time.monotonic()
        fd = stream.start(at)  # what VideoControl._restart_stream runs on the supervisor loop
        blocked.append((time.monotonic() - t0) * 1000)
        select.select([fd], [], [], 10)  # the new pipe's first byte (read nothing: the renderer would)
        first.append((time.monotonic() - t0) * 1000)
        time.sleep(0.6)
    os.close(fd)
    stream.stop()
    return {'case': 'seek', 'loop_blocked_ms': [round(v) for v in blocked], 'max_blocked_ms': round(max(blocked)),
            'first_byte_ms': [round(v) for v in first], 'median_first_byte_ms': round(statistics.median(first))}


def bench_open(server):
    out = {'case': 'open'}
    for name, path, typ in (('mp4', '/vr_180_LR_long.mp4', None), ('hls', '/hls/index.m3u8', 'application/x-mpegurl')):
        runs = []
        for _ in range(3):
            src = {'src': server.base + path, 'via': 'link', 'type': typ or '', 'quality': '', 'format': None}
            web = {'page': None, 'title': '', 'format': None, 'fov': None, 'start': 0.0, 'ua': None, 'sources': [src]}
            t0 = time.monotonic()
            target, info = wv.prepare_target(web, src, allow_local=True)
            t_probe = time.monotonic()
            stream = wv.WebStream(target, info)
            fd = stream.start(0.0)
            select.select([fd], [], [], 15)
            t_first = time.monotonic()
            os.close(fd); stream.stop()
            runs.append(((t_probe - t0) * 1000, (t_first - t0) * 1000))
        out[name] = {'probe_ms': round(statistics.median(r[0] for r in runs)), 'first_byte_ms': round(statistics.median(r[1] for r in runs))}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--only', nargs='*')
    args = ap.parse_args()
    want = lambda n: not args.only or n in args.only  # noqa: E731
    results = []
    with tempfile.TemporaryDirectory() as tmp:
        if want('pump') or want('paced') or want('stop'):
            results.append(bench_pump(tmp))
            if want('paced'): results.append(bench_paced(tmp))
            if want('stop'): results.append(bench_stop(tmp))
        if FFMPEG and (want('seek') or want('open')):
            make_clips(tmp)
            server = Server(tmp)
            try:
                if want('stop'): results.append(bench_stop_ffmpeg(server))
                if want('seek'): results.append(bench_seek(server))
                if want('open'): results.append(bench_open(server))
            finally:
                server.close()
    for r in results:
        print(json.dumps(r) if args.json else '  '.join(f'{k}={v}' for k, v in r.items()), flush=True)


if __name__ == '__main__':
    main()
