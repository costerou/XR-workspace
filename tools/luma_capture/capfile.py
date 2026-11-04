"""The .lcap capture file: one header, then records. Spec: docs/capture-format.md."""
from dataclasses import dataclass
import json
from pathlib import Path
import struct

MAGIC = b'LUMACAP1'
VERSION = 1
FILE_HEADER = struct.Struct('<8sHHHHQII')    # magic, version, sizes of both headers, 0, start ns, bus, 0
RECORD_HEADER = struct.Struct('<IBBBBQQHBBiII8s')  # see Record for the field order

KIND_USB = 1
KIND_NOTE = 2

XFER_ISO, XFER_INT, XFER_CTRL, XFER_BULK = 0, 1, 2, 3
XFER_NAMES = {XFER_ISO: 'iso', XFER_INT: 'int', XFER_CTRL: 'ctrl', XFER_BULK: 'bulk'}

F_SETUP = 0x01           # setup holds a control request
F_GLASSES = 0x02         # this event belongs to the glasses (35ca:1104)
F_KERNEL_TRUNCATED = 0x04  # usbmon delivered fewer bytes than the transfer carried
F_POLICY_DROPPED = 0x08  # the recorder refused to keep this payload (another device, or audio/video)
F_CAPPED = 0x10          # the recorder kept only the first part of this payload
F_COMPANION = 0x20       # this event belongs to the glasses' companion, 35ca:1102 (buttons, microphone)
PARTIAL = F_KERNEL_TRUNCATED | F_POLICY_DROPPED | F_CAPPED


class CaptureError(ValueError):
    """The file is not a capture this code can read."""


@dataclass(slots=True)
class Record:
    t_ns: int                  # wall clock (CLOCK_REALTIME), so it lines up with the journal
    kind: int = KIND_USB
    event: str = 'S'           # S submitted by host, C completed, E error
    xfer: int = XFER_BULK
    ep: int = 0                # 0x80 set = device to host
    urb_id: int = 0            # pairs an S with its C
    bus: int = 0
    dev: int = 0               # USB address; CHANGES after every reconnect
    flags: int = 0
    status: int = 0            # negative errno on a C
    urb_len: int = 0           # bytes asked for (S) or transferred (C)
    setup: bytes = bytes(8)
    data: bytes = b''

    @property
    def is_in(self):
        return bool(self.ep & 0x80)

    @property
    def is_note(self):
        return self.kind == KIND_NOTE

    @property
    def note(self):
        """The decoded JSON object of a note record."""
        return json.loads(self.data)

    @property
    def glasses(self):
        return bool(self.flags & F_GLASSES)

    @property
    def companion(self):
        return bool(self.flags & F_COMPANION)

    @property
    def replayable(self):
        """Exactly what the host received from the glasses, complete: see Replay in the spec."""
        return (self.kind == KIND_USB and self.event == 'C' and self.is_in and self.glasses and self.status == 0
                and self.xfer in (XFER_INT, XFER_BULK) and not self.flags & PARTIAL)


def note_record(t_ns, **fields):
    return Record(t_ns=t_ns, kind=KIND_NOTE, event='\0', xfer=0,
                  data=json.dumps(fields, separators=(',', ':'), ensure_ascii=False).encode())


def encode(record):
    size = RECORD_HEADER.size + len(record.data)
    return RECORD_HEADER.pack(size, record.kind, ord(record.event), record.xfer, record.ep, record.t_ns,
                              record.urb_id, record.bus, record.dev, record.flags, record.status,
                              record.urb_len, len(record.data), record.setup) + record.data


class Writer:
    """Appends records to one file. flush() makes everything so far survive a crash of this process."""

    def __init__(self, path, *, start_ns, bus):
        self.path = Path(path)
        self.bytes = FILE_HEADER.size
        self._file = open(self.path, 'wb', buffering=1 << 16)
        self._file.write(FILE_HEADER.pack(MAGIC, VERSION, FILE_HEADER.size, RECORD_HEADER.size, 0, start_ns, bus, 0))

    def write(self, record):
        raw = encode(record)
        self._file.write(raw)
        self.bytes += len(raw)

    def flush(self):
        self._file.flush()

    def close(self):
        self._file.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class Reader:
    """Iterates one file's records. A file cut off mid-record (a crash) ends early and sets truncated."""

    def __init__(self, path):
        self.path = Path(path)
        self.truncated = False
        with open(self.path, 'rb') as file:
            raw = file.read(FILE_HEADER.size)
        if len(raw) < FILE_HEADER.size or raw[:8] != MAGIC:
            raise CaptureError(f'{path}: not a capture file')
        magic, self.version, self._file_header, self._record_header, _, self.start_ns, self.bus, _ = \
            FILE_HEADER.unpack(raw)
        if self.version != VERSION:
            raise CaptureError(f'{path}: capture version {self.version}, this code reads {VERSION}')
        if self._record_header < RECORD_HEADER.size:
            raise CaptureError(f'{path}: record header of {self._record_header} bytes is too short')

    def __iter__(self):
        with open(self.path, 'rb', buffering=1 << 20) as file:
            file.seek(self._file_header)
            while True:
                head = file.read(self._record_header)
                if not head:
                    return
                if len(head) < self._record_header:
                    self.truncated = True
                    return
                size, kind, event, xfer, ep, t_ns, urb_id, bus, dev, flags, status, urb_len, data_len, setup = \
                    RECORD_HEADER.unpack_from(head)
                if size != self._record_header + data_len:
                    self.truncated = True  # the length fields disagree: nothing after this can be trusted
                    return
                data = file.read(data_len)
                if len(data) < data_len:
                    self.truncated = True
                    return
                if kind in (KIND_USB, KIND_NOTE):  # a future kind is skipped, as the spec promises
                    yield Record(t_ns, kind, chr(event), xfer, ep, urb_id, bus, dev, flags, status, urb_len, setup,
                                 data)


def expand(paths):
    """Capture files in time order, from any mix of files and folders."""
    found = []
    for path in map(Path, paths):
        found.extend(sorted(path.rglob('*.lcap')) if path.is_dir() else [path])
    readers = [Reader(path) for path in dict.fromkeys(found)]
    if not readers:
        raise CaptureError('no .lcap files in ' + ', '.join(map(str, paths)))
    return sorted(readers, key=lambda reader: (reader.start_ns, str(reader.path)))


def records(paths):
    """Every record of a set of captures, file by file in time order."""
    for reader in expand(paths):
        yield from reader


def merge(paths, output):
    """Joins segments into one file, the form --replay takes. Returns the record count."""
    readers = expand(paths)
    count = 0
    with Writer(output, start_ns=readers[0].start_ns, bus=readers[0].bus) as writer:
        for reader in readers:
            for record in reader:
                writer.write(record)
                count += 1
    return count
