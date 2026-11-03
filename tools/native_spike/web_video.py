"""VR video from the web: a direct stream link, or a video page link resolved with yt-dlp (docs/web-video.md).

1. Ctrl+Alt+U in the glasses (or the panel's "Paste from browser") reads the clipboard once and sends the link to the
   run as a `video_open` request (`{"url": "https://..."}`).
2. The run (this module) checks the request, probes the stream with ffprobe and starts a relay: an ffmpeg process that
   copies the stream (no re-encoding) into NUT on a pipe. The renderer gets the pipe's read end as the descriptor it
   already accepts for local files, so the renderer is unchanged and still never touches the network or a path. Only
   the relay's ffmpeg talks to the site, limited to http(s)/tls/tcp/crypto (no `file:`).

A video PAGE link (the address bar of a video site) is resolved with
yt-dlp (`resolve_page`): no config, plugins, cache or cookies; the best direct stream (and separate audio) it lists is
relayed with the request headers yt-dlp gives, minus any Cookie/Authorization. The VR format then comes from the title
and tags (360, 180, VR180, SBS/LR, TB/OU, fisheye...), else the picture shape; Tab corrects it. Without yt-dlp the
refusal says so. Reasons never contain the link (it may hold a signed token) or a video id.

Seeking restarts the relay at the new position (a pipe cannot seek). The run estimates the position from the renderer's
playing state, so it can seek, resume after the end, and show the position in the panel.
"""
import fcntl
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

from .video import FFPROBE_MAX_OUTPUT, HW_DECODE_MAX_WIDTH, MAX_SIDE, VideoRejected, parse_probe, validate_format

YTDLP_TIMEOUT_S = 60
YTDLP_MAX_OUTPUT = 32 << 20  # -J of a long page with many formats is a few MB
MEDIA_EXT = ('.mp4', '.m4v', '.webm', '.mkv', '.mov', '.m3u8', '.mpd', '.ts', '.m2ts', '.avi', '.ogv', '.flv')
PAGE_LINK = "that's a web page link, not a video link"
PAGE_HELP = "copy the video's own link instead"
NO_YTDLP = ('yt-dlp, which finds the video on a page, is not installed (it is in the Arch repos: yt-dlp)')
# yt-dlp request headers passed on to the relay; anything carrying a login is dropped (cookies-free by design).
DROP_HEADERS = ('cookie', 'authorization', 'proxy-authorization')
RELAY_PROTOCOLS = ('https', 'http', 'm3u8', 'm3u8_native')  # yt-dlp format protocols ffmpeg can read directly


class ProbeFailed(VideoRejected):
    """The stream could not be probed; `kind` is 'invalid' (not a media file: often a web page), 'http' or 'other'."""
    def __init__(self, message, kind='other'):
        super().__init__(message)
        self.kind = kind


class PageUnsupported(VideoRejected):
    """yt-dlp found no video on the page (or does not know the site)."""

MAX_URL = 4096
MAX_SOURCES = 8
MAX_WEB_TITLE = 200
MAX_UA = 256
MAX_CLIPBOARD = 16384   # the control frame limit (ipc.MAX_FRAME) bounds the whole request anyway
MAX_START_S = 48 * 3600
WEB_PROBE_TIMEOUT_S = 25
RELAY_BUFFER = 128 << 20  # read-ahead between ffmpeg and the renderer: ~10 s of 8K at 100 Mbit/s
RELAY_CHUNK = 1 << 20
# Kernel buffer of the two relay pipes (ffmpeg -> run, run -> renderer), up from 64 KiB: 16x fewer reads and wake-ups per
# MiB, and the renderer's demuxer rides out a stall of the run's threads. 1 MiB is fs.pipe-max-size's default; a lower
# limit just keeps the kernel's size.
RELAY_PIPE = 1 << 20
SEEK_SETTLE_S = 0.4      # seek keys within this long are combined into one relay restart
END_MARGIN_S = 3         # a seek never lands closer than this to the end
# The relay's ffmpeg and ffprobe may use only these protocols: no file:, no pipes, no other schemes from a playlist.
NET_PROTOCOLS = 'https,http,tls,tcp,crypto'
MARKER = 'viture_xr_video'  # key of a {"web"} request object (version 1)

