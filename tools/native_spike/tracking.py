"""Network-blocked head-tracking driver lifecycle (user grants 2026-10-31).

XRLinuxDriver runs in unprivileged user, network and mount namespaces (only `lo`, which stays down),
so its licence requests cannot leave the machine; it starts without a licence and publishes 3DoF
orientation over SysV IPC. It must not drive the mouse (output_mode=external_only). Lifetime is
independent of managed runs (startup is slow); stop signals only the exact process.

Which driver runs is decided per model, not chosen (docs/own-drivers-default.md, default_driver below):

  luma      our own xr-luma-driver (spike/driver), the Luma Ultra's, `luma`, a path from installed.json. Same network block,
            same SysV feed, same state file, so stop, status and the renderer do not care which one runs. It has no licence
            to fetch, so it needs no fake MAC and no config file. Its stderr goes to LUMA_LOG, where status() looks for
            'luma: calibrated'. Nothing falls back: if it does not calibrate, ensure_ready_luma says so and the start ends.
  xrdriver  XRLinuxDriver, /usr/bin/xrDriver: every model our drivers do not support, automatically (the Pro 2 excepted:
            tools/xr_controller/pro2_route.py is its route). On a Luma Ultra only through the hidden recovery setting.
  3dof      the 3DoF test build of it (docs/tracking-3dof-ab.md), `binary`, pinned by install.tracking_driver_path, also only
            through that setting. It gets the same namespaces and readiness checks. A driver outlives its run, so
            ensure_ready replaces one that is not the binary asked for: a test that quietly used another would be wrong.
"""
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time

from .ipc import private_dir, read_private_json

XRDRIVER, DOF3, LUMA = 'xrdriver', '3dof', 'luma'  # the profile's tracking_driver
LUMA_CPP = 'luma-cpp'  # an override, not a driver kind: the Luma driver still, the C++ build instead of the pinned Rust one
DRIVERS = (XRDRIVER, DOF3, LUMA)
SCOPES = ('xrlinuxdriver', 'luma')  # a consent's `tracking`: what the click agreed to run. Both feed the renderer the same way
PRO2_TRACKING = 'pro2-sdk'  # a Pro 2 consent's `tracking` (panel_consent.py): its bridge, no driver process of this module
# Which driver a model runs. There is no choice in the panel: our own where we have one, XRLinuxDriver where we do not. The Pro 2 is
# not here because it does not run a driver of this module at all (_check_provider_compatibility, pro2_route.py).
LUMA_MODEL = 'luma-ultra'
# The one hidden way back to XRLinuxDriver: `python -m tools.xr_controller tracking-override set xrdriver` (or 3dof), Luma Ultra
# only, and the panel says when it is on. Anything but a valid file is no override: it must never be how our driver gets replaced.
# `set luma-cpp` is the way back from the Rust Luma driver to the C++ one (install.load_installed), with no XRLinuxDriver.
OVERRIDE_FILE = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config') / 'viture-workspace/tracking-override.json'
XRDRIVER_OVERRIDES = (XRDRIVER, DOF3)  # the overrides that run XRLinuxDriver instead of our driver
OVERRIDES = (*XRDRIVER_OVERRIDES, LUMA_CPP)
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
# pass, and each sends nothing beyond frames Transport already allows. A file with anything else fails the start, and the
# failure says why, rather than the driver running with half its flags. --pose-seqlock sends nothing at all: it also writes
# the lock-free pose channel, /dev/shm/xr-luma-pose, which the renderer then reads instead of the mutex feed.
LUMA_FLAGS = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config') / 'viture-workspace/luma-driver-flags'
LUMA_FLAG_CHOICES = {'--start': ('stream', 'prepared', 'full'), '--read-events': None, '--drain-camera': None,
                     '--pose-seqlock': None}
