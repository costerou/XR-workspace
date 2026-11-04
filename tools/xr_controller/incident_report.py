"""Incident reports: the bundle, its redaction and size, how it is written, and the hook that hands it on (docs/technical.md,
Incident reports).

Privacy works in two layers. Structured fields are built from named values only, never from a whole object. Text that
comes from logs goes through Scrubber, every string of the finished bundle included, so a field added later is cleaned too.
Screen content cannot get in: nothing here reads a capture or a core file, only coredumpctl's text backtrace.
"""
import datetime
import getpass
import itertools
import json
import os
from pathlib import Path
import re
import secrets
import signal
import socket
import stat
import subprocess
import tempfile
import time

from tools.native_spike.ipc import private_dir, read_private_json
from tools.native_spike.tracking import CRASHED as TRACKING_CRASHED, USB_DENIED
from .incident_detect import is_exit_report, signal_name

MAX_BUNDLE = 256 * 1024
MAX_SUMMARY = 4096
LINE_MAX = 400
MAX_CORES = 4
MAX_FRAMES = 32
STATE = '.monitor-state'        # beside the reports, but no *.json a watcher of this folder would take for one
SAME_LIVE_S = 600.0             # the same trouble again in a session that carries on
SAME_ENDED_S = 60.0             # a crash loop: each end is news, but not faster than this
MAX_PER_HOUR = 12
HOOK_TIMEOUT_S = 30.0
MAX_HOOK_OUTPUT = 64 * 1024
MAX_HOOK_LOG = 256 * 1024
REMOVED_LINE = '<line removed: window or clipboard text>'
PRIVACY = 'No screen content, window titles, clipboard text or video links. Logs are redacted.'

# Home paths a report may name; anything else in a home folder becomes <private-path>.
_APP_PATHS = ('/.local/share/viture-workspace', '/.local/state/viture-workspace', '/.local/state/xr_driver', '/.config/viture-workspace',
              '/.config/xr_driver', '/.config/systemd/user/viture-', '/.t3/worktrees/viture-workspace', '/Projects/viture-workspace')
_PRIVATE_LINE = re.compile(r'(?i)"(?:initial)?title"|\b(?:initial)?title\s*[:=]|\bwindow[ _-]?title\b|\bclipboard\b|\bwl-(?:copy|paste)\b')
_PEM = re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)', re.S)
_URL = re.compile(r'\b[a-z][a-z0-9+.-]{1,20}://\S+', re.I)
_BARE_URL = re.compile(r'\b(?:www\.\S+|(?:[a-z0-9-]+\.)+[a-z]{2,}/\S*)', re.I)
_BEARER = re.compile(r'(?i)\b(bearer|basic)\s+[a-z0-9._~+/=-]{6,}')
_NAMED = re.compile(r'(?i)\b([\w.-]*(?:password|passwd|passphrase|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|credential|cookie|'
                    r'authorization|auth)[\w.-]*)(["\']?\s*[=:]\s*)("[^"]*"|\'[^\']*\'|\S+)')
_TOKENS = re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{20,}|xox[abeoprs]-[A-Za-z0-9-]{10,}|'
                     r'AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,}|eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,})')
_MEDIA = re.compile(r'(?:~|/)[^\n"\']*?\.(?:mp4|mkv|webm|avi|mov|m4v|mpg|mpeg|wmv|flv|m2ts|mp3|flac|wav|ogg|opus|m3u8?)\b', re.I)
_HOME = re.compile(r'(?P<base>~(?=/)|/home/[^/\s"\']+|/root\b)(?P<rest>/[^\s"\',;:)\]}]*)?')
_BASE64 = re.compile(r'(?<![A-Za-z0-9+/_-])[A-Za-z0-9+_-]{32,}={0,2}')
_HEX = re.compile(r'\b[0-9a-fA-F]{24,}\b')
_EMAIL = re.compile(r'[\w.+-]+@[\w-]+\.[\w.-]+')
_IPV4 = re.compile(r'\b(?:\d{1,3}\.){3}\d{1,3}\b')
_MAC = re.compile(r'\b(?:[0-9a-f]{2}:){5}[0-9a-f]{2}\b', re.I)
_SERIAL = re.compile(r'(?i)\b(serial\s*(?:number)?\s*[:=]\s*)\S+')
_CONTROL = re.compile(r'[\x00-\x1f\x7f]+')


