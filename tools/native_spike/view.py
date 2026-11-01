"""Glasses view controls for a managed capture run: zoom in/out, switch desktop, spacing.

Keyboard binds exist only while a run is active (user preference: Ctrl+Alt+arrows stay free for
text navigation otherwise). They run xr_control.py, which sends one frame to this run's private
control socket. Focus moves with Hyprland's monitor focus (the cursor follows).

Viewing modes (T2, Ctrl+Alt+V cycles): anchor (world-locked 3DoF), follow (world-locked inside a
dead zone, eases back in front) and locked (0DoF, head-locked). Optional cursor follow (Ctrl+Alt+F):
when the pointer crosses onto another managed output, the view switches to it (Hyprland focusedmon).
Mode, cursor follow and spacing are saved in layout.json.
"""
import json
import os
from pathlib import Path
import re

DEFAULT_SPACING, MAX_SPACING, SPACING_STEP = 40, 400, 20
USER_OPS = ('zoom_in', 'zoom_out', 'next', 'prev', 'spacing_up', 'spacing_down', 'recenter', 'mode_next', 'cursor_follow')
MODES = ('anchor', 'follow', 'locked')  # cycle order
# Hyprland Lua key strings join modifiers with " + " (e.g. "SUPER + SHIFT + R").
BINDS = (('CTRL + ALT + up', 'zoom_in'), ('CTRL + ALT + down', 'zoom_out'), ('CTRL + ALT + left', 'prev'),
         ('CTRL + ALT + right', 'next'), ('CTRL + ALT + bracketleft', 'spacing_down'), ('CTRL + ALT + bracketright', 'spacing_up'),
         ('CTRL + ALT + space', 'recenter'), ('CTRL + ALT + V', 'mode_next'), ('CTRL + ALT + F', 'cursor_follow'))
FOCUSEDMON = re.compile(r'focusedmon>>(XR-[1-3]),')
CONFIGRELOADED = re.compile(r'configreloaded>>')  # Omarchy theme switches reload the Hyprland config
COLOR = re.compile(r'(?:0x)?([0-9a-fA-F]{8})\Z')


def parse_border(reply):
    """Hyprland getoption general:col.active_border -> (r, g, b), or None if unrecognised.

    0.56 replies {"gradient": "AARRGGBB[ AARRGGBB...] <angle>deg"}; older ones {"int": ARGB}. A
    gradient uses its first colour.
    """
    if not isinstance(reply, dict): return None
    if isinstance(reply.get('gradient'), str):
        first = reply['gradient'].split()[:1]
        m = COLOR.match(first[0]) if first else None
        if not m: return None
        value = int(m.group(1), 16)
    elif type(reply.get('int')) is int and 0 <= reply['int'] <= 0xffffffff:
        value = reply['int']
    else:
        return None
    return ((value >> 16) & 255, (value >> 8) & 255, value & 255)


def style_frame(run_id, session, request_id, rgb):
    if len(rgb) != 3 or any(type(c) is not int or not 0 <= c <= 255 for c in rgb): raise ValueError('invalid colour')
    return (json.dumps({'schema': 1, 'request_id': request_id, 'run_id': run_id, 'session': session, 'op': 'style',
                        'payload': {'border': list(rgb)}}, separators=(',', ':')) + '\n').encode()
MANAGED = re.compile(r'XR-[1-3]\Z')
SAFE_PATH = re.compile(r'/[A-Za-z0-9_./:-]+\Z')
SAFE_TOKEN = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z')


class ViewState:
    def __init__(self, names, spacing=DEFAULT_SPACING, mode='anchor', cursor_follow=False):
        if not names or len(names) > 3 or any(not MANAGED.match(n) for n in names): raise ValueError('managed outputs required')
        if mode not in MODES or type(cursor_follow) is not bool: raise ValueError('invalid view mode')
        self.names, self.zoomed, self.selected = list(names), False, 0
        self.spacing = min(max(int(spacing), 0), MAX_SPACING)
        self.mode, self.cursor_follow = mode, cursor_follow

    def view(self):
        """Renderer payload (cursor follow is supervisor-side only)."""
        return {'zoomed': self.zoomed, 'selected': self.selected, 'spacing': self.spacing, 'mode': self.mode}

    def settings(self):
        return {'spacing': self.spacing, 'mode': self.mode, 'cursor_follow': self.cursor_follow}

    def cursor_entered(self, name):
        """Pointer focus moved to `name`; with cursor follow on, select it. True if the view changed."""
        if not self.cursor_follow or name not in self.names: return False
        index = self.names.index(name)
        if index == self.selected: return False
        self.selected = index
        return True

    def apply(self, op):
        """Update state; return the output to focus, or None."""
        if op not in USER_OPS: raise ValueError('unsupported view operation')
        if op == 'recenter': return None  # head-tracking reference only; the view layout is unchanged
        if op == 'mode_next':
            self.mode = MODES[(MODES.index(self.mode) + 1) % len(MODES)]
            return None
        if op == 'cursor_follow':
            self.cursor_follow = not self.cursor_follow
            return None
        if op == 'zoom_in':
            self.zoomed = True
            return self.names[self.selected]
        if op == 'zoom_out':
            self.zoomed = False
            return None
        if op in ('next', 'prev'):
            target = self.selected + (1 if op == 'next' else -1)
            if not 0 <= target < len(self.names): return None
            self.selected = target
            return self.names[target]  # overview and zoomed: highlight/slide and focus
        self.spacing = min(max(self.spacing + (SPACING_STEP if op == 'spacing_up' else -SPACING_STEP), 0), MAX_SPACING)
        return None


