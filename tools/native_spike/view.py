"""Glasses view controls for a managed capture run: zoom in/out, switch desktop, spacing.

Keyboard binds exist only while a run is active (user preference: Ctrl+Alt+arrows stay free for
text navigation otherwise). They run xr_control.py, which sends one frame to this run's private
control socket. Focus moves with Hyprland's monitor focus (the cursor follows).
"""
import json
import os
from pathlib import Path
import re

DEFAULT_SPACING, MAX_SPACING, SPACING_STEP = 40, 400, 20
USER_OPS = ('zoom_in', 'zoom_out', 'next', 'prev', 'spacing_up', 'spacing_down')
# Hyprland Lua key strings join modifiers with " + " (e.g. "SUPER + SHIFT + R").
BINDS = (('CTRL + ALT + up', 'zoom_in'), ('CTRL + ALT + down', 'zoom_out'), ('CTRL + ALT + left', 'prev'),
         ('CTRL + ALT + right', 'next'), ('CTRL + ALT + bracketleft', 'spacing_down'), ('CTRL + ALT + bracketright', 'spacing_up'))
MANAGED = re.compile(r'XR-[1-3]\Z')
SAFE_PATH = re.compile(r'/[A-Za-z0-9_./:-]+\Z')
SAFE_TOKEN = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z')


class ViewState:
    def __init__(self, names, spacing=DEFAULT_SPACING):
        if not names or len(names) > 3 or any(not MANAGED.match(n) for n in names): raise ValueError('managed outputs required')
        self.names, self.zoomed, self.selected = list(names), False, 0
        self.spacing = min(max(int(spacing), 0), MAX_SPACING)

    def view(self):
        return {'zoomed': self.zoomed, 'selected': self.selected, 'spacing': self.spacing}

    def apply(self, op):
        """Update state; return the output to focus, or None."""
        if op not in USER_OPS: raise ValueError('unsupported view operation')
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


def load_spacing(path):
    try:
        data = json.loads(Path(path).read_text())
        value = data.get('spacing') if isinstance(data, dict) else None
        if type(value) is int and 0 <= value <= MAX_SPACING: return value
    except (OSError, ValueError):
        pass
    return DEFAULT_SPACING


def save_spacing(path, spacing):
    if type(spacing) is not int or not 0 <= spacing <= MAX_SPACING: raise ValueError('invalid spacing')
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(path.name + '.tmp')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as f: json.dump({'spacing': spacing}, f)
    os.replace(tmp, path)


def view_frame(run_id, session, request_id, state):
    return (json.dumps({'schema': 1, 'request_id': request_id, 'run_id': run_id, 'session': session, 'op': 'view',
                        'payload': state}, separators=(',', ':')) + '\n').encode()


def focus_command(name):
    if not isinstance(name, str) or not MANAGED.match(name): raise ValueError('focus is limited to managed outputs')
    return f'dispatch hl.dsp.focus({{monitor="{name}"}})'


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
    def __init__(self, state, run_id, session, focus, settings, server, first_request_id=2):
        self.state, self.run_id, self.session = state, run_id, session
        self.focus, self.settings, self.server = focus, settings, server
        self.next_id = first_request_id
        self._threads = []

    def initial_frame(self):
        return self._frame()

    def _frame(self):
        frame = view_frame(self.run_id, self.session, self.next_id, self.state.view())
        self.next_id += 1
        return frame

    def on_message(self, message):
        if not isinstance(message, dict) or message.get('op') not in USER_OPS: return None
        before = self.state.view()
        target = self.state.apply(message['op'])
        if target is not None:
            import threading
            # Never block the watchdog loop on a compositor reply.
            t = threading.Thread(target=self._focus, args=(target,), daemon=True); t.start(); self._threads.append(t)
        if self.state.view()['spacing'] != before['spacing']:
            try: save_spacing(self.settings, self.state.view()['spacing'])
            except OSError: pass
        return self._frame() if self.state.view() != before else None

    def _focus(self, target):
        try: self.focus(target)
        except (OSError, ValueError): pass  # focus is best-effort; the view still changes

    def join_focus(self, timeout=2.0):
        for t in self._threads: t.join(timeout)
        self._threads = [t for t in self._threads if t.is_alive()]