# Player `format` tokens a {"web"} request may carry (dl8-video style): -> (projection, stereo).
DL8_FORMATS = {
    'MONO_360': ('360', 'mono'), 'STEREO_360_LR': ('360', 'sbs'), 'STEREO_360_TB': ('360', 'tb'),
    'MONO_180': ('180', 'mono'), 'STEREO_180_LR': ('180', 'sbs'), 'STEREO_180_TB': ('180', 'tb'),
    'STEREO_180_LR_SPHERICAL': ('180', 'sbs'), 'STEREO_180_TB_SPHERICAL': ('180', 'tb'),
    'STEREO_FISHEYE_LR': ('fisheye', 'sbs'),  # fisheye base format (fov from the request)
}
DL8_FLAT = ('MONO_FLAT', 'STEREO_FLAT_LR', 'STEREO_FLAT_LR_SQUARE', 'STEREO_FLAT_TB', 'STEREO_FLAT_TB_SQUARE')
HLS_TYPES = ('application/x-mpegurl', 'application/vnd.apple.mpegurl', 'audio/mpegurl')
DASH_TYPES = ('application/dash+xml',)
WEB_KEYS = {'page', 'title', 'format', 'fov', 'start', 'sources', 'ua', MARKER}
SOURCE_KEYS = {'src', 'type', 'quality', 'format', 'via'}
VIA = ('dl8', 'video', 'resource', 'link', 'page')  # where the source came from (rank: player first; 'page': a page link)
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
        raise VideoRejected('the page plays this video from a blob (in-page streaming); copy the page link or the '
                            "video's own link instead")
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
    """A {"web"} request object (version 1). Returns a normalised copy; raises VideoRejected with a user-facing reason."""
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
    """Clipboard text -> a video_open payload ({"url": ...}). Raises VideoRejected (shown to Cody)."""
    if not isinstance(text, str) or not text.strip(): raise VideoRejected('the clipboard is empty: copy a video link first')
    if len(text) > MAX_CLIPBOARD: raise VideoRejected('the clipboard holds too much text to be a video link')
    t = text.strip()
    if '\n' in t or ' ' in t: raise VideoRejected('the clipboard does not hold a video link')
    validate_url(t, allow_local=allow_local)
    return {'url': t}


def web_request(payload, *, allow_local=False):
    """Normalise video_open's web forms ({"url"} or {"web"}) into one validated shape."""
    if 'url' in payload:
        url = validate_url(payload['url'], allow_local=allow_local)
        return validate_web({MARKER: 1, 'sources': [{'src': url, 'via': 'link'}]}, allow_local=allow_local)
    return validate_web(payload['web'], allow_local=allow_local)


# --- formats and source choice ----------------------------------------------------------------------------------------

def dl8_format(token, fov=None):
    """Player format token -> our format dict, None if unknown (detect from the stream). Refuses flat video."""
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


URL_RE = re.compile(r'(?i)\b(?:https?|ftp|file|blob|data)://\S+|\bwww\.\S+')
PREFIX_RE = re.compile(r'^(?:ERROR:\s*)?(?:\[[^\]]*\]\s*)*(?:[\w.-]{4,}:\s+)?')


def sanitize(text, limit=140):
    """The last meaningful stderr line, safe to show: no links, no `[https @ 0x...]` or `[site] <video id>:` prefix."""
    text = text.decode('utf-8', errors='replace') if isinstance(text, bytes) else str(text or '')
    for line in reversed([l.strip() for l in text.splitlines()]):
        line = PREFIX_RE.sub('', URL_RE.sub('<link>', line))
        line = re.sub(r'^(?:<link>\s*:?\s*)+', '', line).strip(' :.')
        line = re.sub(r'\s+', ' ', ''.join(c if c.isprintable() else ' ' for c in line))
        if line and line not in ('<link>',) and not line.lower().startswith(('conversion failed', 'error opening input')):
            return line[:limit - 3].rstrip() + '...' if len(line) > limit else line
    return ''


def failure_kind(stderr):
    low = (stderr.decode('utf-8', errors='replace') if isinstance(stderr, bytes) else str(stderr or '')).lower()
    if 'invalid data found' in low: return 'invalid'
    if re.search(r'http error \d{3}|server returned \d|\b[45]xx\b', low): return 'http'
    return 'other'


