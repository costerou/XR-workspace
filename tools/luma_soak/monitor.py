"""The live soak: read-only watchers feeding one crash-safe session file.

Four independent witnesses, so one going quiet cannot read as "no drops": the kernel log stream, a
full re-read of the kernel log at the end, sysfs device numbers polled once a second, and the pose feed.
"""
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import queue
import subprocess
import threading
import time

from . import driverlog, probes
from .analyze import Policy, analyze
from .kernel import GLASSES_IDS, HUB_IDS, parse_line

TICK_S = 1.0
LOG_POLL_S = 0.05
FEED_POLL_S = 0.01
FEED_GAP_MS = 50.0        # XRLinuxDriver publishes every 15-17 ms: three missed samples
FEED_REATTACH_S = 2.0     # a restarted driver makes new segments; keep looking for them while the feed is still
DROP_SETTLE_S = 1.5       # a cluster's lines are all in by then
AUDIT_TIMEOUT_S = 30.0


@dataclass
class Config:
    session: Path
    duration_s: float = None
    journalctl: str = 'journalctl'
    driver_logs: list = field(default_factory=list)
    sysfs: str = '/sys/bus/usb/devices'
    proc: str = '/proc'
    feed: str = probes.DEFAULT_FEED
    driver_names: tuple = probes.DRIVER_NAMES
    glasses_ids: frozenset = GLASSES_IDS
    hub_ids: frozenset = HUB_IDS
    fail_fast: bool = False
    settle_s: float = 1.0     # lets journald catch up before the audit
    tick_s: float = TICK_S