class Scrubber:
    """Makes any string fit for a report. `keep`: exact strings it would otherwise hide (the run's id, the build's hashes)."""
    def __init__(self, home=None, user=None, host=None, keep=()):
        self.home = str(Path.home() if home is None else home).rstrip('/')
        self.user = getpass.getuser() if user is None else user
        self.host = socket.gethostname() if host is None else host
        self.keep = {k for k in keep if k}

    def keeping(self, *more): return Scrubber(self.home, self.user, self.host, self.keep | {k for k in more if k})

    def _home(self, m):
        base, rest = m['base'], m['rest'] or ''
        if base == self.home or base == '~':
            if not rest: return '~'
            return '~' + rest if rest.startswith(_APP_PATHS) else '~/<private-path>'
        return '/home/<user>/<private-path>'

    def _hide(self, m): return m[0] if m[0] in self.keep else '<token>'

    def _base64(self, m):
        word = m[0]
        # Needs all three character classes; a mangled C++ name in a backtrace is what a fix needs most.
        if word in self.keep or word.startswith('_Z') or not (re.search('[a-z]', word) and re.search('[A-Z]', word) and re.search('[0-9]', word)):
            return word
        return '<token>'

    def text(self, value):
        text = _PEM.sub('<private-key>', str(value))
        if _PRIVATE_LINE.search(text): return REMOVED_LINE
        text = _CONTROL.sub(' ', text)
        text = _URL.sub('<url>', text)
        text = _BARE_URL.sub('<url>', text)
        text = _BEARER.sub(lambda m: f'{m[1]} <secret>', text)
        text = _TOKENS.sub('<token>', text)
        text = _MEDIA.sub('<media-file>', text)
        text = _HOME.sub(self._home, text)  # before _NAMED, or `.aws/credentials: denied` loses the word that matters
        text = _NAMED.sub(lambda m: f'{m[1]}{m[2]}<secret>', text)
        text = _BASE64.sub(self._base64, text)
        text = _HEX.sub(self._hide, text)
        text = _EMAIL.sub('<email>', text)
        text = _MAC.sub('<mac>', text)
        text = _IPV4.sub('<ip>', text)
        text = _SERIAL.sub(lambda m: f'{m[1]}<serial>', text)
        for word, mask in ((self.user, '<user>'), (self.host, '<host>')):
            if word: text = re.sub(rf'\b{re.escape(word)}\b', mask, text)
        return text if len(text) <= LINE_MAX else text[:LINE_MAX] + '…'

    def tree(self, value):
        if isinstance(value, str): return self.text(value)
        if isinstance(value, dict): return {k: self.tree(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)): return [self.tree(v) for v in value]
        return value


_SUMMARIES = {'hyprland-crash': 'Hyprland crashed or restarted', 'renderer-crash': 'the XR renderer crashed',
              'usb-drop': "the glasses' USB connection dropped", 'video-lost': 'the video link to the glasses was lost',
              'plugin-failed': 'the XR Hyprland plugin failed', 'watchdog': 'the XR session stopped responding',
              'controller-restart': 'the XR controller restarted and found an unfinished session', 'run-killed': 'the XR session was killed',
              'run-failed': 'the XR session failed', 'capture-failed': 'capturing your XR desktops failed',
              'present-stall': 'the picture in the glasses stalled', 'tracking-lost': 'head tracking was lost',
              'renderer-exit': 'the XR renderer stopped unexpectedly', 'recovery-failed': 'Recover did not finish',
              'start-failed': 'the XR session could not start'}
_NO_TAIL = ('controller-restart', 'run-killed', 'recovery-failed', 'start-failed')  # the line already says what became of it


def summary_line(kind, ended):
    line = _SUMMARIES[kind]
    if kind in _NO_TAIL: return line
    return line + {'ended': ' and the session ended', 'stopped': ' and the session was stopped'}.get(ended, ' but the session kept running')


def notify_text(summary): return f'XR Workspace hit a problem: {summary}. A report was saved.'


_FRAME = re.compile(r'#\d+\s+0x[0-9a-f]+\s+(\S+)')
_GPU_RESET = re.compile(r'(?i)GPU reset|ring \S+ timeout|\bGPU fault\b|amdgpu.*\*ERROR\*')


def _top_frame(core):
    for frame in core.get('frames') or []:
        m = _FRAME.match(frame.strip())
        if m and m[1] != 'n/a': return m[1]
    return None


