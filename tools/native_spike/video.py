"""VR video in a capture run (docs/vr-video.md): 360°/180° equirectangular and fisheye video, mono or stereo, head-tracked
in the glasses instead of the desktops.

A video is a mode of a running capture session, not a new run. It needs no new consent: the run's consent already covers
the glasses display, and a video only changes what the renderer draws. Nothing is captured, journaled or created on the
compositor for it, and recovery has nothing to undo. It is opened from a file the user picks: Ctrl+Alt+O (the bind
helper xr_control.py shows a file picker on the focused desktop), the panel's Browse/Play, or a path sent to the
controller. A web video (a stream or video page link, docs/web-video.md) comes from the clipboard with Ctrl+Alt+U
or the panel; web_video.py relays the stream through ffmpeg into a pipe, and the renderer gets that pipe's read end. This supervisor checks the path is a readable regular file, opens it, probes the opened file with ffprobe
(never the path again, so the file cannot be swapped in between), detects the format, and hands the renderer that open
descriptor over the run's private control socket (SCM_RIGHTS). The renderer never sees or opens a path.

While a video plays, modal bare-key binds (VIDEO_BINDS: Space, arrows, Shift+arrows, Tab, Up/Down, Escape, Q) exist, as an
open placement panel's do; they are removed on exit, at run end, and by the bind helper if a key finds no run. The help
card closes and an open placement panel collapses when a video opens. Desktop view keys (zoom, switch, spacing, viewing
mode, the placement pills) are ignored while it plays; Ctrl+Alt+Space recenters the video, Ctrl+Alt+P
toggles prediction and Ctrl+Alt+H shows the controls card. Ctrl+Alt+C (view.PLAYBACK_MENU_OP) hides the controls card
for the rest of the run: it no longer pops up on open or on a play / seek / format key (Up / Down and Ctrl+Alt+H still
show it, and so does a playback error); pressed again, it shows the card and the pop-ups come back.

The controls card follows the OLED rules of the help card: dimmed (nothing above 191/255), never moved, and closed after
view.OVERLAY_IDLE_CLOSE_S seconds without a video key. VideoControl wraps view.ViewControl and passes everything that is
not about video through unchanged.

Quality menu (Q; quality.py): drawn on the controls card in place of its usual body. While it is open Up/Down pick a
setting, Left/Right (and Shift+Left/Right) change it, Tab moves to the next setting, Escape or Q closes it; Space still
plays / pauses. Every change is sent to the renderer at once (`quality` frame) and saved with the view settings
(layout.json or the glasses profile). The settings are sent after every open, so a new renderer gets them too. Its last
row is the background (environment.py: None, Grid), not a video setting: it stays behind the video, so it shows round a 180
degree or fisheye picture (a 360 degree one covers it), and behind the desktops once the video closes.
"""
import json
import os
from pathlib import Path
import queue
import re
import shutil
import stat
import subprocess
import threading

from . import environment, quality
from .view import (CONFIGRELOADED, MENU_OPS, OVERLAY_IDLE_CLOSE_S, PLAYBACK_MENU_OP, PLAYBACK_MENU_TOASTS, SAFE_PATH,
                   SAFE_TOKEN, _binds_lua, _remove_lua)
from .resize import RESIZE_OPS
from .send import SEND_OPS

# The quality menu's keys (quality.py; docs/vr-video.md#quality-menu): Q opens and closes it, Up / Down pick a setting
# while it is open (and show / hide the card, as before, while it is not).
QUALITY_OPS = ('video_quality', 'video_up', 'video_down')
VIDEO_OPS = ('video_open', 'video_pause', 'video_seek_back', 'video_seek_fwd', 'video_seek_back_big', 'video_seek_fwd_big',
             'video_format', 'video_controls', 'video_exit') + QUALITY_OPS
KEY_OPS = VIDEO_OPS[1:9]  # no payload: the panel's buttons (and binds); QUALITY_OPS are binds only
SEEK_S = {'video_seek_back': -10, 'video_seek_fwd': 10, 'video_seek_back_big': -60, 'video_seek_fwd_big': 60}
# Modal binds while a video plays (Hyprland Lua key strings). Ctrl+Alt+O (open) is a run bind in view.BINDS.
VIDEO_BINDS = (('space', 'video_pause'), ('left', 'video_seek_back'), ('right', 'video_seek_fwd'),
               ('SHIFT + left', 'video_seek_back_big'), ('SHIFT + right', 'video_seek_fwd_big'), ('Tab', 'video_format'),
               ('up', 'video_up'), ('down', 'video_down'), ('Escape', 'video_exit'), ('q', 'video_quality'))
# Desktop view keys that do nothing while a video plays (their effect would be invisible, and the panel's keys would
# clash): the view keys, the placement pills' keys (Ctrl+Alt+L and an open panel's arrows, Tab, R, Escape) and the
# overview free pointer's clicks (pill and corner drags, pill clicks, panel sliders). The video collapses an open panel
# when it opens, and the free pointer is off while it plays (free_pointer.OverviewPointer.overview), so these can only
# arrive from stale or racing binds. Send-window keys (Ctrl+Alt+G, 1/2/3, B) are ignored too: the desktops are hidden,
# so a window (and focus) would move somewhere unseen.
IGNORED_WHILE_PLAYING = (('zoom_in', 'zoom_out', 'next', 'prev', 'spacing_up', 'spacing_down', 'mode_next')
                         + MENU_OPS + RESIZE_OPS + SEND_OPS)