def settings_path():
    base = os.environ.get('XDG_CONFIG_HOME') or str(Path.home() / '.config')
    return Path(base) / 'viture-workspace' / 'layout.json'


DEFAULT_SETTINGS = {'spacing': DEFAULT_SPACING, 'mode': 'anchor', 'cursor_follow': False}


def load_settings(path):
    """Each field validated on its own; anything invalid or missing falls back to its default."""
    out = dict(DEFAULT_SETTINGS)
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return out
    if not isinstance(data, dict): return out
    if type(data.get('spacing')) is int and 0 <= data['spacing'] <= MAX_SPACING: out['spacing'] = data['spacing']
    if data.get('mode') in MODES: out['mode'] = data['mode']
    if type(data.get('cursor_follow')) is bool: out['cursor_follow'] = data['cursor_follow']
    return out


def save_settings(path, settings):
    if not isinstance(settings, dict) or set(settings) != set(DEFAULT_SETTINGS) or not _valid(settings):
        raise ValueError('invalid settings')
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(path.name + '.tmp')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as f: json.dump(settings, f)
    os.replace(tmp, path)


def _valid(settings):
    return (type(settings.get('spacing')) is int and 0 <= settings['spacing'] <= MAX_SPACING and settings.get('mode') in MODES
            and type(settings.get('cursor_follow')) is bool)


def load_spacing(path):
    return load_settings(path)['spacing']


def save_spacing(path, spacing):
    if type(spacing) is not int or not 0 <= spacing <= MAX_SPACING: raise ValueError('invalid spacing')
    save_settings(path, {**load_settings(path), 'spacing': spacing})


def view_frame(run_id, session, request_id, state):
    return (json.dumps({'schema': 1, 'request_id': request_id, 'run_id': run_id, 'session': session, 'op': 'view',
                        'payload': state}, separators=(',', ':')) + '\n').encode()


def focus_command(name):
    if not isinstance(name, str) or not MANAGED.match(name): raise ValueError('focus is limited to managed outputs')
    return f'dispatch hl.dsp.focus({{monitor="{name}"}})'


SESSION_MODE = (1920, 1200, 120)  # user grant: session-only Luma/Beast mode on the approved output
PHYSICAL = re.compile(r'(?:DP|HDMI-A|eDP)-[0-9]+\Z')


def session_mode(monitor):
    """The approved output's placement if it is in the granted session mode, else None."""
    if not isinstance(monitor, dict) or not PHYSICAL.match(str(monitor.get('name', ''))): return None
    if (monitor.get('width'), monitor.get('height')) != SESSION_MODE[:2]: return None
    if not isinstance(monitor.get('refreshRate'), (int, float)) or abs(monitor['refreshRate'] - SESSION_MODE[2]) > 0.5: return None
    x, y, scale = monitor.get('x'), monitor.get('y'), monitor.get('scale')
    if type(x) is not int or type(y) is not int or not isinstance(scale, (int, float)) or not 0.25 <= scale <= 4: return None
    return {'name': monitor['name'], 'x': x, 'y': y, 'scale': float(scale)}


def restore_mode_lua(mode):
    """Re-apply the granted session mode after a config reload reset it (fixed shape, validated)."""
    if session_mode({**mode, 'width': SESSION_MODE[0], 'height': SESSION_MODE[1], 'refreshRate': SESSION_MODE[2]}) != mode:
        raise ValueError('invalid session mode')
    w, h, r = SESSION_MODE
    return (f'hl.monitor({{ output = "{mode["name"]}", mode = "{w}x{h}@{r}", position = "{mode["x"]}x{mode["y"]}", '
            f'scale = {mode["scale"]:g} }})')


