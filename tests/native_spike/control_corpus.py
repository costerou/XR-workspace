"""The control-frame corpus (fixtures/control_frames.json): what the Python supervisor sends the renderer, op by op, and what
the renderer's own decoder makes of it, for the Rust rebuild to match byte for byte (docs/rust-rebuild-plan.md, phase 0).

For each of the renderer's 23 ops (spike/include/xr/ipc.hpp ControlOp):
  * `encoded`: frames exactly as the Python encoders built them. Most come from the real control stack walking at random
    (test_control_wire.Rig: ViewControl, the unified pointer, the key bind panel, VR video, DesktopManager); the ops that walk
    does not reach are built by their own encoders (scripted()). The renderer takes every one.
  * `mutated`: one of those frames with its payload changed (a key dropped, added or of the wrong type, or a payload the op's
    own payload fixture lists as invalid), and the renderer's verdict on it: `"accepted"` or the refusal's code and detail.
`envelope`: frames wrong in ways no op owns (framing, JSON, the six keys, identity, replay), with the verdict of the last.

Every case is decided on its own fresh ControlStream, earlier frames of a case first (they must be accepted).

Regenerate after a deliberate change to an encoder or the decoder, with the decoder built:
    CONTROL_WIRE_CHECK=build/release/control_wire_check python3 tests/native_spike/control_corpus.py --write
test_control_corpus.py checks the frames still match the encoders (any Python run) and the verdicts the decoder (ctest
control_wire).
"""
import json
from pathlib import Path
import random
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).with_name('fixtures') / 'control_frames.json'
FIXTURES = FIXTURE.parent
OPS = ('status', 'start', 'stop', 'select', 'recover', 'view', 'recenter', 'style', 'help', 'placement', 'placement_menu', 'options_menu',
       'resize_handle', 'video', 'move_handle', 'overview_pointer', 'roll', 'desktops', 'environment', 'binds', 'edge_pan', 'immersive',
       'restore_band')
# Who builds each op's frame (tools/native_spike). status, stop, select and recover: nothing in the Python supervisor sends
# them to the renderer, so their frames come from the generic encoder the run's control socket uses.
SENDERS = {
    'status': 'xr_control.control_frame (no supervisor sender)', 'start': 'supervisor._launch',
    'stop': 'xr_control.control_frame (no supervisor sender)', 'select': 'xr_control.control_frame (no supervisor sender)',
    'recover': 'xr_control.control_frame (no supervisor sender)', 'view': 'view.view_frame (ViewControl._frame)',
    'recenter': 'view.ViewControl.on_message', 'style': 'view.style_frame', 'help': 'view.help_frame', 'placement': 'view.placement_frame',
    'placement_menu': 'view.placement_menu_frame', 'options_menu': 'view.ViewControl._options_frame', 'resize_handle': 'resize.handle_frame',
    'video': 'video.video_frame', 'move_handle': 'resize.pill_frame', 'overview_pointer': 'free_pointer.overview_pointer_frame',
    'roll': 'free_pointer.roll_frame', 'desktops': 'view.desktops_frame', 'environment': 'environment.renderer_frame',
    'binds': 'keybinds.binds_frame', 'edge_pan': 'edge_pan.frame_bytes', 'immersive': 'immersive.renderer_frame',
    'restore_band': 'free_pointer.restore_band_frame',
}
RUN, SESSION = 'run', 'session'  # test_control_wire.Rig's identity: every frame here carries it
SEEDS, STEPS = 3, 2500  # the walk
KEEP = 6                # encoded frames kept per op, spread over the distinct payloads seen
PAYLOAD_FIXTURES = {op: f'{op}_payloads.json' for op in ('environment', 'immersive', 'overview_pointer', 'placement_menu', 'video')}
INVALID_KEEP = 8        # of each payload fixture's invalid entries


def frame(op, payload, request_id=2, run_id=RUN, session=SESSION):
    """A frame as the supervisor's encoders write one (json.dumps, compact, ensure_ascii=False)."""
    return json.dumps({'schema': 1, 'request_id': request_id, 'run_id': run_id, 'session': session, 'op': op, 'payload': payload},
                      separators=(',', ':'), ensure_ascii=False) + '\n'


def lines(data):
    return [line.decode() + '\n' for line in bytes(data).splitlines() if line]


def walked():
    """Every frame the real control stack sends on SEEDS random walks (test_control_wire.WalkTests), in order."""
    import test_control_wire as tcw

    class Owner(unittest.TestCase):
        def runTest(self): pass

    out = []
    for seed in range(SEEDS):
        owner = Owner()
        try:
            rig, rng = tcw.Rig(owner), random.Random(seed)
            for _ in range(STEPS):
                data, _ = rig.step(rng)
                if data: out += lines(data)
                close = getattr(data, 'close_fds', None)
                if close is not None: close()
        finally:
            owner.doCleanups()
    return out


