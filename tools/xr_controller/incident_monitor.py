"""The incident monitor: a thread in the controller service that outlives runs (docs/technical.md, Incident reports).

The controller's tick hands it one observation a second and never waits for it. The thread feeds the pure detectors
(incident_detect.py), reads the host for what the tick does not know, and writes reports (incident_report.py), so a slow
journal, core dump or hook cannot hold up the panel. It only reads: no unit, driver or compositor is ever touched.
"""
import collections
import glob
import json
import math
import os
from pathlib import Path
import re
import subprocess
import threading
import time

from tools.native_spike import tracking
from tools.native_spike.ipc import read_private_json
from tools.native_spike.profiles import log
from .incident_detect import (Correlator, DriverLog, KernelLog, RunWatch, Signal, classify_end, crash_expected, exit_metrics, parse_unit_exit,
                              signal_name)
from .incident_report import RateLimit, Scrubber, amend_bundle, build_bundle, markdown, notify_text, pin_of, run_hook

CONTROLLER_UNIT = 'viture-workspace-controller.service'
LIVE = ('starting', 'running', 'stopping', 'failed')
STRANDED = ('recovery-needed', 'cli-recovery')
POLL_S = 2.0              # kernel and driver logs, while a session runs
CORE_WAIT_S = 20.0        # systemd-coredump takes a moment; a crash report waits this long for it at most
LOG_LINES = 200           # the last lines of each journal and of the driver log
KERNEL_BEFORE_S = 90.0
KERNEL_AFTER_S = 30.0
MAX_ENDED = 64
QUEUE = 256


def _unit(run_id): return f'viture-xr-{run_id}.service'


_KERNEL_NOISE = re.compile(r'split lock|bus_lock|\bUFW\b')
_KERNEL_WANTED = re.compile(r'(?i)\bxhci|\[drm\]|\bdrm\b|\bamdgpu\b|\b35ca\b|\bPM: suspend')
_KERNEL_PORT = re.compile(r'\b(\d+-\d+(?:\.\d+)*)[:\s]')


def relevant_kernel(lines, ports):
    """The lines about the glasses' USB tree and the GPU. Not other USB devices (their names are private), not the firewall."""
    roots = {p.split('.')[0] for p in ports}
    out = []
    for line in lines:
        if _KERNEL_NOISE.search(line): continue
        port = _KERNEL_PORT.search(line.split(' kernel: ', 1)[-1])
        if (port and port[1].split('.')[0] in roots) or _KERNEL_WANTED.search(line): out.append(line)
    return out


def stack_frames(info):
    """The crashing thread's frames from `coredumpctl info`. The rest of that output (command line, paths) stays out."""
    frames, inside = [], False
    for line in info.splitlines():
        text = line.strip()
        if text.startswith('Stack trace of thread'):
            if inside: break
            inside = True
        elif inside and text.startswith('#'): frames.append(text)
        elif inside and frames: break
    return frames