LUMA_READY = 'luma: calibrated'
LUMA_LOST = ('luma: disconnected', 'luma: calibrating')
# Luma has no licence wait: it either calibrates in seconds or is not going to.
LUMA_TIMEOUT = 30.0
# A reconnect takes about a second and keeps its calibration; restarting the driver would lose that and put more traffic
# on the USB link. So a start gives a driver that is not calibrated right now this long before replacing it.
LUMA_SETTLE = 5.0
# LumaWatch: a crashed driver comes back this many times in this many seconds. Past that it is not a one-off.
RESTARTS, RESTART_WINDOW = 3, 300.0
# Shown to Cody as written, in the panel and as an incident report's likely cause.
CRASHED = ("The glasses' head tracking couldn't connect and the tracking driver crashed. "
           "Unplug the glasses' USB, wait 10 seconds, plug it back in and try again.")
USB_DENIED = "XR can't open the glasses (USB permission). Unplug and replug the glasses' USB, then try again."
LUMA_MISSING = "Our head tracking driver is not installed, so XR can't start with head tracking. Install it, or turn Head tracking off in Startup options."
# The end of a start: nothing has moved yet at this point, so the windows are where they were.
LUMA_FAILED = ("Head tracking did not start: {reason}. XR stopped here and your windows stayed where they were. "
               "Keep the glasses still and press Start again. If it fails again, unplug the glasses' USB, wait 10 seconds and plug it back in.")
# What the driver logs when it dies trying to reach the glasses; any one of them is enough.
CRASH_LINES = ('Cannot open device', 'jcx start failed', 'Failed to send USB command - MsgID: 0x100B', 'Segmentation fault occurred')
# How long a run holds its view with no head tracking before it gives up (supervisor.py). The file is optional and only says
# {"lost_timeout_s": seconds}: anything else is logged and the default stands, because a typo must not cost a session.
RECOVERY_CONFIG = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config') / 'viture-workspace/tracking.json'
LOST_TIMEOUT_S = 60.0
LOST_TIMEOUT_MAX_S = 3600.0
# A driver found uncalibrated has this long to finish reconnecting by itself (about 3 s) and calibrating (about 15 s) before
# it is restarted.
RECONNECT_WAIT_S = 30.0
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
# already plugged in does not give, and logind can miss the ACL when the glasses re-enumerate during a session crash. The
# node then stays root's, and the SDK fails to open it and segfaults in carina_a1088_read_custom_data instead of reporting
# that. A replug fixes it, so look before launching a driver.
GLASSES_USB = ('35ca', '1104')
SYS_USB = Path('/sys/bus/usb/devices')
DEV_USB = Path('/dev/bus/usb')
QUIET_SETTINGS = DRIVER_CONFIG.parent.parent / 'viture-workspace' / 'quiet-driver.json'
QUIET_DEFAULTS = {'enabled': True, 'dof3': True, 'quiet_polls': True, 'reconnect_hold_ms': 20000}


def default_driver(model):
    """The profile's `tracking_driver` for a model: what a start runs, with no override."""
    return LUMA if model == LUMA_MODEL else XRDRIVER


def read_override(path=None):
    """The saved override, None with no file. ValueError when the file cannot be trusted."""
    path = Path(path or OVERRIDE_FILE)
    if not path.exists(): return None
    data = read_private_json(path)
    if not isinstance(data, dict) or set(data) != {'schema', 'driver'} or data['schema'] != 1 or data['driver'] not in OVERRIDES:
        raise ValueError(f'{path.name} is invalid')
    return data['driver']


def load_override(path=None):
    """read_override for a start and the panel, which never raises: a broken file is no override."""
    try: return read_override(path)
    except (OSError, ValueError): return None


def save_override(driver, path=None):
    if driver not in OVERRIDES: raise ValueError('override must be one of ' + ', '.join(OVERRIDES))
    path = Path(path or OVERRIDE_FILE)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.' + path.name + '.', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w') as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(dict(schema=1, driver=driver), stream); stream.flush(); os.fsync(stream.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True); raise


def clear_override(path=None):
    Path(path or OVERRIDE_FILE).unlink(missing_ok=True)


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