def scripted():
    """The ops the walk never reaches, from their own encoders."""
    from unittest import mock
    from tools.native_spike import free_pointer as fp, immersive, view, xr_control
    out = [json.dumps({'schema': 1, 'request_id': 1, 'run_id': RUN, 'session': SESSION, 'op': 'start', 'payload': {}}) + '\n']  # supervisor._launch
    out += [xr_control.control_frame(RUN, SESSION, op, {}, 2) for op in ('status', 'stop', 'recover')]
    out += [xr_control.control_frame(RUN, SESSION, 'select', {'output_id': name}, 2) for name in ('XR-1', 'XR-3')]
    out += [fp.restore_band_frame(RUN, SESSION, 2, True).decode(), fp.restore_band_frame(RUN, SESSION, 3, False).decode()]
    store = mock.Mock(); store.load.return_value = view.DEFAULT_SETTINGS
    c = view.ViewControl(view.ViewState(['XR-1', 'XR-2', 'XR-3']), RUN, SESSION, lambda name: None, store, mock.Mock())
    out += lines(c.initial_frame())
    for preset in immersive.PRESETS:
        out += lines(c.set_immersive({**immersive.DEFAULTS, 'global': True, 'preset': preset, 'strength': immersive.STRENGTH_MAX}))
    out += lines(c.options_intent('options_open', monitor='XR-2'))
    snapshot = c.options.snapshot()
    for scope in (s for s in ('workspace', 'monitor', 'global') if s != snapshot['scope']):
        out += lines(c.options_intent('options_scope', scope=scope))
    out += lines(c.options_intent('options_focus', focus=1))
    out += lines(c.options_intent('options_close'))
    c.join_focus(2)
    return out


def encoded():
    """op -> up to KEEP frames, spread evenly over the distinct payloads the encoders sent (first time each was seen)."""
    distinct = {}
    for line in walked() + scripted():
        f = json.loads(line)
        distinct.setdefault(f['op'], {}).setdefault(json.dumps(f['payload'], sort_keys=True), line)
    out = {}
    for op, seen in distinct.items():
        frames = list(seen.values())
        n = len(frames)
        out[op] = [frames[i] for i in sorted({round(k * (n - 1) / (KEEP - 1)) for k in range(KEEP)})] if n > KEEP else frames
    return out


def _wrong(value):
    """A value of another JSON type."""
    if isinstance(value, bool): return 1
    if isinstance(value, (int, float)): return 'one'
    if isinstance(value, str): return 7
    if isinstance(value, list): return {}
    if isinstance(value, dict): return []
    return True


def mutations(op, line):
    """(what, frame) for one encoded frame of `op`: its payload changed one way at a time."""
    f = json.loads(line)
    p, rid = f['payload'], f['request_id']
    out = [('payload with an unknown key', frame(op, {**p, 'unknown': 1}, rid)),
           ('payload not an object', frame(op, [], rid))]
    for key in sorted(p):
        out.append((f'without {key}', frame(op, {k: v for k, v in p.items() if k != key}, rid)))
        out.append((f'{key} of the wrong type', frame(op, {**p, key: _wrong(p[key])}, rid)))
    name = PAYLOAD_FIXTURES.get(op)
    if name:
        for entry in json.loads((FIXTURES / name).read_text())['invalid'][:INVALID_KEEP]:
            why, payload = (entry['why'], entry['payload']) if isinstance(entry, dict) and set(entry) == {'why', 'payload'} else (None, entry)
            out.append((f'{name}: {why or json.dumps(payload)[:80]}', frame(op, payload, rid)))
    return out


