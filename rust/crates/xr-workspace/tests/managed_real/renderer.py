#!/usr/bin/python3
"""A renderer for tests/managed_real.rs: the supervised protocol only, no Wayland, no GPU.

Records its argv (one JSON line per launch) in `$XDG_RUNTIME_DIR/renderer.jsonl`, reads the start frame, writes
`$XDG_RUNTIME_DIR/renderer-started`, then sends a heartbeat every 50 ms. `FAKE_CASE`: `renderer-fail` exits 9 after three
heartbeats, `success` exits 0 after ten, `heartbeat-stop` falls silent after three; `hold`'s first launch takes the
glasses away (`$XDG_RUNTIME_DIR/fake-glasses-gone`, which the compositor reads) and exits 5 after three, as a USB drop
ends a renderer; `switch`'s first launch puts the glasses in their side-by-side mode (`$XDG_RUNTIME_DIR/fake-glasses-wide`,
as a held R1 does) and exits 5 after three; any other case runs until SIGTERM.

Every frame after the start frame is recorded, in order, to `$XDG_RUNTIME_DIR/renderer-frames.jsonl`:
`{"launch": n, "frame": text, "fds": [{"type": ..., "size": ...}]}`, `n` counting launches from 1, `fds` the descriptors
that came with the frame's first byte (SCM_RIGHTS, as `supervisor._send_frame` sends a `video.FdFrame`'s).
"""
import array
import json
import os
import socket
import stat
import sys
import threading
import time
from pathlib import Path

runtime = Path(os.environ['XDG_RUNTIME_DIR'])
with open(runtime / 'renderer.jsonl', 'a') as f:
    f.write(json.dumps(sys.argv[1:]) + '\n')
launch = len((runtime / 'renderer.jsonl').read_text().splitlines())
args = dict(zip(sys.argv[1::2], sys.argv[2::2]))
conn = socket.socket(fileno=int(args['--supervised-fd']))
data = b''
while not data.endswith(b'\n'):
    chunk = conn.recv(1)
    if not chunk:
        sys.exit(3)
    data += chunk
assert json.loads(data)['op'] == 'start'


def describe(fd):
    st = os.fstat(fd)
    kind = next((k for k, test in (('file', stat.S_ISREG), ('socket', stat.S_ISSOCK), ('fifo', stat.S_ISFIFO))
                 if test(st.st_mode)), 'other')
    os.close(fd)
    return dict(type=kind, size=st.st_size if kind == 'file' else 0)


def record():
    """Every frame after the start, with the descriptors that arrived with its first byte, until the socket closes."""
    pending, fds = b'', []
    with open(runtime / 'renderer-frames.jsonl', 'a') as out:
        while True:
            try: data, ancillary, _, _ = conn.recvmsg(65536, socket.CMSG_SPACE(16 * 4))
            except OSError: return
            if not data: return
            for level, kind, body in ancillary:
                if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                    got = array.array('i'); got.frombytes(body[:len(body) - len(body) % 4])
                    fds += [describe(fd) for fd in got]
            pending += data
            while b'\n' in pending:
                line, pending = pending.split(b'\n', 1)
                out.write(json.dumps(dict(launch=launch, frame=line.decode(), fds=fds)) + '\n')
                out.flush()
                fds = []


threading.Thread(target=record, daemon=True).start()
(runtime / 'renderer-started').write_text('started')
case = os.environ.get('FAKE_CASE', '')
beats = 0
while True:
    if case == 'heartbeat-stop' and beats >= 3:
        time.sleep(60)
        continue
    conn.send(b'H')
    beats += 1
    if case == 'renderer-fail' and beats >= 3:
        os._exit(9)
    if case == 'switch' and launch == 1 and beats >= 3:
        (runtime / 'fake-glasses-wide').write_text('wide')
        os._exit(5)
    if case == 'hold' and launch == 1 and beats >= 3:
        (runtime / 'fake-glasses-gone').write_text('gone')
        os._exit(5)
    if case == 'success' and beats >= 10:
        os._exit(0)
    time.sleep(0.05)