class HostSources:
    """Everything the monitor reads from the host, all of it read-only and bounded by a timeout. A source that cannot answer
    says so with an empty answer."""
    def __init__(self, runtime, state_dir, *, repo=None, sys_root='/sys', proc_root='/proc', driver_log=None, run=subprocess.run):
        self.runtime, self.state_dir = Path(runtime), Path(state_dir)
        self.repo = Path(repo) if repo else Path(__file__).resolve().parents[2]
        self.sys, self.proc = Path(sys_root), Path(proc_root)
        self.driver_log = Path(driver_log) if driver_log else None  # else whichever driver is running: _driver_path
        self._reading = None  # the log the last driver_read's offset belongs to
        self._exec = run

    def _run(self, argv, timeout=5, env=None):
        try:
            return self._exec(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, errors='replace',
                              timeout=timeout, **({'env': env} if env else {}))
        except (OSError, subprocess.SubprocessError): return None

    def boot_id(self):
        try: return (self.proc / 'sys/kernel/random/boot_id').read_text().strip()
        except OSError: return None

    def consent(self, run_id):
        return read_private_json(self.state_dir / f'{run_id}.json')

    def result(self, run_id):
        try: result = read_private_json(self.state_dir / f'{run_id}.result.json')
        except FileNotFoundError: return None
        return result if isinstance(result, dict) and result.get('run_id') == run_id else None

    def recovery(self, run_id):
        """What the last Recover of a run left (__main__.report_recovery): {'remaining': [step id], 'reasons': [text]}, or None."""
        try: record = read_private_json(self.state_dir / f'{run_id}.recovery.json')
        except FileNotFoundError: return None
        if not isinstance(record, dict) or record.get('run_id') != run_id: return None
        def texts(value): return [str(item) for item in value] if isinstance(value, list) else []
        return dict(remaining=texts(record.get('remaining')), reasons=texts(record.get('reasons')))

    @staticmethod
    def _cursor(lines):
        for line in reversed(lines):
            if line.startswith('-- cursor: '): return line[len('-- cursor: '):]
        return None

    def kernel_start(self):
        done = self._run(['journalctl', '-k', '-n', '0', '--show-cursor', '--no-pager'])
        return self._cursor(done.stdout.splitlines()) if done is not None else None

    def kernel_read(self, cursor):
        done = self._run(['journalctl', '-k', f'--after-cursor={cursor}', '--show-cursor', '--no-pager', '-o', 'short-iso', '-n', '2000'])
        if done is None: return [], cursor
        lines = done.stdout.splitlines()
        return [line for line in lines if not line.startswith('-- ')], self._cursor(lines) or cursor

    def kernel_around(self, first, last):
        done = self._run(['journalctl', '-k', f'--since=@{int(first)}', f'--until=@{int(math.ceil(last))}', '--no-pager', '-o', 'short-iso', '-n', '4000'])
        return [line for line in done.stdout.splitlines() if not line.startswith('-- ')] if done is not None else []

    def journal(self, unit, lines):
        done = self._run(['journalctl', '--user', '-u', unit, '-n', str(lines), '--no-pager', '-o', 'short-iso'])
        return [line for line in done.stdout.splitlines() if not line.startswith('-- ')] if done is not None else []

    def _driver_path(self):
        return self.driver_log or tracking.log_path(self.runtime)

    def _driver_file(self):
        try: return os.fdopen(os.open(self._driver_path(), os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC), 'rb')
        except OSError: return None

    def driver_start(self):
        self._reading = self._driver_path()
        stream = self._driver_file()
        if stream is None: return 0
        with stream: return os.fstat(stream.fileno()).st_size

    def driver_read(self, offset):
        stream = self._driver_file()
        if stream is None: return '', 0
        with stream:
            size = os.fstat(stream.fileno()).st_size
            path = self._driver_path()
            if self._reading is not None and path != self._reading: offset = size  # the start changed driver: its log, from here
            self._reading = path
            if size < offset: offset = 0  # the driver started a new log
            stream.seek(offset)
            data = stream.read(1 << 20)
        return data.decode('utf-8', errors='replace'), offset + len(data)

    def driver_tail(self, lines):
        stream = self._driver_file()
        if stream is None: return []
        with stream:
            stream.seek(max(0, os.fstat(stream.fileno()).st_size - (1 << 17)))
            return stream.read().decode('utf-8', errors='replace').splitlines()[-lines:]

    def drm(self, output):
        states = []
        for path in glob.glob(str(self.sys / 'class/drm' / f'card*-{glob.escape(output)}' / 'status')):
            try: states.append(Path(path).read_text().strip())
            except OSError: pass
        if not states: return None
        return 'connected' if 'connected' in states else 'disconnected'

    def usb_ports(self):
        """The USB ports holding a VITURE device, by vendor id; None when the tree cannot be read."""
        try: entries = list((self.sys / 'bus/usb/devices').iterdir())
        except OSError: return None
        ports = set()
        for entry in entries:
            if ':' in entry.name: continue  # an interface, not a device
            try:
                if (entry / 'idVendor').read_text().strip().lower() == '35ca': ports.add(entry.name)
            except OSError: pass
        return ports

    def hyprland_pid(self, instance):
        if not instance: return None
        try: return int((self.runtime / 'hypr' / instance / 'hyprland.lock').read_text().split()[0])
        except (OSError, ValueError, IndexError): return None

    def alive(self, pid):
        if pid is None: return None
        try: return (self.proc / str(pid) / 'comm').read_text().strip() == 'Hyprland'
        except OSError: return False

    def driver_alive(self): return tracking.driver_alive(self.runtime)

    def tracking_driver(self): return tracking.active_driver(self.runtime)

    def core_dumps(self, since, exes, pids):
        """The core dumps of `exes` or `pids` since then: which, what signal, the backtrace's text. Never the core itself, which
        holds the process's memory and so what was on screen. A pid, because another Hyprland aborting is not ours crashing."""
        done = self._run(['coredumpctl', '--no-pager', '--json=short', f'--since=@{int(since)}', 'list'], timeout=10)
        if done is None or done.returncode != 0: return []
        try: entries = json.loads(done.stdout)
        except ValueError: return []
        out = []
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict) or not (entry.get('exe') in exes or entry.get('pid') in pids): continue
            info = self._run(['coredumpctl', '--no-pager', 'info', str(entry.get('pid'))], timeout=10)
            out.append(dict(exe=Path(str(entry.get('exe'))).name, pid=entry.get('pid'), signal=signal_name(entry.get('sig')) if entry.get('sig') else None,
                            frames=stack_frames(info.stdout) if info is not None else []))
        return out

    def source(self):
        env = dict(os.environ, GIT_OPTIONAL_LOCKS='0')  # never touch the index of a checkout someone may be working in
        sha = self._run(['git', '-C', str(self.repo), 'rev-parse', 'HEAD'], env=env)
        dirty = self._run(['git', '-C', str(self.repo), 'status', '--porcelain'], env=env)
        return dict(sha=sha.stdout.strip() or None if sha is not None else None, dirty=bool(dirty.stdout.strip()) if dirty is not None else None)


