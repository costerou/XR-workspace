"""Corpus for the Rust run's overview pointer (rust/crates/xr-workspace/src/view_controls/overview_pointer.rs).

rust/corpus/pointer/overview_pointer_run.json, made only by this module from the shipped Python code: the overview free
pointer as `managed.view_controls` wires it, with the Hyprland client's requests on the wire:

* `UnifiedPointer(control)` on a `view.ViewControl(view.ViewState(NAMES), ...)`, `control.wake = pointer.wake_reader`;
* `UnifiedSampler(client, capture, pointer, bind_args, cursor_shape=cursor_shape).start()`, where `cursor_shape` is the
  broker's `cursor_reader()` (an `OwnershipBrokerClient` on the same client, its handshake taken as done) or None;
* `control.set_overview_geometry(G)` (the renderer's overview layout, as test_free_pointer.py's `G`);
* `control.poll()` every 10 ms until the sampler has settled (no compositor call but a read for half a second, and the
  scenario's own condition), then `sampler.stop()` and `pointer.close()`, as the `finally` does.

The sampler is a thread sampling on a clock, so how many reads it makes is timing. What the corpus keeps is what is not:
every other request in order (`effects`: the binds, the warps, the cursor's hiding), the distinct reads (`reads`), the
first broker read as sent (`first_shape_read`), the requests `stop()` sends (`stop`), and the last `overview_pointer`
payload the loop sent (`pointer`). The Hyprland client is the real `hyprland.HyprlandClient` with its `_request` answered
by the scenario: the monitors, the cursor, a broker reply, `ok` for the rest. The Rust test
rust/crates/xr-workspace/tests/overview_pointer_run.rs drives `view_controls::overview_pointer::Pointer` the same way
through a stand-in compositor socket.

Regenerate (after a deliberate change to free_pointer.py, unified_cursor.py, input_lock.py, overview_cursor.py or
managed.view_controls) with:

    TMPDIR=/tmp /usr/bin/python3 tests/native_spike/overview_pointer_run_corpus.py --write

test_overview_pointer_run_corpus.py fails when the file and the Python code disagree.
"""
import json
import re
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT, ROOT / 'tests' / 'native_spike'):
    if str(p) not in sys.path: sys.path.insert(0, str(p))

from tools.native_spike import free_pointer as fp, hyprland, view  # noqa: E402
from tools.native_spike.ownership import OwnershipBrokerClient  # noqa: E402
from tools.native_spike.unified_cursor import UnifiedPointer, UnifiedSampler  # noqa: E402

CORPUS = ROOT / 'rust' / 'corpus' / 'pointer' / 'overview_pointer_run.json'
FIXTURE = json.loads((ROOT / 'tests/native_spike/fixtures/overview_pointer_payloads.json').read_text())
GEOMETRY = json.loads(FIXTURE['geometry']['line'])['overview_geometry']
INSTANCE = '0123456789abcdef0123456789abcdef01234567_1700000000_1'
NAMES = ['XR-1', 'XR-2', 'XR-3']
ARGS = ['/usr/bin/python3', '/x/tools/native_spike/xr_control.py', '/run/user/1000/c.sock', 'a' * 32, 'e' * 64]
RUN, SESSION = ARGS[3], ARGS[4]
EPOCH = 'epoch-one'
MONITORS = [{'name': 'DP-1', 'x': 0, 'y': 0, 'width': 2560, 'height': 1440, 'scale': 1.0},
            {'name': 'XR-1', 'x': 2560, 'y': 0, 'width': 1920, 'height': 1080, 'scale': 1.0},
            {'name': 'XR-2', 'x': 4480, 'y': 0, 'width': 1920, 'height': 1080, 'scale': 1.0},
            {'name': 'XR-3', 'x': 6400, 'y': 0, 'width': 1920, 'height': 1080, 'scale': 1.0}]
X1 = 2560
QUIET_S = 0.5
LIMIT_S = 8.0

SCENARIOS = {
    # test_unified_cursor's test_the_run_uses_the_unified_cursor: the cursor mid XR-1, the dot over it (desktop mode).
    'desktop': {'cursor': [X1 + 960, 540], 'shape': None},
    # test_free_pointer's test_view_controls_starts_the_sampler...: the cursor at XR-1's top edge.
    'edge': {'cursor': [X1 + 960, 0], 'shape': None},
    # test_text_cursor's test_view_controls_hands_it_to_the_sampler, through the broker (test_a_managed_run_reads_...).
    'text': {'cursor': [X1 + 960, 540], 'shape': 'text'},
}


