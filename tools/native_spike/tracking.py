"""Network-blocked head-tracking driver lifecycle (user grants 2026-10-31).

XRLinuxDriver runs in unprivileged user, network and mount namespaces (only `lo`, which stays down),
so its licence requests cannot leave the machine; it starts without a licence and publishes 3DoF
orientation over SysV IPC. It must not drive the mouse (output_mode=external_only). Lifetime is
independent of managed runs (startup is slow); stop signals only the exact process.

Our own xr-luma-driver (spike/driver) is the other choice, the profile's `tracking_driver`. It takes
the same place: same network-blocked namespaces, same SysV feed, same state file, so stop, status and
the renderer do not care which one runs. It has no licence to fetch, so it needs no fake MAC and no
config file. Its stderr goes to LUMA_LOG, where status() looks for 'luma: calibrated'. It always runs
from the pinned copy (`luma`, a path from installed.json) and ensure_ready_luma hands over to
XRLinuxDriver if it fails to calibrate.
"""
import json
import os
from pathlib import Path
import signal
import subprocess
import time

from .ipc import private_dir

XRDRIVER, LUMA = 'xrdriver', 'luma'  # the profile's tracking_driver
DRIVERS = (XRDRIVER, LUMA)
SCOPES = ('xrlinuxdriver', 'luma')  # a consent's `tracking`: what the click agreed to run. Both feed the renderer the same way
DRIVER = '/usr/bin/xrDriver'
DRIVER_LOG = Path.home() / '.local/state/xr_driver/driver.log'
DRIVER_CONFIG = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config') / 'xr_driver/config.ini'
READY = 'Device calibration complete'
# Readiness belongs to the latest connection, not any earlier calibration.
LOST = ('Retrying driver connection', 'Waiting on device calibration', 'Failed to send USB command')
LUMA_BINARY = 'xr-luma-driver'
LUMA_LOG = Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local/state') / 'viture-workspace/luma-driver.log'
LUMA_LOG_MAX = 1 << 20  # bytes; a start moves a bigger log aside, keeping one
LUMA_READY = 'luma: calibrated'
LUMA_LOST = ('luma: disconnected', 'luma: calibrating')
# A fallback's two waits add up to the 120 seconds the panel promises. Luma has no licence wait: it either calibrates in
# seconds or is not going to.
LUMA_TIMEOUT = 30.0
FALLBACK_TIMEOUT = 90.0
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
        if int(d.name) != exclude and argv and (argv[0] in (DRIVER.encode(), b'xrDriver') or os.path.basename(argv[0]) == LUMA_BINARY.encode()):
            found.append(int(d.name))
    return found


def _driver_argv():
    """Set the namespaces up as their root, then become ourselves in a child user namespace.

    The driver ends up with no capabilities, and its network belongs to the parent namespace, so it
    cannot change any of this. Each step execs in place: the recorded pid and tick are the driver's.
    """
    setup = (f'mount -t tmpfs tmpfs {RESOLVED_DIR} && ip link set lo address {FAKE_MAC} && ip address add {FAKE_ADDRESS} dev lo\n'
             f'exec unshare --user --map-user={os.getuid()} --map-group={os.getgid()} -- "$0"')
    return ['unshare', '--user', '--map-root-user', '--net', '--mount', '--', 'sh', '-c', setup, DRIVER]


def _luma_argv(binary):
    """Own user and network namespaces, as ourselves. unshare execs in place: the recorded pid and tick are the driver's."""
    return ['unshare', '--user', '--map-current-user', '--net', '--', binary]


