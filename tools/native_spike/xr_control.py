"""Bind helper: send one view operation to a running managed capture run. Stdlib only, run with -I.

usage: python3 -I xr_control.py CONTROL_SOCKET RUN_ID SESSION OP
Exits quietly with status 1 if no run is listening (binds only exist during a run anyway).
video_open (Ctrl+Alt+O) first leaves any playing video (video_exit, so the desktops and the picker are visible in the
glasses), then shows a file picker (zenity) on the focused desktop and sends the chosen path; a cancelled picker sends
nothing more. The run checks and opens the file itself (video.py).
video_paste (Ctrl+Alt+U) reads the clipboard once (wl-paste): an http(s) video or video page link, and sends it as
video_open. The run checks it, resolves a page link with yt-dlp and relays the stream (web_video.py, docs/web-video.md).
If the clipboard holds no link, a desktop notification says so and nothing is sent.

Start-up cost is input latency: every bound key, and every overview click (resize_press / resize_release), starts this
helper. The common path imports nothing beyond the interpreter's own start-up modules and _socket (json, re and socket
would more than double its start-up); an empty-payload frame is written directly, byte for byte what json.dumps writes.
json, re, socket and struct are imported only on the paths that need them (a payload, the clipboard, a stale cleanup).
"""
import _socket
import os
import sys
import time

# Legacy, cleanup only: the menu "mouse mode" of builds before the glass pills bound Ctrl+Alt+M and the left button to
# these. The run no longer accepts them (ipc.decode_control); they stay in OPS so a bind left behind by an older run that
# died in that mode still runs, finds no run listening and removes itself (drop_stale_menu_binds: cursor shown again).
LEGACY_OPS = ('menu_mouse', 'menu_mouse_press', 'menu_mouse_release')
OPS = ('zoom_in', 'zoom_out', 'next', 'prev', 'spacing_up', 'spacing_down', 'recenter', 'mode_next',
       'prediction', 'help', 'menu_toggle', 'menu_desktop_next', 'menu_desktop_prev', 'menu_field_next', 'menu_field_prev',
       'menu_inc', 'menu_dec', 'menu_inc_big', 'menu_dec_big', 'menu_reset', 'menu_back', 'menu_ultrawide', 'menu_add',
       'menu_delete', 'resize_press', 'resize_release',
       'send_glasses', 'send_glasses_1', 'send_glasses_2', 'send_glasses_3', 'send_glasses_4', 'send_back', 'typing_toggle',
       'help_prev', 'help_next', 'privacy_display', 'playback_menu_toggle'
       ) + LEGACY_OPS  # view.USER_OPS + MENU_OPS + resize.RESIZE_OPS + send.SEND_OPS + view.TYPING_OP + HELP_PAGE_OPS
                       # + privacy.PRIVACY_OP + view.PLAYBACK_MENU_OP + LEGACY_OPS
# VR video ops (video.VIDEO_OPS); duplicated because this helper is stdlib-only under -I (tests compare).
VIDEO_OPS = ('video_open', 'video_pause', 'video_seek_back', 'video_seek_fwd', 'video_seek_back_big', 'video_seek_fwd_big',
             'video_format', 'video_controls', 'video_exit', 'video_quality', 'video_up', 'video_down')
# Same text as video.remove_video_binds_lua() (tests compare): a video key with no run listening gives the keys back.
REMOVE_VIDEO_BINDS = ('for _, b in ipairs(xr_workspace_video_binds or {}) do '
                      'if tostring(b) ~= "HL.Keybind(expired)" then b:remove() end end xr_workspace_video_binds = nil')
