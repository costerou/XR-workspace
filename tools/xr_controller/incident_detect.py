"""What counts as an incident: signals from observations and log lines, as pure code with no I/O (incident_monitor.py
feeds it; docs/technical.md, Incident reports).

The thresholds come from the real logs of 2026-11-03, when the glasses' USB hub dropped over and over. Three things from
those logs shape this file. A hub drop comes in pairs 2 to 23 s apart, and only the second may end the run, so one drop
is one incident only if the window is longer than that (Correlator). The renderer draws only when something changes
unless head tracking is on, so missing frames mean something only in a tracked run. And a run the USB drop ended leaves a
tidy result (complete, phase done, renderer rc 1), so the result alone never says why.
"""
from dataclasses import dataclass
import datetime
import json
import re
import signal as signals

from tools.native_spike.tracking import SCOPES

# Every kind, most telling first: an incident is named after the highest of its signals (Episode.kind). A crash outranks
# the USB drop that may have set it off, because the crash is the part software can fix.
KINDS = ('hyprland-crash', 'renderer-crash', 'usb-drop', 'video-lost', 'plugin-failed', 'watchdog', 'controller-restart', 'run-killed',
         'run-failed', 'capture-failed', 'present-stall', 'tracking-lost', 'renderer-exit', 'recovery-failed', 'start-failed')

STALL_S = 5.0            # no frames for this long is a stall
COLLAPSE_RATIO = 0.4     # under this share of the session's usual rate; a halving to 60 under load is not a collapse
COLLAPSE_S = 10.0
RATE_WARMUP_S = 5.0      # the first seconds of a session are no measure of it
RATE_DECAY = 0.999       # per second: how slowly the usual rate forgets a peak
MODE_CHANGE_S = 5.0      # longer than a config reload's change, which managed.after_reload puts back
OUTPUT_GONE_S = 3.0
PLUGIN_GONE_S = 3.0
TRACKING_LOST_S = 3.0    # the renderer flips after 100 ms without a sample (pose.hpp), so a blip must not count
TRACKING_NEVER_S = 30.0
DRIVER_GONE_S = 3.0
USB_GONE_S = 2.0
UNRESPONSIVE_S = 15.0    # a stopping run's teardown holds this state for a few seconds at most
CAPTURE_STUCK = 30       # failures in a row, with no frame between: a resize makes one or two (the renderer gives up at 1000)
GAP_S = 5.0              # observations this far apart: the machine slept, or the controller stalled; timers restart
USB_DEDUPE_S = 5.0       # a hub's disconnect and its children's are one drop
USB_FAILS = 2            # one failed command alone is common: 42 of 438 had no retry within 30 s
USB_FAILS_WINDOW_S = 30.0
RESUME_QUIET_S = 30.0    # USB comes back from a suspend through a disconnect

QUIET_S = 25.0           # an incident is over after this long without a signal: longer than the hub's 23 s between drops
MAX_HOLD_S = 60.0        # however noisy, it is reported by then
AFTER_END_S = 5.0        # the run's end is the end of the incident; this lets the logs settle
MAX_SIGNALS = 64


@dataclass(frozen=True)
class Signal:
    at: float     # wall clock
    kind: str     # one of KINDS
    source: str   # where it was seen: renderer, drm, hyprland, kernel, driver-log, unit, controller
    detail: str   # one short line in our own words, never log text


class Sustained:
    """True once a condition has held `hold` seconds, then quiet until it clears."""
    def __init__(self, hold): self.hold, self.since, self.fired = hold, None, False

    def update(self, at, on):
        if not on:
            self.since, self.fired = None, False
            return False
        if self.since is None: self.since = at
        if self.fired or at - self.since < self.hold: return False
        self.fired = True
        return True

    def restart(self): self.since = None