PROJECTIONS = ('360', '180', 'fisheye')
STEREO = ('mono', 'sbs', 'tb')
FOV_RANGE = (180, 220)  # fisheye lens field of view, degrees (spike/include/xr/video.hpp)
# Tab cycles through these, starting after the current format.
FORMAT_CYCLE = tuple({'projection': p, 'stereo': s, 'fov': f} for p, s, f in (
    ('360', 'mono', 180), ('360', 'tb', 180), ('360', 'sbs', 180), ('180', 'sbs', 180), ('180', 'tb', 180), ('180', 'mono', 180),
    ('fisheye', 'sbs', 180), ('fisheye', 'sbs', 190), ('fisheye', 'sbs', 200), ('fisheye', 'mono', 180)))
VIDEO_STATES = ('off', 'loading', 'playing', 'paused', 'ended', 'error')  # renderer status "video"
MAX_TITLE = 48          # ipc.hpp kMaxHelpText
MAX_PATH = 4096
MAX_SIDE = 16384        # larger frames are refused (beyond GL texture limits)
DISPLAY_SIDE = 8192     # larger frames are scaled down by the renderer (video.hpp kVideoMaxTextureSide)
# Hardware decode width limits of the AMD VCN 4 decoder in this machine (RX 7800 XT and the Ryzen 9000 iGPU); wider or
# other codecs decode in software, which may not keep up at 8K. Advisory only (warnings), never a refusal.
HW_DECODE_MAX_WIDTH = {'h264': 4096, 'hevc': 8192, 'vp9': 8192, 'av1': 8192}
ERROR_EXIT_S = 4        # a renderer playback error stays on the card this long, then the desktops come back
START_GRACE_S = 5       # no video state from the renderer this long after open: it cannot play video
FFPROBE_TIMEOUT_S = 10
FFPROBE_MAX_OUTPUT = 1 << 20


class VideoRejected(ValueError):
    """The file cannot be shown (not a video, too large, unsupported projection). The message is shown to the user."""


class FdFrame(bytes):
    """Renderer frames plus descriptors to send with them (SCM_RIGHTS); the supervisor closes them after sending."""
    def __new__(cls, data, fds):
        self = super().__new__(cls, data)
        self.fds = list(fds)
        return self

    def close_fds(self):
        for fd in self.fds:
            try: os.close(fd)
            except OSError: pass
        self.fds = []


# --- requests and renderer payloads ------------------------------------------------------------------------------------

def validate_format(fmt):
    if not isinstance(fmt, dict) or set(fmt) != {'projection', 'stereo', 'fov'}: raise ValueError('invalid video format')
    if fmt['projection'] not in PROJECTIONS or fmt['stereo'] not in STEREO: raise ValueError('invalid video format')
    if type(fmt['fov']) is not int or not FOV_RANGE[0] <= fmt['fov'] <= FOV_RANGE[1]: raise ValueError('invalid video format')
    return {'projection': fmt['projection'], 'stereo': fmt['stereo'], 'fov': fmt['fov']}


def validate_request(op, payload):
    """Control-socket payload of a video op (ipc.decode_control). video_open: {"path": absolute path[, "format": {...}]}."""
    if op not in VIDEO_OPS or not isinstance(payload, dict): raise ValueError('invalid video request')
    if op != 'video_open':
        if payload: raise ValueError('unexpected payload')
        return {}
    if set(payload) - {'path', 'url', 'web', 'format'} or len(set(payload) & {'path', 'url', 'web'}) != 1:
        raise ValueError('invalid video_open')
    if 'path' not in payload:  # a web video (web_video.py): its shape here; local addresses are refused when it opens
        from .web_video import web_request
        web_request(payload, allow_local=True)
        out = {k: payload[k] for k in ('url', 'web') if k in payload}
        if 'format' in payload: out['format'] = validate_format(payload['format'])
        return out
    path = payload['path']
    if not isinstance(path, str) or not path.startswith('/') or not 1 < len(path.encode('utf-8', 'surrogatepass')) <= MAX_PATH or '\0' in path:
        raise ValueError('video_open needs an absolute path')
    out = {'path': path}
    if 'format' in payload: out['format'] = validate_format(payload['format'])
    return out


def _title_ok(t):
    return isinstance(t, str) and 1 <= len(t) <= MAX_TITLE and all(0x20 <= ord(c) < 0x7f for c in t)


