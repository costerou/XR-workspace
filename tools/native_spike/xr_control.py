"""Bind helper: send one view operation to a running managed capture run. Stdlib only, run with -I.

usage: python3 -I xr_control.py CONTROL_SOCKET RUN_ID SESSION OP
Exits quietly with status 1 if no run is listening (binds only exist during a run anyway).
"""
import json
import re
import socket
import sys
import time

OPS = ('zoom_in', 'zoom_out', 'next', 'prev', 'spacing_up', 'spacing_down', 'recenter', 'mode_next', 'cursor_follow')
TOKEN = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z')


def main(argv):
    if len(argv) != 5: return 2
    path, run_id, session, op = argv[1:]
    if op not in OPS or not TOKEN.match(run_id) or not TOKEN.match(session) or not path.startswith('/'): return 2
    frame = json.dumps({'schema': 1, 'request_id': time.time_ns(), 'run_id': run_id, 'session': session, 'op': op,
                        'payload': {}}, separators=(',', ':')) + '\n'
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(0.5); s.connect(path); s.sendall(frame.encode())
    except OSError:
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
