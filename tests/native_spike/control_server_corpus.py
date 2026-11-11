"""The run's control socket as the shipped Python serves it, for the Rust port (rust/crates/xr-workspace/src/view_controls/socket.rs).

rust/corpus/sockets/server.json, made only by this module: every run frame case of rust/corpus/sockets/frames.json, and a
few of the socket's own (writes split or stalled, a client gone early, a hold), each sent by a real client to a real
ipc.PrivateServer on a temporary runtime directory and served by supervisor.control_request (`run`) or
supervisor.serve_without_renderer (`hold`), as the run loop serves them. Recorded per case: what the server did (`taken`:
the op came back to the loop; `answered`: a status reply was written; `dropped`: neither) and the bytes the client got.

The status body is a stand-in view's (`status_reply(status)` gives `STATUS_BODY` with the status it was called with): the
loop passes the renderer's measured status, a hold passes {}.

    TMPDIR=/tmp /usr/bin/python3 tests/native_spike/control_server_corpus.py --write

test_control_server_corpus.py fails when the file and the Python disagree.
"""
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import types

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
FRAMES = ROOT / 'rust/corpus/sockets/frames.json'
CORPUS = ROOT / 'rust/corpus/sockets/server.json'
RUN_STATUS = {'fps': 90.0, 'missed': 0}
HOLD_SECONDS = 0.5


def frame(run_id, session, request_id, op='status', payload=None):
    return json.dumps(dict(schema=1, request_id=request_id, run_id=run_id, session=session, op=op,
                           payload=payload or {}), separators=(',', ':')) + '\n'


def status_body(status):
    reply = json.loads(json.loads(FRAMES.read_text())['status_replies'][0]['text'])
    body = {k: v for k, v in reply.items() if k not in ('schema', 'request_id', 'run_id', 'session')}
    body['status'] = dict(status)
    return body


def raw(case):
    return bytes.fromhex(case['hex']) if 'hex' in case else case['text'].encode()


def connect(server):
    """A client of `server`, through its directory (a 128 character run id is past the socket path limit)."""
    fd = os.open(server.path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        c = socket.socket(socket.AF_UNIX)
        c.connect(f'/proc/self/fd/{fd}/control.sock')
        return c
    finally:
        os.close(fd)


def serve(case, session):
    """One case on a fresh server: (verdict, the taken op's request id or None, the bytes the client got)."""
    from tools.native_spike.ipc import PrivateServer
    from tools.native_spike.supervisor import control_request, serve_without_renderer
    with tempfile.TemporaryDirectory() as runtime:
        os.chmod(runtime, 0o700)
        server = PrivateServer(runtime, case['run_id'], session)
        control = types.SimpleNamespace(server=server, status_reply=status_body)
        try:
            for earlier in case['earlier']:
                with connect(server) as c:
                    c.sendall(earlier.encode())
                    control_request(control, RUN_STATUS)
            got = bytearray()
            client = None
            if case['writes'] is not None:
                client = connect(server)

                def write():
                    try:
                        for w in case['writes']:
                            time.sleep(w.get('pause_ms', 0) / 1000)
                            client.sendall(raw(w))
                        if case.get('close'):
                            client.close()
                            return
                        while True:
                            chunk = client.recv(65536)
                            if not chunk: break
                            got.extend(chunk)
                    except OSError:
                        pass
                writer = threading.Thread(target=write)
                writer.start()
            if case['mode'] == 'run':
                message = control_request(control, RUN_STATUS)
            else:
                message = None
                serve_without_renderer(control, HOLD_SECONDS)
            if client is not None:
                writer.join()
                client.close()
        finally:
            server.close()
    if message is not None: return 'taken', message['request_id'], bytes(got)
    return ('answered' if got else 'dropped'), None, bytes(got)


def cases(corpus):
    session = corpus['session']
    out = []
    for case in corpus['run_cases']:
        item = dict(about=case['about'], run_id=case['run_id'], mode='run', earlier=case['earlier'],
                    writes=[{k: case[k] for k in ('text', 'hex') if k in case}])
        if 'owner' in case: item['owner'] = case['owner']
        out.append(item)
    status = frame('run-1', session, 7)
    recenter = frame('run-1', session, 8, 'recenter')
    base = dict(run_id='run-1', earlier=[])
    out += [
        dict(base, about='the frame and more in one write: the rest stays unread', mode='run',
             writes=[{'text': recenter + 'more'}]),
        dict(base, about='a frame in two writes', mode='run',
             writes=[{'text': recenter[:20]}, {'text': recenter[20:], 'pause_ms': 50}]),
        dict(base, about='a client gone before the newline', mode='run', writes=[{'text': recenter[:-1]}], close=True),
        dict(base, about='a client that stalls past the read timeout', mode='run',
             writes=[{'text': recenter[:20]}, {'text': recenter[20:], 'pause_ms': 400}]),
        dict(base, about='the frame limit with no newline', mode='run', writes=[{'text': 'x' * 16384}]),
        dict(base, about='a status during a hold', mode='hold', writes=[{'text': status}]),
        dict(base, about='an op during a hold is dropped', mode='hold', writes=[{'text': recenter}]),
        dict(base, about='a replayed request id during a hold', mode='hold',
             earlier=[frame('run-1', session, 9, 'recenter')], writes=[{'text': frame('run-1', session, 9)}]),
        dict(base, about='nothing during a hold', mode='hold', writes=None),
    ]
    return out


def build():
    corpus = json.loads(FRAMES.read_text())
    session = corpus['session']
    out = []
    for case in cases(corpus):
        verdict, request_id, got = serve(case, session)
        out.append(dict(case, verdict=verdict, request_id=request_id, reply=got.decode()))
    return {
        'about': 'Generated by tests/native_spike/control_server_corpus.py: do not edit by hand.',
        'session': session,
        'hold_seconds': HOLD_SECONDS,
        'run_body': json.dumps(status_body(RUN_STATUS)),
        'hold_body': json.dumps(status_body({})),
        'cases': out,
    }


def write():
    CORPUS.write_text(json.dumps(build(), indent=1, ensure_ascii=False) + '\n')


if __name__ == '__main__':
    if sys.argv[1:] != ['--write']:
        sys.exit('usage: TMPDIR=/tmp /usr/bin/python3 tests/native_spike/control_server_corpus.py --write')
    write()