# Ctrl+Alt+D (privacy.PRIVACY_OP) with no run listening: the run died with the main display off. Every monitor's DPMS on
# (fixed Lua, the same text as privacy.WAKE_ALL_LUA; tests compare).
WAKE_ALL_DISPLAYS = 'hl.dispatch(hl.dsp.dpms({action="on"}))'
HELPER_OPS = ('video_paste',)  # handled here: becomes video_open with the clipboard's web video
CANCEL_OP = 'resize_cancel'  # resize.CANCEL_OP (tests compare): Super went down while the left button is the run's
MAX_CLIPBOARD = 16384  # web_video.MAX_CLIPBOARD (the control frame limit)
VIDEO_FILTER = 'Videos | *.mp4 *.MP4 *.mkv *.MKV *.webm *.mov *.MOV *.m4v *.avi *.ts *.m2ts'
# TOKEN and INSTANCE stay module attributes (compiled on first use, __getattr__ below); the key path checks tokens with
# _is_token, which accepts exactly what TOKEN.match accepts.
_PATTERNS = {'TOKEN': r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z', 'INSTANCE': r'[0-9a-f]{40}_[0-9]+_[0-9]+\Z'}
_ALNUM = frozenset('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789')
_TOKEN_CHARS = _ALNUM | frozenset('_.:-')  # also exactly the characters json.dumps writes unescaped and unchanged


def _pattern(name):
    value = globals().get(name)
    if value is None:
        import re
        value = globals()[name] = re.compile(_PATTERNS[name])
    return value


def __getattr__(name):
    if name in _PATTERNS: return _pattern(name)
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')


def _is_token(value):
    """TOKEN.match(value) for a string, without re: one leading letter or digit, then up to 127 of [A-Za-z0-9_.:-]."""
    return 0 < len(value) <= 128 and value[0] in _ALNUM and _TOKEN_CHARS.issuperset(value)
# Same text as view.remove_menu_binds_lua() (tests compare); duplicated because this helper is stdlib-only under -I.
# Its tail is the legacy mouse-mode cleanup (view._LEGACY_MOUSE_CLEANUP_LUA): an older run's left-button binds go and the
# cursor is shown and put back; a no-op when there are none.
REMOVE_MENU_BINDS = ('for _, b in ipairs(xr_workspace_menu_binds or {}) do '
                     'if tostring(b) ~= "HL.Keybind(expired)" then b:remove() end end xr_workspace_menu_binds = nil '
                     'for _, b in ipairs(xr_workspace_mouse_binds or {}) do '
                     'if tostring(b) ~= "HL.Keybind(expired)" then b:remove() end end xr_workspace_mouse_binds = nil '
                     'do local s = xr_workspace_mouse xr_workspace_mouse = nil if s then '
                     'pcall(hl.config, { cursor = { invisible = s.invisible } }) '
                     'if s.home then pcall(function() hl.dispatch(hl.dsp.cursor.move({ x = s.home.x, y = s.home.y })) end) end '
                     'end end')
# Same text as resize.remove_resize_binds_lua() (tests compare). A left click with no run listening must not stay bound,
# and a cursor hidden by a pill drag must come back.
REMOVE_RESIZE_BINDS = ('for _, b in ipairs(xr_workspace_resize_binds or {}) do '
                       'if tostring(b) ~= "HL.Keybind(expired)" then b:remove() end end xr_workspace_resize_binds = nil '
                       # resize.GRAB_LEAVE_LUA: a run that died mid pill-drag left the cursor hidden; show it and put it back
                       'do local s = xr_workspace_grab xr_workspace_grab = nil if s then '
                       'pcall(hl.config, { cursor = { invisible = s.invisible } }) '
                       'if s.home then pcall(function() hl.dispatch(hl.dsp.cursor.move({ x = s.home.x, y = s.home.y })) end) end '
                       'end end')
# Same text as overview_cursor.SHOW_LUA (tests compare): the real cursor a dead run hid in the overview comes back. Sent
# after every stale cleanup's own code below; after REMOVE_RESIZE_BINDS, whose grab record still says hidden.
SHOW_OVERVIEW_CURSOR = ('do local s = xr_workspace_overview_cursor xr_workspace_overview_cursor = nil if s then '
                        'if xr_workspace_grab then xr_workspace_grab.invisible = s.invisible '
                        'else pcall(hl.config, { cursor = { invisible = s.invisible } }) end end end')


def drop_stale_menu_binds(environ=os.environ, code=REMOVE_MENU_BINDS):
    """A panel key with no run listening: the supervisor died with a placement panel open. Remove its modal binds so the
    arrows, Tab, R and Escape work again (and an older build's mouse-mode left button and hidden cursor, LEGACY_OPS).
    Fixed Lua only, on the compositor that ran this bind (its own environment). Every cleanup (`code`, the left
    button's too) also shows a cursor the dead run hid in the overview (SHOW_OVERVIEW_CURSOR)."""
    return _eval_stale(environ, code + ' ' + SHOW_OVERVIEW_CURSOR)


def _eval_stale(environ, code):
    """One fixed-Lua eval on the compositor that ran this bind (its own environment); True if it answered ok."""
    import socket
    import struct
    runtime, instance = environ.get('XDG_RUNTIME_DIR', ''), environ.get('HYPRLAND_INSTANCE_SIGNATURE', '')
    if not runtime.startswith('/') or not _pattern('INSTANCE').match(instance): return False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(0.5); s.connect(f'{runtime}/hypr/{instance}/.socket.sock')
            uid = struct.unpack('3i', s.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))[1]
            if uid != os.getuid(): return False
            s.sendall(('/eval ' + code).encode())
            return s.recv(64).strip() == b'ok'
    except OSError:
        return False


