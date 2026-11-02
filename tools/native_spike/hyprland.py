"""Bounded raw IPC and fail-closed normalization; no command-shell execution.

Socket2 in the qualified compositor has no transaction/creation token or replay
cursor. It cannot prove a journal UUID belongs to the currently queried monitor.
Stock transport cannot create/recover owned outputs. An explicitly constructed
OwnershipBrokerClient supplies the separate compositor-resident boundary.
"""
import errno
import hashlib
import math
import os
from pathlib import Path
import re
import socket
import stat
import struct
import time
import uuid
from .ipc import strict_json, token
from .geometry import DEFAULT_DESKTOP, logical_width, validate_desktop

INSTANCE = re.compile(r'[0-9a-f]{40}_[0-9]+_[0-9]+\Z')
NAME = r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}'
# Stable managed-output names: Omarchy gives each its own persistent workspace block.
OWNED = r'XR-[1-9][0-9]*'
# Any managed name, including pre-Omarchy per-run names: never adoptable without a broker.
MANAGED_ANY = r'XR-(?:[1-9][0-9]*|SPIKE-.*)'
PLUGIN_PATH = re.compile(r'(?:/[A-Za-z0-9_.-]+)+/viture-workspace/bin/[0-9a-f]{12}/libxr-ownership\.so')
# Our broker's PLUGIN_INIT name (spike/ownership/plugin.cpp). `plugin list` has no path field, so it is matched by name.
OWNERSHIP_PLUGIN_NAME = 'xr-ownership'


def ownership_plugin_count(names):
    """How many entries of a `plugins_loaded()` list are our broker. Unrelated plugins, such as the hyprbars that
    Omarchy keeps loaded all session for its titlebars workspace mode, do not count: the broker hooks no compositor
    functions and every broker reply is checked against the run's session and epoch, so they cannot stand in for it."""
    return sum(1 for name in names if name == OWNERSHIP_PLUGIN_NAME)
QUERY = {'monitors', 'workspaces', 'clients', 'activewindow', 'getoption', 'cursorpos'}
LIMIT = 1048576
DEADLINE = 2.0

class OwnershipBlocked(ValueError): pass