def explain_failure(stderr, returncode=None, tool='ffmpeg'):
    """ffmpeg/ffprobe stderr -> a short reason for Cody (never the URL, which may hold a signed token). Unknown errors
    show ffmpeg's own last line (links removed), not just an exit code."""
    text = stderr.decode('utf-8', errors='replace') if isinstance(stderr, bytes) else str(stderr or '')
    low = text.lower()
    if 'cenc' in low or 'drm' in low or 'widevine' in low or ('encrypt' in low and 'aes' not in low):
        return 'the stream is DRM-protected and plays only inside the browser'
    for code, why in (('401', 'the site wants a login for this stream (HTTP 401)'),
                      ('403', 'the site refused the stream (HTTP 403): the link may have expired or need your browser login'),
                      ('404', 'the stream was not found (HTTP 404): the link may have expired'),
                      ('410', 'the stream is gone (HTTP 410)'),
                      ('429', 'the site is rate-limiting requests (HTTP 429): try again in a minute')):
        if f'http error {code}' in low or f'server returned {code}' in low: return why
    m = re.search(r'http error (\d{3})', low)
    if m: return f'the site answered HTTP {m.group(1)}'
    if 'server returned 4xx' in low: return 'the site refused the request (HTTP 4xx)'
    if 'server returned 5xx' in low: return 'the site had an error (HTTP 5xx): try again later'
    if 'not on whitelist' in low: return 'the stream refers to something other than web links, which is not played'
    if 'timed out' in low or 'connection timed out' in low: return 'the site did not answer in time'
    if 'name or service not known' in low or 'failed to resolve' in low or 'temporary failure in name' in low:
        return 'could not look up the site (offline?)'
    if 'connection refused' in low: return 'the site refused the connection'
    if 'invalid data found' in low: return 'the link does not lead to a playable video'
    detail = sanitize(text)
    if detail: return f'{tool} could not open the stream: {detail}'
    return f'the stream could not be opened ({tool} exit {returncode})' if returncode else 'the stream could not be opened'


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
    if r.returncode != 0 or len(r.stdout) > FFPROBE_MAX_OUTPUT:
        raise ProbeFailed(explain_failure(r.stderr, r.returncode, 'ffprobe'), failure_kind(r.stderr))
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


def relay_argv(url, headers=None, ua=None, start=0.0, ffmpeg=None, audio=None):
    """ffmpeg copying the stream (video + audio, no re-encoding) as NUT to stdout. NUT carries every codec mpv plays
    (H.264, HEVC, VP9, AV1, AAC, Opus), streams without an index, and starts cleanly mid-way in HLS (Matroska cannot
    take ADTS AAC from a seek into a TS segment). `audio`: a separate audio stream (sites that list video and audio
    apart, resolved by yt-dlp); both inputs get the same headers and start, and one stream of each is mapped."""
    exe = ffmpeg or ffmpeg_path()
    if not exe: raise VideoRejected('ffmpeg is not installed')
    argv = [exe, '-nostdin', '-hide_banner', '-loglevel', 'error']
    for i, u in enumerate([url] + ([audio] if audio else [])):
        argv += _net_args(headers, ua)
        argv += ['-reconnect', '1', '-reconnect_on_network_error', '1', '-reconnect_delay_max', '5']  # http: resume a dropped link
        if start > 0: argv += ['-ss', f'{start:.3f}']
        argv += ['-i', u]
    if audio: argv += ['-map', '0:v:0', '-map', '1:a:0']
    argv += ['-sn', '-dn', '-c', 'copy', '-f', 'nut', 'pipe:1']
    return argv


def _grow_pipe(fd, size=RELAY_PIPE):
    try: fcntl.fcntl(fd, fcntl.F_SETPIPE_SZ, size)
    except (OSError, AttributeError, ValueError): pass  # best effort: the default size still works