def install_binds_lua(python, client, control_socket, run_id, session):
    for p in (python, client, control_socket):
        if not isinstance(p, str) or not SAFE_PATH.match(p): raise ValueError('unsafe path for bind command')
    for t in (run_id, session):
        if not isinstance(t, str) or not SAFE_TOKEN.match(t): raise ValueError('unsafe token for bind command')
    base = f'{python} -I {client} {control_socket} {run_id} {session}'
    binds = ', '.join(f'hl.bind("{chord}", hl.dsp.exec_cmd("{base} {op}"))' for chord, op in BINDS)
    return f'xr_workspace_binds = {{ {binds} }}'


def remove_binds_lua():
    # Hyprland 0.56: remove() on an expired handle (after unbind/reload) segfaults; probe first.
    return ('for _, b in ipairs(xr_workspace_binds or {}) do '
            'if tostring(b) ~= "HL.Keybind(expired)" then b:remove() end end xr_workspace_binds = nil')


class ViewControl:
    """Supervisor-side handler: user op -> state, async focus, saved spacing, renderer view frame."""
    def __init__(self, state, run_id, session, focus, settings, server, first_request_id=2, border=None, on_reload=None):
        self.state, self.run_id, self.session = state, run_id, session
        self.focus, self.settings, self.server = focus, settings, server
        self.next_id = first_request_id
        self._threads = []
        self.events = None       # optional Hyprland event socket (cursor follow); owned by the caller
        self._event_buffer = b''
        self.cursor_switches = 0
        self.border = border     # optional () -> Hyprland getoption reply for general:col.active_border
        self.accent = None       # last theme colour sent to the renderer
        self.on_reload = on_reload  # optional () -> None, run off-loop after Hyprland config reloads
        self.reloads = 0

    def initial_frame(self):
        return self._frame() + self._style_frame()

    def _style_frame(self):
        """Theme border colour for the highlight; empty if unknown or unchanged. Never fatal."""
        if self.border is None: return b''
        try: rgb = parse_border(self.border())
        except (OSError, ValueError, TypeError, KeyError): rgb = None  # incl. clients without the option
        if rgb is None or rgb == self.accent: return b''
        self.accent = rgb
        frame = style_frame(self.run_id, self.session, self.next_id, rgb)
        self.next_id += 1
        return frame

    def _frame(self):
        frame = view_frame(self.run_id, self.session, self.next_id, self.state.view())
        self.next_id += 1
        return frame

    def on_message(self, message):
        if not isinstance(message, dict) or message.get('op') not in USER_OPS: return None
        if message['op'] == 'recenter':
            frame = (json.dumps({'schema': 1, 'request_id': self.next_id, 'run_id': self.run_id, 'session': self.session,
                                 'op': 'recenter', 'payload': {}}, separators=(',', ':')) + '\n').encode()
            self.next_id += 1
            return frame
        before = self.state.view()
        target = self.state.apply(message['op'])
        if target is not None:
            import threading
            # Never block the watchdog loop on a compositor reply.
            t = threading.Thread(target=self._focus, args=(target,), daemon=True); t.start(); self._threads.append(t)
        if message['op'] in ('spacing_up', 'spacing_down', 'mode_next', 'cursor_follow'):
            try: save_settings(self.settings, self.state.settings())
            except (OSError, ValueError): pass
        return self._frame() if self.state.view() != before else None

    def on_events(self, data):
        """Hyprland socket2 bytes; returns a renderer frame if cursor follow switched the view."""
        self._event_buffer += data
        if len(self._event_buffer) > 65536: self._event_buffer = self._event_buffer[-4096:]  # bounded; resync on newline
        *lines, self._event_buffer = self._event_buffer.split(b'\n')
        changed = reloaded = False
        for line in lines:
            text = line.decode('utf-8', errors='replace')
            m = FOCUSEDMON.match(text)
            if m and self.state.cursor_entered(m.group(1)):
                changed = True; self.cursor_switches += 1
            if CONFIGRELOADED.match(text): reloaded = True
        if reloaded:
            self.reloads += 1
            if self.on_reload is not None:  # binds and session mode: compositor calls, never on the watchdog loop
                import threading
                t = threading.Thread(target=self._reload_work, daemon=True); t.start(); self._threads.append(t)
        out = (self._frame() if changed else b'') + (self._style_frame() if reloaded else b'')
        return out or None

    def _reload_work(self):
        try: self.on_reload()
        except (OSError, ValueError, TypeError, KeyError): pass  # best-effort; the run itself is unaffected

    def _focus(self, target):
        try: self.focus(target)
        except (OSError, ValueError): pass  # focus is best-effort; the view still changes

    def join_focus(self, timeout=2.0):
        for t in self._threads: t.join(timeout)
        self._threads = [t for t in self._threads if t.is_alive()]