def validate_renderer_payload(p):
    """Mirror of the renderer's decoder (spike/src/video_ipc.cpp); tests run both over the same fixture."""
    if not isinstance(p, dict) or not isinstance(p.get('action'), str): raise ValueError('invalid video payload')
    a = p['action']
    keys = {'open': {'action', 'projection', 'stereo', 'fov', 'title'}, 'format': {'action', 'projection', 'stereo', 'fov'},
            'close': {'action'}, 'pause': {'action'}, 'seek': {'action', 'seconds'}, 'controls': {'action', 'visible'},
            'quality': {'action', *quality.KEYS}, 'menu': {'action', 'visible', 'selected', 'rows'}}
    if a not in keys or set(p) != keys[a]: raise ValueError('invalid video payload')
    if a == 'quality': quality.validate({k: p[k] for k in quality.KEYS})
    if a == 'menu': _validate_menu(p)
    if a in ('open', 'format'): validate_format({k: p[k] for k in ('projection', 'stereo', 'fov')})
    if a == 'open' and not _title_ok(p['title']): raise ValueError('invalid video title')
    if a == 'seek' and (type(p['seconds']) is not int or p['seconds'] == 0 or not -3600 <= p['seconds'] <= 3600):
        raise ValueError('invalid seek')
    if a == 'controls' and type(p['visible']) is not bool: raise ValueError('invalid controls')
    return p


MAX_MENU_ROWS = 8  # renderer: kMaxQualityRows


def _menu_text_ok(t):
    return isinstance(t, str) and 1 <= len(t) <= quality.MAX_LABEL and all(0x20 <= ord(c) < 0x7f for c in t)


def _validate_menu(p):
    rows = p['rows']
    if type(p['visible']) is not bool or not isinstance(rows, list) or not 1 <= len(rows) <= MAX_MENU_ROWS:
        raise ValueError('invalid quality menu')
    if any(not isinstance(r, dict) or set(r) != {'label', 'value'} or not _menu_text_ok(r['label'])
           or not _menu_text_ok(r['value']) for r in rows):
        raise ValueError('invalid quality menu')
    if type(p['selected']) is not int or not 0 <= p['selected'] < len(rows): raise ValueError('invalid quality menu')


def video_frame(run_id, session, request_id, payload):
    validate_renderer_payload(payload)
    return (json.dumps({'schema': 1, 'request_id': request_id, 'run_id': run_id, 'session': session, 'op': 'video',
                        'payload': payload}, separators=(',', ':')) + '\n').encode()


def ascii_title(path):
    """The file name as the card shows it: printable ASCII only (the pixel font), at most MAX_TITLE characters."""
    return ascii_text(Path(str(path)).name)


def ascii_text(name):
    text = re.sub(r'\s+', ' ', ''.join(c if 0x20 <= ord(c) < 0x7f else '?' for c in name)).strip()
    if len(text) > MAX_TITLE: text = text[:MAX_TITLE - 3].rstrip() + '...'
    return text or 'VR video'


def format_label(fmt):
    head = {'360': '360°', '180': '180°', 'fisheye': f"Fisheye {fmt['fov']}°"}[fmt['projection']]
    return head + {'mono': ' mono', 'sbs': ' side-by-side 3D', 'tb': ' top-bottom 3D'}[fmt['stereo']]


def next_format(fmt):
    try: i = FORMAT_CYCLE.index(fmt)
    except ValueError: i = -1
    return dict(FORMAT_CYCLE[(i + 1) % len(FORMAT_CYCLE)])


# --- the file: open, probe, detect -------------------------------------------------------------------------------------

def open_video_file(path):
    """Read-only descriptor for a regular file (no FIFOs, devices or directories). Raises VideoRejected or OSError."""
    if not isinstance(path, str) or not path.startswith('/'): raise VideoRejected('needs an absolute path')
    try:
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOCTTY | os.O_NONBLOCK)
    except FileNotFoundError: raise VideoRejected('file not found') from None
    except PermissionError: raise VideoRejected('file not readable') from None
    except IsADirectoryError: raise VideoRejected('that is a folder, not a video file') from None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode): raise VideoRejected('not a regular file')
        if st.st_size == 0: raise VideoRejected('the file is empty')
        os.set_blocking(fd, True)
        return fd
    except BaseException:
        os.close(fd); raise


def ffprobe_path():
    return '/usr/bin/ffprobe' if os.access('/usr/bin/ffprobe', os.X_OK) else shutil.which('ffprobe')


