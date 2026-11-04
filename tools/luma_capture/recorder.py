"""Records one USB bus to rolling segment files, and keeps the ones around each drop.

Layout under the output folder:
    rolling/       the last --keep-segments segments, oldest deleted
    start/         the first --keep-start segments, so the driver's start-up commands survive
    drops/<time>/  everything rolling held at a drop, plus what followed for --post-roll seconds

Keeps payloads only for the glasses, their companion's HID reports, and control and hub traffic. The bus also
carries the camera, a headset and microphones; those are stored as bare headers, and isochronous audio or video
never has its data kept.

A transfer up to --max-payload is kept whole, since the start-up read that may hold factory calibration is
16 KiB. An endpoint moving more than THROTTLE_BYTES a second is a camera, not a protocol, so past that it is
cut to HEAD_BYTES per transfer. That bounds what a 6DoF session writes without losing anything rare.
"""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time

from . import capfile
from .capfile import (F_CAPPED, F_COMPANION, F_GLASSES, F_KERNEL_TRUNCATED, F_POLICY_DROPPED, F_SETUP, XFER_CTRL,
                      XFER_ISO, Record)
from .protocol import confirms_drop, drop_signal, port_of
from .usbmon import is_companion, is_glasses, scan_devices

SCAN_S = 0.25
HEAD_BYTES = 4096
THROTTLE_BYTES = 1 << 20
UNKNOWN_SCAN_S = 0.05
FLUSH_S = 0.25
MARKS = 'marks.txt'
JOURNAL = ('journalctl', '-k', '-f', '-n', '0', '-o', 'short-unix', '--no-pager')
KERNEL_FILTER = re.compile(r'\busb\b|xhci|\bhub\b', re.IGNORECASE)
KERNEL_LINE = re.compile(r'^(\d+)\.(\d+)\s+\S+\s+\S+:\s(.*)$')


def stamp(t_ns):
    seconds = t_ns // 10**9
    return time.strftime('%Y%m%dT%H%M%S', time.localtime(seconds)) + f'-{t_ns // 10**6 % 1000:03d}'


def keep_payload(xfer, device_class):
    """Device classes: glasses, companion, hub, other, or unknown for a device sysfs has not listed yet."""
    if xfer == XFER_ISO:
        return False
    return xfer == XFER_CTRL or device_class != 'other'


