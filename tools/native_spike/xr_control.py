"""Bind helper: send one view operation to a running managed capture run. Stdlib only, run with -I.

usage: python3 -I xr_control.py CONTROL_SOCKET RUN_ID SESSION OP
Exits quietly with status 1 if no run is listening (binds only exist during a run anyway).
video_open (Ctrl+Alt+O) first leaves any playing video (video_exit, so the desktops and the picker are visible in the
glasses), then shows a file picker (zenity) on the focused desktop and sends the chosen path; a cancelled picker sends
nothing more. The run checks and opens the file itself (video.py).
"""
import json
import os
import re
import socket
import struct
import sys
import time

OPS = ('zoom_in', 'zoom_out', 'next', 'prev', 'spacing_up', 'spacing_down', 'recenter', 'mode_next', 'cursor_follow',
       'prediction', 'help', 'menu_toggle', 'menu_desktop_next', 'menu_desktop_prev', 'menu_field_next', 'menu_field_prev',
       'menu_inc', 'menu_dec', 'menu_inc_big', 'menu_dec_big', 'menu_reset', 'menu_mouse', 'menu_back', 'menu_mouse_press',
       'menu_mouse_release', 'resize_press', 'resize_release', 'send_glasses', 'send_glasses_1', 'send_glasses_2',
       'send_glasses_3', 'send_back')  # view.USER_OPS + view.MENU_OPS + view.MOUSE_OPS + resize.RESIZE_OPS + send.SEND_OPS
# VR video ops (video.VIDEO_OPS); duplicated because this helper is stdlib-only under -I (tests compare).
VIDEO_OPS = ('video_open', 'video_pause', 'video_seek_back', 'video_seek_fwd', 'video_seek_back_big', 'video_seek_fwd_big',
             'video_format', 'video_controls', 'video_exit')
# Same text as video.remove_video_binds_lua() (tests compare): a video key with no run listening gives the keys back.
REMOVE_VIDEO_BINDS = ('for _, b in ipairs(xr_workspace_video_binds or {}) do '
                      'if tostring(b) ~= "HL.Keybind(expired)" then b:remove() end end xr_workspace_video_binds = nil')
VIDEO_FILTER = 'Videos | *.mp4 *.MP4 *.mkv *.MKV *.webm *.mov *.MOV *.m4v *.avi *.ts *.m2ts'
TOKEN = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z')
INSTANCE = re.compile(r'[0-9a-f]{40}_[0-9]+_[0-9]+\Z')
# Same text as view.remove_menu_binds_lua() (tests compare); duplicated because this helper is stdlib-only under -I.
# It also leaves menu mouse mode: the left-button binds go and the cursor is shown and put back.
REMOVE_MENU_BINDS = ('for _, b in ipairs(xr_workspace_menu_binds or {}) do '
                     'if tostring(b) ~= "HL.Keybind(expired)" then b:remove() end end xr_workspace_menu_binds = nil '
                     'for _, b in ipairs(xr_workspace_mouse_binds or {}) do '
                     'if tostring(b) ~= "HL.Keybind(expired)" then b:remove() end end xr_workspace_mouse_binds = nil '
                     'do local s = xr_workspace_mouse xr_workspace_mouse = nil if s then '
                     'pcall(hl.config, { cursor = { invisible = s.invisible } }) '
                     'if s.home then pcall(function() hl.dispatch(hl.dsp.cursor.move({ x = s.home.x, y = s.home.y })) end) end '
                     'end end')
# Same text as resize.remove_resize_binds_lua() (tests compare). A left click with no run listening must not stay bound.
REMOVE_RESIZE_BINDS = ('for _, b in ipairs(xr_workspace_resize_binds or {}) do '
                       'if tostring(b) ~= "HL.Keybind(expired)" then b:remove() end end xr_workspace_resize_binds = nil')


def drop_stale_menu_binds(environ=os.environ, code=REMOVE_MENU_BINDS):
    """A menu key with no run listening: the supervisor died with the menu open. Remove its modal binds so the arrows,
    Tab, R and Escape work again (and, in mouse mode, the left button and the cursor). Fixed Lua only, on the compositor that ran this bind (its own environment)."""
    runtime, instance = environ.get('XDG_RUNTIME_DIR', ''), environ.get('HYPRLAND_INSTANCE_SIGNATURE', '')
    if not runtime.startswith('/') or not INSTANCE.match(instance): return False
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
        (run or subprocess.run)([binary, '--app-name', 'VITURE Workspace', '--expire-time', '4000', 'VITURE Workspace',
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


def drop_stale_video_binds(environ=os.environ):
    """A video key with no run listening: the run ended with a video playing. Remove its modal binds (fixed Lua)."""
    runtime, instance = environ.get('XDG_RUNTIME_DIR', ''), environ.get('HYPRLAND_INSTANCE_SIGNATURE', '')
    if not runtime.startswith('/') or not INSTANCE.match(instance): return False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(0.5); s.connect(f'{runtime}/hypr/{instance}/.socket.sock')
            uid = struct.unpack('3i', s.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))[1]
            if uid != os.getuid(): return False
            s.sendall(('/eval ' + REMOVE_VIDEO_BINDS).encode())
            return s.recv(64).strip() == b'ok'
    except OSError:
        return False


def send(path, run_id, session, op, payload):
    frame = json.dumps({'schema': 1, 'request_id': time.time_ns(), 'run_id': run_id, 'session': session, 'op': op,
                        'payload': payload}, separators=(',', ':'), ensure_ascii=False) + '\n'
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(0.5); s.connect(path); s.sendall(frame.encode())


def main(argv, picker=pick_video):
    if len(argv) != 5: return 2
    path, run_id, session, op = argv[1:]
    if op not in OPS + VIDEO_OPS or not TOKEN.match(run_id) or not TOKEN.match(session) or not path.startswith('/'): return 2
    payload = {}
    if op == 'video_open':
        try: send(path, run_id, session, 'video_exit', {})  # a playing video hides the desktop the picker opens on
        except OSError: return 1  # no run listening: do not show a picker for nothing
        chosen = picker()
        if chosen is None: return 0  # cancelled
        payload = {'path': chosen}
    try:
        send(path, run_id, session, op, payload)
    except OSError:
        if op.startswith('video_'): drop_stale_video_binds()  # incl. Escape (video_exit)
        if op.startswith('menu_'): drop_stale_menu_binds()  # incl. Escape (menu_toggle); harmless if none are left
        if op.startswith('resize_'): drop_stale_menu_binds(code=REMOVE_RESIZE_BINDS)  # give the left button back
        if op.startswith('send_'): notify_no_session()  # nothing moves
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