class RunWatch:
    """One run's live detectors. `observe` takes one observation a second (incident_monitor builds them) and says only what
    is wrong; Stop, Recover and the run's own start and teardown never reach the checks, which need state 'running'."""
    def __init__(self, run_id, consent, at):
        consent = consent or {}
        self.run_id, self.consent, self.created = run_id, consent, at
        self.tracking = consent.get('tracking') in SCOPES
        self.stopped = False           # a Stop was asked for, so the end is a normal one (classify_end)
        self.running_since = None
        self.last_at = None
        self.settle_until = 0.0        # after a gap: the display and USB come back from a suspend through a disconnect
        self.last_status = None        # the last metrics, for the report
        self.mode = None               # the session's mode, as first seen running
        self.seen_mode = None
        self.drm = None
        self.usual_rate = None
        self.capture_path = None
        self.tracked = False           # head tracking has worked at least once in this run
        self.usb_seen = self.driver_seen = False  # only what was there can go missing
        self._video = Sustained(0)
        self._mode_changed = Sustained(MODE_CHANGE_S)
        self._output_gone = Sustained(OUTPUT_GONE_S)
        self._stall = Sustained(STALL_S)
        self._collapse = Sustained(COLLAPSE_S)
        self._tracking_lost = Sustained(TRACKING_LOST_S)
        self._never_tracked = Sustained(0)
        self._capture_stuck = Sustained(0)
        self._driver_gone = Sustained(DRIVER_GONE_S)
        self._usb_gone = Sustained(USB_GONE_S)
        self._hyprland_gone = Sustained(0)
        self._plugin_gone = Sustained(PLUGIN_GONE_S)
        self._silent = Sustained(UNRESPONSIVE_S)

    def mode_text(self): return _mode_text(self.seen_mode)

    def expected_mode_text(self): return _mode_text(self.mode)

    def _timers(self):
        return (self._mode_changed, self._output_gone, self._stall, self._collapse, self._tracking_lost, self._driver_gone,
                self._usb_gone, self._plugin_gone, self._silent)

    def observe(self, o):
        at, state, out = o['at'], o['state'], []
        if self.last_at is not None and at - self.last_at > GAP_S:
            for timer in self._timers(): timer.restart()
            self.settle_until = at + RESUME_QUIET_S
        self.last_at = at
        if state not in ('running', 'failed'): return out
        if self._hyprland_gone.update(at, o.get('hyprland_alive') is False):
            out.append(Signal(at, 'hyprland-crash', 'hyprland', 'the Hyprland process is gone'))
        if at < self.settle_until: return out
        if state == 'failed':
            # The unit lives but its control socket does not answer: hung, or tearing down for a few seconds.
            if self._silent.update(at, True):
                out.append(Signal(at, 'watchdog', 'controller', f'the run stopped answering for {UNRESPONSIVE_S:.0f} seconds while its unit stayed up'))
            return out
        self._silent.update(at, False)
        if self.running_since is None: self.running_since = at
        status = o.get('status')
        if status is not None: self.last_status = dict(status)
        self._display(at, o, out)
        self._hardware(at, o, out)
        if status is not None:
            self._presentation(at, o, status, out)
            self._capture(at, status, out)
            self._head(at, status, out)
        return out

    def _display(self, at, o, out):
        if not self.consent.get('output'): return  # an unreadable consent: nothing to compare with
        self.drm = o.get('drm')
        if self._video.update(at, self.drm == 'disconnected'):
            out.append(Signal(at, 'video-lost', 'drm', f"the glasses' display output {self.consent.get('output')} disconnected"))
        if not o.get('monitors_known'): return
        monitor = o.get('monitor')
        if self._output_gone.update(at, monitor is None):
            out.append(Signal(at, 'video-lost', 'hyprland', f"the glasses' display output {self.consent.get('output')} left Hyprland"))
        if monitor is None: return
        self.seen_mode = _mode(monitor)
        if self.mode is None: self.mode = self.seen_mode
        if self._mode_changed.update(at, self.seen_mode != self.mode):
            out.append(Signal(at, 'video-lost', 'hyprland', f'the display mode changed from {_mode_text(self.mode)} to {_mode_text(self.seen_mode)}'))

    def _hardware(self, at, o, out):
        self.usb_seen = self.usb_seen or o.get('usb_present') is True
        self.driver_seen = self.driver_seen or o.get('driver_alive') is True
        if self._usb_gone.update(at, self.usb_seen and o.get('usb_present') is False):
            out.append(Signal(at, 'usb-drop', 'usb', 'the glasses left the USB bus'))
        if o.get('monitors_known') and self._plugin_gone.update(at, o.get('plugins') == 0):
            out.append(Signal(at, 'plugin-failed', 'hyprland', 'the ownership plugin is no longer loaded in Hyprland'))
        if self.tracking and self._driver_gone.update(at, self.driver_seen and o.get('driver_alive') is False):
            out.append(Signal(at, 'tracking-lost', 'tracking', 'the head tracking driver process is gone'))

    def _presentation(self, at, o, status, out):
        monitor = o.get('monitor')
        asleep = monitor is not None and monitor.get('dpms') is False
        rate = status.get('presented_hz')
        # Without tracking the renderer draws only when something changes, and a sleeping screen presents nothing.
        watching = self.tracking and not asleep
        if self._stall.update(at, watching and rate is None):
            out.append(Signal(at, 'present-stall', 'renderer', f'no frames reached the glasses for {STALL_S:.0f} seconds'))
        if rate is None or not watching or at - self.running_since < RATE_WARMUP_S:
            self._collapse.update(at, False)
            return
        low = self.usual_rate is not None and rate < COLLAPSE_RATIO * self.usual_rate
        if not low: self.usual_rate = max(rate, (self.usual_rate or 0) * RATE_DECAY)
        if self._collapse.update(at, low):
            out.append(Signal(at, 'present-stall', 'renderer', f'the frame rate collapsed to {rate:.0f} Hz from about {self.usual_rate:.0f} Hz'))

    def _capture(self, at, status, out):
        path = status.get('capture_path')
        if path:
            if self.capture_path == 'dmabuf' and path == 'shm':
                out.append(Signal(at, 'capture-failed', 'renderer', 'capture fell back from dmabuf to shared memory'))
            self.capture_path = path
        failing = status.get('capture_failure_run')
        if type(failing) is int and self._capture_stuck.update(at, failing >= CAPTURE_STUCK):
            out.append(Signal(at, 'capture-failed', 'renderer', f'desktop capture failed {failing} times in a row'))

    def _head(self, at, status, out):
        if not self.tracking: return
        tracking = status.get('tracking')
        if tracking is True: self.tracked = True
        if self.tracked:
            if self._tracking_lost.update(at, tracking is False):
                out.append(Signal(at, 'tracking-lost', 'renderer', f'no head tracking samples for {TRACKING_LOST_S:.0f} seconds'))
        elif self._never_tracked.update(at, tracking is False and at - self.running_since >= TRACKING_NEVER_S):
            out.append(Signal(at, 'tracking-lost', 'renderer', f'head tracking had not started {TRACKING_NEVER_S:.0f} seconds into the session'))


