#!/usr/bin/python3
"""A renderer for tests/managed_real.rs: the supervised protocol only, no Wayland, no GPU.

Records its argv (one JSON line per launch) in `$XDG_RUNTIME_DIR/renderer.jsonl`, reads the start frame, writes
`$XDG_RUNTIME_DIR/renderer-started`, then sends a heartbeat every 50 ms. `FAKE_CASE`: `renderer-fail` exits 9 after three
heartbeats, `success` exits 0 after ten, `heartbeat-stop` falls silent after three; any other case runs until SIGTERM.
"""
import json
import os
import socket
import sys
import time
from pathlib import Path

runtime = Path(os.environ['XDG_RUNTIME_DIR'])
with open(runtime / 'renderer.jsonl', 'a') as f:
    f.write(json.dumps(sys.argv[1:]) + '\n')
args = dict(zip(sys.argv[1::2], sys.argv[2::2]))
conn = socket.socket(fileno=int(args['--supervised-fd']))
data = b''
while not data.endswith(b'\n'):
    chunk = conn.recv(1)
    if not chunk:
        sys.exit(3)
    data += chunk
assert json.loads(data)['op'] == 'start'
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
    if case == 'success' and beats >= 10:
        os._exit(0)
    time.sleep(0.05)