def _recovery(ev):
    """What a recovery left and why: the last Recover's own report, else what the run's result says of its recovery at Stop."""
    saved, result = ev.get('recovery') or {}, ev.get('result') or {}
    return dict(remaining=list(saved.get('remaining') or result.get('remaining') or []),
                reasons=list(saved.get('reasons') or result.get('recovery') or []))


def guess_cause(episode, ev):
    """A first guess from the evidence: what a person would look at first, and how sure it is. Rules in order of weight."""
    kinds = {s.kind for s in episode.signals}
    usb = [s for s in episode.signals if s.kind == 'usb-drop']
    logs = ev.get('logs') or {}
    result, unit = ev.get('result') or {}, ev.get('unit') or {}

    def guess(text, confidence): return dict(text=text, confidence=confidence)

    # Before the core and USB rules: both are symptoms of these, and these say what to do.
    for known in (USB_DENIED, TRACKING_CRASHED):
        if known in (result.get('error') or ''): return guess(known, 'high')
    for core in ev.get('cores') or []:
        top = _top_frame(core)
        text = f"{core.get('exe')} crashed with {core.get('signal')}" + (f' in {top}' if top else '') + '. The backtrace is in core_dumps.'
        if usb: text += " The glasses' USB connection dropped around the same time, which may have set it off."
        return guess(text, 'high')
    if any(_GPU_RESET.search(line) for line in logs.get('kernel') or []):
        return guess('The graphics driver reported a reset or a fault; see the kernel lines.', 'high')
    if unit.get('result') == 'oom-kill':
        return guess('The system ran out of memory and killed the session.', 'high')
    if 'hyprland-crash' in kinds:
        return guess('Hyprland went away with no crash record, so a logout or a restart is likely.', 'medium')
    if usb:
        seen_by_kernel = any(s.source == 'kernel' for s in usb)
        return guess("The glasses' USB hub dropped off the bus. On 2026-11-03 this came before most ended sessions, and it points at the cable, "
                     'the port or the glasses themselves rather than this software.', 'high' if seen_by_kernel else 'medium')
    if 'video-lost' in kinds:
        return guess('The display link to the glasses dropped with no USB trouble: check the cable and the display output.', 'medium')
    if result.get('stop_reason') == 'heartbeat-timeout':
        return guess('The renderer froze for over 2 seconds, which usually means a stuck GPU or compositor.', 'medium')
    left = _recovery(ev)
    if left['reasons']:
        steps = f"{len(left['remaining'])} left ({', '.join(left['remaining'])})" if left['remaining'] else 'none listed'
        # As lists too: every string of a report stops at LINE_MAX, and no reason may be lost to a long sentence.
        return dict(guess(f"Recovery could not finish. Journal steps: {steps}. Why: {' '.join(left['reasons'])}", 'high'), **left)
    if any('dispatch: closed' in line for line in logs.get('run') or []):
        return guess("Hyprland closed the renderer's surface or connection, usually because its output went away.", 'medium')
    if result.get('error'):
        return guess(f"The run stopped with: {result['error']}", 'medium')
    return guess('No clear cause: read the timeline and the logs.', 'low')


