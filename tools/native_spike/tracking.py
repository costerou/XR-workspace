"""Network-blocked head-tracking driver lifecycle (user grants 2026-10-31).

XRLinuxDriver runs in unprivileged user, network and mount namespaces (only `lo`, which stays down),
so its licence requests cannot leave the machine; it starts without a licence and publishes 3DoF
orientation over SysV IPC. It must not drive the mouse (output_mode=external_only). Lifetime is
independent of managed runs (startup is slow); stop signals only the exact process.

The profile's `tracking_driver` picks from three, and each treats failure differently, on purpose:

  xrdriver  XRLinuxDriver, /usr/bin/xrDriver, the standard.
  3dof      the 3DoF test build of it (docs/tracking-3dof-ab.md), `binary`, pinned by install.tracking_driver_path. It gets
            the same namespaces and readiness checks. It never falls back: a test run that quietly used the standard
            driver would give a wrong result. A driver outlives its run, so ensure_ready replaces one that is not the
            binary asked for, for the same reason.
  luma      our own xr-luma-driver (spike/driver), `luma`, a path from installed.json. Same network block, same SysV feed,
            same state file, so stop, status and the renderer do not care which one runs. It has no licence to fetch, so
            it needs no fake MAC and no config file. Its stderr goes to LUMA_LOG, where status() looks for 'luma: calibrated'.
            ensure_ready_luma hands over to the standard driver if it fails to calibrate.
"""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

from .ipc import private_dir

XRDRIVER, DOF3, LUMA = 'xrdriver', '3dof', 'luma'  # the profile's tracking_driver
DRIVERS = (XRDRIVER, DOF3, LUMA)
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
# Flags for the Luma driver, for what the live plan's stage G finds the glasses need. Not a general command line: only these
# pass, and each sends nothing beyond frames Transport already allows. A file with anything else fails the start, so the
# fallback's notification says why rather than the driver running with half its flags.
LUMA_FLAGS = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config') / 'viture-workspace/luma-driver-flags'
LUMA_FLAG_CHOICES = {'--start': ('stream', 'prepared', 'full'), '--read-events': None, '--drain-camera': None}
LUMA_READY = 'luma: calibrated'
LUMA_LOST = ('luma: disconnected', 'luma: calibrating')
# A fallback's two waits add up to the 120 seconds the panel promises. Luma has no licence wait: it either calibrates in
# seconds or is not going to.
LUMA_TIMEOUT = 30.0
FALLBACK_TIMEOUT = 90.0
# A reconnect takes about a second and keeps its calibration; restarting the driver would lose that and put more traffic
# on the USB link. So a start gives a driver that is not calibrated right now this long before replacing it.
LUMA_SETTLE = 5.0
# LumaWatch: a crashed driver comes back this many times in this many seconds. Past that it is not a one-off.
RESTARTS, RESTART_WINDOW = 3, 300.0
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
# The Luma Ultra's control device. The system's udev rule grants it only on ACTION=="add", which a boot with the glasses
# already plugged in does not give, so the node stays root's. The SDK then fails to open it and segfaults in
# carina_a1088_read_custom_data instead of reporting that. A replug fixes it.
GLASSES_USB = ('35ca', '1104')
USB_SYSFS = Path('/sys/bus/usb/devices')
USB_NODES = Path('/dev/bus/usb')


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


def _zombie(pid):
    try: return Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[0] == 'Z'
    except (OSError, IndexError): return False


def _ours(data):
    """The recorded process is still the exact driver we started (pid and kernel start tick), and is not just a corpse.

    The runner that started a driver is its parent and is busy with the session, so a crash leaves a zombie, tick intact.
    """
    return bool(data) and _start_tick(data['pid']) == data['start_tick'] and not _zombie(data['pid'])


def driver_alive(runtime):
    """Is the driver this app started still that process? (The incident monitor; status() rereads the whole log.)"""
    return _ours(_read_state(runtime))


def _kind(data):
    return LUMA if data.get('driver') == LUMA else XRDRIVER  # a state file from before Luma has no `driver`


def active_driver(runtime):
    """Which driver is really running: 'xrdriver', 'luma', or None. After a fallback that is not what the consent says."""
    data = _read_state(runtime)
    return _kind(data) if _ours(data) else None


def log_path(runtime):
    """The log of the driver we last started, running or not."""
    data = _read_state(runtime)
    return LUMA_LOG if data and _kind(data) == LUMA else DRIVER_LOG


