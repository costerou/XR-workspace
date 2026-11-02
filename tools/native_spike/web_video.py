"""VR video from a web page: DelightXR players and plain <video> streams (docs/delightxr.md).

DelightXR is a player that sites embed (`<dl8-video format="STEREO_180_LR">` with `<source>` children, also
`<dl8-live-video>`). The page names the projection and the stream URLs, so nothing has to be guessed or downloaded:

1. The "Play in VITURE" bookmarklet (tools/delightxr/bookmarklet.js) reads the player's format, sources, title and
   current time on the page Cody is watching, pauses it there, and copies a small JSON object to the clipboard. It has
   no access to cookies and sends nothing anywhere.
2. Ctrl+Alt+U in the glasses (or the panel's "Paste from browser") reads the clipboard once and sends it to the run as a
   `video_open` request (`{"web": {...}}`, or `{"url": "https://..."}` for a bare media link).
3. The run (this module) checks the request, picks the best source, probes it with ffprobe and starts a relay: an
   ffmpeg process that copies the stream (no re-encoding) into NUT on a pipe. The renderer gets the pipe's read end as
   the descriptor it already accepts for local files, so the renderer is unchanged and still never touches the network
   or a path. Only the relay's ffmpeg talks to the site, limited to http(s)/tls/tcp/crypto (no `file:`).

Seeking restarts the relay at the new position (a pipe cannot seek). The run estimates the position from the renderer's
playing state, so it can seek, resume after the end, and show the position in the panel.
"""
import ipaddress
import json
import os
import re
import select
import shutil
import signal
import subprocess
import threading
import time
from urllib.parse import unquote, urlsplit

from .video import FFPROBE_MAX_OUTPUT, VideoRejected, parse_probe, validate_format

MAX_URL = 4096
MAX_SOURCES = 8
MAX_WEB_TITLE = 200
MAX_UA = 256
MAX_CLIPBOARD = 16384   # the control frame limit (ipc.MAX_FRAME) bounds the whole request anyway
MAX_START_S = 48 * 3600
WEB_PROBE_TIMEOUT_S = 25
RELAY_BUFFER = 128 << 20  # read-ahead between ffmpeg and the renderer: ~10 s of 8K at 100 Mbit/s
RELAY_CHUNK = 1 << 20
SEEK_SETTLE_S = 0.4      # seek keys within this long are combined into one relay restart
END_MARGIN_S = 3         # a seek never lands closer than this to the end
# The relay's ffmpeg and ffprobe may use only these protocols: no file:, no pipes, no other schemes from a playlist.
NET_PROTOCOLS = 'https,http,tls,tcp,crypto'
MARKER = 'viture_xr_video'  # key of the bookmarklet's JSON object (version 1)

# DelightXR `format` values (delight-vr.com/documentation/dl8-video): -> (projection, stereo).
DL8_FORMATS = {
    'MONO_360': ('360', 'mono'), 'STEREO_360_LR': ('360', 'sbs'), 'STEREO_360_TB': ('360', 'tb'),
    'MONO_180': ('180', 'mono'), 'STEREO_180_LR': ('180', 'sbs'), 'STEREO_180_TB': ('180', 'tb'),
    'STEREO_180_LR_SPHERICAL': ('180', 'sbs'), 'STEREO_180_TB_SPHERICAL': ('180', 'tb'),
    'STEREO_FISHEYE_LR': ('fisheye', 'sbs'),  # base of DelightXR's custom fisheye formats (fov from the page)
}
DL8_FLAT = ('MONO_FLAT', 'STEREO_FLAT_LR', 'STEREO_FLAT_LR_SQUARE', 'STEREO_FLAT_TB', 'STEREO_FLAT_TB_SQUARE')
HLS_TYPES = ('application/x-mpegurl', 'application/vnd.apple.mpegurl', 'audio/mpegurl')
DASH_TYPES = ('application/dash+xml',)
WEB_KEYS = {'page', 'title', 'format', 'fov', 'start', 'sources', 'ua', MARKER}
SOURCE_KEYS = {'src', 'type', 'quality', 'format', 'via'}
VIA = ('dl8', 'video', 'resource', 'link')  # where the bookmarklet found the source (rank: player first)
TOKEN_RE = re.compile(r'[A-Za-z0-9_]{1,64}\Z')