def _mode(monitor): return (monitor.get('width'), monitor.get('height'), round(monitor.get('refresh') or 0))


def _mode_text(mode): return f'{mode[0]}x{mode[1]}@{mode[2]}' if mode else None


_KERNEL = re.compile(r'(\S+) \S+ kernel: (.*)')
_USB_GONE = re.compile(r'usb (\d+-[\d.]+): USB disconnect\b')
_USB_NEW = re.compile(r'usb (\d+-[\d.]+): New USB device found, idVendor=35ca\b', re.I)


def glasses_family(port, ports):
    """Is `port` one of the glasses' ports, or a hub above one?"""
    return any(port == p or p.startswith(port + '.') for p in ports)


class KernelLog:
    """`journalctl -k -o short-iso` lines, newest last. The port is learned from the vendor id, never assumed: a disconnect line
    names no vendor, and another device can drop at the worst moment (a game controller on 1-4 did, in the very second a run
    ended, on 2026-11-03 at 00:00:14)."""
    def __init__(self, ports=()):
        self.ports = set(ports)
        self.quiet_until = 0.0
        self.last_drop = None

    def note_ports(self, ports): self.ports |= set(ports)

    def feed(self, lines):
        out = []
        for line in lines:
            m = _KERNEL.fullmatch(line.rstrip('\n'))
            if not m: continue
            try: at = datetime.datetime.fromisoformat(m[1]).timestamp()
            except ValueError: continue
            text = m[2]
            if 'PM: suspend entry' in text: self.quiet_until = float('inf')
            elif 'PM: suspend exit' in text: self.quiet_until = at + RESUME_QUIET_S
            new = _USB_NEW.match(text)
            if new: self.ports.add(new[1])
            gone = _USB_GONE.match(text)
            if not gone or not glasses_family(gone[1], self.ports) or at < self.quiet_until: continue
            if self.last_drop is None or at - self.last_drop >= USB_DEDUPE_S:
                out.append(Signal(at, 'usb-drop', 'kernel', f'the kernel saw the glasses\' USB port {gone[1]} disconnect'))
            self.last_drop = at
        return out


_DRIVER_TIME = re.compile(r'(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d(?:\.\d+)?) ')


