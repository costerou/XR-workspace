"""Network-blocked XRLinuxDriver lifecycle for head tracking (user grants 2026-10-31).

The driver runs in unprivileged user, network and mount namespaces (only `lo`, which stays down), so
its licence requests cannot leave the machine; it starts without a licence and publishes 3DoF
orientation over SysV IPC. It must not drive the mouse (output_mode=external_only). Lifetime is
independent of managed runs (startup is slow); stop signals only the exact process.
"""
import json
import os
from pathlib import Path
import signal
import subprocess
import time

from .ipc import private_dir

DRIVER = '/usr/bin/xrDriver'
DRIVER_LOG = Path.home() / '.local/state/xr_driver/driver.log'
DRIVER_CONFIG = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config') / 'xr_driver/config.ini'
READY = 'Device calibration complete'
# Without a MAC to hash into a hardware id the driver retries for 30 s, then carries on. A made-up MAC
# on `lo` ends that wait, and `lo` stays down, so there are still no routes. The driver only looks at
# interfaces with an IPv4 address. It must not be 127.0.0.1: an address on a down interface swallows
# traffic instead of refusing it, and name lookups, with no resolver left, try 127.0.0.1 and would
# sit out their timeouts. This one is from a range kept for documentation.
FAKE_MAC = '02:00:00:00:00:01'
FAKE_ADDRESS = '192.0.2.1/32'
# With an id the driver tries to fetch a licence at every start and connect, and curl's name lookup
# would reach the real DNS through systemd-resolved's socket, which is a file: a network namespace
# does not cover it. So the MAC is only set once that directory is hidden. If any step fails the
# driver gets no id, never tries the licence server, and just starts 30 s later.
RESOLVED_DIR = '/run/systemd/resolve'


def _start_tick(pid):
    try: return Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[19]
    except (OSError, IndexError): return None


def _state(runtime):
    directory = Path(runtime) / 'viture-workspace'
    os.close(private_dir(directory, create=True))
    return directory / 'tracking.json'


def _read_state(runtime):
    try:
        data = json.loads(_state(runtime).read_text())
        if isinstance(data, dict) and type(data.get('pid')) is int and isinstance(data.get('start_tick'), str): return data
    except (OSError, ValueError):
        pass
    return None


def _ours(data):
    """The recorded process is still the exact driver we started (pid and kernel start tick)."""
    return bool(data) and _start_tick(data['pid']) == data['start_tick']


def driver_alive(runtime):
    """Is the driver this app started still that process? (The incident monitor; status() rereads the whole log.)"""
    return _ours(_read_state(runtime))


def _other_drivers(exclude=None):
    found = []
    for d in Path('/proc').glob('[0-9]*'):
        try: argv = (d / 'cmdline').read_bytes().split(b'\0')
        except OSError: continue
        if int(d.name) != exclude and argv and argv[0] in (DRIVER.encode(), b'xrDriver'): found.append(int(d.name))
    return found


def _driver_argv():
    """Set the namespaces up as their root, then become ourselves in a child user namespace.

    The driver ends up with no capabilities, and its network belongs to the parent namespace, so it
    cannot change any of this. Each step execs in place: the recorded pid and tick are the driver's.
    """
    setup = (f'mount -t tmpfs tmpfs {RESOLVED_DIR} && ip link set lo address {FAKE_MAC} && ip address add {FAKE_ADDRESS} dev lo\n'
             f'exec unshare --user --map-user={os.getuid()} --map-group={os.getgid()} -- "$0"')
    return ['unshare', '--user', '--map-root-user', '--net', '--mount', '--', 'sh', '-c', setup, DRIVER]


def _interfaces(pid):
    return [line.split(':')[0].strip() for line in Path(f'/proc/{pid}/net/dev').read_text().splitlines()[2:]]


def _network_isolated(pid):
    """The process has its own network namespace and `lo` is all that is in it, so nothing can leave."""
    try: return os.readlink(f'/proc/{pid}/ns/net') != os.readlink('/proc/self/ns/net') and _interfaces(pid) == ['lo']
    except OSError: return False


def _check_config():
    try: lines = dict(l.split('=', 1) for l in DRIVER_CONFIG.read_text().splitlines() if '=' in l and not l.startswith('#'))
    except OSError: raise ValueError('XRLinuxDriver config missing')
    if lines.get('output_mode', '').strip() != 'external_only' or lines.get('disabled', '').strip() != 'false':
        raise ValueError('XRLinuxDriver config must be disabled=false and output_mode=external_only (no mouse/joystick)')


def status(runtime):
    data = _read_state(runtime)
    if not _ours(data): return {'running': False}
    pid = data['pid']
    log = DRIVER_LOG.read_bytes() if DRIVER_LOG.exists() else b''
    current = log[data.get('log_offset', 0):].decode(errors='replace')
    # Readiness belongs to the latest connection, not any earlier calibration.
    invalidated = max(current.rfind(marker) for marker in (
        'Retrying driver connection', 'Waiting on device calibration', 'Failed to send USB command'))
    calibrated = current.rfind(READY) > invalidated
    return {'running': True, 'pid': pid, 'network_isolated': _network_isolated(pid), 'calibrated': calibrated}


def ensure_ready(runtime, timeout=120.0):
    """Reuse a calibrated isolated driver, or restart our disconnected driver.

    Only the PID/start-tick recorded by this app can be stopped. A separate
    driver is never killed; start() refuses to compete with it.
    """
    now = status(runtime)
    if now['running'] and now['network_isolated'] and now['calibrated']:
        return now
    if now['running']:
        stop(runtime)
    return start(runtime, timeout=timeout)


def start(runtime, timeout=120.0):
    if _ours(_read_state(runtime)): raise ValueError('tracking already running')
    _check_config()
    if _other_drivers(): raise ValueError('another XRLinuxDriver is running; refusing to compete for the glasses')
    offset = DRIVER_LOG.stat().st_size if DRIVER_LOG.exists() else 0
    child = subprocess.Popen(_driver_argv(), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True, close_fds=True)
    deadline = time.monotonic() + 2
    tick = None
    while tick is None and time.monotonic() < deadline:  # unshare execs the driver in the same pid
        tick = _start_tick(child.pid); time.sleep(0.02)
    path = _state(runtime)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as f: json.dump({'pid': child.pid, 'start_tick': tick, 'log_offset': offset}, f)
    try:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            now = status(runtime)
            if not now['running']:
                raise ValueError('tracking driver exited during startup')
            if not now['network_isolated']:
                raise ValueError('tracking driver is not network-isolated; stopped')
            if now['calibrated']: return now
            if child.poll() is not None: break
            time.sleep(0.2)
        raise ValueError('tracking driver did not calibrate in time (keep the glasses still while it starts)')
    except BaseException:
        # Cancellation must not leave a child keeping the transient unit alive.
        stop(runtime)
        raise


def stop(runtime, timeout=5.0):
    data = _read_state(runtime)
    if not _ours(data):
        return {'stopped': False}
    pid = data['pid']
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + timeout
    while _start_tick(pid) == data['start_tick'] and time.monotonic() < deadline: time.sleep(0.05)
    if _start_tick(pid) == data['start_tick']: os.kill(pid, signal.SIGKILL)
    try: os.waitpid(pid, os.WNOHANG)
    except ChildProcessError: pass
    try: _state(runtime).unlink()
    except FileNotFoundError: pass
    return {'stopped': True}