# --- validation -------------------------------------------------------------------------------------------------------

def _printable(s, limit):
    """Printable ASCII only: these strings end up in HTTP headers or ffmpeg arguments."""
    return isinstance(s, str) and len(s) <= limit and all(0x20 <= ord(c) < 0x7f for c in s)


def validate_url(url, *, allow_local=False):
    """An http(s) URL the relay may fetch. Refuses blob:/data:/file:, credentials, whitespace and local addresses."""
    if not isinstance(url, str) or not url or len(url) > MAX_URL: raise VideoRejected('invalid video link')
    low = url[:8].lower()
    if low.startswith('blob:'):
        raise VideoRejected('the page plays this video from a blob (in-page streaming); use the bookmarklet, which looks '
                            'for the stream link')
    if not (low.startswith('http://') or low.startswith('https://')): raise VideoRejected('only http(s) video links can be played')
    if any(ord(c) <= 0x20 or ord(c) == 0x7f for c in url): raise VideoRejected('invalid video link')
    try: parts = urlsplit(url)
    except ValueError: raise VideoRejected('invalid video link') from None
    if parts.username is not None or parts.password is not None: raise VideoRejected('links with a user name or password are not played')
    try: host = (parts.hostname or '').rstrip('.')
    except ValueError: raise VideoRejected('invalid video link') from None
    if not host: raise VideoRejected('invalid video link')
    try: parts.port  # raises on a malformed port
    except ValueError: raise VideoRejected('invalid video link') from None
    if not allow_local:
        if host.lower() == 'localhost' or host.lower().endswith('.localhost'): raise VideoRejected('local addresses are not played')
        try: ip = ipaddress.ip_address(host.strip('[]'))
        except ValueError: ip = None
        if ip is not None and (ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_unspecified or ip.is_multicast or ip.is_reserved):
            raise VideoRejected('local addresses are not played')
    return url


def validate_web(obj, *, allow_local=False):
    """The bookmarklet's object (version 1). Returns a normalised copy; raises VideoRejected with a user-facing reason."""
    if not isinstance(obj, dict) or obj.get(MARKER) != 1 or set(obj) - WEB_KEYS: raise VideoRejected('not a VITURE browser-video link')
    out = {}
    page = obj.get('page')
    if page is not None:
        if not isinstance(page, str) or len(page) > MAX_URL or not page.lower().startswith(('http://', 'https://')):
            page = None  # the Referer is optional; a file:// or odd page is simply not sent
        elif any(ord(c) <= 0x20 or ord(c) == 0x7f for c in page): page = None
    out['page'] = page
    title = obj.get('title')
    out['title'] = title[:MAX_WEB_TITLE] if isinstance(title, str) else ''
    fmt = obj.get('format')
    if fmt is not None and (not isinstance(fmt, str) or not TOKEN_RE.match(fmt)): raise VideoRejected('invalid player format')
    out['format'] = fmt.upper() if fmt else None
    fov = obj.get('fov')
    if fov is not None and (type(fov) not in (int, float) or not 90 <= fov <= 360): raise VideoRejected('invalid fisheye field of view')
    out['fov'] = fov
    start = obj.get('start', 0)
    if type(start) not in (int, float) or start != start or not 0 <= start <= MAX_START_S: start = 0
    out['start'] = float(start)
    ua = obj.get('ua')
    out['ua'] = ua if _printable(ua, MAX_UA) and ua else None
    sources = obj.get('sources')
    if not isinstance(sources, list) or not sources: raise VideoRejected('the page shows no video link (is a video player on it?)')
    clean, refusals = [], []
    for s in sources[:MAX_SOURCES]:
        if not isinstance(s, dict) or set(s) - SOURCE_KEYS: raise VideoRejected('invalid video source')
        try: src = validate_url(s.get('src'), allow_local=allow_local)
        except VideoRejected as exc:
            refusals.append(str(exc)); continue
        typ, quality, sfmt, via = s.get('type'), s.get('quality'), s.get('format'), s.get('via', 'dl8')
        if typ is not None and not _printable(typ, 64): raise VideoRejected('invalid video source')
        if quality is not None and not _printable(quality, 32): raise VideoRejected('invalid video source')
        if sfmt is not None and (not isinstance(sfmt, str) or not TOKEN_RE.match(sfmt)): raise VideoRejected('invalid player format')
        if via not in VIA: raise VideoRejected('invalid video source')
        clean.append({'src': src, 'type': (typ or '').lower(), 'quality': quality or '', 'format': sfmt.upper() if sfmt else None,
                      'via': via})
    if not clean: raise VideoRejected(refusals[0] if refusals else 'the page shows no playable video link')
    out['sources'] = clean
    return out