class DriverLog:
    """New text from XRLinuxDriver's driver.log, in whatever pieces it is read; the caller seeks. Only a retry, or failures
    close together, mean a drop: most failed commands are one of those, but the rest (42 of 438) led nowhere."""
    def __init__(self):
        self.rest = ''
        self.failures = []

    def feed(self, text, at):
        lines = (self.rest + text).split('\n')
        self.rest = lines.pop()
        out = []
        for line in lines:
            m = _DRIVER_TIME.match(line)
            when = at
            if m:
                try: when = datetime.datetime.fromisoformat(m[1]).timestamp()
                except ValueError: pass
            if 'Retrying driver connection' in line:
                out.append(Signal(when, 'usb-drop', 'driver-log', 'the tracking driver lost the glasses and is reconnecting'))
            elif 'Failed to send USB command' in line or 'open_imu failed' in line:
                self.failures = [t for t in self.failures if at - t < USB_FAILS_WINDOW_S] + [at]
                if len(self.failures) >= USB_FAILS:
                    out.append(Signal(when, 'usb-drop', 'driver-log', 'the tracking driver\'s USB commands to the glasses are failing'))
                    self.failures = []
            elif 'Waiting on device calibration' in line:
                out.append(Signal(when, 'tracking-lost', 'driver-log', 'the tracking driver is calibrating again'))
            elif 'Starting up XR driver' in line:
                out.append(Signal(when, 'tracking-lost', 'driver-log', 'the tracking driver restarted'))
        return out


_CRASHES = {signals.SIGSEGV, signals.SIGABRT, signals.SIGBUS, signals.SIGILL, signals.SIGFPE, signals.SIGSYS, signals.SIGTRAP, signals.SIGQUIT}
_STOP_SIGNALS = {signals.SIGTERM, signals.SIGKILL, signals.SIGINT}
# Supervisor.stop_reason, for the ways the supervisor itself ended the renderer. An interrupt is Stop; a renderer that
# closed its own socket has its exit code to speak for it.
_WATCHDOG = {'heartbeat-timeout': 'the renderer sent no heartbeat for 2 seconds, so the watchdog stopped it',
             'heartbeat-invalid': 'the renderer sent a bad heartbeat, so the watchdog stopped it',
             'guard-failed': 'the run guard found an output missing or changed, and stopped the run',
             'guard-timeout': 'the run guard did not answer in time, and stopped the run',
             'send-failed': 'the supervisor could not reach the renderer, and stopped it'}


def signal_name(number):
    try: return signals.Signals(number).name
    except ValueError: return f'signal {number}'


def crash_expected(result):
    """Will a core dump appear? (The monitor waits for it.)"""
    rc = (result or {}).get('renderer_returncode')
    return type(rc) is int and rc < 0 and -rc in _CRASHES


def _unit_text(unit):
    unit = unit or {}
    if unit.get('result') == 'oom-kill': return "the system ran out of memory and killed the run's unit"
    if unit.get('status'): return f"the run's unit ended ({unit.get('code')}, {unit['status']}) without reporting a result"
    return "the run's unit ended without reporting a result"


def classify_end(at, result, *, started=True, stopped=False, stranded=False, unit=None):
    """Signals for a run that is over: [] for every normal end. `result` is its .result.json, or None. `started`: it reached
    state 'running'. `stopped`: Stop was asked for. `stranded`: it left work for Recover."""
    out = []
    if result is None:
        if stranded or not stopped:
            out.append(Signal(at, 'run-killed' if started or stranded else 'start-failed', 'unit', _unit_text(unit)))
        return out
    rc, reason, error = result.get('renderer_returncode'), result.get('stop_reason'), result.get('error')
    interrupted = stopped or reason == 'interrupted'
    own_exit = type(rc) is int and (rc > 0 or -rc in _CRASHES)  # a send fails on a renderer that went by itself, too
    if reason in _WATCHDOG and not (reason == 'send-failed' and own_exit):
        out.append(Signal(at, 'watchdog', 'supervisor', _WATCHDOG[reason]))
    elif type(rc) is int and rc < 0:
        number = -rc
        if number in _CRASHES:
            out.append(Signal(at, 'renderer-crash', 'renderer', f'the renderer crashed ({signal_name(number)})'))
        elif not (interrupted and number in _STOP_SIGNALS):
            out.append(Signal(at, 'renderer-crash', 'renderer', f'something killed the renderer ({signal_name(number)})'))
    elif type(rc) is int and rc > 0 and not interrupted:
        out.append(Signal(at, 'renderer-exit', 'renderer', f'the renderer exited with code {rc}'))
    if error == 'KeyboardInterrupt': return out  # Stop while a run was starting
    if error:
        phase = result.get('phase')
        if phase == 'plugin' or error.startswith('unload:'): kind = 'plugin-failed'
        elif rc is None and not started: kind = 'start-failed'
        else: kind = 'run-failed'
        out.append(Signal(at, kind, 'runner', f'the run stopped during {phase}: {error}'))
    elif result.get('complete') is not True:
        out.append(Signal(at, 'run-failed', 'runner', 'the run ended without finishing its recovery'))
    return out


_UNIT_EXIT = re.compile(r'Main process exited, code=(\S+), status=(\S+)')
_UNIT_RESULT = re.compile(r"Failed with result '([^']+)'")