def _iso(at):
    return datetime.datetime.fromtimestamp(at, datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def serialize(bundle): return json.dumps(bundle, indent=1).encode()


def fit(bundle, cap=MAX_BUNDLE):
    """Trim to `cap`: the oldest lines of the biggest log first, as the newest are nearest the incident; then backtraces,
    then the tail of the timeline. What cannot be cut (the facts) is a few kB."""
    while len(serialize(bundle)) > cap:
        sections = [s for s in bundle['logs'].values() if s]
        if sections:
            biggest = max(sections, key=lambda s: sum(map(len, s)))
            del biggest[:max(1, len(biggest) // 4)]
        elif bundle['core_dumps']: bundle['core_dumps'].pop()
        elif bundle['timeline']: bundle['timeline'].pop()
        else: break
        bundle['truncated'] = True
    return bundle


def pin_of(consent, key):
    """A consent's pinned binary, as a dict whatever the file held: no odd consent may cost a report."""
    pin = (consent or {}).get(key)
    return pin if isinstance(pin, dict) else {}


def build_bundle(episode, ev, now, scrub):
    """The report for a closed Episode. `ev` is what incident_monitor gathered. Every string is scrubbed, then it is fitted."""
    consent, result = ev.get('consent') or {}, ev.get('result') or {}
    source = ev.get('source') or {}
    renderer, plugin = pin_of(consent, 'renderer'), pin_of(consent, 'plugin')
    kind = episode.kind
    rc = result.get('renderer_returncode')
    logs = {name: [line for line in (ev.get('logs') or {}).get(name) or [] if not is_exit_report(line)]
            for name in ('run', 'controller', 'driver', 'kernel')}
    cores = [dict(core, frames=list(core.get('frames') or [])[:MAX_FRAMES]) for core in (ev.get('cores') or [])[:MAX_CORES]]
    timeline = [dict(at=_iso(s.at), source=s.source, kind=s.kind, detail=s.detail, **({'repeats': n} if n else {}))
                for s, n in sorted(zip(episode.signals, episode.repeats), key=lambda pair: pair[0].at)]
    bundle = dict(
        schema=1, id=time.strftime('%Y%m%dT%H%M%SZ', time.gmtime(now)) + f'-{kind}', kind=kind, summary=summary_line(kind, episode.ended),
        created_at=_iso(now), first_signal_at=_iso(episode.first), session=episode.ended or 'kept-running', run_id=episode.run_id,
        cause=guess_cause(episode, dict(ev, logs=logs, cores=cores)), timeline=timeline,
        versions=dict(source_sha=source.get('sha'), source_dirty=source.get('dirty'), bin=Path(renderer['path']).parent.name if renderer.get('path') else None,
                      renderer_sha256=renderer.get('sha256'), plugin_sha256=plugin.get('sha256')),
        glasses=dict(model=consent.get('model'), output=consent.get('output'), recovery_output=consent.get('recovery_output'),
                     tracking=consent.get('tracking') == 'xrlinuxdriver'),
        display=dict(mode=ev.get('mode'), expected_mode=ev.get('expected_mode'), drm=ev.get('drm')),
        renderer=dict(returncode=rc, signal=signal_name(-rc) if type(rc) is int and rc < 0 else None, stop_reason=result.get('stop_reason'),
                      complete=result.get('complete'), phase=result.get('phase'), error=result.get('error')),
        recovery=_recovery(ev),
        unit=ev.get('unit') or {}, metrics=dict(last=ev.get('status') or {}, exit_report=ev.get('metrics') or {}), core_dumps=cores, logs=logs,
        suppressed_since_last=ev.get('suppressed') or {}, truncated=False, privacy=PRIVACY)
    # The id too: a long kind makes it look like a token, and it is the file's name.
    clean = scrub.keeping(bundle['id'], episode.run_id, source.get('sha'), renderer.get('sha256'), plugin.get('sha256'))
    return fit(clean.tree(bundle))


def markdown(bundle):
    """The short version for a person, beside the JSON."""
    summary = bundle['summary']
    out = [f"# XR Workspace incident: {bundle['kind']}", '', f'{summary[:1].upper()}{summary[1:]}.', '',
           f"- When: {bundle['first_signal_at']}", f"- Session: {bundle['session']}", f"- Run: {bundle['run_id']}",
           f"- Glasses: {bundle['glasses']['model']} on {bundle['glasses']['output']}, {bundle['display']['mode']}",
           f"- Build: source {(bundle['versions']['source_sha'] or 'unknown')[:12]}, bin {bundle['versions']['bin']}", '',
           f"Likely cause ({bundle['cause']['confidence']} confidence): {bundle['cause']['text']}", '']
    if bundle['cause'].get('reasons'):
        out += ['## Why recovery stopped', '', *[f'- step left: {step}' for step in bundle['cause']['remaining']],
                *[f'- {reason}' for reason in bundle['cause']['reasons']], '']
    out += ['## What happened', '']
    out += [f"- {t['at']} ({t['source']}): {t['detail']}" for t in bundle['timeline'][:12]]
    extra = len(bundle['timeline']) - 12
    if extra > 0: out.append(f'- and {extra} more in the JSON report')
    return '\n'.join(out).encode()[:MAX_SUMMARY].decode(errors='ignore') + '\n'


class Reports:
    """The incidents folder: private (0700, files 0600), written atomically, the .md before its .json so that a watcher
    of .json files always finds both."""
    def __init__(self, directory): self.directory = Path(directory)

    @staticmethod
    def _put(fd, name, data):
        tmp = f'.{name}.{secrets.token_hex(8)}.tmp'
        out = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=fd)
        try:
            with os.fdopen(out, 'wb') as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(data); stream.flush(); os.fsync(stream.fileno())
            os.replace(tmp, name, src_dir_fd=fd, dst_dir_fd=fd)
        except BaseException:
            try: os.unlink(tmp, dir_fd=fd)
            except OSError: pass
            raise

    @staticmethod
    def _taken(fd, name):
        try: os.stat(name, dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError: return False
        return True

    def write(self, bundle, summary):
        fd = private_dir(self.directory, create=True)
        try:
            for n in itertools.count(1):
                stem = bundle['id'] if n == 1 else f"{bundle['id']}-{n}"
                if not self._taken(fd, stem + '.json') and not self._taken(fd, stem + '.md'): break
            self._put(fd, stem + '.md', summary.encode())
            self._put(fd, stem + '.json', serialize(dict(bundle, id=stem)))
            os.fsync(fd)
        finally: os.close(fd)
        return self.directory / (stem + '.json')

    def save_state(self, state):
        fd = private_dir(self.directory, create=True)
        try: self._put(fd, STATE, json.dumps(state).encode())
        finally: os.close(fd)

    def load_state(self):
        try:
            state = read_private_json(self.directory / STATE, limit=1 << 20)
            return state if isinstance(state, dict) else {}
        except (OSError, ValueError): return {}


class RateLimit:
    """How often the same trouble may be reported. A session that ends is news even just after a drop it survived, so the two
    are limited apart. What it holds back is counted, and the next report says how many."""
    def __init__(self): self.last, self.hour, self.suppressed = {}, [], {}

    def allow(self, kind, ended, now):
        key = f"{kind}|{'ended' if ended else 'live'}"
        self.hour = [t for t in self.hour if now - t < 3600]
        wait = SAME_ENDED_S if ended else SAME_LIVE_S
        if (key in self.last and now - self.last[key] < wait) or len(self.hour) >= MAX_PER_HOUR:
            self.suppressed[kind] = self.suppressed.get(kind, 0) + 1
            return False
        self.last[key] = now
        self.hour.append(now)
        return True

    def take_suppressed(self):
        out, self.suppressed = self.suppressed, {}
        return out

    def save(self): return dict(last=self.last, hour=self.hour, suppressed=self.suppressed)

    @classmethod
    def restore(cls, saved):
        limit = cls()
        if not isinstance(saved, dict): return limit
        last, hour, suppressed = saved.get('last'), saved.get('hour'), saved.get('suppressed')
        if isinstance(last, dict) and all(type(v) in (int, float) for v in last.values()): limit.last = last
        if isinstance(hour, list) and all(type(v) in (int, float) for v in hour): limit.hour = hour
        if isinstance(suppressed, dict) and all(type(v) is int for v in suppressed.values()): limit.suppressed = suppressed
        return limit


def hook_path(config_home): return Path(config_home) / 'viture-workspace' / 'on-incident'


def _append_log(path, text):
    path = Path(path)
    try:
        if path.stat().st_size > MAX_HOOK_LOG: os.replace(path, path.with_name(path.name + '.1'))
    except FileNotFoundError: pass
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8', errors='replace') as stream: stream.write(text)


def run_hook(hook, bundle, log_path, *, timeout=HOOK_TIMEOUT_S):
    """Hand one report on: `hook <bundle path>`, with no shell, in a session of its own so a timeout takes its children too.
    Returns None (no hook), its exit code, 'timeout', 'refused' or 'failed'. Output goes to the private log, cut short."""
    hook, bundle = Path(hook), Path(bundle)

    def record(rc, output=''):
        if output and not output.endswith('\n'): output += '\n'
        _append_log(log_path, f'{_iso(time.time())} {bundle.name} rc={rc}\n{output}')
        return rc

    try: st = hook.stat()
    except FileNotFoundError: return None
    except OSError as exc: return record('refused', f'{hook.name}: {exc}')
    if not stat.S_ISREG(st.st_mode) or not os.access(hook, os.X_OK): return record('refused', f'{hook.name}: not executable')
    if st.st_uid not in (os.getuid(), 0) or st.st_mode & 0o022: return record('refused', f'{hook.name}: others could change it')
    with tempfile.TemporaryFile() as output:
        try:
            child = subprocess.Popen([str(hook), str(bundle)], shell=False, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                                     close_fds=True, start_new_session=True, cwd=str(bundle.parent))
        except OSError as exc:
            return record('failed', f'{hook.name}: {exc}')
        try: rc = child.wait(timeout)
        except subprocess.TimeoutExpired:
            rc = 'timeout'
            try: os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError: pass
            child.wait()
        output.seek(0)
        text = output.read(MAX_HOOK_OUTPUT).decode('utf-8', errors='replace')
    return record(rc, text)
