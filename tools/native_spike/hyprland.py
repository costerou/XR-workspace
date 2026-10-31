"""Bounded raw IPC and fail-closed normalization; no command-shell execution.

Socket2 in the qualified compositor has no transaction/creation token or replay
cursor. It cannot prove a journal UUID belongs to the currently queried monitor.
Stock transport cannot create/recover owned outputs. An explicitly constructed
OwnershipBrokerClient supplies the separate compositor-resident boundary.
"""
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

INSTANCE = re.compile(r'[0-9a-f]{40}_[0-9]+_[0-9]+\Z')
NAME = r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}'
# Stable managed-output names: Omarchy gives each its own persistent workspace block.
OWNED = r'XR-[1-9][0-9]*'
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

    def _connect(self, deadline):
        # No symlink traversal for the selected endpoint or its parents.
        for path in (self.runtime_dir, self.runtime_dir / 'hypr', self.directory):
            st = path.lstat()
            if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o022:
                raise PermissionError('unsafe Hyprland socket directory')
        path = self.directory / '.socket.sock'
        st = path.lstat()
        if not stat.S_ISSOCK(st.st_mode) or st.st_uid != os.getuid(): raise PermissionError('unsafe IPC socket')
        conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            directory_fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                conn.settimeout(max(0.001, deadline - time.monotonic()))
                conn.connect(f'/proc/self/fd/{directory_fd}/.socket.sock')
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

    def _request(self, request):
        deadline = time.monotonic() + DEADLINE
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

    def query(self, kind, argument=None):
        if kind not in QUERY: raise ValueError('raw query not allowlisted')
        if kind == 'getoption':
            if argument != 'input:follow_mouse': raise ValueError('option not allowlisted')
            suffix = ' input:follow_mouse'
        else:
            if argument is not None: raise ValueError('unexpected query argument')
            suffix = ' all' if kind == 'monitors' else ''
        return strict_json(self._request('j/' + kind + suffix))

    def mutate(self, command):
        # Internal API only. Never expose this string through the public control wire.
        # Managed output lifecycle is exclusively broker/handle addressed.
        # This host uses Lua config; legacy keyword monitor is not a fallback.
        patterns = [r'keyword input:follow_mouse [0-3]',
                    rf'dispatch moveworkspacetomonitor ([1-9][0-9]*) ({NAME})']
        if not isinstance(command, str) or len(command) > 512: raise ValueError('invalid mutation')
        if not any(re.fullmatch(p, command) for p in patterns): raise ValueError('mutation not allowlisted')
        for word in re.split(r'[ ,]', command):
            if word.startswith('XR-') and not re.fullmatch(OWNED, word): raise ValueError('invalid managed output name')
        if command.startswith('dispatch') and int(command.split()[2]) > 2147483647:
            raise ValueError('invalid workspace')
        return self._request('/' + command).decode('utf-8', errors='strict').strip()

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
                if re.fullmatch(OWNED, m['name']):
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


def plan_outputs(existing, workspaces, run_id):
    token(run_id); existing = monitors(existing)
    if not isinstance(workspaces, list): raise ValueError('invalid workspace snapshot')
    used = set()
    for w in workspaces:
        if not isinstance(w, dict) or type(w.get('id')) is not int or not isinstance(w.get('name'), str):
            raise ValueError('invalid workspace snapshot')
        used.add(w['id'])
    right = max((math.ceil(m['x'] + (m['height'] if m['transform'] % 2 else m['width']) / m['scale']) for m in existing), default=0)
    y = min((m['y'] for m in existing), default=0)
    specs = []
    for index in range(3):
        name = token(f'XR-{index + 1}')
        if any(m['name'] == name for m in existing): raise ValueError('output name already exists')
        x = right + index * 1920
        if x + 1920 > 2147483647: raise ValueError('layout overflow')
        specs.append({'id': str(uuid.uuid4()), 'name': name, 'index': index, 'width': 1920,
                      'height': 1080, 'refresh_millihz': 120000, 'scale': 1.0,
                      'x': x, 'y': y})
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