def parse_unit_exit(lines):
    """systemd's own last words on a unit, from its journal: a --collect unit that failed is gone from `systemctl show`."""
    out = {}
    for line in lines:
        m = _UNIT_EXIT.search(line)
        if m: out = dict(code=m[1], status=m[2])
        m = _UNIT_RESULT.search(line)
        if m: out['result'] = m[1]
    return out


_JOURNAL_LINE = re.compile(r'(?:\S+ ){1,2}?[^:\s]+: (.*)')  # with or without the host field; lazy, or JSON splits at its own first colon
_PRESENTATION = ('submitted', 'presented', 'discarded', 'abandoned', 'pending', 'invalid_timing', 'interval_count', 'interval_mean_ns',
                 'interval_min_ns', 'interval_max_ns', 'observation_duration_ns')
_CAPTURE = ('name', 'frames', 'failures', 'reallocations', 'path')
_TRACKING = ('feed', 'tracking', 'samples', 'lost_events')
_REPORT_KINDS = ('generated_pattern_presentation_timing', 'managed_output_capture')


def _report(line):
    m = _JOURNAL_LINE.fullmatch(line.rstrip('\n'))
    body = m[1] if m else line
    if not body.startswith('{'): return None
    try: value = json.loads(body)
    except ValueError: return None
    return value if isinstance(value, dict) and value.get('kind') in _REPORT_KINDS else None


def is_exit_report(line): return _report(line) is not None


def exit_metrics(lines):
    """The renderer's exit reports in a run's journal, the numbers only. Named fields, so nothing new in a report can
    reach an incident by accident."""
    out = {}
    for line in lines:
        report = _report(line)
        if report is None: continue
        if report['kind'] == _REPORT_KINDS[0]:
            out['presentation'] = {k: report[k] for k in _PRESENTATION if k in report}
        else:
            out['capture'] = [{k: o[k] for k in _CAPTURE if k in o} for o in report.get('outputs') or [] if isinstance(o, dict)]
            tracking = report.get('tracking')
            if isinstance(tracking, dict): out['tracking'] = {k: tracking[k] for k in _TRACKING if k in tracking}
    return out


class Episode:
    """One incident: the signals that belong together, however many there were. `first` and `last` are when they say they
    happened, for the report. It closes by when they arrived, so a wrong clock in a log cannot hold a report back."""
    def __init__(self, run_id, at):
        self.run_id, self.first, self.last = run_id, at, at
        self.arrived_first = self.arrived_last = at
        self.signals, self.repeats = [], []
        self.ended = None           # None: the session carried on; 'ended', or 'stopped' by a person
        self.ended_at = None
        self.hold_until = 0.0       # not before this: a core dump is on its way
        self.context = None         # the RunWatch, for the report

    def add(self, signal, now=None):
        self.last = max(self.last, signal.at)
        self.first = min(self.first, signal.at)
        self.arrived_last = signal.at if now is None else now
        same = (signal.kind, signal.source, signal.detail)
        for i, seen in enumerate(self.signals):
            # Only the same moment: when a hub dropped a second time is the point of the timeline.
            if (seen.kind, seen.source, seen.detail) == same and abs(seen.at - signal.at) < 1:
                self.repeats[i] += 1
                return
        if len(self.signals) < MAX_SIGNALS:
            self.signals.append(signal)
            self.repeats.append(0)

    @property
    def kind(self): return min((s.kind for s in self.signals), key=KINDS.index)

    def ready(self, now):
        if now < self.hold_until: return False
        if self.ended_at is not None: return now - self.ended_at >= AFTER_END_S
        return now - self.arrived_last >= QUIET_S or now - self.arrived_first >= MAX_HOLD_S


class Correlator:
    """Turns a stream of signals into one incident per real event."""
    def __init__(self):
        self.open = None
        self.closed = []

    def add(self, signal, run_id, context=None, now=None):
        if self.open is not None and self.open.run_id != run_id:
            self.closed.append(self.open)
            self.open = None
        if self.open is None:
            self.open = Episode(run_id, signal.at)
            self.open.arrived_first = signal.at if now is None else now
        if context is not None: self.open.context = context
        self.open.add(signal, now)

    def run_ended(self, run_id, at, ended, hold=0.0):
        e = self.open
        if e is None or e.run_id != run_id: return
        e.ended, e.ended_at, e.hold_until = ended, at, at + hold

    def due(self, now):
        done, self.closed = self.closed, []
        if self.open is not None and self.open.ready(now):
            done.append(self.open)
            self.open = None
        return done