class StreamRelay:
    """One ffmpeg relay: network -> ffmpeg (copy) -> read-ahead buffer -> pipe whose read end goes to the renderer.

    `start()` returns the read end (the caller hands it over and closes its copy). `stop()` ends ffmpeg and the pump in
    a few milliseconds (it runs on the supervisor loop, once per seek). Threads: a reader (ffmpeg stdout/stderr into the
    buffer, bounded) and a writer (buffer into the renderer's pipe, non-blocking so stop() never waits on a paused
    renderer); both also watch a wake-up pipe that stop() writes to."""
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
        self._wake = None  # (read, write): stop() wakes both threads at once instead of their 0.25 s polls

    def start(self):
        read_fd, write_fd = os.pipe2(os.O_CLOEXEC)
        try:
            os.set_blocking(write_fd, False)
            _grow_pipe(write_fd)
            self._wake = os.pipe2(os.O_CLOEXEC | os.O_NONBLOCK)
            self.proc = self.popen(self.argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'}, start_new_session=True)
        except BaseException:
            os.close(read_fd); os.close(write_fd)
            if self._wake: os.close(self._wake[0]); os.close(self._wake[1]); self._wake = None
            raise
        out = getattr(self.proc.stdout, 'fileno', None)
        if out: _grow_pipe(out())
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
        wake = self._wake[0]
        try:
            while fds and not self._stop.is_set():
                with self._cond:
                    while self._size >= self.limit and not self._stop.is_set(): self._cond.wait(0.25)
                ready, _, _ = select.select(list(fds) + [wake], [], [], 0.25)
                for fd in ready:
                    if fd == wake: continue  # stop(): the loop condition ends it
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
        fd, wake = self._write_fd, self._wake[0]
        try:
            while not self._stop.is_set():
                with self._cond:
                    while not self._chunks and not self._eof and not self._stop.is_set(): self._cond.wait(0.25)
                    if self._stop.is_set() or (not self._chunks and self._eof): break
                    data = self._chunks[0]
                _, ready, _ = select.select([wake], [fd], [], 0.25)  # readable `wake`: stop()
                if fd not in ready: continue
                try: n = os.write(fd, data)
                except BlockingIOError: continue
                with self._cond:
                    if n == len(data): self._chunks.pop(0)
                    else: self._chunks[0] = memoryview(data)[n:]  # no copy of the rest
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
        if self._wake:
            try: os.write(self._wake[1], b'x')
            except OSError: pass
        # Killed at once: its output is being thrown away, and a copy into a pipe has nothing to finish. A SIGTERM'd
        # ffmpeg stays blocked writing to the pipe nobody reads any more, which held every seek for the whole join
        # timeout (3 s).
        self._kill()
        deadline = time.monotonic() + timeout
        for t in self._threads: t.join(max(0.0, deadline - time.monotonic()))
        if self.proc is not None:
            try: self.returncode = self.proc.wait(timeout=1)
            except subprocess.TimeoutExpired: pass
            for f in (self.proc.stdout, self.proc.stderr):
                try: f.close()
                except (OSError, AttributeError): pass
        if self._wake and not any(t.is_alive() for t in self._threads):
            for w in self._wake:
                try: os.close(w)
                except OSError: pass
            self._wake = None

    def failure(self):
        """A reason if ffmpeg ended with an error, else None."""
        if self.returncode in (None, 0) or self._stop.is_set(): return None
        return explain_failure(self.stderr_tail, self.returncode, 'ffmpeg')


class WebStream:
    """The playing web stream: what to relay, and a position estimate (renderer playing time since the last restart)."""
    def __init__(self, target, info, *, relay_factory=None, ffmpeg=None):
        self.target, self.info = target, info or {}
        self.url, self.audio, self.headers, self.ua = target['url'], target.get('audio'), target['headers'], target['ua']
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
        self.relay = self.relay_factory(relay_argv(self.url, self.headers, self.ua, at, self.ffmpeg, self.audio))
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
        return {'kind': self.target['kind'], 'site': self.target['site'], 'resolved': self.target['via'] == 'yt-dlp',
                'position': round(self.position(), 1),
                'duration': round(self.duration, 1) if self.duration else None, 'seekable': self.seekable,
                'seeking': self.pending_seek is not None}


# --- video pages: yt-dlp ----------------------------------------------------------------------------------------------

def looks_like_media(source):
    """A stream link (by its type or file extension), as opposed to a page to resolve."""
    typ = source.get('type') or ''
    if typ.startswith(('video/', 'audio/')) or typ in HLS_TYPES or typ in DASH_TYPES: return True
    return urlsplit(source['src']).path.lower().endswith(MEDIA_EXT)


def ytdlp_path():
    return '/usr/bin/yt-dlp' if os.access('/usr/bin/yt-dlp', os.X_OK) else shutil.which('yt-dlp')


def ytdlp_argv(url, exe):
    """No config files, plugins, cache, cookies or downloads; one video (no playlists); JSON on stdout."""
    return [exe, '--ignore-config', '--no-plugin-dirs', '--no-cache-dir', '--no-cookies', '--no-cookies-from-browser',
            '--no-playlist', '--skip-download', '--no-warnings', '--no-progress', '--socket-timeout', '15',
            '--dump-single-json', '--', url]


def resolve_page(url, *, run=subprocess.run, ytdlp=None, timeout=YTDLP_TIMEOUT_S):
    """yt-dlp's info for a video page. Raises VideoRejected (yt-dlp missing: NO_YTDLP in the text), PageUnsupported
    (no video found / unknown site) or VideoRejected with yt-dlp's reason (private, removed, age-gated...)."""
    exe = ytdlp if ytdlp is not None else ytdlp_path()
    if not exe: raise VideoRejected(f'{PAGE_LINK}, and {NO_YTDLP}; {PAGE_HELP}')
    env = {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C.UTF-8'}
    if os.environ.get('HOME', '').startswith('/'): env['HOME'] = os.environ['HOME']  # its JS runtime (deno) wants one
    try:
        r = run(ytdlp_argv(url, exe), stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout, check=False, env=env)
    except subprocess.TimeoutExpired: raise VideoRejected('yt-dlp took too long to read the page') from None
    except OSError: raise VideoRejected(f'{PAGE_LINK}, and {NO_YTDLP}; {PAGE_HELP}') from None
    err = r.stderr.decode('utf-8', errors='replace') if isinstance(r.stderr, bytes) else str(r.stderr or '')
    low = err.lower()
    if r.returncode != 0 or len(r.stdout) > YTDLP_MAX_OUTPUT:
        if 'unsupported url' in low or 'no video formats found' in low or 'no video could be found' in low:
            raise PageUnsupported('no video found on that page')
        if 'drm' in low: raise VideoRejected('the video is DRM-protected and plays only inside the browser')
        detail = sanitize(err)
        raise VideoRejected('yt-dlp could not get the video' + (f': {detail}' if detail else ''))
    try: data = json.loads(r.stdout)
    except ValueError: raise VideoRejected('yt-dlp gave no readable answer') from None
    if not isinstance(data, dict): raise VideoRejected('yt-dlp gave no readable answer')
    if data.get('_type') == 'playlist':
        entries = [e for e in data.get('entries') or [] if isinstance(e, dict)]
        if not entries: raise PageUnsupported('no video found on that page')
        data = entries[0]
    return data


def _codec(vcodec):
    v = str(vcodec or '').lower()
    for prefix, name in (('avc', 'h264'), ('h264', 'h264'), ('hev', 'hevc'), ('hvc', 'hevc'), ('h265', 'hevc'),
                         ('vp09', 'vp9'), ('vp9', 'vp9'), ('av01', 'av1'), ('av1', 'av1')):
        if v.startswith(prefix): return name
    return v.split('.')[0] or 'unknown'


def _num(v):
    return v if type(v) in (int, float) and v == v else 0


def _usable(f):
    return (isinstance(f, dict) and isinstance(f.get('url'), str) and f.get('protocol', 'https') in RELAY_PROTOCOLS
            and not f.get('has_drm') and not f.get('fragments'))


def pick_formats(data):
    """(video format, separate audio format or None) from yt-dlp's list. Best = decodes in hardware here at its size,
    then the most pixels, then frame rate, then bitrate (for VR, resolution is the quality limit)."""
    formats = [f for f in data.get('formats') or [data] if _usable(f)]
    # vcodec 'none' is audio only; None means unknown (yt-dlp's generic extractor on a plain <video>): maybe video.
    videos = [f for f in formats if f.get('vcodec') != 'none']
    videos = [f for f in videos if max(_num(f.get('width')), _num(f.get('height'))) <= MAX_SIDE]
    if not videos:
        if any(isinstance(f, dict) and f.get('has_drm') for f in data.get('formats') or []):
            raise VideoRejected('the video is DRM-protected and plays only inside the browser')
        raise VideoRejected('yt-dlp found no stream that can be played outside the browser')

    def rank(f):
        w, h = _num(f.get('width')), _num(f.get('height'))
        limit = HW_DECODE_MAX_WIDTH.get(_codec(f.get('vcodec')))
        return (1 if limit and w <= limit else 0, 1 if f.get('vcodec') else 0, w * h, _num(f.get('fps')),
                _num(f.get('tbr')) or _num(f.get('vbr')))
    video = max(videos, key=rank)
    if video.get('acodec') != 'none': return video, None  # has (or may have) its own audio
    audios = [f for f in formats if f.get('vcodec') == 'none' and f.get('acodec') not in (None, 'none')]
    audio = max(audios, key=lambda f: (_num(f.get('abr')) or _num(f.get('tbr')), _num(f.get('asr')))) if audios else None
    return video, audio


def relay_headers(fmt):
    """yt-dlp's per-format request headers, cookie-free and printable ASCII only: (headers, user agent)."""
    headers, ua = {}, None
    for k, v in (fmt.get('http_headers') or {}).items():
        if not isinstance(k, str) or not re.fullmatch(r'[A-Za-z0-9-]{1,64}', k) or not _printable(v, 1024) or not v: continue
        if k.lower() in DROP_HEADERS: continue
        if k.lower() == 'user-agent': ua = v[:MAX_UA]
        elif len(headers) < 12: headers[k] = v
    return headers, ua


def vr_hint(data):
    """Words that may name the VR format: the title and tags (detect_format reads tokens like 360, VR180, SBS, TB)."""
    words = [str(data.get('title') or '')]
    words += [str(t) for t in (data.get('tags') or [])[:40] if isinstance(t, str)]
    return ' '.join(words)[:2000]


def _target(url, headers, ua, kind, site, via, title, hint, audio=None):
    return {'url': url, 'audio': audio, 'headers': headers, 'ua': ua, 'kind': kind, 'site': site, 'via': via,
            'title': title, 'hint': hint}


def prepare_target(web, src, *, probe=None, resolve=None, allow_local=False):
    """What to relay for the chosen source, and its probe: (target, info).

    A stream link is probed directly. A page link (no media type or extension) goes to yt-dlp; if yt-dlp does not
    know the page, the link is tried as a stream after all, and an HTML answer gives a clear "web page" refusal."""
    probe = probe or probe_url
    resolve = resolve or resolve_page
    url, site = src['src'], urlsplit(src['src']).hostname
    headers, ua = request_headers(web), web.get('ua')
    title = web_title(web, src)

    def direct(page_like):
        try: info = probe(url, headers, ua)
        except ProbeFailed as exc:
            if exc.kind == 'invalid' and page_like: raise VideoRejected(f'{PAGE_LINK}: {PAGE_HELP}') from None
            raise
        return _target(url, headers, ua, source_kind(src), site, 'direct', title, name_from_url(url)), info

    if looks_like_media(src): return direct(False)
    try:
        data = resolve(url)
    except PageUnsupported:
        try: return direct(True)
        except VideoRejected as exc:
            if PAGE_LINK in str(exc): raise VideoRejected(f'{PAGE_LINK}, and yt-dlp found no video on it: {PAGE_HELP}') from None
            raise
    except VideoRejected as exc:
        if NO_YTDLP not in str(exc): raise
        try: return direct(True)
        except VideoRejected as inner:
            if PAGE_LINK in str(inner): raise VideoRejected(str(exc)) from None  # say yt-dlp is missing
            raise
    video, audio = pick_formats(data)
    for f in (video, audio):
        if f is not None: validate_url(f['url'], allow_local=allow_local)
    vh, vua = relay_headers(video)
    kind = 'hls' if str(video.get('protocol', '')).startswith('m3u8') else 'progressive'
    info = probe(video['url'], vh, vua)
    page_title = str(data.get('title') or '').strip() or title
    return _target(video['url'], vh, vua, kind, site, 'yt-dlp', page_title, vr_hint(data), audio and audio['url']), info