class _Live:
    """A run being watched: its detectors, where its logs had got to, and how it ended."""
    def __init__(self, run_id, consent, at):
        self.watch = RunWatch(run_id, consent, at)
        self.kernel, self.driver = KernelLog(), DriverLog()
        self.armed = False
        self.cursor = self.offset = None
        self.next_poll = 0.0
        self.instance = (consent or {}).get('instance')
        self.hypr_pid = None
        self.result = self.unit = None
        self.report_ids = []

    def saved(self, boot):
        w = self.watch
        return dict(boot=boot, run_id=w.run_id, instance=self.instance, hypr_pid=self.hypr_pid, running_since=w.running_since, stopped=w.stopped,
                    ports=sorted(self.kernel.ports), report_ids=self.report_ids)


class IncidentMonitor:
    def __init__(self, sources, reports, *, hook=None, notify=None, hand_on=None, spawn=None, scrub=None, clock=time.time):
        self.sources, self.reports, self.hook, self.clock = sources, reports, hook, clock
        self.notify = notify if notify is not None else self._toast
        self.hand_on = hand_on if hand_on is not None else self._hand_on
        self.spawn = spawn if spawn is not None else (lambda work: threading.Thread(target=work, name='xr-incident-hook', daemon=True).start())
        self.scrub = scrub if scrub is not None else Scrubber()
        self.queue = collections.deque(maxlen=QUEUE)
        self.correlator = Correlator()
        self.live = None
        self.after = {}               # run_id: its state since it ended (a failed Recover)
        self.resumed = False
        self.interval = 0.25
        self.next_core_check = 0.0
        self._halt = threading.Event()
        self._thread = None
        self._said = None
        saved = reports.load_state()
        self.limit = RateLimit.restore(saved.get('limit'))
        ended = saved.get('ended')
        self.ended = dict(ended) if isinstance(ended, dict) else {}
        self.saved_watch = saved.get('watch') if isinstance(saved.get('watch'), dict) else None
        latest = saved.get('latest')
        self.latest = latest if isinstance(latest, dict) else None

    # --- the controller's side ----------------------------------------------------------------------------------------
    def observe(self, observation):
        """Called on the controller's tick: never blocks, never raises."""
        self.queue.append(dict(observation, at=observation.get('at') or self.clock()))

    def start(self):
        self._thread = threading.Thread(target=self._loop, name='xr-incidents', daemon=True)
        self._thread.start()

    def stop(self):
        self._halt.set()
        if self._thread is not None: self._thread.join(5)

    def _loop(self):
        while not self._halt.wait(self.interval):
            try: self.step(self.clock())
            except Exception as exc: self._say(f'incident monitor: {type(exc).__name__}: {exc}')

    # --- one pass -----------------------------------------------------------------------------------------------------
    def step(self, now):
        while self.queue: self._observed(self.queue.popleft())
        self._flush(now)

    def _say(self, text):
        if text != self._said: log(text)  # a fault that repeats is said once
        self._said = text

    def _safe(self, call, *args, default=None):
        """A source that fails costs its evidence, never the report."""
        try: return call(*args)
        except Exception as exc:
            self._say(f'incident monitor: {getattr(call, "__name__", "a source")} failed: {type(exc).__name__}: {exc}')
            return default

    def _observed(self, o):
        if not self.resumed:
            self.resumed = True
            self._resume(o)
        run_id = o.get('run_id')
        if self.live is not None and run_id != self.live.watch.run_id: self._finish(self.live, o)
        if self.live is None and run_id is not None:
            if run_id in self.ended:
                self._after_end(run_id, o)
                return
            self._begin(run_id, o)
        if self.live is None: return
        if o.get('state') in STRANDED: self._finish(self.live, o, stranded=True)
        else: self._feed(self.live, o)

    def _context(self, run_id, at):
        consent = self._safe(self.sources.consent, run_id)
        return _Live(run_id, consent if isinstance(consent, dict) else None, at)

    def _begin(self, run_id, o):
        live = self._context(run_id, o['at'])
        live.instance = live.instance or o.get('instance')
        live.hypr_pid = self._safe(self.sources.hyprland_pid, live.instance)
        self.live = live
        self._save()

    def _resume(self, o):
        """The first observation of a controller start: what became of the run the last one was watching? A controller
        restart under a live session is no incident, and after a reboot there is nothing left to look at."""
        saved, run_id = self.saved_watch, o.get('run_id')
        self.saved_watch = None
        if saved is not None and saved.get('boot') != self._safe(self.sources.boot_id): saved = None
        if saved is not None and isinstance(saved.get('run_id'), str):
            live = self._context(saved['run_id'], o['at'])
            live.instance, live.hypr_pid = saved.get('instance'), saved.get('hypr_pid')
            live.watch.running_since, live.watch.stopped = saved.get('running_since'), saved.get('stopped') is True
            live.report_ids = [s for s in saved.get('report_ids', []) if isinstance(s, str)]
            if isinstance(saved.get('ports'), list): live.kernel.note_ports(saved['ports'])
            if run_id == saved['run_id'] and o.get('state') in LIVE: self.live = live
            else: self._finish(live, o, stranded=run_id == saved['run_id'] and o.get('state') in STRANDED, away=True)
        elif run_id is not None and o.get('state') in STRANDED and run_id not in self.ended:
            self._finish(self._context(run_id, o['at']), o, stranded=True, away=True)

    def _feed(self, live, o):
        watch, state = live.watch, o.get('state')
        if (o.get('stop_requested') == watch.run_id or state == 'stopping') and not watch.stopped:
            watch.stopped = True
            self._save()
        facts = {}
        if state in ('running', 'failed'):
            output = watch.consent.get('output')
            ports = self._safe(self.sources.usb_ports)
            if ports: live.kernel.note_ports(ports)
            monitors = o.get('monitors') or []
            facts = dict(monitor=next((m for m in monitors if m.get('name') == output), None), monitors_known=bool(monitors),
                         drm=self._safe(self.sources.drm, output) if output else None, usb_present=bool(ports) if ports is not None else None,
                         hyprland_alive=self._safe(self.sources.alive, live.hypr_pid), driver_alive=self._safe(self.sources.driver_alive))
        first_running = state == 'running' and watch.running_since is None
        signals = watch.observe({**o, **facts})
        if first_running: self._save()
        if state == 'running':
            if not live.armed: self._arm(live)
            elif o['at'] >= live.next_poll: signals += self._poll(live, o['at'])
        for signal in signals: self.correlator.add(signal, watch.run_id, live, now=o['at'])

    def _arm(self, live):
        """Logs count from here: what happened during the start is the start's business."""
        live.cursor = self._safe(self.sources.kernel_start)
        live.offset = self._safe(self.sources.driver_start)
        live.armed = True
        live.next_poll = 0.0

    def _poll(self, live, at):
        """New log lines. With no position yet (a source that failed at the start) it only finds one: reading from nowhere would
        replay old drops as new."""
        live.next_poll = at + POLL_S
        out = []
        if live.cursor is None: live.cursor = self._safe(self.sources.kernel_start)
        else:
            lines, live.cursor = self._safe(self.sources.kernel_read, live.cursor, default=([], live.cursor))
            out += live.kernel.feed(lines)
        if live.offset is None: live.offset = self._safe(self.sources.driver_start)
        else:
            text, live.offset = self._safe(self.sources.driver_read, live.offset, default=('', live.offset))
            out += live.driver.feed(text, at)
        return out

    def _finish(self, live, o, stranded=False, away=False):
        """A run is over, or waits for Recover. Only an abnormal end reads the last unread log lines: after a clean one, a USB
        disconnect is someone putting the glasses away."""
        watch, at = live.watch, o['at']
        self.live = None
        run_id = watch.run_id
        live.result = self._safe(self.sources.result, run_id)
        lines = self._safe(self.sources.journal, _unit(run_id), 60, default=[])
        live.unit = parse_unit_exit(lines)
        signals = classify_end(at, live.result, started=watch.running_since is not None, stopped=watch.stopped, stranded=stranded, unit=live.unit, log=lines)
        if signals and live.armed: signals = self._poll(live, at) + signals
        if away and signals:
            signals.append(Signal(at, 'controller-restart', 'controller', 'the controller came back to a session that had ended while it was away'))
            if self._safe(self.sources.alive, live.hypr_pid) is False:
                signals.append(Signal(at, 'hyprland-crash', 'hyprland', 'the Hyprland process is gone'))
        for signal in signals: self.correlator.add(signal, run_id, live, now=at)
        self.ended[run_id] = at
        for old in sorted(self.ended, key=self.ended.get)[:-MAX_ENDED]: del self.ended[old]
        episode = self.correlator.open
        hyprland = episode is not None and episode.run_id == run_id and any(s.kind == 'hyprland-crash' for s in episode.signals)
        reason = (live.result or {}).get('stop_reason')
        # A later Stop cannot change why the supervisor already began shutdown.
        stopped = reason == 'interrupted' or (watch.stopped and not reason)
        ended = 'stopped' if stopped else 'ended'
        if live.report_ids:
            self.correlator.add(Signal(at, 'run-failed' if signals else 'renderer-exit', 'controller',
                                       'the monitored run ended; see its recorded outcome'), run_id, live, now=at)
        self.correlator.run_ended(run_id, at, ended, CORE_WAIT_S if crash_expected(live.result) or hyprland else 0.0)
        self._save()

    def _after_end(self, run_id, o):
        """A Recover that came back to where it started is a failure of its own; one that finished is the normal way out."""
        before, state = self.after.get(run_id), o.get('state')
        self.after[run_id] = state
        if before == 'starting' and state in STRANDED:
            at = o['at']
            reasons = (self._safe(self.sources.recovery, run_id) or {}).get('reasons')
            detail = 'Recover ran, and the session still has unfinished work' + (': ' + '; '.join(reasons) if reasons else '')
            context = self._context(run_id, at)
            context.result = self._safe(self.sources.result, run_id)  # how the run itself ended, for the report's renderer section
            self.correlator.add(Signal(at, 'recovery-failed', 'controller', detail), run_id, context, now=at)
            self.correlator.run_ended(run_id, at, 'ended')

    # --- reporting -----------------------------------------------------------------------------------------------------
    def _cores(self, episode):
        live = episode.context
        exes = tuple(e for e in [pin_of(live.watch.consent if live is not None else {}, 'renderer').get('path')] if e)
        pids = tuple(p for p in [live.hypr_pid if live is not None else None] if p)
        if not exes and not pids: return []
        return self._safe(self.sources.core_dumps, episode.first - 30, exes, pids, default=[])

    def _flush(self, now):
        episode = self.correlator.open
        if episode is not None and episode.hold_until > now and now >= self.next_core_check:
            self.next_core_check = now + POLL_S
            if self._cores(episode): episode.hold_until = 0.0
        for done in self.correlator.due(now): self._safe(self._report, done, now)

    def _evidence(self, episode, suppressed):
        live = episode.context or self._context(episode.run_id, episode.first)
        watch = live.watch
        run = self._safe(self.sources.journal, _unit(episode.run_id), LOG_LINES, default=[])
        last = max(episode.last, episode.ended_at or episode.last)
        kernel = self._safe(self.sources.kernel_around, episode.first - KERNEL_BEFORE_S, last + KERNEL_AFTER_S, default=[])
        return dict(consent=watch.consent, result=live.result, unit=live.unit or parse_unit_exit(run), source=self._safe(self.sources.source, default={}),
                    recovery=self._safe(self.sources.recovery, episode.run_id),
                    cores=self._cores(episode), tracking_driver=self._safe(self.sources.tracking_driver), status=watch.last_status, mode=watch.mode_text(), expected_mode=watch.expected_mode_text(), drm=watch.drm,
                    metrics=exit_metrics(run), suppressed=suppressed,
                    logs=dict(run=run, kernel=relevant_kernel(kernel, live.kernel.ports),
                              controller=self._safe(self.sources.journal, CONTROLLER_UNIT, LOG_LINES, default=[]),
                              driver=self._safe(self.sources.driver_tail, LOG_LINES, default=[])))

    def _report(self, episode, now):
        kind = episode.kind
        live = episode.context
        ids = live.report_ids if live is not None else []
        terminal_update = bool(episode.ended and ids)
        allowed = False if terminal_update else self.limit.allow(kind, episode.ended, now)
        # Suppression limits notifications, not evidence. A terminal outcome belongs to every
        # report from this exact run, even when the last incident was much earlier.
        targets = ids if terminal_update else ids[-1:] if not allowed else []
        updated = False
        for stem in targets:
            previous = self._safe(self.reports.read, stem)
            if not isinstance(previous, dict) or previous.get('run_id') != episode.run_id: continue
            bundle = amend_bundle(previous, episode, self._evidence(episode, {}), now, self.scrub)
            path = self._safe(self.reports.update, bundle)
            if path is None: continue
            updated = True
            if self.latest and self.latest['id'] == stem:
                self.latest['summary'] = bundle['summary']
        if updated or not allowed and not terminal_update:
            self._save()
            return
        bundle = build_bundle(episode, self._evidence(episode, self.limit.take_suppressed()), now, self.scrub)
        try: path = self.reports.write(bundle, markdown(bundle))
        except (OSError, ValueError) as exc:
            self._say(f'incident: could not save the {kind} report: {exc}')
            return
        if live is not None: live.report_ids.append(path.stem)
        self.latest = dict(id=path.stem, kind=kind, summary=bundle['summary'], at=bundle['first_signal_at'], path=str(path))
        self._save()
        log(f'incident: {path.name}: {bundle["summary"]}')
        self._safe(self.notify, notify_text(bundle['summary']))
        self._safe(self.hand_on, path)

    @staticmethod
    def _toast(text):
        from tools.native_spike.send import notify
        notify(text)

    def _hand_on(self, path):
        if self.hook is None: return

        def work():
            try:
                rc = run_hook(self.hook, path, self.reports.directory / 'hook.log')
                if rc is not None: log(f'incident: on-incident hook for {path.name}: {rc}')
            except OSError as exc: log(f'incident: on-incident hook for {path.name} failed: {exc}')
        self.spawn(work)

    def _save(self):
        boot = self._safe(self.sources.boot_id)
        state = dict(schema=1, watch=self.live.saved(boot) if self.live is not None else None, ended=self.ended, limit=self.limit.save(), latest=self.latest)
        self._safe(self.reports.save_state, state)