class Recorder:
    def __init__(self, source, out_dir, bus, *, scan=scan_devices, clock=time.time_ns, segment_s=30,
                 keep_segments=10, keep_start=2, post_roll_s=30, stop_after_drops=10, max_payload=1 << 16,
                 max_total_bytes=4 << 30, duration_s=None, log=print):
        self.source = source
        self.out_dir = Path(out_dir)
        self.bus = bus
        self.drops_saved = 0
        self.stop_reason = None
        self._scan = scan
        self._clock = clock
        self._segment_ns = int(segment_s * 1e9)
        self._keep_segments = keep_segments
        self._keep_start = keep_start
        self._post_roll_ns = int(post_roll_s * 1e9)
        self._stop_after_drops = stop_after_drops
        self._max_payload = max_payload
        self._max_total = max_total_bytes
        self._log = log
        self._lock = threading.RLock()
        self._writer = None
        self._rolling = []
        self._started = 0
        self._number = 0
        self._classes = {}
        self._map = None
        self._port = None
        self._seen_glasses = False
        self._last_scan = 0
        self._last_flush = 0
        self._lost = 0
        self._begin_ns = None
        self._end_ns = None if duration_s is None else int(duration_s * 1e9)
        self._drop_at = None
        self._save_at = None
        self._ep_second = {}
        self._glasses_dev = None

    def start(self):
        self.out_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.out_dir, 0o700)   # head motion and desktop timing are nobody else's business
        for name in ('rolling', 'start', 'drops'):
            (self.out_dir / name).mkdir(exist_ok=True)
        now = self._clock()
        self._begin_ns = now
        self._refresh(now, force=True)
        self._open(now)
        self.note('start', bus=self.bus, ring_bytes=getattr(self.source, 'ring_bytes', None), uname=os.uname().release)

    def stop(self, reason):
        if self.stop_reason is None:
            self.stop_reason = reason

    def note(self, kind, *, t_ns=None, **fields):
        """Thread-safe: the log followers call this too."""
        record = capfile.note_record(self._clock() if t_ns is None else t_ns, type=kind, **fields)
        with self._lock:
            if self._writer:
                self._writer.write(record)
            self._check_drop(record)

    def on_event(self, event):
        with self._lock:
            device_class = self._class_of(event.devnum)
            flags = {'glasses': F_GLASSES, 'companion': F_COMPANION}.get(device_class, 0)
            flags |= F_SETUP if event.has_setup else 0
            data = event.data if event.has_data else b''
            if data and not keep_payload(event.xfer_type, device_class):
                data = b''
                flags |= F_POLICY_DROPPED
            elif data:
                kept = min(self._max_payload, self._budget(event, len(data)))
                if kept < len(data):
                    data = data[:kept]
                if len(data) < event.len_cap:
                    flags |= F_CAPPED
            if event.has_data and event.len_cap < event.len_urb:
                flags |= F_KERNEL_TRUNCATED
            record = Record(event.ts_ns, capfile.KIND_USB, event.type, event.xfer_type, event.epnum, event.id,
                            event.busnum, event.devnum, flags, event.status, event.len_urb,
                            event.setup if event.has_setup else bytes(8), data)
            self._writer.write(record)
            self._check_drop(record)

    def _budget(self, event, size):
        """How much of this payload to keep, given how much its endpoint has already moved this second."""
        second = event.ts_ns // 10**9
        key = (event.devnum, event.epnum)
        began, moved = self._ep_second.get(key, (second, 0))
        moved = 0 if began != second else moved
        self._ep_second[key] = (second, moved + size)
        return size if moved + size <= THROTTLE_BYTES else HEAD_BYTES

    def tick(self):
        """The periodic work: device map, kernel loss counter, flush, rotation, saving a drop."""
        now = self._clock()
        with self._lock:
            self._refresh(now)
            if now - self._last_flush >= FLUSH_S * 1e9:
                self._last_flush = now
                self._writer.flush()
                self._check_lost()
            if self._save_at is not None and now >= self._save_at:
                self._save_drop(now)
            elif now - self._started >= self._segment_ns:
                self._rotate(now)
            if self._end_ns is not None and now - self._begin_ns >= self._end_ns:
                self.stop('duration reached')

    def run(self, *, started=None):
        """Records until stopped. started() is called once the first segment is open."""
        self.start()
        if started:
            started()
        try:
            while self.stop_reason is None:
                for event in self.source.read_batch(0.05):
                    self.on_event(event)
                self.tick()
        finally:
            if self.stop_reason is None:
                self.stop('recorder failed')
            self.finish()

    def finish(self):
        now = self._clock()
        with self._lock:
            self.note('stop', reason=self.stop_reason)
            self._check_lost()
            if self._save_at is not None:
                self._save_drop(now, reopen=False)
            else:
                self._close()
                self._retire_last()

    def _class_of(self, devnum):
        device_class = self._classes.get(devnum)
        if device_class is None:
            now = self._clock()
            if now - self._last_scan >= UNKNOWN_SCAN_S * 1e9:
                self._refresh(now, force=True)
            device_class = self._classes.get(devnum, 'unknown')
        return device_class

    def _refresh(self, now, force=False):
        if not force and now - self._last_scan < SCAN_S * 1e9:
            return
        self._last_scan = now
        devices = [d for d in self._scan() if d['bus'] == self.bus]
        classes = {d['dev']: ('glasses' if is_glasses(d) else 'companion' if is_companion(d)
                              else 'hub' if d['hub'] else 'other') for d in devices}
        glasses = next((d for d in devices if is_glasses(d)), None)
        changed = devices != self._map
        self._map, self._classes = devices, classes
        if glasses:
            self._port = port_of(glasses['path'])
        if changed and self._writer:
            self.note('device_map', devices=devices, port=self._port)
        dev = glasses['dev'] if glasses else None
        if self._glasses_dev is not None and dev != self._glasses_dev:
            # A new address with no gap seen means it went and came back between two scans.
            self.note('glasses_gone', **({'why': 'new device number'} if dev else {}))
        if dev is not None and dev != self._glasses_dev and self._begin_ns is not None and self._seen_glasses:
            self.note('glasses_back', dev=dev)
        self._seen_glasses = self._seen_glasses or dev is not None
        self._glasses_dev = dev

    def _check_drop(self, record):
        reason = drop_signal(record, self._port)
        if reason is None or not confirms_drop(reason):
            return
        if self._drop_at is None:
            self._drop_at = record.t_ns
            self._log(f'drop detected: {reason}; saving for {self._post_roll_ns / 1e9:.0f} s more')
        self._save_at = self._clock() + self._post_roll_ns   # drops come in pairs, so each one extends the wait

    def _check_lost(self):
        stats = getattr(self.source, 'stats', None)
        if stats is None:
            return
        _, lost = stats()
        if lost != self._lost:
            self.note('usbmon_stats', dropped=lost, since_last=lost - self._lost)
            self._lost = lost

    def _open(self, now):
        self._number += 1
        path = self.out_dir / 'rolling' / f'seg-{stamp(now)}-{self._number:05d}.lcap'
        self._writer = capfile.Writer(path, start_ns=now, bus=self.bus)
        self._started = now
        self._writer.write(capfile.note_record(now, type='clock', realtime_ns=time.time_ns(),
                                               monotonic_ns=time.monotonic_ns()))
        self._writer.write(capfile.note_record(now, type='device_map', devices=self._map, port=self._port))

    def _close(self):
        if self._writer:
            self._writer.close()
            self._rolling.append(self._writer.path)
            self._writer = None

    def _retire_last(self):
        """A closed segment goes to start/ while that wants more, otherwise it just stays in rolling."""
        started = len(list((self.out_dir / 'start').glob('*.lcap')))
        if started < self._keep_start and self._rolling:
            path = self._rolling.pop()
            path.rename(self.out_dir / 'start' / path.name)

    def _rotate(self, now):
        self._close()
        self._retire_last()
        while len(self._rolling) > self._keep_segments:
            self._rolling.pop(0).unlink()
        if self._total_bytes() > self._max_total:
            self.stop(f'disk budget of {self._max_total >> 20} MiB used')
        self._open(now)

    def _save_drop(self, now, reopen=True):
        self._close()
        folder = self.out_dir / 'drops' / stamp(self._drop_at)
        folder.mkdir(parents=True, exist_ok=True)
        for path in self._rolling:
            shutil.move(path, folder / path.name)
        self._rolling.clear()
        self._log(f'saved {folder}')
        self._drop_at = self._save_at = None
        self.drops_saved += 1
        if self._stop_after_drops and self.drops_saved >= self._stop_after_drops:
            self.stop(f'{self.drops_saved} drops saved')
        if reopen:
            self._open(now)

    def _total_bytes(self):
        return sum(path.stat().st_size for path in self.out_dir.rglob('*.lcap'))