def envelope():
    """(what, frames) wrong in the envelope: no op owns them."""
    ok = frame('recenter', {}, 5)
    nested = {}
    for _ in range(40): nested = {'n': nested}
    return [
        ('not JSON', ['{"schema":1,\n']),
        ('a JSON array', ['[1,2,3]\n']),
        ('an empty line', ['\n']),
        ('a duplicate key', ['{"schema":1,"schema":1,"request_id":2,"run_id":"run","session":"session","op":"recenter","payload":{}}\n']),
        ('nesting past 32', [frame('help', nested, 2)]),
        ('five keys', ['{"schema":1,"request_id":2,"run_id":"run","session":"session","op":"recenter"}\n']),
        ('seven keys', ['{"schema":1,"request_id":2,"run_id":"run","session":"session","op":"recenter","payload":{},"extra":1}\n']),
        ('schema 2', ['{"schema":2,"request_id":2,"run_id":"run","session":"session","op":"recenter","payload":{}}\n']),
        ('schema a string', ['{"schema":"1","request_id":2,"run_id":"run","session":"session","op":"recenter","payload":{}}\n']),
        ('request id 0', [frame('recenter', {}, 0)]),
        ('request id negative', [frame('recenter', {}, -3)]),
        ('request id a float', [frame('recenter', {}, 2.5)]),
        ('run id not a token', [frame('recenter', {}, 2, run_id='run id')]),
        ('session empty', [frame('recenter', {}, 2, session='')]),
        ('op not a string', ['{"schema":1,"request_id":2,"run_id":"run","session":"session","op":7,"payload":{}}\n']),
        ('unknown op', [frame('zoom_in', {}, 2)]),
        ('another run', [frame('recenter', {}, 2, run_id='run2')]),
        ('another session', [frame('recenter', {}, 2, session='session2')]),
        ('replayed id', [ok, ok]),
        ('older id', [ok, frame('recenter', {}, 4)]),
        ('next id', [ok, frame('recenter', {}, 6)]),
        ('over 16384 bytes', [frame('help', {'visible': False, 'rows': [], 'pad': 'x' * 16400}, 2)]),
        ('over 16384 bytes and not JSON', ['{' + 'x' * 17000 + '\n']),
        ('over 524288 bytes', [frame('options_menu', {'pad': 'x' * 530000}, 2)]),
        ('two frames on one line', [frame('recenter', {}, 2)[:-1] + frame('recenter', {}, 3)]),
    ]


def build():
    """The corpus without verdicts: {'ops': {op: {'encoded': [frame], 'mutated': [{'what', 'frame'}]}}, 'envelope': [{'what', 'frames'}]}."""
    enc = encoded()
    missing = set(OPS) - set(enc)
    if missing: raise AssertionError(f'no encoder sent {sorted(missing)}')
    ops = {}
    for op in OPS:
        seen, mutated = set(), []
        for what, f in mutations(op, enc[op][0]):
            if f not in seen and f not in enc[op]:
                seen.add(f)
                mutated.append({'what': what, 'frame': f})
        ops[op] = {'sender': SENDERS[op], 'encoded': enc[op], 'mutated': mutated}
    return {'ops': ops, 'envelope': [{'what': what, 'frames': frames} for what, frames in envelope()]}


def verdict(frames, tool):
    """What the renderer's decoder makes of the last of `frames`, the earlier ones taken first, on a fresh stream:
    'accepted', or {'code', 'detail'}."""
    data = ''.join(frames).encode()
    done = subprocess.run([tool, RUN, SESSION], input=data, capture_output=True, timeout=60, check=True)
    out = [json.loads(line) for line in done.stdout.splitlines()]
    totals = out.pop()
    if totals['accepted'] + totals['refused'] != len(frames) or totals['refused'] > 1 or out and totals['accepted'] != len(frames) - 1:
        raise AssertionError(f'unexpected verdicts {out} {totals} for {frames!r}')
    return 'accepted' if not out else {'code': out[0]['code'], 'detail': out[0]['detail']}


def decide(corpus, tool):
    """The corpus with every verdict filled in by the decoder at `tool`."""
    for op, cases in corpus['ops'].items():
        for f in cases['encoded']:
            if verdict([f], tool) != 'accepted': raise AssertionError(f'the renderer refuses an encoded {op} frame: {f!r}')
        for case in cases['mutated']: case['verdict'] = verdict([case['frame']], tool)
    for case in corpus['envelope']: case['verdict'] = verdict(case['frames'], tool)
    return corpus


COMMENT = __doc__.split('\n\n')[0].replace('\n', ' ') + ' See tests/native_spike/control_corpus.py.'


def write(tool):
    corpus = {'comment': COMMENT, 'run_id': RUN, 'session': SESSION, **decide(build(), tool)}
    FIXTURE.write_text(json.dumps(corpus, indent=1, ensure_ascii=False) + '\n')


if __name__ == '__main__':
    sys.path[:0] = [str(ROOT), str(Path(__file__).parent)]
    import os
    if sys.argv[1:] != ['--write'] or not os.environ.get('CONTROL_WIRE_CHECK'):
        sys.exit('usage: CONTROL_WIRE_CHECK=build/release/control_wire_check python3 tests/native_spike/control_corpus.py --write')
    write(os.environ['CONTROL_WIRE_CHECK'])