def parse_clipboard(text, *, allow_local=False):
    """Clipboard text -> a video_open payload ({"web": ...} or {"url": ...}). Raises VideoRejected (shown to Cody)."""
    if not isinstance(text, str) or not text.strip(): raise VideoRejected('the clipboard is empty: click "Play in VITURE" on the video page first')
    if len(text) > MAX_CLIPBOARD: raise VideoRejected('the clipboard holds too much text to be a video link')
    t = text.strip()
    if t.startswith('{'):
        try: obj = json.loads(t)
        except ValueError: raise VideoRejected('the clipboard does not hold a video link') from None
        validate_web(obj, allow_local=allow_local)
        return {'web': obj}
    if '\n' in t or ' ' in t: raise VideoRejected('the clipboard does not hold a video link')
    validate_url(t, allow_local=allow_local)
    return {'url': t}


def web_request(payload, *, allow_local=False):
    """Normalise video_open's web forms ({"url"} or {"web"}) into the bookmarklet shape (validated)."""
    if 'url' in payload:
        url = validate_url(payload['url'], allow_local=allow_local)
        return validate_web({MARKER: 1, 'sources': [{'src': url, 'via': 'link'}]}, allow_local=allow_local)
    return validate_web(payload['web'], allow_local=allow_local)


# --- formats and source choice ----------------------------------------------------------------------------------------

def dl8_format(token, fov=None):
    """DelightXR format token -> our format dict, None if unknown (detect from the stream). Refuses flat video."""
    if not token: return None
    token = token.upper()
    if token in DL8_FLAT: raise VideoRejected('this is a flat (non-VR) video: watch it on a desktop instead')
    if token not in DL8_FORMATS: return None
    projection, stereo = DL8_FORMATS[token]
    f = 180
    if projection == 'fisheye' and fov is not None: f = int(round(min(220, max(180, fov))))
    return validate_format({'projection': projection, 'stereo': stereo, 'fov': f})


def source_kind(source):
    typ, path = source['type'], urlsplit(source['src']).path.lower()
    if typ in HLS_TYPES or path.endswith('.m3u8'): return 'hls'
    if typ in DASH_TYPES or path.endswith('.mpd'): return 'dash'
    return 'progressive'


def quality_height(label):
    """'8K' -> 4320, '5.7k' -> 2880, '4k' -> 2160, '2160p' -> 2160, 'UHD' -> 2160; 0 if unknown."""
    s = (label or '').strip().lower()
    m = re.search(r'(\d+(?:\.\d+)?)\s*k\b', s)
    if m: return int(float(m.group(1)) * 540)
    m = re.search(r'(\d{3,4})\s*[pi]?\b', s)
    if m: return int(m.group(1))
    return {'uhd': 2160, 'fhd': 1080, 'full hd': 1080, 'hd': 720, 'sd': 480}.get(s, 0)


def choose_source(web):
    """The best source: the player's own over a page-wide find, then the highest quality label, then a progressive file
    (seeks and reconnects best), then HLS/DASH (ffmpeg takes their best variant), then page order."""
    via_rank = {v: i for i, v in enumerate(VIA)}
    kind_rank = {'progressive': 0, 'hls': 1, 'dash': 2}
    order = sorted(enumerate(web['sources']), key=lambda e: (via_rank[e[1]['via']], -quality_height(e[1]['quality']),
                                                             kind_rank[source_kind(e[1])], e[0]))
    return order[0][1]