def follow_file(path, emit, stop, *, poll_s=0.05):
    """Stamps each line added to a log: the SDK's [E] lines carry no time of their own.

    Starts at the end of the file as it stands, so the first poll must have happened before lines count.
    """
    path = Path(path)
    file = None
    try:
        while not stop.wait(poll_s):
            if file is None:
                try:
                    file = open(path, 'rb')
                except OSError:
                    continue
                file.seek(0, os.SEEK_END)
            size = path.stat().st_size if path.exists() else 0
            if size < file.tell():
                file.seek(0)   # truncated and rewritten
            for line in file.readlines():
                emit(line.decode('utf-8', 'replace').rstrip('\n'))
    finally:
        if file:
            file.close()


def follow_kernel(emit, stop, *, command=JOURNAL, on_error=None):
    """Feeds USB lines of the kernel log to emit(t_ns, text), with the time the journal gives them."""
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                                   errors='replace')
    except OSError as error:
        if on_error:
            on_error(str(error))
        return
    closer = threading.Thread(target=lambda: (stop.wait(), process.terminate()), daemon=True)
    closer.start()
    with process.stdout:
        for line in process.stdout:
            match = KERNEL_LINE.match(line)
            if match and KERNEL_FILTER.search(match[3]):
                emit(int(match[1]) * 10**9 + int(match[2].ljust(9, '0')[:9]), match[3].rstrip())
    process.wait()


def add_mark(out_dir, text):
    """Leaves a note for a running recorder: 'pressed brightness up'. It stamps it as it reads it."""
    with open(Path(out_dir) / MARKS, 'a') as file:
        file.write(text.replace('\n', ' ') + '\n')


def start_followers(recorder, driver_log, *, kernel_command=JOURNAL):
    """Runs the log followers on daemon threads. Set the returned event to stop them."""
    stop = threading.Event()
    (recorder.out_dir / MARKS).touch()
    threading.Thread(target=follow_file, daemon=True,
                     args=(recorder.out_dir / MARKS, lambda text: recorder.note('mark', text=text), stop)).start()
    threading.Thread(target=follow_file, daemon=True,
                     args=(driver_log, lambda text: recorder.note('driver_log', text=text), stop)).start()
    threading.Thread(target=follow_kernel, daemon=True, args=(
        lambda t_ns, text: recorder.note('kernel', t_ns=t_ns, text=text), stop),
        kwargs={'command': kernel_command,
                'on_error': lambda why: recorder.note('kernel_log_unavailable', why=why)}).start()
    return stop


def describe(recorder):
    return json.dumps({'output': str(recorder.out_dir), 'drops_saved': recorder.drops_saved,
                       'stopped': recorder.stop_reason})