def _open_luma_log():
    """(where this start begins in the log, the log open for appending)."""
    LUMA_LOG.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        if LUMA_LOG.stat().st_size > LUMA_LOG_MAX: os.replace(LUMA_LOG, LUMA_LOG.with_suffix('.log.1'))
    except FileNotFoundError: pass
    fd = os.open(LUMA_LOG, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    return os.fstat(fd).st_size, fd


def _shim_settings():
    """The quiet driver's settings (docs/usb-drops.md), each one undoable alone. A broken file is the defaults, not the end."""
    try: saved = json.loads(QUIET_SETTINGS.read_text())
    except (OSError, ValueError): saved = {}
    settings = dict(QUIET_DEFAULTS)
    if isinstance(saved, dict): settings.update({k: saved[k] for k in QUIET_DEFAULTS if type(saved.get(k)) is type(QUIET_DEFAULTS[k])})
    return settings


def _driver_env(data_home=None):
    """The driver's environment: ours, with the quiet driver preloaded if the install carries one that still matches its
    pin. No shim or a changed one: the plain driver, as before."""
    env = dict(os.environ)
    settings = _shim_settings()
    if not settings['enabled']: return env
    from tools.xr_controller.install import load_shim
    try: shim = load_shim(data_home or Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local/share'))
    except (OSError, ValueError): shim = None
    if shim is None: return env
    env['LD_PRELOAD'] = shim
    if settings['dof3']: env['XR_QUIET_DOF'] = '3'
    if settings['quiet_polls']: env['XR_QUIET_POLLS'] = '1'
    if settings['reconnect_hold_ms'] > 0: env['XR_QUIET_RECONNECT_HOLD_MS'] = str(settings['reconnect_hold_ms'])
    return env


def _interfaces(pid):
    return [line.split(':')[0].strip() for line in Path(f'/proc/{pid}/net/dev').read_text().splitlines()[2:]]


def _network_isolated(pid):
    """The process has its own network namespace and `lo` is all that is in it, so nothing can leave."""
    try: return os.readlink(f'/proc/{pid}/ns/net') != os.readlink('/proc/self/ns/net') and _interfaces(pid) == ['lo']
    except OSError: return False


def _glasses_nodes():
    """The usbfs node of each attached 35ca:1104. Interfaces and hubs without these files are skipped."""
    nodes = []
    for device in SYS_USB.glob('*'):
        try:
            ids = tuple((device / name).read_text().strip().lower() for name in ('idVendor', 'idProduct'))
            if ids == GLASSES_USB:
                nodes.append(DEV_USB / f"{int((device / 'busnum').read_text()):03d}" / f"{int((device / 'devnum').read_text()):03d}")
        except (OSError, ValueError):
            continue
    return nodes


class UnsupportedTrackingProvider(ValueError):
    """No approved provider for this attached model; never eligible for fallback."""


def _check_provider_compatibility():
    """Passive provider safety gate, separate from model/profile detection.

    A Pro 2 runs its own pinned SDK bridge (tools/xr_controller/pro2_route.py) and
    never a driver from this module. Reject PID or product evidence, including
    conflicts, before reusing or starting one. Never open usbfs/hidraw here. Other
    models retain their existing behavior.
    """
    for device in SYS_USB.glob('*'):
        try:
            vendor = (device / 'idVendor').read_text().strip().lower()
            if vendor != '35ca': continue
        except OSError:
            continue
        try: product_id = (device / 'idProduct').read_text().strip().lower()
        except OSError: product_id = ''
        try: product = (device / 'product').read_text().strip()
        except OSError: product = ''
        if product_id == '1301' or product == 'VITURE PRO 2 XR Glasses':
            raise UnsupportedTrackingProvider(
                'VITURE PRO 2 XR Glasses (35ca:1301): this tracking driver is not for these glasses. '
                'They run their own pinned SDK bridge, or nothing.')


def _check_usb_access():
    """Refuse before the driver crashes on a node we may not open. Sysfs and access(2) only: this never opens the device,
    so it sends nothing to the glasses. Glasses that are not attached are left to the driver, which waits for them."""
    if not all(os.access(node, os.R_OK | os.W_OK) for node in _glasses_nodes()): raise ValueError(USB_DENIED)


def _check_config():
    try: lines = dict(l.split('=', 1) for l in DRIVER_CONFIG.read_text().splitlines() if '=' in l and not l.startswith('#'))
    except OSError: raise ValueError('XRLinuxDriver config missing')
    if lines.get('output_mode', '').strip() != 'external_only' or lines.get('disabled', '').strip() != 'false':
        raise ValueError('XRLinuxDriver config must be disabled=false and output_mode=external_only (no mouse/joystick)')


def _exit_failure(offset, log, code=None):
    """Why a driver that is gone failed, from what it logged since this start. Bytes first: the log is not plain text."""
    text = log.read_bytes() if log.exists() else b''
    current = text[offset:].decode(errors='replace')
    if any(line in current for line in CRASH_LINES): return CRASHED
    return 'tracking driver exited during startup' + (f' (status {code}); see {log}' if code is not None else '')


def _luma_waiting(offset):
    """What the Luma driver last said it was waiting for since this start: ', last waiting on device-not-found', or nothing."""
    try: text = LUMA_LOG.read_bytes()[offset:].decode(errors='replace')
    except OSError: return ''
    found = re.findall(r'luma: waiting reason=(\S+)', text)
    return f', last waiting on {found[-1]}' if found else ''


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


def lost_timeout():
    """RECOVERY_CONFIG's limit in seconds, else LOST_TIMEOUT_S. Never raises."""
    try: text = RECOVERY_CONFIG.read_text()
    except FileNotFoundError: return LOST_TIMEOUT_S
    except OSError as exc:
        print(f'xr-workspace: tracking: {RECOVERY_CONFIG.name} not read: {exc}', file=sys.stderr)
        return LOST_TIMEOUT_S
    try:
        data = json.loads(text)
        value = data['lost_timeout_s'] if isinstance(data, dict) and set(data) == {'lost_timeout_s'} else None
        if type(value) in (int, float) and 0 < value <= LOST_TIMEOUT_MAX_S: return float(value)
    except ValueError:
        pass
    print(f'xr-workspace: tracking: {RECOVERY_CONFIG.name} needs {{"lost_timeout_s": 1..{LOST_TIMEOUT_MAX_S:.0f}}}; '
          f'using {LOST_TIMEOUT_S:.0f} s', file=sys.stderr)
    return LOST_TIMEOUT_S


def _wait_for_reconnect(runtime, seconds):
    """A driver that lost the glasses gets them back by itself, so leave it to: 18 of 53 drops came within 30 s of the one
    before, and a restart here would land on glasses about to drop again. The newest status."""
    deadline = time.monotonic() + seconds
    while True:
        now = status(runtime)
        if not (now['running'] and now['network_isolated']) or now['calibrated'] or time.monotonic() >= deadline: return now
        time.sleep(0.2)


def ensure_ready(runtime, timeout=120.0, luma=None, binary=None):
    """Reuse a calibrated isolated driver of the kind asked for, give a reconnecting one time to finish, or restart ours: disconnected, or the
    other kind, or another binary of the same kind, such as an older install's or the 3DoF test build.

    Only the PID/start-tick recorded by this app can be stopped. A separate
    driver is never killed; start() refuses to compete with it.
    """
    _check_provider_compatibility()
    now = status(runtime)
    target = luma or binary or DRIVER
    wanted = lambda: now['network_isolated'] and now['driver'] == (LUMA if luma else XRDRIVER) and now['binary'] == target
    if now['running'] and wanted() and not now['calibrated']: now = _wait_for_reconnect(runtime, LUMA_SETTLE if luma else RECONNECT_WAIT_S)
    if now['running'] and wanted() and now['calibrated']:
        return now
    if now['running']:
        stop(runtime)
    return start(runtime, timeout=timeout, luma=luma, binary=binary)


def restart_same(runtime):
    """The driver that was running when it died, by the record it left, so a run never changes driver by itself: a 3DoF test
    that came back on the standard driver would give a false result. No record means someone stopped it, so nothing starts."""
    data = _read_state(runtime)
    if data is None: raise ValueError('no tracking driver on record')
    binary = data.get('binary')
    return ensure_ready(runtime, luma=binary) if _kind(data) == LUMA else ensure_ready(runtime, binary=binary)


def ensure_ready_luma(runtime, luma):
    """The Luma driver, calibrated, or a ValueError that says why in words for Cody. Nothing takes over: Cody decided there is no
    fallback to XRLinuxDriver. A cancel (KeyboardInterrupt, the panel's Stop) goes straight through.
    """
    _check_provider_compatibility()
    try: return ensure_ready(runtime, timeout=LUMA_TIMEOUT, luma=luma)
    except UnsupportedTrackingProvider: raise
    except (OSError, ValueError) as exc:
        reason = str(exc) or type(exc).__name__
        if reason in (CRASHED, USB_DENIED): raise  # already what to do, in plain words
        raise ValueError(LUMA_FAILED.format(reason=reason)) from None


def start(runtime, timeout=120.0, luma=None, binary=None):
    """Start XRLinuxDriver (`binary`, default the standard one), or with `luma` (the pinned binary's path) the Luma driver,
    and wait for it to calibrate."""
    _check_provider_compatibility()
    if _ours(_read_state(runtime)): raise ValueError('tracking already running')
    if luma is None: _check_config()
    if _other_drivers(): raise ValueError('another head tracking driver is running; refusing to compete for the glasses')
    _check_usb_access()
    if luma is None:
        offset, output, argv = DRIVER_LOG.stat().st_size if DRIVER_LOG.exists() else 0, subprocess.DEVNULL, _driver_argv(binary)
    else:
        argv = _luma_argv(luma)  # first: a bad flags file should open no log
        offset, output = _open_luma_log()
    # The quiet driver is the standard driver's alone: the Luma driver has no SDK for it to hold back, and in the 3DoF test
    # it would change what is being measured.
    env = _driver_env() if luma is None and binary in (None, DRIVER) else None
    try:
        child = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=output, stderr=output, start_new_session=True, close_fds=True, env=env)
    finally:
        if luma is not None: os.close(output)
    deadline = time.monotonic() + 2
    tick = None
    while tick is None and time.monotonic() < deadline:  # unshare execs the driver in the same pid
        tick = _start_tick(child.pid); time.sleep(0.02)
    path = _state(runtime)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as f: json.dump({'pid': child.pid, 'start_tick': tick, 'log_offset': offset, 'driver': LUMA if luma else XRDRIVER, 'binary': luma or binary or DRIVER}, f)
    log = LUMA_LOG if luma else DRIVER_LOG
    try:
        deadline = time.monotonic() + timeout
        isolation_deadline = time.monotonic() + min(timeout, 2.0)
        while time.monotonic() < deadline:
            # First: a crashed driver is a zombie until reaped, so what follows would say only "exited", or, with no namespace
            # left to read, "not network-isolated". This says how it ended.
            if child.poll() is not None: raise ValueError(_exit_failure(offset, log, child.returncode))
            now = status(runtime)
            if not now['running']: raise ValueError(_exit_failure(offset, log, child.poll()))
            if not now['network_isolated']:
                # unshare may still be setting up its namespace: never accept calibration
                # until isolated, but allow the owned launcher a bounded startup interval.
                if time.monotonic() < isolation_deadline:
                    time.sleep(0.02)
                    continue
                # Its namespaces go just before it can be waited on, so a process caught dying gets a moment to show it.
                try: child.wait(timeout=0.2)
                except subprocess.TimeoutExpired: raise ValueError('tracking driver is not network-isolated; stopped') from None
                raise ValueError(_exit_failure(offset, log, child.returncode))
            if now['calibrated']: return now
            time.sleep(0.2)
        raise ValueError('tracking driver did not calibrate in time (keep the glasses still while it starts)' + (_luma_waiting(offset) if luma else ''))
    except BaseException:
        # Cancellation must not leave a child keeping the transient unit alive.
        stop(runtime)
        _state(runtime).unlink(missing_ok=True)  # stop() leaves the record of a driver that is already gone
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
            except (OSError, ValueError) as exc:
                print(f'xr-workspace: the restart failed: {exc}', file=sys.stderr, flush=True)
                # start() cleans its state after failure. Otherwise the next loop mistakes
                # that cleanup for an intentional stop and exits without telling anyone.
                self.notify(f'The XR Luma driver keeps crashing and could not restart, so head tracking has stopped for this session. See {LUMA_LOG}.')
                return

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