def _open_luma_log():
    """(where this start begins in the log, the log open for appending)."""
    LUMA_LOG.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        if LUMA_LOG.stat().st_size > LUMA_LOG_MAX: os.replace(LUMA_LOG, LUMA_LOG.with_suffix('.log.1'))
    except FileNotFoundError: pass
    fd = os.open(LUMA_LOG, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    return os.fstat(fd).st_size, fd


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
    driver = LUMA if data.get('driver') == LUMA else XRDRIVER  # a state file from before Luma has no `driver`
    path, ready, lost = (LUMA_LOG, LUMA_READY, LUMA_LOST) if driver == LUMA else (DRIVER_LOG, READY, LOST)
    log = path.read_bytes() if path.exists() else b''
    current = log[data.get('log_offset', 0):].decode(errors='replace')
    calibrated = current.rfind(ready) > max(current.rfind(marker) for marker in lost)
    return {'running': True, 'pid': pid, 'network_isolated': _network_isolated(pid), 'calibrated': calibrated,
            'driver': driver, 'binary': data.get('binary')}


def ensure_ready(runtime, timeout=120.0, luma=None):
    """Reuse a calibrated isolated driver of the kind asked for, or restart ours: disconnected, or the
    other kind, or (`luma`, the pinned binary) an older install's.

    Only the PID/start-tick recorded by this app can be stopped. A separate
    driver is never killed; start() refuses to compete with it.
    """
    now = status(runtime)
    if (now['running'] and now['network_isolated'] and now['calibrated']
            and now['driver'] == (LUMA if luma else XRDRIVER) and now['binary'] == luma):
        return now
    if now['running']:
        stop(runtime)
    return start(runtime, timeout=timeout, luma=luma)


def ensure_ready_luma(runtime, luma):
    """The Luma driver, or XRLinuxDriver if it fails: the status, with `fallback` saying why when it did.

    Only a failure hands over. A cancel (KeyboardInterrupt, the panel's Stop) goes straight through.
    """
    try: return ensure_ready(runtime, timeout=LUMA_TIMEOUT, luma=luma)
    except (OSError, ValueError) as exc: reason = str(exc) or type(exc).__name__
    try: return {**ensure_ready(runtime, timeout=FALLBACK_TIMEOUT), 'fallback': reason}
    except (OSError, ValueError) as exc:
        raise ValueError(f'the XR Luma driver failed ({reason}) and so did XRLinuxDriver: {exc}') from None


def start(runtime, timeout=120.0, luma=None):
    """Start XRLinuxDriver, or with `luma` (the pinned binary's path) the Luma driver, and wait for it to calibrate."""
    if _ours(_read_state(runtime)): raise ValueError('tracking already running')
    if luma is None: _check_config()
    if _other_drivers(): raise ValueError('another head tracking driver is running; refusing to compete for the glasses')
    if luma is None:
        offset, output, argv = DRIVER_LOG.stat().st_size if DRIVER_LOG.exists() else 0, subprocess.DEVNULL, _driver_argv()
    else:
        offset, output = _open_luma_log(); argv = _luma_argv(luma)
    try:
        child = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=output, stderr=output, start_new_session=True, close_fds=True)
    finally:
        if luma is not None: os.close(output)
    deadline = time.monotonic() + 2
    tick = None
    while tick is None and time.monotonic() < deadline:  # unshare execs the driver in the same pid
        tick = _start_tick(child.pid); time.sleep(0.02)
    path = _state(runtime)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as f: json.dump({'pid': child.pid, 'start_tick': tick, 'log_offset': offset, 'driver': LUMA if luma else XRDRIVER, 'binary': luma}, f)
    try:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            now = status(runtime)
            # Asked first: an exited child stays a zombie until reaped, which reads as running and not isolated.
            if not now['running'] or child.poll() is not None:
                raise ValueError('tracking driver exited during startup')
            if not now['network_isolated']:
                raise ValueError('tracking driver is not network-isolated; stopped')
            if now['calibrated']: return now
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
    def still_there():
        try: os.waitpid(pid, os.WNOHANG)  # our own child (a fallback stops what it started) is a zombie, tick intact, until reaped
        except ChildProcessError: pass
        return _start_tick(pid) == data['start_tick']
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + timeout
    while still_there() and time.monotonic() < deadline: time.sleep(0.05)
    if still_there(): os.kill(pid, signal.SIGKILL)
    still_there()
    try: _state(runtime).unlink()
    except FileNotFoundError: pass
    return {'stopped': True}