def probe_fd(fd, *, ffprobe=None, run=subprocess.run, timeout=FFPROBE_TIMEOUT_S):
    """ffprobe the already opened file (/proc/self/fd in the child, file protocol only). None if ffprobe is missing."""
    exe = ffprobe if ffprobe is not None else ffprobe_path()
    if not exe: return None
    argv = [exe, '-v', 'error', '-hide_banner', '-protocol_whitelist', 'file', '-print_format', 'json', '-show_streams',
            '-show_format', f'file:/proc/self/fd/{fd}']
    try:
        r = run(argv, pass_fds=(fd,), stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout, check=False,
                env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'})
    except subprocess.TimeoutExpired: raise VideoRejected('the file took too long to read') from None
    except OSError: return None
    if r.returncode != 0 or len(r.stdout) > FFPROBE_MAX_OUTPUT: raise VideoRejected('not a playable video file')
    try: data = json.loads(r.stdout)
    except ValueError: raise VideoRejected('not a playable video file') from None
    return parse_probe(data)


def _int(v):
    try: return int(v)
    except (TypeError, ValueError): return None


def _rate(v):
    try:
        n, _, d = str(v).partition('/')
        n, d = float(n), float(d or 1)
        return n / d if d else None
    except ValueError: return None


STEREO_TYPES = {'side by side': 'sbs', 'top and bottom': 'tb', '2d': 'mono', 'sidebyside': 'sbs', 'topbottom': 'tb'}
MKV_STEREO = {'left_right': 'sbs', 'right_left': 'sbs', 'top_bottom': 'tb', 'bottom_top': 'tb', 'mono': 'mono',
              'block_lr': 'sbs', 'block_rl': 'sbs'}


def parse_probe(data):
    """ffprobe JSON -> {width, height, codec, fps, duration, projection, coverage, stereo}; projection/stereo None if absent."""
    if not isinstance(data, dict): raise VideoRejected('not a playable video file')
    streams = [s for s in data.get('streams') or [] if isinstance(s, dict) and s.get('codec_type') == 'video'
               and not (s.get('disposition') or {}).get('attached_pic')]
    if not streams: raise VideoRejected('no video stream in the file')
    v = streams[0]
    w, h = _int(v.get('width')), _int(v.get('height'))
    if not w or not h or w <= 0 or h <= 0: raise VideoRejected('the video has no picture size')
    info = {'width': w, 'height': h, 'codec': str(v.get('codec_name') or 'unknown')[:32],
            'fps': _rate(v.get('avg_frame_rate')) or _rate(v.get('r_frame_rate')),
            'duration': _rate((data.get('format') or {}).get('duration')), 'projection': None, 'coverage': None, 'stereo': None}
    for side in v.get('side_data_list') or []:
        if not isinstance(side, dict): continue
        kind = side.get('side_data_type')
        if kind == 'Spherical Mapping':
            projection = str(side.get('projection', '')).lower()
            info['projection'] = projection
            left, right = _int(side.get('bound_left')) or 0, _int(side.get('bound_right')) or 0
            if left or right: info['coverage'] = 360 * max(0.0, 1 - (left + right) / 2 ** 32)
        elif kind == 'Stereo 3D':
            info['stereo'] = STEREO_TYPES.get(str(side.get('type', '')).lower())
    tag = str((v.get('tags') or {}).get('stereo_mode', '')).lower()
    if info['stereo'] is None and tag in MKV_STEREO: info['stereo'] = MKV_STEREO[tag]
    return info


def _near(a, b): return abs(a - b) <= 0.08 * b


def detect_format(name, info):
    """(format, source): source 'metadata' (spherical/stereo tags in the file), 'filename', 'shape' or 'default'.

    File metadata wins, then file-name conventions (_360, _180/VR180, _LR/_SBS/_3dh, _TB/_OU/_3dv, _FISHEYE190, _MKX200,
    _RF52), then the picture's shape (2:1 -> 360° mono, 1:1 -> 360° top-bottom, 4:1 -> 360° side-by-side), else 360° mono.
    """
    info = info or {}
    projection = stereo = fov = None
    sources = set()
    meta = info.get('projection')
    if meta:
        if meta in ('cubemap', 'mesh', 'rectilinear'):
            raise VideoRejected(f'{meta} projection is not supported (equirectangular and fisheye only)')
        if meta == 'fisheye': projection = 'fisheye'
        elif meta == 'half equirectangular': projection = '180'
        elif 'equirectangular' in meta:
            coverage = info.get('coverage')
            projection = '180' if coverage is not None and coverage <= 200 else '360'
        if projection: sources.add('metadata')
    if info.get('stereo'):
        stereo = info['stereo']; sources.add('metadata')
    tokens = set(re.split(r'[^a-z0-9]+', str(name).lower()))
    lens = {'fisheye': 180, 'fisheye180': 180, 'fisheye190': 190, 'fisheye200': 200, 'mkx200': 200, 'mkx220': 220,
            'rf52': 190, 'vrca220': 220}
    if projection is None:
        hit = next((t for t in sorted(tokens) if t in lens), None)
        if hit: projection, fov = 'fisheye', lens[hit]
        elif tokens & {'360', 'vr360', '360x180', '360vr', 'mono360', 'tb360', 'sbs360'}: projection = '360'
        elif tokens & {'180', 'vr180', '180x180', '180vr', '180sbs', '180lr', '180tb'}: projection = '180'
        if projection: sources.add('filename')
    if stereo is None:
        if tokens & {'sbs', 'lr', 'rl', '3dh', 'leftright', 'halfsbs', 'hsbs', '180sbs', '180lr', 'sbs360'}: stereo = 'sbs'
        elif tokens & {'tb', 'ou', 'ab', '3dv', 'tab', 'topbottom', 'overunder', 'htab', '180tb', 'tb360'}: stereo = 'tb'
        elif tokens & {'mono', '2d', 'mono360'}: stereo = 'mono'
        if stereo: sources.add('filename')
    w, h = info.get('width'), info.get('height')
    ratio = w / h if w and h else None
    if ratio and (projection is None or stereo is None):
        shaped = False
        if projection is None and stereo is not None:
            eye = ratio / 2 if stereo == 'sbs' else ratio * 2 if stereo == 'tb' else ratio
            projection = '360' if _near(eye, 2) else '180' if _near(eye, 1) else None
            shaped = projection is not None
        elif projection in ('180', 'fisheye') and stereo is None:
            stereo = 'sbs' if _near(ratio, 2) else 'tb' if _near(ratio, 0.5) else 'mono' if _near(ratio, 1) else None
            shaped = stereo is not None
        elif projection == '360' and stereo is None:
            stereo = 'mono' if _near(ratio, 2) else 'tb' if _near(ratio, 1) else 'sbs' if _near(ratio, 4) else None
            shaped = stereo is not None
        elif projection is None and stereo is None:
            guess = {'mono': _near(ratio, 2), 'tb': _near(ratio, 1), 'sbs': _near(ratio, 4)}
            stereo = next((k for k, hit in guess.items() if hit), None)
            projection = '360' if stereo else None
            shaped = stereo is not None
        if shaped: sources.add('shape')
    if projection is None or stereo is None: sources.add('default')
    fmt = {'projection': projection or '360', 'stereo': stereo or 'mono', 'fov': fov or 180}
    source = next(s for s in ('default', 'shape', 'filename', 'metadata') if s in sources) if sources else 'default'
    return fmt, source


def check_limits(info):
    """Refuse what cannot be shown; return warnings (shown in the panel) for what may play badly."""
    if not info: return ['format not checked (ffprobe unavailable)']
    w, h, codec = info['width'], info['height'], info['codec']
    if max(w, h) > MAX_SIDE: raise VideoRejected(f'{w}x{h} is larger than {MAX_SIDE} px')
    warnings = []
    if max(w, h) > DISPLAY_SIDE: warnings.append(f'{w}x{h} is shown scaled down to {DISPLAY_SIDE} px')
    limit = HW_DECODE_MAX_WIDTH.get(codec)
    if limit is None or w > limit:
        warnings.append(f'{codec} {w}x{h} decodes in software here and may stutter')
    fps = info.get('fps')
    if fps and fps > 61 and w * h > 3840 * 2160: warnings.append(f'{fps:.0f} fps above 4K may drop frames')
    return warnings


def _quiet(fn, *args):
    try: fn(*args)
    except (OSError, ValueError, subprocess.SubprocessError): pass


def notify_send(text, *, run=subprocess.run):
    """A desktop notification (mako on Omarchy). The focused desktop is in the glasses, so Cody sees it there."""
    exe = '/usr/bin/notify-send'
    if not os.access(exe, os.X_OK): return
    run([exe, '--app-name=XR Workspace', '--expire-time=8000', 'VR video', str(text)[:300]], stdin=subprocess.DEVNULL,
        capture_output=True, timeout=5, check=False)


# --- Hyprland binds ----------------------------------------------------------------------------------------------------

def install_video_binds_lua(python, client, control_socket, run_id, session):
    """Modal video keys (bare Space/arrows/Tab/Escape) while a video plays; replaces any surviving set, never doubled."""
    for p in (python, client, control_socket):
        if not isinstance(p, str) or not SAFE_PATH.match(p): raise ValueError('unsafe path for bind command')
    for t in (run_id, session):
        if not isinstance(t, str) or not SAFE_TOKEN.match(t): raise ValueError('unsafe token for bind command')
    return remove_video_binds_lua() + ' ' + _binds_lua('xr_workspace_video_binds', VIDEO_BINDS,
                                                       f'{python} -I {client} {control_socket} {run_id} {session}')


def remove_video_binds_lua():
    return _remove_lua('xr_workspace_video_binds')


# --- supervisor-side control -------------------------------------------------------------------------------------------

class VideoControl:
    """view.ViewControl plus VR video. Owns the video state the renderer shows; everything else is passed through.

    `binds`: optional (on: bool) -> None installing/removing VIDEO_BINDS (a compositor call, run off the supervisor loop).
    `probe`: (fd) -> info dict or None (probe_fd). `threads=False` runs file checks inline (tests).
    Web videos (web_video.py): `web_probe` (url, headers, ua) -> info, `web_resolve` (page url) -> yt-dlp info,
    `relay_factory` (argv) -> StreamRelay,
    `allow_local` (tests only: local test servers). `notify`: optional (text) -> None telling Cody why an open was
    refused while he is in the glasses (a desktop notification), run off the supervisor loop.
    """
    def __init__(self, view_control, *, binds=None, probe=probe_fd, threads=True, clock=None, web_probe=None,
                 relay_factory=None, allow_local=False, notify=None, web_resolve=None):
        self.view = view_control
        self.clock = clock or view_control.clock
        self.binds, self.probe, self.threads = binds, probe, threads
        self.web_probe, self.relay_factory, self.allow_local, self.notify = web_probe, relay_factory, allow_local, notify
        self.web_resolve = web_resolve  # (page url) -> yt-dlp info (web_video.resolve_page); tests pass a stand-in
        self.stream = None   # web_video.WebStream while a web video plays
        self.origin = None   # 'file' or 'web'
        self.opening = False
        self.last_poll = None
        self.stream_retries = 0
        self.active = False
        self.title = self.format = self.source = None
        self.warnings, self.error = [], None
        self.controls = False
        self.menu_hidden = False  # Ctrl+Alt+C: the controls card stays hidden unless asked for (view.PLAYBACK_MENU_OP)
        self.menu, self.menu_row = False, 0  # the quality menu on the card (quality.py); never saved, starts closed
        self.last_input = self.opened_at = self.error_seen_at = None
        self.supervisor = None
        self.opens = 0
        self._results = queue.SimpleQueue()
        self._worker = None
        self._bind_lock = threading.Lock()
        self._binds_installed = False
        self._bind_threads = []

    def __getattr__(self, name):  # server, events, flush, stop_menu_binds, join_focus, ... are the view control's
        if name == 'view': raise AttributeError(name)
        return getattr(self.view, name)

    def attach_supervisor(self, supervisor):
        self.supervisor = supervisor  # for the renderer's measured status (its "video" state)
        attach = getattr(self.view, 'attach_supervisor', None)  # the view's desktop row pauses / replaces the run's guard
        if callable(attach): attach(supervisor)

    def initial_frame(self):
        return self.view.initial_frame()

    # frames
    def _frame(self, payload):
        frame = video_frame(self.view.run_id, self.view.session, self.view.next_id, payload)
        self.view.next_id += 1
        return frame

    def _controls_frame(self, visible):
        if visible == self.controls: return b''
        out = self._menu_frame(False) if not visible else b''  # the menu is on the card: hiding the card closes it
        self.controls = visible
        return out + self._frame({'action': 'controls', 'visible': visible})

    # quality menu (quality.py)
    def quality(self):
        """The current video quality settings: the accessor over the view state, which saves them."""
        return quality.video_quality(self.view.state)

    def _menu_frame(self, visible):
        if not visible and not self.menu: return b''
        self.menu = visible
        return self._frame(quality.menu_payload(self.quality(), visible, self.menu_row, self._menu_extra()))

    def _menu_extra(self):
        """The quality menu's rows after the video settings: the background (environment.py)."""
        return [environment.menu_row(environment.environment(self.view.state))]

    def _quality_frame(self):
        return self._frame(quality.renderer_payload(self.quality()))

    def _set_quality(self, settings):
        """Apply live (renderer) and save with the view settings (layout.json / the glasses profile)."""
        settings = quality.validate(settings)
        if settings == self.quality(): return b''
        self.view.state.video_quality = settings
        try: self.view.settings.save(self.view.state.settings())
        except (OSError, ValueError): pass  # best effort, like the other view settings: the run keeps going
        return self._quality_frame()

    def _on_menu_key(self, op):
        """A video key while the quality menu is open; None if it is not a menu key (handled as usual)."""
        rows = len(quality.SETTINGS) + len(self._menu_extra())
        if op in ('video_quality', 'video_exit'): return self._menu_frame(False)
        if op in ('video_up', 'video_down', 'video_format'):
            self.menu_row = (self.menu_row + (-1 if op == 'video_up' else 1)) % rows
            return self._menu_frame(True)
        if op in SEEK_S:
            delta = -1 if SEEK_S[op] < 0 else 1
            if self.menu_row < len(quality.SETTINGS):
                out = self._set_quality(quality.step(self.quality(), self.menu_row, delta))
            else:  # the background's row: applied and saved by the view control
                out = self.view.set_environment(environment.stepped(environment.environment(self.view.state), delta))
            return out + self._menu_frame(True)
        return None

    def _hide_if_hidden(self):
        """After an open frame (the renderer shows the card on open): hide it again when the menu is hidden."""
        return self._controls_frame(False) if self.menu_hidden else b''

    def _menu_toggle(self):
        """Ctrl+Alt+C: the playback menu hidden / shown, with a toast. Shown while a video plays: the card appears now."""
        if getattr(self.view.state, 'typing', False): return None  # its bind is gone in typing mode: a stale key
        self.menu_hidden = not self.menu_hidden
        self.view._tell(PLAYBACK_MENU_TOASTS[self.menu_hidden])
        if not self.active: return None
        self.last_input = self.clock()
        return self._controls_frame(not self.menu_hidden) or None

    # control socket
    def on_message(self, message):
        op = message.get('op') if isinstance(message, dict) else None
        if op == PLAYBACK_MENU_OP: return self._menu_toggle()
        if op in VIDEO_OPS:
            self.view.last_input = self.last_input = self.clock()
            return self._on_video(op, message.get('payload') or {})
        if op == 'typing_toggle':  # view.TYPING_OP: typing mode releases the video's bare keys too, and gives them back
            out = self.view.on_message(message)
            self._sync_binds()
            return out
        if self.active:
            if op == 'help':  # Ctrl+Alt+H: the video controls instead of the desktop help card
                self.last_input = self.clock()
                return self._controls_frame(not self.controls) or None
            if op in IGNORED_WHILE_PLAYING: return None
        return self.view.on_message(message)

    def _on_video(self, op, payload):
        if op == 'video_open':
            if self._worker is not None and self._worker.is_alive():
                self.error = 'still opening the previous video'; return None
            chosen = payload.get('format')
            if 'path' in payload: target, args = self._prepare, (payload.get('path'), chosen)
            else: target, args = self._prepare_web, ({k: payload[k] for k in ('url', 'web') if k in payload}, chosen)
            self.opening, self.error = True, None
            if self.threads:
                self._worker = threading.Thread(target=target, args=args, daemon=True)
                self._worker.start()
            else:
                target(*args)
            return None  # poll() sends the open frame with the descriptor
        if not self.active: return None
        if self.menu:
            out = self._on_menu_key(op)
            if out is not None: return self._controls_frame(True) + out or None
        if op == 'video_quality': return (self._controls_frame(True) + self._menu_frame(True)) or None
        if op == 'video_exit': return self._close() or None
        if op in ('video_controls', 'video_up', 'video_down'): return self._controls_frame(not self.controls) or None
        out = b'' if self.menu_hidden else self._controls_frame(True)
        if op == 'video_pause': out += self._frame({'action': 'pause'})
        elif op in SEEK_S and self.stream is not None:  # a pipe cannot seek: poll() restarts the relay once the keys settle
            if not self.stream.request_seek(SEEK_S[op], self.clock()): self.error = 'a live stream cannot seek'
        elif op in SEEK_S: out += self._frame({'action': 'seek', 'seconds': SEEK_S[op]})
        elif op == 'video_format':
            self.format, self.source = next_format(self.format), 'chosen'
            out += self._frame({'action': 'format', **self.format})
        return out or None

    def _prepare(self, path, chosen):
        """Worker: open, probe and detect. Result (or the refusal) goes to poll() on the supervisor loop."""
        try:
            fd = open_video_file(path)
            try:
                info = self.probe(fd)
                fmt, source = detect_format(Path(path).name, info)
                if chosen: fmt, source = validate_format(chosen), 'chosen'
                warnings = check_limits(info)
            except BaseException:
                os.close(fd); raise
            self._results.put(('open', fd, ascii_title(path), fmt, source, warnings, None))
        except (VideoRejected, OSError, ValueError) as exc:
            self._results.put(('refused', str(exc) or 'cannot open the video'))

    def _prepare_web(self, payload, chosen):
        """Worker: check the web request, pick the source, probe it over the network, start the relay (web_video.py)."""
        from . import web_video as wv
        stream = None
        try:
            web = wv.web_request(payload, allow_local=self.allow_local)
            src = wv.choose_source(web)
            fmt, source = wv.dl8_format(src['format'] or web['format'], web['fov']), 'player'
            # A stream link is probed as is; a video page link is resolved with yt-dlp first (web_video.prepare_target).
            target, info = wv.prepare_target(web, src, probe=self.web_probe, resolve=self.web_resolve, allow_local=self.allow_local)
            if fmt is None:
                fmt, source = detect_format(target['hint'], info)
                if source == 'filename' and target['via'] == 'yt-dlp': source = 'title'  # the page's title and tags
            if chosen: fmt, source = validate_format(chosen), 'chosen'
            warnings = check_limits(info)
            stream = wv.WebStream(target, info, relay_factory=self.relay_factory)
            if not stream.seekable: warnings.append('live stream: no seeking')
            start = web['start'] if web['start'] >= 2 and (not stream.duration or web['start'] < stream.duration - 5) else 0.0
            fd = stream.start(start)
            self._results.put(('open', fd, ascii_text(target['title']), fmt, source, warnings, stream))
        except (VideoRejected, OSError, ValueError) as exc:
            if stream is not None: stream.stop()
            self._results.put(('refused', str(exc) or 'cannot open the web video'))

    def _open(self, fd, title, fmt, source, warnings, stream=None):
        out = b''
        if self.view.state.menu_open: out += self.view._on_menu('menu_toggle') or b''  # collapses: saves, releases the keys
        if self.view.help_visible: out += self.view._help_toggle()
        if self.stream is not None and self.stream is not stream: self.stream.stop()
        self.stream, self.origin, self.opening, self.stream_retries = stream, 'web' if stream else 'file', False, 0
        self.title = title
        out += self._frame({'action': 'open', **fmt, 'title': self.title})
        out += self._quality_frame()  # the renderer applies it to this and later videos; its menu starts closed
        self.menu = False
        now = self.clock()
        self.active, self.format, self.source, self.warnings, self.error = True, fmt, source, list(warnings), None
        self.view.video_active = True  # free pointer off (its sampler stops reading the cursor; binds removed, cursor shown)
        self.controls = True  # the renderer shows the card on open
        out += self._hide_if_hidden()
        self.last_input = self.opened_at = now
        self.error_seen_at = None
        self.opens += 1
        self._sync_binds()
        return FdFrame(out, [fd])

    def _restart_stream(self, at, now):
        """Seek or resume a web video: a new relay from `at`, handed to the renderer as a fresh open (same format)."""
        try: fd = self.stream.start(at)
        except (VideoRejected, OSError, ValueError) as exc:
            return self._close(str(exc) or 'the stream could not be restarted')
        self.controls, self.menu = True, False  # the renderer shows the card on open, its menu closed; quality is kept
        self.opened_at, self.error_seen_at, self.last_input = now, None, now
        out = self._frame({'action': 'open', **self.format, 'title': self.title})
        return FdFrame(out + self._hide_if_hidden(), [fd])

    def _close(self, error=None):
        if self.stream is not None: self.stream.stop(); self.stream = None
        if not self.active: return b''
        self.active, self.controls, self.menu = False, False, False  # the renderer's close resets its card and menu
        self.view.video_active = False
        if error: self.error = error
        self._sync_binds()
        return self._frame({'action': 'close'})

    def poll(self, now=None):
        now = self.clock() if now is None else now
        out = self.view.poll(now) or b''
        fds = []
        while True:
            try: result = self._results.get_nowait()
            except queue.Empty: break
            if result[0] == 'open':
                if self.supervisor is not None and self.supervisor.stopping_at is not None:
                    os.close(result[1])
                    if result[-1] is not None: result[-1].stop()
                    continue
                frame = self._open(*result[1:])
                out += bytes(frame); fds += frame.fds
            else:
                self.opening, self.error = False, result[1]
                self._tell(result[1])
        dt = now - self.last_poll if self.last_poll is not None else 0.0
        self.last_poll = now
        if self.active and self.stream is not None:
            state = self.renderer_state()
            self.stream.advance(dt, state)
            target = self.stream.due_seek(now)
            failure = self.stream.failure()
            frame = None
            if target is not None: frame = self._restart_stream(target, now)
            elif failure and state in ('ended', 'error'):
                # The connection broke for good (ffmpeg's own reconnects failed): resume once or twice, then give up.
                if self.stream_retries < 2 and self.stream.position() > 0:
                    self.stream_retries += 1
                    frame = self._restart_stream(self.stream.position(), now)
                else:
                    self.error = failure; self._tell(failure)
                    out += self._close(failure)
            if frame:
                out += bytes(frame); fds += getattr(frame, 'fds', [])
        if self.active:
            if self.controls and now - self.last_input >= OVERLAY_IDLE_CLOSE_S: out += self._controls_frame(False)
            state = self.renderer_state()
            if state == 'error':
                if self.error_seen_at is None:
                    self.error_seen_at = now
                    self.error = 'the renderer could not play this video'
                    out += self._controls_frame(True)  # the card says why
                elif now - self.error_seen_at >= ERROR_EXIT_S:
                    out += self._close()
            else:
                self.error_seen_at = None
                if state in (None, 'off') and now - self.opened_at >= START_GRACE_S:
                    out += self._close('video playback is not available in this session (renderer without video support)')
        if fds: return FdFrame(out, fds)
        return out or None

    def renderer_state(self):
        status = getattr(self.supervisor, 'status', None)
        state = status.get('video') if isinstance(status, dict) else None
        return state if state in VIDEO_STATES else None

    def on_events(self, data):
        out = self.view.on_events(data)
        if self.active and CONFIGRELOADED.search(data.decode('utf-8', errors='replace')):
            self._binds_installed = False  # a reload drops runtime binds
            self._sync_binds(force=True)
        return out

    def _tell(self, text):
        """Best effort: say why an open failed where Cody looks (a desktop notification), off the supervisor loop."""
        if self.notify is None: return
        threading.Thread(target=lambda: _quiet(self.notify, text), daemon=True).start()

    def snapshot(self):
        """Panel state ('video' in the status reply). `stream`: web videos only (web_video.WebStream.snapshot)."""
        state = self.renderer_state() if self.active else 'off'
        return {'active': self.active, 'title': self.title, 'format': dict(self.format) if self.format else None,
                'label': format_label(self.format) if self.format else None, 'source': self.source,
                'state': state or ('loading' if self.active else 'off'), 'controls': self.controls,
                'menu_hidden': self.menu_hidden,
                'warnings': list(self.warnings), 'error': self.error, 'ops': list(KEY_OPS), 'opening': self.opening,
                'origin': self.origin if self.active else None,
                'quality': self.quality(), 'quality_menu': self.menu and self.active,
                'stream': self.stream.snapshot() if self.active and self.stream is not None else None}

    def status_reply(self, status):
        reply = self.view.status_reply(status)
        reply['video'] = self.snapshot()
        return reply

    # binds (compositor calls: never on the supervisor loop)
    def _sync_binds(self, force=False):
        if self.binds is None: return
        t = threading.Thread(target=self._bind_work, args=(force,), daemon=True); t.start()
        self._bind_threads.append(t)

    def _bind_work(self, force):
        with self._bind_lock:
            binds, want = self.binds, self.active and not getattr(self.view.state, 'typing', False)
            if binds is None or (want == self._binds_installed and not (force and want)): return
            try: binds(want)
            except (OSError, ValueError, TypeError, KeyError): return  # best effort; Ctrl+Alt+O/stop still work
            self._binds_installed = want

    def close(self, timeout=2.5):
        """Run end: no later bind change; remove the video keys if they are up; close descriptors never sent."""
        import time
        deadline = time.monotonic() + timeout
        for t in self._bind_threads: t.join(max(0.0, deadline - time.monotonic()))
        binds, self.binds = self.binds, None
        with self._bind_lock:
            if binds is not None and self._binds_installed:
                try: binds(False)
                except (OSError, ValueError, TypeError, KeyError): pass
                self._binds_installed = False
        if self._worker is not None: self._worker.join(max(0.0, deadline - time.monotonic()))
        if self.stream is not None: self.stream.stop(); self.stream = None
        while True:
            try: result = self._results.get_nowait()
            except queue.Empty: break
            if result[0] == 'open':
                os.close(result[1])
                if result[-1] is not None: result[-1].stop()