def name_from_url(url):
    return unquote(urlsplit(url).path.rsplit('/', 1)[-1]) or 'stream'


def web_title(web, source):
    title = (web.get('title') or '').strip()
    return title or name_from_url(source['src'])


def request_headers(web):
    return {'Referer': web['page']} if web.get('page') else {}


# --- probe and relay --------------------------------------------------------------------------------------------------

def ffmpeg_path():
    return '/usr/bin/ffmpeg' if os.access('/usr/bin/ffmpeg', os.X_OK) else shutil.which('ffmpeg')


def _ffprobe_path():
    return '/usr/bin/ffprobe' if os.access('/usr/bin/ffprobe', os.X_OK) else shutil.which('ffprobe')


def _net_args(headers, ua):
    args = ['-protocol_whitelist', NET_PROTOCOLS, '-rw_timeout', '15000000']
    if ua: args += ['-user_agent', ua]
    if headers: args += ['-headers', ''.join(f'{k}: {v}\r\n' for k, v in headers.items())]
    return args


def explain_failure(stderr, returncode=None):
    """ffmpeg/ffprobe stderr -> a short reason for Cody (never the URL, which may hold a signed token)."""
    text = stderr.decode('utf-8', errors='replace') if isinstance(stderr, bytes) else str(stderr or '')
    low = text.lower()
    if 'cenc' in low or 'drm' in low or 'widevine' in low or ('encrypt' in low and 'aes' not in low):
        return 'the stream is DRM-protected and plays only inside the browser'
    for code, why in (('401', 'the site wants a login for this stream (HTTP 401)'),
                      ('403', 'the site refused the stream (HTTP 403): the link may have expired or need your browser login'),
                      ('404', 'the stream was not found (HTTP 404): the link may have expired'),
                      ('410', 'the stream is gone (HTTP 410)')):
        if f'http error {code}' in low or f'server returned {code}' in low: return why
    if 'not on whitelist' in low: return 'the stream refers to something other than web links, which is not played'
    if 'timed out' in low or 'connection timed out' in low: return 'the site did not answer in time'
    if 'name or service not known' in low or 'failed to resolve' in low or 'temporary failure in name' in low:
        return 'could not look up the site (offline?)'
    if 'connection refused' in low: return 'the site refused the connection'
    if 'invalid data found' in low: return 'the link does not lead to a playable video'
    return 'the stream could not be opened' + (f' (ffmpeg exit {returncode})' if returncode else '')