def _other_drivers(exclude=None):
    """Any head tracking driver, wherever it was started from: /usr/bin, the 3DoF test build, the pinned Luma driver, a bare name."""
    found = []
    for d in Path('/proc').glob('[0-9]*'):
        try: argv = (d / 'cmdline').read_bytes().split(b'\0')
        except OSError: continue
        if int(d.name) != exclude and argv and os.path.basename(argv[0]) in (b'xrDriver', LUMA_BINARY.encode()):
            found.append(int(d.name))
    return found


def _driver_argv(binary=None):
    """Set the namespaces up as their root, then become ourselves in a child user namespace.

    The driver ends up with no capabilities, and its network belongs to the parent namespace, so it
    cannot change any of this. Each step execs in place: the recorded pid and tick are the driver's.
    """
    setup = (f'mount -t tmpfs tmpfs {RESOLVED_DIR} && ip link set lo address {FAKE_MAC} && ip address add {FAKE_ADDRESS} dev lo\n'
             f'exec unshare --user --map-user={os.getuid()} --map-group={os.getgid()} -- "$0"')
    return ['unshare', '--user', '--map-root-user', '--net', '--mount', '--', 'sh', '-c', setup, binary or DRIVER]


def _luma_flags():
    """The saved flags as argv, [] with no file. ValueError on anything but LUMA_FLAG_CHOICES."""
    try: words = LUMA_FLAGS.read_text().split()
    except FileNotFoundError: return []
    out, at = [], 0
    while at < len(words):
        flag = words[at]
        if flag not in LUMA_FLAG_CHOICES: raise ValueError(f'{LUMA_FLAGS} has an unknown flag: {flag}')
        choices = LUMA_FLAG_CHOICES[flag]
        if choices is None: out.append(flag); at += 1; continue
        if at + 1 >= len(words) or words[at + 1] not in choices: raise ValueError(f'{LUMA_FLAGS}: {flag} needs one of {", ".join(choices)}')
        out += [flag, words[at + 1]]; at += 2
    return out


def _luma_argv(binary):
    """Own user and network namespaces, as ourselves. unshare execs in place: the recorded pid and tick are the driver's."""
    return ['unshare', '--user', '--map-current-user', '--net', '--', binary, *_luma_flags()]


def _check_usb_access():
    """Refuse before the driver crashes on a node we may not open. Sysfs and access(2) only: this never opens the
    device, so it sends nothing to the glasses. No glasses at all is fine: the driver waits for them."""
    for device in USB_SYSFS.glob('*'):
        try:
            if (device / 'idVendor').read_text().strip() != GLASSES_USB[0] or (device / 'idProduct').read_text().strip() != GLASSES_USB[1]: continue
            node = USB_NODES / f"{int((device / 'busnum').read_text()):03d}" / f"{int((device / 'devnum').read_text()):03d}"
        except (OSError, ValueError):
            continue
        if not os.access(node, os.R_OK | os.W_OK):
            raise ValueError(f'no permission to open the glasses ({node}), which happens after booting with them plugged in: '
                             'unplug them, plug them back in, and start again')


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
    driver = _kind(data)
    path, ready, lost = (LUMA_LOG, LUMA_READY, LUMA_LOST) if driver == LUMA else (DRIVER_LOG, READY, LOST)
    log = path.read_bytes() if path.exists() else b''
    current = log[data.get('log_offset', 0):].decode(errors='replace')
    calibrated = current.rfind(ready) > max(current.rfind(marker) for marker in lost)
    return {'running': True, 'pid': pid, 'network_isolated': _network_isolated(pid), 'calibrated': calibrated,
            'driver': driver, 'binary': data.get('binary') or DRIVER}  # no binary: a state file from before the choice existed


def ensure_ready(runtime, timeout=120.0, luma=None, binary=None):
    """Reuse a calibrated isolated driver of the kind asked for, or restart ours: disconnected, or the
    other kind, or another binary of the same kind, such as an older install's or the 3DoF test build.

    Only the PID/start-tick recorded by this app can be stopped. A separate
    driver is never killed; start() refuses to compete with it.
    """
    now = status(runtime)
    target = luma or binary or DRIVER
    wanted = lambda: now['network_isolated'] and now['driver'] == (LUMA if luma else XRDRIVER) and now['binary'] == target
    if luma and now['running'] and wanted():  # the right driver, perhaps mid-reconnect: leave it be if it comes back
        deadline = time.monotonic() + LUMA_SETTLE
        while now['running'] and not now['calibrated'] and time.monotonic() < deadline:
            time.sleep(0.1); now = status(runtime)
    if now['running'] and wanted() and now['calibrated']:
        return now
    if now['running']:
        stop(runtime)
    return start(runtime, timeout=timeout, luma=luma, binary=binary)