# A send key (send.SEND_OPS) with no run listening: say so; nothing moves. Stdlib only, like the rest of this helper.
NO_SESSION = 'No XR session is running; the window stays where it is.'


def notify_no_session(environ=os.environ, run=None):
    """Best-effort desktop notification; fixed argv, no shell, never raises."""
    import shutil
    import subprocess
    binary = shutil.which('notify-send', path=environ.get('PATH') or '/usr/bin:/bin')
    if not binary: return False
    try:
        (run or subprocess.run)([binary, '--app-name', 'XR Workspace', '--expire-time', '4000', 'XR Workspace',
                                 NO_SESSION], timeout=2, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, check=False)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def pick_video(run=None, environ=os.environ):
    """File picker for Ctrl+Alt+O: an absolute path, or None if cancelled or no picker is installed."""
    import subprocess
    run = run or subprocess.run
    exe = '/usr/bin/zenity'
    if not os.access(exe, os.X_OK): return None
    home = environ.get('HOME', '')
    start = next((d for d in (home + '/Videos/', home + '/') if home.startswith('/') and os.path.isdir(d)), '/')
    try:
        r = run([exe, '--file-selection', '--title=Play a VR video (180 / 360)', '--file-filter=' + VIDEO_FILTER,
                 '--file-filter=All files | *', '--filename=' + start], capture_output=True, timeout=600, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    try: path = r.stdout.decode('utf-8', errors='strict').rstrip('\n') if r.returncode == 0 else ''
    except UnicodeDecodeError: return None  # the control wire is UTF-8 JSON
    return path if path.startswith('/') and '\n' not in path and len(path.encode()) <= 4096 else None


def notify(text, run=None):
    """Best effort desktop notification (the focused desktop is in the glasses)."""
    import subprocess
    run = run or subprocess.run
    exe = '/usr/bin/notify-send'
    if not os.access(exe, os.X_OK): return
    try: run([exe, '--app-name=XR Workspace', '--expire-time=8000', 'VR video', text], capture_output=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError): pass


def read_clipboard(run=None):
    """The clipboard's text (wl-paste, once), or None. Never more than MAX_CLIPBOARD characters are accepted."""
    import subprocess
    run = run or subprocess.run
    exe = '/usr/bin/wl-paste'
    if not os.access(exe, os.X_OK): return None
    try: r = run([exe, '--no-newline', '--type', 'text'], capture_output=True, timeout=3, check=False)
    except (OSError, subprocess.SubprocessError): return None
    if r.returncode != 0 or len(r.stdout) > MAX_CLIPBOARD * 4: return None
    try: return r.stdout.decode('utf-8', errors='strict')
    except UnicodeDecodeError: return None


def clipboard_payload(text):
    """The clipboard as a video_open payload ({"url": link}); (None, reason) if it holds no link.
    Only the shape is checked here (stdlib, no imports); the run validates everything (web_video.validate_url)."""
    t = (text or '').strip()
    if not t: return None, 'The clipboard is empty. Copy a video link first.'
    if len(t) > MAX_CLIPBOARD: return None, 'The clipboard holds too much text to be a video link.'
    import re
    if re.match(r'https?://\S+\Z', t, re.IGNORECASE): return {'url': t}, None
    return None, 'The clipboard holds no video link. Copy the video page or video link first.'


def drop_stale_video_binds(environ=os.environ):
    """A video key with no run listening: the run ended with a video playing. Remove its modal binds (fixed Lua)."""
    return _eval_stale(environ, REMOVE_VIDEO_BINDS)


def _plain(run_id, session, op, payload):
    """An empty payload and strings that json.dumps writes unchanged: the frame needs no json."""
    return (type(payload) is dict and not payload and type(run_id) is str and type(session) is str and type(op) is str
            and _TOKEN_CHARS.issuperset(run_id) and _TOKEN_CHARS.issuperset(session) and _TOKEN_CHARS.issuperset(op))


def control_frame(run_id, session, op, payload, request_id):
    """The control frame: json.dumps(..., separators=(',', ':'), ensure_ascii=False) plus a newline. An empty payload
    with plain strings (every key press and click) is written directly: the same bytes, without importing json."""
    if type(request_id) is int and _plain(run_id, session, op, payload):
        return '{"schema":1,"request_id":%d,"run_id":"%s","session":"%s","op":"%s","payload":{}}\n' % (
            request_id, run_id, session, op)
    import json
    return json.dumps({'schema': 1, 'request_id': request_id, 'run_id': run_id, 'session': session, 'op': op,
                       'payload': payload}, separators=(',', ':'), ensure_ascii=False) + '\n'


def send(path, run_id, session, op, payload):
    # A frame that needs json imports it before the request id is stamped, so the id is still taken just before the
    # send (ids must arrive in order: the run drops an older one as a replay).
    if not _plain(run_id, session, op, payload): import json  # noqa: F401
    data = control_frame(run_id, session, op, payload, time.time_ns()).encode()
    s = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
    try:
        s.settimeout(0.5); s.connect(path); s.sendall(data)
    finally:
        s.close()


def main(argv, picker=pick_video, clipboard=read_clipboard, tell=notify):
    if len(argv) != 5: return 2
    path, run_id, session, op = argv[1:]
    if op not in OPS + VIDEO_OPS + HELPER_OPS + (CANCEL_OP,) or not _is_token(run_id) or not _is_token(session) or not path.startswith('/'): return 2
    payload = {}
    if op == 'video_open':
        try: send(path, run_id, session, 'video_exit', {})  # a playing video hides the desktop the picker opens on
        except OSError: return 1  # no run listening: do not show a picker for nothing
        chosen = picker()
        if chosen is None: return 0  # cancelled
        payload = {'path': chosen}
    elif op == 'video_paste':
        payload, why = clipboard_payload(clipboard())
        if payload is None:
            tell(why); return 0
        op = 'video_open'
    try:
        send(path, run_id, session, op, payload)
    except OSError:
        if op.startswith('video_'): drop_stale_video_binds()  # incl. Escape (video_exit)
        if op.startswith('menu_'): drop_stale_menu_binds()  # incl. Escape (menu_toggle); harmless if none are left
        if op.startswith('resize_'): drop_stale_menu_binds(code=REMOVE_RESIZE_BINDS)  # give the left button back
        if op.startswith('send_'): notify_no_session()  # nothing moves
        if op == 'privacy_display': _eval_stale(os.environ, WAKE_ALL_DISPLAYS)  # never leave the main display dark
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