def probe_url(url, headers=None, ua=None, *, run=subprocess.run, ffprobe=None, timeout=WEB_PROBE_TIMEOUT_S):
    """ffprobe the stream over the network (same protocols as the relay). Info dict as video.parse_probe, or None if
    ffprobe is missing. HLS/DASH: the highest-resolution video stream is reported (the one the relay picks)."""
    exe = ffprobe if ffprobe is not None else _ffprobe_path()
    if not exe: return None
    argv = [exe, '-v', 'error', '-hide_banner', *_net_args(headers, ua), '-print_format', 'json', '-show_streams',
            '-show_format', '-i', url]
    try:
        r = run(argv, stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout, check=False,
                env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'})
    except subprocess.TimeoutExpired: raise VideoRejected('the site took too long to answer') from None
    except OSError: return None
    if r.returncode != 0 or len(r.stdout) > FFPROBE_MAX_OUTPUT: raise VideoRejected(explain_failure(r.stderr, r.returncode))
    try: data = json.loads(r.stdout)
    except ValueError: raise VideoRejected('the link does not lead to a playable video') from None
    if isinstance(data, dict) and isinstance(data.get('streams'), list):
        # Variant streams (HLS/DASH): report the one ffmpeg's default selection takes, the largest picture.
        videos = [s for s in data['streams'] if isinstance(s, dict) and s.get('codec_type') == 'video'
                  and not (s.get('disposition') or {}).get('attached_pic')]
        def area(s):
            w, h = s.get('width'), s.get('height')
            return w * h if type(w) is int and type(h) is int else 0
        videos.sort(key=area, reverse=True)
        data = dict(data, streams=videos[:1] + [s for s in data['streams'] if s not in videos])
    info = parse_probe(data)
    fmt = data.get('format') if isinstance(data, dict) else None
    info['container'] = str((fmt or {}).get('format_name') or '')[:64]
    return info


def relay_argv(url, headers=None, ua=None, start=0.0, ffmpeg=None):
    """ffmpeg copying the stream (video + audio, no re-encoding) as NUT to stdout. NUT carries every codec mpv plays
    (H.264, HEVC, VP9, AV1, AAC, Opus), streams without an index, and starts cleanly mid-way in HLS (Matroska cannot
    take ADTS AAC from a seek into a TS segment)."""
    exe = ffmpeg or ffmpeg_path()
    if not exe: raise VideoRejected('ffmpeg is not installed')
    argv = [exe, '-nostdin', '-hide_banner', '-loglevel', 'error', *_net_args(headers, ua)]
    argv += ['-reconnect', '1', '-reconnect_on_network_error', '1', '-reconnect_delay_max', '5']  # http: resume a dropped link
    if start > 0: argv += ['-ss', f'{start:.3f}']
    argv += ['-i', url, '-sn', '-dn', '-c', 'copy', '-f', 'nut', 'pipe:1']
    return argv


class StreamRelay:
    """One ffmpeg relay: network -> ffmpeg (copy) -> read-ahead buffer -> pipe whose read end goes to the renderer.

    `start()` returns the read end (the caller hands it over and closes its copy). `stop()` ends ffmpeg and the pump.
    Threads: a reader (ffmpeg stdout/stderr into the buffer, bounded) and a writer (buffer into the renderer's pipe,
    non-blocking so stop() never waits on a paused renderer)."""
    def __init__(self, argv, *, popen=subprocess.Popen, buffer=RELAY_BUFFER):
        self.argv, self.popen, self.limit = list(argv), popen, buffer
        self.proc = None
        self.returncode = None
        self.stderr_tail = b''
        self.bytes_in = self.bytes_out = 0
        self._chunks, self._size, self._eof = [], 0, False
        self._cond = threading.Condition()
        self._stop = threading.Event()
        self._threads = []
        self._write_fd = None

    def start(self):
        read_fd, write_fd = os.pipe2(os.O_CLOEXEC)
        try:
            os.set_blocking(write_fd, False)
            self.proc = self.popen(self.argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'}, start_new_session=True)
        except BaseException:
            os.close(read_fd); os.close(write_fd); raise
        self._write_fd = write_fd
        for target in (self._read, self._write):
            t = threading.Thread(target=target, daemon=True); t.start(); self._threads.append(t)
        return read_fd

    @property
    def finished(self):
        return self.returncode is not None

    def _read(self):
        out, err = self.proc.stdout, self.proc.stderr
        fds = {out.fileno(): 'out', err.fileno(): 'err'}
        try:
            while fds and not self._stop.is_set():
                with self._cond:
                    while self._size >= self.limit and not self._stop.is_set(): self._cond.wait(0.25)
                ready, _, _ = select.select(list(fds), [], [], 0.25)
                for fd in ready:
                    data = os.read(fd, RELAY_CHUNK)
                    if not data: del fds[fd]; continue
                    if fds[fd] == 'err': self.stderr_tail = (self.stderr_tail + data)[-4096:]
                    else:
                        with self._cond:
                            self._chunks.append(data); self._size += len(data); self.bytes_in += len(data)
                            self._cond.notify_all()
        except (OSError, ValueError): pass
        finally:
            try: self.returncode = self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._kill(); self.returncode = self.proc.wait()
            with self._cond:
                self._eof = True; self._cond.notify_all()

    def _write(self):
        fd = self._write_fd
        try:
            while not self._stop.is_set():
                with self._cond:
                    while not self._chunks and not self._eof and not self._stop.is_set(): self._cond.wait(0.25)
                    if self._stop.is_set() or (not self._chunks and self._eof): break
                    data = self._chunks[0]
                _, ready, _ = select.select([], [fd], [], 0.25)
                if not ready: continue
                try: n = os.write(fd, data)
                except BlockingIOError: continue
                with self._cond:
                    if n == len(data): self._chunks.pop(0)
                    else: self._chunks[0] = data[n:]
                    self._size -= n; self.bytes_out += n
                    self._cond.notify_all()
        except OSError: pass  # EPIPE: the renderer closed the video
        finally:
            try: os.close(fd)
            except OSError: pass

    def _kill(self):
        if self.proc is None or self.proc.poll() is not None: return
        try: os.killpg(self.proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError): pass

    def stop(self, timeout=3.0):
        self._stop.set()
        with self._cond: self._cond.notify_all()
        if self.proc is not None and self.proc.poll() is None:
            try: os.killpg(self.proc.pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError): pass
        deadline = time.monotonic() + timeout
        for t in self._threads: t.join(max(0.0, deadline - time.monotonic()))
        self._kill()
        if self.proc is not None:
            try: self.returncode = self.proc.wait(timeout=1)
            except subprocess.TimeoutExpired: pass
            for f in (self.proc.stdout, self.proc.stderr):
                try: f.close()
                except (OSError, AttributeError): pass

    def failure(self):
        """A reason if ffmpeg ended with an error, else None."""
        if self.returncode in (None, 0) or self._stop.is_set(): return None
        return explain_failure(self.stderr_tail, self.returncode)


class WebStream:
    """The playing web stream: what to relay, and a position estimate (renderer playing time since the last restart)."""
    def __init__(self, web, source, info, *, relay_factory=None, ffmpeg=None):
        self.web, self.source, self.info = web, source, info or {}
        self.url, self.headers, self.ua = source['src'], request_headers(web), web.get('ua')
        self.duration = self.info.get('duration') if self.info.get('duration') and self.info['duration'] > 0 else None
        self.ffmpeg = ffmpeg
        self.relay_factory = relay_factory or (lambda argv: StreamRelay(argv))
        self.relay = None
        self.base = 0.0       # stream position at the relay's start
        self.played = 0.0     # renderer 'playing' seconds since then
        self.pending_seek = None
        self.seek_at = None
        self.restarts = 0

    @property
    def seekable(self):
        return self.duration is not None

    def position(self):
        p = self.base + self.played
        return min(p, self.duration) if self.duration else p

    def start(self, at):
        """(Re)start the relay at `at` seconds; returns the renderer's read descriptor."""
        if self.relay is not None: self.relay.stop()
        if self.duration: at = max(0.0, min(at, self.duration - END_MARGIN_S))
        at = max(0.0, at)
        self.relay = self.relay_factory(relay_argv(self.url, self.headers, self.ua, at, self.ffmpeg))
        fd = self.relay.start()
        self.base, self.played, self.restarts = at, 0.0, self.restarts + 1
        return fd

    def advance(self, dt, state):
        if state == 'playing' and dt > 0: self.played += min(dt, 1.0)  # bounded: a stalled loop does not jump ahead

    def request_seek(self, delta, now):
        if not self.seekable: return False
        target = (self.pending_seek if self.pending_seek is not None else self.position()) + delta
        self.pending_seek, self.seek_at = max(0.0, target), now
        return True

    def due_seek(self, now):
        """The combined seek target once the keys have settled, else None."""
        if self.pending_seek is None or now - self.seek_at < SEEK_SETTLE_S: return None
        target, self.pending_seek, self.seek_at = self.pending_seek, None, None
        return target

    def stop(self):
        if self.relay is not None: self.relay.stop(); self.relay = None

    def failure(self):
        return self.relay.failure() if self.relay is not None else None

    def snapshot(self):
        return {'kind': source_kind(self.source), 'site': urlsplit(self.url).hostname, 'position': round(self.position(), 1),
                'duration': round(self.duration, 1) if self.duration else None, 'seekable': self.seekable,
                'seeking': self.pending_seek is not None}