def broker_reply(request, shape):
    j = json.loads(request[len('j/xrspike '):])
    return json.dumps({**{k: j[k] for k in ('schema', 'session', 'run_id', 'request_id')}, 'epoch': EPOCH,
                       'result': {'shape': shape or ''}})


WARP = re.compile(r'hl\.dsp\.cursor\.move\(\{ x = (-?\d+), y = (-?\d+) \}\)')


def answer(request, cursor, shape):
    """The scripted compositor's reply to one request (the Rust stand-in answers alike). `cursor`: where the cursor is,
    moved by a warp (`hl.dsp.cursor.move`), as Hyprland moves it."""
    if request in ('j/monitors', 'j/monitors all'): return json.dumps(MONITORS)
    if request == 'j/cursorpos': return json.dumps({'x': cursor[0], 'y': cursor[1]})
    if request.startswith('j/xrspike '): return broker_reply(request, shape)
    warp = WARP.search(request)
    if warp: cursor[:] = [int(warp[1]), int(warp[2])]
    return 'ok'


def is_read(request):
    return request.startswith('j/')


class Recorder(hyprland.HyprlandClient):
    """The real client, its socket replaced: every request kept."""
    def __init__(self, spec):
        super().__init__(INSTANCE, Path('/run/user/1000'))
        self.spec, self.cursor, self.wire = spec, list(spec['cursor']), []

    def _request(self, request, timeout=hyprland.DEADLINE):
        self.wire.append(request)
        return answer(request, self.cursor, self.spec['shape']).encode()


def frames(data):
    return [json.loads(line) for line in (data or b'').decode().splitlines() if line.strip()]


def summary(wire):
    effects = [r for r in wire if not is_read(r)]
    reads = sorted({'j/xrspike' if r.startswith('j/xrspike ') else r for r in wire if is_read(r)})
    shape = next((r for r in wire if r.startswith('j/xrspike ')), None)
    return effects, reads, shape


def scenario(spec):
    client = Recorder(spec)
    control = view.ViewControl(view.ViewState(NAMES), RUN, SESSION, None, None, None)
    pointer = control.overview_pointer = UnifiedPointer(control)
    control.wake = pointer.wake_reader
    reader = None
    if spec['shape'] is not None:
        broker = OwnershipBrokerClient(client, RUN, SESSION, 'DP-1')
        broker.epoch = EPOCH  # the handshake, taken as done (the run's broker has it before view_controls)
        reader = broker.cursor_reader()
    sampler = UnifiedSampler(client, NAMES, pointer, tuple(ARGS), cursor_shape=reader).start()
    control.set_overview_geometry(fp.parse_overview_geometry(GEOMETRY))
    last = None
    start = changed = time.monotonic()
    seen = 0
    while time.monotonic() - start < LIMIT_S:
        for f in frames(control.poll()):
            if f.get('op') == 'overview_pointer': last = f['payload']
        effects = len([r for r in client.wire if not is_read(r)])
        if effects != seen: seen, changed = effects, time.monotonic()
        ready = spec['shape'] is None or (last or {}).get('look') == spec['shape']
        if ready and time.monotonic() - changed >= QUIET_S: break
        time.sleep(0.01)
    else:
        raise SystemExit('the sampler never settled')
    effects, reads, shape = summary(client.wire)
    client.wire = []
    sampler.stop()
    pointer.close()
    return {'cursor': spec['cursor'], 'shape': spec['shape'], 'effects': effects, 'reads': reads,
            'first_shape_read': shape, 'stop': client.wire, 'pointer': last}


def build():
    return {'note': 'Recorded by tests/native_spike/overview_pointer_run_corpus.py; do not edit.',
            'names': NAMES, 'bind_args': ARGS, 'monitors': MONITORS, 'geometry': GEOMETRY, 'epoch': EPOCH,
            'scenarios': {name: scenario(spec) for name, spec in SCENARIOS.items()}}


def render(data):
    return json.dumps(data, indent=1, sort_keys=True) + '\n'


def main(argv):
    data = render(build())
    if '--write' in argv:
        CORPUS.write_text(data)
    else:
        sys.stdout.write(data)


if __name__ == '__main__':
    main(sys.argv[1:])