def ensure_ready_luma(runtime, luma):
    """The Luma driver, or the standard XRLinuxDriver if it fails: the status, with `fallback` saying why when it did.

    Only a failure hands over. A cancel (KeyboardInterrupt, the panel's Stop) goes straight through.
    """
    try: return ensure_ready(runtime, timeout=LUMA_TIMEOUT, luma=luma)
    except (OSError, ValueError) as exc: reason = str(exc) or type(exc).__name__
    try: return {**ensure_ready(runtime, timeout=FALLBACK_TIMEOUT), 'fallback': reason}
    except (OSError, ValueError) as exc:
        raise ValueError(f'the XR Luma driver failed ({reason}) and so did XRLinuxDriver: {exc}') from None


def start(runtime, timeout=120.0, luma=None, binary=None):
    """Start XRLinuxDriver (`binary`, default the standard one), or with `luma` (the pinned binary's path) the Luma driver,
    and wait for it to calibrate."""
    if _ours(_read_state(runtime)): raise ValueError('tracking already running')
    if luma is None: _check_config()
    if _other_drivers(): raise ValueError('another head tracking driver is running; refusing to compete for the glasses')
    _check_usb_access()
    if luma is None:
        offset, output, argv = DRIVER_LOG.stat().st_size if DRIVER_LOG.exists() else 0, subprocess.DEVNULL, _driver_argv(binary)
    else:
        argv = _luma_argv(luma)  # first: a bad flags file should open no log
        offset, output = _open_luma_log()
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
    with os.fdopen(fd, 'w') as f: json.dump({'pid': child.pid, 'start_tick': tick, 'log_offset': offset, 'driver': LUMA if luma else XRDRIVER, 'binary': luma or binary or DRIVER}, f)
    try:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            # First: a crashed driver is a zombie until reaped, so what follows would say only "exited", or, with no namespace
            # left to read, "not network-isolated". This says how it ended.
            if child.poll() is not None:
                raise ValueError(f'tracking driver exited during startup (status {child.returncode}); see {LUMA_LOG if luma else DRIVER_LOG}')
            now = status(runtime)
            if not now['running']:
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


class LumaWatch(threading.Thread):
    """Brings a crashed Luma driver back during a session, RESTARTS times per RESTART_WINDOW, then says so through `notify`.

    Only for a crash: a stop() removes the state file and a swap changes its driver, and either ends the watch.
    The renderer holds its last pose meanwhile and picks up the feed's segments again, which a new driver reuses.
    """
    def __init__(self, runtime, luma, notify, poll=0.5):
        super().__init__(name='luma-watch', daemon=True)
        self.runtime, self.luma, self.notify, self.poll = runtime, luma, notify, poll
        self.stopping, self.restarts = threading.Event(), []

    def run(self):
        while not self.stopping.wait(self.poll):
            data = _read_state(self.runtime)
            if data is None or _kind(data) != LUMA: return
            if _ours(data): continue
            now = time.monotonic()
            self.restarts = [at for at in self.restarts if now - at < RESTART_WINDOW] + [now]
            if len(self.restarts) > RESTARTS:
                self.notify(f'The XR Luma driver keeps crashing, so head tracking has stopped for this session. See {LUMA_LOG}.')
                return
            print(f'xr-workspace: the XR Luma driver crashed; restart {len(self.restarts)} of {RESTARTS}', file=sys.stderr, flush=True)
            try: ensure_ready(self.runtime, timeout=LUMA_TIMEOUT, luma=self.luma)
            except (OSError, ValueError) as exc: print(f'xr-workspace: the restart failed: {exc}', file=sys.stderr, flush=True)

    def finish(self):
        self.stopping.set()
        self.join(1.0)  # a restart in progress is left to finish: the driver outlives a run anyway


def stop(runtime, timeout=5.0):
    data = _read_state(runtime)
    if not _ours(data):
        return {'stopped': False}
    pid = data['pid']
    def still_there():
        try: os.waitpid(pid, os.WNOHANG)  # our own child (a fallback stops what it started) is a zombie, tick intact, until reaped
        except ChildProcessError: pass
        return _start_tick(pid) == data['start_tick']
    # Before the signal: to LumaWatch, a dead driver whose state file is still there is a crash.
    try: _state(runtime).unlink()
    except FileNotFoundError: pass
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + timeout
    while still_there() and time.monotonic() < deadline: time.sleep(0.05)
    if still_there(): os.kill(pid, signal.SIGKILL)
    still_there()
    return {'stopped': True}