class HyprlandClient:
    def __init__(self, instance: str, runtime_dir: Path):
        if not isinstance(instance, str) or not INSTANCE.fullmatch(instance):
            raise ValueError('explicit full Hyprland instance signature required')
        self.instance = instance
        self.runtime_dir = Path(runtime_dir)
        if not self.runtime_dir.is_absolute(): raise ValueError('absolute runtime directory required')
        self.directory = self.runtime_dir / 'hypr' / instance
        self._peer = None
        self._session = None

    def _connect(self, deadline, name='.socket.sock'):
        # No symlink traversal for the selected endpoint or its parents.
        for path in (self.runtime_dir, self.runtime_dir / 'hypr', self.directory):
            st = path.lstat()
            if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o022:
                raise PermissionError('unsafe Hyprland socket directory')
        if name not in ('.socket.sock', '.socket2.sock'): raise ValueError('unknown Hyprland socket')
        path = self.directory / name
        st = path.lstat()
        if not stat.S_ISSOCK(st.st_mode) or st.st_uid != os.getuid(): raise PermissionError('unsafe IPC socket')
        conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            directory_fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0: raise TimeoutError('Hyprland connect deadline exceeded')
                    conn.settimeout(remaining)
                    try:
                        conn.connect(f'/proc/self/fd/{directory_fd}/{name}')
                    except BlockingIOError as exc:
                        # AF_UNIX returns EAGAIN when the accept queue is full.
                        # Nothing has been sent: retry only connection establishment.
                        if exc.errno != errno.EAGAIN: raise
                        remaining = deadline - time.monotonic()
                        if remaining <= 0: raise TimeoutError('Hyprland connect deadline exceeded') from exc
                        time.sleep(min(0.01, remaining))
                        continue
                    if time.monotonic() >= deadline: raise TimeoutError('Hyprland connect deadline exceeded')
                    break
            finally: os.close(directory_fd)
            peer = struct.unpack('3i', conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            if peer[1] != os.getuid() or peer[0] <= 0: raise PermissionError('wrong compositor peer')
            # Kernel process start tick also protects against PID reuse.
            process = Path(f'/proc/{peer[0]}/stat').read_text()
            start = process.rsplit(')', 1)[1].split()[19]
            identity = (*peer, start)
            if self._peer is not None and self._peer != identity: raise ValueError('compositor peer changed')
            self._peer = identity
            return conn
        except BaseException:
            conn.close(); raise

    def _request(self, request, timeout=DEADLINE):
        deadline = time.monotonic() + timeout
        with self._connect(deadline) as conn:
            conn.settimeout(max(0.001, deadline - time.monotonic()))
            conn.sendall(request.encode('ascii'))
            result = bytearray()
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0: raise TimeoutError('whole-response deadline exceeded')
                conn.settimeout(remaining)
                part = conn.recv(min(65536, LIMIT + 1 - len(result)))
                if not part: return bytes(result)
                result.extend(part)
                if len(result) > LIMIT: raise ValueError('Hyprland response exceeds 1 MiB')

    def event_stream(self):
        """Read-only Hyprland event stream (socket2), same peer checks; caller reads and closes."""
        conn = self._connect(time.monotonic() + DEADLINE, '.socket2.sock')
        conn.setblocking(False)
        return conn

    def query(self, kind, argument=None, timeout=DEADLINE):
        if kind not in QUERY: raise ValueError('raw query not allowlisted')
        if not 0 < timeout <= DEADLINE: raise ValueError('invalid timeout')
        if kind == 'getoption':
            if argument not in ('input:follow_mouse', 'general:col.active_border'): raise ValueError('option not allowlisted')
            suffix = ' ' + argument
        else:
            if argument is not None: raise ValueError('unexpected query argument')
            suffix = ' all' if kind == 'monitors' else ''
        return strict_json(self._request('j/' + kind + suffix, timeout))

    def mutate(self, command):
        self._check_mutation(command)
        return self._request('/' + command).decode('utf-8', errors='strict').strip()

    @staticmethod
    def _check_mutation(command):
        # Internal API only. Never expose this string through the public control wire.
        # Managed output lifecycle is exclusively broker/handle addressed.
        # This host uses Lua config; legacy keyword monitor is not a fallback.
        patterns = [r'keyword input:follow_mouse [0-3]',
                    rf'dispatch moveworkspacetomonitor ([1-9][0-9]*) ({NAME})',
                    r'dispatch hl\.dsp\.focus\(\{monitor="XR-[1-3]"\}\)']  # glasses view focus, managed outputs only
        from .send import MOVE_PATTERN
        patterns.append(MOVE_PATTERN)  # Ctrl+Alt+G/1-3/B: one window by address onto one regular workspace (send.py)
        if not isinstance(command, str) or len(command) > 512: raise ValueError('invalid mutation')
        if not any(re.fullmatch(p, command) for p in patterns): raise ValueError('mutation not allowlisted')
        for word in re.split(r'[ ,]', command):
            if word.startswith('XR-') and not re.fullmatch(OWNED, word): raise ValueError('invalid managed output name')
        if command.startswith('dispatch moveworkspacetomonitor') and int(command.split()[2]) > 2147483647:
            raise ValueError('invalid workspace')
        if command.startswith('dispatch hl.dsp.window.move') and int(re.fullmatch(MOVE_PATTERN, command).group(1)) > 2147483647:
            raise ValueError('invalid workspace')

    def install_view_binds(self, python, client, control_socket, run_id, session):
        """Runtime-only Ctrl+Alt binds for the glasses view; generated Lua only, never free-form eval."""
        from .view import install_binds_lua, install_send_binds_lua, remove_binds_lua
        self._eval(install_binds_lua(python, client, control_socket, run_id, session))
        try: self._eval(install_send_binds_lua(python, client, control_socket, run_id, session))  # replaces any surviving set
        except (OSError, ValueError):
            try: self._eval(remove_binds_lua())  # the run will not start: leave no half-installed binds behind
            except (OSError, ValueError): pass
            raise

    def reinstall_view_binds(self, python, client, control_socket, run_id, session):
        """After a config reload: drop any surviving handles, then bind again (never doubled)."""
        from .view import install_binds_lua, install_send_binds_lua, remove_binds_lua
        self._eval(remove_binds_lua() + ' ' + install_binds_lua(python, client, control_socket, run_id, session))
        self._eval(install_send_binds_lua(python, client, control_socket, run_id, session))

    # Ownership plugin lifecycle. Wire strings follow hyprctl's request format ('/plugin load <abs path>',
    # '/plugin unload <abs path>', 'j/plugin list'); they MUST be confirmed against the VM's Hyprland before
    # any live use (Task 12). The JSON list has no path field, so readback is by plugin name.
    # Deliberately not part of the generic mutate allowlist.
    def plugins_loaded(self):
        raw = strict_json(self._request('j/plugin list'))
        if not isinstance(raw, list) or any(not isinstance(p, dict) or not isinstance(p.get('name'), str) for p in raw):
            raise ValueError('invalid plugin list response')
        return [p['name'] for p in raw]

    def plugin_load(self, path, *, data_home=None):
        self._plugin_command('load', path, data_home)

    def plugin_unload(self, path, *, data_home=None):
        self._plugin_command('unload', path, data_home)

    def _plugin_command(self, verb, path, data_home):
        from ..xr_controller.install import PLUGIN, load_installed
        if data_home is None: data_home = Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local/share')
        if not isinstance(path, str) or not PLUGIN_PATH.fullmatch(path) or Path(path).name != PLUGIN:
            raise ValueError('plugin path must be the pinned install')
        if path != load_installed(data_home)['plugin']['path']: raise ValueError('plugin path must be the pinned install')
        reply = self._request(f'/plugin {verb} {path}').decode('utf-8', errors='replace').strip()
        if reply != 'ok':
            raise ValueError(f'plugin {verb} rejected: ' + ''.join(c for c in reply[:160] if c.isprintable()))

    def apply_session_mode(self, output):
        """Session-only 1920x1200@120 on the approved output at its current placement; verified by readback."""
        from .view import SESSION_MODE, restore_mode_lua, session_mode
        def current(timeout=DEADLINE):
            found = [m for m in monitors(self.query('monitors', timeout=timeout)) if m['name'] == output]
            if len(found) != 1: raise ValueError('session mode output not found')
            return found[0]
        m = current()
        mode = {'name': m['name'], 'x': m['x'], 'y': m['y'], 'scale': float(m['scale'])}
        self._eval(restore_mode_lua(mode))
        # MonitorRuleManager::add only schedules a reload; render.preChecks applies it.
        # An eval acknowledgement is not a mode-application barrier.
        deadline, last = time.monotonic() + DEADLINE, None
        while (remaining := deadline - time.monotonic()) > 0:
            try:
                last = current(timeout=remaining)
            except TimeoutError:
                break
            if session_mode(last) == mode and time.monotonic() <= deadline:
                return mode
            remaining = deadline - time.monotonic()
            if remaining <= 0: break
            time.sleep(min(0.05, remaining))
        observed = ({k: last.get(k) for k in ('name', 'width', 'height', 'refreshRate', 'x', 'y', 'scale')}
                    if last is not None else None)
        raise ValueError(f'session mode {SESSION_MODE} at {mode} not observed within {DEADLINE:g}s; last readback: {observed}')

    def park_presentation(self, output):
        """Keep the rendering output away from the interactive row; preserve its mode.

        Runtime-only, deliberately retained after stop, like apply_session_mode.
        No personal monitor config is written. Config reloads reapply this policy.
        """
        desired = presentation_placement(self.query('monitors'), output)
        w, h, hz = desired['width'], desired['height'], desired['refreshRate']
        self._eval(f'hl.monitor({{ output = "{desired["name"]}", mode = "{w}x{h}@{hz:.6f}", '
                   f'position = "{desired["x"]}x{desired["y"]}", scale = {desired["scale"]}, '
                   f'transform = {desired["transform"]} }})')
        deadline = time.monotonic() + DEADLINE
        while (remaining := deadline - time.monotonic()) > 0:
            snapshot = monitors(self.query('monitors', timeout=remaining))
            found = next((m for m in snapshot if m['name'] == output), None)
            if (found is not None and all(found[k] == desired[k] for k in
                    ('id', 'width', 'height', 'x', 'y', 'scale', 'transform', 'disabled', 'dpmsStatus', 'mirrorOf'))
                    and abs(found['refreshRate'] - hz) < .01 and time.monotonic() <= deadline):
                return found
            time.sleep(min(.05, max(0, deadline - time.monotonic())))
        raise ValueError('presentation placement was not observed before deadline')

    def restore_session_mode(self, mode):
        from .view import restore_mode_lua
        self._eval(restore_mode_lua(mode))

    def remove_view_binds(self):
        from .view import remove_binds_lua
        self._eval(remove_binds_lua())

    def install_video_binds(self, python, client, control_socket, run_id, session):
        """A VR video started: its modal bare-key binds (video.VIDEO_BINDS); generated Lua only."""
        from .video import install_video_binds_lua
        self._eval(install_video_binds_lua(python, client, control_socket, run_id, session))

    def remove_video_binds(self):
        from .video import remove_video_binds_lua
        self._eval(remove_video_binds_lua())

    def install_menu_binds(self, python, client, control_socket, run_id, session):
        """Placement menu opened: its modal bare-key binds (replacing any surviving set); generated Lua only."""
        from .view import install_menu_binds_lua
        self._eval(install_menu_binds_lua(python, client, control_socket, run_id, session))

    def remove_menu_binds(self):
        from .view import remove_menu_binds_lua
        self._eval(remove_menu_binds_lua())

    def install_resize_binds(self, python, client, control_socket, run_id, session):
        """Overview free pointer: the modal left-button press/release binds; generated Lua only."""
        from .resize import install_resize_binds_lua
        self._eval(install_resize_binds_lua(python, client, control_socket, run_id, session))

    def remove_resize_binds(self):
        from .resize import remove_resize_binds_lua
        self._eval(remove_resize_binds_lua())

    def grab_pointer(self, python, client, control_socket, run_id, session, x, y):
        """Overview free pointer, free mode: left-button binds, cursor hidden and parked at (x, y); generated Lua only."""
        from .free_pointer import grab_pointer_lua
        self._eval(grab_pointer_lua(python, client, control_socket, run_id, session, x, y))

    def release_pointer(self, x=None, y=None):
        """Free pointer back in a desktop: binds removed, cursor shown and (if given) put at (x, y); generated Lua only."""
        from .free_pointer import release_pointer_lua
        self._eval(release_pointer_lua(x, y))

    def install_mouse_binds(self, python, client, control_socket, run_id, session):
        """Placement menu mouse mode on: left-button binds, cursor hidden (runtime only); generated Lua only."""
        from .view import install_mouse_binds_lua
        self._eval(install_mouse_binds_lua(python, client, control_socket, run_id, session))

    def remove_mouse_binds(self):
        """Mouse mode off: the click binds go, the cursor is shown and put back where it was."""
        from .view import remove_mouse_binds_lua
        self._eval(remove_mouse_binds_lua())

    def warp_cursor(self, x, y):
        """Mouse mode: put the hidden cursor back at integer layout pixel (x, y); generated Lua only."""
        from .view import warp_cursor_lua
        self._eval(warp_cursor_lua(x, y))

    def _eval(self, code):
        if len(code) > 4096: raise ValueError('bind code too large')
        reply = self._request('/eval ' + code).decode('utf-8', errors='replace').strip()
        if reply != 'ok':
            detail = ''.join(c for c in reply[:160] if c.isprintable())
            raise ValueError(f'compositor rejected bind change: {detail}')

    def verified_session(self):
        # A request establishes the actual SO_PEERCRED process, not a configured echo.
        self.query('monitors')
        pid = self._peer[0]
        if Path(os.readlink(f'/proc/{pid}/exe')).name != 'Hyprland':
            raise ValueError('socket peer is not the selected Hyprland process')
        lock = self.directory / 'hyprland.lock'
        fd = os.open(lock, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            st = os.fstat(stream.fileno())
            if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o022:
                raise PermissionError('unsafe compositor lock')
            fields = stream.read(1024).decode('ascii').splitlines()
        if len(fields) != 2 or fields[0] != str(pid) or not re.fullmatch(r'wayland-[0-9]+', fields[1]):
            raise ValueError('lock/peer mismatch')
        boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        identity = hashlib.sha256(f'{boot}:{self.instance}:{self._peer}'.encode()).hexdigest()
        if self._session is not None and self._session != identity: raise ValueError('session changed')
        self._session = identity
        self._wayland_name = fields[1]
        return {'id': identity}

    def connect_wayland(self, session):
        """Owned connection to the verified lock's endpoint, never ambient display."""
        if self.verified_session() != {'id': session}: raise ValueError('Wayland session mismatch')
        path = self.runtime_dir / self._wayland_name
        st = path.lstat()
        if not stat.S_ISSOCK(st.st_mode) or st.st_uid != os.getuid():
            raise PermissionError('unsafe Wayland socket')
        conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            directory = os.open(self.runtime_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                conn.settimeout(DEADLINE)
                conn.connect(f'/proc/self/fd/{directory}/{self._wayland_name}')
            finally: os.close(directory)
            peer = struct.unpack('3i', conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            if peer[1] != os.getuid() or peer != self._peer[:3]:
                raise PermissionError('Wayland/IPC peer mismatch')
            conn.settimeout(None)
            return conn
        except BaseException:
            conn.close()
            raise

    def require_output_ownership(self):
        raise OwnershipBlocked('Hyprland socket2 has no creation UUID, transaction acknowledgement, '
                               'snapshot/event barrier or replay cursor; independent incarnation '
                               'continuity cannot be proven. No outputs were created or removed.')


def monitors(raw):
    if not isinstance(raw, list): raise ValueError('invalid monitor response')
    names, ids = set(), set()
    for m in raw:
        if not isinstance(m, dict): raise ValueError('invalid monitor')
        token(m['name'])
        if m['name'] in names or type(m['id']) is not int or m['id'] < 0 or m['id'] in ids:
            raise ValueError('ambiguous monitor')
        names.add(m['name']); ids.add(m['id'])
        for key in ('width', 'height'):
            if type(m[key]) is not int or not 0 < m[key] <= 32768: raise ValueError('invalid dimensions')
        for key in ('x', 'y'):
            if type(m[key]) is not int or not -2147483648 <= m[key] <= 2147483647: raise ValueError('invalid position')
        for key in ('scale', 'refreshRate'):
            if type(m[key]) not in (int, float) or not math.isfinite(m[key]) or m[key] <= 0: raise ValueError('invalid mode')
        if type(m['transform']) is not int or not 0 <= m['transform'] <= 7: raise ValueError('invalid transform')
        mirror = m.get('mirrorOf')
        if not isinstance(mirror, str) or not re.fullmatch(r'none|0|[1-9][0-9]*', mirror):
            raise ValueError('missing or invalid mirror evidence')
        for key in ('disabled', 'dpmsStatus'):
            if type(m[key]) is not bool: raise ValueError('incomplete monitor state')
    return raw

class RecoveryAdapter:
    def __init__(self, client, *, approved_physical):
        self.client, self.approved_physical = client, token(approved_physical)
    def query(self, kind):
        if kind == 'session': return self.client.verified_session()
        self.client.verified_session()
        if kind == 'outputs':
            result = []
            snapshot = monitors(self.client.query('monitors'))
            if not snapshot: raise ValueError('empty output snapshot cannot prove safe recovery')
            for m in snapshot:
                if re.fullmatch(MANAGED_ANY, m['name']):
                    raise OwnershipBlocked('cannot bind observed monitor incarnation to journal UUID')
                result.append({'id': 'unowned:' + m['name'], 'name': m['name'],
                               'physical': m['name'] == self.approved_physical and not m['disabled']
                               and m['dpmsStatus'] and m['mirrorOf'] == 'none' and bool(re.fullmatch(r'(?:DP|HDMI-A|eDP|DVI-D)-[0-9]+', m['name']))})
            return result
        if kind == 'workspaces':
            raw = self.client.query('workspaces')
            if not isinstance(raw, list): raise ValueError('invalid workspaces')
            result, ids = [], set()
            for w in raw:
                # Special/negative workspaces are not silently discarded.
                if type(w['id']) is not int or not 0 < w['id'] <= 2147483647 or w['id'] in ids:
                    raise ValueError('unsupported/ambiguous workspace')
                ids.add(w['id']); result.append({'id': w['id'], 'output': token(w['monitor'])})
            return result
        if kind == 'settings':
            raw = self.client.query('getoption', 'input:follow_mouse')
            if not isinstance(raw, dict) or raw.get('option') != 'input:follow_mouse' or type(raw.get('int')) is not int or not 0 <= raw['int'] <= 3:
                raise ValueError('invalid follow_mouse response')
            return {'input:follow_mouse': raw['int']}
        raise ValueError('unsupported normalized query')
    def require_output_ownership(self):
        self.client.require_output_ownership()
    def evacuate_remove(self, output_id, output_name):
        raise OwnershipBlocked('ownership broker required; no name-addressed fallback')
    def mutate(self, command):
        self.client.require_output_ownership()
        raise OwnershipBlocked('recovery mutations require independent continuity proof')


def presentation_placement(raw, output):
    """Place presentation below and left of interactive outputs, outside their row.

    Its right edge cannot extend the virtual desktop origin, even when the
    glasses are wider than the recovery screen. Ignore existing managed outputs
    so reloads and repeated starts produce the same placement.
    """
    token(output)
    if not re.fullmatch(r'(?:DP|HDMI-A|eDP|DVI-D)-[0-9]+', output):
        raise ValueError('presentation must be a physical connector')
    snapshot = monitors(raw)
    usable = [m for m in snapshot if not m['disabled'] and m['dpmsStatus'] and m['mirrorOf'] == 'none']
    matches = [m for m in usable if m['name'] == output]
    others = [m for m in usable if m['name'] != output and not m['name'].startswith(('XR-', 'HEADLESS-'))]
    if len(matches) != 1 or not others:
        raise ValueError('presentation requires an independent usable desktop')
    target = matches[0]
    width = math.ceil((target['height'] if target['transform'] % 2 else target['width']) / target['scale'])
    x = min(m['x'] for m in others) - width
    y = max(m['y'] + math.ceil((m['width'] if m['transform'] % 2 else m['height']) / m['scale']) for m in others) + 8192
    if not (-2147483648 <= x <= 2147483647 and -2147483648 <= y <= 2147483647):
        raise ValueError('presentation layout overflow')
    return dict(target, x=x, y=y)


def plan_outputs(existing, workspaces, run_id, desktops=None):
    token(run_id); existing = monitors(existing)
    if not isinstance(workspaces, list): raise ValueError('invalid workspace snapshot')
    used = set()
    for w in workspaces:
        if not isinstance(w, dict) or type(w.get('id')) is not int or not isinstance(w.get('name'), str):
            raise ValueError('invalid workspace snapshot')
        used.add(w['id'])
    right = max((math.ceil(m['x'] + (m['height'] if m['transform'] % 2 else m['width']) / m['scale']) for m in existing), default=0)
    y = min((m['y'] for m in existing), default=0)
    desktops = [dict(DEFAULT_DESKTOP)] * 3 if desktops is None else desktops
    if not isinstance(desktops, list) or len(desktops) != 3: raise ValueError('exactly three desktops required')
    desktops = [validate_desktop(d) for d in desktops]
    specs, x = [], right
    for index, d in enumerate(desktops):
        name = token(f'XR-{index + 1}')
        if any(m['name'] == name for m in existing): raise ValueError('output name already exists')
        lw = logical_width(d)
        if x + lw > 2147483647: raise ValueError('layout overflow')
        specs.append({'id': str(uuid.uuid4()), 'name': name, 'index': index, 'width': d['width'],
                      'height': d['height'], 'refresh_millihz': 120000, 'scale': d['scale'],
                      'x': x, 'y': y})
        x += lw
    return specs


def verify_output(raw, spec):
    matches = [m for m in raw if m.get('name') == spec['name']]
    if len(matches) != 1: raise ValueError('ok is not verified output creation')
    m = matches[0]
    for key in ('width', 'height', 'scale', 'x', 'y'):
        if m.get(key) != spec[key]: raise ValueError('output geometry readback mismatch')
    if m.get('refreshRate') != spec['refresh_millihz'] / 1000:
        raise ValueError('exact requested refresh not observed; no cadence claim')
    return m


def create_outputs(specs: list[dict], journal, hypr) -> list[str]:
    # Deliberately before prepare/mutate: a partial create could be unrecoverable.
    # Do not implement an unsafe optimistic create followed by name-based rollback.
    hypr.require_output_ownership()
    from .ownership import OwnershipBrokerClient
    if not isinstance(hypr, OwnershipBrokerClient):
        raise OwnershipBlocked('no qualified ownership backend; managed lifecycle blocked')
    return hypr.create_journaled(specs, journal)