class Session:
    """Every record reaches the file as it happens: a killed monitor still leaves evidence to judge."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)  # kernel logs stay private
        self.file = os.fdopen(fd, 'a', buffering=1)
        self.records, self.lock = [], threading.Lock()

    def add(self, src, t=None, **fields):
        record = {'src': src, 't': time.time() if t is None else t, **fields}
        with self.lock:
            self.records.append(record)
            self.file.write(json.dumps(record, ensure_ascii=False) + '\n')
        return record

    def snapshot(self):
        with self.lock:
            return list(self.records)

    def close(self):
        self.file.close()


def load_session(path):
    records = []
    for line in Path(path).read_text(errors='replace').splitlines():
        try:
            records.append(json.loads(line))
        except ValueError:
            continue  # the last line of a killed monitor may be cut short
    return records


class Tail:
    """Follows one log. A file that gets truncated or replaced is read again from its start."""

    def __init__(self, path):
        self.path, self.handle, self.identity, self.partial = Path(path), None, None, b''
        try:
            info = self.path.stat()
        except OSError:
            return
        self.handle = self.path.open('rb')
        self.handle.seek(0, os.SEEK_END)
        self.identity = (info.st_dev, info.st_ino)

    def _reopen(self):
        if self.handle:
            self.handle.close()
        self.handle, self.identity, self.partial = None, None, b''
        try:
            self.handle = self.path.open('rb')
            info = os.fstat(self.handle.fileno())
            self.identity = (info.st_dev, info.st_ino)
        except OSError:
            self.handle = None

    def poll(self):
        try:
            info = self.path.stat()
        except OSError:
            return []
        if self.handle is None or (info.st_dev, info.st_ino) != self.identity or info.st_size < self.handle.tell():
            self._reopen()
            if self.handle is None:
                return []
        data = self.partial + self.handle.read()
        *whole, self.partial = data.split(b'\n')
        return [line.decode(errors='replace') for line in whole]


class Monitor:
    def __init__(self, config, log=lambda text: None):
        self.config, self.log = config, log
        self.session = Session(config.session)
        self.stopping = threading.Event()
        self.stop_reason = None
        self.child = None
        self.dirty_at = None
        self.watched = config.glasses_ids | config.hub_ids
        self.tree = {}

    def request_stop(self, reason):
        self.stop_reason = self.stop_reason or reason
        self.stopping.set()

    def _glasses_present(self, tree):
        return any(d['id'] in self.config.glasses_ids for d in tree.values())

    def _journal(self):
        command = [self.config.journalctl, '-k', '-f', '-n', '0', '--no-pager', '-o', 'short-iso-precise']
        try:
            self.child = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, text=True, errors='replace')
        except OSError as error:
            self.session.add('meta', kind='blind', why=f'kernel log stream did not start: {error}')
            return
        for line in self.child.stdout:
            line = line.rstrip('\n')
            parsed = parse_line(line)
            self.session.add('kernel', t=parsed[0] if parsed else None, line=line)
            if 'USB disconnect' in line:
                self.dirty_at = time.monotonic()
        if not self.stopping.is_set():
            self.session.add('meta', kind='blind', why=f'kernel log stream ended, status {self.child.wait()}')

    def _tails(self):
        tails = [Tail(path) for path in self.config.driver_logs]
        while not self.stopping.wait(LOG_POLL_S):
            for tail in tails:
                for line in tail.poll():
                    self.session.add('driver', t=driverlog.line_time(line), line=line, log=str(tail.path))

    def _ticks(self):
        cpu = probes.CpuSampler(self.config.proc, self.config.driver_names)
        cpu.sample()
        while not self.stopping.wait(self.config.tick_s):
            self.session.add('cpu', **cpu.sample())
            tree = probes.usb_tree(self.config.sysfs)
            for change in probes.tree_changes(self.tree, tree, self.watched):
                self.session.add('sysfs', **change)
                self.dirty_at = time.monotonic()
            self.tree = tree

    def _feed(self):
        value = probes.FeedValue(self.config.feed)
        value.attach()
        last = value.read()
        advances, window_at = 0, time.monotonic()
        changed, changed_wall = window_at, time.time()
        looked = window_at
        while not self.stopping.wait(FEED_POLL_S):
            now = time.monotonic()
            read = value.read()
            if read is not None and read != last:
                still_ms = (now - changed) * 1000
                if last is not None and still_ms >= FEED_GAP_MS:
                    self.session.add('feed', kind='gap', start=changed_wall, ms=round(still_ms, 1))
                last, changed, changed_wall = read, now, time.time()
                advances += 1
            elif now - changed >= FEED_REATTACH_S and now - looked >= FEED_REATTACH_S:
                looked = now
                value.attach()
            if now - window_at >= self.config.tick_s:
                self.session.add('feed', kind='stats', advances=advances, window_s=round(now - window_at, 3))
                advances, window_at = 0, now
        still_ms = (time.monotonic() - changed) * 1000
        if last is not None and still_ms >= FEED_GAP_MS:
            self.session.add('feed', kind='gap', start=changed_wall, ms=round(still_ms, 1))
        value.close()

    def _audit(self, since):
        """The stream can start late or die; the journal itself is the last word on what the kernel said."""
        command = [self.config.journalctl, '-k', '--no-pager', '-o', 'short-iso-precise', f'--since=@{int(since)}']
        try:
            done = subprocess.run(command, capture_output=True, text=True, errors='replace', timeout=AUDIT_TIMEOUT_S, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError) as error:
            return f'failed: {error}'
        if done.returncode != 0:
            return f'failed: journalctl status {done.returncode}'
        for line in done.stdout.splitlines():
            parsed = parse_line(line)
            if parsed:
                self.session.add('kernel', t=parsed[0], line=line, audit=True)
        return 'ok'

    def _announce(self, policy, counted):
        report = analyze(self.session.snapshot(), policy)
        if report['drops'] > counted:
            newest = report['drop_details'][-1]
            self.log(f"DROP {report['drops']} at {newest['at']} ({newest['scope']})")
            if self.config.fail_fast:
                self.request_stop('fail-fast')
        return report['drops']

    def run(self):
        config = self.config
        started, started_mono = time.time(), time.monotonic()
        offset = probes.clock_offset()
        self.tree = probes.usb_tree(config.sysfs)
        present = self._glasses_present(self.tree)
        self.session.add('meta', t=started, kind='start', tree=self.tree, glasses_at_start=present,
                         glasses_ids=sorted(config.glasses_ids), hub_ids=sorted(config.hub_ids), boot_offset_s=round(offset, 3))
        if not present:
            self.log('glasses are not on USB')
        threads = [threading.Thread(target=target, daemon=True) for target in (self._journal, self._tails, self._ticks, self._feed)]
        for thread in threads:
            thread.start()
        policy = Policy(glasses_ids=config.glasses_ids, hub_ids=config.hub_ids)
        counted = 0
        deadline = started_mono + config.duration_s if config.duration_s is not None else None
        while not self.stopping.wait(0.1):
            now = time.monotonic()
            if deadline is not None and now >= deadline:
                self.request_stop('duration')
            elif self.dirty_at is not None and now - self.dirty_at >= DROP_SETTLE_S:
                self.dirty_at = None
                counted = self._announce(policy, counted)
        duration = time.monotonic() - started_mono
        if self.child and self.child.poll() is None:
            self.child.terminate()
        for thread in threads:
            thread.join(2.0)
        if self.child:
            try:
                self.child.wait(2.0)
            except subprocess.TimeoutExpired:
                self.child.kill()
        time.sleep(config.settle_s)
        audit = self._audit(started - 1)
        final = probes.usb_tree(config.sysfs)
        for change in probes.tree_changes(self.tree, final, self.watched):
            self.session.add('sysfs', **change)
        suspended = probes.clock_offset() - offset
        self.session.add('meta', kind='stop', reason=self.stop_reason, duration_s=round(duration, 1), audit=audit,
                         glasses_at_end=self._glasses_present(final), suspended_s=round(suspended, 1) if suspended >= 1 else 0)
        records = self.session.snapshot()
        self.session.close()
        return records
